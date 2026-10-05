"""Prognose vs. Ist: gespeicherte Vorhersagen auswerten und daraus lernen.

* record_pv(): bei jedem Prognose-Abruf (alle 30 min) werden die Vorhersagen
  fuer kommende Stunden gespeichert - je Zielstunde zwei Staende:
    d1 = letzter Stand vom VORTAG (so, wie man am Abend fuer morgen plant)
    d0 = letzter Stand am selben Tag, vor der Stunde
* pv_skill(): Tagessummen d1-Prognose vs. gemessen der letzten Tage
  (mittlere Abweichung in %, Tendenz zu hoch/zu niedrig).
* learn_pv_bias(): typische Fehler der WETTERVORHERSAGE lernen - Verhaeltnis
  gemessen / Vortagsprognose, nach aehnlichen Lagen (vorhergesagte Bewoelkung,
  Sonnenhoehe, Jahreszeit). Beispiel Hochnebel im Herbst: Vorhersage "sonnig",
  real trueb -> fuer solche Lagen wird die Prognose kuenftig gedaempft.
  Wenig Daten -> Faktor 1 (keine Korrektur).
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence

import numpy as np

from . import forecast_learning as fl
from .solar_geometry import clearness, sun_position

logger = logging.getLogger(__name__)

BIAS_KT = np.arange(0.05, 0.86, 0.1)
BIAS_EL = np.arange(2.0, 67.0, 4.0)
BIAS_SEASON = np.arange(-1.0, 1.01, 0.25)        # cos(Jahreszeit): -1 Winter ... +1 Sommer
BIAS_SIGMA = (0.1, 6.0, 0.3)
BIAS_STRENGTH_H = 10.0
BIAS_CLIP = (0.3, 1.5)
BIAS_MIN_DAYS = 10
KEEP_DAYS = 730


def _ensure(conn) -> None:
    # kw = angezeigte Prognose (inkl. Lern-Korrektur), kw_raw = ohne Korrektur (daraus wird gelernt,
    # sonst wuerde die Korrektur ihre eigene Wirkung "lernen")
    conn.execute("CREATE TABLE IF NOT EXISTS pv_forecast_log (target_hour INTEGER NOT NULL, lead TEXT NOT NULL, "
                 "kw REAL, kw_raw REAL, ghi REAL, made_at INTEGER, PRIMARY KEY (target_hour, lead))")


def lead_class(made_at: float, target_hour_end: float) -> Optional[str]:
    """'d1' Ziel am Folgetag, 'd0' spaeter am selben Tag (lokal), sonst None."""
    if target_hour_end - 3600 < made_at:
        return None                                   # Stunde hat schon begonnen
    made_day = datetime.fromtimestamp(made_at).date()
    target_day = datetime.fromtimestamp(target_hour_end - 1800).date()
    if target_day == made_day:
        return "d0"
    if target_day == made_day + timedelta(days=1):
        return "d1"
    return None


def record_pv(t_end: Sequence[float], kw: Sequence[float], ghi: Sequence[float], made_at: Optional[float] = None,
              conn=None, kw_raw: Optional[Sequence[float]] = None) -> int:
    made_at = time.time() if made_at is None else made_at
    kw_raw = kw if kw_raw is None else kw_raw
    rows = []
    for t, p, pr, g in zip(t_end, kw, kw_raw, ghi):
        lead = lead_class(made_at, float(t))
        if lead:
            rows.append((int(t), lead, float(p), float(pr), None if g is None or g != g else float(g), int(made_at)))
    own = conn is None
    conn = conn or fl.connect()
    try:
        _ensure(conn)
        conn.executemany("INSERT OR REPLACE INTO pv_forecast_log (target_hour, lead, kw, kw_raw, ghi, made_at) "
                         "VALUES (?,?,?,?,?,?)", rows)
        conn.execute("DELETE FROM pv_forecast_log WHERE target_hour < ?", (int(made_at - KEEP_DAYS * 86400),))
        conn.commit()
    finally:
        if own:
            conn.close()
    return len(rows)


def _pairs(conn, lead: str, since: float, raw: bool = False) -> np.ndarray:
    _ensure(conn)
    col = "f.kw_raw" if raw else "f.kw"
    rows = conn.execute(
        f"SELECT f.target_hour, {col}, f.ghi, p.pv_kw FROM pv_forecast_log f JOIN pv_hours p ON p.hour_end = f.target_hour "
        "WHERE f.lead = ? AND f.target_hour >= ? ORDER BY f.target_hour", (lead, int(since))).fetchall()
    return np.array([[np.nan if v is None else v for v in r] for r in rows], dtype=float).reshape(-1, 4)


def pv_skill(days: int = 14, lead: str = "d1", conn=None) -> Optional[dict]:
    """Tagessummen Prognose vs. Ist (nur vollstaendige Tage mit nennenswerter Sonne)."""
    own = conn is None
    conn = conn or fl.connect()
    try:
        arr = _pairs(conn, lead, time.time() - (days + 1) * 86400)
    finally:
        if own:
            conn.close()
    if len(arr) == 0:
        return None
    day = np.array([datetime.fromtimestamp(t - 1800).date().toordinal() for t in arr[:, 0]])
    today = date.today().toordinal()
    res = []
    for d in np.unique(day):
        if d >= today:
            continue
        m = day == d
        if m.sum() < 8:                     # Tag nicht vollstaendig protokolliert
            continue
        fc, ist = float(np.nansum(arr[m, 1])), float(np.nansum(arr[m, 3]))
        if max(fc, ist) < 2.0:
            continue
        res.append((date.fromordinal(int(d)), fc, ist))
    if not res:
        return None
    fc = np.array([r[1] for r in res])
    ist = np.array([r[2] for r in res])
    return {
        "days": len(res),
        "mape_pct": float(np.mean(np.abs(fc - ist) / np.maximum(ist, 1.0)) * 100.0),
        "bias_pct": float((fc.sum() - ist.sum()) / max(ist.sum(), 1.0) * 100.0),
        "mae_kwh": float(np.mean(np.abs(fc - ist))),
        "per_day": res,
    }


def _season(t) -> np.ndarray:
    doy = np.array([datetime.fromtimestamp(float(x)).timetuple().tm_yday for x in np.atleast_1d(t)], dtype=float)
    return np.cos(2.0 * math.pi * (doy - 172.0) / 365.25)


def learn_pv_bias(lat: float, lon: float, conn=None) -> Optional[dict]:
    """Korrekturtabelle gemessen / Vortagsprognose nach aehnlichen Lagen."""
    own = conn is None
    conn = conn or fl.connect()
    try:
        arr = _pairs(conn, "d1", 0, raw=True)
    finally:
        if own:
            conn.close()
    if len(arr) == 0:
        return None
    t, fc, ghi, ist = arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3]
    el, _ = sun_position(t - 1800.0, lat, lon)
    ok = (el > 2.0) & (fc > 0.2) & ~np.isnan(ist) & ~np.isnan(ghi)
    n_days = len({datetime.fromtimestamp(x).date() for x in t[ok]})
    if n_days < BIAS_MIN_DAYS:
        return None
    kt = clearness(ghi[ok], t[ok] - 1800.0, el[ok])
    ratio = np.clip(ist[ok] / fc[ok], 0.0, 2.0)
    w = fc[ok]
    table, neff = fl.kernel_grid((BIAS_KT, BIAS_EL, BIAS_SEASON), (kt, el[ok], _season(t[ok])), BIAS_SIGMA,
                                 ratio, w, prior=1.0, strength=BIAS_STRENGTH_H * float(np.mean(w)))
    table = np.clip(table, *BIAS_CLIP)
    pred = fc[ok] * fl.grid_lookup((BIAS_KT, BIAS_EL, BIAS_SEASON), table, kt, el[ok], _season(t[ok]))
    before = float(np.sqrt(np.mean((fc[ok] - ist[ok]) ** 2)))
    after = float(np.sqrt(np.mean((pred - ist[ok]) ** 2)))
    logger.info("[PV-Prognose] Wettervorhersage-Fehler gelernt aus %d Tagen: RMSE %.2f -> %.2f kW", n_days, before, after)
    return {"kt": BIAS_KT.tolist(), "el": BIAS_EL.tolist(), "season": BIAS_SEASON.tolist(),
            "table": np.round(table, 3).tolist(), "days": n_days, "rmse_before": before, "rmse_after": after}


def apply_bias(bias: Optional[dict], t_end, kw, ghi, lat: float, lon: float) -> np.ndarray:
    kw = np.asarray(kw, float)
    if not bias:
        return kw
    t_end = np.asarray(t_end, float)
    el, _ = sun_position(t_end - 1800.0, lat, lon)
    kt = clearness(np.nan_to_num(np.asarray(ghi, float)), t_end - 1800.0, el)
    f = fl.grid_lookup((bias["kt"], bias["el"], bias["season"]), np.asarray(bias["table"], float),
                       kt, el, _season(t_end))
    return kw * np.where((el > 2.0) & ~np.isnan(f), f, 1.0)
