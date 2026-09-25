from __future__ import annotations

import threading
import tkinter as tk
from typing import Any, Dict, List, Optional

import customtkinter as ctk

from core.homeassistant import HomeAssistantClient, load_homeassistant_config
from ui.components.card import Card
from ui.components.tab_shell import TabShell
from ui.components.ui_dispatch import UiQueuePumpMixin
from ui.styles import COLOR_BORDER, COLOR_CARD, COLOR_ROOT, COLOR_SUBTEXT, COLOR_TEXT, emoji, get_safe_font


def _prettify_label(raw: str) -> str:
    """Manche Skripte/Automationen haben in HA keinen gepflegten
    friendly_name und fallen auf die rohe entity_id zurueck (z.B.
    'vorraum_bewegung_licht') - nur bei so einem Rohnamen (Unterstriche,
    keine Leerzeichen) in eine lesbare Form bringen; echte, bereits
    gepflegte Namen bleiben unveraendert."""
    text = str(raw or "").strip()
    if "_" in text and " " not in text:
        words = [w for w in text.split("_") if w]
        text = " ".join(w if w.isupper() else w.capitalize() for w in words)
    return text or str(raw or "")


# 37 flache Buttons in einer Liste waren kaum scanbar - Automationen/
# Skripte stattdessen grob nach Themen gruppieren. Die Reihenfolge hier
# ist Prioritaet bei der Zuordnung (spezifischere Themen zuerst), z.B.
# damit "Heizung Schlafzimmer - AWAY" bei "Heizung" statt bei der
# allgemeineren "Anwesenheit"-Gruppe landet.
_GROUP_KEYWORDS = [
    ("Tagesablauf", "🌅", ("aufwach", "wecker", "gute nacht", "duschen")),
    ("Heizung", "🔥", ("heizung", "preheat")),
    ("Licht", "💡", ("licht", "vorraum")),
    ("Musik", "🎵", ("musik", "spotify", "lautstärke", "lautstaerke", "soundbar")),
    ("Batterie", "🔋", ("batterie",)),
    ("Anwesenheit", "🚶", ("away", "leaving", "coming", "override", "anwesenheit")),
]
_GROUP_FALLBACK = ("Sonstiges", "⚙️")
# Anzeige-Reihenfolge der Gruppen im UI (unabhaengig von der obigen
# Zuordnungs-Prioritaet) - alltagsrelevante Themen zuerst.
_GROUP_DISPLAY_ORDER = ["Tagesablauf", "Anwesenheit", "Heizung", "Licht", "Musik", "Batterie", "Sonstiges"]


def _categorize_action(label: str) -> tuple[str, str]:
    text = label.lower()
    for group, icon, keywords in _GROUP_KEYWORDS:
        if any(kw in text for kw in keywords):
            return group, icon
    return _GROUP_FALLBACK


class HomeAssistantActionsTab(UiQueuePumpMixin):
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
        self._init_ui_queue()

        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = ctk.CTkFrame(self.notebook, fg_color=COLOR_ROOT)
            # Icon ergaenzt, damit dieser Fallback-Pfad (nur relevant, wenn
            # kein tab_frame uebergeben wird) zum regulaeren Tab-Aufbau in
            # app.py passt, der bereits emoji("🏠 HomeA", ...) verwendet.
            self.notebook.add(self.tab_frame, text=emoji("🏠 HomeA", "HomeA"))

        self._build_ui()
        self._start_ui_pump()
        self._init_homeassistant()
        self._render_actions()
        self._refresh_entities_async()

    def cleanup(self) -> None:
        self.alive = False

    # _start_ui_pump()/_post_ui(): siehe UiQueuePumpMixin
    # (ui/components/ui_dispatch.py) - war hier vorher (mit zuletzt 50ms
    # Poll-Intervall, abweichend von den anderen Tabs) unabhaengig
    # dupliziert, siehe Docstring dort fuer die Historie.

    def _build_ui(self) -> None:
        self._shell = TabShell(
            self.tab_frame,
            "Home Assistant",
            "Automationen und Skripte werden geladen ...",
        )
        self._shell.pack(fill=tk.BOTH, expand=True)
        self._shell.subtitle_label.configure(textvariable=self.status_var)

        self._actions_card = Card(self._shell.body, padding=18)
        self._actions_card.grid(row=0, column=0, sticky="nsew")
        # War bisher die einzige Card in der App ohne add_title() - anders
        # als Status/Tado/Health/Ertrag/Analyse, die ihre jeweilige Card
        # immer beschriften.
        self._actions_card.add_title("Automationen & Skripte", icon="⚙️")

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
                font=("Segoe UI", 13),
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

        cols = 2 if getattr(self, "_portrait_layout", False) else 3

        groups: Dict[str, List[Dict[str, Any]]] = {}
        group_icons: Dict[str, str] = {}
        for action in self._actions:
            label = str(action.get("label") or "").strip() or "Aktion"
            group, icon = _categorize_action(label)
            groups.setdefault(group, []).append(action)
            group_icons[group] = icon

        order = [g for g in _GROUP_DISPLAY_ORDER if g in groups]
        order += sorted(g for g in groups if g not in _GROUP_DISPLAY_ORDER)

        self._action_group_grids = []
        for gi, group in enumerate(order):
            group_actions = groups[group]

            header = ctk.CTkLabel(
                self._actions_body,
                text=f"{group_icons[group]}  {group}",
                font=get_safe_font("Bahnschrift", 13, "bold"),
                text_color=COLOR_SUBTEXT,
            )
            header.pack(anchor="w", pady=(18 if gi else 0, 6))

            grid = ctk.CTkFrame(self._actions_body, fg_color="transparent")
            grid.pack(fill=tk.BOTH, expand=True)
            self._action_group_grids.append(grid)
            for c in range(cols):
                grid.grid_columnconfigure(c, weight=1, uniform="ha_btn")

            for idx, action in enumerate(group_actions):
                label = _prettify_label(str(action.get("label") or "").strip() or "Aktion")

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
                    corner_radius=16,
                    height=52,
                    command=lambda a=action: self._trigger_action_async(a),
                ).grid(row=r, column=c, sticky="ew", padx=8, pady=6)

    def set_portrait_layout(self, portrait: bool) -> None:
        """Use a narrower two-column action grid in portrait mode."""
        try:
            self._portrait_layout = portrait
            if hasattr(self, "_shell"):
                self._shell.set_portrait_layout(portrait)
            grids = getattr(self, "_action_group_grids", None)
            if not grids:
                return
            columns = 2 if portrait else 3
            for grid in grids:
                buttons = list(grid.winfo_children())
                for col in range(3):
                    grid.grid_columnconfigure(col, weight=1 if col < columns else 0)
                for index, button in enumerate(buttons):
                    button.grid_configure(row=index // columns, column=index % columns)
                    button.configure(height=60 if portrait else 52, font=("Segoe UI", 14 if portrait else 13))
        except Exception:
            pass

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
