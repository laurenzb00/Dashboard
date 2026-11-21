"""Prueft den Zwischenspeicher fuer die Bucket-Zeitpunkte in heating_stats.

heating_stats laesst sich hier nicht importieren (es zieht core.time_utils
nach), darum wird _bucket_key per AST aus der Datei geholt. Die Umrechnung
wird als Platzhalter hereingereicht - so laesst sich nicht nur das Ergebnis
pruefen, sondern auch, wie oft sie ueberhaupt aufgerufen wird.
"""
import ast
import os
import unittest
from datetime import datetime, timedelta

_SRC = os.path.join(os.path.dirname(__file__), "..", "src", "core",
                    "heating_stats.py")


def _laden():
    with open(_SRC, encoding="utf-8") as fh:
        baum = ast.parse(fh.read())
    fn = [n for n in baum.body
          if isinstance(n, ast.FunctionDef) and n.name == "_bucket_key"]
    assert fn, "_bucket_key nicht in heating_stats.py gefunden"
    konst = [n for n in baum.body
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "BUCKET_MIN"
                     for t in n.targets)]
    assert konst, "BUCKET_MIN nicht gefunden"
    ns = {"db_ts_to_local": None}
    exec(compile(ast.Module(body=konst + fn, type_ignores=[]), _SRC, "exec"), ns)
    return ns["_bucket_key"], ns["BUCKET_MIN"]


_bucket_key, BUCKET_MIN = _laden()


class Zaehler:
    """Platzhalter-Umrechnung, die mitzaehlt, wie oft sie gerufen wurde."""

    def __init__(self, offset_stunden=1):
        self.aufrufe = 0
        self.offset = timedelta(hours=offset_stunden)

    def __call__(self, roh):
        self.aufrufe += 1
        if roh is None or roh == "":
            return None
        text = str(roh)
        try:
            return datetime.fromisoformat(text) + self.offset
        except ValueError:
            return None


def _referenz(roh, konv, bucket_min):
    """Das alte Verhalten, ohne Zwischenspeicher."""
    dt = konv(roh)
    if dt is None:
        return None
    return dt.replace(minute=dt.minute - dt.minute % bucket_min,
                      second=0, microsecond=0)


def _tagesreihe(sekunden_takt=10, stunden=24):
    start = datetime(2026, 11, 16, 0, 0, 0)
    n = int(stunden * 3600 / sekunden_takt)
    return [(start + timedelta(seconds=i * sekunden_takt)).strftime(
        "%Y-%m-%d %H:%M:%S") for i in range(n)]


class TestGleichesErgebnis(unittest.TestCase):

    def test_identisch_zur_alten_rechnung(self):
        reihe = _tagesreihe()
        a, b = Zaehler(), Zaehler()
        cache = {}
        neu = [_bucket_key(r, cache, a) for r in reihe]
        alt = [_referenz(r, b, BUCKET_MIN) for r in reihe]
        self.assertEqual(neu, alt)

    def test_buckets_sind_viertelstunden(self):
        cache = {}
        k = _bucket_key("2026-11-16 13:42:37", cache, Zaehler())
        self.assertEqual(k.minute % BUCKET_MIN, 0)
        self.assertEqual((k.second, k.microsecond), (0, 0))

    def test_sekunden_derselben_minute_sind_ein_bucket(self):
        cache, z = {}, Zaehler()
        a = _bucket_key("2026-11-16 13:42:01", cache, z)
        b = _bucket_key("2026-11-16 13:42:59", cache, z)
        self.assertEqual(a, b)

    def test_minutengrenze_eines_buckets_wird_getroffen(self):
        cache, z = {}, Zaehler()
        innen = _bucket_key("2026-11-16 13:44:59", cache, z)
        aussen = _bucket_key("2026-11-16 13:45:00", cache, z)
        self.assertNotEqual(innen, aussen)

    def test_leerer_oder_kaputter_wert(self):
        cache, z = {}, Zaehler()
        self.assertIsNone(_bucket_key(None, cache, z))
        self.assertIsNone(_bucket_key("kein datum", cache, z))

    def test_nicht_text_funktioniert_auch(self):
        cache, z = {}, Zaehler()
        self.assertIsNotNone(_bucket_key("2026-11-16 13:42:37", cache, z))


class TestSpart(unittest.TestCase):

    def test_eine_umrechnung_je_minute(self):
        reihe = _tagesreihe(sekunden_takt=10, stunden=24)   # 8640 Zeilen
        z, cache = Zaehler(), {}
        for r in reihe:
            _bucket_key(r, cache, z)
        print("\n  %d Zeilen -> %d Umrechnungen (%.0f %% gespart)"
              % (len(reihe), z.aufrufe, 100 * (1 - z.aufrufe / len(reihe))))
        self.assertEqual(z.aufrufe, 24 * 60, "genau eine je Minute")
        self.assertLess(z.aufrufe, len(reihe) / 5)

    def test_ohne_zwischenspeicher_waere_es_jede_zeile(self):
        reihe = _tagesreihe()
        z = Zaehler()
        for r in reihe:
            _referenz(r, z, BUCKET_MIN)
        self.assertEqual(z.aufrufe, len(reihe))


class TestKeineVerwechslung(unittest.TestCase):

    def test_gleiche_minute_andere_zeitzone_bleibt_getrennt(self):
        """Der Grund, warum der Schluessel alles nach den Sekunden behaelt.

        In der Datenbank koennen gemischte Formate stehen - die Fehlerzeile
        "can't compare offset-naive and offset-aware datetimes" vom Februar
        zeigt das. Zwei Zeilen derselben Minute mit verschiedenem Versatz
        duerfen nicht in denselben Topf fallen.
        """
        cache, z = {}, Zaehler(offset_stunden=0)
        a = _bucket_key("2026-11-16 13:42:37+01:00", cache, z)
        b = _bucket_key("2026-11-16 13:42:11+02:00", cache, z)
        self.assertEqual(z.aufrufe, 2, "beide muessen umgerechnet werden")
        self.assertNotEqual(a.utcoffset(), b.utcoffset())

    def test_verschiedene_tage_bleiben_getrennt(self):
        cache, z = {}, Zaehler()
        a = _bucket_key("2026-11-16 13:42:37", cache, z)
        b = _bucket_key("2026-11-17 13:42:37", cache, z)
        self.assertNotEqual(a, b)
        self.assertEqual(z.aufrufe, 2)


if __name__ == "__main__":
    unittest.main()
