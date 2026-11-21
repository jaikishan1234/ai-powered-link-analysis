"""Public URL validation with SSRF protections.

This module validates URL syntax, normalizes hostnames, and performs a
snapshot DNS resolution that rejects non-public destinations. It does not
fetch anything and does not follow redirects.

Security limitations:

- DNS can change between validation and connection (DNS rebinding). The
  fetching layer must connect only to one of ``ValidatedURL.resolved_addresses``
  or re-resolve and re-validate immediately before connecting, and verify the
  peer address it actually connected to.
- Redirect targets are not followed here. The fetching layer must disable
  automatic redirects and validate every ``Location`` target with
  :func:`validate_public_url` before following it.
- Hostname IDNA conversion uses the standard library's IDNA 2003 codec, not
  full UTS #46. Confusable Unicode hostnames are not detected.

Callers must use the returned :class:`ValidatedURL` for the request and must
never re-parse the original raw input. Normalization (IDNA, ports, fragments)
means the raw input and the validated result can differ, so the fetching layer
must build the request from ``ValidatedURL.url``, use ``ValidatedURL.hostname``
for TLS SNI, and use ``ValidatedURL.host_header`` for the HTTP ``Host`` header.
"""

import ipaddress
import socket
import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_MAX_HOSTNAME_LENGTH = 253
_MAX_LABEL_LENGTH = 63

_LOCAL_HOSTNAMES = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
    }
)

_LOCAL_HOSTNAME_SUFFIXES = (
    ".localhost",
    ".local",
    ".localdomain",
    ".internal",
    ".home.arpa",
    ".lan",
    ".corp",
    ".private",
    ".intranet",
    ".arpa",
)

# Special-use TLDs reserved by RFC 6761 and RFC 7686. They must never resolve
# to a public web destination, so reject them even if DNS returns a public IP.
_RESERVED_HOSTNAME_SUFFIXES = (
    ".onion",
    ".test",
    ".example",
    ".invalid",
    ".alt",
)

_METADATA_HOSTNAMES = frozenset(
    {
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.internal",
        "instance-data",
        "instance-data.ec2.internal",
        "metadata.tencentyun.com",
        "metadata.alibabacloud.com",
        "metadata.aliyun.com",
    }
)

# Special-purpose ranges that Python's ipaddress module currently reports as
# globally reachable even though they are not public unicast destinations.
_NON_PUBLIC_NETWORKS = (
    ipaddress.ip_network("192.88.99.0/24"),  # Deprecated 6to4 relay anycast
    ipaddress.ip_network("2001:20::/28"),  # ORCHIDv2
    ipaddress.ip_network("2001:30::/28"),  # Drone Remote ID Protocol
)


class URLValidationError(ValueError):
    """Raised when a URL is malformed or is not safe to fetch."""


@dataclass(frozen=True, slots=True)
class ValidatedURL:
    """A URL that passed syntax, hostname, port, and public-IP checks.

    Callers must build the request from this normalized result and must never
    re-parse the original raw input, which can differ (IDNA, ports, fragments).

    ``url`` is the normalized absolute URL to request. ``hostname`` is the
    normalized ASCII/IDNA hostname without brackets (for IPv6, use
    :attr:`host_header` instead when building the HTTP ``Host`` header).

    ``resolved_addresses`` is a snapshot from validation time. The fetching
    layer must not treat it as a permanent guarantee: DNS can change before
    the connection is made. Connect only to these addresses, or re-resolve and
    re-validate immediately before connecting; use ``hostname`` for TLS SNI,
    use :attr:`host_header` for the Host header, and verify the connected peer.
    """

    url: str
    scheme: str
    hostname: str
    port: int
    resolved_addresses: tuple[IPAddress, ...]

    @property
    def host_header(self) -> str:
        """Return the HTTP ``Host`` header value.

        IPv6 hostnames are bracketed. The port is included only when it is
        not the scheme's default; current validation only permits the default
        ports, so the port is normally omitted.
        """
        return _format_netloc(self.scheme, self.hostname, self.port)


