import argparse
import json
import os
import urllib.request

import pandas as pd

from backtest import load_data
from strategy import add_atr, asia_day_ids, ny_levels, day_ids, find_trades

ASIA = (22, 10)
NY_LATE = (19, 21)
BUF = 1.0
ATR_MULT = 1.0
ATR_LEN = 10
RR = 0.75
EXIT_HOUR = 8
COST = 0.3

GREEN, RED, BLUE, GRAY = 0x2ECC71, 0xE74C3C, 0x3498DB, 0x95A5A6


def load_webhook(path=".env"):
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("DISCORD_WEBHOOK="):
                    return line.split("=", 1)[1]
    return os.environ.get("DISCORD_WEBHOOK")


def send(webhook, embed, content=None):
    payload = {"embeds": [embed]}
    if content:
        payload["content"] = content
    req = urllib.request.Request(
        webhook, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "User-Agent": "AsiaGrabBot/1.0 (backtest notifier)"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.status


def current_asia_day_id(index):
    last = index[-1]
    did = day_ids(index)[-1]
    if last.hour >= ASIA[0]:
        did += 1
    return did


def current_session_start(now):
    today = now.floor("D")
    start_today = today + pd.Timedelta(hours=ASIA[0])
    if now >= start_today:
        return start_today
    return today - pd.Timedelta(days=1) + pd.Timedelta(hours=ASIA[0])


def latest_session_state(df):
    index = df.index
    atr = df["atr"].values
    mask, aid = asia_day_ids(index, ASIA)
    levels = ny_levels(index, df["High"].values, df["Low"].values, NY_LATE)
    trades = find_trades(df, mask, aid, levels, RR, ATR_MULT, BUF, "stop", "rr",
                         EXIT_HOUR, atr=atr, cost=COST)

    now = pd.Timestamp.now(tz="UTC")
    cur_id = current_asia_day_id(index)
    ref = None
    for p in range(cur_id - 1, cur_id - 8, -1):
        if p in levels:
            ref = levels[p]
            break

    session_trades = [t for t in trades if t["entry_time"] >= current_session_start(now)]
    return ref, trades, session_trades, now


def build_embed(df, ref, trades, session_trades, now, symbol):
    last_px = float(df["Close"].iloc[-1])

    if session_trades:
        t = session_trades[-1]
        if t["exit_time"] <= now:
            color = GREEN if t["r"] > 0 else RED
            status = f"Closed {t['r']:+.2f}R ({t['reason']})"
        else:
            color = BLUE
            status = "Open"
        return {
            "title": f"Asia Grab — {symbol}",
            "color": color,
            "fields": [
                {"name": "Setup", "value": ("SHORT — NY high swept" if t["side"] == "short" else "LONG — NY low swept"), "inline": True},
                {"name": "Status", "value": status, "inline": True},
                {"name": "Entry", "value": f"{t['entry']:.2f}", "inline": True},
                {"name": "Stop", "value": f"{t['sl']:.2f}", "inline": True},
                {"name": "Target", "value": f"{t['tp']:.2f}", "inline": True},
                {"name": "Entry time (UTC)", "value": str(t["entry_time"]), "inline": False},
            ],
            "footer": {"text": f"buf {BUF}xATR{ATR_LEN} | TP {RR}R | flat by {EXIT_HOUR}:00 UTC | last {last_px:.2f}"},
        }

    if ref is None:
        return {"title": f"Asia Grab — {symbol}", "color": GRAY,
                "description": "No reference levels available yet."}

    return {
        "title": f"Asia Grab — {symbol} armed",
        "color": GRAY,
        "fields": [
            {"name": "SHORT trigger (sweep above)", "value": f"{ref[0] + BUF * float(df['atr'].iloc[-1]):.2f}", "inline": True},
            {"name": "LONG trigger (sweep below)", "value": f"{ref[1] - BUF * float(df['atr'].iloc[-1]):.2f}", "inline": True},
            {"name": "NY-late high / low", "value": f"{ref[0]:.2f} / {ref[1]:.2f}", "inline": True},
            {"name": "Last price", "value": f"{last_px:.2f}", "inline": True},
            {"name": "Session", "value": "22:00–10:00 UTC · flat by 08:00 UTC", "inline": False},
        ],
        "footer": {"text": "Waiting for liquidity sweep — one trade per session"},
    }


def main():
    p = argparse.ArgumentParser(description="Send daily Asia-grab signal to Discord")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--test", action="store_true")
    args = p.parse_args()

    webhook = load_webhook()
    if not webhook:
        raise SystemExit("No webhook found. Put DISCORD_WEBHOOK=... in .env")

    if args.test:
        status = send(webhook, {"title": "Asia Grab online", "color": GREEN,
                                "description": "Webhook test — daily signals will land here."})
        print(f"Test sent, HTTP {status}")
        return

    df = load_data(args.symbol, "12d", "60m")
    add_atr(df, ATR_LEN)
    ref, trades, session_trades, now = latest_session_state(df)
    embed = build_embed(df, ref, trades, session_trades, now, args.symbol)
    status = send(webhook, embed)
    print(f"Signal sent, HTTP {status}")


if __name__ == "__main__":
    main()
