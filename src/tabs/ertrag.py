from datetime import datetime, timedelta
import tkinter as tk
from tkinter import ttk
import matplotlib.dates as mdates
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
    emoji,
)
from ui.components.card import Card


class ErtragTab:
    """PV-Ertrag pro Tag über längeren Zeitraum."""

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self._update_task_id = None  # Track scheduled update to prevent stacking

        self.tab_frame = tk.Frame(self.notebook, bg=COLOR_ROOT)
        self.notebook.add(self.tab_frame, text=emoji("🔆 Ertrag", "Ertrag"))

        self.card = Card(self.tab_frame)
        self.card.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)
        self.card.add_title("PV-Ertrag (täglich)", icon="📈")

        body = self.card.content()
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        self.chart_frame = tk.Frame(body, bg=COLOR_CARD)
        self.chart_frame.grid(row=0, column=0, sticky="nsew")

        self.fig = Figure(figsize=(7.6, 3.2), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax.set_facecolor(COLOR_CARD)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        self._last_canvas_size = None
        self._resize_pending = False
        self.canvas.get_tk_widget().bind("<Configure>", self._on_canvas_resize)

        stats_frame = ttk.Frame(body)
        stats_frame.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        self.var_sum = tk.StringVar(value="Summe: -- kWh")
        self.var_avg = tk.StringVar(value="Schnitt/Tag: -- kWh")
        self.var_last = tk.StringVar(value="Letzter Tag: -- kWh")
        ttk.Label(stats_frame, textvariable=self.var_sum).pack(side=tk.LEFT, padx=6)
        ttk.Label(stats_frame, textvariable=self.var_avg).pack(side=tk.LEFT, padx=6)
        ttk.Label(stats_frame, textvariable=self.var_last).pack(side=tk.LEFT, padx=6)

        self._last_key = None
        self.store = get_shared_datastore()
        self.root.after(100, self._update_plot)

    def stop(self):
        self.alive = False
        if self._update_task_id:
            try:
                self.root.after_cancel(self._update_task_id)
            except Exception:
                pass

    def _load_pv_daily(self, days: int = 365):
        cutoff = datetime.now() - timedelta(days=days)
        series = []
        for row in self.store.get_daily_totals(days=days):
            try:
                ts = datetime.fromisoformat(row['day'])
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            series.append((ts, float(row.get('pv_kwh') or 0.0)))
        return series

    def _load_pv_monthly(self, months: int = 12):
        """Lade und aggregiere PV-Ertrag nach Monaten."""
        series = []
        cutoff = datetime.now() - timedelta(days=months * 31)
        for row in self.store.get_monthly_totals(months=months):
            try:
                ts = datetime.fromisoformat(row['month'])
            except (ValueError, TypeError):
                continue
            if ts < cutoff:
                continue
            series.append((ts, float(row.get('pv_kwh') or 0.0)))
        return series

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

        window_days = 90
        data = self._load_pv_daily(window_days)
        key = (len(data), data[-1] if data else None) if data else ("empty",)
        
        # Nur redraw wenn sich Daten wirklich geändert haben
        if key == self._last_key:
            # Keine Änderung - nur neu einplanen, nicht redraw
            self.root.after(5 * 60 * 1000, self._update_plot)
            return
        
        self._last_key = key

        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax.set_facecolor(COLOR_CARD)
        self._style_axes()

        if data:
            if not getattr(self, "_log_once", False):
                print(f"[ERTRAG] Matplotlib-Liniendiagramm aktiv (Tage={len(data)})")
                self._log_once = True
            ts, vals = zip(*data)
            self.ax.plot(ts, vals, color=COLOR_PRIMARY, linewidth=2.0, alpha=0.9, marker="o", markersize=3)
            self.ax.fill_between(ts, vals, color=COLOR_PRIMARY, alpha=0.15)

            self.ax.set_ylabel("Ertrag (kWh/Tag)", color=COLOR_TEXT, fontsize=10, fontweight='bold')
            self.ax.tick_params(axis="y", colors=COLOR_TEXT, labelsize=9)
            self.ax.tick_params(axis="x", colors=COLOR_SUBTEXT, labelsize=8)
            locator = mdates.AutoDateLocator(minticks=4, maxticks=10)
            self.ax.xaxis.set_major_locator(locator)
            self.ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            for label in self.ax.get_xticklabels():
                label.set_rotation(45)
                label.set_horizontalalignment("right")
            self.ax.grid(True, color=COLOR_BORDER, alpha=0.2, linewidth=0.6)
            self.ax.set_ylim(bottom=0)

            total = sum(vals)
            avg = total / max(1, len(vals))
            last_ts = ts[-1].strftime("%d.%m.%Y")
            last_val = vals[-1]
            self.var_sum.set(f"Summe ({window_days}T): {total:.0f} kWh")
            self.var_avg.set(f"Ø Tag: {avg:.1f} kWh")
            self.var_last.set(f"{last_ts}: {last_val:.1f} kWh")
        else:
            self.ax.text(0.5, 0.5, "Keine Daten", color=COLOR_SUBTEXT, ha="center", va="center", 
                        fontsize=12, transform=self.ax.transAxes)
            self.ax.set_xticks([])
            self.ax.set_yticks([])
            self.var_sum.set("Summe: -- kWh")
            self.var_avg.set("Ø Tag: -- kWh")
            self.var_last.set("Max: -- kWh")

        self.fig.subplots_adjust(left=0.08, right=0.98, top=0.94, bottom=0.2)
        
        try:
            if self.canvas.get_tk_widget().winfo_exists():
                self.canvas.draw_idle()
        except Exception:
            pass
        
        # Nächster Update in 5 Minuten
        self.root.after(5 * 60 * 1000, self._update_plot)

    def _on_canvas_resize(self, event):
        if self._resize_pending:
            return
        
        w = max(1, event.width)
        h = max(1, event.height)
        if self._last_canvas_size:
            last_w, last_h = self._last_canvas_size
            if abs(w - last_w) < 10 and abs(h - last_h) < 10:
                return
        
        self._last_canvas_size = (w, h)
        self._resize_pending = True
        
        try:
            self.fig.subplots_adjust(left=0.08, right=0.98, top=0.94, bottom=0.2)
            self.root.after(100, lambda: self._do_canvas_draw())
        except Exception:
            self._resize_pending = False
    
    def _do_canvas_draw(self):
        try:
            if self.canvas.get_tk_widget().winfo_exists():
                self.canvas.draw_idle()
        except Exception:
            pass
        finally:
            self._resize_pending = False


