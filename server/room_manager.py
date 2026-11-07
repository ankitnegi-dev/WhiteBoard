"""Create and find rooms by their 6-character code.

Room codes are short, human-typable and unique within the running server.
Empty rooms are retained for a grace period (implemented later); Day 2
only needs create/find.
"""

from __future__ import annotations

import threading
from typing import Optional

from server.auth import new_room_code
from server.room import Room


class RoomManager:
    """Registry mapping room code -> Room."""

    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        self._lock = threading.Lock()

    def get(self, code: str) -> Optional[Room]:
        with self._lock:
            return self._rooms.get(code)

    def create(self, code: Optional[str] = None, password: Optional[str] = None) -> Room:
        with self._lock:
            if code is None:
                code = new_room_code(set(self._rooms.keys()))
            if code in self._rooms:
                raise ValueError(f"room code already exists: {code}")
            room = Room(code)
            if password:
                from server.auth import hash_password

                salt, digest = hash_password(password)
                room.password_salt = salt
                room.password_hash = digest
            self._rooms[code] = room
            return room

    def remove(self, code: str) -> None:
        with self._lock:
            self._rooms.pop(code, None)

    def codes(self) -> list[str]:
        with self._lock:
            return list(self._rooms.keys())
