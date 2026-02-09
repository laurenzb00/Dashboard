import os
import time
from collections import deque
from datetime import datetime, timedelta

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.figure import Figure
from matplotlib.patches import Ellipse, FancyBboxPatch, Rectangle
from mpl_toolkits.axes_grid1 import make_axes_locatable

try:
    from ui.styles import (
        COLOR_CARD,
        COLOR_ROOT,
        COLOR_BORDER,
        COLOR_DANGER,
        COLOR_INFO,
        COLOR_PRIMARY,
        COLOR_SUBTEXT,
        COLOR_SUCCESS,
        COLOR_TEXT,
        COLOR_TITLE,
        COLOR_WARNING,
    )
except ImportError:
    # Fallback Farben, falls Import fehlschlägt
    COLOR_ROOT = "#0E0F12"
    COLOR_CARD = "#0E0F12"
    COLOR_BORDER = "#0E0F12"
    COLOR_PRIMARY = "#3B82F6"
    COLOR_SUCCESS = "#10B981"
    COLOR_WARNING = "#F59E0B"
    COLOR_INFO = "#38BDF8"
    COLOR_DANGER = "#EF4444"
    COLOR_TEXT = "#E6ECF5"
    COLOR_SUBTEXT = "#9AA3B2"
    COLOR_TITLE = "#AAB3C5"

try:
    from core.datastore import get_shared_datastore
except ImportError:
    def get_shared_datastore():
        return None

from core.schema import (
    BUF_TOP_C,
    BUF_MID_C,
    BUF_BOTTOM_C,
    BMK_WARMWASSER_C,
    BMK_BOILER_C,
    PV_POWER_KW,
)

