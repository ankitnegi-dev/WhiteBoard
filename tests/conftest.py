"""Shared pytest fixtures for the whiteboard test suite."""

from __future__ import annotations

import time
import tkinter as tk

import pytest

_ORIGINAL_TK = tk.Tk


def _skip_message(exc: BaseException) -> str:
    return (
        "Tk/Tcl runtime is missing or broken: %s. Repair the Python "
        "install (reinstall Python with Tcl/Tk support, or set TCL_LIBRARY "
        "and TK_LIBRARY to a valid tcl/tk installation)." % exc
    )


def _create_tk(*args, **kwargs):
    """Create a Tk root, retrying the short-lived Windows teardown race."""
    last_error = None
    for _attempt in range(5):
        try:
            root = _ORIGINAL_TK(*args, **kwargs)
            root._callback_errors = []
            root.report_callback_exception = lambda et, ev, tb: root._callback_errors.append(
                (et, ev, tb))
            return root
        except tk.TclError as exc:
            last_error = exc
            time.sleep(0.1)
    pytest.skip(_skip_message(last_error))


def _make_root():
    root = _create_tk()
    root.withdraw()
    return root


@pytest.fixture
def tk_root():
    """A withdrawn Tk root; skips the test if Tcl is unavailable."""
    root = _make_root()
    try:
        yield root
    finally:
        errors = getattr(root, "_callback_errors", [])
        try:
            root.destroy()
        except Exception:
            pass
        if errors:
            exc_type, exc_value, exc_tb = errors[0]
            raise exc_value.with_traceback(exc_tb)


# Patch tkinter.Tk globally so tests that call tk.Tk() directly also skip
# instead of failing when the Tcl runtime is missing.
def _safe_tk(*args, **kwargs):
    return _create_tk(*args, **kwargs)


tk.Tk = _safe_tk