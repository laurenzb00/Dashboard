# Projekt Dashboard

Ein umfassendes Energie- und Smart-Home-Dashboard mit Tkinter-UI.

## Neue Struktur

```
src/
├── main.py              # Hauptanwendung (Einstiegspunkt)
├── core/                # Geschäftslogik-Module
│   ├── BMKDATEN.py      # BMK-API Integration
│   ├── Wechselrichter.py # Fronius Wechselrichter
│   ├── datastore.py     # SQLite Datenverwaltung
│   └── ertrag_validator.py # Ertrag-Validierung
├── tabs/                # Dashboard-Reiter
│   ├── analyse.py       # Analyse & Übersicht
│   ├── calendar.py      # Kalender-Integration
│   ├── ertrag.py        # Ertrag-Anzeige
│   ├── historical.py    # Historische Daten
│   ├── hue.py           # Philips Hue Steuerung
│   ├── spotify.py       # Spotify-Integration
│   ├── system.py        # System-Monitoring
│   └── tado.py          # Tado Thermostat
└── ui/                  # UI-Komponenten & Styling
    ├── app.py           # Haupt-App Klasse
    ├── styles.py        # Styling & Konfiguration
    ├── boiler_widget.py # Boiler-Widget
    ├── energy_flow_widget.py # Energiefluss-Visualisierung
    ├── modern_widgets.py # Moderne Widget-Komponenten
    ├── components/      # UI-Komponenten
    │   ├── card.py
    │   ├── header.py
    │   ├── rounded.py
    │   ├── rounded_button.py
    │   └── statusbar.py
    └── views/           # Spezielle Views
        ├── energy_flow.py
        └── buffer_storage.py

data/                   # Daten-Dateien
├── *.csv              # Messdaten (CSV)
├── ertrag_validation.json # Validierungsdaten
└── ...

config/                # Konfiguration
├── bkmdaten.json      # BMK-Anmeldedaten
├── Pufferspeicher.json # Pufferspeicher-Config
└── logintado          # Tado-Login

resources/             # Ressourcen
└── icons/             # Icon-Dateien

.venv/                 # Virtual Environment
_archive/              # Historische Dateien & Backups

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

### Code-Style
- Python 3.11+
- Type Hints verwenden
- Docstrings für Funktionen

### Neue Module hinzufügen
1. Datei in `src/tabs/` oder `src/core/` erstellen
2. In `src/main.py` importieren
3. Zu UI registrieren

## Bekannte Probleme & Lösungen

Siehe `_archive/` für historische Dokumentation und Fehlerbehebungen.

## Lizenz

Privates Projekt
