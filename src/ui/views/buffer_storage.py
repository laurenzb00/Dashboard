import os
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib.patheffects as path_effects
import numpy as np
import tkinter as tk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.figure import Figure
from matplotlib.patches import Ellipse, FancyBboxPatch, Rectangle
from mpl_toolkits.axes_grid1 import make_axes_locatable

try:
    from ui.styles import (
        COLOR_CARD,
        COLOR_ROOT,
        COLOR_BORDER,
        COLOR_DANGER,
        COLOR_INFO,
        COLOR_PRIMARY,
        COLOR_SUBTEXT,
        COLOR_SUCCESS,
        COLOR_TEXT,
        COLOR_TITLE,
        COLOR_WARNING,
    )
except ImportError:
    # Fallback Farben, falls Import fehlschlägt
    COLOR_ROOT = "#0E0F12"
    COLOR_CARD = "#0E0F12"
    COLOR_BORDER = "#0E0F12"
    COLOR_PRIMARY = "#3B82F6"
    COLOR_SUCCESS = "#10B981"
    COLOR_WARNING = "#F59E0B"
    COLOR_INFO = "#38BDF8"
    COLOR_DANGER = "#EF4444"
    COLOR_TEXT = "#E6ECF5"
    COLOR_SUBTEXT = "#9AA3B2"
    COLOR_TITLE = "#AAB3C5"

try:
    from core.datastore import get_shared_datastore
except ImportError:
    def get_shared_datastore():
        return None

from core.schema import (
    BUF_TOP_C,
    BUF_MID_C,
    BUF_BOTTOM_C,
    BMK_WARMWASSER_C,
    BMK_BETRIEBSMODUS,
    PV_POWER_KW,
)

DEBUG_LOG = os.environ.get("DASHBOARD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")

# Sparkline-Akzentfarben ("Vibrant Amber/Magenta" - Nutzer-Feedback zur
# Farbwahl der PV/Außentemperatur-Sparkline im Energie-Tab). Eigene, kraeftige
# Farben statt der generischen Success/Info-Theme-Farben, damit die
# Sparkline sich staerker vom Rest abhebt.
SPARK_PV_COLOR = "#ffb01a"
SPARK_TEMP_COLOR = "#ff4fd8"


