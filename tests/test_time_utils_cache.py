"""Prueft den Versatz-Zwischenspeicher in core/time_utils.

db_ts_to_local() stand im Profil vom 21.11.2026 in jedem Thread. Gemessen:
1,55 us je Aufruf, davon 1,45 us allein astimezone(). Der Versatz wird jetzt
je UTC-Stunde gemerkt - erlaubt, weil die EU-Umstellung um 01:00 UTC passiert,
also auf einer vollen Stundengrenze.

Diese Tests vergleichen gegen die alte Rechnung, besonders rund um beide
Umstellungen.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core import time_utils as tu  # noqa: E402


def alt_db_ts_to_local(value):
    """Die Fassung vor dem Zwischenspeicher."""
    dt = tu.parse_db_ts(value)
    if dt is None:
        return None
    return dt.astimezone().replace(tzinfo=None)


def _reihe(start, ende, schritt):
    t, out = start, []
    while t < ende:
        out.append(t.strftime("%Y-%m-%d %H:%M:%S"))
        t += schritt
    return out


class TestGleichesErgebnis(unittest.TestCase):

    def setUp(self):
        tu._LOCAL_OFFSET_CACHE.clear()

    def test_ganzes_jahr(self):
        proben = _reihe(datetime(2026, 1, 1), datetime(2027, 1, 1),
                        timedelta(minutes=37))
        for p in proben:
            self.assertEqual(tu.db_ts_to_local(p), alt_db_ts_to_local(p), p)

    def test_umstellung_maerz_minutengenau(self):
        proben = _reihe(datetime(2026, 3, 29), datetime(2026, 3, 30),
                        timedelta(minutes=1))
        for p in proben:
            self.assertEqual(tu.db_ts_to_local(p), alt_db_ts_to_local(p), p)

    def test_umstellung_oktober_minutengenau(self):
        proben = _reihe(datetime(2026, 10, 25), datetime(2026, 10, 26),
                        timedelta(minutes=1))
        for p in proben:
            self.assertEqual(tu.db_ts_to_local(p), alt_db_ts_to_local(p), p)

    def test_zwei_stunden_zwei_eintraege(self):
        tu.db_ts_to_local("2026-07-01 10:00:00")
        tu.db_ts_to_local("2026-07-01 10:59:59")
        self.assertEqual(len(tu._LOCAL_OFFSET_CACHE), 1,
                         "dieselbe UTC-Stunde darf nur einmal rechnen")
        tu.db_ts_to_local("2026-07-01 11:00:00")
        self.assertEqual(len(tu._LOCAL_OFFSET_CACHE), 2)


class TestSonderfaelle(unittest.TestCase):

    def setUp(self):
        tu._LOCAL_OFFSET_CACHE.clear()

    def test_none_und_leer(self):
        self.assertIsNone(tu.db_ts_to_local(None))
        self.assertIsNone(tu.db_ts_to_local(""))

    def test_unparsbar(self):
        self.assertIsNone(tu.db_ts_to_local("kein datum"))
        self.assertIsNone(tu.db_ts_to_local("2026-13-45 99:99:99"))

    def test_mit_zeitzonensuffix_geht_den_alten_weg(self):
        wert = "2026-07-01T10:00:00+02:00"
        self.assertEqual(tu.db_ts_to_local(wert), alt_db_ts_to_local(wert))
        self.assertEqual(len(tu._LOCAL_OFFSET_CACHE), 0,
                         "nur das kanonische Format darf in den Speicher")

    def test_datetime_objekt(self):
        d = datetime(2026, 7, 1, 10, 0, tzinfo=timezone.utc)
        self.assertEqual(tu.db_ts_to_local(d), alt_db_ts_to_local(d))

    def test_mit_z_suffix(self):
        wert = "2026-07-01T10:00:00Z"
        self.assertEqual(tu.db_ts_to_local(wert), alt_db_ts_to_local(wert))

    def test_iso_mit_T_statt_leerzeichen(self):
        """Gleiche Laenge, aber 'T' - muss trotzdem stimmen."""
        wert = "2026-07-01T10:00:00"
        self.assertEqual(tu.db_ts_to_local(wert), alt_db_ts_to_local(wert))

    def test_speicher_laeuft_nicht_voll(self):
        tu._LOCAL_OFFSET_CACHE.clear()
        grenze = tu._LOCAL_OFFSET_CACHE_MAX
        t = datetime(2020, 1, 1)
        for _ in range(grenze + 50):
            tu.db_ts_to_local(t.strftime("%Y-%m-%d %H:%M:%S"))
            t += timedelta(hours=1)
        self.assertLessEqual(len(tu._LOCAL_OFFSET_CACHE), grenze)


class TestParseSchnellweg(unittest.TestCase):

    def test_db_format_bleibt_utc(self):
        d = tu.parse_db_ts("2026-07-01 10:00:00")
        self.assertEqual(d.tzinfo, timezone.utc)
        self.assertEqual((d.hour, d.minute), (10, 0))

    def test_naive_is_local_umgeht_den_schnellweg(self):
        wert = "2026-07-01 10:00:00"
        a = tu.parse_db_ts(wert, naive_is_local=True)
        b = tu.parse_db_ts(wert, naive_is_local=False)
        self.assertNotEqual(a, b, "naive_is_local muss anders rechnen")

    def test_rundlauf_ueber_to_db_ts(self):
        wert = "2026-07-01 10:00:00"
        self.assertEqual(tu.to_db_ts(wert), wert)


if __name__ == "__main__":
    unittest.main()
