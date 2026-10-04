"""PV-Prognose aus der Open-Meteo-Einstrahlungsvorhersage.

Die Anlage muss nicht beschrieben werden (kWp, Neigung, Ausrichtung): das
Modul kalibriert sich selbst gegen die eigenen Fronius-Messwerte.

Modell
------
Fuer eine feste Neigung wird die Einstrahlung auf die geneigte Flaeche
(global_tilted_irradiance, W/m^2) fuer mehrere Ausrichtungen abgefragt
(Ost ... Sued ... West). Die gemessene PV-Leistung wird als nicht-negative
Linearkombination dieser Einstrahlungen angenaehert:

    P_pv(t) ~= sum_i  k_i * GTI(t, azimut_i)        (k_i >= 0, in kW pro W/m^2)

Damit werden Sued-, Ost-West- und gemischte Daecher automatisch abgebildet.
Die Neigung mit dem kleinsten Fehler gewinnt. Nach oben wird auf die
groesste beobachtete Stundenleistung begrenzt (Wechselrichter-Limit).

Zeitbasis: Open-Meteo liefert Stundenmittel der *vorangegangenen* Stunde,
abgefragt in UTC. Ein Wert mit Zeitstempel 12:00 UTC ist also das Mittel
von 11:00-12:00 UTC und wird mit dem PV-Mittel desselben Intervalls
verglichen.

Netzwerkzugriffe passieren nur im Aufrufer-Thread (die Tabs rufen das aus
einem Worker-Thread auf) und sind gecacht:
  * Kalibrierung: data/pv_forecast_model.json, Erneuerung alle 7 Tage
  * Prognose:     data/pv_forecast_cache.json, Erneuerung alle 30 Minuten
Bei fehlendem Netz wird der letzte Cache verwendet bzw. None geliefert.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
import requests

from .time_utils import DB_TS_FORMAT, parse_db_ts
from .weather import WeatherConfig, load_weather_config

logger = logging.getLogger(__name__)

API_URL = "https://api.open-meteo.com/v1/forecast"
AZIMUTHS: tuple[int, ...] = (-90, -45, 0, 45, 90)   # 0 = Sued, -90 = Ost, 90 = West
TILTS: tuple[int, ...] = (15, 30, 45)

CALIBRATION_DAYS = 60            # wie viele Tage Historie fuer den Fit
MIN_CALIBRATION_HOURS = 48       # mindestens so viele Sonnenstunden mit Messwerten
MODEL_MAX_AGE_S = 7 * 24 * 3600
FORECAST_MAX_AGE_S = 30 * 60
FORECAST_PAST_DAYS = 7           # zum Vergleich Prognose/Ist der letzten Tage
FORECAST_DAYS = 2                # heute + morgen
CACHE_KEEP_DAYS = 60

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
MODEL_PATH = _DATA_DIR / "pv_forecast_model.json"
CACHE_PATH = _DATA_DIR / "pv_forecast_cache.json"

_LOCK = threading.Lock()
_session = requests.Session()


# ---------------------------------------------------------------------------
# Open-Meteo
# ---------------------------------------------------------------------------

def _fetch_gti(cfg: WeatherConfig, tilt: int, azimuth: int, past_days: int, forecast_days: int) -> dict[datetime, float]:
    """Stuendliche Einstrahlung auf die geneigte Flaeche, Schluessel = Stundenende (UTC, aware)."""
    resp = _session.get(
        API_URL,
        params={
            "latitude": cfg.latitude,
            "longitude": cfg.longitude,
            "hourly": "global_tilted_irradiance",
            "tilt": tilt,
            "azimuth": azimuth,
            "past_days": past_days,
            "forecast_days": forecast_days,
            "timezone": "UTC",
        },
        timeout=max(5.0, cfg.timeout_s),
    )
    resp.raise_for_status()
    hourly = (resp.json() or {}).get("hourly") or {}
    out: dict[datetime, float] = {}
    for t, v in zip(hourly.get("time") or [], hourly.get("global_tilted_irradiance") or []):
        if v is None:
            continue
        try:
            ts = datetime.fromisoformat(str(t)).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        out[ts] = max(0.0, float(v))
    return out


def _fetch_bases(cfg: WeatherConfig, tilt: int, azimuths: Iterable[int], past_days: int, forecast_days: int):
    """Matrix der Einstrahlungen: (Zeitpunkte, Matrix[n_zeit, n_azimut])."""
    azimuths = list(azimuths)
    series = [_fetch_gti(cfg, tilt, az, past_days, forecast_days) for az in azimuths]
    times = sorted(set.intersection(*(set(s) for s in series))) if series else []
    mat = np.array([[s[t] for s in series] for t in times], dtype=float).reshape(len(times), len(azimuths))
    return times, mat


# ---------------------------------------------------------------------------
# Messwerte
# ---------------------------------------------------------------------------

def hourly_pv_means(store, start_utc: datetime, end_utc: datetime) -> dict[datetime, float]:
    """Mittlere PV-Leistung (kW) je Stunde, Schluessel = Stundenende (UTC, aware)."""
    conn = getattr(store, "conn", None)
    if conn is None:
        return {}
    rows = conn.execute(
        "SELECT timestamp, pv_power FROM fronius WHERE timestamp >= ? AND timestamp < ? AND pv_power IS NOT NULL",
        (start_utc.strftime(DB_TS_FORMAT), end_utc.strftime(DB_TS_FORMAT)),
    ).fetchall()
    sums: dict[datetime, list[float]] = {}
    for ts, pv in rows:
        dt = parse_db_ts(ts)
        if dt is None:
            continue
        kw = float(pv)
        if kw > 200.0:            # alte Daten in W
            kw /= 1000.0
        hour_end = dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        acc = sums.setdefault(hour_end, [0.0, 0])
        acc[0] += max(0.0, kw)
        acc[1] += 1
    return {k: v[0] / v[1] for k, v in sums.items() if v[1] > 0}


# ---------------------------------------------------------------------------
# Fit
# ---------------------------------------------------------------------------

def nnls(a: np.ndarray, b: np.ndarray, max_iter: int = 200) -> np.ndarray:
    """Nicht-negative kleinste Quadrate (Lawson-Hanson), ohne scipy."""
    m, n = a.shape
    x = np.zeros(n)
    passive = np.zeros(n, dtype=bool)
    w = a.T @ (b - a @ x)
    tol = 1e-10 * max(1.0, float(np.abs(a).max(initial=0.0)))
    it = 0
    while (~passive).any() and w[~passive].max(initial=-1.0) > tol and it < max_iter:
        it += 1
        j = int(np.argmax(np.where(passive, -np.inf, w)))
        passive[j] = True
        while True:
            z = np.zeros(n)
            idx = np.flatnonzero(passive)
            z[idx] = np.linalg.lstsq(a[:, idx], b, rcond=None)[0]
            if (z[idx] > 0).all():
                x = z
                break
            neg = idx[z[idx] <= 0]
            alpha = np.min(x[neg] / (x[neg] - z[neg] + 1e-300))
            x = x + alpha * (z - x)
            passive &= x > tol
            x[~passive] = 0.0
            if not passive.any():
                break
        w = a.T @ (b - a @ x)
    return x


def fit_model(times: list[datetime], bases: np.ndarray, pv: dict[datetime, float]) -> Optional[dict]:
    """Fit fuer eine Neigung. Gibt Koeffizienten und Fehlermasse zurueck."""
    rows = [i for i, t in enumerate(times) if t in pv and bases[i].max() > 20.0]
    if len(rows) < MIN_CALIBRATION_HOURS:
        return None
    a = bases[rows]
    b = np.array([pv[times[i]] for i in rows], dtype=float)
    coef = nnls(a, b)
    pred = a @ coef
    rmse = float(np.sqrt(np.mean((pred - b) ** 2)))
    ss_tot = float(np.sum((b - b.mean()) ** 2)) or 1.0
    r2 = 1.0 - float(np.sum((pred - b) ** 2)) / ss_tot
    return {"coef": [float(c) for c in coef], "rmse_kw": rmse, "r2": r2, "hours": len(rows),
            "cap_kw": float(b.max()) * 1.05}


def calibrate(store, cfg: Optional[WeatherConfig] = None, days: int = CALIBRATION_DAYS) -> Optional[dict]:
    """Kalibriert das Modell gegen die Fronius-Historie (Netzwerk!)."""
    cfg = cfg or load_weather_config()
    days = max(3, min(92, int(days)))
    now = datetime.now(timezone.utc)
    pv = hourly_pv_means(store, now - timedelta(days=days + 1), now)
    if len(pv) < MIN_CALIBRATION_HOURS:
        logger.info("[PV-Prognose] Zu wenig PV-Historie fuer Kalibrierung (%d h)", len(pv))
        return None
    best = None
    for tilt in TILTS:
        times, bases = _fetch_bases(cfg, tilt, AZIMUTHS, past_days=days, forecast_days=1)
        res = fit_model(times, bases, pv)
        if res and (best is None or res["rmse_kw"] < best["rmse_kw"]):
            best = {**res, "tilt": tilt}
    if best is None:
        return None
    best.update({
        "azimuths": list(AZIMUTHS),
        "calibrated_at": now.isoformat(timespec="seconds"),
        "days": days,
        "latitude": cfg.latitude,
        "longitude": cfg.longitude,
    })
    logger.info("[PV-Prognose] Kalibriert: Neigung %s°, R²=%.2f, RMSE=%.2f kW, %d h",
                best["tilt"], best["r2"], best["rmse_kw"], best["hours"])
    return best


def predict(model: dict, times: list[datetime], bases: np.ndarray, azimuths: list[int]) -> dict[datetime, float]:
    coef_by_az = dict(zip(model["azimuths"], model["coef"]))
    coef = np.array([coef_by_az.get(az, 0.0) for az in azimuths], dtype=float)
    pred = np.clip(bases @ coef, 0.0, model.get("cap_kw") or None)
    return {t: float(p) for t, p in zip(times, pred)}


# ---------------------------------------------------------------------------
# Cache + oeffentliche API
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Optional[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        logger.warning("[PV-Prognose] Konnte %s nicht schreiben: %s", path.name, exc)


def _model_is_fresh(model: Optional[dict], cfg: WeatherConfig) -> bool:
    if not model:
        return False
    if abs(model.get("latitude", 0) - cfg.latitude) > 1e-3 or abs(model.get("longitude", 0) - cfg.longitude) > 1e-3:
        return False
    ts = parse_db_ts(model.get("calibrated_at"))
    return ts is not None and (datetime.now(timezone.utc) - ts).total_seconds() < MODEL_MAX_AGE_S


def get_model(store, cfg: Optional[WeatherConfig] = None, allow_network: bool = True) -> Optional[dict]:
    cfg = cfg or load_weather_config()
    model = _read_json(MODEL_PATH)
    if _model_is_fresh(model, cfg) or not allow_network or not cfg.enabled:
        return model
    try:
        new_model = calibrate(store, cfg)
    except Exception as exc:
        logger.info("[PV-Prognose] Kalibrierung fehlgeschlagen: %s", exc)
        new_model = None
    if new_model:
        _write_json(MODEL_PATH, new_model)
        return new_model
    return model   # alter Stand ist besser als nichts


def get_forecast(store, cfg: Optional[WeatherConfig] = None, allow_network: bool = True) -> Optional[dict]:
    """Stuendliche Prognose {Stundenende UTC (aware): kW}, oder None.

    Gecacht; Netzwerk nur wenn der Cache aelter als 30 Minuten ist.
    Enthaelt auch die letzten Tage (aus der Einstrahlung berechnet), damit
    Prognose und Ist fuer vergangene Tage verglichen werden koennen.
    """
    cfg = cfg or load_weather_config()
    with _LOCK:
        cache = _read_json(CACHE_PATH) or {}
        values: dict[str, float] = dict(cache.get("values") or {})
        fetched = cache.get("fetched_at", 0.0)
        stale = (time.time() - float(fetched or 0.0)) > FORECAST_MAX_AGE_S
        if stale and allow_network and cfg.enabled:
            model = get_model(store, cfg, allow_network=True)
            if model:
                try:
                    azs = [az for az, c in zip(model["azimuths"], model["coef"]) if c > 0] or [0]
                    times, bases = _fetch_bases(cfg, int(model["tilt"]), azs, FORECAST_PAST_DAYS, FORECAST_DAYS)
                    for t, kw in predict(model, times, bases, azs).items():
                        values[t.strftime(DB_TS_FORMAT)] = round(kw, 4)
                    cutoff = (datetime.now(timezone.utc) - timedelta(days=CACHE_KEEP_DAYS)).strftime(DB_TS_FORMAT)
                    values = {k: v for k, v in values.items() if k >= cutoff}
                    _write_json(CACHE_PATH, {"fetched_at": time.time(), "values": values})
                except Exception as exc:
                    logger.info("[PV-Prognose] Abruf fehlgeschlagen, nutze Cache: %s", exc)
        if not values:
            return None
        out = {}
        for k, v in values.items():
            dt = parse_db_ts(k)
            if dt is not None:
                out[dt] = float(v)
        return out


def day_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """Lokaler Kalendertag -> [Start, Ende) in UTC (DST-korrekt)."""
    start = datetime.combine(day, dtime.min).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), dtime.min).astimezone(timezone.utc)
    return start, end


def forecast_for_day(forecast: Optional[dict], day: date) -> list[tuple[datetime, float]]:
    """Prognose eines lokalen Tages als [(Stundenmitte lokal naiv, kW)], sortiert."""
    if not forecast:
        return []
    start, end = day_bounds_utc(day)
    out = []
    for hour_end, kw in forecast.items():
        mid = hour_end - timedelta(minutes=30)
        if start <= mid < end:
            out.append((mid.astimezone().replace(tzinfo=None), kw))
    out.sort()
    return out


def forecast_kwh(points: list[tuple[datetime, float]]) -> Optional[float]:
    """Summe der Stundenmittel = kWh (jeder Wert deckt genau eine Stunde ab)."""
    if not points:
        return None
    return float(sum(kw for _, kw in points))
