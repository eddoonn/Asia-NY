"""Before/after demonstration of the notify.py session-state fix.

Loads the original notify.py from a checkout and the fixed one side by side, then
runs both over synthetic frames that reproduce the reported faults.

    ASIA_CHECKOUT="/path/to/asia-gold-reversal" python before_after.py
"""
import importlib.util
import os

import pandas as pd

import end_session
import reclaim_monitor
from market_calendar import describe, session_exit_hour
from strategy import add_atr

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKOUT = os.environ.get("ASIA_CHECKOUT", ".")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OLD = load_module("notify_original", os.path.join(CHECKOUT, "notify.py"))
NEW = load_module("notify_fixed", os.path.join(HERE, "notify.py"))


def ts(text):
    return pd.Timestamp(text, tz="UTC")


def frame(rows):
    index = pd.DatetimeIndex([pd.Timestamp(r[0], tz="UTC") for r in rows])
    df = pd.DataFrame(
        {"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
         "Low": [r[3] for r in rows], "Close": [r[4] for r in rows]},
        index=index)
    return add_atr(df, 10)


def show_window(window):
    return "none" if window is None else f"{window[0]:%m-%d %H:%M}Z->{window[1]:%m-%d %H:%M}Z"


def old_window(now):
    """The original model: a fixed 22:00 UTC start, never None."""
    start = OLD.current_session_start(now)
    return start, start + pd.Timedelta(hours=12)


# The feed stops at Friday 20:00 CT and never resumes - mirroring 9/4 -> 9/8,
# where the armed block sat on "last 4476.60" and the same triggers repeated.
FROZEN = [
    ("2026-09-03 19:00", 94.0, 95.0, 89.0, 94.0),   # Thu NY-late -> 95.00 / 89.00
    ("2026-09-03 20:00", 93.5, 94.0, 90.0, 93.0),
    ("2026-09-03 22:00", 93.0, 94.0, 92.0, 93.0),
    ("2026-09-04 09:00", 89.5, 90.0, 89.0, 89.5),
    ("2026-09-04 19:00", 92.0, 92.5, 91.2, 92.0),   # Fri NY-late -> 92.50 / 91.20
    ("2026-09-04 20:00", 92.0, 92.4, 91.3, 92.0),   # last bar of the week
]

# A Sunday-opening session: its own opening day is shut, so the last usable
# NY-late window is Friday's.
SUNDAY = [
    ("2026-09-04 19:00", 94.0, 95.0, 89.0, 94.0),
    ("2026-09-04 20:00", 93.5, 94.0, 90.0, 93.0),
    ("2026-09-06 22:00", 93.0, 94.0, 92.0, 93.0),
    ("2026-09-06 23:00", 92.5, 93.0, 91.0, 92.0),
    ("2026-09-07 01:00", 90.5, 91.0, 84.0, 90.0),
    ("2026-09-07 02:00", 89.5, 90.0, 89.0, 89.5),
    ("2026-09-07 09:00", 89.0, 89.5, 88.5, 89.0),
]

# One session, two bars that would both sweep the low.
TWO_TRIGGERS = [
    ("2026-09-08 19:00", 94.0, 95.0, 89.0, 94.0),
    ("2026-09-08 20:00", 93.5, 94.0, 90.0, 93.0),
    ("2026-09-08 22:00", 93.0, 94.0, 92.0, 93.0),
    ("2026-09-08 23:00", 92.5, 93.0, 91.0, 92.0),
    ("2026-09-09 00:00", 91.5, 92.0, 90.0, 91.0),
    ("2026-09-09 01:00", 90.5, 91.0, 84.0, 90.0),
    ("2026-09-09 02:00", 89.5, 90.0, 89.0, 89.5),
    ("2026-09-09 03:00", 89.5, 90.0, 89.0, 89.5),
    ("2026-09-09 04:00", 88.5, 89.0, 84.0, 88.5),
    ("2026-09-09 05:00", 89.0, 89.5, 88.5, 89.0),
]

