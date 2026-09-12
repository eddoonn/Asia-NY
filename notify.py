"""Asia Grab - daily Discord signal for GC=F (gold).

Session model - the Globex trading day, not a fixed UTC hour
-----------------------------------------------------------
Gold trades Sunday 17:00 CT through Friday 16:00 CT, so the exchange reopens at
17:00 CT every day and the Asia Grab session is the first half of that day: open
with the market, run twelve hours, flat well before the daytime half.  The
window therefore follows DST on its own - 22:00-10:00 UTC in summer, 23:00-11:00
UTC in winter - which is why a hard-coded 22:00 UTC start armed an hour before
the market was open for six months of the year.  See `market_calendar`.

Only one clock is consulted: `now`.  The session a message is about is the one
the calendar says contains `now`, never the session of the last available bar,
so a lagging feed cannot make an old session look current.

The reference level is the NY-late (19:00-21:00 UTC) high/low that the strategy
itself would arm from: the most recent window that closed before the session's
first bar, searching back up to seven sessions.  That mirrors
`strategy.find_trades` exactly (`lv[2] < first_pos`), so a Sunday-opening
session legitimately arms from Friday, and the alert and the strategy can never
disagree about the triggers.

Market closures come from `market_calendar`, not from the feed.  A quiet feed
on a Saturday is the Globex weekly close, not a broken connection, and the two
are reported differently.

The trigger alert
-----------------
The daily runs say what is *armed*; nothing said what *fired*.  `--trigger` posts
a second message the moment the session's sweep appears on the tape - side,
entry, stop, target, the level it swept and how long is left before the flatten -
and stays silent whenever no new trade has appeared, so it can run on the hourly
monitor schedule.  It is keyed on `(session, side, entry bar, entry price)`, so a
second entry in the same session (a re-run with a moved ATR) posts in its own
right instead of being swallowed as a duplicate.

Unlike the armed message, the trigger does not wait for a current feed: once the
sweep has printed, the entry is a fact about the tape and stays true however the
feed behaves afterwards.  The message notes the lag when there is one.

Because it only speaks when something has filled, the alert can go days without
being seen - and it cannot be checked on demand, since a sweep cannot be asked
for.  `--trigger --test` posts the same message built from the most recent entry
the re-derivation found, marked `(TEST)`, so the layout can be verified against
the live channel at any hour.  It reads nothing from the state file and writes
nothing to it, so a test can never consume the real trigger's once-per-trade
slot.
"""
import argparse
import json
import os
import urllib.request

import numpy as np
import pandas as pd

from backtest import load_data
from market_calendar import (SESSION_HOURS, SESSION_OPEN_HOUR,
                             closure as market_closure, next_session_open,
                             session_exit_hour, session_window, session_windows)
import discord_style as style
from strategy import add_atr, day_ids, find_trades, ny_levels

# The session window itself (22:00-10:00 UTC in summer, 23:00-11:00 in winter)
# comes from `market_calendar`; only the NY-late reference window and the
# strategy parameters live here.
NY_LATE = (19, 21)
BUF = 1.0
ATR_MULT = 1.0
ATR_LEN = 10
RR = 0.75
# The flatten is the session-relative 03:00 CT by default - 08:00 UTC on CDT,
# 09:00 on CST - so the flat stays two hours before the close all year.  This is
# the fixed-UTC alternative, kept for A/B runs and for reproducing the historic
# alerts; `end_session.py` reads the same calendar rule.
EXIT_HOUR = 8
COST = 0.3

# How many sessions back `strategy.find_trades` will look for a NY-late window.
REF_LOOKBACK = 7
MAX_BAR_LAG_HOURS = 2.5
STATE_PATH = os.path.join("results", "notify_state.json")

# State key for the trade-triggered alert.  It is separate from `session` so the
# once-a-day armed message and the trigger message cannot clobber each other.
TRIGGER_STATE = "triggered"

GREEN, RED, BLUE, GRAY = 0x2ECC71, 0xE74C3C, 0x3498DB, 0x95A5A6

# The two vocabularies, and the only place they meet.  `GC=F` is the futures
# feed the levels are read from; `GOLD.24-7` is what the broker lists, and a
# reader acting on a message needs the ticker their broker shows.  The eToro
# list comes from the same env var `place_orders.py` orders from, so altering
# the broker's ticker for gold alters the messages with it.
SIGNAL_OF_TICKER = {"GOLD.24-7": "GC=F", "GOLD": "GC=F"}
DEFAULT_ETORO_SYMBOLS = "GOLD.24-7,EURUSD,GBPUSD,USDJPY"


