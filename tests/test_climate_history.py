import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import climate as C  # noqa: E402
from core import climate_history as H  # noqa: E402


class TestStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.h = H.ClimateHistory(os.path.join(self.tmp.name, "c.db"))

    def tearDown(self):
        self.h.close()
        self.tmp.cleanup()

    def test_bucket_upsert_keeps_heating(self):
        t = 1_800_000_000
        self.h.add_rooms([C.Room("a", "A", current=20.0, target=21.0, heating=True)], ts=t)
        self.h.add_rooms([C.Room("a", "A", current=20.4, target=21.0, heating=False)], ts=t + 60)
        pts = self.h.query("a", t - 600)
        self.assertEqual(len(pts), 1)
        self.assertEqual(pts[0][1], 20.4)      # letzter Wert gewinnt
        self.assertTrue(pts[0][3])             # geheizt im Bucket bleibt erhalten

    def test_off_has_no_target_and_backfill_does_not_overwrite(self):
        t = 1_800_000_000
        self.h.add_rooms([C.Room("a", "A", current=19.0, target=20.0, mode="off")], ts=t)
        self.assertIsNone(self.h.query("a", t - 1)[0][2])
        added = self.h.add_points("a", [(t, 5.0, 5.0, False), (t + 300, 19.5, 20.0, True)])
        self.assertEqual(added, 1)
        self.assertEqual(self.h.query("a", t - 1)[0][1], 19.0)

    def test_needs_backfill(self):
        self.assertTrue(self.h.needs_backfill("a"))
        now = time.time()
        self.h.add_points("a", [(now - 47.5 * 3600, 20.0, 20.0, False)])
        self.assertFalse(self.h.needs_backfill("a"))


class TestParsers(unittest.TestCase):
    def test_ha_history_resample(self):
        base = 1_800_000_000
        iso = lambda t: time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(t))
        states = [
            {"state": "auto", "last_updated": iso(base), "attributes": {"current_temperature": 20.0, "temperature": 21, "hvac_action": "idle"}},
            {"state": "auto", "last_updated": iso(base + 610), "attributes": {"current_temperature": 20.5, "temperature": 21, "hvac_action": "heating"}},
            {"state": "off", "last_updated": iso(base + 1210), "attributes": {"current_temperature": 20.8}},
        ]
        pts = H.points_from_ha_history(states, base, base + 1800)
        self.assertEqual(pts[0][1:], (20.0, 21.0, False))
        self.assertTrue(any(p[3] for p in pts))
        self.assertIsNone(pts[-1][2])          # aus -> kein Ziel
        self.assertEqual(pts[-1][1], 20.8)

    def test_tado_day_report(self):
        rep = {
            "measuredData": {"insideTemperature": {"dataPoints": [
                {"timestamp": "2026-10-04T06:00:00.000Z", "value": {"celsius": 18.5}},
                {"timestamp": "2026-10-04T06:15:00.000Z", "value": {"celsius": 19.1}}]}},
            "callForHeat": {"dataIntervals": [
                {"from": "2026-10-04T06:10:00.000Z", "to": "2026-10-04T07:00:00.000Z", "value": "MEDIUM"}]},
            "settings": {"dataIntervals": [
                {"from": "2026-10-04T05:00:00.000Z", "to": "2026-10-04T08:00:00.000Z",
                 "value": {"power": "ON", "temperature": {"celsius": 21.0}}}]},
        }
        pts = H.points_from_tado_day_report(rep)
        self.assertEqual([(p[1], p[2], p[3]) for p in pts], [(18.5, 21.0, False), (19.1, 21.0, True)])


class TestChartHelpers(unittest.TestCase):
    def test_spans_and_range(self):
        try:
            from ui.components import temp_chart as T
        except ImportError as exc:      # z.B. ohne customtkinter/tkinter
            self.skipTest(str(exc))
        pts = [(0, 20, 21, True), (300, 20, 21, True), (600, 20, 21, False), (900, 20, 21, True)]
        self.assertEqual(T.heat_spans(pts), [(0, 600), (900, 1200)])
        lo, hi = T.y_range([(0, 20.0, 20.5, False)])
        self.assertGreaterEqual(hi - lo, 3.0)


if __name__ == "__main__":
    unittest.main()
