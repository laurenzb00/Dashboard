"""
CSV zu SQLite Konvertierung - Performance Optimization
========================================================
Konvertiert große CSV Dateien zu SQLite für schnelleren Zugriff
"""

import sqlite3
import csv
import os
from pathlib import Path

DB_PATH = os.path.join(os.path.dirname(__file__), "data.db")


class DataStore:
    """SQLite-basierter Datenspeicher für schnelle Zugriffe."""
    
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.conn = None
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
    
    def import_fronius_csv(self, csv_path):
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
                        pv = float(row.get('PV', 0) or 0)
                        grid = float(row.get('Netz', 0) or 0)
                        batt = float(row.get('Batterie', 0) or 0)
                        soc = float(row.get('SOC', 0) or 0)
                        
                        cursor.execute("""
                            INSERT OR REPLACE INTO fronius 
                            (timestamp, pv_power, grid_power, batt_power, soc)
                            VALUES (?, ?, ?, ?, ?)
                        """, (ts, pv, grid, batt, soc))
                        count += 1
                        
                        if count % 1000 == 0:
                            self.conn.commit()
                            print(f"[DB] Imported {count} Fronius records...")
                    except Exception as e:
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
        cursor.execute("""
            SELECT timestamp, pv_power, grid_power, batt_power, soc 
            FROM fronius ORDER BY rowid DESC LIMIT 1
        """)
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
    
    def get_daily_totals(self, days=30):
        """Hole tägliche Totals der letzten N Tage."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT 
                DATE(timestamp) as day,
                SUM(pv_power) * 5 / 3600 as pv_kwh,
                COUNT(*) as samples
            FROM fronius
            WHERE timestamp > datetime('now', '-' || ? || ' days')
            GROUP BY day
            ORDER BY day DESC
        """, (days,))
        
        return [
            {
                'day': row[0],
                'pv_kwh': row[1],
                'samples': row[2]
            }
            for row in cursor.fetchall()
        ]
    
    def close(self):
        """Schließe Datenbank."""
        if self.conn:
            self.conn.close()


# Quick Startup Helper
def quick_import_if_needed():
    """Importiere CSVs wenn Datenbank leer ist."""
    store = DataStore()
    
    # Prüfe ob bereits Daten vorhanden
    cursor = store.conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM fronius")
    count = cursor.fetchone()[0]
    
    if count == 0:
        print("[DB] 📊 Datenbank leer - importiere CSVs...")
        fronius_path = os.path.join(os.path.dirname(__file__), "..", "..", "data", "FroniusDaten.csv")
        
        if os.path.exists(fronius_path):
            print(f"[DB] Importing from {fronius_path}...")
            store.import_fronius_csv(fronius_path)
        else:
            print(f"[DB] ⚠️ {fronius_path} not found")
    else:
        print(f"[DB] ✅ Database ready ({count} Fronius records)")
    
    store.close()


if __name__ == "__main__":
    quick_import_if_needed()