def etoro_symbols():
    """The instruments the eToro path orders, in its own order."""
    return [s.strip() for s in os.environ.get(
        "ETORO_SYMBOLS", DEFAULT_ETORO_SYMBOLS).split(",") if s.strip()]


def signal_symbol_for(broker_symbol):
    """The data symbol a broker ticker is traded from - `GOLD.24-7` -> `GC=F`.

    Lives here rather than in `place_orders.py` because the messages need the
    mapping in both directions and `place_orders` already imports this module;
    two copies would be two answers to "what is gold called".
    """
    if broker_symbol in SIGNAL_OF_TICKER:
        return SIGNAL_OF_TICKER[broker_symbol]
    s = broker_symbol.upper()
    if len(s) == 6 and s.isalpha() and "=" not in s:
        return s + "=X"
    return broker_symbol


def broker_ticker(signal):
    """What `signal` is traded as, or None when it is not in the traded list.

    An FX leg is its own ticker (`EURUSD` trades as `EURUSD`), and a signal
    nobody orders - a test run on another instrument - has no ticker to name.
    """
    for sym in etoro_symbols():
        if signal_symbol_for(sym) == signal:
            return sym
    return None


def traded_as(signal):
    """`GC=F → GOLD.24-7` - the feed, then what it is actually traded as.

    Both halves matter: the levels come from the futures symbol, the order goes
    to the broker ticker.  A signal that trades under its own name is quoted
    once, so an FX message does not read `EURUSD → EURUSD`.
    """
    ticker = broker_ticker(signal)
    if ticker and ticker != signal:
        return f"{signal} → {ticker}"
    return signal


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


def session_day_id(window):
    """Epoch-day id of a session's trade date, numbered as `strategy.asia_day_ids`.

    A session opens in the UTC evening, so its opening day id plus one is the
    day its daytime half falls on - the number `find_trades` keys its NY-late
    lookup off, so the notifier has to use the same numbering.
    """
    return int(day_ids(pd.DatetimeIndex([window[0]]))[0]) + 1


def session_ids(index):
    """(mask, ids) grouping bars into Globex Asia sessions.

    Drop-in for `strategy.asia_day_ids`: `ids` holds each session's trade-date
    day id, and -1 for bars outside every window (the idle half of the Globex
    day, the weekend and holidays).  The boundaries come from the calendar, so
    they move with DST instead of assuming a 22:00 UTC open.
    """
    index = pd.DatetimeIndex(index)
    mask = np.zeros(len(index), dtype=bool)
    ids = np.full(len(index), -1, dtype=np.int64)
    if len(index) == 0:
        return mask, ids
    for window in session_windows(index[0] - pd.Timedelta(days=2),
                                  index[-1] + pd.Timedelta(days=2)):
        sel = (index >= window[0]) & (index < window[1])
        if not sel.any():
            continue
        mask |= sel
        ids[sel] = session_day_id(window)
    return mask, ids


def reference_levels(levels, index, window):
    """NY-late high/low the strategy arms this session from, or None.

    Mirrors `strategy.find_trades`: walk back from the session's own day id and
    take the first NY-late window that closed before the session's first bar.
    Requiring `lv[2] < first_pos` is what keeps a session from arming off a
    window that has not finished yet - and it makes the weekend case honest,
    because a Sunday-opening session really does arm from Friday, the last
    NY-late window before the weekly close.
    """
    first_pos = int(index.searchsorted(window[0]))
    did = session_day_id(window)
    for p in range(did - 1, did - 1 - REF_LOOKBACK, -1):
        lv = levels.get(p)
        if lv is not None and lv[2] < first_pos:
            return lv
    return None


def data_lag_hours(index, now):
    return float((now - index[-1]) / pd.Timedelta(hours=1))


