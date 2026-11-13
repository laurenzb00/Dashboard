"""Tests zu den Erweiterungen vom 13.11.2026: Prognose-Protokoll (DNI/DHI/Temperatur), Bandbreite,
Solarthermie-Waechter, Speicherverlust-Term, Strom-Auswertung."""
import os
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import alerts, energy_stats as es, forecast_log, forecast_models, heating_forecast as hf  # noqa: E402


def _fl_conn():
    c = sqlite3.connect(":memory:")
    c.executescript(
        "CREATE TABLE weather (hour_end INTEGER PRIMARY KEY, ghi REAL, dni REAL, dhi REAL, temp REAL, src TEXT);"
        "CREATE TABLE pv_hours (hour_end INTEGER PRIMARY KEY, pv_kw REAL, n INTEGER);"
        "CREATE TABLE heat_hours (hour_start INTEGER PRIMARY KEY, quiet_kwh REAL, quiet_min REAL, free_kwh REAL,"
        " free_min REAL, kessel_min REAL, tank_c REAL, outdoor_c REAL, tank_mid_c REAL);")
    return c


class TestForecastLogColumns(unittest.TestCase):
    def test_migration_adds_columns_to_old_table(self):
        c = _fl_conn()
        c.execute("CREATE TABLE pv_forecast_log (target_hour INTEGER NOT NULL, lead TEXT NOT NULL, kw REAL, "
                  "kw_raw REAL, ghi REAL, made_at INTEGER, PRIMARY KEY (target_hour, lead))")
        c.execute("INSERT INTO pv_forecast_log VALUES (1000, 'd1', 1.0, 1.0, 50.0, 10)")
        forecast_log._ensure(c)
        forecast_log._ensure(c)                      # zweimal = kein Fehler
        cols = [r[1] for r in c.execute("PRAGMA table_info(pv_forecast_log)")]
        self.assertEqual(cols[-3:], ["dni", "dhi", "temp"])
        self.assertEqual(c.execute("SELECT kw, dni FROM pv_forecast_log").fetchone(), (1.0, None))

    def test_record_stores_dni_dhi_temp_and_stays_compatible(self):
        c = _fl_conn()
        now = datetime(2026, 11, 13, 8, 0).timestamp()
        t = [now + 2 * 3600, now + 3 * 3600]
        n = forecast_log.record_pv(t, [1.0, 2.0], [100.0, 200.0], made_at=now, conn=c,
                                   dni=[10.0, float("nan")], dhi=[90.0, 80.0], temp=[4.0, 5.0])
        self.assertEqual(n, 2)
        rows = c.execute("SELECT dni, dhi, temp FROM pv_forecast_log ORDER BY target_hour").fetchall()
        self.assertEqual(rows, [(10.0, 90.0, 4.0), (None, 80.0, 5.0)])
        # alter Aufruf ohne die neuen Spalten
        forecast_log.record_pv([now + 4 * 3600], [3.0], [300.0], made_at=now, conn=c)
        self.assertEqual(c.execute("SELECT COUNT(*) FROM pv_forecast_log WHERE dni IS NULL").fetchone()[0], 2)

    def test_model_log_migration_and_record(self):
        c = _fl_conn()
        c.execute("CREATE TABLE pv_model_log (model TEXT NOT NULL, target_hour INTEGER NOT NULL, lead TEXT NOT NULL, "
                  "kw REAL, ghi REAL, made_at INTEGER, PRIMARY KEY (model, target_hour, lead))")
        now = datetime(2026, 11, 13, 8, 0).timestamp()
        forecast_models.record(c, "icon_eu", [now + 7200], [1.5], [120.0], made_at=now, dni=[300.0], temp=[3.0])
        forecast_models.record(c, "gfs", [now + 7200], [1.5], [120.0], made_at=now)
        rows = dict((r[0], r[1:]) for r in c.execute("SELECT model, dni, temp FROM pv_model_log"))
        self.assertEqual(rows, {"icon_eu": (300.0, 3.0), "gfs": (None, None)})


