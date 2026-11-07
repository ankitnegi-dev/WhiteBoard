"""Client entry point.

Day 1: no GUI yet. This module will host the connect dialog and main
window from Day 3 onward. For now it prints a pointer to the CLI test
client in tests/fake_client.py.
"""

from __future__ import annotations

import sys


def main() -> int:
    print("Whiteboard client GUI is implemented from Day 3.")
    print("For Day 1 testing use: python tests/fake_client.py --host 127.0.0.1 --port 5050")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
