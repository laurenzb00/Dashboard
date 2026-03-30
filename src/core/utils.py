"""Zentrale Utility-Funktionen für das Dashboard.

Diese Modul enthält häufig verwendete Hilfsfunktionen, die in mehreren
Modulen benötigt werden. Dadurch werden Code-Duplikate vermieden.
"""

from datetime import datetime
from typing import Optional, Union


def safe_float(value: Union[str, float, int, None]) -> Optional[float]:
    """Konvertiert einen Wert sicher zu float.
    
    Args:
        value: Ein Wert der zu float konvertiert werden soll
        
    Returns:
        float wenn Konvertierung erfolgreich, sonst None
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def parse_timestamp(ts: Union[str, datetime, None]) -> float:
    """Parst einen ISO-8601-Zeitstempel zu Unix-Timestamp.
    
    Args:
        ts: ISO-8601-String mit Zeitzone oder datetime-Objekt
        
    Returns:
        Unix-Timestamp als float, 0 bei Fehler
    """
    if not ts:
        return 0.0
    try:
        if isinstance(ts, datetime):
            return ts.timestamp()
        return datetime.fromisoformat(str(ts)).timestamp()
    except (ValueError, TypeError, AttributeError):
        return 0.0


def safe_fetchone(cursor, default=None):
    """Sicherer Zugriff auf fetchone() Ergebnis.
    
    Args:
        cursor: Database cursor nach execute()
        default: Rückgabewert wenn kein Ergebnis
        
    Returns:
        Erste Zeile oder default wenn leer
    """
    result = cursor.fetchone()
    return result if result is not None else default


def safe_fetchone_value(cursor, index: int = 0, default=None):
    """Sicherer Zugriff auf einzelnen Wert aus fetchone().
    
    Args:
        cursor: Database cursor nach execute()
        index: Spalten-Index (default: 0)
        default: Rückgabewert wenn kein Ergebnis
        
    Returns:
        Wert an Index oder default wenn leer/None
    """
    result = cursor.fetchone()
    if result is None:
        return default
    try:
        val = result[index]
        return val if val is not None else default
    except (IndexError, TypeError):
        return default
