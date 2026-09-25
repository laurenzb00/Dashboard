import threading
import queue
import logging
import time
import tkinter as tk
import subprocess
import sys
import platform
import os
import socket
import faulthandler
import signal
import traceback
import atexit
import tracemalloc
from pathlib import Path
from core.datastore import DataStore, set_shared_datastore, close_shared_datastore
from core.health import update_source_health
import importlib

# Füge src-Verzeichnis zu Python-Pfad hinzu
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))



from core import BMKDATEN
from core import Wechselrichter
from ui.app import MainApp

# Force Spotify redirect URI but allow override via env



# --- Clear Matplotlib Font Cache ---
def clear_matplotlib_cache() -> None:
    """Clear matplotlib fontlist cache to prevent corruption"""
    try:
        spec = importlib.util.find_spec("matplotlib")
        if spec is None:
            return
        matplotlib = importlib.import_module("matplotlib")
        cache_dir = matplotlib.get_configdir()
        fontlist_file = os.path.join(cache_dir, "fontlist-v390.json")
        if os.path.exists(fontlist_file):
            try:
                os.remove(fontlist_file)
                print("[MATPLOTLIB] Cleared fontlist cache")
            except Exception as e:
                print(f"[MATPLOTLIB] Could not clear cache: {e}")
    except Exception:
        pass

clear_matplotlib_cache()

# --- Ensure Emoji Font is installed ---
def ensure_emoji_font():
    """Install emoji font if not available (for Raspberry Pi compatibility)."""
    system = platform.system()
    try:
        if system == "Linux":
            # Try to install fonts-noto-color-emoji on Linux/Raspberry Pi
            try:
                subprocess.run(["dpkg", "-l"], capture_output=True, check=True, timeout=5)
                # apt is available, check if emoji font is installed.
                # War vorher subprocess.run(["dpkg","-l","|","grep",...], shell=True):
                # bei shell=True + Listen-Argument wird nur das ERSTE Element als
                # Shell-Befehl verwendet, der Rest landet ungenutzt als $0,$1,... -
                # die Pipe zu grep lief also nie, der Check pruefte de facto nichts.
                # dpkg-query -W -f='${Status}' braucht keine Pipe/kein shell=True
                # und ist der fuer genau diesen Zweck vorgesehene dpkg-Befehl.
                result = subprocess.run(
                    ["dpkg-query", "-W", "-f=${Status}", "fonts-noto-color-emoji"],
                    capture_output=True, text=True, timeout=5,
                )
                already_installed = result.returncode == 0 and "installed" in result.stdout
                if not already_installed:
                    print("[EMOJI] Installing fonts-noto-color-emoji...")
                    subprocess.run(
                        ["sudo", "apt-get", "install", "-y", "fonts-noto-color-emoji"],
                        timeout=60, capture_output=True
                    )
            except Exception:
                pass
    except Exception as e:
        print(f"[EMOJI] Could not ensure emoji font: {e}")

ensure_emoji_font()

# --- Logging ---

