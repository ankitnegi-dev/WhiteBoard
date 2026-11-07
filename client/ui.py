"""Tkinter user interface for the whiteboard client.

Only this module touches tkinter widgets. It talks to the network layer
through client.net.NetworkClient and to the board model through
client.state.BoardState. Incoming network messages are polled from the
main thread via root.after.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any, Optional

try:
    import tkinter as tk
    from tkinter import ttk, colorchooser, messagebox, simpledialog
except ImportError as exc:  # pragma: no cover - handled in main.py
    raise

from client.net import NetworkClient
from client.state import BoardState, canvas_to_logical, compute_scale, logical_to_canvas
from client.tools import find_stroke_at

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
    "highlight": "#E8B923",
    "danger": "#B83A2E",
    "border": "#D6D0C4",
    "canvas": "#FBF8F1",
}

DARK = {
    "bg": "#1B1F24",
    "surface": "#262B33",
    "surface_alt": "#1F2329",
    "ink": "#E8E6E1",
    "ink_muted": "#9CA3AF",
    "accent": "#5FA8A8",
    "accent_hover": "#72B8B8",
    "highlight": "#D6A84F",
    "danger": "#D06050",
    "border": "#3A4048",
    "canvas": "#1F2329",
}

USER_COLORS_FALLBACK = ["#e6194b", "#3cb44b", "#4363d8", "#f58231", "#911eb4"]


class WhiteboardApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("LAN Collaborative Whiteboard")
        self.root.geometry("1280x800")
        self.root.minsize(1100, 700)

        self.theme_name = "light"
        self.theme = LIGHT

        self.net: Optional[NetworkClient] = None
        self.state = BoardState()
        self.username: str = ""
        self.room_code: str = ""
        self.role: str = "editor"

        # Drawing state.
        self.tool = "pen"
        self.color = "#1F2937"
        self.user_color = "#1E5B6B"
        self.width = 2.0
        self.canvas_items: dict[str, int] = {}   # stroke_id -> canvas item id
        self.cursor_items: dict[str, int] = {}   # username -> marker item
        self.local_strokes: dict[str, dict[str, Any]] = {}
        self._last_cursor_send = 0.0
        self._blink = False
        self._last_ping_time = 0.0
        self._ping_ts = 0.0

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
        self.theme_name = "light"
        self.theme = LIGHT

        if mtype == "room_created":
            self.room_code = resp["room_code"]
            self.role = resp.get("role", "host")
            # Host color: look it up once participants arrive; default accent.
            self.user_color = "#1E5B6B"
        elif mtype == "join_ok":
            self.room_code = resp.get("room_code", self.entry_room_code.get().strip().upper())
            # room code may not be in join_ok; keep from entry
            if not self.room_code:
                self.room_code = self.entry_room_code.get().strip().upper()
            self.role = resp.get("role", "editor")
            self.user_color = resp.get("user_color", self.user_color)
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
    # Main window
    # ------------------------------------------------------------------ #
    def _build_main(self) -> None:
        self.main_frame = ttk.Frame(self.root)
        self.main_frame.pack(fill="both", expand=True)

        # Left toolbar.
        self.toolbar = ttk.Frame(self.main_frame, width=72, padding=8)
        self.toolbar.pack(side="left", fill="y")
        self.toolbar.pack_propagate(False)

        self.tool_buttons = {}
        self._tool_button("Pen", "pen")
        self._tool_button("Eraser", "eraser")
        ttk.Separator(self.toolbar, orient="horizontal").pack(fill="x", pady=8)

        self.color_btn = ttk.Button(self.toolbar, text="Color", command=self._pick_color, style="Tool.TButton")
        self.color_btn.pack(fill="x", pady=4)
        self.color_swatch = tk.Label(self.toolbar, text="  ", bg=self.color, width=4, relief="solid", borderwidth=1)
        self.color_swatch.pack(pady=4)

        ttk.Label(self.toolbar, text="Width", font=("Segoe UI", 9)).pack(pady=(8, 0))
        width_row = ttk.Frame(self.toolbar)
        width_row.pack(fill="x", pady=2)
        self.width_scale = ttk.Scale(width_row, from_=1, to=20, orient="horizontal", command=self._on_width)
        self.width_scale.set(self.width)
        self.width_scale.pack(side="left", fill="x", expand=True)
        self.width_value = ttk.Label(width_row, text=f"{int(self.width)}", width=3)
        self.width_value.pack(side="left", padx=4)
        self.width_preview = tk.Canvas(self.toolbar, width=56, height=20, highlightthickness=0)
        self.width_preview.pack(pady=2)
        self._update_width_preview()

        ttk.Separator(self.toolbar, orient="horizontal").pack(fill="x", pady=8)
        ttk.Button(self.toolbar, text="Undo", command=self._undo).pack(fill="x", pady=2)
        ttk.Button(self.toolbar, text="Redo", command=self._redo).pack(fill="x", pady=2)
        self.clear_btn = ttk.Button(self.toolbar, text="Clear", command=self._clear_canvas)
        self.clear_btn.pack(fill="x", pady=2)
        self.save_btn = ttk.Button(self.toolbar, text="Save", command=self._save_board)
        self.save_btn.pack(fill="x", pady=2)
        self.load_btn = ttk.Button(self.toolbar, text="Load", command=self._load_board)
        self.load_btn.pack(fill="x", pady=2)

        if self.role != "host":
            self.clear_btn.state(["disabled"])
            self.save_btn.state(["disabled"])
            self.load_btn.state(["disabled"])

        ttk.Separator(self.toolbar, orient="horizontal").pack(fill="x", pady=8)
        self.theme_btn = ttk.Button(self.toolbar, text="Theme", command=self._toggle_theme)
        self.theme_btn.pack(fill="x", pady=4)

        # Center canvas.
        canvas_frame = ttk.Frame(self.main_frame)
        canvas_frame.pack(side="left", fill="both", expand=True)
        self.canvas = tk.Canvas(canvas_frame, bg=self.theme["canvas"], highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=12, pady=12)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_hover)

        # Right panel.
        self.right = ttk.Frame(self.main_frame, width=280, padding=8)
        self.right.pack(side="right", fill="y")
        self.right.pack_propagate(False)

        # Identity chip.
        self.identity_frame = ttk.Frame(self.right, padding=6, relief="solid", borderwidth=1)
        self.identity_frame.pack(fill="x", pady=(0, 8))
        self.identity_dot = tk.Canvas(self.identity_frame, width=14, height=14, highlightthickness=0, bg=self.theme["bg"])
        self.identity_dot.pack(side="left", padx=(0, 6))
        self.identity_dot.create_oval(2, 2, 12, 12, fill=self.user_color, outline="", tags=("dot",))
        self.identity_name = ttk.Label(self.identity_frame, text=self.username, font=("Segoe UI", 11, "bold"))
        self.identity_name.pack(side="left")
        self.identity_role = ttk.Label(self.identity_frame, text=self.role, font=("Segoe UI", 9))
        self.identity_role.pack(side="left", padx=6)

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

        ttk.Label(self.right, text="Chat", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.chat_text = tk.Text(self.right, height=16, state="disabled", wrap="word", font=("Segoe UI", 10))
        self.chat_text.pack(fill="both", expand=True, pady=4)
        chat_input = ttk.Frame(self.right)
        chat_input.pack(fill="x")
        self.chat_entry = ttk.Entry(chat_input)
        self.chat_entry.pack(side="left", fill="x", expand=True)
        self.chat_entry.bind("<Return>", lambda _e: self._send_chat())
        ttk.Button(chat_input, text="Send", command=self._send_chat).pack(side="left", padx=4)

        if self.role == "host":
            ttk.Separator(self.right, orient="horizontal").pack(fill="x", pady=8)
            ttk.Label(self.right, text="Host controls", font=("Segoe UI", 11, "bold")).pack(anchor="w")
            hc = ttk.Frame(self.right)
            hc.pack(fill="x", pady=4)
            ttk.Label(hc, text="Target").grid(row=0, column=0, sticky="w")
            self.host_target = ttk.Combobox(hc, values=[], width=12)
            self.host_target.grid(row=0, column=1, padx=4)
            self.host_target.bind("<<ComboboxSelected>>", lambda _e: self._update_host_buttons())
            ttk.Label(hc, text="Role").grid(row=1, column=0, sticky="w")
            self.host_role = ttk.Combobox(hc, values=["editor", "viewer"], width=12)
            self.host_role.set("editor")
            self.host_role.grid(row=1, column=1, padx=4)
            self.apply_role_btn = ttk.Button(hc, text="Apply role", command=self._apply_role)
            self.apply_role_btn.grid(row=2, column=0, columnspan=2, pady=4, sticky="ew")
            self.kick_btn = ttk.Button(hc, text="Kick", command=self._kick_user)
            self.kick_btn.grid(row=3, column=0, columnspan=2, pady=4, sticky="ew")
            self.apply_role_btn.state(["disabled"])
            self.kick_btn.state(["disabled"])
            self.lock_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(hc, text="Lock room", variable=self.lock_var, command=self._toggle_lock).grid(row=4, column=0, columnspan=2, sticky="w")

        # Status bar.
        self.status_bar = ttk.Frame(self.root, padding=(8, 2))
        self.status_bar.pack(side="bottom", fill="x")
        self.status_conn = ttk.Label(self.status_bar, text="Connecting")
        self.status_conn.pack(side="left", padx=4)
        self.status_activity = ttk.Label(self.status_bar, text="")
        self.status_activity.pack(side="left", padx=12)
        self.status_latency = ttk.Label(self.status_bar, text="latency: -")
        self.status_latency.pack(side="right", padx=4)
        self.status_seq = ttk.Label(self.status_bar, text="seq: 0")
        self.status_seq.pack(side="right", padx=4)

        # Keyboard shortcuts (Control and Command).
        for mod in ("<Control-", "<Command-"):
            self.root.bind(f"{mod}p>", lambda _e: self._set_tool("pen"))
            self.root.bind(f"{mod}e>", lambda _e: self._set_tool("eraser"))
            self.root.bind(f"{mod}t>", lambda _e: self._toggle_theme())
            self.root.bind(f"{mod}z>", lambda _e: self._undo())
            self.root.bind(f"{mod}y>", lambda _e: self._redo())
        self.root.bind("<Control-Shift-Z>", lambda _e: self._redo())
        self.root.bind("<Command-Shift-Z>", lambda _e: self._redo())
        self.root.bind("<Escape>", lambda _e: self._set_tool("pen"))

        # Mouse wheel for width (cross-platform).
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._adjust_width(1))
        self.canvas.bind("<Button-5>", lambda e: self._adjust_width(-1))

    def _tool_button(self, label, tool):
        btn = ttk.Button(self.toolbar, text=label, command=lambda: self._set_tool(tool), style="Tool.TButton")
        btn.pack(fill="x", pady=4)
        self.tool_buttons[tool] = btn
        Tooltip(btn, f"Select {label.lower()} tool")

    def _set_tool(self, tool):
        self.tool = tool
        for t, btn in self.tool_buttons.items():
            btn.configure(style="ToolActive.TButton" if t == tool else "Tool.TButton")
        self.status_conn.config(text=f"Tool: {tool}")

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
        y = 10
        c.create_oval(4, y - self.width / 2, 4 + self.width, y + self.width / 2,
                      fill=self.color, outline="")

    def _adjust_width(self, delta):
        self.width = max(1.0, min(20.0, self.width + delta))
        self.width_scale.set(self.width)

    def _on_wheel(self, event):
        if event.delta > 0:
            self._adjust_width(1)
        elif event.delta < 0:
            self._adjust_width(-1)

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
        style.configure("TButton", background=t["accent"], foreground="#FFFFFF", padding=6)
        style.map("TButton", background=[("active", t["accent_hover"])])
        style.configure("Tool.TButton", background=t["surface"], foreground=t["ink"], padding=4)
        style.map("Tool.TButton", background=[("active", t["surface_alt"])])
        style.configure("ToolActive.TButton", background=t["accent"], foreground="#FFFFFF", padding=4)
        style.configure("TEntry", fieldbackground=t["surface"], foreground=t["ink"])
        style.configure("TCombobox", fieldbackground=t["surface"], foreground=t["ink"])
        style.configure("TScale", background=t["bg"])
        self.root.configure(bg=t["bg"])
        self.canvas.configure(bg=t["canvas"])
        if hasattr(self, "chat_text"):
            self.chat_text.configure(bg=t["surface"], fg=t["ink"], insertbackground=t["ink"])
        if hasattr(self, "width_preview"):
            self.width_preview.configure(bg=t["bg"])

    # ------------------------------------------------------------------ #
    # Canvas geometry
    # ------------------------------------------------------------------ #
    def _on_canvas_configure(self, event):
        self.scale, self.offset_x, self.offset_y = compute_scale(event.width, event.height)
        self._redraw_all()

    def _to_logical(self, x, y):
        return canvas_to_logical(x, y, self.canvas.winfo_width(), self.canvas.winfo_height(),
                                 self.offset_x, self.offset_y, self.scale)

    # ------------------------------------------------------------------ #
    # Drawing input
    # ------------------------------------------------------------------ #
    def _on_press(self, event):
        lx, ly = self._to_logical(event.x, event.y)
        self._last_erased = None
        if self.tool == "pen":
            sid = str(uuid.uuid4())
            op_id = str(uuid.uuid4())
            item = self.canvas.create_line(lx, ly, lx, ly, fill=self.color, width=self.width,
                                           capstyle="round", smooth=True, tags=(sid,))
            self.canvas_items[sid] = item
            self.local_strokes[sid] = {
                "op_id": op_id,
                "points": [[lx, ly]],
                "last_send": time.time(),
                "batch": [],
            }
            self.state.track_pending(op_id)
            self.state.start_drawing(self.username)
            if self.net:
                self.net.send({"type": "stroke_start", "op_id": op_id, "stroke_id": sid,
                               "tool": "pen", "color": self.color, "width": self.width,
                               "x": lx, "y": ly})
        elif self.tool == "eraser":
            sid = find_stroke_at(self.state.objects, lx, ly, threshold=10.0 / max(self.scale, 0.001))
            if sid and self.net and self.role != "viewer":
                self.net.send({"type": "delete_object", "op_id": str(uuid.uuid4()), "target_id": sid})
                logging.getLogger("whiteboard.ui").debug("sent delete_object target=%s", sid)
            elif sid and self.role == "viewer":
                self._toast("Viewers cannot erase strokes.")

    def _on_motion(self, event):
        lx, ly = self._to_logical(event.x, event.y)
        if self.tool == "pen":
            # Find the active stroke (last one being drawn).
            if not self.local_strokes:
                return
            sid = list(self.local_strokes.keys())[-1]
            info = self.local_strokes[sid]
            info["points"].append([lx, ly])
            # Extend the polyline.
            item = self.canvas_items.get(sid)
            if item is not None:
                flat = [c for pt in info["points"] for c in pt]
                self.canvas.coords(item, *flat)
            # Batch points.
            info["batch"].append([lx, ly])
            now = time.time()
            if now - info["last_send"] >= 0.04 and info["batch"]:
                if self.net:
                    self.net.send({"type": "stroke_points", "op_id": str(uuid.uuid4()),
                                   "stroke_id": sid, "points": info["batch"]})
                self.state.note_drawing(self.username, now)
                info["batch"] = []
                info["last_send"] = now
        elif self.tool == "eraser":
            # Hover highlight and sweep-to-erase.
            sid = find_stroke_at(self.state.objects, lx, ly, threshold=10.0 / max(self.scale, 0.001))
            self._highlight_erase_target(sid)
            if sid and self.net and self.role != "viewer" and sid != getattr(self, "_last_erased", None):
                self.net.send({"type": "delete_object", "op_id": str(uuid.uuid4()), "target_id": sid})
                logging.getLogger("whiteboard.ui").debug("sent delete_object target=%s", sid)
                self._last_erased = sid
        else:
            # Throttled cursor move.
            now = time.time()
            if self.net and now - self._last_cursor_send >= 0.05:
                self.net.send({"type": "cursor_move", "x": lx, "y": ly})
                self._last_cursor_send = now

    def _on_release(self, event):
        if self.tool != "pen" or not self.local_strokes:
            return
        sid = list(self.local_strokes.keys())[-1]
        info = self.local_strokes.pop(sid)
        # Flush remaining batch.
        if info["batch"] and self.net:
            self.net.send({"type": "stroke_points", "op_id": str(uuid.uuid4()),
                           "stroke_id": sid, "points": info["batch"]})
        if self.net:
            self.net.send({"type": "stroke_end", "op_id": str(uuid.uuid4()), "stroke_id": sid})
        self.state.end_drawing(self.username)

    def _on_hover(self, event):
        # Keep cursor updates light; handled in motion.
        pass

    # ------------------------------------------------------------------ #
    # Polling and message handling
    # ------------------------------------------------------------------ #
    def _poll(self) -> None:
        if self.net is None:
            return
        try:
            try:
                while True:
                    msg = self.net.inbox.get_nowait()
                    self._handle_message(msg)
            except queue.Empty:
                pass

            # Prune stale drawers and blink.
            before = set(self.state.active_drawers)
            self.state.prune_drawers()
            changed = before != set(self.state.active_drawers)
            self._blink = not self._blink if self.state.active_drawers else False
            if changed or self.state.active_drawers:
                self._rebuild_participants()

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
                        if self.role != "host":
                            if hasattr(self, "clear_btn"):
                                self.clear_btn.state(["disabled"])
                            if hasattr(self, "save_btn"):
                                self.save_btn.state(["disabled"])
                            if hasattr(self, "load_btn"):
                                self.load_btn.state(["disabled"])
                        else:
                            if hasattr(self, "clear_btn"):
                                self.clear_btn.state(["!disabled"])
                            if hasattr(self, "save_btn"):
                                self.save_btn.state(["!disabled"])
                            if hasattr(self, "load_btn"):
                                self.load_btn.state(["!disabled"])
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
        if mtype in ("stroke_start", "stroke_points", "stroke_end", "delete_object"):
            op_id = msg.get("op_id")
            if self.state.is_pending(op_id):
                self.state.clear_pending(op_id)
                # Already rendered locally; just track seq.
                seq = msg.get("seq")
                if isinstance(seq, int) and not isinstance(seq, bool):
                    if seq > self.state.applied_seq:
                        self.state.applied_seq = seq
                return
            status = self.state.apply_op(msg)
            if status == "gap":
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
                return
            # Render remote stroke updates.
            self._render_op(msg)
            return
        if mtype == "snapshot":
            self.state.apply_snapshot(msg)
            self._redraw_all()
            return
        if mtype == "ops_since":
            ops = msg.get("ops", [])
            if isinstance(ops, list):
                self.state.apply_ops_since(int(msg.get("from_seq", self.state.applied_seq)), ops)
                # Redraw to be safe.
                self._redraw_all()
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
                        self._add_stroke_item(obj)
            return
        if mtype == "redo":
            action = msg.get("action")
            if action == "add_stroke":
                obj = msg.get("object")
                if isinstance(obj, dict) and "id" in obj:
                    self.state.objects[obj["id"]] = obj
                    self._add_stroke_item(obj)
            elif action == "remove_stroke":
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
            self.canvas.delete("all")
            self.canvas_items.clear()
            self.state.objects.clear()
            self.state.clear_drawers()
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
                if hasattr(self, "status_latency"):
                    self.status_latency.config(text=f"latency: {int(rtt)} ms")
            return
        if mtype == "error":
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
            item = self.canvas.create_line(x, y, x, y, fill=color, width=width,
                                           capstyle="round", smooth=True, tags=(sid,))
            self.canvas_items[sid] = item
        elif mtype == "stroke_points":
            sid = op.get("stroke_id")
            item = self.canvas_items.get(sid) if isinstance(sid, str) else None
            obj = self.state.objects.get(sid) if isinstance(sid, str) else None
            if item is None:
                return
            if obj is not None:
                flat = [c for pt in obj.get("points", []) for c in pt]
                if len(flat) >= 4:
                    self.canvas.coords(item, *flat)
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
        flat = [c for pt in pts for c in pt]
        color = obj.get("color", "#000000")
        width = obj.get("width", 2)
        item = self.canvas.create_line(*flat, fill=color, width=width,
                                       capstyle="round", smooth=True, tags=(sid,))
        self.canvas_items[sid] = item

    def _draw_board_background(self) -> None:
        self.canvas.delete("board_bg")
        x0, y0 = logical_to_canvas(0, 0, self.offset_x, self.offset_y, self.scale)
        x1, y1 = logical_to_canvas(1600, 900, self.offset_x, self.offset_y, self.scale)
        self.canvas.create_rectangle(x0, y0, x1, y1, fill=self.theme["surface"],
                                     outline=self.theme["border"], width=1, tags=("board_bg",))
        self.canvas.tag_lower("board_bg")

    def _redraw_all(self) -> None:
        self.canvas.delete("all")
        self._draw_board_background()
        self.canvas_items.clear()
        for sid, obj in self.state.objects.items():
            pts = obj.get("points", [])
            if not isinstance(pts, list) or len(pts) < 1:
                continue
            if len(pts) == 1:
                pts = [pts[0], pts[0]]
            flat = [c for pt in pts for c in pt]
            color = obj.get("color", "#000000")
            width = obj.get("width", 2)
            item = self.canvas.create_line(*flat, fill=color, width=width,
                                           capstyle="round", smooth=True, tags=(sid,))
            self.canvas_items[sid] = item
        # Redraw cursors on top.
        for user, item in list(self.cursor_items.items()):
            if isinstance(item, tuple):
                for it in item:
                    self.canvas.tag_raise(it)
            else:
                self.canvas.tag_raise(item)

    # ------------------------------------------------------------------ #
    # Participants and chat
    # ------------------------------------------------------------------ #
    def _rebuild_participants(self) -> None:
        if not hasattr(self, "participants_frame"):
            return
        for w in self.participants_frame.winfo_children():
            w.destroy()
        if hasattr(self, "host_target"):
            names = [u.get("name", "") for u in self.state.participants if u.get("name") != self.username]
            self.host_target["values"] = names
            if self.host_target.get() not in names:
                self.host_target.set("")
            self._update_host_buttons()
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
            dot.create_oval(2, 2, 10, 10, fill=color, outline="")
            role = u.get("role", "editor")
            suffix = " (you)" if name == self.username else ""
            status = "" if online else " (offline)"
            active = name in self.state.active_drawers
            glyph = " \u270e" if active else ""
            if active and self._blink:
                glyph = " \u270f"
            lbl = ttk.Label(row, text=f"{name}{suffix}  [{role}]{status}{glyph}")
            lbl.pack(side="left")
            if active:
                lbl.config(foreground=color)
            if not online:
                lbl.config(foreground=self.theme["ink_muted"])

    def _append_chat(self, user, text, timestamp):
        self.chat_text.config(state="normal")
        try:
            ts = time.strftime("%H:%M", time.localtime(timestamp)) if timestamp else ""
        except (TypeError, ValueError, OSError):
            ts = ""
        color = "#1E5B6B"
        for u in self.state.participants:
            if u.get("name") == user:
                color = u.get("color", color)
        name_tag = f"name_{user}"
        self.chat_text.tag_configure(name_tag, foreground=color, font=("Segoe UI", 10, "bold"))
        self.chat_text.tag_configure("time_tag", foreground=self.theme["ink_muted"], font=("Segoe UI", 9))
        self.chat_text.tag_configure("body_tag", foreground=self.theme["ink"], font=("Segoe UI", 10))
        self.chat_text.insert("end", f"{user}", name_tag)
        if ts:
            self.chat_text.insert("end", f"  {ts}", "time_tag")
        self.chat_text.insert("end", "\n")
        self.chat_text.insert("end", f"{text}\n", "body_tag")
        self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    def _send_chat(self):
        text = self.chat_entry.get().strip()
        if not text or not self.net:
            return
        self.chat_entry.delete(0, "end")
        self.net.send({"type": "chat", "text": text})

    def _undo(self):
        if self.net:
            self.net.send({"type": "undo", "op_id": str(uuid.uuid4())})

    def _redo(self):
        if self.net:
            self.net.send({"type": "redo", "op_id": str(uuid.uuid4())})

    def _clear_canvas(self):
        if self.net and self.role == "host":
            self.net.send({"type": "clear_canvas", "op_id": str(uuid.uuid4())})

    def _save_board(self):
        if not self.net or self.role != "host":
            return
        name = tk.simpledialog.askstring("Save board", "Board name:", parent=self.root)
        if name:
            self.net.send({"type": "save_board", "name": name})

    def _load_board(self):
        if not self.net or self.role != "host":
            return
        name = tk.simpledialog.askstring("Load board", "Board name:", parent=self.root)
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
        self.status_conn.config(text=text)
        self.root.after(3000, lambda: self.status_conn.config(
            text="Connected" if (self.net and self.net.connected) else "Disconnected"))

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
        cx, cy = (x * self.scale + self.offset_x, y * self.scale + self.offset_y)
        color = "#888888"
        for u in self.state.participants:
            if u.get("name") == user:
                color = u.get("color", color)
        active = user in self.state.active_drawers
        item = self.cursor_items.get(user)
        if item is None:
            if active:
                oval = self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill=color, outline=color, tags=("cursor",))
            else:
                oval = self.canvas.create_oval(cx - 4, cy - 4, cx + 4, cy + 4, fill="", outline=color, tags=("cursor",))
            text = self.canvas.create_text(cx + 8, cy, text=user, fill=color, font=("Segoe UI", 9, "bold"), anchor="w", tags=("cursor",))
            self.cursor_items[user] = (oval, text)
        else:
            oval, text = item
            if active:
                self.canvas.itemconfig(oval, fill=color, outline=color)
            else:
                self.canvas.itemconfig(oval, fill="", outline=color)
            self.canvas.coords(oval, cx - 4, cy - 4, cx + 4, cy + 4)
            self.canvas.coords(text, cx + 8, cy)

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def _update_status(self):
        if self.net is None:
            return
        self.status_conn.config(text=f"Connected | room {self.room_code}")
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
        if hasattr(self, "status_activity"):
            self.status_activity.config(text=activity)
        self.status_seq.config(text=f"seq: {self.state.applied_seq}")


class Tooltip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self.tip = None
        widget.bind("<Enter>", self.show)
        widget.bind("<Leave>", self.hide)

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