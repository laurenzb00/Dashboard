"""Unit tests for core.heating_events – heating event detection heuristic."""

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.heating_events import compute_last_heating_event, parse_iso_dt


class TestParseIsoDt(unittest.TestCase):
    def test_basic_iso(self):
        dt = parse_iso_dt("2025-06-15 14:30:00")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.hour, 14)

    def test_iso_with_z(self):
        dt = parse_iso_dt("2025-06-15T14:30:00Z")
        self.assertIsNotNone(dt)

    def test_none_input(self):
        self.assertIsNone(parse_iso_dt(None))
        self.assertIsNone(parse_iso_dt(""))

    def test_invalid_string(self):
        self.assertIsNone(parse_iso_dt("not-a-date"))


class TestComputeLastHeatingEvent(unittest.TestCase):
    """Test heating event detection with synthetic temperature data."""

    def _make_rows(self, kessel_temps, puffer_temps, start=None, interval_minutes=1):
        """Helper: generate rows with matching timestamp/kessel/top."""
        start = start or datetime(2025, 6, 15, 10, 0, 0)
        rows = []
        for i, (k, p) in enumerate(zip(kessel_temps, puffer_temps)):
            ts = (start + timedelta(minutes=i * interval_minutes)).isoformat()
            rows.append({"timestamp": ts, "kessel": k, "top": p})
        return rows

    def test_no_data(self):
        result = compute_last_heating_event([])
        self.assertIsNone(result)

    def test_too_few_points(self):
        rows = self._make_rows([30, 30, 30], [40, 40, 40])
        result = compute_last_heating_event(rows)
        self.assertIsNone(result)

    def test_flat_temperatures_no_event(self):
        """Constant temps -> no heating event detected."""
        kessel = [35.0] * 120
        puffer = [50.0] * 120
        rows = self._make_rows(kessel, puffer)
        result = compute_last_heating_event(rows)
        self.assertIsNone(result)

    def test_strong_kessel_rise_detected(self):
        """A strong Kessel rise (>10 deg in 60 min) should be detected."""
        # 30 minutes flat, then 60 minutes rising by ~15 deg, then 30 minutes flat
        kessel = [30.0] * 30
        for i in range(60):
            kessel.append(30.0 + i * 0.25)  # 0..15 deg rise
        kessel += [45.0] * 30
        puffer = [50.0] * len(kessel)
        rows = self._make_rows(kessel, puffer)
        result = compute_last_heating_event(rows)
        self.assertIsNotNone(result, "Should detect heating event from strong Kessel rise")

    def test_kessel_and_puffer_rise(self):
        """Combined kessel + puffer rise should be detected."""
        kessel = [30.0] * 20
        puffer = [45.0] * 20
        for i in range(60):
            kessel.append(30.0 + i * 0.2)  # 12 deg rise
            puffer.append(45.0 + i * 0.05)  # 3 deg rise
        kessel += [42.0] * 40
        puffer += [48.0] * 40
        rows = self._make_rows(kessel, puffer)
        result = compute_last_heating_event(rows)
        self.assertIsNotNone(result, "Should detect event from kessel + puffer rise")

    def test_small_rise_not_detected(self):
        """A small rise (< 8 deg) should not trigger."""
        kessel = [30.0] * 30
        for i in range(60):
            kessel.append(30.0 + i * 0.1)  # only 6 deg
        kessel += [36.0] * 30
        puffer = [50.0] * len(kessel)
        rows = self._make_rows(kessel, puffer)
        result = compute_last_heating_event(rows)
        self.assertIsNone(result, "Small rise should not be detected as heating event")

    def test_brief_noise_not_detected(self):
        """Brief random noise (not sustained rise) should not trigger a detection."""
        # Mostly flat with a few noisy +-3 deg jitters
        kessel = [30.0] * 120
        for i in (20, 40, 60, 80):
            kessel[i] = 33.0
            kessel[i + 1] = 27.0
        puffer = [50.0] * 120
        rows = self._make_rows(kessel, puffer)
        result = compute_last_heating_event(rows)
        self.assertIsNone(result, "Brief noise should not be detected as heating event")


if __name__ == "__main__":
    unittest.main()
