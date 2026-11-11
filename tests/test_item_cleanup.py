"""Canvas item cleanup tests for undo, redo, and erase."""

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


def _baseline(app):
    return len(app.canvas.find_all())


def _assert_no_tag(app, tag):
    assert app.canvas.find_withtag(tag) == ()


def _assert_has_tag(app, tag):
    assert len(app.canvas.find_withtag(tag)) > 0


def test_item_cleanup_stroke(tk_root):
    app = _build_app(tk_root)
    base = _baseline(app)
    app._set_tool("pen")
    e = _event_at(app, 100, 100)
    app._on_press(e)
    app._on_release(e)
    oid = next(k for k, v in app.state.objects.items() if v.get("type") == "stroke")
    assert len(app.canvas.find_all()) > base
    _assert_has_tag(app, oid)

    app._handle_message({"type": "undo", "action": "remove_stroke", "target_id": oid})
    assert len(app.canvas.find_all()) == base
    _assert_no_tag(app, oid)

    app.state.objects[oid] = {"id": oid, "type": "stroke", "tool": "pen",
                              "color": "#000000", "width": 2, "points": [[100, 100]],
                              "user": "host", "seq": 0}
    app._handle_message({"type": "redo", "action": "add_stroke", "object": app.state.objects[oid]})
    assert len(app.canvas.find_all()) > base
    _assert_has_tag(app, oid)

    app._handle_message({"type": "delete_object", "target_id": oid})
    assert len(app.canvas.find_all()) == base
    _assert_no_tag(app, oid)


def test_item_cleanup_shape(tk_root):
    app = _build_app(tk_root)
    base = _baseline(app)
    app._set_tool("rectangle")
    app._on_press(_event_at(app, 100, 100))
    app._on_motion(_event_at(app, 180, 140))
    app._on_release(_event_at(app, 180, 140))
    oid = next(k for k, v in app.state.objects.items() if v.get("type") == "shape")
    assert len(app.canvas.find_all()) > base
    _assert_has_tag(app, oid)

    obj = dict(app.state.objects[oid])
    app._handle_message({"type": "undo", "action": "remove_object", "target_id": oid})
    assert len(app.canvas.find_all()) == base
    _assert_no_tag(app, oid)

    app.state.objects[oid] = dict(obj)
    app._handle_message({"type": "redo", "action": "add_object", "object": app.state.objects[oid]})
    assert len(app.canvas.find_all()) > base
    _assert_has_tag(app, oid)

    app._handle_message({"type": "delete_object", "target_id": oid})
    assert len(app.canvas.find_all()) == base
    _assert_no_tag(app, oid)


def test_item_cleanup_text(tk_root):
    import uuid
    app = _build_app(tk_root)
    base = _baseline(app)
    oid = str(uuid.uuid4())
    obj = {"id": oid, "type": "text", "x": 100, "y": 100, "text": "hello",
           "size": 12, "color": "#222222", "user": "host", "seq": 0}
    app.state.objects[oid] = obj
    app._add_text_item(obj)
    assert len(app.canvas.find_all()) > base
    _assert_has_tag(app, oid)

    app._handle_message({"type": "undo", "action": "remove_object", "target_id": oid})
    assert len(app.canvas.find_all()) == base
    _assert_no_tag(app, oid)

    app.state.objects[oid] = dict(obj)
    app._handle_message({"type": "redo", "action": "add_object", "object": app.state.objects[oid]})
    assert len(app.canvas.find_all()) > base
    _assert_has_tag(app, oid)

    app._handle_message({"type": "delete_object", "target_id": oid})
    assert len(app.canvas.find_all()) == base
    _assert_no_tag(app, oid)


def test_item_cleanup_sticky(tk_root):
    app = _build_app(tk_root)
    base = _baseline(app)
    app._place_sticky(100, 100)
    if app._sticky_editor is not None:
        app._sticky_editor.destroy()
        app._sticky_editor = None
    oid = next(k for k, v in app.state.objects.items() if v.get("type") == "sticky")
    assert len(app.canvas.find_all()) > base
    _assert_has_tag(app, oid)

    obj = dict(app.state.objects[oid])
    app._handle_message({"type": "undo", "action": "remove_object", "target_id": oid})
    assert len(app.canvas.find_all()) == base
    _assert_no_tag(app, oid)

    app.state.objects[oid] = dict(obj)
    app._handle_message({"type": "redo", "action": "add_object", "object": app.state.objects[oid]})
    assert len(app.canvas.find_all()) > base
    _assert_has_tag(app, oid)

    app._handle_message({"type": "delete_object", "target_id": oid})
    assert len(app.canvas.find_all()) == base
    _assert_no_tag(app, oid)


def test_two_clients_item_counts(tk_root):
    app = _build_app(tk_root)
    base = _baseline(app)
    oids = []
    for i in range(5):
        oid = f"n{i}"
        obj = {"id": oid, "type": "sticky", "x": 100 + i * 30, "y": 100,
               "text": f"note {i}", "color": "#FFF2A8", "width": 220,
               "height": 160, "user": "host", "seq": 0}
        app.state.objects[oid] = obj
        app._add_sticky_item(obj)
        oids.append(oid)
    assert len(app.canvas.find_all()) > base

    for oid in oids[:3]:
        app._handle_message({"type": "undo", "action": "remove_object", "target_id": oid})
    obj = {"id": oids[0], "type": "sticky", "x": 100, "y": 100, "text": "note 0",
           "color": "#FFF2A8", "width": 220, "height": 160, "user": "host", "seq": 0}
    app.state.objects[oids[0]] = obj
    app._handle_message({"type": "redo", "action": "add_object", "object": obj})
    app._handle_message({"type": "delete_object", "target_id": oids[3]})

    for oid in (oids[0], oids[4]):
        _assert_has_tag(app, oid)
    for oid in (oids[1], oids[2], oids[3]):
        _assert_no_tag(app, oid)