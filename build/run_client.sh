#!/usr/bin/env bash
# Run the CLI test client (Linux/macOS).
# Usage: ./build/run_client.sh [host] [port]
set -euo pipefail
cd "$(dirname "$0")/.."
HOST="${1:-127.0.0.1}"
PORT="${2:-5050}"
.venv/bin/python tests/fake_client.py --host "$HOST" --port "$PORT"
