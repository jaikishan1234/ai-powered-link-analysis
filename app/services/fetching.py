"""Safe website fetching with SSRF protections.

The fetcher accepts a :class:`~app.utils.url_validation.ValidatedURL`, not a
raw string. Every request is made through an IP-pinned transport, automatic
redirects are disabled, and redirect targets are validated before they are
followed. Responses are streamed and bounded, and only HTML is accepted.

This stage does not parse HTML, and it does not make DNS rebinding safe on its
own: the pinned transport constrains and verifies the connection, but callers
must still treat the validated address snapshot as time-sensitive.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from urllib.parse import urljoin

import anyio
import httpcore2
import httpx2

from app.core.config import settings
from app.services._pinned_transport import build_pinned_transport
from app.utils.url_validation import (
    URLValidationError,
    ValidatedURL,
    validate_public_url,
)

_USER_AGENT: Final = "ai-powered-link-analysis/0.1"
_REQUEST_HEADERS: Final = {
    "User-Agent": _USER_AGENT,
    "Accept": "text/html, application/xhtml+xml",
}
_HTML_CONTENT_TYPES: Final = frozenset({"text/html", "application/xhtml+xml"})
_REDIRECT_STATUSES: Final = frozenset({301, 302, 303, 307, 308})

_NETWORK_ERRORS: Final = (
    httpcore2.TimeoutException,
    httpcore2.NetworkError,
    httpcore2.ProtocolError,
    httpcore2.UnsupportedProtocol,
    httpcore2.ProxyError,
    httpx2.TimeoutException,
    httpx2.NetworkError,
    httpx2.ProtocolError,
    httpx2.UnsupportedProtocol,
    httpx2.ProxyError,
    httpx2.DecodingError,
    httpx2.StreamError,
    OSError,
)


class FetchError(Exception):
    """Base class for website fetch failures."""


class FetchConnectionError(FetchError):
    """The remote host could not be reached."""


class FetchTimeoutError(FetchError):
    """The remote host did not respond within the configured timeout."""


class FetchProtocolError(FetchError):
    """The remote host returned an invalid or undecodable response."""


class FetchHTTPError(FetchError):
    """The remote host returned an HTTP error status."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"Remote host returned HTTP {status_code}.")
        self.status_code = status_code


class FetchContentTypeError(FetchError):
    """The response was not an HTML document."""


class FetchTooLargeError(FetchError):
    """The response exceeded the configured maximum size."""


class FetchRedirectError(FetchError):
    """A redirect was missing, malformed, unsafe, looping, or excessive."""


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """A successfully fetched HTML document."""

    url: str
    status_code: int
    content_type: str
    html: str
    redirect_count: int


async def fetch_html(validated_url: ValidatedURL) -> FetchedPage:
    """Fetch an HTML document from an already validated URL.

    Automatic redirects are disabled. Each redirect target is validated with
    :func:`validate_public_url` and fetched through a transport pinned to that
    target's validated addresses. Only ``text/html`` and
    ``application/xhtml+xml`` responses are accepted.

    Raises a :class:`FetchError` subclass for network, timeout, HTTP, content
    type, size, and redirect failures.
    """
    if not isinstance(validated_url, ValidatedURL):
        raise TypeError("fetch_html() requires a ValidatedURL.")

    if not validated_url.resolved_addresses:
        raise ValueError("ValidatedURL must contain at least one resolved address.")

    current = validated_url
    visited = {current.url}
    redirect_count = 0

    while True:
        try:
            async with _client_for(current) as client:
                async with client.stream("GET", current.url) as response:
                    if response.status_code in _REDIRECT_STATUSES:
                        if redirect_count >= settings.fetch_max_redirects:
                            raise FetchRedirectError("Too many redirects.")

                        current = await _next_redirect(current, response)
                        if current.url in visited:
                            raise FetchRedirectError("Redirect loop detected.")

                        visited.add(current.url)
                        redirect_count += 1
                        continue

                    if response.status_code >= 300:
                        raise FetchHTTPError(response.status_code)

                    content_type = _require_html_content_type(response)
                    html = await _read_limited_html(response)
                    return FetchedPage(
                        url=current.url,
                        status_code=response.status_code,
                        content_type=content_type,
                        html=html,
                        redirect_count=redirect_count,
                    )
        except FetchError:
            raise
        except _NETWORK_ERRORS as exc:
            raise _translate_network_error(exc) from exc


def _client_for(validated: ValidatedURL) -> httpx2.AsyncClient:
    """Build an HTTPX client pinned to one validated URL."""
    return httpx2.AsyncClient(
        transport=build_pinned_transport(validated),
        trust_env=False,
        follow_redirects=False,
        timeout=httpx2.Timeout(
            connect=settings.fetch_timeout_seconds,
            read=settings.fetch_timeout_seconds,
            write=settings.fetch_timeout_seconds,
            pool=settings.fetch_timeout_seconds,
        ),
        headers=_REQUEST_HEADERS,
    )


async def _next_redirect(
    current: ValidatedURL, response: httpx2.Response
) -> ValidatedURL:
    """Resolve and validate a redirect target.

    Validation performs blocking DNS resolution, so it runs in a worker
    thread rather than blocking the event loop.
    """
    location = response.headers.get("location")
    if location is None or not location.strip():
        raise FetchRedirectError("Redirect response is missing a Location header.")

    try:
        target = urljoin(current.url, location.strip())
    except ValueError:
        raise FetchRedirectError("Redirect Location is malformed.") from None

    try:
        return await anyio.to_thread.run_sync(validate_public_url, target)
    except URLValidationError:
        raise FetchRedirectError("Redirect target is not allowed.") from None


def _require_html_content_type(response: httpx2.Response) -> str:
    """Return the normalized media type, or raise for non-HTML responses."""
    value = response.headers.get("content-type")
    if value is None:
        raise FetchContentTypeError("Response is missing a Content-Type header.")

    media_type = value.split(";", 1)[0].strip().lower()
    if not media_type:
        raise FetchContentTypeError("Response has a malformed Content-Type header.")
    if media_type not in _HTML_CONTENT_TYPES:
        raise FetchContentTypeError(
            f"Unsupported response content type: {media_type}."
        )

    return media_type


async def _read_limited_html(response: httpx2.Response) -> str:
    """Stream decoded response bytes without exceeding the configured limit."""
    limit = settings.fetch_max_response_bytes
    body = bytearray()

    async for chunk in response.aiter_bytes():
        if not chunk:
            continue
        if len(body) + len(chunk) > limit:
            raise FetchTooLargeError(
                f"Response exceeded the maximum size of {limit} bytes."
            )
        body.extend(chunk)

    return _decode_html(bytes(body), response.charset_encoding)


def _decode_html(body: bytes, encoding: str | None) -> str:
    """Decode HTML with the declared charset, falling back to UTF-8."""
    try:
        return body.decode(encoding or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def _translate_network_error(exc: Exception) -> FetchError:
    """Map transport-level failures to application-level fetch errors."""
    if isinstance(exc, (httpcore2.TimeoutException, httpx2.TimeoutException, TimeoutError)):
        return FetchTimeoutError("The remote host did not respond in time.")

    if isinstance(
        exc,
        (
            httpcore2.ProtocolError,
            httpcore2.UnsupportedProtocol,
            httpx2.ProtocolError,
            httpx2.UnsupportedProtocol,
            httpx2.DecodingError,
        ),
    ):
        return FetchProtocolError("The remote host returned an invalid response.")

    return FetchConnectionError("Could not connect to the remote host.")
