import math

from backtest import load_data, run_config
from strategy import atr_values

SPLIT = 0.7


def stats(trades):
    if not trades:
        return "no trades"
    import numpy as np
    r = np.array([t["r"] for t in trades])
    n = len(r)
    wr = 100 * (r > 0).sum() / n
    mean = r.mean()
    se = r.std(ddof=1) / math.sqrt(n) if n > 1 else 0
    t = mean / se if se > 0 else 0.0
    pf = r[r > 0].sum() / abs(r[r <= 0].sum()) if (r <= 0).any() else float("inf")
    return f"{n:>3} trades | WR {wr:>5.1f}% | {r.sum():>+7.2f}R | mean {mean:>+.3f}R | t {t:>+5.2f} | PF {pf:.2f}"


def main():
    df = load_data("USDJPY=X", "365d", "60m")
    a = atr_values(df, 10)
    split = int(len(df) * SPLIT)
    configs = [
        ("wick+opp, no reclaim", dict(sl_mode="wick", wick_buffer=0.5, tp_mode="opposite",
                                      rr=0.0, entry_mode="stop", trend_filter=False)),
        ("wick+opp, reclaim   ", dict(sl_mode="wick", wick_buffer=0.5, tp_mode="opposite",
                                      rr=0.0, entry_mode="reclaim", trend_filter=False)),
        ("wick+opp, reclaim+trend", dict(sl_mode="wick", wick_buffer=0.5, tp_mode="opposite",
                                         rr=0.0, entry_mode="reclaim", trend_filter=True)),
    ]
    for name, cfg in configs:
        print(f"--- USDJPY {name} ---")
        for label, d, aa in (("train", df.iloc[:split], a[:split]),
                             ("test ", df.iloc[split:], a[split:])):
            s, trades = run_config(d, (22, 10), (19, 21), cfg["rr"], 1.0, 1.0, cfg["entry_mode"],
                                   cfg["tp_mode"], 8, atr=aa, cost=0.005, skip_sunday=True,
                                   entry_bar_tp=False, sl_mode=cfg["sl_mode"],
                                   wick_buffer=cfg["wick_buffer"], trend_filter=cfg["trend_filter"])
            print(f"  {label}: {stats(trades)}")
        print()


if __name__ == "__main__":
    main()
