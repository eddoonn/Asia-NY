"""Tests for the Discord-log transcript and the session-timeline audit.

Most of these need no network and no market data: the transcript's arithmetic is
checkable on its own, and `source_of` is driven by a synthetic frame.  The ones
that do need the cached GC=F bars say so and skip cleanly when it is absent.

    python test_reconcile_timeline.py
"""
import os

import pandas as pd

from discord_log import (ARMED, FILLS, FIRST_DUPLICATE, NOTES, SWEEPS, TRADES,
                         buffer_of, implied)
from market_calendar import session_window


def ts(text):
    return pd.Timestamp(text, tz="UTC")


# ---------------------------------------------------------------------------
# the transcript, offline
def test_every_posting_quotes_a_symmetric_buffer():
    """`short - high` must equal `low - long`: both are one ATR10."""
    for i, p in enumerate(ARMED, 1):
        up = p["short"] - p["hi"]
        down = p["lo"] - p["long"]
        assert abs(up - down) <= 0.02, (i, p["at"], up, down)


def test_every_trade_level_is_a_level_the_channel_quoted():
    """Each trade's implied reference is a high (short) or low (long) on show.

    Eleven of the twelve: the 2026-08-26 trade's session is the one session whose
    armed postings are not in the paste - they start on 08-29 - so that one is
    checked against the tape by the ledger instead.
    """
    quoted_hi = {p["hi"] for p in ARMED}
    quoted_lo = {p["lo"] for p in ARMED}
    found, missing = 0, []
    for stamp, side, entry, stop, _tp, _reason, _r in TRADES:
        level, _atr = implied(dict(entry=entry, sl=stop, side=side))
        pool = quoted_hi if side == "short" else quoted_lo
        if any(abs(level - q) <= 0.02 for q in pool):
            found += 1
        else:
            missing.append(stamp)
    assert found == 11, found
    assert missing == ["2026-08-26 23:00"], missing


def test_the_duplicates_start_where_the_second_dispatcher_registered():
    """One posting before the switch, none of them afterwards - that is the bug."""
    singles = [p["at"] for p in ARMED if p["copies"] == 1]
    doubles = [p["at"] for p in ARMED if p["copies"] == 2]
    assert max(singles) < FIRST_DUPLICATE, max(singles)
    assert min(doubles) == FIRST_DUPLICATE, min(doubles)
    assert NOTES[0]["text"].startswith("Asia Grab online")
    assert NOTES[0]["at"][:10] < FIRST_DUPLICATE[:10]


def test_the_transcript_covers_the_period_it_claims():
    """No trade before the first posting, and every leg is a priced order."""
    first = min(p["at"] for p in ARMED)
    assert first <= "2026-08-29 04:41", first
    assert len(TRADES) == 12 and len(ARMED) == 19
    assert sum(len(s["legs"]) for s in SWEEPS) == 8
    assert len(FILLS) == 1
    for sweep in SWEEPS:
        for symbol, side, entry, sl, tp in sweep["legs"]:
            assert side in ("long", "short")
            assert symbol.isupper() and len(symbol) == 6
            assert sl != entry and tp != entry


def test_buffer_of_is_the_atr_either_trigger_implies():
    for p in ARMED:
        assert abs(buffer_of(p) - (p["short"] - p["hi"])) <= 0.02
        assert abs(buffer_of(p) - (p["lo"] - p["long"])) <= 0.02


# ---------------------------------------------------------------------------
# source_of, on a synthetic frame
def synth_frame():
    """Four CDT days: a tagged NY-late window and a session either side.

    Each day's 19:00-21:00 UTC window gets its own high/low so the level pairs
    are distinguishable, and the session half (22:00 UTC to 10:00 UTC) sits well
    outside them, so only the reference window can produce a quoted pair.
    """
    index, high, low = [], [], []
    days = pd.date_range("2026-09-01", periods=4, freq="D", tz="UTC")
    for i, day in enumerate(days):
        hi, lo = 4000.0 + 100 * i + 5, 4000.0 + 100 * i - 5
        for hour in (19, 20):
            index.append(day + pd.Timedelta(hours=hour))
            high.append(hi)
            low.append(lo)
        for hour in list(range(22, 24)) + list(range(0, 10)):
            when = day + pd.Timedelta(hours=hour if hour >= 22 else 24 + hour)
            if when > days[-1] + pd.Timedelta(hours=10):
                continue
            index.append(when)
            high.append(hi + 30)
            low.append(lo - 30)
    mid = [(h + l) / 2 for h, l in zip(high, low)]
    frame = pd.DataFrame({"Open": mid, "High": high, "Low": low, "Close": mid},
                         index=pd.DatetimeIndex(index))
    return frame.sort_index()


