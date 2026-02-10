from __future__ import annotations

from typing import Iterable
from core.schema import (
    PV_POWER_KW,
    GRID_POWER_KW,
    BATTERY_POWER_KW,
    BATTERY_SOC_PCT,
    LOAD_POWER_KW,
    BMK_KESSEL_C,
    BMK_WARMWASSER_C,
    BUF_TOP_C,
    BUF_MID_C,
    BUF_BOTTOM_C,
)

OUTDOOR_C = "outdoor"
TIMESTAMP = "timestamp"

KNOWN_KEYS = {
    PV_POWER_KW,
    GRID_POWER_KW,
    BATTERY_POWER_KW,
    BATTERY_SOC_PCT,
    LOAD_POWER_KW,
    BMK_KESSEL_C,
    BMK_WARMWASSER_C,
    BUF_TOP_C,
    BUF_MID_C,
    BUF_BOTTOM_C,
}

OPTIONAL_KEYS = {TIMESTAMP, OUTDOOR_C}


def validate_payload(payload: dict) -> list[str]:
    """Validate payload keys, returning warnings instead of raising errors."""
    keys = set(payload.keys())
    extra = sorted(keys - KNOWN_KEYS - OPTIONAL_KEYS)
    warnings: list[str] = []
    if extra:
        warnings.append("Unexpected keys: " + ", ".join(extra))
    return warnings


def strip_none(payload: dict, allow_keys: Iterable[str] | None = None) -> dict:
    """Drop None values, optionally restricting to allowed keys."""
    if allow_keys is None:
        return {k: v for k, v in payload.items() if v is not None}
    allow = set(allow_keys)
    return {k: v for k, v in payload.items() if k in allow and v is not None}
