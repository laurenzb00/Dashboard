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
                # apt is available, check if emoji font is installed
                result = subprocess.run(
                    ["dpkg", "-l", "|", "grep", "fonts-noto-color-emoji"],
                    shell=True, capture_output=True, text=True, timeout=5
                )
                if result.returncode != 0:
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

# Set root logger and all libraries to WARNING (only show warnings/errors)
logging.basicConfig(
    filename="datenerfassung.log",
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
console = logging.StreamHandler()
console.setLevel(logging.WARNING)
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(console)

# Set all noisy libraries to WARNING
for noisy in [
    "matplotlib", "phue", "spotipy", "urllib3", "requests", "PyTado", "PyTado.zone", "PyTado.device",
    "BMKDATEN", "Wechselrichter", "PIL.PngImagePlugin"
]:
    logging.getLogger(noisy).setLevel(logging.WARNING)

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
                data = Wechselrichter.abrufen_und_speichern()
                if data:
                    data_queue.put(('wechselrichter', data))
            except Exception as e:
                logging.error(f"Wechselrichter-Thread Fehler: {e}")
            time.sleep(10)
    except Exception as e:
        logging.error(f"Wechselrichter-Thread Fehler: {e}")

def run_bmkdaten():
    try:
        while not shutdown_event.is_set():
            try:
                data = BMKDATEN.abrufen_und_speichern()
                if data:
                    data_queue.put(('bmkdaten', data))
            except Exception as e:
                logging.error(f"BMKDATEN-Thread Fehler: {e}")
            time.sleep(10)
    except Exception as e:
        logging.error(f"BMKDATEN-Thread Fehler: {e}")


def main():
    start_time = time.time()

    root = tk.Tk()
    # Fullscreen state tracking
    root._fullscreen = True


    datastore = DataStore()
    set_shared_datastore(datastore)
    try:
        datastore.seed_from_csv()
    except Exception as exc:
        logging.warning("[DB] Initial import skipped: %s", exc)

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

    # Set fullscreen after UI is built
    root.after(200, lambda: set_fullscreen(True))

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
    print(f"[STARTUP] ✅ Dashboard bereit in {elapsed:.1f}s")

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
        root.after(500, poll_queue)


    poll_queue()
    root.mainloop()

            # Debug prints and placeholder code removed for production cleanup

def run_with_restart():
    """Run main() and restart on crash unless exit requested."""
    exit_requested = False
    while not exit_requested:
        try:
            main()
            exit_requested = True  # Normal exit (exit button)
        except SystemExit:
            exit_requested = True  # Explicit exit (exit button, pkill, etc.)
        except Exception as e:
            print(f"[RESTART] Crash detected: {e}. Restarting in 3 seconds...")
            import time
            time.sleep(3)
            # Optionally log crash details here
        except:
            print("[RESTART] Fatal error. Restarting in 3 seconds...")
            import time
            time.sleep(3)

if __name__ == "__main__":
    run_with_restart()