"""Tests fuer core.pv_forecast (Fit, Prognose je Tag, Cache ohne Netzwerk)."""

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

from core import pv_forecast as pf  # noqa: E402


class TestFit(unittest.TestCase):
    def test_nnls_nonnegative(self):
        rng = np.random.default_rng(0)
        a = rng.random((50, 5))
        b = a @ np.array([1.0, 0.0, 0.5, 0.0, 2.0])
        x = pf.nnls(a, b)
        self.assertTrue((x >= 0).all())
        np.testing.assert_allclose(x, [1.0, 0.0, 0.5, 0.0, 2.0], atol=1e-8)

    def test_fit_east_west(self):
        t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        times = [t0 + timedelta(hours=i) for i in range(24 * 10)]
        bases = np.array([[max(0.0, 700 * np.sin((t.hour + k) / 24 * np.pi)) for k in (-3, -1, 0, 1, 3)] for t in times])
        pv = {t: float(bases[i] @ np.array([0.004, 0, 0, 0, 0.003])) for i, t in enumerate(times)}
        model = pf.fit_model(times, bases, pv)
        self.assertIsNotNone(model)
        self.assertGreater(model["r2"], 0.999)

    def test_fit_needs_enough_hours(self):
        t0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        times = [t0 + timedelta(hours=i) for i in range(10)]
        self.assertIsNone(pf.fit_model(times, np.full((10, 5), 100.0), {t: 1.0 for t in times}))

    def test_predict_caps(self):
        model = {"azimuths": [0], "coef": [0.01], "cap_kw": 5.0}
        t = [datetime(2026, 6, 1, 12, tzinfo=timezone.utc)]
        self.assertEqual(pf.predict(model, t, np.array([[1000.0]]), [0])[t[0]], 5.0)


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
    def test_fresh_cache_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            cache.write_text(json.dumps({"fetched_at": time.time(), "values": {"2026-07-01 12:00:00": 3.5}}))
            with mock.patch.object(pf, "CACHE_PATH", cache), mock.patch.object(pf, "_fetch_gti") as fetch:
                out = pf.get_forecast(store=None)
                fetch.assert_not_called()
            self.assertEqual(out, {datetime(2026, 7, 1, 12, tzinfo=timezone.utc): 3.5})

    def test_network_error_falls_back_to_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache.json"
            model = Path(tmp) / "model.json"
            cache.write_text(json.dumps({"fetched_at": 0, "values": {"2026-07-01 12:00:00": 2.0}}))
            model.write_text(json.dumps({"azimuths": [0], "coef": [0.01], "tilt": 30, "cap_kw": 9,
                                         "calibrated_at": datetime.now(timezone.utc).isoformat(),
                                         "latitude": 48.2569, "longitude": 13.0397}))
            cfg = pf.WeatherConfig(latitude=48.2569, longitude=13.0397)
            with mock.patch.object(pf, "CACHE_PATH", cache), mock.patch.object(pf, "MODEL_PATH", model), \
                    mock.patch.object(pf, "_fetch_gti", side_effect=OSError("offline")):
                out = pf.get_forecast(store=None, cfg=cfg)
            self.assertEqual(list(out.values()), [2.0])


if __name__ == "__main__":
    unittest.main()
