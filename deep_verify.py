import math
import os

import numpy as np
import pandas as pd

from backtest import load_data, run_config
from strategy import add_atr

RISK = 100.0
TOKYO = dict(trigger=(22, 10), reference=(19, 21), exit=8, ebuf=1.0, wb=0.5,
             instruments=[("USDJPY", "USDJPY=X", 0.005), ("EURJPY", "EURJPY=X", 0.008),
                          ("GBPJPY", "GBPJPY=X", 0.012), ("AUDJPY", "AUDJPY=X", 0.008)])
LONDON = dict(trigger=(7, 13), reference=(22, 10), exit=17, ebuf=0.5, wb=0.25,
              instruments=[("EURUSD", "EURUSD=X", 0.0001), ("GBPUSD", "GBPUSD=X", 0.00015),
                           ("USDJPY", "USDJPY=X", 0.005), ("GOLD", "GC=F", 0.5)])


def collect(cfg, tag):
    out = []
    for name, sym, cost in cfg["instruments"]:
        df = load_data(sym, "365d", "60m")
        add_atr(df, 10)
        s1, t1 = run_config(df, cfg["trigger"], cfg["reference"], 0.0, 1.0, cfg["ebuf"],
                            "reclaim", "opposite", cfg["exit"], cost=cost,
                            skip_sunday=True, entry_bar_tp=False, sl_mode="wick",
                            wick_buffer=cfg["wb"])
        s2, t2 = run_config(df, cfg["trigger"], cfg["reference"], 0.0, 1.0, cfg["ebuf"],
                            "reclaim", "opposite", cfg["exit"], cost=cost,
                            skip_sunday=True, entry_bar_tp=False, sl_mode="wick",
                            wick_buffer=cfg["wb"])
        k1 = [(t["entry_time"], t["side"], t["r"]) for t in t1]
        k2 = [(t["entry_time"], t["side"], t["r"]) for t in t2]
        assert k1 == k2, f"non-deterministic engine on {name}"
        for t in t1:
            t["symbol"] = name
        out.extend(t1)
    return out


def reference_for(tpos, t_date, df, cfg, is_tokyo):
    idx = df.index
    hours = np.asarray(idx.hour)
    dates = np.asarray(idx.date)
    rw = cfg["reference"]
    if is_tokyo:
        for off in range(0, 8):
            target = t_date - pd.Timedelta(days=off)
            target = target.date() if hasattr(target, "date") else target
            sel = (dates == target) & (hours >= rw[0]) & (hours < rw[1])
            if sel.sum() > 0:
                pos = np.where(sel)[0]
                if pos[-1] < tpos:
                    return (float(df["High"].values[pos].max()),
                            float(df["Low"].values[pos].min()),
                            float(df["Open"].values[pos[0]]),
                            float(df["Close"].values[pos[-1]]))
        return None
    else:
        prev = t_date - pd.Timedelta(days=1)
        prev = prev.date() if hasattr(prev, "date") else prev
        sel = ((dates == prev) & (hours >= rw[0])) | ((dates == t_date) & (hours < rw[1]))
    sel = sel.copy()
    sel[tpos:] = False
    if sel.sum() == 0:
        return None
    pos = np.where(sel)[0]
    return (float(df["High"].values[pos].max()), float(df["Low"].values[pos].min()),
            float(df["Open"].values[pos[0]]), float(df["Close"].values[pos[-1]]))


