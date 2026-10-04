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
from .safety import Refusal, document, remaining_seconds, require, safe_host
from .tls import MemoryCA, MemoryTLS


class Channel(Protocol):
    def recv(self, size: int) -> bytes: ...

    def sendall(self, data: bytes) -> None: ...


def receive(channel: Channel, size: int, *, deadline: float) -> bytes:
    remaining = remaining_seconds(deadline)
    if isinstance(channel, socket.socket):
        channel.settimeout(min(2, remaining))
    try:
        result = channel.recv(size)
    except TimeoutError:
        raise Refusal("capture_timeout") from None
    remaining_seconds(deadline)
    return result


def send(channel: Channel, raw: bytes, *, deadline: float) -> None:
    remaining = remaining_seconds(deadline)
    if isinstance(channel, socket.socket):
        channel.settimeout(min(2, remaining))
    try:
        channel.sendall(raw)
    except TimeoutError:
        raise Refusal("capture_timeout") from None
    remaining_seconds(deadline)


def read_http(channel: Channel, *, deadline: float, connect: bool = False) -> tuple[str, dict[str, str], bytes]:
    remaining_seconds(deadline)
    buffer = bytearray()
    while b"\r\n\r\n" not in buffer:
        chunk = receive(channel, 1024, deadline=deadline)
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
        remaining_seconds(deadline)
        return lines[0], headers, b""
    require(re.fullmatch(r"0|[1-9][0-9]{0,5}", headers.get("content-length", "")) is not None, "malformed_http")
    size = int(headers["content-length"])
    require(size <= LIMITS["max_body_bytes"], "body_size_refused")
    require(len(body) <= size, "malformed_http")
    while len(body) < size:
        chunk = receive(channel, min(4096, size - len(body)), deadline=deadline)
        require(bool(chunk), "truncated_http")
        body += chunk
    remaining_seconds(deadline)
    return lines[0], headers, body


def reply(channel: Channel, status: int, body: bytes, *, deadline: float) -> None:
    send(channel,
        f"HTTP/1.1 {status} Privacy\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii") + body,
        deadline=deadline,
    )


class LoopbackService:
    def __init__(self, *, deadline: float | None = None):
        self.deadline = time.monotonic() + LIMITS["run_timeout_seconds"] if deadline is None else deadline
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
                remaining = remaining_seconds(self.deadline)
            except Refusal:
                self.expired()
                return
            try:
                self.listener.settimeout(min(0.1, remaining))
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

    def expired(self) -> None:
        self.failures.append("capture_timeout")
        self._stop.set()


class SyntheticOrigin(LoopbackService):
    HOST = "origin.synthetic.invalid"

    def __init__(self, ca: MemoryCA, *, deadline: float | None = None):
        super().__init__(deadline=deadline)
        self._context = ca.server_context(self.HOST)
        self.requests = 0

    def handle(self, connection: socket.socket) -> None:
        channel = MemoryTLS(self._context, connection, min(self.deadline, time.monotonic() + 2))
        try:
            request, headers, body = read_http(channel, deadline=self.deadline)
            require(request.startswith("POST /probe/") and headers.get("host") == self.HOST, "malformed_http")
            document(body, limit=LIMITS["max_body_bytes"])
            self.requests += 1
            reply(channel, 200, b'{"ok":true}', deadline=self.deadline)
        finally:
            channel.close()


