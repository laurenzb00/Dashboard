import requests
import logging
from datetime import datetime
import time

from core.datastore import get_shared_datastore

def abrufen_und_speichern():
    url = "http://192.168.1.202/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
    # Throttle for timeout and warning logs
    if not hasattr(abrufen_und_speichern, "_last_timeout_log"):
        abrufen_und_speichern._last_timeout_log = 0
    if not hasattr(abrufen_und_speichern, "_last_warning_log"):
        abrufen_und_speichern._last_warning_log = 0
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            data = response.json()
            zeitstempel = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pv_leistung = data["Body"]["Data"]["Site"]["P_PV"] / 1000
            netz_leistung = data["Body"]["Data"]["Site"]["P_Grid"] / 1000
            batterie_leistung = data["Body"]["Data"]["Site"]["P_Akku"] / 1000
            hausverbrauch = data["Body"]["Data"]["Site"]["P_Load"] / 1000
            batterieladestand = data["Body"]["Data"]["Inverters"]["1"]["SOC"]

            daten = {
                "Zeitstempel": zeitstempel,
                "PV-Leistung (kW)": pv_leistung,
                "Netz-Leistung (kW)": netz_leistung,
                "Batterie-Leistung (kW)": batterie_leistung,
                "Hausverbrauch (kW)": hausverbrauch,
                "Batterieladestand (%)": batterieladestand
            }
            try:
                store = get_shared_datastore()
                store.insert_fronius_record(daten)
            except Exception:
                logging.exception("Fehler beim Speichern der Fronius-Daten")
            return daten
        return None
    except requests.exceptions.Timeout:
        now = time.time()
        # Only log timeout once every 60 seconds
        if now - abrufen_und_speichern._last_timeout_log > 60:
            logging.warning("Wechselrichter Timeout beim Abruf (requests.get)")
            abrufen_und_speichern._last_timeout_log = now
        return None
    except requests.exceptions.RequestException as e:
        now = time.time()
        # Only log other request warnings once every 60 seconds
        if now - abrufen_und_speichern._last_warning_log > 60:
            logging.warning(f"Wechselrichter RequestException: {e}")
            abrufen_und_speichern._last_warning_log = now
        return None
    except Exception:
        logging.exception("Unerwarteter Fehler in abrufen_und_speichern")
        return None
def run():
    while True:
        abrufen_und_speichern()
        time.sleep(60)

# Nur ausführen, wenn die Datei direkt gestartet wird
if __name__ == "__main__":
    run()