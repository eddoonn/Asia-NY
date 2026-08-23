import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

from strategy import add_atr, label_sessions, ny_reference_levels, find_trades, summarize


def load_data(symbol: str, period: str, interval: str) -> pd.DataFrame:
    df = yf.download(symbol, period=period, interval=interval, progress=False, auto_adjust=False)
    if df.empty:
        raise SystemExit(f"No data returned for {symbol}")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df[["Open", "High", "Low", "Close"]].copy()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")
    return df


def main():
    p = argparse.ArgumentParser(description="Asia-session gold liquidity-grab reversal backtest")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--period", default="60d")
    p.add_argument("--interval", default="60m")
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--atr-mult", type=float, default=1.0)
    p.add_argument("--atr-len", type=int, default=14)
    p.add_argument("--asia-start", type=int, default=0)
    p.add_argument("--asia-end", type=int, default=9)
    p.add_argument("--ny-late-start", type=int, default=18)
    p.add_argument("--ny-late-end", type=int, default=22)
    p.add_argument("--exit-hour", type=int, default=9)
    args = p.parse_args()

    df = load_data(args.symbol, args.period, args.interval)
    df = add_atr(df, args.atr_len)
    asia = (args.asia_start, args.asia_end)
    ny_late = (args.ny_late_start, args.ny_late_end)
    df = label_sessions(df, asia, ny_late)
    levels = ny_reference_levels(df)
    trades = find_trades(df, levels, args.rr, args.atr_mult, args.exit_hour)

    stats = summarize(trades)
    print(f"\nSymbol={args.symbol} period={args.period} interval={args.interval}")
    print(f"Asia={asia} NY-late={ny_late} RR={args.rr} SL={args.atr_mult}*ATR{args.atr_len}")
    for k, v in stats.items():
        print(f"{k}: {v}")

    os.makedirs("results", exist_ok=True)
    if trades:
        tdf = pd.DataFrame(trades)
        tdf.to_csv("results/trades.csv", index=False)
        equity = np.cumsum([t["r"] for t in trades])
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.plot(range(1, len(equity) + 1), equity, marker="o")
        ax.axhline(0, color="gray", linewidth=1)
        ax.set_title("Equity curve (R multiples)")
        ax.set_xlabel("Trade #")
        ax.set_ylabel("Cumulative R")
        fig.tight_layout()
        fig.savefig("results/equity_curve.png", dpi=150)
        print("\nSaved results/trades.csv and results/equity_curve.png")
    else:
        print("\nNo trades generated.")


if __name__ == "__main__":
    main()
