import requests
import csv
import os
from datetime import datetime
import json
import logging

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Mapping aller PP-Indizes zu ihren korrekten Namen
PP_INDEX_MAPPING = {
    0: "Betriebsmodus",              # z.B. "TEILLAST"
    1: "Kesseltemperatur",
    2: "Außentemperatur",
    3: "Wert_3",
    4: "Puffer_Oben",
    5: "Pufferspeicher_Mitte",
    6: "Puffer_Unten",
    7: "Wert_7",
    8: "Kesselrücklauf",
    9: "Rauchgastemperatur",        # Abgastemperatur des Kessels
    10: "Wert_10",                  # Unbekannt, Platzhalter
    11: "Rauchgasauslastung",       # in %
    12: "Warmwassertemperatur",
    13: "Wert_13",
    14: "Hysterese_14",             # -20.00 = typische Regelabweichung
    15: "Differenzial_15",          # 0 = Schwellwert
    16: "Hysterese_16",             # -20.00 = typische Regelabweichung
    17: "Differenzial_17",          # 0 = Schwellwert
    18: "Heizkreispumpe_EG",        # "EIN" = Erdgeschoss aktiv
    19: "Solltemperatur_EG",        # 60.00°C
    20: "Vorlauftemp_EG",           # 60.00°C
    21: "Raumtemp_EG",              # 55.39°C
    22: "Heizkreispumpe_OG",        # "EIN" = Obergeschoss aktiv
    23: "Solltemperatur_OG",        # 60.00°C
    24: "Vorlauftemp_OG",           # 65.32°C
    25: "Heizkreispumpe_DG",        # "EIN" = Dachgeschoss aktiv
    26: "Heizkreispumpe_Boiler",    # "AUS" = Boiler/Warmwasser
    27: "Hysterese_EG",             # -9.00 = Regelabweichung EG
    28: "Hysterese_OG",             # -9.00 = Regelabweichung OG
    29: "Hysterese_DG",             # -20.00
    30: "Reserve_Pumpe_Status",     # "AUS" = Reserve/nicht genutzt
    31: "Hysterese_31",             # -9.00 = Regelabweichung
    32: "Ruecklauftemp_Heizkreis",  # 64.00°C = gemischter Rücklauf
    33: "Kesselpumpe_Status",       # "AUS" = Kesselladepumpe
    34: "Mischer_EG_Elektronisch", # Elektronischer Mischer Erdgeschoss
    35: "Hysterese_35",             # -9.00 = Regelabweichung
    36: "Hysterese_36",             # -9.00 = Regelabweichung
    37: "Grenzwert_37",             # -20.00
    38: "Mischer_OG_Elektronisch", # Elektronischer Mischer Obergeschoss
    39: "Hysterese_39",             # -9.00 = Regelabweichung
    40: "Temperatur_Sensor_40",     # 64.00°C = zusätzlicher Sensor
    41: "Relais_41",                # "AUS" = Relais
    42: "Modus_Status",             # z.B. "Normal"
    43: "Brenner_Status",           # "HEIZEN" oder "AUS"
    44: "Brenner_Status_2",         # Duplikat
    45: "Wert_45",
    46: "Relais_10_Status",         # "AUS"
    47: "Relais_11_Status",         # "AUS"
    48: "Relais_12_Status",         # "AUS"
    49: "Relais_13_Status",         # "AUS"
    50: "Relais_14_Status",         # "AUS"
    51: "Relais_15_Status",         # "AUS"
    52: "Tick_Counter",             # großer Zahlenwert
    53: "Betriebsstunden",          # z.B. "32h"
    54: "Wert_54",
    55: "Wert_55",
    56: "Wert_56",
    57: "Wert_57",
    58: "Wert_58",
    59: "Wert_59",
    60: "Wert_60",
    61: "Wert_61",
    62: "Wert_62",
    63: "Wert_63",
    64: "Wert_64",
    65: "Wert_65",
    66: "Wert_66",
    67: "Wert_67",
    68: "Wert_68",
    69: "Wert_69",
    70: "Relais_16_Status",         # "AUS"
    71: "Relais_17_Status",         # "AUS"
    72: "Relais_18_Status",         # "AUS"
}

