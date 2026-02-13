import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def main() -> int:
    db_path = Path("src/core/data.db")
    print(f"DB exists: {db_path.exists()} path={db_path}")
    if not db_path.exists():
        return 2

    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()

    def max_ts(table: str) -> str | None:
        cur.execute(f"SELECT MAX(timestamp) FROM {table}")
        return cur.fetchone()[0]

    def total_rows(table: str) -> int:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        return int(cur.fetchone()[0])

    def count_since(table: str, cutoff: str) -> int:
        cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE datetime(timestamp) >= datetime(?)",
            (cutoff,),
        )
        return int(cur.fetchone()[0])

    def count_between(table: str, start: str, end: str) -> int:
        cur.execute(
            f"SELECT COUNT(*) FROM {table} WHERE datetime(timestamp) >= datetime(?) AND datetime(timestamp) <= datetime(?)",
            (start, end),
        )
        return int(cur.fetchone()[0])

    def last_timestamps(table: str, n: int = 5) -> list[str]:
        cur.execute(f"SELECT timestamp FROM {table} ORDER BY datetime(timestamp) DESC LIMIT ?", (n,))
        return [str(r[0]) for r in cur.fetchall()]

    for table in ("fronius", "heating"):
        mx = max_ts(table)
        mx_dt = _parse_ts(mx)
        print(f"\nTABLE {table}")
        print("total rows:", total_rows(table))
        print(f"max timestamp: {mx} parsed: {mx_dt}")
        print("last 5 timestamps:", last_timestamps(table, 5))
        if not mx_dt:
            continue

        now_utc = datetime.now(timezone.utc)
        now_cutoff = (now_utc - timedelta(hours=48)).strftime("%Y-%m-%d %H:%M:%S")
        n_now = count_since(table, now_cutoff)
        per_h_now = n_now / 48.0 if n_now else 0.0
        avg_sec_now = (3600.0 / per_h_now) if per_h_now else None
        print(
            "rows since now-48h:",
            n_now,
            "=> per hour:",
            round(per_h_now, 2),
            "avg every",
            (round(avg_sec_now, 1) if avg_sec_now else None),
            "sec",
        )

        # Also compute a "last 48h of available data" window anchored at the table's newest timestamp.
        end_dt = mx_dt
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        end_utc = end_dt.astimezone(timezone.utc)
        start_utc = end_utc - timedelta(hours=48)
        start = start_utc.strftime("%Y-%m-%d %H:%M:%S")
        end = end_utc.strftime("%Y-%m-%d %H:%M:%S")

        n_latest = count_between(table, start, end)
        per_h_latest = n_latest / 48.0 if n_latest else 0.0
        avg_sec_latest = (3600.0 / per_h_latest) if per_h_latest else None
        print(
            "rows in [max-48h, max]:",
            n_latest,
            "=> per hour:",
            round(per_h_latest, 2),
            "avg every",
            (round(avg_sec_latest, 1) if avg_sec_latest else None),
            "sec",
        )

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
