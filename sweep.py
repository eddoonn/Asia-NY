import argparse
import itertools

import pandas as pd

from backtest import load_data, run_config
from strategy import add_atr


def main():
    p = argparse.ArgumentParser(description="Parameter sweep for Asia-session gold reversal")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--period", default="180d")
    p.add_argument("--interval", default="60m")
    p.add_argument("--min-trades", type=int, default=40)
    p.add_argument("--top", type=int, default=15)
    args = p.parse_args()

    df = load_data(args.symbol, args.period, args.interval)
    df = add_atr(df, 14)

    asia_windows = [(0, 9), (0, 8), (22, 9), (23, 9)]
    ny_windows = [(18, 22), (19, 22), (20, 22), (19, 21)]
    entry_modes = ["stop", "close"]
    buffers = [0.0, 0.25, 0.5]
    atr_mults = [0.5, 1.0, 1.5, 2.0]
    exit_hours = [9, 12]
    tp_options = [("rr", 0.5), ("rr", 1.0), ("rr", 2.0), ("opposite", None)]

    rows = []
    combos = list(itertools.product(asia_windows, ny_windows, entry_modes, buffers,
                                    atr_mults, exit_hours, tp_options))
    print(f"Testing {len(combos)} configurations on {len(df)} bars...")
    for asia, ny_late, entry_mode, buf, atr_mult, exit_hour, (tp_mode, rr) in combos:
        stats, _ = run_config(df, asia, ny_late, rr, atr_mult, buf, entry_mode, tp_mode, exit_hour)
        if stats.get("trades", 0) == 0:
            continue
        rows.append({
            "asia": f"{asia[0]:02d}-{asia[1]:02d}",
            "ny_late": f"{ny_late[0]:02d}-{ny_late[1]:02d}",
            "entry": entry_mode,
            "buf": buf,
            "atr_mult": atr_mult,
            "exit_hour": exit_hour,
            "tp": tp_mode if tp_mode == "opposite" else f"{rr}R",
            **stats,
        })

    res = pd.DataFrame(rows)
    res = res[res["trades"] >= args.min_trades]
    res = res.sort_values("total_r", ascending=False)

    pd.set_option("display.width", 200)
    print(f"\n=== Top {args.top} by total R (min {args.min_trades} trades, {args.symbol} {args.period}) ===")
    print(res.head(args.top).to_string(index=False))

    by_pf = res.sort_values(["profit_factor", "total_r"], ascending=False)
    print(f"\n=== Top {args.top} by profit factor ===")
    print(by_pf.head(args.top).to_string(index=False))

    res.to_csv("results/sweep.csv", index=False)
    print(f"\nAll {len(res)} qualifying configs saved to results/sweep.csv")

    base = res[(res["asia"] == "00-09") & (res["ny_late"] == "18-22") &
               (res["entry"] == "stop") & (res["buf"] == 0.0) &
               (res["atr_mult"] == 1.0) & (res["exit_hour"] == 9) & (res["tp"] == "2.0R")]
    if len(base):
        print("\nBaseline config for reference:")
        print(base.to_string(index=False))


if __name__ == "__main__":
    main()
