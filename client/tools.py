"""Drawing tools and geometry helpers for the canvas.

The UI owns the actual canvas items; this module holds the pure logic that
can be unit-tested without tkinter, such as hit-testing a stroke for the
object eraser.

Coordinate transforms live here and nowhere else.  The board is always
1600x900 logical units; the widget may be any size, so the board is
letterboxed inside it.  Keeping the math in one place is what prevents the
classic "pen starts a few pixels away from the cursor" bug: the draw path,
the eraser hit-test, the remote cursors, and the debug overlay all call the
same pair of functions.
"""

from __future__ import annotations

import math
from typing import Any, Optional

LOGICAL_WIDTH = 1600
LOGICAL_HEIGHT = 900


def point_segment_distance(px: float, py: float, x1: float, y1: float,
                           x2: float, y2: float) -> float:
    """Distance from point (px,py) to segment (x1,y1)-(x2,y2)."""
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx = x1 + t * dx
    cy = y1 + t * dy
    return math.hypot(px - cx, py - cy)


def find_stroke_at(objects: dict[str, Any], lx: float, ly: float,
                   threshold: float = 8.0) -> Optional[str]:
    """Return the stroke_id of the topmost stroke near (lx,ly), or None.

    Iteration is reversed so the most recently inserted object wins, which
    matches the canvas paint order.  ``seq`` is not used for this because a
    stroke drawn locally exists in the mirror before the server has assigned
    it a seq.
    """
    for sid, obj in reversed(list(objects.items())):
        if not isinstance(obj, dict) or obj.get("type") != "stroke":
            continue
        pts = obj.get("points", [])
        if not isinstance(pts, list) or not pts:
            continue
        if len(pts) == 1:
            a = pts[0]
            if isinstance(a, (list, tuple)) and len(a) >= 2:
                if math.hypot(lx - float(a[0]), ly - float(a[1])) <= threshold:
                    return sid
            continue
        for i in range(len(pts) - 1):
            a = pts[i]
            b = pts[i + 1]
            if not (isinstance(a, (list, tuple)) and isinstance(b, (list, tuple))):
                continue
            if len(a) < 2 or len(b) < 2:
                continue
            d = point_segment_distance(lx, ly, float(a[0]), float(a[1]),
                                       float(b[0]), float(b[1]))
            if d <= threshold:
                return sid
    return None


def canvas_transform(canvas_width: float, canvas_height: float,
                     inset: float = 0.0) -> tuple[float, float, float]:
    """Return (scale, offset_x, offset_y) for a 16:9 board in the widget.

    ``canvas_width`` and ``canvas_height`` are raw widget dimensions, that
    is, what ``winfo_width`` reports.  ``inset`` is the per-side border plus
    highlight ring (both are part of the widget allocation on Tk), so the
    drawable interior is reduced by ``2 * inset`` and the board offsets are
    expressed in the same raw widget pixel space as ``canvasx``/``canvasy``.
    """
    inset = max(0.0, float(inset))
    client_w = max(1.0, float(canvas_width) - 2.0 * inset)
    client_h = max(1.0, float(canvas_height) - 2.0 * inset)
    scale = min(client_w / LOGICAL_WIDTH, client_h / LOGICAL_HEIGHT)
    if scale <= 0:
        scale = 1.0
    drawn_w = LOGICAL_WIDTH * scale
    drawn_h = LOGICAL_HEIGHT * scale
    offset_x = inset + (client_w - drawn_w) / 2.0
    offset_y = inset + (client_h - drawn_h) / 2.0
    return scale, offset_x, offset_y


def board_rect(canvas_width: float, canvas_height: float,
               inset: float = 0.0) -> tuple[float, float, float, float]:
    """Return the board rectangle in widget pixel coordinates."""
    scale, ox, oy = canvas_transform(canvas_width, canvas_height, inset)
    return ox, oy, ox + LOGICAL_WIDTH * scale, oy + LOGICAL_HEIGHT * scale


def window_to_logical(px: float, py: float, canvas_width: float,
                      canvas_height: float,
                      inset: float = 0.0) -> tuple[float, float]:
    """Map a raw widget pixel to a clamped logical board coordinate."""
    scale, ox, oy = canvas_transform(canvas_width, canvas_height, inset)
    lx = (px - ox) / scale
    ly = (py - oy) / scale
    lx = max(0.0, min(float(LOGICAL_WIDTH), lx))
    ly = max(0.0, min(float(LOGICAL_HEIGHT), ly))
    return lx, ly


def logical_to_window(lx: float, ly: float, canvas_width: float,
                      canvas_height: float,
                      inset: float = 0.0) -> tuple[float, float]:
    """Map a logical board coordinate to a raw widget pixel."""
    scale, ox, oy = canvas_transform(canvas_width, canvas_height, inset)
    return lx * scale + ox, ly * scale + oy


def is_inside_board(px: float, py: float, canvas_width: float,
                    canvas_height: float, inset: float = 0.0) -> bool:
    """Return True when a raw widget pixel is over the drawable board."""
    scale, ox, oy = canvas_transform(canvas_width, canvas_height, inset)
    lx = (px - ox) / scale
    ly = (py - oy) / scale
    return 0.0 <= lx <= LOGICAL_WIDTH and 0.0 <= ly <= LOGICAL_HEIGHT
