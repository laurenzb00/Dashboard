"""Tests fuer core.heating_stats (Einheizen am Kessel, Waermeeintrag Holz/Solar)."""

import sys
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.heating_stats import Bucket, StorageConfig, analyze, detect_events, kessel_active, sun_elevation_deg  # noqa: E402
from core import heating_stats as hs  # noqa: E402

CFG = StorageConfig(puffer_liter=4000, boiler_liter=500, storage_factor=1.0)


def _b(ts, kessel, puffer, warm=55.0, outdoor=5.0, **_ignored):
    return Bucket(ts=ts, kessel=kessel, top=puffer, mid=puffer, bot=puffer, warm=warm, outdoor=outdoor)


class TestKessel(unittest.TestCase):
    def test_kessel_must_be_hot_and_above_puffer(self):
        t = datetime(2026, 2, 9, 12)
        self.assertTrue(kessel_active(_b(t, 70, 50)))
        self.assertFalse(kessel_active(_b(t, 61, 63)))   # nur passiv mit Puffer temperiert
        self.assertFalse(kessel_active(_b(t, 45, 30)))   # zu kalt

    def test_episode_sun_vs_fire(self):
        from datetime import timedelta, timezone
        from core.heating_stats import classify_episodes

        def episode(start, gain_kwh):
            out, p = [], 45.0
            per_bucket = gain_kwh / 12 / (CFG.puffer_kwh_per_k)
            for i in range(16):
                ts = start + timedelta(minutes=15 * i)
                hot = 1 <= i <= 8
                if 1 <= i <= 12:
                    p += per_bucket
                out.append(Bucket(ts=ts, kessel=70.0 if hot else 40.0, top=p, mid=p, bot=p, warm=55, outdoor=20))
            return out

        def pv(start, kw):
            return {(start + timedelta(hours=h)).astimezone(timezone.utc).strftime("%Y-%m-%d %H"): kw
                    for h in range(-1, 6)}

        sunny = episode(datetime(2026, 8, 20, 9), 45.0)
        classify_episodes(sunny, pv(datetime(2026, 8, 20, 9), 6.0), CFG)       # 6 kW PV: Sonne erklaert 45 kWh
        self.assertFalse(any(kessel_active(b) for b in sunny))
        night = episode(datetime(2026, 8, 7, 21), 50.0)
        classify_episodes(night, pv(datetime(2026, 8, 7, 21), 0.0), CFG)       # abends ohne Sonne: Feuer
        self.assertTrue(any(kessel_active(b) for b in night))
        small = episode(datetime(2026, 8, 7, 21), 5.0)
        classify_episodes(small, {}, CFG)                                       # kaum Zuwachs: kein Feuer
        self.assertFalse(any(kessel_active(b) for b in small))

    def test_betriebsmodus_decides(self):
        from core.heating_stats import mode_is_firing
        t = datetime(2026, 10, 6, 19)
        self.assertFalse(mode_is_firing("STANDBY"))
        self.assertFalse(mode_is_firing("Störung 12"))
        self.assertTrue(mode_is_firing("VOLLLAST"))
        self.assertTrue(mode_is_firing("Teillast"))
        hot_solar = Bucket(ts=t, kessel=72, top=55, mid=50, bot=45, warm=55, outdoor=25, modus="STANDBY")
        self.assertFalse(kessel_active(hot_solar))
        fire = Bucket(ts=t, kessel=45, top=55, mid=50, bot=45, warm=55, outdoor=5, modus="ANHEIZEN")
        self.assertTrue(kessel_active(fire))

    def test_load_buckets_with_text_mode(self):
        import sqlite3
        from core.heating_stats import load_buckets

        class Store:
            conn = sqlite3.connect(":memory:")
        Store.conn.execute("CREATE TABLE heating (timestamp TEXT, kesseltemp REAL, puffer_top REAL, puffer_mid REAL, "
                           "puffer_bot REAL, warmwasser REAL, aussentemp REAL, rauchgastemp REAL, betriebsmodus REAL)")
        Store.conn.executemany("INSERT INTO heating VALUES (?,?,?,?,?,?,?,?,?)", [
            ("2026-10-06 10:00:00", 70, 50, 48, 45, 55, 10, None, "STANDBY"),
            ("2026-10-06 10:05:00", 72, 50, 48, 45, 55, 10, None, "STANDBY"),
            ("2026-10-06 10:20:00", 75, 50, 48, 45, 55, 10, None, "VOLLLAST")])
        b = load_buckets(Store, datetime(2026, 10, 6, 0), datetime(2026, 10, 7, 0))
        self.assertEqual(len(b), 2)
        self.assertFalse(kessel_active(b[0]))      # heiss, aber STANDBY -> kein Feuer
        self.assertTrue(kessel_active(b[1]))

    def test_rauchgas_decides_when_recorded(self):
        t = datetime(2026, 8, 5, 11)
        solar = Bucket(ts=t, kessel=72, top=55, mid=50, bot=45, warm=55, outdoor=25, rauchgas=40.0)
        feuer = Bucket(ts=t, kessel=58, top=55, mid=50, bot=45, warm=55, outdoor=5, rauchgas=160.0)
        self.assertFalse(kessel_active(solar))    # Solar heizt den Kesselfuehler, kein Feuer
        self.assertTrue(kessel_active(feuer))     # Feuer, auch wenn Kessel noch kalt


