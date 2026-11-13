import os, sqlite3, sys, threading, time, unittest
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from core.datastore import DataStore  # noqa


class Fake:
    _BINNED_SERIES = DataStore._BINNED_SERIES
    def __init__(self, conn):
        self.conn = conn
        self._lock = threading.RLock()


def mk(rows_f, rows_h):
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE fronius(timestamp TEXT, pv_power REAL)")
    c.execute("CREATE TABLE heating(timestamp TEXT, aussentemp REAL)")
    c.executemany("INSERT INTO fronius VALUES(?,?)", rows_f)
    c.executemany("INSERT INTO heating VALUES(?,?)", rows_h)
    return c


def ts(dt): return dt.strftime("%Y-%m-%d %H:%M:%S")


class T(unittest.TestCase):
    def call(self, c, *a):
        return DataStore.get_binned_series(Fake(c), *a)

    def test_binning_units_filters(self):
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        base = now - timedelta(minutes=now.minute % 15) - timedelta(minutes=30)
        f = [(ts(base + timedelta(minutes=1)), 2000.0),   # W -> 2.0
             (ts(base + timedelta(minutes=2)), 4.0),
             (ts(base + timedelta(minutes=3)), -5.0),     # verworfen
             (ts(base + timedelta(minutes=4)), -0.1)]     # -> 0
        h = [(ts(base + timedelta(minutes=1)), 10.0), (ts(base + timedelta(minutes=2)), 99.0)]
        c = mk(f, h)
        r = self.call(c, "fronius", "pv_power", 24, 15)
        self.assertEqual(len(r), 1)
        self.assertAlmostEqual(r[0][1], (2.0 + 4.0 + 0.0) / 3)
        r = self.call(c, "heating", "aussentemp", 24, 15)
        self.assertEqual(r[0][1], 10.0)

    def test_stale_anchor_and_empty(self):
        old = datetime.now(timezone.utc) - timedelta(days=5)
        c = mk([(ts(old), 3.0), (ts(old - timedelta(hours=30)), 9.0)], [])
        r = self.call(c, "fronius", "pv_power", 24, 15)
        self.assertEqual([round(x[1], 1) for x in r], [3.0])
        self.assertEqual(self.call(c, "heating", "aussentemp", 24, 15), [])

    def test_whitelist(self):
        with self.assertRaises(ValueError):
            self.call(mk([], []), "fronius", "x; DROP", 24, 15)

    def test_speed(self):
        now = datetime.now(timezone.utc)
        f = [(ts(now - timedelta(seconds=10 * i)), 1000.0 + i % 50) for i in range(17000)]
        c = mk(f, [])
        t = time.perf_counter(); r = self.call(c, "fronius", "pv_power", 48, 15)
        dt = time.perf_counter() - t
        print(f"\nbinned: {len(r)} Punkte in {dt*1000:.0f} ms")
        self.assertLess(dt, 0.5)


if __name__ == "__main__":
    unittest.main()
