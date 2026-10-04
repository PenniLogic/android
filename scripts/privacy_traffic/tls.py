"""Ephemeral, in-memory test CA. No key file, OS trust change or external socket."""

from __future__ import annotations

import importlib.metadata
import select
import socket
import ssl
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, TypeVar

from .safety import Refusal, require, safe_host


T = TypeVar("T")
RUNTIME = {
    "cryptography": "50.0.2",
    "pyOpenSSL": "26.4.0",
    "cffi": "2.1.1",
    "pycparser": "3.0",
}


def check_runtime() -> dict[str, str]:
    for name, version in RUNTIME.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            raise Refusal("privacy_runtime_missing") from None
        require(actual == version, "privacy_runtime_version_drift")
    return dict(RUNTIME)


class MemoryCA:
    def __init__(self):
        check_runtime()
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import NameOID

        self._key = ec.generate_private_key(ec.SECP256R1())
        now = datetime.now(timezone.utc)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "privacy.synthetic.invalid")])
        self._certificate = (
            x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(self._key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(self._key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self._key.public_key()), critical=False)
            .add_extension(
                x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True,
            )
            .sign(self._key, hashes.SHA256())
        )
        self.public_pem = self._certificate.public_bytes(serialization.Encoding.PEM).decode("ascii")

    def server_context(self, host: str):
        from OpenSSL import SSL, crypto
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

        require(safe_host(host) is not None and host.endswith(".synthetic.invalid"), "test_tls_host_refused")
        now = datetime.now(timezone.utc)
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
            .issuer_name(self._certificate.subject)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(minutes=10))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(self._key.public_key()), critical=False)
            .add_extension(
                x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True,
            )
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .sign(self._key, hashes.SHA256())
        )
        context = SSL.Context(SSL.TLS_SERVER_METHOD)
        context.set_min_proto_version(SSL.TLS1_2_VERSION)
        context.use_certificate(crypto.X509.from_cryptography(certificate))
        context.use_privatekey(crypto.PKey.from_cryptography_key(key))
        context.check_privatekey()
        return context

    def client_context(self) -> ssl.SSLContext:
        context = ssl.create_default_context(cadata=self.public_pem)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.set_alpn_protocols(["http/1.1"])
        return context


class MemoryTLS:
    def __init__(self, context, connection: socket.socket, deadline: float):
        from OpenSSL import SSL

        self.socket = connection
        self.socket.setblocking(False)
        self.connection = SSL.Connection(context, connection)
        self.connection.set_accept_state()
        self.deadline = deadline
        self._perform(self.connection.do_handshake)

    def _perform(self, action: Callable[[], T]) -> T:
        from OpenSSL import SSL

        while True:
            remaining = self.deadline - time.monotonic()
            require(remaining > 0, "capture_timeout")
            try:
                return action()
            except SSL.WantReadError:
                select.select([self.socket], [], [], remaining)
            except SSL.WantWriteError:
                select.select([], [self.socket], [], remaining)
            except SSL.ZeroReturnError:
                raise Refusal("truncated_http") from None
            except (SSL.Error, OSError):
                raise Refusal("tls_capture_failed") from None

    def recv(self, size: int) -> bytes:
        return self._perform(lambda: self.connection.recv(size))

    def sendall(self, data: bytes) -> None:
        position = 0
        while position < len(data):
            sent = self._perform(lambda: self.connection.send(data[position:]))
            require(sent > 0, "tls_capture_failed")
            position += sent

    def close(self) -> None:
        self.socket.close()
