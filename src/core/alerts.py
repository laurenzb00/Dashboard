"""Warnmeldungen: PV liefert zu wenig, Haus verbraucht ungewoehnlich viel Waerme.

Laeuft im Hintergrund (start_background, alle 15 min). Meldungen gehen ueber
Home Assistant aufs Handy (config/homeassistant.json: "notify_service", z.B.
"mobile_app_pixel_8"; ohne Eintrag als Benachrichtigung in Home Assistant).
Jede Art von Meldung hoechstens alle COOLDOWN_H Stunden. Alle Meldungen landen
ausserdem in data/forecast_learning.db (Tabelle alerts) und im Log.

PV-Stoerung
    In den letzten 2 vollen Stunden hat die Prognose (berechnet mit dem fuer diese
    Stunden schon fast gemessenen Wetter) jeweils >= 1,5 kW erwartet, gemessen
    wurden aber < 35 %. Wolken, die die Vorhersage nicht kannte, schaffen das
    selten zwei Stunden lang bei so grosser Abweichung - Schnee, ein Defekt oder
    eine ausgeloeste Sicherung schon. Bei vollem Akku (Abregelung) keine Meldung.

Waermeverbrauch
    Die letzten 12 Stunden: Verbrauch aus den ruhigen, sonnenfreien Stunden
    (wie beim Lernen) gegen das gelernte Modell bei diesem Wetter. Meldung, wenn
    >= 8 Stunden auswertbar und Verbrauch >= 1,8-fach UND >= 2 kW ueber normal
    (die Messung schwankt stark - erst ein so deutlicher Unterschied ist sicher).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import numpy as np

from . import forecast_learning as fl
from . import heating_stats as hs
from .time_utils import DB_TS_FORMAT

logger = logging.getLogger(__name__)

INTERVAL_S = 15 * 60
COOLDOWN_H = 12
PV_MIN_EXPECTED_KW = 1.5
PV_MAX_RATIO = 0.35
PV_SOC_CURTAIL = 97.0
HEAT_WINDOW_H = 12
HEAT_MIN_HOURS = 8.0
HEAT_RATIO = 1.8
HEAT_EXCESS_KW = 2.0

_started = False


# ---------------------------------------------------------------------------
# Melden
# ---------------------------------------------------------------------------

def _ensure(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS alerts (ts INTEGER, kind TEXT, title TEXT, message TEXT)")


def recent_alerts(hours: float = 24, conn=None) -> list[tuple]:
    own = conn is None
    conn = conn or fl.connect()
    try:
        _ensure(conn)
        return conn.execute("SELECT ts, kind, title, message FROM alerts WHERE ts >= ? ORDER BY ts DESC",
                            (int(time.time() - hours * 3600),)).fetchall()
    finally:
        if own:
            conn.close()


def _default_sender(title: str, message: str) -> bool:
    from .homeassistant import HomeAssistantClient, load_homeassistant_config
    cfg = load_homeassistant_config()
    if not cfg:
        return False
    return HomeAssistantClient(cfg).notify(title, message)


def raise_alert(kind: str, title: str, message: str, sender: Optional[Callable[[str, str], bool]] = None,
                now: Optional[float] = None, conn=None) -> bool:
    """Meldung senden, ausser dieselbe Art kam in den letzten COOLDOWN_H Stunden schon."""
    now = time.time() if now is None else now
    own = conn is None
    conn = conn or fl.connect()
    try:
        _ensure(conn)
        last = conn.execute("SELECT MAX(ts) FROM alerts WHERE kind = ?", (kind,)).fetchone()[0]
        if last is not None and now - last < COOLDOWN_H * 3600:
            return False
        conn.execute("INSERT INTO alerts (ts, kind, title, message) VALUES (?,?,?,?)", (int(now), kind, title, message))
        conn.commit()
    finally:
        if own:
            conn.close()
    logger.warning("[Meldung] %s: %s", title, message)
    try:
        (sender or _default_sender)(title, message)
    except Exception as exc:
        logger.info("[Meldung] Senden an Home Assistant fehlgeschlagen: %s", exc)
    return True


# ---------------------------------------------------------------------------
# PV
# ---------------------------------------------------------------------------

def _hourly_fronius(store, start_utc: datetime, end_utc: datetime) -> dict:
    """{Stundenende UTC (aware): (PV kW, max SOC %)}"""
    conn = getattr(store, "conn", None)
    if conn is None:
        return {}
    rows = conn.execute(
        "SELECT substr(timestamp, 1, 13), AVG(CASE WHEN pv_power > 200 THEN pv_power / 1000.0 "
        "WHEN pv_power < 0 THEN 0 ELSE pv_power END), MAX(soc), COUNT(*) FROM fronius "
        "WHERE timestamp >= ? AND timestamp < ? AND pv_power IS NOT NULL GROUP BY 1",
        (start_utc.strftime(DB_TS_FORMAT), end_utc.strftime(DB_TS_FORMAT))).fetchall()
    out = {}
    for h, kw, soc, n in rows:
        try:
            end = datetime.strptime(h, "%Y-%m-%d %H").replace(tzinfo=timezone.utc) + timedelta(hours=1)
        except (TypeError, ValueError):
            continue
        if n >= 3:
            out[end] = (float(kw or 0.0), None if soc is None else float(soc))
    return out


def check_pv(store, forecast: Optional[dict], now: Optional[datetime] = None) -> Optional[tuple[str, str]]:
    """(Titel, Text) wenn die PV in den letzten 2 vollen Stunden deutlich zu wenig lieferte."""
    if not forecast:
        return None
    now = now or datetime.now(timezone.utc)
    last_end = now.replace(minute=0, second=0, microsecond=0)
    hours = [last_end - timedelta(hours=1), last_end]
    measured = _hourly_fronius(store, hours[0] - timedelta(hours=1), last_end)
    rows = []
    for h in hours:
        exp = forecast.get(h)
        got = measured.get(h)
        if exp is None or got is None:
            return None
        kw, soc = got
        if exp < PV_MIN_EXPECTED_KW or kw >= PV_MAX_RATIO * exp:
            return None
        if soc is not None and soc >= PV_SOC_CURTAIL:
            return None                       # Akku voll: Wechselrichter regelt evtl. ab
        rows.append((h, exp, kw))
    exp_sum = sum(r[1] for r in rows)
    got_sum = sum(r[2] for r in rows)
    pct = got_sum / exp_sum * 100.0
    since = (rows[0][0] - timedelta(hours=1)).astimezone().strftime("%H:%M")
    if got_sum < 0.05 * len(rows):
        hint = "Die Anlage liefert gar nichts – Wechselrichter aus oder Sicherung ausgelöst?"
    else:
        hint = "Mögliche Ursachen: Schnee, Defekt eines Strangs, Verschattung oder unerwartet dichte Wolken."
    return ("PV liefert zu wenig",
            f"Seit {since} nur {got_sum:.1f} kWh statt erwartet {exp_sum:.1f} kWh ({pct:.0f} %). {hint}")


# ---------------------------------------------------------------------------
# Waermeverbrauch
# ---------------------------------------------------------------------------

def recent_demand(store, cfg: hs.StorageConfig, hours: int = HEAT_WINDOW_H,
                  now: Optional[datetime] = None) -> tuple[Optional[float], float, list]:
    """(Verbrauch kW, ausgewertete Stunden, [Stundenbeginn Unix]) der letzten `hours` Stunden."""
    now = now or datetime.now()
    end = now.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours)
    buckets = hs.load_buckets(store, start - timedelta(hours=1), end)
    rows = [r for r in fl.heat_hour_rows(buckets, cfg) if start.timestamp() <= r[0] < end.timestamp()]
    pv = _hourly_fronius(store, start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    kwh = minutes = 0.0
    used = []
    for hour_start, q_kwh, q_min, *_ in rows:
        h_end = datetime.fromtimestamp(hour_start + 3600, timezone.utc)
        if q_min < 30 or pv.get(h_end, (0.0, None))[0] > 0.1:
            continue
        kwh += max(0.0, q_kwh)
        minutes += q_min
        used.append(hour_start)
    if minutes <= 0:
        return None, 0.0, []
    return kwh / minutes * 60.0, minutes / 60.0, used


def check_heat(store, model, wx: Optional[dict], cfg: Optional[hs.StorageConfig] = None,
               now: Optional[datetime] = None) -> Optional[tuple[str, str]]:
    if model is None:
        return None
    cfg = cfg or hs.load_storage_config()
    actual, hours, used = recent_demand(store, cfg, now=now)
    if actual is None or hours < HEAT_MIN_HOURS:
        return None
    rate = model.predictor(wx) if wx else (lambda _t: model.kw_at(None))
    expected = float(np.mean([rate(datetime.fromtimestamp(h)) for h in used]))
    if actual < HEAT_RATIO * expected or actual - expected < HEAT_EXCESS_KW:
        return None
    return ("Ungewöhnlich hoher Wärmeverbrauch",
            f"In den letzten {HEAT_WINDOW_H} h im Schnitt {actual:.1f} kW statt üblicher {expected:.1f} kW bei diesem "
            f"Wetter ({actual / max(expected, 0.1):.1f}-fach). Fenster offen, Pumpe läuft durch oder Leck?")


# ---------------------------------------------------------------------------
# Hintergrund
# ---------------------------------------------------------------------------

def run_checks(store) -> list[str]:
    from .perf_monitor import timed
    with timed("meldungen.pruefen", min_ms=200):
        return _run_checks(store)


def _run_checks(store) -> list[str]:
    sent = []
    try:
        from . import pv_forecast
        fc = pv_forecast.get_forecast(store)
        res = check_pv(store, fc)
        if res and raise_alert("pv", *res):
            sent.append("pv")
    except Exception as exc:
        logger.info("[Meldung] PV-Pruefung fehlgeschlagen: %s", exc)
    try:
        from . import heat_demand
        model, _ = heat_demand.get_models(store)
        res = check_heat(store, model, heat_demand.weather_series())
        if res and raise_alert("waerme", *res):
            sent.append("waerme")
    except Exception as exc:
        logger.info("[Meldung] Waerme-Pruefung fehlgeschlagen: %s", exc)
    return sent


def start_background(store, interval_s: int = INTERVAL_S, first_delay_s: int = 300) -> None:
    """Einmal starten (idempotent). Erste Pruefung nach 5 min (Dashboard soll erst hochfahren)."""
    global _started
    if _started or store is None:
        return
    _started = True

    def loop():
        time.sleep(first_delay_s)
        while True:
            run_checks(store)
            time.sleep(interval_s)
    threading.Thread(target=loop, daemon=True, name="alerts").start()
