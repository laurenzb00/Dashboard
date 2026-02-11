import threading
import os
import time
import logging
import tkinter as tk
from tkinter import ttk
import webbrowser
import customtkinter as ctk
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from ui.styles import (
    COLOR_ROOT,
    COLOR_CARD,
    COLOR_BORDER,
    COLOR_PRIMARY,
    COLOR_SUCCESS,
    COLOR_WARNING,
    COLOR_DANGER,
    COLOR_TEXT,
    COLOR_SUBTEXT,
    COLOR_TITLE,
    emoji,
)
from ui.components.card import Card

TADO_ENABLED = os.getenv("TADO_ENABLE", "").strip().lower() in {"1", "true", "yes", "on"}
if not TADO_ENABLED:
    TADO_ENABLED = bool(os.getenv("TADO_USER") or os.getenv("TADO_PASS"))

# --- Robust: python-tado-Import mit Fallback ---
Tado = None
try:
    from python_tado import Tado as _PythonTado
    Tado = _PythonTado
except ImportError as exc:
    if TADO_ENABLED:
        logging.warning("[TADO] python-tado Import fehlgeschlagen: %s", exc)
    else:
        logging.info("[TADO] python-tado nicht installiert (TADO deaktiviert)")

if Tado is None:
    # Aktuelles `python-tado` (0.19.x) installiert i.d.R. als Paket `PyTado`
    # und exportiert die Klasse über `PyTado.interface`.
    try:
        from PyTado.interface import Tado as _PyTado

        Tado = _PyTado
        logging.info("[TADO] Import via PyTado.interface erfolgreich.")
    except ImportError as exc_pytado:
        try:
            from PyTado.interface.interface import Tado as _PyTado2

            Tado = _PyTado2
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
TADO_CLIENT_ID = os.getenv("TADO_CLIENT_ID", "tado-web-app")
TADO_SCOPE = os.getenv("TADO_SCOPE", "home.user")

