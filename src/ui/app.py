import tkinter as tk
import customtkinter as ctk
import os

DEBUG_LOG = os.environ.get("DASHBOARD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
_show_status = os.environ.get("DASHBOARD_SHOW_STATUS_TAB", "").strip().lower() in ("1", "true", "yes", "on")
_hide_status = os.environ.get("DASHBOARD_HIDE_STATUS_TAB", "").strip().lower() in ("1", "true", "yes", "on")
# Default: hidden (user requested). Re-enable via DASHBOARD_SHOW_STATUS_TAB=1.
SHOW_STATUS_TAB = bool(_show_status) and not bool(_hide_status)


def _dbg_print(msg: str) -> None:
    if DEBUG_LOG:
        print(msg, flush=True)
import platform
import shutil
import subprocess
from datetime import datetime, timedelta
from datetime import timezone
import logging
import time
import threading
import queue
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
    get_safe_font,
)
from ui.components.card import Card
from ui.components.header import HeaderBar
from ui.components.statusbar import StatusBar
from ui.components.rounded import RoundedFrame
from ui.views.energy_flow import EnergyFlowView
from ui.views.buffer_storage import BufferStorageView
from ui.views.pv_sparkline import PVSparklineView
from ui.app_state import AppState
from ui.state_schema import validate_payload

from core.datastore import DataStore, get_shared_datastore
from core.homeassistant import HomeAssistantClient, load_homeassistant_config

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

try:
    from tabs.tagesproduktion import TagesproduktionTab