class TestAnalyze(unittest.TestCase):
    def _day(self):
        start = datetime(2026, 2, 9, 0, 0)
        buckets = []
        puffer = 40.0
        for i in range(96):
            ts = start + timedelta(minutes=15 * i)
            h = ts.hour + ts.minute / 60
            kessel = 45.0
            pv = 0.0
            if 8 <= h < 12:               # Einheizen am Vormittag: +20 K im Puffer
                kessel = 78.0
                puffer += 20.0 / 16
            elif 13 <= h < 15:            # Sonne, Kessel kalt: Solarthermie +2 K
                pv = 3.0
                puffer += 2.0 / 8
            elif h >= 18:                 # Abend: Verbrauch
                puffer -= 0.25
            buckets.append(_b(ts, kessel, puffer, pv=pv))
        return buckets

    def test_split_wood_and_solar(self):
        stats = analyze(self._day(), CFG, first_day=date(2026, 2, 9), last_day=date(2026, 2, 9))
        kwh_per_k = 4000 * 1.163 / 1000
        # Holz = Anstieg + Verbrauch des Hauses waehrend des Abbrands (Rate der ruhigen Stunden)
        self.assertGreater(stats.wood_kwh, 20 * kwh_per_k)
        self.assertLess(stats.wood_kwh, 20 * kwh_per_k + 15)
        self.assertAlmostEqual(stats.solar_kwh, 2 * kwh_per_k, delta=1.0)
        self.assertEqual(len(stats.events), 1)
        self.assertEqual(stats.days[0].events, 1)
        self.assertGreater(stats.days[0].used_kwh, 5)
        self.assertAlmostEqual(stats.solar_share_pct, 100 * 2 / 22, delta=2)

    def test_wood_includes_consumption_during_fire(self):
        """Haus verbraucht konstant 1 K/h im Puffer; Feuer 4 h hebt netto um 4 K -> Holz = 8 K."""
        start = datetime(2026, 2, 9, 0, 0)
        buckets, puffer = [], 50.0
        for i in range(48):                                  # 12 h, 15-min-Raster
            ts = start + timedelta(minutes=15 * i)
            firing = 16 <= i < 32                            # 04:00-08:00
            puffer += (2.0 if firing else 0.0) / 4 - 1.0 / 4
            buckets.append(_b(ts, 80.0 if firing else 40.0, puffer))
        stats = analyze(buckets, CFG)
        kwh_per_k = 4000 * 1.163 / 1000
        # Nachlauf 30 min zaehlt mit -> 4,5 h Verbrauch, netto +4 K, minus 0,5 K Abkuehlung im Nachlauf
        self.assertAlmostEqual(stats.wood_kwh, 8.0 * kwh_per_k, delta=0.6 * kwh_per_k)

    def test_storage_factor_scales_heat(self):
        a = StorageConfig(storage_factor=1.0)
        b = StorageConfig(storage_factor=1.6)
        self.assertAlmostEqual(b.puffer_kwh_per_k / a.puffer_kwh_per_k, 1.6)
        self.assertAlmostEqual(hs.usable_kwh(45.0, b) / hs.usable_kwh(45.0, a), 1.6)

    def test_event_bounds(self):
        self.assertEqual(len(detect_events(self._day())), 1)
        ev = analyze(self._day(), CFG).events[0]
        self.assertEqual(ev.start, datetime(2026, 2, 9, 8, 0))
        self.assertEqual(ev.end, datetime(2026, 2, 9, 12, 0))
        self.assertGreater(ev.wood_kwh, 80)

    def test_noise_is_ignored(self):
        start = datetime(2026, 2, 9, 12)
        buckets = [_b(start + timedelta(minutes=15 * i), 45, 50 + (0.05 if i % 2 else 0), pv=2.0) for i in range(40)]
        stats = analyze(buckets, CFG)
        self.assertAlmostEqual(stats.solar_kwh, 0.0)

    def test_rise_without_kessel_at_night_is_not_solar(self):
        start = datetime(2026, 1, 15, 0, 0)
        buckets = [_b(start + timedelta(minutes=15 * i), 40, 50 + 0.2 * i) for i in range(16)]  # 00-04 Uhr
        stats = analyze(buckets, CFG)
        self.assertAlmostEqual(stats.solar_kwh, 0.0)
        self.assertAlmostEqual(stats.wood_kwh, 0.0)

    def test_boiler_rise_during_day_is_solar(self):
        start = datetime(2026, 7, 1, 11, 0)
        buckets = [_b(start + timedelta(minutes=15 * i), 40, 50, warm=50 + 1.0 * i) for i in range(9)]
        stats = analyze(buckets, CFG)
        self.assertAlmostEqual(stats.solar_kwh, 8 * 500 * 1.163 / 1000, delta=0.05)


