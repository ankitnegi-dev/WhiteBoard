"""Client-side mirror of the server-authoritative board.

The server is the source of truth. This object only caches what we have
applied so far and decides whether an incoming op is new, a duplicate, or
a gap that requires a resync.
"""

from __future__ import annotations

import time
from typing import Any, Optional


LOGICAL_WIDTH = 1600
LOGICAL_HEIGHT = 900


def canvas_to_logical(cx: float, cy: float, canvas_w: int, canvas_h: int,
                      offset_x: float = 0.0, offset_y: float = 0.0,
                      scale: float = 1.0) -> tuple[float, float]:
    """Map a canvas pixel coordinate back to logical board coordinates.

    The canvas keeps the 16:9 aspect ratio, so there may be letterboxing.
    ``offset_x/offset_y`` is the top-left of the drawn area and ``scale``
    is pixels per logical unit.
    """
    if scale <= 0:
        scale = 1.0
    lx = (cx - offset_x) / scale
    ly = (cy - offset_y) / scale
    # Clamp to the logical canvas so a drag that leaves the board does not
    # produce out-of-range coordinates the server would reject.
    lx = max(0.0, min(LOGICAL_WIDTH, lx))
    ly = max(0.0, min(LOGICAL_HEIGHT, ly))
    return lx, ly


def logical_to_canvas(lx: float, ly: float, offset_x: float = 0.0,
                      offset_y: float = 0.0, scale: float = 1.0) -> tuple[float, float]:
    return (lx * scale + offset_x, ly * scale + offset_y)


def compute_scale(canvas_w: int, canvas_h: int) -> tuple[float, float, float]:
    """Return (scale, offset_x, offset_y) for a 16:9 fit inside the canvas."""
    if canvas_w <= 0 or canvas_h <= 0:
        return 1.0, 0.0, 0.0
    scale = min(canvas_w / LOGICAL_WIDTH, canvas_h / LOGICAL_HEIGHT)
    drawn_w = LOGICAL_WIDTH * scale
    drawn_h = LOGICAL_HEIGHT * scale
    offset_x = (canvas_w - drawn_w) / 2.0
    offset_y = (canvas_h - drawn_h) / 2.0
    return scale, offset_x, offset_y


