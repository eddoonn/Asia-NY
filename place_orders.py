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
    p = argparse.ArgumentParser(description="Place Asia-grab stop orders on IG")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    load_env()
    epic = os.environ.get("IG_EPIC", "CS.D.USGLD.CFD.IP")
    currency = os.environ.get("IG_CURRENCY", "GBP")
    account = float(os.environ.get("ACCOUNT_SIZE", "10000"))
    risk_pct = float(os.environ.get("RISK_PCT", "1.0"))

    ref, atr = session_levels(args.symbol)
    short_level = ref[0] + BUF * atr
    long_level = ref[1] - BUF * atr
    stop_dist = ATR_MULT * atr
    limit_dist = RR * stop_dist
    risk_amount = account * risk_pct / 100.0
    size = round(risk_amount / stop_dist, 2)

    print(f"NY-late high/low: {ref[0]:.2f} / {ref[1]:.2f}  ATR{ATR_LEN}: {atr:.2f}")
    print(f"SELL STOP {short_level:.2f} | BUY STOP {long_level:.2f}")
    print(f"stop_dist {stop_dist:.2f} limit_dist {limit_dist:.2f} "
          f"size {size} {currency} risk {risk_amount:.2f}")

    if args.dry_run:
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
