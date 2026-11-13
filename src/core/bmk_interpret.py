"""BMK 40: aus den Rohwerten eine Aussage machen.

Der Kessel liefert 73 Felder pro Minute (Belegung: siehe Projekt-Doc "bmk-kanalbelegung").
Einzeln sagen sie wenig - erst die Kombination aus Betriebsmodus, Pufferladung, Trend, Luftklappen
und CO2 beantwortet die Fragen, die im Alltag zaehlen:

* Was macht der Kessel gerade, und was heisst das? ("Holz abgebrannt, Glut haelt - Puffer erst 52 %:
  Nachlegen lohnt")                                              -> LiveInterpreter / Deutung
* Wie viel Platz hat der Puffer noch? (in kWh und in vollen Fuellungen)
* Wie gut war das letzte Feuer? (Dauer, Teil-/Volllast gegen Glut, Pufferzuwachs, Fuellgrad, Abbrand)
                                                                  -> fire_summaries / fire_lines
* Was war ein Fehlstart (START-Phase ohne Brand) oder nur ein Nachlege-Fenster?

Ableitungen aus den Daten vom 09.-30.10.2026 (nicht aus Herstellerdoku):
* Kanal 3 "Pufferladung %" ist linear zur mittleren Puffertemperatur: ~2,08 * T - 62,9
  (0 % ~ 30,2 Grad, 100 % ~ 78,2 Grad).
* Kanal 11 verhaelt sich wie CO2 im Abgas (Start 0, Teillast ~5, Volllast ~11 %; 18,07 = Leerwert).
* Kanaele 8/9/10 = Saugzug / Primaerluft / Sekundaerluft in %.
* Im Modus NACHLEGEN liefert der Kessel 75 statt 73 Felder (Meldungstext + Zeitstempel ab Index 52).
"""
from __future__ import annotations

import logging
import re
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence

from . import heating_stats as hs

logger = logging.getLogger(__name__)

# --- Belegung -------------------------------------------------------------
IDX_MODUS, IDX_KESSEL, IDX_AUSSEN, IDX_LADUNG = 0, 1, 2, 3
IDX_TOP, IDX_MID, IDX_BOT = 4, 5, 6
IDX_PUMPE, IDX_SAUGZUG, IDX_PRIMAER, IDX_SEKUNDAER, IDX_CO2, IDX_WARM = 7, 8, 9, 10, 11, 12
IDX_HK_EG, IDX_VL_EG, IDX_VL_OG = 18, 21, 24
IDX_BETRIEBSSTUNDEN = 54          # in den normalisierten 73 Feldern
MIN_FIELDS = 56
NOT_CONNECTED = -20.0
CO2_IDLE = 18.0                   # Leerwert im Standby

# --- Physik/Kalibrierung --------------------------------------------------
LADUNG_SLOPE = 2.0825             # Pufferladung % = SLOPE * T_mittel - OFFSET
LADUNG_OFFSET = 62.936
T_LADUNG_0 = LADUNG_OFFSET / LADUNG_SLOPE                       # ~30,2 Grad
T_LADUNG_100 = (100.0 + LADUNG_OFFSET) / LADUNG_SLOPE           # ~78,2 Grad
FILL_RM = 0.2                      # volle Fuellung des BMK 40 (Laurenz' Kontrollzaehlung)

# --- Schwellen ------------------------------------------------------------
LADUNG_VOLL = 85.0
LADUNG_NIEDRIG = 60.0
LADUNG_LEER = 20.0
TREND_WINDOW_MIN = 60
TREND_MIN_SPAN_MIN = 20
CO2_USUAL = {"teillast": 5.4, "volllast": 10.6}       # Median Okt. 2026
CO2_WEAK_DELTA = 2.5
CO2_MIN_PHASE_MIN = 10
REAL_FIRE_MIN = 10                 # Minuten Teil-/Volllast, ab denen ein Feuer "echt" ist
GAP_MIN = 10                       # Luecke zwischen zwei Messungen, ab der ein Lauf endet
# Feuer vermutlich ausgegangen: Teil-/Volllast endet nach kurzer Brennzeit bei noch leerem Puffer.
# Nachgeprueft an den 7 Uebergaengen nach GLUTERHALT vom 05.-13.11.2026: normale Enden bei Puffer
# 88-99 % bzw. nach > 150 min Brennzeit, ausgegangen am 09.11. (36 %, 11 min) und 13.11. (23 %, 17 min).
FIRE_OUT_LADUNG = 70.0             # Puffer darunter ...
FIRE_OUT_BRENN_MIN = 45.0          # ... und weniger Minuten Teil-/Volllast am Stueck -> Meldung
FIRE_OUT_MIN_BRENN = 5.0           # kuerzere Teillast-Zuckungen sind kein Feuer

