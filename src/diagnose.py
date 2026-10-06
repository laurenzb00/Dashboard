"""Diagnose-Paket fuer den Raspberry Pi: sammelt alles, was zur Fehlersuche und
Optimierung hilft, in EINE Zip-Datei - ohne Passwoerter/Tokens.

Aufruf am Pi (Dashboard darf laufen):
    cd /home/laurenz/Dashboard && source .venv/bin/activate
    python src/diagnose.py              # dauert ca. 1-3 Minuten
    python src/diagnose.py --quick      # ohne Rechen-Benchmarks

Ergebnis: ~/dashboard_diagnose_<Datum>.zip  (report.txt + Logs + Leistungsprotokoll)

Inhalt
  1. System: Pi-Modell, OS, Python, Paketversionen, Temperatur, Drosselung,
     Speicher, Festplatte, laufender Dashboard-Prozess
  2. Datenbank: Groesse, Zeilen, Abtastrate, Luecken, Integritaet, neue
     Kessel-Spalten (Rauchgas/Betriebsmodus - zur Pruefung der BMK-Zuordnung)
  3. Lern-Archiv + gelernte Modelle (Kennzahlen), Prognose-Guete, Meldungen
  4. Benchmarks: wie lange typische Ladevorgaenge und das Lernen dauern
  5. Netzwerk: Antwortzeit von Kessel, Wechselrichter, Home Assistant, Open-Meteo
  6. Logs: letzte Zeilen + haeufigste Fehler; Leistungsprotokoll (perf_log.jsonl)
"""
from __future__ import annotations

import io
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
import zipfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parent
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

REPORT: list[str] = []
SECRETS: set[str] = set()


def say(line: str = "") -> None:
    print(line)
    REPORT.append(line)


def section(title: str) -> None:
    say("")
    say("=" * 70)
    say(title)
    say("=" * 70)


def guarded(fn):
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as exc:
            say(f"  !! {fn.__name__} fehlgeschlagen: {type(exc).__name__}: {exc}")
            REPORT.append("".join(traceback.format_exc(limit=3)))
    return wrapper


# ---------------------------------------------------------------------------
# Geheimnisse sammeln und aus allen Ausgaben entfernen
# ---------------------------------------------------------------------------

