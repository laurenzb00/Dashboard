"""Tests fuer ui/visibility.py - die Bremse fuer verborgene Tabs."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ui.visibility import (ANZEIGE_VERZUG_MS, eigenes_map_event,  # noqa: E402
                           ist_sichtbar, naechster_takt)


class FakeWidget:
    def __init__(self, mapped=1, raise_on_call=False):
        self._mapped = mapped
        self._raise = raise_on_call

    def winfo_ismapped(self):
        if self._raise:
            raise RuntimeError("Widget zerstoert")
        return self._mapped


class FakeEvent:
    def __init__(self, widget):
        self.widget = widget


class TestIstSichtbar(unittest.TestCase):
    def test_sichtbar(self):
        self.assertTrue(ist_sichtbar(FakeWidget(1)))

    def test_verborgen(self):
        self.assertFalse(ist_sichtbar(FakeWidget(0)))

    def test_fehler_gilt_als_sichtbar(self):
        """Eine fehlgeschlagene Abfrage darf keinen Tab stilllegen."""
        self.assertTrue(ist_sichtbar(FakeWidget(raise_on_call=True)))

    def test_kein_widget(self):
        self.assertTrue(ist_sichtbar(None))


class TestEigenesMapEvent(unittest.TestCase):
    def test_eigenes_widget(self):
        w = FakeWidget()
        self.assertTrue(eigenes_map_event(FakeEvent(w), w))

    def test_kind_widget_wird_verworfen(self):
        """Tk schickt <Map> auch fuer jedes Kind - sonst holt ein Tabwechsel
        dutzendfach nach."""
        w, kind = FakeWidget(), FakeWidget()
        self.assertFalse(eigenes_map_event(FakeEvent(kind), w))

    def test_ohne_event_direkt_aufgerufen(self):
        w = FakeWidget()
        self.assertTrue(eigenes_map_event(None, w))

    def test_event_ohne_widget_attribut(self):
        class Kaputt:
            @property
            def widget(self):
                raise AttributeError
        self.assertTrue(eigenes_map_event(Kaputt(), FakeWidget()))


class TestNaechsterTakt(unittest.TestCase):
    def test_sichtbar_nimmt_den_aktiven_takt(self):
        self.assertEqual(naechster_takt(True, 15000, 60000), 15000)

    def test_verborgen_nimmt_den_ruhetakt(self):
        self.assertEqual(naechster_takt(False, 15000, 60000), 60000)

    def test_ohne_ruhetakt_bleibt_es_beim_aktiven(self):
        self.assertEqual(naechster_takt(False, 15000), 15000)

    def test_zu_kleiner_ruhetakt_wird_angehoben(self):
        """Verborgen darf nie oefter nachgesehen werden als sichtbar."""
        self.assertEqual(naechster_takt(False, 15000, 5000), 15000)

    def test_immer_ganzzahlig(self):
        for args in ((True, 15000.7, None), (False, 15000, 60000.9)):
            self.assertIsInstance(naechster_takt(*args), int)

    def test_takt_ist_immer_positiv(self):
        for sichtbar in (True, False):
            self.assertGreater(naechster_takt(sichtbar, 1, 2), 0)


class TestVerzug(unittest.TestCase):
    def test_verzug_ist_kurz_aber_nicht_null(self):
        """Null wuerde den Tabwechsel blockieren, viel mehr waere sichtbar."""
        self.assertGreater(ANZEIGE_VERZUG_MS, 0)
        self.assertLessEqual(ANZEIGE_VERZUG_MS, 100)


if __name__ == "__main__":
    unittest.main()


class Besitzer:
    pass


class TestDarfUeberspringen(unittest.TestCase):
    """Die Regel, die den Ausfall vom 21.11.2026 verhindert."""

    def test_sichtbar_wird_nie_uebersprungen(self):
        from ui.visibility import darf_ueberspringen
        b, w = Besitzer(), FakeWidget(1)
        self.assertFalse(darf_ueberspringen(b, w))

    def test_noch_nie_sichtbar_gewesen_wird_nie_uebersprungen(self):
        """Der Kern: ein Tab, der noch nie zu sehen war, muss arbeiten.

        Kommt <Map> in einer Widget-Verschachtelung nicht beim Tab-Rahmen an,
        bliebe er sonst fuer immer leer.
        """
        from ui.visibility import darf_ueberspringen
        b, w = Besitzer(), FakeWidget(0)
        self.assertFalse(darf_ueberspringen(b, w))

    def test_erst_nach_einmal_sichtbar_wird_gespart(self):
        from ui.visibility import darf_ueberspringen
        b, w = Besitzer(), FakeWidget(1)
        self.assertFalse(darf_ueberspringen(b, w))   # sichtbar, merkt sich das
        w._mapped = 0
        self.assertTrue(darf_ueberspringen(b, w))    # jetzt darf gespart werden

    def test_merker_bleibt_dauerhaft(self):
        from ui.visibility import darf_ueberspringen
        b, w = Besitzer(), FakeWidget(1)
        darf_ueberspringen(b, w)
        w._mapped = 0
        for _ in range(5):
            self.assertTrue(darf_ueberspringen(b, w))

    def test_fehlerhafte_abfrage_arbeitet_weiter(self):
        from ui.visibility import darf_ueberspringen
        b, w = Besitzer(), FakeWidget(raise_on_call=True)
        self.assertFalse(darf_ueberspringen(b, w))

    def test_besitzer_ohne_attributzuweisung(self):
        """Ein Objekt mit __slots__ darf nicht alles zum Absturz bringen."""
        from ui.visibility import darf_ueberspringen

        class Eng:
            __slots__ = ()
        self.assertFalse(darf_ueberspringen(Eng(), FakeWidget(1)))

    def test_eigener_merker_trennt_zwei_takte(self):
        """Live und Chart im Waerme-Tab teilen sich einen Merker - hier der
        Nachweis, dass getrennte Merker moeglich sind."""
        from ui.visibility import darf_ueberspringen
        b, w = Besitzer(), FakeWidget(1)
        darf_ueberspringen(b, w, "_a")
        w._mapped = 0
        self.assertTrue(darf_ueberspringen(b, w, "_a"))
        self.assertFalse(darf_ueberspringen(b, w, "_b"))
