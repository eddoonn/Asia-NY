"""Offline tests for weekly_digest.py. No network.

The fixture is the Good Friday week, so one of the five sessions really is a
market closure and the other four cover every other classification.

    python test_weekly_digest.py
"""
import pandas as pd

from market_calendar import week_sessions
from strategy import add_atr
from weekly_digest import (build_digest_embed, session_reports, summarise)

# The Good Friday week, one session per status.  Sessions open 22:00 UTC that
# week (CDT); the Thursday session is inside the Good Friday closure, which
# starts 2026-04-02 21:00 UTC.
#
#   session (open UTC)    trade date   status
#   Sun 03-29 22:00       Mon 03-30    no-levels
#   Mon 03-30 22:00       Tue 03-31    no-sweep
#   Tue 03-31 22:00       Wed 04-01    trade
#   Wed 04-01 22:00       Thu 04-02    no-data
#   Thu 04-02 22:00       Fri 04-03    closed
WEEK_DAYS = ["no-levels", "no-sweep", "trade", "no-data", "closed"]

DIGEST_AT = "2026-04-04 09:00"      # Saturday, after the week closed


def ts(text):
    return pd.Timestamp(text, tz="UTC")


def bar(when, o, h, l, c):
    return (when, o, h, l, c)


def week_rows():
    rows = []
    # Mon 03-30 - nothing has closed before this session opens, so there is no
    # NY-late window to arm from at all.
    rows += [bar("2026-03-29 22:00", 92.0, 92.4, 91.2, 92.0),
             bar("2026-03-29 23:00", 92.0, 92.3, 91.3, 92.0),
             bar("2026-03-30 00:00", 92.0, 92.2, 91.4, 92.0),
             bar("2026-03-30 14:00", 92.0, 92.1, 91.5, 92.0)]
    # Tue 03-31 - armed from Monday's NY-late, but nothing reached the triggers.
    rows += [bar("2026-03-30 19:00", 94.0, 95.0, 89.0, 94.0),
             bar("2026-03-30 20:00", 93.5, 94.0, 90.0, 93.0),
             bar("2026-03-30 22:00", 92.0, 92.4, 91.2, 92.0),
             bar("2026-03-30 23:00", 92.0, 92.3, 91.3, 92.0),
             bar("2026-03-31 00:00", 92.0, 92.2, 91.4, 92.0),
             bar("2026-03-31 02:00", 91.5, 92.0, 91.0, 91.5),
             bar("2026-03-31 14:00", 92.0, 92.1, 91.5, 92.0)]
    # Wed 04-01 - armed from Tuesday's NY-late and swept it.
    rows += [bar("2026-03-31 19:00", 94.0, 95.0, 89.0, 94.0),
             bar("2026-03-31 20:00", 93.5, 94.0, 90.0, 93.0),
             bar("2026-03-31 22:00", 92.0, 92.4, 91.2, 92.0),
             bar("2026-03-31 23:00", 92.0, 92.3, 91.3, 92.0),
             bar("2026-04-01 00:00", 92.0, 92.2, 91.4, 92.0),
             bar("2026-04-01 01:00", 90.5, 91.0, 84.0, 90.0),
             bar("2026-04-01 02:00", 91.5, 92.0, 91.0, 91.5),
             bar("2026-04-01 09:00", 91.5, 92.0, 91.0, 91.5)]
    # Thu 04-02 - market open, session bars missing, but the day itself has
    # bars, so this is a feed gap rather than a whole area gone quiet.
    rows += [bar("2026-04-01 14:00", 92.0, 92.1, 91.5, 92.0)]
    return rows


def weekly_frame():
    rows = week_rows()
    index = pd.DatetimeIndex([pd.Timestamp(r[0], tz="UTC") for r in rows])
    assert index.is_monotonic_increasing, "fixture must be chronological"
    df = pd.DataFrame(
        {"Open": [r[1] for r in rows], "High": [r[2] for r in rows],
         "Low": [r[3] for r in rows], "Close": [r[4] for r in rows]},
        index=index)
    return add_atr(df, 10)


def reports():
    return session_reports(weekly_frame(), now=ts(DIGEST_AT))


def test_week_runs_sunday_open_to_friday_close():
    windows, anchor = week_sessions(ts(DIGEST_AT))
    assert anchor == ts("2026-04-03 10:00")
    assert len(windows) == len(WEEK_DAYS) == 5
    assert windows[0][0] == ts("2026-03-29 22:00")
    assert [w[0].strftime("%a") for w in windows] == ["Sun", "Mon", "Tue", "Wed", "Thu"]
    assert [w[0].hour for w in windows] == [22] * 5, "summer week: 17:00 CT = 22:00 UTC"
    for start, end in windows:
        assert start < end <= anchor


def test_week_windows_follow_the_globex_open_into_winter():
    windows, anchor = week_sessions(ts("2026-12-12 09:00"))
    assert anchor == ts("2026-12-11 11:00"), anchor
    assert [w[0].hour for w in windows] == [23] * 5, "winter: 17:00 CT = 23:00 UTC"
    assert windows[-1][1] == anchor


