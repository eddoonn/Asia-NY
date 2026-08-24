import json
import os
from datetime import datetime, timezone

import pandas as pd

from backtest import load_data
from config import load_env
from notify import load_webhook, send
from strategy import add_atr, day_ids, ny_levels

PAIRS = [
    ("USDJPY", "USDJPY=X"),
    ("EURJPY", "EURJPY=X"),
    ("GBPJPY", "GBPJPY=X"),
    ("AUDJPY", "AUDJPY=X"),
]
ASIA = (22, 10)
NY_LATE = (19, 21)
ENTRY_BUFFER = 1.0
WICK_BUFFER = 0.5
STATE = "results/reclaim_session.json"
BLUE, GRAY = 0x3498DB, 0x95A5A6


def session_active(now):
    return now.weekday() < 5 and (now.hour >= ASIA[0] or now.hour < ASIA[1])


def last_closed_bar(df, now):
    if df.index[-1].hour == now.hour and now - df.index[-1] < pd.Timedelta(minutes=59):
        return df.iloc[-2], df.index[-2]
    return df.iloc[-1], df.index[-1]


def check_pair(client, name, sym, risk, now, state, session_key, dry):
    if any(o["symbol"] == name and o["session"] == session_key for o in state["orders"]):
        return None
    df = load_data(sym, "10d", "60m")
    add_atr(df, 10)
    idx = df.index
    bar, bar_ts = last_closed_bar(df, now)
    if bar_ts.date() != now.date() and not (now.hour < ASIA[1] and (idx[-1].date() - pd.Timedelta(days=1)).month):
        pass

    levels = ny_levels(idx, df["High"].values, df["Low"].values, NY_LATE,
                       opens=df["Open"].values, closes=df["Close"].values)
    cur_id = int(day_ids(idx)[-1])
    ref = None
    for p in range(cur_id - 1, cur_id - 8, -1):
        if p in levels and levels[p][2] < len(idx) - 1:
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
    units = round(risk * bar_c / risk_dist, 2) if "JPY" in name else round(risk / risk_dist, 4)
    print(f"{name}: {side} signal on {bar_ts} bar — entry ~{bar_c:.3f}, SL {sl:.3f}, TP {tp:.3f}")
    if dry:
        return {"symbol": name, "session": session_key, "dry": True, "side": side,
                "entry": bar_c, "sl": sl, "tp": tp, "risk_amount": risk}

    inst = client.resolve(name)
    r = client.place_market(inst["instrumentId"], side, units, sl, tp)
    return {"symbol": name, "session": session_key, "order_id": r.get("orderId"),
            "reference_id": r.get("referenceId"), "instrument_id": inst["instrumentId"],
            "risk_amount": risk, "side": side, "entry": bar_c, "sl": sl, "tp": tp}


def main():
    import argparse
    p = argparse.ArgumentParser(description="Hourly Tokyo-reclaim monitor")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="bypass session-active check (testing)")
    args = p.parse_args()

    load_env()
    now = datetime.now(timezone.utc)
    if not session_active(now) and not args.force:
        print("Session inactive — nothing to monitor")
        return
    session_key = str(now.date()) if now.hour >= ASIA[0] else str((now - pd.Timedelta(days=1)).date())

    state = {"orders": []}
    if os.path.exists(STATE):
        state = json.load(open(STATE))

    account = float(os.environ.get("ACCOUNT_SIZE", "10000"))
    risk = account * float(os.environ.get("RISK_PCT", "1.0")) / 100.0 / len(PAIRS)

    client = None
    placed, signals = [], []
    for name, sym in PAIRS:
        try:
            dry = args.dry_run
            if not dry and client is None:
                from etoro_client import EtoroClient
                client = EtoroClient(mode=os.environ.get("ETORO_MODE", "demo"))
            r = check_pair(client, name, sym, risk, now, state, session_key, dry)
            if r:
                signals.append(r)
                if not dry:
                    state["orders"].append(r)
                    placed.append(r)
        except Exception as e:
            print(f"{name}: ERROR {e}")

    if args.dry_run:
        for s in signals:
            print(f"DRY: {s['symbol']} {s['side']} entry {s['entry']:.3f} SL {s['sl']:.3f} TP {s['tp']:.3f}")
        if not signals:
            print("No confirmed reclaim signals this hour")
        return

    if signals:
        os.makedirs("results", exist_ok=True)
        json.dump(state, open(STATE, "w"), indent=1, default=str)
        fields = [{"name": f"{o['symbol']} {o['side'].upper()}", "inline": True,
                   "value": f"entry {o['entry']:.3f}\nSL {o['sl']:.3f} · TP {o['tp']:.3f}"}
                  for o in placed]
        send(load_webhook(), {"title": f"Tokyo Reclaim — {len(placed)} trade(s) opened",
                              "color": BLUE, "fields": fields,
                              "footer": {"text": f"sweep + reclaim confirmed on hourly close · risk {risk:.0f}/trade"}})


if __name__ == "__main__":
    main()
