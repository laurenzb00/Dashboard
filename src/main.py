import threading
import queue
import logging
import time
import tkinter as tk
import subprocess
import sys
import platform
import os

# Füge src-Verzeichnis zu Python-Pfad hinzu
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))



from core import BMKDATEN
from core import Wechselrichter
from ui.app import MainApp

# Force Spotify redirect URI to loopback IP (avoid localhost which Spotify nicht erlaubt)
os.environ["SPOTIPY_REDIRECT_URI"] = "http://127.0.0.1:8889/callback"

# --- Clear Matplotlib Font Cache ---
def clear_matplotlib_cache():
    """Clear matplotlib fontlist cache to prevent corruption"""
    try:
        import matplotlib
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


# Thread-safe queue for data updates
data_queue = queue.Queue()

def run_wechselrichter():
    try:
        while not shutdown_event.is_set():
            # Instead of running Wechselrichter.run() directly, get the data and put it in the queue
            try:
                Wechselrichter.abrufen_und_speichern()
                # If you want to pass data to the GUI, you could put it in the queue here
                # data = ...
                # data_queue.put(('wechselrichter', data))
            except Exception as e:
                logging.error(f"Wechselrichter-Thread Fehler: {e}")
            time.sleep(10)
    except Exception as e:
        logging.error(f"Wechselrichter-Thread Fehler: {e}")

def run_bmkdaten():
    try:
        while not shutdown_event.is_set():
            try:
                BMKDATEN.abrufen_und_speichern()
                # If you want to pass data to the GUI, you could put it in the queue here
                # data = ...
                # data_queue.put(('bmkdaten', data))
            except Exception as e:
                logging.error(f"BMKDATEN-Thread Fehler: {e}")
            time.sleep(10)
    except Exception as e:
        logging.error(f"BMKDATEN-Thread Fehler: {e}")


def main():
        # Starte Spotify-Login nach GUI-Start, damit Terminal nicht blockiert
        try:
            import spotifylogin
            threading.Thread(target=spotifylogin.start_oauth, daemon=True).start()
        except ImportError:
            print("[MAIN] Could not import spotifylogin")
        except Exception as e:
            print(f"[MAIN] Error initializing Spotify: {e}")
    print("[STARTUP] 🚀 Starte Smart Home Dashboard...")
    start_time = time.time()
    
    # Starte Datensammler parallel
    threads = [
        threading.Thread(target=run_wechselrichter, daemon=True, name="Wechselrichter"),
        threading.Thread(target=run_bmkdaten, daemon=True, name="BMKDATEN"),
    ]
    
    print("[STARTUP] 📊 Starte Datensammler (parallel)...")
    for t in threads:
        t.start()
        print(f"[STARTUP]   ✓ {t.name} Thread gestartet")
    
    # UI starten (geschieht parallel zu Datensammlung)
    print("[STARTUP] 🎨 Initialisiere UI...")
    root = tk.Tk()
    # Auto-detect DPI-based scaling; can be overridden via UI_SCALING env.
    try:
        env_scale = os.getenv("UI_SCALING")
        if env_scale:
            scaling = float(env_scale)
        else:
            dpi = float(root.winfo_fpixels("1i"))  # pixels per inch
            scaling = dpi / 96.0  # normalize to 96 DPI baseline
            scaling = max(0.9, min(1.6, scaling))  # clamp reasonable range
        root.tk.call("tk", "scaling", scaling)
        # Export effective scaling so other modules (e.g., Spotify tab) can align sizes.
        os.environ["UI_SCALING_EFFECTIVE"] = str(scaling)
    except Exception:
        pass
    root.title("Smart Energy Dashboard Pro")
    app = MainApp(root)
    
    elapsed = time.time() - start_time
    print(f"[STARTUP] ✅ Dashboard bereit in {elapsed:.1f}s")

    def on_close():
        logging.info("Programm wird beendet…")
        shutdown_event.set()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)

    # Poll the queue for data updates and schedule GUI updates in the main thread
    def poll_queue():
        try:
            while True:
                item = data_queue.get_nowait()
                # Example: handle data from threads (extend as needed)
                # if item[0] == 'wechselrichter':
                #     app.handle_wechselrichter_data(item[1])
                # elif item[0] == 'bmkdaten':
                #     app.handle_bmkdaten_data(item[1])
        except queue.Empty:
            pass
        root.after(500, poll_queue)

    poll_queue()
    root.mainloop()

            # Debug prints and placeholder code removed for production cleanup

if __name__ == "__main__":
    main()