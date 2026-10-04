"""Unit tests for core.time_utils – timezone utilities."""

import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.time_utils import utc_now, ensure_utc, guard_alive, parse_db_ts, to_db_ts, db_ts_to_local, db_cutoff


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



class TestDbTimestamps(unittest.TestCase):
    """DB-Konvention: UTC im Format YYYY-MM-DD HH:MM:SS."""

    def test_aware_offset_converted_to_utc(self):
        self.assertEqual(to_db_ts("2026-09-13T14:00:08.011306+02:00"), "2026-09-13 12:00:08")

    def test_naive_is_utc_by_default(self):
        self.assertEqual(to_db_ts("2026-09-13 12:00:04"), "2026-09-13 12:00:04")

    def test_z_suffix(self):
        self.assertEqual(to_db_ts("2026-01-01T00:00:00Z"), "2026-01-01 00:00:00")

    def test_naive_local_option(self):
        local = datetime(2026, 7, 1, 14, 0, 0)
        expected = local.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.assertEqual(to_db_ts(local, naive_is_local=True), expected)

    def test_invalid_returns_none(self):
        self.assertIsNone(to_db_ts("kein datum"))
        self.assertIsNone(parse_db_ts(None))
        self.assertIsNone(db_ts_to_local(""))

    def test_db_ts_to_local_is_naive_local(self):
        local = db_ts_to_local("2026-07-01 12:00:00")
        self.assertIsNone(local.tzinfo)
        expected = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
        self.assertEqual(local, expected)

    def test_cutoff_comparable_with_db_strings(self):
        cutoff = db_cutoff(hours=1)
        self.assertEqual(len(cutoff), 19)
        now_db = to_db_ts(datetime.now(timezone.utc))
        self.assertGreater(now_db, cutoff)

if __name__ == "__main__":
    unittest.main()
