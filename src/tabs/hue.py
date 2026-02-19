"""Home Assistant scenes tab (replaces Philips Hue bridge control).

This module keeps the public API that `src/ui/app.py` expects from the former
Hue integration:
- attributes: `bridge`, `_bridge_lock`
- methods: `_threaded_group_cmd`, `activate_scene_by_name_safe`, `cleanup`

The UI lists Home Assistant scenes (scene.*) and triggers `scene.turn_on`.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk
from datetime import datetime
import time
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from core.homeassistant import HomeAssistantClient, load_homeassistant_config
from ui.styles import COLOR_BORDER, COLOR_CARD, COLOR_ROOT, COLOR_SUBTEXT, COLOR_TEXT, COLOR_WARNING, get_safe_font, emoji


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


class HueTab:
    """Home Assistant scenes controller (keeps HueTab name for compatibility)."""

    def __init__(self, root, notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True

        self._bridge_lock = threading.Lock()
        self._ha_client: Optional[HomeAssistantClient] = None
        self._ha_cfg = None
        self.bridge = None  # adapter when configured

        self.status_var = tk.StringVar(value="⚠️ Home Assistant: nicht konfiguriert")
        self.last_refresh_var = tk.StringVar(value="")

        self._dimmer_value = tk.DoubleVar(value=100.0)
        self._dimmer_label_var = tk.StringVar(value="Dimmen: 100%")

        self._vorraum_var = tk.BooleanVar(value=False)
        self._vorraum_status_var = tk.StringVar(value="")
        self._vorraum_ignore_callback = False
        self._vorraum_status_entity_resolved: Optional[str] = None

        self._scene_buttons: Dict[str, tk.Button] = {}
        self._scenes: List[Dict[str, str]] = []

        # Tkinter is not thread-safe. Background workers must not call Tk APIs.
        # We route UI updates through this queue and execute them on the main thread.
        self._ui_queue: "queue.Queue[callable]" = queue.Queue()

        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
            notebook.add(self.tab_frame, text=emoji("💡 Licht", "Licht"))

        self._build_ui()
        self._start_ui_pump()
        self._init_homeassistant()
        self._refresh_scenes_async()
        self._refresh_vorraum_status_async()
        self._schedule_vorraum_poll()

    def _start_ui_pump(self) -> None:
        def pump() -> None:
            if not self.alive:
                return
            try:
                while True:
                    cb = self._ui_queue.get_nowait()
                    try:
                        cb()
                    except Exception:
                        pass
            except queue.Empty:
                pass

            try:
                self.root.after(50, pump)
            except Exception:
                pass

        try:
            self.root.after(0, pump)
        except Exception:
            pass

    def _post_ui(self, callback) -> None:
        try:
            if not self.alive:
                return
            self._ui_queue.put(callback)
        except Exception:
            pass

    # --- public API used by app.py ---
    def cleanup(self) -> None:
        self.alive = False

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
                        ok = bool(client.activate_scene(cfg.scene_all_on))
                    elif (not turn_on) and cfg.scene_all_off:
                        ok = bool(client.activate_scene(cfg.scene_all_off))
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

    # --- dimmer ---
    def _dimmer_targets(self) -> List[str]:
        cfg = self._ha_cfg
        client = self._ha_client
        if cfg and getattr(cfg, "dim_entity_ids", None):
            try:
                ids = [str(x).strip() for x in (cfg.dim_entity_ids or []) if str(x).strip()]
                if ids:
                    return ids
            except Exception:
                pass
        # Fallback: dim all available light.* entities.
        if client:
            try:
                return client.list_lights()
            except Exception:
                return []
        return []

    def _apply_dimmer_label(self) -> None:
        try:
            pct = int(round(float(self._dimmer_value.get())))
        except Exception:
            pct = 0
        pct = max(0, min(100, pct))
        try:
            self._dimmer_label_var.set(f"Dimmen: {pct}%")
        except Exception:
            pass

    def _set_brightness_async(self, brightness_pct: int) -> None:
        def worker() -> None:
            ok = False
            try:
                client = self._ha_client
                cfg = self._ha_cfg
                targets = self._dimmer_targets()
                if not client or not targets:
                    ok = False
                else:
                    pct = int(brightness_pct)
                    if pct <= 0:
                        ok = bool(client.turn_off_lights(targets))
                    else:
                        ok = bool(client.set_light_brightness_pct(targets, pct))

                    # User rule: when dimming above threshold -> ceiling lamp ON; below threshold -> OFF.
                    # Exact threshold value does not change the ceiling lamp state.
                    try:
                        ceiling_ent = str(getattr(cfg, "ceiling_entity_id", "") or "").strip()
                        threshold = int(getattr(cfg, "ceiling_threshold_pct", 80) or 80)
                        threshold = max(0, min(100, threshold))
                        if ceiling_ent:
                            if pct > threshold:
                                ok_ceiling = bool(
                                    client.call_service("homeassistant", "turn_on", {"entity_id": ceiling_ent})
                                )
                                ok = bool(ok and ok_ceiling)
                            elif pct < threshold:
                                ok_ceiling = bool(
                                    client.call_service("homeassistant", "turn_off", {"entity_id": ceiling_ent})
                                )
                                ok = bool(ok and ok_ceiling)
                    except Exception:
                        pass
            except Exception:
                ok = False

            def apply() -> None:
                if not self.alive:
                    return
                self.status_var.set("✅ Dimmer gesetzt" if ok else "⚠️ Dimmer fehlgeschlagen")

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    # --- UI ---
    def _build_ui(self) -> None:
        header = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        header.pack(fill="x", padx=10, pady=10)

        status_lbl = tk.Label(
            header,
            textvariable=self.status_var,
            font=("Segoe UI", 10),
            fg=COLOR_SUBTEXT,
            bg=COLOR_ROOT,
        )
        status_lbl.grid(row=0, column=0, sticky="w")

        refresh_lbl = tk.Label(
            header,
            textvariable=self.last_refresh_var,
            font=("Segoe UI", 9),
            fg=COLOR_SUBTEXT,
            bg=COLOR_ROOT,
        )
        refresh_lbl.grid(row=0, column=1, sticky="e")

        dimmer_card = ctk.CTkFrame(header, fg_color=COLOR_CARD, corner_radius=14)
        dimmer_card.grid(row=1, column=0, sticky="w", pady=(10, 0))

        dim_lbl = ctk.CTkLabel(
            dimmer_card,
            textvariable=self._dimmer_label_var,
            font=get_safe_font("Bahnschrift", 12, "bold"),
            text_color=COLOR_TEXT,
        )
        dim_lbl.pack(side="left", padx=(12, 10), pady=10)

        dim_slider = ctk.CTkSlider(
            dimmer_card,
            from_=0,
            to=100,
            number_of_steps=100,
            width=320,
            height=20,
            variable=self._dimmer_value,
            command=lambda _v: self._apply_dimmer_label(),
            fg_color=COLOR_BORDER,
            progress_color=COLOR_WARNING,
            button_color=COLOR_TEXT,
            button_hover_color=COLOR_TEXT,
        )
        dim_slider.pack(side="left", padx=(0, 12), pady=10)

        def _on_release(_event) -> None:
            try:
                pct = int(round(float(self._dimmer_value.get())))
            except Exception:
                pct = 0
            self._set_brightness_async(pct)

        dim_slider.bind("<ButtonRelease-1>", _on_release)

        vorraum_card = ctk.CTkFrame(header, fg_color=COLOR_CARD, corner_radius=14)
        vorraum_card.grid(row=2, column=0, sticky="w", pady=(10, 0))

        vorraum_title = ctk.CTkLabel(
            vorraum_card,
            text="Vorraum",
            font=get_safe_font("Bahnschrift", 12, "bold"),
            text_color=COLOR_TEXT,
        )
        vorraum_title.pack(side="left", padx=(12, 10), pady=10)

        vorraum_switch = ctk.CTkSwitch(
            vorraum_card,
            text="",
            variable=self._vorraum_var,
            command=self._on_vorraum_toggle,
            fg_color=COLOR_BORDER,
            progress_color=COLOR_WARNING,
            button_color=COLOR_TEXT,
            button_hover_color=COLOR_TEXT,
        )
        vorraum_switch.pack(side="left", padx=(0, 10), pady=10)

        vorraum_status_lbl = ctk.CTkLabel(
            vorraum_card,
            textvariable=self._vorraum_status_var,
            font=get_safe_font("Bahnschrift", 11, "normal"),
            text_color=COLOR_SUBTEXT,
        )
        vorraum_status_lbl.pack(side="left", padx=(0, 12), pady=10)

        btn = tk.Button(
            header,
            text="↻ Neu laden",
            font=("Segoe UI", 9),
            bg=COLOR_CARD,
            fg=COLOR_TEXT,
            activebackground=COLOR_CARD,
            relief="flat",
            command=self._refresh_all_async,
        )
        btn.grid(row=1, column=1, sticky="e", pady=(8, 0))

        header.grid_columnconfigure(0, weight=1)
        header.grid_columnconfigure(1, weight=0)

        self._apply_dimmer_label()

        canvas_frame = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        canvas_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._scroll_canvas = tk.Canvas(canvas_frame, bg=COLOR_ROOT, highlightthickness=0)
        self._scroll_canvas.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(canvas_frame, orient="vertical", command=self._scroll_canvas.yview)
        scrollbar.pack(side="right", fill="y")
        self._scroll_canvas.configure(yscrollcommand=scrollbar.set)

        self._scroll_window = ctk.CTkFrame(self._scroll_canvas, fg_color=COLOR_ROOT, corner_radius=0)
        self._scroll_window_id = self._scroll_canvas.create_window((0, 0), window=self._scroll_window, anchor="nw")

        def _on_frame_configure(_event):
            try:
                self._scroll_canvas.configure(scrollregion=self._scroll_canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas_configure(event):
            try:
                self._scroll_canvas.itemconfigure(self._scroll_window_id, width=event.width)
            except Exception:
                pass

        self._scroll_window.bind("<Configure>", _on_frame_configure)
        self._scroll_canvas.bind("<Configure>", _on_canvas_configure)

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

    def _render_scenes(self) -> None:
        for child in list(self._scroll_window.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass
        self._scene_buttons.clear()

        if not self._scenes:
            lbl = ctk.CTkLabel(
                self._scroll_window,
                text="Keine Szenen gefunden.",
                font=get_safe_font("Bahnschrift", 12, "bold"),
                text_color=COLOR_SUBTEXT,
            )
            lbl.pack(anchor="w", padx=10, pady=10)
            return

        cols = 3
        for i in range(cols):
            self._scroll_window.grid_columnconfigure(i, weight=1)

        for idx, sc in enumerate(self._scenes):
            name = sc.get("name") or sc.get("entity_id") or "(unbenannt)"
            ent = sc.get("entity_id") or ""
            r = idx // cols
            c = idx % cols
            b = ctk.CTkButton(
                self._scroll_window,
                text=name,
                command=lambda e=ent: self._activate_scene_async(e),
                fg_color=COLOR_CARD,
                hover_color=COLOR_BORDER,
                border_color=COLOR_BORDER,
                border_width=1,
                text_color=COLOR_TEXT,
                corner_radius=14,
                height=56,
                font=get_safe_font("Bahnschrift", 14, "bold"),
            )
            b.grid(row=r, column=c, sticky="ew", padx=6, pady=6)
            self._scene_buttons[ent] = b

    def _refresh_scenes_async(self) -> None:
        if not self.alive:
            return

        def worker() -> None:
            client = self._ha_client
            if not client:
                scenes: List[Dict[str, str]] = []
                ok = False
            else:
                try:
                    scenes = client.list_scenes()
                    ok = True
                except Exception:
                    scenes = []
                    ok = False

            def apply() -> None:
                if not self.alive:
                    return
                self._scenes = scenes
                self._set_last_refresh()
                self._render_scenes()
                if ok:
                    self.status_var.set(f"✅ Home Assistant: {len(scenes)} Szenen")
                else:
                    self.status_var.set("⚠️ Home Assistant: Fehler beim Laden")

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_all_async(self) -> None:
        self._refresh_scenes_async()
        self._refresh_vorraum_status_async()

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

    def _on_vorraum_toggle(self) -> None:
        if self._vorraum_ignore_callback:
            return
        try:
            desired_on = bool(self._vorraum_var.get())
        except Exception:
            desired_on = False

        self._vorraum_status_var.set("⏳ schalte …")
        self._set_vorraum_scene_async(desired_on)

    def _set_vorraum_scene_async(self, turn_on: bool) -> None:
        def worker() -> None:
            ok = False
            scene_ent: Optional[str] = None
            try:
                client = self._ha_client
                cfg = self._ha_cfg
                if not client or not cfg:
                    ok = False
                else:
                    scene_key = "vorraum_scene_on" if turn_on else "vorraum_scene_off"
                    configured = getattr(cfg, scene_key, None)
                    scene_ent = self._resolve_scene_entity_id(configured)
                    if not scene_ent:
                        # Fallback: look up by friendly name in loaded scenes.
                        scene_ent = self._resolve_scene_entity_id("vorraum ein" if turn_on else "vorraum aus")

                    if not scene_ent:
                        try:
                            scenes = client.list_scenes()
                            wanted = self._normalize_scene_name("vorraum ein" if turn_on else "vorraum aus")
                            for sc in scenes:
                                if self._normalize_scene_name(sc.get("name") or "") == wanted:
                                    scene_ent = str(sc.get("entity_id") or "").strip() or None
                                    break
                        except Exception:
                            pass

                    ok = bool(scene_ent and client.activate_scene(scene_ent))
            except Exception:
                ok = False

            def apply() -> None:
                if not self.alive:
                    return
                if ok:
                    self.status_var.set(f"✅ Vorraum Szene aktiviert: {scene_ent}")
                else:
                    self.status_var.set("⚠️ Vorraum Szene fehlgeschlagen")

            self._post_ui(apply)

            # Best-effort: refresh status after HA applied changes.
            try:
                time.sleep(0.4)
            except Exception:
                pass
            self._refresh_vorraum_status_async()

        threading.Thread(target=worker, daemon=True).start()

    def _schedule_vorraum_poll(self) -> None:
        if not self.alive:
            return

        def tick() -> None:
            if not self.alive:
                return
            self._refresh_vorraum_status_async()
            try:
                self.root.after(5000, tick)
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
                status_text = "Status: unbekannt"
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
                    status_text = "Status: Ein"
                elif enabled is False:
                    status_text = "Status: Aus"
                else:
                    status_text = "Status: unbekannt"

            def apply() -> None:
                if not self.alive:
                    return
                if resolved_entity:
                    self._vorraum_status_entity_resolved = resolved_entity
                self._vorraum_status_var.set(status_text)

                if enabled is None:
                    return
                try:
                    self._vorraum_ignore_callback = True
                    self._vorraum_var.set(bool(enabled))
                finally:
                    self._vorraum_ignore_callback = False

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _activate_scene_async(self, entity_id: str) -> None:
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return

        def worker() -> None:
            ok = False
            try:
                client = self._ha_client
                ok = bool(client and client.activate_scene(entity_id))
            except Exception:
                ok = False

            def apply() -> None:
                if not self.alive:
                    return
                self.status_var.set(
                    f"✅ Szene aktiviert: {entity_id}" if ok else f"⚠️ Szene fehlgeschlagen: {entity_id}"
                )

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()
