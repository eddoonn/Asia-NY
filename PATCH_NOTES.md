# Asia Grab `notify.py` — session-state fix, CME calendar, Globex session window

Four things: the session-state bug that froze the armed triggers for three days,
a real market calendar so a closure is never mistaken for a broken feed, a session
window that follows the Globex trading day instead of a fixed UTC hour, and the
same window propagated to the scripts that place and close the orders.

## 1. Root cause of the freeze

`latest_session_state()` mixed two clocks and dropped the guard that
`strategy.find_trades` enforces.

```python
cur_id = current_asia_day_id(index)      # derived from index[-1], the last BAR

ref = None
for p in range(cur_id - 1, cur_id - 8, -1):   # 7-session lookback, unguarded
    if p in levels:
        ref = levels[p]
        break

session_trades = [t for t in trades
                  if t["entry_time"] >= current_session_start(now)]  # from the CLOCK
```

The lookback itself is **not** a bug — it is the strategy's own rule. In
`find_trades` the same walk-back is guarded:

```python
for p in range(int(did) - 1, int(did) - 8, -1):
    lv = levels.get(p)
    if lv and lv[2] < first_pos:      # the window must close BEFORE the session opens
        ref = lv
        break
if ref is None:
    continue                          # no usable window -> no trade
```

So there are four defects, and the guard is the one that bites:

1. **Two clocks.** `cur_id` came from the last data bar; `current_session_start`
   came from the wall clock. They describe different sessions whenever the feed
   lags or the market is shut.
2. **The guard was dropped.** Without `lv[2] < first_pos`, any level in the last
   seven day ids was accepted, including one whose window had not finished. That
   is what reproduced Thursday's `NY-late 4528.70 / 4517.70` and the
   `4560.32 / 4486.08` triggers on 9/5, 9/6, 9/7 and 9/8.
3. **Off-by-one before 22:00.** With `index[-1]` at 20:00, `last.hour >= 22` is
   false, so `cur_id` pointed at the session that had already *finished*, and
   `cur_id - 1` selected the window a day early.
4. **No session identity on the report.** `session_trades[-1]` was "the last
   trade whose entry was after the window start", with no upper bound and no
   session matching, so each run's answer depended on its data snapshot.
   `current_session_start` also returned *yesterday's* 22:00 for any `now` before
   22:00, including the 10:00–22:00 dead zone.

`find_trades` emits **at most one trade per session per run** (it `break`s on the
first triggered bar), verified by
`test_one_trade_per_session_even_with_two_triggerable_bars`. Two "closed"
records for one session therefore cannot come from a single run — they are two
runs disagreeing.

## 2. The market calendar

New module `market_calendar.py`. No new dependencies — it converts through
`America/Chicago` with pandas, so DST is handled for free.

The rule, from CME Group's gold futures page:

> Sunday – Friday 6:00 p.m. – 5:00 p.m. (5:00 p.m. – 4:00 p.m. CT) with a
> 60-minute break each day beginning at 5:00 p.m. (4:00 p.m. CT)

Which gives, in UTC:

| | summer (CDT) | winter (CST) |
|---|---|---|
| Sunday open | 22:00 UTC | 23:00 UTC |
| Friday close | 21:00 UTC | 22:00 UTC |
| daily break | 21:00–22:00 UTC | 22:00–23:00 UTC |

