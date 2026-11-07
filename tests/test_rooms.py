"""Integration tests for Day 2 rooms and participants."""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time

import pytest

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
        [sys.executable, os.path.join(ROOT, "server", "main.py"), "--port", str(port), "--log-level", "WARNING", *extra],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
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


def _recv_until(sock: socket.socket, predicate, timeout: float = 5.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = _recv(sock, timeout=0.5)
        except socket.timeout:
            continue
        if predicate(msg):
            return msg
    raise TimeoutError("predicate not satisfied")


def test_create_and_join():
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        created = _recv(a)
        assert created["type"] == "room_created"
        code = created["room_code"]
        assert created["role"] == "host"

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        joined = _recv(b)
        assert joined["type"] == "join_ok"
        assert joined["role"] == "editor"
        assert any(u["name"] == "bob" for u in joined["participants"])

        # Creator is notified about the join.
        notice = _recv_until(a, lambda m: m.get("type") == "user_joined")
        assert notice["username"] == "bob"

        a.close()
        b.close()
    finally:
        _stop_server(proc)


def test_wrong_room_code():
    port = _free_port()
    proc = _start_server(port)
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
        s.settimeout(3)
        _send(s, {"type": "join_room", "username": "bob", "room_code": "NOPE12"})
        reply = _recv(s)
        assert reply["type"] == "join_denied"
        assert reply["reason"] == "room_not_found"
        s.close()
    finally:
        _stop_server(proc)


def test_wrong_password():
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice", "password": "secret"})
        created = _recv(a)
        code = created["room_code"]

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code, "password": "wrong"})
        reply = _recv(b)
        assert reply["type"] == "join_denied"
        assert reply["reason"] == "wrong_password"
        a.close()
        b.close()
    finally:
        _stop_server(proc)


def test_duplicate_username():
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        created = _recv(a)
        code = created["room_code"]

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "alice", "room_code": code})
        reply = _recv(b)
        assert reply["type"] == "join_denied"
        assert reply["reason"] == "username_taken"
        a.close()
        b.close()
    finally:
        _stop_server(proc)


def test_join_and_leave_notifications():
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        # alice sees bob join
        assert _recv_until(a, lambda m: m.get("type") == "user_joined")["username"] == "bob"

        # bob leaves cleanly
        _send(b, {"type": "leave"})
        left_notice = _recv_until(a, lambda m: m.get("type") == "user_left")
        assert left_notice["username"] == "bob"
        a.close()
        b.close()
    finally:
        _stop_server(proc)


def test_three_clients_one_room():
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]

        clients = [a]
        expected = 1
        for name in ("bob", "carol"):
            c = socket.create_connection(("127.0.0.1", port), timeout=3)
            c.settimeout(3)
            _send(c, {"type": "join_room", "username": name, "room_code": code})
            joined = _recv(c)
            assert joined["type"] == "join_ok"
            expected += 1
            assert len(joined["participants"]) == expected
            clients.append(c)

        # Each joiner count: final room has 3 participants.
        # Ask by re-reading a participant list broadcast.
        # alice should have received participant_list updates; drain briefly.
        time.sleep(0.3)
        for c in clients:
            c.close()
    finally:
        _stop_server(proc)


def test_heartbeat_timeout_detection():
    port = _free_port()
    proc = _start_server(port, "--heartbeat-interval", "0.2", "--heartbeat-timeout", "0.5")
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
        s.settimeout(3)
        _send(s, {"type": "create_room", "username": "alice"})
        assert _recv(s)["type"] == "room_created"

        # Do not answer pings. The connection should be closed by the server.
        deadline = time.time() + 3.0
        closed = False
        while time.time() < deadline:
            try:
                data = s.recv(1024)
                if not data:
                    closed = True
                    break
            except socket.timeout:
                continue
            except OSError:
                closed = True
                break
        assert closed, "server did not close idle connection"
        s.close()
    finally:
        _stop_server(proc)


def test_abrupt_reset_disconnect():
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        # Force a TCP reset on close.
        a.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        a.close()

        notice = _recv_until(b, lambda m: m.get("type") == "user_disconnected")
        assert notice["username"] == "alice"
        b.close()
    finally:
        _stop_server(proc)

    stderr = proc.stderr.read().decode("utf-8", errors="replace")
    assert "ERROR" not in stderr, f"unexpected ERROR log: {stderr}"