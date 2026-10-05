"""Tests: Prognose vs. Ist (core.forecast_log) und Warnmeldungen (core.alerts)."""

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

from core import alerts  # noqa: E402
from core import forecast_learning as fl  # noqa: E402
from core import forecast_log as flog  # noqa: E402
from core import heat_demand as hd  # noqa: E402
from core import heating_stats as hs  # noqa: E402
from core.solar_geometry import sun_position  # noqa: E402

LAT, LON = 48.2569, 13.0397


class TmpDB(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.p = mock.patch.object(fl, "DB_PATH", Path(self.tmp.name) / "learn.db")
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()


class TestForecastLog(TmpDB):
    def test_lead_class(self):
        made = datetime(2026, 10, 5, 20, 0).timestamp()
        self.assertEqual(flog.lead_class(made, datetime(2026, 10, 6, 12, 0).timestamp()), "d1")
        self.assertEqual(flog.lead_class(made, datetime(2026, 10, 5, 22, 0).timestamp()), "d0")
        self.assertIsNone(flog.lead_class(made, datetime(2026, 10, 5, 20, 30).timestamp()))   # laeuft schon
        self.assertIsNone(flog.lead_class(made, datetime(2026, 10, 7, 12, 0).timestamp()))    # uebermorgen

    def _simulate(self, days, fog_factor):
        """Vortagsprognose ist bei 'sonnig vorhergesagt' im Winterhalbjahr systematisch zu hoch (Hochnebel)."""
        conn = fl.connect()
        flog._ensure(conn)
        start = datetime.combine(date.today() - timedelta(days=days), datetime.min.time())
        rng = np.random.default_rng(0)
        for d in range(days):
            day0 = start + timedelta(days=d)
            made = (day0 - timedelta(hours=4)).timestamp()            # Vorabend 20 Uhr
            t = np.array([(day0 + timedelta(hours=h)).timestamp() for h in range(8, 17)])
            el, _ = sun_position(t - 1800, LAT, LON)
            ghi = 700 * np.sin(np.radians(np.maximum(el, 0)))       # klar vorhergesagt
            fc = 6.0 * np.sin(np.radians(np.maximum(el, 0))) + 0.5
            flog.record_pv(t, fc, ghi, made_at=made, conn=conn, kw_raw=fc)
            ist = fc * fog_factor * rng.uniform(0.9, 1.1, len(t))
            conn.executemany("INSERT OR REPLACE INTO pv_hours (hour_end, pv_kw, n) VALUES (?,?,?)",
                             [(int(a), float(b), 60) for a, b in zip(t, ist)])
        conn.commit()
        return conn

    def test_skill_and_bias(self):
        conn = self._simulate(20, 0.6)
        sk = flog.pv_skill(days=30, conn=conn)
        self.assertGreaterEqual(sk["days"], 15)
        self.assertGreater(sk["bias_pct"], 50)              # Prognose ~1/0,6 = 67 % zu hoch
        bias = flog.learn_pv_bias(LAT, LON, conn=conn)
        self.assertIsNotNone(bias)
        self.assertLess(bias["rmse_after"], bias["rmse_before"] * 0.5)
        t = np.array([datetime.combine(date.today(), datetime.min.time()).timestamp() + 12 * 3600])
        el, _ = sun_position(t - 1800, LAT, LON)
        out = flog.apply_bias(bias, t, np.array([5.0]), 700 * np.sin(np.radians(el)), LAT, LON)
        self.assertAlmostEqual(float(out[0]), 5.0 * 0.6, delta=0.6)
        conn.close()

    def test_no_bias_with_few_days(self):
        conn = self._simulate(5, 0.6)
        self.assertIsNone(flog.learn_pv_bias(LAT, LON, conn=conn))
        conn.close()


class FakeStore:
    def __init__(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(
            "CREATE TABLE fronius (timestamp TEXT, pv_power REAL, soc REAL);"
            "CREATE TABLE heating (timestamp TEXT, kesseltemp REAL, puffer_top REAL, puffer_mid REAL, "
            "puffer_bot REAL, warmwasser REAL, aussentemp REAL);")

    def pv(self, end_utc, hours, kw, soc=50.0):
        rows = []
        t = end_utc - timedelta(hours=hours)
        while t < end_utc:
            rows.append((t.strftime("%Y-%m-%d %H:%M:%S"), kw, soc))
            t += timedelta(minutes=5)
        self.conn.executemany("INSERT INTO fronius VALUES (?,?,?)", rows)


class TestPVAlert(TmpDB):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 10, 6, 13, 10, tzinfo=timezone.utc)
        self.last = self.now.replace(minute=0)
        self.fc = {self.last - timedelta(hours=1): 5.0, self.last: 5.5}

    def test_alert_when_far_below(self):
        st = FakeStore()
        st.pv(self.last, 2, 0.8)
        res = alerts.check_pv(st, self.fc, now=self.now)
        self.assertIsNotNone(res)
        self.assertIn("1.6 kWh", res[1].replace(",", "."))

    def test_inverter_off(self):
        st = FakeStore()
        st.pv(self.last, 2, 0.0)
        self.assertIn("Wechselrichter", alerts.check_pv(st, self.fc, now=self.now)[1])

    def test_no_alert_normal_or_dim_or_full_battery(self):
        st = FakeStore()
        st.pv(self.last, 2, 4.0)
        self.assertIsNone(alerts.check_pv(st, self.fc, now=self.now))
        self.assertIsNone(alerts.check_pv(st, {k: 1.0 for k in self.fc}, now=self.now))     # kaum Sonne erwartet
        st2 = FakeStore()
        st2.pv(self.last, 2, 0.5, soc=100.0)
        self.assertIsNone(alerts.check_pv(st2, self.fc, now=self.now))                      # Abregelung

    def test_cooldown(self):
        sent = []
        sender = lambda t, m: sent.append(t) or True
        self.assertTrue(alerts.raise_alert("pv", "A", "x", sender=sender, now=1000.0))
        self.assertFalse(alerts.raise_alert("pv", "A", "x", sender=sender, now=1000.0 + 3600))
        self.assertTrue(alerts.raise_alert("waerme", "B", "y", sender=sender, now=1000.0 + 3600))
        self.assertTrue(alerts.raise_alert("pv", "A", "x", sender=sender, now=1000.0 + 13 * 3600))
        self.assertEqual(sent, ["A", "B", "A"])
        self.assertEqual(len(alerts.recent_alerts(hours=1e6)), 3)


class TestHeatAlert(unittest.TestCase):
    def _store(self, kw_loss):
        st = FakeStore()
        cfg = hs.StorageConfig()
        end = datetime.now().replace(minute=0, second=0, microsecond=0)
        t = end - timedelta(hours=14)
        p = 70.0
        rows = []
        while t < end:
            p -= kw_loss * 0.25 / (cfg.puffer_kwh_per_k)
            rows.append((t.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), 40.0, p, p, p, 50.0, 5.0))
            t += timedelta(minutes=15)
        st.conn.executemany("INSERT INTO heating VALUES (?,?,?,?,?,?,?)", rows)
        return st, cfg

    def test_high_consumption(self):
        model = hd.DemandModel(base_kw=0.5, per_k_kw=0.1, hours=500, r2=None, t_min=-5, t_max=20)   # ohne Wetterdaten: 0,5 kW
        st, cfg = self._store(5.0)
        res = alerts.check_heat(st, model, None, cfg)
        self.assertIsNotNone(res)
        st, cfg = self._store(2.0)
        self.assertIsNone(alerts.check_heat(st, model, None, cfg))


if __name__ == "__main__":
    unittest.main()