class BoardState:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}   # stroke_id -> object
        self.applied_seq: int = 0
        self.participants: list[dict[str, Any]] = []
        self.chat: list[dict[str, Any]] = []
        self.pending_op_ids: set[str] = set()
        # Buffered ops that arrived ahead of their seq.
        self._buffered: dict[int, dict[str, Any]] = {}
        # Activity tracking.
        self.active_drawers: set[str] = set()
        self._drawer_last: dict[str, float] = {}

    def apply_snapshot(self, snapshot: dict[str, Any]) -> None:
        objs = snapshot.get("objects", [])
        self.objects = {o["id"]: dict(o) for o in objs if isinstance(o, dict) and "id" in o}
        self.applied_seq = int(snapshot.get("seq", 0))
        tail = snapshot.get("chat_tail")
        if isinstance(tail, list):
            self.chat = [dict(c) for c in tail]

    def apply_chat_history(self, history: list[dict[str, Any]]) -> None:
        self.chat = [dict(c) for c in history]

    def update_participants(self, users: list[dict[str, Any]]) -> None:
        self.participants = [dict(u) for u in users]

    # ------------------------------------------------------------------ #
    # Activity tracking
    # ------------------------------------------------------------------ #
    def start_drawing(self, user: str, now: Optional[float] = None) -> None:
        if not isinstance(user, str) or not user:
            return
        if now is None:
            now = time.time()
        self.active_drawers.add(user)
        self._drawer_last[user] = now

    def note_drawing(self, user: str, now: Optional[float] = None) -> None:
        if not isinstance(user, str) or not user:
            return
        if user not in self.active_drawers:
            self.active_drawers.add(user)
        if now is None:
            now = time.time()
        self._drawer_last[user] = now

    def end_drawing(self, user: str) -> None:
        self.active_drawers.discard(user)
        self._drawer_last.pop(user, None)

    def remove_drawer(self, user: str) -> None:
        self.active_drawers.discard(user)
        self._drawer_last.pop(user, None)

    def clear_drawers(self) -> None:
        self.active_drawers.clear()
        self._drawer_last.clear()

    def prune_drawers(self, timeout: float = 3.0, now: Optional[float] = None) -> None:
        if now is None:
            now = time.time()
        stale = [u for u, t in self._drawer_last.items() if now - t > timeout]
        for u in stale:
            self.active_drawers.discard(u)
            self._drawer_last.pop(u, None)

    # ------------------------------------------------------------------ #
    # Op application with gap detection
    # ------------------------------------------------------------------ #
    def apply_op(self, op: dict[str, Any]) -> str:
        """Return 'applied', 'duplicate', or 'gap'.

        'gap' means the op's seq is ahead of what we have; the caller should
        send resync_request(last_seq=applied_seq).
        """
        if not isinstance(op, dict):
            return "duplicate"
        seq = op.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            # No seq: treat as a live broadcast we cannot order; apply it.
            self._apply_one(op)
            return "applied"
        if seq <= self.applied_seq:
            return "duplicate"
        if seq == self.applied_seq + 1:
            self._apply_one(op)
            self.applied_seq = seq
            # Drain any buffered ops that now fit.
            while self.applied_seq + 1 in self._buffered:
                nxt = self._buffered.pop(self.applied_seq + 1)
                self._apply_one(nxt)
                self.applied_seq += 1
            return "applied"
        # Gap: buffer and ask for resync.
        self._buffered[seq] = op
        return "gap"

    def _apply_one(self, op: dict[str, Any]) -> None:
        mtype = op.get("type")
        user = op.get("user")
        if mtype == "stroke_start":
            sid = op.get("stroke_id")
            if isinstance(sid, str):
                self.objects[sid] = {
                    "id": sid,
                    "type": "stroke",
                    "tool": op.get("tool", "pen"),
                    "color": op.get("color", "#000000"),
                    "width": op.get("width", 2),
                    "points": [],
                    "user": user,
                    "seq": op.get("seq", 0),
                }
            self.start_drawing(user if isinstance(user, str) else "")
        elif mtype == "stroke_points":
            sid = op.get("stroke_id")
            obj = self.objects.get(sid) if isinstance(sid, str) else None
            if obj is not None:
                pts = op.get("points", [])
                if isinstance(pts, list):
                    for pt in pts:
                        if isinstance(pt, (list, tuple)) and len(pt) == 2:
                            obj["points"].append([pt[0], pt[1]])
            self.note_drawing(user if isinstance(user, str) else "")
        elif mtype == "stroke_end":
            sid = op.get("stroke_id")
            obj = self.objects.get(sid) if isinstance(sid, str) else None
            if obj is not None:
                obj["ended"] = True
            self.end_drawing(user if isinstance(user, str) else "")
        elif mtype == "delete_object":
            tid = op.get("target_id")
            if isinstance(tid, str):
                self.objects.pop(tid, None)
        elif mtype == "clear_canvas":
            self.objects.clear()
            self.clear_drawers()
        elif mtype == "undo":
            action = op.get("action")
            if action == "remove_stroke":
                tid = op.get("target_id")
                if isinstance(tid, str):
                    self.objects.pop(tid, None)
            elif action == "restore_object":
                obj = op.get("object")
                if isinstance(obj, dict) and "id" in obj:
                    self.objects[obj["id"]] = dict(obj)
            elif action == "restore_objects":
                for obj in op.get("objects", []):
                    if isinstance(obj, dict) and "id" in obj:
                        self.objects[obj["id"]] = dict(obj)
        elif mtype == "redo":
            action = op.get("action")
            if action == "add_stroke":
                obj = op.get("object")
                if isinstance(obj, dict) and "id" in obj:
                    self.objects[obj["id"]] = dict(obj)
            elif action == "remove_stroke":
                tid = op.get("target_id")
                if isinstance(tid, str):
                    self.objects.pop(tid, None)
            elif action == "remove_objects":
                for tid in op.get("target_ids", []):
                    if isinstance(tid, str):
                        self.objects.pop(tid, None)
        elif mtype == "undo":
            action = op.get("action")
            if action == "remove_stroke":
                tid = op.get("target_id")
                if isinstance(tid, str):
                    self.objects.pop(tid, None)
            elif action == "restore_object":
                obj = op.get("object")
                if isinstance(obj, dict) and "id" in obj:
                    self.objects[obj["id"]] = dict(obj)
            elif action == "restore_objects":
                for obj in op.get("objects", []):
                    if isinstance(obj, dict) and "id" in obj:
                        self.objects[obj["id"]] = dict(obj)
        elif mtype == "redo":
            action = op.get("action")
            if action == "add_stroke":
                obj = op.get("object")
                if isinstance(obj, dict) and "id" in obj:
                    self.objects[obj["id"]] = dict(obj)
            elif action == "remove_stroke":
                tid = op.get("target_id")
                if isinstance(tid, str):
                    self.objects.pop(tid, None)
            elif action == "remove_objects":
                for tid in op.get("target_ids", []):
                    if isinstance(tid, str):
                        self.objects.pop(tid, None)

    def apply_ops_since(self, from_seq: int, ops: list[dict[str, Any]]) -> None:
        for op in sorted(ops, key=lambda o: o.get("seq", 0)):
            self.apply_op(op)

    def needs_resync(self, op: dict[str, Any]) -> bool:
        seq = op.get("seq")
        return isinstance(seq, int) and not isinstance(seq, bool) and seq > self.applied_seq + 1

    def track_pending(self, op_id: str) -> None:
        self.pending_op_ids.add(op_id)

    def is_pending(self, op_id: Optional[str]) -> bool:
        return isinstance(op_id, str) and op_id in self.pending_op_ids

    def clear_pending(self, op_id: Optional[str]) -> None:
        if isinstance(op_id, str):
            self.pending_op_ids.discard(op_id)