import tkinter as tk
import customtkinter as ctk
import os

DEBUG_LOG = os.environ.get("DASHBOARD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
SHOW_STATUS_TAB = os.environ.get("DASHBOARD_HIDE_STATUS_TAB", "").strip().lower() not in ("1", "true", "yes", "on")


def _dbg_print(msg: str) -> None:
    if DEBUG_LOG:
        print(msg, flush=True)
import platform
import shutil
import subprocess
from datetime import datetime, timedelta
import logging
import time
import threading
import sys
from tkinter import ttk

# Füge parent-Verzeichnis (src/) zu Python-Pfad hinzu
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.styles import (
    init_style,
    COLOR_ROOT,
    COLOR_HEADER,
    COLOR_PRIMARY,
    COLOR_TEXT,
    COLOR_BORDER,
    COLOR_CARD,
    COLOR_SUBTEXT,
    emoji,
    EMOJI_OK,
)
from ui.components.card import Card
from ui.components.header import HeaderBar
from ui.components.statusbar import StatusBar
from ui.components.rounded import RoundedFrame
from ui.views.energy_flow import EnergyFlowView
from ui.views.buffer_storage import BufferStorageView

from core.datastore import DataStore, get_shared_datastore

_UI_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_UI_DIR)
_PROJECT_ROOT = os.path.dirname(_SRC_DIR)
def safe_get_datastore() -> DataStore | None:
    try:
        return get_shared_datastore()
    except Exception as exc:
        logging.error("[DB] DataStore nicht erreichbar: %s", exc)
        return None


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


try:
    from tabs.historical import HistoricalTab
except ImportError:
    HistoricalTab = None
try:
    from tabs.ertrag import ErtragTab
except ImportError:
    ErtragTab = None


# Spotify Tab mit integriertem OAuth
try:
    from tabs.spotify import SpotifyTab
except Exception as e:
    print(f"[SPOTIFY-IMPORT-ERROR] {e}")
    SpotifyTab = None

try:
    from tabs.tado import TadoTab
except ImportError:
    TadoTab = None

try:
    from tabs.hue import HueTab
except ImportError:
    HueTab = None

try:
    from tabs.system import SystemTab
except ImportError:
    SystemTab = None

try:
    from tabs.calendar import CalendarTab
except ImportError:
    CalendarTab = None

try:
    from tabs.analyse import AnalyseTab
except ImportError:
    AnalyseTab = None

# StatusTab importieren
try:
    from tabs.status import StatusTab
except ImportError:
    StatusTab = None


class TabviewWrapper:
    """Wrapper für CTkTabview, der die alte ttk.Notebook API emuliert."""
    def __init__(self, tabview: ctk.CTkTabview):
        self._tabview = tabview
        self._tabs = {}  # tab_name -> frame
    
    @property
    def tk(self):
        """Tkinter root für Kompatibilität mit Tabs."""
        return self._tabview.winfo_toplevel().tk
    
    def add(self, frame, text=""):
        """Emuliert notebook.add(frame, text='...')"""
        # Erstelle Tab in CTkTabview
        self._tabview.add(text)
        # Hole das Tab-Frame von CTkTabview
        tab_frame = self._tabview.tab(text)
        # Kopiere frame-Inhalt in tab_frame
        frame.pack(in_=tab_frame, fill=tk.BOTH, expand=True)
        self._tabs[text] = frame
        return text
    
    def tabs(self):
        """Gibt Liste aller Tab-Namen zurück."""
        return list(self._tabs.keys())
    
    def forget(self, tab_id):
        """Entfernt einen Tab (für CTkTabview nicht implementiert)."""
        pass



