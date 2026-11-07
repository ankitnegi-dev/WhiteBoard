"""Tests for the Day 3 shared canvas backend."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

from server.room import Room

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


def _start_server(port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "main.py"), "--port", str(port), "--log-level", "WARNING"],
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


# ------------------------------------------------------------------ unit tests


def test_seq_strictly_increasing_and_dedupe():
    room = Room("TEST")
    op1 = {"type": "stroke_start", "op_id": "o1", "stroke_id": "s1", "tool": "pen",
           "color": "#000000", "width": 2, "x": 1, "y": 2}
    status, stamped, reason = room.apply_op(op1, "alice", "editor")
    assert status == "ok"
    assert stamped["seq"] == 1

    # Duplicate op_id is ignored and does not consume a seq.
    status2, stamped2, reason2 = room.apply_op(dict(op1, stroke_id="s2"), "alice", "editor")
    assert status2 == "duplicate"
    assert stamped2 is None

    op2 = {"type": "stroke_start", "op_id": "o2", "stroke_id": "s2", "tool": "pen",
           "color": "#000000", "width": 2, "x": 3, "y": 4}
    status3, stamped3, reason3 = room.apply_op(op2, "alice", "editor")
    assert status3 == "ok"
    assert stamped3["seq"] == 2
    assert stamped3["seq"] > stamped["seq"]


def test_viewer_cannot_draw():
    room = Room("TEST")
    op = {"type": "stroke_start", "op_id": "o1", "stroke_id": "s1", "tool": "pen",
          "color": "#000000", "width": 2, "x": 1, "y": 2}
    status, stamped, reason = room.apply_op(op, "viewer", "viewer")
    assert status == "invalid"
    assert reason == "forbidden"
    assert stamped is None


def test_malformed_op_rejected():
    room = Room("TEST")
    # Bad color.
    op = {"type": "stroke_start", "op_id": "o1", "stroke_id": "s1", "tool": "pen",
          "color": "red", "width": 2, "x": 1, "y": 2}
    status, stamped, reason = room.apply_op(op, "alice", "editor")
    assert status == "invalid"
    assert reason == "bad_color"

    # Out of range coordinate.
    op2 = {"type": "stroke_start", "op_id": "o2", "stroke_id": "s2", "tool": "pen",
           "color": "#000000", "width": 2, "x": 9999, "y": 2}
    status2, _, reason2 = room.apply_op(op2, "alice", "editor")
    assert status2 == "invalid"
    assert reason2 == "bad_coordinates"

    # Unknown type.
    status3, _, reason3 = room.apply_op({"type": "frobulate", "op_id": "o3"}, "alice", "editor")
    assert status3 == "invalid"
    assert reason3 == "unknown_type"


def test_ops_since_and_snapshot():
    room = Room("TEST")
    op = {"type": "stroke_start", "op_id": "o1", "stroke_id": "s1", "tool": "pen",
          "color": "#000000", "width": 2, "x": 1, "y": 2}
    room.apply_op(op, "alice", "editor")
    snap = room.snapshot()
    assert snap["seq"] == 1
    assert len(snap["objects"]) == 1

    # from_seq 0 returns the op.
    res = room.ops_since(0)
    assert res is not None
    assert len(res["ops"]) == 1
    assert res["ops"][0]["seq"] == 1

    # from_seq current returns empty.
    res2 = room.ops_since(1)
    assert res2 is not None
    assert res2["ops"] == []

    # Huge gap returns None -> caller sends snapshot.
    res3 = room.ops_since(-1)
    assert res3 is None


# ------------------------------------------------------------------ integration


def test_late_joiner_gets_state():
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]

        sid = "stroke-1"
        _send(a, {"type": "stroke_start", "op_id": "op-1", "stroke_id": sid,
                  "tool": "pen", "color": "#000000", "width": 2, "x": 10, "y": 20})
        _send(a, {"type": "stroke_points", "op_id": "op-2", "stroke_id": sid,
                  "points": [[10, 20], [20, 30]]})
        _send(a, {"type": "stroke_end", "op_id": "op-3", "stroke_id": sid})
        # Drain broadcasts.
        time.sleep(0.3)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        joined = _recv(b)
        assert joined["type"] == "join_ok"
        snap = joined["snapshot"]
        assert snap["seq"] >= 3
        assert len(snap["objects"]) == 1
        assert snap["objects"][0]["id"] == sid
        assert len(snap["objects"][0]["points"]) == 2
        a.close()
        b.close()
    finally:
        _stop_server(proc)


def test_chat_history_order_and_timestamps():
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

        _send(a, {"type": "chat", "text": "first"})
        time.sleep(0.2)
        _send(b, {"type": "chat", "text": "second"})
        time.sleep(0.3)

        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "join_room", "username": "carol", "room_code": code})
        joined = _recv(c)
        assert joined["type"] == "join_ok"
        history = joined["chat_history"]
        assert [h["text"] for h in history] == ["first", "second"]
        assert history[0]["timestamp"] <= history[1]["timestamp"]
        a.close()
        b.close()
        c.close()
    finally:
        _stop_server(proc)


def test_malformed_op_rejected_over_wire():
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        _recv(a)  # room_created
        _recv(a)  # participant_list

        # Malformed: bad color.
        _send(a, {"type": "stroke_start", "op_id": "bad-1", "stroke_id": "s-bad",
                  "tool": "pen", "color": "red", "width": 2, "x": 1, "y": 2})
        reply = _recv(a)
        assert reply["type"] == "error"
        assert reply["code"] == "rejected_op"

        # Server still alive: create another room works on a new connection.
        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "create_room", "username": "bob"})
        assert _recv(b)["type"] == "room_created"
        a.close()
        b.close()
    finally:
        _stop_server(proc)


def test_resync_request_returns_ops():
    port = _free_port()
    proc = _start_server(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        _recv(a)  # room_created
        _recv(a)  # participant_list

        sid = "stroke-r"
        _send(a, {"type": "stroke_start", "op_id": "r1", "stroke_id": sid,
                  "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2})
        time.sleep(0.2)

        # Drain the broadcast of the stroke before requesting resync.
        a.settimeout(0.2)
        while True:
            try:
                _recv(a, timeout=0.2)
            except socket.timeout:
                break

        # Request from seq 0 should return the op.
        _send(a, {"type": "resync_request", "last_seq": 0})
        reply = _recv(a)
        assert reply["type"] == "ops_since"
        assert len(reply["ops"]) >= 1
        assert reply["ops"][0]["seq"] == 1

        a.close()
    finally:
        _stop_server(proc)


def test_multi_client_board_hash():
    port = _free_port()
    proc = _start_server(port)
    try:
        # Four clients join one room.
        clients = []
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        clients.append(a)

        for name in ("bob", "carol", "dave"):
            c = socket.create_connection(("127.0.0.1", port), timeout=3)
            c.settimeout(3)
            _send(c, {"type": "join_room", "username": name, "room_code": code})
            assert _recv(c)["type"] == "join_ok"
            clients.append(c)

        # Each client sends one stroke.
        stroke_ids = []
        for idx, c in enumerate(clients):
            sid = f"stroke-{idx}"
            stroke_ids.append(sid)
            _send(c, {"type": "stroke_start", "op_id": f"op-start-{idx}", "stroke_id": sid,
                      "tool": "pen", "color": "#000000", "width": 2, "x": idx, "y": idx})
            _send(c, {"type": "stroke_points", "op_id": f"op-pts-{idx}", "stroke_id": sid,
                      "points": [[idx, idx], [idx + 1, idx + 1]]})
            _send(c, {"type": "stroke_end", "op_id": f"op-end-{idx}", "stroke_id": sid})

        time.sleep(0.5)

        # A late joiner receives the full board.
        late = socket.create_connection(("127.0.0.1", port), timeout=3)
        late.settimeout(3)
        _send(late, {"type": "join_room", "username": "eve", "room_code": code})
        joined = _recv(late)
        assert joined["type"] == "join_ok"
        snap = joined["snapshot"]
        ids = sorted(o["id"] for o in snap["objects"])
        assert ids == sorted(stroke_ids)

        # Hash the canonical board.
        canonical = json.dumps(snap["objects"], sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        assert len(digest) == 64

        for c in clients:
            c.close()
        late.close()
    finally:
        _stop_server(proc)