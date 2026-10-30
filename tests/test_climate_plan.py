import os
import sys
import time
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import climate as C  # noqa: E402
from core import climate_plan as P  # noqa: E402


def local_ts(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi).timestamp()


NIGHT_PLAN = P.DayPlan([P.Segment(0, 360, 17.0), P.Segment(360, 1320, 21.0), P.Segment(1320, 1440, 17.0)])


class TestDayPlan(unittest.TestCase):
    def test_merge_and_levels(self):
        plan = P.DayPlan([P.Segment(0, 100, 21), P.Segment(100, 200, 21), P.Segment(200, 300, 17)])
        self.assertEqual(plan.segments, [P.Segment(0, 200, 21.0), P.Segment(200, 300, 17.0)])
        self.assertEqual((plan.comfort, plan.eco), (21.0, 17.0))
        self.assertEqual((plan.level(21), plan.level(17), plan.level(20.5), plan.level(None)),
                         ("comfort", "eco", "comfort", "off"))

    def test_next_change_wraps_to_tomorrow(self):
        self.assertEqual(NIGHT_PLAN.next_change(100), (360, 21.0))
        self.assertEqual(NIGHT_PLAN.next_change(400), (1320, 17.0))
        self.assertEqual(NIGHT_PLAN.next_change(1400), (1440 + 360, 21.0))   # morgen frueh
        flat = P.DayPlan([P.Segment(0, 1440, 20)])
        self.assertIsNone(flat.next_change(500))
        self.assertFalse(flat.has_change)

    def test_plan_spans_clip_and_multi_day(self):
        t0, t1 = local_ts(2026, 11, 2, 20), local_ts(2026, 11, 3, 8)
        spans = P.plan_spans(NIGHT_PLAN, t0, t1)
        self.assertEqual([(datetime.fromtimestamp(a).strftime("%d %H:%M"), datetime.fromtimestamp(b).strftime("%d %H:%M"), t)
                          for a, b, t in spans],
                         [("02 20:00", "02 22:00", 21.0), ("02 22:00", "03 06:00", 17.0),
                          ("03 06:00", "03 08:00", 21.0)])


    def test_plan_spans_merge_across_midnight(self):
        t0, t1 = local_ts(2026, 11, 2, 18), local_ts(2026, 11, 3, 12)
        spans = P.plan_spans(NIGHT_PLAN, t0, t1)
        self.assertEqual([(datetime.fromtimestamp(a).strftime("%d %H:%M"), datetime.fromtimestamp(b).strftime("%d %H:%M"), t)
                          for a, b, t in spans],
                         [("02 18:00", "02 22:00", 21.0), ("02 22:00", "03 06:00", 17.0), ("03 06:00", "03 12:00", 21.0)])


class TestHistoryPlan(unittest.TestCase):
    NOW = local_ts(2026, 11, 10, 12)                              # Dienstag

    def history(self, days, override_day=None, weekend_target=None):
        pts = []
        for dd in range(1, days + 1):
            d = datetime.fromtimestamp(self.NOW).date().toordinal() - dd
            day = datetime.fromordinal(d)
            base = day.timestamp()
            for slot in range(0, 24 * 12):                       # 5-Minuten-Raster
                hour = slot / 12
                tgt = 17.0 if (hour < 6 or hour >= 22) else 21.0
                if weekend_target is not None and day.weekday() >= 5 and 6 <= hour < 22:
                    tgt = weekend_target
                if override_day == dd and 12 <= hour < 14:
                    tgt = 25.0                                    # einmaliger manueller Ausreisser
                pts.append((int(base + slot * 300), 20.0, tgt, False))
        # heutige (unvollstaendige, manuelle) Werte duerfen den Plan nicht beeinflussen
        pts += [(int(local_ts(2026, 11, 10, 0) + i * 300), 20.0, 25.0, False) for i in range(100)]
        return pts

    def test_learns_night_setback_and_ignores_outlier(self):
        plan = P.plan_from_history(self.history(5, override_day=2), self.NOW)
        self.assertEqual(plan.source, "history")
        self.assertEqual(plan.at(3 * 60).temp, 17.0)
        self.assertEqual(plan.at(13 * 60).temp, 21.0)            # Ausreisser raus
        self.assertEqual(plan.at(23 * 60).temp, 17.0)
        self.assertEqual(plan.next_change(7 * 60), (22 * 60, 17.0))

    def test_weekend_learned_separately_when_enough_days(self):
        pts = self.history(14, weekend_target=22.5)
        monday_plan = P.plan_from_history(pts, self.NOW)          # Dienstag -> Werktage
        self.assertEqual(monday_plan.at(13 * 60).temp, 21.0)
        sat = local_ts(2026, 11, 14, 12)                          # Samstag -> Wochenende (4 Tage Verlauf)
        weekend_plan = P.plan_from_history(pts, sat, days=14)
        self.assertEqual(weekend_plan.at(13 * 60).temp, 22.5)

    def test_too_little_history(self):
        self.assertIsNone(P.plan_from_history(self.history(2), self.NOW))
        self.assertIsNone(P.plan_from_history([], self.NOW))


