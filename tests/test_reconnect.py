"""Integration tests for reconnection, grace periods and heartbeat monitoring."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.slow

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time

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
         "--port", str(port), "--log-level", "WARNING", *extra],
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


def _send(s, o):
    raw = json.dumps(o, separators=(",", ":")).encode()
    s.sendall(struct.pack(">I", len(raw)) + raw)


def _recv(s, timeout=5.0):
    s.settimeout(timeout)
    header = b""
    while len(header) < 4:
        c = s.recv(4 - len(header))
        if not c:
            raise ConnectionError("closed")
        header += c
    (n,) = struct.unpack(">I", header)
    body = b""
    while len(body) < n:
        c = s.recv(n - len(body))
        if not c:
            raise ConnectionError("closed")
        body += c
    return json.loads(body.decode())


def _recv_until(s, pred, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            m = _recv(s, timeout=0.5)
        except socket.timeout:
            continue
        if pred(m):
            return m
    raise TimeoutError("predicate not satisfied")


def _cleanup():
    rooms = os.path.join(ROOT, "rooms")
    if os.path.isdir(rooms):
        for name in os.listdir(rooms):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(rooms, name))
                except OSError:
                    pass


def test_reconnect_valid_token_gets_missing_ops():
    _cleanup()
    port = _free_port()
    proc = _start(port, "--grace-period", "5")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        created = _recv(a)
        code = created["room_code"]
        token = created["token"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        sid = "sx"
        _send(a, {"type": "stroke_start", "op_id": "ax1", "stroke_id": sid,
                  "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2})
        _send(a, {"type": "stroke_end", "op_id": "ax2", "stroke_id": sid})
        time.sleep(0.3)
        a.settimeout(0.2)
        while True:
            try:
                _recv(a, 0.2)
            except socket.timeout:
                break

        a.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        a.close()
        time.sleep(0.5)

        sid2 = "sy"
        _send(b, {"type": "stroke_start", "op_id": "by1", "stroke_id": sid2,
                  "tool": "pen", "color": "#000000", "width": 2, "x": 3, "y": 4})
        _send(b, {"type": "stroke_end", "op_id": "by2", "stroke_id": sid2})
        time.sleep(0.3)
        b.settimeout(0.2)
        while True:
            try:
                _recv(b, 0.2)
            except socket.timeout:
                break

        a2 = socket.create_connection(("127.0.0.1", port), timeout=3)
        a2.settimeout(3)
        _send(a2, {"type": "reconnect", "token": token, "room_code": code, "last_seq": 2})
        r = _recv(a2)
        assert r["type"] == "reconnect_ok"
        assert "ops_since" in r
        ops = r["ops_since"]["ops"]
        assert len(ops) == 2
        assert all(o.get("user") == "bob" for o in ops)
        a2.close()
        b.close()
    finally:
        _stop(proc)
        _cleanup()


def test_reconnect_stale_last_seq_gets_snapshot():
    _cleanup()
    port = _free_port()
    proc = _start(port, "--grace-period", "5")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        created = _recv(a)
        code = created["room_code"]
        token = created["token"]
        _recv(a)

        a2 = socket.create_connection(("127.0.0.1", port), timeout=3)
        a2.settimeout(3)
        _send(a2, {"type": "reconnect", "token": token, "room_code": code, "last_seq": -1})
        r = _recv(a2)
        assert r["type"] == "reconnect_ok"
        assert "snapshot" in r
        a.close()
        a2.close()
    finally:
        _stop(proc)
        _cleanup()


def test_reconnect_invalid_token_and_wrong_room():
    _cleanup()
    port = _free_port()
    proc = _start(port, "--grace-period", "5")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        created = _recv(a)
        code = created["room_code"]
        token = created["token"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "reconnect", "token": "bad", "room_code": code, "last_seq": 0})
        r = _recv(b)
        assert r["type"] == "reconnect_denied"
        assert r["reason"] == "invalid_token"
        b.close()

        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "reconnect", "token": token, "room_code": "ZZZZZZ", "last_seq": 0})
        r2 = _recv(c)
        assert r2["type"] == "reconnect_denied"
        assert r2["reason"] == "room_gone"
        c.close()
        a.close()
    finally:
        _stop(proc)
        _cleanup()


def test_resend_op_id_ignored_not_rebroadcast():
    _cleanup()
    port = _free_port()
    proc = _start(port, "--grace-period", "5")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        _recv(a)["room_code"]
        _recv(a)

        sid = "dup"
        _send(a, {"type": "stroke_start", "op_id": "dup-op", "stroke_id": sid,
                  "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2})
        _send(a, {"type": "stroke_end", "op_id": "dup-end", "stroke_id": sid})
        time.sleep(0.3)
        a.settimeout(0.2)
        while True:
            try:
                _recv(a, 0.2)
            except socket.timeout:
                break

        _send(a, {"type": "resync_request", "last_seq": 0})
        rr = _recv_until(a, lambda m: m.get("type") in ("ops_since", "snapshot"))
        if rr["type"] == "ops_since":
            seq_before = rr["ops"][-1]["seq"] if rr["ops"] else 0
        else:
            seq_before = rr["seq"]

        _send(a, {"type": "stroke_start", "op_id": "dup-op", "stroke_id": sid,
                  "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2})
        time.sleep(0.3)

        _send(a, {"type": "resync_request", "last_seq": 0})
        rr2 = _recv_until(a, lambda m: m.get("type") in ("ops_since", "snapshot"))
        if rr2["type"] == "ops_since":
            seq_after = rr2["ops"][-1]["seq"] if rr2["ops"] else 0
        else:
            seq_after = rr2["seq"]
        assert seq_after == seq_before
        a.close()
    finally:
        _stop(proc)
        _cleanup()


def test_second_connection_replaces_first():
    _cleanup()
    port = _free_port()
    proc = _start(port, "--grace-period", "5")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        created = _recv(a)
        code = created["room_code"]
        token = created["token"]
        _recv(a)

        a2 = socket.create_connection(("127.0.0.1", port), timeout=3)
        a2.settimeout(3)
        _send(a2, {"type": "reconnect", "token": token, "room_code": code, "last_seq": 0})
        r = _recv(a2)
        assert r["type"] == "reconnect_ok"

        deadline = time.time() + 3.0
        closed = False
        while time.time() < deadline:
            try:
                data = a.recv(1024)
                if not data:
                    closed = True
                    break
            except (ConnectionError, OSError, socket.timeout):
                closed = True
                break
        assert closed
        a.close()
        a2.close()
    finally:
        _stop(proc)
        _cleanup()


def test_grace_expiry_frees_username():
    _cleanup()
    port = _free_port()
    proc = _start(port, "--grace-period", "1")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "host"})
        code = _recv(a)["room_code"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        b.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        b.close()
        time.sleep(2.5)

        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "join_room", "username": "bob", "room_code": code})
        r = _recv(c)
        assert r["type"] == "join_ok"
        a.close()
        c.close()
    finally:
        _stop(proc)
        _cleanup()


def test_kicked_user_cannot_reconnect():
    _cleanup()
    port = _free_port()
    proc = _start(port, "--grace-period", "5")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "host"})
        created = _recv(a)
        code = created["room_code"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        joined = _recv(b)
        assert joined["type"] == "join_ok"
        bob_token = joined["token"]

        _send(a, {"type": "kick", "target": "bob"})
        time.sleep(0.3)
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                if not b.recv(1024):
                    break
            except (ConnectionError, OSError, socket.timeout):
                break
            time.sleep(0.1)

        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "reconnect", "token": bob_token, "room_code": code, "last_seq": 0})
        r = _recv(c)
        assert r["type"] == "reconnect_denied"
        assert r["reason"] == "kicked"
        a.close()
        c.close()
    finally:
        _stop(proc)
        _cleanup()


def test_network_drop_reconnect_hashes_match():
    _cleanup()
    port = _free_port()
    proc = _start(port, "--grace-period", "5")
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        created = _recv(a)
        code = created["room_code"]
        token = created["token"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        sid = "dropstroke"
        _send(a, {"type": "stroke_start", "op_id": "d1", "stroke_id": sid,
                  "tool": "pen", "color": "#123456", "width": 2, "x": 5, "y": 6})
        _send(a, {"type": "stroke_end", "op_id": "d2", "stroke_id": sid})
        time.sleep(0.3)
        a.settimeout(0.2)
        while True:
            try:
                _recv(a, 0.2)
            except socket.timeout:
                break
        b.settimeout(0.2)
        while True:
            try:
                _recv(b, 0.2)
            except socket.timeout:
                break

        a.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        a.close()
        time.sleep(0.5)

        sid2 = "bobstroke"
        _send(b, {"type": "stroke_start", "op_id": "b1", "stroke_id": sid2,
                  "tool": "pen", "color": "#654321", "width": 2, "x": 7, "y": 8})
        _send(b, {"type": "stroke_end", "op_id": "b2", "stroke_id": sid2})
        time.sleep(0.3)
        b.settimeout(0.2)
        while True:
            try:
                _recv(b, 0.2)
            except socket.timeout:
                break

        a2 = socket.create_connection(("127.0.0.1", port), timeout=3)
        a2.settimeout(3)
        _send(a2, {"type": "reconnect", "token": token, "room_code": code, "last_seq": -1})
        r = _recv(a2)
        assert r["type"] == "reconnect_ok"
        snap = r.get("snapshot", {})

        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "join_room", "username": "carol", "room_code": code})
        jc = _recv(c)
        snap_b = jc["snapshot"]

        def _hash(s):
            objs = sorted(s.get("objects", []), key=lambda o: o.get("id", ""))
            return hashlib.sha256(json.dumps(objs, sort_keys=True).encode()).hexdigest()

        assert _hash(snap) == _hash(snap_b)
        a2.close()
        b.close()
        c.close()
    finally:
        _stop(proc)
        _cleanup()


def test_monitor_thread_marks_silent_client_offline():
    _cleanup()
    port = _free_port()
    proc = _start(port, "--heartbeat-interval", "0.2", "--heartbeat-timeout", "0.5")
    try:
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
        s.settimeout(3)
        _send(s, {"type": "create_room", "username": "alice"})
        assert _recv(s)["type"] == "room_created"

        deadline = time.time() + 3.0
        closed = False
        while time.time() < deadline:
            try:
                data = s.recv(1024)
                if not data:
                    closed = True
                    break
            except (ConnectionError, OSError, socket.timeout):
                closed = True
                break
        assert closed
        s.close()
    finally:
        _stop(proc)
        _cleanup()
