import argparse
import os

import pandas as pd

from config import load_env
from market_calendar import (closure, is_open, london_exit_hour, session_exit_hour,
                            session_window)
from notify import load_webhook, send
GRAY, BLUE = 0x95A5A6, 0x3498DB

# Both flats come from the calendar, expressed in CT: the Asia session at
# 03:00 CT (08:00 UTC on CDT, 09:00 on CST) and London at 12:00 CT (17:00 UTC on
# CDT, 18:00 on CST). So the workflow no longer hardcodes "08", and neither
# flatten is a summer value applied all year.
CLEANUP_HOURS = 2          # how long after the flat the cleanup cron keeps firing


def flat_hour(profile, now=None):
    """UTC hour this profile's session is flattened at."""
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    return london_exit_hour(now) if profile == "london" else session_exit_hour(now)


def which_profile(now=None):
    """'tokyo' inside the Asia cleanup window, else 'london'.

    The scheduled job fires hourly, so the profile whose flat has just passed
    owns the cleanup. The Asia flat is 08:00 UTC on CDT and 09:00 on CST, which
    is why testing the hour against a constant cannot work.
    """
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    flat = session_exit_hour(now)
    return "tokyo" if flat <= now.hour < flat + CLEANUP_HOURS else "london"


def describe_schedule(now=None):
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    window = session_window(now)
    parts = [f"tokyo flat {session_exit_hour(now):02d}:00 UTC",
             f"london flat {london_exit_hour(now):02d}:00 UTC"]
    if window is not None:
        parts.append(f"session {window[0]:%Y-%m-%d %H:%M} -> {window[1]:%H:%M} UTC")
    return " · ".join(parts)


def main():
    p = argparse.ArgumentParser(description="Cancel unfilled orders and close open positions (session end)")
    p.add_argument("--broker", choices=["ig", "etoro"], default=os.environ.get("BROKER", "ig"))
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--profile", choices=["tokyo", "london"], default=None,
                   help="only clean up orders for this profile (leaves other profile orders untouched)")
    p.add_argument("--auto", action="store_true",
                   help="pick the profile from the market calendar (Asia flat is 08:00 UTC on "
                        "CDT and 09:00 on CST, London's is 17:00 UTC)")
    p.add_argument("--which-profile", action="store_true",
                   help="print the profile whose session is being flattened now, then exit")
    p.add_argument("--as-of", default=None,
                   help="pin the clock to this UTC timestamp (ISO-8601); for tests/backfill")
    args = p.parse_args()

    now = pd.Timestamp.now(tz="UTC") if not args.as_of else pd.Timestamp(args.as_of)
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    if args.which_profile:
        print(which_profile(now))
        return

    # Nothing to flatten against a shut book, and the broker would reject the
    # cancels anyway.  The every-hourly cron fires on weekends and through the
    # Friday-evening close, so this is the guard that keeps it quiet.
    block = closure(now)
    if block is not None:
        print(f"Market closed - {block.name}, reopens {block.until:%Y-%m-%d %H:%M} UTC. "
              f"Nothing to flatten.")
        print(f"DRYRUN BLOCKED market closed - {block.name}")
        return
    if args.auto and args.profile is None:
        args.profile = which_profile(now)
        print(f"Auto-selected profile: {args.profile} ({describe_schedule(now)})")
    elif args.profile is None and args.broker == "etoro":
        print(f"Note: no --profile/--auto given, cleaning up every profile "
              f"({describe_schedule(now)})")

    load_env()
    if args.broker == "etoro":
        return run_etoro(args.dry_run, profile=args.profile)
    return run_ig(args.dry_run)


