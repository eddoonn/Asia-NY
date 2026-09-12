"""Offline regression tests for notify.py session-state handling.

No network and no Discord: synthetic hourly frames drive the session logic
directly.  Covers the reported faults and the Globex-anchored window:

  * the armed triggers froze for three days (9/5 -> 9/8) because the reference
    level was resolved from the last data bar instead of the clock;
  * the 8/28 session was reported as two LONG trades because each run
    re-derived the session from scratch instead of resolving it against one
    clock;
  * a normal weekend closure was indistinguishable from a broken feed;
  * the session boundary is the Globex open (17:00 CT), so it lands at
    22:00 UTC in summer and 23:00 UTC in winter rather than at a fixed hour.

Fixtures are midweek so the market calendar reports "open"; the closure cases
are asserted explicitly.

    python test_notify_session_state.py
"""
import os
import tempfile
from types import SimpleNamespace

import pandas as pd

import notify as notify_mod
from strategy import add_atr, asia_day_ids
from notify import (build_embed, market_blocks, reference_levels,
                    run_trigger, session_day_id, session_ids, session_phase,
                    session_signal, session_state, session_window, state_key,
                    trigger_key, trigger_payload, trade_is_live)


def ts(text):
    return pd.Timestamp(text, tz="UTC")


def bar(when, o, h, l, c):
    return (when, o, h, l, c)


def frame(rows):
    index = pd.DatetimeIndex([pd.Timestamp(r[0], tz="UTC") for r in rows])
    assert index.is_monotonic_increasing, "test rows must be chronological"
    df = pd.DataFrame(
        {"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
         "Low": [r[3] for r in rows], "Close": [r[4] for r in rows]},
        index=index)
    return add_atr(df, 10)


def midweek_rows(second_trigger=False):
    """Tue 9/8 NY-late (95.0/89.0), then the Tue 22:00 -> Wed 10:00 session."""
    rows = [bar("2026-09-08 19:00", 94.0, 95.0, 89.0, 94.0),
            bar("2026-09-08 20:00", 93.5, 94.0, 90.0, 93.0),
            bar("2026-09-08 22:00", 93.0, 94.0, 92.0, 93.0),
            bar("2026-09-08 23:00", 92.5, 93.0, 91.0, 92.0),
            bar("2026-09-09 00:00", 91.5, 92.0, 90.0, 91.0),
            bar("2026-09-09 01:00", 90.5, 91.0, 84.0, 90.0)]
    rows += [bar("2026-09-09 02:00", 89.5, 90.0, 89.0, 89.5),
             bar("2026-09-09 03:00", 89.5, 90.0, 89.0, 89.5)]
    if second_trigger:
        rows += [bar("2026-09-09 04:00", 88.5, 89.0, 84.0, 88.5)]
    rows += [bar("2026-09-09 05:00", 89.0, 89.5, 88.5, 89.0),
             bar("2026-09-09 09:00", 89.0, 89.5, 88.5, 89.0)]
    return rows


def live_rows():
    """The same session, with the sweep left unresolved.

    The 01:00 bar dips through the trigger (95/89 NY-late, so LONG arms at
    `89 - 1xATR`) without reaching the stop or the target, and it is the last
    bar - which is what a position that is still on looks like to
    `find_trades`: an `eod` exit.
    """
    return midweek_rows()[:5] + [bar("2026-09-09 01:00", 86.0, 86.5, 85.0, 86.0)]


def winter_rows():
    """Wed 12/09 NY-late (95.0/89.0), then the Wed 23:00 -> Thu 11:00 session.

    The same shape as `midweek_rows` one hour later in UTC: in CST the Globex
    reopen is 23:00 UTC, so the 22:00-23:00 UTC maintenance break sits *before*
    the session rather than inside it.
    """
    return [bar("2026-12-09 19:00", 94.0, 95.0, 89.0, 94.0),
            bar("2026-12-09 20:00", 93.5, 94.0, 90.0, 93.0),
            bar("2026-12-09 22:00", 93.0, 93.5, 92.5, 93.0),
            bar("2026-12-09 23:00", 93.0, 94.0, 92.0, 93.0),
            bar("2026-12-10 00:00", 92.5, 93.0, 91.0, 92.0),
            bar("2026-12-10 01:00", 91.5, 92.0, 90.0, 91.0),
            bar("2026-12-10 02:00", 90.5, 91.0, 84.0, 90.0),
            bar("2026-12-10 03:00", 89.5, 90.0, 89.0, 89.5),
            bar("2026-12-10 05:00", 89.0, 89.5, 88.5, 89.0),
            bar("2026-12-10 09:00", 89.0, 89.5, 88.5, 89.0),
            bar("2026-12-10 10:00", 89.0, 89.5, 88.5, 89.0)]


