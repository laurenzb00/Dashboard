"""Time zone handling utilities.

This module provides consistent UTC-based time handling throughout
the application to avoid timezone-related bugs.
"""

from __future__ import annotations

from datetime import datetime, timezone
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
