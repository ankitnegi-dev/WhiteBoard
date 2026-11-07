"""Client network layer: connect, send queue, receive loop, reconnect.

Day 1 stub. The receive loop and auto-reconnect with backoff are
implemented in Day 3-5. The threading rule (network thread never touches
tkinter) is enforced by pushing received messages into a queue.Queue.
"""

from __future__ import annotations

import queue
import threading


class NetworkClient:
    """Owns the socket and the send/receive threads for one client."""

    def __init__(self) -> None:
        self.inbox: queue.Queue = queue.Queue()
        self._send_lock = threading.Lock()
