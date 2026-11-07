"""Per-connection handler thread.

One instance (as a function) runs per TCP connection. It owns that socket's
read loop and the send lock that serialises writes. Day 1 behaviour is a
simple echo; later milestones replace the dispatch with a message-type
table, but the surrounding error handling and locking stay the same.
"""

from __future__ import annotations

import logging
import socket
import threading
from typing import Any

from protocol import (
    BadMessage,
    ConnectionClosed,
    FrameTooLarge,
    recv_msg,
    send_msg,
)


def _safe_send(
    conn: socket.socket, lock: threading.Lock, msg: dict[str, Any], log: logging.Logger
) -> bool:
    """Send one message while holding the per-client lock.

    Returns True on success. On failure the caller should stop the loop and
    close the socket; we never let a send error propagate and kill the
    server.
    """
    try:
        with lock:
            send_msg(conn, msg)
        return True
    except (ConnectionClosed, OSError, ValueError) as exc:
        log.info("send failed, closing connection: %s", exc)
        return False


def handle_client(conn: socket.socket, addr: tuple[str, int], server_logger: logging.Logger) -> None:
    """Read framed messages from one client and echo them back.

    Malformed or oversize input closes only this connection. Nothing raised
    here escapes to the accept loop or other clients.
    """
    peer = f"{addr[0]}:{addr[1]}"
    log = server_logger.getChild(f"handler.{peer}")
    send_lock = threading.Lock()
    log.info("handler started")

    try:
        # Day 1: block forever on reads; later milestones add a heartbeat
        # timeout here so dead peers are detected within ~15 seconds.
        conn.settimeout(None)
        while True:
            try:
                msg = recv_msg(conn)
            except ConnectionClosed:
                log.info("client closed connection")
                break
            except FrameTooLarge as exc:
                log.warning("rejected oversize frame from %s: %s", peer, exc)
                _safe_send(conn, send_lock, {"type": "error", "code": "frame_too_large", "message": str(exc)}, log)
                break
            except BadMessage as exc:
                log.warning("rejected bad message from %s: %s", peer, exc)
                _safe_send(conn, send_lock, {"type": "error", "code": "bad_message", "message": str(exc)}, log)
                break
            except Exception as exc:  # pragma: no cover - defensive
                log.exception("unexpected read error from %s: %s", peer, exc)
                break

            log.debug("echoing message from %s: %s", peer, msg.get("type"))
            reply = dict(msg)
            reply["echo"] = True
            if not _safe_send(conn, send_lock, reply, log):
                break
    finally:
        try:
            conn.close()
        except OSError:
            pass
        log.info("handler finished")
