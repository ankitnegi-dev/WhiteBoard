"""Entry point for the whiteboard server.

Run with ``python server/main.py --host 0.0.0.0 --port 5050``. The accept
loop spawns one daemon thread per client so a slow or crashed client can
never block the others. All OS-specific behaviour (signal handling, LAN IP
discovery) is delegated to platform_utils.
"""

from __future__ import annotations

import argparse
import logging
import signal
import socket
import sys
import threading
from typing import Optional

# Support running as ``python server/main.py`` from the project root.
import os
import sys as _sys

_sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import platform_utils
from server.client_handler import handle_client, monitor_clients
from server.room_manager import RoomManager
from server import persistence


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="LAN collaborative whiteboard server")
    parser.add_argument("--host", default="0.0.0.0", help="bind address (default 0.0.0.0)")
    parser.add_argument("--port", type=int, default=5050, help="TCP port (default 5050)")
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="logging verbosity",
    )
    parser.add_argument("--heartbeat-interval", type=float, default=5.0, help="seconds between server pings")
    parser.add_argument("--heartbeat-timeout", type=float, default=15.0, help="seconds of silence before disconnect")
    parser.add_argument("--rooms-dir", default="rooms", help="directory for saved rooms (default rooms)")
    parser.add_argument("--grace-period", type=float, default=120.0, help="seconds an offline user keeps their slot")
    parser.add_argument("--empty-room-grace", type=float, default=5.0, help="seconds before an empty room is removed")
    parser.add_argument("--autosave-interval", type=float, default=30.0, help="seconds between autosave scans")
    parser.add_argument("--rate-limit", type=float, default=60.0, help="max ops per second per client")
    return parser.parse_args(argv)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def make_server_socket(host: str, port: int) -> socket.socket:
    """Create and bind the listening socket."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    return sock


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    log = logging.getLogger("server.main")

    persistence.set_rooms_dir(args.rooms_dir)

    rm = RoomManager(grace_period=args.grace_period,
                     empty_room_grace=args.empty_room_grace)

    sock = make_server_socket(args.host, args.port)
    log.info("server listening on %s:%d", args.host, args.port)
    for ip in platform_utils.get_lan_ips():
        log.info("LAN client target: %s:%d", ip, args.port)
    print(f"Whiteboard server listening on {args.host}:{args.port}")
    for ip in platform_utils.get_lan_ips():
        print(f"  connect clients to {ip}:{args.port}")

    stop_event = threading.Event()

    def _request_stop(_signum=None, _frame=None):
        log.info("shutdown requested")
        stop_event.set()

    signal.signal(signal.SIGINT, _request_stop)
    try:
        signal.signal(signal.SIGTERM, _request_stop)
    except (AttributeError, ValueError):
        pass

    # Single monitor thread for all clients.
    monitor_thread = threading.Thread(
        target=monitor_clients,
        args=(args.heartbeat_interval, args.heartbeat_timeout, stop_event),
        name="client-monitor",
        daemon=True,
    )
    monitor_thread.start()

    # Autosave thread: saves dirty rooms and prunes empty rooms.
    autosave_thread = threading.Thread(
        target=persistence.autosave_loop,
        args=(rm, args.autosave_interval, stop_event, args.empty_room_grace, args.grace_period),
        name="autosave",
        daemon=True,
    )
    autosave_thread.start()

    try:
        while not stop_event.is_set():
            try:
                sock.settimeout(0.5)
                conn, addr = sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if stop_event.is_set():
                    break
                raise
            log.info("client connected from %s:%d", addr[0], addr[1])
            thread = threading.Thread(
                target=handle_client,
                args=(conn, addr, log, args.heartbeat_interval, args.heartbeat_timeout, rm, args.rate_limit),
                name=f"client-{addr[0]}:{addr[1]}",
                daemon=True,
            )
            thread.start()
    finally:
        log.info("shutting down, saving rooms")
        stop_event.set()
        # Save all rooms on shutdown.
        for code in rm.codes():
            room = rm.get(code)
            if room is not None:
                try:
                    persistence.save_room(room)
                except Exception:
                    pass
        try:
            sock.close()
        except OSError:
            pass
        monitor_thread.join(timeout=2.0)
        autosave_thread.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())