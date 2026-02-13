from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

import numpy as np
import matplotlib.dates as mdates
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from ui.styles import (
    COLOR_BORDER,
    COLOR_DANGER,
    COLOR_INFO,
    COLOR_PRIMARY,
    COLOR_ROOT,
    COLOR_SUBTEXT,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_WARNING,
)


@dataclass
class EnergyChartDataPoint:
    timestamp: datetime
    pv_power: float
    house_consumption: float


def _normalize_data(data: Iterable[dict]) -> list[EnergyChartDataPoint]:
    out: list[EnergyChartDataPoint] = []
    for item in data:
        try:
            ts = item.get("timestamp")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if not isinstance(ts, datetime):
                continue
            pv = float(item.get("pv_power"))
            cons = float(item.get("house_consumption"))
            out.append(EnergyChartDataPoint(timestamp=ts, pv_power=pv, house_consumption=cons))
        except Exception:
            continue
    out.sort(key=lambda p: p.timestamp)
    return out


def _make_key(points: list[EnergyChartDataPoint]) -> tuple:
    if not points:
        return ("empty",)
    last = points[-1]
    return (len(points), last.timestamp.isoformat(), round(last.pv_power, 6), round(last.house_consumption, 6))


class EnergyChart:
    def __init__(self, parent):
        self._parent = parent
        self._last_key: Optional[tuple] = None

        self.fig = Figure(figsize=(9.0, 4.5), dpi=100)
        self.fig.patch.set_facecolor(COLOR_ROOT)
        self.fig.patch.set_alpha(1.0)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(COLOR_ROOT)

        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas_widget = self.canvas.get_tk_widget()
        try:
            self.canvas_widget.configure(bg=COLOR_ROOT, highlightthickness=0)
        except Exception:
            pass
        self.canvas_widget.pack(fill="both", expand=True)
        self.canvas_widget.bind("<Configure>", self._on_resize)

        self._pv_line = None
        self._cons_line = None
        self._now_vline = None
        self._tooltip_annot = None
        self._hover_vline = None

        self._x_num: Optional[np.ndarray] = None
        self._pv: Optional[np.ndarray] = None
        self._cons: Optional[np.ndarray] = None
        self._timestamps: Optional[list[datetime]] = None

        self._setup_axes_style()
        self._connect_events()
        self._init_interaction_artists()

    def _on_resize(self, event) -> None:
        try:
            w = max(1, int(getattr(event, "width", 1)))
            h = max(1, int(getattr(event, "height", 1)))
            dpi = float(self.fig.get_dpi() or 100.0)
            self.fig.set_size_inches(w / dpi, h / dpi, forward=False)
            self.canvas.draw_idle()
        except Exception:
            pass

    def _setup_axes_style(self) -> None:
        for spine in ("top", "right"):
            self.ax.spines[spine].set_visible(False)
        for spine in ("left", "bottom"):
            self.ax.spines[spine].set_color(COLOR_BORDER)
            self.ax.spines[spine].set_linewidth(0.6)

        self.ax.tick_params(axis="both", which="major", labelsize=9, colors=COLOR_SUBTEXT, length=2, width=0.5)
        # Very subtle horizontal grid.
        self.ax.grid(True, axis="y", color=COLOR_BORDER, alpha=0.08, linewidth=0.6)
        self.ax.grid(False, axis="x")

    def _connect_events(self) -> None:
        # Connect once; artists are re-created after ax.clear().
        self.canvas.mpl_connect("motion_notify_event", self._on_motion)
        self.canvas.mpl_connect("figure_leave_event", self._on_leave)

    def _init_interaction_artists(self) -> None:
        self._tooltip_annot = self.ax.annotate(
            "",
            xy=(0, 0),
            xytext=(10, 10),
            textcoords="offset points",
            bbox=dict(boxstyle="round,pad=0.25", fc=COLOR_ROOT, ec=COLOR_BORDER, alpha=0.95),
            color=COLOR_TEXT,
            fontsize=9,
        )
        self._tooltip_annot.set_visible(False)

        self._hover_vline = self.ax.axvline(datetime.now(), color=COLOR_INFO, alpha=0.18, linewidth=1.0)
        self._hover_vline.set_visible(False)

    def _on_leave(self, _event) -> None:
        if self._tooltip_annot is not None:
            self._tooltip_annot.set_visible(False)
        if self._hover_vline is not None:
            self._hover_vline.set_visible(False)
        self.canvas.draw_idle()

    def _on_motion(self, event) -> None:
        if event.inaxes != self.ax:
            return
        if self._x_num is None or self._pv is None or self._cons is None or not len(self._x_num):
            return
        if event.xdata is None:
            return

        idx = int(np.clip(np.searchsorted(self._x_num, float(event.xdata)), 0, len(self._x_num) - 1))
        # pick nearest of idx / idx-1
        if idx > 0:
            left = self._x_num[idx - 1]
            right = self._x_num[idx]
            if abs(float(event.xdata) - left) < abs(float(event.xdata) - right):
                idx = idx - 1

        ts = self._timestamps[idx]
        pv = float(self._pv[idx])
        cons = float(self._cons[idx])
        diff = pv - cons

        self._hover_vline.set_xdata([ts, ts])
        self._hover_vline.set_visible(True)

        self._tooltip_annot.xy = (ts, max(pv, cons))
        self._tooltip_annot.set_text(
            f"{ts:%d.%m %H:%M}\nPV: {pv:.2f}\nVerbrauch: {cons:.2f}\nΔ: {diff:+.2f}"
        )
        self._tooltip_annot.set_visible(True)
        self.canvas.draw_idle()

    def render(self, data: Iterable[dict]) -> None:
        points = _normalize_data(data)
        key = _make_key(points)
        if key == self._last_key:
            return
        self._last_key = key

        self.ax.clear()
        self.ax.set_facecolor(COLOR_ROOT)
        self._setup_axes_style()
        self._init_interaction_artists()

        if not points:
            self.ax.text(
                0.5,
                0.5,
                "Keine Daten",
                ha="center",
                va="center",
                transform=self.ax.transAxes,
                color=COLOR_SUBTEXT,
                fontsize=10,
            )
            self.canvas.draw_idle()
            return

        xs = [p.timestamp for p in points]
        pv = np.array([p.pv_power for p in points], dtype=float)
        cons = np.array([p.house_consumption for p in points], dtype=float)

        self._timestamps = xs
        self._x_num = mdates.date2num(xs)
        self._pv = pv
        self._cons = cons

        # PV as area + thin line
        self.ax.fill_between(xs, 0, pv, color=COLOR_WARNING, alpha=0.22, linewidth=0)
        self.ax.plot(xs, pv, color=COLOR_WARNING, linewidth=1.2, alpha=0.85)

        # Consumption as strong line
        self.ax.plot(xs, cons, color=COLOR_PRIMARY, linewidth=2.0, alpha=0.95)

        # Surplus/deficit between curves
        surplus = pv - cons
        self.ax.fill_between(xs, cons, pv, where=surplus > 0, color=COLOR_SUCCESS, alpha=0.24, linewidth=0)
        self.ax.fill_between(xs, pv, cons, where=surplus < 0, color=COLOR_DANGER, alpha=0.22, linewidth=0)

        # Current time marker
        now = datetime.now()
        self.ax.axvline(now, color=COLOR_INFO, alpha=0.25, linewidth=1.2)

        # Axis formatting
        self.ax.set_ylabel("kW", fontsize=9, color=COLOR_SUBTEXT, rotation=0, labelpad=12, va="center")
        self.ax.set_ylim(bottom=0)

        locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
        self.ax.xaxis.set_major_locator(locator)
        self.ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        try:
            self.ax.xaxis.get_offset_text().set_visible(False)
        except Exception:
            pass

        self.canvas.draw_idle()


def build_energy_chart(parent, data: Iterable[dict]):
    """Builds a modern PV vs consumption chart.

    data items must provide:
      - timestamp: datetime or ISO string
      - pv_power: float
      - house_consumption: float
    """
    chart = EnergyChart(parent)
    chart.render(data)
    return chart
