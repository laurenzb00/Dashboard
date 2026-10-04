"""Waermebedarfs-Modell: Verbrauch der Speicher abhaengig von der Aussentemperatur.

    Verbrauch (kW) = Grundlast + Faktor * max(0, 18 °C - Aussentemperatur)

* Grundlast  = Warmwasser, Speicher-/Leitungsverluste, Heizen auch bei milder
               Witterung (kW)
* Faktor     = zusaetzlicher Bedarf pro Grad unter 18 °C (kW/K)

Gelernt wird aus der Historie (Standard: 60 Tage) - aber nur aus ruhigen
Stunden: Kessel aus (inkl. Nachlauf), kein Temperaturanstieg (keine Sonne).
In diesen Stunden ist die Abkuehlung der Speicher = Waermeverbrauch.
Je laenger das Dashboard laeuft und je unterschiedlicher die Temperaturen
waren, desto genauer wird das Modell. Es wird einmal taeglich neu gelernt
(data/heat_demand_model.json).

Damit lassen sich Bedarf und Einheiz-Haeufigkeit fuer die naechsten Tage aus
der Temperaturprognose abschaetzen.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import numpy as np

from . import heating_stats as hs

BASE_TEMP_C = 18.0
FIT_DAYS = 60
MIN_HOURS = 36
MIN_TEMP_SPREAD_K = 3.0
MIN_QUIET_MIN_PER_HOUR = 30
MODEL_MAX_AGE_S = 24 * 3600
GAIN_EPS_KWH = 0.05
_MODEL_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "heat_demand_model.json")


@dataclass
class DemandModel:
    base_kw: float
    per_k_kw: float
    hours: int
    r2: Optional[float]
    t_min: Optional[float]
    t_max: Optional[float]
    fitted_at: str = ""
    temperature_dependent: bool = True
    mean_deficit_k: float = 0.0      # mittleres (18 °C - Aussen) im Lernzeitraum, fuer fehlende Temperaturen

    def kw_at(self, outdoor_c: Optional[float]) -> float:
        deficit = self.mean_deficit_k if outdoor_c is None else max(0.0, BASE_TEMP_C - outdoor_c)
        return self.base_kw + self.per_k_kw * deficit

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @staticmethod
    def from_dict(d: dict) -> "DemandModel":
        return DemandModel(**{k: d.get(k) for k in DemandModel.__dataclass_fields__})


def quiet_hours(buckets: list[hs.Bucket], cfg: hs.StorageConfig) -> list[tuple[datetime, float, Optional[float]]]:
    """(Stunde lokal, Verbrauch kW, Aussentemperatur BMK) fuer Stunden mit genug ruhiger Zeit."""
    acc: dict[datetime, list[float]] = {}   # stunde -> [kWh, minuten, t_sum, t_n]
    prev_q = prev_b = None
    active_until = None
    for b in buckets:
        if hs.kessel_active(b):
            active_until = b.ts + timedelta(minutes=hs.BUCKET_MIN + hs.AFTERGLOW_MIN)
        q = hs.heat_content_kwh(b, cfg)
        if q is None:
            continue
        if prev_q is not None:
            minutes = (b.ts - prev_b.ts).total_seconds() / 60.0
            busy = active_until is not None and b.ts < active_until
            d = q - prev_q
            if 0 < minutes <= hs.MAX_GAP_MIN and not busy and d <= GAIN_EPS_KWH:
                hour = b.ts.replace(minute=0, second=0, microsecond=0)
                a = acc.setdefault(hour, [0.0, 0.0, 0.0, 0])
                a[0] += -d
                a[1] += minutes
                if b.outdoor is not None:
                    a[2] += b.outdoor
                    a[3] += 1
        prev_q, prev_b = q, b
    out = []
    for hour in sorted(acc):
        kwh, minutes, t_sum, t_n = acc[hour]
        if minutes >= MIN_QUIET_MIN_PER_HOUR:
            out.append((hour, max(0.0, kwh / (minutes / 60.0)), (t_sum / t_n) if t_n else None))
    return out


def _temp_for(hour_local: datetime, temps_utc: dict, fallback: Optional[float]) -> Optional[float]:
    if temps_utc:
        mid = (hour_local + timedelta(minutes=30)).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if mid in temps_utc:
            return temps_utc[mid]
    return fallback


def fit(samples: list[tuple[datetime, float, Optional[float]]], temps_utc: Optional[dict] = None) -> Optional[DemandModel]:
    rows = []
    for hour, kw, t_bmk in samples:
        t = _temp_for(hour, temps_utc or {}, t_bmk)
        rows.append((kw, t))
    if len(rows) < 12:
        return None
    with_t = [(kw, t) for kw, t in rows if t is not None]
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if len(with_t) >= MIN_HOURS:
        ts_ = np.array([t for _, t in with_t])
        if ts_.max() - ts_.min() >= MIN_TEMP_SPREAD_K:
            y = np.array([kw for kw, _ in with_t])
            x = np.maximum(0.0, BASE_TEMP_C - ts_)
            a_mat = np.column_stack([np.ones_like(x), x])
            from .pv_forecast import nnls
            coef = nnls(a_mat, y)
            pred = a_mat @ coef
            ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1.0
            r2 = 1.0 - float(np.sum((y - pred) ** 2)) / ss_tot
            return DemandModel(float(coef[0]), float(coef[1]), len(with_t), r2,
                               float(ts_.min()), float(ts_.max()), stamp, True, float(x.mean()))
    # Zu wenig Temperatur-Spannweite: konstanter Verbrauch
    mean_kw = float(np.median([kw for kw, _ in rows]))
    temps = [t for _, t in rows if t is not None]
    return DemandModel(mean_kw, 0.0, len(rows), None, min(temps) if temps else None,
                       max(temps) if temps else None, stamp, False)


def _load_cached() -> Optional[DemandModel]:
    try:
        with open(_MODEL_PATH, "r", encoding="utf-8") as f:
            return DemandModel.from_dict(json.load(f))
    except Exception:
        return None


def _save(model: DemandModel) -> None:
    try:
        os.makedirs(os.path.dirname(_MODEL_PATH), exist_ok=True)
        tmp = _MODEL_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(model.to_dict(), f)
        os.replace(tmp, _MODEL_PATH)
    except Exception:
        pass


def get_model(store, cfg: Optional[hs.StorageConfig] = None, temps_utc: Optional[dict] = None,
              force: bool = False) -> Optional[DemandModel]:
    """Gecachtes Modell; einmal pro Tag neu gelernt."""
    cached = _load_cached()
    if cached and not force and cached.fitted_at:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(cached.fitted_at)).total_seconds()
            if age < MODEL_MAX_AGE_S:
                return cached
        except ValueError:
            pass
    cfg = cfg or hs.load_storage_config()
    now = datetime.now()
    buckets = hs.load_buckets(store, now - timedelta(days=FIT_DAYS), now)
    model = fit(quiet_hours(buckets, cfg), temps_utc)
    if model:
        _save(model)
        return model
    return cached


def hourly_demand(model: DemandModel, start_local: datetime, hours: int, temps_utc: dict) -> list[tuple[datetime, float, Optional[float]]]:
    """[(Stundenbeginn lokal, kW, °C)] fuer die naechsten `hours` Stunden."""
    out = []
    t = start_local.replace(minute=0, second=0, microsecond=0)
    for _ in range(hours):
        temp = _temp_for(t, temps_utc, None)
        out.append((t, model.kw_at(temp), temp))
        t += timedelta(hours=1)
    return out


@dataclass
class WeekOutlook:
    demand_kwh: float
    solar_kwh: float
    mean_temp: Optional[float]
    firings: Optional[int]
    days: list[tuple[date, float, Optional[float]]]   # (Tag, Bedarf kWh, Ø °C)


def week_outlook(model: DemandModel, temps_utc: dict, usable_now: float, avg_firing_kwh: Optional[float],
                 solar_by_day: Optional[dict] = None, now: Optional[datetime] = None, days: int = 7) -> WeekOutlook:
    now = now or datetime.now()
    hourly = hourly_demand(model, now, 24 * days, temps_utc)
    per_day: dict[date, list[float]] = {}
    for t, kw, temp in hourly:
        a = per_day.setdefault(t.date(), [0.0, 0.0, 0])
        a[0] += kw
        if temp is not None:
            a[1] += temp
            a[2] += 1
    day_rows = [(d, v[0], (v[1] / v[2]) if v[2] else None) for d, v in sorted(per_day.items())]
    demand = sum(r[1] for r in day_rows)
    solar = sum((solar_by_day or {}).values())
    temps = [r[2] for r in day_rows if r[2] is not None]
    firings = None
    if avg_firing_kwh and avg_firing_kwh > 10:
        missing = max(0.0, demand - solar - usable_now)
        firings = int(np.ceil(missing / avg_firing_kwh)) if missing > 0 else 0
    return WeekOutlook(demand, solar, (sum(temps) / len(temps)) if temps else None, firings, day_rows)
