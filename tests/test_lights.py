"""Tests fuer core.lights (Licht-Tab-Logik)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import lights as L  # noqa: E402


def st(ent, state="on", **attrs):
    attrs.setdefault("friendly_name", ent.split(".")[1].replace("_", " ").title())
    return {"entity_id": ent, "state": state, "attributes": attrs}


STATES = [
    st("light.wohnzimmer_decke", brightness=128, supported_color_modes=["color_temp"], color_mode="color_temp",
       color_temp_kelvin=2700, min_color_temp_kelvin=2200, max_color_temp_kelvin=6500),
    st("light.wohnzimmer_strip", "off", supported_color_modes=["hs", "color_temp"], effect_list=["colorloop"]),
    st("light.kueche", brightness=255, supported_color_modes=["brightness"]),
    st("light.keller", supported_color_modes=["onoff"]),
    st("light.kaputt", "unavailable"),
    st("scene.hell"),
]
AREAS = {"light.wohnzimmer_decke": "Wohnzimmer", "light.wohnzimmer_strip": "Wohnzimmer", "light.kueche": "Küche"}


class TestLights(unittest.TestCase):
    def setUp(self):
        self.lights = L.collect_lights(STATES, AREAS)

    def test_parse_and_collect(self):
        self.assertEqual(len(self.lights), 4)                       # unavailable + scene raus
        d = {li.entity_id: li for li in self.lights}
        self.assertEqual(d["light.wohnzimmer_decke"].brightness_pct, 50)
        self.assertTrue(d["light.wohnzimmer_decke"].supports_ct)
        self.assertTrue(d["light.wohnzimmer_strip"].supports_color)
        self.assertFalse(d["light.keller"].supports_brightness)
        self.assertEqual(d["light.keller"].area, L.OTHER_ROOM)

    def test_rooms_and_summary(self):
        rooms = L.group_by_room(self.lights)
        self.assertEqual(list(rooms), ["Küche", "Wohnzimmer", L.OTHER_ROOM])
        s = L.summarize(rooms["Wohnzimmer"])
        self.assertEqual((s.on_count, s.total, s.brightness_pct), (1, 2, 50))
        self.assertEqual(s.text, "1 von 2 an · 50 %")
        self.assertEqual(L.summarize([li for li in self.lights if not li.on]).text, "aus")

    def test_mood_commands_per_type(self):
        film = next(m for m in L.MOODS if m.key == "film")
        cmds = dict((tuple(ids), data) for ids, data in L.mood_commands(film, self.lights))
        self.assertEqual(cmds[("light.wohnzimmer_decke", "light.wohnzimmer_strip")]["color_temp_kelvin"], 2200)
        self.assertEqual(cmds[("light.kueche",)], {"brightness_pct": 15, "transition": 1})
        self.assertEqual(cmds[("light.keller",)], {})
        night = next(m for m in L.MOODS if m.key == "nachtlicht")
        cmds = dict((tuple(ids), data) for ids, data in L.mood_commands(night, self.lights))
        self.assertIn("rgb_color", cmds[("light.wohnzimmer_strip",)])

    def test_circadian(self):
        self.assertEqual(L.circadian_kelvin(-20), 2200)
        self.assertEqual(L.circadian_kelvin(60), 5000)
        self.assertLess(L.circadian_kelvin(5), L.circadian_kelvin(30))

    def test_snapshot_and_id(self):
        snap = L.snapshot_entities(STATES, ["light.wohnzimmer_decke", "light.wohnzimmer_strip"])
        self.assertEqual(snap["light.wohnzimmer_decke"]["color_temp_kelvin"], 2700)
        self.assertEqual(snap["light.wohnzimmer_strip"], {"state": "off"})
        self.assertEqual(L.scene_config_id("Gemütlich Abend"), "dashboard_gemuetlich_abend")
        self.assertEqual(L.scene_config_id("Film", {"dashboard_film"}), "dashboard_film_2")

    def test_colors(self):
        self.assertGreater(L.kelvin_to_rgb(2000)[0], L.kelvin_to_rgb(2000)[2])     # warm = rot > blau
        self.assertLess(L.kelvin_to_rgb(9000)[0], 256)
        color = L.scene_preview_color({"light.a": {"state": "on", "rgb_color": [255, 0, 0], "brightness": 255}})
        self.assertEqual(color, "#ff0000")
        self.assertIsNone(L.scene_preview_color({"light.a": {"state": "off"}}))


class FakeClient:
    def __init__(self):
        self.calls = []

    def light_turn_on(self, ids, **data):
        self.calls.append(("on", tuple(ids), data))
        return True

    def call_service(self, domain, service, data):
        self.calls.append((domain + "." + service, data))
        return True


class TestRoutines(unittest.TestCase):
    def test_goodnight_keeps_vorraum(self):
        c = FakeClient()
        r = L.GoodNight(c, ["light.a", "light.b"], "switch.vorraum", keep_minutes=0.001)
        r.run()
        self.assertEqual(c.calls[0][0], "light.turn_off")
        self.assertEqual(c.calls[1], ("homeassistant.turn_on", {"entity_id": "switch.vorraum"}))
        self.assertEqual(c.calls[-1], ("homeassistant.turn_off", {"entity_id": "switch.vorraum"}))

    def test_wakeup_ramps_up(self):
        c = FakeClient()
        lights = L.collect_lights(STATES, AREAS)
        r = L.WakeUp(c, [li for li in lights if li.supports_ct], minutes=1)
        r._sleep = lambda s: True
        r.run()
        bris = [call[2]["brightness_pct"] for call in c.calls]
        self.assertEqual(bris[0], 1)
        self.assertEqual(bris[-1], 100)
        self.assertEqual(bris, sorted(bris))

    def test_party_uses_effect(self):
        c = FakeClient()
        lights = L.collect_lights(STATES, AREAS)
        r = L.Party(c, lights)
        r.stop()
        r.run()
        self.assertEqual(c.calls[0][2]["effect"], "colorloop")


if __name__ == "__main__":
    unittest.main()
