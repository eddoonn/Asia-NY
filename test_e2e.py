import os
import time
import uuid

import requests

from config import load_env
from etoro_client import EtoroClient
from notify import load_webhook, send

load_env()
c = EtoroClient(mode=os.environ.get("ETORO_MODE", "demo"))
BASE_URL = "https://public-api.etoro.com"
seg = c._seg()
INSTRUMENT_ID = 559
RISK = 100.0

print("1. OPEN — market buy $1,000 GOLD with SL/TP attached")
h = dict(c.headers)
h["x-request-id"] = str(uuid.uuid4())
payload = {"action": "open", "transaction": "buy", "instrumentId": INSTRUMENT_ID,
           "orderType": "mkt", "leverage": 1, "amount": 1000,
           "orderCurrency": "usd", "stopLossRate": 4595.0, "takeProfitRate": 4750.0}
r = requests.post(f"{BASE_URL}/api/v2/trading/execution/{seg}/orders",
                  headers=h, json=payload, timeout=20)
print("  ", r.status_code, r.text[:120])
oid = r.json()["orderId"]
ref = h["x-request-id"]
time.sleep(4)

print("2. VERIFY FILL")
d = c.lookup(oid, ref)
st = d["status"]
pos = (d.get("positionExecutions") or [{}])[0]
pid = pos.get("positionId")
open_rate = (pos.get("openingData") or {}).get("avgPrice")
units = (pos.get("openingData") or {}).get("units")
print(f"   status {st['id']} {st['name']} | position {pid} | filled {units} oz @ {open_rate}")

print("3. CLOSE position", pid)
cr = c.close_position(pid, INSTRUMENT_ID)
close_oid = cr["orderForClose"]["orderID"]
print("   close order", close_oid)

print("4. GET EXIT PRICE + P&L")
exit_rate = None
for _ in range(10):
    time.sleep(2)
    try:
        ci = c.close_order_info(close_oid)
        plist = ci.get("positions") or []
        if plist and plist[0].get("rate") is not None:
            exit_rate = float(plist[0]["rate"])
            break
    except Exception:
        continue
pnl = (exit_rate - float(open_rate)) * float(units)
print(f"   exit {exit_rate} | P&L {pnl:+.2f} USD ({pnl / RISK:+.3f}R)")

print("5. CROSS-CHECK vs trading history (what end_session.py reads)")
net = None
for _ in range(5):
    time.sleep(2)
    hist = c.history(time.strftime("%Y-%m-%d"))
    match = [t for t in hist if t.get("positionId") == pid]
    if match:
        net = float(match[0]["netProfit"])
        print(f"   history netProfit: {net:+.2f} USD | closeRate {match[0]['closeRate']}")
        break
if net is None:
    print("   history not populated yet (eToro lag) — close-order P&L stands")

win = pnl > 0
hook = load_webhook()
if hook:
    send(hook, {
        "title": f"TEST TRADE — {'WIN' if win else 'LOSS'}  {pnl:+,.2f} USD  ({pnl / RISK:+.2f}R)",
        "color": 0x00C853 if win else 0xFF1744,
        "description": "**End-to-end pipeline test** (opened and closed by the bot just now)",
        "fields": [
            {"name": "LONG trade", "value": (f"Entry {float(open_rate):,.2f} → Exit {exit_rate:,.2f}\n"
                                             f"P&L **{pnl:+,.2f} USD** ({pnl / RISK:+.2f}R) · closed at market"),
             "inline": False},
        ],
        "footer": {"text": f"GOLD.24-7 · eToro demo · risk {RISK:,.0f} USD/trade"}})
print("6. Discord result message sent")
