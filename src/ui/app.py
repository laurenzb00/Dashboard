"""Main application module for the Smart Home Dashboard.

This module contains the MainApp class which orchestrates the entire
dashboard UI and coordinates between various tabs and data sources.
"""

import tkinter as tk
import customtkinter as ctk
import os
import platform
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
import logging
import time
import threading
import queue
import sys
from tkinter import ttk

logger = logging.getLogger(__name__)

# Environment configuration
DEBUG_LOG = os.environ.get("DASHBOARD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
_show_status = os.environ.get("DASHBOARD_SHOW_STATUS_TAB", "").strip().lower() in ("1", "true", "yes", "on")
_hide_status = os.environ.get("DASHBOARD_HIDE_STATUS_TAB", "").strip().lower() in ("1", "true", "yes", "on")
SHOW_STATUS_TAB = bool(_show_status) and not bool(_hide_status)


def _dbg_print(msg: str) -> None:
    """Debug print if DASHBOARD_DEBUG is enabled."""
    if DEBUG_LOG:
        print(msg, flush=True)


# Add parent directory (src/) to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# UI components
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
    get_safe_font,
)
from ui.components.card import Card
from ui.components.header import HeaderBar
from ui.components.statusbar import StatusBar
from ui.components.rounded import RoundedFrame
from ui.components.standby_overlay import StandbyOverlay
from ui.views.energy_flow import EnergyFlowView
from ui.views.buffer_storage import BufferStorageView
from ui.views.pv_sparkline import PVSparklineView
from ui.app_state import AppState
from ui.state_schema import validate_payload
from ui.tabview_wrapper import TabviewWrapper

# Refactored modules
from ui.app_helpers import (
    parse_iso_datetime,
    parse_timestamp_value,
    format_age_short,
    format_age_compact,
    age_seconds,
    compose_status_text,
)
from ui.app_callbacks import (
    get_shower_script_entity_id,
    get_leaving_home_input_boolean_entity_id,
    get_force_away_webhook_id,
    get_force_home_webhook_id,
    trigger_ha_input_boolean_turn_on,
    trigger_ha_script,
    trigger_ha_automation,
    trigger_ha_webhook,
)
from ui.app_data_handlers import (
    process_wechselrichter_data,
    process_bmkdaten_data,
)
from ui.app_presence import PresenceOverrideManager

# Core modules
from core.datastore import DataStore, get_shared_datastore
from core.utils import safe_float
from core.homeassistant import HomeAssistantClient, load_homeassistant_config
from core.schema import (
    PV_POWER_KW,
    GRID_POWER_KW,
    BATTERY_POWER_KW,
    BATTERY_SOC_PCT,
    LOAD_POWER_KW,
    BMK_KESSEL_C,
    BMK_WARMWASSER_C,
    BMK_BETRIEBSMODUS,
    BUF_TOP_C,
    BUF_MID_C,
    BUF_BOTTOM_C,
)

_UI_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_UI_DIR)
_PROJECT_ROOT = os.path.dirname(_SRC_DIR)
def safe_get_datastore() -> DataStore | None:
    try:
        return get_shared_datastore()
    except Exception as exc:
        logging.error("[DB] DataStore nicht erreichbar: %s", exc)
        return None


try:
    from tabs.historical import HistoricalTab
except ImportError:
    HistoricalTab = None
try:
    from tabs.ertrag import ErtragTab
except ImportError:
    ErtragTab = None

try:
    from tabs.tagesproduktion import TagesproduktionTab
except ImportError:
    TagesproduktionTab = None


# Spotify Tab mit integriertem OAuth
try:
    from tabs.spotify import SpotifyTab
except Exception as e:
    logger.warning("SpotifyTab import failed: %s", e)
    SpotifyTab = None

try:
    from tabs.tado import TadoTab
except ImportError:
    TadoTab = None

try:
    from tabs.hue import HueTab
except ImportError:
    HueTab = None

SystemTab = None

try:
    from tabs.calendar import CalendarTab
except ImportError:
    CalendarTab = None

try:
    from tabs.analyse import AnalyseTab
except ImportError:
    AnalyseTab = None

try:
    from tabs.healthcheck import HealthTab
except ImportError:
    HealthTab = None

try:
    from tabs.homeassistant_actions import HomeAssistantActionsTab
except ImportError:
    HomeAssistantActionsTab = None

# StatusTab importieren
try:
    from tabs.status import StatusTab
except ImportError:
    StatusTab = None



