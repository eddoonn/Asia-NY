import json
import os
from datetime import datetime, timezone

import pandas as pd

from backtest import load_data
from config import load_env
from market_calendar import (closure, is_open, london_hour_window,
                             session_hour_window, session_window)
from notify import load_webhook, reference_levels, send
from strategy import add_atr, day_ids, ny_levels

# A window is either a fixed UTC hour pair, or one of the named windows that the
# market calendar resolves for the season. Globex reopens at 17:00 CT, so GLOBEX
# is 22:00-10:00 UTC on CDT and 23:00-11:00 UTC on CST; LONDON is 07:00-13:00 on
# CDT and 08:00-14:00 on CST. Neither is a constant in UTC.
GLOBEX = "globex"
LONDON = "london"

PROFILES = [
    # Tokyo trades the Asia session itself, and references the NY-late window
    # that closed just before it.
    {"name": "tokyo", "trigger": GLOBEX, "reference": (19, 21),
     "entry_buffer": 1.0, "wick_buffer": 0.5,
     "pairs": [("USDJPY", "USDJPY=X"), ("EURJPY", "EURJPY=X"),
               ("GBPJPY", "GBPJPY=X"), ("AUDJPY", "AUDJPY=X")]},
    # London trades the London morning against the overnight Asia range, so its
    # reference window is the same Globex session - and by design it overlaps
    # the trigger window, which is strategy.find_trades' dynamic-reference case.
    {"name": "london", "trigger": LONDON, "reference": GLOBEX,
     "entry_buffer": 0.5, "wick_buffer": 0.25,
     "pairs": [("EURUSD", "EURUSD=X"), ("GBPUSD", "GBPUSD=X"),
               ("USDJPY", "USDJPY=X")]},
]
STATE = "results/reclaim_session.json"
BLUE, GRAY = 0x3498DB, 0x95A5A6


def resolve_window(value, day=None):
    """`globex`/`london` -> the season-correct UTC hour pair; else literal."""
    if value == GLOBEX:
        return session_hour_window(day)
    if value == LONDON:
        return london_hour_window(day)
    return value


def parse_as_of(value):
    """`--as-of` -> tz-aware UTC datetime; None -> the wall clock."""
    if not value:
        return datetime.now(timezone.utc)
    ts = pd.Timestamp(value)
    return (ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")).to_pydatetime()


def profile_windows(profile, day=None):
    """(trigger, reference) UTC hour windows for a profile."""
    return (resolve_window(profile["trigger"], day),
            resolve_window(profile["reference"], day))


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


def reference_for(profile, levels, idx, now, trigger):
    """The sweep level set this profile should be trading against."""
    if profile["trigger"] == GLOBEX:
        # Same rule as strategy.find_trades: the reference window must have
        # closed before the session's first bar, so a half-formed Asia range
        # can never be armed against.
        window = session_window(now)
        return None if window is None else reference_levels(levels, idx, window)
    # London deliberately references the Asia range while it is still forming
    # (find_trades' dynamic reference), so key it on the most recent day id.
    cur_id = int(day_ids(idx)[-1])
    for p in range(cur_id, cur_id - 8, -1):
        if p in levels:
            return levels[p]
    return None


def check_signal(client, profile, name, sym, risk, now, state, dry):
    trigger, reference = profile_windows(profile)
    # `main` skips out-of-window profiles before it fetches anything, but the
    # check belongs here too: without it the bar it happens to see could be in a
    # window that has not opened yet.
    if not in_window(now, trigger):
        return None
    skey = f"{profile['name']}:{session_key(now, trigger)}"
    if any(o.get("skey") == skey and o["symbol"] == name for o in state["orders"]):
        return None
    df = load_data(sym, "10d", "60m")
    add_atr(df, 10)
    idx = df.index
    if idx[-1].hour == now.hour and now - idx[-1] < pd.Timedelta(minutes=59):
        df = df.iloc[:-1]
        idx = df.index
    bar = df.iloc[-1]
    bar_ts = idx[-1]
    if not in_window(bar_ts, trigger):
        return None

    df_lvl = df.iloc[:-1]
    idx_lvl = df_lvl.index
    levels = ny_levels(idx_lvl, df_lvl["High"].values, df_lvl["Low"].values, reference,
                       opens=df_lvl["Open"].values, closes=df_lvl["Close"].values)
    ref = reference_for(profile, levels, idx_lvl, now, trigger)
    if ref is None:
        return None
    rh, rl = ref[0], ref[1]

    bar_o, bar_h, bar_l, bar_c = (float(bar[k]) for k in ("Open", "High", "Low", "Close"))
    atr = float(df["atr"].iloc[-1])
    buf = float(profile.get("entry_buffer", 1.0)) * atr
    wick_buf = float(profile.get("wick_buffer", 0.25))

    side = sl = tp = None
    if bar_h >= rh + buf and bar_c < rh:
        side = "sellShort"
        sl = bar_h + wick_buf * atr
        tp = rl
    elif bar_l <= rl - buf and bar_c > rl:
        side = "buy"
        sl = bar_l - wick_buf * atr
        tp = rh
    if side is None:
        return None

    risk_dist = abs(sl - bar_c)
    jpy = "JPY" in name
    units = round(risk * bar_c / risk_dist, 2) if jpy else round(risk / risk_dist, 4)
    print(f"[{profile['name']}] {name}: {side} on {bar_ts} - entry ~{bar_c:.4f} "
          f"SL {sl:.4f} TP {tp:.4f} | window {trigger[0]:02d}-{trigger[1]:02d} UTC")
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
    p.add_argument("--as-of", default=None,
                   help="pin the clock to this UTC timestamp (ISO-8601); for tests/backfill")
    args = p.parse_args()

    load_env()
    now = parse_as_of(args.as_of)
    state = {"orders": []}

    # A shut market is not something --force may override: the flag exists to
    # bypass the session-window check while testing, not to trade a closed book.
    block = closure(now)
    if block is not None:
        print(f"DRYRUN BLOCKED market closed - {block.name}, reopens "
              f"{block.until:%Y-%m-%d %H:%M} UTC")
        return
    if os.path.exists(STATE):
        state = json.load(open(STATE))

    account = float(os.environ.get("ACCOUNT_SIZE", "10000"))
    risk_pct = float(os.environ.get("RISK_PCT", "1.0"))
    risk_mode = os.environ.get("RISK_MODE", "each").lower()
    risk_total = account * risk_pct / 100.0
    # Validated sizing is per-instrument (RISK_MODE=each). Only divide if explicitly split.
    risk = risk_total if risk_mode == "each" else risk_total / 4.0

    client = None
    signals = []
    for profile in PROFILES:
        trigger, _ = profile_windows(profile)
        if not args.force and not in_window(now, trigger):
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
            print(f"DRYRUN ORDER place {s['symbol']} {s['side']} "
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
        names = sorted({o["profile"] for o in signals if not o.get("dry")})
        windows = " · ".join(
            f"{p['name']} {profile_windows(p)[0][0]:02d}-{profile_windows(p)[0][1]:02d} UTC"
            for p in PROFILES if p["name"] in names)
        send(load_webhook(), {"title": f"Sweep Reclaim - {len(fields)} trade(s) opened",
                              "color": BLUE, "fields": fields,
                              "footer": {"text": f"sweep + reclaim confirmed on hourly close "
                                                 f"· {windows} · risk {risk:.0f}/trade"}})


if __name__ == "__main__":
    main()
