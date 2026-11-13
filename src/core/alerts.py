"""Warnmeldungen: PV liefert zu wenig, Haus verbraucht ungewoehnlich viel Waerme.

Laeuft im Hintergrund (start_background, alle 15 min). Meldungen gehen ueber
Home Assistant aufs Handy (config/homeassistant.json: "notify_service", z.B.
"mobile_app_pixel_8"; ohne Eintrag als Benachrichtigung in Home Assistant).
Jede Art von Meldung hoechstens alle COOLDOWN_H Stunden. Alle Meldungen landen
ausserdem in data/forecast_learning.db (Tabelle alerts) und im Log.

Solarthermie
    Zwei Sonnentage hintereinander (Globalstrahlung >= 3 kWh/m², kein Feuer) mit weniger als 8 kWh
    Speicherzuwachs. Hoechstens einmal pro Woche; Zeitraeume aus "solar_aus" (config/heizung.json) zaehlen nicht.

Feuer ausgegangen (Kessel)
    Teil-/Volllast endet nach weniger als 45 min bei Pufferladung < 70 %. Wird von
    core.BMKDATEN sofort (nicht erst in der 15-min-Schleife) ausgeloest, Cooldown 1 h.

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
FIRE_OUT_COOLDOWN_H = 1.0
PV_MIN_EXPECTED_KW = 1.5
PV_MAX_RATIO = 0.10  # vorher 0.35: Nebel/Hochnebel loeste Fehlalarme aus
PV_SOC_CURTAIL = 97.0
HEAT_WINDOW_H = 12
HEAT_MIN_HOURS = 8.0
HEAT_RATIO = 1.8
HEAT_EXCESS_KW = 2.0
SOLAR_MIN_GHI_WH = 3000.0    # Tagessumme Globalstrahlung, ab der die Kollektoren liefern muessen
SOLAR_MIN_GAIN_KWH = 8.0     # weniger Speicherzuwachs an so einem Tag = "liefert nichts"
SOLAR_SUN_WM2 = 100.0        # Stunden mit mehr Strahlung zaehlen als Sonnenstunden
SOLAR_DAYS_NEEDED = 2        # so viele Sonnentage hintereinander ohne Ertrag, dann melden
SOLAR_LOOKBACK_D = 5
SOLAR_COOLDOWN_H = 7 * 24.0  # Pool-Betrieb dauert Wochen - nicht taeglich erinnern
HEAT_SETTLE_H = 4            # so lange nach einem Feuer zaehlen Stunden nicht (Speicher schichtet um ->
                             # scheinbar hoher Verbrauch; loeste 25.10. und 06.11.2026 Fehlalarme aus)

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
                now: Optional[float] = None, conn=None, cooldown_h: Optional[float] = None) -> bool:
    """Meldung senden, ausser dieselbe Art kam in den letzten `cooldown_h` (Standard COOLDOWN_H) Stunden schon."""
    now = time.time() if now is None else now
    limit_s = (COOLDOWN_H if cooldown_h is None else cooldown_h) * 3600
    own = conn is None
    conn = conn or fl.connect()
    try:
        _ensure(conn)
        last = conn.execute("SELECT MAX(ts) FROM alerts WHERE kind = ?", (kind,)).fetchone()[0]
        if last is not None and now - last < limit_s:
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
# Kessel: Feuer ausgegangen
# ---------------------------------------------------------------------------

def fire_out_alert(info: dict, sender: Optional[Callable[[str, str], bool]] = None, conn=None,
                   now: Optional[float] = None) -> bool:
    """Handy-Meldung "Feuer ausgegangen?" (info = FireOut.to_dict() aus core.bmk_interpret).

    Kommt direkt aus dem Kessel-Collector (jede Minute geprueft), nicht aus der 15-min-Schleife,
    damit man rechtzeitig nachlegen kann. Eigener Cooldown, damit zwei Versuche kurz hintereinander
    nicht an der 12-h-Sperre der anderen Meldungen scheitern.
    """
    if not info:
        return False
    return raise_alert("feuer_aus", info.get("titel") or "Feuer im Kessel ausgegangen?",
                       info.get("text") or "Die Verbrennung ist nach kurzer Brennzeit zu Ende.",
                       sender=sender, now=now, conn=conn, cooldown_h=FIRE_OUT_COOLDOWN_H)


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
    buckets = hs.load_buckets(store, start - timedelta(hours=1 + HEAT_SETTLE_H), end)
    all_rows = fl.heat_hour_rows(buckets, cfg)
    fire_hours = [r[0] for r in all_rows if len(r) > 5 and (r[5] or 0.0) > 0.0]
    rows = [r for r in all_rows if start.timestamp() <= r[0] < end.timestamp()]
    pv = _hourly_fronius(store, start.astimezone(timezone.utc), end.astimezone(timezone.utc))
    kwh = minutes = 0.0
    used = []
    for hour_start, q_kwh, q_min, *_ in rows:
        h_end = datetime.fromtimestamp(hour_start + 3600, timezone.utc)
        if q_min < 30 or pv.get(h_end, (0.0, None))[0] > 0.1:
            continue
        if any(0 <= hour_start - f <= HEAT_SETTLE_H * 3600 for f in fire_hours):
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
# Solarthermie
# ---------------------------------------------------------------------------

def solar_day_gains(heat_rows, ghi_by_hour: dict) -> dict:
    """{lokaler Tag: (GHI Wh/m², Solargewinn kWh, Sonnenstunden)} fuer Tage ohne Kesselbetrieb.

    heat_rows: (hour_start, quiet_kwh, quiet_min, free_kwh, free_min, kessel_min, ...) wie heat_hours;
    ghi_by_hour: {hour_start: W/m²}. Gewinn = Speicheraenderung in den Sonnenstunden plus der Verbrauch,
    der in der Zeit gedeckt wurde (Rate aus den Stunden ohne Sonne desselben Tages).
    """
    days: dict = {}
    for r in heat_rows:
        hour_start, free_kwh, free_min, kessel_min = r[0], r[3], r[4], r[5]
        d = datetime.fromtimestamp(hour_start).date()
        a = days.setdefault(d, {"ghi": 0.0, "net": 0.0, "sun_h": 0, "kessel": 0.0, "dark_kwh": 0.0, "dark_h": 0.0})
        ghi = ghi_by_hour.get(hour_start)
        ghi = 0.0 if ghi is None else float(ghi)
        a["ghi"] += ghi
        a["kessel"] += kessel_min or 0.0
        if (kessel_min or 0.0) > 0 or free_kwh is None:
            continue
        if ghi > SOLAR_SUN_WM2:
            a["net"] += free_kwh
            a["sun_h"] += 1
        elif ghi < 5.0 and (free_min or 0.0) >= 45:
            a["dark_kwh"] += free_kwh
            a["dark_h"] += free_min / 60.0
    out = {}
    for d, a in days.items():
        if a["kessel"] > 30 or a["sun_h"] < 3:
            continue
        rate = (-a["dark_kwh"] / a["dark_h"]) if a["dark_h"] >= 2 else 1.3     # kW Verbrauch ohne Sonne
        out[d] = (a["ghi"], a["net"] + max(0.0, rate) * a["sun_h"], a["sun_h"])
    return out


def _solar_paused(day) -> bool:
    try:
        from . import heat_demand
        return any(a <= day <= b for a, b in heat_demand._solar_off_periods())
    except Exception:
        return False


def check_solar(conn=None, now: Optional[datetime] = None) -> Optional[tuple[str, str]]:
    """Meldung, wenn die Solarthermie an den letzten Sonnentagen nichts in den Speicher gebracht hat."""
    now = now or datetime.now()
    start = int((now - timedelta(days=SOLAR_LOOKBACK_D + 1)).timestamp())
    own = conn is None
    conn = conn or fl.connect()
    try:
        heat = conn.execute("SELECT hour_start, quiet_kwh, quiet_min, free_kwh, free_min, kessel_min FROM heat_hours "
                            "WHERE hour_start >= ? ORDER BY hour_start", (start,)).fetchall()
        ghi = dict(conn.execute("SELECT hour_end - 3600, ghi FROM weather WHERE hour_end >= ?", (start,)).fetchall())
    finally:
        if own:
            conn.close()
    gains = solar_day_gains(heat, ghi)
    sunny = [(d, g) for d, g in sorted(gains.items())
             if d < now.date() and g[0] >= SOLAR_MIN_GHI_WH and not _solar_paused(d)]
    last = sunny[-SOLAR_DAYS_NEEDED:]
    if len(last) < SOLAR_DAYS_NEEDED or any(g[1] >= SOLAR_MIN_GAIN_KWH for _, g in last):
        return None
    if (now.date() - last[-1][0]).days > 2:
        return None                                   # zu lange her - nicht mehr aktuell
    tage = ", ".join(f"{d:%d.%m.} ({g[0] / 1000:.1f} kWh/m² Sonne, {max(0.0, g[1]):.0f} kWh im Speicher)" for d, g in last)
    return ("Solarthermie liefert nichts",
            f"An den letzten Sonnentagen kam kaum Wärme in den Speicher: {tage}. "
            "Pumpe/Regelung prüfen – oder läuft die Anlage auf den Pool?")


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
    try:
        res = check_solar()
        if res and raise_alert("solar", *res, cooldown_h=SOLAR_COOLDOWN_H):
            sent.append("solar")
    except Exception as exc:
        logger.info("[Meldung] Solar-Pruefung fehlgeschlagen: %s", exc)
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
