"""Client network layer.

The network thread never touches tkinter. It owns the socket, a send queue,
and a receive loop. Incoming messages are pushed into ``self.inbox`` (a
queue.Queue) and the UI polls that queue from the main thread.

This split is the single most important rule in the client: tkinter is
not thread-safe, so all widget access stays on the UI thread.
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from typing import Any, Optional

from protocol import ConnectionClosed, recv_msg, send_msg


class NetworkClient:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.inbox: queue.Queue = queue.Queue()
        self.send_queue: queue.Queue = queue.Queue()
        self._send_lock = threading.Lock()
        self._stop = threading.Event()
        self._sender: Optional[threading.Thread] = None
        self._receiver: Optional[threading.Thread] = None
        self.connected = False

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #
    def connect(self, timeout: float = 5.0, stop_event: Optional[threading.Event] = None) -> None:
        """Blocking connect. Raises on failure. Starts worker threads."""
        if stop_event is not None and stop_event.is_set():
            raise RuntimeError("cancelled")
        sock = socket.create_connection((self.host, self.port), timeout=timeout)
        sock.settimeout(None)
        if stop_event is not None and stop_event.is_set():
            try:
                sock.close()
            except OSError:
                pass
            raise RuntimeError("cancelled")
        self.sock = sock
        self.connected = True
        self._stop.clear()
        self._sender = threading.Thread(target=self._sender_loop, name="net-sender", daemon=True)
        self._receiver = threading.Thread(target=self._receiver_loop, name="net-receiver", daemon=True)
        self._sender.start()
        self._receiver.start()

    def disconnect(self) -> None:
        self._stop.set()
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        if self._sender is not None:
            self._sender.join(timeout=1.0)
        if self._receiver is not None:
            self._receiver.join(timeout=1.0)
        self.connected = False

    # ------------------------------------------------------------------ #
    # Sending
    # ------------------------------------------------------------------ #
    def send(self, msg: dict[str, Any]) -> None:
        if self._stop.is_set():
            return
        self.send_queue.put(msg)

    def create_room(self, username: str, password: Optional[str] = None) -> None:
        self.send({"type": "create_room", "username": username, "password": password})

    def join_room(self, username: str, room_code: str, password: Optional[str] = None) -> None:
        self.send({"type": "join_room", "username": username, "room_code": room_code, "password": password})

    def reconnect(self, token: str, room_code: str, last_seq: int = 0) -> None:
        self.send({"type": "reconnect", "token": token, "room_code": room_code, "last_seq": last_seq})

    def wait_for(self, types: set[str], timeout: float = 5.0) -> Optional[dict[str, Any]]:
        """Block until a message whose type is in ``types`` arrives."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = self.inbox.get(timeout=0.1)
            except queue.Empty:
                continue
            if msg.get("type") in types:
                return msg
            # Put unrelated messages back? They are already consumed. For the
            # connect flow we only expect one response, so dropping is fine.
        return None

    # ------------------------------------------------------------------ #
    # Worker loops
    # ------------------------------------------------------------------ #
    def _sender_loop(self) -> None:
        sock = self.sock
        assert sock is not None
        while not self._stop.is_set():
            try:
                msg = self.send_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                with self._send_lock:
                    send_msg(sock, msg)
            except (ConnectionClosed, OSError, ValueError):
                self._stop.set()
                self.inbox.put({"type": "_disconnected", "reason": "send failed"})
                break

    def _receiver_loop(self) -> None:
        sock = self.sock
        assert sock is not None
        while not self._stop.is_set():
            try:
                msg = recv_msg(sock)
            except socket.timeout:
                continue
            except ConnectionClosed:
                self._stop.set()
                self.inbox.put({"type": "_disconnected", "reason": "connection closed"})
                break
            except Exception as exc:  # pragma: no cover - defensive
                self._stop.set()
                self.inbox.put({"type": "_disconnected", "reason": str(exc)})
                break

            if not isinstance(msg, dict):
                continue
            mtype = msg.get("type")
            # The server pings us; reply with pong and do not bother the UI.
            if mtype == "ping":
                self.send({"type": "pong"})
                continue
            self.inbox.put(msg)