class TestStatus(unittest.TestCase):
    NOW = local_ts(2026, 11, 2, 23, 10)

    def room(self, **kw):
        base = dict(id="1", name="Schlafzimmer", current=19.5, target=17.0, mode="plan")
        base.update(kw)
        return C.Room(**base)

    def test_plan_night_setback_shows_next_change(self):
        st = P.describe_status(self.room(), NIGHT_PLAN, self.NOW)
        self.assertEqual((st.kind, st.level, st.on_plan), ("plan", "eco", True))
        self.assertEqual(st.title, "Zeitplan aktiv")
        self.assertIn("Jetzt Absenkung 17 °C", st.detail)
        self.assertIn("ab morgen 06:00 Komfort 21 °C", st.detail)

    def test_tado_next_change_wins_over_plan(self):
        r = self.room(next_change_ts=local_ts(2026, 11, 3, 5, 30), next_change_temp=21.5)
        st = P.describe_status(r, NIGHT_PLAN, self.NOW)
        self.assertIn("ab morgen 05:30 Komfort 21,5 °C", st.detail)

    def test_plan_without_plan_data(self):
        st = P.describe_status(self.room(target=21.0), None, self.NOW)
        self.assertEqual((st.kind, st.level), ("plan", "plan"))
        self.assertEqual(st.detail, "Ziel 21 °C")

    def test_manual_until_next_block(self):
        r = self.room(mode="manual", target=22.0, override="next_block",
                      override_end_ts=local_ts(2026, 11, 3, 6, 0))
        st = P.describe_status(r, NIGHT_PLAN, self.NOW)
        self.assertEqual((st.kind, st.on_plan), ("manual", False))
        self.assertEqual(st.title, "Manuell bis nächster Zeitblock")
        self.assertIn("22 °C bis morgen 06:00", st.detail)

    def test_timer_remaining(self):
        r = self.room(mode="manual", target=21.0, override="timer", override_end_ts=self.NOW + 42 * 60)
        st = P.describe_status(r, NIGHT_PLAN, self.NOW)
        self.assertEqual(st.kind, "timer")
        self.assertIn("noch 42 min", st.detail)
        self.assertIn("bis 23:52", st.detail)

    def test_hold_and_unknown_manual(self):
        hold = P.describe_status(self.room(mode="manual", override="hold"), NIGHT_PLAN, self.NOW)
        self.assertEqual(hold.title, "Manuell – dauerhaft")
        unknown = P.describe_status(self.room(mode="manual"), NIGHT_PLAN, self.NOW)
        self.assertEqual(unknown.title, "Manuell")
        self.assertIn("wieder Zeitplan", unknown.detail)

    def test_away_window_off_offline(self):
        away = P.describe_status(self.room(presence="away", target=16.0), NIGHT_PLAN, self.NOW)
        self.assertEqual((away.kind, away.level, away.on_plan), ("away", "away", False))
        self.assertIn("16 °C", away.detail)
        manual_away = P.describe_status(self.room(presence="away", mode="manual", override="hold"), None, self.NOW)
        self.assertEqual(manual_away.kind, "manual")            # manueller Eingriff geht vor
        self.assertEqual(P.describe_status(self.room(window_open=True), None, self.NOW).kind, "window")
        self.assertEqual(P.describe_status(self.room(mode="off"), None, self.NOW).kind, "off")
        self.assertEqual(P.describe_status(self.room(available=False), None, self.NOW).title, "Offline")

    def test_format_helpers(self):
        self.assertEqual(P.fmt_duration(42 * 60), "42 min")
        self.assertEqual(P.fmt_duration(2 * 3600 + 5 * 60), "2 h 05 min")
        self.assertEqual(P.fmt_duration(3 * 3600), "3 h")
        now = local_ts(2026, 11, 2, 12)                         # Montag
        self.assertEqual(P.fmt_clock(local_ts(2026, 11, 2, 14, 5), now), "14:05")
        self.assertEqual(P.fmt_clock(local_ts(2026, 11, 3, 6), now), "morgen 06:00")
        self.assertEqual(P.fmt_clock(local_ts(2026, 11, 7, 6), now), "Sa 06:00")


