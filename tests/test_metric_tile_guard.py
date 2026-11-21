"""Prueft die Sparlogik in MetricTile.set_value.

MetricTile ist ein CTkFrame und laesst sich ohne Tk nicht anlegen. Darum wird
set_value per AST aus der Datei geholt und an ein Stellvertreter-Objekt
gebunden, das die Tk-Aufrufe nur mitschreibt. Getestet wird damit genau der
Code, der auf dem Pi laeuft - nicht eine Nachbildung.
"""
import ast
import os
import re
import unittest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src", "ui",
                    "components", "metric_tile.py")


def _hole_set_value():
    with open(_SRC, encoding="utf-8") as fh:
        baum = ast.parse(fh.read())
    klasse = next(n for n in baum.body
                  if isinstance(n, ast.ClassDef) and n.name == "MetricTile")
    gewollt = [n for n in klasse.body
               if isinstance(n, ast.FunctionDef)
               and n.name in ("set_value", "_animate_value_to",
                              "_render_display_value", "_cancel_value_animation")]
    namen = {n.name for n in gewollt}
    assert "set_value" in namen, "set_value nicht gefunden"
    ns = {"_NUMBER_RE": re.compile(r"[-+]?\d+(?:[.,]\d+)?")}
    exec(compile(ast.Module(body=gewollt, type_ignores=[]), _SRC, "exec"), ns)
    return ns


NS = _hole_set_value()


class FakeLabel:
    def __init__(self, protokoll, name):
        self._p = protokoll
        self._name = name

    def configure(self, **kw):
        self._p.append((self._name, dict(kw)))


class FakeTile:
    """Stellvertreter mit denselben Attributen, die set_value anfasst."""

    def __init__(self, mapped=True):
        self.protokoll = []
        self.value_label = FakeLabel(self.protokoll, "value")
        self.accent_label = FakeLabel(self.protokoll, "accent")
        self._display_value = None
        self._value_prefix = ""
        self._value_suffix = ""
        self._value_decimals = 0
        self._value_anim_job = None
        self._mapped = mapped
        self.nach_aufrufe = []

    # von set_value benutzt
    def winfo_ismapped(self):
        return self._mapped

    def winfo_exists(self):
        return True

    def after(self, ms, fn):
        self.nach_aufrufe.append(ms)
        self._offen = fn
        return "job"

    def animation_abspielen(self, max_schritte=200):
        """Die geplanten Animationsbilder abarbeiten, bis sie auslaufen."""
        for _ in range(max_schritte):
            fn, self._offen = getattr(self, "_offen", None), None
            if fn is None:
                return
            fn()
        raise AssertionError("Animation laeuft nicht aus")

    def after_cancel(self, job):
        pass

    # die mitgezogenen Methoden an sich binden
    def __getattr__(self, name):
        if name in NS:
            fn = NS[name]
            return lambda *a, **k: fn(self, *a, **k)
        raise AttributeError(name)

    def set_value(self, *a, **k):
        return NS["set_value"](self, *a, **k)

    @property
    def texte(self):
        return [kw.get("text") for n, kw in self.protokoll
                if n == "value" and "text" in kw]

    @property
    def farben(self):
        return [kw.get("text_color") for n, kw in self.protokoll
                if "text_color" in kw]