def collect_secrets() -> None:
    keys = re.compile(r"token|secret|pass|password|client_id|client_secret|api_key|key|webhook", re.I)

    def walk(o, key=""):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for v in o:
                walk(v, key)
        elif isinstance(o, str) and keys.search(key) and len(o) >= 6:
            SECRETS.add(o)
    for p in (ROOT / "config").glob("*.json"):
        if "example" in p.name:
            continue
        try:
            walk(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    for k, v in os.environ.items():
        if keys.search(k) and v and len(v) >= 6:
            SECRETS.add(v)
    for p in list(SRC.rglob(".tado_refresh_token")) + list(ROOT.glob("config/.spotify_cache")):
        try:
            SECRETS.add(p.read_text().strip())
        except Exception:
            pass


def redact(text: str) -> str:
    for s in sorted(SECRETS, key=len, reverse=True):
        text = text.replace(s, "***")
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1***", text)
    text = re.sub(r"(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+)", "***JWT***", text)
    return text


# ---------------------------------------------------------------------------
# 1. System
# ---------------------------------------------------------------------------

def _read(p: str) -> str:
    try:
        return Path(p).read_text(errors="replace").strip("\x00\n ")
    except Exception:
        return "?"


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return "?"


THROTTLE_BITS = {0: "Unterspannung JETZT", 1: "Takt begrenzt JETZT", 2: "gedrosselt JETZT", 3: "Temperaturgrenze JETZT",
                 16: "Unterspannung seit Start", 17: "Takt begrenzt seit Start", 18: "gedrosselt seit Start",
                 19: "Temperaturgrenze seit Start"}


@guarded
def system_info() -> None:
    section("1. SYSTEM")
    say(f"Modell:      {_read('/proc/device-tree/model')}")
    osr = _read("/etc/os-release")
    pretty = re.search(r'PRETTY_NAME="([^"]+)"', osr)
    say(f"OS:          {pretty.group(1) if pretty else platform.platform()}")
    say(f"Kernel:      {platform.release()}  ({platform.machine()})")
    say(f"Python:      {sys.version.split()[0]}  ({sys.executable})")
    up = _read("/proc/uptime").split()
    if up and up[0] != "?":
        say(f"Laufzeit:    {float(up[0]) / 3600:.1f} h seit Neustart")
    say(f"Load:        {os.getloadavg() if hasattr(os, 'getloadavg') else '?'}")
    try:
        t = int(_read("/sys/class/thermal/thermal_zone0/temp")) / 1000
        say(f"CPU-Temp:    {t:.1f} °C")
    except Exception:
        pass
    thr = _run(["vcgencmd", "get_throttled"])
    say(f"Drosselung:  {thr}")
    try:
        v = int(thr.split("=")[-1], 16)
        flags = [txt for bit, txt in THROTTLE_BITS.items() if v & (1 << bit)]
        say(f"             -> {', '.join(flags) if flags else 'keine Probleme'}")
    except Exception:
        pass
    say(f"Takt:        {_run(['vcgencmd', 'measure_clock', 'arm'])}   Spannung: {_run(['vcgencmd', 'measure_volts'])}")
    try:
        import psutil
        vm = psutil.virtual_memory()
        sw = psutil.swap_memory()
        say(f"RAM:         {vm.total / 1e9:.1f} GB, frei {vm.available / 1e9:.2f} GB ({vm.percent:.0f} % belegt), "
            f"Swap belegt {sw.used / 1e6:.0f} MB")
        du = shutil.disk_usage(ROOT)
        say(f"Speicher:    {du.total / 1e9:.1f} GB, frei {du.free / 1e9:.1f} GB")
        found = False
        for p in psutil.process_iter(["pid", "cmdline", "create_time"]):
            cmd = " ".join(p.info.get("cmdline") or [])
            if "main.py" in cmd and "python" in cmd:
                found = True
                with p.oneshot():
                    p.cpu_percent(None)
                time.sleep(1.0)
                say(f"Dashboard:   PID {p.pid}, läuft seit {(time.time() - p.info['create_time']) / 3600:.1f} h, "
                    f"CPU {p.cpu_percent(None):.0f} %, RAM {p.memory_info().rss / 1e6:.0f} MB, "
                    f"{p.num_threads()} Threads")
        if not found:
            say("Dashboard:   läuft gerade NICHT")
    except ImportError:
        say("psutil fehlt")
    xr = (_run(["xrandr", "--current"]) if shutil.which("xrandr") else "").splitlines()
    say(f"Display:     DISPLAY={os.environ.get('DISPLAY', '-')}  {xr[0] if xr else ''}")
    say("")
    say("Pakete:")
    from importlib import metadata
    for name in ("customtkinter", "ttkbootstrap", "matplotlib", "numpy", "pandas", "requests", "psutil",
                 "python-tado", "PyTado", "spotipy", "Pillow", "pytz", "icalendar"):
        try:
            say(f"  {name:15s} {metadata.version(name)}")
        except Exception:
            say(f"  {name:15s} -")
    git = _run(["git", "-C", str(ROOT), "log", "-1", "--format=%h %ci %s"])
    say(f"Git-Stand:   {git}")
    dirty = _run(["git", "-C", str(ROOT), "status", "--short"])
    if dirty:
        say(f"  lokale Änderungen: {dirty.replace(chr(10), ' | ')[:300]}")


# ---------------------------------------------------------------------------
# 2. Datenbank
# ---------------------------------------------------------------------------

def db_path() -> Path:
    return Path(os.environ.get("DASHBOARD_DB_PATH", str(SRC / "core" / "data.db"))).expanduser()


@guarded
def database() -> None:
    section("2. DATENBANK")
    p = db_path()
    if not p.exists():
        say(f"nicht gefunden: {p}")
        return
    wal = p.with_name(p.name + "-wal")
    say(f"Datei:       {p}  {p.stat().st_size / 1e6:.0f} MB, WAL {wal.stat().st_size / 1e6 if wal.exists() else 0:.1f} MB")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True, timeout=30)
    t0 = time.monotonic()
    qc = conn.execute("PRAGMA quick_check").fetchone()[0]
    say(f"Integrität:  {qc}  ({time.monotonic() - t0:.1f} s)")
    say(f"Schema-Ver.: {conn.execute('PRAGMA user_version').fetchone()[0]}   Journal: "
        f"{conn.execute('PRAGMA journal_mode').fetchone()[0]}")
    now = datetime.now(timezone.utc)
    for table in ("fronius", "heating"):
        try:
            n, lo, hi = conn.execute(f"SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM {table}").fetchone()
        except Exception:
            continue
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        say(f"{table:8s}:   {n} Zeilen, {lo} … {hi} (UTC)")
        say(f"             Spalten: {', '.join(cols)}")
        cut = (now - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        n24 = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE timestamp >= ?", (cut,)).fetchone()[0]
        say(f"             letzte 24 h: {n24} Zeilen = alle {86400 / max(n24, 1):.0f} s ein Wert")
        # Luecken > 15 min in den letzten 30 Tagen
        cut30 = (now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
        ts = [r[0] for r in conn.execute(f"SELECT timestamp FROM {table} WHERE timestamp >= ? ORDER BY timestamp", (cut30,))]
        gaps = []
        prev = None
        for t in ts:
            d = datetime.strptime(t[:19], "%Y-%m-%d %H:%M:%S")
            if prev and (d - prev) > timedelta(minutes=15):
                gaps.append((prev, d))
            prev = d
        say(f"             Lücken > 15 min (30 Tage): {len(gaps)}" +
            ("" if not gaps else "  längste: " + ", ".join(f"{a:%d.%m. %H:%M}–{b:%d.%m. %H:%M}"
                                                           for a, b in sorted(gaps, key=lambda g: g[1] - g[0])[-3:])))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(heating)")]
    if "rauchgastemp" in cols:
        say("")
        say("Kessel-Zustand (neue Spalten, letzte 14 Tage):")
        cut = (now - timedelta(days=14)).strftime("%Y-%m-%d %H:%M:%S")
        n, nn, mn, mx = conn.execute("SELECT COUNT(*), COUNT(rauchgastemp), MIN(rauchgastemp), MAX(rauchgastemp) "
                                     "FROM heating WHERE timestamp >= ?", (cut,)).fetchone()
        say(f"  Rauchgas: {nn}/{n} Werte, min {mn}, max {mx}")
        say("  Betriebsmodus -> Anzahl, Ø Kessel, Ø Rauchgas (zum Zuordnen der Codes):")
        for row in conn.execute("SELECT betriebsmodus, COUNT(*), ROUND(AVG(kesseltemp),1), ROUND(AVG(rauchgastemp),1) "
                                "FROM heating WHERE timestamp >= ? GROUP BY betriebsmodus ORDER BY 2 DESC LIMIT 15", (cut,)):
            say(f"    {row}")
        say("  Rauchgas > 90 °C (Feuer?) je Tag:")
        for row in conn.execute("SELECT substr(timestamp,1,10), COUNT(*), ROUND(MAX(rauchgastemp)), ROUND(MAX(kesseltemp)) "
                                "FROM heating WHERE timestamp >= ? AND rauchgastemp > 90 GROUP BY 1", (cut,)):
            say(f"    {row}")
    bdir = p.parent / "backups"
    if bdir.exists():
        bs = sorted(bdir.glob("*.db"))
        say(f"Backups:     {len(bs)} Dateien, {sum(b.stat().st_size for b in bs) / 1e6:.0f} MB, neuestes {bs[-1].name if bs else '-'}")
    conn.close()


# ---------------------------------------------------------------------------
# 3. Lern-Archiv, Modelle, Prognose-Guete, Meldungen
# ---------------------------------------------------------------------------

@guarded
def learning() -> None:
    section("3. LERNEN / PROGNOSEN / MELDUNGEN")
    from core import forecast_learning as fl
    if not fl.DB_PATH.exists():
        say("Lern-Archiv noch nicht angelegt")
    else:
        conn = sqlite3.connect(f"file:{fl.DB_PATH}?mode=ro", uri=True)
        say(f"Lern-Archiv: {fl.DB_PATH.stat().st_size / 1e6:.1f} MB")
        for t in ("weather", "pv_hours", "heat_hours", "pv_forecast_log", "alerts"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                col = {"weather": "hour_end", "pv_hours": "hour_end", "heat_hours": "hour_start",
                       "pv_forecast_log": "target_hour", "alerts": "ts"}[t]
                lo, hi = conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {t}").fetchone()
                rng = f"{datetime.fromtimestamp(lo):%d.%m.%Y} … {datetime.fromtimestamp(hi):%d.%m.%Y %H:%M}" if lo else ""
                say(f"  {t:16s} {n:7d}  {rng}")
            except Exception:
                say(f"  {t:16s} -")
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall()) if conn else {}
        say(f"  meta: {meta}")
        try:
            for ts, kind, title, msg in conn.execute("SELECT ts, kind, title, message FROM alerts ORDER BY ts DESC LIMIT 10"):
                say(f"  Meldung {datetime.fromtimestamp(ts):%d.%m. %H:%M} [{kind}] {title}: {msg}")
        except Exception:
            pass
        conn.close()
    for name in ("pv_forecast_model.json", "heat_demand_model.json", "solar_thermal_model.json"):
        p = ROOT / "data" / name
        if not p.exists():
            say(f"{name}: -")
            continue
        d = json.loads(p.read_text(encoding="utf-8"))
        small = {k: v for k, v in d.items() if not isinstance(v, (list, dict))}
        if isinstance(d.get("bias"), dict):
            small["bias"] = {k: v for k, v in d["bias"].items() if not isinstance(v, list)}
        say(f"{name}: {json.dumps(small, ensure_ascii=False)[:900]}")
    try:
        from core import forecast_log
        sk = forecast_log.pv_skill(days=30)
        if sk:
            say(f"PV-Prognose vom Vortag (30 Tage): {sk['days']} Tage, Ø {sk['mape_pct']:.0f} % daneben, "
                f"Tendenz {sk['bias_pct']:+.0f} %")
            for d, fc, ist in sk["per_day"][-14:]:
                say(f"    {d:%d.%m.}  Prognose {fc:5.1f}  Ist {ist:5.1f} kWh")
        else:
            say("PV-Prognose-Güte: noch keine Daten")
    except Exception as exc:
        say(f"PV-Prognose-Güte: {exc}")


# ---------------------------------------------------------------------------
# 4. Benchmarks
# ---------------------------------------------------------------------------

@guarded
def benchmarks() -> None:
    section("4. BENCHMARKS (Dauer typischer Aufgaben)")
    from core import heating_stats as hs

    class Store:
        conn = sqlite3.connect(f"file:{db_path()}?mode=ro", uri=True, timeout=30, check_same_thread=False)

    def bench(name, fn):
        t0 = time.monotonic()
        try:
            r = fn()
            say(f"  {name:45s} {time.monotonic() - t0:7.2f} s")
            return r
        except Exception as exc:
            say(f"  {name:45s} FEHLER {type(exc).__name__}: {exc}")
    now = datetime.now()
    bench("Heizdaten laden 24 h (load_buckets)", lambda: hs.load_buckets(Store, now - timedelta(hours=24), now))
    bench("Heizdaten laden 7 Tage", lambda: hs.load_buckets(Store, now - timedelta(days=7), now))
    bench("Saison-Statistik (season_stats)", lambda: hs.season_stats(Store, hs.load_storage_config()))
    try:
        from core import energy_day
        bench("Ertrag Tagesdaten heute", lambda: energy_day.summarize(energy_day.load_day_samples(Store, now.date())))
    except Exception:
        pass
    from core import forecast_learning as fl
    if fl.DB_PATH.exists():
        from core import heat_demand as hd, pv_forecast as pf
        from core.weather import load_weather_config
        from datetime import date as _date
        fl._updated_day = _date.today()          # Archiv hier NICHT veraendern, nur lesen
        cfg = load_weather_config()
        lconn = sqlite3.connect(f"file:{fl.DB_PATH}?mode=ro", uri=True)
        data = bench("PV: Lerndaten lesen", lambda: pf.training_data(lconn))
        if data is not None:
            m = bench("PV: Modell lernen (ohne Speichern)", lambda: pf.fit_from_data(data, cfg.latitude, cfg.longitude))
            if m:
                say(f"      -> Neigung {m['tilt']}°, R² {m['r2']:.3f}, Stundenfehler {m['rmse_phys_kw']:.2f} -> "
                    f"{m['rmse_final_kw']:.2f} kW, Tagesfehler {m.get('day_mape_pct', float('nan')):.0f} %, "
                    f"{m['hours']} h, Ausreißer {m['outliers']}")
        lconn.close()
        res = bench("Wärme: Modelle lernen (ohne Speichern)", lambda: hd.learn(Store, allow_network=False))
        if res and res[0]:
            d = res[0]
            say(f"      -> Trägheit {d.tau_h:g} h, Heizgrenze {d.tb_c:g} °C, {d.hours} h, CV {d.cv_rmse_global:.2f}/"
                f"{d.cv_rmse:.2f} kW, Raster {'ja' if d.table else 'nein'}, ignorierte Tage {len(d.anomaly_days or [])}")
            for t in (-10, 0, 10, 20):
                say(f"         {t:4d} °C: {d.kw_at(float(t)):.2f} kW")


# ---------------------------------------------------------------------------
# 5. Netzwerk
# ---------------------------------------------------------------------------

@guarded
def network() -> None:
    section("5. NETZWERK (Antwortzeiten)")
    import requests
    targets = []
    try:
        from core.BMKDATEN import BMK_URL
        targets.append(("Kessel (BMK)", BMK_URL, {}))
    except Exception:
        pass
    try:
        from core.Wechselrichter import FRONIUS_URL
        targets.append(("Wechselrichter", FRONIUS_URL, {}))
    except Exception:
        pass
    try:
        from core.homeassistant import load_homeassistant_config
        ha = load_homeassistant_config()
        if ha:
            targets.append(("Home Assistant", ha.url + "/api/", {"Authorization": f"Bearer {ha.token}"}))
            say(f"  HA notify_service: {ha.notify_service or '(nicht gesetzt -> HA-Benachrichtigung)'}")
    except Exception:
        pass
    targets.append(("Open-Meteo", "https://api.open-meteo.com/v1/forecast?latitude=48.25&longitude=13.04&hourly=temperature_2m&forecast_days=1", {}))
    for name, url, headers in targets:
        times, status = [], None
        for _ in range(3):
            t0 = time.monotonic()
            try:
                r = requests.get(url, headers=headers, timeout=8)
                status = r.status_code
                times.append((time.monotonic() - t0) * 1000)
            except Exception as exc:
                status = type(exc).__name__
        say(f"  {name:16s} Status {status}, " + (f"{min(times):.0f}/{max(times):.0f} ms (min/max)" if times else "keine Antwort"))
    try:
        r = requests.get(targets[0][1], timeout=8) if targets else None
        if r is not None and r.ok:
            vals = [v.strip() for v in r.text.splitlines() if v.strip()]
            from core.BMKDATEN import PP_INDEX_MAPPING
            say("  BMK-Rohwerte jetzt (Index: Name = Wert) – zum Prüfen der Zuordnung:")
            for i, v in enumerate(vals[:75]):
                say(f"    {i:2d}: {PP_INDEX_MAPPING.get(i, '?'):28s} = {v}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 6. Logs + Leistungsprotokoll
# ---------------------------------------------------------------------------

LOGS = ["datenerfassung.log", "data/app_debug.log", "src/crash.log", "src/test_run.log", "crash.log"]


def _tail(p: Path, n: int) -> str:
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 2_000_000))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    except Exception:
        return ""


@guarded
def logs_and_perf(zf: zipfile.ZipFile) -> None:
    section("6. LOGS / LEISTUNG")
    norm = re.compile(r"\d+([.,:]\d+)*")
    for rel in LOGS:
        p = ROOT / rel
        if not p.exists():
            continue
        text = _tail(p, 3000)
        zf.writestr(f"logs/{p.name}", redact(text))
        c = Counter()
        for line in text.splitlines():
            if "ERROR" in line or "WARNING" in line or "Traceback" in line:
                msg = line.split(" - ")[-1] if " - " in line else line
                c[norm.sub("#", msg)[:140]] += 1
        say(f"{rel}: {p.stat().st_size / 1e6:.1f} MB, häufigste Warnungen/Fehler (letzte 3000 Zeilen):")
        for msg, n in c.most_common(12):
            say(f"  {n:5d}× {redact(msg)}")
    perf = ROOT / "data" / "perf_log.jsonl"
    if not perf.exists():
        say("perf_log.jsonl: noch nicht vorhanden (Dashboard mit neuem Code noch nicht gelaufen)")
        return
    raw = perf.read_text(encoding="utf-8", errors="replace")
    old = perf.with_suffix(".jsonl.1")
    if old.exists():
        raw = old.read_text(encoding="utf-8", errors="replace") + raw
    zf.writestr("perf_log.jsonl", redact(raw))
    rows = []
    for line in raw.splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    mins = [r for r in rows if r.get("kind") == "minute"]
    frz = [r for r in rows if r.get("kind") == "freeze"]
    tim = [r for r in rows if r.get("kind") == "timing"]
    if mins:
        def stat(key):
            v = sorted(r[key] for r in mins if r.get(key) is not None)
            return f"Median {v[len(v) // 2]}, 95 % {v[int(len(v) * 0.95)]}, Max {v[-1]}" if v else "-"
        span = (mins[-1]["ts"] - mins[0]["ts"]) / 3600
        say(f"Leistung über {span:.1f} h ({len(mins)} Minuten):")
        for key, label in (("ui_lag_ms_p95", "UI-Verzögerung p95 [ms]"), ("ui_lag_ms_max", "UI-Verzögerung max [ms]"),
                           ("cpu_pct", "CPU Dashboard [%]"), ("sys_cpu_pct", "CPU gesamt [%]"),
                           ("rss_mb", "RAM Dashboard [MB]"), ("threads", "Threads"), ("cpu_temp_c", "CPU-Temp [°C]"),
                           ("sys_mem_avail_mb", "RAM frei [MB]")):
            say(f"  {label:26s} {stat(key)}")
        rss = [r["rss_mb"] for r in mins if r.get("rss_mb")]
        if len(rss) > 60:
            say(f"  RAM-Trend: erste Stunde Ø {sum(rss[:60]) / 60:.0f} MB -> letzte Stunde Ø {sum(rss[-60:]) / 60:.0f} MB")
        thr = {r.get("throttled") for r in mins if r.get("throttled")}
        say(f"  Drosselung gemeldet: {thr or '-'}")
    say(f"Hänger der Oberfläche (> 1 s): {len(frz)}")
    where = Counter()
    for f in frz:
        st = [s for s in f.get("stack", []) if "/src/" in s or "\\src\\" in s]
        where[(st[-1] if st else (f.get("stack") or ["?"])[-1])[:160]] += 1
    for w, n in where.most_common(8):
        say(f"  {n:4d}× {w}")
    if frz:
        longest = max(frz, key=lambda f: f.get("seconds", 0))
        say(f"  längster: {longest.get('seconds')} s am {datetime.fromtimestamp(longest['ts']):%d.%m. %H:%M}")
    if tim:
        agg: dict = {}
        for t in tim:
            agg.setdefault(t["name"], []).append(t["ms"])
        say("Dauer von Aufgaben:")
        for name, v in sorted(agg.items(), key=lambda kv: -max(kv[1])):
            v.sort()
            say(f"  {name:28s} {len(v):4d}×  Median {v[len(v) // 2] / 1000:6.2f} s  Max {v[-1] / 1000:6.2f} s")


def main() -> None:
    quick = "--quick" in sys.argv
    collect_secrets()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = Path.home() / f"dashboard_diagnose_{stamp}.zip"
    say(f"Dashboard-Diagnose {datetime.now():%d.%m.%Y %H:%M}")
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        system_info()
        database()
        learning()
        if not quick:
            benchmarks()
        network()
        logs_and_perf(zf)
        zf.writestr("report.txt", redact("\n".join(REPORT)))
    print("")
    print(f"Fertig: {out}  ({out.stat().st_size / 1e6:.1f} MB)")
    print("Auf den Laptop holen, z.B.:")
    print(f'  scp laurenz@192.168.1.200:{out} "C:\\Users\\laure\\OneDrive\\Studium-SBG\\SoS 25\\Datenerfassung\\eigenes projekt\\analyse"')


if __name__ == "__main__":
    main()
