"""Integration tests for backpressure and resync mode."""

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


def test_queue_overflow_resync_mode():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port, "--grace-period", "5")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        # slow bob: joins but never reads
        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        # active carol
        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "join_room", "username": "carol", "room_code": code})
        assert _recv(c)["type"] == "join_ok"

        # flood strokes from alice
        for i in range(150):
            sid = f"s{i}"
            _send(a, {"type": "stroke_start", "op_id": f"o{i}a", "stroke_id": sid,
                      "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2})
            _send(a, {"type": "stroke_end", "op_id": f"o{i}b", "stroke_id": sid})
        time.sleep(2.0)

        received = 0
        c.settimeout(0.5)
        while True:
            try:
                m = _recv(c, 0.5)
                if m.get("type") in ("stroke_start", "stroke_end"):
                    received += 1
            except socket.timeout:
                break
        assert received > 0, "active client stalled during overflow"

        # server still alive
        _send(c, {"type": "chat", "text": "hi"})
        r = _recv(c, timeout=3)
        assert r.get("type") == "chat", r
        a.close()
        b.close()
        c.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()
