"""Verhindert den Fehler vom 21.11.2026.

Die Tabs importieren ihre Sichtbarkeitshelfer in einem try/except, damit eine
fehlende ui/visibility.py keinen Tab lahmlegt. Dabei ist mir zweimal dasselbe
passiert: ein Name wurde benutzt, stand aber nur im Rueckfallzweig und nicht
in der Importzeile. Laeuft der Import normal durch - also im Normalfall -, ist
der Name dann unbekannt, und jeder Aufruf endet in einem NameError. Die Tabs
bauen sich noch auf, bekommen aber nie Daten.

Dieser Test prueft statisch, dass beide Zweige dieselben Namen binden und dass
jeder benutzte Name in beiden gebunden ist. Er braucht weder Tk noch einen
Import der Tab-Module.
"""
import ast
import os
import unittest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src")
_DATEIEN = (
    os.path.join(_SRC, "tabs", "ertrag.py"),
    os.path.join(_SRC, "tabs", "waerme.py"),
)
_MODUL = "ui.visibility"


def _analysiere(pfad):
    with open(pfad, encoding="utf-8") as fh:
        baum = ast.parse(fh.read())

    importiert = set()
    rueckfall = set()

    def _holt_visibility(knoten):
        return any(isinstance(k, ast.ImportFrom) and k.module == _MODUL
                   for k in ast.walk(knoten))

    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.ImportFrom) and knoten.module == _MODUL:
            importiert |= {a.asname or a.name for a in knoten.names}
        # Nur das try/except, in dessen try-Zweig genau dieser Import steht -
        # die Dateien haben viele andere try-Bloecke.
        if isinstance(knoten, ast.Try) and _holt_visibility(
                ast.Module(body=knoten.body, type_ignores=[])):
            for behandler in knoten.handlers:
                hilfs = ast.Module(body=behandler.body, type_ignores=[])
                for k in ast.walk(hilfs):
                    if isinstance(k, ast.FunctionDef):
                        rueckfall.add(k.name)
                    elif isinstance(k, ast.Assign):
                        rueckfall |= {t.id for t in k.targets
                                      if isinstance(t, ast.Name)}

    benutzt = {k.id for k in ast.walk(baum)
               if isinstance(k, ast.Name) and isinstance(k.ctx, ast.Load)}
    return importiert, rueckfall, benutzt


class TestSichtbarkeitsImporte(unittest.TestCase):

    def test_jeder_benutzte_helfer_ist_importiert(self):
        for pfad in _DATEIEN:
            importiert, rueckfall, benutzt = _analysiere(pfad)
            relevant = (importiert | rueckfall) & benutzt
            fehlend = sorted(relevant - importiert)
            self.assertEqual(
                fehlend, [],
                "%s benutzt %s, importiert den Namen aber nicht - im Normalfall "
                "gibt das einen NameError" % (os.path.basename(pfad), fehlend))

    def test_jeder_benutzte_helfer_hat_einen_rueckfall(self):
        for pfad in _DATEIEN:
            importiert, rueckfall, benutzt = _analysiere(pfad)
            relevant = (importiert | rueckfall) & benutzt
            fehlend = sorted(relevant - rueckfall)
            self.assertEqual(
                fehlend, [],
                "%s benutzt %s ohne Ersatz im except-Zweig - fehlt die Datei, "
                "ist der Tab tot" % (os.path.basename(pfad), fehlend))

    def test_beide_zweige_binden_dasselbe(self):
        for pfad in _DATEIEN:
            importiert, rueckfall, _ = _analysiere(pfad)
            self.assertEqual(
                sorted(importiert), sorted(rueckfall),
                "%s: Importzeile und Rueckfall stimmen nicht ueberein"
                % os.path.basename(pfad))

    def test_die_helfer_gibt_es_wirklich(self):
        """Gegenprobe gegen ui/visibility.py selbst - ein Tippfehler im
        Importnamen faellt sonst erst zur Laufzeit auf."""
        quelle = os.path.join(_SRC, "ui", "visibility.py")
        with open(quelle, encoding="utf-8") as fh:
            baum = ast.parse(fh.read())
        vorhanden = {k.name for k in baum.body if isinstance(k, ast.FunctionDef)}
        vorhanden |= {t.id for k in baum.body if isinstance(k, ast.Assign)
                      for t in k.targets if isinstance(t, ast.Name)}
        for pfad in _DATEIEN:
            importiert, _, _ = _analysiere(pfad)
            fehlend = sorted(importiert - vorhanden)
            self.assertEqual(
                fehlend, [],
                "%s importiert %s - das gibt es in ui/visibility.py nicht"
                % (os.path.basename(pfad), fehlend))


if __name__ == "__main__":
    unittest.main()
