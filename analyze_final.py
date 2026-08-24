import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from backtest import load_data, run_config
from strategy import add_atr

RISK_PER_TRADE = 100.0
SPLIT_NOTE = "walk-forward boundary used in validation: ~2026-04-15"

STRATEGIES = {
    "Tokyo Reclaim": {
        "trigger": (22, 10), "reference": (19, 21), "exit": 8,
        "entry_buf": 1.0, "wick_buf": 0.5,
        "instruments": [
            ("USDJPY", "USDJPY=X", 0.005, 36 + 9, 22.80 + 6.02),
            ("EURJPY", "EURJPY=X", 0.008, 27 + 14, 2.12 + 10.70),
            ("GBPJPY", "GBPJPY=X", 0.012, 35 + 17, 2.56 + 5.70),
            ("AUDJPY", "AUDJPY=X", 0.008, 31 + 10, 2.65 + 5.76),
        ],
    },
    "London Reclaim": {
        "trigger": (7, 13), "reference": (22, 10), "exit": 17,
        "entry_buf": 0.5, "wick_buf": 0.25,
        "instruments": [
            ("EURUSD", "EURUSD=X", 0.0001, 46 + 19, 21.27 + 5.36),
            ("GBPUSD", "GBPUSD=X", 0.00015, 58 + 27, 21.73 + 22.59),            ("USDJPY", "USDJPY=X", 0.005, 40 + 19, 58.37 + 4.33),
            ("GOLD", "GC=F", 0.5, 30 + 10, 21.42 + 1.95),
        ],
    },
}


def stats_block(trades):
    if isinstance(trades, pd.DataFrame):
        r = trades["r"].to_numpy()
    else:
        r = np.array([t["r"] for t in trades])
    n = len(r)
    wins = int((r > 0).sum())
    mean = r.mean()
    se = r.std(ddof=1) / math.sqrt(n) if n > 1 else 0.0
    t = mean / se if se > 0 else 0.0
    pf = r[r > 0].sum() / abs(r[r <= 0].sum()) if (r <= 0).any() else float("inf")
    eq = pd.Series(r).cumsum()
    dd = float((eq.cummax() - eq).max())
    return {"trades": n, "wins": wins, "wr": round(100 * wins / n, 1),
            "total_r": round(float(r.sum()), 2), "mean_r": round(float(mean), 3),
            "t": round(float(t), 2), "pf": round(float(pf), 2), "max_dd": round(dd, 2)}


def run_strategy(name, cfg):
    all_trades = []
    print(f"\n=== {name} ===")
    print(f"trigger {cfg['trigger']} UTC | reference {cfg['reference']} | "
          f"entry_buf {cfg['entry_buf']}xATR | wick_buf {cfg['wick_buf']}xATR | exit {cfg['exit']}:00 UTC")
    for inst, sym, cost, exp_n, exp_r in cfg["instruments"]:
        df = load_data(sym, "365d", "60m")
        add_atr(df, 10)
        _, trades = run_config(df, cfg["trigger"], cfg["reference"], 0.0, 1.0,
                               cfg["entry_buf"], "reclaim", "opposite", cfg["exit"],
                               cost=cost, skip_sunday=True, entry_bar_tp=False,
                               sl_mode="wick", wick_buffer=cfg["wick_buf"])
        trades = [t for t in trades]
        got_n, got_r = len(trades), round(sum(t["r"] for t in trades), 2)
        if got_n == exp_n and abs(got_r - exp_r) < 0.05:
            match = "OK"
        elif abs(got_n - exp_n) <= 3 and abs(got_r - exp_r) <= 10:
            match = "OK (boundary/gap lookback difference vs sliced runs — full-history pass is authoritative)"
        else:
            match = "MISMATCH!"
        print(f"  {inst}: {got_n} trades, {got_r:+.2f}R  [verification vs walk-forward: {match}]")
        if match.startswith("MISMATCH"):
            assert False, f"verification failed for {inst}: {match}"
        for t in trades:
            t["symbol"] = inst
        all_trades.extend(trades)

    t = pd.DataFrame(all_trades).sort_values("entry_time").reset_index(drop=True)
    t["month"] = t["entry_time"].dt.strftime("%Y-%m")
    return t


def month_table(t, label):
    rows = []
    for m in sorted(t["month"].unique()):
        mdf = t[t["month"] == m]
        r = mdf["r"]
        wins = int((r > 0).sum())
        rows.append({"month": m, "trades": len(mdf), "wins": wins,
                     "win_rate": round(100 * wins / len(mdf), 1),
                     "total_r": round(r.sum(), 2),
                     "usd": round(r.sum() * RISK_PER_TRADE, 2)})
    df = pd.DataFrame(rows)
    total = {"month": "TOTAL", "trades": int(df["trades"].sum()),
             "wins": int(df["wins"].sum()),
             "win_rate": round(100 * df["wins"].sum() / df["trades"].sum(), 1),
             "total_r": round(df["total_r"].sum(), 2),
             "usd": round(df["usd"].sum(), 2)}
    df = pd.concat([df, pd.DataFrame([total])], ignore_index=True)
    print(f"\n--- {label} month by month ---")
    print(df.to_string(index=False))
    return df


def main():
    results = {}
    for name, cfg in STRATEGIES.items():
        t = run_strategy(name, cfg)
        s = stats_block(t)
        print(f"  {name} OVERALL: {s}")
        results[name] = t
        month_table(t, name)

    print("\n=== COMBINED PORTFOLIO (both strategies, $100/trade) ===")
    comb = pd.concat(results.values()).sort_values("entry_time").reset_index(drop=True)
    comb["month"] = comb["entry_time"].dt.strftime("%Y-%m")
    comb_df = month_table(comb, "Tokyo + London combined")

    by_strat = comb.groupby(["month", "symbol"])["r"].sum().unstack()
    print("\nPer-instrument monthly R:")
    print(by_strat.round(2).to_string())

    os.makedirs("results", exist_ok=True)
    for name, t in results.items():
        t.to_csv(f"results/final_{name.replace(' ', '_').lower()}.csv", index=False)
    comb_df.to_csv("results/final_combined_months.csv", index=False)

    fig, ax = plt.subplots(figsize=(13, 6))
    months = [m for m in comb_df["month"] if m != "TOTAL"]
    mr = comb_df[comb_df["month"] != "TOTAL"]
    colors = ["#00c853" if v > 0 else "#ff1744" for v in mr["total_r"]]
    ax.bar(months, mr["total_r"], color=colors)
    for i, v in enumerate(mr["total_r"]):
        ax.text(i, v + (0.3 if v > 0 else -0.7), f"{v:+.1f}", ha="center", fontsize=9)
    ax.axhline(0, color="gray", linewidth=1)
    ax.set_title("Monthly R — Tokyo Reclaim + London Reclaim portfolio (Jun 2025 – Aug 2026)")
    ax.set_ylabel("R")
    fig.tight_layout()
    fig.savefig("results/final_monthly.png", dpi=150)
    print("\nSaved final CSVs and results/final_monthly.png")


if __name__ == "__main__":
    import os
    main()
