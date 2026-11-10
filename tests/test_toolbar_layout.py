"""Layout and reachability tests for the top toolbar.

These tests exist because an earlier canvas-scroll toolbar silently clipped
buttons. Every control must fit inside the window without any scrolling.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import pytest

SIZES = [(1280, 720), (1366, 768), (1920, 1080)]


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)


def _interactive(widget):
    kinds = (ttk.Button, ttk.Checkbutton, ttk.Combobox, ttk.Scale,
             tk.Button, tk.Checkbutton)
    return [child for child in _walk(widget) if isinstance(child, kinds)]


def _relative(root, widget):
    x = widget.winfo_rootx() - root.winfo_rootx()
    y = widget.winfo_rooty() - root.winfo_rooty()
    return x, y


@pytest.fixture
def app(tk_root):
    from client.ui import WhiteboardApp

    tk_root.maxsize(4096, 2160)
    application = WhiteboardApp(tk_root)
    application.connect_frame.destroy()
    application.username = "host"
    application.room_code = "ABC123"
    application.role = "host"
    application._build_main()
    application._apply_theme()
    tk_root.deiconify()
    try:
        yield tk_root, application
    finally:
        try:
            tk_root.update_idletasks()
        except tk.TclError:
            pass


@pytest.mark.parametrize("size", SIZES)
def test_toolbar_controls_fit_without_scrolling(app, size):
    root, application = app
    width, height = size
    root.geometry(f"{width}x{height}+0+0")
    root.update()
    assert (root.winfo_width(), root.winfo_height()) == (width, height)

    toolbar = application.toolbar
    controls = _interactive(toolbar)
    assert len(controls) >= 12

    for control in controls:
        try:
            label = str(control.cget("text"))
        except tk.TclError:
            label = control.winfo_class()
        assert control.winfo_viewable(), f"{label} is not viewable"
        assert control.winfo_width() >= control.winfo_reqwidth(), f"{label} is clipped horizontally"
        assert control.winfo_height() >= control.winfo_reqheight(), f"{label} is clipped vertically"
        x, y = _relative(root, control)
        assert x >= 0 and y >= 0, f"{label} starts outside the window"
        assert x + control.winfo_width() <= width, f"{label} extends past the right edge"
        assert y + control.winfo_height() <= height, f"{label} extends past the bottom edge"
        parent = control.master
        parent_x, parent_y = _relative(root, parent)
        assert x + control.winfo_width() <= parent_x + parent.winfo_width(), \
            f"{label} overflows its row"
        assert y + control.winfo_height() <= parent_y + parent.winfo_height(), \
            f"{label} overflows its row vertically"

    scrollbars = [child for child in _walk(toolbar)
                  if isinstance(child, (ttk.Scrollbar, tk.Scrollbar))]
    assert scrollbars == []

    assert application.toolbar_row1.winfo_reqwidth() <= toolbar.winfo_width()
    assert application.toolbar_row2.winfo_reqwidth() <= toolbar.winfo_width()

    buttons = [child for child in controls
               if isinstance(child, (ttk.Button, ttk.Checkbutton,
                                     tk.Button, tk.Checkbutton))]
    heights = {button.winfo_height() for button in buttons}
    assert min(heights) >= 30
    assert max(heights) - min(heights) <= 1


def test_board_drawable_area_at_1366(app):
    root, application = app
    root.geometry("1366x768+0+0")
    root.update()
    assert application.canvas.winfo_width() >= 900
    assert application.canvas.winfo_width() >= application._canvas_inset() * 2


@pytest.mark.parametrize("size", SIZES)
def test_theme_and_cursor_toggles_are_inside_window(app, size):
    root, application = app
    width, height = size
    root.geometry(f"{width}x{height}+0+0")
    root.update()
    for widget in (application.theme_btn, application.show_cursors_btn):
        assert widget.winfo_viewable()
        assert widget.winfo_width() >= widget.winfo_reqwidth()
        assert widget.winfo_height() >= widget.winfo_reqheight()
        x, y = _relative(root, widget)
        assert x >= 0 and y >= 0
        assert x + widget.winfo_width() <= width
        assert y + widget.winfo_height() <= height


def test_shapes_split_button_and_palette(app):
    root, application = app
    root.update()

    application._select_shape("ellipse")
    assert application.tool == "ellipse"
    assert application.tool_buttons["shape"].cget("style") == "ToolActive.TButton"
    assert application.shapes_btn.cget("text") == "Shapes"
    assert application.shapes_btn.cget("image")

    application._set_tool("pen")
    application._activate_last_shape()
    assert application.tool == "ellipse"

    application._open_shapes_palette()
    assert application._shapes_palette is not None
    assert application._shapes_palette.winfo_exists()
    application._close_shapes_palette()
    assert application._shapes_palette is None


def test_new_toolbar_buttons_exist_and_disabled(app):
    root, application = app
    root.update()
    for name in ("select_btn", "sticky_btn", "export_btn"):
        button = getattr(application, name)
        assert button.winfo_class() == "TButton"
        assert not button.instate(["disabled"])
        assert button._tooltip is not None
    history = application.history_btn
    assert history.winfo_class() == "TButton"
    assert history.instate(["disabled"])
    assert history._tooltip.text == "Coming soon"
    root, application = app
    root.update()
    before = application.fill_btn.winfo_height()
    assert application.fill_btn.winfo_class() == "TButton"
    application._toggle_fill()
    assert application.fill_var.get() is True
    assert application.fill_btn.cget("style") == "ToolActive.TButton"
    application.fill_btn.update_idletasks()
    assert application.fill_btn.winfo_height() == before
    application._toggle_fill()
    assert application.fill_var.get() is False
    assert application.fill_btn.cget("style") == "Tool.TButton"
