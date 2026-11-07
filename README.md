# LAN Collaborative Whiteboard

Server-based, multi-user whiteboard over a LAN using TCP sockets. The server
is authoritative and holds the operation log; clients are thin views.

Day 1 delivers the networking foundation: length-prefixed JSON framing, a
threaded echo server, and a CLI test client. Later milestones add rooms,
drawing, undo/redo, persistence, and the tkinter UI.

## Prerequisites

- Python 3.11 or newer.
- On Linux, install tkinter (it is not always included):
  - Debian/Ubuntu: `sudo apt install python3-tk`
  - Fedora: `sudo dnf install python3-tkinter`

## Setup (Windows cmd)

```
cd "C:\path\to\whiteboard"
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## Setup (Linux)

```
cd /path/to/whiteboard
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## Run the server

Windows cmd:

```
.venv\Scripts\python.exe server\main.py --host 0.0.0.0 --port 5050
```

or `build\run_server.bat 5050`.

Linux:

```
.venv/bin/python server/main.py --host 0.0.0.0 --port 5050
```

or `./build/run_server.sh 5050`.

The server prints its LAN IPs and port on startup. Find them manually with:

- Windows: `ipconfig` (look for IPv4 Address under the active adapter)
- Linux: `ip addr` (look for inet under the active interface)

Open the chosen port in the firewall if clients on other machines cannot
connect. Windows example:

```
netsh advfirewall firewall add rule name="Whiteboard" dir=in action=allow protocol=TCP localport=5050
```

## Run a CLI test client

Windows cmd:

```
.venv\Scripts\python.exe tests\fake_client.py --host 127.0.0.1 --port 5050
```

Linux:

```
.venv/bin/python tests/fake_client.py --host 127.0.0.1 --port 5050
```

Type a line and press Enter to send it; received messages print on a
separate thread.

## Run the GUI client

Windows cmd:

```
.venv\Scripts\python.exe -m client.main
```

Linux:

```
.venv/bin/python -m client.main
```

If tkinter is missing on Linux, install it first:

```
sudo apt install python3-tk
```

## Run the tests

Windows cmd:

```
.venv\Scripts\python.exe -m pytest -q
```

Linux:

```
.venv/bin/python -m pytest -q
```

## Spike: tkinter + PyInstaller

```
cd spike
..\.venv\Scripts\pyinstaller.exe --onefile --windowed hello_tk.py
```

On Linux use `.venv/bin/pyinstaller`. Run the resulting executable to
confirm the canvas draws a line on mouse drag.
