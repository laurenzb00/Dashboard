from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import socket
import threading
import time
from typing import Dict
from urllib.parse import urlsplit


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


def check_tcp_reachable(host: str, port: int, timeout: float = 3.0) -> tuple[bool, int | None]:
    """Einfacher TCP-Erreichbarkeitstest ("anpingen").

    Ein echtes ICMP-Ping braucht auf den meisten Systemen Root-Rechte und ist
    ausserdem in Heimnetzen haeufig durch die Firewall des Geraets blockiert.
    Ein TCP-Connect-Versuch auf den tatsaechlich genutzten Port beantwortet
    die praktisch relevante Frage ("ist das Geraet im Netzwerk erreichbar")
    genauso gut und funktioniert ohne besondere Rechte. Gibt (erreichbar,
    latenz_ms) zurueck; latenz_ms ist None, wenn nicht erreichbar.
    """
    if not host:
        return False, None
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        return True, int((time.perf_counter() - start) * 1000)
    except Exception:
        return False, None


def host_port_from_url(url: str, default_port: int = 80) -> tuple[str, int]:
    """Extrahiert Host/Port aus einer URL fuer check_tcp_reachable()."""
    parts = urlsplit(url)
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else default_port
    return parts.hostname or "", port
