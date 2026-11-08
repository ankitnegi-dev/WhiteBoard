"""Tests for drawing helpers, hit-testing, and the single coordinate transform."""

from __future__ import annotations

import pytest

from client.tools import (
    LOGICAL_HEIGHT,
    LOGICAL_WIDTH,
    canvas_transform,
    find_stroke_at,
    is_inside_board,
    logical_to_window,
    point_segment_distance,
    window_to_logical,
)


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


def test_single_point_stroke_hits_at_point_and_misses_far_away():
    objects = {
        "s1": {"type": "stroke", "points": [[5, 5]], "seq": 1},
    }
    assert find_stroke_at(objects, 5.5, 5.5, threshold=2.0) == "s1"
    assert find_stroke_at(objects, 20, 20, threshold=2.0) is None


def test_overlapping_topmost_wins():
    objects = {
        "bottom": {"type": "stroke", "points": [[0, 0], [10, 0]], "seq": 1},
        "top": {"type": "stroke", "points": [[0, 0], [10, 0]], "seq": 2},
    }
    assert find_stroke_at(objects, 5, 0, threshold=2.0) == "top"


def test_letterbox_scale_conversion():
    scale, ox, oy = canvas_transform(800, 600)
    # Center of the window maps to center of the logical canvas.
    lx, ly = window_to_logical(400, 300, 800, 600)
    assert abs(lx - LOGICAL_WIDTH / 2) < 1e-6
    assert abs(ly - LOGICAL_HEIGHT / 2) < 1e-6
    cx, cy = logical_to_window(LOGICAL_WIDTH / 2, LOGICAL_HEIGHT / 2, 800, 600)
    assert abs(cx - 400) < 1e-6
    assert abs(cy - 300) < 1e-6
    assert scale == 0.5
    assert ox == 0.0
    assert oy == 75.0


@pytest.mark.parametrize("width,height,inset", [
    (1600, 900, 0),
    (800, 600, 0),
    (1280, 800, 0),
    (1024, 768, 2),
    (1536, 864, 4),
    (400, 300, 1),
])
def test_pixel_logical_pixel_round_trip(width, height, inset):
    # Corners and centre must survive a pixel -> logical -> pixel trip to
    # within one pixel at every window size.
    for lx, ly in ((0, 0), (LOGICAL_WIDTH, LOGICAL_HEIGHT), (0, LOGICAL_HEIGHT),
                   (LOGICAL_WIDTH, 0), (LOGICAL_WIDTH / 2, LOGICAL_HEIGHT / 2),
                   (123.5, 456.25)):
        px, py = logical_to_window(lx, ly, width, height, inset)
        rx, ry = window_to_logical(px, py, width, height, inset)
        back_x, back_y = logical_to_window(rx, ry, width, height, inset)
        assert abs(back_x - px) <= 1.0
        assert abs(back_y - py) <= 1.0
        assert abs(rx - lx) <= 1.0
        assert abs(ry - ly) <= 1.0


@pytest.mark.parametrize("pixel,expected", [
    ((200, 75), True),      # top-left board pixel in 800x600
    ((400, 300), True),     # centre
    ((10, 10), False),      # letterbox above the board
    ((799, 599), False),    # letterbox below/left of board
])
def test_inside_board(pixel, expected):
    assert is_inside_board(pixel[0], pixel[1], 800, 600) is expected


def test_inset_moves_board_origin():
    # A four pixel border/highlight ring must move the paper inward; a click
    # at raw (0,0) is no longer on the board.
    assert is_inside_board(0, 0, 800, 600, inset=4) is False
    assert is_inside_board(4, 75 + 4, 800, 600, inset=4) is True
