import os
import uuid

import requests

BASE = "https://public-api.etoro.com"


class EtoroClient:
    def __init__(self, mode=None):
        self.mode = (mode or os.environ.get("ETORO_MODE", "demo")).lower()
        self.headers = {
            "x-api-key": os.environ["ETORO_PUBLIC_KEY"],
            "x-user-key": os.environ["ETORO_USER_KEY"],
            "Content-Type": "application/json",
        }

    def _seg(self):
        return "demo" if self.mode == "demo" else "real"

    def _rid(self):
        return str(uuid.uuid4())

    def search(self, symbol):
        h = {**self.headers, "x-request-id": self._rid()}
        r = requests.get(f"{BASE}/api/v1/market-data/search", headers=h,
                         params={"internalSymbolFull": symbol}, timeout=20)
        r.raise_for_status()
        return r.json()

    def resolve(self, symbol):
        data = self.search(symbol)
        for item in data.get("items", []):
            if str(item.get("internalSymbolFull", "")).upper() == symbol.upper():
                return item
        raise SystemExit(f"Instrument {symbol!r} not found on eToro")

    def place_mit(self, instrument, transaction, trigger, sl, tp, units, leverage=1):
        payload = {"action": "open", "transaction": transaction,
                   "symbol": instrument.get("internalSymbolFull"),
                   "instrumentId": instrument["instrumentId"],
                   "orderType": "mit", "triggerRate": round(float(trigger), 2),
                   "leverage": leverage, "units": round(float(units), 4),
                   "orderCurrency": "usd",
                   "stopLossRate": round(float(sl), 2),
                   "takeProfitRate": round(float(tp), 2)}
        path = f"{BASE}/api/v2/trading/execution/{self._seg()}/orders"
        h = {**self.headers, "x-request-id": self._rid()}
        r = requests.post(path, headers=h, json=payload, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"eToro place order -> {r.status_code}: {r.text}")
        return r.json()

    def lookup(self, order_id, reference_id=None):
        h = {**self.headers, "x-request-id": reference_id or self._rid()}
        path = f"{BASE}/api/v2/trading/info/{self._seg()}/orders:lookup"
        r = requests.get(path, headers=h, params={"orderId": order_id}, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"eToro lookup {order_id} -> {r.status_code}: {r.text}")
        return r.json()

    def cancel_order(self, order_id):
        path = f"{BASE}/api/v2/trading/execution/{self._seg()}/orders/{order_id}"
        h = {**self.headers, "x-request-id": self._rid()}
        r = requests.delete(path, headers=h, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"eToro cancel {order_id} -> {r.status_code}: {r.text}")
        return r.json() if r.text else {}

    def close_position(self, position_id, instrument_id):
        path = f"{BASE}/api/v1/trading/execution/{self._seg()}/market-close-orders/positions/{position_id}"
        h = {**self.headers, "x-request-id": self._rid()}
        payload = {"InstrumentId": instrument_id, "UnitsToDeduct": None}
        r = requests.post(path, headers=h, json=payload, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"eToro close {position_id} -> {r.status_code}: {r.text}")
        return r.json()

    def portfolio(self):
        h = {**self.headers, "x-request-id": self._rid()}
        r = requests.get(f"{BASE}/api/v1/trading/info/{self._seg()}/portfolio", headers=h, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"eToro portfolio -> {r.status_code}: {r.text}")
        return r.json()

    def history(self, min_date):
        h = {**self.headers, "x-request-id": self._rid()}
        r = requests.get(f"{BASE}/api/v1/trading/info/trade/{self._seg()}/history",
                         headers=h, params={"minDate": min_date, "pageSize": 100}, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"eToro history -> {r.status_code}: {r.text}")
        return r.json()

    def close_order_info(self, order_id):
        h = {**self.headers, "x-request-id": self._rid()}
        r = requests.get(f"{BASE}/api/v1/trading/info/{self._seg()}/close-orders/{order_id}",
                         headers=h, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"eToro close-order info {order_id} -> {r.status_code}: {r.text}")
        return r.json()
