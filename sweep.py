import argparse
import itertools

import numpy as np
import pandas as pd

from backtest import load_data, run_config
from strategy import atr_values


def segment_r(trades, n_segments):
    if not trades:
        return []
    times = np.array([t["entry_time"].value for t in trades])
    rs = np.array([t["r"] for t in trades])
    order = np.argsort(times)
    times, rs = times[order], rs[order]
    edges = np.quantile(times, np.linspace(0, 1, n_segments + 1))
    out = []
    for i in range(n_segments):
        lo, hi = edges[i], edges[i + 1]
        if i == n_segments - 1:
            hi += 1
        mask = (times >= lo) & (times < hi)
        out.append(float(rs[mask].sum()))
    return out


def main():
    p = argparse.ArgumentParser(description="Walk-forward parameter sweep for Asia-session gold reversal")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--period", default="365d")
    p.add_argument("--interval", default="60m")
    p.add_argument("--min-trades", type=int, default=60)
    p.add_argument("--segments", type=int, default=3)
    p.add_argument("--top", type=int, default=15)
    args = p.parse_args()

    df = load_data(args.symbol, args.period, args.interval)
    atrs = {n: atr_values(df, n) for n in (10, 14, 20)}

    asia_windows = [(22, 9), (22, 10), (21, 9), (0, 9)]
    ny_windows = [(19, 22), (20, 22), (19, 21)]
    buffers = [0.25, 0.5, 0.75, 1.0]
    atr_mults = [1.0, 1.5, 2.0]
    exit_hours = [8, 9, 12]
    tp_options = [0.4, 0.5, 0.75, 1.0, "opposite"]

    rows = []
    combos = list(itertools.product(asia_windows, ny_windows, buffers, atr_mults,
                                    exit_hours, tp_options, atrs.keys()))
    print(f"Testing {len(combos)} configurations on {len(df)} bars "
          f"({args.segments}-segment walk-forward filter)...")

    for i, (asia, ny_late, buf, atr_mult, exit_hour, tp, atr_len) in enumerate(combos):
        tp_mode = "opposite" if tp == "opposite" else "rr"
        rr = None if tp == "opposite" else tp
        stats, trades = run_config(df, asia, ny_late, rr, atr_mult, buf, "stop",
                                   tp_mode, exit_hour, atr=atrs[atr_len])
        if stats.get("trades", 0) < args.min_trades:
            continue
        segs = segment_r(trades, args.segments)
        rows.append({
            "asia": f"{asia[0]:02d}-{asia[1]:02d}",
            "ny_late": f"{ny_late[0]:02d}-{ny_late[1]:02d}",
            "buf": buf,
            "atr_mult": atr_mult,
            "atr_len": atr_len,
            "exit_hour": exit_hour,
            "tp": tp if tp == "opposite" else f"{tp}R",
            "seg_r": " / ".join(f"{s:+.1f}" for s in segs),
            "min_seg_r": round(min(segs), 2),
            **stats,
        })
        if (i + 1) % 500 == 0:
            print(f"  {i + 1}/{len(combos)} done, {len(rows)} qualifying")

    if not rows:
        print("No configuration met the filters.")
        return

    res = pd.DataFrame(rows)
    robust = res[res["min_seg_r"] > 0].sort_values("total_r", ascending=False)

    pd.set_option("display.width", 250)
    print(f"\n=== Top {args.top} robust configs (profitable in ALL {args.segments} segments, "
          f"min {args.min_trades} trades, {args.symbol} {args.period}) ===")
    print(robust.head(args.top).to_string(index=False))

    print(f"\n=== Top {args.top} by total R (no consistency filter) ===")
    print(res.sort_values("total_r", ascending=False).head(args.top).to_string(index=False))

    res.to_csv("results/sweep_robust.csv", index=False)
    print(f"\n{len(res)} qualifying configs saved to results/sweep_robust.csv "
          f"({len(robust)} passed the all-segments filter)")


if __name__ == "__main__":
    main()
