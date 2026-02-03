import tkinter as tk
import os
from datetime import datetime, timedelta
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from mpl_toolkits.axes_grid1 import make_axes_locatable
from matplotlib.patches import FancyBboxPatch, Ellipse, Rectangle
from matplotlib.colors import LinearSegmentedColormap, Normalize
from ui.styles import (
from core.datastore import get_shared_datastore
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_TITLE,
    COLOR_WARNING,
    COLOR_INFO,
    COLOR_DANGER,
    COLOR_SUCCESS,
    COLOR_PRIMARY,
)


DEBUG_LOG = os.getenv("DASH_DEBUG", "0") == "1"

class BufferStorageView(tk.Frame):
    """Zylindrischer Pufferspeicher mit geclippter Heatmap + Sparkline."""

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
        self.layout.grid_columnconfigure(1, weight=0)
        self.layout.grid_rowconfigure(0, weight=1)
        self.layout.grid_rowconfigure(1, weight=0)

        self.plot_frame = tk.Frame(self.layout, bg=COLOR_CARD)
        self.plot_frame.grid(row=0, column=0, sticky="nsew")

        self.val_texts = []

        if not self.datastore:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = self.datastore.get_recent_fronius(hours=hours + 4, limit=2000)
        samples = []
        for entry in rows[-1000:]:
            ts = self._parse_ts(entry.get('timestamp'))
            pv_kw = entry.get('pv')
            if ts is None or pv_kw is None or ts < cutoff:
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes, seconds=ts.second, microseconds=ts.microsecond)
            samples.append((ts_bin, float(pv_kw)))
        if not samples:
            return []
        agg = {}
        for ts, val in samples:
            s, c = agg.get(ts, (0.0, 0))
            agg[ts] = (s + val, c + 1)
        out = [(ts, s / c) for ts, (s, c) in sorted(agg.items())]
        return self._smooth_series(out, window=5)
        #     self.canvas.draw_idle()

    def _create_figure(self, fig_width: float, fig_height: float):
        if not self.datastore:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = self.datastore.get_recent_heating(hours=hours + 4, limit=1600)
        samples = []
        for entry in rows[-800:]:
            ts = self._parse_ts(entry.get('timestamp'))
            val = self._safe_float(entry.get('outdoor'))
            if ts is None or val is None or ts < cutoff:
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes, seconds=ts.second, microseconds=ts.microsecond)
            samples.append((ts_bin, val))
        if not samples:
            return []
        agg = {}
        for ts, val in samples:
            s, c = agg.get(ts, (0.0, 0))
            agg[ts] = (s + val, c + 1)
        out = [(ts, s / c) for ts, (s, c) in sorted(agg.items())]
        return self._smooth_series(out, window=5)
        puffer_top = Ellipse((0.20, 0.92), 0.24, 0.08, transform=self.ax.transAxes,
                  edgecolor="#2A3446", facecolor="none", linewidth=1.0, alpha=0.7)
        if not self.datastore:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = self.datastore.get_recent_heating(hours=hours + 4, limit=1600)
        samples = []
        for entry in rows[-800:]:
            ts = self._parse_ts(entry.get('timestamp'))
            mid = self._safe_float(entry.get('mid'))
            if ts is None or mid is None or ts < cutoff:
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes, seconds=ts.second, microseconds=ts.microsecond)
            samples.append((ts_bin, mid))
        if not samples:
            return []
        agg = {}
        for ts, val in samples:
            s, c = agg.get(ts, (0.0, 0))
            agg[ts] = (s + val, c + 1)
        out = [(ts, s / c) for ts, (s, c) in sorted(agg.items())]
        smoothed = []
        for i in range(len(out)):
            w = out[max(0, i-1):min(len(out), i+2)]
            smoothed.append((out[i][0], sum(v for _, v in w) / len(w)))
        return smoothed
        return LinearSegmentedColormap.from_list("buffer", colors, N=256)
    
    def _temp_color(self, temp: float) -> str:
        rgba = self._build_cmap()(self.norm(temp))
        r, g, b = [int(255 * c) for c in rgba[:3]]
        return f"#{r:02x}{g:02x}{b:02x}"

    def update_temperatures(self, top: float, mid: float, bottom: float, kessel_c: float | None = None):
        """Update temperature display - uses draw_idle() to avoid blocking."""
        if not self.winfo_exists():
            return
        if not hasattr(self, "canvas_widget") or not self.canvas_widget.winfo_exists():
            return

        temps = (top, mid, bottom)
        if self._last_temps == temps:
            return
        
        elapsed = time.time() - self._start_time
        if DEBUG_LOG:
            print(f"[BUFFER] update_temperatures() at {elapsed:.3f}s: {top:.1f}/{mid:.1f}/{bottom:.1f}")
        
        self._last_temps = temps
        vmin = min(temps) - 3
        vmax = max(temps) + 3
        self.norm = Normalize(vmin=vmin, vmax=vmax)

        self.data = self._build_stratified_data(top, mid, bottom)
        self.im.set_data(self.data)
        self.im.set_norm(self.norm)

        c_top = self._temp_color(top)
        c_mid = self._temp_color(mid)
        c_bot = self._temp_color(bottom)

        for t in self.val_texts:
            t.remove()
        self.val_texts = [
            self.ax.text(0.04, 0.85, f"{top:.1f}°C", transform=self.ax.transAxes, color=c_top, fontsize=9, va="center", ha="right", weight="bold"),
            self.ax.text(0.04, 0.50, f"{mid:.1f}°C", transform=self.ax.transAxes, color=c_mid, fontsize=10, va="center", ha="right", weight="bold"),
            self.ax.text(0.04, 0.15, f"{bottom:.1f}°C", transform=self.ax.transAxes, color=c_bot, fontsize=9, va="center", ha="right", weight="bold"),
        ]

        if kessel_c is not None:
            c_kessel = self._temp_color(kessel_c)
            self.boiler_rect.set_facecolor(c_kessel)
            # Add boiler temperature text - smaller and without outline
            if hasattr(self, 'boiler_temp_text'):
                self.boiler_temp_text.remove()
            if hasattr(self, 'boiler_temp_outline'):
                for outline_text in self.boiler_temp_outline:
                    outline_text.remove()
            self.boiler_temp_text = self.ax.text(0.69, 0.30, f"{kessel_c:.1f}°C", 
                transform=self.ax.transAxes, color="#FFFFFF", fontsize=8, 
                va="center", ha="center", weight="bold", zorder=100)
        
        self._update_sparkline()
        
        # Use draw_idle() to defer redraw and avoid blocking
        # Note: This will redraw all static elements too (titles), causing flicker
        # But it's necessary for matplotlib updates. The flicker is minimized by
        # only updating when temperatures actually change (see check at top)
        if DEBUG_LOG:
            print(f"[BUFFER] Calling canvas.draw_idle() at {time.time() - self._start_time:.3f}s")
        
        # Redraw safely if widget still exists
        try:
            if self.canvas_widget.winfo_exists():
                self.canvas.draw_idle()
        except Exception as e:
            print(f"[BUFFER] Canvas draw error: {e}")

    def _build_stratified_data(self, top: float, mid: float, bottom: float) -> np.ndarray:
        # Build smooth vertical stratification (bottom->mid->top)
        h = 120
        y = np.linspace(0, 1, h)
        vals = np.zeros_like(y)
        # Bottom zone (0-0.33), Mid zone (0.33-0.66), Top zone (0.66-1)
        for i, t in enumerate(y):
            if t < 0.33:
                vals[i] = bottom + (mid - bottom) * (t / 0.33)
            elif t < 0.66:
                vals[i] = mid + (top - mid) * ((t - 0.33) / 0.33)
            else:
                vals[i] = top
        return vals.reshape(h, 1)

    def _create_sparkline(self):
        self.spark_fig = Figure(figsize=(3.4, 0.9), dpi=100)  # Pi 5: Stable DPI
        self.spark_fig.patch.set_alpha(0)
        self.spark_ax = self.spark_fig.add_subplot(111)
        self.spark_ax.set_facecolor("none")
        self.spark_canvas = FigureCanvasTkAgg(self.spark_fig, master=self.spark_frame)
        self.spark_canvas.get_tk_widget().pack(fill=tk.X, expand=False)

    def _update_sparkline(self):
        if (datetime.now().timestamp() - self._last_spark_update) < 30:  # Pi 5: Update every 30s
            return
        if not hasattr(self, "spark_canvas"):
            return
        if not self.spark_canvas.get_tk_widget().winfo_exists():
            return
        self._last_spark_update = datetime.now().timestamp()
        
        pv_series = self._load_pv_series(hours=24, bin_minutes=15)
        temp_series = self._load_outdoor_temp_series(hours=24, bin_minutes=15)
        
        self.spark_ax.clear()
        
        # Create second y-axis for temperature
        if hasattr(self, "spark_ax2"):
            try:
                self.spark_ax2.remove()
            except Exception:
                pass
        self.spark_ax2 = self.spark_ax.twinx()
        ax2 = self.spark_ax2
        
        # Plot PV production (left axis) - yellow/green
        if pv_series:
            xs_pv, ys_pv = zip(*pv_series)
            self.spark_ax.plot(xs_pv, ys_pv, color=COLOR_SUCCESS, linewidth=2.0, alpha=0.9, label="PV")
            self.spark_ax.fill_between(xs_pv, ys_pv, color=COLOR_SUCCESS, alpha=0.15)
            self.spark_ax.scatter([xs_pv[-1]], [ys_pv[-1]], color=COLOR_SUCCESS, s=12, zorder=10)
        
        # Plot outdoor temperature (right axis) - blue
        if temp_series:
            xs_temp, ys_temp = zip(*temp_series)
            ax2.plot(xs_temp, ys_temp, color=COLOR_INFO, linewidth=2.0, alpha=0.9, label="Temp", linestyle="--")
            ax2.scatter([xs_temp[-1]], [ys_temp[-1]], color=COLOR_INFO, s=12, zorder=10)
        
        # Subtle axis styling
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
        
        # Light tick styling
        self.spark_ax.tick_params(axis='both', which='major', labelsize=7, colors=COLOR_SUBTEXT, length=2, width=0.5)
        ax2.tick_params(axis='y', which='major', labelsize=7, colors=COLOR_SUBTEXT, length=2, width=0.5)
        
        # Y-axis labels with units
        self.spark_ax.set_ylabel('kW', fontsize=7, color=COLOR_SUCCESS, rotation=0, labelpad=10, va='center')
        ax2.set_ylabel('°C', fontsize=7, color=COLOR_INFO, rotation=0, labelpad=10, va='center')
        
        # Limit number of ticks
        self.spark_ax.yaxis.set_major_locator(plt.MaxNLocator(4))
        ax2.yaxis.set_major_locator(plt.MaxNLocator(4))
        self.spark_ax.xaxis.set_major_locator(plt.MaxNLocator(6))
        
        # Format x-axis to show hours
        import matplotlib.dates as mdates
        self.spark_ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))
        
        # Ensure labels are visible within figure bounds
        try:
            self.spark_fig.tight_layout(pad=0.3)
        except Exception as e:
            print(f"[BUFFER] tight_layout warning: {e}")
        
        try:
            self.spark_canvas.draw_idle()
        except Exception as e:
            print(f"[BUFFER] Sparkline canvas draw error: {e}")

    def _load_pv_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        """Load PV production with smoothing."""
        if not self.datastore:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = self.datastore.get_recent_fronius(hours=hours + 4, limit=2000)
        samples = []
        for entry in rows[-1000:]:
            ts = self._parse_ts(entry.get('timestamp'))
            pv_kw = entry.get('pv')
            if ts is None or pv_kw is None or ts < cutoff:
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes, seconds=ts.second, microseconds=ts.microsecond)
            samples.append((ts_bin, float(pv_kw)))
        if not samples:
            return []
        agg = {}
        for ts, val in samples:
            s, c = agg.get(ts, (0.0, 0))
            agg[ts] = (s + val, c + 1)
        out = [(ts, s / c) for ts, (s, c) in sorted(agg.items())]
        return self._smooth_series(out, window=5)
    
    def _load_outdoor_temp_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        """Load outdoor temperature with smoothing."""
        if not self.datastore:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = self.datastore.get_recent_heating(hours=hours + 4, limit=1600)
        samples = []
        for entry in rows[-800:]:
            ts = self._parse_ts(entry.get('timestamp'))
            val = self._safe_float(entry.get('outdoor'))
            if ts is None or val is None or ts < cutoff:
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes, seconds=ts.second, microseconds=ts.microsecond)
            samples.append((ts_bin, val))
        if not samples:
            return []
        agg = {}
        for ts, val in samples:
            s, c = agg.get(ts, (0.0, 0))
            agg[ts] = (s + val, c + 1)
        out = [(ts, s / c) for ts, (s, c) in sorted(agg.items())]
        return self._smooth_series(out, window=5)
    
    def _smooth_series(self, series: list[tuple[datetime, float]], window: int = 5) -> list[tuple[datetime, float]]:
        """Apply moving average smoothing to series."""
        if len(series) < window:
            return series
        smoothed = []
        half_window = window // 2
        for i in range(len(series)):
            start = max(0, i - half_window)
            end = min(len(series), i + half_window + 1)
            window_values = [val for _, val in series[start:end]]
            smoothed_val = sum(window_values) / len(window_values)
            smoothed.append((series[i][0], smoothed_val))
        return smoothed

    def _load_puffer_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        rows = self.datastore.get_recent_heating(hours=hours + 4, limit=1600)
        samples = []
        for entry in rows[-800:]:
            ts = self._parse_ts(entry.get('timestamp'))
            mid = self._safe_float(entry.get('mid'))
            if ts is None or mid is None or ts < cutoff:
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes, seconds=ts.second, microseconds=ts.microsecond)
            samples.append((ts_bin, mid))
        if not samples:
            return []
        agg = {}
        for ts, val in samples:
            s, c = agg.get(ts, (0.0, 0))
            agg[ts] = (s + val, c + 1)
        out = [(ts, s / c) for ts, (s, c) in sorted(agg.items())]
        smoothed = []
        for i in range(len(out)):
            window_vals = out[max(0, i - 1):min(len(out), i + 2)]
            smoothed.append((out[i][0], sum(v for _, v in window_vals) / len(window_vals)))
        return smoothed

    @staticmethod
    def _parse_ts(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None
