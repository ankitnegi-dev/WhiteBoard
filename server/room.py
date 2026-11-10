"""Room state: participants, op log, objects, chat, undo/redo stacks, lock.

Day 3 implements the shared canvas backend: an append-only operation log
stamped with a per-room seq, op_id deduplication, server-side validation,
a strokes/objects map, chat and snapshots for late joiners. No GUI code
lives here.
"""

from __future__ import annotations

import re

try:
    from protocol import SHAPE_KINDS
except Exception:  # pragma: no cover
    SHAPE_KINDS = ("line", "rect", "ellipse", "arrow")
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

USER_COLORS: list[str] = [
    "#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231",
    "#911eb4", "#42d4f4", "#f032e6", "#bfef45", "#fabed4",
]

# Canvas logical size (must match client scaling).
CANVAS_WIDTH = 1600
CANVAS_HEIGHT = 900

MAX_POINT_BATCH = 500
MAX_TEXT_LENGTH = 500
MAX_WIDTH = 50.0
MIN_WIDTH = 1.0
MAX_CHAT_LENGTH = 500
MAX_OPS_SINCE = 1000

DRAWING_OPS = {"stroke_start", "stroke_points", "stroke_end", "delete_object",
               "shape", "text"}
TOOLS = {"pen", "eraser"}
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class Participant:
    username: str
    color: str
    role: str = "editor"          # host | editor | viewer
    online: bool = True
    token: str = ""
    kicked: bool = False
    disconnected_at: Optional[float] = None
    state: Optional[dict[str, Any]] = None
    send: Optional[Callable[[dict[str, Any]], None]] = None
    kick: Optional[Callable[[], None]] = None


