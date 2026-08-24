import json
import os
from datetime import datetime, timezone

import pandas as pd

from backtest import load_data
from config import load_env
from notify import load_webhook, send
from strategy import add_atr, day_ids, ny_levels

PROFILES = [
    {"name": "tokyo", "trigger": (22, 10), "reference": (19, 21), "exit_hour": 8,
     "pairs": [("USDJPY", "USDJPY=X"), ("EURJPY", "EURJPY=X"),
               ("GBPJPY", "GBPJPY=X"), ("AUDJPY", "AUDJPY=X")]},
    {"name": "london", "trigger": (7, 13), "reference": (22, 10), "exit_hour": 17,
     "pairs": [("EURUSD", "EURUSD=X"), ("GBPUSD", "GBPUSD=X"),
               ("USDJPY", "USDJPY=X"), ("GOLD", "GC=F")]},
]
ENTRY_BUFFER = 1.0
WICK_BUFFER = 0.25
STATE = "results/reclaim_session.json"
BLUE, GRAY = 0x3498DB, 0x95A5A6


def in_window(now, trigger):
    h = now.hour
    s, e = trigger
    if s <= e:
        return s <= h < e
    return h >= s or h < e


def session_key(now, trigger):
    s, _ = trigger
    if now.hour >= s:
        return str(now.date())
    return str((now - pd.Timedelta(days=1)).date())


def last_closed_bar(df, now):
    if df.index[-1].hour == now.hour and now - df.index[-1] < pd.Timedelta(minutes=59):
        return df.iloc[-2], df.index[-2]
    return df.iloc[-1], df.index[-1]


def check_signal(client, profile, name, sym, risk, now, state, dry):
    skey = f"{profile['name']}:{session_key(now, profile['trigger'])}"
    if any(o.get("skey") == skey and o["symbol"] == name for o in state["orders"]):
        return None
    df = load_data(sym, "10d", "60m")
    add_atr(df, 10)
    idx = df.index
    bar, bar_ts = last_closed_bar(df, now)
    if not in_window(bar_ts.to_pydatetime().replace(tzinfo=timezone.utc), profile["trigger"]):
        return None

    levels = ny_levels(idx, df["High"].values, df["Low"].values, profile["reference"],
                       opens=df["Open"].values, closes=df["Close"].values)
    cur_id = int(day_ids(idx)[-1])
    ref = None
    for p in range(cur_id, cur_id - 8, -1):
        if p in levels and levels[p][2] < int(day_ids(idx).get_indexer([bar_ts])[0]):
            ref = levels[p]
            break
    if ref is None:
        return None
    rh, rl = ref[0], ref[1]

    bar_o, bar_h, bar_l, bar_c = (float(bar[k]) for k in ("Open", "High", "Low", "Close"))
    atr = float(df["atr"].iloc[-1])
    buf = ENTRY_BUFFER * atr
    prev_day_bull = ref[4] > ref[3]

    side = sl = tp = None
    if bar_h >= rh + buf and bar_c < rh and not prev_day_bull:
        side = "sellShort"
        sl = bar_h + WICK_BUFFER * atr
        tp = rl
    elif bar_l <= rl - buf and bar_c > rl and prev_day_bull:
        side = "buy"
        sl = bar_l - WICK_BUFFER * atr
        tp = rh
    if side is None:
        return None

    risk_dist = abs(sl - bar_c)
    jpy = "JPY" in name
    units = round(risk * bar_c / risk_dist, 2) if jpy else round(risk / risk_dist, 4)
    print(f"[{profile['name']}] {name}: {side} on {bar_ts} — entry ~{bar_c:.4f} SL {sl:.4f} TP {tp:.4f}")
    if dry:
        return {"symbol": name, "skey": skey, "profile": profile["name"], "dry": True,
                "side": side, "entry": bar_c, "sl": sl, "tp": tp, "risk_amount": risk}

    inst = client.resolve(name)
    lev = 10 if jpy else (5 if "GOLD" in name.upper() else 10)
    r = client.place_market(inst["instrumentId"], side, units, sl, tp, leverage=lev)
    return {"symbol": name, "skey": skey, "profile": profile["name"],
            "order_id": r.get("orderId"), "reference_id": r.get("referenceId"),
            "instrument_id": inst["instrumentId"], "risk_amount": risk,
            "side": side, "entry": bar_c, "sl": sl, "tp": tp}


def main():
    import argparse
    p = argparse.ArgumentParser(description="Hourly sweep-reclaim monitor (Tokyo + London)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="bypass session-window check (testing)")
    args = p.parse_args()

    load_env()
    now = datetime.now(timezone.utc)
    state = {"orders": []}
    if os.path.exists(STATE):
        state = json.load(open(STATE))

    account = float(os.environ.get("ACCOUNT_SIZE", "10000"))
    risk = account * float(os.environ.get("RISK_PCT", "1.0")) / 100.0 / 4.0

    client = None
    signals = []
    for profile in PROFILES:
        if not args.force and not in_window(now, profile["trigger"]):
            continue
        for name, sym in profile["pairs"]:
            try:
                dry = args.dry_run
                if not dry and client is None:
                    from etoro_client import EtoroClient
                    client = EtoroClient(mode=os.environ.get("ETORO_MODE", "demo"))
                r = check_signal(client, profile, name, sym, risk, now, state, dry)
                if r:
                    signals.append(r)
                    if not dry:
                        state["orders"].append(r)
            except Exception as e:
                print(f"[{profile['name']}] {name}: ERROR {e}")

    if args.dry_run:
        for s in signals:
            print(f"DRY: [{s['profile']}] {s['symbol']} {s['side']} entry {s['entry']:.4f} "
                  f"SL {s['sl']:.4f} TP {s['tp']:.4f}")
        if not signals:
            print("No confirmed reclaim signals this hour")
        return

    if signals:
        os.makedirs("results", exist_ok=True)
        json.dump(state, open(STATE, "w"), indent=1, default=str)
        fields = [{"name": f"[{o['profile']}] {o['symbol']} {o['side'].upper()}", "inline": True,
                   "value": f"entry {o['entry']:.4f}\nSL {o['sl']:.4f} · TP {o['tp']:.4f}"}
                  for o in signals if not o.get("dry")]
        send(load_webhook(), {"title": f"Sweep Reclaim — {len(fields)} trade(s) opened",
                              "color": BLUE, "fields": fields,
                              "footer": {"text": f"sweep + reclaim confirmed on hourly close · risk {risk:.0f}/trade"}})


if __name__ == "__main__":
    main()
