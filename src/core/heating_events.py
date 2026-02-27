"""Heuristic detection of heating (Einheiz) events from temperature data."""

from __future__ import annotations

from datetime import datetime
from typing import Optional


def parse_iso_dt(value: str | None) -> datetime | None:
    """Parse ISO timestamp string, treating naive timestamps as local time."""
    if not value:
        return None
    try:
        raw = str(value).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
        return dt
    except Exception:
        return None


def compute_last_heating_event(rows: list[dict]) -> Optional[datetime]:
    """Detect the last 'Einheiz' (fire-start) event from recent heating rows.

    Parameters
    ----------
    rows : list[dict]
        Recent heating records with keys 'timestamp', 'kessel', and 'top'.
        Expected to cover at least the last 2-3 hours.

    Returns
    -------
    datetime or None
        Estimated start time of the last detected heating event.
    """
    # Pre-convert timestamps to Unix floats for faster comparison
    points: list[tuple[float, float, float, datetime]] = []
    for row in rows:
        dt = parse_iso_dt(row.get("timestamp"))
        if dt is None:
            continue
        kessel_val = row.get("kessel")
        buf_val = row.get("top")
        if kessel_val is None or buf_val is None:
            continue
        try:
            kessel = float(kessel_val)
            puffer = float(buf_val)
        except (ValueError, TypeError):
            continue
        points.append((dt.timestamp(), kessel, puffer, dt))

    if len(points) < 8:
        return None

    # Data is already ordered from DB, but sort to be safe
    points.sort(key=lambda x: x[0])

    now_ts = points[-1][0]
    window_start_ts = now_ts - 7200.0  # 2h in seconds
    # Keep only the last 2h window (plus 1 extra point before for slope checks).
    start_idx = 0
    for idx in range(len(points)):
        if points[idx][0] >= window_start_ts:
            start_idx = max(0, idx - 1)
            break
    points = points[start_idx:]

    if len(points) < 6:
        return None

    # Pre-compute thresholds
    KESSEL_RISE_STRONG_DEG = 10.0
    KESSEL_RISE_MIN_DEG = 8.0
    KESSEL_LOOKAHEAD_S = 3600.0  # 60 min
    PUFFER_RISE_MIN_DEG = 2.5
    PUFFER_LOOKAHEAD_S = 7200.0  # 120 min
    START_DELTA_EPS = 0.3
    START_CONFIRM_S = 900.0  # 15 min
    MAX_SINGLE_STEP_DEG = 4.0

    last_event: datetime | None = None

    for i in range(1, len(points) - 2):
        ts_i, k_i, p_i, dt_i = points[i]

        # Basic spike guard
        prev_ts, prev_k = points[i - 1][0], points[i - 1][1]
        if abs(k_i - prev_k) > MAX_SINGLE_STEP_DEG and (ts_i - prev_ts) < 1200.0:
            continue

        # 1) Confirm start-of-rise with a short confirmation window on Kessel.
        confirm_limit = ts_i + START_CONFIRM_S
        k_confirm_max = k_i
        for t in range(i + 1, len(points)):
            if points[t][0] > confirm_limit:
                break
            if points[t][1] > k_confirm_max:
                k_confirm_max = points[t][1]
        if (k_confirm_max - k_i) < START_DELTA_EPS:
            continue

        # 2) Look ahead for strong Kessel rise.
        k_limit = ts_i + KESSEL_LOOKAHEAD_S
        max_k = k_i
        for t in range(i + 1, len(points)):
            if points[t][0] > k_limit:
                break
            if points[t][1] > max_k:
                max_k = points[t][1]
        k_rise = max_k - k_i
        if k_rise < KESSEL_RISE_MIN_DEG:
            continue

        # 3) Optional Puffer rise (slow). Only require it for the weaker Kessel case.
        p_limit = ts_i + PUFFER_LOOKAHEAD_S
        max_p = p_i
        for t in range(i + 1, len(points)):
            if points[t][0] > p_limit:
                break
            if points[t][2] > max_p:
                max_p = points[t][2]
        p_rise = max_p - p_i

        if k_rise >= KESSEL_RISE_STRONG_DEG or p_rise >= PUFFER_RISE_MIN_DEG:
            last_event = dt_i

    return last_event
