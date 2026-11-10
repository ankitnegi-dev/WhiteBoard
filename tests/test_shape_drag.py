"""Regression tests for shape dragging and live preview."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from client.tools import logical_to_window
from protocol import SHAPE_KINDS


class FakeNet:
    def __init__(self):
        self.connected = True
        self.inbox = None
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def disconnect(self):
        self.connected = False


def _event_at(app, lx, ly, state=0):
    width, height = app._canvas_size()
    px, py = logical_to_window(lx, ly, width, height, app._canvas_inset())
    return SimpleNamespace(x=px, y=py, state=state)


def _build_app(tk_root):
    from client.ui import WhiteboardApp

    app = WhiteboardApp(tk_root)
    app.connect_frame.destroy()
    app.username = "host"
    app.room_code = "ABC123"
    app.role = "host"
    app.user_color = "#1E5B6B"
    app.net = FakeNet()
    app._build_main()
    app._apply_theme()
    tk_root.deiconify()
    tk_root.update()
    return app


@pytest.mark.parametrize("kind", list(SHAPE_KINDS))
@pytest.mark.parametrize("fill", [False, True])
def test_shape_drag_preview_and_one_op(tk_root, kind, fill):
    app = _build_app(tk_root)
    app._set_tool(kind)
    app.fill_var.set(fill)

    app.net.sent.clear()
    start = _event_at(app, 100, 100)
    app._on_press(start)
    app._on_motion(_event_at(app, 120, 110, 0))
    assert app._shape_preview is not None

    for point, state in (((130, 120), 0), ((160, 110), 1), ((190, 130), 0)):
        app._on_motion(_event_at(app, *point, state))
        assert app._shape_preview is not None

    app._on_release(_event_at(app, 200, 200))
    assert app._shape_preview is None
    shape_ops = [msg for msg in app.net.sent if msg.get("type") == "shape"]
    assert len(shape_ops) == 1
    if kind in ("line", "arrow", "double_arrow"):
        assert shape_ops[0]["fill"] is None
    else:
        assert shape_ops[0]["fill"] == (app.color if fill else None)


def test_shape_zero_size_drag_sends_nothing(tk_root):
    app = _build_app(tk_root)
    app._set_tool("rectangle")
    app.net.sent.clear()
    event = _event_at(app, 100, 100)
    app._on_press(event)
    assert app._shape_preview is None
    app._on_release(event)
    assert not [msg for msg in app.net.sent if msg.get("type") == "shape"]


def test_shape_flipped_drag_sends_one_op(tk_root):
    app = _build_app(tk_root)
    app._set_tool("ellipse")
    app.net.sent.clear()
    app._on_press(_event_at(app, 220, 210))
    app._on_motion(_event_at(app, 120, 110, 1))
    app._on_release(_event_at(app, 110, 105))
    shape_ops = [msg for msg in app.net.sent if msg.get("type") == "shape"]
    assert len(shape_ops) == 1