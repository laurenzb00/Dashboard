from datetime import datetime, timedelta
from datetime import date
import logging
import threading
import tkinter as tk
from tkinter import ttk
import customtkinter as ctk
import numpy as np
from core.datastore import get_shared_datastore
from core.time_utils import db_cutoff, db_ts_to_local
from core import energy_day
from core import pv_forecast
from ui.components.ui_dispatch import UiQueuePumpMixin
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_TITLE,
    FONT_SIZE_TITLE,
    FONT_SIZE_SUBTITLE,
    FONT_SIZE_BODY,
    BUTTON_HEIGHT_SECONDARY,
    PADDING_SECTION,
    emoji,
    get_safe_font,
)
from ui.views.energy_chart import build_energy_chart
from ui.components.tab_shell import TabShell
from ui.components.metric_tile import MetricTile

# Austrian energy price defaults (EUR/kWh)
_STROMPREIS_EUR_KWH = 0.25
_EINSPEISETARIF_EUR_KWH = 0.08


class ErtragTab(UiQueuePumpMixin):
    """PV-Ertrag pro Tag über längeren Zeitraum."""

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self._update_task_id = None  # Track scheduled update to prevent stacking
        # Zeitraum-Wechsel lief bisher synchron im Tk-Main-Thread (DB-Query +
        # kWh-Integration + Monats-Query) - die ganze App fror dabei kurz ein.
        # Gleiches Worker-Thread+Queue-Muster wie tabs/hue.py.
        self._init_ui_queue()
        self._update_token = 0
        self._start_ui_pump()

        # Tab Frame - Use provided frame or create legacy one
        if tab_frame is not None:
            # IMPORTANT: If the app reuses an existing tab frame, it may still
            # contain the old matplotlib canvas. Clear it to avoid showing two charts.
            try:
                for child in tab_frame.winfo_children():
                    child.destroy()
            except Exception:
                pass
            self.tab_frame = tab_frame
        else:
            self.tab_frame = tk.Frame(self.notebook, bg=COLOR_ROOT)
            self.notebook.add(self.tab_frame, text=emoji("🔆 Ertrag", "Ertrag"))

        self._shell = TabShell(self.tab_frame, "Ertrag", "Energiefluss, Verbrauch und Autarkie")
        self._shell.pack(fill=tk.BOTH, expand=True)
        self.tab_frame = self._shell.body

        self._period_var = tk.StringVar(value="Tag")
        self._period_map: dict[str, int] = {"Tag": 1, "7 Tage": 7, "30 Tage": 30, "180 Tage": 180, "1 Jahr": 365}
        self._day: date = date.today()
        self._portrait = False

        # Layout like HistoricalTab: topbar + portrait metrics panel + plot card + status line
        # self.tab_frame is TabShell.body, whose own __init__ sets row 0 to
        # weight=1 (for its original single-child layout) - reset it here or
        # the topbar row competes with the chart row for extra height and
        # ends up vertically centered with blank space above/below it.
        self.tab_frame.grid_rowconfigure(0, minsize=56, weight=0)
        # Portrait-only metrics panel; hidden (minsize=0) until set_portrait_layout(True).
        self.tab_frame.grid_rowconfigure(1, minsize=0, weight=0)
        self.tab_frame.grid_rowconfigure(2, weight=1)
        self.tab_frame.grid_columnconfigure(0, weight=1)

        topbar = tk.Frame(self.tab_frame, bg=COLOR_CARD)
        topbar.grid(row=0, column=0, sticky="ew", padx=PADDING_SECTION, pady=(PADDING_SECTION, 8))

        # Zeitraum-Wahl: Touch-freundliche Buttons statt Combobox
        period_frame = tk.Frame(topbar, bg=COLOR_CARD)
        period_frame.pack(side=tk.RIGHT, padx=(0, 12))
        tk.Label(period_frame, text="Zeitraum:", bg=COLOR_CARD, fg=COLOR_SUBTEXT, font=get_safe_font("Bahnschrift", FONT_SIZE_SUBTITLE)).pack(side=tk.LEFT, padx=(0, 10))
        
        # Touch-freundliche Button-Gruppe - war zuvor rohes tk.Button ohne
        # jede Rundung (einziger noch eckiger Zeitraum-Wahlschalter, waehrend
        # Historie/Tagesproduktion schon auf CTkButton mit der "Glas"-Rundung
        # liefen). Jetzt an dasselbe Muster angeglichen.
        self._period_buttons = {}
        for period in ["Tag", "7 Tage", "30 Tage", "180 Tage", "1 Jahr"]:
            btn = ctk.CTkButton(
                period_frame,
                text=period,
                font=get_safe_font("Bahnschrift", FONT_SIZE_BODY, "bold"),
                width=86 if period != "Tag" else 64,
                height=BUTTON_HEIGHT_SECONDARY,
                corner_radius=14,
                command=lambda p=period: self._select_period(p)
            )
            btn.pack(side=tk.LEFT, padx=4)
            self._period_buttons[period] = btn
        self._update_period_button_colors()

        self.topbar_status = tk.Label(topbar, text="", bg=COLOR_CARD, fg=COLOR_SUBTEXT, font=get_safe_font("Bahnschrift", FONT_SIZE_SUBTITLE, "bold"))
        self.topbar_status.pack(side=tk.RIGHT)

        # Portrait-only metrics panel (gleiches Muster wie historical.py/
        # tagesproduktion.py). Zeigte bisher nur 5 der 6 Kennzahlen - die
        # Monatsvergleich-Zahl lief separat ueber die stats_frame-Zeile
        # unten, die es NUR in ertrag.py gab (in historical.py/
        # tagesproduktion.py gibt es in Landscape gar keine zweite,
        # textbasierte Kennzahlen-Zeile). Diese Redundanz aus zwei
        # unterschiedlich gestalteten Leisten fuer dieselben Werte wirkte
        # uneinheitlich - jetzt gibt es nur noch die MetricTile-Reihe, dafuer
        # mit dem Monatsvergleich als 6. Kachel statt eigener Zeile.
        self.metrics_frame = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        self._metric_tiles: dict[str, MetricTile] = {}
        # Reihenfolge = Anzeige-Reihenfolge. Zweite/letzte Kachel wechseln je
        # nach Modus die Bedeutung (Tag: Prognose/Akku, Zeitraum: Ersparnis/Monat).
        tile_specs = [
            ("pv", "PV-Ertrag", COLOR_WARNING),
            ("slot2", "Prognose", COLOR_WARNING),
            ("verbrauch", "Verbrauch", COLOR_PRIMARY),
            ("bezug", "Netzbezug", COLOR_DANGER),
            ("einspeisung", "Einspeisung", COLOR_SUBTEXT),
            ("autarkie", "Autarkie", COLOR_SUCCESS),
            ("eigenverbrauch", "Eigenverbr.", COLOR_SUCCESS),
            ("slot8", "Akku", COLOR_SUCCESS),
        ]
        self._tile_order = [k for k, _, _ in tile_specs]
        for key, caption, color in tile_specs:
            self._metric_tiles[key] = MetricTile(self.metrics_frame, caption, value_color=color)
        self._layout_tiles(portrait=False)
        self.metrics_frame.grid(row=1, column=0, sticky="ew", padx=PADDING_SECTION, pady=(0, 8))

        plot_container = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        plot_container.grid(row=2, column=0, sticky="nsew", padx=PADDING_SECTION, pady=0)
        plot_container.grid_rowconfigure(0, weight=1)
        plot_container.grid_columnconfigure(0, weight=1)

        # Neutral dark background (avoid bluish tint).
        self.card = tk.Frame(plot_container, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.card.grid(row=0, column=0, sticky="nsew")
        self.card.grid_rowconfigure(0, weight=1)
        self.card.grid_columnconfigure(0, weight=1)

        self.day_nav = tk.Frame(self.card, bg=COLOR_CARD)
        self.day_nav.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        self.day_nav.grid_columnconfigure(1, weight=1)
        nav_font = get_safe_font("Bahnschrift", FONT_SIZE_BODY, "bold")
        self._btn_prev_day = ctk.CTkButton(
            self.day_nav, text="◀", width=56, height=BUTTON_HEIGHT_SECONDARY - 8, corner_radius=14,
            font=nav_font, fg_color=COLOR_BORDER, hover_color=COLOR_PRIMARY, text_color=COLOR_TEXT,
            command=lambda: self._shift_day(-1),
        )
        self._btn_prev_day.grid(row=0, column=0, sticky="w")
        self.day_label = tk.Label(self.day_nav, text="", bg=COLOR_CARD, fg=COLOR_TEXT,
                                  font=get_safe_font("Bahnschrift", FONT_SIZE_SUBTITLE, "bold"))
        self.day_label.grid(row=0, column=1)
        self.day_label.bind("<Button-1>", lambda _e: self._goto_today())
        self._btn_next_day = ctk.CTkButton(
            self.day_nav, text="▶", width=56, height=BUTTON_HEIGHT_SECONDARY - 8, corner_radius=14,
            font=nav_font, fg_color=COLOR_BORDER, hover_color=COLOR_PRIMARY, text_color=COLOR_TEXT,
            command=lambda: self._shift_day(1),
        )
        self._btn_next_day.grid(row=0, column=2, sticky="e")
        self.card.grid_rowconfigure(0, weight=0)
        self.card.grid_rowconfigure(1, weight=1)

        self.chart_frame = tk.Frame(self.card, bg=COLOR_CARD)
        self.chart_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
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

        # Modernes Energiefluss-Diagramm (PV area + Verbrauch line + Überschuss/Defizit).
        self.energy_chart = build_energy_chart(self.chart_frame, [])
        # Backstop resize: the canvas widget's own <Configure> can fire with a
        # stale size when this tab is built while hidden behind another tab.
        self.chart_frame.bind("<Configure>", lambda _event: self.energy_chart.refresh_size())
        # <Map> fires reliably when this tab (previously grid_forgotten by
        # CTkTabview while another tab was active) becomes visible again -
        # <Configure> alone isn't a reliable signal for that transition.
        self.energy_chart.canvas_widget.bind("<Map>", lambda _event: self.energy_chart.refresh_size())

        self._last_key = None
        self._apply_mode_ui()
        self.store = get_shared_datastore()
        self._update_task_id = self.root.after(100, self._update_plot)
        # Belt-and-suspenders: the chart has been observed stuck at its
        # figsize=(9.0, 4.5) default even though chart_frame ends up much
        # bigger, i.e. the passive <Configure>/<Map> bindings don't reliably
        # fire a real resize during the CTkTabview build/first-show dance.
        # A couple of extra delayed forced passes after startup catch that.
        self.root.after(500, self.energy_chart.refresh_size)
        self.root.after(1200, self.energy_chart.refresh_size)

    def set_portrait_layout(self, portrait: bool) -> None:
        if hasattr(self, "_shell"):
            self._shell.set_portrait_layout(portrait)
        self._portrait = bool(portrait)
        self._layout_tiles(portrait)
        # Row 1/3 changing size changes how tall row 2 (the chart) ends up -
        # force a resize pass instead of hoping a <Configure> event cascades
        # down reliably.
        if hasattr(self, "energy_chart"):
            self.root.after(50, self.energy_chart.refresh_size)
            self.root.after(300, self.energy_chart.refresh_size)

    def _set_tile(self, key: str, text: str, color: str | None = None) -> None:
        tile = getattr(self, "_metric_tiles", {}).get(key)
        if tile is not None:
            tile.set_value(text, color=color)

    def _set_caption(self, key: str, caption: str) -> None:
        tile = getattr(self, "_metric_tiles", {}).get(key)
        if tile is not None:
            tile.caption_label.configure(text=caption.upper())

    def _layout_tiles(self, portrait: bool) -> None:
        """Querformat: 1 Reihe mit 8 Kacheln, Hochformat: 2 Reihen mit je 4."""
        cols = 4 if portrait else 8
        for col in range(8):
            self.metrics_frame.grid_columnconfigure(col, weight=1 if col < cols else 0, uniform="tiles" if col < cols else "")
        for idx, key in enumerate(self._tile_order):
            self._metric_tiles[key].grid(row=idx // cols, column=idx % cols, sticky="nsew", padx=3, pady=3)
        self.tab_frame.grid_rowconfigure(1, minsize=150 if portrait else 76, weight=0)

    # --- Tagesansicht -------------------------------------------------------

    def _is_day_mode(self) -> bool:
        return self._period_var.get() == "Tag"

    def _apply_mode_ui(self) -> None:
        """Datumsleiste und Kachel-Beschriftungen an den Modus anpassen."""
        if self._is_day_mode():
            self.day_nav.grid()
            self._set_caption("slot2", "Prognose")
            self._set_caption("slot8", "Akku")
            self._update_day_label()
        else:
            self.day_nav.grid_remove()
            self._set_caption("slot2", "Ersparnis")
            self._set_caption("slot8", "Monat")

    def _update_day_label(self) -> None:
        wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][self._day.weekday()]
        today = date.today()
        if self._day == today:
            prefix = "Heute"
        elif self._day == today - timedelta(days=1):
            prefix = "Gestern"
        elif self._day == today + timedelta(days=1):
            prefix = "Morgen"
        else:
            prefix = wd
        self.day_label.config(text=f"{prefix}, {self._day:%d.%m.%Y}")
        # Vorwaerts nur bis morgen (dort gibt es nur die Prognose)
        state = "normal" if self._day < today + timedelta(days=1) else "disabled"
        self._btn_next_day.configure(state=state)

    def _shift_day(self, delta: int) -> None:
        new_day = self._day + timedelta(days=delta)
        if new_day > date.today() + timedelta(days=1):
            return
        self._day = new_day
        self._update_day_label()
        self._update_plot()

    def _goto_today(self) -> None:
        if self._day != date.today():
            self._day = date.today()
            self._update_day_label()
            self._update_plot()

    def _update_day_plot(self) -> None:
        day = self._day
        self._update_token += 1
        token = self._update_token

        def worker() -> None:
            try:
                samples = energy_day.load_day_samples(self.store, day) if self.store else []
                floor = energy_day.soc_floor(self.store) if self.store else None
                summary = energy_day.summarize(samples, floor)
                binned = energy_day.bin_samples(samples, minutes=5)
            except Exception:
                logging.exception("[ERTRAG] Tagesdaten konnten nicht geladen werden")
                samples, summary, binned = [], energy_day.DaySummary(), []
            try:
                forecast = pv_forecast.get_forecast(self.store)
            except Exception:
                logging.exception("[ERTRAG] PV-Prognose fehlgeschlagen")
                forecast = None
            fc_day = pv_forecast.forecast_for_day(forecast, day)
            skill_text = _forecast_status_text()
            key = (
                "tag", day.isoformat(), len(samples),
                samples[-1].ts.isoformat() if samples else None,
                len(fc_day), round(sum(v for _, v in fc_day), 3),
            )

            def apply() -> None:
                if not self.alive or token != self._update_token:
                    return
                self._skill_text = skill_text
                from core.perf_monitor import timed
                with timed("ertrag.anzeigen", min_ms=100):
                    self._apply_day_result(day, key, binned, summary, fc_day)

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_day_result(self, day, key, binned, summary, fc_day) -> None:
        if key != self._last_key or day == date.today():
            self._last_key = key
            start = datetime.combine(day, datetime.min.time())
            self.energy_chart.render(
                binned,
                forecast=fc_day,
                empty_spans=summary.empty_spans,
                x_range=(start, start + timedelta(days=1)),
                show_soc=True,
            )
        else:
            self.energy_chart.refresh_size()

        has_data = summary.samples > 0
        dash = "--"
        self.topbar_status.config(text=getattr(self, "_skill_text", ""))
        self._set_tile("pv", f"{summary.pv_kwh:.1f} kWh" if has_data else dash)
        fc_kwh = pv_forecast.forecast_kwh(fc_day)
        if fc_kwh is None:
            self._set_tile("slot2", dash, color=COLOR_SUBTEXT)
        else:
            self._set_tile("slot2", f"{fc_kwh:.1f} kWh", color=COLOR_WARNING)
        self._set_tile("verbrauch", f"{summary.load_kwh:.1f} kWh" if has_data else dash)
        self._set_tile("bezug", f"{summary.import_kwh:.1f} kWh" if has_data else dash)
        self._set_tile("einspeisung", f"{summary.export_kwh:.1f} kWh" if has_data else dash)
        aut = summary.autarky_pct
        self._set_tile("autarkie", f"{aut:.0f}%" if aut is not None else "--%")
        eig = summary.self_consumption_pct
        self._set_tile("eigenverbrauch", f"{eig:.0f}%" if eig is not None else "--%")
        if summary.empty_at is not None:
            self._set_tile("slot8", f"leer {summary.empty_at:%H:%M}", color=COLOR_DANGER)
        elif summary.full_at is not None:
            self._set_tile("slot8", f"voll {summary.full_at:%H:%M}", color=COLOR_SUCCESS)
        elif summary.soc_min is not None:
            self._set_tile("slot8", f"min {summary.soc_min:.0f}%", color=COLOR_SUCCESS)
        else:
            self._set_tile("slot8", dash, color=COLOR_SUBTEXT)

        self._update_task_id = self.root.after(60 * 1000, self._update_plot)

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
            fig = getattr(getattr(self, "energy_chart", None), "fig", None)
            if fig is not None:
                plt.close(fig)
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

    def _load_load_daily(self, days: int = 365):
        cutoff = datetime.now() - timedelta(days=days)
        rows = self.store.get_recent_fronius(hours=days * 24, limit=None) if self.store else []
        return self._integrate_daily_power(rows, cutoff)

    @staticmethod
    def _integrate_daily_power(rows: list[dict], cutoff: datetime) -> list[tuple[datetime, float]]:
        buckets: dict[date, float] = {}
        prev_ts = None
        prev_power = None

        for row in rows:
            ts = db_ts_to_local(row.get("timestamp"))
            if ts is None:
                continue
            if ts < cutoff:
                continue
            load_kw = row.get("load")
            if load_kw is None:
                continue
            try:
                power = abs(float(load_kw))
            except Exception:
                continue

            if prev_ts is not None and prev_power is not None:
                delta_h = (ts - prev_ts).total_seconds() / 3600
                if 0 < delta_h <= 6:
                    ErtragTab._distribute_daily_energy(buckets, prev_ts, prev_power, ts, power)

            prev_ts, prev_power = ts, power

        out = [(datetime.combine(day, datetime.min.time()), kwh) for day, kwh in buckets.items()]
        out.sort(key=lambda t: t[0])
        return out

    @staticmethod
    def _distribute_daily_energy(
        buckets: dict[date, float],
        start_ts: datetime,
        start_power: float,
        end_ts: datetime,
        end_power: float,
    ) -> None:
        def _add(day_ts: datetime, p_start: float, p_end: float, hours: float) -> None:
            if hours <= 0:
                return
            energy = (p_start + p_end) / 2.0 * hours
            day_key = day_ts.date()
            buckets[day_key] = buckets.get(day_key, 0.0) + energy

        current_ts = start_ts
        current_power = start_power
        final_ts = end_ts
        final_power = end_power

        while current_ts.date() != final_ts.date():
            boundary = datetime.combine(current_ts.date() + timedelta(days=1), datetime.min.time())
            total_hours = (final_ts - current_ts).total_seconds() / 3600
            if total_hours <= 0:
                return
            span_hours = (boundary - current_ts).total_seconds() / 3600
            if span_hours <= 0:
                break
            ratio = span_hours / total_hours
            boundary_power = current_power + (final_power - current_power) * ratio
            _add(current_ts, current_power, boundary_power, span_hours)
            current_ts = boundary
            current_power = boundary_power

        remaining_hours = (final_ts - current_ts).total_seconds() / 3600
        if remaining_hours > 0:
            _add(current_ts, current_power, final_power, remaining_hours)

    @staticmethod
    def _date_range(start: date, end: date):
        cur = start
        one = timedelta(days=1)
        while cur <= end:
            yield cur
            cur = cur + one

    def _with_gaps_daily(self, data: list[tuple[datetime, float]], window_days: int) -> tuple[list[datetime], np.ndarray]:
        """Return dense daily series across the selected window, inserting NaN for missing days."""
        if not data:
            return ([], np.array([], dtype=float))

        # Ensure chronological order
        data_sorted = sorted(data, key=lambda t: t[0])

        end_day = datetime.now().date()
        start_day = end_day - timedelta(days=max(1, int(window_days)) - 1)

        by_day: dict[date, float] = {}
        for ts, val in data_sorted:
            by_day[ts.date()] = float(val)

        xs: list[datetime] = []
        ys: list[float] = []
        for d in self._date_range(start_day, end_day):
            xs.append(datetime.combine(d, datetime.min.time()))
            ys.append(by_day.get(d, float("nan")))

        return xs, np.array(ys, dtype=float)

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

    # _start_ui_pump()/_post_ui(): siehe UiQueuePumpMixin
    # (ui/components/ui_dispatch.py) - war hier vorher unabhaengig
    # dupliziert, siehe Docstring dort fuer die Historie.

    def _select_period(self, period: str) -> None:
        """Wechselt Zeitraum und aktualisiert Button-Farben."""
        self._period_var.set(period)
        self._update_period_button_colors()
        self._last_key = None
        self._apply_mode_ui()
        self._update_plot()

    def _update_period_button_colors(self) -> None:
        """Aktualisiert Button-Farben basierend auf aktuellem Zeitraum."""
        current = self._period_var.get()
        for period, btn in self._period_buttons.items():
            if period == current:
                btn.configure(fg_color=COLOR_PRIMARY, text_color="#ffffff", hover_color=COLOR_PRIMARY)
            else:
                btn.configure(fg_color=COLOR_BORDER, text_color=COLOR_TEXT, hover_color=COLOR_PRIMARY)

    def _load_energy_flow(self, days: int, bin_minutes: int = 10) -> list[dict]:
        """Load PV power + house consumption power + grid power for the last N days.

        Output schema matches build_energy_chart():
          - timestamp: datetime
          - pv_power: float (kW)
          - house_consumption: float (kW)
          - grid_power: float (kW, + = import, - = export)
        """
        if not self.store:
            return []

        try:
            conn = getattr(self.store, "conn", None)
            if conn is None:
                return []

            cutoff = db_cutoff(days=int(days))  # UTC, wie die DB-Zeitstempel
            bucket_seconds = max(60, int(bin_minutes) * 60)

            # Bucket by unixepoch seconds to avoid loading huge raw row counts for long windows.
            bucket_expr = (
                f"datetime((CAST(strftime('%s', datetime(timestamp)) AS INTEGER) / {bucket_seconds}) * {bucket_seconds}, 'unixepoch')"
            )
            # War "WHERE datetime(timestamp) >= datetime(?)" - das datetime()-
            # Wrapping der indizierten timestamp-Spalte (siehe idx_fronius_ts
            # in datastore.py) macht das Predicate nicht-sargable: SQLite
            # kann den Index dafuer nicht nutzen und scannt bei jedem
            # Zeitraum-Wechsel die komplette Tabelle. Andere Queries in
            # datastore.py (z.B. get_recent_fronius) vergleichen denselben
            # "%Y-%m-%d %H:%M:%S"-Cutoff bereits direkt/unwrapped gegen
            # timestamp - hier genauso, um den Index nutzen zu koennen.
            sql = (
                "SELECT "
                + bucket_expr
                + " AS bucket_ts, "
                + "AVG(pv_power) AS pv_avg, "
                + "AVG(ABS(load_power)) AS load_avg, "
                + "AVG(grid_power) AS grid_avg "
                + "FROM fronius "
                + "WHERE timestamp >= ? "
                + "GROUP BY bucket_ts "
                + "ORDER BY bucket_ts ASC"
            )

            rows = conn.execute(sql, (cutoff,)).fetchall()
            if not rows:
                sql = (
                    "SELECT " + bucket_expr + " AS bucket_ts, "
                    "AVG(pv_power) AS pv_avg, AVG(ABS(load_power)) AS load_avg, AVG(grid_power) AS grid_avg "
                    "FROM fronius GROUP BY bucket_ts ORDER BY bucket_ts ASC"
                )
                rows = conn.execute(sql).fetchall()
        except Exception:
            return []

        out: list[dict] = []
        for row in rows:
            bucket_ts = row[0]
            pv_avg = row[1]
            load_avg = row[2]
            grid_avg = row[3]
            ts = db_ts_to_local(bucket_ts)  # SQLite liefert UTC
            if ts is None:
                continue

            try:
                pv_kw = float(pv_avg) if pv_avg is not None else 0.0
            except Exception:
                pv_kw = 0.0
            try:
                load_kw = float(load_avg) if load_avg is not None else 0.0
            except Exception:
                load_kw = 0.0
            try:
                grid_kw = float(grid_avg) if grid_avg is not None else 0.0
            except Exception:
                grid_kw = 0.0

            # Backward compatibility: some sources may still store W.
            if pv_kw > 200.0:
                pv_kw = pv_kw / 1000.0
            if load_kw > 200.0:
                load_kw = load_kw / 1000.0
            if abs(grid_kw) > 200.0:
                grid_kw = grid_kw / 1000.0

            pv_kw = max(0.0, pv_kw)
            load_kw = max(0.0, load_kw)
            out.append({"timestamp": ts, "pv_power": pv_kw, "house_consumption": load_kw, "grid_power": grid_kw})
        return out

    def _update_plot(self):
        if not self.alive:
            return
        if getattr(self, "energy_chart", None) is None:
            return

        if self._update_task_id:
            try:
                self.root.after_cancel(self._update_task_id)
            except Exception:
                pass
            self._update_task_id = None

        if self._is_day_mode():
            self._update_day_plot()
            return

        window_days = int(self._period_map.get(self._period_var.get(), 7) or 7)

        # Choose a coarse bin for long windows to keep UI fast.
        # War bei 180 Tagen (180min-Bins, ~1440 Punkte) und 1 Jahr (360min-
        # Bins, ~1460 Punkte) faktisch unlesbar: die Leistung (kW) schwankt
        # jeden Tag zwischen 0 (Nacht) und Spitzenwert (Mittag) - bei
        # mehreren hundert Tagen in einen ~950px breiten Chart gequetscht
        # ergibt das nur noch einen dichten Kamm aus Zacken statt eines
        # erkennbaren Verlaufs (mit synthetischen Testdaten nachgebaut und
        # verglichen). Ab 90 Tagen jetzt Tages-Mittelwerte (1440min-Bins) -
        # das glaettet den taeglichen Tag/Nacht-Zyklus komplett weg und
        # zeigt stattdessen den eigentlich interessanten saisonalen Trend
        # als glatte Linie (siehe auch Tagesproduktion-Tab, der PV-Ertrag
        # bei langen Zeitraeumen ebenfalls pro Tag statt pro Leistungswert
        # darstellt).
        if window_days <= 7:
            bin_minutes = 10
        elif window_days <= 30:
            bin_minutes = 30
        else:
            bin_minutes = 1440

        # War bisher alles synchron hier im Tk-Main-Thread (DB-Query in
        # _load_energy_flow, kWh-Integration, Monats-Query) - dadurch fror
        # bei jedem Zeitraum-Wechsel kurz die GESAMTE App ein, nicht nur der
        # Chart. Jetzt: Query + reine Python-Berechnung im Worker-Thread,
        # nur noch die Tk-/Matplotlib-Anwendung des Ergebnisses in apply().
        self._update_token += 1
        token = self._update_token

        def worker() -> None:
            data = self._load_energy_flow(window_days, bin_minutes=bin_minutes)

            last = data[-1] if data else None
            key = (
                len(data),
                (last.get("timestamp") if last else None),
                (float(last.get("pv_power")) if last else None),
                (float(last.get("house_consumption")) if last else None),
            )

            # Integrate kW to kWh over the visible window (trapezoid), for the footer stats.
            pv_kwh = 0.0
            load_kwh = 0.0
            grid_import_kwh = 0.0
            grid_export_kwh = 0.0
            try:
                if len(data) >= 2:
                    max_gap_h = max(6.0, (float(bin_minutes) / 60.0) * 4.0)
                    for a, b in zip(data, data[1:]):
                        ta = a.get("timestamp")
                        tb = b.get("timestamp")
                        if not isinstance(ta, datetime) or not isinstance(tb, datetime):
                            continue
                        dt_h = (tb - ta).total_seconds() / 3600.0
                        if dt_h <= 0 or dt_h > max_gap_h:
                            continue
                        pv_kwh += (float(a.get("pv_power", 0.0)) + float(b.get("pv_power", 0.0))) / 2.0 * dt_h
                        load_kwh += (float(a.get("house_consumption", 0.0)) + float(b.get("house_consumption", 0.0))) / 2.0 * dt_h
                        # Grid: positive = import, negative = export
                        g_a = float(a.get("grid_power", 0.0))
                        g_b = float(b.get("grid_power", 0.0))
                        g_avg = (g_a + g_b) / 2.0
                        if g_avg > 0:
                            grid_import_kwh += g_avg * dt_h
                        else:
                            grid_export_kwh += abs(g_avg) * dt_h
            except Exception:
                pv_kwh = 0.0
                load_kwh = 0.0
                grid_import_kwh = 0.0
                grid_export_kwh = 0.0

            # Monatsvergleich (last 3 months)
            try:
                monthly = self.store.get_monthly_totals(months=3) if self.store else []
            except Exception:
                monthly = []

            def apply() -> None:
                if not self.alive or token != self._update_token:
                    return
                self._apply_update_result(
                    data, key, bin_minutes, pv_kwh, load_kwh, grid_import_kwh, grid_export_kwh, monthly
                )

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _apply_update_result(
        self, data, key, bin_minutes, pv_kwh, load_kwh, grid_import_kwh, grid_export_kwh, monthly
    ) -> None:
        if key == self._last_key:
            # Data unchanged, but still re-sync the figure size every tick.
            # render() would normally do this, but it's skipped below when
            # the key matches - without this, a chart first drawn at a
            # stale/small size (e.g. tab built hidden behind another
            # CTkTabview tab) would never get resized again once the PV
            # data stops changing between polls.
            self.energy_chart.refresh_size()
            self._update_task_id = self.root.after(60 * 1000, self._update_plot)
            return
        self._last_key = key

        self.energy_chart.render(data)

        self.topbar_status.config(text="")
        self._set_tile("pv", f"{pv_kwh:.1f} kWh")
        self._set_tile("verbrauch", f"{load_kwh:.1f} kWh")
        self._set_tile("bezug", f"{grid_import_kwh:.1f} kWh")
        self._set_tile("einspeisung", f"{grid_export_kwh:.1f} kWh")

        # Autarkiegrad: 1 - (Netzbezug / Gesamtverbrauch)
        if load_kwh > 0.1:
            autarkie_pct = max(0.0, min(100.0, (1.0 - grid_import_kwh / load_kwh) * 100.0))
            self._set_tile("autarkie", f"{autarkie_pct:.0f}%")
        else:
            self._set_tile("autarkie", "--%")

        # Eigenverbrauchsquote: Anteil des PV-Stroms, der selbst genutzt wurde
        eigenverbrauch_kwh = max(0.0, pv_kwh - grid_export_kwh)
        if pv_kwh > 0.1:
            self._set_tile("eigenverbrauch", f"{min(100.0, eigenverbrauch_kwh / pv_kwh * 100.0):.0f}%")
        else:
            self._set_tile("eigenverbrauch", "--%")

        # Kostenersparnis: Eigenverbrauch × Strompreis + Einspeisung × Einspeisetarif
        ersparnis_eur = eigenverbrauch_kwh * _STROMPREIS_EUR_KWH + grid_export_kwh * _EINSPEISETARIF_EUR_KWH
        if pv_kwh > 0.1:
            self._set_tile("slot2", f"{ersparnis_eur:.2f} €", color=COLOR_PRIMARY)
        else:
            self._set_tile("slot2", "-- €", color=COLOR_PRIMARY)

        # Laufender Monat (PV) im Vergleich zum Vormonat
        if monthly:
            cur = float(monthly[-1].get("pv_kwh", 0.0))
            text = f"{cur:.0f} kWh"
            if len(monthly) >= 2:
                prev = float(monthly[-2].get("pv_kwh", 0.0))
                if prev > 0.5:
                    text += f" ({(cur / prev - 1.0) * 100.0:+.0f}%)"
            self._set_tile("slot8", text, color=COLOR_WARNING)
        else:
            self._set_tile("slot8", "--", color=COLOR_SUBTEXT)

        self._update_task_id = self.root.after(60 * 1000, self._update_plot)

    # Keine dynamische Größenanpassung nötig




def _forecast_status_text() -> str:
    """Kopfzeile: Treffsicherheit der Vortagsprognose + PV-Warnung der letzten 24 h."""
    parts = []
    try:
        from core import alerts
        for ts, kind, title, _msg in alerts.recent_alerts(24):
            if kind == "pv":
                parts.append(f"⚠ {title} ({datetime.fromtimestamp(ts):%H:%M})")
                break
    except Exception:
        pass
    try:
        from core import forecast_log
        sk = forecast_log.pv_skill(days=14)
        if sk and sk["days"] >= 3:
            tend = ""
            if abs(sk["bias_pct"]) >= 5:
                tend = " · eher zu hoch" if sk["bias_pct"] > 0 else " · eher zu niedrig"
            parts.append(f"Prognose vom Vortag: Ø {sk['mape_pct']:.0f} % daneben ({sk['days']} Tage){tend}")
        else:
            parts.append("Prognose-Güte: wird gesammelt")
    except Exception:
        pass
    return "   ".join(parts)