def session_state(df, now=None, max_lag_hours=MAX_BAR_LAG_HOURS, exit_hour=None):
    """Resolve the session that `now` belongs to.

    `window` is decided by the calendar alone - never by the last data bar - and
    `ref`/`trade` are only populated when that session is running and the feed is
    current, so a stale level set can never be re-broadcast.

    `exit_hour` defaults to the session-relative flatten, so the re-derivation
    and the live system agree in winter as well as summer.
    """
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    exit_hour = session_exit_hour(now) if exit_hour is None else exit_hour
    index = df.index
    atr = df["atr"].values
    mask, aid = session_ids(index)
    levels = ny_levels(index, df["High"].values, df["Low"].values, NY_LATE)
    trades = find_trades(df, mask, aid, levels, RR, ATR_MULT, BUF, "stop", "rr",
                         exit_hour, atr=atr, cost=COST)

    lag = data_lag_hours(index, now)
    window = session_window(now)
    state = {
        "now": now,
        "window": window,
        "market": market_closure(now),
        "ref": None,
        "trade": None,
        "lag_hours": lag,
        "stale": lag > max_lag_hours,
        "exit_hour": exit_hour,
        "trades": trades,
        "levels": levels,
    }
    if window is not None and not market_blocks(state) and not state["stale"]:
        start, end = window
        state["ref"] = reference_levels(levels, index, window)
        found = [t for t in trades if start <= t["entry_time"] < end]
        state["trade"] = found[-1] if found else None
    return state


def market_blocks(state):
    """True when a closure - not the feed - is why nothing is tradeable.

    The daily maintenance break is deliberately not blocking: it is a one-hour
    gap inside the 12-hour session, and in winter the scheduled 22:10 UTC run
    lands inside it.  It is reported as a note instead.
    """
    found = state["market"]
    return found is not None and found.kind in ("weekly-close", "holiday")


def market_note(state):
    found = state["market"]
    if found is None or found.kind != "daily-break":
        return None
    return f"Globex maintenance break until {found.until:%H:%M} UTC"


def session_phase(state):
    """One of: market-closed, idle, stale, no-ref, armed, open, closed."""
    if market_blocks(state):
        return "market-closed"
    if state["window"] is None:
        return "idle"
    if state["stale"]:
        return "stale"
    if state["ref"] is None:
        return "no-ref"
    trade = state["trade"]
    if trade is None:
        return "armed"
    return "closed" if trade["exit_time"] <= state["now"] else "open"


def state_key(state):
    """Identity of what this message is about - stable across price ticks."""
    parts = [session_phase(state)]
    if market_blocks(state):
        parts.append(f"reopens={state['market'].until.isoformat()}")
    if state["window"] is not None:
        parts.append(f"session={state['window'][0].isoformat()}")
    if state["ref"] is not None:
        parts.append(f"ref={state['ref'][0]:.2f}/{state['ref'][1]:.2f}")
    trade = state["trade"]
    if trade is not None:
        parts.append(f"trade={trade['side']}@{trade['entry_time'].isoformat()}")
    return "|".join(parts)


def build_embed(df, state, symbol):
    """The daily message: the session as the clock finds it."""
    last_px = float(df["Close"].iloc[-1])
    last_bar = df.index[-1]
    phase = session_phase(state)
    note = market_note(state)

    if phase == "market-closed":
        found = state["market"]
        return {
            "title": style.title(traded_as(symbol), "MARKET CLOSED"),
            "color": GRAY,
            "description": (f"{found.name} · reopens "
                            f"{found.until:%a %Y-%m-%d %H:%M} UTC"),
        }

    if phase == "stale":
        return {
            "title": style.title(traded_as(symbol), "FEED STALE"),
            "color": GRAY,
            "description": (f"Last bar {last_bar:%Y-%m-%d %H:%M} UTC, "
                            f"{state['lag_hours']:.1f}h behind.\n"
                            "Not arming from a stale level set."),
        }

    if phase == "idle":
        nxt = next_session_open(state["now"])
        description = (f"The Asia window is the first {SESSION_HOURS}h of the "
                       f"Globex day, from {SESSION_OPEN_HOUR:02d}:00 CT.\n"
                       f"Next session opens {nxt:%Y-%m-%d %H:%M} UTC")
        if note:
            description += f"\n{note}"
        return {
            "title": style.title(traded_as(symbol), "NO SESSION"),
            "color": GRAY,
            "description": description,
        }

    start, end = state["window"]

    if phase == "no-ref":
        return {
            "title": style.title(traded_as(symbol), "NOT ARMED"),
            "color": GRAY,
            "description": (f"No NY-late ({NY_LATE[0]:02d}:00-{NY_LATE[1]:02d}:00 UTC) "
                            f"range for the {start:%Y-%m-%d} session.\n"
                            f"It runs to {end:%H:%M} UTC with nothing to sweep."),
        }

    trade = state["trade"]

    if trade is not None:
        return trade_embed(state, trade, symbol, last_px=last_px)

    ref = state["ref"]
    atr_now = float(df["atr"].iloc[-1])
    return {
        "title": style.title(traded_as(symbol), "ARMED"),
        "color": GRAY,
        "description": ("One trade this session: the first sweep of the "
                        "NY-late range fills it."),
        "fields": [
            {"name": "SHORT if above",
             "value": f"{ref[0] + BUF * atr_now:.2f}", "inline": True},
            {"name": "LONG if below",
             "value": f"{ref[1] - BUF * atr_now:.2f}", "inline": True},
            {"name": "NY-late range",
             "value": f"{ref[0]:.2f} / {ref[1]:.2f}", "inline": True},
            {"name": "Last price", "value": f"{last_px:.2f}", "inline": True},
            {"name": "Session", "inline": False,
             "value": style.rule(f"{start:%Y-%m-%d}",
                                 f"{start:%H:%M}-{end:%H:%M} UTC",
                                 f"flat {state['exit_hour']:02d}:00 UTC")},
        ],
        "footer": {"text": style.rule(f"tape to {last_bar:%H:%M} UTC", note)},
    }


