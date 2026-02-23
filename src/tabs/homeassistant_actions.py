from __future__ import annotations

import queue
import threading
import tkinter as tk
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from core.homeassistant import HomeAssistantClient, load_homeassistant_config
from ui.components.card import Card
from ui.styles import COLOR_BORDER, COLOR_CARD, COLOR_ROOT, COLOR_SUBTEXT, COLOR_TEXT, get_safe_font, emoji


class HomeAssistantActionsTab:
    """Trigger Home Assistant automations/scripts configured in config/homeassistant.json.

    Configuration format (in config/homeassistant.json):
    {
      "actions": [
        {"label": "Guten Morgen", "service": "automation.trigger", "data": {"entity_id": "automation.guten_morgen"}},
        {"label": "Staubsauger", "service": "script.turn_on", "data": {"entity_id": "script.start_vacuum"}}
      ]
    }
    """

    def __init__(self, root: tk.Tk, notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True

        self._ha_client: Optional[HomeAssistantClient] = None
        self._ha_cfg = None
        self._actions: List[Dict[str, Any]] = []

        self.status_var = tk.StringVar(value="Home Assistant: –")

        # Tkinter is not thread-safe. Background workers must not call Tk APIs.
        self._ui_queue: "queue.Queue[callable]" = queue.Queue()

        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = ctk.CTkFrame(self.notebook, fg_color=COLOR_ROOT)
            self.notebook.add(self.tab_frame, text=emoji("🏠 Home Assistant", "Home Assistant"))

        self._build_ui()
        self._start_ui_pump()
        self._init_homeassistant()
        self._render_actions()

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

        header = Card(main, padding=12)
        header.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)
        header.add_title("Home Assistant", icon="🏠")

        header_body = ctk.CTkFrame(header.content(), fg_color="transparent")
        header_body.pack(fill=tk.X)

        self._status_label = ctk.CTkLabel(
            header_body,
            textvariable=self.status_var,
            font=get_safe_font("Bahnschrift", 12),
            text_color=COLOR_TEXT,
        )
        self._status_label.pack(anchor="w")

        hint = ctk.CTkLabel(
            header_body,
            text="Aktionen werden aus config/homeassistant.json geladen.",
            font=("Segoe UI", 10),
            text_color=COLOR_SUBTEXT,
        )
        hint.pack(anchor="w", pady=(6, 0))

        self._actions_card = Card(main, padding=12)
        self._actions_card.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        self._actions_card.add_title("Automationen & Skripte", icon="🤖")

        self._actions_body = ctk.CTkFrame(self._actions_card.content(), fg_color="transparent")
        self._actions_body.pack(fill=tk.BOTH, expand=True)

    def _init_homeassistant(self) -> None:
        cfg = load_homeassistant_config()
        if not cfg:
            self._ha_client = None
            self._ha_cfg = None
            self._actions = []
            try:
                self.status_var.set("⚠️ Home Assistant: config/homeassistant.json oder ENV fehlt")
            except Exception:
                pass
            return

        self._ha_cfg = cfg
        self._ha_client = HomeAssistantClient(cfg)

        actions = getattr(cfg, "actions", None)
        if isinstance(actions, list):
            self._actions = actions
        else:
            self._actions = []

        try:
            self.status_var.set("✅ Home Assistant: bereit")
        except Exception:
            pass

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
                font=("Segoe UI", 12),
                text_color=COLOR_SUBTEXT,
            ).pack(anchor="w", pady=4)
            return

        if not self._actions:
            ctk.CTkLabel(
                self._actions_body,
                text="Keine Aktionen konfiguriert. Füge in config/homeassistant.json eine Liste 'actions' hinzu.",
                font=("Segoe UI", 12),
                text_color=COLOR_SUBTEXT,
                wraplength=760,
                justify="left",
            ).pack(anchor="w", pady=4)
            return

        grid = ctk.CTkFrame(self._actions_body, fg_color="transparent")
        grid.pack(fill=tk.BOTH, expand=True)
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, weight=1)

        self._action_buttons: List[ctk.CTkButton] = []
        for idx, action in enumerate(self._actions):
            label = str(action.get("label") or "").strip() or "Aktion"
            domain = str(action.get("domain") or "").strip()
            service = str(action.get("service") or "").strip()

            ent = ""
            try:
                data = action.get("data") or {}
                ent = str(data.get("entity_id") or "").strip()
            except Exception:
                ent = ""

            sub = ent or f"{domain}.{service}".strip(".")

            card = ctk.CTkFrame(grid, fg_color=COLOR_CARD, corner_radius=10, border_width=1, border_color=COLOR_BORDER)
            r, c = divmod(idx, 2)
            card.grid(row=r, column=c, sticky="nsew", padx=6, pady=6)
            card.grid_columnconfigure(0, weight=1)

            ctk.CTkLabel(
                card,
                text=label,
                font=get_safe_font("Bahnschrift", 13, "bold"),
                text_color=COLOR_TEXT,
            ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 0))

            ctk.CTkLabel(
                card,
                text=sub,
                font=("Segoe UI", 10),
                text_color=COLOR_SUBTEXT,
            ).grid(row=1, column=0, sticky="w", padx=10, pady=(2, 8))

            btn = ctk.CTkButton(
                card,
                text="Start",
                fg_color=COLOR_CARD,
                text_color=COLOR_TEXT,
                hover_color=COLOR_BORDER,
                command=lambda a=action: self._trigger_action_async(a),
                width=120,
            )
            btn.grid(row=2, column=0, sticky="w", padx=10, pady=(0, 10))
            self._action_buttons.append(btn)

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
