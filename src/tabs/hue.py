"""Licht-Tab (Home Assistant).

Aufbau
------
* Oben "Alle Lichter": grosser Live-Dimmer (TouchSlider), Lichtfarbe warm<->kalt,
  Alles aus, Auto-Modus, Vorraum-Status (nur Anzeige).

Vorraum: wird ausschliesslich vom Bewegungsmelder geschaltet. Das Dashboard
nimmt alle Vorraum-Lichter aus Dimmer, Szenen, Stimmungen und Ablaeufen aus
(core.lights.excluded_entities) und zeigt nur den Status an.
* Ansicht "Szenen": Favoriten, Stimmungen (Film, Essen, Lesen, Arbeiten,
  Nachtlicht, Party), Home-Assistant-Szenen nach Bereich gruppiert, Kacheln in
  der Farbe der Szene, "Aktuelles Licht als Szene speichern" (Bildschirmtastatur).
  Lange druecken: Favorit setzen / eigene Szene loeschen.
* Ansicht "Raeume": je HA-Bereich eine Karte mit Schalter, Dimmer, Lichtfarbe
  und den einzelnen Lampen.
* Ansicht "Ablaeufe": Aufwachen (Sonnenaufgang), Gute Nacht, Party, Auto-Modus.

Logik: core/lights.py. Die alte oeffentliche API fuer app.py bleibt erhalten
(`_threaded_group_cmd`, `activate_scene_by_name_safe`, `come_home_safe`,
`leave_home_safe`, `cleanup`, `bridge`).
"""

from __future__ import annotations

import threading
import time
import tkinter as tk
from datetime import datetime
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from core import lights as L
from core.homeassistant import HomeAssistantClient, load_homeassistant_config
from ui.components.gestures import bind_long_press
from ui.components.onscreen_keyboard import OnscreenKeyboard
from ui.components.tab_shell import TabShell
from ui.components.touch_slider import TouchSlider
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

POLL_MS = 10_000
AREA_REFRESH_S = 300
AUTO_TICK_MS = 120_000
# Deckenlampe (Schalter) geht beim Dimmen ab diesem Wert mit an, darunter aus.
# Ueberschreibbar mit "ceiling_threshold_pct" in config/homeassistant.json.
CEILING_ON_PCT = 75
KELVIN_GRADIENT = ["#ff9b3d", "#ffc58a", "#fff1dc", "#e6f0ff", "#b9d2ff"]


def _bri_fmt(v: float) -> str:
    return "Aus" if v < 1 else f"{v:.0f} %"


def _k_fmt(v: float) -> str:
    return f"{v:.0f} K"


_SCENE_ICONS = [
    (frozenset({"aus"}), "power"),
    (frozenset({"ein", "hell", "an"}), "💡"),
    (frozenset({"nacht", "nachtlicht"}), "🌙"),
    (frozenset({"chill", "relax"}), "🛋️"),
    (frozenset({"pc", "arbeit", "office"}), "🖥️"),
    (frozenset({"vorraum"}), "🚪"),
    (frozenset({"schlafzimmer", "schlaf", "bett"}), "🛏️"),
    (frozenset({"blinken", "blink"}), "✨"),
]



def _prettify_scene_name(raw: str) -> str:
    """HA-Szenen ohne eigenen 'friendly_name' liefern nur den rohen
    Objekt-Teil der entity_id (z.B. 'schlafzimmer_vor_blinken') - das wirkt
    im UI wie ein Debug-Wert statt einem Szenennamen. Nur bei so einem
    Rohnamen (Unterstriche, keine Leerzeichen) in eine lesbare Form bringen;
    echte, bereits in HA gepflegte Namen (z.B. 'Alles hell', 'chill')
    bleiben unveraendert."""
    text = str(raw or "").strip()
    if text.lower().startswith("scene."):
        text = text.split(".", 1)[1]
    if "_" in text and " " not in text:
        words = [w for w in text.split("_") if w]
        text = " ".join(w if w.isupper() else w.capitalize() for w in words)
    return text or str(raw or "")


def _scene_icon(raw: str) -> str:
    text = str(raw or "").strip()
    if text.lower().startswith("scene."):
        text = text.split(".", 1)[1]
    tokens = set(text.lower().replace("-", "_").split("_")) | set(text.lower().split())
    for keywords, icon in _SCENE_ICONS:
        if tokens & keywords:
            return icon
    return "🎬"


class _HomeAssistantBridgeAdapter:
    """Adapter that mimics the small subset of `phue.Bridge` used by the app.

    Both `src/ui/app.py` (header switch sync) and `src/tabs/healthcheck.py`
    call `bridge.get_group(0)` and expect `{"state": {"any_on": bool}}`.
    """

    def __init__(self, client: HomeAssistantClient, master_entity_id: Optional[str]):
        self._client = client
        self._master_entity_id = master_entity_id
        self._cache_any_on: bool = False
        self._cache_ts: float = 0.0

    def set_cached_any_on(self, value: bool) -> None:
        try:
            self._cache_any_on = bool(value)
            self._cache_ts = time.monotonic()
        except Exception:
            pass

    def get_group(self, group_id: int) -> Dict[str, Any]:
        if int(group_id) != 0:
            return {"state": {"any_on": False}}
        try:
            # Small cache to keep UI snappy (app polls every ~7s).
            now = time.monotonic()
            if (now - self._cache_ts) < 3.0:
                return {"state": {"any_on": bool(self._cache_any_on)}}

            # Switch semantics (user spec): ON if *any* light is on; OFF only if *all* lights are off.
            # Therefore we compute 'any_on' across all light.* states (master_entity_id is not sufficient).
            any_on = bool(self._client.any_lights_on(None))
            self._cache_any_on = any_on
            self._cache_ts = now
            return {"state": {"any_on": any_on}}
        except Exception:
            return {"state": {"any_on": False}}



