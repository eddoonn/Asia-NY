"""The Discord log, transcribed.

The `Asia Gold APP` / `new new` channel pasted by the user, as data.  One entry
per *posting*: the log shows most postings twice from 2026-09-03 onward (a second
dispatcher was registered at 2026-09-02 22:22 - "Asia Grab online / Webhook
test"), so `copies` records how many times the same content appears rather than
duplicating the row.

About the timestamps
--------------------
Discord renders message times in the *reader's* local zone, and this log has no
zone attached, so `at` is the printed clock and must not be read as UTC.  It is
still usable as an ordering, and the date is right to within a day.  Everything
the audit depends on is content:

* the quoted NY-late high/low and the two sweep triggers, which are the levels
  the live system was armed from;
* the explicit `Entry time (UTC)` field, which is UTC by construction.

The buffer forensics in `reconcile_timeline.py` recover the offset independently
(the triggers imply a buffer of `1.0xATR10`, and that value pins the bar the
levels were read at, which pins the UTC instant), and they land on UTC+2.

Structure
---------
`ARMED`   the "Asia Grab - GC=F armed" postings: reference levels and triggers.
`TRADES`  the realised gold trades, in the tuple shape `reconcile_log` uses.
`SWEEPS`  the FX "Sweep Reclaim - n trade(s) opened" postings (the `scalp-agent`
          profiles, not the gold session).
`FILLS`   the demo-broker fill notices.
`NOTES`   the non-signal postings, kept because they date the second dispatcher.
"""
from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# "Asia Grab - GC=F armed".  `short` sweeps above `hi`, `long` sweeps below `lo`;
# the quoted high/low is the NY-late reference the two triggers are built from.
ARMED = [
    dict(at="2026-08-29 04:41", copies=1, short=4709.75, long=4616.45,
         hi=4672.40, lo=4653.80, last=4504.10),
    dict(at="2026-08-29 14:48", copies=1, short=4711.51, long=4614.69,
         hi=4672.40, lo=4653.80, last=4529.90),
    dict(at="2026-08-30 01:11", copies=1, short=4711.51, long=4614.69,
         hi=4672.40, lo=4653.80, last=4529.90),
    dict(at="2026-08-30 14:47", copies=1, short=4711.51, long=4614.69,
         hi=4672.40, lo=4653.80, last=4529.90),
    dict(at="2026-08-31 01:28", copies=1, short=4568.67, long=4463.53,
         hi=4529.90, lo=4502.30, last=4509.80),
    dict(at="2026-09-01 02:14", copies=1, short=4518.10, long=4465.20,
         hi=4504.90, lo=4478.40, last=4500.50),
    dict(at="2026-09-02 01:02", copies=1, short=4403.47, long=4351.43,
         hi=4385.20, lo=4369.70, last=4376.80),
    dict(at="2026-09-03 01:06", copies=2, short=4455.92, long=4400.58,
         hi=4438.20, lo=4418.30, last=4432.00),
    dict(at="2026-09-04 00:59", copies=2, short=4540.97, long=4505.43,
         hi=4528.70, lo=4517.70, last=4522.50),
    dict(at="2026-09-05 00:58", copies=2, short=4560.32, long=4486.08,
         hi=4528.70, lo=4517.70, last=4477.20),
    dict(at="2026-09-05 12:48", copies=2, short=4560.32, long=4486.08,
         hi=4528.70, lo=4517.70, last=4476.60),
    dict(at="2026-09-06 00:49", copies=2, short=4560.32, long=4486.08,
         hi=4528.70, lo=4517.70, last=4476.60),
    dict(at="2026-09-06 13:06", copies=2, short=4560.32, long=4486.08,
         hi=4528.70, lo=4517.70, last=4476.60),
    dict(at="2026-09-07 00:47", copies=2, short=4516.38, long=4439.22,
         hi=4485.60, lo=4470.00, last=4476.60),
    dict(at="2026-09-07 15:13", copies=2, short=4560.32, long=4486.08,
         hi=4528.70, lo=4517.70, last=4476.60),
    dict(at="2026-09-08 01:15", copies=2, short=4560.32, long=4486.08,
         hi=4528.70, lo=4517.70, last=4476.60),
    dict(at="2026-09-09 01:06", copies=2, short=4440.93, long=4361.87,
         hi=4421.80, lo=4381.00, last=4395.10),
    dict(at="2026-09-10 01:07", copies=2, short=4470.66, long=4418.64,
         hi=4450.40, lo=4438.90, last=4437.40),
    dict(at="2026-09-11 01:03", copies=2, short=4392.97, long=4335.73,
         hi=4374.00, lo=4354.70, last=4354.40),
]