class TestBand(unittest.TestCase):
    def _fill(self, c, ratios, now):
        forecast_log._ensure(c)
        day0 = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
        for i, r in enumerate(ratios):
            d = day0 - timedelta(days=i + 1)
            for h in range(8, 17):
                t = int((d + timedelta(hours=h)).timestamp())
                c.execute("INSERT INTO pv_forecast_log (target_hour, lead, kw, kw_raw, ghi, made_at) VALUES (?,?,?,?,?,?)",
                          (t, "d1", 2.0, 2.0, 300.0, t - 86400))
                c.execute("INSERT INTO pv_hours VALUES (?,?,?)", (t, 2.0 * r, 360))

    def test_band_quantiles_and_fog(self):
        c = _fl_conn()
        now = datetime(2026, 11, 13, 12, 0).timestamp()
        self._fill(c, [1.0] * 14 + [0.3, 0.3, 1.6, 1.6, 1.2, 0.8], now)
        b = forecast_log.pv_band(conn=c, now=now)
        self.assertEqual(b["days"], 20)
        self.assertAlmostEqual(b["fog_share"], 0.1)
        self.assertLess(b["lo"], 1.0)
        self.assertGreater(b["hi"], 1.0)
        lo, hi = forecast_log.band_kwh(10.0, b)
        self.assertLess(lo, 10.0)
        self.assertGreater(hi, 10.0)
        self.assertIn("von 20 Tagen", forecast_log.fog_hint(b))
        self.assertRegex(forecast_log.band_text(10.0, b), r"^10 kWh \(\d+–\d+\)$")

    def test_too_few_days_gives_no_band(self):
        c = _fl_conn()
        now = datetime(2026, 11, 13, 12, 0).timestamp()
        self._fill(c, [1.0] * 5, now)
        self.assertIsNone(forecast_log.pv_band(conn=c, now=now))
        self.assertEqual(forecast_log.band_text(10.0, None), "10 kWh")
        self.assertEqual(forecast_log.fog_hint(None), "")
        self.assertEqual(forecast_log.band_text(None, None), "–")

    def test_band_never_excludes_the_forecast_itself(self):
        c = _fl_conn()
        now = datetime(2026, 11, 13, 12, 0).timestamp()
        self._fill(c, [1.5] * 12, now)               # immer zu niedrig prognostiziert
        b = forecast_log.pv_band(conn=c, now=now)
        self.assertEqual(b["lo"], 1.0)
        self.assertAlmostEqual(b["hi"], 1.5)


class TestSolarWatch(unittest.TestCase):
    def _day(self, c, day, ghi_peak, gain_per_sun_hour, kessel=False):
        for h in range(24):
            t = int((day + timedelta(hours=h)).timestamp())
            sun = 9 <= h <= 15
            ghi = ghi_peak if sun else 0.0
            free = gain_per_sun_hour if sun else -1.0
            c.execute("INSERT OR REPLACE INTO heat_hours VALUES (?,?,?,?,?,?,?,?,?)",
                      (t, max(0.0, -free), 60.0, free, 60.0, 60.0 if (kessel and h == 10) else 0.0, 50.0, 10.0, 48.0))
            c.execute("INSERT OR REPLACE INTO weather VALUES (?,?,?,?,?,?)", (t + 3600, ghi, 0, 0, 10.0, "forecast"))

    def test_two_sunny_days_without_gain_alert(self):
        c = _fl_conn()
        now = datetime(2026, 7, 20, 9, 0)
        d = now.replace(hour=0)
        self._day(c, d - timedelta(days=1), 700.0, -1.0)
        self._day(c, d - timedelta(days=2), 700.0, -1.0)
        res = alerts.check_solar(conn=c, now=now)
        self.assertIsNotNone(res)
        self.assertIn("Solarthermie", res[0])
        self.assertIn("19.07.", res[1])

    def test_working_system_no_alert(self):
        c = _fl_conn()
        now = datetime(2026, 7, 20, 9, 0)
        d = now.replace(hour=0)
        self._day(c, d - timedelta(days=1), 700.0, 6.0)
        self._day(c, d - timedelta(days=2), 700.0, -1.0)
        self.assertIsNone(alerts.check_solar(conn=c, now=now))

    def test_cloudy_or_fire_days_do_not_count(self):
        c = _fl_conn()
        now = datetime(2026, 7, 20, 9, 0)
        d = now.replace(hour=0)
        self._day(c, d - timedelta(days=1), 200.0, -1.0)                 # trueb
        self._day(c, d - timedelta(days=2), 700.0, -1.0, kessel=True)    # Feuer
        self._day(c, d - timedelta(days=3), 700.0, -1.0)                 # nur EIN Sonnentag ohne Ertrag
        self.assertIsNone(alerts.check_solar(conn=c, now=now))

    def test_gain_adds_consumption_during_sun_hours(self):
        c = _fl_conn()
        d = datetime(2026, 7, 19)
        self._day(c, d, 700.0, 2.0)
        heat = c.execute("SELECT hour_start, quiet_kwh, quiet_min, free_kwh, free_min, kessel_min FROM heat_hours").fetchall()
        ghi = dict(c.execute("SELECT hour_end - 3600, ghi FROM weather").fetchall())
        g = alerts.solar_day_gains(heat, ghi)[d.date()]
        self.assertAlmostEqual(g[0], 7 * 700.0)
        self.assertAlmostEqual(g[1], 7 * 2.0 + 7 * 1.0)        # Netto + 1 kW Verbrauch in 7 Sonnenstunden
        self.assertEqual(g[2], 7)


