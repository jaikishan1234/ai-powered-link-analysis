"""Tests for the IP-pinned HTTPX transport.

These tests use a fake network backend, so no sockets are opened. They verify
that the transport never re-resolves the hostname, always connects to a
validated address, verifies the connected peer, and preserves the hostname for
the HTTP Host header and TLS SNI.
"""

from __future__ import annotations

import asyncio
import ipaddress
import ssl
from collections.abc import Iterable
from typing import Any

import httpcore2
import httpx2
import pytest

from app.services import _pinned_transport
from app.services._pinned_transport import (
    PinnedNetworkBackend,
    PinnedTransport,
    build_pinned_transport,
)
from app.utils.url_validation import ValidatedURL

PUBLIC_IPV4 = "93.184.216.34"
SECOND_PUBLIC_IPV4 = "93.184.216.35"
PUBLIC_IPV6 = "2606:4700:4700::1111"


class _FakeStream:
    """A minimal AsyncNetworkStream stand-in."""

    def __init__(self, peer: Any = (PUBLIC_IPV4, 443)) -> None:
        self.peer = peer
        self.closed = False

    def get_extra_info(self, info: str) -> Any:
        if info == "server_addr":
            return self.peer
        return None

    async def aclose(self) -> None:
        self.closed = True


class _FakeBackend(httpcore2.AsyncNetworkBackend):
    """Records connection attempts instead of opening sockets."""

    def __init__(
        self,
        stream: _FakeStream | None = None,
        error: Exception | None = None,
    ) -> None:
        self.stream = stream or _FakeStream()
        self.error = error
        self.calls: list[dict[str, Any]] = []
        self.slept: list[float] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> Any:
        self.calls.append(
            {
                "host": host,
                "port": port,
                "timeout": timeout,
                "local_address": local_address,
                "socket_options": socket_options,
            }
        )
        if self.error is not None:
            raise self.error
        return self.stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> Any:
        raise AssertionError("Unix sockets must not be used")

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)


