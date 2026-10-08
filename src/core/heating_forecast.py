"""Einheiz-Empfehlung: Wie lange reicht der Puffer, und bringt die Sonne genug?

Vorgehen
--------
1. **Verbrauch**: lernendes Waermebedarfs-Modell aus core/heat_demand
   (traege Aussentemperatur + Sonne, aehnliches Wetter zaehlt mehr) mit der
   stuendlichen Wetterprognose. Ohne Modell/Prognose: mittlere Abkuehlrate
   der letzten 24 h in ruhigen Phasen (Kessel aus, kein Anstieg).
2. **Solarthermie**: eigenes Kollektor-Modell (core/heat_demand.SolarThermalModel
   Version 2) aus der Strahlungsprognose fuer die Kollektorebene und der
   Kollektor-Kennlinie - unabhaengig von der PV (andere Ausrichtung, andere
   Temperaturabhaengigkeit). Die Speichertemperatur fuer den Wirkungsgrad
   kommt laufend aus der Simulation (voller, heisser Puffer -> weniger Ertrag).
   Rueckfall: k = Solarthermie-kWh / PV-kWh (altes Modell bzw. Saison-Faktor).
3. **Simulation**: Stuendlich fuer die naechsten 36 h:
       E(t+1h) = min(E_voll, E(t) - Verbrauch + Solar(t, E))
   Startwert ist der aktuell nutzbare Speicherinhalt. Faellt E auf 0, ist
   der Puffer "leer" (Mittel unter "nutzbar ab", Standard 35 °C).
4. **Empfehlung** aus dem Zeitpunkt, an dem der Puffer leer wird.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from . import heat_demand
from . import heating_stats as hs

HORIZON_H = 36
MIN_RATE_KW = 0.2
MIN_FACTOR_DAYS = 5
GAIN_EPS_KWH = 0.05


@dataclass
class Recommendation:
    level: str                       # "ok" | "soon" | "today" | "now" | "burning" | "unknown"
    title: str
    detail: str = ""
    empty_at: Optional[datetime] = None          # lokale Zeit, naiv
    rate_kw: Optional[float] = None
    solar_factor: Optional[float] = None
    solar_rest_today_kwh: Optional[float] = None
    solar_tomorrow_kwh: Optional[float] = None
    projection: list[tuple[datetime, float]] = field(default_factory=list)   # (lokal, kWh)
    steps: list[tuple[datetime, float, float]] = field(default_factory=list)   # (Beginn lokal, Verbrauch kW, Solar kWh)
    temps: list[tuple[datetime, float]] = field(default_factory=list)          # (Stunde lokal, °C) fuer 36 h
    outdoor_now: Optional[float] = None
    model: Optional["heat_demand.DemandModel"] = None
    outlook: Optional["heat_demand.WeekOutlook"] = None


def consumption_rate_kw(buckets: list[hs.Bucket], cfg: hs.StorageConfig) -> Optional[float]:
    """Mittlere Waermeabnahme (kW) in ruhigen Phasen: Kessel aus (inkl. Nachlauf), kein Anstieg."""
    total_kwh = 0.0
    total_h = 0.0
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
                total_kwh += -d
                total_h += minutes / 60.0
        prev_q, prev_b = q, b
    if total_h < 2.0:
        return None
    return max(MIN_RATE_KW, total_kwh / total_h)


def solar_factor(season: hs.HeatingStats, pv_daily: dict[date, float]) -> Optional[float]:
    """Solarthermie-kWh pro PV-kWh aus den Saisontagen mit nennenswerter Sonne."""
    solar_sum = pv_sum = 0.0
    n = 0
    for d in season.days:
        pv = pv_daily.get(d.day)
        if pv is None or pv < 3.0:
            continue
        solar_sum += d.solar_kwh
        pv_sum += pv
        n += 1
    if n < MIN_FACTOR_DAYS or pv_sum <= 0:
        return None
    return solar_sum / pv_sum


def _fmt_when(ts: datetime, now: datetime) -> str:
    if ts.date() == now.date():
        return f"{ts:%H:%M}"
    if ts.date() == now.date() + timedelta(days=1):
        return f"morgen {ts:%H:%M}"
    wd = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"][ts.weekday()]
    return f"{wd} {ts:%H:%M}"


def plan(usable_now: Optional[float], rate_kw: Optional[float], factor: Optional[float],
         pv_forecast_utc: Optional[dict], now: Optional[datetime] = None,
         kessel_active_now: bool = False, rate_fn=None, factor_fn=None, solar_fn=None,
         e_max: Optional[float] = None) -> Recommendation:
    """Simuliert die naechsten 36 h und leitet die Empfehlung ab.

    rate_fn(lokale Zeit) -> kW ueberschreibt den konstanten rate_kw (temperaturabhaengig),
    factor_fn(lokale Zeit) -> Solar-kWh je PV-kWh ueberschreibt den festen factor.
    solar_fn(lokale Zeit, nutzbarer Inhalt kWh) -> Solar-kW (Kollektor-Modell) hat Vorrang vor beidem.
    e_max: nutzbarer Inhalt bei vollem Speicher (mehr kann die Sonne nicht laden).
    """
    now = (now or datetime.now()).replace(second=0, microsecond=0)
    if usable_now is None or rate_kw is None:
        return Recommendation("unknown", "Noch zu wenig Daten",
                              "Für eine Empfehlung braucht es Heizungsdaten der letzten 24 h.")

    def pv_kw_at(local_hour_end: datetime) -> float:
        if not pv_forecast_utc:
            return 0.0
        key = local_hour_end.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return float(pv_forecast_utc.get(key, 0.0))

    k = factor or 0.0
    energy = usable_now
    projection = [(now, energy)]
    steps: list[tuple[datetime, float, float]] = []
    empty_at = None
    solar_today = solar_tomorrow = 0.0
    t = now
    # erster Schritt bis zur naechsten vollen Stunde, danach stuendlich
    next_hour = (now + timedelta(hours=1)).replace(minute=0)
    while t < now + timedelta(hours=HORIZON_H):
        t_next = min(next_hour, now + timedelta(hours=HORIZON_H))
        frac = (t_next - t).total_seconds() / 3600.0
        if solar_fn is not None:
            solar = max(0.0, solar_fn(t, energy)) * frac
        else:
            k_here = factor_fn(t) if factor_fn is not None else k
            solar = k_here * pv_kw_at(next_hour) * frac
        if t.date() == now.date():
            solar_today += solar
        elif t.date() == now.date() + timedelta(days=1):
            solar_tomorrow += solar
        rate_here = rate_fn(t) if rate_fn is not None else rate_kw
        steps.append((t, rate_here, solar))
        new_energy = energy - rate_here * frac + solar
        if empty_at is None and new_energy <= 0 < energy:
            empty_at = t + timedelta(hours=frac * energy / max(1e-6, energy - new_energy))
        energy = max(0.0, new_energy)
        if e_max is not None:
            energy = min(energy, e_max)
        projection.append((t_next, energy))
        t = t_next
        next_hour = t + timedelta(hours=1)
    if usable_now <= 0:
        empty_at = now

    # Fuer die Anzeige: Sonne morgen auch dann schaetzen, wenn der Horizont endet
    has_solar = bool(factor) or solar_fn is not None
    rec = Recommendation("ok", "", rate_kw=rate_kw, solar_factor=factor, empty_at=empty_at,
                         solar_rest_today_kwh=solar_today if has_solar else None,
                         solar_tomorrow_kwh=solar_tomorrow if has_solar else None,
                         projection=projection, steps=steps)

    sun_note = ""
    if has_solar and solar_tomorrow >= 1.0:
        sun_note = f" · Sonne morgen ≈ {solar_tomorrow:.0f} kWh"
    elif not has_solar and pv_forecast_utc:
        sun_note = " · Solar-Anteil wird noch gelernt"

    if kessel_active_now:
        rec.level, rec.title = "burning", "Kessel läuft – Puffer wird geladen"
        rec.detail = f"Verbrauch zuletzt ≈ {rate_kw:.1f} kW"
        return rec
    if empty_at is None:
        rec.level, rec.title = "ok", "Kein Einheizen nötig"
        rec.detail = f"Puffer{' + Sonne' if solar_today + solar_tomorrow >= 1 else ''} reicht über die nächsten 36 h" + sun_note
    elif empty_at <= now + timedelta(hours=3):
        rec.level, rec.title = "now", "Jetzt einheizen"
        rec.detail = f"Puffer leer ca. {_fmt_when(empty_at, now)}" + sun_note
    elif empty_at.date() == now.date() or (empty_at.date() == now.date() + timedelta(days=1) and empty_at.hour < 9):
        rec.level = "today"
        rec.title = "Heute Abend einheizen" if now.hour >= 12 else "Heute einheizen"
        rec.detail = f"Puffer reicht bis ca. {_fmt_when(empty_at, now)}" + sun_note
    else:
        rec.level, rec.title = "soon", "Morgen einheizen"
        rec.detail = f"Puffer reicht bis ca. {_fmt_when(empty_at, now)}" + sun_note
    return rec


def week_solar(usable: float, now: datetime, rate_fn, solar_fn, e_max: Optional[float],
               days: int = 7) -> dict:
    """Solar-Ertrag je Tag fuer den Wochenausblick (gleiche Simulation, ohne Einheizen).

    Ohne Feuer kuehlt der Puffer ab -> Kollektoren arbeiten effizienter; das ist
    fuer die Frage "wie oft muss ich einheizen" die passende Annahme.
    """
    out: dict[date, float] = {}
    e = usable
    t = now.replace(minute=0, second=0, microsecond=0)
    end = now + timedelta(days=days)
    while t < end:
        frac = 1.0 if t >= now else (t + timedelta(hours=1) - now).total_seconds() / 3600.0
        s = max(0.0, solar_fn(t, e)) * frac
        e_new = e - rate_fn(t) * frac + s
        if e_max is not None and e_new > e_max:
            s = max(0.0, s - (e_new - e_max))          # voller Speicher nimmt nichts mehr auf
            e_new = e_max
        out[t.date()] = out.get(t.date(), 0.0) + s
        e = max(0.0, e_new)
        t += timedelta(hours=1)
    return out


def recommend(store, cfg: Optional[hs.StorageConfig] = None, season: Optional[hs.HeatingStats] = None,
              pv_forecast_utc: Optional[dict] = None, now: Optional[datetime] = None,
              temps_utc: Optional[dict] = None) -> Recommendation:
    """Alles zusammen: Daten laden, Verbrauchsmodell/Faktor bestimmen, simulieren."""
    cfg = cfg or hs.load_storage_config()
    now = now or datetime.now()
    buckets = hs.load_buckets(store, now - timedelta(hours=24), now + timedelta(minutes=1))
    rate_24h = consumption_rate_kw(buckets, cfg)

    usable = None
    kessel_now = False
    base = cfg.usable_from_c * (cfg.puffer_kwh_per_k + cfg.boiler_kwh_per_k)
    for b in reversed(buckets):
        q = hs.heat_content_kwh(b, cfg)
        if q is not None:
            usable = max(0.0, q - base)
            kessel_now = hs.kessel_active(b) and (now - b.ts) <= timedelta(minutes=45)
            break

    factor = None
    if season is not None:
        pv_daily: dict[date, float] = {}
        try:
            for row in store.get_daily_totals(days=None) or []:
                pv_daily[date.fromisoformat(str(row["day"])[:10])] = float(row.get("pv_kwh") or 0.0)
        except Exception:
            pv_daily = {}
        factor = solar_factor(season, pv_daily)

    # Lernende Modelle (einmal nach Start und taeglich neu gelernt) + Wetterprognose
    model = solar_model = None
    try:
        model, solar_model = heat_demand.get_models(store, cfg)
    except Exception:
        model = solar_model = None
    try:
        wx = heat_demand.weather_series()
    except Exception:
        wx = {}
    if temps_utc is None:
        temps_utc = {}
        if wx and len(wx.get("t", [])):
            for ts, v in zip(wx["t"], wx["temp"]):
                if v == v:     # nicht NaN
                    temps_utc[datetime.fromtimestamp(int(ts), timezone.utc)] = float(v)
    outdoor_now = heat_demand._temp_for(now.replace(minute=0, second=0, microsecond=0), temps_utc or {}, None)
    if outdoor_now is None:
        outdoor_now = next((b.outdoor for b in reversed(buckets) if b.outdoor is not None), None)

    rate_fn = None
    rate_now = rate_24h
    if model is not None and (model.temperature_dependent or rate_24h is None):
        if getattr(model, "version", 1) >= 2 and wx:
            rate_fn = model.predictor(wx)
        else:
            rate_fn = lambda t: model.kw_at(heat_demand._temp_for(t.replace(minute=0, second=0, microsecond=0),
                                                                   temps_utc or {}, outdoor_now))
        rate_now = rate_fn(now)

    factor_fn = None
    solar_fn = None
    kpk = cfg.puffer_kwh_per_k + cfg.boiler_kwh_per_k
    e_max = max(0.0, (cfg.full_at_c - cfg.usable_from_c) * kpk)
    if solar_model is not None and getattr(solar_model, "version", 1) >= 2 and wx and len(wx.get("t", [])):
        col = solar_model.forecast_fn(wx)
        # nutzbarer Inhalt -> Speichermittel (darunter gilt "nutzbar ab")
        solar_fn = lambda t, e: col(t, cfg.usable_from_c + max(0.0, e) / kpk)
    elif solar_model is not None:
        tank_now = None
        for b in reversed(buckets):
            layers = [v for v in (b.top, b.mid, b.bot) if v is not None]
            if layers:
                tank_now = sum(layers) / len(layers)
                break
        factor = solar_model.mean_ratio
        factor_fn = lambda t: solar_model.ratio(
            heat_demand._temp_for(t.replace(minute=0, second=0, microsecond=0), temps_utc or {}, outdoor_now),
            tank_now)

    rec = plan(usable, rate_now, factor, pv_forecast_utc, now=now, kessel_active_now=kessel_now,
               rate_fn=rate_fn, factor_fn=factor_fn, solar_fn=solar_fn, e_max=e_max)
    rec.outdoor_now = outdoor_now
    if temps_utc:
        hour0 = now.replace(minute=0, second=0, microsecond=0)
        for i in range(HORIZON_H + 1):
            t = hour0 + timedelta(hours=i)
            v = heat_demand._temp_for(t, temps_utc, None)
            if v is not None:
                rec.temps.append((t + timedelta(minutes=30), v))
    rec.model = model
    if model is not None and usable is not None:
        firing_kwh = [e.wood_kwh for e in (season.events if season else []) if e.wood_kwh > 20]
        avg = (sum(firing_kwh) / len(firing_kwh)) if firing_kwh else None
        solar_by_day = {}
        if solar_fn is not None and rate_fn is not None:
            solar_by_day = week_solar(usable, now, rate_fn, solar_fn, e_max)
        elif factor:
            solar_by_day = {now.date(): rec.solar_rest_today_kwh or 0.0,
                            now.date() + timedelta(days=1): rec.solar_tomorrow_kwh or 0.0}
        rec.outlook = heat_demand.week_outlook(model, temps_utc or {}, usable, avg, solar_by_day, now=now,
                                               rate_fn=rate_fn)
    return rec
