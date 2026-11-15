"""Manual smoke test for the connect and rejoin flow."""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from client.ui import WhiteboardApp


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_port(port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _pump(root, app, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            root.update()
        except tk.TclError:
            return False
        time.sleep(0.05)
    return True


def step(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}: {name}" + (f" ({detail})" if detail else ""))
    return ok


def main():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "main.py"),
         "--port", str(port), "--log-level", "WARNING"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if not _wait_port(port):
        print("FAIL: server did not start")
        proc.terminate()
        return 1

    tmp = tempfile.mkdtemp()
    os.environ["APPDATA"] = tmp
    os.environ["XDG_CONFIG_HOME"] = tmp

    all_ok = True
    root1 = tk.Tk()
    app1 = WhiteboardApp(root1)
    root1.update()
    app1.entry_host.delete(0, "end")
    app1.entry_host.insert(0, "127.0.0.1")
    app1.entry_port.delete(0, "end")
    app1.entry_port.insert(0, str(port))
    app1.entry_username.delete(0, "end")
    app1.entry_username.insert(0, "alice")
    app1.connect_button.invoke()
    ok = _pump(root1, app1, 5.0) and hasattr(app1, "main_frame")
    all_ok &= step("create room", ok, getattr(app1, "room_code", ""))
    code = getattr(app1, "room_code", "")

    # Draw one stroke.
    if ok:
        app1._set_tool("pen")
        from client.tools import logical_to_window
        w, h = app1._canvas_size()
        px, py = logical_to_window(100, 100, w, h, app1._canvas_inset())
        app1.canvas.event_generate("<ButtonPress-1>", x=int(px), y=int(py))
        _pump(root1, app1, 0.2)
        px2, py2 = logical_to_window(160, 100, w, h, app1._canvas_inset())
        app1.canvas.event_generate("<B1-Motion>", x=int(px2), y=int(py2))
        _pump(root1, app1, 0.2)
        app1.canvas.event_generate("<ButtonRelease-1>", x=int(px2), y=int(py2))
        _pump(root1, app1, 1.0)
        ok = len([o for o in app1.state.objects.values() if o.get("type") == "stroke"]) == 1
        all_ok &= step("draw stroke", ok)

    # Chat.
    if ok:
        app1.chat_entry.delete(0, "end")
        app1.chat_entry.insert(0, "smoke chat")
        app1._send_chat()
        _pump(root1, app1, 0.5)
        all_ok &= step("send chat", True)

    # Close without goodbye.
    app1._on_close()
    time.sleep(1.5)

    # Reopen and rejoin.
    root2 = tk.Tk()
    app2 = WhiteboardApp(root2)
    root2.update()
    assert app2.rejoin_button.instate(["!disabled"]), "Rejoin button disabled"
    app2.rejoin_button.invoke()
    ok = _pump(root2, app2, 5.0) and hasattr(app2, "main_frame")
    all_ok &= step("rejoin last room", ok, getattr(app2, "room_code", ""))
    if ok:
        all_ok &= step("rejoin username", "alice" in app2.root.title())
        all_ok &= step("rejoin room code", app2.room_code == code)

    # Second client joins the room.
    root3 = tk.Tk()
    app3 = WhiteboardApp(root3)
    root3.update()
    app3.connect_mode.set("join")
    app3._update_connect_mode()
    app3.entry_host.delete(0, "end")
    app3.entry_host.insert(0, "127.0.0.1")
    app3.entry_port.delete(0, "end")
    app3.entry_port.insert(0, str(port))
    app3.entry_username.delete(0, "end")
    app3.entry_username.insert(0, "bob")
    app3.entry_room_code.delete(0, "end")
    app3.entry_room_code.insert(0, code)
    app3.connect_button.invoke()
    ok = _pump(root3, app3, 5.0) and hasattr(app3, "main_frame")
    all_ok &= step("second client joins", ok)

    app2._on_close()
    app3._on_close()
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()

    print("PASS" if all_ok else "FAIL")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())