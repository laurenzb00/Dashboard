import requests
import csv
from datetime import datetime
import time
import os

def abrufen_und_speichern():
    try:
        url = "http://192.168.1.202/solar_api/v1/GetPowerFlowRealtimeData.fcgi"
        response = requests.get(url)

        if response.status_code == 200:
            data = response.json()
            zeitstempel = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            pv_leistung = abs(data["Body"]["Data"]["Site"]["P_PV"] / 1000)
            netz_leistung = abs(data["Body"]["Data"]["Site"]["P_Grid"] / 1000)
            batterie_leistung = abs(data["Body"]["Data"]["Site"]["P_Akku"] / 1000)
            hausverbrauch = abs(data["Body"]["Data"]["Site"]["P_Load"] / 1000)
            batterieladestand = data["Body"]["Data"]["Inverters"]["1"]["SOC"]

            daten = {
                "Zeitstempel": zeitstempel,
                "PV-Leistung (kW)": pv_leistung,
                "Netz-Leistung (kW)": netz_leistung,
                "Batterie-Leistung (kW)": batterie_leistung,
                "Hausverbrauch (kW)": hausverbrauch,
                "Batterieladestand (%)": batterieladestand
            }

            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            csv_datei = os.path.join(base_dir, "data", "FroniusDaten.csv")
            datei_existiert = os.path.exists(csv_datei)

            os.makedirs(os.path.dirname(csv_datei), exist_ok=True)
            with open(csv_datei, "a", newline="", encoding="utf-8") as file:
                writer = csv.writer(file)
                if not datei_existiert or os.stat(csv_datei).st_size == 0:
                    writer.writerow(daten.keys())
                writer.writerow(daten.values())
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