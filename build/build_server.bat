@echo off
REM Build the server executable with PyInstaller (Windows).
setlocal
cd /d "%~dp0\.."
if not exist .venv\Scripts\pyinstaller.exe (
    echo Run setup first: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)
.venv\Scripts\pyinstaller.exe --onefile --name WhiteboardServer --workpath .pyi_work --distpath dist --specpath .pyi_work server\main.py
endlocal
