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
    p.add_argument("--skip-sunday", action="store_true",
                   default=os.environ.get("SKIP_SUNDAY", "0") == "1")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    load_env()
    account = float(os.environ.get("ACCOUNT_SIZE", "10000"))
    risk_pct = float(os.environ.get("RISK_PCT", "1.0"))

    if args.skip_sunday and pd.Timestamp.now(tz="UTC").weekday() == 6:
        msg = "Sunday session — skipped by --skip-sunday filter (thin liquidity)"
        print(msg)
        hook = load_webhook()
        if hook:
            send(hook, {"title": "No orders tonight — Sunday filter", "color": GRAY,
                        "description": msg})
        return

    ref, atr = session_levels(args.symbol)
    short_level = ref[0] + BUF * atr
    long_level = ref[1] - BUF * atr
    stop_dist = ATR_MULT * atr
    limit_dist = RR * stop_dist
    risk_amount = account * risk_pct / 100.0

    if args.broker == "etoro":
        run_etoro(account, risk_pct, args.dry_run)
    else:
        ref, atr = session_levels(args.symbol)
        run_ig(ref[0] + BUF * atr, ref[1] - BUF * atr, ATR_MULT * atr, RR * ATR_MULT * atr,
               risk_amount, risk_pct, args.dry_run)


def args_symbol_for(etoro_symbol):
    mapping = {"GOLD.24-7": "GC=F", "GOLD": "GC=F"}
    if etoro_symbol in mapping:
        return mapping[etoro_symbol]
    s = etoro_symbol.upper()
    if len(s) == 6 and s.isalpha() and "=" not in s:
        return s + "=X"
    return etoro_symbol


def fx_quote_is_jpy(symbol):
    return symbol.upper().replace(".24-7", "").endswith("JPY")


def run_etoro(account, risk_pct, dry):
    import json
    from etoro_client import EtoroClient
    symbols = [s.strip() for s in os.environ.get(
        "ETORO_SYMBOLS", "GOLD.24-7,EURUSD,GBPUSD,USDJPY").split(",") if s.strip()]
    mode = os.environ.get("RISK_MODE", "split")
    risk_total = account * risk_pct / 100.0
    per_risk = risk_total / len(symbols) if mode == "split" else risk_total

    print(f"eToro ({os.environ.get('ETORO_MODE', 'demo')}) instruments: {symbols}")
    print(f"risk {risk_total:.2f} total -> {per_risk:.2f} per instrument ({mode})")

    client = None
    all_orders = []
    lines = []
    for sym in symbols:
        try:
            ref, atr = session_levels(args_symbol_for(sym))
            s_level = ref[0] + BUF * atr
            l_level = ref[1] - BUF * atr
            s_dist = ATR_MULT * atr
            l_dist = RR * s_dist
            if dry:
                lines.append((sym, f"SHORT {s_level:,.5f} · LONG {l_level:,.5f} · "
                                   f"SL {s_dist:,.5f} · TP {l_dist:,.5f}"))
                continue
            if client is None:
                client = EtoroClient()
            inst = client.resolve(sym)
            instrument_id = inst["instrumentId"]

            pf = client.portfolio()
            pending = [o for o in (pf.get("clientPortfolio", {}).get("orders") or [])
                       if o.get("instrumentID") == instrument_id]
            if pending:
                lines.append((sym, f"{len(pending)} order(s) already resting — skipped"))
                continue

            price = (ref[0] + ref[1]) / 2
            if fx_quote_is_jpy(sym):
                units = round(per_risk * price / s_dist, 2)
                leverage = 10
            elif sym.upper().startswith("GOLD"):
                units = round(per_risk / s_dist, 4)
                leverage = 5
            else:
                units = round(per_risk / s_dist, 2)
                leverage = 10

            for txn, trigger, sl, tp in (
                    ("sellShort", s_level, s_level + s_dist, s_level - l_dist),
                    ("buy", l_level, l_level - s_dist, l_level + l_dist)):
                r = client.place_mit(inst, txn, trigger, sl, tp, units, leverage=leverage)
                all_orders.append({"order_id": r.get("orderId"), "reference_id": r.get("referenceId"),
                                   "transaction": txn, "symbol": sym,
                                   "trigger": trigger, "sl": sl, "tp": tp,
                                   "units": units, "risk_amount": per_risk,
                                   "instrument_id": instrument_id})
            lines.append((sym, f"SHORT {s_level:,.5f} · LONG {l_level:,.5f} · "
                               f"SL {s_dist:,.5f} · TP {l_dist:,.5f} · {units} units @{leverage}x"))
            print(f"{sym}: placed 2 MIT orders (risk {per_risk:.2f})")
        except Exception as e:
            lines.append((sym, f"FAILED: {e}"))
            print(f"{sym}: FAILED — {e}")

    if dry:
        for sym, desc in lines:
            print(f"  {sym}: {desc}")
        print("DRY RUN — no orders placed")
        return

    os.makedirs("results", exist_ok=True)
    with open("results/etoro_session.json", "w") as f:
        json.dump({"orders": all_orders, "risk_amount": risk_total}, f, indent=2)

    hook = load_webhook()
    if hook:
        fields = [{"name": sym, "value": desc, "inline": False} for sym, desc in lines]
        fields.append({"name": "Risk",
                       "value": f"{per_risk:,.2f} USD per instrument × {len(symbols)} "
                                f"= {per_risk * len(symbols):,.2f} USD total ({risk_pct}% of account, {mode})",
                       "inline": False})
        fields.append({"name": "Next message",
                       "value": "Session result with exact P&L per instrument in $ and R (~09:05 London)",
                       "inline": False})
        send(hook, {"title": f"Orders placed — {len(all_orders) // 2}/{len(symbols)} instruments armed",
                    "color": BLUE,
                    "description": ("Asia sweep traps resting on eToro. Whichever level is touched first fills; "
                                    "the sibling cancels at session end."),
                    "fields": fields,
                    "footer": {"text": f"eToro {os.environ.get('ETORO_MODE', 'demo')} · session 22:00–10:00 UTC"}})


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
