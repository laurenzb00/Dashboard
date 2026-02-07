# Normalizer für verschiedene Datenquellen
from .schema import *

def normalize_fronius(raw_json):
    """Normalisiert Fronius-API-JSON zu final keys."""
    def as_float(x, default=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default
    return {
        PV_POWER_KW: as_float(raw_json.get("P_PV")) / 1000.0,  # W -> kW
        GRID_POWER_KW: as_float(raw_json.get("P_Grid")) / 1000.0,
        LOAD_POWER_KW: as_float(raw_json.get("P_Load")) / 1000.0 if "P_Load" in raw_json else 0.0,
    }

def normalize_bmk(raw_values):
    """Normalisiert BMK-CSV/JSON zu final keys."""
    def as_float(x, default=0.0):
        try:
            return float(x)
        except (TypeError, ValueError):
            return default
    warmwasser = as_float(raw_values.get("Warmwasser") or raw_values.get("Warmwassertemperatur"))
    return {
        BMK_KESSEL_C: as_float(raw_values.get("Kessel") or raw_values.get("Kesseltemperatur")),
        BMK_WARMWASSER_C: warmwasser,
        # Legacy alias (avoid breaking older UI code that still reads BMK_BOILER_C)
        BMK_BOILER_C: warmwasser,
        BUF_TOP_C: as_float(raw_values.get("Puffer_Oben") or raw_values.get("Pufferspeicher Oben")),
        BUF_MID_C: as_float(raw_values.get("Puffer_Mitte") or raw_values.get("Pufferspeicher Mitte")),
        BUF_BOTTOM_C: as_float(raw_values.get("Puffer_Unten") or raw_values.get("Pufferspeicher Unten")),
    }
