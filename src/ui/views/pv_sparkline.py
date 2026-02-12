import json
import os
from pathlib import Path
import time
from collections import deque
from datetime import datetime, timedelta

import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
import matplotlib.dates as mdates
from matplotlib.ticker import FixedLocator

from core.datastore import get_shared_datastore
from core.schema import PV_POWER_KW
from ui.styles import (
    COLOR_ROOT,
    COLOR_BORDER,
    COLOR_SUBTEXT,
    COLOR_TEXT,
    COLOR_TITLE,
    COLOR_SUCCESS,
    COLOR_INFO,
)

DEBUG_LOG = os.environ.get("DASHBOARD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")


class PVSparklineView(tk.Frame):
    """PV + Outdoor temperature sparkline for the energy tab."""

    def __init__(self, parent: tk.Widget, datastore=None):
        super().__init__(parent, bg=COLOR_ROOT)
        self.datastore = datastore or get_shared_datastore()

        self._spark_history_pv = deque(maxlen=2000)
        self._spark_history_temp = deque(maxlen=2000)
        self._last_spark_sample_ts = 0.0
        self._spark_cache_pv = []
        self._spark_cache_temp = []
        self._spark_cache_ts = 0.0
        self._spark_cache_file = Path(__file__).resolve().parents[3] / "data" / "sparkline_cache.json"

        header = tk.Frame(self, bg=COLOR_ROOT)
        header.pack(fill=tk.X, padx=6, pady=(4, 2))
        self._header = header
        tk.Label(
            header,
            text="PV & Aussentemp. (24h)",
            fg=COLOR_TITLE,
            bg=COLOR_ROOT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w")

        self.spark_fig = Figure(figsize=(7.5, 1.8), dpi=100)
        self.spark_fig.patch.set_facecolor(COLOR_ROOT)
        self.spark_ax = self.spark_fig.add_subplot(111)
        self.spark_ax.set_facecolor(COLOR_ROOT)
        self.spark_canvas = FigureCanvasTkAgg(self.spark_fig, master=self)
        self._canvas_widget = self.spark_canvas.get_tk_widget()
        self._canvas_widget.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self.spark_ax.tick_params(axis='both', which='major', labelsize=9, colors=COLOR_SUBTEXT)
        self.spark_ax.set_axisbelow(True)
        self.spark_ax.grid(True, alpha=0.12)
        self.spark_fig.subplots_adjust(left=0.06, right=0.98, top=0.90, bottom=0.22)

        # On startup, render from persisted cache immediately so the sparkline
        # isn't empty after a restart. Then refresh from DB shortly after.
        try:
            cached = self._load_cache() or {}
            self._spark_cache_pv = list(cached.get("pv", []) or [])
            self._spark_cache_temp = list(cached.get("temp", []) or [])
            # Mark cache as fresh for the first draw (avoid DB query during UI init).
            if self._spark_cache_pv or self._spark_cache_temp:
                self._spark_cache_ts = time.time()
        except Exception:
            pass

        def _initial_draw() -> None:
            try:
                self._update_sparkline()
            except Exception:
                pass

        def _refresh_from_db() -> None:
            try:
                # Force DB refresh on next update.
                self._spark_cache_ts = 0.0
                self._update_sparkline()
            except Exception:
                pass

        self.after(150, _initial_draw)
        self.after(4000, _refresh_from_db)

    def set_target_height(self, total_px: int) -> None:
        """Set a compact total height for the sparkline row.

        This is used on 600px-tall screens so the energy/buffer cards have enough
        room and nothing gets clipped.
        """
        try:
            total_px = int(total_px)
        except Exception:
            return
        if total_px <= 0:
            return

        try:
            # Prevent child widgets from forcing the parent to grow.
            self.pack_propagate(False)
        except Exception:
            pass
        try:
            self.configure(height=total_px)
        except Exception:
            pass

        try:
            header_h = int(getattr(self, "_header", None).winfo_height() or 0)
        except Exception:
            header_h = 0

        # Approximate remaining height for the matplotlib canvas.
        canvas_px = max(60, total_px - max(18, header_h) - 14)

        try:
            self._canvas_widget.configure(height=canvas_px)
        except Exception:
            pass

        try:
            dpi = float(self.spark_fig.get_dpi() or 100)
            cur_w_in, _cur_h_in = self.spark_fig.get_size_inches()
            new_h_in = max(0.75, canvas_px / dpi)
            self.spark_fig.set_size_inches(cur_w_in, new_h_in, forward=True)

            # Tighter margins in compact mode.
            self.spark_fig.subplots_adjust(left=0.06, right=0.98, top=0.90, bottom=0.26)
            self.spark_canvas.draw_idle()
        except Exception:
            pass

    def update_data(self, data: dict) -> None:
        self._record_spark_sample(data)
        now = time.monotonic()
        last = getattr(self, "_last_redraw_ts", 0.0)
        if now - last < 5.0:
            return
        self._last_redraw_ts = now
        self._update_sparkline()

    def rebuild_cache_now(self) -> None:
        """Force a DB refresh and rewrite the persisted sparkline cache."""
        try:
            self._spark_cache_ts = 0.0
            self._update_sparkline()
        except Exception:
            pass

    def _record_spark_sample(self, data: dict) -> None:
        now_ts = time.time()
        if now_ts - self._last_spark_sample_ts < 60.0:
            return
        self._last_spark_sample_ts = now_ts
        sample_time = datetime.now()
        pv_kw = self._safe_float(data.get(PV_POWER_KW))
        if pv_kw is not None:
            self._spark_history_pv.append((sample_time, max(0.0, pv_kw)))
        outdoor = self._safe_float(data.get('outdoor'))
        if outdoor is not None:
            self._spark_history_temp.append((sample_time, outdoor))

    def _update_sparkline(self) -> None:
        refresh_needed = (time.time() - self._spark_cache_ts) > 60.0
        if refresh_needed:
            prev_pv = list(self._spark_cache_pv)
            prev_temp = list(self._spark_cache_temp)
            try:
                pv_series_db = self._load_pv_series(hours=48, bin_minutes=15)
            except Exception as exc:
                if DEBUG_LOG:
                    print(f"[SPARKLINE] _load_pv_series error: {exc}")
                pv_series_db = []
            try:
                temp_series_db = self._load_outdoor_temp_series(hours=48, bin_minutes=15)
            except Exception as exc:
                if DEBUG_LOG:
                    print(f"[SPARKLINE] _load_outdoor_temp_series error: {exc}")
                temp_series_db = []

            # Do not wipe previously cached series when a refresh returns empty.
            if pv_series_db:
                self._spark_cache_pv = pv_series_db
            else:
                pv_series_db = prev_pv
            if temp_series_db:
                self._spark_cache_temp = temp_series_db
            else:
                temp_series_db = prev_temp

            self._spark_cache_ts = time.time()
            if pv_series_db or temp_series_db:
                self._save_cache(pv_series_db, temp_series_db)
        else:
            pv_series_db = list(self._spark_cache_pv)
            temp_series_db = list(self._spark_cache_temp)

        if not pv_series_db and not temp_series_db:
            cached = self._load_cache()
            if cached:
                pv_series_db = cached.get("pv", [])
                temp_series_db = cached.get("temp", [])

        pv_series = list(pv_series_db)
        if not pv_series:
            pv_series = self._history_to_series(self._spark_history_pv, hours=6, bin_minutes=5)

        temp_series = list(temp_series_db)
        if not temp_series:
            temp_series = self._history_to_series(self._spark_history_temp, hours=6, bin_minutes=5)

        self.spark_ax.clear()
        now = datetime.now()
        # Keep the x-axis aligned to midnight boundaries, but anchor it to the
        # newest available datapoint's day (PV or temp). This avoids an empty
        # plot when data ingestion is stale while preserving the day-aligned UX.
        anchor_ts = None
        if pv_series:
            anchor_ts = pv_series[-1][0]
        if temp_series:
            t_last = temp_series[-1][0]
            if anchor_ts is None or t_last > anchor_ts:
                anchor_ts = t_last
        if anchor_ts is None:
            anchor_ts = now

        start_of_today = anchor_ts.replace(hour=0, minute=0, second=0, microsecond=0)
        cutoff = start_of_today - timedelta(days=1)
        window_end = start_of_today + timedelta(days=1)

        if hasattr(self, "spark_ax2"):
            try:
                self.spark_ax2.remove()
            except Exception:
                pass
        self.spark_ax2 = self.spark_ax.twinx()
        ax2 = self.spark_ax2
        ax2.patch.set_alpha(0)
        for ax in (self.spark_ax, ax2):
            ax.set_facecolor('none')
            for spine in ax.spines.values():
                spine.set_visible(True)
                spine.set_edgecolor(COLOR_BORDER)
                spine.set_linewidth(0.5)

        if not pv_series and not temp_series:
            self.spark_ax.text(0.5, 0.5, "Keine Daten (48h)", ha="center", va="center",
                               transform=self.spark_ax.transAxes, color=COLOR_SUBTEXT, fontsize=9)
            self.spark_ax.set_xticks([])
            self.spark_ax.set_yticks([])
            ax2.set_yticks([])
            self.spark_canvas.draw_idle()
            return

        if pv_series:
            xs_pv, ys_pv = zip(*pv_series)
            self.spark_ax.plot(xs_pv, ys_pv, color=COLOR_SUCCESS, linewidth=2.0, alpha=0.9)
            self.spark_ax.fill_between(xs_pv, ys_pv, color=COLOR_SUCCESS, alpha=0.15)
            self.spark_ax.scatter([xs_pv[-1]], [ys_pv[-1]], color=COLOR_SUCCESS, s=12, zorder=10)
            max_pv = max(float(v) for v in ys_pv)
            self.spark_ax.set_ylim(0.0, max(0.5, max_pv * 1.15))
        if temp_series:
            xs_temp, ys_temp = zip(*temp_series)
            ax2.plot(xs_temp, ys_temp, color=COLOR_INFO, linewidth=2.0, alpha=0.9, linestyle="--")
            ax2.scatter([xs_temp[-1]], [ys_temp[-1]], color=COLOR_INFO, s=12, zorder=10)
            min_t = min(float(v) for v in ys_temp)
            max_t = max(float(v) for v in ys_temp)
            span = max_t - min_t
            pad = max(1.0, span * 0.15)
            ax2.set_ylim(min_t - pad, max_t + pad)

        self.spark_ax.spines['top'].set_visible(False)
        self.spark_ax.spines['right'].set_visible(False)
        self.spark_ax.spines['left'].set_color(COLOR_BORDER)
        self.spark_ax.spines['bottom'].set_color(COLOR_BORDER)
        self.spark_ax.spines['left'].set_linewidth(0.5)
        self.spark_ax.spines['bottom'].set_linewidth(0.5)
        ax2.spines['top'].set_visible(False)
        ax2.spines['left'].set_visible(False)
        ax2.spines['right'].set_color(COLOR_BORDER)
        ax2.spines['bottom'].set_color(COLOR_BORDER)
        ax2.spines['right'].set_linewidth(0.5)
        ax2.spines['bottom'].set_linewidth(0.5)
        self.spark_ax.tick_params(axis='both', which='major', labelsize=8, colors=COLOR_SUBTEXT, length=2, width=0.5)
        ax2.tick_params(axis='y', which='major', labelsize=8, colors=COLOR_SUBTEXT, length=2, width=0.5)
        self.spark_ax.set_ylabel('kW', fontsize=8, color=COLOR_SUCCESS, rotation=0, labelpad=10, va='center')
        ax2.set_ylabel('°C', fontsize=8, color=COLOR_INFO, rotation=0, labelpad=10, va='center')
        tick_hours = list(range(0, 49, 6))
        tick_times = [cutoff + timedelta(hours=h) for h in tick_hours]
        self.spark_ax.xaxis.set_major_locator(FixedLocator(mdates.date2num(tick_times)))
        self.spark_ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))

        try:
            self.spark_ax.set_xlim(cutoff, window_end)
            ax2.set_xlim(cutoff, window_end)
        except Exception:
            pass
        self.spark_ax.margins(x=0.01)
        self.spark_canvas.draw_idle()

    def _save_cache(self, pv_series: list[tuple[datetime, float]], temp_series: list[tuple[datetime, float]]) -> None:
        try:
            # Merge with existing cache: avoid overwriting non-empty PV/Temp with empty lists.
            existing = self._load_cache() or {}
            if not pv_series:
                pv_series = list(existing.get("pv", []) or [])
            if not temp_series:
                temp_series = list(existing.get("temp", []) or [])
            payload = {
                "pv": [[ts.isoformat(), float(val)] for ts, val in pv_series],
                "temp": [[ts.isoformat(), float(val)] for ts, val in temp_series],
            }
            self._spark_cache_file.parent.mkdir(parents=True, exist_ok=True)
            self._spark_cache_file.write_text(json.dumps(payload), encoding="utf-8")
        except Exception:
            pass

    def _load_cache(self) -> dict | None:
        try:
            if not self._spark_cache_file.exists():
                return None
            raw = json.loads(self._spark_cache_file.read_text(encoding="utf-8"))
            pv = []
            for item in raw.get("pv", []):
                ts = self._parse_ts(item[0])
                val = self._safe_float(item[1])
                if ts is not None and val is not None:
                    pv.append((ts, val))
            temp = []
            for item in raw.get("temp", []):
                ts = self._parse_ts(item[0])
                val = self._safe_float(item[1])
                if ts is not None and val is not None:
                    temp.append((ts, val))
            return {"pv": pv, "temp": temp}
        except Exception:
            return None

    def _history_to_series(self, history: deque[tuple[datetime, float]], hours: int, bin_minutes: int) -> list[tuple[datetime, float]]:
        if not history:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        samples: list[tuple[datetime, float]] = []
        for ts, val in list(history):
            if val is None or ts < cutoff:
                continue
            rounded = ts.replace(second=0, microsecond=0) - timedelta(minutes=ts.minute % bin_minutes)
            samples.append((rounded, float(val)))
        if not samples:
            return []
        return self._aggregate_series(samples)

    def _load_pv_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        now = datetime.now()
        rows = self.datastore.get_recent_fronius(hours=None, limit=4000)
        parsed_rows: list[tuple[datetime, dict]] = []
        for entry in rows:
            ts = self._parse_ts(entry.get('timestamp'))
            if ts is None:
                continue
            if ts > now:
                ts = now
            parsed_rows.append((ts, entry))
        parsed_rows.sort(key=lambda t: t[0])

        samples: list[tuple[datetime, float]] = []
        for ts, entry in parsed_rows[-2500:]:
            pv_kw = self._safe_float(entry.get('pv'))
            if pv_kw is None or ts < cutoff:
                continue
            if pv_kw > 200.0:
                pv_kw = pv_kw / 1000.0
            if pv_kw < -0.2:
                continue
            pv_kw = max(0.0, pv_kw)
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, pv_kw))
        return self._aggregate_series(samples)

    def _load_outdoor_temp_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        now = datetime.now()

        rows = self.datastore.get_recent_heating(hours=None, limit=4000)
        parsed_rows: list[tuple[datetime, dict]] = []
        for entry in rows:
            ts = self._parse_ts(entry.get('timestamp'))
            if ts is None:
                continue
            if ts > now:
                ts = now
            parsed_rows.append((ts, entry))
        parsed_rows.sort(key=lambda t: t[0])

        samples: list[tuple[datetime, float]] = []
        for ts, entry in parsed_rows[-2500:]:
            val = self._safe_float(entry.get('outdoor'))
            if val is None or ts < cutoff:
                continue
            if not (-40.0 <= val <= 60.0):
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, val))
        return self._aggregate_series(samples)

    def _aggregate_series(self, samples: list[tuple[datetime, float]]) -> list[tuple[datetime, float]]:
        if not samples:
            return []
        agg: dict[datetime, tuple[float, int]] = {}
        for ts, val in samples:
            total, count = agg.get(ts, (0.0, 0))
            agg[ts] = (total + val, count + 1)
        averaged = [(ts, total / count) for ts, (total, count) in sorted(agg.items())]
        return self._smooth_series(averaged, window=5)

    def _smooth_series(self, series: list[tuple[datetime, float]], window: int = 5) -> list[tuple[datetime, float]]:
        if len(series) < window:
            return series
        smoothed: list[tuple[datetime, float]] = []
        half_window = window // 2
        for idx in range(len(series)):
            start = max(0, idx - half_window)
            end = min(len(series), idx + half_window + 1)
            values = [val for _, val in series[start:end]]
            smoothed.append((series[idx][0], sum(values) / len(values)))
        return smoothed

    @staticmethod
    def _parse_ts(value):
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
    def _safe_float(value):
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
