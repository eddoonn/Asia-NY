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
SPLIT = 0.7
BASES = {
    "wick_opp": dict(sl_mode="wick", wick_buffer=0.5, tp_mode="opposite", rr=0.0),
    "atr_075": dict(sl_mode="atr", wick_buffer=0.5, tp_mode="rr", rr=0.75),
}
VARIANTS = list(itertools.product([False, True], [False, True]))  # (trend_filter, reclaim)


def run_variant(data, base, trend, reclaim):
    rows = []
    for name, d in data.items():
        for label, df, a in (("train", d["df"].iloc[:d["split"]], d["atrs"][10][:d["split"]]),
                             ("test", d["df"].iloc[d["split"]:], d["atrs"][10][d["split"]:])):
            modes = ("stop",) if reclaim else ("stop", "stop-next")
            for mode in modes:
                s, _ = run_config(df, (22, 10), (19, 21), base["rr"], 1.0, 1.0,
                                  "reclaim" if reclaim else mode, base["tp_mode"], 8,
                                  atr=a, cost=d["cost"], skip_sunday=True,
                                  entry_bar_tp=False, sl_mode=base["sl_mode"],
                                  wick_buffer=base["wick_buffer"], trend_filter=trend)
                rows.append({"base": base["name"], "trend": trend, "reclaim": reclaim,
                             "instrument": name, "segment": label, "mode": mode,
                             "trades": s.get("trades", 0),
                             "total_r": round(s.get("total_r", 0), 2),
                             "win_rate": s.get("win_rate_pct", 0)})
    return rows


def main():
    t0 = time.time()
    data = {}
    for name, symbol, cost in INSTRUMENTS:
        df = load_data(symbol, "365d", "60m")
        data[name] = {"df": df, "split": int(len(df) * SPLIT), "cost": cost,
                      "atrs": {10: atr_values(df, 10)}}

    all_rows = []
    for base_name, base in BASES.items():
        base["name"] = base_name
        for trend, reclaim in VARIANTS:
            rows = run_variant(data, base, trend, reclaim)
            all_rows.extend(rows)
            df = pd.DataFrame(rows)
            core = df[~((df["reclaim"]) & (df["mode"] != "stop"))]
            seg = core.groupby(["segment", "instrument"])["total_r"].sum().unstack()
            pos_all = bool((seg > 0).all().all()) if not seg.empty else False
            print(f"base={base_name} trend={int(trend)} reclaim={int(reclaim)}: "
                  f"all-instruments-positive={pos_all}")
            print(seg.to_string())
            print()

    res = pd.DataFrame(all_rows)
    res.to_csv("results/signal_variants.csv", index=False)
    print(f"Done in {time.time() - t0:.0f}s — saved results/signal_variants.csv")


if __name__ == "__main__":
    main()
