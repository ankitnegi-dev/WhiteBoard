"""Convergence tests for the server-authoritative board."""

from __future__ import annotations

import hashlib
import json
import os
import random
import socket
import struct
import subprocess
import sys
import time

import protocol
from client.state import BoardState

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


def _start(port):
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "main.py"),
         "--port", str(port), "--log-level", "WARNING"],
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


def _recv(s, timeout=1.0):
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


def _drain(client, timeout=0.2):
    msgs = []
    s = client["sock"]
    s.settimeout(0.05)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            msgs.append(_recv(s, 0.05))
        except socket.timeout:
            break
        except ConnectionError:
            break
    for m in sorted(msgs, key=lambda x: x.get("seq", 0) if isinstance(x, dict) else 0):
        if isinstance(m, dict) and "seq" in m:
            client["state"].apply_op(m)
    return msgs


def _hash_objects(objs):
    return hashlib.sha256(json.dumps(sorted(objs, key=lambda o: o.get("id", "")),
                                     sort_keys=True).encode()).hexdigest()


def _server_hash(port, code):
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.settimeout(3)
    _send(s, {"type": "join_room", "username": f"observer{time.time()}", "room_code": code})
    joined = _recv(s, 5)
    s.close()
    return _hash_objects(joined["snapshot"]["objects"])


def _make_client(port, username, room_code=None, create=False):
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.settimeout(3)
    if create:
        _send(s, {"type": "create_room", "username": username})
        created = _recv(s, 5)
        room_code = created["room_code"]
    else:
        _send(s, {"type": "join_room", "username": username, "room_code": room_code})
        joined = _recv(s, 5)
        assert joined["type"] == "join_ok"
    state = BoardState()
    client = {"sock": s, "state": state, "username": username, "room_code": room_code}
    _drain(client, 0.2)
    return client, room_code

def _assert_convergence(clients, port):
    _drain(clients[0], 1.0)
    _drain(clients[1], 1.0)
    hashes = [board_hash(c["state"]) for c in clients]
    shash = _server_hash(port, clients[0]["room_code"])
    assert len(set(hashes + [shash])) == 1, (hashes, shash)



def board_hash(state):
    return state.board_hash()[1]


def _owned_objects(state, username):
    return [oid for oid, obj in state.objects.items()
            if obj.get("user") == username and obj.get("type") in
            ("stroke", "shape", "text", "sticky")]


def test_convergence_scripted():
    port = _free_port()
    proc = _start(port)
    try:
        alice, code = _make_client(port, "alice", create=True)
        bob, _ = _make_client(port, "bob", room_code=code)
        clients = [alice, bob]
        rng = random.Random(20260309)

        for step in range(1, 41):
            c = rng.choice(clients)
            other = [x for x in clients if x is not c][0]
            action = rng.choice(["stroke", "shape", "text", "sticky", "move", "edit",
                                 "erase_own", "erase_other"])
            if action == "stroke":
                oid = f"{c['username']}-stroke-{step}"
                _send(c["sock"], {"type": "stroke_start", "op_id": f"o{step}", "stroke_id": oid,
                                  "tool": "pen", "color": "#123456", "width": 2, "x": 10, "y": 20})
                _send(c["sock"], {"type": "stroke_end", "op_id": f"o{step}e", "stroke_id": oid})
            elif action == "shape":
                oid = f"{c['username']}-shape-{step}"
                _send(c["sock"], {"type": "shape", "op_id": f"o{step}", "shape_id": oid,
                                  "kind": "rectangle", "p1": [1, 2], "p2": [30, 40],
                                  "color": "#123456", "width": 2, "fill": None})
            elif action == "text":
                oid = f"{c['username']}-text-{step}"
                _send(c["sock"], {"type": "text", "op_id": f"o{step}", "obj_id": oid,
                                  "x": 5, "y": 6, "text": f"hello {step}", "size": 12,
                                  "color": "#222222"})
            elif action == "sticky":
                oid = f"{c['username']}-sticky-{step}"
                _send(c["sock"], {"type": "sticky", "op_id": f"o{step}", "obj_id": oid,
                                  "x": 10, "y": 20, "text": f"note {step}",
                                  "color": protocol.STICKY_COLORS[0]})
            elif action == "move":
                owned = _owned_objects(c["state"], c["username"]) + _owned_objects(other["state"], other["username"])
                if owned:
                    oid = rng.choice(owned)
                    _send(c["sock"], {"type": "move_object", "op_id": f"o{step}", "target_id": oid,
                                      "x": 50, "y": 60})
            elif action == "edit":
                stickies = [oid for oid in c["state"].objects if "sticky" in oid]
                if stickies:
                    oid = rng.choice(stickies)
                    _send(c["sock"], {"type": "edit_object", "op_id": f"o{step}", "target_id": oid,
                                      "text": f"edited {step}"})
            elif action == "erase_own":
                owned = _owned_objects(c["state"], c["username"])
                if owned:
                    oid = rng.choice(owned)
                    _send(c["sock"], {"type": "delete_object", "op_id": f"o{step}", "target_id": oid})
            elif action == "erase_other":
                other_owned = _owned_objects(other["state"], other["username"])
                if other_owned:
                    oid = rng.choice(other_owned)
                    _send(c["sock"], {"type": "delete_object", "op_id": f"o{step}", "target_id": oid})
            elif action == "undo":
                _send(c["sock"], {"type": "undo", "op_id": f"o{step}"})
            elif action == "redo":
                _send(c["sock"], {"type": "redo", "op_id": f"o{step}"})
            _drain(alice, 0.2)
            _drain(bob, 0.2)
            if step % 10 == 0:
                _assert_convergence(clients, port)

        _drain(alice, 0.5)
        _drain(bob, 0.5)
        with open(r"C:\Users\ANKIT\AppData\Local\Temp\opencode\final87.txt", "w") as f:
            f.write("alice has shape: " + str("alice-shape-87" in alice["state"].objects) + "\n")
            f.write("bob has shape: " + str("alice-shape-87" in bob["state"].objects) + "\n")
            f.write("alice applied: " + str(alice["state"].applied_seq) + " latest: " + str(alice["state"].latest_seq) + "\n")
        _assert_convergence(clients, port)
        alice["sock"].close()
        bob["sock"].close()
    finally:
        _stop(proc)