class BufferStorageView(tk.Frame):

    # Heatmap scale targets (°C)
    # TEMP_BLUE_MAX/TEMP_ORANGE_FROM used to sit only 2°C apart (53/55), so
    # two close real readings (z.B. Puffer-Mitte 53.5°C vs. Boiler 54.8°C -
    # nur 1.3°C Unterschied) landeten auf fast entgegengesetzten Enden der
    # Skala (kräftiges Blau vs. sattes Orange). Der Übergang ist jetzt auf
    # den tatsächlichen Betriebsbereich (meist 40-60°C) verbreitert, damit
    # nah beieinanderliegende Temperaturen auch optisch nah beieinander
    # liegen, ohne die Endpunkte (35°C kalt / 75°C sehr heiß) zu verändern.
    TEMP_MIN = 35.0
    TEMP_BLUE_MAX = 46.0
    TEMP_ORANGE_FROM = 62.0
    TEMP_MAX = 75.0

    @staticmethod
    def _blend_hex(c1: str, c2: str, t: float) -> str:
        """Blend two #RRGGBB colors; t=0 -> c1, t=1 -> c2."""
        try:
            a = c1.lstrip("#")
            b = c2.lstrip("#")
            r1, g1, b1 = int(a[0:2], 16), int(a[2:4], 16), int(a[4:6], 16)
            r2, g2, b2 = int(b[0:2], 16), int(b[2:4], 16), int(b[4:6], 16)
            t = max(0.0, min(1.0, float(t)))
            r = int(round(r1 + (r2 - r1) * t))
            g = int(round(g1 + (g2 - g1) * t))
            bl = int(round(b1 + (b2 - b1) * t))
            return f"#{r:02x}{g:02x}{bl:02x}"
        except Exception:
            return c1

    def _update_sparkline(self) -> None:
        if not hasattr(self, "spark_ax") or not hasattr(self, "spark_canvas"):
            return
        refresh_needed = (time.time() - getattr(self, "_spark_cache_ts", 0.0)) > 60.0
        if refresh_needed:
            try:
                pv_series_db = self._load_pv_series(hours=24, bin_minutes=15)
            except Exception as exc:
                if DEBUG_LOG:
                    print(f"[BUFFER] _load_pv_series error: {exc}")
                pv_series_db = []
            try:
                temp_series_db = self._load_outdoor_temp_series(hours=24, bin_minutes=15)
            except Exception as exc:
                if DEBUG_LOG:
                    print(f"[BUFFER] _load_outdoor_temp_series error: {exc}")
                temp_series_db = []
            self._spark_cache_pv = pv_series_db
            self._spark_cache_temp = temp_series_db
            self._spark_cache_ts = time.time()
        else:
            pv_series_db = list(self._spark_cache_pv)
            temp_series_db = list(self._spark_cache_temp)

        pv_series = list(pv_series_db)
        pv_hours = 24 if pv_series else 0
        if not pv_series:
            pv_series = self._history_to_series(self._spark_history_pv, hours=6, bin_minutes=5)
            if pv_series:
                pv_hours = 6

        temp_series = list(temp_series_db)
        temp_hours = 24 if temp_series else 0
        if not temp_series:
            temp_series = self._history_to_series(self._spark_history_temp, hours=6, bin_minutes=5)
            if temp_series:
                temp_hours = 6

        if DEBUG_LOG:
            print(f"[BUFFER] sparkline pv_series={len(pv_series)} temp_series={len(temp_series)}")

        self.spark_ax.clear()
        now = datetime.now()
        window_hours = max(pv_hours, temp_hours, 6 if (pv_series or temp_series) else 24)
        cutoff = now - timedelta(hours=window_hours)
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
            self.spark_ax.text(0.5, 0.5, "Keine Daten (24h)", ha="center", va="center",
                               transform=self.spark_ax.transAxes, color=COLOR_SUBTEXT, fontsize=9)
            self.spark_ax.set_xticks([])
            self.spark_ax.set_yticks([])
            ax2.set_yticks([])
            try:
                # Use only draw_idle() - draw() is redundant and blocks
                self.spark_canvas.draw_idle()
            except Exception as exc:
                print(f"[BUFFER] Sparkline canvas draw error: {exc}")
            return

        if pv_series:
            xs_pv, ys_pv = zip(*pv_series)
            self.spark_ax.plot(xs_pv, ys_pv, color=SPARK_PV_COLOR, linewidth=2.0, alpha=0.95)
            self.spark_ax.fill_between(xs_pv, ys_pv, color=SPARK_PV_COLOR, alpha=0.18)
            self.spark_ax.scatter([xs_pv[-1]], [ys_pv[-1]], color=SPARK_PV_COLOR, s=12, zorder=10)
            try:
                max_pv = max(float(v) for v in ys_pv)
            except Exception:
                max_pv = 0.0
            self.spark_ax.set_ylim(0.0, max(0.5, max_pv * 1.15))
        if temp_series:
            xs_temp, ys_temp = zip(*temp_series)
            ax2.plot(xs_temp, ys_temp, color=SPARK_TEMP_COLOR, linewidth=2.0, alpha=0.9, linestyle="--")
            ax2.scatter([xs_temp[-1]], [ys_temp[-1]], color=SPARK_TEMP_COLOR, s=12, zorder=10)
            try:
                min_t = min(float(v) for v in ys_temp)
                max_t = max(float(v) for v in ys_temp)
                span = max_t - min_t
                pad = max(1.0, span * 0.15)
                ax2.set_ylim(min_t - pad, max_t + pad)
            except Exception:
                pass

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
        self.spark_ax.set_ylabel('kW', fontsize=8, color=SPARK_PV_COLOR, rotation=0, labelpad=10, va='center')
        ax2.set_ylabel('°C', fontsize=8, color=SPARK_TEMP_COLOR, rotation=0, labelpad=10, va='center')
        self.spark_ax.yaxis.set_major_locator(plt.MaxNLocator(4))
        ax2.yaxis.set_major_locator(plt.MaxNLocator(4))
        self.spark_ax.xaxis.set_major_locator(plt.MaxNLocator(6))
        self.spark_ax.xaxis.set_major_formatter(mdates.DateFormatter('%H:%M'))

        try:
            self.spark_ax.set_xlim(cutoff, now)
            ax2.set_xlim(cutoff, now)
        except Exception:
            pass
        self.spark_ax.margins(x=0.01)
        try:
            self.spark_fig.tight_layout(pad=0.3)
        except Exception as exc:
            print(f"[BUFFER] tight_layout warning: {exc}")
        try:
            # Use only draw_idle() - draw() is redundant and blocks
            self.spark_canvas.draw_idle()
        except Exception as exc:
            print(f"[BUFFER] Sparkline canvas draw error: {exc}")


    def _record_spark_sample(self, data: dict) -> None:
        now_ts = time.time()
        if now_ts - getattr(self, "_last_spark_sample_ts", 0.0) < 60.0:
            return
        self._last_spark_sample_ts = now_ts
        sample_time = datetime.now()
        pv_kw = self._safe_float(data.get(PV_POWER_KW))
        if pv_kw is not None:
            self._spark_history_pv.append((sample_time, max(0.0, pv_kw)))
        outdoor = self._safe_float(data.get('outdoor'))
        if outdoor is not None:
            self._spark_history_temp.append((sample_time, outdoor))

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


    def _build_stratified_data(self, top, mid, bot):
        """
        Returns a stratified 2D numpy array for buffer visualization.
        Array order is bottom->top to match origin="lower" in imshow.

        Previous implementation used a 3x1 array ([bot, mid, top]) which looks very blocky.
        We upsample vertically (piecewise linear bot->mid->top) and tile horizontally so
        Matplotlib interpolation produces smooth transitions.
        """
        try:
            t_top = float(top)
            t_mid = float(mid)
            t_bot = float(bot)
        except Exception:
            t_top, t_mid, t_bot = 0.0, 0.0, 0.0

        layers = 120
        split = layers // 2
        lower = np.linspace(t_bot, t_mid, split, endpoint=False, dtype=float)
        upper = np.linspace(t_mid, t_top, layers - split, endpoint=True, dtype=float)
        temps = np.concatenate([lower, upper], axis=0)

        cols = 12
        arr = np.tile(temps[:, np.newaxis], (1, cols))
        arr[np.isnan(arr)] = 0.0
        return arr

    def _create_sparkline(self):
        # Minimal placeholder to prevent crash; extend as needed
        # You can implement the actual sparkline drawing here
        pass

    def __init__(self, parent: tk.Widget, height: int = 280, datastore=None):
        super().__init__(parent, bg=COLOR_ROOT)
        self._start_time = time.time()
        self.height = height
        if datastore is not None:
            self.datastore = datastore
        else:
            self.datastore = get_shared_datastore()
        # Entfernt: configure(height) und pack_propagate(False) für flexibles Layout

        self.data = np.array([[60.0], [50.0], [40.0]])
        self._last_temps = None  # type: ignore
        self._last_spark_update = 0

        # Betriebsmodus timeline (in-memory only)
        self._mode_segments: deque[tuple[datetime, Optional[datetime], str]] = deque(maxlen=120)
        self._mode_color_map: dict[str, str] = {}
        self._mode_palette = [COLOR_PRIMARY, COLOR_SUCCESS, COLOR_WARNING, COLOR_DANGER, COLOR_INFO]

        self.layout = tk.Frame(self, bg=COLOR_ROOT)
        self.layout.pack(fill=tk.BOTH, expand=True)
        self.layout.grid_columnconfigure(0, weight=1)
        self.layout.grid_rowconfigure(0, weight=1)

        self.plot_frame = tk.Frame(self.layout, bg=COLOR_CARD)
        # sticky="nsew" (nicht "new"!): self.layout gibt Zeile 0 weight=1, sie
        # bekommt also die komplette verfuegbare Hoehe der Puffer-Karte (die
        # per Grid-Gewicht in app.py bewusst groesszuegig bemessen ist). Mit
        # nur "new" bleibt plot_frame auf seiner natuerlichen (kleinen)
        # Groesse stehen und der ganze zusaetzliche Platz bleibt darunter als
        # leere schwarze Flaeche stehen - das war zwischenzeitlich hier so
        # eingestellt ("Keep the heatmap compact"), hat aber genau die
        # gemeldete grosse Luecke unter der Puffer-Karte verursacht. Mit
        # "nsew" streckt sich plot_frame ueber die komplette Zeile; die
        # Heatmap (FigureCanvasTkAgg) und die Mode-Timeline (fixe 80px durch
        # mode_canvas_container) skalieren automatisch mit.
        self.plot_frame.grid(row=0, column=0, sticky="nsew")
        self.plot_frame.grid_propagate(False)
        # WICHTIG: plot_frame's eigene Kinder (mode_label, canvas_widget,
        # mode_canvas_container) werden per PACK verwaltet, nicht per grid.
        # grid_propagate(False) schuetzt nur gegen GRID-Kinder - es hat also
        # NICHT verhindert, dass das Matplotlib-Canvas (das sich bei jedem
        # <Configure>-Event selbst per config(width=,height=) vergroessert,
        # siehe FigureCanvasTkAgg) seine gewachsene Groesse als "natuerliche"
        # Anforderung nach oben durchreicht: plot_frame -> self (BufferStorage-
        # View, dessen pack_propagate frueher bewusst entfernt wurde) ->
        # buffer_card. Das erzeugte eine Rueckkopplungsschleife: buffer_card
        # bekam dadurch immer mehr vom Grid-Gewicht in app.py "gestohlen",
        # egal welches Verhaeltnis dort eingestellt war (Energiefluss blieb
        # bei minsize haengen, Puffer wucherte auf fast die volle Hoehe).
        # pack_propagate(False) hier unterbindet genau dieses Hochreichen:
        # plot_frame bekommt seine Groesse weiterhin ausschliesslich von
        # aussen (self.layout Zeile 0, weight=1, sticky=nsew) vorgegeben.
        self.plot_frame.pack_propagate(False)

        self.val_texts = []

        # Reduzierte Figures für 60:40 Layout
        fig_width = 4.4  # etwas groesser fuer mehr Lesbarkeit
        fig_height = 2.9
        self._create_figure(fig_width, fig_height)
        self._setup_plot()

    def resize(self, height: int) -> None:
        """Adjust container height without recreating heavy matplotlib assets."""
        elapsed = time.time() - self._start_time
        if DEBUG_LOG:
            print(f"[BUFFER] resize() at {elapsed:.3f}s -> {height}")
        self.height = max(160, int(height))
        try:
            self.plot_frame.configure(height=self.height)
        except Exception:
            pass

    def _create_figure(self, fig_width: float, fig_height: float) -> None:
        if hasattr(self, "canvas_widget") and self.canvas_widget.winfo_exists():
            self.canvas_widget.destroy()
        if hasattr(self, "mode_canvas") and self.mode_canvas.winfo_exists():
            try:
                self.mode_canvas.destroy()
            except Exception:
                pass
        if hasattr(self, "mode_canvas_container") and self.mode_canvas_container.winfo_exists():
            try:
                self.mode_canvas_container.destroy()
            except Exception:
                pass
        if hasattr(self, "mode_label") and self.mode_label.winfo_exists():
            try:
                self.mode_label.destroy()
            except Exception:
                pass
        self.fig = Figure(figsize=(fig_width, fig_height), dpi=100)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(COLOR_CARD)

        # Betriebsmodus headline (above heatmap)
        self.mode_label = tk.Label(
            self.plot_frame,
            text="Betriebsmodus: --",
            bg=COLOR_CARD,
            fg=COLOR_TEXT,
            font=("Segoe UI", 14, "bold"),
            anchor="w",
        )
        self.mode_label.pack(side=tk.TOP, fill=tk.X, padx=6, pady=(2, 0))

        self.canvas = FigureCanvasTkAgg(self.fig, master=self.plot_frame)
        self.canvas_widget = self.canvas.get_tk_widget()
        # Flexible Skalierung ohne min_width Constraint für 50/50 Layout
        self.canvas_widget.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Mode timeline bar under the heatmap - tall enough to carry hour
        # tick marks in addition to the start/end labels.
        #
        # WICHTIG: mode_canvas selbst nur mit height=76 zu konfigurieren hat
        # nicht ausgereicht, um es klein zu halten, seit plot_frame durch die
        # Grid-Gewichtung (body-Zeile fuer die Puffer-Karte) viel groesser
        # geworden ist - die Zeitleiste wurde dadurch zu einer grossen,
        # groesstenteils leeren Flaeche ("der Platz wo der Betriebsmodus
        # angezeigt wird ist sehr gross"). Ein eigener Container mit
        # pack_propagate(False) UND fill=X (kein expand) erzwingt eine feste
        # Hoehe garantiert, unabhaengig davon, wie gross plot_frame wird.
        self.mode_canvas_container = tk.Frame(self.plot_frame, height=80, bg=COLOR_CARD)
        self.mode_canvas_container.pack(side=tk.BOTTOM, fill=tk.X)
        self.mode_canvas_container.pack_propagate(False)

        self.mode_canvas = tk.Canvas(
            self.mode_canvas_container,
            bg=COLOR_CARD,
            highlightthickness=0,
        )
        self.mode_canvas.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 2))
        try:
            self.mode_canvas.bind("<Configure>", lambda _evt: self._draw_mode_timeline())
        except Exception:
            pass

    # Feine, ruhige "Glas"-Optik fuer die Tank-Gefaesse (Nutzer-Feedback:
    # zunaechst mehrere Iterationen ueber HTML-Mockups abgestimmt - "3D-Optik"
    # aber "nicht so bunt und clumsy, etwas feiner"). Drei zusaetzliche,
    # rein optische Ebenen ueber der bestehenden Farbe/Form, OHNE die
    # Daten-Logik (Schichtung, Farbverlauf-Wissenschaft, Positionen) oder die
    # bestehende Titel-/Text-Platzierung anzufassen:
    #   1) Glow: gestapelte, transparente Kopien der Gefaess-Form dahinter
    #      (zorder 1, unter allem anderen) - kein echter Weichzeichner noetig.
    #   2) Zylinder-Schattierung: ein statisches RGBA-Overlay (hell-dunkel-
    #      hell in Fliess-Richtung), auf die Gefaess-Form geclippt - macht die
    #      Roehre rund statt flach, ohne die darunterliegende Temperaturfarbe
    #      zu veraendern.
    #   3) Kappen-Glanzlicht oben: eine kleine, halbtransparente weisse
    #      Ellipse nahe der Oberkante (Blick von leicht oben ins Glas).
    @staticmethod
    def _cyl_shade_rgba(h: int = 160, w: int = 80):
        """Statisches Hell-Dunkel-Hell-Schattierungs-Overlay (links hell =
        Lichteinfall, Mitte/rechts dunkler Kern, schmaler Reflex nahe dem
        rechten Rand) als zwei RGBA-Bilder (weiss/schwarz getrennt, da ein
        einzelnes RGBA-Array keine gemischten Vorzeichen darstellen kann)."""
        x = np.linspace(0.0, 1.0, w)
        white_a = 0.55 * np.exp(-((x - 0.05) ** 2) / (2 * 0.045 ** 2))
        white_a += 0.30 * np.exp(-((x - 0.90) ** 2) / (2 * 0.03 ** 2))
        black_a = 0.40 * np.exp(-((x - 0.60) ** 2) / (2 * 0.17 ** 2))
        white_rgba = np.zeros((h, w, 4))
        white_rgba[:, :, 0:3] = 1.0
        white_rgba[:, :, 3] = np.clip(white_a, 0.0, 1.0)
        black_rgba = np.zeros((h, w, 4))
        black_rgba[:, :, 3] = np.clip(black_a, 0.0, 1.0)
        return white_rgba, black_rgba

    def _add_vessel_depth(self, x0: float, width: float, y0: float, top: float,
                           body_patch: FancyBboxPatch, glow_color: str, rounding: float = 0.17) -> None:
        """Legt Glow + Zylinder-Schattierung + Kappen-Glanzlicht um ein
        bereits gezeichnetes Tank-Gefaess (body_patch dient nur als
        Clip-Pfad fuer die Schattierung)."""
        for pad, alpha in ((0.075, 0.07), (0.05, 0.11), (0.028, 0.16)):
            glow = FancyBboxPatch(
                (x0 - pad, y0 - pad), width + 2 * pad, (top - y0) + 2 * pad,
                boxstyle=f"round,pad=0.0,rounding_size={rounding + pad}",
                transform=self.ax.transAxes, linewidth=0, facecolor=glow_color,
                alpha=alpha, zorder=1,
            )
            self.ax.add_patch(glow)

        white_rgba, black_rgba = self._cyl_shade_rgba()
        im_w = self.ax.imshow(white_rgba, aspect="auto", origin="lower",
                              extent=[x0, x0 + width, y0, top], zorder=3)
        im_w.set_clip_path(body_patch)
        im_b = self.ax.imshow(black_rgba, aspect="auto", origin="lower",
                              extent=[x0, x0 + width, y0, top], zorder=3)
        im_b.set_clip_path(body_patch)

        cap_h = (top - y0) * 0.09
        self.ax.add_patch(Ellipse(
            (x0 + width / 2, top - cap_h * 0.35), width * 0.86, cap_h,
            transform=self.ax.transAxes, facecolor="white", edgecolor="none",
            alpha=0.16, zorder=4.3,
        ))

    def _setup_plot(self) -> None:
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()
        self.ax.set_facecolor(COLOR_CARD)

        self.norm = Normalize(vmin=self.TEMP_MIN, vmax=self.TEMP_MAX)

        # Flat, modern vessel: thin outline only, no drop-shadow/glossy 3D
        # treatment. Top capped below the title line so the "PUFFER" /
        # "WARMWASSER" labels always keep a clear gap above the graphic.
        PUFFER_TOP = 0.86
        BOILER_TOP = 0.56

        self.im = self.ax.imshow(
            self.data,
            aspect="auto",
            interpolation="bicubic",
            cmap=self._build_cmap(),
            norm=self.norm,
            origin="lower",
            extent=[0.06, 0.50, 0.06, PUFFER_TOP],
            zorder=2,
        )

        puffer_cyl = FancyBboxPatch(
            (0.09, 0.06),
            0.34,
            PUFFER_TOP - 0.06,
            # Staerker abgerundete Ecken (0.10 -> 0.17) + duennerer Rand fuer
            # eine modernere, weichere Silhouette (Nutzer-Feedback: "form
            # etwas moderner").
            boxstyle="round,pad=0.02,rounding_size=0.17",
            transform=self.ax.transAxes,
            linewidth=1.1,
            edgecolor=COLOR_ROOT,
            facecolor="none",
            alpha=0.75,
            zorder=4,
        )
        self.im.set_clip_path(puffer_cyl)
        self.ax.add_patch(puffer_cyl)
        # Glow/Zylinder-Rundung/Kappen-Glanzlicht - "kuehler" Glow (aus der
        # blauen Seite der Ocean-to-Ember-Palette), da der Puffer im
        # ueblichen Betriebsbereich meist im kuehleren Skalenabschnitt liegt.
        self._add_vessel_depth(0.09, 0.34, 0.06, PUFFER_TOP, puffer_cyl, glow_color="#1f7fa8")
        self.ax.add_patch(Ellipse((0.26, PUFFER_TOP), 0.34, 0.08, transform=self.ax.transAxes,
                                  edgecolor=COLOR_ROOT, facecolor="none", linewidth=1.0, alpha=0.7, zorder=4))
        self.ax.add_patch(Ellipse((0.26, 0.06), 0.34, 0.08, transform=self.ax.transAxes,
                                  edgecolor=COLOR_ROOT, facecolor="none", linewidth=1.0, alpha=0.7, zorder=4))
        # Feste Schriftgröße und feste Ränder für optimalen Sitz
        self.fig.subplots_adjust(left=0.04, right=0.96, top=0.91, bottom=0.10)
        title_kw = dict(color=COLOR_TITLE, fontsize=14, va="top", ha="center", weight="bold")
        self.ax.text(0.26, 0.985, "PUFFER", transform=self.ax.transAxes, **title_kw)
        self.ax.text(0.74, 0.985, "WARMWASSER", transform=self.ax.transAxes, **title_kw)

        # Subtle dark outline behind the white value text keeps it legible no
        # matter which part of the gradient (pale ice-blue, bright orange) sits
        # behind it.
        text_outline = [path_effects.withStroke(linewidth=3, foreground=COLOR_ROOT, alpha=0.85)]

        # Temperatur-Textfelder links
        self.val_texts = [
            self.ax.text(0.12, 0.78, "--°C", color=COLOR_TEXT, fontsize=16, va="center", ha="left",
                         transform=self.ax.transAxes, weight="bold", zorder=5, path_effects=text_outline),
            self.ax.text(0.12, 0.46, "--°C", color=COLOR_TEXT, fontsize=16, va="center", ha="left",
                         transform=self.ax.transAxes, weight="bold", zorder=5, path_effects=text_outline),
            self.ax.text(0.12, 0.14, "--°C", color=COLOR_TEXT, fontsize=16, va="center", ha="left",
                         transform=self.ax.transAxes, weight="bold", zorder=5, path_effects=text_outline),
        ]

        self.boiler_rect = FancyBboxPatch(
            (0.58, 0.06),
            0.32,
            BOILER_TOP - 0.06,
            boxstyle="round,pad=0.02,rounding_size=0.17",
            transform=self.ax.transAxes,
            linewidth=1.0,
            edgecolor=COLOR_ROOT,
            facecolor=self._temp_color(60),
            alpha=0.95,
            zorder=4,
        )
        self.ax.add_patch(self.boiler_rect)
        # Warmer Glow (Bernstein/Orange-Seite der Palette) - Warmwasser liegt
        # ueblicherweise im oberen, waermeren Skalenbereich.
        self._add_vessel_depth(0.58, 0.32, 0.06, BOILER_TOP, self.boiler_rect, glow_color="#e8542f")
        self.ax.add_patch(Ellipse((0.74, BOILER_TOP), 0.32, 0.08, transform=self.ax.transAxes,
                                  edgecolor=COLOR_ROOT, facecolor="none", linewidth=1.0, alpha=0.7, zorder=4))
        self.ax.add_patch(Ellipse((0.74, 0.06), 0.32, 0.08, transform=self.ax.transAxes,
                                  edgecolor=COLOR_ROOT, facecolor="none", linewidth=1.0, alpha=0.7, zorder=4))
        self.ax.text(0.74, 0.62, "Boiler", transform=self.ax.transAxes,
             color=COLOR_TITLE, fontsize=13, va="top", ha="center", weight="bold", zorder=5)
        # Boiler-Temperaturtext
        self.boiler_text = self.ax.text(0.74, 0.34, "--°C", color=COLOR_TEXT, fontsize=23, va="center", ha="center",
                                        transform=self.ax.transAxes, weight="bold", zorder=5, path_effects=text_outline)
        # Boiler-Modus-Text (Betriebsmodus)
        self.boiler_mode_text = self.ax.text(
            0.74,
            0.22,
            "",
            color=COLOR_TEXT,
            fontsize=11,
            va="center",
            ha="center",
            transform=self.ax.transAxes,
            weight="bold",
            zorder=5,
        )

        divider = make_axes_locatable(self.ax)
        cax = divider.append_axes("right", size="4%", pad=0.15)
        cbar = self.fig.colorbar(self.im, cax=cax, orientation="vertical")
        cbar.set_label("°C", rotation=0, labelpad=10, color=COLOR_TEXT, fontsize=11)
        # Mark cold/knee/hot instead of only the auto ticks, so the gradient
        # reads as "cold -> lauwarm -> heiß" at a glance. The blue/orange knee
        # (53-55°C) is only 2°C wide, too narrow to label both ends without
        # overlapping text, so a single midpoint tick stands in for it.
        knee_mid = (self.TEMP_BLUE_MAX + self.TEMP_ORANGE_FROM) / 2.0
        threshold_ticks = sorted({self.TEMP_MIN, knee_mid, self.TEMP_MAX})
        cbar.set_ticks(threshold_ticks)
        cbar.set_ticklabels([f"{t:.0f}" for t in threshold_ticks])
        cbar.ax.tick_params(labelsize=10, colors=COLOR_TEXT)
        cbar.outline.set_edgecolor(COLOR_BORDER)
        cbar.outline.set_linewidth(0.8)

    @staticmethod
    def _build_cmap() -> LinearSegmentedColormap:
        # "Ocean-to-Ember" Palette (Nutzer-Feedback: kraeftiger/kontrastreicher
        # als die vorherige Blau/Orange/Rot-Abstufung, mit eigener statt von
        # den Theme-Farben abgeleiteter Farbwahl). Kalt = dunkles Navy ueber
        # Ozean-Tuerkis zu hellem Cyan, warm = Bernstein ueber Orange-Rot zu
        # tiefem Karminrot. Positionen sind direkt als Anteil der TEMP_MIN..
        # TEMP_MAX-Spanne (35-75°C) gesetzt, exakt wie im abgestimmten
        # Vorschau-Rendering.
        stops: list[tuple[float, str]] = [
            (0.00, "#0a2540"),
            (0.12, "#0e3f6b"),
            (0.28, "#0f7ea8"),
            (0.42, "#22c3d6"),
            (0.55, "#8fe3e0"),
            (0.62, "#f2e07a"),
            (0.72, "#f4a53d"),
            (0.85, "#e8542f"),
            (1.00, "#c81e3a"),
        ]
        return LinearSegmentedColormap.from_list("dashboard_temp_ocean_ember", stops, N=512)

    def _temp_color(self, temp: float) -> str:
        rgba = self._build_cmap()(self.norm(temp))
        r, g, b = [int(255 * c) for c in rgba[:3]]
        return f"#{r:02x}{g:02x}{b:02x}"

    def _get_boiler_color(self, temp: float) -> str:
        return self._temp_color(temp)

    def update_data(self, data: dict):
        """Update für BufferStorageView: erwartet dict mit final keys."""
        import time
        # Always capture mode changes, even if we throttle heavy redraw.
        mode_changed = self._update_mode_state(data)

        now_mono = time.monotonic()
        last = getattr(self, "_last_redraw_ts", 0.0)
        # Increased from 3s to 10s - matplotlib heatmap redraw is expensive on Pi
        if now_mono - last < 10.0:
            if mode_changed:
                self._draw_mode_timeline()
            return
        self._last_redraw_ts = now_mono
        top = float(data.get(BUF_TOP_C) or 0.0)
        mid = float(data.get(BUF_MID_C) or 0.0)
        bot = float(data.get(BUF_BOTTOM_C) or 0.0)
        # Boiler = Warmwasser (final key only).
        boiler = float(data.get(BMK_WARMWASSER_C) or 0.0)
        now = time.time()
        if not hasattr(self, '_last_heat_dbg'):
            self._last_heat_dbg = 0.0
        if now - self._last_heat_dbg > 2.0:
            if DEBUG_LOG:
                print(f"[BUFFER_PARSED] top={top} mid={mid} bot={bot} boiler={boiler}", flush=True)
            self._last_heat_dbg = now
        self.update_temperatures(top, mid, bot, boiler)

        # Timeline redraw (cheap)
        self._draw_mode_timeline()

    @staticmethod
    def _parse_payload_dt(payload: dict) -> datetime:
        ts = payload.get("timestamp") or payload.get("Zeitstempel")
        if ts:
            try:
                raw = str(ts).strip().replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw)
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except Exception:
                pass
        return datetime.now(timezone.utc)

    def _mode_color(self, mode: str) -> str:
        mode = (mode or "").strip()
        if not mode:
            return COLOR_SUBTEXT
        if mode in self._mode_color_map:
            return self._mode_color_map[mode]
        color = self._mode_palette[len(self._mode_color_map) % len(self._mode_palette)]
        self._mode_color_map[mode] = color
        return color

    def _update_mode_state(self, payload: dict) -> bool:
        mode_raw = payload.get(BMK_BETRIEBSMODUS)
        if mode_raw in (None, ""):
            return False
        try:
            mode = str(mode_raw).strip()
        except Exception:
            return False
        if not mode:
            return False

        color = self._mode_color(mode)

        # Update headline over heatmap (prominent + color matches timeline)
        try:
            if hasattr(self, "mode_label"):
                self.mode_label.configure(text=f"Betriebsmodus: {mode}", fg=color)
        except Exception:
            pass

        # (Optional) keep text over boiler empty to avoid duplication.
        try:
            if hasattr(self, "boiler_mode_text"):
                self.boiler_mode_text.set_text("")
        except Exception:
            pass

        dt = self._parse_payload_dt(payload)

        # Update segments (close previous on change)
        if self._mode_segments and self._mode_segments[0][2] == mode:
            return False
        if self._mode_segments:
            start_dt, _end_dt, prev_mode = self._mode_segments[0]
            self._mode_segments[0] = (start_dt, dt, prev_mode)
        self._mode_segments.appendleft((dt, None, mode))
        return True

    def _draw_mode_timeline(self, hours: float = 24.0) -> None:
        if not hasattr(self, "mode_canvas"):
            return
        c = self.mode_canvas
        try:
            width = int(c.winfo_width())
        except Exception:
            width = 0
        if width <= 10:
            return
        try:
            height = int(c.winfo_height())
        except Exception:
            height = 76
        height = max(60, height)

        try:
            c.delete("all")
        except Exception:
            return

        now = datetime.now(timezone.utc)
        window_end = now
        window_start = now - timedelta(hours=float(hours))
        span_s = max(1.0, (window_end - window_start).total_seconds())

        # Border line
        try:
            c.create_rectangle(1, 1, width - 1, height - 1, outline=COLOR_BORDER)
        except Exception:
            pass

        def x(dt: datetime) -> int:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            else:
                dt = dt.astimezone(timezone.utc)
            t = (dt - window_start).total_seconds()
            t = max(0.0, min(span_s, t))
            return int(2 + (width - 4) * (t / span_s))

        # Legend (mode -> color)
        legend_y = 2
        legend_x = 6
        legend_max_x = width - 6
        unique_modes: list[str] = []
        try:
            for start_dt, end_dt, mode in list(self._mode_segments):
                seg_start = start_dt
                seg_end = end_dt or now
                if seg_end < window_start or seg_start > window_end:
                    continue
                m = (mode or "").strip()
                if m and m not in unique_modes:
                    unique_modes.append(m)
        except Exception:
            unique_modes = []

        for mode in unique_modes[:6]:
            color = self._mode_color(mode)
            try:
                c.create_rectangle(legend_x, legend_y + 2, legend_x + 10, legend_y + 12, fill=color, outline="")
                c.create_text(legend_x + 14, legend_y + 12, text=mode, fill=color, anchor="sw")
            except Exception:
                pass
            legend_x += 14 + int(max(40, len(mode) * 7))
            if legend_x >= legend_max_x:
                break
        if len(unique_modes) > 6 and legend_x < legend_max_x:
            try:
                c.create_text(legend_max_x, legend_y + 12, text=f"+{len(unique_modes) - 6}", fill=COLOR_SUBTEXT, anchor="se")
            except Exception:
                pass

        # Timeline segments from oldest->newest for correct overlap
        segments = list(self._mode_segments)
        segments.reverse()

        bar_top = 18
        bar_bottom = height - 24
        for start_dt, end_dt, mode in segments:
            seg_start = start_dt
            seg_end = end_dt or now
            if seg_end < window_start or seg_start > window_end:
                continue
            seg_start = max(seg_start, window_start)
            seg_end = min(seg_end, window_end)
            x0 = x(seg_start)
            x1 = x(seg_end)
            if x1 <= x0:
                continue
            color = self._mode_color(mode)
            try:
                c.create_rectangle(x0, bar_top, x1, bar_bottom, fill=color, outline="")
            except Exception:
                pass

        # Hour tick marks every 6h so the timeline reads as an actual time
        # axis instead of a bare, unlabeled strip.
        tick_step_h = 6
        n_ticks = int(hours // tick_step_h)
        for i in range(n_ticks + 1):
            tick_dt = window_start + timedelta(hours=i * tick_step_h)
            tx = x(tick_dt)
            try:
                c.create_line(tx, bar_bottom, tx, bar_bottom + 6, fill=COLOR_BORDER)
                anchor = "s"
                if i == 0:
                    anchor = "sw"
                elif i == n_ticks:
                    anchor = "se"
                c.create_text(tx, height - 2, text=tick_dt.astimezone().strftime("%H:%M"),
                              fill=COLOR_SUBTEXT, anchor=anchor, font=("Segoe UI", 8))
            except Exception:
                pass


    def update_temperatures(self, top, mid, bot, boiler):
        # Update heatmap with stratified 2D array
        self.data = self._build_stratified_data(top, mid, bot)
        
        if hasattr(self, 'im'):
            self.im.set_data(self.data)
            self.im.set_cmap(self._build_cmap())
            self.im.set_norm(self.norm)
        # Update left temperature texts
        if hasattr(self, 'val_texts') and len(self.val_texts) == 3:
            self.val_texts[0].set_text(f"{top:.1f}°C")
            self.val_texts[1].set_text(f"{mid:.1f}°C")
            self.val_texts[2].set_text(f"{bot:.1f}°C")
        if hasattr(self, 'boiler_text'):
            self.boiler_text.set_text(f"{boiler:.1f}°C")
        if hasattr(self, 'boiler_rect'):
            self.boiler_rect.set_facecolor(self._temp_color(boiler))
        # Redraw canvas only if widget exists
        if hasattr(self, 'canvas') and hasattr(self, 'canvas_widget') and self.canvas_widget.winfo_exists():
            try:
                self.canvas.draw_idle()
            except Exception:
                try:
                    self.canvas.draw()
                except Exception:
                    pass

    def _load_pv_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            if DEBUG_LOG:
                print("[DEBUG] Kein Datastore für PV-Serie!")
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        now = datetime.now()

        # DB timestamps may come in different string formats; don't rely on SQL text filtering/order.
        rows = self.datastore.get_recent_fronius(hours=None, limit=4000)
        if DEBUG_LOG:
            print(f"[DEBUG] get_recent_fronius liefert {len(rows)} Zeilen")
            if rows:
                print(f"[DEBUG] Beispiel-Eintrag Fronius: {rows[-1]}")
                print(f"[DEBUG] Alle Keys im letzten Eintrag: {list(rows[-1].keys())}")

        parsed_rows: list[tuple[datetime, dict]] = []
        for entry in rows:
            ts = self._parse_ts(entry.get('timestamp'))
            if ts is None:
                continue
            # Cap future timestamps (clock drift)
            if ts > now:
                ts = now
            parsed_rows.append((ts, entry))
        parsed_rows.sort(key=lambda t: t[0])

        samples: list[tuple[datetime, float]] = []
        for ts, entry in parsed_rows[-2500:]:
            pv_kw = self._safe_float(entry.get('pv'))
            if pv_kw is None or ts < cutoff:
                continue
            # Some historical sources (older CSV/API logs) may have stored PV power in W
            # while the dashboard expects kW everywhere. Heuristic: values above ~200 kW
            # are extremely unlikely for this setup -> treat as W and convert.
            if pv_kw > 200.0:
                pv_kw = pv_kw / 1000.0
            # PV should never be significantly negative; treat small noise as 0.
            if pv_kw < -0.2:
                continue
            pv_kw = max(0.0, pv_kw)
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, pv_kw))
        if DEBUG_LOG:
            print(f"[DEBUG] PV-Samples: {len(samples)}")
        return self._aggregate_series(samples)

    def _load_outdoor_temp_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            if DEBUG_LOG:
                print("[DEBUG] Kein Datastore für Außentemperatur!")
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        now = datetime.now()

        rows = self.datastore.get_recent_heating(hours=None, limit=4000)
        if DEBUG_LOG:
            print(f"[DEBUG] get_recent_heating liefert {len(rows)} Zeilen (outdoor)")
            if rows:
                print(f"[DEBUG] Beispiel-Eintrag (outdoor): {rows[0]}")
                print(f"[DEBUG] Keys im ersten Eintrag: {list(rows[0].keys())}")
            else:
                print("[DEBUG] Keine Daten von get_recent_heating (outdoor)")

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
            # Plausibility clamp (sensor glitches)
            if not (-40.0 <= val <= 60.0):
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, val))
        if DEBUG_LOG:
            print(f"[DEBUG] Outdoor-Samples: {len(samples)}")
        return self._aggregate_series(samples)

    def _load_puffer_series(self, hours: int = 24, bin_minutes: int = 15) -> list[tuple[datetime, float]]:
        if not self.datastore:
            if DEBUG_LOG:
                print("[DEBUG] Kein Datastore für Puffer-Serie!")
            return []
        cutoff = datetime.now() - timedelta(hours=hours)
        now = datetime.now()

        rows = self.datastore.get_recent_heating(hours=None, limit=4000)
        if DEBUG_LOG:
            print(f"[DEBUG] get_recent_heating liefert {len(rows)} Zeilen (puffer)")
            if rows:
                print(f"[DEBUG] Beispiel-Eintrag (puffer): {rows[0]}")
                print(f"[DEBUG] Keys im ersten Eintrag: {list(rows[0].keys())}")
            else:
                print("[DEBUG] Keine Daten von get_recent_heating (puffer)")

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
            mid = self._safe_float(entry.get('mid'))
            if mid is None or ts < cutoff:
                continue
            if not (-40.0 <= mid <= 120.0):
                continue
            ts_bin = ts - timedelta(minutes=ts.minute % bin_minutes,
                                    seconds=ts.second,
                                    microseconds=ts.microsecond)
            samples.append((ts_bin, mid))
        if DEBUG_LOG:
            print(f"[DEBUG] Puffer-Samples: {len(samples)}")
        aggregated = self._aggregate_series(samples)
        if len(aggregated) < 3:
            return aggregated
        smoothed: list[tuple[datetime, float]] = []
        for idx, current in enumerate(aggregated):
            window_vals = aggregated[max(0, idx - 1): min(len(aggregated), idx + 2)]
            smoothed.append((current[0], sum(val for _, val in window_vals) / len(window_vals)))
        return smoothed

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
        # Parse timestamps coming from different sources.
        # Some are naive ("YYYY-MM-DD HH:MM:SS"), some are offset-aware ("...+01:00").
        # For UI charting we normalize to *naive local time* to avoid TypeError
        # when comparing offset-aware vs. naive datetimes.
        from datetime import datetime
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

    def stop(self):
        """Cleanup resources to prevent memory leaks and segfaults."""
        try:
            import matplotlib.pyplot as plt
            if hasattr(self, 'fig') and self.fig:
                plt.close(self.fig)
                self.fig = None
            if hasattr(self, 'canvas_widget') and self.canvas_widget:
                self.canvas_widget.destroy()
        except Exception:
            pass
