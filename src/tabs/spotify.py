import logging
import threading
import os
import tkinter as tk
import webbrowser
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

        link_frame = ttk.Frame(self.tab_frame)
        link_frame.pack(fill=tk.X, padx=20, pady=10)
        ttk.Label(link_frame, text="Login-Link", font=("Arial", 10, "bold")).pack(anchor=tk.W)
        self.link_var = tk.StringVar(value="Noch kein Login-Link erzeugt")
        self.link_entry = ttk.Entry(
            link_frame,
            textvariable=self.link_var,
            state="readonly",
            width=64
        )
        self.link_entry.pack(fill=tk.X, expand=True, pady=4)
        link_buttons = ttk.Frame(link_frame)
        link_buttons.pack(anchor=tk.W)
        ttk.Button(
            link_buttons,
            text="Link kopieren",
            command=self._copy_login_url,
            bootstyle="secondary-outline"
        ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(
            link_buttons,
            text="Link im Browser öffnen",
            command=self._open_latest_in_browser,
            bootstyle="info-outline"
        ).pack(side=tk.LEFT)

        self.client = None
        self._latest_login_url: str | None = None
        self._ensure_cached_session()

        # Optional Auto-Login per Umgebungsvariable (Standard = aus)
        if os.getenv("SPOTIFY_AUTO_LOGIN", "0") == "1" and self.client is None:
            self._set_status("Spotify Auto-Login gestartet…")
            self._start_login_flow(auto_open=False)
    
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
        """Generate a login URL without auto-launching the browser."""
        self._set_status("Erzeuge Spotify Login-Link…")
        self._start_login_flow(auto_open=False)

    def _start_login_flow(self, auto_open: bool | None = False):
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
            login_url = result.get("url")
            if login_url:
                self._update_login_url(login_url)
                logging.info("[SPOTIFY] Login-Link: %s", login_url)
                print(f"[SPOTIFY] Login-Link: {login_url}")
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
                self._set_status("Link wurde im Terminal ausgegeben – bitte im Browser öffnen")
            ok, err = spotifylogin.wait_for_login_result()
            if ok:
                self._set_status("Spotify Login abgeschlossen – Token gespeichert")
                self._ensure_cached_session()
            else:
                self._set_status(f"Login fehlgeschlagen: {err}")

        threading.Thread(target=_worker, daemon=True).start()

    def _set_status(self, message: str) -> None:
        self.root.after(0, self.status_var.set, message)

    def _update_login_url(self, url: str) -> None:
        self._latest_login_url = url
        self.root.after(0, self.link_var.set, url)

    def _copy_login_url(self) -> None:
        if not self._latest_login_url:
            self._set_status("Kein Login-Link verfügbar")
            return
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._latest_login_url)
            self._set_status("Login-Link in Zwischenablage")
        except Exception as exc:
            logging.error(f"[SPOTIFY] Clipboard Fehler: {exc}")
            self._set_status("Zwischenablage nicht verfügbar")

    def _open_latest_in_browser(self) -> None:
        if not self._latest_login_url:
            self._set_status("Kein Login-Link verfügbar")
            return
        try:
            if not webbrowser.open(self._latest_login_url, new=1):
                self._set_status("Browser konnte nicht geöffnet werden")
            else:
                self._set_status("Login-Link im Browser geöffnet")
        except Exception as exc:
            logging.error(f"[SPOTIFY] Browser-Start fehlgeschlagen: {exc}")
            self._set_status("Browser-Start fehlgeschlagen")

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
