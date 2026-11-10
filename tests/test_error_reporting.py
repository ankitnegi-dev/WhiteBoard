"""Tests for rate-limited UI exception reporting."""

from __future__ import annotations

import logging
import sys
import time


def test_report_exception_dedupes(tk_root):
    from client.ui import WhiteboardApp

    app = WhiteboardApp(tk_root)
    app.connect_frame.destroy()
    app.username = "host"
    app.room_code = "ABC123"
    app.role = "host"
    app._build_main()
    app._apply_theme()
    tk_root.deiconify()
    tk_root.update()

    class FakeLogger:
        def __init__(self):
            self.errors = []

        def error(self, msg, *args, **kwargs):
            self.errors.append((msg, args))

    fake = FakeLogger()

    original_get_logger = logging.getLogger

    def fake_get_logger(name=None):
        return fake

    logging.getLogger = fake_get_logger
    try:
        toasts = []
        app._toast = lambda text: toasts.append(text)

        try:
            raise ValueError("boom")
        except ValueError:
            exc_info = sys.exc_info()

        app._report_exception(*exc_info)
        app._report_exception(*exc_info)
        assert len(fake.errors) == 1
        assert "unhandled UI exception" in fake.errors[0][0]
        assert len(toasts) == 1

        app._ui_error_last_summary = 0.0
        app._ui_error_last_toast = time.time() + 10.0
        app._report_exception(*exc_info)
        assert len(fake.errors) == 2
        assert "repeated" in fake.errors[1][0].lower()
        assert len(toasts) == 1
    finally:
        logging.getLogger = original_get_logger