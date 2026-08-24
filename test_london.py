import itertools
import time

import pandas as pd

from backtest import load_data, run_config
from strategy import atr_values

INSTRUMENTS = [
    ("EURUSD", "EURUSD=X", 0.0001),
    ("GBPUSD", "GBPUSD=X", 0.00015),
    ("USDJPY", "USDJPY=X", 0.005),
    ("GOLD", "GC=F", 0.5),
]
TRIGGER = (7, 13)
REFERENCE = (22, 10)
SPLIT = 0.7
ENTRY_BUFFERS = [0.5, 1.0]
WICK_BUFFERS = [0.25, 0.5, 1.0]
TRENDS = [False, True]
EXITS = [16, 17]


def main():
    t0 = time.time()
    grids = list(itertools.product(ENTRY_BUFFERS, WICK_BUFFERS, TRENDS, EXITS))
    data = {}
    for name, symbol, cost in INSTRUMENTS:
        df = load_data(symbol, "365d", "60m")
        data[name] = {"df": df, "split": int(len(df) * 0.7), "cost": cost,
                      "atrs": {10: atr_values(df, 10)}}

    survivors = []
    diag = {}
    tested = 0
    for ebuf, wb, trend, exit_h in grids:
        ok = True
        details = {}
        for name, d in data.items():
            res = {}
            for mode in ("stop", "stop-next"):
                s_tr, _ = run_config(d["df"].iloc[:d["split"]], TRIGGER, REFERENCE, 0.0, 1.0,
                                     ebuf, "reclaim", "opposite", exit_h, atr=d["atrs"][10][:d["split"]],
                                     cost=d["cost"], skip_sunday=True, entry_bar_tp=False,
                                     sl_mode="wick", wick_buffer=wb,
                                     trend_filter=trend)
                s_te, _ = run_config(d["df"].iloc[d["split"]:], TRIGGER, REFERENCE, 0.0, 1.0,
                                     ebuf, "reclaim", "opposite", exit_h, atr=d["atrs"][10][d["split"]:],
                                     cost=d["cost"], skip_sunday=True, entry_bar_tp=False,
                                     sl_mode="wick", wick_buffer=wb,
                                     trend_filter=trend)
                res[("train", mode)] = s_tr
                res[("test", mode)] = s_te
            for key, s in res.items():
                if s.get("trades", 0) == 0 or s.get("total_r", 0) <= 0:
                    ok = False
                    break
            if not ok:
                break
            details[name] = res
        tested += 1
        diag[(ebuf, wb, trend, exit_h)] = details
        if not ok:
            continue
        min_r = min(s.get("total_r", 0) for d in details.values() for s in d.values())
        trades_train = sum(d[("train", "stop")].get("trades", 0) for d in details.values())
        trades_test = sum(d[("test", "stop")].get("trades", 0) for d in details.values())
        if trades_train < 80 or trades_test < 40:
            continue
        test_vals = [s["total_r"] for d in details.values() for s in
                     (d[("test", "stop")], d[("test", "stop-next")])]
        survivors.append({"entry_buf": ebuf, "wick_buf": wb, "trend": int(trend),
                          "exit": exit_h, "min_segment_r": round(min_r, 2),
                          "test_avg_r": round(sum(test_vals) / len(test_vals), 2),
                          "trades_train": trades_train, "trades_test": trades_test})
        if tested % 8 == 0:
            print(f"  {tested}/{len(grids)} done, {len(survivors)} survivors, {time.time()-t0:.0f}s")

    print(f"\nDone in {time.time()-t0:.0f}s. {tested} grids tested.")
    if not survivors:
        print("NO London-reclaim configs survived the strict filter. Best-effort diagnostics:")
        best = None
        for key, details in diag.items():
            tot = sum(s.get("total_r", 0) for d in details.values() for s in d.values())
            if best is None or tot > best[1]:
                best = (key, tot, details)
        if best:
            key, tot, details = best
            print(f"best config {key}: sum {tot:+.2f}R across all segments/instruments")
            for name, d in details.items():
                for seg_mode, s in d.items():
                    print(f"  {name} {seg_mode}: {s.get('trades', 0)} trades {s.get('total_r', 0):+.2f}R")
        return
    res = pd.DataFrame(survivors).sort_values("test_avg_r", ascending=False)
    pd.set_option("display.width", 200)
    print(f"\n=== {len(res)} SURVIVORS ===")
    print(res.head(20).to_string(index=False))
    res.to_csv("results/london_survivors.csv", index=False)
    print("\nSaved results/london_survivors.csv")


if __name__ == "__main__":
    main()