def run_etoro(dry, profile=None):
    import json
    import time
    from datetime import date, timedelta
    from etoro_client import EtoroClient

    state_path = "results/etoro_session.json"
    reclaim_path = "results/reclaim_session.json"
    orders, risk_amount = [], 100.0
    loaded = False
    raw_states = {}
    for sp in (state_path, reclaim_path):
        if os.path.exists(sp):
            s = json.load(open(sp))
            raw_states[sp] = s
            orders.extend(s.get("orders", []))
            risk_amount = float(s.get("risk_amount") or s.get("risk") or risk_amount)
            # per-order risk_amount may override
            for o in s.get("orders", []):
                if o.get("risk_amount"):
                    risk_amount = float(o["risk_amount"])
            loaded = True
    if not loaded:
        print("No session state found — nothing to clean up")
        return
    if profile:
        filt = [o for o in orders if o.get("profile") == profile]
        kept = [o for o in orders if o.get("profile") != profile]
        # legacy orders (from place_orders) have no profile — they are the Asia
        # session's, so the tokyo cleanup owns them
        if profile == "tokyo":
            legacy = [o for o in orders if not o.get("profile")]
            filt.extend(legacy)
            kept = [o for o in kept if o.get("profile")]
        orders = filt
        if not orders:
            print(f"No {profile} orders to clean up — leaving {len(kept)} other-profile order(s) untouched")
            return
    else:
        kept = []
    if not orders:
        print("Session state has no orders — nothing to clean up")
        return

    if dry:
        print(f"DRY RUN — would check {len(orders)} eToro order(s): "
              + ", ".join(str(o["order_id"]) for o in orders))
        for o in orders:
            print(f"DRYRUN ORDER cancel/close {o.get('symbol')} "
                  f"{o.get('side', '')} {o.get('order_id')}")
        return

    client = EtoroClient()
    hist = client.history((date.today() - timedelta(days=2)).isoformat())
    hist_by_order = {}
    for t in (hist if isinstance(hist, list) else hist.get("items", [])):
        hist_by_order[t.get("orderId")] = t

    results = []
    for o in orders:
        oid = o["order_id"]
        txn = o["transaction"]
        side = "SHORT" if txn == "sellShort" else "LONG"
        closed_trade = hist_by_order.get(oid)
        if closed_trade:
            pnl = float(closed_trade.get("netProfit", 0.0))
            exit_rate = float(closed_trade.get("closeRate", 0.0))
            tp, sl = float(o.get("tp", 0)), float(o.get("sl", 0))
            reason = "target hit"
            if sl and abs(exit_rate - sl) < 1.0:
                reason = "stop hit"
            elif not (tp and abs(exit_rate - tp) < 1.0):
                reason = "closed at market"
            results.append({"side": side, "pnl": pnl, "r": pnl / float(o.get("risk_amount", risk_amount)),
                            "symbol": o.get("symbol", ""),
                            "open_rate": float(closed_trade.get("openRate", o["trigger"])),
                            "exit_rate": exit_rate, "reason": reason,
                            "open_ts": closed_trade.get("openTimestamp", ""),
                            "close_ts": closed_trade.get("closeTimestamp", "")})
            print(f"{side} order {oid}: auto-closed ({reason}), PnL {pnl:+.2f}")
            continue

        info = client.lookup(oid, o.get("reference_id"))
        body = info if isinstance(info, dict) else {}
        ods = body.get("orders") or [body]
        handled = False
        for od in ods:
            status = (od.get("status") or {}).get("id")
            if status in (1, 2, 11, 12):
                client.cancel_order(oid)
                results.append({"side": side, "pnl": 0.0, "r": 0.0, "no_fill": True,
                                "symbol": o.get("symbol", "")})
                print(f"{side} order {oid}: cancelled (was pending)")
                handled = True
            elif status in (3, 5):
                for pe in od.get("positionExecutions") or []:
                    pid = pe.get("positionId")
                    try:
                        cr = client.close_position(pid, o["instrument_id"])
                        close_oid = cr.get("orderForClose", {}).get("orderID")
                        exit_rate = None
                        for _ in range(8):
                            time.sleep(2)
                            try:
                                ci = client.close_order_info(close_oid)
                                pos_list = ci.get("positions") or []
                                if pos_list and pos_list[0].get("rate") is not None:
                                    exit_rate = float(pos_list[0]["rate"])
                                    break
                            except Exception:
                                continue
                        direction = 1 if txn == "buy" else -1
                        pnl = (exit_rate - float(o["trigger"])) * float(o.get("units", 0)) * direction if exit_rate else 0.0
                        results.append({"side": side, "pnl": pnl, "r": pnl / float(o.get("risk_amount", risk_amount)),
                                        "symbol": o.get("symbol", ""),
                                        "open_rate": float(o["trigger"]),
                                        "exit_rate": exit_rate or 0.0,
                                        "reason": "closed at market"})
                        print(f"{side} position {pid}: closed, PnL {pnl:+.2f}")
                    except Exception as e:
                        results.append({"side": side, "pnl": 0.0, "r": 0.0,
                                        "error": f"close failed: {e}"})
                        print(f"{side} position {pid}: close FAILED — {e}")
                handled = True
        if not handled:
            results.append({"side": side, "pnl": 0.0, "r": 0.0, "error": "unknown status — check app"})

    if profile and kept:
        # rewrite state files with remaining orders only
        for sp, s in raw_states.items():
            remaining = [o for o in s.get("orders", []) if o in kept]
            if remaining:
                s["orders"] = remaining
                json.dump(s, open(sp, "w"), indent=2)
            else:
                if s.get("orders"):
                    os.remove(sp)
        print(f"Left {len(kept)} {profile}-unrelated order(s) in state for next session")
    else:
        for sp in (state_path, reclaim_path):
            if os.path.exists(sp):
                os.remove(sp)

    trades = [r for r in results if not r.get("no_fill") and not r.get("error")]
    no_fills = [r for r in results if r.get("no_fill")]
    errors = [r for r in results if r.get("error")]
    total_pnl = sum(t["pnl"] for t in trades)
    total_r = sum(t["r"] for t in trades)

    hook = load_webhook()
    if not hook:
        return
    now = pd.Timestamp.now(tz="UTC")
    seg = (f"GOLD.24-7 · eToro {os.environ.get('ETORO_MODE', 'demo')} · "
           f"risk {risk_amount:,.0f} USD/trade · {describe_schedule(now)}")
    if trades:
        win = total_pnl > 0
        title = f"SESSION RESULT — {'WIN' if win else 'LOSS'}  {total_pnl:+,.2f} USD  ({total_r:+.2f}R)"
        fields = []
        for t in trades:
            line = (f"Entry {t.get('open_rate', 0):,.2f} → Exit {t.get('exit_rate', 0):,.2f}\n"
                    f"P&L **{t['pnl']:+,.2f} USD** ({t['r']:+.2f}R) · {t.get('reason', '')}")
            if t.get("open_ts") and t.get("close_ts"):
                line += f"\n{t['open_ts'][11:16]} → {t['close_ts'][11:16]} UTC"
            fields.append({"name": f"{t.get('symbol', '')} {t['side']}", "value": line, "inline": False})
        for n in no_fills:
            fields.append({"name": f"{n.get('symbol', '')} {n['side']} order",
                           "value": "Never triggered — cancelled, no loss", "inline": False})
        for e in errors:
            fields.append({"name": "Attention", "value": e["error"], "inline": False})
        send(hook, {"title": title, "color": 0x00C853 if win else 0xFF1744,
                    "description": "**Asia Grab session closed.**",
                    "fields": fields, "footer": {"text": seg}})
    else:
        send(hook, {"title": "SESSION RESULT — NO TRADE", "color": GRAY,
                    "description": "Price never swept either level. Orders cancelled, capital untouched.",
                    "footer": {"text": seg}})