_DATE_RE = re.compile(r"^\d{2}\.\d{2} \d{2}:\d{2}:\d{2}$")


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(str(v).strip().replace(",", "."))
    except ValueError:
        return None
    return None if x != x else x


def _temp(v) -> Optional[float]:
    x = _num(v)
    return None if x is None or x <= NOT_CONNECTED + 0.5 or x > 200 else x


def phase_of(modus: Optional[str]) -> str:
    m = (modus or "").strip().upper()
    if not m:
        return "unbekannt"
    if m.startswith("STAND") or m in ("AUS", "OFF", "BEREIT"):
        return "aus"
    if m.startswith("START") or m.startswith("ANHEIZ"):
        return "anheizen"
    if m.startswith("TEIL"):
        return "teillast"
    if m.startswith("VOLL"):
        return "volllast"
    if m.startswith("GLUT"):
        return "gluterhalt"
    if m.startswith("NACHLEG"):
        return "nachlegen"
    return "unbekannt"


def ladung_from_temps(top, mid, bot) -> Optional[float]:
    t = [x for x in (top, mid, bot) if x is not None]
    if len(t) < 2:
        return None
    return LADUNG_SLOPE * (sum(t) / len(t)) - LADUNG_OFFSET


# ---------------------------------------------------------------------------
# Rohwerte lesen
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BmkReading:
    ts: datetime
    modus: str
    kessel: Optional[float]
    aussen: Optional[float]
    top: Optional[float]
    mid: Optional[float]
    bot: Optional[float]
    warm: Optional[float]
    ladung: Optional[float]          # % (Kanal 3, sonst aus den Temperaturen)
    saugzug: Optional[float]
    primaer: Optional[float]
    sekundaer: Optional[float]
    co2: Optional[float]             # None im Leerlauf
    pumpe: Optional[float]
    heizfreigabe: Optional[bool]
    vl_eg: Optional[float]
    vl_og: Optional[float]
    betriebsstunden: Optional[int]
    meldung: Optional[str] = None

    @property
    def phase(self) -> str:
        return phase_of(self.modus)


def normalize_fields(values: Sequence) -> tuple[list, Optional[str]]:
    """73-Felder-Liste und Meldungstext. Im Modus NACHLEGEN sind ab Index 52 zwei Felder eingeschoben."""
    v = [str(x).strip() for x in values]
    if len(v) >= 75 and _DATE_RE.match(v[53] or ""):
        return v[:52] + v[54:], v[52] or None
    return v, None


def parse_bmk(values: Sequence, ts: Optional[datetime] = None) -> Optional[BmkReading]:
    if not values:
        return None
    v, meldung = normalize_fields(values)
    if len(v) < MIN_FIELDS or not v[IDX_MODUS]:
        return None
    top, mid, bot = _temp(v[IDX_TOP]), _temp(v[IDX_MID]), _temp(v[IDX_BOT])
    ladung = _num(v[IDX_LADUNG])
    if ladung is None or not -5 <= ladung <= 105:
        ladung = ladung_from_temps(top, mid, bot)
    co2 = _num(v[IDX_CO2])
    if co2 is not None and (co2 >= CO2_IDLE or co2 < 0):
        co2 = None
    hours = _num(v[IDX_BETRIEBSSTUNDEN])
    return BmkReading(
        ts=ts or datetime.now(), modus=v[IDX_MODUS], kessel=_temp(v[IDX_KESSEL]), aussen=_temp(v[IDX_AUSSEN]),
        top=top, mid=mid, bot=bot, warm=_temp(v[IDX_WARM]), ladung=ladung,
        saugzug=_num(v[IDX_SAUGZUG]), primaer=_num(v[IDX_PRIMAER]), sekundaer=_num(v[IDX_SEKUNDAER]),
        co2=co2, pumpe=_num(v[IDX_PUMPE]),
        heizfreigabe=(v[IDX_HK_EG].upper() == "EIN") if len(v) > IDX_HK_EG and v[IDX_HK_EG] else None,
        vl_eg=_temp(v[IDX_VL_EG]), vl_og=_temp(v[IDX_VL_OG]),
        betriebsstunden=int(hours) if hours is not None and 0 < hours < 1e6 else None, meldung=meldung)


