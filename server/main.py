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
from server.client_handler import handle_client


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
    return parser.parse_args(argv)


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def make_server_socket(host: str, port: int) -> socket.socket:
    """Create and bind the listening socket.

    SO_REUSEADDR lets us restart quickly after a crash or Ctrl+C without
    waiting for the OS to release the port (TIME_WAIT).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(128)
    return sock


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    setup_logging(args.log_level)
    log = logging.getLogger("server.main")

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

    # SIGINT works on both Windows and Linux for Ctrl+C. SIGTERM is the
    # normal Unix service-manager signal; Windows ignores it, which is fine.
    signal.signal(signal.SIGINT, _request_stop)
    try:
        signal.signal(signal.SIGTERM, _request_stop)
    except (AttributeError, ValueError):
        pass

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
                args=(conn, addr, log),
                name=f"client-{addr[0]}:{addr[1]}",
                daemon=True,
            )
            thread.start()
    finally:
        log.info("shutting down, closing listening socket")
        try:
            sock.close()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