PROBES = [("Fri 9/4 23:00", "2026-09-04 23:00"),
          ("Sat 9/5 00:58", "2026-09-05 00:58"),
          ("Sun 9/6 00:49", "2026-09-06 00:49"),
          ("Mon 9/7 00:47", "2026-09-07 00:47"),
          ("Tue 9/8 01:15", "2026-09-08 01:15")]

WINDOW_PROBES = [
    ("Tue 9/8 22:10 CDT", "2026-09-08 22:10"),
    ("Wed 9/9 21:30 CDT", "2026-09-09 21:30"),
    ("Wed 12/9 22:10 CST", "2026-12-09 22:10"),
    ("Thu 12/10 00:30 CST", "2026-12-10 00:30"),
    ("Thu 12/10 10:30 CST", "2026-12-10 10:30"),
    ("Sun 11/1 22:30 DST ends", "2026-11-01 22:30"),
]


def show_freeze():
    print("=" * 100)
    print("1. FEED FREEZES - the armed triggers that stood still for three days")
    print("=" * 100)
    df = frame(FROZEN)
    print(f"last data bar: {df.index[-1]}   close {float(df['Close'].iloc[-1]):.2f}")
    print("The old notifier picks its reference from index[-1], which never advances,")
    print("while the fixed one asks the clock and the CME calendar.\n")
    for label, when in PROBES:
        now = ts(when)
        ref, trades, session_trades, old_now = OLD.latest_session_state(df)
        old_embed = OLD.build_embed(df, ref, trades, session_trades, old_now, "GC=F")
        armed = [f["value"] for f in old_embed.get("fields", [])
                 if f["name"].startswith(("SHORT", "LONG"))]
        state = NEW.session_state(df, now=now)
        print(f"  {label:14s} | market: {describe(now):<58s} | NEW {NEW.session_phase(state)}")
        print(f"                 | OLD {old_embed.get('title', '')[:26]:26s} triggers {armed}")
    print("\n  -> OLD re-broadcasts Thursday's levels on every run, weekend included.")
    print("     NEW names the weekly close up to Sunday 22:00 UTC. After that the two")
    print("     rules diverge by design: a quiet feed during a named closure is a")
    print("     closure, a quiet feed while the market is open is a feed fault.")
    print("     (Sunday/Monday 19:00-20:00 CT is the one window where the published")
    print("     sources disagree about the evening before a Monday holiday - see")
    print("     METALS_CLOSURES in market_calendar.py.)")


def show_session_window():
    print()
    print("=" * 100)
    print("2. SESSION WINDOW - a fixed 22:00 UTC start vs the Globex open (17:00 CT)")
    print("=" * 100)
    print("Metals reopen at 17:00 CT every day, which is 22:00 UTC on CDT and 23:00")
    print("UTC on CST. A fixed UTC hour is therefore an hour early in winter.\n")
    print(f"  {'probe':24s} {'OLD (fixed 22:00 UTC)':34s} NEW (Globex open)")
    for label, when in WINDOW_PROBES:
        now = ts(when)
        print(f"  {label:24s} {show_window(old_window(now)):34s} "
              f"{show_window(NEW.session_window(now))}")
    print("\n  -> In winter OLD opens at 22:00 UTC, which is 16:00 CT: the maintenance")
    print("     break, an hour before metals trade. NEW opens with the market, so the")
    print("     winter session is 23:00-11:00 UTC. OLD also never returns None - it")
    print("     claims a session all through the 10:00-22:00 UTC dead zone, which is")
    print("     what let the last bar decide what 'now' was. Note that the 22:10 UTC")
    print("     scheduled run now lands *before* the winter session, so the workflow")
    print("     needs a 23:10 cron too (see PATCH_NOTES).")


