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
    from etoro_client import EtoroClient

    state_path = "results/etoro_session.json"
    if not os.path.exists(state_path):
        print("No eToro session state found — nothing to clean up")
        return
    with open(state_path) as f:
        state = json.load(f)
    if dry:
        print(f"DRY RUN — would check {len(state['orders'])} eToro order(s): "
              + ", ".join(str(o["order_id"]) for o in state["orders"]))
        return

    client = EtoroClient()
    cancelled, closed = [], []
    for o in state["orders"]:
        info = client.lookup(o["order_id"])
        body = info if isinstance(info, dict) else {}
        orders = body.get("orders") or [body]
        for od in orders:
            status = (od.get("status") or {}).get("id")
            if status in (1, 2, 11, 12):
                client.cancel_order(o["order_id"])
                cancelled.append(o["order_id"])
                print(f"Cancelled pending order {o['order_id']} (status {status})")
            elif status in (3, 5):
                for pe in od.get("positionExecutions") or []:
                    pid = pe.get("positionId")
                    client.close_position(pid, o["instrument_id"])
                    closed.append(pid)
                    print(f"Closed position {pid}")
            else:
                print(f"Order {o['order_id']} status {status} — nothing to do")

    os.remove(state_path)
    hook = load_webhook()
    if hook:
        send(hook, {"title": "Session end — eToro cleanup",
                    "color": BLUE if (cancelled or closed) else GRAY,
                    "description": f"Cancelled {len(cancelled)} order(s), closed {len(closed)} position(s)."})


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
