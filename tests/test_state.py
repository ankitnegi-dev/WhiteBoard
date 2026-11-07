"""Tests for client-side state and coordinate scaling (no tkinter)."""

from __future__ import annotations

from client.state import (
    BoardState,
    canvas_to_logical,
    compute_scale,
    logical_to_canvas,
)


def test_compute_scale_letterboxes():
    scale, ox, oy = compute_scale(1600, 900)
    assert scale == 1.0
    assert ox == 0.0
    assert oy == 0.0

    scale2, ox2, oy2 = compute_scale(800, 600)
    # 16:9 into 800x600 -> width limited, letterboxed top and bottom.
    assert abs(scale2 - 0.5) < 1e-9
    assert ox2 == 0
    assert oy2 > 0


def test_canvas_to_logical_round_trip():
    scale, ox, oy = compute_scale(1600, 900)
    cx, cy = logical_to_canvas(100, 200, ox, oy, scale)
    lx, ly = canvas_to_logical(cx, cy, 1600, 900, ox, oy, scale)
    assert abs(lx - 100) < 1e-6
    assert abs(ly - 200) < 1e-6


def test_canvas_to_logical_clamps():
    scale, ox, oy = compute_scale(1600, 900)
    lx, ly = canvas_to_logical(-5, 10000, 1600, 900, ox, oy, scale)
    assert lx == 0.0
    assert ly == 900.0

def test_board_bounds_clamp():
    scale, ox, oy = compute_scale(1600, 900)
    # Outside left/top clamps to 0.
    assert canvas_to_logical(-10, -10, 1600, 900, ox, oy, scale) == (0.0, 0.0)
    # Outside right/bottom clamps to max.
    assert canvas_to_logical(2000, 1000, 1600, 900, ox, oy, scale) == (1600.0, 900.0)


def test_gap_detection_and_duplicate():
    state = BoardState()
    op1 = {"type": "stroke_start", "seq": 1, "stroke_id": "s1", "op_id": "o1"}
    assert state.apply_op(op1) == "applied"
    assert state.applied_seq == 1

    # Duplicate seq is ignored.
    assert state.apply_op({"type": "stroke_start", "seq": 1, "stroke_id": "s2", "op_id": "o2"}) == "duplicate"

    # Gap: seq 3 arrives before seq 2.
    assert state.apply_op({"type": "stroke_start", "seq": 3, "stroke_id": "s3", "op_id": "o3"}) == "gap"
    assert state.applied_seq == 1

    # Now seq 2 arrives, drains buffer, applied_seq becomes 3.
    assert state.apply_op({"type": "stroke_start", "seq": 2, "stroke_id": "s2b", "op_id": "o2b"}) == "applied"
    assert state.applied_seq == 3
    assert "s3" in state.objects


def test_snapshot_applies():
    state = BoardState()
    state.apply_snapshot({"seq": 5, "objects": [{"id": "s1", "type": "stroke", "points": [[1, 2]]}], "chat_tail": [{"user": "a", "text": "hi"}]})
    assert state.applied_seq == 5
    assert "s1" in state.objects
    assert len(state.chat) == 1


def test_active_drawers_lifecycle():
    state = BoardState()
    state.start_drawing("alice")
    assert "alice" in state.active_drawers

    state.note_drawing("bob")
    assert "bob" in state.active_drawers

    state.end_drawing("alice")
    assert "alice" not in state.active_drawers
    assert "bob" in state.active_drawers

    state.remove_drawer("bob")
    assert "bob" not in state.active_drawers

    state.start_drawing("carol")
    state.start_drawing("dave")
    assert len(state.active_drawers) == 2

    state.clear_drawers()
    assert len(state.active_drawers) == 0


def test_active_drawers_timeout():
    state = BoardState()
    now = 1000.0
    state.start_drawing("alice", now=now)
    state.prune_drawers(timeout=3.0, now=now + 1.0)
    assert "alice" in state.active_drawers
    state.prune_drawers(timeout=3.0, now=now + 4.0)
    assert "alice" not in state.active_drawers


def test_delete_object_removes_from_mirror():
    state = BoardState()
    state.apply_op({"type": "stroke_start", "seq": 1, "stroke_id": "s1", "op_id": "o1",
                    "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2, "user": "alice"})
    assert "s1" in state.objects
    state.apply_op({"type": "delete_object", "seq": 2, "op_id": "o2", "target_id": "s1", "user": "alice"})
    assert "s1" not in state.objects


def test_undo_redo_state_still_behaves():
    state = BoardState()
    state.apply_op({"type": "stroke_start", "seq": 1, "stroke_id": "s1", "op_id": "o1",
                    "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2, "user": "alice"})
    # Simulate an undo op removing the stroke.
    state.apply_op({"type": "undo", "seq": 2, "op_id": "o2", "action": "remove_stroke",
                    "target_id": "s1", "user": "alice"})
    assert "s1" not in state.objects
    # Redo restores it.
    state.apply_op({"type": "redo", "seq": 3, "op_id": "o3", "action": "add_stroke",
                    "object": {"id": "s1", "type": "stroke", "points": []}, "user": "alice"})
    assert "s1" in state.objects


def test_active_drawers_from_ops():
    state = BoardState()
    state.apply_op({"type": "stroke_start", "seq": 1, "stroke_id": "s1", "op_id": "o1",
                    "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2, "user": "alice"})
    assert "alice" in state.active_drawers

    state.apply_op({"type": "stroke_points", "seq": 2, "stroke_id": "s1", "op_id": "o2",
                    "points": [[1, 2], [3, 4]], "user": "alice"})
    assert "alice" in state.active_drawers

    state.apply_op({"type": "stroke_end", "seq": 3, "stroke_id": "s1", "op_id": "o3", "user": "alice"})
    assert "alice" not in state.active_drawers

    state.apply_op({"type": "clear_canvas", "seq": 4, "op_id": "o4", "user": "alice"})
    # clear does not add; active already empty
    assert "alice" not in state.active_drawers