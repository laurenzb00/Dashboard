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


if __name__ == "__main__":
    unittest.main()
