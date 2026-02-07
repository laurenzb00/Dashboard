from datetime import datetime, timezone

def ensure_utc(dt: datetime) -> datetime:
    """Konvertiert naive Datetimes zu UTC-aware, aware Datetimes zu UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    else:
        return dt.astimezone(timezone.utc)

def utc_now() -> datetime:
    return datetime.now(timezone.utc)

def local_display(dt: datetime) -> str:
    return dt.astimezone().strftime('%Y-%m-%d %H:%M:%S')

def guard_alive(method):
    def wrapper(self, *args, **kwargs):
        if getattr(self, 'alive', False):
            return method(self, *args, **kwargs)
    return wrapper
