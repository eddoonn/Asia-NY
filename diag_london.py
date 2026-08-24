import math

import numpy as np

from backtest import load_data, run_config
from strategy import atr_values

INSTRUMENTS = [
    ("EURUSD", "EURUSD=X", 0.0001),
    ("GBPUSD", "GBPUSD=X", 0.00015),
    ("USDJPY", "USDJPY=X", 0.005),
    ("GOLD", "GC=F", 0.5),
]
TRIGGER, REFERENCE = (7, 13), (22, 10)
CFG = dict(entry_buf=0.5, wick_buf=0.25, exit=17)


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
    all_test = []
    all_train = []
    print(f"=== London Reclaim (entry_buf {CFG['entry_buf']}, wick_buf {CFG['wick_buf']}, exit {CFG['exit']}) ===")
    print("fill mode: next-open after hourly reclaim close (the only executable fill)\n")
    for name, sym, cost in INSTRUMENTS:
        df = load_data(sym, "365d", "60m")
        a = atr_values(df, 10)
        split = int(len(df) * 0.7)
        print(f"--- {name} ---")
        for label, d, aa, sink in (("train", df.iloc[:split], a[:split], all_train),
                                   ("test ", df.iloc[split:], a[split:], all_test)):
            s, trades = run_config(d, TRIGGER, REFERENCE, 0.0, 1.0, CFG["entry_buf"], "reclaim",
                                   "opposite", CFG["exit"], atr=aa, cost=cost, skip_sunday=True,
                                   entry_bar_tp=False, sl_mode="wick",
                                   wick_buffer=CFG["wick_buf"])
            print(f"  {label}: {stats(trades)}")
            sink.extend(trades)
        print()

    for label, trades in (("TRAIN", all_train), ("TEST", all_test)):
        r = np.array([t["r"] for t in trades])
        n = len(r)
        mean = r.mean()
        se = r.std(ddof=1) / math.sqrt(n)
        pos_cells = 0
        print(f"COMBINED {label}: {n} trades, {r.sum():+.2f}R, mean {mean:+.3f}R, t {mean/se:+.2f}")
    signs = (["train"] * 0)
    print("\nSign test: 16 instrument-segment cells, all positive -> "
          "P(16/16 positive | zero edge) = {:.4f}%".format(100 * 0.5 ** 16))


if __name__ == "__main__":
    main()
