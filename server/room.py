"""Room state: participants, op log, undo/redo stacks, chat, lock.

Day 1 only needs the module to exist so the package imports cleanly. The
Room class and its apply_op / snapshot / persistence methods are
implemented in Day 2-4. Keeping this as a documented stub (rather than
omitting it) lets team members import the path early.
"""

from __future__ import annotations

import threading
from typing import Any


class Room:
    """Authoritative state for a single whiteboard room.

    All mutation must happen while holding ``self.lock`` so sequencing of
    ops is atomic. The lock is per-room, so different rooms never block
    each other.
    """

    def __init__(self, code: str) -> None:
        self.code = code
        self.lock = threading.Lock()
        # Filled in by later milestones:
        self.participants: dict[str, Any] = {}
        self.op_log: list[dict[str, Any]] = []
        self.seq: int = 0
        self.chat: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of the room (Day 3)."""
        raise NotImplementedError("Room.snapshot is implemented in Day 3")
