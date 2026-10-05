"""Tests fuer das Lern-Archiv (core.forecast_learning) und den lernenden Waermebedarf."""

import os
import sqlite3
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import forecast_learning as fl  # noqa: E402
from core import heat_demand as hd  # noqa: E402
from core import heating_stats as hs  # noqa: E402

LAT, LON = 48.2569, 13.0397
CFG = hs.StorageConfig()


def synth_wx(days, start, seed=0):
    rng = np.random.default_rng(seed)
    t = np.arange(start + 3600, start + days * 86400 + 1, 3600, dtype=float)
    day_mean = np.repeat(rng.uniform(-12, 14, days + 1), 24)[: len(t)]
    hour = (t / 3600) % 24
    temp = day_mean + 4 * np.sin((hour - 9) / 24 * 2 * np.pi)
    ghi = np.clip(500 * np.sin((hour - 6) / 12 * np.pi), 0, None) * np.repeat(rng.uniform(0.1, 1, days + 1), 24)[: len(t)]
    return {"t": t, "temp": temp, "ghi": ghi, "dni": ghi * 0, "dhi": ghi}


def true_demand(t_eff, g_eff):
    """Nicht-linear: unter 0 °C steigt der Bedarf staerker (Lueftung, Waermepumpe ...)."""
    return 0.5 + 0.12 * np.maximum(0, 16 - t_eff) + 0.05 * np.maximum(0, -t_eff) - 0.0015 * g_eff


class TestKernelHelpers(unittest.TestCase):
    def test_grid_lookup_bilinear(self):
        tab = np.array([[0.0, 1.0], [2.0, 3.0]])
        v = fl.grid_lookup(([0, 1], [0, 1]), tab, np.array([0.5]), np.array([0.5]))
        self.assertAlmostEqual(float(v[0]), 1.5)
        v = fl.grid_lookup(([0, 1], [0, 1]), tab, np.array([5.0]), np.array([-1.0]))   # Rand
        self.assertAlmostEqual(float(v[0]), 2.0)

    def test_kernel_grid_shrinks_to_prior(self):
        x = np.array([0.0, 0.1, -0.1])
        out, neff = fl.kernel_grid(([0.0, 10.0], [0.0]), (x, np.zeros(3)), (1.0, 1.0), np.array([2.0, 2.0, 2.0]),
                                   np.ones(3), prior=1.0, strength=1.0)
        self.assertAlmostEqual(out[0, 0], (6 * 0.99 + 1) / (3 * 0.99 + 1), delta=0.05)   # viele aehnliche -> ~2
        self.assertAlmostEqual(out[1, 0], 1.0, delta=1e-6)                               # nichts in der Naehe -> Prior

    def test_ema_lags(self):
        t = np.arange(0, 48 * 3600, 3600, dtype=float)
        v = np.where(t < 24 * 3600, 0.0, 10.0)
        e = fl.ema_series(t, v, 6.0)
        self.assertLess(e[24], 3.0)
        self.assertGreater(e[-1], 9.0)


