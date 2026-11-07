"""Drawing tools and geometry helpers for the canvas.

The UI owns the actual canvas items; this module holds the pure logic that
can be unit-tested without tkinter, such as hit-testing a stroke for the
object eraser.
"""

from __future__ import annotations

import math
from typing import Any, Optional


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
    """Return the stroke_id of the topmost stroke near (lx,ly), or None."""
    # Later seqs are drawn on top, so search in reverse seq order.
    candidates = []
    for sid, obj in objects.items():
        if not isinstance(obj, dict) or obj.get("type") != "stroke":
            continue
        pts = obj.get("points", [])
        if not isinstance(pts, list) or len(pts) < 2:
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
                candidates.append((obj.get("seq", 0), sid))
                break
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][1]