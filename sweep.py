import argparse
import itertools

import numpy as np
import pandas as pd

from backtest import load_data, run_config
from strategy import atr_values


def segment_r(trades, n_segments):
    if not trades:
        return []
    times = np.array([t["entry_time"].value for t in trades])
    rs = np.array([t["r"] for t in trades])
    order = np.argsort(times)
    times, rs = times[order], rs[order]
    edges = np.quantile(times, np.linspace(0, 1, n_segments + 1))
    out = []
    for i in range(n_segments):
        lo, hi = edges[i], edges[i + 1]
        if i == n_segments - 1:
            hi += 1
        mask = (times >= lo) & (times < hi)
        out.append(float(rs[mask].sum()))
    return out


def sweep(df, atrs, args):
    asia_windows = [(22, 9), (22, 10), (21, 9), (0, 9)]
    ny_windows = [(19, 22), (20, 22), (19, 21)]
    buffers = [0.25, 0.5, 0.75, 1.0]
    atr_mults = [1.0, 1.5, 2.0]
    exit_hours = [8, 9, 12]
    tp_options = [0.4, 0.5, 0.75, 1.0, "opposite"]

    combos = list(itertools.product(asia_windows, ny_windows, buffers, atr_mults,
                                    exit_hours, tp_options, atrs.keys()))
    rows = []
    for i, (asia, ny_late, buf, atr_mult, exit_hour, tp, atr_len) in enumerate(combos):
        tp_mode = "opposite" if tp == "opposite" else "rr"
        rr = None if tp == "opposite" else tp
        stats, trades = run_config(df, asia, ny_late, rr, atr_mult, buf, "stop",
                                   tp_mode, exit_hour, atr=atrs[atr_len], cost=args.cost,
                                   skip_sunday=args.skip_sunday)
        if stats.get("trades", 0) < args.min_trades:
            continue
        segs = segment_r(trades, args.segments)
        rows.append({
            "asia": f"{asia[0]:02d}-{asia[1]:02d}",
            "ny_late": f"{ny_late[0]:02d}-{ny_late[1]:02d}",
            "buf": buf,
            "atr_mult": atr_mult,
            "atr_len": atr_len,
            "exit_hour": exit_hour,
            "tp": tp if tp == "opposite" else f"{tp}R",
            "seg_r": " / ".join(f"{s:+.1f}" for s in segs),
            "min_seg_r": round(min(segs), 2),
            **stats,
        })
        if (i + 1) % 1000 == 0:
            print(f"  {i + 1}/{len(combos)} done, {len(rows)} qualifying")
    return pd.DataFrame(rows)


def parse_row(row):
    asia = tuple(int(x) for x in row["asia"].split("-"))
    ny_late = tuple(int(x) for x in row["ny_late"].split("-"))
    if row["tp"] == "opposite":
        tp_mode, rr = "opposite", None
    else:
        tp_mode, rr = "rr", float(row["tp"].rstrip("R"))
    return asia, ny_late, rr


def main():
    p = argparse.ArgumentParser(description="Walk-forward parameter sweep for Asia-session gold reversal")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--period", default="365d")
    p.add_argument("--interval", default="60m")
    p.add_argument("--min-trades", type=int, default=40)
    p.add_argument("--segments", type=int, default=3)
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--cost", type=float, default=0.0)
    p.add_argument("--oos-split", type=float, default=0.7,
                   help="fraction of data used for training; rest is untouched test set (0 disables)")
    p.add_argument("--validate", type=int, default=20, help="top-N train configs to evaluate out-of-sample")
    p.add_argument("--skip-sunday", action="store_true")
    args = p.parse_args()

    df = load_data(args.symbol, args.period, args.interval)

    if args.oos_split <= 0 or args.oos_split >= 1:
        atrs = {n: atr_values(df, n) for n in (10, 14, 20)}
        res = sweep(df, atrs, args)
        if res.empty:
            print("No configuration met the filters.")
            return
        robust = res[res["min_seg_r"] > 0].sort_values("total_r", ascending=False)
        pd.set_option("display.width", 250)
        print(f"\n=== Top {args.top} robust configs (profitable in ALL {args.segments} segments) ===")
        print(robust.head(args.top).to_string(index=False))
        res.to_csv("results/sweep_robust.csv", index=False)
        print(f"\n{len(res)} configs saved to results/sweep_robust.csv ({len(robust)} all-segment profitable)")
        return

    split = int(len(df) * args.oos_split)
    train_df = df.iloc[:split]
    test_df = df.iloc[split:]
    print(f"Train: {len(train_df)} bars ({train_df.index[0].date()} .. {train_df.index[-1].date()})")
    print(f"Test:  {len(test_df)} bars ({test_df.index[0].date()} .. {test_df.index[-1].date()})")

    atrs_train = {n: atr_values(train_df, n) for n in (10, 14, 20)}
    print(f"\nSweeping on TRAIN set ({args.segments}-segment consistency filter)...")
    res = sweep(train_df, atrs_train, args)
    if res.empty:
        print("No configuration met the filters on train.")
        return
    robust = res[res["min_seg_r"] > 0].sort_values("total_r", ascending=False).head(args.validate)
    if robust.empty:
        print("No all-segment-profitable configs on train.")
        return

    atrs_test = {n: atr_values(test_df, n) for n in (10, 14, 20)}
    print(f"\nValidating top {len(robust)} train configs on UNTOUCHED test set...")
    val_rows = []
    for _, row in robust.iterrows():
        asia, ny_late, rr = parse_row(row)
        tp_mode = "opposite" if rr is None else "rr"
        stats, _ = run_config(test_df, asia, ny_late, rr, float(row["atr_mult"]),
                              float(row["buf"]), "stop", tp_mode, int(row["exit_hour"]),
                              atr=atrs_test[int(row["atr_len"])], cost=args.cost)
        val_rows.append({
            "asia": row["asia"], "ny_late": row["ny_late"], "buf": row["buf"],
            "atr_mult": row["atr_mult"], "atr_len": row["atr_len"],
            "exit_hour": row["exit_hour"], "tp": row["tp"],
            "train_r": row["total_r"], "train_wr": row["win_rate_pct"],
            "train_trades": row["trades"],
            "test_r": stats.get("total_r", 0), "test_wr": stats.get("win_rate_pct", 0),
            "test_pf": stats.get("profit_factor", 0), "test_trades": stats.get("trades", 0),
            "test_dd": stats.get("max_drawdown_r", 0),
        })

    val = pd.DataFrame(val_rows).sort_values("test_r", ascending=False)
    pd.set_option("display.width", 250)
    print("\n=== Out-of-sample validation (sorted by test R) ===")
    print(val.to_string(index=False))

    pos = (val["test_r"] > 0).sum()
    print(f"\nTest-set summary: {pos}/{len(val)} configs profitable out-of-sample | "
          f"median test R: {val['test_r'].median():+.2f} | mean test R: {val['test_r'].mean():+.2f}")
    print(f"Top-by-train config test result: {val.iloc[0]['test_r']:+.2f}R "
          f"({val.iloc[0]['test_wr']}% WR, PF {val.iloc[0]['test_pf']})")

    val.to_csv("results/oos_validation.csv", index=False)
    print("Saved results/oos_validation.csv")


if __name__ == "__main__":
    main()
