"""Tests fuer den eigenen Zeitplan (core.own_schedule)."""

import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import own_schedule as S  # noqa: E402
from core.climate import Room  # noqa: E402
from core.climate_plan import DayPlan, Segment  # noqa: E402
from core.presence import APPROACHING, AWAY, HOME, UNKNOWN, PresenceState  # noqa: E402

MON = datetime(2026, 11, 2)            # Montag


def at(day_off=0, h=0, m=0):
    return MON + timedelta(days=day_off, hours=h, minutes=m)


def sched(enabled=True, **kw):
    s = S.default_schedule()           # 06:00 21 °C, 22:00 18 °C an allen Tagen
    s.enabled = enabled
    for k, v in kw.items():
        setattr(s, k, v)
    return s


def room(target=18.0, mode="plan", **kw):
    return Room(id="climate.schlafzimmer", name="Schlafzimmer", target=target, mode=mode, **kw)


class TestEditing(unittest.TestCase):
    def test_normalize(self):
        d = S.normalize_day([[600, 20.3], [360, 99], [600, 19.0], [-5, 20], [1500, 20], ["x", 1]])
        self.assertEqual(d, [[360, 25.0], [600, 19.0]])

    def test_add_switch(self):
        self.assertEqual(S.add_switch([]), [[360, 21.0]])
        d = S.add_switch([[360, 21.0], [1320, 18.0]])
        self.assertEqual(len(d), 3)
        self.assertEqual([m for m, _ in d], sorted(m for m, _ in d))
        full = [[i * 60, 20.0] for i in range(S.MAX_SWITCHES_PER_DAY)]
        self.assertEqual(len(S.add_switch(full)), S.MAX_SWITCHES_PER_DAY)

    def test_shift_time_skips_taken_and_clamps(self):
        d = [[360, 21.0], [375, 19.0], [1320, 18.0]]
        moved = S.shift_time(d, 0, 15)               # 06:15 ist belegt -> springt auf 06:30
        self.assertIn(390, [m for m, _ in moved])
        self.assertEqual(len(moved), 3)
        self.assertEqual(S.shift_time([[0, 20.0]], 0, -15), [[0, 20.0]])
        self.assertEqual(S.shift_time([[1425, 20.0]], 0, 15), [[1425, 20.0]])

    def test_shift_temp_and_remove(self):
        self.assertEqual(S.shift_temp([[360, 24.5]], 0, 1.0), [[360, 25.0]])
        self.assertEqual(S.shift_temp([[360, 5.0]], 0, -0.5), [[360, 5.0]])
        self.assertEqual(S.remove_switch([[360, 21.0], [1320, 18.0]], 0), [[1320, 18.0]])

    def test_copy_day(self):
        s = S.WeekSchedule(days=[[[360, 21.0]], [], [], [], [], [], []])
        S.copy_day(s, 0, S.WORKDAYS)
        self.assertEqual(s.days[4], [[360, 21.0]])
        self.assertEqual(s.days[5], [])
        s.days[0][0][1] = 15.0                       # Kopien sind unabhaengig
        self.assertEqual(s.days[1][0][1], 21.0)


