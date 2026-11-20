"""Validated transport-neutral HTTP values and ownership-independent metadata.

This module does not open sockets, store native handles, or assume an event loop.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import re
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit
from secrets import token_hex

from valiance.std._http_body import UploadPipe, MultipartUpload, StreamingUpload
from valiance.std._http_errors import HttpValueError

_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_BAD_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")
_BAD_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def validate_method(method: str) -> str:
    """Keep extension methods while rejecting whitespace and wire delimiters."""
    if not isinstance(method, str) or not _TOKEN.fullmatch(method):
        raise HttpValueError("HTTP method must be a nonempty ASCII token")
    return method


def validate_url(url: str) -> str:
    """Normalize a full HTTP(S) URL without silently changing the target path.

    Hostnames are converted to IDNA ASCII and IPv6 literals are bracketed;
    malformed escapes, controls, userinfo, fragments, and ambiguous authorities
    are rejected. The application is responsible for authorizing destinations.
    """
    if not isinstance(url, str) or _BAD_CONTROL.search(url) or "\\" in url:
        raise HttpValueError("URL contains a control character or backslash")
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise HttpValueError("malformed URL authority") from exc
    if parsed.scheme.lower() not in {"http", "https"}:
        raise HttpValueError("only absolute HTTP and HTTPS URLs are supported")
    if not parsed.netloc or parsed.hostname is None:
        raise HttpValueError("HTTP URL must have a host")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise HttpValueError("userinfo in an HTTP URL is not permitted")
    if parsed.fragment or "#" in url:
        raise HttpValueError("URL fragments are not permitted in requests")
    if _BAD_ESCAPE.search(parsed.path) or _BAD_ESCAPE.search(parsed.query):
        raise HttpValueError("malformed percent escape in URL")
    try:
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("port out of range")
        hostname = parsed.hostname
        assert hostname is not None
        if ":" in hostname:
            hostname = f"[{ipaddress.IPv6Address(hostname).compressed}]"
        else:
            hostname = hostname.encode("idna").decode("ascii").lower()
            if not hostname or any(not part or len(part) > 63 for part in hostname.split(".")):
                raise ValueError("invalid DNS name")
    except (UnicodeError, ValueError) as exc:
        raise HttpValueError("invalid HTTP URL host or port") from exc
    authority = hostname + (f":{port}" if port is not None else "")
    # Escapes are preserved, including the distinction between '+' and '%20'.
    return urlunsplit((parsed.scheme.lower(), authority, parsed.path or "/", parsed.query, ""))


def _validate_header(name: str, value: str) -> tuple[str, str]:
    """Validate one HTTP field name and value against header-injection rules."""
    if not isinstance(name, str) or not _TOKEN.fullmatch(name):
        raise HttpValueError("HTTP header name must be an ASCII token")
    if not isinstance(value, str) or _BAD_CONTROL.search(value):
        raise HttpValueError("HTTP header value contains a control character")
    try:
        value.encode("latin-1")
    except UnicodeEncodeError as exc:
        raise HttpValueError("HTTP header values must be Latin-1 representable") from exc
    return name, value


@dataclass(frozen=True, slots=True)
class Headers:
    """Immutable ordered multi-map, preserving duplicate fields and spelling."""

    pairs: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Normalize the immutable ordered collection while retaining duplicate header fields."""
        object.__setattr__(self, "pairs", tuple(_validate_header(*pair) for pair in self.pairs))

    def values(self, name: str) -> tuple[str, ...]:
        """Return all case-insensitive matches without collapsing repeated HTTP fields."""
        return tuple(value for key, value in self.pairs if key.casefold() == name.casefold())

    def one(self, name: str) -> str | None:
        """Return a unique header value, rejecting ambiguous repeated values."""
        values = self.values(name)
        if len(values) > 1:
            raise HttpValueError(f"ambiguous repeated header {name!r}")
        return values[0] if values else None

    def append(self, name: str, value: str) -> Headers:
        """Return a new collection with an additional validated header pair."""
        return Headers((*self.pairs, _validate_header(name, value)))

    def replace(self, name: str, value: str) -> Headers:
        """Return a new collection replacing all values of the named field."""
        return Headers(tuple(pair for pair in self.pairs if pair[0].casefold() != name.casefold()) + (_validate_header(name, value),))


