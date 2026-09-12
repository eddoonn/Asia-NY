"""CME Globex gold (COMEX GC) market calendar.

Trading hours, per CME Group's gold futures page:

    "Sunday - Friday 6:00 p.m. - 5:00 p.m. (5:00 p.m. - 4:00 p.m. CT) with a
     60-minute break each day beginning at 5:00 p.m. (4:00 p.m. CT)"

So in America/Chicago terms the week is: open Sunday 17:00 CT, close Friday
16:00 CT, with a 16:00-17:00 CT maintenance break every day.  Converting
through `America/Chicago` keeps the UTC boundaries correct across DST: the
weekly close is 21:00 UTC in summer and 22:00 UTC in winter.

Holiday closures are declared in `METALS_CLOSURES` as windows in CT local time.
They come from CME Group's published 2026 schedule for metals (GC) and they are
data, not logic - if one is wrong or missing the calendar reports "open", the
feed-lag check takes over, and nothing is ever armed on a level set that does
not belong to the session.  Re-verify once a year against:

    https://www.cmegroup.com/trading-hours.html
    the "Gold Futures and Options" fact card at cmegroup.com

The Asia Grab session is the first half of a Globex trading day, so it is
derived from that same 17:00 CT reopen rather than from a fixed UTC hour: see
`session_window`, `session_windows` and `week_sessions`.  In UTC the window is
22:00-10:00 in summer and 23:00-11:00 in winter.

Run `python market_calendar.py --year 2026` to print every closure so the table
can be diffed against the exchange calendar in one pass,
`python market_calendar.py --check "2026-09-05 00:58"` to explain one moment, and
`python market_calendar.py --week "2026-09-12 09:00"` to print a whole week's
session windows in both zones.
"""
import argparse
from collections import namedtuple
from datetime import timedelta

import pandas as pd

CT = "America/Chicago"
UTC = "UTC"

# Sunday 17:00 CT open, Friday 16:00 CT close, 16:00-17:00 CT break daily.
WEEK_OPEN = (6, 17)      # weekday() == 6 is Sunday
WEEK_CLOSE = (4, 16)     # weekday() == 4 is Friday
BREAK_START_HOUR = 16

# The Asia Grab session is the first half of a Globex trading day: it opens with
# the exchange at 17:00 CT, Sunday through Thursday, and runs twelve hours to
# 05:00 CT.  Expressed in UTC that is 22:00-10:00 in summer (CDT) and 23:00-11:00
# in winter (CST) - which is why the old fixed 22:00 UTC start was an hour early
# for six months of the year.
SESSION_OPEN_HOUR = 17
SESSION_CLOSE_HOUR = 5
SESSION_HOURS = (SESSION_CLOSE_HOUR - SESSION_OPEN_HOUR) % 24

# Sunday..Thursday open; Friday and Saturday do not.
SESSION_WEEKDAYS = (6, 0, 1, 2, 3)

# The strategy flattens two hours before the session closes, i.e. 03:00 CT all
# year round.  That is 08:00 UTC on CDT and 09:00 UTC on CST - the same *market*
# time in both seasons, which a fixed 08:00 UTC is not.
FLAT_BEFORE_CLOSE_HOURS = 2

# The London profile trades the London morning.  08:00 London is 02:00 CT all
# year round (London and Chicago move clocks within a fortnight of each other),
# so `(7, 13)` UTC was again only correct in summer - it is 08:00-14:00 UTC on
# CST.  Defined here in CT for the same reason the session is.
LONDON_TRIGGER_CT = (2, 8)

#: London's flatten, likewise in CT (12:00 CT = 17:00 UTC on CDT, 18:00 on CST).
LONDON_EXIT_CT = 12

Closure = namedtuple("Closure", "kind name until")

