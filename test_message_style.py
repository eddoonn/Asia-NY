"""Every Discord message the stack can send, checked against the house style.

The messages are built in five scripts, and `discord_style.problems()` is the
single definition of the style, so this file is what makes "applies to all of
them" true rather than aspirational: each shape is built for real and validated.
It also fails if any script ever hands a dict straight to `send()`, because such
a message is invisible to this test.

    python test_message_style.py            # validate every message
    python test_message_style.py --show     # print them the way Discord does
"""
from __future__ import annotations

import pathlib
import re
import sys

import pandas as pd

import discord_style as style
import end_session
import notify
import place_orders
import reclaim_monitor
from discord_style import problems, render
from test_notify_session_state import (frame, live_rows, midweek_rows, stale_rows,
                                       ts)
from test_weekly_digest import reports
from weekly_digest import build_digest_embed, safe_print

# The scripts that can post to the channel, and the one builder per message
# shape in each.  A shape missing from here is a message nobody checks.
SCRIPTS = ["notify.py", "weekly_digest.py", "place_orders.py",
           "reclaim_monitor.py", "end_session.py", "post_samples.py"]


def notify_messages():
    """(name, embed) for every shape `notify.py` can post."""
    out = []
    df = frame(midweek_rows())
    closed = notify.session_state(df, now=ts("2026-09-09 09:00"))
    live = notify.session_state(frame(live_rows()), now=ts("2026-09-09 01:05"))
    armed_state = notify.session_state(frame(midweek_rows()[:5]),
                                       now=ts("2026-09-09 00:30"))
    idle = notify.session_state(df, now=ts("2026-09-09 12:00"))
    stale = notify.session_state(frame(stale_rows()), now=ts("2026-09-09 05:00"))
    shut = notify.session_state(frame(stale_rows()), now=ts("2026-09-05 00:58"))
    no_ref = dict(closed, ref=None, trade=None)

    last = float(df["Close"].iloc[-1])
    out.append(("notify: armed", notify.build_embed(df, armed_state, "GC=F")))
    out.append(("notify: result", notify.build_embed(df, closed, "GC=F")))
    out.append(("notify: open", notify.build_embed(frame(live_rows()), live, "GC=F")))
    out.append(("notify: idle", notify.build_embed(df, idle, "GC=F")))
    out.append(("notify: stale", notify.build_embed(stale and frame(stale_rows()),
                                                    stale, "GC=F")))
    out.append(("notify: closed market", notify.build_embed(
        frame(stale_rows()), shut, "GC=F")))
    out.append(("notify: no reference", notify.build_embed(df, no_ref, "GC=F")))
    out.append(("notify: entry alert", notify.trigger_payload(
        live, "GC=F", last_px=last)[1]))
    out.append(("notify: entry test", notify.sample_trigger_payload(
        live, "GC=F", last_px=last)[1]))
    out.append(("notify: webhook hello", notify.hello_embed()))
    return out


def digest_messages():
    week_start, week_end, found = reports()
    embed, _info = build_digest_embed(week_start, week_end, found, "GC=F")
    return [("weekly digest", embed, 12)]


