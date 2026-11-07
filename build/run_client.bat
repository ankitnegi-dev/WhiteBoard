@echo off
REM Run the CLI test client (Windows).
REM Usage: build\run_client.bat [host] [port]
setlocal
cd /d "%~dp0\.."
set HOST=%1
if "%HOST%"=="" set HOST=127.0.0.1
set PORT=%2
if "%PORT%"=="" set PORT=5050
.venv\Scripts\python.exe tests\fake_client.py --host %HOST% --port %PORT%
endlocal
