"""Static checks for packaging configuration."""

from __future__ import annotations

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_build_scripts_include_required_modules():
    server = open(os.path.join(ROOT, "build", "build_server.bat")).read()
    client = open(os.path.join(ROOT, "build", "build_client.bat")).read()
    for name in ("protocol", "platform_utils", "server.client_handler",
                 "server.room_manager", "server.persistence"):
        assert name in server
    for name in ("protocol", "shapes_geometry", "platform_utils", "client.ui",
                 "client.state", "client.tools", "client.net"):
        assert name in client
    assert "--collect-all tkinter" in client


def test_gitignore_excludes_build_outputs():
    text = open(os.path.join(ROOT, ".gitignore")).read()
    for entry in ("dist/", ".pyi_work/", "*.spec", "release/"):
        assert entry in text