# ---------------------------------------------------------------------------
# Live-Deutung
# ---------------------------------------------------------------------------

@dataclass
class Deutung:
    phase: str
    headline: str
    detail: str = ""
    tone: str = "info"                 # aus | info | ok | warn | hot
    empfehlung: Optional[str] = None   # nachlegen | nicht_nachlegen | nicht_oeffnen | None
    ladung_pct: Optional[float] = None
    trend_pct_h: Optional[float] = None
    reicht_h: Optional[float] = None
    platz_kwh: Optional[float] = None
    fuellungen: Optional[float] = None
    phase_min: Optional[float] = None
    abbrand: Optional[str] = None      # gewohnt | schwach
    hinweise: list = field(default_factory=list)
    feuer_aus: Optional[dict] = None   # gesetzt, solange das Feuer als ausgegangen gilt (siehe FireOut)
    feuer_aus_neu: bool = False        # nur in der Messung, in der es erkannt wurde -> Meldung senden

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["hinweise"] = list(self.hinweise)
        return d


_PHASE_TEXT = {"gluterhalt": "Gluterhalt", "aus": "Standby", "anheizen": "Anheizen"}


@dataclass(frozen=True)
class FireOut:
    """Teil-/Volllast ist nach kurzer Brennzeit bei leerem Puffer zu Ende: Feuer vermutlich ausgegangen."""
    ts: datetime
    phase: str                          # Phase, in die der Kessel gewechselt ist
    brenn_min: float
    ladung: Optional[float]
    kessel: Optional[float]

    def title(self) -> str:
        return "Feuer im Kessel ausgegangen?"

    def message(self) -> str:
        lad = f"Puffer erst {self.ladung:.0f} %" if self.ladung is not None else "Puffer noch nicht geladen"
        kes = f", Kessel {self.kessel:.0f} °C" if self.kessel is not None else ""
        return (f"Die Verbrennung ist nach nur {_fmt_dur(self.brenn_min)} zu Ende "
                f"(jetzt {_PHASE_TEXT.get(self.phase, self.phase)}). {lad}{kes}. "
                "Holz zu früh abgebrannt oder Feuer erloschen – nachlegen bzw. neu anzünden?")

    def to_dict(self) -> dict:
        return {"ts": self.ts.isoformat(), "phase": self.phase, "brenn_min": round(self.brenn_min, 1),
                "ladung": self.ladung, "kessel": self.kessel, "titel": self.title(), "text": self.message()}


def free_capacity(reading: BmkReading, cfg: hs.StorageConfig) -> tuple[Optional[float], Optional[float]]:
    """(kWh bis 100 % Pufferladung, entspricht so vielen vollen Fuellungen)."""
    if reading.ladung is None:
        return None, None
    kwh_per_pct = cfg.puffer_kwh_per_k * (T_LADUNG_100 - T_LADUNG_0) / 100.0
    free = max(0.0, 100.0 - reading.ladung) * kwh_per_pct
    fill = FILL_RM * cfg.wood_kwh_per_rm * cfg.boiler_efficiency
    return free, (free / fill if fill > 0 else None)


def _fmt_h(h: float) -> str:
    if h >= 24:
        return "über 24 h"
    if h < 1:
        return f"{max(5, int(round(h * 60 / 5.0)) * 5)} min"
    return f"{h:.0f} h" if h >= 10 else f"{h:.1f} h".replace(".", ",")


