import argparse
import os

from config import load_env
from notify import load_webhook, send
GRAY, BLUE = 0x95A5A6, 0x3498DB


def main():
    p = argparse.ArgumentParser(description="Cancel unfilled orders and close open positions (session end)")
    p.add_argument("--broker", choices=["ig", "etoro"], default=os.environ.get("BROKER", "ig"))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    load_env()
    if args.broker == "etoro":
        return run_etoro(args.dry_run)
    return run_ig(args.dry_run)


def run_etoro(dry):
    import json
    import time
    from datetime import date, timedelta
    from etoro_client import EtoroClient

    state_path = "results/etoro_session.json"
    if not os.path.exists(state_path):
        print("No eToro session state found — nothing to clean up")
        return
    with open(state_path) as f:
        state = json.load(f)
    orders = state["orders"]
    risk_amount = float(state.get("risk_amount") or orders[0].get("risk_amount") or 100.0)

    if dry:
        print(f"DRY RUN — would check {len(orders)} eToro order(s): "
              + ", ".join(str(o["order_id"]) for o in orders))
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

    os.remove(state_path)

    trades = [r for r in results if not r.get("no_fill") and not r.get("error")]
    no_fills = [r for r in results if r.get("no_fill")]
    errors = [r for r in results if r.get("error")]
    total_pnl = sum(t["pnl"] for t in trades)
    total_r = sum(t["r"] for t in trades)

    hook = load_webhook()
    if not hook:
        return
    seg = f"GOLD.24-7 · eToro {os.environ.get('ETORO_MODE', 'demo')} · risk {risk_amount:,.0f} USD/trade"
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
