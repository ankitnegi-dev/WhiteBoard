# LAN Collaborative Whiteboard Protocol

Source of truth for the wire format. Derived from docs/ROADMAP.md sections
4 and 5. If a change is needed, update this document first, then code.

UI is tkinter-based (tk.Canvas + ttk). No web or HTML UI.

## 1. Framing

TCP is a byte stream with no message boundaries. Every message is framed:

    [4-byte big-endian unsigned length N][N bytes of UTF-8 JSON]

- Length prefix uses struct.pack(">I", n).
- recv_exact(sock, n) loops until n bytes arrive.
- Reject messages larger than MAX_MESSAGE_SIZE (1 MB) before reading the
  body. Image uploads in a later milestone may raise this or use chunking.
- send_msg and recv_msg live in protocol.py and are shared by both sides.

## 2. Message envelope

Every message is a JSON object with a "type" field. Server-stamped ops
also carry "seq", "user", and "ts".

Example:

    {"type": "stroke_points", "op_id": "uuid", "stroke_id": "uuid",
     "points": [[10, 20], [12, 25]]}

## 3. Message catalogue

### Connection and session

- create_room {username, password?} -> room_created {room_code, token, role: host}
- join_room {username, room_code, password?} -> join_ok {token, role, user_color, participants, snapshot, seq, chat_history} or join_denied {reason, message}. Reasons: room_not_found, room_locked, username_taken, wrong_password, bad_request
- reconnect {token, room_code, last_seq} -> reconnect_ok {ops_since or snapshot, seq} or reconnect_denied {reason, message}. Reasons: invalid_token, token_expired, room_gone, kicked, bad_request
- leave
- ping / pong (heartbeat; pong echoes the ping's ts for RTT)
- error {code, message}

### Participants

- user_joined, user_left, user_disconnected, user_reconnected, kicked
- participant_list {users: [{name, color, role, online}]}
- role_change {target, role} (host only)
- kick {target} (host only)
- lock_room {locked} (host only)
- cursor_move {x, y} (throttled to about 20 per second, not logged)

### Drawing ops (go into the op log)

- stroke_start {op_id, stroke_id, tool, color, width, x, y}
- stroke_points {op_id, stroke_id, points: [[x, y], ...]} (batched every 30-50 ms)
- stroke_end {op_id, stroke_id}
- shape {op_id, shape_id, kind: SHAPE_KINDS, p1, p2, color, width, fill?}
- text {op_id, obj_id, x, y, text, size, color}
- sticky {op_id, obj_id, x, y, text, color}; fixed 220x160 logical size;
  text length 1..300; color one of the five sticky note colors.
- edit_object {op_id, target_id, text}; text length 1..300; edits a sticky's
  text. Last write wins by server seq.
- move_object {op_id, target_id, x, y}; moves a sticky. Last write wins by
  server seq.
- delete_object {op_id, target_id} (object eraser)
- clear_canvas {op_id} (host or permitted role only)
- undo {op_id} / redo {op_id}

### State sync

- snapshot {seq, objects, chat_tail}
- ops_since {from_seq, ops: [...]}
- resync_request {last_seq}

### Chat

- chat {text} -> broadcast {user, text, timestamp, seq_chat}

### Persistence and extras

- save_board {name}, load_board {name}, list_boards, boards_list
- export_request, version_save {label}, version_restore {label}

## 4. Example flow: a stroke

1. Client draws locally right away (optimistic rendering).
2. Client sends stroke_start, several stroke_points, then stroke_end.
3. Server checks permission, dedupes by op_id, assigns the next seq,
   appends to the log, and broadcasts the stamped op to all clients in
   the room.
4. Other clients render it. The origin client matches the echo by op_id
   and does not draw it twice.

## 5. Ordering, reliability, and concurrency

- Total order: only the server assigns seq. Lock the room, increment seq,
  append to the log, release. Every client applies ops in seq order.
- Duplicates: each op has a client-generated op_id (UUID). The server
  keeps a set of seen op_ids per room and silently ignores repeats (for
  example after a resend on reconnection). Clients also ignore a seq they
  already applied.
- Missing or out-of-order: the client tracks last_applied_seq. If it
  receives seq greater than last+1, it buffers the op and sends
  resync_request(last_applied_seq). The server replies with ops_since, or
  a snapshot if the gap is too large.
- Delayed ops: because the server order is authoritative, a late op is
  applied at its stamped position. Strokes are independent objects, so
  overlap order is deterministic: later seq draws on top.
- Concurrent edits: two users drawing at once produce interleaved ops,
  which is fine because each stroke has its own stroke_id. Deleting an
  object that is already deleted is a no-op.
- Thread safety: per-room threading.Lock for sequencing; a per-client send
  lock (or per-client send queue with a writer thread) so two threads never
  interleave bytes on one socket.
- Backpressure: a slow client must not stall the room. Use a bounded send
  queue per client; if it overflows, drop the client into resync mode.

### Reconnection and grace period

- A disconnected user stays in the room as offline for a configurable grace
  period (default 120 s). Their token, role, color and undo/redo stacks are
  reserved during that time.
- reconnect validates the token. On success the same username, color, role and
  undo/redo stacks are restored, the user is marked online again, and the
  server broadcasts user_reconnected and participant_list.
- reconnect_ok carries ops_since when last_seq is within MAX_OPS_SINCE of the
  current seq and the log was not compacted past it; otherwise it carries a
  snapshot (plus chat_tail) so the client can rebuild from scratch.
- A second connection that presents the same token while the first socket is
  still half-open replaces the first socket cleanly; the first socket is
  closed and its handler exits without marking the user offline.
- After the grace period the slot is freed and the token expires; a later
  reconnect is denied with token_expired.
- If a client's send queue overflows, that client is put into resync mode:
  further broadcasts may be dropped for it, and it is expected to send
  resync_request (or reconnect) to catch up. Other clients are not stalled.

### Undo and redo semantics

- Undo is per user: it reverts the calling user's most recent active
  operation. It never undoes someone else's work.
- A stroke is one undoable unit (start through end).
- Undo is logged as a new op (undo referencing the target op or object),
  so all clients stay in sync and the log stays append-only.
- Redo re-applies the last undone op of that user. Any new drawing action
  by that user clears their redo stack.
- If another user has since deleted the target object, undo becomes a
  no-op and the user is told.
- clear_canvas by the host can itself be undone by the host (restore the
  cleared objects as one unit).
- Undo and redo of sticky create, edit and move are per author. Undo restores
  the previous text or position. If the sticky was deleted by someone else in
  the meantime, undo is a no-op with a notice.
- Undo and redo stacks are kept server-side per user so they survive a
  client restart within the session.

## 6. Validation (server-side, never trust the client)

- Message must parse, have a known type, and required fields with the right
  types.
- Coordinates inside the logical canvas (0..1600 by 0..900); width between
  1 and 50; color as #rrggbb; text length <= 500; point batch <= 500;
  chat text <= 500.
- shape: kind is one of the SHAPE_KINDS whitelist defined in protocol.py
  (basic, flowchart and callout shapes, plus the legacy line, rect, ellipse
  and arrow kinds); p1 and p2 are [x, y] inside the canvas; width 1..50;
  color #rrggbb; fill is null or a #rrggbb color. Old kinds keep working so
  saved boards still load.
- sticky: obj_id string; x, y inside the canvas; text length 1..300; color one
  of the five sticky note colors. The note has a fixed 220x160 logical size.
- edit_object: op_id, target_id string; text length 1..300. Target must be an
  existing sticky. Last write wins by server seq.
- move_object: op_id, target_id string; x, y inside the canvas. Target must be
  an existing sticky. Last write wins by server seq.
- undo of create, edit or move of a sticky by its author restores the previous
  state. If another user deleted the sticky in the meantime, undo is a no-op
  and the caller is told "target already removed".
- Sender must be a member with a role that allows the op (viewers cannot
  draw; only host can clear or change roles).
- Rate limit per client (for example max ops per second) to prevent
  flooding.

## 7. Custom exceptions (protocol.py)

- ConnectionClosed: peer closed mid-frame (clean EOF).
- FrameTooLarge: declared length exceeds MAX_MESSAGE_SIZE.
- BadMessage: invalid JSON or top-level value is not an object.
