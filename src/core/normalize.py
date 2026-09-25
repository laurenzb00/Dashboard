"""Data normalizers for various energy and heating data sources.

This module provides functions to normalize raw API/CSV data into
standardized schema keys for consistent storage and display.
"""

from __future__ import annotations

from typing import Any

from .schema import (
    PV_POWER_KW,
    GRID_POWER_KW,
    LOAD_POWER_KW,
    BMK_KESSEL_C,
    BMK_WARMWASSER_C,
    BUF_TOP_C,
    BUF_MID_C,
    BUF_BOTTOM_C,
)


def _as_float(value: Any, default: float = 0.0) -> float:
    """Safely convert a value to float.
    
    Args:
        value: Any value to convert.
        default: Value to return on conversion failure.
        
    Returns:
        Float value or default.
    """
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _first_present(raw_values: dict[str, Any], *keys: str) -> Any:
    """Return the value of the first key that is actually present (not None).

    Vorher wurde hier `raw_values.get(a) or raw_values.get(b)` verwendet -
    das behandelt einen echten Messwert von 0/0.0 (z.B. ein unkonfigurierter
    Puffer-Sensor oder eine reale 0-Grad-Ablesung) als "falsy" und ersetzt
    ihn faelschlich durch den Fallback-Key/Default, obwohl der Primaerwert
    da war. `is not None` prueft stattdessen wirklich auf Anwesenheit.
    """
    for key in keys:
        value = raw_values.get(key)
        if value is not None:
            return value
    return None


def normalize_fronius(raw_json: dict[str, Any]) -> dict[str, float]:
    """Normalize Fronius inverter API JSON to schema keys.
    
    Converts power values from Watts to kilowatts.
    
    Args:
        raw_json: Raw JSON response from Fronius API.
        
    Returns:
        Dictionary with normalized schema keys and kW values.
        
    Example:
        >>> data = {"P_PV": 5000.0, "P_Grid": -1000.0, "P_Load": 2000.0}
        >>> normalize_fronius(data)
        {'pv_power_kw': 5.0, 'grid_power_kw': -1.0, 'load_power_kw': 2.0}
    """
    return {
        PV_POWER_KW: _as_float(raw_json.get("P_PV")) / 1000.0,  # W -> kW
        GRID_POWER_KW: _as_float(raw_json.get("P_Grid")) / 1000.0,
        LOAD_POWER_KW: _as_float(raw_json.get("P_Load")) / 1000.0 if "P_Load" in raw_json else 0.0,
    }


def normalize_bmk(raw_values: dict[str, Any]) -> dict[str, float]:
    """Normalize BMK heating system data to schema keys.
    
    Handles various field name formats from BMK CSV/JSON exports.
    
    Args:
        raw_values: Raw data dictionary from BMK system.
        
    Returns:
        Dictionary with normalized schema keys and temperature values in Celsius.
        
    Example:
        >>> data = {"Kesseltemperatur": 45.5, "Warmwasser": 55.0}
        >>> normalize_bmk(data)
        {'bmk_kessel_c': 45.5, 'bmk_warmwasser_c': 55.0, ...}
    """
    warmwasser = _as_float(
        _first_present(raw_values, "Warmwasser", "Warmwassertemperatur")
    )
    return {
        BMK_KESSEL_C: _as_float(
            _first_present(raw_values, "Kessel", "Kesseltemperatur")
        ),
        BMK_WARMWASSER_C: warmwasser,
        BUF_TOP_C: _as_float(
            _first_present(raw_values, "Puffer_Oben", "Pufferspeicher Oben")
        ),
        BUF_MID_C: _as_float(
            _first_present(raw_values, "Puffer_Mitte", "Pufferspeicher Mitte")
        ),
        BUF_BOTTOM_C: _as_float(
            _first_present(raw_values, "Puffer_Unten", "Pufferspeicher Unten")
        ),
    }
