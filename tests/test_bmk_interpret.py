"""Tests fuer core.bmk_interpret (Parser, Deutung, Feuer-Bilanz), Fehlstart-Logik und BMK-Speicherung."""

import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core import bmk_interpret as bi  # noqa: E402
from core import heating_stats as hs  # noqa: E402
from core.datastore import DataStore  # noqa: E402

CFG = hs.StorageConfig()
T0 = datetime(2026, 10, 24, 8, 0)


def raw_values(modus="STANDBY", kessel=49.0, aussen=9.0, top=60.0, mid=58.0, bot=50.0, ladung=None, warm=55.0,
               saug=0.0, prim=0.0, sek=0.0, co2=18.07, pumpe=0.0, hk="EIN", vl_eg=41.0, vl_og=49.0, hours=2900,
               meldung=None):
    mean = (top + mid + bot) / 3
    ladung = bi.LADUNG_SLOPE * mean - bi.LADUNG_OFFSET if ladung is None else ladung
    v = ["0"] * 73
    v[0] = modus
    for i, x in ((1, kessel), (2, aussen), (3, ladung), (4, top), (5, mid), (6, bot), (7, pumpe), (8, saug),
                 (9, prim), (10, sek), (11, co2), (12, warm), (21, vl_eg), (24, vl_og), (54, hours), (55, 620)):
        v[i] = f"{x:.2f}"
    for i in (18, 22, 25):
        v[i] = hk
    for i in (14, 16, 29, 37, 56, 57, 66, 67, 68, 69):
        v[i] = "-20.00"
    v[52], v[53] = "2831505", "32h"
    if meldung is not None:
        v = v[:52] + [meldung, "10.24 08:00:00"] + v[52:]       # 75 Felder wie im Modus NACHLEGEN
    return v


def reading(**kw):
    ts = kw.pop("ts", T0)
    return bi.parse_bmk(raw_values(**kw), ts)


class TestParse(unittest.TestCase):
    def test_basic_fields(self):
        r = reading(modus="VOLLLAST", kessel=72.5, co2=11.2, sek=59, saug=100, prim=88)
        self.assertEqual(r.phase, "volllast")
        self.assertAlmostEqual(r.kessel, 72.5)
        self.assertAlmostEqual(r.co2, 11.2)
        self.assertEqual(r.sekundaer, 59.0)
        self.assertTrue(r.heizfreigabe)
        self.assertEqual(r.betriebsstunden, 2900)
        self.assertIsNone(r.meldung)

    def test_idle_co2_and_unconnected_are_none(self):
        r = reading(co2=18.07, vl_eg=-20.0)
        self.assertIsNone(r.co2)
        self.assertIsNone(r.vl_eg)

    def test_75_field_rows_are_shifted_back(self):
        v = raw_values(modus="NACHLEGEN", meldung="Pufferspeicher ist geladen NICHT NACHLEGEN", hours=2911)
        self.assertEqual(len(v), 75)
        r = bi.parse_bmk(v, T0)
        self.assertEqual(r.meldung, "Pufferspeicher ist geladen NICHT NACHLEGEN")
        self.assertEqual(r.betriebsstunden, 2911)

    def test_garbage(self):
        self.assertIsNone(bi.parse_bmk([], T0))
        self.assertIsNone(bi.parse_bmk(["STANDBY", "1"], T0))

    def test_ladung_formula_matches_temps(self):
        self.assertAlmostEqual(bi.ladung_from_temps(78.2, 78.2, 78.2), 100.0, delta=0.3)
        self.assertAlmostEqual(bi.ladung_from_temps(30.2, 30.2, 30.2), 0.0, delta=0.3)
        r = bi.parse_bmk(raw_values(ladung=-99.0), T0)           # unplausibler Kanalwert -> aus Temperaturen
        self.assertGreater(r.ladung, 30)

    def test_phase_names(self):
        for text, ph in (("STANDBY", "aus"), ("START", "anheizen"), ("TEILLAST", "teillast"),
                         ("VOLLLAST", "volllast"), ("GLUTERHALT.", "gluterhalt"), ("NACHLEGEN", "nachlegen"),
                         ("???", "unbekannt")):
            self.assertEqual(bi.phase_of(text), ph)


