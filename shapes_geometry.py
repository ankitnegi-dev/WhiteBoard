"""Shared geometry for whiteboard shapes.

One module used by canvas rendering, eraser hit-testing, live preview and
export so there is no duplicated drawing code.  Every function takes a
bounding box (x1, y1, x2, y2) in logical canvas coordinates and returns a
list of (x, y) points.  Closed shapes are returned as a polygon vertex
list; open shapes (line, arrow) as a polyline.  Coordinates are absolute
(logical canvas units); degenerate boxes never raise.
"""

from __future__ import annotations

import math
from typing import List, Tuple

Point = Tuple[float, float]


def _box(x1, y1, x2, y2):
    left = min(x1, x2)
    right = max(x1, x2)
    top = min(y1, y2)
    bottom = max(y1, y2)
    w = right - left
    h = bottom - top
    cx = (left + right) / 2.0
    cy = (top + bottom) / 2.0
    return left, top, right, bottom, w, h, cx, cy


def _ellipse_points(cx, cy, rx, ry, n=40):
    pts = []
    for i in range(n):
        a = 2 * math.pi * i / n
        pts.append((cx + rx * math.cos(a), cy + ry * math.sin(a)))
    pts.append(pts[0])
    return pts


def shape_points(kind: str, x1: float, y1: float, x2: float, y2: float) -> List[Point]:
    left, top, right, bottom, w, h, cx, cy = _box(x1, y1, x2, y2)
    if w == 0 and h == 0:
        return [(x1, y1)]
    if kind in ("line", "arrow", "double_arrow"):
        return [(x1, y1), (x2, y2)]
    if kind in ("rectangle", "rect", "process"):
        return [(left, top), (right, top), (right, bottom), (left, bottom), (left, top)]
    if kind == "rounded_rectangle":
        r = min(w, h) * 0.15
        # approximate with polygon
        pts = [(left + r, top), (right - r, top)]
        for i in range(1, 6):
            a = math.pi * (1.5 + 0.5 * i / 5.0)  # not exact; simple
        # simpler: use ellipse arc samples
        pts = []
        n = 8
        for i in range(n + 1):
            a = -math.pi / 2 + (math.pi / 2) * (i / n)
            pts.append((right - r + r * math.cos(a), top + r + r * math.sin(a)))
        for i in range(n + 1):
            a = 0 + (math.pi / 2) * (i / n)
            pts.append((right - r + r * math.cos(a), bottom - r + r * math.sin(a)))
        for i in range(n + 1):
            a = math.pi / 2 + (math.pi / 2) * (i / n)
            pts.append((left + r + r * math.cos(a), bottom - r + r * math.sin(a)))
        for i in range(n + 1):
            a = math.pi + (math.pi / 2) * (i / n)
            pts.append((left + r + r * math.cos(a), top + r + r * math.sin(a)))
        pts.append(pts[0])
        return pts
    if kind == "ellipse":
        rx = w / 2.0 if w > 0 else 1
        ry = h / 2.0 if h > 0 else 1
        return _ellipse_points(cx, cy, rx, ry, 40)
    if kind == "triangle":
        return [(cx, top), (right, bottom), (left, bottom), (cx, top)]
    if kind == "right_triangle":
        return [(left, top), (right, bottom), (left, bottom), (left, top)]
    if kind == "diamond":
        return [(cx, top), (right, cy), (cx, bottom), (left, cy), (cx, top)]
    if kind == "pentagon":
        pts = []
        for i in range(5):
            a = -math.pi / 2 + 2 * math.pi * i / 5
            r = min(w, h) / 2.0
            pts.append((cx + r * math.cos(a) * (w / (2 * r) if r else 1),
                        cy + r * math.sin(a) * (h / (2 * r) if r else 1)))
        # simpler: scale on axes
        pts = []
        for i in range(5):
            a = -math.pi / 2 + 2 * math.pi * i / 5
            pts.append((cx + (w / 2.0) * math.cos(a), cy + (h / 2.0) * math.sin(a)))
        pts.append(pts[0])
        return pts
    if kind == "hexagon":
        pts = []
        for i in range(6):
            a = 2 * math.pi * i / 6
            pts.append((cx + (w / 2.0) * math.cos(a), cy + (h / 2.0) * math.sin(a)))
        pts.append(pts[0])
        return pts
    if kind == "star":
        pts = []
        for i in range(10):
            a = -math.pi / 2 + 2 * math.pi * i / 10
            r = (min(w, h) / 2.0) if i % 2 == 0 else (min(w, h) / 4.0)
            pts.append((cx + r * (w / min(w, h) if min(w, h) else 1) * math.cos(a),
                        cy + r * (h / min(w, h) if min(w, h) else 1) * math.sin(a)))
        pts.append(pts[0])
        return pts
    if kind == "plus":
        t = 0.3
        hw, hh = w / 2.0, h / 2.0
        pts = [
            (cx - t * hw, top), (cx + t * hw, top),
            (cx + t * hw, cy - t * hh), (right, cy - t * hh),
            (right, cy + t * hh), (cx + t * hw, cy + t * hh),
            (cx + t * hw, bottom), (cx - t * hw, bottom),
            (cx - t * hw, cy + t * hh), (left, cy + t * hh),
            (left, cy - t * hh), (cx - t * hw, cy - t * hh),
            (cx - t * hw, top),
        ]
        return pts
    if kind == "heart":
        pts = []
        for i in range(40):
            t = 2 * math.pi * i / 40
            # parametric heart, scaled
            x = 16 * (math.sin(t) ** 3)
            y = 13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t)
            # normalize roughly [-16,16] x [-17,13]
            nx = (x + 16) / 32.0
            ny = (13 - y) / 30.0  # flip y
            pts.append((left + nx * w, top + ny * h))
        pts.append(pts[0])
        return pts
    if kind == "decision":
        return [(cx, top), (right, cy), (cx, bottom), (left, cy), (cx, top)]
    if kind == "terminator":
        # rounded rect (pill)
        r = h / 2.0
        pts = []
        n = 12
        for i in range(n + 1):
            a = -math.pi / 2 + math.pi * (i / n)
            pts.append((right - r + r * math.cos(a), cy + r * math.sin(a)))
        for i in range(n + 1):
            a = math.pi / 2 + math.pi * (i / n)
            pts.append((left + r + r * math.cos(a), cy + r * math.sin(a)))
        pts.append(pts[0])
        return pts
    if kind == "data":
        # parallelogram
        off = w * 0.2
        return [(left + off, top), (right, top), (right - off, bottom), (left, bottom), (left + off, top)]
    if kind == "document":
        # rect with wavy bottom
        pts = [(left, top), (right, top), (right, bottom - h * 0.1)]
        for i in range(6):
            x = right - (w / 5.0) * i
            y = bottom - h * 0.1 + (h * 0.1) * (1 if i % 2 == 0 else -1) * 0.5
            pts.append((x, y))
        pts.append((left, bottom - h * 0.1))
        pts.append((left, top))
        return pts
    if kind == "speech_bubble":
        # rect with triangle tail
        return [(left, top), (right, top), (right, bottom - h * 0.25),
                (cx + w * 0.1, bottom - h * 0.25), (cx, bottom),
                (cx - w * 0.1, bottom - h * 0.25), (left, bottom - h * 0.25),
                (left, top)]
    # unknown: fall back to rectangle
    return [(left, top), (right, top), (right, bottom), (left, bottom), (left, top)]


def shape_segments(kind, x1, y1, x2, y2):
    """Return list of (x1,y1,x2,y2) segments for outline hit-testing convenience."""
    pts = shape_points(kind, x1, y1, x2, y2)
    segs = []
    for i in range(len(pts) - 1):
        segs.append((pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]))
    return segs


def point_in_polygon(px, py, poly):
    # ray casting
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside