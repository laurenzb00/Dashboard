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

from core import bmk_interpret as bi, heating_stats as hs  # noqa: E402


def _rows(plan, start=datetime(2026, 11, 11, 12, 0)):
    """plan: [(modus, minuten), ...] oder [(modus, minuten, ladung), ...]."""
    rows, t = [], start
    for step in plan:
        modus, minutes = step[0], step[1]
        lad = step[2] if len(step) > 2 else None
        for i in range(int(minutes)):
            row = {"ts": t, "modus": modus}
            if lad is not None:
                # linear steigende Pufferladung innerhalb des Abschnitts
                row["ladung"] = lad[0] + (lad[1] - lad[0]) * (i / max(1, minutes - 1))
            rows.append(row)
            t += timedelta(minutes=1)
    return rows


class TestLoadEnergy(unittest.TestCase):
    """kWh je Ladung aus dem Pufferzuwachs."""

    def test_kwh_follows_charge_gain(self):
        cfg = hs.StorageConfig()
        kwh_per_pct = cfg.puffer_kwh_per_k * (bi.T_LADUNG_100 - bi.T_LADUNG_0) / 100.0
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 60, (20.0, 40.0)), ("GLUTERHALT.", 5, (40.0, 39.0))])
        (minutes, kwh), = bi.load_burns(rows, cfg)
        self.assertAlmostEqual(minutes, 60, delta=2)
        self.assertAlmostEqual(kwh, 20.0 * kwh_per_pct, delta=kwh_per_pct)

    def test_peak_is_used_so_later_drain_does_not_count(self):
        cfg = hs.StorageConfig()
        a = _rows([("NACHLEGEN", 3), ("TEILLAST", 60, (20.0, 40.0)), ("GLUTERHALT.", 30, (40.0, 30.0))])
        b = _rows([("NACHLEGEN", 3), ("TEILLAST", 60, (20.0, 40.0)), ("GLUTERHALT.", 5, (40.0, 40.0))])
        self.assertAlmostEqual(bi.load_burns(a, cfg)[0][1], bi.load_burns(b, cfg)[0][1], delta=0.5)

    def test_quantiles_for_kwh(self):
        cfg = hs.StorageConfig()
        plan = []
        # alle Zuwaechse deutlich ueber der Waerme-Schwelle (sonst keine Ladung)
        for gain in (10.0, 15.0, 20.0, 25.0, 30.0, 35.0):
            plan += [("NACHLEGEN", 3), ("TEILLAST", 60, (10.0, 10.0 + gain)), ("GLUTERHALT.", 5)]
        lo, med, hi, n = bi.typical_load_kwh(_rows(plan), cfg)
        self.assertLessEqual(lo, med)
        self.assertLessEqual(med, hi)
        self.assertEqual(n, 6)

    def test_door_opened_without_adding_wood(self):
        """Tuer auf, aber kein Holz drauf: lange Brennzeit, kaum Waerme -> keine Ladung.

        Laurenz, 13.11.: "nur weil die Tuer geoeffnet wurde muss ja nicht
        zwingend nachgelegt worden sein". Nachsehen oder schueren sieht im
        Betriebsmodus genauso aus wie Nachlegen - unterscheiden kann es nur die
        Waerme danach.
        """
        cfg = hs.StorageConfig()
        fill = bi.FILL_RM * cfg.wood_kwh_per_rm * cfg.boiler_efficiency
        kwh_per_pct = cfg.puffer_kwh_per_k * (bi.T_LADUNG_100 - bi.T_LADUNG_0) / 100.0
        klein = (fill * bi.LOAD_MIN_KWH_SHARE * 0.4) / kwh_per_pct      # deutlich zu wenig
        gross = (fill * bi.LOAD_MIN_KWH_SHARE * 3.0) / kwh_per_pct      # klar genug
        nur_geschaut = _rows([("NACHLEGEN", 3), ("TEILLAST", 90, (30.0, 30.0 + klein)), ("GLUTERHALT.", 5)])
        nachgelegt = _rows([("NACHLEGEN", 3), ("TEILLAST", 90, (30.0, 30.0 + gross)), ("GLUTERHALT.", 5)])
        self.assertEqual(bi.load_burns(nur_geschaut, cfg), [])
        self.assertEqual(len(bi.load_burns(nachgelegt, cfg)), 1)

    def test_without_charge_data_time_alone_decides(self):
        """Ohne ladung-Spalte kann die Waerme nicht geprueft werden - dann zaehlt die Zeit."""
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 90), ("GLUTERHALT.", 5)])
        self.assertEqual(len(bi.load_burns(rows)), 1)

    def test_no_charge_data_gives_no_kwh(self):
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 60), ("GLUTERHALT.", 5)])
        self.assertEqual(bi.load_burns(rows)[0][1], None)
        self.assertEqual(bi.load_burn_kwh(rows), [])


class TestCurrentLoad(unittest.TestCase):
    def test_running_load_is_reported(self):
        cfg = hs.StorageConfig()
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 45, (20.0, 30.0))])
        cur = bi.current_load(rows, cfg)
        self.assertAlmostEqual(cur["min"], 45, delta=2)
        self.assertGreater(cur["kwh"], 0)
        self.assertGreater(cur["kw"], 0)

    def test_burnt_down_load_is_not_current(self):
        """Gluterhalt erreicht -> die Fuellung ist durch, auch wenn sie kurz war."""
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 12, (20.0, 22.0)), ("GLUTERHALT.", 20)])
        self.assertIsNone(bi.current_load(rows))

    def test_standby_is_not_current(self):
        rows = _rows([("NACHLEGEN", 3), ("TEILLAST", 60), ("GLUTERHALT.", 10), ("STANDBY", 60)])
        self.assertIsNone(bi.current_load(rows))

    def test_nothing_burning(self):
        self.assertIsNone(bi.current_load(_rows([("STANDBY", 60)])))
        self.assertIsNone(bi.current_load([]))

    def test_weak_load_has_low_power(self):
        """Die Ladeleistung trennt schwachen von normalem Abbrand."""
        cfg = hs.StorageConfig()
        stark = bi.current_load(_rows([("NACHLEGEN", 3), ("VOLLLAST", 60, (20.0, 32.0))]), cfg)
        schwach = bi.current_load(_rows([("NACHLEGEN", 3), ("TEILLAST", 60, (20.0, 21.0))]), cfg)
        self.assertGreater(stark["kw"], schwach["kw"] * 5)


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
        early, med, late, n = bi.typical_load_burn(rows)
        self.assertEqual(n, 8)
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
        """Die LETZTEN n Ladungen zaehlen, nicht die n laengsten."""
        old = [30] * 25          # alte, kurze Ladungen
        new = [120] * 20         # die letzten 20 sind lang
        med = bi.typical_load_burn(self._with_loads(old + new))[1]
        self.assertEqual(med, 120.0)

    def test_recent_not_largest(self):
        """Gegenprobe: zuletzt kurze Ladungen muessen den Median senken."""
        rows = self._with_loads([200] * 25 + [40] * 20)
        self.assertEqual(bi.typical_load_burn(rows)[1], 40.0)


if __name__ == "__main__":
    unittest.main()
