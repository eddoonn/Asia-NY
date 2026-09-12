"""Weekly Asia Grab digest.

One short message that says what the strategy actually did all week, so a quiet
alert channel can be told apart from a strategy that decided not to trade:
which sessions ran, which were market closures, and which produced no sweep.

Every session of the week is classified as exactly one of:

    trade      a sweep fired and the trade is reported
    no-sweep   the market traded and levels existed, but no sweep reached them
    no-levels  no NY-late window for the session, so nothing could be armed
    no-data    the market was open but the feed had no bars for the session
    closed     the market was shut (weekly close or a holiday)

Anchoring: a week is the five sessions opening Sun..Thu at the 17:00 CT Globex
open, so the last one closes Friday 05:00 CT and the digest runs from there.
The windows come from `market_calendar`, so they are 22:00-10:00 UTC in summer
and 23:00-11:00 UTC in winter.

Driven from notify.py so there is one webhook and one state file:

    python notify.py --digest
    python notify.py --digest --force
    python notify.py --digest --as-of "2026-09-12 09:00"    # backfill
"""
import argparse
from collections import Counter

import pandas as pd

import discord_style as style
from backtest import load_data
from market_calendar import (SESSION_HOURS, SESSION_OPEN_HOUR, session_exit_hour,
                             to_ct, week_sessions)
from market_calendar import closure as market_closure
from market_calendar import closures_between
from notify import (ATR_LEN, ATR_MULT, BUF, COST, GRAY, GREEN, NY_LATE, RED, RR,
                    STATE_PATH, load_state, load_webhook, reference_levels,
                    remember, send, session_ids, traded_as)
from discord_style import reason_words
from strategy import add_atr, find_trades, ny_levels

SAMPLE = pd.Timedelta(minutes=30)
CLOSED_SHARE = 0.99

STATUS_LABEL = {
    "trade": "traded",
    "no-sweep": "no sweep",
    "no-levels": "no levels",
    "no-data": "no data",
    "closed": "closed",
}


def safe_print(text=""):
    """Print without dying on a console that cannot encode the arrows/dashes."""
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"))


def closure_share(start, end):
    """(fraction of the window the market was shut, dominant closure name)."""
    points = pd.date_range(start, end, freq=SAMPLE, inclusive="left")
    if len(points) == 0:
        return 0.0, None
    found = [market_closure(point) for point in points]
    shut = [f for f in found if f is not None]
    if not shut:
        return 0.0, None
    names = [f.name for f in shut]
    return len(shut) / len(points), max(set(names), key=names.count)


def session_report(window, index, levels, trades):
    start, end = window
    shut, name = closure_share(start, end)
    bars = int(((index >= start) & (index < end)).sum())
    half_day = pd.Timedelta(hours=SESSION_HOURS)
    around = (((index >= start - half_day) & (index < start))
              | ((index >= end) & (index < end + half_day)))
    ref = reference_levels(levels, index, window)
    trade = next((t for t in trades if start <= t["entry_time"] < end), None)

    if shut >= CLOSED_SHARE:
        status = "closed"
    elif bars == 0:
        status = "no-data"
    elif ref is None:
        status = "no-levels"
    elif trade is None:
        status = "no-sweep"
    else:
        status = "trade"

    return {"start": start, "end": end, "status": status, "bars": bars,
            "neighbour_bars": int(around.sum()), "ref": ref, "trade": trade,
            "closure": name, "closed_share": shut,
            # Labelled by trade date - the day the session's daytime half falls
            # on, which is the exchange's own convention and what the daily
            # alerts already show.
            "trade_date": to_ct(end).date()}


def session_reports(df, now=None, exit_hour=None):
    """(week_start, week_end, reports) for the most recently completed week.

    `exit_hour` defaults to the session-relative flatten (03:00 CT), which is the
    hour the strategy and `end_session.py` both use.
    """
    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    if exit_hour is None:
        exit_hour = session_exit_hour(now)
    windows, week_end = week_sessions(now)
    index = df.index
    mask, aid = session_ids(index)
    levels = ny_levels(index, df["High"].values, df["Low"].values, NY_LATE)
    trades = find_trades(df, mask, aid, levels, RR, ATR_MULT, BUF, "stop", "rr",
                         exit_hour, atr=df["atr"].values, cost=COST)
    reports = [session_report(window, index, levels, trades) for window in windows]
    return windows[0][0], week_end, reports


def summarise(reports):
    counts = Counter(r["status"] for r in reports)
    traded = [r for r in reports if r["status"] == "trade"]
    net = sum(r["trade"]["r"] for r in traded)
    return counts, traded, net


