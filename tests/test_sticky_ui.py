"""Headless UI tests for sticky notes."""

from __future__ import annotations

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


def _build_app(tk_root):
    from client.ui import WhiteboardApp

    app = WhiteboardApp(tk_root)
    app.connect_frame.destroy()
    app.username = "host"
    app.room_code = "R"
    app.role = "host"
    app.user_color = "#1E5B6B"
    app.net = FakeNet()
    app._build_main()
    app._apply_theme()
    tk_root.deiconify()
    tk_root.update()
    return app


def _event_at(app, lx, ly, state=0):
    width, height = app._canvas_size()
    px, py = logical_to_window(lx, ly, width, height, app._canvas_inset())
    return SimpleNamespace(x=px, y=py, state=state)


def test_sticky_click_creates_one_op(tk_root):
    app = _build_app(tk_root)
    app._set_tool("sticky")
    app.net.sent.clear()
    app._on_press(_event_at(app, 120, 120))
    assert len([m for m in app.net.sent if m.get("type") == "sticky"]) == 1


def test_sticky_double_click_edit_queues_edit_object(tk_root):
    app = _build_app(tk_root)
    app.state.objects["note1"] = {"id": "note1", "type": "sticky", "x": 100, "y": 100,
                                  "text": "old", "color": "#FFF2A8", "width": 220,
                                  "height": 160, "user": "host", "seq": 1}
    app._add_sticky_item(app.state.objects["note1"])
    tk_root.update()

    app._on_double_click(_event_at(app, 110, 110))
    assert app._sticky_editor is not None
    app._sticky_editor.delete("1.0", "end")
    app._sticky_editor.insert("1.0", "new text")
    app._sticky_editor.event_generate("<FocusOut>")
    tk_root.update()
    edits = [m for m in app.net.sent if m.get("type") == "edit_object"]
    assert len(edits) == 1
    assert edits[0]["text"] == "new text"


def test_sticky_editor_colors_and_empty(tk_root):
    app = _build_app(tk_root)
    app.state.objects["note1"] = {"id": "note1", "type": "sticky", "x": 100, "y": 100,
                                  "text": "old", "color": "#FFF2A8", "width": 220,
                                  "height": 160, "user": "host", "seq": 1}
    app._add_sticky_item(app.state.objects["note1"])
    tk_root.update()

    app._open_sticky_editor("note1", initial="")
    assert app._sticky_editor is not None
    assert app._sticky_editor.cget("bg") == "#FFF2A8"
    assert app._sticky_editor.cget("fg") == "#222222"
    assert app._sticky_editor.get("1.0", "end-1c") == ""


def test_select_drag_sends_one_move_object(tk_root):
    app = _build_app(tk_root)
    app.state.objects["note1"] = {"id": "note1", "type": "sticky", "x": 100, "y": 100,
                                  "text": "old", "color": "#FFF2A8", "width": 220,
                                  "height": 160, "user": "host", "seq": 1}
    app._add_sticky_item(app.state.objects["note1"])
    tk_root.update()

    app._set_tool("select")
    app.net.sent.clear()
    app._on_press(_event_at(app, 110, 110))
    app._on_motion(_event_at(app, 150, 140))
    app._on_release(_event_at(app, 150, 140))
    moves = [m for m in app.net.sent if m.get("type") == "move_object"]
    assert len(moves) == 1