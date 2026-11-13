"""Per-connection handler thread.

Day 5 replaces per-client watchdog threads with one monitor thread scanning
all clients, adds reconnect, grace periods, rate limiting and a bounded send
queue that drops an overflowing client into resync mode instead of stalling
the room.
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from typing import Any, Optional

from protocol import BadMessage, ConnectionClosed, FrameTooLarge, recv_msg, send_msg
from server.auth import new_token
from server.room import Participant
from server.room_manager import RoomManager
from server import persistence
from server.auth import LoginLimiter

_LOGIN_LIMITER = LoginLimiter()

# Shared registry for the whole server. Tests may inject their own.
_DEFAULT_ROOM_MANAGER = RoomManager()

# Registry of live client records for the single monitor thread.
_ACTIVE_CLIENTS: dict[int, dict[str, Any]] = {}
_ACTIVE_CLIENTS_LOCK = threading.Lock()


def register_client(record: dict[str, Any]) -> None:
    with _ACTIVE_CLIENTS_LOCK:
        _ACTIVE_CLIENTS[id(record)] = record


def unregister_client(record: dict[str, Any]) -> None:
    with _ACTIVE_CLIENTS_LOCK:
        _ACTIVE_CLIENTS.pop(id(record), None)


def monitor_clients(heartbeat_interval: float, heartbeat_timeout: float,
                    stop_event: Optional[threading.Event] = None) -> None:
    """Single watchdog thread scanning every client for heartbeat timeouts.

    Design note: one thread for all clients is preferred over per-client
    watchdogs because it scales with the number of clients without creating
    a thread per connection, it centralises ping scheduling so intervals stay
    consistent, and a slow scan cannot be starved by one stuck handler.
    """
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        time.sleep(0.5)
        now = time.time()
        with _ACTIVE_CLIENTS_LOCK:
            records = list(_ACTIVE_CLIENTS.values())
        for rec in records:
            try:
                if rec["stop_event"].is_set():
                    continue
                last_seen = rec.get("last_seen", now)
                last_ping = rec.get("last_ping", now)
                if now - last_seen > heartbeat_timeout:
                    logging.getLogger("server.client_handler").info(
                        "heartbeat timeout for %s", rec.get("peer"))
                    rec["stop_event"].set()
                    try:
                        rec["conn"].close()
                    except OSError:
                        pass
                    continue
                if now - last_ping >= heartbeat_interval:
                    rec["last_ping"] = now
                    try:
                        rec["enqueue"]({"type": "ping"})
                    except Exception:
                        pass
            except Exception:
                continue


def _get_room_manager(room_manager: Optional[RoomManager]) -> RoomManager:
    return room_manager if room_manager is not None else _DEFAULT_ROOM_MANAGER


def _send_now(conn: socket.socket, lock: threading.Lock, msg: dict[str, Any]) -> bool:
    try:
        with lock:
            send_msg(conn, msg)
        return True
    except (ConnectionClosed, OSError, ValueError):
        return False


def handle_client(
    conn: socket.socket,
    addr: tuple[str, int],
    server_logger: logging.Logger,
    heartbeat_interval: float = 5.0,
    heartbeat_timeout: float = 15.0,
    room_manager: Optional[RoomManager] = None,
    rate_limit: float = 60.0,
) -> None:
    """Handle one client connection for its whole lifetime."""
    peer = f"{addr[0]}:{addr[1]}"
    log = server_logger.getChild(f"handler.{peer}")
    rm = _get_room_manager(room_manager)

    send_lock = threading.Lock()
    send_queue: queue.Queue = queue.Queue(maxsize=100)
    stop_event = threading.Event()

    state: dict[str, Any] = {
        "joined": False,
        "left_cleanly": False,
        "replaced": False,
        "resync_mode": False,
        "username": None,
        "room_code": None,
        "role": None,
        "color": None,
        "token": None,
        "last_seen": time.time(),
        "last_ping": time.time(),
        "kicked": False,
        "op_times": [],
        "rate_limit": rate_limit,
    }

    def do_kick():
        stop_event.set()
        try:
            conn.close()
        except OSError:
            pass

    state["kick"] = do_kick

    def enqueue(msg: dict[str, Any]) -> None:
        if stop_event.is_set():
            return
        try:
            send_queue.put(msg, timeout=0.1)
        except queue.Full:
            # Put this client into resync mode instead of stalling the room.
            state["resync_mode"] = True
            log.warning("send queue full for %s; client put into resync mode", peer)

    def writer() -> None:
        while not stop_event.is_set():
            try:
                msg = send_queue.get(timeout=0.5)
            except queue.Empty:
                continue
            if msg is None:
                break
            if not _send_now(conn, send_lock, msg):
                stop_event.set()
                break

    writer_thread = threading.Thread(target=writer, name=f"writer-{peer}", daemon=True)
    writer_thread.start()

    client_record = {
        "peer": peer,
        "state": state,
        "enqueue": enqueue,
        "conn": conn,
        "stop_event": stop_event,
        "last_seen": state["last_seen"],
        "last_ping": state["last_ping"],
    }
    register_client(client_record)

    def _rate_ok() -> bool:
        now = time.time()
        window = [t for t in state["op_times"] if now - t < 1.0]
        state["op_times"] = window
        if len(window) >= int(max(1, rate_limit)):
            return False
        state["op_times"].append(now)
        return True

    log.info("handler started")
    try:
        conn.settimeout(None)
        while not stop_event.is_set():
            try:
                msg = recv_msg(conn)
            except ConnectionClosed:
                log.info("%s disconnected (closed connection)", peer)
                break
            except ConnectionResetError:
                log.info("%s disconnected (connection reset)", peer)
                break
            except ConnectionAbortedError:
                log.info("%s disconnected (connection aborted)", peer)
                break
            except BrokenPipeError:
                log.info("%s disconnected (broken pipe)", peer)
                break
            except FrameTooLarge as exc:
                log.warning("rejected oversize frame from %s: %s", peer, exc)
                enqueue({"type": "error", "code": "frame_too_large", "message": str(exc)})
                break
            except BadMessage as exc:
                log.warning("rejected bad message from %s: %s", peer, exc)
                enqueue({"type": "error", "code": "bad_message", "message": str(exc)})
                break
            except OSError as exc:
                log.info("%s disconnected (%s)", peer, exc)
                break
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("unexpected error from %s: %s", peer, exc)
                break

            now = time.time()
            state["last_seen"] = now
            client_record["last_seen"] = now
            mtype = msg.get("type") if isinstance(msg, dict) else None
            if not isinstance(msg, dict) or not isinstance(mtype, str):
                enqueue({"type": "error", "code": "bad_message", "message": "message must be an object with a string type"})
                continue

            if not state["joined"]:
                if mtype == "create_room":
                    _handle_create_room(msg, state, rm, enqueue, log, peer)
                elif mtype == "join_room":
                    _handle_join_room(msg, state, rm, enqueue, log, peer)
                elif mtype == "reconnect":
                    _handle_reconnect(msg, state, rm, enqueue, log, peer)
                else:
                    enqueue({"type": "error", "code": "not_joined", "message": "send create_room or join_room first"})
                continue

            # Joined state.
            room = rm.get(state["room_code"]) if state["room_code"] else None
            if room is None:
                enqueue({"type": "error", "code": "room_gone", "message": "room no longer exists"})
                break

            # Rate limiting for ops.
            op_types = {"stroke_start", "stroke_points", "stroke_end", "delete_object",
                        "chat", "undo", "redo", "clear_canvas", "cursor_move",
                        "shape", "text", "sticky", "edit_object", "move_object"}
            if mtype in op_types and not _rate_ok():
                enqueue({"type": "error", "code": "rate_limited", "message": "too many ops per second"})
                continue

            if mtype == "leave":
                state["left_cleanly"] = True
                _handle_leave(state, rm, log)
                break
            elif mtype in ("pong", "ping"):
                if mtype == "ping":
                    enqueue({"type": "pong", "ts": msg.get("ts")})
                continue
            elif mtype == "resync_request":
                last_seq = msg.get("last_seq")
                result = room.ops_since(last_seq)
                if result is None:
                    enqueue({"type": "snapshot", **room.snapshot()})
                else:
                    enqueue({"type": "ops_since", "from_seq": result["from_seq"], "ops": result["ops"]})
                continue
            elif mtype == "cursor_move":
                x = msg.get("x")
                y = msg.get("y")
                if isinstance(x, (int, float)) and isinstance(y, (int, float)) and not isinstance(x, bool) and not isinstance(y, bool):
                    room.broadcast(
                        {"type": "cursor_move", "user": state["username"], "x": x, "y": y},
                        exclude=state["username"],
                    )
                continue
            elif mtype in ("stroke_start", "stroke_points", "stroke_end", "delete_object",
                           "shape", "text", "sticky", "edit_object", "move_object"):
                status, op, reason = room.apply_op(msg, state["username"], state["role"])
                if status == "duplicate":
                    continue
                if status != "ok" or op is None:
                    enqueue({"type": "error", "code": "rejected_op", "message": reason})
                    continue
                room.broadcast(op)
                continue
            elif mtype == "chat":
                ok, entry, reason = room.apply_chat(msg, state["username"])
                if not ok or entry is None:
                    enqueue({"type": "error", "code": "rejected_chat", "message": reason})
                    continue
                room.broadcast(
                    {
                        "type": "chat",
                        "user": entry["user"],
                        "text": entry["text"],
                        "timestamp": entry["timestamp"],
                        "seq_chat": entry["seq_chat"],
                    }
                )
                continue
            elif mtype == "undo":
                status, op, reason = room.undo(msg.get("op_id"), state["username"])
                if status == "duplicate":
                    continue
                if status != "ok" or op is None:
                    enqueue({"type": "error", "code": "rejected_undo", "message": reason or status})
                    continue
                room.broadcast(op)
                continue
            elif mtype == "redo":
                status, op, reason = room.redo(msg.get("op_id"), state["username"])
                if status == "duplicate":
                    continue
                if status != "ok" or op is None:
                    enqueue({"type": "error", "code": "rejected_redo", "message": reason or status})
                    continue
                room.broadcast(op)
                continue
            elif mtype == "board_hash_request":
                seq, digest = room.board_hash()
                enqueue({"type": "board_hash", "seq": seq, "hash": digest})
                continue
            elif mtype == "clear_canvas":
                if state.get("role") != "host":
                    enqueue({"type": "error", "code": "forbidden", "message": "only the host can clear the canvas"})
                    continue
                status, op, reason = room.clear_canvas(msg.get("op_id"), state["username"])
                if status == "duplicate":
                    continue
                if status != "ok" or op is None:
                    enqueue({"type": "error", "code": "rejected_clear", "message": reason or status})
                    continue
                room.dirty = True
                room.broadcast(op)
                continue
            elif mtype == "role_change":
                if state.get("role") != "host":
                    enqueue({"type": "error", "code": "forbidden", "message": "only the host can change roles"})
                    continue
                target = msg.get("target")
                new_role = msg.get("role")
                if not isinstance(target, str) or target not in room.participants:
                    enqueue({"type": "error", "code": "bad_request", "message": "unknown target"})
                    continue
                if new_role not in ("host", "editor", "viewer"):
                    enqueue({"type": "error", "code": "bad_request", "message": "invalid role"})
                    continue
                with room.lock:
                    p = room.participants[target]
                    p.role = new_role
                    if p.state is not None:
                        p.state["role"] = new_role
                    room.broadcast({"type": "participant_list", "users": room.participant_list()})
                continue
            elif mtype == "kick":
                if state.get("role") != "host":
                    enqueue({"type": "error", "code": "forbidden", "message": "only the host can kick"})
                    continue
                target = msg.get("target")
                with room.lock:
                    p = room.participants.get(target) if isinstance(target, str) else None
                    if p is None:
                        enqueue({"type": "error", "code": "bad_request", "message": "unknown target"})
                        continue
                    p.kicked = True
                    room.participants.pop(target, None)
                    if p.token:
                        room.kicked_tokens.add(p.token)
                    room.broadcast({"type": "user_left", "username": target})
                    room.broadcast({"type": "participant_list", "users": room.participant_list()})
                if p.kick is not None:
                    p.kick()
                continue
            elif mtype == "remove_participant":
                if state.get("role") != "host":
                    enqueue({"type": "error", "code": "forbidden", "message": "only the host can remove participants"})
                    continue
                target = msg.get("target")
                if not isinstance(target, str) or not target.strip():
                    enqueue({"type": "error", "code": "bad_request", "message": "target required"})
                    continue
                target = target.strip()
                ok, reason = room.remove_participant(target)
                if not ok:
                    enqueue({"type": "error", "code": "rejected_remove", "message": reason})
                    continue
                room.broadcast({"type": "participant_list", "users": room.participant_list()})
                continue
            elif mtype == "lock_room":
                if state.get("role") != "host":
                    enqueue({"type": "error", "code": "forbidden", "message": "only the host can lock the room"})
                    continue
                locked = bool(msg.get("locked", False))
                room.locked = locked
                with room.lock:
                    room.broadcast({"type": "participant_list", "users": room.participant_list()})
                continue
            elif mtype == "save_board":
                if state.get("role") != "host":
                    enqueue({"type": "error", "code": "forbidden", "message": "only the host can save"})
                    continue
                name = msg.get("name")
                if not isinstance(name, str) or not name.strip():
                    enqueue({"type": "error", "code": "bad_request", "message": "name required"})
                    continue
                safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_").strip()
                if not safe:
                    enqueue({"type": "error", "code": "bad_request", "message": "invalid name"})
                    continue
                persistence.save_board(room, safe)
                room.dirty = False
                enqueue({"type": "save_ok", "name": safe})
                continue
            elif mtype == "load_board":
                if state.get("role") != "host":
                    enqueue({"type": "error", "code": "forbidden", "message": "only the host can load"})
                    continue
                name = msg.get("name")
                if not isinstance(name, str) or not name.strip():
                    enqueue({"type": "error", "code": "bad_request", "message": "name required"})
                    continue
                safe = "".join(ch for ch in name if ch.isalnum() or ch in "-_").strip()
                data = persistence.load_room(safe)
                if data is None:
                    enqueue({"type": "error", "code": "not_found", "message": "board not found"})
                    continue
                persistence.apply_loaded(room, data)
                enqueue({"type": "load_ok", "name": safe})
                room.broadcast({"type": "snapshot", **room.snapshot()})
                continue
            elif mtype == "list_boards":
                enqueue({"type": "boards_list", "boards": persistence.list_boards()})
                continue
            else:
                enqueue({"type": "error", "code": "unknown_type", "message": f"unknown message type: {mtype}"})
    finally:
        stop_event.set()
        unregister_client(client_record)
        try:
            writer_thread.join(timeout=1.0)
        except Exception:
            pass

        if state["joined"] and not state["left_cleanly"] and not state.get("kicked") and not state.get("replaced"):
            room = rm.get(state["room_code"]) if state["room_code"] else None
            if room is not None:
                username = state["username"]
                with room.lock:
                    p = room.participants.get(username)
                    if p is not None:
                        p.online = False
                        p.send = None
                        p.disconnected_at = time.time()
                room.broadcast({"type": "user_disconnected", "username": username})
                room.broadcast({"type": "participant_list", "users": room.participant_list()})
        try:
            conn.close()
        except OSError:
            pass
        log.info("handler finished")


def _validate_username(msg: dict[str, Any], enqueue) -> Optional[str]:
    username = msg.get("username")
    if not isinstance(username, str) or not username.strip():
        enqueue({"type": "error", "code": "bad_request", "message": "username must be a non-empty string"})
        return None
    username = username.strip()
    if len(username) > 32:
        enqueue({"type": "error", "code": "bad_request", "message": "username is too long"})
        return None
    return username


def _handle_create_room(msg, state, rm, enqueue, log, peer) -> None:
    username = _validate_username(msg, enqueue)
    if username is None:
        return
    password = msg.get("password")
    if password is not None and not isinstance(password, str):
        enqueue({"type": "error", "code": "bad_request", "message": "password must be a string"})
        return
    if isinstance(password, str) and len(password) > 128:
        enqueue({"type": "error", "code": "bad_request", "message": "password is too long"})
        return

    room = rm.create(password=password if isinstance(password, str) and password else None)
    color = room.next_color()
    token = new_token()

    participant = Participant(username=username, color=color, role="host", token=token, send=enqueue,
                              state=state, kick=state.get("kick"))
    with room.lock:
        room.participants[username] = participant
        participants = room.participant_list()

    state["joined"] = True
    state["username"] = username
    state["room_code"] = room.code
    state["role"] = "host"
    state["color"] = color
    state["token"] = token

    enqueue({"type": "room_created", "room_code": room.code, "token": token, "role": "host"})
    enqueue({"type": "participant_list", "users": participants})
    log.info("created room %s for %s", room.code, username)


def _handle_join_room(msg, state, rm, enqueue, log, peer) -> None:
    ip = peer.split(":")[0]
    username = _validate_username(msg, enqueue)
    if username is None:
        return
    room_code = msg.get("room_code")
    if not isinstance(room_code, str) or not room_code.strip():
        enqueue({"type": "join_denied", "reason": "bad_request", "message": "room_code must be a non-empty string"})
        return
    room_code = room_code.strip().upper()
    password = msg.get("password")
    if password is not None and not isinstance(password, str):
        enqueue({"type": "join_denied", "reason": "bad_request", "message": "password must be a string"})
        return

    if _LOGIN_LIMITER.is_locked(ip):
        enqueue({"type": "join_denied", "reason": "too_many_attempts", "message": "too many failed attempts, try later"})
        return

    room = rm.get(room_code)
    if room is None:
        enqueue({"type": "join_denied", "reason": "room_not_found", "message": "no room with that code"})
        return

    # Prune expired disconnected participants before checking username.
    now = time.time()
    with room.lock:
        expired = [u for u, p in list(room.participants.items())
                   if not p.online and p.disconnected_at is not None
                   and now - p.disconnected_at > rm.grace_period]
        for u in expired:
            room.participants.pop(u, None)
        # Prune removed (freed) slots whose grace period expired.
        for token, p in list(room.removed_participants.items()):
            if p.disconnected_at is not None and now - p.disconnected_at > rm.grace_period:
                room.removed_participants.pop(token, None)

    with room.lock:
        if room.locked:
            enqueue({"type": "join_denied", "reason": "room_locked", "message": "room is locked"})
            return
        if username in room.participants:
            participant = room.participants[username]
            if not participant.online:
                remaining = room._grace_remaining(participant, now)
                enqueue({"type": "join_denied", "reason": "username_reserved",
                         "message": f"That name is reserved for a participant who left recently. If it is you, use Rejoin last room. Otherwise try again in {int(remaining)} seconds or pick another name.",
                         "remaining_grace": remaining})
                return
            enqueue({"type": "join_denied", "reason": "username_taken", "message": "username already in room"})
            return
        if not room.check_password(password if isinstance(password, str) else None):
            locked = _LOGIN_LIMITER.record_failure(ip)
            reason = "too_many_attempts" if locked else "wrong_password"
            msg_text = "too many failed attempts, try later" if locked else "incorrect password"
            enqueue({"type": "join_denied", "reason": reason, "message": msg_text})
            return

    _LOGIN_LIMITER.record_success(ip)

    color = room.next_color()
    token = new_token()
    participant = Participant(username=username, color=color, role="editor", token=token, send=enqueue,
                              state=state, kick=state.get("kick"))
    with room.lock:
        room.participants[username] = participant
        participants = room.participant_list()

    state["joined"] = True
    state["username"] = username
    state["room_code"] = room.code
    state["role"] = "editor"
    state["color"] = color
    state["token"] = token

    enqueue(
        {
            "type": "join_ok",
            "token": token,
            "role": "editor",
            "user_color": color,
            "participants": participants,
            "snapshot": room.snapshot(),
            "seq": room.seq,
            "chat_history": list(room.chat),
        }
    )

    with room.lock:
        room.broadcast({"type": "user_joined", "username": username, "color": color, "role": "editor"}, exclude=username)
        room.broadcast({"type": "participant_list", "users": room.participant_list()}, exclude=username)
    log.info("%s joined room %s", username, room.code)


def _handle_reconnect(msg, state, rm, enqueue, log, peer) -> None:
    token = msg.get("token")
    room_code = msg.get("room_code")
    last_seq = msg.get("last_seq")

    if not isinstance(token, str) or not token.strip():
        enqueue({"type": "reconnect_denied", "reason": "invalid_token", "message": "token required"})
        return
    if not isinstance(room_code, str) or not room_code.strip():
        enqueue({"type": "reconnect_denied", "reason": "bad_request", "message": "room_code required"})
        return
    room_code = room_code.strip().upper()

    room = rm.get(room_code)
    if room is None:
        enqueue({"type": "reconnect_denied", "reason": "room_gone", "message": "room no longer exists"})
        return

    now = time.time()
    # Prune expired.
    with room.lock:
        expired = [u for u, p in list(room.participants.items())
                   if not p.online and p.disconnected_at is not None
                   and now - p.disconnected_at > rm.grace_period]
        for u in expired:
            room.participants.pop(u, None)
        # Prune removed (freed) slots whose grace period expired.
        for token, p in list(room.removed_participants.items()):
            if p.disconnected_at is not None and now - p.disconnected_at > rm.grace_period:
                room.removed_participants.pop(token, None)

    # Kicked tokens are remembered even after the participant is removed.
    with room.lock:
        if token in room.kicked_tokens:
            kicked = True
        else:
            kicked = False
    if kicked:
        enqueue({"type": "reconnect_denied", "reason": "kicked", "message": "you were kicked"})
        return

    # Find participant by token.
    participant = None
    with room.lock:
        for p in room.participants.values():
            if p.token == token:
                participant = p
                break
        if participant is None:
            for p in room.removed_participants.values():
                if p.token == token:
                    participant = p
                    break

    if participant is None:
        enqueue({"type": "reconnect_denied", "reason": "invalid_token", "message": "unknown token"})
        return
    if participant.kicked:
        enqueue({"type": "reconnect_denied", "reason": "kicked", "message": "you were kicked"})
        return
    now = time.time()
    if (not participant.online) and participant.disconnected_at is not None \
            and now - participant.disconnected_at > rm.grace_period:
        with room.lock:
            room.removed_participants.pop(participant.token, None)
            room.participants.pop(participant.username, None)
        enqueue({"type": "reconnect_denied", "reason": "token_expired", "message": "reconnection grace period expired"})
        return

    # If the slot was freed, restore it now.
    with room.lock:
        if participant.username in room.participants and room.participants[participant.username] is not participant:
            enqueue({"type": "reconnect_denied", "reason": "username_taken", "message": "that name is now in use"})
            return
        room.removed_participants.pop(participant.token, None)
        room.participants[participant.username] = participant

    # Replace old socket if still half-open.
    old_state = participant.state
    if old_state is not None and old_state is not state:
        old_state["replaced"] = True
    if participant.kick is not None and participant.state is not state:
        try:
            participant.kick()
        except Exception:
            pass

    username = participant.username
    color = participant.color
    role = participant.role

    with room.lock:
        participant.online = True
        participant.send = enqueue
        participant.state = state
        participant.kick = state.get("kick")
        participant.disconnected_at = None
        participants = room.participant_list()

    state["joined"] = True
    state["username"] = username
    state["room_code"] = room.code
    state["role"] = role
    state["color"] = color
    state["token"] = token

    # Build reconnect_ok with ops_since or snapshot.
    result = room.ops_since(last_seq) if isinstance(last_seq, int) and not isinstance(last_seq, bool) else None
    if result is None:
        enqueue({
            "type": "reconnect_ok",
            "seq": room.seq,
            "snapshot": room.snapshot(),
            "chat_history": list(room.chat),
            "participants": participants,
        })
    else:
        enqueue({
            "type": "reconnect_ok",
            "seq": room.seq,
            "ops_since": {"from_seq": result["from_seq"], "ops": result["ops"]},
            "participants": participants,
        })

    with room.lock:
        room.broadcast({"type": "user_reconnected", "username": username}, exclude=username)
        room.broadcast({"type": "participant_list", "users": room.participant_list()}, exclude=username)
    # Also send participant_list to the reconnected client.
    enqueue({"type": "participant_list", "users": participants})
    log.info("%s reconnected to room %s", username, room.code)


def _handle_leave(state, rm, log) -> None:
    room = rm.get(state["room_code"]) if state["room_code"] else None
    if room is None:
        return
    username = state["username"]
    with room.lock:
        room.participants.pop(username, None)
        room.broadcast({"type": "user_left", "username": username})
        room.broadcast({"type": "participant_list", "users": room.participant_list()})
    log.info("%s left room %s", username, state["room_code"])