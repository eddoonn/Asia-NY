import math

import numpy as np

from backtest import load_data, run_config
from strategy import atr_values


def stats(trades):
    if not trades:
        return "no trades"
    r = np.array([t["r"] for t in trades])
    n = len(r)
    wr = 100 * (r > 0).sum() / n
    mean = r.mean()
    se = r.std(ddof=1) / math.sqrt(n) if n > 1 else 0
    t = mean / se if se > 0 else 0.0
    pf = r[r > 0].sum() / abs(r[r <= 0].sum()) if (r <= 0).any() else float("inf")
    return f"{n:>3} trades | WR {wr:>5.1f}% | {r.sum():>+7.2f}R | mean {mean:>+.3f}R | t {t:>+5.2f} | PF {pf:.2f}"


def main():
    instruments = [
        ("USDJPY (searched)", "USDJPY=X", 0.005),
        ("EURJPY (never touched)", "EURJPY=X", 0.008),
        ("GBPJPY (never touched)", "GBPJPY=X", 0.012),
        ("AUDJPY (never touched)", "AUDJPY=X", 0.008),
    ]
    print("=== Tokyo-reclaim (wick+opposite+reclaim, skip-Sunday) — cross-instrument validation ===\n")
    for name, sym, cost in instruments:
        try:
            df = load_data(sym, "365d", "60m")
        except SystemExit:
            print(f"{name}: no data")
            continue
        a = atr_values(df, 10)
        split = int(len(df) * 0.7)
        print(f"--- {name} ---")
        for label, d, aa in (("train", df.iloc[:split], a[:split]),
                             ("test ", df.iloc[split:], a[split:])):
            s, trades = run_config(d, (22, 10), (19, 21), 0.0, 1.0, 1.0, "reclaim", "opposite", 8,
                                   atr=aa, cost=cost, skip_sunday=True, entry_bar_tp=False,
                                   sl_mode="wick", wick_buffer=0.5)
            print(f"  {label}: {stats(trades)}")
        print()


if __name__ == "__main__":
    main()
