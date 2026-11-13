"""Wettermodelle fuer die PV-Prognose vergleichen.

Die Tagesprognose streut vor allem wegen des Wetters (Nebel/Wolken), nicht wegen des
PV-Modells. Open-Meteo bietet mehrere Wettermodelle (DWD ICON-D2/ICON-EU, ECMWF IFS, GFS ...).
Welches am Standort am besten trifft, laesst sich nur aus eigenen Daten sagen:

* log_models():   holt (gedrosselt, alle 3 h) die Strahlungsvorhersage je Modell, rechnet sie mit dem
                  gelernten PV-Modell in kW um und speichert sie je Zielstunde und Vorlauf
                  (d1 = Stand vom Vortag, d0 = Stand am selben Tag) in Tabelle pv_model_log.
* compare():      Tagessummen je Modell gegen den gemessenen Ertrag (nur Tage, an denen ALLE
                  Modelle vollstaendig vorliegen, damit der Vergleich fair ist), plus Mittelwert.
* status_text():  kurze Zeile fuer die Kopfzeile im Ertrag-Tab.

Das Modell fuer die Anzeige bleibt "best_match", bis in config/weather.json
"pv_wettermodell" auf ein anderes Modell gesetzt wird (Tabelle zeigt, welches).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Optional, Sequence

import numpy as np

from . import forecast_learning as fl
from . import forecast_log
from .weather import WeatherConfig, load_weather_config

logger = logging.getLogger(__name__)

LOG_INTERVAL_S = 3 * 3600
KEEP_DAYS = 400
MIN_HOURS_PER_DAY = 8
MIN_DAYS_FOR_TEXT = 5
ENSEMBLE = "mittel"
BASELINE = "best_match"

_NAMES = {"best_match": "Standard", "icon_d2": "ICON-D2", "icon_eu": "ICON-EU", "ecmwf_ifs025": "IFS",
          "gfs_seamless": "GFS", ENSEMBLE: "Mittel"}

_running = threading.Lock()


def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS pv_model_log (model TEXT NOT NULL, target_hour INTEGER NOT NULL, "
                 "lead TEXT NOT NULL, kw REAL, ghi REAL, made_at INTEGER, PRIMARY KEY (model, target_hour, lead))")
    # 13.11.2026: Direktstrahlung und Temperatur je Modell mitschreiben (senkrechte Module haengen an der DNI)
    have = {r[1] for r in conn.execute("PRAGMA table_info(pv_model_log)")}
    for col in ("dni", "temp"):
        if col not in have:
            conn.execute(f"ALTER TABLE pv_model_log ADD COLUMN {col} REAL")


def record(conn, model_name: str, t_end: Sequence[float], kw: Sequence[float], ghi: Sequence[float],
           made_at: Optional[float] = None, dni: Optional[Sequence[float]] = None,
           temp: Optional[Sequence[float]] = None) -> int:
    made_at = time.time() if made_at is None else made_at
    n = len(t_end)
    dni = [None] * n if dni is None else dni
    temp = [None] * n if temp is None else temp
    num = lambda v: None if v is None or v != v else float(v)
    rows = []
    for t, p, g, dn, tc in zip(t_end, kw, ghi, dni, temp):
        lead = forecast_log.lead_class(made_at, float(t))
        if lead and p == p:
            rows.append((model_name, int(t), lead, float(p), num(g), int(made_at), num(dn), num(tc)))
    _ensure(conn)
    conn.executemany("INSERT OR REPLACE INTO pv_model_log (model, target_hour, lead, kw, ghi, made_at, dni, temp) "
                     "VALUES (?,?,?,?,?,?,?,?)", rows)
    return len(rows)


def log_models(pv_model: dict, cfg: Optional[WeatherConfig] = None, models: Optional[Sequence[str]] = None,
               fetch=None, predict=None, now: Optional[float] = None, force: bool = False) -> int:
    """Alle konfigurierten Modelle abrufen und protokollieren. Gibt die Zahl gespeicherter Zeilen zurueck.

    fetch/predict sind austauschbar (Tests). Gedrosselt ueber meta.models_logged_at.
    """
    cfg = cfg or load_weather_config()
    models = tuple(cfg.log_models if models is None else models)
    if not models or not cfg.enabled or not pv_model:
        return 0
    if fetch is None:
        fetch = fl.fetch_weather
    if predict is None:
        from . import pv_forecast
        predict = pv_forecast.predict_arrays
    now = time.time() if now is None else now
    conn = fl.connect()
    saved = 0
    try:
        _ensure(conn)
        last = fl._meta_get(conn, "models_logged_at")
        if not force and last and now - float(last) < LOG_INTERVAL_S:
            return 0
        # Das Standardmodell wird immer mitgefuehrt (Vergleichsbasis), auch wenn es nicht aufgelistet ist.
        todo = [BASELINE] + [m for m in models if m != BASELINE]
        for name in todo:
            try:
                rows = fetch(cfg, 0, 2, None if name == BASELINE else name)
                if not rows:
                    continue
                arr = np.array([[np.nan if v is None else v for v in r] for r in rows], dtype=float)
                kw = predict(pv_model, arr[:, 0], arr[:, 1], arr[:, 2], arr[:, 3], arr[:, 4])
                saved += record(conn, name, arr[:, 0], kw, arr[:, 1], made_at=now, dni=arr[:, 2], temp=arr[:, 4])
            except Exception as exc:
                logger.info("[Wettermodelle] %s nicht abrufbar: %s", name, exc)
        cutoff = int(now - KEEP_DAYS * 86400)
        conn.execute("DELETE FROM pv_model_log WHERE target_hour < ?", (cutoff,))
        fl._meta_set(conn, "models_logged_at", int(now))
        conn.commit()
    finally:
        conn.close()
    if saved:
        logger.info("[Wettermodelle] %d Stundenwerte protokolliert (%s)", saved, ", ".join(todo))
    return saved


def log_models_async(pv_model: dict, cfg: Optional[WeatherConfig] = None) -> None:
    """Im Hintergrund-Thread (blockiert die Prognose nicht, hoechstens ein Lauf gleichzeitig)."""
    if not _running.acquire(blocking=False):
        return

    def _run():
        try:
            log_models(pv_model, cfg)
        except Exception as exc:
            logger.info("[Wettermodelle] Protokollierung fehlgeschlagen: %s", exc)
        finally:
            _running.release()

    threading.Thread(target=_run, name="wettermodelle", daemon=True).start()


def compare(days: int = 21, lead: str = "d1", conn=None, now: Optional[float] = None) -> Optional[dict]:
    """Tagessummen je Modell gegen gemessen. Nur Tage, an denen jedes Modell >= 8 Stunden hat."""
    now = time.time() if now is None else now
    own = conn is None
    conn = conn or fl.connect()
    try:
        _ensure(conn)
        rows = conn.execute(
            "SELECT m.model, m.target_hour, m.kw, p.pv_kw FROM pv_model_log m "
            "JOIN pv_hours p ON p.hour_end = m.target_hour WHERE m.lead = ? AND m.target_hour >= ? "
            "ORDER BY m.target_hour", (lead, int(now - (days + 1) * 86400))).fetchall()
    finally:
        if own:
            conn.close()
    if not rows:
        return None
    today = datetime.fromtimestamp(now).date().toordinal()
    per: dict = {}                     # (model, day) -> [kw_sum, ist_sum, n]
    hour_kw: dict = {}                 # (day, hour) -> {model: kw}  fuer den Mittelwert
    ist_h: dict = {}
    for model, th, kw, ist in rows:
        if kw is None or ist is None:
            continue
        day = datetime.fromtimestamp(th - 1800).date().toordinal()
        if day >= today:
            continue
        a = per.setdefault((model, day), [0.0, 0.0, 0])
        a[0] += kw
        a[1] += ist
        a[2] += 1
        hour_kw.setdefault((day, th), {})[model] = kw
        ist_h[(day, th)] = ist
    models = sorted({m for m, _ in per})
    if not models:
        return None
    days_ok = {d for (_, d) in per}
    for m in models:
        days_ok &= {d for (mm, d), a in per.items() if mm == m and a[2] >= MIN_HOURS_PER_DAY}
    # Mittel ueber alle Modelle je Stunde (nur Stunden, in denen alle Modelle vorliegen)
    ens: dict = {}
    for (day, th), by in hour_kw.items():
        if day in days_ok and len(by) == len(models):
            e = ens.setdefault(day, [0.0, 0.0, 0])
            e[0] += float(np.mean(list(by.values())))
            e[1] += ist_h[(day, th)]
            e[2] += 1
    out = {}
    for m in models + ([ENSEMBLE] if len(models) > 1 else []):
        fc, ist = [], []
        for d in sorted(days_ok):
            a = ens.get(d) if m == ENSEMBLE else per.get((m, d))
            if a and a[2] >= MIN_HOURS_PER_DAY and max(a[0], a[1]) >= 2.0:
                fc.append(a[0])
                ist.append(a[1])
        if not fc:
            continue
        fc, ist = np.array(fc), np.array(ist)
        out[m] = {"days": len(fc), "mape_pct": float(np.mean(np.abs(fc - ist) / np.maximum(ist, 1.0)) * 100.0),
                  "bias_pct": float((fc.sum() - ist.sum()) / max(ist.sum(), 1.0) * 100.0),
                  "mae_kwh": float(np.mean(np.abs(fc - ist)))}
    if not out:
        return None
    best = min(out, key=lambda k: out[k]["mape_pct"])
    return {"models": out, "best": best, "days": max(v["days"] for v in out.values())}


def status_text(days: int = 21) -> str:
    """z.B. 'Wettermodelle (12 Tage): Mittel 24 % · ICON-D2 27 % · Standard 33 %' oder ''."""
    res = compare(days)
    if not res or res["days"] < MIN_DAYS_FOR_TEXT:
        return ""
    order = sorted(res["models"].items(), key=lambda kv: kv[1]["mape_pct"])
    parts = [f"{_NAMES.get(k, k)} {v['mape_pct']:.0f} %" for k, v in order[:3]]
    return f"Wettermodelle ({res['days']} Tage): " + " · ".join(parts)
