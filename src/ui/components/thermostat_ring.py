"""Runder Thermostat-Regler (wie am Tado-Thermostat) fuer Touch.

* 270°-Ring, Luecke unten. Der gefuellte Bogen zeigt die Zieltemperatur,
  ein weisser Strich die aktuelle Raumtemperatur.
* In der Mitte gross die Ist-Temperatur, darunter Ziel und Status.
* Unten in der Ring-Luecke zwei runde Tasten − / + (on_step(-0.5 / +0.5)).
* Am Ring ziehen stellt das Ziel ein (0,5er-Schritte); beim Loslassen wird
  `on_release(wert)` aufgerufen. Waehrend des Ziehens zeigt die Mitte das Ziel.
* Farbe des Bogens: orange = heizt gerade, blau = haelt die Temperatur, grau = aus.
"""
from __future__ import annotations

import math
import time
import tkinter as tk
from typing import Callable, Optional

from ui.styles import COLOR_BORDER, COLOR_CARD, COLOR_SUBTEXT, COLOR_TEXT, get_safe_font

START_DEG = 225.0      # Minimum (unten links), tk-Winkel gegen den Uhrzeigersinn ab 3 Uhr
SWEEP_DEG = 270.0
HEAT_COLOR = "#F97316"
IDLE_COLOR = "#60A5FA"
OFF_COLOR = "#4B5563"
EXTRA_H = 16            # Platz unter dem Ring fuer die − / + Tasten


def _fmt(v: Optional[float], unit: str = "°") -> str:
    if v is None:
        return "--"
    return f"{v:.1f}".replace(".", ",") + unit


def ring_color(heating: bool, target: Optional[float], current: Optional[float], off: bool) -> str:
    if off or target is None:
        return OFF_COLOR
    if heating or (current is not None and target > current + 0.3):
        return HEAT_COLOR
    return IDLE_COLOR


def value_to_angle(v: float, lo: float, hi: float) -> float:
    f = (v - lo) / (hi - lo) if hi > lo else 0.0
    return START_DEG - SWEEP_DEG * max(0.0, min(1.0, f))


def angle_to_fraction(deg: float) -> float:
    """tk-Winkel (Grad) -> Anteil 0..1 auf dem Ring; Luecke unten wird zum naechsten Ende geklemmt."""
    d = (START_DEG - deg) % 360.0
    if d <= SWEEP_DEG:
        return d / SWEEP_DEG
    return 1.0 if d < SWEEP_DEG + (360.0 - SWEEP_DEG) / 2 else 0.0


