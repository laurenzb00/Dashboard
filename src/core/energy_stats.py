"""Strom-Auswertung aus den Fronius-Messwerten: Monatsbilanz, Grundlast, Dauerlaeufer.

Antworten auf drei Fragen, die die Auswertung vom 13.11.2026 aufgeworfen hat:

* Monatsbilanz: PV, Verbrauch, Netzbezug, Einspeisung -> Autarkie und Eigenverbrauch je Monat.
* Grundlast: was das Haus mindestens zieht (5-%-Quantil der Minutenwerte) - bei der Auswertung
  0,53 kW = 38 % des gesamten Verbrauchs.
* Dauerlaeufer: Geraete, die regelmaessig mit gleicher Leistung und Dauer laufen (Thermostat-Geraete
  wie Boiler, Heizstab, Waermepumpe). Gefunden wurde ein 2,7-kW-Geraet mit 45 min je Lauf, im
  November 4x taeglich = 28 % des Verbrauchs.

Alles reine Funktionen auf Listen/Arrays plus zwei SQL-Lader; keine Tk-Abhaengigkeit. Die Abfragen
laufen ueber die ganze fronius-Tabelle und gehoeren in einen Worker-Thread.
Vorzeichen (wie im Rest des Dashboards): grid > 0 = Bezug, load < 0 = Verbrauch, batt > 0 = Entladen.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

import numpy as np

BASELOAD_QUANTILE = 5.0
MINUTE_DAYS = 14
CYCLE_EXCESS_KW = 1.5            # so viel ueber der laufenden Grundlast = "Grossverbraucher laeuft"
CYCLE_MIN_MIN = 15
CYCLE_MAX_MIN = 180
CYCLE_BASE_WINDOW_MIN = 121
CYCLE_BIN_KW = 0.5
CYCLE_MIN_RUNS_PER_DAY = 0.7
CYCLE_MAX_DURATION_SPREAD = 0.35  # (75%-25%)/Median der Laufzeit - Thermostat-Geraete laufen gleich lang

_PV = "CASE WHEN pv_power IS NULL THEN 0 WHEN pv_power > 200 THEN pv_power / 1000.0 WHEN pv_power < 0 THEN 0 ELSE pv_power END"


@dataclass(frozen=True)
class MonthBalance:
    month: str                    # "2026-10"
    hours: int
    pv_kwh: float
    load_kwh: float
    import_kwh: float
    export_kwh: float

    @property
    def autarky_pct(self) -> Optional[float]:
        return None if self.load_kwh <= 0 else max(0.0, min(100.0, 100.0 * (1.0 - self.import_kwh / self.load_kwh)))

    @property
    def self_use_pct(self) -> Optional[float]:
        return None if self.pv_kwh <= 0 else max(0.0, min(100.0, 100.0 * (1.0 - self.export_kwh / self.pv_kwh)))

    @property
    def days(self) -> float:
        return self.hours / 24.0


@dataclass(frozen=True)
class CyclicLoad:
    kw: float                     # Leistung des Geraets (ueber Grundlast)
    minutes: float                # typische Laufzeit
    runs_per_day: float
    kwh_per_day: float
    share_pct: float              # Anteil am Verbrauch im betrachteten Zeitraum
    grid_share_pct: float         # davon aus dem Netz
    last_start: Optional[datetime] = None      # lokal, naiv


@dataclass
class EnergyStats:
    months: list[MonthBalance] = field(default_factory=list)
    baseload_kw: Optional[float] = None
    baseload_share_pct: Optional[float] = None
    mean_load_kw: Optional[float] = None
    cyclic: list[CyclicLoad] = field(default_factory=list)
    minute_days: float = 0.0


# ---------------------------------------------------------------------------
# Laden
# ---------------------------------------------------------------------------

def load_months(conn) -> list[MonthBalance]:
    """Monatsbilanz aus Stundenmitteln (Monat nach UTC - der Unterschied zur Ortszeit ist hier egal)."""
    rows = conn.execute(
        "SELECT substr(h, 1, 7), COUNT(*), SUM(pv), SUM(ld), SUM(imp), SUM(ex) FROM ("
        f"SELECT substr(timestamp, 1, 13) AS h, AVG({_PV}) AS pv, AVG(ABS(load_power)) AS ld, "
        "AVG(CASE WHEN grid_power > 0 THEN grid_power ELSE 0 END) AS imp, "
        "AVG(CASE WHEN grid_power < 0 THEN -grid_power ELSE 0 END) AS ex "
        "FROM fronius WHERE load_power IS NOT NULL AND grid_power IS NOT NULL GROUP BY 1) GROUP BY 1 ORDER BY 1").fetchall()
    return [MonthBalance(m, int(n), float(pv or 0), float(ld or 0), float(imp or 0), float(ex or 0))
            for m, n, pv, ld, imp, ex in rows if m]


def load_minutes(conn, days: int = MINUTE_DAYS, now: Optional[datetime] = None):
    """(Minutenbeginn Unix, Last kW, Netzbezug kW) als Arrays, lueckenlos nur wo Daten da sind."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT CAST(strftime('%s', substr(timestamp, 1, 16) || ':00') AS INTEGER), AVG(ABS(load_power)), "
        "AVG(CASE WHEN grid_power > 0 THEN grid_power ELSE 0 END), COUNT(*) FROM fronius "
        "WHERE timestamp >= ? AND load_power IS NOT NULL GROUP BY 1 ORDER BY 1", (cutoff,)).fetchall()
    rows = [r for r in rows if r[0] is not None]
    t = np.array([r[0] for r in rows], dtype=float)
    load = np.array([r[1] for r in rows], dtype=float)
    imp = np.array([0.0 if r[2] is None else r[2] for r in rows], dtype=float)
    return t, load, imp