@dataclass(frozen=True, slots=True)
class ClientConfig:
    """Validated limits and default policies shared across transfers."""

    max_connections: int = 64
    max_connections_per_origin: int = 8
    max_pending: int = 1024
    body_buffer_bytes: int = 256 * 1024
    max_client_buffer_bytes: int = 2 * 1024 * 1024
    max_response_header_bytes: int = 128 * 1024
    max_response_header_fields: int = 256
    max_body_bytes: int = 16 * 1024 * 1024
    max_buffered_upload_bytes: int = 16 * 1024 * 1024
    connect_timeout_ms: int = 10_000
    # Opt-in deadline for obtaining an assigned libcurl connection.
    connection_acquire_timeout_ms: int | None = None
    total_timeout_ms: int = 60_000
    read_idle_timeout_ms: int | None = None
    write_idle_timeout_ms: int | None = None
    follow_redirects: bool = False
    max_redirects: int = 5
    allow_downgrade: bool = False
    decompress: bool = True
    enable_cookies: bool = False
    require_https: bool = False
    deny_non_public_ips: bool = False
    allowed_origins: tuple[str, ...] | None = None
    proxy: str | None = None
    ca_bundle: str | None = None

    def __post_init__(self) -> None:
        """Reject invalid client limits and contradictory security or redirect policies."""
        for name in ("max_connections", "max_connections_per_origin", "max_pending", "body_buffer_bytes", "max_client_buffer_bytes", "max_response_header_bytes", "max_response_header_fields", "max_body_bytes", "max_buffered_upload_bytes", "connect_timeout_ms", "total_timeout_ms"):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise HttpValueError(f"{name} must be a positive integer")
        if self.max_client_buffer_bytes < 16 * 1024:
            raise HttpValueError("max_client_buffer_bytes must allow at least one transport chunk (16384 bytes)")
        for name in ("connection_acquire_timeout_ms", "read_idle_timeout_ms", "write_idle_timeout_ms"):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 1):
                raise HttpValueError(f"{name} must be a positive integer or None")
        for name in ("follow_redirects", "allow_downgrade", "decompress", "enable_cookies", "require_https", "deny_non_public_ips"):
            if type(getattr(self, name)) is not bool:
                raise HttpValueError(f"{name} must be a Boolean")
        if self.max_connections_per_origin > self.max_connections:
            raise HttpValueError("per-origin connections cannot exceed total connections")
        if type(self.max_redirects) is not int or not 0 <= self.max_redirects <= 50:
            raise HttpValueError("max_redirects must be in 0..50")
        if self.body_buffer_bytes > self.max_body_bytes:
            raise HttpValueError("body buffer limit cannot exceed complete-body limit")
        if self.follow_redirects and self.max_redirects == 0:
            raise HttpValueError("redirect policy requires a positive max_redirects")
        if self.allowed_origins is not None:
            if not isinstance(self.allowed_origins, tuple) or not self.allowed_origins:
                raise HttpValueError("allowed_origins must be a nonempty tuple")
            normalized = []
            for origin in self.allowed_origins:
                validated = validate_url(origin)
                parts = urlsplit(validated)
                if parts.path != '/' or parts.query:
                    raise HttpValueError("allowlisted origins cannot have paths or queries")
                normalized.append(validated)
            object.__setattr__(self, 'allowed_origins', tuple(normalized))
        if self.proxy is not None:
            validate_url(self.proxy)
            if self.deny_non_public_ips:
                raise HttpValueError("IP restriction is incompatible with a proxy")
        if self.ca_bundle is not None and (not isinstance(self.ca_bundle, str) or not self.ca_bundle):
            raise HttpValueError("ca_bundle must be a nonempty path")


@dataclass(frozen=True, slots=True)
class Request:
    """Validated outbound request with buffered or single-use streamed content."""

    method: str
    url: str
    headers: Headers = field(default_factory=Headers)
    body: bytes | StreamingUpload | None = None

    def __post_init__(self) -> None:
        """Validate the request method, URL, headers, and declared body length."""
        object.__setattr__(self, "method", validate_method(self.method))
        object.__setattr__(self, "url", validate_url(self.url))
        if not isinstance(self.headers, Headers):
            raise HttpValueError("headers must be a Headers value")
        if self.body is not None and not isinstance(self.body, (bytes, UploadPipe, MultipartUpload)):
            raise HttpValueError("request body must be bytes or an upload source")
        lengths = self.headers.values("content-length")
        expected = (self.body.expected_bytes if isinstance(self.body, (UploadPipe, MultipartUpload)) else
                    len(self.body or b""))
        if len(lengths) > 1 or (lengths and (not lengths[0].isdigit() or
                                    expected is None or int(lengths[0]) != expected)):
            raise HttpValueError("conflicting or incorrect content length")
        if self.headers.values("transfer-encoding"):
            raise HttpValueError("transfer-encoding is managed by the transport")
        if self.headers.values("host"):
            raise HttpValueError("Host/authority is managed by the transport")


@dataclass(frozen=True, slots=True)
class ResponseMetadata:
    status: int
    final_url: str
    headers: Headers
    protocol: str


def encode_query(pairs: tuple[tuple[str, str], ...]) -> str:
    """Percent-encode ordered pairs without collapsing repeated query keys."""
    return "&".join(f"{quote(key, safe='~')}={quote(value, safe='~')}" for key, value in pairs)


# The buffered encoders have a conservative absolute ceiling in addition to
# ClientConfig.max_buffered_upload_bytes. Use UploadPipe for larger requests.
_FORM_ENCODING_LIMIT = 16 * 1024 * 1024


