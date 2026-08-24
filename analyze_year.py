import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from analyze_month import stats_block, breakdown
from backtest import load_data, run_config
from strategy import add_atr

ASIA = (22, 10)
NY_LATE = (19, 21)
BUF, ATR_MULT, ATR_LEN, RR, EXIT_HOUR, COST = 1.0, 1.0, 10, 0.75, 8, 0.3


def month_drawdown(r):
    eq = r.cumsum()
    return float((eq.cummax() - eq).max())


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--year", default="2026")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--cost", type=float, default=0.3)
    p.add_argument("--entry-mode", default="stop")
    p.add_argument("--tag", default="")
    p.add_argument("--skip-sunday", action="store_true")
    a = p.parse_args()
    year = a.year
    df = load_data(a.symbol, "365d", "60m")
    add_atr(df, ATR_LEN)
    _, trades = run_config(df, ASIA, NY_LATE, RR, ATR_MULT, BUF, a.entry_mode, "rr",
                           EXIT_HOUR, cost=a.cost, skip_sunday=a.skip_sunday)
    t = pd.DataFrame(trades)
    t = t[t["entry_time"].dt.year == int(year)].copy().reset_index(drop=True)
    if t.empty:
        raise SystemExit(f"no trades in {year}")
    t["month"] = t["entry_time"].dt.strftime("%Y-%m")
    t["weekday"] = t["entry_time"].dt.day_name()
    t["cum_r"] = t["r"].cumsum().round(2)
    t["hold_h"] = ((t["exit_time"] - t["entry_time"]).dt.total_seconds() / 3600).round(1)

    pd.set_option("display.width", 250)
    print(f"=== {year} YEAR-TO-DATE — Asia Grab tuned config (cost {COST}) ===")
    print(f"bars: {len(df)} from {df.index[0].date()} to {df.index[-1].date()}")
    print(f"trades in {year}: {len(t)}\n")
    print(f"OVERALL: {stats_block(t)}\n")

    rows = []
    for m in t["month"].unique():
        mdf = t[t["month"] == m]
        s = stats_block(mdf)
        s["month"] = m
        s["dd_r"] = round(month_drawdown(mdf["r"]), 2)
        s["best_day_r"] = round(mdf.groupby(mdf["entry_time"].dt.date)["r"].sum().max(), 2)
        s["worst_day_r"] = round(mdf.groupby(mdf["entry_time"].dt.date)["r"].sum().min(), 2)
        s["pos_days"] = int((mdf.groupby(mdf["entry_time"].dt.date)["r"].sum() > 0).sum())
        s["trade_days"] = int(mdf["entry_time"].dt.date.nunique())
        rows.append(s)
    months = pd.DataFrame(rows)
    cols = ["month", "trades", "wins", "losses", "win_rate_pct", "total_r", "avg_r",
            "profit_factor", "avg_win_r", "avg_loss_r", "dd_r", "best_day_r",
            "worst_day_r", "pos_days", "trade_days"]
    print("=== MONTH BY MONTH ===")
    print(months[cols].to_string(index=False))

    print("\n=== BY SIDE (per month) ===")
    for m in t["month"].unique():
        print(f"\n{m}:")
        print(breakdown(t[t["month"] == m], "side").to_string())
        print("exit reasons:", dict(breakdown(t[t["month"] == m], "reason")["trades"]))
        print("avg MAE/MFE:", round(t[t['month'] == m]['mae_r'].mean(), 2), "/",
              round(t[t['month'] == m]['mfe_r'].mean(), 2), "R")

    print("\n=== WEEKDAY PERFORMANCE (2026) ===")
    print(breakdown(t, "weekday").to_string())

    risk = 100.0
    print(f"\nDollar view at {risk:.0f} USD risk/trade:")
    for _, row in months.iterrows():
        print(f"  {row['month']}: {row['total_r'] * risk:+.2f} USD  ({row['trades']} trades)")
    print(f"  TOTAL 2026: {t['r'].sum() * risk:+.2f} USD")

    os.makedirs("results", exist_ok=True)
    tag = f"_{a.tag}" if a.tag else ""
    t.to_csv(f"results/trades_{year}{tag}.csv", index=False)
    months.to_csv(f"results/months_{year}{tag}.csv", index=False)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(13, 9))
    colors = ["#00c853" if v > 0 else "#ff1744" for v in months["total_r"]]
    ax1.bar(months["month"], months["total_r"], color=colors)
    ax1.axhline(0, color="gray", linewidth=1)
    ax1.set_title(f"{year} monthly R — Asia Grab (GOLD, tuned config, cost {COST})")
    ax1.set_ylabel("R")
    for i, v in enumerate(months["total_r"]):
        ax1.text(i, v + (0.15 if v > 0 else -0.35), f"{v:+.1f}", ha="center", fontsize=9)
    ax2.plot(t["exit_time"], t["cum_r"], color="navy", linewidth=1.4)
    ax2.fill_between(t["exit_time"], t["cum_r"], alpha=0.15, color="navy")
    ax2.axhline(0, color="gray", linewidth=1)
    ax2.set_title("Cumulative R through the year")
    fig.tight_layout()
    out = f"results/year_{year}_analysis.png"
    fig.savefig(out, dpi=150)
    print(f"\nSaved results/trades_{year}.csv, results/months_{year}.csv, {out}")


if __name__ == "__main__":
    main()