class TestDeutung(unittest.TestCase):
    def test_off_with_full_buffer(self):
        d = bi.deuten(reading(top=78, mid=77, bot=76), CFG)
        self.assertEqual(d.tone, "ok")
        self.assertIn("Puffer voll", d.headline)

    def test_off_shows_remaining_hours_from_trend(self):
        r = reading(top=62, mid=58, bot=50)
        d = bi.deuten(r, CFG, trend=-4.0)
        self.assertIsNotNone(d.reicht_h)
        self.assertAlmostEqual(d.reicht_h, (r.ladung - bi.LADUNG_LEER) / 4.0, places=3)
        self.assertIn("reicht noch", d.detail)
        self.assertIn("Füllung", d.detail)

    def test_ember_with_low_buffer_recommends_refill(self):
        d = bi.deuten(reading(modus="GLUTERHALT.", top=58, mid=55, bot=40), CFG)
        self.assertEqual(d.empfehlung, "nachlegen")
        self.assertEqual(d.tone, "warn")
        self.assertIn("Nachlegen lohnt", d.headline)

    def test_ember_with_full_buffer_says_not_needed(self):
        d = bi.deuten(reading(modus="GLUTERHALT.", top=78, mid=77, bot=76), CFG)
        self.assertEqual(d.empfehlung, "nicht_nachlegen")
        self.assertIn("nicht nötig", d.headline)

    def test_burning_flags_weak_combustion_only_after_warmup(self):
        weak = reading(modus="VOLLLAST", co2=6.0)
        self.assertEqual(bi.deuten(weak, CFG, phase_min=30).abbrand, "schwach")
        self.assertIn("schwächer", bi.deuten(weak, CFG, phase_min=30).detail)
        self.assertIsNone(bi.deuten(weak, CFG, phase_min=3).abbrand)           # CO2 baut sich erst auf
        self.assertEqual(bi.deuten(reading(modus="VOLLLAST", co2=10.8), CFG, phase_min=30).abbrand, "gewohnt")
        self.assertEqual(bi.deuten(weak, CFG, phase_min=30).empfehlung, "nicht_oeffnen")

    def test_nachlegen_messages(self):
        locked = bi.parse_bmk(raw_values(modus="NACHLEGEN", meldung="Pufferspeicher ist geladen NICHT NACHLEGEN"), T0)
        d = bi.deuten(locked, CFG)
        self.assertEqual(d.empfehlung, "nicht_nachlegen")
        self.assertIn("Nachlegen", d.headline)
        burning = bi.parse_bmk(raw_values(modus="NACHLEGEN", meldung="Verbrennung aktiv TÜRE NICHT ÖFFNEN!"), T0)
        d = bi.deuten(burning, CFG)
        self.assertEqual(d.empfehlung, "nicht_oeffnen")
        self.assertIn("Tür", d.headline)

    def test_capacity_in_fillings(self):
        r = reading(top=40, mid=38, bot=32)                        # ~ Ladung 12 %
        free, fills = bi.free_capacity(r, CFG)
        fill_kwh = bi.FILL_RM * CFG.wood_kwh_per_rm * CFG.boiler_efficiency
        self.assertAlmostEqual(fills, free / fill_kwh, places=6)
        self.assertGreater(fills, 1.2)

    def test_warm_water_hint(self):
        d = bi.deuten(reading(warm=35.0, top=70, mid=65, bot=55), CFG)
        self.assertTrue(any("Warmwasser" in h for h in d.hinweise))

    def test_unknown_mode_falls_back(self):
        d = bi.deuten(reading(modus="SERVICE"), CFG)
        self.assertEqual(d.headline, "Betriebsmodus: SERVICE")


class TestLiveInterpreter(unittest.TestCase):
    def test_trend_and_phase_minutes(self):
        live = bi.LiveInterpreter(CFG)
        d = None
        for i in range(45):
            r = reading(ts=T0 + timedelta(minutes=i), modus="TEILLAST", top=50 + i * 0.2, mid=48 + i * 0.2, bot=40 + i * 0.2)
            d = live.interpret(r)
        self.assertGreater(d.trend_pct_h, 3)
        self.assertAlmostEqual(d.phase_min, 44.0, delta=1.0)
        self.assertIn("%/h", d.headline)

    def test_no_trend_without_history(self):
        live = bi.LiveInterpreter(CFG)
        d = live.interpret(reading())
        self.assertIsNone(d.trend_pct_h)

    def test_phase_restarts_after_gap(self):
        live = bi.LiveInterpreter(CFG)
        live.interpret(reading(ts=T0, modus="TEILLAST"))
        live.interpret(reading(ts=T0 + timedelta(minutes=60), modus="TEILLAST"))
        self.assertAlmostEqual(live.phase_minutes(T0 + timedelta(minutes=60)), 0.0, delta=0.1)

    def test_out_of_order_ignored(self):
        live = bi.LiveInterpreter(CFG)
        live.add(reading(ts=T0 + timedelta(minutes=5)))
        live.add(reading(ts=T0))
        self.assertEqual(len(live._hist), 1)