def winter_flatten_rows():
    """A winter trade that survives to the flatten, so the flat hour decides.

    Nothing between 02:00 and 10:00 reaches the target or the stop, so the exit
    is the time exit - at 09:00 UTC (03:00 CT) under the session-relative rule,
    and at 08:00 under the old fixed hour.
    """
    return [bar("2026-12-09 19:00", 93.0, 95.0, 89.0, 93.5),   # Wed NY-late 95 / 89
            bar("2026-12-09 20:00", 93.0, 94.0, 90.0, 93.0),
            bar("2026-12-09 23:00", 92.0, 92.5, 91.0, 91.5),   # Wed 23:00 UTC open
            bar("2026-12-10 00:00", 91.0, 91.5, 90.0, 90.5),
            bar("2026-12-10 01:00", 90.0, 90.5, 89.5, 90.0),
            bar("2026-12-10 02:00", 88.0, 88.0, 85.0, 86.0),   # sweep -> long
            bar("2026-12-10 03:00", 86.0, 86.4, 85.6, 86.0),
            bar("2026-12-10 05:00", 86.0, 86.5, 85.5, 86.1),
            bar("2026-12-10 08:00", 86.1, 86.6, 86.0, 86.2),
            bar("2026-12-10 09:00", 86.2, 86.7, 86.1, 86.3),
            bar("2026-12-10 10:00", 86.3, 86.8, 86.2, 86.4)]


def winter_quiet_rows():
    """The winter session open with no sweep at all."""
    return [bar("2026-12-09 19:00", 94.0, 95.0, 89.0, 94.0),
            bar("2026-12-09 20:00", 93.5, 94.0, 90.0, 93.0),
            bar("2026-12-09 23:00", 93.0, 94.0, 92.0, 93.0),
            bar("2026-12-10 00:00", 92.5, 93.0, 91.0, 92.0)]


def sunday_rows():
    """Fri 9/4 NY-late, a dead weekend, then the session opening Sun 9/6.

    The session's own opening day (Sunday) has no NY-late window because the
    exchange is shut, so the last usable reference is Friday's.
    """
    return [bar("2026-09-04 19:00", 94.0, 95.0, 89.0, 94.0),
            bar("2026-09-04 20:00", 93.5, 94.0, 90.0, 93.0),
            bar("2026-09-06 22:00", 93.0, 94.0, 92.0, 93.0),
            bar("2026-09-06 23:00", 92.5, 93.0, 91.0, 92.0),
            bar("2026-09-07 01:00", 90.5, 91.0, 84.0, 90.0),
            bar("2026-09-07 02:00", 89.5, 90.0, 89.0, 89.5),
            bar("2026-09-07 09:00", 89.0, 89.5, 88.5, 89.0)]


def two_session_rows():
    return [bar("2026-09-08 19:00", 94.0, 95.0, 89.0, 94.0),
            bar("2026-09-08 20:00", 93.5, 94.0, 90.0, 93.0),
            bar("2026-09-08 22:00", 93.0, 94.0, 92.0, 93.0),
            bar("2026-09-09 00:00", 91.5, 92.0, 90.0, 91.0),
            bar("2026-09-09 01:00", 90.5, 91.0, 84.0, 90.0),
            bar("2026-09-09 09:00", 89.5, 90.0, 89.0, 89.5),
            bar("2026-09-09 19:00", 89.0, 89.5, 86.0, 88.0),
            bar("2026-09-09 20:00", 88.5, 89.0, 87.0, 88.5),
            bar("2026-09-09 22:00", 88.0, 89.0, 87.5, 88.5),
            bar("2026-09-09 23:00", 88.0, 88.5, 81.0, 87.0),
            bar("2026-09-10 09:00", 87.0, 87.5, 86.5, 87.0)]