class TestSun(unittest.TestCase):
    def test_elevation(self):
        noon_summer = sun_elevation_deg(datetime(2026, 6, 21, 13, 10), 48.26, 13.04)
        self.assertAlmostEqual(noon_summer, 65.2, delta=1.0)
        self.assertLess(sun_elevation_deg(datetime(2026, 6, 21, 1, 0), 48.26, 13.04), -10)
        self.assertLess(sun_elevation_deg(datetime(2026, 12, 21, 7, 30), 48.26, 13.04), 0)   # vor Sonnenaufgang
        self.assertGreater(sun_elevation_deg(datetime(2026, 12, 21, 12, 0), 48.26, 13.04), 15)


class TestEmpty(unittest.TestCase):
    def test_empty(self):
        stats = analyze([], CFG, first_day=date(2026, 2, 1), last_day=date(2026, 2, 7))
        self.assertEqual(len(stats.days), 7)
        self.assertEqual(stats.wood_kwh, 0)
        self.assertIsNone(stats.solar_share_pct)



class TestWaermeHelpers(unittest.TestCase):
    def test_usable_and_charge(self):
        from core.heating_stats import charge_pct, usable_kwh, wood_rm
        cfg = StorageConfig(storage_factor=1.0)
        self.assertAlmostEqual(usable_kwh(45.0, cfg), 10 * 4.652, places=2)
        self.assertEqual(usable_kwh(30.0, cfg), 0.0)
        self.assertAlmostEqual(charge_pct(57.5, cfg), 50.0)
        self.assertAlmostEqual(wood_rm(960.0, cfg), 1.0)    # 960 / 0.60 / 1600

    def test_season_start(self):
        from core.heating_stats import season_start
        self.assertEqual(season_start(date(2026, 10, 4)), date(2026, 9, 1))
        self.assertEqual(season_start(date(2027, 2, 1)), date(2026, 9, 1))

    def test_season_stats_cache(self):
        import sqlite3, tempfile, os
        import core.heating_stats as hs
        from core.time_utils import to_db_ts
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE heating (timestamp TEXT, kesseltemp REAL, puffer_top REAL, puffer_mid REAL, "
                     "puffer_bot REAL, warmwasser REAL, aussentemp REAL)")
        t, p = datetime(2026, 9, 10, 8, 0), 40.0
        for i in range(16):            # 4 h Einheizen: +20 K
            p += 20 / 16
            conn.execute("INSERT INTO heating VALUES (?,?,?,?,?,?,?)",
                         (to_db_ts(t + timedelta(minutes=15 * i), naive_is_local=True), 78, p, p, p, 55, 10))

        class Store:
            pass
        store = Store()
        store.conn = conn
        old = hs._DAY_CACHE_PATH
        hs._DAY_CACHE_PATH = os.path.join(tempfile.mkdtemp(), "cache.json")
        try:
            a = hs.season_stats(store, StorageConfig(storage_factor=1.0), today=date(2026, 9, 20))
            conn.execute("DELETE FROM heating")   # abgeschlossene Tage kommen jetzt aus dem Cache
            b = hs.season_stats(store, StorageConfig(storage_factor=1.0), today=date(2026, 9, 20))
        finally:
            hs._DAY_CACHE_PATH = old
        self.assertEqual(len(a.days), 20)
        self.assertAlmostEqual(a.wood_kwh, 20 * 4.652 * 15 / 16, delta=0.5)  # erster Messwert hat keinen Vorgaenger
        self.assertAlmostEqual(b.wood_kwh, a.wood_kwh)
        self.assertEqual(len(b.events), 1)

if __name__ == "__main__":
    unittest.main()
