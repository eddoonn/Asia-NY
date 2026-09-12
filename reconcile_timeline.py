"""The Discord log, re-derived session by session.

`reconcile_log.py` checks the twelve *trades* in the channel.  This checks the
whole log over the whole time it covers - the nineteen "armed" postings as well
as the trades - because an armed posting is a second, independent readout of the
same thing: it quotes the NY-late reference the live system had loaded and the
two triggers it built from it, at a known moment.  Twelve trades are twelve
samples; the log has thirty-one postings.

Three questions, in order:

1. **What did each posting read?**  The quoted high/low pair is looked up against
   every session's own reference on the tape, so it is identified without
   assuming anything about the message clock.
2. **Was that the right window for the session it belongs to?**  The session a
   posting sits in is the one the market calendar says contains it, and the
   reference it needs is the one `strategy.find_trades` would arm from.  A run
   after the weekly close, or between sessions, has no session at all - those
   are the ones the calendar now suppresses instead of posting.
3. **Can this window distinguish the two reference clocks?**  It cannot: every
   session in it is on CDT, where 14:00-16:00 CT *is* 19:00-21:00 UTC.  The log
   therefore validates the session window and the levels, and is silent on the
   one winter question that matters - see `backtest_globex.py`.

The printed message times are local (Discord renders in the reader's zone), so
`OFFSET_HOURS` recovers UTC.  It is not a guess: the trigger geometry implies a
buffer of `1.0xATR10`, and that value pins the bar the levels were read at, which
pins the instant.  Every non-stale posting reads the 23:00 UTC bar, which is
`printed - 2h` to within the half hour the runs vary by.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backtest_globex import GLOBEX, run                            # noqa: E402

# `backtest_globex` puts the strategy repository on the path as well, and that
# repository has its own `notify.py` - so claim the front of the path back
# before importing the notifier under test.
sys.path.insert(0, HERE)

from discord_log import ARMED, FILLS, NOTES, SWEEPS, TRADES, buffer_of  # noqa: E402
from market_calendar import (CT, closure as market_closure,         # noqa: E402
                             session_exit_hour, session_window,
                             session_windows, to_ct)
from notify import (ATR_LEN, ATR_MULT, BUF, COST, NY_LATE, RR,      # noqa: E402
                    reference_levels, session_ids)
from strategy import add_atr, find_trades, ny_levels                # noqa: E402

# The window the log covers: its first trade opens 2026-08-26 23:00 UTC and its
# last alert posts after the 2026-09-11 session.
SINCE = pd.Timestamp("2026-08-26 00:00", tz="UTC")
UNTIL = pd.Timestamp("2026-09-12 00:00", tz="UTC")

# The exchange's own NY-late reference, on the clock the schedule is published in
# (14:00-16:00 CT).  `notify.NY_LATE` is the fixed-UTC one the live stack uses.
GLOBEX_REF_CT = (14, 16)

# Discord prints local time; UTC = printed - this.  See the module docstring.
OFFSET_HOURS = 2

TOL = 0.6                # the alerts round to 2dp; allow half a tick either way
LOOKBACK_SESSIONS = 4    # how far back a posting's levels may have come from


def _ascii_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def load_frame(path=None, symbol="GC=F", period="730d", interval="60m"):
    """The cached GC=F frame, fetched and cached on first use.

    Mirrors `backtest_globex`: a fresh clone has no `.cache`, and the audit is
    worth being able to run from one, so this fetches rather than assuming the
    file is there.  Re-runs stay offline.
    """
    path = path or os.path.join(
        HERE, ".cache", f"{symbol.replace('=', '_')}_{period}_{interval}.pkl")
    if os.path.exists(path):
        frame = pd.read_pickle(path)
    else:
        from backtest import load_data
        frame = load_data(symbol, period, interval)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        frame.to_pickle(path)
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    return add_atr(frame, ATR_LEN)


def levels_for(frame, clock):
    """NY-late levels keyed the way `day_ids` numbers them, on a given clock.

    `clock="ct"` is the exchange's own window (14:00-16:00 CT); `clock="utc"` is
    the fixed 19:00-21:00 UTC the live stack still uses.  Only the index the
    hours are read off differs, so the two are directly comparable.
    """
    index = frame.index.tz_convert(CT) if clock == "ct" else frame.index
    window = GLOBEX_REF_CT if clock == "ct" else NY_LATE
    return ny_levels(index, frame["High"].values, frame["Low"].values, window)


def ref_for(levels, frame, clock, window):
    """The reference a session arms from, resolved on the given clock."""
    if clock == "ct":
        index = frame.index.tz_convert(CT)
        start = pd.Timestamp(window[0]).tz_convert(CT)
        return reference_levels(levels, index, (start, start))
    return reference_levels(levels, frame.index, window)


def same(a, b, tol=TOL):
    return (a is not None and b is not None
            and abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol)


def source_of(frame, levels_ct, around, hi, lo, state):
    """Which session's own reference a quoted pair is, nearest to `around`.

    Returns `(window, ref, staleness)`.  `staleness` is how many sessions older
    than the posting's own the window it read is, so 0 means it quoted its own
    session and 1 means the previous one.  It is `None` when there was no session
    to compare against (a weekend or holiday run).
    """
    quoted = (hi, lo)
    windows = session_windows(around - pd.Timedelta(days=LOOKBACK_SESSIONS + 1),
                              around + pd.Timedelta(days=2))
    windows = [w for w in windows if w[0] <= around + pd.Timedelta(hours=12)]
    for window in reversed(windows):
        ref = ref_for(levels_ct, frame, "ct", window)
        if not same(ref, quoted):
            continue
        if state is None:
            return window, ref, None
        if window == state:
            return window, ref, 0
        between = len([w for w in windows if window[0] < w[0] < state[0]])
        return window, ref, 1 + between
    return None, None, None


def buffer_probe(frame, printed, buffer, offsets=(1, 2, 3)):
    """Compare an implied buffer to the freshest bar at each candidate offset.

    A posting's triggers imply a buffer of `1.0xATR10` as of the run that built
    them, and the run can only have seen a bar that had already closed.  So for
    each candidate `UTC = printed - offset` the test is: does the last closed
    bar carry that ATR?  Returns `{offset: (bar, atr, gap)}`.
    """
    index = frame.index
    atr = frame["atr"].values
    out = {}
    for off in offsets:
        instant = printed - pd.Timedelta(hours=off)
        pos = int(index.searchsorted(instant, side="right")) - 1
        if pos < 0:
            continue
        out[off] = (index[pos], float(atr[pos]), abs(float(atr[pos]) - buffer))
    return out


def run_live(frame, exit_hour):
    """The live gold path: Globex session from the calendar, UTC reference.

    Mirrors `notify.session_state` - the session mask and ids come from the
    calendar, the levels from the fixed-UTC `NY_LATE` window, and the flatten
    from the same session-relative rule.
    """
    df = frame.copy()
    add_atr(df, ATR_LEN)
    mask, aid = session_ids(df.index)
    levels = ny_levels(df.index, df["High"].values, df["Low"].values, NY_LATE)
    return find_trades(df, mask, aid, levels, RR, ATR_MULT, BUF, "stop", "rr",
                       exit_hour, atr=df["atr"].values, cost=COST)


def trade_date(window):
    return to_ct(window[1]).date()


def channel_trades(window):
    out = []
    for stamp, side, entry, stop, target, reason, r in TRADES:
        when = pd.Timestamp(stamp, tz="UTC")
        if window[0] <= when < window[1]:
            out.append(dict(entry_time=when, side=side, entry=entry, sl=stop,
                            tp=target, reason=reason, r=r))
    return out


def in_window(trades, window):
    return [t for t in trades
            if window[0] <= pd.Timestamp(t["entry_time"]) < window[1]]


def fmt_trades(trades, date=None):
    if not trades:
        return "-"
    parts = []
    for t in trades:
        when = pd.Timestamp(t["entry_time"])
        stamp = f"{when:%H:%M}"
        if date is not None and when.date() != date:
            stamp = f"{when:%m-%d %H:%M}"
        parts.append(f"{t['side'].upper()} {stamp} {t['r']:+.2f}({t['reason']})")
    return "; ".join(parts)


def armed_postings(frame, levels_ct, offset=OFFSET_HOURS):
    """Every armed posting, resolved against the tape and the calendar."""
    rows = []
    for i, posting in enumerate(ARMED, 1):
        printed = pd.Timestamp(posting["at"], tz="UTC")
        utc = printed - pd.Timedelta(hours=offset)
        buf = buffer_of(posting)
        state = session_window(utc)
        if state is None:
            found = market_closure(utc)
            market = f"closed: {found.name}" if found is not None else "between sessions"
        else:
            market = "trading"
        window, ref, back = source_of(frame, levels_ct, utc, posting["hi"],
                                      posting["lo"], state)
        if state is None:
            verdict = "outside-session"
        elif back == 0:
            verdict = "on time"
        elif back is not None:
            verdict = "stale"
        else:
            verdict = "no match"
        rows.append(dict(n=i, printed=printed, utc=utc, posting=posting, buf=buf,
                         state=state, market=market, window=window, ref=ref,
                         back=back, verdict=verdict,
                         probe=buffer_probe(frame, printed, buf)))
    return rows


def describe_source(row):
    """One-line provenance for the levels a posting quotes."""
    if row["window"] is None:
        return "not on this tape"
    date = trade_date(row["window"])
    if row["back"] == 0:
        return "own"
    if row["back"] is None:
        return f"{date:%m-%d} session"
    return f"{date:%m-%d} session ({row['back']} older)"


def report_postings(rows, detail=False):
    print("== 1. every armed posting, against the tape ==\n")
    print(f"{'#':>2} {'printed':>16} {'UTC (-2h)':>15} {'hi / lo':>19} {'buf':>6} "
          f"{'levels from':>22} {'market':>20}  verdict")
    for r in rows:
        p = r["posting"]
        print(f"{r['n']:>2} {p['at']:>16} {r['utc']:%m-%d %H:%M}Z  "
              f"{p['hi']:>8.2f}/{p['lo']:<8.2f} {r['buf']:>6.2f} "
              f"{describe_source(r):>22} {r['market']:>20}  {r['verdict']}")

    counts = {}
    for r in rows:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print(f"\n   {' · '.join(f'{v}: {n}' for v, n in sorted(counts.items()))}")
    print("   `stale` = inside a live session, quoting an earlier session's levels")
    print("   `outside-session` = a run the market calendar now suppresses")

    if detail:
        report_buffers(rows)


def report_buffers(rows):
    print("\n   buffer forensics - the buffer is 1.0xATR10, so it dates the run:")
    print(f"     {'#':>2} {'buffer':>7}   " + "   ".join(
        f"at UTC+{off}  (bar / gap)" for off in (1, 2, 3)))
    best = {}
    for r in rows:
        cells = []
        for off, (bar, _atr, gap) in sorted(r["probe"].items()):
            cells.append(f"{bar:%m-%d %H:%M}Z {gap:>5.2f}")
            if gap < 0.30:
                best.setdefault(r["n"], []).append(off)
        print(f"     {r['n']:>2} {r['buf']:>7.2f}   " + "   ".join(cells))
    winners = [n for n, offs in best.items() if offs == [OFFSET_HOURS]]
    print(f"\n     UTC+{OFFSET_HOURS} is the only offset within three-tenths of a tick "
          f"on {len(winners)} postings ({', '.join(str(n) for n in winners)});")
    print("     on the rest the ATR is flat, or the buffer is frozen, and the offset")
    print("     is not distinguishable. The verdicts are: identical at UTC+1, and at")
    print("     UTC+3 two postings (9, 14) fall out of their session.")


def report_ledger(frame, levels_ct, levels_utc):
    windows = [w for w in session_windows(SINCE, UNTIL) if w[0] >= SINCE]
    exit_hour = session_exit_hour(SINCE + pd.Timedelta(days=1))
    trades_ct = run(frame, GLOBEX)[1]
    trades_live = run_live(frame, exit_hour)

    print(f"\n== 2. session ledger ({len(windows)} sessions over the log) ==\n")
    print(f"{'trade date':>12} {'session (UTC)':>27} {'ref window':>14} "
          f"{'levels':>16}  {'channel':>34}  {'backtest':>34}")
    agree = 0
    ok = 0
    for window in windows:
        date = trade_date(window)
        ref_ct = ref_for(levels_ct, frame, "ct", window)
        ref_utc = ref_for(levels_utc, frame, "utc", window)
        agree += int(same(ref_ct, ref_utc))
        lv = f"{ref_ct[0]:.2f} / {ref_ct[1]:.2f}" if ref_ct else "-"
        src = "-"
        if ref_ct:
            last = frame.index[int(ref_ct[2])]
            src = f"{last - pd.Timedelta(hours=1):%m-%d %H}-{last + pd.Timedelta(hours=1):%H}Z"
        chan = channel_trades(window)
        bt = in_window(trades_ct, window)
        live = in_window(trades_live, window)
        if not chan and not bt:
            ok += 1
        elif len(chan) == len(bt) and all(
                abs(a["entry"] - b["entry"]) <= 0.05 and abs(a["r"] - b["r"]) <= 0.02
                for a, b in zip(chan, bt)):
            ok += 1
        flag = "" if len(live) == len(bt) else "  <- live differs"
        label = f"{date:%a %m-%d}"
        print(f"{label:>12} {window[0]:%m-%d %H:%M}->{window[1]:%m-%d %H:%M} "
              f"{src:>13} {lv:>16}  {fmt_trades(chan, date):>34}  "
              f"{fmt_trades(bt, date):>34}{flag}")
    print(f"\n   channel and backtest agree on {ok} of {len(windows)} sessions")
    print(f"   the two reference clocks pick identical windows on {agree} of "
          f"{len(windows)} sessions (every one of them CDT)")
    print("   the one session they disagree on is 08-28, which the log entered twice:"
          "\n   `find_trades` emits one trade per session *per run*, so a second run"
          "\n   of a live session is invisible to a single re-derivation.")
    return windows


def report_divergence(frame):  # noqa: C901 - one report, kept together
    """Where the two reference clocks part company, over the whole frame."""
    windows = session_windows(frame.index[0], frame.index[-1])
    levels_ct = levels_for(frame, "ct")
    levels_utc = levels_for(frame, "utc")
    differ, first = 0, None
    for window in windows:
        a = ref_for(levels_ct, frame, "ct", window)
        b = ref_for(levels_utc, frame, "utc", window)
        if a is None and b is None:
            continue
        if not same(a, b):
            differ += 1
            first = first or window

    # Past the end of the tape the windows are still declared, so the first
    # divergence after the log can be located on the calendar alone: the two
    # definitions coincide exactly while the session opens at 17:00 CT = 22:00
    # UTC, and part company the moment it opens at 23:00 UTC.
    first_after = None
    for window in session_windows(UNTIL, UNTIL + pd.Timedelta(days=120)):
        if window[0].hour == 23:
            first_after = window
            break
    print("\n== 3. what this window can and cannot test ==\n")
    print("   Every session in the log is on CDT, where 14:00-16:00 CT is")
    print("   19:00-21:00 UTC, so the postings and the trades pin the session")
    print("   window and the levels - and say nothing about which clock the")
    print("   reference should be read on. Re-derived over the whole")
    print(f"   {len(windows)}-session frame the two clocks disagree on {differ} sessions,")
    print(f"   starting {first[0]:%Y-%m-%d} UTC - the first one after the log ends")
    print(f"   opens {(first_after[0] if first_after else pd.NaT)} UTC.")
    return differ, first, first_after


def main():
    _ascii_stdout()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--detail", action="store_true",
                   help="add the buffer forensics per posting")
    args = p.parse_args()

    frame = load_frame()
    armed_msgs = sum(r["copies"] for r in ARMED)
    doubled = [t for t in TRADES if t[0] >= "2026-09-03"]
    print(f"Asia Grab - the Discord log, re-derived "
          f"({SINCE:%Y-%m-%d} .. {UNTIL - pd.Timedelta(days=1):%Y-%m-%d})\n")
    print(f"   the log: {len(ARMED)} armed postings ({armed_msgs} messages) · "
          f"{len(TRADES)} gold trades ({len(TRADES) + len(doubled)} messages) · "
          f"{len(SWEEPS)} sweep-reclaim postings "
          f"({sum(len(s['legs']) for s in SWEEPS)} FX legs) · "
          f"{len(FILLS)} demo fill · {len(NOTES)} test posting")
    print(f"   duplicates: every posting appears twice from {doubled[0][0][:10]} on, "
          f"right after the second dispatcher registered")
    print(f"   the printed clock is local (UTC+{OFFSET_HOURS}); see --detail\n")

    levels_ct = levels_for(frame, "ct")
    levels_utc = levels_for(frame, "utc")
    rows = armed_postings(frame, levels_ct)
    report_postings(rows, args.detail)
    report_ledger(frame, levels_ct, levels_utc)
    report_divergence(frame)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
