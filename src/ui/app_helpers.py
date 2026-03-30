"""Helper functions for the main app module.

Contains static utility functions for timestamp parsing, age formatting,
and other common operations used throughout the app.
"""

from datetime import datetime, timezone
from typing import Any


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string into a datetime object.
    
    Handles both naive timestamps (treated as local time) and
    timezone-aware timestamps. Supports 'Z' suffix for UTC.
    
    Args:
        value: ISO-8601 timestamp string, or None.
        
    Returns:
        Parsed datetime object, or None if parsing fails.
        
    Examples:
        >>> parse_iso_datetime("2024-01-15T10:30:00Z")
        datetime.datetime(2024, 1, 15, 10, 30, tzinfo=datetime.timezone.utc)
        >>> parse_iso_datetime(None)
        None
    """
    if not value:
        return None
    try:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            # Treat naive timestamps as local time
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt
    except Exception:
        return None


def parse_timestamp_value(value: Any) -> datetime | None:
    """Parse a timestamp value (string or datetime) into a datetime.
    
    More lenient than parse_iso_datetime - accepts datetime objects directly.
    
    Args:
        value: A datetime object or ISO-8601 string.
        
    Returns:
        Datetime object, or None if parsing fails.
    """
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except Exception:
        return None


def parse_timestamp_as_epoch(ts: Any) -> float:
    """Parse a timestamp and return epoch seconds.
    
    Args:
        ts: Timestamp as ISO-8601 string or datetime.
        
    Returns:
        Epoch timestamp (seconds since 1970), or 0 if parsing fails.
    """
    if not ts:
        return 0
    try:
        return datetime.fromisoformat(str(ts)).timestamp()
    except Exception:
        return 0


def format_age_short(seconds: float | None) -> str:
    """Format an age in seconds into a short human-readable string.
    
    Progressively uses larger units (s, m, h, d) as appropriate.
    
    Args:
        seconds: Age in seconds, or None.
        
    Returns:
        Formatted string like "30s", "5m", "2h 30m", "3d 12h", or "--" for None.
        
    Examples:
        >>> format_age_short(30)
        '30s'
        >>> format_age_short(125)
        '2m'
        >>> format_age_short(3700)
        '1h 01m'
    """
    if seconds is None:
        return "--"
    try:
        total = max(0, int(seconds))
    except Exception:
        return "--"
    if total < 60:
        return f"{total}s"
    minutes, _ = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes:02d}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours:02d}h"


def format_age_compact(seconds: float | int) -> str:
    """Format age compactly (single unit only).
    
    Args:
        seconds: Age in seconds.
        
    Returns:
        Compact string like "30s", "5m", or "2h".
    """
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def age_seconds(ts: datetime | None) -> float | None:
    """Calculate the age of a timestamp in seconds.
    
    Handles both timezone-aware and naive datetimes correctly.
    
    Args:
        ts: Timestamp to calculate age from.
        
    Returns:
        Age in seconds, or None if ts is None.
    """
    if ts is None:
        return None
    try:
        if ts.tzinfo is not None and ts.tzinfo.utcoffset(ts) is not None:
            now = datetime.now(ts.tzinfo)
        else:
            now = datetime.now()
        return (now - ts).total_seconds()
    except Exception:
        return None


def format_bmk_mode(value: Any) -> str | None:
    """Format a BMK heating system mode value.
    
    Normalizes various input types (bool, int, float, str) to a
    consistent string representation.
    
    Args:
        value: The mode value to format.
        
    Returns:
        Formatted string representation, or None if value is None/empty.
        
    Examples:
        >>> format_bmk_mode(True)
        '1'
        >>> format_bmk_mode(2.0)
        '2'
        >>> format_bmk_mode("Auto")
        'Auto'
    """
    if value is None:
        return None
    try:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            # Avoid "1.0" noise for enum-like values
            iv = int(value)
            return str(iv)
        s = str(value).strip()
        return s if s else None
    except Exception:
        return None


def compose_status_text(parts: list[str], max_len: int = 140, separator: str = " • ") -> str:
    """Compose a status text from parts, respecting max length.
    
    Drops least important parts (from the end) if the combined text
    would exceed max_len.
    
    Args:
        parts: List of status text parts.
        max_len: Maximum length of the combined string.
        separator: Separator between parts.
        
    Returns:
        Combined status text, truncated if necessary.
    """
    # Filter empty parts
    parts = [p.strip() for p in parts if p and p.strip()]
    
    def joined(p: list[str]) -> str:
        return separator.join(p)
    
    # Try full text
    if len(joined(parts)) <= max_len:
        return joined(parts)
    
    # Drop parts from the end until it fits
    while parts and len(joined(parts)) > max_len:
        parts = parts[:-1]
    
    result = joined(parts)
    if len(result) <= max_len:
        return result
    
    # Last resort: hard cut (rare)
    if max_len > 1:
        return result[: max_len - 1] + "…"
    return "…"
