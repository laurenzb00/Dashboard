"""Waerme-Tab: Pufferspeicher live, Solarthermie und Holz.

* Live-Grafik: zwei Puffer in Reihe (je 2000 l; Fuehler: Puffer 1 oben,
  Puffer 2 oben, Puffer 2 unten) und Boiler als Tanks mit Temperaturschichtung,
  Waermemenge je Puffer, Ladezustand, Kessel-/Solar-Anzeige.
* Kacheln: nutzbare Energie, Solar/Holz heute, letztes Einheizen,
  Holz in der Saison (Raummeter), Solaranteil der Saison.
* Einheiz-Empfehlung (core/heating_forecast): "Heute Abend einheizen - Puffer
  reicht bis ca. 20:30 · Sonne morgen ≈ 6 kWh".
* Chart "Heute": Waermeinhalt der Speicher ueber den Tag; markiert, wann die
  Sonne bzw. das Holz geladen hat, plus gestrichelte Prognose bis Mitternacht.
* Chart "Saison": kumulierter Waermeeintrag Holz/Solar seit 1. September,
  Einheizvorgaenge als Striche am unteren Rand.
* Chart "Woche": erwarteter Waermebedarf der naechsten 7 Tage aus dem
  gelernten Verbrauchsmodell (core/heat_demand) und der Temperaturprognose.

Berechnung siehe core/heating_stats.py.
"""
from __future__ import annotations

import logging
import math
import threading
import tkinter as tk
from datetime import date, datetime, timedelta

import customtkinter as ctk
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import numpy as np
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from core import bmk_interpret
from core import heating_forecast as hf
from core import heating_stats as hs
from core import pv_forecast
from core.datastore import get_shared_datastore
from core.schema import BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_BOTTOM_C, BUF_MID_C, BUF_TOP_C
from ui.components.chart_style import apply_chart_style
from ui.components.metric_tile import MetricTile
from ui.components.tab_shell import TabShell
from ui.components.ui_dispatch import UiQueuePumpMixin
from ui.styles import (
    BUTTON_HEIGHT_SECONDARY,
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_DANGER,
    COLOR_PRIMARY,
    COLOR_ROOT,
    COLOR_SUBTEXT,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_WARNING,
    FONT_SIZE_BODY,
    PADDING_SECTION,
    get_safe_font,
)
from ui.views.chart_resize_mixin import MatplotlibCanvasResizeMixin

logger = logging.getLogger(__name__)

COLOR_WOOD = "#e8542f"

# Feuer-Historie fuer die Karten unter dem Chart
FIRE_HISTORY_DAYS = 60          # Fenster fuer "typische Brennzeit" (bmk_status wird erst seit 11/2026 geschrieben)
TYP_BURN_CACHE_S = 3600.0       # 60 Tage Minutendaten sind teuer -> hoechstens stuendlich neu
LIVE_BMK_HOURS = 12.0           # Vorlauf fuer Trend/Brennzeit der Live-Deutung
LIVE_BMK_MAX_AGE_MIN = 15.0     # aelter -> keine Live-Aussage mehr
COLOR_SOLAR = "#f4b63d"

# Gleiche Farbskala wie die Puffer-Heatmap im Energie-Tab (ui/temp_colors.py)
from ui.temp_colors import temp_color  # noqa: E402

LIVE_REFRESH_MS = 15_000
CHART_REFRESH_MS = 5 * 60_000


def tank_layer_temp(frac: float, t_top, t_bot) -> float | None:
    """Temperatur an relativer Hoehe (0 = oben, 1 = unten) eines einzelnen Tanks.

    Die Fuehler sitzen bei 10 % (oben) und 90 % (unten); dazwischen linear,
    ausserhalb konstant. Fehlt ein Wert, gilt der andere.
    """
    if t_top is None:
        return t_bot
    if t_bot is None or frac <= 0.1:
        return t_top
    if frac >= 0.9:
        return t_bot
    return t_top + (t_bot - t_top) * (frac - 0.1) / 0.8


