"""Unit tests for core.utils – utility functions."""

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.utils import safe_float, parse_timestamp


class TestSafeFloat(unittest.TestCase):
    """Tests for safe_float conversion function."""

    def test_valid_float(self):
        self.assertAlmostEqual(safe_float(3.14), 3.14)

    def test_valid_int(self):
        self.assertAlmostEqual(safe_float(42), 42.0)

    def test_valid_string(self):
        self.assertAlmostEqual(safe_float("2.5"), 2.5)

    def test_none_returns_none(self):
        self.assertIsNone(safe_float(None))

    def test_invalid_string_returns_none(self):
        self.assertIsNone(safe_float("abc"))
        self.assertIsNone(safe_float("not-a-number"))

    def test_empty_string_returns_none(self):
        self.assertIsNone(safe_float(""))


class TestParseTimestamp(unittest.TestCase):
    """Tests for timestamp parsing."""

    def test_valid_iso_string(self):
        result = parse_timestamp("2024-01-15T10:30:00+00:00")
        self.assertGreater(result, 0)

    def test_datetime_object(self):
        dt = datetime(2024, 1, 15, 10, 30, 0, tzinfo=timezone.utc)
        result = parse_timestamp(dt)
        self.assertGreater(result, 0)

    def test_none_returns_zero(self):
        self.assertEqual(parse_timestamp(None), 0.0)

    def test_empty_string_returns_zero(self):
        self.assertEqual(parse_timestamp(""), 0.0)

    def test_invalid_string_returns_zero(self):
        self.assertEqual(parse_timestamp("not-a-timestamp"), 0.0)


if __name__ == "__main__":
    unittest.main()
