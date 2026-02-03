"""
Ertrag-Validator: Überprüft und rekonstruiert ErtragHistory.csv aus FroniusDaten.csv
Rekonstruiert fehlende Einträge und validiert Konsistenz
"""
import os
import pandas as pd
from datetime import datetime, timedelta
import json

WORKING_DIR = os.path.dirname(os.path.abspath(__file__))
# Nach Reorganisierung: data/ Verzeichnis im Root
DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(WORKING_DIR)), "data")
FRONIUS_CSV = os.path.join(DATA_DIR, "FroniusDaten.csv")
ERTRAG_CSV = os.path.join(DATA_DIR, "ErtragHistory.csv")
ERTRAG_BACKUP = os.path.join(WORKING_DIR, "ErtragHistory_backup.csv")
ERTRAG_VALIDATION_LOG = os.path.join(WORKING_DIR, "ertrag_validation.json")


def load_data():
    """Lade Fronius und Ertrag Daten."""
    try:
        fronius = pd.read_csv(FRONIUS_CSV, parse_dates=["Zeitstempel"], dtype={"Zeitstempel": str}) if os.path.exists(FRONIUS_CSV) else pd.DataFrame()
        if not fronius.empty and "Zeitstempel" in fronius.columns:
            fronius["Zeitstempel"] = pd.to_datetime(fronius["Zeitstempel"], errors="coerce")
            fronius = fronius.dropna(subset=["Zeitstempel"])
    except Exception as e:
        print(f"[ERTRAG] Error loading Fronius: {e}")
        fronius = pd.DataFrame()
    
    try:
        ertrag = pd.read_csv(ERTRAG_CSV, parse_dates=["Zeitstempel"], dtype={"Zeitstempel": str}) if os.path.exists(ERTRAG_CSV) else pd.DataFrame()
        if not ertrag.empty and "Zeitstempel" in ertrag.columns:
            ertrag["Zeitstempel"] = pd.to_datetime(ertrag["Zeitstempel"], errors="coerce")
            ertrag = ertrag.dropna(subset=["Zeitstempel"])
    except Exception as e:
        print(f"[ERTRAG] Error loading Ertrag: {e}")
        ertrag = pd.DataFrame()
    
    if not fronius.empty:
        fronius = fronius.sort_values("Zeitstempel").drop_duplicates(subset=["Zeitstempel"], keep="first")
    if not ertrag.empty:
        ertrag = ertrag.sort_values("Zeitstempel").drop_duplicates(subset=["Zeitstempel"], keep="first")
    
    return fronius, ertrag


def reconstruct_ertrag_from_fronius(fronius_df: pd.DataFrame) -> pd.DataFrame:
    """
    Rekonstruiere ErtragHistory aus FroniusDaten durch tägliche Integration.
    
    Methode:
    - Gruppiere FroniusDaten nach Tag
    - Berechne PV-Energie pro Tag durch Trapez-Integration
    - Energy [kWh] = ∑(kW × Δt_hours) für alle Messwerte an diesem Tag
    
    Args:
        fronius_df: FroniusDaten DataFrame mit Spalten: Zeitstempel, PV-Leistung (kW)
    
    Returns:
        ErtragHistory DataFrame mit täglichen Werten
    """
    if fronius_df.empty:
        return pd.DataFrame(columns=["Zeitstempel", "Ertrag_kWh"])
    
    fronius_df = fronius_df.copy().sort_values("Zeitstempel").reset_index(drop=True)
    
    # Extrahiere Datum (ohne Zeit)
    fronius_df["Date"] = fronius_df["Zeitstempel"].dt.date
    
    ertrag_records = []
    
    # Iteriere über jeden Tag
    for date, day_group in fronius_df.groupby("Date"):
        day_group = day_group.sort_values("Zeitstempel").reset_index(drop=True)
        
        if day_group.empty:
            continue
        
        # Berechne Zeitdifferenzen zwischen Messwerten (in Stunden)
        time_diffs = day_group["Zeitstempel"].diff()
        time_diffs_hours = time_diffs.dt.total_seconds() / 3600
        time_diffs_hours.iloc[0] = 0  # Erste Differenz = 0
        
        # Berechne Energie: kW × Δt_h = kWh
        # Nutze Trapez-Integration für bessere Genauigkeit
        energy_kwh = 0.0
        for i in range(len(day_group) - 1):
            p1 = day_group["PV-Leistung (kW)"].iloc[i]
            p2 = day_group["PV-Leistung (kW)"].iloc[i + 1]
            dt = (day_group["Zeitstempel"].iloc[i + 1] - day_group["Zeitstempel"].iloc[i]).total_seconds() / 3600
            
            # Trapez-Regel: E = (P1 + P2) / 2 × Δt
            energy_kwh += (p1 + p2) / 2.0 * dt
        
        if energy_kwh > 0:
            ertrag_records.append({
                "Zeitstempel": pd.Timestamp(datetime.combine(date, datetime.min.time())),
                "Ertrag_kWh": energy_kwh
            })
    
    return pd.DataFrame(ertrag_records)


