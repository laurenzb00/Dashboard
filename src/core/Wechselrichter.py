import requests
from datetime import datetime
import time

from core.datastore import get_shared_datastore

def abrufen_und_speichern():
    try:
        url = "http://192.168.1.202/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
        response = requests.get(url)

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
                pass
            return daten
        return None
    except Exception:
        return None
def run():
    while True:
        abrufen_und_speichern()
        time.sleep(60)

# Nur ausführen, wenn die Datei direkt gestartet wird
if __name__ == "__main__":
    run()