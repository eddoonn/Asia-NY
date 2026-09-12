"""Asia Grab: Globex-anchored session vs the fixed-UTC baseline.

The strategy was tuned on a fixed window - `--asia 22-10 --ny-late 19-21
--exit-hour 8`. Those are the Globex hours *only under CDT*; on CST the exchange
reopens at 23:00 UTC and the equivalent windows are 23:00-11:00, 20:00-22:00 and
09:00. So the live config trades an hour early for five months of the year.

This runs the same strategy code twice over the same bars and reports what
actually changes:

  baseline   fixed UTC hours on the UTC index  (22-10 / 19-21 / flat 08:00)
  globex     CT hours on the CT index          (17-05 / 14-16 / flat 03:00 CT)

Nothing about the strategy is reimplemented to do this: `run_config` is the
repository's own driver, and the Globex run simply hands it a frame whose index
is localised to `America/Chicago`. Then `index.hour`, `index.date`,
`index.weekday()` and `day_ids()` are all CT, which is the definition of
"follow the Globex week".
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
MF = os.path.expanduser(os.environ.get("MF_ROOT", "~/Desktop/MatchForecast codes"))
sys.path.insert(0, os.path.join(MF, "Asia-NY-repo"))

import contextlib                                     # noqa: E402

import strategy as strategy_mod                      # noqa: E402
from backtest import load_data, run_config           # noqa: E402
from market_calendar import CT                       # noqa: E402
from strategy import add_atr                         # noqa: E402

# The live tuned config, from the README and place_orders.py / notify.py.
STRATEGY = dict(rr=0.75, atr_mult=1.0, atr_len=10, entry_buffer=1.0,
                entry_mode="stop", tp_mode="rr", cost=0.3, skip_sunday=False,
                sl_mode="atr", wick_buffer=0.5)
# skip_sunday=False is not an oversight: `find_trades` defaults to False, the
# README's tuned command passes no --skip-sunday, and the Discord log trades the
# Sunday 2026-08-30 session. The workflow's SKIP_SUNDAY=1 applies to the FX
# monitor, not to the gold session (place_orders.py --skip-sunday is a separate
# flag, and it is the gold session that is being reconciled).

BASELINE = dict(label="fixed-UTC", asia=(22, 10), ny_late=(19, 21),
                exit_hour=8, ct=False)
GLOBEX = dict(label="globex-CT", asia=(17, 5), ny_late=(14, 16),
              exit_hour=3, ct=True)
# The live stack as it stands today: the session window and the flatten come
# from the calendar, but the NY-late reference is still the fixed 19:00-21:00
# UTC window (`notify.NY_LATE`, `place_orders.NY_LATE`).  On CST that window is
# 13:00-15:00 CT, so on a CT index this config *is* the live behaviour in
# winter; its summer column is a fiction and must not be read.
LIVE = dict(label="live (UTC ref)", asia=(17, 5), ny_late=(13, 15),
            exit_hour=3, ct=True)


def session_label(t):
    """The CT date the session's evening half falls on.

    This is the one label both configs agree on, so it is what lets a session be
    compared across them: the fixed-UTC config opens its session at 16:00 CT in
    winter (an hour before the exchange), the Globex config at 17:00 CT, but
    both evening halves land on the same CT date.
    """
    sig = pd.Timestamp(t["entry_time"]).tz_convert(CT)
    return str((sig - pd.Timedelta(hours=12)).date()) if sig.hour < 12 else str(sig.date())


def in_winter(ts):
    """True when the CT offset at `ts` is standard time (-6h)."""
    local = pd.Timestamp(ts).tz_convert(CT)
    return local.dst() == pd.Timedelta(0)


def local_day_ids(index):
    """Day number of each bar's *local* calendar date.

    `strategy.day_ids` returns `index.asi8 // day`, and `asi8` is the **UTC**
    epoch no matter how the index is labelled. So feeding a CT-labelled index to
    the unmodified day ids still groups by UTC day, which splits a Globex
    session in two: the 22:00 UTC half lands on day D and the 00:00-10:00 UTC
    half on D+1, so the `hour >= 17` rule pushes the morning bars to D+2. The
    session id has to come from the local date for the grouping to be right.
    """
    return np.asarray([d.toordinal() for d in index.tz_convert(CT).date],
                      dtype="int64")


@contextlib.contextmanager
def local_day_grouping(enabled):
    """Use local-day ids for the CT run; restore the originals after."""
    if not enabled:
        yield
        return
    saved = strategy_mod.day_ids
    strategy_mod.day_ids = local_day_ids
    try:
        yield
    finally:
        strategy_mod.day_ids = saved


def run(frame, cfg):
    df = frame.copy()
    if cfg["ct"]:
        df.index = df.index.tz_convert(CT)
    df = add_atr(df, STRATEGY["atr_len"])
    with local_day_grouping(cfg["ct"]):
        stats, trades = run_config(
            df, cfg["asia"], cfg["ny_late"], STRATEGY["rr"], STRATEGY["atr_mult"],
            STRATEGY["entry_buffer"], STRATEGY["entry_mode"], STRATEGY["tp_mode"],
            cfg["exit_hour"], cost=STRATEGY["cost"],
            skip_sunday=STRATEGY["skip_sunday"], sl_mode=STRATEGY["sl_mode"],
            wick_buffer=STRATEGY["wick_buffer"])
    for t in trades:
        # Re-anchor to UTC: only the *clock the strategy reads* was CT.  `side`
        # is left exactly as `find_trades` emits it ("long"/"short").
        for k in ("signal_time", "entry_time", "exit_time"):
            if t.get(k) is not None:
                t[k] = pd.Timestamp(t[k]).tz_convert("UTC")
    return stats, trades


def bucket(trades):
    winter = [t for t in trades if in_winter(t["entry_time"])]
    summer = [t for t in trades if not in_winter(t["entry_time"])]
    return winter, summer


def brief(trades):
    if not trades:
        return dict(n=0, r=0.0, wr=0.0, pf=0.0)
    r = np.array([t["r"] for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    gl = abs(losses.sum())
    return dict(n=len(r), r=round(float(r.sum()), 2),
                wr=round(100.0 * len(wins) / len(r), 1),
                pf=round(float(wins.sum() / gl), 2) if gl else float("inf"))


def key(t):
    """Session identity: the UTC instant of the entry bar.

    The two configs group sessions differently by construction, so anything
    derived from the grouping (the CT session date, say) is not comparable
    across them.  The entry instant is: one entry bar per session either way.
    """
    return f"{pd.Timestamp(t['entry_time']):%Y-%m-%d %H:%M}"


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--period", default="730d")
    p.add_argument("--interval", default="60m")
    p.add_argument("--detail", action="store_true",
                   help="list every winter trade where the two configs differ")
    args = p.parse_args()

    cache = os.path.join(HERE, ".cache",
                         f"{args.symbol.replace('=', '_')}_{args.period}_{args.interval}.pkl")
    if os.path.exists(cache):
        print(f"Loading {args.symbol} {args.period} {args.interval} (cached) ...")
        frame = pd.read_pickle(cache)
    else:
        print(f"Loading {args.symbol} {args.period} {args.interval} ...")
        frame = load_data(args.symbol, args.period, args.interval)
        os.makedirs(os.path.dirname(cache), exist_ok=True)
        frame.to_pickle(cache)
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    print(f"{len(frame)} bars  {frame.index[0]:%Y-%m-%d} -> {frame.index[-1]:%Y-%m-%d}\n")

    results = {}
    for cfg in (BASELINE, GLOBEX, LIVE):
        stats, trades = run(frame, cfg)
        winter, summer = bucket(trades)
        results[cfg["label"]] = (stats, trades, winter, summer)

    base_stats, base_trades, base_w, base_s = results["fixed-UTC"]
    glob_stats, glob_trades, glob_w, glob_s = results["globex-CT"]
    live_w = results["live (UTC ref)"][2]

    print(f"{'':14} {'all':>26}   {'winter only (CST)':>26}")
    print(f"{'config':14} {'n':>4} {'R':>8} {'WR%':>7} {'PF':>6}  "
          f"{'n':>4} {'R':>8} {'WR%':>7} {'PF':>6}")
    for label, stats, _t, w, _s in (("fixed-UTC", base_stats, base_trades, base_w, base_s),
                                    ("globex-CT", glob_stats, glob_trades, glob_w, glob_s)):
        a, b = brief(_t), brief(w)
        print(f"{label:14} {a['n']:>4} {a['r']:>8.2f} {a['wr']:>7.1f} {a['pf']:>6.2f}  "
              f"{b['n']:>4} {b['r']:>8.2f} {b['wr']:>7.1f} {b['pf']:>6.2f}")

    bs_keys = {key(t) for t in base_s}
    gs_keys = {key(t) for t in glob_s}
    boundary = sorted((bs_keys ^ gs_keys) |
                      {k for k in bs_keys & gs_keys
                       if next(t for t in base_s if key(t) == k)["r"]
                       != next(t for t in glob_s if key(t) == k)["r"]})
    print(f"\nsummer (CDT): {brief(base_s)['n']} vs {brief(glob_s)['n']} trades, "
          f"{brief(base_s)['r']:+.2f}R vs {brief(glob_s)['r']:+.2f}R")
    if not boundary:
        print("  no difference at all: in summer the CT window describes exactly the"
              "\n  same hours as the fixed one, so the two runs are the same run.")
    else:
        print(f"  both configs describe the same hours in summer, so the *windows* "
              f"are identical; {len(boundary)} entry/entries still differ, because "
              f"the session *grouping* is not: `skip_sunday` tests the first "
              f"available bar, and under CT a feed gap that eats the Sunday-evening "
              f"prints leaves the morning-only session correctly identified as a "
              f"Sunday, where the UTC clock saw a Monday and traded it.")
    for k in boundary:
        b = next((t for t in base_s if key(t) == k), None)
        g = next((t for t in glob_s if key(t) == k), None)
        was = f"{b['r']:+.2f}R ({b['reason']})" if b else "no trade"
        now = f"{g['r']:+.2f}R ({g['reason']})" if g else "no trade"
        print(f"    {k} UTC {pd.Timestamp(k):%a}: baseline {was} -> globex {now}")

    # Which of the two changes moves the winter numbers: the session window or
    # the reference window?  The live row changes only the session window.
    live_b = brief(live_w)
    print(f"\nwinter, the two changes separately:")
    print(f"  {'UTC session + UTC ref (baseline)':34} {brief(base_w)['n']:>4} "
          f"{brief(base_w)['r']:>+8.2f}R")
    print(f"  {'Globex session + UTC ref (live)':34} {live_b['n']:>4} "
          f"{live_b['r']:>+8.2f}R   session window: "
          f"{live_b['r'] - brief(base_w)['r']:+.2f}R")
    print(f"  {'Globex session + CT ref (globex)':34} {brief(glob_w)['n']:>4} "
          f"{brief(glob_w)['r']:>+8.2f}R   reference window: "
          f"{brief(glob_w)['r'] - live_b['r']:+.2f}R")
    print("  the third row is the full port; the difference between the rows is")
    print("  the reference clock, not the session clock.")

    bw = {session_label(t): t for t in base_w}
    gw = {session_label(t): t for t in glob_w}
    only_base = sorted(set(bw) - set(gw))
    only_glob = sorted(set(gw) - set(bw))
    moved = [k for k in sorted(set(bw) & set(gw)) if bw[k]["r"] != gw[k]["r"]]

    print(f"\nwinter session diff ({len(base_w)} vs {len(glob_w)} trades):")
    print(f"  dropped by globex : {len(only_base)}")
    print(f"  added by globex   : {len(only_glob)}")
    print(f"  same session, different result: {len(moved)}")
    dr = brief(glob_w)["r"] - brief(base_w)["r"]
    print(f"  winter net R      : {brief(base_w)['r']:+.2f} -> {brief(glob_w)['r']:+.2f} "
          f"({dr:+.2f}R)")

    print(f"\nwinter net R by month (the old fixed window vs Globex):")
    months = sorted({f"{pd.Timestamp(t['entry_time']):%Y-%m}" for t in base_w + glob_w})
    print(f"  {'month':8} {'base n':>6} {'base R':>8} {'glob n':>6} {'glob R':>8} {'dR':>7}")
    for m in months:
        b = [t for t in base_w if f"{pd.Timestamp(t['entry_time']):%Y-%m}" == m]
        g = [t for t in glob_w if f"{pd.Timestamp(t['entry_time']):%Y-%m}" == m]
        print(f"  {m:8} {brief(b)['n']:>6} {brief(b)['r']:>8.2f} "
              f"{brief(g)['n']:>6} {brief(g)['r']:>8.2f} "
              f"{brief(g)['r'] - brief(b)['r']:>+7.2f}")

    if args.detail:
        for label, keys, src in (("dropped", only_base, bw), ("added", only_glob, gw)):
            for k in keys:
                t = src[k]
                print(f"    {label:7} {k} {t['side']:>4} entry "
                      f"{pd.Timestamp(t['entry_time']):%m-%d %H:%M} UTC  {t['r']:+.2f}R "
                      f"({t['reason']})")
        for k in moved:
            b, g = bw[k], gw[k]
            print(f"    moved   {k} entry {pd.Timestamp(b['entry_time']):%H:%M} -> "
                  f"{pd.Timestamp(g['entry_time']):%H:%M} UTC  "
                  f"{b['r']:+.2f}R -> {g['r']:+.2f}R ({b['reason']} -> {g['reason']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
