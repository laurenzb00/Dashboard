"""Taegliches Backup nach OneDrive (oder jedes andere rclone-Ziel).

Warum: Das lokale Backup (DataStore.backup_database -> src/core/backups/)
liegt auf derselben SD-Karte wie die Datenbank. Stirbt die Karte, ist beides
weg. Deshalb wird einmal am Tag eine Kopie mit rclone hochgeladen:

    <ziel>/2026-10-07/data.db.gz
                      forecast_learning.db.gz
                      config/*.json
                      data/*.json            (gelernte Modelle, klein)

Einrichtung am Pi (einmalig):  sudo apt install rclone  und ein rclone-Ziel
namens "onedrive" anlegen (siehe Anleitung in docs bzw. Chat). Ohne rclone
oder ohne Ziel passiert nichts - der Health-Tab zeigt "nicht eingerichtet".

Optionale Einstellungen in config/backup.json:
    {"remote": "onedrive:Dashboard-Backup", "keep_days": 30, "hour": 3}
"""
from __future__ import annotations

import gzip
import json
import logging
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
STATUS_PATH = DATA_DIR / "offsite_backup_status.json"

DEFAULTS = {"remote": "onedrive:Dashboard-Backup", "keep_days": 30, "hour": 3}
ALERT_AFTER_DAYS = 3
CHECK_INTERVAL_S = 3600
UPLOAD_TIMEOUT_S = 45 * 60

_started = False
_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Einstellungen / Status
# ---------------------------------------------------------------------------

def load_config(path: Optional[Path] = None) -> dict:
    cfg = dict(DEFAULTS)
    p = path or (CONFIG_DIR / "backup.json")
    try:
        if p.exists():
            cfg.update(json.loads(p.read_text(encoding="utf-8")) or {})
    except Exception as exc:
        logger.warning("[Backup] config/backup.json nicht lesbar: %s", exc)
    return cfg


def load_status(path: Optional[Path] = None) -> dict:
    try:
        return json.loads((path or STATUS_PATH).read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_status(status: dict, path: Optional[Path] = None) -> None:
    p = path or STATUS_PATH
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=1), encoding="utf-8")
        tmp.replace(p)
    except Exception as exc:
        logger.warning("[Backup] Status nicht gespeichert: %s", exc)


# ---------------------------------------------------------------------------
# rclone
# ---------------------------------------------------------------------------

def rclone_exe() -> Optional[str]:
    return shutil.which("rclone")


def _run(args: list[str], timeout: float = 120) -> tuple[int, str]:
    try:
        p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, "Zeitueberschreitung"
    except Exception as exc:
        return 1, str(exc)


def remote_configured(remote: str) -> bool:
    exe = rclone_exe()
    if not exe:
        return False
    rc, out = _run([exe, "listremotes"], timeout=30)
    name = remote.split(":", 1)[0] + ":"
    return rc == 0 and name in out.split()


# ---------------------------------------------------------------------------
# Paket bauen
# ---------------------------------------------------------------------------

def _gzip_copy(src: Path, dst: Path) -> None:
    with open(src, "rb") as fi, gzip.open(dst, "wb", compresslevel=6) as fo:
        shutil.copyfileobj(fi, fo, 1024 * 1024)