class TankCanvas(tk.Canvas):
    """Zeichnet die zwei Puffer in Reihe und den Boiler als Tanks mit Temperaturschichtung."""

    def __init__(self, parent):
        super().__init__(parent, bg=COLOR_CARD, highlightthickness=0)
        self.state: dict = {}
        self.bind("<Configure>", lambda _e: self.redraw())

    def set_state(self, **state) -> None:
        self.state.update(state)
        # Nur neu zeichnen, wenn sich sichtbar etwas geaendert hat: ein
        # Redraw erzeugt mehrere hundert Canvas-Objekte (Farbverlauf) und
        # kostete auf dem Pi spuerbar UI-Zeit - alle 15 s, meist umsonst.
        def _norm(v):
            return round(v, 1) if isinstance(v, float) else v
        try:
            sig = (self.winfo_width(), self.winfo_height(),
                   tuple(sorted((k, repr(_norm(v))) for k, v in self.state.items())))
        except Exception:
            sig = None
        if sig is not None and sig == getattr(self, "_drawn_sig", None):
            return
        self._drawn_sig = sig
        self.redraw()

    def _tank(self, x, y, w, h, t_top, t_bot, title, subtitle, labels=(), note=""):
        r = min(w * 0.22, 26)
        yy = float(y)
        while yy < y + h:
            dy_top = yy - y + 0.5
            dy_bot = (y + h) - yy - 0.5
            in_corner = dy_top < r or dy_bot < r
            step = 1.0 if in_corner else 2.0
            inset = 0.0
            if dy_top < r:
                inset = r - math.sqrt(max(0.0, r * r - (r - dy_top) ** 2))
            elif dy_bot < r:
                inset = r - math.sqrt(max(0.0, r * r - (r - dy_bot) ** 2))
            col = temp_color(tank_layer_temp((yy - y) / h, t_top, t_bot))
            self.create_rectangle(x + inset, yy, x + w - inset, yy + step, fill=col, outline="")
            yy += step
        # Umriss: exakt dieselben Radien wie die Fuellung
        oc, ow = "#d7dde8", 2
        self.create_line(x + r, y, x + w - r, y, fill=oc, width=ow)
        self.create_line(x + r, y + h, x + w - r, y + h, fill=oc, width=ow)
        self.create_line(x, y + r, x, y + h - r, fill=oc, width=ow)
        self.create_line(x + w, y + r, x + w, y + h - r, fill=oc, width=ow)
        for bx0, by0, start in ((x, y, 90), (x + w - 2 * r, y, 0), (x, y + h - 2 * r, 180), (x + w - 2 * r, y + h - 2 * r, 270)):
            self.create_arc(bx0, by0, bx0 + 2 * r, by0 + 2 * r, start=start, extent=90, style="arc", outline=oc, width=ow)
        # Glanzlicht
        self.create_line(x + w * 0.18, y + r, x + w * 0.18, y + h - r, fill="#ffffff", width=2, stipple="gray25")
        # Messwerte an den echten Fuehlerhoehen (frac: 0 = oben, 1 = unten)
        for frac, val in labels:
            if val is None:
                continue
            yy = y + h * frac
            self.create_line(x + w + 4, yy, x + w + 14, yy, fill=COLOR_SUBTEXT)
            self.create_text(x + w + 18, yy, text=f"{val:.0f}°", anchor="w", fill=COLOR_TEXT,
                             font=get_safe_font("Bahnschrift", 15, "bold"))
        self.create_text(x + w / 2, y + h + 18, text=title, fill=COLOR_TEXT,
                         font=get_safe_font("Bahnschrift", 15, "bold"))
        if subtitle:
            self.create_text(x + w / 2, y + h + 42, text=subtitle, fill=COLOR_SUBTEXT,
                             font=get_safe_font("Bahnschrift", 14))
        if note:
            self.create_text(x + w / 2, y + h + 64, text=note, fill=COLOR_SUBTEXT,
                             font=get_safe_font("Bahnschrift", 12, "italic"))

    def redraw(self) -> None:
        self.delete("all")
        W = max(10, self.winfo_width())
        H = max(10, self.winfo_height())
        st = self.state
        cfg = st.get("cfg") or hs.StorageConfig()
        top, mid, bot, warm = st.get("top"), st.get("mid"), st.get("bot"), st.get("warm")
        kessel = st.get("kessel")

        head_h = 38
        foot_h = 76
        avail_h = max(60, H - head_h - foot_h - 10)
        # Zwei Puffer in Reihe + Boiler; je Tank 46 px Platz rechts fuer die Fuehlerwerte
        gutter = 46
        tank_w = max(46, min(W * 0.17, avail_h * 0.42))
        boiler_w = tank_w * 0.75
        boiler_h = avail_h * 0.55
        gap = max(6.0, (W - 2 * tank_w - boiler_w - 2 * gutter) / 3.5)
        p1x = gap
        p2x = p1x + tank_w + gutter + gap * 0.5
        bx = p2x + tank_w + gutter + gap
        y0 = head_h

        # Fuehler: top = Puffer 1 oben, mid = Puffer 2 oben, bot = Puffer 2 unten.
        # Puffer 1 hat unten keinen Fuehler (geschaetzt = Puffer 2 oben).
        t1, t2 = hs.split_tanks(top, mid, bot, cfg)

        def sub(t) -> str:
            if t.usable_kwh is None:
                return ""
            return f"{t.usable_kwh:.0f} kWh · {t.charge_pct:.0f} %"

        self._tank(p1x, y0, tank_w, avail_h, top, mid,
                   f"Puffer 1 · {cfg.tank_liter:.0f} l", sub(t1),
                   labels=((0.1, top),), note="unten geschätzt")
        self._tank(p2x, y0, tank_w, avail_h, mid, bot,
                   f"Puffer 2 · {cfg.tank_liter:.0f} l", sub(t2),
                   labels=((0.1, mid), (0.9, bot)))
        self._tank(bx, y0 + avail_h - boiler_h, boiler_w, boiler_h, warm, warm,
                   f"Boiler {cfg.boiler_liter:.0f} l", "Warmwasser")
        if warm is not None:
            self.create_text(bx + boiler_w / 2, y0 + avail_h - boiler_h / 2, text=f"{warm:.0f}°",
                             fill="#ffffff", font=get_safe_font("Bahnschrift", 16, "bold"))

        # Kopfzeile: Kessel / Solar
        if kessel is not None:
            active = hs.kessel_active(hs.Bucket(ts=datetime.now(), kessel=kessel, top=top, mid=mid, bot=bot,
                                                warm=warm, outdoor=None))
            txt = f"🔥 Kessel {kessel:.0f}°" if active else f"Kessel {kessel:.0f}°"
            self.create_text(12, 16, text=txt, anchor="w", fill=COLOR_WOOD if active else COLOR_SUBTEXT,
                             font=get_safe_font("Bahnschrift", 15, "bold"))
        if st.get("solar_active"):
            self.create_text(W - 12, 16, text="☀ Solar lädt", anchor="e", fill=COLOR_SOLAR,
                             font=get_safe_font("Bahnschrift", 15, "bold"))