def run_ig(dry):
    epic = os.environ.get("IG_EPIC", "CS.D.USGLD.CFD.IP")

    if dry:
        print("DRY RUN — would cancel unfilled working orders and close open positions on", epic)
        print(f"DRYRUN ORDER cancel/close {epic}")
        return

    from ig_client import IGClient
    ig = IGClient()
    ig.login()

    cancelled = []
    wo = ig.working_orders().get("workingOrders", [])
    for w in wo:
        if w["marketData"]["epic"] == epic:
            deal_id = w["workingOrderData"]["dealId"]
            ig.delete_working_order(deal_id)
            cancelled.append(deal_id)
            print(f"Cancelled working order {deal_id}")

    closed = []
    pos = ig.positions().get("positions", [])
    for pt in pos:
        m = pt["market"]
        if m["epic"] == epic:
            d = pt["position"]
            direction = "SELL" if d["direction"] == "BUY" else "BUY"
            r = ig.close_position(d["dealId"], epic, direction, abs(float(d["size"])))
            closed.append((d["dealId"], d["direction"], d["size"]))
            print(f"Closed {d['direction']} {d['size']} -> {r}")

    hook = load_webhook()
    if hook:
        send(hook, {"title": "Session end — cleanup", "color": BLUE if (cancelled or closed) else GRAY,
                    "description": f"Cancelled {len(cancelled)} order(s), closed {len(closed)} position(s) on {epic}."})


if __name__ == "__main__":
    main()
