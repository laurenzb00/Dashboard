from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import threading
from typing import Dict


@dataclass
class SourceHealth:
    name: str
    last_ok: datetime | None = None
    last_error: datetime | None = None
    error_count: int = 0
    last_latency_ms: int | None = None
    last_error_msg: str | None = None


_LOCK = threading.Lock()
_HEALTH: Dict[str, SourceHealth] = {}


def update_source_health(name: str, ok: bool, latency_ms: int | None = None, error: str | None = None) -> None:
    now = datetime.now()
    with _LOCK:
        entry = _HEALTH.get(name)
        if entry is None:
            entry = SourceHealth(name=name)
            _HEALTH[name] = entry
        if ok:
            entry.last_ok = now
            if latency_ms is not None:
                entry.last_latency_ms = int(latency_ms)
        else:
            entry.last_error = now
            entry.error_count += 1
            entry.last_error_msg = error


def get_health_snapshot() -> Dict[str, SourceHealth]:
    with _LOCK:
        return {name: SourceHealth(**vars(entry)) for name, entry in _HEALTH.items()}
