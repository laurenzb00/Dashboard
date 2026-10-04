"""Einheiz-Statistik: Holz-Einheizvorgaenge und Waermeeintrag Holz vs. Solar.

Erkennung
---------
* **Einheizen (Holz)** wird ausschliesslich an der Kesseltemperatur erkannt:
  Der Kessel gilt als aktiv, wenn er mindestens KESSEL_MIN_C warm ist UND
  waermer als der Puffer oben (sonst ist er nur passiv mit dem Puffer
  temperiert). Ein Vorgang beginnt nach mindestens EVENT_GAP_MIN Minuten
  Pause und muss mindestens KESSEL_PEAK_MIN_C erreichen.
* **Waermeeintrag**: Aus den Speichertemperaturen wird der Waermeinhalt
  berechnet (Puffer: Mittel aus oben/mitte/unten, Boiler: Warmwasser):
      Q = V_puffer * 1.163 Wh/(l*K) * T_puffer + V_boiler * 1.163 * T_boiler
  Umladen Puffer -> Boiler hebt sich dadurch auf. Steigt Q, wird der
  Anstieg zugeordnet:
    - Kessel aktiv (oder bis zu 30 min danach) -> Holz
    - sonst, wenn es Tag ist (Sonne ueber dem Horizont am Standort aus
      config/weather.json)                     -> Solar (Solarthermie)
    - sonst (nachts ohne Kessel)               -> nicht zugeordnet
  Es zaehlen nur echte Anstiege (Netto im Speicher). Kleine Schwankungen
  (Sensorrauschen) werden ignoriert: zusammenhaengende Anstiege zaehlen
  erst ab MIN_RUN_KWH.

Volumen in config/heizung.json ueberschreibbar:
    {"puffer_liter": 4000, "boiler_liter": 500}
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

import math
from datetime import timezone

from .time_utils import DB_TS_FORMAT, db_ts_to_local, to_db_ts

WH_PER_LITER_K = 1.163

KESSEL_MIN_C = 60.0          # darunter ist der Kessel aus
KESSEL_OVER_PUFFER_K = 2.0   # Kessel muss waermer als Puffer oben sein
KESSEL_PEAK_MIN_C = 65.0     # Mindest-Spitze fuer einen echten Einheizvorgang
AFTERGLOW_MIN = 30           # Nachlauf: Waerme fliesst noch nach
EVENT_GAP_MIN = 90           # Pause zwischen zwei Vorgaengen
EVENT_MIN_DURATION_MIN = 30
BUCKET_MIN = 15
MAX_GAP_MIN = 75             # aeltere, verdichtete Daten liegen stuendlich vor
MIN_RUN_KWH = 1.0
SUN_MIN_ELEVATION_DEG = 0.0  # "Tag" = Sonne ueber dem Horizont

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "config", "heizung.json")


@dataclass(frozen=True)
class StorageConfig:
    puffer_liter: float = 4000.0   # 2 x 2000 l
    boiler_liter: float = 500.0

    @property
    def puffer_kwh_per_k(self) -> float:
        return self.puffer_liter * WH_PER_LITER_K / 1000.0

    @property
    def boiler_kwh_per_k(self) -> float:
        return self.boiler_liter * WH_PER_LITER_K / 1000.0


def load_storage_config() -> StorageConfig:
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return StorageConfig(
            puffer_liter=float(data.get("puffer_liter", 4000.0)),
            boiler_liter=float(data.get("boiler_liter", 500.0)),
        )
    except Exception:
        return StorageConfig()


@dataclass
class Bucket:
    ts: datetime                 # lokale Zeit (Bucket-Beginn)
    kessel: Optional[float]
    top: Optional[float]
    mid: Optional[float]
    bot: Optional[float]
    warm: Optional[float]
    outdoor: Optional[float]


@dataclass
class HeatingEvent:
    start: datetime
    end: datetime
    peak_kessel: float
    wood_kwh: float = 0.0

    @property
    def duration_min(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


@dataclass
class DayStats:
    day: date
    wood_kwh: float = 0.0
    solar_kwh: float = 0.0
    other_kwh: float = 0.0
    used_kwh: float = 0.0
    events: int = 0
    outdoor_sum: float = 0.0
    outdoor_n: int = 0

    @property
    def outdoor_mean(self) -> Optional[float]:
        return self.outdoor_sum / self.outdoor_n if self.outdoor_n else None


@dataclass
class HeatingStats:
    days: list[DayStats] = field(default_factory=list)
    events: list[HeatingEvent] = field(default_factory=list)

    @property
    def wood_kwh(self) -> float:
        return sum(d.wood_kwh for d in self.days)

    @property
    def solar_kwh(self) -> float:
        return sum(d.solar_kwh for d in self.days)

    @property
    def solar_share_pct(self) -> Optional[float]:
        total = self.wood_kwh + self.solar_kwh
        return self.solar_kwh / total * 100.0 if total > 0.5 else None

    @property
    def events_per_week(self) -> Optional[float]:
        n_days = len([d for d in self.days])
        return len(self.events) / n_days * 7.0 if n_days else None


# ---------------------------------------------------------------------------
# Daten laden
# ---------------------------------------------------------------------------

def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _heat_val(v) -> Optional[float]:
    """0.0 ist bei BMK ein Platzhalter fuer 'kein Wert'."""
    x = _f(v)
    if x is None or x == 0.0 or x < -40 or x > 120:
        return None
    return x


def load_buckets(store, start: datetime, end: datetime) -> list[Bucket]:
    """15-min-Mittelwerte (lokale Zeit) aus der heating-Tabelle."""
    conn = getattr(store, "conn", None)
    if conn is None:
        return []
    s, e = to_db_ts(start, naive_is_local=True), to_db_ts(end, naive_is_local=True)
    acc: dict[datetime, dict[str, list[float]]] = {}

    def _key(ts_raw) -> Optional[datetime]:
        dt = db_ts_to_local(ts_raw)
        if dt is None:
            return None
        return dt.replace(minute=dt.minute - dt.minute % BUCKET_MIN, second=0, microsecond=0)

    for row in conn.execute(
        "SELECT timestamp, kesseltemp, puffer_top, puffer_mid, puffer_bot, warmwasser, aussentemp "
        "FROM heating WHERE timestamp >= ? AND timestamp < ?", (s, e)
    ):
        k = _key(row[0])
        if k is None:
            continue
        slot = acc.setdefault(k, {})
        for name, val in zip(("kessel", "top", "mid", "bot", "warm"), row[1:6]):
            v = _heat_val(val)
            if v is not None:
                slot.setdefault(name, []).append(v)
        o = _f(row[6])
        if o is not None and -40 <= o <= 60:
            slot.setdefault("outdoor", []).append(o)

    out = []
    for k in sorted(acc):
        slot = acc[k]
        m = {n: (sum(v) / len(v)) for n, v in slot.items() if v}
        if "top" not in m and "kessel" not in m:
            continue
        out.append(Bucket(ts=k, kessel=m.get("kessel"), top=m.get("top"), mid=m.get("mid"),
                          bot=m.get("bot"), warm=m.get("warm"), outdoor=m.get("outdoor")))
    return out


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

def kessel_active(b: Bucket) -> bool:
    if b.kessel is None:
        return False
    ref = b.top if b.top is not None else 0.0
    return b.kessel >= KESSEL_MIN_C and b.kessel >= ref + KESSEL_OVER_PUFFER_K


def heat_content_kwh(b: Bucket, cfg: StorageConfig) -> Optional[float]:
    layers = [v for v in (b.top, b.mid, b.bot) if v is not None]
    if len(layers) < 2:
        return None
    q = cfg.puffer_kwh_per_k * (sum(layers) / len(layers))
    if b.warm is not None:
        q += cfg.boiler_kwh_per_k * b.warm
    return q


def sun_elevation_deg(local_naive: datetime, lat: float, lon: float) -> float:
    """Sonnenhoehe (Grad) fuer eine lokale Zeit - NOAA-Naeherung, ~0,5 Grad genau."""
    utc = local_naive.astimezone(timezone.utc)
    doy = utc.timetuple().tm_yday
    hour = utc.hour + utc.minute / 60.0 + utc.second / 3600.0
    g = 2.0 * math.pi / 365.0 * (doy - 1 + (hour - 12) / 24.0)
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    true_solar_min = hour * 60.0 + eqtime + 4.0 * lon
    ha = math.radians(true_solar_min / 4.0 - 180.0)
    phi = math.radians(lat)
    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    return 90.0 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))


def _is_daytime(b: Bucket, lat: float, lon: float) -> bool:
    mid = b.ts + timedelta(minutes=BUCKET_MIN / 2)
    return sun_elevation_deg(mid, lat, lon) > SUN_MIN_ELEVATION_DEG


def detect_events(buckets: list[Bucket]) -> list[HeatingEvent]:
    events: list[HeatingEvent] = []
    cur: Optional[HeatingEvent] = None
    last_active: Optional[datetime] = None
    for i, b in enumerate(buckets):
        if not kessel_active(b):
            continue
        if cur is not None and last_active is not None and (b.ts - last_active) <= timedelta(minutes=EVENT_GAP_MIN):
            cur.end = b.ts + timedelta(minutes=BUCKET_MIN)
            cur.peak_kessel = max(cur.peak_kessel, b.kessel)
        else:
            if cur is not None:
                events.append(cur)
            # Beginn auf den Anfang des Kessel-Anstiegs zurueckverlegen (max. 60 min)
            start = b.ts
            j = i - 1
            while j >= 0 and (b.ts - buckets[j].ts) <= timedelta(minutes=60):
                kj, kn = buckets[j].kessel, buckets[j + 1].kessel
                if kj is None or kn is None or kn - kj < 0.5:
                    break
                start = buckets[j + 1].ts  # erster Messwert nach dem Anzuenden
                j -= 1
            cur = HeatingEvent(start=start, end=b.ts + timedelta(minutes=BUCKET_MIN), peak_kessel=b.kessel)
        last_active = b.ts
    if cur is not None:
        events.append(cur)
    return [e for e in events
            if e.peak_kessel >= KESSEL_PEAK_MIN_C and e.duration_min >= EVENT_MIN_DURATION_MIN]


def analyze(buckets: list[Bucket], cfg: Optional[StorageConfig] = None,
            first_day: Optional[date] = None, last_day: Optional[date] = None,
            lat: float = 48.2569, lon: float = 13.0397) -> HeatingStats:
    cfg = cfg or StorageConfig()
    stats = HeatingStats()
    if first_day and last_day:
        days = {first_day + timedelta(days=i): DayStats(first_day + timedelta(days=i))
                for i in range((last_day - first_day).days + 1)}
    else:
        days = {}
    if not buckets:
        stats.days = list(days.values())
        return stats

    events = detect_events(buckets)

    # Kessel aktiv inkl. Nachlauf
    active_until: Optional[datetime] = None
    active_flags = []
    for b in buckets:
        if kessel_active(b):
            active_until = b.ts + timedelta(minutes=BUCKET_MIN + AFTERGLOW_MIN)
        active_flags.append(active_until is not None and b.ts < active_until)

    def _day(d: date) -> DayStats:
        return days.setdefault(d, DayStats(d))

    for b in buckets:
        if b.outdoor is not None:
            ds = _day(b.ts.date())
            ds.outdoor_sum += b.outdoor
            ds.outdoor_n += 1

    # Anstiege zu Laeufen gleicher Quelle zusammenfassen, Rauschen verwerfen
    run_src: Optional[str] = None
    run_steps: list[tuple[datetime, float]] = []

    def _flush():
        nonlocal run_src, run_steps
        total = sum(v for _, v in run_steps)
        if run_src and total >= MIN_RUN_KWH:
            for ts, v in run_steps:
                ds = _day(ts.date())
                if run_src == "wood":
                    ds.wood_kwh += v
                    for ev in events:
                        if ev.start - timedelta(minutes=BUCKET_MIN) <= ts <= ev.end + timedelta(minutes=AFTERGLOW_MIN):
                            ev.wood_kwh += v
                            break
                elif run_src == "solar":
                    ds.solar_kwh += v
                else:
                    ds.other_kwh += v
        run_src, run_steps = None, []

    prev_q = None
    prev_b = None
    for b, active in zip(buckets, active_flags):
        q = heat_content_kwh(b, cfg)
        if q is None:
            continue
        if prev_q is not None and (b.ts - prev_b.ts) <= timedelta(minutes=MAX_GAP_MIN):
            d = q - prev_q
            if d > 0:
                src = "wood" if active else ("solar" if _is_daytime(b, lat, lon) else "other")
                if src != run_src:
                    _flush()
                    run_src = src
                run_steps.append((b.ts, d))
            else:
                _flush()
                _day(b.ts.date()).used_kwh += -d
        else:
            _flush()
        prev_q, prev_b = q, b
    _flush()

    for ev in events:
        _day(ev.start.date()).events += 1
    stats.events = events
    lo = first_day or min(days)
    hi = last_day or max(days)
    stats.days = [days[d] for d in sorted(days) if lo <= d <= hi]
    return stats


def compute(store, days: int, cfg: Optional[StorageConfig] = None, today: Optional[date] = None) -> HeatingStats:
    """Statistik der letzten `days` Kalendertage (inkl. heute)."""
    today = today or date.today()
    first = today - timedelta(days=max(1, days) - 1)
    start = datetime.combine(first, datetime.min.time()) - timedelta(hours=2)  # Vorlauf fuer Event-Start
    end = datetime.combine(today + timedelta(days=1), datetime.min.time())
    buckets = load_buckets(store, start, end)
    try:
        from .weather import load_weather_config
        wcfg = load_weather_config()
        lat, lon = wcfg.latitude, wcfg.longitude
    except Exception:
        lat, lon = 48.2569, 13.0397
    stats = analyze(buckets, cfg or load_storage_config(), first_day=first, last_day=today, lat=lat, lon=lon)
    stats.events = [e for e in stats.events if e.start.date() >= first]
    return stats