# Diese Funktion wird von main.py gesucht!
def abrufen_und_speichern():
    """
    Ruft alle Daten von der Heizungs-API ab und speichert sie.
    Unterstützt:
    - Heizungstemperaturen.csv (komplett mit allen Werten)
    - Pufferspeicher.json (strukturierte Pufferanlage-Daten)
    """
    try:
        url = "http://192.168.1.201/daqdata.cgi"
        # Timeout verhindert Hänger, wenn Heizung nicht antwortet
        response = requests.get(url, timeout=5)

        if response.status_code == 200:
            lines = response.text.split("\n")
            values = [line.strip() for line in lines if line.strip()]

            zeitstempel = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            
            logger.debug(f"BMK Response hat {len(values)} Werte")
            
            # Extrahiere ALLE verfügbaren Daten
            daten_heizung = _extrahiere_alle_daten(values, zeitstempel)

            def _get_field(name: str, *alts: str):
                if not daten_heizung:
                    return ""
                if name in daten_heizung:
                    return daten_heizung.get(name)
                for alt in alts:
                    if alt in daten_heizung:
                        return daten_heizung.get(alt)
                return ""

            # Kurz-Zuordnung basierend auf PP_INDEX_MAPPING
            daten_kurz = {
                "Zeitstempel": zeitstempel,
                "Kesseltemperatur": _get_field("Kesseltemperatur"),
                "Außentemperatur": _get_field("Außentemperatur"),
                "Pufferspeicher Oben": _get_field("Puffer_Oben", "Pufferspeicher Oben"),
                "Pufferspeicher Mitte": _get_field("Pufferspeicher_Mitte", "Pufferspeicher Mitte"),
                "Pufferspeicher Unten": _get_field("Puffer_Unten", "Pufferspeicher Unten"),
                "Warmwasser": _get_field("Warmwassertemperatur", "Warmwasser"),
            }
            
            # Speichere in Heizungstemperaturen.csv
            if daten_heizung:
                daten_heizung.update(daten_kurz)
                _speichere_heizungsdaten(daten_heizung)
            else:
                _speichere_heizungsdaten(daten_kurz)
            
            # Speichere Pufferanlage-Daten separat (strukturiert)
            daten_puffer = _extrahiere_pufferdaten(values, zeitstempel)
            if daten_puffer:
                _speichere_pufferdaten(daten_puffer)
                
    except Exception as e:
        logger.error(f"Fehler bei BMK: {e}")


def _extrahiere_alle_daten(values, zeitstempel):
    """
    Extrahiert ALLE verfügbaren Daten aus der PP-Antwort mit korrekter Zuordnung
    Nutzt PP_INDEX_MAPPING für korrekte Spaltennamen
    """
    if len(values) < 1:
        return None
    
    try:
        daten = {
            "Zeitstempel": zeitstempel,
        }
        
        # Durchlaufe ALLE verfügbaren Indizes mit korrektem Mapping
        for idx in range(min(len(values), len(PP_INDEX_MAPPING))):
            spalten_name = PP_INDEX_MAPPING.get(idx, f"Wert_{idx}")
            wert = values[idx].strip() if idx < len(values) else ""
            float_wert = _safe_float(wert)
            daten[spalten_name] = float_wert if float_wert is not None else wert
        return daten
    except Exception as e:
        logger.error(f"Fehler beim Extrahieren: {e}")
        return None


