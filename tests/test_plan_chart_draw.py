import os
import sys
import unittest
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from core.climate_plan import DayPlan, Segment  # noqa: E402
from ui.components import plan_chart_draw as D  # noqa: E402


class Rec:
    """Canvas-Attrappe: merkt sich, was gezeichnet wurde."""

    def __init__(self):
        self.items = []

    def delete(self, *_):
        self.items = []

    def _add(self, kind, args, kw):
        self.items.append((kind, args, kw))

    def create_line(self, *a, **kw):
        self._add("line", a, kw)

    def create_rectangle(self, *a, **kw):
        self._add("rect", a, kw)

    def create_oval(self, *a, **kw):
        self._add("oval", a, kw)

    def create_text(self, *a, **kw):
        self._add("text", a, kw)

    def texts(self):
        return [kw.get("text") for k, _a, kw in self.items if k == "text"]


def ts(h, mi=0, day=4):
    return datetime(2026, 11, day, h, mi).timestamp()


PLAN = DayPlan([Segment(0, 330, 17.0), Segment(330, 1320, 21.0), Segment(1320, 1440, 17.0)])


def points(now):
    out, t, cur = [], ts(0), 18.0
    while t <= now:
        tgt = 17.0 if (t - ts(0)) / 3600 < 5.5 else 21.0
        heating = cur < tgt - 0.2
        cur += 0.05 if heating else -0.02
        out.append((int(t), round(cur, 2), tgt, heating))
        t += 300
    return out


def render(w=1100, h=470, hours=0.0, plan=PLAN, now=None, **kw):
    now = now or ts(15, 40)
    c = Rec()
    D.draw_plan_chart(c, w, h, D.ChartData(points(now), now, hours, plan, current=20.5, target=21.0, **kw))
    return c


class TestHelpers(unittest.TestCase):
    def test_window_today_and_rolling(self):
        t0, t1 = D.window_for(0, ts(15, 40))
        self.assertEqual((datetime.fromtimestamp(t0).hour, round((t1 - t0) / 3600)), (0, 24))
        a, b = D.window_for(24, ts(15, 40))
        self.assertEqual((b, b - a), (ts(15, 40), 24 * 3600))

    def test_y_scale_has_minimum_span_and_whole_degrees(self):
        lo, hi, step = D.y_scale([20.2, 20.6])
        self.assertGreaterEqual(hi - lo, 6)
        self.assertEqual((lo % 1, hi % 1, step), (0, 0, 1.0))
        self.assertEqual(D.y_scale([]), (16.0, 24.0, 2.0))
        self.assertEqual(D.y_scale([5.0, 25.0])[2], 5.0)           # grosse Spanne -> grober Gitterschritt

    def test_heat_spans_merge(self):
        pts = [(0, 20, 21, True), (300, 20, 21, True), (600, 20, 21, False), (900, 20, 21, True)]
        self.assertEqual(D.heat_spans(pts), [(0, 600), (900, 1200)])

    def test_blend(self):
        self.assertEqual(D.blend("#ffffff", "#000000", 0.5), "#7f7f7f")


class TestDraw(unittest.TestCase):
    def test_today_with_plan_shows_plan_in_plain_text(self):
        t = render().texts()
        for expected in ("Absenkung 17°", "Komfort 21°", "jetzt", "00:00", "24:00", "20,5 °C"):
            self.assertIn(expected, t)
        self.assertTrue(any(str(x).startswith("Heute") for x in t))

    def test_manual_override_is_labelled(self):
        c = render(now=ts(21, 30), override_end=ts(23, 59) + 3600)
        self.assertIn("manuell 21°", c.texts())

    def test_without_plan_has_no_plan_strip_or_legend_entries(self):
        t = render(plan=None).texts()
        self.assertNotIn("Absenkung 17°", t)
        self.assertNotIn("Komfort", t)
        self.assertIn("Ist", t)

    def test_learned_plan_is_flagged_in_title_but_no_plan_is_not(self):
        self.assertTrue(any("Plan aus Verlauf gelernt" in str(x) for x in render().texts()))
        self.assertFalse(any("gelernt" in str(x) for x in render(plan=None).texts()))

    def test_rolling_modes_label_days(self):
        t = render(hours=48).texts()
        self.assertTrue(any(str(x).startswith("Letzte 2 Tage") for x in t))
        self.assertIn("Mi", t)                                       # 4.11.2026 ist ein Mittwoch (Mitternacht)

    def test_narrow_width_drops_plan_legend_but_keeps_strip(self):
        t = render(w=520, h=360).texts()
        self.assertNotIn("Absenkung", t)                              # Legendeneintrag weg ...
        self.assertTrue(any("Komfort 21" in str(x) for x in t))      # ... Plan-Leiste bleibt

    def test_empty_state(self):
        c = Rec()
        D.draw_plan_chart(c, 800, 400, D.ChartData([], ts(12), 0, None))
        self.assertIn("Verlauf wird aufgezeichnet …", c.texts())

    def test_label_stays_inside_canvas_near_right_edge(self):
        w = 1100
        c = render(w=w, now=ts(23, 40), override_end=ts(23, 59) + 3600)
        for kind, a, kw in c.items:
            if kind == "text":
                self.assertLessEqual(a[0], w - 4, kw.get("text"))
                self.assertGreaterEqual(a[0], 0, kw.get("text"))


if __name__ == "__main__":
    unittest.main()
