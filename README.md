# Projekt Dashboard

Ein umfassendes Energie- und Smart-Home-Dashboard mit Tkinter-UI.

## Struktur

```
src/
├── main.py                 # Einstiegspunkt
├── spotifylogin.py         # Spotify-OAuth (vom Spotify-Tab genutzt)
├── core/                   # Datenquellen, Datenbank, Auswertungen
│   ├── Wechselrichter.py   # Fronius (PV, Netz, Akku)
│   ├── BMKDATEN.py         # BMK-Heizung (Kessel, Puffer, Warmwasser)
│   ├── homeassistant.py    # Home Assistant
│   ├── weather.py          # Open-Meteo: Kurzvorhersage + stuendliche Temperatur
│   ├── datastore.py        # SQLite (Zeitstempel in UTC)
│   ├── time_utils.py       # Zeitzonen-Hilfen
│   ├── pv_forecast.py      # PV-Prognose (selbstkalibrierend)
│   ├── energy_day.py       # Tagesbilanz PV/Verbrauch/Akku
│   ├── heating_stats.py    # Einheizen, Waermeeintrag Holz/Solar
│   ├── heat_demand.py      # Waermebedarf abhaengig von der Aussentemperatur
│   ├── heating_forecast.py # Einheiz-Empfehlung
│   └── ...                 # health, heating_events, ertrag_validator, schema, utils
├── tabs/                   # Ein Modul pro Tab
│   ├── ertrag.py           # Ertrag (Tag/Zeitraum, Autarkie, Prognose)
│   ├── waerme.py           # Waerme (Puffer live, Empfehlung, Heute/Saison/Woche)
│   ├── historical.py       # Heizung (Temperaturverlaeufe)
│   └── ...                 # status, tado, hue, spotify, calendar, healthcheck, ...
└── ui/                     # App-Rahmen, Komponenten, Views
    ├── app.py
    ├── components/
    └── views/

config/     # Zugangsdaten/Einstellungen (*.example.json als Vorlage)
data/       # Caches (Prognosen, Saison-Statistik), werden automatisch erzeugt
resources/  # Icons
tests/      # Unit-Tests: python -m pytest tests
```

## Installation

### 1. Virtual Environment

```bash
python -m venv .venv
.\.venv\Scripts\activate  # Windows
source .venv/bin/activate # Linux/macOS
```

### 2. Dependencies

```bash
pip install -r requirements.txt
```

### 3. Auf Raspberry Pi zusätzlich:

```bash
sudo apt-get install -y fonts-noto-color-emoji
```

## Verwendung

```bash
# Hauptanwendung starten
python src/main.py

# Oder mit dem Start-Skript
bash start.sh  # Linux/macOS
start.sh       # Windows
```

### Datenbankpfad

Standardmaessig verwendet das Dashboard `src/core/data.db`. Wenn die aktuelle
Datenbank an einem anderen Ort liegt, kann der Pfad vor dem Start gesetzt werden:

```bat
set DASHBOARD_DB_PATH=D:\Pfad\zur\aktuellen\data.db
python src\main.py
```

## Integrationen

- **Energie**: Fronius Wechselrichter, BMK API
- **Musik**: Spotify-Integration
- **Smart Home**: Philips Hue, Tado Thermostat
- **Kalender**: iCalendar-Integration
- **Monitoring**: Systemressourcen & Heizung

## Datenbankschema

Die App verwendet SQLite für schnelle Abfragen:
- `energy`: Energiemesswerte
- `heating`: Heizungstemperaturen
- `system`: Systemmetriken
- `ertrag`: Ertragsdaten

## Entwicklung

### Home Assistant: Automationen & Skripte starten

In `config/homeassistant.json` kannst du optional `actions` definieren, die im Tab "HomeA" als Buttons erscheinen.

Beispiel:

```json
{
    "actions": [
        {"label": "Guten Morgen", "service": "automation.trigger", "data": {"entity_id": "automation.guten_morgen"}},
        {"label": "Staubsauger", "service": "script.turn_on", "data": {"entity_id": "script.start_vacuum"}}
    ]
}
```

### Code-Style
- Python 3.11+
- Type Hints verwenden
- Docstrings für Funktionen

### Neue Module hinzufügen
1. Datei in `src/tabs/` oder `src/core/` erstellen
2. In `src/main.py` importieren
3. Zu UI registrieren

## Bekannte Probleme & Lösungen

Fehler landen im Log (`datenerfassung.log`); der Health-Tab zeigt den Zustand der Datenquellen.

## Lizenz

Privates Projekt