def _extrahiere_pufferdaten(values, zeitstempel):
    """
    Extrahiert strukturierte Pufferanlage-Daten
    """
    if len(values) < 7:
        return None
    
    try:
        # Berechne Durchschnittstemp und Stratifikation
        temp_oben = _safe_float(values[4]) if len(values) > 4 else None
        temp_mitte = _safe_float(values[5]) if len(values) > 5 else None
        temp_unten = _safe_float(values[6]) if len(values) > 6 else None
        
        temps = [temp_oben, temp_mitte, temp_unten]
        temps_valid = [t for t in temps if t is not None]
        
        if not temps_valid:
            return None
        
        puffer_data = {
            "Zeitstempel": zeitstempel,
            "Oben": temp_oben,
            "Mitte": temp_mitte,
            "Unten": temp_unten,
            "Durchschnitt": sum(temps_valid) / len(temps_valid) if temps_valid else None,
            "Stratifikation": temp_oben - temp_unten if (temp_oben and temp_unten) else None,
            "Status": _bestimme_puffer_status(temp_oben, temp_mitte, temp_unten)
        }
        
        return puffer_data
    except Exception as e:
        logger.error(f"Fehler bei Pufferextraktion: {e}")
        return None


def _bestimme_puffer_status(oben, mitte, unten):
    """
    Bestimmt den Betriebsstatus des Pufferspeichers
    """
    if not all([oben, mitte, unten]):
        return "FEHLER"
    
    temp_durchschnitt = (oben + mitte + unten) / 3
    
    if temp_durchschnitt > 70:
        return "GELADEN"
    elif temp_durchschnitt > 50:
        return "TEILGELADEN"
    elif temp_durchschnitt > 30:
        return "ENTLADEN"
    else:
        return "KALT"


def _safe_float(value):
    """
    Sicher zu float konvertieren
    """
    if not value or value == "":
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _speichere_heizungsdaten(daten):
    """
    Speichert Heizungsdaten in CSV (data/ Verzeichnis nach Reorganisierung)
    """
    # Nach Reorganisierung: data/ Verzeichnis im Root
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    csv_datei = os.path.join(base_dir, "data", "Heizungstemperaturen.csv")
    datei_existiert = os.path.exists(csv_datei)

    def _get(*keys):
        for key in keys:
            if key in daten:
                return daten.get(key)
        return ""

    try:
        with open(csv_datei, "a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if not datei_existiert:
                writer.writerow([
                    "Zeitstempel",
                    "Kesseltemperatur",
                    "Außentemperatur",
                    "Pufferspeicher Oben",
                    "Pufferspeicher Mitte",
                    "Pufferspeicher Unten",
                    "Warmwasser",
                ])
            aussen = _get("Außentemperatur", "Aussentemperatur")
            puffer_oben = _get("Puffer_Oben", "Pufferspeicher Oben", "Puffer Oben")
            writer.writerow([
                _get("Zeitstempel"),
                _get("Kesseltemperatur"),
                aussen,
                puffer_oben,
                _get("Pufferspeicher_Mitte", "Puffer_Mitte", "Pufferspeicher Mitte", "Puffer Mitte"),
                _get("Puffer_Unten", "Pufferspeicher Unten", "Puffer Unten"),
                _get("Warmwassertemperatur", "Warmwasser"),
            ])
        logger.debug(f"Heizungsdaten gespeichert: {daten.get('Zeitstempel')}")
    except Exception as e:
        logger.error(f"Fehler beim Speichern von Heizungsdaten: {e}")


def _speichere_pufferdaten(daten):
    """
    Speichert Pufferanlage-Daten in JSON (für strukturierte Abfragen)
    """
    json_datei = "Pufferspeicher.json"
    
    try:
        # Lade bestehende Daten
        puffer_history = []
        if os.path.exists(json_datei):
            try:
                with open(json_datei, "r", encoding="utf-8") as f:
                    puffer_history = json.load(f)
            except:
                puffer_history = []
        
        # Füge neue Daten hinzu
        puffer_history.append(daten)
        
        # Halte nur die letzten 1000 Einträge (für Performance)
        if len(puffer_history) > 1000:
            puffer_history = puffer_history[-1000:]
        
        # Speichere
        with open(json_datei, "w", encoding="utf-8") as f:
            json.dump(puffer_history, f, ensure_ascii=False, indent=2)
        
        logger.debug(f"Pufferdaten gespeichert: {daten.get('Zeitstempel')}")
    except Exception as e:
        logger.error(f"Fehler beim Speichern von Pufferdaten: {e}")


# Falls man es einzeln testen will
if __name__ == "__main__":
    abrufen_und_speichern()