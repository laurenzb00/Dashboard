#!/bin/bash
# Installationsskript für das Dashboard-Projekt

# Systempakete installieren

echo "[INSTALL] Systempakete werden installiert..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-tk git fonts-noto-color-emoji libatlas-base-dev libjpeg-dev libfreetype6-dev libpng-dev libopenjp2-7 libtiff5 ttf-dejavu python3-venv

echo "[INSTALL] Systempakete installiert."


# Python venv erstellen und aktivieren

# Python venv erstellen und aktivieren
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

# Python-Libraries installieren (aus requirements.txt, falls vorhanden)

if [ -f requirements.txt ]; then
    echo "[INSTALL] Python-Abhängigkeiten werden installiert..."
    pip install --upgrade pip
    pip install -r requirements.txt
    # Zusätzliche Abhängigkeiten, falls requirements.txt unvollständig ist
    pip install pytado python-tado spotipy flask phue icalendar recurring-ical-events pytz numpy pandas matplotlib plotly kaleido psutil ttkbootstrap pillow requests
    echo "[INSTALL] Python-Abhängigkeiten installiert."
else
    echo "requirements.txt nicht gefunden!"
fi

# Optional: Desktop-Shortcut anlegen (für Raspberry Pi Desktop)
if [ -d ~/Desktop ]; then
    cat <<EOF > ~/Desktop/Dashboard.desktop
[Desktop Entry]
Type=Application
Name=Dashboard
Exec=python3 $(pwd)/src/main.py
Icon=utilities-terminal
Terminal=true
EOF
    chmod +x ~/Desktop/Dashboard.desktop
fi

mkdir -p ~/.local/bin
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

cat <<EOF > ~/.local/bin/dbpull
#!/bin/bash
cd "$PROJECT_DIR"
python3 src/core/datastore.py --pull
EOF
chmod +x ~/.local/bin/dbpull

cat <<EOF > ~/.local/bin/dbstart
#!/bin/bash
cd "$PROJECT_DIR"
python3 src/main.py
EOF
chmod +x ~/.local/bin/dbstart

# ~/.local/bin zum PATH hinzufügen, falls nicht vorhanden
if ! echo $PATH | grep -q "$HOME/.local/bin"; then
     echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
     export PATH="$HOME/.local/bin:$PATH"
fi

# Optional: Autostart-Eintrag für das Dashboard (Raspberry Pi Desktop)
AUTOSTART_DIR=~/.config/autostart
if [ -d "$AUTOSTART_DIR" ]; then
    cat <<EOF > "$AUTOSTART_DIR/Dashboard.desktop"
[Desktop Entry]
Type=Application
Name=Dashboard
Exec=python3 $(pwd)/src/main.py
Icon=utilities-terminal
Terminal=true
EOF
    chmod +x "$AUTOSTART_DIR/Dashboard.desktop"
    echo "Autostart-Eintrag für Dashboard wurde erstellt."
fi

echo "[INSTALL] Installation abgeschlossen! Die Kurzbefehle dbpull und dbstart sind jetzt verfügbar."
echo "[INSTALL] Prüfe Emoji-Fonts..."
if fc-list | grep -qi "NotoColorEmoji"; then
    echo "[INSTALL] Emoji-Fonts OK."
else
    echo "[INSTALL] WARNUNG: Emoji-Fonts fehlen! Bitte installiere fonts-noto-color-emoji."
fi

echo "[INSTALL] Prüfe PyTado-Installation..."
if ! python -c "import PyTado" 2>/dev/null; then
    echo "[INSTALL] WARNUNG: PyTado konnte nicht importiert werden! Prüfe Installation mit: pip install pytado"
else
    echo "[INSTALL] PyTado OK."
fi
