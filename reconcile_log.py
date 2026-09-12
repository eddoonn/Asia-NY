"""Same period, same strategy, all the Globex changes: do we get the log's trades?

The Discord channel recorded 12 realised gold trades between 2026-08-26 and
2026-09-11, with entry, stop, target, exit reason and R. This re-derives that
window from market data with the Globex-anchored session window and the 03:00 CT
flatten, and matches the two session by session.

It is a reconciliation, not a re-test: the log is the ground truth of what the
live path actually did, so every difference is either a bug in the port or a bug
in what ran at the time. The window matters - it spans Labor Day, which closes
metals for the whole NY-late reference window of 2026-09-07.
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from backtest_globex import GLOBEX, STRATEGY, run, session_label   # noqa: E402
from discord_log import TRADES                                     # noqa: E402
from market_calendar import session_window, to_ct                  # noqa: E402

# The paste starts at the 2026-08-27 20:02 message, so the log covers the session
# that opened 2026-08-26 and everything after it.  Earlier sessions are outside
# the log, not missing from it.
SINCE = pd.Timestamp("2026-08-26 22:00", tz="UTC")
UNTIL = pd.Timestamp("2026-09-12", tz="UTC")

# The twelve realised trades live in `discord_log` with the rest of the
# transcript; `entry_time` is the "Entry time (UTC)" field and `r` is the R
# printed in "Closed ...R (reason)".  `find_trades` calls the sides
# "long"/"short".
LOGGED = TRADES

PRICE_TOL = 0.05     # the alerts round to 2dp, so allow half a tick either way
R_TOL = 0.02


def logged_rows():
    rows = []
    for stamp, side, entry, stop, target, reason, r in LOGGED:
        t = pd.Timestamp(stamp, tz="UTC")
        w = session_window(t)
        rows.append(dict(entry_time=t, side=side, entry=entry, sl=stop, tp=target,
                         reason=reason, r=r,
                         session=session_label(dict(entry_time=t)),
                         window=w))
    return rows


def backtest_rows():
    frame = pd.read_pickle(os.path.join(HERE, ".cache", "GC_F_730d_60m.pkl"))
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    _stats, trades = run(frame, GLOBEX)
    out = []
    for t in trades:
        et = pd.Timestamp(t["entry_time"])
        w = session_window(et)
        out.append(dict(entry_time=et, side=t["side"],
                        entry=float(t["entry"]), sl=float(t["sl"]), tp=float(t["tp"]),
                        reason=t["reason"], r=float(t["r"]),
                        session=session_label(t), window=w,
                        exit_time=pd.Timestamp(t["exit_time"])))
    return out


def net_r(rows):
    return round(sum(x["r"] for x in rows), 2)


def _ascii_stdout():
    """Windows consoles default to cp1252 and choke on the arrows/box chars."""
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main():
    _ascii_stdout()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--all-sessions", action="store_true",
                   help="also show sessions in the window with no trade either side")
    args = p.parse_args()

    logged = [x for x in logged_rows() if x["window"] is not None]
    bt = [x for x in backtest_rows() if SINCE <= x["entry_time"] < UNTIL]
    bt_all = bt

    print(f"window {SINCE:%Y-%m-%d} .. {UNTIL:%Y-%m-%d} · Globex config "
          f"(trigger {GLOBEX['asia']} CT, ref {GLOBEX['ny_late']} CT, "
          f"flat {GLOBEX['exit_hour']:02d}:00 CT, rr {STRATEGY['rr']}, "
          f"cost {STRATEGY['cost']})\n")
    print(f"logged trades:    {len(logged)}  net {net_r(logged):+.2f}R")
    print(f"backtest trades:  {len(bt_all)}  net {net_r(bt_all):+.2f}R\n")

    by_log = {}
    for x in logged:
        by_log.setdefault(x["session"], []).append(x)
    by_bt = {}
    for x in bt_all:
        by_bt.setdefault(x["session"], []).append(x)

    print(f"{'session':11} {'logged':>47}   {'backtest':>47}")
    print(f"{'':11} {'side   entry    sl     tp    x     R':>47}   "
          f"{'side   entry    sl     tp    x     R':>47}  verdict")
    exact, diffs, missing, extra = 0, 0, 0, 0
    detail = []
    for s in sorted(set(by_log) | set(by_bt)):
        lg, bts = by_log.get(s, []), by_bt.get(s, [])
        if not lg and not bts and not args.all_sessions:
            continue
        n = max(len(lg), len(bts))
        for i in range(n):
            a = lg[i] if i < len(lg) else None
            b = bts[i] if i < len(bts) else None
            verdict = compare(a, b)
            if verdict == "match":
                exact += 1
            elif a and b:
                diffs += 1
                detail.append((s, a, b, verdict))
            elif a:
                missing += 1
                detail.append((s, a, b, verdict))
            else:
                extra += 1
                detail.append((s, a, b, verdict))
            tag = s if i == 0 else ""
            print(f"{tag:11} {fmt(a):>46}   {fmt(b):>46}  {verdict}")

    print(f"\n{exact} match · {diffs} differ · {missing} logged but not produced · "
          f"{extra} produced but not logged")
    if diffs or missing or extra:
        print("\ndifferences in full:")
        for s, a, b, verdict in detail:
            print(f"  {s} [{verdict}]")
            if a:
                print(f"    logged   {a['entry_time']:%Y-%m-%d %H:%M} {a['side']:>5} "
                      f"entry {a['entry']:.2f} sl {a['sl']:.2f} tp {a['tp']:.2f} "
                      f"({a['reason']}) {a['r']:+.2f}R   {implied(a)}")
            if b:
                print(f"    backtest {b['entry_time']:%Y-%m-%d %H:%M} {b['side']:>5} "
                      f"entry {b['entry']:.2f} sl {b['sl']:.2f} tp {b['tp']:.2f} "
                      f"({b['reason']}) {b['r']:+.2f}R   {implied(b)}")
        print("\n  An entry is `reference_level +/- 1.0xATR10` and the stop is a"
              "\n  further 1.0xATR10 away, so the implied pair separates a"
              "\n  reference-level disagreement from an ATR10 disagreement:")
        for s, a, b, _v in detail:
            if a and b and a["side"] == b["side"]:
                ra, rb = implied(a), implied(b)
                print(f"    {s}: reference {'same' if ra[0] == rb[0] else 'DIFFERS'}"
                      f" ({ra[0]:.2f} vs {rb[0]:.2f}), ATR {ra[1]:.2f} vs {rb[1]:.2f}")
            elif a:
                print(f"    {s}: no backtest trade to compare (needs a second run in"
                      f" the same session - see PATCH_NOTES)")
    return 0


def implied(x):
    """(reference level, ATR10) implied by a trade's entry and stop geometry."""
    atr = abs(x["entry"] - x["sl"])
    level = x["entry"] + atr if x["side"] == "long" else x["entry"] - atr
    return round(level, 2), round(atr, 2)


def compare(a, b):
    if a is None:
        return "extra"
    if b is None:
        return "missing"
    if a["side"] != b["side"]:
        return "side-diff"
    if abs(a["entry"] - b["entry"]) > PRICE_TOL:
        return "entry-diff"
    if abs(a["sl"] - b["sl"]) > PRICE_TOL:
        return "stop-diff"
    if abs(a["tp"] - b["tp"]) > PRICE_TOL:
        return "target-diff"
    if a["reason"] != b["reason"]:
        return f"exit-diff ({a['reason']}->{b['reason']})"
    if abs(a["r"] - b["r"]) > R_TOL:
        return "r-diff"
    return "match"


def fmt(x):
    if x is None:
        return "-"
    return (f"{x['side']:>5} {x['entry']:>9.2f} {x['sl']:>7.2f} {x['tp']:>7.2f} "
            f"{x['reason']:>4} {x['r']:>+6.2f}")


if __name__ == "__main__":
    raise SystemExit(main())