class TestHeatSettle(unittest.TestCase):
    def test_hours_after_fire_are_skipped(self):
        base = datetime(2026, 11, 6, 0, 0)
        rows = []
        for h in range(-6, 12):
            ts = (base + timedelta(hours=h)).timestamp()
            fire = h in (-2, -1)
            rows.append((ts, 0.0 if fire else (20.0 if h < 3 else 5.0), 0.0 if fire else 60.0, 0.0, 60.0,
                         60.0 if fire else 0.0, 60.0, 5.0, 58.0))

        orig = (alerts.hs.load_buckets, alerts.fl.heat_hour_rows, alerts._hourly_fronius)
        alerts.hs.load_buckets = lambda *a, **k: []
        alerts.fl.heat_hour_rows = lambda b, cfg: rows
        alerts._hourly_fronius = lambda *a, **k: {}
        try:
            kw, hours, used = alerts.recent_demand(None, None, now=base + timedelta(hours=12))
        finally:
            alerts.hs.load_buckets, alerts.fl.heat_hour_rows, alerts._hourly_fronius = orig
        # Feuer bis Stunde -1 -> Stunden 0..3 (<= 4 h danach) zaehlen nicht, 4..11 schon
        self.assertEqual(len(used), 8)
        self.assertAlmostEqual(kw, 5.0)


class TestTankLoss(unittest.TestCase):
    def test_fit_recovers_coefficient(self):
        rng = np.random.default_rng(1)
        rows = []
        for _ in range(600):
            tank = rng.uniform(35, 70)
            out = rng.uniform(-5, 20)
            kw = 1.0 + 0.35 * max(0.0, 16 - out) + 0.08 * tank + rng.normal(0, 0.3)
            rows.append((max(0.0, kw), 60.0, 0.0, tank, out))
        c, ref = hf.fit_tank_loss(rows)
        self.assertAlmostEqual(c, 0.08, delta=0.01)
        self.assertAlmostEqual(ref, 52.5, delta=2.0)

    def test_fit_clips_and_needs_data(self):
        self.assertIsNone(hf.fit_tank_loss([(1.0, 60.0, 0.0, 50.0, 5.0)] * 10))
        rows = [(39.0 - t / 2.0, 60.0, 0.0, float(t), 5.0) for t in range(30, 70)] * 10     # faellt mit der Temperatur
        self.assertEqual(hf.fit_tank_loss(rows)[0], 0.0)
        rows = [(t / 2.0, 60.0, 0.0, float(t), 5.0) for t in range(30, 70)] * 10      # 0,5 kW/K -> gedeckelt
        self.assertEqual(hf.fit_tank_loss(rows)[0], hf.TANK_LOSS_CLIP[1])

    def test_plan_with_extra_fn(self):
        now = datetime(2026, 11, 13, 12, 0)
        a = hf.plan(60.0, 6.0, None, None, now=now, rate_fn=lambda t: 6.0)
        b = hf.plan(60.0, 6.0, None, None, now=now, rate_fn=lambda t: 6.0, extra_fn=lambda e: -2.0)
        c = hf.plan(60.0, 6.0, None, None, now=now, rate_fn=lambda t: 6.0, extra_fn=lambda e: 2.0)
        self.assertEqual(a.empty_at, now + timedelta(hours=10))
        self.assertEqual(b.empty_at, now + timedelta(hours=15))
        self.assertLess(c.empty_at, a.empty_at)
        d = hf.plan(60.0, 6.0, None, None, now=now, rate_fn=lambda t: 6.0, extra_fn=lambda e: -99.0)
        self.assertGreaterEqual(min(s[1] for s in d.steps), hf.MIN_RATE_KW)