except ImportError:
    TagesproduktionTab = None


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
    def _start_ui_pump(self) -> None:
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
                self.root.after(50, pump)
            except Exception:
                pass

        try:
            self.root.after(0, pump)
        except Exception:
            pass

    def _post_ui(self, callback) -> None:
        try:
            self._ui_queue.put(callback)
        except Exception:
            pass

    @staticmethod
    def _safe_parse_iso_dt(value: str | None) -> datetime | None:
        if not value:
            return None
        try:
            raw = str(value).strip()
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                # Treat naive timestamps as local time.
                dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return dt
        except Exception:
            return None

    @staticmethod
    def _format_age_short(seconds: float | None) -> str:
        if seconds is None:
            return "--"
        try:
            total = max(0, int(seconds))
        except Exception:
            return "--"
        if total < 60:
            return f"{total}s"
        minutes, _ = divmod(total, 60)
        if minutes < 60:
            return f"{minutes}m"
        hours, minutes = divmod(minutes, 60)
        if hours < 24:
            return f"{hours}h {minutes:02d}m"
        days, hours = divmod(hours, 24)
        return f"{days}d {hours:02d}h"

    def _compute_last_heating_event_dt(self) -> datetime | None:
        """Heuristik: letzte 'Einheiz'-Phase erkennen.

        User-Spec (aktuell):
        - Temperaturen müssen davor NICHT fallen.
        - Entscheidend ist, ob innerhalb der letzten ~2h ein klarer Anstieg vorhanden ist.
        - Beim Einheizen steigt die Kesseltemperatur schnell und stark an,
          während Puffer oben ggf. nur langsam und nur um wenige Grad (3–5°C) steigt.

        Rückgabewert: Zeitpunkt des (geschätzten) Startpunkts des Einheizens.
        """
        if not getattr(self, "datastore", None):
            return None
        try:
            # Request more than 2h to tolerate irregular sampling; filter below.
            rows = self.datastore.get_recent_heating(hours=6, limit=2600)
        except Exception:
            return None

        points: list[tuple[datetime, float, float]] = []
        for row in rows:
            dt = self._safe_parse_iso_dt(row.get("timestamp"))
            if dt is None:
                continue
            try:
                kessel = float(row.get("kessel")) if row.get("kessel") is not None else None
            except Exception:
                kessel = None
            buf_val = row.get("top")
            try:
                puffer = float(buf_val) if buf_val is not None else None
            except Exception:
                puffer = None
            if kessel is None or puffer is None:
                continue
            points.append((dt, kessel, puffer))

        if len(points) < 8:
            return None

        # Ensure chronological order (datastore ordering can vary).
        try:
            points.sort(key=lambda x: x[0])
        except Exception:
            pass

        now_dt = points[-1][0]
        window_start = now_dt - timedelta(hours=2)
        # Keep only the last 2h window (plus 1 extra point before for slope checks).
        start_idx = 0
        for idx in range(len(points)):
            if points[idx][0] >= window_start:
                start_idx = max(0, idx - 1)
                break
        points = points[start_idx:]

        if len(points) < 6:
            return None

        # Parameters tuned for noisy sensor data and a short (2h) window.
        # A "heating event" is primarily a fast, strong Kessel rise.
        KESSEL_RISE_STRONG_DEG = 10.0
        KESSEL_RISE_MIN_DEG = 8.0
        KESSEL_LOOKAHEAD_MINUTES = 60
        # Optional small Puffer rise confirmation (slow, few degrees).
        PUFFER_RISE_MIN_DEG = 2.5
        PUFFER_LOOKAHEAD_MINUTES = 120
        # Start-of-rise: require short-term positive move to avoid noise.
        START_DELTA_EPS = 0.3
        START_CONFIRM_MINUTES = 15
        # Ignore wild single-step spikes.
        MAX_SINGLE_STEP_DEG = 4.0

        last_event: datetime | None = None

        for i in range(1, len(points) - 2):
            dt_i, k_i, p_i = points[i]

            # Basic spike guard
            try:
                prev_dt, prev_k, prev_p = points[i - 1]
                if abs(k_i - prev_k) > MAX_SINGLE_STEP_DEG and (dt_i - prev_dt).total_seconds() < 20 * 60:
                    continue
            except Exception:
                pass

            # 1) Confirm start-of-rise with a short confirmation window on Kessel.
            dt_confirm = dt_i + timedelta(minutes=START_CONFIRM_MINUTES)
            k_confirm_max = k_i
            for t in range(i + 1, len(points)):
                if points[t][0] > dt_confirm:
                    break
                if points[t][1] > k_confirm_max:
                    k_confirm_max = points[t][1]
            if (k_confirm_max - k_i) < START_DELTA_EPS:
                continue

            # 2) Look ahead for strong Kessel rise.
            dt_k_limit = dt_i + timedelta(minutes=KESSEL_LOOKAHEAD_MINUTES)
            max_k = k_i
            for t in range(i + 1, len(points)):
                if points[t][0] > dt_k_limit:
                    break
                if points[t][1] > max_k:
                    max_k = points[t][1]
            k_rise = max_k - k_i
            if k_rise < KESSEL_RISE_MIN_DEG:
                continue

            # 3) Optional Puffer rise (slow). Only require it for the weaker Kessel case.
            dt_p_limit = dt_i + timedelta(minutes=PUFFER_LOOKAHEAD_MINUTES)
            max_p = p_i
            for t in range(i + 1, len(points)):
                if points[t][0] > dt_p_limit:
                    break
                if points[t][2] > max_p:
                    max_p = points[t][2]
            p_rise = max_p - p_i

            if k_rise >= KESSEL_RISE_STRONG_DEG or p_rise >= PUFFER_RISE_MIN_DEG:
                # Keep the most recent detected event in the 2h window.
                last_event = dt_i

        return last_event

    def _refresh_status_metrics_if_needed(self, now_monotonic: float) -> None:
        if not hasattr(self, "_status_metrics"):
            self._status_metrics = {
                "last_refresh": 0.0,
                "pv_today_kwh": None,
                "last_heat_event_dt": None,
            }
        if now_monotonic - float(self._status_metrics.get("last_refresh") or 0.0) < 30.0:
            return

        # PV today (kWh)
        pv_today_kwh = None
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

        # Last heating event
        last_heat_event_dt = self._compute_last_heating_event_dt()

        self._status_metrics["pv_today_kwh"] = pv_today_kwh
        self._status_metrics["last_heat_event_dt"] = last_heat_event_dt
        self._status_metrics["last_refresh"] = now_monotonic
    def build_tabs(self):
        """Robustly rebuilds all tabs, ensuring correct references after UI changes (fullscreen, etc)."""
        # CTkTabview: Tabs können nicht dynamisch entfernt werden, daher nur _add_other_tabs aufrufen
        # Dashboard-Tab wurde bereits in __init__ erstellt
        try:
            self._add_other_tabs()
        except Exception as e:
            print(f"[build_tabs] Error adding tabs: {e}")
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
        now_mono = time.monotonic()
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
            if hasattr(self, "app_state") and self.app_state:
                self.app_state.update(data)
            else:
                if hasattr(self, 'energy_view'):
                    self.energy_view.update_data(data)
                if hasattr(self, 'buffer_view'):
                    try:
                        self.buffer_view.update_data(data)
                    except Exception:
                        logging.exception("buffer_view update_data failed")
                if hasattr(self, 'sparkline_view'):
                    try:
                        self.sparkline_view.update_data(data)
                    except Exception:
                        logging.exception("sparkline_view update_data failed")
                if hasattr(self, 'historical_tab'):
                    try:
                        self.historical_tab.update_data(data)
                    except Exception:
                        logging.exception("historical_tab update_data failed")

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
                    heat_part = f"Einheizen: {last_heat_event_dt.strftime('%H:%M')} (vor {self._format_age_short(age_s)})"
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
                    from core.schema import BMK_BETRIEBSMODUS
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

            def _compose_status(max_len: int = 140) -> str:
                sep = " • "
                parts: list[str] = []
                for item in (mode_part, heat_part, pv_part, cal_text):
                    t = (item or "").strip()
                    if t:
                        parts.append(t)

                # Drop least important parts first if too long (calendar)
                def joined(p: list[str]) -> str:
                    return sep.join(p)

                if len(joined(parts)) <= max_len:
                    return joined(parts)

                # Remove calendar
                if cal_text and cal_text in parts:
                    parts = [p for p in parts if p != cal_text]
                if len(joined(parts)) <= max_len:
                    return joined(parts)
                s = joined(parts)
                if len(s) <= max_len:
                    return s

                # Last resort: hard cut (rare, but prevents important bits from being cut at the very end)
                return (s[: max_len - 1] + "…") if max_len > 1 else "…"

            status_str = _compose_status(140)

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

    def handle_bmkdaten_data(self, data: dict):
        """Echtzeit-Heizungsdaten aus dem Worker-Thread übernehmen."""
        ts = self._parse_timestamp_value(data.get("Zeitstempel")) or datetime.now()
        source = self._source_health.get("heating")
        if source:
            source["ts"] = ts
            source["count"] += 1

        kessel = _safe_float(data.get("Kesseltemperatur") or data.get("kesseltemp"))
        warmwasser = _safe_float(data.get("Warmwasser") or data.get("Warmwassertemperatur") or data.get("warmwasser"))
        outdoor = _safe_float(data.get("Außentemperatur") or data.get("Aussentemperatur") or data.get("outdoor"))
        top = _safe_float(data.get("Pufferspeicher Oben") or data.get("Puffer_Oben") or data.get("puffer_top"))
        mid = _safe_float(data.get("Pufferspeicher Mitte") or data.get("Pufferspeicher_Mitte") or data.get("puffer_mid"))
        bot = _safe_float(data.get("Pufferspeicher Unten") or data.get("Puffer_Unten") or data.get("puffer_bot"))

        def _format_bmk_mode(value) -> str | None:
            if value is None:
                return None
            try:
                if isinstance(value, bool):
                    return "1" if value else "0"
                if isinstance(value, (int, float)):
                    # avoid "1.0" noise for enum-like values
                    iv = int(value)
                    return str(iv)
                s = str(value).strip()
                return s if s else None
            except Exception:
                return None

        betriebsmodus = _format_bmk_mode(
            data.get("Betriebsmodus")
            or data.get("betriebsmodus")
            or data.get("Modus_Status")
            or data.get("Betriebsstatus")
        )

        if hasattr(self, "app_state") and self.app_state:
            from core.schema import (
                BMK_KESSEL_C,
                BMK_WARMWASSER_C,
                BMK_BETRIEBSMODUS,
                BUF_TOP_C,
                BUF_MID_C,
                BUF_BOTTOM_C,
            )

            payload = {
                "timestamp": data.get("Zeitstempel") or data.get("timestamp"),
                "outdoor": outdoor,
                BMK_KESSEL_C: kessel,
                BMK_WARMWASSER_C: warmwasser,
                BMK_BETRIEBSMODUS: betriebsmodus,
                BUF_TOP_C: top,
                BUF_MID_C: mid,
                BUF_BOTTOM_C: bot,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            self.app_state.update(payload)

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

        # Define base header and status heights - moderner mit mehr Platz
        self._base_header_h = 72  # Größerer Header (Buttons + Switch)
        self._base_status_h = 44  # Kompaktere Statusleiste

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
            on_leave=self.on_leave_home,
            on_come_home=self.on_come_home,
            on_shower=self.on_shower_go,
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
        self.buffer_card = Card(self.body, padding=0)
        self.buffer_card.grid(row=0, column=1, sticky="nsew", padx=6, pady=6)
        self.buffer_card.add_title("Warmwasser", icon="🔥")
        self.buffer_view = BufferStorageView(self.buffer_card.content(), height=320, datastore=self.datastore)
        self.buffer_view.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        self.sparkline_card = Card(self.body, padding=0)
        self.sparkline_card.grid(row=1, column=0, columnspan=2, sticky="ew", padx=6, pady=(0, 6))
        self.sparkline_view = PVSparklineView(self.sparkline_card.content(), datastore=self.datastore)
        self.sparkline_view.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)

        # Statusbar - moderner Style mit besserem Spacing
        self.status = StatusBar(self.main_container, on_exit=self.on_exit, on_toggle_fullscreen=self.toggle_fullscreen)
        self.status.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        self._apply_fullscreen()
        self.build_tabs()
        # Keep the header Hue switch in sync with the bridge state.
        self._start_hue_switch_sync()
        self.root.after(500, self.update_tick)

        # Apply a height budget once after initial layout settles so that
        # 600px-tall screens (1014x600 / 1024x600) don't clip content.
        try:
            self.root.after(350, self._apply_compact_height_budget)
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
                font=get_safe_font("Bahnschrift", 12, "bold"),
                height=32,
                corner_radius=12,
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
                print(f"[ERROR] HueTab initialization failed: {e}")
                import traceback
                traceback.print_exc()
                self.hue_tab = None

        # HomeA (Home Assistant Automationen/Skripte) soll als 3. Tab erscheinen
        if HomeAssistantActionsTab:
            try:
                _dbg_print("[TABS] HomeAssistantActionsTab wird erstellt...")
                self.tabview.add("HomeA")
                ha_frame = self.tabview.tab("HomeA")
                try:
                    ha_frame.configure(fg_color=COLOR_ROOT)
                except:
                    pass
                self.homeassistant_actions_tab = HomeAssistantActionsTab(self.root, self.notebook, tab_frame=ha_frame)
                _dbg_print("[TABS] HomeAssistantActionsTab erfolgreich hinzugefügt.")
            except Exception as e:
                print(f"[ERROR] HomeAssistantActionsTab init failed: {e}")
                self.homeassistant_actions_tab = None
        
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

        if TagesproduktionTab:
            try:
                _dbg_print("[TABS] TagesproduktionTab wird erstellt...")
                self.tabview.add(emoji("📊 Tagesproduktion", "Tagesproduktion"))
                prod_frame = self.tabview.tab(emoji("📊 Tagesproduktion", "Tagesproduktion"))
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
                print(f"[ERROR] TagesproduktionTab init failed: {e}")
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
                print(f"[ERROR] StatusTab init failed: {e}")
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
                print(f"[ERROR] HealthTab init failed: {e}")
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
        """Header-Button 🏃 (Away): startet die Leaving-Home Automation."""

        self._trigger_ha_automation(
            "automation.leaving_home_alles_licht_aus_heizung_aus_spotify_iphone",
            status_label="Leaving Home",
        )

    def on_come_home(self):
        """Header-Button 🏠 (Home): startet die Coming-Home Automation."""

        self._trigger_ha_automation(
            "automation.schlafzimmer_coming_home_resume",
            status_label="Coming Home",
        )

    def on_shower_go(self) -> None:
        """Header-Button 🚿: führt das Script 'duschen gehen' aus."""

        entity_id = self._get_shower_script_entity_id()
        self._trigger_ha_script(entity_id, status_label="Duschen gehen")

    def _get_shower_script_entity_id(self) -> str:
        try:
            env = (os.environ.get("SHOWER_SCRIPT_ENTITY_ID") or "").strip()
            if env:
                return env
        except Exception:
            pass

        try:
            client = self._get_presence_ha_client()
            cfg = getattr(client, "config", None) if client else None
            val = getattr(cfg, "shower_script_entity_id", None) if cfg else None
            val = str(val or "").strip()
            if val:
                return val
        except Exception:
            pass

        return "script.duschen_gehen"

    def _trigger_ha_script(self, entity_id: str, status_label: str) -> None:
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return

        try:
            self.status.update_status(f"Script: {status_label}…")
        except Exception:
            pass

        def worker() -> None:
            ok = False
            err = ""
            try:
                client = self._get_presence_ha_client()
                if not client:
                    err = "Home Assistant nicht konfiguriert"
                else:
                    ok = bool(client.call_service("script", "turn_on", {"entity_id": entity_id}))
            except Exception as exc:
                ok = False
                err = str(exc)

            def apply() -> None:
                try:
                    if ok:
                        self.status.update_status(f"Script: {status_label} gestartet")
                    else:
                        msg = err or "fehlgeschlagen"
                        self.status.update_status(f"Script: Fehler ({msg})")
                except Exception:
                    pass

            try:
                self._post_ui(apply)
            except Exception:
                pass

        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            pass

    def _trigger_ha_automation(self, entity_id: str, status_label: str) -> None:
        entity_id = str(entity_id or "").strip()
        if not entity_id:
            return

        try:
            self.status.update_status(f"Automation: {status_label}…")
        except Exception:
            pass

        def worker() -> None:
            ok = False
            err = ""
            try:
                client = self._get_presence_ha_client()
                if not client:
                    err = "Home Assistant nicht konfiguriert"
                else:
                    ok = bool(client.call_service("automation", "trigger", {"entity_id": entity_id}))
            except Exception as exc:
                ok = False
                err = str(exc)

            def apply() -> None:
                try:
                    if ok:
                        self.status.update_status(f"Automation: {status_label} gestartet")
                    else:
                        msg = err or "fehlgeschlagen"
                        self.status.update_status(f"Automation: Fehler ({msg})")
                except Exception:
                    pass

            try:
                self._post_ui(apply)
            except Exception:
                pass

        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception:
            pass

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

    def _presence_override_start(self, location_name: str, minutes: int = 10) -> None:
        """Start (or restart) a temporary presence override."""

        # Cancel existing timer.
        try:
            self._presence_override_stop(silent=True)
        except Exception:
            pass

        # Configuration (fixed by user request: 10 minutes override).
        try:
            duration_s = max(60, int(minutes) * 60)
        except Exception:
            duration_s = 600
        refresh_s = 30  # keep-alive cadence (tracker mode)

        self._presence_override_person_entity_id = "person.laurenz"
        # Optional script-based override (recommended): create two HA scripts that
        # implement the 10-minute override logic via helpers/templates.
        script_home = (os.environ.get("PRESENCE_OVERRIDE_SCRIPT_HOME") or "").strip()
        script_away = (os.environ.get("PRESENCE_OVERRIDE_SCRIPT_AWAY") or "").strip()
        if script_home and script_away:
            self._presence_override_mode = "script"
            self._presence_override_script_entity_id = script_home if str(location_name).strip() == "home" else script_away
            # No keep-alive needed; HA owns the timer. We just keep the UI timer.
            refresh_s = 30
        else:
            self._presence_override_mode = "tracker"
            self._presence_override_script_entity_id = None
            # Mobile-app trackers can't be overridden reliably via device_tracker.see.
            # Use a dedicated manual tracker entity that you add to the person in HA.
            self._presence_override_tracker_entity_id = os.environ.get(
                "PRESENCE_OVERRIDE_TRACKER_ENTITY_ID",
                "device_tracker.laurenz_override",
            ).strip() or "device_tracker.laurenz_override"
        self._presence_override_location_name = str(location_name or "").strip() or "home"
        self._presence_override_refresh_s = int(refresh_s)
        self._presence_override_deadline_mono = time.monotonic() + float(duration_s)
        self._presence_override_after_id = None
        self._presence_override_last_ok = None
        self._presence_override_last_error = ""

        try:
            pretty = "HOME" if self._presence_override_location_name == "home" else "AWAY"
            self.status.update_status(f"Presence Override: {pretty} (10m)")
        except Exception:
            pass

        # Trigger immediately and schedule keep-alives.
        self._presence_override_tick()

    def _presence_override_stop(self, silent: bool = False) -> None:
        after_id = getattr(self, "_presence_override_after_id", None)
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                pass

        self._presence_override_after_id = None
        self._presence_override_deadline_mono = None
        self._presence_override_location_name = None

        if not silent:
            try:
                self.status.update_status("Presence Override: beendet")
            except Exception:
                pass

    def _presence_override_tick(self) -> None:
        deadline = getattr(self, "_presence_override_deadline_mono", None)
        if not deadline:
            return

        now = time.monotonic()
        if now >= float(deadline):
            self._presence_override_stop(silent=False)
            return

        person_ent = getattr(self, "_presence_override_person_entity_id", "person.laurenz")
        mode = str(getattr(self, "_presence_override_mode", "tracker") or "tracker")
        script_ent = getattr(self, "_presence_override_script_entity_id", None)
        tracker_ent = getattr(self, "_presence_override_tracker_entity_id", "device_tracker.laurenz_override")
        location_name = getattr(self, "_presence_override_location_name", "home")
        refresh_s = int(getattr(self, "_presence_override_refresh_s", 30) or 30)
        refresh_s = max(10, min(120, refresh_s))

        # Run the actual HA call in a background thread.
        def worker() -> None:
            ok = False
            err = ""
            try:
                client = self._get_presence_ha_client()
                if not client:
                    err = "Home Assistant nicht konfiguriert"
                else:
                    if mode == "script":
                        if not script_ent:
                            err = "Override-Script nicht konfiguriert"
                        else:
                            ok = bool(client.call_service("script", "turn_on", {"entity_id": str(script_ent)}))
                            if not ok:
                                err = "script.turn_on fehlgeschlagen"
                    else:
                        # If the override tracker isn't attached to the person in HA, the person won't follow.
                        try:
                            pst = client.get_state(person_ent) or {}
                            trackers = (pst.get("attributes") or {}).get("device_trackers") or []
                            if isinstance(trackers, list) and tracker_ent not in [str(x) for x in trackers]:
                                err = f"Bitte {tracker_ent} bei {person_ent} hinzufügen"
                        except Exception:
                            pass

                        ok = bool(client.force_person_presence(person_ent, location_name, device_tracker_entity_id=tracker_ent))
                        if not ok:
                            err = "device_tracker.see fehlgeschlagen"
            except Exception as exc:
                ok = False
                err = str(exc)

            def apply() -> None:
                prev_ok = getattr(self, "_presence_override_last_ok", None)
                prev_err = str(getattr(self, "_presence_override_last_error", "") or "")
                self._presence_override_last_ok = bool(ok)
                self._presence_override_last_error = str(err or "")

                # Only update status when state changes (avoid spam every 30s).
                if prev_ok is None:
                    if not ok and err:
                        try:
                            self.status.update_status(f"Presence Override: Fehler ({err})")
                        except Exception:
                            pass
                    return

                if bool(prev_ok) != bool(ok):
                    if ok:
                        try:
                            pretty = "HOME" if location_name == "home" else "AWAY"
                            self.status.update_status(f"Presence Override: {pretty} (10m)")
                        except Exception:
                            pass
                    else:
                        msg = err or prev_err
                        if msg:
                            try:
                                self.status.update_status(f"Presence Override: Fehler ({msg})")
                            except Exception:
                                pass

            try:
                self._post_ui(apply)
            except Exception:
                pass

        try:
            # In script mode we only need to fire once (first tick).
            if mode == "script":
                if getattr(self, "_presence_override_last_ok", None) is None:
                    threading.Thread(target=worker, daemon=True).start()
            else:
                threading.Thread(target=worker, daemon=True).start()
        except Exception:
            pass

        # Reschedule keep-alive.
        try:
            self._presence_override_after_id = self.root.after(refresh_s * 1000, self._presence_override_tick)
        except Exception:
            self._presence_override_after_id = None

    def _apply_fullscreen(self):
        """Setzt echtes Vollbild (ohne overrideredirect) und zentriert das Fenster."""
        try:
            self.root.attributes("-fullscreen", True)
            self.is_fullscreen = True
            self.root.resizable(False, False)
            sw = max(1, self.root.winfo_screenwidth())
            sh = max(1, self.root.winfo_screenheight())
            w = min(sw, 1024)
            h = min(sh, 600)
            self.root.geometry(f"{w}x{h}+0+0")
        except Exception:
            pass

        # Ensure compact layouts fit on small screens (e.g. 1014x600)
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

    def _apply_compact_height_budget(self) -> None:
        """Compute available height by subtracting header + tab selector + statusbar.

        This prevents vertical clipping on 600px-tall screens.
        """
        try:
            self.root.update_idletasks()

            root_h = int(self.root.winfo_height() or 0)
            if root_h < 200:
                return

            header_h = int(self.header.winfo_height() or self._base_header_h)
            status_h = int(self.status.winfo_height() or self._base_status_h)
            tab_sel_h = int(self._get_tab_selector_height())

            # Height available for the active tab content area
            tab_content_h = max(200, root_h - header_h - status_h - tab_sel_h)

            # Reserve a compact sparkline row so row0 (energy+buffer) always fits.
            sparkline_h = max(88, min(140, int(tab_content_h * 0.22)))

            # Account for grid paddings in the dashboard body.
            row0_h = max(160, tab_content_h - sparkline_h - 18)

            # Apply sparkline sizing (also shrinks matplotlib figure inside)
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

            # Title/header inside cards takes some vertical space; keep views conservative.
            view_h = max(140, row0_h - 52)

            if hasattr(self, "energy_view") and hasattr(self.energy_view, "canvas"):
                try:
                    self.energy_view.canvas.config(height=view_h)
                    self.energy_view.height = view_h
                except Exception:
                    pass

            if hasattr(self, "buffer_view"):
                try:
                    self.buffer_view.configure(height=view_h)
                    self.buffer_view.height = view_h
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

        if hasattr(self, "app_state") and self.app_state:
            from core.schema import (
                PV_POWER_KW,
                GRID_POWER_KW,
                BATTERY_POWER_KW,
                BATTERY_SOC_PCT,
                LOAD_POWER_KW,
            )
            payload = {
                "timestamp": data.get("Zeitstempel") or data.get("timestamp"),
                PV_POWER_KW: pv_kw,
                GRID_POWER_KW: grid_kw,
                BATTERY_POWER_KW: batt_kw,
                BATTERY_SOC_PCT: soc,
                LOAD_POWER_KW: load_kw,
            }
            payload = {k: v for k, v in payload.items() if v is not None}
            self.app_state.update(payload)

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

    @staticmethod
    def _age_seconds(ts: datetime | None) -> float | None:
        if ts is None:
            return None
        try:
            if ts.tzinfo is not None and ts.tzinfo.utcoffset(ts) is not None:
                now = datetime.now(ts.tzinfo)
            else:
                now = datetime.now()
            return (now - ts).total_seconds()
        except Exception:
            return None

    def _update_status_summary(self):
        # Legacy: previously wrote data-age summaries into the footer.
        # The status bar now shows user-facing status messages, so we keep
        # freshness info internal (e.g. for the Health tab) and do not render
        # it in the StatusBar anymore.
        return

    def _update_freshness_and_sparkline(self):
        last_ts = self._get_last_timestamp()
        if last_ts:
            delta = datetime.now() - last_ts
            seconds = int(delta.total_seconds())
            self._data_fresh_seconds = seconds
        else:
            self._data_fresh_seconds = None

        # Sparkline moved into right card; keep footer minimal
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