def test_the_dst_week_is_one_rule_not_a_straddle():
    # The week containing the 2026-11-01 change is entirely CST, because the
    # switch happens at 02:00 local - before that Sunday's session opens.
    windows, anchor = week_sessions(ts("2026-11-07 09:00"))
    assert anchor == ts("2026-11-06 11:00")
    assert [w[0].hour for w in windows] == [23] * 5
    before, _ = week_sessions(ts("2026-11-05 12:00"))
    assert [w[0].hour for w in before] == [22] * 5, "the week before is still CDT"


def test_week_anchor_holds_all_week_and_rolls_forward():
    # Mid-week and weekend calls must all describe the same finished week.
    for moment in ("2026-04-04 09:00", "2026-04-06 12:00", "2026-04-09 23:00"):
        assert week_sessions(ts(moment))[1] == ts("2026-04-03 10:00"), moment
    # Before Friday 10:00 the week has not finished, so the previous one is used.
    assert week_sessions(ts("2026-04-03 09:00"))[1] == ts("2026-03-27 10:00")
    assert week_sessions(ts("2026-04-03 10:00"))[1] == ts("2026-04-03 10:00")


def test_every_session_gets_exactly_one_status():
    _, _, found = reports()
    statuses = [r["status"] for r in found]
    assert statuses == WEEK_DAYS, statuses


def test_closure_outranks_a_missing_feed():
    _, _, found = reports()
    closed = [r for r in found if r["status"] == "closed"][0]
    assert closed["bars"] == 0, "fixture gives the closed session no bars"
    assert closed["closure"] == "Good Friday"
    assert closed["closed_share"] == 1.0


def test_no_data_is_not_confused_with_no_sweep():
    _, _, found = reports()
    by_status = {r["status"]: r for r in found}
    assert by_status["no-data"]["bars"] == 0
    assert by_status["no-sweep"]["bars"] > 0
    assert by_status["no-sweep"]["ref"] is not None
    assert by_status["no-levels"]["bars"] > 0
    assert by_status["no-levels"]["ref"] is None


def test_traded_session_carries_its_own_trade():
    _, _, found = reports()
    trade = [r for r in found if r["status"] == "trade"][0]
    assert trade["trade"] is not None
    assert trade["start"] <= trade["trade"]["entry_time"] < trade["end"]
    assert trade["start"] == ts("2026-03-31 22:00")
    assert trade["trade_date"].isoformat() == "2026-04-01"


def test_no_data_session_is_a_feed_gap_when_the_rest_of_the_day_has_bars():
    _, _, found = reports()
    gap = [r for r in found if r["status"] == "no-data"][0]
    assert gap["bars"] == 0
    assert gap["neighbour_bars"] > 0, "the fixture keeps that day's earlier bars"
    from weekly_digest import _describe_session
    line = _describe_session(gap)
    assert "no price data" in line
    assert "silent either side" not in line, line


def test_no_data_wording_separates_a_gap_from_a_silent_feed():
    from weekly_digest import _describe_session
    base = {"trade_date": pd.Timestamp("2026-04-02").date(),
            "status": "no-data", "bars": 0, "neighbour_bars": 0}
    assert "silent either side" in _describe_session(base)
    fed = dict(base, neighbour_bars=2)
    line = _describe_session(fed)
    assert "no price data" in line and "silent either side" not in line, line


def test_summary_counts_and_net_match_the_reports():
    _, _, found = reports()
    counts, traded, net = summarise(found)
    assert len(found) == 5
    assert counts["trade"] == 1 and counts["no-sweep"] == 1
    assert counts["no-levels"] == 1 and counts["no-data"] == 1 and counts["closed"] == 1
    assert net == round(sum(r["trade"]["r"] for r in traded), 10)


def test_digest_embed_lists_sessions_and_closures():
    week_start, week_end, found = reports()
    embed, info = build_digest_embed(week_start, week_end, found, "GC=F")
    text = embed["description"]
    # Sessions are labelled by trade date, the day their daytime half falls on.
    for label in ("Mon 03-30", "Tue 03-31", "Wed 04-01", "Thu 04-02", "Fri 04-03"):
        assert label in text, label
    assert "market closed — Good Friday" in text
    assert "no sweep — levels" in text
    assert "no NY-late levels" in text
    assert "no price data" in text
    assert "5 sessions" in text
    assert embed["title"].endswith("(Mar 29 – Apr 03)")
    assert info["week_end"] == week_end.isoformat()
    assert info["counts"]["closed"] == 1
    assert embed["fields"][0]["name"] == "Market closures"
    assert "Good Friday" in embed["fields"][0]["value"]


def test_digest_is_stable_for_the_same_week():
    week_start, week_end, found = reports()
    first = build_digest_embed(week_start, week_end, found, "GC=F")[0]
    again = build_digest_embed(week_start, week_end, found, "GC=F")[0]
    assert first == again
    # ... and a later call in the following week renders an identical digest,
    # which is what makes the state key safe to dedupe on.
    later = session_reports(weekly_frame(), now=ts("2026-04-06 12:00"))
    assert later[0] == week_start and later[1] == week_end
    assert build_digest_embed(later[0], later[1], later[2], "GC=F")[0] == first


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
