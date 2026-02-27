from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from core.homeassistant import HomeAssistantClient, load_homeassistant_config
from ui.components.card import Card
from ui.styles import COLOR_BORDER, COLOR_CARD, COLOR_ROOT, COLOR_SUBTEXT, COLOR_TEXT, get_safe_font


class HomeAssistantActionsTab:
    """Home Assistant actions tab.

    - If `actions` are configured in config/homeassistant.json, those are shown.
    - Otherwise, all `automation.*` and `script.*` entities are discovered via
      the Home Assistant states API and shown as buttons.
    """

    def __init__(self, root: tk.Tk, notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True

        self._ha_client: Optional[HomeAssistantClient] = None
        self._ha_cfg = None

        self._configured_actions: List[Dict[str, Any]] = []
        self._discovered_actions: List[Dict[str, Any]] = []
        self._actions: List[Dict[str, Any]] = []

        self._entity_load_running = False

        self.status_var = tk.StringVar(value="Home Assistant: –")

        # Tkinter is not thread-safe. Background workers must not call Tk APIs.
        self._ui_queue: "queue.Queue[callable]" = queue.Queue()

        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = ctk.CTkFrame(self.notebook, fg_color=COLOR_ROOT)
            self.notebook.add(self.tab_frame, text="HomeA")

        self._build_ui()
        self._start_ui_pump()
        self._init_homeassistant()
        self._render_actions()
        self._refresh_entities_async()

    def cleanup(self) -> None:
        self.alive = False

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

    def _build_ui(self) -> None:
        main = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        main.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        main.grid_rowconfigure(0, weight=0)
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=4, pady=(0, 8))

        ctk.CTkLabel(
            header,
            textvariable=self.status_var,
            font=get_safe_font("Bahnschrift", 11),
            text_color=COLOR_TEXT,
        ).pack(anchor="w")

        self._actions_card = Card(main, padding=8)
        self._actions_card.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        self._actions_body = ctk.CTkScrollableFrame(
            self._actions_card.content(),
            fg_color="transparent",
            scrollbar_button_color=COLOR_BORDER,
            scrollbar_button_hover_color=COLOR_BORDER,
        )
        self._actions_body.pack(fill=tk.BOTH, expand=True)

    def _init_homeassistant(self) -> None:
        cfg = load_homeassistant_config()
        if not cfg:
            self._ha_client = None
            self._ha_cfg = None
            self._configured_actions = []
            self._discovered_actions = []
            self._actions = []
            try:
                self.status_var.set("⚠️ Home Assistant: config/homeassistant.json oder ENV fehlt")
            except Exception:
                pass
            return

        self._ha_cfg = cfg
        self._ha_client = HomeAssistantClient(cfg)

        actions = getattr(cfg, "actions", None)
        self._configured_actions = list(actions) if isinstance(actions, list) else []
        self._actions = list(self._configured_actions)

        try:
            self.status_var.set("⏳ Home Assistant: lade Automationen/Skripte …")
        except Exception:
            pass

    def _refresh_entities_async(self) -> None:
        client = self._ha_client
        if not client:
            return
        if self._entity_load_running:
            return
        self._entity_load_running = True

        def worker() -> None:
            discovered: List[Dict[str, Any]] = []
            err: str = ""
            try:
                states = client.get_states()
                for st in states:
                    try:
                        entity_id = str(st.get("entity_id") or "").strip()
                        if not (entity_id.startswith("automation.") or entity_id.startswith("script.")):
                            continue

                        attrs = st.get("attributes") or {}
                        friendly = str(attrs.get("friendly_name") or "").strip()
                        label = friendly or entity_id

                        if entity_id.startswith("automation."):
                            domain, service = "automation", "trigger"
                        else:
                            domain, service = "script", "turn_on"

                        discovered.append(
                            {
                                "label": label,
                                "domain": domain,
                                "service": service,
                                "data": {"entity_id": entity_id},
                            }
                        )
                    except Exception:
                        continue

                discovered.sort(key=lambda a: str(a.get("label") or "").lower())
            except Exception as exc:
                err = str(exc)

            def apply() -> None:
                if not self.alive:
                    return
                self._entity_load_running = False

                if err:
                    self._discovered_actions = []
                    if not self._configured_actions:
                        self._actions = []
                    try:
                        self.status_var.set(f"⚠️ Home Assistant: Fehler beim Laden ({err})")
                    except Exception:
                        pass
                else:
                    self._discovered_actions = discovered
                    # Combine configured + discovered actions (configured first) and dedupe.
                    combined: List[Dict[str, Any]] = []
                    seen: set[tuple[str, str, str]] = set()

                    def _key(a: Dict[str, Any]) -> tuple[str, str, str]:
                        d = str(a.get("domain") or "").strip().lower()
                        s = str(a.get("service") or "").strip().lower()
                        ent = ""
                        try:
                            data = a.get("data") or {}
                            ent = str(data.get("entity_id") or "").strip().lower()
                        except Exception:
                            ent = ""
                        return (d, s, ent)

                    for a in (list(self._configured_actions) + list(self._discovered_actions)):
                        k = _key(a)
                        if k in seen:
                            continue
                        seen.add(k)
                        combined.append(a)

                    self._actions = combined
                    try:
                        extra = f" (+{len(self._configured_actions)} config)" if self._configured_actions else ""
                        self.status_var.set(f"✅ Home Assistant: {len(self._discovered_actions)} gefunden{extra}")
                    except Exception:
                        pass

                self._render_actions()

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _render_actions(self) -> None:
        try:
            for child in list(self._actions_body.winfo_children()):
                child.destroy()
        except Exception:
            pass

        if not self._ha_client:
            ctk.CTkLabel(
                self._actions_body,
                text="Home Assistant ist nicht konfiguriert.",
                font=("Segoe UI", 11),
                text_color=COLOR_SUBTEXT,
            ).pack(anchor="w", pady=4)
            return

        if not self._actions:
            msg = "Lade …" if self._entity_load_running else "Keine Automationen/Skripte gefunden."
            ctk.CTkLabel(
                self._actions_body,
                text=msg,
                font=("Segoe UI", 11),
                text_color=COLOR_SUBTEXT,
            ).pack(anchor="w", pady=4)
            return

        cols = 3
        grid = ctk.CTkFrame(self._actions_body, fg_color="transparent")
        grid.pack(fill=tk.BOTH, expand=True)
        for c in range(cols):
            grid.grid_columnconfigure(c, weight=1, uniform="ha_btn")

        for idx, action in enumerate(self._actions):
            label = str(action.get("label") or "").strip() or "Aktion"

            r, c = divmod(idx, cols)
            ctk.CTkButton(
                grid,
                text=label,
                font=("Segoe UI", 11),
                fg_color=COLOR_CARD,
                text_color=COLOR_TEXT,
                hover_color=COLOR_BORDER,
                border_width=1,
                border_color=COLOR_BORDER,
                corner_radius=8,
                height=36,
                command=lambda a=action: self._trigger_action_async(a),
            ).grid(row=r, column=c, sticky="ew", padx=4, pady=3)

    def _trigger_action_async(self, action: Dict[str, Any]) -> None:
        client = self._ha_client
        if not client:
            return

        domain = str(action.get("domain") or "").strip()
        service = str(action.get("service") or "").strip()
        data = action.get("data")
        if not isinstance(data, dict):
            data = {}

        label = str(action.get("label") or "").strip() or f"{domain}.{service}".strip(".")

        try:
            self.status_var.set(f"⏳ Starte: {label} …")
        except Exception:
            pass

        def worker() -> None:
            ok = False
            err = ""
            try:
                ok = bool(client.call_service(domain, service, data))
            except Exception as exc:
                ok = False
                err = str(exc)

            def apply() -> None:
                if not self.alive:
                    return
                if ok:
                    self.status_var.set(f"✅ gestartet: {label}")
                else:
                    msg = f"⚠️ Fehler: {label}" if not err else f"⚠️ Fehler: {label} ({err})"
                    self.status_var.set(msg)

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()
