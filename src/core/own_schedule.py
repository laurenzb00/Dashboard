"""Eigener Heizungs-Zeitplan fuer den Thermo-Tab (statt des Tado-Zeitplans aus der Cloud).

Reine Logik ohne Tk/Netzwerk - dadurch testbar.

Modell
------
Ein Wochenplan: je Wochentag eine Liste von Schaltpunkten "ab HH:MM gilt X °C". Der Zustand um
00:00 ergibt sich aus dem letzten Schaltpunkt des Vortags (so wie im Tado-Plan). Ein Tag ohne
Schaltpunkte setzt also den Vortag fort.

Durchsetzung (Enforcer)
-----------------------
Der Enforcer entscheidet bei jeder Abfrage (alle ~30 s), ob dem Thermostat etwas gesagt werden muss:

* Beim Start eines neuen Blocks wird die Zieltemperatur als Timer bis zum Ende des Blocks gesetzt
  (hoechstens 23 h). Faellt das Dashboard aus, faellt Tado nach Ablauf auf seinen eigenen Plan zurueck.
* Manuelle Eingriffe (Ring, Tado-App, Timer) werden bis zum Ende des laufenden Blocks respektiert.
* Faellt der Raum mitten im Block in Tados eigenen Plan zurueck (Timer abgelaufen, in der Tado-App
  "Zeitplan" gedrueckt) und weicht ab, wird nachgestellt - hoechstens alle 10 min und 24x pro Tag
  (Schutz, falls Tados Plan und dieser Plan sich bekaempfen).
* Offenes Fenster oder "Heizung aus": es wird nichts gesetzt.
* Anwesenheit (Home Assistant, core/presence.py): Sind alle Personen laenger weg ("away"), wird auf
  `away_temp` abgesenkt (Standard 5 °C = Tados Minimum, praktisch aus mit Frostschutz). Kommt jemand
  naeher ("approaching") oder ist zu Hause, geht es sofort zurueck auf den Wert des Plans. Ist die
  Anwesenheit nicht bekannt, gilt "zu Hause" (ein HA-Ausfall darf nie das Haus auskuehlen); dann greift
  ersatzweise Tados eigene Abwesenheitserkennung (`respect_away`).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from core.climate_plan import DayPlan, Segment

logger = logging.getLogger(__name__)

DAY_NAMES = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
TEMP_MIN, TEMP_MAX = 5.0, 25.0
STEP_MIN = 15                      # Raster der Uhrzeiten im Editor
TEMP_STEP = 0.5
MAX_SWITCHES_PER_DAY = 10
MAX_TIMER_S = 23 * 3600            # laenger darf ein Tado-Timer nicht laufen
MIN_TIMER_S = 120
REASSERT_GAP_S = 600
FAILED_RETRY_S = 120
MAX_APPLIES_PER_DAY = 24
TOLERANCE = 0.25                   # so nah gilt die Zieltemperatur als erreicht
LOOKAHEAD_DAYS = 9
AWAY_TEMP_MIN, AWAY_TEMP_MAX = TEMP_MIN, 18.0
APPROACH_KM_MIN, APPROACH_KM_MAX = 2.0, 60.0

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "config", "zeitplan.json")

# Schaltpunkt: [Minute des Tages 0..1439, Temperatur]
Switch = List[float]


@dataclass
class WeekSchedule:
    enabled: bool = False
    respect_away: bool = True                      # Rueckfall ohne HA-Anwesenheit: bei Tado-Abwesenheit nichts setzen
    days: List[List[Switch]] = field(default_factory=lambda: [[] for _ in range(7)])
    away_enabled: bool = True                      # bei Abwesenheit (Home Assistant) absenken
    away_temp: float = 5.0
    approach_km: float = 12.0                      # ab dieser Naehe (und naeherkommend) wieder aufheizen
    persons: List[str] = field(default_factory=list)   # leer = person.laurenz bzw. DASHBOARD_PRESENCE_ENTITY_ID

    def __post_init__(self) -> None:
        self.away_temp = max(AWAY_TEMP_MIN, min(AWAY_TEMP_MAX, round(float(self.away_temp) * 2) / 2))
        self.approach_km = max(APPROACH_KM_MIN, min(APPROACH_KM_MAX, round(float(self.approach_km))))
        self.persons = [str(p).strip() for p in (self.persons or []) if str(p).strip()]
        days = list(self.days)[:7]
        days += [[] for _ in range(7 - len(days))]
        self.days = [normalize_day(d) for d in days]

    def copy(self) -> "WeekSchedule":
        return WeekSchedule(self.enabled, self.respect_away, [[list(s) for s in d] for d in self.days],
                            self.away_enabled, self.away_temp, self.approach_km, list(self.persons))

    @property
    def usable(self) -> bool:
        return any(self.days)


# --------------------------------------------------------------------------
# Bereinigen / Editieren (Listen von [minute, temp])
# --------------------------------------------------------------------------

def clamp_temp(t: float) -> float:
    return max(TEMP_MIN, min(TEMP_MAX, round(float(t) / TEMP_STEP) * TEMP_STEP))


def normalize_day(entries: Sequence) -> List[Switch]:
    """Minuten 0..1439, Temperatur gerundet/begrenzt, nach Zeit sortiert, gleiche Zeit: der letzte gewinnt."""
    by_min: Dict[int, float] = {}
    for e in entries or []:
        try:
            m, t = int(round(float(e[0]))), float(e[1])
        except (TypeError, ValueError, IndexError):
            continue
        if 0 <= m < 1440:
            by_min[m] = clamp_temp(t)
    return [[m, by_min[m]] for m in sorted(by_min)][:MAX_SWITCHES_PER_DAY]


def add_switch(entries: List[Switch]) -> List[Switch]:
    """Neuen Schaltpunkt hinzufuegen: 1 h nach dem letzten (sonst 06:00), Temperatur des letzten."""
    out = [list(e) for e in entries]
    if len(out) >= MAX_SWITCHES_PER_DAY:
        return out
    taken = {int(e[0]) for e in out}
    minute = (int(out[-1][0]) + 60) if out else 6 * 60
    temp = out[-1][1] if out else 21.0
    for _ in range(1440 // STEP_MIN):
        if minute >= 1440:
            minute = 0
        if minute not in taken:
            break
        minute += STEP_MIN
    else:
        return out
    out.append([minute, temp])
    return normalize_day(out)


def remove_switch(entries: List[Switch], index: int) -> List[Switch]:
    out = [list(e) for e in entries]
    if 0 <= index < len(out):
        del out[index]
    return out


def shift_time(entries: List[Switch], index: int, delta_min: int) -> List[Switch]:
    """Uhrzeit eines Schaltpunkts verschieben; ueberspringt belegte Zeiten, bleibt im Tag (0:00..23:45)."""
    out = [list(e) for e in entries]
    if not 0 <= index < len(out) or not delta_min:
        return out
    taken = {int(e[0]) for i, e in enumerate(out) if i != index}
    step = STEP_MIN if delta_min > 0 else -STEP_MIN
    new = int(out[index][0])
    for _ in range(abs(delta_min) // STEP_MIN):
        cand = new + step
        while 0 <= cand < 1440 and cand in taken:
            cand += step
        if not 0 <= cand < 1440:
            break
        new = cand
    out[index][0] = new
    return normalize_day(out)


def shift_temp(entries: List[Switch], index: int, delta: float) -> List[Switch]:
    out = [list(e) for e in entries]
    if 0 <= index < len(out):
        out[index][1] = clamp_temp(out[index][1] + delta)
    return out


def copy_day(sched: WeekSchedule, src: int, targets: Sequence[int]) -> None:
    for d in targets:
        if d != src and 0 <= d < 7:
            sched.days[d] = [list(e) for e in sched.days[src]]


WORKDAYS = (0, 1, 2, 3, 4)
WEEKEND = (5, 6)


# --------------------------------------------------------------------------
# Zeitleiste: aktiver Block / naechste Aenderung
# --------------------------------------------------------------------------

def _at(d: date, minute: int) -> datetime:
    return datetime.combine(d, datetime.min.time()) + timedelta(minutes=int(minute))


def timeline(sched: WeekSchedule, now: datetime, back_days: int = LOOKAHEAD_DAYS,
             ahead_days: int = LOOKAHEAD_DAYS) -> List[Tuple[datetime, float]]:
    """Alle Schaltpunkte um `now` als (Start, Temperatur); aufeinanderfolgende gleiche Temperaturen verschmolzen."""
    pts: List[Tuple[datetime, float]] = []
    d0 = now.date() - timedelta(days=back_days)
    for i in range(back_days + ahead_days + 1):
        d = d0 + timedelta(days=i)
        for m, t in sched.days[d.weekday()]:
            pts.append((_at(d, m), float(t)))
    merged: List[Tuple[datetime, float]] = []
    for ts, t in pts:
        if merged and abs(merged[-1][1] - t) < 1e-9:
            continue
        merged.append((ts, t))
    return merged


def active_block(sched: WeekSchedule, now: datetime) -> Optional[Tuple[datetime, float]]:
    """(Start, Temperatur) des Blocks, der jetzt gilt."""
    if not sched.usable:
        return None
    cur = None
    for ts, t in timeline(sched, now):
        if ts <= now:
            cur = (ts, t)
        else:
            break
    return cur


def next_change(sched: WeekSchedule, now: datetime) -> Optional[Tuple[datetime, float]]:
    """(Zeitpunkt, neue Temperatur) der naechsten echten Aenderung."""
    if not sched.usable:
        return None
    for ts, t in timeline(sched, now):
        if ts > now:
            return ts, t
    return None


def target_at(sched: WeekSchedule, now: datetime) -> Optional[float]:
    blk = active_block(sched, now)
    return blk[1] if blk else None


def seconds_to_next_change(sched: WeekSchedule, now: datetime) -> Optional[int]:
    """Dauer bis zum Ende des Blocks fuer einen Timer (zwischen MIN_TIMER_S und MAX_TIMER_S)."""
    if not sched.usable:
        return None
    nxt = next_change(sched, now)
    secs = (nxt[0] - now).total_seconds() if nxt else MAX_TIMER_S
    return int(max(MIN_TIMER_S, min(MAX_TIMER_S, secs)))


def day_plan(sched: WeekSchedule, day: date) -> Optional[DayPlan]:
    """Tagesverlauf eines Datums als DayPlan (fuer Diagramm und Statustext)."""
    if not sched.usable:
        return None
    start = datetime.combine(day, datetime.min.time())
    tl = timeline(sched, start + timedelta(hours=12), back_days=LOOKAHEAD_DAYS, ahead_days=2)
    end = start + timedelta(days=1)
    segs: List[Segment] = []
    for i, (ts, t) in enumerate(tl):
        until = tl[i + 1][0] if i + 1 < len(tl) else end
        a, b = max(ts, start), min(until, end)
        if b > a:
            segs.append(Segment(int((a - start).total_seconds() // 60), int((b - start).total_seconds() // 60), t))
    return DayPlan(segs, "own") if segs else None


def from_day_plan(plan: Optional[DayPlan], enabled: bool = False) -> WeekSchedule:
    """Einen (gelernten) Tagesplan als Startwert fuer alle sieben Tage uebernehmen."""
    entries: List[Switch] = []
    if plan is not None:
        last = None
        for s in plan.segments:
            if s.temp is None or s.temp == last:
                continue
            entries.append([s.start, s.temp])
            last = s.temp
    return WeekSchedule(enabled=enabled, days=[[list(e) for e in entries] for _ in range(7)])


def default_schedule() -> WeekSchedule:
    """Sinnvoller Startplan, falls es noch nichts gibt: Komfort 06:00-22:00, sonst Absenkung."""
    day = [[6 * 60, 21.0], [22 * 60, 18.0]]
    return WeekSchedule(enabled=False, days=[[list(e) for e in day] for _ in range(7)])


def describe(sched: WeekSchedule, now: datetime) -> str:
    """'Jetzt 21 °C · ab 22:00 18 °C' bzw. 'Jetzt 21 °C · bleibt so'."""
    blk = active_block(sched, now)
    if blk is None:
        return "Kein Zeitplan angelegt"
    nxt = next_change(sched, now)
    txt = f"Jetzt {fmt_t(blk[1])}"
    if nxt:
        days = (nxt[0].date() - now.date()).days
        when = nxt[0].strftime("%H:%M") if days <= 0 else (
            f"morgen {nxt[0]:%H:%M}" if days == 1 else f"{DAY_NAMES[nxt[0].weekday()]} {nxt[0]:%H:%M}")
        txt += f" · ab {when} {fmt_t(nxt[1])}"
    else:
        txt += " · bleibt so"
    return txt


def fmt_t(t: Optional[float]) -> str:
    if t is None:
        return "--"
    return (f"{t:.0f}" if t == int(t) else f"{t:.1f}".replace(".", ",")) + " °C"


def fmt_minute(m: int) -> str:
    return f"{int(m) // 60:02d}:{int(m) % 60:02d}"


# --------------------------------------------------------------------------
# Speichern / Laden
# --------------------------------------------------------------------------

def to_json(sched: WeekSchedule) -> dict:
    return {"version": 2, "enabled": bool(sched.enabled), "respect_away": bool(sched.respect_away),
            "away": {"enabled": bool(sched.away_enabled), "temp": sched.away_temp, "approach_km": sched.approach_km,
                     "persons": list(sched.persons)},
            "days": {DAY_NAMES[i]: [[int(m), float(t)] for m, t in sched.days[i]] for i in range(7)}}


def from_json(data: dict) -> WeekSchedule:
    days_raw = (data or {}).get("days") or {}
    days = [days_raw.get(DAY_NAMES[i]) or days_raw.get(str(i)) or [] for i in range(7)]
    away = (data or {}).get("away") or {}
    try:
        return WeekSchedule(enabled=bool((data or {}).get("enabled", False)),
                            respect_away=bool((data or {}).get("respect_away", True)), days=days,
                            away_enabled=bool(away.get("enabled", True)), away_temp=away.get("temp", 5.0),
                            approach_km=away.get("approach_km", 12.0), persons=away.get("persons") or [])
    except (TypeError, ValueError):
        return WeekSchedule(enabled=bool((data or {}).get("enabled", False)), days=days)


def load(path: Optional[str] = None) -> WeekSchedule:
    """Gespeicherten Plan lesen; fehlt die Datei oder ist sie kaputt: Startplan (ausgeschaltet)."""
    path = path or CONFIG_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            return from_json(json.load(f))
    except FileNotFoundError:
        return default_schedule()
    except Exception as exc:
        logger.warning("[ZEITPLAN] %s nicht lesbar (%s) - Startplan", path, exc)
        return default_schedule()


def save(sched: WeekSchedule, path: Optional[str] = None) -> None:
    """Atomar schreiben (Temp-Datei + Umbenennen), damit ein Stromausfall keine halbe Datei hinterlaesst."""
    path = os.path.abspath(path or CONFIG_PATH)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".zeitplan_", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(to_json(sched), f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# Durchsetzung
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    room_id: str
    temp: float
    duration_s: int
    reason: str            # "blockbeginn" | "nachgestellt" | "abwesend" | "zurueck"
    key: float = 0.0       # Startzeit des Blocks (Unix-Sek.), zu dem die Aktion gehoert


@dataclass
class _RoomState:
    key: Optional[float] = None          # Startzeit des zuletzt behandelten Blocks
    last_try_ts: float = 0.0
    last_ok_ts: float = 0.0
    day: Optional[date] = None
    applies: int = 0
    warned: bool = False
    away_active: bool = False            # der Raum steht wegen Abwesenheit auf der Absenktemperatur


class Enforcer:
    """Entscheidet, wann dem Thermostat die Zieltemperatur des Plans gesagt werden muss."""

    def __init__(self) -> None:
        self._st: Dict[str, _RoomState] = {}

    def step(self, sched: WeekSchedule, room, now_ts: float, presence=None) -> Optional[Action]:
        """presence: core.presence.PresenceState oder None (= unbekannt, dann zaehlt Tados Abwesenheit)."""
        if not sched.enabled or not sched.usable:
            return None
        st = self._st.setdefault(room.id, _RoomState())
        if not room.available or room.window_open:
            return None
        if room.mode == "off":                       # bewusst ausgeschaltet: nicht anfassen
            st.away_active = False
            return None
        today = datetime.fromtimestamp(now_ts).date()
        if st.day != today:
            st.day, st.applies, st.warned = today, 0, False
        mode = getattr(presence, "mode", "unknown") if sched.away_enabled else "unknown"
        known = mode in ("home", "away", "approaching")
        if not known and sched.respect_away and room.presence == "away":
            return None
        if known and mode == "away":
            return self._away_step(sched, room, st, now_ts)
        if st.away_active:
            return self._return_step(sched, room, st, now_ts)
        now = datetime.fromtimestamp(now_ts)
        blk = active_block(sched, now)
        if blk is None:
            return None
        start, temp = blk
        key = start.timestamp()
        first = st.key is None
        new_block = st.key != key
        at_target = room.target is not None and abs(room.target - temp) < TOLERANCE
        duration = seconds_to_next_change(sched, now) or MAX_TIMER_S
        gap = FAILED_RETRY_S if st.last_try_ts > st.last_ok_ts else REASSERT_GAP_S

        if new_block:
            if first and room.mode == "manual":
                st.key = key                    # Neustart waehrend eines Eingriffs: nicht ueberschreiben
                return None
            if room.mode == "manual" and room.override == "timer" and room.override_end_ts \
                    and room.override_end_ts > now_ts:
                st.key = key                    # laufender Timer: danach greift das Nachstellen unten
                return None
            if room.mode == "plan" and at_target:
                st.key = key
                return None
            if now_ts - st.last_try_ts < FAILED_RETRY_S or not self._allowed(st):
                return None
            return Action(room.id, temp, duration, "blockbeginn", key)

        if room.mode == "plan" and not at_target:
            if now_ts - st.last_try_ts < gap or not self._allowed(st):
                return None
            return Action(room.id, temp, duration, "nachgestellt", key)
        return None

    def _away_step(self, sched: WeekSchedule, room, st: _RoomState, now_ts: float) -> Optional[Action]:
        temp = sched.away_temp
        at_away = room.target is not None and abs(room.target - temp) < TOLERANCE
        if not st.away_active:
            if at_away:
                st.away_active = True                # steht schon auf der Absenkung (z. B. nach Neustart)
                return None
            if now_ts - st.last_try_ts < FAILED_RETRY_S or not self._allowed(st):
                return None
            return Action(room.id, temp, MAX_TIMER_S, "abwesend", 0.0)
        if room.mode == "plan" and not at_away:      # Tados Plan hat uebernommen (Timer ab) -> nachstellen
            if now_ts - st.last_try_ts < REASSERT_GAP_S or not self._allowed(st):
                return None
            return Action(room.id, temp, MAX_TIMER_S, "abwesend", 0.0)
        return None

    def _return_step(self, sched: WeekSchedule, room, st: _RoomState, now_ts: float) -> Optional[Action]:
        """Jemand ist wieder da oder kommt naeher: zurueck auf den Wert des Plans - auch gegen manuelle Absenkung."""
        now = datetime.fromtimestamp(now_ts)
        blk = active_block(sched, now)
        if blk is None:
            return None
        start, temp = blk
        at_target = room.target is not None and abs(room.target - temp) < TOLERANCE
        if room.mode == "plan" and at_target:
            st.away_active, st.key = False, start.timestamp()
            return None
        if now_ts - st.last_try_ts < FAILED_RETRY_S:
            return None
        return Action(room.id, temp, seconds_to_next_change(sched, now) or MAX_TIMER_S, "zurueck", start.timestamp())

    def _allowed(self, st: _RoomState) -> bool:
        if st.applies >= MAX_APPLIES_PER_DAY:
            if not st.warned:
                st.warned = True
                logger.warning("[ZEITPLAN] %d Eingriffe heute - Tados eigener Plan scheint gegenzusteuern; "
                               "pausiert bis morgen. Tado-Zeitplan in der Tado-App abflachen.", st.applies)
            return False
        return True

    def record(self, action: Action, now_ts: float, ok: bool) -> None:
        """Ergebnis melden. Nur bei Erfolg gilt der Block als erledigt."""
        st = self._st.setdefault(action.room_id, _RoomState())
        st.last_try_ts = now_ts
        if ok:
            st.last_ok_ts = now_ts
            st.applies += 1
            if action.reason == "abwesend":
                st.away_active = True
            elif action.reason == "zurueck":
                st.away_active = False
                st.key = action.key
            else:
                st.key = action.key
