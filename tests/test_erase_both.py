"""Headless GUI tests for erase convergence."""

from __future__ import annotations

import tkinter as tk
from types import SimpleNamespace

from client.tools import logical_to_window


class FakeNet:
    def __init__(self):
        self.connected = True
        self.inbox = None
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def disconnect(self):
        self.connected = False


def _build_app(root):
    from client.ui import WhiteboardApp

    app = WhiteboardApp(root)
    app.connect_frame.destroy()
    app.username = "host"
    app.room_code = "R"
    app.role = "host"
    app.user_color = "#1E5B6B"
    app.net = FakeNet()
    app._build_main()
    app._apply_theme()
    root.deiconify()
    root.update()
    return app


def test_erase_both_canvases():
    root1 = tk.Tk()
    root2 = tk.Tk()
    try:
        app1 = _build_app(root1)
        app2 = _build_app(root2)

        oid = "stroke1"
        obj = {"id": oid, "type": "stroke", "tool": "pen", "color": "#000000",
               "width": 2, "points": [[100, 100], [200, 100]], "user": "host", "seq": 1}
        for app in (app1, app2):
            app.state.objects[oid] = dict(obj)
            app._add_stroke_item(app.state.objects[oid])

        # Erase on app1.
        width, height = app1._canvas_size()
        px, py = logical_to_window(150, 100, width, height, app1._canvas_inset())
        app1._set_tool("eraser")
        app1._on_press(SimpleNamespace(x=px, y=py, state=0))
        assert app1.canvas.find_withtag(oid) == ()
        assert oid not in app1.state.objects

        # Deliver the broadcast to app2.
        msg = app1.net.sent[-1]
        app2._handle_message(msg)
        assert app2.canvas.find_withtag(oid) == ()
        assert oid not in app2.state.objects
    finally:
        root1.destroy()
        root2.destroy()


def test_board_hash_mismatch_requests_resync():
    root = tk.Tk()
    try:
        app = _build_app(root)
        app.state.objects["x"] = {"id": "x", "type": "text", "x": 1, "y": 2,
                                  "text": "hi", "size": 12, "color": "#000000",
                                  "user": "host", "seq": 1}
        app.state.applied_seq = 7
        toasts = []
        app._toast = lambda text: toasts.append(text)
        app.net.sent.clear()
        app._handle_message({"type": "board_hash", "seq": 7, "hash": "wrong"})
        assert app._sync_state == "out of sync"
        assert any(m.get("type") == "resync_request" for m in app.net.sent)
        assert toasts
    finally:
        root.destroy()