def stale_rows():
    """A midweek session whose feed stops right after the open."""
    return [bar("2026-09-08 19:00", 92.0, 92.5, 91.2, 92.0),
            bar("2026-09-08 20:00", 92.0, 92.4, 91.3, 92.0),
            bar("2026-09-08 22:00", 92.0, 92.4, 91.2, 92.0)]


# ---------------------------------------------------------------- session window

def test_session_window_follows_the_globex_open_not_a_fixed_utc_hour():
    # Summer (CDT): the 17:00 CT reopen is 22:00 UTC, so 22:00-10:00 UTC.
    start, end = session_window(ts("2026-09-09 23:00"))
    assert start == ts("2026-09-09 22:00"), start
    assert end == ts("2026-09-10 10:00"), end
    assert session_window(ts("2026-09-09 21:59")) is None
    assert session_window(ts("2026-09-09 10:00")) is None
    assert session_window(ts("2026-09-09 11:48")) is None
    start, end = session_window(ts("2026-09-09 09:59"))
    assert start == ts("2026-09-08 22:00"), start
    assert end == ts("2026-09-09 10:00"), end

    # Winter (CST): the same CT rule lands an hour later in UTC.
    start, end = session_window(ts("2026-12-09 23:30"))
    assert start == ts("2026-12-09 23:00"), start
    assert end == ts("2026-12-10 11:00"), end
    assert session_window(ts("2026-12-10 22:15")) is None
    start, end = session_window(ts("2026-12-10 10:59"))
    assert start == ts("2026-12-09 23:00"), start
    assert end == ts("2026-12-10 11:00"), end
    assert session_window(ts("2026-12-10 11:00")) is None


def test_dst_transition_moves_the_session_open():
    # US DST ends 2026-11-01, so that Sunday's session opens at 23:00 UTC.
    assert session_window(ts("2026-11-01 22:30")) is None, "still before the open"
    start, end = session_window(ts("2026-11-01 23:30"))
    assert start == ts("2026-11-01 23:00"), start
    assert end == ts("2026-11-02 11:00"), end
    # The week before it was still on CDT.
    start, _ = session_window(ts("2026-10-28 23:30"))
    assert start == ts("2026-10-28 22:00"), start


def test_the_2210_cron_lands_inside_the_session_in_summer_only():
    # discord-notify.yml fires at 22:10 UTC. In summer that is 17:10 CT, ten
    # minutes after the open; in winter it is 16:10 CT, still in the break.
    assert session_window(ts("2026-09-09 22:10")) is not None
    assert session_window(ts("2026-12-09 22:10")) is None


def test_session_ids_match_the_summer_window_the_strategy_used():
    df = frame(midweek_rows())
    mask, ids = session_ids(df.index)
    legacy_mask, legacy_ids = asia_day_ids(df.index, (22, 10))
    assert list(mask) == list(legacy_mask), "summer boundaries must be unchanged"
    assert list(ids) == list(legacy_ids), "trade-date numbering must be unchanged"


def test_session_ids_use_the_winter_boundaries():
    df = frame(winter_rows())
    mask, _ = session_ids(df.index)
    legacy_mask, _ = asia_day_ids(df.index, (22, 10))
    now = {str(s): bool(v) for s, v in zip(df.index, mask)}
    old = {str(s): bool(v) for s, v in zip(df.index, legacy_mask)}
    # The 22:00 UTC bar sits in the maintenance break: the fixed window took it
    # (hour >= 22) and then dropped the session's real last hour (hour 10).
    assert now["2026-12-09 22:00:00+00:00"] is False
    assert old["2026-12-09 22:00:00+00:00"] is True
    assert now["2026-12-10 10:00:00+00:00"] is True
    assert old["2026-12-10 10:00:00+00:00"] is False
    tied = [str(s) for s, keep in zip(df.index, mask) if keep]
    assert tied[0] == "2026-12-09 23:00:00+00:00", tied
    assert tied[-1] == "2026-12-10 10:00:00+00:00", tied


# ------------------------------------------------------------ session identity

def test_one_trade_per_session_even_with_two_triggerable_bars():
    df = frame(midweek_rows(second_trigger=True))
    state = session_state(df, now=ts("2026-09-09 06:04"))
    assert len(state["trades"]) == 1, (
        f"a session must yield at most one trade, got {len(state['trades'])}")
    trade = state["trade"]
    assert trade is not None
    start, end = state["window"]
    assert start <= trade["entry_time"] < end


