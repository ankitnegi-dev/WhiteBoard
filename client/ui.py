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
    from tkinter import ttk, colorchooser, messagebox
except ImportError as exc:  # pragma: no cover - handled in main.py
    raise

from client.net import NetworkClient
from client.state import BoardState, canvas_to_logical, compute_scale
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
        self.root.geometry("1200x760")
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
        self.width = 2.0
        self.canvas_items: dict[str, int] = {}   # stroke_id -> canvas item id
        self.cursor_items: dict[str, int] = {}   # username -> marker item
        self.local_strokes: dict[str, dict[str, Any]] = {}
        self._last_cursor_send = 0.0

        # Scaling state for the logical 1600x900 canvas.
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0

        self._show_connect()

    # ------------------------------------------------------------------ #
    # Connect screen
    # ------------------------------------------------------------------ #
    def _show_connect(self) -> None:
        self.connect_frame = ttk.Frame(self.root, padding=24)
        self.connect_frame.pack(fill="both", expand=True)

        title = ttk.Label(self.connect_frame, text="LAN Collaborative Whiteboard", font=("Segoe UI", 20, "bold"))
        title.pack(pady=(0, 16))

        form = ttk.Frame(self.connect_frame)
        form.pack()

        self._add_field(form, "Server IP", "host", "127.0.0.1", 0)
        self._add_field(form, "Port", "port", "5050", 1)
        self._add_field(form, "Username", "username", "", 2)
        self._add_field(form, "Room code", "room_code", "", 3)
        self._add_field(form, "Password", "password", "", 4, show="*")

        btns = ttk.Frame(form)
        btns.grid(row=5, column=0, columnspan=2, pady=(16, 0))
        ttk.Button(btns, text="Create room", command=lambda: self._start_connect("create")).pack(side="left", padx=4)
        ttk.Button(btns, text="Join room", command=lambda: self._start_connect("join")).pack(side="left", padx=4)

        self.connect_status = ttk.Label(self.connect_frame, text="", foreground="#B83A2E")
        self.connect_status.pack(pady=(12, 0))

    def _add_field(self, parent, label, key, default, row, show=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 8))
        entry = ttk.Entry(parent, width=28, show=show) if show else ttk.Entry(parent, width=28)
        entry.insert(0, default)
        entry.grid(row=row, column=1, pady=4)
        setattr(self, f"entry_{key}", entry)

    def _start_connect(self, mode: str) -> None:
        host = self.entry_host.get().strip()
        port_text = self.entry_port.get().strip()
        username = self.entry_username.get().strip()
        room_code = self.entry_room_code.get().strip()
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
        elif mtype == "join_ok":
            self.room_code = resp.get("room_code", self.entry_room_code.get().strip().upper())
            # room code may not be in join_ok; keep from entry
            if not self.room_code:
                self.room_code = self.entry_room_code.get().strip().upper()
            self.role = resp.get("role", "editor")
            self.state.apply_snapshot(resp.get("snapshot", {}))
            if isinstance(resp.get("chat_history"), list):
                self.state.apply_chat_history(resp["chat_history"])
            if isinstance(resp.get("participants"), list):
                self.state.update_participants(resp["participants"])
            # Track pending none.

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

        self._tool_button("Pen", "pen")
        self._tool_button("Eraser", "eraser")
        ttk.Separator(self.toolbar, orient="horizontal").pack(fill="x", pady=8)

        self.color_btn = ttk.Button(self.toolbar, text="Color", command=self._pick_color)
        self.color_btn.pack(fill="x", pady=4)
        self.color_swatch = tk.Label(self.toolbar, text="  ", bg=self.color, width=4, relief="solid")
        self.color_swatch.pack(pady=4)

        ttk.Label(self.toolbar, text="Width").pack(pady=(8, 0))
        self.width_scale = ttk.Scale(self.toolbar, from_=1, to=20, orient="vertical", command=self._on_width)
        self.width_scale.set(self.width)
        self.width_scale.pack(fill="y", expand=True, pady=4)

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

        # Status bar.
        self.status_bar = ttk.Frame(self.root, padding=(8, 2))
        self.status_bar.pack(side="bottom", fill="x")
        self.status_conn = ttk.Label(self.status_bar, text="Connecting")
        self.status_conn.pack(side="left", padx=4)
        self.status_room = ttk.Label(self.status_bar, text="")
        self.status_room.pack(side="left", padx=12)
        self.status_latency = ttk.Label(self.status_bar, text="latency: -")
        self.status_latency.pack(side="right", padx=4)
        self.status_seq = ttk.Label(self.status_bar, text="seq: 0")
        self.status_seq.pack(side="right", padx=4)

        # Keyboard shortcuts (Control and Command).
        for mod in ("<Control-", "<Command-"):
            self.root.bind(f"{mod}p>", lambda _e: self._set_tool("pen"))
            self.root.bind(f"{mod}e>", lambda _e: self._set_tool("eraser"))
            self.root.bind(f"{mod}t>", lambda _e: self._toggle_theme())
        self.root.bind("<Escape>", lambda _e: self._set_tool("pen"))

        # Mouse wheel for width (cross-platform).
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-4>", lambda e: self._adjust_width(1))
        self.canvas.bind("<Button-5>", lambda e: self._adjust_width(-1))

    def _tool_button(self, label, tool):
        btn = ttk.Button(self.toolbar, text=label, command=lambda: self._set_tool(tool))
        btn.pack(fill="x", pady=4)

    def _set_tool(self, tool):
        self.tool = tool
        self.status_conn.config(text=f"Tool: {tool}")

    def _pick_color(self):
        res = colorchooser.askcolor(color=self.color, parent=self.root)
        if res and res[1]:
            self.color = res[1]
            self.color_swatch.config(bg=self.color)

    def _on_width(self, _val):
        try:
            self.width = float(self.width_scale.get())
        except (TypeError, ValueError):
            pass

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
        style.configure("TEntry", fieldbackground=t["surface"], foreground=t["ink"])
        style.configure("TScale", background=t["bg"])
        self.root.configure(bg=t["bg"])
        self.canvas.configure(bg=t["canvas"])

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
            if self.net:
                self.net.send({"type": "stroke_start", "op_id": op_id, "stroke_id": sid,
                               "tool": "pen", "color": self.color, "width": self.width,
                               "x": lx, "y": ly})
        elif self.tool == "eraser":
            sid = find_stroke_at(self.state.objects, lx, ly)
            if sid and self.net:
                self.net.send({"type": "delete_object", "op_id": str(uuid.uuid4()), "target_id": sid})

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
                info["batch"] = []
                info["last_send"] = now
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
            while True:
                msg = self.net.inbox.get_nowait()
                self._handle_message(msg)
        except queue.Empty:
            pass
        self.status_seq.config(text=f"seq: {self.state.applied_seq}")
        self.root.after(10, self._poll)

    def _handle_message(self, msg: dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "_disconnected":
            self.status_conn.config(text=f"Disconnected: {msg.get('reason')}")
            return
        if mtype == "participant_list":
            users = msg.get("users", [])
            if isinstance(users, list):
                self.state.update_participants(users)
                self._rebuild_participants()
            return
        if mtype in ("user_joined", "user_left", "user_disconnected", "user_reconnected"):
            # participant_list usually follows; nothing else needed.
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
        if mtype == "error":
            self.status_conn.config(text=f"Error: {msg.get('message', msg.get('code'))}")
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

    def _redraw_all(self) -> None:
        self.canvas.delete("all")
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
            self.canvas.tag_raise(item)

    # ------------------------------------------------------------------ #
    # Participants and chat
    # ------------------------------------------------------------------ #
    def _rebuild_participants(self) -> None:
        for w in self.participants_frame.winfo_children():
            w.destroy()
        for u in self.state.participants:
            row = ttk.Frame(self.participants_frame)
            row.pack(fill="x", pady=2)
            dot = tk.Canvas(row, width=12, height=12, highlightthickness=0, bg=self.theme["bg"])
            dot.pack(side="left", padx=(0, 6))
            color = u.get("color", "#888888")
            dot.create_oval(2, 2, 10, 10, fill=color, outline="")
            name = u.get("name", "?")
            role = u.get("role", "editor")
            online = u.get("online", True)
            status = "" if online else " (offline)"
            lbl = ttk.Label(row, text=f"{name}  [{role}]{status}")
            lbl.pack(side="left")

    def _append_chat(self, user, text, timestamp):
        self.chat_text.config(state="normal")
        try:
            ts = time.strftime("%H:%M", time.localtime(timestamp)) if timestamp else ""
        except (TypeError, ValueError, OSError):
            ts = ""
        self.chat_text.insert("end", f"{user}  {ts}\n", "name")
        self.chat_text.insert("end", f"  {text}\n")
        self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    def _send_chat(self):
        text = self.chat_entry.get().strip()
        if not text or not self.net:
            return
        self.chat_entry.delete(0, "end")
        self.net.send({"type": "chat", "text": text})

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
        item = self.cursor_items.get(user)
        if item is None:
            item = self.canvas.create_text(cx, cy, text=user, fill=color, font=("Segoe UI", 9, "bold"), tags=("cursor",))
            self.cursor_items[user] = item
        else:
            self.canvas.coords(item, cx, cy)

    # ------------------------------------------------------------------ #
    # Status
    # ------------------------------------------------------------------ #
    def _update_status(self):
        if self.net is None:
            return
        self.status_conn.config(text="Connected" if self.net.connected else "Disconnected")
        self.status_room.config(text=f"room: {self.room_code}")