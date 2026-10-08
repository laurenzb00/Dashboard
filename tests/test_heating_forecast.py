"""Tests fuer core.heating_forecast (Einheiz-Empfehlung)."""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import heating_stats as hs  # noqa: E402
from core.heating_forecast import consumption_rate_kw, plan, solar_factor, week_solar  # noqa: E402

CFG = hs.StorageConfig()


def _pv(day: date, peak_kw: float) -> dict:
    """PV-Prognose 9-16 Uhr lokal mit konstanter Leistung (Schluessel: Stundenende UTC)."""
    out = {}
    for h in range(10, 17):
        end_local = datetime.combine(day, datetime.min.time()) + timedelta(hours=h)
        out[end_local.astimezone(timezone.utc)] = peak_kw
    return out


class TestPlan(unittest.TestCase):
    NOW = datetime(2026, 1, 15, 14, 0)

    def test_today_evening(self):
        rec = plan(usable_now=10.0, rate_kw=2.0, factor=None, pv_forecast_utc=None, now=self.NOW)
        self.assertEqual(rec.level, "today")
        self.assertEqual(rec.empty_at, datetime(2026, 1, 15, 19, 0))
        self.assertIn("19:00", rec.detail)

    def test_now(self):
        rec = plan(usable_now=3.0, rate_kw=2.0, factor=None, pv_forecast_utc=None, now=self.NOW)
        self.assertEqual(rec.level, "now")

    def test_enough_for_36h(self):
        rec = plan(usable_now=100.0, rate_kw=2.0, factor=None, pv_forecast_utc=None, now=self.NOW)
        self.assertEqual(rec.level, "ok")
        self.assertIsNone(rec.empty_at)

    def test_sun_tomorrow_postpones(self):
        # 40 kWh, 1,5 kW Verbrauch -> ohne Sonne leer morgen ~16:40
        without = plan(40.0, 1.5, None, None, now=self.NOW)
        self.assertEqual(without.level, "soon")
        sunny = plan(40.0, 1.5, 0.8, _pv(date(2026, 1, 16), 5.0), now=self.NOW)
        self.assertEqual(sunny.level, "ok")
        self.assertAlmostEqual(sunny.solar_tomorrow_kwh, 0.8 * 5.0 * 7, delta=0.1)
        self.assertIn("Sonne morgen", sunny.detail)

    def test_solar_fn_uses_tank_temperature(self):
        # Kollektor-Modell: Ertrag sinkt, je voller (heisser) der Speicher
        def solar_fn(t, e):
            return 4.0 * max(0.0, 1.0 - e / 200.0) if 10 <= t.hour < 16 else 0.0
        low = plan(20.0, 1.0, None, None, now=self.NOW.replace(hour=8), solar_fn=solar_fn)
        high = plan(150.0, 1.0, None, None, now=self.NOW.replace(hour=8), solar_fn=solar_fn)
        self.assertGreater(low.solar_rest_today_kwh, high.solar_rest_today_kwh)
        self.assertIsNotNone(low.solar_tomorrow_kwh)

    def test_full_tank_caps_energy(self):
        rec = plan(95.0, 0.0, None, None, now=self.NOW.replace(hour=8), solar_fn=lambda t, e: 10.0, e_max=100.0)
        self.assertLessEqual(max(e for _, e in rec.projection), 100.0 + 1e-9)

    def test_week_solar_counts_only_storable(self):
        now = datetime(2026, 4, 1, 0, 0)
        out = week_solar(90.0, now, lambda t: 0.0, lambda t, e: 5.0 if 10 <= t.hour < 14 else 0.0, 100.0, days=3)
        self.assertEqual(len(out), 3)
        self.assertAlmostEqual(sum(out.values()), 10.0, delta=1e-6)     # nur bis voll
        out2 = week_solar(0.0, now, lambda t: 1.0, lambda t, e: 5.0 if 10 <= t.hour < 14 else 0.0, 1000.0, days=2)
        self.assertAlmostEqual(out2[now.date()], 20.0, delta=1e-6)

    def test_kessel_running(self):
        rec = plan(20.0, 2.0, None, None, now=self.NOW, kessel_active_now=True)
        self.assertEqual(rec.level, "burning")

    def test_unknown(self):
        self.assertEqual(plan(None, 2.0, None, None, now=self.NOW).level, "unknown")
        self.assertEqual(plan(20.0, None, None, None, now=self.NOW).level, "unknown")


class TestInputs(unittest.TestCase):
    def test_rate_ignores_heating(self):
        start = datetime(2026, 1, 15, 0, 0)
        buckets, p = [], 60.0
        for i in range(48):                      # 12 h
            ts = start + timedelta(minutes=15 * i)
            kessel = 78.0 if 4 <= ts.hour < 6 else 40.0
            p += (2.5 if kessel > 60 else -2.0 * 0.25 / CFG.puffer_kwh_per_k)
            buckets.append(hs.Bucket(ts=ts, kessel=kessel, top=p, mid=p, bot=p, warm=50.0, outdoor=0.0))
        self.assertAlmostEqual(consumption_rate_kw(buckets, CFG), 2.0, delta=0.05)

    def test_solar_factor(self):
        season = hs.HeatingStats(days=[hs.DayStats(date(2026, 9, d), solar_kwh=8.0) for d in range(1, 8)])
        pv = {date(2026, 9, d): 20.0 for d in range(1, 8)}
        self.assertAlmostEqual(solar_factor(season, pv), 0.4)
        self.assertIsNone(solar_factor(season, {}))


if __name__ == "__main__":
    unittest.main()