def test_repeated_runs_agree_on_the_session_trade():
    df = frame(midweek_rows())
    early = session_state(df, now=ts("2026-09-09 06:04"))
    late = session_state(df, now=ts("2026-09-09 09:30"))
    assert session_phase(early) == session_phase(late) == "closed"
    assert state_key(early) == state_key(late)
    assert early["trade"]["entry_time"] == late["trade"]["entry_time"]


def test_state_key_ignores_price_ticks():
    df = frame(midweek_rows())
    a = session_state(df, now=ts("2026-09-09 06:04"))
    b = session_state(df, now=ts("2026-09-09 07:37"))
    assert state_key(a) == state_key(b), "identical state must not re-post"


def test_current_feed_uses_this_sessions_own_levels():
    df = frame(midweek_rows())
    state = session_state(df, now=ts("2026-09-09 05:30"))
    assert state["stale"] is False, state["lag_hours"]
    assert state["market"] is None
    start, end = state["window"]
    assert start == ts("2026-09-08 22:00")
    assert session_day_id(state["window"]) - 1 in state["levels"], (
        "the session's opening day has its own NY-late window")
    ref = state["ref"]
    assert ref is not None, "expected the session's own NY-late window"
    assert abs(ref[0] - 95.0) < 1e-9 and abs(ref[1] - 89.0) < 1e-9, ref
    assert session_phase(state) in ("armed", "closed"), state["trade"]
    trade = state["trade"]
    if trade is not None:
        assert start <= trade["entry_time"] < end
        assert trade["entry_time"] == ts("2026-09-09 01:00")


def test_a_session_with_its_own_window_never_reaches_past_it():
    # The reported freeze: Thursday's levels re-broadcast for days. A session
    # that has a window of its own must use it, never an older one.
    rows = [bar("2026-09-07 19:00", 98.0, 99.0, 97.0, 98.0),
            bar("2026-09-07 20:00", 98.0, 98.5, 97.5, 98.0)]
    df = frame(rows + midweek_rows())
    state = session_state(df, now=ts("2026-09-09 05:30"))
    levels = state["levels"]
    window = state["window"]
    assert state["ref"] is not None
    older = {k: v for k, v in levels.items() if k < session_day_id(window) - 1}
    assert older, "frame still holds an older NY-late window to fall back to"
    assert older[next(iter(older))][0] == 99.0
    assert reference_levels(levels, df.index, window)[:2] == (95.0, 89.0), (
        "must arm from its own window, not an older one")


def test_sunday_session_arms_from_friday_exactly_as_the_strategy_does():
    # A Sunday open has no NY-late window of its own (the exchange is shut),
    # so both the notifier and find_trades fall back to Friday. They have to
    # agree, otherwise the alert and the strategy disagree about the triggers.
    df = frame(sunday_rows())
    state = session_state(df, now=ts("2026-09-07 01:30"))
    start, end = state["window"]
    assert start == ts("2026-09-06 22:00"), start
    opening_day = session_day_id(state["window"]) - 1
    assert opening_day not in state["levels"], "Sunday really has no window"
    ref = state["ref"]
    assert ref is not None, "must fall back to Friday, not report no-ref"
    assert abs(ref[0] - 95.0) < 1e-9 and abs(ref[1] - 89.0) < 1e-9, ref
    trades = [t for t in state["trades"] if start <= t["entry_time"] < end]
    assert trades, "the strategy itself arms this session from Friday"
    assert trades[0]["entry_time"] == ts("2026-09-07 01:00")
    assert reference_levels(state["levels"], df.index, state["window"])[:2] == ref[:2]


def test_a_window_that_had_not_closed_by_the_open_is_not_used():
    # The guard find_trades applies: `lv[2] < first_pos`. A NY-late window that
    # runs past the session open belongs to the *next* session, not this one.
    df = frame([bar("2026-09-08 22:00", 93.0, 94.0, 92.0, 93.0),
                bar("2026-09-08 23:00", 92.5, 93.0, 91.0, 92.0),
                bar("2026-09-09 19:00", 92.0, 96.0, 91.0, 95.0)])
    state = session_state(df, now=ts("2026-09-08 23:30"))
    assert state["window"][0] == ts("2026-09-08 22:00")
    assert state["levels"], "there is a NY-late window later in the frame"
    assert state["ref"] is None, "it had not closed before the session opened"
    assert session_phase(state) == "no-ref"


