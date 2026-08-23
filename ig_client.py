import os

import requests


class IGClient:
    def __init__(self):
        env = os.environ.get("IG_ACCOUNT_TYPE", "demo").lower()
        self.base = "https://demo-api.ig.com" if env == "demo" else "https://api.ig.com"
        self.api_key = os.environ["IG_API_KEY"]
        self.username = os.environ["IG_USERNAME"]
        self.password = os.environ["IG_PASSWORD"]
        self.account_id = os.environ.get("IG_ACCOUNT_ID", "")
        self.headers = None

    def login(self):
        r = requests.post(
            f"{self.base}/gateway/deal/session",
            headers={"Content-Type": "application/json; charset=UTF-8",
                     "Accept": "application/json; charset=UTF-8",
                     "X-IG-API-KEY": self.api_key, "Version": "2"},
            json={"identifier": self.username, "password": self.password},
            timeout=20)
        r.raise_for_status()
        h = {"CST": r.headers["CST"],
             "X-SECURITY-TOKEN": r.headers["X-SECURITY-TOKEN"],
             "X-IG-API-KEY": self.api_key,
             "Accept": "application/json; charset=UTF-8",
             "Content-Type": "application/json; charset=UTF-8"}
        if self.account_id:
            h["IG-ACCOUNT-ID"] = self.account_id
        self.headers = h
        return r.json()

    def _req(self, method, path, version, payload=None, params=None):
        h = dict(self.headers)
        h["Version"] = version
        r = requests.request(method, f"{self.base}{path}", headers=h,
                             json=payload, params=params, timeout=20)
        if r.status_code >= 400:
            raise RuntimeError(f"IG {method} {path} -> {r.status_code}: {r.text}")
        return r.json() if r.text else {}

    def search(self, term):
        return self._req("GET", "/gateway/deal/markets", "1", params={"searchTerm": term})

    def market(self, epic):
        return self._req("GET", f"/gateway/deal/markets/{epic}", "3")

    def working_orders(self):
        return self._req("GET", "/gateway/deal/workingorders", "2")

    def positions(self):
        return self._req("GET", "/gateway/deal/positions", "2")

    def create_working_order(self, epic, direction, size, level, stop_distance,
                             limit_distance, currency="GBP", expiry="DFB"):
        payload = {"epic": epic, "orderType": "STOP", "direction": direction,
                   "size": size, "level": round(level, 2),
                   "stopDistance": round(stop_distance, 2),
                   "limitDistance": round(limit_distance, 2),
                   "guaranteedStop": False, "timeInForce": "GTC",
                   "currencyCode": currency, "expiry": expiry, "forceOpen": False}
        return self._req("POST", "/gateway/deal/workingorders/otc", "2", payload)

    def delete_working_order(self, deal_id):
        return self._req("DELETE", f"/gateway/deal/workingorders/otc/{deal_id}", "2")

    def close_position(self, deal_id, epic, direction, size, expiry="DFB"):
        payload = {"dealId": deal_id, "epic": epic, "expiry": expiry,
                   "direction": direction, "size": size, "orderType": "MARKET"}
        return self._req("DELETE", "/gateway/deal/positions/otc", "1", payload)