class TadoTab:
    """Tado Klima-Tab (Status + Steuerung).

    Hinweis: Login/Authentifizierung bleibt wie zuvor (env vars / device flow).
    Der Tab bleibt immer sichtbar und zeigt bei fehlender Konfiguration
    eine klare Anleitung statt zu verschwinden.
    """
    
    def __init__(self, root: tk.Tk, notebook: ttk.Notebook, tab_frame=None):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self.api = None
        self.zone_id = None
        self.zones: list[dict] = []
        self._zone_name_to_id: dict[str, int] = {}
        self._pending_apply_job = None
        self._suppress_target_send = False
        self._suppress_mode_send = False
        self._device_url: str | None = None
        logging.info("[TADO] Tab initialisiert")
        
        # UI Variablen
        self.var_temp_ist = tk.StringVar(value="--.- °C")
        self.var_temp_soll = tk.StringVar(value="-- °C")
        self.var_humidity = tk.StringVar(value="-- %")
        self.var_status = tk.StringVar(value="Verbinde...")
        self.var_power = tk.IntVar(value=0)
        self.var_zone = tk.StringVar(value="-")
        self.var_hint = tk.StringVar(value="")
        self.var_target = tk.DoubleVar(value=20.0)
        self.var_mode = tk.StringVar(value="Auto")

        # Tab Frame - Use provided frame or create legacy one
        if tab_frame is not None:
            self.tab_frame = tab_frame
        else:
            self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
            notebook.add(self.tab_frame, text=emoji("🌡️ Raumtemperatur", "Raumtemperatur"))

        try:
            # CTk container: background
            self.tab_frame.configure(fg_color=COLOR_ROOT)
        except Exception:
            pass

        self._build_ui()

        # Start Update Loop
        self.root.after(0, lambda: threading.Thread(target=self._loop, daemon=True).start())

    def stop(self):
        self.alive = False
        if self._pending_apply_job is not None:
            try:
                self.root.after_cancel(self._pending_apply_job)
            except Exception:
                pass
            self._pending_apply_job = None

    def _build_ui(self) -> None:
        # Layout: header + two cards
        container = ctk.CTkFrame(self.tab_frame, fg_color="transparent")
        container.pack(fill=tk.BOTH, expand=True, padx=12, pady=12)

        header = ctk.CTkFrame(container, fg_color="transparent")
        header.pack(fill=tk.X, pady=(0, 8))

        ctk.CTkLabel(
            header,
            text=emoji("🌡️ Raumtemperatur", "Raumtemperatur"),
            font=("Segoe UI", 16, "bold"),
            text_color=COLOR_TITLE,
        ).pack(side=tk.LEFT)

        ctk.CTkLabel(
            header,
            textvariable=self.var_status,
            font=("Segoe UI", 11),
            text_color=COLOR_SUBTEXT,
        ).pack(side=tk.RIGHT)

        hint = ctk.CTkFrame(container, fg_color="transparent")
        hint.pack(fill=tk.X, pady=(0, 10))
        self._hint_label = ctk.CTkLabel(
            hint,
            textvariable=self.var_hint,
            font=("Segoe UI", 10),
            text_color=COLOR_SUBTEXT,
            wraplength=900,
            justify="left",
        )
        self._hint_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._open_url_btn = ctk.CTkButton(
            hint,
            text="Im Browser öffnen",
            width=150,
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_SUCCESS,
            command=self._open_device_url,
        )
        self._open_url_btn.pack(side=tk.RIGHT, padx=(10, 0))
        self._open_url_btn.configure(state="disabled")

        content = ctk.CTkFrame(container, fg_color="transparent")
        content.pack(fill=tk.BOTH, expand=True)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)

        # Left: live data
        self.card_live = Card(content)
        self.card_live.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)
        self.card_live.add_title("Aktuell", icon="📊")

        live = ctk.CTkFrame(self.card_live.content(), fg_color="transparent")
        live.pack(fill=tk.BOTH, expand=True)

        ctk.CTkLabel(live, text="Zone", font=("Segoe UI", 11), text_color=COLOR_SUBTEXT).pack(anchor="w")
        ctk.CTkLabel(live, textvariable=self.var_zone, font=("Segoe UI", 14, "bold"), text_color=COLOR_TEXT).pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(live, text="Temperatur", font=("Segoe UI", 11), text_color=COLOR_SUBTEXT).pack(anchor="w")
        ctk.CTkLabel(live, textvariable=self.var_temp_ist, font=("Segoe UI", 34, "bold"), text_color=COLOR_PRIMARY).pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(live, text="Luftfeuchtigkeit", font=("Segoe UI", 11), text_color=COLOR_SUBTEXT).pack(anchor="w")
        ctk.CTkLabel(live, textvariable=self.var_humidity, font=("Segoe UI", 20, "bold"), text_color=COLOR_TEXT).pack(anchor="w", pady=(0, 10))

        ctk.CTkLabel(live, text="Heizleistung", font=("Segoe UI", 11), text_color=COLOR_SUBTEXT).pack(anchor="w")
        self._power_bar = ctk.CTkProgressBar(live, height=12, fg_color=COLOR_CARD, progress_color=COLOR_WARNING)
        self._power_bar.pack(fill=tk.X, pady=(6, 0))
        self._power_bar.set(0.0)

        # Right: controls
        self.card_ctrl = Card(content)
        self.card_ctrl.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=0)
        self.card_ctrl.add_title("Steuerung", icon="⚙️")

        ctrl = ctk.CTkFrame(self.card_ctrl.content(), fg_color="transparent")
        ctrl.pack(fill=tk.BOTH, expand=True)

        ctk.CTkLabel(ctrl, text="Zone", font=("Segoe UI", 11), text_color=COLOR_SUBTEXT).pack(anchor="w")
        ctk.CTkLabel(ctrl, textvariable=self.var_zone, font=("Segoe UI", 13, "bold"), text_color=COLOR_TEXT).pack(anchor="w", pady=(4, 14))

        ctk.CTkLabel(ctrl, text="Modus", font=("Segoe UI", 11), text_color=COLOR_SUBTEXT).pack(anchor="w")
        try:
            self._mode_toggle = ctk.CTkSegmentedButton(
                ctrl,
                values=["Auto", "Manuell"],
                variable=self.var_mode,
                command=self._on_mode_changed,
                fg_color=COLOR_CARD,
                selected_color=COLOR_PRIMARY,
                selected_hover_color=COLOR_SUCCESS,
                unselected_color=COLOR_CARD,
                unselected_hover_color=COLOR_BORDER,
                text_color=COLOR_TEXT,
            )
            self._mode_toggle.pack(fill=tk.X, pady=(6, 14))
        except Exception:
            # Fallback if segmented button not available in installed customtkinter
            mode_row = ctk.CTkFrame(ctrl, fg_color="transparent")
            mode_row.pack(fill=tk.X, pady=(6, 14))
            self._mode_auto = ctk.CTkRadioButton(mode_row, text="Auto", variable=self.var_mode, value="Auto", command=self._on_mode_changed)
            self._mode_manual = ctk.CTkRadioButton(mode_row, text="Manuell", variable=self.var_mode, value="Manuell", command=self._on_mode_changed)
            self._mode_auto.pack(side=tk.LEFT, padx=(0, 10))
            self._mode_manual.pack(side=tk.LEFT)

        ctk.CTkLabel(ctrl, text="Zieltemperatur", font=("Segoe UI", 11), text_color=COLOR_SUBTEXT).pack(anchor="w")

        # Large target display
        self._target_label = ctk.CTkLabel(
            ctrl,
            textvariable=self.var_temp_soll,
            font=("Segoe UI", 28, "bold"),
            text_color=COLOR_WARNING,
        )
        self._target_label.pack(anchor="w", pady=(4, 8))

        # Slider + +/- (0.5°C steps)
        slider_row = ctk.CTkFrame(ctrl, fg_color="transparent")
        slider_row.pack(fill=tk.X)
        minus_btn = ctk.CTkButton(
            slider_row,
            text="−",
            width=40,
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=lambda: self._nudge_target(-1.0),
        )
        minus_btn.pack(side=tk.LEFT)
        self._target_slider = ctk.CTkSlider(
            slider_row,
            from_=12,
            to=30,
            number_of_steps=36,
            variable=self.var_target,
            command=self._on_target_slider,
            fg_color=COLOR_CARD,
            progress_color=COLOR_PRIMARY,
            button_color=COLOR_PRIMARY,
            button_hover_color=COLOR_SUCCESS,
        )
        self._target_slider.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=10)
        plus_btn = ctk.CTkButton(
            slider_row,
            text="+",
            width=40,
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=lambda: self._nudge_target(+1.0),
        )
        plus_btn.pack(side=tk.LEFT)

        quick_row = ctk.CTkFrame(ctrl, fg_color="transparent")
        quick_row.pack(fill=tk.X, pady=(10, 0))
        self._minus_half_btn = ctk.CTkButton(
            quick_row,
            text="−0.5°C",
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=lambda: self._nudge_target(-0.5),
        )
        self._minus_half_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self._plus_half_btn = ctk.CTkButton(
            quick_row,
            text="+0.5°C",
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=lambda: self._nudge_target(+0.5),
        )
        self._plus_half_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        btn_row = ctk.CTkFrame(ctrl, fg_color="transparent")
        btn_row.pack(fill=tk.X, pady=(14, 0))
        self._apply_btn = ctk.CTkButton(
            btn_row,
            text="Anwenden",
            fg_color=COLOR_PRIMARY,
            hover_color=COLOR_SUCCESS,
            command=self._apply_target_temperature,
        )
        self._apply_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))
        self._auto_btn = ctk.CTkButton(
            btn_row,
            text="Auto",
            fg_color=COLOR_CARD,
            text_color=COLOR_TEXT,
            hover_color=COLOR_BORDER,
            command=self._set_auto,
        )
        self._auto_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

    def _ui_set(self, var: tk.StringVar, value: str):
        try:
            self.root.after(0, var.set, value)
        except Exception:
            pass

    def _ui_call(self, fn, *args, **kwargs) -> None:
        try:
            self.root.after(0, lambda: fn(*args, **kwargs))
        except Exception:
            pass

    def _open_device_url(self) -> None:
        url = self._device_url
        if not url:
            return
        try:
            webbrowser.open(url)
        except Exception:
            pass

    def _set_hint(self, text: str, device_url: str | None = None) -> None:
        self._device_url = device_url
        self._ui_set(self.var_hint, text)
        def _btn_state():
            try:
                self._open_url_btn.configure(state="normal" if device_url else "disabled")
            except Exception:
                pass
        self._ui_call(_btn_state)

    def _set_power_bar(self, pct: int) -> None:
        try:
            self._power_bar.set(max(0.0, min(1.0, float(pct) / 100.0)))
        except Exception:
            pass

    def _set_controls_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        try:
            self._target_slider.configure(state=state)
        except Exception:
            pass
        for btn in (getattr(self, "_apply_btn", None), getattr(self, "_minus_half_btn", None), getattr(self, "_plus_half_btn", None)):
            if btn is None:
                continue
            try:
                btn.configure(state=state)
            except Exception:
                pass

    def _on_mode_changed(self, *_args) -> None:
        if self._suppress_mode_send:
            return
        mode = (self.var_mode.get() or "Auto").strip()
        if mode == "Manuell":
            self._ui_call(self._set_controls_enabled, True)
            self._ui_set(self.var_status, "Manuell")
            return
        # Auto selected
        self._set_auto()

    def _nudge_target(self, delta: float) -> None:
        try:
            current = float(self.var_target.get())
        except Exception:
            current = 20.0
        new_temp = max(12.0, min(30.0, current + delta))
        self.var_target.set(new_temp)
        self._on_target_slider(new_temp)

    def _on_target_slider(self, value) -> None:
        try:
            temp = float(value)
        except Exception:
            return
        # Display only; apply is explicit via button
        if abs(temp - round(temp)) < 0.01:
            self._ui_set(self.var_temp_soll, f"{temp:.0f} °C")
        else:
            self._ui_set(self.var_temp_soll, f"{temp:.1f} °C")

    def _apply_target_temperature(self) -> None:
        self._pending_apply_job = None
        if not (self.api and self.zone_id):
            return
        try:
            temp = float(self.var_target.get())
        except Exception:
            return
        try:
            self.api.set_temperature(self.zone_id, temp)
            self._ui_set(self.var_status, "Manuell")
            self._ui_set(self.var_mode, "Manuell")
            self._ui_call(self._set_controls_enabled, True)
        except Exception as e:
            self._ui_set(self.var_status, f"Fehler: {type(e).__name__}")

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

    def _change_temp(self, delta: int):
        """Legacy helper (kept for compatibility)."""
        self._nudge_target(float(delta))

    def _set_heating(self):
        """Aktiviere Heizung."""
        try:
            if self.api and self.zone_id:
                current = float(self.var_target.get())
                self.api.set_temperature(self.zone_id, current)
                self.var_status.set("Heizung aktiviert")
        except Exception:
            self.var_status.set("Fehler")

    def _set_auto(self):
        """Zurück auf Automatik/Plan (reset override)."""
        try:
            if self.api and self.zone_id:
                self.api.reset_zone_override(self.zone_id)
                self._ui_set(self.var_status, "Auto")
                self._ui_set(self.var_mode, "Auto")
                self._ui_call(self._set_controls_enabled, False)
        except Exception:
            self._ui_set(self.var_status, "Fehler")

    def _loop(self):
        """Hintergrund-Update Loop."""
        logging.info("[TADO] Loop gestartet")

        if Tado is None:
            self._ui_set(self.var_status, "python-tado nicht installiert")
            self._set_hint("Bitte `pip install python-tado` ausführen und Dashboard neu starten.")
            self._ui_set(self.var_temp_ist, "N/A")
            self._ui_set(self.var_humidity, "N/A")
            self._ui_call(self._set_controls_enabled, False)
            while self.alive:
                time.sleep(30)
            return

        # Login
        try:
            # Bevorzugt: direkter Login mit User/Pass (python-tado Standard)
            if TADO_USER and TADO_PASS:
                try:
                    self.api = Tado(TADO_USER, TADO_PASS)
                    self._ui_set(self.var_status, "Verbunden")
                except Exception:
                    self.api = Tado(TADO_USER, TADO_PASS, client_id=TADO_CLIENT_ID)
                    self._ui_set(self.var_status, "Verbunden")
            else:
                # OAuth Device Flow (seit 2025) + Token-Cache
                self.api = Tado(token_file_path=TADO_TOKEN_FILE)
                status = self.api.device_activation_status()
                logging.debug("[TADO] device_activation_status: %s", status)
                if status != "COMPLETED":
                    url = self._normalize_device_url(self.api.device_verification_url())
                    if url:
                        logging.info("[TADO] Device activation URL: %s", url)
                        self._ui_set(self.var_status, "Tado: Bitte Gerät im Browser aktivieren")
                        self._set_hint(f"Aktivierung erforderlich: {url}", device_url=url)

                    # Wait until flow is pending before activation
                    start = time.time()
                    while status == "NOT_STARTED" and (time.time() - start) < 10:
                        time.sleep(1)
                        status = self.api.device_activation_status()

                    if status == "PENDING":
                        self.api.device_activation()
                        status = self.api.device_activation_status()
                        logging.debug("[TADO] Status nach Aktivierung: %s", status)

                    if status != "COMPLETED":
                        self._ui_set(self.var_status, "Tado Aktivierung fehlgeschlagen")
                        self._set_hint("Aktivierung nicht abgeschlossen. Bitte später erneut versuchen.")
                        self._ui_set(self.var_temp_ist, "N/A")
                        self._ui_set(self.var_humidity, "N/A")
                        while self.alive:
                            time.sleep(30)
                        return
            
            zones = self.api.get_zones()
            logging.debug("[TADO] zones gefunden: %s", len(zones))
            self.zones = zones or []

            # Single-zone setup: pick best match (Schlaf/Bed) else first.
            picked = None
            for z in self.zones:
                name = (z.get("name") or "")
                if "schlaf" in name.lower() or "bed" in name.lower():
                    picked = z
                    break
            if picked is None and self.zones:
                picked = self.zones[0]

            if picked is not None:
                self.zone_id = picked.get("id")
                self._ui_set(self.var_zone, picked.get("name", "-"))

            if not self.zone_id:
                self._ui_set(self.var_status, "Tado: Keine Zone gefunden")
                self._ui_call(self._set_controls_enabled, False)
                while self.alive:
                    time.sleep(30)
                return
            
            self._ui_set(self.var_status, "Verbunden")
            self._set_hint("")
            # Start in Auto mode until we see an overlay
            self._ui_set(self.var_mode, "Auto")
            self._ui_call(self._set_controls_enabled, False)
        except ImportError:
            self._ui_set(self.var_status, "python-tado nicht installiert! Bitte im Terminal ausführen: 'pip install python-tado' (im .venv falls vorhanden). Dann Dashboard neu starten.")
            self._set_hint("Bitte `pip install python-tado` ausführen und Dashboard neu starten.")
            self._ui_set(self.var_temp_ist, "N/A")
            self._ui_set(self.var_humidity, "N/A")
            self._ui_call(self._set_controls_enabled, False)
            while self.alive:
                time.sleep(5)
            return
        except Exception as e:
            # Zeige Fehlername und ggf. Message für bessere Diagnose
            err_type = type(e).__name__
            err_msg = str(e)
            msg = f"Login fehlgeschlagen: {err_type}"
            if err_msg:
                msg += f" – {err_msg}"
            self._ui_set(self.var_status, msg)
            self._ui_set(self.var_temp_ist, "N/A")
            self._ui_set(self.var_humidity, "N/A")
            self._set_hint("Login/Verbindung fehlgeschlagen. Prüfe Zugangsdaten oder Device-Flow Aktivierung.")
            self._ui_call(self._set_controls_enabled, False)
            while self.alive:
                time.sleep(30)
            return

        # Update Loop
        while self.alive:
            try:
                state = self.api.get_zone_state(self.zone_id)
                state = self._state_to_dict(state)

                if not getattr(self, "_state_logged", False):
                    logging.debug("[TADO] zone_state keys: %s", list(state.keys()))
                    logging.debug("[TADO] sensorDataPoints keys: %s", list(state.get("sensorDataPoints", {}).keys()))
                    logging.debug("[TADO] activityDataPoints keys: %s", list(state.get("activityDataPoints", {}).keys()))
                    logging.debug("[TADO] setting keys: %s", list(state.get("setting", {}).keys()))
                    logging.debug("[TADO] overlay keys: %s", list(state.get("overlay", {}).keys()))
                    self._state_logged = True
                
                # Temperatur
                current = state.get("current_temp")
                if current is None:
                    current = self._get_nested(state, "sensorDataPoints", "insideTemperature", "celsius")
                if current is None:
                    current = self._get_nested(state, "sensorDataPoints", "insideTemperature", "value")
                if current is None:
                    current = self._get_nested(state, "insideTemperature", "celsius")
                if current is None:
                    current = self._get_nested(state, "setting", "temperature", "celsius")
                if current is None:
                    current = 0.0
                self._ui_set(self.var_temp_ist, f"{current:.1f} °C")
                
                # Feuchtigkeit
                humidity = state.get("current_humidity")
                if humidity is None:
                    humidity = self._get_nested(state, "sensorDataPoints", "humidity", "percentage")
                if humidity is None:
                    humidity = self._get_nested(state, "sensorDataPoints", "humidity", "value")
                if humidity is None:
                    humidity = 0.0
                self._ui_set(self.var_humidity, f"{humidity:.0f} %")
                
                # Zieltemperatur
                target = state.get("target_temp")
                overlay = state.get('overlay')
                setting = (overlay or {}).get("setting") or state.get("setting", {})
                if target is None and setting:
                    target = self._get_nested(setting, "temperature", "celsius")
                if target is None and setting:
                    target = 20

                # Mode: overlay present => manual override
                try:
                    manual = bool(overlay)
                except Exception:
                    manual = False
                try:
                    self._suppress_mode_send = True
                    self._ui_set(self.var_mode, "Manuell" if manual else "Auto")
                    self._ui_call(self._set_controls_enabled, manual)
                finally:
                    self._suppress_mode_send = False

                if target is not None:
                    if abs(float(target) - round(float(target))) < 0.01:
                        self._ui_set(self.var_temp_soll, f"{float(target):.0f} °C")
                    else:
                        self._ui_set(self.var_temp_soll, f"{float(target):.1f} °C")
                    # Update slider value without triggering a write-back
                    try:
                        self._suppress_target_send = True
                        self._ui_call(self.var_target.set, float(target))
                    finally:
                        self._suppress_target_send = False
                    
                power = state.get("power") or setting.get('power', 'OFF')
                if power == 'ON':
                    power_pct = state.get("heating_power_percentage")
                    if power_pct is None:
                        power_pct = self._get_nested(state, "activityDataPoints", "heatingPower", "percentage")
                    if power_pct is None:
                        power_pct = 75
                    self.var_power.set(int(power_pct))
                    self._ui_call(self._set_power_bar, int(power_pct))
                    self._ui_set(self.var_status, "Heizung aktiv")
                else:
                    self.var_power.set(0)
                    self._ui_call(self._set_power_bar, 0)
                    self._ui_set(self.var_status, "Auto" if not manual else "Manuell")
                if target is None:
                    self._ui_set(self.var_temp_soll, "-- °C")
                    self._ui_set(self.var_status, "Automatik")
                    
            except Exception as e:
                if not getattr(self, "_state_error_logged", False):
                    logging.warning("[TADO] zone_state error: %s: %s", type(e).__name__, e)
                    self._state_error_logged = True
            
            time.sleep(30)  # Update alle 30 Sekunden

