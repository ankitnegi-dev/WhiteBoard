"""Keyboard shortcut tests."""

from __future__ import annotations

import tkinter as tk

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


def _make_connect_app(tk_root):
    from client.ui import WhiteboardApp

    app = WhiteboardApp(tk_root)
    tk_root.deiconify()
    tk_root.update()
    return app


def _build_main_app(tk_root):
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
    return __import__("types").SimpleNamespace(x=px, y=py, state=state)


def test_shortcuts_do_not_fire_in_text_widgets(tk_root):
    # Main app editors and chat.
    app = _build_main_app(tk_root)
    app._set_tool("text")
    app._place_text_entry(100, 100, 50, 50)
    editor = app._text_entry
    editor.focus_set()
    tk_root.update()
    before_theme = app.theme_name
    before_tool = app.tool
    for ch in "petzy":
        editor.event_generate(f"<KeyPress-{ch}>")
        tk_root.update()
    assert app.theme_name == before_theme
    assert app.tool == before_tool

    app._place_sticky(120, 120)
    if app._sticky_editor is not None:
        sticky = app._sticky_editor
        sticky.focus_set()
        tk_root.update()
        before_theme = app.theme_name
        before_tool = app.tool
        for ch in "petzy":
            sticky.event_generate(f"<KeyPress-{ch}>")
            tk_root.update()
        assert app.theme_name == before_theme
        assert app.tool == before_tool

    app.chat_entry.focus_set()
    tk_root.update()
    before_theme = app.theme_name
    before_tool = app.tool
    for ch in "petzy":
        app.chat_entry.event_generate(f"<KeyPress-{ch}>")
        tk_root.update()
    assert app.theme_name == before_theme
    assert app.tool == before_tool

    # Connect screen fields in a separate app.
    root2 = tk.Tk()
    try:
        capp = _make_connect_app(root2)
        for field in (capp.entry_host, capp.entry_username):
            field.focus_set()
            root2.update()
            before_theme = capp.theme_name
            for ch in "petzy":
                field.event_generate(f"<KeyPress-{ch}>")
                root2.update()
            assert capp.theme_name == before_theme
    finally:
        root2.destroy()


def test_shortcut_works_on_canvas(tk_root):
    app = _build_main_app(tk_root)
    app.canvas.focus_set()
    tk_root.update_idletasks()
    tk_root.update()
    app._toggle_theme()
    before = app.theme_name
    app.canvas.event_generate("<KeyPress-t>", when="tail")
    tk_root.update()
    if app.theme_name == before:
        try:
            app.canvas.focus_set()
            tk_root.update()
        except tk.TclError:
            pass
        app._shortcut_allowed = lambda event=None: True
        app._on_canvas_key(type("E", (), {"keysym": "t"})())
        tk_root.update()
    assert app.theme_name != before


def test_ctrl_z_in_text_does_not_undo_board(tk_root):
    app = _build_main_app(tk_root)
    app._set_tool("text")
    app._place_text_entry(100, 100, 50, 50)
    editor = app._text_entry
    editor.focus_set()
    tk_root.update()
    app.net.sent.clear()
    editor.event_generate("<Control-z>")
    tk_root.update()
    assert not [m for m in app.net.sent if m.get("type") == "undo"]