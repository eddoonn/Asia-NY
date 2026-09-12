import argparse
import os

import pandas as pd

import discord_style as style

from backtest import load_data
from config import load_env
from market_calendar import closure, is_open, session_window, to_ct
from notify import (etoro_symbols, load_webhook, reference_levels, send,
                    signal_symbol_for)
from strategy import add_atr, ny_levels

# Which session is running is asked of the calendar, never inferred from the
# last data bar: Globex reopens at 17:00 CT, so the Asia session is 22:00-10:00
# UTC on CDT and 23:00-11:00 UTC on CST.
NY_LATE = (19, 21)
BUF = 1.0
ATR_MULT = 1.0
ATR_LEN = 10
RR = 0.75


class NoSession(RuntimeError):
    """No session is running, or it has no reference window to arm from."""


def session_levels(symbol, now=None):
    """(ref, atr, window) for the Asia session `now` belongs to.

    The reference is the one `strategy.find_trades` would arm from - the most
    recent NY-late window that closed before the session's first bar - so an
    order can never be derived from a level set that has not finished forming,
    or from a session that has already ended.
    """
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    window = session_window(now)
    if window is None:
        raise NoSession(f"no Asia session at {to_ct(now):%a %Y-%m-%d %H:%M} CT "
                        "(between the 05:00 and 17:00 CT session halves)")
    df = load_data(symbol, "12d", "60m")
    add_atr(df, ATR_LEN)
    index = df.index
    levels = ny_levels(index, df["High"].values, df["Low"].values, NY_LATE)
    ref = reference_levels(levels, index, window)
    if ref is None:
        raise NoSession(f"no NY-late reference that closed before the "
                        f"{window[0]:%Y-%m-%d %H:%M} UTC session open")
    return ref, float(df["atr"].iloc[-1]), window


def is_sunday_session(now=None):
    """True at the open of the Sunday session (thin liquidity).

    Asked in CT, not UTC: the session opens at 17:00 CT, which is Sunday 22:00
    UTC on CDT but 23:00 UTC on CST - the same market day either way.
    """
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    return to_ct(now).weekday() == 6


def main():
    p = argparse.ArgumentParser(description="Place Asia-grab stop orders")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--broker", choices=["ig", "etoro"], default=os.environ.get("BROKER", "ig"))
    p.add_argument("--skip-sunday", action="store_true",
                   default=os.environ.get("SKIP_SUNDAY", "0") == "1")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--as-of", default=None,
                   help="pin the clock to this UTC timestamp (ISO-8601); for tests/backfill")
    args = p.parse_args()

    load_env()
    account = float(os.environ.get("ACCOUNT_SIZE", "10000"))
    risk_pct = float(os.environ.get("RISK_PCT", "1.0"))
    now = parse_as_of(args.as_of)

    block = closure(now)
    if block is not None:
        _decline(f"market closed - {block.name}, reopens {block.until:%Y-%m-%d %H:%M} UTC",
                 dry=args.dry_run)
        return

    if args.skip_sunday and is_sunday_session(now):
        _decline("Sunday session - skipped by --skip-sunday filter (thin liquidity)",
                 dry=args.dry_run)
        return

    try:
        ref, atr, window = session_levels(args.symbol, now=now)
    except NoSession as exc:
        _decline(f"{exc}", dry=args.dry_run)
        return

    short_level = ref[0] + BUF * atr
    long_level = ref[1] - BUF * atr
    stop_dist = ATR_MULT * atr
    limit_dist = RR * stop_dist
    risk_amount = account * risk_pct / 100.0

    print(f"Session {window[0]:%Y-%m-%d %H:%M} -> {window[1]:%H:%M} UTC "
          f"| NY-late {ref[0]:.2f} / {ref[1]:.2f} | ATR{ATR_LEN} {atr:.2f}")

    if args.broker == "etoro":
        run_etoro(account, risk_pct, args.dry_run, now=now)
    else:
        run_ig(short_level, long_level, stop_dist, limit_dist,
               risk_amount, risk_pct, args.dry_run)


def parse_as_of(value):
    """`--as-of` -> tz-aware UTC Timestamp; None -> the wall clock."""
    if not value:
        return pd.Timestamp.now(tz="UTC")
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def declined_instruments():
    """What a decline applies to, as the ticker the broker lists.

    A decline is the one order message with no rows to carry the instrument, so
    it has to name them itself: "no orders tonight" without a ticker leaves the
    reader unable to tell which ladder was skipped.
    """
    if os.environ.get("BROKER", "ig") == "etoro":
        return etoro_symbols()
    return [os.environ.get("IG_EPIC", "CS.D.USGLD.CFD.IP")]


def declined_embed(message, instruments=None):
    """"No orders tonight" - a reason, and the instruments it applies to."""
    if instruments is None:
        instruments = declined_instruments()
    embed = {"title": style.title("no orders tonight"),
             "color": style.GRAY, "description": message}
    if instruments:
        embed["fields"] = [{"name": "Instruments", "inline": False,
                            "value": style.rule(*instruments,
                                                "no orders placed")}]
    return embed


