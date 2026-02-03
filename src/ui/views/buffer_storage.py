import tkinter as tk
import os
import csv
import time
from datetime import datetime, timedelta
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from mpl_toolkits.axes_grid1 import make_axes_locatable
from matplotlib.patches import FancyBboxPatch, Ellipse, Rectangle
from matplotlib.colors import LinearSegmentedColormap, Normalize
from ui.styles import (
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


def _normalize_header(name: str) -> str:
    return (
        name.strip()
        .lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace(" ", "")
        .replace("_", "")
    )

class BufferStorageView(tk.Frame):
    """Zylindrischer Pufferspeicher mit geclippter Heatmap + Sparkline."""

    def __init__(self, parent: tk.Widget, height: int = 280):
        super().__init__(parent, bg=COLOR_CARD)
        self._start_time = time.time()
        self.height = height
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

        self.spark_frame = tk.Frame(self.layout, bg=COLOR_CARD)
        self.spark_frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        tk.Label(self.spark_frame, text="PV & Außentemp. (24h)", fg=COLOR_TITLE, bg=COLOR_CARD, font=("Segoe UI", 10)).pack(anchor="w")

        fig_width = 3.2
        fig_height = max(1.8, self.height / 100)
        self._create_figure(fig_width, fig_height)
        self._setup_plot()
        self._create_sparkline()

    def resize(self, height: int):
        """FIXED: Don't recreate figure on resize - just update container height."""
        elapsed = time.time() - self._start_time
        if DEBUG_LOG:
            print(f"[BUFFER] resize() called at {elapsed:.3f}s with height={height}")
        
        old_height = self.height
        self.height = max(160, int(height))
        
        # Only reconfigure container height, don't recreate matplotlib figure
        self.configure(height=self.height)
        # Log but DON'T recreate the entire plot
        if DEBUG_LOG:
            print(f"[BUFFER] Height changed from {old_height} to {self.height}, NOT recreating figure")
        
        # If you MUST resize the figure (e.g., very large change), do it sparingly:
        # if abs(self.height - old_height) > 50:
        #     print(f"[BUFFER] Large height change, recreating figure")
        #     fig_width = 2.6
        #     fig_height = max(1.6, self.height / 100)
        #     self._create_figure(fig_width, fig_height)
        #     self._setup_plot()
        #     self.canvas.draw_idle()

    def _create_figure(self, fig_width: float, fig_height: float):
        """Create matplotlib figure and canvas - only called once at init."""
        elapsed = time.time() - self._start_time
        if DEBUG_LOG:
            print(f"[BUFFER] _create_figure() at {elapsed:.3f}s: {fig_width}x{fig_height}")
        
        if hasattr(self, "canvas_widget") and self.canvas_widget.winfo_exists():
            if DEBUG_LOG:
                print(f"[BUFFER] WARNING: Destroying existing canvas at {elapsed:.3f}s")
            self.canvas_widget.destroy()
        self.fig = Figure(figsize=(fig_width, fig_height), dpi=100)  # Pi 5: Stable DPI
        self.fig.patch.set_alpha(0)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("none")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.configure(width=int(fig_width * 100), height=int(fig_height * 100))
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)

    def _setup_plot(self):
        """Setup plot elements - only called once at init or after explicit recreate."""
        elapsed = time.time() - self._start_time
        if DEBUG_LOG:
            print(f"[BUFFER] _setup_plot() at {elapsed:.3f}s")
        
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()
        self.ax.set_facecolor("none")

        self.norm = Normalize(vmin=45, vmax=75)
        
        # Pufferspeicher heatmap (left cylinder)
        self.im = self.ax.imshow(
            self.data,
            aspect="auto",
            interpolation="gaussian",
            cmap=self._build_cmap(),
            norm=self.norm,
            origin="lower",
            extent=[0.05, 0.35, 0.08, 0.92],
        )

        # Pufferspeicher cylinder shape
        puffer_cyl = FancyBboxPatch(
            (0.08, 0.08), 0.24, 0.84,
            boxstyle="round,pad=0.02,rounding_size=0.10",
            transform=self.ax.transAxes,
            linewidth=1.3,
            edgecolor="#2A3446",
            facecolor="none",
            alpha=0.75,
        )
        self.im.set_clip_path(puffer_cyl)
        self.ax.add_patch(puffer_cyl)

        puffer_top = Ellipse((0.20, 0.92), 0.24, 0.08, transform=self.ax.transAxes,
                  edgecolor="#2A3446", facecolor="none", linewidth=1.0, alpha=0.7)
        puffer_bottom = Ellipse((0.20, 0.08), 0.24, 0.08, transform=self.ax.transAxes,
                     edgecolor="#2A3446", facecolor="none", linewidth=1.0, alpha=0.7)
        self.ax.add_patch(puffer_top)
        self.ax.add_patch(puffer_bottom)

        # Highlight on Pufferspeicher
        hl = Rectangle((0.10, 0.10), 0.03, 0.80, transform=self.ax.transAxes,
                   facecolor="#ffffff", alpha=0.07, linewidth=0)
        self.ax.add_patch(hl)

        # Labels for Pufferspeicher zones
        self.ax.text(0.20, 0.98, "Pufferspeicher", transform=self.ax.transAxes, color=COLOR_TITLE, fontsize=12, va="top", ha="center", weight="bold")

        # Boiler cylinder (right, single color with better styling) - much smaller height (about half Pufferspeicher), on same baseline
        self.boiler_rect = FancyBboxPatch(
            (0.58, 0.08), 0.22, 0.45,
            boxstyle="round,pad=0.02,rounding_size=0.10",
            transform=self.ax.transAxes,
            linewidth=1.1,
            edgecolor="#2A3446",
            facecolor=self._temp_color(60),
            alpha=0.95,
        )
        self.ax.add_patch(self.boiler_rect)
        
        # Boiler caps
        self.boiler_top = Ellipse((0.69, 0.53), 0.22, 0.08, transform=self.ax.transAxes,
                                  edgecolor="#2A3446", facecolor="none", linewidth=1.0, alpha=0.7)
        self.boiler_bottom = Ellipse((0.69, 0.08), 0.22, 0.08, transform=self.ax.transAxes,
                                     edgecolor="#2A3446", facecolor="none", linewidth=1.0, alpha=0.7)
        self.ax.add_patch(self.boiler_top)
        self.ax.add_patch(self.boiler_bottom)
        
        # Highlight on Boiler
        boiler_hl = Rectangle((0.60, 0.10), 0.03, 0.41, transform=self.ax.transAxes,
                   facecolor="#ffffff", alpha=0.06, linewidth=0)
        self.ax.add_patch(boiler_hl)
        
        self.ax.text(0.69, 0.60, "Boiler", transform=self.ax.transAxes, color=COLOR_TITLE, fontsize=12, va="top", ha="center", weight="bold")
        
        # Add colorbar on the right
        from mpl_toolkits.axes_grid1 import make_axes_locatable
        divider = make_axes_locatable(self.ax)
        cax = divider.append_axes("right", size="4%", pad=0.15)
        cbar = self.fig.colorbar(self.im, cax=cax, orientation='vertical')
        cbar.set_label('°C', rotation=0, labelpad=10, color=COLOR_TEXT, fontsize=9)
        cbar.ax.tick_params(labelsize=8, colors=COLOR_TEXT)
        cbar.outline.set_edgecolor(COLOR_BORDER)
        cbar.outline.set_linewidth(0.8)

    def _build_cmap(self):
        colors = [COLOR_INFO, COLOR_WARNING, COLOR_DANGER]
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
        path = self._data_path("FroniusDaten.csv")
        if not os.path.exists(path):
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        lines = self._read_lines_safe(path)
        if len(lines) < 2:
            return []
        rows = []
        for line in lines[-1000:]:
            line = line.strip()
            if not line or line.lower().startswith("zeit"):
                continue
            try:
                row = next(csv.reader([line]))
                ts = datetime.fromisoformat(row[0])
                if ts < cutoff:
                    continue
                pv_kw = float(row[1])  # PV production in kW
                ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes, seconds=ts.second, microseconds=ts.microsecond)
                rows.append((ts_bin, pv_kw))
            except Exception:
                continue
        if not rows:
            return []
        # Aggregate by time bin
        agg = {}
        for ts, val in rows:
            s, c = agg.get(ts, (0.0, 0))
            agg[ts] = (s + val, c + 1)
        out = [(ts, s / c) for ts, (s, c) in sorted(agg.items())]
        # Strong smoothing with moving average (window of 5)
        return self._smooth_series(out, window=5)
    
    def _load_outdoor_temp_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        """Load outdoor temperature with smoothing."""
        path = self._data_path("Heizungstemperaturen.csv")
        if not os.path.exists(path):
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        lines = self._read_lines_safe(path)
        if len(lines) < 2:
            return []
        header = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("zeit"):
                header = next(csv.reader([line]))
                break
        idx_map = self._header_indices(header) if header else {}
        rows = []
        for line in lines[-800:]:
            line = line.strip()
            if not line or line.lower().startswith("zeit"):
                continue
            try:
                row = next(csv.reader([line]))
                ts = datetime.fromisoformat(row[0])
                if ts < cutoff:
                    continue
                val = self._row_value_by_keys(
                    row,
                    idx_map,
                    "Außentemperatur",
                    "Aussentemperatur",
                    "Außentemp",
                    "Aussentemp",
                    "Aussen",
                    "Außen",
                    "OutdoorTemp",
                    "OutTemp",
                )
                if val is None:
                    continue
                outdoor_temp = float(val)
                ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes, seconds=ts.second, microseconds=ts.microsecond)
                rows.append((ts_bin, outdoor_temp))
            except Exception:
                continue
        if not rows:
            return []
        # Aggregate by time bin
        agg = {}
        for ts, val in rows:
            s, c = agg.get(ts, (0.0, 0))
            agg[ts] = (s + val, c + 1)
        out = [(ts, s / c) for ts, (s, c) in sorted(agg.items())]
        # Strong smoothing with moving average (window of 5)
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

    @staticmethod
    def _header_indices(header: list[str]) -> dict[str, int]:
        return {_normalize_header(name): idx for idx, name in enumerate(header)}

    @staticmethod
    def _row_value_by_keys(row: list[str], idx_map: dict[str, int], *keys: str) -> str | None:
        for key in keys:
            norm = _normalize_header(key)
            if norm in idx_map:
                idx = idx_map[norm]
                if 0 <= idx < len(row):
                    return row[idx]
        return None

    def _load_puffer_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        path = self._data_path("Heizungstemperaturen.csv")
        if not os.path.exists(path):
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        lines = self._read_lines_safe(path)
        if len(lines) < 2:
            return []
        header = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.lower().startswith("zeit"):
                header = next(csv.reader([line]))
                break
        idx_map = self._header_indices(header) if header else {}
        rows = []
        for line in lines[-800:]:
            line = line.strip()
            if not line or line.lower().startswith("zeit"):
                continue
            try:
                row = next(csv.reader([line]))
                ts = datetime.fromisoformat(row[0])
                if ts < cutoff:
                    continue
                val = self._row_value_by_keys(
                    row,
                    idx_map,
                    "Pufferspeicher Mitte",
                    "Puffer_Mitte",
                    "Puffer Mitte",
                )
                if val is None:
                    continue
                mid = float(val)
                ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes, seconds=ts.second, microseconds=ts.microsecond)
                rows.append((ts_bin, mid))
            except Exception:
                continue
        if not rows:
            return []
        agg = {}
        for ts, val in rows:
            s, c = agg.get(ts, (0.0, 0))
            agg[ts] = (s + val, c + 1)
        out = [(ts, s / c) for ts, (s, c) in sorted(agg.items())]
        smoothed = []
        for i in range(len(out)):
            w = out[max(0, i-1):min(len(out), i+2)]
            smoothed.append((out[i][0], sum(v for _, v in w) / len(w)))
        return smoothed

    @staticmethod
    def _data_path(filename: str) -> str:
        # Calculate root directory: src/ui/views -> root/
        root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # Try multiple common paths
        candidates = [
            os.path.join(root_dir, "data", filename),  # data/ directory in root
            os.path.join(root_dir, filename),  # Root directory
            os.path.join("/home/laurenz/projekt1/Projekt1/data", filename),  # Raspberry Pi data path
            os.path.join("/home/laurenz/projekt1/Projekt1", filename),  # Raspberry Pi root
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate
        # Default fallback
        return candidates[0]

    @staticmethod
    def _read_lines_safe(path: str, max_bytes: int = 262144, max_lines: int = 2000) -> list[str]:
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - max_bytes))
                chunk = f.read().decode("utf-8-sig", errors="replace")
            lines = chunk.splitlines()
        except Exception:
            try:
                with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
                    lines = f.readlines()
            except Exception:
                return []

        if max_lines and len(lines) > max_lines:
            return lines[-max_lines:]
        return lines
