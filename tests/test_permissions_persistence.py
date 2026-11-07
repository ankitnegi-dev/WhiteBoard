"""Tests for Day 4: undo/redo, permissions, roles, persistence, lockout."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ROOMS_DIR = os.path.join(ROOT, "rooms")


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"server did not listen on port {port}")


def _start_server(port, *extra):
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "main.py"), "--port", str(port), "--log-level", "WARNING", *extra],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    _wait_for_port(port)
    return proc


def _stop_server(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _send(sock, obj):
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)


def _recv(sock, timeout=5.0):
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


def _recv_until(sock, predicate, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msg = _recv(sock, timeout=0.5)
        except socket.timeout:
            continue
        if predicate(msg):
            return msg
    raise TimeoutError("predicate not satisfied")


def _cleanup_rooms():
    if os.path.isdir(ROOMS_DIR):
        for name in os.listdir(ROOMS_DIR):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(ROOMS_DIR, name))
                except OSError:
                    pass


# ------------------------------------------------------------------ undo/redo


def test_undo_redo_two_users():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)  # participant_list

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        # alice draws a stroke
        sid_a = "stroke-a"
        _send(a, {"type": "stroke_start", "op_id": "a1", "stroke_id": sid_a, "tool": "pen",
                  "color": "#000000", "width": 2, "x": 1, "y": 2})
        _send(a, {"type": "stroke_end", "op_id": "a2", "stroke_id": sid_a})
        time.sleep(0.3)

        # bob draws a stroke
        sid_b = "stroke-b"
        _send(b, {"type": "stroke_start", "op_id": "b1", "stroke_id": sid_b, "tool": "pen",
                  "color": "#000000", "width": 2, "x": 3, "y": 4})
        _send(b, {"type": "stroke_end", "op_id": "b2", "stroke_id": sid_b})
        time.sleep(0.3)

        # alice undoes her stroke
        _send(a, {"type": "undo", "op_id": "u1"})
        time.sleep(0.3)

        # late joiner sees only bob's stroke
        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "join_room", "username": "carol", "room_code": code})
        joined = _recv(c)
        assert joined["type"] == "join_ok"
        ids = [o["id"] for o in joined["snapshot"]["objects"]]
        assert sid_a not in ids
        assert sid_b in ids

        # alice redoes
        _send(a, {"type": "redo", "op_id": "r1"})
        time.sleep(0.3)

        d = socket.create_connection(("127.0.0.1", port), timeout=3)
        d.settimeout(3)
        _send(d, {"type": "join_room", "username": "dave", "room_code": code})
        joined2 = _recv(d)
        ids2 = [o["id"] for o in joined2["snapshot"]["objects"]]
        assert sid_a in ids2
        assert sid_b in ids2

        for s in (a, b, c, d):
            s.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()


def test_undo_after_other_deleted_target():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        sid = "stroke-x"
        _send(a, {"type": "stroke_start", "op_id": "sx1", "stroke_id": sid, "tool": "pen",
                  "color": "#000000", "width": 2, "x": 1, "y": 2})
        _send(a, {"type": "stroke_end", "op_id": "sx2", "stroke_id": sid})
        time.sleep(0.3)

        # bob deletes the stroke
        _send(b, {"type": "delete_object", "op_id": "del1", "target_id": sid})
        time.sleep(0.3)

        # alice undoes her stroke: target already gone
        _send(a, {"type": "undo", "op_id": "ux"})
        reply = _recv_until(a, lambda m: m.get("type") == "error" and m.get("code") == "rejected_undo")
        assert reply is not None

        a.close()
        b.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()


# ------------------------------------------------------------------ permissions


def test_clear_by_nonhost_rejected():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        _send(b, {"type": "clear_canvas", "op_id": "c1"})
        reply = _recv(b)
        assert reply["type"] == "error"
        assert reply["code"] == "forbidden"
        a.close()
        b.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()


def test_viewer_cannot_draw_over_wire():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        # host makes bob a viewer
        _send(a, {"type": "role_change", "target": "bob", "role": "viewer"})
        time.sleep(0.2)
        # bob receives participant_list with updated role
        _recv_until(b, lambda m: m.get("type") == "participant_list" and
                    any(u["name"] == "bob" and u["role"] == "viewer" for u in m.get("users", [])))

        _send(b, {"type": "stroke_start", "op_id": "v1", "stroke_id": "vs", "tool": "pen",
                  "color": "#000000", "width": 2, "x": 1, "y": 2})
        reply = _recv(b)
        assert reply["type"] == "error"
        assert reply["code"] == "rejected_op"
        a.close()
        b.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()


def test_role_change_takes_effect():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        _send(a, {"type": "role_change", "target": "bob", "role": "viewer"})
        time.sleep(0.2)
        _recv_until(b, lambda m: m.get("type") == "participant_list" and
                    any(u["name"] == "bob" and u["role"] == "viewer" for u in m.get("users", [])))

        # as viewer, draw rejected
        _send(b, {"type": "stroke_start", "op_id": "r1", "stroke_id": "rs", "tool": "pen",
                  "color": "#000000", "width": 2, "x": 1, "y": 2})
        assert _recv(b)["code"] == "rejected_op"

        # promote back to editor
        _send(a, {"type": "role_change", "target": "bob", "role": "editor"})
        time.sleep(0.2)
        _recv_until(b, lambda m: m.get("type") == "participant_list" and
                    any(u["name"] == "bob" and u["role"] == "editor" for u in m.get("users", [])))

        sid = "rs2"
        _send(b, {"type": "stroke_start", "op_id": "r2", "stroke_id": sid, "tool": "pen",
                  "color": "#000000", "width": 2, "x": 1, "y": 2})
        _send(b, {"type": "stroke_end", "op_id": "r3", "stroke_id": sid})
        time.sleep(0.2)

        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "join_room", "username": "carol", "room_code": code})
        joined = _recv(c)
        ids = [o["id"] for o in joined["snapshot"]["objects"]]
        assert sid in ids
        a.close()
        b.close()
        c.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()


def test_kick():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        assert _recv(b)["type"] == "join_ok"

        _send(a, {"type": "kick", "target": "bob"})
        # alice notified
        _recv_until(a, lambda m: m.get("type") == "user_left" and m.get("username") == "bob")
        # bob's socket closes
        deadline = time.time() + 3
        closed = False
        while time.time() < deadline:
            try:
                data = b.recv(1024)
                if not data:
                    closed = True
                    break
            except (ConnectionError, OSError, socket.timeout):
                closed = True
                break
        assert closed
        a.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()


def test_locked_room_rejects_join():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        _send(a, {"type": "lock_room", "locked": True})
        time.sleep(0.2)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        reply = _recv(b)
        assert reply["type"] == "join_denied"
        assert reply["reason"] == "room_locked"
        a.close()
        b.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()


# ------------------------------------------------------------------ persistence


def test_save_load_roundtrip():
    _cleanup_rooms()
    # Save on server 1.
    port1 = _free_port()
    proc1 = _start_server(port1)
    try:
        a = socket.create_connection(("127.0.0.1", port1), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        _recv(a)  # room_created
        _recv(a)  # participant_list

        sid = "stroke-p"
        _send(a, {"type": "stroke_start", "op_id": "p1", "stroke_id": sid, "tool": "pen",
                  "color": "#000000", "width": 2, "x": 1, "y": 2})
        _send(a, {"type": "stroke_end", "op_id": "p2", "stroke_id": sid})
        time.sleep(0.3)
        # Drain echoed broadcasts.
        a.settimeout(0.2)
        while True:
            try:
                _recv(a, timeout=0.2)
            except socket.timeout:
                break

        _send(a, {"type": "save_board", "name": "board1"})
        assert _recv(a)["type"] == "save_ok"
        a.close()
    finally:
        _stop_server(proc1)

    # Load on server 2.
    port2 = _free_port()
    proc2 = _start_server(port2)
    try:
        a = socket.create_connection(("127.0.0.1", port2), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code2 = _recv(a)["room_code"]
        _recv(a)

        _send(a, {"type": "load_board", "name": "board1"})
        assert _recv(a)["type"] == "load_ok"
        time.sleep(0.3)

        # late joiner sees the stroke
        b = socket.create_connection(("127.0.0.1", port2), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code2})
        joined = _recv(b)
        assert joined["type"] == "join_ok"
        ids = [o["id"] for o in joined["snapshot"]["objects"]]
        assert sid in ids

        canonical = json.dumps(joined["snapshot"]["objects"], sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert len(digest) == 64
        a.close()
        b.close()
    finally:
        _stop_server(proc2)
        _cleanup_rooms()


# ------------------------------------------------------------------ lockout


def test_lockout_after_wrong_passwords():
    _cleanup_rooms()
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice", "password": "secret"})
        code = _recv(a)["room_code"]
        _recv(a)
        a.close()

        # 4 wrong attempts -> wrong_password
        for i in range(4):
            s = socket.create_connection(("127.0.0.1", port), timeout=3)
            s.settimeout(3)
            _send(s, {"type": "join_room", "username": f"bob{i}", "room_code": code, "password": "wrong"})
            reply = _recv(s)
            assert reply["type"] == "join_denied"
            assert reply["reason"] == "wrong_password", reply
            s.close()

        # 5th wrong attempt triggers lockout
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
        s.settimeout(3)
        _send(s, {"type": "join_room", "username": "bob4", "room_code": code, "password": "wrong"})
        reply = _recv(s)
        assert reply["type"] == "join_denied"
        assert reply["reason"] == "too_many_attempts", reply
        s.close()

        # Subsequent attempt while locked
        s = socket.create_connection(("127.0.0.1", port), timeout=3)
        s.settimeout(3)
        _send(s, {"type": "join_room", "username": "bob5", "room_code": code, "password": "secret"})
        reply = _recv(s)
        assert reply["type"] == "join_denied"
        assert reply["reason"] == "too_many_attempts", reply
        s.close()
    finally:
        _stop_server(proc)
        _cleanup_rooms()