class Room:
    def __init__(self, code: str) -> None:
        self.code = code
        self.lock = threading.Lock()
        self.participants: Dict[str, Participant] = {}
        self.password_salt: Optional[str] = None
        self.password_hash: Optional[str] = None
        self.locked: bool = False

        # Shared canvas state.
        self.seq: int = 0
        self.op_log: List[dict[str, Any]] = []
        self.seen_op_ids: set[str] = set()
        self.objects: Dict[str, dict[str, Any]] = {}   # id -> object state
        self.chat: List[dict[str, Any]] = []
        self.chat_seq: int = 0

        # Undo/redo stacks are kept per user (filled in Day 4).
        self.undo_stacks: Dict[str, list[dict[str, Any]]] = {}
        self.redo_stacks: Dict[str, list[dict[str, Any]]] = {}
        self.dirty: bool = False
        self.empty_since: Optional[float] = None
        self.kicked_tokens: set[str] = set()

    def has_password(self) -> bool:
        return self.password_hash is not None

    def check_password(self, password: Optional[str]) -> bool:
        if not self.has_password():
            return True
        if not isinstance(password, str):
            return False
        from server.auth import verify_password

        assert self.password_salt is not None and self.password_hash is not None
        return verify_password(password, self.password_salt, self.password_hash)

    def participant_list(self) -> List[dict[str, Any]]:
        return [
            {"name": p.username, "color": p.color, "role": p.role, "online": p.online}
            for p in self.participants.values()
        ]

    def broadcast(self, msg: dict[str, Any], exclude: Optional[str] = None) -> None:
        for username, p in list(self.participants.items()):
            if username == exclude:
                continue
            if p.online and p.send is not None:
                p.send(msg)

    def next_color(self) -> str:
        return USER_COLORS[len(self.participants) % len(USER_COLORS)]

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def _is_number(self, v: Any) -> bool:
        return isinstance(v, (int, float)) and not isinstance(v, bool)

    def _in_canvas(self, x: Any, y: Any) -> bool:
        return self._is_number(x) and self._is_number(y) and 0 <= x <= CANVAS_WIDTH and 0 <= y <= CANVAS_HEIGHT

    def validate_op(self, msg: Any, username: str, role: str) -> Tuple[bool, str]:
        if not isinstance(msg, dict):
            return False, "bad_message"
        mtype = msg.get("type")
        if not isinstance(mtype, str):
            return False, "bad_type"

        # Permission: viewers may not draw or delete.
        if role == "viewer" and mtype in DRAWING_OPS:
            return False, "forbidden"

        if mtype == "stroke_start":
            if not self._require_str(msg.get("op_id")):
                return False, "bad_op_id"
            if not self._require_str(msg.get("stroke_id")):
                return False, "bad_stroke_id"
            tool = msg.get("tool")
            if tool not in TOOLS:
                return False, "bad_tool"
            color = msg.get("color")
            if not isinstance(color, str) or not COLOR_RE.match(color):
                return False, "bad_color"
            width = msg.get("width")
            if not self._is_number(width) or not (MIN_WIDTH <= width <= MAX_WIDTH):
                return False, "bad_width"
            if not self._in_canvas(msg.get("x"), msg.get("y")):
                return False, "bad_coordinates"
            return True, ""

        if mtype == "stroke_points":
            if not self._require_str(msg.get("op_id")):
                return False, "bad_op_id"
            if not self._require_str(msg.get("stroke_id")):
                return False, "bad_stroke_id"
            points = msg.get("points")
            if not isinstance(points, list) or not points:
                return False, "bad_points"
            if len(points) > MAX_POINT_BATCH:
                return False, "too_many_points"
            for pt in points:
                if not isinstance(pt, (list, tuple)) or len(pt) != 2:
                    return False, "bad_point"
                if not self._in_canvas(pt[0], pt[1]):
                    return False, "bad_coordinates"
            return True, ""

        if mtype == "stroke_end":
            if not self._require_str(msg.get("op_id")):
                return False, "bad_op_id"
            if not self._require_str(msg.get("stroke_id")):
                return False, "bad_stroke_id"
            return True, ""

        if mtype == "delete_object":
            if not self._require_str(msg.get("op_id")):
                return False, "bad_op_id"
            if not self._require_str(msg.get("target_id")):
                return False, "bad_target_id"
            return True, ""

        if mtype == "chat":
            text = msg.get("text")
            if not isinstance(text, str) or not text.strip():
                return False, "bad_text"
            if len(text) > MAX_CHAT_LENGTH:
                return False, "text_too_long"
            return True, ""

        if mtype == "shape":
            if not self._require_str(msg.get("op_id")):
                return False, "bad_op_id"
            if not self._require_str(msg.get("shape_id")):
                return False, "bad_shape_id"
            kind = msg.get("kind")
            if kind not in SHAPE_KINDS:
                return False, "bad_kind"
            p1 = msg.get("p1")
            p2 = msg.get("p2")
            if not (isinstance(p1, (list, tuple)) and len(p1) == 2):
                return False, "bad_p1"
            if not (isinstance(p2, (list, tuple)) and len(p2) == 2):
                return False, "bad_p2"
            if not self._in_canvas(p1[0], p1[1]):
                return False, "bad_coordinates"
            if not self._in_canvas(p2[0], p2[1]):
                return False, "bad_coordinates"
            color = msg.get("color")
            if not isinstance(color, str) or not COLOR_RE.match(color):
                return False, "bad_color"
            width = msg.get("width")
            if not self._is_number(width) or not (MIN_WIDTH <= width <= MAX_WIDTH):
                return False, "bad_width"
            fill = msg.get("fill", None)
            if fill is not None:
                if not isinstance(fill, str) or not COLOR_RE.match(fill):
                    return False, "bad_fill"
            return True, ""

        if mtype == "text":
            if not self._require_str(msg.get("op_id")):
                return False, "bad_op_id"
            if not self._require_str(msg.get("obj_id")):
                return False, "bad_obj_id"
            if not self._in_canvas(msg.get("x"), msg.get("y")):
                return False, "bad_coordinates"
            t = msg.get("text")
            if not isinstance(t, str) or not t.strip():
                return False, "bad_text"
            if len(t) > MAX_TEXT_LENGTH:
                return False, "text_too_long"
            size = msg.get("size")
            if not self._is_number(size) or not (8 <= size <= 72):
                return False, "bad_size"
            color = msg.get("color")
            if not isinstance(color, str) or not COLOR_RE.match(color):
                return False, "bad_color"
            return True, ""

        return False, "unknown_type"

    def _require_str(self, v: Any) -> bool:
        return isinstance(v, str) and bool(v.strip()) and len(v) <= 128

    # ------------------------------------------------------------------ #
    # Apply
    # ------------------------------------------------------------------ #
    def apply_op(self, msg: Any, username: str, role: str) -> Tuple[str, Optional[dict[str, Any]], str]:
        """Validate, dedupe and append a drawing op. Returns (status, op, reason)."""
        with self.lock:
            ok, reason = self.validate_op(msg, username, role)
            if not ok:
                return "invalid", None, reason

            mtype = msg["type"]
            op_id = msg.get("op_id")
            if isinstance(op_id, str) and op_id in self.seen_op_ids:
                return "duplicate", None, "duplicate_op_id"

            # Object-level handling.
            if mtype == "stroke_start":
                sid = msg["stroke_id"]
                if sid in self.objects:
                    return "invalid", None, "stroke_already_exists"
                self.objects[sid] = {
                    "id": sid,
                    "type": "stroke",
                    "tool": msg["tool"],
                    "color": msg["color"],
                    "width": msg["width"],
                    "points": [],
                    "user": username,
                }
                self.redo_stacks.pop(username, None)
            elif mtype == "stroke_points":
                sid = msg["stroke_id"]
                obj = self.objects.get(sid)
                if obj is None or obj.get("type") != "stroke":
                    return "invalid", None, "stroke_not_found"
                for pt in msg["points"]:
                    obj["points"].append([pt[0], pt[1]])
            elif mtype == "stroke_end":
                sid = msg["stroke_id"]
                obj = self.objects.get(sid)
                if obj is None or obj.get("type") != "stroke":
                    return "invalid", None, "stroke_not_found"
                obj["ended"] = True
                self.undo_stacks.setdefault(username, []).append(
                    {"kind": "stroke", "user": username, "stroke_id": sid, "object": dict(obj)}
                )
            elif mtype == "delete_object":
                tid = msg["target_id"]
                existing = self.objects.get(tid)
                if existing is not None:
                    self.undo_stacks.setdefault(username, []).append(
                        {"kind": "delete", "user": username, "target_id": tid, "object": dict(existing)}
                    )
                self.objects.pop(tid, None)
                self.redo_stacks.pop(username, None)
            elif mtype == "shape":
                sid = msg["shape_id"]
                if sid in self.objects:
                    return "invalid", None, "shape_already_exists"
                obj = {
                    "id": sid,
                    "type": "shape",
                    "kind": msg["kind"],
                    "p1": [msg["p1"][0], msg["p1"][1]],
                    "p2": [msg["p2"][0], msg["p2"][1]],
                    "color": msg["color"],
                    "width": msg["width"],
                    "fill": msg.get("fill", None),
                    "user": username,
                }
                self.objects[sid] = obj
                self.redo_stacks.pop(username, None)
                self.undo_stacks.setdefault(username, []).append(
                    {"kind": "object", "user": username, "object_id": sid, "object": dict(obj)}
                )
            elif mtype == "text":
                oid = msg["obj_id"]
                if oid in self.objects:
                    return "invalid", None, "text_already_exists"
                obj = {
                    "id": oid,
                    "type": "text",
                    "x": msg["x"],
                    "y": msg["y"],
                    "text": msg["text"],
                    "size": msg["size"],
                    "color": msg["color"],
                    "user": username,
                }
                self.objects[oid] = obj
                self.redo_stacks.pop(username, None)
                self.undo_stacks.setdefault(username, []).append(
                    {"kind": "object", "user": username, "object_id": oid, "object": dict(obj)}
                )

            # Stamp and append.
            self.seq += 1
            op = dict(msg)
            op["seq"] = self.seq
            op["user"] = username
            op["ts"] = time.time()
            if isinstance(op_id, str):
                self.seen_op_ids.add(op_id)
            self.op_log.append(op)
            self.dirty = True
            # Attach seq to the object for snapshots.
            if mtype in ("stroke_start", "stroke_points", "stroke_end"):
                obj = self.objects.get(msg.get("stroke_id"))
                if obj is not None:
                    obj["seq"] = self.seq
            return "ok", op, ""

    def apply_chat(self, msg: Any, username: str) -> Tuple[bool, Optional[dict[str, Any]], str]:
        with self.lock:
            ok, reason = self.validate_op(msg, username, "viewer")
            if not ok:
                return False, None, reason
            text = msg["text"]
            self.chat_seq += 1
            ts = time.time()
            entry = {"user": username, "text": text, "timestamp": ts, "seq_chat": self.chat_seq}
            self.chat.append(entry)
            self.dirty = True
            return True, entry, ""

    # ------------------------------------------------------------------ #
    # Undo / redo / clear
    # ------------------------------------------------------------------ #
    def clear_redo(self, username: str) -> None:
        with self.lock:
            self.redo_stacks.pop(username, None)

    def push_undo(self, username: str, entry: dict[str, Any]) -> None:
        with self.lock:
            self.undo_stacks.setdefault(username, []).append(entry)

    def clear_canvas(self, op_id: Any, username: str) -> Tuple[str, Optional[dict[str, Any]], str]:
        with self.lock:
            if not isinstance(op_id, str) or not op_id.strip():
                return "invalid", None, "bad_op_id"
            if op_id in self.seen_op_ids:
                return "duplicate", None, "duplicate_op_id"
            cleared = [dict(o) for o in self.objects.values()]
            self.objects.clear()
            entry = {"kind": "clear", "user": username, "cleared": cleared}
            self.seq += 1
            op = {"type": "clear_canvas", "op_id": op_id, "user": username,
                  "seq": self.seq, "ts": time.time()}
            self.seen_op_ids.add(op_id)
            self.op_log.append(op)
            self.dirty = True
            self.undo_stacks.setdefault(username, []).append(entry)
            self.redo_stacks.pop(username, None)
            return "ok", op, ""

    def undo(self, op_id: Any, username: str) -> Tuple[str, Optional[dict[str, Any]], str]:
        with self.lock:
            if not isinstance(op_id, str) or not op_id.strip():
                return "invalid", None, "bad_op_id"
            if op_id in self.seen_op_ids:
                return "duplicate", None, "duplicate_op_id"
            stack = self.undo_stacks.get(username, [])
            if not stack:
                return "empty", None, "nothing to undo"
            entry = stack.pop()
            kind = entry.get("kind")

            if kind == "stroke":
                sid = entry.get("stroke_id")
                obj = self.objects.get(sid) if isinstance(sid, str) else None
                if obj is None:
                    # Another user already removed it: no-op, tell the caller.
                    self.redo_stacks.setdefault(username, []).insert(0, entry) if False else None
                    # Do not push to redo; target is gone.
                    return "noop", None, "target already removed"
                del self.objects[sid]
                broadcast = {"type": "undo", "op_id": op_id, "action": "remove_stroke",
                             "target_id": sid, "user": username}
            elif kind == "delete":
                obj = entry.get("object")
                tid = entry.get("target_id")
                if isinstance(obj, dict) and isinstance(tid, str) and tid not in self.objects:
                    self.objects[tid] = dict(obj)
                broadcast = {"type": "undo", "op_id": op_id, "action": "restore_object",
                             "object": dict(obj) if isinstance(obj, dict) else None, "user": username}
            elif kind == "object":
                oid = entry.get("object_id")
                if isinstance(oid, str) and oid in self.objects:
                    del self.objects[oid]
                broadcast = {"type": "undo", "op_id": op_id, "action": "remove_object",
                              "target_id": oid, "user": username}
            elif kind == "clear":
                for o in entry.get("cleared", []):
                    if isinstance(o, dict) and "id" in o and o["id"] not in self.objects:
                        self.objects[o["id"]] = dict(o)
                broadcast = {"type": "undo", "op_id": op_id, "action": "restore_objects",
                             "objects": [dict(o) for o in entry.get("cleared", [])], "user": username}
            else:
                # Unknown entry: put it back and report.
                stack.append(entry)
                return "invalid", None, "unknown undo entry"

            self.seq += 1
            broadcast["seq"] = self.seq
            broadcast["ts"] = time.time()
            self.seen_op_ids.add(op_id)
            self.op_log.append(broadcast)
            self.dirty = True
            self.redo_stacks.setdefault(username, []).append(entry)
            return "ok", broadcast, ""

    def redo(self, op_id: Any, username: str) -> Tuple[str, Optional[dict[str, Any]], str]:
        with self.lock:
            if not isinstance(op_id, str) or not op_id.strip():
                return "invalid", None, "bad_op_id"
            if op_id in self.seen_op_ids:
                return "duplicate", None, "duplicate_op_id"
            stack = self.redo_stacks.get(username, [])
            if not stack:
                return "empty", None, "nothing to redo"
            entry = stack.pop()
            kind = entry.get("kind")

            if kind == "stroke":
                obj = entry.get("object")
                if isinstance(obj, dict) and "id" in obj and obj["id"] not in self.objects:
                    self.objects[obj["id"]] = dict(obj)
                broadcast = {"type": "redo", "op_id": op_id, "action": "add_stroke",
                             "object": dict(obj) if isinstance(obj, dict) else None, "user": username}
            elif kind == "delete":
                tid = entry.get("target_id")
                if isinstance(tid, str):
                    self.objects.pop(tid, None)
                broadcast = {"type": "redo", "op_id": op_id, "action": "remove_stroke",
                             "target_id": tid, "user": username}
            elif kind == "object":
                obj = entry.get("object")
                if isinstance(obj, dict) and "id" in obj and obj["id"] not in self.objects:
                    self.objects[obj["id"]] = dict(obj)
                broadcast = {"type": "redo", "op_id": op_id, "action": "add_object",
                              "object": dict(obj) if isinstance(obj, dict) else None, "user": username}
            elif kind == "clear":
                ids = [o["id"] for o in entry.get("cleared", []) if isinstance(o, dict) and "id" in o]
                for tid in ids:
                    self.objects.pop(tid, None)
                broadcast = {"type": "redo", "op_id": op_id, "action": "remove_objects",
                             "target_ids": ids, "user": username}
            else:
                stack.append(entry)
                return "invalid", None, "unknown redo entry"

            self.seq += 1
            broadcast["seq"] = self.seq
            broadcast["ts"] = time.time()
            self.seen_op_ids.add(op_id)
            self.op_log.append(broadcast)
            self.dirty = True
            self.undo_stacks.setdefault(username, []).append(entry)
            return "ok", broadcast, ""

    def ops_since(self, from_seq: Any) -> Optional[dict[str, Any]]:
        with self.lock:
            if not isinstance(from_seq, int) or isinstance(from_seq, bool):
                return None
            if from_seq < 0:
                return None
            if from_seq >= self.seq:
                return {"from_seq": self.seq, "ops": []}
            if self.seq - from_seq > MAX_OPS_SINCE:
                return None
            ops = [op for op in self.op_log if op["seq"] > from_seq]
            return {"from_seq": from_seq, "ops": ops}

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            objects = sorted(self.objects.values(), key=lambda o: (o.get("seq", 0), o.get("id", "")))
            return {
                "seq": self.seq,
                "objects": [dict(o) for o in objects],
                "chat_tail": [dict(c) for c in self.chat[-50:]],
            }