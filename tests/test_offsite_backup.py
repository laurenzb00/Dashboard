import os
import sqlite3
import stat
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import offsite_backup as ob  # noqa: E402

FAKE_RCLONE = r'''#!/usr/bin/env python3
import os, shutil, sys
root = os.environ["FAKE_REMOTE_ROOT"]
def p(r): return os.path.join(root, r.split(":", 1)[1])
a = sys.argv[1:]
if a[0] == "listremotes": print("onedrive:")
elif a[0] == "copy": shutil.copytree(a[1], p(a[2]), dirs_exist_ok=True)
elif a[0] == "lsf":
    d = p(a[-1])
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []: print(n + "/")
elif a[0] == "purge": shutil.rmtree(p(a[1]))
'''


class Store:
    def __init__(self, folder: Path):
        self.db_path = str(folder / "data.db")
        c = sqlite3.connect(self.db_path)
        c.execute("CREATE TABLE t (x)")
        c.execute("INSERT INTO t VALUES (1)")
        c.commit()
        c.close()

    def backup_database(self):
        d = Path(self.db_path).parent / "backups"
        d.mkdir(exist_ok=True)
        dst = d / "data_20261007_030000.db"
        s, t = sqlite3.connect(self.db_path), sqlite3.connect(str(dst))
        s.backup(t)
        s.close()
        t.close()
        return dst


class OffsiteBackupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.remote = base / "remote"
        self.remote.mkdir()
        bindir = base / "bin"
        bindir.mkdir()
        exe = bindir / "rclone"
        exe.write_text(FAKE_RCLONE)
        exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
        (base / "config").mkdir()
        (base / "config" / "homeassistant.json").write_text("{}")
        (base / "config" / "homeassistant.example.json").write_text("{}")
        (base / "data").mkdir()
        (base / "data" / "heat_demand_model.json").write_text("{}")
        self.base = base
        self.patches = [
            mock.patch.dict(os.environ, {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                                         "FAKE_REMOTE_ROOT": str(self.remote)}),
            mock.patch.object(ob, "CONFIG_DIR", base / "config"),
            mock.patch.object(ob, "DATA_DIR", base / "data"),
            mock.patch.object(ob, "STATUS_PATH", base / "data" / "offsite_backup_status.json"),
        ]
        for p in self.patches:
            p.start()
        from core import forecast_learning as fl
        self.fl_patch = mock.patch.object(fl, "DB_PATH", base / "data" / "missing.db")
        self.fl_patch.start()

    def tearDown(self):
        self.fl_patch.stop()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_upload_and_prune(self):
        old = self.remote / "Dashboard-Backup" / "2026-08-01"
        old.mkdir(parents=True)
        (self.remote / "Dashboard-Backup" / "notes").mkdir()
        now = datetime(2026, 10, 7, 3, 5)
        st = ob.run_once(Store(self.base), now=now, status_path=ob.STATUS_PATH)
        day = self.remote / "Dashboard-Backup" / "2026-10-07"
        self.assertTrue((day / "data.db.gz").exists())
        self.assertTrue((day / "config" / "homeassistant.json").exists())
        self.assertFalse((day / "config" / "homeassistant.example.json").exists())
        self.assertTrue((day / "data" / "heat_demand_model.json").exists())
        self.assertFalse(old.exists())                         # > 30 Tage -> geloescht
        self.assertTrue((self.remote / "Dashboard-Backup" / "notes").exists())
        self.assertEqual(st["last_ok"], "2026-10-07T03:05:00")
        self.assertIn("heute", ob.status_text(st, now=now))
        # am selben Tag nicht nochmal
        self.assertFalse(ob.due(st, ob.load_config(), datetime(2026, 10, 7, 20)))
        self.assertTrue(ob.due(st, ob.load_config(), datetime(2026, 10, 8, 3, 1)))
        self.assertFalse(ob.due(st, ob.load_config(), datetime(2026, 10, 8, 1)))

    def test_not_configured(self):
        with mock.patch.object(ob, "rclone_exe", return_value=None):
            st = ob.run_once(Store(self.base), status_path=ob.STATUS_PATH)
        self.assertFalse(st["configured"])
        self.assertIn("nicht eingerichtet", ob.status_text(st))

    def test_alert_after_days(self):
        st = {"configured": True, "last_ok": "2026-10-01T03:00:00", "error": "offline"}
        with mock.patch("core.alerts.raise_alert") as ra:
            ob._maybe_alert(st, datetime(2026, 10, 5, 4))
            ra.assert_called_once()
        self.assertIn("🔴", ob.status_text(st, now=datetime(2026, 10, 5, 4)))


if __name__ == "__main__":
    unittest.main()
