"""IP-pinned HTTPX transport for safe outbound website fetches.

This module isolates the only dependency on ``httpcore2`` internals:

- :class:`PinnedNetworkBackend` connects to a validated IP address instead of
  letting the HTTP stack resolve the hostname again, then verifies the address
  the socket actually connected to.
- :class:`PinnedTransport` adapts that backend to HTTPX while keeping the
  validated hostname as the request origin, so the HTTP ``Host`` header and
  TLS SNI remain the hostname rather than the IP address.

Maintenance note: ``httpcore2.AsyncNetworkBackend`` and
``httpcore2.AsyncConnectionPool`` are part of httpcore's public API, but their
signatures can change between major versions. Keep this file small and update
it deliberately when upgrading ``httpx2``/``httpcore2``. Do not add proxy or
Unix-socket support here.
"""

from __future__ import annotations

import ipaddress
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from typing import Any

import httpcore2
import httpx2

from app.utils.url_validation import ValidatedURL

_MAX_CONNECTIONS = 10

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def _normalize_ip(address: IPAddress) -> IPAddress:
    """Unwrap IPv4-mapped IPv6 addresses for stable comparisons."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


class PinnedNetworkBackend(httpcore2.AsyncNetworkBackend):
    """Connect only to a validated IP and verify the connected peer.

    The hostname passed by httpcore is checked for consistency but is never
    used for DNS resolution. All connections for this backend go to the first
    validated address, and the peer returned by the socket must be one of the
    validated addresses.
    """

    def __init__(
        self,
        validated: ValidatedURL,
        backend: httpcore2.AsyncNetworkBackend | None = None,
    ) -> None:
        if not validated.resolved_addresses:
            raise ValueError("ValidatedURL has no resolved addresses.")

        self._hostname = validated.hostname
        self._pinned_address = validated.resolved_addresses[0]
        self._allowed_addresses = frozenset(
            _normalize_ip(address) for address in validated.resolved_addresses
        )
        self._backend = backend or httpcore2.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        if host.lower() != self._hostname.lower():
            raise httpcore2.ConnectError(
                "Unexpected connection host; refusing to connect."
            )

        stream = await self._backend.connect_tcp(
            host=str(self._pinned_address),
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

        try:
            peer_ip = self._peer_address(stream)
            if peer_ip not in self._allowed_addresses:
                raise httpcore2.ConnectError(
                    "Connected peer is not a validated address."
                )
        except BaseException:
            await stream.aclose()
            raise

        return stream

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore2.AsyncNetworkStream:
        raise httpcore2.ConnectError("Unix socket connections are not supported.")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)

    @staticmethod
    def _peer_address(stream: httpcore2.AsyncNetworkStream) -> IPAddress:
        """Return the connected peer IP, or raise if it cannot be verified."""
        peer = stream.get_extra_info("server_addr")
        if not isinstance(peer, tuple) or not peer or not isinstance(peer[0], str):
            raise httpcore2.ConnectError(
                "Could not verify the connected peer address."
            )

        try:
            return _normalize_ip(ipaddress.ip_address(peer[0].split("%", 1)[0]))
        except ValueError:
            raise httpcore2.ConnectError(
                "Could not verify the connected peer address."
            ) from None


class _ResponseStream(httpx2.AsyncByteStream):
    """Adapt an httpcore response stream to HTTPX."""

    def __init__(self, stream: AsyncIterable[bytes]) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        close = getattr(self._stream, "aclose", None)
        if close is not None:
            await close()


class PinnedTransport(httpx2.AsyncBaseTransport):
    """HTTPX transport that pins connections to a validated address set."""

    def __init__(self, validated: ValidatedURL) -> None:
        ssl_context = httpx2.create_ssl_context(
            verify=True, cert=None, trust_env=False
        )
        self._validated = validated
        self._pool = httpcore2.AsyncConnectionPool(
            ssl_context=ssl_context,
            proxy=None,
            max_connections=_MAX_CONNECTIONS,
            max_keepalive_connections=0,
            keepalive_expiry=None,
            http1=True,
            http2=False,
            network_backend=PinnedNetworkBackend(validated),
        )

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        extensions = dict(request.extensions)
        # Force SNI to the validated hostname even if the caller set something
        # else. For plain HTTP this extension is ignored by httpcore.
        extensions["sni_hostname"] = self._validated.hostname

        core_request = httpcore2.Request(
            method=request.method,
            url=httpcore2.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=extensions,
        )

        core_response = await self._pool.handle_async_request(core_request)
        return httpx2.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_ResponseStream(core_response.stream),
            extensions=core_response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


def build_pinned_transport(validated: ValidatedURL) -> httpx2.AsyncBaseTransport:
    """Build a transport pinned to one validated URL's resolved addresses."""
    return PinnedTransport(validated)
