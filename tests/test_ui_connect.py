"""Tests for connect form logic (requires a display)."""

from __future__ import annotations

import tkinter as tk

from client.ui import WhiteboardApp


def test_connect_mode_visibility_and_normalization():
    root = tk.Tk()
    root.withdraw()
    try:
        app = WhiteboardApp(root)
        assert app.entry_room_code.grid_info() == {}
        app.connect_mode.set("join")
        app._update_connect_mode()
        root.update_idletasks()
        assert app.entry_room_code.grid_info() != {}
        app.connect_mode.set("create")
        app._update_connect_mode()
        root.update_idletasks()
        assert app.entry_room_code.grid_info() == {}

        app.connect_mode.set("join")
        app._update_connect_mode()
        root.update_idletasks()
        app.entry_room_code.delete(0, "end")
        app.entry_room_code.insert(0, "  ab12  ")
        app._normalize_room_code()
        assert app.entry_room_code.get() == "AB12"
    finally:
        root.destroy()


def test_title_format():
    username, role, code = "alice", "host", "ABC123"
    assert f"LAN Whiteboard - {username} ({role}) - Room {code}" == "LAN Whiteboard - alice (host) - Room ABC123"