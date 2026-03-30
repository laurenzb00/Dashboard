"""Home Assistant callback handlers for the main app.

Contains functions for triggering webhooks, scripts, automations,
and input booleans via the Home Assistant API.
"""

import os
import threading
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from core.homeassistant import HomeAssistantClient


def get_shower_script_entity_id(ha_client: "HomeAssistantClient | None") -> str:
    """Get the entity ID for the shower script.
    
    Checks environment variable first, then config, then defaults.
    
    Args:
        ha_client: Home Assistant client for config access.
        
    Returns:
        Entity ID string like "script.duschen_gehen".
    """
    try:
        env = (os.environ.get("SHOWER_SCRIPT_ENTITY_ID") or "").strip()
        if env:
            return env
    except Exception:
        pass

    try:
        cfg = getattr(ha_client, "config", None) if ha_client else None
        val = getattr(cfg, "shower_script_entity_id", None) if cfg else None
        val = str(val or "").strip()
        if val:
            return val
    except Exception:
        pass

    return "script.duschen_gehen"


def get_leaving_home_input_boolean_entity_id(ha_client: "HomeAssistantClient | None") -> str:
    """Get the entity ID for the leaving home input boolean.
    
    Args:
        ha_client: Home Assistant client for config access.
        
    Returns:
        Entity ID string.
    """
    try:
        env = (os.environ.get("LEAVING_HOME_INPUT_BOOLEAN_ENTITY_ID") or "").strip()
        if env:
            return env
    except Exception:
        pass

    try:
        cfg = getattr(ha_client, "config", None) if ha_client else None
        val = getattr(cfg, "leaving_home_input_boolean_entity_id", None) if cfg else None
        val = str(val or "").strip()
        if val:
            return val
    except Exception:
        pass

    return "input_boolean.leaving_home"


def get_force_away_webhook_id(ha_client: "HomeAssistantClient | None") -> str:
    """Get the webhook ID for forcing away status.
    
    Args:
        ha_client: Home Assistant client for config access.
        
    Returns:
        Webhook ID string.
    """
    try:
        env = (os.environ.get("HOMEASSISTANT_FORCE_AWAY_WEBHOOK_ID") or "").strip()
        if env:
            return env
    except Exception:
        pass

    try:
        cfg = getattr(ha_client, "config", None) if ha_client else None
        val = getattr(cfg, "force_away_webhook_id", None) if cfg else None
        val = str(val or "").strip()
        if val:
            return val
    except Exception:
        pass

    return "9b5bdbb281ff4d129a5c3f95d3530f58"


def get_force_home_webhook_id(ha_client: "HomeAssistantClient | None") -> str:
    """Get the webhook ID for forcing home status.
    
    Args:
        ha_client: Home Assistant client for config access.
        
    Returns:
        Webhook ID string.
    """
    try:
        env = (os.environ.get("HOMEASSISTANT_FORCE_HOME_WEBHOOK_ID") or "").strip()
        if env:
            return env
    except Exception:
        pass

    try:
        cfg = getattr(ha_client, "config", None) if ha_client else None
        val = getattr(cfg, "force_home_webhook_id", None) if cfg else None
        val = str(val or "").strip()
        if val:
            return val
    except Exception:
        pass

    return "3d3a1b55b8554ee49bbfa77a4380a0b0"


def trigger_ha_input_boolean_turn_on(
    ha_client: "HomeAssistantClient | None",
    entity_id: str,
    status_label: str,
    status_callback: Callable[[str], None],
    post_ui: Callable[[Callable], None],
) -> None:
    """Turn on a Home Assistant input boolean.
    
    Runs the API call in a background thread to avoid blocking.
    
    Args:
        ha_client: Home Assistant client.
        entity_id: The input boolean entity ID.
        status_label: Label for status messages.
        status_callback: Function to update status message.
        post_ui: Function to post UI updates to main thread.
    """
    entity_id = str(entity_id or "").strip()
    if not entity_id:
        return

    try:
        status_callback(f"Input Boolean: {status_label}…")
    except Exception:
        pass

    def worker() -> None:
        ok = False
        err = ""
        try:
            if not ha_client:
                err = "Home Assistant nicht konfiguriert"
            else:
                ok = bool(ha_client.call_service("input_boolean", "turn_on", {"entity_id": entity_id}))
        except Exception as exc:
            ok = False
            err = str(exc)

        def apply() -> None:
            try:
                if ok:
                    status_callback(f"Input Boolean: {status_label} aktiviert")
                else:
                    msg = err or "fehlgeschlagen"
                    status_callback(f"Input Boolean: Fehler ({msg})")
            except Exception:
                pass

        try:
            post_ui(apply)
        except Exception:
            pass

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pass


