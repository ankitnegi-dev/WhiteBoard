"""Minimal CLI test client for the Day 1 echo server.

Usage:
    python tests/fake_client.py --host 127.0.0.1 --port 5050

Type a line and press Enter to send it as {"type": "chat", "text": ...}.
Everything received from the server is printed by a separate receiver
thread, so sending and receiving never block each other.
"""

from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import uuid

# Allow running as ``python tests/fake_client.py`` from the project root.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from protocol import ConnectionClosed, recv_msg, send_msg


def parse_args():
    p = argparse.ArgumentParser(description="Whiteboard CLI test client")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=5050)
    # Timeout used only for establishing the TCP connection. Once connected
    # we clear it (see main) so an idle client never times out on recv.
    p.add_argument("--connect-timeout", type=float, default=5.0)
    p.add_argument("--username", default="user")
    p.add_argument("--create", action="store_true", help="create a new room on connect")
    p.add_argument("--join", metavar="ROOM_CODE", help="join an existing room on connect")
    p.add_argument("--password", default=None, help="room password")
    p.add_argument("--show-heartbeat", action="store_true", help="print ping/pong messages")
    p.add_argument("--test-stroke", action="store_true", help="send a sample stroke after joining")
    return p.parse_args()


def receiver(sock: socket.socket, stop: threading.Event, show_heartbeat: bool = False) -> None:
    try:
        while not stop.is_set():
            try:
                msg = recv_msg(sock)
            except socket.timeout:
                # A recv timeout is not a disconnect: the connection is still
                # healthy, we just have not received data yet. Keep waiting.
                continue
            if isinstance(msg, dict) and msg.get("type") == "ping":
                try:
                    send_msg(sock, {"type": "pong"})
                except Exception:
                    pass
                if show_heartbeat:
                    print(f"<- {msg}", flush=True)
                continue
            if isinstance(msg, dict) and msg.get("type") == "pong":
                if show_heartbeat:
                    print(f"<- {msg}", flush=True)
                continue
            print(f"<- {msg}", flush=True)
    except ConnectionClosed:
        print("<- connection closed by server", flush=True)
    except Exception as exc:  # pragma: no cover - interactive
        print(f"<- error: {exc}", flush=True)
    finally:
        stop.set()


def main() -> int:
    args = parse_args()
    sock = socket.create_connection((args.host, args.port), timeout=args.connect_timeout)
    # The timeout above applies only to connect(). Clear it afterwards so an
    # idle but connected client never raises socket.timeout on recv.
    sock.settimeout(None)
    stop = threading.Event()
    threading.Thread(target=receiver, args=(sock, stop, args.show_heartbeat), daemon=True).start()

    if args.create:
        send_msg(sock, {"type": "create_room", "username": args.username, "password": args.password})
    elif args.join:
        send_msg(sock, {"type": "join_room", "username": args.username, "room_code": args.join, "password": args.password})

    if args.test_stroke:
        sid = str(uuid.uuid4())
        send_msg(sock, {"type": "stroke_start", "op_id": str(uuid.uuid4()), "stroke_id": sid, "tool": "pen", "color": "#000000", "width": 2, "x": 10, "y": 20})
        send_msg(sock, {"type": "stroke_points", "op_id": str(uuid.uuid4()), "stroke_id": sid, "points": [[10, 20], [20, 30], [30, 40]]})
        send_msg(sock, {"type": "stroke_end", "op_id": str(uuid.uuid4()), "stroke_id": sid})

    print(f"connected to {args.host}:{args.port}; type lines to send, Ctrl+C to quit")
    try:
        for line in sys.stdin:
            text = line.rstrip("\n")
            if not text:
                continue
            try:
                send_msg(sock, {"type": "chat", "text": text})
            except Exception as exc:
                print(f"send failed: {exc}", flush=True)
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
