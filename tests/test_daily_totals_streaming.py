"""Prueft, dass get_daily_totals die Zeilen streamt statt sie zu sammeln.

Im Profil vom 21.11.2026 war das fetchall() in dieser Methode mit 3,63 s der
groesste Posten aller Worker-Threads. Bei days=None sind das saemtliche rund
750000 Zeilen der fronius-Tabelle auf einmal im Speicher - obwohl
_integrate_daily_energy ausdruecklich fuer einen Durchgang gebaut ist.
"""
import os
import sys
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.datastore import _integrate_daily_energy  # noqa: E402

_SRC = os.path.join(os.path.dirname(__file__), "..", "src", "core",
                    "datastore.py")


class ZaehlenderIterator:
    """Gibt Zeilen einzeln heraus und merkt sich den Hoechststand."""

    def __init__(self, zeilen):
        self._zeilen = zeilen
        self.gleichzeitig = 0
        self.gesamt = 0

    def __iter__(self):
        for z in self._zeilen:
            self.gesamt += 1
            self.gleichzeitig = 1       # immer nur eine Zeile unterwegs
            yield z


def _messreihe(tage=3, takt_s=600, kw=4.0):
    t = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ende = t + timedelta(days=tage)
    out = []
    while t < ende:
        out.append((t.strftime("%Y-%m-%d %H:%M:%S"), kw * 1000))
        t += timedelta(seconds=takt_s)
    return out


class TestIntegrationVertraegtEinenIterator(unittest.TestCase):

    def test_liste_und_iterator_liefern_dasselbe(self):
        zeilen = _messreihe()
        aus_liste = _integrate_daily_energy(zeilen)
        aus_iter = _integrate_daily_energy(iter(zeilen))
        self.assertEqual(aus_liste, aus_iter)
        self.assertTrue(aus_liste, "die Testreihe sollte Tage ergeben")

    def test_wird_nur_einmal_durchlaufen(self):
        zeilen = _messreihe()
        zi = ZaehlenderIterator(zeilen)
        _integrate_daily_energy(zi)
        self.assertEqual(zi.gesamt, len(zeilen),
                         "genau ein Durchgang ueber die Zeilen")

    def test_leere_eingabe(self):
        self.assertEqual(_integrate_daily_energy(iter([])), [])

    def test_luecken_werden_uebersprungen(self):
        zeilen = _messreihe(tage=1)
        zeilen.append(("2026-07-20 12:00:00", 5000.0))   # 19 Tage Luecke
        erg = _integrate_daily_energy(iter(zeilen))
        tage = {e["day"] for e in erg}
        self.assertNotIn("2026-07-20", tage,
                         "eine Luecke von 19 Tagen darf nicht integriert werden")


class TestEchteAbfrage(unittest.TestCase):
    """Gegen eine echte SQLite-Datenbank, mit Cursor statt Liste."""

    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(
            "CREATE TABLE fronius (timestamp TEXT UNIQUE, pv_power REAL)")
        self.conn.executemany("INSERT INTO fronius VALUES (?,?)", _messreihe())
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def test_cursor_direkt_wie_fetchall(self):
        sql = "SELECT timestamp, pv_power FROM fronius ORDER BY timestamp ASC"
        mit_liste = _integrate_daily_energy(self.conn.execute(sql).fetchall())
        mit_cursor = _integrate_daily_energy(self.conn.execute(sql))
        self.assertEqual(mit_liste, mit_cursor)


class TestCodeStreamtWirklich(unittest.TestCase):

    def test_kein_fetchall_mehr_in_get_daily_totals(self):
        with open(_SRC, encoding="utf-8") as fh:
            quelle = fh.read()
        start = quelle.index("def get_daily_totals")
        ende = quelle.index("def get_monthly_totals")
        # Kommentarzeilen raus - das Wort steht dort als Erklaerung.
        block = "\n".join(z for z in quelle[start:ende].splitlines()
                          if not z.lstrip().startswith("#"))
        self.assertNotIn("fetchall()", block,
                         "get_daily_totals sammelt die Zeilen wieder ein")
        self.assertIn("_integrate_daily_energy(_zeilen())", block)


if __name__ == "__main__":
    unittest.main()
