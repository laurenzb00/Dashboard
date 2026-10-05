"""Raumtemperatur-Tab (Tado): alle Raeume als Thermostat-Ringe.

Siehe TadoTab-Docstring fuer Datenquellen und das Tado-API-Tageslimit.
Datenmodell/Parser: core/climate.py.
"""
from __future__ import annotations

import importlib
import logging
import os
import socket
import threading
import time
import tkinter as tk
import webbrowser
from datetime import date, datetime, time as dtime, timedelta, timezone
from tkinter import ttk
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import customtkinter as ctk

from core import climate as C
from core.climate_history import ClimateHistory, points_from_ha_history, points_from_tado_day_report
from core.homeassistant import HomeAssistantClient, load_homeassistant_config
from ui.components.tab_shell import TabShell
from ui.components.temp_chart import TempChart
from ui.components.thermostat_ring import ThermostatRing
from ui.components.ui_dispatch import UiQueuePumpMixin
from ui.styles import (
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_DANGER,
    COLOR_PRIMARY,
    COLOR_ROOT,
    COLOR_SUBTEXT,
    COLOR_SUCCESS,
    COLOR_TEXT,
    COLOR_WARNING,
    emoji,
    get_safe_font,
)

TADO_ENABLED = os.getenv("TADO_ENABLE", "").strip().lower() in {"1", "true", "yes", "on"}
if not TADO_ENABLED:
    TADO_ENABLED = bool(os.getenv("TADO_USER") or os.getenv("TADO_PASS"))

# --- Robust: python-tado-Import mit Fallback ---
Tado = None
_TADO_IMPL = None  # "python_tado" | "pytado" | None
try:
    _python_tado_mod = importlib.import_module("python_tado")
    Tado = getattr(_python_tado_mod, "Tado")
    _TADO_IMPL = "python_tado"
except ImportError as exc:
    if TADO_ENABLED:
        logging.warning("[TADO] python-tado Import fehlgeschlagen: %s", exc)
    else:
        logging.info("[TADO] python-tado nicht installiert (TADO deaktiviert)")

if Tado is None:
    # Aktuelles `python-tado` (0.19.x) installiert i.d.R. als Paket `PyTado`
    # und exportiert die Klasse über `PyTado.interface`.
    try:
        _pytado_interface_mod = importlib.import_module("PyTado.interface")
        Tado = getattr(_pytado_interface_mod, "Tado")
        _TADO_IMPL = "pytado"
        logging.info("[TADO] Import via PyTado.interface erfolgreich.")
    except ImportError as exc_pytado:
        try:
            _pytado_interface2_mod = importlib.import_module("PyTado.interface.interface")
            Tado = getattr(_pytado_interface2_mod, "Tado")
            _TADO_IMPL = "pytado"
            logging.info("[TADO] Import via PyTado.interface.interface erfolgreich.")
        except ImportError as exc_pytado2:
            if TADO_ENABLED:
                logging.warning("[TADO] PyTado Import fehlgeschlagen: %s", exc_pytado)
                logging.warning("[TADO] PyTado (alt) Import ebenfalls fehlgeschlagen: %s", exc_pytado2)
            else:
                logging.info("[TADO] PyTado nicht installiert (TADO deaktiviert)")

# --- KONFIGURATION ---
TADO_USER = os.getenv("TADO_USER")
TADO_PASS = os.getenv("TADO_PASS")
TADO_TOKEN_FILE = os.getenv(
    "TADO_TOKEN_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tado_refresh_token"),
)
# War "tado-web-app" - das ist der Grund fuer das dauerhafte NOT_STARTED
# (siehe _perform_login): Tado lehnt diese Client-ID mittlerweile mit
# "invalid_client_id" ab (sichtbar geworden erst, nachdem der "Im Browser
# oeffnen"-Button tatsaechlich funktionierte und die Tado-Fehlerseite
# zeigte). Der neue Wert ist die aktuelle Device-Flow-Client-ID, sowohl aus
# Tados eigener API-Doku (support.tado.com) als auch aus dem aktuellen
# PyTado-Quelltext (CLIENT_ID_DEVICE in PyTado/const.py) bestaetigt - beide
# Quellen stimmen ueberein. Die auf dem Pi installierte PyTado-Version ist
# vermutlich aelter und hat intern noch die alte, jetzt ungueltige
# Client-ID als Default - deshalb wird sie unten jetzt explizit mitgegeben
# statt sich auf den Library-internen Default zu verlassen.
TADO_CLIENT_ID = os.getenv("TADO_CLIENT_ID", "1bb50063-6b0c-4d11-bd99-387f4a91cc46")
TADO_SCOPE = os.getenv("TADO_SCOPE", "home.user")
TADO_ECO_TEMP = float(os.getenv("TADO_ECO_TEMP", "19.0"))
TADO_COMFORT_TEMP = float(os.getenv("TADO_COMFORT_TEMP", "21.0"))


# "auto" (HA wenn Thermostate vorhanden, sonst direkt) | "ha" | "direct"
TADO_SOURCE = os.getenv("TADO_SOURCE", "auto").strip().lower()
# Tado-API-Tageslimit: 20.000 mit Auto-Assist-Abo (vorhanden), sonst 100.
# Ohne Abo am Pi TADO_DAILY_LIMIT=100 setzen - das Intervall passt sich an.
DAILY_LIMIT = int(os.getenv("TADO_DAILY_LIMIT", "20000"))
RING_SIZE = int(os.getenv("TADO_RING_SIZE", "230"))   # maximale Ringgroesse


def _round_half(v: float) -> float:
    return round(float(v) * 2) / 2


