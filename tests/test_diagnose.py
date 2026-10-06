"""Diagnose-Skript: Geheimnisse werden entfernt; Leistungsprotokoll schreibt nur im laufenden Dashboard."""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import diagnose  # noqa: E402
from core import perf_monitor  # noqa: E402


class TestDiagnose(unittest.TestCase):
    def test_redact(self):
        diagnose.SECRETS.clear()
        diagnose.SECRETS.add("geheimes-token-123")
        txt = "url=http://x token=geheimes-token-123 Authorization: Bearer abc.def.ghi eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abcdefgh"
        out = diagnose.redact(txt)
        self.assertNotIn("geheimes-token-123", out)
        self.assertNotIn("abc.def.ghi", out)
        self.assertNotIn("eyJhbGci", out)
        self.assertIn("url=http://x", out)

    def test_perf_log_only_when_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "perf.jsonl"
            with mock.patch.object(perf_monitor, "LOG_PATH", log), mock.patch.object(perf_monitor, "_started", False):
                with perf_monitor.timed("x", min_ms=0):
                    pass
                self.assertFalse(log.exists())
            with mock.patch.object(perf_monitor, "LOG_PATH", log), mock.patch.object(perf_monitor, "_started", True):
                with perf_monitor.timed("x", min_ms=0):
                    pass
                self.assertIn('"name": "x"', log.read_text())


if __name__ == "__main__":
    unittest.main()