class MainApp:
    def build_tabs(self):
        """Robustly rebuilds all tabs, ensuring correct references after UI changes (fullscreen, etc)."""
        # CTkTabview: Tabs können nicht dynamisch entfernt werden, daher nur _add_other_tabs aufrufen
        # Dashboard-Tab wurde bereits in __init__ erstellt
        try:
            self._add_other_tabs()
        except Exception as e:
            print(f"[build_tabs] Error adding tabs: {e}")
        # Ensure all tab references are up to date
        if hasattr(self, 'historical_tab') and self.historical_tab:
            _dbg_print("[build_tabs] historical_tab is set.")
        else:
            _dbg_print("[build_tabs] historical_tab is None!")
        if hasattr(self, 'spotify_tab') and self.spotify_tab:
            _dbg_print("[build_tabs] spotify_tab is set.")
        if hasattr(self, 'hue_tab') and self.hue_tab:
            _dbg_print("[build_tabs] hue_tab is set.")

    def _start_ertrag_validator(self):
        """Starte wöchentliche Ertrag-Validierung im Hintergrund."""
        def validate_loop():
            try:
                from core.ertrag_validator import validate_and_repair_ertrag
                _dbg_print("[ERTRAG] Validation beim Start...")
                validate_and_repair_ertrag(self.datastore, verbose=DEBUG_LOG)

                def _refresh_tabs() -> None:
                    try:
                        if hasattr(self, 'ertrag_tab') and self.ertrag_tab:
                            self.ertrag_tab._last_key = None
                            self.ertrag_tab._update_plot()
                        if hasattr(self, 'historical_tab') and self.historical_tab:
                            self.historical_tab._last_key = None
                            self.historical_tab._update_plot()
                    except Exception:
                        logging.exception("[ERTRAG] Tab refresh after validation failed")

                # Tkinter/UI updates must run on main thread
                try:
                    self.root.after(0, _refresh_tabs)
                except Exception:
                    pass
            except Exception as e:
                print(f"[ERTRAG] Validator nicht verfügbar: {e}")
            # Dann jede Woche wiederholen (7 Tage = 604800 Sekunden)
            while True:
                time.sleep(7 * 24 * 3600)  # 1 Woche
                try:
                    from core.ertrag_validator import validate_and_repair_ertrag
                    _dbg_print("[ERTRAG] Wöchentliche Validierung...")
                    validate_and_repair_ertrag(self.datastore, verbose=DEBUG_LOG)

                    def _refresh_tabs_weekly() -> None:
                        try:
                            if hasattr(self, 'ertrag_tab') and self.ertrag_tab:
                                self.ertrag_tab._last_key = None
                                self.ertrag_tab._update_plot()
                            if hasattr(self, 'historical_tab') and self.historical_tab:
                                self.historical_tab._last_key = None
                                self.historical_tab._update_plot()
                        except Exception:
                            logging.exception("[ERTRAG] Weekly tab refresh failed")

                    try:
                        self.root.after(0, _refresh_tabs_weekly)
                    except Exception:
                        pass
                except Exception as e:
                    print(f"[ERTRAG] Fehler bei wöchentlicher Validierung: {e}")
        validator_thread = threading.Thread(target=validate_loop, daemon=True)
        validator_thread.start()

    def update_tick(self):
        """Zentrale UI-Update-Schleife: holt aktuelle Daten, aktualisiert Widgets und Status."""
        import time
        data = {}
        self._tick_count += 1
        now = time.time()
        try:
            # --- Datenquellen: Letzte Timestamps holen ---
            pv = self.datastore.get_last_fronius_record() or {}
            heat = self.datastore.get_last_heating_record() or {}
            data.update(pv)
            data.update(heat)

            # --- Rate-limitiertes Debug-Logging (alle 2s) ---
            if not hasattr(self, '_dbg_last_data'):
                self._dbg_last_data = 0.0
            if now - self._dbg_last_data > 2.0:
                keys = [
                    "pv_power_kw",
                    "grid_power_kw",
                    "battery_power_kw",
                    "battery_soc_pct",
                    "bmk_kessel_c",
                    "bmk_warmwasser_c",
                    "buf_top_c",
                    "buf_mid_c",
                    "buf_bottom_c",
                ]
                if DEBUG_LOG:
                    print("[DATA_KEYS]", {k: data.get(k, "MISSING") for k in keys}, flush=True)
                self._dbg_last_data = now

            # --- Widgets/Diagramme updaten (nur im MainThread!) ---
            if hasattr(self, 'energy_view'):
                self.energy_view.update_data(data)
            if hasattr(self, 'buffer_view'):
                try:
                    self.buffer_view.update_data(data)
                except Exception:
                    logging.exception("buffer_view update_data failed")
            if hasattr(self, 'historical_tab'):
                try:
                    self.historical_tab.update_data(data)
                except Exception:
                    logging.exception("historical_tab update_data failed")

            # --- Debug-Statusanzeige (unten rechts in Statusbar) ---
            last_update = datetime.now().strftime('%H:%M:%S')
            pv_ts = pv.get("timestamp")
            heat_ts = heat.get("timestamp")
            pv_age = None
            heat_age = None
            if pv_ts:
                try:
                    pv_age = round(now - self._parse_ts(pv_ts))
                except Exception:
                    pv_age = None
            if heat_ts:
                try:
                    heat_age = round(now - self._parse_ts(heat_ts))
                except Exception:
                    heat_age = None
            status_str = f"BMK age: {heat_age if heat_age is not None else '-'}s | Fronius age: {pv_age if pv_age is not None else '-'}s | last update: {last_update}"
            if hasattr(self, 'status'):
                self.status.update_status(status_str)

            # --- Rate-limitiertes Logging (alle 10s) ---
            if not hasattr(self, '_last_log_tick'):
                self._last_log_tick = 0
            if now - self._last_log_tick > 10:
                from core.schema import (
                    PV_POWER_KW,
                    GRID_POWER_KW,
                    BATTERY_POWER_KW,
                    BATTERY_SOC_PCT,
                    BMK_KESSEL_C,
                    BMK_WARMWASSER_C,
                    BUF_TOP_C,
                )
                logging.info(
                    f"latest values: PV {data.get(PV_POWER_KW, 0)}kW, Grid {data.get(GRID_POWER_KW, 0)}kW, "
                    f"Batt {data.get(BATTERY_POWER_KW, 0)}kW, SOC {data.get(BATTERY_SOC_PCT, 0)}%, "
                    f"Kessel {data.get(BMK_KESSEL_C, 0)}°C, Warmwasser {data.get(BMK_WARMWASSER_C, 0)}°C, "
                    f"PufferTop {data.get(BUF_TOP_C, 0)}°C"
                )
                self._last_log_tick = now
        except Exception:
            logging.exception("update_tick failed")
        # Tick erneut einplanen
        self.root.after(500, self.update_tick)

    # DEPRECATED: _loop() is no longer used. All updates handled by update_tick().
    def _loop(self):
        pass

    # DEPRECATED: handle_bmkdaten_data() is no longer used.
    def handle_bmkdaten_data(self, data: dict):
        pass

    # DEPRECATED: _fetch_real_data() is no longer used.
    def _fetch_real_data(self):
        pass

    @staticmethod
    def _parse_ts(ts):
        # Erwartet ISO-8601-String mit expliziter Zeitzone (Europe/Vienna)
        from datetime import datetime
        if not ts:
            return 0
        try:
            return datetime.fromisoformat(str(ts)).timestamp()
        except Exception:
            return 0

    def __init__(self, root: ctk.CTk, datastore: DataStore | None = None):
        _dbg_print("[INIT] MainApp: Initialisierung gestartet")
        self._start_time = time.time()
        _dbg_print("[INIT] MainApp: Zeitstempel gesetzt")
        self._debug_log = os.getenv("DASH_DEBUG", "0") == "1"
        self._configure_debounce_id = None
        self._last_size = (0, 0)
        self._resize_enabled = False
        self.root = root
        _dbg_print("[INIT] MainApp: CustomTkinter root gesetzt")
        self.root.title("Smart Home Dashboard")
        self._tick_count = 0
        self._dbg_last_dump = 0.0  # Für Debug-Logging der Daten-Keys

        # Shared DataStore wird beim Start bereitgestellt
        _dbg_print("[INIT] MainApp: DataStore wird geladen...")
        self.datastore = datastore or safe_get_datastore()
        _dbg_print(f"[INIT] MainApp: DataStore geladen: {type(self.datastore)}")

        # Health-Status für Datenquellen (PV, Heizung)
        self._source_health = {
            "pv": {"label": "PV", "ts": None, "count": 0},
            "heating": {"label": "Heizung", "ts": None, "count": 0},
        }

        self._data_fresh_seconds = None
        self._last_status_compact = ""
        self._last_data_dump_ts = 0.0  # Für rate-limitiertes Daten-Logging

        # Fix: Initialisiere self._last_data mit final keys
        self._last_data = {
            "pv_power_kw": 0,
            "grid_power_kw": 0,
            "battery_power_kw": 0,
            "battery_soc_pct": 0,
            "bmk_kessel_c": 0,
            "bmk_warmwasser_c": 0,
            "buf_top_c": 0,
            "buf_mid_c": 0,
            "buf_bottom_c": 0,
        }

        # Define base header and status heights - moderner mit mehr Platz
        self._base_header_h = 55  # Kompakt aber mit Platz für alle Elemente
        self._base_status_h = 52  # Größer für bessere Buttons

        # Start weekly Ertrag validation in background
        self._start_ertrag_validator()

        # Debug: Bind Configure events
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.bind("<Map>", self._on_root_map)
        # Fix DPI scaling and force a true 1024x600 borderless fullscreen
        try:
            self.root.tk.call("tk", "scaling", 1.0)
        except Exception:
            pass
        sw = max(1, self.root.winfo_screenwidth())
        sh = max(1, self.root.winfo_screenheight())
        target_w = min(sw, 1024)
        target_h = min(sh, 600)
        # Minimaler Offset, aber maximale nutzbare Höhe
        offset_y = 0
        usable_h = max(200, target_h - offset_y)
        self.is_fullscreen = True
        self.root.resizable(False, False)
        try:
            self.root.attributes("-fullscreen", True)
            self.root.attributes("-zoomed", True)
        except Exception:
            pass
        init_style(self.root)
        self._ensure_emoji_font()
        self._status_icon_ok, self._status_icon_warn = self._resolve_status_icons()

        self._base_energy_h = 230
        self._base_buffer_h = 180
        
        # Haupt-Container Frame mit COLOR_ROOT Hintergrund (bedeckt gesamtes root)
        self.main_container = ctk.CTkFrame(self.root, fg_color=COLOR_ROOT, corner_radius=0, border_width=0)
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self.main_container.grid_rowconfigure(0, minsize=self._base_header_h)
        self.main_container.grid_rowconfigure(1, weight=1)
        self.main_container.grid_rowconfigure(2, minsize=self._base_status_h)
        self.main_container.grid_columnconfigure(0, weight=1)

        # Header - modernerer Style mit mehr Höhe
        _dbg_print("[INIT] MainApp: HeaderBar wird erstellt...")
        self.header = HeaderBar(
            self.main_container,
            on_toggle_a=self.on_toggle_a,
            on_toggle_b=self.on_toggle_b,
            on_exit=self.on_exit,
        )
        self.header.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        _dbg_print("[INIT] MainApp: HeaderBar erstellt und platziert.")

        # Start periodic header update for date/time
        self._update_header_datetime()

        # CTkTabview (Tabs) - moderner mit besserem Spacing
        _dbg_print("[INIT] MainApp: CustomTkinter Tabview wird erstellt...")
        self.notebook_container = ctk.CTkFrame(self.main_container, fg_color=COLOR_ROOT, corner_radius=0, border_width=0)
        self.notebook_container.grid(row=1, column=0, sticky="nsew", padx=0, pady=0)
        
        self.tabview = ctk.CTkTabview(
            self.notebook_container, 
            fg_color=COLOR_ROOT,
            border_color=COLOR_ROOT,
            segmented_button_fg_color=COLOR_ROOT,
            segmented_button_selected_color=COLOR_PRIMARY,
            segmented_button_selected_hover_color=COLOR_PRIMARY,
            segmented_button_unselected_color=COLOR_CARD,
            segmented_button_unselected_hover_color=COLOR_BORDER,
            text_color=COLOR_TEXT,
            text_color_disabled=COLOR_SUBTEXT,
            corner_radius=0,
            border_width=0
        )
        self.tabview.pack(fill=tk.BOTH, expand=True)
        self._style_tabview_buttons()
        # Backward-Compat Wrapper für alte notebook.add() API
        self.notebook = TabviewWrapper(self.tabview)

        # Energy Dashboard Tab
        _dbg_print("[INIT] MainApp: Dashboard-Tab wird erstellt...")
        self.tabview.add(emoji("⚡ Energie", "Energie"))
        self.dashboard_tab = self.tabview.tab(emoji("⚡ Energie", "Energie"))
        # Setze Tab-Frame Hintergrund explizit auf COLOR_ROOT
        try:
            self.dashboard_tab.configure(fg_color=COLOR_ROOT)
        except:
            pass
        _dbg_print("[INIT] MainApp: Dashboard-Tab hinzugefügt.")

        # Body (Energy + Buffer)
        _dbg_print("[INIT] MainApp: Body-Frame für Dashboard wird erstellt...")
        self.body = ctk.CTkFrame(self.dashboard_tab, fg_color=COLOR_ROOT, corner_radius=0, border_width=0)
        self.body.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        self.body.grid_columnconfigure(0, weight=4)  # Mehr Platz für Energiefluss
        self.body.grid_columnconfigure(1, weight=1, minsize=320)  # Schmalere Heatmap-Spalte
        self.body.grid_rowconfigure(0, weight=1)

        # Energy Card (60:40 Grid) - flexible Größe
        _dbg_print("[INIT] MainApp: EnergyCard und EnergyView werden erstellt...")
        self.energy_card = Card(self.body, padding=0)
        self.energy_card.grid(row=0, column=0, sticky="nsew", padx=0, pady=0)
        self.energy_card.add_title("Energiefluss", icon="⚡")
        self.energy_view = EnergyFlowView(self.energy_card.content(), width=200, height=180)
        self.energy_view.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Buffer Card (60:40 Grid) - flexible Größe
        _dbg_print("[INIT] MainApp: BufferCard und BufferView werden erstellt...")
        self.buffer_card = Card(self.body, padding=0)
        self.buffer_card.grid(row=0, column=1, sticky="nsew", padx=0, pady=0)
        self.buffer_card.add_title("Warmwasser", icon="🔥")
        self.buffer_view = BufferStorageView(self.buffer_card.content(), height=280, datastore=self.datastore)
        self.buffer_view.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Statusbar - moderner Style mit besserem Spacing
        self.status = StatusBar(self.main_container, on_exit=self.root.quit, on_toggle_fullscreen=self.toggle_fullscreen)
        self.status.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        self._apply_fullscreen()
        self.build_tabs()
        self.root.after(500, self.update_tick)

    def _update_header_datetime(self):
        now = datetime.now()
        date_text = now.strftime("%d.%m.%Y")
        weekday = now.strftime("%A")
        time_text = now.strftime("%H:%M")
        heat = self.datastore.get_last_heating_record() or {}
        norm = self.datastore.normalize_heating_record(heat, stale_minutes=5)
        if norm['is_stale'] or norm['outdoor'] is None:
            out_temp = "--.- °C"
        else:
            out_temp = f"{norm['outdoor']:.1f} °C"
        self.header.update_header(date_text, weekday, time_text, out_temp)
        self.root.after(1000, self._update_header_datetime)

    def _style_tabview_buttons(self) -> None:
        """Make the active tab more readable and improve contrast."""
        try:
            segmented = getattr(self.tabview, "_segmented_button", None)
            if segmented is None:
                return
            segmented.configure(
                font=("Segoe UI", 12, "bold"),
                height=36,
                corner_radius=14,
                border_width=1,
                border_color=COLOR_BORDER,
                fg_color=COLOR_CARD,
                unselected_color=COLOR_CARD,
                unselected_hover_color=COLOR_BORDER,
                selected_color=COLOR_PRIMARY,
                selected_hover_color=COLOR_PRIMARY,
                text_color=COLOR_TEXT,
                text_color_disabled=COLOR_SUBTEXT,
            )
        except Exception:
            pass

    # PV Status Tab und zugehörige Methoden entfernt, ersetzt durch StatusTab

    def _add_other_tabs(self):
        _dbg_print("[TABS] Starte Initialisierung aller weiteren Tabs...")
        """Integriert alle weiteren Tabs, StatusTab immer als letzter Tab (rechts)."""
        
        # Hue Tab (direkt mit CTk Tabview)
        if HueTab:
            try:
                _dbg_print("[TABS] HueTab wird erstellt...")
                # Tab in Tabview erstellen
                self.tabview.add(emoji("💡 Licht", "Licht"))
                hue_frame = self.tabview.tab(emoji("💡 Licht", "Licht"))
                # Setze Frame Hintergrund
                try:
                    hue_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                # HueTab initialisieren mit direktem Frame
                self.hue_tab = HueTab(self.root, self.notebook, tab_frame=hue_frame)
                _dbg_print("[TABS] HueTab erfolgreich hinzugefügt.")
            except Exception as e:
                print(f"[ERROR] HueTab initialization failed: {e}")
                import traceback
                traceback.print_exc()
                self.hue_tab = None
        
        # Andere Tabs (Portierung zu CustomTkinter fortlaufend)
        if SpotifyTab:
            try:
                _dbg_print("[TABS] SpotifyTab wird erstellt...")
                self.tabview.add("Spotify")
                spotify_frame = self.tabview.tab("Spotify")
                try:
                    spotify_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.spotify_tab = SpotifyTab(self.root, self.notebook, tab_frame=spotify_frame)
                _dbg_print("[TABS] SpotifyTab erfolgreich hinzugefügt.")
            except Exception as e:
                print(f"[ERROR] SpotifyTab initialization failed: {e}")
                self.spotify_tab = None

        if TadoTab:
            try:
                _dbg_print("[TABS] TadoTab wird erstellt...")
                self.tabview.add(emoji("🌡️ Raumtemperatur", "Raumtemperatur"))
                tado_frame = self.tabview.tab(emoji("🌡️ Raumtemperatur", "Raumtemperatur"))
                try:
                    tado_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.tado_tab = TadoTab(self.root, self.notebook, tab_frame=tado_frame)
                _dbg_print("[TABS] TadoTab erfolgreich hinzugefügt.")
            except Exception as e:
                print(f"[ERROR] TadoTab initialization failed: {e}")
                self.tado_tab = None
        else:
            _dbg_print(f"[TABS] TadoTab nicht verfügbar (Import fehlgeschlagen)")

        if CalendarTab:
            try:
                _dbg_print("[TABS] CalendarTab wird erstellt...")
                self.tabview.add(emoji("📅 Kalender", "Kalender"))
                calendar_frame = self.tabview.tab(emoji("📅 Kalender", "Kalender"))
                try:
                    calendar_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.calendar_tab = CalendarTab(self.root, self.notebook, tab_frame=calendar_frame)
                _dbg_print("[TABS] CalendarTab erfolgreich hinzugefügt.")
            except Exception as e:
                print(f"[ERROR] CalendarTab init failed: {e}")
                self.calendar_tab = None

        if HistoricalTab:
            try:
                _dbg_print("[TABS] HistoricalTab wird erstellt...")
                self.tabview.add(emoji("📈 Historie", "Historie"))
                historical_frame = self.tabview.tab(emoji("📈 Historie", "Historie"))
                try:
                    historical_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.historical_tab = HistoricalTab(self.root, self.notebook, datastore=self.datastore, tab_frame=historical_frame)
                _dbg_print("[TABS] HistoricalTab erfolgreich hinzugefügt.")
            except Exception as e:
                print(f"[ERROR] HistoricalTab init failed: {e}")
                self.historical_tab = None

        if ErtragTab:
            try:
                _dbg_print("[TABS] ErtragTab wird erstellt...")
                self.tabview.add(emoji("🔆 Ertrag", "Ertrag"))
                ertrag_frame = self.tabview.tab(emoji("🔆 Ertrag", "Ertrag"))
                try:
                    ertrag_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.ertrag_tab = ErtragTab(self.root, self.notebook, tab_frame=ertrag_frame)
                _dbg_print("[TABS] ErtragTab erfolgreich hinzugefügt.")
            except Exception as e:
                print(f"[ERROR] ErtragTab init failed: {e}")
                self.ertrag_tab = None

        # SystemTab soll vorletzter Tab sein
        if SystemTab:
            try:
                _dbg_print("[TABS] SystemTab wird erstellt...")
                self.tabview.add(emoji("⚙️ System", "System"))
                system_frame = self.tabview.tab(emoji("⚙️ System", "System"))
                try:
                    system_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.system_tab = SystemTab(self.root, self.notebook, tab_frame=system_frame)
                _dbg_print("[TABS] SystemTab erfolgreich hinzugefügt.")
            except Exception as e:
                print(f"[ERROR] SystemTab init failed: {e}")
                self.system_tab = None

        # StatusTab immer als letzter Tab (rechts)
        if StatusTab and SHOW_STATUS_TAB:
            try:
                _dbg_print("[TABS] StatusTab wird erstellt...")
                self.tabview.add("Status")
                status_frame = self.tabview.tab("Status")
                try:
                    status_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.status_tab = StatusTab(self.root, tab_frame=status_frame)
                _dbg_print("[TABS] StatusTab erfolgreich hinzugefügt.")
            except Exception as e:
                print(f"[ERROR] StatusTab init failed: {e}")
                self.status_tab = None
        elif StatusTab:
            _dbg_print("[TABS] StatusTab ausgeblendet (DASHBOARD_HIDE_STATUS_TAB=1 zum Ausblenden aktiv)")
        _dbg_print("[TABS] Alle weiteren Tabs wurden verarbeitet.")

    # --- Callbacks ---
    def on_toggle_a(self):
        self.status.update_status("Hue: Alle an")
        try:
            if hasattr(self, "hue_tab") and self.hue_tab:
                self.hue_tab._threaded_group_cmd(True)
        except Exception:
            pass

    def on_toggle_b(self):
        self.status.update_status("Hue: Alle aus")
        try:
            if hasattr(self, "hue_tab") and self.hue_tab:
                self.hue_tab._threaded_group_cmd(False)
        except Exception:
            pass

    def on_exit(self):
        self.status.update_status("Beende...")
        # Cleanup DataStore

    def _apply_fullscreen(self):
        """Setzt echtes Vollbild (ohne overrideredirect) und zentriert das Fenster."""
        try:
            self.root.attributes("-fullscreen", True)
            self.is_fullscreen = True
            self.root.resizable(False, False)
            self.root.geometry("1024x600+0+0")
        except Exception:
            pass

    def _apply_windowed(self):
        """Setzt das Fenster in den Fenstermodus (kein Vollbild)."""
        try:
            self.root.attributes("-fullscreen", False)
            self.root.overrideredirect(False)
            self.root.resizable(True, True)
            w, h = 900, 540
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.is_fullscreen = False
        except Exception:
            pass

    # Duplicate _apply_windowed removed (F811)

    def toggle_fullscreen(self):
        """Wechselt zwischen Vollbild und Fenstermodus."""
        if getattr(self, 'is_fullscreen', False):
            print("[WINDOW] Wechsel zu Fenstermodus")
            self._apply_windowed()
            self.status.update_status("Fenstermodus")
        else:
            print("[FULLSCREEN] Wechsel zu Vollbild")
            self._apply_fullscreen()
            self.status.update_status("Vollbild")

    def minimize_window(self):
        """Minimiert das Fenster zuverlässig."""
        try:
            print("[MINIMIZE] Iconify window (normales Minimieren)...")
            self.root.iconify()
            self.status.update_status("Minimiert (Taskleiste/Alt+Tab)")
        except Exception as e:
            print(f"[MINIMIZE] Fehler: {e}")

    def _on_root_map(self, event):
        """Kein automatischer Fullscreen nach Minimieren/Alt+Tab, normales Fensterverhalten."""
        pass

    def _mark_layout_stable(self):
        """Mark layout as stable after initial settling period."""
        elapsed = time.time() - self._start_time
        if self._debug_log:
            print(f"[LAYOUT] Marked stable at {elapsed:.3f}s")
        self._layout_stable = True
        
    def _on_root_configure(self, event):
        """Debug: Log Configure events and debounce resize handling."""
        if event.widget != self.root:
            return
        if not getattr(self, "_layout_stable", False):
            return
        if not self._resize_enabled:
            return
        
        elapsed = time.time() - self._start_time
        new_size = (event.width, event.height)
        
        # Only log if size actually changed
        if new_size != self._last_size:
            print(f"[CONFIGURE] Root at {elapsed:.3f}s: {event.width}x{event.height}")
            self._last_size = new_size
            
            # Debounce: Cancel pending rescale, schedule new one after 350ms
            # (longer debounce = fewer spurious resizes during initial layout)
            if self._configure_debounce_id:
                self.root.after_cancel(self._configure_debounce_id)
            self._configure_debounce_id = self.root.after(350, lambda: self._handle_resize(event.width, event.height))

    def _apply_initial_sizing(self, w: int, h: int):
        """Apply initial sizing once at startup - no rescaling."""
        try:
            header_h = max(1, self.header.winfo_height())
            status_h = max(1, self.status.winfo_height())
            available = max(200, h - header_h - status_h - 6)
            body_h = max(1, self.body.winfo_height())
            
            # Set initial view heights without triggering complete redraw
            view_h = max(160, body_h - 28)
            
            print(f"[LAYOUT] Initial view height: {view_h}px (body: {body_h}, available: {available})")
        except Exception as e:
            print(f"[LAYOUT] Initial sizing failed: {e}")

    def _handle_resize(self, w: int, h: int):
        """Handle debounced resize events - only if size actually changed significantly."""
        elapsed = time.time() - self._start_time
        if self._debug_log:
            print(f"[RESIZE] Handling resize at {elapsed:.3f}s: {w}x{h}")
        
        try:
            header_h = max(1, self.header.winfo_height())
            status_h = max(1, self.status.winfo_height())
            available = max(200, h - header_h - status_h - 6)
            body_h = max(1, self.body.winfo_height())
            view_h = max(160, body_h - 28)
            
            # Only resize if change is significant (>10px)
            if hasattr(self, '_last_view_h') and abs(view_h - self._last_view_h) < 10:
                if self._debug_log:
                    print(f"[RESIZE] Skipping - change too small")
                return
            
            self._last_view_h = view_h
            
            if hasattr(self, "energy_view"):
                if self._debug_log:
                    print(f"[RESIZE] Resizing energy_view to height {view_h}")
                # DON'T use full resize - just update canvas size
                current_energy_h = self.energy_view.canvas.winfo_height()
                if abs(current_energy_h - view_h) >= 2:
                    self.energy_view.canvas.config(height=view_h)
                    self.energy_view.height = view_h
                
            if hasattr(self, "buffer_view"):
                if self._debug_log:
                    print(f"[RESIZE] Resizing buffer_view to height {view_h}")
                # DON'T recreate figure - just resize container
                current_buffer_h = self.buffer_view.winfo_height()
                if abs(current_buffer_h - view_h) >= 2:
                    self.buffer_view.configure(height=view_h)
                    self.buffer_view.height = view_h
            self._resize_enabled = False
                
        except Exception as e:
            if self._debug_log:
                print(f"[RESIZE] Exception: {e}")
            self._resize_enabled = False

    def _apply_runtime_scaling(self):
        """DEPRECATED: Old runtime scaling - now handled by _handle_resize."""
        # This function is kept for compatibility but does nothing
        print(f"[SCALING] _apply_runtime_scaling called (deprecated, doing nothing)")
        pass

    def _log_component_heights(self):
        """Log actual component heights to diagnose Pi vs PC differences."""
        try:
            # Force geometry calculation
            self.root.update_idletasks()
            
            root_h = self.root.winfo_height()
            header_h = self.header.winfo_height()
            notebook_h = self.notebook.winfo_height()
            status_h = self.status.winfo_height()
            dash_h = self.dashboard_tab.winfo_height()
            body_h = self.body.winfo_height()
            energy_h = self.energy_view.winfo_height()
            buffer_h = self.buffer_view.winfo_height()
            
            print(f"[DEBUG] Actual Heights:")
            print(f"  Root window: {root_h}px")
            print(f"  Header: {header_h}px")
            print(f"  Notebook/Tabs: {notebook_h}px")
            print(f"  Dashboard tab: {dash_h}px")
            print(f"  Body content: {body_h}px")
            print(f"  Energy view: {energy_h}px")
            print(f"  Buffer view: {buffer_h}px")
            print(f"  Statusbar: {status_h}px")
            print(f"  Fixed overhead: {header_h + notebook_h + status_h}px")
            print(f"  Available for body: {root_h - header_h - notebook_h - status_h}px")
        except Exception as e:
            print(f"[DEBUG] Height logging failed: {e}")

    def _ensure_emoji_font(self):
        """Prüft Emoji-Font und versucht Installation auf Linux (apt-get)."""
        if EMOJI_OK:
            return
        # Nur Linux: optional Auto-Install, wenn apt-get verfügbar und root
        if platform.system().lower() != "linux":
            self.status.update_status("Emoji-Font fehlt (Pi: fonts-noto-color-emoji)")
            return
        if not shutil.which("apt-get"):
            self.status.update_status("Emoji-Font fehlt (apt-get nicht gefunden)")
            return
        if hasattr(os, "geteuid") and os.geteuid() != 0:
            self.status.update_status("Emoji-Font fehlt (sudo nötig): fonts-noto-color-emoji")
            return
        try:
            subprocess.run(["apt-get", "update"], check=True)
            subprocess.run(["apt-get", "install", "-y", "fonts-noto-color-emoji"], check=True)
            self.status.update_status("Emoji-Font installiert, bitte neu starten")
        except Exception:
            self.status.update_status("Emoji-Font Installation fehlgeschlagen")

    def _resolve_status_icons(self) -> tuple[str, str]:
        """Use clean ASCII markers unless emojis are explicitly requested."""
        prefer_emoji = os.getenv("DASH_STATUS_EMOJI", "0").strip().lower() in {"1", "true", "yes"}
        if prefer_emoji and EMOJI_OK:
            return "✅", "⚠️"
        return "~", "▲"

    # --- Update Loop mit echten Daten (OPTIMIZED for Pi performance) ---
    def _loop(self):
        self._tick += 1
        self._update_status_summary()

        # Versuche echte Daten zu laden
        try:
            self._fetch_real_data()
        except Exception as e:
            logging.debug(f"Fehler beim Abrufen echter Daten: {e}")

        # Header every 3s
        now = datetime.now()
        if self._tick % 1 == 0:
            date_text = now.strftime("%d.%m.%Y")
            weekday = now.strftime("%A")
            time_text = now.strftime("%H:%M")
            out_temp = f"{self._last_data['out_temp']:.1f} °C"
            self.header.update_header(date_text, weekday, time_text, out_temp)
            soc = self._last_data["soc"]
            self.status.update_center(f"SOC {soc:.0f}%")

        # Energy every 3s (was 1.5s) - smart delta detection avoids redundant rendering
        self.energy_view.update_flows(
            self._last_data["pv"],
            self._last_data["load"],
            self._last_data["grid"],
            self._last_data["batt"],
            self._last_data["soc"],
        )

        # Buffer every 9s (was 6s) - matplotlib rendering is expensive
        if self._tick % 3 == 0:
                # Show Warmwasser as the main value in the mini diagram
                warmwasser = self._last_data.get("warmwasser")
                self.buffer_view.update_temperatures(
                    self._last_data["puffer_top"],
                    self._last_data["puffer_mid"],
                    self._last_data["puffer_bot"],
                    warmwasser,
                )

        # Data freshness every 15s
        if self._tick % 5 == 0:
            self._update_freshness_and_sparkline()

        # Pi 5: 2s main loop (balanced performance)
        self.root.after(2000, self._loop)

    def handle_wechselrichter_data(self, data: dict):
        """Echtzeit-PV-Daten aus dem Worker-Thread übernehmen."""
        ts = self._parse_timestamp_value(data.get("Zeitstempel")) or datetime.now()
        source = self._source_health.get("pv")
        if source:
            source["ts"] = ts
            source["count"] += 1

        pv_kw = _safe_float(data.get("PV-Leistung (kW)"))
        grid_kw = _safe_float(data.get("Netz-Leistung (kW)"))
        batt_kw = _safe_float(data.get("Batterie-Leistung (kW)"))
        load_kw = _safe_float(data.get("Hausverbrauch (kW)"))
        soc = _safe_float(data.get("Batterieladestand (%)"))

        if pv_kw is not None:
            self._last_data["pv"] = pv_kw * 1000
        if load_kw is not None:
            self._last_data["load"] = load_kw * 1000
        if batt_kw is not None:
            self._last_data["batt"] = -batt_kw * 1000

        if grid_kw is not None:
            self._last_data["grid"] = grid_kw * 1000
        elif pv_kw is not None or load_kw is not None or batt_kw is not None:
            calc_load = load_kw if load_kw is not None else (pv_kw or 0.0) + (grid_kw or 0.0) - (batt_kw or 0.0)
            calc_grid = (calc_load or 0.0) - (pv_kw or 0.0) + (batt_kw or 0.0)
            self._last_data["grid"] = calc_grid * 1000

        if soc is not None:
            self._last_data["soc"] = soc

        self._update_status_summary()

    @staticmethod
    def _parse_timestamp_value(value):
        # Erwartet ISO-8601-String mit expliziter Zeitzone (Europe/Vienna)
        from datetime import datetime
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except Exception:
            return None

    @staticmethod
    def _format_age_compact(seconds: float | int) -> str:
        seconds = int(max(0, seconds))
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m"
        return f"{seconds // 3600}h"

    def _update_status_summary(self):
        if not hasattr(self, "status"):
            return
        parts = []
        now = datetime.now()
        for key in ("pv", "heating"):
            info = self._source_health.get(key)
            if not info:
                continue
            ts = info.get("ts")
            if not ts:
                parts.append(f"{info['label']} ⚠️ --")
                continue
            age = (now - ts).total_seconds()
            icon = self._status_icon_ok if age <= 90 else self._status_icon_warn
            parts.append(f"{info['label']} {icon} {self._format_age_compact(age)}")

        if self._data_fresh_seconds is not None:
            icon = self._status_icon_ok if self._data_fresh_seconds <= 120 else self._status_icon_warn
            parts.append(f"DB {icon} {self._format_age_compact(self._data_fresh_seconds)}")
        else:
            parts.append(f"DB {self._status_icon_warn} --")

        summary = " | ".join(parts)
        if summary != self._last_status_compact:
            self.status.update_status(summary)
            self._last_status_compact = summary

    def _update_freshness_and_sparkline(self):
        last_ts = self._get_last_timestamp()
        if last_ts:
            delta = datetime.now() - last_ts
            seconds = int(delta.total_seconds())
            if seconds < 60:
                text = f"Daten: {seconds} s"
            elif seconds < 3600:
                text = f"Daten: {seconds//60} min"
            else:
                text = f"Daten: {seconds//3600} h"
            self.status.update_data_freshness(text, alert=seconds > 60)
            self._data_fresh_seconds = seconds
        else:
            self.status.update_data_freshness("Daten: --", alert=True)
            self._data_fresh_seconds = None

        # Sparkline moved into right card; keep footer minimal
        self._update_status_summary()
        self._update_footer_lamps()

    def _get_last_timestamp(self) -> datetime | None:
        if not self.datastore:
            return None
        try:
            cached = self.datastore.get_last_ingest_datetime()
        except Exception:
            cached = None
        if cached:
            return cached
        ts_str = self.datastore.get_latest_timestamp()
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str)
        except Exception:
            return None

    def _load_pv_sparkline(self, minutes: int = 60) -> list[float]:
        import sys
        print("[SPARKLINE] _load_pv_sparkline called", file=sys.stderr)
        if not self.datastore:
            print("[SPARKLINE] Kein Datastore!", file=sys.stderr)
            return []
        cutoff = datetime.now() - timedelta(minutes=minutes)
        hours = max(1, (minutes // 60) + 1)
        rows = self.datastore.get_recent_fronius(hours=hours, limit=1200)
        print(f"[SPARKLINE] rows geladen: {len(rows)}", file=sys.stderr)
        values: list[float] = []
        for row in rows[-400:]:
            ts = self._parse_timestamp_value(row.get('timestamp'))
            pv_kw = row.get('pv')
            if ts is None or pv_kw is None:
                continue
            if ts < cutoff:
                continue
            values.append(float(pv_kw))
        print(f"[SPARKLINE] Werte für Sparkline: {values[-10:] if values else values}", file=sys.stderr)
        return values

    def _fetch_real_data(self):
        """Versucht, echte Daten direkt aus dem DataStore zu laden."""
        if not self.datastore:
            return

        try:
            record = self.datastore.get_last_fronius_record()
            if record:
                from core.schema import PV_POWER_KW, GRID_POWER_KW, BATTERY_POWER_KW, BATTERY_SOC_PCT, LOAD_POWER_KW
                pv_kw = float(record.get(PV_POWER_KW) or 0.0)
                grid_kw = float(record.get(GRID_POWER_KW) or 0.0)
                batt_kw = float(record.get(BATTERY_POWER_KW) or 0.0)
                soc = float(record.get(BATTERY_SOC_PCT) or 0.0)
                load_kw = record.get(LOAD_POWER_KW)
                if load_kw is None:
                    load_kw = pv_kw + grid_kw - batt_kw
                else:
                    load_kw = float(load_kw)

                self._last_data["pv"] = pv_kw * 1000
                self._last_data["grid"] = grid_kw * 1000
                self._last_data["batt"] = -batt_kw * 1000
                self._last_data["load"] = load_kw * 1000
                self._last_data["soc"] = soc
        except Exception as exc:
            logging.debug(f"DataStore Fronius Fehler: {exc}")

        try:
            heating = self.datastore.get_last_heating_record()
            if heating:
                out_val = _safe_float(heating.get('outdoor'))
                top_val = _safe_float(heating.get('top'))
                mid_val = _safe_float(heating.get('mid'))
                bot_val = _safe_float(heating.get('bot'))
                warm_val = _safe_float(heating.get('warm'))
                kessel_val = _safe_float(heating.get('kessel'))

                if out_val is not None:
                    self._last_data["out_temp"] = out_val
                if top_val is not None:
                    self._last_data["puffer_top"] = top_val
                if mid_val is not None:
                    self._last_data["puffer_mid"] = mid_val
                if bot_val is not None:
                    self._last_data["puffer_bot"] = bot_val
                if warm_val is not None:
                    self._last_data["warmwasser"] = warm_val
                if kessel_val is not None:
                    self._last_data["kesseltemperatur"] = kessel_val
        except Exception as exc:
            logging.debug(f"DataStore BMK Fehler: {exc}")


def run():
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root._set_appearance_mode("dark")  # Force dark mode
    # Setze root Hintergrund auf dunkel - behebt hellgraue Flächen
    try:
        root.configure(fg_color="#0E0F12")
    except:
        pass  # Falls fg_color nicht unterstützt wird
    app = MainApp(root)
    root.mainloop()

if __name__ == "__main__":
    run()

