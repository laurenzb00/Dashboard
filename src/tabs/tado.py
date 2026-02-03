import threading
import os
import time
import tkinter as tk
from tkinter import ttk
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
    emoji,
)
from ui.components.card import Card

# --- KONFIGURATION ---
TADO_USER = os.getenv("TADO_USER")
TADO_PASS = os.getenv("TADO_PASS")
TADO_TOKEN_FILE = os.getenv(
    "TADO_TOKEN_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".tado_refresh_token"),
)

class TadoTab:
    """Klima-Steuerung mit modernem Card-Layout."""
    
    def __init__(self, root: tk.Tk, notebook: ttk.Notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True
        self.api = None
        self.zone_id = None
        print("[TADO] TadoTab initialized")
        
        # UI Variablen
        self.var_temp_ist = tk.StringVar(value="--.- °C")
        self.var_temp_soll = tk.StringVar(value="-- °C")
        self.var_humidity = tk.StringVar(value="-- %")
        self.var_status = tk.StringVar(value="Verbinde...")
        self.var_power = tk.IntVar(value=0)

        # Tab Frame
        self.tab_frame = tk.Frame(notebook, bg=COLOR_ROOT)
        notebook.add(self.tab_frame, text=emoji("🌡️ Klima", "Klima"))
        
        self.tab_frame.grid_columnconfigure(0, weight=1)
        self.tab_frame.grid_rowconfigure(2, weight=1)

        # Header mit Status
        header = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        
        ttk.Label(header, text="Schlafzimmer Klima", font=("Arial", 14, "bold")).pack(side=tk.LEFT)
        ttk.Label(header, textvariable=self.var_status, foreground=COLOR_SUBTEXT, font=("Arial", 9)).pack(side=tk.RIGHT)

        # Hauptgrid: 2 Cards nebeneinander
        content = tk.Frame(self.tab_frame, bg=COLOR_ROOT)
        content.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=1)

        # Card 1: Aktuelle Werte (IST)
        card1 = Card(content)
        card1.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=0)
        card1.add_title("Aktuelle Werte", icon="📊")
        
        # Temperatur (große Anzeige)
        temp_frame = tk.Frame(card1.content(), bg=COLOR_CARD)
        temp_frame.pack(fill=tk.X, pady=(0, 12))
        
        ttk.Label(temp_frame, text="Temperatur", font=("Arial", 10), foreground=COLOR_SUBTEXT).pack(anchor="w", padx=6, pady=(0, 2))
        ttk.Label(temp_frame, textvariable=self.var_temp_ist, font=("Arial", 32, "bold"), foreground=COLOR_PRIMARY).pack(anchor="w", padx=6)
        
        # Luftfeuchtigkeit
        hum_frame = tk.Frame(card1.content(), bg=COLOR_CARD)
        hum_frame.pack(fill=tk.X, pady=6)
        
        ttk.Label(hum_frame, text="Luftfeuchtigkeit", font=("Arial", 10), foreground=COLOR_SUBTEXT).pack(anchor="w", padx=6, pady=(0, 2))
        ttk.Label(hum_frame, textvariable=self.var_humidity, font=("Arial", 20, "bold")).pack(anchor="w", padx=6)
        
        # Heizleistung
        power_frame = tk.Frame(card1.content(), bg=COLOR_CARD)
        power_frame.pack(fill=tk.X, pady=6)
        
        ttk.Label(power_frame, text="Heizleistung", font=("Arial", 10), foreground=COLOR_SUBTEXT).pack(anchor="w", padx=6, pady=(0, 2))
        ttk.Progressbar(power_frame, variable=self.var_power, maximum=100, length=200).pack(fill=tk.X, padx=6)

        # Card 2: Steuerung
        card2 = Card(content)
        card2.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=0)
        card2.add_title("Steuerung", icon="⚙️")
        
        # Zieltemperatur Regler
        ctrl_label = ttk.Label(card2.content(), text="Zieltemperatur", font=("Arial", 10), foreground=COLOR_SUBTEXT)
        ctrl_label.pack(pady=(0, 8))
        
        ctrl_frame = tk.Frame(card2.content(), bg=COLOR_CARD)
        ctrl_frame.pack(pady=12)
        
        minus_btn = ttk.Button(ctrl_frame, text="−", width=3, command=lambda: self._change_temp(-1))
        minus_btn.pack(side=tk.LEFT, padx=8)
        
        ttk.Label(ctrl_frame, textvariable=self.var_temp_soll, font=("Arial", 28, "bold"), foreground=COLOR_WARNING).pack(side=tk.LEFT, padx=16)
        
        plus_btn = ttk.Button(ctrl_frame, text="+", width=3, command=lambda: self._change_temp(+1))
        plus_btn.pack(side=tk.LEFT, padx=8)
        
        # Buttons
        btn_frame = tk.Frame(card2.content(), bg=COLOR_CARD)
        btn_frame.pack(fill=tk.X, pady=12)
        
        ttk.Button(btn_frame, text="Heizen", command=self._set_heating).pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)
        ttk.Button(btn_frame, text="Aus", command=self._set_off).pack(side=tk.LEFT, padx=3, fill=tk.X, expand=True)

        # Temperatur-Historie (matplotlib)
        try:
            history_card = Card(self.tab_frame)
            history_card.grid(row=2, column=0, sticky="nsew", padx=12, pady=(0, 12))
            history_card.add_title("Temperatur-Verlauf (24h)", icon="📈")
            
            import matplotlib.pyplot as plt
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            
            fig, ax = plt.subplots(figsize=(10, 2.5), dpi=80)
            fig.patch.set_facecolor(COLOR_CARD)
            ax.set_facecolor(COLOR_CARD)
            for spine in ["top", "right"]:
                ax.spines[spine].set_visible(False)
            for spine in ["left", "bottom"]:
                ax.spines[spine].set_color(COLOR_BORDER)
            
            self.history_fig = fig
            self.history_ax = ax
            self.history_canvas = FigureCanvasTkAgg(fig, master=history_card.content())
            self.history_canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.history_temps = []  # Liste für Temperatur-Historie
        except Exception as e:
            print(f"[TADO] History chart error: {e}")
            self.history_canvas = None

        # Start Update Loop
        self.root.after(0, lambda: threading.Thread(target=self._loop, daemon=True).start())

    def stop(self):
        self.alive = False

    def _ui_set(self, var: tk.StringVar, value: str):
        try:
            self.root.after(0, var.set, value)
        except Exception:
            pass

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

    def _change_temp(self, delta: int):
        """Ändere Zieltemperatur um delta Grad."""
        try:
            current = float(self.var_temp_soll.get().split()[0])
            new_temp = max(12, min(30, current + delta))  # Begrenzt 12-30°C
            self.var_temp_soll.set(f"{new_temp:.0f} °C")
            if self.api and self.zone_id:
                self.api.set_temperature(self.zone_id, new_temp)
        except Exception:
            pass

    def _set_heating(self):
        """Aktiviere Heizung."""
        try:
            if self.api and self.zone_id:
                current = float(self.var_temp_soll.get().split()[0])
                self.api.set_temperature(self.zone_id, current)
                self.var_status.set("Heizung aktiviert")
        except Exception:
            self.var_status.set("Fehler")

    def _set_off(self):
        """Deaktiviere Heizung."""
        try:
            if self.api and self.zone_id:
                self.api.reset_zone_override(self.zone_id)
                self.var_status.set("Heizung aus")
        except Exception:
            self.var_status.set("Fehler")

    def _loop(self):
        """Hintergrund-Update Loop."""
        print("[TADO] Loop started")
        # Login
        try:
            from PyTado.interface import Tado

            # OAuth Device Flow (seit 2025) + Token-Cache
            self.api = Tado(token_file_path=TADO_TOKEN_FILE)
            status = self.api.device_activation_status()
            print(f"[TADO] device_activation_status: {status}")
            if status != "COMPLETED":
                url = self.api.device_verification_url()
                if url:
                    print(f"[TADO] Device activation URL: {url}")
                    self._ui_set(self.var_status, "Tado: Bitte Gerät im Browser aktivieren")

                # Wait until flow is pending before activation
                start = time.time()
                while status == "NOT_STARTED" and (time.time() - start) < 10:
                    time.sleep(1)
                    status = self.api.device_activation_status()

                if status == "PENDING":
                    self.api.device_activation()
                    status = self.api.device_activation_status()
                    print(f"[TADO] device_activation_status after activation: {status}")

                if status != "COMPLETED":
                    self._ui_set(self.var_status, "Tado Aktivierung fehlgeschlagen")
                    self._ui_set(self.var_temp_ist, "N/A")
                    self._ui_set(self.var_humidity, "N/A")
                    while self.alive:
                        time.sleep(30)
                    return
            
            zones = self.api.get_zones()
            print(f"[TADO] zones: {len(zones)}")
            for z in zones:
                if "Schlaf" in z.get('name', '') or "Bed" in z.get('name', ''):
                    self.zone_id = z.get('id')
                    break
            
            if not self.zone_id and zones:
                self.zone_id = zones[0].get('id')

            if not self.zone_id:
                self._ui_set(self.var_status, "Tado: Keine Zone gefunden")
                while self.alive:
                    time.sleep(30)
                return
            
            self._ui_set(self.var_status, "Verbunden")
        except ImportError:
            self._ui_set(self.var_status, "PyTado nicht installiert")
            self._ui_set(self.var_temp_ist, "N/A")
            self._ui_set(self.var_humidity, "N/A")
            while self.alive:
                time.sleep(5)
            return
        except Exception as e:
            self._ui_set(self.var_status, f"Login fehlgeschlagen: {type(e).__name__}")
            self._ui_set(self.var_temp_ist, "N/A")
            self._ui_set(self.var_humidity, "N/A")
            while self.alive:
                time.sleep(30)
            return

        # Update Loop
        while self.alive:
            try:
                state = self.api.get_zone_state(self.zone_id)
                state = self._state_to_dict(state)

                if not getattr(self, "_state_logged", False):
                    print("[TADO] zone_state keys:", list(state.keys()))
                    print("[TADO] sensorDataPoints keys:", list(state.get("sensorDataPoints", {}).keys()))
                    print("[TADO] activityDataPoints keys:", list(state.get("activityDataPoints", {}).keys()))
                    print("[TADO] setting keys:", list(state.get("setting", {}).keys()))
                    print("[TADO] overlay keys:", list(state.get("overlay", {}).keys()))
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

                if target is not None:
                    self._ui_set(self.var_temp_soll, f"{target:.0f} °C")
                    
                power = state.get("power") or setting.get('power', 'OFF')
                if power == 'ON':
                    power_pct = state.get("heating_power_percentage")
                    if power_pct is None:
                        power_pct = self._get_nested(state, "activityDataPoints", "heatingPower", "percentage")
                    if power_pct is None:
                        power_pct = 75
                    self.var_power.set(int(power_pct))
                    self._ui_set(self.var_status, "Heizung aktiv")
                else:
                    self.var_power.set(0)
                    self._ui_set(self.var_status, "Heizung aus")
                if target is None:
                    self._ui_set(self.var_temp_soll, "-- °C")
                    self._ui_set(self.var_status, "Automatik")
                
                # Update history chart
                try:
                    if current and self.history_canvas:
                        self.history_temps.append(current)
                        if len(self.history_temps) > 288:  # 24h * 5min = 288 punkte
                            self.history_temps = self.history_temps[-288:]
                        self._update_history_chart()
                except Exception:
                    pass
                    
            except Exception as e:
                if not getattr(self, "_state_error_logged", False):
                    print(f"[TADO] zone_state error: {type(e).__name__}: {e}")
                    self._state_error_logged = True
            
            time.sleep(30)  # Update alle 30 Sekunden
    
    def _update_history_chart(self):
        """Update 24h temperature chart."""
        try:
            if not hasattr(self, 'history_canvas') or not self.history_canvas:
                return
            
            self.history_ax.clear()
            
            if len(self.history_temps) > 1:
                # Plot min/max area + current line
                import numpy as np
                temps = np.array(self.history_temps)
                x = np.arange(len(temps))
                
                self.history_ax.plot(x, temps, color=COLOR_PRIMARY, linewidth=2, label="Temperatur")
                self.history_ax.fill_between(x, temps, alpha=0.2, color=COLOR_PRIMARY)
                self.history_ax.set_ylabel("°C", color=COLOR_TEXT, fontsize=9)
                self.history_ax.tick_params(axis="y", colors=COLOR_TEXT, labelsize=8)
                self.history_ax.tick_params(axis="x", colors=COLOR_SUBTEXT, labelsize=7)
                self.history_ax.grid(True, alpha=0.2, axis="y")
                self.history_ax.set_ylim(min(temps) - 2, max(temps) + 2)
            else:
                self.history_ax.text(0.5, 0.5, "Keine Historie", ha="center", va="center",
                                   transform=self.history_ax.transAxes, color=COLOR_SUBTEXT)
            
            self.history_fig.tight_layout(pad=0.4)
            try:
                self.history_canvas.draw_idle()
            except:
                pass
        except Exception as e:
            print(f"[TADO] Chart update error: {e}")
