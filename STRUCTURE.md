# Projektstruktur - Detaillierte Dokumentation

## Verzeichnis-Übersicht nach Reorganisierung

### src/ - Quellcode

#### src/main.py
Haupteinstiegspunkt der Anwendung. Startet die Tkinter-GUI, orchestriert alle Module und verbindet die verschiedenen Tabs mit der UI.

#### src/core/ - Geschäftslogik-Module
Kernfunktionalitäten und Datenintegration:

- **BMKDATEN.py**: BMK-API Integration für Heizungsdaten
  - Authentifizierung gegen BMK API
  - Datenabfrage für Heizungstemperaturen
  - Caching und Fehlerbehandlung

- **Wechselrichter.py**: Fronius Wechselrichter Integration
  - Energieerzeugung von der Solaranlage
  - Datenabfrage via HTTP
  - CSV-Export der Messwerte

- **datastore.py**: SQLite Datenbank Management
  - Schnelle Abfragen für große Datenmengen
  - Datenbank-Schema und Migrations-Logik
  - Aggregationen und Statistiken

- **ertrag_validator.py**: Ertrag-Validierung
  - Validierung der Ertragsdaten
  - Anomalie-Erkennung
  - Datenqualitätschecks

#### src/tabs/ - Dashboard-Reiter (Tabs)
Jeder Tab ist ein eigenständiges Modul für einen Bereich der Dashboard-UI:

- **analyse.py**: Übersichtsseite mit Dashboards und Kennzahlen
- **calendar.py**: Kalender-Integration (iCalendar)
- **ertrag.py**: Erzeugung & Ertrag Monitoring
- **historical.py**: Historische Datenanalyse
- **hue.py**: Philips Hue Lampen-Steuerung
- **spotify.py**: Spotify-Player Integration
- **system.py**: System- und Prozess-Monitoring
- **tado.py**: Tado Thermostat Steuerung

#### src/ui/ - Benutzeroberfläche
UI-Framework und Komponenten:

- **app.py**: Hauptanwendungs-Fenster
  - Tkinter Canvas & Frame Setup
  - Tab-Navigation
  - Event-Loop Management

- **styles.py**: Globales Styling
  - Farbschema (Color Palette)
  - Fonts und Icon-Emojis
  - Responsive Design

- **boiler_widget.py**: Spezial-Widget für Boiler-Status
- **modern_widgets.py**: Wiederverwendbare UI-Komponenten
- **energy_flow_widget.py**: Energiefluss-Visualisierung

- **components/**: Basis-UI-Komponenten
  - card.py: Card-Container
  - header.py: Header-Leiste
  - rounded.py: Gerundete Frames
  - rounded_button.py: Gerundete Buttons
  - statusbar.py: Status-Anzeige

- **views/**: Spezielle View-Komponenten
  - energy_flow.py: Detaillierte Energiefluss-View
  - buffer_storage.py: Pufferspeicher-Anzeige

### data/ - Datendateien

CSV-Messdaten und JSON-Konfigurationen:
- `ErtragHistory.csv`: Historische Erzeugungsdaten
- `FroniusDaten.csv`: Wechselrichter-Messwerte
- `Heizungstemperaturen.csv`: BMK-Heizungsdaten
- `ertrag_validation.json`: Validierungskonfiguration

### config/ - Konfiguration

- `bkmdaten.json`: BMK-API Anmeldedaten (Benutzername, Passwort)
- `Pufferspeicher.json`: Pufferspeicher-Parameter
- `logintado`: Tado-API Token und Gerätekonfiguration

**Wichtig**: Diese Dateien enthalten sensitive Daten und sollten **NICHT** in Git committed werden!

### resources/ - Ressourcen

- `icons/`: Icon-Bilder für die UI
  - Verschiedene PNG/SVG Icons für Buttons und Widgets

### _archive/ - Historische Dateien

Alte Versionen, Backups und Dokumentation:
- Alte Tab-Versionen (nicht mehr verwendet)
- Befestigungsdokumentationen
- Backup von CSV-Dateien
- Experimentelle Features

**Nicht in Hauptanwendung geladen!**

### Root-Level Wichtige Dateien

- `README.md`: Projektbeschreibung und Startanleitung
- `requirements.txt`: Python-Dependencies
- `start.sh`: Start-Skript für Linux/macOS
- `start.bat`: Start-Skript für Windows
- `.gitignore`: Git-Ignore Konfiguration
- `.venv/`: Python Virtual Environment (lokal)

## Module & Abhängigkeiten

### Import-Struktur

```
main.py (src/)
├── core.BMKDATEN
├── core.Wechselrichter
├── ui.app
│   ├── ui.styles
│   ├── ui.components.*
│   ├── ui.boiler_widget
│   ├── ui.modern_widgets
│   ├── core.datastore
│   ├── tabs.*
│   └── ui.views.*
└── ...tabs
```

## Dateinamenskonvention nach Reorganisierung

**Alte Struktur** → **Neue Struktur**

```
analyse_tab_modern.py → src/tabs/analyse.py
calendar_tab_modern.py → src/tabs/calendar.py
energy_flow_widget_v2.py → src/ui/energy_flow_widget.py
hue_tab_modern.py → src/tabs/hue.py
spotify_tab_modern.py → src/tabs/spotify.py
system_tab_modern.py → src/tabs/system.py
tado_tab_modern.py → src/tabs/tado.py
BMKDATEN.py → src/core/BMKDATEN.py
Wechselrichter.py → src/core/Wechselrichter.py
datastore.py → src/core/datastore.py
```

Alle alten Versionen wurden gelöscht, nur die modernen ("_modern") Versionen bleiben erhalten.

## Best Practices für Erweiterung

### Neuer Tab hinzufügen

1. Datei erstellen: `src/tabs/my_feature.py`
2. Eine Funktion definieren, die die Tab-UI aufbaut
3. In `src/ui/app.py` importieren und registrieren
4. Im Tab-Navigation-Menü hinzufügen

### Neue Komponente hinzufügen

1. In `src/ui/components/` oder `src/ui/views/` erstellen
2. Class/Function definieren
3. In der jeweiligen parent-Datei importieren

### Neues Core-Modul

1. In `src/core/` erstellen
2. Schnittstellen mit anderen Modulen definieren
3. In `src/main.py` oder benötigten Tabs importieren

## Performance & Optimierung

- **Datenbank**: SQLite für schnelle Abfragen, siehe `src/core/datastore.py`
- **Caching**: Implementiert für API-Aufrufe (BMK, Spotify)
- **Threading**: Für langsamkeiten API-Aufrufe

## Debugging & Testing

- Logs werden in `logs/` geschrieben
- Debug-Ausgaben in der Konsole
- Fehlerbehandlung mit try-except in kritischen Bereichen

Siehe `README.md` für weitere Informationen.
