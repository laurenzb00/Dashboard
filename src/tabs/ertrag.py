from datetime import datetime, timedelta
from datetime import date
import tkinter as tk
from tkinter import ttk
import matplotlib.dates as mdates
import numpy as np
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
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_TITLE,
    emoji,
)
from ui.components.card import Card


class ErtragTab:
    """PV-Ertrag pro Tag über längeren Zeitraum."""

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self._update_task_id = None  # Track scheduled update to prevent stacking

        # Tab Frame - Use provided frame or create legacy one
        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
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

        tk.Label(
            topbar,
            text="PV-Ertrag",
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
        for period in ["30d", "90d", "180d", "365d"]:
            btn = tk.Button(
                period_frame,
                text=period,
                font=("Segoe UI", 11, "bold"),
                width=5,
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

        # Feste Größe und Layout für das Diagramm, da Bildschirm bekannt
        self.fig = Figure(figsize=(9.0, 4.5), dpi=100)
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

    def _update_plot(self):
        if not self.alive:
            return
        if not hasattr(self, "canvas") or self.canvas is None:
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
        raw = self._load_pv_daily(window_days)
        raw_load = self._load_load_daily(window_days)
        xs, ys = self._with_gaps_daily(raw, window_days)
        xs_load, ys_load = self._with_gaps_daily(raw_load, window_days)
        # Stable key: length + last non-nan sample
        last_non_nan = None
        if len(ys) > 0:
            try:
                idx = np.where(~np.isnan(ys))[0]
                if len(idx) > 0:
                    last_non_nan = (xs[int(idx[-1])], float(ys[int(idx[-1])]))
            except Exception:
                last_non_nan = None
        key = (len(xs), last_non_nan) if xs else ("empty",)
        
        # Nur redraw wenn sich Daten wirklich geändert haben
        if key == self._last_key:
            # Keine Änderung - nur neu einplanen, nicht redraw
            self._update_task_id = self.root.after(5 * 60 * 1000, self._update_plot)
            return
        
        self._last_key = key

        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor(COLOR_ROOT)
        self.fig.patch.set_alpha(1.0)
        self.ax.set_facecolor(COLOR_ROOT)
        self._style_axes()

        # Ensure renderer matches widget size before drawing.
        self._sync_figure_to_canvas()

        has_data = bool(len(ys) and np.any(~np.isnan(ys)))
        if has_data:
            if (
                not getattr(self, "_log_once", False)
                and __import__("os").environ.get("DASHBOARD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
            ):
                print(f"[ERTRAG] Matplotlib-Liniendiagramm aktiv (Tage={window_days})")
                self._log_once = True

            self.ax.plot(xs, ys, color=COLOR_PRIMARY, linewidth=1.8, alpha=0.95, label="PV-Ertrag")

            if len(ys_load) and np.any(~np.isnan(ys_load)):
                self.ax.fill_between(xs_load, 0, ys_load, color=COLOR_SUBTEXT, alpha=0.25, label="Verbrauch")
                self.ax.plot(xs_load, ys_load, color=COLOR_SUBTEXT, linewidth=1.2, alpha=0.6)

                if len(xs) == len(xs_load):
                    surplus = np.where((~np.isnan(ys)) & (~np.isnan(ys_load)) & (ys > ys_load), ys - ys_load, 0.0)
                    deficit = np.where((~np.isnan(ys)) & (~np.isnan(ys_load)) & (ys_load > ys), ys_load - ys, 0.0)
                    self.ax.fill_between(xs, ys_load, ys, where=surplus > 0, color=COLOR_SUCCESS, alpha=0.22, label="Ueberschuss")
                    self.ax.fill_between(xs, ys, ys_load, where=deficit > 0, color=COLOR_WARNING, alpha=0.18, label="Defizit")

            self.ax.set_ylabel("kWh/Tag", color=COLOR_SUBTEXT, fontsize=11, rotation=0, labelpad=18, va="center")
            locator = mdates.AutoDateLocator(minticks=4, maxticks=8)
            self.ax.xaxis.set_major_locator(locator)
            self.ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))
            try:
                self.ax.xaxis.get_offset_text().set_visible(False)
            except Exception:
                pass

            # Keep a bit of future padding so the last x tick label fits.
            try:
                if xs:
                    pad = max(
                        timedelta(hours=12),
                        timedelta(days=max(1, int(window_days)) * 0.111),
                    )
                    self.ax.set_xlim(xs[0], xs[-1] + pad)
            except Exception:
                pass

            # Prevent the last x tick label from being clipped at the right edge:
            # make labels extend to the left of their tick position.
            try:
                for lbl in self.ax.get_xticklabels():
                    lbl.set_ha("right")
            except Exception:
                pass

            self.ax.set_ylim(bottom=0)

            try:
                # A bit more horizontal padding so the last tick label fits.
                self.ax.margins(x=0.02)
            except Exception:
                pass

            total = float(np.nansum(ys))
            count = int(np.sum(~np.isnan(ys)))
            avg = total / max(1, count)
            # last non-nan
            last_idx = int(np.where(~np.isnan(ys))[0][-1])
            last_ts = xs[last_idx].strftime("%d.%m.%Y")
            last_val = float(ys[last_idx])
            self.topbar_status.config(text=f"{window_days}d")
            self.var_sum.set(f"Summe ({window_days}T): {total:.0f} kWh")
            self.var_avg.set(f"Ø Tag: {avg:.1f} kWh")
            self.var_last.set(f"{last_ts}: {last_val:.1f} kWh")
            if len(ys_load) and np.any(~np.isnan(ys_load)):
                self.ax.legend(loc="upper left", frameon=False, fontsize=9)
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
        self._apply_layout()
        
        try:
            if self.canvas.get_tk_widget().winfo_exists():
                self.canvas.draw()
        except Exception:
            pass
        
        # Nächster Update in 5 Minuten
        self._update_task_id = self.root.after(5 * 60 * 1000, self._update_plot)

    # Keine dynamische Größenanpassung nötig


