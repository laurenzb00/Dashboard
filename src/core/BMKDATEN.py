import csv
import logging
import os
from datetime import datetime
from typing import Dict, Optional

import requests

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

PP_INDEX_MAPPING = {
    0: "Betriebsmodus",
    1: "Kesseltemperatur",
    2: "Außentemperatur",
    3: "Wert_3",
    4: "Puffer_Oben",
    5: "Pufferspeicher_Mitte",
    6: "Puffer_Unten",
    7: "Wert_7",
    8: "Kesselrücklauf",
    9: "Rauchgastemperatur",
    10: "Wert_10",
    11: "Rauchgasauslastung",
    12: "Warmwassertemperatur",
    13: "Wert_13",
    14: "Hysterese_14",
    15: "Differenzial_15",
    16: "Hysterese_16",
    17: "Differenzial_17",
    18: "Heizkreispumpe_EG",
    19: "Solltemperatur_EG",
    20: "Vorlauftemp_EG",
    21: "Raumtemp_EG",
    22: "Heizkreispumpe_OG",
    23: "Solltemperatur_OG",
    24: "Vorlauftemp_OG",
    25: "Heizkreispumpe_DG",
    26: "Heizkreispumpe_Boiler",
    27: "Hysterese_EG",
    28: "Hysterese_OG",
    29: "Hysterese_DG",
    30: "Reserve_Pumpe_Status",
    31: "Hysterese_31",
    32: "Ruecklauftemp_Heizkreis",
    33: "Kesselpumpe_Status",
    34: "Mischer_EG_Elektronisch",
    35: "Hysterese_35",
    36: "Hysterese_36",
    37: "Grenzwert_37",
    38: "Mischer_OG_Elektronisch",
    39: "Hysterese_39",
    40: "Temperatur_Sensor_40",
    41: "Relais_41",
    42: "Modus_Status",
    43: "Brenner_Status",
    44: "Brenner_Status_2",
    45: "Wert_45",
    46: "Relais_10_Status",
    47: "Relais_11_Status",
    48: "Relais_12_Status",
    49: "Relais_13_Status",
    50: "Relais_14_Status",
    51: "Relais_15_Status",
    52: "Tick_Counter",
    53: "Betriebsstunden",
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
    70: "Relais_16_Status",
    71: "Relais_17_Status",
    72: "Relais_18_Status",
}


def abrufen_und_speichern() -> Optional[Dict[str, float]]:
    """Ruft Daten von der Heizungs-API ab und legt sie in der CSV ab."""
    url = "http://192.168.1.201/daqdata.cgi"

    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error(f"Fehler bei BMK HTTP-Request: {exc}")
        return None

    try:
        values = [line.strip() for line in response.text.splitlines() if line.strip()]
        zeitstempel = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.debug(f"BMK Response hat {len(values)} Werte")

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

        daten_kurz = {
            "Zeitstempel": zeitstempel,
            "Kesseltemperatur": _get_field("Kesseltemperatur"),
            "Außentemperatur": _get_field("Außentemperatur"),
            "Pufferspeicher Oben": _get_field("Puffer_Oben", "Pufferspeicher Oben"),
            "Pufferspeicher Mitte": _get_field("Pufferspeicher_Mitte", "Pufferspeicher Mitte"),
            "Pufferspeicher Unten": _get_field("Puffer_Unten", "Pufferspeicher Unten"),
            "Warmwasser": _get_field("Warmwassertemperatur", "Warmwasser"),
        }

        if daten_heizung:
            daten_heizung.update(daten_kurz)
            _speichere_heizungsdaten(daten_heizung)
            result = daten_heizung
        else:
            _speichere_heizungsdaten(daten_kurz)
            result = daten_kurz

        # Optional: weiterverwenden, nicht mehr speichern um alte Abhängigkeiten zu vermeiden
        _extrahiere_pufferdaten(values, zeitstempel)
        return result
    except Exception as exc:
        logger.error(f"Fehler bei BMK Verarbeitung: {exc}")
        return None


def _extrahiere_alle_daten(values, zeitstempel):
    """Erstellt ein Dict mit allen bekannten PP-Indizes."""
    if not values:
        return None

    try:
        daten = {"Zeitstempel": zeitstempel}
        for idx in range(min(len(values), len(PP_INDEX_MAPPING))):
            spalten_name = PP_INDEX_MAPPING.get(idx, f"Wert_{idx}")
            wert = values[idx].strip() if idx < len(values) else ""
            float_wert = _safe_float(wert)
            daten[spalten_name] = float_wert if float_wert is not None else wert
        return daten
    except Exception as exc:
        logger.error(f"Fehler beim Extrahieren: {exc}")
        return None


def _extrahiere_pufferdaten(values, zeitstempel):
    """Berechnet optionale Kenngrößen des Pufferspeichers."""
    if len(values) < 7:
        return None

    try:
        temp_oben = _safe_float(values[4]) if len(values) > 4 else None
        temp_mitte = _safe_float(values[5]) if len(values) > 5 else None
        temp_unten = _safe_float(values[6]) if len(values) > 6 else None
        temps_valid = [t for t in (temp_oben, temp_mitte, temp_unten) if t is not None]
        if not temps_valid:
            return None

        return {
            "Zeitstempel": zeitstempel,
            "Oben": temp_oben,
            "Mitte": temp_mitte,
            "Unten": temp_unten,
            "Durchschnitt": sum(temps_valid) / len(temps_valid),
            "Stratifikation": temp_oben - temp_unten if (temp_oben is not None and temp_unten is not None) else None,
            "Status": _bestimme_puffer_status(temp_oben, temp_mitte, temp_unten),
        }
    except Exception as exc:
        logger.error(f"Fehler bei Pufferextraktion: {exc}")
        return None


def _bestimme_puffer_status(oben, mitte, unten):
    if not all(value is not None for value in (oben, mitte, unten)):
        return "FEHLER"

    temp_durchschnitt = (oben + mitte + unten) / 3
    if temp_durchschnitt > 70:
        return "GELADEN"
    if temp_durchschnitt > 50:
        return "TEILGELADEN"
    if temp_durchschnitt > 30:
        return "ENTLADEN"
    return "KALT"


def _safe_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _speichere_heizungsdaten(daten):
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
            writer.writerow([
                _get("Zeitstempel"),
                _get("Kesseltemperatur"),
                _get("Außentemperatur", "Aussentemperatur"),
                _get("Puffer_Oben", "Pufferspeicher Oben", "Puffer Oben"),
                _get("Pufferspeicher_Mitte", "Puffer_Mitte", "Pufferspeicher Mitte", "Puffer Mitte"),
                _get("Puffer_Unten", "Pufferspeicher Unten", "Puffer Unten"),
                _get("Warmwassertemperatur", "Warmwasser"),
            ])
        logger.debug(f"Heizungsdaten gespeichert: {daten.get('Zeitstempel')}")
    except Exception as exc:
        logger.error(f"Fehler beim Speichern von Heizungsdaten: {exc}")


if __name__ == "__main__":
    abrufen_und_speichern()
            puffer_unten = _get("Puffer_Unten", "Pufferspeicher Unten", "Puffer Unten")
            warmwasser = _get("Warmwassertemperatur", "Warmwasser")
            writer.writerow([
                daten.get("Zeitstempel", ""),
                _get("Kesseltemperatur"),
                aussen,
                puffer_oben,
                puffer_mitte,
                puffer_unten,
                warmwasser,
            ])
    except Exception as exc:
        logger.error(f"Fehler beim Schreiben der Heizungsdaten: {exc}")