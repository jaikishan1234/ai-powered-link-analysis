"""Tests for public URL validation and SSRF protections."""

import ipaddress
import socket

import pytest

from app.utils import url_validation
from app.utils.url_validation import (
    URLValidationError,
    ValidatedURL,
    validate_public_url,
)

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


def _expect_invalid(url: str, message: str) -> URLValidationError:
    with pytest.raises(URLValidationError) as exc_info:
        validate_public_url(url)
    assert message in str(exc_info.value)
    return exc_info.value


@pytest.mark.parametrize(
    ("url", "scheme", "port"),
    [
        ("http://example.com/path?q=1", "http", 80),
        ("https://example.com/", "https", 443),
    ],
)
def test_valid_public_hostnames(
    monkeypatch: pytest.MonkeyPatch, url: str, scheme: str, port: int
) -> None:
    calls = _mock_dns(monkeypatch, PUBLIC_IPV4)

    result = validate_public_url(url)

    assert result.url == url
    assert result.scheme == scheme
    assert result.hostname == "example.com"
    assert result.port == port
    assert result.resolved_addresses == (ipaddress.ip_address(PUBLIC_IPV4),)
    assert calls == [("example.com", port)]


def test_url_and_hostname_are_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mock_dns(monkeypatch, PUBLIC_IPV4)

    result = validate_public_url("HTTPS://EXAMPLE.COM.:443/path#fragment")

    assert result.url == "https://example.com/path"
    assert result.hostname == "example.com"
    assert result.port == 443
    assert calls == [("example.com", 443)]


def test_public_ipv4_literal_is_accepted() -> None:
    result = validate_public_url("http://8.8.8.8/")

    assert result.url == "http://8.8.8.8/"
    assert result.hostname == "8.8.8.8"
    assert result.port == 80
    assert result.resolved_addresses == (ipaddress.ip_address("8.8.8.8"),)


def test_public_ipv6_literal_is_accepted() -> None:
    result = validate_public_url("https://[2606:4700:4700::1111]/")

    assert result.url == "https://[2606:4700:4700::1111]/"
    assert result.hostname == PUBLIC_IPV6
    assert result.port == 443
    assert result.resolved_addresses == (ipaddress.ip_address(PUBLIC_IPV6),)


def test_public_ipv4_mapped_ipv6_literal_is_accepted() -> None:
    result = validate_public_url("http://[::ffff:8.8.8.8]/")

    assert result.hostname == "::ffff:8.8.8.8"
    assert result.resolved_addresses == (ipaddress.ip_address("::ffff:8.8.8.8"),)
    assert result.host_header == "[::ffff:8.8.8.8]"


def test_host_header_for_ipv4() -> None:
    assert validate_public_url("http://8.8.8.8/").host_header == "8.8.8.8"


def test_host_header_for_ipv6() -> None:
    result = validate_public_url("https://[2606:4700:4700::1111]/")

    assert result.host_header == "[2606:4700:4700::1111]"


def test_host_header_for_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)

    assert validate_public_url("https://example.com/").host_header == "example.com"


def test_host_header_omits_default_port(monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4)

    assert validate_public_url("http://example.com:80/").host_header == "example.com"


def test_host_header_includes_non_default_port_when_policy_changes() -> None:
    validated = ValidatedURL(
        url="https://example.com:8443/",
        scheme="https",
        hostname="example.com",
        port=8443,
        resolved_addresses=(),
    )

    assert validated.host_header == "example.com:8443"


def test_host_header_brackets_ipv6_with_non_default_port() -> None:
    validated = ValidatedURL(
        url="https://[2001:db8::1]:8443/",
        scheme="https",
        hostname="2001:db8::1",
        port=8443,
        resolved_addresses=(),
    )

    assert validated.host_header == "[2001:db8::1]:8443"


def test_hostname_resolving_to_public_ipv6_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mock_dns(monkeypatch, PUBLIC_IPV6)

    result = validate_public_url("https://example.com/")

    assert result.resolved_addresses == (ipaddress.ip_address(PUBLIC_IPV6),)
    assert calls == [("example.com", 443)]


def test_hostname_resolving_to_public_ipv4_and_ipv6_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4, PUBLIC_IPV6)

    result = validate_public_url("https://example.com/")

    assert result.resolved_addresses == (
        ipaddress.ip_address(PUBLIC_IPV4),
        ipaddress.ip_address(PUBLIC_IPV6),
    )


@pytest.mark.parametrize("url", ["", "   "])
def test_empty_urls_are_rejected(url: str) -> None:
    _expect_invalid(url, "must not be empty")


