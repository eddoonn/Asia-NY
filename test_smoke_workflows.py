"""Tests for the workflow smoke harness, and for the gates it depends on.

The harness is only useful if it can (a) find every scheduled command and
(b) tell an order action from a refusal. Both are asserted here, together with
the end-to-end case: an order script pinned to a shut market must decline.
"""
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone

from market_calendar import is_open
from smoke_workflows import (ACTION_RE, BLOCKED_RE, fire_times, parse_workflow,
                            run_command)
from smoke_workflows import main as smoke_main

HERE = os.path.dirname(os.path.abspath(__file__))

ASIA_WORKFLOW = """\
name: asia-grab
on:
  schedule:
    - cron: '5 8 * * *'
    - cron: '5 22,23,0-10,7-13 * * *'
jobs:
  monitor:
    steps:
      - name: Sweep-reclaim hourly monitor
        run: python reclaim_monitor.py --force
      - name: End session
        run: |
          H=$(date -u +%H)
          if [ "$H" = "08" ]; then python end_session.py --broker etoro --profile tokyo; else python end_session.py --broker etoro --profile london; fi
"""

NOTIFY_WORKFLOW = """\
name: discord-daily-signal
on:
  schedule:
    - cron: '10 22 * * *'
jobs:
  notify:
    steps:
      - run: |
          if [ "${{ inputs.test }}" = "true" ]; then
            python notify.py --test
          else
            python notify.py
          fi
"""


class CronTests(unittest.TestCase):
    def test_list_and_range_fields(self):
        start = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)
        fires = fire_times("5 22,23,0-10,7-13 * * *", start, start + timedelta(days=1))
        # 0-10 and 7-13 overlap, so the hour set is 0-13 plus 22 and 23.
        self.assertEqual([f.hour for f in fires],
                         list(range(0, 14)) + [22, 23])
        self.assertEqual({f.minute for f in fires}, {5})

    def test_wildcard_day_fields_cover_every_day(self):
        start = datetime(2026, 9, 7, 0, 0, tzinfo=timezone.utc)
        fires = fire_times("10 22 * * *", start, start + timedelta(days=7))
        self.assertEqual(len(fires), 7)
        self.assertEqual({f.hour for f in fires}, {22})

    def test_month_restriction(self):
        jan = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
        june = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        self.assertEqual(len(fire_times("0 12 * 12 *", jan, jan + timedelta(days=365))), 31)
        self.assertEqual(len(fire_times("0 12 * 12 *", june, june + timedelta(days=31))), 0)


class ParseTests(unittest.TestCase):
    def test_finds_crons_and_commands(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write(ASIA_WORKFLOW)
            path = f.name
        try:
            crons, cmds = parse_workflow(path)
        finally:
            os.unlink(path)
        self.assertEqual(crons, ["5 8 * * *", "5 22,23,0-10,7-13 * * *"])
        self.assertEqual(cmds[0], ("reclaim_monitor.py", ["--force"]))
        self.assertIn(("end_session.py", ["--broker", "etoro", "--profile", "tokyo"]), cmds)
        self.assertIn(("end_session.py", ["--broker", "etoro", "--profile", "london"]), cmds)

    def test_command_does_not_leak_into_the_next_yaml_line(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write(ASIA_WORKFLOW)
            path = f.name
        try:
            _crons, cmds = parse_workflow(path)
        finally:
            os.unlink(path)
        for script, args in cmds:
            self.assertTrue(script.endswith(".py"), script)
            self.assertFalse([a for a in args if a.endswith(":")], f"{script} leaked: {args}")

    def test_inline_if_branches(self):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False) as f:
            f.write(NOTIFY_WORKFLOW)
            path = f.name
        try:
            crons, cmds = parse_workflow(path)
        finally:
            os.unlink(path)
        self.assertEqual(crons, ["10 22 * * *"])
        self.assertEqual(cmds, [("notify.py", ["--test"]), ("notify.py", [])])


class MarkerTests(unittest.TestCase):
    def test_order_lines_are_detected(self):
        self.assertTrue(ACTION_RE.search("DRYRUN ORDER place GC=F SHORT 4401\n"))
        self.assertTrue(ACTION_RE.search("DRYRUN ORDER cancel/close GC=F sell 12\n"))
        self.assertTrue(ACTION_RE.search("[placed] XAUUSD buy 1.2\n"))
        self.assertTrue(ACTION_RE.search("Cancelled working order abc\n"))

    def test_refusals_are_not_order_lines(self):
        quiet = ("No orders tonight - market closed\n"
                 "DRYRUN BLOCKED market closed - Globex weekly close\n"
                 "DRY RUN - no orders placed\n")
        self.assertFalse(ACTION_RE.search(quiet))
        self.assertTrue(BLOCKED_RE.search(quiet))

    def test_closed_clock_is_closed(self):
        saturday = datetime(2026, 9, 5, 8, 5, tzinfo=timezone.utc)
        self.assertFalse(is_open(saturday))


class GateTests(unittest.TestCase):
    """The order scripts must decline while the market is shut."""

    CLOSED = datetime(2026, 9, 5, 8, 5, tzinfo=timezone.utc)   # Saturday
    OPEN = datetime(2026, 9, 3, 8, 5, tzinfo=timezone.utc)     # Thursday

    def _run(self, script, args, when):
        with tempfile.TemporaryDirectory() as td:
            return run_command(script, args, when, td)

    def test_end_session_declines_on_a_closed_market(self):
        t0 = time.time()
        out = self._run("end_session.py", ["--broker", "etoro", "--profile", "tokyo"],
                        self.CLOSED)
        self.assertLess(time.time() - t0, 5.0, "the gate must fire before any fetch")
        self.assertTrue(BLOCKED_RE.search(out))
        self.assertFalse(ACTION_RE.search(out))

    def test_place_orders_declines_on_a_closed_market(self):
        out = self._run("place_orders.py", ["--broker", "etoro"], self.CLOSED)
        self.assertTrue(BLOCKED_RE.search(out))
        self.assertFalse(ACTION_RE.search(out))

    def test_reclaim_monitor_force_does_not_bypass_a_closed_market(self):
        out = self._run("reclaim_monitor.py", ["--force"], self.CLOSED)
        self.assertTrue(BLOCKED_RE.search(out))
        self.assertFalse(ACTION_RE.search(out))


class HarnessTests(unittest.TestCase):
    def test_full_replay_over_the_labor_day_week_passes(self):
        rc = smoke_main_quiet(["--workflows", os.path.join(HERE, "workflows-fixture"),
                               "--window", "2026-09-07 00:00",
                               "--max-runs", "8"])
        self.assertEqual(rc, 0)

    def test_missing_workflows_is_an_error(self):
        self.assertEqual(smoke_main_quiet(["--workflows", "/nonexistent/workflows"]), 2)


def smoke_main_quiet(argv):
    import contextlib
    import io
    import sys
    buf = io.StringIO()
    argv = ["smoke"] + list(argv)
    with contextlib.redirect_stdout(buf):
        saved = sys.argv
        sys.argv = argv
        try:
            rc = smoke_main()
        finally:
            sys.argv = saved
    return rc


if __name__ == "__main__":
    unittest.main()
