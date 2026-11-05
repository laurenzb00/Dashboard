"""Bedienlogik des Zeitplan-Editors und der Tab-Anbindung - ohne Display (Tk/customtkinter als Attrappen)."""

import importlib.util
import sys
import threading
import types
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from core import own_schedule as S  # noqa: E402
from core.climate import Room  # noqa: E402


class Var:
    def __init__(self, value=None, **_):
        self._v = value

    def get(self):
        return self._v

    def set(self, v):
        self._v = v

    def trace_add(self, *a, **k):
        pass


class Widget:
    """Merkt sich Texte/Befehle und Kinder - mehr braucht die Bedienlogik nicht."""

    def __init__(self, master=None, **kw):
        self.master, self.kw, self.kids, self.dead = master, dict(kw), [], False
        if isinstance(master, Widget):
            master.kids.append(self)

    def configure(self, **kw):
        self.kw.update(kw)
    config = configure

    def cget(self, k):
        return self.kw.get(k, 0)

    def winfo_children(self):
        return [k for k in self.kids if not k.dead]

    def destroy(self):
        self.dead = True
        for k in self.kids:
            k.destroy()

    def winfo_width(self):
        return 800

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        return lambda *a, **k: None


def _stub_modules():
    tk = types.ModuleType("tkinter")
    tk.StringVar, tk.BooleanVar, tk.Canvas = Var, Var, Widget
    tk.LEFT = tk.RIGHT = tk.X = tk.BOTH = "x"
    for name in ("Tk", "Frame", "Misc"):
        setattr(tk, name, Widget)
    tk.ttk = mock.MagicMock()
    ctk = types.ModuleType("customtkinter")
    for name in ("CTkFrame", "CTkLabel", "CTkButton", "CTkSwitch", "CTkScrollableFrame"):
        setattr(ctk, name, type(name, (Widget,), {}))
    styles = types.ModuleType("ui.styles")
    for n in ("COLOR_BORDER", "COLOR_CARD", "COLOR_DANGER", "COLOR_INFO", "COLOR_PRIMARY", "COLOR_ROOT",
              "COLOR_SUBTEXT", "COLOR_SUCCESS", "COLOR_TEXT", "COLOR_WARNING"):
        setattr(styles, n, "#000000")
    styles.get_safe_font = lambda *a, **k: ("x", 10)
    styles.emoji = lambda a, b="": a
    return {"tkinter": tk, "tkinter.ttk": tk.ttk, "customtkinter": ctk, "ui.styles": styles}


def _load(name, relpath, extra=None):
    mods = _stub_modules()
    mods.update(extra or {})
    with mock.patch.dict(sys.modules, mods):
        spec = importlib.util.spec_from_file_location(name, SRC / relpath)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod


def find(w, text, exact=False):
    out = []
    t = w.kw.get("text")
    if isinstance(t, str) and (t == text if exact else text in t) and w.kw.get("command"):
        out.append(w)
    for k in w.winfo_children():
        out += find(k, text, exact)
    return out


def labels(w):
    out = [w.kw["text"]] if isinstance(w.kw.get("text"), str) else []
    for k in w.winfo_children():
        out += labels(k)
    return out


