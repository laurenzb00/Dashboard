"""Verlauf der Raumtemperaturen (Thermostate) - eigene kleine SQLite-DB.

* Datei: data/climate_history.db (getrennt von data.db, keine Schema-Migration).
* Zeit als Unix-Sekunden (UTC) in 5-Minuten-Buckets; pro Bucket gewinnt der
  letzte Messwert, "heizt" wird ueber den Bucket ODER-verknuepft.
* Live-Werte kommen bei jeder Abfrage aus dem Tado-Tab (add_rooms).
* Rueckfuellen der letzten 2 Tage beim Start:
  - Home Assistant: /api/history/period (points_from_ha_history)
  - Tado direkt: Tagesreport get_historic/getHistoric (points_from_tado_day_report)
  Rueckgefuellte Punkte ueberschreiben nie live gemessene (INSERT OR IGNORE).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

BUCKET_S = 300
KEEP_DAYS = 14

# (ts, current, target, heating)
Point = Tuple[int, Optional[float], Optional[float], bool]


def bucket(ts: float) -> int:
    return int(ts // BUCKET_S * BUCKET_S)


def _default_path() -> Path:
    try:
        from core.datastore import DATA_DIR
        return Path(DATA_DIR) / "climate_history.db"
    except Exception:
        return Path(__file__).resolve().parents[2] / "data" / "climate_history.db"


class ClimateHistory:
    def __init__(self, path: Optional[str | Path] = None):
        self.path = Path(path) if path else _default_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), check_same_thread=False, timeout=10)
        with self._lock:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS samples (room TEXT NOT NULL, ts INTEGER NOT NULL, "
                "current REAL, target REAL, heating INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (room, ts))"
            )
            self._db.commit()
        self._last_prune = 0.0

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- schreiben -----------------------------------------------------------

    def add_rooms(self, rooms: Iterable[Any], ts: Optional[float] = None) -> None:
        b = bucket(time.time() if ts is None else ts)
        rows = [(str(r.id), b, r.current, None if r.mode == "off" else r.target, int(bool(r.heating)))
                for r in rooms if getattr(r, "available", True)]
        if not rows:
            return
        with self._lock:
            self._db.executemany(
                "INSERT INTO samples (room, ts, current, target, heating) VALUES (?,?,?,?,?) "
                "ON CONFLICT(room, ts) DO UPDATE SET current=COALESCE(excluded.current, current), "
                "target=excluded.target, heating=MAX(heating, excluded.heating)", rows)
            self._db.commit()
        if time.time() - self._last_prune > 6 * 3600:
            self.prune()

    def add_points(self, room: str, points: Sequence[Point]) -> int:
        rows = [(str(room), bucket(ts), cur, tgt, int(bool(h))) for ts, cur, tgt, h in points]
        with self._lock:
            before = self._db.total_changes
            self._db.executemany("INSERT OR IGNORE INTO samples (room, ts, current, target, heating) "
                                 "VALUES (?,?,?,?,?)", rows)
            self._db.commit()
            return self._db.total_changes - before

    def prune(self, keep_days: int = KEEP_DAYS) -> None:
        self._last_prune = time.time()
        with self._lock:
            self._db.execute("DELETE FROM samples WHERE ts < ?", (int(time.time() - keep_days * 86400),))
            self._db.commit()

    # --- lesen ---------------------------------------------------------------

    def query(self, room: str, since_ts: float) -> List[Point]:
        with self._lock:
            cur = self._db.execute("SELECT ts, current, target, heating FROM samples "
                                   "WHERE room=? AND ts>=? ORDER BY ts", (str(room), int(since_ts)))
            return [(ts, c, t, bool(h)) for ts, c, t, h in cur.fetchall()]

    def coverage_start(self, room: str, since_ts: float) -> Optional[int]:
        """Aeltester gespeicherter Zeitpunkt ab since_ts (None = nichts da)."""
        with self._lock:
            row = self._db.execute("SELECT MIN(ts) FROM samples WHERE room=? AND ts>=?",
                                   (str(room), int(since_ts))).fetchone()
        return row[0] if row and row[0] is not None else None

    def needs_backfill(self, room: str, hours: float = 48) -> bool:
        since = time.time() - hours * 3600
        start = self.coverage_start(room, since)
        return start is None or start > since + 2 * 3600


# --- Rueckfuellen ---------------------------------------------------------------

def _parse_iso(s: Any) -> Optional[float]:
    if not s:
        return None
    try:
        txt = str(s).replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def points_from_ha_history(states: Sequence[Dict[str, Any]], start_ts: float,
                           end_ts: Optional[float] = None) -> List[Point]:
    """HA-Zustandsliste einer climate-Entitaet -> 5-Minuten-Raster (Werte fortgeschrieben)."""
    events = []
    for st in states or []:
        ts = _parse_iso(st.get("last_updated") or st.get("last_changed"))
        if ts is None:
            continue
        a = st.get("attributes") or {}
        state = str(st.get("state") or "").lower()
        if state in ("unavailable", "unknown"):
            events.append((ts, None, None, False))
            continue
        events.append((ts, _f(a.get("current_temperature")),
                       None if state == "off" else _f(a.get("temperature")),
                       str(a.get("hvac_action") or "").lower() == "heating"))
    events.sort(key=lambda e: e[0])
    if not events:
        return []
    end_ts = time.time() if end_ts is None else end_ts
    out: List[Point] = []
    i, cur = 0, None
    t = bucket(max(start_ts, events[0][0])) + BUCKET_S
    while t <= end_ts:
        heat_in_bucket = False
        while i < len(events) and events[i][0] <= t:
            cur = events[i]
            heat_in_bucket = heat_in_bucket or cur[3]
            i += 1
        if cur is not None and cur[1] is not None:
            out.append((t - BUCKET_S, cur[1], cur[2], bool(cur[3] or heat_in_bucket)))
        t += BUCKET_S
    return out


def points_from_tado_day_report(report: Dict[str, Any]) -> List[Point]:
    """Tado-Tagesreport (dayReport) -> Punkte an den Messzeitpunkten (~15 min)."""
    if not isinstance(report, dict):
        return []
    temps = (((report.get("measuredData") or {}).get("insideTemperature") or {}).get("dataPoints") or [])

    def intervals(key):
        out = []
        for iv in ((report.get(key) or {}).get("dataIntervals") or []):
            a, b = _parse_iso(iv.get("from")), _parse_iso(iv.get("to"))
            if a is not None and b is not None:
                out.append((a, b, iv.get("value")))
        return out

    heat_iv = intervals("callForHeat")
    set_iv = intervals("settings")
    out: List[Point] = []
    for dp in temps:
        ts = _parse_iso(dp.get("timestamp"))
        val = dp.get("value") or {}
        cur = _f(val.get("celsius") if isinstance(val, dict) else val)
        if ts is None or cur is None:
            continue
        heating = any(a <= ts < b and str(v or "NONE").upper() != "NONE" for a, b, v in heat_iv)
        target = None
        for a, b, v in set_iv:
            if a <= ts < b and isinstance(v, dict) and str(v.get("power", "")).upper() == "ON":
                target = _f((v.get("temperature") or {}).get("celsius"))
                break
        out.append((int(ts), cur, target, heating))
    return out
