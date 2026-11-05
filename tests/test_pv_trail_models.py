"""Tests fuer den PV-Nachfuehr-Faktor (core.pv_forecast) und den Wettermodell-Vergleich (core.forecast_models)."""

import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import forecast_learning as fl  # noqa: E402
from core import forecast_models as fm  # noqa: E402
from core import pv_forecast as pf  # noqa: E402
from core.weather import WeatherConfig  # noqa: E402

DAY0 = int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())


def day_hours(day_index, kwh_per_hour_profile):
    base = DAY0 + day_index * 86400
    return [base + (6 + i) * 3600 + 3600 for i in range(len(kwh_per_hour_profile))], list(kwh_per_hour_profile)


def series(days, model_kw, measured_factor):
    t, y, m = [], [], []
    prof = [1.0, 2.0, 3.0, 2.0, 1.0]            # 9 kWh/Tag
    for d in range(days):
        tt, mm = day_hours(d, [x * model_kw for x in prof])
        t += tt
        m += mm
        y += [x * measured_factor(d) for x in mm]
    return np.array(t, float), np.array(y, float), np.array(m, float)


class TestTrailingFactor(unittest.TestCase):
    def test_factor_matches_recent_ratio(self):
        t, y, m = series(14, 1.0, lambda d: 0.8)
        ref = DAY0 + 14 * 86400 + 3600
        tf = pf.trailing_factor(t, y, m, ref=ref)
        self.assertAlmostEqual(tf["factor"], 0.8, places=3)
        self.assertEqual(tf["days"], 14)

    def test_recent_days_count_more(self):
        # Erste 10 Tage 1,0, letzte 4 Tage 0,6 -> Faktor deutlich unter dem einfachen Mittel (0,886)
        t, y, m = series(14, 1.0, lambda d: 1.0 if d < 10 else 0.6)
        tf = pf.trailing_factor(t, y, m, ref=DAY0 + 14 * 86400 + 3600)
        self.assertLess(tf["factor"], 0.85)
        self.assertGreater(tf["factor"], 0.6)

    def test_current_incomplete_day_ignored(self):
        t, y, m = series(10, 1.0, lambda d: 0.5 if d == 9 else 1.0)
        # ref liegt am Tag 9 -> dieser Tag zaehlt nicht
        tf = pf.trailing_factor(t, y, m, ref=DAY0 + 9 * 86400 + 12 * 3600)
        self.assertAlmostEqual(tf["factor"], 1.0, places=3)

    def test_needs_enough_days_and_clips(self):
        t, y, m = series(3, 1.0, lambda d: 1.0)
        self.assertIsNone(pf.trailing_factor(t, y, m, ref=DAY0 + 3 * 86400 + 3600))
        t, y, m = series(10, 1.0, lambda d: 5.0)
        tf = pf.trailing_factor(t, y, m, ref=DAY0 + 10 * 86400 + 3600)
        self.assertEqual(tf["factor"], pf.TRAIL_CLIP[1])
        t, y, m = series(10, 1.0, lambda d: 0.0)
        self.assertEqual(pf.trailing_factor(t, y, m, ref=DAY0 + 10 * 86400 + 3600)["factor"], pf.TRAIL_CLIP[0])

    def test_nan_and_dark_days_are_skipped(self):
        t, y, m = series(10, 1.0, lambda d: 0.9)
        y[3] = np.nan
        m[20:25] = 0.0                              # Tag mit (fast) null Modell-Ertrag
        tf = pf.trailing_factor(t, y, m, ref=DAY0 + 10 * 86400 + 3600)
        self.assertIsNotNone(tf)
        self.assertLess(tf["days"], 10)

    def test_multiplier_expires(self):
        model = {"trail": {"factor": 0.8, "days": 10, "asof": 1000.0}}
        self.assertEqual(pf.trail_multiplier(model, now=1000.0 + 86400), 0.8)
        self.assertEqual(pf.trail_multiplier(model, now=1000.0 + 5 * 86400), 1.0)
        self.assertEqual(pf.trail_multiplier({}, now=1.0), 1.0)
        self.assertEqual(pf.trail_multiplier(None), 1.0)


class TestParse(unittest.TestCase):
    PAYLOAD = {"hourly": {"time": ["2026-11-05T10:00", "2026-11-05T11:00"],
                          "shortwave_radiation": [100, 200], "direct_normal_irradiance": [50, 60],
                          "diffuse_radiation": [30, 40], "temperature_2m": [5.0, 6.0]}}

    def test_plain_names(self):
        rows = fl._parse_hourly(self.PAYLOAD)
        self.assertEqual(rows[1][1:], (200.0, 60.0, 40.0, 6.0))

    def test_model_suffix_names(self):
        h = {"time": self.PAYLOAD["hourly"]["time"]}
        for k, v in self.PAYLOAD["hourly"].items():
            if k != "time":
                h[f"{k}_icon_d2"] = v
        rows = fl._parse_hourly({"hourly": h}, "icon_d2")
        self.assertEqual(rows[0][1], 100.0)
        self.assertEqual(fl._parse_hourly({"hourly": h})[0][1], None)

    def test_fetch_weather_passes_model(self):
        cfg = WeatherConfig()
        resp = mock.Mock()
        resp.json.return_value = self.PAYLOAD
        with mock.patch.object(fl._session, "get", return_value=resp) as g:
            fl.fetch_weather(cfg, 0, 2, "icon_d2")
            self.assertEqual(g.call_args.kwargs["params"]["models"], "icon_d2")
            fl.fetch_weather(cfg, 0, 2, "best_match")
            self.assertNotIn("models", g.call_args.kwargs["params"])
            fl.fetch_weather(cfg, 0, 2)
            self.assertNotIn("models", g.call_args.kwargs["params"])


