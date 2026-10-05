"""Waermebedarf und Solarthermie - gelernt aus der gesamten Historie.

Waermebedarf (Verbrauch aus den Speichern, kW)
----------------------------------------------
Gelernt wird aus "ruhigen" Stunden (Kessel aus inkl. Nachlauf, kein Anstieg):
dann ist die Abkuehlung der Speicher = Verbrauch. Die Stunden stammen aus dem
Lern-Archiv (core/forecast_learning) und reichen so weit zurueck wie die
Messdaten - alte Winter zaehlen also dauerhaft mit.

Einflussgroessen:
* T_eff: Aussentemperatur, traege gemittelt (das Haus reagiert verzoegert).
  Die Traegheit (0-24 h) wird aus den Daten gewaehlt.
* G_eff: Sonneneinstrahlung, ebenso gemittelt (Sonne durchs Fenster spart Heizung).

1. Globales Grundmodell (fuer Bereiche mit wenig Daten):
       kW = Grundlast + Faktor * max(0, Heizgrenze - T_eff) - Sonnenfaktor * G_eff
   Heizgrenze und Traegheit per Kreuzvalidierung gewaehlt.
2. "Aehnliches Wetter zaehlt mehr": fuer jeden Punkt eines Rasters
   (T_eff x G_eff) wird eine lokale Ebene aus den Stunden mit aehnlicher
   Temperatur und Sonne gefittet (Gauss-Gewichte). Fuer einen kalten,
   truebem Jaennertag zaehlen also kalte, truebe Stunden am meisten.
   Wenig aehnliche Stunden -> Richtung Grundmodell geschrumpft.

Solarthermie (Ertrag der Kollektoren, kW)
-----------------------------------------
Aus den Temperatur-Anstiegen der Speicher ohne Kessel tagsueber:
    Solar-Ertrag = Netto-Anstieg + (gelernter) Verbrauch in derselben Stunde
im Verhaeltnis zur gleichzeitigen PV-Leistung (gleiche Sonne, gleicher Ort).
Das Verhaeltnis haengt von Aussen- und Speichertemperatur ab (heisser Speicher,
kalte Luft -> schlechterer Kollektorwirkungsgrad) und wird ebenfalls nach
aehnlichen Bedingungen gelernt (Raster Aussen- x Speichertemperatur).

Neu gelernt: einmal nach jedem Programmstart und dann taeglich
(data/heat_demand_model.json, data/solar_thermal_model.json).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np

from . import forecast_learning as fl
from . import heating_stats as hs
from .solar_geometry import sun_position

logger = logging.getLogger(__name__)

BASE_TEMP_C = 18.0
FIT_DAYS = 60                    # nur noch fuer die Temperatur-Abfrage der Anzeige
MIN_HOURS = 36
MIN_TEMP_SPREAD_K = 3.0
MIN_QUIET_MIN_PER_HOUR = 30
GAIN_EPS_KWH = 0.05
MODEL_VERSION = 2

TAU_CANDIDATES_H = (0.0, 3.0, 6.0, 12.0, 24.0)
TB_CANDIDATES_C = tuple(np.arange(12.0, 21.0, 1.0))
GRID_T = np.arange(-20.0, 26.0, 1.0)
GRID_G = np.arange(0.0, 601.0, 50.0)
SIGMA_T, SIGMA_G = 2.0, 80.0
STRENGTH_H = 8.0

ST_GRID_TOUT = np.arange(-15.0, 36.0, 2.5)
ST_GRID_TANK = np.arange(20.0, 91.0, 5.0)
ST_SIGMA = (4.0, 6.0)
ST_MIN_PV_KW = 0.3
ST_MIN_HOURS = 30
SUN_PV_KW = 0.1                  # ab dieser PV-Leistung gilt eine Stunde als "Sonne im Spiel"

# Ausreisser (robuste Statistik: Median und MAD statt Mittelwert und Standardabweichung)
DAY_MIN_HOURS = 6                # Tag braucht so viele ruhige Stunden fuer ein Urteil
DAY_LOW_Z, DAY_LOW_RATIO = -3.0, 0.75     # Urlaub: deutlich UND statistisch auffaellig zu wenig
DAY_HIGH_Z, DAY_HIGH_RATIO = 5.0, 1.6     # extrem zu viel (Messfehler, Sensor haengt)
HOUR_Z = 6.0                     # einzelne Stunde extrem daneben

_DATA = os.path.join(os.path.dirname(__file__), "..", "..", "data")
_MODEL_PATH = os.path.join(_DATA, "heat_demand_model.json")
_SOLAR_PATH = os.path.join(_DATA, "solar_thermal_model.json")
_LOCK = threading.RLock()
_attempted_day: Optional[date] = None
_mem: dict = {}


# ---------------------------------------------------------------------------
# Modelle
# ---------------------------------------------------------------------------

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
    mean_deficit_k: float = 0.0      # mittleres (Heizgrenze - Aussen) im Lernzeitraum, fuer fehlende Temperaturen
    # ab Version 2 (lernendes Modell)
    version: int = 1
    tb_c: float = BASE_TEMP_C        # Heizgrenze
    tau_h: float = 0.0               # Traegheit des Hauses
    sun_kw_per_wm2: float = 0.0      # Einsparung durch Sonne
    grid_t: Optional[list] = None
    grid_g: Optional[list] = None
    table: Optional[list] = None     # kW je (T_eff, G_eff)
    cv_rmse_global: Optional[float] = None
    cv_rmse: Optional[float] = None
    first_day: Optional[str] = None
    typical_g: float = 0.0
    anomaly_days: Optional[list] = None      # ignorierte Tage (Urlaub, Messfehler), ISO-Datum
    outlier_hours: int = 0                   # einzelne ignorierte Stunden (Sensorfehler)

    def _global_kw(self, t, g=0.0):
        t = np.asarray(t, float)
        return np.maximum(0.0, self.base_kw + self.per_k_kw * np.maximum(0.0, self.tb_c - t)
                          - self.sun_kw_per_wm2 * np.asarray(g, float))

    def kw_at(self, outdoor_c: Optional[float], g_eff: Optional[float] = None) -> float:
        """Verbrauch bei (traeger) Aussentemperatur; ohne Temperatur: mittlerer Bedarf."""
        if outdoor_c is None or (isinstance(outdoor_c, float) and np.isnan(outdoor_c)):
            return float(self.base_kw + self.per_k_kw * self.mean_deficit_k)
        g = self.typical_g if g_eff is None or np.isnan(g_eff) else g_eff
        return float(self.kw_eff(np.array([outdoor_c]), np.array([g]))[0])

    def kw_eff(self, t_eff: np.ndarray, g_eff: np.ndarray) -> np.ndarray:
        if self.table is None:
            return self._global_kw(t_eff, 0.0 if self.version < 2 else g_eff)
        gt, gg = np.asarray(self.grid_t), np.asarray(self.grid_g)
        tab = np.asarray(self.table, float)
        t_c = np.clip(t_eff, gt[0], gt[-1])
        out = fl.grid_lookup((gt, gg), tab, t_c, np.clip(g_eff, gg[0], gg[-1]))
        # ausserhalb des Rasters mit der globalen Steigung weiter
        out = out + self.per_k_kw * np.maximum(0.0, gt[0] - t_eff)
        return np.maximum(0.0, out)

    def predictor(self, wx: Optional[dict]) -> Callable[[datetime], float]:
        """Funktion lokale Zeit (Stundenbeginn) -> kW, aus Wetter-Reihen {t, temp, ghi}."""
        if not wx or len(wx.get("t", [])) == 0:
            return lambda _t: self.kw_at(None)
        t = np.asarray(wx["t"], float)
        temp = np.asarray(wx["temp"], float)
        ghi = np.nan_to_num(np.asarray(wx.get("ghi", np.zeros_like(t)), float))
        t_eff = fl.ema_series(t, temp, self.tau_h)
        g_eff = fl.ema_series(t, ghi, max(3.0, self.tau_h))

        def f(local: datetime) -> float:
            mid = local.replace(minute=0, second=0, microsecond=0).astimezone().timestamp() + 1800.0
            te = fl.hourly_series(t, t_eff, np.array([mid]))[0]
            ge = fl.hourly_series(t, g_eff, np.array([mid]))[0]
            return self.kw_at(None if np.isnan(te) else float(te), None if np.isnan(ge) else float(ge))
        return f

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @staticmethod
    def from_dict(d: dict) -> "DemandModel":
        return DemandModel(**{k: v for k, v in d.items() if k in DemandModel.__dataclass_fields__})


@dataclass
class SolarThermalModel:
    """Solarthermie-kWh je PV-kWh, abhaengig von Aussen- und Speichertemperatur."""
    mean_ratio: float
    hours: int
    grid_tout: list = field(default_factory=list)
    grid_tank: list = field(default_factory=list)
    table: list = field(default_factory=list)
    max_ratio: float = 1.0
    fitted_at: str = ""

    def ratio(self, t_out: Optional[float], tank_c: Optional[float]) -> float:
        if not self.table or t_out is None or tank_c is None:
            return self.mean_ratio
        v = fl.grid_lookup((self.grid_tout, self.grid_tank), np.asarray(self.table, float),
                           np.array([t_out]), np.array([tank_c]))[0]
        return float(np.clip(v, 0.0, self.max_ratio))

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    @staticmethod
    def from_dict(d: dict) -> "SolarThermalModel":
        return SolarThermalModel(**{k: v for k, v in d.items() if k in SolarThermalModel.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Ruhige Stunden direkt aus Buckets (Tests / kurze Historie)
# ---------------------------------------------------------------------------

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
    """Einfaches Grundmodell (Heizgrenze 18 °C) ohne Archiv - Rueckfall und fuer kurze Historien."""
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
    mean_kw = float(np.median([kw for kw, _ in rows]))
    temps = [t for _, t in rows if t is not None]
    return DemandModel(mean_kw, 0.0, len(rows), None, min(temps) if temps else None,
                       max(temps) if temps else None, stamp, False)


# ---------------------------------------------------------------------------
# Lernen aus dem Archiv
# ---------------------------------------------------------------------------

def _hour_features(hour_start: np.ndarray, wx: dict, bmk_outdoor: np.ndarray, tau_h: float):
    """T_eff, G_eff zur Stundenmitte. Ohne Open-Meteo: BMK-Sensor (ohne Traegheit/Sonne)."""
    mid = hour_start + 1800.0
    if len(wx["t"]):
        t_eff = fl.hourly_series(wx["t"], fl.ema_series(wx["t"], wx["temp"], tau_h), mid)
        g_eff = fl.hourly_series(wx["t"], fl.ema_series(wx["t"], np.nan_to_num(wx["ghi"]), max(3.0, tau_h)), mid)
    else:
        t_eff = np.full(len(mid), np.nan)
        g_eff = np.full(len(mid), np.nan)
    miss = np.isnan(t_eff)
    t_eff[miss] = bmk_outdoor[miss]
    g_eff[np.isnan(g_eff)] = 0.0
    return t_eff, g_eff


def _global_design(t_eff, g_eff, tb):
    return np.column_stack([np.ones_like(t_eff), np.maximum(0.0, tb - t_eff), -g_eff])


def _fit_global(t_eff, g_eff, y, tb):
    from .pv_forecast import nnls
    a = _global_design(t_eff, g_eff, tb)
    coef = nnls(a, y)
    return coef


def _cv_rmse(t_eff, g_eff, y, tb, folds: np.ndarray) -> float:
    err = []
    for k in np.unique(folds):
        tr, te = folds != k, folds == k
        if tr.sum() < MIN_HOURS or te.sum() == 0:
            continue
        coef = _fit_global(t_eff[tr], g_eff[tr], y[tr], tb)
        pred = np.maximum(0.0, _global_design(t_eff[te], g_eff[te], tb) @ coef)
        err.append((pred - y[te]) ** 2)
    return float(np.sqrt(np.mean(np.concatenate(err)))) if err else float("inf")


def _robust_z(x: np.ndarray, floor: float) -> np.ndarray:
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    return (x - med) / max(mad, floor)


def find_anomalies(hour_start: np.ndarray, y: np.ndarray, bmk_outdoor: np.ndarray, wx: dict,
                   tau_h: float = 6.0, tb: float = 16.0):
    """Urlaubs-/Ausreisser-Tage und extreme Einzelstunden erkennen.

    Vorlaeufiges Grundmodell auf allen Daten -> je Tag Verhaeltnis Ist / erwartet
    (bei diesem Wetter). Ein Tag gilt als Ausreisser, wenn er BEIDES ist:
    statistisch auffaellig (robuster z-Wert aus Median/MAD aller Tage) und
    deutlich (z.B. unter 75 % des Erwarteten). So fallen Urlaube, Besuch mit
    Dauerduschen oder ein haengender Sensor heraus, normale Schwankung nicht.
    Rueckgabe: (Maske behalten, [ignorierte Tage ISO], Anzahl ignorierter Stunden).
    """
    keep = np.ones(len(y), dtype=bool)
    t_eff, g_eff = _hour_features(hour_start, wx, bmk_outdoor, tau_h)
    ok = ~np.isnan(t_eff)
    if ok.sum() < MIN_HOURS:
        return keep, [], 0
    coef = _fit_global(t_eff[ok], g_eff[ok], y[ok], tb)
    pred = np.full(len(y), np.nan)
    pred[ok] = np.maximum(0.05, _global_design(t_eff[ok], g_eff[ok], tb) @ coef)

    # 0) extreme Spitzen nach oben (Sensorsprung) - verfaelschen sonst die Tagessumme
    spike = np.zeros(len(y), dtype=bool)
    spike[np.flatnonzero(ok)[_robust_z((y - pred)[ok], 0.05) > HOUR_Z]] = True
    use = ok & ~spike

    # 1) ganze Tage (lokales Datum) - vor den Einzelstunden, damit ein Urlaubstag
    #    nicht stundenweise zerfaellt
    days = np.array([datetime.fromtimestamp(float(h)).date().toordinal() for h in hour_start])
    uniq, inv = np.unique(days, return_inverse=True)
    n = np.bincount(inv[use], minlength=len(uniq))
    sy = np.bincount(inv[use], weights=y[use], minlength=len(uniq))
    sp = np.bincount(inv[use], weights=pred[use], minlength=len(uniq))
    judged = (n >= DAY_MIN_HOURS) & (sp > 0)
    bad_day = np.zeros(len(uniq), dtype=bool)
    if judged.sum() >= 10:
        ratio = np.where(judged, sy / np.where(sp > 0, sp, 1.0), 1.0)
        z_d = np.zeros(len(uniq))
        z_d[judged] = _robust_z(np.log(np.maximum(ratio[judged], 1e-3)), 0.05)
        bad_day = judged & (((z_d < DAY_LOW_Z) & (ratio < DAY_LOW_RATIO)) |
                            ((z_d > DAY_HIGH_Z) & (ratio > DAY_HIGH_RATIO)))

    # 2) einzelne Stunden (Sensorspruenge) auf den uebrigen Tagen
    rest = use & ~bad_day[inv]
    bad_h = spike.copy()
    if rest.sum() >= MIN_HOURS:
        z_h = _robust_z((y - pred)[rest], 0.05)
        bad_h[np.flatnonzero(rest)[np.abs(z_h) > HOUR_Z]] = True
    keep = ~bad_h & ~bad_day[inv]
    anomaly_days = [date.fromordinal(int(d)).isoformat() for d in uniq[bad_day]]
    if anomaly_days:
        logger.info("[Waermebedarf] %d auffaellige Tage ignoriert (z.B. Urlaub): %s", len(anomaly_days),
                    ", ".join(anomaly_days[-10:]))
    return keep, anomaly_days, int(bad_h.sum())


def fit_from_archive(hour_start: np.ndarray, demand_kw: np.ndarray, bmk_outdoor: np.ndarray,
                     wx: dict) -> Optional[DemandModel]:
    ok = ~np.isnan(demand_kw)
    hour_start, demand_kw, bmk_outdoor = hour_start[ok], demand_kw[ok], bmk_outdoor[ok]
    if len(demand_kw) < MIN_HOURS:
        return None
    # Ausreisser (Urlaub, Sensorfehler) robust erkennen statt pauschal abschneiden
    y = demand_kw
    clean, anomaly_days, outlier_hours = find_anomalies(hour_start, y, bmk_outdoor, wx)
    hour_start, y, bmk_outdoor = hour_start[clean], y[clean], bmk_outdoor[clean]
    if len(y) < MIN_HOURS:
        return None
    folds = ((hour_start // 86400).astype(int) // 3) % 5          # 3-Tages-Bloecke
    best = None
    for tau in TAU_CANDIDATES_H:
        t_eff, g_eff = _hour_features(hour_start, wx, bmk_outdoor, tau)
        m = ~np.isnan(t_eff)
        if m.sum() < MIN_HOURS:
            continue
        for tb in TB_CANDIDATES_C:
            e = _cv_rmse(t_eff[m], g_eff[m], y[m], tb, folds[m])
            if best is None or e < best[0]:
                best = (e, tau, float(tb))
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if best is None:
        return None
    cv_global, tau, tb = best
    t_eff, g_eff = _hour_features(hour_start, wx, bmk_outdoor, tau)
    m = ~np.isnan(t_eff)
    t_eff, g_eff, y, hs_ = t_eff[m], g_eff[m], y[m], hour_start[m]
    spread = float(t_eff.max() - t_eff.min())
    coef = _fit_global(t_eff, g_eff, y, tb)
    if spread < MIN_TEMP_SPREAD_K:
        coef = np.array([float(np.median(y)), 0.0, 0.0])
    model = DemandModel(
        base_kw=float(coef[0]), per_k_kw=float(coef[1]), hours=int(len(y)), r2=None,
        t_min=float(t_eff.min()), t_max=float(t_eff.max()), fitted_at=stamp,
        temperature_dependent=spread >= MIN_TEMP_SPREAD_K and coef[1] > 0,
        mean_deficit_k=float(np.mean(np.maximum(0.0, tb - t_eff))), version=MODEL_VERSION,
        tb_c=tb, tau_h=tau, sun_kw_per_wm2=float(coef[2]), cv_rmse_global=cv_global,
        first_day=datetime.fromtimestamp(float(hs_.min())).date().isoformat(), typical_g=float(np.median(g_eff)),
        anomaly_days=anomaly_days, outlier_hours=outlier_hours)

    # "Aehnliches Wetter zaehlt mehr": lokale Ebenen auf dem Raster, Grundmodell als Prior
    prior = lambda c0, c1: float(model._global_kw(np.array([c0]), np.array([c1]))[0])
    w = np.ones_like(y)
    table, _neff = fl.local_linear_grid((GRID_T, GRID_G), (t_eff, g_eff), (SIGMA_T, SIGMA_G), y, w,
                                        prior, STRENGTH_H)
    model.grid_t, model.grid_g = GRID_T.tolist(), GRID_G.tolist()
    model.table = np.round(np.maximum(table, 0.0), 4).tolist()

    # Guete: Kreuzvalidierung des Rasters (gleiche Bloecke)
    err = []
    for k in np.unique(folds[m]):
        tr, te = folds[m] != k, folds[m] == k
        if tr.sum() < MIN_HOURS or te.sum() == 0:
            continue
        sub = DemandModel(**{**model.to_dict(), "table": None})
        c = _fit_global(t_eff[tr], g_eff[tr], y[tr], tb)
        sub.base_kw, sub.per_k_kw, sub.sun_kw_per_wm2 = float(c[0]), float(c[1]), float(c[2])
        pr = lambda c0, c1, s=sub: float(s._global_kw(np.array([c0]), np.array([c1]))[0])
        tab_k, _ = fl.local_linear_grid((GRID_T, GRID_G), (t_eff[tr], g_eff[tr]), (SIGMA_T, SIGMA_G),
                                        y[tr], np.ones(int(tr.sum())), pr, STRENGTH_H)
        sub.table, sub.grid_t, sub.grid_g = tab_k.tolist(), model.grid_t, model.grid_g
        err.append((sub.kw_eff(t_eff[te], g_eff[te]) - y[te]) ** 2)
        if len(err) >= 3:            # 3 Folds reichen als Schaetzung (spart Rechenzeit am Pi)
            break
    if err:
        model.cv_rmse = float(np.sqrt(np.mean(np.concatenate(err))))
    pred = model.kw_eff(t_eff, g_eff)
    ss_tot = float(np.sum((y - y.mean()) ** 2)) or 1.0
    model.r2 = 1.0 - float(np.sum((pred - y) ** 2)) / ss_tot
    logger.info("[Waermebedarf] Gelernt aus %d h seit %s: Traegheit %g h, Heizgrenze %g °C, "
                "Fehler (CV) %.2f kW global -> %s kW aehnlich", model.hours, model.first_day, tau, tb,
                cv_global, f"{model.cv_rmse:.2f}" if model.cv_rmse is not None else "?")
    return model


def fit_solar_thermal(hour_start, free_kwh, free_min, kessel_min, tank_c, bmk_outdoor, pv_kw,
                      wx: dict, demand: DemandModel, lat: float, lon: float) -> Optional[SolarThermalModel]:
    """Solar-Ertrag = Netto-Anstieg + Verbrauch, ins Verhaeltnis zur PV gesetzt."""
    mid = hour_start + 1800.0
    el, _ = sun_position(mid, lat, lon)
    ok = (el > 5.0) & (free_min >= 45.0) & (kessel_min <= 0.0) & (pv_kw >= ST_MIN_PV_KW) & ~np.isnan(tank_c)
    if ok.sum() < ST_MIN_HOURS:
        return None
    t_eff, g_eff = _hour_features(hour_start[ok], wx, bmk_outdoor[ok], demand.tau_h)
    t_now = _hour_features(hour_start[ok], wx, bmk_outdoor[ok], 0.0)[0]
    use_demand = demand.kw_eff(t_eff, g_eff)
    gain = free_kwh[ok] / (free_min[ok] / 60.0) + use_demand
    ratio = np.clip(gain / pv_kw[ok], 0.0, None)
    w = pv_kw[ok]
    good = ~np.isnan(t_now)
    if good.sum() >= ST_MIN_HOURS:
        good &= np.abs(_robust_z(np.where(good, ratio, np.nanmedian(ratio)), 0.02)) <= HOUR_Z
    if good.sum() < ST_MIN_HOURS:
        return None
    ratio, w, t_out, tank = ratio[good], w[good], t_now[good], tank_c[ok][good]
    mean_ratio = float(np.sum(w * ratio) / np.sum(w))
    # Grundmodell: linear in (Speicher - Aussen), gewichtet
    dt = tank - t_out
    a = np.column_stack([np.ones_like(dt), dt]) * np.sqrt(w)[:, None]
    coef = np.linalg.lstsq(a, ratio * np.sqrt(w), rcond=None)[0]
    prior = lambda c0, c1: float(max(0.0, coef[0] + coef[1] * (c1 - c0)))
    table, _ = fl.local_linear_grid((ST_GRID_TOUT, ST_GRID_TANK), (t_out, tank), ST_SIGMA, ratio, w,
                                    prior, 5.0 * float(np.mean(w)))
    mx = float(np.percentile(ratio, 95)) * 1.2
    model = SolarThermalModel(mean_ratio=mean_ratio, hours=int(len(ratio)), grid_tout=ST_GRID_TOUT.tolist(),
                              grid_tank=ST_GRID_TANK.tolist(), table=np.round(np.clip(table, 0, mx), 4).tolist(),
                              max_ratio=mx, fitted_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    logger.info("[Solarthermie] Gelernt aus %d Sonnenstunden: Ø %.2f kWh Waerme je kWh PV", model.hours, mean_ratio)
    return model


def _load_archive():
    conn = fl.connect()
    try:
        heat = np.array([[np.nan if v is None else v for v in r] for r in conn.execute(
            "SELECT hour_start, quiet_kwh, quiet_min, free_kwh, free_min, kessel_min, tank_c, outdoor_c "
            "FROM heat_hours ORDER BY hour_start").fetchall()], dtype=float).reshape(-1, 8)
        pv = dict(conn.execute("SELECT hour_end, pv_kw FROM pv_hours").fetchall())
        wx = fl.load_weather(conn)
    finally:
        conn.close()
    return heat, pv, wx


def learn(store, cfg: Optional[hs.StorageConfig] = None, allow_network: bool = True):
    """Archiv nachfuehren und beide Modelle neu lernen."""
    from .weather import load_weather_config
    wcfg = load_weather_config()
    fl.update(store, wcfg, cfg, allow_network=allow_network)
    heat, pv, wx = _load_archive()
    if len(heat) == 0:
        return None, None
    hour_start = heat[:, 0]
    quiet_min = heat[:, 2]
    demand_kw = np.where(quiet_min >= MIN_QUIET_MIN_PER_HOUR,
                         np.maximum(0.0, heat[:, 1]) / np.maximum(quiet_min, 1e-9) * 60.0, np.nan)
    # Stunden mit Sonne ausschliessen: dort verdeckt der Solar-Ertrag einen Teil des
    # Verbrauchs (Speicher kuehlt langsamer ab) -> Bedarf waere zu niedrig gelernt.
    pv_kw = np.array([pv.get(int(h) + 3600, np.nan) for h in hour_start], dtype=float)
    el, _ = sun_position(hour_start + 1800.0, wcfg.latitude, wcfg.longitude)
    sunny = (pv_kw > SUN_PV_KW) | (np.isnan(pv_kw) & (el > 5.0))
    demand_kw[sunny] = np.nan
    demand = fit_from_archive(hour_start, demand_kw, heat[:, 7], wx)
    solar = None
    if demand is not None:
        solar = fit_solar_thermal(hour_start, heat[:, 3], heat[:, 4], heat[:, 5], heat[:, 6], heat[:, 7],
                                  pv_kw, wx, demand, wcfg.latitude, wcfg.longitude)
    return demand, solar


# ---------------------------------------------------------------------------
# Laden / Speichern / oeffentliche API
# ---------------------------------------------------------------------------

def _read(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save_json(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception:
        pass


def _load_cached() -> Optional[DemandModel]:
    d = _read(_MODEL_PATH)
    try:
        return DemandModel.from_dict(d) if d else None
    except Exception:
        return None


def _save(model: DemandModel) -> None:
    _save_json(_MODEL_PATH, model.to_dict())


def get_models(store, cfg: Optional[hs.StorageConfig] = None, force: bool = False,
               allow_network: bool = True) -> tuple[Optional[DemandModel], Optional[SolarThermalModel]]:
    """(Waermebedarf, Solarthermie). Neu gelernt einmal nach Programmstart und dann taeglich."""
    global _attempted_day
    with _LOCK:
        if force or _attempted_day != date.today():
            _attempted_day = date.today()
            t0 = time.monotonic()
            try:
                demand, solar = learn(store, cfg, allow_network=allow_network)
            except Exception as exc:
                logger.warning("[Waermebedarf] Lernen fehlgeschlagen: %s", exc, exc_info=True)
                demand, solar = None, None
            if demand is None:
                # Rueckfall: einfaches Modell aus den letzten Wochen
                try:
                    cfg_ = cfg or hs.load_storage_config()
                    now = datetime.now()
                    demand = fit(quiet_hours(hs.load_buckets(store, now - timedelta(days=FIT_DAYS), now), cfg_))
                except Exception:
                    demand = None
            if demand is not None:
                _save(demand)
                _mem["demand"] = demand
            if solar is not None:
                _save_json(_SOLAR_PATH, solar.to_dict())
                _mem["solar"] = solar
            logger.info("[Waermebedarf] Lernen fertig in %.1f s", time.monotonic() - t0)
        if "demand" not in _mem:
            _mem["demand"] = _load_cached()
        if "solar" not in _mem:
            d = _read(_SOLAR_PATH)
            try:
                _mem["solar"] = SolarThermalModel.from_dict(d) if d else None
            except Exception:
                _mem["solar"] = None
        return _mem.get("demand"), _mem.get("solar")


def get_model(store, cfg: Optional[hs.StorageConfig] = None, temps_utc: Optional[dict] = None,
              force: bool = False) -> Optional[DemandModel]:
    """Kompatibel zur alten API: nur das Bedarfsmodell."""
    return get_models(store, cfg, force=force)[0]


_wx_cache: dict = {}


def weather_series(allow_network: bool = True, past_days: int = 3, forecast_days: int = 8) -> dict:
    """Wetter-Reihen {t (Stundenende, Unix), temp, ghi} fuer Prognosen - 1 h gecacht.

    Neue Werte landen auch im Lern-Archiv. Ohne Netz: was das Archiv hat.
    """
    from .weather import load_weather_config
    if _wx_cache.get("at", 0) > time.time() - 3600:
        return _wx_cache["wx"]
    cfg = load_weather_config()
    if allow_network and cfg.enabled:
        try:
            rows = fl.fetch_weather(cfg, past_days, forecast_days)
            conn = fl.connect()
            try:
                fl.store_weather(conn, rows, "forecast")
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            logger.info("[Waermebedarf] Wetterabruf fehlgeschlagen, nutze Archiv: %s", exc)
    conn = fl.connect()
    try:
        wx = fl.load_weather(conn, int(time.time() - (past_days + 2) * 86400), int(time.time() + 17 * 86400))
    finally:
        conn.close()
    _wx_cache.update(at=time.time(), wx=wx)
    return wx


def hourly_demand(model: DemandModel, start_local: datetime, hours: int, temps_utc: dict,
                  rate_fn: Optional[Callable[[datetime], float]] = None) -> list[tuple[datetime, float, Optional[float]]]:
    """[(Stundenbeginn lokal, kW, °C)] fuer die naechsten `hours` Stunden."""
    out = []
    t = start_local.replace(minute=0, second=0, microsecond=0)
    for _ in range(hours):
        temp = _temp_for(t, temps_utc, None)
        kw = rate_fn(t) if rate_fn is not None else model.kw_at(temp)
        out.append((t, kw, temp))
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
                 solar_by_day: Optional[dict] = None, now: Optional[datetime] = None, days: int = 7,
                 rate_fn: Optional[Callable[[datetime], float]] = None) -> WeekOutlook:
    now = now or datetime.now()
    hourly = hourly_demand(model, now, 24 * days, temps_utc, rate_fn)
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
