import math

import numpy as np
import pandas as pd

from backtest import load_data, run_config
from strategy import add_atr

ASIA = (22, 10)
NY_LATE = (19, 21)
BUF, ATR_MULT, ATR_LEN, RR, EXIT_HOUR = 1.0, 1.0, 10, 0.75, 8
INSTRUMENTS = [
    ("GOLD", "GC=F", 0.5),
    ("EURUSD", "EURUSD=X", 0.0001),
    ("GBPUSD", "GBPUSD=X", 0.00015),
    ("USDJPY", "USDJPY=X", 0.005),
]


def audit_data(df, name):
    issues = []
    n = len(df)
    bad = int((df["High"] < df["Low"]).sum())
    flat = int((df["High"] == df["Low"]).sum())
    dup = int(n - len(df.index.unique()))
    full_hours = pd.date_range(df.index[0], df.index[-1], freq="1h", tz="UTC")
    missing = len(full_hours) - n
    issues.append(f"bars {n}, missing hours {missing}, High<Low {bad}, High==Low {flat}, dupes {dup}")
    return "; ".join(issues)


def wr_ci(wins, n, z=1.96):
    if n == 0:
        return (0.0, 100.0)
    p = wins / n
    se = math.sqrt(p * (1 - p) / n)
    return (100 * max(0.0, p - z * se), 100 * min(1.0, p + z * se))


def main():
    all_trades = {}
    print("=== DATA QUALITY AUDIT ===")
    for name, symbol, cost in INSTRUMENTS:
        df = load_data(symbol, "365d", "60m")
        add_atr(df, ATR_LEN)
        print(f"{name}: {audit_data(df, name)}")
        _, trades = run_config(df, ASIA, NY_LATE, RR, ATR_MULT, BUF, "stop", "rr",
                               EXIT_HOUR, cost=cost, skip_sunday=True, entry_bar_tp=False)
        for t in trades:
            t["symbol"] = name
        all_trades[name] = trades

    frames = []
    for name, trades in all_trades.items():
        tdf = pd.DataFrame(trades)
        tdf = tdf[tdf["entry_time"].dt.year == 2026].copy()
        frames.append(tdf)
    t = pd.concat(frames).sort_values("entry_time").reset_index(drop=True)
    t["month"] = t["entry_time"].dt.strftime("%Y-%m")
    t["cum_r"] = t["r"].cumsum().round(2)

    pd.set_option("display.width", 250)
    print("\n=== 2026 HONEST BACKTEST (level fills, NO same-bar TP, pessimistic costs, skip-Sunday) ===")
    r = t["r"]
    wins = int((r > 0).sum())
    lo, hi = wr_ci(wins, len(t))
    mean_r = r.mean()
    se = r.std(ddof=1) / math.sqrt(len(r))
    t_stat = mean_r / se if se > 0 else 0.0
    eq = r.cumsum()
    dd = float((eq.cummax() - eq).max())
    print(f"trades {len(t)} | WR {100*wins/len(t):.1f}% (95% CI {lo:.1f}-{hi:.1f}%) | "
          f"total {r.sum():+.2f}R | mean {mean_r:+.3f}R (t={t_stat:+.2f}) | PF "
          f"{r[r>0].sum()/abs(r[r<=0].sum()):.2f} | maxDD {dd:.2f}R")
    sig = "STATISTICALLY DISTINGUISHABLE FROM ZERO" if abs(t_stat) > 1.96 else "NOT DISTINGUISHABLE FROM ZERO"
    print(f"edge significance: {sig} (|t| needs > 1.96)")

    print("\n=== MONTH BY MONTH ===")
    rows = []
    for m in t["month"].unique():
        mdf = t[t["month"] == m]
        mr = mdf["r"]
        mw = int((mr > 0).sum())
        mlo, mhi = wr_ci(mw, len(mdf))
        rows.append({"month": m, "trades": len(mdf), "win_rate": round(100 * mw / len(mdf), 1),
                     "wr_95ci": f"{mlo:.0f}-{mhi:.0f}%", "total_r": round(mr.sum(), 2),
                     "avg_r": round(mr.mean(), 3),
                     "pf": round(mr[mr > 0].sum() / abs(mr[mr <= 0].sum()), 2) if (mr <= 0).any() else float("inf"),
                     "usd": round(mr.sum() * 100, 2)})
    months = pd.DataFrame(rows)
    print(months.to_string(index=False))

    print("\n=== PER INSTRUMENT ===")
    for name in frames[0]["symbol"].unique() if len(frames) else []:
        pass
    for name, trades in all_trades.items():
        tdf = pd.DataFrame([x for x in trades if x["entry_time"].year == 2026])
        if tdf.empty:
            print(f"{name}: no trades")
            continue
        rr_ = tdf["r"]
        w = int((rr_ > 0).sum())
        lo2, hi2 = wr_ci(w, len(tdf))
        m = rr_.mean()
        s = rr_.std(ddof=1) / math.sqrt(len(rr_))
        print(f"{name}: {len(tdf)} trades | WR {100*w/len(tdf):.1f}% (CI {lo2:.0f}-{hi2:.0f}%) | "
              f"{rr_.sum():+.2f}R | mean {m:+.3f}R (t={m/s if s>0 else 0:+.2f})")

    print("\n=== NIGHTLY PORTFOLIO RISK ===")
    nightly = t.groupby(t["entry_time"].dt.date)["r"].sum()
    print(f"worst night: {nightly.min():+.2f}R | best night: {nightly.max():+.2f}R | "
          f"nights all-stop (<= -3R): {int((nightly <= -3).sum())} of {len(nightly)}")

    t.to_csv("results/trades_2026_honest.csv", index=False)
    months.to_csv("results/months_2026_honest.csv", index=False)
    print("\nSaved results/trades_2026_honest.csv, results/months_2026_honest.csv")


if __name__ == "__main__":
    main()
