from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from core.time_utils import db_ts_to_local
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
from ui.views.chart_resize_mixin import MatplotlibCanvasResizeMixin


@dataclass
class EnergyChartDataPoint:
    timestamp: datetime
    pv_power: float
    house_consumption: float
    soc: Optional[float] = None


def _normalize_data(data: Iterable[dict]) -> list[EnergyChartDataPoint]:
    out: list[EnergyChartDataPoint] = []
    for item in data:
        try:
            ts = item.get("timestamp")
            if isinstance(ts, str):
                ts = db_ts_to_local(ts)
            if not isinstance(ts, datetime):
                continue
            pv = float(item.get("pv_power"))
            cons = float(item.get("house_consumption"))
            soc = item.get("soc")
            soc = float(soc) if soc is not None else None
            out.append(EnergyChartDataPoint(timestamp=ts, pv_power=pv, house_consumption=cons, soc=soc))
        except Exception:
            continue
    out.sort(key=lambda p: p.timestamp)
    return out


def _make_key(points: list[EnergyChartDataPoint]) -> tuple:
    if not points:
        return ("empty",)
    last = points[-1]
    return (len(points), last.timestamp.isoformat(), round(last.pv_power, 6), round(last.house_consumption, 6))


class EnergyChart(MatplotlibCanvasResizeMixin):
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

        self._last_synced_wh: tuple[int, int] = (0, 0)

        self._pv_line = None
        self._cons_line = None
        self._now_vline = None
        self._tooltip_annot = None
        self._hover_vline = None

        self._x_num: Optional[np.ndarray] = None
        self._pv: Optional[np.ndarray] = None
        self._cons: Optional[np.ndarray] = None
        self._timestamps: Optional[list[datetime]] = None
        # Optionale Zusatzebenen der Tagesansicht (Ertrag-Tab "Tag")
        self.ax2 = None
        self._soc: Optional[np.ndarray] = None
        self._fc_x: Optional[np.ndarray] = None
        self._fc_y: Optional[np.ndarray] = None
        self._has_soc_axis = False

        self._setup_axes_style()
        self._connect_events()
        self._init_interaction_artists()

    # _sync_size() und _clear_tk_canvas(): siehe MatplotlibCanvasResizeMixin
    # (ui/views/chart_resize_mixin.py) - waren zuvor hier, in historical.py
    # und in tagesproduktion.py dreifach wortgleich dupliziert.

    def stop(self) -> None:
        """No-op placeholder kept for callers (e.g. ErtragTab.stop())."""
        pass

    def refresh_size(self) -> None:
        """Force a resize-sync using the canvas widget's current geometry.

        Backstop for cases where the canvas widget's own <Configure> event
        fires with a stale/too-small size before the parent tab is actually
        mapped (e.g. a CTkTabview tab built while hidden behind another tab).
        """
        try:
            w = int(self.canvas_widget.winfo_width() or 0)
            h = int(self.canvas_widget.winfo_height() or 0)
            try:
                mapped = bool(self.canvas_widget.winfo_ismapped())
                logging.info(
                    "[ERTRAG-RESIZE] refresh_size canvas=%sx%s mapped=%s last_synced=%s",
                    w, h, mapped, self._last_synced_wh,
                )
            except Exception:
                pass
            if not self._sync_size(w, h):
                return
            self._apply_layout(w, h)
            self.canvas.draw_idle()
        except Exception:
            pass

    def _on_resize(self, event) -> None:
        try:
            w = max(1, int(getattr(event, "width", 1)))
            h = max(1, int(getattr(event, "height", 1)))
            try:
                logging.info(
                    "[ERTRAG-RESIZE] on_resize event w=%s h=%s last_synced=%s",
                    w, h, self._last_synced_wh,
                )
            except Exception:
                pass
            if not self._sync_size(w, h):
                return
            self._apply_layout(w, h)
            self.canvas.draw_idle()
        except Exception:
            pass

    def _apply_layout(self, width: int | None = None, height: int | None = None) -> None:
        """Keep axes and labels inside the current canvas."""
        try:
            width = width or int(self.canvas_widget.winfo_width() or 0)
            height = height or int(self.canvas_widget.winfo_height() or 0)
            if width < 50 or height < 50:
                return
            compact = width < 720
            self.fig.subplots_adjust(
                left=0.12 if compact else 0.08,
                right=(0.90 if compact else 0.93) if self._has_soc_axis else 0.97,
                top=0.91,
                bottom=0.22 if compact else 0.16,
            )
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
        if event.inaxes is None or event.inaxes not in (self.ax, self.ax2):
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

        self._tooltip_annot.xy = (float(self._x_num[idx]), float(np.nanmax([pv, cons, 0.0])))
        lines = [f"{ts:%d.%m %H:%M}", f"PV: {pv:.2f} kW", f"Verbrauch: {cons:.2f} kW", f"Δ: {diff:+.2f} kW"]
        if self._fc_x is not None and len(self._fc_x):
            x = float(mdates.date2num(ts))
            if self._fc_x[0] <= x <= self._fc_x[-1]:
                lines.append(f"Prognose: {float(np.interp(x, self._fc_x, self._fc_y)):.2f} kW")
        if self._soc is not None and idx < len(self._soc) and not np.isnan(self._soc[idx]):
            lines.append(f"Akku: {float(self._soc[idx]):.0f} %")
        self._tooltip_annot.set_text("\n".join(lines))
        self._tooltip_annot.set_visible(True)
        self.canvas.draw_idle()

    def render(
        self,
        data: Iterable[dict],
        forecast: Optional[list[tuple[datetime, float]]] = None,
        empty_spans: Optional[list[tuple[datetime, datetime]]] = None,
        x_range: Optional[tuple[datetime, datetime]] = None,
        show_soc: bool = False,
    ) -> None:
        """Zeichnet PV (Flaeche) und Verbrauch (Linie).

        Optional (Tagesansicht):
          forecast     [(Zeit, kW)] - PV-Prognose als gestrichelte Linie
          empty_spans  [(von, bis)] - Zeitraeume mit leerem Akku (rot hinterlegt)
          x_range      feste x-Achse (z.B. 00:00-24:00), Stunden-Beschriftung
          show_soc     Akkustand (Feld "soc" der Datenpunkte) auf rechter Achse
        """
        points = _normalize_data(data)
        forecast = list(forecast or [])
        empty_spans = list(empty_spans or [])
        key = (
            _make_key(points),
            len(forecast),
            round(sum(v for _, v in forecast), 3),
            tuple((a.isoformat(), b.isoformat()) for a, b in empty_spans),
            (x_range[0].isoformat(), x_range[1].isoformat()) if x_range else None,
            bool(show_soc),
            datetime.now().strftime("%H:%M") if x_range else None,  # "Jetzt"-Marker wandert
        )
        if key == self._last_key:
            return
        self._last_key = key

        if self.ax2 is not None:
            try:
                self.ax2.remove()
            except Exception:
                pass
            self.ax2 = None
        soc_vals = [p.soc for p in points if p.soc is not None]
        self._has_soc_axis = bool(show_soc and soc_vals)

        # Ensure the render buffer matches the current widget size.
        try:
            w = int(self.canvas_widget.winfo_width() or 0)
            h = int(self.canvas_widget.winfo_height() or 0)
            if self._sync_size(w, h):
                self._apply_layout(w, h)
        except Exception:
            pass

        self.ax.clear()
        self.ax.set_facecolor(COLOR_ROOT)
        self._setup_axes_style()
        self._init_interaction_artists()
        self._apply_layout()
        self._x_num = self._pv = self._cons = self._soc = None
        self._timestamps = None
        self._fc_x = self._fc_y = None

        if not points and not forecast:
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

        handles = []
        if points:
            xs_raw = [p.timestamp for p in points]
            pv_raw = [p.pv_power for p in points]
            cons_raw = [p.house_consumption for p in points]
            soc_raw = [p.soc if p.soc is not None else float("nan") for p in points]

            # Datenluecken (z.B. Fronius-Ausfall ueber mehrere Tage) nicht
            # ueberbruecken - sonst verbinden fill_between()/plot() den letzten
            # Punkt vor der Luecke direkt mit dem ersten danach und erzeugen ein
            # spitzes Dreieck quer durch den eigentlich leeren Bereich. Ab mehr
            # als dem 3-fachen des ueblichen Punktabstands (mind. 3h bzw. in der
            # Tagesansicht 20 min) gilt eine Luecke als "echt" - dort einen
            # NaN-Punkt einfuegen, den matplotlib als Unterbrechung behandelt.
            xs, pv_list, cons_list, soc_list = xs_raw[:1], pv_raw[:1], cons_raw[:1], soc_raw[:1]
            if len(xs_raw) > 1:
                deltas = [(xs_raw[i + 1] - xs_raw[i]).total_seconds() for i in range(len(xs_raw) - 1)]
                deltas_sorted = sorted(deltas)
                typical = deltas_sorted[len(deltas_sorted) // 2]
                gap_threshold = max(typical * 3, (20 * 60) if x_range else (3 * 3600))
                for i in range(1, len(xs_raw)):
                    if (xs_raw[i] - xs_raw[i - 1]).total_seconds() > gap_threshold:
                        xs.append(xs_raw[i - 1] + timedelta(seconds=1))
                        pv_list.append(float("nan"))
                        cons_list.append(float("nan"))
                        soc_list.append(float("nan"))
                    xs.append(xs_raw[i])
                    pv_list.append(pv_raw[i])
                    cons_list.append(cons_raw[i])
                    soc_list.append(soc_raw[i])

            pv = np.array(pv_list, dtype=float)
            cons = np.array(cons_list, dtype=float)

            self._timestamps = xs
            self._x_num = mdates.date2num(xs)
            self._pv = pv
            self._cons = cons
            self._soc = np.array(soc_list, dtype=float) if self._has_soc_axis else None

            # PV as area + thin line
            self.ax.fill_between(xs, 0, pv, color=COLOR_WARNING, alpha=0.22, linewidth=0)
            (h_pv,) = self.ax.plot(xs, pv, color=COLOR_WARNING, linewidth=1.2, alpha=0.85, label="PV")

            # Consumption as strong line
            (h_cons,) = self.ax.plot(xs, cons, color=COLOR_PRIMARY, linewidth=2.0, alpha=0.95, label="Verbrauch")

            # Surplus/deficit between curves
            surplus = pv - cons
            self.ax.fill_between(xs, cons, pv, where=surplus > 0, color=COLOR_SUCCESS, alpha=0.24, linewidth=0)
            self.ax.fill_between(xs, pv, cons, where=surplus < 0, color=COLOR_DANGER, alpha=0.22, linewidth=0)
            handles += [h_pv, h_cons]

        if forecast:
            fx = [t for t, _ in forecast]
            fy = [v for _, v in forecast]
            (h_fc,) = self.ax.plot(fx, fy, color=COLOR_WARNING, linewidth=1.6, linestyle=(0, (4, 3)),
                                   alpha=0.95, label="Prognose")
            self._fc_x = mdates.date2num(fx)
            self._fc_y = np.array(fy, dtype=float)
            handles.insert(1 if points else 0, h_fc)

        for i, (a, b) in enumerate(empty_spans):
            self.ax.axvspan(a, b, color=COLOR_DANGER, alpha=0.10, linewidth=0, zorder=0)
            if i == 0:
                self.ax.text(a, 0.97, " Akku leer", transform=self.ax.get_xaxis_transform(),
                             color=COLOR_DANGER, fontsize=8, va="top", ha="left", alpha=0.9)

        if self._has_soc_axis and self._timestamps is not None:
            self.ax2 = self.ax.twinx()
            self.ax2.set_ylim(0, 105)
            self.ax2.set_yticks([0, 50, 100])
            self.ax2.yaxis.set_major_formatter(lambda v, _pos: f"{v:.0f}%")
            self.ax2.tick_params(axis="y", labelsize=8, colors=COLOR_SUCCESS, length=2, width=0.5)
            for spine in ("top", "left", "bottom"):
                self.ax2.spines[spine].set_visible(False)
            self.ax2.spines["right"].set_color(COLOR_BORDER)
            self.ax2.spines["right"].set_linewidth(0.6)
            (h_soc,) = self.ax2.plot(self._timestamps, self._soc, color=COLOR_SUCCESS, linewidth=1.4,
                                     alpha=0.9, label="Akku")
            handles.append(h_soc)
            # Tooltip/Hover-Linie gehoeren auf die oberste Achse, sonst verdeckt
            self._tooltip_annot.remove()
            self._tooltip_annot = self.ax2.annotate(
                "", xy=(0, 0), xytext=(10, 10), textcoords="offset points",
                bbox=dict(boxstyle="round,pad=0.25", fc=COLOR_ROOT, ec=COLOR_BORDER, alpha=0.95),
                color=COLOR_TEXT, fontsize=9, xycoords=self.ax.transData,
            )
            self._tooltip_annot.set_visible(False)

        # Current time marker
        now = datetime.now()
        if x_range is None or x_range[0] <= now <= x_range[1]:
            self.ax.axvline(now, color=COLOR_INFO, alpha=0.25, linewidth=1.2)

        # Axis formatting
        self.ax.set_ylabel("kW", fontsize=9, color=COLOR_SUBTEXT, rotation=0, labelpad=12, va="center")
        self.ax.set_ylim(bottom=0)

        if x_range is not None:
            self.ax.set_xlim(x_range[0], x_range[1])
            locator = mdates.HourLocator(byhour=range(0, 24, 3))
            self.ax.xaxis.set_major_locator(locator)
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        else:
            locator = mdates.AutoDateLocator(minticks=6, maxticks=12)
            self.ax.xaxis.set_major_locator(locator)
            self.ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        try:
            self.ax.xaxis.get_offset_text().set_visible(False)
        except Exception:
            pass

        if len(handles) > 2 or forecast or self._has_soc_axis:
            leg = self.ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.0, 1.12), ncol=len(handles),
                                 frameon=False, fontsize=8, handlelength=1.8, columnspacing=1.2)
            for txt in leg.get_texts():
                txt.set_color(COLOR_SUBTEXT)

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