def _fmt_fill(f: Optional[float]) -> str:
    if f is None:
        return ""
    return f"{f:.1f}".replace(".", ",") + (" Füllung" if round(f, 1) == 1.0 else " Füllungen")


class LiveInterpreter:
    """Haelt die letzten Messungen im Speicher und deutet die jeweils neueste."""

    def __init__(self, cfg: Optional[hs.StorageConfig] = None, keep_min: int = 240):
        self._cfg = cfg
        self._hist: deque = deque()
        self._keep = timedelta(minutes=keep_min)
        self._lock = threading.Lock()
        self._fire_out: Optional[FireOut] = None

    @property
    def cfg(self) -> hs.StorageConfig:
        if self._cfg is None:
            self._cfg = hs.load_storage_config()
        return self._cfg

    def add(self, r: BmkReading) -> None:
        with self._lock:
            if self._hist and r.ts <= self._hist[-1].ts:
                return
            self._hist.append(r)
            while self._hist and r.ts - self._hist[0].ts > self._keep:
                self._hist.popleft()

    def preload(self, readings: Iterable[BmkReading]) -> None:
        for r in sorted(readings, key=lambda x: x.ts):
            self.add(r)

    def trend_pct_h(self, now: datetime) -> Optional[float]:
        with self._lock:
            pts = [(r.ts, r.ladung) for r in self._hist
                   if r.ladung is not None and now - r.ts <= timedelta(minutes=TREND_WINDOW_MIN)]
        if len(pts) < 5:
            return None
        t0 = pts[0][0]
        xs = [(t - t0).total_seconds() / 3600.0 for t, _ in pts]
        if (xs[-1] - xs[0]) * 60 < TREND_MIN_SPAN_MIN:
            return None
        mx, my = sum(xs) / len(xs), sum(y for _, y in pts) / len(pts)
        den = sum((x - mx) ** 2 for x in xs)
        return sum((x - mx) * (y - my) for x, (_, y) in zip(xs, pts)) / den if den > 0 else None

    def phase_minutes(self, now: datetime) -> Optional[float]:
        """Seit wann laeuft die aktuelle Phase (nur wenn lueckenlos beobachtet, sonst None)."""
        with self._lock:
            hist = list(self._hist)
        if not hist:
            return None
        cur = hist[-1].phase
        start = prev = hist[-1].ts
        complete = True                      # ganze Historie gehoert zur aktuellen Phase?
        for r in reversed(hist[:-1]):
            if r.phase != cur or (prev - r.ts) > timedelta(minutes=GAP_MIN):
                complete = False
                break
            start = prev = r.ts
        if complete and (hist[-1].ts - hist[0].ts) >= self._keep - timedelta(minutes=2):
            return None                      # mindestens so lang wie der Speicher reicht - unbekannt
        return (now - start).total_seconds() / 60.0

    def burn_minutes(self, until: BmkReading) -> Optional[float]:
        """Minuten Teil-/Volllast am Stueck bis einschliesslich `until` (None: reicht ueber den Speicher hinaus)."""
        with self._lock:
            hist = list(self._hist)
        run = [r for r in hist if r.ts <= until.ts]
        if not run or run[-1].ts != until.ts:
            return None
        start = until.ts
        complete = True
        for r in reversed(run[:-1]):
            if r.phase not in ("teillast", "volllast") or (start - r.ts) > timedelta(minutes=GAP_MIN):
                complete = False
                break
            start = r.ts
        if complete and (run[-1].ts - run[0].ts) >= self._keep - timedelta(minutes=2):
            return None
        return (until.ts - start).total_seconds() / 60.0 + 1.0

    def _check_fire_out(self, prev: Optional[BmkReading], r: BmkReading) -> bool:
        """True genau in der Messung, in der "Feuer vermutlich ausgegangen" erkannt wird."""
        if r.phase in ("teillast", "volllast", "nachlegen", "anheizen"):
            if not (r.phase == "anheizen" and prev is not None and prev.phase in ("teillast", "volllast")):
                self._fire_out = None
        if prev is None or prev.phase not in ("teillast", "volllast"):
            return False
        if r.phase not in ("gluterhalt", "aus", "anheizen") or r.ts - prev.ts > timedelta(minutes=GAP_MIN):
            return False
        if r.ladung is None or r.ladung >= FIRE_OUT_LADUNG:
            return False
        brenn = self.burn_minutes(prev)
        if brenn is None or not FIRE_OUT_MIN_BRENN <= brenn < FIRE_OUT_BRENN_MIN:
            return False
        self._fire_out = FireOut(ts=r.ts, phase=r.phase, brenn_min=brenn, ladung=r.ladung, kessel=r.kessel)
        return True

    def interpret(self, r: BmkReading) -> Deutung:
        with self._lock:
            prev = self._hist[-1] if self._hist else None
        fresh = prev is None or r.ts > prev.ts
        self.add(r)
        new = self._check_fire_out(prev, r) if fresh else False
        d = deuten(r, self.cfg, trend=self.trend_pct_h(r.ts), phase_min=self.phase_minutes(r.ts),
                   fire_out=self._fire_out)
        d.feuer_aus_neu = new
        if new and d.feuer_aus is None and self._fire_out is not None:
            d.feuer_aus = self._fire_out.to_dict()
        return d


