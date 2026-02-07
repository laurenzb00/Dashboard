import atexit
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError as exc:  # pragma: no cover
    logging.error("[SPOTIFY] spotipy not available: %s", exc)
    spotipy = None
    SpotifyOAuth = None


SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing user-library-read"
_LOGIN_EVENT = threading.Event()
_LOGIN_ERROR: Optional[str] = None
_ACTIVE_AUTH: Optional["SpotifyOAuth"] = None
_CALLBACK_SERVER: Optional[ThreadingHTTPServer] = None
_CALLBACK_PATH = "/"
_LOGIN_LINK_PATH: Optional[Path] = None


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_config() -> dict:
    config_path = _project_root() / "config" / "spotify.json"
    if config_path.exists():
        try:
            return json.loads(config_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logging.error("[SPOTIFY] Failed to read config: %s", exc)
    return {}


def _build_oauth() -> Optional["SpotifyOAuth"]:
    if SpotifyOAuth is None:
        return None

    cfg = _load_config()

    client_id = os.getenv("SPOTIPY_CLIENT_ID") or cfg.get("client_id")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET") or cfg.get("client_secret")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI") or cfg.get("redirect_uri") or "http://127.0.0.1:8889/callback"
    if os.environ.get("DASHBOARD_DEBUG", "").strip().lower() in ("1", "true", "yes", "on"):
        print(f"[SPOTIFY DEBUG] Using redirect_uri: {redirect_uri}")

    if not client_id or not client_secret:
        logging.error("[SPOTIFY] Missing client_id/client_secret (env or config/spotify.json)")
        return None

    cache_path = _project_root() / "config" / ".spotify_cache"

    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=str(cache_path),
        open_browser=False,
    )


def start_oauth() -> Optional["spotipy.Spotify"]:
    """Return spotipy client if cached token is available."""
    if spotipy is None:
        return None
    auth = _build_oauth()
    if auth is None:
        return None

    token_info = auth.validate_token(auth.cache_handler.get_cached_token())
    if not token_info:
        return None

    logging.info("[SPOTIFY] Authenticated via cached token.")
    return spotipy.Spotify(auth_manager=auth)


def begin_login_flow(auto_open: Optional[bool] = None) -> dict:
    """Return authorize URL and ensure callback server is running."""
    if SpotifyOAuth is None:
        return {"ok": False, "error": "spotipy nicht installiert"}

    auth = _build_oauth()
    if auth is None:
        return {"ok": False, "error": "Spotify-Konfiguration unvollständig"}

    error = _ensure_callback_server(auth)
    if error:
        return {"ok": False, "error": error}

    _reset_login_state(auth)
    login_url = auth.get_authorize_url()
    _write_login_hint(login_url)

    if _should_auto_open(auto_open):
        _launch_browser(login_url)

    return {"ok": True, "url": login_url, "redirect": auth.redirect_uri}


def wait_for_login_result(timeout: int = 300) -> Tuple[bool, Optional[str]]:
    if not _LOGIN_EVENT.wait(timeout):
        return False, "Zeitüberschreitung – kein Spotify-Callback erhalten"
    return (_LOGIN_ERROR is None), _LOGIN_ERROR


def get_login_hint_path() -> Optional[Path]:
    return _LOGIN_LINK_PATH


def _reset_login_state(auth: "SpotifyOAuth") -> None:
    global _LOGIN_ERROR, _ACTIVE_AUTH
    _LOGIN_EVENT.clear()
    _LOGIN_ERROR = None
    _ACTIVE_AUTH = auth


def _write_login_hint(url: str) -> None:
    global _LOGIN_LINK_PATH
    hint_path = _project_root() / "config" / "spotify_login_url.txt"
    hint_path.write_text(url + "\n", encoding="utf-8")
    _LOGIN_LINK_PATH = hint_path
    logging.info("[SPOTIFY] Login-URL gespeichert unter %s", hint_path)


def _ensure_callback_server(auth: "SpotifyOAuth") -> Optional[str]:
    global _CALLBACK_SERVER, _CALLBACK_PATH

    parsed = urlparse(auth.redirect_uri)
    if not parsed.port:
        return "Redirect-URI benötigt Port (z.B. :8889)"
    _CALLBACK_PATH = parsed.path or "/"

    if _CALLBACK_SERVER:
        return None

    try:
        server = ThreadingHTTPServer(("0.0.0.0", parsed.port), _make_handler(_CALLBACK_PATH, auth.state))
        server.daemon_threads = True
    except OSError as exc:
        return f"Port {parsed.port} belegt: {exc}"

    _CALLBACK_SERVER = server
    thread = threading.Thread(target=server.serve_forever, name="SpotifyCallbackServer", daemon=True)
    thread.start()
    atexit.register(server.shutdown)
    logging.info("[SPOTIFY] Callback-Server läuft auf Port %s", parsed.port)
    return None


