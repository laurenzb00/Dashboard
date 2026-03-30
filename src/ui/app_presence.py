"""Presence override functionality for the main app.

Handles temporary override of Home Assistant person presence state
via device tracker or script-based approaches.
"""

import os
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from core.homeassistant import HomeAssistantClient


class PresenceOverrideManager:
    """Manages temporary presence override state.
    
    Allows forcing a person's home/away state for a configurable duration
    using either script-based or tracker-based approaches.
    
    Attributes:
        _deadline_mono: Monotonic time when override expires.
        _location_name: Current override location ("home" or "away").
        _after_id: Tkinter after() callback ID for keep-alive timer.
    """
    
    def __init__(
        self,
        get_ha_client: Callable[[], "HomeAssistantClient | None"],
        post_ui: Callable[[Callable], None],
        status_callback: Callable[[str], None],
        after_func: Callable[[int, Callable], Any],
        after_cancel_func: Callable[[Any], None],
    ) -> None:
        """Initialize the presence override manager.
        
        Args:
            get_ha_client: Function to get Home Assistant client.
            post_ui: Function to post UI updates to main thread.
            status_callback: Function to update status message.
            after_func: Tkinter root.after() function.
            after_cancel_func: Tkinter root.after_cancel() function.
        """
        self._get_ha_client = get_ha_client
        self._post_ui = post_ui
        self._status_callback = status_callback
        self._after = after_func
        self._after_cancel = after_cancel_func
        
        # Override state
        self._deadline_mono: float | None = None
        self._location_name: str | None = None
        self._after_id: Any = None
        self._last_ok: bool | None = None
        self._last_error: str = ""
        
        # Configuration
        self._person_entity_id: str = "person.laurenz"
        self._mode: str = "tracker"
        self._script_entity_id: str | None = None
        self._tracker_entity_id: str = "device_tracker.laurenz_override"
        self._refresh_s: int = 30
    
    def start(self, location_name: str, minutes: int = 10) -> None:
        """Start or restart a temporary presence override.
        
        Args:
            location_name: "home" or "away".
            minutes: Duration of the override in minutes.
        """
        # Cancel existing timer
        self.stop(silent=True)
        
        # Configuration (fixed by user request: default 10 minutes)
        try:
            duration_s = max(60, int(minutes) * 60)
        except Exception:
            duration_s = 600
        refresh_s = 30  # keep-alive cadence
        
        self._person_entity_id = "person.laurenz"
        
        # Optional script-based override
        script_home = (os.environ.get("PRESENCE_OVERRIDE_SCRIPT_HOME") or "").strip()
        script_away = (os.environ.get("PRESENCE_OVERRIDE_SCRIPT_AWAY") or "").strip()
        
        if script_home and script_away:
            self._mode = "script"
            self._script_entity_id = script_home if str(location_name).strip() == "home" else script_away
            refresh_s = 30  # No keep-alive needed; HA owns the timer
        else:
            self._mode = "tracker"
            self._script_entity_id = None
            self._tracker_entity_id = os.environ.get(
                "PRESENCE_OVERRIDE_TRACKER_ENTITY_ID",
                "device_tracker.laurenz_override",
            ).strip() or "device_tracker.laurenz_override"
        
        self._location_name = str(location_name or "").strip() or "home"
        self._refresh_s = int(refresh_s)
        self._deadline_mono = time.monotonic() + float(duration_s)
        self._after_id = None
        self._last_ok = None
        self._last_error = ""
        
        try:
            pretty = "HOME" if self._location_name == "home" else "AWAY"
            self._status_callback(f"Presence Override: {pretty} (10m)")
        except Exception:
            pass
        
        # Trigger immediately and schedule keep-alives
        self._tick()
    
    def stop(self, silent: bool = False) -> None:
        """Stop the presence override.
        
        Args:
            silent: If True, don't update status message.
        """
        if self._after_id is not None:
            try:
                self._after_cancel(self._after_id)
            except Exception:
                pass
        
        self._after_id = None
        self._deadline_mono = None
        self._location_name = None
        
        if not silent:
            try:
                self._status_callback("Presence Override: beendet")
            except Exception:
                pass
    
    def _tick(self) -> None:
        """Keep-alive tick for presence override."""
        if not self._deadline_mono:
            return
        
        now = time.monotonic()
        if now >= float(self._deadline_mono):
            self.stop(silent=False)
            return
        
        person_ent = self._person_entity_id
        mode = self._mode
        script_ent = self._script_entity_id
        tracker_ent = self._tracker_entity_id
        location_name = self._location_name or "home"
        refresh_s = max(10, min(120, self._refresh_s))
        
        def worker() -> None:
            ok = False
            err = ""
            try:
                client = self._get_ha_client()
                if not client:
                    err = "Home Assistant nicht konfiguriert"
                else:
                    if mode == "script":
                        if not script_ent:
                            err = "Override-Script nicht konfiguriert"
                        else:
                            ok = bool(client.call_service("script", "turn_on", {"entity_id": str(script_ent)}))
                            if not ok:
                                err = "script.turn_on fehlgeschlagen"
                    else:
                        # Check if tracker is attached to person
                        try:
                            pst = client.get_state(person_ent) or {}
                            trackers = (pst.get("attributes") or {}).get("device_trackers") or []
                            if isinstance(trackers, list) and tracker_ent not in [str(x) for x in trackers]:
                                err = f"Bitte {tracker_ent} bei {person_ent} hinzufügen"
                        except Exception:
                            pass
                        
                        ok = bool(client.force_person_presence(
                            person_ent, location_name, device_tracker_entity_id=tracker_ent
                        ))
                        if not ok:
                            err = "device_tracker.see fehlgeschlagen"
            except Exception as exc:
                ok = False
                err = str(exc)
            
            def apply() -> None:
                prev_ok = self._last_ok
                prev_err = str(self._last_error or "")
                self._last_ok = bool(ok)
                self._last_error = str(err or "")
                
                # Only update status when state changes
                if prev_ok is None:
                    if not ok and err:
                        try:
                            self._status_callback(f"Presence Override: Fehler ({err})")
                        except Exception:
                            pass
                    return
                
                if bool(prev_ok) != bool(ok):
                    if ok:
                        try:
                            pretty = "HOME" if location_name == "home" else "AWAY"
                            self._status_callback(f"Presence Override: {pretty} (10m)")
                        except Exception:
                            pass
                    else:
                        msg = err or prev_err
                        if msg:
                            try:
                                self._status_callback(f"Presence Override: Fehler ({msg})")
                            except Exception:
                                pass
            
            try:
                self._post_ui(apply)
            except Exception:
                pass
        
        try:
            # In script mode we only fire once (first tick)
            if mode == "script":
                if self._last_ok is None:
                    threading.Thread(target=worker, daemon=True).start()
            else:
                threading.Thread(target=worker, daemon=True).start()
        except Exception:
            pass
        
        # Reschedule keep-alive
        try:
            self._after_id = self._after(refresh_s * 1000, self._tick)
        except Exception:
            self._after_id = None