# ---------------------------------------------------------------------------
# Auswerten
# ---------------------------------------------------------------------------

def baseload(load: Sequence[float]) -> Optional[float]:
    a = np.asarray(load, dtype=float)
    a = a[~np.isnan(a)]
    if len(a) < 600:               # weniger als 10 h Minutenwerte
        return None
    return float(np.percentile(a, BASELOAD_QUANTILE))


def _running_base(load: np.ndarray, window: int = CYCLE_BASE_WINDOW_MIN) -> np.ndarray:
    """Laufende Grundlast: 20-%-Quantil in einem Fenster von ~2 h."""
    n = len(load)
    if n < window:
        return np.full(n, np.percentile(load, 20) if n else 0.0)
    half = window // 2
    padded = np.concatenate([np.full(half, load[0]), load, np.full(half, load[-1])])
    view = np.lib.stride_tricks.sliding_window_view(padded, window)
    return np.percentile(view, 20, axis=1)


def find_runs(t: np.ndarray, load: np.ndarray, imp: Optional[np.ndarray] = None) -> list[tuple]:
    """Laeufe grosser Verbraucher: [(Start Unix, Minuten, kW ueber Grundlast, Netzbezug kW)]."""
    if len(t) < 60:
        return []
    imp = np.zeros(len(t)) if imp is None else imp
    excess = load - _running_base(load)
    on = excess > CYCLE_EXCESS_KW
    runs = []
    i, n = 0, len(t)
    while i < n:
        if not on[i]:
            i += 1
            continue
        j = i
        # kurze Aussetzer (<= 2 min) und Messluecken (<= 3 min) ueberbruecken
        while j + 1 < n and t[j + 1] - t[j] <= 180 and (on[j + 1] or (j + 2 < n and on[j + 2] and t[j + 2] - t[j] <= 180)):
            j += 1
        minutes = (t[j] - t[i]) / 60.0 + 1.0
        if CYCLE_MIN_MIN <= minutes <= CYCLE_MAX_MIN:
            seg = slice(i, j + 1)
            runs.append((float(t[i]), minutes, float(np.median(excess[seg])), float(np.mean(imp[seg]))))
        i = j + 1
    return runs