def order_messages():
    lines = [
        ("GOLD.24-7", "SHORT 4,560.32 · LONG 4,486.08 · SL 38.77 · TP 29.08"),
        ("EURUSD", "SHORT 1.16253 · LONG 1.15871 · SL 0.00173 · TP 0.00130"),
        ("GBPUSD", "SHORT 1.34102 · LONG 1.33688 · SL 0.00190 · TP 0.00143"),
        ("USDJPY", "SHORT 153.655 · LONG 152.926 · SL 0.318 · TP 0.239"),
    ]
    sweep = [
        dict(profile="tokyo", symbol="USDJPY", side="long", entry=153.6550,
             sl=152.9258, tp=154.0190),
        dict(profile="tokyo", symbol="EURJPY", side="long", entry=178.7440,
             sl=178.0856, tp=179.0090),
        dict(profile="tokyo", symbol="GBPJPY", side="long", entry=208.0860,
             sl=207.3883, tp=208.5330),
    ]
    ig_results = [("SELL", 4560.32, {"orderId": 1}),
                  ("BUY", 4486.08, {"orderId": 2})]
    trades = [dict(symbol="GOLD.24-7", side="SHORT", open_rate=4392.81,
                   exit_rate=4411.62, pnl=-21.30, r=-1.02, reason="sl",
                   open_ts="2026-09-11T06:00", close_ts="2026-09-11T07:12")]
    no_fills = [dict(symbol="GOLD.24-7", side="LONG")]
    errors = [dict(error="eToro returned 401 for the portfolio call")]
    # The widest footer the stack can build: the whole four-instrument eToro
    # ladder declined, so the label is the longest it ever gets.
    ladder = [dict(symbol=s) for s in
              ("GOLD.24-7", "EURUSD", "GBPUSD", "USDJPY")]
    flat = dict(symbol="USDJPY", side="LONG")
    return [
        ("orders: declined", place_orders.declined_embed(
            "No NY-late range closed in time for the 2026-09-11 session.",
            ["GOLD.24-7", "EURUSD", "GBPUSD", "USDJPY"])),
        ("orders: declined, closed market", place_orders.declined_embed(
            "Market closed - Thanksgiving, reopens Fri 2026-11-27 23:00 UTC.")),
        ("orders: eToro armed", place_orders.etoro_orders_embed(
            lines, 100.0, len(lines), 1.0, "each",
            "session 09-10 22:00-10:00 UTC", len(lines))),
        ("orders: IG armed", place_orders.ig_orders_embed(
            ig_results, 1.53, "GBP", 38.77, 29.08, "CS.D.USGLD.CFD.IP", 1.0, 100.0)),
        ("sweep reclaim", reclaim_monitor.sweep_embed(sweep, 100)),
        ("session result", end_session.result_embed(
            trades, no_fills, errors, -21.30, -1.02, False,
            _session_footer(trades + no_fills))),
        # A whole ladder that filled nothing: the footer is the only place the
        # tickers can appear, so this is the case the label has to survive.
        ("session no trade", end_session.no_trade_embed(_session_footer(ladder))),
        # ...and a session that traded something other than gold, so the label
        # is proved to be read off the fills instead of hardcoded.
        ("session no trade, FX", end_session.no_trade_embed(
            _session_footer([flat]))),
        ("session cleanup", end_session.cleanup_embed(["d1", "d2"], ["p1"],
                                                      "CS.D.USGLD.CFD.IP")),
    ]


def _session_footer(results):
    """The real footer `end_session.py` builds, so its length is checked."""
    return " · ".join([end_session.instrument_label(results), "eToro demo",
                       "risk 100 USD/trade", "tokyo flat 08:00 UTC"])


def every_message():
    """Every shape, as `(name, embed, max_description_lines)`."""
    out = [(name, embed, 3) for name, embed in notify_messages()]
    out += [(name, embed, 3) for name, embed in order_messages()]
    out += digest_messages()
    return out


def test_every_message_shape_the_stack_can_send_meets_the_style():
    broken = {}
    for name, embed, max_lines in every_message():
        found = problems(embed, max_lines=max_lines)
        if found:
            broken[name] = found
    assert broken == {}, broken


def test_every_message_about_a_trade_names_the_instrument():
    """A trade message has to say which ticker it is talking about.

    The signal symbol and the broker ticker are different vocabularies - levels
    come from `GC=F`, the order goes to `GOLD.24-7` - so a message that names
    only one of them is ambiguous about what to buy or sell.
    """
    # Shapes that describe a trade, and the ticker each must carry.  The hello
    # and the digest headline are not trades; a multi-instrument list is
    # covered by the rows themselves.
    must_name = {
        "notify: armed": "GC=F → GOLD.24-7",
        "notify: result": "GC=F → GOLD.24-7",
        "notify: open": "GC=F → GOLD.24-7",
        "notify: idle": "GC=F → GOLD.24-7",
        "notify: stale": "GC=F → GOLD.24-7",
        "notify: closed market": "GC=F → GOLD.24-7",
        "notify: no reference": "GC=F → GOLD.24-7",
        "notify: entry alert": "GC=F → GOLD.24-7",
        "notify: entry test": "GC=F → GOLD.24-7",
        "orders: declined": "GOLD.24-7",
        # No instruments were passed, so this is the broker default and the
        # shape that proves the decline names something without being told to.
        "orders: declined, closed market": "CS.D.USGLD.CFD.IP",
        "orders: IG armed": "CS.D.USGLD.CFD.IP",
        "orders: eToro armed": "GOLD.24-7",
        "sweep reclaim": "USDJPY",
        "session result": "GOLD.24-7",
        "session no trade": "GOLD.24-7",
        "session no trade, FX": "USDJPY",
        "session cleanup": "CS.D.USGLD.CFD.IP",
        "weekly digest": "GC=F → GOLD.24-7",
    }
    # The handshake is the one message with no trade behind it.
    not_a_trade = {"notify: webhook hello"}
    shapes = {name: render(embed) for name, embed, _l in every_message()}
    assert set(must_name) | not_a_trade == set(shapes), (
        set(must_name) ^ (set(shapes) - not_a_trade))
    for name, ticker in must_name.items():
        assert ticker in shapes[name], f"{name} does not name {ticker!r}:\n{shapes[name]}"


