"""Belegt die beiden Beschleunigungen in tabs/historical._render_plot.

Das Profil vom 21.11.2026 (py-spy, 30 s) zeigte den Oberflaechen-Thread 9,0 s
rechnend. Davon entfielen 2,62 s auf matplotlib.dates.date2num und 2,15 s auf
die Schleife, die den Temperaturbereich bestimmt - zusammen mehr als die
Haelfte.

Die Tab-Klasse laesst sich hier nicht anlegen (Tk, CustomTkinter). Geprueft
wird deshalb, dass die neuen Rechenwege dasselbe liefern wie die alten, und
dass der Code in historical.py sie auch wirklich benutzt.
"""
import os
import re
import unittest
from datetime import datetime, timedelta

import numpy as np

_SRC = os.path.join(os.path.dirname(__file__), "..", "src", "tabs",
                    "historical.py")


def _quelle():
    with open(_SRC, encoding="utf-8") as fh:
        return fh.read()


class TestWertebereich(unittest.TestCase):
    """valid_values: Schleife -> vektoriell."""

    @staticmethod
    def _alt(ordered):
        return [float(v) for vals in ordered.values() for v in vals
                if np.isfinite(v)]

    @staticmethod
    def _neu(ordered):
        teile = [a[np.isfinite(a)] for a in ordered.values()]
        teile = [a for a in teile if a.size]
        return np.concatenate(teile) if teile else np.empty(0)

    def _reihen(self, n=500, anteil_nan=0.05, seed=0):
        r = np.random.default_rng(seed)
        return {f"s{k}": np.where(r.random(n) < anteil_nan, np.nan,
                                  r.random(n) * 60 + 20) for k in range(6)}

    def test_gleiche_werte(self):
        for seed in range(3):
            o = self._reihen(seed=seed)
            alt, neu = self._alt(o), self._neu(o)
            self.assertEqual(len(alt), neu.size)
            self.assertAlmostEqual(min(alt), float(neu.min()), places=9)
            self.assertAlmostEqual(max(alt), float(neu.max()), places=9)

    def test_alles_nan(self):
        o = {"a": np.full(10, np.nan), "b": np.full(10, np.nan)}
        self.assertEqual(self._alt(o), [])
        self.assertEqual(self._neu(o).size, 0)

    def test_leere_reihen(self):
        o = {"a": np.empty(0)}
        self.assertEqual(self._neu(o).size, 0)

    def test_unendlich_zaehlt_nicht_mit(self):
        o = {"a": np.array([1.0, np.inf, -np.inf, 2.0])}
        self.assertEqual(sorted(self._neu(o)), [1.0, 2.0])
        self.assertEqual(sorted(self._alt(o)), [1.0, 2.0])


class TestZeitachse(unittest.TestCase):
    """date2num: einmal statt je Kurve."""

    def test_gleiche_achse_und_beschriftung(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.dates as mdates
        from matplotlib.figure import Figure

        t0 = datetime(2026, 11, 1)
        times = [t0 + timedelta(seconds=10 * i) for i in range(800)]
        reihen = {f"s{k}": np.linspace(20, 80, 800) for k in range(6)}

        def bauen(vorher_umrechnen):
            fig = Figure(figsize=(9, 4), dpi=100)
            ax = fig.add_subplot(111)
            ax.set_xlim(times[0], times[-1])       # wie im Code: zuerst Datum
            x = mdates.date2num(times) if vorher_umrechnen else times
            for v in reihen.values():
                ax.plot(x, v)
            loc = mdates.AutoDateLocator(minticks=4, maxticks=8)
            ax.xaxis.set_major_locator(loc)
            ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
            fig.canvas.draw()
            return (ax.get_xlim(),
                    [t.get_text() for t in ax.get_xticklabels()],
                    [tuple(l.get_xydata()[0]) for l in ax.get_lines()])

        alt = bauen(False)
        neu = bauen(True)
        self.assertEqual(alt[0], neu[0], "xlim muss identisch bleiben")
        self.assertEqual(alt[1], neu[1], "Achsenbeschriftung muss gleich sein")
        np.testing.assert_allclose(np.array(alt[2]), np.array(neu[2]),
                                   err_msg="erster Punkt je Kurve verschoben")


class TestCodeBenutztDieSchnellenWege(unittest.TestCase):
    """Sonst stimmen die Tests oben, aber die Datei macht es trotzdem alt."""

    def test_date2num_wird_vor_der_schleife_gerufen(self):
        q = _quelle()
        self.assertIn("x_werte = mdates.date2num(times_sorted)", q)
        self.assertNotIn("self.ax.plot(times_sorted,", q,
                         "plot() bekommt noch die datetime-Liste")

    def test_wertebereich_ist_vektoriell(self):
        q = _quelle()
        self.assertIn("np.concatenate(_teile)", q)
        self.assertIsNone(
            re.search(r"for value in values\s*\n\s*if np\.isfinite\(value\)", q),
            "die alte Schleife steht noch in der Datei")

    def test_render_status_vertraegt_ein_array(self):
        q = _quelle()
        self.assertIn("if valid_values is not None and len(valid_values) > 0:", q,
                      "'if valid_values:' wirft bei einem Array ValueError")


if __name__ == "__main__":
    unittest.main()