class TestModelComparison(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        p = mock.patch.object(fl, "DB_PATH", Path(self.tmp.name) / "fl.db")
        p.start()
        self.addCleanup(p.stop)
        # "jetzt": 12:00 UTC am 10.10.; Zieltage 5.-9.10. (Vortagsstand d1)
        self.now = datetime(2026, 10, 10, 12, 0, tzinfo=timezone.utc).timestamp()
        self.cfg = WeatherConfig(log_models=("good", "bad"))

    def _fill(self, ist_kw=2.0):
        conn = fl.connect()
        try:
            for d in range(5, 10):
                for h in range(8, 18):                      # 10 Stunden je Tag
                    th = int(datetime(2026, 10, d, h, tzinfo=timezone.utc).timestamp()) + 3600
                    conn.execute("INSERT OR REPLACE INTO pv_hours (hour_end, pv_kw, n) VALUES (?,?,60)", (th, ist_kw))
                    made = th - 20 * 3600                    # Vortag
                    for name, kw in (("best_match", ist_kw * 1.3), ("good", ist_kw * 1.02), ("bad", ist_kw * 0.6)):
                        fm.record(conn, name, [th], [kw], [300.0], made_at=made)
            conn.commit()
        finally:
            conn.close()

    def test_compare_ranks_models(self):
        self._fill()
        res = fm.compare(14, now=self.now)
        self.assertEqual(res["days"], 5)
        m = res["models"]
        self.assertLess(m["good"]["mape_pct"], m["best_match"]["mape_pct"])
        self.assertLess(m["best_match"]["mape_pct"], m["bad"]["mape_pct"])
        self.assertIn(fm.ENSEMBLE, m)
        self.assertEqual(res["best"], "good")
        self.assertGreater(m["bad"]["bias_pct"], -100)
        self.assertLess(m["bad"]["bias_pct"], 0)

    def test_status_text(self):
        self.assertEqual(fm.status_text(), "")
        self._fill()
        with mock.patch.object(fm.time, "time", return_value=self.now):
            txt = fm.status_text(14)
        self.assertIn("Wettermodelle (5 Tage)", txt)
        self.assertIn("good", txt)

    def test_incomplete_days_are_dropped_for_all(self):
        self._fill()
        conn = fl.connect()
        try:   # Modell "bad" hat am 7.10. nur 3 Stunden -> Tag faellt fuer ALLE Modelle weg
            lo = int(datetime(2026, 10, 7, 11, tzinfo=timezone.utc).timestamp())
            hi = int(datetime(2026, 10, 8, 0, tzinfo=timezone.utc).timestamp())
            conn.execute("DELETE FROM pv_model_log WHERE model='bad' AND target_hour BETWEEN ? AND ?", (lo, hi))
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(fm.compare(14, now=self.now)["days"], 4)

    def test_log_models_throttled_and_tolerant(self):
        calls = []

        def fetch(cfg, past, days, model):
            calls.append(model)
            if model == "bad":
                raise RuntimeError("HTTP 400")
            t0 = int(self.now // 3600 * 3600) + 3600
            return [(t0 + 3600 * i, 100.0, 50.0, 30.0, 5.0) for i in range(36)]

        def predict(model, t, ghi, dni, dhi, temp):
            return np.full(len(t), 1.5)

        n = fm.log_models({"x": 1}, self.cfg, fetch=fetch, predict=predict, now=self.now)
        self.assertGreater(n, 0)
        self.assertEqual(calls[0], None)                       # Standardmodell zuerst (model=None)
        self.assertIn("good", calls)
        n2 = fm.log_models({"x": 1}, self.cfg, fetch=fetch, predict=predict, now=self.now + 600)
        self.assertEqual(n2, 0)                                 # gedrosselt
        n3 = fm.log_models({"x": 1}, self.cfg, fetch=fetch, predict=predict, now=self.now + 4 * 3600)
        self.assertGreater(n3, 0)
        conn = fl.connect()
        try:
            models = {r[0] for r in conn.execute("SELECT DISTINCT model FROM pv_model_log")}
        finally:
            conn.close()
        self.assertEqual(models, {"best_match", "good"})        # "bad" fiel aus, ohne alles zu stoppen

    def test_disabled_when_no_models(self):
        cfg = WeatherConfig(log_models=())
        self.assertEqual(fm.log_models({"x": 1}, cfg, fetch=lambda *a: [], predict=lambda *a: [], now=self.now), 0)


if __name__ == "__main__":
    unittest.main()
