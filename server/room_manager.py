"""Create and find rooms by their 6-character code.

Day 1 stub. Implementation (unique code generation, empty-room cleanup
after a grace period) lands in Day 2.
"""

from __future__ import annotations

from typing import Optional

from server.room import Room


class RoomManager:
    """Registry mapping room code -> Room."""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}

    def get(self, code: str) -> Optional[Room]:
        return self._rooms.get(code)

    def create(self, code: str) -> Room:
        room = Room(code)
        self._rooms[code] = room
        return room