def validate_and_repair_ertrag():
    """
    Hauptvalidierungsfunktion:
    1. Lade aktuelle ErtragHistory
    2. Rekonstruiere aus FroniusDaten
    3. Vergleiche und repariere
    4. Speichere Backup und Validierungsbericht
    """
    print("\n" + "="*60)
    print("ERTRAG-VALIDIERUNG GESTARTET")
    print("="*60)
    
    fronius, ertrag_current = load_data()
    
    if fronius.empty:
        print("ERROR: Keine FroniusDaten gefunden!")
        return False
    
    print(f"\n✓ FroniusDaten geladen: {len(fronius)} Einträge ({fronius['Zeitstempel'].min()} bis {fronius['Zeitstempel'].max()})")
    print(f"✓ ErtragHistory geladen: {len(ertrag_current)} Einträge")
    
    # Rekonstruiere aus Fronius
    print("\n→ Rekonstruiere ErtragHistory aus FroniusDaten...")
    ertrag_reconstructed = reconstruct_ertrag_from_fronius(fronius)
    
    print(f"✓ Rekonstruiert: {len(ertrag_reconstructed)} Einträge")
    
    # Vergleiche
    print("\n→ Vergleiche Current vs. Reconstructed...")
    
    # Berechne Gesamtenergie
    current_total = ertrag_current["Ertrag_kWh"].sum() if not ertrag_current.empty else 0
    reconstructed_total = ertrag_reconstructed["Ertrag_kWh"].sum()
    
    print(f"  Current Total:       {current_total:.2f} kWh")
    print(f"  Reconstructed Total: {reconstructed_total:.2f} kWh")
    
    if current_total > 0:
        diff_percent = abs(reconstructed_total - current_total) / current_total * 100
        print(f"  Differenz:           {diff_percent:.1f}%")
    
    # Backup erstellen
    if not ertrag_current.empty:
        ertrag_current.to_csv(ERTRAG_BACKUP, index=False)
        print(f"\n✓ Backup erstellt: {ERTRAG_BACKUP}")
    
    # Verwende rekonstruierte Daten als Wahrheit
    ertrag_final = ertrag_reconstructed.copy()
    ertrag_final.to_csv(ERTRAG_CSV, index=False)
    print(f"✓ ErtragHistory aktualisiert: {ERTRAG_CSV}")
    
    # Schreibe Validierungsbericht
    report = {
        "timestamp": datetime.now().isoformat(),
        "fronius_entries": len(fronius),
        "fronius_range": {
            "start": fronius["Zeitstempel"].min().isoformat(),
            "end": fronius["Zeitstempel"].max().isoformat()
        },
        "current_entries": len(ertrag_current),
        "current_total_kwh": float(current_total),
        "reconstructed_entries": len(ertrag_reconstructed),
        "reconstructed_total_kwh": float(reconstructed_total),
        "difference_percent": float(diff_percent) if current_total > 0 else 0,
        "backup_created": ERTRAG_BACKUP if not ertrag_current.empty else None
    }
    
    with open(ERTRAG_VALIDATION_LOG, "w") as f:
        json.dump(report, f, indent=2)
    
    print(f"✓ Validierungsbericht: {ERTRAG_VALIDATION_LOG}")
    
    print("\n" + "="*60)
    print("ERTRAG-VALIDIERUNG ABGESCHLOSSEN")
    print("="*60 + "\n")
    
    return True


if __name__ == "__main__":
    validate_and_repair_ertrag()