# ---------------------------------------------------------------------------
# Realised gold trades.  Same tuple shape as `reconcile_log.LOGGED`:
# (entry_time UTC, side, entry, stop, target, exit reason, R).
TRADES = [
    ("2026-08-26 23:00", "short", 4675.55, 4694.80, 4661.11, "tp", +0.73),
    ("2026-08-28 01:00", "long", 4638.26, 4622.72, 4649.91, "tp", +0.73),
    ("2026-08-28 04:00", "long", 4639.87, 4625.94, 4650.32, "sl", -1.02),
    ("2026-08-31 02:00", "long", 4472.40, 4442.50, 4494.82, "tp", +0.74),
    ("2026-09-01 07:00", "long", 4462.11, 4445.82, 4474.33, "tp", +0.73),
    ("2026-09-02 01:00", "long", 4351.63, 4333.56, 4365.18, "tp", +0.73),
    ("2026-09-03 01:00", "short", 4452.07, 4465.94, 4441.67, "tp", +0.73),
    ("2026-09-04 06:00", "long", 4506.80, 4495.90, 4514.98, "tp", +0.72),
    ("2026-09-08 06:00", "long", 4451.05, 4432.10, 4465.26, "sl", -1.02),
    ("2026-09-09 05:00", "short", 4439.46, 4457.12, 4426.21, "tp", +0.73),
    ("2026-09-10 02:00", "short", 4464.06, 4477.72, 4453.81, "tp", +0.73),
    ("2026-09-11 06:00", "short", 4392.81, 4411.62, 4378.70, "time", +0.20),
]

# ---------------------------------------------------------------------------
# "Sweep Reclaim - n trade(s) opened".  These are the FX profiles
# (`scalp-agent/strategies/sweep_reclaim.py`), not the gold session: the gold
# session is one trade per session, these open in batches per profile.
SWEEPS = [
    dict(at="2026-09-02 04:59", profile="tokyo", legs=[
        ("AUDJPY", "short", 114.4590, 114.7016, 114.3720)]),
    dict(at="2026-09-03 09:49", profile="london", legs=[
        ("EURUSD", "short", 1.1608, 1.1619, 1.1587),
        ("USDJPY", "long", 157.0500, 156.2290, 158.9680)]),
    dict(at="2026-09-04 01:05", profile="tokyo", legs=[
        ("GBPJPY", "short", 210.5530, 211.3884, 210.5030)]),
    dict(at="2026-09-07 10:14", profile="london", legs=[
        ("EURUSD", "short", 1.1625, 1.1640, 1.1612)]),
    dict(at="2026-09-09 10:31", profile="tokyo", legs=[
        ("USDJPY", "long", 153.6550, 152.9258, 154.0190),
        ("EURJPY", "long", 178.7440, 178.0856, 179.0090),
        ("GBPJPY", "long", 208.0860, 207.3883, 208.5330)]),
]

# ---------------------------------------------------------------------------
# Broker notices and non-signal postings.
FILLS = [
    dict(at="2026-09-02 22:22", symbol="GOLD.24-7", side="long", kind="MIT",
         entry=4403.47, stop=4383.47, target=4418.47, size_oz=5.2994,
         risk_usd=100.00, account="eToro demo"),
]

NOTES = [
    dict(at="2026-09-02 22:22", text="Asia Grab online / Webhook test - daily "
                                     "signals will land here."),
]

# The first posting that appears twice, i.e. when the second dispatcher starts.
FIRST_DUPLICATE = "2026-09-03 01:06"


def logged_rows():
    """The twelve realised trades, resolved to sessions like the alerts do."""
    rows = []
    for stamp, side, entry, stop, target, reason, r in TRADES:
        rows.append(dict(entry_time=pd.Timestamp(stamp, tz="UTC"), side=side,
                         entry=entry, sl=stop, tp=target, reason=reason, r=r))
    return rows


def implied(trade):
    """(reference level, ATR10) implied by an entry/stop pair.

    An entry is `reference +/- 1.0xATR10` and the stop sits a further `1.0xATR`
    away, so `|entry - stop|` is ATR10 and the level falls out of the side.
    """
    atr = abs(trade["entry"] - trade["sl"])
    level = trade["entry"] + atr if trade["side"] == "long" else trade["entry"] - atr
    return round(level, 2), round(atr, 2)


def buffer_of(row):
    """The 1.0xATR10 buffer an armed posting implies, from either trigger."""
    return round(((row["short"] - row["hi"]) + (row["lo"] - row["long"])) / 2.0, 2)


def quoted_levels():
    """Distinct (hi, lo) pairs the armed postings quote, with their postings."""
    out = {}
    for row in ARMED:
        out.setdefault((row["hi"], row["lo"]), []).append(row)
    return out


