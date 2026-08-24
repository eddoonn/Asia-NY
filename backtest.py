import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

from strategy import add_atr, session_mask, asia_day_ids, ny_levels, find_trades, summarize, atr_values


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


def run_config(df, asia, ny_late, rr, atr_mult, entry_buffer, entry_mode, tp_mode, exit_hour, atr=None, cost=0.0, skip_sunday=False, entry_bar_tp=True, sl_mode="atr", wick_buffer=0.5, trend_filter=False):
    index = df.index
    high = df["High"].values
    low = df["Low"].values
    asia_mask, asia_day_id = asia_day_ids(index, asia)
    levels = ny_levels(index, high, low, ny_late,
                       opens=df["Open"].values, closes=df["Close"].values)
    trades = find_trades(df, asia_mask, asia_day_id, levels, rr, atr_mult,
                         entry_buffer, entry_mode, tp_mode, exit_hour, atr=atr, cost=cost,
                         skip_sunday=skip_sunday, entry_bar_tp=entry_bar_tp,
                         sl_mode=sl_mode, wick_buffer=wick_buffer, trend_filter=trend_filter,
                         ref_window=ny_late)
    return summarize(trades), trades


def main():
    p = argparse.ArgumentParser(description="Asia-session gold liquidity-grab reversal backtest")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--period", default="60d")
    p.add_argument("--interval", default="60m")
    p.add_argument("--rr", type=float, default=2.0)
    p.add_argument("--atr-mult", type=float, default=1.0)
    p.add_argument("--atr-len", type=int, default=14)
    p.add_argument("--asia", default="0-9")
    p.add_argument("--ny-late", default="18-22")
    p.add_argument("--entry-mode", choices=["stop", "stop-next", "close"], default="stop")
    p.add_argument("--entry-buffer", type=float, default=0.0)
    p.add_argument("--tp-mode", choices=["rr", "opposite"], default="rr")
    p.add_argument("--exit-hour", type=int, default=9)
    p.add_argument("--cost", type=float, default=0.0,
                   help="round-trip spread+slippage+fees in price units (e.g. 0.3 for GC)")
    p.add_argument("--account", type=float, default=10000.0)
    p.add_argument("--risk-pct", type=float, default=1.0)
    p.add_argument("--skip-sunday", action="store_true")
    args = p.parse_args()

    asia = tuple(int(x) for x in args.asia.split("-"))
    ny_late = tuple(int(x) for x in args.ny_late.split("-"))

    df = load_data(args.symbol, args.period, args.interval)
    df = add_atr(df, args.atr_len)

    stats, trades = run_config(df, asia, ny_late, args.rr, args.atr_mult,
                               args.entry_buffer, args.entry_mode, args.tp_mode, args.exit_hour,
                               cost=args.cost, skip_sunday=args.skip_sunday)

    print(f"\nSymbol={args.symbol} period={args.period} interval={args.interval}")
    print(f"Asia={asia} NY-late={ny_late} entry={args.entry_mode} buf={args.entry_buffer} "
          f"TP={args.tp_mode} RR={args.rr} SL={args.atr_mult}*ATR{args.atr_len} exitH={args.exit_hour} "
          f"cost={args.cost}")
    for k, v in stats.items():
        print(f"{k}: {v}")

    if trades:
        risk_dollars = args.account * args.risk_pct / 100.0
        total_dollars = stats["total_r"] * risk_dollars
        print(f"\nSizing: risk {args.risk_pct}% of {args.account:.0f} = {risk_dollars:.2f} per trade")
        print(f"Net PnL: {total_dollars:+.2f} USD -> equity {args.account + total_dollars:.2f}")

        tdf = pd.DataFrame(trades)
        monthly = tdf.groupby(tdf["entry_time"].dt.strftime("%Y-%m"))["r"].agg(["count", "sum"])
        monthly["sum"] = monthly["sum"].round(2)
        print("\nMonthly R:")
        print(monthly.to_string())

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
