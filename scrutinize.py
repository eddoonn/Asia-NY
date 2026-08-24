import math

import numpy as np
import pandas as pd

from backtest import load_data, run_config
from deep_verify import LONDON, TOKYO, collect
from strategy import add_atr, atr_values


def totals(trades):
    r = np.array([t["r"] for t in trades])
    return len(r), float(r.sum()), float(r.mean())


def stress_costs(data):
    print("\n--- A. COST STRESS (x1 / x2 / x3 modeled costs) ---")
    for mult in (1, 2, 3):
        line = []
        for sname, cfg in (("Tokyo", TOKYO), ("London", LONDON)):
            tr = []
            for name, sym, cost in cfg["instruments"]:
                df = data[sym]["df"]
                a = data[sym]["atrs"][10]
                s, trades = run_config(df, cfg["trigger"], cfg["reference"], 0.0, 1.0,
                                       cfg["ebuf"], "reclaim", "opposite", cfg["exit"],
                                       cost=cost * mult, skip_sunday=True,
                                       entry_bar_tp=False, sl_mode="wick",
                                       wick_buffer=cfg["wb"])
                tr.extend(trades)
            n, tot, mean = totals(tr)
            line.append(f"{sname}: {n} trades {tot:+.1f}R (avg {mean:+.3f})")
        print(f"  cost x{mult}: " + " | ".join(line))


def stress_slippage():
    print("\n--- B. STOP-LOSS SLIPPAGE STRESS (extra 0.1R / 0.2R on every SL exit) ---")
    trades = pd.read_pickle("results/deep_trades_2026.pkl")
    for extra in (0.0, 0.1, 0.2):
        adj = []
        for sname in trades:
            for t in trades[sname]:
                r = t["r"] - (extra if t["reason"] == "sl" else 0.0)
                adj.append(r)
        r = np.array(adj)
        print(f"  slippage {extra:.1f}R: total {r.sum():+.1f}R | mean {r.mean():+.3f}R | "
              f"WR {100*(r>0).mean():.1f}%")


def neighborhood(data):
    print("\n--- C. PARAMETER NEIGHBORHOOD (edge must degrade gracefully, not collapse) ---")
    grids = [
        ("Tokyo base", TOKYO, dict(ebuf=1.0, wb=0.5, exit=8)),
        ("Tokyo ebuf 0.9", TOKYO, dict(ebuf=0.9, wb=0.5, exit=8)),
        ("Tokyo ebuf 1.1", TOKYO, dict(ebuf=1.1, wb=0.5, exit=8)),
        ("Tokyo wick 0.4", TOKYO, dict(ebuf=1.0, wb=0.4, exit=8)),
        ("Tokyo wick 0.6", TOKYO, dict(ebuf=1.0, wb=0.6, exit=8)),
        ("Tokyo exit 7", TOKYO, dict(ebuf=1.0, wb=0.5, exit=7)),
        ("Tokyo exit 9", TOKYO, dict(ebuf=1.0, wb=0.5, exit=9)),
        ("London base", LONDON, dict(ebuf=0.5, wb=0.25, exit=17)),
        ("London ebuf 0.4", LONDON, dict(ebuf=0.4, wb=0.25, exit=17)),
        ("London ebuf 0.6", LONDON, dict(ebuf=0.6, wb=0.25, exit=17)),
        ("London wick 0.15", LONDON, dict(ebuf=0.5, wb=0.15, exit=17)),
        ("London wick 0.35", LONDON, dict(ebuf=0.5, wb=0.35, exit=17)),
        ("London exit 16", LONDON, dict(ebuf=0.5, wb=0.25, exit=16)),
    ]
    for label, cfg, params in grids:
        tr = []
        for name, sym, cost in cfg["instruments"]:
            df = data[sym]["df"]
            s, trades = run_config(df, cfg["trigger"], cfg["reference"], 0.0, 1.0,
                                   params["ebuf"], "reclaim", "opposite", params["exit"],
                                   atr=data[sym]["atrs"][10], cost=cost, skip_sunday=True,
                                   entry_bar_tp=False, sl_mode="wick",
                                   wick_buffer=params["wb"])
            tr.extend([t for t in trades if t["entry_time"].year == 2026])
        n, tot, mean = totals(tr)
        print(f"  {label:18s}: {n:>3} trades 2026, {tot:+8.2f}R, avg {mean:+.3f}R")


