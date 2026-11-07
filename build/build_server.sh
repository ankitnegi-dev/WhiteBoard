#!/usr/bin/env bash
# Build the server executable with PyInstaller (Linux/macOS).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/pyinstaller ]; then
    echo "Run setup first"
    exit 1
fi
.venv/bin/pyinstaller --onefile --name WhiteboardServer --workpath .pyi_work --distpath dist --specpath .pyi_work server/main.py
