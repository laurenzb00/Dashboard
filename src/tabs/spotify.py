import logging
import os
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from io import BytesIO
from typing import Optional

import requests
try:
    import ttkbootstrap as ttk
    from ttkbootstrap.constants import BOTH, LEFT, RIGHT, W
except ImportError:
    import tkinter.ttk as ttk
    from tkinter.constants import BOTH, LEFT, RIGHT
    W = "w"
try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None


POLL_INTERVAL_MS = 5000
COVER_SIZE = (260, 260)
PLAYLIST_IMAGE_SIZE = (96, 96)


class SpotifyTab:
    def safe_toggle_style(style_name="round-toggle"):
        try:
            import ttkbootstrap as ttk
            s = ttk.Style()
            # Prüfe, ob Style existiert
            try:
                s.layout(style_name)
                return style_name
            except Exception:
                pass
            # Fallbacks
            for fallback in ["toggle", "roundtoggle", None]:
                try:
                    if fallback and s.layout(fallback):
                        return fallback
                except Exception:
                    continue
            return None
        except Exception:
            return None
    def _build_devices_tab(self) -> None:
        header = ttk.Frame(self.devices_frame)
        header.pack(fill=tk.X)
        ttk.Label(header, text="Geräteauswahl", font=("Arial", 14, "bold")).pack(anchor=W)
        ttk.Label(header, text="Wähle hier das Ausgabegerät für Spotify.",
                  font=("Arial", 10), foreground="#94a3b8").pack(anchor=W, pady=(2, 6))
        ttk.Button(header, text="Geräte aktualisieren", command=self._refresh_devices,
                   bootstyle="info-outline").pack(anchor=W)

        body = ttk.Frame(self.devices_frame)
        body.pack(fill=BOTH, expand=True, pady=(12, 0))
        self.device_container = ttk.Frame(body)
        self.device_container.pack(fill=BOTH, expand=True)
        ttk.Label(self.device_container, text="Keine Geräte geladen", padding=12).pack()

    def _update_login_url(self, url: str) -> None:
        """Speichert die aktuelle Login-URL und aktualisiert die UI-Variable."""
        self._latest_login_url = url
        # Optional: Schreibe die URL in eine Datei für Debugging oder externe Nutzung
        try:
            config_dir = os.path.join(os.path.dirname(__file__), '../../config')
            os.makedirs(config_dir, exist_ok=True)
            with open(os.path.join(config_dir, 'spotify_login_url.txt'), 'w', encoding='utf-8') as f:
                f.write(url)
            logging.info("[SPOTIFY] Login-URL gespeichert unter %s", os.path.join(config_dir, 'spotify_login_url.txt'))
        except Exception as exc:
            logging.error("[SPOTIFY] Login-URL konnte nicht gespeichert werden: %s", exc)
    """Touch-optimierte Spotify-Integration mit Now-Playing- und Playlist-Ansicht."""

    def _bind_tab_refresh(self):
        # Automatisches Aktualisieren der Playlists beim Tab-Wechsel
        def on_tab_changed(event):
            nb = event.widget
            # Prüfe, ob Playlists-Tab aktiv ist
            if nb.tab(nb.select(), "text") == "Playlists":
                self._refresh_playlists()
        self.content_notebook.bind("<<NotebookTabChanged>>", on_tab_changed)

    def _create_playlist_icon(self, playlist: dict, idx: int):
        # 6 Playlists pro Zeile, vertikales Scrollen, kompaktes Layout, unsichtbarer Button über Cover
        col_count = 6
        row = idx // col_count
        col_idx = idx % col_count
        cell = ttk.Frame(self.playlist_inner, padding=2)
        cell.grid(row=row, column=col_idx, padx=8, pady=8, sticky="n")
        image_url = (playlist.get("images") or [{}])[0].get("url")
        photo = self._get_playlist_photo(playlist.get("id"), image_url)
        # Unsichtbarer Button über dem Cover, reagiert auf Klick und leuchtet kurz auf
        def on_select():
            btn.configure(style="Playlist.Highlight.TButton")
            self.root.after(120, lambda: btn.configure(style="Playlist.Transparent.TButton"))
            self._play_playlist(playlist.get("uri"))

        # Transparentes Button-Style anlegen (nur einmal global)
        style = ttk.Style()
        if not style.lookup("Playlist.Transparent.TButton", "background"):
            style.configure(
                "Playlist.Transparent.TButton",
                background="",
                borderwidth=0,
                relief="flat",
                foreground="",
                focuscolor="",
                highlightthickness=0,
                highlightbackground="",
                highlightcolor="",
                lightcolor="",
                darkcolor="",
                bordercolor="",
                padding=0,
                focusthickness=0,
                focustcolor="",
                selectcolor="",
                indicatorcolor=""
            )
        if not style.lookup("Playlist.Highlight.TButton", "background"):
            style.configure(
                "Playlist.Highlight.TButton",
                background="#3b82f6",
                borderwidth=0,
                relief="flat",
                foreground="",
                focuscolor="",
                highlightthickness=0,
                highlightbackground="",
                highlightcolor="",
                lightcolor="",
                darkcolor="",
                bordercolor="",
                padding=0
            )

        # Cover selbst als Button, damit alles klickbar bleibt und nichts überlagert
        btn = ttk.Button(
            cell,
            image=photo if photo else None,
            text="" if photo else "Cover",
            command=on_select,
            style="Playlist.Transparent.TButton",
            takefocus=True
        )
        btn.pack()
        name = playlist.get("name", "Unbenannte Playlist")
        ttk.Label(cell, text=name, font=("Arial", 11, "bold"), wraplength=PLAYLIST_IMAGE_SIZE[0]+10).pack(pady=(2,0))
        tracks_total = playlist.get("tracks", {}).get("total", 0)
        ttk.Label(cell, text=f"{tracks_total} Titel", font=("Arial", 9), foreground="#9ca3af").pack()

    def __init__(self, root, notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True

        self.tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_frame, text="Spotify")

        self.status_var = tk.StringVar(value="Spotify Integration bereit")
        self.link_var = tk.StringVar(value="Noch kein Login-Link erzeugt")
        self.status_detail_var = tk.StringVar(value="Konfiguration wird geprüft…")
        self.token_info_var = tk.StringVar(value="Noch kein Token gefunden")
        # Load callback URI from config file if available
        import json
        config_path = os.path.join(os.path.dirname(__file__), '../../config/spotify.json')
        callback_uri = None
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    cfg = json.load(f)
                    callback_uri = cfg.get('redirect_uri')
            except Exception:
                callback_uri = None
        if not callback_uri:
            callback_uri = os.getenv("SPOTIPY_REDIRECT_URI") or "http://127.0.0.1:8889/callback"
        self.redirect_var = tk.StringVar(value=f"Callback-URL: {callback_uri}")

        self._build_ui()

        # Runtime state holders
        self.client = None
        self._latest_login_url = None
        self._cover_photo = None
        self._playlist_images = {}
        self._devices = []
        self._last_track_id = None
        self._last_playback = None
        self._liked_track = False
        self._poll_job = None
        self._volume_after_id = None
        self._pending_volume = None
        self._ignore_volume_event = False
        self._playlists_data = []

        self._ensure_cached_session()
        self._start_playback_poll()

    # ------------------------------------------------------------------
    # UI Aufbau
    # ------------------------------------------------------------------
    def _build_header(self) -> None:
        self.status_var = tk.StringVar(value="Spotify Integration bereit")
        ttk.Label(self.tab_frame, textvariable=self.status_var, font=("Arial", 12)).pack(pady=(12, 4))

        control_frame = ttk.Frame(self.tab_frame)
        control_frame.pack(pady=(0, 6))
        ttk.Button(control_frame, text="Browser-Login öffnen", command=self._open_browser_login,
                   bootstyle="success-outline").pack(side=LEFT, padx=4)
        ttk.Button(control_frame, text="Status aktualisieren", command=self._refresh_status,
                   bootstyle="secondary-outline").pack(side=LEFT, padx=4)
        ttk.Button(control_frame, text="Logout", command=self._logout,
                   bootstyle="danger-outline").pack(side=LEFT, padx=4)

        # Login link UI removed for local-only use
        callback_uri = os.getenv("SPOTIPY_REDIRECT_URI") or "http://127.0.0.1:8889/callback"
        self.redirect_var = tk.StringVar(value=f"Callback-URL: {callback_uri}")
        ttk.Label(self.tab_frame, textvariable=self.redirect_var, font=("Arial", 9), foreground="#94a3b8").pack(anchor=W, pady=(6, 0))

        info_frame = ttk.Frame(self.tab_frame)
        info_frame.pack(fill=tk.X, padx=20, pady=(0, 8))
        self.status_detail_var = tk.StringVar(value="Konfiguration wird geprüft…")
        self.token_info_var = tk.StringVar(value="Noch kein Token gefunden")
        ttk.Label(info_frame, textvariable=self.status_detail_var, font=("Arial", 10)).pack(anchor=W)
        ttk.Label(info_frame, textvariable=self.token_info_var, font=("Arial", 9), foreground="#94a3b8").pack(anchor=W)

    def _build_content(self) -> None:
        self.content_notebook = ttk.Notebook(self.tab_frame, bootstyle="dark")
        self.content_notebook.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))

        self.now_playing_frame = ttk.Frame(self.content_notebook, padding=12)
        self.library_frame = ttk.Frame(self.content_notebook, padding=12)
        self.recent_frame = ttk.Frame(self.content_notebook, padding=12)
        self.devices_frame = ttk.Frame(self.content_notebook, padding=12)
        self.content_notebook.add(self.now_playing_frame, text="Now Playing")
        self.content_notebook.add(self.library_frame, text="Playlists")
        self.content_notebook.add(self.recent_frame, text="Zuletzt gespielt")
        self.content_notebook.add(self.devices_frame, text="Geräte")

        self._build_now_playing_tab()
        self._build_library_tab()
        self._build_recent_tab()
        self._build_devices_tab()

    def _build_recent_tab(self) -> None:
        top = ttk.Frame(self.recent_frame)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Zuletzt gespielt", font=("Arial", 14, "bold")).pack(anchor=W)

        list_wrapper = ttk.Frame(self.recent_frame)
        list_wrapper.pack(fill=BOTH, expand=True, pady=(6, 0))
        self.recent_canvas = tk.Canvas(list_wrapper, highlightthickness=0)
        v_scrollbar = ttk.Scrollbar(list_wrapper, orient=tk.VERTICAL, command=self.recent_canvas.yview)
        self.recent_canvas.configure(yscrollcommand=v_scrollbar.set)
        self.recent_canvas.pack(side=tk.LEFT, fill=BOTH, expand=True)
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.recent_inner = ttk.Frame(self.recent_canvas)
        self.recent_canvas.create_window((0, 0), window=self.recent_inner, anchor="nw")
        self.recent_inner.bind(
            "<Configure>",
            lambda e: self.recent_canvas.configure(scrollregion=self.recent_canvas.bbox("all")),
        )
        self.recent_empty = ttk.Label(self.recent_inner, text="Noch keine Daten geladen", padding=16)
        self.recent_empty.pack()
        # Automatisch laden, wenn Tab aktiviert wird
        self.content_notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed_recent)

    def _on_tab_changed_recent(self, event):
        nb = event.widget
        if nb.tab(nb.select(), "text") == "Zuletzt gespielt":
            self._refresh_recent()

    def _refresh_recent(self):
        if not self.client:
            return
        response = self._safe_spotify_call(self.client.current_user_recently_played, limit=30)
        items = (response or {}).get("items", [])
        self._render_recent(items)

    def _render_recent(self, items):
        for child in self.recent_inner.winfo_children():
            child.destroy()
        if not items:
            ttk.Label(self.recent_inner, text="Keine zuletzt gespielten Titel gefunden", padding=16).pack()
            return
        for idx, entry in enumerate(items):
            track = entry.get("track", {})
            name = track.get("name", "Unbekannter Titel")
            artists = ", ".join(a.get("name", "") for a in track.get("artists", []))
            album = track.get("album", {}).get("name", "")
            played_at = entry.get("played_at", "")
            label = ttk.Label(self.recent_inner, text=f"{name} – {artists}\n{album}", anchor="w", justify="left", font=("Arial", 11))
            label.pack(fill=tk.X, padx=8, pady=4)

    def _build_now_playing_tab(self) -> None:
        container = ttk.Frame(self.now_playing_frame)
        container.pack(fill=BOTH, expand=True)
        container.columnconfigure(0, weight=1)
        container.columnconfigure(1, weight=1)

        left = ttk.Frame(container)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self.cover_label = ttk.Label(left, text="Kein Cover", anchor="center", width=28, padding=8)
        self.cover_label.pack(fill=tk.BOTH, expand=True)

        info_box = ttk.Frame(left)
        info_box.pack(fill=tk.X, pady=8)
        self.track_var = tk.StringVar(value="–")
        self.artist_var = tk.StringVar(value="")
        self.album_var = tk.StringVar(value="")
        ttk.Label(info_box, textvariable=self.track_var, font=("Arial", 18, "bold"), wraplength=360).pack(anchor=W)
        ttk.Label(info_box, textvariable=self.artist_var, font=("Arial", 14), foreground="#99c1ff").pack(anchor=W, pady=(2, 0))
        ttk.Label(info_box, textvariable=self.album_var, font=("Arial", 12), foreground="#9ca3af").pack(anchor=W)

        right = ttk.Frame(container)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        volume_box = ttk.Labelframe(right, text="Lautstärke")
        volume_box.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        self.volume_var = tk.IntVar(value=50)
        volume_controls = ttk.Frame(volume_box)
        volume_controls.pack(fill=tk.X, pady=4)
        ttk.Button(volume_controls, text="-", width=4, command=lambda: self._adjust_volume(-10), bootstyle="secondary").pack(side=LEFT, padx=3)
        self.volume_scale = ttk.Scale(volume_controls, from_=0, to=100, orient=tk.HORIZONTAL,
                                      command=self._on_volume_change, length=200)
        self.volume_scale.pack(side=LEFT, expand=True, fill=tk.X)
        ttk.Button(volume_controls, text="+", width=4, command=lambda: self._adjust_volume(10), bootstyle="secondary").pack(side=LEFT, padx=3)

        quick_box = ttk.Labelframe(right, text="Schnellaktionen")
        quick_box.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        self.shuffle_var = tk.BooleanVar(value=False)
        toggle_style = self.safe_toggle_style("round-toggle")
        try:
            if toggle_style:
                ttk.Checkbutton(quick_box, text="Shuffle", variable=self.shuffle_var,
                                command=self._set_shuffle, bootstyle=toggle_style).pack(side=LEFT, padx=8)
            else:
                ttk.Checkbutton(quick_box, text="Shuffle", variable=self.shuffle_var,
                                command=self._set_shuffle).pack(side=LEFT, padx=8)
        except Exception:
            ttk.Checkbutton(quick_box, text="Shuffle", variable=self.shuffle_var,
                            command=self._set_shuffle).pack(side=LEFT, padx=8)
        self.repeat_mode = tk.StringVar(value="off")
        self.repeat_button = ttk.Button(quick_box, text="Repeat: off", command=self._cycle_repeat,
                                        bootstyle="outline-secondary")
        self.repeat_button.pack(side=LEFT, padx=8)
        self.like_button = ttk.Button(quick_box, text="❤ Like", command=self._toggle_like,
                                      bootstyle="outline-success")
        self.like_button.pack(side=LEFT, padx=8)

        # Progress bar and controls now below quick actions
        self.progress_var = tk.StringVar(value="0:00 / 0:00")
        ttk.Label(right, textvariable=self.progress_var, font=("Arial", 11), foreground="#94a3b8").grid(row=2, column=0, sticky="ew", pady=(16, 4))

        controls = ttk.Frame(right)
        controls.grid(row=3, column=0, pady=(0, 0))
        ttk.Button(controls, text="⏮", width=5, command=self._prev_track, bootstyle="secondary-outline").pack(side=LEFT, padx=4)
        self.play_button = ttk.Button(controls, text="Play", width=8, command=self._toggle_playback, bootstyle="success")
        self.play_button.pack(side=LEFT, padx=4)
        ttk.Button(controls, text="⏭", width=5, command=self._next_track, bootstyle="secondary-outline").pack(side=LEFT, padx=4)

    def _build_library_tab(self) -> None:
        top = ttk.Frame(self.library_frame)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Playlists & Favoriten", font=("Arial", 14, "bold")).pack(anchor=W)

        list_wrapper = ttk.Frame(self.library_frame)
        list_wrapper.pack(fill=BOTH, expand=True, pady=(6, 0))
        self.playlist_canvas = tk.Canvas(list_wrapper, highlightthickness=0)
        v_scrollbar = ttk.Scrollbar(list_wrapper, orient=tk.VERTICAL, command=self.playlist_canvas.yview)
        self.playlist_canvas.configure(yscrollcommand=v_scrollbar.set)
        self.playlist_canvas.pack(side=tk.LEFT, fill=BOTH, expand=True)
        v_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.playlist_inner = ttk.Frame(self.playlist_canvas)
        self.playlist_canvas.create_window((0, 0), window=self.playlist_inner, anchor="nw")
        self.playlist_inner.bind(
            "<Configure>",
            lambda e: self.playlist_canvas.configure(scrollregion=self.playlist_canvas.bbox("all")),
        )
        self.playlist_empty = ttk.Label(self.playlist_inner, text="Noch keine Playlists geladen", padding=16)
        self.playlist_empty.pack()

    # ------------------------------------------------------------------
    # Spotify API Helpers
    # ------------------------------------------------------------------
    def _ensure_cached_session(self):
        # --- New login logic ---
        spotifylogin = self._import_spotifylogin()
        if not spotifylogin:
            self.client = None
            self._set_status("Spotify Modul fehlt")
            self.status_var.set("Spotify Modul fehlt")
            self.token_info_var.set("Kein Spotify Modul gefunden")
            return

        self.status_var.set("Login wird geprüft…")
        self.token_info_var.set("")
        try:
            client = spotifylogin.start_oauth()
        except Exception as exc:
            logging.error("[SPOTIFY] OAuth Fehler: %s", exc)
            self.client = None
            self._set_status("Login fehlgeschlagen")
            self.status_var.set("Login fehlgeschlagen")
            self.token_info_var.set(f"Fehler: {exc}")
            return

        if client:
            self.client = client
            self._set_status("Login erfolgreich – Spotify verbunden")
            self.status_var.set("Login erfolgreich – Spotify verbunden")
            self.token_info_var.set("Token gefunden und verbunden")
        else:
            self.client = None
            self._set_status("Spotify Login erforderlich")
            self.status_var.set("Spotify Login erforderlich")
            self.token_info_var.set("Kein Token gefunden – bitte im Browser einloggen")

        self._refresh_status()

    def _start_playback_poll(self):
        if self._poll_job:
            self.root.after_cancel(self._poll_job)
        self._poll_job = self.root.after(500, self._poll_playback)

    def _poll_playback(self):
        if not self.alive:
            return
        if not self.client:
            self._ensure_cached_session()
        if self.client:
            playback = self._safe_spotify_call(self.client.current_playback)
            if playback:
                self._last_playback = playback
                self._update_now_playing(playback)
            devices = self._safe_spotify_call(self.client.devices)
            if devices:
                self._update_devices(devices.get("devices", []))
        self._poll_job = self.root.after(POLL_INTERVAL_MS, self._poll_playback)

    def _safe_spotify_call(self, func, *args, **kwargs):
        if not self.client:
            return None
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            logging.error("[SPOTIFY] API Fehler: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Now Playing Updates
    # ------------------------------------------------------------------
    def _update_now_playing(self, playback: dict) -> None:
        item = playback.get("item") or {}
        track_id = item.get("id")
        track = item.get("name") or "–"
        artists = ", ".join(a.get("name", "") for a in item.get("artists", [])) or "–"
        album = item.get("album", {}).get("name") or "–"

        self.track_var.set(track)
        self.artist_var.set(artists)
        self.album_var.set(album)

        duration_ms = item.get("duration_ms") or 0
        progress_ms = playback.get("progress_ms") or 0
        self.progress_var.set(f"{self._fmt_time(progress_ms)} / {self._fmt_time(duration_ms)}")

        is_playing = playback.get("is_playing")
        self.play_button.configure(text="Pause" if is_playing else "Play",
                                   bootstyle="success" if is_playing else "secondary")

        volume = playback.get("device", {}).get("volume_percent")
        if volume is not None:
            self._ignore_volume_event = True
            self.volume_scale.set(volume)
            self._ignore_volume_event = False

        self.shuffle_var.set(bool(playback.get("shuffle_state", False)))
        repeat = playback.get("repeat_state", "off")
        self.repeat_mode.set(repeat)
        self.repeat_button.configure(text=f"Repeat: {repeat}")

        if track_id != self._last_track_id and track_id:
            images = item.get("album", {}).get("images") or []
            if images:
                self._set_cover_image(images[0].get("url"))
            self._update_like_state(track_id)
        self._last_track_id = track_id

    def _fmt_time(self, millis: int) -> str:
        seconds = max(0, int(millis / 1000))
        return f"{seconds // 60}:{seconds % 60:02d}"

    def _set_cover_image(self, url: Optional[str]) -> None:
        if not url:
            self.cover_label.configure(text="Cover nicht verfügbar", image="")
            return
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img = img.resize(COVER_SIZE, Image.LANCZOS)
            self._cover_photo = ImageTk.PhotoImage(img)
            self.cover_label.configure(image=self._cover_photo, text="")
        except Exception as exc:
            logging.error("[SPOTIFY] Cover-Download fehlgeschlagen: %s", exc)
            self.cover_label.configure(text="Cover nicht verfügbar", image="")

    def _update_like_state(self, track_id: str) -> None:
        liked = self._safe_spotify_call(self.client.current_user_saved_tracks_contains, [track_id])
        if isinstance(liked, list) and liked:
            self._liked_track = bool(liked[0])
            self._sync_like_button()

    def _sync_like_button(self) -> None:
        if self._liked_track:
            self.like_button.configure(text="❤ Gespeichert", bootstyle="success")
        else:
            self.like_button.configure(text="❤ Like", bootstyle="outline-success")

    # ------------------------------------------------------------------
    # Controls
    # ------------------------------------------------------------------
    def _toggle_playback(self):
        if not self.client:
            return
        playback = self._last_playback or {}
        if playback.get("is_playing"):
            self._safe_spotify_call(self.client.pause_playback)
        else:
            self._safe_spotify_call(self.client.start_playback)

    def _prev_track(self):
        if not self.client:
            return
        self._safe_spotify_call(self.client.previous_track)

    def _next_track(self):
        if not self.client:
            return
        self._safe_spotify_call(self.client.next_track)

    def _adjust_volume(self, delta: int):
        current = 0
        try:
            current = float(self.volume_scale.get())
        except Exception:
            pass
        new_value = max(0, min(100, int(current + delta)))
        self._ignore_volume_event = True
        self.volume_scale.set(new_value)
        self._ignore_volume_event = False
        self._queue_volume_update(new_value)

    def _on_volume_change(self, value: str):
        if self._ignore_volume_event:
            return
        try:
            volume = int(float(value))
        except (TypeError, ValueError):
            return
        self._queue_volume_update(volume)

    def _queue_volume_update(self, volume: int) -> None:
        volume = max(0, min(100, int(volume)))
        self._pending_volume = volume
        if self._volume_after_id:
            try:
                self.root.after_cancel(self._volume_after_id)
            except Exception:
                pass
        self._volume_after_id = self.root.after(400, self._apply_volume_change)

    def _apply_volume_change(self) -> None:
        self._volume_after_id = None
        if self._pending_volume is None or not self.client:
            return
        self._safe_spotify_call(self.client.volume, self._pending_volume)
        self._pending_volume = None

    def _set_shuffle(self):
        if not self.client:
            return
        state = bool(self.shuffle_var.get())
        self._safe_spotify_call(self.client.shuffle, state)

    def _cycle_repeat(self):
        if not self.client:
            return
        modes = ["off", "context", "track"]
        try:
            idx = modes.index(self.repeat_mode.get())
        except ValueError:
            idx = 0
        new_mode = modes[(idx + 1) % len(modes)]
        self._safe_spotify_call(self.client.repeat, new_mode)
        self.repeat_mode.set(new_mode)
        self.repeat_button.configure(text=f"Repeat: {new_mode}")

    def _toggle_like(self):
        if not self.client or not self._last_track_id:
            return
        track_id = self._last_track_id
        if self._liked_track:
            self._safe_spotify_call(self.client.current_user_saved_tracks_delete, [track_id])
            self._liked_track = False
        else:
            self._safe_spotify_call(self.client.current_user_saved_tracks_add, [track_id])
            self._liked_track = True
        self._sync_like_button()

    def _build_ui(self) -> None:
        wrapper = ttk.Frame(self.tab_frame)
        wrapper.pack(fill=BOTH, expand=True)

        ttk.Label(wrapper, textvariable=self.status_var, font=("Arial", 12), padding=6).pack(fill=tk.X, padx=12, pady=(10, 6))

        self.content_notebook = ttk.Notebook(wrapper, bootstyle="dark")
        self.content_notebook.pack(fill=BOTH, expand=True, padx=12, pady=(0, 12))

        self.now_playing_frame = ttk.Frame(self.content_notebook, padding=12)
        self.library_frame = ttk.Frame(self.content_notebook, padding=12)
        self.devices_frame = ttk.Frame(self.content_notebook, padding=12)
        self.status_frame = ttk.Frame(self.content_notebook, padding=16)
        # Add tabs in desired order: Status last
        self.content_notebook.add(self.now_playing_frame, text="Now Playing")
        self.content_notebook.add(self.library_frame, text="Playlists")
        self.content_notebook.add(self.devices_frame, text="Geräte")
        self.content_notebook.add(self.status_frame, text="Status")

        self._build_status_tab()
        self._build_now_playing_tab()
        self._build_library_tab()
        self._build_devices_tab()
        self._bind_tab_refresh()

    def _build_status_tab(self) -> None:
        frame = self.status_frame
        control_frame = ttk.Frame(frame)
        control_frame.pack(fill=tk.X, pady=(0, 10))
        ttk.Button(control_frame, text="Browser-Login öffnen", command=self._open_browser_login,
                   bootstyle="success-outline").pack(side=LEFT, padx=4)
        ttk.Button(control_frame, text="Status aktualisieren", command=self._refresh_status,
                   bootstyle="secondary-outline").pack(side=LEFT, padx=4)
        ttk.Button(control_frame, text="Logout", command=self._logout,
                   bootstyle="danger-outline").pack(side=LEFT, padx=4)

        # Login link UI and browser logic removed for local-only use
        ttk.Label(frame, textvariable=self.redirect_var, font=("Arial", 9), foreground="#94a3b8").pack(anchor=W)

        info_frame = ttk.Labelframe(frame, text="Status & Token")
        info_frame.pack(fill=tk.X)
        ttk.Label(info_frame, textvariable=self.status_detail_var, font=("Arial", 10)).pack(anchor=W, pady=(4, 2))
        ttk.Label(info_frame, textvariable=self.token_info_var, font=("Arial", 9), foreground="#94a3b8").pack(anchor=W, pady=(0, 4))
    # ------------------------------------------------------------------
    def _update_devices(self, devices: list[dict]) -> None:
        if devices == self._devices:
            return
        self._devices = devices
        for child in self.device_container.winfo_children():
            child.destroy()
        if not devices:
            ttk.Label(self.device_container, text="Keine Geräte", padding=8).pack()
            return
        active_id = next((dev.get("id") for dev in devices if dev.get("is_active")), None)
        for dev in devices:
            bootstyle = "primary" if dev.get("id") == active_id else "secondary"
            text = f"{dev.get('name', 'Gerät')}\n{dev.get('type', '')}"
            ttk.Button(self.device_container, text=text, width=22,
                       command=lambda d=dev: self._activate_device(d), bootstyle=bootstyle).pack(fill=tk.X, pady=4)

    def _activate_device(self, device: dict):
        if not self.client or not device.get("id"):
            return
        self._safe_spotify_call(self.client.transfer_playback, device.get("id"), force_play=False)

    def _refresh_devices(self):
        if not self.client:
            self._set_status("Spotify Login erforderlich")
            return
        devices = self._safe_spotify_call(self.client.devices)
        if devices:
            self._update_devices(devices.get("devices", []))

    def _refresh_playlists(self):
        if not self.client:
            return
        response = self._safe_spotify_call(self.client.current_user_playlists, limit=40)
        self._playlists_data = (response or {}).get("items", [])
        self._render_playlists()

    def _render_playlists(self):
        for child in self.playlist_inner.winfo_children():
            child.destroy()
        # Sort playlists by last played (if available), else by size
            def id_key(pl):
                # Use the 'id' field for sorting
                return pl.get('id', '')
            filtered = sorted(self._playlists_data, key=id_key)
        if not filtered:
            ttk.Label(self.playlist_inner, text="Keine Playlists gefunden", padding=16).pack()
            return
        # Horizontal layout: each playlist is a column
        for idx, playlist in enumerate(filtered):
            self._create_playlist_icon(playlist, idx)


    def _get_playlist_photo(self, playlist_id: Optional[str], url: Optional[str]) -> Optional[ImageTk.PhotoImage]:
        if not playlist_id or not url:
            return None
        cached = self._playlist_images.get(playlist_id)
        if cached:
            return cached
        try:
            resp = requests.get(url, timeout=5)
            resp.raise_for_status()
            img = Image.open(BytesIO(resp.content)).convert("RGB")
            img = img.resize(PLAYLIST_IMAGE_SIZE, Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self._playlist_images[playlist_id] = photo
            return photo
        except Exception:
            return None

    def _play_playlist(self, uri: Optional[str]):
        if self.client and uri:
            self._safe_spotify_call(self.client.start_playback, context_uri=uri)

    def _queue_playlist(self, playlist: dict):
        if not self.client:
            return
        playlist_id = playlist.get("id")
        if not playlist_id:
            return
        items = self._safe_spotify_call(self.client.playlist_items, playlist_id, limit=1)
        first = ((items or {}).get("items") or [{}])[0].get("track")
        if first and first.get("uri"):
            self._safe_spotify_call(self.client.add_to_queue, first.get("uri"))

    # ------------------------------------------------------------------
    # Login/Status Helpers
    # ------------------------------------------------------------------
    def _set_status(self, message: str) -> None:
        self.root.after(0, self.status_var.set, message)



    # _open_latest_in_browser removed for local-only use

    def _open_browser_login(self):
        self._set_status("Erzeuge Spotify Login-Link…")
        self._start_login_flow(auto_open=True)

    def _start_login_flow(self, auto_open: Optional[bool] = False):
        def _worker():
            import os
            os.environ["SPOTIFY_FORCE_BROWSER"] = "1"
            spotifylogin = self._import_spotifylogin()
            if not spotifylogin:
                self._set_status("Spotify Modul fehlt")
                return
            try:
                result = spotifylogin.begin_login_flow(auto_open=True)
            except Exception as exc:
                logging.error("[SPOTIFY] Login-Flow Fehler: %s", exc)
                self._set_status("Login-Flow konnte nicht gestartet werden")
                return
            login_url = result.get("url")
            if login_url:
                self._update_login_url(login_url)
                logging.info("[SPOTIFY] Login-Link: %s", login_url)
                # Fallback: always try to open in browser if not already opened
                try:
                    import webbrowser
                    webbrowser.open(login_url, new=1)
                except Exception as exc:
                    logging.error("[SPOTIFY] Fallback Browser-Start fehlgeschlagen: %s", exc)
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

    def _refresh_status(self):
        spotifylogin = self._import_spotifylogin()
        if not spotifylogin:
            self._set_status("Spotify Modul fehlt")
            self._set_status_details("spotifylogin.py konnte nicht geladen werden")
            self._set_token_info("Keine Informationen verfügbar")
            return
        try:
            status = spotifylogin.get_login_status()
        except Exception as exc:
            logging.error("[SPOTIFY] Statusabfrage fehlgeschlagen: %s", exc)
            self._set_status_details("Status konnte nicht ermittelt werden")
            self._set_token_info("Unbekannter Fehler")
            return
        redirect = status.get("redirect_uri")
        if redirect:
            self.root.after(0, self.redirect_var.set, f"Callback-URL: {redirect}")
        if not status.get("configured"):
            self._set_status_details("Konfiguration unvollständig – client_id/client_secret fehlen")
            self._set_token_info("Kein Token gespeichert")
            return
        if status.get("error"):
            self._set_status_details(status["error"])
        else:
            self._set_status_details("Konfiguration OK – Login möglich")
        if status.get("has_token"):
            expires_at = status.get("expires_at")
            if expires_at:
                expires = datetime.fromtimestamp(expires_at).strftime("%d.%m.%Y %H:%M")
                token_text = f"Token gültig bis {expires}"
            else:
                token_text = "Token vorhanden"
            scope = status.get("scope")
            if scope:
                token_text += f" | Scope: {scope}"
            self._set_token_info(token_text)
        else:
            self._set_token_info("Kein Token gespeichert – bitte Login starten")

    def _set_status_details(self, message: str) -> None:
        self.root.after(0, self.status_detail_var.set, message)

    def _set_token_info(self, message: str) -> None:
        self.root.after(0, self.token_info_var.set, message)

    def _logout(self):
        spotifylogin = self._import_spotifylogin()
        if not spotifylogin:
            self._set_status("Spotify Modul fehlt")
            return
        try:
            removed = spotifylogin.logout()
        except Exception as exc:
            logging.error("[SPOTIFY] Logout fehlgeschlagen: %s", exc)
            self._set_status("Logout fehlgeschlagen")
            return
        if removed:
            self.client = None
            self._set_status("Spotify Logout durchgeführt – Token entfernt")
        else:
            self._set_status("Kein gespeicherter Token gefunden")
        self._refresh_status()

    def _import_spotifylogin(self):
        try:
            import sys
            parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if parent_dir not in sys.path:
                sys.path.insert(0, parent_dir)
            import spotifylogin
            return spotifylogin
        except Exception as exc:
            logging.error("[SPOTIFY] spotifylogin Importfehler: %s", exc)
            return None

    def stop(self):
        self.alive = False
        if self._poll_job:
            try:
                self.root.after_cancel(self._poll_job)
            except Exception:
                pass
        if self._volume_after_id:
            try:
                self.root.after_cancel(self._volume_after_id)
            except Exception:
                pass