class TestTimeline(unittest.TestCase):
    def test_target_across_midnight(self):
        s = sched()
        self.assertEqual(S.target_at(s, at(0, 3)), 18.0)      # Nacht: noch 22:00-Wert vom Vortag
        self.assertEqual(S.target_at(s, at(0, 6)), 21.0)
        self.assertEqual(S.target_at(s, at(0, 21, 59)), 21.0)
        self.assertEqual(S.target_at(s, at(0, 22)), 18.0)

    def test_block_start_merges_equal_neighbours(self):
        s = sched()
        start, temp = S.active_block(s, at(1, 2))             # Di 02:00 -> Block seit Mo 22:00
        self.assertEqual((start, temp), (at(0, 22), 18.0))
        s.days[1] = [[0, 18.0], [360, 21.0], [1320, 18.0]]     # 00:00 18 °C aendert nichts
        self.assertEqual(S.active_block(s, at(1, 2))[0], at(0, 22))

    def test_next_change(self):
        s = sched()
        self.assertEqual(S.next_change(s, at(0, 14)), (at(0, 22), 18.0))
        self.assertEqual(S.next_change(s, at(0, 23)), (at(1, 6), 21.0))

    def test_weekend_differs(self):
        s = sched()
        s.days[5] = [[480, 21.0], [1380, 18.0]]
        self.assertEqual(S.target_at(s, at(5, 7)), 18.0)
        self.assertEqual(S.target_at(s, at(5, 9)), 21.0)
        self.assertEqual(S.next_change(s, at(4, 23)), (at(5, 8), 21.0))

    def test_empty_day_continues_previous(self):
        s = S.WeekSchedule(days=[[[360, 21.0]], [], [], [], [], [], []])
        self.assertEqual(S.target_at(s, at(2, 12)), 21.0)
        self.assertIsNone(S.next_change(s, at(2, 12)))          # nur ein Wert -> nie eine Aenderung

    def test_constant_schedule_has_no_next_change(self):
        s = S.WeekSchedule(days=[[[360, 20.0]] for _ in range(7)])
        self.assertIsNone(S.next_change(s, at(0, 12)))
        self.assertEqual(S.seconds_to_next_change(s, at(0, 12)), S.MAX_TIMER_S)

    def test_unusable(self):
        s = S.WeekSchedule()
        self.assertFalse(s.usable)
        self.assertIsNone(S.active_block(s, at(0, 12)))
        self.assertIsNone(S.day_plan(s, MON.date()))
        self.assertEqual(S.describe(s, at(0, 12)), "Kein Zeitplan angelegt")

    def test_timer_duration(self):
        s = sched()
        self.assertEqual(S.seconds_to_next_change(s, at(0, 20)), 2 * 3600)
        self.assertEqual(S.seconds_to_next_change(s, at(0, 21, 59)), S.MIN_TIMER_S)      # 60 s -> Mindestdauer

    def test_day_plan_segments(self):
        p = S.day_plan(sched(), MON.date())
        self.assertEqual([(x.start, x.end, x.temp) for x in p.segments],
                         [(0, 360, 18.0), (360, 1320, 21.0), (1320, 1440, 18.0)])
        self.assertTrue(p.has_change)

    def test_describe(self):
        self.assertEqual(S.describe(sched(), at(0, 14)), "Jetzt 21 °C · ab 22:00 18 °C")
        self.assertEqual(S.describe(sched(), at(0, 23)), "Jetzt 18 °C · ab morgen 06:00 21 °C")
        s = S.WeekSchedule(days=[[[360, 20.5]] for _ in range(7)])
        self.assertEqual(S.describe(s, at(0, 12)), "Jetzt 20,5 °C · bleibt so")

    def test_from_day_plan(self):
        plan = DayPlan([Segment(0, 360, 17.0), Segment(360, 1320, 21.0), Segment(1320, 1440, 17.0)])
        s = S.from_day_plan(plan)
        self.assertEqual(s.days[3], [[0, 17.0], [360, 21.0], [1320, 17.0]])
        self.assertEqual(S.target_at(s, at(0, 12)), 21.0)
        self.assertFalse(S.from_day_plan(None).usable)


class TestStorage(unittest.TestCase):
    def test_roundtrip(self):
        s = sched()
        s.days[5] = [[480, 22.5]]
        s.respect_away = False
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "sub", "zeitplan.json")
            S.save(s, p)
            back = S.load(p)
            self.assertEqual(back.days, s.days)
            self.assertTrue(back.enabled)
            self.assertFalse(back.respect_away)
            self.assertEqual(os.listdir(os.path.dirname(p)), ["zeitplan.json"])     # keine Temp-Reste

    def test_missing_and_broken_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(S.load(os.path.join(tmp, "nix.json")).enabled)
            self.assertTrue(S.load(os.path.join(tmp, "nix.json")).usable)
            bad = os.path.join(tmp, "bad.json")
            Path(bad).write_text("{kaputt", encoding="utf-8")
            self.assertTrue(S.load(bad).usable)

    def test_from_json_tolerant(self):
        s = S.from_json({"enabled": True, "days": {"Mo": [[600, 99]], "5": [[60, 20]]}})
        self.assertEqual(s.days[0], [[600, 25.0]])
        self.assertEqual(s.days[5], [[60, 20.0]])
        json.dumps(S.to_json(s))


