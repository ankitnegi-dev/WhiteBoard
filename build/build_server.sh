#!/usr/bin/env bash
# Build the server executable with PyInstaller (Linux).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/pyinstaller ]; then
    echo "Run setup first"
    exit 1
fi
mkdir -p .pyi_work
.venv/bin/pyinstaller --clean --noconfirm --onefile --name WhiteboardServer --workpath .pyi_work --distpath dist --specpath .pyi_work --hidden-import protocol --hidden-import platform_utils --hidden-import server.client_handler --hidden-import server.room_manager --hidden-import server.persistence server/main.py