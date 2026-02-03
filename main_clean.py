#!/usr/bin/env python
"""
CLEANED UP MAIN - Alle Fixes implementiert
Startet das Dashboard neu mit gesäuberten Modulen
"""
import os
import sys

# Step 1: Kill old processes
print("[STARTUP] Removing old cache files...")
import shutil
for d in ['__pycache__', '.cache']:
    path = os.path.join(os.getcwd(), d)
    if os.path.exists(path):
        try:
            shutil.rmtree(path)
            print(f"  ✓ Removed {d}")
        except:
            pass

# Step 2: Clear matplotlib font cache
try:
    import matplotlib
    cache_dir = matplotlib.get_configdir()
    fontlist = os.path.join(cache_dir, "fontlist-v390.json")
    if os.path.exists(fontlist):
        os.remove(fontlist)
        print("[STARTUP] ✓ Matplotlib font cache cleared")
except:
    pass

# Step 3: Now import everything
print("[STARTUP] 🚀 Starting clean dashboard...")

import threading
import queue
import logging
import time
import tkinter as tk
import subprocess
import platform

import BMKDATEN
import Wechselrichter
from ui.app import MainApp

os.environ["SPOTIPY_REDIRECT_URI"] = "http://127.0.0.1:8889/callback"

logging.basicConfig(
    filename="datenerfassung.log",
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

shutdown_event = threading.Event()
data_queue = queue.Queue()

def run_wechselrichter():
    try:
        while not shutdown_event.is_set():
            try:
                Wechselrichter.abrufen_und_speichern()
            except Exception as e:
                logging.error(f"Wechselrichter-Thread: {e}")
            time.sleep(10)
    except Exception as e:
        logging.error(f"Wechselrichter-Thread Fatal: {e}")

def run_bmkdaten():
    try:
        while not shutdown_event.is_set():
            try:
                BMKDATEN.abrufen_und_speichern()
            except Exception as e:
                logging.error(f"BMKDATEN-Thread: {e}")
            time.sleep(10)
    except Exception as e:
        logging.error(f"BMKDATEN-Thread Fatal: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    
    # Start data threads
    w_thread = threading.Thread(target=run_wechselrichter, daemon=True)
    b_thread = threading.Thread(target=run_bmkdaten, daemon=True)
    w_thread.start()
    b_thread.start()
    
    print("[STARTUP] ✓ Data threads started")
    
    # Start UI
    app = MainApp(root)
    
    def on_closing():
        print("[SHUTDOWN] Closing app...")
        shutdown_event.set()
        app.stop()
        root.destroy()
    
    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