class TestEnergyStats(unittest.TestCase):
    def _conn(self):
        c = sqlite3.connect(":memory:")
        c.execute("CREATE TABLE fronius (timestamp TEXT, pv_power REAL, grid_power REAL, batt_power REAL, soc REAL, load_power REAL)")
        return c

    def test_months_balance(self):
        c = self._conn()
        t0 = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
        rows = []
        for h in range(48):
            ts = (t0 + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
            day = 8 <= h % 24 < 16
            rows.append((ts, 3000.0 if day else None, -1.0 if day else 1.0, 0.0, 50.0, -2.0))
        c.executemany("INSERT INTO fronius VALUES (?,?,?,?,?,?)", rows)
        m = es.load_months(c)
        self.assertEqual(len(m), 1)
        self.assertEqual(m[0].hours, 48)
        self.assertAlmostEqual(m[0].pv_kwh, 48.0)           # 16 h * 3 kW (W -> kW)
        self.assertAlmostEqual(m[0].load_kwh, 96.0)
        self.assertAlmostEqual(m[0].import_kwh, 32.0)
        self.assertAlmostEqual(m[0].export_kwh, 16.0)
        self.assertAlmostEqual(m[0].autarky_pct, 100 * (1 - 32 / 96))
        self.assertAlmostEqual(m[0].self_use_pct, 100 * (1 - 16 / 48))

    def _series(self, days=7, every_h=6, dur=45, kw=2.7):
        n = days * 1440
        t = np.arange(n) * 60.0 + datetime(2026, 11, 1).timestamp()
        rng = np.random.default_rng(3)
        load = 0.6 + rng.normal(0, 0.05, n)
        for start in range(60, n - dur, every_h * 60):
            load[start:start + dur] += kw
        # ein unregelmaessiger Verbraucher (Kochen): unterschiedlich lang
        for i, start in enumerate(range(700, n - 200, 1440)):
            load[start:start + 20 + 25 * (i % 4)] += 2.0
        return t, load

    def test_cyclic_device_found(self):
        t, load = self._series()
        found = es.find_cyclic(t, load, np.where(load > 2, load, 0.0))
        self.assertGreaterEqual(len(found), 1)
        c = found[0]
        self.assertAlmostEqual(c.kw, 2.7, delta=0.15)
        self.assertAlmostEqual(c.minutes, 45, delta=2)
        self.assertAlmostEqual(c.runs_per_day, 4.0, delta=0.3)
        self.assertAlmostEqual(c.kwh_per_day, 4 * 2.7 * 0.75, delta=0.8)
        self.assertGreater(c.grid_share_pct, 90)

    def test_no_cyclic_in_flat_load_and_baseload(self):
        n = 3 * 1440
        t = np.arange(n) * 60.0
        load = np.full(n, 0.5)
        load[::7] = 0.9
        self.assertEqual(es.find_cyclic(t, load), [])
        self.assertAlmostEqual(es.baseload(load), 0.5)
        self.assertIsNone(es.baseload(load[:100]))

    def test_summary_lines(self):
        s = es.EnergyStats(months=[es.MonthBalance("2026-10", 744, 790, 1085, 517, 166)], baseload_kw=0.53,
                           baseload_share_pct=38.0,
                           cyclic=[es.CyclicLoad(2.7, 45, 4.2, 8.9, 28.0, 80.0)])
        lines = es.summary_lines(s)
        self.assertIn("Grundlast 0,53 kW", lines[0])
        self.assertIn("Dauerläufer 2,7 kW", lines[1])
        self.assertIn("Okt 26 52 %", lines[2])
        self.assertTrue(lines[3].startswith("Eigenverbrauch"))
        self.assertEqual(es.summary_lines(es.EnergyStats()), [])


if __name__ == "__main__":
    unittest.main()


class TestComfort(unittest.TestCase):
    def setUp(self):
        self.cfg = __import__("core.heating_stats", fromlist=["x"]).StorageConfig()

    def test_comfort_before_empty(self):
        now = datetime(2026, 11, 13, 12, 0)
        r = hf.plan(200.0, 6.0, None, None, now=now, rate_fn=lambda t: 6.0)
        c = hf.comfort_at(r.projection, self.cfg)
        self.assertIsNotNone(c)
        self.assertLess(c, r.empty_at)                 # oben kalt, bevor der Puffer ganz leer ist
        kpk = self.cfg.puffer_kwh_per_k + self.cfg.boiler_kwh_per_k
        self.assertAlmostEqual(hf.comfort_mean_c(self.cfg), 42.0, places=6)
        hours = (c - now).total_seconds() / 3600.0
        self.assertAlmostEqual(hours, (200.0 - (42.0 - 35.0) * kpk) / 6.0, delta=0.6)

    def test_already_cold_and_never(self):
        now = datetime(2026, 11, 13, 12, 0)
        r = hf.plan(10.0, 6.0, None, None, now=now, rate_fn=lambda t: 6.0)
        self.assertEqual(hf.comfort_at(r.projection, self.cfg), now)
        r = hf.plan(300.0, 0.0, None, None, now=now, rate_fn=lambda t: 0.0)
        self.assertIsNone(hf.comfort_at(r.projection, self.cfg))
        self.assertIsNone(hf.comfort_at([], self.cfg))

    def test_threshold_from_config(self):
        import dataclasses
        warm = dataclasses.replace(self.cfg, comfort_top_c=60.0)
        self.assertAlmostEqual(hf.comfort_mean_c(warm), 57.0)
        now = datetime(2026, 11, 13, 12, 0)
        r = hf.plan(200.0, 6.0, None, None, now=now, rate_fn=lambda t: 6.0)
        self.assertLess(hf.comfort_at(r.projection, warm), hf.comfort_at(r.projection, self.cfg))
