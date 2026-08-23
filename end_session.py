import argparse
import os

from config import load_env
from notify import load_webhook, send
GRAY, BLUE = 0x95A5A6, 0x3498DB


def main():
    p = argparse.ArgumentParser(description="Cancel unfilled orders and close open positions (session end)")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    load_env()
    epic = os.environ.get("IG_EPIC", "CS.D.USGLD.CFD.IP")

    if args.dry_run:
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
