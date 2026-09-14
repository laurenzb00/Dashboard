from __future__ import annotations

import logging
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
    COLOR_CARD,
    COLOR_PRIMARY,
    COLOR_ROOT,
    COLOR_SUBTEXT,
    COLOR_TEXT,
    COLOR_TITLE,
    COLOR_WARNING,
    FONT_SIZE_TITLE,
    FONT_SIZE_SUBTITLE,
    FONT_SIZE_BODY,
    BUTTON_HEIGHT_SECONDARY,
    PADDING_SECTION,
    emoji,
)
from ui.components.tab_shell import TabShell
from ui.components.metric_tile import MetricTile


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
            self._shell = TabShell(tab_frame, "Tagesproduktion", "PV-Ertrag pro Tag")
            self._shell.pack(fill=tk.BOTH, expand=True)
            self.pack(in_=self._shell.body, fill=tk.BOTH, expand=True)
            # self is a sibling of _shell under tab_frame (packed "in" the
            # shell's body region), so it must be explicitly raised above
            # _shell or the shell's body frame paints over it and hides
            # everything (topbar, chart, statusbar).
            self.tkraise()

        self._resize_job = None
        self._last_synced_wh = (0, 0)
        self._build_ui()
        self.after(180, self._update_plot)
        # Belt-and-suspenders: the figure has been observed stuck at its
        # figsize=(10.0, 4.8) default (1000x480px) even though chart_frame
        # ends up much bigger, i.e. the passive <Configure>/<Map> bindings
        # on canvas_widget don't reliably fire a real resize during the
        # CTkTabview build/first-show dance. A couple of extra delayed
        # forced passes after startup catch that case.
        self.after(500, self._resize_canvas_now)
        self.after(1200, self._resize_canvas_now)

    def _build_ui(self) -> None:
        self.grid_rowconfigure(0, minsize=56)
        # Portrait-only metrics panel; hidden (minsize=0) until set_portrait_layout(True).
        self.grid_rowconfigure(1, minsize=0, weight=0)
        self.grid_rowconfigure(2, weight=1)
        self.grid_rowconfigure(3, minsize=40)
        self.grid_columnconfigure(0, weight=1)

        topbar = tk.Frame(self, bg=COLOR_ROOT)
        topbar.grid(row=0, column=0, sticky="ew", padx=PADDING_SECTION, pady=(PADDING_SECTION, 8))

        period_frame = tk.Frame(topbar, bg=COLOR_ROOT)
        period_frame.pack(side=tk.RIGHT, padx=(0, 12))
        tk.Label(
            period_frame,
            text="Zeitraum:",
            bg=COLOR_ROOT,
            fg=COLOR_SUBTEXT,
            font=("Segoe UI", FONT_SIZE_SUBTITLE),
        ).pack(side=tk.LEFT, padx=(0, 8))

        import customtkinter as ctk

        for period in ["7 Tage", "30 Tage", "180 Tage", "1 Jahr"]:
            btn = ctk.CTkButton(
                period_frame,
                text=period,
                font=("Segoe UI", FONT_SIZE_BODY, "bold"),
                width=100,
                height=BUTTON_HEIGHT_SECONDARY,
                corner_radius=10,
                command=lambda p=period: self._select_period(p),
            )
            btn.pack(side=tk.LEFT, padx=4)
            self._period_buttons[period] = btn
        self._update_period_button_colors()

        # Portrait-only metrics panel: summary numbers already computed in
        # _update_plot(), shown so the extra vertical height in portrait mode
        # isn't left empty. Built eagerly but not gridded until
        # set_portrait_layout(True) grids it.
        self.metrics_frame = tk.Frame(self, bg=COLOR_ROOT)
        for col in range(4):
            self.metrics_frame.grid_columnconfigure(col, weight=1)
        self.metrics_frame.grid_rowconfigure(0, weight=1)
        self._metric_tiles: dict[str, MetricTile] = {}
        tile_specs = [
            ("total", "Gesamt"),
            ("average", "Ø Tag"),
            ("peak", "Maximum"),
            ("last", "Letzter Tag"),
        ]
        for idx, (key, caption) in enumerate(tile_specs):
            tile = MetricTile(self.metrics_frame, caption)
            tile.grid(row=0, column=idx, sticky="nsew", padx=4, pady=4)
            self._metric_tiles[key] = tile

        plot_container = tk.Frame(self, bg=COLOR_ROOT)
        plot_container.grid(row=2, column=0, sticky="nsew", padx=PADDING_SECTION, pady=0)
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

        self.chart_frame = tk.Frame(self.card, bg=COLOR_CARD)
        self.chart_frame.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        # Without this, the matplotlib canvas widget's self-configured size
        # (set via fig.set_size_inches(..., forward=True)) can make Tk grow
        # chart_frame to fit it instead of the other way around, which then
        # cascades up and clips the tab against the window edge.
        self.chart_frame.grid_propagate(False)
        # Analog zur Puffer-Karte im Energie-Tab: canvas_widget haengt hier
        # per PACK (nicht grid) in chart_frame - grid_propagate(False)
        # allein schuetzt nicht zuverlaessig gegen ein pack-verwaltetes
        # Kind, das seine gewachsene Groesse nach oben durchreicht.
        self.chart_frame.pack_propagate(False)
        self.chart_frame.bind("<Configure>", lambda _event: self._schedule_canvas_resize())

        self.fig = Figure(figsize=(10.0, 4.8), dpi=100)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.fig.patch.set_alpha(1.0)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(COLOR_CARD)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.chart_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        try:
            self.canvas_widget.configure(bg=COLOR_ROOT, highlightthickness=0)
        except Exception:
            pass
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)
        self.canvas_widget.bind("<Configure>", self._on_canvas_resize)
        # <Map> fires reliably when this tab (previously grid_forgotten by
        # CTkTabview while another tab was active) becomes visible again -
        # <Configure> alone isn't a reliable signal for that transition.
        self.canvas_widget.bind("<Map>", self._on_canvas_resize)

        self.statusbar = tk.Label(
            self,
            text="",
            bg=COLOR_ROOT,
            fg=COLOR_SUBTEXT,
            font=("Segoe UI", 11),
            anchor="w",
            justify=tk.LEFT,
        )
        self.statusbar.grid(row=3, column=0, sticky="ew", padx=10, pady=(6, 10))
        # Same fix as HistoricalTab: this line ("Zeitraum: ... Gesamt: ...
        # Ø Tag: ... Maximum: ... Letzter Tag: ...") is long and a bare Label
        # doesn't wrap on its own - it just overflows the window on a narrow
        # portrait width. Keep wraplength synced to the real available width.
        self.statusbar.bind(
            "<Configure>",
            lambda e: self.statusbar.configure(wraplength=max(100, e.width - 4)),
        )

    def set_portrait_layout(self, portrait: bool) -> None:
        if hasattr(self, "_shell"):
            self._shell.set_portrait_layout(portrait)
        if portrait:
            self.grid_rowconfigure(1, minsize=100, weight=0)
            self.metrics_frame.grid(row=1, column=0, sticky="ew", padx=PADDING_SECTION, pady=(0, 8))
        else:
            self.metrics_frame.grid_remove()
            self.grid_rowconfigure(1, minsize=0, weight=0)
        # Row 1 (metrics panel) changing size changes how tall row 2 (the
        # chart) ends up - force a resize pass instead of hoping a
        # <Configure> event cascades down reliably.
        self.after(50, self._resize_canvas_now)
        self.after(300, self._resize_canvas_now)

    def _update_metric_tiles(
        self,
        total: float | None = None,
        average: float | None = None,
        peak: float | None = None,
        last_val: float | None = None,
    ) -> None:
        if not hasattr(self, "_metric_tiles"):
            return

        def _fmt(v: float | None) -> str:
            return f"{v:.1f} kWh" if v is not None and np.isfinite(v) else "--"

        self._metric_tiles["total"].set_value(_fmt(total))
        self._metric_tiles["average"].set_value(_fmt(average))
        self._metric_tiles["peak"].set_value(_fmt(peak))
        self._metric_tiles["last"].set_value(_fmt(last_val))

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
        self._schedule_canvas_resize()

    def _schedule_canvas_resize(self) -> None:
        try:
            if self._resize_job is not None:
                self.after_cancel(self._resize_job)
            self._resize_job = self.after_idle(self._resize_canvas_now)
        except Exception:
            pass

    def _resize_canvas_now(self) -> None:
        self._resize_job = None
        try:
            w = int(self.canvas_widget.winfo_width() or 0)
            h = int(self.canvas_widget.winfo_height() or 0)
            try:
                cf_w = int(self.chart_frame.winfo_width() or 0)
                cf_h = int(self.chart_frame.winfo_height() or 0)
                mapped = bool(self.canvas_widget.winfo_ismapped())
                logging.info(
                    "[TAGES-RESIZE] canvas=%sx%s chart_frame=%sx%s mapped=%s last_synced=%s",
                    w, h, cf_w, cf_h, mapped, self._last_synced_wh,
                )
            except Exception:
                pass
            if not self._sync_size(w, h):
                return
            self._apply_layout()
            self.canvas.draw_idle()
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
            self._last_synced_wh = (w, h)
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
            width = int(self.canvas_widget.winfo_width() or 0)
            compact = width < 720
            self.fig.subplots_adjust(
                left=0.13 if compact else 0.08,
                right=0.97,
                top=0.90,
                bottom=0.24 if compact else 0.18,
            )
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
            if not rows and self.datastore:
                rows = self.datastore.get_daily_totals(days=None)
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

        # When the current window has no data, the loader may return archive
        # data. Anchor the visible window to that archive instead of creating
        # an empty array of dates around today's system clock.
        end_day = max(ts.date() for ts, _value in data)
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
            self._update_metric_tiles()
            self._apply_layout()
            self.canvas.draw_idle()
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

        # Surface useful summary values below the chart instead of only the
        # last point, which is easy to miss on a tall portrait layout.
        last_val = None
        try:
            if len(raw) > 0:
                last_val = float(raw[-1][1])
        except Exception:
            last_val = None
        valid_values = ys[np.isfinite(ys)]
        if valid_values.size == 0:
            self.statusbar.configure(text=f"Zeitraum: {self._period_var.get()}")
            self._update_metric_tiles()
        else:
            total = float(np.sum(valid_values))
            average = float(np.mean(valid_values))
            peak = float(np.max(valid_values))
            last_text = f"  •  Letzter Tag: {last_val:.1f} kWh" if last_val is not None and np.isfinite(last_val) else ""
            self.statusbar.configure(
                text=(
                    f"Zeitraum: {self._period_var.get()}  •  Gesamt: {total:.1f} kWh"
                    f"  •  Ø Tag: {average:.1f} kWh  •  Maximum: {peak:.1f} kWh{last_text}"
                )
            )
            self._update_metric_tiles(total, average, peak, last_val)

        self._apply_layout()
        # Full draw (not draw_idle) to avoid ghost pixels / overlays.
        self.canvas.draw_idle()
        self._schedule_update()
