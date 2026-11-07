@echo off
REM Build the client executable with PyInstaller (Windows).
setlocal
cd /d "%~dp0\.."
if not exist .venv\Scripts\pyinstaller.exe (
    echo Run setup first
    exit /b 1
)
.venv\Scripts\pyinstaller.exe --onefile --windowed --name WhiteboardClient --workpath .pyi_work --distpath dist --specpath .pyi_work client\main.py
endlocal
