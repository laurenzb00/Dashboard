from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from datetime import date, datetime, timedelta

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import numpy as np

from ui.styles import (
    COLOR_BORDER,
    COLOR_PRIMARY,
    COLOR_ROOT,
    COLOR_SUBTEXT,
    COLOR_TEXT,
    COLOR_TITLE,
    COLOR_WARNING,
    emoji,
)


class TagesproduktionTab(tk.Frame):
    """Tagesproduktion (PV-kWh pro Tag) als Linien-Diagramm.

    Anforderungen:
    - Zeitraum wählbar
    - Tage klar erkennbar (Marker + bei kurzen Zeiträumen Tages-Raster)
    """

    def __init__(
        self,
        parent: tk.Misc,
        notebook: ttk.Notebook,
        datastore,
        tab_frame=None,
        *args,
        **kwargs,
    ):
        frame_parent = tab_frame if tab_frame is not None else notebook
        super().__init__(frame_parent, bg=COLOR_ROOT, *args, **kwargs)
        self.root = parent.winfo_toplevel()
        self.notebook = notebook
        self.datastore = datastore

        self._period_var = tk.StringVar(value="30 Tage")
        self._period_map: dict[str, int] = {
            "7 Tage": 7,
            "30 Tage": 30,
            "180 Tage": 180,
            "1 Jahr": 365,
        }
        self._period_buttons: dict[str, object] = {}
        self.after_job = None

        # Only add to notebook if not using provided tab_frame
        if tab_frame is None:
            notebook.add(self, text=emoji("📊 Tagesproduktion", "Tagesproduktion"))
        else:
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

        tk.Label(
            topbar,
            text="Tagesproduktion",
            bg=COLOR_ROOT,
            fg=COLOR_TITLE,
            font=("Segoe UI", 13, "bold"),
        ).pack(side=tk.LEFT, padx=(2, 10))

        period_frame = tk.Frame(topbar, bg=COLOR_ROOT)
        period_frame.pack(side=tk.RIGHT, padx=(0, 12))
        tk.Label(
            period_frame,
            text="Zeitraum:",
            bg=COLOR_ROOT,
            fg=COLOR_SUBTEXT,
            font=("Segoe UI", 12),
        ).pack(side=tk.LEFT, padx=(0, 8))

        import customtkinter as ctk

        for period in ["7 Tage", "30 Tage", "180 Tage", "1 Jahr"]:
            btn = ctk.CTkButton(
                period_frame,
                text=period,
                font=("Segoe UI", 11, "bold"),
                width=74,
                height=28,
                corner_radius=8,
                command=lambda p=period: self._select_period(p),
            )
            btn.pack(side=tk.LEFT, padx=2)
            self._period_buttons[period] = btn
        self._update_period_button_colors()

        plot_container = tk.Frame(self, bg=COLOR_ROOT)
        plot_container.grid(row=1, column=0, sticky="nsew", padx=10, pady=0)
        plot_container.grid_rowconfigure(0, weight=1)
        plot_container.grid_columnconfigure(0, weight=1)

        self.card = tk.Frame(
            plot_container,
            bg=COLOR_ROOT,
            highlightthickness=1,
            highlightbackground=COLOR_BORDER,
        )
        self.card.grid(row=0, column=0, sticky="nsew")
        self.card.grid_rowconfigure(0, weight=1)
        self.card.grid_columnconfigure(0, weight=1)

        self.chart_frame = tk.Frame(self.card, bg=COLOR_ROOT)
        self.chart_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)

        self.fig = Figure(figsize=(10.0, 4.8), dpi=100)
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

    def _select_period(self, period: str) -> None:
        self._period_var.set(period)
        self._update_period_button_colors()
        self._update_plot()

    def _update_period_button_colors(self) -> None:
        current = self._period_var.get()
        for period, btn in self._period_buttons.items():
            try:
                if period == current:
                    btn.configure(fg_color=COLOR_PRIMARY, text_color="#ffffff", hover_color=COLOR_PRIMARY)
                else:
                    btn.configure(fg_color=COLOR_BORDER, text_color=COLOR_TEXT, hover_color=COLOR_PRIMARY)
            except Exception:
                pass

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
            if not self._sync_size(w, h):
                return
            self._apply_layout()
            # Full draw avoids stale pixels that can look like an overlayed smaller chart.
            self.canvas.draw()
        except Exception:
            pass

    def _sync_size(self, w: int, h: int) -> bool:
        """Sync Matplotlib figure size to the current Tk widget size.

        CTk/Tk layouts can briefly report 1x1 during relayouts. Resizing the
        renderer to that can leave a tiny re-render on top of an older buffer.
        """
        try:
            if w < 50 or h < 50:
                return False
            dpi = float(self.fig.get_dpi() or 100.0)
            self.fig.set_size_inches(w / dpi, h / dpi, forward=True)
            return True
        except Exception:
            return False

    def _clear_tk_canvas(self) -> None:
        """Ensure the underlying Tk canvas is configured for clean redraws."""
        try:
            tk_canvas = getattr(self.canvas, "_tkcanvas", None)
            if tk_canvas is not None:
                try:
                    tk_canvas.configure(bg=COLOR_ROOT, highlightthickness=0, bd=0)
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_layout(self) -> None:
        try:
            self.fig.subplots_adjust(left=0.07, right=0.98, top=0.90, bottom=0.18)
        except Exception:
            pass

    @staticmethod
    def _date_range(start: date, end: date):
        cur = start
        one = timedelta(days=1)
        while cur <= end:
            yield cur
            cur = cur + one

    def _load_daily_pv(self, window_days: int) -> list[tuple[datetime, float]]:
        out: list[tuple[datetime, float]] = []
        try:
            rows = self.datastore.get_daily_totals(days=window_days) if self.datastore else []
        except Exception:
            rows = []

        for row in rows or []:
            try:
                day_raw = row.get("day")
                if not day_raw:
                    continue
                # get_daily_totals returns YYYY-MM-DD (no tz). Treat as midnight.
                ts = datetime.fromisoformat(str(day_raw))
                val = row.get("pv_kwh")
                if val is None:
                    continue
                out.append((ts, float(val)))
            except Exception:
                continue
        out.sort(key=lambda t: t[0])
        return out

    def _with_gaps_daily(self, data: list[tuple[datetime, float]], window_days: int) -> tuple[list[datetime], np.ndarray]:
        if not data:
            return ([], np.array([], dtype=float))

        end_day = datetime.now().date()
        start_day = end_day - timedelta(days=max(1, int(window_days)) - 1)
        by_day: dict[date, float] = {}
        for ts, val in data:
            by_day[ts.date()] = float(val)

        xs: list[datetime] = []
        ys: list[float] = []
        for d in self._date_range(start_day, end_day):
            xs.append(datetime.combine(d, datetime.min.time()))
            ys.append(by_day.get(d, float("nan")))
        return xs, np.array(ys, dtype=float)

    def _style_axes(self) -> None:
        self.ax.set_facecolor(COLOR_ROOT)
        self.ax.spines["top"].set_visible(False)
        self.ax.spines["right"].set_visible(False)
        self.ax.spines["left"].set_color(COLOR_BORDER)
        self.ax.spines["bottom"].set_color(COLOR_BORDER)
        self.ax.spines["left"].set_linewidth(0.6)
        self.ax.spines["bottom"].set_linewidth(0.6)
        self.ax.tick_params(axis="both", which="major", labelsize=9, colors=COLOR_SUBTEXT, length=2, width=0.5)
        self.ax.grid(True, axis="y", color=COLOR_BORDER, alpha=0.08, linewidth=0.6)
        self.ax.grid(False, axis="x")

    def _update_plot(self) -> None:
        window_days = int(self._period_map.get(self._period_var.get(), 30))
        raw = self._load_daily_pv(window_days)
        xs, ys = self._with_gaps_daily(raw, window_days)

        # Keep the render buffer aligned with the widget size before clearing/plotting.
        try:
            self._clear_tk_canvas()
            w = int(self.canvas_widget.winfo_width() or 0)
            h = int(self.canvas_widget.winfo_height() or 0)
            self._sync_size(w, h)
        except Exception:
            pass

        self.ax.clear()
        self._style_axes()

        if not xs or ys.size == 0:
            self.ax.text(
                0.5,
                0.5,
                "Keine Daten",
                ha="center",
                va="center",
                transform=self.ax.transAxes,
                color=COLOR_SUBTEXT,
                fontsize=10,
            )
            self.statusbar.configure(text="Keine Daten im Zeitraum")
            self._apply_layout()
            self.canvas.draw()
            self._schedule_update()
            return

        # Plot: daily line with markers so individual days are easy to see.
        self.ax.plot(
            xs,
            ys,
            color=COLOR_WARNING,
            linewidth=2.0,
            alpha=0.9,
            marker="o",
            markersize=3.5,
            markerfacecolor=COLOR_WARNING,
            markeredgewidth=0.0,
        )

        # Make day boundaries visible for short windows.
        if window_days <= 30:
            for d in (dt.date() for dt in xs):
                try:
                    self.ax.axvline(
                        datetime.combine(d, datetime.min.time()),
                        color=COLOR_BORDER,
                        alpha=0.10,
                        linewidth=0.7,
                    )
                except Exception:
                    pass

        # Axis formatting.
        self.ax.set_ylabel("PV (kWh / Tag)", color=COLOR_SUBTEXT, fontsize=10)
        self.ax.set_ylim(bottom=0)

        if window_days <= 30:
            day_interval = 3 if window_days == 30 else 1
            self.ax.xaxis.set_major_locator(mdates.DayLocator(interval=day_interval))
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
            for label in self.ax.get_xticklabels():
                label.set_rotation(0)
        elif window_days <= 180:
            self.ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))
        else:
            self.ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

        # Status: show last value if available.
        last_val = None
        try:
            if len(raw) > 0:
                last_val = float(raw[-1][1])
        except Exception:
            last_val = None
        if last_val is None or not np.isfinite(last_val):
            self.statusbar.configure(text=f"Zeitraum: {self._period_var.get()}")
        else:
            self.statusbar.configure(text=f"Zeitraum: {self._period_var.get()}  •  Letzter Tag: {last_val:.1f} kWh")

        self._apply_layout()
        # Full draw (not draw_idle) to avoid ghost pixels / overlays.
        self.canvas.draw()
        self._schedule_update()
