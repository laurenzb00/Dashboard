"""Data handlers for the main app.

Contains functions for processing incoming data from energy sources
(PV inverter, BMK heating system) and updating app state.
"""

from datetime import datetime
from typing import Any, TYPE_CHECKING

from core.utils import safe_float
from core.schema import (
    PV_POWER_KW,
    GRID_POWER_KW,
    BATTERY_POWER_KW,
    BATTERY_SOC_PCT,
    LOAD_POWER_KW,
    BMK_KESSEL_C,
    BMK_WARMWASSER_C,
    BMK_BETRIEBSMODUS,
    BUF_TOP_C,
    BUF_MID_C,
    BUF_BOTTOM_C,
)
from ui.app_helpers import format_bmk_mode, parse_timestamp_value

if TYPE_CHECKING:
    from ui.app_state import AppState


def process_wechselrichter_data(
    data: dict[str, Any],
    app_state: "AppState | None",
    last_data: dict[str, Any],
    source_health: dict[str, dict],
) -> None:
    """Process incoming PV inverter data.
    
    Extracts power values, updates internal state cache, and pushes
    updates to the app_state for UI subscribers.
    
    Args:
        data: Raw data dictionary from the inverter.
        app_state: AppState instance for reactive updates.
        last_data: Mutable dict for storing last known values.
        source_health: Health tracking dict for data sources.
    """
    ts = parse_timestamp_value(data.get("Zeitstempel")) or datetime.now()
    source = source_health.get("pv")
    if source:
        source["ts"] = ts
        source["count"] += 1
    
    pv_kw = safe_float(data.get("PV-Leistung (kW)"))
    grid_kw = safe_float(data.get("Netz-Leistung (kW)"))
    batt_kw = safe_float(data.get("Batterie-Leistung (kW)"))
    load_kw = safe_float(data.get("Hausverbrauch (kW)"))
    soc = safe_float(data.get("Batterieladestand (%)"))
    
    # Update last_data cache (in Watts for legacy compatibility)
    if pv_kw is not None:
        last_data["pv"] = pv_kw * 1000
    if load_kw is not None:
        last_data["load"] = load_kw * 1000
    if batt_kw is not None:
        last_data["batt"] = -batt_kw * 1000
    
    if grid_kw is not None:
        last_data["grid"] = grid_kw * 1000
    elif pv_kw is not None or load_kw is not None or batt_kw is not None:
        # Calculate grid power if not directly provided
        calc_load = load_kw if load_kw is not None else (pv_kw or 0.0) + (grid_kw or 0.0) - (batt_kw or 0.0)
        calc_grid = (calc_load or 0.0) - (pv_kw or 0.0) + (batt_kw or 0.0)
        last_data["grid"] = calc_grid * 1000
    
    if soc is not None:
        last_data["soc"] = soc
    
    # Push to app_state for reactive UI updates
    if app_state is not None:
        payload = {
            "timestamp": data.get("Zeitstempel") or data.get("timestamp"),
            PV_POWER_KW: pv_kw,
            GRID_POWER_KW: grid_kw,
            BATTERY_POWER_KW: batt_kw,
            BATTERY_SOC_PCT: soc,
            LOAD_POWER_KW: load_kw,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        app_state.update(payload)


def process_bmkdaten_data(
    data: dict[str, Any],
    app_state: "AppState | None",
    source_health: dict[str, dict],
) -> None:
    """Process incoming BMK heating system data.
    
    Extracts temperature values and mode, and pushes updates to the
    app_state for UI subscribers.
    
    Args:
        data: Raw data dictionary from the BMK system.
        app_state: AppState instance for reactive updates.
        source_health: Health tracking dict for data sources.
    """
    import logging
    logging.info("[BMK] process_bmkdaten_data: data=%s", data)
    
    ts = parse_timestamp_value(data.get("Zeitstempel")) or datetime.now()
    source = source_health.get("heating")
    if source:
        source["ts"] = ts
        source["count"] += 1
    
    # Extract temperature values with fallback field names
    kessel = safe_float(data.get("Kesseltemperatur") or data.get("kesseltemp"))
    warmwasser = safe_float(
        data.get("Warmwasser") or 
        data.get("Warmwassertemperatur") or 
        data.get("warmwasser")
    )
    outdoor = safe_float(
        data.get("Außentemperatur") or 
        data.get("Aussentemperatur") or 
        data.get("outdoor")
    )
    top = safe_float(
        data.get("Pufferspeicher Oben") or 
        data.get("Puffer_Oben") or 
        data.get("puffer_top")
    )
    mid = safe_float(
        data.get("Pufferspeicher Mitte") or 
        data.get("Pufferspeicher_Mitte") or 
        data.get("puffer_mid")
    )
    bot = safe_float(
        data.get("Pufferspeicher Unten") or 
        data.get("Puffer_Unten") or 
        data.get("puffer_bot")
    )
    
    logging.info("[BMK] Extracted: kessel=%s warmwasser=%s outdoor=%s top=%s mid=%s bot=%s",
                 kessel, warmwasser, outdoor, top, mid, bot)
    
    betriebsmodus = format_bmk_mode(
        data.get("Betriebsmodus") or
        data.get("betriebsmodus") or
        data.get("Modus_Status") or
        data.get("Betriebsstatus")
    )
    
    # Push to app_state for reactive UI updates
    if app_state is not None:
        payload = {
            "timestamp": data.get("Zeitstempel") or data.get("timestamp"),
            "outdoor": outdoor,
            BMK_KESSEL_C: kessel,
            BMK_WARMWASSER_C: warmwasser,
            BMK_BETRIEBSMODUS: betriebsmodus,
            BUF_TOP_C: top,
            BUF_MID_C: mid,
            BUF_BOTTOM_C: bot,
        }
        payload = {k: v for k, v in payload.items() if v is not None}
        logging.info("[BMK] Pushing to app_state: %s", payload)
        app_state.update(payload)
    else:
        logging.warning("[BMK] app_state is None!")
