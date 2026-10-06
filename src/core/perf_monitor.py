"""Leistungs-Protokoll fuer den Betrieb am Raspberry Pi (immer an, sehr sparsam).

Schreibt nach data/perf_log.jsonl (eine JSON-Zeile je Eintrag, max. 5 MB + 1 Altdatei):

* "minute"  jede Minute: CPU/RAM/Threads des Dashboards, Systemlast,
            CPU-Temperatur, Drosselung des Pi (vcgencmd get_throttled),
            Reaktionszeit der Oberflaeche (wie spaet kommt ein 250-ms-Takt an:
            Median, 95 %, Maximum).
* "freeze"  die Oberflaeche hing > FREEZE_S: Dauer und WO der Hauptthread
            gerade steckte (Stack) - zeigt direkt, welche Funktion blockiert.
* "timing"  Dauer von bekannten schweren Aufgaben (Lernen, Prognose ...),
            Aufruf ueber `with timed("name"):`.

Auswertung: python src/diagnose.py packt alles zusammen.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import threading
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "perf_log.jsonl"
MAX_BYTES = 5 * 1024 * 1024
TICK_MS = 250
FREEZE_S = 1.0
SUMMARY_S = 60

_lock = threading.Lock()
_started = False
_lags: list[float] = []
_last_beat = time.monotonic()
_freezes = 0


def _write(entry: dict) -> None:
    # Nur im laufenden Dashboard protokollieren (nicht in Tests/Diagnose-Skript)
    if not _started:
        return
    entry.setdefault("ts", round(time.time(), 1))
    line = json.dumps(entry, ensure_ascii=False)
    with _lock:
        try:
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_BYTES:
                LOG_PATH.replace(LOG_PATH.with_suffix(".jsonl.1"))
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


@contextmanager
def timed(name: str, min_ms: float = 50.0, **extra):
    """Dauer einer Aufgabe protokollieren (nur wenn >= min_ms)."""
    t0 = time.monotonic()
    ok = True
    try:
        yield
    except Exception:
        ok = False
        raise
    finally:
        ms = (time.monotonic() - t0) * 1000.0
        if ms >= min_ms:
            _write({"kind": "timing", "name": name, "ms": round(ms, 1), "ok": ok,
                    "thread": threading.current_thread().name, **extra})


def _cpu_temp() -> Optional[float]:
    try:
        return int(Path("/sys/class/thermal/thermal_zone0/temp").read_text().strip()) / 1000.0
    except Exception:
        return None


def _fan_rpm() -> Optional[int]:
    try:
        import glob
        for p in glob.glob("/sys/devices/platform/cooling_fan/hwmon/hwmon*/fan1_input"):
            return int(Path(p).read_text().strip())
    except Exception:
        pass
    return None


def _throttled() -> Optional[str]:
    try:
        out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=3).stdout
        return out.strip().split("=")[-1] or None
    except Exception:
        return None


def _summary(proc) -> dict:
    global _lags, _freezes
    with _lock:
        lags, _lags = _lags, []
        freezes, _freezes = _freezes, 0
    lags.sort()

    def pct(p):
        return round(lags[min(len(lags) - 1, int(len(lags) * p))], 1) if lags else None
    e = {"kind": "minute", "ui_lag_ms_p50": pct(0.5), "ui_lag_ms_p95": pct(0.95),
         "ui_lag_ms_max": round(lags[-1], 1) if lags else None, "ui_ticks": len(lags), "freezes": freezes,
         "cpu_temp_c": _cpu_temp(), "fan_rpm": _fan_rpm()}
    try:
        e["load1"] = round(os.getloadavg()[0], 2)
    except Exception:
        pass
    if proc is not None:
        try:
            with proc.oneshot():
                e["cpu_pct"] = proc.cpu_percent(None)
                e["rss_mb"] = round(proc.memory_info().rss / 1e6, 1)
                e["threads"] = proc.num_threads()
                try:
                    e["fds"] = proc.num_fds()
                except Exception:
                    pass
            import psutil
            vm = psutil.virtual_memory()
            e["sys_mem_avail_mb"] = round(vm.available / 1e6)
            e["sys_cpu_pct"] = psutil.cpu_percent(None)
        except Exception:
            pass
    return e


def start(root) -> None:
    """Am Tk-Hauptthread aufrufen, sobald das Fenster existiert (idempotent)."""
    global _started, _last_beat
    if _started:
        return
    _started = True
    main_ident = threading.get_ident()
    try:
        import psutil
        proc = psutil.Process()
        proc.cpu_percent(None)
        psutil.cpu_percent(None)
    except Exception:
        proc = None
    _write({"kind": "start", "pid": os.getpid(), "python": sys.version.split()[0],
            "throttled": _throttled()})

    expected = [time.monotonic() + TICK_MS / 1000.0]

    def tick():
        global _last_beat
        now = time.monotonic()
        with _lock:
            _lags.append(max(0.0, (now - expected[0]) * 1000.0))
        _last_beat = now
        expected[0] = now + TICK_MS / 1000.0
        try:
            root.after(TICK_MS, tick)
        except Exception:
            pass

    root.after(TICK_MS, tick)

    def watchdog():
        global _freezes
        reported_for = None
        last_summary = time.monotonic()
        last_throttle = 0.0
        while True:
            time.sleep(0.25)
            now = time.monotonic()
            stall = now - _last_beat
            if stall >= FREEZE_S and reported_for != _last_beat:
                reported_for = _last_beat
                frame = sys._current_frames().get(main_ident)
                stack = traceback.format_stack(frame)[-12:] if frame else []
                # warten, bis die Oberflaeche wieder reagiert, um die Gesamtdauer zu kennen
                beat = _last_beat
                while _last_beat == beat and time.monotonic() - beat < 120:
                    time.sleep(0.1)
                with _lock:
                    _freezes += 1
                _write({"kind": "freeze", "seconds": round(time.monotonic() - beat, 2),
                        "stack": [s.strip() for s in stack]})
            if now - last_summary >= SUMMARY_S:
                last_summary = now
                e = _summary(proc)
                if now - last_throttle >= 600:
                    last_throttle = now
                    e["throttled"] = _throttled()
                _write(e)

    threading.Thread(target=watchdog, daemon=True, name="perf-monitor").start()
