"""Tests for the client session token store."""

from __future__ import annotations

import os

import platform_utils
import client.session_store as session_store


def test_session_store_round_trip_and_no_password(tmp_path, monkeypatch):
    monkeypatch.setattr(platform_utils, "user_config_dir", lambda app_name="lanwhiteboard": str(tmp_path))
    monkeypatch.setattr(session_store, "_path", lambda: os.path.join(str(tmp_path), "sessions.json"))
    entry = {"server_host": "127.0.0.1", "port": 5050, "room_code": "ABC",
             "username": "alice", "token": "tok", "role": "host", "password": "secret"}
    session_store.record_session(entry)
    loaded = session_store.load_sessions() if hasattr(session_store, "load_sessions") else session_store._load_raw()
    assert loaded and loaded[0]["token"] == "tok"
    assert "password" not in loaded[0]
    assert session_store.find_session("127.0.0.1", 5050, "ABC", "alice")["token"] == "tok"


def test_session_store_corrupt_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(session_store, "_path", lambda: os.path.join(str(tmp_path), "sessions.json"))
    path = os.path.join(str(tmp_path), "sessions.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert session_store._load_raw() == []
    assert session_store.last_session() is None