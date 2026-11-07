#!/usr/bin/env bash
# Run the whiteboard server from source (Linux/macOS).
# Usage: ./build/run_server.sh [port]
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
fi
PORT="${1:-5050}"
.venv/bin/python server/main.py --host 0.0.0.0 --port "$PORT"