def _make_handler(expected_path: str, expected_state: Optional[str]):
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):  # pragma: no cover - silence default logging
            return

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != expected_path:
                self._respond(404, "Pfad nicht gefunden")
                return

            params = parse_qs(parsed.query)
            state = params.get("state", [None])[0]
            code = params.get("code", [None])[0]

            if expected_state and state != expected_state:
                _finish_login_with_error("Ungültiger OAuth-State")
                self._respond(400, "Ungültiger State")
                return

            if not code:
                _finish_login_with_error("Spotify lieferte keinen Auth-Code")
                self._respond(400, "Fehlender Code")
                return

            threading.Thread(target=_exchange_code, args=(code,), daemon=True).start()
            self._respond(200, "<h2>Spotify Login erfolgreich</h2><p>Du kannst dieses Fenster schließen.</p>")

        def _respond(self, status: int, body: str):
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    return _Handler


def _exchange_code(code: str) -> None:
    auth = _ACTIVE_AUTH or _build_oauth()
    if auth is None:
        _finish_login_with_error("SpotifyAuth nicht initialisiert")
        return
    try:
        auth.get_access_token(code=code, as_dict=True, check_cache=False)
        logging.info("[SPOTIFY] Token erfolgreich übernommen")
        _mark_login_success()
    except Exception as exc:
        _finish_login_with_error(str(exc))


def _mark_login_success():
    global _LOGIN_ERROR
    _LOGIN_ERROR = None
    _LOGIN_EVENT.set()


def _finish_login_with_error(message: str) -> None:
    global _LOGIN_ERROR
    _LOGIN_ERROR = message
    _LOGIN_EVENT.set()
    logging.error("[SPOTIFY] Login fehlgeschlagen: %s", message)


def _should_auto_open(auto_open: Optional[bool]) -> bool:
    if auto_open is not None:
        return auto_open
    forced = os.getenv("SPOTIFY_FORCE_BROWSER")
    if forced == "1":
        return True
    if forced == "0":
        return False
    if os.name == "nt":
        return True
    return bool(os.getenv("DISPLAY"))


def _launch_browser(url: str) -> None:
    try:
        import webbrowser
        # Try to use a real browser, not a text-based or terminal browser
        browsers = [
            "windows-default",
            "chrome",
            "chromium",
            "firefox",
            "edge",
            "opera",
        ]
        opened = False
        for b in browsers:
            try:
                browser = webbrowser.get(b)
                if browser.open(url, new=1):
                    opened = True
                    break
            except webbrowser.Error:
                continue
        if not opened:
            # Fallback to system default
            if not webbrowser.open(url, new=1):
                logging.warning("[SPOTIFY] Bitte Browser selbst öffnen: %s", url)
        else:
            logging.info("[SPOTIFY] Login-URL im Browser geöffnet")
    except Exception as exc:
        logging.warning("[SPOTIFY] Browser konnte nicht geöffnet werden: %s", exc)


def get_login_status() -> dict:
    status = {
        "configured": False,
        "has_token": False,
        "redirect_uri": None,
        "expires_at": None,
        "scope": None,
        "error": None,
    }
    cfg = _load_config()
    client_id = os.getenv("SPOTIPY_CLIENT_ID") or cfg.get("client_id")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET") or cfg.get("client_secret")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI") or cfg.get("redirect_uri") or "http://127.0.0.1:8889/callback"
    status["redirect_uri"] = redirect_uri
    status["configured"] = bool(client_id and client_secret)
    if SpotifyOAuth is None:
        status["error"] = "spotipy nicht installiert"
        return status
    if not status["configured"]:
        status["error"] = "client_id/client_secret fehlen"
        return status

    auth = _build_oauth()
    if auth is None:
        status["error"] = "SpotifyOAuth konnte nicht initialisiert werden"
        return status

    cached = auth.cache_handler.get_cached_token()
    if not cached:
        return status
    token_info = auth.validate_token(cached)
    if not token_info:
        return status
    status["has_token"] = True
    status["expires_at"] = token_info.get("expires_at")
    status["scope"] = token_info.get("scope") or SCOPES
    return status


def logout() -> bool:
    cache = _project_root() / "config" / ".spotify_cache"
    try:
        cache.unlink()
        logging.info("[SPOTIFY] Cached token removed")
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:
        logging.error("[SPOTIFY] Could not remove cache: %s", exc)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    result = begin_login_flow()
    if not result.get("ok"):
        print("Fehler:", result.get("error"))
    else:
        print("Öffne folgenden Link auf einem beliebigen Gerät:")
        print(result["url"])
        ok, err = wait_for_login_result()
        if ok:
            print("Token gespeichert.")
        else:
            print("Login fehlgeschlagen:", err)
