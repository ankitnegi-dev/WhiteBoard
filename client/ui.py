"""Tkinter user interface for the whiteboard client.

Only this module touches tkinter widgets. It talks to the network layer
through client.net.NetworkClient and to the board model through
client.state.BoardState. Incoming network messages are polled from the
main thread via root.after.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from typing import Any, Optional

try:
    import tkinter as tk
    from tkinter import ttk, colorchooser, simpledialog
except ImportError:  # pragma: no cover - handled in main.py
    raise

from client.net import NetworkClient
from client.state import BoardState
from client.tools import (
    LOGICAL_HEIGHT,
    LOGICAL_WIDTH,
    board_rect,
    canvas_transform,
    find_stroke_at,
    is_inside_board,
    logical_to_window,
    shift_constraint,
    window_to_logical,
)

# --------------------------------------------------------------------------- #
# Theme palettes (see docs/design-system.md)
# --------------------------------------------------------------------------- #

LIGHT = {
    "bg": "#F4F1EA",
    "surface": "#FFFFFF",
    "surface_alt": "#ECE7DC",
    "ink": "#1F2937",
    "ink_muted": "#6B7280",
    "accent": "#1E5B6B",
    "accent_hover": "#174A57",
    "on_accent": "#FFFFFF",
    "highlight": "#E8B923",
    "danger": "#B83A2E",
    "border": "#D6D0C4",
    "canvas": "#DCD3C1",
    # The board itself is always light paper so ink stays readable even
    # when the surrounding chrome switches to the dark theme.
    "paper": "#FBF8F1",
    "paper_border": "#8A7F68",
    "board_accent": "#1E5B6B",
}

DARK = {
    "bg": "#1B1F24",
    "surface": "#262B33",
    "surface_alt": "#1F2329",
    "ink": "#E8E6E1",
    "ink_muted": "#9CA3AF",
    "accent": "#5FA8A8",
    "accent_hover": "#72B8B8",
    "on_accent": "#102022",
    "highlight": "#D6A84F",
    "danger": "#D06050",
    "border": "#3A4048",
    "canvas": "#12161A",
    "paper": "#FBF8F1",
    "paper_border": "#8A7F68",
    "board_accent": "#1E5B6B",
}

USER_COLORS_FALLBACK = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4"]


class WhiteboardApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.report_callback_exception = self._report_exception
        self.root.title("LAN Collaborative Whiteboard")
        self.root.geometry("1280x800")
        self.root.minsize(1280, 720)

        self.theme_name = "light"
        self.theme = LIGHT

        self.net: Optional[NetworkClient] = None
        self.state = BoardState()
        self.username: str = ""
        self.room_code: str = ""
        self.role: str = "editor"
        self.token: str = ""
        self._connect_params: Optional[dict[str, Any]] = None

        # Reconnection state.
        self._reconnecting: bool = False
        self._reconnect_backoff: float = 1.0
        self._reconnect_after_id: Optional[str] = None
        self._pending_ops: dict[str, dict[str, Any]] = {}
        self._sync_state: str = "synced"
        self._resync_pending: bool = False
        self._ui_error_counts: dict[tuple, int] = {}
        self._ui_error_last_full: dict[tuple, float] = {}
        self._ui_error_last_summary: float = 0.0
        self._ui_error_last_toast: float = 0.0
        self._UI_ERROR_LOG_INTERVAL: float = 5.0
        self._UI_ERROR_TOAST_INTERVAL: float = 5.0

        # Drawing state.
        self.tool = "pen"
        self.color = "#1F2937"
        self.user_color = "#1E5B6B"
        self.width = 2.0
        self.canvas_items: dict[str, int] = {}   # stroke_id -> canvas item id
        self.cursor_items: dict[str, Any] = {}   # username -> (oval, text)
        self._cursor_last: dict[str, float] = {}
        self.local_strokes: dict[str, dict[str, Any]] = {}
        self._shape_start = None
        self._shape_kind = None
        self._shape_preview = None
        self._text_entry = None
        self._text_pos = None
        self._last_cursor_send = 0.0
        self._blink = False
        self._last_ping_time = 0.0
        self._ping_ts = 0.0
        self.debug_overlay = False
        self.debug_label = None

        # Scaling state for the logical 1600x900 canvas.
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0

        self._show_connect()

    # ------------------------------------------------------------------ #
    # Connect screen
    # ------------------------------------------------------------------ #
    def _show_connect(self) -> None:
        self.connect_frame = ttk.Frame(self.root, padding=0)
        self.connect_frame.pack(fill="both", expand=True)

        # Centered card.
        wrapper = ttk.Frame(self.connect_frame)
        wrapper.place(relx=0.5, rely=0.5, anchor="center")

        card = ttk.Frame(wrapper, padding=32, relief="solid", borderwidth=1)
        card.pack()

        title = ttk.Label(card, text="LAN Collaborative Whiteboard", font=("Segoe UI", 24, "bold"))
        title.pack(pady=(0, 4))
        subtitle = ttk.Label(card, text="Connect to a server on your LAN", font=("Segoe UI", 11))
        subtitle.pack(pady=(0, 16))

        self.connect_mode = tk.StringVar(value="create")
        tabs = ttk.Frame(card)
        tabs.pack(pady=(0, 12))
        ttk.Radiobutton(tabs, text="Create room", variable=self.connect_mode, value="create",
                        command=self._update_connect_mode).pack(side="left", padx=8)
        ttk.Radiobutton(tabs, text="Join room", variable=self.connect_mode, value="join",
                        command=self._update_connect_mode).pack(side="left", padx=8)

        form = ttk.Frame(card)
        form.pack()

        self._add_field(form, "Server IP", "host", "127.0.0.1", 0)
        self._add_field(form, "Port", "port", "5050", 1)
        self._add_field(form, "Username", "username", "", 2)
        self.room_code_row = 3
        self._add_field(form, "Room code", "room_code", "", self.room_code_row)
        self._add_field(form, "Password", "password", "", 4, show="*")

        self.room_code_hint = ttk.Label(card, text="The server generates the room code for you.", font=("Segoe UI", 9, "italic"))
        self.room_code_hint.pack(pady=(4, 0))

        self.connect_status = ttk.Label(card, text="", foreground="#B83A2E", font=("Segoe UI", 10))
        self.connect_status.pack(pady=(12, 0))

        self.connect_button = ttk.Button(card, text="Create room", command=lambda: self._start_connect(self.connect_mode.get()))
        self.connect_button.pack(pady=(12, 0), ipadx=12, ipady=4)

        self._update_connect_mode()

    def _update_connect_mode(self):
        mode = self.connect_mode.get()
        label = getattr(self, "label_room_code", None)
        entry = getattr(self, "entry_room_code", None)
        if mode == "create":
            if label is not None:
                label.grid_remove()
            if entry is not None:
                entry.grid_remove()
            self.room_code_hint.config(text="The server generates the room code for you.")
            self.connect_button.config(text="Create room")
        else:
            if label is not None:
                label.grid(row=self.room_code_row, column=0, sticky="w", pady=4, padx=(0, 8))
            if entry is not None:
                entry.grid(row=self.room_code_row, column=1, pady=4)
            self.room_code_hint.config(text="Enter the 6-character code from the host.")
            self.connect_button.config(text="Join room")

    def _add_field(self, parent, label, key, default, row, show=None):
        lbl = ttk.Label(parent, text=label)
        lbl.grid(row=row, column=0, sticky="w", pady=4, padx=(0, 8))
        entry = ttk.Entry(parent, width=28, show=show) if show else ttk.Entry(parent, width=28)
        entry.insert(0, default)
        entry.grid(row=row, column=1, pady=4)
        setattr(self, f"entry_{key}", entry)
        setattr(self, f"label_{key}", lbl)
        entry.bind("<Return>", lambda _e: self._start_connect(self.connect_mode.get()))
        if key == "room_code":
            entry.bind("<KeyRelease>", lambda _e: self._normalize_room_code())
            entry.bind("<<Paste>>", lambda _e: self.root.after(10, self._normalize_room_code))

    def _normalize_room_code(self) -> None:
        entry = getattr(self, "entry_room_code", None)
        if entry is None:
            return
        value = entry.get().strip().upper()
        if value != entry.get():
            entry.delete(0, "end")
            entry.insert(0, value)

    def _start_connect(self, mode: str) -> None:
        host = self.entry_host.get().strip()
        port_text = self.entry_port.get().strip()
        username = self.entry_username.get().strip()
        room_code = self.entry_room_code.get().strip().upper()
        password = self.entry_password.get()

        if not host:
            self.connect_status.config(text="Server IP is required.")
            return
        try:
            port = int(port_text)
        except ValueError:
            self.connect_status.config(text="Port must be a number.")
            return
        if not username:
            self.connect_status.config(text="Username is required.")
            return
        if mode == "join" and not room_code:
            self.connect_status.config(text="Room code is required to join.")
            return

        self._connect_params = {
            "mode": mode, "host": host, "port": port,
            "username": username, "room_code": room_code, "password": password,
        }
        self.connect_status.config(text="Connecting...")
        self.root.update_idletasks()

        threading.Thread(target=self._connect_worker, args=(mode, host, port, username, room_code, password), daemon=True).start()

    def _connect_worker(self, mode, host, port, username, room_code, password):
        try:
            net = NetworkClient(host, port)
            net.connect()
            if mode == "create":
                net.create_room(username, password if password else None)
            else:
                net.join_room(username, room_code, password if password else None)
            resp = net.wait_for({"room_created", "join_ok", "join_denied", "_disconnected", "error"}, timeout=6.0)
            if resp is None:
                net.disconnect()
                self.root.after(0, self._connect_failed, "No response from server.")
                return
            self.root.after(0, self._handle_connect_result, mode, net, username, resp)
        except Exception as exc:
            self.root.after(0, self._connect_failed, str(exc))

    def _connect_failed(self, message: str) -> None:
        self.connect_status.config(text=f"Connection failed: {message}")

    def _handle_connect_result(self, mode, net, username, resp) -> None:
        mtype = resp.get("type")
        if mtype in ("join_denied", "error", "_disconnected"):
            net.disconnect()
            reason = resp.get("message") or resp.get("reason") or "denied"
            self.connect_status.config(text=f"Join denied: {reason}")
            return

        self.net = net
        self.username = username
        self.token = resp.get("token", self.token)
        self.theme_name = "light"
        self.theme = LIGHT

        if mtype == "room_created":
            self.room_code = resp["room_code"]
            self.role = resp.get("role", "host")
            self.token = resp.get("token", self.token)
            # Host color: look it up once participants arrive; default accent.
            self.user_color = "#1E5B6B"
        elif mtype == "join_ok":
            self.room_code = resp.get("room_code", self.entry_room_code.get().strip().upper())
            # room code may not be in join_ok; keep from entry
            if not self.room_code:
                self.room_code = self.entry_room_code.get().strip().upper()
            self.role = resp.get("role", "editor")
            self.user_color = resp.get("user_color", self.user_color)
            self.token = resp.get("token", self.token)
            if not self.user_color and isinstance(resp.get("participants"), list):
                for u in resp["participants"]:
                    if u.get("name") == self.username:
                        self.user_color = u.get("color", self.user_color)
            self.state.apply_snapshot(resp.get("snapshot", {}))
            if isinstance(resp.get("chat_history"), list):
                self.state.apply_chat_history(resp["chat_history"])
            if isinstance(resp.get("participants"), list):
                self.state.update_participants(resp["participants"])
            # Track pending none.

        self.root.title(f"LAN Whiteboard - {self.username} ({self.role}) - Room {self.room_code}")

        # Remove connect screen.
        self.connect_frame.destroy()
        self._build_main()
        self._apply_theme()

        # If join_ok, redraw snapshot.
        if mtype == "join_ok":
            self._redraw_all()

        self.root.after(10, self._poll)

    # ------------------------------------------------------------------ #
    # Reconnection
    # ------------------------------------------------------------------ #
    def _begin_reconnect(self) -> None:
        self._reconnecting = True
        self._reconnect_backoff = 1.0
        # End any incomplete stroke cleanly.
        for sid, info in list(self.local_strokes.items()):
            if info.get("batch"):
                op_id = str(uuid.uuid4())
                msg = {"type": "stroke_points", "op_id": op_id, "stroke_id": sid,
                       "points": [list(p) for p in info["batch"]]}
                self.state.track_pending(op_id)
                self._pending_ops[op_id] = msg
                info["batch"] = []
            if sid in self.state.objects:
                self.state.objects[sid]["ended"] = True
            op_id = str(uuid.uuid4())
            msg = {"type": "stroke_end", "op_id": op_id, "stroke_id": sid}
            self.state.track_pending(op_id)
            self._pending_ops[op_id] = msg
        self.local_strokes.clear()
        if self.reconnect_banner is not None:
            self.reconnect_banner.config(text="Reconnecting...")
            self.reconnect_banner.pack(side="top", fill="x")
        self._schedule_reconnect()

    def _schedule_reconnect(self) -> None:
        if self._reconnect_after_id is not None:
            try:
                self.root.after_cancel(self._reconnect_after_id)
            except Exception:
                pass
        delay_ms = int(self._reconnect_backoff * 1000)
        self._reconnect_after_id = self.root.after(delay_ms, self._reconnect_step)

    def _reconnect_step(self) -> None:
        self._reconnect_after_id = None
        if not self._reconnecting or self._connect_params is None:
            return
        params = self._connect_params
        threading.Thread(target=self._reconnect_worker, args=(params,), daemon=True).start()

    def _reconnect_worker(self, params: dict[str, Any]) -> None:
        try:
            net = NetworkClient(params["host"], params["port"])
            net.connect()
            last_seq = self.state.applied_seq
            net.send({"type": "reconnect", "token": self.token,
                      "room_code": self.room_code, "last_seq": last_seq})
            resp = net.wait_for({"reconnect_ok", "reconnect_denied", "_disconnected", "error"}, timeout=6.0)
            if resp is None:
                net.disconnect()
                self.root.after(0, self._reconnect_failed, "No response from server.")
                return
            self.root.after(0, self._handle_reconnect_result, net, resp)
        except Exception as exc:
            self.root.after(0, self._reconnect_failed, str(exc))

    def _reconnect_failed(self, message: str) -> None:
        if not self._reconnecting:
            return
        self._reconnect_backoff = min(self._reconnect_backoff * 2, 10.0)
        self._schedule_reconnect()

    def _handle_reconnect_result(self, net: NetworkClient, resp: dict[str, Any]) -> None:
        mtype = resp.get("type")
        if mtype in ("reconnect_denied", "error", "_disconnected"):
            net.disconnect()
            reason = resp.get("message") or resp.get("reason") or "denied"
            self._return_to_connect(f"Reconnection denied: {reason}")
            return
        if mtype != "reconnect_ok":
            net.disconnect()
            self._reconnect_failed("Unexpected response.")
            return

        self.net = net
        # Apply state.
        if isinstance(resp.get("snapshot"), dict):
            self.state.apply_snapshot(resp["snapshot"])
            if isinstance(resp.get("chat_history"), list):
                self.state.apply_chat_history(resp["chat_history"])
        elif isinstance(resp.get("ops_since"), dict):
            osi = resp["ops_since"]
            ops = osi.get("ops", [])
            if isinstance(ops, list):
                self.state.apply_ops_since(int(osi.get("from_seq", self.state.applied_seq)), ops)
        if isinstance(resp.get("seq"), int) and not isinstance(resp.get("seq"), bool):
            if resp["seq"] > self.state.applied_seq:
                self.state.applied_seq = resp["seq"]
        if isinstance(resp.get("participants"), list):
            self.state.update_participants(resp["participants"])
            for u in resp["participants"]:
                if u.get("name") == self.username and u.get("color"):
                    self.user_color = u.get("color")
        # Resend pending ops.
        for op_id, msg in list(self._pending_ops.items()):
            self.net.send(msg)
        # Redraw.
        self._redraw_all()
        if self.reconnect_banner is not None:
            self.reconnect_banner.pack_forget()
        self._reconnecting = False
        self._reconnect_backoff = 1.0
        self._sync_state = "synced"
        self._resync_pending = False
        self.state.behind_since = None
        self.root.after(10, self._poll)

    def _return_to_connect(self, message: str) -> None:
        self._reconnecting = False
        if self.net is not None:
            try:
                self.net.disconnect()
            except Exception:
                pass
            self.net = None
        if hasattr(self, "main_frame"):
            try:
                self.main_frame.destroy()
            except Exception:
                pass
        self.state = BoardState()
        self.local_strokes.clear()
        self._pending_ops.clear()
        self.token = ""
        self._show_connect()
        self.connect_status.config(text=message)

    # ------------------------------------------------------------------ #
    # Main window
    # ------------------------------------------------------------------ #
    def _build_main(self) -> None:
        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill="both", expand=True)

        self.reconnect_banner = ttk.Label(self.main_frame, text="", anchor="center",
                                         background="#E8B923", foreground="#102022",
                                         font=("Segoe UI", 11, "bold"))
        self.reconnect_banner.pack(side="top", fill="x")
        self.reconnect_banner.pack_forget()

        # Top toolbar (two rows). No scrolling; fixed layout.
        self.toolbar = ttk.Frame(self.main_frame, padding=(8, 4))
        self.toolbar.pack(side="top", fill="x")
        self.toolbar_row1 = ttk.Frame(self.toolbar)
        self.toolbar_row1.pack(fill="x", pady=2)
        self.toolbar_row2 = ttk.Frame(self.toolbar)
        self.toolbar_row2.pack(fill="x", pady=2)
        row1 = self.toolbar_row1
        row2 = self.toolbar_row2

        self.tool_buttons = {}
        self.tool_tooltips = {}
        button_pad = (8, 7)

        def action_button(parent, text, command, tip, width=None):
            """Create one fixed-height toolbar button sized from its content."""
            b = ttk.Button(parent, text=text, command=command,
                           style="Tool.TButton", padding=button_pad)
            if width is None:
                width = max(5, len(text) + 2)
            b.configure(width=width)
            b.pack(side="left", padx=2)
            Tooltip(b, tip)
            return b

        def tool_button(label, tool):
            b = action_button(row1, label, lambda: self._set_tool(tool),
                              f"Select {label.lower()} tool")
            self.tool_buttons[tool] = b
            self.tool_tooltips[tool] = b._tooltip
            return b

        # Row 1: tools, shapes, style.
        tool_button("Pen", "pen")
        tool_button("Eraser", "eraser")
        tool_button("Text", "text")
        self.select_btn = action_button(row1, "Select", lambda: None, "Coming soon", width=7)
        self.select_btn.state(["disabled"])
        self.sticky_btn = action_button(row1, "Sticky", lambda: None, "Coming soon", width=7)
        self.sticky_btn.state(["disabled"])

        self._last_shape = "rectangle"
        self.shapes_btn = action_button(row1, "Shapes", self._activate_last_shape,
                                        "Use the last shape", width=9)
        self.shapes_btn.pack_configure(padx=(2, 0))
        self.shapes_arrow_btn = ttk.Button(row1, text="\u25BE",
                                           command=self._open_shapes_palette,
                                           style="Tool.TButton", padding=(4, 7),
                                           width=2)
        self.shapes_arrow_btn.pack(side="left", padx=(0, 2))
        Tooltip(self.shapes_arrow_btn, "Open shapes palette")
        self.tool_buttons["shape"] = self.shapes_btn
        self.tool_tooltips["shape"] = self.shapes_btn._tooltip
        self._update_shapes_button_icon()

        ttk.Separator(row1, orient="vertical").pack(side="left", fill="y", padx=6)

        self.color_btn = action_button(row1, "Color", self._pick_color, "Pick color")
        self.color_swatch = tk.Label(row1, text="", bg=self.color, width=3,
                                     relief="solid", borderwidth=1, cursor="hand2")
        self.color_swatch.pack(side="left", padx=2)
        self.color_swatch.bind("<Button-1>", lambda _e: self._pick_color())
        Tooltip(self.color_swatch, "Color swatch")

        ttk.Label(row1, text="Width").pack(side="left", padx=(6, 2))
        self.width_scale = ttk.Scale(row1, from_=1, to=20, orient="horizontal",
                                     command=self._on_width, length=120)
        self.width_scale.set(self.width)
        self.width_scale.pack(side="left", padx=2)
        self.width_value = ttk.Label(row1, text=f"{int(self.width)}", width=3)
        self.width_value.pack(side="left", padx=2)
        self.width_preview = tk.Canvas(row1, width=40, height=24, highlightthickness=0)
        self.width_preview.pack(side="left", padx=2)
        self._update_width_preview()

        self.fill_var = tk.BooleanVar(value=False)
        self.fill_btn = action_button(row1, "Fill", self._toggle_fill,
                                      "Fill closed shapes", width=6)
        self.fill_btn.pack_configure(padx=(8, 2))

        ttk.Label(row1, text="Text size").pack(side="left", padx=(6, 2))
        self.text_size = ttk.Combobox(row1, values=["10", "12", "16", "20", "24", "32"], width=5)
        self.text_size.set("16")
        self.text_size.pack(side="left", padx=2)

        # Row 2: edit and file.
        self.undo_btn = action_button(row2, "Undo", self._undo, "Undo your last action")
        self.redo_btn = action_button(row2, "Redo", self._redo, "Redo your last action")

        ttk.Separator(row2, orient="vertical").pack(side="left", fill="y", padx=6)

        self.clear_btn = action_button(row2, "Clear", self._clear_canvas, "Host only")
        self.save_btn = action_button(row2, "Save", self._save_board, "Host only")
        self.load_btn = action_button(row2, "Load", self._load_board, "Host only")
        self.export_btn = action_button(row2, "Export", lambda: None, "Coming soon", width=7)
        self.export_btn.state(["disabled"])
        self.history_btn = action_button(row2, "History", lambda: None, "Coming soon", width=8)
        self.history_btn.state(["disabled"])

        # Reserved room for Export and History; those buttons arrive later.
        self.file_reserved = ttk.Frame(row2, width=170, height=34)
        self.file_reserved.pack(side="left", padx=(10, 2))
        self.file_reserved.pack_propagate(False)

        if self.role != "host":
            self.clear_btn.state(["disabled"])
            self.save_btn.state(["disabled"])
            self.load_btn.state(["disabled"])

        # Center canvas.  The pack padding is outside the widget, so event
        # coordinates never include it; only the widget border and highlight
        # ring are part of the coordinate transform.  Both are explicitly
        # zero here, which makes the mapping stable across Tk platforms.
        canvas_frame = ttk.Frame(self.main_frame)
        canvas_frame.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg=self.theme["canvas"],
                                borderwidth=0, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=12)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)

        # Right panel.  A fixed width is what stops chat/participant content
        # from resizing the whole rail while someone is drawing.
        self.right = ttk.Frame(self.main_frame, width=320, padding=8)
        self.right.pack(side="right", fill="y")
        self.right.pack_propagate(False)

        # Identity chip.
        self.identity_frame = ttk.Frame(self.right, padding=6, relief="solid", borderwidth=1)
        self.identity_frame.pack(fill="x", pady=(0, 8))
        identity_top = ttk.Frame(self.identity_frame)
        identity_top.pack(fill="x")
        self.identity_dot = tk.Canvas(identity_top, width=14, height=14, highlightthickness=0, bg=self.theme["bg"])
        self.identity_dot.pack(side="left", padx=(0, 6))
        self.identity_dot.create_oval(2, 2, 12, 12, fill=self.user_color, outline="", tags=("dot",))
        self.identity_name = ttk.Label(identity_top, text=self.username, font=("Segoe UI", 11, "bold"))
        self.identity_name.pack(side="left")
        self.identity_role = ttk.Label(identity_top, text=self.role, font=("Segoe UI", 9))
        self.identity_role.pack(side="left", padx=6)
        identity_toggles = ttk.Frame(self.identity_frame)
        identity_toggles.pack(fill="x", pady=(4, 0))
        self.show_cursors_var = tk.BooleanVar(value=True)
        self.show_cursors_btn = ttk.Checkbutton(identity_toggles, text="Show cursors",
                                                variable=self.show_cursors_var,
                                                command=self._toggle_cursors)
        self.show_cursors_btn.pack(side="left")
        Tooltip(self.show_cursors_btn, "Show or hide remote cursors")
        self.theme_btn = ttk.Button(identity_toggles, text="Theme", command=self._toggle_theme)
        self.theme_btn.pack(side="right")
        Tooltip(self.theme_btn, "Switch light and dark theme")

        room_header = ttk.Frame(self.right)
        room_header.pack(fill="x", pady=(0, 8))
        ttk.Label(room_header, text="Room code", font=("Segoe UI", 9)).pack(anchor="w")
        code_row = ttk.Frame(room_header)
        code_row.pack(fill="x")
        self.room_code_label = ttk.Label(code_row, text=self.room_code, font=("Segoe UI", 16, "bold"))
        self.room_code_label.pack(side="left")
        ttk.Button(code_row, text="Copy", command=self._copy_room_code, width=6).pack(side="right")

        ttk.Label(self.right, text="Participants", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.participants_frame = ttk.Frame(self.right)
        self.participants_frame.pack(fill="x", pady=(4, 8))

        if self.role == "host":
            ttk.Separator(self.right, orient="horizontal").pack(fill="x", pady=8)
            ttk.Label(self.right, text="Host controls", font=("Segoe UI", 11, "bold")).pack(anchor="w")
            hc = ttk.Frame(self.right)
            hc.pack(fill="x", pady=4)
            ttk.Label(hc, text="Target").grid(row=0, column=0, sticky="w")
            self.host_target = ttk.Combobox(hc, values=[], width=12, state="readonly")
            self.host_target.grid(row=0, column=1, padx=4)
            self.host_target.bind("<<ComboboxSelected>>", lambda _e: self._update_host_buttons())
            ttk.Label(hc, text="Role").grid(row=1, column=0, sticky="w")
            self.host_role = ttk.Combobox(hc, values=["editor", "viewer"], width=12, state="readonly")
            self.host_role.set("editor")
            self.host_role.grid(row=1, column=1, padx=4)
            self.apply_role_btn = ttk.Button(hc, text="Apply role", command=self._apply_role)
            self.apply_role_btn.grid(row=2, column=0, columnspan=2, pady=4, sticky="ew")
            Tooltip(self.apply_role_btn, "Host only")
            self.kick_btn = ttk.Button(hc, text="Kick", command=self._kick_user)
            self.kick_btn.grid(row=3, column=0, columnspan=2, pady=4, sticky="ew")
            Tooltip(self.kick_btn, "Host only")
            self.apply_role_btn.state(["disabled"])
            self.kick_btn.state(["disabled"])
            self.lock_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(hc, text="Lock room", variable=self.lock_var, command=self._toggle_lock).grid(row=4, column=0, columnspan=2, sticky="w")

        # Chat takes the remaining vertical space; the input is reserved at
        # the bottom first so it stays visible at the minimum window height.
        ttk.Label(self.right, text="Chat", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        chat_input = ttk.Frame(self.right)
        chat_input.pack(side="bottom", fill="x")
        self.chat_entry = ttk.Entry(chat_input)
        self.chat_entry.pack(side="left", fill="x", expand=True)
        self.chat_entry.bind("<Return>", lambda _e: self._send_chat())
        ttk.Button(chat_input, text="Send", command=self._send_chat).pack(side="left", padx=4)
        self.chat_text = tk.Text(self.right, height=10, state="disabled", wrap="word",
                                 font=("Segoe UI", 10), padx=6, pady=6, borderwidth=0,
                                 highlightthickness=1,
                                 highlightbackground=self.theme["border"])
        self.chat_text.pack(fill="both", expand=True, pady=4)

        # Status bar.  Fixed character widths keep changing values (latency,
        # seq, activity) from shoving the neighbouring labels around.
        self.status_bar = ttk.Frame(self.root, padding=(8, 2))
        self.status_bar.pack(side="bottom", fill="x")
        self.status_conn = ttk.Label(self.status_bar, text="Connecting", width=36, anchor="w")
        self.status_conn.pack(side="left", padx=4)
        self.status_activity = ttk.Label(self.status_bar, text="", width=36, anchor="w")
        self.status_activity.pack(side="left", padx=12)
        self.status_latency = ttk.Label(self.status_bar, text="latency: -", width=16, anchor="e")
        self.status_latency.pack(side="right", padx=4)
        self.status_seq = ttk.Label(self.status_bar, text="seq: 0", width=10, anchor="e")
        self.status_seq.pack(side="right", padx=4)
        self.status_sync = ttk.Label(self.status_bar, text="synced", width=10, anchor="e")
        self.status_sync.pack(side="right", padx=4)

        # Keyboard shortcuts (Control and Command).
        for mod in ("<Control-", "<Command-"):
            self.root.bind(f"{mod}p>", lambda _e: self._set_tool("pen"))
            self.root.bind(f"{mod}e>", lambda _e: self._set_tool("eraser"))
            self.root.bind(f"{mod}t>", lambda _e: self._toggle_theme())
            self.root.bind(f"{mod}z>", lambda _e: self._undo())
            self.root.bind(f"{mod}y>", lambda _e: self._redo())
        self.root.bind("<Control-Shift-Z>", lambda _e: self._redo())
        self.root.bind("<Command-Shift-Z>", lambda _e: self._redo())
        self.root.bind("<Control-Shift-D>", lambda _e: self._toggle_debug_overlay())
        self.root.bind("<Escape>", lambda _e: self._set_tool("pen"))

        # Mouse wheel for width (cross-platform).
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._adjust_width(1))
        self.canvas.bind("<Button-5>", lambda e: self._adjust_width(-1))

        self._update_permissions()

    def _set_enabled(self, widget, enabled: bool) -> None:
        if widget is None:
            return
        if enabled:
            widget.state(["!disabled"])
        else:
            widget.state(["disabled"])

    def _update_permissions(self) -> None:
        """Enable only actions the current role is allowed to perform.

        Disabled controls stay visible so users can see the feature exists;
        their tooltips explain whether the limit is host-only or viewer-only.
        """
        is_viewer = self.role == "viewer"
        is_host = self.role == "host"
        reason = "Viewers cannot do this" if is_viewer else "Host only"
        active_key = self.tool if self.tool in ("pen", "eraser", "text") else "shape"
        for tool, btn in getattr(self, "tool_buttons", {}).items():
            self._set_enabled(btn, not is_viewer)
            if is_viewer:
                btn.configure(style="Tool.TButton")
            else:
                btn.configure(style="ToolActive.TButton" if tool == active_key else "Tool.TButton")
            tip = getattr(self, "tool_tooltips", {}).get(tool)
            if tip is not None:
                if is_viewer:
                    tip.set_text(reason)
                elif tool == "shape":
                    tip.set_text(f"Use last shape: {self._last_shape.replace('_', ' ')}")
                else:
                    tip.set_text(f"Select {tool.lower()} tool")
        arrow = getattr(self, "shapes_arrow_btn", None)
        if arrow is not None:
            self._set_enabled(arrow, not is_viewer)
            tip = getattr(arrow, "_tooltip", None)
            if tip is not None:
                tip.set_text(reason if is_viewer else "Open shapes palette")
        fill = getattr(self, "fill_btn", None)
        if fill is not None:
            self._set_enabled(fill, not is_viewer)
            selected = bool(self.fill_var.get()) and not is_viewer
            fill.configure(style="ToolActive.TButton" if selected else "Tool.TButton")
            tip = getattr(fill, "_tooltip", None)
            if tip is not None:
                tip.set_text(reason if is_viewer else "Fill closed shapes")
        for name in ("undo_btn", "redo_btn"):
            widget = getattr(self, name, None)
            self._set_enabled(widget, not is_viewer)
            tip = getattr(widget, "_tooltip", None)
            if tip is not None:
                if is_viewer:
                    tip.set_text(reason)
                else:
                    tip.set_text("Undo your last action" if name == "undo_btn"
                                 else "Redo your last action")
        for name in ("clear_btn", "save_btn", "load_btn"):
            widget = getattr(self, name, None)
            self._set_enabled(widget, is_host)
            tip = getattr(widget, "_tooltip", None)
            if tip is not None:
                tip.set_text(reason if is_viewer else "Host only")
        self._update_host_buttons()

    def _set_tool(self, tool):
        if getattr(self, "_text_entry", None) is not None:
            try:
                self._text_entry.destroy()
            except tk.TclError:
                pass
            self._text_entry = None
        self.tool = tool
        self._highlight_erase_target(None)
        active_key = tool if tool in ("pen", "eraser", "text") else "shape"
        for t, btn in self.tool_buttons.items():
            btn.configure(style="ToolActive.TButton" if t == active_key else "Tool.TButton")
        if hasattr(self, "status_conn"):
            self.status_conn.config(text=f"Tool: {tool.replace('_', ' ')}")

    def _activate_last_shape(self):
        self._select_shape(self._last_shape)

    def _toggle_fill(self):
        if self.role == "viewer":
            return
        self.fill_var.set(not self.fill_var.get())
        self.fill_btn.configure(style="ToolActive.TButton" if self.fill_var.get()
                                else "Tool.TButton")
        tip = getattr(self.fill_btn, "_tooltip", None)
        if tip is not None:
            tip.set_text("Fill closed shapes: on" if self.fill_var.get()
                         else "Fill closed shapes: off")

    def _shape_icon_photo(self, kind, color, size=16):
        """Rasterize one shape kind into a small PhotoImage icon."""
        try:
            import shapes_geometry as sg
            pts = sg.shape_points(kind, 2.0, 2.0, size - 3.0, size - 3.0)
        except Exception:
            pts = [(2, 2), (size - 3, 2), (size - 3, size - 3),
                   (2, size - 3), (2, 2)]
        image = tk.PhotoImage(master=self.root, width=size, height=size)
        for index in range(len(pts) - 1):
            x1, y1 = pts[index]
            x2, y2 = pts[index + 1]
            steps = max(1, int(round(max(abs(x2 - x1), abs(y2 - y1)))))
            for step in range(steps + 1):
                t = step / steps
                px = int(round(x1 + (x2 - x1) * t))
                py = int(round(y1 + (y2 - y1) * t))
                if 0 <= px < size and 0 <= py < size:
                    image.put(color, (px, py))
        return image

    def _update_shapes_button_icon(self):
        if not hasattr(self, "shapes_btn"):
            return
        self._shape_icon = self._shape_icon_photo(self._last_shape, self.theme["ink"], 16)
        self.shapes_btn.configure(image=self._shape_icon, compound="left", text="Shapes")
        tip = getattr(self.shapes_btn, "_tooltip", None)
        if tip is not None and self.role != "viewer":
            tip.set_text(f"Use last shape: {self._last_shape.replace('_', ' ')}")

    def _close_shapes_palette(self):
        palette = getattr(self, "_shapes_palette", None)
        self._shapes_palette = None
        if palette is not None:
            try:
                palette.destroy()
            except tk.TclError:
                pass

    def _open_shapes_palette(self):
        self._close_shapes_palette()
        palette = tk.Toplevel(self.root)
        palette.title("Shapes")
        palette.transient(self.root)
        palette.resizable(False, False)
        palette.configure(bg=self.theme["bg"])
        groups = [
            ("Basic", ["line", "arrow", "double_arrow", "rectangle",
                       "rounded_rectangle", "ellipse", "triangle", "right_triangle",
                       "diamond", "pentagon", "hexagon", "star", "plus", "heart"]),
            ("Flowchart", ["process", "decision", "terminator", "data", "document"]),
            ("Callout", ["speech_bubble"]),
        ]
        icons = []
        grid_row = 0
        for title, kinds in groups:
            ttk.Label(palette, text=title, font=("Segoe UI", 9, "bold")).grid(
                row=grid_row, column=0, columnspan=5, sticky="w", padx=8, pady=(8, 2))
            grid_row += 1
            for index, kind in enumerate(kinds):
                col = index % 5
                row = grid_row + index // 5
                icon = self._shape_icon_photo(kind, self.theme["ink"], 24)
                icons.append(icon)
                label = kind.replace("_", " ")
                btn = ttk.Button(palette, text=label, image=icon, compound="top",
                                 command=lambda k=kind: self._select_shape(k))
                btn.grid(row=row, column=col, padx=3, pady=3, sticky="ew")
            grid_row += (len(kinds) + 4) // 5
        palette._shape_icons = icons
        palette.bind("<Escape>", lambda _e: self._close_shapes_palette())
        palette.protocol("WM_DELETE_WINDOW", self._close_shapes_palette)
        palette.update_idletasks()
        px = self.shapes_arrow_btn.winfo_rootx()
        py = self.shapes_arrow_btn.winfo_rooty() + self.shapes_arrow_btn.winfo_height() + 2
        palette.geometry(f"+{px}+{py}")
        self._shapes_palette = palette

    def _select_shape(self, kind):
        self._last_shape = kind
        self._set_tool(kind)
        self._update_shapes_button_icon()
        self._close_shapes_palette()

    def _pick_color(self):
        res = colorchooser.askcolor(color=self.color, parent=self.root)
        if res and res[1]:
            self.color = res[1]
            self.color_swatch.config(bg=self.color)

    def _copy_room_code(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.room_code)
            self._toast("Room code copied.")
        except tk.TclError:
            self._toast("Could not copy.")

    def _on_width(self, _val):
        try:
            self.width = float(self.width_scale.get())
        except (TypeError, ValueError):
            pass
        if hasattr(self, "width_value"):
            self.width_value.config(text=f"{int(self.width)}")
        self._update_width_preview()

    def _update_width_preview(self):
        if not hasattr(self, "width_preview"):
            return
        c = self.width_preview
        c.delete("all")
        c.configure(bg=self.theme["bg"])
        y = 12
        c.create_oval(4, y - self.width / 2, 4 + self.width, y + self.width / 2,
                      fill=self.theme["accent"], outline=self.theme["ink"], width=1)

    def _adjust_width(self, delta):
        self.width = max(1.0, min(20.0, self.width + delta))
        self.width_scale.set(self.width)

    def _on_wheel(self, event):
        if event.delta > 0:
            self._adjust_width(1)
        elif event.delta < 0:
            self._adjust_width(-1)

    def _toggle_cursors(self):
        # Hide/show remote cursor markers.
        state = "normal" if self.show_cursors_var.get() else "hidden"
        for oval, text in self.cursor_items.values():
            for item in (oval, text):
                try:
                    self.canvas.itemconfigure(item, state=state)
                except tk.TclError:
                    pass

    def _toggle_theme(self):
        self.theme_name = "dark" if self.theme_name == "light" else "light"
        self.theme = DARK if self.theme_name == "dark" else LIGHT
        self._apply_theme()
        self._redraw_all()

    def _apply_theme(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        t = self.theme
        style.configure(".", background=t["bg"], foreground=t["ink"], font=("Segoe UI", 10))
        style.configure("TFrame", background=t["bg"])
        style.configure("TLabel", background=t["bg"], foreground=t["ink"])
        style.configure("TButton", background=t["accent"], foreground=t["on_accent"],
                        padding=6, relief="flat", borderwidth=1, bordercolor=t["border"])
        style.map("TButton",
                  background=[("pressed", t["accent_hover"]), ("active", t["accent_hover"]),
                              ("disabled", t["surface_alt"])],
                  foreground=[("disabled", t["ink_muted"])],
                  bordercolor=[("disabled", t["border"])])
        style.configure("Tool.TButton", background=t["surface"], foreground=t["ink"],
                        padding=4, relief="flat", borderwidth=1, bordercolor=t["border"])
        style.map("Tool.TButton",
                  background=[("pressed", t["surface_alt"]), ("active", t["surface_alt"]),
                              ("disabled", t["surface_alt"])],
                  foreground=[("disabled", t["ink_muted"])],
                  bordercolor=[("disabled", t["border"])])
        style.configure("ToolActive.TButton", background=t["accent"], foreground=t["on_accent"],
                        padding=4, relief="flat", borderwidth=1, bordercolor=t["accent"])
        style.map("ToolActive.TButton",
                  background=[("pressed", t["accent_hover"]), ("active", t["accent_hover"]),
                              ("disabled", t["surface_alt"])],
                  foreground=[("disabled", t["ink_muted"])],
                  bordercolor=[("disabled", t["border"])])
        style.configure("TEntry", fieldbackground=t["surface"], foreground=t["ink"],
                        insertcolor=t["ink"], bordercolor=t["border"])
        style.configure("TCombobox", fieldbackground=t["surface"], foreground=t["ink"],
                        background=t["surface"], arrowcolor=t["ink"], bordercolor=t["border"])
        style.map("TCombobox",
                  fieldbackground=[("readonly", t["surface"]), ("disabled", t["surface_alt"])],
                  foreground=[("readonly", t["ink"]), ("disabled", t["ink_muted"])],
                  background=[("readonly", t["surface"])],
                  arrowcolor=[("readonly", t["ink"]), ("disabled", t["ink_muted"])])
        style.configure("TCheckbutton", background=t["bg"], foreground=t["ink"])
        style.map("TCheckbutton", foreground=[("disabled", t["ink_muted"])])
        style.configure("TScale", background=t["bg"], troughcolor=t["surface_alt"])
        self.root.option_add("*TCombobox*Listbox.background", t["surface"])
        self.root.option_add("*TCombobox*Listbox.foreground", t["ink"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", t["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", t["on_accent"])
        self.root.configure(bg=t["bg"])
        if hasattr(self, "canvas"):
            self.canvas.configure(bg=t["canvas"])
            self._draw_board_background()
        if hasattr(self, "chat_text"):
            self.chat_text.configure(bg=t["surface"], fg=t["ink"], insertbackground=t["ink"],
                                     selectbackground=t["accent"], selectforeground=t["on_accent"],
                                     highlightbackground=t["border"], highlightcolor=t["border"])
            self._configure_chat_tags()
        if hasattr(self, "width_preview"):
            self.width_preview.configure(bg=t["bg"])
            self._update_width_preview()
        if hasattr(self, "identity_dot"):
            self.identity_dot.configure(bg=t["bg"])
        for info in getattr(self, "_participant_rows", {}).values():
            info["dot"].configure(bg=t["bg"])
            info["last_fg"] = None
        self._update_activity_indicators()
        if self.debug_label is not None:
            self.debug_label.configure(bg=t["surface"], fg=t["ink"])
        self._update_shapes_button_icon()
        self._update_permissions()

    def _configure_chat_tags(self):
        if not hasattr(self, "chat_text"):
            return
        t = self.theme
        self.chat_text.tag_configure("time_tag", foreground=t["ink_muted"], font=("Segoe UI", 9))
        self.chat_text.tag_configure("body_tag", foreground=t["ink"], font=("Segoe UI", 10))
        for tag in self.chat_text.tag_names():
            if tag.startswith("name_"):
                user = tag[5:]
                color = None
                for u in self.state.participants:
                    if u.get("name") == user:
                        color = u.get("color")
                        break
                self.chat_text.tag_configure(tag, foreground=color or t["accent"],
                                             font=("Segoe UI", 10, "bold"))

    # ------------------------------------------------------------------ #
    # Canvas geometry
    # ------------------------------------------------------------------ #
    def _canvas_inset(self) -> int:
        """Per-side pixels between the widget edge and the drawable area."""
        border = int(self.canvas.cget("borderwidth") or 0)
        highlight = int(self.canvas.cget("highlightthickness") or 0)
        return border + highlight

    def _canvas_size(self) -> tuple[int, int]:
        return self.canvas.winfo_width(), self.canvas.winfo_height()

    def _on_canvas_configure(self, event):
        self.scale, self.offset_x, self.offset_y = canvas_transform(
            event.width, event.height, self._canvas_inset())
        self._draw_board_background()
        self._redraw_all()
        if self.debug_overlay:
            self._raise_debug_overlay()

    def _to_logical(self, x, y):
        """Convert a raw widget pixel to clamped logical board coordinates."""
        # canvasx/canvasy handle any future scrolling.  They do not remove
        # the widget border, so the shared transform receives the same inset
        # the configure handler used.
        cx = self.canvas.canvasx(x)
        cy = self.canvas.canvasy(y)
        w, h = self._canvas_size()
        return window_to_logical(cx, cy, w, h, self._canvas_inset())

    def _to_window(self, lx, ly):
        """Convert logical board coordinates back to raw widget pixels."""
        w, h = self._canvas_size()
        return logical_to_window(lx, ly, w, h, self._canvas_inset())

    def _over_board(self, x, y) -> bool:
        cx = self.canvas.canvasx(x)
        cy = self.canvas.canvasy(y)
        w, h = self._canvas_size()
        return is_inside_board(cx, cy, w, h, self._canvas_inset())

    def _erase_threshold(self) -> float:
        """Ten screen pixels expressed in logical units."""
        _scale, _ox, _oy = canvas_transform(*self._canvas_size(), self._canvas_inset())
        return 10.0 / max(_scale, 0.001)

    def _stroke_width(self, logical_width: float) -> float:
        """Board units scaled to screen pixels, never thinner than one pixel."""
        return max(1.0, float(logical_width) * max(self.scale, 0.001))

    def _flatten_points(self, points) -> list[float]:
        """Map logical points to one flat canvas pixel coordinate list."""
        w, h = self._canvas_size()
        inset = self._canvas_inset()
        flat: list[float] = []
        for pt in points:
            if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                continue
            cx, cy = logical_to_window(float(pt[0]), float(pt[1]), w, h, inset)
            flat.extend((cx, cy))
        return flat

    def _toggle_debug_overlay(self):
        self.debug_overlay = not self.debug_overlay
        if self.debug_overlay:
            if self.debug_label is None:
                self.debug_label = tk.Label(self.canvas, text="", bg=self.theme["surface"],
                                            fg=self.theme["ink"], font=("Courier", 9),
                                            padx=4, pady=2, justify="left")
            self._raise_debug_overlay()
        else:
            if self.debug_label is not None:
                self.debug_label.place_forget()

    def _raise_debug_overlay(self):
        if self.debug_label is not None and self.debug_overlay:
            self.debug_label.place(x=4, y=4)
            self.debug_label.lift()

    def _update_debug_overlay(self, event_x, event_y, lx, ly):
        if not self.debug_overlay or self.debug_label is None:
            return
        w, h = self._canvas_size()
        x0, y0, x1, y1 = board_rect(w, h, self._canvas_inset())
        cx = self.canvas.canvasx(event_x)
        cy = self.canvas.canvasy(event_y)
        text = (f"raw=({event_x},{event_y}) canvas=({cx:.1f},{cy:.1f})\n"
                f"logical=({lx:.1f},{ly:.1f}) board_px=({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f})\n"
                f"scale={self.scale:.4f} offset=({self.offset_x:.1f},{self.offset_y:.1f})"
                f" inset={self._canvas_inset()}")
        self.debug_label.config(text=text)
        self._raise_debug_overlay()

    def _highlight_erase_target(self, sid):
        # Restore the previous highlight.  The id may refer to a stroke that
        # was just deleted, so every lookup is defensive.
        prev = getattr(self, "_hover_erase", None)
        if prev is not None and prev in self.canvas_items:
            obj = self.state.objects.get(prev)
            if obj is not None:
                try:
                    self.canvas.itemconfig(self.canvas_items[prev],
                                           width=self._stroke_width(obj.get("width", 2)),
                                           fill=obj.get("color", "#000000"))
                except tk.TclError:
                    pass
        self._hover_erase = sid
        if sid is not None and sid in self.canvas_items:
            item = self.canvas_items[sid]
            obj = self.state.objects.get(sid, {})
            try:
                self.canvas.itemconfig(item,
                                       width=self._stroke_width(obj.get("width", 2)) + 4,
                                       fill=self.theme["board_accent"])
            except tk.TclError:
                self._hover_erase = None

    # ------------------------------------------------------------------ #
    # Drawing input
    # ------------------------------------------------------------------ #
    def _on_press(self, event):
        if self._reconnecting:
            self._toast("Reconnecting...")
            return
        # Ignore presses in the letterbox/surround; only the paper is
        # drawable.  Using the shared transform keeps this consistent with
        # the pen and eraser hit-tests.
        if not self._over_board(event.x, event.y):
            return
        lx, ly = self._to_logical(event.x, event.y)
        self._update_debug_overlay(event.x, event.y, lx, ly)
        self._last_erased = None
        if self.role == "viewer":
            self._toast("Viewers cannot draw.")
            return
        if self.tool == "pen":
            sid = str(uuid.uuid4())
            op_id = str(uuid.uuid4())
            cx, cy = self._to_window(lx, ly)
            item = self.canvas.create_line(cx, cy, cx, cy, fill=self.color,
                                           width=self._stroke_width(self.width),
                                           capstyle="round", smooth=True, tags=(sid,))
            self.canvas_items[sid] = item
            self._raise_matte()
            # The press point is the first local point and the first point
            # of the first stroke_points batch.  The visible line therefore
            # starts exactly under the cursor instead of at the first motion
            # event.
            self.local_strokes[sid] = {
                "points": [[lx, ly]],
                "last_send": time.time(),
                "batch": [[lx, ly]],
            }
            # Mirror the local stroke immediately; the eraser can then hit
            # your own line before the server echo arrives.
            self.state.objects[sid] = {
                "id": sid,
                "type": "stroke",
                "tool": "pen",
                "color": self.color,
                "width": self.width,
                "points": [[lx, ly]],
                "user": self.username,
                "seq": 0,
            }
            self.state.track_pending(op_id)
            msg = {"type": "stroke_start", "op_id": op_id, "stroke_id": sid,
                   "tool": "pen", "color": self.color, "width": self.width,
                   "x": lx, "y": ly}
            self._pending_ops[op_id] = msg
            self.state.start_drawing(self.username)
            if self.net:
                self.net.send(msg)
        elif self.tool == "eraser":
            self._erase_at(lx, ly)
        elif self.tool not in ("pen", "eraser", "text"):
            self._shape_start = (lx, ly)
            self._shape_kind = self.tool
            self._shape_preview = None
            self._update_shape_preview(lx, ly, lx, ly, event.state)
        elif self.tool == "text":
            self._place_text_entry(event.x, event.y, lx, ly)

    def _on_motion(self, event):
        lx, ly = self._to_logical(event.x, event.y)
        self._update_debug_overlay(event.x, event.y, lx, ly)
        if self.tool == "pen":
            # Find the active stroke (last one being drawn).
            if not self.local_strokes:
                return
            sid = list(self.local_strokes.keys())[-1]
            info = self.local_strokes[sid]
            if info["points"] and [lx, ly] == info["points"][-1]:
                return
            info["points"].append([lx, ly])
            self.state.objects.setdefault(sid, {"id": sid, "type": "stroke", "points": []})["points"].append([lx, ly])
            # Extend the polyline.  The model stores logical units; the
            # canvas stores pixels, so every update goes through the shared
            # transform.
            item = self.canvas_items.get(sid)
            if item is not None:
                flat = self._flatten_points(info["points"])
                if len(flat) >= 4:
                    self.canvas.coords(item, *flat)
                    self._raise_matte()
            # Batch points.  The batch already starts with the press point.
            info["batch"].append([lx, ly])
            now = time.time()
            if now - info["last_send"] >= 0.04 and info["batch"]:
                self._send_stroke_points(sid, info["batch"])
                info["batch"] = []
                info["last_send"] = now
                self.state.note_drawing(self.username, now)
        elif self.tool == "eraser":
            # Hover highlight and sweep-to-erase.
            self._erase_at(lx, ly)
        elif self.tool not in ("pen", "eraser", "text"):
            if self._shape_start is not None:
                x0, y0 = self._shape_start
                shift = bool(event.state & 0x0001)
                x2, y2 = (lx, ly)
                if shift:
                    x2, y2 = shift_constraint(self._shape_kind, x0, y0, lx, ly)
                self._update_shape_preview(x0, y0, x2, y2, event.state)
        else:
            # Throttled cursor move.
            now = time.time()
            if self.net and now - self._last_cursor_send >= 0.05:
                self.net.send({"type": "cursor_move", "x": lx, "y": ly})
                self._last_cursor_send = now

    def _send_stroke_points(self, sid: str, points: list[list[float]]) -> None:
        if not points or not self.net:
            return
        # Clamp defensively one more time: no point may ever leave the board.
        safe = [[max(0.0, min(float(LOGICAL_WIDTH), float(p[0]))),
                 max(0.0, min(float(LOGICAL_HEIGHT), float(p[1])))] for p in points]
        op_id = str(uuid.uuid4())
        self.state.track_pending(op_id)
        msg = {"type": "stroke_points", "op_id": op_id,
               "stroke_id": sid, "points": safe}
        self._pending_ops[op_id] = msg
        self.net.send(msg)

    def _erase_at(self, lx: float, ly: float) -> None:
        sid = find_stroke_at(self.state.objects, lx, ly, threshold=self._erase_threshold())
        self._highlight_erase_target(sid)
        if not sid:
            return
        if self.role == "viewer":
            self._toast("Viewers cannot erase strokes.")
            return
        if not self.net or sid == getattr(self, "_last_erased", None):
            return
        op_id = str(uuid.uuid4())
        self._last_delete = {"op_id": op_id, "target_id": sid}
        msg = {"type": "delete_object", "op_id": op_id, "target_id": sid}
        self._pending_ops[op_id] = msg
        self.state.track_pending(op_id)
        self.net.send(msg)
        logging.getLogger("whiteboard.ui").debug("sent delete_object op=%s target=%s", op_id, sid)
        self._last_erased = sid

    def _on_release(self, event):
        if self.tool not in ("pen", "eraser", "text"):
            if self._shape_start is None:
                return
            x0, y0 = self._shape_start
            lx, ly = self._to_logical(event.x, event.y)
            shift = bool(event.state & 0x0001)
            if shift:
                lx, ly = shift_constraint(self._shape_kind, x0, y0, lx, ly)
            # clamp
            lx = max(0.0, min(float(LOGICAL_WIDTH), lx))
            ly = max(0.0, min(float(LOGICAL_HEIGHT), ly))
            x0 = max(0.0, min(float(LOGICAL_WIDTH), x0))
            y0 = max(0.0, min(float(LOGICAL_HEIGHT), y0))
            self._clear_shape_preview()
            if abs(lx - x0) <= 1.0 and abs(ly - y0) <= 1.0:
                # A click without a drag creates no shape.
                self._shape_start = None
                self._shape_preview = None
                return
            kind = self._shape_kind
            sid = str(uuid.uuid4())
            op_id = str(uuid.uuid4())
            closed = kind not in ("line", "arrow", "double_arrow")
            fill = self.color if (self.fill_var.get() and closed) else None
            msg = {"type": "shape", "op_id": op_id, "shape_id": sid, "kind": kind,
                   "p1": [x0, y0], "p2": [lx, ly], "color": self.color,
                   "width": self.width, "fill": fill}
            # optimistic
            obj = {"id": sid, "type": "shape", "kind": kind, "p1": [x0, y0],
                   "p2": [lx, ly], "color": self.color, "width": self.width,
                   "fill": fill, "user": self.username, "seq": 0}
            self.state.objects[sid] = obj
            self._add_shape_item(obj)
            self.state.track_pending(op_id)
            self._pending_ops[op_id] = msg
            self.state.start_drawing(self.username)
            self.state.end_drawing(self.username)
            if self.net:
                self.net.send(msg)
            self._shape_start = None
            self._shape_preview = None
            return
        if self.tool != "pen" or not self.local_strokes:
            return
        lx, ly = self._to_logical(event.x, event.y)
        sid = list(self.local_strokes.keys())[-1]
        info = self.local_strokes.get(sid)
        if info is None:
            return
        # Add the release point when it adds information; never duplicate
        # the previous sample.
        if info["points"] and [lx, ly] != info["points"][-1]:
            info["points"].append([lx, ly])
            self.state.objects.setdefault(sid, {"id": sid, "type": "stroke", "points": []})["points"].append([lx, ly])
            item = self.canvas_items.get(sid)
            if item is not None:
                flat = self._flatten_points(info["points"])
                if len(flat) >= 4:
                    self.canvas.coords(item, *flat)
                    self._raise_matte()
            info["batch"].append([lx, ly])
        # Flush remaining batch (it starts with the press point).
        if info["batch"]:
            self._send_stroke_points(sid, info["batch"])
        self.local_strokes.pop(sid, None)
        if self.net:
            op_id = str(uuid.uuid4())
            self.state.track_pending(op_id)
            msg = {"type": "stroke_end", "op_id": op_id, "stroke_id": sid}
            self._pending_ops[op_id] = msg
            self.net.send(msg)
        self.state.end_drawing(self.username)

    def _on_hover(self, event):
        # The hover highlight is part of the eraser's affordance; pen hover
        # does not need a canvas update.
        if self.tool != "eraser" or not self._over_board(event.x, event.y):
            if self.tool == "eraser":
                self._highlight_erase_target(None)
            return
        lx, ly = self._to_logical(event.x, event.y)
        self._update_debug_overlay(event.x, event.y, lx, ly)
        self._highlight_erase_target(find_stroke_at(self.state.objects, lx, ly,
                                                    threshold=self._erase_threshold()))

    # ------------------------------------------------------------------ #
    # Polling and message handling
    # ------------------------------------------------------------------ #
    def _poll(self) -> None:
        if self.net is None:
            if self._reconnecting:
                self.root.after(100, self._poll)
            return
        try:
            try:
                while True:
                    msg = self.net.inbox.get_nowait()
                    try:
                        self._handle_message(msg)
                    except Exception as exc:
                        import logging
                        logging.getLogger("whiteboard.ui").exception("error handling message: %s", exc)
                        self._toast(f"Message error: {exc}")
            except queue.Empty:
                pass

            # Prune stale drawers and blink.
            before = set(self.state.active_drawers)
            self.state.prune_drawers()
            changed = before != set(self.state.active_drawers)
            self._blink = not self._blink if self.state.active_drawers else False
            if changed or self.state.active_drawers:
                self._rebuild_participants()

            # Hide cursors idle for 5 seconds.
            now = time.time()
            for user in list(self.cursor_items.keys()):
                if now - self._cursor_last.get(user, now) > 5.0:
                    oval, text = self.cursor_items.pop(user)
                    try:
                        self.canvas.delete(oval)
                        self.canvas.delete(text)
                    except tk.TclError:
                        pass
                    self._cursor_last.pop(user, None)
            if not getattr(self, "show_cursors_var", None) or not self.show_cursors_var.get():
                for oval, text in self.cursor_items.values():
                    try:
                        self.canvas.itemconfigure(oval, state="hidden")
                        self.canvas.itemconfigure(text, state="hidden")
                    except tk.TclError:
                        pass
            else:
                for oval, text in self.cursor_items.values():
                    try:
                        self.canvas.itemconfigure(oval, state="normal")
                        self.canvas.itemconfigure(text, state="normal")
                    except tk.TclError:
                        pass

            # Latency ping every 5 seconds.
            now = time.time()
            if now - self._last_ping_time >= 5.0:
                self._last_ping_time = now
                self._ping_ts = now
                if self.net:
                    self.net.send({"type": "ping", "ts": now})

            # Status bar activity text.
            self._update_status()
        except Exception as exc:
            import logging
            logging.getLogger("whiteboard.ui").exception("poll loop error: %s", exc)
            if not getattr(self, "_poll_error_shown", False):
                self._poll_error_shown = True
                try:
                    self._toast(f"UI error: {exc}")
                except Exception:
                    pass
        finally:
            try:
                self.root.after(10, self._poll)
            except Exception:
                pass

    def _handle_message(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "_disconnected":
            self.status_conn.config(text=f"Disconnected: {msg.get('reason')}")
            if not self._reconnecting:
                self._begin_reconnect()
            return
        if mtype == "participant_list":
            users = msg.get("users", [])
            if isinstance(users, list):
                self.state.update_participants(users)
                for u in users:
                    if u.get("name") == self.username and u.get("color"):
                        self.user_color = u.get("color")
                        if hasattr(self, "identity_dot"):
                            self.identity_dot.delete("dot")
                            self.identity_dot.create_oval(2, 2, 12, 12, fill=self.user_color, outline="", tags=("dot",))
                # Update own role if changed.
                for u in users:
                    if u.get("name") == self.username and u.get("role") != self.role:
                        self.role = u.get("role", self.role)
                        self.root.title(f"LAN Whiteboard - {self.username} ({self.role}) - Room {self.room_code}")
                        if hasattr(self, "identity_role"):
                            self.identity_role.config(text=self.role)
                        self._update_permissions()
                self._rebuild_participants()
            return
        if mtype in ("user_joined", "user_left", "user_reconnected"):
            return
        if mtype == "user_disconnected":
            user = msg.get("username")
            if isinstance(user, str):
                self.state.remove_drawer(user)
            return
        if mtype == "chat":
            self.state.chat.append(msg)
            self._append_chat(msg.get("user", "?"), msg.get("text", ""), msg.get("timestamp"))
            return
        if mtype == "cursor_move":
            self._update_cursor(msg)
            return
        if mtype in ("stroke_start", "stroke_points", "stroke_end", "delete_object",
                     "shape", "text"):
            op_id = msg.get("op_id")
            if self.state.is_pending(op_id):
                self.state.clear_pending(op_id)
                self._pending_ops.pop(op_id, None)
                # Already rendered locally; just track seq.
                seq = msg.get("seq")
                if isinstance(seq, int) and not isinstance(seq, bool):
                    if seq > self.state.applied_seq:
                        self.state.applied_seq = seq
                return
            status = self.state.apply_op(msg)
            if status == "gap":
                self._sync_state = "syncing"
                self._resync_pending = True
                if self.net:
                    self.net.send({"type": "resync_request", "last_seq": self.state.applied_seq})
                return
            if mtype == "delete_object":
                tid = msg.get("target_id")
                if isinstance(tid, str):
                    item = self.canvas_items.pop(tid, None)
                    if item is not None:
                        self.canvas.delete(item)
                    self.state.objects.pop(tid, None)
                    if getattr(self, "_hover_erase", None) == tid:
                        self._hover_erase = None
                return
            if mtype == "shape":
                sid = msg.get("shape_id")
                obj = self.state.objects.get(sid) if isinstance(sid, str) else None
                if isinstance(obj, dict):
                    self._add_shape_item(obj)
                return
            if mtype == "text":
                oid = msg.get("obj_id")
                obj = self.state.objects.get(oid) if isinstance(oid, str) else None
                if isinstance(obj, dict):
                    self._add_text_item(obj)
                return
            # Render remote stroke updates.
            self._render_op(msg)
            return
        if mtype == "snapshot":
            self.state.apply_snapshot(msg)
            self._sync_state = "synced"
            self._resync_pending = False
            self.state.behind_since = None
            self._redraw_all()
            return
        if mtype == "ops_since":
            ops = msg.get("ops", [])
            if isinstance(ops, list):
                self.state.apply_ops_since(int(msg.get("from_seq", self.state.applied_seq)), ops)
                self._sync_state = "synced"
                self._resync_pending = False
                self.state.behind_since = None
                # Redraw to be safe.
                self._redraw_all()
            return
        if mtype == "reconnect_ok":
            self._handle_reconnect_ok(msg)
            return
        if mtype == "reconnect_denied":
            reason = msg.get("reason") or "denied"
            message = msg.get("message") or reason
            self._return_to_connect(f"Reconnection denied: {message}")
            return
        if mtype == "join_ok":
            # Late-arriving join confirmation (should already be handled).
            self.state.apply_snapshot(msg.get("snapshot", {}))
            if isinstance(msg.get("chat_history"), list):
                self.state.apply_chat_history(msg["chat_history"])
            if isinstance(msg.get("participants"), list):
                self.state.update_participants(msg["participants"])
            self._rebuild_participants()
            self._redraw_all()
            return
        if mtype == "undo":
            action = msg.get("action")
            if action == "remove_stroke":
                tid = msg.get("target_id")
                if isinstance(tid, str):
                    item = self.canvas_items.pop(tid, None)
                    if item is not None:
                        self.canvas.delete(item)
                    self.state.objects.pop(tid, None)
            elif action == "restore_object":
                obj = msg.get("object")
                if isinstance(obj, dict) and "id" in obj:
                    self.state.objects[obj["id"]] = obj
                    self._add_stroke_item(obj)
            elif action == "restore_objects":
                for obj in msg.get("objects", []):
                    if isinstance(obj, dict) and "id" in obj:
                        self.state.objects[obj["id"]] = obj
                        if obj.get("type") == "shape":
                            self._add_shape_item(obj)
                        elif obj.get("type") == "text":
                            self._add_text_item(obj)
                        else:
                            self._add_stroke_item(obj)
            elif action == "remove_object":
                tid = msg.get("target_id")
                if isinstance(tid, str):
                    item = self.canvas_items.pop(tid, None)
                    if item is not None:
                        self.canvas.delete(item)
                    self.state.objects.pop(tid, None)
            elif action == "add_object":
                obj = msg.get("object")
                if isinstance(obj, dict) and "id" in obj:
                    self.state.objects[obj["id"]] = obj
                    if obj.get("type") == "shape":
                        self._add_shape_item(obj)
                    elif obj.get("type") == "text":
                        self._add_text_item(obj)
                    else:
                        self._add_stroke_item(obj)
            return
        if mtype == "redo":
            action = msg.get("action")
            if action == "add_stroke":
                obj = msg.get("object")
                if isinstance(obj, dict) and "id" in obj:
                    self.state.objects[obj["id"]] = obj
                    self._add_stroke_item(obj)
            elif action == "add_object":
                obj = msg.get("object")
                if isinstance(obj, dict) and "id" in obj:
                    self.state.objects[obj["id"]] = obj
                    if obj.get("type") == "shape":
                        self._add_shape_item(obj)
                    elif obj.get("type") == "text":
                        self._add_text_item(obj)
                    else:
                        self._add_stroke_item(obj)
            elif action == "remove_stroke":
                tid = msg.get("target_id")
                if isinstance(tid, str):
                    item = self.canvas_items.pop(tid, None)
                    if item is not None:
                        self.canvas.delete(item)
                    self.state.objects.pop(tid, None)
            elif action == "remove_object":
                tid = msg.get("target_id")
                if isinstance(tid, str):
                    item = self.canvas_items.pop(tid, None)
                    if item is not None:
                        self.canvas.delete(item)
                    self.state.objects.pop(tid, None)
            elif action == "remove_objects":
                for tid in msg.get("target_ids", []):
                    if isinstance(tid, str):
                        item = self.canvas_items.pop(tid, None)
                        if item is not None:
                            self.canvas.delete(item)
                        self.state.objects.pop(tid, None)
            return
        if mtype == "clear_canvas":
            self.state.objects.clear()
            self.state.clear_drawers()
            self._redraw_all()
            return
        if mtype == "kicked":
            self.state.remove_drawer(self.username)
            self._toast("You were kicked from the room.")
            if self.net:
                self.net.disconnect()
            return
        if mtype in ("save_ok", "load_ok"):
            self._toast(f"{mtype.replace('_ok', '').capitalize()} successful.")
            return
        if mtype == "boards_list":
            boards = msg.get("boards", [])
            self._toast("Boards: " + ", ".join(boards) if boards else "No saved boards.")
            return
        if mtype == "pong":
            ts = msg.get("ts")
            if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                rtt = (time.time() - float(ts)) * 1000.0
                text = f"latency: {int(rtt)} ms"
                if hasattr(self, "status_latency") and getattr(self, "_last_latency_text", None) != text:
                    self._last_latency_text = text
                    self.status_latency.config(text=text)
            return
        if mtype == "error":
            # Keep the sent operation next to the rejection in the log; that
            # is what makes an eraser rejection diagnosable.
            logging.getLogger("whiteboard.ui").warning(
                "server rejected message=%s last_delete=%s", msg, getattr(self, "_last_delete", None))
            self._toast(f"Rejected: {msg.get('message', msg.get('code'))}")
            return

    def _render_op(self, op: dict[str, Any]) -> None:
        mtype = op.get("type")
        if mtype == "stroke_start":
            sid = op.get("stroke_id")
            if not isinstance(sid, str):
                return
            if sid in self.canvas_items:
                return
            x = op.get("x", 0)
            y = op.get("y", 0)
            color = op.get("color", "#000000")
            width = op.get("width", 2)
            cx, cy = self._to_window(float(x), float(y))
            item = self.canvas.create_line(cx, cy, cx, cy, fill=color,
                                           width=self._stroke_width(width),
                                           capstyle="round", smooth=True, tags=(sid,))
            self.canvas_items[sid] = item
            self._raise_matte()
        elif mtype == "stroke_points":
            sid = op.get("stroke_id")
            item = self.canvas_items.get(sid) if isinstance(sid, str) else None
            obj = self.state.objects.get(sid) if isinstance(sid, str) else None
            if item is None:
                return
            if obj is not None:
                flat = self._flatten_points(obj.get("points", []))
                if len(flat) >= 4:
                    self.canvas.coords(item, *flat)
                    self._raise_matte()
        # stroke_end needs no visual change.

    def _add_stroke_item(self, obj: dict[str, Any]) -> None:
        sid = obj.get("id")
        if not isinstance(sid, str) or sid in self.canvas_items:
            return
        pts = obj.get("points", [])
        if not isinstance(pts, list) or not pts:
            pts = [[0, 0], [0, 0]]
        if len(pts) == 1:
            pts = [pts[0], pts[0]]
        flat = self._flatten_points(pts)
        if len(flat) < 4:
            return
        color = obj.get("color", "#000000")
        width = obj.get("width", 2)
        item = self.canvas.create_line(*flat, fill=color,
                                       width=self._stroke_width(width),
                                       capstyle="round", smooth=True, tags=(sid,))
        self.canvas_items[sid] = item
        self._raise_matte()

    def _add_shape_item(self, obj: dict[str, Any]) -> None:
        sid = obj.get("id")
        if not isinstance(sid, str) or sid in self.canvas_items:
            return
        kind = obj.get("kind")
        p1 = obj.get("p1", [0, 0])
        p2 = obj.get("p2", [0, 0])
        color = obj.get("color", "#000000")
        width = self._stroke_width(obj.get("width", 2))
        fill = obj.get("fill")
        try:
            import shapes_geometry as _sg
            pts = _sg.shape_points(kind, float(p1[0]), float(p1[1]),
                                   float(p2[0]), float(p2[1]))
        except Exception:
            pts = []
        if not pts:
            return
        flat = []
        for (lx, ly) in pts:
            cx, cy = self._to_window(lx, ly)
            flat.extend((cx, cy))
        if len(flat) < 4:
            return
        # closed shape if first==last
        item = self._draw_shape(kind, float(p1[0]), float(p1[1]),
                                float(p2[0]), float(p2[1]), color, width, fill,
                                "final", (sid,))
        if item is None:
            return
        self.canvas_items[sid] = item
        self._raise_matte()

    def _add_text_item(self, obj: dict[str, Any]) -> None:
        oid = obj.get("id")
        if not isinstance(oid, str) or oid in self.canvas_items:
            return
        x, y = self._to_window(float(obj.get("x", 0)), float(obj.get("y", 0)))
        size = int(max(8, float(obj.get("size", 12)) * self.scale))
        color = obj.get("color", "#000000")
        text = str(obj.get("text", ""))
        item = self.canvas.create_text(x, y, text=text, fill=color, anchor="nw",
                                       font=("Segoe UI", size), tags=(oid,))
        self.canvas_items[oid] = item
        self._raise_matte()

    def _update_shape_preview(self, x0, y0, x1, y1, state):
        self._clear_shape_preview()
        kind = self._shape_kind
        color = self.color
        width = self._stroke_width(self.width)
        item = self._draw_shape(kind, x0, y0, x1, y1, color, width, None,
                                "preview", ("shape_preview",))
        self._shape_preview = item
        if item is not None:
            self._raise_matte()

    def _draw_shape(self, kind, x1, y1, x2, y2, color, width, fill, style="final",
                    tags=()):
        """Render one shape. Shared by committed shapes and live previews."""
        try:
            import shapes_geometry as _sg
            pts = _sg.shape_points(kind, float(x1), float(y1), float(x2), float(y2))
        except Exception:
            pts = []
        if not pts:
            return None
        flat = []
        for (lx, ly) in pts:
            cx, cy = self._to_window(lx, ly)
            flat.extend((cx, cy))
        if len(flat) < 4:
            return None
        tags = tuple(tags) if not isinstance(tags, tuple) else tags
        dashed = (4, 4) if style == "preview" else None
        if kind in ("line", "arrow", "double_arrow"):
            kwargs = {"fill": color, "width": width, "tags": tags}
            if dashed is not None:
                kwargs["dash"] = dashed
            if kind in ("arrow", "double_arrow"):
                kwargs["arrow"] = tk.LAST
            return self.canvas.create_line(*flat, **kwargs)
        kwargs = {"outline": color, "width": width, "tags": tags}
        if dashed is not None:
            kwargs["dash"] = dashed
        item_fill = fill if (style == "final" and fill) else ""
        kwargs["fill"] = item_fill
        return self.canvas.create_polygon(*flat, **kwargs)

    def _clear_shape_preview(self):
        if self._shape_preview is not None:
            try:
                self.canvas.delete(self._shape_preview)
            except tk.TclError:
                pass
            self._shape_preview = None

    def _place_text_entry(self, px, py, lx, ly):
        if self.role == "viewer":
            return
        if getattr(self, "_text_entry", None) is not None:
            return
        cx, cy = self._to_window(lx, ly)
        t = self.theme
        entry = tk.Text(self.canvas, width=20, height=2, font=("Segoe UI", int(self.text_size.get())),
                        wrap="word", bg=t["surface"], fg=t["ink"], insertbackground=t["ink"],
                        selectbackground=t["accent"], selectforeground=t["on_accent"],
                        borderwidth=1, relief="solid", highlightthickness=1,
                        highlightbackground=t["accent"], highlightcolor=t["accent"])
        entry.place(x=cx, y=cy)
        entry.focus_set()
        self._text_entry = entry
        self._text_pos = (lx, ly)

        def commit(event=None):
            text = entry.get("1.0", "end-1c")
            entry.destroy()
            self._text_entry = None
            if not text.strip():
                return "break"
            oid = str(uuid.uuid4())
            op_id = str(uuid.uuid4())
            size = int(self.text_size.get())
            msg = {"type": "text", "op_id": op_id, "obj_id": oid, "x": lx, "y": ly,
                   "text": text, "size": size, "color": self.color}
            obj = {"id": oid, "type": "text", "x": lx, "y": ly, "text": text,
                   "size": size, "color": self.color, "user": self.username, "seq": 0}
            self.state.objects[oid] = obj
            self._add_text_item(obj)
            self.state.track_pending(op_id)
            self._pending_ops[op_id] = msg
            self.state.start_drawing(self.username)
            self.state.end_drawing(self.username)
            if self.net:
                self.net.send(msg)
            return "break"

        def cancel(event=None):
            entry.destroy()
            self._text_entry = None
            return "break"

        entry.bind("<Return>", lambda e: commit() if not (e.state & 0x0001) else None)
        entry.bind("<Shift-Return>", lambda e: None)
        entry.bind("<Escape>", lambda e: cancel())
        entry.bind("<FocusOut>", lambda e: cancel())

    def _draw_board_background(self) -> None:
        self.canvas.delete("board_bg")
        w, h = self._canvas_size()
        x0, y0, x1, y1 = board_rect(w, h, self._canvas_inset())
        # Always light paper, even in the dark theme, so dark ink and the
        # coloured strokes stay readable.  The surround carries the theme.
        self.canvas.create_rectangle(x0, y0, x1, y1, fill=self.theme["paper"],
                                     outline=self.theme["paper_border"], width=2,
                                     tags=("board_bg",))
        self.canvas.tag_lower("board_bg")
        self._draw_clip_matte()

    def _draw_clip_matte(self) -> None:
        """Cover everything outside the paper with the surround colour.

        Tk canvases have no clip rectangle, so four opaque rectangles act as
        a matte.  They are raised above strokes and cursor markers below.
        """
        self.canvas.delete("matte")
        w, h = self._canvas_size()
        x0, y0, x1, y1 = board_rect(w, h, self._canvas_inset())
        surround = self.theme["canvas"]
        for coords in ((0, 0, max(0, x0), h),
                       (x1, 0, w, h),
                       (x0, 0, x1, max(0, y0)),
                       (x0, y1, x1, h)):
            self.canvas.create_rectangle(*coords, fill=surround, outline="",
                                         tags=("matte",))
        self._raise_matte()

    def _raise_matte(self) -> None:
        try:
            self.canvas.tag_raise("matte")
        except tk.TclError:
            pass

    def _redraw_all(self) -> None:
        self.canvas.delete("all")
        self.canvas_items.clear()
        # Item ids in cursor_items are stale after delete("all"); the next
        # cursor message recreates them.
        self.cursor_items.clear()
        self._cursor_last.clear()
        self._hover_erase = None
        self._draw_board_background()
        for oid, obj in self.state.objects.items():
            otype = obj.get("type")
            if otype == "stroke":
                pts = obj.get("points", [])
                if not isinstance(pts, list) or len(pts) < 1:
                    continue
                self._add_stroke_item(obj)
            elif otype == "shape":
                self._add_shape_item(obj)
            elif otype == "text":
                self._add_text_item(obj)
        self.canvas.tag_raise("matte")
        if self.debug_overlay:
            self._raise_debug_overlay()

    # ------------------------------------------------------------------ #
    # Participants and chat
    # ------------------------------------------------------------------ #
    def _rebuild_participants(self) -> None:
        if not hasattr(self, "participants_frame"):
            return
        if not hasattr(self, "_participant_rows"):
            self._participant_rows = {}
            self._participant_signature = None

        signature = tuple((u.get("name"), u.get("role"), u.get("online"), u.get("color")) for u in self.state.participants)
        if hasattr(self, "host_target"):
            names = [u.get("name", "") for u in self.state.participants if u.get("name") != self.username]
            if tuple(self.host_target["values"]) != tuple(names):
                self.host_target["values"] = names
            if self.host_target.get() not in names:
                if names:
                    self.host_target.set(names[0])
                else:
                    self.host_target.set("")
            self._update_host_buttons()

        if signature == self._participant_signature:
            self._update_activity_indicators()
            return

        # Rebuild rows only when membership or roles change.
        for w in self.participants_frame.winfo_children():
            w.destroy()
        self._participant_rows.clear()

        for u in self.state.participants:
            row = ttk.Frame(self.participants_frame)
            row.pack(fill="x", pady=2)
            dot = tk.Canvas(row, width=12, height=12, highlightthickness=0, bg=self.theme["bg"])
            dot.pack(side="left", padx=(0, 6))
            name = u.get("name", "?")
            color = u.get("color", "#888888")
            online = u.get("online", True)
            if not online:
                color = self.theme["ink_muted"]
            dot.create_oval(2, 2, 10, 10, fill=color, outline="", tags=("dot",))
            role = u.get("role", "editor")
            suffix = " (you)" if name == self.username else ""
            status = "" if online else " (offline)"
            name_label = ttk.Label(row, text=f"{name}{suffix}  [{role}]{status}")
            name_label.pack(side="left")
            # Reserved slot for the drawing indicator (fixed width).
            draw_label = ttk.Label(row, text="", width=10)
            draw_label.pack(side="left")
            self._participant_rows[name] = {
                "dot": dot,
                "name_label": name_label,
                "draw_label": draw_label,
                "color": color,
                "online": online,
            }

        self._participant_signature = signature
        self._update_activity_indicators()

    def _update_activity_indicators(self):
        if not hasattr(self, "_participant_rows"):
            return
        for name, info in self._participant_rows.items():
            active = name in self.state.active_drawers
            color = info.get("color", "#888888")
            if active:
                glyph = "\u270e" if not self._blink else "\u270f"
                foreground = color
            else:
                glyph = ""
                foreground = self.theme["ink_muted"]
            # The reserved slot is always present; only its glyph/colour
            # changes, and only when the value really changed.
            if info.get("last_glyph") != glyph:
                info["last_glyph"] = glyph
                info["draw_label"].config(text=glyph)
            if info.get("last_fg") != foreground:
                info["last_fg"] = foreground
                info["draw_label"].config(foreground=foreground)

    def _append_chat(self, user, text, timestamp):
        self.chat_text.config(state="normal")
        try:
            ts = time.strftime("%H:%M", time.localtime(timestamp)) if timestamp else ""
        except (TypeError, ValueError, OSError):
            ts = ""
        color = self.theme["accent"]
        for u in self.state.participants:
            if u.get("name") == user:
                color = u.get("color", color)
        self._configure_chat_tags()
        name_tag = f"name_{user}"
        self.chat_text.tag_configure(name_tag, foreground=color, font=("Segoe UI", 10, "bold"))
        self.chat_text.insert("end", f"{user}", name_tag)
        if ts:
            self.chat_text.insert("end", f"  {ts}", "time_tag")
        self.chat_text.insert("end", "\n")
        self.chat_text.insert("end", f"{text}\n", "body_tag")
        # Auto-scroll only if the user was already at the bottom.
        try:
            at_bottom = self.chat_text.yview()[1] >= 0.999
        except tk.TclError:
            at_bottom = True
        if at_bottom:
            self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    def _send_chat(self):
        text = self.chat_entry.get().strip()
        if not text or not self.net:
            return
        self.chat_entry.delete(0, "end")
        self.net.send({"type": "chat", "text": text})

    def _undo(self):
        if self.role == "viewer":
            self._toast("Viewers cannot do this.")
            return
        if self.net:
            self.net.send({"type": "undo", "op_id": str(uuid.uuid4())})

    def _redo(self):
        if self.role == "viewer":
            self._toast("Viewers cannot do this.")
            return
        if self.net:
            self.net.send({"type": "redo", "op_id": str(uuid.uuid4())})

    def _clear_canvas(self):
        if self.net and self.role == "host":
            self.net.send({"type": "clear_canvas", "op_id": str(uuid.uuid4())})

    def _save_board(self):
        if not self.net or self.role != "host":
            return
        name = simpledialog.askstring("Save board", "Board name:", parent=self.root)
        if name:
            self.net.send({"type": "save_board", "name": name})

    def _load_board(self):
        if not self.net or self.role != "host":
            return
        name = simpledialog.askstring("Load board", "Board name:", parent=self.root)
        if name:
            self.net.send({"type": "load_board", "name": name})

    def _update_host_buttons(self):
        if not hasattr(self, "apply_role_btn"):
            return
        if self.host_target.get().strip():
            self.apply_role_btn.state(["!disabled"])
            self.kick_btn.state(["!disabled"])
        else:
            self.apply_role_btn.state(["disabled"])
            self.kick_btn.state(["disabled"])

    def _apply_role(self):
        if not self.net or self.role != "host":
            return
        target = self.host_target.get().strip()
        new_role = self.host_role.get().strip()
        if target:
            self.net.send({"type": "role_change", "target": target, "role": new_role})

    def _kick_user(self):
        if not self.net or self.role != "host":
            return
        target = self.host_target.get().strip()
        if target:
            self.net.send({"type": "kick", "target": target})

    def _toggle_lock(self):
        if self.net and self.role == "host":
            self.net.send({"type": "lock_room", "locked": bool(self.lock_var.get())})

    def _toast(self, text):
        if not hasattr(self, "status_conn"):
            return
        self.status_conn.config(text=text)
        self.root.after(3000, lambda: self.status_conn.config(
            text="Connected" if (self.net and self.net.connected) else "Disconnected"))

    def _report_exception(self, exc_type, exc_value, exc_tb):
        import logging
        import time
        import traceback

        logger = logging.getLogger("whiteboard.ui")
        frames = traceback.extract_tb(exc_tb)
        if frames:
            last = frames[-1]
            location = f"{last.filename}:{last.lineno}"
        else:
            location = "<unknown>"
        key = (getattr(exc_type, "__name__", str(exc_type)), str(exc_value), location)
        now = time.time()
        self._ui_error_counts[key] = self._ui_error_counts.get(key, 0) + 1
        count = self._ui_error_counts[key]

        if key not in self._ui_error_last_full:
            self._ui_error_last_full[key] = now
            logger.error("unhandled UI exception: %s",
                        "".join(traceback.format_exception(exc_type, exc_value, exc_tb)))
        elif (now - self._ui_error_last_summary) >= self._UI_ERROR_LOG_INTERVAL:
            self._ui_error_last_summary = now
            total = sum(self._ui_error_counts.values())
            logger.error("UI exceptions repeated: %d total; latest %s: %s (%s) count=%d",
                        total, key[0], key[1], key[2], count)

        if (now - self._ui_error_last_toast) >= self._UI_ERROR_TOAST_INTERVAL:
            self._ui_error_last_toast = now
            try:
                self._toast(f"UI error: {exc_value}")
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Cursors
    # ------------------------------------------------------------------ #
    def _update_cursor(self, msg):
        user = msg.get("user")
        if not isinstance(user, str) or user == self.username:
            return
        x = msg.get("x")
        y = msg.get("y")
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return
        lx = max(0.0, min(float(LOGICAL_WIDTH), float(x)))
        ly = max(0.0, min(float(LOGICAL_HEIGHT), float(y)))
        cx, cy = self._to_window(lx, ly)
        color = "#888888"
        for u in self.state.participants:
            if u.get("name") == user:
                color = u.get("color", color)
        active = user in self.state.active_drawers
        # Keep the label inside the paper when the marker is near the edge.
        text_x = cx + 8
        anchor = "w"
        if text_x + 70 > self.canvas.winfo_width():
            text_x = cx - 8
            anchor = "e"
        item = self.cursor_items.get(user)
        if item is None:
            if active:
                oval = self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=color, outline=color, tags=("cursor",))
            else:
                oval = self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="", outline=color, tags=("cursor",))
            text = self.canvas.create_text(text_x, cy, text=user, fill=color,
                                           font=("Segoe UI", 8), anchor=anchor,
                                           tags=("cursor",))
            self.cursor_items[user] = (oval, text)
        else:
            oval, text = item
            if active:
                self.canvas.itemconfig(oval, fill=color, outline=color)
            else:
                self.canvas.itemconfig(oval, fill="", outline=color)
            self.canvas.coords(oval, cx - 4, cy - 4, cx + 4, cy + 4)
            self.canvas.coords(text, text_x, cy)
            self.canvas.itemconfig(text, anchor=anchor)
        self._cursor_last[user] = time.time()
        self._raise_matte()

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def _update_status(self):
        if self.net is None or not hasattr(self, "status_conn"):
            return
        conn = f"Connected | room {self.room_code}"
        if getattr(self, "_last_conn_text", None) != conn:
            self._last_conn_text = conn
            self.status_conn.config(text=conn)
        # Activity text.
        drawers = sorted(self.state.active_drawers)
        if len(drawers) == 1:
            activity = f"{drawers[0]} is drawing"
        elif len(drawers) == 2:
            activity = f"{drawers[0]} and {drawers[1]} are drawing"
        elif len(drawers) > 2:
            activity = f"{len(drawers)} people are drawing"
        else:
            activity = ""
        if getattr(self, "_last_activity_text", None) != activity:
            self._last_activity_text = activity
            self.status_activity.config(text=activity)
        seq_text = f"seq: {self.state.applied_seq}"
        if getattr(self, "_last_seq_text", None) != seq_text:
            self._last_seq_text = seq_text
            self.status_seq.config(text=seq_text)
        now = time.time()
        applied = self.state.applied_seq
        latest = getattr(self.state, "latest_seq", applied)
        resync_pending = getattr(self, "_resync_pending", False) or self._sync_state == "syncing"
        if applied >= latest and latest > 0 and not resync_pending:
            sync_text = "synced"
            color = self.theme["ink_muted"]
            self.state.behind_since = None
        elif applied >= latest and latest == 0:
            sync_text = "synced"
            color = self.theme["ink_muted"]
            self.state.behind_since = None
        else:
            if self.state.behind_since is None:
                self.state.behind_since = now
            if now - self.state.behind_since > 5.0:
                sync_text = "out of sync"
                color = self.theme["danger"]
            else:
                sync_text = "syncing..."
                color = self.theme["highlight"]
        if getattr(self, "_last_sync_text", None) != sync_text:
            self._last_sync_text = sync_text
            if hasattr(self, "status_sync"):
                self.status_sync.config(text=sync_text, foreground=color)


class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        # Let permission updates find and rewrite an existing tooltip.
        widget._tooltip = self
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

    def set_text(self, text):
        self.text = text
        if self.tip is not None:
            self.hide()
            self.show()

    def show(self, _event=None):
        if self.tip is not None:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        lbl = tk.Label(tw, text=self.text, background="#FFFFE0", relief="solid", borderwidth=1,
                       font=("Segoe UI", 9), padx=4, pady=2)
        lbl.pack()

    def hide(self, _event=None):
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None