"""A bounded HTTP/1.1 CONNECT interceptor for owned synthetic loopback origins only."""

from __future__ import annotations

import re
import socket
import ssl
import threading
import time
from typing import Protocol

from .capture import Inspector
from .policy import LIMITS
from .safety import Refusal, document, require, safe_host
from .tls import MemoryCA, MemoryTLS


class Channel(Protocol):
    def recv(self, size: int) -> bytes: ...

    def sendall(self, data: bytes) -> None: ...


def read_http(channel: Channel, *, connect: bool = False) -> tuple[str, dict[str, str], bytes]:
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = channel.recv(1024)
        require(bool(chunk), "truncated_http")
        buffer.extend(chunk)
        delimiter = buffer.find(b"\r\n\r\n")
        require(
            delimiter <= LIMITS["max_header_bytes"] if delimiter >= 0 else len(buffer) <= LIMITS["max_header_bytes"],
            "header_size_refused",
        )
    raw_head, body = bytes(buffer).split(b"\r\n\r\n", 1)
    require(len(raw_head) <= LIMITS["max_header_bytes"], "header_size_refused")
    try:
        lines = raw_head.decode("ascii").split("\r\n")
    except UnicodeError:
        raise Refusal("malformed_http") from None
    require(all(line and all(" " <= character <= "~" for character in line) for line in lines), "malformed_http")
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        require(bool(separator) and re.fullmatch(r"[A-Za-z0-9-]+", name) is not None, "malformed_http")
        name = name.lower()
        require(name not in headers and value.startswith(" ") and not value.startswith("  "), "malformed_http")
        headers[name] = value[1:]
    require("transfer-encoding" not in headers and "content-encoding" not in headers, "opaque_payload")
    if connect:
        require(set(headers) == {"host"} and not body, "malformed_http")
        return lines[0], headers, b""
    require(re.fullmatch(r"0|[1-9][0-9]{0,5}", headers.get("content-length", "")) is not None, "malformed_http")
    size = int(headers["content-length"])
    require(size <= LIMITS["max_body_bytes"], "body_size_refused")
    require(len(body) <= size, "malformed_http")
    while len(body) < size:
        chunk = channel.recv(min(4096, size - len(body)))
        require(bool(chunk), "truncated_http")
        body += chunk
    return lines[0], headers, body


def reply(channel: Channel, status: int, body: bytes) -> None:
    channel.sendall(
        f"HTTP/1.1 {status} Privacy\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii") + body,
    )