def test_each_session_reports_its_own_trade():
    df = frame(two_session_rows())
    state = session_state(df, now=ts("2026-09-09 23:30"))
    start, end = state["window"]
    assert start == ts("2026-09-09 22:00")
    assert len(state["trades"]) == 2, "one trade per session, two sessions"
    trade = state["trade"]
    assert trade is not None
    assert trade["entry_time"] == ts("2026-09-09 23:00")
    assert start <= trade["entry_time"] < end


def test_between_sessions_reports_idle():
    df = frame(midweek_rows())
    state = session_state(df, now=ts("2026-09-09 12:00"))
    assert state["market"] is None
    assert state["window"] is None
    assert session_phase(state) == "idle"
    embed = build_embed(df, state, "GC=F")
    assert "No session running" in embed["description"]
    assert "Globex day" in embed["description"]


# ------------------------------------------------------- closures and staleness

def test_weekend_quiet_feed_is_a_market_closure_not_a_feed_fault():
    # The reported outage: 9/5 00:58 UTC sat inside the Globex weekly close.
    df = frame(stale_rows())
    state = session_state(df, now=ts("2026-09-05 00:58"))
    assert market_blocks(state) is True
    assert state["market"].kind == "weekly-close"
    assert session_phase(state) == "market-closed"
    assert state["ref"] is None and state["trade"] is None
    embed = build_embed(df, state, "GC=F")
    assert "Market closed" in embed["description"]
    assert "weekly close" in embed["description"]
    assert "2026-09-06 22:00" in embed["description"]


def test_broken_feed_during_an_open_market_still_reports_stale():
    # Wed 05:00 UTC is Wed 00:00 CT - trading - and the feed stopped at 22:00.
    df = frame(stale_rows())
    state = session_state(df, now=ts("2026-09-09 05:00"))
    assert state["market"] is None
    assert state["window"] is not None
    assert state["stale"] is True
    assert session_phase(state) == "stale"
    assert state["ref"] is None, "a stale feed must not arm"
    assert "stale" in build_embed(df, state, "GC=F")["description"].lower()


def test_holiday_closure_beats_the_feed_check():
    df = frame(stale_rows())
    state = session_state(df, now=ts("2026-09-07 18:45"))     # Labor Day pause
    assert session_phase(state) == "market-closed"
    assert state["market"].name == "Labor Day"
    assert "Labor Day" in build_embed(df, state, "GC=F")["description"]


# ------------------------------------------------------------------ winter

def test_winter_quiet_session_reports_the_globex_window():
    df = frame(winter_quiet_rows())
    state = session_state(df, now=ts("2026-12-10 00:30"))
    assert state["market"] is None
    assert state["stale"] is False, state["lag_hours"]
    assert state["window"] == (ts("2026-12-09 23:00"), ts("2026-12-10 11:00"))
    assert session_phase(state) == "armed", state["trade"]
    session_field = [f["value"] for f in build_embed(df, state, "GC=F")["fields"]
                     if f["name"] == "Session"]
    assert session_field, "armed message must carry the session window"
    assert "23:00–11:00 UTC" in session_field[0], session_field
    assert "Globex 17:00 CT" in session_field[0], session_field


def test_winter_session_uses_its_own_levels_and_trades():
    df = frame(winter_rows())
    state = session_state(df, now=ts("2026-12-10 02:30"))
    assert state["window"] == (ts("2026-12-09 23:00"), ts("2026-12-10 11:00"))
    assert state["stale"] is False, state["lag_hours"]
    ref = state["ref"]
    assert ref is not None and abs(ref[0] - 95.0) < 1e-9 and abs(ref[1] - 89.0) < 1e-9
    trade = state["trade"]
    assert trade is not None and trade["entry_time"] == ts("2026-12-10 02:00"), trade
    start, end = state["window"]
    assert start <= trade["entry_time"] < end


def test_winter_break_sits_before_the_session_not_inside_it():
    # 22:15 UTC is 16:15 CT in winter, so the schedule fires during the
    # maintenance break - which is now *before* the 23:00 UTC open, not in the
    # session's first hour as it was with the fixed 22:00 window.
    df = frame(winter_quiet_rows())
    state = session_state(df, now=ts("2026-12-09 22:15"))
    assert state["market"].kind == "daily-break"
    assert market_blocks(state) is False
    assert state["window"] is None, "the session has not opened yet"
    assert session_phase(state) == "idle"
    embed = build_embed(df, state, "GC=F")
    assert "maintenance break" in embed["description"]
    assert "23:00" in embed["description"]