def show_reference_follows_the_strategy():
    print()
    print("=" * 100)
    print("3. REFERENCE LEVEL - the alert now agrees with strategy.find_trades")
    print("=" * 100)
    df = frame(SUNDAY)
    state = NEW.session_state(df, now=ts("2026-09-07 01:30"))
    window = state["window"]
    trades = [t for t in state["trades"] if window[0] <= t["entry_time"] < window[1]]
    print(f"  session  : {window[0]} -> {window[1]}  (opens Sunday, exchange shut)")
    print("  its own NY-late window: none - Sunday has no bars")
    print(f"  NEW ref  : {state['ref'][:2] if state['ref'] else None}"
          "   <- Friday's, the last window that closed before the open")
    print(f"  strategy : {len(trades)} trade(s) in that session "
          f"{[(t['side'], str(t['entry_time'])) for t in trades]}")
    print("\n  -> The notifier used to demand a window on the session's own opening")
    print("     day and report 'no NY-late levels' for every Sunday open, while the")
    print("     strategy armed from Friday and traded. Both now apply the same rule:")
    print("     the most recent window that closed before the session's first bar.")


def show_order_path():
    print()
    print("=" * 100)
    print("4. ORDER PATH - the same window, asked of the calendar instead of hardcoded")
    print("=" * 100)
    summer = pd.Timestamp("2026-09-09").date()
    winter = pd.Timestamp("2026-12-09").date()
    profiles = {p["name"]: p for p in reclaim_monitor.PROFILES}

    print(f"  {'':22s} {'before (constant)':20s} after (calendar)")
    print(f"  {'reclaim tokyo trigger':22s} {'(22, 10)':20s} "
          f"summer {reclaim_monitor.profile_windows(profiles['tokyo'], summer)[0]} "
          f"/ winter {reclaim_monitor.profile_windows(profiles['tokyo'], winter)[0]}")
    print(f"  {'reclaim london ref':22s} {'(22, 10)':20s} "
          f"summer {reclaim_monitor.profile_windows(profiles['london'], summer)[1]} "
          f"/ winter {reclaim_monitor.profile_windows(profiles['london'], winter)[1]}")
    print()
    print("  session flatten")
    for label, when in (("10 Sep (CDT)", "2026-09-10 12:00"),
                        ("10 Dec (CST)", "2026-12-10 12:00")):
        print(f"    {label:14s} before 08:00 UTC   after "
              f"{session_exit_hour(ts(when)):02d}:00 UTC (03:00 CT)")
    print()
    print("  end_session profile, on the hourly cleanup cron")
    for when in ("2026-09-09 08:05", "2026-12-09 08:05", "2026-12-09 09:05"):
        before = "tokyo" if ts(when).hour == 8 else "london"
        print(f"    {when}   before H=08 -> {before:6s}   after -> "
              f"{end_session.which_profile(ts(when))}")
    print("\n  -> In winter the old rule cleans up London at 08:05 and never touches the")
    print("     Asia orders at all: they stay resting through the next session. The")
    print("     scheduled job already fires hourly through 10:05, so the fix needs no")
    print("     new cron - only the 09:05 run has to know it is the Asia cleanup.")


def show_one_trade_per_session():
    print()
    print("=" * 100)
    print("5. 8/28 - TWO LONG TRADES IN ONE SESSION")
    print("=" * 100)
    df = frame(TWO_TRIGGERS)
    state = NEW.session_state(df, now=ts("2026-09-09 06:04"))
    print("  bars that sweep the session low: 01:00 and 04:00")
    print(f"  trades strategy.find_trades emits for that session: {len(state['trades'])}")
    for t in state["trades"]:
        print(f"    {t['side']:5s} entry {t['entry']:.2f} @ {t['entry_time']} "
              f"-> {t['r']:+.2f}R ({t['reason']})")
    print("  -> at most one trade per session per run, so two 'closed' records for")
    print("     8/28 cannot both come from one run: it is the same session run")
    print("     twice, and ATR10 moved between the two, so the second run placed a")
    print("     genuinely different order.  One trade per session holds per run.")
    print("     (The doubled *postings* are a separate fault: a second webhook app")
    print("     registered 2026-09-02 22:22, six days after this session.)")


if __name__ == "__main__":
    show_freeze()
    show_session_window()
    show_reference_follows_the_strategy()
    show_order_path()
    show_one_trade_per_session()
