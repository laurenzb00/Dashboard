"""Central SQLite datastore for PV and heating metrics."""

from __future__ import annotations

from collections import defaultdict
import csv
import os
import sqlite3
import threading
from datetime import datetime, timedelta
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
    """SQLite-basierter Datenspeicher für schnelle Zugriffe."""

    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = str(db_path)
        self.conn = None
        self._lock = threading.RLock()
        self._init_db()
    
    def _init_db(self):
        """Initialisiere Datenbank mit Tabellen."""
        # check_same_thread=False erlaubt Nutzung in verschiedenen Threads (safe für read-only)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        # Pi 5 Optimierungen: WAL + moderater Cache
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA cache_size=-32000")  # 32MB Cache (sicherer)
        self.conn.execute("PRAGMA mmap_size=67108864")  # 64MB Memory-Map (reduziert)
        self.conn.execute("PRAGMA temp_store=MEMORY")
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
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
                        pv = _safe_float(_first_value(row,
                            'PV-Leistung (kW)', 'PV', 'PV [kW]', 'P_PV'))
                        grid = _safe_float(_first_value(row,
                            'Netz-Leistung (kW)', 'Netz', 'Netz [kW]', 'P_Grid'))
                        batt = _safe_float(_first_value(row,
                            'Batterie-Leistung (kW)', 'Batterie', 'Batterie [kW]', 'P_Akku'))
                        soc = _safe_float(_first_value(row,
                            'Batterieladestand (%)', 'SOC', 'SoC', 'State of Charge'))

                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO fronius
                            (timestamp, pv_power, grid_power, batt_power, soc)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (ts, pv, grid, batt, soc),
                        )
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
        """Hole letzten PV-Record."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT timestamp, pv_power, grid_power, batt_power, soc
            FROM fronius ORDER BY timestamp DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row:
            return {
                'timestamp': row[0],
                'pv': row[1],
                'grid': row[2],
                'batt': row[3],
                'soc': row[4]
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
        ).fetchall()
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
                day_obj = datetime.fromisoformat(item['day'])
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
        soc = _safe_float(record.get('Batterieladestand (%)') or record.get('soc'))
        with self._lock:
            self.conn.execute(
                """
                INSERT OR REPLACE INTO fronius (timestamp, pv_power, grid_power, batt_power, soc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (ts, pv, grid, batt, soc),
            )
            self.conn.commit()

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
            self.conn.execute(
                """
                INSERT OR REPLACE INTO heating (timestamp, kesseltemp, außentemp, puffer_top, puffer_mid, puffer_bot, warmwasser)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (ts, kessel, outdoor, top, mid, bot, warm),
            )
            self.conn.commit()

    def get_recent_fronius(self, hours: int = 24, limit: Optional[int] = None) -> List[dict]:
        cutoff = _hours_ago_iso(hours)
        cursor = self.conn.cursor()
        sql = (
            "SELECT timestamp, pv_power, grid_power, batt_power, soc "
            "FROM fronius WHERE (? IS NULL OR timestamp >= ?) ORDER BY timestamp ASC"
        )
        params: list = [cutoff, cutoff]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
        rows = cursor.execute(sql, params).fetchall()
        return [
            {
                'timestamp': row[0],
                'pv': row[1],
                'grid': row[2],
                'batt': row[3],
                'soc': row[4],
            }
            for row in rows
        ]

    def get_recent_heating(self, hours: int = 24, limit: Optional[int] = None) -> List[dict]:
        cutoff = _hours_ago_iso(hours)
        cursor = self.conn.cursor()
        sql = (
            "SELECT timestamp, kesseltemp, außentemp, puffer_top, puffer_mid, puffer_bot, warmwasser "
            "FROM heating WHERE (? IS NULL OR timestamp >= ?) ORDER BY timestamp ASC"
        )
        params: list = [cutoff, cutoff]
        if limit:
            sql += " LIMIT ?"
            params.append(limit)
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
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT timestamp, kesseltemp, außentemp, puffer_top, puffer_mid, puffer_bot, warmwasser
            FROM heating ORDER BY timestamp DESC LIMIT 1
            """
        )
        row = cursor.fetchone()
        if row:
            return {
                'timestamp': row[0],
                'kessel': row[1],
                'outdoor': row[2],
                'top': row[3],
                'mid': row[4],
                'bot': row[5],
                'warm': row[6],
            }
        return None

    def get_latest_timestamp(self) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT MAX(timestamp) FROM fronius")
        fr = cursor.fetchone()[0]
        cursor.execute("SELECT MAX(timestamp) FROM heating")
        ht = cursor.fetchone()[0]
        return max(filter(None, [fr, ht]), default=None)

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
    """Trapez-Integration zur Energie pro Tag."""
    parsed: list[tuple[datetime, float]] = []
    for ts, pv in rows:
        if ts is None or pv is None:
            continue
        try:
            parsed.append((datetime.fromisoformat(ts), float(pv)))
        except ValueError:
            continue
    if len(parsed) < 2:
        return []

    buckets: dict[str, dict[str, float | int]] = defaultdict(lambda: {'pv_kwh': 0.0, 'samples': 0})

    for idx in range(len(parsed) - 1):
        t0, p0 = parsed[idx]
        t1, p1 = parsed[idx + 1]
        if p0 is None or p1 is None:
            continue
        delta_h = (t1 - t0).total_seconds() / 3600
        # Filter offensichtliche Ausreißer (z.B. wenn Logger aus war)
        if delta_h <= 0 or delta_h > 6:
            continue
        _distribute_segment_energy(buckets, t0, p0, t1, p1)

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
        day_key = day_ts.date().isoformat()
        bucket = buckets[day_key]
        bucket['pv_kwh'] += energy
        bucket['samples'] += 1

    while current_ts.date() != final_ts.date():
        boundary = datetime.combine(current_ts.date() + timedelta(days=1), datetime.min.time())
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
    return (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


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


if __name__ == "__main__":
    quick_import_if_needed()
