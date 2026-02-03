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

        self.client = None
        self._ensure_cached_session()

        # Optional Auto-Login per Umgebungsvariable (Standard = aus)
        if os.getenv("SPOTIFY_AUTO_LOGIN", "0") == "1" and self.client is None:
            self._set_status("Spotify Auto-Login gestartet…")
            self._start_login_flow(auto_open=True)
    
    def _ensure_cached_session(self):
        spotifylogin = self._import_spotifylogin()
        if not spotifylogin:
            self._set_status("Spotify Modul fehlt")
            return
        try:
            client = spotifylogin.start_oauth()
        except Exception as exc:
            logging.error(f"[SPOTIFY] Start OAuth failed: {exc}")
            client = None
        if client:
            self.client = client
            self._set_status("Spotify Token gefunden – verbunden")
        else:
            self._set_status("Spotify Login erforderlich")

    def _open_browser_login(self):
        """Force opening the Spotify login in the browser on demand."""
        self._set_status("Öffne Browser-Login…")
        self._start_login_flow(auto_open=True)

    def _start_login_flow(self, auto_open: bool | None = None):
        def _worker():
            spotifylogin = self._import_spotifylogin()
            if not spotifylogin:
                self._set_status("Spotify Modul fehlt")
                return
            try:
                result = spotifylogin.begin_login_flow(auto_open=auto_open)
            except Exception as exc:
                logging.error(f"[SPOTIFY] Login-Flow Fehler: {exc}")
                self._set_status("Login-Flow konnte nicht gestartet werden")
                return
            if not result.get("ok"):
                self._set_status(f"Spotify Fehler: {result.get('error')}")
                return
            hint = None
            try:
                hint = spotifylogin.get_login_hint_path()
            except Exception:
                hint = None
            if hint:
                self._set_status(f"Login-Link gespeichert in {hint}")
            else:
                self._set_status("Browser geöffnet – bitte Login abschließen")
            ok, err = spotifylogin.wait_for_login_result()
            if ok:
                self._set_status("Spotify Login abgeschlossen – Token gespeichert")
                self._ensure_cached_session()
            else:
                self._set_status(f"Login fehlgeschlagen: {err}")

        threading.Thread(target=_worker, daemon=True).start()

    def _set_status(self, message: str) -> None:
        self.root.after(0, self.status_var.set, message)

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
