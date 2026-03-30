"""Unit tests for core.time_utils – timezone utilities."""

import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.time_utils import utc_now, ensure_utc, guard_alive


class TestUtcNow(unittest.TestCase):
    """Tests for UTC timestamp generation."""

    def test_returns_datetime(self):
        result = utc_now()
        self.assertIsInstance(result, datetime)

    def test_has_utc_timezone(self):
        result = utc_now()
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_is_recent(self):
        before = datetime.now(timezone.utc)
        result = utc_now()
        after = datetime.now(timezone.utc)
        self.assertGreaterEqual(result, before)
        self.assertLessEqual(result, after)


class TestEnsureUtc(unittest.TestCase):
    """Tests for UTC timezone coercion."""

    def test_aware_datetime_converted(self):
        offset = timezone(timedelta(hours=2))
        dt = datetime(2025, 6, 15, 14, 0, 0, tzinfo=offset)

        result = ensure_utc(dt)

        self.assertEqual(result.tzinfo, timezone.utc)
        # 14:00 CEST (UTC+2) -> 12:00 UTC
        self.assertEqual(result.hour, 12)

    def test_naive_datetime_assumed_utc(self):
        dt = datetime(2025, 6, 15, 12, 0, 0)

        result = ensure_utc(dt)

        self.assertEqual(result.tzinfo, timezone.utc)
        self.assertEqual(result.hour, 12)

    def test_utc_datetime_unchanged(self):
        dt = datetime(2025, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

        result = ensure_utc(dt)

        self.assertEqual(result, dt)
        self.assertEqual(result.hour, 12)


class TestGuardAlive(unittest.TestCase):
    """Tests for the guard_alive method decorator."""

    def test_executes_when_alive(self):
        call_count = [0]

        class Widget:
            alive = True

            @guard_alive
            def do_work(self):
                call_count[0] += 1
                return "done"

        result = Widget().do_work()

        self.assertEqual(result, "done")
        self.assertEqual(call_count[0], 1)

    def test_skips_when_not_alive(self):
        call_count = [0]

        class Widget:
            alive = False

            @guard_alive
            def do_work(self):
                call_count[0] += 1
                return "done"

        result = Widget().do_work()

        self.assertIsNone(result)
        self.assertEqual(call_count[0], 0)

    def test_preserves_arguments(self):
        class Calculator:
            alive = True

            @guard_alive
            def add(self, a, b, c=0):
                return a + b + c

        result = Calculator().add(1, 2, c=3)

        self.assertEqual(result, 6)


if __name__ == "__main__":
    unittest.main()
