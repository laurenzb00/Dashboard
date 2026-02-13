
from __future__ import annotations
"""Central SQLite datastore for PV and heating metrics."""
from .time_utils import ensure_utc
from collections import defaultdict
import csv
import os
import sqlite3
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, List, Optional


DB_PATH = Path(__file__).resolve().with_name("data.db")
DATA_DIR = DB_PATH.parent.parent.parent / "data"

_SHARED_LOCK = threading.Lock()
_SHARED_STORE: Optional["DataStore"] = None


def set_shared_datastore(store: "DataStore") -> None:
    global _SHARED_STORE
    with _SHARED_LOCK:
        _SHARED_STORE = store


def get_shared_datastore() -> "DataStore":
    global _SHARED_STORE
    with _SHARED_LOCK:
        if _SHARED_STORE is None:
            _SHARED_STORE = DataStore()
        return _SHARED_STORE


def close_shared_datastore() -> None:
    global _SHARED_STORE
    with _SHARED_LOCK:
        if _SHARED_STORE is not None:
            try:
                _SHARED_STORE.close()
            except Exception:
                pass
            _SHARED_STORE = None


class DataStore:
    def normalize_heating_record(self, record: dict, stale_minutes: int = 5) -> dict:
        """
        Normalize heating record for UI: parse timestamp, map keys, handle staleness.
        Zeitstrategie: UTC everywhere. Alle Datetimes werden als UTC interpretiert.
        Mapping: Kessel = BMK_KESSEL_C, Warmwasser = BMK_WARMWASSER_C (Boiler), keine Fallbacks.
        """
        from .schema import BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C, BUF_MID_C, BUF_BOTTOM_C
        ts = record.get('timestamp')
        dt = _parse_iso_timestamp(ts)
        dt = ensure_utc(dt) if dt else None
        now = datetime.now(timezone.utc)
        age_min = (now - dt).total_seconds() / 60 if dt else None
        outdoor = _safe_float(record.get('outdoor'))
        kessel = _safe_float(record.get(BMK_KESSEL_C))
        warmwasser = _safe_float(record.get(BMK_WARMWASSER_C))
        top = _safe_float(record.get(BUF_TOP_C))
        mid = _safe_float(record.get(BUF_MID_C))
        bot = _safe_float(record.get(BUF_BOTTOM_C))
        is_stale = age_min is not None and age_min > stale_minutes
        return {
            'timestamp': ts,
            'datetime': dt,
            'age_min': age_min,
            'is_stale': is_stale,
            'outdoor': outdoor,
            BMK_KESSEL_C: kessel,
            BMK_WARMWASSER_C: warmwasser,
            BUF_TOP_C: top,
            BUF_MID_C: mid,
            BUF_BOTTOM_C: bot,
        }
    """SQLite-basierter Datenspeicher für schnelle Zugriffe."""

    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = str(db_path)
        self.conn = None
        self._lock = threading.RLock()
        self._last_ingest_dt: Optional[datetime] = None
        # Check if DB is locked by another process (try to acquire exclusive lock)
        try:
            test_conn = sqlite3.connect(self.db_path, timeout=2)
            try:
                test_conn.execute("BEGIN EXCLUSIVE")
                test_conn.execute("ROLLBACK")
            except sqlite3.OperationalError as e:
                if "database is locked" in str(e):
                    logging.error(f"Die Datenbank {self.db_path} ist bereits von einem anderen Prozess gesperrt! Bitte stelle sicher, dass kein zweites Dashboard oder Import-Skript läuft.")
                    raise SystemExit(1)
            finally:
                test_conn.close()
        except Exception as e:
            logging.error(f"Fehler beim Prüfen auf DB-Lock: {e}")
        self._init_db()
        self._hydrate_last_ingest_cache()
    
    def _init_db(self):
        """Initialisiere Datenbank mit Tabellen."""
        # check_same_thread=False erlaubt Nutzung in verschiedenen Threads (safe für read-only)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=15.0)
        # Pi 5 Optimierungen: WAL + moderater Cache
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA cache_size=-32000")  # 32MB Cache (sicherer)
        self.conn.execute("PRAGMA mmap_size=67108864")  # 64MB Memory-Map (reduziert)
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.conn.execute("PRAGMA busy_timeout=5000")
        cursor = self.conn.cursor()
        
        # Fronius PV Daten
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS fronius (
                id INTEGER PRIMARY KEY,
                timestamp TEXT UNIQUE,
                pv_power REAL,
                grid_power REAL,
                batt_power REAL,
                soc REAL,
                load_power REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Backward-compatible migration: add load_power if missing
        try:
            cols = [row[1] for row in cursor.execute("PRAGMA table_info(fronius)").fetchall()]
            if "load_power" not in cols:
                cursor.execute("ALTER TABLE fronius ADD COLUMN load_power REAL")
        except Exception:
            pass
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_fronius_ts ON fronius(timestamp)")
        
        # Ertrag Historie
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS ertrag_history (
                id INTEGER PRIMARY KEY,
                date TEXT UNIQUE,
                total_ertrag REAL,
                daily_ertrag REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_ertrag_date ON ertrag_history(date)")
        
        # Heizung/Pufferspeicher
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS heating (
                id INTEGER PRIMARY KEY,
                timestamp TEXT UNIQUE,
                kesseltemp REAL,
                außentemp REAL,
                puffer_top REAL,
                puffer_mid REAL,
                puffer_bot REAL,
                warmwasser REAL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_heating_ts ON heating(timestamp)")
        
        self.conn.commit()

    def _hydrate_last_ingest_cache(self) -> None:
        """Populate ingest cache from existing DB content on startup."""
        with self._lock:
            ts = self._get_latest_timestamp_unlocked()
            if not ts:
                self._last_ingest_dt = None
                return
            parsed = _parse_iso_timestamp(ts)
            self._last_ingest_dt = ensure_utc(parsed) if parsed else None
    
    def import_fronius_csv(self, csv_path: str | os.PathLike[str]) -> bool:
        """Importiere FroniusDaten.csv in Datenbank."""
        if not os.path.exists(csv_path):
            return False

        cursor = self.conn.cursor()
        count = 0

        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        ts = row.get('Zeitstempel') or row.get('timestamp')
                        if not ts:
                            continue
                        pv = _safe_float(_first_value(
                            row, 'PV-Leistung (kW)', 'PV', 'PV [kW]', 'P_PV'))
                        grid = _safe_float(_first_value(
                            row, 'Netz-Leistung (kW)', 'Netz', 'Netz [kW]', 'P_Grid'))
                        batt = _safe_float(_first_value(
                            row, 'Batterie-Leistung (kW)', 'Batterie', 'Batterie [kW]', 'P_Akku'))
                        soc = _safe_float(_first_value(
                            row, 'Batterieladestand (%)', 'SOC', 'SoC', 'State of Charge'))

                        pv = _normalize_power_kw(pv)
                        grid = _normalize_power_kw(grid)
                        batt = _normalize_power_kw(batt)

                        load_power = _safe_float(_first_value(
                            row, 'Hausverbrauch (kW)', 'Hausverbrauch', 'P_Load', 'Load'))
                        load_power = _normalize_power_kw(load_power)
                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO fronius
                            (timestamp, pv_power, grid_power, batt_power, soc, load_power)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (ts, pv, grid, batt, soc, load_power),
                        )
                        # Update ingest cache after each insert
                        self._update_last_ingest_locked(ts)
                        count += 1

                        if count % 2000 == 0:
                            self.conn.commit()
                            print(f"[DB] Imported {count} Fronius records...")
                    except Exception:
                        continue

            self.conn.commit()
            print(f"[DB] ✅ Imported {count} Fronius records")
            return True
        except Exception as e:
            print(f"[DB] ❌ Import error: {e}")
            return False
    
    def get_last_fronius_record(self):
        """Hole letzten PV-Record als dict mit final keys (schema.py)."""
        from core.schema import PV_POWER_KW, GRID_POWER_KW, BATTERY_POWER_KW, BATTERY_SOC_PCT, LOAD_POWER_KW
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT timestamp, pv_power, grid_power, batt_power, soc, load_power
            FROM fronius ORDER BY timestamp DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row:
            self._update_last_ingest_locked(row[0])
            return {
                'timestamp': row[0],
                PV_POWER_KW: row[1] or 0.0,
                GRID_POWER_KW: row[2] or 0.0,
                BATTERY_POWER_KW: row[3] or 0.0,
                BATTERY_SOC_PCT: row[4] or 0.0,
                LOAD_POWER_KW: row[5],
            }
        return None
    
    def get_hourly_averages(self, hours=24):
        """Hole stündliche Durchschnitte der letzten N Stunden."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT 
                datetime(timestamp, 'start of hour') as hour,
                AVG(pv_power) as avg_pv,
                AVG(grid_power) as avg_grid,
                AVG(batt_power) as avg_batt,
                AVG(soc) as avg_soc
            FROM fronius
            WHERE timestamp > datetime('now', '-' || ? || ' hours')
            GROUP BY hour
            ORDER BY hour DESC
        """, (hours,))
        
        return [
            {
                'hour': row[0],
                'pv': row[1],
                'grid': row[2],
                'batt': row[3],
                'soc': row[4]
            }
            for row in cursor.fetchall()
        ]
    
    def get_daily_totals(self, days: Optional[int] = 30) -> List[dict]:
        """Integriere PV-Leistung zu täglichen kWh-Werten."""
        cursor = self.conn.cursor()
        params: list = []
        where = ""
        if days is not None:
            where = "WHERE timestamp >= datetime('now', '-' || ? || ' days')"
            params.append(days)
        rows = cursor.execute(
            f"""
            SELECT timestamp, pv_power
            FROM fronius
            {where}
            ORDER BY timestamp ASC
            """,
            params,
        )
        return _integrate_daily_energy(rows)

    def get_monthly_totals(self, months: int = 12) -> List[dict]:
        """Aggregiere tägliche PV-Werte zu MonatskWh."""
        if months <= 0:
            return []
        # Holen wir etwas Puffer (31 Tage pro Monat)
        daily = self.get_daily_totals(days=months * 31)
        monthly: dict[str, dict[str, float | int]] = {}
        for item in daily:
            try:
                day_obj = ensure_utc(datetime.fromisoformat(item['day']))
            except ValueError:
                continue
            month_key = day_obj.replace(day=1).date().isoformat()
            bucket = monthly.setdefault(month_key, {'pv_kwh': 0.0, 'days': 0})
            bucket['pv_kwh'] += float(item.get('pv_kwh') or 0.0)
            bucket['days'] += 1
        out = [
            {
                'month': month,
                'pv_kwh': data['pv_kwh'],
                'days': data['days'],
            }
            for month, data in monthly.items()
        ]
        out.sort(key=lambda entry: entry['month'])
        # Nur die letzten N Monate zurückgeben
        return out[-months:]
    
    def close(self):
        """Schließe Datenbank."""
        if self.conn:
            self.conn.close()

    # --- Neue Schreib-/Lese-APIs ---

    def insert_fronius_record(self, record: dict) -> None:
        """Persistiere einen Fronius-Datensatz."""
        if not record:
            return
        ts = record.get('Zeitstempel') or record.get('timestamp')
        if not ts:
            return
        pv = _safe_float(record.get('PV-Leistung (kW)') or record.get('pv'))
        grid = _safe_float(record.get('Netz-Leistung (kW)') or record.get('grid'))
        batt = _safe_float(record.get('Batterie-Leistung (kW)') or record.get('batt'))
        load_power = _safe_float(record.get('Hausverbrauch (kW)') or record.get('load') or record.get('P_Load'))
        soc = _safe_float(record.get('Batterieladestand (%)') or record.get('soc'))

        pv = _normalize_power_kw(pv)
        grid = _normalize_power_kw(grid)
        batt = _normalize_power_kw(batt)
        load_power = _normalize_power_kw(load_power)
        with self._lock:
            self._execute_with_retry(
                """
                INSERT OR REPLACE INTO fronius (timestamp, pv_power, grid_power, batt_power, soc, load_power)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (ts, pv, grid, batt, soc, load_power),
            )
            self._commit_with_retry()
            self._update_last_ingest_locked(ts)

    def insert_heating_record(self, record: dict) -> None:
        """Persistiere Heizungs-/Pufferdaten."""
        if not record:
            return
        ts = record.get('Zeitstempel') or record.get('timestamp')
        if not ts:
            return
        kessel = _safe_float(record.get('Kesseltemperatur') or record.get('kesseltemp'))
        outdoor = _safe_float(record.get('Außentemperatur') or record.get('Aussentemperatur') or record.get('außentemp'))
        top = _safe_float(record.get('Pufferspeicher Oben') or record.get('Puffer_Oben') or record.get('puffer_top'))
        mid = _safe_float(record.get('Pufferspeicher Mitte') or record.get('Puffer_Mitte') or record.get('puffer_mid'))
        bot = _safe_float(record.get('Pufferspeicher Unten') or record.get('Puffer_Unten') or record.get('puffer_bot'))
        warm = _safe_float(record.get('Warmwasser') or record.get('Warmwassertemperatur'))
        with self._lock:
            self._execute_with_retry(
                """
                INSERT OR REPLACE INTO heating (timestamp, kesseltemp, außentemp, puffer_top, puffer_mid, puffer_bot, warmwasser)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, kessel, outdoor, top, mid, bot, warm),
            )
            self._commit_with_retry()
            self._update_last_ingest_locked(ts)

    def get_recent_fronius(self, hours: int = 24, limit: Optional[int] = None) -> List[dict]:
        cutoff = _hours_ago_iso(hours)
        cursor = self.conn.cursor()
        params: list = [cutoff, cutoff]
        if limit:
            # If a LIMIT is requested we want the newest records, not the oldest.
            # Fetch descending and reverse to keep chronological order for charts.
            sql = (
                "SELECT timestamp, pv_power, grid_power, batt_power, soc, load_power "
                "FROM fronius WHERE (? IS NULL OR datetime(timestamp) >= datetime(?)) "
                "ORDER BY datetime(timestamp) DESC LIMIT ?"
            )
            params.append(limit)
            rows = cursor.execute(sql, params).fetchall()
            rows.reverse()
        else:
            sql = (
                "SELECT timestamp, pv_power, grid_power, batt_power, soc, load_power "
                "FROM fronius WHERE (? IS NULL OR datetime(timestamp) >= datetime(?)) "
                "ORDER BY datetime(timestamp) ASC"
            )
            rows = cursor.execute(sql, params).fetchall()
        return [
            {
                'timestamp': row[0],
                'pv': row[1],
                'grid': row[2],
                'batt': row[3],
                'soc': row[4],
                'load': row[5],
            }
            for row in rows
        ]

    def get_recent_heating(self, hours: int = 24, limit: Optional[int] = None) -> List[dict]:
        cutoff = _hours_ago_iso(hours)
        cursor = self.conn.cursor()
        params: list = [cutoff, cutoff]
        if limit:
            # If a LIMIT is requested we want the newest records, not the oldest.
            # Fetch descending and reverse to keep chronological order for charts.
            sql = (
                "SELECT timestamp, kesseltemp, außentemp, puffer_top, puffer_mid, puffer_bot, warmwasser "
                "FROM heating WHERE (? IS NULL OR datetime(timestamp) >= datetime(?)) "
                "ORDER BY datetime(timestamp) DESC LIMIT ?"
            )
            params.append(limit)
            rows = cursor.execute(sql, params).fetchall()
            rows.reverse()
        else:
            sql = (
                "SELECT timestamp, kesseltemp, außentemp, puffer_top, puffer_mid, puffer_bot, warmwasser "
                "FROM heating WHERE (? IS NULL OR datetime(timestamp) >= datetime(?)) "
                "ORDER BY datetime(timestamp) ASC"
            )
            rows = cursor.execute(sql, params).fetchall()
        return [
            {
                'timestamp': row[0],
                'kessel': row[1],
                'outdoor': row[2],
                'top': row[3],
                'mid': row[4],
                'bot': row[5],
                'warm': row[6],
            }
            for row in rows
        ]

    def get_last_heating_record(self) -> Optional[dict]:
        """
        Hole letzten Heizungs-Record als dict mit final keys (schema.py).
        Zeitstrategie: UTC everywhere. Mapping: Kessel = BMK_KESSEL_C, Warmwasser = BMK_WARMWASSER_C.
        Keine Fallbacks, keine Verschachtelung.
        """
        from .schema import BMK_KESSEL_C, BMK_WARMWASSER_C, BUF_TOP_C, BUF_MID_C, BUF_BOTTOM_C
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT timestamp, kesseltemp, warmwasser, außentemp, puffer_top, puffer_mid, puffer_bot
            FROM heating ORDER BY timestamp DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row:
            self._update_last_ingest_locked(row[0])
            return {
                'timestamp': row[0],
                BMK_KESSEL_C: _safe_float(row[1]),
                BMK_WARMWASSER_C: _safe_float(row[2]),
                'outdoor': _safe_float(row[3]),
                BUF_TOP_C: _safe_float(row[4]),
                BUF_MID_C: _safe_float(row[5]),
                BUF_BOTTOM_C: _safe_float(row[6]),
            }
        return None

    def get_latest_timestamp(self) -> Optional[str]:
        with self._lock:
            ts = self._get_latest_timestamp_unlocked()
            if ts:
                self._update_last_ingest_locked(ts)
            return ts

    def get_last_ingest_datetime(self) -> Optional[datetime]:
        with self._lock:
            return ensure_utc(self._last_ingest_dt) if self._last_ingest_dt else None

    def get_last_ingest_timestamp(self) -> Optional[str]:
        dt = self.get_last_ingest_datetime()
        if not dt:
            return None
        return dt.strftime("%Y-%m-%d %H:%M:%S")

    def _execute_with_retry(self, query: str, params: tuple | list = (), retries: int = 5, base_delay: float = 0.05) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                self.conn.execute(query, params)
                return
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(base_delay * (attempt + 1))
        if last_exc:
            raise last_exc

    def _commit_with_retry(self, retries: int = 5, base_delay: float = 0.05) -> None:
        last_exc: Optional[Exception] = None
        for attempt in range(retries):
            try:
                self.conn.commit()
                return
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if "locked" not in str(exc).lower():
                    raise
                time.sleep(base_delay * (attempt + 1))
        if last_exc:
            raise last_exc

    def _get_latest_timestamp_unlocked(self) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT MAX(timestamp) FROM fronius")
        fr = cursor.fetchone()[0]
        cursor.execute("SELECT MAX(timestamp) FROM heating")
        ht = cursor.fetchone()[0]
        values = [ts for ts in (fr, ht) if ts]
        if not values:
            return None
        return max(values)

    def _update_last_ingest_locked(self, ts: str) -> None:
        dt = _parse_iso_timestamp(ts)
        if not dt:
            return
        dt = ensure_utc(dt)
        current = ensure_utc(self._last_ingest_dt) if self._last_ingest_dt else None
        if current is None or dt > current:
            self._last_ingest_dt = dt

    def seed_from_csv(self, data_dir: Optional[Path] = None) -> None:
        base = Path(data_dir) if data_dir else DATA_DIR
        fr_csv = base / "FroniusDaten.csv"
        heat_csv = base / "Heizungstemperaturen.csv"
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM fronius")
        fr_count = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM heating")
        heat_count = cursor.fetchone()[0]
        fr_needs_signal = fr_csv.exists() and not self._table_has_signal("fronius", "pv_power")
        if (fr_count == 0 or fr_needs_signal) and fr_csv.exists():
            if fr_count > 0:
                self.conn.execute("DELETE FROM fronius")
                self.conn.commit()
            self.import_fronius_csv(fr_csv)
        if heat_count == 0 and heat_csv.exists():
            self.import_heating_csv(heat_csv)

    def import_heating_csv(self, csv_path: str | os.PathLike[str]) -> bool:
        if not os.path.exists(csv_path):
            return False
        cursor = self.conn.cursor()
        count = 0
        try:
            with open(csv_path, 'r', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = row.get('Zeitstempel')
                    if not ts:
                        continue
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO heating
                        (timestamp, kesseltemp, außentemp, puffer_top, puffer_mid, puffer_bot, warmwasser)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            ts,
                            _safe_float(row.get('Kesseltemperatur')),
                            _safe_float(row.get('Außentemperatur') or row.get('Aussentemperatur')),
                            _safe_float(row.get('Pufferspeicher Oben') or row.get('Puffer_Oben')),
                            _safe_float(row.get('Pufferspeicher Mitte') or row.get('Puffer_Mitte')),
                            _safe_float(row.get('Pufferspeicher Unten') or row.get('Puffer_Unten')),
                            _safe_float(row.get('Warmwasser') or row.get('Warmwassertemperatur')),
                        ),
                    )
                    count += 1
                    if count % 1000 == 0:
                        self.conn.commit()
            self.conn.commit()
            return True
        except Exception:
            return False

    def _table_has_signal(self, table: str, column: str, threshold: float = 1e-6) -> bool:
        cursor = self.conn.cursor()
        query = (
            f"SELECT COUNT(*) FROM {table} "
            f"WHERE {column} IS NOT NULL AND ABS({column}) > ?"
        )
        cursor.execute(query, (threshold,))
        return cursor.fetchone()[0] > 0


