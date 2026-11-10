"""Integration tests for sticky notes."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import subprocess
import sys
import time

import protocol

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
    msgs = []
    deadline = time.time() + timeout
    s.settimeout(0.2)
    while time.time() < deadline:
        try:
            msgs.append(_recv(s, 0.2))
        except socket.timeout:
            break
        except ConnectionError:
            break
    return msgs


def _cleanup():
    rooms = os.path.join(ROOT, "rooms")
    if os.path.isdir(rooms):
        for name in os.listdir(rooms):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(rooms, name))
                except OSError:
                    pass


def _drain(s, timeout=0.5):
    msgs = []
    deadline = time.time() + timeout
    s.settimeout(0.2)
    while time.time() < deadline:
        try:
            msgs.append(_recv(s, 0.2))
        except socket.timeout:
            break
        except ConnectionError:
            break
    return msgs
    rooms = os.path.join(ROOT, "rooms")
    if os.path.isdir(rooms):
        for name in os.listdir(rooms):
            if name.endswith(".json"):
                try:
                    os.remove(os.path.join(rooms, name))
                except OSError:
                    pass


def _hash_objects(objs):
    return hashlib.sha256(json.dumps(sorted(objs, key=lambda o: o.get("id", "")),
                                     sort_keys=True).encode()).hexdigest()


def test_sticky_validation():
    _cleanup()
    port = _free_port()
    proc = _start(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        # valid
        _send(a, {"type": "sticky", "op_id": "v1", "obj_id": "st1", "x": 10, "y": 20,
                  "text": "hello", "color": protocol.STICKY_COLORS[0]})
        time.sleep(0.2)
        msgs = _drain(a, 0.3)
        assert not any(m.get("type") == "error" for m in msgs)

        # text too long
        _send(a, {"type": "sticky", "op_id": "v2", "obj_id": "st2", "x": 10, "y": 20,
                  "text": "x" * 301, "color": protocol.STICKY_COLORS[0]})
        r = _recv(a, timeout=3)
        assert r.get("type") == "error"

        # bad color
        _send(a, {"type": "sticky", "op_id": "v3", "obj_id": "st3", "x": 10, "y": 20,
                  "text": "hi", "color": "#000000"})
        r = _recv(a, timeout=3)
        assert r.get("type") == "error"

        # bad coords
        _send(a, {"type": "sticky", "op_id": "v4", "obj_id": "st4", "x": -1, "y": 20,
                  "text": "hi", "color": protocol.STICKY_COLORS[0]})
        r = _recv(a, timeout=3)
        assert r.get("type") == "error"

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        _recv(b)
        # make bob a viewer
        _send(a, {"type": "role_change", "target": "bob", "role": "viewer"})
        time.sleep(0.2)
        _drain(a, 0.2)
        _drain(b, 0.2)

        _send(b, {"type": "sticky", "op_id": "v5", "obj_id": "st5", "x": 10, "y": 20,
                  "text": "hi", "color": protocol.STICKY_COLORS[0]})
        r = _recv(b, timeout=3)
        assert r.get("type") == "error"
        a.close()
        b.close()
    finally:
        _stop(proc)
        _cleanup()


def test_sticky_concurrent_edit_move():
    _cleanup()
    port = _free_port()
    proc = _start(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        # alice creates a sticky
        _send(a, {"type": "sticky", "op_id": "c0", "obj_id": "S", "x": 10, "y": 20,
                  "text": "start", "color": protocol.STICKY_COLORS[0]})
        time.sleep(0.3)
        _drain(a, 0.3)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        _recv(b)
        _drain(b, 0.3)

        # concurrent edit and move
        _send(a, {"type": "edit_object", "op_id": "ce", "target_id": "S", "text": "edited"})
        _send(b, {"type": "move_object", "op_id": "cm", "target_id": "S", "x": 100, "y": 200})
        time.sleep(0.5)
        time.sleep(0.5)

        # both clients should receive both ops
        def obj_from_msgs(msgs):
            o = None
            for m in msgs:
                if m.get("type") == "sticky":
                    o = m
                elif m.get("type") in ("edit_object", "move_object"):
                    pass
            return o
        # compare via late joiner
        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "join_room", "username": "carol", "room_code": code})
        joined = _recv(c)
        assert joined["type"] == "join_ok"
        objs = joined["snapshot"]["objects"]
        sticky = [o for o in objs if o.get("id") == "S"][0]
        assert sticky["text"] == "edited"
        assert sticky["x"] == 100 and sticky["y"] == 200

        # both original clients' mirrors are not tracked here; identical board hash via late joiner only
        a.close()
        b.close()
        c.close()
    finally:
        _stop(proc)
        _cleanup()


def test_sticky_undo_redo():
    _cleanup()
    port = _free_port()
    proc = _start(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        # bob draws something else
        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        _recv(b)
        _send(b, {"type": "stroke_start", "op_id": "bp", "stroke_id": "st1",
                  "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2})
        _send(b, {"type": "stroke_end", "op_id": "be", "stroke_id": "st1"})
        time.sleep(0.2)
        _drain(a, 0.3)
        _drain(b, 0.3)

        # alice creates, edits, moves
        _send(a, {"type": "sticky", "op_id": "u0", "obj_id": "S", "x": 10, "y": 20,
                  "text": "hello", "color": protocol.STICKY_COLORS[0]})
        time.sleep(0.2)
        _drain(a, 0.2)
        _send(a, {"type": "edit_object", "op_id": "u1", "target_id": "S", "text": "world"})
        time.sleep(0.2)
        _drain(a, 0.2)
        _send(a, {"type": "move_object", "op_id": "u2", "target_id": "S", "x": 30, "y": 40})
        time.sleep(0.2)
        _drain(a, 0.2)

        def late_ids_text_pos():
            c = socket.create_connection(("127.0.0.1", port), timeout=3)
            c.settimeout(3)
            _send(c, {"type": "join_room", "username": f"late{time.time()}", "room_code": code})
            joined = _recv(c)
            c.close()
            objs = joined["snapshot"]["objects"]
            sticky = [o for o in objs if o.get("id") == "S"]
            return joined, sticky

        # undo move
        _send(a, {"type": "undo", "op_id": "um"})
        time.sleep(0.2)
        _drain(a, 0.2)
        joined, sticky = late_ids_text_pos()
        assert sticky and sticky[0]["text"] == "world"
        assert sticky[0]["x"] == 10 and sticky[0]["y"] == 20
        assert "st1" in [o["id"] for o in joined["snapshot"]["objects"]]

        # undo edit
        _send(a, {"type": "undo", "op_id": "ue"})
        time.sleep(0.2)
        _drain(a, 0.2)
        joined, sticky = late_ids_text_pos()
        assert sticky and sticky[0]["text"] == "hello"

        # undo create
        _send(a, {"type": "undo", "op_id": "uc"})
        time.sleep(0.2)
        _drain(a, 0.2)
        joined, sticky = late_ids_text_pos()
        assert not sticky
        assert "st1" in [o["id"] for o in joined["snapshot"]["objects"]]

        # redo create
        _send(a, {"type": "redo", "op_id": "rc"})
        time.sleep(0.2)
        _drain(a, 0.2)
        joined, sticky = late_ids_text_pos()
        assert sticky and sticky[0]["text"] == "hello"

        # redo edit
        _send(a, {"type": "redo", "op_id": "re"})
        time.sleep(0.2)
        _drain(a, 0.2)
        joined, sticky = late_ids_text_pos()
        assert sticky and sticky[0]["text"] == "world"

        # redo move
        _send(a, {"type": "redo", "op_id": "rm"})
        time.sleep(0.2)
        _drain(a, 0.2)
        joined, sticky = late_ids_text_pos()
        assert sticky and sticky[0]["x"] == 30 and sticky[0]["y"] == 40

        a.close()
        b.close()
    finally:
        _stop(proc)
        _cleanup()


def test_sticky_undo_noop_after_delete():
    _cleanup()
    port = _free_port()
    proc = _start(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        code = _recv(a)["room_code"]
        _recv(a)

        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        _recv(b)

        _send(a, {"type": "sticky", "op_id": "n1", "obj_id": "S", "x": 10, "y": 20,
                  "text": "hello", "color": protocol.STICKY_COLORS[0]})
        time.sleep(0.2)
        _drain(a, 0.2)
        _drain(b, 0.2)

        _send(a, {"type": "edit_object", "op_id": "e1", "target_id": "S", "text": "world"})
        time.sleep(0.2)
        _drain(a, 0.2)
        _drain(b, 0.2)

        # bob deletes the sticky
        _send(b, {"type": "delete_object", "op_id": "d1", "target_id": "S"})
        time.sleep(0.2)
        _drain(a, 0.2)
        _drain(b, 0.2)

        # alice undoes the edit: target gone, no-op with notice
        _send(a, {"type": "undo", "op_id": "u1"})
        msgs = _drain(a, 0.5)
        assert any(m.get("type") == "error" for m in msgs)
        err = [m for m in msgs if m.get("type") == "error"][0]
        assert "target" in (err.get("message") or "").lower() or "removed" in (err.get("message") or "").lower()
        a.close()
        b.close()
    finally:
        _stop(proc)
        _cleanup()


def test_sticky_edit_move_nonexistent_rejected():
    _cleanup()
    port = _free_port()
    proc = _start(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        _recv(a)
        _recv(a)

        _send(a, {"type": "edit_object", "op_id": "e1", "target_id": "missing", "text": "x"})
        r = _recv(a, timeout=3)
        assert r.get("type") == "error"

        _send(a, {"type": "move_object", "op_id": "m1", "target_id": "missing", "x": 1, "y": 2})
        r = _recv(a, timeout=3)
        assert r.get("type") == "error"
        a.close()
    finally:
        _stop(proc)
        _cleanup()


def test_sticky_snapshot_reconnect_save_load_clear():
    _cleanup()
    port = _free_port()
    proc = _start(port)
    try:
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        created = _recv(a)
        code = created["room_code"]
        token = created["token"]
        _drain(a, 0.2)

        _send(a, {"type": "sticky", "op_id": "s1", "obj_id": "S", "x": 10, "y": 20,
                  "text": "hello", "color": protocol.STICKY_COLORS[0]})
        time.sleep(0.3)
        _drain(a, 0.3)

        # late joiner snapshot
        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "bob", "room_code": code})
        joined = _recv(b)
        assert joined["type"] == "join_ok"
        ids = [o["id"] for o in joined["snapshot"]["objects"]]
        assert "S" in ids
        b.close()

        # reconnect with last_seq 0 gets the sticky back
        a.close()
        a2 = socket.create_connection(("127.0.0.1", port), timeout=3)
        a2.settimeout(3)
        _send(a2, {"type": "reconnect", "token": token, "room_code": code, "last_seq": 0})
        recon = _recv(a2, timeout=5)
        assert recon["type"] == "reconnect_ok"
        if "snapshot" in recon:
            rids = [o["id"] for o in recon["snapshot"]["objects"]]
            assert "S" in rids
        else:
            ops = recon.get("ops_since", {}).get("ops", [])
            assert any(o.get("type") == "sticky" and o.get("obj_id") == "S" for o in ops)
        a2.close()

        # save, clear, undo clear
        a3 = socket.create_connection(("127.0.0.1", port), timeout=3)
        a3.settimeout(3)
        _send(a3, {"type": "reconnect", "token": token, "room_code": code, "last_seq": 0})
        _recv(a3, timeout=5)
        _drain(a3, 0.3)

        _send(a3, {"type": "save_board", "name": "mixed"})
        assert _recv(a3, timeout=5)["type"] == "save_ok"

        _send(a3, {"type": "clear_canvas", "op_id": "clear1"})
        time.sleep(0.2)
        _drain(a3, 0.3)

        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "join_room", "username": "carol", "room_code": code})
        joined = _recv(c)
        assert "S" not in [o["id"] for o in joined["snapshot"]["objects"]]
        c.close()

        _send(a3, {"type": "undo", "op_id": "undo_clear"})
        time.sleep(0.2)
        _drain(a3, 0.3)

        d = socket.create_connection(("127.0.0.1", port), timeout=3)
        d.settimeout(3)
        _send(d, {"type": "join_room", "username": "dave", "room_code": code})
        joined = _recv(d)
        assert "S" in [o["id"] for o in joined["snapshot"]["objects"]]
        d.close()

        # load restores the saved sticky
        _send(a3, {"type": "load_board", "name": "mixed"})
        assert _recv_until(a3, lambda m: m.get("type") == "load_ok", timeout=5)["type"] == "load_ok"
        time.sleep(0.2)
        _drain(a3, 0.3)

        e = socket.create_connection(("127.0.0.1", port), timeout=3)
        e.settimeout(3)
        _send(e, {"type": "join_room", "username": "eve", "room_code": code})
        joined = _recv(e)
        assert "S" in [o["id"] for o in joined["snapshot"]["objects"]]
        a3.close()
        e.close()
    finally:
        _stop(proc)
        _cleanup()