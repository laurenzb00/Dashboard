"""Tests fuer core.heat_demand (temperaturabhaengiges Verbrauchsmodell)."""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import heating_stats as hs  # noqa: E402
from core.heat_demand import DemandModel, fit, quiet_hours, week_outlook  # noqa: E402

CFG = hs.StorageConfig()


def _history(days=5, base=0.6, per_k=0.15, burn_hours=(6, 7)):
    """15-min-Buckets: Aussentemperatur schwankt taeglich 0..10 °C, Verbrauch = base + per_k*(18-T)."""
    import math
    start = datetime(2026, 1, 10, 0, 0)
    buckets, p = [], 65.0
    for i in range(days * 96):
        ts = start + timedelta(minutes=15 * i)
        h = ts.hour + ts.minute / 60
        t_out = 5 + 5 * math.sin((h - 9) / 24 * 2 * math.pi)
        burning = burn_hours[0] <= ts.hour < burn_hours[1]
        if burning:
            p += 3.0
        else:
            kw = base + per_k * max(0, 18 - t_out)
            p -= kw * 0.25 / (CFG.puffer_kwh_per_k + 0)  # Boiler konstant
        buckets.append(hs.Bucket(ts=ts, kessel=80.0 if burning else 40.0, top=p, mid=p, bot=p,
                                 warm=50.0, outdoor=t_out))
    return buckets


class TestDemandModel(unittest.TestCase):
    def test_fit_recovers_parameters(self):
        model = fit(quiet_hours(_history(), CFG))
        self.assertTrue(model.temperature_dependent)
        self.assertAlmostEqual(model.per_k_kw, 0.15, delta=0.02)
        self.assertAlmostEqual(model.base_kw, 0.6, delta=0.15)
        self.assertGreater(model.r2, 0.9)
        self.assertAlmostEqual(model.kw_at(8.0), model.base_kw + model.per_k_kw * 10, places=6)
        self.assertGreater(model.kw_at(-10.0), model.kw_at(10.0))

    def test_kessel_hours_excluded(self):
        hours = quiet_hours(_history(days=1), CFG)
        self.assertFalse(any(6 <= h.hour < 7 for h, _, _ in hours))

    def test_constant_when_no_temperature_spread(self):
        buckets = [hs.Bucket(ts=datetime(2026, 1, 10) + timedelta(minutes=15 * i), kessel=40, top=60 - 0.02 * i,
                             mid=60 - 0.02 * i, bot=60 - 0.02 * i, warm=50, outdoor=5.0) for i in range(200)]
        model = fit(quiet_hours(buckets, CFG))
        self.assertFalse(model.temperature_dependent)
        self.assertEqual(model.per_k_kw, 0.0)

    def test_too_little_data(self):
        self.assertIsNone(fit([]))

    def test_week_outlook(self):
        model = DemandModel(base_kw=1.0, per_k_kw=0.2, hours=100, r2=0.9, t_min=0, t_max=10)
        now = datetime(2026, 1, 10, 0, 0)
        temps = {}
        for h in range(24 * 8):
            t_local = now + timedelta(hours=h, minutes=30)
            temps[t_local.astimezone(timezone.utc).replace(minute=0)] = 8.0
        ol = week_outlook(model, temps, usable_now=50.0, avg_firing_kwh=120.0, now=now)
        self.assertAlmostEqual(ol.demand_kwh, 7 * 24 * 3.0, delta=0.5)   # 1 + 0.2*10 = 3 kW
        self.assertEqual(ol.firings, 4)                                     # (504 - 50) / 120 -> 4
        self.assertAlmostEqual(ol.mean_temp, 8.0)


if __name__ == "__main__":
    unittest.main()