# (start CT, end CT, name) - the window during which metals do not trade.
#
# One case the published sources disagree on, left deliberately open: the
# evening session on the Sunday before a Monday holiday (MLK, Presidents,
# Memorial, Labor Day).  Some calendars say Sunday opens as normal and runs into
# the holiday; others list Sunday as closed.  The table below follows the first
# reading, so a quiet feed that Sunday is reported as `stale` rather than as a
# closure.  If it turns out Sunday really is shut, add the window - for example
# ("2026-09-06 17:00", "2026-09-07 17:00", "Labor Day (no Sunday session)") -
# and it is a closure again.  Nothing else changes: a closure only ever removes
# a signal, never creates one.
METALS_CLOSURES = {
    2026: [
        ("2026-01-19 13:30", "2026-01-19 17:00", "Martin Luther King Jr. Day"),
        ("2026-02-16 13:30", "2026-02-16 17:00", "Presidents Day"),
        ("2026-04-02 16:00", "2026-04-05 17:00", "Good Friday"),
        ("2026-05-25 13:30", "2026-05-25 17:00", "Memorial Day"),
        ("2026-06-19 12:00", "2026-06-21 17:00", "Juneteenth"),
        ("2026-07-03 12:00", "2026-07-05 17:00", "Independence Day (observed)"),
        ("2026-09-07 13:30", "2026-09-07 17:00", "Labor Day"),
        ("2026-11-26 13:30", "2026-11-26 17:00", "Thanksgiving Day"),
        ("2026-11-27 13:45", "2026-11-29 17:00", "Day after Thanksgiving"),
        ("2026-12-24 12:45", "2026-12-27 17:00", "Christmas"),
        # Globex does not reopen on New Year's Eve; it stays shut through
        # New Year's Day and reopens Sunday 2027-01-03 at 17:00 CT.
        ("2026-12-31 16:00", "2027-01-03 17:00", "New Year"),
    ],
    2027: [
        ("2027-01-18 13:30", "2027-01-18 17:00", "Martin Luther King Jr. Day"),
        ("2027-02-15 13:30", "2027-02-15 17:00", "Presidents Day"),
        ("2027-03-25 16:00", "2027-03-28 17:00", "Good Friday"),
        ("2027-05-31 13:30", "2027-05-31 17:00", "Memorial Day"),
        ("2027-06-18 12:00", "2027-06-20 17:00", "Juneteenth (observed)"),
        ("2027-07-05 12:00", "2027-07-05 17:00", "Independence Day (observed)"),
        ("2027-09-06 13:30", "2027-09-06 17:00", "Labor Day"),
        ("2027-11-25 13:30", "2027-11-25 17:00", "Thanksgiving Day"),
        ("2027-11-26 13:45", "2027-11-28 17:00", "Day after Thanksgiving"),
        ("2027-12-23 16:00", "2027-12-26 17:00", "Christmas"),
        ("2027-12-31 16:00", "2028-01-02 17:00", "New Year"),
    ],
}


def to_ct(now):
    return pd.Timestamp(now).tz_convert(CT)


def to_utc(now):
    return pd.Timestamp(now).tz_convert(UTC)


def ct_window(start, end):
    return pd.Timestamp(start, tz=CT), pd.Timestamp(end, tz=CT)


def window_for(text):
    """'2026-09-07 13:30' -> Timestamp in CT."""
    return pd.Timestamp(text, tz=CT)


def closures(year):
    """Declared holiday windows for `year`, as (start, end, name) in CT."""
    out = []
    for start, end, name in METALS_CLOSURES.get(year, []):
        s, e = ct_window(start, end)
        out.append((s, e, name))
    return out


def _closures_near(year):
    """Windows declared in the neighbouring years too, so a New Year window that
    starts in December is still visible in January."""
    out = []
    for y in (year - 1, year, year + 1):
        out.extend(closures(y))
    return sorted(out)


def _ct(day, hour, minute=0):
    """A wall-clock time in CT on `day`.

    Built from date components rather than by adding a `Timedelta`: durations
    are absolute, so adding 17h to a midnight that fell before the DST change
    lands on 16:00 CT, not 17:00 CT.
    """
    return pd.Timestamp(f"{day:%Y-%m-%d} {hour:02d}:{minute:02d}", tz=CT)


def _sunday_open(local):
    ahead = (WEEK_OPEN[0] - local.weekday()) % 7
    day = (local + pd.Timedelta(days=ahead)).date()
    return _ct(day, *WEEK_OPEN[1:])


def _session_open_ct(day):
    """17:00 CT on `day` - the daily Globex reopen."""
    return _ct(day, SESSION_OPEN_HOUR)


