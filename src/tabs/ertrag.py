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

        self._period_var = tk.StringVar(value="90d")
        self._period_map: dict[str, int] = {"30d": 30, "90d": 90, "180d": 180, "365d": 365}

        # Layout like HistoricalTab: topbar + plot card + status line
        self.tab_frame.grid_rowconfigure(0, minsize=44)
        self.tab_frame.grid_rowconfigure(1, weight=1)
        self.tab_frame.grid_rowconfigure(2, minsize=26)
        self.tab_frame.grid_columnconfigure(0, weight=1)

        topbar = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        topbar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))

        tk.Label(topbar, text="Zeitraum", bg=COLOR_ROOT, fg=COLOR_SUBTEXT, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 8))
        self.period_combo = ttk.Combobox(
            topbar,
            textvariable=self._period_var,
            values=list(self._period_map.keys()),
            state="readonly",
            width=6,
        )
        self.period_combo.pack(side=tk.LEFT)
        self.period_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_plot())

        self.topbar_status = tk.Label(topbar, text="", bg=COLOR_ROOT, fg=COLOR_TITLE, font=("Segoe UI", 10))
        self.topbar_status.pack(side=tk.RIGHT)

        plot_container = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        plot_container.grid(row=1, column=0, sticky="nsew", padx=10, pady=0)
        plot_container.grid_rowconfigure(0, weight=1)
        plot_container.grid_columnconfigure(0, weight=1)

        self.card = tk.Frame(plot_container, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.card.grid(row=0, column=0, sticky="nsew")
        self.card.grid_rowconfigure(0, weight=1)
        self.card.grid_columnconfigure(0, weight=1)

        self.chart_frame = tk.Frame(self.card, bg=COLOR_CARD)
        self.chart_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        # Feste Größe und Layout für das Diagramm, da Bildschirm bekannt
        self.fig = Figure(figsize=(9.0, 4.5), dpi=100)
        self.fig.patch.set_alpha(0)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor("none")
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        # Keine dynamische Resize-Events nötig

        stats_frame = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        stats_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 10))
        self.var_sum = tk.StringVar(value="Summe: -- kWh")
        self.var_avg = tk.StringVar(value="Schnitt/Tag: -- kWh")
        self.var_last = tk.StringVar(value="Letzter Tag: -- kWh")
        tk.Label(stats_frame, textvariable=self.var_sum, bg=COLOR_ROOT, fg=COLOR_TEXT, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(stats_frame, textvariable=self.var_avg, bg=COLOR_ROOT, fg=COLOR_SUBTEXT, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(stats_frame, textvariable=self.var_last, bg=COLOR_ROOT, fg=COLOR_SUBTEXT, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 12))

        self._last_key = None
        self.store = get_shared_datastore()
        self._update_task_id = self.root.after(100, self._update_plot)

    def stop(self):
        self.alive = False
        if self._update_task_id:
            try:
                self.root.after_cancel(self._update_task_id)
            except Exception:
                pass
        # Explicitly close matplotlib figure to prevent memory leaks
        try:
            import matplotlib.pyplot as plt
            if hasattr(self, 'fig') and self.fig:
                plt.close(self.fig)
                self.fig = None
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
            pv_kwh = row.get('pv_kwh')
            if pv_kwh is None:
                continue
            series.append((ts, float(pv_kwh)))
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
            pv_kwh = row.get('pv_kwh')
            if pv_kwh is None:
                continue
            series.append((ts, float(pv_kwh)))
        return series

    def _style_axes(self):
        self.ax.set_facecolor("none")
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

        if self._update_task_id:
            try:
                self.root.after_cancel(self._update_task_id)
            except Exception:
                pass
            self._update_task_id = None

        window_days = self._period_map.get(self._period_var.get(), 90)
        data = self._load_pv_daily(window_days)
        key = (len(data), data[-1] if data else None) if data else ("empty",)
        
        # Nur redraw wenn sich Daten wirklich geändert haben
        if key == self._last_key:
            # Keine Änderung - nur neu einplanen, nicht redraw
            self._update_task_id = self.root.after(5 * 60 * 1000, self._update_plot)
            return
        
        self._last_key = key

        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_alpha(0)
        self.ax.set_facecolor("none")
        self._style_axes()

        if data:
            if not getattr(self, "_log_once", False):
                print(f"[ERTRAG] Matplotlib-Liniendiagramm aktiv (Tage={len(data)})")
                self._log_once = True
            ts, vals = zip(*data)
            self.ax.plot(ts, vals, color=COLOR_PRIMARY, linewidth=2.0, alpha=0.9, marker="o", markersize=3)

            self.ax.set_ylabel("Ertrag (kWh/Tag)", color=COLOR_TEXT, fontsize=11, fontweight='bold')
            self.ax.tick_params(axis="y", colors=COLOR_TEXT, labelsize=10)
            self.ax.tick_params(axis="x", colors=COLOR_SUBTEXT, labelsize=9)
            locator = mdates.AutoDateLocator(minticks=4, maxticks=10)
            self.ax.xaxis.set_major_locator(locator)
            self.ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            for label in self.ax.get_xticklabels():
                label.set_rotation(35)
                label.set_horizontalalignment("right")
            self.ax.grid(True, color=COLOR_BORDER, alpha=0.2, linewidth=0.6)
            self.ax.set_ylim(bottom=0)

            total = sum(vals)
            avg = total / max(1, len(vals))
            last_ts = ts[-1].strftime("%d.%m.%Y")
            last_val = vals[-1]
            self.topbar_status.config(text=f"{window_days}d")
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
            self.var_last.set("Letzter Tag: -- kWh")
            self.topbar_status.config(text=f"{window_days}d")

        # Feste Ränder für optimalen Sitz
        self.fig.subplots_adjust(left=0.10, right=0.98, top=0.92, bottom=0.18)
        
        try:
            if self.canvas.get_tk_widget().winfo_exists():
                self.canvas.draw_idle()
        except Exception:
            pass
        
        # Nächster Update in 5 Minuten
        self._update_task_id = self.root.after(5 * 60 * 1000, self._update_plot)

    # Keine dynamische Größenanpassung nötig


