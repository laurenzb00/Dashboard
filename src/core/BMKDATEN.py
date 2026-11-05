import json
import logging
import os
import time
from datetime import datetime
from typing import Dict, Optional

import pytz
import requests
from core import bmk_interpret
from core.datastore import get_shared_datastore
from core.utils import safe_float

logger = logging.getLogger(__name__)

# Reuse TCP connections across requests
_session = requests.Session()

# Load BMK config
_CONFIG_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "config", "bkmdaten.json"
)
_bmk_config: Dict = {}
try:
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        _bmk_config = json.load(f)
except Exception as e:
    logger.warning(f"Konnte BMK-Config nicht laden: {e}")

BMK_URL = _bmk_config.get("url", "http://192.168.1.201/daqdata.cgi")
BMK_TIMEOUT = _bmk_config.get("timeout_s", 5)


def _resilient_get(url, timeout):
    """GET with automatic session recovery on connection errors."""
    global _session
    try:
        return _session.get(url, timeout=timeout)
    except (requests.exceptions.ConnectionError, ConnectionResetError):
        _session = requests.Session()
        return _session.get(url, timeout=timeout)


# Fehler-Log Throttle: Nur alle 10 Minuten einen Timeout-Fehler loggen
_last_bmk_timeout_log = 0
_bmk_timeout_log_interval = 600  # Sekunden (10 Minuten)

PP_INDEX_MAPPING = {
    0: "Betriebsmodus",
    1: "Kesseltemperatur",
    2: "Außentemperatur",
    3: "Pufferladung_Prozent",       # linear zur mittleren Puffertemperatur (r = 0,9998)
    4: "Puffer_Oben",
    5: "Pufferspeicher_Mitte",
    6: "Puffer_Unten",
    7: "Pumpe_7",                    # 0/100, ~98 % in Teil-/Volllast
    # 8/9 waren als Kesselruecklauf/Rauchgas eingetragen - die Diagnose vom 06.10.2026 zeigt
    # etwas anderes (8 = 100 und 9 = Rampe 0..96 taeglich ~19 Uhr bei kaltem Kessel).
    # Rohwerte werden jetzt minuetlich in heating_bmk_raw gespeichert, um die echte
    # Rauchgas-Position beim naechsten Einheizen zu finden.
    8: "Saugzug_Prozent",
    9: "Primaerluft_Prozent",
    10: "Sekundaerluft_Prozent",
    11: "CO2_Abgas_Prozent",         # 18,07 = Leerwert im Standby
    12: "Warmwassertemperatur",
    13: "Wert_13",
    14: "Hysterese_14",
    15: "Differenzial_15",
    16: "Hysterese_16",
    17: "Differenzial_17",
    18: "Heizkreispumpe_EG",
    19: "Solltemperatur_EG",
    20: "Vorlauftemp_EG",
    21: "Vorlauftemp_EG_Ist",
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
    52: "Geraetenummer",
    53: "Service_Intervall",
    54: "Betriebsstunden",
    55: "Betriebstage",
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
    url = BMK_URL

    global _last_bmk_timeout_log
    try:
        response = _resilient_get(url, timeout=BMK_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as exc:
        # Nur alle 10 Minuten loggen, wenn Timeout
        if isinstance(exc, requests.exceptions.ConnectTimeout):
            now = time.time()
            if now - _last_bmk_timeout_log > _bmk_timeout_log_interval:
                logger.error(f"Fehler bei BMK HTTP-Request: {exc}")
                _last_bmk_timeout_log = now
        else:
            logger.error(f"Fehler bei BMK HTTP-Request: {exc}")
        return None

    try:
        values = [line.strip() for line in response.text.splitlines() if line.strip()]
        # Lokale Zeit (Europe/Vienna) als ISO-8601-String mit expliziter Zeitzone
        vienna = pytz.timezone("Europe/Vienna")
        now_local = datetime.now(vienna)
        zeitstempel = now_local.isoformat()
        # logger.debug(f"BMK Response hat {len(values)} Werte")

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
            result = daten_heizung
        else:
            result = daten_kurz

        _persist_to_datastore(result)
        _persist_raw(values, zeitstempel)
        try:
            deutung = _interpret(values, now_local, zeitstempel)
            if deutung:
                result["Deutung"] = deutung
        except Exception as exc:
            logger.debug("[BMK] Deutung fehlgeschlagen: %s", exc)

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
        values, meldung = bmk_interpret.normalize_fields(values)     # NACHLEGEN: 2 Zusatzfelder ab Index 52
        if meldung:
            daten["Meldung"] = meldung
        for idx in range(min(len(values), len(PP_INDEX_MAPPING))):
            spalten_name = PP_INDEX_MAPPING.get(idx, f"Wert_{idx}")
            wert = values[idx].strip() if idx < len(values) else ""
            float_wert = safe_float(wert)
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
        temp_oben = safe_float(values[4]) if len(values) > 4 else None
        temp_mitte = safe_float(values[5]) if len(values) > 5 else None
        temp_unten = safe_float(values[6]) if len(values) > 6 else None
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


_last_raw = 0.0
RAW_INTERVAL_S = 60
_last_status = 0.0
_live_preloaded = False


def _preload_live() -> None:
    """Nach einem Neustart den Trend aus den letzten Stunden der Datenbank aufbauen."""
    global _live_preloaded
    _live_preloaded = True
    try:
        rows = get_shared_datastore().get_bmk_status(hours=4)
        bmk_interpret.live().preload(bmk_interpret.reading_from_row(r["ts"], r) for r in rows)
    except Exception as exc:
        logger.debug("[BMK] Verlauf fuer Deutung nicht geladen: %s", exc)


def _interpret(values, now_local, zeitstempel) -> Optional[dict]:
    """Rohwerte -> Aussage (Phase, Nachlegen, Platz im Puffer ...) und dauerhaft speichern (1x/min)."""
    global _last_status
    reading = bmk_interpret.parse_bmk(values, now_local.replace(tzinfo=None))
    if reading is None:
        return None
    if not _live_preloaded:
        _preload_live()
    deutung = bmk_interpret.live().interpret(reading).to_dict()
    now = time.time()
    if now - _last_status >= RAW_INTERVAL_S:
        _last_status = now
        try:
            get_shared_datastore().insert_bmk_status(zeitstempel, bmk_interpret.status_row(reading))
        except Exception as exc:
            logger.debug("[DB] BMK-Status nicht gespeichert: %s", exc)
    return deutung


def _persist_raw(values, zeitstempel) -> None:
    """Alle BMK-Rohwerte einmal pro Minute sichern (zum Zuordnen unbekannter Werte)."""
    global _last_raw
    now = time.time()
    if now - _last_raw < RAW_INTERVAL_S:
        return
    _last_raw = now
    try:
        get_shared_datastore().insert_bmk_raw(zeitstempel, list(values))
    except Exception as exc:
        logger.debug("[DB] BMK-Rohwerte nicht gespeichert: %s", exc)


def _persist_to_datastore(payload: Dict[str, float]) -> None:
    try:
        store = get_shared_datastore()
        logger.info("[DB] Speichere Heizungsdaten: ts=%s kessel=%s puffer_top=%s", 
                    payload.get('Zeitstempel'), 
                    payload.get('Kesseltemperatur'),
                    payload.get('Pufferspeicher Oben') or payload.get('Puffer_Oben'))
        store.insert_heating_record(payload)
        logger.info("[DB] Heizungseintrag erfolgreich")
    except Exception as exc:
        logger.error(f"[DB] Heizungseintrag fehlgeschlagen: {exc}")


if __name__ == "__main__":
    abrufen_und_speichern()
