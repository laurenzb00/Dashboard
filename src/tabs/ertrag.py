from datetime import datetime, timedelta
from datetime import date
import tkinter as tk
from tkinter import ttk
import numpy as np
from core.datastore import get_shared_datastore
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
    emoji,
)
from ui.views.energy_chart import build_energy_chart

# Austrian energy price defaults (EUR/kWh)
_STROMPREIS_EUR_KWH = 0.25
_EINSPEISETARIF_EUR_KWH = 0.08


class ErtragTab:
    """PV-Ertrag pro Tag über längeren Zeitraum."""

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self._update_task_id = None  # Track scheduled update to prevent stacking

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

        self._period_var = tk.StringVar(value="7 Tage")
        self._period_map: dict[str, int] = {"7 Tage": 7, "30 Tage": 30, "180 Tage": 180, "1 Jahr": 365}

        # Layout like HistoricalTab: topbar + plot card + status line
        self.tab_frame.grid_rowconfigure(0, minsize=44)
        self.tab_frame.grid_rowconfigure(1, weight=1)
        self.tab_frame.grid_rowconfigure(2, minsize=26)
        self.tab_frame.grid_columnconfigure(0, weight=1)

        topbar = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        topbar.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))

        tk.Label(
            topbar,
            text="Energiefluss",
            bg=COLOR_ROOT,
            fg=COLOR_TITLE,
            font=("Segoe UI", 13, "bold"),
        ).pack(side=tk.LEFT, padx=(2, 10))

        # Zeitraum-Wahl: Touch-freundliche Buttons statt Combobox
        period_frame = tk.Frame(topbar, bg=COLOR_ROOT)
        period_frame.pack(side=tk.RIGHT, padx=(0, 12))
        tk.Label(period_frame, text="Zeitraum:", bg=COLOR_ROOT, fg=COLOR_SUBTEXT, font=("Segoe UI", 12)).pack(side=tk.LEFT, padx=(0, 8))
        
        # Touch-freundliche Button-Gruppe
        self._period_buttons = {}
        for period in ["7 Tage", "30 Tage", "180 Tage", "1 Jahr"]:
            btn = tk.Button(
                period_frame,
                text=period,
                font=("Segoe UI", 11, "bold"),
            width=8,
                height=1,
                relief=tk.FLAT,
                bg=COLOR_BORDER,
                fg=COLOR_TEXT,
                activebackground=COLOR_PRIMARY,
                activeforeground="#ffffff",
                borderwidth=0,
                command=lambda p=period: self._select_period(p)
            )
            btn.pack(side=tk.LEFT, padx=2)
            self._period_buttons[period] = btn
        self._update_period_button_colors()

        self.topbar_status = tk.Label(topbar, text="", bg=COLOR_ROOT, fg=COLOR_SUBTEXT, font=("Segoe UI", 12, "bold"))
        self.topbar_status.pack(side=tk.RIGHT)

        plot_container = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        plot_container.grid(row=1, column=0, sticky="nsew", padx=10, pady=0)
        plot_container.grid_rowconfigure(0, weight=1)
        plot_container.grid_columnconfigure(0, weight=1)

        # Neutral dark background (avoid bluish tint).
        self.card = tk.Frame(plot_container, bg=COLOR_ROOT, highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.card.grid(row=0, column=0, sticky="nsew")
        self.card.grid_rowconfigure(0, weight=1)
        self.card.grid_columnconfigure(0, weight=1)

        self.chart_frame = tk.Frame(self.card, bg=COLOR_ROOT)
        self.chart_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        # Modernes Energiefluss-Diagramm (PV area + Verbrauch line + Überschuss/Defizit).
        self.energy_chart = build_energy_chart(self.chart_frame, [])

        stats_frame = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        stats_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=(6, 10))
        self.var_sum = tk.StringVar(value="PV: -- kWh")
        self.var_avg = tk.StringVar(value="Verbrauch: -- kWh")
        self.var_last = tk.StringVar(value="Δ: -- kWh")
        self.var_autarkie = tk.StringVar(value="Autarkie: --%")
        self.var_ersparnis = tk.StringVar(value="Ersparnis: -- €")
        self.var_monthly = tk.StringVar(value="")
        tk.Label(stats_frame, textvariable=self.var_sum, bg=COLOR_ROOT, fg=COLOR_TEXT, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(stats_frame, textvariable=self.var_avg, bg=COLOR_ROOT, fg=COLOR_SUBTEXT, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(stats_frame, textvariable=self.var_last, bg=COLOR_ROOT, fg=COLOR_SUBTEXT, font=("Segoe UI", 10)).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(stats_frame, textvariable=self.var_autarkie, bg=COLOR_ROOT, fg=COLOR_SUCCESS, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(stats_frame, textvariable=self.var_ersparnis, bg=COLOR_ROOT, fg=COLOR_PRIMARY, font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT, padx=(0, 12))
        tk.Label(stats_frame, textvariable=self.var_monthly, bg=COLOR_ROOT, fg=COLOR_SUBTEXT, font=("Segoe UI", 10)).pack(side=tk.RIGHT, padx=(12, 0))

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
            fig = getattr(getattr(self, "energy_chart", None), "fig", None)
            if fig is not None:
                plt.close(fig)
        except Exception:
            pass

    def _sync_figure_to_canvas(self) -> None:
        """Ensure the figure render buffer matches the widget size.

        Otherwise, an older larger render can remain visible and look like a second plot.
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
            ts_raw = row.get("timestamp")
            try:
                ts = datetime.fromisoformat(str(ts_raw))
            except Exception:
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

    def _on_canvas_resize(self, event) -> None:
        try:
            w = max(1, int(getattr(event, "width", 1)))
            h = max(1, int(getattr(event, "height", 1)))
            dpi = float(self.fig.get_dpi() or 100.0)
            self.fig.set_size_inches(w / dpi, h / dpi, forward=False)
            self._apply_layout()
            self.canvas.draw()
        except Exception:
            pass

    def _apply_layout(self) -> None:
        # Stable layout with DPI-aware right margin.
        # Avoid tight_layout() here: it can shift the axes to the right depending
        # on renderer/tick extents (and makes the "clipped on the right" issue worse).
        try:
            # Derive UI scale from actual pixels-per-point.
            # This is more reliable across Windows/macOS/Linux (incl. Raspberry Pi)
            # than relying only on tk scaling.
            px_per_point = None
            try:
                px_per_point = float(self.root.winfo_fpixels("1p"))  # 1 point = 1/72 inch
            except Exception:
                px_per_point = None

            if px_per_point is None or not (0.5 <= px_per_point <= 6.0):
                # Fallback: tk scaling is roughly px/point on many Tk builds.
                try:
                    px_per_point = float(self.root.tk.call("tk", "scaling"))
                except Exception:
                    # Reasonable default for 96 DPI.
                    px_per_point = 96.0 / 72.0

            canvas_w = 0
            try:
                canvas_w = int(self.canvas_widget.winfo_width() or 0)
            except Exception:
                canvas_w = 0

            canvas_h = 0
            try:
                canvas_h = int(self.canvas_widget.winfo_height() or 0)
            except Exception:
                canvas_h = 0

            # Use pixel-based margins so Windows DPI scaling can't clip labels.
            # This also avoids the "plot shifts right" effect.
            if canvas_w > 0 and canvas_h > 0:
                # Margins are specified in points (scale with UI/font DPI), then converted to pixels.
                left_px = int(52 * px_per_point)
                right_px = int(72 * px_per_point)
                top_px = int(24 * px_per_point)
                bottom_px = int(44 * px_per_point)

                left = max(0.02, min(0.20, left_px / canvas_w))
                right = max(0.70, min(0.99, 1.0 - (right_px / canvas_w)))
                bottom = max(0.05, min(0.30, bottom_px / canvas_h))
                top = max(0.75, min(0.97, 1.0 - (top_px / canvas_h)))

                # Ensure a sane minimum plot area.
                if right - left < 0.60:
                    right = min(0.99, left + 0.60)

                self.fig.subplots_adjust(left=left, right=right, top=top, bottom=bottom)
            else:
                # Fallback
                self.fig.subplots_adjust(left=0.07, right=0.94, top=0.90, bottom=0.16)
        except Exception:
            pass

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
        self.ax.set_facecolor(COLOR_ROOT)
        for spine in ["top", "right"]:
            self.ax.spines[spine].set_visible(False)
        for spine in ["left", "bottom"]:
            self.ax.spines[spine].set_color(COLOR_BORDER)
            self.ax.spines[spine].set_linewidth(0.5)

        self.ax.grid(True, color=COLOR_BORDER, alpha=0.20, linewidth=0.6)
        # Slightly smaller x label size helps on high DPI.
        self.ax.tick_params(axis="y", which="major", labelsize=11, colors=COLOR_SUBTEXT, length=3, width=0.5)
        self.ax.tick_params(axis="x", which="major", labelsize=10, colors=COLOR_SUBTEXT, length=3, width=0.5)
        # Padding so the last x tick label doesn't get clipped.
        self.ax.tick_params(axis="x", pad=5)

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
                btn.configure(bg=COLOR_PRIMARY, fg="#ffffff", activebackground=COLOR_PRIMARY)
            else:
                btn.configure(bg=COLOR_BORDER, fg=COLOR_TEXT, activebackground=COLOR_PRIMARY)

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

            cutoff = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%d %H:%M:%S")
            bucket_seconds = max(60, int(bin_minutes) * 60)

            # Bucket by unixepoch seconds to avoid loading huge raw row counts for long windows.
            bucket_expr = (
                f"datetime((CAST(strftime('%s', datetime(timestamp)) AS INTEGER) / {bucket_seconds}) * {bucket_seconds}, 'unixepoch')"
            )
            sql = (
                "SELECT "
                + bucket_expr
                + " AS bucket_ts, "
                + "AVG(pv_power) AS pv_avg, "
                + "AVG(ABS(load_power)) AS load_avg, "
                + "AVG(grid_power) AS grid_avg "
                + "FROM fronius "
                + "WHERE datetime(timestamp) >= datetime(?) "
                + "GROUP BY bucket_ts "
                + "ORDER BY bucket_ts ASC"
            )

            rows = conn.execute(sql, (cutoff,)).fetchall()
        except Exception:
            return []

        out: list[dict] = []
        for row in rows:
            bucket_ts = row[0]
            pv_avg = row[1]
            load_avg = row[2]
            grid_avg = row[3]
            try:
                ts = datetime.fromisoformat(str(bucket_ts))
            except Exception:
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

        window_days = int(self._period_map.get(self._period_var.get(), 7) or 7)

        # Choose a coarse bin for long windows to keep UI fast.
        if window_days <= 7:
            bin_minutes = 10
        elif window_days <= 30:
            bin_minutes = 30
        elif window_days <= 180:
            bin_minutes = 180
        else:
            bin_minutes = 360

        data = self._load_energy_flow(window_days, bin_minutes=bin_minutes)

        last = data[-1] if data else None
        key = (
            len(data),
            (last.get("timestamp") if last else None),
            (float(last.get("pv_power")) if last else None),
            (float(last.get("house_consumption")) if last else None),
        )

        if key == self._last_key:
            self._update_task_id = self.root.after(60 * 1000, self._update_plot)
            return
        self._last_key = key

        self.energy_chart.render(data)

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

        diff_kwh = pv_kwh - load_kwh
        label = self._period_var.get()
        self.topbar_status.config(text=label)
        self.var_sum.set(f"PV ({label}): {pv_kwh:.1f} kWh")
        self.var_avg.set(f"Verbrauch: {load_kwh:.1f} kWh")
        self.var_last.set(f"Δ: {diff_kwh:+.1f} kWh")

        # Autarkiegrad: 1 - (Netzbezug / Gesamtverbrauch)
        if load_kwh > 0.1:
            autarkie_pct = max(0.0, min(100.0, (1.0 - grid_import_kwh / load_kwh) * 100.0))
            self.var_autarkie.set(f"Autarkie: {autarkie_pct:.0f}%")
        else:
            self.var_autarkie.set("Autarkie: --%")

        # Kostenersparnis: Eigenverbrauch × Strompreis + Einspeisung × Einspeisetarif
        eigenverbrauch_kwh = max(0.0, pv_kwh - grid_export_kwh)
        ersparnis_eur = eigenverbrauch_kwh * _STROMPREIS_EUR_KWH + grid_export_kwh * _EINSPEISETARIF_EUR_KWH
        if pv_kwh > 0.1:
            self.var_ersparnis.set(f"Ersparnis: {ersparnis_eur:.2f} €")
        else:
            self.var_ersparnis.set("Ersparnis: -- €")

        # Monatsvergleich (last 3 months)
        try:
            monthly = self.store.get_monthly_totals(months=3) if self.store else []
            if monthly:
                parts = []
                for m in monthly[-3:]:
                    month_str = m.get("month", "")[:7]  # YYYY-MM
                    kwh = float(m.get("pv_kwh", 0.0))
                    parts.append(f"{month_str}: {kwh:.0f} kWh")
                self.var_monthly.set(" | ".join(parts))
            else:
                self.var_monthly.set("")
        except Exception:
            self.var_monthly.set("")

        self._update_task_id = self.root.after(60 * 1000, self._update_plot)

    # Keine dynamische Größenanpassung nötig