class TestEditor(unittest.TestCase):
    def setUp(self):
        self.mod = _load("schedule_editor_under_test", "ui/components/schedule_editor.py")
        self.saved, self.closed = [], []
        base = S.default_schedule()
        self.ed = self.mod.ScheduleEditor(Widget(), base, on_save=self.saved.append,
                                          on_close=lambda: self.closed.append(1),
                                          on_import=lambda: S.from_day_plan(None))
        self.ed._pick_day(0)

    def press(self, text, n=0):
        btns = find(self.ed, text, exact=len(text) == 1)
        self.assertGreater(len(btns), n, f"Taste {text!r} fehlt")
        btns[n].kw["command"]()

    def day(self):
        return self.ed._s.days[self.ed._day]

    def test_time_and_temp_steppers(self):
        self.assertEqual(self.day(), [[360, 21.0], [1320, 18.0]])
        self.press("+", 0)                      # erste Zeile: Uhrzeit +15 min
        self.assertEqual(self.day()[0], [375, 21.0])
        self.press("−", 1)                      # erste Zeile: Temperatur -0,5
        self.assertEqual(self.day()[0], [375, 20.5])

    def test_add_remove(self):
        self.press("Zeitpunkt hinzufügen")
        self.assertEqual(len(self.day()), 3)
        self.press("🗑", 0)
        self.assertEqual(len(self.day()), 2)

    def test_copy_buttons(self):
        self.ed._s.days[0] = [[300, 22.0]]
        self.press("Auf Sa+So kopieren")
        self.assertEqual(self.ed._s.days[6], [[300, 22.0]])
        self.assertEqual(self.ed._s.days[2], [[360, 21.0], [1320, 18.0]])
        self.press("Auf alle Tage kopieren")
        self.assertTrue(all(d == [[300, 22.0]] for d in self.ed._s.days))

    def test_day_switch_changes_rows(self):
        self.ed._s.days[3] = [[480, 19.5]]
        self.ed._pick_day(3)
        self.assertIn("19,5 °C", labels(self.ed._rows))

    def test_save_enables_and_validates(self):
        self.press("Speichern")
        self.assertEqual(len(self.saved), 1)
        self.assertTrue(self.saved[0].enabled)
        empty = self.mod.ScheduleEditor(Widget(), S.WeekSchedule(), on_save=self.saved.append, on_close=lambda: None)
        empty._save()
        self.assertEqual(len(self.saved), 1)
        self.assertIn("Mindestens", empty._msg.get())

    def test_save_does_not_touch_original(self):
        self.press("+", 0)
        self.assertEqual(self.ed._s.days[0][0][0], 375)
        self.assertEqual(S.default_schedule().days[0][0][0], 360)

    def test_import_empty_message(self):
        self.press("Aus Tado-Verlauf übernehmen")
        self.assertIn("noch kein fester Plan", self.ed._msg.get())

    def test_import_applies_plan(self):
        from core.climate_plan import DayPlan, Segment
        plan = DayPlan([Segment(0, 300, 16.0), Segment(300, 1440, 20.0)])
        ed = self.mod.ScheduleEditor(Widget(), S.WeekSchedule(), on_save=lambda s: None, on_close=lambda: None,
                                     on_import=lambda: S.from_day_plan(plan))
        ed._import()
        self.assertEqual(ed._s.days[2], [[0, 16.0], [300, 20.0]])

    def test_away_settings(self):
        self.assertEqual(self.ed._s.away_temp, 5.0)
        self.assertIn("5 °C", labels(self.ed._away_temp_lbl.master))
        steppers = find(self.ed, "+", exact=True)
        minus = find(self.ed, "−", exact=True)
        steppers[-2].kw["command"]()                # Abwesend-Temperatur +0,5
        self.assertEqual(self.ed._s.away_temp, 5.5)
        minus[-2].kw["command"]()
        minus[-2].kw["command"]()                   # unter dem Minimum bleibt es bei 5
        self.assertEqual(self.ed._s.away_temp, 5.0)
        steppers[-1].kw["command"]()                # Aufheizen ab +2 km
        self.assertEqual(self.ed._s.approach_km, 14.0)
        for _ in range(40):
            steppers[-1].kw["command"]()
        self.assertEqual(self.ed._s.approach_km, S.APPROACH_KM_MAX)
        self.ed._away_var.set(False)
        self.ed._toggle_away()
        self.assertFalse(self.ed._s.away_enabled)

    def test_import_keeps_away_settings(self):
        from core.climate_plan import DayPlan, Segment
        self.ed._s.away_temp, self.ed._s.approach_km, self.ed._s.away_enabled = 7.0, 20.0, False
        self.ed._on_import = lambda: S.from_day_plan(DayPlan([Segment(0, 1440, 20.0)]))
        self.ed._import()
        self.assertEqual((self.ed._s.away_temp, self.ed._s.approach_km, self.ed._s.away_enabled), (7.0, 20.0, False))

    def test_temp_color_scale(self):
        self.assertEqual(self.mod.temp_color(18, 18, 18), self.mod.COLOR_COMFORT)
        self.assertEqual(self.mod.temp_color(18, 18, 21), self.mod.COLOR_ECO.upper())
        self.assertEqual(self.mod.temp_color(21, 18, 21), self.mod.COLOR_COMFORT.upper())


class _Obj:
    pass


