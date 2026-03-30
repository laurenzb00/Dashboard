"""Unit tests for ui.app_helpers – UI helper functions."""

import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ui.app_helpers import (
    parse_iso_datetime,
    format_age_short,
    format_age_compact,
    age_seconds,
    format_bmk_mode,
    compose_status_text,
)


class TestParseIsoDatetime(unittest.TestCase):
    """Tests for ISO datetime parsing."""

    def test_basic_datetime(self):
        result = parse_iso_datetime("2024-01-15 10:30:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.hour, 10)
        self.assertEqual(result.minute, 30)

    def test_iso_format_with_t(self):
        result = parse_iso_datetime("2024-01-15T10:30:00")
        self.assertIsNotNone(result)
        self.assertEqual(result.hour, 10)

    def test_utc_z_suffix(self):
        result = parse_iso_datetime("2024-01-15T10:30:00Z")
        self.assertIsNotNone(result)
        self.assertEqual(result.tzinfo, timezone.utc)

    def test_none_input(self):
        self.assertIsNone(parse_iso_datetime(None))
        self.assertIsNone(parse_iso_datetime(""))

    def test_invalid_format(self):
        self.assertIsNone(parse_iso_datetime("not-a-date"))
        self.assertIsNone(parse_iso_datetime("abc123"))


class TestFormatAgeShort(unittest.TestCase):
    """Tests for short age formatting."""

    def test_seconds(self):
        self.assertEqual(format_age_short(30), "30s")
        self.assertEqual(format_age_short(59), "59s")

    def test_minutes(self):
        self.assertEqual(format_age_short(60), "1m")
        self.assertEqual(format_age_short(125), "2m")
        self.assertEqual(format_age_short(3540), "59m")

    def test_hours(self):
        self.assertEqual(format_age_short(3600), "1h 00m")
        self.assertEqual(format_age_short(3700), "1h 01m")
        self.assertEqual(format_age_short(7380), "2h 03m")

    def test_days(self):
        self.assertEqual(format_age_short(86400), "1d 00h")
        self.assertEqual(format_age_short(90000), "1d 01h")

    def test_none_returns_dashes(self):
        self.assertEqual(format_age_short(None), "--")

    def test_negative_becomes_zero(self):
        self.assertEqual(format_age_short(-10), "0s")


class TestFormatAgeCompact(unittest.TestCase):
    """Tests for compact age formatting."""

    def test_seconds(self):
        self.assertEqual(format_age_compact(30), "30s")

    def test_minutes(self):
        self.assertEqual(format_age_compact(120), "2m")
        self.assertEqual(format_age_compact(3540), "59m")

    def test_hours(self):
        self.assertEqual(format_age_compact(3600), "1h")
        self.assertEqual(format_age_compact(7200), "2h")


class TestAgeSeconds(unittest.TestCase):
    """Tests for age calculation."""

    def test_none_input(self):
        self.assertIsNone(age_seconds(None))

    def test_recent_datetime(self):
        one_minute_ago = datetime.now() - timedelta(minutes=1)
        result = age_seconds(one_minute_ago)
        self.assertIsNotNone(result)
        self.assertGreater(result, 55)
        self.assertLess(result, 65)

    def test_timezone_aware(self):
        one_minute_ago = datetime.now(timezone.utc) - timedelta(minutes=1)
        result = age_seconds(one_minute_ago)
        self.assertIsNotNone(result)
        self.assertGreater(result, 55)
        self.assertLess(result, 65)


class TestFormatBmkMode(unittest.TestCase):
    """Tests for BMK mode formatting."""

    def test_bool_true(self):
        self.assertEqual(format_bmk_mode(True), "1")

    def test_bool_false(self):
        self.assertEqual(format_bmk_mode(False), "0")

    def test_integer(self):
        self.assertEqual(format_bmk_mode(2), "2")
        self.assertEqual(format_bmk_mode(0), "0")

    def test_float_truncated(self):
        self.assertEqual(format_bmk_mode(2.0), "2")
        self.assertEqual(format_bmk_mode(1.5), "1")

    def test_string_passthrough(self):
        self.assertEqual(format_bmk_mode("Auto"), "Auto")
        self.assertEqual(format_bmk_mode("  Off  "), "Off")

    def test_none(self):
        self.assertIsNone(format_bmk_mode(None))

    def test_empty_string(self):
        self.assertIsNone(format_bmk_mode(""))
        self.assertIsNone(format_bmk_mode("   "))


class TestComposeStatusText(unittest.TestCase):
    """Tests for status text composition."""

    def test_single_part(self):
        result = compose_status_text(["Hello"])
        self.assertEqual(result, "Hello")

    def test_multiple_parts(self):
        result = compose_status_text(["Part1", "Part2", "Part3"])
        self.assertEqual(result, "Part1 • Part2 • Part3")

    def test_custom_separator(self):
        result = compose_status_text(["A", "B"], separator=" | ")
        self.assertEqual(result, "A | B")

    def test_empty_parts_filtered(self):
        result = compose_status_text(["A", "", "B", None, "C"])
        self.assertEqual(result, "A • B • C")

    def test_max_length_truncation(self):
        result = compose_status_text(["Long1", "Long2", "Long3"], max_len=15)
        self.assertLessEqual(len(result), 15)


if __name__ == "__main__":
    unittest.main()
