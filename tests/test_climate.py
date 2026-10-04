import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import climate as C  # noqa: E402


def ha(ent, state, **a):
    return {"entity_id": ent, "state": state, "attributes": a}


class TestHA(unittest.TestCase):
    def test_parse_and_modes(self):
        rooms = C.rooms_from_ha([
            ha("climate.wohnzimmer", "auto", friendly_name="Wohnzimmer", current_temperature=21.4,
               temperature=21, current_humidity=48, hvac_action="heating"),
            ha("climate.bad", "heat", current_temperature="22.1", temperature=22.5),
            ha("climate.keller", "off"),
            ha("climate.gast", "unavailable"),
            ha("light.x", "on"),
        ])
        self.assertEqual([r.name for r in rooms], ["Bad", "Gast", "Keller", "Wohnzimmer"])
        wz = rooms[-1]
        self.assertEqual((wz.current, wz.target, wz.humidity, wz.heating, wz.mode), (21.4, 21.0, 48.0, True, "plan"))
        self.assertEqual(rooms[0].mode, "manual")
        self.assertEqual(rooms[0].current, 22.1)
        self.assertEqual(rooms[2].mode, "off")
        self.assertIsNone(rooms[2].current)          # kein Fake-Wert 0,0
        self.assertFalse(rooms[1].available)


class TestTado(unittest.TestCase):
    def test_raw_api_state(self):
        st = {"sensorDataPoints": {"insideTemperature": {"celsius": 19.8}, "humidity": {"percentage": 52.0}},
              "setting": {"power": "ON", "temperature": {"celsius": 18}},
              "overlay": {"setting": {"power": "ON", "temperature": {"celsius": 20.5}}},
              "activityDataPoints": {"heatingPower": {"percentage": 40}}}
        r = C.room_from_tado_state(3, "Schlafzimmer", st)
        self.assertEqual((r.id, r.current, r.target, r.humidity, r.mode, r.heating, r.power_pct),
                         ("3", 19.8, 20.5, 52.0, "manual", True, 40))

    def test_missing_values_and_off(self):
        r = C.room_from_tado_state(1, "X", {"setting": {"power": "OFF"}})
        self.assertEqual(r.mode, "off")
        self.assertIsNone(r.current)
        self.assertIsNone(r.humidity)
        self.assertFalse(r.heating)

    def test_tadozone_object_style(self):
        r = C.room_from_tado_state(1, "X", {"current_temp": 20.0, "target_temp": 21.0, "current_humidity": 40,
                                            "heating_power_percentage": 0, "overlay_active": False,
                                            "power": "ON", "open_window": True})
        self.assertEqual((r.mode, r.heating, r.window_open), ("plan", False, True))


class TestPollInterval(unittest.TestCase):
    def test_interval_from_limit(self):
        self.assertEqual(C.direct_poll_s(20000), 60)
        self.assertTrue(18 * 60 <= C.direct_poll_s(100) <= 21 * 60)
        # 100er-Limit: Abfragen pro Tag bleiben deutlich darunter
        self.assertLess(86400 / C.direct_poll_s(100), 75)


class TestSummary(unittest.TestCase):
    def test_summary(self):
        rooms = [C.Room("a", "A", current=20.0, heating=True), C.Room("b", "B", current=22.0, window_open=True),
                 C.Room("c", "C")]
        s = C.summarize(rooms)
        self.assertEqual((s.total, s.heating, s.mean_current, s.windows_open), (3, 1, 21.0, 1))
        self.assertIn("3 Räume", s.text)
        self.assertIn("1 heizt", s.text)
        self.assertEqual(C.summarize([]).text, "Keine Räume")

    def test_fmt(self):
        self.assertEqual(C.fmt_temp(None), "--")
        self.assertEqual(C.fmt_temp(20.25), "20,2 °C")


if __name__ == "__main__":
    unittest.main()
