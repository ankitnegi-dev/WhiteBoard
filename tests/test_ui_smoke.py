"""Headless-safe smoke test for the Tk UI (runs on Windows with a display)."""

from __future__ import annotations

import tkinter as tk

import pytest

tkinter = pytest.importorskip("tkinter")


def _make_app():
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display available: {exc}")
    root.withdraw()
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
    root, app = _make_app()
    try:
        app.net = FakeNet()
        app.username = "host"
        app.room_code = "ABC123"
        app.role = "host"
        app.user_color = "#E6194B"
        app._build_main()
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
        app.state.prune_drawers()
        app._rebuild_participants()
        app._update_status()
    finally:
        root.destroy()


def test_smoke_connected_viewer():
    root, app = _make_app()
    try:
        app.net = FakeNet()
        app.username = "viewer"
        app.room_code = "ABC123"
        app.role = "viewer"
        app.user_color = "#4363D8"
        app._build_main()
        app.root.title(f"LAN Whiteboard - {app.username} ({app.role}) - Room {app.room_code}")

        app._handle_message({"type": "join_ok", "snapshot": {"seq": 0, "objects": [], "chat_tail": []},
                             "participants": [{"name": "viewer", "color": "#4363D8", "role": "viewer", "online": True}],
                             "chat_history": [], "seq": 0})
        app._handle_message({"type": "participant_list", "users": [
            {"name": "viewer", "color": "#4363D8", "role": "viewer", "online": True},
        ]})
        app._rebuild_participants()
        app._update_status()
        # Viewer should have clear disabled if built; not built here, but state is fine.
    finally:
        root.destroy()