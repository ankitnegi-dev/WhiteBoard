"""Static checks that must pass before any milestone is reported.

Pyflakes catches the exact class of bug that broke the eraser in a real run:
an undefined name inside an event handler.  The unit tests only exercise the
paths they know about; this gate covers every module.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_pyflakes_reports_no_undefined_names_or_unused_imports():
    result = subprocess.run(
        [sys.executable, "-m", "pyflakes", "client", "server", "protocol.py", "platform_utils.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, f"pyflakes failed:\n{output}"
    assert output.strip() == "", f"pyflakes reported issues:\n{output}"