def minute_rows(segments, ladung_start=30.0, step=0.1):
    """segments: [(Modus, Minuten)] -> Zeilen im Format bmk_status; Pufferladung steigt waehrend Teil-/Volllast."""
    rows, t, lad = [], T0, ladung_start
    for modus, n in segments:
        for _ in range(n):
            if modus in ("TEILLAST", "VOLLLAST"):
                lad += step
            rows.append({"ts": t, "modus": modus, "ladung": lad, "kessel": 70.0 if modus != "STANDBY" else 45.0,
                         "co2": 10.5 if modus == "VOLLLAST" else None, "sekundaer": 55.0 if modus == "VOLLLAST" else 0.0})
            t += timedelta(minutes=1)
    return rows


class TestFireSummaries(unittest.TestCase):
    def test_good_fire(self):
        rows = minute_rows([("STANDBY", 30), ("START", 20), ("TEILLAST", 120), ("VOLLLAST", 200),
                            ("GLUTERHALT.", 60), ("STANDBY", 30)], step=0.15)
        fires = bi.fire_summaries(rows, CFG)
        self.assertEqual([f.kind for f in fires], ["feuer"])
        f = fires[0]
        self.assertAlmostEqual(f.minutes["volllast"], 200, delta=1)
        self.assertGreater(f.zuwachs_pct, 40)
        self.assertEqual(f.urteil, "gutes Feuer")
        self.assertAlmostEqual(f.co2_voll, 10.5)
        self.assertGreater(f.fuellung, 0.5)
        self.assertIn("gutes Feuer", bi.fire_line(f))

    def test_ember_heavy_fire_with_low_buffer(self):
        rows = minute_rows([("START", 20), ("TEILLAST", 40), ("VOLLLAST", 30), ("GLUTERHALT.", 300), ("STANDBY", 10)],
                           ladung_start=36.0, step=0.18)
        f = bi.fire_summaries(rows, CFG)[0]
        self.assertEqual(f.kind, "feuer")
        self.assertIn("viel Glut", f.urteil)
        self.assertIn("zu klein", f.urteil)

    def test_false_start_and_loading_window_are_not_fires(self):
        rows = minute_rows([("STANDBY", 20), ("START", 20), ("STANDBY", 120), ("NACHLEGEN", 90), ("STANDBY", 20)])
        fires = bi.fire_summaries(rows, CFG)
        self.assertEqual([f.kind for f in fires], ["fehlstart", "nachlege_fenster"])
        self.assertEqual(bi.false_start_count(fires), 1)
        self.assertEqual(bi.fire_lines(fires), [])

    def test_gap_splits_runs(self):
        rows = minute_rows([("TEILLAST", 30)])
        later = minute_rows([("TEILLAST", 30)])
        for r in later:
            r["ts"] += timedelta(hours=3)
        self.assertEqual(len(bi.fire_summaries(rows + later, CFG)), 2)

    def test_status_row_roundtrip(self):
        r = reading(modus="VOLLLAST", co2=10.0)
        row = bi.status_row(r)
        back = bi.reading_from_row(T0, row)
        self.assertEqual(back.modus, "VOLLLAST")
        self.assertAlmostEqual(back.co2, 10.0)
        self.assertEqual(back.heizfreigabe, True)


