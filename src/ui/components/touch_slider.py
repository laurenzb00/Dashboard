"""Touch-freundlicher Schieberegler (tk.Canvas).

Gegenueber CTkSlider:
* Wert wird schon *waehrend* des Ziehens gemeldet (gedrosselt, Standard 0,3 s),
  damit das Licht live mitgeht - nicht erst beim Loslassen.
* Grosse Flaeche, Wert steht direkt im Regler ("60 %", ganz links "Aus").
* Rastpunkte (z.B. 10/25/50/75/100) schnappen beim Loslassen ein.
* Optionaler Farbverlauf als Spur (z.B. warm -> kalt fuer die Lichtfarbe).
* set_value() von aussen (Zustand aus Home Assistant) wird ignoriert, solange
  der Finger auf dem Regler ist und kurz danach - kein "Zurueckspringen".
"""
from __future__ import annotations

import time
import tkinter as tk
from typing import Callable, Optional, Sequence

from ui.styles import COLOR_BORDER, COLOR_CARD, COLOR_TEXT, get_safe_font


class TouchSlider(tk.Canvas):
    def __init__(
        self,
        parent,
        from_: float = 0,
        to: float = 100,
        value: float = 0,
        height: int = 52,
        fill_color: str = "#F59E0B",
        track_color: str = COLOR_BORDER,
        gradient: Optional[Sequence[str]] = None,
        snap_points: Sequence[float] = (),
        snap_range: float = 3.0,
        formatter: Optional[Callable[[float], str]] = None,
        on_change: Optional[Callable[[float], None]] = None,
        on_release: Optional[Callable[[float], None]] = None,
        throttle_s: float = 0.3,
        bg: str = COLOR_CARD,
        **kwargs,
    ):
        super().__init__(parent, height=height, bg=bg, highlightthickness=0, **kwargs)
        self.from_, self.to = float(from_), float(to)
        self.value = float(value)
        self.fill_color = fill_color
        self.track_color = track_color
        self.gradient = list(gradient) if gradient else None
        self.snap_points = list(snap_points)
        self.snap_range = snap_range
        self.formatter = formatter or (lambda v: f"{v:.0f}")
        self.on_change = on_change
        self.on_release = on_release
        self.throttle_s = throttle_s
        self.enabled = True
        self._dragging = False
        self._last_sent = 0.0
        self._last_user = 0.0
        self._pending = None
        self.bind("<Configure>", lambda _e: self._draw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    # --- oeffentlich -----------------------------------------------------

    def set_value(self, value: Optional[float], force: bool = False) -> None:
        """Wert von aussen setzen (z.B. aktueller HA-Zustand)."""
        if value is None:
            return
        if not force and (self._dragging or time.monotonic() - self._last_user < 3.0):
            return
        self.value = self._clamp(value)
        self._draw()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._draw()

    # --- intern ------------------------------------------------------------

    def _clamp(self, v: float) -> float:
        return max(self.from_, min(self.to, float(v)))

    def _geometry(self):
        w = max(40, self.winfo_width())
        h = max(20, self.winfo_height())
        pad = 4
        r = (h - 2 * pad) / 2
        return w, h, pad, r

    def _value_from_x(self, x: float) -> float:
        w, _h, pad, r = self._geometry()
        f = (x - pad - r) / max(1.0, (w - 2 * pad - 2 * r))
        return self._clamp(self.from_ + max(0.0, min(1.0, f)) * (self.to - self.from_))

    def _pill(self, x0, y0, x1, y1, color):
        r = (y1 - y0) / 2
        if x1 - x0 < 2 * r:
            x1 = x0 + 2 * r
        self.create_oval(x0, y0, x0 + 2 * r, y1, fill=color, outline="")
        self.create_oval(x1 - 2 * r, y0, x1, y1, fill=color, outline="")
        self.create_rectangle(x0 + r, y0, x1 - r, y1, fill=color, outline="")

    def _draw(self) -> None:
        self.delete("all")
        w, h, pad, r = self._geometry()
        x0, y0, x1, y1 = pad, pad, w - pad, h - pad
        f = (self.value - self.from_) / max(1e-9, (self.to - self.from_))
        knob_x = x0 + r + f * (x1 - x0 - 2 * r)
        if self.gradient:
            # Spur als Farbverlauf (Ecken ueber Kreise in Randfarbe)
            n = len(self.gradient)
            self.create_oval(x0, y0, x0 + 2 * r, y1, fill=self.gradient[0], outline="")
            self.create_oval(x1 - 2 * r, y0, x1, y1, fill=self.gradient[-1], outline="")
            span = (x1 - r) - (x0 + r)
            steps = max(2, int(span / 3))
            for i in range(steps):
                t = i / (steps - 1)
                c = self._mix(self.gradient, t)
                xa = x0 + r + span * i / steps
                self.create_rectangle(xa, y0, xa + span / steps + 1, y1, fill=c, outline="")
        else:
            self._pill(x0, y0, x1, y1, self.track_color)
            if f > 0:
                self._pill(x0, y0, max(x0 + 2 * r, knob_x + r), y1, self.fill_color if self.enabled else "#555b66")
        # Knopf
        kr = r - 3
        self.create_oval(knob_x - kr, h / 2 - kr, knob_x + kr, h / 2 + kr, fill="#ffffff", outline="#d0d5de", width=1)
        # Wert-Text
        txt = self.formatter(self.value)
        on_fill = not self.gradient and knob_x > w * 0.32
        tx = (x0 + knob_x) / 2 if on_fill else (knob_x + x1) / 2
        color = "#111317" if (self.gradient or on_fill) else COLOR_TEXT
        self.create_text(tx, h / 2, text=txt, fill=color, font=get_safe_font("Bahnschrift", 15, "bold"))

    @staticmethod
    def _mix(colors: Sequence[str], t: float) -> str:
        t = max(0.0, min(1.0, t))
        seg = t * (len(colors) - 1)
        i = min(int(seg), len(colors) - 2)
        u = seg - i
        a = [int(colors[i][k:k + 2], 16) for k in (1, 3, 5)]
        b = [int(colors[i + 1][k:k + 2], 16) for k in (1, 3, 5)]
        return "#" + "".join(f"{int(x + (y - x) * u):02x}" for x, y in zip(a, b))

    def _press(self, e) -> None:
        if not self.enabled:
            return
        self._dragging = True
        self._last_user = time.monotonic()
        self.value = self._value_from_x(e.x)
        self._draw()
        self._emit_throttled()

    def _motion(self, e) -> None:
        if not self._dragging:
            return
        self._last_user = time.monotonic()
        self.value = self._value_from_x(e.x)
        self._draw()
        self._emit_throttled()

    def _release(self, e) -> None:
        if not self._dragging:
            return
        self._dragging = False
        self._last_user = time.monotonic()
        v = self._value_from_x(e.x)
        for p in self.snap_points:
            if abs(v - p) <= self.snap_range:
                v = float(p)
                break
        self.value = v
        self._draw()
        if self._pending is not None:
            try:
                self.after_cancel(self._pending)
            except Exception:
                pass
            self._pending = None
        if self.on_release:
            self.on_release(self.value)

    def _emit_throttled(self) -> None:
        if not self.on_change:
            return
        now = time.monotonic()
        if now - self._last_sent >= self.throttle_s:
            self._last_sent = now
            self.on_change(self.value)
        elif self._pending is None:
            delay = int((self.throttle_s - (now - self._last_sent)) * 1000) + 10
            self._pending = self.after(delay, self._flush)

    def _flush(self) -> None:
        self._pending = None
        if self._dragging and self.on_change:
            self._last_sent = time.monotonic()
            self.on_change(self.value)