def _describe_session(report):
    """One line per session, in the same words the daily alerts use.

    Labelled by its trade date - the day the session's daytime half falls on,
    which is the exchange's own convention and what the Discord alerts show.
    """
    when = f"{report['trade_date']:%a %m-%d}"
    status = report["status"]
    if status == "trade":
        trade = report["trade"]
        entry = f"{trade['entry_time']:%H:%M}"
        if trade["entry_time"].date() != report["trade_date"]:
            entry = f"{trade['entry_time']:%m-%d %H:%M}"
        return (f"`{when}` {trade['side'].upper()} {entry} · "
                f"{trade['r']:+.2f}R ({reason_words(trade['reason'])})")
    if status == "no-sweep":
        ref = report["ref"]
        return f"`{when}` no sweep · range {ref[0]:.2f} / {ref[1]:.2f}"
    if status == "no-levels":
        return f"`{when}` not armed · no NY-late range to sweep"
    if status == "no-data":
        if report["bars"] == 0 and report["neighbour_bars"] == 0:
            return (f"`{when}` no data · feed silent either side "
                    f"(check the calendar)")
        return f"`{when}` no data for the session"
    return f"`{when}` market closed · {report['closure']}"


def build_digest_embed(week_start, week_end, reports, symbol, now=None):
    counts, traded, net = summarise(reports)
    total = len(reports)
    # Only the categories that happened: a week with no holidays should not
    # spend a line saying there were no holidays.
    headline = [f"{total} sessions"]
    if counts["trade"]:
        headline.append(f"{counts['trade']} traded")
        headline.append(f"net {net:+.2f}R")
    for key, label in (("no-sweep", "no sweep"), ("no-levels", "not armed"),
                       ("closed", "closed"), ("no-data", "no data")):
        if counts[key]:
            headline.append(f"{counts[key]} {label}")

    lines = [_describe_session(r) for r in reports]
    description = "`" + " · ".join(headline) + "`\n" + "\n".join(lines)

    embed = {
        "title": style.title("weekly digest",
                            f"{week_start:%b %d}-{week_end:%b %d}"),
        "color": GREEN if net > 0 else (RED if net < 0 else GRAY),
        "description": description,
    }

    holidays = closures_between(week_start, week_end)
    if holidays:
        embed["fields"] = [{
            "name": "Market closures",
            "value": "\n".join(f"{name}: {start:%a %H:%M} to {end:%a %H:%M} UTC"
                               for start, end, name in holidays)[:1024],
            "inline": False,
        }]

    embed["footer"] = {"text": style.rule(
        traded_as(symbol),
        f"sessions {SESSION_HOURS}h from the {SESSION_OPEN_HOUR:02d}:00 CT open",
        f"TP {RR}R")}
    return embed, {"net_r": round(net, 2), "counts": dict(counts),
                   "week_end": week_end.isoformat()}


def run_digest(args, webhook):
    now = (pd.Timestamp(args.as_of, tz="UTC") if args.as_of
           else pd.Timestamp.now(tz="UTC"))
    df = load_data(args.symbol, "12d", "60m")
    add_atr(df, ATR_LEN)
    week_start, week_end, reports = session_reports(
        df, now=now, exit_hour=getattr(args, "exit_hour", None))
    embed, info = build_digest_embed(week_start, week_end, reports,
                                     args.symbol, now=now)

    key = f"digest|{week_end.isoformat()}"
    if getattr(args, "dry_run", False):
        safe_print(f"DRY RUN — week ending {week_end:%Y-%m-%d}, key {key}")
        safe_print(f"  {embed['title']}")
        safe_print(f"  colour {embed['color']:#06x}")
        for line in embed["description"].splitlines():
            safe_print(f"  {line}")
        for field in embed.get("fields", []):
            safe_print(f"  [{field['name']}]")
            for line in field["value"].splitlines():
                safe_print(f"    {line}")
        safe_print(f"  footer: {embed['footer']['text']}")
        return

    previous = {} if args.no_state else load_state(args.state_path)
    if not args.force and previous.get("digest") == key:
        print(f"Digest for the week ending {week_end:%Y-%m-%d} already sent - skipping")
        return

    status = send(webhook, embed)
    print(f"Digest sent, HTTP {status} - {key}")
    print(f"  {info['counts']} net {info['net_r']:+.2f}R")
    if not args.no_state:
        remember("digest", key, args.state_path,
                 digest_posted_at=now.isoformat())


def main():
    p = argparse.ArgumentParser(description="Send the weekly Asia Grab digest")
    p.add_argument("--symbol", default="GC=F")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-state", action="store_true")
    p.add_argument("--state-path", default=STATE_PATH)
    p.add_argument("--as-of", default=None)
    p.add_argument("--exit-hour", type=int, default=None,
                   help="UTC hour the strategy flattens at (default 03:00 CT)")
    p.add_argument("--dry-run", action="store_true",
                   help="render the digest and print it without sending")
    args = p.parse_args()
    webhook = load_webhook()
    if not webhook and not args.dry_run:
        raise SystemExit("No webhook found. Put DISCORD_WEBHOOK=... in .env")
    run_digest(args, webhook)


if __name__ == "__main__":
    main()