def ts(dt):
    return dt.timestamp()


class TestEnforcer(unittest.TestCase):
    def setUp(self):
        self.s = sched()
        self.e = S.Enforcer()

    def run_step(self, room_, now):
        act = self.e.step(self.s, room_, ts(now))
        if act:
            self.e.record(act, ts(now), True)
        return act

    def test_disabled_or_unusable(self):
        self.assertIsNone(S.Enforcer().step(sched(enabled=False), room(), ts(at(0, 7))))
        self.assertIsNone(S.Enforcer().step(S.WeekSchedule(enabled=True), room(), ts(at(0, 7))))

    def test_sets_at_block_start_with_timer_to_next_change(self):
        act = self.run_step(room(target=18.0, mode="plan"), at(0, 6, 0))
        self.assertEqual((act.temp, act.reason), (21.0, "blockbeginn"))
        self.assertEqual(act.duration_s, 16 * 3600)
        # danach: Raum steht jetzt im Eingriff auf 21 -> nichts mehr
        self.assertIsNone(self.run_step(room(target=21.0, mode="manual", override="timer"), at(0, 6, 1)))

    def test_no_call_if_already_at_target(self):
        self.assertIsNone(self.run_step(room(target=21.0, mode="plan"), at(0, 6, 5)))
        self.assertIsNone(self.run_step(room(target=21.0, mode="plan"), at(0, 6, 40)))

    def test_manual_change_respected_until_next_block(self):
        self.run_step(room(target=18.0), at(0, 6, 0))
        manual = room(target=23.0, mode="manual", override="timer", override_end_ts=ts(at(0, 15)))
        self.assertIsNone(self.run_step(manual, at(0, 12)))
        act = self.run_step(room(target=21.0, mode="manual", override="hold"), at(0, 22, 0))
        self.assertEqual((act.temp, act.reason), (18.0, "blockbeginn"))      # neuer Block gewinnt

    def test_restart_keeps_running_manual_override(self):
        e = S.Enforcer()
        r = room(target=23.0, mode="manual", override="timer", override_end_ts=ts(at(0, 15)))
        self.assertIsNone(e.step(self.s, r, ts(at(0, 12))))
        self.assertIsNone(e.step(self.s, r, ts(at(0, 12, 1))))

    def test_running_timer_across_block_start_defers_then_reasserts(self):
        self.run_step(room(target=21.0, mode="plan"), at(0, 12))                      # Block seit 06:00 erledigt
        r = room(target=24.0, mode="manual", override="timer", override_end_ts=ts(at(0, 22, 30)))
        self.assertIsNone(self.run_step(r, at(0, 22, 5)))                            # Timer laeuft ueber 22:00
        act = self.run_step(room(target=21.0, mode="plan"), at(0, 22, 31))           # Timer vorbei, Tado-Plan 21
        self.assertEqual((act.temp, act.reason), (18.0, "nachgestellt"))

    def test_reassert_when_tado_plan_takes_over_but_rate_limited(self):
        self.run_step(room(target=18.0), at(0, 6, 0))
        drift = room(target=19.0, mode="plan")
        a = self.run_step(drift, at(0, 6, 20))
        self.assertEqual(a.reason, "nachgestellt")
        self.assertIsNone(self.run_step(drift, at(0, 6, 25)))                       # < 10 min
        self.assertIsNotNone(self.run_step(drift, at(0, 6, 31)))

    def test_failed_call_is_retried_soon(self):
        now = at(0, 6, 0)
        act = self.e.step(self.s, room(target=18.0), ts(now))
        self.e.record(act, ts(now), False)
        self.assertIsNone(self.e.step(self.s, room(target=18.0), ts(now + timedelta(seconds=30))))
        again = self.e.step(self.s, room(target=18.0), ts(now + timedelta(seconds=S.FAILED_RETRY_S + 5)))
        self.assertEqual(again.reason, "blockbeginn")

    def test_away_window_off_unavailable_skip_then_catch_up(self):
        self.assertIsNone(self.run_step(room(presence="away"), at(0, 6, 0)))
        self.assertIsNone(self.run_step(room(window_open=True), at(0, 6, 1)))
        self.assertIsNone(self.run_step(room(mode="off"), at(0, 6, 2)))
        self.assertIsNone(self.run_step(room(available=False), at(0, 6, 3)))
        act = self.run_step(room(presence="home"), at(0, 6, 4))                    # wieder da -> nachholen
        self.assertEqual(act.temp, 21.0)

    def test_away_can_be_overridden(self):
        self.s.respect_away = False
        self.assertIsNotNone(self.run_step(room(presence="away"), at(0, 6, 0)))

    def test_daily_cap(self):
        self.run_step(room(target=18.0), at(0, 6, 0))
        t = at(0, 7, 0)
        n = 0
        for i in range(60):
            if self.run_step(room(target=19.0), t + timedelta(minutes=11 * i)):
                n += 1
        self.assertLessEqual(n + 1, S.MAX_APPLIES_PER_DAY)
        self.assertGreater(n, 5)