def test_source_of_counts_sessions_back_to_the_window_it_read():
    import reconcile_timeline as rt

    frame = synth_frame()
    levels = rt.levels_for(frame, "ct")
    when = ts("2026-09-03 23:00")          # inside the session opening 09-03
    state = session_window(when)
    assert state is not None

    own = rt.ref_for(levels, frame, "ct", state)
    assert own is not None
    _w, _r, back = rt.source_of(frame, levels, when, own[0], own[1], state)
    assert back == 0, back

    # the previous session's window is one session older
    prev = rt.ref_for(levels, frame, "ct",
                      (state[0] - pd.Timedelta(days=1), state[0]))
    assert prev is not None and abs(prev[1] - own[1]) > 0.6
    _w, _r, back = rt.source_of(frame, levels, when, prev[0], prev[1], state)
    assert back == 1, back

    # with no session running there is nothing to count from
    _w, _r, back = rt.source_of(frame, levels, when, prev[0], prev[1], None)
    assert back is None, back

    # a pair that is on no window at all is reported as such
    assert rt.source_of(frame, levels, when, 1.0, 2.0, state) == (None, None, None)


# ---------------------------------------------------------------------------
# the audit, against the cached bars
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     ".cache", "GC_F_730d_60m.pkl")


def has_cache():
    """True when the cached GC=F bars are available (the audit needs them)."""
    return os.path.exists(CACHE)


def test_the_offset_is_pinned_and_the_verdicts_do_not_depend_on_it():
    import collections

    import reconcile_timeline as rt

    if not has_cache():
        print("      (skipped: no cached GC=F bars)")
        return
    frame = rt.load_frame()
    levels = rt.levels_for(frame, "ct")
    verdicts = {off: collections.Counter(r["verdict"] for r in
                                        rt.armed_postings(frame, levels, offset=off))
                for off in (1, 2, 3)}
    assert verdicts[1] == verdicts[2], verdicts

    rows = rt.armed_postings(frame, levels, offset=2)
    counts = collections.Counter(r["verdict"] for r in rows)
    assert counts["on time"] == 9 and counts["outside-session"] == 9, counts
    assert counts["stale"] == 1, counts

    # the buffer is 1.0xATR10, so it names the offset: +2 is the only one that
    # lands within a third of a tick on the postings whose ATR is not flat
    wins = []
    for r in rows:
        close = {off for off, (_bar, _atr, gap) in r["probe"].items() if gap < 0.30}
        if close == {2}:
            wins.append(r["n"])
    assert wins == [7, 8, 17, 18, 19], wins


def test_both_reference_clocks_pick_the_same_window_all_through_the_log():
    import reconcile_timeline as rt

    if not has_cache():
        print("      (skipped: no cached GC=F bars)")
        return
    frame = rt.load_frame()
    levels_ct = rt.levels_for(frame, "ct")
    levels_utc = rt.levels_for(frame, "utc")
    windows = [w for w in rt.session_windows(rt.SINCE, rt.UNTIL) if w[0] >= rt.SINCE]
    assert len(windows) == 12, len(windows)
    for window in windows:
        a = rt.ref_for(levels_ct, frame, "ct", window)
        b = rt.ref_for(levels_utc, frame, "utc", window)
        assert rt.same(a, b), (window, a, b)

    # ...and they part company the first time the session opens at 23:00 UTC
    later = rt.session_windows(rt.UNTIL, rt.UNTIL + pd.Timedelta(days=120))
    first = next(w for w in later if w[0].hour == 23)
    assert first[0] == ts("2026-11-01 23:00"), first


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
        except Exception as exc:                        # noqa: BLE001
            failures += 1
            print(f"ERROR {fn.__name__}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} checks pass")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