def find_cyclic(t: np.ndarray, load: np.ndarray, imp: Optional[np.ndarray] = None) -> list[CyclicLoad]:
    """Geraete, die regelmaessig mit gleicher Leistung und aehnlicher Dauer laufen, groesster Verbraucher zuerst."""
    runs = find_runs(t, load, imp)
    if not runs or len(t) < 2:
        return []
    days = max(1.0, len(t) / 1440.0)
    total_kwh = float(np.nansum(load)) / 60.0
    groups: dict[int, list] = {}
    for r in runs:
        groups.setdefault(int(round(r[2] / CYCLE_BIN_KW)), []).append(r)
    # Nachbar-Klassen zusammenlegen, wenn das Geraet genau auf einer Klassengrenze liegt
    out = []
    used: set[int] = set()
    for key in sorted(groups, key=lambda k: -len(groups[k])):
        if key in used:
            continue
        members = list(groups[key])
        used.add(key)
        for nb in (key - 1, key + 1):
            if nb in groups and nb not in used and len(groups[nb]) >= 0.3 * len(groups[key]):
                kw_a = np.median([m[2] for m in members])
                kw_b = np.median([m[2] for m in groups[nb]])
                if abs(kw_a - kw_b) <= 0.6 * CYCLE_BIN_KW:
                    members += groups[nb]
                    used.add(nb)
        dur = np.array([m[1] for m in members])
        if len(members) / days < CYCLE_MIN_RUNS_PER_DAY or len(members) < 5:
            continue
        med = float(np.median(dur))
        spread = float(np.percentile(dur, 75) - np.percentile(dur, 25)) / max(med, 1.0)
        if spread > CYCLE_MAX_DURATION_SPREAD:
            continue
        kw = float(np.median([m[2] for m in members]))
        kwh = float(sum(m[1] * m[2] for m in members)) / 60.0
        grid_kwh = float(sum(m[1] * min(m[3], m[2]) for m in members)) / 60.0
        out.append(CyclicLoad(
            kw=kw, minutes=med, runs_per_day=len(members) / days, kwh_per_day=kwh / days,
            share_pct=100.0 * kwh / total_kwh if total_kwh > 0 else 0.0,
            grid_share_pct=100.0 * grid_kwh / kwh if kwh > 0 else 0.0,
            last_start=datetime.fromtimestamp(max(m[0] for m in members))))
    out.sort(key=lambda c: -c.kwh_per_day)
    return out


def compute(conn, now: Optional[datetime] = None, minute_days: int = MINUTE_DAYS) -> EnergyStats:
    """Alles zusammen. conn = sqlite3-Verbindung der Mess-Datenbank (store.conn)."""
    stats = EnergyStats(months=load_months(conn))
    t, load, imp = load_minutes(conn, minute_days, now)
    stats.minute_days = len(t) / 1440.0
    stats.baseload_kw = baseload(load)
    if len(load):
        stats.mean_load_kw = float(np.nanmean(load))
        if stats.baseload_kw is not None and stats.mean_load_kw > 0:
            stats.baseload_share_pct = 100.0 * stats.baseload_kw / stats.mean_load_kw
    stats.cyclic = find_cyclic(t, load, imp)
    return stats


# ---------------------------------------------------------------------------
# Text fuer die Anzeige
# ---------------------------------------------------------------------------

_MONATE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def month_label(month: str) -> str:
    try:
        return f"{_MONATE[int(month[5:7]) - 1]} {month[2:4]}"
    except (ValueError, IndexError):
        return month


def summary_lines(stats: EnergyStats, months: int = 4) -> list[str]:
    """Kurze Klartext-Zeilen fuer eine Karte im Dashboard."""
    lines = []
    if stats.baseload_kw is not None:
        s = f"Grundlast {stats.baseload_kw:.2f} kW ≈ {stats.baseload_kw * 24:.0f} kWh/Tag".replace(".", ",")
        if stats.baseload_share_pct is not None:
            s += f" ({stats.baseload_share_pct:.0f} % des Verbrauchs)"
        lines.append(s)
    for c in stats.cyclic[:2]:
        lines.append((f"Dauerläufer {c.kw:.1f} kW: {c.runs_per_day:.1f}× täglich je {c.minutes:.0f} min ≈ "
                      f"{c.kwh_per_day:.1f} kWh/Tag").replace(".", ",")
                     + f" ({c.share_pct:.0f} % des Verbrauchs, {c.grid_share_pct:.0f} % davon aus dem Netz)")
    recent = [m for m in stats.months if m.hours >= 24 * 5][-months:]
    if recent:
        parts = []
        for m in recent:
            a = m.autarky_pct
            parts.append(f"{month_label(m.month)} {a:.0f} %" if a is not None else f"{month_label(m.month)} –")
        lines.append("Autarkie: " + " · ".join(parts))
        parts = [f"{month_label(m.month)} {m.self_use_pct:.0f} %" for m in recent if m.self_use_pct is not None]
        if parts:
            lines.append("Eigenverbrauch: " + " · ".join(parts))
    return lines
