"""Validiert und rekonstruiert PV-Ertrag direkt in SQLite."""

import json
import os
from datetime import datetime
from typing import List

from core.datastore import DataStore


WORKING_DIR = os.path.dirname(os.path.abspath(__file__))
ERTRAG_BACKUP_JSON = os.path.join(WORKING_DIR, "ertrag_history_backup.json")
ERTRAG_VALIDATION_LOG = os.path.join(WORKING_DIR, "ertrag_validation.json")


def load_current_ertrag(store: DataStore) -> List[dict]:
    cursor = store.conn.cursor()
    rows = cursor.execute(
        """
        SELECT date, daily_ertrag, total_ertrag
        FROM ertrag_history
        ORDER BY date ASC
        """
    ).fetchall()
    return [
        {
            "date": row[0],
            "daily_ertrag": float(row[1]) if row[1] is not None else 0.0,
            "total_ertrag": float(row[2]) if row[2] is not None else 0.0,
        }
        for row in rows
    ]


def backup_current_ertrag(current: List[dict]) -> None:
    if not current:
        return
    with open(ERTRAG_BACKUP_JSON, "w", encoding="utf-8") as handle:
        json.dump(current, handle, indent=2)


def reconstruct_ertrag_from_store(store: DataStore) -> List[dict]:
    daily_rows = store.get_daily_totals(days=None)
    cumulative = 0.0
    reconstructed: List[dict] = []
    for row in daily_rows:
        daily = float(row.get("pv_kwh") or 0.0)
        if daily <= 0:
            continue
        cumulative += daily
        reconstructed.append(
            {
                "date": row["day"],
                "daily_ertrag": daily,
                "total_ertrag": cumulative,
                "samples": int(row.get("samples") or 0),
            }
        )
    return reconstructed


def persist_ertrag_history(store: DataStore, rows: List[dict]) -> None:
    cursor = store.conn.cursor()
    cursor.execute("DELETE FROM ertrag_history")
    cursor.executemany(
        """
        INSERT INTO ertrag_history (date, total_ertrag, daily_ertrag)
        VALUES (?, ?, ?)
        """,
        [
            (row["date"], row["total_ertrag"], row["daily_ertrag"])
            for row in rows
        ],
    )
    store.conn.commit()


def get_fronius_stats(store: DataStore) -> dict:
    cursor = store.conn.cursor()
    count, start, end = cursor.execute(
        "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM fronius"
    ).fetchone()
    return {
        "count": count or 0,
        "start": start,
        "end": end,
    }


def validate_and_repair_ertrag() -> bool:
    """Rekonstruiert ErtragHistory direkt aus SQLite-Daten."""
    print("\n" + "=" * 60)
    print("ERTRAG-VALIDIERUNG GESTARTET")
    print("=" * 60)

    store = DataStore()
    try:
        stats = get_fronius_stats(store)
        if stats["count"] == 0:
            print("ERROR: Keine Fronius-Daten in der Datenbank gefunden!")
            return False

        current = load_current_ertrag(store)
        reconstructed = reconstruct_ertrag_from_store(store)

        print(
            f"\n✓ Fronius-Datensätze: {stats['count']} "
            f"({stats['start']} bis {stats['end']})"
        )
        print(f"✓ ErtragHistory (aktuell): {len(current)} Einträge")
        print(f"✓ ErtragHistory (neu): {len(reconstructed)} Einträge")

        current_total = sum(row["daily_ertrag"] for row in current)
        reconstructed_total = sum(row["daily_ertrag"] for row in reconstructed)
        print("\n→ Vergleich aktueller Bestand vs. Rekonstruktion")
        print(f"  Current Total:       {current_total:.2f} kWh")
        print(f"  Reconstructed Total: {reconstructed_total:.2f} kWh")

        diff_percent = 0.0
        if current_total > 0:
            diff_percent = abs(reconstructed_total - current_total) / current_total * 100
            print(f"  Differenz:           {diff_percent:.1f}%")

        backup_current_ertrag(current)
        if current:
            print(f"\n✓ Backup (JSON): {ERTRAG_BACKUP_JSON}")

        persist_ertrag_history(store, reconstructed)
        print("✓ ErtragHistory-Tabelle aktualisiert")

        report = {
            "timestamp": datetime.now().isoformat(),
            "fronius_entries": stats["count"],
            "fronius_range": stats,
            "current_entries": len(current),
            "current_total_kwh": float(current_total),
            "reconstructed_entries": len(reconstructed),
            "reconstructed_total_kwh": float(reconstructed_total),
            "difference_percent": float(diff_percent),
            "backup_created": ERTRAG_BACKUP_JSON if current else None,
        }

        with open(ERTRAG_VALIDATION_LOG, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
        print(f"✓ Validierungsbericht: {ERTRAG_VALIDATION_LOG}")

        print("\n" + "=" * 60)
        print("ERTRAG-VALIDIERUNG ABGESCHLOSSEN")
        print("=" * 60 + "\n")
        return True
    finally:
        store.close()


if __name__ == "__main__":
    validate_and_repair_ertrag()