def session_signal(state):
    """The session's trade from the re-derivation, staleness and phase aside.

    `state["trade"]` is deliberately withheld while the feed lags so a stale
    level set can never be re-broadcast.  A trigger is not a level set: once the
    sweep has printed, the entry is on the tape and stays true however the feed
    behaves afterwards.  So the trigger path reads `state["trades"]` directly.
    """
    window = state["window"]
    if window is None:
        return None
    start, end = window
    found = [t for t in state["trades"] if start <= t["entry_time"] < end]
    return found[-1] if found else None


def trigger_key(state, trade):
    """Identity of a trigger: the session, the side and the entry bar/price.

    Stable across price ticks and across the fill/no-fill distinction, but a
    different entry - a second run, or the other side - is a different key, so
    it posts in its own right.
    """
    return (f"{state['window'][0].isoformat()}|{trade['side']}"
            f"|{trade['entry_time'].isoformat()}|{trade['entry']:.2f}")


def trade_is_live(trade):
    """True when the re-derivation ran out of bars without resolving the exit.

    `find_trades` always fills an exit in: `sl`, `tp`, the `time` flatten, or
    `eod` at the last available bar.  Only `eod` means it never got there, which
    is the signature of a position that is still on.
    """
    return trade["reason"] == "eod"


def trade_embed(state, trade, symbol, last_px=None, alert=False, test=False):
    """The one trade message.

    The entry alert, the daily result and the channel test are the same builder
    on purpose: a trade read at 06:00 and again at 08:10 has to look like one
    event, not two.  `alert` only picks the state word - `ENTRY` as it fills,
    otherwise `OPEN` or the outcome - because the title is all a phone shows.

    The level is derived from the trade (`entry -/+ 1xATR10`) rather than looked
    up in the session's reference, so the line always adds up and the message
    never depends on the feed still holding the arming window.
    """
    live = trade_is_live(trade)
    if live:
        word = "ENTRY" if alert else "OPEN"
        color = BLUE
    else:
        word = (f"CLOSED {trade['r']:+.2f}R "
                f"({style.reason_words(trade['reason'])})")
        color = GREEN if trade["r"] > 0 else RED

    level, _atr = style.trade_level(trade)
    window = state["window"]
    deadline = f"Flat {state['exit_hour']:02d}:00 UTC"
    if live and window is not None and not test:
        flat = state["now"].normalize() + pd.Timedelta(hours=state["exit_hour"])
        if state["now"].hour >= state["exit_hour"]:
            flat += pd.Timedelta(days=1)
        left = flat - state["now"]
        if pd.Timedelta(0) < left < pd.Timedelta(hours=14):
            deadline += (f" ({int(left.total_seconds() // 3600)}h"
                         f"{int(left.total_seconds() % 3600 // 60):02d}m left)")

    foot = []
    if window is not None:
        foot.append(f"session {window[0]:%Y-%m-%d}")
    foot.append(style.stamp(trade["entry_time"]))
    if last_px is not None:
        foot.append(f"last {last_px:.2f}")
    if state["stale"] and not test:
        # A test is sent out of hours by design, so the lag line would be an
        # artefact of when it was asked for, not a property of the trade.
        foot.append(f"feed {state['lag_hours']:.1f}h behind")
    if test:
        foot.append("TEST - sample, not a fill")

    return {
        "title": style.title(
            traded_as(symbol),
            f"{trade['side'].upper()} {word}" + (" (TEST)" if test else "")),
        "color": color,
        "description": style.swept_line(trade, level),
        "fields": [
            {"name": "Entry", "value": f"{trade['entry']:.2f}", "inline": True},
            {"name": "Stop", "value": f"{trade['sl']:.2f}", "inline": True},
            {"name": "Target", "value": f"{trade['tp']:.2f}", "inline": True},
            {"name": "Session", "inline": False,
             "value": style.rule(deadline, f"TP {RR}R", "1 trade/session")},
        ],
        "footer": {"text": " · ".join(foot)},
    }


