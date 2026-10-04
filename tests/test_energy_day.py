"""Tests fuer core.energy_day (Tagesbilanz, Autarkie, Akku-Auswertung)."""

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.energy_day import Sample, bin_samples, summarize  # noqa: E402


def _samples(n_minutes, pv=0.0, load=1.0, grid=1.0, soc=50.0, start=datetime(2026, 7, 1, 0, 0)):
    return [Sample(ts=start + timedelta(minutes=i), pv=pv, load=load, grid=grid, soc=soc) for i in range(n_minutes + 1)]


class TestSummarize(unittest.TestCase):
    def test_energy_integration(self):
        s = summarize(_samples(60, pv=2.0, load=1.0, grid=-1.0))
        self.assertAlmostEqual(s.pv_kwh, 2.0, places=6)
        self.assertAlmostEqual(s.load_kwh, 1.0, places=6)
        self.assertAlmostEqual(s.export_kwh, 1.0, places=6)
        self.assertAlmostEqual(s.import_kwh, 0.0, places=6)
        self.assertAlmostEqual(s.autarky_pct, 100.0)
        self.assertAlmostEqual(s.self_consumption_pct, 50.0)

    def test_full_import_means_zero_autarky(self):
        s = summarize(_samples(60, pv=0.0, load=1.0, grid=1.0))
        self.assertAlmostEqual(s.autarky_pct, 0.0)
        self.assertIsNone(s.self_consumption_pct)

    def test_gaps_are_not_bridged(self):
        a = _samples(10, pv=1.0)
        b = _samples(10, pv=1.0, start=a[-1].ts + timedelta(hours=3))
        s = summarize(a + b)
        self.assertAlmostEqual(s.pv_kwh, 2 * 10 / 60, places=6)

    def test_empty_battery_detected(self):
        start = datetime(2026, 7, 1, 18, 0)
        socs = [30, 20, 10, 7, 7, 7, 7, 7, 7, 7, 7, 7, 15]
        smp = [Sample(ts=start + timedelta(minutes=5 * i), pv=0, load=1, grid=1, soc=v) for i, v in enumerate(socs)]
        s = summarize(smp, floor=7.0)
        self.assertEqual(len(s.empty_spans), 1)
        self.assertEqual(s.empty_at, start + timedelta(minutes=15))
        self.assertEqual(s.soc_min, 7)

    def test_high_floor_is_not_empty(self):
        smp = _samples(60, soc=35.0)
        s = summarize(smp, floor=35.0)
        self.assertEqual(s.empty_spans, [])

    def test_full_at(self):
        start = datetime(2026, 7, 1, 10, 0)
        smp = [Sample(ts=start + timedelta(minutes=i), pv=3, load=1, grid=0, soc=v)
               for i, v in enumerate([90, 95, 99.5, 100])]
        self.assertEqual(summarize(smp).full_at, start + timedelta(minutes=2))

    def test_empty_input(self):
        s = summarize([])
        self.assertEqual(s.samples, 0)
        self.assertIsNone(s.autarky_pct)


class TestBinning(unittest.TestCase):
    def test_bin_means(self):
        b = bin_samples(_samples(9, pv=2.0, soc=40.0), minutes=5)
        self.assertEqual(len(b), 2)
        self.assertAlmostEqual(b[0]["pv_power"], 2.0)
        self.assertAlmostEqual(b[0]["soc"], 40.0)
        self.assertEqual(b[0]["timestamp"], datetime(2026, 7, 1, 0, 2, 30))


if __name__ == "__main__":
    unittest.main()