def validate_public_url(url: str) -> ValidatedURL:
    """Validate a URL and reject non-public destinations.

    Only HTTP and HTTPS URLs on their default ports are accepted. Hostnames
    are normalized to lowercase ASCII/IDNA form and resolved once; every
    resolved address must be public. This function performs blocking DNS
    resolution, so async callers should run it in a worker thread.

    The returned URL omits default ports and fragments.
    """
    if not isinstance(url, str):
        raise URLValidationError("URL must be a string.")

    candidate = url.strip()
    if not candidate:
        raise URLValidationError("URL must not be empty.")
    _reject_invalid_characters(candidate)

    try:
        parsed = urlsplit(candidate)
    except ValueError:
        raise URLValidationError("URL is malformed.") from None

    scheme = parsed.scheme.lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise URLValidationError("Only HTTP and HTTPS URLs are supported.")
    if not parsed.netloc:
        raise URLValidationError("URL must include a hostname.")

    try:
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
    except ValueError:
        raise URLValidationError("URL has an invalid hostname.") from None

    if username is not None or password is not None:
        raise URLValidationError("URLs with embedded credentials are not allowed.")
    if not hostname:
        raise URLValidationError("URL must include a hostname.")
    if parsed.netloc.endswith(":"):
        raise URLValidationError("URL has an invalid port.")

    try:
        port = parsed.port
    except ValueError:
        raise URLValidationError("URL has an invalid port.") from None

    port_text = _extract_port_text(parsed.netloc)
    if port_text is not None and not _is_canonical_port_text(port_text):
        raise URLValidationError("URL has an invalid port.")

    effective_port = _validate_port(scheme, port)
    normalized_hostname, resolved_addresses = _validate_hostname(
        hostname, effective_port
    )
    normalized_url = _build_normalized_url(
        scheme, normalized_hostname, effective_port, parsed.path, parsed.query
    )

    return ValidatedURL(
        url=normalized_url,
        scheme=scheme,
        hostname=normalized_hostname,
        port=effective_port,
        resolved_addresses=resolved_addresses,
    )


def _reject_invalid_characters(url: str) -> None:
    """Reject whitespace, control, and Unicode format characters."""
    for character in url:
        if (
            character == "\\"
            or character.isspace()
            or ord(character) < 0x20
            or ord(character) == 0x7F
            or unicodedata.category(character) == "Cf"
        ):
            raise URLValidationError("URL contains invalid characters.")


def _validate_port(scheme: str, port: int | None) -> int:
    """Return the effective port, allowing only the scheme's default port."""
    effective_port = _DEFAULT_PORTS[scheme] if port is None else port
    if effective_port != _DEFAULT_PORTS[scheme]:
        raise URLValidationError(
            f"Port {effective_port} is not supported for {scheme.upper()} URLs."
        )
    return effective_port


def _extract_port_text(netloc: str) -> str | None:
    """Return the raw port text from an authority, or ``None`` if absent.

    Credentials are rejected before this helper is called, so ``netloc`` is
    either ``host``, ``host:port``, ``[ipv6]``, or ``[ipv6]:port``.
    """
    if netloc.startswith("["):
        closing_bracket = netloc.find("]")
        remainder = netloc[closing_bracket + 1 :]
        return remainder[1:] if remainder.startswith(":") else None
    _, separator, port_text = netloc.rpartition(":")
    return port_text if separator else None


def _is_canonical_port_text(port_text: str) -> bool:
    """Return whether port text is plain ASCII decimal without zero padding."""
    if not port_text.isascii() or not port_text.isdigit():
        return False
    return not (len(port_text) > 1 and port_text.startswith("0"))


def _validate_hostname(
    hostname: str, port: int
) -> tuple[str, tuple[IPAddress, ...]]:
    """Validate a hostname and return its normalized form and addresses."""
    if "%" in hostname:
        raise URLValidationError("URL has an invalid hostname.")

    ip_literal = _parse_ip(hostname)
    if ip_literal is not None:
        if not _is_public_ip(ip_literal):
            raise URLValidationError("URL must use a public IP address.")
        return str(ip_literal), (ip_literal,)

    normalized_hostname = _normalize_hostname(hostname)
    _reject_noncanonical_ipv4(normalized_hostname)
    _reject_local_hostname(normalized_hostname)

    resolved_addresses = _resolve_hostname(normalized_hostname, port)
    if any(not _is_public_ip(address) for address in resolved_addresses):
        raise URLValidationError("URL resolves to a non-public IP address.")

    return normalized_hostname, resolved_addresses


