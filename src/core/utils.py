"""Zentrale Utility-Funktionen für das Dashboard.

Diese Modul enthält häufig verwendete Hilfsfunktionen, die in mehreren
Modulen benötigt werden. Dadurch werden Code-Duplikate vermieden.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


def safe_float(value: str | float | int | None) -> float | None:
    """Konvertiert einen Wert sicher zu float.
    
    Args:
        value: Ein Wert der zu float konvertiert werden soll
        
    Returns:
        float wenn Konvertierung erfolgreich, sonst None
        
    Examples:
        >>> safe_float("3.14")
        3.14
        >>> safe_float(None)
        None
        >>> safe_float("invalid")
        None
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_timestamp(ts: str | datetime | None) -> float:
    """Parst einen ISO-8601-Zeitstempel zu Unix-Timestamp.
    
    Args:
        ts: ISO-8601-String mit Zeitzone oder datetime-Objekt
        
    Returns:
        Unix-Timestamp als float, 0 bei Fehler
        
    Examples:
        >>> parse_timestamp("2024-01-15T10:30:00+00:00")
        1705314600.0
        >>> parse_timestamp(None)
        0.0
    """
    if not ts:
        return 0.0
    try:
        if isinstance(ts, datetime):
            return ts.timestamp()
        return datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0.0


def safe_fetchone(cursor: Any, default: Any = None) -> tuple | None:
    """Sicherer Zugriff auf fetchone() Ergebnis.
    
    Prevents AttributeError when fetchone() returns None.
    
    Args:
        cursor: Database cursor nach execute()
        default: Rückgabewert wenn kein Ergebnis
        
    Returns:
        Erste Zeile oder default wenn leer
        
    Examples:
        >>> row = safe_fetchone(cursor)
        >>> if row:
        ...     process(row)
    """
    result = cursor.fetchone()
    return result if result is not None else default


def safe_fetchone_value(cursor: Any, index: int = 0, default: Any = None) -> Any:
    """Sicherer Zugriff auf einzelnen Wert aus fetchone().
    
    Combines fetchone() with index access, handling None cases.
    
    Args:
        cursor: Database cursor nach execute()
        index: Spalten-Index (default: 0)
        default: Rückgabewert wenn kein Ergebnis
        
    Returns:
        Wert an Index oder default wenn leer/None
        
    Examples:
        >>> count = safe_fetchone_value(cursor, 0, default=0)
    """
    result = cursor.fetchone()
    if result is None:
        return default
    try:
        val = result[index]
        return val if val is not None else default
    except (IndexError, TypeError):
        return default
