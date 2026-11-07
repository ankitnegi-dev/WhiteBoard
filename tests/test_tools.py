"""Tests for drawing helpers and hit-testing."""

from __future__ import annotations

from client.tools import find_stroke_at, point_segment_distance
from client.state import canvas_to_logical, compute_scale


def test_point_segment_distance_near():
    d = point_segment_distance(5, 0, 0, 0, 10, 0)
    assert d == 0.0


def test_point_segment_distance_far():
    d = point_segment_distance(5, 10, 0, 0, 10, 0)
    assert abs(d - 10.0) < 1e-9


def test_find_stroke_near_segment():
    objects = {
        "s1": {"type": "stroke", "points": [[0, 0], [10, 0]], "seq": 1},
    }
    assert find_stroke_at(objects, 5, 1, threshold=2.0) == "s1"


def test_find_stroke_far_returns_none():
    objects = {
        "s1": {"type": "stroke", "points": [[0, 0], [10, 0]], "seq": 1},
    }
    assert find_stroke_at(objects, 5, 10, threshold=2.0) is None


def test_single_point_stroke_not_hit():
    objects = {
        "s1": {"type": "stroke", "points": [[5, 5]], "seq": 1},
    }
    assert find_stroke_at(objects, 5, 5, threshold=2.0) is None


def test_overlapping_topmost_wins():
    objects = {
        "bottom": {"type": "stroke", "points": [[0, 0], [10, 0]], "seq": 1},
        "top": {"type": "stroke", "points": [[0, 0], [10, 0]], "seq": 2},
    }
    assert find_stroke_at(objects, 5, 0, threshold=2.0) == "top"


def test_letterbox_scale_conversion():
    scale, ox, oy = compute_scale(800, 600)
    # Center of the window maps to center of the logical canvas.
    lx, ly = canvas_to_logical(400, 300, 800, 600, ox, oy, scale)
    assert abs(lx - 800) < 1e-6
    assert abs(ly - 450) < 1e-6