#!/bin/bash
# Startup script for Dashboard

# Change to script directory
cd "$(dirname "$0")"

# Activate Virtual Env
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "No virtual environment found. Creating one..."
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
fi

# Ensure PYTHONPATH includes src
export PYTHONPATH="${PYTHONPATH}:$(pwd)/src"

# Verify Spotify config
if [ ! -f "config/spotify.json" ]; then
    echo "WARNING: config/spotify.json not found."
fi

# Run the application with extended diagnostics for segfault tracing
export PYTHONMALLOC=${PYTHONMALLOC:-debug}
export MPLBACKEND=${MPLBACKEND:-TkAgg}
PYTHON_BIN=${PYTHON_BIN:-python}
echo "Starting Application with faulthandler/tracemalloc..."
$PYTHON_BIN -X faulthandler -X tracemalloc=25 src/main.py