class TestTadoStateParsing(unittest.TestCase):
    def test_overlay_until_next_block_and_next_change(self):
        st = {"tadoMode": "HOME",
              "sensorDataPoints": {"insideTemperature": {"celsius": 20.0}},
              "setting": {"power": "ON", "temperature": {"celsius": 17}},
              "overlay": {"setting": {"power": "ON", "temperature": {"celsius": 22}},
                          "termination": {"type": "TADO_MODE", "typeSkillBasedApp": "NEXT_TIME_BLOCK",
                                          "expiry": "2026-11-03T05:00:00Z"}},
              "nextScheduleChange": {"start": "2026-11-03T05:00:00Z",
                                     "setting": {"type": "HEATING", "power": "ON", "temperature": {"celsius": 21}}}}
        r = C.room_from_tado_state(1, "Schlafzimmer", st)
        self.assertEqual((r.mode, r.override, r.presence, r.target), ("manual", "next_block", "home", 22.0))
        self.assertEqual(r.override_end_ts, 1793682000.0)
        self.assertEqual((r.next_change_ts, r.next_change_temp, r.next_change_off), (1793682000.0, 21.0, False))

    def test_timer_remaining_seconds(self):
        st = {"overlay": {"setting": {"power": "ON", "temperature": {"celsius": 21}},
                          "termination": {"type": "TIMER", "remainingTimeInSeconds": 1800}}}
        before = time.time()
        r = C.room_from_tado_state(1, "X", st)
        self.assertEqual(r.override, "timer")
        self.assertAlmostEqual(r.override_end_ts, before + 1800, delta=5)

    def test_manual_hold_has_no_end(self):
        st = {"overlay": {"setting": {"power": "ON", "temperature": {"celsius": 21}},
                          "termination": {"type": "MANUAL", "expiry": None}}}
        r = C.room_from_tado_state(1, "X", st)
        self.assertEqual((r.override, r.override_end_ts), ("hold", None))

    def test_away_and_off_next_change(self):
        st = {"tadoMode": "AWAY", "setting": {"power": "ON", "temperature": {"celsius": 16}},
              "nextScheduleChange": {"start": "2026-11-03T05:00:00Z", "setting": {"power": "OFF"}}}
        r = C.room_from_tado_state(1, "X", st)
        self.assertEqual((r.presence, r.mode, r.override), ("away", "plan", None))
        self.assertEqual((r.next_change_temp, r.next_change_off), (None, True))

    def test_flat_pytado_variant_and_missing(self):
        r = C.room_from_tado_state(1, "X", {"current_temp": 20.0, "target_temp": 21.0, "overlay_active": True,
                                            "overlay_termination_type": "TIMER",
                                            "overlay_termination_timestamp": "2026-11-03T05:00:00+00:00",
                                            "tado_mode": "HOME"})
        self.assertEqual((r.override, r.presence), ("timer", "home"))
        plain = C.room_from_tado_state(1, "X", {"current_temp": 20.0})
        self.assertEqual((plain.override, plain.presence, plain.next_change_ts), (None, None, None))

    def test_ha_preset(self):
        rooms = C.rooms_from_ha([{"entity_id": "climate.schlafzimmer", "state": "auto",
                                  "attributes": {"current_temperature": 20, "temperature": 16, "preset_mode": "away"}}])
        self.assertEqual(rooms[0].presence, "away")


if __name__ == "__main__":
    unittest.main()
