"""Replay every scheduled workflow command and fail if one would touch the book
while the Globex market is closed.

What it does, per workflow file:

1. extracts every `python <script> <args>` line from the `run:` steps,
2. expands every `cron:` entry into concrete UTC fire times over a window that
   deliberately includes a weekend, a Monday holiday and a DST transition,
3. runs each (command, fire time) pair **in-process** with `--dry-run` and the
   clock pinned via `--as-of`, in a throwaway working directory so nothing the
   scripts write can escape,
4. fails if any run emits an order action while `market_calendar.is_open()` says
   the exchange is shut.

The scripts announce what they would do on stdout, so the check needs no broker
and no network:

    DRYRUN ORDER place GC=F ...      <- would act
    DRYRUN BLOCKED market closed ... <- declined

A command that is only a report (the notifier) is run and checked for the same
markers, because "the notifier never places orders" is exactly the kind of thing
that quietly stops being true.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import os
import re
import runpy
import sys
import tempfile
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from market_calendar import is_open, closure, to_ct  # noqa: E402

# Any of these on stdout means the run intended to place, cancel or close.
ACTION_RE = re.compile(
    r"^DRYRUN ORDER |\[placed\]|Cancelled working order|Closed .* on CS\.D\.|"
    r"cancel_order|close_position|fetch\(",
    re.M)
BLOCKED_RE = re.compile(r"^DRYRUN BLOCKED ", re.M)

# Windows chosen to include: a plain week, the Labor Day (Mon 2026-09-07) week,
# and the autumn DST transition (Sun 2026-11-01).
DEFAULT_WINDOWS = [
    "2026-08-31 00:00",   # Mon -> covers the prior weekend + Labor Day
    "2026-09-07 00:00",
    "2026-10-26 00:00",   # -> covers the 2026-11-01 DST change
]

# The workflow `env:` blocks, so the scripts see the same configuration the
# runners give them.  (CI-only values; no broker credentials.)
WORKFLOW_ENV = {
    "BROKER": "etoro",
    "ETORO_MODE": "demo",
    "ETORO_SYMBOL": "GOLD.24-7",
    "ETORO_SYMBOLS": "GOLD.24-7,EURUSD,GBPUSD,USDJPY",
    "RISK_MODE": "each",
    "ACCOUNT_SIZE": "10000",
    "RISK_PCT": "1.0",
    "SKIP_SUNDAY": "1",
}


# --- cron --------------------------------------------------------------------
def _expand_field(expr, lo, hi):
    out = set()
    for part in expr.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, _, s = part.partition("/")
            step = int(s)
        if part == "*":
            start, end = lo, hi
        elif "-" in part:
            a, b = part.split("-")
            start, end = int(a), int(b)
        else:
            start = end = int(part)
        out.update(range(start, end + 1, step))
    return out


def fire_times(cron, start, end):
    """UTC datetimes in [start, end) matching a 5-field cron expression."""
    minute_f, hour_f, dom_f, mon_f, dow_f = cron.split()
    minutes = _expand_field(minute_f, 0, 59)
    hours = _expand_field(hour_f, 0, 23)
    months = _expand_field(mon_f, 1, 12)
    dom_any, dow_any = dom_f.strip() == "*", dow_f.strip() == "*"
    doms = None if dom_any else _expand_field(dom_f, 1, 31)
    dows = None if dow_any else {d % 7 for d in _expand_field(dow_f, 0, 7)}

    out = []
    t = start.replace(second=0, microsecond=0)
    if t.minute or t.second:
        t += timedelta(minutes=1)
        t = t.replace(second=0, microsecond=0)
    while t < end:
        if t.month in months and t.hour in hours and t.minute in minutes:
            dom_ok = doms is None or t.day in doms
            dow_ok = dows is None or t.weekday() in dows
            if (dom_ok and dow_ok) if (dom_any or dow_any) else (dom_ok or dow_ok):
                out.append(t)
        t += timedelta(minutes=1)
    return out


# --- workflow parsing --------------------------------------------------------
# A command runs to the end of the line, or to the next `;` in an inline
# if/then, or to a `#` comment - never past them into the surrounding YAML.
PY_RE = re.compile(r"\bpython3?\s+([^\s|&;\\\n]+\.py)([^\n;#]*)", re.M)
CRON_RE = re.compile(r"^\s*-?\s*cron:\s*['\"]?([^'\"\n#]+)", re.M)


def parse_workflow(path):
    """(crons, [(script, args)]) from a GitHub Actions workflow file."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    crons = [m.group(1).strip() for m in CRON_RE.finditer(text)]
    cmds = []
    for m in PY_RE.finditer(text):
        args = [a for a in m.group(2).split() if not a.startswith("#")]
        cmds.append((m.group(1), args))
    return crons, cmds


