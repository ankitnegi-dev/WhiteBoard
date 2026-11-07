"""Spike: prove tkinter canvas drawing and PyInstaller packaging work.

Draws a freehand line while the mouse is dragged. This is the smallest
possible end-to-end check of the GUI stack we will use from Day 3, and of
the ``--onefile`` build on Windows. It is intentionally throwaway code.
"""

from __future__ import annotations

import tkinter as tk


def main() -> None:
    root = tk.Tk()
    root.title("hello_tk spike")
    canvas = tk.Canvas(root, width=640, height=480, bg="white")
    canvas.pack(fill="both", expand=True)

    state = {"last": None}

    def on_press(event):
        state["last"] = (event.x, event.y)

    def on_drag(event):
        if state["last"] is not None:
            x0, y0 = state["last"]
            canvas.create_line(x0, y0, event.x, event.y, width=2, fill="black")
            state["last"] = (event.x, event.y)

    def on_release(_event):
        state["last"] = None

    canvas.bind("<Button-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)

    root.mainloop()


if __name__ == "__main__":
    main()
