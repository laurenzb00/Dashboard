"""Raumklima (Tado) - Datenmodell und Parser, unabhaengig von der Quelle.

Quellen:
* Home Assistant: climate.*-Entitaeten (Tado-Integration oder andere Thermostate).
  Kein eigener Tado-Login, keine zusaetzlichen Tado-API-Abfragen.
* Direkt (python-tado/PyTado): alle Zonen mit EINEM Aufruf (zoneStates).

Tado begrenzt die REST-API seit 27.01.2026: 100 Abfragen/Tag ohne Abo,
20.000 mit Auto-Assist/AI-Assist. Das Abfrage-Intervall wird aus dem Limit
berechnet (direct_poll_s), damit hoechstens ~70 % davon fuer Abfragen draufgehen.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DIRECT_POLL_MIN_S = 60


def direct_poll_s(daily_limit: int) -> int:
    """Abfrage-Intervall fuer den Direktbetrieb: 100/Tag -> ~20 min, 20.000/Tag -> 60 s."""
    budget = max(1, int(daily_limit * 0.7))
    return max(DIRECT_POLL_MIN_S, int(86400 / budget) + 1)
HA_POLL_S = 30
TARGET_MIN, TARGET_MAX = 5.0, 25.0


@dataclass
class Room:
    id: str                       # climate.entity_id (HA) oder Zonen-ID (direkt)
    name: str
    current: Optional[float] = None
    target: Optional[float] = None
    humidity: Optional[float] = None
    heating: bool = False
    power_pct: Optional[int] = None
    mode: str = "plan"            # "plan" | "manual" | "off"
    window_open: bool = False
    available: bool = True
    # --- Zeitplan-Kontext (None = Quelle liefert es nicht) -----------------
    presence: Optional[str] = None        # "home" | "away" (Tado-Anwesenheit/Geofencing)
    override: Optional[str] = None        # nur bei mode "manual": "next_block" | "timer" | "hold"
    override_end_ts: Optional[float] = None   # Ende des manuellen Eingriffs (Unix-Sek.), falls bekannt
    next_change_ts: Optional[float] = None    # naechste Planaenderung (Unix-Sek.)
    next_change_temp: Optional[float] = None  # Zieltemperatur danach (None + next_change_off = Aus)
    next_change_off: bool = False

    @property
    def mode_text(self) -> str:
        return {"plan": "Zeitplan", "manual": "Manuell", "off": "Aus"}.get(self.mode, self.mode)


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _iso_ts(v) -> Optional[float]:
    """ISO-8601-Zeitstempel (mit 'Z' oder Offset) -> Unix-Sekunden, sonst None."""
    if not v:
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _presence(v) -> Optional[str]:
    t = str(v or "").strip().lower()
    return t if t in ("home", "away") else None


def rooms_from_ha(states: List[Dict[str, Any]]) -> List[Room]:
    rooms = []
    for st in states:
        ent = str(st.get("entity_id") or "")
        if not ent.startswith("climate."):
            continue
        a = st.get("attributes") or {}
        state = str(st.get("state") or "").lower()
        hvac_mode = state
        mode = "off" if hvac_mode == "off" else ("plan" if hvac_mode == "auto" else "manual")
        action = str(a.get("hvac_action") or "").lower()
        power = a.get("heating_power")
        rooms.append(Room(
            id=ent,
            name=str(a.get("friendly_name") or ent.split(".", 1)[1].replace("_", " ").title()),
            current=_f(a.get("current_temperature")),
            target=_f(a.get("temperature")),
            humidity=_f(a.get("current_humidity")),
            heating=action == "heating",
            power_pct=int(power) if isinstance(power, (int, float)) else None,
            mode=mode,
            window_open=bool(a.get("open_window") or a.get("window_open")),
            available=state not in ("unavailable", "unknown"),
            presence=_presence(a.get("preset_mode")),   # Tado-HA: preset "home" / "away"
        ))
    rooms.sort(key=lambda r: r.name.lower())
    return rooms


def _get(d: Any, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def room_from_tado_state(zone_id: Any, name: str, state: Dict[str, Any]) -> Room:
    """Ein zoneState aus der Tado-API (python-tado/PyTado) -> Room. Fehlende Werte bleiben None."""
    current = state.get("current_temp")
    if current is None:
        current = _get(state, "sensorDataPoints", "insideTemperature", "celsius")
    humidity = state.get("current_humidity")
    if humidity is None:
        humidity = _get(state, "sensorDataPoints", "humidity", "percentage")
    overlay = state.get("overlay")
    setting = (overlay or {}).get("setting") or state.get("setting") or {}
    target = state.get("target_temp")
    if target is None:
        target = _get(setting, "temperature", "celsius")
    power_state = str(setting.get("power") or state.get("power") or "").upper()
    power_pct = state.get("heating_power_percentage")
    if power_pct is None:
        power_pct = _get(state, "activityDataPoints", "heatingPower", "percentage")
    manual = bool(overlay) or bool(state.get("overlay_active"))
    mode = "off" if power_state == "OFF" else ("manual" if manual else "plan")
    override, override_end = (_override_from_state(state, overlay) if manual else (None, None))
    next_ts, next_temp, next_off = _next_change_from_state(state)
    return Room(
        id=str(zone_id), name=name, current=_f(current), target=_f(target), humidity=_f(humidity),
        heating=bool(power_pct and float(power_pct) > 0), power_pct=int(power_pct) if power_pct is not None else None,
        mode=mode, window_open=bool(state.get("openWindow") or state.get("openWindowDetected")
                         or state.get("open_window") or state.get("open_window_detected")),
        presence=_presence(state.get("tadoMode") or state.get("tado_mode")),
        override=override, override_end_ts=override_end,
        next_change_ts=next_ts, next_change_temp=next_temp, next_change_off=next_off,
    )


def _override_from_state(state: Dict[str, Any], overlay: Any) -> tuple:
    """Art und Ende des manuellen Eingriffs: ('next_block'|'timer'|'hold'|None, Unix-Sek.|None).

    Tado-API: overlay.termination.type = MANUAL | TIMER | TADO_MODE; die App-Variante
    steht in typeSkillBasedApp (NEXT_TIME_BLOCK = "bis zum naechsten Zeitblock").
    PyTado-Objekt-Variante: overlay_termination_type / overlay_termination_timestamp.
    """
    term = (overlay or {}).get("termination") or {}
    raw = (term.get("typeSkillBasedApp") or term.get("type")
           or state.get("overlay_termination_type") or "")
    kind = {"NEXT_TIME_BLOCK": "next_block", "TADO_MODE": "next_block",
            "TIMER": "timer", "MANUAL": "hold"}.get(str(raw).upper())
    end = _iso_ts(term.get("expiry") or state.get("overlay_termination_timestamp"))
    if end is None:
        rem = _f(term.get("remainingTimeInSeconds"))
        if rem is not None:
            end = time.time() + rem
    if kind == "hold":
        end = None
    return kind, end


def _next_change_from_state(state: Dict[str, Any]) -> tuple:
    """(Zeitpunkt, Zieltemperatur, aus?) der naechsten Planaenderung - aus demselben zoneState."""
    nxt = state.get("nextScheduleChange") or state.get("next_schedule_change")
    if not isinstance(nxt, dict):
        return None, None, False
    ts = _iso_ts(nxt.get("start"))
    setting = nxt.get("setting") or {}
    if ts is None or not isinstance(setting, dict):
        return None, None, False
    if str(setting.get("power") or "").upper() == "OFF":
        return ts, None, True
    return ts, _f(_get(setting, "temperature", "celsius")), False


@dataclass
class Summary:
    total: int
    heating: int
    mean_current: Optional[float]
    manual: int
    windows_open: int

    @property
    def text(self) -> str:
        if not self.total:
            return "Keine Räume"
        parts = [f"{self.total} {'Raum' if self.total == 1 else 'Räume'}"]
        parts.append(f"{self.heating} heizt" if self.heating == 1 else f"{self.heating} heizen")
        if self.mean_current is not None:
            parts.append(f"Ø {self.mean_current:.1f} °C".replace(".", ","))
        if self.windows_open:
            parts.append(f"🪟 {self.windows_open} Fenster offen")
        return " · ".join(parts)


def summarize(rooms: List[Room]) -> Summary:
    cur = [r.current for r in rooms if r.current is not None]
    return Summary(
        total=len(rooms), heating=sum(1 for r in rooms if r.heating),
        mean_current=(sum(cur) / len(cur)) if cur else None,
        manual=sum(1 for r in rooms if r.mode == "manual"),
        windows_open=sum(1 for r in rooms if r.window_open),
    )


def fmt_temp(v: Optional[float], digits: int = 1) -> str:
    if v is None:
        return "--"
    txt = f"{v:.{digits}f}".replace(".", ",")
    return f"{txt} °C"
