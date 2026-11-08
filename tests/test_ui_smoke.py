"""Headless-safe smoke tests for the Tk UI (Windows with a display)."""

from __future__ import annotations

import time
import tkinter as tk
from types import SimpleNamespace

import pytest

from client.tools import logical_to_window

tkinter = pytest.importorskip("tkinter")


def _make_app():
    # Tk can occasionally fail to re-open its Tcl library immediately after
    # a previous root is destroyed on Windows.  Retry briefly before giving
    # up; a real headless environment still skips below.
    root = None
    last_error = None
    for _attempt in range(5):
        try:
            root = tk.Tk()
            break
        except tk.TclError as exc:
            last_error = exc
            time.sleep(0.1)
    if root is None:
        pytest.skip(f"no display available: {last_error}")
    from client.ui import WhiteboardApp

    app = WhiteboardApp(root)
    return root, app


class FakeNet:
    def __init__(self):
        self.connected = True
        self.inbox = None
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)

    def disconnect(self):
        self.connected = False


def _event(x, y):
    return SimpleNamespace(x=x, y=y)


def _build_main_app(role="host", username="host"):
    root, app = _make_app()
    root.geometry("1280x800")
    root.deiconify()
    app.net = FakeNet()
    app.username = username
    app.room_code = "ABC123"
    app.role = role
    app.user_color = "#E6194B"
    app._build_main()
    root.update()
    return root, app


def _event_at(app, lx, ly):
    """Build a fake event whose raw pixel maps to the given logical point."""
    w, h = app._canvas_size()
    px, py = logical_to_window(lx, ly, w, h, app._canvas_inset())
    return _event(px, py)


def test_smoke_not_connected():
    root, app = _make_app()
    try:
        app.net = None
        app._update_status()
        app.root.title(f"LAN Whiteboard - {app.username or 'guest'} (viewer) - Room -")
        app._rebuild_participants()
        app.state.prune_drawers()
    finally:
        root.destroy()


def test_smoke_connected_host():
    root, app = _build_main_app()
    try:
        app.root.title(f"LAN Whiteboard - {app.username} ({app.role}) - Room {app.room_code}")
        app.identity_name.config(text=app.username)
        app.identity_role.config(text=app.role)

        snapshot = {"seq": 0, "objects": [], "chat_tail": []}
        app._handle_message({"type": "join_ok", "snapshot": snapshot, "participants": [
            {"name": "host", "color": "#E6194B", "role": "host", "online": True},
        ], "chat_history": [], "seq": 0})
        app._handle_message({"type": "participant_list", "users": [
            {"name": "host", "color": "#E6194B", "role": "host", "online": True},
        ]})

        # Feed a stroke sequence.
        app._handle_message({"type": "stroke_start", "seq": 1, "op_id": "o1", "stroke_id": "s1",
                             "tool": "pen", "color": "#000000", "width": 2, "x": 1, "y": 2, "user": "remote"})
        app._handle_message({"type": "stroke_points", "seq": 2, "op_id": "o2", "stroke_id": "s1",
                             "points": [[1, 2], [3, 4]], "user": "remote"})
        app._handle_message({"type": "stroke_end", "seq": 3, "op_id": "o3", "stroke_id": "s1", "user": "remote"})
        app._handle_message({"type": "user_joined", "username": "remote", "color": "#3CB44B", "role": "editor"})

        assert "s1" in app.state.objects
        # Remote strokes are stored in logical units but must be drawn in
        # canvas pixels through the same transform.
        w, h = app._canvas_size()
        expected = logical_to_window(1, 2, w, h, app._canvas_inset())
        coords = app.canvas.coords(app.canvas_items["s1"])
        assert coords[0] == pytest.approx(expected[0], abs=1.0)
        assert coords[1] == pytest.approx(expected[1], abs=1.0)
        app.state.prune_drawers()
        app._rebuild_participants()
        app._update_status()
    finally:
        root.destroy()


def test_switching_to_eraser_keeps_color():
    root, app = _build_main_app()
    try:
        before = app.color
        app._set_tool("eraser")
        assert app.color == before
        assert app.color_swatch.cget("bg") == before
    finally:
        root.destroy()


def test_eraser_press_and_drag_queue_deletes():
    root, app = _build_main_app()
    try:
        app.state.objects["s1"] = {"id": "s1", "type": "stroke",
                                   "points": [[100, 100], [200, 100]], "seq": 1}
        app.state.objects["s2"] = {"id": "s2", "type": "stroke",
                                   "points": [[100, 150], [200, 150]], "seq": 2}
        app.tool = "eraser"
        app._on_press(_event_at(app, 150, 100))
        app._on_motion(_event_at(app, 150, 150))
        deletes = [m["target_id"] for m in app.net.sent if m.get("type") == "delete_object"]
        assert deletes == ["s1", "s2"]
    finally:
        root.destroy()


