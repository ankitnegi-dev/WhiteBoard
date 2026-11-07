"""Entry point for the whiteboard client.

The GUI is tkinter-based. If tkinter is not installed (common on minimal
Linux installs) we print a clear instruction instead of crashing with an
ImportError traceback.
"""

from __future__ import annotations

def main() -> int:
    try:
        import tkinter  # noqa: F401
    except ImportError:
        print("tkinter is not installed.")
        print("Install it with: sudo apt install python3-tk  (Debian/Ubuntu)")
        print("or: sudo dnf install python3-tkinter  (Fedora)")
        return 1

    import tkinter as tk

    from client.ui import WhiteboardApp

    root = tk.Tk()
    app = WhiteboardApp(root)
    # Keep a reference to avoid garbage collection.
    root.app = app
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())