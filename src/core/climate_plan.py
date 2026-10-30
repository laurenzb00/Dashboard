"""Tagesplan der Zieltemperatur und Statustext fuer den Thermo-Tab.

Reine Logik ohne Tk/Netzwerk - dadurch testbar.

* DayPlan: Tagesverlauf der Zieltemperatur als Segmente (Minuten ab 00:00),
  gelernt aus dem gespeicherten Verlauf (plan_from_history). Dadurch sieht man den
  Zeitplan (z. B. Nachtabsenkung) auch dann, wenn die Quelle ihn nicht herausgibt
  (Home Assistant) - und ohne zusaetzliche Tado-API-Abfragen.
* describe_status(): beantwortet "Wieso hat der Raum gerade diese Zieltemperatur?"
  - Zeitplan (Komfort/Absenkung), Manuell (bis wann), Timer, Abwesend, Fenster, Aus.
  Die naechste Aenderung kommt bevorzugt direkt von Tado (nextScheduleChange im
  zoneState), sonst aus dem gelernten Plan.

Vereinfachung: Der gelernte Plan gilt fuer den ganzen Tag gleich; beim Lernen werden
Werktage und Wochenende getrennt betrachtet, sobald genug Tage vorliegen.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple

from core.climate import Room, fmt_temp

DAY_MIN = 1440
SETBACK_DELTA = 1.0       # >= 1 K unter dem Komfortwert gilt als "Absenkung"

MATCH_TOL = 0.5          # so nah (°C) muss ein Tag am Median liegen, um zu "passen"
MIN_AGREEMENT = 0.8      # Anteil passender Zeitfenster, ab dem ein Plan als erkannt gilt

# (ts, current, target, heating) wie in core/climate_history.py
Point = Tuple[int, Optional[float], Optional[float], bool]


@dataclass(frozen=True)
class Segment:
    start: int                 # Minute des Tages, 0..1440
    end: int
    temp: Optional[float]      # None = Heizung aus


@dataclass
class DayPlan:
    segments: List[Segment]
    source: str = "history"    # woher der Plan stammt (derzeit: gelernt aus dem Verlauf)

    def __post_init__(self) -> None:
        self.segments = merge_segments(self.segments)

    # --- Abfragen ----------------------------------------------------------
    @property
    def comfort(self) -> Optional[float]:
        temps = [s.temp for s in self.segments if s.temp is not None]
        return max(temps) if temps else None

    @property
    def eco(self) -> Optional[float]:
        temps = [s.temp for s in self.segments if s.temp is not None]
        return min(temps) if temps else None

    def level(self, temp: Optional[float]) -> str:
        """'off' | 'eco' (Absenkung) | 'comfort'."""
        if temp is None:
            return "off"
        c = self.comfort
        return "eco" if c is not None and temp <= c - SETBACK_DELTA else "comfort"

    def at(self, minute: float) -> Optional[Segment]:
        for s in self.segments:
            if s.start <= minute < s.end:
                return s
        return None

    def next_change(self, minute: float) -> Optional[Tuple[float, Optional[float]]]:
        """(Minute ab heute 00:00 - kann >1440 sein, neue Temperatur) der naechsten Aenderung."""
        cur = self.at(minute)
        if cur is None or not self.segments:
            return None
        for day_off in (0, DAY_MIN):
            for s in self.segments:
                start = s.start + day_off
                if start > minute and s.temp != cur.temp:
                    return start, s.temp
        return None

    @property
    def has_change(self) -> bool:
        return len({s.temp for s in self.segments}) > 1


def merge_segments(segments: Iterable[Segment]) -> List[Segment]:
    """Sortieren, Luecken/Ueberlappungen bereinigen, gleiche Nachbarn verschmelzen."""
    segs = sorted((s for s in segments if s.end > s.start), key=lambda s: s.start)
    out: List[Segment] = []
    for s in segs:
        start = max(s.start, out[-1].end) if out else s.start
        if s.end <= start:
            continue
        if out and out[-1].temp == s.temp and out[-1].end >= start:
            out[-1] = Segment(out[-1].start, s.end, s.temp)
        else:
            out.append(Segment(start, s.end, s.temp))
    return out


# --------------------------------------------------------------------------
# Plan aus dem Verlauf lernen (Home-Assistant-Betrieb / Rueckfall)
# --------------------------------------------------------------------------

def plan_from_history(points: Sequence[Point], now_ts: float, days: int = 14, slot_min: int = 30,
                      min_days: int = 3) -> Optional[DayPlan]:
    """Typischer Tagesverlauf der Zieltemperatur aus den letzten Tagen (Median je Zeitfenster).

    * Werktage und Wochenende werden getrennt gelernt, sobald mindestens `min_days` Tage der
      Art von "heute" vorliegen - sonst zaehlen alle Tage.
    * Manuelle Eingriffe fallen heraus, solange sie nicht an der Mehrheit der Tage zur selben
      Uhrzeit passieren.
    * Tage mit durchgehend gleichem Soll (Abwesenheit, Frostschutz) zaehlen nicht mit.
    * Liefert None, wenn zu wenig Verlauf da ist (< min_days Tage oder > 25 % der Zeitfenster
      ohne Daten) oder die Tage nicht zusammenpassen (< 80 % der Zeitfenster innerhalb 0,5 °C).
    """
    n_slots = DAY_MIN // slot_min
    since = now_ts - days * 86400
    today = datetime.fromtimestamp(now_ts).date()
    by_day: dict = {}                                   # date -> {slot: [targets]}
    for ts, _cur, tgt, _h in points:
        if tgt is None or ts < since:
            continue
        dt = datetime.fromtimestamp(ts)
        if dt.date() == today:
            continue                                    # heute ist unvollstaendig (und evtl. manuell)
        slot = (dt.hour * 60 + dt.minute) // slot_min
        by_day.setdefault(dt.date(), {}).setdefault(slot, []).append(float(tgt))
    # Tage mit durchgehend gleichem Soll (Abwesenheit/Frostschutz/Dauer-Handeingriff) sagen nichts
    # ueber den Zeitplan - ausser alle Tage sind so.
    def _flat(slots: dict) -> bool:
        vals = [statistics.median(v) for v in slots.values()]
        return max(vals) - min(vals) < 1.0

    varied = {d: v for d, v in by_day.items() if not _flat(v)}
    pool = varied if len(varied) >= min_days else by_day
    same_kind = {d: v for d, v in pool.items() if (d.weekday() >= 5) == (today.weekday() >= 5)}
    use = same_kind if len(same_kind) >= min_days else pool
    if len(use) < min_days:
        return None
    med: List[Optional[float]] = []
    for slot in range(n_slots):
        day_vals = [statistics.median(v[slot]) for v in use.values() if slot in v]
        med.append(round(statistics.median(day_vals) * 2) / 2 if len(day_vals) >= min_days else None)
    if sum(1 for m in med if m is None) > n_slots * 0.25:
        return None
    # Stimmen die Tage ueberein? Sonst ist das kein Zeitplan, sondern Handbetrieb -> kein Plan.
    hits = total = 0
    for v in use.values():
        for slot, vals in v.items():
            if med[slot] is None:
                continue
            total += 1
            hits += abs(statistics.median(vals) - med[slot]) <= MATCH_TOL
    if total == 0 or hits / total < MIN_AGREEMENT:
        return None
    # Luecken fuellen: vorwaerts, am Anfang rueckwaerts
    last = next((m for m in med if m is not None), None)
    for i, m in enumerate(med):
        if m is None:
            med[i] = last
        else:
            last = m
    return DayPlan([Segment(i * slot_min, (i + 1) * slot_min, m) for i, m in enumerate(med)], "history")


def plan_spans(plan: DayPlan, t0: float, t1: float) -> List[Tuple[float, float, Optional[float]]]:
    """Segmente des (taeglich gleichen) Plans als (von_ts, bis_ts, temp), auf [t0, t1] beschnitten."""
    out: List[Tuple[float, float, Optional[float]]] = []
    day = datetime.fromtimestamp(t0).date() - timedelta(days=1)
    last = datetime.fromtimestamp(t1).date()
    while day <= last:
        for s in plan.segments:
            a = _at_minute(day, s.start)
            b = _at_minute(day, s.end)
            if b <= t0 or a >= t1:
                continue
            out.append((max(a, t0), min(b, t1), s.temp))
        day += timedelta(days=1)
    merged: List[Tuple[float, float, Optional[float]]] = []
    for a, b, temp in out:          # 22:00-24:00 und 00:00-05:30 mit gleicher Temperatur = ein Block
        if merged and merged[-1][2] == temp and abs(merged[-1][1] - a) < 1:
            merged[-1] = (merged[-1][0], b, temp)
        else:
            merged.append((a, b, temp))
    return merged


def _at_minute(day: date, minute: int) -> float:
    base = datetime.combine(day, dtime(0, 0))
    return (base + timedelta(minutes=minute)).timestamp()


# --------------------------------------------------------------------------
# Statustext
# --------------------------------------------------------------------------

@dataclass
class Status:
    kind: str       # "plan" | "manual" | "timer" | "away" | "window" | "off"
    title: str      # kurze Ueberschrift, z. B. "Zeitplan aktiv"
    detail: str     # eine Zeile: was gerade gilt und was als Naechstes kommt
    level: str      # Farbwahl: "comfort" | "eco" | "manual" | "away" | "window" | "off" | "plan"
    on_plan: bool   # True = der Zeitplan steuert gerade (nichts manuell uebersteuert)


def fmt_duration(seconds: float) -> str:
    m = max(0, int(round(seconds / 60)))
    if m < 60:
        return f"{m} min"
    h, rest = divmod(m, 60)
    return f"{h} h {rest:02d} min" if rest else f"{h} h"


def fmt_clock(ts: float, now_ts: float) -> str:
    """'14:35', 'morgen 06:00' oder 'Sa 06:00' - je nach Abstand."""
    dt, now = datetime.fromtimestamp(ts), datetime.fromtimestamp(now_ts)
    days = (dt.date() - now.date()).days
    clock = dt.strftime("%H:%M")
    if days <= 0:
        return clock
    if days == 1:
        return f"morgen {clock}"
    return f"{('Mo', 'Di', 'Mi', 'Do', 'Fr', 'Sa', 'So')[dt.weekday()]} {clock}"


def _temp(t: Optional[float]) -> str:
    """'17 °C' bzw. '17,5 °C'; None = 'aus'."""
    return "aus" if t is None else fmt_temp(t, 0 if t == int(t) else 1)


def _upcoming(room: Room, plan: Optional[DayPlan], now_ts: float) -> Optional[Tuple[float, Optional[float]]]:
    """Naechste Planaenderung (ts, temp): bevorzugt die Angabe von Tado, sonst aus dem Plan."""
    if room.next_change_ts and room.next_change_ts > now_ts:
        return room.next_change_ts, (None if room.next_change_off else room.next_change_temp)
    if plan is not None:
        now = datetime.fromtimestamp(now_ts)
        minute = now.hour * 60 + now.minute + now.second / 60
        nxt = plan.next_change(minute)
        if nxt:
            return _at_minute(now.date(), 0) + nxt[0] * 60, nxt[1]
    return None


def describe_status(room: Room, plan: Optional[DayPlan], now_ts: float) -> Status:
    """Warum gilt gerade diese Zieltemperatur - und was kommt als Naechstes?"""
    if not room.available:
        return Status("off", "Offline", "Keine Verbindung zum Thermostat", "off", False)
    if room.window_open:
        return Status("window", "Fenster offen", "Heizung pausiert, bis das Fenster wieder zu ist", "window", False)
    if room.mode == "off":
        return Status("off", "Heizung aus", "Der Raum wird nicht beheizt", "off", False)

    if room.mode == "manual":
        t = _temp(room.target)
        if room.override == "timer":
            left = f" noch {fmt_duration(room.override_end_ts - now_ts)}" if room.override_end_ts else ""
            until = f" (bis {fmt_clock(room.override_end_ts, now_ts)})" if room.override_end_ts else ""
            return Status("timer", "Timer läuft", f"{t}{left}{until} – danach wieder Zeitplan", "manual", False)
        if room.override == "hold":
            return Status("manual", "Manuell – dauerhaft",
                          f"{t} bleibt, bis du auf „Zeitplan“ zurückstellst", "manual", False)
        if room.override == "next_block" and room.override_end_ts:
            return Status("manual", "Manuell bis nächster Zeitblock",
                          f"{t} bis {fmt_clock(room.override_end_ts, now_ts)} – danach wieder Zeitplan", "manual", False)
        nxt = _upcoming(room, plan, now_ts)
        tail = f" – spätestens ab {fmt_clock(nxt[0], now_ts)} wieder Zeitplan" if nxt else ""
        return Status("manual", "Manuell", f"{t} statt Zeitplan{tail}", "manual", False)

    if room.presence == "away":
        t = f" auf {_temp(room.target)}" if room.target is not None else ""
        return Status("away", "Abwesend",
                      f"Niemand zu Hause – Tado hält den Raum{t}. Der Zeitplan ist pausiert, bis jemand zurückkommt.",
                      "away", False)

    # Zeitplan steuert
    level = plan.level(room.target) if plan else "plan"
    if level == "off":
        level = "plan"
    now_part = {"eco": "Absenkung", "comfort": "Komfort"}.get(level)
    if room.target is None:
        detail = "Zeitplan aktiv"
    elif now_part:
        detail = f"Jetzt {now_part} {_temp(room.target)}"
    else:
        detail = f"Ziel {_temp(room.target)}"
    nxt = _upcoming(room, plan, now_ts)
    if nxt:
        nts, ntemp = nxt
        nxt_lvl = ""
        if plan is not None and ntemp is not None:
            nxt_lvl = {"eco": "Absenkung ", "comfort": "Komfort "}.get(plan.level(ntemp), "")
        detail += f" · ab {fmt_clock(nts, now_ts)} {nxt_lvl}{_temp(ntemp)}"
    elif plan is not None and not plan.has_change:
        detail += " · den ganzen Tag gleich"
    return Status("plan", "Zeitplan aktiv", detail, level, True)
