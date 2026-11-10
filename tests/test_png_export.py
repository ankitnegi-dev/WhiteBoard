"""Tests for PNG export."""

from __future__ import annotations

from PIL import Image

from protocol import SHAPE_KINDS


def _build_app(tk_root):
    from client.ui import WhiteboardApp

    app = WhiteboardApp(tk_root)
    app.connect_frame.destroy()
    app.username = "host"
    app.room_code = "R"
    app.role = "host"
    app._build_main()
    app._apply_theme()
    tk_root.deiconify()
    tk_root.update()
    return app


def _mixed_objects():
    objs = {}
    objs["stroke1"] = {"id": "stroke1", "type": "stroke", "tool": "pen",
                       "color": "#123456", "width": 6,
                       "points": [[100, 100], [220, 100]], "user": "host", "seq": 1}
    for index, kind in enumerate(SHAPE_KINDS):
        objs[f"shape{index}"] = {"id": f"shape{index}", "type": "shape", "kind": kind,
                                 "p1": [300 + index * 5, 100], "p2": [360 + index * 5, 160],
                                 "color": "#654321", "width": 2, "fill": "#ABCDEF",
                                 "user": "host", "seq": 2 + index}
    objs["text1"] = {"id": "text1", "type": "text", "x": 50, "y": 300,
                     "text": "Export me", "size": 24, "color": "#112233",
                     "user": "host", "seq": 100}
    objs["sticky1"] = {"id": "sticky1", "type": "sticky", "x": 500, "y": 400,
                       "text": "note", "color": "#FFF2A8", "width": 220, "height": 160,
                       "user": "host", "seq": 101}
    return objs


def test_png_export_sizes_and_pixels(tk_root, tmp_path):
    app = _build_app(tk_root)
    app.state.objects = _mixed_objects()
    app._redraw_all()

    path1 = tmp_path / "board1.png"
    app._export_board(path=str(path1), scale=1)
    assert path1.exists()
    image1 = Image.open(path1)
    assert image1.size == (1600, 900)
    # stroke pixel is the stroke color, not paper
    assert image1.getpixel((110, 100)) != (251, 248, 241)
    assert image1.getpixel((110, 100))[:3] == (0x12, 0x34, 0x56)
    # sticky area is the note color
    assert image1.getpixel((520, 450))[:3] == (0xFF, 0xF2, 0xA8)

    path2 = tmp_path / "board2.png"
    app._export_board(path=str(path2), scale=2)
    image2 = Image.open(path2)
    assert image2.size == (3200, 1800)


def test_png_export_headless_ui(tk_root, tmp_path, monkeypatch):
    from tkinter import filedialog

    app = _build_app(tk_root)
    app.state.objects = _mixed_objects()
    app._redraw_all()

    path = tmp_path / "dialog.png"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kwargs: str(path))
    app._export_board()
    assert path.exists()
    assert Image.open(path).size == (1600, 900)