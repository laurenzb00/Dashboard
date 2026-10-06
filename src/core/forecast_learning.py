"""Lern-Archiv fuer die Prognosen (PV, Waermebedarf, Solarthermie).

Sammelt dauerhaft Stundenwerte in data/forecast_learning.db, damit die Modelle
aus der GESAMTEN Historie lernen - nicht nur aus den letzten Wochen:

* weather    Open-Meteo je Stunde: Globalstrahlung (GHI), Direkt- (DNI) und
             Diffusstrahlung (DHI) als Mittel der vorangegangenen Stunde,
             Lufttemperatur zum Zeitpunkt. Schluessel = Stundenende (Unix, UTC).
* pv_hours   gemessene PV-Leistung je Stunde (Mittel, kW), aus fronius.
* heat_hours Speicher-Bilanz je Stunde aus der heating-Tabelle:
             - quiet_*:   Abkuehlung in ruhigen Phasen (Kessel aus inkl. Nachlauf,
                          kein Anstieg) = Waermeverbrauch
             - free_*:    Netto-Aenderung ohne Kessel (inkl. Solar-Anstieg)
             - tank_c, outdoor_c (BMK-Sensor)

update(store) arbeitet inkrementell (nur neue Stunden + 1 Tag Ueberlappung)
und ist gedrosselt: hoechstens einmal pro Kalendertag und einmal nach jedem
Programmstart. Erstbefuellung: Open-Meteo liefert die letzten 92 Tage; fuer
aeltere eigene Messdaten wird einmalig das Open-Meteo-Archiv (ERA5) abgefragt.

Ausserdem: "aehnliches Wetter zaehlt mehr" - Kernel-Regression auf einem
Raster (kernel_grid / local_linear_grid) und Interpolation (grid_lookup).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np
import requests

from . import heating_stats as hs
from .time_utils import DB_TS_FORMAT
from .weather import WeatherConfig, load_weather_config

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
WEATHER_VARS = "shortwave_radiation,direct_normal_irradiance,diffuse_radiation,temperature_2m"
MAX_PAST_DAYS = 92
ARCHIVE_MAX_YEARS = 3
OVERLAP_S = 24 * 3600
HEAT_CHUNK_DAYS = 31
HEAT_VERSION = 2       # 2: Feuer vs. Sonne per Speicher-Zuwachs / Rauchgas (heating_stats.classify_episodes)

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
DB_PATH = _DATA_DIR / "forecast_learning.db"

_LOCK = threading.RLock()
_session = requests.Session()
_updated_day: Optional[date] = None      # None = seit Programmstart noch nicht


# ---------------------------------------------------------------------------
# Datenbank
# ---------------------------------------------------------------------------

def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=20)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS weather (hour_end INTEGER PRIMARY KEY, ghi REAL, dni REAL, dhi REAL,
                                            temp REAL, src TEXT);
        CREATE TABLE IF NOT EXISTS pv_hours (hour_end INTEGER PRIMARY KEY, pv_kw REAL, n INTEGER);
        CREATE TABLE IF NOT EXISTS heat_hours (hour_start INTEGER PRIMARY KEY,
            quiet_kwh REAL, quiet_min REAL, free_kwh REAL, free_min REAL, kessel_min REAL,
            tank_c REAL, outdoor_c REAL);
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    return conn


def _meta_get(conn, key: str) -> Optional[str]:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))


def _unix(dt: datetime) -> int:
    if dt.tzinfo is None:
        dt = dt.astimezone()            # naiv = lokal
    return int(dt.timestamp())


# ---------------------------------------------------------------------------
# Wetter (Open-Meteo)
# ---------------------------------------------------------------------------

def _parse_hourly(payload: dict) -> list[tuple]:
    h = (payload or {}).get("hourly") or {}
    rows = []
    for i, t in enumerate(h.get("time") or []):
        try:
            ts = int(datetime.fromisoformat(str(t)).replace(tzinfo=timezone.utc).timestamp())
        except ValueError:
            continue

        def v(name):
            arr = h.get(name) or []
            x = arr[i] if i < len(arr) else None
            return float(x) if x is not None else None
        rows.append((ts, v("shortwave_radiation"), v("direct_normal_irradiance"), v("diffuse_radiation"),
                     v("temperature_2m")))
    return rows


def fetch_weather(cfg: WeatherConfig, past_days: int, forecast_days: int) -> list[tuple]:
    r = _session.get(FORECAST_URL, params={
        "latitude": cfg.latitude, "longitude": cfg.longitude, "hourly": WEATHER_VARS,
        "past_days": max(0, min(MAX_PAST_DAYS, int(past_days))),
        "forecast_days": max(1, min(16, int(forecast_days))), "timezone": "UTC",
    }, timeout=max(8.0, cfg.timeout_s))
    r.raise_for_status()
    return _parse_hourly(r.json())


def fetch_archive(cfg: WeatherConfig, start: date, end: date) -> list[tuple]:
    r = _session.get(ARCHIVE_URL, params={
        "latitude": cfg.latitude, "longitude": cfg.longitude, "hourly": WEATHER_VARS,
        "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "UTC",
    }, timeout=60)
    r.raise_for_status()
    return _parse_hourly(r.json())


def store_weather(conn, rows: Iterable[tuple], src: str) -> int:
    rows = [r + (src,) for r in rows if any(x is not None for x in r[1:])]
    conn.executemany("INSERT OR REPLACE INTO weather (hour_end, ghi, dni, dhi, temp, src) VALUES (?,?,?,?,?,?)", rows)
    return len(rows)


def _update_weather(conn, store, cfg: WeatherConfig, now: float) -> None:
    last = conn.execute("SELECT MAX(hour_end) FROM weather WHERE hour_end <= ?", (int(now),)).fetchone()[0]
    past = MAX_PAST_DAYS if last is None else int((now - last) / 86400) + 2
    try:
        n = store_weather(conn, fetch_weather(cfg, past, 3), "forecast")
        logger.info("[Lernen] Wetter: %d Stunden aktualisiert (%d Tage zurueck)", n, min(past, MAX_PAST_DAYS))
    except Exception as exc:
        logger.info("[Lernen] Wetterabruf fehlgeschlagen: %s", exc)
        return
    # Einmalig: aeltere eigene Messdaten mit dem Archiv abdecken
    if _meta_get(conn, "archive_done") == "1":
        return
    first_data = _first_measurement(store)
    first_weather = conn.execute("SELECT MIN(hour_end) FROM weather").fetchone()[0]
    if first_data is None or first_weather is None or first_data >= first_weather - 86400:
        _meta_set(conn, "archive_done", "1")
        return
    start = max(datetime.fromtimestamp(first_data, timezone.utc).date(),
                date.today() - timedelta(days=365 * ARCHIVE_MAX_YEARS))
    end = datetime.fromtimestamp(first_weather, timezone.utc).date()
    try:
        cur = start
        while cur <= end:
            chunk_end = min(end, cur + timedelta(days=180))
            store_weather(conn, fetch_archive(cfg, cur, chunk_end), "archive")
            cur = chunk_end + timedelta(days=1)
        _meta_set(conn, "archive_done", "1")
        logger.info("[Lernen] Wetter-Archiv nachgeladen: %s bis %s", start, end)
    except Exception as exc:
        logger.info("[Lernen] Wetter-Archiv nicht verfuegbar (wird spaeter erneut versucht): %s", exc)


def _first_measurement(store) -> Optional[int]:
    conn = getattr(store, "conn", None)
    if conn is None:
        return None
    best = None
    for table in ("fronius", "heating"):
        try:
            v = conn.execute(f"SELECT MIN(timestamp) FROM {table}").fetchone()[0]
        except Exception:
            continue
        if v:
            try:
                ts = int(datetime.strptime(str(v)[:19], DB_TS_FORMAT).replace(tzinfo=timezone.utc).timestamp())
            except ValueError:
                continue
            best = ts if best is None else min(best, ts)
    return best


# ---------------------------------------------------------------------------
# Messwerte -> Stundenwerte
# ---------------------------------------------------------------------------

def _update_pv(conn, store, now: float) -> None:
    src = getattr(store, "conn", None)
    if src is None:
        return
    last = conn.execute("SELECT MAX(hour_end) FROM pv_hours").fetchone()[0]
    since = 0 if last is None else last - OVERLAP_S
    since_txt = datetime.fromtimestamp(since, timezone.utc).strftime(DB_TS_FORMAT)
    rows = src.execute(
        "SELECT substr(timestamp, 1, 13) AS h, "
        "AVG(CASE WHEN pv_power > 200 THEN pv_power / 1000.0 WHEN pv_power < 0 THEN 0 ELSE pv_power END), COUNT(*) "
        "FROM fronius WHERE timestamp >= ? AND pv_power IS NOT NULL GROUP BY h", (since_txt,)).fetchall()
    out = []
    cur_hour = int(now // 3600 * 3600)
    for h, kw, n in rows:
        try:
            start = int(datetime.strptime(h, "%Y-%m-%d %H").replace(tzinfo=timezone.utc).timestamp())
        except (TypeError, ValueError):
            continue
        if start >= cur_hour:          # laufende Stunde noch unvollstaendig
            continue
        out.append((start + 3600, float(kw), int(n)))
    conn.executemany("INSERT OR REPLACE INTO pv_hours (hour_end, pv_kw, n) VALUES (?,?,?)", out)


def heat_hour_rows(buckets: Sequence[hs.Bucket], cfg: hs.StorageConfig) -> list[tuple]:
    """15-min-Buckets -> Stundenbilanz (siehe Modul-Doku). Zeit: Stundenbeginn Unix."""
    acc: dict[datetime, list[float]] = {}
    prev_q = prev_b = None
    active_until = None
    for b in buckets:
        hour = b.ts.replace(minute=0, second=0, microsecond=0)
        a = acc.setdefault(hour, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0])
        if hs.kessel_active(b):
            active_until = b.ts + timedelta(minutes=hs.BUCKET_MIN + hs.AFTERGLOW_MIN)
        layers = [v for v in (b.top, b.mid, b.bot) if v is not None]
        if layers:
            a[5] += sum(layers) / len(layers)
            a[6] += 1
        if b.outdoor is not None:
            a[7] += b.outdoor
            a[8] += 1
        q = hs.heat_content_kwh(b, cfg)
        if q is None:
            continue
        if prev_q is not None:
            minutes = (b.ts - prev_b.ts).total_seconds() / 60.0
            if 0 < minutes <= hs.MAX_GAP_MIN:
                busy = active_until is not None and b.ts < active_until
                d = q - prev_q
                if busy:
                    a[4] += minutes
                else:
                    a[2] += d
                    a[3] += minutes
                    if d <= 0.05:
                        a[0] += -d
                        a[1] += minutes
        prev_q, prev_b = q, b
    rows = []
    for hour, a in sorted(acc.items()):
        rows.append((_unix(hour), a[0], a[1], a[2], a[3], a[4],
                     (a[5] / a[6]) if a[6] else None, (a[7] / a[8]) if a[8] else None))
    return rows


def _update_heat(conn, store, cfg: hs.StorageConfig, now: float) -> None:
    if getattr(store, "conn", None) is None:
        return
    if _meta_get(conn, "heat_version") != str(HEAT_VERSION):
        # Erkennung "Kessel brennt" hat sich geaendert -> Stundenbilanz neu aufbauen
        conn.execute("DELETE FROM heat_hours")
        _meta_set(conn, "heat_version", HEAT_VERSION)
        conn.commit()
    last = conn.execute("SELECT MAX(hour_start) FROM heat_hours").fetchone()[0]
    first = _first_measurement(store) if last is None else last - OVERLAP_S
    if first is None:
        return
    start = datetime.fromtimestamp(first).replace(minute=0, second=0, microsecond=0)
    end = datetime.fromtimestamp(now).replace(minute=0, second=0, microsecond=0)   # laufende Stunde nicht
    while start < end:
        chunk_end = min(end, start + timedelta(days=HEAT_CHUNK_DAYS))
        # 1 h Vorlauf, damit die erste Stunde einen Vorgaenger hat
        buckets = hs.load_buckets(store, start - timedelta(hours=1), chunk_end)
        rows = [r for r in heat_hour_rows(buckets, cfg) if _unix(start) <= r[0] < _unix(chunk_end)]
        conn.executemany("INSERT OR REPLACE INTO heat_hours (hour_start, quiet_kwh, quiet_min, free_kwh, free_min, "
                         "kessel_min, tank_c, outdoor_c) VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.commit()
        start = chunk_end


def update(store, cfg: Optional[WeatherConfig] = None, storage: Optional[hs.StorageConfig] = None,
           force: bool = False, allow_network: bool = True) -> bool:
    """Archiv nachfuehren. Gedrosselt: einmal je Programmstart und Kalendertag."""
    global _updated_day
    with _LOCK:
        today = date.today()
        if not force and _updated_day == today:
            return False
        cfg = cfg or load_weather_config()
        storage = storage or hs.load_storage_config()
        now = time.time()
        t0 = time.monotonic()
        from .perf_monitor import timed
        conn = connect()
        try:
            if allow_network and cfg.enabled:
                with timed("lernen.wetter"):
                    _update_weather(conn, store, cfg, now)
            with timed("lernen.pv_stunden"):
                _update_pv(conn, store, now)
            with timed("lernen.waerme_stunden"):
                _update_heat(conn, store, storage, now)
            _meta_set(conn, "updated_at", int(now))
            conn.commit()
        finally:
            conn.close()
        _updated_day = today
        logger.info("[Lernen] Archiv aktualisiert in %.1f s", time.monotonic() - t0)
        return True


def models_due(fitted_at_unix: Optional[float], fitted_this_run: bool) -> bool:
    """Neu lernen: nach jedem Start einmal, danach bei Tageswechsel."""
    if not fitted_this_run or not fitted_at_unix:
        return True
    return datetime.fromtimestamp(fitted_at_unix).date() != date.today()


# ---------------------------------------------------------------------------
# Lesen
# ---------------------------------------------------------------------------

def load_weather(conn, start: Optional[int] = None, end: Optional[int] = None) -> dict[str, np.ndarray]:
    q = "SELECT hour_end, ghi, dni, dhi, temp FROM weather"
    args: list = []
    if start is not None or end is not None:
        q += " WHERE hour_end >= ? AND hour_end < ?"
        args = [start or 0, end or 2 ** 40]
    rows = conn.execute(q + " ORDER BY hour_end", args).fetchall()
    arr = np.array([[np.nan if v is None else v for v in r] for r in rows], dtype=float).reshape(-1, 5)
    return {k: arr[:, i] for i, k in enumerate(("t", "ghi", "dni", "dhi", "temp"))}


def hourly_series(times: np.ndarray, values: np.ndarray, query: np.ndarray) -> np.ndarray:
    """Werte an Zeitpunkten (linear interpoliert, NaN ausserhalb / bei Luecken > 3 h)."""
    ok = ~np.isnan(values)
    times, values = times[ok], values[ok]
    if len(times) == 0:
        return np.full(len(query), np.nan)
    out = np.interp(query, times, values, left=np.nan, right=np.nan)
    idx = np.clip(np.searchsorted(times, query), 1, len(times) - 1)
    gap = times[idx] - times[idx - 1]
    out[gap > 3 * 3600] = np.nan
    return out


def ema_series(times: np.ndarray, values: np.ndarray, tau_h: float) -> np.ndarray:
    """Exponentiell gleitendes Mittel (Traegheit des Hauses), stuendliche Reihe."""
    if tau_h <= 0:
        return values.copy()
    out = np.empty_like(values)
    acc = np.nan
    prev_t = None
    for i, (t, v) in enumerate(zip(times, values)):
        if np.isnan(v):
            out[i] = acc
            continue
        if np.isnan(acc) or prev_t is None or t - prev_t > 6 * 3600:
            acc = v
        else:
            a = 1.0 - np.exp(-(t - prev_t) / 3600.0 / tau_h)
            acc = acc + a * (v - acc)
        prev_t = t
        out[i] = acc
    return out


# ---------------------------------------------------------------------------
# "Aehnliches Wetter zaehlt mehr": Kernel-Regression auf einem Raster
# ---------------------------------------------------------------------------

def _kernel(grid: np.ndarray, x: np.ndarray, sigma: float, periodic: Optional[float] = None) -> np.ndarray:
    d = grid[:, None] - x[None, :]
    if periodic:
        d = (d + periodic / 2) % periodic - periodic / 2
    return np.exp(-0.5 * (d / sigma) ** 2)


def kernel_grid(axes: Sequence[np.ndarray], xs: Sequence[np.ndarray], sigmas: Sequence[float],
                y: np.ndarray, w: np.ndarray, prior: float, strength: float):
    """Gewichtetes Mittel von y je Rasterpunkt (Nadaraya-Watson, Produktkern).

    Je naeher (aehnlicher) eine alte Stunde einem Rasterpunkt, desto mehr zaehlt
    sie. Wenig Daten in der Naehe -> Richtung `prior` geschrumpft:
        wert = (Summe w*y + strength*prior) / (Summe w + strength)
    Rueckgabe: (Werte-Raster, effektive Anzahl Stunden je Rasterpunkt).
    """
    ks = [_kernel(np.asarray(g, float), np.asarray(x, float), s) for g, x, s in zip(axes, xs, sigmas)]
    wy = w * y
    if len(ks) == 2:
        num = (ks[0] * wy) @ ks[1].T
        den = (ks[0] * w) @ ks[1].T
    elif len(ks) == 3:
        num = np.stack([(ks[0] * (ks[2][k] * wy)) @ ks[1].T for k in range(ks[2].shape[0])], axis=-1)
        den = np.stack([(ks[0] * (ks[2][k] * w)) @ ks[1].T for k in range(ks[2].shape[0])], axis=-1)
    else:
        raise ValueError("2 oder 3 Dimensionen")
    return (num + strength * prior) / (den + strength), den


def local_linear_grid(axes: Sequence[np.ndarray], xs: Sequence[np.ndarray], sigmas: Sequence[float],
                      y: np.ndarray, w: np.ndarray, prior_fn, strength: float, ridge: float = 1e-3):
    """Lokal-lineare Regression je Rasterpunkt (2D).

    Wie kernel_grid, aber an jedem Rasterpunkt wird eine gewichtete Ebene
    gefittet - so stimmen auch die Raender (z.B. Kaelte, die selten vorkam)
    und die Steigung wird aus aehnlichen Tagen gelernt. Geschrumpft Richtung
    prior_fn(x0, x1) (globales Modell), wenn in der Naehe wenig Daten liegen.
    """
    g0, g1 = (np.asarray(a, float) for a in axes)
    x0, x1 = (np.asarray(x, float) for x in xs)
    k0, k1 = _kernel(g0, x0, sigmas[0]), _kernel(g1, x1, sigmas[1])
    out = np.zeros((len(g0), len(g1)))
    neff = np.zeros_like(out)
    s0, s1 = max(sigmas[0], 1e-9), max(sigmas[1], 1e-9)
    for i, c0 in enumerate(g0):
        wi = k0[i] * w
        for j, c1 in enumerate(g1):
            ww = wi * k1[j]
            tot = ww.sum()
            neff[i, j] = tot
            prior = prior_fn(c0, c1)
            if tot < 1e-6:
                out[i, j] = prior
                continue
            # Ebene y = b0 + b1*(x0-c0)/s0 + b2*(x1-c1)/s1, Ridge auf die Steigungen
            a = np.column_stack([np.ones_like(x0), (x0 - c0) / s0, (x1 - c1) / s1])
            aw = a * ww[:, None]
            m = a.T @ aw + np.diag([0.0, ridge, ridge]) * tot
            rhs = aw.T @ y
            try:
                b0 = float(np.linalg.solve(m, rhs)[0])
            except np.linalg.LinAlgError:
                b0 = float((ww @ y) / tot)
            out[i, j] = (tot * b0 + strength * prior) / (tot + strength)
    return out, neff


def grid_lookup(axes: Sequence[Sequence[float]], table: np.ndarray, *points: np.ndarray) -> np.ndarray:
    """Multilineare Interpolation im Raster (Werte ausserhalb: Randwert)."""
    pts = [np.asarray(p, float) for p in points]
    bad = np.zeros(np.broadcast(*pts).shape, dtype=bool)
    for p in pts:
        bad = bad | np.isnan(p)
    pts = [np.where(np.isnan(p), np.asarray(ax, float)[0], p) for p, ax in zip(pts, axes)]
    idx, frac = [], []
    for ax, p in zip(axes, pts):
        ax = np.asarray(ax, float)
        pos = np.interp(p, ax, np.arange(len(ax)))
        i0 = np.clip(np.floor(pos).astype(int), 0, len(ax) - 2)
        idx.append(i0)
        frac.append(np.clip(pos - i0, 0.0, 1.0))
    out = np.zeros(np.broadcast(*pts).shape)
    nd = len(axes)
    for corner in range(2 ** nd):
        wgt = np.ones_like(out)
        sel = []
        for d in range(nd):
            bit = (corner >> d) & 1
            wgt = wgt * (frac[d] if bit else 1.0 - frac[d])
            sel.append(idx[d] + bit)
        out = out + wgt * table[tuple(sel)]
    return np.where(bad, np.nan, out)
