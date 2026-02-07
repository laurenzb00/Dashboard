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

    def __init__(self, parent: tk.Misc, notebook: ttk.Notebook, datastore, *args, **kwargs):
        super().__init__(notebook, bg=COLOR_ROOT, *args, **kwargs)
        self.root = parent.winfo_toplevel()
        self.notebook = notebook
        self.datastore = datastore

        self._period_var = tk.StringVar(value="24h")
        self._period_map: dict[str, int] = {"24h": 24, "7d": 168, "30d": 720}
        self.after_job = None

        # Compatibility hooks used elsewhere in app.py
        self._last_key = None
        self._latest_data = None

        notebook.add(self, text=emoji("📈 Historie", "Historie"))

        self._build_ui()
        self._update_plot()

    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, minsize=44)
        self.grid_rowconfigure(1, weight=1)
        self.grid_rowconfigure(2, minsize=26)
        self.grid_columnconfigure(0, weight=1)

        topbar = tk.Frame(self, bg=COLOR_ROOT)
        topbar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))

        tk.Label(
            topbar,
            text="Zeitraum",
            bg=COLOR_ROOT,
            fg=COLOR_SUBTEXT,
            font=("Segoe UI", 10),
        ).pack(side=tk.LEFT, padx=(0, 8))

        self.period_combo = ttk.Combobox(
            topbar,
            textvariable=self._period_var,
            values=list(self._period_map.keys()),
            state="readonly",
            width=6,
        )
        self.period_combo.pack(side=tk.LEFT)
        self.period_combo.bind("<<ComboboxSelected>>", lambda _e: self._update_plot())

        self.topbar_status = tk.Label(
            topbar,
            text="",
            bg=COLOR_ROOT,
            fg=COLOR_TITLE,
            font=("Segoe UI", 10),
        )
        self.topbar_status.pack(side=tk.RIGHT)

        plot_container = tk.Frame(self, bg=COLOR_ROOT)
        plot_container.grid(row=1, column=0, sticky="nsew", padx=10, pady=0)
        plot_container.grid_rowconfigure(0, weight=1)
        plot_container.grid_columnconfigure(0, weight=1)

        card = tk.Frame(plot_container, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        card.grid(row=0, column=0, sticky="nsew")
        card.grid_rowconfigure(0, weight=1)
        card.grid_columnconfigure(0, weight=1)

        self.fig = Figure(figsize=(8.0, 3.0), dpi=100)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(COLOR_CARD)

        self.canvas = FigureCanvasTkAgg(self.fig, master=card)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        self.statusbar = tk.Label(
            self,
            text="",
            bg=COLOR_ROOT,
            fg=COLOR_SUBTEXT,
            font=("Segoe UI", 9),
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

    def _schedule_update(self) -> None:
        if self.after_job is not None:
            try:
                self.after_cancel(self.after_job)
            except Exception:
                pass
        self.after_job = self.after(60000, self._update_plot)

    def _update_plot(self) -> None:
        hours = self._period_map.get(self._period_var.get(), 24)
        now = datetime.now()
        cutoff = now - timedelta(hours=hours)

        try:
            rows = self.datastore.get_recent_heating(hours=None, limit=8000) if self.datastore else []
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

        self.ax.clear()
        for spine in self.ax.spines.values():
            spine.set_color(COLOR_BORDER)

        self.ax.grid(True, alpha=0.20)
        self.ax.tick_params(axis="x", labelsize=9, colors=COLOR_SUBTEXT)
        self.ax.tick_params(axis="y", labelsize=9, colors=COLOR_SUBTEXT)
        self.ax.set_ylabel("°C", color=COLOR_SUBTEXT)

        # Always show the full window for the selected period.
        try:
            self.ax.set_xlim(cutoff, now)
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
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
            self._render_status(hours, 0)
            self.canvas.draw_idle()
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

        self.ax.legend(loc="upper right", fontsize=9, frameon=False)

        try:
            self.fig.tight_layout(pad=0.8)
        except Exception:
            pass

        self._render_status(hours, len(times_sorted))
        self.canvas.draw_idle()
        self._schedule_update()

    def _render_status(self, hours: int, points: int) -> None:
        self.topbar_status.config(text=f"{hours}h")
        self.statusbar.config(text=f"Letztes Update: {datetime.now().strftime('%H:%M:%S')}  |  Datenpunkte: {points}")

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
