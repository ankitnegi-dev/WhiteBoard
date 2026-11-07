"""Tests for the CLI test client's receive loop."""

from __future__ import annotations

import io
import socket
import struct
import threading
import time

import pytest

import tests.fake_client as fc


def _frame(obj):
    import json
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    return struct.pack(">I", len(raw)) + raw


def test_receiver_replies_pong_and_hides_heartbeat_by_default(capsys):
    a, b = socket.socketpair()
    a.settimeout(2.0)
    b.settimeout(2.0)
    stop = threading.Event()
    t = threading.Thread(target=fc.receiver, args=(b, stop, False), daemon=True)
    t.start()
    try:
        # Send a ping from the server side.
        a.sendall(_frame({"type": "ping"}))
        # Read the pong reply.
        header = a.recv(4)
        assert len(header) == 4
        (n,) = struct.unpack(">I", header)
        body = a.recv(n)
        import json
        msg = json.loads(body.decode("utf-8"))
        assert msg == {"type": "pong"}
        # Give the receiver a moment to (not) print.
        time.sleep(0.2)
        captured = capsys.readouterr()
        assert "ping" not in captured.out
    finally:
        stop.set()
        a.close()
        b.close()
        t.join(timeout=2)


def test_receiver_shows_heartbeat_when_enabled(capsys):
    a, b = socket.socketpair()
    a.settimeout(2.0)
    b.settimeout(2.0)
    stop = threading.Event()
    t = threading.Thread(target=fc.receiver, args=(b, stop, True), daemon=True)
    t.start()
    try:
        a.sendall(_frame({"type": "ping"}))
        # Read pong.
        header = a.recv(4)
        (n,) = struct.unpack(">I", header)
        a.recv(n)
        time.sleep(0.2)
        captured = capsys.readouterr()
        assert "ping" in captured.out
    finally:
        stop.set()
        a.close()
        b.close()
        t.join(timeout=2)