class MainApp:
    """Main application class for the Smart Home Dashboard.
    
    Coordinates all UI components, data sources, and tab management.
    Uses a background queue for thread-safe UI updates.
    """
    
    def _start_ui_pump(self) -> None:
        """Start the UI queue pump for thread-safe updates."""
        if getattr(self, "_ui_pump_started", False):
            return
        self._ui_pump_started = True

        def pump() -> None:
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
                # Increased from 100ms to 1000ms to reduce main thread load
                self.root.after(1000, pump)
            except Exception:
                pass

        try:
            self.root.after(0, pump)
        except Exception:
            pass

    def _post_ui(self, callback) -> None:
        """Post a callback to be executed on the main thread."""
        try:
            self._ui_queue.put(callback)
        except Exception:
            pass

    def _compute_last_heating_event_dt(self) -> datetime | None:
        """Heuristik: letzte 'Einheiz'-Phase erkennen.

        Returns:
            Zeitpunkt des (geschätzten) Startpunkts des Einheizens.
        """
        if not getattr(self, "datastore", None):
            return None
        try:
            # Only 3h needed (2h window + 1h buffer for slopes)
            rows = self.datastore.get_recent_heating(hours=3, limit=1200)
        except Exception:
            return None

        from core.heating_events import compute_last_heating_event
        return compute_last_heating_event(rows)

    def _refresh_status_metrics_if_needed(self, now_monotonic: float) -> None:
        """Refresh status metrics (PV today, last heating event) if stale."""
        if not hasattr(self, "_status_metrics"):
            self._status_metrics = {
                "last_refresh": 0.0,
                "pv_today_kwh": None,
                "last_heat_event_dt": None,
            }
        if now_monotonic - float(self._status_metrics.get("last_refresh") or 0.0) < 30.0:
            return
        
        # Mark as refreshing to prevent duplicate calls
        self._status_metrics["last_refresh"] = now_monotonic
        
        # Run expensive DB queries in background thread
        def worker():
            pv_today_kwh = None
            last_heat_event_dt = None
            try:
                daily = self.datastore.get_daily_totals(days=2) if self.datastore else []
                today_key = datetime.now(timezone.utc).date().isoformat()
                for item in reversed(daily or []):
                    if str(item.get("day")) == today_key:
                        pv_today_kwh = float(item.get("pv_kwh") or 0.0)
                        break
                if pv_today_kwh is None and daily:
                    pv_today_kwh = float(daily[-1].get("pv_kwh") or 0.0)
            except Exception:
                pv_today_kwh = None
            
            try:
                last_heat_event_dt = self._compute_last_heating_event_dt()
            except Exception:
                last_heat_event_dt = None
            
            def apply():
                self._status_metrics["pv_today_kwh"] = pv_today_kwh
                self._status_metrics["last_heat_event_dt"] = last_heat_event_dt
            self._post_ui(apply)
        
        threading.Thread(target=worker, daemon=True).start()
    def build_tabs(self):
        """Robustly rebuilds all tabs, ensuring correct references after UI changes (fullscreen, etc)."""
        # CTkTabview: Tabs können nicht dynamisch entfernt werden, daher nur _add_other_tabs aufrufen
        # Dashboard-Tab wurde bereits in __init__ erstellt
        try:
            self._add_other_tabs()
        except Exception as e:
            logger.error("Error adding tabs: %s", e)
        self._subscribe_view_updates()
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
                logger.warning("Validator nicht verfügbar: %s", e)
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
                    logger.error("Fehler bei wöchentlicher Validierung: %s", e)
        validator_thread = threading.Thread(target=validate_loop, daemon=True)
        validator_thread.start()

    def update_tick(self):
        """Zentrale UI-Update-Schleife: aktualisiert Status mit gecachten Daten."""
        self._tick_count += 1
        now_mono = time.monotonic()
        
        # Animate arrows only while the energy tab is visible; pause them on
        # other tabs so navigation stays responsive.
        try:
            current_tab = self.tabview.get()
            is_dashboard = "Energie" in current_tab
            if hasattr(self, "energy_view") and self.energy_view:
                if is_dashboard and not self.energy_view._anim_enabled:
                    self.energy_view._anim_enabled = True
                    self.energy_view._start_animation()
                elif not is_dashboard and self.energy_view._anim_enabled:
                    self.energy_view._anim_enabled = False
        except Exception:
            pass
        
        try:
            # Data updates now come via handle_wechselrichter_data/handle_bmkdaten_data
            # which push to app_state. No DB queries needed here.
            
            # --- Statusmeldungen (unten) ---
            try:
                self._refresh_status_metrics_if_needed(now_mono)
            except Exception:
                pass

            pv_today_kwh = None
            last_heat_event_dt = None
            if hasattr(self, "_status_metrics"):
                pv_today_kwh = self._status_metrics.get("pv_today_kwh")
                last_heat_event_dt = self._status_metrics.get("last_heat_event_dt")

            heat_part = "Einheizen: --"
            try:
                if last_heat_event_dt is not None:
                    age_s = (datetime.now().astimezone() - last_heat_event_dt).total_seconds()
                    heat_part = f"Einheizen: {last_heat_event_dt.strftime('%H:%M')} (vor {format_age_short(age_s)})"
            except Exception:
                pass

            pv_part = "PV heute: --"
            try:
                if pv_today_kwh is not None:
                    pv_part = f"PV heute: {pv_today_kwh:.1f} kWh"
            except Exception:
                pass

            mode_part = ""
            try:
                if hasattr(self, "app_state") and self.app_state:
                    mode = self.app_state.get(BMK_BETRIEBSMODUS)
                    if mode not in (None, ""):
                            mode_part = f"Modus: {mode}"
            except Exception:
                mode_part = ""

            cal_text = ""
            try:
                tab = getattr(self, "calendar_tab", None)
                if tab is not None and callable(getattr(tab, "get_today_overlay_text", None)):
                    cal_text = (tab.get_today_overlay_text() or "").strip()
            except Exception:
                cal_text = ""

            # Use centralized compose_status_text helper
            status_str = compose_status_text([mode_part, heat_part, pv_part, cal_text], max_len=140)

            # Throttle auto-status updates to reduce visual noise.
            try:
                last_emit = getattr(self, "_auto_status_last_emit", 0.0)
                last_text = getattr(self, "_auto_status_last_text", "")
            except Exception:
                last_emit = 0.0
                last_text = ""

            should_emit = False
            try:
                if status_str != last_text and (now_mono - float(last_emit)) >= 2.0:
                    should_emit = True
            except Exception:
                should_emit = True

            if should_emit and hasattr(self, "status"):
                try:
                    self.status.set_auto_status(status_str)
                except Exception:
                    self.status.update_status(status_str)
                try:
                    self._auto_status_last_emit = now_mono
                    self._auto_status_last_text = status_str
                except Exception:
                    pass

        except Exception:
            logging.exception("update_tick failed")
        # Tick erneut einplanen - increased from 2000ms to 3000ms
        # Data collectors only update every 10s, so faster polling wastes CPU
        self.root.after(3000, self.update_tick)

    def handle_bmkdaten_data(self, data: dict):
        """Echtzeit-Heizungsdaten aus dem Worker-Thread übernehmen."""
        logging.info("[BMK] handle_bmkdaten_data called with keys: %s", list(data.keys()) if data else None)
        process_bmkdaten_data(data, self.app_state, self._source_health)

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

        # Tkinter is not thread-safe; route background-thread UI updates via this queue.
        self._ui_queue: "queue.Queue[callable]" = queue.Queue()
        self._start_ui_pump()

        # Shared DataStore wird beim Start bereitgestellt
        _dbg_print("[INIT] MainApp: DataStore wird geladen...")
        self.datastore = datastore or safe_get_datastore()
        _dbg_print(f"[INIT] MainApp: DataStore geladen: {type(self.datastore)}")

        self.app_state = AppState(validator=validate_payload)
        self._state_unsubscribers = []

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

        # Touch targets are deliberately generous for the 14-inch touchscreen.
        # 132 -> 150: HeaderBar's portrait height grew with the Runde-3
        # Header-Neubau (groesserer, mittig zentrierter Uhrzeit-Block).
        self._base_header_h = 150
        self._base_status_h = 68

        # Start weekly Ertrag validation in background
        self._start_ertrag_validator()

        # Debug: Bind Configure events
        self.root.bind("<Configure>", self._on_root_configure)
        self.root.bind("<Map>", self._on_root_map)
        # Use the complete native display instead of the former 1024x600 size.
        try:
            sw = max(1, self.root.winfo_screenwidth())
            sh = max(1, self.root.winfo_screenheight())
            portrait_screen = sh > sw
            dpi_scale = 1.18 if portrait_screen else max(1.0, min(1.25, sw / 1536.0))
            self.root.tk.call("tk", "scaling", dpi_scale)
        except Exception:
            pass
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
            on_leave=self.on_leave_home,
            on_come_home=self.on_come_home,
            on_shower=self.on_shower_go,
            on_exit=self.on_exit,
        )
        self._portrait_screen = bool(self.root.winfo_screenheight() > self.root.winfo_screenwidth())
        if self._portrait_screen:
            self.header.set_portrait_layout(True)
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
        self.body.grid_columnconfigure(1, weight=1, minsize=360)  # Schmalere Heatmap-Spalte
        self.body.grid_rowconfigure(0, weight=1)
        self.body.grid_rowconfigure(1, weight=0)

        # Energy Card (60:40 Grid) - flexible Größe
        _dbg_print("[INIT] MainApp: EnergyCard und EnergyView werden erstellt...")
        self.energy_card = Card(self.body, padding=0)
        self.energy_card.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.energy_card.add_title("Energiefluss", icon="⚡")
        self.energy_view = EnergyFlowView(self.energy_card.content(), width=200, height=180)
        self.energy_view.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Buffer Card (60:40 Grid) - flexible Größe
        _dbg_print("[INIT] MainApp: BufferCard und BufferView werden erstellt...")
        self.buffer_card = Card(self.body, padding=12)
        self.buffer_card.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        # War zuvor "Warmwasser" betitelt, obwohl die Karte BEIDE Werte zeigt
        # (Puffer-Heatmap UND Warmwasser/Boiler) - die Matplotlib-Grafik
        # darunter beschriftet die beiden Gefaesse zusaetzlich selbst noch
        # einmal mit "PUFFER"/"WARMWASSER". Der alte Kartentitel war dadurch
        # sowohl ungenau als auch redundant zur eigenen Grafik-Beschriftung.
        self.buffer_card.add_title("Wärmespeicher", icon="🔥")
        self.buffer_view = BufferStorageView(self.buffer_card.content(), height=320, datastore=self.datastore)
        self.buffer_view.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self.sparkline_card = Card(self.body, padding=12)
        self.sparkline_card.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        # Einheitlicher Karten-Titel (Icon + Bahnschrift-Bold) wie bei den
        # anderen beiden Energie-Tab-Karten, statt eines eigenen, kleineren
        # tk.Label-Headers in Segoe UI nur innerhalb der Sparkline-Ansicht -
        # die drei Karten wirkten dadurch bisher nicht "aus einem Guss".
        self.sparkline_card.add_title("PV & Außentemperatur (24h)", icon="🔆")
        self.sparkline_view = PVSparklineView(self.sparkline_card.content(), datastore=self.datastore)
        self.sparkline_view.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Presence-gesteuerter Standby-Bildschirmschoner: eigenstaendiges
        # place()-Widget direkt auf root (nicht im Grid von main_container),
        # damit es bei "nicht zuhause" (siehe _sync_presence_standby_state)
        # das komplette Dashboard unabhaengig von dessen Layout ueberdecken
        # kann. Zu Beginn nicht platziert/unsichtbar.
        self.standby_overlay = StandbyOverlay(self.root)
        self._standby_active = False

        # Statusbar - moderner Style mit besserem Spacing
        self.status = StatusBar(self.main_container, on_exit=self.on_exit, on_toggle_fullscreen=self.toggle_fullscreen)
        if self._portrait_screen:
            self.status.set_portrait_layout(True)
        self.status.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        self._apply_fullscreen()
        self.build_tabs()
        # Keep the header Hue switch in sync with the bridge state.
        self._start_hue_switch_sync()
        # Standby-Screensaver: fragt echte HA-Anwesenheit ab (nicht Touch-
        # Leerlauf) und blendet bei "nicht zuhause" StandbyOverlay ein.
        self._start_presence_standby_sync()
        # Initial update_tick delayed, then runs every 2000ms
        self.root.after(1000, self.update_tick)

        # Historie/Tagesproduktion/Ertrag bauen ihr Matplotlib-Diagramm schon
        # beim App-Start, waehrend der Tab noch unsichtbar ist. Ihre eigenen
        # zeitgesteuerten Resize-Versuche (after 180/500/1200ms) laufen alle
        # ab, WAEHREND die aeussere Layout-Berechnung hier (siehe
        # _apply_compact_height_budget, deren Retries teils mehrere Sekunden
        # dauern) noch gar nicht fertig ist - das Diagramm bleibt dadurch
        # dauerhaft auf seiner kleinen Ausgangsgroesse (Figure-Default)
        # haengen, weil beim SPAETEREN manuellen Tab-Wechsel weder
        # <Configure> noch <Map> erneut feuern (das Canvas-Widget war ja
        # schon "gemappt", seine Groesse aendert sich zu dem Zeitpunkt
        # nicht mehr). Ein echter CTkTabview "command="-Hook waere die
        # sauberere Loesung, ist aber je nach customtkinter-Version nicht
        # garantiert vorhanden. Stattdessen: periodischer Sicherheitscheck,
        # der NUR bei tatsaechlicher Groessen-Abweichung einmal
        # nachsynchronisiert (kein Dauerlauf/Endlos-Schleife wie der
        # fruehere, entfernte Scaling-Loop - nach dem Sync stimmt
        # _last_synced_wh wieder mit der echten Groesse ueberein, der
        # naechste Check findet dann keine Abweichung mehr).
        try:
            self.root.after(2000, self._watch_lazy_tab_charts)
        except Exception:
            pass

        # Apply a height budget once after initial layout settles.
        try:
            self.root.after(350, self._apply_compact_height_budget)
        except Exception:
            pass

        # TEMPORAERE DIAGNOSE (bitte Konsolenausgabe nach dem Start hier
        # kopieren/schicken): druckt einmalig die tatsaechlichen Pixelhoehen
        # der Energie-Tab-Widgets aus, damit wir sehen koennen, wo die
        # verfuegbare Hoehe tatsaechlich verschwindet, statt weiter zu raten.
        try:
            self.root.after(3000, self._debug_print_layout_heights)
            self.root.after(8000, self._debug_print_layout_heights)
        except Exception:
            pass

    def _debug_print_layout_heights(self) -> None:
        # Schreibt in eine Datei statt (nur) auf die Konsole, weil die App
        # nicht immer aus einem sichtbaren Konsolenfenster gestartet wird
        # (z.B. Autostart auf dem Touchscreen-Geraet ueber VNC). Die Datei
        # landet im selben Projektordner wie datenerfassung.log und kann so
        # auch ohne Konsolenzugriff ausgelesen werden.
        lines: list[str] = []

        def rec(label: str, w) -> None:
            try:
                lines.append(f"{label}: {w.winfo_width()}x{w.winfo_height()}")
            except Exception as e:
                lines.append(f"{label}: ERROR {e}")

        try:
            self.root.update_idletasks()
            lines.append(f"=== LAYOUT DEBUG {datetime.now().isoformat()} ===")
            rec("root", self.root)
            rec("body", self.body)
            rec("energy_card", self.energy_card)
            rec("buffer_card", self.buffer_card)
            rec("sparkline_card", self.sparkline_card)
            try:
                lines.append(f"body.grid_size: {self.body.grid_size()}")
                for row in (0, 1, 2):
                    info = self.body.grid_rowconfigure(row)
                    lines.append(f"body row {row} config: {info}")
            except Exception as e:
                lines.append(f"body grid info error: {e}")
            try:
                rec("buffer_view", self.buffer_view)
                rec("buffer_view.layout", self.buffer_view.layout)
                rec("buffer_view.plot_frame", self.buffer_view.plot_frame)
                rec("buffer_view.canvas_widget", self.buffer_view.canvas_widget)
                rec("buffer_view.mode_canvas_container", self.buffer_view.mode_canvas_container)
                lines.append(f"buffer_card.grid_info: {self.buffer_card.grid_info()}")
            except Exception as e:
                lines.append(f"buffer_view sub-widget error: {e}")
            try:
                rec("energy_view", self.energy_view)
                rec("energy_view.canvas", self.energy_view.canvas)
                lines.append(f"energy_card.grid_info: {self.energy_card.grid_info()}")
            except Exception as e:
                lines.append(f"energy_view sub-widget error: {e}")
            lines.append("=== END LAYOUT DEBUG ===")
        except Exception as e:
            lines.append(f"[LAYOUT DEBUG] failed: {e}")

        text = "\n".join(lines) + "\n"
        try:
            print(text, flush=True)
        except Exception:
            pass
        try:
            debug_path = os.path.join(_PROJECT_ROOT, "data", "layout_debug.txt")
            os.makedirs(os.path.dirname(debug_path), exist_ok=True)
            with open(debug_path, "a", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def _start_hue_switch_sync(self) -> None:
        if getattr(self, "_hue_switch_sync_started", False):
            return
        self._hue_switch_sync_started = True
        try:
            self.root.after(2000, self._sync_hue_switch_state)
        except Exception:
            pass

    def _sync_hue_switch_state(self) -> None:
        """Poll Hue bridge group(0) and update the header switch accordingly."""

        def _reschedule() -> None:
            try:
                self.root.after(7000, self._sync_hue_switch_state)
            except Exception:
                pass

        tab = getattr(self, "hue_tab", None)
        bridge = getattr(tab, "bridge", None) if tab else None
        lock = getattr(tab, "_bridge_lock", None) if tab else None
        if not tab or bridge is None or lock is None:
            _reschedule()
            return

        def worker() -> None:
            is_on: bool | None = None
            try:
                with lock:
                    group = bridge.get_group(0)
                state = (group or {}).get("state", {})
                # Master switch: show ON if any light is currently ON.
                is_on = bool(state.get("any_on", False))
            except Exception:
                is_on = None

            def apply() -> None:
                try:
                    if is_on is not None and hasattr(self, "header") and self.header:
                        self.header.set_light_switch_state(is_on)
                        try:
                            if hasattr(self.header, "set_leave_home_active"):
                                self.header.set_leave_home_active(not is_on)
                        except Exception:
                            pass
                except Exception:
                    pass
                _reschedule()

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    # ------------------------------------------------------------------
    # Presence-gesteuerter Standby-Bildschirmschoner
    # ------------------------------------------------------------------
    # Nutzer-Vorgabe (woertlich): "Es gibt die Moeglichkeit, dass Du erstens
    # den Status abfragst, ob ich zu Hause bin oder nicht. Wenn ich nicht zu
    # Hause bin, braucht das Display ja nicht laufen beziehungsweise nicht
    # viel anzeigen oder nur einen Screensaver anzeigen." - bewusst KEIN
    # Touch-Leerlauf-Timer, sondern echte HA-Anwesenheit von person.laurenz,
    # analog zum bereits vorhandenen _sync_hue_switch_state()-Muster.

    def _presence_person_entity_id(self) -> str:
        """Entity-ID fuer die Standby-Anwesenheitsabfrage (ueberschreibbar)."""
        return os.environ.get("DASHBOARD_PRESENCE_ENTITY_ID", "").strip() or "person.laurenz"

    def _start_presence_standby_sync(self) -> None:
        if getattr(self, "_presence_standby_sync_started", False):
            return
        self._presence_standby_sync_started = True
        try:
            # Erster Check erst nach 5s, damit HA-Client/Konfiguration (siehe
            # _get_presence_ha_client) beim Start sicher initialisiert ist.
            self.root.after(5000, self._sync_presence_standby_state)
        except Exception:
            pass

    def _sync_presence_standby_state(self) -> None:
        """Pollt periodisch den echten HA-Anwesenheitsstatus von
        person.laurenz und blendet bei 'nicht zuhause' StandbyOverlay ein."""

        def _reschedule() -> None:
            try:
                self.root.after(20000, self._sync_presence_standby_state)
            except Exception:
                pass

        client = self._get_presence_ha_client()
        if client is None:
            _reschedule()
            return

        entity_id = self._presence_person_entity_id()

        def worker() -> None:
            state_str: str | None = None
            try:
                data = client.get_state(entity_id)
                if isinstance(data, dict):
                    state_str = str(data.get("state") or "").strip().lower()
            except Exception:
                state_str = None

            def apply() -> None:
                # Bei Fehlern/leerem Status bewusst NICHTS umschalten - ein
                # einzelner HA-Ausfall soll weder faelschlich den
                # Screensaver einblenden noch einen aktiven faelschlich
                # beenden. "home" ist der einzige Zustand, der als
                # "zuhause" zaehlt; jede Zone/jeder andere Wert (z.B.
                # "not_home", ein Zonenname) gilt als "weg".
                if state_str:
                    self._set_standby_active(state_str != "home")
                _reschedule()

            self._post_ui(apply)

        threading.Thread(target=worker, daemon=True).start()

    def _set_standby_active(self, active: bool) -> None:
        if getattr(self, "_standby_active", False) == bool(active):
            return
        self._standby_active = bool(active)
        overlay = getattr(self, "standby_overlay", None)
        if overlay is None:
            return
        try:
            if active:
                overlay.show()
            else:
                overlay.hide()
        except Exception:
            pass

    # strftime("%A") haengt vom System-Locale ab, das auf dem Pi nicht auf
    # Deutsch gesetzt ist - deshalb stand im Header bisher "Monday" statt
    # "Montag", obwohl der Rest der App komplett deutsch ist. Eine feste
    # Liste statt locale.setlocale(), weil ein fehlendes de_DE-Locale-Paket
    # auf dem Pi sonst beim Start eine Exception werfen wuerde.
    _WEEKDAYS_DE = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]

    def _update_header_datetime(self):
        now = datetime.now()
        date_text = now.strftime("%d.%m.%Y")
        weekday = self._WEEKDAYS_DE[now.weekday()]
        time_text = now.strftime("%H:%M")
        # Use cached outdoor temp (updated asynchronously)
        if not hasattr(self, '_cached_out_temp'):
            self._cached_out_temp = "--.- °C"
            self._cached_out_temp_ts = 0.0
        
        # Trigger async refresh if cache is stale (every 15s)
        mono = time.monotonic()
        if mono - self._cached_out_temp_ts >= 15.0:
            self._refresh_outdoor_temp_async()
            self._cached_out_temp_ts = mono  # Mark as refreshing
        
        self.header.update_header(date_text, weekday, time_text, self._cached_out_temp)
        overlay = getattr(self, "standby_overlay", None)
        if overlay is not None:
            try:
                overlay.update_time(time_text)
                overlay.update_date(date_text, weekday)
            except Exception:
                pass
        # Update every 10s (time only changes visibly every minute, temp every 15s)
        self.root.after(10000, self._update_header_datetime)

    def _refresh_outdoor_temp_async(self):
        """Fetch outdoor temp in background thread to avoid blocking main thread."""
        def worker():
            try:
                heat = self.datastore.get_last_heating_record() or {}
                norm = self.datastore.normalize_heating_record(heat, stale_minutes=5)
                if norm['is_stale'] or norm['outdoor'] is None:
                    temp_str = "--.- °C"
                else:
                    temp_str = f"{norm['outdoor']:.1f} °C"
            except Exception:
                temp_str = "--.- °C"
            
            def apply():
                self._cached_out_temp = temp_str
            self._post_ui(apply)
        
        threading.Thread(target=worker, daemon=True).start()

    def _style_tabview_buttons(self) -> None:
        """Make the active tab more readable and improve contrast."""
        try:
            segmented = getattr(self.tabview, "_segmented_button", None)
            if segmented is None:
                return
            # Etwas kompakter als vorher (17/16pt, 70/64px hoch, corner_radius
            # 20): bei 10-11 Tabs (Energie/Licht/HomeA/Spotify/Raum/Kalender/
            # Historie/Ertrag/Tagesprod./Status/Health) lief die Tab-Leiste
            # sonst rechts (und teils auch links) über den Bildschirmrand
            # hinaus, sodass die äußeren Tabs abgeschnitten wurden. Kleinere
            # Schrift/Höhe/Eckenradius sparen an jedem Tab ein paar Pixel -
            # zusammen mit den gekürzten Tab-Namen sollte das jetzt reichen.
            segmented.configure(
                font=get_safe_font("Bahnschrift", 15 if getattr(self, "_portrait_screen", False) else 14, "bold"),
                height=60 if getattr(self, "_portrait_screen", False) else 54,
                # 14 -> 16: an dieselbe "Glas"-Rundung wie TabShell-Header/
                # Card angeglichen, statt als einziges Element im Grundgeruest
                # noch auf dem alten (kleineren) Radius zu stehen.
                corner_radius=16,
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
        """Integriert alle weiteren Tabs.

        Hinweis: Der Health-Tab wird bewusst ganz am Ende hinzugefügt,
        damit er immer ganz rechts steht.
        """
        
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
                logger.error("HueTab initialization failed: %s", e, exc_info=True)
                self.hue_tab = None

        # HomeA (Home Assistant Automationen/Skripte) soll als 3. Tab erscheinen
        if HomeAssistantActionsTab:
            try:
                _dbg_print("[TABS] HomeAssistantActionsTab wird erstellt...")
                self.tabview.add(emoji("🏠 HomeA", "HomeA"))
                ha_frame = self.tabview.tab(emoji("🏠 HomeA", "HomeA"))
                try:
                    ha_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.homeassistant_actions_tab = HomeAssistantActionsTab(self.root, self.notebook, tab_frame=ha_frame)
                _dbg_print("[TABS] HomeAssistantActionsTab erfolgreich hinzugefügt.")
            except Exception as e:
                logger.error("HomeAssistantActionsTab init failed: %s", e)
                self.homeassistant_actions_tab = None
        
        # Andere Tabs (Portierung zu CustomTkinter fortlaufend)
        if SpotifyTab:
            try:
                _dbg_print("[TABS] SpotifyTab wird erstellt...")
                self.tabview.add(emoji("🎵 Spotify", "Spotify"))
                spotify_frame = self.tabview.tab(emoji("🎵 Spotify", "Spotify"))
                try:
                    spotify_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.spotify_tab = SpotifyTab(self.root, self.notebook, tab_frame=spotify_frame)
                _dbg_print("[TABS] SpotifyTab erfolgreich hinzugefügt.")
            except Exception as e:
                logger.error("SpotifyTab initialization failed: %s", e)
                self.spotify_tab = None

        if TadoTab:
            try:
                _dbg_print("[TABS] TadoTab wird erstellt...")
                # Gekuerzt ("Raumtemperatur" -> "Raum"): der volle Name war
                # einer der Hauptgruende, warum die Tab-Leiste bei 10 Tabs
                # rechts/links ueber den Bildschirmrand hinaus lief und Tabs
                # abgeschnitten wurden. Der volle Titel steht weiterhin oben
                # im Tab selbst (TabShell in tado.py).
                self.tabview.add(emoji("🌡️ Raum", "Raum"))
                tado_frame = self.tabview.tab(emoji("🌡️ Raum", "Raum"))
                try:
                    tado_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.tado_tab = TadoTab(self.root, self.notebook, tab_frame=tado_frame)
                _dbg_print("[TABS] TadoTab erfolgreich hinzugefügt.")
            except Exception as e:
                logger.error("TadoTab initialization failed: %s", e)
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
                logger.error("CalendarTab init failed: %s", e)
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
                logger.error("HistoricalTab init failed: %s", e)
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
                logger.error("ErtragTab init failed: %s", e)
                self.ertrag_tab = None

        if TagesproduktionTab:
            try:
                _dbg_print("[TABS] TagesproduktionTab wird erstellt...")
                # Gekuerzt, gleicher Grund wie beim Raum-Tab oben.
                self.tabview.add(emoji("📊 Tagesprod.", "Tagesprod."))
                prod_frame = self.tabview.tab(emoji("📊 Tagesprod.", "Tagesprod."))
                try:
                    prod_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                # Clear old widgets (e.g. after rebuild) before creating a new tab instance.
                try:
                    for child in prod_frame.winfo_children():
                        child.destroy()
                except Exception:
                    pass
                self.tagesproduktion_tab = TagesproduktionTab(
                    self.root,
                    self.notebook,
                    datastore=self.datastore,
                    tab_frame=prod_frame,
                )
                _dbg_print("[TABS] TagesproduktionTab erfolgreich hinzugefügt.")
            except Exception as e:
                logger.error("TagesproduktionTab init failed: %s", e)
                self.tagesproduktion_tab = None

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
                logger.error("StatusTab init failed: %s", e)
                self.status_tab = None
        elif StatusTab:
            _dbg_print("[TABS] StatusTab ausgeblendet (DASHBOARD_HIDE_STATUS_TAB=1 zum Ausblenden aktiv)")

        # HealthTab immer ganz rechts (letzter Tab)
        if HealthTab:
            try:
                _dbg_print("[TABS] HealthTab wird erstellt...")
                self.tabview.add(emoji("🩺 Health", "Health"))
                health_frame = self.tabview.tab(emoji("🩺 Health", "Health"))
                try:
                    health_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.health_tab = HealthTab(self.root, self.notebook, datastore=self.datastore, app=self, tab_frame=health_frame)
                _dbg_print("[TABS] HealthTab erfolgreich hinzugefügt.")
            except Exception as e:
                logger.error("HealthTab init failed: %s", e)
                self.health_tab = None
        _dbg_print("[TABS] Alle weiteren Tabs wurden verarbeitet.")

    def _subscribe_view_updates(self) -> None:
        if not hasattr(self, "app_state") or not self.app_state:
            return
        for unsubscribe in list(getattr(self, "_state_unsubscribers", [])):
            try:
                unsubscribe()
            except Exception:
                pass
        self._state_unsubscribers = []

        def _subscribe(handler, label: str) -> None:
            if handler is None:
                return

            def _listener(payload: dict) -> None:
                try:
                    handler(payload)
                except Exception:
                    logging.exception("%s update_data failed", label)

            self._state_unsubscribers.append(self.app_state.subscribe(_listener))

        if hasattr(self, "energy_view"):
            _subscribe(self.energy_view.update_data, "energy_view")
        if hasattr(self, "buffer_view"):
            _subscribe(self.buffer_view.update_data, "buffer_view")
        if hasattr(self, "sparkline_view"):
            _subscribe(self.sparkline_view.update_data, "sparkline_view")
        if hasattr(self, "historical_tab"):
            _subscribe(self.historical_tab.update_data, "historical_tab")

    # --- Callbacks ---
    def on_toggle_a(self):
        self.status.update_status("Licht: Alle an")
        try:
            if hasattr(self, "hue_tab") and self.hue_tab:
                self.hue_tab._threaded_group_cmd(True)
        except Exception:
            pass
        try:
            self.root.after(1800, self._sync_hue_switch_state)
        except Exception:
            pass

    def on_toggle_b(self):
        self.status.update_status("Licht: Alle aus")
        try:
            if hasattr(self, "hue_tab") and self.hue_tab:
                self.hue_tab._threaded_group_cmd(False)
        except Exception:
            pass
        try:
            self.root.after(1800, self._sync_hue_switch_state)
        except Exception:
            pass

    def on_exit(self):
        self.status.update_status("Beende...")
        try:
            self._presence_override_stop(silent=True)
        except Exception:
            pass
        try:
            if hasattr(self, "hue_tab") and self.hue_tab:
                try:
                    self.hue_tab.cleanup()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            self.root.quit()
        except Exception:
            pass

    def on_leave_home(self):
        """Header-Button 🏃 (Away): erzwingt Away via Home-Assistant Webhook."""

        webhook_id = self._get_force_away_webhook_id()
        self._trigger_ha_webhook(webhook_id, status_label="Away erzwingen")

    def on_come_home(self):
        """Header-Button 🏠 (Home): erzwingt Home via Home-Assistant Webhook."""

        webhook_id = self._get_force_home_webhook_id()
        self._trigger_ha_webhook(webhook_id, status_label="Home erzwingen")

    def on_shower_go(self) -> None:
        """Header-Button 🚿: führt das Script 'duschen gehen' aus."""

        entity_id = self._get_shower_script_entity_id()
        self._trigger_ha_script(entity_id, status_label="Duschen gehen")

    def _get_shower_script_entity_id(self) -> str:
        """Get the shower script entity ID."""
        return get_shower_script_entity_id(self._get_presence_ha_client())

    def _get_leaving_home_input_boolean_entity_id(self) -> str:
        """Get the leaving home input boolean entity ID."""
        return get_leaving_home_input_boolean_entity_id(self._get_presence_ha_client())

    def _get_force_away_webhook_id(self) -> str:
        """Get the force away webhook ID."""
        return get_force_away_webhook_id(self._get_presence_ha_client())

    def _get_force_home_webhook_id(self) -> str:
        """Get the force home webhook ID."""
        return get_force_home_webhook_id(self._get_presence_ha_client())

    def _trigger_ha_input_boolean_turn_on(self, entity_id: str, status_label: str) -> None:
        """Turn on a Home Assistant input boolean."""
        trigger_ha_input_boolean_turn_on(
            self._get_presence_ha_client(),
            entity_id,
            status_label,
            self.status.update_status,
            self._post_ui,
        )

    def _trigger_ha_script(self, entity_id: str, status_label: str) -> None:
        """Trigger a Home Assistant script."""
        trigger_ha_script(
            self._get_presence_ha_client(),
            entity_id,
            status_label,
            self.status.update_status,
            self._post_ui,
        )

    def _trigger_ha_automation(self, entity_id: str, status_label: str) -> None:
        """Trigger a Home Assistant automation."""
        trigger_ha_automation(
            self._get_presence_ha_client(),
            entity_id,
            status_label,
            self.status.update_status,
            self._post_ui,
        )

    def _trigger_ha_webhook(self, webhook_id: str, status_label: str) -> None:
        """Trigger a Home Assistant webhook."""
        trigger_ha_webhook(
            self._get_presence_ha_client(),
            webhook_id,
            status_label,
            self.status.update_status,
            self._post_ui,
        )

    # ------------------------------------------------------------------
    # Presence override (Home/Away buttons)
    # ------------------------------------------------------------------
    def _get_presence_ha_client(self) -> HomeAssistantClient | None:
        """Return a Home Assistant client instance (best-effort)."""

        # Prefer existing HA client from Hue tab (single source of config).
        try:
            tab = getattr(self, "hue_tab", None)
            client = getattr(tab, "_ha_client", None) if tab else None
            if isinstance(client, HomeAssistantClient):
                return client
        except Exception:
            pass

        # Fallback: create a dedicated client for presence overrides.
        try:
            client = getattr(self, "_presence_ha_client", None)
            if isinstance(client, HomeAssistantClient):
                return client
        except Exception:
            client = None

        try:
            cfg = load_homeassistant_config()
            if not cfg:
                return None
            client = HomeAssistantClient(cfg)
            self._presence_ha_client = client
            return client
        except Exception:
            return None

    def _init_presence_override_manager(self) -> None:
        """Initialize the presence override manager."""
        self._presence_manager = PresenceOverrideManager(
            get_ha_client=self._get_presence_ha_client,
            post_ui=self._post_ui,
            status_callback=self.status.update_status,
            after_func=self.root.after,
            after_cancel_func=self.root.after_cancel,
        )

    def _presence_override_start(self, location_name: str, minutes: int = 10) -> None:
        """Start (or restart) a temporary presence override."""
        if not hasattr(self, "_presence_manager"):
            self._init_presence_override_manager()
        self._presence_manager.start(location_name, minutes)

    def _presence_override_stop(self, silent: bool = False) -> None:
        """Stop the presence override."""
        if hasattr(self, "_presence_manager"):
            self._presence_manager.stop(silent)

    def _apply_fullscreen(self):
        """Setzt echtes Vollbild auf der nativen Bildschirmauflösung."""
        try:
            self.root.attributes("-fullscreen", True)
            self.is_fullscreen = True
            self.root.resizable(False, False)
        except Exception:
            pass

        # Recalculate child sizes after the fullscreen transition.
        try:
            self.root.after(250, self._apply_compact_height_budget)
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

        try:
            self.root.after(250, self._apply_compact_height_budget)
        except Exception:
            pass

    def _get_tab_selector_height(self) -> int:
        """Returns the visible height of the CTkTabview selector row."""
        try:
            segmented = getattr(self.tabview, "_segmented_button", None)
            if segmented is None:
                return 0
            h = int(segmented.winfo_height() or 0)
            # During early init winfo_height can be 0/1; use a sane fallback.
            return h if h >= 12 else 36
        except Exception:
            return 36

    def _watch_lazy_tab_charts(self) -> None:
        """Sicherheitsnetz gegen dauerhaft zu klein gerenderte Matplotlib-
        Charts in Historie/Tagesproduktion/Ertrag (siehe Kommentar bei der
        ersten Terminierung dieser Methode weiter oben). Prueft periodisch,
        ob eines dieser Charts sichtbar ist UND seine tatsaechliche Canvas-
        Groesse von der zuletzt synchronisierten Figure-Groesse abweicht -
        nur dann wird einmalig resynchronisiert.
        """
        tick = int(getattr(self, "_watch_lazy_tab_charts_tick", 0)) + 1
        self._watch_lazy_tab_charts_tick = tick
        log_this_tick = (tick % 5 == 0)  # ca. alle 5s eine Debug-Zeile pro Tab
        try:
            for tab_attr in ("historical_tab", "tagesproduktion_tab"):
                tab = getattr(self, tab_attr, None)
                if tab is None:
                    continue
                canvas_widget = getattr(tab, "canvas_widget", None)
                resize_fn = getattr(tab, "_resize_canvas_now", None)
                if canvas_widget is None or not callable(resize_fn):
                    continue
                try:
                    mapped = canvas_widget.winfo_ismapped()
                    w = int(canvas_widget.winfo_width() or 0)
                    h = int(canvas_widget.winfo_height() or 0)
                    last_w, last_h = getattr(tab, "_last_synced_wh", (0, 0))
                    if log_this_tick:
                        logging.info(
                            "[WATCHDOG] %s mapped=%s canvas=%sx%s last_synced=%sx%s",
                            tab_attr, mapped, w, h, last_w, last_h,
                        )
                    if not mapped:
                        continue
                    if w > 50 and h > 50 and (abs(w - last_w) > 4 or abs(h - last_h) > 4):
                        logging.info("[WATCHDOG] %s Groessenabweichung erkannt -> resize_fn()", tab_attr)
                        resize_fn()
                except Exception:
                    logging.exception("[WATCHDOG] Fehler bei %s", tab_attr)

            ertrag_tab = getattr(self, "ertrag_tab", None)
            energy_chart = getattr(ertrag_tab, "energy_chart", None) if ertrag_tab else None
            if energy_chart is not None:
                canvas_widget = getattr(energy_chart, "canvas_widget", None)
                refresh_fn = getattr(energy_chart, "refresh_size", None)
                if canvas_widget is not None and callable(refresh_fn):
                    try:
                        mapped = canvas_widget.winfo_ismapped()
                        w = int(canvas_widget.winfo_width() or 0)
                        h = int(canvas_widget.winfo_height() or 0)
                        last_w, last_h = getattr(energy_chart, "_last_synced_wh", (0, 0))
                        if log_this_tick:
                            logging.info(
                                "[WATCHDOG] ertrag_tab mapped=%s canvas=%sx%s last_synced=%sx%s",
                                mapped, w, h, last_w, last_h,
                            )
                        if mapped and w > 50 and h > 50 and (abs(w - last_w) > 4 or abs(h - last_h) > 4):
                            logging.info("[WATCHDOG] ertrag_tab Groessenabweichung erkannt -> refresh_fn()")
                            refresh_fn()
                    except Exception:
                        logging.exception("[WATCHDOG] Fehler bei ertrag_tab")
        except Exception:
            logging.exception("[WATCHDOG] Fehler im Haupt-Loop")
        finally:
            try:
                self.root.after(1000, self._watch_lazy_tab_charts)
            except Exception:
                pass

    def _apply_compact_height_budget(self) -> None:
        """Compute available height by subtracting fixed chrome from the root."""
        try:
            self.root.update_idletasks()

            self._apply_dashboard_orientation()

            root_h = int(self.root.winfo_height() or 0)
            if root_h < 200:
                return

            header_h = int(self.header.winfo_height() or self._base_header_h)
            status_h = int(self.status.winfo_height() or self._base_status_h)
            tab_sel_h = int(self._get_tab_selector_height())

            # Height available for the active tab content area
            tab_content_h = max(200, root_h - header_h - status_h - tab_sel_h)

            # In portrait mode the two cards are stacked and need separate
            # budgets; sharing the landscape height would make the second card
            # overflow the visible dashboard.
            portrait = bool(getattr(self, "_portrait_layout", False))

            # WICHTIG: Im Portrait-Modus haben body's drei Zeilen (Energie/
            # Puffer/Sparkline) inzwischen ALLE ein weight>0 (Verhaeltnis
            # 1:3:1 in _apply_dashboard_orientation) und teilen sich die
            # komplette verfuegbare Hoehe robust untereinander auf. Die hier
            # unten berechneten sparkline_h/energy_view_h/buffer_view_h
            # basieren dagegen auf root_h/header_h/status_h-Messungen, die
            # sich auf diesem Geraet als zu klein/veraltet herausgestellt
            # haben (root_h wird offenbar nicht immer zum Zeitpunkt des
            # Aufrufs schon korrekt gemeldet). Wenn wir diese Werte trotzdem
            # per configure(height=...)/grid_propagate(False)/resize() auf
            # die Karten und inneren Views draufpinnen, kollidiert das mit
            # der Grid-Gewichtung: Am Ende bleiben Energiefluss-Icons und
            # Puffer-Heatmap auf der alten, zu kleinen Groesse haengen,
            # obwohl die Karten drumherum laengst per Gewicht groesser
            # gezogen wurden - sichtbar als leerer schwarzer Rand um die
            # Diagramme. Im Portrait-Modus ueberspringen wir das Pinning
            # daher komplett und verlassen uns ausschliesslich auf die
            # Grid-Gewichte plus die eigene <Configure>-Behandlung jeder
            # Karte (Matplotlib macht das automatisch, energy_flow.py seit
            # dem Scaling-Loop-Fix ebenfalls sauber). Im Landscape-Modus
            # bleibt das bisherige Pinning unveraendert bestehen.
            if not portrait:
                # Sparkline soll nur etwa 20% der Tab-Hoehe einnehmen, mit
                # einer Mindesthoehe fuer Lesbarkeit und einer Obergrenze,
                # damit sie auf sehr grossen Bildschirmen nicht unnoetig
                # gross wird.
                sparkline_h = max(130, min(220, int(tab_content_h * 0.20)))
                row0_h = max(160, tab_content_h - sparkline_h - 18)

                try:
                    if hasattr(self, "sparkline_card"):
                        self.sparkline_card.configure(height=sparkline_h)
                        try:
                            self.sparkline_card.grid_propagate(False)
                        except Exception:
                            pass
                    if hasattr(self, "sparkline_view") and hasattr(self.sparkline_view, "set_target_height"):
                        self.sparkline_view.set_target_height(sparkline_h)
                except Exception:
                    pass

                energy_view_h = max(180, row0_h - 52)
                buffer_view_h = energy_view_h

                if hasattr(self, "energy_card"):
                    try:
                        self.energy_card.grid_propagate(True)
                    except Exception:
                        pass
                if hasattr(self, "buffer_card"):
                    try:
                        self.buffer_card.grid_propagate(True)
                    except Exception:
                        pass

                if hasattr(self, "energy_view") and hasattr(self.energy_view, "resize"):
                    try:
                        self.energy_view.resize(self.energy_view.width, energy_view_h)
                    except Exception:
                        pass

                if hasattr(self, "buffer_view"):
                    try:
                        self.buffer_view.configure(height=buffer_view_h)
                        self.buffer_view.height = buffer_view_h
                        if hasattr(self.buffer_view, "resize"):
                            self.buffer_view.resize(buffer_view_h)
                    except Exception:
                        pass

            # If we're still in the very early init phase, some widgets report height=1.
            # Retry a few times so the budget is applied after the window is mapped.
            try:
                attempts = int(getattr(self, "_height_budget_attempts", 0))
            except Exception:
                attempts = 0
            try:
                self._height_budget_attempts = attempts + 1
            except Exception:
                pass

            try:
                energy_canvas_h = int(self.energy_view.canvas.winfo_height() or 0) if hasattr(self, "energy_view") else 0
            except Exception:
                energy_canvas_h = 0

            if energy_canvas_h <= 5 and attempts < 5:
                try:
                    self.root.after(250, self._apply_compact_height_budget)
                except Exception:
                    pass

        except Exception:
            pass

    def _apply_dashboard_orientation(self) -> None:
        """Stack dashboard cards in portrait mode and restore the wide layout."""
        try:
            width = int(self.root.winfo_width() or 0)
            height = int(self.root.winfo_height() or 0)
            if width < 200 or height < 200 or not hasattr(self, "body"):
                return

            portrait = height > width
            if portrait == getattr(self, "_portrait_layout", None):
                return
            self._portrait_layout = portrait

            for tab_name in (
                "tado_tab",
                "health_tab",
                "status_tab",
                "homeassistant_actions_tab",
                "hue_tab",
                "spotify_tab",
                "system_tab",
                "calendar_tab",
                "historical_tab",
                "tagesproduktion_tab",
                "ertrag_tab",
            ):
                tab = getattr(self, tab_name, None)
                setter = getattr(tab, "set_portrait_layout", None) if tab else None
                if callable(setter):
                    try:
                        setter(portrait)
                    except Exception:
                        # Don't let one tab's failure abort the loop and
                        # silently skip every tab listed after it.
                        logger.debug("set_portrait_layout failed for %s", tab_name, exc_info=True)

            if portrait:
                self.body.grid_columnconfigure(0, weight=1, minsize=0)
                self.body.grid_columnconfigure(1, weight=0, minsize=0)
                # Alle drei Zeilen bekommen ein festes Gewichts-Verhaeltnis
                # 2:2:1 (= 40% Energie / 40% Puffer / 20% Sparkline der
                # verfuegbaren Koerperhoehe). minsize bleibt als
                # Schutz-Untergrenze fuer sehr kleine Fenster erhalten, greift
                # aber auf einem echten Bildschirm praktisch nie.
                #
                # Vorherige Versuche:
                # - weight=1 NUR fuer die Sparkline-Zeile (Energie/Puffer
                #   weight=0): die Sparkline bekam JEDEN uebrigen Pixel und
                #   wurde dadurch viel groesser als gewuenscht.
                # - weight=0 fuer ALLE drei Zeilen (in der Annahme,
                #   _apply_compact_height_budget() wuerde per configure(
                #   height=...) exakte Pixelhoehen vorgeben, was nur bei
                #   weight=0 respektiert wird): das fuehrte zu einer riesigen
                #   schwarzen Luecke unter der Sparkline, weil diese
                #   Pixel-Berechnung (basierend auf root_h/header_h/status_h
                #   zu einem fruehen/ungenauen Zeitpunkt) auf diesem Geraet
                #   deutlich zu klein ausfaellt und dann NIEMAND den Rest der
                #   Flaeche auffuellt.
                # - weight=2:2:1, ERSTER Versuch: fuehrte dazu, dass
                #   _apply_compact_height_budget()'s Pinning-Versuch
                #   (configure(height=...) + grid_propagate(False), fuer
                #   veraltete/zu kleine Werte gedacht) mit der Gewichtung
                #   kollidierte - die Karten wurden per Gewicht groesser
                #   gezogen, aber Energiefluss-Icons/Puffer-Heatmap blieben
                #   auf der alten kleinen Groesse haengen (schwarzer Rand um
                #   die Diagramme). Das Pinning fuer Portrait wurde deshalb in
                #   _apply_compact_height_budget() komplett entfernt - siehe
                #   Kommentar dort.
                # - weight=1:3:1 (20%/60%/20%): nachdem das Pinning-Problem
                #   behoben war, fuellte der Inhalt sein Feld korrekt, aber
                #   die Puffer-Heatmap wirkte optisch viel zu dominant/lang
                #   gegenueber dem kleinen Energiefluss-Bereich.
                # Zurueck auf 2:2:1, jetzt OHNE das Pinning-Problem von oben
                # (das ist bereits behoben) - damit teilen sich Energiefluss
                # und Puffer/Warmwasser die Flaeche gleichmaessig.
                # Ein festes Gewichts-Verhaeltnis ist robust gegen
                # Messungenauigkeiten, weil es sich immer auf die tatsaechlich
                # verfuegbare Hoehe bezieht statt auf eine vorab berechnete
                # Pixelzahl - und jede der drei Karten passt ihren Inhalt
                # (Matplotlib-Figures bzw. das Energiefluss-Canvas) ueber ihre
                # eigene <Configure>-Behandlung automatisch an die tatsaechlich
                # zugewiesene Groesse an.
                self.body.grid_rowconfigure(0, weight=2, minsize=200)
                self.body.grid_rowconfigure(1, weight=2, minsize=380)
                self.body.grid_rowconfigure(2, weight=1, minsize=100)
                self.energy_card.grid_configure(row=0, column=0, columnspan=1, sticky="nsew")
                self.buffer_card.grid_configure(row=1, column=0, columnspan=1, sticky="nsew")
                self.sparkline_card.grid_configure(row=2, column=0, columnspan=1, sticky="nsew", padx=6, pady=(0, 6))
            else:
                self.body.grid_columnconfigure(0, weight=4, minsize=0)
                self.body.grid_columnconfigure(1, weight=1, minsize=360)
                self.body.grid_rowconfigure(0, weight=1, minsize=0)
                self.body.grid_rowconfigure(1, weight=0, minsize=0)
                self.body.grid_rowconfigure(2, weight=0, minsize=0)
                self.energy_card.grid_configure(row=0, column=0, columnspan=1, sticky="nsew")
                self.buffer_card.grid_configure(row=0, column=1, columnspan=1, sticky="nsew")
                self.sparkline_card.grid_configure(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        except Exception:
            logger.debug("Dashboard orientation update failed", exc_info=True)

    # Duplicate _apply_windowed removed (F811)

    def toggle_fullscreen(self):
        """Wechselt zwischen Vollbild und Fenstermodus."""
        if getattr(self, 'is_fullscreen', False):
            _dbg_print("[WINDOW] Wechsel zu Fenstermodus")
            self._apply_windowed()
            self.status.update_status("Fenstermodus")
        else:
            _dbg_print("[FULLSCREEN] Wechsel zu Vollbild")
            self._apply_fullscreen()
            self.status.update_status("Vollbild")

    def minimize_window(self):
        """Minimiert das Fenster zuverlässig."""
        try:
            _dbg_print("[MINIMIZE] Iconify window (normales Minimieren)...")
            self.root.iconify()
            self.status.update_status("Minimiert (Taskleiste/Alt+Tab)")
        except Exception as e:
            logger.warning("Minimize fehlgeschlagen: %s", e)

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
            
            _dbg_print(f"[LAYOUT] Initial view height: {view_h}px (body: {body_h}, available: {available})")
        except Exception as e:
            logger.warning("Initial sizing failed: %s", e)

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
                if abs(current_energy_h - view_h) >= 2 and hasattr(self.energy_view, "resize"):
                    self.energy_view.resize(self.energy_view.width, view_h)
                
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
        _dbg_print("[SCALING] _apply_runtime_scaling called (deprecated, doing nothing)")
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
            
            if DEBUG_LOG:
                _dbg_print(f"[DEBUG] Heights: root={root_h}, header={header_h}, notebook={notebook_h}, body={body_h}, energy={energy_h}, buffer={buffer_h}")
        except Exception as e:
            if DEBUG_LOG:
                _dbg_print(f"[DEBUG] Height logging failed: {e}")

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

    def handle_wechselrichter_data(self, data: dict):
        """Echtzeit-PV-Daten aus dem Worker-Thread übernehmen."""
        process_wechselrichter_data(data, self.app_state, self._last_data, self._source_health)

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
        if not self.datastore:
            logger.debug("[SPARKLINE] Kein Datastore!")
            return []
        cutoff = datetime.now() - timedelta(minutes=minutes)
        hours = max(1, (minutes // 60) + 1)
        rows = self.datastore.get_recent_fronius(hours=hours, limit=1200)
        values: list[float] = []
        for row in rows[-400:]:
            ts = self._parse_timestamp_value(row.get('timestamp'))
            pv_kw = row.get('pv')
            if ts is None or pv_kw is None:
                continue
            if ts < cutoff:
                continue
            values.append(float(pv_kw))
        return values


def run():
    # Zentrales File-Logging fuer die ganze App: der Nutzer startet nicht
    # ueber eine Konsole, kann also weder stdout noch das Standard-
    # "handler of last resort" (nur WARNING+ auf stderr, ohne Datei) sehen.
    # Ohne diesen Handler landen z.B. alle logging.info(...)-Aufrufe in
    # tabs/tado.py (Geraete-URL, Button-Klicks, Browser-Oeffnen-Versuche)
    # nirgendwo, wo sie abrufbar waeren. Schreibt bei jedem Start frisch
    # (mode="w"), damit der Log genau die aktuelle Session zeigt.
    try:
        log_path = os.path.join(_PROJECT_ROOT, "data", "app_debug.log")
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        file_handler = logging.FileHandler(log_path, mode="w", encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"
        ))
        root_logger.addHandler(file_handler)
        logging.info("=== App-Start, File-Logging aktiv: %s ===", log_path)
    except Exception:
        pass

    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    root._set_appearance_mode("dark")  # Force dark mode
    # Setze root Hintergrund auf dunkel - behebt hellgraue Flächen
    try:
        root.configure(fg_color=COLOR_ROOT)
    except:
        pass  # Falls fg_color nicht unterstützt wird
    app = MainApp(root)
    root.mainloop()

if __name__ == "__main__":
    run()

