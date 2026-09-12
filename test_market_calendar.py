"""Offline tests for market_calendar.py. No network.

    python test_market_calendar.py
"""
import pandas as pd

from market_calendar import (CT, Closure, closure, describe, is_open, next_open,
                             to_ct, window_for)


def utc(text):
    return pd.Timestamp(text, tz="UTC")


def test_weekly_close_bounds_summer():
    # CDT: week opens Sunday 17:00 CT = 22:00 UTC, closes Friday 16:00 CT = 21:00 UTC
    assert is_open(utc("2026-09-04 20:59")), "Friday just before the close"
    assert closure(utc("2026-09-04 21:00")).kind == "weekly-close"
    assert closure(utc("2026-09-05 12:00")).kind == "weekly-close"
    assert closure(utc("2026-09-06 21:59")).kind == "weekly-close"
    assert is_open(utc("2026-09-06 22:00")), "Sunday 17:00 CT reopen"

    friday_close = closure(utc("2026-09-04 22:00"))
    assert friday_close.until == utc("2026-09-06 22:00")
    assert friday_close.name == "Globex weekly close"


def test_weekly_close_bounds_winter_dst():
    # CST: the same wall-clock rule lands one hour later in UTC
    assert is_open(utc("2026-12-11 21:59")), "Friday 15:59 CT in winter"
    assert closure(utc("2026-12-11 22:00")).kind == "weekly-close"
    assert closure(utc("2026-12-13 22:59")).kind == "weekly-close"
    assert is_open(utc("2026-12-13 23:00")), "Sunday 17:00 CT in winter"
    assert closure(utc("2026-12-11 22:00")).until == utc("2026-12-13 23:00")


def test_dst_transition_sunday():
    # US DST ends 2026-11-01; that Sunday's open is 17:00 CST = 23:00 UTC
    assert to_ct(utc("2026-11-01 22:00")).hour == 16
    assert closure(utc("2026-11-01 22:00")).kind == "weekly-close"
    assert is_open(utc("2026-11-01 23:00"))


def test_daily_maintenance_break():
    assert is_open(utc("2026-09-09 20:59"))              # Wed 15:59 CT
    assert closure(utc("2026-09-09 21:30")).kind == "daily-break"
    assert closure(utc("2026-09-09 21:30")).until == utc("2026-09-09 22:00")
    assert is_open(utc("2026-09-09 22:00"))              # Wed 17:00 CT


def test_good_friday_closes_the_thursday_night_session():
    assert is_open(utc("2026-04-02 20:00"))              # Thu 15:00 CT
    assert closure(utc("2026-04-02 21:30")).name == "Good Friday"
    assert closure(utc("2026-04-03 10:00")).name == "Good Friday"
    assert closure(utc("2026-04-04 12:00")).name == "Good Friday"
    assert is_open(utc("2026-04-05 22:00"))              # Sun 17:00 CT reopen


def test_full_close_holidays_run_through_the_weekend():
    for stamp in ("2026-06-19 17:00", "2026-06-20 12:00", "2026-06-21 21:00"):
        assert closure(utc(stamp)).name == "Juneteenth", stamp
    assert is_open(utc("2026-06-21 22:00"))

    assert closure(utc("2026-07-03 18:00")).name == "Independence Day (observed)"
    assert is_open(utc("2026-07-05 22:00"))


def test_monday_holiday_pauses_only_the_afternoon():
    assert is_open(utc("2026-09-07 18:29"))              # Mon 13:29 CT
    assert closure(utc("2026-09-07 18:30")).name == "Labor Day"
    assert closure(utc("2026-09-07 18:30")).until == utc("2026-09-07 22:00")
    assert is_open(utc("2026-09-07 22:00")), "Monday evening session runs"


def test_christmas_and_new_year_windows_span_the_year_boundary():
    assert is_open(utc("2026-12-24 17:00"))              # Thu 11:00 CT
    assert closure(utc("2026-12-24 18:45")).name == "Christmas"
    assert closure(utc("2026-12-25 15:00")).name == "Christmas"
    assert closure(utc("2026-12-26 15:00")).name == "Christmas"
    assert is_open(utc("2026-12-27 23:00"))              # Sun 17:00 CT

    assert is_open(utc("2026-12-31 21:30"))              # Thu 15:30 CT, regular session
    assert closure(utc("2026-12-31 22:30")).name == "New Year"
    assert closure(utc("2027-01-01 15:00")).name == "New Year"
    assert is_open(utc("2027-01-03 23:00")), "Sunday 17:00 CT reopen in 2027"


def test_the_reported_outage_window_is_a_closure_not_a_feed_fault():
    # 9/5 00:58 UTC was inside the weekly close that ran 9/4 21:00 -> 9/6 22:00.
    found = closure(utc("2026-09-05 00:58"))
    assert isinstance(found, Closure)
    assert found.kind == "weekly-close"
    assert found.until == utc("2026-09-06 22:00")
    print(f"      9/5 00:58 -> {describe(utc('2026-09-05 00:58'))}")
    # 9/8 01:15 UTC is Monday 20:15 CT, after the Labor Day pause: genuinely open,
    # so a quiet feed there is a real feed fault rather than a closure.
    assert is_open(utc("2026-09-08 01:15"))
    assert closure(utc("2026-09-07 19:00")).name == "Labor Day"


def test_next_open_always_lands_on_an_open_market():
    probes = ["2026-09-05 00:58", "2026-09-06 12:00", "2026-09-04 21:30",
              "2026-04-03 10:00", "2026-12-25 18:00", "2026-09-09 21:30",
              "2026-07-04 12:00", "2026-11-01 20:00", "2026-12-31 23:00"]
    for stamp in probes:
        moment = utc(stamp)
        resume = next_open(moment)
        assert resume >= moment, stamp
        assert is_open(resume), f"{stamp} -> next_open {resume} is still closed"
        assert closure(moment) is not None, f"{stamp} should be closed"


def test_ct_window_helper_matches_the_declared_table():
    start, end = window_for("2026-09-07 13:30"), window_for("2026-09-07 17:00")
    assert start.tzinfo is not None and end.tzinfo is not None
    assert start.tz_convert(CT).hour == 13
    assert end.tz_convert(CT).hour == 17


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
