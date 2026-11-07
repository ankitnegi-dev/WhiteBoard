"""Integration tests for the Day 1 echo server.

The server is started as a subprocess so the test exercises the real
entry point, argument parsing, and accept loop. No GUI is involved.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import subprocess
import sys
import time
import threading

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


@pytest.fixture
def server():
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, os.path.join(ROOT, "server", "main.py"), "--port", str(port), "--log-level", "WARNING"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_port(port)
    except Exception:
        proc.kill()
        raise
    yield port, proc
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


def _recv_msg(sock: socket.socket) -> dict:
    header = sock.recv(4)
    # recv may return partial; loop to be safe.
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


def _send_msg(sock: socket.socket, obj: dict) -> None:
    raw = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    sock.sendall(struct.pack(">I", len(raw)) + raw)


def test_echo_with_three_simultaneous_clients(server):
    port, _proc = server
    socks = []
    try:
        for _ in range(3):
            s = socket.create_connection(("127.0.0.1", port), timeout=3)
            s.settimeout(3)
            socks.append(s)

        for i, s in enumerate(socks):
            _send_msg(s, {"type": "chat", "text": f"hello-{i}"})

        for i, s in enumerate(socks):
            reply = _recv_msg(s)
            assert reply["echo"] is True
            assert reply["text"] == f"hello-{i}"
    finally:
        for s in socks:
            try:
                s.close()
            except OSError:
                pass


def test_server_survives_garbage_bytes(server):
    port, _proc = server
    # A client that sends random bytes and closes must not take the server down.
    garbage = socket.create_connection(("127.0.0.1", port), timeout=3)
    try:
        garbage.sendall(os.urandom(100))
    finally:
        garbage.close()
    time.sleep(0.3)

    # A fresh client must still get an echo.
    s = socket.create_connection(("127.0.0.1", port), timeout=3)
    s.settimeout(3)
    try:
        _send_msg(s, {"type": "ping", "n": 7})
        reply = _recv_msg(s)
        assert reply["echo"] is True
        assert reply["n"] == 7
    finally:
        s.close()


def test_idle_client_still_echoes(server):
    port, _proc = server
    fake_client = os.path.join(ROOT, "tests", "fake_client.py")
    proc = subprocess.Popen(
        [
            sys.executable,
            fake_client,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--connect-timeout",
            "0.2",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    collected: list[str] = []

    def reader():
        for line in proc.stdout:
            collected.append(line)

    threading.Thread(target=reader, daemon=True).start()
    try:
        # Stay idle longer than the connect timeout. With the bug, the
        # receiver thread would die here with a socket.timeout.
        time.sleep(0.6)
        assert proc.poll() is None, "client exited during idle"
        proc.stdin.write("hello-idle\n")
        proc.stdin.flush()

        deadline = time.time() + 5.0
        while time.time() < deadline:
            if any("hello-idle" in line for line in collected):
                break
            time.sleep(0.1)

        output = "".join(collected)
        assert "hello-idle" in output, f"echo not received; output={output!r}"
        assert "<- error" not in output, f"receiver died; output={output!r}"
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