def _session_close_ct(day):
    """05:00 CT on `day` - twelve hours after the previous day's reopen."""
    return _ct(day, SESSION_CLOSE_HOUR)


def closure(now):
    """The closure covering `now`, or None when metals are trading.

    kind is one of 'holiday', 'weekly-close', 'daily-break'; `until` is the UTC
    timestamp at which trading resumes.
    """
    local = to_ct(now)
    for start, end, name in _closures_near(local.year):
        if start <= local < end:
            return Closure("holiday", name, to_utc(end))

    weekday = local.weekday()
    if weekday == 5 or (weekday == 6 and local.hour < WEEK_OPEN[1]) or \
            (weekday == 4 and local.hour >= WEEK_CLOSE[1]):
        return Closure("weekly-close", "Globex weekly close", to_utc(_sunday_open(local)))

    if local.hour == BREAK_START_HOUR:
        resume = _ct(local.date(), BREAK_START_HOUR + 1)
        return Closure("daily-break", "Globex daily maintenance break", to_utc(resume))

    return None


def is_open(now):
    return closure(now) is None


def next_open(now):
    """UTC timestamp of the next resumption of trading, or `now` if open."""
    found = closure(now)
    return to_utc(now) if found is None else found.until


def closures_between(start, end):
    """Declared holiday windows intersecting [start, end) as (start, end, name)."""
    start, end = to_utc(start), to_utc(end)
    out = []
    for win_start, win_end, name in _closures_near(to_ct(start).year):
        if to_utc(win_start) < end and to_utc(win_end) > start:
            out.append((to_utc(win_start), to_utc(win_end), name))
    return sorted(out)


def session_open_on(day):
    """(open, close) in UTC for the session opening on the CT date `day`.

    None when the exchange does not reopen then: a Friday or Saturday, or a
    holiday that runs through 17:00 CT (Good Friday takes out Thursday's open).
    Both endpoints are wall-clock CT times built from date components, so the
    UTC hours follow DST without any offset arithmetic.
    """
    opened = to_utc(_session_open_ct(day))
    if closure(opened) is not None:
        return None
    return opened, to_utc(_session_close_ct(day + timedelta(days=1)))


def session_windows(start, end):
    """Every Asia session window intersecting [start, end), oldest first."""
    start, end = to_utc(start), to_utc(end)
    day = (to_ct(start) - pd.Timedelta(days=1)).date()
    last = to_ct(end).date()
    out = []
    while day <= last:
        window = session_open_on(day)
        if window is not None and window[1] > start and window[0] < end:
            out.append(window)
        day += timedelta(days=1)
    return out


def session_window(now):
    """(open, close) in UTC of the Asia session containing `now`, or None.

    A session opens at 17:00 CT and the next at 17:00 CT the following day, so
    `now` is either in the session that opened today or the one that opened
    yesterday.  Between 05:00 and 17:00 CT nothing is running - that is the
    idle half of the Globex day, not a session.
    """
    now = to_utc(now)
    today = to_ct(now).date()
    for back in (0, 1):
        window = session_open_on(today - timedelta(days=back))
        if window is not None and window[0] <= now < window[1]:
            return window
    return None


def next_session_open(now):
    """UTC timestamp of the next session open strictly after `now`."""
    now = to_utc(now)
    day = to_ct(now).date()
    for ahead in range(0, 8):
        window = session_open_on(day + timedelta(days=ahead))
        if window is not None and window[0] > now:
            return window[0]
    raise SystemExit(f"no Globex session open within a week of {now}")


def week_sessions(now):
    """(windows, anchor) for the most recently completed Globex week.

    `windows` is the five sessions opening Sunday..Thursday, oldest first, and
    `anchor` is the close of the last one - Friday 05:00 CT, which is 10:00 UTC
    in summer and 11:00 UTC in winter.  The windows are the *declared* ones, so
    a holiday that cancelled a session still appears and can be reported as
    closed rather than silently missing.
    """
    local = to_ct(now)
    friday = None
    for back in range(0, 16):
        day = local.date() - timedelta(days=back)
        if day.weekday() == WEEK_CLOSE[0] and _session_close_ct(day) <= local:
            friday = day
            break
    if friday is None:
        raise SystemExit("could not locate a completed Globex week")
    sunday = friday - timedelta(days=len(SESSION_WEEKDAYS))
    windows = [
        (to_utc(_session_open_ct(sunday + timedelta(days=k))),
         to_utc(_session_close_ct(sunday + timedelta(days=k + 1))))
        for k in range(len(SESSION_WEEKDAYS))
    ]
    return windows, to_utc(_session_close_ct(friday))


