"""Integration tests for autosave and empty-room cleanup."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow

import json
import os
import socket
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"server did not listen on port {port}")


def _start_server(port: int, *extra: str) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "main.py"),
         "--port", str(port), "--log-level", "WARNING", *extra],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _wait_for_port(port)
    return proc


def _stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _send(sock: socket.socket, obj: dict) -> None:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)


def _recv(sock: socket.socket, timeout: float = 5.0) -> dict:
    sock.settimeout(timeout)
    header = b""
    while len(header) < 4:
        chunk = sock.recv(4 - len(header))
        if not chunk:
            raise ConnectionError("closed")
        header += chunk
    (n,) = struct.unpack(">I", header)
    body = b""
    while len(body) < n:
        chunk = sock.recv(n - len(body))
        if not chunk:
            raise ConnectionError("closed")
        body += chunk
    return json.loads(body.decode("utf-8"))


def _cleanup_rooms():
    rooms = os.path.join(ROOT, "rooms")
    if os.path.isdir(rooms):
        for name in os.listdir(rooms):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(rooms, name))
                except OSError:
                    pass


def test_autosave_writes_when_room_changed():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port, "--autosave-interval", "1")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        sid = "as"
        _send(a, {"type": "stroke_start", "op_id": "a1", "stroke_id": sid,
                  "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2})
        _send(a, {"type": "stroke_end", "op_id": "a2", "stroke_id": sid})
        time.sleep(2.0)

        path = os.path.join(ROOT, "rooms", f"{code}.json")
        assert os.path.exists(path), "autosave did not write changed room"
        a.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()


def test_autosave_no_write_when_unchanged():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port, "--autosave-interval", "1")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        sid = "as2"
        _send(a, {"type": "stroke_start", "op_id": "b1", "stroke_id": sid,
                  "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2})
        _send(a, {"type": "stroke_end", "op_id": "b2", "stroke_id": sid})
        time.sleep(2.0)

        path = os.path.join(ROOT, "rooms", f"{code}.json")
        assert os.path.exists(path)
        m1 = os.path.getmtime(path)
        time.sleep(2.0)
        m2 = os.path.getmtime(path)
        assert m1 == m2, "autosave rewrote unchanged room"
        a.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()


def test_empty_room_saved_removed_loadable():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port, "--autosave-interval", "1", "--empty-room-grace", "1")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        # clean leave makes the room empty immediately
        _send(a, {"type": "leave"})
        time.sleep(0.3)
        a.close()

        # wait for empty-room grace + autosave
        deadline = time.time() + 5.0
        path = os.path.join(ROOT, "rooms", f"{code}.json")
        while time.time() < deadline:
            if os.path.exists(path):
                break
            time.sleep(0.2)
        assert os.path.exists(path), "empty room was not saved"

        # room removed from memory: join returns room_not_found
        deadline = time.time() + 5.0
        removed = False
        while time.time() < deadline:
            try:
                c = socket.create_connection(("127.0.0.1", port), timeout=2)
                c.settimeout(2)
                _send(c, {"type": "join_room", "username": "bob", "room_code": code})
                r = _recv(c)
                c.close()
                if r["type"] == "join_denied" and r.get("reason") == "room_not_found":
                    removed = True
                    break
            except OSError:
                pass
            time.sleep(0.3)
        assert removed, "empty room was not removed from memory"

        # loadable again
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("code") == code
    finally:
        _stop_server(proc)
        _cleanup_rooms()
