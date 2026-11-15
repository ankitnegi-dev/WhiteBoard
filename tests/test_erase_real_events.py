"""Real-event eraser convergence tests."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import tkinter as tk

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("no listen")


def _start(port):
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "main.py"),
         "--port", str(port), "--log-level", "WARNING"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _wait_port(port)
    return proc


def _stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _pump(root, app, timeout=1.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not hasattr(app, "main_frame"):
        try:
            root.update()
        except tk.TclError:
            return False
        time.sleep(0.05)
    end = time.time() + timeout
    while time.time() < end:
        try:
            root.update()
        except tk.TclError:
            break
        time.sleep(0.02)
    return True


def _connect_form(app, host, port, username, room_code="", password=""):
    app.entry_host.delete(0, "end")
    app.entry_host.insert(0, host)
    app.entry_port.delete(0, "end")
    app.entry_port.insert(0, str(port))
    app.entry_username.delete(0, "end")
    app.entry_username.insert(0, username)
    app.entry_room_code.delete(0, "end")
    app.entry_room_code.insert(0, room_code)
    app.entry_password.delete(0, "end")
    app.entry_password.insert(0, password)


@pytest.fixture
def server():
    port = _free_port()
    proc = _start(port)
    yield port
    _stop(proc)


def test_erase_real_events(server):
    import os
    import tempfile
    tmp = tempfile.mkdtemp()
    os.environ["APPDATA"] = tmp
    os.environ["XDG_CONFIG_HOME"] = tmp

    rootA = tk.Tk()
    rootB = tk.Tk()
    from client.ui import WhiteboardApp
    appA = WhiteboardApp(rootA)
    appB = WhiteboardApp(rootB)
    rootA.update()
    rootB.update()

    _connect_form(appA, "127.0.0.1", server, "alice")
    appA.connect_button.invoke()
    assert _pump(rootA, appA, 5.0)
    code = appA.room_code

    appB.connect_mode.set("join")
    appB._update_connect_mode()
    _connect_form(appB, "127.0.0.1", server, "bob", code)
    appB.connect_button.invoke()
    assert _pump(rootB, appB, 5.0)

    # Draw a stroke on A using real canvas events.
    appA._set_tool("pen")
    w, h = appA._canvas_size()
    from client.tools import logical_to_window
    x0, y0 = logical_to_window(100, 100, w, h, appA._canvas_inset())
    x1, y1 = logical_to_window(180, 100, w, h, appA._canvas_inset())
    appA.canvas.event_generate("<ButtonPress-1>", x=int(x0), y=int(y0))
    _pump(rootA, appA, 0.2)
    appA.canvas.event_generate("<B1-Motion>", x=int(x1), y=int(y1))
    _pump(rootA, appA, 0.2)
    appA.canvas.event_generate("<ButtonRelease-1>", x=int(x1), y=int(y1))
    _pump(rootA, appA, 1.0)
    _pump(rootB, appB, 1.0)

    assert len([o for o in appA.state.objects.values() if o.get("type") == "stroke"]) == 1
    assert len([o for o in appB.state.objects.values() if o.get("type") == "stroke"]) == 1
    oid = next(k for k, v in appA.state.objects.items() if v.get("type") == "stroke")
    assert appA.canvas.find_withtag(oid)
    assert appB.canvas.find_withtag(oid)

    # Erase on A using real canvas events.
    appA._set_tool("eraser")
    ex, ey = logical_to_window(150, 100, w, h, appA._canvas_inset())
    appA.canvas.event_generate("<Motion>", x=int(ex), y=int(ey))
    _pump(rootA, appA, 0.2)
    appA.canvas.event_generate("<ButtonPress-1>", x=int(ex), y=int(ey))
    _pump(rootA, appA, 0.5)
    appA.canvas.event_generate("<ButtonRelease-1>", x=int(ex), y=int(ey))
    _pump(rootA, appA, 1.0)
    _pump(rootB, appB, 1.0)

    assert oid not in appA.state.objects
    assert oid not in appB.state.objects
    assert appA.canvas.find_withtag(oid) == ()
    assert appB.canvas.find_withtag(oid) == ()

    # Compare hashes via state objects.
    import hashlib, json
    def h(st):
        return hashlib.sha256(json.dumps(sorted(st.objects.values(), key=lambda o: o.get("id", "")), sort_keys=True).encode()).hexdigest()
    assert h(appA.state) == h(appB.state)

    rootA.destroy()
    rootB.destroy()