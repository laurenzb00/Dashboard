import tkinter as tk
from tkinter import Frame, Label
import matplotlib
matplotlib.use("Agg")
from matplotlib import dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from datetime import datetime
import numpy as np

# Farben/Fallbacks
try:
    from src.ui.styles import (
        COLOR_CARD, COLOR_BORDER, COLOR_TEXT, COLOR_SUBTEXT,
        COLOR_PRIMARY, COLOR_INFO, COLOR_WARNING, COLOR_DANGER
    )
except ImportError:
    COLOR_CARD = "#222"
    COLOR_BORDER = "#444"
    COLOR_TEXT = "#EEE"
    COLOR_SUBTEXT = "#AAA"
    COLOR_PRIMARY = "#1E90FF"
    COLOR_INFO = "#00CED1"
    COLOR_WARNING = "#FFD700"
    COLOR_DANGER = "#FF6347"

COLOR_SUCCESS = "#FFF"  # Warmwasser/Boiler: weiß

class HistoricalTab(Frame):
    def __init__(self, parent, notebook, datastore, *args, **kwargs):
        super().__init__(notebook, *args, **kwargs)
        self.datastore = datastore
        self.root = parent.winfo_toplevel()
        self.after_job = None
        self.last_data_len = 0
        self.last_timestamp = None
        self._resize_debounce_id = None
        self._last_view_size = (0, 0)
        self._stats_warn = False

        # Layout: row 0 = stats, row 1 = plot
        self.grid_rowconfigure(0, minsize=60)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Stats grid
        self.stats_frame = Frame(self, bg=COLOR_CARD)
        self.stats_frame.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8,2))
        self.stats_labels = {}
        value_names = [
            ("Puffer Oben", "top", COLOR_PRIMARY),
            ("Puffer Mitte", "mid", COLOR_INFO),
            ("Puffer Unten", "bot", COLOR_WARNING),
            ("Kessel", "kessel", COLOR_DANGER),
            ("Warmwasser", "warm", COLOR_SUCCESS),
            ("Außen", "outdoor", COLOR_SUBTEXT),
        ]
        for i, (name, key, color) in enumerate(value_names):
            lbl = Label(self.stats_frame, text=f"{name}: --", fg=color, bg=COLOR_CARD, font=("Segoe UI", 12, "bold"))
            lbl.grid(row=0, column=i, sticky="nsew", padx=6, pady=2)
            self.stats_labels[key] = lbl

        # Datenalter
        self.age_label = Label(self.stats_frame, text="Datenalter: --", fg=COLOR_TEXT, bg=COLOR_CARD, font=("Segoe UI", 11))
        self.age_label.grid(row=1, column=0, columnspan=3, sticky="w", padx=6, pady=(0,2))

        # Warnhinweis
        self.warn_label = Label(self.stats_frame, text="", fg=COLOR_WARNING, bg=COLOR_CARD, font=("Segoe UI", 11, "bold"))
        self.warn_label.grid(row=1, column=3, columnspan=4, sticky="e", padx=6, pady=(0,2))

        # Plot
        self.fig = Figure(figsize=(7, 2.5), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax.set_facecolor(COLOR_CARD)
        for spine in self.ax.spines.values():
            spine.set_color(COLOR_BORDER)
        self.ax.tick_params(axis='x', colors=COLOR_SUBTEXT)
        self.ax.tick_params(axis='y', colors=COLOR_SUBTEXT)
        self.ax.yaxis.label.set_color(COLOR_TEXT)
        self.ax.xaxis.label.set_color(COLOR_TEXT)
        self.ax.title.set_color(COLOR_TEXT)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        self.canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=8, pady=8)

        # Plot lines
        self.lines = {}
        self.legend = None

        # Add tab to notebook
        notebook.add(self, text="Heizung-Historie")

        # Bind resize
        self.bind("<Configure>", self._on_resize)

        self._update_plot()
        self._schedule_update()

    def _on_resize(self, event):
        if self._resize_debounce_id:
            self.after_cancel(self._resize_debounce_id)
        self._resize_debounce_id = self.after(100, self._handle_resize)

    def _handle_resize(self):
        w = self.winfo_width()
        h = self.winfo_height()
        if (w, h) != self._last_view_size:
            self.fig.set_size_inches(max(4, w/100), max(2, h/100))
            self.canvas.draw_idle()
            self._last_view_size = (w, h)

    def _parse_ts(self, ts):
        if not ts:
            return None
        s = str(ts).strip().replace('T', ' ', 1)
        if '.' in s:
            s = s.split('.')[0]
        if '+' in s:
            s = s.split('+')[0]
        if 'Z' in s:
            s = s.replace('Z', '')
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    def _schedule_update(self):
        self._cancel_update()
        self.after_job = self.root.after(30000, self._update_plot)

    def _cancel_update(self):
        if self.after_job:
            self.root.after_cancel(self.after_job)
            self.after_job = None

    def _update_plot(self):
        # Daten holen
        data = self.datastore.get_recent_heating(hours=168, limit=2000)
        if not data:
            self._schedule_update()
            return

        # Robust timestamp parsing, filter future/old
        now = datetime.now()
        times = []
        values = {k: [] for k in ['top','mid','bot','kessel','warm','outdoor']}
        for row in data:
            ts = self._parse_ts(row['timestamp'])
            if not ts or ts > now + timedelta(seconds=60):
                continue  # skip future
            times.append(ts)
            for k in values:
                values[k].append(float(row.get(k, 0.0)))

        # Clamp xmax
        if times:
            xmax = min(max(times), now)
        else:
            xmax = now

        # Update stats
        latest_idx = -1 if times else None
        for k, lbl in self.stats_labels.items():
            v = values[k][latest_idx] if times else 0.0
            lbl.config(text=f"{lbl.cget('text').split(':')[0]}: {v:.1f}")

        # Datenalter
        if times:
            age_min = int((now - times[-1]).total_seconds() // 60)
            self.age_label.config(text=f"Datenalter: {age_min} min")
            self._stats_warn = age_min > 30
            if self._stats_warn:
                self.warn_label.config(text="Warnung: Daten >30min alt!", fg=COLOR_WARNING)
            else:
                self.warn_label.config(text="", fg=COLOR_TEXT)
            print(f"[HISTORICAL] latest ts: {times[-1]} age: {age_min} min", flush=True)
        else:
            self.age_label.config(text="Datenalter: --")
            self.warn_label.config(text="Keine Daten", fg=COLOR_WARNING)

        # Plot
        self.ax.clear()
        self.ax.set_facecolor(COLOR_CARD)
        for spine in self.ax.spines.values():
            spine.set_color(COLOR_BORDER)
        for k, color in zip(['top','mid','bot','kessel','warm','outdoor'], [COLOR_PRIMARY, COLOR_INFO, COLOR_WARNING, COLOR_DANGER, COLOR_SUCCESS, COLOR_SUBTEXT]):
            if times and values[k]:
                self.lines[k] = self.ax.plot(times, values[k], label=k.capitalize(), color=color, linewidth=2)[0]
        self.ax.set_xlim(times[0] if times else now, xmax)
        self.ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m %H:%M'))
        self.ax.legend(loc='upper left', fontsize=9)
        self.ax.set_ylabel('°C')
        self.ax.set_xlabel('Zeit')
        self.fig.tight_layout()
        self.canvas.draw_idle()
        self._schedule_update()
        # Ensure tab is added to notebook
        if hasattr(parent, "add"):
            parent.add(self, text="Heizung-Historie")

    def _schedule_update(self):
        self._cancel_update()
        self.after_job = self.root.after(30000, self._update_plot)

    def _cancel_update(self):
        if self.after_job:
            self.root.after_cancel(self.after_job)
            self.after_job = None

    def _parse_ts(self, ts):
        if not ts:
            return None
        s = str(ts).strip().replace('T', ' ', 1)
        if '.' in s:
            s = s.split('.')[0]
        if '+' in s:
            s = s.split('+')[0]
        if 'Z' in s:
            s = s.replace('Z', '')
        try:
            return datetime.fromisoformat(s)
        except Exception:
            return None

    def _update_plot(self):
        # Daten holen
        data = self.datastore.get_recent_heating(hours=168)
        if not data:
            self._schedule_update()
            return

        # Prüfe, ob Update nötig
        data_len = len(data)
        last_ts = data[-1]['timestamp'] if data else None
        if data_len == self.last_data_len and last_ts == self.last_timestamp:
            self._schedule_update()
            return
        self.last_data_len = data_len
        self.last_timestamp = last_ts

        # Aktuelle Werte oben anzeigen
        latest = data[-1] if data else {}
        for key, lbl in self.value_labels.items():
            val = latest.get(key)
            if val is not None:
                lbl.config(text=f"{lbl.cget('text').split(':')[0]}: {val:.1f}°C")
            else:
                lbl.config(text=f"{lbl.cget('text').split(':')[0]}: --")

        # Zeitachsen und Werte extrahieren, None filtern
        times = []
        values = {k: [] for k in ['top', 'mid', 'bot', 'kessel', 'warm', 'outdoor']}
        for row in data:
            ts = self._parse_ts(row.get('timestamp'))
            if not ts:
                continue
            # Pufferwerte müssen vorhanden sein
            if any(row.get(k) is None for k in ['top', 'mid', 'bot']):
                continue
            times.append(ts)
            for k in values:
                v = row.get(k)
                values[k].append(v if v is not None else np.nan)

        # Downsampling auf max 2000 Punkte
        n = len(times)
        max_points = 2000
        if n > max_points:
            step = n // max_points
            times = times[::step]
            for k in values:
                values[k] = values[k][::step]

        # Plot aktualisieren
        self.ax.clear()
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax.set_facecolor(COLOR_CARD)
        self.ax.spines['bottom'].set_color(COLOR_BORDER)
        self.ax.spines['top'].set_color(COLOR_BORDER)
        self.ax.spines['right'].set_color(COLOR_BORDER)
        self.ax.spines['left'].set_color(COLOR_BORDER)
        self.ax.tick_params(axis='x', colors=COLOR_SUBTEXT)
        self.ax.tick_params(axis='y', colors=COLOR_SUBTEXT)
        self.ax.yaxis.label.set_color(COLOR_TEXT)
        self.ax.xaxis.label.set_color(COLOR_TEXT)
        self.ax.title.set_color(COLOR_TEXT)

        # Linien plotten
        line_cfg = [
            ("Puffer Oben", "top", COLOR_PRIMARY),
            ("Puffer Mitte", "mid", COLOR_INFO),
            ("Puffer Unten", "bot", COLOR_WARNING),
            ("Kessel", "kessel", COLOR_DANGER),
            ("Warmwasser", "warm", COLOR_SUCCESS),
            ("Außen", "outdoor", COLOR_SUBTEXT),
        ]
        for name, key, color in line_cfg:
            y = np.array(values[key])
            if np.all(np.isnan(y)):
                continue  # Keine Werte vorhanden
            # Für warm/outdoor: plotten, wenn mind. 1 Wert vorhanden
            if key in ['warm', 'outdoor'] and np.nanmax(y) == np.nanmin(y) and np.isnan(np.nanmax(y)):
                continue
            l, = self.ax.plot(times, y, label=name, color=color, linewidth=2 if key != 'outdoor' else 1.2)
            self.lines[key] = l

        # Achsen/Legende
        self.ax.set_xlabel("Zeit", color=COLOR_TEXT)
        self.ax.set_ylabel("Temperatur [°C]", color=COLOR_TEXT)
        self.ax.set_title("Heizungs-/Pufferhistorie (7 Tage)", color=COLOR_TEXT)
        self.ax.legend(loc="upper left", fontsize=9, facecolor=COLOR_CARD, edgecolor=COLOR_BORDER)
        self.ax.grid(True, color=COLOR_BORDER, alpha=0.2)

        # X-Achse: Datum+Uhrzeit, Labels drehen
        self.ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter('%d.%m %H:%M'))
        self.fig.autofmt_xdate(rotation=30)

        # Y-Achse: min/max + Rand
        all_y = np.concatenate([np.array(values[k]) for k in ['top', 'mid', 'bot', 'kessel', 'warm'] if len(values[k])])
        all_y = all_y[~np.isnan(all_y)]
        if len(all_y):
            ymin = np.min(all_y)
            ymax = np.max(all_y)
            yrange = ymax - ymin
            self.ax.set_ylim(ymin - 2, ymax + 2 if yrange < 10 else ymax + 0.1 * yrange)

        self.canvas.draw_idle()
        self._schedule_update()

    def stop(self):
        self._cancel_update()
        try:
            self.fig.clf()
            self.canvas.get_tk_widget().destroy()
        except Exception:
            pass

