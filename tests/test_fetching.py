"""Tests for safe website fetching.

All network access is mocked. DNS is replaced with a fake resolver and the
pinned transport is replaced with an in-memory HTTPX mock transport, so these
tests never contact a real website.
"""

from __future__ import annotations

import asyncio
import gzip
import ipaddress
import socket
import zlib
from collections.abc import AsyncIterator
from typing import Any

import httpx2
import pytest

from app.core.config import settings
from app.services import fetching
from app.services._pinned_transport import PinnedTransport
from app.utils import url_validation
from app.utils.url_validation import ValidatedURL

PUBLIC_IPV4 = "93.184.216.34"
PUBLIC_IPV6 = "2606:4700:4700::1111"


@pytest.fixture(autouse=True)
def _block_live_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail tests that accidentally attempt real DNS resolution."""

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("Unexpected live DNS resolution")

    monkeypatch.setattr(url_validation.socket, "getaddrinfo", fail)


def _mock_dns(
    monkeypatch: pytest.MonkeyPatch, *addresses: str
) -> list[tuple[str, int]]:
    """Replace DNS resolution and record the hostname and port queried."""
    calls: list[tuple[str, int]] = []

    def fake_getaddrinfo(
        host: str, port: int, *args: object, **kwargs: object
    ) -> list[tuple[int, int, int, str, tuple[object, ...]]]:
        calls.append((host, port))
        results: list[tuple[int, int, int, str, tuple[object, ...]]] = []
        for address in addresses:
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            if family == socket.AF_INET6:
                sockaddr: tuple[object, ...] = (address, port, 0, 0)
            else:
                sockaddr = (address, port)
            results.append(
                (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr)
            )
        return results

    monkeypatch.setattr(url_validation.socket, "getaddrinfo", fake_getaddrinfo)
    return calls


def _validated(
    url: str = "https://example.com/",
    hostname: str = "example.com",
    addresses: tuple[str, ...] = (PUBLIC_IPV4,),
) -> ValidatedURL:
    """Build a ValidatedURL without performing DNS."""
    scheme, port = ("https", 443) if url.startswith("https://") else ("http", 80)
    return ValidatedURL(
        url=url,
        scheme=scheme,
        hostname=hostname,
        port=port,
        resolved_addresses=tuple(ipaddress.ip_address(a) for a in addresses),
    )


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    recorded: list[ValidatedURL] | None = None,
) -> None:
    """Route fetch_html through an in-memory transport."""

    def fake_build(validated: ValidatedURL) -> httpx2.AsyncBaseTransport:
        if recorded is not None:
            recorded.append(validated)
        return httpx2.MockTransport(handler)

    monkeypatch.setattr(fetching, "build_pinned_transport", fake_build)


class _ChunkStream(httpx2.AsyncByteStream):
    """A stream that records how many chunks were consumed."""

    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.consumed = 0
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def _html_response(
    body: bytes = b"<html><body>Hello</body></html>",
    status_code: int = 200,
    content_type: str = "text/html; charset=utf-8",
) -> httpx2.Response:
    return httpx2.Response(
        status_code,
        headers={"content-type": content_type},
        content=body,
    )


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_successful_html_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return _html_response(b"<html>Hello</html>")

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated()))

    assert page.url == "https://example.com/"
    assert page.status_code == 200
    assert page.content_type == "text/html"
    assert page.html == "<html>Hello</html>"
    assert page.redirect_count == 0
    assert requests[0].method == "GET"
    assert requests[0].headers["user-agent"] == fetching._USER_AGENT
    assert requests[0].headers["accept"] == "text/html, application/xhtml+xml"


def test_xhtml_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _html_response(
            b"<html xmlns='http://www.w3.org/1999/xhtml'/>",
            content_type="application/xhtml+xml",
        )

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated()))

    assert page.content_type == "application/xhtml+xml"


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 410, 500, 502, 503])
def test_http_error_responses(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _html_response(b"error", status_code=status_code)

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchHTTPError) as exc_info:
        _run(fetching.fetch_html(_validated()))

    assert exc_info.value.status_code == status_code


def test_connection_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchConnectionError):
        _run(fetching.fetch_html(_validated()))


@pytest.mark.parametrize(
    "error",
    [
        httpx2.ConnectTimeout("connect timeout"),
        httpx2.ReadTimeout("read timeout"),
        httpx2.WriteTimeout("write timeout"),
        httpx2.PoolTimeout("pool timeout"),
        TimeoutError("builtin timeout"),
    ],
)
def test_timeout_failures(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise error

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchTimeoutError):
        _run(fetching.fetch_html(_validated()))


@pytest.mark.parametrize(
    "error",
    [
        httpx2.RemoteProtocolError("bad protocol"),
        httpx2.DecodingError("bad encoding"),
    ],
)
def test_protocol_failures(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise error

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchProtocolError):
        _run(fetching.fetch_html(_validated()))


def test_os_error_is_a_connection_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise OSError("network is down")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchConnectionError):
        _run(fetching.fetch_html(_validated()))


def test_stream_error_is_translated_to_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.StreamError("response stream was closed")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchError):
        _run(fetching.fetch_html(_validated()))


@pytest.mark.parametrize(
    "content_type",
    [
        "application/json",
        "application/pdf",
        "application/octet-stream",
        "image/png",
        "text/plain",
        "text/css",
        "text/javascript",
    ],
)
def test_unsupported_content_types(
    monkeypatch: pytest.MonkeyPatch, content_type: str
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _html_response(b"not html", content_type=content_type)

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchContentTypeError):
        _run(fetching.fetch_html(_validated()))


def test_missing_content_type(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, content=b"<html></html>")

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchContentTypeError):
        _run(fetching.fetch_html(_validated()))


@pytest.mark.parametrize("content_type", ["", " ; charset=utf-8", ";charset=utf-8"])
def test_malformed_content_type(
    monkeypatch: pytest.MonkeyPatch, content_type: str
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _html_response(b"<html></html>", content_type=content_type)

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchContentTypeError):
        _run(fetching.fetch_html(_validated()))


def test_content_type_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _html_response(b"<html></html>", content_type="Text/HTML; Charset=UTF-8")

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated()))

    assert page.content_type == "text/html"


def test_streamed_response_size_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetching.settings, "fetch_max_response_bytes", 10)
    stream = _ChunkStream([b"12345678", b"abcdefgh", b"unread"])

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/html"},
            stream=stream,
        )

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchTooLargeError):
        _run(fetching.fetch_html(_validated()))

    # The reader stops as soon as the limit would be exceeded.
    assert stream.consumed == 2
    assert stream.closed is True


def test_response_at_exact_limit_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetching.settings, "fetch_max_response_bytes", 10)
    stream = _ChunkStream([b"12345", b"67890"])

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/html"},
            stream=stream,
        )

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated()))

    assert page.html == "1234567890"


@pytest.mark.parametrize(
    "compress",
    [gzip.compress, zlib.compress],
    ids=["gzip", "deflate"],
)
def test_compressed_response_cannot_bypass_size_limit(
    monkeypatch: pytest.MonkeyPatch, compress: Any
) -> None:
    body = b"<html>" + b"a" * 5000 + b"</html>"
    raw = compress(body)
    assert len(raw) < len(body)

    monkeypatch.setattr(fetching.settings, "fetch_max_response_bytes", 100)
    stream = _ChunkStream([raw])

    async def handler(request: httpx2.Request) -> httpx2.Response:
        encoding = "gzip" if compress is gzip.compress else "deflate"
        return httpx2.Response(
            200,
            headers={
                "content-type": "text/html",
                "content-encoding": encoding,
            },
            stream=stream,
        )

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchTooLargeError):
        _run(fetching.fetch_html(_validated()))


def test_compressed_response_under_limit_is_decoded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    body = b"<html>compressed</html>"
    raw = gzip.compress(body)

    monkeypatch.setattr(fetching.settings, "fetch_max_response_bytes", 1000)
    stream = _ChunkStream([raw])

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={
                "content-type": "text/html",
                "content-encoding": "gzip",
            },
            stream=stream,
        )

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated()))

    assert page.html == body.decode()


def test_declared_charset_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _html_response(
            b"<html>caf\xe9</html>",
            content_type="text/html; charset=iso-8859-1",
        )

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated()))

    assert page.html == "<html>café</html>"


def test_unknown_charset_falls_back_to_utf8(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _html_response(
            b"<html>hello</html>",
            content_type="text/html; charset=does-not-exist",
        )

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated()))

    assert page.html == "<html>hello</html>"


def test_relative_redirect_is_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.path == "/start":
            return httpx2.Response(
                302, headers={"location": "/landing", "content-type": "text/html"}
            )
        return _html_response(b"<html>landed</html>")

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated("https://example.com/start")))

    assert page.url == "https://example.com/landing"
    assert page.html == "<html>landed</html>"
    assert page.redirect_count == 1
    assert [request.url.path for request in requests] == ["/start", "/landing"]


def test_absolute_cross_host_redirect_is_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.host == "a.example.com":
            return httpx2.Response(
                301,
                headers={
                    "location": "https://b.example.com/landing",
                    "content-type": "text/html",
                },
            )
        return _html_response(b"<html>other host</html>")

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated("https://a.example.com/start")))

    assert page.url == "https://b.example.com/landing"
    assert page.redirect_count == 1
    assert [request.url.host for request in requests] == [
        "a.example.com",
        "b.example.com",
    ]


@pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
def test_redirect_status_codes(
    monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/start":
            return httpx2.Response(
                status_code,
                headers={"location": "/next", "content-type": "text/html"},
            )
        return _html_response()

    _patch_transport(monkeypatch, handler)

    page = _run(fetching.fetch_html(_validated("https://example.com/start")))

    assert page.url == "https://example.com/next"
    assert page.redirect_count == 1


@pytest.mark.parametrize(
    "location",
    [
        "http://localhost/secret",
        "http://127.0.0.1/secret",
        "http://[::1]/secret",
        "http://10.0.0.1/secret",
        "http://192.168.1.1/secret",
        "http://169.254.169.254/latest/meta-data/",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "https://user:pass@example.com/secret",
        "https://example.com:8443/secret",
    ],
)
def test_redirect_to_unsafe_destination_is_rejected(
    monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            302, headers={"location": location, "content-type": "text/html"}
        )

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchRedirectError):
        _run(fetching.fetch_html(_validated("https://example.com/start")))


@pytest.mark.parametrize("location", [None, "", "   "])
def test_redirect_without_location_is_rejected(
    monkeypatch: pytest.MonkeyPatch, location: str | None
) -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        headers = {"content-type": "text/html"}
        if location is not None:
            headers["location"] = location
        return httpx2.Response(302, headers=headers)

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchRedirectError):
        _run(fetching.fetch_html(_validated("https://example.com/start")))


@pytest.mark.parametrize(
    "location",
    [
        "https://example.com:abc/",
        "https://[::1",
        "http://",
        "https://exa mple.com/",
    ],
)
def test_malformed_redirect_is_rejected(
    monkeypatch: pytest.MonkeyPatch, location: str
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            302, headers={"location": location, "content-type": "text/html"}
        )

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchRedirectError):
        _run(fetching.fetch_html(_validated("https://example.com/start")))


def test_redirect_loop_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/a":
            location = "/b"
        else:
            location = "/a"
        return httpx2.Response(
            302, headers={"location": location, "content-type": "text/html"}
        )

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchRedirectError, match="loop"):
        _run(fetching.fetch_html(_validated("https://example.com/a")))


def test_redirect_limit_is_enforced(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)
    monkeypatch.setattr(fetching.settings, "fetch_max_redirects", 1)
    requests: list[str] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request.url.path)
        location = "/b" if request.url.path == "/a" else "/c"
        return httpx2.Response(
            302, headers={"location": location, "content-type": "text/html"}
        )

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchRedirectError, match="Too many redirects"):
        _run(fetching.fetch_html(_validated("https://example.com/a")))

    assert requests == ["/a", "/b"]


def test_zero_redirect_budget_rejects_first_redirect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)
    monkeypatch.setattr(fetching.settings, "fetch_max_redirects", 0)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            302, headers={"location": "/next", "content-type": "text/html"}
        )

    _patch_transport(monkeypatch, handler)

    with pytest.raises(fetching.FetchRedirectError, match="Too many redirects"):
        _run(fetching.fetch_html(_validated("https://example.com/start")))


def test_each_redirect_is_pinned_to_its_own_validated_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)
    recorded: list[ValidatedURL] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == "/start":
            return httpx2.Response(
                302, headers={"location": "/next", "content-type": "text/html"}
            )
        return _html_response()

    _patch_transport(monkeypatch, handler, recorded)

    _run(fetching.fetch_html(_validated("https://example.com/start")))

    assert [validated.url for validated in recorded] == [
        "https://example.com/start",
        "https://example.com/next",
    ]


def test_credentials_and_cookies_are_not_forwarded_across_hosts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)
    requests: list[httpx2.Request] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        if request.url.host == "a.example.com":
            request.headers["Authorization"] = "Bearer first-host-secret"
            request.headers["Cookie"] = "session=secret; Path=/"
            return httpx2.Response(
                302,
                headers={
                    "location": "https://b.example.com/landing",
                    "content-type": "text/html",
                },
            )
        return _html_response()

    _patch_transport(monkeypatch, handler)

    _run(fetching.fetch_html(_validated("https://a.example.com/start")))

    first_request = requests[0]
    assert first_request.headers["authorization"] == "Bearer first-host-secret"
    second_request = requests[1]
    assert second_request.url.host == "b.example.com"
    for header in ("cookie", "authorization", "proxy-authorization"):
        assert header not in second_request.headers


def test_fetch_requires_a_validated_url() -> None:
    with pytest.raises(TypeError):
        _run(fetching.fetch_html("https://example.com/"))  # type: ignore[arg-type]


def test_fetch_requires_resolved_addresses() -> None:
    validated = _validated(addresses=())

    with pytest.raises(ValueError):
        _run(fetching.fetch_html(validated))


def test_client_disables_environment_proxies_and_automatic_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.setenv(name, "http://127.0.0.1:9")

    async def scenario() -> None:
        client = fetching._client_for(_validated())
        try:
            assert client.trust_env is False
            assert client.follow_redirects is False
            assert isinstance(client._transport, PinnedTransport)
            assert client.timeout.connect == settings.fetch_timeout_seconds
            assert client.timeout.read == settings.fetch_timeout_seconds
            assert client.timeout.write == settings.fetch_timeout_seconds
            assert client.timeout.pool == settings.fetch_timeout_seconds
        finally:
            await client.aclose()

    _run(scenario())


def test_fetch_uses_configured_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetching.settings, "fetch_timeout_seconds", 1.25)
    seen: list[httpx2.Timeout] = []

    original_client_for = fetching._client_for

    def recording_client_for(validated: ValidatedURL) -> httpx2.AsyncClient:
        client = original_client_for(validated)
        seen.append(client.timeout)
        return client

    monkeypatch.setattr(fetching, "_client_for", recording_client_for)

    async def handler(request: httpx2.Request) -> httpx2.Response:
        return _html_response()

    _patch_transport(monkeypatch, handler)

    _run(fetching.fetch_html(_validated()))

    assert seen[0].connect == 1.25
    assert seen[0].read == 1.25
    assert seen[0].write == 1.25
    assert seen[0].pool == 1.25
