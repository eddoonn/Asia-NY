import argparse
import os

import pandas as pd

from backtest import load_data
from config import load_env
from notify import load_webhook, send
from strategy import add_atr, day_ids, ny_levels

ASIA = (22, 10)
NY_LATE = (19, 21)
BUF = 1.0
ATR_MULT = 1.0
ATR_LEN = 10
RR = 0.75
GREEN, GRAY = 0x2ECC71, 0x95A5A6


def session_levels(symbol):
    df = load_data(symbol, "12d", "60m")
    add_atr(df, ATR_LEN)
    index = df.index
    ids = day_ids(index)
    cur_id = ids[-1] + (1 if index[-1].hour >= ASIA[0] else 0)
    levels = ny_levels(index, df["High"].values, df["Low"].values, NY_LATE)
    ref = None
    for p in range(cur_id - 1, cur_id - 8, -1):
        if p in levels:
            ref = levels[p]
            break
    if ref is None:
        raise SystemExit("No reference levels available")
    atr = float(df["atr"].iloc[-1])
    return ref, atr


def main():
    p = argparse.ArgumentParser(description="Place Asia-grab stop orders")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--broker", choices=["ig", "etoro"], default=os.environ.get("BROKER", "ig"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    load_env()
    account = float(os.environ.get("ACCOUNT_SIZE", "10000"))
    risk_pct = float(os.environ.get("RISK_PCT", "1.0"))

    ref, atr = session_levels(args.symbol)
    short_level = ref[0] + BUF * atr
    long_level = ref[1] - BUF * atr
    stop_dist = ATR_MULT * atr
    limit_dist = RR * stop_dist
    risk_amount = account * risk_pct / 100.0

    if args.broker == "etoro":
        run_etoro(short_level, long_level, stop_dist, limit_dist, risk_amount, risk_pct, args.dry_run)
    else:
        run_ig(short_level, long_level, stop_dist, limit_dist, risk_amount, risk_pct, args.dry_run)


def run_etoro(short_level, long_level, stop_dist, limit_dist, risk_amount, risk_pct, dry):
    import json
    from etoro_client import EtoroClient
    symbol = os.environ.get("ETORO_SYMBOL", "GOLD")
    units = round(risk_amount / stop_dist, 4)

    print(f"eToro ({os.environ.get('ETORO_MODE', 'demo')}) instrument {symbol}")
    print(f"NY-late high/low sweep: SELL_SHORT MIT {short_level:.2f} | BUY MIT {long_level:.2f}")
    print(f"stop_dist {stop_dist:.2f} limit_dist {limit_dist:.2f} units {units} oz "
          f"(risk {risk_amount:.2f} USD)")
    if dry:
        print("DRY RUN — no orders placed")
        return

    client = EtoroClient()
    inst = client.resolve(symbol)
    instrument_id = inst["instrumentId"]
    print(f"Resolved {inst.get('internalSymbolFull')} -> id {instrument_id}")

    try:
        pf = client.portfolio()
        pending = [o for o in (pf.get("clientPortfolio", {}).get("orders") or [])
                   if o.get("instrumentID") == instrument_id]
        if pending:
            msg = f"Orders already resting for {symbol} ({len(pending)}) — skipping to avoid duplicates"
            print(msg)
            hook = load_webhook()
            if hook:
                send(hook, {"title": "Orders already placed — skipped", "color": GRAY,
                            "description": msg})
            return
    except Exception as e:
        print(f"Duplicate check failed ({e}) — continuing")

    orders = []
    for txn, trigger, sl, tp in (
            ("sellShort", short_level, short_level + stop_dist, short_level - limit_dist),
            ("buy", long_level, long_level - stop_dist, long_level + limit_dist)):
        r = client.place_mit(inst, txn, trigger, sl, tp, units)
        orders.append({"order_id": r.get("orderId"), "reference_id": r.get("referenceId"),
                       "transaction": txn,
                       "trigger": trigger, "sl": sl, "tp": tp,
                       "units": units, "risk_amount": risk_amount,
                       "instrument_id": instrument_id})
        print(f"Placed {txn} MIT @ {trigger:.2f} SL {sl:.2f} TP {tp:.2f} -> {r}")

    os.makedirs("results", exist_ok=True)
    with open("results/etoro_session.json", "w") as f:
        json.dump({"orders": orders, "risk_amount": risk_amount}, f, indent=2)

    hook = load_webhook()
    if hook:
        send(hook, {
            "title": "Orders placed for tonight's Asia session",
            "color": BLUE,
            "description": (
                "**The setup:** yesterday's NY close left a high and a low. "
                "If Asia sweeps one of them, we fade the move.\n"
                "Two resting trigger orders are live on eToro — **whichever level is touched first fills, "
                "the other is cancelled at 09:05 London.** No touch = no trade."),
            "fields": [
                {"name": "SHORT — if price sweeps ABOVE",
                 "value": (f"Fill at **{short_level:,.2f}**\n"
                           f"Stop {short_level + stop_dist:,.2f}\n"
                           f"Target {short_level - limit_dist:,.2f}"),
                 "inline": True},
                {"name": "LONG — if price sweeps BELOW",
                 "value": f"Fill at **{long_level:,.2f}**\nStop {long_level - stop_dist:,.2f}\nTarget {long_level + limit_dist:,.2f}",
                 "inline": True},
                {"name": "Position size",
                 "value": f"{units:.2f} oz (~${risk_amount:,.0f} risk, 1% of account)",
                 "inline": False},
                {"name": "Next message you'll get",
                 "value": "The session result with exact profit/loss in $ and R (sent ~09:05 London, or after the trade closes)",
                 "inline": False},
            ],
            "footer": {"text": f"GOLD.24-7 · eToro {os.environ.get('ETORO_MODE', 'demo')} · session 22:00–10:00 UTC"}})