class HueTab(UiQueuePumpMixin):
    """Licht-Tab (Name bleibt HueTab fuer app.py)."""

    def __init__(self, root, notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True

        self._bridge_lock = threading.Lock()
        self._ha_client: Optional[HomeAssistantClient] = None
        self._ha_cfg = None
        self.bridge = None

        self.status_var = tk.StringVar(value="⚠️ Home Assistant: nicht konfiguriert")
        self.last_refresh_var = tk.StringVar(value="")
        self._vorraum_status_var = tk.StringVar(value="Vorraum: –")
        self._excluded: set = set()
        self._vorraum_status_entity_resolved: Optional[str] = None

        self._scenes: List[Dict[str, str]] = []
        self._states: List[Dict[str, Any]] = []
        self._areas: Dict[str, Dict[str, str]] = {"lights": {}, "scenes": {}}
        self._areas_ts = 0.0
        self._lights: List[L.LightInfo] = []
        self._scene_meta: Dict[str, Dict[str, Any]] = {}   # entity_id -> {config_id, color}
        self._room_widgets: Dict[str, Dict[str, Any]] = {}
        self._rendered_key = None
        self._portrait_layout = False

        self.prefs = L.load_prefs()
        self.prefs.setdefault("favorites", [])
        self._view = self.prefs.get("view", "Szenen")
        self._mood_target = "Alle"
        self._wakeup_minutes = int(self.prefs.get("wakeup_minutes", 20))
        self._routines: Dict[str, L.Routine] = {}

        self._init_ui_queue()
        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
            notebook.add(self.tab_frame, text=emoji("💡 Licht", "Licht"))

        self._build_ui()
        self._start_ui_pump()
        self._init_homeassistant()
        self._poll()
        self._refresh_vorraum_status_async()
        self._schedule_vorraum_poll()
        self.root.after(5000, self._auto_tick)

    # ------------------------------------------------------------------ UI --

    def _build_ui(self) -> None:
        self._shell = TabShell(self.tab_frame, "Licht", "Szenen, Räume und Abläufe")
        self._shell.pack(fill=tk.BOTH, expand=True)
        self._shell.subtitle_label.configure(textvariable=self.status_var)
        body = self._shell.body
        # TabShell gibt Zeile 0 Gewicht 1 - hier muss nur der Inhalt (Zeile 2)
        # wachsen, sonst entstehen grosse Luecken um die "Alle Lichter"-Karte.
        body.grid_rowconfigure(0, weight=0)
        body.grid_rowconfigure(2, weight=1)
        body.grid_columnconfigure(0, weight=1)

        # --- "Alle Lichter"
        top = ctk.CTkFrame(body, fg_color=COLOR_CARD, corner_radius=18, border_width=1, border_color=COLOR_BORDER)
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=(4, 8))
        top.grid_columnconfigure(1, weight=3)
        top.grid_columnconfigure(2, weight=2)
        head = ctk.CTkFrame(top, fg_color="transparent")
        head.grid(row=0, column=0, sticky="w", padx=(14, 8), pady=(10, 4))
        ctk.CTkLabel(head, text="💡 Alle Lichter", text_color=COLOR_TEXT,
                     font=get_safe_font("Bahnschrift", 15, "bold")).pack(anchor="w")
        self._all_summary = ctk.CTkLabel(head, text="--", text_color=COLOR_SUBTEXT,
                                         font=get_safe_font("Bahnschrift", 12))
        self._all_summary.pack(anchor="w")
        self.all_bri = TouchSlider(top, from_=0, to=100, value=0, height=52, fill_color=COLOR_WARNING,
                                   snap_points=(10, 25, 50, 75, 100), formatter=_bri_fmt,
                                   on_change=lambda v: self._set_brightness(None, v, final=False),
                                   on_release=lambda v: self._set_brightness(None, v, final=True))
        self.all_bri.grid(row=0, column=1, sticky="ew", padx=(0, 10), pady=(10, 4))
        self.all_k = TouchSlider(top, from_=2200, to=6500, value=3000, height=52, gradient=KELVIN_GRADIENT,
                                 formatter=_k_fmt,
                                 on_change=lambda v: self._set_kelvin(None, v),
                                 on_release=lambda v: self._set_kelvin(None, v))
        self.all_k.grid(row=0, column=2, sticky="ew", padx=(0, 14), pady=(10, 4))

        btns = ctk.CTkFrame(top, fg_color="transparent")
        btns.grid(row=1, column=0, columnspan=3, sticky="ew", padx=10, pady=(2, 10))
        self._btn_all_off = self._pill_button(btns, "⏻  Alles aus", lambda: self._all_off(), COLOR_DANGER)
        self._btn_all_off.pack(side="left", padx=4)
        self._btn_auto = self._pill_button(btns, "☀  Auto", self._toggle_auto, COLOR_BORDER)
        self._btn_auto.pack(side="left", padx=4)
        # Vorraum: nur Statusanzeige (geschaltet wird nur vom Bewegungsmelder)
        self._vorraum_pill = ctk.CTkLabel(btns, textvariable=self._vorraum_status_var, height=40, corner_radius=20,
                                          fg_color=COLOR_ROOT, text_color=COLOR_SUBTEXT, padx=14,
                                          font=get_safe_font("Bahnschrift", 13, "bold"))
        self._vorraum_pill.pack(side="left", padx=(10, 4))
        self._pill_button(btns, "↻", self._refresh_all_async, COLOR_BORDER, width=48).pack(side="right", padx=4)
        self._update_auto_button()

        # --- Ansichts-Umschalter
        seg = ctk.CTkFrame(body, fg_color="transparent")
        seg.grid(row=1, column=0, sticky="ew", padx=4, pady=(0, 6))
        self._view_buttons = {}
        for name, icon in (("Szenen", "🎨"), ("Räume", "🏠"), ("Abläufe", "⏱")):
            b = ctk.CTkButton(seg, text=f"{icon}  {name}", height=42, corner_radius=14,
                              font=get_safe_font("Bahnschrift", 14, "bold"),
                              command=lambda n=name: self._set_view(n))
            b.pack(side="left", fill="x", expand=True, padx=3)
            self._view_buttons[name] = b

        # --- scrollbarer Inhalt
        frame = tk.Frame(body, bg=COLOR_ROOT)
        frame.grid(row=2, column=0, sticky="nsew", padx=4)
        self._scroll_canvas = tk.Canvas(frame, bg=COLOR_ROOT, highlightthickness=0)
        self._scroll_canvas.pack(side="left", fill="both", expand=True)
        self._content = ctk.CTkFrame(self._scroll_canvas, fg_color=COLOR_ROOT, corner_radius=0)
        self._content_id = self._scroll_canvas.create_window((0, 0), window=self._content, anchor="nw")
        self._content.bind("<Configure>", lambda _e: self._scroll_canvas.configure(
            scrollregion=self._scroll_canvas.bbox("all")))
        self._scroll_canvas.bind("<Configure>", lambda e: self._scroll_canvas.itemconfigure(
            self._content_id, width=e.width))
        # Wischen zum Scrollen (Touch) statt Scrollbar
        self._drag_y = None
        self._scroll_canvas.bind_all("<MouseWheel>", self._on_wheel, add="+")
        self._update_view_buttons()

    def _pill_button(self, parent, text, command, color, width=120):
        return ctk.CTkButton(parent, text=text, command=command, width=width, height=40, corner_radius=20,
                             fg_color=COLOR_ROOT, hover_color=COLOR_BORDER, border_width=2, border_color=color,
                             text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 13, "bold"))

    def _on_wheel(self, e) -> None:
        try:
            if self._scroll_canvas.winfo_ismapped():
                self._scroll_canvas.yview_scroll(int(-e.delta / 120), "units")
        except Exception:
            pass

    def _bind_drag_scroll(self, widget) -> None:
        """Ziehen auf Hintergrund/Karten scrollt (nicht auf Slidern)."""
        def press(e):
            self._drag_y = e.y_root
            self._drag_moved = False

        def motion(e):
            if self._drag_y is None:
                return
            dy = e.y_root - self._drag_y
            if abs(dy) > 8:
                self._drag_moved = True
                self._scroll_canvas.yview_scroll(int(-dy / 8), "units")
                self._drag_y = e.y_root

        def release(_e):
            self._drag_y = None
        for w in [widget] + list(self._descendants(widget)):
            if isinstance(w, TouchSlider):
                continue
            w.bind("<ButtonPress-1>", press, add="+")
            w.bind("<B1-Motion>", motion, add="+")
            w.bind("<ButtonRelease-1>", release, add="+")

    @staticmethod
    def _descendants(w):
        for c in w.winfo_children():
            yield c
            yield from HueTab._descendants(c)

    def set_portrait_layout(self, portrait: bool) -> None:
        try:
            self._shell.set_portrait_layout(portrait)
        except Exception:
            pass
        if self._portrait_layout != portrait:
            self._portrait_layout = portrait
            self._rendered_key = None
            self._render()

    def _set_view(self, name: str) -> None:
        self._view = name
        self.prefs["view"] = name
        L.save_prefs(self.prefs)
        self._update_view_buttons()
        self._rendered_key = None
        self._render()
        self._scroll_canvas.yview_moveto(0)

    def _update_view_buttons(self) -> None:
        for n, b in self._view_buttons.items():
            act = n == self._view
            b.configure(fg_color=COLOR_PRIMARY if act else COLOR_CARD, text_color="#ffffff" if act else COLOR_TEXT,
                        hover_color=COLOR_PRIMARY if act else COLOR_BORDER)

    # -------------------------------------------------------------- Daten --

    def _poll(self) -> None:
        if not self.alive:
            return
        self._refresh_state_async()
        self.root.after(POLL_MS, self._poll)

    def _refresh_all_async(self) -> None:
        self._areas_ts = 0.0
        self._scene_meta.clear()
        self._refresh_state_async()

    def _refresh_state_async(self, delay_s: float = 0.0) -> None:
        def worker() -> None:
            if delay_s:
                time.sleep(delay_s)
            client = self._ha_client
            if not client:
                return
            try:
                states = client.get_states()
            except Exception:
                self._post_ui(lambda: self.status_var.set("⚠️ Home Assistant nicht erreichbar"))
                return
            areas = self._areas
            if time.time() - self._areas_ts > AREA_REFRESH_S:
                areas = client.get_areas()
                self._areas_ts = time.time()
            # Farb-Vorschau fuer Szenen aus dem HA-Editor (einmalig je Szene)
            meta = dict(self._scene_meta)
            for st in states:
                ent = str(st.get("entity_id") or "")
                if not ent.startswith("scene.") or ent in meta:
                    continue
                cid = (st.get("attributes") or {}).get("id")
                color = None
                cfg = None
                if cid:
                    try:
                        cfg = client.get_scene_config(str(cid)) or {}
                        color = L.scene_preview_color(cfg.get("entities") or {})
                    except Exception:
                        color = None
                meta[ent] = {"config_id": str(cid) if cid else None, "color": color,
                             "entities": dict((cfg or {}).get("entities") or {}) if cid else {}}
            vor_ent = str(getattr(self._ha_cfg, "vorraum_status_entity_id", "") or "").strip()
            excluded = L.excluded_entities(states, areas.get("lights"), {vor_ent} if vor_ent else None)
            scenes = []
            allow = set(self._ha_cfg.scene_entity_ids or []) if self._ha_cfg else set()
            scene_areas = areas.get("scenes") or {}
            for st in states:
                ent = str(st.get("entity_id") or "")
                if ent.startswith("scene.") and (not allow or ent in allow):
                    name = (st.get("attributes") or {}).get("friendly_name") or ent
                    if L.scene_is_excluded(ent, str(name), scene_areas.get(ent)):
                        continue
                    scenes.append({"entity_id": ent, "name": str(name)})
            scenes.sort(key=lambda s: s["name"].lower())
            only = list(self._ha_cfg.dim_entity_ids or []) if self._ha_cfg and self._ha_cfg.dim_entity_ids else None
            lights = [li for li in L.collect_lights(states, areas.get("lights"), only)
                      if li.entity_id not in excluded]

            def apply() -> None:
                if not self.alive:
                    return
                self._states, self._areas, self._scene_meta = states, areas, meta
                self._scenes, self._lights = scenes, lights
                self._excluded = excluded
                self.last_refresh_var.set(datetime.now().strftime("%H:%M:%S"))
                if self.status_var.get().startswith(("⚠️ Home Assistant nicht", "🔌")):
                    self.status_var.set(f"✅ {len(lights)} Lichter · {len(scenes)} Szenen")
                self._update_live()
                self._render()

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _update_live(self) -> None:
        s = L.summarize(self._lights)
        self._all_summary.configure(text=s.text)
        self.all_bri.set_value(s.brightness_pct if s.on_count else 0)
        if s.supports_ct:
            self.all_k.from_, self.all_k.to = s.min_kelvin, s.max_kelvin
            self.all_k.set_enabled(True)
            if s.kelvin:
                self.all_k.set_value(s.kelvin)
        else:
            self.all_k.set_enabled(False)
        for room, w in self._room_widgets.items():
            members = [li for li in self._lights if li.area == room]
            if not members:
                continue
            rs = L.summarize(members)
            w["summary"].configure(text=rs.text)
            w["bri"].set_value(rs.brightness_pct if rs.on_count else 0)
            if w.get("k") is not None and rs.kelvin:
                w["k"].set_value(rs.kelvin)
            sw = w["switch"]
            w["ignore"] = True
            (sw.select() if rs.on_count else sw.deselect())
            w["ignore"] = False
            for li in members:
                chip = w["chips"].get(li.entity_id)
                if chip is not None:
                    chip.configure(fg_color=COLOR_WARNING if li.on else COLOR_ROOT,
                                   text_color="#111317" if li.on else COLOR_TEXT)

    # ---------------------------------------------------------- Befehle --

    def _targets(self, room: Optional[str]) -> List[L.LightInfo]:
        return [li for li in self._lights if room is None or li.area == room]

    def _run(self, fn, refresh: float = 0.8) -> None:
        def worker():
            ok = False
            try:
                ok = bool(fn())
            except Exception:
                ok = False
            if not ok:
                self._post_ui(lambda: self.status_var.set("⚠️ Befehl an Home Assistant fehlgeschlagen"))
            self._refresh_state_async(delay_s=refresh)
        threading.Thread(target=worker, daemon=True).start()

    def _set_brightness(self, room: Optional[str], value: float, final: bool) -> None:
        client = self._ha_client
        targets = self._targets(room)
        if not client or not targets:
            return
        ids = [li.entity_id for li in targets]
        pct = int(round(value))

        def do():
            if pct < 1:
                return client.call_service("light", "turn_off", {"entity_id": ids})
            dimmable = [li.entity_id for li in targets if li.supports_brightness]
            other = [e for e in ids if e not in dimmable]
            ok = True
            if dimmable:
                ok = client.light_turn_on(dimmable, brightness_pct=pct, transition=0.4)
            if other:
                ok = client.call_service("light", "turn_on", {"entity_id": other}) and ok
            return ok

        ceiling, want_on = self._ceiling_target(room, pct)

        def do_with_ceiling():
            ok = do()
            # Deckenlampe (Schalter) erst beim Loslassen und nur, wenn sich
            # ihr Zustand wirklich aendern muss - vorher wurde sie bei jedem
            # Loslassen geschaltet, auch wenn sie schon so war (kurzes Ein/Aus).
            if ceiling and want_on is not None:
                client.call_service("homeassistant", "turn_on" if want_on else "turn_off", {"entity_id": ceiling})
            return ok

        self._run(do_with_ceiling if final else do, refresh=1.5 if final else 3.0)

    def _ceiling_target(self, room: Optional[str], pct: int) -> tuple[Optional[str], Optional[bool]]:
        """Deckenlampe Schlafzimmer: ab CEILING_ON_PCT % an, darunter aus.

        Gilt fuer den "Alle Lichter"-Dimmer und den Dimmer des Raums, in dem
        die Deckenlampe haengt. Gibt (entity, gewuenschter Zustand) zurueck,
        Zustand None = keine Aenderung noetig."""
        cfg = self._ha_cfg
        ceiling = str(getattr(cfg, "ceiling_entity_id", "") or "").strip() if cfg else ""
        if not ceiling or ceiling in self._excluded:
            return None, None
        if room is not None and room.lower().replace(" ", "_") not in ceiling.lower():
            return None, None
        thr = int(getattr(cfg, "ceiling_threshold_pct", 0) or CEILING_ON_PCT)
        want = pct >= thr
        cur = next((str(s_.get("state") or "") for s_ in self._states if s_.get("entity_id") == ceiling), "")
        if cur in ("on", "off") and (cur == "on") == want:
            return ceiling, None
        return ceiling, want

    def _set_kelvin(self, room: Optional[str], value: float) -> None:
        client = self._ha_client
        targets = [li for li in self._targets(room) if li.supports_ct and li.on]
        if not client or not targets:
            return
        k = L.clamp_kelvin(int(value), targets)
        self._run(lambda: client.light_turn_on([li.entity_id for li in targets], color_temp_kelvin=k,
                                               transition=0.4), refresh=2.0)

    def _toggle_room(self, room: str, on: bool) -> None:
        client = self._ha_client
        ids = [li.entity_id for li in self._targets(room)]
        if client and ids:
            self._run(lambda: client.call_service("light", "turn_on" if on else "turn_off", {"entity_id": ids}))

    def _toggle_light(self, entity_id: str) -> None:
        if self._ha_client:
            self._run(lambda: self._ha_client.call_service("light", "toggle", {"entity_id": entity_id}))

    def _all_off(self) -> None:
        self._stop_routine("party")
        self._stop_routine("wakeup")
        client = self._ha_client
        cfg = self._ha_cfg
        if not client:
            return
        if cfg and cfg.scene_all_off:
            self._run(lambda: self._apply_scene(cfg.scene_all_off))
        else:
            ids = [li.entity_id for li in self._lights]
            self._run(lambda: client.call_service("light", "turn_off", {"entity_id": ids}))

    def _apply_mood(self, mood: L.Mood) -> None:
        client = self._ha_client
        room = None if self._mood_target == "Alle" else self._mood_target
        targets = self._targets(room)
        if not client or not targets:
            return
        self._stop_routine("party")
        if mood.party:
            self._start_routine(L.Party(client, targets, on_done=self._routine_done))
            self.status_var.set("🎉 Party läuft – „Alles aus“ oder Abläufe → Stopp")
            return

        def do():
            ok = True
            for ids, data in L.mood_commands(mood, targets):
                ok = client.light_turn_on(ids, **data) and ok
            return ok
        self._run(do)
        self.status_var.set(f"{mood.icon} {mood.name}" + (f" · {room}" if room else ""))

    def _activate_scene_async(self, entity_id: str) -> None:
        entity_id = str(entity_id or "").strip()
        if not entity_id or not self._ha_client:
            return
        self._stop_routine("party")
        name = next((s["name"] for s in self._scenes if s["entity_id"] == entity_id), entity_id)
        self.status_var.set(f"✅ {_prettify_scene_name(name)}")
        self._run(lambda: self._apply_scene(entity_id))

    def _apply_scene(self, entity_id: str) -> bool:
        """Szene aktivieren - ohne ausgenommene Lichter (Vorraum).

        Enthaelt die im HA-Editor gespeicherte Szene ein Vorraum-Licht, wird
        sie per scene.apply ohne diese Entitaeten gesetzt; sonst normal."""
        client = self._ha_client
        if not client:
            return False
        ents = (self._scene_meta.get(entity_id) or {}).get("entities") or {}
        filtered = L.filter_scene_entities(ents, self._excluded)
        if filtered is None:
            return bool(client.activate_scene(entity_id))
        if not filtered:
            return True
        return bool(client.call_service("scene", "apply", {"entities": filtered}))

    # ------------------------------------------------------- Szenen-Edit --

    def _ask_save_scene(self) -> None:
        if not self._lights:
            return
        top = self.root.winfo_toplevel()
        OnscreenKeyboard.ask(top, "Aktuelles Licht als Szene speichern – Name:", self._save_scene)

    def _save_scene(self, name: Optional[str]) -> None:
        if not name or not self._ha_client:
            return
        client = self._ha_client
        ids = [li.entity_id for li in self._lights]
        existing = {m.get("config_id") for m in self._scene_meta.values() if m.get("config_id")}

        def do():
            states = client.get_states()
            entities = L.snapshot_entities(states, ids)
            sid = L.scene_config_id(name, existing)
            try:
                return client.save_scene_config(sid, name, entities)
            except Exception:
                # Fallback (z.B. Token ohne Admin-Rechte): nur bis zum HA-Neustart
                return client.call_service("scene", "create", {"scene_id": sid, "snapshot_entities": ids})
        self.status_var.set(f"💾 Szene „{name}“ wird gespeichert …")
        self._scene_meta.clear()
        self._run(do, refresh=2.0)

    def _scene_menu(self, entity_id: str, name: str) -> None:
        """Long-Press auf eine Szene: Favorit / Loeschen."""
        top = self.root.winfo_toplevel()
        overlay = ctk.CTkFrame(top, fg_color=COLOR_ROOT, corner_radius=0)
        overlay.place(relx=0, rely=0, relwidth=1, relheight=1)
        box = ctk.CTkFrame(overlay, fg_color=COLOR_CARD, corner_radius=18, border_width=1, border_color=COLOR_BORDER)
        box.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(box, text=name, text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 18, "bold")).pack(
            padx=30, pady=(18, 10))
        fav = entity_id in self.prefs["favorites"]

        def close():
            overlay.destroy()

        def toggle_fav():
            favs = self.prefs["favorites"]
            (favs.remove(entity_id) if entity_id in favs else favs.append(entity_id))
            L.save_prefs(self.prefs)
            close()
            self._rendered_key = None
            self._render()

        def delete():
            close()
            cid = (self._scene_meta.get(entity_id) or {}).get("config_id")
            if cid and self._ha_client:
                self._scene_meta.pop(entity_id, None)
                self._run(lambda: self._ha_client.delete_scene_config(cid), refresh=2.0)
                self.status_var.set(f"🗑 Szene „{name}“ gelöscht")

        def btn(text, cmd, color=COLOR_BORDER):
            ctk.CTkButton(box, text=text, command=cmd, width=260, height=48, corner_radius=14, fg_color=color,
                          text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 15, "bold")).pack(padx=24, pady=5)
        btn("☆  Aus Favoriten entfernen" if fav else "★  Zu Favoriten", toggle_fav)
        cid = (self._scene_meta.get(entity_id) or {}).get("config_id") or ""
        if cid.startswith("dashboard_"):
            btn("🗑  Szene löschen", delete, COLOR_DANGER)
        btn("Abbrechen", close)
        ctk.CTkFrame(box, fg_color="transparent", height=10).pack()

    # ------------------------------------------------------------ Ablaeufe --

    def _start_routine(self, routine: L.Routine) -> None:
        self._stop_routine(routine.name)
        self._routines[routine.name] = routine
        routine.start()
        self._rendered_key = None
        self._render()

    def _stop_routine(self, name: str) -> None:
        r = self._routines.get(name)
        if r and r.running:
            r.stop()

    def _routine_done(self, name: str) -> None:
        def apply():
            self._rendered_key = None
            self._render()
            self._refresh_state_async()
        self._post_ui(apply)

    def _start_wakeup(self) -> None:
        if not self._ha_client:
            return
        room = None if self._mood_target == "Alle" else self._mood_target
        self._start_routine(L.WakeUp(self._ha_client, self._targets(room), self._wakeup_minutes,
                                     on_done=self._routine_done))
        self.status_var.set(f"🌅 Aufwachen läuft ({self._wakeup_minutes} min)")

    def _start_goodnight(self) -> None:
        if not self._ha_client:
            return
        self._stop_routine("party")
        self._stop_routine("wakeup")
        self._start_routine(L.GoodNight(self._ha_client, [li.entity_id for li in self._lights], None,
                                        on_done=self._routine_done))
        self.status_var.set("😴 Gute Nacht")

    def _toggle_auto(self) -> None:
        self.prefs["auto"] = not bool(self.prefs.get("auto"))
        L.save_prefs(self.prefs)
        self._update_auto_button()
        if self.prefs["auto"]:
            self._auto_apply()
        self._rendered_key = None
        self._render()

    def _update_auto_button(self) -> None:
        on = bool(self.prefs.get("auto"))
        self._btn_auto.configure(fg_color=COLOR_WARNING if on else COLOR_ROOT,
                                 text_color="#111317" if on else COLOR_TEXT,
                                 border_color=COLOR_WARNING if on else COLOR_BORDER)

    def _auto_target_kelvin(self) -> int:
        from core.heating_stats import sun_elevation_deg
        try:
            from core.weather import load_weather_config
            w = load_weather_config()
            lat, lon = w.latitude, w.longitude
        except Exception:
            lat, lon = 48.2569, 13.0397
        return L.circadian_kelvin(sun_elevation_deg(datetime.now(), lat, lon))

    def _auto_apply(self) -> None:
        client = self._ha_client
        if not client or any(r.running for r in self._routines.values()):
            return
        k = self._auto_target_kelvin()
        targets = [li for li in self._lights if li.on and li.supports_ct
                   and (li.kelvin is None or abs(li.kelvin - k) > 150)]
        if targets:
            kk = L.clamp_kelvin(k, targets)
            self._run(lambda: client.light_turn_on([li.entity_id for li in targets], color_temp_kelvin=kk,
                                                   transition=30), refresh=2.0)

    def _auto_tick(self) -> None:
        if not self.alive:
            return
        if self.prefs.get("auto"):
            self._auto_apply()
        self.root.after(AUTO_TICK_MS, self._auto_tick)

    # ------------------------------------------------------------ Rendern --

    def _render(self) -> None:
        key = (self._view, self._portrait_layout, tuple(s["entity_id"] for s in self._scenes),
               tuple((li.entity_id, li.area, li.supports_ct) for li in self._lights),
               tuple(sorted(self.prefs.get("favorites", []))), self._mood_target,
               tuple((k, v.get("color")) for k, v in sorted(self._scene_meta.items())),
               tuple(sorted(n for n, r in self._routines.items() if r.running)), bool(self.prefs.get("auto")),
               self._wakeup_minutes)
        if key == self._rendered_key:
            return
        self._rendered_key = key
        for c in list(self._content.winfo_children()):
            c.destroy()
        self._room_widgets = {}
        if self._view == "Räume":
            self._render_rooms()
        elif self._view == "Abläufe":
            self._render_routines()
        else:
            self._render_scenes()
        self._update_live()
        self._bind_drag_scroll(self._content)

    def _section(self, title: str):
        ctk.CTkLabel(self._content, text=title.upper(), text_color=COLOR_SUBTEXT, anchor="w",
                     font=get_safe_font("Bahnschrift", 12, "bold")).pack(fill="x", padx=8, pady=(10, 2))
        grid = ctk.CTkFrame(self._content, fg_color="transparent")
        grid.pack(fill="x", padx=2)
        return grid

    TILE_H = 76

    @staticmethod
    def _round_rect(cv, x0, y0, x1, y1, r, **kw):
        pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1,
               x0 + r, y1, x0, y1, x0, y1 - r, x0, y0 + r, x0, y0]
        return cv.create_polygon(pts, smooth=True, splinesteps=12, **kw)

    @staticmethod
    def _draw_power(cv, cx, cy, r, color):
        cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=120, extent=300, style="arc", outline=color, width=3)
        cv.create_line(cx, cy - r - 2, cx, cy - 1, fill=color, width=3, capstyle="round")

    def _tile(self, parent, idx, cols, icon, name, color, command, long_press=None, star=False, dashed=False):
        """Kachel als EIN tk.Canvas (statt CTkFrame + 2 CTkLabels).

        Auf dem Pi blieben bei den verschachtelten CTk-Widgets die Inhalte der
        rechten Spalte schwarz (nicht neu gezeichnet). Eine einzige Zeichen-
        flaeche zeichnet sich bei jeder Groessenaenderung komplett neu und
        ist ausserdem deutlich schneller (3 statt ~8 Tk-Fenster je Kachel)."""
        r, c = divmod(idx, cols)
        for cc in range(cols):  # alle Spalten gleich breit, auch wenn die Zeile nicht voll ist
            parent.grid_columnconfigure(cc, weight=1, uniform="tile")
        h = self.TILE_H
        cv = tk.Canvas(parent, height=h, bg=COLOR_ROOT, highlightthickness=0, bd=0, cursor="hand2")
        cv.grid(row=r, column=c, sticky="ew", padx=5, pady=5)
        accent = color or COLOR_BORDER
        state = {"border": accent, "pressed": False}
        icon_font = get_safe_font("Segoe UI Emoji", 20)
        text_font = get_safe_font("Bahnschrift", 15, "bold")
        label = name + ("  ★" if star else "")

        def draw(_e=None):
            cv.delete("all")
            w = cv.winfo_width()
            if w < 40:
                return
            fill = COLOR_BORDER if state["pressed"] else COLOR_CARD
            self._round_rect(cv, 2, 2, w - 3, h - 3, 18, fill=fill, outline=state["border"], width=2,
                             dash=(6, 4) if dashed else None)
            cx, cy, rr = 38, h / 2, 23
            cv.create_oval(cx - rr, cy - rr, cx + rr, cy + rr, fill=color or COLOR_ROOT, outline="")
            if icon == "power":
                self._draw_power(cv, cx, cy, 11, COLOR_TEXT)
            else:
                cv.create_text(cx, cy, text=icon, font=icon_font, fill=COLOR_TEXT)
            cv.create_text(cx + rr + 14, cy, text=label, anchor="w", fill=COLOR_TEXT, font=text_font,
                           width=max(40, w - (cx + rr + 26)))

        cv.bind("<Configure>", draw)

        def press(_e):
            state["pressed"] = True
            draw()

        def release(_e):
            state["pressed"] = False
            draw()
            self._tile_click(command)
        cv.bind("<ButtonPress-1>", press, add="+")
        cv.bind("<ButtonRelease-1>", release, add="+")
        cv.bind("<Leave>", lambda _e: (state.update(pressed=False), draw()), add="+")

        if long_press:
            class _Feedback:  # bind_long_press setzt border_color als "gedrueckt"-Anzeige
                @staticmethod
                def configure(border_color=None, **_kw):
                    if border_color:
                        state["border"] = border_color
                        draw()

            def fire():
                self._suppress_click_until = time.monotonic() + 0.8
                long_press()
            bind_long_press(cv, fire, feedback_widget=_Feedback, press_color=COLOR_PRIMARY,
                            release_color=accent)
        return cv

    _suppress_click_until = 0.0
    _drag_moved = False

    def _tile_click(self, command) -> None:
        # Kein Ausloesen nach Long-Press oder wenn eigentlich gescrollt wurde
        if time.monotonic() < self._suppress_click_until or self._drag_moved:
            return
        command()

    def _cols(self) -> int:
        return 2 if self._portrait_layout else 3

    def _render_scenes(self) -> None:
        cols = self._cols()
        if not self._ha_client:
            ctk.CTkLabel(self._content, text="Home Assistant ist nicht konfiguriert (config/homeassistant.json).",
                         text_color=COLOR_SUBTEXT).pack(pady=20)
            return
        by_ent = {s["entity_id"]: s for s in self._scenes}
        favs = [e for e in self.prefs.get("favorites", []) if e in by_ent]

        def scene_tile(grid, i, ent):
            s = by_ent[ent]
            name = _prettify_scene_name(s["name"])
            color = (self._scene_meta.get(ent) or {}).get("color")
            self._tile(grid, i, cols, _scene_icon(s["name"]), name, color,
                       lambda e=ent: self._activate_scene_async(e),
                       long_press=lambda e=ent, n=name: self._scene_menu(e, n), star=ent in favs)

        if favs:
            g = self._section("★ Favoriten")
            for i, ent in enumerate(favs):
                scene_tile(g, i, ent)

        # Stimmungen + Ziel (alle / Raum)
        rooms = list(L.group_by_room(self._lights).keys())
        head = ctk.CTkFrame(self._content, fg_color="transparent")
        head.pack(fill="x", padx=8, pady=(12, 2))
        ctk.CTkLabel(head, text="STIMMUNGEN", text_color=COLOR_SUBTEXT,
                     font=get_safe_font("Bahnschrift", 12, "bold")).pack(side="left")
        if len(rooms) > 1:
            ctk.CTkOptionMenu(head, values=["Alle"] + rooms, width=170, height=32,
                              variable=tk.StringVar(value=self._mood_target),
                              command=self._set_mood_target, fg_color=COLOR_CARD, button_color=COLOR_BORDER,
                              font=get_safe_font("Bahnschrift", 13)).pack(side="right")
            ctk.CTkLabel(head, text="für", text_color=COLOR_SUBTEXT,
                         font=get_safe_font("Bahnschrift", 12)).pack(side="right", padx=6)
        g = ctk.CTkFrame(self._content, fg_color="transparent")
        g.pack(fill="x", padx=2)
        for i, m in enumerate(L.MOODS):
            self._tile(g, i, cols, m.icon, m.name, m.preview, lambda mm=m: self._apply_mood(mm))

        # HA-Szenen nach Bereich
        scene_areas = self._areas.get("scenes") or {}
        groups: Dict[str, List[str]] = {}
        for s in self._scenes:
            groups.setdefault(scene_areas.get(s["entity_id"], "Szenen"), []).append(s["entity_id"])
        order = sorted(groups, key=lambda a: (a == "Szenen", a.lower()))
        for area in order:
            g = self._section(area)
            for i, ent in enumerate(groups[area]):
                scene_tile(g, i, ent)
        if not self._scenes:
            ctk.CTkLabel(self._content, text="Keine Szenen in Home Assistant gefunden.",
                         text_color=COLOR_SUBTEXT).pack(anchor="w", padx=10)

        g = self._section("Eigene Szene")
        self._tile(g, 0, cols, "＋", "Aktuelles Licht als Szene speichern", None, self._ask_save_scene,
                   dashed=True)
        ctk.CTkLabel(self._content, text="Tipp: Szene lange drücken → Favorit / löschen",
                     text_color=COLOR_SUBTEXT, font=get_safe_font("Bahnschrift", 11)).pack(anchor="w", padx=10,
                                                                                            pady=(4, 10))

    def _set_mood_target(self, value: str) -> None:
        self._mood_target = value
        self._rendered_key = None
        self.root.after(50, self._render)

    def _render_rooms(self) -> None:
        rooms = L.group_by_room(self._lights)
        if not rooms:
            ctk.CTkLabel(self._content, text="Keine Lichter gefunden.", text_color=COLOR_SUBTEXT).pack(pady=20)
            return
        cols = 1 if self._portrait_layout else 2
        grid = ctk.CTkFrame(self._content, fg_color="transparent")
        grid.pack(fill="x", padx=2, pady=(4, 8))
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1, uniform="room")
        for idx, (room, members) in enumerate(rooms.items()):
            r, c = divmod(idx, cols)
            card = ctk.CTkFrame(grid, fg_color=COLOR_CARD, corner_radius=16, border_width=1, border_color=COLOR_BORDER)
            card.grid(row=r, column=c, sticky="nsew", padx=5, pady=5)
            card.grid_columnconfigure(0, weight=1)
            head = ctk.CTkFrame(card, fg_color="transparent")
            head.grid(row=0, column=0, sticky="ew", padx=12, pady=(10, 2))
            ctk.CTkLabel(head, text=room, text_color=COLOR_TEXT,
                         font=get_safe_font("Bahnschrift", 15, "bold")).pack(side="left")
            w: Dict[str, Any] = {"chips": {}, "ignore": False}
            sw = ctk.CTkSwitch(head, text="", width=56, height=28, switch_width=56, switch_height=28,
                               fg_color=COLOR_BORDER, progress_color=COLOR_WARNING, button_color=COLOR_TEXT,
                               command=lambda rm=room, ww=w: (None if ww["ignore"] else
                                                               self._toggle_room(rm, bool(ww["switch"].get()))))
            sw.pack(side="right")
            w["switch"] = sw
            w["summary"] = ctk.CTkLabel(head, text="", text_color=COLOR_SUBTEXT, font=get_safe_font("Bahnschrift", 12))
            w["summary"].pack(side="right", padx=8)
            w["bri"] = TouchSlider(card, value=0, height=46, fill_color=COLOR_WARNING, snap_points=(10, 25, 50, 75, 100),
                                   formatter=_bri_fmt,
                                   on_change=lambda v, rm=room: self._set_brightness(rm, v, final=False),
                                   on_release=lambda v, rm=room: self._set_brightness(rm, v, final=True))
            w["bri"].grid(row=1, column=0, sticky="ew", padx=10, pady=4)
            rs = L.summarize(members)
            w["k"] = None
            if rs.supports_ct:
                w["k"] = TouchSlider(card, from_=rs.min_kelvin, to=rs.max_kelvin, value=rs.kelvin or 3000, height=40,
                                     gradient=KELVIN_GRADIENT, formatter=_k_fmt,
                                     on_change=lambda v, rm=room: self._set_kelvin(rm, v),
                                     on_release=lambda v, rm=room: self._set_kelvin(rm, v))
                w["k"].grid(row=2, column=0, sticky="ew", padx=10, pady=4)
            if len(members) > 1:
                chips = ctk.CTkFrame(card, fg_color="transparent")
                chips.grid(row=3, column=0, sticky="ew", padx=8, pady=(2, 10))
                for li in members:
                    short = li.name.replace(room, "").strip(" -_") or li.name
                    b = ctk.CTkButton(chips, text=short, height=32, corner_radius=16, width=10,
                                      fg_color=COLOR_ROOT, border_width=1, border_color=COLOR_BORDER,
                                      text_color=COLOR_TEXT, font=get_safe_font("Bahnschrift", 12),
                                      command=lambda e=li.entity_id: self._toggle_light(e))
                    b.pack(side="left", padx=3, pady=2)
                    w["chips"][li.entity_id] = b
            else:
                ctk.CTkFrame(card, fg_color="transparent", height=6).grid(row=3, column=0)
            self._room_widgets[room] = w

    def _render_routines(self) -> None:
        def card(icon, title, text, running, start, stop=None, extra=None):
            c = ctk.CTkFrame(self._content, fg_color=COLOR_CARD, corner_radius=16, border_width=2,
                             border_color=COLOR_WARNING if running else COLOR_BORDER)
            c.pack(fill="x", padx=6, pady=5)
            c.grid_columnconfigure(1, weight=1)
            ctk.CTkLabel(c, text=icon, font=get_safe_font("Segoe UI Emoji", 26), width=50).grid(
                row=0, column=0, rowspan=2, padx=(12, 6), pady=10)
            ctk.CTkLabel(c, text=title, text_color=COLOR_TEXT, anchor="w",
                         font=get_safe_font("Bahnschrift", 15, "bold")).grid(row=0, column=1, sticky="w", pady=(10, 0))
            ctk.CTkLabel(c, text=text, text_color=COLOR_SUBTEXT, anchor="w", justify="left", wraplength=520,
                         font=get_safe_font("Bahnschrift", 12)).grid(row=1, column=1, sticky="w", pady=(0, 10))
            if extra:
                extra(c).grid(row=0, column=2, rowspan=2, padx=6)
            label = "■  Stopp" if running and stop else "▶  Start"
            ctk.CTkButton(c, text=label, width=110, height=44, corner_radius=14,
                          fg_color=COLOR_DANGER if running and stop else COLOR_PRIMARY, text_color="#ffffff",
                          font=get_safe_font("Bahnschrift", 14, "bold"),
                          command=(stop if running and stop else start)).grid(row=0, column=3, rowspan=2, padx=12)

        def minutes_picker(parent):
            f = ctk.CTkFrame(parent, fg_color="transparent")
            for m in (10, 20, 30):
                act = m == self._wakeup_minutes
                ctk.CTkButton(f, text=f"{m} min", width=62, height=34, corner_radius=12,
                              fg_color=COLOR_PRIMARY if act else COLOR_BORDER, text_color=COLOR_TEXT,
                              font=get_safe_font("Bahnschrift", 12, "bold"),
                              command=lambda mm=m: self._set_wakeup_minutes(mm)).pack(side="left", padx=2)
            return f

        wake = self._routines.get("wakeup")
        target = "alle Lichter" if self._mood_target == "Alle" else self._mood_target
        card("🌅", "Aufwachen", f"Sanfter Sonnenaufgang: {target} von 1 % sehr warm auf 100 % neutral.",
             bool(wake and wake.running), self._start_wakeup, lambda: self._stop_routine("wakeup"), minutes_picker)
        gn = self._routines.get("goodnight")
        card("😴", "Gute Nacht", "Alle Lichter sanft aus (Vorraum bleibt beim Bewegungsmelder).",
             bool(gn and gn.running), self._start_goodnight, lambda: self._stop_routine("goodnight"))
        party = self._routines.get("party")
        card("🎉", "Party", "Farbwechsel auf allen Farb-Lampen (Effekt der Lampe oder alle 4 s eine neue Farbe).",
             bool(party and party.running),
             lambda: self._apply_mood(next(m for m in L.MOODS if m.party)), lambda: self._stop_routine("party"))
        auto_on = bool(self.prefs.get("auto"))
        k = self._auto_target_kelvin()
        card("☀", "Auto-Lichtfarbe",
             f"Passt die Lichtfarbe eingeschalteter Lampen an den Sonnenstand an – morgens kühl, abends warm. "
             f"Jetzt: {k} K." + (" Aktiv." if auto_on else ""),
             auto_on, self._toggle_auto, self._toggle_auto)

    def _set_wakeup_minutes(self, m: int) -> None:
        self._wakeup_minutes = m
        self.prefs["wakeup_minutes"] = m
        L.save_prefs(self.prefs)
        self._rendered_key = None
        self._render()

    # --------------------------------------------------- bisherige API -----

    def cleanup(self) -> None:
        self.alive = False
        for r in self._routines.values():
            r.stop()


    def _threaded_group_cmd(self, turn_on: bool) -> None:
        """Best-effort master on/off for header switch callbacks."""

        # Prevent the header switch from snapping back before HA updates its state.
        try:
            with self._bridge_lock:
                if isinstance(self.bridge, _HomeAssistantBridgeAdapter):
                    self.bridge.set_cached_any_on(bool(turn_on))
        except Exception:
            pass

        def worker() -> None:
            ok = False
            try:
                client = self._ha_client
                cfg = self._ha_cfg
                if not client or not cfg:
                    ok = False
                else:
                    if turn_on and cfg.scene_all_on:
                        ok = self._apply_scene(cfg.scene_all_on)
                    elif (not turn_on) and cfg.scene_all_off:
                        ok = self._apply_scene(cfg.scene_all_off)
                    elif cfg.master_entity_id:
                        service = "turn_on" if turn_on else "turn_off"
                        ok = bool(client.call_service("homeassistant", service, {"entity_id": cfg.master_entity_id}))
                    else:
                        ok = False
            except Exception:
                ok = False

            def apply() -> None:
                if not self.alive:
                    return
                self.status_var.set("✅ Home Assistant: OK" if ok else "⚠️ Home Assistant: keine Aktion möglich")

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def come_home_safe(self) -> bool:
        """Trigger the configured 'come home' scene (best-effort, async)."""
        try:
            cfg = self._ha_cfg
            if not cfg:
                return False
            scene_id = getattr(cfg, "scene_come_home", None) or getattr(cfg, "scene_all_on", None)
            if not scene_id:
                return False
            self._activate_scene_async(str(scene_id))
            return True
        except Exception:
            return False

    def leave_home_safe(self) -> bool:
        """Trigger the configured 'leave home' scene (best-effort, async)."""
        try:
            cfg = self._ha_cfg
            if not cfg:
                return False
            scene_id = getattr(cfg, "scene_leave_home", None) or getattr(cfg, "scene_all_off", None)
            if not scene_id:
                return False
            self._activate_scene_async(str(scene_id))
            return True
        except Exception:
            return False

    def activate_scene_by_name_safe(self, name: str) -> bool:
        """Activate a scene by friendly name (best-effort)."""

        wanted = str(name or "").strip().lower()
        if not wanted:
            return False

        # Backward-compatible mapping: app.py calls "Hell" for come-home.
        # User intent: map this to the configured "all on" scene (e.g. "Alles hell").
        try:
            cfg = self._ha_cfg
            if wanted == "hell" and cfg:
                scene_id = getattr(cfg, "scene_come_home", None) or getattr(cfg, "scene_all_on", None)
                if scene_id:
                    self._activate_scene_async(str(scene_id))
                    return True
        except Exception:
            pass

        for sc in self._scenes:
            if (sc.get("name") or "").strip().lower() == wanted:
                self._activate_scene_async(sc.get("entity_id") or "")
                return True

        suffix = wanted.replace(" ", "_")
        for sc in self._scenes:
            ent = (sc.get("entity_id") or "").strip().lower()
            if ent.endswith("." + suffix):
                self._activate_scene_async(sc.get("entity_id") or "")
                return True

        return False

    def _init_homeassistant(self) -> None:
        cfg = load_homeassistant_config()
        self._ha_cfg = cfg
        if not cfg:
            self._ha_client = None
            self.bridge = None
            self.status_var.set("⚠️ Home Assistant: config/homeassistant.json oder ENV fehlt")
            return

        self._ha_client = HomeAssistantClient(cfg)
        self.bridge = _HomeAssistantBridgeAdapter(self._ha_client, cfg.master_entity_id)
        self.status_var.set("🔌 Home Assistant: bereit")

    def _set_last_refresh(self) -> None:
        try:
            self.last_refresh_var.set(datetime.now().strftime("%H:%M:%S"))
        except Exception:
            pass

    def _normalize_scene_name(self, name: str) -> str:
        return " ".join(str(name or "").strip().lower().split())

    def _resolve_scene_entity_id(self, scene_name_or_entity_id: Optional[str]) -> Optional[str]:
        raw = str(scene_name_or_entity_id or "").strip()
        if not raw:
            return None
        if raw.lower().startswith("scene."):
            return raw

        wanted = self._normalize_scene_name(raw)
        for sc in (self._scenes or []):
            if self._normalize_scene_name(sc.get("name") or "") == wanted:
                ent = str(sc.get("entity_id") or "").strip()
                return ent or None
        return None

    def _schedule_vorraum_poll(self) -> None:
        if not self.alive:
            return

        def tick() -> None:
            if not self.alive:
                return
            self._refresh_vorraum_status_async()
            try:
                self.root.after(10_000, tick)
            except Exception:
                pass

        try:
            self.root.after(5000, tick)
        except Exception:
            pass

    def _refresh_vorraum_status_async(self) -> None:
        if not self.alive:
            return

        def worker() -> None:
            client = self._ha_client
            cfg = self._ha_cfg

            resolved_entity = (self._vorraum_status_entity_resolved or "").strip()
            if not resolved_entity and cfg:
                resolved_entity = str(getattr(cfg, "vorraum_status_entity_id", "") or "").strip()

            state_data = None
            try:
                if client and resolved_entity:
                    state_data = client.get_state(resolved_entity)
            except Exception:
                state_data = None

            # Fallbacks: try common entity IDs, then heuristic search.
            if client and state_data is None and not resolved_entity:
                for candidate in ("light.vorraum", "switch.vorraum", "input_boolean.vorraum"):
                    try:
                        st = client.get_state(candidate)
                        if st is not None:
                            resolved_entity = candidate
                            state_data = st
                            break
                    except Exception:
                        continue

            if client and state_data is None and not resolved_entity:
                try:
                    for st in client.get_states():
                        ent = str(st.get("entity_id") or "")
                        if not ent.startswith(("light.", "switch.", "input_boolean.")):
                            continue
                        attrs = st.get("attributes") or {}
                        friendly = str(attrs.get("friendly_name") or "").strip().lower()
                        if "vorraum" in friendly:
                            resolved_entity = ent
                            state_data = st
                            break
                except Exception:
                    pass

            enabled: Optional[bool]
            status_text: str
            if not client or state_data is None:
                enabled = None
                status_text = "🚪 Vorraum: ?"
            else:
                try:
                    state_raw = str(state_data.get("state") or "").strip().lower()
                    if state_raw in ("on", "true", "open"):
                        enabled = True
                    elif state_raw in ("off", "false", "closed"):
                        enabled = False
                    else:
                        enabled = None
                except Exception:
                    enabled = None

                if enabled is True:
                    status_text = "🚪 Vorraum: an"
                elif enabled is False:
                    status_text = "🚪 Vorraum: aus"
                else:
                    status_text = "🚪 Vorraum: ?"

            def apply() -> None:
                if not self.alive:
                    return
                if resolved_entity:
                    self._vorraum_status_entity_resolved = resolved_entity
                self._vorraum_status_var.set(status_text)
                try:
                    on = enabled is True
                    self._vorraum_pill.configure(fg_color=COLOR_WARNING if on else COLOR_ROOT,
                                                 text_color="#111317" if on else COLOR_SUBTEXT)
                except Exception:
                    pass

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

