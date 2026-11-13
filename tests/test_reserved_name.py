"""Tests for reserved usernames and rejoin."""

from __future__ import annotations

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


def _start(port, grace=1.0):
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "main.py"),
         "--port", str(port), "--grace-period", str(grace), "--log-level", "WARNING"],
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


def _drain(s, timeout=0.3):
    msgs = []
    s.settimeout(0.2)
    deadline = time.time() + timeout
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


def test_reserved_name_rejoin_and_expiry():
    _cleanup()
    port = _free_port()
    proc = _start(port, grace=2.0)
    try:
        # alice creates a room and a sticky note
        a = socket.create_connection(("127.0.0.1", port), timeout=3)
        a.settimeout(3)
        _send(a, {"type": "create_room", "username": "alice"})
        created = _recv(a)
        code = created["room_code"]
        token = created["token"]
        color = "#e6194b"
        role = "host"
        _send(a, {"type": "sticky", "op_id": "s1", "obj_id": "note1", "x": 10, "y": 20,
                  "text": "hello", "color": "#FFF2A8"})
        time.sleep(0.2)
        _drain(a, 0.3)

        # close without goodbye
        a.close()
        time.sleep(1.2)

        # same name without token is refused as reserved
        b = socket.create_connection(("127.0.0.1", port), timeout=3)
        b.settimeout(3)
        _send(b, {"type": "join_room", "username": "alice", "room_code": code})
        denied = _recv(b, timeout=3)
        assert denied["type"] == "join_denied"
        assert denied["reason"] == "username_reserved"
        assert "reserved" in denied["message"].lower()
        assert denied.get("remaining_grace", 0) > 0
        b.close()

        # rejoin with the saved token restores color, role and undo history
        c = socket.create_connection(("127.0.0.1", port), timeout=3)
        c.settimeout(3)
        _send(c, {"type": "reconnect", "token": token, "room_code": code, "last_seq": 0})
        recon = _recv(c, timeout=5)
        assert recon["type"] == "reconnect_ok"
        participants = recon.get("participants", [])
        alice_p = [p for p in participants if p.get("name") == "alice"]
        assert alice_p and alice_p[0].get("color") == color
        assert alice_p and alice_p[0].get("role") == role
        # role is not in reconnect_ok; check participant list broadcast
        time.sleep(0.2)
        _drain(c, 0.5)
        # undo the sticky to prove history came back
        _send(c, {"type": "undo", "op_id": "u1"})
        time.sleep(0.3)
        _drain(c, 0.3)
        d = socket.create_connection(("127.0.0.1", port), timeout=3)
        d.settimeout(3)
        _send(d, {"type": "join_room", "username": "dave", "room_code": code})
        joined = _recv(d)
        assert joined["type"] == "join_ok"
        assert "note1" not in [o["id"] for o in joined["snapshot"]["objects"]]
        c.close()
        d.close()

        # after the grace period a plain join works as a new participant
        time.sleep(3.5)
        e = socket.create_connection(("127.0.0.1", port), timeout=3)
        e.settimeout(3)
        _send(e, {"type": "join_room", "username": "alice", "room_code": code})
        joined = _recv(e, timeout=5)
        assert joined["type"] == "join_ok"
        assert joined.get("role") == "editor"
        e.close()
    finally:
        _stop(proc)
        _cleanup()