def run_ig(short_level, long_level, stop_dist, limit_dist, risk_amount, risk_pct, dry):
    epic = os.environ.get("IG_EPIC", "CS.D.USGLD.CFD.IP")
    currency = os.environ.get("IG_CURRENCY", "GBP")
    size = round(risk_amount / stop_dist, 2)

    print(f"NY-late sweep: SELL STOP {short_level:.2f} | BUY STOP {long_level:.2f}")
    print(f"stop_dist {stop_dist:.2f} limit_dist {limit_dist:.2f} "
          f"size {size} {currency} risk {risk_amount:.2f}")

    if dry:
        print("DRY RUN — no orders placed")
        return

    from ig_client import IGClient
    ig = IGClient()
    ig.login()
    info = ig.market(epic)
    min_size = float(info["dealingRules"]["minDealSize"]["value"])
    if size < min_size:
        print(f"WARNING: size {size} below IG minimum {min_size}, using minimum "
              f"(real risk = {min_size * stop_dist:.2f} {currency})")
        size = min_size
    snapshot = info.get("snapshot", {})
    print(f"Market: {info['instrument']['name']} bid {snapshot.get('bid')} offer {snapshot.get('offer')}")

    results = []
    for direction, level in (("SELL", short_level), ("BUY", long_level)):
        r = ig.create_working_order(epic, direction, size, level, stop_dist, limit_dist, currency)
        results.append((direction, level, r))
        print(f"Placed {direction} STOP @ {level:.2f} -> {r}")

    hook = load_webhook()
    if hook:
        fields = [{"name": f"{d} STOP", "value": f"{lv:.2f} (SL {stop_dist:.1f} / TP {limit_dist:.1f} pts, size {size})",
                   "inline": True} for d, lv, _ in results]
        send(hook, {"title": "IG orders placed — Asia Grab", "color": GREEN,
                    "fields": fields,
                    "footer": {"text": f"{epic} | risk {risk_pct}% = {risk_amount:.0f} {currency}"}})


if __name__ == "__main__":
    main()
