"""Anwesenheit aus Home Assistant (person.*) fuer die Heizungssteuerung - reine Logik, ohne Netzwerk.

Zustaende
---------
* home        mindestens eine Person ist zu Hause (HA-Zustand "home") - oder noch nicht lange genug weg
* away        alle Personen waren laenger als `away_delay_s` nicht zu Hause und kommen nicht naeher
* approaching alle weg, aber die naechste Person ist innerhalb `approach_km` und kommt naeher
              (oder ist innerhalb `arrive_km` und entfernt sich nicht) -> Heizung soll wieder anlaufen
* unknown     keine verwertbare Angabe (HA nicht erreichbar, Tracker "unavailable") - wird wie "home"
              behandelt, damit ein HA-Ausfall nie das Haus auskuehlen laesst

Die Entfernung kommt aus latitude/longitude der person-Entitaet und der Zone `zone.home`.
"Naeher kommen" heisst: die Entfernung ist in den letzten Minuten um mindestens 300 m gesunken.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, List, Optional, Tuple

HOME, AWAY, APPROACHING, UNKNOWN = "home", "away", "approaching", "unknown"

AWAY_DELAY_S = 15 * 60          # so lange durchgehend nicht zu Hause, bevor "away" gilt (Entprellung)
ARRIVE_KM = 3.0                 # innerhalb davon gilt "kommt an", solange die Entfernung nicht waechst
APPROACH_RELEASE = 1.3          # Hysterese: erst jenseits approach_km * 1.3 zurueck auf "away"
CLOSER_KM = 0.3                 # so viel muss die Entfernung sinken, damit es als "naeher" zaehlt
TREND_MIN_AGE_S = 120           # Vergleichswert muss mindestens so alt sein
TREND_WINDOW_S = 600
MAX_ACCURACY_M = 1000.0         # ungenauere GPS-Positionen werden ignoriert

_BAD_STATES = ("", "unknown", "unavailable", "none")


@dataclass(frozen=True)
class PresenceState:
    mode: str = UNKNOWN
    distance_km: Optional[float] = None
    since_ts: float = 0.0               # seit wann dieser Zustand gilt
    away_for_s: float = 0.0             # so lange ist niemand mehr zu Hause (0 = jemand da)
    who: str = ""                       # Name der naechsten Person

    @property
    def is_away(self) -> bool:
        return self.mode == AWAY


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _num(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def home_from_zone(zone_state: Optional[dict]) -> Optional[Tuple[float, float]]:
    a = (zone_state or {}).get("attributes") or {}
    lat, lon = _num(a.get("latitude")), _num(a.get("longitude"))
    return (lat, lon) if lat is not None and lon is not None else None


class PresenceTracker:
    def __init__(self, away_delay_s: float = AWAY_DELAY_S) -> None:
        self.away_delay_s = away_delay_s
        self._state = PresenceState()
        self._non_home_since: Optional[float] = None
        self._hist: Deque[Tuple[float, float]] = deque()

    @property
    def state(self) -> PresenceState:
        return self._state

    def update(self, persons: Iterable[dict], home: Optional[Tuple[float, float]], now_ts: float,
               approach_km: float = 12.0, arrive_km: float = ARRIVE_KM) -> PresenceState:
        """persons: HA-Zustaende der person-Entitaeten ({"state","attributes",...})."""
        valid: List[dict] = []
        for p in persons or []:
            if isinstance(p, dict) and str(p.get("state") or "").strip().lower() not in _BAD_STATES:
                valid.append(p)
        if not valid:
            return self._state                       # keine Angabe: letzten Zustand behalten

        if any(str(p["state"]).strip().lower() == "home" for p in valid):
            self._non_home_since = None
            self._hist.clear()
            return self._set(HOME, now_ts, None, "")

        if self._non_home_since is None:
            self._non_home_since = now_ts
        dist, who = self._nearest(valid, home)
        if dist is not None:
            self._hist.append((now_ts, dist))
        while self._hist and now_ts - self._hist[0][0] > TREND_WINDOW_S:
            self._hist.popleft()

        away_for = now_ts - self._non_home_since
        if dist is not None:
            if self._state.mode == APPROACHING and dist <= approach_km * APPROACH_RELEASE:
                return self._set(APPROACHING, now_ts, dist, who, away_for)      # Hysterese
            change = self._distance_drop()           # >0: kommt naeher, <0: entfernt sich
            if change is not None:
                if dist <= approach_km and change >= CLOSER_KM:
                    return self._set(APPROACHING, now_ts, dist, who, away_for)
                if dist <= arrive_km and change >= -CLOSER_KM:
                    return self._set(APPROACHING, now_ts, dist, who, away_for)
        mode = AWAY if away_for >= self.away_delay_s else HOME
        return self._set(mode, now_ts, dist, who, away_for)

    # ---------------------------------------------------------------- intern --

    def _nearest(self, persons: List[dict], home) -> Tuple[Optional[float], str]:
        best: Tuple[Optional[float], str] = (None, "")
        if home is None:
            return best
        for p in persons:
            a = p.get("attributes") or {}
            lat, lon = _num(a.get("latitude")), _num(a.get("longitude"))
            acc = _num(a.get("gps_accuracy"))
            if lat is None or lon is None or (acc is not None and acc > MAX_ACCURACY_M):
                continue
            d = haversine_km(lat, lon, home[0], home[1])
            if best[0] is None or d < best[0]:
                best = (d, str(a.get("friendly_name") or p.get("entity_id") or ""))
        return best

    def _distance_drop(self) -> Optional[float]:
        """Wie viel naeher (km) als der aelteste brauchbare Wert der letzten Minuten; None = zu wenig Verlauf."""
        if len(self._hist) < 2:
            return None
        newest_ts, newest = self._hist[-1]
        old = next(((t, d) for t, d in self._hist if newest_ts - t >= TREND_MIN_AGE_S), None)
        return None if old is None else old[1] - newest

    def _set(self, mode: str, now_ts: float, dist: Optional[float], who: str, away_for: float = 0.0) -> PresenceState:
        since = self._state.since_ts if mode == self._state.mode else now_ts
        self._state = PresenceState(mode, dist, since, away_for, who)
        return self._state


def describe(state: PresenceState, now_ts: float, away_temp: Optional[float] = None) -> str:
    """Kurzer Klartext fuer die Statusleiste."""
    d = f" · {state.distance_km:.0f} km entfernt" if state.distance_km is not None else ""
    if state.mode == HOME:
        if state.away_for_s >= 60:
            return f"🏠 Niemand zu Hause seit {int(state.away_for_s // 60)} min – Absenkung in Kürze"
        return "🏠 Zu Hause"
    if state.mode == AWAY:
        t = f", Heizung auf {away_temp:.0f} °C" if away_temp is not None else ""
        return f"🚗 Abwesend{d}{t}"
    if state.mode == APPROACHING:
        return f"🚗 Kommt näher{d} – Heizung läuft wieder an"
    return "Anwesenheit unbekannt (Home Assistant)"