def _parse_ip(value: str) -> IPAddress | None:
    """Parse a canonical IPv4 or IPv6 address, or return ``None``."""
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _normalize_hostname(hostname: str) -> str:
    """Normalize a hostname to lowercase ASCII/IDNA form."""
    if hostname.endswith("."):
        hostname = hostname[:-1]
    if not hostname:
        raise URLValidationError("URL must include a hostname.")

    try:
        ascii_hostname = hostname.lower().encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise URLValidationError("Hostname is not valid.") from None

    # IDNA maps some Unicode dot variants to an ASCII dot, which can leave a
    # trailing dot after the earlier normalization step.
    if ascii_hostname.endswith("."):
        ascii_hostname = ascii_hostname[:-1]
    if not ascii_hostname:
        raise URLValidationError("URL must include a hostname.")

    if len(ascii_hostname) > _MAX_HOSTNAME_LENGTH:
        raise URLValidationError("Hostname is not valid.")

    for label in ascii_hostname.split("."):
        if not label or len(label) > _MAX_LABEL_LENGTH:
            raise URLValidationError("Hostname is not valid.")
        if label.startswith("-") or label.endswith("-"):
            raise URLValidationError("Hostname is not valid.")
        if not all(character.isalnum() or character == "-" for character in label):
            raise URLValidationError("Hostname is not valid.")

    return ascii_hostname


def _reject_noncanonical_ipv4(hostname: str) -> None:
    """Reject shorthand, octal, and hexadecimal IPv4 representations."""
    try:
        socket.inet_aton(hostname)
    except OSError:
        return
    raise URLValidationError("URL must use a valid public IP address.")


def _reject_local_hostname(hostname: str) -> None:
    """Reject single-label, local-only, metadata, and reserved hostnames."""
    if "." not in hostname:
        raise URLValidationError("Local and internal hostnames are not allowed.")
    if hostname in _LOCAL_HOSTNAMES or hostname in _METADATA_HOSTNAMES:
        raise URLValidationError("Local and internal hostnames are not allowed.")
    if any(hostname.endswith(suffix) for suffix in _LOCAL_HOSTNAME_SUFFIXES):
        raise URLValidationError("Local and internal hostnames are not allowed.")
    if any(hostname.endswith(suffix) for suffix in _RESERVED_HOSTNAME_SUFFIXES):
        raise URLValidationError("Reserved hostnames are not allowed.")


def _resolve_hostname(hostname: str, port: int) -> tuple[IPAddress, ...]:
    """Resolve a hostname once and return its unique IP addresses."""
    try:
        results = socket.getaddrinfo(
            hostname, port, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except (OSError, UnicodeError):
        raise URLValidationError("Could not resolve hostname.") from None

    addresses: list[IPAddress] = []
    for _, _, _, _, sockaddr in results:
        raw_address = sockaddr[0]
        if "%" in raw_address:
            raise URLValidationError(
                "Hostname resolved to an unsupported address."
            ) from None
        address = _parse_ip(raw_address)
        if address is None:
            raise URLValidationError(
                "Hostname resolved to an unsupported address."
            ) from None
        if address not in addresses:
            addresses.append(address)

    if not addresses:
        raise URLValidationError("Could not resolve hostname.")
    return tuple(addresses)


def _is_public_ip(address: IPAddress) -> bool:
    """Return whether an address is a public unicast destination."""
    candidate: IPAddress = address
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        candidate = address.ipv4_mapped

    if any(candidate in network for network in _NON_PUBLIC_NETWORKS):
        return False
    if isinstance(candidate, ipaddress.IPv6Address) and candidate.is_site_local:
        return False

    return candidate.is_global and not (
        candidate.is_private
        or candidate.is_loopback
        or candidate.is_link_local
        or candidate.is_reserved
        or candidate.is_multicast
        or candidate.is_unspecified
    )


def _format_host(hostname: str) -> str:
    """Return a hostname formatted for a URL authority (IPv6 bracketed)."""
    return f"[{hostname}]" if ":" in hostname else hostname


def _format_netloc(scheme: str, hostname: str, port: int) -> str:
    """Return an authority, including the port only when it is non-default."""
    host = _format_host(hostname)
    return host if port == _DEFAULT_PORTS[scheme] else f"{host}:{port}"


def _build_normalized_url(
    scheme: str, hostname: str, port: int, path: str, query: str
) -> str:
    """Build a normalized URL without credentials, fragments, or default ports."""
    netloc = _format_netloc(scheme, hostname, port)
    return urlunsplit((scheme, netloc, path, query, ""))
