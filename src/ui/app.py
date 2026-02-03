import tkinter as tk
import os
import platform
import shutil
import subprocess
import csv
from datetime import datetime, timedelta
import json
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
    COLOR_CARD,
    COLOR_BORDER,
    emoji,
    EMOJI_OK,
)
from ui.components.card import Card
from ui.components.header import HeaderBar
from ui.components.statusbar import StatusBar
from ui.components.rounded import RoundedFrame
from ui.views.energy_flow import EnergyFlowView
from ui.views.buffer_storage import BufferStorageView

# SQLite DataStore for fast queries
try:
    from core.datastore import DataStore
    USE_DATASTORE = True
except ImportError:
    USE_DATASTORE = False
    print("[DB] DataStore nicht verfügbar - nutze CSV fallback")

_UI_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.dirname(_UI_DIR)
_PROJECT_ROOT = os.path.dirname(_SRC_DIR)
_DATA_ROOT = os.path.join(_PROJECT_ROOT, "data")


def _data_path(filename: str) -> str:
    return os.path.join(_DATA_ROOT, filename)


def _read_lines_safe(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.readlines()
    except Exception:
        return []


def _read_tail_lines(path: str, max_bytes: int = 262144, max_lines: int = 2000) -> list[str]:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read().decode("utf-8-sig", errors="replace")
        lines = chunk.splitlines()
    except Exception:
        lines = _read_lines_safe(path)

    if max_lines and len(lines) > max_lines:
        return lines[-max_lines:]
    return lines


def _read_last_data_line(path: str, max_bytes: int = 65536) -> str | None:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            chunk = f.read().decode("utf-8-sig", errors="replace")
        lines = chunk.splitlines()
    except Exception:
        lines = _read_lines_safe(path)

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        if line.lower().startswith("zeit"):
            continue
        return line
    return None


def _normalize_header(name: str) -> str:
    return (
        name.strip()
        .lower()
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("ß", "ss")
        .replace(" ", "")
        .replace("_", "")
    )


def _read_csv_header(path: str) -> list[str]:
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                return next(csv.reader([line]))
    except Exception:
        return []
    return []


def _read_last_row_dict(path: str) -> dict[str, str]:
    header = _read_csv_header(path)
    if not header:
        return {}
    last_line = _read_last_data_line(path)
    if not last_line:
        return {}
    try:
        row = next(csv.reader([last_line]))
    except Exception:
        return {}
    if len(row) < len(header):
        row += [""] * (len(header) - len(row))
    return dict(zip(header, row))


def _get_row_value(row_dict: dict[str, str], *keys: str) -> str | None:
    if not row_dict:
        return None
    norm_map = {_normalize_header(k): k for k in row_dict.keys()}
    for key in keys:
        norm = _normalize_header(key)
        if norm in norm_map:
            return row_dict.get(norm_map[norm])
    return None


def _safe_float(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _parse_bmk_row(row: list[str]) -> dict[str, str]:
    """Parse a BMK CSV row that contains the full PP data (Betriebsmodus as col 1)."""
    if not row or len(row) < 10:
        return {}
    if _safe_float(row[1]) is not None:
        return {}
    values = row[1:]
    return {
        "Betriebsmodus": values[0] if len(values) > 0 else "",
        "Kesseltemperatur": values[2] if len(values) > 2 else "",
        "Außentemperatur": values[3] if len(values) > 3 else "",
        "Pufferspeicher Oben": values[5] if len(values) > 5 else "",
        "Pufferspeicher Mitte": values[6] if len(values) > 6 else "",
        "Pufferspeicher Unten": values[7] if len(values) > 7 else "",
        "Warmwasser": values[13] if len(values) > 13 else "",
    }


def _parse_short_bmk_row(row: list[str]) -> dict[str, str]:
    """Parse a short 7-column BMK row (no Betriebsmodus)."""
    if not row or len(row) < 7:
        return {}
    return {
        "Kesseltemperatur": row[1],
        "Außentemperatur": row[2],
        "Pufferspeicher Oben": row[3],
        "Pufferspeicher Mitte": row[4],
        "Pufferspeicher Unten": row[5],
        "Warmwasser": row[6],
    }


def _is_plausible_bmk(values: dict[str, str]) -> bool:
    out_temp = _safe_float(values.get("Außentemperatur"))
    p_top = _safe_float(values.get("Pufferspeicher Oben"))
    p_mid = _safe_float(values.get("Pufferspeicher Mitte"))
    p_bot = _safe_float(values.get("Pufferspeicher Unten"))
    warm = _safe_float(values.get("Warmwasser"))

    if out_temp is None or not (-40 <= out_temp <= 50):
        return False
    for v in [p_top, p_mid, p_bot, warm]:
        if v is None or not (10 <= v <= 95):
            return False
    return True


def _get_last_valid_bmk_values(path: str, max_lines: int = 200) -> dict[str, str]:
    lines = _read_lines_safe(path)
    if not lines:
        return {}
    for line in reversed(lines[-max_lines:]):
        line = line.strip()
        if not line or line.lower().startswith("zeit"):
            continue
        try:
            row = next(csv.reader([line]))
        except Exception:
            continue
        parsed = _parse_bmk_row(row)
        if not parsed:
            parsed = _parse_short_bmk_row(row)
        if parsed and _is_plausible_bmk(parsed):
            return parsed
    return {}
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



class MainApp:
    """1024x600 Dashboard mit Grid-Layout, Cards, Header und Statusbar + Tabs."""

    def __init__(self, root: tk.Tk):
        self._start_time = time.time()
        self._debug_log = os.getenv("DASH_DEBUG", "0") == "1"
        self._configure_debounce_id = None
        self._last_size = (0, 0)
        self._resize_enabled = False
        self.root = root
        self.root.title("Smart Home Dashboard")
        
        # Initialize DataStore with parallel import
        self.datastore = None
        if USE_DATASTORE:
            self._init_datastore_async()
        
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
        self._apply_fullscreen(target_w, usable_h, offset_y)
        self.root.resizable(False, False)
        try:
            self.root.attributes("-fullscreen", True)
            self.root.attributes("-zoomed", True)
        except Exception:
            pass
        init_style(self.root)
        self._ensure_emoji_font()
        
        if self._debug_log:
            print(f"[DEBUG] Screen: {sw}x{sh}, Target: {target_w}x{target_h}")

        # Grid Setup: Minimize fixed row sizes to maximize content area
        self._base_header_h = 52
        self._base_tabs_h = 30
        self._base_status_h = 24
        self._base_energy_w = 460
        self._base_energy_h = 230
        self._base_buffer_h = 180
        self.root.grid_rowconfigure(0, minsize=self._base_header_h)
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_rowconfigure(2, minsize=self._base_status_h)
        self.root.grid_columnconfigure(0, weight=1)

        # Header
        self.header = HeaderBar(
            self.root,
            on_toggle_a=self.on_toggle_a,
            on_toggle_b=self.on_toggle_b,
            on_exit=self.on_exit,
        )
        self.header.grid(row=0, column=0, sticky="nsew", padx=8, pady=(4, 2))

        # Notebook (Tabs) inside rounded container
        self.notebook_container = RoundedFrame(self.root, bg=COLOR_HEADER, border=None, radius=18, padding=0)
        self.notebook_container.grid(row=1, column=0, sticky="nsew", padx=8, pady=0)
        self.notebook = ttk.Notebook(self.notebook_container.content())
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.notebook.grid_propagate(False)

        # Energy Dashboard Tab
        self.dashboard_tab = tk.Frame(self.notebook, bg=COLOR_ROOT)
        self.notebook.add(self.dashboard_tab, text=emoji("⚡ Energie", "Energie"))
        self.dashboard_tab.pack_propagate(False)

        # Body (Energy + Buffer)
        self.body = tk.Frame(self.dashboard_tab, bg=COLOR_ROOT)
        self.body.pack(fill=tk.BOTH, expand=True, padx=0, pady=0)
        self.body.grid_columnconfigure(0, weight=7)
        self.body.grid_columnconfigure(1, weight=3)
        self.body.grid_rowconfigure(0, weight=1)

        # Energy Card (70%) - reduced size and padding
        self.energy_card = Card(self.body, padding=6)
        self.energy_card.grid(row=0, column=0, sticky="nsew", padx=(0, 4), pady=0)
        self.energy_card.add_title("Energiefluss", icon="⚡")
        # LAYOUT FIX: Start with minimal size, will resize after layout settles
        self.energy_view = EnergyFlowView(self.energy_card.content(), width=240, height=200)
        self.energy_view.pack(fill=tk.BOTH, expand=True, pady=2)

        # Buffer Card (30%) - reduced size and padding
        self.buffer_card = Card(self.body, padding=6)
        self.buffer_card.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=0)
        # Update title to 'Kesseltemperatur' with appropriate icon
        self.buffer_card.add_title("Kesseltemperatur", icon="🔥")
        # LAYOUT FIX: Start with minimal height, will resize after layout settles
        self.buffer_view = BufferStorageView(self.buffer_card.content(), height=180)
        self.buffer_view.pack(fill=tk.BOTH, expand=True)

        # Statusbar
        self.status = StatusBar(self.root, on_exit=self.root.quit, on_toggle_fullscreen=self.toggle_fullscreen)
        self.status.grid(row=2, column=0, sticky="nsew", padx=8, pady=(2, 4))
        
        # [DEBUG] Instrumentation: Log when UI is fully built
        if self._debug_log:
            print(f"[LAYOUT] UI built at {time.time() - self._start_time:.3f}s")
        
        # LAYOUT FIX: Single update_idletasks() instead of 3x to avoid minimize lag
        self.root.update_idletasks()
        
        w = self.root.winfo_width()
        h = self.root.winfo_height()
        if self._debug_log:
            print(f"[LAYOUT] Size after 3x update_idletasks: {w}x{h}")
        
        # NOW get the actual canvas sizes and initialize views with correct dimensions
        try:
            energy_w = max(200, self.energy_view.canvas.winfo_width())
            energy_h = max(200, self.energy_view.canvas.winfo_height())
            buffer_h = max(160, self.buffer_view.winfo_height())
            if self._debug_log:
                print(f"[LAYOUT] Final widget sizes - Energy: {energy_w}x{energy_h}, Buffer: {buffer_h}")
            
            # Force views to initialize with these final sizes
            self.energy_view.width = energy_w
            self.energy_view.height = energy_h
            self.energy_view.nodes = self.energy_view._define_nodes()
            self.energy_view._base_img = self.energy_view._render_background()
            self.energy_view.canvas.config(width=energy_w, height=energy_h)
            
            self.buffer_view.height = buffer_h
            self.buffer_view.configure(height=buffer_h)
        except Exception as e:
            if self._debug_log:
                print(f"[LAYOUT] Error setting initial sizes: {e}")
        
        # Mark layout as stable immediately (no delay needed now)
        self._layout_stable = True
        if self._debug_log:
            print(f"[LAYOUT] Marked stable at {time.time() - self._start_time:.3f}s")


        # Status Tab entfernt

        # Add PV Status Tab
        self.pv_status_tab = tk.Frame(self.notebook, bg=COLOR_ROOT)
        self.notebook.add(self.pv_status_tab, text=emoji("🔆 PV Status", "PV Status"))
        self._init_pv_status_tab()

        # Add other tabs
        self._add_other_tabs()

    def _init_pv_status_tab(self):
        frame = self.pv_status_tab
        self.pv_status_pv = tk.StringVar(value="-- kW")
        self.pv_status_batt = tk.StringVar(value="-- %")
        self.pv_status_grid = tk.StringVar(value="-- kW")
        self.pv_status_recommend = tk.StringVar(value="--")
        self.pv_status_time = tk.StringVar(value="--")
        row = 0
        tk.Label(frame, text="PV-Leistung", font=("Segoe UI", 16), fg="#0ea5e9", bg=COLOR_ROOT).grid(row=row, column=0, sticky="w", padx=20, pady=8)
        tk.Label(frame, textvariable=self.pv_status_pv, font=("Segoe UI", 16, "bold"), fg="#0ea5e9", bg=COLOR_ROOT).grid(row=row, column=1, sticky="w")
        row += 1
        tk.Label(frame, text="Batterie", font=("Segoe UI", 14), fg="#10b981", bg=COLOR_ROOT).grid(row=row, column=0, sticky="w", padx=20, pady=4)
        tk.Label(frame, textvariable=self.pv_status_batt, font=("Segoe UI", 14), fg="#10b981", bg=COLOR_ROOT).grid(row=row, column=1, sticky="w")
        row += 1
        tk.Label(frame, text="Netzbezug", font=("Segoe UI", 14), fg="#ef4444", bg=COLOR_ROOT).grid(row=row, column=0, sticky="w", padx=20, pady=4)
        tk.Label(frame, textvariable=self.pv_status_grid, font=("Segoe UI", 14), fg="#ef4444", bg=COLOR_ROOT).grid(row=row, column=1, sticky="w")
        row += 1
        tk.Label(frame, text="Empfehlung", font=("Segoe UI", 16, "bold"), fg="#f87171", bg=COLOR_ROOT).grid(row=row, column=0, sticky="w", padx=20, pady=16)
        tk.Label(frame, textvariable=self.pv_status_recommend, font=("Segoe UI", 16, "bold"), fg="#f87171", bg=COLOR_ROOT).grid(row=row, column=1, sticky="w")
        row += 1
        tk.Label(frame, text="Letztes Update", font=("Segoe UI", 12), fg="#a3a3a3", bg=COLOR_ROOT).grid(row=row, column=0, sticky="w", padx=20, pady=8)
        tk.Label(frame, textvariable=self.pv_status_time, font=("Segoe UI", 12), fg="#a3a3a3", bg=COLOR_ROOT).grid(row=row, column=1, sticky="w")
        self._update_pv_status_tab()

    def _update_pv_status_tab(self):
        fronius_csv = _data_path("FroniusDaten.csv")
        if os.path.exists(fronius_csv):
            try:
                last_line = _read_last_data_line(fronius_csv)
                if last_line:
                    row = next(csv.reader([last_line]))
                    self.pv_status_pv.set(f"{row[1]} kW" if len(row) > 1 else "-- kW")
                    self.pv_status_batt.set(f"{row[5]} %" if len(row) > 5 else "-- %")
                    self.pv_status_grid.set(f"{row[2]} kW" if len(row) > 2 else "-- kW")
                    try:
                        pv = float(row[1]) if len(row) > 1 else None
                        batt = float(row[5]) if len(row) > 5 else None
                        grid = float(row[2]) if len(row) > 2 else None
                        if pv is not None and pv < 0.2:
                            rec = "Wenig PV – Netzbezug möglich."
                        elif batt is not None and batt < 20:
                            rec = "Batterie fast leer."
                        elif grid is not None and grid > 0.5:
                            rec = "Hoher Netzbezug."
                        else:
                            rec = "Alles ok."
                    except:
                        rec = "--"
                    self.pv_status_recommend.set(rec)
                    self.pv_status_time.set(str(row[0]) if len(row) > 0 else "--")
            except Exception:
                pass
        self.root.after(30000, self._update_pv_status_tab)  # Pi 5: Update every 30s

        # State
        self._tick = 0
        self._last_data = {
            "pv": 0,
            "load": 0,
            "grid": 0,
            "batt": 0,
            "soc": 0,
            "out_temp": 0,
            "puffer_top": 0,
            "puffer_mid": 0,
            "puffer_bot": 0,
        }
        self._last_fresh_update = 0
        self._loop()

    def _add_other_tabs(self):
        """Integriert den SpotifyTab (modern, mit OAuth) sowie Tado, Hue, System und Calendar Tabs."""
        if SpotifyTab:
            try:
                print("[SPOTIFY] SpotifyTab wird als Tab hinzugefügt!")
                # SpotifyTab fügt sich selbst dem Notebook hinzu
                self.spotify_tab = SpotifyTab(self.root, self.notebook)
                print("[SPOTIFY] ✓ Tab erfolgreich hinzugefügt")
            except Exception as e:
                print(f"[ERROR] SpotifyTab initialization failed: {e}")
                import traceback
                traceback.print_exc()
                self.spotify_tab = None
        
        if TadoTab:
            try:
                self.tado_tab = TadoTab(self.root, self.notebook)
                if self._debug_log:
                    print(f"[TADO] Tab added successfully")
            except Exception as e:
                print(f"[ERROR] TadoTab initialization failed: {e}")
                self.tado_tab = None
                if self._debug_log:
                    print(f"[TADO] Tab not available (init failed)")
        else:
            if self._debug_log:
                print(f"[TADO] Tab not available (import failed)")
        if HueTab:
            try:
                self.hue_tab = HueTab(self.root, self.notebook)
                if self._debug_log:
                    print(f"[HUE] Tab added successfully")
            except Exception as e:
                print(f"[ERROR] HueTab initialization failed: {e}")
                self.hue_tab = None
        
        if SystemTab:
            try:
                self.system_tab = SystemTab(self.root, self.notebook)
            except Exception as e:
                print(f"[ERROR] SystemTab init failed: {e}")
                self.system_tab = None
        
        if CalendarTab:
            try:
                self.calendar_tab = CalendarTab(self.root, self.notebook)
            except Exception as e:
                print(f"[ERROR] CalendarTab init failed: {e}")
                self.calendar_tab = None
        
        if HistoricalTab:
            try:
                self.historical_tab = HistoricalTab(self.root, self.notebook)
            except Exception as e:
                print(f"[ERROR] HistoricalTab init failed: {e}")
                self.historical_tab = None
        
        if ErtragTab:
            try:
                self.ertrag_tab = ErtragTab(self.root, self.notebook)
            except Exception as e:
                print(f"[ERROR] ErtragTab init failed: {e}")
                self.ertrag_tab = None

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
        if self.datastore:
            try:
                self.datastore.close()
            except:
                pass
        self.root.after(100, self.root.quit)
    
    def _init_datastore_async(self):
        """Initialisiere DataStore und importiere CSVs parallel im Hintergrund."""
        def worker():
            try:
                start = time.time()
                self.datastore = DataStore()
                
                # Check if import needed
                cursor = self.datastore.conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM fronius")
                count = cursor.fetchone()[0]
                
                if count == 0:
                    print("[DB] 📊 Importiere FroniusDaten.csv parallel...")
                    fronius_path = _data_path("FroniusDaten.csv")
                    if os.path.exists(fronius_path):
                        self.datastore.import_fronius_csv(fronius_path)
                        elapsed = time.time() - start
                        print(f"[DB] ✅ Import abgeschlossen in {elapsed:.1f}s")
                    else:
                        print(f"[DB] ⚠️ {fronius_path} nicht gefunden")
                else:
                    print(f"[DB] ✅ DataStore bereit ({count} records)")
            except Exception as e:
                print(f"[DB] ❌ Initialisierung fehlgeschlagen: {e}")
                self.datastore = None
        
        # Start in background thread
        threading.Thread(target=worker, daemon=True).start()

    def _start_ertrag_validator(self):
        """Starte wöchentliche Ertrag-Validierung im Hintergrund."""
        def validate_loop():
            # Beim Start validieren
            try:
                from core.ertrag_validator import validate_and_repair_ertrag
                print("[ERTRAG] Validation beim Start...")
                validate_and_repair_ertrag()
                print("[ERTRAG] Ertrag- und Heizungs-Tabs werden aktualisiert...")
                # Update tabs after validation
                if hasattr(self, 'ertrag_tab') and self.ertrag_tab:
                    self.ertrag_tab._last_key = None
                    self.ertrag_tab._update_plot()
                if hasattr(self, 'historical_tab') and self.historical_tab:
                    self.historical_tab._last_key = None
                    self.historical_tab._update_plot()
            except Exception as e:
                print(f"[ERTRAG] Validator nicht verfügbar: {e}")
            
            # Dann jede Woche wiederholen (7 Tage = 604800 Sekunden)
            while True:
                time.sleep(7 * 24 * 3600)  # 1 Woche
                try:
                    from core.ertrag_validator import validate_and_repair_ertrag
                    print("[ERTRAG] Wöchentliche Validierung...")
                    validate_and_repair_ertrag()
                    # Update tabs after validation
                    if hasattr(self, 'ertrag_tab') and self.ertrag_tab:
                        self.ertrag_tab._last_key = None
                        self.ertrag_tab._update_plot()
                    if hasattr(self, 'historical_tab') and self.historical_tab:
                        self.historical_tab._last_key = None
                        self.historical_tab._update_plot()
                except Exception as e:
                    print(f"[ERTRAG] Fehler bei wöchentlicher Validierung: {e}")
        
        validator_thread = threading.Thread(target=validate_loop, daemon=True)
        validator_thread.start()

    def _apply_fullscreen(self, target_w: int, target_h: int, offset_y: int):
        self.root.overrideredirect(True)
        self.root.geometry(f"{target_w}x{target_h}+0+{offset_y}")
        try:
            self.root.attributes("-fullscreen", True)
        except Exception:
            pass

    def _apply_windowed(self, w: int = 1024, h: int = 600, x: int = 50, y: int = 50):
        try:
            self.root.attributes("-fullscreen", False)
        except Exception:
            pass
        self.root.overrideredirect(False)
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def toggle_fullscreen(self):
        # Statt Fullscreen: Sicher minimieren (ohne overrideredirect)
        try:
            try:
                self.root.attributes("-fullscreen", False)
            except Exception:
                pass
            self.root.overrideredirect(False)
            self.root.update_idletasks()
            self.root.iconify()
            self.status.update_status("Minimiert")
        except Exception as e:
            print(f"[MINIMIZE] Fehler: {e}")

    def _on_root_map(self, event):
        """Restore fullscreen after window is brought back from taskbar."""
        if event.widget != self.root:
            return
        sw = max(1, self.root.winfo_screenwidth())
        sh = max(1, self.root.winfo_screenheight())
        target_w = min(sw, 1024)
        target_h = min(sh, 600)
        self.is_fullscreen = True
        # Erst overrideredirect aktivieren, dann Fullscreen
        self.root.overrideredirect(True)
        self.root.update_idletasks()
        try:
            self.root.attributes("-fullscreen", True)
        except Exception:
            pass
        self._apply_fullscreen(target_w, target_h, 0)
        self._resize_enabled = True
        self.root.after(200, lambda: self._handle_resize(self.root.winfo_width(), self.root.winfo_height()))

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

    # --- Update Loop mit echten Daten (OPTIMIZED for Pi performance) ---
    def _loop(self):
        self._tick += 1

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
            self.status.update_status(f"Updated {time_text}")
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
            # Show Kesseltemperatur as the main value in the mini diagram
            kessel = self._last_data.get("kesseltemperatur")
            # Fallback to warmwasser if kesseltemperatur is not available
            boiler = kessel if kessel is not None else self._last_data.get("warmwasser", 65.0)
            self.buffer_view.update_temperatures(
                self._last_data["puffer_top"],
                self._last_data["puffer_mid"],
                self._last_data["puffer_bot"],
                boiler,
            )

        # Data freshness every 15s
        if self._tick % 5 == 0:
            self._update_freshness_and_sparkline()

        # Pi 5: 2s main loop (balanced performance)
        self.root.after(2000, self._loop)

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
        else:
            self.status.update_data_freshness("Daten: --", alert=True)

        # Sparkline moved into right card; keep footer minimal

    def _get_last_timestamp(self) -> datetime | None:
        def _last_csv_ts(path: str) -> datetime | None:
            if not os.path.exists(path):
                return None
            lines = _read_tail_lines(path, max_bytes=131072, max_lines=800)
            if len(lines) < 2:
                return None
            for line in reversed(lines):
                line = line.strip()
                if not line or line.lower().startswith("zeit"):
                    continue
                try:
                    row = next(csv.reader([line]))
                    ts = datetime.fromisoformat(row[0])
                    return ts
                except Exception:
                    continue
            return None

        fronius = _last_csv_ts(_data_path("FroniusDaten.csv"))
        heating = _last_csv_ts(_data_path("Heizungstemperaturen.csv"))
        ts_candidates = [t for t in [fronius, heating] if t]
        if not ts_candidates:
            return None
        return max(ts_candidates)

    def _load_pv_sparkline(self, minutes: int = 60) -> list[float]:
        path = _data_path("FroniusDaten.csv")
        if not os.path.exists(path):
            return []
        cutoff = datetime.now() - timedelta(minutes=minutes)
        lines = _read_tail_lines(path, max_bytes=196608, max_lines=800)
        if len(lines) < 2:
            return []
        # Use recent lines only for speed
        recent_lines = lines[-400:]
        values = []
        for line in recent_lines:
            line = line.strip()
            if not line or line.lower().startswith("zeit"):
                continue
            try:
                row = next(csv.reader([line]))
                ts = datetime.fromisoformat(row[0])
                if ts < cutoff:
                    continue
                pv_kw = float(row[1])
                values.append(pv_kw)
            except Exception:
                continue
        return values

    def _fetch_real_data(self):
        """Versucht, echte Daten aus CSV/APIs zu laden - mit SQLite Fallback."""
        import csv

        fronius_csv = _data_path("FroniusDaten.csv")
        bmk_csv = _data_path("Heizungstemperaturen.csv")

        # Hole letzte CSV-Zeile vorab, um sie bei Bedarf (oder wenn frischer als DB) zu nutzen
        csv_row = None
        csv_ts = None
        if os.path.exists(fronius_csv):
            try:
                last_line = _read_last_data_line(fronius_csv)
                if last_line:
                    csv_row = next(csv.reader([last_line]))
                    csv_ts = datetime.fromisoformat(csv_row[0]) if csv_row else None
            except Exception:
                csv_row = None
                csv_ts = None

        # Fronius Daten - bevorzuge DataStore wenn verfügbar
        if self.datastore:
            try:
                record = self.datastore.get_last_fronius_record()
                if record:
                    record_ts = None
                    try:
                        record_ts = datetime.fromisoformat(record['timestamp'])
                    except Exception:
                        record_ts = None

                    # Wenn CSV frischer ist als DB-Record, nimm CSV statt stale DB-Wert
                    if csv_ts and record_ts and record_ts < csv_ts:
                        record = None  # erzwinge CSV unten
                    else:
                        pv_kw = record['pv']
                        grid_kw = record['grid']
                        batt_kw = record['batt']
                        soc = record['soc']

                        # Calculate load from balance
                        load_kw = pv_kw + batt_kw - grid_kw

                        self._last_data["pv"] = pv_kw * 1000  # kW -> W
                        self._last_data["grid"] = grid_kw * 1000
                        self._last_data["batt"] = -batt_kw * 1000
                        self._last_data["load"] = load_kw * 1000
                        self._last_data["soc"] = soc

                        # Skip CSV fallback if DataStore worked
                        bmk_csv_exists = True
            except Exception as e:
                logging.debug(f"DataStore Fronius Fehler: {e} - Fallback zu CSV")
                self.datastore = None  # Deactivate on error
        
        # CSV Fallback (nur wenn DataStore nicht funktioniert)
        if (not self.datastore or csv_row) and os.path.exists(fronius_csv):
            try:
                row = csv_row
                if not row:
                    last_line = _read_last_data_line(fronius_csv)
                    row = next(csv.reader([last_line])) if last_line else None
                if row and len(row) >= 6:
                    pv_kw = float(row[1])
                    grid_kw = float(row[2])
                    batt_kw = float(row[3])
                    load_kw = float(row[4])

                    # Derive grid sign from power balance
                    netz_calc_kw = pv_kw + batt_kw - load_kw
                    if abs(grid_kw) > 1e-4:
                        grid_kw = abs(grid_kw) * (1 if netz_calc_kw <= 0 else -1)
                    else:
                        grid_kw = netz_calc_kw

                    self._last_data["pv"] = pv_kw * 1000  # kW -> W
                    self._last_data["grid"] = grid_kw * 1000
                    self._last_data["batt"] = -batt_kw * 1000  # Invert: negativ = laden, positiv = entladen
                    self._last_data["load"] = load_kw * 1000
                    self._last_data["soc"] = float(row[5])
            except Exception as e:
                logging.debug(f"Fronius CSV Fehler: {e}")

        # BMK Daten (letzter Eintrag)
        if os.path.exists(bmk_csv):
            try:
                row_dict = _read_last_row_dict(bmk_csv)
                last_line = _read_last_data_line(bmk_csv)
                row = next(csv.reader([last_line])) if last_line else []
                parsed = _get_last_valid_bmk_values(bmk_csv) or _parse_bmk_row(row) or _parse_short_bmk_row(row)

                out_val = _safe_float(parsed.get("Außentemperatur")) if parsed else None
                top_val = _safe_float(parsed.get("Pufferspeicher Oben")) if parsed else None
                mid_val = _safe_float(parsed.get("Pufferspeicher Mitte")) if parsed else None
                bot_val = _safe_float(parsed.get("Pufferspeicher Unten")) if parsed else None
                warm_val = _safe_float(parsed.get("Warmwasser")) if parsed else None

                if out_val is None and row_dict:
                    out_val = _safe_float(_get_row_value(row_dict, "Außentemperatur", "Aussentemperatur", "OutdoorTemp", "OutTemp"))
                if top_val is None and row_dict:
                    top_val = _safe_float(_get_row_value(row_dict, "Pufferspeicher Oben", "Puffer_Oben", "Puffer Oben"))
                if mid_val is None and row_dict:
                    mid_val = _safe_float(_get_row_value(row_dict, "Pufferspeicher Mitte", "Puffer_Mitte", "Puffer Mitte"))
                if bot_val is None and row_dict:
                    bot_val = _safe_float(_get_row_value(row_dict, "Pufferspeicher Unten", "Puffer_Unten", "Puffer Unten"))
                if warm_val is None and row_dict:
                    warm_val = _safe_float(_get_row_value(row_dict, "Warmwasser", "Warmwassertemperatur"))

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
            except Exception as e:
                logging.debug(f"BMK CSV Fehler: {e}")


def run():
    root = tk.Tk()
    app = MainApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()