def session_hour_window(day=None):
    """(start_hour, end_hour) UTC of the Asia session opening on CT date `day`.

    A single hour pair, for the parts of the stack that describe a window as
    `(22, 10)` - `strategy.session_mask`, `strategy.ny_levels`.  It is 22/10 on
    CDT and 23/11 on CST; `day` defaults to today in CT.

    One caveat worth knowing: a frame that spans a DST change is described by a
    single pair, so the hour either side of the change is out by one. Callers
    that care about a specific moment use `session_window` instead.
    """
    if day is None:
        day = pd.Timestamp.now(tz=CT).date()
    start = to_utc(_session_open_ct(day))
    end = to_utc(_session_close_ct(day + timedelta(days=1)))
    return int(start.hour), int(end.hour)


def ct_hour_window(start_ct, end_ct, day=None):
    """A CT clock window -> the equivalent UTC hour pair.

    `start_ct > end_ct` wraps midnight.  Used for windows that are published in
    Central Time but consumed as UTC hours (the London profile's trigger).
    """
    if day is None:
        day = pd.Timestamp.now(tz=CT).date()
    start = to_utc(_ct(day, start_ct))
    end = to_utc(_ct(day, end_ct))
    return int(start.hour), int(end.hour)


def london_hour_window(day=None):
    """London trigger as a UTC hour pair: 07-13 on CDT, 08-14 on CST."""
    return ct_hour_window(*LONDON_TRIGGER_CT, day=day)


def session_exit_hour(now=None):
    """UTC hour the session flattens at: 03:00 CT, two hours before the close.

    `now` is any moment on that CT date - before the flat and after it give the
    same answer, so this is safe to call from a re-derivation long after the
    session closed.  Returns 8 on CDT and 9 on CST.
    """
    now = pd.Timestamp.now(tz=UTC) if now is None else to_utc(now)
    flat = _ct(to_ct(now).date(), SESSION_CLOSE_HOUR - FLAT_BEFORE_CLOSE_HOURS)
    return int(to_utc(flat).hour)


def london_exit_hour(now=None):
    """UTC hour the London profile flattens at: 12:00 CT.

    Same rule as the Asia flatten - expressed in CT so it moves with the season.
    It is 17:00 UTC on CDT and 18:00 on CST; the fixed 17:00 was a summer value
    applied all year.
    """
    now = pd.Timestamp.now(tz=UTC) if now is None else to_utc(now)
    flat = _ct(to_ct(now).date(), LONDON_EXIT_CT)
    return int(to_utc(flat).hour)


def describe_session(now):
    """One-line explanation of the Asia session state at `now`."""
    window = session_window(now)
    if window is None:
        nxt = next_session_open(now)
        return f"between sessions; next opens {nxt:%a %Y-%m-%d %H:%M} UTC"
    start, end = window
    return (f"{start:%a %Y-%m-%d %H:%M} -> {end:%a %Y-%m-%d %H:%M} UTC "
            f"({SESSION_HOURS}h from the {SESSION_OPEN_HOUR:02d}:00 CT open)")


def describe(now):
    """A one-line explanation of the market state at `now`."""
    found = closure(now)
    local = to_ct(now)
    if found is None:
        return f"trading ({local:%a %H:%M} CT)"
    return (f"closed - {found.name} ({found.kind}), "
            f"reopens {found.until:%a %Y-%m-%d %H:%M} UTC")


