"""Expose three loopback-only Modbus :502 endpoints inside the HA container.

Run this alongside the simulator in the same network namespace. Home Assistant
can then add 127.0.0.2, .3, and .4 through the official plugin's IP-only form.
"""

from __future__ import annotations

import select
import socket
import socketserver
import threading
import time


TARGETS = {
    "127.0.0.2": ("127.0.0.1", 1502),
    "127.0.0.3": ("127.0.0.1", 1503),
    "127.0.0.4": ("127.0.0.1", 1504),
}


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], target: tuple[str, int]):
        self.target = target
        super().__init__(address, ProxyHandler)


class ProxyHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        try:
            with socket.create_connection(self.server.target, timeout=5) as upstream:
                peers = (self.request, upstream)
                while True:
                    readable, _, _ = select.select(peers, [], [])
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        destination = upstream if source is self.request else self.request
                        destination.sendall(data)
        except OSError:
            return


def main() -> None:
    servers: list[ProxyServer] = []
    try:
        for address, target in TARGETS.items():
            server = ProxyServer((address, 502), target)
            servers.append(server)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            print(f"{address}:502 -> {target[0]}:{target[1]}", flush=True)
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
