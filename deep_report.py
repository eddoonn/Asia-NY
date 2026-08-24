import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

RISK = 100.0


def wr_ci(w, n):
    p = w / n
    se = math.sqrt(p * (1 - p) / n)
    return 100 * max(0.0, p - 1.96 * se), 100 * min(1.0, p + 1.96 * se)


def month_table(t, label):
    rows = []
    for m in sorted(t["month"].unique()):
        mdf = t[t["month"] == m]
        r = mdf["r"].to_numpy()
        n = len(r)
        w = int((r > 0).sum())
        lo, hi = wr_ci(w, n)
        mean = r.mean()
        se = r.std(ddof=1) / math.sqrt(n) if n > 1 else 0
        tt = mean / se if se > 0 else 0.0
        eq = pd.Series(r).cumsum()
        dd = float((eq.cummax() - eq).max())
        days = mdf.groupby(mdf["entry_time"].dt.date)["r"].sum()
        rows.append({"month": m, "tr": n, "win%": round(100 * w / n, 1),
                     "wr95ci": f"{lo:.0f}-{hi:.0f}", "R": round(r.sum(), 2),
                     "avgR": round(mean, 3), "t": round(tt, 2),
                     "PF": round(r[r > 0].sum() / abs(r[r <= 0].sum()), 2) if (r <= 0).any() else 99.0,
                     "DD": round(dd, 2),
                     "TP": int((mdf["reason"] == "tp").sum()), "SL": int((mdf["reason"] == "sl").sum()),
                     "time": int((mdf["reason"] == "time").sum()),
                     "bestD": round(days.max(), 2), "worstD": round(days.min(), 2),
                     "posD": f"{int((days > 0).sum())}/{len(days)}",
                     "usd": round(r.sum() * RISK, 0)})
    df = pd.DataFrame(rows)
    print(df.to_string(index=False))
    return df


def per_instrument(t):
    for name, g in t.groupby("symbol"):
        r = g["r"].to_numpy()
        n = len(r)
        w = int((r > 0).sum())
        mean = r.mean()
        se = r.std(ddof=1) / math.sqrt(n) if n > 1 else 0
        tt = mean / se if se > 0 else 0.0
        print(f"  {name}: {n:>3} trades | WR {100*w/n:>5.1f}% | {r.sum():>+8.2f}R | "
              f"mean {mean:>+.3f}R | t {tt:>+5.2f}")


def main():
    trades = pd.read_pickle("results/deep_trades_2026.pkl")
    for t in trades.values():
        for x in t:
            x["month"] = x["entry_time"].strftime("%Y-%m")

    frames = []
    for sname in ("Tokyo", "London"):
        t = pd.DataFrame(trades[sname])
        print(f"\n{'='*70}\n{sname.upper()} RECLAIM — 2026 ({len(t)} trades, verified)\n{'='*70}")
        overall_r = t["r"].to_numpy()
        w = int((overall_r > 0).sum())
        mean = overall_r.mean()
        se = overall_r.std(ddof=1) / math.sqrt(len(overall_r))
        print(f"OVERALL: {len(t)} trades | WR {100*w/len(t):.1f}% | {overall_r.sum():+.2f}R | "
              f"mean {mean:+.3f}R | t {mean/se:+.2f} | "
              f"PF {overall_r[overall_r>0].sum()/abs(overall_r[overall_r<=0].sum()):.2f}")
        print("\nMonth by month:")
        mt = month_table(t, sname)
        print("\nPer instrument:")
        per_instrument(t)
        t.to_csv(f"results/deep_{sname.lower()}_2026.csv", index=False)
        mt.to_csv(f"results/deep_{sname.lower()}_months_2026.csv", index=False)
        t["strategy"] = sname
        frames.append(t)

    comb = pd.concat(frames).sort_values("entry_time").reset_index(drop=True)
    print(f"\n{'='*70}\nCOMBINED PORTFOLIO — {len(comb)} trades\n{'='*70}")
    r = comb["r"].to_numpy()
    w = int((r > 0).sum())
    mean = r.mean()
    se = r.std(ddof=1) / math.sqrt(len(r))
    eq = pd.Series(r).cumsum()
    dd = float((eq.cummax() - eq).max())
    print(f"TOTAL: {len(comb)} trades | WR {100*w/len(comb):.1f}% | {r.sum():+.2f}R | "
          f"mean {mean:+.3f}R | t {mean/se:+.2f} | maxDD {dd:.2f}R | "
          f"{r.sum()*RISK:+,.0f} USD at {RISK:.0f}/trade")
    month_table(comb, "combined")

    hold = (comb["exit_time"] - comb["entry_time"]).dt.total_seconds() / 3600
    print(f"\nHold time: median {hold.median():.1f}h | mean {hold.mean():.1f}h | "
          f"max {hold.max():.1f}h")
    print(f"MAE: mean {comb['mae_r'].mean():.2f}R | MFE: mean {comb['mfe_r'].mean():.2f}R")
    wd = comb.groupby(comb["entry_time"].dt.day_name())["r"].agg(["count", "sum"])
    wd["sum"] = wd["sum"].round(2)
    print("\nWeekday R:")
    print(wd.to_string())

    fig, ax = plt.subplots(figsize=(13, 6))
    m = comb.groupby("month")["r"].sum()
    colors = ["#00c853" if v > 0 else "#ff1744" for v in m]
    ax.bar(m.index, m.values, color=colors)
    ax.axhline(0, color="gray", linewidth=1)
    ax.set_title("2026 verified monthly R — Tokyo + London Reclaim")
    plt.xticks(rotation=45)
    fig.tight_layout()
    fig.savefig("results/deep_monthly_2026.png", dpi=150)
    print("\nSaved deep_*.csv files and results/deep_monthly_2026.png")


if __name__ == "__main__":
    main()
