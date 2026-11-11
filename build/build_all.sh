#!/usr/bin/env bash
# Build both executables and assemble a clean release folder (Linux).
set -euo pipefail
cd "$(dirname "$0")/.."
bash build/build_server.sh
bash build/build_client.sh
rm -rf release
mkdir -p release
cp dist/WhiteboardServer release/
cp dist/WhiteboardClient release/
cat > release/run_server.sh <<'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
./WhiteboardServer "$@"
EOF
cat > release/run_client.sh <<'EOF'
#!/usr/bin/env bash
cd "$(dirname "$0")"
./WhiteboardClient &
EOF
chmod +x release/run_server.sh release/run_client.sh release/WhiteboardServer release/WhiteboardClient
cat > release/README.txt <<'EOF'
Whiteboard LAN quick start
1. Install python3-tk on Ubuntu if running from source.
2. Start WhiteboardServer and note the LAN IP.
3. Start WhiteboardClient on each machine.
4. Create a room in one client.
5. Join with the room code from the other clients.
6. Draw, chat, undo, and export PNG.
7. Save and load boards from the toolbar.
8. If the binary is blocked, run from source with Python 3.11+.
9. Keep the server and clients on the same LAN.
10. Internet is not required.
11. Rooms are saved next to WhiteboardServer.
12. Client errors are written to whiteboard-client.log.
13. Rebuild with build/build_all.sh.
14. Windows needs its own build.
15. Do not expose this server to the public internet.
EOF
echo "Release folder created."