def test_convergence_random_200():
    port = _free_port()
    proc = _start(port)
    try:
        alice, code = _make_client(port, "alice", create=True)
        bob, _ = _make_client(port, "bob", room_code=code)
        clients = [alice, bob]
        rng = random.Random(12345)

        for step in range(1, 201):
            c = rng.choice(clients)
            other = [x for x in clients if x is not c][0]
            action = rng.choice(["stroke", "shape", "text", "sticky", "move", "edit",
                                 "erase_own", "erase_other"])
            if action == "stroke":
                oid = f"{c['username']}-stroke-{step}"
                _send(c["sock"], {"type": "stroke_start", "op_id": f"r{step}", "stroke_id": oid,
                                  "tool": "pen", "color": "#123456", "width": 2, "x": 10, "y": 20})
                _send(c["sock"], {"type": "stroke_end", "op_id": f"r{step}e", "stroke_id": oid})
            elif action == "shape":
                oid = f"{c['username']}-shape-{step}"
                _send(c["sock"], {"type": "shape", "op_id": f"r{step}", "shape_id": oid,
                                  "kind": "rectangle", "p1": [1, 2], "p2": [30, 40],
                                  "color": "#123456", "width": 2, "fill": None})
            elif action == "text":
                oid = f"{c['username']}-text-{step}"
                _send(c["sock"], {"type": "text", "op_id": f"r{step}", "obj_id": oid,
                                  "x": 5, "y": 6, "text": f"hello {step}", "size": 12,
                                  "color": "#222222"})
            elif action == "sticky":
                oid = f"{c['username']}-sticky-{step}"
                _send(c["sock"], {"type": "sticky", "op_id": f"r{step}", "obj_id": oid,
                                  "x": 10, "y": 20, "text": f"note {step}",
                                  "color": protocol.STICKY_COLORS[0]})
            elif action == "move":
                owned = _owned_objects(c["state"], c["username"]) + _owned_objects(other["state"], other["username"])
                if owned:
                    oid = rng.choice(owned)
                    _send(c["sock"], {"type": "move_object", "op_id": f"r{step}", "target_id": oid,
                                      "x": 50, "y": 60})
            elif action == "edit":
                stickies = [oid for oid in c["state"].objects if "sticky" in oid]
                if stickies:
                    oid = rng.choice(stickies)
                    _send(c["sock"], {"type": "edit_object", "op_id": f"r{step}", "target_id": oid,
                                      "text": f"edited {step}"})
            elif action == "erase_own":
                owned = _owned_objects(c["state"], c["username"])
                if owned:
                    oid = rng.choice(owned)
                    _send(c["sock"], {"type": "delete_object", "op_id": f"r{step}", "target_id": oid})
            elif action == "erase_other":
                other_owned = _owned_objects(other["state"], other["username"])
                if other_owned:
                    oid = rng.choice(other_owned)
                    _send(c["sock"], {"type": "delete_object", "op_id": f"r{step}", "target_id": oid})
            elif action == "undo":
                _send(c["sock"], {"type": "undo", "op_id": f"r{step}"})
            elif action == "redo":
                _send(c["sock"], {"type": "redo", "op_id": f"r{step}"})
            _drain(alice, 0.2)
            _drain(bob, 0.2)
            if step % 10 == 0:
                _assert_convergence(clients, port)

        _drain(alice, 0.5)
        _drain(bob, 0.5)
        with open(r"C:\Users\ANKIT\AppData\Local\Temp\opencode\final87.txt", "w") as f:
            f.write("alice has shape: " + str("alice-shape-87" in alice["state"].objects) + "\n")
            f.write("bob has shape: " + str("alice-shape-87" in bob["state"].objects) + "\n")
            f.write("alice applied: " + str(alice["state"].applied_seq) + " latest: " + str(alice["state"].latest_seq) + "\n")
        _assert_convergence(clients, port)
        alice["sock"].close()
        bob["sock"].close()
    finally:
        _stop(proc)