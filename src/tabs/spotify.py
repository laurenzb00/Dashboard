import logging
import threading
import os
import tkinter as tk
import ttkbootstrap as ttk
from ttkbootstrap.constants import *

class SpotifyTab:
    """Spotify Integration Tab - Minimalist Working Version"""
    
    def __init__(self, root, notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True
        
        self.tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_frame, text="Spotify")

        self.status_var = tk.StringVar(value="Spotify Integration bereit")
        status_label = ttk.Label(
            self.tab_frame,
            textvariable=self.status_var,
            font=("Arial", 12)
        )
        status_label.pack(pady=20)

        login_button = ttk.Button(
            self.tab_frame,
            text="Browser-Login öffnen",
            command=self._open_browser_login,
            bootstyle="success-outline"
        )
        login_button.pack(pady=5)

        # Start OAuth initialization in background
        threading.Thread(target=self._init, daemon=True).start()
    
    def _init(self):
        """Initialize Spotify OAuth in background"""
        try:
            spotifylogin = self._import_spotifylogin()
            if spotifylogin:
                spotifylogin.start_oauth()
            logging.info("[SPOTIFY] OAuth init completed")
            self.status_var.set("Spotify OAuth aktiv")
        except Exception as e:
            logging.error(f"[SPOTIFY] OAuth init failed: {e}")
            self.status_var.set("Spotify OAuth Fehler")

    def _open_browser_login(self):
        """Force opening the Spotify login in the browser on demand."""
        self.status_var.set("Öffne Browser-Login…")

        def _worker():
            try:
                spotifylogin = self._import_spotifylogin()
                if spotifylogin:
                    spotifylogin.start_oauth()
                    self.status_var.set("Browser-Login gestartet")
                else:
                    self.status_var.set("Spotify Modul fehlt")
            except Exception as exc:
                logging.error(f"[SPOTIFY] Browser-Login Fehler: {exc}")
                self.status_var.set("Login fehlgeschlagen")

        threading.Thread(target=_worker, daemon=True).start()

    def _import_spotifylogin(self):
        try:
            import sys
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            import spotifylogin
            return spotifylogin
        except Exception as exc:
            logging.error(f"[SPOTIFY] spotifylogin Importfehler: {exc}")
            return None
    
    def stop(self):
        """Stop the Spotify tab"""
        self.alive = False
