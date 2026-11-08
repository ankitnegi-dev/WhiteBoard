"""Save and load room state to JSON files under rooms/.

Persistence is deliberately simple and human-readable so it can be
explained and inspected during the demo. Only the standard library is
used.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

ROOMS_DIR = Path("rooms")


def set_rooms_dir(path: Any) -> None:
    """Override the directory used for saved rooms (configurable via CLI)."""
    global ROOMS_DIR
    ROOMS_DIR = Path(path)


def rooms_dir() -> Path:
    ROOMS_DIR.mkdir(parents=True, exist_ok=True)
    return ROOMS_DIR


def _room_path(code: str) -> Path:
    return rooms_dir() / f"{code}.json"


def save_room(room: Any) -> None:
    """Save a room's op log, objects, chat and metadata to JSON by room code."""
    _save(room, room.code)


def save_board(room: Any, name: str) -> None:
    """Save a room's board to rooms/<name>.json."""
    _save(room, name)


def _save(room: Any, name: str) -> None:
    with room.lock:
        payload = {
            "code": room.code,
            "seq": room.seq,
            "op_log": [dict(op) for op in room.op_log],
            "objects": [dict(o) for o in room.objects.values()],
            "chat": [dict(c) for c in room.chat],
            "dirty": False,
        }
    path = rooms_dir() / f"{name}.json"
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    room.dirty = False


def load_room(code: str) -> Optional[dict[str, Any]]:
    path = _room_path(code)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_boards() -> list[str]:
    if not rooms_dir().exists():
        return []
    return sorted(p.stem for p in rooms_dir().glob("*.json"))


def apply_loaded(room: Any, data: dict[str, Any]) -> None:
    """Replace a room's board with loaded data."""
    with room.lock:
        room.seq = int(data.get("seq", 0))
        room.op_log = [dict(op) for op in data.get("op_log", [])]
        room.seen_op_ids = {op.get("op_id") for op in room.op_log if isinstance(op.get("op_id"), str)}
        room.objects = {}
        for obj in data.get("objects", []):
            if isinstance(obj, dict) and "id" in obj:
                room.objects[obj["id"]] = dict(obj)
        room.chat = [dict(c) for c in data.get("chat", [])]
        room.chat_seq = len(room.chat)
        room.dirty = False
        # Reset undo/redo stacks on load.
        room.undo_stacks.clear()
        room.redo_stacks.clear()


def autosave_loop(room_manager: Any, interval: float = 30.0,
                  stop_event: Any = None, empty_room_grace: float = 5.0,
                  grace_period: float = 120.0) -> None:
    """Daemon-friendly loop: save dirty rooms, prune disconnected users and
    remove empty rooms after their grace period. Stops when stop_event is set.
    """
    while True:
        if stop_event is not None:
            if stop_event.wait(interval):
                break
        else:
            time.sleep(interval)
        now = time.time()
        try:
            # Prune disconnected participants that exceeded the grace period.
            for code in list(room_manager.codes()):
                room = room_manager.get(code)
                if room is None:
                    continue
                with room.lock:
                    expired = [
                        u for u, p in list(room.participants.items())
                        if not getattr(p, "online", True)
                        and getattr(p, "disconnected_at", None) is not None
                        and now - p.disconnected_at > grace_period
                    ]
                    for u in expired:
                        room.participants.pop(u, None)

            # Save dirty rooms.
            for code in list(room_manager.codes()):
                room = room_manager.get(code)
                if room is not None and getattr(room, "dirty", False):
                    try:
                        save_room(room)
                    except Exception:
                        pass

            # Remove empty rooms after the short grace period, saving first.
            for code in list(room_manager.codes()):
                room = room_manager.get(code)
                if room is None:
                    continue
                with room.lock:
                    online = any(getattr(p, "online", False) for p in room.participants.values())
                if online:
                    room.empty_since = None
                    continue
                if getattr(room, "empty_since", None) is None:
                    room.empty_since = now
                    continue
                if now - room.empty_since >= empty_room_grace:
                    try:
                        save_room(room)
                    except Exception:
                        pass
                    room_manager.remove(code)
        except Exception:
            pass