def deuten(r: BmkReading, cfg: hs.StorageConfig, trend: Optional[float] = None,
           phase_min: Optional[float] = None, fire_out: Optional[FireOut] = None) -> Deutung:
    """Kombination der Werte -> Aussage. Reine Funktion (gut testbar)."""
    ph = r.phase
    lad = r.ladung
    free, fills = free_capacity(r, cfg)
    pct = f"{lad:.0f} %" if lad is not None else "--"
    trend_txt = ""
    reicht = None
    if trend is not None and abs(trend) >= 0.5:
        trend_txt = f"{trend:+.0f} %/h".replace("-", "−")
        if trend < -0.5 and lad is not None and lad > LADUNG_LEER:
            reicht = (lad - LADUNG_LEER) / -trend
    d = Deutung(phase=ph, headline="", ladung_pct=lad, trend_pct_h=trend, reicht_h=reicht, platz_kwh=free,
                fuellungen=fills, phase_min=phase_min)
    platz = f"Platz für {_fmt_fill(fills)}" if fills is not None else ""
    msg = (r.meldung or "").upper()

    if ph == "aus":
        if lad is not None and lad >= LADUNG_VOLL:
            d.headline, d.tone = f"Kessel aus · Puffer voll ({pct})", "ok"
        else:
            d.headline, d.tone = f"Kessel aus · Puffer {pct}", "aus"
        bits = []
        if reicht is not None:
            bits.append(f"sinkt {trend_txt}, reicht noch {_fmt_h(reicht)} bis {LADUNG_LEER:.0f} %")
        elif trend_txt:
            bits.append(f"Puffer {trend_txt}")
        if platz and (lad is None or lad < LADUNG_VOLL):
            bits.append(platz)
        d.detail = " · ".join(bits)

    elif ph == "anheizen":
        wait = f" (seit {phase_min:.0f} min)" if phase_min is not None and phase_min >= 1 else ""
        d.headline, d.tone = f"Anheizen{wait} · Kessel {r.kessel:.0f} °C" if r.kessel is not None \
            else f"Anheizen{wait}", "hot"
        d.detail = "Tür schließen, Brand baut sich auf"
        d.empfehlung = "nicht_oeffnen"

    elif ph in ("teillast", "volllast"):
        name = "Volllast" if ph == "volllast" else "Teillast"
        d.headline = f"Brennt ({name})" + (f" · Kessel {r.kessel:.0f} °C" if r.kessel is not None else "") \
            + f" · Puffer {pct}" + (f" ({trend_txt})" if trend_txt else "")
        d.tone = "hot"
        bits = []
        if platz:
            bits.append(platz)
        usual = CO2_USUAL[ph]
        if r.co2 is not None and (phase_min is None or phase_min >= CO2_MIN_PHASE_MIN):
            if r.co2 < usual - CO2_WEAK_DELTA:
                d.abbrand = "schwach"
                bits.append(f"Abbrand schwächer als sonst (CO₂ {r.co2:.0f} % statt ~{usual:.0f} %)")
            else:
                d.abbrand = "gewohnt"
        d.detail = " · ".join(bits)
        d.empfehlung = "nicht_oeffnen"

    elif ph == "gluterhalt":
        if fire_out is not None:
            d.headline, d.tone = f"Feuer vermutlich ausgegangen · Puffer erst {pct}", "warn"
            d.detail = f"nach nur {_fmt_dur(fire_out.brenn_min)} Brennzeit im Gluterhalt · {platz}".strip(" ·")
            d.empfehlung = "nachlegen"
            d.feuer_aus = fire_out.to_dict()
        elif lad is not None and lad >= LADUNG_VOLL:
            d.headline, d.tone = f"Glut hält · Puffer voll ({pct}) – Nachlegen nicht nötig", "ok"
            d.empfehlung = "nicht_nachlegen"
        elif lad is not None and lad >= LADUNG_NIEDRIG:
            d.headline, d.tone = f"Holz abgebrannt, Glut hält · Puffer {pct}", "info"
            d.detail = f"Nachlegen sinnvoll, wenn Wärme gebraucht wird · {platz}".strip(" ·")
        else:
            d.headline, d.tone = f"Holz abgebrannt, Glut hält · Puffer erst {pct} – Nachlegen lohnt", "warn"
            d.detail = platz
            d.empfehlung = "nachlegen"

    elif ph == "nachlegen":
        if "NICHT NACHLEGEN" in msg:
            d.headline, d.tone = f"Nachlegen laut Kessel nicht nötig · Puffer {pct}", "ok"
            d.empfehlung = "nicht_nachlegen"
        elif "NICHT ÖFFNEN" in msg or "NICHT OEFFNEN" in msg:
            d.headline, d.tone = f"Verbrennung aktiv · Tür geschlossen lassen · Puffer {pct}", "hot"
            d.empfehlung = "nicht_oeffnen"
        else:
            d.headline, d.tone = f"Nachlegen-Fenster offen · Puffer {pct}", "info"
            d.detail = platz
        if r.meldung and not d.detail:
            d.detail = r.meldung.capitalize() if r.meldung.isupper() else r.meldung

    else:
        d.headline = f"Betriebsmodus: {r.modus}"

    # Auffaelligkeiten aus Kombinationen
    if r.warm is not None and r.warm < 40 and r.top is not None and r.top >= 55:
        d.hinweise.append(f"Warmwasser nur {r.warm:.0f} °C bei {r.top:.0f} °C Puffer oben")
    if r.kessel is not None and r.kessel >= 85:
        d.hinweise.append(f"Kessel sehr heiß ({r.kessel:.0f} °C)")
    if r.heizfreigabe and r.vl_eg is not None and r.top is not None and r.vl_eg < r.top - 25 and ph == "aus" \
            and lad is not None and lad < LADUNG_NIEDRIG:
        d.hinweise.append("Heizkreis läuft mit kühlem Vorlauf – Puffer ist fast leer")
    return d


