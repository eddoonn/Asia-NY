import itertools
import time

import pandas as pd

from backtest import load_data, run_config
from strategy import atr_values

INSTRUMENTS = [
    ("GOLD", "GC=F", 0.5),
    ("EURUSD", "EURUSD=X", 0.0001),
    ("GBPUSD", "GBPUSD=X", 0.00015),
    ("USDJPY", "USDJPY=X", 0.005),
]
ASIA_WINDOWS = [(22, 9), (22, 10)]
NY_WINDOWS = [(19, 21), (20, 22)]
WICK_BUFFERS = [0.25, 0.5, 1.0]
EXITS = [8, 12]
ATR_LENS = [10, 20]
ENTRY_BUFFERS = [0.5, 1.0]
SPLIT = 0.7


def main():
    t0 = time.time()
    grids = list(itertools.product(ASIA_WINDOWS, NY_WINDOWS, WICK_BUFFERS, EXITS, ATR_LENS, ENTRY_BUFFERS))
    print(f"{len(grids)} configs x {len(INSTRUMENTS)} instruments x 4 evaluations "
          f"(TP=opposite NY level, SL=beyond sweep wick + buffer)")

    data = {}
    for name, symbol, cost in INSTRUMENTS:
        df = load_data(symbol, "365d", "60m")
        split = int(len(df) * SPLIT)
        data[name] = {"df": df, "split": split, "cost": cost,
                      "atrs": {n: atr_values(df, n) for n in set(ATR_LENS)}}

    survivors = []
    tested = 0
    for asia, ny, wb, exit_h, atr_len, ebuf in grids:
        cfg = {"asia": asia, "ny": ny, "buf": ebuf, "am": 1.0, "rr": 0.0,
               "exit": exit_h, "atr_len": atr_len}
        ok = True
        details = {}
        for name, d in data.items():
            cfg["cost"] = d["cost"]
            res = {}
            for mode in ("stop", "stop-next"):
                a_tr = d["atrs"][atr_len][:d["split"]]
                a_te = d["atrs"][atr_len][d["split"]:]
                s_tr, _ = run_config(d["df"].iloc[:d["split"]], asia, ny, 0.0, 1.0, ebuf,
                                     mode, "opposite", exit_h, atr=a_tr, cost=d["cost"],
                                     skip_sunday=True, entry_bar_tp=False,
                                     sl_mode="wick", wick_buffer=wb)
                s_te, _ = run_config(d["df"].iloc[d["split"]:], asia, ny, 0.0, 1.0, ebuf,
                                     mode, "opposite", exit_h, atr=a_te, cost=d["cost"],
                                     skip_sunday=True, entry_bar_tp=False,
                                     sl_mode="wick", wick_buffer=wb)
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
        if not ok:
            continue
        min_r = min(s.get("total_r", 0) for d in details.values() for s in d.values())
        trades_train = sum(d[("train", "stop")].get("trades", 0) for d in details.values())
        trades_test = sum(d[("test", "stop")].get("trades", 0) for d in details.values())
        if trades_train < 80 or trades_test < 40:
            continue
        test_vals = [s["total_r"] for d in details.values() for s in
                     (d[("test", "stop")], d[("test", "stop-next")])]
        survivors.append({
            "asia": f"{asia[0]:02d}-{asia[1]:02d}", "ny": f"{ny[0]:02d}-{ny[1]:02d}",
            "wick_buf": wb, "entry_buf": ebuf, "exit": exit_h, "atr_len": atr_len,
            "min_segment_r": round(min_r, 2),
            "test_avg_r": round(sum(test_vals) / len(test_vals), 2),
            "trades_train": trades_train, "trades_test": trades_test,
        })
        if tested % 25 == 0:
            print(f"  {tested}/{len(grids)} done, {len(survivors)} survivors, {time.time()-t0:.0f}s")

    print(f"\nDone in {time.time()-t0:.0f}s. {tested} grids tested.")
    if not survivors:
        print("NO CONFIG SURVIVED the fill-agnostic walk-forward filter with wick stops + opposite-level targets.")
        return
    res = pd.DataFrame(survivors).sort_values("test_avg_r", ascending=False)
    pd.set_option("display.width", 200)
    print(f"\n=== {len(res)} SURVIVORS ===")
    print(res.head(20).to_string(index=False))
    res.to_csv("results/wick_survivors.csv", index=False)
    print("\nSaved results/wick_survivors.csv")


if __name__ == "__main__":
    main()
