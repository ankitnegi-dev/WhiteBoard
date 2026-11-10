"""Shared wire protocol for the LAN collaborative whiteboard.

This module is the single source of truth for how bytes move between the
server and clients. Both sides import it so framing and validation stay
identical. Keeping framing here (rather than duplicating it) is what lets
us test split reads and coalesced reads once and trust both endpoints.

Transport is TCP, which is a byte stream with no message boundaries, so
every message is length-prefixed:

    [4 bytes big-endian unsigned length N][N bytes UTF-8 JSON]

See docs/protocol.md for the full message catalogue.
"""

from __future__ import annotations

import json
import socket
import struct
from typing import Any, Dict

# Hard upper bound on a single framed message. Rejecting oversize lengths
# *before* reading the body stops a malicious/buggy peer from making us
# allocate gigabytes of memory. 1 MB covers normal ops; image uploads (a
# later milestone) will raise this or use a chunked path.
MAX_MESSAGE_SIZE: int = 1 * 1024 * 1024

# Whitelist of shape kinds accepted by the server. Old kinds (line, rect,
# ellipse, arrow) are kept so saved boards still load.
SHAPE_KINDS: tuple[str, ...] = (
    "line", "arrow", "double_arrow",
    "rectangle", "rounded_rectangle", "ellipse",
    "triangle", "right_triangle", "diamond", "pentagon", "hexagon",
    "star", "plus", "heart",
    "process", "decision", "terminator", "data", "document",
    "speech_bubble",
    # legacy
    "rect",
)

# 4-byte big-endian unsigned length prefix, as required by the spec.
_LENGTH_FORMAT: str = ">I"
_LENGTH_SIZE: int = struct.calcsize(_LENGTH_FORMAT)


class ConnectionClosed(Exception):
    """Raised when the peer closes the connection (clean EOF) mid-frame."""


class FrameTooLarge(Exception):
    """Raised when a declared frame length exceeds MAX_MESSAGE_SIZE."""


class BadMessage(Exception):
    """Raised when a frame is not valid UTF-8 JSON or not a JSON object."""


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """Read exactly ``n`` bytes from ``sock``, looping until they arrive.

    TCP's ``recv`` may return fewer bytes than requested (partial read) or
    more than one message at once (coalesced read). This helper hides that
    from callers so the rest of the code can think in whole messages.

    Raises ConnectionClosed if the peer closes before n bytes arrive.

    Note on timeouts: if ``sock`` has a timeout and it fires part way through
    the read, the bytes already consumed are gone from the socket buffer.
    The caller cannot safely retry recv_exact/recv_msg on the same
    connection after that; it must close the socket. For that reason the
    server and clients clear the socket timeout (settimeout(None)) once a
    connection is established and use heartbeat pings for idle detection
    instead of relying on bare socket timeouts.
    """
    chunks: list[bytes] = []
    remaining = n
    while remaining > 0:
        # A timeout or interrupt surfaces as the normal socket exception.
        data = sock.recv(remaining)
        if not data:
            # Empty recv means the peer closed the connection. Treat a
            # partial frame as a closed connection rather than silently
            # returning short data, which would corrupt the stream.
            raise ConnectionClosed(
                f"connection closed after {n - remaining} of {n} bytes"
            )
        chunks.append(data)
        remaining -= len(data)
    return b"".join(chunks)


def send_msg(sock: socket.socket, obj: Any) -> None:
    """Serialize ``obj`` as JSON and send it as one framed message.

    The caller is responsible for holding any per-socket send lock so two
    threads never interleave bytes on the same TCP connection.
    """
    try:
        payload = json.dumps(obj, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise BadMessage(f"object is not JSON serializable: {exc}") from exc

    if len(payload) > MAX_MESSAGE_SIZE:
        raise FrameTooLarge(
            f"message body {len(payload)} bytes exceeds limit {MAX_MESSAGE_SIZE}"
        )

    header = struct.pack(_LENGTH_FORMAT, len(payload))
    # sendall retries on partial sends, which is what we want for TCP.
    sock.sendall(header + payload)


def recv_msg(sock: socket.socket) -> Dict[str, Any]:
    """Read one framed message and return it as a dict.

    Raises FrameTooLarge for an oversize declared length (checked before
    reading the body), BadMessage for invalid JSON or a non-object, and
    ConnectionClosed on EOF.

    Timeout handling: if the socket times out mid-frame, the partial bytes
    are already consumed and this function (or recv_exact) will surface a
    socket.timeout. The caller must close the connection rather than retry,
    because the stream is no longer frame-aligned. See recv_exact's
    docstring for the full note.
    """
    header = recv_exact(sock, _LENGTH_SIZE)
    (length,) = struct.unpack(_LENGTH_FORMAT, header)

    if length > MAX_MESSAGE_SIZE:
        # We deliberately do NOT read the body: the peer is either buggy or
        # hostile, and consuming up to 4 GB would be a DoS vector.
        raise FrameTooLarge(
            f"declared frame length {length} exceeds limit {MAX_MESSAGE_SIZE}"
        )
    if length == 0:
        raise BadMessage("empty frame (zero-length body)")

    body = recv_exact(sock, length)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BadMessage(f"frame is not valid UTF-8: {exc}") from exc

    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise BadMessage(f"frame is not valid JSON: {exc}") from exc

    if not isinstance(obj, dict):
        raise BadMessage(f"top-level JSON must be an object, got {type(obj).__name__}")

    return obj