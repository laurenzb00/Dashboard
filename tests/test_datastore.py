"""Unit tests for core.datastore – DataStore CRUD and retention cleanup."""

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.datastore import DataStore


class TestDataStoreBasic(unittest.TestCase):
    """Basic insert / read / cleanup operations on an in-memory-like temp DB."""

    def setUp(self):
        self._tmpfile = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmpfile.close()
        self.store = DataStore(db_path=self._tmpfile.name)

    def tearDown(self):
        try:
            self.store.close()
        except Exception:
            pass
        try:
            os.unlink(self._tmpfile.name)
        except Exception:
            pass

    # --- Fronius ---

    def test_insert_and_read_fronius(self):
        ts = "2025-06-15 12:00:00"
        self.store.insert_fronius_record({
            "Zeitstempel": ts,
            "PV-Leistung (kW)": 3.5,
            "Netz-Leistung (kW)": -1.2,
            "Batterie-Leistung (kW)": 0.8,
            "Batterieladestand (%)": 45.0,
            "Hausverbrauch (kW)": 2.1,
        })
        rec = self.store.get_last_fronius_record()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["timestamp"], ts)
        self.assertAlmostEqual(rec["pv_power_kw"], 3.5, places=1)

    def test_fronius_cache_invalidation(self):
        self.store.insert_fronius_record({
            "Zeitstempel": "2025-06-15 12:00:00",
            "PV-Leistung (kW)": 1.0,
        })
        rec1 = self.store.get_last_fronius_record()
        self.store.insert_fronius_record({
            "Zeitstempel": "2025-06-15 12:01:00",
            "PV-Leistung (kW)": 9.9,
        })
        rec2 = self.store.get_last_fronius_record()
        self.assertEqual(rec2["timestamp"], "2025-06-15 12:01:00")

    # --- Heating ---

    def test_insert_and_read_heating(self):
        ts = "2025-06-15 12:00:00"
        self.store.insert_heating_record({
            "Zeitstempel": ts,
            "Kesseltemperatur": 75.0,
            "Außentemperatur": 15.0,
            "Pufferspeicher Oben": 65.0,
            "Pufferspeicher Mitte": 55.0,
            "Pufferspeicher Unten": 45.0,
            "Warmwasser": 50.0,
        })
        rec = self.store.get_last_heating_record()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["timestamp"], ts)
        self.assertAlmostEqual(rec["bmk_kessel_c"], 75.0, places=1)

    def test_heating_kessel_state_columns(self):
        ts = "2025-06-15 12:00:00"
        self.store.insert_heating_record({"Zeitstempel": ts, "Kesseltemperatur": 75.0, "Pufferspeicher Oben": 65.0,
                                          "Pufferspeicher Mitte": 55.0, "Pufferspeicher Unten": 45.0,
                                          "Rauchgastemperatur": 180.0, "Kesselrücklauf": 62.0, "Betriebsmodus": "VOLLLAST"})
        row = self.store.conn.execute("SELECT rauchgastemp, kessel_ruecklauf, betriebsmodus FROM heating "
                                      "WHERE timestamp = ?", (ts,)).fetchone()
        self.assertEqual(tuple(row), (180.0, 62.0, "VOLLLAST"))

    # --- Recent queries ---

    def test_get_recent_fronius(self):
        base = datetime.now(timezone.utc)
        for i in range(5):
            ts = (base - timedelta(minutes=i * 10)).strftime("%Y-%m-%d %H:%M:%S")
            self.store.insert_fronius_record({
                "Zeitstempel": ts,
                "PV-Leistung (kW)": float(i),
            })
        recent = self.store.get_recent_fronius(hours=1)
        self.assertGreaterEqual(len(recent), 5)

    def test_get_recent_heating(self):
        base = datetime.now(timezone.utc)
        for i in range(5):
            ts = (base - timedelta(minutes=i * 10)).strftime("%Y-%m-%d %H:%M:%S")
            self.store.insert_heating_record({
                "Zeitstempel": ts,
                "Kesseltemperatur": 60.0 + i,
            })
        recent = self.store.get_recent_heating(hours=1)
        self.assertGreaterEqual(len(recent), 5)

    # --- Cleanup / Retention ---

    def test_cleanup_old_records(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=400)).strftime("%Y-%m-%d %H:%M:%S")
        new_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

        self.store.insert_fronius_record({"Zeitstempel": old_ts, "PV-Leistung (kW)": 1.0})
        self.store.insert_fronius_record({"Zeitstempel": new_ts, "PV-Leistung (kW)": 2.0})
        self.store.insert_heating_record({"Zeitstempel": old_ts, "Kesseltemperatur": 50.0})
        self.store.insert_heating_record({"Zeitstempel": new_ts, "Kesseltemperatur": 60.0})

        result = self.store.cleanup_old_records(retention_days=365)
        self.assertGreaterEqual(result["fronius"], 1)
        self.assertGreaterEqual(result["heating"], 1)

        # Recent records should remain
        rec = self.store.get_last_fronius_record()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["timestamp"], new_ts)

    def test_cleanup_no_old_records(self):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        self.store.insert_fronius_record({"Zeitstempel": ts, "PV-Leistung (kW)": 1.0})
        result = self.store.cleanup_old_records(retention_days=365)
        self.assertEqual(result["fronius"], 0)
        self.assertEqual(result["heating"], 0)

    # --- Daily / Monthly totals ---

    def test_daily_totals(self):
        base = datetime.now(timezone.utc)
        for i in range(24):
            ts = (base - timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S")
            self.store.insert_fronius_record({
                "Zeitstempel": ts,
                "PV-Leistung (kW)": 3.0,
            })
        daily = self.store.get_daily_totals(days=2)
        self.assertGreaterEqual(len(daily), 1)
        self.assertIn("pv_kwh", daily[0])

    def test_monthly_totals(self):
        base = datetime.now(timezone.utc)
        # Insert records every 6 hours for 60 days so trapezoid integration works
        for d in range(60):
            for h in (0, 6, 12, 18):
                ts = (base - timedelta(days=d, hours=h)).strftime("%Y-%m-%d %H:%M:%S")
                self.store.insert_fronius_record({
                    "Zeitstempel": ts,
                    "PV-Leistung (kW)": 5.0,
                })
        monthly = self.store.get_monthly_totals(months=3)
        self.assertGreaterEqual(len(monthly), 1)
        self.assertIn("pv_kwh", monthly[0])

    # --- Edge cases ---

    def test_insert_empty_record(self):
        self.store.insert_fronius_record({})
        self.store.insert_fronius_record(None)
        self.store.insert_heating_record({})
        self.store.insert_heating_record(None)
        # Should not raise

    def test_get_latest_timestamp_empty(self):
        ts = self.store.get_latest_timestamp()
        self.assertIsNone(ts)



class TestTimestampMigration(unittest.TestCase):
    """Alte DBs (gemischte Zeitformate) werden einmalig auf UTC migriert."""

    def setUp(self):
        import sqlite3
        self._tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self._tmpdir, "legacy.db")
        conn = sqlite3.connect(self.db_path)
        conn.execute("CREATE TABLE fronius (id INTEGER PRIMARY KEY, timestamp TEXT UNIQUE, pv_power REAL, grid_power REAL, batt_power REAL, soc REAL, load_power REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        conn.execute("CREATE TABLE heating (id INTEGER PRIMARY KEY, timestamp TEXT UNIQUE, kesseltemp REAL, aussentemp REAL, puffer_top REAL, puffer_mid REAL, puffer_bot REAL, warmwasser REAL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        # Fronius: naive Lokalzeit; Heizung: mit Offset, inkl. Duplikat in derselben Sekunde
        self.local_fr = datetime(2026, 7, 1, 14, 0, 4)
        conn.execute("INSERT INTO fronius (timestamp, pv_power) VALUES (?, 1.0)", (self.local_fr.strftime("%Y-%m-%d %H:%M:%S"),))
        conn.execute("INSERT INTO heating (timestamp, kesseltemp) VALUES ('2026-07-01T14:00:08.100000+02:00', 60.0)")
        conn.execute("INSERT INTO heating (timestamp, kesseltemp) VALUES ('2026-07-01T14:00:08.900000+02:00', 60.0)")
        conn.commit()
        conn.close()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_migration_to_utc(self):
        store = DataStore(db_path=self.db_path)
        try:
            expected_fr = self.local_fr.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            self.assertEqual(store.get_last_fronius_record()["timestamp"], expected_fr)
            self.assertEqual(store.get_last_heating_record()["timestamp"], "2026-07-01 12:00:08")
            n = store.conn.execute("SELECT COUNT(*) FROM heating").fetchone()[0]
            self.assertEqual(n, 1)
            self.assertEqual(store.conn.execute("PRAGMA user_version").fetchone()[0], 1)
        finally:
            store.close()
        self.assertTrue(os.path.exists(os.path.join(self._tmpdir, "legacy_pre_utc_migration.db")))

    def test_migration_runs_only_once(self):
        DataStore(db_path=self.db_path).close()
        store = DataStore(db_path=self.db_path)
        try:
            self.assertEqual(store.get_last_heating_record()["timestamp"], "2026-07-01 12:00:08")
        finally:
            store.close()

    def test_insert_aware_timestamp_stored_as_utc(self):
        store = DataStore(db_path=self.db_path)
        try:
            store.insert_fronius_record({"Zeitstempel": "2026-07-02T10:00:00+02:00", "PV-Leistung (kW)": 2.0})
            self.assertEqual(store.get_last_fronius_record()["timestamp"], "2026-07-02 08:00:00")
        finally:
            store.close()

if __name__ == "__main__":
    unittest.main()
