@echo off
REM Build the client executable with PyInstaller (Windows).
setlocal
cd /d "%~dp0\.."
if not exist .venv\Scripts\pyinstaller.exe (
    echo Run setup first
    exit /b 1
)
if not exist .pyi_work mkdir .pyi_work
REM Optional icon generated with Pillow.
.venv\Scripts\python.exe -c "from PIL import Image; Image.new('RGB',(64,64),'#1E5B6B').save('build/whiteboard.ico', format='ICO', sizes=[(16,16),(32,32),(48,48),(64,64)])" >nul 2>nul
set "ICONFILE=%CD%\build\whiteboard.ico"
if exist "%ICONFILE%" (
.venv\Scripts\pyinstaller.exe --clean --noconfirm --onefile --windowed --name WhiteboardClient --workpath .pyi_work --distpath dist --specpath .pyi_work --icon "%ICONFILE%" --collect-all tkinter --collect-all PIL --hidden-import protocol --hidden-import shapes_geometry --hidden-import platform_utils --hidden-import client.ui --hidden-import client.state --hidden-import client.tools --hidden-import client.net client\main.py
) else (
.venv\Scripts\pyinstaller.exe --clean --noconfirm --onefile --windowed --name WhiteboardClient --workpath .pyi_work --distpath dist --specpath .pyi_work --collect-all tkinter --collect-all PIL --hidden-import protocol --hidden-import shapes_geometry --hidden-import platform_utils --hidden-import client.ui --hidden-import client.state --hidden-import client.tools --hidden-import client.net client\main.py
)
if errorlevel 1 exit /b 1
endlocal