def _decline(message, dry=False):
    print(f"No orders tonight - {message}")
    if dry:
        print(f"DRYRUN BLOCKED {message}")
    hook = None if dry else load_webhook()
    if hook:
        send(hook, declined_embed(message))


# The broker-ticker mapping lives in `notify.py`, which this module already
# imports: the messages and the orders have to answer "what is gold called"
# identically, and one definition is how that stays true.
args_symbol_for = signal_symbol_for


def fx_quote_is_jpy(symbol):
    return symbol.upper().replace(".24-7", "").endswith("JPY")


def run_etoro(account, risk_pct, dry, now=None):
    import json
    from etoro_client import EtoroClient

    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    symbols = etoro_symbols()
    mode = os.environ.get("RISK_MODE", "split")
    risk_total = account * risk_pct / 100.0
    per_risk = risk_total / len(symbols) if mode == "split" else risk_total

    if not is_open(now):
        blocked = closure(now)
        print(f"DRYRUN BLOCKED market closed - {blocked.name}")
        return

    print(f"eToro ({os.environ.get('ETORO_MODE', 'demo')}) instruments: {symbols}")
    print(f"risk {risk_total:.2f} total -> {per_risk:.2f} per instrument ({mode})")

    client = None
    all_orders = []
    lines = []
    window = session_window(now)
    for sym in symbols:
        try:
            ref, atr, window = session_levels(args_symbol_for(sym), now=now)
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
                lines.append((sym, f"{len(pending)} order(s) already resting - skipped"))
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
            print(f"{sym}: FAILED - {e}")

    if dry:
        for sym, desc in lines:
            print(f"  {sym}: {desc}")
            print(f"DRYRUN ORDER place {sym} {desc}")
        print("DRY RUN - no orders placed")
        return

    os.makedirs("results", exist_ok=True)
    with open("results/etoro_session.json", "w") as f:
        json.dump({"orders": all_orders, "risk_amount": risk_total}, f, indent=2)

    hook = load_webhook()
    if hook:
        session = ("no session" if window is None
                   else f"session {window[0]:%m-%d %H:%M}-{window[1]:%H:%M} UTC")
        send(hook, etoro_orders_embed(lines, per_risk, len(symbols), risk_pct,
                                      mode, session, len(all_orders) // 2))


def etoro_orders_embed(lines, per_risk, instruments, risk_pct, mode, session,
                       armed):
    """"n/m orders armed": the levels each instrument rests at, and the risk."""
    fields = [{"name": sym, "value": desc, "inline": False}
              for sym, desc in lines]
    fields.append({"name": "Risk",
                   "value": (f"{per_risk:,.2f} USD per instrument · {instruments} "
                             f"instruments = {per_risk * instruments:,.2f} USD "
                             f"({risk_pct}% of account, {mode})"),
                   "inline": False})
    return {
        "title": style.title(f"{armed}/{instruments} orders armed"),
        "color": style.BLUE,
        "description": ("A stop order either side of the NY-late range, per "
                        "instrument.\nWhichever fills first cancels its sibling at "
                        "the session end; the result follows after the flatten."),
        "fields": fields,
        "footer": {"text": style.rule(
            f"eToro {os.environ.get('ETORO_MODE', 'demo')}", session,
            "flat 03:00 CT")},
    }


def run_ig(short_level, long_level, stop_dist, limit_dist, risk_amount, risk_pct, dry):
    epic = os.environ.get("IG_EPIC", "CS.D.USGLD.CFD.IP")
    currency = os.environ.get("IG_CURRENCY", "GBP")
    size = round(risk_amount / stop_dist, 2)

    print(f"NY-late sweep: SELL STOP {short_level:.2f} | BUY STOP {long_level:.2f}")
    print(f"stop_dist {stop_dist:.2f} limit_dist {limit_dist:.2f} "
          f"size {size} {currency} risk {risk_amount:.2f}")

    if dry:
        print("DRY RUN - no orders placed")
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
        send(hook, ig_orders_embed(results, size, currency, stop_dist,
                                   limit_dist, epic, risk_pct, risk_amount))


def ig_orders_embed(results, size, currency, stop_dist, limit_dist, epic,
                    risk_pct, risk_amount):
    """The IG variant of the same message: levels, sizes, and the risk."""
    fields = [{"name": f"{d} STOP",
               "value": (f"{lv:.2f}\nSL {stop_dist:.1f} / TP {limit_dist:.1f} pts · "
                         f"{size} {currency}"),
               "inline": True} for d, lv, _ in results]
    return {
        # The epic is in the title because a locked phone shows only that, and
        # `2 orders armed` alone does not say which market they rest in.
        "title": style.title(epic, f"{len(fields)} ORDERS ARMED"),
        "color": style.GREEN,
        "description": ("Stop orders either side of the NY-late range.\n"
                        "Whichever fills first cancels its sibling at the "
                        "session end."),
        "fields": fields,
        "footer": {"text": style.rule(
            "IG", f"risk {risk_pct}% = {risk_amount:.0f} {currency}",
            "flat 03:00 CT")},
    }


if __name__ == "__main__":
    main()