# Quick Startup Helper
def quick_import_if_needed():
    """Importiere CSVs wenn Datenbank leer ist."""
    store = DataStore()
    store.seed_from_csv()
    store.close()


def _integrate_daily_energy(rows: Iterable[tuple[str, Optional[float]]]) -> List[dict]:
    """Trapez-Integration zur Energie pro Tag (streaming, speichersparend)."""
    buckets: dict[str, dict[str, float | int]] = defaultdict(lambda: {'pv_kwh': 0.0, 'samples': 0})
    prev_ts: Optional[datetime] = None
    prev_power: Optional[float] = None

    for ts, pv in rows:
        if ts is None or pv is None:
            continue
        try:
            cur_ts = ensure_utc(datetime.fromisoformat(ts))
            cur_power = float(pv)
        except ValueError:
            continue

        if prev_ts is not None and prev_power is not None:
            delta_h = (cur_ts - prev_ts).total_seconds() / 3600
            # Filter offensichtliche Ausreißer (z.B. wenn Logger aus war)
            if 0 < delta_h <= 6:
                _distribute_segment_energy(buckets, prev_ts, prev_power, cur_ts, cur_power)

        prev_ts, prev_power = cur_ts, cur_power

    if not buckets:
        return []

    out = [
        {
            'day': day,
            'pv_kwh': data['pv_kwh'],
            'samples': data['samples'],
        }
        for day, data in buckets.items()
    ]
    out.sort(key=lambda entry: entry['day'])
    return out


