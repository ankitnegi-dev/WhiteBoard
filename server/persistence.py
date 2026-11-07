"""Save and load room state to JSON files under rooms/.

Day 1 stub. Persistence and autosave are implemented in Day 4.
"""

from __future__ import annotations

from pathlib import Path


def rooms_dir() -> Path:
    """Directory where saved boards live. Created on demand."""
    return Path("rooms")
