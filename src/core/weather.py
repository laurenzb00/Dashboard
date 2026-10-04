"""Einfacher Wetter-Vorhersage-Client (Open-Meteo, kein API-Key noetig).

Liefert nur die kurze Kopfzeilen-Vorhersage (Icon + Tageshoch/-tief) neben
der ueber die eigene Heizungs-Sensorik gemessenen Aussentemperatur - siehe
ui/components/header.py:update_forecast() / MainApp._refresh_weather_async().

Folgt demselben Config-Datei-Muster wie core/homeassistant.py/BMKDATEN.py:
config/weather.json (mit config/weather.example.json als Vorlage) statt
hartcodierter Werte. Anders als die anderen Integrationen braucht Open-Meteo
keinen API-Key/Token - nur die Koordinaten des Hauses, damit die Vorhersage
zum eigenen Standort passt (Default: Raum Braunau am Inn - in config/
weather.json anpassen, falls das nicht stimmt).
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "weather.json")

# Fallback-Koordinaten (Raum Braunau am Inn), falls config/weather.json fehlt
# oder nicht lesbar ist.
_DEFAULT_LAT = 48.2569
_DEFAULT_LON = 13.0397

# WMO-Wettercode -> Emoji, siehe https://open-meteo.com/en/docs (Feld
# "weather_code"). Nur grobe Kategorien, keine vollstaendige Zuordnung -
# fuer eine einzeilige Kopfzeilen-Vorhersage reicht das.
_WMO_ICON = {
    0: "☀️", 1: "🌤️", 2: "⛅", 3: "☁️",
    45: "🌫️", 48: "🌫️",
    51: "🌦️", 53: "🌦️", 55: "🌦️",
    56: "🌧️", 57: "🌧️",
    61: "🌧️", 63: "🌧️", 65: "🌧️",
    66: "🌨️", 67: "🌨️",
    71: "🌨️", 73: "🌨️", 75: "🌨️", 77: "🌨️",
    80: "🌦️", 81: "🌧️", 82: "⛈️",
    85: "🌨️", 86: "🌨️",
    95: "⛈️", 96: "⛈️", 99: "⛈️",
}


def _wmo_icon(code: Optional[int]) -> str:
    if code is None:
        return ""
    try:
        return _WMO_ICON.get(int(code), "")
    except Exception:
        return ""


@dataclass(frozen=True)
class WeatherConfig:
    latitude: float = _DEFAULT_LAT
    longitude: float = _DEFAULT_LON
    enabled: bool = True
    timeout_s: float = 6.0


def load_weather_config() -> WeatherConfig:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return WeatherConfig(
            latitude=float(data.get("latitude", _DEFAULT_LAT)),
            longitude=float(data.get("longitude", _DEFAULT_LON)),
            enabled=bool(data.get("enabled", True)),
            timeout_s=float(data.get("timeout_s", 6.0)),
        )
    except Exception as exc:
        logger.debug("Konnte config/weather.json nicht laden, nutze Standardwerte: %s", exc)
        return WeatherConfig()


_session = requests.Session()


def fetch_forecast(config: Optional[WeatherConfig] = None) -> Optional[dict]:
    """Holt die aktuelle Kurzvorhersage von Open-Meteo.

    Gibt bei JEDEM Fehler (kein Internet, Timeout, ungueltige Antwort,
    deaktiviert in der Config) None zurueck statt eine Exception zu werfen -
    der Aufrufer soll bei fehlendem Netz einfach die Vorhersage weglassen,
    nicht abstuerzen (dieselbe Fehlertoleranz wie bei den anderen externen
    Clients im Projekt, z.B. BMKDATEN.py/homeassistant.py).
    """
    cfg = config or load_weather_config()
    if not cfg.enabled:
        return None
    try:
        resp = _session.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": cfg.latitude,
                "longitude": cfg.longitude,
                "current": "temperature_2m,weather_code",
                "daily": "temperature_2m_max,temperature_2m_min,weather_code",
                "timezone": "auto",
                "forecast_days": 1,
            },
            timeout=cfg.timeout_s,
        )
        resp.raise_for_status()
        data = resp.json()
        current = data.get("current", {}) or {}
        daily = data.get("daily", {}) or {}
        code = current.get("weather_code")
        daily_max = daily.get("temperature_2m_max") or []
        daily_min = daily.get("temperature_2m_min") or []
        return {
            "temp_now": current.get("temperature_2m"),
            "code": code,
            "icon": _wmo_icon(code),
            "temp_max": daily_max[0] if daily_max else None,
            "temp_min": daily_min[0] if daily_min else None,
        }
    except Exception as exc:
        logger.debug("Wetter-Abruf fehlgeschlagen: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Stuendliche Aussentemperatur (fuer das Waermebedarfs-Modell)
# ---------------------------------------------------------------------------

_TEMP_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "temperature_cache.json")
_TEMP_CACHE_MAX_AGE_S = 3600


def fetch_hourly_temperature(config: Optional[WeatherConfig] = None, past_days: int = 60,
                             forecast_days: int = 7, allow_network: bool = True) -> dict:
    """Stuendliche Lufttemperatur {Stunde (UTC, aware): °C} - Vergangenheit und Prognose.

    Gecacht in data/temperature_cache.json (1 h). Bei Fehlern wird der Cache
    verwendet; ohne Cache ein leeres dict.
    """
    import time
    from datetime import datetime, timezone

    cfg = config or load_weather_config()
    cache = {}
    try:
        with open(_TEMP_CACHE_PATH, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    values = dict(cache.get("values") or {})
    fresh = (time.time() - float(cache.get("fetched_at", 0) or 0)) < _TEMP_CACHE_MAX_AGE_S
    if allow_network and cfg.enabled and not fresh:
        try:
            resp = _session.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": cfg.latitude,
                    "longitude": cfg.longitude,
                    "hourly": "temperature_2m",
                    "past_days": max(0, min(92, int(past_days))),
                    "forecast_days": max(1, min(16, int(forecast_days))),
                    "timezone": "UTC",
                },
                timeout=cfg.timeout_s,
            )
            resp.raise_for_status()
            hourly = (resp.json() or {}).get("hourly") or {}
            for t, v in zip(hourly.get("time") or [], hourly.get("temperature_2m") or []):
                if v is not None:
                    values[str(t)] = float(v)
            # nur ~1 Jahr behalten
            keys = sorted(values)
            values = {k: values[k] for k in keys[-24 * 400:]}
            os.makedirs(os.path.dirname(_TEMP_CACHE_PATH), exist_ok=True)
            tmp = _TEMP_CACHE_PATH + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"fetched_at": time.time(), "values": values}, f)
            os.replace(tmp, _TEMP_CACHE_PATH)
        except Exception as exc:
            logger.debug("Temperatur-Abruf fehlgeschlagen, nutze Cache: %s", exc)
    out = {}
    for k, v in values.items():
        try:
            out[datetime.fromisoformat(k).replace(tzinfo=timezone.utc)] = float(v)
        except ValueError:
            continue
    return out
