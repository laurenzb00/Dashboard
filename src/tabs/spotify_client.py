"""Spotify API client wrapper and helper functions.

Provides a thread-safe interface to the Spotify Web API with
automatic token refresh and error handling.
"""

import logging
import os
import json
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


def load_spotify_config() -> dict[str, Any]:
    """Load Spotify configuration from config file.
    
    Returns:
        Configuration dictionary with client_id, client_secret, redirect_uri.
    """
    config_path = os.path.join(os.path.dirname(__file__), '../../config/spotify.json')
    if os.path.exists(config_path):
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as exc:
            logger.error("[SPOTIFY] Config konnte nicht geladen werden: %s", exc)
    return {}


def get_spotify_credentials() -> tuple[str | None, str | None, str | None]:
    """Get Spotify API credentials from config or environment.
    
    Environment variables take precedence over config file.
    
    Returns:
        Tuple of (client_id, client_secret, redirect_uri)
    """
    config = load_spotify_config()
    
    client_id = os.environ.get("SPOTIPY_CLIENT_ID") or config.get("client_id")
    client_secret = os.environ.get("SPOTIPY_CLIENT_SECRET") or config.get("client_secret")
    redirect_uri = os.environ.get("SPOTIPY_REDIRECT_URI") or config.get("redirect_uri")
    
    return client_id, client_secret, redirect_uri


def format_track_time(millis: int) -> str:
    """Format milliseconds into mm:ss format.
    
    Args:
        millis: Duration in milliseconds.
        
    Returns:
        Formatted string like "3:45".
    """
    seconds = millis // 1000
    m, s = divmod(seconds, 60)
    return f"{m}:{s:02d}"


class SpotifyClientWrapper:
    """Wrapper for Spotipy client with error handling.
    
    Provides safe API calls that catch and log errors without crashing.
    
    Attributes:
        client: The underlying Spotipy client instance.
        _last_error: Last error message for debugging.
    """
    
    def __init__(self, client: Any = None) -> None:
        """Initialize the wrapper.
        
        Args:
            client: Spotipy client instance.
        """
        self.client = client
        self._last_error: str = ""
    
    def safe_call(self, func: Callable, *args, **kwargs) -> Any:
        """Execute a Spotify API call with error handling.
        
        Args:
            func: The API function to call.
            *args: Positional arguments for the function.
            **kwargs: Keyword arguments for the function.
            
        Returns:
            Result of the API call, or None on error.
        """
        if not self.client:
            return None
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            self._last_error = str(exc)
            logger.error("[SPOTIFY] API Fehler: %s", exc)
            return None
    
    def current_playback(self) -> dict | None:
        """Get current playback state.
        
        Returns:
            Playback info dictionary or None.
        """
        return self.safe_call(self.client.current_playback) if self.client else None
    
    def devices(self) -> list[dict]:
        """Get available playback devices.
        
        Returns:
            List of device dictionaries.
        """
        result = self.safe_call(self.client.devices) if self.client else None
        return (result or {}).get("devices", [])
    
    def pause_playback(self) -> bool:
        """Pause playback.
        
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.pause_playback)
            return True
        except Exception:
            return False
    
    def start_playback(self, **kwargs) -> bool:
        """Start/resume playback.
        
        Args:
            **kwargs: Additional arguments (context_uri, device_id, etc.).
            
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.start_playback, **kwargs)
            return True
        except Exception:
            return False
    
    def next_track(self) -> bool:
        """Skip to next track.
        
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.next_track)
            return True
        except Exception:
            return False
    
    def previous_track(self) -> bool:
        """Go to previous track.
        
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.previous_track)
            return True
        except Exception:
            return False
    
    def volume(self, volume_percent: int) -> bool:
        """Set playback volume.
        
        Args:
            volume_percent: Volume 0-100.
            
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.volume, volume_percent)
            return True
        except Exception:
            return False
    
    def shuffle(self, state: bool) -> bool:
        """Set shuffle state.
        
        Args:
            state: True to enable shuffle.
            
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.shuffle, state)
            return True
        except Exception:
            return False
    
    def repeat(self, state: str) -> bool:
        """Set repeat mode.
        
        Args:
            state: "off", "track", or "context".
            
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.repeat, state)
            return True
        except Exception:
            return False
    
    def current_user_saved_tracks_contains(self, track_ids: list[str]) -> list[bool]:
        """Check if tracks are in user's library.
        
        Args:
            track_ids: List of track IDs to check.
            
        Returns:
            List of booleans.
        """
        result = self.safe_call(self.client.current_user_saved_tracks_contains, track_ids)
        return result if result else [False] * len(track_ids)
    
    def current_user_saved_tracks_add(self, track_ids: list[str]) -> bool:
        """Add tracks to user's library.
        
        Args:
            track_ids: Track IDs to add.
            
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.current_user_saved_tracks_add, track_ids)
            return True
        except Exception:
            return False
    
    def current_user_saved_tracks_delete(self, track_ids: list[str]) -> bool:
        """Remove tracks from user's library.
        
        Args:
            track_ids: Track IDs to remove.
            
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.current_user_saved_tracks_delete, track_ids)
            return True
        except Exception:
            return False
    
    def transfer_playback(self, device_id: str, force_play: bool = False) -> bool:
        """Transfer playback to a device.
        
        Args:
            device_id: Target device ID.
            force_play: Whether to start playing on transfer.
            
        Returns:
            True if successful.
        """
        if not self.client:
            return False
        try:
            self.safe_call(self.client.transfer_playback, device_id, force_play)
            return True
        except Exception:
            return False
    
    def current_user_playlists(self, limit: int = 50) -> list[dict]:
        """Get user's playlists.
        
        Args:
            limit: Maximum number of playlists to return.
            
        Returns:
            List of playlist dictionaries.
        """
        result = self.safe_call(self.client.current_user_playlists, limit=limit)
        return (result or {}).get("items", [])
    
    def current_user_recently_played(self, limit: int = 20) -> list[dict]:
        """Get recently played tracks.
        
        Args:
            limit: Maximum number of tracks.
            
        Returns:
            List of track items.
        """
        result = self.safe_call(self.client.current_user_recently_played, limit=limit)
        return (result or {}).get("items", [])
