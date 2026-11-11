"""Entry point for the whiteboard client.

The GUI is tkinter-based. If tkinter is not installed (common on minimal
Linux installs) we print a clear instruction instead of crashing with an
ImportError traceback.
"""

from __future__ import annotations

def main() -> int:
    log_path = None
    try:
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
        root.app = app
        root.mainloop()
        return 0
    except Exception:
        import os
        import sys
        import traceback

        if getattr(sys, "frozen", False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.getcwd()
        log_path = os.path.join(base, "whiteboard-client.log")
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(traceback.format_exc())
            handle.write("\n")
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("Whiteboard client", f"An error was logged to {log_path}")
            root.destroy()
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())