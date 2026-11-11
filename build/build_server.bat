@echo off
REM Build the server executable with PyInstaller (Windows).
setlocal
cd /d "%~dp0\.."
if not exist .venv\Scripts\pyinstaller.exe (
    echo Run setup first: python -m venv .venv ^&^& .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)
if not exist .pyi_work mkdir .pyi_work
.venv\Scripts\pyinstaller.exe --clean --noconfirm --onefile --name WhiteboardServer --workpath .pyi_work --distpath dist --specpath .pyi_work --hidden-import protocol --hidden-import platform_utils --hidden-import server.client_handler --hidden-import server.room_manager --hidden-import server.persistence server\main.py
if errorlevel 1 exit /b 1
endlocal