# ...existing code...

class HistoricalTab(Frame):
    def __init__(self, parent, datastore, *args, **kwargs):
        super().__init__(parent, *args, **kwargs)
        self.datastore = datastore
        self.root = parent.winfo_toplevel()
        self.after_job = None

        # Layout: Stats oben, Plot unten
        self.grid_rowconfigure(0, minsize=80)
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self.stats_frame = tk.Frame(self, bg=COLOR_CARD, height=80)
        self.stats_frame.grid(row=0, column=0, sticky="ew")
        self.stats_frame.grid_propagate(False)

        self.plot_frame = tk.Frame(self, bg=COLOR_CARD)
        self.plot_frame.grid(row=1, column=0, sticky="nsew")

        # Stats-Labels
        self.stats_labels = {}
        stats_keys = [
            ("Puffer Oben", "top", COLOR_PRIMARY),
            ("Puffer Mitte", "mid", COLOR_INFO),
            ("Puffer Unten", "bot", COLOR_WARNING),
            ("Kessel", "kessel", COLOR_DANGER),
            ("Warmwasser", "warm", COLOR_TEXT),
            ("Außen", "outdoor", COLOR_SUBTEXT),
        ]
        for idx, (name, key, color) in enumerate(stats_keys):
            lbl = Label(self.stats_frame, text=f"{name}: -- °C", fg=color, bg=COLOR_CARD,
                        font=("Segoe UI", 13, "bold"))
            lbl.grid(row=0, column=idx, padx=12, pady=8, sticky="w")
            self.stats_labels[key] = lbl

        self.stale_label = Label(self.stats_frame, text="", fg=COLOR_DANGER, bg=COLOR_CARD,
                                font=("Segoe UI", 11, "bold"))
        self.stale_label.grid(row=1, column=0, columnspan=len(stats_keys), sticky="w", padx=12)

        # Matplotlib Figure
        self.fig = Figure(figsize=(7, 3), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax.set_facecolor(COLOR_CARD)
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        self._debounce_resize_id = None
        self.bind("<Configure>", self._on_resize)

        self._update_plot()
        self._schedule_update()

    def _on_resize(self, event):
        if self._debounce_resize_id:
            self.after_cancel(self._debounce_resize_id)
        self._debounce_resize_id = self.after(200, self._resize_plot)

    def _resize_plot(self):
        self.canvas.get_tk_widget().config(width=self.plot_frame.winfo_width(),
                                           height=self.plot_frame.winfo_height())

    def _schedule_update(self):
        if self.after_job:
            self.after_cancel(self.after_job)
        self.after_job = self.after(30000, self._update_plot)

    def _parse_ts(self, ts):
        import re
        from datetime import datetime, timezone
        import pytz
        if not ts:
            return None
        s = str(ts).strip()
        s = re.sub(r"\.\d{1,6}", "", s)
        s = s.replace('T', ' ', 1)
        if s.endswith('Z'):
            s = s[:-1]
            try:
                dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None
        elif re.search(r"[+-]\d{2}:\d{2}$", s):
            try:
                dt = datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
            except Exception:
                return None
        else:
            try:
                dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
                dt = dt.replace(tzinfo=None)
            except Exception:
                return None
        try:
            vienna = pytz.timezone("Europe/Vienna")
            if dt.tzinfo is not None:
                local_dt = dt.astimezone(vienna)
                naive_local = local_dt.replace(tzinfo=None)
            else:
                naive_local = dt
        except Exception:
            naive_local = dt
        if naive_local > datetime.now():
            print(f"[DEBUG] Parsed timestamp in future: {ts} -> {naive_local}")
        return naive_local

    def _update_plot(self):
        # Daten holen
        data = self.datastore.get_recent_heating(hours=6, limit=400)
        if not data:
            for key, lbl in self.stats_labels.items():
                lbl.config(text=f"{lbl.cget('text').split(':')[0]}: -- °C")
            self.stale_label.config(text="Keine Daten")
            self._schedule_update()
            return

        # Letzter Wert
        latest = data[-1]
        ts = self._parse_ts(latest.get('timestamp'))
        now = datetime.now()
        stale_minutes = (now - ts).total_seconds() / 60 if ts else None

        # Stats aktualisieren
        for key, lbl in self.stats_labels.items():
            val = latest.get(key)
            if val is not None:
                lbl.config(text=f"{lbl.cget('text').split(':')[0]}: {val:.1f} °C")
            else:
                lbl.config(text=f"{lbl.cget('text').split(':')[0]}: -- °C")

        # Stale-Status
        if stale_minutes is not None and stale_minutes > 5:
            self.stale_label.config(text=f"STALE ({int(stale_minutes)} min alt)", fg=COLOR_DANGER)
        else:
            self.stale_label.config(text="", fg=COLOR_DANGER)

        # Plot-Daten
        times = []
        values = {k: [] for k in ['top', 'mid', 'bot', 'kessel', 'warm', 'outdoor']}
        for row in data:
            t = self._parse_ts(row.get('timestamp'))
            if not t:
                continue
            for k in values:
                v = row.get(k)
                values[k].append(v if v is not None else np.nan)
            times.append(t)

        # Plot
        self.ax.clear()
        line_cfg = [
            ("Puffer Oben", "top", COLOR_PRIMARY),
            ("Puffer Mitte", "mid", COLOR_INFO),
            ("Puffer Unten", "bot", COLOR_WARNING),
            ("Kessel", "kessel", COLOR_DANGER),
            ("Warmwasser", "warm", COLOR_TEXT),
            ("Außen", "outdoor", COLOR_SUBTEXT),
        ]
        for name, key, color in line_cfg:
            y = np.array(values[key])
            if np.all(np.isnan(y)):
                continue
            self.ax.plot(times, y, label=name, color=color, linewidth=2)

        self.ax.set_xlabel("Zeit", color=COLOR_TEXT)
        self.ax.set_ylabel("Temperatur [°C]", color=COLOR_TEXT)
        self.ax.set_title("Heizungs-/Pufferhistorie", color=COLOR_TEXT)
        self.ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter('%d.%m %H:%M'))
        self.fig.autofmt_xdate(rotation=30)
        self.ax.tick_params(axis='x', colors=COLOR_SUBTEXT, labelsize=9)
        self.ax.tick_params(axis='y', colors=COLOR_SUBTEXT, labelsize=9)

        # y-limits dynamisch, clamp auf 0–95
        all_y = np.concatenate([np.array(values[k]) for k in values if len(values[k])])
        all_y = all_y[~np.isnan(all_y)]
        if len(all_y):
            ymin = max(0, np.min(all_y) - 2)
            ymax = min(95, np.max(all_y) + 2)
            self.ax.set_ylim(ymin, ymax)

        self.ax.legend(loc="upper left", fontsize=9, facecolor=COLOR_CARD, edgecolor=COLOR_BORDER)
        self.ax.grid(True, color=COLOR_BORDER, alpha=0.2)

        self.canvas.draw_idle()
        self._schedule_update()

    def stop(self):
        if self.after_job:
            self.after_cancel(self.after_job)
        try:
            self.fig.clf()
            self.canvas.get_tk_widget().destroy()
        except Exception:
            pass
    def _update_plot(self):
        if not self.alive:
            return
        if not self.canvas.get_tk_widget().winfo_exists():
            return

        rows = self._load_temps()
        key = (len(rows), rows[-1] if rows else None) if rows else ("empty",)
        
        # Nur redraw wenn sich Daten wirklich geändert haben
        if key != self._last_key:
            self._last_key = key
            self.fig.clear()
            self.ax = self.fig.add_subplot(111)
            self.fig.patch.set_facecolor(COLOR_CARD)
            self.ax.set_facecolor(COLOR_CARD)
            self._style_axes()

            if rows:
                if not getattr(self, "_log_once", False):
                    print(f"[HISTORIE] Matplotlib-Liniendiagramm aktiv (Samples={len(rows)})")
                    self._log_once = True
                ts, top, mid, bot, boiler, outside = zip(*rows)
                # Collect all temperature values for dynamic y-axis scaling
                all_temps = list(top) + list(mid) + list(bot) + list(boiler) + list(outside)
                # Moderneres Design mit besseren Farben und Liniendicken
                self.ax.plot(ts, top, color=COLOR_PRIMARY, label="Puffer oben", linewidth=2.0, alpha=0.8)
                self.ax.plot(ts, mid, color=COLOR_INFO, label="Puffer mitte", linewidth=1.5, alpha=0.7)
                self.ax.plot(ts, bot, color=COLOR_SUBTEXT, label="Puffer unten", linewidth=1.5, alpha=0.6)
                self.ax.plot(ts, boiler, color=COLOR_WARNING, label="Kessel", linewidth=2.0, alpha=0.8)
                self.ax.plot(ts, outside, color=COLOR_DANGER, label="Außen", linewidth=1.8, alpha=0.8, linestyle='--')
                self.ax.set_ylabel("°C", color=COLOR_TEXT, fontsize=10, fontweight='bold')
                self.ax.tick_params(axis="y", colors=COLOR_TEXT, labelsize=9)
                self.ax.tick_params(axis="x", colors=COLOR_SUBTEXT, labelsize=8)
                # Dynamic y-axis scaling
                self.fig.subplots_adjust(left=0.10, right=0.995, top=0.92, bottom=0.18)
                if len(all_temps) > 1:
                    min_temp = min(all_temps)
                    max_temp = max(all_temps)
                    self.ax.set_ylim(min_temp - 2, max_temp + 2)
                # Improved x-axis formatting
                self.ax.xaxis.set_major_locator(mdates.AutoDateLocator())
                self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m %H:%M"))
                self.ax.grid(True, color=COLOR_BORDER, alpha=0.2, linewidth=0.5)
                self.ax.legend(facecolor=COLOR_CARD, edgecolor=COLOR_BORDER, labelcolor=COLOR_TEXT, fontsize=8, loc='upper left')

                # Current values
                self.var_top.set(f"{top[-1]:.1f} °C")
                self.var_mid.set(f"{mid[-1]:.1f} °C")
                self.var_bot.set(f"{bot[-1]:.1f} °C")
                self.var_boiler.set(f"{boiler[-1]:.1f} °C")
                self.var_out.set(f"{outside[-1]:.1f} °C")
            else:
                self.ax.text(0.5, 0.5, "Keine Daten", color=COLOR_SUBTEXT, ha="center", va="center", 
                            fontsize=12, transform=self.ax.transAxes)
                self.ax.set_xticks([])
                self.ax.set_yticks([])
                self.var_top.set("-- °C")
                self.var_mid.set("-- °C")
                self.var_bot.set("-- °C")
                self.var_boiler.set("-- °C")
                self.var_out.set("-- °C")

            # Feste Ränder und Schriftgrößen für optimalen Sitz
            self.fig.subplots_adjust(left=0.10, right=0.995, top=0.92, bottom=0.18)
            self.fig.patch.set_alpha(0)
            self.ax.set_facecolor("none")
            self.ax.set_ylabel("°C", color=COLOR_TEXT, fontsize=13, fontweight='bold')
            self.ax.tick_params(axis="y", colors=COLOR_TEXT, labelsize=12)
            self.ax.tick_params(axis="x", colors=COLOR_SUBTEXT, labelsize=11)
            for label in self.ax.get_xticklabels():
                label.set_rotation(35)
                label.set_horizontalalignment("right")
            legend = self.ax.get_legend()
            if legend:
                try:
                    legend.set_fontsize(11)
                except AttributeError:
                    legend.prop = {'size': 11}
                try:
                    legend.set_bbox_to_anchor((1, 1))
                    legend.set_loc('upper left')
                except Exception:
                    pass
            try:
                if self.canvas.get_tk_widget().winfo_exists():
                    self.canvas.draw_idle()
            except Exception:
                pass

        # Nächster Update in 30 Sekunden - cancel previous task first
        if self._update_task_id:
            try:
                self.root.after_cancel(self._update_task_id)
            except Exception:
                pass
        self._update_task_id = self.root.after(30 * 1000, self._update_plot)

    def _on_plot_frame_resize(self, event):
        # Dynamisches Resizing der Figure auf die exakte Frame-Größe
        w = max(1, event.width)
        h = max(1, event.height)
        if self._last_canvas_size:
            last_w, last_h = self._last_canvas_size
            if abs(w - last_w) < 8 and abs(h - last_h) < 8:
                return
        self._last_canvas_size = (w, h)
        try:
            dpi = self.fig.dpi or 100
            width_in = w / dpi
            height_in = h / dpi
            self.fig.set_size_inches(width_in, height_in, forward=True)
            self.canvas.draw_idle()
        except Exception:
            pass
    
    def _do_canvas_draw(self):
        """Deferred canvas draw to prevent Configure event loop."""
        try:
            if self.canvas.get_tk_widget().winfo_exists():
                self.canvas.draw_idle()
        except Exception:
            pass
        finally:
            self._resize_pending = False

    def _resize_figure(self, width_px: int, height_px: int) -> None:
        dpi = self.fig.dpi or 100
        # Maximalbreite auf 900px begrenzen (z.B. Card-Breite)
        width_px = min(width_px, 900)
        width_in = max(4.5, width_px / dpi)
        height_in = max(2.8, height_px / dpi)
        self.fig.set_size_inches(width_in, height_in, forward=True)

    @staticmethod
    def _parse_ts(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None