def _sqlite_snapshot(src: Path, dst: Path) -> None:
    """WAL-sichere Kopie ueber die SQLite-Backup-API (nur lesend auf src)."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=15)
    d = sqlite3.connect(str(dst))
    try:
        s.backup(d)
    finally:
        d.close()
        s.close()


def build_package(staging: Path, db_backup: Optional[Path], learning_db: Optional[Path],
                  config_dir: Optional[Path] = None, data_dir: Optional[Path] = None) -> list[str]:
    """Legt alle Dateien fuer den Upload in `staging` ab. Gibt die Dateinamen zurueck."""
    config_dir = config_dir or CONFIG_DIR
    data_dir = data_dir or DATA_DIR
    files: list[str] = []
    if db_backup and db_backup.exists():
        _gzip_copy(db_backup, staging / "data.db.gz")
        files.append("data.db.gz")
    if learning_db and learning_db.exists():
        tmp = staging / "forecast_learning.db"
        try:
            _sqlite_snapshot(learning_db, tmp)
            _gzip_copy(tmp, staging / "forecast_learning.db.gz")
            files.append("forecast_learning.db.gz")
        except Exception as exc:
            logger.warning("[Backup] Lern-Archiv nicht gesichert: %s", exc)
        finally:
            tmp.unlink(missing_ok=True)
    if config_dir.exists():
        (staging / "config").mkdir(exist_ok=True)
        for f in sorted(config_dir.glob("*.json")):
            if f.name.endswith(".example.json"):
                continue
            shutil.copy2(f, staging / "config" / f.name)
            files.append(f"config/{f.name}")
    if data_dir.exists():
        small = [f for f in sorted(data_dir.glob("*.json"))
                 if f.stat().st_size < 5 * 1024 * 1024 and f.name != STATUS_PATH.name]
        if small:
            (staging / "data").mkdir(exist_ok=True)
            for f in small:
                shutil.copy2(f, staging / "data" / f.name)
                files.append(f"data/{f.name}")
    return files


def newest_local_backup(db_path: Path) -> Optional[Path]:
    d = Path(db_path).parent / "backups"
    found = sorted(d.glob(f"{Path(db_path).stem}_*.db")) if d.exists() else []
    return found[-1] if found else None


# ---------------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------------

def due(status: dict, cfg: dict, now: Optional[datetime] = None) -> bool:
    """Einmal pro Tag ab cfg['hour'] Uhr; nach einer Luecke > 1 Tag sofort."""
    now = now or datetime.now()
    last_ok = status.get("last_ok")
    if not last_ok:
        return True
    try:
        last = datetime.fromisoformat(last_ok)
    except ValueError:
        return True
    if last.date() >= now.date():
        return False
    if now - last > timedelta(hours=36):
        return True
    return now.hour >= int(cfg.get("hour", 3))


def prune(exe: str, remote: str, keep_days: int, today: Optional[date] = None) -> list[str]:
    """Tagesordner aelter als keep_days im Ziel loeschen."""
    today = today or date.today()
    rc, out = _run([exe, "lsf", "--dirs-only", remote], timeout=120)
    if rc != 0:
        return []
    removed = []
    for line in out.splitlines():
        name = line.strip().rstrip("/")
        try:
            d = date.fromisoformat(name)
        except ValueError:
            continue
        if (today - d).days > keep_days:
            if _run([exe, "purge", f"{remote}/{name}"], timeout=300)[0] == 0:
                removed.append(name)
    return removed


def run_once(store=None, force: bool = False, now: Optional[datetime] = None,
             status_path: Optional[Path] = None) -> dict:
    """Backup ausfuehren, falls faellig. Gibt den (neuen) Status zurueck."""
    if not _lock.acquire(blocking=False):
        return load_status(status_path)
    try:
        cfg = load_config()
        status = load_status(status_path)
        now = now or datetime.now()
        remote = str(cfg["remote"])
        exe = rclone_exe()
        if not exe or not remote_configured(remote):
            status.update(configured=False, remote=remote)
            _save_status(status, status_path)
            return status
        status["configured"] = True
        status["remote"] = remote
        if not force and not due(status, cfg, now):
            return status

        status["last_try"] = now.isoformat(timespec="seconds")
        db_backup = None
        if store is not None:
            try:
                db_backup = store.backup_database() or newest_local_backup(Path(store.db_path))
            except Exception as exc:
                logger.warning("[Backup] Lokales Backup fehlgeschlagen: %s", exc)
        if db_backup is None:
            try:
                from .datastore import DB_PATH as _db
                db_backup = newest_local_backup(Path(_db))
            except Exception:
                db_backup = None
        try:
            from . import forecast_learning as fl
            learning = Path(fl.DB_PATH)
        except Exception:
            learning = None

        with tempfile.TemporaryDirectory(prefix="dash_backup_") as tmp:
            staging = Path(tmp)
            files = build_package(staging, db_backup, learning)
            size = sum(p.stat().st_size for p in staging.rglob("*") if p.is_file())
            target = f"{remote}/{now.date().isoformat()}"
            rc, out = _run([exe, "copy", str(staging), target, "--transfers", "2", "--retries", "3"],
                           timeout=UPLOAD_TIMEOUT_S)
        if rc == 0 and "data.db.gz" in files:
            status.update(last_ok=now.isoformat(timespec="seconds"), error="", size_bytes=size,
                          files=len(files))
            try:
                pruned = prune(exe, remote, int(cfg.get("keep_days", 30)), now.date())
                if pruned:
                    logger.info("[Backup] Alte Backups entfernt: %s", ", ".join(pruned))
            except Exception as exc:
                logger.info("[Backup] Aufraeumen fehlgeschlagen: %s", exc)
            logger.info("[Backup] OneDrive-Backup ok: %s (%.1f MB)", target, size / 1e6)
        else:
            err = "keine Datenbank-Sicherung gefunden" if rc == 0 else (out.strip().splitlines() or ["?"])[-1][:200]
            status["error"] = err
            logger.warning("[Backup] Upload fehlgeschlagen: %s", err)
        _save_status(status, status_path)
        _maybe_alert(status, now)
        return status
    finally:
        _lock.release()


def _maybe_alert(status: dict, now: datetime) -> None:
    if not status.get("configured"):
        return
    ref = status.get("last_ok") or status.get("first_try")
    if not ref:
        status["first_try"] = now.isoformat(timespec="seconds")
        return
    try:
        age = now - datetime.fromisoformat(ref)
    except ValueError:
        return
    if age > timedelta(days=ALERT_AFTER_DAYS):
        try:
            from .alerts import raise_alert
            raise_alert("backup", "Backup fehlt",
                        f"Seit {age.days} Tagen kein OneDrive-Backup. Letzter Fehler: {status.get('error') or '-'}")
        except Exception as exc:
            logger.info("[Backup] Meldung nicht gesendet: %s", exc)


def status_text(status: Optional[dict] = None, now: Optional[datetime] = None) -> str:
    """Eine Zeile fuer den Health-Tab."""
    st = load_status() if status is None else status
    now = now or datetime.now()
    if not st or not st.get("configured"):
        return "⚪ OneDrive-Backup: nicht eingerichtet"
    last_ok = st.get("last_ok")
    if not last_ok:
        err = st.get("error")
        return "🟠 OneDrive-Backup: noch keins" + (f" ({err})" if err else "")
    try:
        last = datetime.fromisoformat(last_ok)
    except ValueError:
        return "OneDrive-Backup: –"
    days = (now.date() - last.date()).days
    when = "heute" if days == 0 else "gestern" if days == 1 else f"vor {days} Tagen"
    mb = (st.get("size_bytes") or 0) / 1e6
    icon = "🟢" if days <= 1 else "🟠" if days <= ALERT_AFTER_DAYS else "🔴"
    txt = f"{icon} OneDrive-Backup: {when} {last.strftime('%H:%M')} ({mb:.0f} MB)"
    if st.get("error") and days > 0:
        txt += f" – Fehler: {st['error'][:60]}"
    return txt


def start_background(store, first_delay_s: int = 600) -> None:
    """Einmal starten. Prueft stuendlich, ob das Tages-Backup faellig ist."""
    global _started
    if _started:
        return
    _started = True

    def loop():
        time.sleep(first_delay_s)
        while True:
            try:
                run_once(store)
            except Exception as exc:
                logger.warning("[Backup] Fehler: %s", exc)
            time.sleep(CHECK_INTERVAL_S)
    threading.Thread(target=loop, daemon=True, name="offsite-backup").start()