class _FactsStrip(tk.Canvas):
    """Kennzahlen unter dem Chart als Karten statt als lange Textzeile.

    Ersetzt die fruehere `statusbar`: dort wurden bis zu acht Textbrocken mit
    " · " aneinandergehaengt und in 11 px Grau umgebrochen - auf dem
    1920x1200-Touchscreen aus Betrachtungsabstand praktisch unlesbar, und zwei
    der Brocken ("Saison 29x eingeheizt", "zuletzt 13.11. 12:30") wiederholten
    nur die Kacheln oben. Jede Karte zeigt jetzt eine Aussage: grosse Zahl,
    kurze Beschriftung und - wo es etwas zu vergleichen gibt - eine kleine
    Grafik (Abbrand-Anteil, Puffer-Ladung vorher/nachher, Bedarfskurve).
    Diagnosewerte (Fehlstarts, Lernstunden) stehen als Fussnote auf der Karte,
    zu der sie gehoeren, statt als eigener Textbrocken.
    """

    CARD_H = 108
    ALERT_H = 38
    GAP = 8

    def __init__(self, master):
        super().__init__(master, bg=COLOR_ROOT, highlightthickness=0, height=self.CARD_H)
        self.facts: dict = {}
        self._drawn_sig = None
        self.bind("<Configure>", lambda _e: self._maybe_redraw())

    def set_facts(self, facts: dict) -> None:
        self.facts = facts or {}
        self._maybe_redraw()

    # --- Zeichnen ---------------------------------------------------------

    def _maybe_redraw(self) -> None:
        """Nur neu zeichnen, wenn sich sichtbar etwas geaendert hat.

        Gleiches Muster wie TankCanvas: ein Redraw erzeugt einige Dutzend
        Canvas-Objekte, und der Tab aktualisiert alle 15 s - meist umsonst.
        """
        try:
            sig = (self.winfo_width(), repr(self._cards()), repr(self.facts.get("alert")))
        except Exception:
            sig = None
        if sig is not None and sig == self._drawn_sig:
            return
        self._drawn_sig = sig
        self.redraw()

    def _round_rect(self, x, y, w, h, r=12, fill=COLOR_CARD, outline=COLOR_BORDER):
        self.create_rectangle(x + r, y, x + w - r, y + h, fill=fill, outline="")
        self.create_rectangle(x, y + r, x + w, y + h - r, fill=fill, outline="")
        for cx, cy, start in ((x, y, 90), (x + w - 2 * r, y, 0),
                              (x, y + h - 2 * r, 180), (x + w - 2 * r, y + h - 2 * r, 270)):
            self.create_arc(cx, cy, cx + 2 * r, cy + 2 * r, start=start, extent=90,
                            style="pieslice", fill=fill, outline="")
        self.create_line(x + r, y, x + w - r, y, fill=outline)
        self.create_line(x + r, y + h, x + w - r, y + h, fill=outline)
        self.create_line(x, y + r, x, y + h - r, fill=outline)
        self.create_line(x + w, y + r, x + w, y + h - r, fill=outline)
        for cx, cy, start in ((x, y, 90), (x + w - 2 * r, y, 0),
                              (x, y + h - 2 * r, 180), (x + w - 2 * r, y + h - 2 * r, 270)):
            self.create_arc(cx, cy, cx + 2 * r, cy + 2 * r, start=start, extent=90,
                            style="arc", outline=outline)

    def _bar(self, x, y, w, frac, color, h=9):
        """Waagrechter Balken 0..1 auf grauer Spur."""
        self.create_rectangle(x, y, x + w, y + h, fill=COLOR_BORDER, outline="")
        f = max(0.0, min(1.0, float(frac)))
        if f > 0:
            self.create_rectangle(x, y, x + w * f, y + h, fill=color, outline="")

    def _cards(self) -> list:
        """Reiner Renderer: die Karten baut WaermeTab._build_facts() zustandsabhaengig."""
        return self.facts.get("cards") or []

    def redraw(self) -> None:
        self.delete("all")
        W = max(10, self.winfo_width())
        cards = self._cards()
        alert = self.facts.get("alert")
        y = 0

        if alert:
            self._round_rect(0, 0, W - 1, self.ALERT_H, r=10, fill="#3a2016", outline=COLOR_DANGER)
            self.create_text(14, self.ALERT_H / 2, text="⚠", anchor="w", fill=COLOR_DANGER,
                             font=get_safe_font("Segoe UI Emoji", 16))
            self.create_text(40, self.ALERT_H / 2, text=alert[1], anchor="w", fill=COLOR_TEXT,
                             font=get_safe_font("Bahnschrift", 15, "bold"))
            self.create_text(W - 14, self.ALERT_H / 2, text=alert[0], anchor="e", fill=COLOR_SUBTEXT,
                             font=get_safe_font("Bahnschrift", 13))
            y = self.ALERT_H + self.GAP

        if not cards:
            self.create_text(14, y + 20, text="Auswertung läuft …", anchor="w", fill=COLOR_SUBTEXT,
                             font=get_safe_font("Bahnschrift", 14))
            self.configure(height=y + 44)
            return

        n = len(cards)
        cw = (W - (n - 1) * self.GAP) / n
        for i, c in enumerate(cards):
            self._card(i * (cw + self.GAP), y, cw - 1, self.CARD_H, c)
        self.configure(height=y + self.CARD_H)

    def _card(self, x, y, w, h, c) -> None:
        self._round_rect(x, y, w, h, fill=COLOR_CARD, outline=COLOR_BORDER)
        pad = 14
        self.create_oval(x + pad, y + 15, x + pad + 8, y + 23, fill=c["color"], outline="")
        cap_item = self.create_text(x + pad + 14, y + 19, text=c["cap"], anchor="w", fill=COLOR_SUBTEXT,
                                    font=get_safe_font("Bahnschrift", 12, "bold"))
        # Der grosse Wert ist hier nicht immer eine Zahl, sondern auch mal ein Satz
        # ("Heute Abend einheizen"). Groesse so weit reduzieren, bis er in die Karte
        # passt, statt ihn am Rand abzuschneiden.
        for size in (23, 20, 17, 15, 13):
            val_item = self.create_text(x + pad, y + 46, text=c["val"], anchor="w", fill=c["color"],
                                        font=get_safe_font("Bahnschrift", size, "bold"))
            try:
                if self.bbox(val_item)[2] <= x + w - pad or size == 13:
                    break
            except Exception:
                break
            self.delete(val_item)
        if c.get("sub"):
            self.create_text(x + pad, y + 68, text=c["sub"], anchor="w", fill=COLOR_TEXT,
                             font=get_safe_font("Bahnschrift", 13))

        gx, gw, gy = x + pad, w - 2 * pad, y + 84
        kind = c.get("kind")
        if kind == "bar":
            self._bar(gx, gy, gw, c["frac"], c["color"])
        elif kind == "range":
            # Ladung vorher -> nachher auf der 0..100-%-Spur
            self.create_rectangle(gx, gy, gx + gw, gy + 9, fill=COLOR_BORDER, outline="")
            a, b = max(0.0, min(1.0, c["a"])), max(0.0, min(1.0, c["b"]))
            self.create_rectangle(gx, gy, gx + gw * a, gy + 9, fill=COLOR_SUBTEXT, outline="")
            self.create_rectangle(gx + gw * a, gy, gx + gw * b, gy + 9, fill=c["color"], outline="")
        elif kind == "curve":
            self._curve(gx, gy - 7, gw, 16, c)
        elif kind == "ramp":
            # Keil statt Linie: der Verlust waechst mit der Speichertemperatur,
            # als Flaeche ist das auf einen Blick erkennbar.
            self.create_polygon(gx, gy + 9, gx + gw, gy - 5, gx + gw, gy + 9,
                                fill=c["color"], outline="")

        if c.get("note"):
            # Fussnote rechts oben - im Hochformat sind die Karten schmal genug,
            # dass sie in die Ueberschrift laufen wuerde. Erst zeichnen, dann
            # anhand der echten Textbreite wieder verwerfen, wenn es kollidiert.
            note_item = self.create_text(x + w - pad, y + 19, text=c["note"], anchor="e",
                                         fill=COLOR_SUBTEXT, font=get_safe_font("Bahnschrift", 11))
            try:
                if self.bbox(note_item)[0] < self.bbox(cap_item)[2] + 12:
                    self.delete(note_item)
            except Exception:
                pass

    def _curve(self, x, y, w, h, c) -> None:
        """Bedarfskurve kW ueber Aussentemperatur, warm links -> kalt rechts."""
        pts = c.get("pts") or []
        if len(pts) < 2:
            return
        kws = [p[1] for p in pts]
        lo, hi = min(kws), max(kws)
        span = max(0.1, hi - lo)
        t0, t1 = pts[0][0], pts[-1][0]
        tspan = (t1 - t0) or 1.0
        xy = []
        for t, kw in pts:
            xy += [x + w * (t - t0) / tspan, y + h - h * (kw - lo) / span]
        self.create_line(*xy, fill=c["color"], width=2, smooth=True)
        od = c.get("outdoor")
        if od is not None and min(t0, t1) <= od <= max(t0, t1):
            px = x + w * (od - t0) / tspan
            kw = min(pts, key=lambda p: abs(p[0] - od))[1]
            py = y + h - h * (kw - lo) / span
            self.create_oval(px - 4, py - 4, px + 4, py + 4, fill="#ffffff", outline=c["color"], width=2)


