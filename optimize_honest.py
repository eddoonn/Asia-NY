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
BUFFERS = [0.75, 1.0, 1.5]
ATR_MULTS = [1.0, 1.5, 2.0]
RRS = [0.5, 0.75, 1.0]
EXITS = [8, 12]
ATR_LENS = [10, 20]
SPLIT = 0.7


def evaluate(df, split, atrs, cfg):
    out = {}
    for mode in ("stop", "stop-next"):
        a_train = atrs[cfg["atr_len"]][:split]
        a_test = atrs[cfg["atr_len"]][split:]
        s_tr, _ = run_config(df.iloc[:split], cfg["asia"], cfg["ny"], cfg["rr"], cfg["am"],
                             cfg["buf"], mode, "rr", cfg["exit"], atr=a_train,
                             cost=cfg["cost"], skip_sunday=True, entry_bar_tp=False)
        s_te, _ = run_config(df.iloc[split:], cfg["asia"], cfg["ny"], cfg["rr"], cfg["am"],
                             cfg["buf"], mode, "rr", cfg["exit"], atr=a_test,
                             cost=cfg["cost"], skip_sunday=True, entry_bar_tp=False)
        out[("train", mode)] = s_tr
        out[("test", mode)] = s_te
    return out


def main():
    t0 = time.time()
    grids = list(itertools.product(ASIA_WINDOWS, NY_WINDOWS, BUFFERS, ATR_MULTS,
                                   RRS, EXITS, ATR_LENS))
    print(f"{len(grids)} configs x {len(INSTRUMENTS)} instruments x 4 evaluations "
          f"(train/test x level/next-open fills)")

    data = {}
    for name, symbol, cost in INSTRUMENTS:
        df = load_data(symbol, "365d", "60m")
        split = int(len(df) * SPLIT)
        data[name] = {
            "df": df, "split": split, "cost": cost,
            "atrs": {n: atr_values(df, n) for n in set(ATR_LENS)},
        }

    survivors = []
    tested = 0
    for asia, ny, buf, am, rr, exit_h, atr_len in grids:
        cfg = {"asia": asia, "ny": ny, "buf": buf, "am": am, "rr": rr,
               "exit": exit_h, "atr_len": atr_len}
        ok = True
        details = {}
        for name, d in data.items():
            cfg["cost"] = d["cost"]
            res = evaluate(d["df"], d["split"], d["atrs"], cfg)
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
        test_mean = sum(s["total_r"] for d in details.values() for s in
                        [d[("test", "stop")], d[("test", "stop-next")]]) / (4 * len(INSTRUMENTS))
        trades_test = sum(d[("test", "stop")].get("trades", 0) for d in details.values())
        trades_train = sum(d[("train", "stop")].get("trades", 0) for d in details.values())
        if trades_train < 80 or trades_test < 40:
            continue
        survivors.append({
            "asia": f"{asia[0]:02d}-{asia[1]:02d}", "ny": f"{ny[0]:02d}-{ny[1]:02d}",
            "buf": buf, "atr_mult": am, "rr": rr, "exit": exit_h, "atr_len": atr_len,
            "min_segment_r": round(min_r, 2),
            "test_avg_r_per_inst": round(test_mean, 2),
            "trades_train": trades_train, "trades_test": trades_test,
        })
        if tested % 50 == 0:
            print(f"  {tested}/{len(grids)} grids done, {len(survivors)} survivors, "
                  f"{time.time() - t0:.0f}s")

    print(f"\nDone in {time.time() - t0:.0f}s. {tested} grids tested.")
    if not survivors:
        print("NO CONFIG SURVIVED the fill-agnostic walk-forward filter. "
              "The honest conclusion: no tradeable edge found in this family.")
        return

    res = pd.DataFrame(survivors).sort_values("test_avg_r_per_inst", ascending=False)
    pd.set_option("display.width", 200)
    print(f"\n=== {len(res)} SURVIVORS: profitable in train+test, under level AND next-open fills, all 4 instruments ===")
    print(res.head(20).to_string(index=False))
    res.to_csv("results/optimization_survivors.csv", index=False)
    print("\nSaved results/optimization_survivors.csv")


if __name__ == "__main__":
    main()