class TadoTab(UiQueuePumpMixin):
    """Raumklima-Tab: alle Tado-Raeume auf einen Blick, Zieltemperatur per Touch-Slider.

    Datenquelle (automatisch, ueberschreibbar mit TADO_SOURCE=ha|direct):
    * Home Assistant, wenn dort climate.*-Entitaeten existieren - keine eigenen
      Tado-Abfragen, alle HA_POLL_S Sekunden aktualisiert.
    * Sonst direkt ueber python-tado/PyTado (Login per Geraete-Code wie bisher).
      Alle Raeume mit EINER Abfrage; Intervall aus DAILY_LIMIT (mit Abo 60 s,
      ohne Abo ~20 min).
    """

    _KEEP_URL = object()

    def __init__(self, root: tk.Tk, notebook: ttk.Notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self.api = None
        self.zone_id = None              # erste Zone (Kompatibilitaet)
        self.zones: list[dict] = []
        self._zone_ids: dict[str, object] = {}
        self._device_url: str | None = None

        self.source: str | None = None   # "ha" | "direct" | None
        self._ha_client = None
        self._rooms: list[C.Room] = []
        self._cards: dict[str, dict] = {}
        self._nudge_jobs: dict[str, object] = {}
        self._last_update: datetime | None = None
        self._next_poll: datetime | None = None
        self._wake = threading.Event()
        self._req_day = date.today()
        self._req_count = 0
        self._portrait = False
        self._hist_hours = 24
        self._series: dict = {}
        self._backfill_started = False
        try:
            self._history = ClimateHistory()
        except Exception as exc:
            logging.warning("[TADO] Verlauf-DB nicht verfuegbar: %s", exc)
            self._history = None

        self.var_status = tk.StringVar(value="Verbinde ...")
        self.var_hint = tk.StringVar(value="")

        self._init_ui_queue()
        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
            notebook.add(self.tab_frame, text=emoji("🌡️ Thermo", "Thermo"))
        try:
            self.tab_frame.configure(fg_color=COLOR_ROOT)
        except Exception:
            pass

        self._build_ui()
        self._start_ui_pump()
        threading.Thread(target=self._loop, daemon=True).start()
        logging.info("[TADO] Tab initialisiert")

    def stop(self):
        self.alive = False
        self._wake.set()

    # ------------------------------------------------------------------ UI --

    def _build_ui(self) -> None:
        self._shell = TabShell(self.tab_frame, "Thermostate", "Alle Räume – am Ring ziehen stellt die Zieltemperatur")
        self._shell.pack(fill=tk.BOTH, expand=True)
        self._shell.subtitle_label.configure(textvariable=self.var_status)
        body = self._shell.body

        # --- Uebersicht
        top = ctk.CTkFrame(body, fg_color=COLOR_CARD, corner_radius=18, border_width=1, border_color=COLOR_BORDER)
        top.pack(fill=tk.X, padx=4, pady=(4, 8))
        top.grid_columnconfigure(0, weight=1)
        left = ctk.CTkFrame(top, fg_color="transparent")
        left.grid(row=0, column=0, sticky="ew", padx=(14, 8), pady=10)
        self._summary_lbl = ctk.CTkLabel(left, text="🌡️  --", text_color=COLOR_TEXT, anchor="w", justify="left",
                                         font=get_safe_font("Bahnschrift", 14, "bold"), wraplength=440)
        self._summary_lbl.pack(anchor="w")
        self._info_lbl = ctk.CTkLabel(left, text="", text_color=COLOR_SUBTEXT, anchor="w", justify="left",
                                      font=get_safe_font("Bahnschrift", 11), wraplength=520)
        self._info_lbl.pack(anchor="w")
        rng = ctk.CTkFrame(self._shell.header, fg_color="transparent")
        rng.grid(row=0, column=1, rowspan=2, sticky="e", padx=18)
        ctk.CTkLabel(rng, text="Verlauf", text_color=COLOR_SUBTEXT,
                     font=get_safe_font("Bahnschrift", 12)).pack(side=tk.LEFT, padx=(0, 8))
        self._range_btns = {}
        for hrs, text in ((6, "6 h"), (24, "24 h"), (48, "2 Tage")):
            b = ctk.CTkButton(rng, text=text, width=64, height=32, corner_radius=16,
                              font=get_safe_font("Bahnschrift", 12, "bold"), text_color=COLOR_TEXT,
                              hover_color=COLOR_BORDER, command=lambda h=hrs: self._set_range(h))
            b.pack(side=tk.LEFT, padx=2)
            self._range_btns[hrs] = b
        self._update_range_buttons()
        btns = ctk.CTkFrame(top, fg_color="transparent")
        self._top_btns = btns
        btns.grid(row=0, column=1, sticky="e", padx=10, pady=10)
        self._pill(btns, "📅  Alle auf Plan", self._all_plan, COLOR_PRIMARY, 150).pack(side=tk.LEFT, padx=4)
        self._pill(btns, f"🌿  Eco {TADO_ECO_TEMP:.0f}°", lambda: self._all_temp(TADO_ECO_TEMP),
                   COLOR_SUCCESS, 120).pack(side=tk.LEFT, padx=4)
        self._pill(btns, f"☀  Komfort {TADO_COMFORT_TEMP:.0f}°", lambda: self._all_temp(TADO_COMFORT_TEMP),
                   COLOR_WARNING, 150).pack(side=tk.LEFT, padx=4)
        self._pill(btns, "↻", self._refresh_now, COLOR_BORDER, 48).pack(side=tk.LEFT, padx=4)

        # --- Login-/Hinweiszeile (nur sichtbar wenn Text vorhanden)
        self._hint_frame = ctk.CTkFrame(body, fg_color=COLOR_CARD, corner_radius=14)
        self._hint_label = ctk.CTkLabel(self._hint_frame, textvariable=self.var_hint, text_color=COLOR_SUBTEXT,
                                        font=get_safe_font("Bahnschrift", 12), wraplength=760, justify="left")
        self._hint_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=12, pady=8)
        self._open_url_btn = ctk.CTkButton(self._hint_frame, text="Im Browser öffnen", width=170, height=44,
                                           fg_color=COLOR_PRIMARY, hover_color=COLOR_SUCCESS,
                                           command=self._open_device_url, state="disabled")
        self._open_url_btn.pack(side=tk.RIGHT, padx=(6, 10), pady=8)
        self._reset_token_btn = ctk.CTkButton(self._hint_frame, text="Token zurücksetzen", width=160, height=44,
                                              fg_color=COLOR_DANGER, hover_color="#B91C1C",
                                              command=self._reset_tado_token)
        self._reset_token_btn.pack(side=tk.RIGHT, padx=6, pady=8)
        self._hint_anchor = top
        self.var_hint.trace_add("write", lambda *_: self._update_hint_visibility())

        # --- Raumkarten
        try:
            self._grid = ctk.CTkScrollableFrame(body, fg_color="transparent")
        except Exception:
            self._grid = ctk.CTkFrame(body, fg_color="transparent")
        self._grid.pack(fill=tk.BOTH, expand=True, padx=0)
        self._empty_lbl = ctk.CTkLabel(self._grid, text="Lade Räume ...", text_color=COLOR_SUBTEXT,
                                       font=get_safe_font("Bahnschrift", 14))
        self._empty_lbl.grid(row=0, column=0, padx=12, pady=20, sticky="w")
        self.tab_frame.bind("<Configure>", self._resize_rings, add="+")

    def _pill(self, parent, text, command, color, width=120):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=44, corner_radius=22,
                             fg_color=COLOR_ROOT, hover_color=COLOR_BORDER, border_width=2, border_color=color,
                             text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 13, "bold"))

    def _update_hint_visibility(self) -> None:
        try:
            if self.var_hint.get().strip():
                if not self._hint_frame.winfo_ismapped():
                    self._hint_frame.pack(fill=tk.X, padx=4, pady=(0, 8), after=self._hint_anchor)
            else:
                self._hint_frame.pack_forget()
        except Exception:
            pass

    def set_portrait_layout(self, portrait: bool) -> None:
        try:
            self._shell.set_portrait_layout(portrait)
        except Exception:
            pass
        try:
            if portrait:
                self._top_btns.grid_configure(row=1, column=0, sticky="w", pady=(0, 10))
            else:
                self._top_btns.grid_configure(row=0, column=1, sticky="e", pady=10)
        except Exception:
            pass
        if portrait != self._portrait:
            self._portrait = portrait
            self._layout_cards()

    def _columns(self) -> int:
        n = max(1, len(self._rooms))
        if self._portrait:
            return min(n, 2)
        return n if n <= 5 else (n + 1) // 2 if n <= 8 else 4

    def _resize_rings(self, _e=None) -> None:
        try:
            width = self.tab_frame.winfo_width()
        except Exception:
            return
        if width < 100:
            return
        cols = self._columns()
        size = max(130, min(RING_SIZE, int((width - 40) / cols) - 34))
        for w in self._cards.values():
            w["ring"].set_size(size)
            w["chart"].set_width(size + 20)

    def _layout_cards(self) -> None:
        cols = self._columns()
        for c in range(8):
            self._grid.grid_columnconfigure(c, weight=1 if c < cols else 0, uniform="room" if c < cols else "")
        for i, rid in enumerate(r.id for r in self._rooms):
            w = self._cards.get(rid)
            if w:
                w["frame"].grid(row=i // cols, column=i % cols, sticky="nsew", padx=4, pady=4)
        self._resize_rings()

    def _build_card(self, room: C.Room) -> dict:
        f = ctk.CTkFrame(self._grid, fg_color=COLOR_CARD, corner_radius=22)
        name = ctk.CTkLabel(f, text=room.name, text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 16, "bold"))
        name.pack(pady=(8, 0))
        rid = room.id
        ring = ThermostatRing(f, from_=C.TARGET_MIN, to=C.TARGET_MAX, size=160,
                              on_release=lambda v, r=rid: self._set_target(r, v),
                              on_step=lambda d, r=rid: self._nudge(r, d))
        ring.pack(padx=10, pady=(2, 4))
        chart = TempChart(f, width=180, height=72, on_tap=self._cycle_range)
        chart.pack(fill=tk.X, padx=10, pady=(0, 8))
        # − / + sitzen im Ring; hier nur Timer und Zeitplan
        btns = ctk.CTkFrame(f, fg_color="transparent")
        btns.pack(fill=tk.X, padx=10, pady=(0, 10))
        btns.grid_columnconfigure((0, 1), weight=1, uniform="b")
        small = dict(height=40, width=40, corner_radius=20, fg_color=COLOR_ROOT, hover_color=COLOR_BORDER,
                     text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 13, "bold"))
        for i, (text, cmd) in enumerate((("⏱ 1 h", lambda r=rid: self._set_timer(r)),
                                         ("📅 Plan", lambda r=rid: self._set_plan(r)))):
            ctk.CTkButton(btns, text=text, command=cmd, **small).grid(row=0, column=i, sticky="ew", padx=3)
        return {"frame": f, "name": name, "ring": ring, "chart": chart}

    def _render(self, rooms: list, series: dict | None = None) -> None:
        """Nur auf dem UI-Thread aufrufen."""
        old_ids = [r.id for r in self._rooms]
        self._rooms = list(rooms)
        if [r.id for r in rooms] != old_ids:
            for w in self._cards.values():
                w["frame"].destroy()
            self._cards = {r.id: self._build_card(r) for r in rooms}
            self._layout_cards()
        if rooms:
            self._empty_lbl.grid_remove()
        else:
            self._empty_lbl.configure(text="Keine Räume gefunden.")
            self._empty_lbl.grid()
        for r in rooms:
            self._update_card(r)
        if series is not None:
            self._apply_series(series)
        self._update_summary()

    def _update_card(self, r: C.Room, force_slider: bool = False) -> None:
        w = self._cards.get(r.id)
        if not w:
            return
        w["name"].configure(text=r.name if r.available else f"{r.name} (offline)")
        w["ring"].set_state(current=r.current, target=r.target, humidity=r.humidity, heating=r.heating,
                            mode=r.mode, window_open=r.window_open, force=force_slider)

    def _update_summary(self) -> None:
        s = C.summarize(self._rooms)
        self._summary_lbl.configure(text="🌡️  " + s.text)
        parts = []
        if self.source == "ha":
            parts.append("Quelle: Home Assistant")
        elif self.source == "direct":
            parts.append("Quelle: Tado direkt")
        if self._last_update:
            parts.append(f"aktualisiert {self._last_update:%H:%M}")
        if self.source == "direct":
            if self._next_poll:
                parts.append(f"nächste Abfrage {self._next_poll:%H:%M}")
            parts.append(f"{self._req_count}/{DAILY_LIMIT} Tado-Abfragen heute")
        self._info_lbl.configure(text=" · ".join(parts))

    # ------------------------------------------------------------ Befehle --

    def _room(self, rid: str):
        return next((r for r in self._rooms if r.id == rid), None)

    def _optimistic(self, rid: str, target=None, mode=None, force_slider=False) -> None:
        r = self._room(rid)
        if not r:
            return
        if target is not None:
            r.target = target
        if mode:
            r.mode = mode
        self._update_card(r, force_slider=force_slider)
        self._update_summary()

    def _set_target(self, rid: str, value: float) -> None:
        temp = _round_half(value)
        self._optimistic(rid, temp, "manual")
        self._command(lambda: self._do_set_temp([rid], temp), f"{self._name(rid)}: {C.fmt_temp(temp)}")

    def _nudge(self, rid: str, delta: float) -> None:
        r = self._room(rid)
        if not r:
            return
        base = r.target if r.target is not None else 20.0
        temp = max(C.TARGET_MIN, min(C.TARGET_MAX, _round_half(base + delta)))
        self._optimistic(rid, temp, "manual", force_slider=True)
        job = self._nudge_jobs.pop(rid, None)
        if job is not None:
            try:
                self.root.after_cancel(job)
            except Exception:
                pass
        # mehrere Tipper zu einem Befehl zusammenfassen (spart Tado-Abfragen)
        self._nudge_jobs[rid] = self.root.after(1500, lambda: self._flush_nudge(rid))

    def _flush_nudge(self, rid: str) -> None:
        self._nudge_jobs.pop(rid, None)
        r = self._room(rid)
        if r and r.target is not None:
            t = r.target
            self._command(lambda: self._do_set_temp([rid], t), f"{r.name}: {C.fmt_temp(t)}")

    def _set_timer(self, rid: str) -> None:
        r = self._room(rid)
        if not r:
            return
        temp = r.target if r.target is not None else TADO_COMFORT_TEMP
        self._optimistic(rid, temp, "manual")
        self._command(lambda: self._do_set_temp([rid], temp, duration_s=3600),
                      f"{r.name}: {C.fmt_temp(temp)} für 1 Stunde")

    def _set_plan(self, rid: str) -> None:
        self._optimistic(rid, mode="plan")
        self._command(lambda: self._do_plan([rid]), f"{self._name(rid)}: zurück auf Zeitplan")

    def _all_plan(self) -> None:
        ids = [r.id for r in self._rooms]
        for rid in ids:
            self._optimistic(rid, mode="plan")
        self._command(lambda: self._do_plan(ids), "Alle Räume auf Zeitplan")

    def _all_temp(self, temp: float) -> None:
        ids = [r.id for r in self._rooms]
        for rid in ids:
            self._optimistic(rid, temp, "manual", force_slider=True)
        self._command(lambda: self._do_set_temp(ids, temp), f"Alle Räume: {C.fmt_temp(temp)}")

    def apply_profile_safe(self, profile: str) -> bool:
        """eco / comfort / auto fuer alle Raeume (fuer Automationen)."""
        p = (profile or "").strip().lower()
        ids = [r.id for r in self._rooms]
        if not ids:
            return False
        try:
            if p in ("auto", "schedule"):
                return self._do_plan(ids)
            if p in ("eco", "spar", "save"):
                return self._do_set_temp(ids, float(TADO_ECO_TEMP))
            if p in ("comfort", "komfort", "home"):
                return self._do_set_temp(ids, float(TADO_COMFORT_TEMP))
        except Exception:
            return False
        return False

    def _name(self, rid: str) -> str:
        r = self._room(rid)
        return r.name if r else rid

    def _command(self, fn, label: str) -> None:
        if not self._rooms or self.source is None:
            return

        def worker():
            try:
                ok = bool(fn())
            except Exception as exc:
                logging.warning("[TADO] Befehl fehlgeschlagen (%s): %s", label, exc)
                ok = False
            self._post_ui(lambda: self.var_status.set(("✓ " if ok else "⚠️ Fehlgeschlagen: ") + label))
            # Danach echten Zustand nachladen. Direkt ohne Abo (100/Tag) nicht -
            # dann bleibt der angezeigte Wert bis zur naechsten Abfrage stehen.
            if self.source == "ha" or DAILY_LIMIT >= 1000:
                time.sleep(3.0)
                self._wake.set()
        threading.Thread(target=worker, daemon=True).start()

    def _do_set_temp(self, ids: list, temp: float, duration_s: int | None = None) -> bool:
        if self.source == "ha":
            c = self._ha_client
            if duration_s:
                ok = True
                for ent in ids:   # Tado-spezifischer Dienst, je Entitaet
                    try:
                        c.call_service("tado", "set_climate_timer", {
                            "entity_id": ent, "temperature": temp,
                            "time_period": f"{duration_s // 3600:02d}:{duration_s % 3600 // 60:02d}:00"})
                    except Exception:
                        ok = c.call_service("climate", "set_temperature", {"entity_id": ent, "temperature": temp}) and ok
                return ok
            return c.call_service("climate", "set_temperature", {"entity_id": ids, "temperature": temp})
        if self.source == "direct":
            for rid in ids:
                self._direct_set_temp(self._zone_ids.get(rid, rid), temp, duration_s)
            return True
        return False

    def _do_plan(self, ids: list) -> bool:
        if self.source == "ha":
            return self._ha_client.call_service("climate", "set_hvac_mode", {"entity_id": ids, "hvac_mode": "auto"})
        if self.source == "direct":
            for rid in ids:
                self._direct_reset(self._zone_ids.get(rid, rid))
            return True
        return False

    def _count_request(self, n: int = 1) -> None:
        today = date.today()
        if today != self._req_day:
            self._req_day, self._req_count = today, 0
        self._req_count += n

    def _try_api(self, attempts) -> None:
        """Erste passende API-Variante ausfuehren (python-tado vs. PyTado)."""
        last = None
        for name, args, kwargs in attempts:
            fn = getattr(self.api, name, None)
            if not callable(fn):
                continue
            try:
                self._count_request()
                fn(*args, **kwargs)
                return
            except TypeError as exc:
                last = exc
        raise last or RuntimeError("Keine passende Tado-API-Methode gefunden")

    def _direct_set_temp(self, zone, temp: float, duration_s: int | None = None) -> None:
        if duration_s:
            attempts = [
                ("set_zone_overlay", (zone,), dict(overlay_mode="TIMER", set_temp=float(temp), duration=duration_s,
                                                    device_type="HEATING", power="ON")),
                ("setZoneOverlay", (zone, "TIMER", float(temp), duration_s), {}),
            ]
        else:
            # bis zur naechsten Planaenderung (wie in der Tado-App) - vergisst man nicht
            attempts = [
                ("set_zone_overlay", (zone,), dict(overlay_mode="NEXT_TIME_BLOCK", set_temp=float(temp),
                                                    device_type="HEATING", power="ON")),
                ("setZoneOverlay", (zone, "NEXT_TIME_BLOCK", float(temp)), {}),
                ("set_temperature", (zone, float(temp)), {}),
            ]
        self._try_api(attempts)

    def _direct_reset(self, zone) -> None:
        self._try_api([
            ("reset_zone_overlay", (zone,), {}),
            ("resetZoneOverlay", (zone,), {}),
            ("reset_zone_override", (zone,), {}),
        ])

    def _refresh_now(self) -> None:
        if self.source == "direct" and self._req_count >= DAILY_LIMIT - 10:
            self.var_status.set("Tageslimit fast erreicht - nächste Abfrage automatisch")
            return
        self._wake.set()

    def health_text(self) -> str:
        """Fuer den Health-Tab - ohne eigene Tado-Abfrage."""
        if self.source == "ha":
            return f"Tado: OK (Home Assistant, {len(self._rooms)} Räume)"
        if self.source == "direct":
            if self._last_update:
                return f"Tado: OK ({self._req_count}/{DAILY_LIMIT} Abfragen heute)"
            return "Tado: verbinde ..."
        return "Tado: –"

    # ------------------------------------------------------- Datenquelle --

    def _publish(self, rooms: list, status: str) -> None:
        """Aus dem Worker-Thread: Werte speichern, Verlauf lesen, UI aktualisieren."""
        series = None
        if self._history is not None and rooms:
            try:
                self._history.add_rooms(rooms)
                series = self._load_series([r.id for r in rooms], self._hist_hours)
            except Exception as exc:
                logging.warning("[TADO] Verlauf speichern fehlgeschlagen: %s", exc)

        def apply():
            self._last_update = datetime.now()
            self.var_status.set(status)
            self._render(rooms, series)
        self._post_ui(apply)
        if rooms and not self._backfill_started and self._history is not None:
            self._backfill_started = True
            threading.Thread(target=self._backfill, args=([r.id for r in rooms],), daemon=True).start()

    # ------------------------------------------------------------ Verlauf --

    def _load_series(self, ids: list, hours: float) -> dict:
        since = time.time() - hours * 3600 - 600
        return {rid: self._history.query(rid, since) for rid in ids}

    def _apply_series(self, series: dict) -> None:
        self._series = series
        now = time.time()
        for rid, w in self._cards.items():
            w["chart"].set_data(series.get(rid, []), self._hist_hours, now)

    def _update_range_buttons(self) -> None:
        for hrs, b in self._range_btns.items():
            on = hrs == self._hist_hours
            b.configure(fg_color=COLOR_PRIMARY if on else COLOR_ROOT)

    def _set_range(self, hours: int) -> None:
        self._hist_hours = hours
        self._update_range_buttons()
        if self._history is None:
            return
        ids = [r.id for r in self._rooms]

        def work():
            try:
                series = self._load_series(ids, hours)
            except Exception:
                return
            self._post_ui(lambda: self._apply_series(series))
        threading.Thread(target=work, daemon=True).start()

    def _cycle_range(self) -> None:
        order = [6, 24, 48]
        self._set_range(order[(order.index(self._hist_hours) + 1) % 3] if self._hist_hours in order else 24)

    def _backfill(self, ids: list) -> None:
        """Einmal beim Start: die letzten 2 Tage aus HA bzw. dem Tado-Tagesreport nachladen."""
        hist = self._history
        todo = [rid for rid in ids if hist.needs_backfill(rid, 48)]
        if not todo:
            return
        start = time.time() - 48 * 3600
        added = 0
        try:
            if self.source == "ha" and self._ha_client is not None:
                iso = datetime.fromtimestamp(start, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
                for states in self._ha_client.get_history(todo, iso):
                    if not states:
                        continue
                    ent = states[0].get("entity_id")
                    for st in states:          # minimal_response liefert entity_id nur im ersten Eintrag
                        st.setdefault("entity_id", ent)
                    added += hist.add_points(ent, points_from_ha_history(states, start))
            elif self.source == "direct" and DAILY_LIMIT >= 1000:
                # Tagesreport je Zone und Tag (3 Tage = 3 Abfragen pro Raum, nur mit Abo)
                days = [(date.today() - timedelta(days=d)).isoformat() for d in (2, 1, 0)]
                for rid in todo:
                    zone = self._zone_ids.get(rid, rid)
                    for day in days:
                        fn = getattr(self.api, "get_historic", None) or getattr(self.api, "getHistoric", None)
                        if not callable(fn):
                            return
                        self._count_request()
                        rep = self._state_to_dict(fn(zone, day))
                        added += hist.add_points(rid, [p for p in points_from_tado_day_report(rep) if p[0] >= start])
        except Exception as exc:
            logging.warning("[TADO] Verlauf nachladen fehlgeschlagen: %s: %s", type(exc).__name__, exc)
        logging.info("[TADO] Verlauf nachgeladen: %s Punkte", added)
        if added:
            self._set_range_from_thread()

    def _set_range_from_thread(self) -> None:
        self._post_ui(lambda: self._set_range(self._hist_hours))

    def _wait(self, seconds: float) -> None:
        self._wake.clear()
        self._wake.wait(timeout=seconds)

    def _loop(self) -> None:
        source = self._detect_source()
        self.source = source
        if source == "ha":
            self._ha_loop()
        elif source == "direct":
            self._direct_loop()
        else:
            self._ui_set(self.var_status, "Tado nicht eingerichtet")
            self._set_hint("Weder Thermostate in Home Assistant (climate.*) gefunden noch python-tado installiert. "
                           "Am einfachsten die Tado-Integration in Home Assistant hinzufügen und das Dashboard neu starten.")
            self._post_ui(lambda: self._render([]))

    def _detect_source(self) -> str | None:
        want = TADO_SOURCE
        if want in ("auto", "ha"):
            cfg = None
            try:
                cfg = load_homeassistant_config()
            except Exception:
                cfg = None
            if cfg:
                self._ha_client = HomeAssistantClient(cfg)
                for attempt in range(3):
                    if not self.alive:
                        return None
                    try:
                        if C.rooms_from_ha(self._ha_client.get_states()):
                            return "ha"
                        break   # HA erreichbar, aber keine Thermostate
                    except Exception as exc:
                        logging.info("[TADO] HA nicht erreichbar (%s), Versuch %s", exc, attempt + 1)
                        time.sleep(10)
            if want == "ha":
                return "ha" if self._ha_client else None
        return "direct" if Tado is not None else None

    def _ha_loop(self) -> None:
        logging.info("[TADO] Quelle: Home Assistant")
        while self.alive:
            try:
                rooms = C.rooms_from_ha(self._ha_client.get_states())
                self._publish(rooms, "Verbunden über Home Assistant" if rooms else "Keine climate.*-Entitäten in HA")
            except Exception as exc:
                self._ui_set(self.var_status, f"⚠️ Home Assistant nicht erreichbar ({type(exc).__name__})")
            self._wait(C.HA_POLL_S)

    def _direct_loop(self) -> None:
        logging.info("[TADO] Quelle: Tado direkt (%s)", _TADO_IMPL)
        if not self._perform_login():
            return
        self._count_request()   # get_zones beim Login
        zones = [z for z in (self.zones or []) if isinstance(z, dict)
                 and str(z.get("type", "HEATING")).upper() == "HEATING"]
        self._zone_ids = {str(z.get("id")): z.get("id") for z in zones}
        names = {str(z.get("id")): str(z.get("name") or z.get("id")) for z in zones}
        if zones:
            self.zone_id = zones[0].get("id")
        self._set_hint("", clear_url=True)
        if not zones:
            self._ui_set(self.var_status, "Tado: keine Heizungs-Zonen gefunden")
            self._post_ui(lambda: self._render([]))
            return
        while self.alive:
            calls = 1
            try:
                rooms, calls = self._fetch_direct(names)
                self._publish(rooms, "Verbunden mit Tado")
            except Exception as exc:
                logging.warning("[TADO] zoneStates fehlgeschlagen: %s: %s", type(exc).__name__, exc)
                self._ui_set(self.var_status, f"⚠️ Tado-Abfrage fehlgeschlagen ({type(exc).__name__})")
            interval = C.direct_poll_s(DAILY_LIMIT) * max(1, calls)
            if self._req_count >= DAILY_LIMIT - 5:
                # Limit fast erreicht: bis Mitternacht pausieren
                now = datetime.now()
                interval = max(interval, (datetime.combine(now.date() + timedelta(days=1), dtime(0, 5)) - now).total_seconds())
            self._next_poll = datetime.now() + timedelta(seconds=interval)
            self._post_ui(self._update_summary)
            self._wait(interval)

    def _fetch_direct(self, names: dict):
        """Alle Zonen mit einer Abfrage; Rueckfall: je Zone eine (teuer)."""
        states: dict = {}
        calls = 0
        for meth in ("get_zone_states", "getZoneStates"):
            fn = getattr(self.api, meth, None)
            if callable(fn):
                calls += 1
                self._count_request()
                raw = self._state_to_dict(fn())
                zs = raw.get("zoneStates", raw) if isinstance(raw, dict) else None
                if isinstance(zs, dict):
                    states = {str(k): self._state_to_dict(v) for k, v in zs.items()}
                break
        if not any(k in states for k in names):
            states = {}
            for zid in names:
                calls += 1
                self._count_request()
                states[zid] = self._state_to_dict(self._get_zone_state(self._zone_ids.get(zid, zid)))
        rooms = [C.room_from_tado_state(zid, names[zid], states[zid]) for zid in names if zid in states]
        rooms.sort(key=lambda r: r.name.lower())
        return rooms, calls

    # ------------------------------------------------- Thread-Helfer --

    def _ui_set(self, var: tk.StringVar, value: str):
        self._post_ui(lambda: var.set(value))

    def _ui_call(self, fn, *args, **kwargs) -> None:
        self._post_ui(lambda: fn(*args, **kwargs))

    def _set_controls_enabled(self, enabled: bool) -> None:
        for w in self._cards.values():
            try:
                w["ring"].set_enabled(enabled)
            except Exception:
                pass

    def _get_zone_state(self, zone_id):
        for meth in ("get_zone_state", "getZoneState", "getState"):
            fn = getattr(self.api, meth, None)
            if callable(fn):
                return fn(zone_id)
        raise AttributeError("get_zone_state")

    # ---------------------------------------- Login / Praesenz (unveraendert) --

    def set_away_safe(self) -> bool:
        """Set Tado to 'Away' presence (best-effort).

        Supports both python-tado and PyTado variants.
        Returns True if a compatible method was found and invoked.
        """
        api = getattr(self, "api", None)
        if api is None:
            return False

        # PyTado (installed in this project) uses setAway()/setHome().
        for name in ("setAway", "set_away", "setAwayMode", "set_away_mode"):
            fn = getattr(api, name, None)
            if callable(fn):
                try:
                    fn()
                    return True
                except Exception:
                    return False

        # Fallback: changePresence("AWAY") (some versions)
        fn = getattr(api, "changePresence", None)
        if callable(fn):
            try:
                fn("AWAY")
                return True
            except Exception:
                return False

        # As a last resort, at least drop any manual override back to schedule.
        try:
            zone_id = getattr(self, "zone_id", None)
            if zone_id and callable(getattr(api, "reset_zone_override", None)):
                api.reset_zone_override(zone_id)
                return True
        except Exception:
            return False

        return False

    def set_home_safe(self) -> bool:
        """Set Tado to 'Home' presence (best-effort).

        Supports both python-tado and PyTado variants.
        Returns True if a compatible method was found and invoked.
        """
        api = getattr(self, "api", None)
        if api is None:
            return False

        for name in ("setHome", "set_home", "setHomeMode", "set_home_mode"):
            fn = getattr(api, name, None)
            if callable(fn):
                try:
                    fn()
                    return True
                except Exception:
                    return False

        fn = getattr(api, "changePresence", None)
        if callable(fn):
            try:
                fn("HOME")
                return True
            except Exception:
                return False

        return False

    def _check_tado_reachable(self) -> str:
        """Simpler TCP-Connect-Test auf den Tado-OAuth-Host (siehe Aufrufstelle).

        Kein HTTP-Request, keine neue Abhaengigkeit noetig - reicht als grober
        Netzwerk-Check (DNS-Aufloesung + TCP-Handshake auf Port 443).
        """
        try:
            socket.create_connection(("login.tado.com", 443), timeout=4).close()
            return "erreichbar"
        except Exception as e:
            return f"NICHT erreichbar ({type(e).__name__}: {e})"

    def _reset_tado_token(self) -> None:
        """Loescht die lokal gecachte Tado-Token-Datei.

        Reiner Datei-Delete, keine Aenderung an der eigentlichen Login-Logik.
        Sinnvoll als Diagnose-/Reparaturschritt, falls eine alte/beschaedigte
        Token-Datei aus einem frueheren (fehlgeschlagenen) Login-Versuch den
        Geraete-Code-Flow dauerhaft blockiert. Der bereits laufende Login-
        Hintergrund-Thread haelt sein Tado(...)-Objekt weiter im Speicher -
        wirkt also erst nach einem manuellen Neustart des Dashboards, daher
        hier bewusst kein Versuch, den laufenden Thread zu unterbrechen.
        """
        try:
            if os.path.exists(TADO_TOKEN_FILE):
                os.remove(TADO_TOKEN_FILE)
                logging.info("[TADO] Token-Datei gelöscht: %s", TADO_TOKEN_FILE)
                self._ui_set(
                    self.var_hint,
                    "Token-Datei gelöscht. Bitte Dashboard neu starten, damit die Aktivierung neu beginnt.",
                )
            else:
                logging.info("[TADO] Keine Token-Datei zum Löschen gefunden: %s", TADO_TOKEN_FILE)
                self._ui_set(
                    self.var_hint,
                    "Keine Token-Datei gefunden (schon leer). Bitte Dashboard trotzdem neu starten.",
                )
        except Exception as e:
            logging.error("[TADO] Token-Reset fehlgeschlagen: %s", e)
            self._ui_set(self.var_hint, f"Token-Reset fehlgeschlagen: {e}")

    def _open_device_url(self) -> None:
        url = self._device_url
        logging.info("[TADO] Button geklickt, URL: %s", url)
        if not url:
            logging.warning("[TADO] Keine URL gesetzt!")
            self._ui_set(self.var_hint, "Kein Aktivierungslink verfügbar - bitte warten oder Login neu starten.")
            return
        try:
            # Also place the link on the clipboard: the dashboard often runs
            # on a headless Pi while the user opens the link on another device.
            clipboard_ok = False
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(url)
                self.root.update()
                clipboard_ok = True
            except Exception:
                pass
            # Try multiple methods for Raspberry Pi compatibility
            import subprocess
            import platform

            system = platform.system().lower()
            logging.info("[TADO] System: %s, versuche Browser zu öffnen für: %s", system, url)
            opened_with = None
            attempts: list[str] = []

            if system == "linux":
                # Try common Linux browsers
                for cmd in ["xdg-open", "chromium-browser", "chromium", "firefox", "sensible-browser"]:
                    try:
                        result = subprocess.Popen([cmd, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        logging.info("[TADO] Browser gestartet mit: %s (pid=%s)", cmd, result.pid)
                        opened_with = cmd
                        break
                    except FileNotFoundError:
                        logging.debug("[TADO] %s nicht gefunden", cmd)
                        attempts.append(f"{cmd}: nicht installiert")
                        continue
                    except Exception as e:
                        logging.warning("[TADO] %s fehlgeschlagen: %s", cmd, e)
                        attempts.append(f"{cmd}: {e}")
                        continue

            if opened_with is None:
                logging.info("[TADO] Fallback: webbrowser.open()")
                try:
                    if webbrowser.open_new_tab(url):
                        opened_with = "webbrowser-Modul"
                    else:
                        attempts.append("webbrowser-Modul: kein Erfolg gemeldet")
                except Exception as e:
                    attempts.append(f"webbrowser-Modul: {e}")

            if opened_with:
                clip_note = " (Link auch in Zwischenablage kopiert)" if clipboard_ok else ""
                self._ui_set(self.var_hint, f"Browser geöffnet via {opened_with}{clip_note}: {url}")
                logging.info("[TADO] Browser erfolgreich geöffnet via %s", opened_with)
            else:
                detail = " | ".join(attempts) if attempts else "kein Browser gefunden"
                clip_note = " Link wurde in die Zwischenablage kopiert." if clipboard_ok else ""
                self._ui_set(self.var_hint, f"Konnte keinen Browser öffnen ({detail}).{clip_note}\nLink manuell öffnen: {url}")
                logging.warning("[TADO] Kein Browser konnte geöffnet werden: %s", detail)
        except Exception as e:
            logging.error("[TADO] Browser öffnen fehlgeschlagen: %s", e)
            self._ui_set(self.var_hint, f"Fehler beim Öffnen: {e}\nURL manuell öffnen: {url}")

    def _set_hint(self, text: str, device_url: str | None | object = _KEEP_URL, clear_url: bool = False) -> None:
        # Only update _device_url if explicitly passed or clear_url=True
        if clear_url:
            self._device_url = None
            logging.info("[TADO] _set_hint: clearing device_url")
        elif device_url is not self._KEEP_URL:
            self._device_url = device_url
            logging.info("[TADO] _set_hint: device_url=%s", device_url)
        else:
            logging.info("[TADO] _set_hint: keeping existing device_url=%s", self._device_url)
        
        self._ui_set(self.var_hint, text)
        current_url = self._device_url
        def _btn_state():
            try:
                new_state = "normal" if current_url else "disabled"
                logging.debug("[TADO] Button state -> %s", new_state)
                self._open_url_btn.configure(state=new_state)
            except Exception as e:
                logging.warning("[TADO] Button state update failed: %s", e)
        self._ui_call(_btn_state)

    def _get_nested(self, data: dict, *keys, default=None):
        cur = data
        for key in keys:
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    def _state_to_dict(self, state):
        if isinstance(state, dict):
            return state
        for attr in ("to_dict", "dict"):
            fn = getattr(state, attr, None)
            if callable(fn):
                try:
                    return fn()
                except Exception:
                    pass
        try:
            return dict(state)
        except Exception:
            pass
        try:
            return vars(state)
        except Exception:
            return {}

    def _normalize_device_url(self, url: str | None) -> str | None:
        if not url:
            return url
        try:
            parsed = urlparse(url)
            query = parse_qs(parsed.query, keep_blank_values=True)
            changed = False
            if TADO_CLIENT_ID and "client_id" not in query:
                query["client_id"] = [TADO_CLIENT_ID]
                changed = True
            if TADO_SCOPE and "scope" not in query:
                query["scope"] = [TADO_SCOPE]
                changed = True
            if not changed:
                return url
            return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
        except Exception:
            return url

    def _call_any(self, *names: str, **kwargs):
        api = getattr(self, "api", None)
        if api is None:
            raise RuntimeError("Tado API not connected")
        last_exc = None
        for name in names:
            try:
                fn = getattr(api, name)
                return fn(**kwargs)
            except TypeError as exc:
                last_exc = exc
            except AttributeError as exc:
                last_exc = exc
        if last_exc:
            raise last_exc

    def _get_zones(self):
        return self._call_any("get_zones", "getZones")

    def _perform_login(self) -> bool:
        """Tado-Login mit automatischem Retry.

        Vorher: ein einzelner Fehlschlag beim Login (z.B. weil beim
        Erstellen von Tado(...) - typischerweise kurz nach dem Boot des Pi,
        wenn Netzwerk/DNS noch nicht bereit sind - der Geraete-Code-Flow auf
        Tado's Servern nicht registriert werden konnte und
        device_activation_status() dauerhaft bei "NOT_STARTED" blieb) hat
        die Tado-Integration fuer den Rest des App-Laufs komplett
        lahmgelegt ("Tado Aktivierung fehlgeschlagen" / "Login
        fehlgeschlagen"), obwohl sich die Ursache (Netzwerk) kurz danach von
        selbst geloest haette. Lokal auf denselben self.api weiter zu warten
        half dabei nicht, weil device_activation_status() nur den beim
        Erstellen von Tado(...) einmalig festgelegten Zustand zurueckgibt -
        Tado(...) muss dafuer komplett NEU erstellt werden.

        Jetzt: bei jedem Fehlschlag wird nach einer kurzen, wachsenden Pause
        der komplette Login (inkl. Neu-Erstellen von Tado(...)) automatisch
        wiederholt, bis er klappt oder die App beendet wird.

        Returns True sobald self.zones erfolgreich geladen wurde, False
        wenn die App waehrenddessen beendet wurde (self.alive == False)
        oder python-tado fehlt (dauerhafter Fehler).
        """
        login_attempt = 0
        while self.alive:
            login_attempt += 1
            try:
                # Prefer direct User/Pass only for python-tado.
                # PyTado uses token/device activation flow (token_file_path).
                if _TADO_IMPL == "python_tado" and TADO_USER and TADO_PASS:
                    try:
                        self.api = Tado(TADO_USER, TADO_PASS)
                        self._ui_set(self.var_status, "Verbunden")
                    except Exception:
                        self.api = Tado(TADO_USER, TADO_PASS, client_id=TADO_CLIENT_ID)
                        self._ui_set(self.var_status, "Verbunden")
                else:
                    # OAuth Device Flow (seit 2025) + Token-Cache. client_id
                    # jetzt explizit mitgegeben (siehe TADO_CLIENT_ID oben) -
                    # ohne das faellt PyTado auf seinen eigenen internen
                    # Default zurueck, der auf der hier installierten Version
                    # offenbar noch die alte, von Tado mittlerweile
                    # abgelehnte Client-ID ist (invalid_client_id).
                    try:
                        self.api = Tado(token_file_path=TADO_TOKEN_FILE, client_id=TADO_CLIENT_ID)
                    except TypeError:
                        # Sehr alte PyTado-Version ohne client_id-Parameter im
                        # Konstruktor - dann bleibt nur der Library-interne
                        # Default (kann die alte, ungueltige Client-ID sein).
                        logging.warning(
                            "[TADO] Installierte PyTado-Version akzeptiert kein "
                            "client_id-Argument - nutze Library-Default."
                        )
                        self.api = Tado(token_file_path=TADO_TOKEN_FILE)
                    status = self.api.device_activation_status()
                    logging.info(
                        "[TADO] Login-Versuch %s: device_activation_status=%s",
                        login_attempt, status,
                    )
                    if status != "COMPLETED":
                        # WICHTIG: Laut PyTado-Doku liefert device_verification_url()
                        # erst ab Status PENDING eine echte URL - waehrend
                        # NOT_STARTED ist sie None (der Device-Code-Flow ist auf
                        # Tado's Servern noch gar nicht registriert). Der Code hier
                        # hat die URL bisher VOR dieser Wartezeit abgerufen und sie
                        # danach nie neu geholt - "url" blieb dadurch dauerhaft
                        # None, der Aktivierungs-Hinweis/Button im UI blieb leer
                        # ("Tado Aktivierung fehlgeschlagen. URL: None"), obwohl
                        # nach dem Warten auf PENDING eine echte URL verfuegbar
                        # gewesen waere. Fix: erst warten, DANN die URL holen.
                        start = time.time()
                        while status == "NOT_STARTED" and (time.time() - start) < 10:
                            time.sleep(1)
                            status = self.api.device_activation_status()

                        url = self._normalize_device_url(self.api.device_verification_url())
                        if url:
                            logging.info("[TADO] Device activation URL: %s", url)
                            self._ui_set(self.var_status, "Tado: Bitte Gerät im Browser aktivieren")
                            self._set_hint(f"Aktivierung erforderlich: {url}", device_url=url)
                        else:
                            logging.warning(
                                "[TADO] Keine Verification-URL erhalten (status=%s, Versuch %s)",
                                status, login_attempt,
                            )

                        if status == "PENDING":
                            self.api.device_activation()
                            status = self.api.device_activation_status()
                            logging.debug("[TADO] Status nach Aktivierung: %s", status)

                        if status != "COMPLETED":
                            # Nochmal versuchen, falls sich seit oben etwas geaendert hat.
                            url = self._normalize_device_url(self.api.device_verification_url()) or url
                            wait_s = min(15 * login_attempt, 120)
                            # War bisher "Tado Aktivierung ausstehend (Versuch 29,
                            # naechster Versuch in 120s)" / "Aktivierung nicht
                            # abgeschlossen. URL: None" - interner Retry-Zaehler
                            # und ein rohes "URL: None" landeten direkt im
                            # Dashboard, obwohl das reine Debug-Infos sind (die
                            # Versuchsnummer/Wartezeit steht weiterhin im Log,
                            # siehe logging.warning unten). Nutzer-Text jetzt
                            # nur noch: was ist zu tun (Link nutzen, falls
                            # vorhanden - sonst kurz warten).
                            self._ui_set(self.var_status, "Tado: Aktivierung im Browser ausstehend")
                            # Der PyTado-Status (NOT_STARTED/PENDING/...) ist kein
                            # interner Debug-Zaehler wie "Versuch N", sondern der
                            # eigentliche Fortschritt des Aktivierungsflows - ohne
                            # Konsolen-/SSH-Zugriff auf den Pi war das bisher
                            # unsichtbar. Kurz mit anzeigen hilft einzugrenzen, ob
                            # ueberhaupt ein Geraete-Code bei Tado registriert wurde
                            # (NOT_STARTED) oder nur die Aktivierung im Browser
                            # noch aussteht (PENDING).
                            if url:
                                # Keep URL available for manual activation
                                self._set_hint(
                                    "Bitte Tado im Browser aktivieren (Link rechts oeffnen) - "
                                    f"wird im Hintergrund automatisch weiter geprueft. (Status: {status})",
                                    device_url=url,
                                )
                            else:
                                # Status bleibt hier oft dauerhaft NOT_STARTED, ohne dass
                                # Tado(...) oder device_activation_status() eine Exception
                                # werfen - kann u.a. daran liegen, dass der eigentliche
                                # Registrierungs-Request von PyTado
                                # (POST https://login.tado.com/oauth2/device_authorize,
                                # lt. PyTado-Quelltext) den Pi wegen eines Netzwerk-/DNS-/
                                # Firewall-Problems gar nicht erst erreicht. Ohne Konsolen-
                                # /SSH-Zugriff auf den Pi war das bisher nicht von einem
                                # "wartet einfach noch" zu unterscheiden - ein einfacher
                                # TCP-Connect-Test auf denselben Host macht das sichtbar.
                                reachability = self._check_tado_reachable()
                                self._set_hint(
                                    f"Warte auf Aktivierungs-Link von Tado ... (Status: {status}, "
                                    f"login.tado.com: {reachability})",
                                    device_url=None,
                                )
                            logging.warning(
                                "[TADO] Login-Versuch %s fehlgeschlagen (status=%s), naechster Versuch in %ss",
                                login_attempt, status, wait_s,
                            )
                            for _ in range(wait_s):
                                if not self.alive:
                                    return False
                                time.sleep(1)
                            continue

                zones = self._get_zones()
                logging.debug("[TADO] zones gefunden: %s", len(zones))
                self.zones = zones or []
                return True

            except ImportError:
                self._ui_set(self.var_status, "python-tado nicht installiert! Bitte im Terminal ausführen: 'pip install python-tado' (im .venv falls vorhanden). Dann Dashboard neu starten.")
                self._set_hint("Bitte `pip install python-tado` ausführen und Dashboard neu starten.")
                self._ui_call(self._set_controls_enabled, False)
                while self.alive:
                    time.sleep(5)
                return False
            except Exception as e:
                # Zeige Fehlername und ggf. Message für bessere Diagnose
                err_type = type(e).__name__
                err_msg = str(e)
                wait_s = min(15 * login_attempt, 120)
                msg = f"Login fehlgeschlagen: {err_type} (Versuch {login_attempt}, naechster Versuch in {wait_s}s)"
                if err_msg:
                    msg += f" – {err_msg}"
                self._ui_set(self.var_status, msg)
                self._set_hint(f"Login/Verbindung fehlgeschlagen, naechster Versuch in {wait_s}s...")
                self._ui_call(self._set_controls_enabled, False)
                logging.exception(
                    "[TADO] Login-Versuch %s mit Exception fehlgeschlagen, naechster Versuch in %ss",
                    login_attempt, wait_s,
                )
                for _ in range(wait_s):
                    if not self.alive:
                        return False
                    time.sleep(1)
                continue
        return False