def pres(mode):
    return PresenceState(mode)


class TestEnforcerAway(unittest.TestCase):
    def setUp(self):
        self.s = sched()
        self.e = S.Enforcer()

    def go(self, room_, now, mode):
        act = self.e.step(self.s, room_, ts(now), pres(mode))
        if act:
            self.e.record(act, ts(now), True)
        return act

    def test_away_lowers_to_away_temp_once(self):
        self.go(room(target=18.0), at(0, 6, 0), HOME)                      # Block 06:00 erledigt
        act = self.go(room(target=21.0, mode="manual", override="timer"), at(0, 9), AWAY)
        self.assertEqual((act.temp, act.reason, act.duration_s), (5.0, "abwesend", S.MAX_TIMER_S))
        held = room(target=5.0, mode="manual", override="timer")
        self.assertIsNone(self.go(held, at(0, 9, 1), AWAY))
        self.assertIsNone(self.go(held, at(0, 14), AWAY))

    def test_away_overrides_manual_boost_but_respects_later_manual_change(self):
        self.go(room(target=21.0), at(0, 8), HOME)
        self.assertEqual(self.go(room(target=24.0, mode="manual", override="hold"), at(0, 9), AWAY).reason, "abwesend")
        self.assertIsNone(self.go(room(target=22.0, mode="manual", override="timer"), at(0, 10), AWAY))

    def test_away_reasserts_when_tado_plan_takes_over(self):
        self.go(room(target=21.0), at(0, 8), HOME)
        self.go(room(target=21.0), at(0, 9), AWAY)
        back_on_tado = room(target=21.0, mode="plan")
        self.assertIsNone(self.go(back_on_tado, at(0, 9, 5), AWAY))             # < 10 min seit dem Setzen
        self.assertEqual(self.go(back_on_tado, at(0, 9, 20), AWAY).reason, "abwesend")

    def test_return_restores_plan_temperature_immediately(self):
        self.go(room(target=21.0), at(0, 8), HOME)
        self.go(room(target=21.0), at(0, 9), AWAY)
        act = self.go(room(target=5.0, mode="manual", override="timer"), at(0, 17), APPROACHING)
        self.assertEqual((act.temp, act.reason), (21.0, "zurueck"))
        self.assertEqual(act.duration_s, 5 * 3600)                              # bis 22:00
        # danach normaler Betrieb: kein erneuter Eingriff im selben Block
        self.assertIsNone(self.go(room(target=21.0, mode="manual", override="timer"), at(0, 17, 1), HOME))
        self.assertIsNone(self.go(room(target=23.0, mode="manual", override="timer"), at(0, 18), HOME))

    def test_return_in_new_block_uses_new_block_value(self):
        self.go(room(target=21.0), at(0, 8), HOME)
        self.go(room(target=21.0), at(0, 9), AWAY)
        act = self.go(room(target=5.0, mode="manual"), at(0, 23), HOME)
        self.assertEqual((act.temp, act.reason), (18.0, "zurueck"))

    def test_block_changes_while_away_do_nothing(self):
        self.go(room(target=21.0), at(0, 8), HOME)
        self.go(room(target=21.0), at(0, 9), AWAY)
        held = room(target=5.0, mode="manual", override="timer")
        self.assertIsNone(self.go(held, at(0, 22, 0), AWAY))
        self.assertIsNone(self.go(held, at(1, 6, 0), AWAY))

    def test_failed_away_call_is_retried(self):
        self.go(room(target=21.0), at(0, 8), HOME)
        act = self.e.step(self.s, room(target=21.0), ts(at(0, 9)), pres(AWAY))
        self.e.record(act, ts(at(0, 9)), False)
        self.assertIsNone(self.e.step(self.s, room(target=21.0), ts(at(0, 9, 1)), pres(AWAY)))
        self.assertIsNotNone(self.e.step(self.s, room(target=21.0), ts(at(0, 9, 3)), pres(AWAY)))

    def test_restart_while_already_lowered(self):
        e = S.Enforcer()
        r = room(target=5.0, mode="manual", override="timer")
        self.assertIsNone(e.step(self.s, r, ts(at(0, 12)), pres(AWAY)))
        self.assertIsNone(e.step(self.s, r, ts(at(0, 12, 1)), pres(AWAY)))
        self.assertEqual(e.step(self.s, r, ts(at(0, 12, 2)), pres(HOME)).reason, "zurueck")

    def test_unknown_presence_means_home_and_falls_back_to_tado(self):
        self.assertIsNotNone(self.go(room(target=18.0), at(0, 6), UNKNOWN))     # kein Absenken bei Unklarheit
        self.assertIsNone(self.go(room(presence="away", target=18.0), at(0, 22), UNKNOWN))   # Tado-Abwesenheit
        self.assertIsNone(self.e.step(self.s, room(presence="away"), ts(at(0, 22, 5)), None))

    def test_known_presence_ignores_tado_geofence(self):
        act = self.go(room(presence="away", target=18.0), at(0, 6), HOME)
        self.assertEqual(act.reason, "blockbeginn")

    def test_disabled_away_feature(self):
        self.s.away_enabled = False
        act = self.go(room(target=18.0), at(0, 6), AWAY)
        self.assertEqual(act.reason, "blockbeginn")                            # Plan laeuft ganz normal weiter

    def test_manual_off_is_respected(self):
        self.assertIsNone(self.go(room(mode="off"), at(0, 9), AWAY))
        self.assertIsNone(self.go(room(mode="off"), at(0, 9), HOME))

    def test_away_settings_persist_and_clamp(self):
        s = S.WeekSchedule(enabled=True, away_temp=99, approach_km=500, persons=[" person.a ", ""], days=sched().days)
        self.assertEqual((s.away_temp, s.approach_km, s.persons), (18.0, 60.0, ["person.a"]))
        s.away_enabled, s.away_temp, s.approach_km = False, 16.5, 8
        back = S.from_json(S.to_json(s))
        self.assertEqual((back.away_enabled, back.away_temp, back.approach_km, back.persons),
                         (False, 16.5, 8.0, ["person.a"]))
        self.assertEqual(s.copy().away_temp, 16.5)

    def test_old_json_without_away_section(self):
        s = S.from_json({"enabled": True, "days": {"Mo": [[360, 21]]}})
        self.assertEqual((s.away_enabled, s.away_temp, s.approach_km), (True, 5.0, 12.0))


if __name__ == "__main__":
    unittest.main()
