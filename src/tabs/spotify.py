import logging
import os
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from io import BytesIO
from typing import Optional

import requests
import ttkbootstrap as ttk
from PIL import Image, ImageTk
from ttkbootstrap.constants import BOTH, LEFT, RIGHT, W


POLL_INTERVAL_MS = 5000
COVER_SIZE = (260, 260)
PLAYLIST_IMAGE_SIZE = (96, 96)


class SpotifyTab:
    """Touch-optimierte Spotify-Integration mit Now-Playing- und Playlist-Ansicht."""

    def __init__(self, root, notebook):
        self.root = root
        self.notebook = notebook
        self.alive = True

        self.tab_frame = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_frame, text="Spotify")

        self._build_header()
        self._build_content()

        # Runtime state holders
        self.client = None
        self._latest_login_url: Optional[str] = None
        self._cover_photo: Optional[ImageTk.PhotoImage] = None
        self._playlist_images: dict[str, ImageTk.PhotoImage] = {}
        self._devices: list[dict] = []
        self._last_track_id: Optional[str] = None
        self._last_playback: Optional[dict] = None
        self._liked_track = False
        self._poll_job: Optional[str] = None
        self._volume_after_id: Optional[str] = None
        self._pending_volume: Optional[int] = None
        self._ignore_volume_event = False
        self._playlists_data: list[dict] = []

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

        link_frame = ttk.Frame(self.tab_frame)
        link_frame.pack(fill=tk.X, padx=20, pady=(6, 10))
        ttk.Label(link_frame, text="Login-Link", font=("Arial", 10, "bold")).pack(anchor=W)
        self.link_var = tk.StringVar(value="Noch kein Login-Link erzeugt")
        self.link_entry = ttk.Entry(link_frame, textvariable=self.link_var, state="readonly")
        self.link_entry.pack(fill=tk.X, expand=True, pady=(2, 4))
        link_buttons = ttk.Frame(link_frame)
        link_buttons.pack(anchor=W)
        ttk.Button(link_buttons, text="Link kopieren", command=self._copy_login_url,
                   bootstyle="secondary-outline").pack(side=LEFT, padx=(0, 6))
        ttk.Button(link_buttons, text="Link im Browser öffnen", command=self._open_latest_in_browser,
                   bootstyle="info-outline").pack(side=LEFT)

        callback_uri = os.getenv("SPOTIPY_REDIRECT_URI") or "http://127.0.0.1:8889/callback"
        self.redirect_var = tk.StringVar(value=f"Callback-URL: {callback_uri}")
        ttk.Label(link_frame, textvariable=self.redirect_var, font=("Arial", 9), foreground="#94a3b8").pack(anchor=W, pady=(6, 0))
        ttk.Label(link_frame,
                  text="Öffne den Link auf einem Gerät im selben Netzwerk – der Dashboard-Port 8889 nimmt den Rückruf automatisch an.",
                  font=("Arial", 9), wraplength=540).pack(anchor=W, pady=(2, 0))

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
        self.devices_frame = ttk.Frame(self.content_notebook, padding=12)
        self.content_notebook.add(self.now_playing_frame, text="Now Playing")
        self.content_notebook.add(self.library_frame, text="Playlists")
        self.content_notebook.add(self.devices_frame, text="Geräte")

        self._build_now_playing_tab()
        self._build_library_tab()
        self._build_devices_tab()

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

        self.progress_var = tk.StringVar(value="0:00 / 0:00")
        ttk.Label(left, textvariable=self.progress_var, font=("Arial", 11), foreground="#94a3b8").pack(anchor=W, pady=(0, 6))

        controls = ttk.Frame(left)
        controls.pack(pady=(4, 0))
        ttk.Button(controls, text="⏮", width=5, command=self._prev_track, bootstyle="secondary-outline").pack(side=LEFT, padx=4)
        self.play_button = ttk.Button(controls, text="Play", width=8, command=self._toggle_playback, bootstyle="success")
        self.play_button.pack(side=LEFT, padx=4)
        ttk.Button(controls, text="⏭", width=5, command=self._next_track, bootstyle="secondary-outline").pack(side=LEFT, padx=4)

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
        ttk.Checkbutton(quick_box, text="Shuffle", variable=self.shuffle_var,
                        command=self._set_shuffle, bootstyle="round-toggle").pack(side=LEFT, padx=8)
        self.repeat_mode = tk.StringVar(value="off")
        self.repeat_button = ttk.Button(quick_box, text="Repeat: off", command=self._cycle_repeat,
                                        bootstyle="outline-secondary")
        self.repeat_button.pack(side=LEFT, padx=8)
        self.like_button = ttk.Button(quick_box, text="❤ Like", command=self._toggle_like,
                                      bootstyle="outline-success")
        self.like_button.pack(side=LEFT, padx=8)

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

    def _build_library_tab(self) -> None:
        top = ttk.Frame(self.library_frame)
        top.pack(fill=tk.X)
        ttk.Label(top, text="Playlists & Favoriten", font=("Arial", 14, "bold")).pack(anchor=W)
        search_row = ttk.Frame(top)
        search_row.pack(fill=tk.X, pady=(8, 6))
        self.playlist_search = tk.StringVar()
        search_entry = ttk.Entry(search_row, textvariable=self.playlist_search)
        search_entry.pack(side=LEFT, fill=tk.X, expand=True)
        search_entry.bind("<KeyRelease>", lambda _e: self._render_playlists())
        ttk.Button(search_row, text="Aktualisieren", command=self._refresh_playlists,
                   bootstyle="info").pack(side=LEFT, padx=6)

        list_wrapper = ttk.Frame(self.library_frame)
        list_wrapper.pack(fill=BOTH, expand=True, pady=(6, 0))
        self.playlist_canvas = tk.Canvas(list_wrapper, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_wrapper, orient=tk.VERTICAL, command=self.playlist_canvas.yview)
        self.playlist_canvas.configure(yscrollcommand=scrollbar.set)
        self.playlist_canvas.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar.pack(side=RIGHT, fill=tk.Y)

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
        spotifylogin = self._import_spotifylogin()
        if not spotifylogin:
            self._set_status("Spotify Modul fehlt")
            return
        try:
            client = spotifylogin.start_oauth()
        except Exception as exc:
            logging.error("[SPOTIFY] Start OAuth failed: %s", exc)
            client = None
        if client:
            self.client = client
            self._set_status("Spotify Token gefunden – verbunden")
        else:
            self._set_status("Spotify Login erforderlich")
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

    def _next_track(self):
        if self.client:
            self._safe_spotify_call(self.client.next_track)

    def _prev_track(self):
        if self.client:
            self._safe_spotify_call(self.client.previous_track)

    def _on_volume_change(self, value: str):
        if self._ignore_volume_event:
            return
        self._pending_volume = int(float(value))
        if self._volume_after_id:
            self.root.after_cancel(self._volume_after_id)
        self._volume_after_id = self.root.after(350, self._send_volume)

    def _adjust_volume(self, delta: int):
        current = int(float(self.volume_scale.get()))
        new_value = max(0, min(100, current + delta))
        self.volume_scale.set(new_value)
        self._on_volume_change(str(new_value))

    def _send_volume(self):
        if self.client and self._pending_volume is not None:
            self._safe_spotify_call(self.client.volume, self._pending_volume)
        self._volume_after_id = None

    def _set_shuffle(self):
        if self.client:
            self._safe_spotify_call(self.client.shuffle, self.shuffle_var.get())

    def _cycle_repeat(self):
        modes = ["off", "context", "track"]
        current = self.repeat_mode.get()
        next_mode = modes[(modes.index(current) + 1) % len(modes)] if current in modes else "context"
        if self.client:
            self._safe_spotify_call(self.client.repeat, next_mode)
        self.repeat_mode.set(next_mode)
        self.repeat_button.configure(text=f"Repeat: {next_mode}")

    def _toggle_like(self):
        if not self.client or not self._last_track_id:
            return
        if self._liked_track:
            self._safe_spotify_call(self.client.current_user_saved_tracks_delete, [self._last_track_id])
            self._liked_track = False
        else:
            self._safe_spotify_call(self.client.current_user_saved_tracks_add, [self._last_track_id])
            self._liked_track = True
        self._sync_like_button()

    # ------------------------------------------------------------------
    # Geräte & Playlists
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
        query = (self.playlist_search.get() or "").lower()
        filtered = [pl for pl in self._playlists_data if query in pl.get("name", "").lower()]
        if not filtered:
            ttk.Label(self.playlist_inner, text="Keine Playlists gefunden", padding=16).pack()
            return
        for playlist in filtered:
            self._create_playlist_card(playlist)

    def _create_playlist_card(self, playlist: dict):
        card = ttk.Frame(self.playlist_inner, padding=10, relief="ridge")
        card.pack(fill=tk.X, pady=6)
        card.columnconfigure(1, weight=1)

        image_url = (playlist.get("images") or [{}])[0].get("url")
        photo = self._get_playlist_photo(playlist.get("id"), image_url)
        ttk.Label(card, image=photo if photo else None, text="" if photo else "Cover",
                  width=12).grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 10))

        name = playlist.get("name", "Unbenannte Playlist")
        tracks_total = playlist.get("tracks", {}).get("total", 0)
        ttk.Label(card, text=name, font=("Arial", 14, "bold")).grid(row=0, column=1, sticky=W)
        ttk.Label(card, text=f"{tracks_total} Titel", font=("Arial", 10), foreground="#9ca3af").grid(row=1, column=1, sticky=W)

        btns = ttk.Frame(card)
        btns.grid(row=0, column=2, rowspan=2, padx=(10, 0))
        uri = playlist.get("uri")
        ttk.Button(btns, text="Jetzt abspielen", command=lambda u=uri: self._play_playlist(u), bootstyle="success").pack(fill=tk.X, pady=2)
        ttk.Button(btns, text="In Queue", command=lambda p=playlist: self._queue_playlist(p), bootstyle="secondary").pack(fill=tk.X, pady=2)

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
            logging.error("[SPOTIFY] Clipboard Fehler: %s", exc)
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
            logging.error("[SPOTIFY] Browser-Start fehlgeschlagen: %s", exc)
            self._set_status("Browser-Start fehlgeschlagen")

    def _open_browser_login(self):
        self._set_status("Erzeuge Spotify Login-Link…")
        self._start_login_flow(auto_open=False)

    def _start_login_flow(self, auto_open: Optional[bool] = False):
        def _worker():
            spotifylogin = self._import_spotifylogin()
            if not spotifylogin:
                self._set_status("Spotify Modul fehlt")
                return
            try:
                result = spotifylogin.begin_login_flow(auto_open=auto_open)
            except Exception as exc:
                logging.error("[SPOTIFY] Login-Flow Fehler: %s", exc)
                self._set_status("Login-Flow konnte nicht gestartet werden")
                return
            login_url = result.get("url")
            if login_url:
                self._update_login_url(login_url)
                logging.info("[SPOTIFY] Login-Link: %s", login_url)
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
