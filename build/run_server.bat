@echo off
REM Run the whiteboard server from source (Windows).
REM Usage: build\run_server.bat [port]
setlocal
cd /d "%~dp0\.."
if not exist .venv\Scripts\python.exe (
    echo Creating virtual environment...
    python -m venv .venv
    .venv\Scripts\python.exe -m pip install -r requirements.txt
)
set PORT=%1
if "%PORT%"=="" set PORT=5050
.venv\Scripts\python.exe server\main.py --host 0.0.0.0 --port %PORT%
endlocal