def entry_embed(state, trade, symbol="GC=F", last_px=None, test=False):
    """The entry alert: the trade message as it arrives, at the entry bar."""
    return trade_embed(state, trade, symbol, last_px=last_px, alert=True,
                       test=test)


def hello_embed():
    """The webhook handshake: what lands here, and when the night is."""
    return {
        "title": style.title("webhook test"),
        "color": GREEN,
        "description": ("Armed levels, entry alerts and results land here.\n"
                        "One message per session; the night is 22:00-10:00 UTC "
                        "(23:00-11:00 in winter)."),
    }


def trigger_payload(state, symbol="GC=F", last_px=None):
    """(key, embed) for "a trade just triggered", or (None, None).

    Called from the hourly monitor runs, so it must stay quiet whenever nothing
    new has happened: no session, no trade this session, or a shut book.
    """
    if state["window"] is None or market_blocks(state):
        return None, None
    trade = session_signal(state)
    if trade is None:
        return None, None
    return (trigger_key(state, trade),
            entry_embed(state, trade, symbol, last_px=last_px))


def sample_trigger_payload(state, symbol="GC=F", last_px=None):
    """(key, embed) for a sample entry alert, for testing the channel.

    Built from the most recent entry the re-derivation actually found, so every
    number is real tape and the layout is the live layout; only the `(TEST)`
    marker and the key are synthetic.  Works at any hour and on a closed market,
    because it describes a session that has already printed rather than one that
    is running - which is the point, since a trade may not sweep for days.
    """
    trades = state["trades"]
    if not trades:
        return None, None
    # An entry that is still on shows the message as it arrives at the entry
    # bar - the common case when this is run inside a session.
    still_on = [t for t in trades if trade_is_live(t)]
    trade = still_on[-1] if still_on else trades[-1]
    key = (f"test|{trade['side']}|{trade['entry_time'].isoformat()}"
           f"|{trade['entry']:.2f}")
    return key, entry_embed(state, trade, symbol, last_px=last_px, test=True)


def run_trigger(state, symbol, webhook, state_path=STATE_PATH, force=False,
                dry_run=False, last_px=None):
    """Send the trigger alert at most once per trade.

    Returns `(key, embed, sent)`.  `key` is None when no trade has triggered,
    `embed` is None when this trigger was already posted, and `sent` is False
    for a dry run so a caller can tell the two apart.
    """
    key, embed = trigger_payload(state, symbol, last_px)
    if key is None:
        return None, None, False
    previous = load_state(state_path)
    if not force and previous.get(TRIGGER_STATE) == key:
        return key, None, False
    if dry_run:
        return key, embed, False
    send(webhook, embed)
    # `trigger_session`, not `session`: the daily message owns that name, and the
    # whole point of the named-key state file is that one message cannot unsend
    # another one's record.
    remember(TRIGGER_STATE, key, state_path,
             triggered_at=state["now"].isoformat(),
             trigger_session=state["window"][0].isoformat())
    return key, embed, True


