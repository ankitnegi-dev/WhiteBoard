"""Small helpers that isolate operating-system differences.

The rest of the codebase must not import ``platform`` or call OS-specific
APIs directly. Funneling those calls through here keeps the Windows and
Linux paths testable and makes a future port (for example macOS) a single-
file change.
"""

from __future__ import annotations

import os
import platform
import socket
from typing import List


def is_windows() -> bool:
    """Return True when running on Windows."""
    return platform.system() == "Windows"


def is_linux() -> bool:
    """Return True when running on Linux."""
    return platform.system() == "Linux"


def get_lan_ips() -> List[str]:
    """Best-effort list of non-loopback IPv4 addresses on this machine.

    Used at server startup so the operator can tell clients which IP to
    connect to. We try two independent methods and merge the results because
    either one can fail depending on the OS and network setup.

    No packets are sent; the UDP "connect" trick only consults the routing
    table, so this works on an isolated LAN with no internet access.
    """
    found: set[str] = set()

    # Method 1: resolve the hostname. Works well on Windows and most Linux
    # desktops, but can return only 127.0.0.1 on minimal installs.
    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            if ip and not ip.startswith("127."):
                found.add(ip)
    except OSError:
        pass

    # Method 2: ask the kernel which source address it would use to reach
    # an external address. We never actually send anything.
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            ip = probe.getsockname()[0]
            if ip and not ip.startswith("127."):
                found.add(ip)
        finally:
            probe.close()
    except OSError:
        pass

    if not found:
        found.add("127.0.0.1")
    return sorted(found)


def user_config_dir(app_name: str = "lanwhiteboard") -> str:
    """Return a per-user config directory for the whiteboard."""
    if is_windows():
        base = os.environ.get("APPDATA")
        if not base:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "LANWhiteboard")
    base = os.environ.get("XDG_CONFIG_HOME")
    if not base:
        base = os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, app_name)