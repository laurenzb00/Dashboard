"""Grosses Tagesdiagramm fuer den Thermo-Tab (tk.Canvas).

Die eigentliche Zeichnung steckt in plan_chart_draw.py (ohne Tk, testbar). Dieses
Widget haelt nur Daten, zeichnet bei Groessenaenderung neu und meldet Antippen.
"""
from __future__ import annotations

from typing import Callable, Optional, Sequence

import tkinter as tk

from core.climate_plan import DayPlan
from ui.components.plan_chart_draw import ChartData, Palette, Point, draw_plan_chart
from ui.styles import (
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_INFO,
    COLOR_SUBTEXT,
    COLOR_TEXT,
    COLOR_WARNING,
    get_safe_font,
)

PALETTE = Palette(bg=COLOR_CARD, border=COLOR_BORDER, text=COLOR_TEXT, subtext=COLOR_SUBTEXT,
                  target=COLOR_INFO, comfort=COLOR_WARNING)


class PlanChart(tk.Canvas):
    def __init__(self, parent, height: int = 440, on_tap: Optional[Callable[[], None]] = None,
                 bg: str = COLOR_CARD, **kw):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, **kw)
        self.on_tap = on_tap
        self._data: Optional[ChartData] = None
        self._family = get_safe_font("Bahnschrift", 10)[0]
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<ButtonRelease-1>", lambda _e: self.on_tap and self.on_tap())

    def set_data(self, points: Sequence[Point], now: float, hours: float, plan: Optional[DayPlan] = None,
                 current: Optional[float] = None, target: Optional[float] = None,
                 override_end: Optional[float] = None) -> None:
        self._data = ChartData(points=list(points), now=now, hours=float(hours), plan=plan, current=current,
                               target=target, override_end=override_end, family=self._family)
        self._draw()

    def set_height(self, height: int) -> None:
        if int(height) != int(self.cget("height")):
            self.configure(height=int(height))

    def _draw(self) -> None:
        if self._data is None:
            self.delete("all")
            return
        draw_plan_chart(self, self.winfo_width(), self.winfo_height(), self._data, PALETTE)