class TestFalseStartBuckets(unittest.TestCase):
    @staticmethod
    def bucket(i, modus):
        return hs.Bucket(ts=T0 + timedelta(minutes=15 * i), kessel=60.0, top=60.0, mid=55.0, bot=45.0, warm=55.0,
                         outdoor=8.0, modus=modus)

    def test_start_only_run_is_relabelled(self):
        modes = ["STANDBY", "STANDBY", "START", "START", "STANDBY", "STANDBY"]
        bs = [self.bucket(i, m) for i, m in enumerate(modes)]
        self.assertEqual(hs.mark_false_starts(bs), 2)
        self.assertEqual([b.modus for b in bs][2:4], ["FEHLSTART", "FEHLSTART"])
        self.assertFalse(any(hs.kessel_active(b) for b in bs))

    def test_real_fire_is_kept(self):
        modes = ["STANDBY", "START", "TEILLAST", "VOLLLAST", "GLUTERHALT.", "STANDBY"]
        bs = [self.bucket(i, m) for i, m in enumerate(modes)]
        self.assertEqual(hs.mark_false_starts(bs), 0)
        self.assertTrue(all(hs.kessel_active(b) for b in bs[1:5]))

    def test_run_at_window_edge_is_kept(self):
        bs = [self.bucket(0, "STANDBY"), self.bucket(1, "START")]       # Fenster endet mitten im Anheizen
        self.assertEqual(hs.mark_false_starts(bs), 0)
        bs = [self.bucket(0, "START"), self.bucket(1, "STANDBY")]
        self.assertEqual(hs.mark_false_starts(bs), 0)

    def test_loading_window_only(self):
        modes = ["STANDBY", "NACHLEGEN", "NACHLEGEN", "STANDBY"]
        bs = [self.bucket(i, m) for i, m in enumerate(modes)]
        hs.mark_false_starts(bs)
        self.assertEqual(bs[1].modus, "NACHLEGEFENSTER")
        self.assertFalse(hs.kessel_active(bs[1]))

    def test_strongest_mode_wins_in_bucket(self):
        self.assertEqual(hs._pick_mode(["START", "START", "TEILLAST", "VOLLLAST", "GLUTERHALT."]), "VOLLLAST")
        self.assertEqual(hs._pick_mode(["START", "NACHLEGEN"]), "NACHLEGEN")
        self.assertEqual(hs._pick_mode(["STANDBY", "STANDBY"]), "STANDBY")
        self.assertIsNone(hs._pick_mode([]))


class TestStatusStorage(unittest.TestCase):
    def test_insert_and_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(Path(tmp) / "t.db")
            ts = datetime.now().astimezone().isoformat()
            store.insert_bmk_status(ts, bi.status_row(reading(modus="TEILLAST", co2=5.0)))
            store.insert_bmk_status(ts, bi.status_row(reading(modus="VOLLLAST", co2=11.0)))   # gleicher Schluessel
            rows = store.get_bmk_status(days=1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["modus"], "VOLLLAST")
            self.assertAlmostEqual(rows[0]["co2"], 11.0)
            self.assertIsInstance(rows[0]["ts"], datetime)
            fires = bi.recent_fires(store, days=1, cfg=CFG)
            self.assertIsInstance(fires, list)

    def test_missing_table_gives_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(Path(tmp) / "t2.db")
            self.assertEqual(store.get_bmk_status(days=3), [])


try:
    import pytz  # noqa: F401
    HAVE_PYTZ = True
except ImportError:
    HAVE_PYTZ = False


@unittest.skipUnless(HAVE_PYTZ, "pytz nicht installiert")
class TestCollectorIntegration(unittest.TestCase):
    def test_bmkdaten_adds_deutung_and_persists(self):
        from core import BMKDATEN
        with tempfile.TemporaryDirectory() as tmp:
            store = DataStore(Path(tmp) / "t3.db")
            values = raw_values(modus="GLUTERHALT.", top=58, mid=55, bot=40)
            resp = mock.Mock()
            resp.text = "\n".join(values)
            resp.raise_for_status = lambda: None
            with mock.patch.object(BMKDATEN, "_resilient_get", return_value=resp), \
                    mock.patch.object(BMKDATEN, "get_shared_datastore", return_value=store), \
                    mock.patch.object(BMKDATEN, "_last_status", 0.0), mock.patch.object(BMKDATEN, "_last_raw", 0.0), \
                    mock.patch.object(BMKDATEN, "_live_preloaded", False):
                result = BMKDATEN.abrufen_und_speichern()
            self.assertEqual(result["Deutung"]["phase"], "gluterhalt")
            self.assertEqual(result["Deutung"]["empfehlung"], "nachlegen")
            self.assertEqual(result["CO2_Abgas_Prozent"], 18.07)
            self.assertEqual(result["Betriebsstunden"], 2900.0)
            self.assertEqual(len(store.get_bmk_status(days=1)), 1)

    def test_bmkdaten_75_field_row_gets_message_and_right_counters(self):
        from core import BMKDATEN
        values = raw_values(modus="NACHLEGEN", meldung="Verbrennung aktiv TÜRE NICHT ÖFFNEN!", hours=2912)
        daten = BMKDATEN._extrahiere_alle_daten(values, "2026-10-24T08:00:00+02:00")
        self.assertEqual(daten["Meldung"], "Verbrennung aktiv TÜRE NICHT ÖFFNEN!")
        self.assertEqual(daten["Betriebsstunden"], 2912.0)


if __name__ == "__main__":
    unittest.main()
