"""Headless UI tests for rejoin."""

from __future__ import annotations

import client.session_store as session_store


def _build_app(tk_root):
    from client.ui import WhiteboardApp

    app = WhiteboardApp(tk_root)
    tk_root.deiconify()
    tk_root.update()
    return app


def test_rejoin_button_disabled_without_entry(tk_root, monkeypatch):
    monkeypatch.setattr(session_store, "last_session", lambda: None)
    app = _build_app(tk_root)
    assert app.rejoin_button.instate(["disabled"])


def test_rejoin_button_fills_form(tk_root, monkeypatch):
    entry = {"server_host": "10.0.0.1", "port": 5051, "room_code": "ROOM",
             "username": "alice", "token": "tok", "role": "host", "saved_at": 1}
    monkeypatch.setattr(session_store, "last_session", lambda: entry)
    app = _build_app(tk_root)
    assert not app.rejoin_button.instate(["disabled"])
    app._rejoin_last_room()
    assert app.entry_host.get() == "10.0.0.1"
    assert app.entry_port.get() == "5051"
    assert app.entry_username.get() == "alice"
    assert app.entry_room_code.get() == "ROOM"