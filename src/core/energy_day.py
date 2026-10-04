"""Auswertung eines einzelnen Tages: Energiebilanz, Autarkie, Akkuverlauf.

Reine Logik ohne UI - wird vom Ertrag-Tab (Ansicht "Tag") verwendet.
Vorzeichen wie von Fronius geliefert und im Datastore gespeichert:
  grid_power > 0 = Netzbezug, < 0 = Einspeisung
  load_power  = Hausverbrauch (Fronius liefert ihn negativ -> Betrag)
  batt_power > 0 = Akku entlaedt
Fehlt load_power (aeltere Datensaetze), wird der Verbrauch aus der Bilanz
PV + Netz + Akku berechnet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

from .pv_forecast import day_bounds_utc
from .time_utils import DB_TS_FORMAT, db_ts_to_local

# Unterhalb dieses Mindest-Ladestands (aus der Historie) gilt der Akku nur
# dann als "leer", wenn das Minimum selbst niedrig ist - sonst wuerde ein
# Akku, der nie unter 35 % faellt, bei 35 % faelschlich als leer gelten.
EMPTY_FLOOR_MAX_PCT = 20.0
FULL_PCT = 99.0


@dataclass
class Sample:
    ts: datetime          # lokale Zeit, naiv
    pv: float             # kW
    load: Optional[float]
    grid: Optional[float]
    soc: Optional[float]


@dataclass
class DaySummary:
    pv_kwh: float = 0.0
    load_kwh: float = 0.0
    import_kwh: float = 0.0
    export_kwh: float = 0.0
    soc_min: Optional[float] = None
    soc_max: Optional[float] = None
    full_at: Optional[datetime] = None
    empty_spans: list[tuple[datetime, datetime]] = field(default_factory=list)
    samples: int = 0

    @property
    def autarky_pct(self) -> Optional[float]:
        if self.load_kwh < 0.1:
            return None
        return max(0.0, min(100.0, (1.0 - self.import_kwh / self.load_kwh) * 100.0))

    @property
    def self_consumption_pct(self) -> Optional[float]:
        if self.pv_kwh < 0.1:
            return None
        return max(0.0, min(100.0, (self.pv_kwh - self.export_kwh) / self.pv_kwh * 100.0))

    @property
    def empty_at(self) -> Optional[datetime]:
        """Wann der Akku (zuletzt) leer wurde."""
        return self.empty_spans[-1][0] if self.empty_spans else None

    @property
    def empty_minutes(self) -> float:
        return sum((b - a).total_seconds() for a, b in self.empty_spans) / 60.0


def _kw(value) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if abs(v) > 200.0:     # alte Datensaetze in W
        v /= 1000.0
    return v


def load_day_samples(store, day: date) -> list[Sample]:
    conn = getattr(store, "conn", None)
    if conn is None:
        return []
    start, end = day_bounds_utc(day)
    rows = conn.execute(
        "SELECT timestamp, pv_power, load_power, grid_power, batt_power, soc FROM fronius "
        "WHERE timestamp >= ? AND timestamp < ? ORDER BY timestamp ASC",
        (start.strftime(DB_TS_FORMAT), end.strftime(DB_TS_FORMAT)),
    ).fetchall()
    out: list[Sample] = []
    for ts, pv, load, grid, batt, soc in rows:
        dt = db_ts_to_local(ts)
        if dt is None:
            continue
        pv_kw = _kw(pv)
        load_kw = _kw(load)
        grid_kw = _kw(grid)
        batt_kw = _kw(batt)
        if load_kw is None and grid_kw is not None:
            load_kw = (pv_kw or 0.0) + grid_kw + (batt_kw or 0.0)
        soc_v = None
        try:
            soc_v = float(soc) if soc is not None else None
        except (TypeError, ValueError):
            soc_v = None
        if soc_v is not None and soc_v <= 0.0:
            soc_v = None  # 0 % = Messausfall (Wechselrichter im Standby)
        out.append(Sample(
            ts=dt,
            pv=max(0.0, pv_kw or 0.0),
            load=abs(load_kw) if load_kw is not None else None,
            grid=grid_kw,
            soc=soc_v,
        ))
    return out


def soc_floor(store, days: int = 60) -> Optional[float]:
    """Niedrigster Ladestand der letzten Tage (= Entladegrenze des Akkus)."""
    conn = getattr(store, "conn", None)
    if conn is None:
        return None
    from .time_utils import db_cutoff
    row = conn.execute(
        "SELECT MIN(soc) FROM fronius WHERE timestamp >= ? AND soc > 0",  # 0 = Messausfall
        (db_cutoff(days=days),),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def _max_gap_seconds(samples: list[Sample]) -> float:
    if len(samples) < 3:
        return 3600.0
    deltas = sorted((b.ts - a.ts).total_seconds() for a, b in zip(samples, samples[1:]))
    typical = deltas[len(deltas) // 2]
    return max(15 * 60.0, typical * 3.0)


def summarize(samples: list[Sample], floor: Optional[float] = None) -> DaySummary:
    s = DaySummary(samples=len(samples))
    if not samples:
        return s
    max_gap = _max_gap_seconds(samples)
    for a, b in zip(samples, samples[1:]):
        dt_h = (b.ts - a.ts).total_seconds() / 3600.0
        if dt_h <= 0 or dt_h * 3600.0 > max_gap:
            continue
        s.pv_kwh += (a.pv + b.pv) / 2.0 * dt_h
        if a.load is not None and b.load is not None:
            s.load_kwh += (a.load + b.load) / 2.0 * dt_h
        if a.grid is not None and b.grid is not None:
            g = (a.grid + b.grid) / 2.0
            if g > 0:
                s.import_kwh += g * dt_h
            else:
                s.export_kwh += -g * dt_h

    socs = [x for x in samples if x.soc is not None]
    if socs:
        s.soc_min = min(x.soc for x in socs)
        s.soc_max = max(x.soc for x in socs)
        full = next((x for x in socs if x.soc >= FULL_PCT), None)
        s.full_at = full.ts if full else None
        if floor is not None and floor <= EMPTY_FLOOR_MAX_PCT:
            threshold = floor + 1.0
            span_start = None
            prev = None
            for x in socs:
                gap = prev is not None and (x.ts - prev.ts).total_seconds() > max_gap
                if span_start is not None and (x.soc > threshold or gap):
                    s.empty_spans.append((span_start, prev.ts if gap else x.ts))
                    span_start = None
                if span_start is None and x.soc <= threshold:
                    span_start = x.ts
                prev = x
            if span_start is not None:
                s.empty_spans.append((span_start, prev.ts))
            # Flackern um die Grenze (z.B. 5 -> 6 -> 5 %) zu einem Block zusammenfassen
            merged: list[tuple[datetime, datetime]] = []
            for a, b in s.empty_spans:
                if merged and (a - merged[-1][1]) <= timedelta(minutes=10):
                    merged[-1] = (merged[-1][0], b)
                else:
                    merged.append((a, b))
            s.empty_spans = [(a, b) for a, b in merged if (b - a) >= timedelta(minutes=5)]
    return s


def bin_samples(samples: list[Sample], minutes: int = 5) -> list[dict]:
    """Mittelwerte je Zeitfenster fuer den Chart (Schema von build_energy_chart + soc)."""
    buckets: dict[datetime, list[Sample]] = {}
    for x in samples:
        key = x.ts.replace(minute=x.ts.minute - x.ts.minute % minutes, second=0, microsecond=0)
        buckets.setdefault(key, []).append(x)

    def _mean(vals):
        vals = [v for v in vals if v is not None]
        return sum(vals) / len(vals) if vals else None

    out = []
    for key in sorted(buckets):
        grp = buckets[key]
        mid = key + timedelta(minutes=minutes / 2.0)
        out.append({
            "timestamp": mid,
            "pv_power": _mean([g.pv for g in grp]) or 0.0,
            "house_consumption": _mean([g.load for g in grp]) or 0.0,
            "grid_power": _mean([g.grid for g in grp]) or 0.0,
            "soc": _mean([g.soc for g in grp]),
        })
    return out