def _distribute_segment_energy(
    buckets: dict[str, dict[str, float | int]],
    start_ts: datetime,
    start_power: float,
    end_ts: datetime,
    end_power: float,
) -> None:
    """Verteile eine Messspanne auf die jeweils betroffenen Kalendertage."""
    current_ts = start_ts
    current_power = start_power
    final_ts = end_ts
    final_power = end_power

    def _add(day_ts: datetime, p_start: float, p_end: float, hours: float) -> None:
        if hours <= 0:
            return
        energy = (p_start + p_end) / 2.0 * hours
        day_key = ensure_utc(day_ts).date().isoformat()
        bucket = buckets[day_key]
        bucket['pv_kwh'] += energy
        bucket['samples'] += 1

    while current_ts.date() != final_ts.date():
        boundary = ensure_utc(datetime.combine(current_ts.date() + timedelta(days=1), datetime.min.time()))
        total_hours = (final_ts - current_ts).total_seconds() / 3600
        if total_hours <= 0:
            return
        span_hours = (boundary - current_ts).total_seconds() / 3600
        if span_hours <= 0:
            break
        ratio = span_hours / total_hours
        boundary_power = current_power + (final_power - current_power) * ratio
        _add(current_ts, current_power, boundary_power, span_hours)
        current_ts = boundary
        current_power = boundary_power

    remaining_hours = (final_ts - current_ts).total_seconds() / 3600
    if remaining_hours > 0:
        _add(current_ts, current_power, final_power, remaining_hours)


