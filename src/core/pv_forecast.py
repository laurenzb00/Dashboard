"""PV-Prognose, die aus der eigenen Historie lernt.

Die Anlage muss nicht beschrieben werden (kWp, Neigung, Ausrichtung) - alles
wird aus den Fronius-Messwerten und dem Wetter-Archiv (core/forecast_learning)
gelernt, aus ALLEN gesammelten Stunden.

1. Physik (global)
   Sonnenstand je Stunde (core/solar_geometry) + Open-Meteo-Strahlung
   (GHI/DNI/DHI) -> Einstrahlung auf Modulflaechen verschiedener Ausrichtung
   (Ost ... Sued ... West) und Neigung, mit Temperaturverlust der Module
   (-0,37 %/K Zelltemperatur, Zelle ~ Luft + 25 K bei 800 W/m^2):
       P(t) ~= sum_i k_i * POA_i(t) * f_T(t)        (k_i >= 0)
   Die Neigung mit dem kleinsten Fehler gewinnt; Ost-West- oder gemischte
   Daecher ergeben sich von selbst. Ausreisser (Schnee, Ausfall, Abregelung)
   werden erkannt und beim Fit ignoriert.

2. "Aehnliches Wetter zaehlt mehr" (Korrektur)
   Verhaeltnis gemessen / Physik, gelernt je Sonnenstand (Azimut, Hoehe) und
   Bewoelkung (Klarheitsindex kt): Fuer jeden Rasterpunkt zaehlen alte Stunden
   mit aehnlichem Sonnenstand und aehnlichem Himmel am meisten. So werden
   Verschattung (Baeume, Nachbarhaus, Gaube), Morgendunst, Reflexionen und
   Fehler der Wettervorhersage bei bestimmten Lagen gelernt. Wenig Daten in
   der Naehe -> Faktor geht gegen 1 (reine Physik).

Neu gelernt wird einmal nach jedem Programmstart und dann einmal pro Tag
(im Worker-Thread des aufrufenden Tabs). Modell: data/pv_forecast_model.json.
Prognose: eine Open-Meteo-Abfrage, gecacht 30 min in data/pv_forecast_cache.json.

Zeitbasis: Open-Meteo-Strahlung = Mittel der vorangegangenen Stunde (UTC).
Ein Wert mit Zeitstempel 12:00 UTC gilt fuer 11:00-12:00 und wird mit dem
PV-Mittel desselben Intervalls verglichen; der Sonnenstand wird zur
Intervallmitte berechnet.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from . import forecast_learning as fl
from . import forecast_log
from .solar_geometry import clearness, poa, sun_position
from .time_utils import DB_TS_FORMAT, parse_db_ts
from .weather import WeatherConfig, load_weather_config

logger = logging.getLogger(__name__)

MODEL_VERSION = 2
AZIMUTHS: tuple[int, ...] = (-90, -60, -30, 0, 30, 60, 90)   # 0 = Sued, -90 = Ost, 90 = West
TILTS: tuple[int, ...] = tuple(range(10, 65, 5))
GAMMA_PER_K = -0.0037            # Leistungs-Temperaturkoeffizient kristalliner Module
NOCT_K_PER_WM2 = 25.0 / 800.0    # Zellerwaermung ueber Luft
MIN_ELEV_DEG = 2.0
MIN_CALIBRATION_HOURS = 48

# Korrektur-Raster (aehnliches Wetter)
CORR_AZ = np.arange(-130.0, 131.0, 5.0)
CORR_EL = np.arange(2.0, 69.0, 2.0)
CORR_KT = np.arange(0.05, 0.86, 0.1)
CORR_SIGMA = (7.0, 3.0, 0.08)
CORR_STRENGTH_H = 3.0            # so viele "typische Stunden" wiegt der Ausgangswert 1,0
CORR_CLIP = (0.2, 1.5)

FORECAST_MAX_AGE_S = 30 * 60
FORECAST_PAST_DAYS = 7
FORECAST_DAYS = 2
CACHE_KEEP_DAYS = 60

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
MODEL_PATH = _DATA_DIR / "pv_forecast_model.json"
CACHE_PATH = _DATA_DIR / "pv_forecast_cache.json"

_LOCK = threading.RLock()
_attempted_day: Optional[date] = None
_model_mem: Optional[dict] = None


# ---------------------------------------------------------------------------
# Merkmale
# ---------------------------------------------------------------------------

def plane_features(t_end, ghi, dni, dhi, temp, lat: float, lon: float, tilt: float, azimuths):
    """Je Ausrichtung: Einstrahlung * Temperaturfaktor (kW/m^2) und deren Direktanteil."""
    t_mid = np.asarray(t_end, float) - 1800.0
    el, az = sun_position(t_mid, lat, lon)
    temp = np.where(np.isnan(temp), 10.0, temp)
    cols, beams = [], []
    for a in azimuths:
        tot, beam = poa(ghi, dni, dhi, el, az, tilt, a)
        f_t = 1.0 + GAMMA_PER_K * (temp + NOCT_K_PER_WM2 * tot - 25.0)
        cols.append(tot * f_t / 1000.0)
        beams.append(beam * f_t / 1000.0)
    return np.column_stack(cols), np.column_stack(beams), el, az


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


def _robust_fit(x: np.ndarray, y: np.ndarray, cap: float):
    """NNLS, dann Ausreisser (Schnee/Ausfall/Abregelung) entfernen und erneut fitten."""
    coef = nnls(x, y)
    pred = x @ coef
    res = y - pred
    mad = float(np.median(np.abs(res - np.median(res)))) * 1.4826 or 1e-3
    keep = np.abs(res) <= 4.0 * mad
    keep &= ~((pred > 0.15 * cap) & (y < 0.3 * pred))          # Schnee/Ausfall
    keep &= ~((y >= 0.98 * cap) & (pred > 1.1 * y))             # Wechselrichter-Limit
    if keep.sum() >= MIN_CALIBRATION_HOURS:
        coef = nnls(x[keep], y[keep])
        pred = x @ coef
    rmse = float(np.sqrt(np.mean((pred[keep] - y[keep]) ** 2)))
    return coef, keep, rmse


# ---------------------------------------------------------------------------
# Lernen
# ---------------------------------------------------------------------------

def training_data(conn) -> dict:
    rows = conn.execute(
        "SELECT p.hour_end, p.pv_kw, w.ghi, w.dni, w.dhi, w.temp FROM pv_hours p "
        "JOIN weather w ON w.hour_end = p.hour_end WHERE w.ghi IS NOT NULL ORDER BY p.hour_end").fetchall()
    arr = np.array([[np.nan if v is None else v for v in r] for r in rows], dtype=float).reshape(-1, 6)
    return {k: arr[:, i] for i, k in enumerate(("t", "pv", "ghi", "dni", "dhi", "temp"))}


def fit_from_data(d: dict, lat: float, lon: float) -> Optional[dict]:
    if len(d["t"]) == 0:
        return None
    dni = np.where(np.isnan(d["dni"]), 0.0, d["dni"])
    dhi = np.where(np.isnan(d["dhi"]), d["ghi"], d["dhi"])
    el, _ = sun_position(d["t"] - 1800.0, lat, lon)
    use = (el > MIN_ELEV_DEG) & ~np.isnan(d["pv"]) & ~np.isnan(d["ghi"])
    if use.sum() < MIN_CALIBRATION_HOURS:
        logger.info("[PV-Prognose] Zu wenig Lernstunden (%d)", int(use.sum()))
        return None
    t, y = d["t"][use], d["pv"][use]
    ghi, dni, dhi, temp = d["ghi"][use], dni[use], dhi[use], d["temp"][use]
    cap = float(np.percentile(y, 99.9)) * 1.02 if len(y) > 20 else float(y.max()) * 1.05

    best = None
    for tilt in TILTS:
        x, _, _, _ = plane_features(t, ghi, dni, dhi, temp, lat, lon, tilt, AZIMUTHS)
        coef, keep, rmse = _robust_fit(x, y, cap)
        if best is None or rmse < best["rmse"]:
            best = {"tilt": tilt, "coef": coef, "keep": keep, "rmse": rmse}
    tilt, coef, keep = best["tilt"], best["coef"], best["keep"]
    x, beam, el_u, az_u = plane_features(t, ghi, dni, dhi, temp, lat, lon, tilt, AZIMUTHS)
    phys = np.clip(x @ coef, 0.0, cap)

    # Tage mit Schnee/Ausfall (ganzer Tag weit unter Physik) - Verschattung dagegen
    # betrifft nur einzelne Stunden und bleibt fuer die Korrektur erhalten.
    day = (t // 86400).astype(int)
    day_idx = day - day.min()
    d_meas = np.bincount(day_idx, weights=y)
    d_phys = np.bincount(day_idx, weights=phys)
    bad_day = (d_phys > 1.0) & (d_meas < 0.4 * d_phys)
    good = ~bad_day[day_idx] & ~((y >= 0.98 * cap) & (phys > 1.1 * y))

    # --- Korrektur nach aehnlichem Wetter (Sonnenstand + Bewoelkung)
    kt = clearness(ghi, t - 1800.0, el_u)
    sel = good & (phys > 0.03 * cap)
    corr_table = None
    if sel.sum() >= MIN_CALIBRATION_HOURS:
        ratio = np.clip(y[sel] / phys[sel], 0.0, 2.0)
        w = phys[sel]
        table, neff = fl.kernel_grid((CORR_AZ, CORR_EL, CORR_KT), (az_u[sel], el_u[sel], kt[sel]), CORR_SIGMA,
                                     ratio, w, prior=1.0, strength=CORR_STRENGTH_H * float(np.mean(w)))
        corr_table = np.clip(table, *CORR_CLIP)
    model = {
        "version": MODEL_VERSION, "tilt": tilt, "azimuths": list(AZIMUTHS), "coef": [float(c) for c in coef],
        "cap_kw": cap, "hours": int(good.sum()), "outliers": int((~good).sum()),
        "first_hour": int(t.min()), "last_hour": int(t.max()), "latitude": lat, "longitude": lon,
        "fitted_at": time.time(), "fitted_at_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if corr_table is not None:
        model["corr"] = {"az": CORR_AZ.tolist(), "el": CORR_EL.tolist(), "kt": CORR_KT.tolist(),
                         "table": np.round(corr_table, 3).tolist()}
    pred = predict_arrays(model, t, ghi, dni, dhi, temp)
    for name, p in (("phys", phys), ("final", pred)):
        err = p[good] - y[good]
        model[f"rmse_{name}_kw"] = float(np.sqrt(np.mean(err ** 2)))
    ss_tot = float(np.sum((y[good] - y[good].mean()) ** 2)) or 1.0
    model["r2"] = 1.0 - float(np.sum((pred[good] - y[good]) ** 2)) / ss_tot
    # Tagessummen-Fehler (aussagekraeftiger als Stunden)
    days = (t[good] // 86400).astype(int)
    dm = np.bincount(days - days.min(), weights=y[good])
    dp = np.bincount(days - days.min(), weights=pred[good])
    nz = dm > 0.5
    if nz.any():
        model["day_mape_pct"] = float(np.mean(np.abs(dp[nz] - dm[nz]) / dm[nz]) * 100.0)
    logger.info("[PV-Prognose] Gelernt aus %d h: Neigung %d°, R²=%.3f, RMSE %.2f -> %.2f kW (Aehnlich-Korrektur), "
                "Tagesfehler %.0f %%", model["hours"], tilt, model["r2"], model["rmse_phys_kw"],
                model["rmse_final_kw"], model.get("day_mape_pct", float("nan")))
    return model


def predict_arrays(model: dict, t_end, ghi, dni, dhi, temp) -> np.ndarray:
    t_end = np.asarray(t_end, float)
    ghi = np.nan_to_num(np.asarray(ghi, float))
    dni = np.nan_to_num(np.asarray(dni, float))
    dhi = np.where(np.isnan(np.asarray(dhi, float)), ghi, dhi)
    temp = np.asarray(temp, float)
    x, _, el, az = plane_features(t_end, ghi, dni, dhi, temp, model["latitude"], model["longitude"],
                                  model["tilt"], model["azimuths"])
    pred = x @ np.asarray(model["coef"], float)
    corr = model.get("corr")
    if corr:
        kt = clearness(ghi, t_end - 1800.0, el)
        f = fl.grid_lookup((corr["az"], corr["el"], corr["kt"]), np.asarray(corr["table"], float), az, el, kt)
        pred = pred * np.where(el > MIN_ELEV_DEG, f, 1.0)
    return np.clip(pred, 0.0, model.get("cap_kw") or None)


def calibrate(store, cfg: Optional[WeatherConfig] = None, allow_network: bool = True) -> Optional[dict]:
    cfg = cfg or load_weather_config()
    fl.update(store, cfg, allow_network=allow_network)
    conn = fl.connect()
    try:
        data = training_data(conn)
        model = fit_from_data(data, cfg.latitude, cfg.longitude)
        if model:
            # Typische Fehler der Wettervorhersage (Prognose vom Vortag vs. gemessen)
            try:
                model["bias"] = forecast_log.learn_pv_bias(cfg.latitude, cfg.longitude, conn=conn)
            except Exception as exc:
                logger.info("[PV-Prognose] Vorhersagefehler nicht gelernt: %s", exc)
    finally:
        conn.close()
    return model


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
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except Exception as exc:
        logger.warning("[PV-Prognose] Konnte %s nicht schreiben: %s", path.name, exc)


def _usable(model: Optional[dict], cfg: WeatherConfig) -> bool:
    return bool(model) and model.get("version") == MODEL_VERSION \
        and abs(model.get("latitude", 0) - cfg.latitude) < 1e-3 and abs(model.get("longitude", 0) - cfg.longitude) < 1e-3


def get_model(store, cfg: Optional[WeatherConfig] = None, allow_network: bool = True,
              force: bool = False) -> Optional[dict]:
    """Gelerntes Modell. Neu gelernt einmal nach Programmstart und dann taeglich."""
    global _attempted_day, _model_mem
    cfg = cfg or load_weather_config()
    with _LOCK:
        model = _model_mem if _model_mem is not None else _read_json(MODEL_PATH)
        if not _usable(model, cfg):
            model = None
        if force or _attempted_day != date.today():
            _attempted_day = date.today()
            try:
                from .perf_monitor import timed
                with timed("pv.lernen", min_ms=0):
                    new_model = calibrate(store, cfg, allow_network=allow_network)
            except Exception as exc:
                logger.warning("[PV-Prognose] Lernen fehlgeschlagen: %s", exc)
                new_model = None
            if new_model:
                _write_json(MODEL_PATH, new_model)
                _write_json(CACHE_PATH, {})        # Prognose mit neuem Modell neu rechnen
                model = new_model
        _model_mem = model
        return model


def get_forecast(store, cfg: Optional[WeatherConfig] = None, allow_network: bool = True) -> Optional[dict]:
    """Stuendliche Prognose {Stundenende UTC (aware): kW}, oder None.

    Gecacht; Netzwerk nur wenn der Cache aelter als 30 Minuten ist. Enthaelt
    auch die letzten Tage, damit Prognose und Ist verglichen werden koennen.
    """
    cfg = cfg or load_weather_config()
    with _LOCK:
        model = get_model(store, cfg, allow_network=allow_network)
        cache = _read_json(CACHE_PATH) or {}
        values: dict[str, float] = dict(cache.get("values") or {})
        stale = (time.time() - float(cache.get("fetched_at", 0.0) or 0.0)) > FORECAST_MAX_AGE_S
        if stale and allow_network and cfg.enabled and model:
            try:
                rows = fl.fetch_weather(cfg, FORECAST_PAST_DAYS, FORECAST_DAYS)
                if rows:
                    arr = np.array([[np.nan if v is None else v for v in r] for r in rows], dtype=float)
                    raw = predict_arrays(model, arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4])
                    # Lern-Korrektur der Wettervorhersage nur fuer kommende Stunden; vergangene
                    # Stunden beruhen schon auf (fast) gemessenem Wetter.
                    future = arr[:, 0] - 3600 >= time.time()
                    corrected = forecast_log.apply_bias(model.get("bias"), arr[:, 0], raw, arr[:, 1],
                                                        model["latitude"], model["longitude"])
                    pred = np.where(future, np.clip(corrected, 0.0, model.get("cap_kw") or None), raw)
                    try:
                        forecast_log.record_pv(arr[:, 0], pred, arr[:, 1], kw_raw=raw)
                    except Exception as exc:
                        logger.info("[PV-Prognose] Prognose nicht protokolliert: %s", exc)
                    for ts, kw in zip(arr[:, 0], pred):
                        key = datetime.fromtimestamp(int(ts), timezone.utc).strftime(DB_TS_FORMAT)
                        values[key] = round(float(kw), 4)
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