class InterceptProxy(LoopbackService):
    def __init__(self, inspector: Inspector, ca: MemoryCA, origin: SyntheticOrigin):
        super().__init__(deadline=inspector.deadline)
        self.inspector = inspector
        self.ca = ca
        self.origin = origin
        self.origin.deadline = min(self.origin.deadline, inspector.deadline)
        self._contexts = {route["host"]: ca.server_context(route["host"]) for route in inspector.policy.data["destinations"]}

    def handle(self, connection: socket.socket) -> None:
        host: str | None = None
        channel: Channel = connection
        try:
            self.inspector.connection()
            request, headers, _ = read_http(connection, deadline=self.deadline, connect=True)
            parts = request.split(" ")
            require(len(parts) == 3 and parts[0] == "CONNECT" and parts[2] == "HTTP/1.1", "malformed_http")
            authority = parts[1]
            require(authority.endswith(":443") and headers["host"] == authority, "malformed_http")
            host = authority[:-4]
            self.inspector.admit_host(host, inventory=True)
            send(connection, b"HTTP/1.1 200 Connection Established\r\n\r\n", deadline=self.deadline)
            channel = MemoryTLS(
                self._contexts[host], connection,
                min(self.deadline, time.monotonic() + 2),
            )
            self.inspector.tls_interceptions += 1
            request, headers, body = read_http(channel, deadline=self.deadline)
            parts = request.split(" ")
            require(len(parts) == 3 and parts[2] == "HTTP/1.1", "malformed_http")
            route, scrubbed = self.inspector.inspect(host, parts[0], parts[1], headers, body)
            upstream_body = self._forward(route["path"], scrubbed)
            self.inspector.response(upstream_body)
            self.inspector.forwarded += 1
            reply(channel, 200, upstream_body, deadline=self.deadline)
        except Refusal as error:
            self.inspector.refuse(error.code, host)
            if error.code in ("request_budget_exceeded", "capture_budget_exceeded", "capture_timeout"):
                self._stop.set()
            if error.code != "capture_timeout":
                reply(channel, 422, b'{"privacy_refused":true}', deadline=self.deadline)
        except (OSError, ssl.SSLError):
            self.inspector.refuse("upstream_failed", host)
            raise Refusal("upstream_failed") from None

    def expired(self) -> None:
        self.inspector.refuse("capture_timeout")
        self._stop.set()

    def _forward(self, path: str, body: bytes) -> bytes:
        with socket.create_connection(
            ("127.0.0.1", self.origin.port), timeout=min(2, remaining_seconds(self.deadline)),
        ) as socket_to_origin:
            remaining_seconds(self.deadline)
            socket_to_origin.settimeout(min(2, remaining_seconds(self.deadline)))
            with self.ca.client_context().wrap_socket(socket_to_origin, server_hostname=self.origin.HOST) as upstream:
                send(upstream,
                    f"POST {path} HTTP/1.1\r\nHost: {self.origin.HOST}\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii") + body,
                    deadline=self.deadline,
                )
                status, headers, response = read_http(upstream, deadline=self.deadline)
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
    with socket.create_connection(
        ("127.0.0.1", proxy.port), timeout=min(3, remaining_seconds(proxy.deadline)),
    ) as client:
        send(client, f"CONNECT {host}:443 HTTP/1.1\r\nHost: {host}:443\r\n\r\n".encode("ascii"), deadline=proxy.deadline)
        line = bytearray()
        while not line.endswith(b"\r\n\r\n"):
            chunk = receive(client, 1, deadline=proxy.deadline)
            require(bool(chunk) and len(line) <= LIMITS["max_header_bytes"], "proxy_start_failed")
            line.extend(chunk)
        if not line.startswith(b"HTTP/1.1 200 "):
            return 422
        client.settimeout(min(2, remaining_seconds(proxy.deadline)))
        with proxy.ca.client_context().wrap_socket(client, server_hostname=host) as secured:
            extras = "".join(f"{name}: {value}\r\n" for name, value in (extra_headers or {}).items())
            send(secured,
                f"POST {path} HTTP/1.1\r\nHost: {host}\r\nContent-Type: {content_type}\r\n"
                f"Content-Length: {len(body) if declared_body_bytes is None else declared_body_bytes}\r\n"
                f"Connection: close\r\nX-Privacy-Run: {proxy.inspector.run_id}\r\n"
                f"{extras}\r\n".encode("ascii") + body,
                deadline=proxy.deadline,
            )
            status, _, response = read_http(secured, deadline=proxy.deadline)
            require(response in (b'{"ok":true}', b'{"privacy_refused":true}'), "opaque_response")
            return int(status.split(" ")[1])
