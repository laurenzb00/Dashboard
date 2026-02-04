#!/bin/bash
# Installationsskript für das Dashboard-Projekt

# Systempakete installieren
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-tk git


# Python venv erstellen und aktivieren
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate

# Python-Libraries installieren (aus requirements.txt, falls vorhanden)
if [ -f requirements.txt ]; then
    pip install --upgrade pip
    pip install -r requirements.txt
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
cat <<EOF > ~/.local/bin/dbpull
#!/bin/bash
# Beispiel: Datenbank-Backup oder Pull
cd "$(dirname "$0")/../../"  # ins Projektverzeichnis
# Hier eigenen dbpull-Befehl eintragen
python3 src/core/datastore.py --pull
EOF
chmod +x ~/.local/bin/dbpull

cat <<EOF > ~/.local/bin/dbstart
#!/bin/bash
# Dashboard starten
cd "$(dirname \"$0\")/../../"  # ins Projektverzeichnis
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

echo "Installation abgeschlossen! Die Kurzbefehle dbpull und dbstart sind jetzt verfügbar."
