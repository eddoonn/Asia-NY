import argparse
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from backtest import load_data


def stats_block(m: pd.DataFrame) -> dict:
    r = m["r"]
    wins, losses = r[r > 0], r[r <= 0]
    gw, gl = wins.sum(), abs(losses.sum())
    eq = r.cumsum()
    dd = (eq.cummax() - eq).max()
    hold_h = (m["exit_time"] - m["entry_time"]).dt.total_seconds() / 3600
    return {
        "trades": len(m),
        "wins": int((r > 0).sum()),
        "losses": int((r <= 0).sum()),
        "win_rate_pct": round(100 * (r > 0).mean(), 1),
        "total_r": round(r.sum(), 2),
        "avg_r": round(r.mean(), 3),
        "profit_factor": round(gw / gl, 2) if gl > 0 else float("inf"),
        "avg_win_r": round(wins.mean(), 3) if len(wins) else 0,
        "avg_loss_r": round(losses.mean(), 3) if len(losses) else 0,
        "best_r": round(r.max(), 2),
        "worst_r": round(r.min(), 2),
        "max_dd_r": round(float(dd), 2),
        "avg_hold_h": round(hold_h.mean(), 1),
        "avg_mae_r": round(m["mae_r"].mean(), 2),
        "avg_mfe_r": round(m["mfe_r"].mean(), 2),
    }


def breakdown(m: pd.DataFrame, key: str) -> pd.DataFrame:
    g = m.groupby(key)["r"]
    out = pd.DataFrame({
        "trades": g.count(),
        "wins": g.apply(lambda x: (x > 0).sum()),
        "win_rate_pct": (g.apply(lambda x: (x > 0).mean()) * 100).round(1),
        "total_r": g.sum().round(2),
        "avg_r": g.mean().round(3),
    })
    return out.sort_values("total_r", ascending=False)


def main():
    p = argparse.ArgumentParser(description="Detailed monthly trade analysis")
    p.add_argument("--trades", default="results/trades.csv")
    p.add_argument("--month", default="2026-08")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--account", type=float, default=10000.0)
    p.add_argument("--risk-pct", type=float, default=1.0)
    args = p.parse_args()

    t = pd.read_csv(args.trades, parse_dates=["entry_time", "exit_time"])
    m = t[t["entry_time"].dt.strftime("%Y-%m") == args.month].copy().reset_index(drop=True)
    if m.empty:
        raise SystemExit(f"No trades found for {args.month}")

    m["weekday"] = m["entry_time"].dt.day_name()
    m["week"] = "W" + (m["entry_time"].dt.isocalendar().week.astype(int)
                       - m["entry_time"].dt.isocalendar().week.iloc[0] + 1).astype(str)
    m["hold_h"] = ((m["exit_time"] - m["entry_time"]).dt.total_seconds() / 3600).round(1)
    m["cum_r"] = m["r"].cumsum().round(2)
    m["grab"] = np.where(m["side"] == "short", "NY high swept", "NY low swept")

    s = stats_block(m)
    risk_dollars = args.account * args.risk_pct / 100.0

    pd.set_option("display.width", 250)
    print(f"=== {args.month} performance ===")
    for k, v in s.items():
        print(f"{k}: {v}")
    print(f"net_usd: {s['total_r'] * risk_dollars:+.2f} "
          f"(risk {risk_dollars:.0f}/trade on {args.account:.0f} account)")

    print("\n=== Trade-by-trade ===")
    cols = ["date", "weekday", "grab", "entry_time", "entry", "sl", "tp",
            "exit_time", "exit", "hold_h", "reason", "mae_r", "mfe_r", "r", "cum_r"]
    print(m[cols].to_string(index=False))

    print("\n=== By exit reason ===")
    print(breakdown(m, "reason").to_string())
    print("\n=== By side ===")
    print(breakdown(m, "side").to_string())
    print("\n=== By weekday ===")
    print(breakdown(m, "weekday").to_string())
    print("\n=== By week of month ===")
    print(breakdown(m, "week").to_string())

    winners, losers = m[m["r"] > 0], m[m["r"] <= 0]
    print("\n=== Winner quality ===")
    if len(winners):
        print(f"winners avg MAE {winners['mae_r'].mean():.2f}R | avg MFE {winners['mfe_r'].mean():.2f}R | "
              f"avg hold {winners['hold_h'].mean():.1f}h")
    if len(losers):
        print(f"losers  avg MAE {losers['mae_r'].mean():.2f}R | avg MFE {losers['mfe_r'].mean():.2f}R | "
              f"avg hold {losers['hold_h'].mean():.1f}h")

    os.makedirs("results", exist_ok=True)
    df = load_data(args.symbol, "40d", "60m")
    px = df[(df.index >= m["entry_time"].min().floor("D"))
            & (df.index <= m["exit_time"].max().ceil("D"))]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), sharex=False,
                                   gridspec_kw={"height_ratios": [3, 1]})
    ax1.plot(px.index, px["Close"], color="gray", linewidth=1, label="GC=F close")
    for _, tr in m.iterrows():
        c = "green" if tr["r"] > 0 else "red"
        marker = "^" if tr["side"] == "long" else "v"
        ax1.scatter(tr["entry_time"], tr["entry"], color=c, marker=marker, s=70, zorder=5)
        ax1.scatter(tr["exit_time"], tr["exit"], color=c, marker="x", s=60, zorder=5)
        ax1.annotate("", xy=(tr["exit_time"], tr["exit"]), xytext=(tr["entry_time"], tr["entry"]),
                     arrowprops=dict(arrowstyle="->", color=c, alpha=0.45, linewidth=1))
    ax1.set_title(f"{args.symbol} {args.month} — entries (triangles), exits (x), green=win red=loss")
    ax1.legend(loc="upper left")
    ax2.plot(m["exit_time"], m["cum_r"], marker="o", color="navy")
    ax2.axhline(0, color="gray", linewidth=1)
    ax2.set_title("Cumulative R")
    ax2.set_ylabel("R")
    fig.tight_layout()
    out = f"results/analysis_{args.month}.png"
    fig.savefig(out, dpi=150)
    m.to_csv(f"results/analysis_{args.month}.csv", index=False)
    print(f"\nSaved {out} and results/analysis_{args.month}.csv")


if __name__ == "__main__":
    main()