@pytest.mark.parametrize(
    "url",
    ["http://", "http:///path", "https://", "https:///path"],
)
def test_missing_hostnames_are_rejected(url: str) -> None:
    _expect_invalid(url, "must include a hostname")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "not-a-url",
    ],
)
def test_unsupported_schemes_are_rejected(url: str) -> None:
    _expect_invalid(url, "Only HTTP and HTTPS")


@pytest.mark.parametrize(
    "url",
    [
        "http://exa mple.com/",
        "http://example.com/pa\tth",
        "http://example.com\\@evil.com/",
    ],
)
def test_invalid_characters_are_rejected(url: str) -> None:
    _expect_invalid(url, "invalid characters")


@pytest.mark.parametrize(
    "url",
    [
        "http://exa\u200bample.com/",  # zero-width space
        "http://exa\u200dample.com/",  # zero-width joiner
        "http://exa\ufeffample.com/",  # zero-width no-break space
        "http://exam\u202eple.com/",  # right-to-left override
        "http://exam\u2066ple.com/",  # left-to-right isolate
    ],
)
def test_unicode_format_characters_are_rejected(url: str) -> None:
    _expect_invalid(url, "invalid characters")


@pytest.mark.parametrize("url", ["http://[::1", "http://::1/"])
def test_malformed_urls_are_rejected(url: str) -> None:
    with pytest.raises(URLValidationError):
        validate_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://user:pass@example.com/",
        "http://user@example.com/",
        "http://:pass@example.com/",
    ],
)
def test_embedded_credentials_are_rejected(url: str) -> None:
    _expect_invalid(url, "embedded credentials")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/",
        "http://localhost.localdomain/",
        "http://foo.localhost/",
        "http://printer.local/",
        "http://router/",
        "http://service.internal/",
        "http://foo.home.arpa/",
        "http://metadata/",
        "http://metadata.google.internal/",
        "http://metadata.goog/",
        "http://instance-data/",
        "http://instance-data.ec2.internal/",
        "http://metadata.tencentyun.com/",
        "http://metadata.azure.internal/",
        "http://metadata.alibabacloud.com/",
        "http://metadata.aliyun.com/",
    ],
)
def test_local_and_metadata_hostnames_are_rejected(url: str) -> None:
    _expect_invalid(url, "Local and internal hostnames")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.onion/",
        "http://example.test/",
        "http://example.example/",
        "http://example.invalid/",
        "http://example.alt/",
    ],
)
def test_reserved_hostname_suffixes_are_rejected(url: str) -> None:
    _expect_invalid(url, "Reserved hostnames")


@pytest.mark.parametrize(
    "url",
    [
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://127.0.0.1/",
        "http://169.254.169.254/",
        "http://192.0.2.1/",
        "http://224.0.0.1/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "http://100.100.100.200/",
        "http://192.88.99.1/",
    ],
)
def test_non_public_ipv4_literals_are_rejected(url: str) -> None:
    _expect_invalid(url, "public IP address")


@pytest.mark.parametrize(
    "url",
    [
        "http://[::1]/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
        "http://[ff02::1]/",
        "http://[::]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[fec0::1]/",
        "http://[2001:db8::1]/",
        "http://[fd00:ec2::254]/",
        "http://[2001:20::1]/",
        "http://[2001:30::1]/",
        "http://[64:ff9b::7f00:1]/",
        "http://[2002:7f00:1::1]/",
    ],
)
def test_non_public_ipv6_literals_are_rejected(url: str) -> None:
    _expect_invalid(url, "public IP address")


@pytest.mark.parametrize(
    "url",
    [
        "http://[::ffff:0:10.0.0.1]/",
        "http://[::ffff:0:127.0.0.1]/",
    ],
)
def test_private_ipv4_translated_ipv6_literals_are_rejected(url: str) -> None:
    _expect_invalid(url, "public IP address")


@pytest.mark.parametrize(
    "url",
    [
        "http://0177.0.0.1/",
        "http://0x7f.0.0.1/",
        "http://2130706433/",
        "http://127.1/",
        "http://1.2.3/",
    ],
)
def test_noncanonical_ipv4_forms_are_rejected(url: str) -> None:
    _expect_invalid(url, "valid public IP address")


def test_hostname_resolving_to_private_ip_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, "10.0.0.5")

    error = _expect_invalid("http://example.com/", "non-public IP address")
    assert "10.0.0.5" not in str(error)