# ---------------------------------------------------------------------------
# Feuer-Bilanz aus dem Verlauf
# ---------------------------------------------------------------------------

@dataclass
class Fire:
    start: datetime
    end: datetime
    kind: str                          # feuer | fehlstart | nachlege_fenster | unvollstaendig
    minutes: dict
    ladung_start: Optional[float] = None
    ladung_peak: Optional[float] = None
    kessel_peak: Optional[float] = None
    co2_voll: Optional[float] = None
    sek_voll: Optional[float] = None
    kwh: Optional[float] = None
    fuellung: Optional[float] = None
    urteil: str = ""

    @property
    def dauer_min(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    @property
    def brenn_min(self) -> float:
        return self.minutes.get("teillast", 0.0) + self.minutes.get("volllast", 0.0)

    @property
    def glut_anteil(self) -> float:
        return self.minutes.get("gluterhalt", 0.0) / self.dauer_min if self.dauer_min > 0 else 0.0

    @property
    def zuwachs_pct(self) -> Optional[float]:
        if self.ladung_start is None or self.ladung_peak is None:
            return None
        return max(0.0, self.ladung_peak - self.ladung_start)


def _fmt_dur(minutes: float) -> str:
    h, m = divmod(int(round(minutes)), 60)
    return f"{h} h {m:02d}" if h else f"{m} min"


def _verdict(f: Fire) -> str:
    z = f.zuwachs_pct
    if z is None:
        return ""
    peak = f.ladung_peak or 0.0
    if f.glut_anteil >= 0.5 and peak < LADUNG_VOLL:
        return (f"viel Glut ({f.glut_anteil * 100:.0f} %) bei nur {peak:.0f} % Puffer – "
                "Füllung vermutlich zu klein oder zu früh abgebrannt")
    if f.glut_anteil >= 0.5:
        return "Puffer wurde voll, Glut danach normal"
    if z >= 35 and f.glut_anteil <= 0.35:
        return "gutes Feuer"
    if z < 15:
        return "kaum Wärme eingespeichert – sehr kleine Füllung oder hoher Verbrauch"
    return "mittleres Feuer"


def fire_summaries(rows: Sequence[dict], cfg: Optional[hs.StorageConfig] = None) -> list[Fire]:
    """rows: aufsteigend nach ts (datetime), Schluessel modus, ladung, kessel, co2, sekundaer.

    Ein Lauf = zusammenhaengende Nicht-STANDBY-Messungen. 'feuer' braucht >= 10 min Teil-/Volllast;
    sonst Fehlstart (nur START), Nachlege-Fenster (nur NACHLEGEN) oder unvollstaendig.
    """
    cfg = cfg or hs.load_storage_config()
    kwh_per_pct = cfg.puffer_kwh_per_k * (T_LADUNG_100 - T_LADUNG_0) / 100.0
    fill_kwh = FILL_RM * cfg.wood_kwh_per_rm * cfg.boiler_efficiency
    out: list[Fire] = []
    run: list[dict] = []

    def close():
        if not run:
            return
        mins: dict = {}
        for a, b in zip(run, run[1:] + [None]):
            nxt = b["ts"] if b else a["ts"] + timedelta(minutes=1)
            dt = min(5.0, max(0.0, (nxt - a["ts"]).total_seconds() / 60.0))
            mins[phase_of(a["modus"])] = mins.get(phase_of(a["modus"]), 0.0) + dt
        brenn = mins.get("teillast", 0.0) + mins.get("volllast", 0.0)
        if brenn >= REAL_FIRE_MIN:
            kind = "feuer"
        elif mins.get("anheizen", 0.0) > 0:
            kind = "fehlstart"
        elif mins.get("nachlegen", 0.0) > 0 and len(mins) == 1:
            kind = "nachlege_fenster"
        else:
            kind = "unvollstaendig"
        lads = [x["ladung"] for x in run if x.get("ladung") is not None]
        fire = Fire(start=run[0]["ts"], end=run[-1]["ts"] + timedelta(minutes=1), kind=kind, minutes=mins)
        if lads:
            head = lads[: max(1, min(5, len(lads)))]
            fire.ladung_start = min(head)
            fire.ladung_peak = max(lads)
        ks = [x["kessel"] for x in run if x.get("kessel") is not None]
        fire.kessel_peak = max(ks) if ks else None
        volls = [x for x in run if phase_of(x["modus"]) == "volllast"]
        co2 = [x["co2"] for x in volls if x.get("co2") is not None and 0 <= x["co2"] < CO2_IDLE]
        sek = [x["sekundaer"] for x in volls if x.get("sekundaer") is not None]
        fire.co2_voll = sum(co2) / len(co2) if co2 else None
        fire.sek_voll = sum(sek) / len(sek) if sek else None
        if kind == "feuer" and fire.zuwachs_pct is not None:
            fire.kwh = fire.zuwachs_pct * kwh_per_pct
            fire.fuellung = fire.kwh / fill_kwh if fill_kwh > 0 else None
        fire.urteil = _verdict(fire) if kind == "feuer" else {
            "fehlstart": "Start ohne Brand", "nachlege_fenster": "Nachlege-Fenster ohne Brand",
            "unvollstaendig": ""}[kind]
        out.append(fire)

    prev = None
    for r in rows:
        active = phase_of(r["modus"]) not in ("aus", "unbekannt")
        if prev is not None and (r["ts"] - prev) > timedelta(minutes=GAP_MIN):
            close()
            run = []
        prev = r["ts"]
        if active:
            run.append(r)
        else:
            close()
            run = []
    close()
    return out


def fire_line(f: Fire) -> str:
    """Eine Zeile fuer die Anzeige (nur echte Feuer)."""
    brenn = f.brenn_min / f.dauer_min * 100 if f.dauer_min else 0
    parts = [f"{f.start:%d.%m. %H:%M}", _fmt_dur(f.dauer_min), f"{brenn:.0f} % Teil-/Volllast"]
    if f.ladung_start is not None and f.ladung_peak is not None:
        z = f"Puffer {f.ladung_start:.0f}→{f.ladung_peak:.0f} %"
        if f.fuellung is not None:
            z += f" (≈ {_fmt_fill(f.fuellung)})"
        parts.append(z)
    if f.urteil:
        parts.append(f.urteil)
    return " · ".join(parts)


def fire_lines(fires: Sequence[Fire], n: int = 2) -> list[str]:
    real = [f for f in fires if f.kind == "feuer"]
    return [fire_line(f) for f in real[-n:]]


def false_start_count(fires: Sequence[Fire]) -> int:
    return sum(1 for f in fires if f.kind == "fehlstart")


# ---------------------------------------------------------------------------
# Speicher: Tabelle bmk_status
# ---------------------------------------------------------------------------

STATUS_COLUMNS = ("modus", "kessel", "aussen", "ladung", "saugzug", "primaer", "sekundaer", "co2", "pumpe",
                  "heizfreigabe", "vl_eg", "vl_og", "betriebsstunden", "meldung")


def status_row(r: BmkReading) -> dict:
    return {"modus": r.modus, "kessel": r.kessel, "aussen": r.aussen, "ladung": r.ladung, "saugzug": r.saugzug,
            "primaer": r.primaer, "sekundaer": r.sekundaer, "co2": r.co2, "pumpe": r.pumpe,
            "heizfreigabe": None if r.heizfreigabe is None else int(r.heizfreigabe),
            "vl_eg": r.vl_eg, "vl_og": r.vl_og, "betriebsstunden": r.betriebsstunden, "meldung": r.meldung}


def reading_from_row(ts: datetime, row: dict) -> BmkReading:
    return BmkReading(ts=ts, modus=row.get("modus") or "", kessel=row.get("kessel"), aussen=row.get("aussen"),
                      top=None, mid=None, bot=None, warm=None, ladung=row.get("ladung"),
                      saugzug=row.get("saugzug"), primaer=row.get("primaer"), sekundaer=row.get("sekundaer"),
                      co2=row.get("co2"), pumpe=row.get("pumpe"),
                      heizfreigabe=None if row.get("heizfreigabe") is None else bool(row["heizfreigabe"]),
                      vl_eg=row.get("vl_eg"), vl_og=row.get("vl_og"),
                      betriebsstunden=row.get("betriebsstunden"), meldung=row.get("meldung"))


def recent_fires(store, days: float = 14.0, cfg: Optional[hs.StorageConfig] = None) -> list[Fire]:
    """Feuer-Bilanz der letzten Tage aus der Datenbank (leer, wenn noch nichts gespeichert ist)."""
    try:
        rows = store.get_bmk_status(days=days)
    except Exception as exc:
        logger.info("[BMK] Verlauf nicht lesbar: %s", exc)
        return []
    return fire_summaries(rows, cfg)


# gemeinsamer Live-Interpreter fuer den Collector-Thread
_LIVE = LiveInterpreter()


def live() -> LiveInterpreter:
    return _LIVE
