"""Small JSON store for reconnect tokens."""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any

import platform_utils

MAX_ENTRIES = 20
MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def _path() -> str:
    return os.path.join(platform_utils.user_config_dir(), "sessions.json")


def _load_raw() -> list[dict[str, Any]]:
    path = _path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    result = []
    now = time.time()
    for entry in data:
        if not isinstance(entry, dict):
            continue
        if not all(k in entry for k in ("server_host", "port", "room_code", "username", "token")):
            continue
        saved_at = entry.get("saved_at", now)
        if not isinstance(saved_at, (int, float)):
            saved_at = now
        if now - float(saved_at) > MAX_AGE_SECONDS:
            continue
        result.append(entry)
    return result


def _save_raw(entries: list[dict[str, Any]]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Keep the newest 20 entries.
    entries = sorted(entries, key=lambda e: e.get("saved_at", 0), reverse=True)[:MAX_ENTRIES]
    fd, tmp = tempfile.mkstemp(prefix="sessions.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(entries, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass


def record_session(entry: dict[str, Any]) -> None:
    entry = dict(entry)
    entry.pop("password", None)
    entry["saved_at"] = time.time()
    entries = _load_raw()
    # Replace an existing entry for the same connection.
    entries = [e for e in entries if not (
        e.get("server_host") == entry.get("server_host")
        and e.get("port") == entry.get("port")
        and e.get("room_code") == entry.get("room_code")
        and e.get("username") == entry.get("username")
    )]
    entries.insert(0, entry)
    _save_raw(entries)


def find_session(host: str, port: int, room_code: str, username: str) -> dict[str, Any] | None:
    for entry in _load_raw():
        if (entry.get("server_host") == host and entry.get("port") == port
                and entry.get("room_code") == room_code
                and entry.get("username") == username):
            return entry
    return None


def remove_session(host: str, port: int, room_code: str, username: str) -> None:
    entries = [e for e in _load_raw() if not (
        e.get("server_host") == host and e.get("port") == port
        and e.get("room_code") == room_code and e.get("username") == username)]
    _save_raw(entries)


def last_session() -> dict[str, Any] | None:
    entries = _load_raw()
    return entries[0] if entries else None