class LoopbackService:
    def __init__(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(4)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._active: socket.socket | None = None
        self._lock = threading.Lock()
        self.failures: list[str] = []
        self._thread = threading.Thread(target=self._serve, name="privacy-owned-loopback")

    def __enter__(self):
        self._thread.start()
        require(self._ready.wait(1) and self._thread.is_alive(), "proxy_start_failed")
        return self

    def __exit__(self, _type, _value, _traceback):
        self._stop.set()
        self.listener.close()
        with self._lock:
            if self._active is not None:
                self._active.close()
        self._thread.join(3)
        require(not self._thread.is_alive(), "proxy_shutdown_failed")

    def _serve(self) -> None:
        self._ready.set()
        while not self._stop.is_set():
            try:
                connection, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stop.is_set():
                    return
                self.failures.append("listener_failed")
                return
            with self._lock:
                self._active = connection
            try:
                require(address[0] == "127.0.0.1", "loopback_peer_required")
                connection.settimeout(2)
                self.handle(connection)
            except (OSError, Refusal):
                if not self._stop.is_set():
                    self.failures.append("connection_failed")
            finally:
                connection.close()
                with self._lock:
                    self._active = None

    def handle(self, connection: socket.socket) -> None:
        raise NotImplementedError


class SyntheticOrigin(LoopbackService):
    HOST = "origin.synthetic.invalid"

    def __init__(self, ca: MemoryCA):
        super().__init__()
        self._context = ca.server_context(self.HOST)
        self.requests = 0

    def handle(self, connection: socket.socket) -> None:
        channel = MemoryTLS(self._context, connection, time.monotonic() + 2)
        try:
            request, headers, body = read_http(channel)
            require(request.startswith("POST /probe/") and headers.get("host") == self.HOST, "malformed_http")
            document(body, limit=LIMITS["max_body_bytes"])
            self.requests += 1
            reply(channel, 200, b'{"ok":true}')
        finally:
            channel.close()


class InterceptProxy(LoopbackService):
    def __init__(self, inspector: Inspector, ca: MemoryCA, origin: SyntheticOrigin):
        super().__init__()
        self.inspector = inspector
        self.ca = ca
        self.origin = origin
        self._contexts = {route["host"]: ca.server_context(route["host"]) for route in inspector.policy.data["destinations"]}

    def handle(self, connection: socket.socket) -> None:
        host: str | None = None
        channel: Channel = connection
        try:
            self.inspector.connection()
            request, headers, _ = read_http(connection, connect=True)
            parts = request.split(" ")
            require(len(parts) == 3 and parts[0] == "CONNECT" and parts[2] == "HTTP/1.1", "malformed_http")
            authority = parts[1]
            require(authority.endswith(":443") and headers["host"] == authority, "malformed_http")
            host = authority[:-4]
            self.inspector.scan([authority])
            self.inspector.admit_host(host, inventory=True)
            connection.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            channel = MemoryTLS(
                self._contexts[host], connection,
                min(self.inspector.started + LIMITS["run_timeout_seconds"], time.monotonic() + 2),
            )
            self.inspector.tls_interceptions += 1
            request, headers, body = read_http(channel)
            parts = request.split(" ")
            require(len(parts) == 3 and parts[2] == "HTTP/1.1", "malformed_http")
            route, scrubbed = self.inspector.inspect(host, parts[0], parts[1], headers, body)
            upstream_body = self._forward(route["path"], scrubbed)
            self.inspector.response(upstream_body)
            self.inspector.forwarded += 1
            reply(channel, 200, upstream_body)
        except Refusal as error:
            self.inspector.refuse(error.code, host)
            reply(channel, 422, b'{"privacy_refused":true}')
            if error.code in ("request_budget_exceeded", "capture_budget_exceeded", "capture_timeout"):
                self._stop.set()
        except (OSError, ssl.SSLError):
            self.inspector.refuse("upstream_failed", host)
            raise Refusal("upstream_failed") from None

    def _forward(self, path: str, body: bytes) -> bytes:
        with socket.create_connection(("127.0.0.1", self.origin.port), timeout=2) as socket_to_origin:
            with self.ca.client_context().wrap_socket(socket_to_origin, server_hostname=self.origin.HOST) as upstream:
                upstream.sendall(
                    f"POST {path} HTTP/1.1\r\nHost: {self.origin.HOST}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii") + body,
                )
                status, headers, response = read_http(upstream)
                require(
                    status.startswith("HTTP/1.1 200 ") and headers.get("content-type") == "application/json",
                    "upstream_failed",
                )
                return response


def synthetic_request(
    proxy: InterceptProxy, host: str, path: str, body: bytes, *,
    content_type: str = "application/json", extra_headers: dict[str, str] | None = None,
    declared_body_bytes: int | None = None,
) -> int:
    require(
        safe_host(host) is not None and host.endswith(".synthetic.invalid"), "synthetic_client_host_required",
    )
    with socket.create_connection(("127.0.0.1", proxy.port), timeout=3) as client:
        client.sendall(f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode("ascii"))
        line = bytearray()
        while not line.endswith(b"\r\n\r\n"):
            chunk = client.recv(1)
            require(bool(chunk) and len(line) <= LIMITS["max_header_bytes"], "proxy_start_failed")
            line.extend(chunk)
        if not line.startswith(b"HTTP/1.1 200 "):
            return 422
        with proxy.ca.client_context().wrap_socket(client, server_hostname=host) as secured:
            extras = "".join(f"{name}: {value}\r\n" for name, value in (extra_headers or {}).items())
            secured.sendall(
                f"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: {content_type}\r\n"
                f"Content-Length: {len(body) if declared_body_bytes is None else declared_body_bytes}\r\n"
                f"Connection: close\r\nX-Privacy-Run: {proxy.inspector.run_id}\r\n"
                f"{extras}\r\n".encode("ascii") + body,
            )
            status, _, response = read_http(secured)
            require(response in (b'{"ok":true}', b'{"privacy_refused":true}'), "opaque_response")
            return int(status.split(" ")[1])