_TEST_MODE = os.getenv("DASHBOARD_TEST_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
_TEST_LOG_PATH = None
if _TEST_MODE:
    raw_path = os.getenv("DASHBOARD_TEST_LOG")
    if raw_path:
        _TEST_LOG_PATH = Path(raw_path).expanduser()
    else:
        _TEST_LOG_PATH = Path(__file__).resolve().with_name("test_run.log")
    try:
        _TEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        _test_log_file = _TEST_LOG_PATH.open("a", encoding="utf-8", buffering=1)
        sys.stdout = _test_log_file
        sys.stderr = _test_log_file
    except Exception:
        _TEST_LOG_PATH = None

_log_file = str(_TEST_LOG_PATH) if _TEST_LOG_PATH else "datenerfassung.log"

# Set root logger and all libraries to WARNING (only show warnings/errors)
logging.basicConfig(
    filename=_log_file,
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
console = logging.StreamHandler(sys.stdout)
console.setLevel(logging.WARNING)
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(console)

# Set all noisy libraries to WARNING
for noisy in [
    "matplotlib", "phue", "spotipy", "urllib3", "requests", "PyTado", "PyTado.zone", "PyTado.device",
    "BMKDATEN", "Wechselrichter", "PIL.PngImagePlugin"
]:
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# --- Zusaetzliches Debug-File-Logging in data/app_debug.log ---
# WICHTIG: src/ui/app.py hat ein eigenes run()/if __name__=="__main__" mit
# eigenem File-Logging-Setup - das wird aber NIE ausgefuehrt, weil start.sh
# / start.bat tatsaechlich "python src/main.py" starten, welches MainApp
# direkt instanziert und NIE app.run() aufruft. Ein dort eingebauter
# FileHandler haette also nie funktioniert (daher fehlte data/app_debug.log
# trotz Neustart). Hier, im tatsaechlich ausgefuehrten Einstiegspunkt, ist
# es die richtige Stelle dafuer. Ziel-Ordner "data/" wurde bereits in
# diesem Projekt erfolgreich fuer Debug-Dateien genutzt (layout_debug.txt),
# die zuverlaessig zwischen diesem Rechner und dem Windows-Ordner
# synchronisiert werden - im Gegensatz zu Dateien im Code-Verzeichnis
# selbst, die nur per manuellem Pull aktualisiert werden.
#
# Root-Logger-Level wird auf INFO abgesenkt, damit z.B. tabs/tado.py's
# logging.info(...)-Aufrufe (Button-Klicks, Geraete-URL, Browser-Oeffnen-
# Versuche) ueberhaupt erst durchkommen - vorher wurden sie schon auf
# Root-Logger-Ebene wegen WARNING gefiltert, bevor sie irgendeinen Handler
# erreichten. Die Konsole (siehe "console" oben) bleibt bewusst bei
# WARNING, damit dort nicht ploetzlich viel mehr Text erscheint.
try:
    _debug_log_path = Path(__file__).resolve().parent.parent / "data" / "app_debug.log"
    _debug_log_path.parent.mkdir(parents=True, exist_ok=True)
    _debug_file_handler = logging.FileHandler(str(_debug_log_path), mode="w", encoding="utf-8")
    _debug_file_handler.setLevel(logging.INFO)
    _debug_file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    _root_logger = logging.getLogger()
    _root_logger.addHandler(_debug_file_handler)
    if _root_logger.level > logging.INFO:
        _root_logger.setLevel(logging.INFO)
    logging.info("=== App-Start (main.py), Debug-File-Logging aktiv: %s ===", _debug_log_path)
except Exception as _exc:
    print(f"[DEBUG-LOG] Konnte data/app_debug.log nicht einrichten: {_exc}")

shutdown_event = threading.Event()
_CRASH_LOG_FILE = None
CRASH_LOG_PATH = Path(__file__).resolve().with_name("crash.log")


# Thread-safe queue for data updates
data_queue = queue.Queue()

def _install_crash_logger(log_path: Path | str | None = None) -> None:
    global _CRASH_LOG_FILE
    if _CRASH_LOG_FILE is not None:
        return
    target = Path(log_path) if log_path else CRASH_LOG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        _CRASH_LOG_FILE = target.open("a", encoding="utf-8", buffering=1)
    except OSError:
        _CRASH_LOG_FILE = None
        return

    def _handle_exception(exc_type, exc_value, exc_traceback):
        logging.critical("Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))
        if _CRASH_LOG_FILE:
            _CRASH_LOG_FILE.write("\n==== Crash at {} ====".format(time.strftime("%Y-%m-%d %H:%M:%S")))
            _CRASH_LOG_FILE.write("\n")
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=_CRASH_LOG_FILE)
            _CRASH_LOG_FILE.flush()

    log_target = _CRASH_LOG_FILE or sys.stderr
    try:
        faulthandler.enable(log_target, all_threads=True)
    except RuntimeError:
        try:
            faulthandler.disable()
            faulthandler.enable(log_target, all_threads=True)
        except Exception:
            pass
    except Exception:
        pass

    if hasattr(signal, "SIGUSR1"):
        try:
            faulthandler.register(signal.SIGUSR1, file=log_target, all_threads=True)
        except Exception:
            pass
    sys.excepthook = _handle_exception
    atexit.register(lambda: _CRASH_LOG_FILE and _CRASH_LOG_FILE.close())


_install_crash_logger()


def _start_tracemalloc_snapshotter():
    """Enable tracemalloc and dump snapshot on exit/SIGUSR2 for Pi debugging."""
    enable_flag = os.getenv("TRACEMALLOC_ENABLE", "0").strip().lower()
    if enable_flag not in {"1", "true", "yes"}:
        return
    if os.getenv("TRACEMALLOC_DISABLE", "0").strip().lower() in {"1", "true", "yes"}:
        return
    depth_env = os.getenv("TRACEMALLOC_DEPTH", "25").strip()
    try:
        depth = max(1, min(100, int(depth_env)))
    except ValueError:
        depth = 25
    snapshot_path = Path(os.getenv("TRACEMALLOC_SNAPSHOT",
                                   "tracemalloc_snapshot.bin")).resolve()
    try:
        tracemalloc.start(depth)
        print(f"[TRACEMALLOC] Enabled (depth={depth}) -> {snapshot_path}")
    except Exception as exc:
        logging.warning("Tracemalloc start failed: %s", exc)
        return

    def _dump_snapshot(reason: str) -> None:
        try:
            snapshot = tracemalloc.take_snapshot()
            snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            snapshot.dump(str(snapshot_path))
            print(f"[TRACEMALLOC] Snapshot written ({reason}) -> {snapshot_path}")
        except Exception as exc:
            logging.error("Tracemalloc snapshot (%s) failed: %s", reason, exc)

    atexit.register(lambda: _dump_snapshot("atexit"))

    if hasattr(signal, "SIGUSR2"):
        def _sigusr2_handler(signum, frame):
            _dump_snapshot("SIGUSR2")
        try:
            signal.signal(signal.SIGUSR2, _sigusr2_handler)
        except Exception:
            pass


_start_tracemalloc_snapshotter()

def run_wechselrichter():
    try:
        while not shutdown_event.is_set():
            # Statt nur abrufen_und_speichern(), Daten an die GUI übergeben
            try:
                start = time.perf_counter()
                data = Wechselrichter.abrufen_und_speichern()
                latency_ms = int((time.perf_counter() - start) * 1000)
                if data:
                    data_queue.put(('wechselrichter', data))
                    update_source_health("pv", ok=True, latency_ms=latency_ms)
                else:
                    update_source_health("pv", ok=False, error="no data")
            except Exception as e:
                logging.error(f"Wechselrichter-Thread Fehler: {e}")
                update_source_health("pv", ok=False, error=str(e))
            time.sleep(10)
    except Exception as e:
        logging.error(f"Wechselrichter-Thread Fehler: {e}")
        update_source_health("pv", ok=False, error=str(e))

def run_bmkdaten():
    logging.info("[BMKDATEN] Thread gestartet")
    try:
        while not shutdown_event.is_set():
            try:
                start = time.perf_counter()
                data = BMKDATEN.abrufen_und_speichern()
                latency_ms = int((time.perf_counter() - start) * 1000)
                if data:
                    logging.info("[BMKDATEN] Daten empfangen: %s keys, latency=%dms", len(data), latency_ms)
                    data_queue.put(('bmkdaten', data))
                    update_source_health("heating", ok=True, latency_ms=latency_ms)
                else:
                    logging.warning("[BMKDATEN] Keine Daten empfangen")
                    update_source_health("heating", ok=False, error="no data")
            except Exception as e:
                logging.error(f"BMKDATEN-Thread Fehler: {e}")
                update_source_health("heating", ok=False, error=str(e))
            time.sleep(10)
    except Exception as e:
        logging.error(f"BMKDATEN-Thread Fehler: {e}")
        update_source_health("heating", ok=False, error=str(e))


def main():
    start_time = time.time()

    root = tk.Tk()
    # Fullscreen state tracking
    windowed_flag = os.getenv("DASHBOARD_WINDOWED", "0").strip().lower() in {"1", "true", "yes", "on"}
    root._fullscreen = not windowed_flag


    datastore = DataStore()
    set_shared_datastore(datastore)
    try:
        datastore.seed_from_csv()
    except Exception as exc:
        logging.warning("[DB] Initial import skipped: %s", exc)

    try:
        # War cleanup_old_records(retention_days=365) - hat alte Rohdaten
        # nach einem Jahr endgueltig geloescht. Jetzt stattdessen: Rohdaten
        # aelter als 90 Tage zu Stundenmittelwerten verdichten (kein
        # Datenverlust mehr, nur Detailgrad reduziert). Siehe
        # DataStore.compact_old_records() fuer Details.
        datastore.compact_old_records(older_than_days=90, bucket_seconds=3600)
    except Exception as exc:
        logging.warning("[DB] Verdichtung alter Daten uebersprungen: %s", exc)

    try:
        # Online-Backup der SQLite-DB (sqlite3 backup API, WAL-sicher).
        # Intern gedrosselt (hoechstens 1x/Tag) und rotiert alte Backups -
        # siehe DataStore.backup_database().
        datastore.backup_database()
    except Exception as exc:
        logging.warning("[DB] Backup uebersprungen: %s", exc)

    env_scale = os.getenv("UI_SCALING")

    try:
        if env_scale:
            scaling = float(env_scale)
        else:
            dpi = float(root.winfo_fpixels("1i"))
            scaling = dpi / 96.0
            scaling = max(0.9, min(1.6, scaling))
        root.tk.call("tk", "scaling", scaling)
        # Export effective scaling so other modules (e.g., Spotify tab) can align sizes.
        os.environ["UI_SCALING_EFFECTIVE"] = str(round(scaling, 3))
    except Exception:
        pass


    root.title("Smart Energy Dashboard Pro")
    app = MainApp(root)

    def set_fullscreen(enable: bool):
        # In windowed mode, never enable fullscreen (keeps window discoverable on multi-monitor setups)
        if windowed_flag and enable:
            enable = False
        root._fullscreen = enable
        root.attributes("-fullscreen", enable)
        if enable:
            root.focus_force()

    def toggle_fullscreen(event=None):
        set_fullscreen(not getattr(root, '_fullscreen', False))

    def end_fullscreen(event=None):
        set_fullscreen(False)

    # Bind F11 to toggle, ESC to exit fullscreen
    root.bind('<F11>', toggle_fullscreen)
    root.bind('<Escape>', end_fullscreen)

    # Set fullscreen after UI is built (unless windowed mode is enabled)
    if not windowed_flag:
        root.after(200, lambda: set_fullscreen(True))
    else:
        def _bring_to_front() -> None:
            try:
                root.attributes("-fullscreen", False)
            except Exception:
                pass
            try:
                root.state("normal")
            except Exception:
                pass
            try:
                root.geometry("1024x600+50+50")
            except Exception:
                pass
            try:
                root.deiconify()
                root.lift()
                root.focus_force()
            except Exception:
                pass
            # Temporary topmost to guarantee visibility, then revert
            try:
                root.attributes("-topmost", True)
                root.after(600, lambda: root.attributes("-topmost", False))
            except Exception:
                pass

        root.after(200, _bring_to_front)

    def _start_collectors() -> list[threading.Thread]:
        threads: list[threading.Thread] = []
        for target, name in (
            (run_wechselrichter, "WechselrichterCollector"),
            (run_bmkdaten, "BMKDATENCollector"),
        ):
            try:
                thread = threading.Thread(target=target, name=name, daemon=True)
                thread.start()
                threads.append(thread)
            except Exception as exc:
                logging.error("%s konnte nicht gestartet werden: %s", name, exc)
        return threads

    collector_threads = _start_collectors()
    elapsed = time.time() - start_time
    logger.info("Dashboard bereit in %.1fs", elapsed)

    def on_close():
        logging.info("Programm wird beendet…")
        shutdown_event.set()
        for thread in collector_threads:
            try:
                thread.join(timeout=2.0)
            except Exception:
                pass
        try:
            close_shared_datastore()
        except Exception:
            pass
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # Poll the queue for data updates and schedule GUI updates in the main thread
    def poll_queue():
        try:
            while True:
                item = data_queue.get_nowait()
                if item[0] == 'wechselrichter':
                    app.handle_wechselrichter_data(item[1])
                elif item[0] == 'bmkdaten':
                    app.handle_bmkdaten_data(item[1])
        except queue.Empty:
            pass
        # Increased from 1500ms to 2000ms - collectors update every 10s
        root.after(2000, poll_queue)


    poll_queue()
    root.mainloop()

            # Debug prints and placeholder code removed for production cleanup

def run_with_restart():
    """Run main() and restart on crash unless exit requested.

    Retried vorher immer mit fixen 3s, egal wie oft main() direkt
    hintereinander abstuerzt (z.B. Platte voll durch wachsende Logs, oder
    ein Fehler gleich beim Start wie ein kaputter Tk/Display- oder DB-Open-
    Aufruf) - eine dauerhafte Fehlerursache drehte sich damit endlos im
    3s-Takt, statt sich zu "beruhigen". Jetzt: exponentieller Backoff
    (3s, 6s, 12s, ... bis max. 5 Minuten), der sich zuruecksetzt, sobald
    das Dashboard eine Weile (>5 Minuten) stabil lief - ein einzelner
    seltener Absturz nach Tagen im Betrieb wird also weiterhin sofort mit
    kurzem Delay neu gestartet, nur eine Crash-Schleife bremst sich selbst.
    """
    import time as _time

    exit_requested = False
    base_delay = 3.0
    max_delay = 300.0
    stable_after_s = 300.0
    consecutive_crashes = 0
    last_crash_ts = None

    while not exit_requested:
        try:
            main()
            exit_requested = True  # Normal exit (exit button)
        except SystemExit:
            exit_requested = True  # Explicit exit (exit button, pkill, etc.)
        except Exception as e:
            now = _time.time()
            if last_crash_ts is not None and (now - last_crash_ts) > stable_after_s:
                consecutive_crashes = 0
            consecutive_crashes += 1
            last_crash_ts = now
            delay = min(max_delay, base_delay * (2 ** (consecutive_crashes - 1)))
            logger.error(
                "Crash detected (#%d in a row): %s. Restarting in %.0f seconds...",
                consecutive_crashes, e, delay,
            )
            _time.sleep(delay)
        except:
            now = _time.time()
            if last_crash_ts is not None and (now - last_crash_ts) > stable_after_s:
                consecutive_crashes = 0
            consecutive_crashes += 1
            last_crash_ts = now
            delay = min(max_delay, base_delay * (2 ** (consecutive_crashes - 1)))
            logger.critical(
                "Fatal error (#%d in a row). Restarting in %.0f seconds...",
                consecutive_crashes, delay,
            )
            _time.sleep(delay)

if __name__ == "__main__":
    run_with_restart()