def test_hostname_resolving_to_mixed_addresses_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV4, "10.0.0.5")

    _expect_invalid("http://example.com/", "non-public IP address")


def test_hostname_resolving_to_loopback_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, "127.0.0.1")

    _expect_invalid("http://localtest.me/", "non-public IP address")


def test_hostname_resolving_to_private_ipv6_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, "fd00::1")

    _expect_invalid("http://example.com/", "non-public IP address")


def test_scoped_ipv6_dns_result_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, "fe80::1%eth0")

    _expect_invalid("http://example.com/", "unsupported address")


def test_hostname_resolving_to_mixed_ipv6_addresses_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, PUBLIC_IPV6, "fd00::1")

    _expect_invalid("http://example.com/", "non-public IP address")


def test_dns_failure_is_rejected_without_leaking_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise socket.gaierror("internal resolver detail")

    monkeypatch.setattr(url_validation.socket, "getaddrinfo", fail)

    error = _expect_invalid("http://example.com/", "Could not resolve hostname")
    assert "internal resolver detail" not in str(error)


def test_empty_dns_result_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch)

    _expect_invalid("http://example.com/", "Could not resolve hostname")


def test_dns_result_with_invalid_address_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_dns(monkeypatch, "not-an-ip")

    _expect_invalid("http://example.com/", "unsupported address")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8080/",
        "https://example.com:8443/",
        "http://example.com:443/",
        "https://example.com:80/",
        "http://example.com:0/",
    ],
)
def test_unsupported_ports_are_rejected(url: str) -> None:
    _expect_invalid(url, "not supported for")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:abc/",
        "http://example.com:99999/",
        "http://example.com:/",
    ],
)
def test_invalid_ports_are_rejected(url: str) -> None:
    _expect_invalid(url, "invalid port")


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:080/",
        "https://example.com:0443/",
        "http://example.com:0080/",
        "http://example.com:00/",
        "http://[::1]:080/",
        "http://example.com:\u0668\u0660/",
    ],
)
def test_noncanonical_ports_are_rejected(url: str) -> None:
    _expect_invalid(url, "invalid port")


@pytest.mark.parametrize(
    ("url", "normalized", "port"),
    [
        ("http://example.com:80/", "http://example.com/", 80),
        ("https://example.com:443/", "https://example.com/", 443),
    ],
)
def test_explicit_default_ports_are_allowed_and_removed(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
    normalized: str,
    port: int,
) -> None:
    calls = _mock_dns(monkeypatch, PUBLIC_IPV4)

    result = validate_public_url(url)

    assert result.url == normalized
    assert result.port == port
    assert calls == [("example.com", port)]


@pytest.mark.parametrize("hostname", ["bücher.com", "BÜCHER.com"])
def test_idn_hostnames_are_normalized_to_punycode(
    monkeypatch: pytest.MonkeyPatch, hostname: str
) -> None:
    calls = _mock_dns(monkeypatch, PUBLIC_IPV4)

    result = validate_public_url(f"https://{hostname}/")

    assert result.hostname == "xn--bcher-kva.com"
    assert result.url == "https://xn--bcher-kva.com/"
    assert calls == [("xn--bcher-kva.com", 443)]


def test_unicode_dot_idn_hostname_is_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _mock_dns(monkeypatch, PUBLIC_IPV4)

    result = validate_public_url("https://bücher\u3002com/")

    assert result.hostname == "xn--bcher-kva.com"
    assert result.url == "https://xn--bcher-kva.com/"
    assert calls == [("xn--bcher-kva.com", 443)]


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost\u3002/",
        "http://\uff4c\uff4f\uff43\uff41\uff4c\uff48\uff4f\uff53\uff54/",
        "http://metadata.google.internal\u3002/",
        "http://printer\u3002local/",
    ],
)
def test_unicode_local_hostname_variants_are_rejected(url: str) -> None:
    _expect_invalid(url, "Local and internal hostnames")


@pytest.mark.parametrize(
    "url",
    [
        "http://bad_host.example/",
        "http://-bad.example/",
        "http://bad-.example/",
        "http://example..com/",
        "http://" + "a" * 64 + ".com/",
    ],
)
def test_invalid_hostnames_are_rejected(url: str) -> None:
    _expect_invalid(url, "Hostname is not valid")


@pytest.mark.parametrize(
    "url",
    [
        "http://[fe80::1%25eth0]/",
        "http://[::ffff:127.0.0.1%25eth0]/",
    ],
)
def test_ipv6_zone_identifiers_are_rejected(url: str) -> None:
    _expect_invalid(url, "invalid hostname")
