@echo off
REM Build both executables and assemble a clean release folder (Windows).
setlocal
cd /d "%~dp0\.."
call build\build_server.bat
if errorlevel 1 exit /b 1
call build\build_client.bat
if errorlevel 1 exit /b 1
if exist release rmdir /s /q release
mkdir release
copy /Y dist\WhiteboardServer.exe release\ >nul
copy /Y dist\WhiteboardClient.exe release\ >nul
(
echo @echo off
echo cd /d "%%~dp0"
echo WhiteboardServer.exe %%*
echo pause
) > release\run_server.bat
(
echo @echo off
echo cd /d "%%~dp0"
echo start "" "WhiteboardClient.exe"
) > release\run_client.bat
(
echo @echo off
echo netsh advfirewall firewall add rule name="Whiteboard TCP 5050" dir=in action=allow protocol=TCP localport=5050
echo echo Done. Run this script as Administrator.
echo pause
) > release\allow_firewall.bat
(
echo Whiteboard LAN quick start
echo 1. Run allow_firewall.bat as Administrator once.
echo 2. Start WhiteboardServer.exe and note the LAN IP.
echo 3. Start WhiteboardClient.exe on each machine.
echo 4. Create a room in one client.
echo 5. Join with the room code from the other clients.
echo 6. Draw, chat, undo, and export PNG.
echo 7. Save and load boards from the toolbar.
echo 8. If the exe is blocked, run from source with Python 3.11+.
echo 9. Keep the server and clients on the same LAN.
echo 10. Internet is not required.
echo 11. Rooms are saved next to WhiteboardServer.exe.
echo 12. Client errors are written to whiteboard-client.log.
echo 13. Rebuild with build\build_all.bat.
echo 14. Linux needs its own build.
echo 15. Do not expose this server to the public internet.
) > release\README.txt
echo Release folder created.
endlocal