def test_eraser_uses_canvas_tags_and_is_safe_with_missing_item():
    root, app = _build_main_app()
    try:
        app.state.objects["s1"] = {"id": "s1", "type": "stroke",
                                   "points": [[100, 100], [200, 100]], "seq": 1}
        app.tool = "eraser"
        # No canvas item exists for this state-only stroke; the hover code
        # must not raise, and the delete still targets the right id.
        app._on_press(_event_at(app, 150, 100))
        deletes = [m for m in app.net.sent if m.get("type") == "delete_object"]
        assert deletes and deletes[-1]["target_id"] == "s1"
    finally:
        root.destroy()


def test_viewer_cannot_erase_or_draw():
    root, app = _build_main_app(role="viewer", username="viewer")
    try:
        app.state.objects["s1"] = {"id": "s1", "type": "stroke",
                                   "points": [[100, 100], [200, 100]], "seq": 1}
        app.tool = "eraser"
        app._on_press(_event_at(app, 150, 100))
        app.tool = "pen"
        app._on_press(_event_at(app, 120, 120))
        assert not [m for m in app.net.sent if m.get("type") in ("delete_object", "stroke_start")]
        assert "Viewers cannot" in app.status_conn.cget("text")
    finally:
        root.destroy()


def test_pen_press_motion_release_starts_at_cursor():
    root, app = _build_main_app()
    try:
        app.tool = "pen"
        press = _event_at(app, 321.5, 222.25)
        app._on_press(press)
        start = [m for m in app.net.sent if m.get("type") == "stroke_start"][0]
        sid = start["stroke_id"]
        # The visible polyline starts at the raw press pixel, not at the
        # logical coordinate misread as a pixel.  This is the regression
        # that made the pen appear offset from the cursor.
        item = app.canvas_items[sid]
        coords = app.canvas.coords(item)
        expected = app._to_logical(press.x, press.y)
        assert coords[0] == pytest.approx(press.x, abs=1.0)
        assert coords[1] == pytest.approx(press.y, abs=1.0)
        assert start["x"] == pytest.approx(expected[0], abs=1e-6)
        assert start["y"] == pytest.approx(expected[1], abs=1e-6)

        app._on_motion(_event_at(app, 400, 300))
        app._on_release(_event_at(app, 400, 300))
        ends = [m for m in app.net.sent if m.get("type") == "stroke_end"]
        assert ends

        # The first stroke_points batch carries the press point exactly once.
        all_points = []
        for msg in app.net.sent:
            if msg.get("type") == "stroke_points":
                all_points.extend(msg["points"])
        first = (start["x"], start["y"])
        matches = [p for p in all_points
                   if p[0] == pytest.approx(first[0], abs=1e-9)
                   and p[1] == pytest.approx(first[1], abs=1e-9)]
        assert len(matches) == 1
        assert all(0 <= p[0] <= 1600 and 0 <= p[1] <= 900 for p in all_points)

        # A full redraw must keep the first pixel under the press pixel.
        app._redraw_all()
        redrawn = app.canvas.coords(app.canvas_items[sid])
        assert redrawn[0] == pytest.approx(press.x, abs=1.0)
        assert redrawn[1] == pytest.approx(press.y, abs=1.0)
    finally:
        root.destroy()


def test_pen_ignores_press_outside_board():
    root, app = _build_main_app()
    try:
        app.tool = "pen"
        # The canvas origin is in the letterbox/surround, not on the paper,
        # for a 1280x800 window with a toolbar and right rail.
        assert not app._over_board(0, 0)
        app._on_press(_event(0, 0))
        assert not [m for m in app.net.sent if m.get("type") == "stroke_start"]
    finally:
        root.destroy()


def test_pen_motion_is_clamped_to_board():
    root, app = _build_main_app()
    try:
        app.tool = "pen"
        app._on_press(_event_at(app, 1590, 890))
        # Drag far past the right/bottom edge.
        app._on_motion(_event(5000, 5000))
        sid = list(app.local_strokes)[0]
        for pt in app.local_strokes[sid]["points"]:
            assert 0 <= pt[0] <= 1600
            assert 0 <= pt[1] <= 900
        app._on_release(_event(5000, 5000))
    finally:
        root.destroy()


def test_participants_same_data_twice_does_not_rebuild_rows():
    root, app = _build_main_app()
    try:
        users = [
            {"name": "host", "color": "#E6194B", "role": "host", "online": True},
            {"name": "bob", "color": "#3CB44B", "role": "editor", "online": True},
        ]
        app.state.update_participants(users)
        app._rebuild_participants()
        rows_first = {name: info["name_label"].winfo_id() for name, info in app._participant_rows.items()}
        app.state.update_participants([dict(u) for u in users])
        app._rebuild_participants()
        rows_second = {name: info["name_label"].winfo_id() for name, info in app._participant_rows.items()}
        assert rows_first == rows_second
    finally:
        root.destroy()


