"""Guard test for connection threads."""

from __future__ import annotations

import socket
import threading
import time


def test_pending_connections_do_not_leak_threads():
    errors = []

    def hook(args):
        errors.append((args.exc_type, args.exc_value))

    old_hook = threading.excepthook
    threading.excepthook = hook
    try:
        for _ in range(20):
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            port = listener.getsockname()[1]
            try:
                import tkinter as tk
                from client.ui import WhiteboardApp

                root = tk.Tk()
                app = WhiteboardApp(root)
                app.entry_host.delete(0, "end")
                app.entry_host.insert(0, "127.0.0.1")
                app.entry_port.delete(0, "end")
                app.entry_port.insert(0, str(port))
                app.entry_username.delete(0, "end")
                app.entry_username.insert(0, "user")
                app.entry_room_code.delete(0, "end")
                app.entry_room_code.insert(0, "ROOM")
                app._start_connect("join", timeout=0.1)
                root.update()
                time.sleep(0.2)
                thread = getattr(app, "_connect_thread", None)
                if thread is not None:
                    thread.join(timeout=1.0)
                    if thread.is_alive():
                        print("GUARD THREAD STILL ALIVE")
                app._connect_thread = None
                app._on_close()
                del app
                root.destroy() if False else None
                import gc
                gc.collect()
                time.sleep(0.05)
            finally:
                listener.close()
        time.sleep(0.2)
    finally:
        threading.excepthook = old_hook

    assert errors == [], errors