def rederive(trades, df, cfg, name):
    idx = df.index
    o, h, l, c = (df[k].values for k in ("Open", "High", "Low", "Close"))
    atr = df["atr"].values
    hours = np.asarray(idx.hour)
    s, e = cfg["trigger"]
    trig_ok = (hours >= s) & (hours < e) if s <= e else (hours >= s) | (hours < e)
    is_tokyo = cfg["reference"] == (19, 21)

    verified, mismatches = 0, []
    for t in trades:
        t_date = pd.Timestamp(t["date"]).date()
        epos_arr = idx.get_indexer([t["entry_time"]])
        epos = int(epos_arr[0])
        if epos < 1:
            mismatches.append((t, "entry at data edge"))
            continue
        tpos = epos - 1
        if not trig_ok[tpos]:
            mismatches.append((t, "trigger bar outside trigger window"))
            continue
        ref = reference_for(tpos, t_date, df, cfg, is_tokyo)
        if ref is None:
            mismatches.append((t, "no reference session"))
            continue
        rh, rl = ref[0], ref[1]
        a = atr[tpos]
        buf = cfg["ebuf"] * a
        if t["side"] == "short":
            touch = h[tpos] >= rh + buf
            reclaim = c[tpos] < rh
            sl = h[tpos] + cfg["wb"] * a
            tp = rl
            risk = sl - (rh + buf)
        else:
            touch = l[tpos] <= rl - buf
            reclaim = c[tpos] > rl
            sl = l[tpos] - cfg["wb"] * a
            tp = rh
            risk = (rl - buf) - sl
        if not (touch and reclaim):
            mismatches.append((t, f"conditions not met (touch={touch}, reclaim={reclaim})"))
            continue
        if abs(float(o[epos]) - t["entry"]) > 1e-6:
            mismatches.append((t, f"entry {t['entry']} != next open {o[epos]}"))
            continue
        if abs(sl - t["sl"]) > 1e-6 or abs(tp - t["tp"]) > 1e-6:
            mismatches.append((t, f"SL/TP derived ({sl:.5f},{tp:.5f}) vs ({t['sl']:.5f},{t['tp']:.5f})"))
            continue
        exit_r = None
        cost = dict(cfg["instruments"]).get(name, 0.0) if isinstance(cfg["instruments"][0], dict) else 0.0
        for inst in cfg["instruments"]:
            if inst[0] == name:
                cost = inst[2]
        for p in range(epos, len(idx)):
            first = (p == epos)
            if t["side"] == "short":
                hit_sl = h[p] >= sl
                hit_tp = (l[p] <= tp) and not first
            else:
                hit_sl = l[p] <= sl
                hit_tp = (h[p] >= tp) and not first
            if hit_sl:
                exit_r = (-risk - cost) / risk
                break
            if hit_tp:
                pnl = (t["entry"] - tp) if t["side"] == "short" else (tp - t["entry"])
                exit_r = (pnl - cost) / risk
                break
            if hours[p] == cfg["exit"] and idx[p] > t["entry_time"]:
                pnl = (t["entry"] - c[p]) if t["side"] == "short" else (c[p] - t["entry"])
                exit_r = (pnl - cost) / risk
                break
        if exit_r is None:
            p = len(idx) - 1
            pnl = (t["entry"] - c[p]) if t["side"] == "short" else (c[p] - t["entry"])
            exit_r = (pnl - cost) / risk
        if abs(exit_r - t["r"]) > 0.02:
            mismatches.append((t, f"R derived {exit_r:.3f} vs recorded {t['r']}"))
            continue
        verified += 1
    return verified, mismatches


def main():
    print("=== STEP 1: 2026 DATA AUDIT ===")
    datasets = {}
    for _, sym, _c in TOKYO["instruments"] + LONDON["instruments"]:
        if sym in datasets:
            continue
        df = load_data(sym, "365d", "60m")
        add_atr(df, 10)
        d26 = df[df.index.year == 2026]
        bad = int((d26["High"] < d26["Low"]).sum())
        flat = int((d26["High"] == d26["Low"]).sum())
        dup = int(len(d26) - len(d26.index.unique()))
        print(f"{sym}: {len(d26)} bars | High<Low {bad} | High==Low {flat} | dupes {dup}")
        assert bad == 0 and dup == 0
        datasets[sym] = df

    print("\n=== STEP 2: REPRODUCIBILITY (double run) ===")
    all_trades = {}
    for sname, cfg in (("Tokyo", TOKYO), ("London", LONDON)):
        ta = collect(cfg, "a")
        tb = collect(cfg, "b")
        ka = [(x["entry_time"], x["symbol"], x["side"], x["r"]) for x in ta]
        kb = [(x["entry_time"], x["symbol"], x["side"], x["r"]) for x in tb]
        assert ka == kb, f"non-deterministic {sname}"
        t26 = [x for x in ta if x["entry_time"].year == 2026]
        all_trades[sname] = t26
        print(f"{sname}: {len(ta)} trades total, {len(t26)} in 2026 — reproducible PASS")

    print("\n=== STEP 3: INDEPENDENT RE-DERIVATION OF EVERY 2026 TRADE ===")
    ok = True
    for sname, cfg in (("Tokyo", TOKYO), ("London", LONDON)):
        tv = tm = 0
        for inst in cfg["instruments"]:
            name = inst[0]
            trades = [x for x in all_trades[sname] if x["symbol"] == name]
            v, mism = rederive(trades, datasets[inst[1]], cfg, name)
            tv += v
            tm += len(mism)
            for t, why in mism:
                print(f"  MISMATCH {name} {t['date']} {t['side']}: {why}")
        status = "PASS" if tm == 0 else f"FAIL ({tm} mismatches)"
        print(f"{sname}: {tv}/{tv + tm} trades independently re-derived — {status}")
        ok = ok and tm == 0

    if ok:
        pd.to_pickle(all_trades, "results/deep_trades_2026.pkl")
        print("\nAll checks passed. State saved to results/deep_trades_2026.pkl")
    else:
        raise SystemExit("VERIFICATION FAILED — do not trust the numbers")


if __name__ == "__main__":
    main()
