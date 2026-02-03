import json
import logging
import os
import webbrowser
from typing import Optional

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError as exc:
    logging.error(f"[SPOTIFY] spotipy not available: {exc}")
    spotipy = None
    SpotifyOAuth = None


SCOPES = "user-read-playback-state user-modify-playback-state user-read-currently-playing"


def _load_config() -> dict:
    # Assuming this file is in src/, go up one level to project root
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    config_path = os.path.join(root_dir, "config", "spotify.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logging.error(f"[SPOTIFY] Failed to read config: {exc}")
    return {}


def _build_oauth() -> Optional["SpotifyOAuth"]:
    if SpotifyOAuth is None:
        return None
    cfg = _load_config()
    client_id = os.getenv("SPOTIPY_CLIENT_ID") or cfg.get("client_id")
    client_secret = os.getenv("SPOTIPY_CLIENT_SECRET") or cfg.get("client_secret")
    redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI") or cfg.get("redirect_uri") or "http://localhost:8888/callback"

    if not client_id or not client_secret:
        logging.error("[SPOTIFY] Missing client_id/client_secret (env or config/spotify.json)")
        return None

    # Cache file stored in config/.spotify_cache
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cache_path = os.path.join(root_dir, "config", ".spotify_cache")
    
    return SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=cache_path,
        open_browser=True,  # Immer Browser öffnen
    )


def start_oauth() -> Optional["spotipy.Spotify"]:
    """
    Attempt to authenticate. If no token is cached, it will print/log the URL
    but cannot finish without user interaction unless a browser opens nicely.
    """
    if spotipy is None:
        return None
    auth = _build_oauth()
    if auth is None:
        return None

    # check if we have a valid token
    token_info = auth.get_cached_token()
    
    if not token_info:
        # No token found, need to authorize
        try:
            auth_url = auth.get_authorize_url()
            print("\n" + "="*60)
            print("[SPOTIFY] Authentication Required!")
            print(f"Open this URL: {auth_url}")
            print("="*60 + "\n")
            logging.info("[SPOTIFY] Auth URL: %s", auth_url)
            
            # Try to open browser
            webbrowser.open(auth_url, new=1)
        except Exception as e:
            logging.error(f"[SPOTIFY] Error opening auth url: {e}")
        return None

    logging.info("[SPOTIFY] Authenticated successfully with cached token.")
    return spotipy.Spotify(auth=token_info["access_token"])


if __name__ == "__main__":
    # Test run
    logging.basicConfig(level=logging.INFO)
    start_oauth()