class TestDemandLearning(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        start = int(datetime(2025, 10, 1, tzinfo=timezone.utc).timestamp())
        cls.wx = synth_wx(150, start)
        tau = 6.0
        t_eff = fl.ema_series(cls.wx["t"], cls.wx["temp"], tau)
        g_eff = fl.ema_series(cls.wx["t"], cls.wx["ghi"], tau)
        hours = cls.wx["t"][24:] - 3600.0           # Stundenbeginn
        mid = hours + 1800
        te = fl.hourly_series(cls.wx["t"], t_eff, mid)
        ge = fl.hourly_series(cls.wx["t"], g_eff, mid)
        rng = np.random.default_rng(3)
        cls.hours = hours
        cls.demand = true_demand(te, ge) + rng.normal(0, 0.15, len(hours))
        cls.te, cls.ge = te, ge

    def test_recovers_inertia_and_nonlinearity(self):
        t0 = time.monotonic()
        m = hd.fit_from_archive(self.hours, self.demand.copy(), np.full(len(self.hours), np.nan), self.wx)
        self.assertLess(time.monotonic() - t0, 30)
        self.assertIn(m.tau_h, (3.0, 6.0, 12.0))
        self.assertTrue(m.temperature_dependent)
        # aehnliches Wetter schlaegt das globale Modell (Knick unter 0 °C)
        self.assertLess(m.cv_rmse, m.cv_rmse_global)
        for temp in (-10.0, 0.0, 10.0):
            self.assertAlmostEqual(m.kw_at(temp, 50.0), float(true_demand(np.array([temp]), np.array([50.0]))[0]),
                                   delta=0.2)
        # Kaelter als je gesehen: weiter steigend statt flach
        self.assertGreater(m.kw_at(-25.0, 0.0), m.kw_at(-15.0, 0.0))
        # Roundtrip ueber JSON
        m2 = hd.DemandModel.from_dict(m.to_dict())
        self.assertAlmostEqual(m2.kw_at(-3.0), m.kw_at(-3.0))

    def test_vacation_days_ignored(self):
        y = self.demand.copy()
        days = (self.hours - self.hours[0]) // 86400
        vac = (days >= 40) & (days < 50)                 # 10 Tage Urlaub: nur 40 % Verbrauch
        y[vac] *= 0.4
        y[100] = 40.0                                    # Sensorsprung
        m = hd.fit_from_archive(self.hours, y, np.full(len(self.hours), np.nan), self.wx)
        self.assertGreaterEqual(len(m.anomaly_days), 8)
        self.assertLessEqual(len(m.anomaly_days), 12)
        self.assertGreaterEqual(m.outlier_hours, 1)
        for temp in (-5.0, 5.0):
            self.assertAlmostEqual(m.kw_at(temp, 50.0), float(true_demand(np.array([temp]), np.array([50.0]))[0]),
                                   delta=0.2)

    def test_normal_variation_not_flagged(self):
        m = hd.fit_from_archive(self.hours, self.demand.copy(), np.full(len(self.hours), np.nan), self.wx)
        self.assertLessEqual(len(m.anomaly_days), 1)

    def test_predictor_uses_weather_series(self):
        m = hd.fit_from_archive(self.hours, self.demand.copy(), np.full(len(self.hours), np.nan), self.wx)
        f = m.predictor(self.wx)
        i = 2000
        local = datetime.fromtimestamp(self.hours[i]).astimezone().replace(tzinfo=None)
        self.assertAlmostEqual(f(local), float(true_demand(self.te[i:i + 1], self.ge[i:i + 1])[0]), delta=0.25)


class TestSolarThermal(unittest.TestCase):
    def test_ratio_depends_on_tank_and_outdoor(self):
        start = int(datetime(2026, 3, 1, tzinfo=timezone.utc).timestamp())
        wx = synth_wx(90, start, seed=4)
        demand = hd.DemandModel(base_kw=0.5, per_k_kw=0.1, hours=100, r2=None, t_min=-10, t_max=20, version=2,
                                tb_c=16.0, tau_h=0.0)
        rng = np.random.default_rng(5)
        n = len(wx["t"]) - 1
        hour_start = wx["t"][:n] - 3600 + 3600
        hour_start = wx["t"][:n] - 3600
        t_out = fl.hourly_series(wx["t"], wx["temp"], hour_start + 1800)
        tank = rng.uniform(30, 85, n)
        pv = np.clip(wx["ghi"][:n] / 100.0, 0, None)          # PV-kW folgt der Sonne
        ratio_true = np.clip(1.2 - 0.012 * (tank - t_out), 0, None)
        gain = ratio_true * pv
        use = demand.kw_eff(t_out, np.zeros(n))
        free_kwh = gain - use                                  # Netto-Anstieg je Stunde (60 min)
        m = hd.fit_solar_thermal(hour_start, free_kwh, np.full(n, 60.0), np.zeros(n), tank, t_out, pv,
                                 {"t": np.array([]), "temp": np.array([]), "ghi": np.array([])}, demand, LAT, LON)
        self.assertIsNotNone(m)
        hot = m.ratio(0.0, 80.0)
        warm = m.ratio(20.0, 40.0)
        self.assertLess(hot, warm)
        self.assertAlmostEqual(warm, 1.2 - 0.012 * 20, delta=0.15)
        self.assertAlmostEqual(hot, 1.2 - 0.012 * 80, delta=0.15)


class FakeStore:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.executescript(
            "CREATE TABLE fronius (timestamp TEXT, pv_power REAL);"
            "CREATE TABLE heating (timestamp TEXT, kesseltemp REAL, puffer_top REAL, puffer_mid REAL, "
            "puffer_bot REAL, warmwasser REAL, aussentemp REAL);")


class TestArchiveUpdate(unittest.TestCase):
    def test_incremental_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FakeStore(os.path.join(tmp, "data.db"))
            now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
            start = now - timedelta(days=3)
            rows_f, rows_h = [], []
            p = 70.0
            for i in range(3 * 24 * 4):
                ts = start + timedelta(minutes=15 * i)
                rows_f.append((ts.strftime("%Y-%m-%d %H:%M:%S"), 2000.0 if 8 <= ts.hour < 16 else 0.0))   # alte Werte in W
                p -= 0.05
                rows_h.append((ts.strftime("%Y-%m-%d %H:%M:%S"), 40.0, p, p, p, 50.0, 3.0))
            store.conn.executemany("INSERT INTO fronius VALUES (?,?)", rows_f)
            store.conn.executemany("INSERT INTO heating VALUES (?,?,?,?,?,?,?)", rows_h)
            store.conn.commit()
            wx_rows = [(int((start + timedelta(hours=h)).timestamp()), 100.0, 50.0, 50.0, 5.0) for h in range(100)]
            with mock.patch.object(fl, "DB_PATH", Path(tmp) / "learn.db"), \
                    mock.patch.object(fl, "fetch_weather", return_value=wx_rows) as fw, \
                    mock.patch.object(fl, "fetch_archive", return_value=[]), \
                    mock.patch.object(fl, "_updated_day", None):
                self.assertTrue(fl.update(store, fl.WeatherConfig(), CFG))
                self.assertFalse(fl.update(store, fl.WeatherConfig(), CFG))          # gleicher Tag: nichts
                self.assertEqual(fw.call_args[0][1], fl.MAX_PAST_DAYS)              # Erstbefuellung 92 Tage
                conn = fl.connect()
                pv = dict(conn.execute("SELECT hour_end, pv_kw FROM pv_hours").fetchall())
                heat = conn.execute("SELECT quiet_kwh, quiet_min FROM heat_hours WHERE quiet_min >= 30").fetchall()
                nw = conn.execute("SELECT COUNT(*) FROM weather").fetchone()[0]
                conn.close()
            self.assertEqual(nw, 100)
            self.assertIn(2.0, [round(v, 3) for v in pv.values()])                   # W -> kW
            self.assertGreater(len(heat), 40)
            kw = [q / (m / 60) for q, m in heat]
            self.assertAlmostEqual(float(np.median(kw)), 0.05 * 4 * CFG.puffer_kwh_per_k, delta=0.05)


if __name__ == "__main__":
    unittest.main()
