"""Tests zu bmk_interpret.load_burn_minutes() / typical_load_burn().

Hintergrund: Ein `Fire` ist eine ganze Brennsitzung und umfasst oft mehrere
Fuellungen (09.11.2026: 988 min mit sechs Tueroeffnungen). Fuer "wann ist die
Fuellung abgebrannt" zaehlt der Abschnitt zwischen zwei NACHLEGEN-Bloecken.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import bmk_interpret as bi  # noqa: E402


def _rows(plan, start=datetime(2026, 11, 11, 12, 0)):
    """plan: [(modus, minuten), ...] -> Minutenzeilen wie get_bmk_status()."""
    rows, t = [], start
    for modus, minutes in plan:
        for _ in range(int(minutes)):
            rows.append({"ts": t, "modus": modus})
            t += timedelta(minutes=1)
    return rows


class TestLoadBurnMinutes(unittest.TestCase):
    def test_splits_one_session_into_loads(self):
        # Eine durchgehende Sitzung mit drei Tueroeffnungen = drei Ladungen
        rows = _rows([("STANDBY", 30),
                      ("NACHLEGEN", 3), ("TEILLAST", 60), ("VOLLLAST", 30),      # 90
                      ("NACHLEGEN", 3), ("TEILLAST", 45),                        # 45
                      ("NACHLEGEN", 3), ("VOLLLAST", 70), ("GLUTERHALT.", 40)])  # 70
        self.assertEqual([round(b) for b in bi.load_burn_minutes(rows)], [90, 45, 70])

    def test_fire_brenn_min_would_lump_them_together(self):
        """Gegenprobe: ueber die ganze Sitzung waeren es 205 min statt 3x ~68."""
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 60), ("VOLLLAST", 30),
                      ("NACHLEGEN", 3), ("TEILLAST", 45),
                      ("NACHLEGEN", 3), ("VOLLLAST", 70), ("GLUTERHALT.", 40)])
        self.assertEqual(sum(bi.load_burn_minutes(rows)), 205)
        self.assertEqual(len(bi.load_burn_minutes(rows)), 3)

    def test_extinguished_load_is_dropped(self):
        # 12 min Brennzeit = Fuellung praktisch wieder ausgegangen -> zaehlt nicht
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 12), ("GLUTERHALT.", 30),
                      ("NACHLEGEN", 3), ("TEILLAST", 80), ("GLUTERHALT.", 20)])
        self.assertEqual([round(b) for b in bi.load_burn_minutes(rows)], [80])

    def test_door_opened_without_fire_is_dropped(self):
        rows = _rows([("NACHLEGEN", 3), ("START", 20), ("STANDBY", 30),
                      ("NACHLEGEN", 3), ("TEILLAST", 60), ("GLUTERHALT.", 10)])
        self.assertEqual([round(b) for b in bi.load_burn_minutes(rows)], [60])

    def test_running_load_is_not_counted(self):
        """Die letzte, noch laufende Ladung fehlt - sonst waere der Median zu klein."""
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 90), ("GLUTERHALT.", 10),
                      ("NACHLEGEN", 3), ("TEILLAST", 25)])
        self.assertEqual([round(b) for b in bi.load_burn_minutes(rows)], [90])

    def test_data_gap_discards_that_load(self):
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 40)])
        later = rows[-1]["ts"] + timedelta(hours=3)
        rows += [{"ts": later + timedelta(minutes=i), "modus": "TEILLAST"} for i in range(40)]
        rows += [{"ts": rows[-1]["ts"] + timedelta(minutes=1), "modus": "NACHLEGEN"}]
        self.assertEqual(bi.load_burn_minutes(rows), [])

    def test_empty(self):
        self.assertEqual(bi.load_burn_minutes([]), [])


class TestTypicalLoadBurn(unittest.TestCase):
    def _with_loads(self, minutes_list):
        plan = []
        for m in minutes_list:
            plan += [("NACHLEGEN", 3), ("TEILLAST", m), ("GLUTERHALT.", 5)]
        return _rows(plan)

    def test_quantiles_ordered(self):
        rows = self._with_loads([40, 60, 70, 80, 90, 100, 120, 240])
        early, med, late = bi.typical_load_burn(rows)
        self.assertLessEqual(early, med)
        self.assertLessEqual(med, late)
        self.assertEqual(med, 85.0)                 # Mittel aus 80 und 90

    def test_outlier_does_not_move_median_much(self):
        base = [60, 70, 80, 90, 100]
        a = bi.typical_load_burn(self._with_loads(base))[1]
        b = bi.typical_load_burn(self._with_loads(base + [600]))[1]
        self.assertLessEqual(abs(a - b), 15.0)

    def test_too_few_loads_gives_none(self):
        self.assertIsNone(bi.typical_load_burn(self._with_loads([60, 70, 80])))
        self.assertIsNone(bi.typical_load_burn_min(self._with_loads([60, 70, 80])))
        self.assertIsNone(bi.typical_load_burn([]))

    def test_min_helper_matches_median(self):
        rows = self._with_loads([40, 60, 70, 80, 90])
        self.assertEqual(bi.typical_load_burn_min(rows), bi.typical_load_burn(rows)[1])

    def test_only_last_n_loads_count(self):
        old = [30] * 25          # alte, kurze Ladungen
        new = [120] * 20         # die letzten 20 sind lang
        med = bi.typical_load_burn(self._with_loads(old + new))[1]
        self.assertEqual(med, 120.0)


if __name__ == "__main__":
    unittest.main()
