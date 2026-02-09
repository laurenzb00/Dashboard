from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np

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
    COLOR_SUCCESS,
    COLOR_TITLE,
    emoji,
)


class HistoricalTab(tk.Frame):
    """Heizung-Historie: zeigt Temperatur-Verläufe als Linienplot.

    Ziele:
    - Tab wird zuverlässig im Notebook angezeigt
    - Zeitraum wählbar (24h/7d/30d)
    - Fehlende Werte werden als Lücken dargestellt (kein Fake-0)
    """

    def __init__(self, parent: tk.Misc, notebook: ttk.Notebook, datastore, tab_frame=None, *args, **kwargs):
        # Use provided tab_frame as parent or notebook (legacy)
        frame_parent = tab_frame if tab_frame is not None else notebook
        super().__init__(frame_parent, bg=COLOR_ROOT, *args, **kwargs)
        self.root = parent.winfo_toplevel()
        self.notebook = notebook
        self.datastore = datastore

        self._period_var = tk.StringVar(value="24h")
        self._period_map: dict[str, int] = {
            "24h": 24,
            "7d": 168,
            "30d": 720,
            "90d": 2160,
            "180d": 4320,
            "365d": 8760,
        }
        self.after_job = None

        # Compatibility hooks used elsewhere in app.py
        self._last_key = None
        self._latest_data = None

        # Only add to notebook if not using provided tab_frame
        if tab_frame is None:
            notebook.add(self, text=emoji("📈 Historie", "Historie"))
        else:
            # Pack self into the provided frame to fill it
            self.pack(fill=tk.BOTH, expand=True)

        self._build_ui()
        self._update_plot()

    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, minsize=44)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, minsize=26)
        self.grid_columnconfigure(0, weight=1)

        topbar = tk.Frame(self, bg=COLOR_ROOT)
        topbar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))

        self.topbar_status = tk.Label(
            topbar,
            text="",
            bg=COLOR_ROOT,
            fg=COLOR_TITLE,
            font=("Segoe UI", 12, "bold"),
        )
        self.topbar_status.pack(side=tk.RIGHT)

        # Zeitraum-Wahl: Touch-freundliche Buttons statt Combobox
        period_frame = tk.Frame(topbar, bg=COLOR_ROOT)
        period_frame.pack(side=tk.RIGHT, padx=(0, 12))
        tk.Label(
            period_frame,
            text="Zeitraum:",
            bg=COLOR_ROOT,
            fg=COLOR_SUBTEXT,
            font=("Segoe UI", 12),
        ).pack(side=tk.LEFT, padx=(0, 8))
        
        # Touch-freundliche Button-Gruppe mit CustomTkinter
        import customtkinter as ctk
        self._period_buttons = {}
        for period in ["24h", "7d", "30d", "90d", "180d", "365d"]:
            btn = ctk.CTkButton(
                period_frame,
                text=period,
                font=("Segoe UI", 11, "bold"),
                width=50,
                height=28,
                corner_radius=8,
                command=lambda p=period: self._select_period(p)
            )
            btn.pack(side=tk.LEFT, padx=2)
            self._period_buttons[period] = btn
        self._update_period_button_colors()

        plot_container = tk.Frame(self, bg=COLOR_ROOT)
        plot_container.grid(row=1, column=0, sticky="nsew", padx=10, pady=0)
        plot_container.grid_rowconfigure(0, weight=1)
        plot_container.grid_columnconfigure(0, weight=1)

        # Neutral dark background (avoid bluish tint).
        self.card = tk.Frame(plot_container, bg=COLOR_ROOT, highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.card.grid(row=0, column=0, sticky="nsew")
        self.card.grid_rowconfigure(0, weight=1)
        self.card.grid_columnconfigure(0, weight=1)

        # Zusätzlicher Chart-Frame für Padding zwischen Card-Border und Canvas
        self.chart_frame = tk.Frame(self.card, bg=COLOR_ROOT)
        self.chart_frame.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        # Figur schmaler machen, um rechtes Abschneiden zu vermeiden
        self.fig = Figure(figsize=(8.2, 4.5), dpi=100)
        # Solid background prevents redraw artifacts that can look like "two diagrams".
        self.fig.patch.set_facecolor(COLOR_ROOT)
        self.fig.patch.set_alpha(1.0)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(COLOR_ROOT)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        try:
            self.canvas_widget.configure(bg=COLOR_ROOT, highlightthickness=0)
        except Exception:
            pass
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)
        self.canvas_widget.bind("<Configure>", self._on_canvas_resize)

        self.statusbar = tk.Label(
            self,
            text="",
            bg=COLOR_ROOT,
            fg=COLOR_SUBTEXT,
            font=("Segoe UI", 11),
            anchor="w",
        )
        self.statusbar.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 10))

    @staticmethod
    def _parse_ts(value) -> datetime | None:
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
    def _as_float(v):
        if v in (None, ""):
            return None
        try:
            return float(v)
        except Exception:
            return None

    def _select_period(self, period: str) -> None:
        """Wechselt Zeitraum und aktualisiert Button-Farben."""
        self._period_var.set(period)
        self._update_period_button_colors()
        self._update_plot()

    def _update_period_button_colors(self) -> None:
        """Aktualisiert Button-Farben basierend auf aktuellem Zeitraum."""
        current = self._period_var.get()
        for period, btn in self._period_buttons.items():
            if period == current:
                btn.configure(fg_color=COLOR_PRIMARY, text_color="white", hover_color=COLOR_PRIMARY)
            else:
                btn.configure(fg_color=COLOR_CARD, text_color=COLOR_TEXT, hover_color=COLOR_BORDER)

    def _schedule_update(self) -> None:
        if self.after_job is not None:
            try:
                self.after_cancel(self.after_job)
            except Exception:
                pass
        self.after_job = self.after(60000, self._update_plot)

    def _on_canvas_resize(self, event) -> None:
        try:
            w = max(1, int(getattr(event, "width", 1)))
            h = max(1, int(getattr(event, "height", 1)))
            dpi = float(self.fig.get_dpi() or 100.0)
            self.fig.set_size_inches(w / dpi, h / dpi, forward=False)
            self._apply_layout()
            # Full draw to avoid leftover pixels from a previous larger render.
            self.canvas.draw()
        except Exception:
            pass

    def _apply_layout(self) -> None:
        # Extra padding to avoid right/bottom clipping of tick labels.
        # Mehr Platz rechts und links für bessere Darstellung
        try:
            self.fig.subplots_adjust(left=0.08, right=0.95, top=0.90, bottom=0.18)
        except Exception:
            pass

    def _sync_figure_to_canvas(self) -> None:
        """Make sure the figure render buffer matches the widget size.

        If the renderer buffer is smaller than the Tk widget, old pixels can remain visible
        and look like a second plot underneath.
        """
        try:
            if not hasattr(self, "canvas_widget"):
                return
            w = int(self.canvas_widget.winfo_width() or 0)
            h = int(self.canvas_widget.winfo_height() or 0)
            if w <= 2 or h <= 2:
                return
            dpi = float(self.fig.get_dpi() or 100.0)
            self.fig.set_size_inches(w / dpi, h / dpi, forward=False)
        except Exception:
            pass

    def _style_axes(self) -> None:
        self.ax.set_facecolor(COLOR_ROOT)
        # Sparkline-like minimal frame
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)
        self.ax.spines["left"].set_color(COLOR_BORDER)
        self.ax.spines["bottom"].set_color(COLOR_BORDER)
        self.ax.spines["left"].set_linewidth(0.5)
        self.ax.spines["bottom"].set_linewidth(0.5)

        self.ax.grid(True, color=COLOR_BORDER, alpha=0.20, linewidth=0.6)
        self.ax.tick_params(axis="both", which="major", labelsize=10, colors=COLOR_SUBTEXT, length=3, width=0.5)
        self.ax.set_ylabel("°C", fontsize=7, color=COLOR_INFO, rotation=0, labelpad=10, va="center")
        try:
            self.ax.xaxis.get_offset_text().set_visible(False)
        except Exception:
            pass

    def _update_plot(self) -> None:
        hours = self._period_map.get(self._period_var.get(), 24)
        period_label = self._period_var.get() or f"{hours}h"
        now = datetime.now()
        cutoff = now - timedelta(hours=hours)

        try:
            rows = self.datastore.get_recent_heating(hours=hours, limit=None) if self.datastore else []
        except Exception:
            rows = []

        times: list[datetime] = []
        series = {
            "top": [],
            "mid": [],
            "bot": [],
            "kessel": [],
            "warm": [],
            "outdoor": [],
        }

        for row in rows:
            ts = self._parse_ts((row or {}).get("timestamp"))
            if ts is None:
                continue
            if ts < cutoff or ts > now + timedelta(seconds=60):
                continue
            times.append(ts)

            for key in series.keys():
                val = self._as_float((row or {}).get(key))
                if val is None:
                    series[key].append(np.nan)
                    continue

                # Plausibility filtering; keep outdoor wider and allow 0°C.
                if key == "outdoor":
                    if not (-40.0 <= val <= 60.0):
                        series[key].append(np.nan)
                    else:
                        series[key].append(val)
                    continue

                # Heating temps: treat 0.0 as missing (common placeholder), and clamp plausible range.
                if val == 0.0:
                    series[key].append(np.nan)
                elif not (-40.0 <= val <= 120.0):
                    series[key].append(np.nan)
                else:
                    series[key].append(val)

        # Defensive: rebuild axes to avoid accidental overlay of multiple axes
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor(COLOR_ROOT)
        self.fig.patch.set_alpha(1.0)
        self._style_axes()

        # Ensure renderer matches widget size before drawing.
        self._sync_figure_to_canvas()

        # Title like sparkline: left aligned, subtle
        try:
            self.ax.set_title(
                f"Heizung & Temperaturen ({period_label})",
                loc="left",
                fontsize=13,
                color=COLOR_TEXT,
                pad=8,
            )
        except Exception:
            pass

        # Zeitachse etwas über "jetzt" hinaus erweitern für bessere Darstellung des letzten Wertes
        future_margin = timedelta(hours=hours * 0.02)  # 2% der Gesamtzeit als Puffer
        try:
            self.ax.set_xlim(cutoff, now + future_margin)
        except Exception:
            pass
        
        # Vertikale "Jetzt"-Linie
        try:
            self.ax.axvline(now, color=COLOR_PRIMARY, linewidth=1.5, linestyle='--', alpha=0.6, label='Jetzt')
        except Exception:
            pass

        if not times:
            self.ax.text(
                0.5,
                0.5,
                "Keine Daten",
                ha="center",
                va="center",
                transform=self.ax.transAxes,
                color=COLOR_SUBTEXT,
                fontsize=14,
            )
            # Empty state: no fake axes (avoid 0..1 scale / duplicate tick labels)
            self.ax.set_xticks([])
            self.ax.set_yticks([])
            try:
                for spine in self.ax.spines.values():
                    spine.set_visible(False)
                self.ax.grid(False)
                self.ax.set_ylabel("")
                self.ax.xaxis.get_offset_text().set_visible(False)
            except Exception:
                pass
            self._render_status(hours, 0)
            self._apply_layout()
            self.canvas.draw()
            self._schedule_update()
            return

        # Sort by time (DB text order may be inconsistent)
        order = np.argsort(np.array(times, dtype="datetime64[ns]"))
        times_sorted = [times[i] for i in order]

        def _ordered(arr):
            a = np.array(arr, dtype=float)
            return a[order]

        plot_defs = [
            ("top", "Puffer oben", COLOR_PRIMARY, "-"),
            ("mid", "Puffer mitte", COLOR_INFO, "-"),
            ("bot", "Puffer unten", COLOR_WARNING, "-"),
            ("kessel", "Kessel", COLOR_DANGER, "-"),
            ("warm", "Warmwasser", COLOR_SUCCESS, "-"),
            ("outdoor", "Außen", COLOR_SUBTEXT, "--"),
        ]

        for key, label, color, style in plot_defs:
            self.ax.plot(times_sorted, _ordered(series[key]), label=label, color=color, linewidth=1.6, linestyle=style, alpha=0.95)

        locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
        self.ax.xaxis.set_major_locator(locator)
        self.ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
        try:
            self.ax.xaxis.get_offset_text().set_visible(False)
        except Exception:
            pass

        # Legend: keep it compact and out of the way (avoid overlapping the newest data at the right).
        self.ax.legend(
            loc="upper left",
            fontsize=8,
            frameon=False,
            labelcolor=COLOR_SUBTEXT,
            ncol=3,
            handlelength=1.2,
            columnspacing=0.8,
            handletextpad=0.4,
        )

        try:
            self.ax.margins(x=0.01)
        except Exception:
            pass

        self._apply_layout()

        self._render_status(hours, len(times_sorted))
        self.canvas.draw()
        self._schedule_update()

    def _render_status(self, hours: int, points: int) -> None:
        # Show the selected period label instead of huge hour numbers.
        self.topbar_status.config(text=f"{self._period_var.get()}")
        self.statusbar.config(text=f"Letztes Update: {datetime.now().strftime('%H:%M')}  |  Datenpunkte: {points}")

    def update_data(self, data: dict) -> None:
        # Called by app update loop; keep for compatibility.
        self._latest_data = data

    def stop(self) -> None:
        if self.after_job is not None:
            try:
                self.after_cancel(self.after_job)
            except Exception:
                pass
            self.after_job = None
