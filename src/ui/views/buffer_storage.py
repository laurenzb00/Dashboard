import os
import time
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

class BufferStorageView(tk.Frame):
    """Heatmap-style buffer storage widget backed by SQLite data."""

    def __init__(self, parent: tk.Widget, height: int = 280):
        super().__init__(parent, bg=COLOR_CARD)
        self._start_time = time.time()
        self.height = height
        self.datastore = get_shared_datastore()
        self.configure(height=self.height)
        self.pack_propagate(False)

        self.data = np.array([[60.0], [50.0], [40.0]])
        self._last_temps: tuple[float, float, float] | None = None
        self._last_spark_update = 0

        self.layout = tk.Frame(self, bg=COLOR_CARD)
        self.layout.pack(fill=tk.BOTH, expand=True)
        self.layout.grid_columnconfigure(0, weight=1)
        self.layout.grid_rowconfigure(0, weight=1)
        self.layout.grid_rowconfigure(1, weight=0)

        self.plot_frame = tk.Frame(self.layout, bg=COLOR_CARD)
        self.plot_frame.grid(row=0, column=0, sticky="nsew")

        self.val_texts: list = []

        self.spark_frame = tk.Frame(self.layout, bg=COLOR_CARD)
        self.spark_frame.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        tk.Label(
            self.spark_frame,
            text="PV & Außentemp. (24h)",
            fg=COLOR_TITLE,
            bg=COLOR_CARD,
            font=("Segoe UI", 10),
        ).pack(anchor="w")

        # Fester Platz für das Diagramm, da Bildschirmgröße bekannt ist
        fig_width = 9.0  # optimal für ca. 1200px Breite
        fig_height = 4.5 # optimal für ca. 600px Höhe
        self._create_figure(fig_width, fig_height)
        self._setup_plot()
        self._create_sparkline()

    def resize(self, height: int) -> None:
        """Adjust container height without recreating heavy matplotlib assets."""
        elapsed = time.time() - self._start_time
        if DEBUG_LOG:
            print(f"[BUFFER] resize() at {elapsed:.3f}s -> {height}")
        self.height = max(160, int(height))
        self.configure(height=self.height)

    def _create_figure(self, fig_width: float, fig_height: float) -> None:
        if hasattr(self, "canvas_widget") and self.canvas_widget.winfo_exists():
            self.canvas_widget.destroy()
        self.fig = Figure(figsize=(fig_width, fig_height), dpi=100)
        self.fig.patch.set_alpha(0)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("none")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        # Enforce minimum figure size for scaling
        min_width, min_height = 320, 180
        self.canvas_widget.configure(width=max(int(fig_width * 100), min_width), height=max(int(fig_height * 100), min_height))
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)

    def _setup_plot(self) -> None:
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()
        self.ax.set_facecolor("none")

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
            edgecolor="#2A3446",
            facecolor="none",
            alpha=0.75,
        )
        self.im.set_clip_path(puffer_cyl)
        self.ax.add_patch(puffer_cyl)
        self.ax.add_patch(Ellipse((0.20, 0.92), 0.24, 0.08, transform=self.ax.transAxes,
                                  edgecolor="#2A3446", facecolor="none", linewidth=1.0, alpha=0.7))
        self.ax.add_patch(Ellipse((0.20, 0.08), 0.24, 0.08, transform=self.ax.transAxes,
                                  edgecolor="#2A3446", facecolor="none", linewidth=1.0, alpha=0.7))
        self.ax.add_patch(Rectangle((0.10, 0.10), 0.03, 0.80, transform=self.ax.transAxes,
                                    facecolor="#ffffff", alpha=0.07, linewidth=0))
        # Feste Schriftgröße und feste Ränder für optimalen Sitz
        self.fig.subplots_adjust(left=0.10, right=0.98, top=0.92, bottom=0.18)
        self.ax.text(0.20, 0.98, "Pufferspeicher", transform=self.ax.transAxes,
                 color=COLOR_TITLE, fontsize=13, va="top", ha="center", weight="bold")

        self.boiler_rect = FancyBboxPatch(
            (0.58, 0.08),
            0.22,
            0.45,
            boxstyle="round,pad=0.02,rounding_size=0.10",
            transform=self.ax.transAxes,
            linewidth=1.1,
            edgecolor="#2A3446",
            facecolor=self._temp_color(60),
            alpha=0.95,
        )
        self.ax.add_patch(self.boiler_rect)
        self.ax.add_patch(Ellipse((0.69, 0.53), 0.22, 0.08, transform=self.ax.transAxes,
                                  edgecolor="#2A3446", facecolor="none", linewidth=1.0, alpha=0.7))
        self.ax.add_patch(Ellipse((0.69, 0.08), 0.22, 0.08, transform=self.ax.transAxes,
                                  edgecolor="#2A3446", facecolor="none", linewidth=1.0, alpha=0.7))
        self.ax.add_patch(Rectangle((0.60, 0.10), 0.03, 0.41, transform=self.ax.transAxes,
                                    facecolor="#ffffff", alpha=0.06, linewidth=0))
        self.ax.text(0.69, 0.60, "Boiler", transform=self.ax.transAxes,
                     color=COLOR_TITLE, fontsize=13, va="top", ha="center", weight="bold")
        # Keine dynamische Rotation oder tight_layout nötig bei festen Größen

        divider = make_axes_locatable(self.ax)
        cax = divider.append_axes("right", size="4%", pad=0.15)
        cbar = self.fig.colorbar(self.im, cax=cax, orientation="vertical")
        cbar.set_label("°C", rotation=0, labelpad=10, color=COLOR_TEXT, fontsize=9)
        cbar.ax.tick_params(labelsize=8, colors=COLOR_TEXT)
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

    def update_temperatures(
        self,
        top: float,
        mid: float,
        bottom: float,
        kessel_c: float | None = None,
    ) -> None:
        if not self.winfo_exists() or not hasattr(self, "canvas_widget"):
            return
        if not self.canvas_widget.winfo_exists():
            return

        temps = (top, mid, bottom)
        if self._last_temps == temps:
            return

        self._last_temps = temps
        self.norm = Normalize(vmin=min(temps) - 3, vmax=max(temps) + 3)
        self.data = self._build_stratified_data(top, mid, bottom)
        self.im.set_data(self.data)
        self.im.set_norm(self.norm)

        c_top = self._temp_color(top)
        c_mid = self._temp_color(mid)
        c_bot = self._temp_color(bottom)

        for text in self.val_texts:
            text.remove()
        self.val_texts = [
            self.ax.text(0.04, 0.85, f"{top:.1f}°C", transform=self.ax.transAxes, color=c_top,
                         fontsize=9, va="center", ha="right", weight="bold"),
            self.ax.text(0.04, 0.50, f"{mid:.1f}°C", transform=self.ax.transAxes, color=c_mid,
                         fontsize=10, va="center", ha="right", weight="bold"),
            self.ax.text(0.04, 0.15, f"{bottom:.1f}°C", transform=self.ax.transAxes, color=c_bot,
                         fontsize=9, va="center", ha="right", weight="bold"),
        ]

        if kessel_c is not None:
            self.boiler_rect.set_facecolor(self._temp_color(kessel_c))
            if hasattr(self, "boiler_temp_text"):
                self.boiler_temp_text.remove()
            self.boiler_temp_text = self.ax.text(
                0.69,
                0.30,
                f"{kessel_c:.1f}°C",
                transform=self.ax.transAxes,
                color="#FFFFFF",
                fontsize=8,
                va="center",
                ha="center",
                weight="bold",
                zorder=100,
            )

        self._update_sparkline()
        try:
            self.canvas.draw_idle()
        except Exception as exc:
            print(f"[BUFFER] Canvas draw error: {exc}")

    def _build_stratified_data(self, top: float, mid: float, bottom: float) -> np.ndarray:
        h = 120
        y = np.linspace(0, 1, h)
        vals = np.zeros_like(y)
        for idx, pos in enumerate(y):
            if pos < 0.33:
                vals[idx] = bottom + (mid - bottom) * (pos / 0.33)
            elif pos < 0.66:
                vals[idx] = mid + (top - mid) * ((pos - 0.33) / 0.33)
            else:
                vals[idx] = top
        return vals.reshape(h, 1)

    def _create_sparkline(self) -> None:
        self.spark_fig = Figure(figsize=(3.4, 0.9), dpi=100)
        self.spark_fig.patch.set_alpha(0)
        self.spark_ax = self.spark_fig.add_subplot(111)
        self.spark_ax.set_facecolor("none")
        self.spark_canvas = FigureCanvasTkAgg(self.spark_fig, master=self.spark_frame)
        self.spark_canvas.get_tk_widget().pack(fill=tk.X, expand=False)

    def _update_sparkline(self) -> None:
        if (datetime.now().timestamp() - self._last_spark_update) < 30:
            return
        if not hasattr(self, "spark_canvas") or not self.spark_canvas.get_tk_widget().winfo_exists():
            return
        self._last_spark_update = datetime.now().timestamp()

        pv_series = self._load_pv_series(hours=24, bin_minutes=15)
        temp_series = self._load_outdoor_temp_series(hours=24, bin_minutes=15)

        self.spark_ax.clear()
        if hasattr(self, "spark_ax2"):
            try:
                self.spark_ax2.remove()
            except Exception:
                pass
        self.spark_ax2 = self.spark_ax.twinx()
        ax2 = self.spark_ax2

        if pv_series:
            xs_pv, ys_pv = zip(*pv_series)
            self.spark_ax.plot(xs_pv, ys_pv, color=COLOR_SUCCESS, linewidth=2.0, alpha=0.9)
            self.spark_ax.fill_between(xs_pv, ys_pv, color=COLOR_SUCCESS, alpha=0.15)
            self.spark_ax.scatter([xs_pv[-1]], [ys_pv[-1]], color=COLOR_SUCCESS, s=12, zorder=10)

        if temp_series:
            xs_temp, ys_temp = zip(*temp_series)
            ax2.plot(xs_temp, ys_temp, color=COLOR_INFO, linewidth=2.0, alpha=0.9, linestyle="--")
            ax2.scatter([xs_temp[-1]], [ys_temp[-1]], color=COLOR_INFO, s=12, zorder=10)

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

        self.spark_ax.tick_params(axis='both', which='major', labelsize=7, colors=COLOR_SUBTEXT, length=2, width=0.5)
        ax2.tick_params(axis='y', which='major', labelsize=7, colors=COLOR_SUBTEXT, length=2, width=0.5)

        self.spark_ax.set_ylabel('kW', fontsize=7, color=COLOR_SUCCESS, rotation=0, labelpad=10, va='center')
        ax2.set_ylabel('°C', fontsize=7, color=COLOR_INFO, rotation=0, labelpad=10, va='center')

        self.spark_ax.yaxis.set_major_locator(plt.MaxNLocator(4))
        ax2.yaxis.set_major_locator(plt.MaxNLocator(4))
        self.spark_ax.xaxis.set_major_locator(plt.MaxNLocator(6))
        self.spark_ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))

        try:
            self.spark_fig.tight_layout(pad=0.3)
        except Exception as exc:
            print(f"[BUFFER] tight_layout warning: {exc}")

        try:
            self.spark_canvas.draw_idle()
        except Exception as exc:
            print(f"[BUFFER] Sparkline canvas draw error: {exc}")

        self.spark_ax.set_ylabel('kW', fontsize=7, color=COLOR_SUCCESS, rotation=0, labelpad=10, va='center')
        ax2.set_ylabel('°C', fontsize=7, color=COLOR_INFO, rotation=0, labelpad=10, va='center')

        self.spark_ax.yaxis.set_major_locator(plt.MaxNLocator(4))
        ax2.yaxis.set_major_locator(plt.MaxNLocator(4))
        self.spark_ax.xaxis.set_major_locator(plt.MaxNLocator(6))
        self.spark_ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))

        try:
            self.spark_fig.tight_layout(pad=0.3)
        except Exception as exc:
            print(f"[BUFFER] tight_layout warning: {exc}")

        try:
            self.spark_canvas.draw_idle()
        except Exception as exc:
            print(f"[BUFFER] Sparkline canvas draw error: {exc}")

    def _load_pv_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            if DEBUG_LOG:
                print("[SPARKLINE] Kein Datastore verfügbar!")
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = self.datastore.get_recent_fronius(hours=hours + 4, limit=2000)
        if DEBUG_LOG:
            print(f"[SPARKLINE] get_recent_fronius liefert {len(rows)} Zeilen")
        samples: list[tuple[datetime, float]] = []
        for entry in rows[-1000:]:
            ts = self._parse_ts(entry.get('timestamp'))
            pv_kw = self._safe_float(entry.get('pv'))
            if ts is None or pv_kw is None or ts < cutoff:
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, pv_kw))
        if DEBUG_LOG:
            print(f"[SPARKLINE] PV-Samples: {samples[:5]} ... (insg. {len(samples)})")
        return self._aggregate_series(samples)

    def _load_outdoor_temp_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            if DEBUG_LOG:
                print("[SPARKLINE] Kein Datastore verfügbar (outdoor)!")
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = self.datastore.get_recent_heating(hours=hours + 4, limit=1600)
        if DEBUG_LOG:
            print(f"[SPARKLINE] get_recent_heating liefert {len(rows)} Zeilen")
        samples: list[tuple[datetime, float]] = []
        for entry in rows[-800:]:
            ts = self._parse_ts(entry.get('timestamp'))
            # Fallback: falls 'outdoor' nicht im Dict, versuche 'außentemp'
            val = self._safe_float(entry.get('outdoor'))
            if val is None and 'außentemp' in entry:
                val = self._safe_float(entry.get('außentemp'))
            if ts is None or val is None or ts < cutoff:
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, val))
        if DEBUG_LOG:
            print(f"[SPARKLINE] Outdoor-Samples: {samples[:5]} ... (insg. {len(samples)})")
        return self._aggregate_series(samples)

    def _load_puffer_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = self.datastore.get_recent_heating(hours=hours + 4, limit=1600)
        samples: list[tuple[datetime, float]] = []
        for entry in rows[-800:]:
            ts = self._parse_ts(entry.get('timestamp'))
            mid = self._safe_float(entry.get('mid'))
            if ts is None or mid is None or ts < cutoff:
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, mid))
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
        if not value:
            return None
        s = str(value).strip().replace('T', ' ', 1)
        # Remove milliseconds if present
        if '.' in s:
            s = s.split('.')[0]
        # Remove timezone if present
        if '+' in s:
            s = s.split('+')[0]
        if 'Z' in s:
            s = s.replace('Z', '')
        try:
            return datetime.fromisoformat(s)
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

    def update_temperatures(self, temp_top, temp_mid, temp_bot, temp_warmwasser=None):
        """Aktualisiert die Visualisierung mit neuen Temperaturen."""
        try:
            t_top = float(temp_top)
            t_mid = float(temp_mid)
            t_bot = float(temp_bot)
            t_warmwasser = float(temp_warmwasser) if temp_warmwasser is not None else None
        except (ValueError, TypeError):
            t_top, t_mid, t_bot = 0, 0, 0
            t_warmwasser = None
        # Beispiel: Heatmap/Gradient/Block-Update
        # Hier könntest du weitere Visualisierungen einbauen
        # Für die Sparkline gibt es keine Temperaturanzeige, aber ggf. für andere Widgets
        # Placeholder: Du kannst hier z.B. Labels setzen oder eine eigene Methode aufrufen
        # print(f"[BUFFER] update_temperatures: top={t_top}, mid={t_mid}, bot={t_bot}, warmwasser={t_warmwasser}")
        pass
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
