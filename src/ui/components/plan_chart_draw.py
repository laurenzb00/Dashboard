"""Zeichenlogik des grossen Tagesdiagramms (Thermo-Tab) - ohne Tk-Import.

Das Widget (plan_chart.PlanChart) ruft nur draw_plan_chart() auf. Weil hier kein
Tk gebraucht wird, laesst sich das Diagramm in Tests und fuer Vorschaubilder mit
einer beliebigen Canvas-aehnlichen Klasse zeichnen (create_line/rectangle/text/oval, delete).

Inhalt (von hinten nach vorn):
* Farbige Baender = der Zeitplan: Komfort (warm), Absenkung (indigo), Aus (grau).
* Plan-Stufenlinie je Band, mit Temperatur beschriftet.
* Gestrichelt (cyan): tatsaechliche Zieltemperatur der Vergangenheit - weicht sie
  vom Plan ab, wurde manuell eingegriffen. In die Zukunft: Dauer des manuellen Eingriffs.
* Orange Balken am Boden: Heizphasen.
* Weisse Linie: gemessene Raumtemperatur. Senkrechte "jetzt"-Linie mit Istwert.
* Unter dem Diagramm: Zeitplan-Leiste mit Klartext ("Absenkung 17°", "Komfort 21°").
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, List, Optional, Sequence, Tuple

from core.climate_plan import DayPlan, plan_spans

GAP_S = 20 * 60                      # Luecke > 20 min -> Linie unterbrechen
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

Point = Tuple[int, Optional[float], Optional[float], bool]


@dataclass(frozen=True)
class Palette:
    """Entspricht ui.styles (dort Tk-Import, deshalb hier gespiegelt; plan_chart.py reicht die echten Werte durch)."""
    bg: str = "#1B1F27"
    border: str = "#3A4150"
    text: str = "#E6ECF5"
    subtext: str = "#9AA3B2"
    ist: str = "#F3F6FB"
    target: str = "#38BDF8"
    heat: str = "#F97316"
    comfort: str = "#F59E0B"
    eco: str = "#818CF8"
    off: str = "#6B7280"


@dataclass
class ChartData:
    points: Sequence[Point]
    now: float
    hours: float = 0.0                       # 0 = "Heute" (00:00-24:00), sonst Rueckblick in Stunden
    plan: Optional[DayPlan] = None
    current: Optional[float] = None
    target: Optional[float] = None
    override_end: Optional[float] = None     # Ende eines manuellen Eingriffs (Unix-Sek.)
    family: str = "Arial"


def blend(fg: str, bg: str, a: float) -> str:
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(bv + (fv - bv) * a):02x}" for fv, bv in zip(f, b))


def window_for(hours: float, now: float) -> Tuple[float, float]:
    """(t0, t1): hours <= 0 -> heutiger Kalendertag, sonst die letzten `hours` Stunden bis jetzt."""
    if hours and hours > 0:
        return now - hours * 3600, now
    mid = datetime.fromtimestamp(now).replace(hour=0, minute=0, second=0, microsecond=0)
    return mid.timestamp(), (mid + timedelta(days=1)).timestamp()


def heat_spans(points: Sequence[Point], step: int = 300) -> List[Tuple[float, float]]:
    spans: List[List[float]] = []
    for ts, _c, _t, heating in points:
        if not heating:
            continue
        if spans and ts <= spans[-1][1]:
            spans[-1][1] = ts + step
        else:
            spans.append([ts, ts + step])
    return [(a, b) for a, b in spans]


def y_scale(values: Sequence[float], min_span: float = 6.0) -> Tuple[float, float, float]:
    """(lo, hi, Gitterschritt) auf ganze Grad, mit etwas Luft und Mindestspanne."""
    if not values:
        return 16.0, 24.0, 2.0
    lo, hi = math.floor(min(values) - 0.5), math.ceil(max(values) + 0.5)
    if hi - lo < min_span:
        extra = (min_span - (hi - lo)) / 2
        lo, hi = math.floor(lo - extra), math.ceil(hi + extra)
    span = hi - lo
    step = 1.0 if span <= 8 else 2.0 if span <= 16 else 5.0
    lo = math.floor(lo / step) * step
    hi = math.ceil(hi / step) * step
    return lo, hi, step


def _fmt_deg(v: float) -> str:
    return (f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.1f}".replace(".", ",")) + "°"


def _title_parts(d: ChartData) -> Tuple[str, str]:
    """(Titel, Zusatz): Zusatz = Hinweis, dass der Plan nur gelernt (also naeherungsweise) ist."""
    if d.hours <= 0:
        today = datetime.fromtimestamp(d.now)
        base = f"Heute · {WEEKDAYS[today.weekday()]} {today.day}.{today.month}."
    else:
        base = "Letzte 24 Stunden" if d.hours <= 24 else "Letzte 2 Tage"
    note = "· Plan aus Verlauf gelernt" if d.plan is not None and d.plan.source == "history" else ""
    return base, note


def draw_plan_chart(c: Any, w: int, h: int, d: ChartData, pal: Palette = Palette()) -> None:
    c.delete("all")
    w, h = max(int(w), 200), max(int(h), 140)
    # Schriftgroessen in PIXELN (negativ = Pixel in Tk) - unabhaengig vom Display-DPI
    fs = 20 if w >= 900 else 16
    small = 16 if w >= 900 else 13

    def font(size: int = fs, bold: bool = False):
        return (d.family, -size, "bold") if bold else (d.family, -size)

    level_col = {"comfort": pal.comfort, "eco": pal.eco, "off": pal.off}
    plan = d.plan
    t0, t1 = window_for(d.hours, d.now)

    left, right = 64, w - 18
    legend_h = 40
    strip_h = 46 if plan else 0
    axis_h = 34
    top = legend_h + 6
    bottom = h - axis_h - (strip_h + 8 if strip_h else 0)
    if bottom - top < 60:
        bottom = top + 60

    def x(ts: float) -> float:
        return left + (min(max(ts, t0), t1) - t0) / (t1 - t0) * (right - left)

    pts = [p for p in d.points if t0 - GAP_S <= p[0] <= t1]
    spans = plan_spans(plan, t0, t1) if plan else []

    # --- y-Bereich aus allem, was gezeichnet wird ------------------------------
    vals: List[float] = [t for _a, _b, t in spans if t is not None]
    vals += [v for p in pts for v in (p[1], p[2]) if v is not None]
    vals += [v for v in (d.current, d.target) if v is not None]
    lo, hi, step = y_scale(vals)

    def y(v: float) -> float:
        return bottom - (v - lo) / (hi - lo) * (bottom - top)

    # --- Titel + Legende ---------------------------------------------------------
    title, note = _title_parts(d)
    title_w = len(title) * fs * 0.64
    c.create_text(left, legend_h / 2 + 2, text=title, anchor="w", fill=pal.text, font=font(fs, True))
    if note:
        c.create_text(left + title_w + 12, legend_h / 2 + 3, text=note, anchor="w", fill=pal.subtext,
                      font=font(small))
    items = [("Ist", "line", pal.ist), ("Ziel", "dash", pal.target), ("Heizt", "box", pal.heat)]
    if plan:
        items += [("Komfort", "box", blend(pal.comfort, pal.bg, 0.55)), ("Absenkung", "box", blend(pal.eco, pal.bg, 0.55))]

    def item_w(it) -> float:
        return len(it[0]) * small * 0.62 + 8 + (28 if it[1] != "box" else 20) + 12

    free = right - (left + title_w + (len(note) * small * 0.6 + 12 if note else 0) + 24)
    while items and sum(item_w(it) for it in items) > free:
        items.pop()                      # von rechts: erst Absenkung/Komfort (steht ohnehin in der Leiste)
    cx = right
    for label, kind, col in reversed(items):
        tw = len(label) * small * 0.62
        cx -= tw
        c.create_text(cx, legend_h / 2 + 2, text=label, anchor="w", fill=pal.subtext, font=font(small))
        cx -= 8
        ly = legend_h / 2 + 2
        if kind == "line":
            c.create_line(cx - 22, ly, cx, ly, fill=col, width=3, capstyle="round")
            cx -= 28
        elif kind == "dash":
            c.create_line(cx - 22, ly, cx, ly, fill=col, width=2, dash=(5, 3))
            cx -= 28
        else:
            c.create_rectangle(cx - 14, ly - 7, cx, ly + 7, fill=col, width=0)
            cx -= 20
        cx -= 12

    if not pts and not spans:
        c.create_text((left + right) / 2, (top + bottom) / 2, text="Verlauf wird aufgezeichnet …",
                      fill=pal.subtext, font=font(fs))
        return

    # --- Zeitplan-Baender --------------------------------------------------------
    for a, b, temp in spans:
        lvl = plan.level(temp)
        c.create_rectangle(x(a), top, x(b), bottom, fill=blend(level_col[lvl], pal.bg, 0.20), width=0)

    # --- Gitter + Achsenbeschriftung ---------------------------------------------
    grid = blend(pal.border, pal.bg, 0.55)
    v = lo
    while v <= hi + 1e-9:
        yy = y(v)
        c.create_line(left, yy, right, yy, fill=grid)
        c.create_text(left - 8, yy, text=_fmt_deg(v), anchor="e", fill=pal.subtext, font=font(small))
        v += step
    tick_h = 3 if d.hours <= 0 else 6 if d.hours <= 24 else 12
    day0 = datetime.fromtimestamp(t0).replace(hour=0, minute=0, second=0, microsecond=0)
    label_y = h - axis_h / 2 + 2
    k = 0
    while True:
        dt = day0 + timedelta(hours=k * tick_h)
        ts = dt.timestamp()
        k += 1
        if ts > t1 + 1:
            break
        if ts < t0 - 1:
            continue
        midnight = dt.hour == 0
        c.create_line(x(ts), top, x(ts), bottom, fill=blend(pal.border, pal.bg, 0.9 if midnight else 0.45))
        if d.hours <= 0:
            txt = "24:00" if ts >= t1 - 1 else dt.strftime("%H:%M")
        else:
            txt = WEEKDAYS[dt.weekday()] if midnight else dt.strftime("%H:%M")
        c.create_text(min(max(x(ts), left + 18), right - 18), label_y, text=txt, anchor="center",
                      fill=pal.text if midnight else pal.subtext, font=font(small, midnight and d.hours > 0))

    # --- Plan-Stufenlinie + Beschriftung -------------------------------------------
    prev_y: Optional[float] = None
    for a, b, temp in spans:
        if temp is None:
            prev_y = None
            continue
        col = level_col[plan.level(temp)]
        yy = y(temp)
        c.create_line(x(a), yy, x(b), yy, fill=col, width=3)
        if prev_y is not None:
            c.create_line(x(a), prev_y, x(a), yy, fill=blend(col, pal.bg, 0.6), width=2)
        prev_y = yy
        if x(b) - x(a) >= 70:
            ty = yy - 16 if yy - 16 > top + 34 else yy + 16
            c.create_text(x(a) + 8, ty, text=_fmt_deg(temp), anchor="w", fill=col, font=font(fs, True))

    # --- Heizphasen --------------------------------------------------------------
    for a, b in heat_spans(pts):
        xa, xb = x(a), x(b)
        if xb > xa:
            c.create_rectangle(xa, bottom - 10, xb, bottom, fill=pal.heat, width=0)

    # --- tatsaechliche Zieltemperatur (gestrichelt) ---------------------------------
    seg: List[float] = []
    for ts, _cur, tgt, _h in pts:
        if ts > d.now:
            break
        if tgt is None:
            if len(seg) >= 4:
                c.create_line(*seg, fill=pal.target, width=2, dash=(6, 4))
            seg = []
            continue
        xx, yy = x(ts), y(tgt)
        if seg:
            seg += [xx, seg[-1]]
        seg += [xx, yy]
    if len(seg) >= 4:
        c.create_line(*seg, x(d.now), seg[-1], fill=pal.target, width=2, dash=(6, 4))
    if d.override_end and d.target is not None and d.override_end > d.now and t0 <= d.now <= t1:
        end = min(d.override_end, t1)
        c.create_line(x(d.now), y(d.target), x(end), y(d.target), fill=pal.target, width=2, dash=(6, 4))
        label = f"manuell {_fmt_deg(d.target)}"
        need = len(label) * small * 0.66 + 14
        if x(end) - x(d.now) > 40:
            above_line = d.current is None or d.target > d.current   # Istwert-Label sitzt dann darunter
            ly = y(d.target) + (-14 if above_line and y(d.target) - 14 > top + 30 else 16)
            if right - x(d.now) >= need:
                c.create_text(x(d.now) + 10, ly, text=label, anchor="w", fill=pal.target, font=font(small, True))
            else:                                    # kein Platz rechts: links vom Jetzt-Strich
                c.create_text(x(d.now) - 10, ly, text=label, anchor="e", fill=pal.target, font=font(small, True))

    # --- gemessene Temperatur --------------------------------------------------------
    seg, last_ts = [], None
    for ts, cur, _t, _h in pts:
        if ts > d.now + 300:
            break
        if cur is None or (last_ts is not None and ts - last_ts > GAP_S):
            if len(seg) >= 4:
                c.create_line(*seg, fill=pal.ist, width=3, capstyle="round", joinstyle="round")
            seg = []
        if cur is not None:
            seg += [x(ts), y(cur)]
            last_ts = ts
    if len(seg) >= 4:
        c.create_line(*seg, fill=pal.ist, width=3, capstyle="round", joinstyle="round")

    # --- jetzt ---------------------------------------------------------------------
    if t0 <= d.now <= t1:
        nx = x(d.now)
        c.create_line(nx, top, nx, bottom, fill=pal.text, width=2, dash=(2, 4))
        tag_w = 64
        tx = min(max(nx, left + tag_w / 2), w - 4 - tag_w / 2)
        c.create_rectangle(tx - tag_w / 2, top, tx + tag_w / 2, top + 26, fill=pal.text, width=0)
        c.create_text(tx, top + 13, text="jetzt", anchor="center", fill=pal.bg, font=font(small, True))
        if d.current is not None:
            cy = y(d.current)
            c.create_oval(nx - 8, cy - 8, nx + 8, cy + 8, fill=pal.ist, outline=pal.bg, width=3)
            label = _fmt_deg(d.current).replace("°", " °C")
            lw = len(label) * fs * 0.62 + 12
            on_right = (w - 4) - (nx + 14) >= lw          # Platz rechts vom Punkt (dort ist noch nichts gemessen)
            # Istwert unter den Punkt, wenn das Ziel darueber liegt (Linie/Label dort) oder oben die Marke im Weg ist
            below = (d.target is not None and d.target > d.current) or cy - 26 < top + 30
            lx = nx + 14 if on_right else nx - 14
            ly = cy + (22 if below else -20)
            x0, x1 = (lx - 4, lx + lw - 4) if on_right else (lx - lw + 4, lx + 4)
            c.create_rectangle(x0, ly - fs * 0.7, x1, ly + fs * 0.7, fill=pal.bg, width=0)   # Plakette: Linien nicht durch den Text
            c.create_text(lx, ly, text=label, anchor="w" if on_right else "e", fill=pal.ist, font=font(fs, True))

    c.create_line(left, bottom, right, bottom, fill=pal.border)

    # --- Zeitplan-Leiste -------------------------------------------------------------
    if plan:
        sy0 = bottom + 8
        for a, b, temp in spans:
            xa, xb = x(a) + 1, x(b) - 1
            if xb - xa < 3:
                continue
            lvl = plan.level(temp)
            c.create_rectangle(xa, sy0, xb, sy0 + strip_h, fill=blend(level_col[lvl], pal.bg, 0.45), width=0)
            name = {"comfort": "Komfort", "eco": "Absenkung", "off": "Aus"}[lvl]
            full = name if temp is None else f"{name} {_fmt_deg(temp)}"
            width = xb - xa
            if width >= len(full) * fs * 0.62 + 12:
                label = full
            elif temp is not None and width >= 52:
                label = _fmt_deg(temp)
            else:
                continue
            c.create_text((xa + xb) / 2, sy0 + strip_h / 2, text=label, anchor="center", fill=pal.text,
                          font=font(fs, True))
