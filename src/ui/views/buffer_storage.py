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

from core.datastore import get_shared_datastore
from ui.styles import (
    COLOR_CARD,
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


DEBUG_LOG = os.getenv("DASH_DEBUG", "0") == "1"


class BufferStorageView(tk.Frame):
    SPARK_UPDATE_INTERVAL = 300000  # 5 Minuten in ms
    """Heatmap-style buffer storage widget backed by SQLite data."""

    def __init__(self, parent: tk.Widget, height: int = 280):
        super().__init__(parent, bg=COLOR_CARD)
        self._start_time = time.time()
        self.spark_frame = tk.Frame(self, bg=COLOR_CARD)
        self.spark_frame.pack(fill=tk.X, expand=False)
        self._create_sparkline()
        self.after(self.SPARK_UPDATE_INTERVAL, self._schedule_sparkline_update)

    def _create_sparkline(self) -> None:
        self.spark_fig = Figure(figsize=(3.4, 0.9), dpi=100)
        self.spark_fig.patch.set_alpha(0)
        self.spark_ax = self.spark_fig.add_subplot(111)
        self.spark_ax.set_facecolor("none")
        self.spark_canvas = FigureCanvasTkAgg(self.spark_fig, master=self.spark_frame)
        self.spark_canvas.get_tk_widget().pack(fill=tk.X, expand=False)

    def _schedule_sparkline_update(self):
        self._update_sparkline()
        self.after(self.SPARK_UPDATE_INTERVAL, self._schedule_sparkline_update)

    def _update_sparkline(self) -> None:
        if (datetime.now().timestamp() - getattr(self, '_last_spark_update', 0)) < 30:
            return
        if not hasattr(self, "spark_canvas") or not self.spark_canvas.get_tk_widget().winfo_exists():
            return
        self._last_spark_update = datetime.now().timestamp()

        bin_minutes = 15
        now = datetime.now()
        x_max = self._ceil_to_bin(now, bin_minutes)
        x_min = x_max - timedelta(hours=24)
        steps = int((x_max - x_min).total_seconds() // (bin_minutes * 60))
        time_bins = [x_min + timedelta(minutes=bin_minutes * i) for i in range(steps + 1)]

        def fill_series(series):
            value_map = {dt: val for dt, val in series}
            return [value_map.get(b, np.nan) for b in time_bins]

        pv_series = self._load_pv_series(hours=24, bin_minutes=bin_minutes)
        temp_series = self._load_outdoor_temp_series(hours=24, bin_minutes=bin_minutes)

        # Debug-Ausgabe
        print("[SPARK] pv:", len(pv_series), "temp:", len(temp_series),
              "pv_last:", pv_series[-1] if pv_series else None,
              "temp_last:", temp_series[-1] if temp_series else None)

        pv_values = fill_series(pv_series)
        temp_values = fill_series(temp_series)

        self.spark_ax.clear()
        if hasattr(self, "spark_ax2"):
            try:
                self.spark_ax2.remove()
            except Exception:
                pass
        self.spark_ax2 = self.spark_ax.twinx()
        ax2 = self.spark_ax2

        # Visual separation for today/yesterday
        today = self._floor_to_bin(datetime.now(), bin_minutes)
        yesterday = today - timedelta(days=1)
        self.spark_ax.axvspan(x_min, today, color="#e0e0e0", alpha=0.08, zorder=0)
        self.spark_ax.axvspan(today, x_max, color="#ccefff", alpha=0.07, zorder=0)
        self.spark_ax.text(today + timedelta(hours=-12), self.spark_ax.get_ylim()[1], "Gestern", fontsize=8, color=COLOR_SUBTEXT, ha="center", va="bottom")
        self.spark_ax.text(today + timedelta(hours=12), self.spark_ax.get_ylim()[1], "Heute", fontsize=8, color=COLOR_SUBTEXT, ha="center", va="bottom")

        self.spark_ax.set_xlim(x_min, x_max)
        self.spark_ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        self.spark_ax.xaxis.set_major_locator(plt.MaxNLocator(6))

        self.spark_ax.plot(time_bins, pv_values, color=COLOR_SUCCESS, linewidth=2.0, alpha=0.9)
        self.spark_ax.fill_between(time_bins, pv_values, color=COLOR_SUCCESS, alpha=0.15)
        if not np.isnan(pv_values[-1]):
            self.spark_ax.scatter([time_bins[-1]], [pv_values[-1]], color=COLOR_SUCCESS, s=12, zorder=10)

        ax2.plot(time_bins, temp_values, color=COLOR_INFO, linewidth=2.0, alpha=0.9, linestyle="--")
        if not np.isnan(temp_values[-1]):
            ax2.scatter([time_bins[-1]], [temp_values[-1]], color=COLOR_INFO, s=12, zorder=10)

        # Styling wie gehabt
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
