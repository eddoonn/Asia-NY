"""Offline tests for the order path: place_orders, reclaim_monitor, end_session.

No network and no broker: the frames that drive `session_levels` are synthetic,
and `load_data` is patched out.  What is under test is that the order path asks
the calendar rather than a constant - the Asia session is 22:00-10:00 UTC on CDT
and 23:00-11:00 UTC on CST, and the flatten is 03:00 CT in both.

    python test_order_path.py
"""
import pandas as pd

import end_session
import place_orders
import reclaim_monitor
from market_calendar import session_exit_hour, session_hour_window, to_ct
from strategy import add_atr


def ts(text):
    return pd.Timestamp(text, tz="UTC")


def frame(rows):
    index = pd.DatetimeIndex([pd.Timestamp(r[0], tz="UTC") for r in rows])
    assert index.is_monotonic_increasing, "test rows must be chronological"
    df = pd.DataFrame(
        {"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
         "Low": [r[3] for r in rows], "Close": [r[4] for r in rows]},
        index=index)
    return add_atr(df, 10)


def patched(df):
    """Patch place_orders.load_data to serve `df` and return the original."""
    original = place_orders.load_data
    place_orders.load_data = lambda *a, **k: df.copy()
    return original


SUMMER = [("2026-09-08 19:00", 94.0, 95.0, 89.0, 94.0),   # Tue NY-late 95 / 89
          ("2026-09-08 20:00", 93.5, 94.0, 90.0, 93.0),
          ("2026-09-08 22:00", 93.0, 94.0, 92.0, 93.0),   # Tue 22:00 UTC open
          ("2026-09-09 09:00", 89.0, 89.5, 88.5, 89.0)]

WINTER = [("2026-12-08 19:00", 94.0, 95.0, 89.0, 94.0),   # Tue NY-late 95 / 89
          ("2026-12-08 20:00", 93.5, 94.0, 90.0, 93.0),
          ("2026-12-08 22:00", 93.0, 93.5, 92.5, 93.0),   # maintenance break
          ("2026-12-08 23:00", 93.0, 94.0, 92.0, 93.0),   # Tue 23:00 UTC open
          ("2026-12-09 10:00", 89.0, 89.5, 88.5, 89.0)]

BLANK = [("2026-09-06 22:00", 93.0, 94.0, 92.0, 93.0),    # Sunday open, no NY-late
         ("2026-09-07 09:00", 89.0, 89.5, 88.5, 89.0)]

# A sweep below the NY-late low that closes back above it: the reclaim signal.
RECLAIM = [("2026-09-08 19:00", 94.0, 95.0, 89.0, 94.0),
           ("2026-09-08 20:00", 93.5, 94.0, 90.0, 93.0),
           ("2026-09-08 22:00", 93.0, 94.0, 92.0, 93.0),
           ("2026-09-08 23:00", 92.5, 93.0, 91.0, 92.0),
           ("2026-09-09 00:00", 91.5, 92.0, 90.0, 91.0),
           ("2026-09-09 01:00", 90.0, 90.5, 84.5, 89.5)]


# ------------------------------------------------------------------ calendar

def test_session_hour_window_is_one_rule_two_offsets():
    assert session_hour_window(pd.Timestamp("2026-09-09").date()) == (22, 10)
    assert session_hour_window(pd.Timestamp("2026-12-09").date()) == (23, 11)
    # DST ends 2026-11-01, and the change is at 02:00 local, i.e. before the
    # 17:00 CT open - so the week either side is cleanly one offset or the other.
    assert session_hour_window(pd.Timestamp("2026-10-29").date()) == (22, 10)
    assert session_hour_window(pd.Timestamp("2026-11-01").date()) == (23, 11)


def test_the_flatten_is_0300_ct_not_a_fixed_utc_hour():
    assert session_exit_hour(ts("2026-09-09 12:00")) == 8, "CDT: 03:00 CT = 08:00 UTC"
    assert session_exit_hour(ts("2026-12-09 12:00")) == 9, "CST: 03:00 CT = 09:00 UTC"
    # Stable whether asked before or after the flat on that CT date.
    assert session_exit_hour(ts("2026-12-10 02:00")) == 9
    assert session_exit_hour(ts("2026-12-10 06:00")) == 9
    assert to_ct(ts("2026-12-10 09:00")).hour == 3, "09:00 UTC really is 03:00 CT"


