"""Tests for protocol framing.

These tests use an in-process socketpair so they exercise the real
recv/send code paths, including partial reads and coalesced messages,
without depending on a running server.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time

import pytest

from protocol import (
    MAX_MESSAGE_SIZE,
    BadMessage,
    ConnectionClosed,
    FrameTooLarge,
    recv_exact,
    recv_msg,
    send_msg,
)


def _pair():
    a, b = socket.socketpair()
    a.settimeout(2.0)
    b.settimeout(2.0)
    return a, b


def test_recv_exact_handles_split_reads():
    a, b = _pair()
    try:
        # Send the bytes in two chunks so recv_exact must loop.
        def sender():
            time.sleep(0.05)
            b.sendall(b"he")
            time.sleep(0.05)
            b.sendall(b"llo")
            b.close()

        threading.Thread(target=sender, daemon=True).start()
        data = recv_exact(a, 5)
        assert data == b"hello"
    finally:
        a.close()
        b.close()


def test_recv_exact_raises_on_clean_eof_mid_frame():
    a, b = _pair()
    try:
        b.sendall(b"he")
        b.close()
        with pytest.raises(ConnectionClosed):
            recv_exact(a, 5)
    finally:
        a.close()
        b.close()


def test_recv_msg_split_across_multiple_recvs():
    a, b = _pair()
    try:
        payload = {"type": "chat", "text": "hi"}
        raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        framed = struct.pack(">I", len(raw)) + raw

        def sender():
            # Split the header and body into separate sends.
            time.sleep(0.05)
            b.sendall(framed[:2])
            time.sleep(0.05)
            b.sendall(framed[2:6])
            time.sleep(0.05)
            b.sendall(framed[6:])

        threading.Thread(target=sender, daemon=True).start()
        msg = recv_msg(a)
        assert msg == payload
    finally:
        a.close()
        b.close()


def test_recv_msg_coalesced_multiple_messages():
    a, b = _pair()
    try:
        m1 = {"type": "a", "n": 1}
        m2 = {"type": "b", "n": 2}
        r1 = json.dumps(m1, separators=(",", ":")).encode("utf-8")
        r2 = json.dumps(m2, separators=(",", ":")).encode("utf-8")
        # Both frames in a single send: the receiver must still split them.
        b.sendall(struct.pack(">I", len(r1)) + r1 + struct.pack(">I", len(r2)) + r2)
        assert recv_msg(a) == m1
        assert recv_msg(a) == m2
    finally:
        a.close()
        b.close()


def test_recv_msg_rejects_oversize_length_before_reading_body():
    a, b = _pair()
    try:
        # Declare a length above the limit but send no body. recv_msg must
        # raise without blocking on recv_exact for the (huge) body.
        b.sendall(struct.pack(">I", MAX_MESSAGE_SIZE + 1))
        b.close()
        with pytest.raises(FrameTooLarge):
            recv_msg(a)
    finally:
        a.close()
        b.close()


def test_recv_msg_rejects_invalid_json():
    a, b = _pair()
    try:
        body = b"{not json"
        b.sendall(struct.pack(">I", len(body)) + body)
        with pytest.raises(BadMessage):
            recv_msg(a)
    finally:
        a.close()
        b.close()


def test_recv_msg_rejects_non_object_json():
    a, b = _pair()
    try:
        body = b"[1, 2, 3]"
        b.sendall(struct.pack(">I", len(body)) + body)
        with pytest.raises(BadMessage):
            recv_msg(a)
    finally:
        a.close()
        b.close()


def test_recv_msg_raises_connection_closed_on_eof_mid_frame():
    a, b = _pair()
    try:
        b.sendall(struct.pack(">I", 100)[:2])
        b.close()
        with pytest.raises(ConnectionClosed):
            recv_msg(a)
    finally:
        a.close()
        b.close()


def test_send_msg_roundtrip():
    a, b = _pair()
    try:
        msg = {"type": "ping", "value": 42}
        send_msg(a, msg)
        assert recv_msg(b) == msg
    finally:
        a.close()
        b.close()
