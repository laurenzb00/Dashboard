"""Tests fuer core.presence (Anwesenheit aus Home Assistant)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import presence as P  # noqa: E402

HOME = (48.2570, 13.0400)           # irgendwo bei Braunau
KM_LAT = 1 / 111.2                  # 1 km in Breitengrad


def person(state, km=None, acc=None, name="Laurenz"):
    a = {"friendly_name": name}
    if km is not None:
        a["latitude"], a["longitude"] = HOME[0] + km * KM_LAT, HOME[1]
    if acc is not None:
        a["gps_accuracy"] = acc
    return {"entity_id": "person.laurenz", "state": state, "attributes": a}


class TestHelpers(unittest.TestCase):
    def test_haversine(self):
        self.assertAlmostEqual(P.haversine_km(*HOME, HOME[0] + 10 * KM_LAT, HOME[1]), 10.0, delta=0.1)
        self.assertEqual(P.haversine_km(*HOME, *HOME), 0.0)

    def test_home_from_zone(self):
        self.assertEqual(P.home_from_zone({"attributes": {"latitude": 1, "longitude": "2"}}), (1.0, 2.0))
        self.assertIsNone(P.home_from_zone({"attributes": {"latitude": 1}}))
        self.assertIsNone(P.home_from_zone(None))


class TestTracker(unittest.TestCase):
    def setUp(self):
        self.t = P.PresenceTracker(away_delay_s=900)

    def upd(self, now, state, km=None, **kw):
        return self.t.update([person(state, km, **kw)], HOME, now, **kw.pop("cfg", {}))

    def test_home(self):
        s = self.upd(0, "home")
        self.assertEqual(s.mode, P.HOME)
        self.assertEqual(s.away_for_s, 0)

    def test_away_only_after_delay(self):
        self.upd(0, "home")
        self.assertEqual(self.upd(60, "not_home", 20).mode, P.HOME)           # Entprellung
        self.assertEqual(self.upd(600, "not_home", 22).mode, P.HOME)
        s = self.upd(60 + 900, "not_home", 25)
        self.assertEqual(s.mode, P.AWAY)
        self.assertAlmostEqual(s.distance_km, 25, delta=0.5)
        self.assertEqual(s.who, "Laurenz")

    def test_returning_home_resets(self):
        self.upd(0, "not_home", 30)
        self.upd(1000, "not_home", 30)
        self.assertEqual(self.t.state.mode, P.AWAY)
        self.assertEqual(self.upd(1100, "home").mode, P.HOME)
        self.assertEqual(self.upd(1160, "not_home", 5).mode, P.HOME)          # Zaehler startet neu

    def test_leaving_nearby_is_not_approaching(self):
        self.upd(0, "home")
        self.upd(30, "not_home", 0.8)
        self.upd(150, "not_home", 2.0)
        s = self.upd(270, "not_home", 3.5)
        self.assertEqual(s.mode, P.HOME)                                       # entfernt sich: weder weg noch naehert sich
        self.assertEqual(self.upd(2000, "not_home", 40).mode, P.AWAY)

    def test_approach_by_distance_trend(self):
        self.upd(0, "not_home", 60)
        self.upd(1000, "not_home", 40)
        self.assertEqual(self.t.state.mode, P.AWAY)
        self.upd(1100, "not_home", 14)                                        # noch ausserhalb 12 km
        self.assertEqual(self.t.state.mode, P.AWAY)
        self.upd(1160, "not_home", 11)
        s = self.upd(1280, "not_home", 8)                                     # 3 km in 2 min naeher, < 12 km
        self.assertEqual(s.mode, P.APPROACHING)
        self.assertAlmostEqual(s.distance_km, 8, delta=0.5)

    def test_approach_needs_history(self):
        self.upd(0, "not_home", 50)
        s = self.upd(1000, "not_home", 9)                                     # nur ein Wert nahe: noch nichts bekannt
        self.assertNotEqual(s.mode, P.APPROACHING)

    def test_approach_hysteresis_and_giving_up(self):
        for i, km in enumerate((30, 28, 20, 12, 10, 8)):
            self.upd(1000 + i * 120, "not_home", km)
        self.assertEqual(self.t.state.mode, P.APPROACHING)
        self.assertEqual(self.upd(2000, "not_home", 14).mode, P.APPROACHING)   # 14 < 12*1.3
        self.assertEqual(self.upd(2120, "not_home", 20).mode, P.AWAY)          # wieder weit weg

    def test_arriving_within_arrive_radius_even_if_stationary(self):
        self.upd(0, "not_home", 25)
        self.upd(1000, "not_home", 2.5)
        s = self.upd(1130, "not_home", 2.4)
        self.assertEqual(s.mode, P.APPROACHING)

    def test_unknown_keeps_last_state_and_never_cools(self):
        self.assertEqual(self.t.update([], HOME, 0).mode, P.UNKNOWN)
        self.upd(0, "not_home", 40)
        self.upd(1000, "not_home", 40)
        self.assertEqual(self.t.state.mode, P.AWAY)
        s = self.t.update([person("unavailable")], HOME, 1100)
        self.assertEqual(s.mode, P.AWAY)                                       # HA-Aussetzer: Zustand halten

    def test_inaccurate_gps_ignored(self):
        self.upd(0, "not_home", 40)
        s = self.upd(1000, "not_home", 1.0, acc=5000)                          # Fantasie-Position
        self.assertEqual(s.mode, P.AWAY)
        self.assertIsNone(s.distance_km)

    def test_no_home_coordinates_still_away_after_delay(self):
        t = P.PresenceTracker(away_delay_s=100)
        t.update([person("not_home", 40)], None, 0)
        self.assertEqual(t.update([person("not_home", 40)], None, 200).mode, P.AWAY)

    def test_two_persons_one_home(self):
        s = self.t.update([person("not_home", 50), person("home", name="Partner")], HOME, 5000)
        self.assertEqual(s.mode, P.HOME)

    def test_two_persons_nearest_counts(self):
        t = P.PresenceTracker(away_delay_s=0)
        t.update([person("not_home", 50), person("not_home", 20, name="Partner")], HOME, 0)
        s = t.update([person("not_home", 50), person("not_home", 20, name="Partner")], HOME, 10)
        self.assertEqual(s.who, "Partner")

    def test_zone_name_counts_as_not_home(self):
        self.upd(0, "Arbeit", 30)
        self.assertEqual(self.upd(1000, "Arbeit", 30).mode, P.AWAY)

    def test_describe(self):
        self.assertEqual(P.describe(P.PresenceState(P.HOME), 0), "🏠 Zu Hause")
        self.assertIn("5 °C", P.describe(P.PresenceState(P.AWAY, 25.0), 0, 5.0))
        self.assertIn("läuft wieder an", P.describe(P.PresenceState(P.APPROACHING, 8.0), 0))
        self.assertIn("unbekannt", P.describe(P.PresenceState(), 0))


if __name__ == "__main__":
    unittest.main()
