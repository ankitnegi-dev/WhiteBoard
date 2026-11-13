"""Static check that UI network-client attribute access is valid."""

from __future__ import annotations

import ast
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_ui_network_client_attributes_exist():
    path = os.path.join(ROOT, "client", "ui.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    attrs = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute):
            if (isinstance(node.value.value, ast.Name) and node.value.value.id == "self"
                    and node.value.attr == "net"):
                attrs.add(node.attr)
    from client.net import NetworkClient
    instance = NetworkClient("127.0.0.1", 1)
    missing = sorted(a for a in attrs if not hasattr(instance, a))
    assert not missing, f"missing NetworkClient attributes: {missing}"