def test_the_flatten_moves_on_the_dst_weekend():
    assert session_exit_hour(ts("2026-10-30 12:00")) == 8      # Friday before
    assert session_exit_hour(ts("2026-11-02 12:00")) == 9      # Monday after
    # The Sunday session itself opens 17:00 CT, already CST.
    assert session_exit_hour(ts("2026-11-01 23:30")) == 9


# ------------------------------------------------------------ reclaim_monitor

def test_globex_windows_resolve_per_season():
    summer = pd.Timestamp("2026-09-09").date()
    winter = pd.Timestamp("2026-12-09").date()
    by_name = {p["name"]: p for p in reclaim_monitor.PROFILES}

    tokyo = by_name["tokyo"]
    assert reclaim_monitor.profile_windows(tokyo, day=summer) == ((22, 10), (19, 21))
    assert reclaim_monitor.profile_windows(tokyo, day=winter) == ((23, 11), (19, 21))

    # London is 08:00 London = 02:00 CT all year, which is 07:00-13:00 UTC on
    # CDT and 08:00-14:00 on CST - the fixed (7, 13) pair was an hour early in
    # winter too.
    london = by_name["london"]
    assert reclaim_monitor.profile_windows(london, day=summer) == ((7, 13), (22, 10))
    assert reclaim_monitor.profile_windows(london, day=winter) == ((8, 14), (23, 11))


def test_the_tokyo_window_covers_the_real_session_open_in_winter():
    tokyo = next(p for p in reclaim_monitor.PROFILES if p["name"] == "tokyo")
    trigger, _ = reclaim_monitor.profile_windows(
        tokyo, day=pd.Timestamp("2026-12-09").date())
    assert reclaim_monitor.in_window(ts("2026-12-09 23:30"), trigger), "right after the open"
    assert reclaim_monitor.in_window(ts("2026-12-10 10:30"), trigger), "last hour of the session"
    # The old fixed (22, 10) pair took the 22:00-23:00 UTC maintenance-break bar
    # as if it were the session open, and dropped the real last hour.
    assert reclaim_monitor.in_window(ts("2026-12-09 22:30"), (22, 10))
    assert not reclaim_monitor.in_window(ts("2026-12-09 22:30"), trigger)
    assert not reclaim_monitor.in_window(ts("2026-12-10 10:30"), (22, 10))


# --------------------------------------------------------------- end_session

def test_the_asia_cleanup_owns_its_own_flat_in_both_seasons():
    # Summer: flat 08:00 UTC, so the 08:05 and 09:05 crons are the Asia cleanup.
    assert end_session.which_profile(ts("2026-09-09 08:05")) == "tokyo"
    assert end_session.which_profile(ts("2026-09-09 09:05")) == "tokyo"
    assert end_session.which_profile(ts("2026-09-09 07:05")) == "london"
    assert end_session.which_profile(ts("2026-09-09 17:05")) == "london"
    # Winter: the flat is 09:00 UTC. A hardcoded 08:00 would clean up the wrong
    # session and leave the Asia orders resting - which is the whole point.
    assert end_session.which_profile(ts("2026-12-09 08:05")) == "london"
    assert end_session.which_profile(ts("2026-12-09 09:05")) == "tokyo"
    assert end_session.which_profile(ts("2026-12-09 10:05")) == "tokyo"
    assert end_session.which_profile(ts("2026-12-09 11:05")) == "london"


def test_flat_hour_matches_the_calendar():
    # Both flats are CT clock times - 03:00 CT for Asia, 12:00 CT for London - so
    # both move an hour with DST. The London hour was a hardcoded 17:00 UTC.
    assert end_session.flat_hour("tokyo", ts("2026-09-09 08:05")) == 8
    assert end_session.flat_hour("tokyo", ts("2026-12-09 08:05")) == 9
    assert end_session.flat_hour("london", ts("2026-09-09 08:05")) == 17
    assert end_session.flat_hour("london", ts("2026-12-09 08:05")) == 18