def _validated(
    url: str = "https://example.com/",
    hostname: str = "example.com",
    addresses: tuple[str, ...] = (PUBLIC_IPV4,),
) -> ValidatedURL:
    scheme, port = ("https", 443) if url.startswith("https://") else ("http", 80)
    return ValidatedURL(
        url=url,
        scheme=scheme,
        hostname=hostname,
        port=port,
        resolved_addresses=tuple(ipaddress.ip_address(a) for a in addresses),
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_backend_requires_resolved_addresses() -> None:
    with pytest.raises(ValueError):
        PinnedNetworkBackend(_validated(addresses=()))


def test_backend_connects_to_pinned_ip_not_hostname() -> None:
    backend = _FakeBackend()
    pinned = PinnedNetworkBackend(_validated(), backend=backend)

    stream = _run(
        pinned.connect_tcp(
            host="example.com",
            port=443,
            timeout=5.0,
            local_address="127.0.0.1",
            socket_options=[(1, 2, 3)],
        )
    )

    assert stream is backend.stream
    assert backend.calls == [
        {
            "host": PUBLIC_IPV4,
            "port": 443,
            "timeout": 5.0,
            "local_address": "127.0.0.1",
            "socket_options": [(1, 2, 3)],
        }
    ]


def test_backend_host_check_is_case_insensitive() -> None:
    backend = _FakeBackend()
    pinned = PinnedNetworkBackend(_validated(), backend=backend)

    _run(pinned.connect_tcp(host="EXAMPLE.COM", port=443))

    assert backend.calls[0]["host"] == PUBLIC_IPV4


def test_backend_rejects_unexpected_connection_host() -> None:
    backend = _FakeBackend()
    pinned = PinnedNetworkBackend(_validated(), backend=backend)

    with pytest.raises(httpcore2.ConnectError):
        _run(pinned.connect_tcp(host="evil.example", port=443))

    assert backend.calls == []


def test_backend_rejects_unverified_peer() -> None:
    stream = _FakeStream(peer=("203.0.113.9", 443))
    pinned = PinnedNetworkBackend(
        _validated(), backend=_FakeBackend(stream=stream)
    )

    with pytest.raises(httpcore2.ConnectError):
        _run(pinned.connect_tcp(host="example.com", port=443))

    assert stream.closed is True


@pytest.mark.parametrize(
    "peer",
    [
        None,
        (),
        ("not-an-ip", 443),
        (1234, 443),
        ("", 443),
    ],
)
def test_backend_rejects_missing_or_malformed_peer(peer: Any) -> None:
    stream = _FakeStream(peer=peer)
    pinned = PinnedNetworkBackend(
        _validated(), backend=_FakeBackend(stream=stream)
    )

    with pytest.raises(httpcore2.ConnectError):
        _run(pinned.connect_tcp(host="example.com", port=443))

    assert stream.closed is True


def test_backend_accepts_any_validated_address() -> None:
    stream = _FakeStream(peer=(SECOND_PUBLIC_IPV4, 443))
    pinned = PinnedNetworkBackend(
        _validated(addresses=(PUBLIC_IPV4, SECOND_PUBLIC_IPV4)),
        backend=_FakeBackend(stream=stream),
    )

    result = _run(pinned.connect_tcp(host="example.com", port=443))

    assert result is stream
    assert stream.closed is False


def test_backend_normalizes_ipv4_mapped_ipv6_peer() -> None:
    stream = _FakeStream(peer=(PUBLIC_IPV4, 443))
    pinned = PinnedNetworkBackend(
        _validated(addresses=("::ffff:" + PUBLIC_IPV4,)),
        backend=_FakeBackend(stream=stream),
    )

    result = _run(pinned.connect_tcp(host="example.com", port=443))

    assert result is stream


def test_backend_accepts_ipv6_peer_with_scope_suffix() -> None:
    stream = _FakeStream(peer=(PUBLIC_IPV6 + "%eth0", 443))
    pinned = PinnedNetworkBackend(
        _validated(addresses=(PUBLIC_IPV6,)),
        backend=_FakeBackend(stream=stream),
    )

    result = _run(pinned.connect_tcp(host="example.com", port=443))

    assert result is stream


def test_backend_propagates_inner_connection_error() -> None:
    error = httpcore2.ConnectError("connection refused")
    pinned = PinnedNetworkBackend(
        _validated(), backend=_FakeBackend(error=error)
    )

    with pytest.raises(httpcore2.ConnectError, match="connection refused"):
        _run(pinned.connect_tcp(host="example.com", port=443))


def test_backend_rejects_unix_sockets() -> None:
    pinned = PinnedNetworkBackend(_validated())

    with pytest.raises(httpcore2.ConnectError):
        _run(pinned.connect_unix_socket("/tmp/socket"))


def test_backend_sleep_delegates_to_inner_backend() -> None:
    inner = _FakeBackend()
    pinned = PinnedNetworkBackend(_validated(), backend=inner)

    _run(pinned.sleep(0.5))

    assert inner.slept == [0.5]


class _FakePool:
    def __init__(self, response: httpcore2.Response | None = None) -> None:
        self.response = response or httpcore2.Response(
            200, headers=[(b"content-type", b"text/html")], content=b"ok"
        )
        self.requests: list[httpcore2.Request] = []
        self.closed = False

    async def handle_async_request(
        self, request: httpcore2.Request
    ) -> httpcore2.Response:
        self.requests.append(request)
        return self.response

    async def aclose(self) -> None:
        self.closed = True


def _patch_pool(
    monkeypatch: pytest.MonkeyPatch, pool: _FakePool
) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def fake_pool(**kwargs: Any) -> _FakePool:
        captured.update(kwargs)
        return pool

    monkeypatch.setattr(_pinned_transport.httpcore2, "AsyncConnectionPool", fake_pool)
    return captured


def test_transport_uses_pinned_backend_and_bounded_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _FakePool()
    captured = _patch_pool(monkeypatch, pool)

    PinnedTransport(_validated())

    assert isinstance(captured["ssl_context"], ssl.SSLContext)
    assert captured["max_connections"] == 10
    assert captured["max_keepalive_connections"] == 0
    assert captured["keepalive_expiry"] is None
    assert captured["http1"] is True
    assert captured["http2"] is False
    assert isinstance(captured["network_backend"], PinnedNetworkBackend)
    assert captured["proxy"] is None


def test_transport_preserves_hostname_and_forces_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = _FakePool()
    _patch_pool(monkeypatch, pool)
    transport = PinnedTransport(_validated())

    request = httpx2.Request(
        "GET",
        "https://example.com/path?q=1",
        extensions={"sni_hostname": "attacker.example"},
    )
    response = _run(transport.handle_async_request(request))

    core_request = pool.requests[0]
    assert core_request.url.host == b"example.com"
    assert core_request.url.port is None  # default HTTPS port
    assert core_request.url.target == b"/path?q=1"
    assert core_request.extensions["sni_hostname"] == "example.com"
    assert (b"Host", b"example.com") in core_request.headers
    assert response.status_code == 200
    assert _run(response.aread()) == b"ok"


def test_transport_closes_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _FakePool()
    _patch_pool(monkeypatch, pool)
    transport = PinnedTransport(_validated())

    _run(transport.aclose())

    assert pool.closed is True


def test_build_pinned_transport_returns_pinned_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_pool(monkeypatch, _FakePool())

    transport = build_pinned_transport(_validated())

    assert isinstance(transport, PinnedTransport)