def encode_form(pairs: tuple[tuple[str, str], ...], limit: int = _FORM_ENCODING_LIMIT) -> bytes:
    """application/x-www-form-urlencoded, retaining ordering and repeats."""
    if type(limit) is not int or limit < 1 or limit > _FORM_ENCODING_LIMIT:
        raise HttpValueError("invalid form encoding size limit")
    result = bytearray()
    for name, value in pairs:
        if not isinstance(name, str) or not isinstance(value, str):
            raise HttpValueError("form fields must be text")
        encoded = (quote_plus(name, safe='~') + '=' + quote_plus(value, safe='~')).encode('ascii')
        if len(result) + len(encoded) + bool(result) > limit:
            raise HttpValueError("form encoding exceeds maximum buffered upload size")
        if result:
            result.extend(b'&')
        result.extend(encoded)
    return bytes(result)


@dataclass(frozen=True, slots=True)
class MultipartPart:
    name: str
    data: bytes | UploadPipe
    filename: str | None = None
    content_type: str | None = None


def _disposition_value(value: str) -> str:
    """RFC 7578 percent-encoding prevents quote/CRLF parameter injection."""
    if not isinstance(value, str) or not value or any(ord(x) < 32 or ord(x) == 127 for x in value):
        raise HttpValueError("multipart part name/filename must be nonempty printable text")
    return quote(value, safe=' -._~')


def _multipart_boundary(boundary: str | None) -> str:
    """Choose or validate a safe multipart MIME boundary token."""
    if boundary is None:
        boundary = 'valiance-' + token_hex(18)
    if not isinstance(boundary, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,70}', boundary):
        raise HttpValueError("invalid multipart boundary")
    return boundary


def _multipart_fragments(parts: tuple[MultipartPart, ...], boundary: str,
                         *, streaming: bool):
    """Use identical MIME validation and framing for buffered and streamed bodies."""
    for part in parts:
        allowed = (bytes, UploadPipe) if streaming else (bytes,)
        if not isinstance(part, MultipartPart) or not isinstance(part.data, allowed):
            raise HttpValueError("invalid multipart part")
        name = _disposition_value(part.name)
        if part.content_type is not None:
            # A complete MIME type, not a freeform header containing CR/LF.
            if not re.fullmatch(
                    r'[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+(?:; charset=[A-Za-z0-9_-]+)?',
                    part.content_type):
                raise HttpValueError("invalid multipart content type")
        disposition = 'Content-Disposition: form-data; name="' + name + '"'
        if part.filename is not None:
            disposition += '; filename="' + _disposition_value(part.filename) + '"'
        prefix = '--' + boundary + '\r\n' + disposition + '\r\n'
        if part.content_type is not None:
            prefix += 'Content-Type: ' + part.content_type + '\r\n'
        yield (prefix + '\r\n').encode('ascii')
        yield part.data
        yield b'\r\n'
    yield ('--' + boundary + '--\r\n').encode('ascii')


def encode_multipart(parts: tuple[MultipartPart, ...],
                     limit: int = _FORM_ENCODING_LIMIT,
                     boundary: str | None = None) -> tuple[bytes, str]:
    """Return a bounded multipart body and its transport-owned Content-Type."""
    if type(limit) is not int or not 1 <= limit <= _FORM_ENCODING_LIMIT:
        raise HttpValueError("invalid multipart encoding size limit")
    boundary = _multipart_boundary(boundary)
    wire = bytearray()
    for fragment in _multipart_fragments(parts, boundary, streaming=False):
        if len(wire) + len(fragment) > limit:
            raise HttpValueError("multipart body exceeds maximum buffered upload size")
        wire.extend(fragment)
    return bytes(wire), 'multipart/form-data; boundary=' + boundary


def encode_multipart_stream(parts: tuple[MultipartPart, ...],
                            boundary: str | None = None) -> tuple[MultipartUpload, str]:
    """Compose MIME framing with one-shot upload sources without reading files.

    Buffered segments still obey the same absolute 16 MiB limit as ordinary
    multipart encoding. Streamed part lengths determine the full content length
    when supplied; otherwise curl uses upload streaming framing.
    """
    boundary = _multipart_boundary(boundary)
    segments: list[bytes | UploadPipe] = []
    buffered = 0
    for fragment in _multipart_fragments(parts, boundary, streaming=True):
        if isinstance(fragment, bytes):
            buffered += len(fragment)
            if buffered > _FORM_ENCODING_LIMIT:
                raise HttpValueError("multipart framing/buffered parts exceed maximum upload size")
        segments.append(fragment)
    return MultipartUpload(tuple(segments)), 'multipart/form-data; boundary=' + boundary


def url_with_query(url: str, pairs: tuple[tuple[str, str], ...]) -> str:
    """Append ordered, repeated query fields without changing existing escapes."""
    parsed = urlsplit(validate_url(url))
    appended = encode_query(pairs)
    query = (parsed.query + '&' if parsed.query and appended else parsed.query) + appended
    return validate_url(urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, '')))
