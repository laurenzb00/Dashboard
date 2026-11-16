"""Prueft, dass die zusammengefassten Verlaufsbaender dasselbe Bild ergeben.

tabs/waerme.py laesst sich hier nicht importieren - das Modul zieht die ganze
Oberflaeche nach. Darum wird tank_gradient_bands wie in test_ofenladung.py per
AST aus der Datei geholt und mit Platzhaltern ausgefuehrt.
"""
import ast
import math
import os
import unittest

_SRC = os.path.join(os.path.dirname(__file__), "..", "src", "tabs", "waerme.py")
_NEEDED = ("tank_layer_temp", "tank_gradient_bands")
_CONSTS = ("TANK_QUANT_K",)


def _load():
    with open(_SRC, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    wanted = [n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name in _NEEDED]
    # Die Modulkonstanten, die als Standardwerte in den Signaturen stehen,
    # muessen mit - sonst schlaegt schon das Kompilieren fehl.
    konstanten = [n for n in tree.body
                  if isinstance(n, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id in _CONSTS
                          for t in n.targets)]
    missing = set(_NEEDED) - {n.name for n in wanted}
    if missing:
        raise AssertionError("nicht gefunden in waerme.py: %s" % sorted(missing))
    ns = {"math": math}
    exec(compile(ast.Module(body=konstanten + wanted, type_ignores=[]),
                 _SRC, "exec"), ns)
    return ns


NS = _load()
tank_layer_temp = NS["tank_layer_temp"]
tank_gradient_bands = NS["tank_gradient_bands"]


def referenz(x, y, w, h, t_top, t_bot, color_fn):
    """Die alte Fassung: ein Rechteck je 1-2 Pixel Tankhoehe."""
    r = min(w * 0.22, 26)
    yy = float(y)
    out = []
    while yy < y + h:
        dy_top = yy - y + 0.5
        dy_bot = (y + h) - yy - 0.5
        in_corner = dy_top < r or dy_bot < r
        step = 1.0 if in_corner else 2.0
        inset = 0.0
        if dy_top < r:
            inset = r - math.sqrt(max(0.0, r * r - (r - dy_top) ** 2))
        elif dy_bot < r:
            inset = r - math.sqrt(max(0.0, r * r - (r - dy_bot) ** 2))
        col = color_fn(tank_layer_temp((yy - y) / h, t_top, t_bot))
        out.append((x + inset, yy, x + w - inset, yy + step, col))
        yy += step
    return out


def abtasten(bands, x, y, w, h, schritt=0.5):
    """Farbe und waagrechte Ausdehnung je abgetasteter Zeile."""
    zeilen = {}
    n = int((h) / schritt)
    for i in range(n):
        yy = y + i * schritt + schritt / 2
        treffer = None
        for x0, y0, x1, y1, col in bands:
            if y0 <= yy < y1:
                treffer = (round(x0, 6), round(x1, 6), col)
        zeilen[round(yy, 3)] = treffer
    return zeilen


# Eine Farbskala wie die echte: in Stufen, damit benachbarte Baender
# ueberhaupt zusammengefasst werden koennen.
def farbe_stufig(t):
    if t is None:
        return "#000000"
    k = int(max(0.0, min(90.0, t)))
    return "#%02x%02x%02x" % (k * 2 % 256, 255 - k * 2 % 256, 128)


def farbe_stetig(t):
    """Worst case: jede Zeile eine andere Farbe, nichts laesst sich sparen."""
    if t is None:
        return "#000000"
    return "#%08x" % int(t * 1e6)


class TestGleichesBild(unittest.TestCase):
    faelle = [
        (0, 0, 120, 400, 66.4, 38.25),
        (10, 20, 90, 260, 78.0, 35.0),
        (0, 0, 200, 120, 55.0, 55.0),      # kein Verlauf
        (0, 0, 60, 60, 70.0, 40.0),        # fast nur Rundung
        (5, 5, 118, 401, 40.0, 75.0),      # unten heisser
        (0, 0, 120, 400, None, 44.0),      # oberer Fuehler fehlt
        (0, 0, 120, 400, 61.0, None),      # unterer Fuehler fehlt
        (0, 0, 120, 7, 60.0, 50.0),        # extrem flach
    ]

    def test_identisch_bei_stufiger_farbskala(self):
        for args in self.faelle:
            with self.subTest(args=args):
                neu = list(tank_gradient_bands(*args, farbe_stufig, quant_k=0))
                alt = referenz(*args, farbe_stufig)
                x, y, w, h = args[0], args[1], args[2], args[3]
                self.assertEqual(abtasten(neu, x, y, w, h),
                                 abtasten(alt, x, y, w, h))

    def test_identisch_bei_stetiger_farbskala(self):
        for args in self.faelle:
            with self.subTest(args=args):
                neu = list(tank_gradient_bands(*args, farbe_stetig, quant_k=0))
                alt = referenz(*args, farbe_stetig)
                x, y, w, h = args[0], args[1], args[2], args[3]
                self.assertEqual(abtasten(neu, x, y, w, h),
                                 abtasten(alt, x, y, w, h))

    def test_keine_luecke_und_keine_ueberlappung(self):
        for args in self.faelle:
            with self.subTest(args=args):
                bands = sorted(tank_gradient_bands(*args, farbe_stufig),
                               key=lambda b: b[1])
                y, h = args[1], args[3]
                self.assertAlmostEqual(bands[0][1], float(y), places=6)
                for a, b in zip(bands, bands[1:]):
                    self.assertAlmostEqual(a[3], b[1], places=6,
                                           msg="Luecke oder Ueberlappung")
                self.assertGreaterEqual(bands[-1][3], y + h)

    def test_deutlich_weniger_objekte(self):
        args = (0, 0, 120, 400, 66.4, 38.25)
        neu = len(list(tank_gradient_bands(*args, farbe_stufig)))
        alt = len(referenz(*args, farbe_stufig))
        self.assertLess(neu, alt / 2,
                        "Zusammenfassen bringt zu wenig: %d statt %d" % (neu, alt))

    def test_worst_case_nicht_schlechter(self):
        """Ohne Rundung und mit stetiger Farbskala darf es nicht mehr werden."""
        args = (0, 0, 120, 400, 66.4, 38.25)
        neu = len(list(tank_gradient_bands(*args, farbe_stetig, quant_k=0)))
        alt = len(referenz(*args, farbe_stetig))
        self.assertLessEqual(neu, alt)

    def test_rundung_begrenzt_objekte_auch_bei_stetiger_skala(self):
        """Der eigentliche Gewinn: mit 0,5 K Stufen ist die Zahl gedeckelt."""
        args = (0, 0, 120, 400, 66.4, 38.25)
        neu = len(list(tank_gradient_bands(*args, farbe_stetig)))
        alt = len(referenz(*args, farbe_stetig))
        self.assertLess(neu, alt / 2,
                        "%d statt %d Rechtecke" % (neu, alt))

    def test_rundung_bleibt_unter_einem_viertel_grad(self):
        args = (0, 0, 120, 400, 66.4, 38.25)
        gesehen = []
        tank_gradient_bands(*args, lambda t: gesehen.append(t) or "#000000")
        for t in gesehen:
            if t is None:
                continue
            self.assertAlmostEqual(t, round(t / 0.5) * 0.5, places=9)

    def test_objektzahl_nennen(self):
        """Dokumentiert die Zahlen, auf die sich die Aenderung beruft."""
        args = (0, 0, 120, 400, 66.4, 38.25)
        alt = len(referenz(*args, farbe_stetig))
        neu = len(list(tank_gradient_bands(*args, farbe_stetig)))
        print("\n  Rechtecke je Tank: vorher %d, jetzt %d (%.0f %% weniger)"
              % (alt, neu, 100 * (1 - neu / alt)))


if __name__ == "__main__":
    unittest.main()


class TestKuerzen(unittest.TestCase):
    """kuerzen() ersetzt die itemconfigure/bbox-Schleife in _fit()."""

    @staticmethod
    def _laden():
        with open(_SRC, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        wanted = [n for n in tree.body
                  if isinstance(n, ast.FunctionDef) and n.name == "kuerzen"]
        self_assert = wanted or None
        if not self_assert:
            raise AssertionError("kuerzen() nicht in waerme.py gefunden")
        ns = {}
        exec(compile(ast.Module(body=wanted, type_ignores=[]), _SRC, "exec"), ns)
        return ns["kuerzen"]

    def setUp(self):
        self.kuerzen = self._laden()
        # 10 Pixel je Zeichen, Auslassungszeichen zaehlt wie ein Zeichen.
        self.messen = lambda t: 10 * len(t)

    def test_kurzer_text_bleibt_unveraendert(self):
        self.assertEqual(self.kuerzen("kurz", 1000, self.messen), "kurz")

    def test_passt_genau(self):
        self.assertEqual(self.kuerzen("abcde", 50, self.messen), "abcde")

    def test_wird_gekuerzt_und_passt(self):
        text = "Heute Abend einheizen, der Puffer reicht nicht"
        out = self.kuerzen(text, 200, self.messen)
        self.assertLessEqual(self.messen(out), 200)
        self.assertTrue(out.endswith("…"))
        self.assertTrue(text.startswith(out[:-1]))

    def test_nimmt_den_laengsten_passenden_anfang(self):
        text = "abcdefghijklmnop"
        out = self.kuerzen(text, 100, self.messen)      # 10 Zeichen passen
        self.assertEqual(out, "abcdefghi…")        # 9 + Auslassung

    def test_extrem_schmal_laeuft_nicht_ueber(self):
        """Lieber nur die Auslassung als ein Rest in der Nachbarkarte.

        Die Vorfassung hoerte bei vier Zeichen auf und lieferte dort
        einen zu breiten Text - genau das, was _fit() verhindern soll.
        """
        out = self.kuerzen("abcdefghij", 5, self.messen)
        self.assertEqual(out, "…")
        self.assertLessEqual(self.messen(out), 10)

    def test_gleiches_ergebnis_wie_die_alte_schleife(self):
        """Die Vorfassung kuerzte in Zweierschritten; das Ergebnis darf nicht
        laenger sein als dort und muss weiterhin passen."""
        def alt(text, maxw, messen):
            t = text
            while len(t) > 4 and messen(t) > maxw:
                t = t[:-2]
            return t if t == text else t + "…"
        for text in ("Nachlegen in etwa einer Stunde",
                     "Puffer ist voll, nicht nachlegen",
                     "abc", "a" * 80):
            for maxw in (30, 70, 150, 400, 2000):
                with self.subTest(text=text[:20], maxw=maxw):
                    neu = self.kuerzen(text, maxw, self.messen)
                    frueher = alt(text, maxw, self.messen)
                    if neu != text:
                        # Der neue Weg haelt die Breite immer ein, der alte
                        # nicht (er stoppte bei vier Zeichen).
                        self.assertLessEqual(self.messen(neu), maxw)
                    self.assertLessEqual(len(neu), max(len(frueher), len(text)))