def trigger_ha_script(
    ha_client: "HomeAssistantClient | None",
    entity_id: str,
    status_label: str,
    status_callback: Callable[[str], None],
    post_ui: Callable[[Callable], None],
) -> None:
    """Trigger a Home Assistant script.
    
    Runs the API call in a background thread to avoid blocking.
    
    Args:
        ha_client: Home Assistant client.
        entity_id: The script entity ID.
        status_label: Label for status messages.
        status_callback: Function to update status message.
        post_ui: Function to post UI updates to main thread.
    """
    entity_id = str(entity_id or "").strip()
    if not entity_id:
        return

    try:
        status_callback(f"Script: {status_label}…")
    except Exception:
        pass

    def worker() -> None:
        ok = False
        err = ""
        try:
            if not ha_client:
                err = "Home Assistant nicht konfiguriert"
            else:
                ok = bool(ha_client.call_service("script", "turn_on", {"entity_id": entity_id}))
        except Exception as exc:
            ok = False
            err = str(exc)

        def apply() -> None:
            try:
                if ok:
                    status_callback(f"Script: {status_label} gestartet")
                else:
                    msg = err or "fehlgeschlagen"
                    status_callback(f"Script: Fehler ({msg})")
            except Exception:
                pass

        try:
            post_ui(apply)
        except Exception:
            pass

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pass


def trigger_ha_automation(
    ha_client: "HomeAssistantClient | None",
    entity_id: str,
    status_label: str,
    status_callback: Callable[[str], None],
    post_ui: Callable[[Callable], None],
) -> None:
    """Trigger a Home Assistant automation.
    
    Runs the API call in a background thread to avoid blocking.
    
    Args:
        ha_client: Home Assistant client.
        entity_id: The automation entity ID.
        status_label: Label for status messages.
        status_callback: Function to update status message.
        post_ui: Function to post UI updates to main thread.
    """
    entity_id = str(entity_id or "").strip()
    if not entity_id:
        return

    try:
        status_callback(f"Automation: {status_label}…")
    except Exception:
        pass

    def worker() -> None:
        ok = False
        err = ""
        try:
            if not ha_client:
                err = "Home Assistant nicht konfiguriert"
            else:
                ok = bool(ha_client.call_service("automation", "trigger", {"entity_id": entity_id}))
        except Exception as exc:
            ok = False
            err = str(exc)

        def apply() -> None:
            try:
                if ok:
                    status_callback(f"Automation: {status_label} gestartet")
                else:
                    msg = err or "fehlgeschlagen"
                    status_callback(f"Automation: Fehler ({msg})")
            except Exception:
                pass

        try:
            post_ui(apply)
        except Exception:
            pass

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pass


def trigger_ha_webhook(
    ha_client: "HomeAssistantClient | None",
    webhook_id: str,
    status_label: str,
    status_callback: Callable[[str], None],
    post_ui: Callable[[Callable], None],
) -> None:
    """Trigger a Home Assistant webhook.
    
    Runs the API call in a background thread to avoid blocking.
    
    Args:
        ha_client: Home Assistant client.
        webhook_id: The webhook ID to trigger.
        status_label: Label for status messages.
        status_callback: Function to update status message.
        post_ui: Function to post UI updates to main thread.
    """
    webhook_id = str(webhook_id or "").strip()
    if not webhook_id:
        return

    try:
        status_callback(f"Webhook: {status_label}…")
    except Exception:
        pass

    def worker() -> None:
        ok = False
        err = ""
        try:
            if not ha_client:
                err = "Home Assistant nicht konfiguriert"
            else:
                ok = bool(ha_client.trigger_webhook(webhook_id))
        except Exception as exc:
            ok = False
            err = str(exc)

        def apply() -> None:
            try:
                if ok:
                    status_callback(f"Webhook: {status_label} ausgelöst")
                else:
                    msg = err or "fehlgeschlagen"
                    status_callback(f"Webhook: Fehler ({msg})")
            except Exception:
                pass

        try:
            post_ui(apply)
        except Exception:
            pass

    try:
        threading.Thread(target=worker, daemon=True).start()
    except Exception:
        pass
