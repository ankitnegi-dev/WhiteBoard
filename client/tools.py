"""Drawing tools and geometry helpers for the canvas."""

from __future__ import annotations

import math
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
try:
    import shapes_geometry as _sg
except Exception:  # pragma: no cover
    _sg = None

LOGICAL_WIDTH = 1600
LOGICAL_HEIGHT = 900


def point_segment_distance(px, py, x1, y1, x2, y2):
    dx = x2 - x1
    dy = y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx = x1 + t * dx
    cy = y1 + t * dy
    return math.hypot(px - cx, py - cy)


def find_stroke_at(objects, lx, ly, threshold=8.0):
    return find_object_at(objects, lx, ly, threshold)


def canvas_transform(canvas_width, canvas_height, inset=0.0):
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


def board_rect(canvas_width, canvas_height, inset=0.0):
    scale, ox, oy = canvas_transform(canvas_width, canvas_height, inset)
    return ox, oy, ox + LOGICAL_WIDTH * scale, oy + LOGICAL_HEIGHT * scale


def window_to_logical(px, py, canvas_width, canvas_height, inset=0.0):
    scale, ox, oy = canvas_transform(canvas_width, canvas_height, inset)
    lx = (px - ox) / scale
    ly = (py - oy) / scale
    lx = max(0.0, min(float(LOGICAL_WIDTH), lx))
    ly = max(0.0, min(float(LOGICAL_HEIGHT), ly))
    return lx, ly


def logical_to_window(lx, ly, canvas_width, canvas_height, inset=0.0):
    scale, ox, oy = canvas_transform(canvas_width, canvas_height, inset)
    return lx * scale + ox, ly * scale + oy


def is_inside_board(px, py, canvas_width, canvas_height, inset=0.0):
    scale, ox, oy = canvas_transform(canvas_width, canvas_height, inset)
    lx = (px - ox) / scale
    ly = (py - oy) / scale
    return 0.0 <= lx <= LOGICAL_WIDTH and 0.0 <= ly <= LOGICAL_HEIGHT


def _point_in_rect(lx, ly, x1, y1, x2, y2):
    lo_x, hi_x = (x1, x2) if x1 <= x2 else (x2, x1)
    lo_y, hi_y = (y1, y2) if y1 <= y2 else (y2, y1)
    return lo_x <= lx <= hi_x and lo_y <= ly <= hi_y


def _point_in_ellipse(lx, ly, x1, y1, x2, y2):
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    rx = abs(x2 - x1) / 2.0
    ry = abs(y2 - y1) / 2.0
    if rx <= 0 or ry <= 0:
        return math.hypot(lx - cx, ly - cy) <= 1
    dx = (lx - cx) / rx
    dy = (ly - cy) / ry
    return (dx * dx + dy * dy) <= 1.0


def _point_on_rect_outline(lx, ly, x1, y1, x2, y2, threshold):
    edges = [(x1, y1, x2, y1), (x2, y1, x2, y2),
             (x2, y2, x1, y2), (x1, y2, x1, y1)]
    for a, b, c, d in edges:
        if point_segment_distance(lx, ly, a, b, c, d) <= threshold:
            return True
    return False


def _point_on_ellipse_outline(lx, ly, x1, y1, x2, y2, threshold):
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    rx = abs(x2 - x1) / 2.0
    ry = abs(y2 - y1) / 2.0
    if rx <= 0 or ry <= 0:
        return math.hypot(lx - cx, ly - cy) <= threshold
    dx = (lx - cx) / rx
    dy = (ly - cy) / ry
    dist = abs(math.hypot(dx, dy) - 1.0) * min(rx, ry)
    return dist <= threshold


def find_object_at(objects, lx, ly, threshold=8.0):
    for oid, obj in reversed(list(objects.items())):
        if not isinstance(obj, dict):
            continue
        otype = obj.get("type")
        if otype == "stroke":
            pts = obj.get("points", [])
            if not isinstance(pts, list) or not pts:
                continue
            if len(pts) == 1:
                a = pts[0]
                if isinstance(a, (list, tuple)) and len(a) >= 2:
                    if math.hypot(lx - float(a[0]), ly - float(a[1])) <= threshold:
                        return oid
                continue
            hit = False
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
                    hit = True
                    break
            if hit:
                return oid
        elif otype == "shape":
            kind = obj.get("kind")
            p1 = obj.get("p1", [0, 0])
            p2 = obj.get("p2", [0, 0])
            if not (isinstance(p1, (list, tuple)) and isinstance(p2, (list, tuple))):
                continue
            x1, y1 = float(p1[0]), float(p1[1])
            x2, y2 = float(p2[0]), float(p2[1])
            filled = obj.get("fill") is not None
            if _sg is not None:
                try:
                    pts = _sg.shape_points(kind, x1, y1, x2, y2)
                except Exception:
                    pts = []
                # distance to outline
                hit = False
                for i in range(len(pts) - 1):
                    a = pts[i]; b = pts[i + 1]
                    if point_segment_distance(lx, ly, a[0], a[1], b[0], b[1]) <= threshold:
                        hit = True
                        break
                if hit:
                    return oid
                if filled and len(pts) >= 3:
                    try:
                        if _sg.point_in_polygon(lx, ly, pts):
                            return oid
                    except Exception:
                        pass
            else:
                # fallback old logic
                if kind in ("line", "arrow", "double_arrow"):
                    if point_segment_distance(lx, ly, x1, y1, x2, y2) <= threshold:
                        return oid
                elif kind in ("rect", "rectangle"):
                    if filled and _point_in_rect(lx, ly, x1, y1, x2, y2):
                        return oid
                    if _point_on_rect_outline(lx, ly, x1, y1, x2, y2, threshold):
                        return oid
                elif kind == "ellipse":
                    if filled and _point_in_ellipse(lx, ly, x1, y1, x2, y2):
                        return oid
                    if _point_on_ellipse_outline(lx, ly, x1, y1, x2, y2, threshold):
                        return oid
        elif otype == "text":
            x = float(obj.get("x", 0))
            y = float(obj.get("y", 0))
            size = float(obj.get("size", 12))
            text = str(obj.get("text", ""))
            w = max(10.0, len(text) * size * 0.6)
            h = size * 1.4
            if x - threshold <= lx <= x + w + threshold and \
               y - threshold <= ly <= y + h + threshold:
                return oid
    return None


def shift_constraint(kind, x1, y1, x2, y2):
    dx = x2 - x1
    dy = y2 - y1
    if kind in ("rect", "ellipse"):
        side = max(abs(dx), abs(dy))
        sx = side if dx >= 0 else -side
        sy = side if dy >= 0 else -side
        return x1 + sx, y1 + sy
    if kind in ("line", "arrow"):
        length = math.hypot(dx, dy)
        if length == 0:
            return x2, y2
        angle = math.atan2(dy, dx)
        step = math.pi / 12.0
        snapped = round(angle / step) * step
        return x1 + length * math.cos(snapped), y1 + length * math.sin(snapped)
    return x2, y2