class TestTabIntegration(unittest.TestCase):
    """_do_set_temp / _do_plan / _enforce / _status_room auf einem Tab ohne Oberflaeche."""

    @classmethod
    def setUpClass(cls):
        class Mixin:
            pass
        mm = mock.MagicMock
        extra = {
            "ui.components.plan_chart": types.SimpleNamespace(PlanChart=mm()),
            "ui.components.schedule_editor": types.SimpleNamespace(ScheduleEditor=mm()),
            "ui.components.tab_shell": types.SimpleNamespace(TabShell=mm()),
            "ui.components.temp_chart": types.SimpleNamespace(TempChart=mm()),
            "ui.components.thermostat_ring": types.SimpleNamespace(ThermostatRing=mm()),
            "ui.components.ui_dispatch": types.SimpleNamespace(UiQueuePumpMixin=Mixin),
        }
        for k in list(extra):
            m = types.ModuleType(k)
            m.__dict__.update(vars(extra[k]))
            extra[k] = m
        extra["ui.components"] = types.ModuleType("ui.components")
        extra["ui"] = types.ModuleType("ui")
        cls.mod = _load("tado_under_test", "tabs/tado.py", extra)

    def make(self, enabled=True):
        tab = self.mod.TadoTab.__new__(self.mod.TadoTab)
        tab._sched = S.default_schedule()
        tab._sched.enabled = enabled
        tab._enforcer = S.Enforcer()
        tab._rooms = []
        tab.source = "ha"
        tab._ha_client = mock.Mock()
        tab._ha_client.call_service.return_value = True
        tab._wake = threading.Event()
        tab.var_status = Var("")
        tab._post_ui = lambda fn: fn()
        tab._learned = {}
        tab._presence = self.mod.PR.PresenceTracker(away_delay_s=0)
        tab._presence_state = self.mod.PR.PresenceState()
        tab._home_coords = (48.25, 13.04)
        tab._presence_err_logged = False
        tab._own_var = Var(enabled)
        tab._sched_lbl = Widget()
        return tab

    def test_manual_set_runs_until_next_plan_block(self):
        tab = self.make()
        with mock.patch.object(self.mod, "datetime") as dt:
            dt.now.return_value = datetime(2026, 11, 5, 20, 0)
            self.assertTrue(tab._do_set_temp(["climate.sz"], 23.0))
        call = tab._ha_client.call_service.call_args
        self.assertEqual(call.args[:2], ("tado", "set_climate_timer"))
        self.assertEqual(call.args[2]["time_period"], "02:00:00")           # bis 22:00
        self.assertEqual(call.args[2]["temperature"], 23.0)

    def test_without_own_plan_nothing_changes(self):
        tab = self.make(enabled=False)
        tab._do_set_temp(["climate.sz"], 23.0)
        self.assertEqual(tab._ha_client.call_service.call_args.args[:2], ("climate", "set_temperature"))

    def test_plan_button_applies_own_target(self):
        tab = self.make()
        with mock.patch.object(self.mod, "datetime") as dt:
            dt.now.return_value = datetime(2026, 11, 5, 12, 0)
            self.assertTrue(tab._do_plan(["climate.sz"]))
        a = tab._ha_client.call_service.call_args.args
        self.assertEqual((a[1], a[2]["temperature"], a[2]["time_period"]), ("set_climate_timer", 21.0, "10:00:00"))

    def test_plan_button_without_own_plan_is_auto_mode(self):
        tab = self.make(enabled=False)
        tab._do_plan(["climate.sz"])
        self.assertEqual(tab._ha_client.call_service.call_args.args[:2], ("climate", "set_hvac_mode"))

    def test_enforce_sets_at_block_start_once(self):
        tab = self.make()
        now = datetime(2026, 11, 5, 6, 0, 10).timestamp()
        room = Room(id="climate.sz", name="SZ", target=18.0, mode="plan")
        with mock.patch.object(self.mod.time, "time", return_value=now), \
                mock.patch.object(self.mod.threading, "Timer"):
            tab._enforce([room])
            tab._enforce([Room(id="climate.sz", name="SZ", target=21.0, mode="manual", override="timer")])
        self.assertEqual(tab._ha_client.call_service.call_count, 1)
        self.assertEqual(tab._ha_client.call_service.call_args.args[2]["temperature"], 21.0)
        self.assertIn("bis 22:00", tab.var_status.get())

    def test_enforce_failure_is_retried_and_reported(self):
        tab = self.make()
        tab._ha_client.call_service.side_effect = RuntimeError("HA down")
        now = datetime(2026, 11, 5, 6, 0, 10).timestamp()
        room = Room(id="climate.sz", name="SZ", target=18.0, mode="plan")
        with mock.patch.object(self.mod.time, "time", return_value=now):
            tab._enforce([room])
        self.assertIn("konnte nicht", tab.var_status.get())
        n = tab._ha_client.call_service.call_count
        self.assertGreaterEqual(n, 1)
        with mock.patch.object(self.mod.time, "time", return_value=now + 30):
            tab._enforce([room])
        self.assertEqual(tab._ha_client.call_service.call_count, n)           # nicht im Sekundentakt
        with mock.patch.object(self.mod.time, "time", return_value=now + S.FAILED_RETRY_S + 10):
            tab._enforce([room])
        self.assertGreater(tab._ha_client.call_service.call_count, n)         # nach kurzer Pause nochmal

    def test_enforce_off_does_nothing(self):
        tab = self.make(enabled=False)
        tab._enforce([Room(id="x", name="x", target=10.0, mode="plan")])
        tab._ha_client.call_service.assert_not_called()

    def test_status_room_treats_own_timer_as_plan(self):
        tab = self.make()
        now = datetime(2026, 11, 5, 12, 0)
        r = Room(id="x", name="x", target=21.0, mode="manual", override="timer", override_end_ts=1.0)
        with mock.patch.object(self.mod, "datetime") as dt:
            dt.now.return_value = now
            out = tab._status_room(r)
            hand = tab._status_room(Room(id="x", name="x", target=23.5, mode="manual", override="timer"))
        self.assertEqual((out.mode, out.override), ("plan", None))
        self.assertEqual(out.next_change_temp, 18.0)
        self.assertEqual(datetime.fromtimestamp(out.next_change_ts), datetime(2026, 11, 5, 22, 0))
        self.assertEqual(hand.mode, "manual")                      # echter Handeingriff bleibt sichtbar

    def _person(self, state, km=None):
        a = {"friendly_name": "Laurenz"}
        if km is not None:
            a["latitude"], a["longitude"] = 48.25 + km / 111.2, 13.04
        return {"entity_id": "person.laurenz", "state": state, "attributes": a}

    def test_poll_presence_reads_person_and_zone(self):
        tab = self.make()
        tab._home_coords = None
        zone = {"state": "zoning", "attributes": {"latitude": 48.25, "longitude": 13.04}}
        tab._ha_client.get_state.side_effect = lambda ent: zone if ent == "zone.home" else self._person("not_home", 30)
        with mock.patch.object(self.mod.os, "environ", {}):
            st = tab._poll_presence(tab._sched, 1000.0)
            st = tab._poll_presence(tab._sched, 1100.0)
        asked = [c.args[0] for c in tab._ha_client.get_state.call_args_list]
        self.assertIn("person.laurenz", asked)
        self.assertEqual(tab._home_coords, (48.25, 13.04))
        self.assertEqual(st.mode, "away")
        self.assertAlmostEqual(st.distance_km, 30, delta=0.5)

    def test_poll_presence_uses_env_and_configured_persons(self):
        tab = self.make()
        tab._ha_client.get_state.return_value = self._person("home")
        with mock.patch.object(self.mod.os, "environ", {"DASHBOARD_PRESENCE_ENTITY_ID": "person.x"}):
            tab._poll_presence(tab._sched, 1.0)
        self.assertEqual(tab._ha_client.get_state.call_args_list[0].args[0], "person.x")
        tab._sched.persons = ["person.a", "person.b"]
        tab._ha_client.get_state.reset_mock()
        tab._poll_presence(tab._sched, 2.0)
        self.assertEqual([c.args[0] for c in tab._ha_client.get_state.call_args_list][:2], ["person.a", "person.b"])

    def test_poll_presence_ha_failure_keeps_state_and_never_raises(self):
        tab = self.make()
        tab._ha_client.get_state.side_effect = OSError("down")
        st = tab._poll_presence(tab._sched, 5.0)
        self.assertEqual(st.mode, "unknown")
        self.assertIsNone(self.make(enabled=True)._poll_presence(S.WeekSchedule(away_enabled=False), 1.0))

    def test_enforce_away_then_return_messages(self):
        tab = self.make()
        t0 = datetime(2026, 11, 5, 9, 0).timestamp()
        room = Room(id="climate.sz", name="SZ", target=21.0, mode="plan")
        tab._ha_client.get_state.return_value = self._person("not_home", 40)
        with mock.patch.object(self.mod.time, "time", return_value=t0), \
                mock.patch.object(self.mod.threading, "Timer"), mock.patch.object(self.mod.os, "environ", {}):
            tab._enforce([room])            # Block-Beginn zuerst (Start des Tages)
            tab._enforce([room])
        first = [c.args[2]["temperature"] for c in tab._ha_client.call_service.call_args_list]
        self.assertIn(5.0, first)
        self.assertIn("Abwesend", tab.var_status.get())
        tab._ha_client.get_state.return_value = self._person("home")
        with mock.patch.object(self.mod.time, "time", return_value=t0 + 3600), \
                mock.patch.object(self.mod.threading, "Timer"), mock.patch.object(self.mod.os, "environ", {}):
            tab._enforce([Room(id="climate.sz", name="SZ", target=5.0, mode="manual", override="timer")])
        self.assertEqual(tab._ha_client.call_service.call_args.args[2]["temperature"], 21.0)
        self.assertIn("wieder da", tab.var_status.get())

    def test_plan_for_prefers_own(self):
        tab = self.make()
        tab._learned = {"x": "gelernt"}
        self.assertEqual(tab._plan_for("x").source, "own")
        tab._sched.enabled = False
        self.assertEqual(tab._plan_for("x"), "gelernt")


if __name__ == "__main__":
    unittest.main()
