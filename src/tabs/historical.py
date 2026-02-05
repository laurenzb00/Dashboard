import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from core.datastore import get_shared_datastore
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_PRIMARY,
    COLOR_INFO,
    COLOR_WARNING,
    COLOR_DANGER,
    emoji,
)
from ui.components.card import Card


class HistoricalTab:
    """Historie der Heizung/Puffer/Außen + aktuelle Werte."""

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self._last_temps_cache = None  # Cache um Segfault zu vermeiden
        self._last_cache_time = 0
        self._update_task_id = None  # Track scheduled update to prevent stacking

        self.tab_frame = tk.Frame(self.notebook, bg=COLOR_ROOT)
        self.notebook.add(self.tab_frame, text=emoji("📈 Heizung-Historie", "Heizung-Historie"))
        self.datastore = get_shared_datastore()

        self.tab_frame.grid_columnconfigure(0, weight=1)
        self.tab_frame.grid_rowconfigure(0, weight=0)
        self.tab_frame.grid_rowconfigure(1, weight=1)

        # Main Card
        self.card = Card(self.tab_frame)
        self.card.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=12, pady=12)
        self.card.add_title("Heizungsdaten (letzte 7 Tage)", icon="🌡️")

        body = self.card.content()
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)

        stats_frame = tk.Frame(body, bg=COLOR_CARD)
        stats_frame.grid(row=0, column=0, sticky="new", pady=(0, 0), ipady=0)
        
        self.var_top = tk.StringVar(value="-- °C")
        self.var_mid = tk.StringVar(value="-- °C")
        self.var_bot = tk.StringVar(value="-- °C")
        self.var_boiler = tk.StringVar(value="-- °C")
        self.var_out = tk.StringVar(value="-- °C")
        
        stats_grid = [
            ("Puffer oben", self.var_top, COLOR_PRIMARY),
            ("Puffer mitte", self.var_mid, COLOR_INFO),
            ("Puffer unten", self.var_bot, COLOR_SUBTEXT),
            ("Kessel", self.var_boiler, COLOR_WARNING),
            ("Außen", self.var_out, COLOR_TEXT),
        ]
        
        for idx, (label, var, color) in enumerate(stats_grid):
            stat_card = tk.Frame(stats_frame, bg=COLOR_CARD)
            stat_card.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=4)
            
            ttk.Label(stat_card, text=label, font=("Arial", 9), foreground=color).pack(anchor="w", padx=6, pady=(4, 2))
            ttk.Label(stat_card, textvariable=var, font=("Arial", 14, "bold")).pack(anchor="w", padx=6, pady=(0, 4))

        # Plot
        plot_frame = tk.Frame(body, bg=COLOR_CARD)
        plot_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.fig = Figure(figsize=(8, 3.5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax.set_facecolor(COLOR_CARD)
        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._last_canvas_size = None
        # Dynamische Größenanpassung: Passe Figure-Größe an Frame an
        def on_resize(event):
            w = max(4, event.width / 100)
            h = max(2, event.height / 100)
            self.fig.set_size_inches(w, h)
            self.canvas.draw_idle()
        plot_frame.bind("<Configure>", on_resize)

        self._last_key = None
        self._resize_pending = False  # Prevent Configure event loop
        self.root.after(100, self._update_plot)

    def stop(self):
        self.alive = False
        if self._update_task_id:
            try:
                self.root.after_cancel(self._update_task_id)
            except Exception:
                pass
        # Explicitly close matplotlib figure and destroy canvas to prevent memory leaks
        try:
            import matplotlib.pyplot as plt
            if hasattr(self, 'fig') and self.fig:
                plt.close(self.fig)
                self.fig = None
            if hasattr(self, 'canvas') and self.canvas:
                widget = self.canvas.get_tk_widget()
                if widget:
                    widget.destroy()
        except Exception:
            pass

    def _load_temps(self):
        import time
        # Cache: Nur alle 120s neu laden um Segfault zu vermeiden
        now = time.time()
        if self._last_temps_cache and (now - self._last_cache_time) < 120:
            return self._last_temps_cache
        
        if not self.datastore:
            return []

        rows = self.datastore.get_recent_heating(hours=168, limit=2000)
        cutoff = datetime.now() - timedelta(days=4)
        parsed = []
        fallback = []
        for entry in rows:
            ts = self._parse_ts(entry.get('timestamp'))
            top = self._safe_float(entry.get('top'))
            mid = self._safe_float(entry.get('mid'))
            bot = self._safe_float(entry.get('bot'))
            # Kessel = entry.get('kessel')
            boiler = self._safe_float(entry.get('kessel'))
            outside = self._safe_float(entry.get('outdoor'))
            if None in (ts, top, mid, bot, boiler, outside):
                continue
            fallback.append((ts, top, mid, bot, boiler, outside))
            if ts >= cutoff:
                parsed.append((ts, top, mid, bot, boiler, outside))

        parsed.sort(key=lambda r: r[0])
        if parsed:
            self._last_temps_cache = parsed
            self._last_cache_time = now
            return parsed

        fallback.sort(key=lambda r: r[0])
        if fallback:
            result = fallback[-500:]
            self._last_temps_cache = result
            self._last_cache_time = now
            return result

        self._last_temps_cache = []
        self._last_cache_time = now
        return []

    @staticmethod
    def _safe_float(value) -> float | None:
        if value is None:
            return None
        try:
            return float(str(value).replace(",", "."))
        except Exception:
            return None

    def _style_axes(self):
        self.ax.set_facecolor(COLOR_CARD)
        for spine in ["top", "right"]:
            self.ax.spines[spine].set_visible(False)
        for spine in ["left", "bottom"]:
            self.ax.spines[spine].set_color(COLOR_BORDER)
            self.ax.spines[spine].set_linewidth(1)

    def _update_plot(self):
        if not self.alive:
            return
        if not self.canvas.get_tk_widget().winfo_exists():
            return

        rows = self._load_temps()
        key = (len(rows), rows[-1] if rows else None) if rows else ("empty",)
        
        # Nur redraw wenn sich Daten wirklich geändert haben
        if key != self._last_key:
            self._last_key = key
            self.fig.clear()
            self.ax = self.fig.add_subplot(111)
            self.fig.patch.set_facecolor(COLOR_CARD)
            self.ax.set_facecolor(COLOR_CARD)
            self._style_axes()

            if rows:
                if not getattr(self, "_log_once", False):
                    print(f"[HISTORIE] Matplotlib-Liniendiagramm aktiv (Samples={len(rows)})")
                    self._log_once = True
                ts, top, mid, bot, boiler, outside = zip(*rows)
                # Collect all temperature values for dynamic y-axis scaling
                all_temps = list(top) + list(mid) + list(bot) + list(boiler) + list(outside)
                # Moderneres Design mit besseren Farben und Liniendicken
                self.ax.plot(ts, top, color=COLOR_PRIMARY, label="Puffer oben", linewidth=2.0, alpha=0.8)
                self.ax.plot(ts, mid, color=COLOR_INFO, label="Puffer mitte", linewidth=1.5, alpha=0.7)
                self.ax.plot(ts, bot, color=COLOR_SUBTEXT, label="Puffer unten", linewidth=1.5, alpha=0.6)
                self.ax.plot(ts, boiler, color=COLOR_WARNING, label="Kessel", linewidth=2.0, alpha=0.8)
                self.ax.plot(ts, outside, color=COLOR_DANGER, label="Außen", linewidth=1.8, alpha=0.8, linestyle='--')
                self.ax.set_ylabel("°C", color=COLOR_TEXT, fontsize=10, fontweight='bold')
                self.ax.tick_params(axis="y", colors=COLOR_TEXT, labelsize=9)
                self.ax.tick_params(axis="x", colors=COLOR_SUBTEXT, labelsize=8)
                # Dynamic y-axis scaling
                self.fig.subplots_adjust(left=0.10, right=0.995, top=0.92, bottom=0.18)
                if len(all_temps) > 1:
                    min_temp = min(all_temps)
                    max_temp = max(all_temps)
                    self.ax.set_ylim(min_temp - 2, max_temp + 2)
                # Improved x-axis formatting
                self.ax.xaxis.set_major_locator(mdates.AutoDateLocator())
                self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
                self.ax.grid(True, color=COLOR_BORDER, alpha=0.2, linewidth=0.5)
                self.ax.legend(facecolor=COLOR_CARD, edgecolor=COLOR_BORDER, labelcolor=COLOR_TEXT, fontsize=8, loc='upper left')

                # Current values
                self.var_top.set(f"{top[-1]:.1f} °C")
                self.var_mid.set(f"{mid[-1]:.1f} °C")
                self.var_bot.set(f"{bot[-1]:.1f} °C")
                self.var_boiler.set(f"{boiler[-1]:.1f} °C")
                self.var_out.set(f"{outside[-1]:.1f} °C")
            else:
                self.ax.text(0.5, 0.5, "Keine Daten", color=COLOR_SUBTEXT, ha="center", va="center", 
                            fontsize=12, transform=self.ax.transAxes)
                self.ax.set_xticks([])
                self.ax.set_yticks([])
                self.var_top.set("-- °C")
                self.var_mid.set("-- °C")
                self.var_bot.set("-- °C")
                self.var_boiler.set("-- °C")
                self.var_out.set("-- °C")

            # Feste Ränder und Schriftgrößen für optimalen Sitz
            self.fig.subplots_adjust(left=0.10, right=0.995, top=0.92, bottom=0.18)
            self.fig.patch.set_alpha(0)
            self.ax.set_facecolor("none")
            self.ax.set_ylabel("°C", color=COLOR_TEXT, fontsize=13, fontweight='bold')
            self.ax.tick_params(axis="y", colors=COLOR_TEXT, labelsize=12)
            self.ax.tick_params(axis="x", colors=COLOR_SUBTEXT, labelsize=11)
            for label in self.ax.get_xticklabels():
                label.set_rotation(35)
                label.set_horizontalalignment("right")
            legend = self.ax.get_legend()
            if legend:
                try:
                    legend.set_fontsize(11)
                except AttributeError:
                    legend.prop = {'size': 11}
                try:
                    legend.set_bbox_to_anchor((1, 1))
                    legend.set_loc('upper left')
                except Exception:
                    pass
            try:
                if self.canvas.get_tk_widget().winfo_exists():
                    self.canvas.draw_idle()
            except Exception:
                pass

        # Nächster Update in 30 Sekunden - cancel previous task first
        if self._update_task_id:
            try:
                self.root.after_cancel(self._update_task_id)
            except Exception:
                pass
        self._update_task_id = self.root.after(30 * 1000, self._update_plot)

    def _on_plot_frame_resize(self, event):
        # Dynamisches Resizing der Figure auf die exakte Frame-Größe
        w = max(1, event.width)
        h = max(1, event.height)
        if self._last_canvas_size:
            last_w, last_h = self._last_canvas_size
            if abs(w - last_w) < 8 and abs(h - last_h) < 8:
                return
        self._last_canvas_size = (w, h)
        try:
            dpi = self.fig.dpi or 100
            width_in = w / dpi
            height_in = h / dpi
            self.fig.set_size_inches(width_in, height_in, forward=True)
            self.canvas.draw_idle()
        except Exception:
            pass
    
    def _do_canvas_draw(self):
        """Deferred canvas draw to prevent Configure event loop."""
        try:
            if self.canvas.get_tk_widget().winfo_exists():
                self.canvas.draw_idle()
        except Exception:
            pass
        finally:
            self._resize_pending = False

    def _resize_figure(self, width_px: int, height_px: int) -> None:
        dpi = self.fig.dpi or 100
        # Maximalbreite auf 900px begrenzen (z.B. Card-Breite)
        width_px = min(width_px, 900)
        width_in = max(4.5, width_px / dpi)
        height_in = max(2.8, height_px / dpi)
        self.fig.set_size_inches(width_in, height_in, forward=True)

    @staticmethod
    def _parse_ts(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None