# --- synthetic data ----------------------------------------------------------
def synthetic_frame(now, days=16):
    """Hourly bars around `now`, enough for the reference/NY-late lookups.

    Slightly trending with a repeating intraday shape so the levels are
    non-degenerate; the exact prices do not matter, only that the scripts reach
    their decision logic without a network call.
    """
    end = pd.Timestamp(now) + pd.Timedelta(hours=6)
    idx = pd.date_range(end - pd.Timedelta(days=days), end, freq="h", tz="UTC")
    step = pd.Series(range(len(idx)), index=idx, dtype="float64")
    base = 4400.0 + 0.02 * step + 3.0 * ((step % 24) - 12) / 12.0
    o = base.values
    return pd.DataFrame({"Open": o,
                         "High": o + 4.0,
                         "Low": o - 4.0,
                         "Close": o + 0.5}, index=idx)


@contextlib.contextmanager
def frozen_data(now):
    """Point every script's `load_data` at a synthetic frame."""
    add_repo_paths(default_workflows())
    import backtest
    real = backtest.load_data
    frame = synthetic_frame(now)

    def fake(symbol, period, interval):          # noqa: ARG001
        return frame.copy()

    backtest.load_data = fake
    try:
        yield
    finally:
        backtest.load_data = real


def seed_state(cwd, now):
    os.makedirs(os.path.join(cwd, "results"), exist_ok=True)
    payload = {
        "orders": [{"order_id": 900001, "symbol": "GC=F", "side": "sell",
                    "profile": "tokyo", "risk_amount": 100.0, "closed": False},
                   {"order_id": 900002, "symbol": "GC=F", "side": "buy",
                    "profile": "tokyo", "risk_amount": 100.0, "closed": False}],
        "risk_amount": 100.0,
    }
    for name in ("etoro_session.json", "reclaim_session.json"):
        import json
        with open(os.path.join(cwd, "results", name), "w") as f:
            json.dump(payload, f)


