"""Centralized schema keys for all measurement values.

This module defines the standardized keys used throughout the application
for energy and heating metrics. Using these constants ensures consistency
and prevents typos in key names.

Key naming convention:
- Lowercase with underscores
- Unit suffix (_kw, _pct, _c) for clarity
- Prefix indicates source (pv_, bmk_, buf_)
"""

from typing import Final, Set

# PV/Inverter metrics
PV_POWER_KW: Final[str] = "pv_power_kw"
"""Current PV generation power in kilowatts."""

GRID_POWER_KW: Final[str] = "grid_power_kw"
"""Grid power in kW (positive = import, negative = export)."""

BATTERY_POWER_KW: Final[str] = "battery_power_kw"
"""Battery power in kW (positive = charging, negative = discharging)."""

BATTERY_SOC_PCT: Final[str] = "battery_soc_pct"
"""Battery state of charge in percent (0-100)."""

LOAD_POWER_KW: Final[str] = "load_power_kw"
"""Current household load in kilowatts."""

# BMK heating system metrics
BMK_KESSEL_C: Final[str] = "bmk_kessel_c"
"""Boiler (Kessel) temperature in Celsius."""

BMK_WARMWASSER_C: Final[str] = "bmk_warmwasser_c"
"""Hot water (Warmwasser) temperature in Celsius."""

BMK_BETRIEBSMODUS: Final[str] = "bmk_betriebsmodus"
"""BMK operating mode (string or numeric)."""

# Buffer storage temperatures
BUF_TOP_C: Final[str] = "buf_top_c"
"""Buffer storage top temperature in Celsius."""

BUF_MID_C: Final[str] = "buf_mid_c"
"""Buffer storage middle temperature in Celsius."""

BUF_BOTTOM_C: Final[str] = "buf_bottom_c"
"""Buffer storage bottom temperature in Celsius."""

# Complete set of all measurement keys
ALL_KEYS: Final[Set[str]] = {
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
}
