"""Time zone handling utilities.

This module provides consistent UTC-based time handling throughout
the application to avoid timezone-related bugs.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

T = TypeVar("T")


def ensure_utc(dt: datetime) -> datetime:
    """Convert a datetime to UTC-aware.
    
    Handles both naive datetimes (assumed UTC) and aware datetimes
    (converted to UTC).
    
    Args:
        dt: Datetime object to convert.
        
    Returns:
        UTC-aware datetime.
        
    Examples:
        >>> naive = datetime(2024, 1, 15, 12, 0, 0)
        >>> ensure_utc(naive).tzinfo
        datetime.timezone.utc
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    else:
        return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    """Get current UTC time as timezone-aware datetime.
    
    Returns:
        Current UTC time.
    """
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Datenbank-Zeitstempel
#
# Konvention: In der SQLite-DB stehen ALLE Zeitstempel als UTC im Format
# "YYYY-MM-DD HH:MM:SS" (ohne Zeitzonen-Suffix). Damit funktionieren
# String-Vergleiche (WHERE timestamp >= ?), ORDER BY und SQLites datetime()
# einheitlich. Umgerechnet in lokale Zeit wird erst fuer die Anzeige.
# ---------------------------------------------------------------------------

DB_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def parse_db_ts(value: object, naive_is_local: bool = False) -> datetime | None:
    """Parst einen Zeitstempel (String oder datetime) zu einem UTC-aware datetime.

    Naive Werte gelten als UTC (DB-Konvention), ausser naive_is_local=True
    (z.B. fuer alte CSV-Dateien, die lokale Zeit ohne Zeitzone enthalten).
    Gibt None zurueck, wenn der Wert leer oder nicht parsebar ist.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
    if dt.tzinfo is None:
        if naive_is_local:
            return dt.astimezone(timezone.utc)  # naive -> Systemzeitzone (DST-korrekt)
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_db_ts(value: object, naive_is_local: bool = False) -> str | None:
    """Normalisiert einen Zeitstempel auf das kanonische DB-Format (UTC)."""
    dt = parse_db_ts(value, naive_is_local=naive_is_local)
    if dt is None:
        return None
    return dt.strftime(DB_TS_FORMAT)


def db_ts_to_local(value: object) -> datetime | None:
    """DB-Zeitstempel -> naive *lokale* Zeit (fuer Charts/Anzeige und Vergleiche
    mit datetime.now()). Aware Eingaben werden korrekt umgerechnet."""
    dt = parse_db_ts(value)
    if dt is None:
        return None
    return dt.astimezone().replace(tzinfo=None)


def db_cutoff(hours: float | None = None, *, days: float | None = None) -> str | None:
    """Cutoff-String (jetzt minus Zeitraum) im DB-Format fuer WHERE timestamp >= ?."""
    if hours is None and days is None:
        return None
    delta = timedelta(hours=float(hours or 0.0), days=float(days or 0.0))
    return (datetime.now(timezone.utc) - delta).strftime(DB_TS_FORMAT)


def local_now_iso() -> str:
    """Aktuelle lokale Zeit als ISO-String MIT Offset (z.B. fuer Messwerte)."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def local_display(dt: datetime) -> str:
    """Format datetime for local display.
    
    Converts to local timezone and formats as YYYY-MM-DD HH:MM:SS.
    
    Args:
        dt: Datetime to format.
        
    Returns:
        Formatted string in local timezone.
    """
    return dt.astimezone().strftime('%Y-%m-%d %H:%M:%S')


def guard_alive(method: Callable[..., T]) -> Callable[..., T | None]:
    """Decorator to skip method calls when self.alive is False.
    
    Useful for UI callbacks that should not run after cleanup.
    
    Args:
        method: Method to wrap.
        
    Returns:
        Wrapped method that returns None if not alive.
    """
    def wrapper(self, *args, **kwargs) -> T | None:
        if getattr(self, 'alive', False):
            return method(self, *args, **kwargs)
        return None
    return wrapper