def test_describe_schedule_names_the_session_it_is_working_on():
    line = end_session.describe_schedule(ts("2026-12-09 08:05"))
    assert "tokyo flat 09:00 UTC" in line
    assert "23:00" in line and "11:00" in line


# -------------------------------------------------------------- place_orders

def test_place_orders_arms_from_the_session_reference_in_summer():
    original = patched(frame(SUMMER))
    try:
        ref, atr, window = place_orders.session_levels("GC=F", now=ts("2026-09-08 23:30"))
    finally:
        place_orders.load_data = original
    assert window == (ts("2026-09-08 22:00"), ts("2026-09-09 10:00")), window
    assert (ref[0], ref[1]) == (95.0, 89.0), ref
    assert atr > 0


def test_place_orders_uses_the_winter_window_not_the_summer_one():
    original = patched(frame(WINTER))
    try:
        ref, _, window = place_orders.session_levels("GC=F", now=ts("2026-12-09 00:30"))
    finally:
        place_orders.load_data = original
    assert window == (ts("2026-12-08 23:00"), ts("2026-12-09 11:00")), window
    assert (ref[0], ref[1]) == (95.0, 89.0), ref


def test_place_orders_refuses_outside_a_session():
    original = patched(frame(SUMMER))
    try:
        for when in ("2026-09-09 12:00", "2026-09-09 20:00"):
            try:
                place_orders.session_levels("GC=F", now=ts(when))
                raise AssertionError(f"{when} should not arm")
            except place_orders.NoSession as exc:
                assert "no Asia session" in str(exc), exc
    finally:
        place_orders.load_data = original


def test_place_orders_refuses_when_nothing_closed_before_the_open():
    original = patched(frame(BLANK))
    try:
        try:
            place_orders.session_levels("GC=F", now=ts("2026-09-06 22:30"))
            raise AssertionError("a session with no closed NY-late window must not arm")
        except place_orders.NoSession as exc:
            assert "no NY-late reference" in str(exc), exc
    finally:
        place_orders.load_data = original


def test_reclaim_tokyo_arms_inside_the_globex_window_only():
    original = reclaim_monitor.load_data
    reclaim_monitor.load_data = lambda *a, **k: frame(RECLAIM)
    try:
        tokyo = next(p for p in reclaim_monitor.PROFILES if p["name"] == "tokyo")
        state = {"orders": []}
        armed = reclaim_monitor.check_signal(None, tokyo, "USDJPY", "USDJPY=X",
                                            100.0, ts("2026-09-09 02:05").to_pydatetime(),
                                            state, True)
        assert armed is not None, "the reclaim bar is inside the Asia session"
        assert armed["side"] == "buy"
        assert armed["skey"] == "tokyo:2026-09-08"
        # 22:05 UTC is ten minutes after the open in summer - but the same hour
        # is 16:05 CT in winter, which is still the maintenance break.
        summer = reclaim_monitor.check_signal(
            None, tokyo, "USDJPY", "USDJPY=X", 100.0,
            ts("2026-09-08 23:05").to_pydatetime(), state, True)
        assert summer is not None, "summer: 23:05 UTC is inside the session"
        winter = reclaim_monitor.check_signal(
            None, tokyo, "USDJPY", "USDJPY=X", 100.0,
            ts("2026-12-08 22:05").to_pydatetime(), state, True)
        assert winter is None, "winter: 22:05 UTC is before the 23:00 open"
    finally:
        reclaim_monitor.load_data = original


def test_the_sunday_filter_reads_the_market_day_not_the_utc_hour():
    # Both of these are Sunday 17:00 CT, at the open of the Sunday session.
    assert place_orders.is_sunday_session(ts("2026-09-06 22:05")) is True
    assert place_orders.is_sunday_session(ts("2026-11-01 23:05")) is True
    assert to_ct(ts("2026-09-06 22:05")).weekday() == 6
    assert to_ct(ts("2026-11-01 23:05")).weekday() == 6
    # ... and the Monday and Tuesday opens are not.
    assert place_orders.is_sunday_session(ts("2026-09-07 22:05")) is False
    assert place_orders.is_sunday_session(ts("2026-11-02 23:05")) is False


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