def run_command(script, args, as_of, state_dir):
    """Run one workflow command in-process with the clock pinned. -> stdout."""
    path = os.path.join(HERE, script)
    if not os.path.exists(path):
        return None
    argv = [path, *args, "--dry-run", "--as-of", f"{as_of:%Y-%m-%d %H:%M:%S}"]
    buf = io.StringIO()
    old_argv, old_cwd = sys.argv, os.getcwd()
    old_env = {k: os.environ.get(k) for k in WORKFLOW_ENV}
    os.environ.update(WORKFLOW_ENV)
    sys.argv = argv
    try:
        os.chdir(state_dir)
        with frozen_data(as_of), contextlib.redirect_stdout(buf):
            try:
                runpy.run_path(path, run_name="__main__")
            except SystemExit:
                pass
            except Exception as exc:            # noqa: BLE001
                print(f"EXCEPTION {type(exc).__name__}: {exc}")
    finally:
        sys.argv = old_argv
        os.chdir(old_cwd)
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    return buf.getvalue()


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--workflows", action="append", default=None,
                   help="workflow file or directory; repeatable")
    p.add_argument("--window", action="append", default=None,
                   help="UTC start of a replay window (ISO); repeatable")
    p.add_argument("--max-runs", type=int, default=0, help="cap the number of command/time pairs")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    roots = args.workflows or default_workflows()
    files = []
    for r in roots:
        if os.path.isdir(r):
            files += [os.path.join(r, f) for f in sorted(os.listdir(r))
                      if f.endswith((".yml", ".yaml"))]
        elif os.path.exists(r):
            files.append(r)
    if not files:
        print("No workflow files found. Pass --workflows <path>.")
        return 2

    for repo in add_repo_paths(roots):
        print(f"repo on sys.path: {repo}")
    ensure_repo_paths()

    windows = [datetime.fromisoformat(w).replace(tzinfo=timezone.utc)
               for w in (args.window or DEFAULT_WINDOWS)]

    pairs, seen = [], set()
    for wf in files:
        crons, cmds = parse_workflow(wf)
        if not crons:
            continue
        for cron in crons:
            for start in windows:
                for fire in fire_times(cron, start, start + timedelta(days=7)):
                    for script, c_args in cmds:
                        key = (script, tuple(c_args), fire)
                        if key in seen:
                            continue
                        seen.add(key)
                        pairs.append((os.path.basename(wf), cron, script, c_args, fire))
    if args.max_runs:
        pairs = pairs[:args.max_runs]

    print(f"{len(files)} workflow file(s), {len(pairs)} scheduled command/time pairs\n")

    violations, declined_while_closed, ran_open = [], 0, 0
    with tempfile.TemporaryDirectory() as td:
        for wf, cron, script, c_args, fire in pairs:
            as_of = fire.replace(tzinfo=timezone.utc)
            seed_state(td, as_of)
            out = run_command(script, c_args, as_of, td)
            if out is None:
                print(f"  SKIP {script} (not in this drop-in)")
                continue
            acted = bool(ACTION_RE.search(out))
            blocked = bool(BLOCKED_RE.search(out))
            live = is_open(as_of)
            if live:
                ran_open += 1
            elif acted:
                violations.append((wf, cron, script, as_of, out))
            elif blocked:
                declined_while_closed += 1
            if args.verbose:
                state = "OPEN" if live else "closed"
                mark = "ORDER" if acted else ("blocked" if blocked else "-")
                print(f"  {as_of:%a %Y-%m-%d %H:%M} {state:6} {mark:7} "
                      f"{script} {' '.join(c_args)}")

    print(f"\nreplayed {len(pairs)} run(s): {ran_open} while open, "
          f"{declined_while_closed} correctly declined while closed, "
          f"{len(violations)} violation(s)")

    if violations:
        print("\nFAIL - these scheduled commands would touch the book while the "
              "Globex market is shut:")
        for wf, cron, script, as_of, out in violations:
            when = to_ct(pd.Timestamp(as_of))
            why = closure(pd.Timestamp(as_of))
            print(f"  {wf} cron '{cron}' -> {script} at {as_of:%Y-%m-%d %H:%M} UTC "
                  f"({when:%a %H:%M} CT, {why.name if why else 'open'})")
            for line in out.splitlines():
                if ACTION_RE.search(line + "\n"):
                    print(f"      {line.strip()}")
        return 1

    print("PASS - no scheduled command acts on the book outside a Globex session.")
    return 0


def default_workflows():
    base = os.path.expanduser(
        os.environ.get("MF_ROOT", "~/Desktop/MatchForecast codes"))
    return [os.path.join(base, r, ".github", "workflows")
            for r in ("Asia-NY-repo", "asia-gold-reversal")]


def add_repo_paths(roots):
    """Put each workflow's repository on sys.path.

    The scripts under test are this directory's copies, but they still import
    shared, unmodified modules (`strategy`, `sweep`, `etoro_client`) out of the
    repository, exactly as they do in CI.
    """
    added = []
    for r in roots:
        repo = os.path.dirname(os.path.dirname(os.path.abspath(r)))
        if os.path.isdir(repo) and repo not in sys.path:
            sys.path.append(repo)
            added.append(repo)
    return added


def ensure_repo_paths():
    add_repo_paths(default_workflows())


if __name__ == "__main__":
    raise SystemExit(main())