def test_debug_overlay_reports_raw_and_logical():
    root, app = _build_main_app()
    try:
        app._toggle_debug_overlay()
        event = _event_at(app, 800, 450)
        app._update_debug_overlay(event.x, event.y, *app._to_logical(event.x, event.y))
        text = app.debug_label.cget("text")
        assert "raw=" in text and "logical=" in text and "board_px=" in text
        app._toggle_debug_overlay()
    finally:
        root.destroy()


def test_disabled_buttons_are_readable_in_both_themes():
    from client.ui import DARK, LIGHT

    root, app = _build_main_app(role="editor", username="editor")
    try:
        for theme in (LIGHT, DARK):
            app.theme = theme
            app._apply_theme()
            style = app.root.tk.call("ttk::style", "lookup", "TButton", "-foreground", "disabled")
            bg = app.root.tk.call("ttk::style", "lookup", "TButton", "-background", "disabled")
            assert _contrast(str(style), str(bg)) >= 3.0
            assert app.width_preview is not None
            assert app.clear_btn.instate(["disabled"])
            assert app.undo_btn.instate(["!disabled"])
            assert app.clear_btn._tooltip.text == "Host only"
    finally:
        root.destroy()


def test_dark_theme_board_chat_dropdown_and_selected_tool():
    from client.ui import DARK

    root, app = _build_main_app(role="editor", username="editor")
    try:
        app.theme_name = "dark"
        app.theme = DARK
        app._apply_theme()
        assert app.canvas.cget("bg") == DARK["canvas"]
        board_item = app.canvas.find_withtag("board_bg")[0]
        assert app.canvas.itemcget(board_item, "fill") == DARK["paper"]
        assert _contrast(app.color, DARK["paper"]) >= 4.5

        app._append_chat("bob", "body text must stay visible", None)
        assert app.chat_text.tag_cget("body_tag", "foreground") == DARK["ink"]
        app._toggle_theme()
        app._toggle_theme()
        assert app.chat_text.tag_cget("body_tag", "foreground") == DARK["ink"]

        dot_item = app.width_preview.find_all()[0]
        assert app.width_preview.itemcget(dot_item, "fill") == DARK["accent"]

        app._set_tool("pen")
        assert app.tool_buttons["pen"].cget("style") == "ToolActive.TButton"
        assert app.tool_buttons["eraser"].cget("style") == "Tool.TButton"

        field_bg = app.root.tk.call("ttk::style", "lookup", "TCombobox", "-fieldbackground", "readonly")
        field_fg = app.root.tk.call("ttk::style", "lookup", "TCombobox", "-foreground", "readonly")
        assert str(field_bg) == DARK["surface"]
        assert str(field_fg) == DARK["ink"]
    finally:
        root.destroy()


def _luminance(hexcolor):
    value = hexcolor.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def chan(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast(a, b):
    la, lb = _luminance(a), _luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def test_theme_contrast_pairs():
    from client.ui import DARK, LIGHT

    pairs = [
        ("ink", "bg"),
        ("ink", "surface"),
        ("ink_muted", "bg"),
        ("ink_muted", "surface"),
        ("ink_muted", "surface_alt"),   # disabled text
        ("accent", "bg"),
        ("accent", "surface"),
        ("on_accent", "accent"),        # selected tool label
        ("board_accent", "paper"),      # eraser highlight / board ink
        ("danger", "surface"),
    ]
    for name, theme in (("light", LIGHT), ("dark", DARK)):
        for fg_key, bg_key in pairs:
            ratio = _contrast(theme[fg_key], theme[bg_key])
            assert ratio >= 3.0, f"{name}: {fg_key} on {bg_key} is {ratio:.2f}:1"
        assert _contrast(theme["paper"], theme["canvas"]) >= 1.15
        assert _contrast(theme["paper_border"], theme["paper"]) >= 3.0


def test_smoke_connected_viewer():
    root, app = _build_main_app(role="viewer", username="viewer")
    try:
        app.root.title(f"LAN Whiteboard - {app.username} ({app.role}) - Room {app.room_code}")
        app._handle_message({"type": "join_ok", "snapshot": {"seq": 0, "objects": [], "chat_tail": []},
                             "participants": [{"name": "viewer", "color": "#4363D8", "role": "viewer", "online": True}],
                             "chat_history": [], "seq": 0})
        app._handle_message({"type": "participant_list", "users": [
            {"name": "viewer", "color": "#4363D8", "role": "viewer", "online": True},
        ]})
        app._rebuild_participants()
        app._update_status()
        assert app.clear_btn.instate(["disabled"])
        assert app.tool_buttons["pen"].instate(["disabled"])
        assert app.tool_buttons["pen"]._tooltip.text == "Viewers cannot do this"
        assert app.clear_btn._tooltip.text == "Viewers cannot do this"
    finally:
        root.destroy()