def test_a_signal_the_broker_lists_under_its_own_name_is_not_quoted_twice():
    """The arrow only appears when the two vocabularies actually differ.

    A signal nobody orders, and one whose broker ticker *is* the symbol, are
    each quoted once - the point is to name the traded instrument, not to print
    an arrow everywhere.
    """
    assert notify.traded_as("GC=F") == "GC=F → GOLD.24-7"
    assert notify.traded_as("EURUSD=X") == "EURUSD=X → EURUSD"
    assert notify.traded_as("EURUSD") == "EURUSD"
    assert notify.traded_as("UNORDERED") == "UNORDERED"


def test_every_shape_can_be_previewed_and_is_labelled_a_sample():
    """`post_samples.py` shows the real copy, so each shape needs a label.

    A sample that is not marked as one is worse than no sample: these land in
    the same channel the live signals do.
    """
    import post_samples

    labels = [post_samples.sample_label(f"{i}/18", name)
              for i, (name, _e, _l) in enumerate(every_message(), start=1)]
    assert len(set(labels)) == len(labels), "two shapes share a sample label"
    for name, label in zip([n for n, _e, _l in every_message()], labels):
        assert name in label, f"the sample label does not name {name!r}: {label!r}"
        assert "not a live signal" in label, label


def test_every_shape_posts_through_a_builder_not_a_dict_literal():
    """A dict handed straight to `send()` is a message no test can see."""
    offenders = []
    for script in SCRIPTS:
        source = pathlib.Path(script).read_text()
        for match in re.finditer(r'send\([^)]*\{"title"', source):
            line = source[:match.start()].count("\n") + 1
            offenders.append(f"{script}:{line}")
    assert offenders == [], offenders


def test_the_validator_catches_a_breach_of_each_rule():
    """A style test that cannot fail proves nothing, so break it on purpose."""
    good = {"title": style.title("GC=F", "ARMED"), "color": style.GRAY,
            "description": "One trade this session.", "fields": [],
            "footer": {"text": "session 2026-09-04"}}
    assert problems(good) == [], problems(good)

    cases = {
        "em dash": dict(good, description="Closed — at the flatten."),
        "unbranded title": dict(good, title="Asia Grab armed"),
        "state in the title is not capped": dict(good, title="Asia Grab · GC=F · armed"),
        "long description": dict(good, description="\n".join(["x"] * 4)),
        "too many fields": dict(good, fields=[
            {"name": f"f{i}", "value": "1", "inline": True} for i in range(6)]),
        "long footer": dict(good, footer={"text": "y" * 96}),
        "a price twice in one line": dict(good, fields=[
            {"name": "Entry", "value": "4392.81 / 4392.81", "inline": True}]),
        "the flat stated twice": dict(good, fields=[
            {"name": "Session", "value": "Flat 08:00 UTC", "inline": False}],
            footer={"text": "flat 08:00 UTC"}),
        "the ATR stated twice": dict(good, fields=[
            {"name": "Entry", "value": "4374.00 + 1xATR10", "inline": True}],
            footer={"text": "buf 1xATR10"}),
    }
    for label, embed in cases.items():
        assert problems(embed), f"the validator missed: {label}"

    # ...and does not fire where a number legitimately repeats: an R multiple is
    # a rule that also happens to be the result, and a one-trade session's total
    # is that trade's own P&L
    fine = dict(good, title=style.title("GC=F", "SHORT CLOSED +0.75R (target hit)"),
                fields=[{"name": "Session", "value": "Flat 08:00 UTC · TP 0.75R",
                         "inline": False},
                        {"name": "P&L", "value": "**+21.50 USD** (+0.75R)",
                         "inline": False}])
    assert problems(fine) == [], problems(fine)

    # ...and the last price may sit exactly on a level it also quotes
    on_the_level = dict(good, description="NY low swept · 89.00 - 1xATR10 (3.67)",
                        fields=[{"name": "NY-late range",
                                 "value": "95.00 / 89.00", "inline": True},
                                {"name": "Last price", "value": "89.00",
                                 "inline": True}],
                        footer={"text": "session 2026-09-08 · last 89.00"})
    assert problems(on_the_level) == [], problems(on_the_level)


def main():
    if "--show" in sys.argv:
        # safe_print: the console on Windows is not UTF-8 and the copy uses
        # `·` and `→`
        for name, embed, _lines in every_message():
            safe_print(f"\n=== {name} " + "=" * max(0, 60 - len(name)))
            safe_print(render(embed))
        return 0

    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as exc:
            failures += 1
            print(f"FAIL  {name}: {exc}")
    shaped = len(every_message())
    print(f"\n{len(tests) - failures}/{len(tests)} passed, "
          f"{shaped} message shapes checked")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