def bootstrap():
    print("\n--- D. BOOTSTRAP MONTE CARLO (10,000 resamples of the 207 trades) ---")
    trades = pd.read_pickle("results/deep_trades_2026.pkl")
    r = np.array([t["r"] for s in trades for t in trades[s]])
    rng = np.random.default_rng(42)
    sims = np.array([rng.choice(r, size=len(r), replace=True).sum() for _ in range(10000)])
    p5, p50, p95 = np.percentile(sims, [5, 50, 95])
    print(f"  observed: {r.sum():+.1f}R | bootstrap 5th {p5:+.1f}R | median {p50:+.1f}R | "
          f"95th {p95:+.1f}R | P(loss) = {(sims < 0).mean()*100:.2f}%")


def entry_gap_audit(data):
    print("\n--- E. ENTRY GAP AUDIT (fill price vs prior close — data holes would show here) ---")
    trades = pd.read_pickle("results/deep_trades_2026.pkl")
    sym_map = {"USDJPY": "USDJPY=X", "EURJPY": "EURJPY=X", "GBPJPY": "GBPJPY=X",
               "AUDJPY": "AUDJPY=X", "EURUSD": "EURUSD=X", "GBPUSD": "GBPUSD=X",
               "GOLD": "GC=F"}
    gaps = []
    for sname in trades:
        for t in trades[sname]:
            sym = sym_map[t["symbol"]]
            df = data[sym]["df"]
            pos = df.index.get_indexer([t["entry_time"]])[0]
            if pos < 1:
                continue
            gap = abs(float(df["Open"].iloc[pos]) - float(df["Close"].iloc[pos - 1]))
            atr = float(df["atr"].iloc[pos - 1])
            gaps.append(gap / atr if atr > 0 else 0)
    g = np.array(gaps)
    print(f"  {len(g)} entries | gap/ATR: median {np.median(g):.3f} | p90 {np.percentile(g,90):.3f} | "
          f"max {g.max():.3f} | entries with gap > 0.5 ATR: {int((g > 0.5).sum())}")


def subperiod():
    print("\n--- G. SUB-PERIOD STABILITY (Jan-Apr vs May-Aug 2026) ---")
    trades = pd.read_pickle("results/deep_trades_2026.pkl")
    for sname in trades:
        for label, lo, hi in (("H1", 1, 4), ("H2", 5, 8)):
            r = np.array([t["r"] for t in trades[sname]
                          if lo <= t["entry_time"].month <= hi])
            print(f"  {sname} {label}: {len(r)} trades {r.sum():+.1f}R (avg {r.mean():+.3f})")


def leave_one_out():
    print("\n--- H. LEAVE-ONE-OUT PORTFOLIO (2026) ---")
    trades = pd.read_pickle("results/deep_trades_2026.pkl")
    all_r = [(t["symbol"], t["r"]) for s in trades for t in trades[s]]
    syms = sorted(set(s for s, _ in all_r))
    base = sum(r for _, r in all_r)
    print(f"  full portfolio: {base:+.1f}R")
    for s in syms:
        rest = sum(r for sym, r in all_r if sym != s)
        print(f"  without {s}: {rest:+.1f}R ({len([1 for sym,_ in all_r if sym==s])} trades removed)")


def main():
    data = {}
    for _, sym, _c in TOKYO["instruments"] + LONDON["instruments"]:
        if sym in data:
            continue
        df = load_data(sym, "365d", "60m")
        add_atr(df, 10)
        data[sym] = {"df": df, "atrs": {10: atr_values(df, 10)}}

    stress_costs(data)
    stress_slippage()
    neighborhood(data)
    bootstrap()
    entry_gap_audit(data)
    subperiod()
    leave_one_out()


if __name__ == "__main__":
    main()