class WaermeTab(UiQueuePumpMixin):
    def __init__(self, root: tk.Tk, notebook=None, datastore=None, tab_frame=None):
        self.root = root
        self.alive = True
        self.datastore = datastore or get_shared_datastore()
        self.cfg = hs.load_storage_config()
        self._mode = "Heute"
        self._portrait = False
        self._live_job = None
        self._chart_job = None
        self._update_token = 0
        # Zustand fuer die Kartenleiste (wird im Worker gefuellt)
        self._deutung = None            # bmk_interpret.Deutung der letzten Messung
        self._burn_min = None           # Minuten Teil-/Volllast am Stueck
        self._last_fire = None          # letztes abgeschlossenes Feuer
        self._typ_burn_cache = None     # Median-Brennzeit, siehe _typical_burn_min()
        self._typ_burn_at = 0.0
        self._last_result = None
        self._init_ui_queue()
        self._start_ui_pump()

        if tab_frame is None:
            tab_frame = tk.Frame(root, bg=COLOR_ROOT)
            if notebook is not None:
                notebook.add(tab_frame, text="Wärme")
        self._shell = TabShell(tab_frame, "Wärme", "Pufferspeicher, Solarthermie und Holz")
        self._shell.pack(fill=tk.BOTH, expand=True)
        body = self._shell.body
        self.body = body
        body.grid_rowconfigure(0, weight=0)
        body.grid_rowconfigure(1, weight=0)
        body.grid_rowconfigure(2, weight=1)
        body.grid_rowconfigure(3, weight=0)
        body.grid_columnconfigure(0, weight=1)

        # --- Kacheln
        self.metrics = tk.Frame(body, bg=COLOR_ROOT)
        self.metrics.grid(row=0, column=0, sticky="ew", padx=PADDING_SECTION, pady=(PADDING_SECTION, 8))
        specs = [
            ("nutzbar", "Puffer nutzbar", COLOR_PRIMARY),
            ("solar", "Solar heute", COLOR_SOLAR),
            ("holz", "Holz heute", COLOR_WOOD),
            ("zuletzt", "Eingeheizt", COLOR_TEXT),
            ("rm", "Holz Saison", COLOR_WOOD),
            ("anteil", "Solaranteil", COLOR_SOLAR),
        ]
        self._tile_order = [k for k, _, _ in specs]
        self.tiles = {k: MetricTile(self.metrics, cap, value_color=col) for k, cap, col in specs}

        # --- Einheiz-Empfehlung
        self.banner = ctk.CTkFrame(body, fg_color=COLOR_CARD, corner_radius=12, border_width=2,
                                   border_color=COLOR_BORDER)
        self.banner.grid(row=1, column=0, sticky="ew", padx=PADDING_SECTION + 3, pady=(0, 8))
        self.banner.grid_columnconfigure(1, weight=1)
        self.banner_icon = ctk.CTkLabel(self.banner, text="⏳", width=44,
                                        font=get_safe_font("Segoe UI Emoji", 26))
        self.banner_icon.grid(row=0, column=0, rowspan=3, padx=(12, 6), pady=4)
        self.banner_title = ctk.CTkLabel(self.banner, text="Empfehlung wird berechnet …", anchor="w",
                                         text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 19, "bold"))
        self.banner_title.grid(row=0, column=1, sticky="w", pady=(4, 0))
        self.banner_detail = ctk.CTkLabel(self.banner, text="", anchor="w", text_color=COLOR_SUBTEXT,
                                          font=get_safe_font("Bahnschrift", 15), height=24)
        self.banner_detail.grid(row=1, column=1, sticky="w", pady=(0, 0))
        self.banner_outlook = ctk.CTkLabel(self.banner, text="", anchor="w", text_color=COLOR_SUBTEXT,
                                           font=get_safe_font("Bahnschrift", 15), height=24)
        self.banner_outlook.grid(row=2, column=1, sticky="w", pady=(0, 4))
        # Lange Detailzeile im Hochformat umbrechen statt abschneiden
        def _wrap(e):
            for lbl in (self.banner_detail, self.banner_outlook):
                lbl.configure(wraplength=max(200, e.width - 90), justify="left")
        self.banner.bind("<Configure>", _wrap)

        # --- Inhalt: Tank links, Chart rechts
        self.content = tk.Frame(body, bg=COLOR_ROOT)
        self.content.grid(row=2, column=0, sticky="nsew", padx=PADDING_SECTION)
        self.tank_card = tk.Frame(self.content, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.tank = TankCanvas(self.tank_card)
        self.tank.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)

        self.chart_card = tk.Frame(self.content, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        self.chart_card.grid_rowconfigure(1, weight=1)
        self.chart_card.grid_columnconfigure(0, weight=1)
        bar = tk.Frame(self.chart_card, bg=COLOR_CARD)
        bar.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 0))
        self.chart_title = tk.Label(bar, text="", bg=COLOR_CARD, fg=COLOR_TEXT,
                                    font=get_safe_font("Bahnschrift", 16, "bold"))
        self.chart_title.pack(side=tk.LEFT)
        self._mode_buttons = {}
        for mode in ("Woche", "Saison", "Heute"):
            btn = ctk.CTkButton(bar, text=mode, width=76, height=BUTTON_HEIGHT_SECONDARY - 8, corner_radius=14,
                                font=get_safe_font("Bahnschrift", FONT_SIZE_BODY, "bold"),
                                command=lambda m=mode: self._select_mode(m))
            btn.pack(side=tk.RIGHT, padx=3)
            self._mode_buttons[mode] = btn
        self._update_mode_buttons()

        self.chart_frame = tk.Frame(self.chart_card, bg=COLOR_CARD)
        self.chart_frame.grid(row=1, column=0, sticky="nsew", padx=6, pady=6)
        self.chart_frame.grid_propagate(False)
        self.chart_frame.pack_propagate(False)
        self.chart = _WaermeChart(self.chart_frame)

        self.facts = _FactsStrip(body)
        self.facts.grid(row=3, column=0, sticky="ew", padx=PADDING_SECTION, pady=(6, 8))

        self.set_portrait_layout(False)
        self.root.after(300, self._refresh_live)
        self.root.after(600, self._refresh_chart)
        self.root.after(1500, self.chart.refresh_size)

    # --- Layout -------------------------------------------------------------

    def set_portrait_layout(self, portrait: bool) -> None:
        self._portrait = bool(portrait)
        try:
            self._shell.set_portrait_layout(portrait)
        except Exception:
            pass
        cols = 3 if portrait else 6
        for col in range(6):
            self.metrics.grid_columnconfigure(col, weight=1 if col < cols else 0, uniform="t" if col < cols else "")
        for idx, key in enumerate(self._tile_order):
            self.tiles[key].grid(row=idx // cols, column=idx % cols, sticky="nsew", padx=3, pady=3)

        self.tank_card.grid_forget()
        self.chart_card.grid_forget()
        for i in range(2):
            self.content.grid_rowconfigure(i, weight=0, minsize=0)
            self.content.grid_columnconfigure(i, weight=0, minsize=0, uniform="")
        if portrait:
            self.content.grid_columnconfigure(0, weight=1)
            self.content.grid_rowconfigure(0, weight=2, minsize=240, uniform="r")
            self.content.grid_rowconfigure(1, weight=3, uniform="r")
            self.tank_card.grid(row=0, column=0, sticky="nsew", pady=(0, 8))
            self.chart_card.grid(row=1, column=0, sticky="nsew")
        else:
            self.content.grid_rowconfigure(0, weight=1)
            self.content.grid_columnconfigure(0, weight=2, uniform="c")
            self.content.grid_columnconfigure(1, weight=5, uniform="c")
            self.tank_card.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
            self.chart_card.grid(row=0, column=1, sticky="nsew")
        self.root.after(80, self.chart.refresh_size)
        self.root.after(400, self.chart.refresh_size)

    def _select_mode(self, mode: str) -> None:
        self._mode = mode
        self._update_mode_buttons()
        if self._last_result:
            self._render_chart(*self._last_result)

    def _update_mode_buttons(self) -> None:
        for mode, btn in self._mode_buttons.items():
            if mode == self._mode:
                btn.configure(fg_color=COLOR_PRIMARY, text_color="#ffffff", hover_color=COLOR_PRIMARY)
            else:
                btn.configure(fg_color=COLOR_BORDER, text_color=COLOR_TEXT, hover_color=COLOR_PRIMARY)

    # --- Live-Werte ---------------------------------------------------------

    def _refresh_live(self) -> None:
        if not self.alive:
            return
        try:
            rec = self.datastore.get_last_heating_record() if self.datastore else None
        except Exception:
            rec = None
        rec = rec or {}

        def val(key):
            return hs._heat_val(rec.get(key))

        top, mid, bot = val(BUF_TOP_C), val(BUF_MID_C), val(BUF_BOTTOM_C)
        self.tank.set_state(cfg=self.cfg, top=top, mid=mid, bot=bot, warm=val(BMK_WARMWASSER_C),
                            kessel=val(BMK_KESSEL_C))
        usable = hs.tanks_usable_kwh(top, mid, bot, self.cfg)   # Summe beider Puffer
        self.tiles["nutzbar"].set_value(f"{usable:.0f} kWh" if usable is not None else "--")
        self._live_job = self.root.after(LIVE_REFRESH_MS, self._refresh_live)

    # --- Auswertung (Worker-Thread) -----------------------------------------

    def _refresh_chart(self) -> None:
        if not self.alive:
            return
        self._update_token += 1
        token = self._update_token

        def worker() -> None:
            try:
                today = date.today()
                timeline, today_stats = hs.day_timeline(self.datastore, today, self.cfg)
                season = hs.season_stats(self.datastore, self.cfg, today=today)
            except Exception:
                logger.exception("[WAERME] Auswertung fehlgeschlagen")
                timeline, today_stats, season = [], hs.DayStats(date.today()), hs.HeatingStats()
            try:
                try:
                    fc = pv_forecast.get_forecast(self.datastore)
                except Exception:
                    fc = None
                rec = hf.recommend(self.datastore, self.cfg, season=season, pv_forecast_utc=fc)
            except Exception:
                logger.exception("[WAERME] Empfehlung fehlgeschlagen")
                rec = hf.Recommendation("unknown", "Keine Empfehlung möglich")
            heat_alert = None
            try:
                from core import alerts
                heat_alert = next(((ts, title) for ts, kind, title, _m in alerts.recent_alerts(24)
                                   if kind in ("waerme", "solar")), None)
            except Exception:
                heat_alert = None
            self._heat_alert = heat_alert
            # Feuer-Bilanz aus dem dauerhaft gespeicherten BMK-Verlauf (core/bmk_interpret)
            try:
                fires = bmk_interpret.recent_fires(self.datastore, days=14, cfg=self.cfg)
                # Das Fire-Objekt selbst behalten (statt nur der fertigen Textzeile):
                # die Karten unter dem Chart brauchen die Einzelwerte.
                real = [x for x in fires if x.kind == "feuer"]
                self._last_fire = real[-1] if real else None
            except Exception:
                logger.exception("[WAERME] Feuer-Bilanz fehlgeschlagen")
                self._last_fire = None
            self._typ_burn_min = self._typical_burn_min()
            # Live-Deutung (Nachlegen lohnt? Platz im Puffer?) aus dem gespeicherten
            # BMK-Verlauf. Bewusst ueber den Store statt ueber bmk_interpret.live():
            # der gemeinsame Interpreter gehoert dem Collector-Thread und waere leer,
            # wenn der Collector in diesem Prozess gar nicht laeuft.
            self._deutung, self._burn_min = None, None
            try:
                rows = self.datastore.get_bmk_status(hours=LIVE_BMK_HOURS) or []
                readings = [bmk_interpret.reading_from_row(r["ts"], r) for r in rows if r.get("ts")]
                if readings and (datetime.now() - readings[-1].ts) <= timedelta(minutes=LIVE_BMK_MAX_AGE_MIN):
                    li = bmk_interpret.LiveInterpreter(self.cfg, keep_min=int(LIVE_BMK_HOURS * 60))
                    li.preload(readings[:-1])
                    self._deutung = li.interpret(readings[-1])
                    self._burn_min = li.burn_minutes(readings[-1])
            except Exception:
                logger.exception("[WAERME] Live-Deutung fehlgeschlagen")

            def apply() -> None:
                if not self.alive or token != self._update_token:
                    return
                self._last_result = (timeline, today_stats, season, rec)
                from core.perf_monitor import timed
                with timed("waerme.anzeigen", min_ms=100):
                    self._apply_result(timeline, today_stats, season, rec)

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()
        self._chart_job = self.root.after(CHART_REFRESH_MS, self._refresh_chart)

    _LEVEL_STYLE = {
        "ok": ("✅", COLOR_SUCCESS),
        "soon": ("🕒", COLOR_SOLAR),
        "today": ("🔥", COLOR_WOOD),
        "now": ("🔥", "#ff3b3b"),
        "burning": ("🔥", COLOR_SOLAR),
        "unknown": ("⏳", COLOR_SUBTEXT),
    }

    def _show_recommendation(self, rec) -> None:
        icon, color = self._LEVEL_STYLE.get(rec.level, ("⏳", COLOR_SUBTEXT))
        self.banner.configure(border_color=color)
        self.banner_icon.configure(text=icon)
        self.banner_title.configure(text=rec.title, text_color=color if rec.level != "unknown" else COLOR_TEXT)
        detail = rec.detail
        if rec.rate_kw is not None and rec.level not in ("burning",):
            detail += f" · Verbrauch ≈ {rec.rate_kw:.1f} kW"
            if rec.outdoor_now is not None:
                detail += f" bei {rec.outdoor_now:.0f} °C"
        self.banner_detail.configure(text=detail)
        ol = rec.outlook
        if ol is not None:
            text = f"Nächste 7 Tage: ≈ {ol.demand_kwh:.0f} kWh Wärmebedarf"
            if ol.mean_temp is not None:
                text += f" (Ø {ol.mean_temp:.0f} °C)"
            if ol.firings is not None:
                text += " → Puffer reicht" if ol.firings == 0 else f" → ca. {ol.firings}× einheizen"
            self.banner_outlook.configure(text=text)
        else:
            self.banner_outlook.configure(text="Wochenausblick: Verbrauchsmodell lernt noch")

    def _apply_result(self, timeline, today_stats, season, rec=None) -> None:
        if rec is not None:
            self._show_recommendation(rec)
        self.tiles["solar"].set_value(f"{today_stats.solar_kwh:.0f} kWh")
        self.tiles["holz"].set_value(f"{today_stats.wood_kwh:.0f} kWh")
        last = season.events[-1] if season.events else None
        if last is None:
            self.tiles["zuletzt"].set_value("--", animate=False)
        else:
            d = last.start.date()
            if d == date.today():
                txt = f"heute {last.start:%H:%M}"
            elif d == date.today() - timedelta(days=1):
                txt = f"gestern {last.start:%H:%M}"
            else:
                txt = f"{last.start:%d.%m. %H:%M}"
            self.tiles["zuletzt"].set_value(txt, animate=False)
        self.tiles["rm"].set_value(f"{hs.wood_rm(season.wood_kwh, self.cfg):.1f} rm")
        share = season.solar_share_pct
        self.tiles["anteil"].set_value(f"{share:.0f}%" if share is not None else "--%")

        recent = [p for p in timeline if p.ts >= datetime.now() - timedelta(minutes=45)]
        self.tank.set_state(solar_active=any(p.source == "solar" for p in recent))

        self.facts.set_facts(self._build_facts(rec))
        self._render_chart(timeline, today_stats, season, rec)

    # Farbe der Nachlege-Karte nach dem Ton aus bmk_interpret.deuten()
    _TONE_COLOR = {"ok": COLOR_SUCCESS, "info": COLOR_PRIMARY, "warn": COLOR_WARNING,
                   "hot": COLOR_WOOD, "aus": COLOR_SUBTEXT}

    def _build_facts(self, rec=None) -> dict:
        """Die drei Karten unter dem Chart - je nach Kesselzustand mit anderem Inhalt.

        Kern ist `core.bmk_interpret.deuten()`: dort wird schon entschieden, ob
        Nachlegen lohnt, nicht noetig ist (Puffer voll) oder die Tuer zubleiben
        muss. Das lief bisher nur im Collector fuer die Push-Meldungen und war
        auf dem Dashboard nirgends zu sehen.

        Brennt es     -> Nachlegen (Tuer zu) · Fuellung abgebrannt · Platz im Puffer
        Glut          -> Nachlegen (lohnt?)  · abgebrannt seit     · Platz im Puffer
        Kessel aus    -> Einheizen (Prognose)· letztes Feuer       · Platz im Puffer
        """
        facts: dict = {}

        alert = getattr(self, "_heat_alert", None)
        if alert:
            facts["alert"] = (f"{datetime.fromtimestamp(alert[0]):%H:%M}", alert[1])

        d = getattr(self, "_deutung", None)
        burn_min = getattr(self, "_burn_min", None)
        typ_min = getattr(self, "_typ_burn_min", None)
        fire = getattr(self, "_last_fire", None)
        cards = []
        phase = getattr(d, "phase", None)
        burning = phase in ("anheizen", "teillast", "volllast", "nachlegen")

        # --- 1. Was ist zu tun? ---------------------------------------------
        if d is not None and phase != "aus":
            cards.append({"cap": "NACHLEGEN", "val": self._advice_value(d),
                          "sub": d.headline, "color": self._TONE_COLOR.get(d.tone, COLOR_TEXT),
                          "kind": None, "note": ""})
        elif rec is not None and rec.level != "unknown":
            # Kessel aus: die Einheiz-Empfehlung aus core/heating_forecast
            cards.append({"cap": "EINHEIZEN", "val": rec.title, "sub": rec.detail or "",
                          "color": self._LEVEL_STYLE.get(rec.level, ("", COLOR_TEXT))[1],
                          "kind": None, "note": ""})

        # --- 2. Wie weit ist die Fuellung? ----------------------------------
        if burning and burn_min is not None and typ_min:
            early, med, late = typ_min
            frac = max(0.0, min(1.0, burn_min / med))
            now = datetime.now()
            rest = max(0.0, med - burn_min)
            # Korridor aus dem 25-/75-%-Quantil: der Median allein waere zu genau
            # dargestellt (siehe bmk_interpret.typical_load_burn).
            lo = now + timedelta(minutes=max(0.0, early - burn_min))
            hi = now + timedelta(minutes=max(0.0, late - burn_min))
            cards.append({"cap": "FÜLLUNG ABGEBRANNT", "color": COLOR_WOOD, "kind": "bar", "frac": frac,
                          "val": ("gleich" if rest < 10 else f"ca. {now + timedelta(minutes=rest):%H:%M}"),
                          "sub": f"meist {lo:%H:%M}–{hi:%H:%M}  ·  brennt seit {self._fmt_min(burn_min)}",
                          "note": f"{frac * 100:.0f} % von {self._fmt_min(med)}"})
        elif burning:
            cards.append({"cap": "FÜLLUNG", "val": "brennt", "color": COLOR_WOOD, "kind": None,
                          "sub": (f"seit {self._fmt_min(burn_min)}" if burn_min else "Brennzeit wird gemessen"),
                          "note": ""})
        elif phase == "gluterhalt" and fire is not None:
            cards.append({"cap": "FÜLLUNG ABGEBRANNT", "val": f"{fire.end:%H:%M}", "color": COLOR_SUBTEXT,
                          "sub": f"{self._fmt_min(fire.brenn_min)} Teil-/Volllast", "kind": None, "note": ""})
        elif fire is not None and fire.dauer_min:
            h, m = divmod(int(round(fire.dauer_min)), 60)
            bits = [f"{fire.kwh:.0f} kWh"] if fire.kwh else []
            if fire.kessel_peak is not None:
                bits.append(f"max {fire.kessel_peak:.0f}°")
            cards.append({"cap": "LETZTES FEUER", "val": f"{fire.start:%d.%m. %H:%M}", "color": COLOR_WOOD,
                          "sub": f"{h} h {m:02d} min  ·  " + "  ·  ".join(bits) if bits else f"{h} h {m:02d} min",
                          "kind": "bar", "frac": fire.brenn_min / fire.dauer_min,
                          "note": f"{fire.brenn_min / fire.dauer_min * 100:.0f} % Teil-/Volllast"})

        # --- 3. Passt ueberhaupt noch etwas rein? ---------------------------
        if d is not None and d.fuellungen is not None:
            lad = d.ladung_pct
            sub = f"{d.platz_kwh:.0f} kWh frei" if d.platz_kwh is not None else ""
            if lad is not None:
                sub += f"  ·  Puffer {lad:.0f} %" if sub else f"Puffer {lad:.0f} %"
            cards.append({"cap": "PLATZ IM PUFFER", "val": f"≈ {d.fuellungen:.1f} Füllungen".replace(".", ","),
                          "color": COLOR_SUCCESS if d.fuellungen >= 0.5 else COLOR_SUBTEXT,
                          "kind": "bar", "frac": (lad or 0.0) / 100.0, "sub": sub,
                          "note": "Puffer voll" if d.fuellungen < 0.2 else ""})

        facts["cards"] = cards
        return facts

    def _typical_burn_min(self):
        """Median-Brennzeit EINER Ofenladung; hoechstens stuendlich neu berechnet.

        Bewusst nicht Fire.brenn_min: ein `Fire` ist eine ganze Brennsitzung und
        umfasst oft mehrere Fuellungen (09.11.2026: 988 min mit sechs
        Tueroeffnungen), sein Median lag bei 439 min. Fuer "wann ist die Fuellung
        abgebrannt" zaehlt der Abschnitt zwischen zwei NACHLEGEN-Bloecken -
        Median 84 min (Diagnose 13.11.). Siehe bmk_interpret.load_burn_minutes().

        Das Fenster ist weit, damit ueber den Sommer genug Ladungen zusammenkommen;
        das sind viele Minutenzeilen und zu teuer fuer jeden Chart-Refresh, darum
        der Cache.
        """
        import time as _time
        now = _time.time()
        if self._typ_burn_at and now - self._typ_burn_at < TYP_BURN_CACHE_S:
            return self._typ_burn_cache
        self._typ_burn_at = now
        try:
            rows = self.datastore.get_bmk_status(days=FIRE_HISTORY_DAYS) or []
            self._typ_burn_cache = bmk_interpret.typical_load_burn(rows)
        except Exception:
            logger.exception("[WAERME] typische Brennzeit fehlgeschlagen")
            self._typ_burn_cache = None
        return self._typ_burn_cache

    @staticmethod
    def _fmt_min(m) -> str:
        if m is None:
            return "–"
        h, mm = divmod(int(round(m)), 60)
        return f"{h} h {mm:02d} min" if h else f"{mm} min"

    @staticmethod
    def _advice_value(d) -> str:
        """Kurzform der Empfehlung aus bmk_interpret.deuten() fuer die grosse Zahl."""
        if d.empfehlung == "nachlegen":
            return "jetzt sinnvoll"
        if d.empfehlung == "nicht_nachlegen":
            return "nicht nötig"
        if d.empfehlung == "nicht_oeffnen":
            return "Tür zu lassen"
        return {"gluterhalt": "wenn Wärme gebraucht wird"}.get(d.phase, "–")

    def _render_chart(self, timeline, today_stats, season, rec=None) -> None:
        if self._mode == "Heute":
            self.chart_title.config(text="Speicher heute")
            self.chart.render_today(timeline, today_stats, projection=(rec.projection if rec else None))
        elif self._mode == "Woche":
            self.chart_title.config(text="Wärmebedarf nächste 7 Tage")
            self.chart.render_week(rec.outlook if rec else None, rec.model if rec else None)
        else:
            self.chart_title.config(text=f"Saison seit {hs.season_start():%d.%m.%Y}")
            self.chart.render_season(season, self.cfg)

    def stop(self) -> None:
        self.alive = False
        for job in (self._live_job, self._chart_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass


class _WaermeChart(MatplotlibCanvasResizeMixin):
    def __init__(self, parent):
        self.fig = Figure(figsize=(7, 4), dpi=100)
        self.fig.patch.set_facecolor(COLOR_CARD)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasTkAgg(self.fig, master=parent)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.configure(bg=COLOR_CARD, highlightthickness=0)
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)
        self._last_synced_wh = (0, 0)
        self.canvas_widget.bind("<Configure>", lambda e: self._on_resize(e.width, e.height))
        self.canvas_widget.bind("<Map>", lambda _e: self.refresh_size())

    def _on_resize(self, w, h) -> None:
        if self._sync_size(w, h):
            self._layout()
            self.canvas.draw_idle()

    def refresh_size(self) -> None:
        try:
            self._on_resize(int(self.canvas_widget.winfo_width()), int(self.canvas_widget.winfo_height()))
        except Exception:
            pass

    def _layout(self) -> None:
        w = int(self.canvas_widget.winfo_width() or 0)
        compact = w < 560
        extra = getattr(self, "_bottom_extra", 0.0)      # zweizeilige Achsenbeschriftung (Wochenansicht)
        self.fig.subplots_adjust(left=0.15 if compact else 0.11, right=0.97, top=0.88,
                                 bottom=(0.17 if compact else 0.15) + extra)

    def _reset(self):
        self._bottom_extra = 0.0
        self.fig.clear()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_facecolor(COLOR_CARD)
        apply_chart_style(self.ax, grid_axis="y")
        self.ax.set_facecolor(COLOR_CARD)
        self.refresh_size()
        self._layout()

    def _no_data(self, text="Keine Daten"):
        self.ax.text(0.5, 0.5, text, ha="center", va="center", transform=self.ax.transAxes,
                     color=COLOR_SUBTEXT, fontsize=14)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.canvas.draw_idle()

    def _legend(self, handles):
        leg = self.ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0, 1.13), ncol=len(handles),
                             frameon=False, fontsize=12, handlelength=1.4, columnspacing=1.2)
        for t in leg.get_texts():
            t.set_color(COLOR_SUBTEXT)

    def render_today(self, timeline, day_stats, projection=None) -> None:
        self._reset()
        pts = [p for p in timeline if p.q_kwh is not None]
        if len(pts) < 2:
            self._no_data("Noch keine Heizungsdaten heute")
            return
        xs = [p.ts for p in pts]
        ys = np.array([p.q_kwh for p in pts], dtype=float)
        # Datenluecken > 75 min nicht verbinden
        for i in range(1, len(xs)):
            if (xs[i] - xs[i - 1]) > timedelta(minutes=75):
                ys[i - 1] = np.nan
        src = [p.source for p in pts]
        self.ax.fill_between(xs, 0, ys, color="#3b4a63", alpha=0.35, linewidth=0)
        wood = np.array([s == "wood" for s in src])
        solar = np.array([s == "solar" for s in src])
        # Markierung bis zum Vorpunkt ausdehnen, damit auch einzelne Intervalle sichtbar sind
        wood = wood | np.roll(wood, -1)
        solar = solar | np.roll(solar, -1)
        self.ax.fill_between(xs, 0, ys, where=wood, color=COLOR_WOOD, alpha=0.55, linewidth=0, step=None)
        self.ax.fill_between(xs, 0, ys, where=solar, color=COLOR_SOLAR, alpha=0.6, linewidth=0)
        (h_line,) = self.ax.plot(xs, ys, color="#d7dde8", linewidth=1.8, label="Speicherinhalt")
        from matplotlib.patches import Patch
        handles = [h_line,
                   Patch(color=COLOR_SOLAR, alpha=0.6, label=f"Solar +{day_stats.solar_kwh:.0f} kWh"),
                   Patch(color=COLOR_WOOD, alpha=0.55, label=f"Holz +{day_stats.wood_kwh:.0f} kWh")]
        start = datetime.combine(xs[0].date(), datetime.min.time())
        end = start + timedelta(days=1)
        proj = [(t, e) for t, e in (projection or []) if t <= end]
        if len(proj) >= 2:
            (h_proj,) = self.ax.plot([t for t, _ in proj], [e for _, e in proj], color="#d7dde8",
                                     linewidth=1.5, linestyle=(0, (4, 3)), alpha=0.8, label="Prognose")
            handles.insert(1, h_proj)
            ys_max = max(float(np.nanmax(ys)), max(e for _, e in proj))
        else:
            ys_max = float(np.nanmax(ys))
        self.ax.set_xlim(start, end)
        now = datetime.now()
        if start <= now <= start + timedelta(days=1):
            self.ax.axvline(now, color=COLOR_PRIMARY, alpha=0.5, linewidth=1.2, linestyle="--")
        self.ax.set_ylim(bottom=0, top=max(10.0, ys_max * 1.15))
        self.ax.xaxis.set_major_locator(mdates.HourLocator(byhour=range(0, 24, 3)))
        self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        self.ax.set_ylabel("kWh", fontsize=12, color=COLOR_SUBTEXT, rotation=0, labelpad=12, va="center")
        self._legend(handles)
        self.canvas.draw_idle()

    def render_week(self, outlook, model) -> None:
        """Wärmebedarf je Tag als Balken; Außentemperatur steht unter dem Wochentag.

        Eine Achse (kWh) statt zweier - die Temperatur ist die Ursache, der Bedarf
        das Ergebnis, beides direkt am Tag ablesbar.
        """
        self._reset()
        if outlook is None or not outlook.days:
            self._no_data("Verbrauchsmodell lernt noch –\nnach einigen Tagen mit Daten verfügbar")
            return
        rows = outlook.days[:7]
        self._bottom_extra = 0.08
        self._layout()
        xs = np.arange(len(rows))
        kwh = np.array([v for _, v, _ in rows], dtype=float)
        # erster Tag ist angebrochen ("Rest heute") -> heller, damit er nicht wie ein ganzer Tag wirkt
        colors = [COLOR_WOOD] * len(rows)
        alphas = [0.45] + [0.9] * (len(rows) - 1)
        for x, v, c, a in zip(xs, kwh, colors, alphas):
            self.ax.bar(x, v, width=0.62, color=c, alpha=a, linewidth=0)
            self.ax.annotate(f"{v:.0f}", (x, v), xytext=(0, 4), textcoords="offset points", ha="center",
                             va="bottom", fontsize=12, color=COLOR_TEXT)
        wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        labels = []
        for i, (d, _, t) in enumerate(rows):
            name = "Rest heute" if i == 0 else wd[d.weekday()]
            temp = f"{t:.0f} °C".replace("-", "−") if t is not None else "–"
            labels.append(f"{name}\n{temp}")
        self.ax.set_xticks(xs)
        self.ax.set_xticklabels(labels, fontsize=12)
        self.ax.set_xlim(-0.6, len(rows) - 0.4)
        self.ax.set_ylim(0, max(10.0, float(kwh.max()) * 1.2))
        self.ax.set_ylabel("kWh", fontsize=12, color=COLOR_SUBTEXT, rotation=0, labelpad=12, va="center")
        self.ax.grid(axis="x", visible=False)
        self.canvas.draw_idle()

    def render_season(self, season, cfg) -> None:
        self._reset()
        days = season.days
        if not days or (season.wood_kwh + season.solar_kwh) < 0.5:
            self._no_data("Noch keine Daten in dieser Saison")
            return
        xs = [datetime.combine(d.day, datetime.min.time()) + timedelta(hours=12) for d in days]
        solar_cum = np.cumsum([d.solar_kwh for d in days])
        wood_cum = np.cumsum([d.wood_kwh for d in days])
        self.ax.fill_between(xs, 0, solar_cum, color=COLOR_SOLAR, alpha=0.75, linewidth=0)
        self.ax.fill_between(xs, solar_cum, solar_cum + wood_cum, color=COLOR_WOOD, alpha=0.7, linewidth=0)
        self.ax.plot(xs, solar_cum + wood_cum, color="#f3c1b2", linewidth=1.2)
        # Einheizvorgaenge als Striche am unteren Rand
        top = float(solar_cum[-1] + wood_cum[-1]) or 1.0
        for ev in season.events:
            self.ax.plot([ev.start, ev.start], [0, top * 0.03], color="#ffffff", alpha=0.6, linewidth=1.0)
        # Summen rechts am Ende beschriften
        self.ax.annotate(f"{wood_cum[-1]:.0f} kWh\n≈ {hs.wood_rm(wood_cum[-1], cfg):.1f} rm",
                         xy=(xs[-1], solar_cum[-1] + wood_cum[-1] / 2), xytext=(-6, 0), textcoords="offset points",
                         ha="right", va="center", fontsize=11, color="#ffffff")
        if solar_cum[-1] > top * 0.06:
            self.ax.annotate(f"{solar_cum[-1]:.0f} kWh", xy=(xs[-1], solar_cum[-1] / 2), xytext=(-6, 0),
                             textcoords="offset points", ha="right", va="center", fontsize=11, color="#2a1d05")
        from matplotlib.patches import Patch
        share = season.solar_share_pct
        handles = [Patch(color=COLOR_WOOD, alpha=0.7, label="Holz"),
                   Patch(color=COLOR_SOLAR, alpha=0.75, label=f"Solar ({share:.0f} %)" if share is not None else "Solar")]
        self.ax.set_xlim(xs[0] - timedelta(hours=12), xs[-1] + timedelta(hours=12))
        self.ax.set_ylim(0, top * 1.12)
        locator = mdates.MonthLocator()
        self.ax.xaxis.set_major_locator(locator if len(days) > 45 else mdates.AutoDateLocator(maxticks=8))
        if len(days) > 45:
            from matplotlib.ticker import FuncFormatter
            months = ["Jän", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
            self.ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: months[mdates.num2date(v).month - 1]))
        else:
            self.ax.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m."))
        self.ax.set_ylabel("kWh", fontsize=12, color=COLOR_SUBTEXT, rotation=0, labelpad=12, va="center")
        self._legend(handles)
        self.canvas.draw_idle()
