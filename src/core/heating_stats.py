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

Einstellungen in config/heizung.json (alle optional):
    {"puffer_liter": 4000, "boiler_liter": 500,
     "nutzbar_ab_c": 35, "holz_kwh_pro_rm": 1800, "kessel_wirkungsgrad": 0.85}
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
    usable_from_c: float = 35.0          # darunter bringt der Puffer den Heizkreisen nichts mehr
    full_at_c: float = 80.0              # "voll" fuer die Ladezustands-Anzeige
    wood_kwh_per_rm: float = 1800.0      # gemischtes Brennholz (Buche ~2100, Fichte ~1500)
    boiler_efficiency: float = 0.85      # Kessel: Holzenergie -> Speicher

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
            usable_from_c=float(data.get("nutzbar_ab_c", 35.0)),
            full_at_c=float(data.get("voll_bei_c", 80.0)),
            wood_kwh_per_rm=float(data.get("holz_kwh_pro_rm", 1800.0)),
            boiler_efficiency=float(data.get("kessel_wirkungsgrad", 0.85)),
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
    rauchgas: Optional[float] = None     # ab 2026-10 aufgezeichnet - eindeutigster Hinweis auf Feuer
    modus: Optional[float] = None        # BMK-Betriebsmodus (Code)
    firing: Optional[bool] = None        # Ergebnis der Episoden-Pruefung (load_buckets), sonst None


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

    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(heating)")}
    except Exception:
        cols = set()
    # Aeltere Datensaetze haben die Aussentemperatur in der Spalte "außentemp"
    outdoor_expr = 'COALESCE(aussentemp, "außentemp")' if "außentemp" in cols else "aussentemp"
    extra = ", rauchgastemp, betriebsmodus" if {"rauchgastemp", "betriebsmodus"} <= cols else ", NULL, NULL"
    for row in conn.execute(
        "SELECT timestamp, kesseltemp, puffer_top, puffer_mid, puffer_bot, warmwasser, " + outdoor_expr + extra + " "
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
        rg = _f(row[7])
        if rg is not None and 0 < rg < 600:
            slot.setdefault("rauchgas", []).append(rg)
        md = _f(row[8])
        if md is not None:
            slot.setdefault("modus", []).append(md)

    out = []
    for k in sorted(acc):
        slot = acc[k]
        m = {n: (sum(v) / len(v)) for n, v in slot.items() if v}
        if "top" not in m and "kessel" not in m:
            continue
        out.append(Bucket(ts=k, kessel=m.get("kessel"), top=m.get("top"), mid=m.get("mid"),
                          bot=m.get("bot"), warm=m.get("warm"), outdoor=m.get("outdoor"),
                          # Rauchgas: Spitzenwert im Bucket (brennt es irgendwann darin?)
                          rauchgas=max(slot["rauchgas"]) if slot.get("rauchgas") else None,
                          modus=max(slot["modus"]) if slot.get("modus") else None))
    # Rauchgas-Spalte nur verwenden, wenn darin je ein Feuer zu sehen war (sonst ist
    # der BMK-Index evtl. etwas anderes) - dann gilt die Episoden-Pruefung.
    rg = [b.rauchgas for b in out if b.rauchgas is not None]
    if rg and max(rg) < RAUCHGAS_PLAUSIBEL_C:
        for b in out:
            b.rauchgas = None
    classify_episodes(out, _pv_hourly(conn, s, e))
    return out


def _pv_hourly(conn, s: str, e: str) -> dict:
    """PV-Stundenmittel (kW) je Stunde (Schluessel: UTC 'YYYY-MM-DD HH')."""
    try:
        rows = conn.execute(
            "SELECT substr(timestamp, 1, 13), AVG(CASE WHEN pv_power > 200 THEN pv_power / 1000.0 "
            "WHEN pv_power < 0 THEN 0 ELSE pv_power END) FROM fronius WHERE timestamp >= ? AND timestamp < ? "
            "AND pv_power IS NOT NULL GROUP BY 1", (s, e)).fetchall()
    except Exception:
        return {}
    return {h: float(v or 0.0) for h, v in rows}


def classify_episodes(buckets: list[Bucket], pv_hourly: dict, cfg: Optional[StorageConfig] = None) -> None:
    """Kessel-heiss-Phasen ohne Rauchgasdaten: Feuer oder Sonne?

    Die Kesseltemperatur allein reicht nicht - Solar bringt den Kesselfuehler
    ebenfalls schnell ueber 60 °C. Entscheidend ist, wie viel Waerme in den
    Speicher kam und ob die Sonne das erklaeren kann:
        Feuer  <=>  Zuwachs >= 15 kWh  UND  Zuwachs > 1,5 x PV-kWh + 10 kWh
    (Fenster: Beginn bis 2 h nach Ende; 1,5 x PV mit Reserve fuer ~15 m^2 Kollektoren.)
    Ergebnis in Bucket.firing; Buckets mit Rauchgas bleiben unberuehrt.
    """
    cfg = cfg or load_storage_config()
    n = len(buckets)
    i = 0
    while i < n:
        b = buckets[i]
        if b.rauchgas is not None or not _kessel_hot(b):
            i += 1
            continue
        j = i
        while j + 1 < n and buckets[j + 1].rauchgas is None and _kessel_hot(buckets[j + 1]) and \
                (buckets[j + 1].ts - buckets[j].ts) <= timedelta(minutes=MAX_GAP_MIN):
            j += 1
        start, end = buckets[i].ts, buckets[j].ts + timedelta(minutes=BUCKET_MIN)
        pre = [heat_content_kwh(x, cfg) for x in buckets[max(0, i - 2):i + 1]]
        win = [heat_content_kwh(x, cfg) for x in buckets[i:n] if x.ts <= end + timedelta(hours=2)]
        pre = [q for q in pre if q is not None]
        win = [q for q in win if q is not None]
        gain = (max(win) - min(pre)) if pre and win else 0.0
        pv_kwh = 0.0
        t = start.replace(minute=0)
        while t <= end + timedelta(hours=2):
            key = t.astimezone(timezone.utc).strftime("%Y-%m-%d %H")
            pv_kwh += pv_hourly.get(key, 0.0)
            t += timedelta(hours=1)
        firing = gain >= EPISODE_MIN_GAIN_KWH and gain > SOLAR_KWH_PER_PV_KWH_MAX * pv_kwh + SOLAR_MARGIN_KWH
        for x in buckets[i:j + 1]:
            x.firing = firing
        i = j + 1


# ---------------------------------------------------------------------------
# Auswertung
# ---------------------------------------------------------------------------

RAUCHGAS_FEUER_C = 90.0      # darueber brennt Holz (Abgas im Betrieb typ. 120-250 °C)


RAUCHGAS_PLAUSIBEL_C = 100.0  # Rauchgas-Spalte nur nutzen, wenn je ein echtes Feuer darin zu sehen war
EPISODE_MIN_GAIN_KWH = 15.0
# Solarthermie ~15 m^2, 16-17 Jahre alt: Spitze grob 5-7 kW Waerme bei ~9-10 kW PV.
# Mehr Waerme als das 1,5-fache der PV-Energie (+ Reserve) kann die Sonne nicht liefern.
SOLAR_KWH_PER_PV_KWH_MAX = 1.5
SOLAR_MARGIN_KWH = 10.0


def _kessel_hot(b: Bucket) -> bool:
    """Alte Regel: Kessel heiss und waermer als der Puffer (kann auch Solar sein)."""
    if b.kessel is None:
        return False
    ref = b.top if b.top is not None else 0.0
    return b.kessel >= KESSEL_MIN_C and b.kessel >= ref + KESSEL_OVER_PUFFER_K


def kessel_active(b: Bucket) -> bool:
    """Brennt Holz im Kessel?

    1. Rauchgastemperatur (ab 2026-10 aufgezeichnet): eindeutig.
    2. Sonst Ergebnis der Episoden-Pruefung aus load_buckets (Speicher-Zuwachs,
       der nicht von der Sonne kommen kann).
    3. Sonst (einzelne Buckets, Tests): Kessel heiss und waermer als der Puffer.
    """
    if b.rauchgas is not None:
        return b.rauchgas >= RAUCHGAS_FEUER_C
    if b.firing is not None:
        return b.firing
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


# ---------------------------------------------------------------------------
# Fuer den Waerme-Tab: Live-Zustand, Tagesverlauf, Saison (mit Cache)
# ---------------------------------------------------------------------------

def puffer_mean(top, mid, bot) -> Optional[float]:
    vals = [v for v in (top, mid, bot) if v is not None]
    return sum(vals) / len(vals) if len(vals) >= 2 else None


def usable_kwh(mean_c: Optional[float], cfg: StorageConfig) -> Optional[float]:
    if mean_c is None:
        return None
    return max(0.0, mean_c - cfg.usable_from_c) * cfg.puffer_kwh_per_k


def charge_pct(mean_c: Optional[float], cfg: StorageConfig) -> Optional[float]:
    if mean_c is None:
        return None
    span = max(1.0, cfg.full_at_c - cfg.usable_from_c)
    return max(0.0, min(100.0, (mean_c - cfg.usable_from_c) / span * 100.0))


def wood_rm(wood_kwh: float, cfg: StorageConfig) -> float:
    """Waerme im Speicher -> verheizte Raummeter (ueber den Kesselwirkungsgrad)."""
    return wood_kwh / max(0.1, cfg.boiler_efficiency) / max(1.0, cfg.wood_kwh_per_rm)


def _location() -> tuple[float, float]:
    try:
        from .weather import load_weather_config
        w = load_weather_config()
        return w.latitude, w.longitude
    except Exception:
        return 48.2569, 13.0397


@dataclass
class TimelinePoint:
    ts: datetime                 # lokale Zeit (Bucket-Mitte)
    q_kwh: Optional[float]       # Waermeinhalt Puffer+Boiler oberhalb "nutzbar ab"
    source: Optional[str]        # "wood" / "solar" / None - wer gerade laedt
    kessel: Optional[float]


def day_timeline(store, day: date, cfg: Optional[StorageConfig] = None) -> tuple[list[TimelinePoint], DayStats]:
    """Verlauf eines Tages (15-min) inkl. Quelle der Anstiege + Tageswerte."""
    cfg = cfg or load_storage_config()
    lat, lon = _location()
    start = datetime.combine(day, datetime.min.time())
    buckets = load_buckets(store, start - timedelta(hours=2), start + timedelta(days=1))
    stats = analyze(buckets, cfg, first_day=day, last_day=day, lat=lat, lon=lon)
    day_stats = stats.days[0] if stats.days else DayStats(day)

    active_until = None
    points: list[TimelinePoint] = []
    prev_q = None
    base = cfg.usable_from_c * (cfg.puffer_kwh_per_k + cfg.boiler_kwh_per_k)
    for b in buckets:
        if kessel_active(b):
            active_until = b.ts + timedelta(minutes=BUCKET_MIN + AFTERGLOW_MIN)
        q = heat_content_kwh(b, cfg)
        src = None
        if q is not None and prev_q is not None and q - prev_q > 0.05:
            if active_until is not None and b.ts < active_until:
                src = "wood"
            elif _is_daytime(b, lat, lon):
                src = "solar"
        if q is not None:
            prev_q = q
        if b.ts >= start:
            points.append(TimelinePoint(
                ts=b.ts + timedelta(minutes=BUCKET_MIN / 2),
                q_kwh=(q - base) if q is not None else None,
                source=src, kessel=b.kessel,
            ))
    return points, day_stats


SEASON_START_MONTH = 9          # Heizsaison ab 1. September
_CACHE_VERSION = 2
_DAY_CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "heating_stats_cache.json")