def test_the_winter_flatten_is_0300_ct_not_a_fixed_utc_hour():
    df = frame(winter_flatten_rows())
    state = session_state(df, now=ts("2026-12-10 09:30"))
    assert state["exit_hour"] == 9, "03:00 CT is 09:00 UTC on CST"
    trade = state["trade"]
    assert trade is not None, "the fixture must produce a trade"
    assert trade["reason"] == "time", trade
    assert trade["exit_time"] == ts("2026-12-10 09:00"), trade
    assert "flat by 09:00 UTC" in build_embed(df, state, "GC=F")["footer"]["text"]

    pinned = session_state(df, now=ts("2026-12-10 09:30"), exit_hour=8)
    assert pinned["exit_hour"] == 8
    assert pinned["trade"]["exit_time"] == ts("2026-12-10 08:00")
    assert trade["r"] != pinned["trade"]["r"], (
        "the extra hour the session rule buys has to change the R")


def test_the_summer_flatten_is_unchanged_at_0800_utc():
    # 03:00 CT is 08:00 UTC on CDT, which is what the live system already did.
    df = frame(midweek_rows())
    state = session_state(df, now=ts("2026-09-09 06:04"))
    assert state["exit_hour"] == 8


def test_daily_break_in_summer_is_noted_without_suppressing():
    # Wed 21:30 UTC is 16:30 CT in summer: the one-hour maintenance break,
    # which sits in the dead zone before the 22:00 UTC session.
    df = frame(midweek_rows())
    state = session_state(df, now=ts("2026-09-09 21:30"))
    assert state["market"].kind == "daily-break"
    assert market_blocks(state) is False
    assert session_phase(state) == "idle"
    assert "maintenance break" in build_embed(df, state, "GC=F")["description"]


# ---------------------------------------------------------------------------
# the trade-triggered alert
class Recorder:
    """Stand-in for `notify.send`, so the tests never touch the network."""

    def __init__(self):
        self.sent = []

    def __call__(self, webhook, embed, content=None):
        self.sent.append(embed)
        return 204

    def __enter__(self):
        self.original = notify_mod.send
        notify_mod.send = self
        return self

    def __exit__(self, *exc):
        notify_mod.send = self.original
        return False


def test_trigger_names_the_trade_and_the_flat_deadline():
    df = frame(live_rows())
    state = session_state(df, now=ts("2026-09-09 01:05"))
    trade = session_signal(state)
    assert trade is not None and trade_is_live(trade), trade

    key, embed = trigger_payload(state, "GC=F", last_px=float(df["Close"].iloc[-1]))
    assert key is not None and embed is not None
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert fields["Setup"].startswith("LONG"), fields
    assert fields["Entry"] == f"{trade['entry']:.2f}"
    assert fields["Stop"] == f"{trade['sl']:.2f}"
    assert fields["Target"] == f"{trade['tp']:.2f}"
    assert "LIVE" in fields["Status"], fields["Status"]
    assert "08:00 UTC" in fields["Status"], fields["Status"]
    assert "h" in fields["Status"] and "m left" in fields["Status"]
    # the level it swept, with the buffer read back off the entry
    assert fields["Triggered at"].startswith("89.00 - 1xATR10"), fields["Triggered at"]
    assert str(trade["entry_time"]) == fields["Entry time (UTC)"]
    assert "TRIGGERED" in embed["title"]


def test_trigger_is_sent_once_per_trade():
    df = frame(live_rows())
    state = session_state(df, now=ts("2026-09-09 01:05"))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state.json")
        with Recorder() as rec:
            first = run_trigger(state, "GC=F", "http://x", path)
            for _ in range(3):
                again = run_trigger(state, "GC=F", "http://x", path)
        assert first[2] is True and first[1] is not None, first
        assert len(rec.sent) == 1, len(rec.sent)
        assert again[0] == first[0] and again[1] is None and again[2] is False, again
        assert notify_mod.load_state(path)[notify_mod.TRIGGER_STATE] == first[0]
        # a forced re-send is still available, and a dry run never writes
        with Recorder() as rec2:
            run_trigger(state, "GC=F", "http://x", path, force=True)
        assert len(rec2.sent) == 1


