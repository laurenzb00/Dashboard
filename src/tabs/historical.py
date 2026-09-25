from __future__ import annotations

import logging
import threading
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
    FONT_SIZE_TITLE,
    FONT_SIZE_SUBTITLE,
    FONT_SIZE_BODY,
    BUTTON_HEIGHT_SECONDARY,
    PADDING_SECTION,
    emoji,
)
from ui.components.tab_shell import TabShell
from ui.components.metric_tile import MetricTile
from ui.components.ui_dispatch import UiQueuePumpMixin
from ui.views.chart_resize_mixin import MatplotlibCanvasResizeMixin


class HistoricalTab(MatplotlibCanvasResizeMixin, UiQueuePumpMixin, tk.Frame):
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
        # Zeitraum-Wechsel lud bisher synchron im Tk-Main-Thread ALLE
        # Rohdatenpunkte (bei 90d/180d/365d besonders spuerbar, siehe
        # _update_plot) und fror dabei die ganze App ein. Gleiches Worker-
        # Thread+Queue-Muster wie tabs/hue.py.
        self._init_ui_queue()
        self._update_token = 0

        # Only add to notebook if not using provided tab_frame
        if tab_frame is None:
            notebook.add(self, text=emoji("📈 Historie", "Historie"))
        else:
            self._shell = TabShell(tab_frame, "Historie", "Heizung und Temperaturen")
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
        # after_func=self.after: diese Klasse ist selbst ein tk.Frame,
        # nutzt also ihr eigenes .after() statt self.root.after() (dem
        # Default in UiQueuePumpMixin) - identisch zum bisherigen Verhalten.
        self._start_ui_pump(after_func=self.after)
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

        self.topbar_status = tk.Label(
            topbar,
            text="",
            bg=COLOR_ROOT,
            fg=COLOR_TITLE,
            font=("Segoe UI", FONT_SIZE_SUBTITLE, "bold"),
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
            font=("Segoe UI", FONT_SIZE_SUBTITLE),
        ).pack(side=tk.LEFT, padx=(0, 8))
        
        # Touch-freundliche Button-Gruppe mit CustomTkinter
        import customtkinter as ctk
        self._period_buttons = {}
        for period in ["24h", "7d", "30d", "90d", "180d", "365d"]:
            btn = ctk.CTkButton(
                period_frame,
                text=period,
                font=("Segoe UI", FONT_SIZE_BODY, "bold"),
                width=68,
                height=BUTTON_HEIGHT_SECONDARY,
                corner_radius=14,
                command=lambda p=period: self._select_period(p)
            )
            btn.pack(side=tk.LEFT, padx=4)
            self._period_buttons[period] = btn
        self._update_period_button_colors()

        # Portrait-only metrics panel: latest value per series, shown so the
        # extra vertical height in portrait mode isn't left empty. Built
        # eagerly but not gridded until set_portrait_layout(True) grids it.
        self.metrics_frame = tk.Frame(self, bg=COLOR_ROOT)
        for col in range(3):
            self.metrics_frame.grid_columnconfigure(col, weight=1)
        for row in range(2):
            self.metrics_frame.grid_rowconfigure(row, weight=1)
        self._metric_tiles: dict[str, MetricTile] = {}
        tile_specs = [
            ("top", "Puffer oben", COLOR_PRIMARY),
            ("mid", "Puffer mitte", COLOR_INFO),
            ("bot", "Puffer unten", COLOR_WARNING),
            ("kessel", "Kessel", COLOR_DANGER),
            ("warm", "Warmwasser", COLOR_SUCCESS),
            ("outdoor", "Außen", COLOR_SUBTEXT),
        ]
        for idx, (key, caption, color) in enumerate(tile_specs):
            tile = MetricTile(self.metrics_frame, caption, value_color=color)
            tile.grid(row=idx // 3, column=idx % 3, sticky="nsew", padx=4, pady=4)
            self._metric_tiles[key] = tile

        plot_container = tk.Frame(self, bg=COLOR_ROOT)
        plot_container.grid(row=2, column=0, sticky="nsew", padx=PADDING_SECTION, pady=0)
        plot_container.grid_rowconfigure(0, weight=1)
        plot_container.grid_columnconfigure(0, weight=1)

        # Neutral dark background (avoid bluish tint).
        self.card = tk.Frame(plot_container, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.card.grid(row=0, column=0, sticky="nsew")
        self.card.grid_rowconfigure(0, weight=1)
        self.card.grid_columnconfigure(0, weight=1)

        # Zusätzlicher Chart-Frame für Padding zwischen Card-Border und Canvas
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

        # Figur groß genug für vollständige Darstellung ohne Abschneiden
        self.fig = Figure(figsize=(10.0, 4.8), dpi=100)
        # Solid background prevents redraw artifacts that can look like "two diagrams".
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
        # A single-line Label with no wraplength doesn't shrink - on a narrow
        # portrait width the "Datenpunkte: ... | Temperaturbereich: ..." text
        # just overflows past the window edge and looks cut off. Keep
        # wraplength in sync with the actual available width instead.
        self.statusbar.bind(
            "<Configure>",
            lambda e: self.statusbar.configure(wraplength=max(100, e.width - 4)),
        )

    def set_portrait_layout(self, portrait: bool) -> None:
        if hasattr(self, "_shell"):
            self._shell.set_portrait_layout(portrait)
        if portrait:
            self.grid_rowconfigure(1, minsize=150, weight=0)
            self.metrics_frame.grid(row=1, column=0, sticky="ew", padx=PADDING_SECTION, pady=(0, 8))
        else:
            self.metrics_frame.grid_remove()
            self.grid_rowconfigure(1, minsize=0, weight=0)
        # Row 1 (metrics panel) changing size changes how tall row 2 (the
        # chart) ends up - force a resize pass instead of hoping a
        # <Configure> event cascades down reliably.
        self.after(50, self._resize_canvas_now)
        self.after(300, self._resize_canvas_now)

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

    # _start_ui_pump()/_post_ui(): siehe UiQueuePumpMixin
    # (ui/components/ui_dispatch.py) - war hier vorher unabhaengig
    # dupliziert, siehe Docstring dort fuer die Historie. Liveness-Check
    # nutzt automatisch winfo_exists() (kein self.alive auf dieser Klasse).

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
                btn.configure(fg_color=COLOR_PRIMARY, text_color="#ffffff", hover_color=COLOR_PRIMARY)
            else:
                btn.configure(fg_color=COLOR_BORDER, text_color=COLOR_TEXT, hover_color=COLOR_PRIMARY)

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

    # _sync_size(): siehe MatplotlibCanvasResizeMixin (ui/views/chart_resize_mixin.py) -
    # war zuvor hier, in tagesproduktion.py und in ui/views/energy_chart.py
    # dreifach wortgleich dupliziert.

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
                    "[HIST-RESIZE] canvas=%sx%s chart_frame=%sx%s mapped=%s last_synced=%s",
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

    def _apply_layout(self) -> None:
        try:
            width = int(self.canvas_widget.winfo_width() or 0)
            compact = width < 720
            self.fig.subplots_adjust(
                left=0.13 if compact else 0.08,
                right=0.97,
                top=0.88,
                bottom=0.24 if compact else 0.17,
            )
        except Exception:
            pass

    def _sync_figure_to_canvas(self) -> None:
        """Make sure the figure render buffer matches the widget size.

        If the renderer buffer is smaller than the Tk widget, old pixels can remain visible
        and look like a second plot underneath.
        """
        try:
            if not hasattr(self, "canvas_widget"):
                return False
            w = int(self.canvas_widget.winfo_width() or 0)
            h = int(self.canvas_widget.winfo_height() or 0)
            if not self._sync_size(w, h):
                return False
            self._apply_layout()
            return True
        except Exception:
            return False

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
        self.ax.tick_params(axis="both", which="major", labelsize=11, colors=COLOR_SUBTEXT, length=3, width=0.5)
        # X-Achsen-Labels mit Padding, damit sie nicht abgeschnitten werden
        self.ax.tick_params(axis="x", pad=5)
        self.ax.set_ylabel("°C", fontsize=9, color=COLOR_INFO, rotation=0, labelpad=10, va="center")
        try:
            self.ax.xaxis.get_offset_text().set_visible(False)
        except Exception:
            pass

    @staticmethod
    def _downsample_timeseries(times: list[datetime], series: dict[str, list[float]], bin_hours: int) -> tuple[list[datetime], dict[str, list[float]]]:
        if not times or bin_hours <= 1:
            return times, series

        bin_seconds = max(1, int(bin_hours * 3600))
        buckets: dict[int, dict[str, list[float]]] = {}

        for idx, ts in enumerate(times):
            try:
                bin_id = int(ts.timestamp()) // bin_seconds
            except Exception:
                continue
            if bin_id not in buckets:
                buckets[bin_id] = {k: [] for k in series.keys()}
            for key, values in series.items():
                val = values[idx]
                if val is None or (isinstance(val, float) and np.isnan(val)):
                    continue
                buckets[bin_id][key].append(float(val))

        if not buckets:
            return times, series

        ordered_bins = sorted(buckets.keys())
        binned_times = [datetime.fromtimestamp(bid * bin_seconds) for bid in ordered_bins]
        binned_series: dict[str, list[float]] = {k: [] for k in series.keys()}

        for bid in ordered_bins:
            bucket = buckets[bid]
            for key in series.keys():
                vals = bucket.get(key, [])
                if not vals:
                    binned_series[key].append(np.nan)
                else:
                    binned_series[key].append(float(np.mean(vals)))

        return binned_times, binned_series

    def _update_plot(self) -> None:
        hours = self._period_map.get(self._period_var.get(), 24)
        period_label = self._period_var.get() or f"{hours}h"

        # War bisher komplett synchron im Tk-Main-Thread: DB-Query OHNE Limit/
        # SQL-Aggregation + Plausibilitaets-Filterung ueber ALLE Rohpunkte,
        # bevor der Grossteil beim Downsampling unten wieder verworfen wird -
        # bei 90d/180d/365d der spuerbarste Bremsklotz der drei Chart-Tabs
        # ("Diagramme laden sehr langsam"), und die ganze App fror dabei mit
        # ein. Jetzt: Laden + reine Python-Aufbereitung im Worker-Thread, nur
        # noch die Matplotlib-/Tk-Anwendung des fertigen Ergebnisses in
        # _render_plot() auf dem Main-Thread.
        self._update_token += 1
        token = self._update_token

        def worker() -> None:
            now = datetime.now()
            cutoff = now - timedelta(hours=hours)

            # Fuer lange Zeitraeume wurden bisher IMMER ALLE Rohzeilen geladen
            # (bei 90d/180d/365d potenziell zehntausende) und erst danach in
            # Python per _downsample_timeseries() auf ein paar hundert Punkte
            # heruntergerechnet - der Grossteil der geladenen/geparsten Daten
            # wurde also nur weggeworfen. Jetzt macht SQLite das Bucketing+
            # Mitteln direkt in der Datenbank (gleiches Muster wie
            # _load_energy_flow() in ertrag.py); es werden von vornherein nur
            # noch die schon gemittelten Punkte geladen.
            bin_hours = 0
            if hours >= 720:
                bin_hours = 24
            elif hours >= 168:
                bin_hours = 3

            try:
                if bin_hours and self.datastore:
                    bucket_seconds = bin_hours * 3600
                    rows = self.datastore.get_heating_bucketed(hours=hours, bucket_seconds=bucket_seconds)
                    using_archive = False
                    if not rows:
                        rows = self.datastore.get_heating_bucketed(hours=None, bucket_seconds=bucket_seconds)
                        using_archive = bool(rows)
                else:
                    rows = self.datastore.get_recent_heating(hours=hours, limit=None) if self.datastore else []
                    using_archive = False
                    if not rows and self.datastore:
                        rows = self.datastore.get_recent_heating(hours=None, limit=None)
                        using_archive = bool(rows)
            except Exception:
                rows = []
                using_archive = False

            if using_archive and rows:
                archive_now = self._parse_ts(rows[-1].get("timestamp")) or now
                now = archive_now
                cutoff = now - timedelta(hours=hours)

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

                if bin_hours:
                    # Plausibilitaets-Filterung ist hier schon SQL-seitig passiert
                    # (siehe get_heating_bucketed) - Werte sind entweder ein
                    # gueltiger Mittelwert oder bereits None (= keine gueltigen
                    # Rohwerte in diesem Bucket).
                    for key in series.keys():
                        val = (row or {}).get(key)
                        series[key].append(float(val) if val is not None else np.nan)
                    continue

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

            times_sorted: list[datetime] = []
            ordered_series: dict[str, np.ndarray] = {}
            if times:
                order = np.argsort(np.array(times, dtype="datetime64[ns]"))
                times_sorted = [times[i] for i in order]

                def _ordered(arr):
                    a = np.array(arr, dtype=float)
                    return a[order]

                ordered_series = {key: _ordered(series[key]) for key in series.keys()}
                # Weiteres Downsampling ist jetzt ueberfluessig: bei bin_hours>0
                # liefert das SQL-Bucketing oben die Zielaufloesung schon direkt
                # aus der Datenbank.

            def apply() -> None:
                if token != self._update_token:
                    return
                self._render_plot(hours, period_label, now, cutoff, using_archive, times_sorted, ordered_series)

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _render_plot(
        self,
        hours: int,
        period_label: str,
        now: datetime,
        cutoff: datetime,
        using_archive: bool,
        times_sorted: list[datetime],
        ordered_series: dict[str, np.ndarray],
    ) -> None:
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

        # "Jetzt" bei 75% der Breite: zeige 33% zusätzliche Zeit in die Zukunft
        # Damit: cutoff bis now = 75% der Breite, now bis future_end = 25% der Breite
        future_extension = timedelta(hours=hours * 0.33)  # 1/3 der Vergangenheit = 25% der Gesamtbreite
        try:
            self.ax.set_xlim(cutoff, now + future_extension)
        except Exception:
            pass
        
        # Vertikale "Jetzt"-Linie bei ~75%
        try:
            self.ax.axvline(now, color=COLOR_PRIMARY, linewidth=1.8, linestyle='--', alpha=0.7, label='Jetzt', zorder=10)
        except Exception:
            pass

        if not times_sorted:
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
            self._update_metric_tiles(None)
            self._render_status(hours, 0, archive=using_archive)
            self._apply_layout()
            self.canvas.draw_idle()
            self._schedule_update()
            return

        # Sortierung + Downsampling passieren bereits im Worker-Thread (siehe
        # _update_plot) - times_sorted/ordered_series kommen hier fertig an.
        self._update_metric_tiles(ordered_series)

        plot_defs = [
            ("top", "Puffer oben", COLOR_PRIMARY, "-"),
            ("mid", "Puffer mitte", COLOR_INFO, "-"),
            ("bot", "Puffer unten", COLOR_WARNING, "-"),
            ("kessel", "Kessel", COLOR_DANGER, "-"),
            ("warm", "Warmwasser", COLOR_SUCCESS, "-"),
            ("outdoor", "Außen", COLOR_SUBTEXT, "--"),
        ]

        for key, label, color, style in plot_defs:
            self.ax.plot(times_sorted, ordered_series[key], label=label, color=color, linewidth=1.6, linestyle=style, alpha=0.95)

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
            fontsize=9,
            frameon=False,
            labelcolor=COLOR_SUBTEXT,
            ncol=2 if int(self.canvas_widget.winfo_width() or 0) < 900 else 3,
            handlelength=1.2,
            columnspacing=0.8,
            handletextpad=0.4,
        )

        # Keine automatischen X-Margins verwenden, da wir xlim explizit setzen
        try:
            self.ax.margins(x=0, y=0.05)
        except Exception:
            pass

        self._apply_layout()

        valid_values = [
            float(value)
            for values in ordered_series.values()
            for value in values
            if np.isfinite(value)
        ]
        self._render_status(hours, len(times_sorted), valid_values, archive=using_archive)
        self.canvas.draw_idle()
        self._schedule_update()

    def _update_metric_tiles(self, ordered_series: dict[str, np.ndarray] | None) -> None:
        if not hasattr(self, "_metric_tiles"):
            return
        for key, tile in self._metric_tiles.items():
            value = None
            if ordered_series is not None:
                arr = ordered_series.get(key)
                if arr is not None:
                    for v in reversed(arr):
                        if np.isfinite(v):
                            value = float(v)
                            break
            tile.set_value(f"{value:.1f} °C" if value is not None else "--")

    def _render_status(self, hours: int, points: int, valid_values: list[float] | None = None, archive: bool = False) -> None:
        # Show the selected period label instead of huge hour numbers.
        self.topbar_status.config(text=f"{self._period_var.get()}")
        summary = f"Datenpunkte: {points}"
        if valid_values:
            summary += f"  |  Temperaturbereich: {min(valid_values):.1f} bis {max(valid_values):.1f} °C"
        prefix = "Archivdaten  |  " if archive else ""
        self.statusbar.config(text=f"{prefix}Letztes Update: {datetime.now().strftime('%H:%M')}  |  {summary}")

    def update_data(self, data: dict) -> None:
        # Called by app update loop; keep for compatibility.
        self._latest_data = data

    def stop(self) -> None:
        if self._resize_job is not None:
            try:
                self.after_cancel(self._resize_job)
            except Exception:
                pass
            self._resize_job = None
        if self.after_job is not None:
            try:
                self.after_cancel(self.after_job)
            except Exception:
                pass
            self.after_job = None
        # Fix memory leak: properly close matplotlib figure
        try:
            import matplotlib.pyplot as plt
            if hasattr(self, 'fig') and self.fig is not None:
                plt.close(self.fig)
                self.fig = None
        except Exception:
            pass