def load_state(path=STATE_PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(payload, path=STATE_PATH):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w") as f:
        json.dump(payload, f, indent=2)


def remember(name, value, path=STATE_PATH, **extra):
    """Record one named key without clobbering the others in the same file."""
    state = load_state(path)
    state[name] = value
    state.update(extra)
    save_state(state, path)
    return state


def main():
    p = argparse.ArgumentParser(description="Send daily Asia-grab signal to Discord")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--test", action="store_true",
                   help=("with --trigger: post a sample entry alert built from "
                         "the most recent real entry, labelled as a test and "
                         "without touching the state file; alone: the webhook "
                         "hello"))
    p.add_argument("--force", action="store_true",
                   help="post even if this exact session state was already posted")
    p.add_argument("--no-state", action="store_true",
                   help="ignore the state file and always post")
    p.add_argument("--state-path", default=STATE_PATH)
    p.add_argument("--max-lag-hours", type=float, default=MAX_BAR_LAG_HOURS)
    p.add_argument("--exit-hour", type=int, default=None,
                   help=("UTC hour the strategy flattens at (default: the "
                         "session-relative 03:00 CT, i.e. 08:00 UTC on CDT "
                         f"and 09:00 on CST; {EXIT_HOUR} pins the old fixed hour)"))
    p.add_argument("--digest", action="store_true",
                   help="send the weekly digest instead of the session state")
    p.add_argument("--trigger", action="store_true",
                   help=("post the trade-triggered alert if a sweep has filled "
                         "since the last run, and post nothing otherwise - safe "
                         "to run every hour through the session"))
    p.add_argument("--as-of", default=None,
                   help='reference time for --digest, e.g. "2026-09-12 09:00" (UTC)')
    p.add_argument("--dry-run", action="store_true",
                   help="render the message and print it without sending")
    args = p.parse_args()

    webhook = load_webhook()
    if not webhook and not args.dry_run:
        raise SystemExit("No webhook found. Put DISCORD_WEBHOOK=... in .env")

    if args.test and not args.trigger:
        status = send(webhook, hello_embed())
        print(f"Test sent, HTTP {status}")
        return

    if args.digest:
        from weekly_digest import run_digest
        return run_digest(args, webhook)

    df = load_data(args.symbol, "12d", "60m")
    add_atr(df, ATR_LEN)
    state = session_state(df, max_lag_hours=args.max_lag_hours,
                          exit_hour=args.exit_hour)

    if args.trigger and args.test:
        # The channel test: post the entry alert now, whatever the clock says.
        # Nothing is written, so it cannot consume the real trigger's slot.
        key, embed = sample_trigger_payload(
            state, args.symbol, last_px=float(df["Close"].iloc[-1]))
        if key is None:
            print("Trigger test: no entry in the loaded window to sample "
                  "- nothing sent")
            return
        if args.dry_run:
            print(f"DRY RUN (TEST) — {key}")
            print(json.dumps(embed, indent=2, default=str))
            return
        status = send(webhook, embed)
        print(f"Trigger test sent, HTTP {status} - {key}")
        return

    if args.trigger:
        key, embed, _sent = run_trigger(
            state, args.symbol, webhook, args.state_path, force=args.force,
            dry_run=args.dry_run, last_px=float(df["Close"].iloc[-1]))
        if key is None:
            # Say which of the quiet cases it is: this line is the hourly cron's
            # only output, and "no sweep yet" is success, not silence.
            if state["window"] is None:
                why = "no session running"
            elif market_blocks(state):
                why = f"market closed ({state['market'].name})"
            elif session_signal(state) is None:
                why = "armed, no sweep yet"
            else:
                why = "nothing to send"
            print(f"Trigger: {why} - nothing sent")
            return
        if embed is None:
            print(f"Trigger already posted ({key}) - skipping")
            return
        if args.dry_run:
            print(f"DRY RUN — {key}")
            print(json.dumps(embed, indent=2, default=str))
            return
        print(f"Trigger sent - {key}")
        return

    key = state_key(state)

    previous = {} if args.no_state else load_state(args.state_path)
    if not args.force and previous.get("session") == key:
        print(f"No change since last post ({key}) - skipping")
        return

    embed = build_embed(df, state, args.symbol)
    if args.dry_run:
        print(f"DRY RUN — {key}")
        print(json.dumps(embed, indent=2, default=str))
        return
    status = send(webhook, embed)
    print(f"Signal sent, HTTP {status} - {key}")

    if not args.no_state:
        remember("session", key, args.state_path,
                 posted_at=state["now"].isoformat(),
                 session=(state["window"][0].isoformat()
                          if state["window"] is not None else None))


if __name__ == "__main__":
    main()