def _hours_ago_iso(hours: int | None) -> Optional[str]:
    if hours is None:
        return None
    # UTC everywhere: Cutoff immer UTC
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


def _parse_iso_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        raw = str(value).strip()
        # Accept common ISO UTC suffix.
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        # Optional correction: if source stores local timestamps without tzinfo.
        # Default remains "treat naive as UTC" (existing behavior).
        if dt.tzinfo is None and os.environ.get("DASHBOARD_TS_ASSUME_LOCAL", "").strip().lower() in ("1", "true", "yes", "on"):
            try:
                local_tz = datetime.now().astimezone().tzinfo
                dt = dt.replace(tzinfo=local_tz).astimezone(timezone.utc)
            except Exception:
                pass
        return dt
    except Exception:
        return None


def _first_value(row: dict, *keys: str):
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return None


def _safe_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _normalize_power_kw(value: Optional[float]) -> Optional[float]:
    """Normalize power values to kW.

    Some sources provide power in W (e.g. P_PV from Fronius API/old logs). The DB/UI
    expects kW. Heuristic: absolute values above ~200 kW are treated as W.
    """
    if value is None:
        return None
    try:
        v = float(value)
    except (ValueError, TypeError):
        return None
    if abs(v) > 200.0:
        return v / 1000.0
    return v


if __name__ == "__main__":
    quick_import_if_needed()