DEBUG_LOG = os.environ.get("DASHBOARD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


class BufferStorageView(tk.Frame):

    def _update_sparkline(self) -> None:
        if not hasattr(self, "spark_ax") or not hasattr(self, "spark_canvas"):
            return
        refresh_needed = (time.time() - getattr(self, "_spark_cache_ts", 0.0)) > 60.0
        if refresh_needed:
            try:
                pv_series_db = self._load_pv_series(hours=24, bin_minutes=15)
            except Exception as exc:
                if DEBUG_LOG:
                    print(f"[BUFFER] _load_pv_series error: {exc}")
                pv_series_db = []
            try:
                temp_series_db = self._load_outdoor_temp_series(hours=24, bin_minutes=15)
            except Exception as exc:
                if DEBUG_LOG:
                    print(f"[BUFFER] _load_outdoor_temp_series error: {exc}")
                temp_series_db = []
            self._spark_cache_pv = pv_series_db
            self._spark_cache_temp = temp_series_db
            self._spark_cache_ts = time.time()
        else:
            pv_series_db = list(self._spark_cache_pv)
            temp_series_db = list(self._spark_cache_temp)

        pv_series = list(pv_series_db)
        pv_hours = 24 if pv_series else 0
        if not pv_series:
            pv_series = self._history_to_series(self._spark_history_pv, hours=6, bin_minutes=5)
            if pv_series:
                pv_hours = 6

        temp_series = list(temp_series_db)
        temp_hours = 24 if temp_series else 0
        if not temp_series:
            temp_series = self._history_to_series(self._spark_history_temp, hours=6, bin_minutes=5)
            if temp_series:
                temp_hours = 6

        if DEBUG_LOG:
            print(f"[BUFFER] sparkline pv_series={len(pv_series)} temp_series={len(temp_series)}")

        self.spark_ax.clear()
        now = datetime.now()
        window_hours = max(pv_hours, temp_hours, 6 if (pv_series or temp_series) else 24)
        cutoff = now - timedelta(hours=window_hours)
        if hasattr(self, "spark_ax2"):
            try:
                self.spark_ax2.remove()
            except Exception:
                pass
        self.spark_ax2 = self.spark_ax.twinx()
        ax2 = self.spark_ax2
        ax2.patch.set_alpha(0)
        for ax in (self.spark_ax, ax2):
            ax.set_facecolor('none')
            for spine in ax.spines.values():
                spine.set_visible(True)
                spine.set_edgecolor(COLOR_BORDER)
                spine.set_linewidth(0.5)

        if not pv_series and not temp_series:
            self.spark_ax.text(0.5, 0.5, "Keine Daten (24h)", ha="center", va="center",
                               transform=self.spark_ax.transAxes, color=COLOR_SUBTEXT, fontsize=9)
            self.spark_ax.set_xticks([])
            self.spark_ax.set_yticks([])
            ax2.set_yticks([])
            try:
                self.spark_canvas.draw_idle()
                self.spark_canvas.draw()
            except Exception as exc:
                print(f"[BUFFER] Sparkline canvas draw error: {exc}")
            return

        if pv_series:
            xs_pv, ys_pv = zip(*pv_series)
            self.spark_ax.plot(xs_pv, ys_pv, color=COLOR_SUCCESS, linewidth=2.0, alpha=0.9)
            self.spark_ax.fill_between(xs_pv, ys_pv, color=COLOR_SUCCESS, alpha=0.15)
            self.spark_ax.scatter([xs_pv[-1]], [ys_pv[-1]], color=COLOR_SUCCESS, s=12, zorder=10)
            try:
                max_pv = max(float(v) for v in ys_pv)
            except Exception:
                max_pv = 0.0
            self.spark_ax.set_ylim(0.0, max(0.5, max_pv * 1.15))
        if temp_series:
            xs_temp, ys_temp = zip(*temp_series)
            ax2.plot(xs_temp, ys_temp, color=COLOR_INFO, linewidth=2.0, alpha=0.9, linestyle="--")
            ax2.scatter([xs_temp[-1]], [ys_temp[-1]], color=COLOR_INFO, s=12, zorder=10)
            try:
                min_t = min(float(v) for v in ys_temp)
                max_t = max(float(v) for v in ys_temp)
                span = max_t - min_t
                pad = max(1.0, span * 0.15)
                ax2.set_ylim(min_t - pad, max_t + pad)
            except Exception:
                pass

        self.spark_ax.spines['top'].set_visible(False)
        self.spark_ax.spines['right'].set_visible(False)
        self.spark_ax.spines['left'].set_color(COLOR_BORDER)
        self.spark_ax.spines['bottom'].set_color(COLOR_BORDER)
        self.spark_ax.spines['left'].set_linewidth(0.5)
        self.spark_ax.spines['bottom'].set_linewidth(0.5)
        ax2.spines['top'].set_visible(False)
        ax2.spines['left'].set_visible(False)
        ax2.spines['right'].set_color(COLOR_BORDER)
        ax2.spines['bottom'].set_color(COLOR_BORDER)
        ax2.spines['right'].set_linewidth(0.5)
        ax2.spines['bottom'].set_linewidth(0.5)
        self.spark_ax.tick_params(axis='both', which='major', labelsize=8, colors=COLOR_SUBTEXT, length=2, width=0.5)
        ax2.tick_params(axis='y', which='major', labelsize=8, colors=COLOR_SUBTEXT, length=2, width=0.5)
        self.spark_ax.set_ylabel('kW', fontsize=8, color=COLOR_SUCCESS, rotation=0, labelpad=10, va='center')
        ax2.set_ylabel('°C', fontsize=8, color=COLOR_INFO, rotation=0, labelpad=10, va='center')
        self.spark_ax.yaxis.set_major_locator(plt.MaxNLocator(4))
        ax2.yaxis.set_major_locator(plt.MaxNLocator(4))
        self.spark_ax.xaxis.set_major_locator(plt.MaxNLocator(6))
        self.spark_ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))

        try:
            self.spark_ax.set_xlim(cutoff, now)
            ax2.set_xlim(cutoff, now)
        except Exception:
            pass
        self.spark_ax.margins(x=0.01)
        try:
            self.spark_fig.tight_layout(pad=0.3)
        except Exception as exc:
            print(f"[BUFFER] tight_layout warning: {exc}")
        try:
            self.spark_canvas.draw_idle()
            self.spark_canvas.draw()
        except Exception as exc:
            print(f"[BUFFER] Sparkline canvas draw error: {exc}")


    def _record_spark_sample(self, data: dict) -> None:
        now_ts = time.time()
        if now_ts - getattr(self, "_last_spark_sample_ts", 0.0) < 60.0:
            return
        self._last_spark_sample_ts = now_ts
        sample_time = datetime.now()
        pv_kw = self._safe_float(data.get(PV_POWER_KW) or data.get('pv'))
        if pv_kw is not None:
            self._spark_history_pv.append((sample_time, max(0.0, pv_kw)))
        outdoor = self._safe_float(data.get('outdoor'))
        if outdoor is not None:
            self._spark_history_temp.append((sample_time, outdoor))

    def _history_to_series(self, history: deque[tuple[datetime, float]], hours: int, bin_minutes: int) -> list[tuple[datetime, float]]:
        if not history:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        samples: list[tuple[datetime, float]] = []
        for ts, val in list(history):
            if val is None or ts < cutoff:
                continue
            rounded = ts.replace(second=0, microsecond=0) - timedelta(minutes=ts.minute % bin_minutes)
            samples.append((rounded, float(val)))
        if not samples:
            return []
        return self._aggregate_series(samples)


    def _build_stratified_data(self, top, mid, bot):
        """
        Returns a 3x1 numpy array for buffer visualization.
        Array order: [bot, mid, top] (bottom to top) to match origin="lower" in imshow.
        """
        arr = np.array([[bot], [mid], [top]], dtype=float)
        arr[np.isnan(arr)] = 0.0
        return arr

    def _create_sparkline(self):
        # Minimal placeholder to prevent crash; extend as needed
        # You can implement the actual sparkline drawing here
        pass

    def __init__(self, parent: tk.Widget, height: int = 280, datastore=None):
        super().__init__(parent, bg=COLOR_ROOT)
        self._start_time = time.time()
        self.height = height
        if datastore is not None:
            self.datastore = datastore
        else:
            self.datastore = get_shared_datastore()
        # Entfernt: configure(height) und pack_propagate(False) für flexibles Layout

        self.data = np.array([[60.0], [50.0], [40.0]])
        self._last_temps = None  # type: ignore
        self._last_spark_update = 0
        self._spark_history_pv = deque(maxlen=2000)
        self._spark_history_temp = deque(maxlen=2000)
        self._last_spark_sample_ts = 0.0
        self._spark_cache_pv = []
        self._spark_cache_temp = []
        self._spark_cache_ts = 0.0

        self.layout = tk.Frame(self, bg=COLOR_ROOT)
        self.layout.pack(fill=tk.BOTH, expand=True)
        self.layout.grid_columnconfigure(0, weight=1)
        self.layout.grid_rowconfigure(0, weight=1)
        self.layout.grid_rowconfigure(1, weight=0)

        self.plot_frame = tk.Frame(self.layout, bg=COLOR_ROOT)
        self.plot_frame.grid(row=0, column=0, sticky="nsew")

        self.val_texts = []

        self.spark_frame = tk.Frame(self.layout, bg=COLOR_ROOT)
        self.spark_frame.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        tk.Label(
            self.spark_frame,
            text="PV & Außentemp. (24h)",
            fg=COLOR_TITLE,
            bg=COLOR_ROOT,
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        # Reduzierte Figures für 60:40 Layout
        fig_width = 3.8  # kleiner für 40% Breite
        fig_height = 2.0
        self._create_figure(fig_width, fig_height)
        self._setup_plot()
        # --- Sparkline Figure (PV & Außentemp) - kompakter für 40% Breite ---
        self.spark_fig = Figure(figsize=(3.5, 1.2), dpi=100)
        self.spark_fig.patch.set_facecolor(COLOR_ROOT)  # Explizit setzen
        self.spark_ax = self.spark_fig.add_subplot(111)
        self.spark_ax.set_facecolor(COLOR_ROOT)  # Match card color
        self.spark_ax.patch.set_facecolor(COLOR_ROOT)  # Explizit setzen
        self.spark_canvas = FigureCanvasTkAgg(self.spark_fig, master=self.spark_frame)
        self.spark_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self.spark_ax.tick_params(axis='both', which='major', labelsize=9, colors=COLOR_SUBTEXT)
        self.spark_ax.set_axisbelow(True)
        self.spark_ax.grid(True, alpha=0.12)
        # Tight layout verhindert Clipping
        self.spark_fig.subplots_adjust(left=0.08, right=0.98, top=0.90, bottom=0.20)
        # ---
        self._create_sparkline()

    def resize(self, height: int) -> None:
        """Adjust container height without recreating heavy matplotlib assets."""
        elapsed = time.time() - self._start_time
        if DEBUG_LOG:
            print(f"[BUFFER] resize() at {elapsed:.3f}s -> {height}")
        self.height = max(160, int(height))
        # Entfernt: configure(height) für flexibles Layout

    def _create_figure(self, fig_width: float, fig_height: float) -> None:
        if hasattr(self, "canvas_widget") and self.canvas_widget.winfo_exists():
            self.canvas_widget.destroy()
        self.fig = Figure(figsize=(fig_width, fig_height), dpi=100)
        self.fig.patch.set_facecolor(COLOR_ROOT)  # Explizit auf COLOR_ROOT setzen
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(COLOR_ROOT)  # Konsistent mit Card-Hintergrund
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        # Flexible Skalierung ohne min_width Constraint für 50/50 Layout
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)

    def _setup_plot(self) -> None:
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()
        self.ax.set_facecolor(COLOR_ROOT)  # Konsistent mit Card-Hintergrund

        self.norm = Normalize(vmin=45, vmax=75)
        self.im = self.ax.imshow(
            self.data,
            aspect="auto",
            interpolation="gaussian",
            cmap=self._build_cmap(),
            norm=self.norm,
            origin="lower",
            extent=[0.05, 0.35, 0.08, 0.92],
        )

        puffer_cyl = FancyBboxPatch(
            (0.08, 0.08),
            0.24,
            0.84,
            boxstyle="round,pad=0.02,rounding_size=0.10",
            transform=self.ax.transAxes,
            linewidth=1.3,
            edgecolor=COLOR_ROOT,
            facecolor="none",
            alpha=0.75,
        )
        self.im.set_clip_path(puffer_cyl)
        self.ax.add_patch(puffer_cyl)
        self.ax.add_patch(Ellipse((0.20, 0.92), 0.24, 0.08, transform=self.ax.transAxes,
                                  edgecolor=COLOR_ROOT, facecolor="none", linewidth=1.0, alpha=0.7))
        self.ax.add_patch(Ellipse((0.20, 0.08), 0.24, 0.08, transform=self.ax.transAxes,
                                  edgecolor=COLOR_ROOT, facecolor="none", linewidth=1.0, alpha=0.7))
        # Entfernt: Helles weißes Overlay-Rectangle
        # Feste Schriftgröße und feste Ränder für optimalen Sitz
        self.fig.subplots_adjust(left=0.10, right=0.98, top=0.92, bottom=0.18)
        self.ax.text(0.20, 0.98, "Pufferspeicher", transform=self.ax.transAxes,
                 color=COLOR_TITLE, fontsize=13, va="top", ha="center", weight="bold")

        # Temperatur-Textfelder links
        self.val_texts = [
            self.ax.text(0.05, 0.85, "--°C", color="#FFFFFF", fontsize=11, va="center", ha="left", transform=self.ax.transAxes, weight="bold"),
            self.ax.text(0.05, 0.50, "--°C", color="#FFFFFF", fontsize=11, va="center", ha="left", transform=self.ax.transAxes, weight="bold"),
            self.ax.text(0.05, 0.15, "--°C", color="#FFFFFF", fontsize=11, va="center", ha="left", transform=self.ax.transAxes, weight="bold"),
        ]

        self.boiler_rect = FancyBboxPatch(
            (0.58, 0.08),
            0.22,
            0.45,
            boxstyle="round,pad=0.02,rounding_size=0.10",
            transform=self.ax.transAxes,
            linewidth=1.1,
            edgecolor=COLOR_ROOT,
            facecolor=self._temp_color(60),
            alpha=0.95,
        )
        self.ax.add_patch(self.boiler_rect)
        self.ax.add_patch(Ellipse((0.69, 0.53), 0.22, 0.08, transform=self.ax.transAxes,
                                  edgecolor=COLOR_ROOT, facecolor="none", linewidth=1.0, alpha=0.7))
        self.ax.add_patch(Ellipse((0.69, 0.08), 0.22, 0.08, transform=self.ax.transAxes,
                                  edgecolor=COLOR_ROOT, facecolor="none", linewidth=1.0, alpha=0.7))
        # Entfernt: Helles weißes Overlay-Rectangle
        self.ax.text(0.69, 0.60, "Boiler", transform=self.ax.transAxes,
                     color=COLOR_TITLE, fontsize=13, va="top", ha="center", weight="bold")
        # Boiler-Temperaturtext
        self.boiler_text = self.ax.text(0.69, 0.32, "--°C", color="#FFFFFF", fontsize=14, va="center", ha="center", transform=self.ax.transAxes, weight="bold")

        divider = make_axes_locatable(self.ax)
        cax = divider.append_axes("right", size="4%", pad=0.15)
        cbar = self.fig.colorbar(self.im, cax=cax, orientation="vertical")
        cbar.set_label("°C", rotation=0, labelpad=10, color=COLOR_TEXT, fontsize=9)
        cbar.ax.tick_params(labelsize=9, colors=COLOR_TEXT)
        cbar.outline.set_edgecolor(COLOR_BORDER)
        cbar.outline.set_linewidth(0.8)

    @staticmethod
    def _build_cmap() -> LinearSegmentedColormap:
        colors = [COLOR_INFO, COLOR_WARNING, COLOR_DANGER]
        return LinearSegmentedColormap.from_list("buffer", colors, N=256)

    def _temp_color(self, temp: float) -> str:
        rgba = self._build_cmap()(self.norm(temp))
        r, g, b = [int(255 * c) for c in rgba[:3]]
        return f"#{r:02x}{g:02x}{b:02x}"

    def _get_boiler_color(self, temp: float) -> str:
        return self._temp_color(temp)

    def update_data(self, data: dict):
        """Update für BufferStorageView: erwartet dict mit final keys."""
        import time
        top = float(data.get(BUF_TOP_C) or 0.0)
        mid = float(data.get(BUF_MID_C) or 0.0)
        bot = float(data.get(BUF_BOTTOM_C) or 0.0)
        # Boiler = Warmwasser. Prefer the final key BMK_WARMWASSER_C.
        # Fallback to BMK_BOILER_C for legacy callers.
        boiler = float((data.get(BMK_WARMWASSER_C) if BMK_WARMWASSER_C in data else data.get(BMK_BOILER_C)) or 0.0)
        now = time.time()
        if not hasattr(self, '_last_heat_dbg'):
            self._last_heat_dbg = 0.0
        if now - self._last_heat_dbg > 2.0:
            if DEBUG_LOG:
                print(f"[BUFFER_PARSED] top={top} mid={mid} bot={bot} boiler={boiler}", flush=True)
            self._last_heat_dbg = now
        self._record_spark_sample(data)
        self.update_temperatures(top, mid, bot, boiler)


    def update_temperatures(self, top, mid, bot, boiler):
        # Update heatmap with stratified 2D array
        self.data = self._build_stratified_data(top, mid, bot)
        
        # Dynamische Normalisierung basierend auf aktuellen Werten
        data_min = min(top, mid, bot)
        data_max = max(top, mid, bot)
        vmin = max(30, data_min - 5)  # Mindestens 30°C
        vmax = min(80, data_max + 5)  # Maximal 80°C
        
        if hasattr(self, 'im'):
            self.im.set_data(self.data)
            # Erneuere Colormap UND Normalisierung mit dynamischen Grenzen
            from matplotlib.colors import Normalize
            self.norm = Normalize(vmin=vmin, vmax=vmax)
            self.im.set_cmap(self._build_cmap())
            self.im.set_norm(self.norm)
        # Update left temperature texts
        if hasattr(self, 'val_texts') and len(self.val_texts) == 3:
            self.val_texts[0].set_text(f"{top:.1f}°C")
            self.val_texts[1].set_text(f"{mid:.1f}°C")
            self.val_texts[2].set_text(f"{bot:.1f}°C")
        if hasattr(self, 'boiler_text'):
            self.boiler_text.set_text(f"{boiler:.1f}°C")
        if hasattr(self, 'boiler_rect'):
            self.boiler_rect.set_facecolor(self._temp_color(boiler))
        # Redraw canvas only if widget exists
        if hasattr(self, 'canvas') and hasattr(self, 'canvas_widget') and self.canvas_widget.winfo_exists():
            try:
                self.canvas.draw_idle()
            except Exception:
                try:
                    self.canvas.draw()
                except Exception:
                    pass
        # Sparkline regelmäßig aktualisieren
        self._update_sparkline()

        try:
            self.spark_canvas.draw_idle()
        except Exception as exc:
            print(f"[BUFFER] Sparkline canvas draw error: {exc}")

    def _load_pv_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            if DEBUG_LOG:
                print("[DEBUG] Kein Datastore für PV-Serie!")
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        now = datetime.now()

        # DB timestamps may come in different string formats; don't rely on SQL text filtering/order.
        rows = self.datastore.get_recent_fronius(hours=None, limit=4000)
        if DEBUG_LOG:
            print(f"[DEBUG] get_recent_fronius liefert {len(rows)} Zeilen")
            if rows:
                print(f"[DEBUG] Beispiel-Eintrag Fronius: {rows[-1]}")
                print(f"[DEBUG] Alle Keys im letzten Eintrag: {list(rows[-1].keys())}")

        parsed_rows: list[tuple[datetime, dict]] = []
        for entry in rows:
            ts = self._parse_ts(entry.get('timestamp'))
            if ts is None:
                continue
            # Cap future timestamps (clock drift)
            if ts > now:
                ts = now
            parsed_rows.append((ts, entry))
        parsed_rows.sort(key=lambda t: t[0])

        samples: list[tuple[datetime, float]] = []
        for ts, entry in parsed_rows[-2500:]:
            pv_kw = self._safe_float(entry.get('pv'))
            if pv_kw is None or ts < cutoff:
                continue
            # Some historical sources (older CSV/API logs) may have stored PV power in W
            # while the dashboard expects kW everywhere. Heuristic: values above ~200 kW
            # are extremely unlikely for this setup -> treat as W and convert.
            if pv_kw > 200.0:
                pv_kw = pv_kw / 1000.0
            # PV should never be significantly negative; treat small noise as 0.
            if pv_kw < -0.2:
                continue
            pv_kw = max(0.0, pv_kw)
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, pv_kw))
        if DEBUG_LOG:
            print(f"[DEBUG] PV-Samples: {len(samples)}")
        return self._aggregate_series(samples)

    def _load_outdoor_temp_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            if DEBUG_LOG:
                print("[DEBUG] Kein Datastore für Außentemperatur!")
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        now = datetime.now()

        rows = self.datastore.get_recent_heating(hours=None, limit=4000)
        if DEBUG_LOG:
            print(f"[DEBUG] get_recent_heating liefert {len(rows)} Zeilen (outdoor)")
            if rows:
                print(f"[DEBUG] Beispiel-Eintrag (outdoor): {rows[0]}")
                print(f"[DEBUG] Keys im ersten Eintrag: {list(rows[0].keys())}")
            else:
                print("[DEBUG] Keine Daten von get_recent_heating (outdoor)")

        parsed_rows: list[tuple[datetime, dict]] = []
        for entry in rows:
            ts = self._parse_ts(entry.get('timestamp'))
            if ts is None:
                continue
            if ts > now:
                ts = now
            parsed_rows.append((ts, entry))
        parsed_rows.sort(key=lambda t: t[0])

        samples: list[tuple[datetime, float]] = []
        for ts, entry in parsed_rows[-2500:]:
            val = self._safe_float(entry.get('outdoor'))
            if val is None or ts < cutoff:
                continue
            # Plausibility clamp (sensor glitches)
            if not (-40.0 <= val <= 60.0):
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, val))
        if DEBUG_LOG:
            print(f"[DEBUG] Outdoor-Samples: {len(samples)}")
        return self._aggregate_series(samples)

    def _load_puffer_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            if DEBUG_LOG:
                print("[DEBUG] Kein Datastore für Puffer-Serie!")
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        now = datetime.now()

        rows = self.datastore.get_recent_heating(hours=None, limit=4000)
        if DEBUG_LOG:
            print(f"[DEBUG] get_recent_heating liefert {len(rows)} Zeilen (puffer)")
            if rows:
                print(f"[DEBUG] Beispiel-Eintrag (puffer): {rows[0]}")
                print(f"[DEBUG] Keys im ersten Eintrag: {list(rows[0].keys())}")
            else:
                print("[DEBUG] Keine Daten von get_recent_heating (puffer)")

        parsed_rows: list[tuple[datetime, dict]] = []
        for entry in rows:
            ts = self._parse_ts(entry.get('timestamp'))
            if ts is None:
                continue
            if ts > now:
                ts = now
            parsed_rows.append((ts, entry))
        parsed_rows.sort(key=lambda t: t[0])

        samples: list[tuple[datetime, float]] = []
        for ts, entry in parsed_rows[-2500:]:
            mid = self._safe_float(entry.get('mid'))
            if mid is None or ts < cutoff:
                continue
            if not (-40.0 <= mid <= 120.0):
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, mid))
        if DEBUG_LOG:
            print(f"[DEBUG] Puffer-Samples: {len(samples)}")
        aggregated = self._aggregate_series(samples)
        if len(aggregated) < 3:
            return aggregated
        smoothed: list[tuple[datetime, float]] = []
        for idx, current in enumerate(aggregated):
            window_vals = aggregated[max(0, idx - 1): min(len(aggregated), idx + 2)]
            smoothed.append((current[0], sum(val for _, val in window_vals) / len(window_vals)))
        return smoothed

    def _aggregate_series(self, samples: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
        if not samples:
            return []
        agg: dict[datetime, tuple[float, int]] = {}
        for ts, val in samples:
            total, count = agg.get(ts, (0.0, 0))
            agg[ts] = (total + val, count + 1)
        averaged = [(ts, total / count) for ts, (total, count) in sorted(agg.items())]
        return self._smooth_series(averaged, window=5)

    def _smooth_series(self, series: list[tuple[datetime, float]], window: int = 5) -> list[tuple[datetime, float]]:
        if len(series) < window:
            return series
        smoothed: list[tuple[datetime, float]] = []
        half_window = window // 2
        for idx in range(len(series)):
            start = max(0, idx - half_window)
            end = min(len(series), idx + half_window + 1)
            values = [val for _, val in series[start:end]]
            smoothed.append((series[idx][0], sum(values) / len(values)))
        return smoothed

    @staticmethod
    def _parse_ts(value):
        # Parse timestamps coming from different sources.
        # Some are naive ("YYYY-MM-DD HH:MM:SS"), some are offset-aware ("...+01:00").
        # For UI charting we normalize to *naive local time* to avoid TypeError
        # when comparing offset-aware vs. naive datetimes.
        from datetime import datetime
        if not value:
            return None
        try:
            s = str(value).strip()
            if s.endswith("Z"):
                s = s[:-1] + "+00:00"
            dt = datetime.fromisoformat(s)
            if getattr(dt, "tzinfo", None) is not None:
                dt = dt.astimezone().replace(tzinfo=None)
            return dt
        except Exception:
            return None

    @staticmethod
    def _safe_float(value):
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None

    def stop(self):
        """Cleanup resources to prevent memory leaks and segfaults."""
        try:
            import matplotlib.pyplot as plt
            if hasattr(self, 'fig') and self.fig:
                plt.close(self.fig)
                self.fig = None
            if hasattr(self, 'canvas_widget') and self.canvas_widget:
                self.canvas_widget.destroy()
            if hasattr(self, 'spark_fig') and self.spark_fig:
                plt.close(self.spark_fig)
                self.spark_fig = None
            if hasattr(self, 'spark_canvas') and self.spark_canvas:
                widget = self.spark_canvas.get_tk_widget()
                if widget:
                    widget.destroy()
        except Exception:
            pass
