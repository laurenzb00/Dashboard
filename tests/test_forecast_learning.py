"""Tests fuer das Lern-Archiv (core.forecast_learning) und den lernenden Waermebedarf."""

import json
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


def synth_wx_sun(days, start, seed=0):
    """Wetter mit Direkt-/Diffusstrahlung nach echtem Sonnenstand (fuer Kollektor-Tests)."""
    from core.solar_geometry import sun_position
    rng = np.random.default_rng(seed)
    t = np.arange(start + 3600, start + days * 86400 + 1, 3600, dtype=float)
    el, _ = sun_position(t - 1800, LAT, LON)
    s = np.clip(np.sin(np.radians(el)), 0, None)
    clear = np.repeat(rng.uniform(0.0, 1.0, days + 1), 24)[: len(t)]
    dni = 850 * clear * (s > 0.05) * s ** 0.3
    dhi = (60 + 120 * (1 - clear)) * s
    ghi = dni * s + dhi
    temp = np.repeat(rng.uniform(-12, 25, days + 1), 24)[: len(t)] + 4 * np.sin(((t / 3600) % 24 - 9) / 24 * 2 * np.pi)
    return {"t": t, "temp": temp, "ghi": ghi, "dni": dni, "dhi": dhi}


class TestCollector(unittest.TestCase):
    """Solarthermie mit eigener Physik statt ueber die PV."""

    def _data(self, tilt=40.0, mult=1.7, area=12.0, days=120):
        start = int(datetime(2026, 2, 1, tzinfo=timezone.utc).timestamp())
        wx = synth_wx_sun(days, start, seed=7)
        demand = hd.DemandModel(base_kw=0.5, per_k_kw=0.1, hours=100, r2=None, t_min=-10, t_max=20, version=2,
                                tb_c=16.0, tau_h=0.0)
        n = len(wx["t"])
        hour_start = wx["t"] - 3600
        rng = np.random.default_rng(8)
        tank = np.repeat(rng.uniform(30, 80, days + 1), 24)[:n]       # Speichermittel
        low = tank - rng.uniform(3, 9, n)                              # Puffer Mitte
        truth = hd.SolarThermalModel(mean_ratio=0, hours=0, version=2, tilt=tilt, azimuth=-30.0,
                                     area_eff_m2=area, loss_mult=mult, latitude=LAT, longitude=LON)
        gain = truth.kw(wx["t"], wx["ghi"], wx["dni"], wx["dhi"], wx["temp"], low)
        t_out = wx["temp"]
        free = gain - demand.kw_eff(t_out, np.zeros(n)) + rng.normal(0, 0.3, n)
        return wx, demand, hour_start, free, tank, t_out, truth, low

    def test_fit_recovers_collector(self):
        wx, demand, hour_start, free, tank, t_out, truth, low = self._data()
        n = len(hour_start)
        with mock.patch.object(hd, "_collector_config", return_value=(-30.0, None)):
            m = hd.fit_collector(hour_start, free, np.full(n, 60.0), np.zeros(n), tank, t_out, wx, demand, LAT, LON,
                                 tank_mid_c=low)
        self.assertIsNotNone(m)
        self.assertEqual(m.version, 2)
        self.assertAlmostEqual(m.tilt, 40.0, delta=5.1)
        # Tagesertrag muss passen (Flaeche und Verluste koennen sich gegenseitig etwas ausgleichen)
        pred = m.kw(wx["t"], wx["ghi"], wx["dni"], wx["dhi"], wx["temp"], low).sum()
        real = truth.kw(wx["t"], wx["ghi"], wx["dni"], wx["dhi"], wx["temp"], low).sum()
        self.assertAlmostEqual(pred / real, 1.0, delta=0.05)
        self.assertAlmostEqual(m.mid_below_mean_k, 6.0, delta=0.5)

    def test_off_and_pool_days_are_ignored(self):
        """Sommer mit Stoerung/Pool: viele Tage fast ohne Ertrag duerfen die Flaeche nicht druecken."""
        wx, demand, hour_start, free, tank, t_out, truth, low = self._data()
        n = len(hour_start)
        days = np.array([datetime.fromtimestamp(float(h)).date().toordinal() for h in hour_start])
        rng = np.random.default_rng(3)
        off_days = set(rng.choice(np.unique(days), size=len(np.unique(days)) * 2 // 5, replace=False).tolist())
        off = np.array([d in off_days for d in days])
        gain = truth.kw(wx["t"], wx["ghi"], wx["dni"], wx["dhi"], wx["temp"], low)
        free_off = free - np.where(off, gain * 0.95, 0.0)          # Anlage aus / Waerme in den Pool
        with mock.patch.object(hd, "_collector_config", return_value=(-30.0, None)), \
                mock.patch.object(hd, "_solar_off_periods", return_value=[]):
            m = hd.fit_collector(hour_start, free_off, np.full(n, 60.0), np.zeros(n), tank, t_out, wx, demand,
                                 LAT, LON, tank_mid_c=low)
        self.assertIsNotNone(m)
        pred = m.kw(wx["t"], wx["ghi"], wx["dni"], wx["dhi"], wx["temp"], low).sum()
        self.assertAlmostEqual(pred / gain.sum(), 1.0, delta=0.08)
        self.assertGreater(m.off_days, 10)

    def test_configured_off_period_is_skipped(self):
        wx, demand, hour_start, free, tank, t_out, truth, low = self._data()
        n = len(hour_start)
        first = datetime.fromtimestamp(float(hour_start[0])).date()
        with mock.patch.object(hd, "_collector_config", return_value=(-30.0, 40.0)), \
                mock.patch.object(hd, "_solar_off_periods", return_value=[(first, first + timedelta(days=29))]):
            m = hd.fit_collector(hour_start, free, np.full(n, 60.0), np.zeros(n), tank, t_out, wx, demand,
                                 LAT, LON, tank_mid_c=low)
        self.assertGreaterEqual(m.off_days, 29)

    def test_live_factor(self):
        wx, demand, hour_start, free, tank, t_out, truth, low = self._data(days=10)
        n = len(hour_start)
        s0 = max(0, int(np.argmax(wx["ghi"])) - 14)              # sonnigster Tag
        rows = [(hour_start[i], 0, 0, free[i], 60.0, 0.0, tank[i], t_out[i], low[i]) for i in range(s0, s0 + 30)]
        # Verbrauch wie in _data (Aussentemperatur der Stunde)
        by_h = {datetime.fromtimestamp(float(hour_start[i])): float(demand.kw_eff(t_out[i:i + 1], np.zeros(1))[0])
                for i in range(n)}
        f_ok = hd.live_solar_factor(truth, rows, wx, lambda t: by_h[t])
        gain = truth.kw(wx["t"], wx["ghi"], wx["dni"], wx["dhi"], wx["temp"], low)
        rows_off = [(r[0], 0, 0, r[3] - gain[s0 + j], 60.0, 0.0, r[6], r[7], r[8]) for j, r in enumerate(rows)]
        f_off = hd.live_solar_factor(truth, rows_off, wx, lambda t: by_h[t])
        self.assertIsNotNone(f_ok)
        self.assertEqual(f_ok, 1.0)
        self.assertLess(f_off, 0.7)          # geschrumpft Richtung 1 - ein Tag ist kein sicheres Urteil

    def test_pump_needs_collector_above_sensor(self):
        """Schwache Sonne: kommt der Kollektor nicht ueber Puffer Mitte, laeuft nichts."""
        g = np.array([150.0])
        args = dict(area=12.0, eta0=0.78, a1=3.6, a2=0.012, mult=1.0, tm_offset=5.0, heat_cap_kj=8.0)
        cold = hd.collector_hourly_kwh(g, g, np.array([5.0]), np.array([5.0]), np.array([20.0]), **args)[0]
        warm = hd.collector_hourly_kwh(g, g, np.array([5.0]), np.array([5.0]), np.array([45.0]), **args)[0]
        self.assertGreater(cold, 0.0)
        self.assertEqual(warm, 0.0)
        # Start nach einer Stunde ohne Sonne: Aufheizen kostet Ertrag
        start = hd.collector_hourly_kwh(np.array([600.0]), np.array([0.0]), np.array([5.0]), np.array([5.0]),
                                        np.array([40.0]), **args)[0]
        running = hd.collector_hourly_kwh(np.array([600.0]), np.array([600.0]), np.array([5.0]), np.array([5.0]),
                                          np.array([40.0]), **args)[0]
        self.assertLess(start, running)
        self.assertAlmostEqual(running - start, 12.0 * 8.0 * (40 - 5) / 3600.0, places=6)

    def test_cold_air_and_hot_tank_reduce_yield(self):
        m = hd.SolarThermalModel(mean_ratio=0, hours=0, version=2, tilt=40.0, azimuth=-30.0, area_eff_m2=12.0,
                                 loss_mult=1.5, latitude=LAT, longitude=LON)
        t0 = float(datetime(2026, 3, 20, 12, tzinfo=timezone.utc).timestamp())
        t = np.array([t0 - 3600, t0])                  # zweite Stunde: Pumpe laeuft schon
        sun = dict(ghi=np.full(2, 600.0), dni=np.full(2, 700.0), dhi=np.full(2, 120.0))
        warm = m.kw(t, sun["ghi"], sun["dni"], sun["dhi"], np.full(2, 20.0), np.full(2, 50.0))[1]
        cold = m.kw(t, sun["ghi"], sun["dni"], sun["dhi"], np.full(2, -10.0), np.full(2, 50.0))[1]
        hot_tank = m.kw(t, sun["ghi"], sun["dni"], sun["dhi"], np.full(2, 20.0), np.full(2, 80.0))[1]
        self.assertGreater(warm, cold)          # anders als PV: Kaelte schadet dem Kollektor
        self.assertGreater(warm, hot_tank)
        self.assertGreater(cold, 0.0)

    def test_forecast_fn_matches_kw(self):
        wx, *_ = self._data(days=10)
        m = hd.SolarThermalModel(mean_ratio=0, hours=0, version=2, tilt=40.0, azimuth=-30.0, area_eff_m2=12.0,
                                 loss_mult=1.5, latitude=LAT, longitude=LON)
        f = m.forecast_fn(wx)
        i = int(np.argmax(wx["ghi"]))
        local_start = datetime.fromtimestamp(wx["t"][i] - 3600)
        sl = slice(i - 1, i + 1)          # mit Vorstunde (Pumpe schon an?)
        ref = m.kw(wx["t"][sl], wx["ghi"][sl], wx["dni"][sl], wx["dhi"][sl], wx["temp"][sl],
                   np.full(2, 55.0 - m.mid_below_mean_k))[1]
        self.assertAlmostEqual(f(local_start, 55.0), ref, places=6)
        self.assertGreater(ref, 1.0)

    def test_old_model_json_still_loads(self):
        m = hd.SolarThermalModel.from_dict({"mean_ratio": 0.6, "hours": 10, "table": []})
        self.assertEqual(m.version, 1)
        self.assertAlmostEqual(m.ratio(5.0, 50.0), 0.6)


def _raw(eg, og, dg):
    v = ["STANDBY"] + ["0"] * 40
    v[18], v[22], v[25] = eg, og, dg
    return json.dumps(v)


class TestCircuits(unittest.TestCase):
    """Heizkreispumpen EG/OG/DG: Archiv und Mehrverbrauch."""

    def test_circuits_on(self):
        self.assertEqual(fl.circuits_on(json.loads(_raw("EIN", "AUS", "EIN"))), 2)
        self.assertIsNone(fl.circuits_on(["x"] * 5))
        self.assertIsNone(fl.circuits_on(json.loads(_raw("EIN", "48.00", "EIN"))))

    def test_hour_rows_and_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FakeStore(os.path.join(tmp, "data.db"))
            store.conn.execute("CREATE TABLE heating_bmk_raw (timestamp TEXT PRIMARY KEY, data TEXT)")
            now = time.time()
            h0 = int(now) - int(now) % 3600 - 3 * 3600
            rows = []
            for m in range(0, 120):                      # 2 volle Stunden, minuetlich
                ts = datetime.fromtimestamp(h0 + m * 60, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                rows.append((ts, _raw("EIN", "EIN" if m < 60 else "AUS", "AUS")))
            store.conn.executemany("INSERT INTO heating_bmk_raw VALUES (?,?)", rows)
            store.conn.commit()
            conn = fl.connect(os.path.join(tmp, "fl.db"))
            fl._update_circuits(conn, store, now)
            got = fl.load_circuits(conn)
            self.assertEqual(got[h0], 2.0)
            self.assertEqual(got[h0 + 3600], 1.0)
            self.assertAlmostEqual(fl.recent_circuits(store, hours=4), 1.5, places=6)
            conn.close()

    def test_fit_circuit_effect(self):
        start = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp())
        wx = synth_wx(60, start, seed=9)
        m = hd.DemandModel(base_kw=1.0, per_k_kw=0.2, hours=500, r2=None, t_min=5, t_max=25, version=2,
                           tb_c=15.0, tau_h=0.0)
        rng = np.random.default_rng(2)
        b_start = np.arange(start, start + 59 * 86400, 6 * 3600, dtype=float) - 1800.0
        te, ge = hd._hour_features(b_start, wx, np.full(len(b_start), np.nan), 0.0)
        circ = rng.choice([0.0, 1.0, 2.0, 3.0], size=len(b_start))
        b_kw = m.kw_eff(te, ge) + 0.4 * (circ - circ.mean()) + rng.normal(0, 0.2, len(b_start))
        hd.fit_circuit_effect(m, b_start, b_kw, np.full(len(b_start), 6.0), np.full(len(b_start), np.nan), circ, wx)
        self.assertAlmostEqual(m.circuit_kw, 0.4, delta=0.08)
        self.assertGreater(m.circuit_adjust(3.0), 0.0)
        self.assertLess(m.circuit_adjust(0.0), 0.0)
        self.assertAlmostEqual(m.circuit_adjust(3.0, hours_ahead=24.0), m.circuit_adjust(3.0) / np.e, places=6)

    def test_no_effect_without_data(self):
        m = hd.DemandModel(base_kw=1.0, per_k_kw=0.2, hours=500, r2=None, t_min=5, t_max=25, version=2)
        hd.fit_circuit_effect(m, np.zeros(5), np.ones(5), np.ones(5), np.full(5, np.nan), np.full(5, np.nan), {})
        self.assertEqual(m.circuit_kw, 0.0)
        self.assertEqual(m.circuit_adjust(3.0), 0.0)


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