def _audit(year):
    print(f"CME Globex gold (GC) - declared closures around {year}")
    print(f"{'local (CT)':<30} {'UTC':<30} name")
    for start, end, name in _closures_near(year):
        print(f"{start:%Y-%m-%d %H:%M %Z}  ->  "
              f"{to_utc(start):%Y-%m-%d %H:%M} .. {to_utc(end):%Y-%m-%d %H:%M}  {name}")
    print()
    print("Weekly rule : open Sun 17:00 CT, close Fri 16:00 CT")
    print("Daily rule  : break 16:00-17:00 CT")
    print(f"Session     : {SESSION_OPEN_HOUR:02d}:00-{SESSION_CLOSE_HOUR:02d}:00 CT "
          f"({SESSION_HOURS}h) on Sun-Thu")
    print(f"Flatten     : {SESSION_CLOSE_HOUR - FLAT_BEFORE_CLOSE_HOURS:02d}:00 CT "
          f"({session_exit_hour(pd.Timestamp(f'{year}-07-01 12:00', tz=UTC)):02d}:00 UTC"
          f" on CDT, "
          f"{session_exit_hour(pd.Timestamp(f'{year}-12-01 12:00', tz=UTC)):02d}:00 UTC"
          f" on CST)")
    _audit_sessions(pd.Timestamp(f"{year}-06-20 12:00", tz=UTC), 2)
    _audit_sessions(pd.Timestamp(f"{year}-12-15 12:00", tz=UTC), 2)
    print()
    print("Verify these windows against https://www.cmegroup.com/trading-hours.html")


def _audit_sessions(anchor, days):
    print()
    print(f"Asia Grab session windows near {anchor:%Y-%m-%d} "
          f"(one CT rule, two UTC offsets)")
    for back in range(days, -days, -1):
        day = anchor.date() - timedelta(days=back)
        window = session_open_on(day)
        if window is None:
            continue
        start, end = window
        print(f"  opens {day:%a %Y-%m-%d} 17:00 CT ({to_ct(start):%Z})  "
              f"->  {start:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M} UTC")


def main():
    p = argparse.ArgumentParser(description="CME Globex gold market calendar")
    p.add_argument("--year", type=int, default=None,
                   help="print every declared closure near this year")
    p.add_argument("--check", default=None,
                   help='explain one moment, e.g. "2026-09-05 00:58" (UTC)')
    p.add_argument("--session", default=None,
                   help='explain the Asia session at a moment, e.g. "2026-12-09 23:30"')
    p.add_argument("--flat", default=None,
                   help='explain the flatten hour at a moment, e.g. "2026-12-10 03:00"')
    p.add_argument("--week", default=None,
                   help='print a completed Globex week as of a moment')
    args = p.parse_args()

    if args.session:
        stamp = pd.Timestamp(args.session, tz=UTC)
        print(f"{stamp:%Y-%m-%d %H:%M} UTC -> {describe_session(stamp)}")
        window = session_window(stamp)
        if window is not None:
            flat = window[1] - pd.Timedelta(hours=FLAT_BEFORE_CLOSE_HOURS)
            print(f"session flatten: {flat:%Y-%m-%d %H:%M} UTC "
                  f"({to_ct(flat):%H:%M} CT)")
        return
    if args.flat:
        stamp = pd.Timestamp(args.flat, tz=UTC)
        print(f"{stamp:%Y-%m-%d %H:%M} UTC ({to_ct(stamp):%a %H:%M %Z}) "
              f"-> flatten hour {session_exit_hour(stamp):02d}:00 UTC")
        return
    if args.week:
        stamp = pd.Timestamp(args.week, tz=UTC)
        windows, anchor = week_sessions(stamp)
        print(f"completed week as of {stamp:%Y-%m-%d %H:%M} UTC, "
              f"anchor {anchor:%Y-%m-%d %H:%M} UTC")
        for start, end in windows:
            trade_date = to_ct(end).date()
            print(f"  {to_ct(start):%a %Y-%m-%d %H:%M} CT -> "
                  f"{to_ct(end):%a %Y-%m-%d %H:%M} CT | "
                  f"{start:%Y-%m-%d %H:%M} .. {end:%Y-%m-%d %H:%M} UTC | "
                  f"trade date {trade_date:%Y-%m-%d}")
        return
    if args.check:
        stamp = pd.Timestamp(args.check, tz=UTC)
        print(f"{stamp:%Y-%m-%d %H:%M} UTC -> {describe(stamp)}")
        print(f"next open: {next_open(stamp):%Y-%m-%d %H:%M} UTC")
        print(f"session  : {describe_session(stamp)}")
        return
    _audit(args.year if args.year else pd.Timestamp.now(tz=UTC).year)


if __name__ == "__main__":
    main()
