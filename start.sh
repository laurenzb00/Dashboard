#!/usr/bin/env bash
set -euo pipefail

cd /home/laurenz/Dashboard
source .venv/bin/activate

export PYTHONPATH="$(pwd)/src${PYTHONPATH:+:$PYTHONPATH}"

echo "Using: $(which python) ($(python -V))"
exec python src/main.py
