#!/usr/bin/env bash
# Sammelt ein Laufzeitprofil des Dashboards plus die Systemwerte, die bei
# Haengern mit hineinspielen, und packt alles in ein Zip.
#
# Hintergrund: der eingebaute Haenger-Melder zeigt nur den Stapel, wenn eine
# Sekunde schon ueberschritten ist. Er sieht damit die Sekunde nicht, die sich
# aus hundert Stuecken zu zehn Millisekunden zusammensetzt. py-spy liest den
# laufenden Prozess von aussen aus - ohne Code-Aenderung, ohne Neustart.
#
# Aufruf:   bash tools/leistung_messen.sh [Sekunden]
# Wichtig:  WAEHREND der Messung durch die Tabs blaettern, damit genau das im
#           Profil landet, worum es geht.

set -u
DAUER="${1:-120}"
ZIEL="${HOME}/Dashboard/analyse/leistung_$(date +%Y%m%d_%H%M)"
mkdir -p "$ZIEL" || { echo "Kann $ZIEL nicht anlegen"; exit 1; }

PID="$(pgrep -f 'python.*src/main.py' | head -1)"
if [ -z "${PID}" ]; then
    echo "Dashboard-Prozess nicht gefunden (gesucht: python ... src/main.py)."
    echo "Laeuft es gerade? 'pgrep -af python' zeigt alle Python-Prozesse."
    exit 1
fi
echo "Dashboard laeuft als PID ${PID}."

if ! command -v py-spy >/dev/null 2>&1; then
    echo "Installiere py-spy ..."
    pip install py-spy --break-system-packages -q \
        || pip install py-spy -q \
        || { echo "py-spy liess sich nicht installieren."; exit 1; }
fi
PYSPY="$(command -v py-spy)"

# --- Systemzustand vorher ------------------------------------------------
{
    echo "=== $(date -Is) ==="
    echo "--- Temperatur und Drosselung ---"
    vcgencmd measure_temp     2>/dev/null || echo "vcgencmd nicht verfuegbar"
    vcgencmd get_throttled    2>/dev/null
    vcgencmd measure_clock arm 2>/dev/null
    echo "--- Speicher ---"
    free -m
    echo "--- Prozess ---"
    grep -E 'VmRSS|VmSwap|Threads' "/proc/${PID}/status" 2>/dev/null
    echo "--- Auslagerung gesamt (seit Start) ---"
    grep -E 'pswpin|pswpout' /proc/vmstat 2>/dev/null
} > "${ZIEL}/system_vorher.txt" 2>&1

# --- Momentaufnahme aller Threads ---------------------------------------
sudo "${PYSPY}" dump --pid "${PID}" > "${ZIEL}/threads.txt" 2>&1 \
    || echo "py-spy dump fehlgeschlagen - siehe threads.txt" 

# --- Das eigentliche Profil ---------------------------------------------
echo
echo "Messe ${DAUER} Sekunden. JETZT durch die Tabs blaettern!"
sudo "${PYSPY}" record --pid "${PID}" --duration "${DAUER}" \
     --rate 100 --subprocesses --idle \
     --output "${ZIEL}/profil.svg" > "${ZIEL}/record_log.txt" 2>&1 \
    || echo "py-spy record fehlgeschlagen - siehe record_log.txt"

# Zusaetzlich als Text, weil sich das leichter auswerten laesst als ein Bild
sudo "${PYSPY}" record --pid "${PID}" --duration 30 \
     --rate 100 --format speedscope \
     --output "${ZIEL}/profil.speedscope.json" >> "${ZIEL}/record_log.txt" 2>&1 \
    || true

# --- Systemzustand nachher ----------------------------------------------
{
    echo "=== $(date -Is) ==="
    vcgencmd measure_temp  2>/dev/null
    vcgencmd get_throttled 2>/dev/null
    free -m
    grep -E 'VmRSS|VmSwap|Threads' "/proc/${PID}/status" 2>/dev/null
    grep -E 'pswpin|pswpout' /proc/vmstat 2>/dev/null
} > "${ZIEL}/system_nachher.txt" 2>&1

# --- Einpacken -----------------------------------------------------------
ZIP="${ZIEL}.zip"
( cd "$(dirname "${ZIEL}")" && zip -qr "$(basename "${ZIP}")" "$(basename "${ZIEL}")" )

echo
echo "Fertig."
echo "  Ordner: ${ZIEL}"
echo "  Zip:    ${ZIP}"
echo
echo "Das Zip auf den Rechner kopieren:"
echo "  scp laurenz@raspberrypi:${ZIP} \"C:\\Users\\laure\\OneDrive\\Studium-SBG\\SoS 25\\Datenerfassung\\eigenes projekt\\analyse\\\""
