"""Kleiner Temperaturverlauf fuer eine Thermostat-Karte (tk.Canvas, ohne matplotlib).

* Linie (2 px, blau): gemessene Raumtemperatur.
* Gestrichelte Stufenlinie (grau): Zieltemperatur.
* Orange hinterlegte Zeitspannen: Thermostat hat geheizt.
* Eine y-Achse (°C); Min/Max links, Zeitmarken unten. Antippen: on_tap().
"""
from __future__ import annotations

import time
from typing import Callable, Optional, Sequence, Tuple

import tkinter as tk

from ui.styles import COLOR_BORDER, COLOR_CARD, COLOR_SUBTEXT, get_safe_font

LINE = "#60A5FA"
TARGET = "#9AA3B2"
HEAT = "#F97316"
GAP_S = 20 * 60          # Luecke > 20 min -> Linie unterbrechen

Point = Tuple[int, Optional[float], Optional[float], bool]


def _blend(fg: str, bg: str, a: float) -> str:
    f = [int(fg[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(bg[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{int(bv + (fv - bv) * a):02x}" for fv, bv in zip(f, b))


def y_range(points: Sequence[Point], min_span: float = 3.0) -> Tuple[float, float]:
    vals = [v for p in points for v in (p[1], p[2]) if v is not None]
    if not vals:
        return 18.0, 22.0
    lo, hi = min(vals), max(vals)
    if hi - lo < min_span:
        mid = (hi + lo) / 2
        lo, hi = mid - min_span / 2, mid + min_span / 2
    pad = (hi - lo) * 0.08
    return lo - pad, hi + pad


def heat_spans(points: Sequence[Point], step: int = 300) -> list:
    """Zusammenhaengende Heiz-Zeitraeume [(von, bis), ...]."""
    spans = []
    for ts, _c, _t, h in points:
        if not h:
            continue
        if spans and ts <= spans[-1][1]:
            spans[-1][1] = ts + step
        else:
            spans.append([ts, ts + step])
    return [tuple(s) for s in spans]


class TempChart(tk.Canvas):
    def __init__(self, parent, width: int = 180, height: int = 84, on_tap: Optional[Callable[[], None]] = None,
                 bg: str = COLOR_CARD, **kw):
        super().__init__(parent, width=width, height=height, bg=bg, highlightthickness=0, **kw)
        self._bg = bg
        self.points: Sequence[Point] = []
        self.hours = 24.0
        self.now: Optional[float] = None
        self.on_tap = on_tap
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<ButtonRelease-1>", lambda _e: self.on_tap and self.on_tap())

    def set_data(self, points: Sequence[Point], hours: float, now: Optional[float] = None) -> None:
        self.points, self.hours, self.now = list(points), float(hours), now
        self._draw()

    def set_width(self, width: int) -> None:
        if int(width) != int(self.cget("width")):
            self.configure(width=int(width))

    def _draw(self) -> None:
        self.delete("all")
        w, h = max(self.winfo_width(), 20), max(self.winfo_height(), 20)
        family = get_safe_font("Bahnschrift", 9)[0]
        small = (family, 9)
        left, right, top, bottom = 38, w - 4, 4, h - 15
        now = self.now or time.time()
        t0 = now - self.hours * 3600
        pts = [p for p in self.points if p[0] >= t0 - 300]

        def x(ts):
            return left + (ts - t0) / (now - t0) * (right - left)

        # Zeitmarken
        ticks = (6, 0) if self.hours <= 6 else (24, 12, 0) if self.hours <= 24 else (48, 24, 0)
        for hrs in ticks:
            if hrs > self.hours:
                continue
            xx = x(now - hrs * 3600)
            self.create_line(xx, top, xx, bottom, fill=_blend(COLOR_BORDER, self._bg, 0.5))
            label = "jetzt" if hrs == 0 else f"−{hrs} h"
            self.create_text(min(max(xx, left + 14), right - 14), h - 1, text=label, anchor="s",
                             fill=COLOR_SUBTEXT, font=small)
        self.create_line(left, bottom, right, bottom, fill=COLOR_BORDER)

        if not any(p[1] is not None for p in pts):
            self.create_text((left + right) / 2, (top + bottom) / 2, text="Verlauf wird aufgezeichnet …",
                             fill=COLOR_SUBTEXT, font=small)
            return
        lo, hi = y_range(pts)

        def y(v):
            return bottom - (v - lo) / (hi - lo) * (bottom - top)

        # Heizen (Hintergrund)
        heat_fill = _blend(HEAT, self._bg, 0.28)
        for a, b in heat_spans(pts):
            xa, xb = max(x(a), left), min(x(b), right)
            if xb > xa:
                self.create_rectangle(xa, top, xb, bottom, fill=heat_fill, width=0)

        # Ziel (Stufe, gestrichelt)
        seg = []
        for ts, _c, tgt, _h in pts:
            if tgt is None:
                if len(seg) >= 4:
                    self.create_line(*seg, fill=TARGET, width=1, dash=(3, 3))
                seg = []
                continue
            xx, yy = x(ts), y(tgt)
            if seg:
                seg += [xx, seg[-1]]
            seg += [xx, yy]
        if len(seg) >= 4:
            self.create_line(*seg, x(now), seg[-1], fill=TARGET, width=1, dash=(3, 3))

        # Ist-Temperatur
        seg, last_ts = [], None
        for ts, cur, _t, _h in pts:
            if cur is None or (last_ts is not None and ts - last_ts > GAP_S):
                if len(seg) >= 4:
                    self.create_line(*seg, fill=LINE, width=2, smooth=False, capstyle="round", joinstyle="round")
                seg = []
            if cur is not None:
                seg += [x(ts), y(cur)]
                last_ts = ts
        if len(seg) >= 4:
            self.create_line(*seg, fill=LINE, width=2, capstyle="round", joinstyle="round")
        elif len(seg) == 2:
            self.create_oval(seg[0] - 2, seg[1] - 2, seg[0] + 2, seg[1] + 2, fill=LINE, width=0)

        # Achse: Min/Max (Textfarbe, nicht Serienfarbe)
        for v in (hi, lo):
            label = f"{v:.0f}°" if abs(hi - lo) >= 3.5 else f"{v:.1f}°".replace(".", ",")
            self.create_text(left - 4, y(v), text=label, anchor="e", fill=COLOR_SUBTEXT, font=small)
