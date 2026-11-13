"""End-to-end connect tests using a real server."""

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


def _start(port, *extra):
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "main.py"),
         "--port", str(port), "--grace-period", "2", "--log-level", "WARNING", *extra],
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


def _pump(root, app, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline and not hasattr(app, "main_frame"):
        try:
            root.update()
        except tk.TclError:
            return False
        time.sleep(0.05)
    return hasattr(app, "main_frame")


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


def test_create_room_e2e(server, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    root = tk.Tk()
    from client.ui import WhiteboardApp
    app = WhiteboardApp(root)
    root.update()
    _connect_form(app, "127.0.0.1", server, "alice")
    app.connect_button.invoke()
    assert _pump(root, app, 5.0)
    assert "alice" in app.root.title()
    assert "host" in app.root.title()
    assert app.room_code_label.cget("text")
    assert "alice" in app.state.participants[0].get("name", "") or any(u.get("name") == "alice" for u in app.state.participants)
    root.destroy()


def test_join_room_e2e(server, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    root1 = tk.Tk()
    from client.ui import WhiteboardApp
    app1 = WhiteboardApp(root1)
    root1.update()
    _connect_form(app1, "127.0.0.1", server, "alice")
    app1.connect_button.invoke()
    assert _pump(root1, app1, 5.0)
    code = app1.room_code

    root2 = tk.Tk()
    app2 = WhiteboardApp(root2)
    root2.update()
    app2.connect_mode.set("join")
    app2._update_connect_mode()
    _connect_form(app2, "127.0.0.1", server, "bob", code)
    app2.connect_button.invoke()
    assert _pump(root2, app2, 5.0)
    assert "bob" in app2.root.title()
    assert any(u.get("name") == "bob" for u in app2.state.participants)
    root1.destroy()
    root2.destroy()


def test_wrong_code_and_password(server, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # wrong room code
    root = tk.Tk()
    from client.ui import WhiteboardApp
    app = WhiteboardApp(root)
    root.update()
    app.connect_mode.set("join")
    app._update_connect_mode()
    _connect_form(app, "127.0.0.1", server, "alice", "ZZZZZZ")
    app.connect_button.invoke()
    assert not _pump(root, app, 2.0)
    assert "denied" in app.connect_status.cget("text").lower() or "not found" in app.connect_status.cget("text").lower()
    root.destroy()

    # wrong password
    root2 = tk.Tk()
    app2 = WhiteboardApp(root2)
    root2.update()
    _connect_form(app2, "127.0.0.1", server, "host", password="secret")
    app2.connect_button.invoke()
    assert _pump(root2, app2, 5.0)
    code = app2.room_code
    root2.destroy()

    root3 = tk.Tk()
    app3 = WhiteboardApp(root3)
    root3.update()
    app3.connect_mode.set("join")
    app3._update_connect_mode()
    _connect_form(app3, "127.0.0.1", server, "bob", code, password="wrong")
    app3.connect_button.invoke()
    assert not _pump(root3, app3, 2.0)
    assert "denied" in app3.connect_status.cget("text").lower() or "password" in app3.connect_status.cget("text").lower()
    root3.destroy()


def test_taken_username(server, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    root1 = tk.Tk()
    from client.ui import WhiteboardApp
    app1 = WhiteboardApp(root1)
    root1.update()
    _connect_form(app1, "127.0.0.1", server, "alice")
    app1.connect_button.invoke()
    assert _pump(root1, app1, 5.0)

    session_file = os.path.join(str(tmp_path), "LANWhiteboard", "sessions.json")
    try:
        os.remove(session_file)
    except OSError:
        pass

    root2 = tk.Tk()
    app2 = WhiteboardApp(root2)
    root2.update()
    app2.connect_mode.set("join")
    app2._update_connect_mode()
    _connect_form(app2, "127.0.0.1", server, "alice", app1.room_code)
    app2.connect_button.invoke()
    assert not _pump(root2, app2, 2.0)
    assert "denied" in app2.connect_status.cget("text").lower()
    root1.destroy()
    root2.destroy()


def test_rejoin_last_room(server, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    root1 = tk.Tk()
    from client.ui import WhiteboardApp
    app1 = WhiteboardApp(root1)
    root1.update()
    _connect_form(app1, "127.0.0.1", server, "alice")
    app1.connect_button.invoke()
    assert _pump(root1, app1, 5.0)
    code = app1.room_code
    # close without goodbye
    root1.destroy()
    time.sleep(1.5)

    root2 = tk.Tk()
    app2 = WhiteboardApp(root2)
    root2.update()
    assert app2.rejoin_button.instate(["!disabled"])
    app2.rejoin_button.invoke()
    assert _pump(root2, app2, 5.0)
    assert "alice" in app2.root.title()
    assert app2.room_code == code
    root2.destroy()


def test_server_not_running(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    root = tk.Tk()
    from client.ui import WhiteboardApp
    app = WhiteboardApp(root)
    root.update()
    _connect_form(app, "127.0.0.1", 1, "alice")
    app.connect_button.invoke()
    assert not _pump(root, app, 6.0)
    assert "denied" in app.connect_status.cget("text").lower() or "failed" in app.connect_status.cget("text").lower() or "refused" in app.connect_status.cget("text").lower()
    root.destroy()


def test_create_after_failure(server, tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    root = tk.Tk()
    from client.ui import WhiteboardApp
    app = WhiteboardApp(root)
    root.update()
    # wrong code first
    app.connect_mode.set("join")
    app._update_connect_mode()
    _connect_form(app, "127.0.0.1", server, "alice", "ZZZZZZ")
    app.connect_button.invoke()
    assert not _pump(root, app, 2.0)
    # create now
    app.connect_mode.set("create")
    app._update_connect_mode()
    _connect_form(app, "127.0.0.1", server, "alice2")
    app.connect_button.invoke()
    assert _pump(root, app, 5.0)
    assert "alice2" in app.root.title()
    root.destroy()