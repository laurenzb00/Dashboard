"""Tests fuer core.pv_forecast (Lernen aus Historie, Aehnlich-Korrektur, Cache) und Sonnenstand."""

import json
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
from core import pv_forecast as pf  # noqa: E402
from core.solar_geometry import poa, sun_position  # noqa: E402

LAT, LON = 48.2569, 13.0397


def synth_weather(days=90, start=datetime(2026, 3, 1, tzinfo=timezone.utc), seed=1):
    """Stuendliches Wetter: Klarhimmel * Tages-Bewoelkung, Temperatur mit Tagesgang."""
    rng = np.random.default_rng(seed)
    t = np.arange(int(start.timestamp()) + 3600, int(start.timestamp()) + days * 86400 + 1, 3600, dtype=float)
    el, _ = sun_position(t - 1800, LAT, LON)
    s = np.clip(np.sin(np.radians(el)), 0, None)
    cloud = np.repeat(rng.uniform(0.15, 1.0, days + 1), 24)[: len(t)]
    dni = np.where(el > 0, 900 * np.exp(-0.14 / np.maximum(s, 0.05)) * cloud, 0.0)
    dhi = np.where(el > 0, (60 + 120 * (1 - cloud)) * s ** 0.5, 0.0)
    ghi = dni * s + dhi
    hour = (t / 3600) % 24
    temp = 8 + 8 * np.sin((hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, len(t))
    return t, ghi, dni, dhi, temp, el


def true_pv(t, ghi, dni, dhi, temp, tilt=30, az=0, kwp=8.0, shade=True):
    el, saz = sun_position(t - 1800, LAT, LON)
    tot, beam = poa(ghi, dni, dhi, el, saz, tilt, az)
    f_t = 1 + pf.GAMMA_PER_K * (temp + pf.NOCT_K_PER_WM2 * tot - 25)
    p = kwp * (tot / 1000) * f_t
    if shade:   # Baum im Osten: Morgensonne tief im Osten verschattet (nur Direktanteil)
        shaded = (saz < -45) & (el < 20)
        p = p - np.where(shaded, 0.8 * kwp * beam / 1000 * f_t, 0.0)
    return np.clip(p, 0, 7.0)


class TestGeometry(unittest.TestCase):
    def test_sun_position(self):
        u = datetime(2026, 6, 21, 11, 8, tzinfo=timezone.utc).timestamp()
        el, az = sun_position([u], LAT, LON)
        self.assertAlmostEqual(float(el[0]), 90 - LAT + 23.44, delta=0.6)
        self.assertLess(abs(float(az[0])), 3)
        el, az = sun_position([datetime(2026, 3, 20, 7, tzinfo=timezone.utc).timestamp()], LAT, LON)
        self.assertLess(float(az[0]), -45)      # morgens im Osten

    def test_poa_facing_sun(self):
        tot, beam = poa([600.0], [800.0], [100.0], np.array([40.0]), np.array([0.0]), 50, 0)
        self.assertAlmostEqual(float(beam[0]), 800.0, delta=1.0)   # Sonne steht senkrecht auf der Flaeche
        self.assertGreater(float(tot[0]), 800.0)


class TestLearning(unittest.TestCase):
    def test_nnls_nonnegative(self):
        a = np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
        x = pf.nnls(a, np.array([-1.0, 2.0, 1.0]))
        self.assertTrue((x >= 0).all())

    def test_learns_orientation_shading_and_cap(self):
        t, ghi, dni, dhi, temp, _ = synth_weather()
        pv = true_pv(t, ghi, dni, dhi, temp)
        day3 = (t >= t[0] + 3 * 86400) & (t < t[0] + 4 * 86400)
        pv[day3] = 0.0                       # ganzer Tag Ausfall/Schnee -> ignoriert
        model = pf.fit_from_data({"t": t, "pv": pv, "ghi": ghi, "dni": dni, "dhi": dhi, "temp": temp}, LAT, LON)
        self.assertIsNotNone(model)
        self.assertLessEqual(abs(model["tilt"] - 30), 10)
        coef = dict(zip(model["azimuths"], model["coef"]))
        self.assertGreater(coef[0] + coef[30] + coef[-30], 0.7 * sum(coef.values()))
        self.assertLessEqual(model["cap_kw"], 7.2)
        self.assertLess(model["rmse_final_kw"], model["rmse_phys_kw"])     # Korrektur hilft
        self.assertGreater(model["r2"], 0.97)
        self.assertLess(model["day_mape_pct"], 5.0)
        # Morgens tief im Osten wurde Verschattung gelernt, mittags nicht
        tab = np.asarray(model["corr"]["table"])
        f_morning = fl.grid_lookup((model["corr"]["az"], model["corr"]["el"], model["corr"]["kt"]), tab,
                                   np.array([-80.0]), np.array([10.0]), np.array([0.37]))[0]
        f_noon = fl.grid_lookup((model["corr"]["az"], model["corr"]["el"], model["corr"]["kt"]), tab,
                                np.array([0.0]), np.array([45.0]), np.array([0.65]))[0]
        self.assertLess(f_morning, 0.8)
        self.assertAlmostEqual(f_noon, 1.0, delta=0.08)

    def test_generalizes_to_new_days(self):
        t, ghi, dni, dhi, temp, _ = synth_weather(days=60, seed=2)
        model = pf.fit_from_data({"t": t, "pv": true_pv(t, ghi, dni, dhi, temp), "ghi": ghi, "dni": dni,
                                  "dhi": dhi, "temp": temp}, LAT, LON)
        t2, ghi2, dni2, dhi2, temp2, _ = synth_weather(days=20, start=datetime(2026, 5, 5, tzinfo=timezone.utc), seed=9)
        pred = pf.predict_arrays(model, t2, ghi2, dni2, dhi2, temp2)
        truth = true_pv(t2, ghi2, dni2, dhi2, temp2)
        day_err = abs(pred.sum() - truth.sum()) / truth.sum()
        self.assertLess(day_err, 0.04)

    def test_needs_enough_hours(self):
        t, ghi, dni, dhi, temp, _ = synth_weather(days=1)
        self.assertIsNone(pf.fit_from_data({"t": t, "pv": t * 0, "ghi": ghi, "dni": dni, "dhi": dhi,
                                            "temp": temp}, LAT, LON))


class TestDay(unittest.TestCase):
    def test_forecast_for_day_and_kwh(self):
        day = date(2026, 7, 1)
        start, end = pf.day_bounds_utc(day)
        fc = {start + timedelta(hours=h + 1): 1.0 for h in range(24)}
        fc[end + timedelta(hours=1)] = 5.0  # gehoert zum naechsten Tag
        pts = pf.forecast_for_day(fc, day)
        self.assertEqual(len(pts), 24)
        self.assertAlmostEqual(pf.forecast_kwh(pts), 24.0)
        self.assertEqual(pts[0][0], datetime(2026, 7, 1, 0, 30))
        self.assertIsNone(pf.forecast_kwh([]))


class TestCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name) / "cache.json"
        self.model_path = Path(self.tmp.name) / "model.json"
        self.cfg = pf.WeatherConfig(latitude=LAT, longitude=LON)
        self.model = {"version": pf.MODEL_VERSION, "azimuths": [0], "coef": [8.0], "tilt": 30, "cap_kw": 7,
                      "latitude": LAT, "longitude": LON, "fitted_at": time.time()}
        self.model_path.write_text(json.dumps(self.model))
        self.patches = [mock.patch.object(pf, "CACHE_PATH", self.cache), mock.patch.object(pf, "MODEL_PATH", self.model_path),
                        mock.patch.object(pf, "_attempted_day", date.today()), mock.patch.object(pf, "_model_mem", None),
                        mock.patch.object(fl, "DB_PATH", Path(self.tmp.name) / "learn.db")]   # Prognose-Protokoll
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def test_fresh_cache_without_network(self):
        self.cache.write_text(json.dumps({"fetched_at": time.time(), "values": {"2026-07-01 12:00:00": 3.5}}))
        with mock.patch.object(fl, "fetch_weather") as fetch:
            out = pf.get_forecast(store=None, cfg=self.cfg)
            fetch.assert_not_called()
        self.assertEqual(out, {datetime(2026, 7, 1, 12, tzinfo=timezone.utc): 3.5})

    def test_network_error_falls_back_to_cache(self):
        self.cache.write_text(json.dumps({"fetched_at": 0, "values": {"2026-07-01 12:00:00": 2.0}}))
        with mock.patch.object(fl, "fetch_weather", side_effect=OSError("offline")):
            out = pf.get_forecast(store=None, cfg=self.cfg)
        self.assertEqual(list(out.values()), [2.0])

    def test_forecast_from_weather(self):
        # aktueller Tag, Sonne im Sueden (11:08 UTC ~ Mittag am Standort)
        noon = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
        t = noon.timestamp()
        rows = [(int(t), 800.0, 700.0, 150.0, 25.0), (int(t) + 12 * 3600, 0.0, 0.0, 0.0, 15.0)]
        with mock.patch.object(fl, "fetch_weather", return_value=rows):
            out = pf.get_forecast(store=None, cfg=self.cfg)
        vals = sorted(out.items())
        self.assertGreater(vals[0][1], 2.0)
        self.assertLessEqual(vals[0][1], 7.0)
        self.assertEqual(vals[1][1], 0.0)

    def test_relearns_once_per_day(self):
        with mock.patch.object(pf, "_attempted_day", None), \
                mock.patch.object(pf, "calibrate", return_value=None) as cal:
            pf.get_model(None, self.cfg)
            pf.get_model(None, self.cfg)
            self.assertEqual(cal.call_count, 1)


if __name__ == "__main__":
    unittest.main()