def test_trigger_does_not_clobber_the_daily_session_key():
    df = frame(live_rows())
    state = session_state(df, now=ts("2026-09-09 01:05"))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state.json")
        notify_mod.remember("session", state_key(state), path)
        with Recorder():
            run_trigger(state, "GC=F", "http://x", path)
        saved = notify_mod.load_state(path)
    assert saved["session"] == state_key(state)
    assert saved[notify_mod.TRIGGER_STATE] == trigger_key(state, session_signal(state))


def test_no_trigger_message_while_the_session_is_only_armed():
    # everything but the sweep bar: the levels are set, nothing has filled
    df = frame(midweek_rows()[:5])
    state = session_state(df, now=ts("2026-09-09 00:30"))
    assert session_phase(state) == "armed"
    assert session_signal(state) is None
    assert trigger_payload(state) == (None, None)


def test_trigger_is_silent_when_nothing_is_running():
    df = frame(live_rows())
    weekend = session_state(df, now=ts("2026-09-12 12:00"))
    assert weekend["window"] is None
    assert trigger_payload(weekend) == (None, None)

    # and when the calendar says the book is shut, whatever the tape holds
    muted = dict(window=(ts("2026-09-11 22:00"), ts("2026-09-12 10:00")),
                 market=SimpleNamespace(kind="weekly-close"), trades=[], ref=None,
                 stale=False, lag_hours=1.0, now=ts("2026-09-12 02:00"))
    assert trigger_payload(muted) == (None, None)


def test_a_lagging_feed_does_not_swallow_a_trigger():
    """The entry is a fact once it prints; only the *levels* need a live feed."""
    df = frame(live_rows())
    late = session_state(df, now=ts("2026-09-09 07:30"))   # 6.5h behind the tape
    assert late["stale"] is True and late["trade"] is None
    key, embed = trigger_payload(late, "GC=F")
    assert key is not None and embed is not None
    fields = {f["name"]: f["value"] for f in embed["fields"]}
    assert "no longer on the feed" in fields["Triggered at"], fields
    assert "behind" in embed["footer"]["text"]


def test_a_finished_trade_says_it_has_finished():
    df = frame(midweek_rows())
    state = session_state(df, now=ts("2026-09-09 09:00"))
    trade = session_signal(state)
    assert trade is not None and not trade_is_live(trade)
    _key, embed = trigger_payload(state, "GC=F")
    status = {f["name"]: f["value"] for f in embed["fields"]}["Status"]
    assert status.startswith(f"Triggered, then {trade['reason']}"), status


def test_a_second_entry_in_the_same_session_is_its_own_trigger():
    """Same bar, wider history, larger ATR - a different order, so a new key.

    This is the log's 08-28 case in miniature: the trigger is `level - 1xATR10`,
    so an ATR that has moved puts the entry somewhere else on the same bar, and a
    second run arms a second order.  The key has to separate them rather than
    swallow the second as a duplicate.
    """
    def level_of(trade):
        """The reference a trade was armed from: `entry` is the trigger price."""
        return (trade["entry"] + (trade["entry"] - trade["sl"]) if trade["side"] == "long"
                else trade["entry"] - (trade["sl"] - trade["entry"]))

    first = session_state(frame(live_rows()), now=ts("2026-09-09 01:05"))
    # one earlier bar is given a wider range: same levels, larger ATR10, so the
    # trigger `level - 1xATR10` sits somewhere else on the very same bar
    wider = [r if r[0] != "2026-09-08 22:00"
             else bar(r[0], r[1], 95.5, r[3], r[4]) for r in live_rows()]
    second = session_state(frame(wider), now=ts("2026-09-09 01:05"))
    a, b = session_signal(first), session_signal(second)
    assert a is not None and b is not None
    assert a["side"] == b["side"] == "long"
    assert a["entry_time"] == b["entry_time"], "same bar"
    assert round(level_of(a), 2) == round(level_of(b), 2) == 89.0, (level_of(a), level_of(b))
    assert abs(a["entry"] - b["entry"]) > 0.01, (a["entry"], b["entry"])
    assert trigger_key(first, a) != trigger_key(second, b)


def main():
    tests = [fn for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {fn.__name__}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