class TestUnveraenderterWert(unittest.TestCase):
    def test_zweimal_derselbe_text_schreibt_einmal(self):
        t = FakeTile()
        t.set_value("42 kWh")
        anzahl = len(t.protokoll)
        t.set_value("42 kWh")
        self.assertEqual(len(t.protokoll), anzahl,
                         "unveraenderter Wert hat trotzdem Tk-Aufrufe erzeugt")

    def test_neuer_text_kommt_an(self):
        t = FakeTile()
        t.set_value("42 kWh")
        t.set_value("45 kWh")
        t.animation_abspielen()
        self.assertEqual(t.texte[-1], "45 kWh",
                         "die Animation muss genau auf dem Endwert landen")

    def test_rueckkehr_zum_vorherigen_wert_kommt_an(self):
        t = FakeTile()
        t.set_value("42 kWh")
        t.set_value("45 kWh")
        vorher = len(t.protokoll)
        t.set_value("42 kWh")
        self.assertGreater(len(t.protokoll), vorher)

    def test_gleicher_text_andere_farbe_kommt_an(self):
        t = FakeTile()
        t.set_value("42 kWh", color="#ff0000")
        vorher = len(t.protokoll)
        t.set_value("42 kWh", color="#00ff00")
        self.assertGreater(len(t.protokoll), vorher)
        self.assertIn("#00ff00", t.farben)

    def test_farbe_wird_nicht_doppelt_gesetzt(self):
        t = FakeTile()
        t.set_value("42 kWh", color="#ff0000")
        t.set_value("43 kWh", color="#ff0000")
        self.assertEqual(t.farben.count("#ff0000"), 2,
                         "Farbe pro Label einmal, nicht erneut bei gleichem Wert")

    def test_erster_wert_wird_immer_geschrieben(self):
        t = FakeTile()
        t.set_value("--")
        self.assertTrue(t.texte, "der erste Wert muss ankommen")


class TestUnsichtbarNichtAnimieren(unittest.TestCase):
    def test_unsichtbar_springt_direkt(self):
        t = FakeTile(mapped=False)
        t.set_value("42 kWh")
        t.set_value("45 kWh")
        self.assertEqual(t.nach_aufrufe, [],
                         "unsichtbar darf keine Animationsbilder planen")
        self.assertEqual(t.texte[-1], "45 kWh")

    def test_sichtbar_animiert_weiterhin(self):
        t = FakeTile(mapped=True)
        t.set_value("42 kWh")
        t.set_value("45 kWh")
        self.assertTrue(t.nach_aufrufe,
                        "sichtbar soll weiterhin sanft hochzaehlen")
        t.animation_abspielen()
        self.assertGreater(len(t.nach_aufrufe), 3,
                           "es sollen mehrere Zwischenbilder laufen")
        self.assertEqual(t.texte[-1], "45 kWh")

    def test_fehlerhafte_abfrage_animiert_weiter(self):
        """Wirft winfo_ismapped, bleibt es beim alten Verhalten."""
        t = FakeTile(mapped=True)

        def kaputt():
            raise RuntimeError
        t.winfo_ismapped = kaputt
        t.set_value("42 kWh")
        t.set_value("45 kWh")
        self.assertTrue(t.nach_aufrufe)
        t.animation_abspielen()
        self.assertEqual(t.texte[-1], "45 kWh")

    def test_endwert_stimmt_auch_unsichtbar(self):
        t = FakeTile(mapped=False)
        for v in ("1 kWh", "7 kWh", "123 kWh"):
            t.set_value(v)
        self.assertEqual(t.texte[-1], "123 kWh")


class TestAufwandBeziffern(unittest.TestCase):
    def test_bilder_je_animation_nennen(self):
        """Belegt die Zahl, auf die sich der Kommentar im Code beruft."""
        t = FakeTile(mapped=True)
        t.set_value("42 kWh")
        t.set_value("45 kWh")
        t.animation_abspielen()
        bilder = len(t.nach_aufrufe)
        schreibvorgaenge = len(t.texte)
        print("\n  Animation 42->45 kWh: %d Bilder, %d Label-Schreibvorgaenge"
              % (bilder, schreibvorgaenge))
        self.assertGreaterEqual(bilder, 5)

    def test_grosser_sprung_braucht_mehr_bilder(self):
        """Die Obergrenze aus dem Kommentar im Code."""
        t = FakeTile(mapped=True)
        t.set_value("0 kWh")
        t.set_value("500 kWh")
        t.animation_abspielen()
        print("  Animation 0->500 kWh: %d Bilder" % len(t.nach_aufrufe))
        self.assertLessEqual(len(t.nach_aufrufe), 25)


if __name__ == "__main__":
    unittest.main()