class ThermostatRing(tk.Canvas):
    def __init__(self, parent, from_: float = 5.0, to: float = 25.0, size: int = 220,
                 on_release: Optional[Callable[[float], None]] = None,
                 on_step: Optional[Callable[[float], None]] = None, bg: str = COLOR_CARD, **kw):
        super().__init__(parent, width=size, height=size + EXTRA_H, bg=bg, highlightthickness=0, **kw)
        self.on_step = on_step
        self._buttons = []   # (x, y, r, delta)
        self.from_, self.to = float(from_), float(to)
        self.on_release = on_release
        self.current: Optional[float] = None
        self.target: Optional[float] = None
        self.humidity: Optional[float] = None
        self.heating = False
        self.mode = "plan"
        self.window_open = False
        self.enabled = True
        self._dragging = False
        self._last_user = 0.0
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    # --- oeffentlich -------------------------------------------------------

    def set_state(self, current=None, target=None, humidity=None, heating=False, mode="plan",
                  window_open=False, force: bool = False) -> None:
        self.current, self.humidity = current, humidity
        self.heating, self.mode, self.window_open = heating, mode, window_open
        user_busy = self._dragging or time.monotonic() - self._last_user < 3.0
        if force or not user_busy:
            self.target = target
        self._draw()

    def set_target(self, value: Optional[float]) -> None:
        self.target = value
        self._last_user = time.monotonic()
        self._draw()

    def get(self) -> Optional[float]:
        return self.target

    def set_size(self, size: int) -> None:
        size = int(size)
        if size != int(self.cget("width")):
            self.configure(width=size, height=size + EXTRA_H)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._draw()

    # --- Zeichnen ----------------------------------------------------------

    def _geom(self):
        w, h = max(self.winfo_width(), 10), max(self.winfo_height() - EXTRA_H, 10)
        s = min(w, h)
        thick = max(10, int(s * 0.075))
        r = s / 2 - thick / 2 - 6
        return w / 2, h / 2, r, thick, s

    def _xy(self, cx, cy, r, deg):
        a = math.radians(deg)
        return cx + r * math.cos(a), cy - r * math.sin(a)

    def _draw(self) -> None:
        self.delete("all")
        cx, cy, r, thick, s = self._geom()
        box = (cx - r, cy - r, cx + r, cy + r)
        family = get_safe_font("Bahnschrift", 10)[0]
        off = self.mode == "off"
        # Track
        self.create_arc(*box, start=START_DEG - SWEEP_DEG, extent=SWEEP_DEG, style="arc",
                        outline=COLOR_BORDER, width=thick)
        # Ziel-Bogen
        if self.target is not None and not off:
            a = value_to_angle(self.target, self.from_, self.to)
            color = ring_color(self.heating, self.target, self.current, off) if self.enabled else OFF_COLOR
            extent = START_DEG - a
            if extent > 0.5:
                self.create_arc(*box, start=a, extent=extent, style="arc", outline=color, width=thick)
            # runde Enden + Griff
            for deg, rad in ((START_DEG, thick / 2), (a, thick * 0.75)):
                x, y = self._xy(cx, cy, r, deg)
                self.create_oval(x - rad, y - rad, x + rad, y + rad, fill=color if deg == START_DEG else "#FFFFFF",
                                 outline=color if deg == START_DEG else color, width=0 if deg == START_DEG else 3)
        # Ist-Markierung
        if self.current is not None:
            a = value_to_angle(self.current, self.from_, self.to)
            x0, y0 = self._xy(cx, cy, r - thick * 0.9, a)
            x1, y1 = self._xy(cx, cy, r + thick * 0.9, a)
            self.create_line(x0, y0, x1, y1, fill=COLOR_TEXT, width=3)

        # Mitte
        if self._dragging:
            big, big_col = _fmt(self.target), ring_color(self.heating, self.target, self.current, off)
            sub = "Ziel"
        else:
            big, big_col = _fmt(self.current), COLOR_TEXT
            sub = "Aus" if off else f"Ziel {_fmt(self.target)}"
        # oben: Status (heizt / Fenster / Manuell / Zeitplan), darunter gross Ist, dann Ziel + Feuchte
        status, col = self._status()
        if status:
            self.create_text(cx, cy - s * 0.21, text=status, fill=col, font=(family, max(9, int(s * 0.058)), "bold"))
        if self.humidity is not None and not self._dragging:
            sub = f"{sub} · {self.humidity:.0f} %"
        self.create_text(cx, cy - s * 0.03, text=big, fill=big_col,
                         font=(family, max(16, int(s * 0.15)), "bold"))
        self.create_text(cx, cy + s * 0.15, text=sub, fill=COLOR_SUBTEXT if not self._dragging else big_col,
                         font=(family, max(9, int(s * 0.058)), "bold" if self._dragging else ""))
        # − / + in der Luecke unten
        self._buttons = []
        if self.on_step is not None:
            br = max(17, int(s * 0.11))
            for deg, txt, delta in ((246, "−", -0.5), (294, "+", 0.5)):
                bx, by = self._xy(cx, cy, r * 0.98, deg)
                col = COLOR_BORDER if self.enabled else OFF_COLOR
                self.create_oval(bx - br, by - br, bx + br, by + br, fill="#111317", outline=col, width=2)
                self.create_text(bx, by - 1, text=txt, fill=COLOR_TEXT, font=(family, max(12, int(br * 0.9)), "bold"))
                self._buttons.append((bx, by, br + 4, delta))

    def _status(self):
        if self.window_open:
            return "🪟 Fenster", "#38BDF8"
        if self.heating:
            return "🔥 heizt", "#F97316"
        if self.mode == "manual":
            return "Manuell", "#F59E0B"
        if self.mode == "off":
            return "", COLOR_SUBTEXT
        return "Zeitplan", COLOR_SUBTEXT

    # --- Bedienung ---------------------------------------------------------

    def _value_at(self, e) -> Optional[float]:
        cx, cy, r, thick, _s = self._geom()
        dx, dy = e.x - cx, cy - e.y
        if math.hypot(dx, dy) < r * 0.55:      # Mitte: kein Stellen (vermeidet Fehlbedienung)
            return None
        f = angle_to_fraction(math.degrees(math.atan2(dy, dx)))
        v = self.from_ + f * (self.to - self.from_)
        return round(v * 2) / 2

    def _press(self, e) -> None:
        if not self.enabled:
            return
        for bx, by, br, delta in self._buttons:
            if math.hypot(e.x - bx, e.y - by) <= br:
                if self.on_step:
                    self.on_step(delta)
                return
        v = self._value_at(e)
        if v is None:
            return
        self._dragging = True
        self.target = v
        self._last_user = time.monotonic()
        self._draw()

    def _motion(self, e) -> None:
        if not self._dragging:
            return
        cx, cy, r, _t, _s = self._geom()
        f = angle_to_fraction(math.degrees(math.atan2(cy - e.y, e.x - cx)))
        v = round((self.from_ + f * (self.to - self.from_)) * 2) / 2
        # Spruenge ueber die Luecke unten verhindern
        if self.target is not None and abs(v - self.target) > (self.to - self.from_) * 0.5:
            return
        if v != self.target:
            self.target = v
            self._last_user = time.monotonic()
            self._draw()

    def _release(self, _e) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self._last_user = time.monotonic()
        self._draw()
        if self.on_release and self.target is not None:
            self.on_release(self.target)
