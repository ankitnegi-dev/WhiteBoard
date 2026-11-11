#!/usr/bin/env bash
# Build the client executable with PyInstaller (Linux).
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/pyinstaller ]; then
    echo "Run setup first"
    exit 1
fi
mkdir -p .pyi_work
.venv/bin/pyinstaller --clean --noconfirm --onefile --windowed --name WhiteboardClient --workpath .pyi_work --distpath dist --specpath .pyi_work --collect-all tkinter --collect-all PIL --hidden-import protocol --hidden-import shapes_geometry --hidden-import platform_utils --hidden-import client.ui --hidden-import client.state --hidden-import client.tools --hidden-import client.net client/main.py