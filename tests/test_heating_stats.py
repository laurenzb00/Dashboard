"""Tests fuer core.heating_stats (Einheizen am Kessel, Waermeeintrag Holz/Solar)."""

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.heating_stats import Bucket, StorageConfig, analyze, detect_events, kessel_active, sun_elevation_deg  # noqa: E402

CFG = StorageConfig(puffer_liter=4000, boiler_liter=500)


def _b(ts, kessel, puffer, warm=55.0, outdoor=5.0, **_ignored):
    return Bucket(ts=ts, kessel=kessel, top=puffer, mid=puffer, bot=puffer, warm=warm, outdoor=outdoor)


class TestKessel(unittest.TestCase):
    def test_kessel_must_be_hot_and_above_puffer(self):
        t = datetime(2026, 2, 9, 12)
        self.assertTrue(kessel_active(_b(t, 70, 50)))
        self.assertFalse(kessel_active(_b(t, 61, 63)))   # nur passiv mit Puffer temperiert
        self.assertFalse(kessel_active(_b(t, 45, 30)))   # zu kalt


class TestAnalyze(unittest.TestCase):
    def _day(self):
        start = datetime(2026, 2, 9, 0, 0)
        buckets = []
        puffer = 40.0
        for i in range(96):
            ts = start + timedelta(minutes=15 * i)
            h = ts.hour + ts.minute / 60
            kessel = 45.0
            pv = 0.0
            if 8 <= h < 12:               # Einheizen am Vormittag: +20 K im Puffer
                kessel = 78.0
                puffer += 20.0 / 16
            elif 13 <= h < 15:            # Sonne, Kessel kalt: Solarthermie +2 K
                pv = 3.0
                puffer += 2.0 / 8
            elif h >= 18:                 # Abend: Verbrauch
                puffer -= 0.25
            buckets.append(_b(ts, kessel, puffer, pv=pv))
        return buckets

    def test_split_wood_and_solar(self):
        stats = analyze(self._day(), CFG, first_day=date(2026, 2, 9), last_day=date(2026, 2, 9))
        kwh_per_k = 4000 * 1.163 / 1000
        self.assertAlmostEqual(stats.wood_kwh, 20 * kwh_per_k, delta=1.0)
        self.assertAlmostEqual(stats.solar_kwh, 2 * kwh_per_k, delta=1.0)
        self.assertEqual(len(stats.events), 1)
        self.assertEqual(stats.days[0].events, 1)
        self.assertGreater(stats.days[0].used_kwh, 5)
        self.assertAlmostEqual(stats.solar_share_pct, 100 * 2 / 22, delta=2)

    def test_event_bounds(self):
        self.assertEqual(len(detect_events(self._day())), 1)
        ev = analyze(self._day(), CFG).events[0]
        self.assertEqual(ev.start, datetime(2026, 2, 9, 8, 0))
        self.assertEqual(ev.end, datetime(2026, 2, 9, 12, 0))
        self.assertGreater(ev.wood_kwh, 80)

    def test_noise_is_ignored(self):
        start = datetime(2026, 2, 9, 12)
        buckets = [_b(start + timedelta(minutes=15 * i), 45, 50 + (0.05 if i % 2 else 0), pv=2.0) for i in range(40)]
        stats = analyze(buckets, CFG)
        self.assertAlmostEqual(stats.solar_kwh, 0.0)

    def test_rise_without_kessel_at_night_is_not_solar(self):
        start = datetime(2026, 1, 15, 0, 0)
        buckets = [_b(start + timedelta(minutes=15 * i), 40, 50 + 0.2 * i) for i in range(16)]  # 00-04 Uhr
        stats = analyze(buckets, CFG)
        self.assertAlmostEqual(stats.solar_kwh, 0.0)
        self.assertAlmostEqual(stats.wood_kwh, 0.0)

    def test_boiler_rise_during_day_is_solar(self):
        start = datetime(2026, 7, 1, 11, 0)
        buckets = [_b(start + timedelta(minutes=15 * i), 40, 50, warm=50 + 1.0 * i) for i in range(9)]
        stats = analyze(buckets, CFG)
        self.assertAlmostEqual(stats.solar_kwh, 8 * 500 * 1.163 / 1000, delta=0.05)


class TestSun(unittest.TestCase):
    def test_elevation(self):
        noon_summer = sun_elevation_deg(datetime(2026, 6, 21, 13, 10), 48.26, 13.04)
        self.assertAlmostEqual(noon_summer, 65.2, delta=1.0)
        self.assertLess(sun_elevation_deg(datetime(2026, 6, 21, 1, 0), 48.26, 13.04), -10)
        self.assertLess(sun_elevation_deg(datetime(2026, 12, 21, 7, 30), 48.26, 13.04), 0)   # vor Sonnenaufgang
        self.assertGreater(sun_elevation_deg(datetime(2026, 12, 21, 12, 0), 48.26, 13.04), 15)


class TestEmpty(unittest.TestCase):
    def test_empty(self):
        stats = analyze([], CFG, first_day=date(2026, 2, 1), last_day=date(2026, 2, 7))
        self.assertEqual(len(stats.days), 7)
        self.assertEqual(stats.wood_kwh, 0)
        self.assertIsNone(stats.solar_share_pct)


if __name__ == "__main__":
    unittest.main()