def season_start(today: Optional[date] = None) -> date:
    today = today or date.today()
    year = today.year if today.month >= SEASON_START_MONTH else today.year - 1
    return date(year, SEASON_START_MONTH, 1)


def _load_day_cache() -> dict:
    try:
        with open(_DAY_CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("version") == _CACHE_VERSION:
            return data
    except Exception:
        pass
    return {"version": _CACHE_VERSION, "days": {}, "events": {}}


def _save_day_cache(data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_DAY_CACHE_PATH), exist_ok=True)
        tmp = _DAY_CACHE_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, _DAY_CACHE_PATH)
    except Exception:
        pass


def season_stats(store, cfg: Optional[StorageConfig] = None, today: Optional[date] = None,
                 use_cache: bool = True) -> HeatingStats:
    """Statistik seit Saisonbeginn. Abgeschlossene Tage (aelter als gestern)
    werden in data/heating_stats_cache.json zwischengespeichert, damit nicht
    bei jedem Aufruf Monate an Rohdaten neu ausgewertet werden muessen."""
    cfg = cfg or load_storage_config()
    today = today or date.today()
    first = season_start(today)
    cache = _load_day_cache() if use_cache else {"version": _CACHE_VERSION, "days": {}, "events": {}}
    cfg_key = f"{cfg.puffer_liter}/{cfg.boiler_liter}"
    if cache.get("cfg") != cfg_key:
        cache = {"version": _CACHE_VERSION, "days": {}, "events": {}, "cfg": cfg_key}

    final_until = today - timedelta(days=2)  # bis einschliesslich vorgestern gilt als abgeschlossen
    missing = [first + timedelta(days=i) for i in range((today - first).days + 1)
               if (first + timedelta(days=i)) > final_until
               or (first + timedelta(days=i)).isoformat() not in cache["days"]]
    lat, lon = _location()
    if missing:
        lo, hi = min(missing), max(missing)
        start = datetime.combine(lo, datetime.min.time()) - timedelta(hours=2)
        end = datetime.combine(hi + timedelta(days=1), datetime.min.time())
        st = analyze(load_buckets(store, start, end), cfg, first_day=lo, last_day=hi, lat=lat, lon=lon)
        for d in st.days:
            if d.day in missing:
                cache["days"][d.day.isoformat()] = [d.wood_kwh, d.solar_kwh, d.used_kwh, d.events,
                                                    d.outdoor_sum, d.outdoor_n]
        for ev in st.events:
            if ev.start.date() in missing:
                cache["events"][ev.start.isoformat()] = [ev.end.isoformat(), ev.peak_kessel, ev.wood_kwh]
        if use_cache:
            _save_day_cache(cache)

    out = HeatingStats()
    for i in range((today - first).days + 1):
        d = first + timedelta(days=i)
        row = cache["days"].get(d.isoformat())
        ds = DayStats(d)
        if row:
            ds.wood_kwh, ds.solar_kwh, ds.used_kwh, ds.events, ds.outdoor_sum, ds.outdoor_n = row
        out.days.append(ds)
    for start_s, (end_s, peak, wood) in sorted(cache["events"].items()):
        st_dt = datetime.fromisoformat(start_s)
        if first <= st_dt.date() <= today:
            out.events.append(HeatingEvent(start=st_dt, end=datetime.fromisoformat(end_s),
                                           peak_kessel=peak, wood_kwh=wood))
    return out
