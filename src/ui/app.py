import tkinter as tk
import os
import platform
import shutil
import subprocess
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



class MainApp:
    """1024x600 Dashboard mit Grid-Layout, Cards, Header und Statusbar + Tabs."""

    def __init__(self, root: tk.Tk, datastore: DataStore | None = None):
        self._start_time = time.time()
        self._debug_log = os.getenv("DASH_DEBUG", "0") == "1"
        self._configure_debounce_id = None
        self._last_size = (0, 0)
        self._resize_enabled = False
        self.root = root
        self.root.title("Smart Home Dashboard")
        
        # Shared DataStore wird beim Start bereitgestellt
        self.datastore = datastore or safe_get_datastore()
        
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
        self._apply_fullscreen()
        self.root.resizable(False, False)
        try:
            self.root.attributes("-fullscreen", True)
            self.root.attributes("-zoomed", True)
        except Exception:
            pass
        init_style(self.root)
        self._ensure_emoji_font()
        self._status_icon_ok, self._status_icon_warn = self._resolve_status_icons()
        
        if self._debug_log:
            print(f"[DEBUG] Screen: {sw}x{sh}, Target: {target_w}x{target_h}")

        # Grid Setup: Minimize fixed row sizes to maximize content area
        self._base_header_h = 52
        self._base_tabs_h = 30
        self._base_status_h = 34
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
        record = self.datastore.get_last_fronius_record() if self.datastore else None
        if record:
            pv = record.get('pv') or 0.0
            batt = record.get('soc') or 0.0
            grid = record.get('grid') or 0.0
            timestamp = record.get('timestamp') or "--"
            self.pv_status_pv.set(f"{pv:.2f} kW")
            self.pv_status_batt.set(f"{batt:.0f} %")
            self.pv_status_grid.set(f"{grid:.2f} kW")
            if pv < 0.2:
                rec = "Wenig PV – Netzbezug möglich."
            elif batt < 20:
                rec = "Batterie fast leer."
            elif grid > 0.5:
                rec = "Hoher Netzbezug."
            else:
                rec = "Alles ok."
            self.pv_status_recommend.set(rec)
            self.pv_status_time.set(timestamp)
        else:
            self.pv_status_pv.set("-- kW")
            self.pv_status_batt.set("-- %")
            self.pv_status_grid.set("-- kW")
            self.pv_status_recommend.set("--")
            self.pv_status_time.set("--")
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
        self._data_fresh_seconds = None
        self._source_health = {
            "pv": {"label": "PV", "ts": None, "count": 0},
            "heating": {"label": "Heizung", "ts": None, "count": 0},
        }
        self._last_status_compact = ""
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
    
    def _start_ertrag_validator(self):
        """Starte wöchentliche Ertrag-Validierung im Hintergrund."""
        def validate_loop():
            # Beim Start validieren
            try:
                from core.ertrag_validator import validate_and_repair_ertrag
                print("[ERTRAG] Validation beim Start...")
                validate_and_repair_ertrag(self.datastore)
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
                    validate_and_repair_ertrag(self.datastore)
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

    def _apply_fullscreen(self):
        """Setzt echtes Vollbild (ohne overrideredirect) und zentriert das Fenster."""
        try:
            self.root.attributes("-fullscreen", True)
            self.is_fullscreen = True
            # Optional: Fensterposition auf (0,0) setzen, falls nötig
            self.root.geometry("1024x600+0+0")
        except Exception:
            pass

    def _apply_windowed(self):
        """Setzt Fenstermodus maximal robust: entfernt alle Vollbild-Flags, setzt sichere Größe, bringt Fenster in den Vordergrund."""
        try:
            # Deaktiviere alle Vollbild- und override-Attribute
            self.root.attributes("-fullscreen", False)
            self.root.overrideredirect(False)
            self.is_fullscreen = False
            # Setze eine sichere, sichtbare Größe und Position
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            w, h = 1024, 600
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 2)
            self.root.geometry(f"{w}x{h}+{x}+{y}")
            self.root.update_idletasks()
            # Bringe das Fenster in den Vordergrund und erzwinge Sichtbarkeit
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
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

        if pv_kw is not None or grid_kw is not None or load_kw is not None:
            netz_calc = (pv_kw or 0.0) + (batt_kw or 0.0) - (load_kw or 0.0)
            if grid_kw is None or abs(grid_kw) < 1e-4:
                signed_grid_kw = netz_calc
            else:
                signed_grid_kw = abs(grid_kw) * (1 if netz_calc <= 0 else -1)
            self._last_data["grid"] = signed_grid_kw * 1000

        if soc is not None:
            self._last_data["soc"] = soc

        self._update_status_summary()

    def handle_bmkdaten_data(self, data: dict):
        """Heizungs-/Pufferdaten aus dem Worker-Thread übernehmen."""
        ts = self._parse_timestamp_value(data.get("Zeitstempel")) or datetime.now()
        source = self._source_health.get("heating")
        if source:
            source["ts"] = ts
            source["count"] += 1

        out_temp = _safe_float(data.get("Außentemperatur") or data.get("Aussentemperatur"))
        top = _safe_float(data.get("Pufferspeicher Oben") or data.get("Puffer_Oben"))
        mid = _safe_float(data.get("Pufferspeicher Mitte") or data.get("Puffer_Mitte"))
        bot = _safe_float(data.get("Pufferspeicher Unten") or data.get("Puffer_Unten"))
        warm = _safe_float(data.get("Warmwasser") or data.get("Warmwassertemperatur"))
        kessel = _safe_float(data.get("Kesseltemperatur"))

        if out_temp is not None:
            self._last_data["out_temp"] = out_temp
        if top is not None:
            self._last_data["puffer_top"] = top
        if mid is not None:
            self._last_data["puffer_mid"] = mid
        if bot is not None:
            self._last_data["puffer_bot"] = bot
        if warm is not None:
            self._last_data["warmwasser"] = warm
        if kessel is not None:
            self._last_data["kesseltemperatur"] = kessel

        self._update_status_summary()

    @staticmethod
    def _parse_timestamp_value(value):
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

    def _fetch_real_data(self):
        """Versucht, echte Daten direkt aus dem DataStore zu laden."""
        if not self.datastore:
            return

        try:
            record = self.datastore.get_last_fronius_record()
            if record:
                pv_kw = float(record.get('pv') or 0.0)
                grid_kw = float(record.get('grid') or 0.0)
                batt_kw = float(record.get('batt') or 0.0)
                soc = float(record.get('soc') or 0.0)
                load_kw = pv_kw + batt_kw - grid_kw

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
    root = tk.Tk()
    app = MainApp(root)
    root.mainloop()


if __name__ == "__main__":
    run()