That is why the 9/4 → 9/8 quiet period was not a feed fault: the week had already
closed. `METALS_CLOSURES` carries the holiday windows as CT data (verified from
CME's published 2026 schedule for metals), and `python market_calendar.py
--year 2026` prints every one of them so the table can be diffed against the
exchange calendar in a single pass.

`notify.py` wires it in:

* `session_phase()` gained **`market-closed`**, which outranks `idle` and
  `stale`: a closure explains a quiet feed, so the feed check never gets a
  chance to cry wolf.
* `stale` now means one thing only — the market is open and the bars stopped.
* `state_key()` keys a closure on its reopen time, so a whole weekend produces
  one message rather than one per session.

## 3. Session window follows the Globex day

The session was `ASIA = (22, 10)` in UTC. That is right on CDT and wrong on CST,
because the number it should be derived from is the **exchange reopen at 17:00
CT**:

| | session open CT | open UTC | close UTC |
|---|---|---|---|
| summer (CDT) | 17:00 | 22:00 | 10:00 |
| winter (CST) | 17:00 | **23:00** | **11:00** |

So for six months of the year the strategy started an hour before metals traded,
inside the maintenance break. The window is now the first half of a Globex
trading day, derived in `market_calendar`:

* `session_window(now)` — the window containing `now`, or `None`. A session
  opens at 17:00 CT and the next at 17:00 CT the following day, so `now` is
  either in the session that opened today or the one that opened yesterday.
  Between 05:00 and 17:00 CT nothing is running.
* `session_windows(start, end)` — every window intersecting a range.
* `week_sessions(now)` — the five windows opening Sun..Thu, plus the anchor.
* `session_ids(index)` (in `notify.py`) — `(mask, ids)` grouping bars into
  sessions, a drop-in for `strategy.asia_day_ids` with the same trade-date
  numbering. `strategy.py` is untouched.

Endpoints are built from date components (`_ct(day, 17)`), never by adding a
`Timedelta`, so a window can never straddle a DST change: the change happens at
02:00 local and every session runs 17:00–05:00.

**The flatten follows the session too.** It sits two hours before the close,
which is 03:00 CT all year - so it is 08:00 UTC on CDT and **09:00 UTC on CST**,
not a fixed 08:00. `session_exit_hour(now)` supplies it, `notify.py` and
`weekly_digest.py` default to it, and `end_session.py` reads the same rule, so
the re-derivation and the live system cannot drift. `--exit-hour 8` pins the old
fixed hour for A/B runs and for reproducing the historic alerts; the September
logs are unaffected either way, because September is CDT.

This is a strategy-visible change: from November to March trades are now held an
hour longer, so winter results move. The exported October/November history wins
or loses that hour too.

**Workflow consequence.** `discord-notify.yml` fires at 22:10 UTC. In summer
that is 17:10 CT, ten minutes after the open. In winter it is 16:10 CT — still
inside the maintenance break, *before* the session opens, so that run now
correctly reports `idle` and the nightly armed message would go missing for half
the year. Add the winter cron:

```yaml
- cron: '10 22 * * *'   # 17:10 CT on CDT
- cron: '10 23 * * *'   # 17:10 CT on CST
```

Both fire every day; `state_key` dedupes, and the one that lands outside the
session takes the `idle` branch and posts nothing new.

## 4. Reference levels now match the strategy

The earlier version of this patch made `reference_levels()` demand a NY-late
window on the session's *own opening day* and never fall back. That is stricter
than `find_trades`, and it was wrong for every Monday trade date: a session
opening Sunday has no NY-late window of its own because the exchange is shut, so
the alert said "no NY-late levels" while the strategy armed from Friday and
traded.

`reference_levels()` now mirrors `find_trades` exactly — walk back from the
session's trade-date id, take the first window that closed before the session's
first bar, at most seven sessions:

```python
first_pos = int(index.searchsorted(window[0]))
did = session_day_id(window)
for p in range(did - 1, did - 1 - REF_LOOKBACK, -1):
    lv = levels.get(p)
    if lv is not None and lv[2] < first_pos:
        return lv
return None
```

The freeze cannot come back: the session is resolved from the clock, and the
guard means a window that has not finished is never used. A Sunday open arming
from Friday is not staleness, it is the strategy — Friday's window is the last
one that closed.

## 5. Weekly digest

New module `weekly_digest.py`, driven from the notifier so there is one webhook
and one state file:

```bash
python notify.py --digest                          # week just finished
python notify.py --digest --dry-run                # render, send nothing
python notify.py --digest --as-of "2026-09-05 09:00"   # backfill
```

A week is the five sessions opening Sun..Thu at the 17:00 CT Globex open, so the
last one closes Friday 05:00 CT (10:00 UTC in summer, 11:00 in winter) and the
digest is anchored there. Each session is classified as exactly one of:

| status | meaning |
|---|---|
| `trade` | a sweep fired; the trade and its R are shown |
| `no-sweep` | market open, a reference window existed, nothing reached it |
| `no-levels` | no usable NY-late window in seven sessions, so nothing could be armed |
| `no-data` | market open but the feed had no bars for the session |
| `closed` | the market was shut (weekly close or holiday) |

Sessions are labelled by **trade date** — the day the session's daytime half
falls on, which is the exchange's own convention and lines up with the entry
`Entry time (UTC)` already shown in the daily alerts.

Real output, week ending 2026-09-11:

```
Asia Grab — weekly digest (Sep 06 – Sep 11)
`5 sessions · 4 traded · 0 no sweep · 0 no levels · 0 closed · 1 no data · net +0.65R`

`Mon 09-07` no trade — no price data, and the feed is silent either side (check the calendar)
`Tue 09-08` LONG 06:00 → -1.02R (sl)
`Wed 09-09` SHORT 05:00 → +0.73R (tp)
`Thu 09-10` SHORT 02:00 → +0.73R (tp)
`Fri 09-11` SHORT 06:00 → +0.20R (time)
[Market closures]
Labor Day — Mon 18:30 to Mon 22:00 UTC
```

Backfilling the previous week reproduces the Discord log entry for entry:

```
Asia Grab — weekly digest (Aug 30 – Sep 04)
`5 sessions · 5 traded · 0 no sweep · 0 no levels · 0 closed · 0 no data · net +3.65R`

`Mon 08-31` LONG 02:00 → +0.74R (tp)
`Tue 09-01` LONG 07:00 → +0.73R (tp)
`Wed 09-02` LONG 01:00 → +0.73R (tp)
`Thu 09-03` SHORT 01:00 → +0.73R (tp)
`Fri 09-04` LONG 06:00 → +0.72R (tp)
```

Every entry time, side and R matches the alert channel, including the 9/8
`−1.02R` trade. That is the check that matters: with the reference rule aligned,
the re-derivation and the live system agree on **every** logged session, not just
the ones the bug did not touch. (Before this change the digest reported
`3 traded / net +1.66R` for that week and `4 traded / net +2.91R` for the
previous one, and it called the 9/8 −1.02R trade a phantom. It was not a
phantom — the *displayed levels* were stale, but the trade the strategy placed
was legitimate.)

The digest dedupes on `digest|<week_end>`, so a repeated run for the same week
posts nothing without `--force`.

## 5b. A triggered-trade alert

The daily runs said what was *armed* and, once the day was over, what had closed.
Nothing said **a trade had filled**, which is the one message you want while it is
still live. `notify.py --trigger` sends it:

```bash
python notify.py --trigger              # post only if a sweep has filled
python notify.py --trigger --dry-run    # render it, send nothing
```

It runs on the hourly monitor schedule, because the entry appears on the tape
whenever the sweep prints - 01:00, 02:00, 05:00, 06:00 in the log's twelve - and
stays silent otherwise:

```yaml
# asiagrab.yml, alongside the monitor step
- cron: '5 22,23,0-10,7-13 * * *'
  ...
  python notify.py --trigger
```

Rendered from the real 2026-09-11 session - the sweep bar at 06:00, one run
later, tape ending on that bar:

```
Asia Grab — GC=F TRIGGERED                        [blue]
  Setup            SHORT — NY high swept
  Status           LIVE — flat by 08:00 UTC (1h55m left)
  Entry            4392.81
  Stop             4411.62
  Target           4378.70
  Triggered at     4374.00 + 1xATR10 18.81
  Entry time (UTC) 2026-09-11 06:00:00+00:00
  buf 1xATR10 | TP 0.75R | one trade per session | session 2026-09-10 22:00-10:00 UTC | last 4394.80
```

Every number is the log's own for that session (`Entry 4392.81`, `Stop 4411.62`,
`Target 4378.70`, quoted `NY-late high 4374.00`).

Three things it does deliberately:

* **Not gated on a current feed.** The armed message needs one, because a stale
  level set must never be re-broadcast. A trigger is not a level set: once the
  sweep has printed, the entry is a fact about the tape and stays true however
  the feed behaves afterwards - which is exactly the 09-05 shape, where the feed
  went quiet with a position open. The message quotes the lag instead of
  withholding itself, and falls back to "reference no longer on the feed" rather
  than inventing a level it can no longer read.
* **Keyed on `(session, side, entry bar, entry price)`.** A *different* entry is
  a different message, not a duplicate - which is what the log's 08-28 session
  needs, since a second run with a moved ATR put a second order on the same bar
  (7e). Re-running an unchanged state posts nothing.
* **Its own state key** (`triggered`, with `trigger_session` beside it). The
  daily message owns `session`, the digest owns `digest|<week_end>`; a message
  that cannot unsend another one's record is the point of the named-key file.
  This caught a real bug while it was being written: the extra field was
  originally called `session` and clobbered the daily key - there is now a test
  for it.

It also reports a trade it arrives *after* the fact: if the first run to see the
fill is the 08:10 one, the status says `Triggered, then time +0.20R` (and goes
red or green) instead of claiming a position that has already been flattened.

Seven new tests in `test_notify_session_state.py` (31 there now): the message and
its flat deadline, exactly-one send across repeated runs, the state key not
clobbering `session`, silence while only armed, silence with no session or a shut
book, posting through a lagging feed, a finished trade reported as finished, and
the two-entries-one-session key.

## 6. Order path

`place_orders.py`, `reclaim_monitor.py` and `end_session.py` carried their own
copies of the same constants. All three now ask the calendar.

### `place_orders.py`

`session_levels()` used to derive the session from the last data bar:

```python
cur_id = ids[-1] + (1 if index[-1].hour >= ASIA[0] else 0)   # ASIA = (22, 10)
for p in range(cur_id - 1, cur_id - 8, -1):
    if p in levels:                                            # no guard
```

On a lagging feed that arms the traps from a session that has already finished,
and it never checks that the reference window closed before the open. It now
takes the session from `session_window(now)` and the reference from
`reference_levels()` - the same call the notifier makes, so orders and alerts can
never be derived from different level sets. When there is no session, or no
NY-late window that closed in time, it raises `NoSession` and posts
**"No orders tonight"** instead of placing orders against a guess:

```
No orders tonight - no Asia session at Wed 2026-07-01 07:00 CT
                   (between the 05:00 and 17:00 CT session halves)
No orders tonight - no NY-late reference that closed before the 2026-09-06 22:00 UTC session open
```

Two smaller things in the same file:

* `SKIP_SUNDAY` now asks for the **CT** market day (`to_ct(now).weekday() == 6`).
  The session opens at 17:00 CT, which is Sunday 22:00 UTC on CDT and Sunday
  23:00 UTC on CST - the same market day either way, which a UTC weekday check
  only got right by luck.
* `run_etoro` sent its confirmation with `"color": BLUE` while only `GREEN, GRAY`
  were defined, so **every "Orders placed" message raised `NameError`** after the
  orders were already resting. That is why the channel shows no "Orders placed"
  line for the 9/2 run even though the eToro fill landed. `BLUE` is now defined.

### `reclaim_monitor.py`

Profiles name their windows, and `globex` resolves through the calendar:

```python
GLOBEX = "globex"
PROFILES = [
    {"name": "tokyo",  "trigger": GLOBEX,    "reference": (19, 21), ...},
    {"name": "london", "trigger": (7, 13),   "reference": GLOBEX,   ...},
]

def profile_windows(profile, day=None):
    return (resolve_window(profile["trigger"], day),
            resolve_window(profile["reference"], day))
```

* **tokyo** - trigger `22:00-10:00` on CDT, `23:00-11:00` on CST. Its reference is
the NY-late window that closed just before, now looked up with the strategy's own
guard instead of an unguarded eight-deep day-id walk.
* **london** - trigger stays `07:00-13:00` UTC, but its reference was hardcoded to
the Asia window as `(22, 10)`; it is now `(23, 11)` in winter. London deliberately
references a range that is still forming, which is `find_trades`'
*dynamic-reference* case, so that lookup is left alone and commented as such.

The signal message footer now names the window in force
(`tokyo 23-11 UTC · risk 100/trade`), so a winter alert is self-describing.

### `end_session.py`

The workflow decided the profile from a hardcoded hour:

```yaml
H=$(date -u +%H)
if [ "$H" = "08" ]; then python end_session.py --broker etoro --profile tokyo; else ...london; fi
```

In winter the Asia flatten is 09:00 UTC, so the 08:05 run cleaned up **London**
and never touched the Asia orders at all - they stayed resting through the next
session, with the sibling leg still live. `which_profile(now)` replaces that,
and `--which-profile` lets the workflow ask instead of guessing:

```yaml
- name: End session and report P&L
  run: python end_session.py --broker etoro --auto
```

No new cron is needed: `end` already runs on the hourly monitor schedule, which
covers 09:05 and 10:05 as well as 08:05.

## 7. Evidence

`python test_market_calendar.py` — 11/11,
`python test_notify_session_state.py` — 23/23,
`python test_weekly_digest.py` — 13/13,
`python test_order_path.py` — 14/14. 61 checks, no network.

The order path is tested without a broker: `place_orders.load_data` and
`reclaim_monitor.load_data` are patched to serve synthetic frames, so
`session_levels` and `check_signal` can be exercised against a summer session, a
winter session, an idle market and a session with no closed reference —
including the winter case where 22:05 UTC is still the maintenance break and the
Asia session has not opened.

New coverage for the session window:

```
test_session_window_follows_the_globex_open_not_a_fixed_utc_hour
test_dst_transition_moves_the_session_open
test_the_2210_cron_lands_inside_the_session_in_summer_only
test_session_ids_match_the_summer_window_the_strategy_used
test_session_ids_use_the_winter_boundaries
test_winter_quiet_session_reports_the_globex_window
test_winter_session_uses_its_own_levels_and_trades
test_winter_break_sits_before_the_session_not_inside_it
test_sunday_session_arms_from_friday_exactly_as_the_strategy_does
test_a_session_with_its_own_window_never_reaches_past_it
test_a_window_that_had_not_closed_by_the_open_is_not_used
test_week_windows_follow_the_globex_open_into_winter
test_the_dst_week_is_one_rule_not_a_straddle
test_the_winter_flatten_is_0300_ct_not_a_fixed_utc_hour
test_the_summer_flatten_is_unchanged_at_0800_utc
```

`test_the_winter_flatten_is_0300_ct_not_a_fixed_utc_hour` holds a winter trade
open past 08:00 on purpose: with the session rule it exits on the 09:00 bar for
`+0.08R`, and pinned to the old fixed hour it exits at 08:00 for `+0.05R`. That
missing hour is the whole point of the change, and it is a strategy result, not a
display detail.

`test_dst_transition_moves_the_session_open` pins the awkward one: US DST ends
2026-11-01, so that Sunday's session opens at **23:00** UTC, not 22:00 — and the
week before it is still on 22:00.

Two real bugs were caught while writing the tests:

* `Timedelta` arithmetic is absolute, so adding 17h to a midnight that fell
  before the DST change gave 16:00 CT instead of 17:00 CT. Wall-clock times are
  now built from date components (`_ct`).
* `session_open_on()` excludes a Friday or Saturday open by asking `closure()`
  rather than by re-deriving the weekday rule — one source of truth, so a
  holiday that swallows a reopen (Good Friday takes out Thursday's) is handled
  for free.

`before_after.py` runs the original and fixed modules side by side:

```
2. SESSION WINDOW - a fixed 22:00 UTC start vs the Globex open (17:00 CT)
  probe                    OLD (fixed 22:00 UTC)              NEW (Globex open)
  Tue 9/8 22:10 CDT        09-08 22:00Z->09-09 10:00Z         09-08 22:00Z->09-09 10:00Z
  Wed 9/9 21:30 CDT        09-08 22:00Z->09-09 10:00Z         none
  Wed 12/9 22:10 CST       12-09 22:00Z->12-10 10:00Z         none
  Thu 12/10 00:30 CST      12-09 22:00Z->12-10 10:00Z         12-09 23:00Z->12-10 11:00Z
  Thu 12/10 10:30 CST      12-09 22:00Z->12-10 10:00Z         12-09 23:00Z->12-10 11:00Z
  Sun 11/1 22:30 DST ends  11-01 22:00Z->11-02 10:00Z         none

3. REFERENCE LEVEL - the alert now agrees with strategy.find_trades
  session  : 2026-09-06 22:00 -> 2026-09-07 10:00  (opens Sunday, exchange shut)
  NEW ref  : (95.0, 89.0)   <- Friday's, the last window that closed before the open
  strategy : 1 trade(s) in that session [('long', '2026-09-07 01:00')]

1. FEED FREEZES
Fri 9/4 23:00 | closed - Globex weekly close, reopens Sun 2026-09-06 22:00 UTC | NEW market-closed
Sat 9/5 00:58 | closed - Globex weekly close, reopens Sun 2026-09-06 22:00 UTC | NEW market-closed
Sun 9/6 00:49 | closed - Globex weekly close, reopens Sun 2026-09-06 22:00 UTC | NEW market-closed
Mon 9/7 00:47 | trading (Sun 19:47 CT)                                          | NEW stale
Tue 9/8 01:15 | trading (Mon 20:15 CT)                                          | NEW stale
        OLD, all five runs: armed ['98.35', '85.65']   <- Thursday's levels, frozen
```

```
4. ORDER PATH
                         before (constant)    after (calendar)
  reclaim tokyo trigger  (22, 10)             summer (22, 10) / winter (23, 11)
  reclaim london ref     (22, 10)             summer (22, 10) / winter (23, 11)

  session flatten
    10 Sep (CDT)   before 08:00 UTC   after 08:00 UTC (03:00 CT)
    10 Dec (CST)   before 08:00 UTC   after 09:00 UTC (03:00 CT)

  end_session profile, on the hourly cleanup cron
    2026-09-09 08:05   before H=08 -> tokyo    after -> tokyo
    2026-12-09 08:05   before H=08 -> tokyo    after -> london
    2026-12-09 09:05   before H=08 -> london   after -> tokyo
```

The old module emits an identical armed payload every run, weekend included, and
its `current_session_start` never returns `None` — it claims a session all
through the 10:00–22:00 UTC dead zone, which is what let the last bar decide what
"now" was. The new one names the closure, then switches to `stale` once the
market is genuinely back.

The healthy armed message is field-for-field what the live channel already posts,
with two deliberate additions that make the session visible:

```
"Session": "22:00–10:00 UTC · flat by 08:00 UTC · 2026-09-09 · Globex 17:00 CT"
footer:    "Waiting for liquidity sweep — one trade per session | last bar 22:00 UTC"
```

Even the triggers line up: replaying the 22:10 UTC run on 2026-09-09 gives
`NY-late high / low 4450.40 / 4438.90`, the same levels the channel received
(`SHORT trigger 4471.42 / LONG trigger 4417.88`; the channel showed
`4470.66 / 4418.64`, the same levels with that run's ATR).

## 7b. `scalp-agent` sweep-reclaim — the same window, from the CT side

The order scripts read `market_calendar`, which resolves a window for *today*.
A backtest cannot: it spans both seasons, so no single UTC hour pair is correct
across it. `scalp-agent` therefore gets the window a different way.

`scalp-agent/globex_session.py` (new) defines the exchange's own geometry in
Central Time - 17:00 CT open, 05:00 CT half-day close, 14:00-16:00 CT NY-late,
02:00-08:00 CT London - and derives everything from `index.tz_convert("America/Chicago")`.
Because the exchange publishes the schedule in CT and CT moves with the season,
**one definition is correct on both sides of a DST change**, which is exactly
what a fixed UTC tuple cannot be. `strategies/sweep_reclaim.py` now takes its
windows from there:

| profile | trigger | reference | flat |
|---|---|---|---|
| `gold` / `tokyo` | 17:00-05:00 CT | 14:00-16:00 CT | 03:00 CT |
| `london` | 02:00-08:00 CT | 17:00-05:00 CT | 12:00 CT |

resolving to 22:00-10:00 UTC on CDT and 23:00-11:00 UTC on CST. `SweepProfile`
now documents that its hours are CT, and signals carry `exit_ct_hour` so
`backtest.simulate` flattens on the clock the schedule is published in.

Two bugs came out of this that the fixed windows had hidden:

* **`day_ids` is UTC, whatever the index is labelled.** `strategy.day_ids`
  returns `index.asi8 // day`, and `asi8` is the UTC epoch regardless of tz - so
  localising an index does *not* give local day numbers, and grouping a CT
  session with UTC day numbers splits it in two: the 22:00 UTC half lands on day
  D, the 00:00-10:00 UTC half on D+1, and the `hour >= 17` rule pushes the
  morning bars to D+2. `globex_session.ct_day_ids` builds the id from
  `datetime.date.toordinal()` instead. (`tz_localize(None)` is not a fix either
  - for a tz-aware index it behaves like `tz_convert("UTC")` and silently shifts
  every id by a day. That one cost me a debugging round.)
* **`skip_sunday` reads the first *available* bar**, so a feed gap that eats the
  Sunday-evening prints makes a Sunday session look like a Monday one and it
  gets traded. `iter_signals` now tests the session's own opening CT weekday,
  derived from the session id.

`diag_profiles.py` and `strategies/round2.py` had their own copies of the fixed
windows and are on the same definition now. `run_scan.py` compared profile
windows against `now.hour` (UTC), which is wrong the moment the profiles are CT;
it converts to CT.

## 7c. What the Globex window does to the Asia Grab backtest

`backtest_globex.py` runs the repository's own `run_config` twice over the same
bars - the fixed-UTC config on the UTC index, and the CT config on a CT-localised
index - with the live tuned parameters from the README
(`--rr 0.75 --atr-mult 1.0 --atr-len 10 --entry-buffer 1.0 --cost 0.3`). GC=F
hourly, 730d, 13,759 bars:

| | trades | net R | win rate | PF |
|---|---|---|---|---|
| all, fixed-UTC | 516 | **+123.61R** | 72.5% | 1.88 |
| all, Globex-CT | 520 | **+109.59R** | 70.8% | 1.73 |
| **summer only**, fixed-UTC | 383 | **+85.64R** | - | - |
| **summer only**, Globex-CT | 383 | **+85.64R** | - | - |
| **winter only**, fixed-UTC | 133 | **+37.97R** | 75.2% | 2.18 |
| **winter only**, Globex-CT | 137 | **+23.96R** | 68.6% | 1.57 |

A note on `skip_sunday`: it must be **False** here. `find_trades` defaults to
False, the README's tuned command passes no `--skip-sunday`, and the Discord log
trades the Sunday 2026-08-30 session. The workflow's `SKIP_SUNDAY=1` governs the
FX monitor (and `place_orders.py --skip-sunday`), not the gold session.

**Summer is bit-identical: 383 trades, +85.64R both ways.** That is the strongest
thing this file says - in summer the CT window describes exactly the same hours
as the fixed one, so once the grouping is right the two runs are literally the
same run. Any summer difference would have been a bug in the port.

Winter is where the entire difference lives: **-14.01R across two winters**, and
it is concentrated:

| month | baseline | Globex | dR |
|---|---|---|---|
| 2024-11 | +6.66R (17) | +7.40R (18) | +0.74 |
| 2024-12 | +5.99R (11) | +6.70R (12) | +0.71 |
| 2025-01 | +2.65R (18) | +1.16R (17) | -1.49 |
| 2025-02 | +2.00R (11) | +1.65R (11) | -0.35 |
| 2025-03 | +3.56R (5) | +1.82R (5) | -1.74 |
| 2025-11 | +11.71R (16) | +3.21R (16) | **-8.50** |
| 2025-12 | +1.80R (15) | +0.22R (18) | -1.58 |
| 2026-01 | -1.48R (17) | -3.00R (16) | -1.52 |
| 2026-02 | +3.11R (18) | +2.86R (19) | -0.25 |
| 2026-03 | +1.96R (5) | +1.96R (5) | 0.00 |

At the session level: 4 winter sessions dropped, 8 added, and **48 of the 133
shared sessions change result** - the entry, stop and target are all built from
the 20:00-22:00 UTC reference instead of 19:00-21:00, so they are simply
different trades about a third of the time.

### Which of the two changes is it?

The port changes two things at once - the session window and the reference
window - so `backtest_globex.py` now runs the live stack's actual winter
behaviour as a third config: the Globex session from the calendar, but the
reference still read off the fixed UTC hours, which on CST is 13:00-15:00 CT.
That isolates the two:

| winter (CST) | trades | net R |
|---|---|---|
| UTC session + UTC reference (baseline) | 133 | +37.97R |
| **Globex session + UTC reference (what the live stack does)** | **134** | **+36.05R** |
| Globex session + CT reference (full port) | 137 | +23.96R |

**The session window costs -1.92R; the reference window costs -12.09R.** Almost
the whole winter difference is the reference clock, not the session clock.

That matters because the two are not equally well founded. The session window is
a fact - the exchange opens at 17:00 CT, full stop - and in winter the old fixed
22:00 UTC start armed an hour before the market was open. The reference window is
a *choice*: 19:00-21:00 UTC was the window the strategy was tuned on, and
14:00-16:00 CT is the same *local* time of day, not the same UTC hours. Moving it
costs 12R over two winters, so it should be decided deliberately rather than
inherited from the session change - and today **the live path has not moved it**:
`notify.NY_LATE` and `place_orders.NY_LATE` are both `(19, 21)` UTC, so live
alerts and orders are on the middle row whichever way you settle it. The Discord
log cannot help decide, because every session it covers is CDT, where the two
windows are the same window (see 7f).

So the honest statement of the winter numbers is: the *session* fix is roughly
neutral on this tape (+37.97R -> +36.05R), and the *reference* move is the whole
-14.01R. `python backtest_globex.py --detail` prints the per-session diff. The
frame is cached under `.cache/`, so re-runs are offline.

## 7e. Reconciling the Discord log against the same period

The live log is ground truth: 12 realised gold trades, 2026-08-26 to 2026-09-11,
with entry, stop, target, exit reason and R. `reconcile_log.py` re-derives that
exact window with the Globex window and the 03:00 CT flatten, and matches the two
session by session. The window matters - it spans Labor Day, which closes metals
for the whole NY-late reference window of 2026-09-07, so that session's levels
come from Friday.

```
logged trades:    12  net +4.73R
backtest trades:  11  net +5.77R
10 match | 1 differ | 1 logged but not produced | 0 produced but not logged
```

**10 of the 12 reproduce to the cent** - entry, stop, target, exit reason and R.
That includes the Labor-Day session, whose +0.73R entry of 4451.05 comes off
Friday's levels. The reference levels agree on **11 of 11 sessions**.

Both remaining differences are ATR10, not levels. An entry is
`reference_level +/- 1.0xATR10` and the stop is a further `1.0xATR10` away, so the
implied pair separates the two causes exactly:

**1. A second entry in the same session (2026-08-27).** The log has two LONG
entries three hours apart, and their implied pairs are
`(4653.80, 15.54)` and `(4653.80, 13.93)` - the **same reference low**, two
different ATRs, which is why the two logs' entries and stops look unrelated.
`find_trades` emits at most one trade per session *per run*, so two entries means
**two runs**, and the trigger `rl - 1.0xATR10` moved from 4638.26 to 4639.87
because ATR10 fell from 15.54 to 13.99 between them. The strategy's one-trade-
per-session invariant is per-run, not per-session: re-running the same session
with a changed ATR places a *second* order. That is the mechanism behind the
"two LONG trades in the 8/28 session" the paste flagged at the start, and it is
not a duplicate of the log - the levels genuinely differ.

**2. One Sunday session, levels identical, ATR not (2026-08-30).** Implied pairs
are `(4502.30, 29.90)` logged and `(4502.30, 31.30)` here: reference low exact,
ATR10 out by 1.40 (4.5%). ATR10 is the only input in the chain that is not
pinned to a bar - it depends on the data provider and the exact pull instant.
Pull length is not the explanation (12d/14d/16d all give 31.30), and neither is
the entry hour on the current bar set (02:00 gives 31.30, 03:00 gives 26.15,
04:00 gives 24.73 - none is 29.90). Worth checking whether the live path and the
backtest read ATR from the same provider and the same bar set. Note also that
this is the **Sunday** session, which `place_orders.py --skip-sunday` is
configured to decline in CI - so the order that filled did not come from the job
that is supposed to be the only placer.

Both differences are the same size class as a single average win, so they do not
change the picture: the port reproduces what ran.

## 7f. The whole Discord log, session by session

`reconcile_log.py` checks the twelve trades. `reconcile_timeline.py` checks the
whole log over the whole time it covers - thirty-one postings, of which nineteen
are "armed" messages. An armed posting is a *second* readout of the same thing:
it quotes the reference the live system had loaded and both triggers built from
it, at a known moment. Twelve trades are twelve samples; the log has thirty-one.

### The printed clock is local, and the buffer proves which offset

Discord renders message times in the reader's zone, so the paste's timestamps are
not UTC. They can be recovered without guessing: the triggers sit `1.0xATR10`
from the quoted levels, so each posting's buffer dates the run that built it, and
a run can only have read a bar that had already closed. At UTC+2 the implied
buffer lands within three-tenths of a tick of that bar on five postings
(7, 8, 17, 18, 19); at UTC+1 it is off by up to 4.09, and at UTC+3 by up to 4.68.

The verdicts do **not** depend on it: they are identical at UTC+1 and UTC+2, and
at UTC+3 only two postings (9, 14) fall out of their session. That is asserted in
the tests.

### Every posting, classified

```
  #        printed        UTC (-2h)          hi / lo      buf   levels from   market             verdict
  1  2026-08-29 04:41  08-29 02:41Z  4672.40/4653.80  37.35  08-28 session  weekly close       outside-session
  5  2026-08-31 01:28  08-30 23:28Z  4529.90/4502.30  38.77  own            trading            on time
  8  2026-09-03 01:06  09-02 23:06Z  4438.20/4418.30  17.72  own            trading            on time
 14  2026-09-07 00:47  09-06 22:47Z  4485.60/4470.00  30.78  own            trading            on time
 16  2026-09-08 01:15  09-07 23:15Z  4528.70/4517.70  31.62  09-04 (2 old)  trading            stale
 19  2026-09-11 01:03  09-10 23:03Z  4374.00/4354.70  18.97  own            trading            on time
```

`on time: 9 · outside-session: 9 · stale: 1`.

The nine **outside-session** postings are the interesting class, because they are
the ones the market calendar now suppresses rather than posts: four land in the
Friday-evening weekly close (08-29 x2, 08-30 x2 with the *previous* week's
levels), four more across the long weekend (09-05 x2, 09-06 x2), and one between
sessions on the Monday. Two of them are the runs that re-armed the frozen
`4528.70 / 4517.70` levels for three days after the market had shut.

The one **stale** posting is the 09-08 one already known from 7e: it quotes the
session two back (`09-04` window) while the session it sits in needed the
`09-04` session's own Friday window - the same stale display that sat next to a
correctly armed order. Note the pattern either side of it: 09-07 00:47 has the
*right* Friday levels, then 09-07 15:13 and 09-08 01:15 revert to the older ones
and the session trades off the right levels anyway.

### Every session, both clocks

```
trade date     session (UTC)          ref window       levels                 channel              backtest
Thu 08-27 08-26 22:00->08-27 10:00  08-26 19-21Z  4656.30 / 4638.60  SHORT 23:00 +0.73(tp)  SHORT 23:00 +0.73(tp)
Fri 08-28 08-27 22:00->08-28 10:00  08-27 19-21Z  4672.40 / 4653.80  LONG 01:00 +0.73; 04:00 -1.02  LONG 01:00 +0.73
Mon 08-31 08-30 22:00->08-31 10:00  08-28 19-21Z  4529.90 / 4502.30  LONG 02:00 +0.74(tp)  LONG 02:00 +0.74(tp)
Tue 09-08 09-07 22:00->09-08 10:00  09-04 19-21Z  4485.60 / 4470.00  LONG 06:00 -1.02(sl)  LONG 06:00 -1.02(sl)
Fri 09-11 09-10 22:00->09-11 10:00  09-10 19-21Z  4374.00 / 4354.70  SHORT 06:00 +0.20(time) SHORT 06:00 +0.20(time)
```

Twelve sessions, eleven of them traded, twelve logged trades (the 08-28 session
was entered twice). Two things come out of it:

* **The tape confirms every level the channel quoted.** Each armed posting's
  high/low is some session's own reference window, and each of the twelve trades'
  implied level is a level the channel had shown - eleven of them from an armed
  posting, the twelfth (2026-08-26) from a session whose postings are simply not
  in the paste, which starts on 08-29.
* **Channel and backtest agree on 10 of 12** sessions for the same reason as 7e:
  the 08-28 double entry is a second run, and the 08-30 ATR10 differs by 4.5%.

### What this window can and cannot test

This is the useful negative result. **Every session in the log is on CDT, where
14:00-16:00 CT is exactly 19:00-21:00 UTC**, so the two reference clocks pick
identical windows on **12 of 12** sessions. The log therefore validates the
session window, the flatten and the levels - and is *silent* on which clock the
reference should be read on, which is the question 7c says costs 12R.

Re-derived over the whole 625-session frame the clocks disagree on **163
sessions**, all of them winter, starting 2024-11-04. The first one after the log
ends opens **2026-11-01 23:00 UTC** - the DST change, when the session start
moves to 23:00 and the fixed 19:00-21:00 UTC reference stops being four hours
before the open and becomes five.

### Also visible in the log, not yet acted on

* **The duplicates date the second webhook app, not a second repository.** The
  log's 19 armed postings are single up to and including 2026-09-02 and paired
  from `2026-09-03 01:06` on - 7 single, then 12 paired (`discord_log.ARMED`
  carries the count, `FIRST_DUPLICATE` pins the boundary). Immediately before it
  sits the `2026-09-02 22:22` post - *"Asia Grab online / Webhook test - daily
  signals will land here"* - from a second app. The monitor's own `Sweep Reclaim`
  postings straddle the same boundary without pairing (1 before, 4 after), so
  what is doubled is the daily-signal delivery, not the strategy. It is not two
  repositories either: both local checkouts push to `eddoonn/Asia-NY`, and the
  repository named `asia-gold-reversal` is a rename to `asia-gold-reversal-OLD`
  (section 9a). Two dispatchers, one signal stream, which is the same class of
  problem as the double-entry in 7e and the reason the paste looked like it
  contained duplicates.
* **Five FX postings ride along in the same channel** (`Sweep Reclaim - n trades
  opened`): 2026-09-02 AUDJPY, 09-03 EURUSD + USDJPY, 09-04 GBPJPY, 09-07
  EURUSD, 09-09 USDJPY + EURJPY + GBPJPY. Eight legs on the `scalp-agent`
  profiles that 7b put on the Globex window. They are transcribed in
  `discord_log.SWEEPS` but not reconciled here - the gold and FX sessions are
  different windows with different risk, and the FX legs' fills are not in the
  log, so there is nothing to close them out against.

`python reconcile_timeline.py` prints the classification; `--detail` adds the
buffer forensics.

## 7d. `smoke_workflows.py` — scheduled commands may not touch a shut book

The workflows fire every hour, all week. `asiagrab.yml`'s monitor cron is
`5 22,23,0-10,7-13 * * *`, so it lands on Saturday and Sunday mornings, on the
Friday-evening close, and inside every holiday - and `reclaim_monitor.py` is
invoked with `--force`, which bypasses the window check entirely.

The harness parses every workflow file, expands each `cron:` into real UTC fire
times over three windows chosen to include a weekend, Labor Day 2026-09-07 and
the 2026-11-01 DST transition, then runs each (command, fire time) pair
**in-process** with `--dry-run` and the clock pinned via the new `--as-of`. Every
script announces what it would do:

```
DRYRUN ORDER place GC=F ...        <- would act
DRYRUN BLOCKED market closed ...   <- declined
```

and the run fails if any command emits an order action while
`market_calendar.is_open()` says the exchange is shut. No broker, no network -
`load_data` is swapped for a synthetic frame and the run happens in a throwaway
working directory, so nothing the scripts write can escape.

`as_of` is new on `place_orders.py`, `reclaim_monitor.py` and `end_session.py`
(`notify.py` already had it), and each now gates on `closure(now)`:

* `place_orders.py` declines before it fetches anything.
* `reclaim_monitor.py` declines, and **`--force` does not override it** - the
  flag exists to bypass the session-window check in testing, not to trade a
  closed book. That was reachable from CI before this change.
* `end_session.py` declines. The hourly cron was cancelling and closing on
  Saturday mornings and through the Friday-evening close; the broker rejects
  those, so the job only ever produced noise and a false "SESSION CLEANUP"
  report.

The current suite: **1,512 replayed runs, 1,074 while open, 412 correctly
declined while closed, 0 violations** (18 seconds). The check is not vacuous -
neutering the three gates makes it fail and prints exactly the offending
schedules, e.g. `end_session.py` at Sun 05:05 CT under the Globex weekly close.
`test_smoke_workflows.py` covers the cron expansion, the workflow parser (an
`if/then` step must not leak into the next YAML line), the marker regexes, and
the three gates at a pinned Saturday, with a fixture workflow whose only cron is
`5 8 * * 6` so a pass cannot come from never declining.

## 8. One open question

The evening session on the **Sunday before a Monday holiday** (MLK, Presidents,
Memorial, Labor Day) is the one point where the published sources disagree: some
say Sunday opens as normal and runs into the holiday, others list Sunday as
closed. `METALS_CLOSURES` follows the first reading, so a quiet feed that Sunday
reads as `stale` rather than as a closure.

The 2026 data argues the other way. The session opening Sunday 2026-08-30
(trade date Mon 08-31) traded normally, so Yahoo does carry Sunday-evening bars
for `GC=F`. The session opening Sunday 2026-09-06 (trade date Mon 09-07) has
**no bars at all** — not during the session, not in the twelve hours either side.
That is what a closed market looks like, not a feed gap.

I left the table alone because a wrong closure can only ever remove a signal and
I would rather you make that call on the exchange calendar. If you want it shut:

```python
("2026-09-06 17:00", "2026-09-07 17:00", "Labor Day (no Sunday session)"),
```

Nothing else changes; the digest would report that session as `closed` instead of
`no-data`.

## 9. Apply

Copy `notify.py`, `market_calendar.py`, `weekly_digest.py`, `place_orders.py`,
`reclaim_monitor.py`, `end_session.py` and the six test files into the checkout
(the audit scripts - `backtest_globex.py`, `discord_log.py`, `reconcile_log.py`,
`reconcile_timeline.py`, `smoke_workflows.py` - are diagnostics and can live
outside it):

```bash
python test_market_calendar.py
python test_notify_session_state.py
python test_weekly_digest.py
python test_order_path.py
python market_calendar.py --year 2026            # audit the closure table
python market_calendar.py --week "2026-12-12 09:00"   # inspect a winter week
python market_calendar.py --check "2026-12-09 22:30"  # one moment, both zones
python market_calendar.py --flat "2026-12-10 12:00"   # the flatten hour
python notify.py --test                          # webhook still good
python notify.py --dry-run                       # see the session message
python notify.py --trigger --dry-run             # see the trade-triggered message
python notify.py --digest --dry-run              # see the weekly digest
python end_session.py --which-profile            # tokyo or london, right now
python place_orders.py --broker etoro --dry-run   # dry, no state written
python reclaim_monitor.py --dry-run --force       # dry, ignores the window
```

`place_orders.py` and `reclaim_monitor.py` place real orders. Run both with
`--dry-run` first, and check `--force` isn't passed to the monitor by accident:
it bypasses the window check.

Then, in `.github/workflows`:

* add `- cron: '10 23 * * *'` to `discord-notify.yml` so the winter 17:10 CT run
  exists (section 3);
* add `python notify.py --trigger` to the hourly monitor step in `asiagrab.yml`,
  so a fill is announced while it is still live (section 5b);
* replace the `H=$(date -u +%H)` block in `asiagrab.yml` with
  `python end_session.py --broker etoro --auto` (section 6);
* schedule the digest after the week closes — Saturday 09:00 UTC, say —
  and have the job run `python notify.py --digest` for that cron:

```yaml
- cron: '0 9 * * 6'    # Saturday 09:00 UTC: weekly digest
```

Only one copy of `notify.py` should be live, and there is only one: both local
checkouts point at `https://github.com/eddoonn/Asia-NY` (the folder named
`asia-gold-reversal` pushes there too), so that is one repository with one
`.github/workflows/discord-notify.yml` (22:10 and 08:10 UTC).
`eddoonn/asia-gold-reversal` is a *rename*, not a second live repo - it redirects
to `eddoonn/asia-gold-reversal-OLD`.

**The doubled alerts come from a second webhook app, not a second repository.**
That renamed repo's workflow was committed `2026-08-27 00:29 +0100`, so if its
schedule were the second dispatcher the pairing would have started on 08-27; the
log shows it starting six days later, on the first posting after the
`2026-09-02 22:22` *"Asia Grab online / Webhook test"* registration (7c). Nothing
in git is duplicated, so the fix belongs at the webhook end - retiring the second
integration - and not in the repository.

## 9b. Files in this drop-in

Asia Grab (staged here; your checkouts are untouched):

| file | change |
|---|---|
| `market_calendar.py` | + `LONDON_TRIGGER_CT`, `ct_hour_window`, `london_hour_window` |
| `place_orders.py` | `--as-of`, market-closed gate, `DRYRUN` markers |
| `reclaim_monitor.py` | `--as-of`, London CT window, gate that `--force` cannot bypass |
| `end_session.py` | `--as-of`, market-closed gate, `DRYRUN` markers |
| `backtest_globex.py` | **new** — fixed-UTC vs Globex-CT comparison, winter decomposition |
| `discord_log.py` | **new** — the Discord log as data (postings, trades, FX legs) |
| `reconcile_log.py` | **new** — the Discord log vs the same period, session by session |
| `reconcile_timeline.py` | **new** — the whole log audited: postings, sessions, both clocks |
| `notify.py --trigger` | **new mode** — the trade-triggered alert (section 5b) |
| `test_reconcile_timeline.py` | **new** — 8 checks, transcript arithmetic + verdicts |
| `smoke_workflows.py` | **new** — scheduled-command replay |
| `test_smoke_workflows.py` | **new** — 14 tests for the harness and the gates |
| `workflows-fixture/closed-only.yml` | **new** — closed-only fixture workflow |
| `test_order_path.py` | London expectation updated to `(8, 14)` in winter |

`scalp-agent/` (a runnable mirror of the repo, so the tests can import it):

| file | change |
|---|---|
| `globex_session.py` | **new** — the CT session geometry |
| `strategies/sweep_reclaim.py` | CT windows, `exit_ct_hour`, session-day `skip_sunday` |
| `backtest.py` | flatten on `exit_ct_hour` when a signal carries one |
| `diag_profiles.py`, `strategies/round2.py` | CT windows, local day ids |
| `run_scan.py` | window checks in CT |
| `tests/test_globex_session.py` | **new** — 13 tests, DST + Sunday + profiles |
| `tests/test_sweep_reclaim.py` | unchanged (June fixtures are CDT, so identical) |

Run everything:

```bash
cd asia-grab-notify-fix
export PYTHONPATH="$HOME/Desktop/MatchForecast codes/asia-gold-reversal"
for f in test_market_calendar test_notify_session_state test_weekly_digest test_order_path; do python $f.py; done
python -m unittest test_smoke_workflows
python smoke_workflows.py              # 1,512 scheduled runs, offline, ~18s
python backtest_globex.py --detail     # cached frame; delete .cache/ to refetch
python reconcile_log.py                # the 12 logged trades, session by session
python reconcile_timeline.py --detail  # the whole log, and the offset forensics
(cd scalp-agent && PYTHONPATH=. python -m unittest discover -s tests)
```

## 10. Still open

* **The reference clock is a live decision, not a settled one.** The session
  window is a fact about the exchange; the NY-late reference window is a tuning
  choice, and putting it on the CT clock costs 12R over two winters (7c).
  `notify.NY_LATE` and `place_orders.NY_LATE` still read `(19, 21)` UTC, so the
  live stack is on the middle row - Globex session, unchanged reference - and its
  winter numbers are the ones already in the log (~+36R rather than +24R). Left
  as-is deliberately: it wants a decision, and a backtest that tunes the
  reference window directly, not a quiet side-effect of this change.
* **The Discord log's FX postings are unreconciled.** Eight legs across five
  `Sweep Reclaim` postings sit in the same channel (`discord_log.SWEEPS`). The
  log has no fills or exits for them, so there is nothing to close them against;
  reproducing them would need the FX bars and the `scalp-agent` run that posted
  them, which is the next thing after a reference-window decision.
* **A third copy of these windows lives in `scalp-agent`.**
  `strategies/sweep_reclaim.py` has `SweepProfile("gold", (22, 10), (19, 21), 8)`,
  `SweepProfile("tokyo", (22, 10), ...)` and `SweepProfile("london", (7, 13),
  (22, 10), 17)`, and `strategies/round2.py` / `diag_profiles.py` repeat them. If
  that is the live path now, it has the same winter problem and wants the same
  treatment - it was left alone because it is a different project with its own
  test suite.
* The research scripts (`deep_verify.py`, `optimize_wick.py`, `analyze_*.py`,
  `test_london.py`) pin `(22, 10)` and `exit=8` as backtest configs. Those are
  the *inputs* to a re-run, not the live path, so they should be left as the
  historical baseline - but a winter-window run is worth adding so the
  strategy's own numbers move to the new definition rather than lagging it.
* London's reference lookup still uses the day-id walk rather than
  `find_trades`' dynamic-reference rule. That is semantics, not a window, and it
  needs a backtest to settle.
* The holiday table stops at 2027. The daily and weekly rules never need
  updating; the holiday windows do.
* Early-close times are modelled only where they define a closure window.
* The digest reads the same 12-day hourly window as the notifier; a week whose
  data has aged out of that window would need a longer `period`.
* London's flatten was the last fixed-UTC hour in the order path
  (`LONDON_FLAT_HOUR = 17` in `end_session.py`). It is now 12:00 CT via
  `market_calendar.london_exit_hour`, so it is 17:00 UTC on CDT and 18:00 on CST,
  matching the London trigger window. `which_profile` still falls back to
  "london" for every hour outside the Asia cleanup window, which is the
  pre-existing design - but it now cleans the right session in winter.
* The smoke test runs `place_orders.py` at every cron, but `place` is
  `workflow_dispatch`-only in `asiagrab.yml`. Over-testing is deliberate; if you
  want the pairing to be exact, the harness would need to read the `if:` guards.
* `patterns_2026.py`, `round3.py` and `micro_sweep.py` still use fixed UTC
  London windows (`7-17`). Those are separate intraday research strategies, not
  the live sweep-reclaim path, so they were left alone - but they carry the same
  winter drift and are worth a pass before any of them goes live.
* The backtest frame is GC=F only. The JPY crosses have their own session
  handling in `strategy.py` and deserve the same comparison.
