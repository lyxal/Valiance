"""Transport-independent HTTP application core.

This module deliberately has no socket or protocol engine.  All logical
requests (including those made by future HTTP/1, HTTP/2 and HTTP/3 adapters)
pass through the same validated target, routing and middleware pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from email.utils import formatdate
import gzip
import hashlib
from enum import Enum
import json
import logging
import time
from threading import local
import ipaddress
import re
from typing import Callable, Iterable, Mapping
from urllib.parse import quote, unquote_to_bytes, urlsplit

_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "QUERY")
_TOKEN = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_CAPTURE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*$")
_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_ENCODED_SEPARATOR = re.compile(r"%(?:2f|5c)", re.I)
_DOUBLE_SEPARATOR = re.compile(r"%25(?:2e|2f|5c)", re.I)
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_FORBIDDEN_RESPONSE_HEADERS = frozenset({"connection", "transfer-encoding", "upgrade", "keep-alive", "proxy-connection"})
_SINGLETON_HEADERS = frozenset({"content-type", "content-length", "host", "location", "authorization", "www-authenticate"})


class ConfigError(ValueError):
    """Invalid application structure; not an HTTP response."""


class DispatchError(ValueError):
    """Invalid socket-free test envelope, not an HTTP response."""


@dataclass
class WebError(Exception):
    name: str
    message: str = ""
    allowed: tuple[str, ...] = ()
    stage: str = "Handler"

    @property
    def status(self) -> int:
        """Map the error category to its HTTP status."""
        return _ERROR_STATUS.get(self.name, 500)


_ERROR_STATUS = dict.fromkeys((
    "BadRequest", "MissingParam", "AmbiguousParam", "InvalidHeader",
    "AmbiguousHeader", "InvalidCookie", "AmbiguousCookie", "InvalidForm",
    "InvalidMultipart", "InvalidJson", "BadText", "MalformedTarget"), 400)
_ERROR_STATUS.update(dict(
    Unauthorized=401, Forbidden=403, NotFound=404, MethodNotAllowed=405, UpgradeRequired=426,
    NotAcceptable=406, RequestTimeout=408, Conflict=409, Gone=410,
    PreconditionFailed=412, BodyTooLarge=413, TargetTooLong=414,
    UnsupportedMediaType=415, RangeNotSatisfiable=416,
    UnprocessableContent=422, TooEarly=425, PreconditionRequired=428,
    TooManyRequests=429, HeadersTooLarge=431, MisdirectedRequest=421, WrongMode=500, NotImplemented=501,
    BadGateway=502, ServiceUnavailable=503, GatewayTimeout=504))


@dataclass(frozen=True)
class Params:
    """Immutable ordered pair multimap: repeat names never get overwritten."""
    pairs: tuple[tuple[str, str], ...] = ()

    def values(self, name: str) -> tuple[str, ...]:
        """Return all values for the exact parameter name in arrival order."""
        return tuple(value for key, value in self.pairs if key == name)

    def one(self, name: str) -> str | None:
        """Return the sole value, rejecting ambiguous duplicates."""
        found = self.values(name)
        if len(found) > 1:
            raise WebError("AmbiguousParam", "Parameter appears more than once")
        return found[0] if found else None

    def require(self, name: str) -> str:
        """Require exactly one value for the parameter."""
        value = self.one(name)
        if value is None:
            raise WebError("MissingParam", "Required parameter is missing")
        return value

    def append(self, name: str, value: str) -> Params:
        """Return a copy with another occurrence appended."""
        return Params((*self.pairs, (name, value)))

    def replace(self, name: str, value: str) -> Params:
        """Replace every occurrence with one new value."""
        return Params(tuple(pair for pair in self.pairs if pair[0] != name) + ((name, value),))

    def render_query(self) -> str:
        """Encode the ordered pairs as a query string."""
        return "&".join(f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in self.pairs)


def _percent_decode(value: str, *, form: bool = False) -> str:
    """Decode percent escapes and optionally treat plus as a space."""
    if _PERCENT.search(value):
        raise WebError("MalformedTarget", "Malformed percent encoding")
    if form:
        value = value.replace("+", " ")
    try:
        decoded = unquote_to_bytes(value).decode("utf-8", errors="strict")
    except (UnicodeError, ValueError) as exc:
        raise WebError("MalformedTarget", "Invalid UTF-8") from exc
    if _CONTROL.search(decoded):
        raise WebError("MalformedTarget", "Forbidden control character")
    return decoded


def parse_params(value: str, *, form: bool = False, max_fields: int = 1024) -> Params:
    """Parse an ordered query or form parameter multimap."""
    if not value:
        return Params()
    pieces = value.split("&")
    if len(pieces) > max_fields:
        raise WebError("BadRequest", "Too many parameters")
    result = []
    for part in pieces:
        key, separator, val = part.partition("=")
        result.append((_percent_decode(key, form=form),
                       _percent_decode(val if separator else "", form=form)))
    return Params(tuple(result))


def validate_target(raw: str) -> tuple[str, Params]:
    """One strict decoding pass, shared by dispatch and transport adapters."""
    if not isinstance(raw, str) or not raw.startswith("/") or raw.startswith("//"):
        raise WebError("MalformedTarget", "Expected an origin-form request target")
    if "#" in raw or len(raw.encode("utf-8")) > 8192:
        raise WebError("MalformedTarget", "Fragment or excessive request target")
    path, _, query = raw.partition("?")
    if _CONTROL.search(path) or "\\" in path:
        raise WebError("MalformedTarget", "Invalid path")
    parts = path.split("/")[1:]
    decoded = []
    for segment in parts:
        if _ENCODED_SEPARATOR.search(segment) or _DOUBLE_SEPARATOR.search(segment):
            raise WebError("MalformedTarget", "Encoded path separator")
        value = _percent_decode(segment)
        if value in (".", "..") or "/" in value or "\\" in value:
            raise WebError("MalformedTarget", "Ambiguous path segment")
        decoded.append(value)
    return "/" + "/".join(decoded), parse_params(query)


def _validate_header(name: str, value: str) -> None:
    """Reject invalid header field names and values."""
    if not _TOKEN.fullmatch(name) or _CONTROL.search(value):
        raise WebError("InvalidHeader", "Invalid header name or value")


@dataclass(frozen=True)
class Headers:
    pairs: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        """Validate each header pair at construction."""
        for name, value in self.pairs:
            _validate_header(name, value)

    def values(self, name: str) -> tuple[str, ...]:
        """Return all occurrences of a case-insensitive field name."""
        return tuple(v for k, v in self.pairs if k.lower() == name.lower())

    def one(self, name: str) -> str | None:
        """Return a single field value and reject duplicate occurrences."""
        values = self.values(name)
        if len(values) > 1 and name.lower() in _SINGLETON_HEADERS:
            raise WebError("AmbiguousHeader", "Conflicting singleton field")
        if name.lower() == "set-cookie" and len(values) > 1:
            raise WebError("AmbiguousHeader", "Use headerValues for Set-Cookie")
        return values[0] if values else None

    def with_header(self, name: str, value: str) -> Headers:
        """Replace all instances of a header with one value."""
        _validate_header(name, value)
        return Headers(tuple((k, v) for k, v in self.pairs if k.lower() != name.lower()) + ((name, value),))

    def add(self, name: str, value: str) -> Headers:
        """Append a header field without merging occurrences."""
        _validate_header(name, value)
        if name.lower() in _SINGLETON_HEADERS and self.values(name):
            raise WebError("InvalidHeader", "Duplicate singleton field")
        return Headers((*self.pairs, (name, value)))

    def without(self, name: str) -> Headers:
        """Remove all occurrences of a header field."""
        return Headers(tuple((k, v) for k, v in self.pairs if k.lower() != name.lower()))


def _validate_template(path: str) -> tuple[tuple[str, str], ...]:
    """Validate and split a route template into segments."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ConfigError("Route path must begin with /")
    if path == "/":
        return ()
    parts = path[1:].split("/")
    if any(not part for part in parts):
        raise ConfigError("Empty path segments are not valid route templates")
    segments = []
    captures = set()
    for pos, segment in enumerate(parts):
        if segment.startswith("{") and segment.endswith("}"):
            name = segment[1:-1]
            catchall = name.startswith("*")
            if catchall:
                name = name[1:]
            if not _CAPTURE.fullmatch(name) or name in captures:
                raise ConfigError("Invalid or duplicate capture name")
            if catchall and pos != len(parts) - 1:
                raise ConfigError("Catch-all must be terminal")
            captures.add(name)
            segments.append(("catchall" if catchall else "capture", name))
        else:
            if any(ch in segment for ch in "{}?#\\") or _CONTROL.search(segment):
                raise ConfigError("Invalid literal route segment")
            segments.append(("literal", segment))
    return tuple(segments)


def _path_segments(path: str) -> tuple[str, ...]:
    """Split a path into its routing segments."""
    return tuple(path[1:].split("/")) if path != "/" else ()


def _match(template: tuple[tuple[str, str], ...], path: str) -> dict[str, str] | None:
    """Match a path and return its named captures."""
    values = _path_segments(path)
    if len(values) < len(template) - (1 if template and template[-1][0] == "catchall" else 0):
        return None
    result = {}
    i = 0
    for kind, value in template:
        if kind == "catchall":
            result[value] = "/".join(values[i:])
            return result
        if i >= len(values) or (kind == "literal" and values[i] != value):
            return None
        if kind == "capture":
            result[value] = values[i]
        i += 1
    return result if i == len(values) else None


def _specificity(template: tuple[tuple[str, str], ...]) -> tuple[int, ...]:
    """Rank route segments by their matching specificity."""
    weights = {"literal": 4, "capture": 3, "catchall": 1}
    return tuple(weights[kind] for kind, _ in template) + (2,)


def _overlap_equal_specificity(a: tuple[tuple[str, str], ...], b: tuple[tuple[str, str], ...]) -> bool:
    """Equal specificity iff equal segment kinds and compatible literal names."""
    return (len(a) == len(b) and all(
        k1 == k2 and (k1 != "literal" or v1 == v2)
        for (k1, v1), (k2, v2) in zip(a, b)))


class RequestBodyStream:
    """Linear, bounded request-body reader. No eager collection or replay.

    `close` releases the reader; the transport must close the connection when
    an unread request has not reached its protocol EndOfMessage marker.
    """
    def __init__(self, source: object, *, max_bytes: int = 1024 ** 3,
                 synthetic_trailers: Headers | None = None,
                 initial_headers: Headers | None = None):
        """Track an incoming body and its limits for one consumer."""
        from threading import RLock
        if not callable(getattr(source, 'read', None)) or not callable(getattr(source, 'close', None)):
            raise DispatchError('Streaming request source requires read and close')
        if type(max_bytes) is not int or not 0 < max_bytes <= 1024 ** 3:
            raise DispatchError('Invalid streaming request body limit')
        self.source = source
        self.max_bytes = max_bytes
        # Do not expose these fields until protocol EOF; a stream can be
        # closed early without ever accepting its trailer block.
        self.trailer_fields = Headers()
        self._initial_headers = initial_headers or Headers()
        self._synthetic_trailers = (validate_request_trailers(
            synthetic_trailers, initial=self._initial_headers)
            if synthetic_trailers is not None else None)
        self.total = 0
        self.claimed = False
        self.closed = False
        self.complete = False
        self._empty_reads = 0
        self._lock = RLock()

    def claim(self) -> RequestBodyStream:
        """Claim exclusive access to the incoming body."""
        with self._lock:
            if self.claimed or self.closed:
                raise WebError('BodyUnavailable', 'Request stream already claimed or closed')
            self.claimed = True
            return self

    def read(self, max_bytes: int) -> bytes | None:
        """Read a bounded chunk and track completion of the body."""
        if type(max_bytes) is not int or not 0 < max_bytes <= 65536:
            raise WebError('BadRequest', 'Request stream read must be between 1 and 65536 bytes')
        with self._lock:
            if self.closed:
                raise WebError('BodyUnavailable', 'Request stream is closed')
            if not self.claimed:
                raise WebError('BodyUnavailable', 'Request stream has not been claimed')
            try:
                chunk = self.source.read(max_bytes)
                if chunk is None:
                    tail = (self._synthetic_trailers if self._synthetic_trailers is not None
                            else getattr(self.source, 'trailers', Headers()))
                    self.trailer_fields = validate_request_trailers(
                        tail, initial=self._initial_headers)
                    self.complete = True
                    self.close()
                    return None
                if not isinstance(chunk, bytes) or len(chunk) > max_bytes:
                    raise WebError('BadRequest', 'Invalid request body source output')
                self.total += len(chunk)
                if self.total > self.max_bytes:
                    raise WebError('BodyTooLarge', 'Streaming request exceeds configured limit')
                if not chunk:
                    self._empty_reads += 1
                    if self._empty_reads > 16:
                        raise WebError('BadRequest', 'Request source made no progress')
                else:
                    self._empty_reads = 0
                return chunk
            except BaseException:
                self.close()
                raise

    def discard(self) -> None:
        """Consume the remaining body within the discard budget."""
        with self._lock:
            if self.closed:
                return
            if not self.claimed:
                self.claim()
            while self.read(65536) is not None:
                pass

    def close(self) -> None:
        """Release the incoming source and notify close listeners."""
        with self._lock:
            if not self.closed:
                self.closed = True
                try:
                    self.source.close()
                finally:
                    callback = getattr(self, '_on_close', None)
                    if callback is not None:
                        callback()


def test_request_stream(method: str, target: str, source: object, *,
                        headers: Headers | None = None,
                        max_bytes: int = 1024 ** 3,
                        trailers: Headers | None = None) -> Request:
    """Construct a test request backed by an incoming body stream."""
    if method not in ('POST', 'PUT', 'PATCH', 'QUERY'):
        raise DispatchError('Only POST, PUT, PATCH, QUERY support streaming requests')
    request = test_request(method, target, headers=headers)
    return replace(request, body_stream=RequestBodyStream(
        source, max_bytes=max_bytes, synthetic_trailers=trailers,
        initial_headers=request.headers))


def body_stream(request: Request) -> RequestBodyStream:
    """Claim the stream belonging to a body-capable request."""
    _body_capable(request)
    if request.body_stream is None:
        raise WebError('BodyUnavailable', 'Request is not in streaming mode')
    return request.body_stream.claim()


def trailers(request: Request) -> Headers:
    """Read validated request trailers only after a streamed body reaches EOF.

    Never block to fetch trailers: the application must explicitly drain or
    discard the body first. Initial headers are not merged with trailers.
    """
    _body_capable(request)
    if request.body_stream is None:
        raise WebError('WrongMode', 'Request trailers require a streaming body')
    if not request.body_stream.complete:
        raise WebError('BodyUnavailable', 'Request trailers require body EOF')
    return request.body_stream.trailer_fields


@dataclass(frozen=True)
class MultipartLimits:
    """Immutable multipart parsing budgets; independent of the route body cap.

    The six public server knobs are copied into each request; the additional
    file-count and MIME-header quotas are conservative implementation bounds.
    Buffered uploads are further constrained by the buffered body limit.
    """
    max_parts: int = 128
    max_fields: int = 1000
    max_files: int = 100
    max_field_bytes: int = 1024 * 1024
    max_file_bytes: int = 256 * 1024 * 1024
    max_name_bytes: int = 1024
    max_filename_bytes: int = 4096
    max_header_bytes: int = 16384

    def __post_init__(self) -> None:
        """Validate the limits applied to multipart parsing."""
        for name, value in vars(self).items():
            if type(value) is not int or not 0 < value <= 1024 ** 3:
                raise ConfigError(f'Invalid multipart quota: {name}')


def _multipart_name_limits(disposition: Mapping[str, str], *,
                           max_name_bytes: int, max_filename_bytes: int) -> None:
    """Check UTF-8 byte lengths, never characters, before exposing part names."""
    if len(disposition['name'].encode('utf-8')) > max_name_bytes:
        raise WebError('BodyTooLarge', 'Multipart field name exceeds limit')
    if ('filename' in disposition and
            len(disposition['filename'].encode('utf-8')) > max_filename_bytes):
        raise WebError('BodyTooLarge', 'Multipart filename exceeds limit')


class ContextKey:
    """Opaque request-context key. Identity, not its label or type, controls lookup.

    Create a separate key for each independently scoped value. The request owns
    its immutable context snapshot; there is no process-wide value registry.
    """

    __slots__ = ()


def context(request: Request, key: ContextKey) -> object | None:
    """Read a request-local value through the exact key that inserted it."""
    if not isinstance(key, ContextKey):
        raise TypeError('Request context requires a ContextKey')
    return request.context.get(key)


def require_context(request: Request, key: ContextKey) -> object:
    """Read a required value without conflating absence with None."""
    if not isinstance(key, ContextKey):
        raise TypeError('Request context requires a ContextKey')
    if key not in request.context:
        raise WebError('MissingContext', 'Required request context value is missing')
    return request.context[key]


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    raw_target: str
    query: Params
    headers: Headers = field(default_factory=Headers)
    original_method: str = ""
    route_params: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""
    body_stream: RequestBodyStream | None = None
    context: Mapping[object, object] = field(default_factory=dict)
    protocol: str = "Test"
    scheme: str = "http"
    authority: str = "localhost"
    request_id: str = "test"
    multipart_limits: MultipartLimits = field(default_factory=MultipartLimits)
    peer: str = ""
    client_address: str = ""

    def capture(self, name: str) -> str:
        """Retrieve a named capture or raise a missing-parameter error."""
        if name not in self.route_params:
            raise WebError("MissingParam", "Route capture is missing")
        return self.route_params[name]

    def with_context(self, key: object, value: object) -> Request:
        """Return a request with updated context metadata."""
        return replace(self, context={**self.context, key: value})


def test_request(method: str, target: str, *, headers: Headers | None = None,
                 body: bytes = b"", authority: str = "localhost") -> Request:
    """Construct a buffered request for in-process dispatch."""
    if not isinstance(method, str) or not _TOKEN.fullmatch(method):
        raise DispatchError("Invalid synthetic method")
    if not isinstance(body, bytes):
        raise DispatchError("Synthetic body must be bytes")
    try:
        path, query = validate_target(target)
        if not authority or _CONTROL.search(authority) or any(c in authority for c in " /\\@"):
            raise WebError("BadRequest", "Invalid authority")
        header_set = headers if headers is not None else Headers()
        if method == 'QUERY' and body and header_set.one('Content-Type') is None:
            raise WebError('UnsupportedMediaType', 'QUERY content requires Content-Type')
        if header_set.one("Host") not in (None, authority):
            raise WebError("InvalidHeader", "Host and authority disagree")
    except WebError as exc:
        raise DispatchError(str(exc)) from exc
    return Request(method, path, target, query, header_set, method,
                   body=body, authority=authority)


class StreamBody:
    """Exclusive, pull-based source. HTTP writes never buffer the entire body.

    A StreamBody may pass through immutable response metadata changes, but its
    bytes are consumed exactly once. Failure and cancellation close the source.
    """

    def __init__(self, source: object, content_type: str = 'application/octet-stream',
                 length: int | None = None):
        """Initialize a response body with a byte budget and cleanup callback."""
        from threading import RLock
        if not isinstance(content_type, str) or not content_type or _CONTROL.search(content_type):
            raise WebError('InvalidHeader', 'Invalid streaming Content-Type')
        if not callable(getattr(source, 'read', None)) or not callable(getattr(source, 'close', None)):
            raise WebError('InvalidResponse', 'ByteSource requires read and close')
        if length is not None and (type(length) is not int or length < 0):
            raise WebError('InvalidResponse', 'Invalid streaming length')
        self.source = source
        self.content_type = content_type
        self.length = length
        self._closed = False
        self._started = False
        self._exhausted = False
        self._empty_reads = 0
        self._lock = RLock()

    @property
    def closed(self) -> bool:
        """Report whether the response source has closed."""
        with self._lock:
            return self._closed

    def read(self, max_bytes: int) -> bytes | None:
        """Read and validate a bounded chunk from the response producer."""
        if type(max_bytes) is not int or not 0 < max_bytes <= 1048576:
            raise WebError('InvalidResponse', 'Stream read limit out of range')
        with self._lock:
            if self._closed:
                raise WebError('BodyUnavailable', 'Stream is closed or consumed')
            if self._exhausted:
                return None
            self._started = True
            try:
                data = self.source.read(max_bytes)
            except BaseException:
                self.close()
                raise
            if data is None:
                self._exhausted = True
                self.close()
                return None
            if not isinstance(data, bytes) or len(data) > max_bytes:
                self.close()
                raise WebError('InvalidResponse', 'ByteSource returned invalid bytes or exceeded read limit')
            if not data:
                self._empty_reads += 1
                if self._empty_reads > 16:
                    self.close()
                    raise WebError('InvalidResponse', 'ByteSource made no progress')
            else:
                self._empty_reads = 0
            return data

    def close(self) -> None:
        """Close the producer and release its resources once."""
        with self._lock:
            if not self._closed:
                self._closed = True
                self.source.close()


def stream(source: object, content_type: str = 'application/octet-stream',
           *, length: int | None = None) -> StreamBody:
    """Wrap a producer as a bounded streaming response body."""
    return StreamBody(source, content_type, length)


@dataclass(frozen=True)
class Event:
    """Immutable SSE payload; line controls in event/ID cannot inject fields."""
    data: str
    event: str | None = None
    id: str | None = None
    retry_ms: int | None = None

    def __post_init__(self) -> None:
        """Validate the fields of a server-sent event."""
        if not isinstance(self.data, str):
            raise WebError('InvalidResponse', 'SSE data must be text')
        for label, value in (('event', self.event), ('id', self.id)):
            if value is not None and (not isinstance(value, str) or
                                      any(char in value for char in ('\r', '\n', '\x00'))):
                raise WebError('InvalidResponse', f'Invalid SSE {label}')
        if self.retry_ms is not None and (type(self.retry_ms) is not int or
                                          not 0 <= self.retry_ms <= 2147483647):
            raise WebError('InvalidResponse', 'Invalid SSE retry interval')


def encode_event(event: Event) -> bytes:
    """Encode an event as UTF-8 server-sent event fields."""
    if not isinstance(event, Event):
        raise WebError('InvalidResponse', 'EventSource must yield Event or None')
    lines = []
    if event.event is not None:
        lines.append('event: ' + event.event + '\n')
    if event.id is not None:
        lines.append('id: ' + event.id + '\n')
    if event.retry_ms is not None:
        lines.append('retry: ' + str(event.retry_ms) + '\n')
    for line in event.data.replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        lines.append('data: ' + line + '\n')
    result = (''.join(lines) + '\n').encode('utf-8', 'strict')
    if len(result) > 65536:
        raise WebError('BodyTooLarge', 'SSE event exceeds 64 KiB limit')
    return result


class _EventReader:
    """One-event-at-a-time adapter; retains at most one encoded event."""
    def __init__(self, source: object):
        """Adapt an event producer to the streaming byte interface."""
        if not callable(getattr(source, 'next', None)) or not callable(getattr(source, 'close', None)):
            raise WebError('InvalidResponse', 'EventSource requires next and close')
        self.source = source
        self.pending = b''
        self.closed = False

    def read(self, maximum: int) -> bytes | None:
        """Read bytes from the next encoded event or its buffered remainder."""
        if self.closed:
            return None
        if not self.pending:
            event = self.source.next()
            if event is None:
                return None
            self.pending = encode_event(event)
        piece, self.pending = self.pending[:maximum], self.pending[maximum:]
        return piece

    def close(self) -> None:
        """Close the underlying event producer."""
        if not self.closed:
            self.closed = True
            self.source.close()


def event_stream(source: object) -> 'Response':
    """Build an owned SSE response with bounded, pull-driven event encoding."""
    body = StreamBody(_EventReader(source), 'text/event-stream; charset=utf-8')
    return Response(body=body, headers=Headers((('Cache-Control', 'no-cache'),
                                                ('X-Accel-Buffering', 'no'))))


# These fields cannot safely be sent after the body: they control framing,
# response interpretation, cache/auth policy, or routing. Never permit a
# trailer to override the corresponding initial header.
_FORBIDDEN_TRAILER_FIELDS = frozenset({
    'authorization', 'proxy-authorization', 'proxy-authenticate',
    'www-authenticate', 'cookie', 'set-cookie', 'host', 'connection',
    'keep-alive', 'proxy-connection', 'transfer-encoding', 'content-length',
    'te', 'trailer', 'upgrade', 'content-type', 'content-encoding',
    'content-range', 'content-disposition', 'cache-control', 'etag',
    'last-modified', 'expires', 'date', 'location', 'vary', 'content-location',
    'accept-ranges', 'retry-after',
})


def validate_request_trailers(fields: Headers, *, max_fields: int = 32,
                              max_bytes: int = 32768,
                              initial: Headers | None = None) -> Headers:
    """Validate untrusted trailing request metadata before exposing it.

    A trailer must never redefine framing, routing, authentication, or
    representation metadata. Strict limits apply separately from the body
    limit, even when the body itself is streamed incrementally.
    """
    if not isinstance(fields, Headers):
        raise WebError('InvalidHeader', 'Invalid request trailer collection')
    try:
        total = sum(len(name.encode('ascii')) + len(value.encode('latin-1')) + 4
                    for name, value in fields.pairs)
    except UnicodeError as exc:
        raise WebError('InvalidHeader', 'Invalid request trailer encoding') from exc
    if len(fields.pairs) > max_fields or total > max_bytes:
        raise WebError('HeadersTooLarge', 'Request trailers exceed limits')
    for name, value in fields.pairs:
        if (name.lower() in _FORBIDDEN_TRAILER_FIELDS or
                (initial is not None and initial.values(name))):
            raise WebError('InvalidHeader', 'Forbidden request trailer field')
        if len(name) + len(value) > 8192:
            raise WebError('HeadersTooLarge', 'Request trailer field exceeds limit')
    return fields


def with_trailers(response: 'Response', trailers: Headers) -> 'Response':
    """Attach a trailer producer and its declared field names."""
    if not isinstance(response.body, StreamBody) or response.status in (204, 205, 304):
        raise WebError('InvalidHeader', 'Response trailers require a streamed body')
    if not isinstance(trailers, Headers) or not trailers.pairs or len(trailers.pairs) > 32:
        raise WebError('InvalidHeader', 'Expected 1 to 32 trailer fields')
    if sum(len(name) + len(value) + 4 for name, value in trailers.pairs) > 32768:
        raise WebError('InvalidHeader', 'Trailing header block exceeds 32 KiB')
    for name, value in trailers.pairs:
        if name.lower() in _FORBIDDEN_TRAILER_FIELDS:
            raise WebError('InvalidHeader', 'Forbidden trailing field')
        if len(name) + len(value) > 8192:
            raise WebError('InvalidHeader', 'Trailing field exceeds limit')
        try:
            value.encode('latin-1', 'strict')
        except UnicodeEncodeError as exc:
            raise WebError('InvalidHeader', 'Trailing field cannot be encoded on HTTP/1.1') from exc
        if response.headers.values(name):
            raise WebError('InvalidHeader', 'A trailing field cannot replace an initial field')
    if response.headers.one('Content-Length') is not None:
        raise WebError('InvalidHeader', 'Trailers require chunked framing, not Content-Length')
    return replace(response, trailers=trailers)


@dataclass(frozen=True)
class Response:
    status: int = 200
    body: bytes | str | StreamBody | None = None
    headers: Headers = field(default_factory=Headers)
    early_hints: tuple[str, ...] = ()
    trailers: Headers = field(default_factory=Headers)

    def __post_init__(self) -> None:
        """Validate response metadata and normalize the body representation."""
        if self.trailers.pairs:
            if not isinstance(self.body, StreamBody) or self.status in (204, 205, 304):
                raise WebError('InvalidResponse', 'Only streamed responses may have trailers')
            if (len(self.trailers.pairs) > 32 or
                    sum(len(name) + len(value) + 4 for name, value in self.trailers.pairs) > 32768 or
                    any(name.lower() in _FORBIDDEN_TRAILER_FIELDS or
                        len(name) + len(value) > 8192 or
                        self.headers.values(name)
                        for name, value in self.trailers.pairs)):
                raise WebError('InvalidResponse', 'Invalid trailing response fields')
            if self.headers.values('Content-Length'):
                raise WebError('InvalidResponse', 'Response trailers conflict with Content-Length')
            for _name, _value in self.trailers.pairs:
                try:
                    _value.encode('latin-1', 'strict')
                except UnicodeEncodeError as exc:
                    raise WebError('InvalidResponse', 'Non-HTTP/1.1 trailing field value') from exc
        if len(self.early_hints) > 32:
            raise WebError('InvalidHeader', 'Too many Early Hints links')
        for hint in self.early_hints:
            _validate_early_link(hint)
        if self.body is not None and not isinstance(self.body, (str, bytes, StreamBody)):
            raise WebError("InvalidResponse", "Unsupported response body type")
        if not 100 <= self.status <= 599 or self.status in (306,):
            raise WebError("InvalidResponse", "Invalid status code")
        if (self.status < 200 or self.status in (204, 205, 304)) and self.body not in (None, b"", ""):
            raise WebError("InvalidResponse", "Status forbids a representation")
        for name, _ in self.headers.pairs:
            if name.lower() in _FORBIDDEN_RESPONSE_HEADERS:
                raise WebError("InvalidResponse", "Transport controls are not response headers")
        if isinstance(self.body, str) and not self.headers.values("Content-Type"):
            object.__setattr__(self, "headers", self.headers.with_header("Content-Type", "text/plain; charset=utf-8"))
        elif isinstance(self.body, bytes) and not self.headers.values("Content-Type"):
            object.__setattr__(self, "headers", self.headers.with_header("Content-Type", "application/octet-stream"))
        elif isinstance(self.body, StreamBody) and not self.headers.values('Content-Type'):
            object.__setattr__(self, 'headers', self.headers.with_header('Content-Type', self.body.content_type))

    @property
    def bytes(self) -> bytes:
        """Return the buffered response body as bytes."""
        if isinstance(self.body, StreamBody):
            raise WebError('BodyUnavailable', 'Streaming responses cannot be implicitly buffered')
        return self.body.encode("utf-8") if isinstance(self.body, str) else (self.body or b"")

    def with_header(self, name: str, value: str) -> Response:
        """Replace the response header with one value."""
        return replace(self, headers=self.headers.with_header(name, value))

    def add_header(self, name: str, value: str) -> Response:
        """Append another occurrence of a response header."""
        return replace(self, headers=self.headers.add(name, value))

    def without_header(self, name: str) -> Response:
        """Remove all occurrences of a response header."""
        return replace(self, headers=self.headers.without(name))


_EARLY_LINK = re.compile(r'^<([^<>\s]+)>\s*;\s*rel\s*=\s*(?:"preload"|preload)(?:\s*;\s*[A-Za-z][A-Za-z0-9-]*\s*=\s*(?:"[^"\r\n]*"|[!#$%&\'*+.^_`|~0-9A-Za-z-]+))*$', re.I)


def _validate_early_link(value: str) -> None:
    """Validate one preload Link field without permitting response splitting."""
    if (not isinstance(value, str) or not value.isascii() or len(value) > 4096 or
            _CONTROL.search(value)):
        raise WebError('InvalidHeader', 'Invalid Early Hints Link')
    match = _EARLY_LINK.fullmatch(value)
    if not match:
        raise WebError('InvalidHeader', 'Early Hints require a preload Link')
    location = match.group(1)
    try:
        parts = urlsplit(location)
        if parts.fragment or parts.username or parts.password:
            raise ValueError('Invalid preload reference')
        if parts.scheme:
            if parts.scheme.lower() not in ('http', 'https') or not parts.hostname:
                raise ValueError('Invalid preload origin')
        elif location.startswith('//') or not location.startswith('/'):
            raise ValueError('Expected an absolute path or HTTP(S) reference')
        else:
            validate_target(location)
    except (ValueError, WebError) as exc:
        raise WebError('InvalidHeader', 'Invalid Early Hints URL') from exc


def with_early_hints(response: Response, links: Iterable[str]) -> Response:
    """Attach preload hints to the same final semantic response."""
    checked = tuple(links)
    if not checked:
        return replace(response, early_hints=())
    for link in checked:
        _validate_early_link(link)
    return replace(response, early_hints=checked)


def json_response(value: object, *, status: int = 200) -> Response:
    """Serialize a value into an application/json response."""
    return Response(status, json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(),
                    Headers((("Content-Type", "application/json"),)))


def redirect(location: str, *, status: int = 303, allow_external: bool = False) -> Response:
    """Build a redirect response with a validated Location."""
    if status not in (301, 302, 303, 307, 308) or _CONTROL.search(location) or "\\" in location:
        raise WebError("InvalidLocation", "Invalid redirect")
    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc:
        if not allow_external or parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
            raise WebError("InvalidLocation", "External redirect is not permitted")
    elif not location.startswith("/") or location.startswith("//"):
        raise WebError("InvalidLocation", "Expected same-origin absolute path")
    else:
        validate_target(location)
    if parsed.fragment:
        raise WebError("InvalidLocation", "Fragments are not valid redirect targets")
    return Response(status, headers=Headers((("Location", location),)))


def no_store(response: Response) -> Response:
    """Prohibit storing the response in caches."""
    return response.with_header("Cache-Control", "no-store")


def cache_public(response: Response, max_age: int, *, immutable: bool = False) -> Response:
    """Allow shared caches to reuse the response for the supplied lifetime."""
    if not isinstance(max_age, int) or max_age < 0:
        raise WebError("InvalidCachePolicy", "Invalid max-age")
    return response.with_header("Cache-Control", f"public, max-age={max_age}" + (", immutable" if immutable else ""))


def cache_private(response: Response, max_age: int) -> Response:
    """Allow private caches to reuse the response for the supplied lifetime."""
    if not isinstance(max_age, int) or max_age < 0:
        raise WebError("InvalidCachePolicy", "Invalid max-age")
    return response.with_header("Cache-Control", f"private, max-age={max_age}")


def with_etag(response: Response, tag: str, *, weak: bool = False) -> Response:
    """Attach a validated entity tag to the response."""
    if _CONTROL.search(tag) or '"' in tag or "\\" in tag:
        raise WebError("InvalidETag", "Invalid entity tag")
    return response.with_header("ETag", f'{"W/" if weak else ""}"{tag}"')


def with_last_modified(response: Response, unix_seconds: int) -> Response:
    """Attach a Last-Modified timestamp to the response."""
    if not isinstance(unix_seconds, int):
        raise WebError('InvalidHeader', 'Last-Modified requires a Unix timestamp')
    try:
        return response.with_header('Last-Modified', formatdate(unix_seconds, usegmt=True))
    except (ValueError, OverflowError) as exc:
        raise WebError('InvalidHeader', 'Last-Modified is out of range') from exc


def validate_response(response: Response, *, head: bool = False) -> Response:
    """Reject malformed application framing before network commitment."""
    if response.status < 200:
        raise WebError('InvalidResponse', 'Informational responses require a transport handshake')
    length = response.headers.one('Content-Length')
    if length is not None:
        if not length.isascii() or not length.isdecimal():
            raise WebError('InvalidResponse', 'Invalid Content-Length')
        if not head and not isinstance(response.body, StreamBody) and int(length) != len(response.bytes):
            raise WebError('InvalidResponse', 'Content-Length does not match response body')
    if response.trailers.pairs and (not isinstance(response.body, StreamBody) or length is not None):
        raise WebError('InvalidResponse', 'Trailers require a streamed response without Content-Length')
    if isinstance(response.body, StreamBody) and length is not None and (response.body.length is None or int(length) != response.body.length):
        raise WebError('InvalidResponse', 'Stream Content-Length must be a declared exact length')
    if response.status in (204, 304) and length is not None:
        raise WebError('InvalidResponse', 'This status cannot carry Content-Length')
    return response



def collect_body(response: Response, limit: int = 16777216) -> bytes:
    """Collect an in-memory representation, without implicit UTF-8 conversion."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise WebError('InvalidResponse', 'Body collection limit must be nonnegative')
    if isinstance(response.body, StreamBody):
        source = response.body
        result = bytearray()
        try:
            while True:
                piece = source.read(min(65536, max(1, limit - len(result) + 1)))
                if piece is None:
                    break
                result.extend(piece)
                if len(result) > limit:
                    raise WebError('BodyTooLarge', 'Response body exceeds collection limit')
            if source.length is not None and len(result) != source.length:
                raise WebError('InvalidResponse', 'Stream length does not match emitted bytes')
            return bytes(result)
        finally:
            source.close()
    result = response.bytes
    if len(result) > limit:
        raise WebError('BodyTooLarge', 'Response body exceeds collection limit')
    return result


def response_text(response: Response, limit: int = 16777216) -> str:
    """Strictly decode a bounded response, never replace or drop invalid bytes."""
    raw = collect_body(response, limit)
    try:
        return raw.decode('utf-8', 'strict')
    except UnicodeDecodeError as exc:
        raise WebError('BadText', 'Response body is not valid UTF-8') from exc

def normalize_result(result: object) -> Response:
    """Convert a handler outcome into a response."""
    if isinstance(result, Response):
        return result
    if isinstance(result, str):
        return Response(body=result)
    raise WebError("InvalidResponse", "Handler returned an unsupported value")


Middleware = Callable[[Request], object]
Handler = Callable[[Request], object]


@dataclass(frozen=True)
class After:
    request: Request
    after: Callable[[Response], Response]


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    handler: Handler
    middleware: tuple[Middleware, ...] = ()
    source: str = ""
    body_mode: str = "Buffered"
    max_body_bytes: int | None = None
    websocket_policy: object | None = None

    @property
    def template(self) -> tuple[tuple[str, str], ...]:
        """Reconstruct the route's path template."""
        return _validate_template(self.path)


@dataclass(frozen=True)
class Router:
    entries: tuple[Route, ...] = ()
    middleware: tuple[Middleware, ...] = ()

    def route(self, method: str, path: str, handler: Handler, *,
              body_mode: str = 'Buffered', max_body_bytes: int | None = None) -> Router:
        """Return a router with the supplied HTTP route registered."""
        _validate_template(path)
        if method not in _METHODS:
            raise ConfigError('Unsupported route method')
        if body_mode not in ('Buffered', 'Stream'):
            raise ConfigError('Invalid route body mode')
        if method not in ('POST', 'PUT', 'PATCH', 'QUERY') and (
                body_mode != 'Buffered' or max_body_bytes is not None):
            raise ConfigError('Body options require a content-bearing route')
        if max_body_bytes is not None and (
                type(max_body_bytes) is not int or not 0 < max_body_bytes <= 1024 ** 3):
            raise ConfigError('Invalid route body limit')
        return replace(self, entries=(*self.entries, Route(
            method, path, handler, body_mode=body_mode,
            max_body_bytes=max_body_bytes)))

    def websocket(self, path: str, handler: Callable[[object], object],
                  policy: object | None = None) -> Router:
        """Register a distinct upgrade route without exposing it as GET."""
        from valiance.std._web_ws import WebSocketPolicy
        _validate_template(path)
        policy = WebSocketPolicy() if policy is None else policy
        if not isinstance(policy, WebSocketPolicy) or not callable(handler):
            raise ConfigError('Invalid WebSocket route registration')
        return replace(self, entries=(*self.entries, Route(
            'WEBSOCKET', path, handler, websocket_policy=policy),))

    def with_body_mode(self, method: str, path: str, mode: str) -> Router:
        """Set request body handling for the selected route."""
        if method not in ('POST', 'PUT', 'PATCH', 'QUERY') or mode not in ('Buffered', 'Stream'):
            raise ConfigError('Only content-bearing methods may select a body mode')
        matches = [i for i, r in enumerate(self.entries) if r.method == method and r.path == path]
        if len(matches) != 1:
            raise ConfigError('Body mode requires exactly one registered method/template')
        result = list(self.entries)
        i = matches[0]
        result[i] = replace(result[i], body_mode=mode)
        return replace(self, entries=tuple(result))

    def use(self, callback: Middleware) -> Router:
        """Return a router with an additional middleware callback."""
        return replace(self, middleware=(*self.middleware, callback))

    def group(self, prefix: str, router: Router | Callable[[Router], Router],
              middleware: Iterable[Middleware] = ()) -> Router:
        """Mount a child router under the supplied path prefix."""
        prefix_parts = _validate_template(prefix)
        del prefix_parts
        if callable(router) and not isinstance(router, Router):
            router = router(Router())
        if not isinstance(router, Router):
            raise ConfigError("Group content must be a Router or Router builder")
        grouped = []
        for route in router.entries:
            effective = prefix.rstrip("/") + (route.path if route.path != "/" else "")
            if not effective:
                effective = "/"
            _validate_template(effective)
            grouped.append(replace(route, path=effective,
                                   middleware=(*tuple(middleware), *router.middleware, *route.middleware)))
        return replace(self, entries=(*self.entries, *grouped))


# Stable request-edge security policy shared by HTTP/1 and HTTP/2.
# Validate all configuration before binding, then use the actual socket peer
# rather than client-supplied routing metadata to decide proxy trust.
def _authority_parts(authority: str) -> tuple[str, int | None]:
    """Split and validate an authority into host and port."""
    if (not isinstance(authority, str) or not authority or len(authority) > 512 or
            _CONTROL.search(authority) or authority != authority.strip() or
            any(ch in authority for ch in ' /\\@?#,%')):
        raise ValueError('Invalid authority')
    if authority.startswith('['):
        bracket = authority.find(']')
        if bracket == -1:
            raise ValueError('Invalid IPv6 authority')
        host = str(ipaddress.IPv6Address(authority[1:bracket])).lower()
        tail = authority[bracket + 1:]
        if tail and not tail.startswith(':'):
            raise ValueError('Invalid IPv6 port')
        port_text = tail[1:] if tail else None
    else:
        if authority.count(':') > 1:
            raise ValueError('IPv6 authority requires brackets')
        name, sep, tail = authority.partition(':')
        if not name:
            raise ValueError('Missing authority host')
        try:
            host = str(ipaddress.IPv4Address(name))
        except ipaddress.AddressValueError:
            try:
                host = name.encode('idna').decode('ascii').lower().rstrip('.')
            except UnicodeError as exc:
                raise ValueError('Invalid DNS authority') from exc
            if (not host or len(host) > 253 or any(
                    re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label) is None
                    for label in host.split('.'))):
                raise ValueError('Invalid DNS authority')
        port_text = tail if sep else None
    if port_text is not None and (not port_text.isascii() or
                                 not port_text.isdigit() or not 1 <= int(port_text) <= 65535):
        raise ValueError('Invalid authority port')
    return host, int(port_text) if port_text is not None else None


def _forwarded_ip(value: str) -> str:
    """Accept only numeric forwarded client addresses, not opaque identifiers."""
    if value.startswith('['):
        bracket = value.find(']')
        if bracket < 0:
            raise ValueError('Invalid forwarded IPv6 address')
        ip = str(ipaddress.IPv6Address(value[1:bracket]))
        tail = value[bracket + 1:]
        if tail:
            if not tail.startswith(':') or not tail[1:].isdigit() or not 1 <= int(tail[1:]) <= 65535:
                raise ValueError('Invalid forwarded port')
        return ip
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        host, port = _authority_parts(value)
        if port is None:
            raise ValueError('Forwarded address must be numeric')
        return str(ipaddress.IPv4Address(host))


def _forwarded_elements(values: tuple[str, ...]) -> tuple[dict[str, str], ...]:
    """Small strict RFC 7239 subset: bounded unambiguous for/proto/host fields.

    Reject escapes, duplicate keys and malformed quotes instead of trying to
    salvage partially trusted forwarding data. A proxy may emit multiple
    comma-separated elements, but it must not supply mixed formats.
    """
    wire = ','.join(values)
    if len(wire) > 8192 or _CONTROL.search(wire) or '\\' in wire:
        raise ValueError('Unsafe Forwarded header')
    # Commas and semicolons inside quotes are data, not separators.
    def split_quoted(text: str, sep: str) -> list[str]:
        """Split a forwarded header field outside quoted strings."""
        out: list[str] = []
        quoted = False
        start = 0
        for i, ch in enumerate(text):
            if ch == '"':
                quoted = not quoted
            elif ch == sep and not quoted:
                out.append(text[start:i].strip())
                start = i + 1
        if quoted:
            raise ValueError('Unclosed forwarding quote')
        out.append(text[start:].strip())
        return out
    elements = split_quoted(wire, ',')
    if not 1 <= len(elements) <= 8:
        raise ValueError('Invalid Forwarded hop count')
    result = []
    for element in elements:
        pairs: dict[str, str] = {}
        for field in split_quoted(element, ';'):
            key, sep, value = field.partition('=')
            key = key.strip().lower()
            value = value.strip()
            if (not sep or not re.fullmatch(r'[a-z][a-z0-9_-]*', key) or
                    not value or key in pairs):
                raise ValueError('Invalid Forwarded field')
            if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                value = value[1:-1]
            elif '"' in value:
                raise ValueError('Invalid Forwarded quoting')
            if not value or any(ch.isspace() for ch in value):
                raise ValueError('Invalid Forwarded value')
            pairs[key] = value
        if 'for' not in pairs:
            raise ValueError('Forwarded requires for=')
        result.append(pairs)
    return tuple(result)


@dataclass(frozen=True)
class NetworkPolicy:
    """Explicit host and reverse-proxy trust, including WebSocket upgrades.

    The default mode ignores every forwarding header. Forwarding is trusted
    only when the *direct* TCP peer belongs to the configured CIDR networks.
    Only one forwarding dialect is read; they are never opportunistically
    combined. Public origin is validated configuration metadata; automatic
    external URL generation is not yet guaranteed.
    """
    allowed_hosts: tuple[str, ...] = ()
    trusted_proxies: tuple[str, ...] = ()
    proxy_mode: str = 'Disabled'
    public_origin: str | None = None

    def __post_init__(self) -> None:
        """Validate proxy trust and forwarded-header settings."""
        if self.proxy_mode not in ('Disabled', 'RFCForwarded', 'XForwarded'):
            raise ConfigError('Unsupported proxy mode')
        if (not isinstance(self.allowed_hosts, tuple) or
                not isinstance(self.trusted_proxies, tuple) or
                len(self.allowed_hosts) > 128 or len(self.trusted_proxies) > 128):
            raise ConfigError('Invalid host/proxy list')
        try:
            for name in self.allowed_hosts:
                if name == '*':
                    raise ValueError('Unrestricted wildcard allowlist is not allowed')
                _authority_parts(name)
            for network in self.trusted_proxies:
                if not isinstance(network, str) or '/' not in network:
                    raise ValueError('Trusted proxies must be CIDRs')
                ipaddress.ip_network(network, strict=False)
            if self.proxy_mode != 'Disabled' and not self.trusted_proxies:
                raise ValueError('Enabled proxy mode requires trusted peer CIDRs')
            if self.public_origin is not None:
                _origin_key(self.public_origin)
        except (ValueError, TypeError) as exc:
            raise ConfigError('Invalid server host/proxy/origin configuration') from exc

    def verify(self, headers: Headers, authority: str, scheme: str,
               peer: str) -> tuple[str, str, str]:
        """Return (effective scheme, authority, client IP); reject before dispatch."""
        try:
            _authority_parts(authority)
        except ValueError as exc:
            raise WebError('BadRequest', 'Malformed authority') from exc
        client = peer
        effective_authority = authority
        effective_scheme = scheme
        try:
            trusted = (self.proxy_mode != 'Disabled' and any(
                ipaddress.ip_address(peer) in ipaddress.ip_network(network, strict=False)
                for network in self.trusted_proxies))
        except ValueError:
            trusted = False
        if trusted:
            if self.proxy_mode == 'RFCForwarded':
                fields = headers.values('forwarded')
                if fields and any(headers.values(k) for k in
                                  ('x-forwarded-for', 'x-forwarded-host', 'x-forwarded-proto')):
                    raise WebError('BadRequest', 'Conflicting proxy header dialects')
                if fields:
                    try:
                        hops = _forwarded_elements(fields)
                        if len(hops) > 8:
                            raise ValueError('Too many Forwarded hops')
                        # Iterate from the physically closest hop toward the
                        # first outside the explicitly trusted proxy set.
                        current = peer
                        for item in reversed(hops):
                            if not any(ipaddress.ip_address(current) in
                                       ipaddress.ip_network(n, strict=False)
                                       for n in self.trusted_proxies):
                                break
                            current = _forwarded_ip(item['for'])
                            if 'proto' in item:
                                if item['proto'] not in ('http', 'https'):
                                    raise ValueError('Invalid forwarded scheme')
                                effective_scheme = item['proto']
                            if 'host' in item:
                                _authority_parts(item['host'])
                                effective_authority = item['host']
                        client = current
                    except ValueError as exc:
                        raise WebError('BadRequest', 'Malformed Forwarded chain') from exc
            elif self.proxy_mode == 'XForwarded':
                chain = headers.values('x-forwarded-for')
                if chain:
                    if headers.values('forwarded'):
                        raise WebError('BadRequest', 'Conflicting proxy header dialects')
                    try:
                        wire = ','.join(chain)
                        hops = [x.strip() for x in wire.split(',')]
                        if len(wire) > 8192 or not 1 <= len(hops) <= 8:
                            raise ValueError('Invalid forwarding chain size')
                        current = peer
                        for hop in reversed(hops):
                            if not any(ipaddress.ip_address(current) in
                                       ipaddress.ip_network(n, strict=False)
                                       for n in self.trusted_proxies):
                                break
                            current = _forwarded_ip(hop)
                        client = current
                        for name in ('x-forwarded-proto', 'x-forwarded-host'):
                            values = headers.values(name)
                            if len(values) > 1 or (values and ',' in values[0]):
                                raise ValueError('Ambiguous proxy metadata')
                        if headers.values('x-forwarded-proto'):
                            effective_scheme = headers.one('x-forwarded-proto')
                            if effective_scheme not in ('http', 'https'):
                                raise ValueError('Invalid proxy scheme')
                        if headers.values('x-forwarded-host'):
                            effective_authority = headers.one('x-forwarded-host')
                            _authority_parts(effective_authority)
                    except ValueError as exc:
                        raise WebError('BadRequest', 'Malformed X-Forwarded chain') from exc
                elif headers.values('x-forwarded-proto') or headers.values('x-forwarded-host'):
                    raise WebError('BadRequest', 'X-Forwarded-For required for proxy identity')
        # A forwarding header cannot relabel a TLS-authenticated connection
        # as cleartext. TLS termination *before* this listener instead starts
        # from http and may explicitly advertise its public https scheme.
        if scheme == 'https' and effective_scheme != 'https':
            raise WebError('BadRequest', 'Forwarding metadata conflicts with TLS')
        if self.allowed_hosts:
            host, port = _authority_parts(effective_authority)
            allowed = False
            for entry in self.allowed_hosts:
                expected_host, expected_port = _authority_parts(entry)
                if host == expected_host and (expected_port is None or port == expected_port):
                    allowed = True
                    break
            if not allowed:
                raise WebError('MisdirectedRequest', 'Request authority is not allowed')
        return effective_scheme, effective_authority, client


@dataclass(frozen=True)
class LogEvent:
    """Privacy-safe HTTP completion record; no path values or credentials.

    Streaming response/request byte counts describe buffered data only. They
    deliberately do not pretend to report native TLS or application IO.
    """
    request_id: str
    method: str
    route_template: str | None
    status: int
    protocol: str
    elapsed_milliseconds: float
    request_bytes: int
    response_bytes: int
    peer: str = ""
    client_address: str = ""
    error_stage: str | None = None


@dataclass(frozen=True)
class App(Router):
    on_error: Callable[[Request | None, Exception, str], Response] | None = None
    logger: Callable[[LogEvent], object] | None = None

    def with_logger(self, callback: Callable[[LogEvent], object]) -> App:
        """Return an app with a request logging callback."""
        if not callable(callback):
            raise ConfigError('Logger must be callable')
        return replace(self, logger=callback)

    def route(self, method: str, path: str, handler: Handler, *,
              body_mode: str = 'Buffered', max_body_bytes: int | None = None) -> App:
        """Return an app with an HTTP route registered."""
        updated = super().route(method, path, handler, body_mode=body_mode,
                                max_body_bytes=max_body_bytes)
        return replace(self, entries=updated.entries)

    def websocket(self, path: str, handler: Callable[[object], object],
                  policy: object | None = None) -> App:
        """Return an app with a WebSocket route registered."""
        updated = super().websocket(path, handler, policy)
        return replace(self, entries=updated.entries)

    def with_body_mode(self, method: str, path: str, mode: str) -> App:
        """Configure how a selected app route consumes its body."""
        updated = super().with_body_mode(method, path, mode)
        return replace(self, entries=updated.entries)

    def use(self, callback: Middleware) -> App:
        """Add a middleware callback to the app."""
        return replace(self, middleware=(*self.middleware, callback))

    def group(self, prefix: str, router: Router | Callable[[Router], Router],
              middleware: Iterable[Middleware] = ()) -> App:
        """Mount a router under an app path prefix."""
        result = super().group(prefix, router, middleware)
        return replace(self, entries=result.entries)


# CORS is a pure, opt-in middleware. Route admission data comes from the
# dispatcher, not from untrusted HTTP request headers or user-supplied context.
_CORS_ROUTE_METHODS = object()


def _origin_key(value: str) -> tuple[str, str, int]:
    """Normalize an origin for policy comparison."""
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError('Origin must be a single absolute HTTP(S) origin')
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if (parsed.scheme not in ('http', 'https') or not host or
                not parsed.netloc or parsed.username is not None or
                parsed.password is not None or parsed.path or parsed.query or
                parsed.fragment or '#' in value or '?' in value or
                _CONTROL.search(value) or '\\' in value):
            raise ValueError('Invalid origin')
        if ':' not in host:
            host = host.encode('idna').decode('ascii').lower()
            if len(host) > 253 or any(
                    not 1 <= len(label) <= 63 or
                    re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', label) is None
                    for label in host.rstrip('.').split('.')
            ):
                raise ValueError('Invalid DNS host in Origin')
        else:
            host = str(ipaddress.IPv6Address(host)).lower()
        if port is not None and port < 1:
            raise ValueError('Invalid origin port')
        return parsed.scheme, host, port if port is not None else (443 if parsed.scheme == 'https' else 80)
    except (ValueError, UnicodeError, ipaddress.AddressValueError) as exc:
        raise ValueError('Invalid HTTP(S) origin') from exc


def _cors_vary(response: Response, *names: str) -> Response:
    """Add the CORS-dependent fields to the response's Vary header."""
    lines = response.headers.values('Vary')
    values = [part.strip() for line in lines for part in line.split(',') if part.strip()]
    if '*' in values:
        return response
    for name in names:
        if name.lower() not in {part.lower() for part in values}:
            values.append(name)
    return response.with_header('Vary', ', '.join(values))


@dataclass(frozen=True)
class CorsPolicy:
    allowed_origins: tuple[str, ...]
    allow_credentials: bool = False
    allowed_methods: tuple[str, ...] = _METHODS
    allowed_headers: tuple[str, ...] = ()
    exposed_headers: tuple[str, ...] = ()
    max_age: int | None = None
    allow_private_network: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize the configured CORS rules."""
        for name in ('allowed_origins', 'allowed_methods', 'allowed_headers', 'exposed_headers'):
            values = getattr(self, name)
            if isinstance(values, (str, bytes)):
                raise ConfigError('CORS policy lists must contain individual values')
            object.__setattr__(self, name, tuple(values))
        if not self.allowed_origins:
            raise ConfigError('CORS allowedOrigins cannot be empty')
        if '*' in self.allowed_origins:
            if len(self.allowed_origins) != 1 or self.allow_credentials:
                raise ConfigError('Credentialed or mixed wildcard CORS origins are forbidden')
        else:
            for origin in self.allowed_origins:
                try:
                    _origin_key(origin)
                except ValueError as exc:
                    raise ConfigError(str(exc)) from exc
        if not self.allowed_methods or any(method not in _METHODS for method in self.allowed_methods):
            raise ConfigError('Invalid CORS allowedMethods')
        if any(not _TOKEN.fullmatch(value) for value in (*self.allowed_headers, *self.exposed_headers)):
            raise ConfigError('Invalid CORS header name')
        if self.max_age is not None and (type(self.max_age) is not int or self.max_age < 0):
            raise ConfigError('CORS maxAge must be a nonnegative integer')


def cors(policy: CorsPolicy) -> Middleware:
    """Build middleware for cross-origin requests and preflights."""
    if not isinstance(policy, CorsPolicy):
        raise ConfigError('CORS requires a validated CorsPolicy')
    allowed_origin_keys = (set(_origin_key(origin) for origin in policy.allowed_origins)
                           if policy.allowed_origins != ('*',) else set())
    methods = set(policy.allowed_methods)
    allowed_headers = {header.lower() for header in policy.allowed_headers}

    def middleware(request: Request) -> Request | Response | After:
        """Evaluate an incoming request against the CORS policy."""
        origin_values = request.headers.values('Origin')
        preflight = request.method == 'OPTIONS' and any(
            request.headers.values(name) for name in
            ('Access-Control-Request-Method', 'Access-Control-Request-Headers',
             'Access-Control-Request-Private-Network'))
        def deny() -> Response:
            """Build a response rejecting the cross-origin request."""
            response = Response(403, 'Forbidden')
            return _cors_vary(response, 'Origin', 'Access-Control-Request-Method',
                              'Access-Control-Request-Headers', 'Access-Control-Request-Private-Network')
        def no_grant(response: Response) -> Response:
            # Do not let a handler-supplied CORS grant circumvent the installed
            # middleware policy on denied origins or requests without Origin.
            """Continue the request without granting cross-origin access."""
            for name in ('Access-Control-Allow-Origin', 'Access-Control-Allow-Credentials',
                         'Access-Control-Expose-Headers'):
                response = replace(response, headers=response.headers.without(name))
            return (_cors_vary(response, 'Origin')
                    if policy.allowed_origins != ('*',) else response)

        if len(origin_values) != 1:
            return deny() if preflight else After(request, no_grant)
        origin = origin_values[0]
        try:
            approved = policy.allowed_origins == ('*',) or _origin_key(origin) in allowed_origin_keys
        except ValueError:
            approved = False
        if not approved:
            return deny() if preflight else After(request, no_grant)
        grant = '*' if policy.allowed_origins == ('*',) else origin

        def grant_headers(response: Response) -> Response:
            """Build the headers that grant the approved origin access."""
            response = response.with_header('Access-Control-Allow-Origin', grant)
            if grant != '*':
                response = _cors_vary(response, 'Origin')
            if policy.allow_credentials:
                response = response.with_header('Access-Control-Allow-Credentials', 'true')
            return response

        if preflight:
            requested = request.headers.values('Access-Control-Request-Method')
            if len(requested) != 1 or requested[0] not in methods or \
                    requested[0] not in request.context.get(_CORS_ROUTE_METHODS, ()):
                return deny()
            requested_headers = request.headers.values('Access-Control-Request-Headers')
            if len(requested_headers) > 1:
                return deny()
            headers = []
            if requested_headers:
                headers = [header.strip() for header in requested_headers[0].split(',')]
                if any(not _TOKEN.fullmatch(header) or header.lower() not in allowed_headers
                       for header in headers):
                    return deny()
            private = request.headers.values('Access-Control-Request-Private-Network')
            if len(private) > 1 or (private and (private != ('true',) or not policy.allow_private_network)):
                return deny()
            response = grant_headers(Response(204))
            response = response.with_header('Access-Control-Allow-Methods', requested[0])
            if headers:
                response = response.with_header('Access-Control-Allow-Headers', ', '.join(headers))
            if private:
                response = response.with_header('Access-Control-Allow-Private-Network', 'true')
            if policy.max_age is not None:
                response = response.with_header('Access-Control-Max-Age', str(policy.max_age))
            return _cors_vary(response, 'Origin', 'Access-Control-Request-Method',
                              'Access-Control-Request-Headers',
                              'Access-Control-Request-Private-Network')

        def after(response: Response) -> Response:
            """Apply the approved CORS headers to the downstream response."""
            response = grant_headers(response)
            if policy.exposed_headers:
                response = response.with_header('Access-Control-Expose-Headers',
                                                ', '.join(policy.exposed_headers))
            return response
        return After(request, after)
    return middleware


# Compression is opt-in and only transforms bounded, buffered representations.
# Streaming is deliberately handled by the separate owned-stream API; never
# collect an unbounded body in this middleware.
@dataclass(frozen=True)
class CompressionPolicy:
    min_bytes: int = 1024
    gzip_enabled: bool = True
    brotli_enabled: bool = True
    max_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        """Validate the compression limits and configured codings."""
        if (type(self.min_bytes) is not int or self.min_bytes < 0 or
                type(self.max_bytes) is not int or self.max_bytes < 1 or
                self.min_bytes > self.max_bytes or
                type(self.gzip_enabled) is not bool or type(self.brotli_enabled) is not bool or
                not (self.gzip_enabled or self.brotli_enabled)):
            raise ConfigError('Invalid compression policy')
        if self.brotli_enabled:
            try:
                import brotli  # noqa: F401
            except ImportError as exc:
                raise ConfigError('Brotli is enabled but not installed') from exc


def _encoding_preferences(headers: Headers) -> dict[str, float] | None:
    """Parse the client's encoding quality preferences."""
    lines = headers.values('Accept-Encoding')
    if not lines:
        return None
    # An explicitly empty field permits only an unencoded representation.
    if all(not line.strip() for line in lines):
        return {}
    qualities: dict[str, float] = {}
    for line in lines:
        for raw in line.split(','):
            element = raw.strip()
            if not element:
                raise WebError('InvalidHeader', 'Invalid Accept-Encoding')
            sections = [part.strip() for part in element.split(';')]
            token = sections[0].lower()
            if not _TOKEN.fullmatch(token) or token in qualities:
                raise WebError('InvalidHeader', 'Invalid or duplicate Accept-Encoding coding')
            quality = 1.0
            if len(sections) > 1:
                if len(sections) != 2 or not sections[1].lower().startswith('q='):
                    raise WebError('InvalidHeader', 'Invalid Accept-Encoding parameter')
                quality_text = sections[1][2:].strip()
                if not _QVALUE.fullmatch(quality_text):
                    raise WebError('InvalidHeader', 'Invalid Accept-Encoding quality')
                quality = float(quality_text)
            qualities[token] = quality
    return qualities


def _coding_quality(preferences: dict[str, float], coding: str) -> float:
    """Find the effective quality of an encoding."""
    if coding in preferences:
        return preferences[coding]
    if coding == 'identity':
        return preferences.get('*', 1.0)
    return preferences.get('*', 0.0)


def _can_compress(response: Response, size: int, max_bytes: int) -> bool:
    """Determine whether a response is eligible for compression."""
    if size == 0 or size > max_bytes or response.status < 200 or response.status in (204, 205, 206, 304):
        return False
    if (response.headers.values('Content-Encoding') or response.headers.values('Content-Range') or
            any('attachment' in item.lower() for item in response.headers.values('Content-Disposition'))):
        return False
    if any('no-transform' in (item.strip().lower() for item in line.split(','))
           for line in response.headers.values('Cache-Control')):
        return False
    media = response.headers.one('Content-Type')
    if not media:
        return False
    media = media.split(';', 1)[0].strip().lower()
    if (media == 'text/event-stream' or media.startswith('multipart/') or
            media in ('application/zip', 'application/gzip', 'application/x-gzip',
                      'application/pdf', 'application/octet-stream') or
            (media.startswith(('image/', 'audio/', 'video/')) and media != 'image/svg+xml')):
        return False
    return (media.startswith('text/') or media in ('application/json', 'application/javascript',
            'application/xml', 'application/xhtml+xml', 'image/svg+xml') or
            (media.startswith('application/') and (media.endswith('+json') or media.endswith('+xml'))))


_COMPRESSION_DEFER_CONDITIONAL = object()


def compression(policy: CompressionPolicy) -> Middleware:
    """RFC-aware buffered gzip/Brotli negotiation and cache-variant isolation.

    Uses normal after-middleware; never changes request identity and never
    guesses that unknown/binary representations are compressible.
    """
    if not isinstance(policy, CompressionPolicy):
        raise ConfigError('compression requires a CompressionPolicy')

    def before(request: Request) -> After | Request:
        """Negotiate response compression from the incoming request."""
        preferences = _encoding_preferences(request.headers)
        # A Range applies to the selected identity representation. Do not
        # transform a file/range response, even when If-Range misses.
        if request.headers.values('Range'):
            return request
        request = request.with_context(_COMPRESSION_DEFER_CONDITIONAL, True)

        def finish(response: Response) -> Response:
            # A preceding conditional helper deferred until the selected
            # compressed/identity representation was known. It MUST compare
            # against this variant's ETag, never the original entity tag.
            """Finalize the middleware result with the required cache variation."""
            context = dict(request.context)
            context.pop(_COMPRESSION_DEFER_CONDITIONAL, None)
            return conditional(replace(request, context=context), response)

        def after(response: Response) -> Response:
            # Streaming bodies are already owned, linear and potentially
            # unbounded. No buffering or speculative encoding is permitted.
            """Encode an eligible downstream response using the selected coding."""
            if isinstance(response.body, StreamBody):
                return finish(response)
            raw = response.bytes
            if not _can_compress(response, len(raw), policy.max_bytes):
                return finish(response)
            # Always vary for an eligible representation, even when the
            # caller sent no Accept-Encoding or the body is too small.
            response = _cors_vary(response, 'Accept-Encoding')
            if preferences is None:
                return finish(response)
            available = []
            if policy.brotli_enabled:
                available.append(('br', _coding_quality(preferences, 'br')))
            if policy.gzip_enabled:
                available.append(('gzip', _coding_quality(preferences, 'gzip')))
            available = [(coding, q) for coding, q in available if q > 0]
            identity_quality = _coding_quality(preferences, 'identity')
            best = max((q for _, q in available), default=0.0)
            if identity_quality > 0 and (len(raw) < policy.min_bytes or best <= 0):
                return finish(response)
            if best <= 0:
                # The caller explicitly excludes identity and every supported
                # coding. A representation cannot be sent acceptably.
                raise WebError('NotAcceptable', 'No acceptable content coding')
            # An omitted identity quality makes identity acceptable, but it
            # does not override the client's explicit coding preference.
            if 'identity' in preferences and identity_quality > best:
                return finish(response)
            # Brotli wins equal-quality ties, but not if gzip has higher q.
            coding = next(c for c, q in available if q == best)
            if coding == 'br':
                import brotli
                compressed = brotli.compress(raw, quality=5)
            else:
                compressed = gzip.compress(raw, compresslevel=6, mtime=0)
            # Never transfer a strong validator from the identity variant to
            # compressed bytes. This strong validator identifies exact output.
            if response.headers.values('ETag'):
                variant_tag = hashlib.sha256(compressed).hexdigest()
                response = response.with_header('ETag', f'"sha256-{variant_tag}"')
            response = response.without_header('Content-Length')
            response = response.with_header('Content-Encoding', coding)
            return finish(replace(response, body=compressed))

        return After(request, after)

    return before


@dataclass(frozen=True)
class PreparedApp:
    app: App
    routes: tuple[Route, ...]


def prepare(app: App) -> PreparedApp:
    """Build the dispatch plan from the app's routes and policies."""
    if not isinstance(app, App):
        raise ConfigError("An App is required")
    for i, route in enumerate(app.entries):
        for other in app.entries[:i]:
            if route.method == other.method and _overlap_equal_specificity(route.template, other.template):
                raise ConfigError(f"Ambiguous {route.method} route: {route.path} vs {other.path}")
    return PreparedApp(app, app.entries)


def routes(app: App | Router) -> tuple[tuple[str, str, bool], ...]:
    # Router introspection must enforce the same route-table validation as an
    # App, rather than reporting ambiguous entries as if they were executable.
    """List the routes exposed by the app or router."""
    if not isinstance(app, (Router, App)):
        raise ConfigError('An App or Router is required')
    prepare(app if isinstance(app, App) else App(app.entries, app.middleware))
    result = [(route.method, route.path, False) for route in app.entries]
    existing = {(route.method, route.path) for route in app.entries if route.method != "WEBSOCKET"}
    generated: set[tuple[str, str]] = set()
    for route in app.entries:
        if route.method == "WEBSOCKET":
            continue
        for method in (('HEAD', 'OPTIONS') if route.method == 'GET' else ('OPTIONS',)):
            identity = (method, route.path)
            if identity not in existing and identity not in generated:
                result.append((method, route.path, True))
                generated.add(identity)
    return tuple(result)


def route_url(app: App | Router, method: str, path: str,
              params: Mapping[str, str], query: Params = Params()) -> str:
    """Build a route URL by substituting encoded capture values."""
    if not any(route.method == method and route.path == path for route in app.entries):
        raise ConfigError("Unknown route identity")
    tokens = _validate_template(path)
    expected = {v for kind, v in tokens if kind != "literal"}
    if expected != set(params):
        raise ConfigError("Missing or unexpected route parameters")
    parts = []
    for kind, value in tokens:
        if kind == "literal":
            parts.append(value)
        elif kind == "capture":
            item = params[value]
            if item in (".", "..") or not item or _CONTROL.search(item):
                raise ConfigError("Unsafe route parameter")
            parts.append(quote(item, safe="~"))
        else:
            items = params[value].split("/") if params[value] else []
            if any(item in (".", "..", "") or _CONTROL.search(item) for item in items):
                raise ConfigError("Unsafe catch-all")
            parts.extend(quote(item, safe="~") for item in items)
    result = "/" + "/".join(parts)
    suffix = query.render_query()
    return result + ("?" + suffix if suffix else "")


def _error_response(request: Request, error: Exception, stage: str, app: App) -> Response:
    """Convert a dispatch failure into an HTTP response."""
    from valiance.runtime.runtime_values import PanicSignal, is_recoverable_fault
    if isinstance(error, PanicSignal) and not is_recoverable_fault(error):
        raise error
    if isinstance(error, WebError) and error.stage == 'ResponseNormalization':
        stage = 'ResponseNormalization'
    if app.on_error is not None:
        try:
            result = app.on_error(request, error, stage)
            if not isinstance(result, Response):
                raise TypeError("Error handler must return Response")
            return result
        except Exception:
            return Response(500, "Internal Server Error")
    if not isinstance(error, WebError):
        return Response(500, "Internal Server Error")
    status = (500 if error.stage == 'ResponseNormalization' and error.name in
              {'InvalidCookie', 'InvalidHeader', 'AmbiguousHeader'} else error.status)
    message = error.message if status < 500 and error.stage != "Routing" and error.message else (
        {404: "Not Found", 405: "Method Not Allowed", 501: "Not Implemented"}.get(status, "Bad Request")
        if status < 500 else "Internal Server Error")
    response = Response(status, message)
    if status == 405:
        response = response.with_header("Allow", ", ".join(error.allowed))
    if status == 401:
        response = response.with_header("WWW-Authenticate", "Bearer")
    return response


def _identity_ok(before: Request, after: Request) -> bool:
    """Check that middleware preserved the original request identity."""
    return (before.method == after.method and before.original_method == after.original_method
            and before.path == after.path and before.raw_target == after.raw_target
            and before.route_params == after.route_params and before.body == after.body
            and before.body_stream is after.body_stream
            and before.authority == after.authority and before.request_id == after.request_id
            and before.headers == after.headers and before.query == after.query
            and before.scheme == after.scheme and before.protocol == after.protocol
            and before.multipart_limits == after.multipart_limits)


def _allow(methods: Iterable[str]) -> tuple[str, ...]:
    """Build the allowed method list for the matched routes."""
    enabled = set(methods)
    if "GET" in enabled:
        enabled.add("HEAD")
    enabled.add("OPTIONS")
    return tuple(method for method in _METHODS if method in enabled)


def select_websocket_route(app: App | PreparedApp, target: str
                           ) -> tuple[Route, dict[str, str]] | None:
    """Find the most-specific WebSocket upgrade route for the target."""
    prepared = prepare(app) if isinstance(app, App) else app
    path, _ = validate_target(target)
    matches = [(r, caps) for r in prepared.routes if r.method == 'WEBSOCKET'
               and (caps := _match(r.template, path)) is not None]
    if not matches:
        return None
    best = max(_specificity(r.template) for r, _ in matches)
    return next((r, caps) for r, caps in matches if _specificity(r.template) == best)


def select_body_policy(app: App | PreparedApp, method: str,
                       target: str) -> tuple[str, int | None]:
    """Select the effective route's admission mode and optional byte limit.

    Method selection happens only among the most-specific path matches, just
    as in dispatch. A path match for another method cannot grant body limits
    to a 404/405 request or bypass the transport's global limits.
    """
    prepared = prepare(app) if isinstance(app, App) else app
    path, _ = validate_target(target)
    matches = [route for route in prepared.routes if _match(route.template, path) is not None]
    if not matches:
        return 'Buffered', None
    best = max(_specificity(route.template) for route in matches)
    matching = next((route for route in matches if
                     _specificity(route.template) == best and route.method == method), None)
    return (matching.body_mode, matching.max_body_bytes) if matching is not None else ('Buffered', None)


def select_body_mode(app: App | PreparedApp, method: str, target: str) -> str:
    """Compatibility selector for clients interested only in body mode."""
    return select_body_policy(app, method, target)[0]


def _dispatch_unlogged(app: App | PreparedApp, request: Request, *,
                       defer_head_stream_close: bool = False) -> Response:
    """Run route matching and middleware without emitting the request log."""
    prepared = prepare(app) if isinstance(app, App) else app
    if not isinstance(prepared, PreparedApp) or not isinstance(request, Request):
        raise DispatchError("Expected prepared application and validated synthetic request")
    # Never trust an unchecked Request constructor, including one built by a host.
    try:
        actual_path, actual_query = validate_target(request.raw_target)
        if actual_path != request.path or actual_query != request.query:
            raise DispatchError("Synthetic request target does not match its decoded fields")
    except WebError as exc:
        raise DispatchError(str(exc)) from exc
    app = prepared.app
    selected_mode, route_limit = select_body_policy(prepared, request.method, request.raw_target)
    if selected_mode == 'Stream' and request.body_stream is None:
        raise DispatchError('Streaming route requires testRequestStream or a transport-owned source')
    if selected_mode != 'Stream' and request.body_stream is not None:
        request.body_stream.close()
        raise DispatchError('A streamed synthetic request cannot enter a buffered route')
    if route_limit is not None:
        if request.body_stream is not None:
            # Apply the route cap before handing the source to any middleware.
            # The existing source cap can only be tightened, never raised.
            if request.body_stream.claimed:
                request.body_stream.close()
                raise DispatchError('A synthetic request stream was already claimed')
            request.body_stream.max_bytes = min(request.body_stream.max_bytes, route_limit)
        elif len(request.body) > route_limit:
            return _error_response(request, WebError('BodyTooLarge',
                'Request body exceeds route limit'), 'BodyAdmission', app)
    websocket_only = any(route.method == 'WEBSOCKET' and
                         _match(route.template, request.path) is not None
                         for route in prepared.routes)
    matches = [(route, captures) for route in prepared.routes
               if route.method != 'WEBSOCKET' and
               (captures := _match(route.template, request.path)) is not None]
    chosen: list[tuple[Route, dict[str, str]]] = []
    if matches:
        best = max(_specificity(route.template) for route, _ in matches)
        chosen = [(route, params) for route, params in matches if _specificity(route.template) == best]
        # Preparation checks ambiguous method+pattern, so all selected patterns have
        # the same capture shape even when methods differ.
        request = replace(request, route_params=chosen[0][1])
    methods = {route.method for route, _ in chosen}
    allowed = _allow(methods)
    scoped = chosen[0][0].middleware if chosen else ()
    request = replace(request, context={**request.context, _CORS_ROUTE_METHODS: allowed})
    middleware = (*app.middleware, *scoped)
    afters: list[Callable[[Response], Response]] = []
    result: Response | None = None
    current = request
    for callback in middleware:
        try:
            outcome = callback(current)
            if isinstance(outcome, Exception):
                if isinstance(outcome, WebError) and outcome.name == 'MethodNotAllowed' and not outcome.allowed:
                    outcome = replace(outcome, allowed=allowed)
                raise outcome
            if isinstance(outcome, After):
                if not _identity_ok(current, outcome.request) or not callable(outcome.after):
                    raise WebError("InvalidResponse", "Middleware changed request identity")
                afters.append(outcome.after)
                current = outcome.request
            elif isinstance(outcome, Request):
                if not _identity_ok(current, outcome):
                    raise WebError("InvalidResponse", "Middleware changed request identity")
                current = outcome
            elif isinstance(outcome, Response):
                result = outcome
                break
            else:
                raise WebError("InvalidResponse", "Middleware must return Request, Response, Error or After")
        except Exception as exc:
            result = _error_response(current, exc, "BeforeMiddleware", app)
            break
    if result is None:
        try:
            if current.method not in _METHODS and current.method != 'TRACE':
                raise WebError("NotImplemented", "Not Implemented", stage="Routing")
            if not chosen:
                if request.method == 'GET' and websocket_only:
                    raise WebError('UpgradeRequired', 'WebSocket upgrade required', stage='Routing')
                raise WebError("NotFound", "Not Found", stage="Routing")
            if current.method == "OPTIONS" and "OPTIONS" not in methods:
                result = Response(204, headers=Headers((("Allow", ", ".join(allowed)),)))
            else:
                method = current.method
                route = next((route for route, _ in chosen if route.method == method), None)
                if method == "HEAD" and route is None:
                    route = next((route for route, _ in chosen if route.method == "GET"), None)
                    if route is not None:
                        current = replace(current, method="GET")
                if route is None:
                    if method not in _METHODS and method != "TRACE":
                        raise WebError("NotImplemented", "Not Implemented", stage="Routing")
                    raise WebError("MethodNotAllowed", "Method Not Allowed", allowed, stage="Routing")
                outcome = route.handler(current)
                if isinstance(outcome, Exception):
                    if isinstance(outcome, WebError) and outcome.name == 'MethodNotAllowed' and not outcome.allowed:
                        outcome = replace(outcome, allowed=allowed)
                    raise outcome
                result = normalize_result(outcome)
        except Exception as exc:
            result = _error_response(current, exc, "Routing" if isinstance(exc, WebError) and exc.stage == "Routing" else "Handler", app)
    for callback in reversed(afters):
        try:
            next_response = callback(result)
            if isinstance(next_response, Exception):
                raise next_response
            if not isinstance(next_response, Response):
                raise WebError("InvalidResponse", "After callback must return a Response")
            if isinstance(result.body, StreamBody) and result.body is not next_response.body:
                result.body.close()
            result = next_response
        except Exception as exc:
            if isinstance(result.body, StreamBody):
                result.body.close()
            result = _error_response(current, exc, "AfterMiddleware", app)
    try:
        validate_response(result, head=request.method == "HEAD")
    except WebError as exc:
        if isinstance(result.body, StreamBody):
            result.body.close()
        result = _error_response(current, exc, "ResponseNormalization", app)
    if request.method == "HEAD":
        stream = isinstance(result.body, StreamBody)
        if stream:
            known_length = result.body.length
            # The HTTP/2 adapter owns deferred HEAD source cleanup. An
            # application-defined close() must not block multiplexed frame
            # handling or delay END_STREAM for an otherwise complete HEAD.
            if not defer_head_stream_close:
                result.body.close()
        else:
            known_length = len(result.bytes)
        result = replace(result, body=result.body if stream and defer_head_stream_close
                         else None, trailers=Headers())
        if (known_length is not None and result.status not in (204, 304)
                and result.headers.one("Content-Length") is None):
            result = result.with_header("Content-Length", str(known_length))
    if request.body_stream is not None:
        request.body_stream.close()
    return result

# Per-thread recursion guard: a logger may invoke dispatch itself. Never
# recursively call that logger or let logging failures change a HTTP response.
_LOGGING_GUARD = local()
_SAFE_LOG = logging.getLogger(__name__)


def dispatch(app: App | PreparedApp, request: Request, *,
             defer_head_stream_close: bool = False) -> Response:
    """Dispatch a request and report the outcome to the configured logger."""
    started = time.monotonic()
    response = _dispatch_unlogged(app, request,
                                  defer_head_stream_close=defer_head_stream_close)
    prepared = app if isinstance(app, PreparedApp) else None
    application = prepared.app if prepared is not None else app
    callback = application.logger if isinstance(application, App) else None
    if callback is not None and not getattr(_LOGGING_GUARD, 'active', False):
        matched = None
        selected_routes = prepared.routes if prepared is not None else application.entries
        for route in selected_routes:
            if route.method == request.method or (request.method == 'HEAD' and route.method == 'GET'):
                if _match(route.template, request.path) is not None:
                    matched = route.path
                    break
        event = LogEvent(
            request_id=request.request_id, method=request.method,
            route_template=matched, status=response.status,
            protocol=request.protocol,
            elapsed_milliseconds=(time.monotonic() - started) * 1000,
            request_bytes=len(request.body),
            response_bytes=0 if isinstance(response.body, StreamBody) else len(response.bytes),
            peer=request.peer, client_address=request.client_address,
        )
        _LOGGING_GUARD.active = True
        try:
            callback(event)
        except Exception:
            # Do not leak exception text (which could include secrets),
            # recurse into the error hook, or corrupt an already-built response.
            _SAFE_LOG.warning('std.web completion logger callback failed')
        finally:
            _LOGGING_GUARD.active = False
    return response


# Request and response semantics shared by the socket-free dispatcher and all
# future protocol adapters.  These helpers do not perform network IO.
_CONTENT_TYPE = re.compile(r"^\s*([!#$%&'*+.^_`|~0-9a-z-]+/[!#$%&'*+.^_`|~0-9a-z-]+)(?:\s*;\s*(.*))?$", re.I)
_COOKIE_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_COOKIE_VALUE = re.compile(r'^[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*$')


def content_type(request: Request) -> tuple[str, dict[str, str]] | None:
    """Parse the declared media type, rejecting contradictory/invalid parameters."""
    raw = request.headers.one("Content-Type")
    if raw is None:
        return None
    match = _CONTENT_TYPE.fullmatch(raw)
    if not match:
        raise WebError("InvalidHeader", "Invalid Content-Type")
    parameters: dict[str, str] = {}
    for part in (match.group(2) or "").split(";"):
        if not part.strip():
            continue
        name, sep, value = part.strip().partition("=")
        if not sep or not _TOKEN.fullmatch(name.strip()):
            raise WebError("InvalidHeader", "Invalid Content-Type parameter")
        name = name.strip().lower()
        value = value.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif not _TOKEN.fullmatch(value):
            raise WebError("InvalidHeader", "Invalid Content-Type parameter value")
        if name in parameters:
            raise WebError("InvalidHeader", "Duplicate Content-Type parameter")
        parameters[name] = value
    return match.group(1).lower(), parameters


_QVALUE = re.compile(r'(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)\Z')


def _split_http_list(value: str, delimiter: str) -> list[str]:
    """Split an RFC field list without treating delimiters in quotes as separators."""
    chunks, current, quoted, escaped = [], [], False, False
    for char in value:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\" and quoted:
            escaped = True
        elif char == '"':
            quoted = not quoted
            current.append(char)
        elif char == delimiter and not quoted:
            chunks.append(''.join(current).strip())
            current = []
        else:
            current.append(char)
    if quoted or escaped:
        raise WebError('InvalidHeader', 'Unterminated quoted HTTP parameter')
    chunks.append(''.join(current).strip())
    return chunks


def _media_parameters(parts: list[str]) -> dict[str, str]:
    """Parse a media type and its parameters."""
    parameters: dict[str, str] = {}
    for part in parts:
        key, sep, value = part.partition('=')
        key, value = key.strip().lower(), value.strip()
        if not sep or not _TOKEN.fullmatch(key):
            raise WebError('InvalidHeader', 'Invalid media parameter')
        if value.startswith('"') and value.endswith('"') and len(value) >= 2:
            value = value[1:-1]
            if _CONTROL.search(value):
                raise WebError('InvalidHeader', 'Invalid quoted media parameter')
        elif not _TOKEN.fullmatch(value):
            raise WebError('InvalidHeader', 'Invalid media parameter value')
        if key in parameters:
            raise WebError('InvalidHeader', 'Duplicate media parameter')
        parameters[key] = value
    return parameters


def accepts(request: Request, media_type: str) -> bool:
    """Parse Accept quality and specificity; q=0 on a specific range wins over *.

    Absent Accept permits any media type. Malformed on-wire negotiation is
    an ordinary InvalidHeader error, never an open-ended parser exception.
    """
    requested = _split_http_list(media_type, ';')
    if not requested or not _CONTENT_TYPE.fullmatch(media_type):
        raise WebError('InvalidHeader', 'Invalid proposed media type')
    requested_type = requested[0].strip().lower()
    attributes = _media_parameters(requested[1:])
    if 'q' in attributes:
        raise WebError('InvalidHeader', 'Proposed media type must not contain quality')
    incoming = request.headers.values('Accept')
    if not incoming:
        return True
    best: tuple[int, int, int, float] | None = None
    for raw in incoming:
        for part in _split_http_list(raw, ','):
            if not part:
                raise WebError('InvalidHeader', 'Empty media range in Accept')
            segments = _split_http_list(part, ';')
            item = segments[0].strip().lower()
            if '/' not in item:
                raise WebError('InvalidHeader', 'Invalid Accept range')
            major, minor = item.split('/', 1)
            if not ((major == '*' and minor == '*') or
                    (major != '*' and _TOKEN.fullmatch(major) and (minor == '*' or _TOKEN.fullmatch(minor)))):
                raise WebError('InvalidHeader', 'Invalid Accept range')
            params = _media_parameters(segments[1:])
            qvalue = params.pop('q', '1')
            if not _QVALUE.fullmatch(qvalue):
                raise WebError('InvalidHeader', 'Invalid Accept quality')
            if (major not in ('*', requested_type.split('/', 1)[0]) or
                    minor not in ('*', requested_type.split('/', 1)[1]) or
                    any(attributes.get(k, '').lower() != v.lower() for k, v in params.items())):
                continue
            priority = (int(major != '*'), int(minor != '*'), len(params), float(qvalue))
            if best is None or priority[:3] > best[:3] or (priority[:3] == best[:3] and priority[3] > best[3]):
                best = priority
    return best is not None and best[3] > 0


def _body_capable(request: Request) -> None:
    """Check whether this request method exposes a body."""
    if request.method not in ("POST", "PUT", "PATCH", "QUERY"):
        raise WebError("BodyUnavailable", "The method has no request-body helper")


def body_bytes(request: Request, *, max_bytes: int = 16 * 1024 * 1024) -> bytes:
    """Read the buffered request body as bytes."""
    _body_capable(request)
    if request.body_stream is not None:
        raise WebError('BodyUnavailable', 'Streaming request does not support buffered body helpers')
    if len(request.body) > max_bytes:
        raise WebError("BodyTooLarge", "Request content exceeds the configured limit")
    return request.body


def body_text(request: Request) -> str:
    """Decode the buffered request body as text."""
    raw = body_bytes(request)
    media = content_type(request)
    if media is not None and media[1].get("charset", "utf-8").lower() != "utf-8":
        raise WebError("UnsupportedMediaType", "Only UTF-8 content is supported")
    try:
        return raw.decode("utf-8", "strict")
    except UnicodeDecodeError as exc:
        raise WebError("BadText", "Invalid UTF-8 request content") from exc


def body_json(request: Request) -> object:
    """Decode JSON from a request with an appropriate media type."""
    media = content_type(request)
    if media is None or (media[0] != "application/json" and
                         not (media[0].startswith("application/") and media[0].endswith("+json"))):
        raise WebError("UnsupportedMediaType", "Expected a JSON media type")
    if media[1].get("charset", "utf-8").lower() != "utf-8":
        raise WebError("UnsupportedMediaType", "JSON must use UTF-8")
    try:
        return json.loads(body_bytes(request).decode("utf-8", "strict"),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-JSON number")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise WebError("InvalidJson", "Malformed JSON request content") from exc


def form_params(request: Request, *, max_fields: int = 1000) -> Params:
    """Decode URL-encoded form fields from a buffered body."""
    media = content_type(request)
    if media is None or media[0] != "application/x-www-form-urlencoded":
        raise WebError("UnsupportedMediaType", "Expected URL-encoded form content")
    if media[1].get("charset", "utf-8").lower() != "utf-8":
        raise WebError("UnsupportedMediaType", "Form content must use UTF-8")
    try:
        return parse_params(body_bytes(request).decode("utf-8", "strict"), form=True,
                            max_fields=max_fields)
    except UnicodeDecodeError as exc:
        raise WebError("InvalidForm", "Invalid form encoding") from exc
    except WebError as exc:
        if exc.name in ("MalformedTarget", "BadRequest"):
            raise WebError("InvalidForm", "Invalid form content") from exc
        raise



@dataclass(frozen=True)
class BufferedUpload:
    """Bounded in-memory multipart file; filename is an untrusted label."""
    name: str
    filename: str
    content_type: str | None
    data: bytes = field(repr=False)

    @property
    def size(self) -> int:
        """Return the size of the buffered file in bytes."""
        return len(self.data)


@dataclass(frozen=True)
class MultipartForm:
    fields: Params
    files: tuple[BufferedUpload, ...] = ()

    def uploads(self, name: str) -> tuple[BufferedUpload, ...]:
        """Return all uploaded files for the supplied form field."""
        return tuple(item for item in self.files if item.name == name)

    def upload_one(self, name: str) -> BufferedUpload | None:
        """Return the sole upload and reject duplicate files for that field."""
        matches = self.uploads(name)
        if len(matches) > 1:
            raise WebError('AmbiguousParam', 'Multiple uploads have the same field name')
        return matches[0] if matches else None


_MULTIPART_BOUNDARY = re.compile(r"^[0-9A-Za-z'()+_,./:=? -]{1,70}$")


def _multipart_header_params(raw: str, expected: str) -> dict[str, str]:
    """Parse one Content-Disposition with quotes/escapes, not a comma-joined field."""
    try:
        sections = _split_http_list(raw, ';')
    except WebError as exc:
        raise WebError('InvalidMultipart', 'Malformed multipart parameters') from exc
    if not sections or sections[0].lower() != expected:
        raise WebError('InvalidMultipart', 'Expected form-data disposition')
    parameters: dict[str, str] = {}
    for section in sections[1:]:
        key, separator, value = section.partition('=')
        key, value = key.strip().lower(), value.strip()
        if not separator or not _TOKEN.fullmatch(key) or key in parameters:
            raise WebError('InvalidMultipart', 'Malformed or duplicate disposition parameter')
        if value.startswith('"'):
            if len(value) < 2 or not value.endswith('"'):
                raise WebError('InvalidMultipart', 'Unterminated disposition parameter')
            value = value[1:-1]
            result = []
            at = 0
            while at < len(value):
                if value[at] == '\\':
                    at += 1
                    if at >= len(value):
                        raise WebError('InvalidMultipart', 'Invalid disposition escape')
                result.append(value[at])
                at += 1
            value = ''.join(result)
        elif not _TOKEN.fullmatch(value):
            raise WebError('InvalidMultipart', 'Invalid disposition parameter')
        # The MIME header block itself is capped at 16 KiB. Let the
        # request's smaller name/filename quota report HTTP 413 separately.
        if _CONTROL.search(value) or len(value.encode('utf-8')) > 16384:
            raise WebError('InvalidMultipart', 'Invalid disposition parameter')
        parameters[key] = value
    if 'name' not in parameters or not parameters['name'] or 'filename*' in parameters:
        raise WebError('InvalidMultipart', 'Missing name or unsupported filename encoding')
    return parameters


def multipart(request: Request, *, max_parts: int | None = None,
              max_fields: int | None = None, max_files: int | None = None,
              max_field_bytes: int | None = None,
              max_header_bytes: int | None = None,
              max_file_bytes: int | None = None,
              max_name_bytes: int | None = None,
              max_filename_bytes: int | None = None) -> MultipartForm:
    """Strict, bounded buffered RFC 7578 subset; never decodes uploaded file bytes.

    This implementation intentionally does NOT provide streaming, temporary
    file ownership, or uploads larger than the HTTP buffered-body limit.
    """
    _body_capable(request)
    # An omitted override inherits the immutable request snapshot; callers may
    # tighten quotas for synthetic/core tests without widening server budgets.
    policy = request.multipart_limits
    selections = dict(max_parts=max_parts, max_fields=max_fields, max_files=max_files,
                      max_field_bytes=max_field_bytes, max_header_bytes=max_header_bytes,
                      max_file_bytes=max_file_bytes, max_name_bytes=max_name_bytes,
                      max_filename_bytes=max_filename_bytes)
    try:
        selected = replace(policy, **{key: min(getattr(policy, key), value)
                                       for key, value in selections.items() if value is not None})
    except (ValueError, TypeError, ConfigError) as exc:
        raise WebError('BadRequest', 'Invalid multipart parser limits') from exc
    max_parts, max_fields, max_files = selected.max_parts, selected.max_fields, selected.max_files
    max_field_bytes, max_header_bytes = selected.max_field_bytes, selected.max_header_bytes
    max_file_bytes = selected.max_file_bytes
    media = content_type(request)
    if media is None or media[0] != 'multipart/form-data':
        raise WebError('UnsupportedMediaType', 'Expected multipart/form-data')
    parameters = media[1]
    boundary = parameters.get('boundary')
    if (not boundary or not boundary.isascii() or
            not _MULTIPART_BOUNDARY.fullmatch(boundary) or boundary.endswith(' ')):
        raise WebError('InvalidMultipart', 'Invalid multipart boundary')
    if any(key != 'boundary' for key in parameters):
        raise WebError('InvalidMultipart', 'Unexpected multipart media parameter')
    body = body_bytes(request)  # Inherited 16 MiB total body limit.
    marker = b'--' + boundary.encode('ascii')
    if not body.startswith(marker):
        raise WebError('InvalidMultipart', 'Missing opening multipart boundary')
    pos = len(marker)
    if body[pos:pos + 2] == b'--':
        if body[pos + 2:] not in (b'', b'\r\n'):
            raise WebError('InvalidMultipart', 'Invalid closing boundary')
        return MultipartForm(Params())
    if body[pos:pos + 2] != b'\r\n':
        raise WebError('InvalidMultipart', 'Missing CRLF after multipart boundary')
    pos += 2
    fields: list[tuple[str, str]] = []
    files: list[BufferedUpload] = []
    count = 0
    delimiter = b'\r\n' + marker
    while True:
        count += 1
        if count > max_parts:
            raise WebError('BodyTooLarge', 'Too many multipart parts')
        header_end = body.find(b'\r\n\r\n', pos, pos + max_header_bytes + 4)
        if header_end < 0:
            raise WebError('InvalidMultipart', 'Malformed or oversized multipart headers')
        header_block = body[pos:header_end]
        if len(header_block) > max_header_bytes:
            raise WebError('BodyTooLarge', 'Multipart headers exceed limit')
        lines = header_block.split(b'\r\n')
        if len(lines) > 16 or any(not line or line[:1] in (b' ', b'\t') for line in lines):
            raise WebError('InvalidMultipart', 'Invalid multipart header layout')
        part_headers: dict[str, str] = {}
        for line in lines:
            if b':' not in line:
                raise WebError('InvalidMultipart', 'Invalid multipart header')
            raw_name, raw_value = line.split(b':', 1)
            try:
                name = raw_name.decode('ascii').lower()
                value = raw_value.strip(b' \t').decode('utf-8', 'strict')
            except UnicodeError as exc:
                raise WebError('InvalidMultipart', 'Invalid multipart header encoding') from exc
            if not _TOKEN.fullmatch(name) or _CONTROL.search(value) or name in part_headers:
                raise WebError('InvalidMultipart', 'Duplicate or invalid multipart header')
            part_headers[name] = value
        if 'content-disposition' not in part_headers:
            raise WebError('InvalidMultipart', 'Missing Content-Disposition')
        disposition = _multipart_header_params(part_headers['content-disposition'], 'form-data')
        _multipart_name_limits(disposition, max_name_bytes=selected.max_name_bytes,
                               max_filename_bytes=selected.max_filename_bytes)
        if any(key not in ('content-disposition', 'content-type', 'content-transfer-encoding')
               for key in part_headers):
            raise WebError('InvalidMultipart', 'Unsupported multipart part header')
        if part_headers.get('content-transfer-encoding', 'binary').lower() != 'binary':
            raise WebError('InvalidMultipart', 'Transfer encoded parts are not supported')
        part_media = part_headers.get('content-type')
        if part_media is not None and not _CONTENT_TYPE.fullmatch(part_media):
            raise WebError('InvalidMultipart', 'Invalid part media type')
        pos = header_end + 4
        # Scan only complete delimiters, never split binary data on prefix matches.
        search = pos
        while True:
            part_end = body.find(delimiter, search)
            if part_end < 0:
                raise WebError('InvalidMultipart', 'Missing final multipart boundary')
            after_marker = part_end + len(delimiter)
            if body[after_marker:after_marker + 2] in (b'--', b'\r\n'):
                break
            search = part_end + 1
        raw_data = body[pos:part_end]
        field_name = disposition['name']
        if 'filename' in disposition:
            if len(files) >= max_files:
                raise WebError('BodyTooLarge', 'Too many multipart files')
            if len(raw_data) > max_file_bytes:
                raise WebError('BodyTooLarge', 'Multipart file exceeds per-file limit')
            files.append(BufferedUpload(field_name, disposition['filename'], part_media, raw_data))
        else:
            if len(fields) >= max_fields or len(raw_data) > max_field_bytes:
                raise WebError('BodyTooLarge', 'Multipart field limit exceeded')
            if part_media is not None:
                matched = _CONTENT_TYPE.fullmatch(part_media)
                # Field text uses UTF-8 only; never silently guess another charset.
                assert matched is not None
                if matched.group(1).lower() != 'text/plain':
                    raise WebError('InvalidMultipart', 'Unsupported multipart field media type')
                if matched.group(2):
                    params = _media_parameters(_split_http_list(matched.group(2), ';'))
                    if any(k != 'charset' for k in params) or params.get('charset', 'utf-8').lower() != 'utf-8':
                        raise WebError('InvalidMultipart', 'Unsupported multipart field encoding')
            try:
                text = raw_data.decode('utf-8', 'strict')
            except UnicodeError as exc:
                raise WebError('InvalidMultipart', 'Invalid UTF-8 multipart field') from exc
            fields.append((field_name, text))
        pos = after_marker
        if body[pos:pos + 2] == b'--':
            if body[pos + 2:] not in (b'', b'\r\n'):
                raise WebError('InvalidMultipart', 'Trailing bytes after closing boundary')
            return MultipartForm(Params(tuple(fields)), tuple(files))
        pos += 2


from valiance.std._web_multipart_stream import MultipartPart, UploadStream, multipart_stream


def save_upload(upload: BufferedUpload, destination: str) -> None:
    """Save to an explicit application-chosen path; never use upload.filename.

    Create exclusively (no clobbering existing files/symlinks), with private
    initial permissions. Streaming uploads and owned leases are not yet exposed.
    """
    import os
    if not isinstance(upload, BufferedUpload) or not destination:
        raise WebError('Internal', 'Invalid application upload destination')
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
    try:
        descriptor = os.open(destination, flags, 0o600)
    except FileExistsError as exc:
        raise WebError('Conflict', 'The destination file already exists') from exc
    except OSError as exc:
        raise WebError('Internal', 'Unable to create upload destination') from exc
    try:
        start = 0
        while start < len(upload.data):
            amount = os.write(descriptor, upload.data[start:])
            if amount <= 0:
                raise OSError('Unable to write multipart upload')
            start += amount
    except OSError as exc:
        # Only attempt to remove the freshly-created path; never overwrite an
        # existing destination. The application controls its destination dir.
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise WebError('Internal', 'Unable to save upload') from exc
    finally:
        os.close(descriptor)

def cookies(request: Request) -> Params:
    """Conservative Cookie header parsing; preserve pairs and duplicates."""
    pairs: list[tuple[str, str]] = []
    for raw in request.headers.values("Cookie"):
        for item in raw.split(";"):
            item = item.strip()
            if not item:
                continue
            name, sep, value = item.partition("=")
            name, value = name.strip(), value.strip()
            if not sep or not _COOKIE_NAME.fullmatch(name):
                raise WebError("InvalidCookie", "Malformed Cookie header")
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            if not _COOKIE_VALUE.fullmatch(value):
                raise WebError("InvalidCookie", "Malformed cookie value")
            pairs.append((name, value))
    return Params(tuple(pairs))


def cookie(request: Request, name: str) -> str | None:
    """Look up a single named cookie in the request."""
    try:
        return cookies(request).one(name)
    except WebError as exc:
        if exc.name == "AmbiguousParam":
            raise WebError("AmbiguousCookie", "Repeated Cookie name") from exc
        raise


def with_cookie(response: Response, name: str, value: str, *, path: str = "/",
                domain: str | None = None, http_only: bool = True, secure: bool = True,
                same_site: str = "Lax", max_age: int | None = None,
                expires: int | None = None, partitioned: bool = False) -> Response:
    """Emit one independently validated Set-Cookie field, without encoding input."""
    if not isinstance(name, str) or not _COOKIE_NAME.fullmatch(name) or not isinstance(value, str) or not _COOKIE_VALUE.fullmatch(value):
        raise WebError("InvalidCookie", "Invalid cookie name or value")
    if not isinstance(path, str) or not path.startswith("/") or any(
            not (0x20 <= ord(ch) <= 0x3a or 0x3c <= ord(ch) <= 0x7e)
            for ch in path):
        raise WebError("InvalidCookie", "Invalid cookie path")
    if domain is not None:
        if (not isinstance(domain, str) or len(domain) > 253
                or not all(0 < len(label) <= 63 and re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
                           for label in domain.split('.'))):
            raise WebError("InvalidCookie", "Invalid cookie domain")
    if same_site not in ("Strict", "Lax", "None"):
        raise WebError("InvalidCookie", "Invalid SameSite policy")
    if not isinstance(http_only, bool) or not isinstance(secure, bool) or not isinstance(partitioned, bool):
        raise WebError("InvalidCookie", "Invalid cookie flags")
    if (same_site == "None" or partitioned or name.startswith(("__Secure-", "__Host-", "__Http-"))) and not secure:
        raise WebError("InvalidCookie", "Cookie policy requires Secure")
    if name.startswith(("__Http-", "__Host-Http-")) and not http_only:
        raise WebError("InvalidCookie", "Cookie prefix requires HttpOnly")
    if name.startswith("__Host-") and (path != "/" or domain is not None):
        raise WebError("InvalidCookie", "__Host- cookie requires host-only Path=/")
    if max_age is not None and (type(max_age) is not int or max_age < -2**63 or max_age > 2**63 - 1):
        raise WebError("InvalidCookie", "Invalid Max-Age")
    if expires is not None and type(expires) is not int:
        raise WebError("InvalidCookie", "Invalid Expires")
    parts = [f"{name}={value}", f"Path={path}"]
    if domain is not None:
        parts.append(f"Domain={domain}")
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    if expires is not None:
        try:
            parts.append(f"Expires={formatdate(expires, usegmt=True)}")
        except (ValueError, OverflowError, OSError) as exc:
            raise WebError("InvalidCookie", "Invalid Expires") from exc
    if secure:
        parts.append("Secure")
    if http_only:
        parts.append("HttpOnly")
    parts.append(f"SameSite={same_site}")
    if partitioned:
        parts.append("Partitioned")
    return response.add_header("Set-Cookie", "; ".join(parts))


def delete_cookie(response: Response, name: str, *, path: str = "/",
                  domain: str | None = None, partitioned: bool = False) -> Response:
    """Expire a cookie by writing an immediate deletion header."""
    return with_cookie(response, name, "", path=path, domain=domain,
                       max_age=0, expires=0, partitioned=partitioned)


def created(location: str, response: Response | None = None) -> Response:
    # Redirect validates location syntax but created must not redirect or change methods.
    """Build a 201 response with the supplied resource location."""
    location_header = redirect(location).headers.one("Location")
    return replace(response or Response(201), status=201).with_header("Location", location_header)


@dataclass(frozen=True)
class Validators:
    """Current representation validators checked before application mutation.

    ``exists`` distinguishes a missing resource from one without an ETag.
    Callers must evaluate these fields against the current state, under the
    same application-level synchronization as the intended mutation.
    """
    etag: str | None = None
    last_modified: int | None = None
    exists: bool = True

    def __post_init__(self) -> None:
        """Validate the entity tag and modification time."""
        if self.etag is not None and not _is_etag(self.etag):
            raise WebError('InvalidETag', 'Invalid representation ETag')
        if self.last_modified is not None and not isinstance(self.last_modified, int):
            raise WebError('InvalidHeader', 'Invalid representation timestamp')


# Entity-tag grammar is intentionally not confused with generic HTTP token
# grammar: opaque tags may contain backslashes but cannot contain quotes or
# controls. The wire list parser rejects unexpected trailing values.
_ETAG = re.compile(r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"\Z')


def _is_etag(value: str) -> bool:
    """Check whether a string is a valid entity tag."""
    return bool(_ETAG.fullmatch(value))


def _etag_list(raw: str) -> tuple[str, ...] | None:
    """Return parsed entity tags, or None for the literal wildcard ``*``."""
    if raw.strip() == '*':
        return None
    # DQUOTE is not allowed inside the opaque tag, so commas are separators
    # only outside quoted tags; a comma *inside* an ETag remains content.
    items: list[str] = []
    cursor = 0
    while cursor < len(raw):
        while cursor < len(raw) and raw[cursor] in ' \t':
            cursor += 1
        match = re.match(r'(?:W/)?"[\x21\x23-\x7e\x80-\xff]*"', raw[cursor:])
        if not match:
            raise WebError('InvalidHeader', 'Invalid entity-tag condition')
        items.append(match.group())
        cursor += len(match.group())
        while cursor < len(raw) and raw[cursor] in ' \t':
            cursor += 1
        if cursor == len(raw):
            break
        if raw[cursor] != ',':
            raise WebError('InvalidHeader', 'Invalid entity-tag list')
        cursor += 1
        if cursor == len(raw):
            raise WebError('InvalidHeader', 'Trailing comma in entity-tag list')
    if not items:
        raise WebError('InvalidHeader', 'Empty entity-tag list')
    return tuple(items)


def _etag_matches(raw: str, tag: str | None, *, weak: bool, exists: bool = True) -> bool:
    """Compare entity tags with the requested comparison strength."""
    candidates = _etag_list(raw)
    if candidates is None:
        return exists
    if tag is None:
        return False
    compared = tag.removeprefix('W/') if weak else tag
    if not weak and tag.startswith('W/'):
        return False
    return any((candidate.removeprefix('W/') if weak else candidate) == compared
               and (weak or not candidate.startswith('W/')) for candidate in candidates)


def _http_date(value: str):
    """Parse an HTTP timestamp for conditional request evaluation."""
    from email.utils import parsedate_to_datetime
    from datetime import timezone
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return None
        return date.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError, IndexError):
        return None


def preconditions(request: Request, validators: Validators) -> None:
    """Check mutation preconditions *before* committing any state changes.

    The validator snapshot and mutation must be protected by application
    synchronization: HTTP preconditions do not create database transactions.
    """
    if_match = request.headers.one('If-Match')
    if if_match is not None:
        if not _etag_matches(if_match, validators.etag, weak=False,
                             exists=validators.exists):
            raise WebError('PreconditionFailed', 'If-Match precondition failed')
    else:
        unmodified = request.headers.one('If-Unmodified-Since')
        if unmodified is not None and validators.exists and validators.last_modified is not None:
            date = _http_date(unmodified)
            from datetime import datetime, timezone
            if date is not None:
                try:
                    updated = datetime.fromtimestamp(validators.last_modified, timezone.utc)
                except (OverflowError, ValueError, OSError) as exc:
                    raise WebError('InvalidHeader', 'Invalid validator timestamp') from exc
                if updated > date:
                    raise WebError('PreconditionFailed', 'If-Unmodified-Since precondition failed')
    none_match = request.headers.one('If-None-Match')
    if none_match is not None and _etag_matches(none_match, validators.etag,
                                                 weak=True, exists=validators.exists):
        if request.method not in ('GET', 'HEAD'):
            raise WebError('PreconditionFailed', 'If-None-Match precondition failed')
    return None


def conditional(request: Request, response: Response) -> Response:
    """Select 304 for safe conditional requests, retaining cache metadata."""
    if request.context.get(_COMPRESSION_DEFER_CONDITIONAL):
        return response
    if request.method not in ('GET', 'HEAD') or response.status != 200:
        return response
    tag = response.headers.one('ETag')
    modified = response.headers.one('Last-Modified')
    modified_date = _http_date(modified) if modified is not None else None
    validators = Validators(etag=tag, last_modified=int(modified_date.timestamp())
                            if modified_date is not None else None, exists=True)
    preconditions(request, validators)
    inm = request.headers.one('If-None-Match')
    ims = request.headers.one('If-Modified-Since')
    not_modified = False
    if inm is not None:
        not_modified = _etag_matches(inm, tag, weak=True)
    elif ims is not None and modified:
        earlier, since = _http_date(modified), _http_date(ims)
        not_modified = earlier is not None and since is not None and earlier <= since
    if not not_modified:
        return response
    if isinstance(response.body, StreamBody):
        response.body.close()
    allowed = {'etag', 'last-modified', 'cache-control', 'expires', 'vary', 'date', 'content-location'}
    return Response(304, headers=Headers(tuple((k, v) for k, v in response.headers.pairs
                                             if k.lower() in allowed)))

_STATUS_CODES = {
    'Continue': 100, 'SwitchingProtocols': 101, 'Processing': 102, 'EarlyHints': 103,
    'OK': 200, 'Created': 201, 'Accepted': 202, 'NonAuthoritativeInformation': 203,
    'NoContent': 204, 'ResetContent': 205, 'PartialContent': 206, 'MultiStatus': 207,
    'AlreadyReported': 208, 'IMUsed': 226, 'MultipleChoices': 300,
    'MovedPermanently': 301, 'Found': 302, 'SeeOther': 303, 'NotModified': 304,
    'UseProxy': 305, 'TemporaryRedirect': 307, 'PermanentRedirect': 308,
    'BadRequest': 400, 'Unauthorized': 401, 'PaymentRequired': 402,
    'Forbidden': 403, 'NotFound': 404, 'MethodNotAllowed': 405,
    'NotAcceptable': 406, 'ProxyAuthenticationRequired': 407,
    'RequestTimeout': 408, 'Conflict': 409, 'Gone': 410, 'LengthRequired': 411,
    'PreconditionFailed': 412, 'ContentTooLarge': 413, 'URITooLong': 414,
    'UnsupportedMediaType': 415, 'RangeNotSatisfiable': 416,
    'ExpectationFailed': 417, 'ImATeapot': 418, 'MisdirectedRequest': 421,
    'UnprocessableContent': 422, 'Locked': 423, 'FailedDependency': 424,
    'TooEarly': 425, 'UpgradeRequired': 426, 'PreconditionRequired': 428,
    'TooManyRequests': 429, 'RequestHeaderFieldsTooLarge': 431,
    'UnavailableForLegalReasons': 451, 'InternalServerError': 500,
    'NotImplemented': 501, 'BadGateway': 502, 'ServiceUnavailable': 503,
    'GatewayTimeout': 504, 'HTTPVersionNotSupported': 505,
    'VariantAlsoNegotiates': 506, 'InsufficientStorage': 507,
    'LoopDetected': 508, 'NotExtended': 510,
    'NetworkAuthenticationRequired': 511,
}

# File response support is deliberately bounded until owned file leases and
# streamed response bodies are available. All opened file descriptors are
# closed before a Response is returned; no raw filesystem paths are emitted.
_FILE_RESPONSE_LIMIT = 16 * 1024 * 1024
_FILE_MIME = {
    '.html': 'text/html; charset=utf-8', '.htm': 'text/html; charset=utf-8',
    '.css': 'text/css; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
    '.mjs': 'text/javascript; charset=utf-8', '.txt': 'text/plain; charset=utf-8',
    '.json': 'application/json', '.svg': 'image/svg+xml',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.gif': 'image/gif', '.webp': 'image/webp', '.ico': 'image/x-icon',
    '.pdf': 'application/pdf', '.wasm': 'application/wasm',
    '.woff': 'font/woff', '.woff2': 'font/woff2',
}


def _read_regular_file(fd: int, *, limit: int | None = None) -> tuple[bytes, object]:
    """Read a bounded regular file for a buffered file response."""
    import os
    import stat
    if limit is None:
        limit = _FILE_RESPONSE_LIMIT
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode):
        raise WebError('NotFound', 'Not Found')
    if metadata.st_size > limit:
        raise WebError('Internal', 'Static file exceeds the buffered response limit')
    with os.fdopen(fd, 'rb', closefd=False) as stream:
        content = stream.read(limit + 1)
    if len(content) > limit:
        raise WebError('Internal', 'Static file exceeds the buffered response limit')
    return content, metadata


def _file_response(content: bytes, filename: str, *, html: bool = False) -> Response:
    """Build a file response with content metadata and validators."""
    from pathlib import PurePath
    mime = 'text/html; charset=utf-8' if html else _FILE_MIME.get(PurePath(filename).suffix.lower(), 'application/octet-stream')
    return Response(200, content, Headers((
        ('Content-Type', mime), ('X-Content-Type-Options', 'nosniff'),
        ('Cache-Control', 'no-store'),
    )))


def file(path: str, *, html: bool = False) -> Response:
    """Serve one application-authorized path as bounded exact bytes."""
    import os
    if not isinstance(path, str) or not path or '\x00' in path:
        raise WebError('NotFound', 'Not Found')
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0))
        content, _ = _read_regular_file(fd)
    except FileNotFoundError as exc:
        raise WebError('NotFound', 'Not Found') from exc
    except PermissionError as exc:
        raise WebError('Forbidden', 'Forbidden') from exc
    except OSError as exc:
        raise WebError('Internal', 'File read failed') from exc
    finally:
        if fd is not None:
            os.close(fd)
    return _file_response(content, path, html=html)


def download(path: str, filename: str) -> Response:
    """Return exact file bytes with RFC 6266/8187-safe attachment metadata."""
    from urllib.parse import quote
    if not isinstance(filename, str) or not filename or _CONTROL.search(filename):
        raise WebError('InvalidHeader', 'Invalid download filename')
    basename = filename.replace('\\', '/').split('/')[-1]
    if not basename or basename in ('.', '..'):
        raise WebError('InvalidHeader', 'Invalid download filename')
    # The ASCII fallback is unambiguous and cannot introduce a new parameter.
    fallback = ''.join(c if c.isascii() and (c.isalnum() or c in '._-') else '_'
                       for c in basename)
    if not fallback or fallback in ('.', '..'):
        fallback = 'download'
    disposition = f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{quote(basename, safe="")}'
    return file(path).with_header('Content-Disposition', disposition)


def static_root(root: str) -> str:
    """Validate a trusted root at application construction (no symlink root)."""
    import os
    import stat
    if not isinstance(root, str) or not root or '\x00' in root:
        raise ConfigError('Static root must be a directory')
    # Without secure descriptor-relative directory walking, root confinement
    # cannot be guaranteed against symlink replacement. Fail closed rather
    # than silently downgrade on platforms lacking the necessary primitives.
    if (os.name != 'posix' or not getattr(os, 'O_NOFOLLOW', 0) or
            os.open not in os.supports_dir_fd):
        raise ConfigError('Secure static routes require POSIX no-follow directory descriptors')
    absolute = os.path.abspath(root)
    try:
        info = os.lstat(absolute)
        if not stat.S_ISDIR(info.st_mode):
            raise ConfigError('Static root must be a non-symlink directory')
    except OSError as exc:
        raise ConfigError('Static root is unavailable') from exc
    return absolute


def _static_read(root: str, segments: tuple[str, ...]) -> tuple[bytes, object, str]:
    """Walk a confined rooted path with dirfd + O_NOFOLLOW on POSIX."""
    import os
    opened = []
    nofollow = getattr(os, 'O_NOFOLLOW', 0)
    dirflags = os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_CLOEXEC', 0) | nofollow
    fileflags = os.O_RDONLY | getattr(os, 'O_CLOEXEC', 0) | nofollow
    try:
        opened.append(os.open(root, dirflags))
        for segment in segments[:-1]:
            opened.append(os.open(segment, dirflags, dir_fd=opened[-1]))
        name = segments[-1] if segments else 'index.html'
        fd = os.open(name, fileflags, dir_fd=opened[-1])
        opened.append(fd)
        import stat
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            opened.append(os.open('index.html', fileflags, dir_fd=fd))
            fd = opened[-1]
            name = 'index.html'
        content, metadata = _read_regular_file(fd)
        return content, metadata, name
    except WebError as exc:
        if exc.name == 'Internal':
            raise
        raise WebError('NotFound', 'Not Found') from exc
    except OSError as exc:
        # A missing entry, symlink, inaccessible directory, or device should not
        # disclose whether any host path exists to a remote request.
        raise WebError('NotFound', 'Not Found') from exc
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _static_ranges(value: str, size: int) -> list[tuple[int, int]] | None:
    """None means ignore malformed/unknown units; [] means unsatisfiable."""
    if not value.startswith('bytes='):
        return None
    fields = value[6:].split(',')
    if len(fields) > 16:
        return []
    ranges = []
    for item in fields:
        match = re.fullmatch(r'\s*(\d*)-(\d*)\s*', item)
        if not match or not (match.group(1) or match.group(2)):
            return None
        start, end = match.groups()
        if start:
            lo = int(start)
            hi = int(end) if end else size - 1
            if hi < lo:
                return None
        else:
            suffix = int(end)
            if not suffix:
                continue
            lo, hi = max(0, size - suffix), size - 1
        if lo >= size:
            continue
        ranges.append((lo, min(size - 1, hi)))
    return ranges


def static_file(root: str, request: Request) -> Response:
    """A confined static GET route with validators and bounded byte ranges."""
    import hashlib
    from datetime import timezone
    from email.utils import parsedate_to_datetime
    from secrets import token_hex
    name = request.route_params.get('path', '')
    segments = tuple(name.split('/')) if name else ()
    if any(not item or item in ('.', '..') or item.startswith('.') or
           ':' in item or '\\' in item or '\x00' in item for item in segments):
        raise WebError('NotFound', 'Not Found')
    content, stat_result, filename = _static_read(root, segments)
    etag = '"' + hashlib.sha256(content).hexdigest() + '"'
    response = _file_response(content, filename)
    response = response.with_header('ETag', etag)
    response = response.with_header('Last-Modified', formatdate(stat_result.st_mtime, usegmt=True))
    response = response.with_header('Accept-Ranges', 'bytes')
    response = conditional(request, response)
    if response.status != 200 or request.original_method == 'HEAD':
        return response
    field = request.headers.one('Range')
    if field is None:
        return response
    if_range = request.headers.one('If-Range')
    if if_range is not None:
        if if_range.startswith('W/') or if_range != etag:
            try:
                date = parsedate_to_datetime(if_range)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                if stat_result.st_mtime > date.timestamp():
                    return response
            except (TypeError, ValueError, OverflowError):
                return response
    ranges = _static_ranges(field, len(content))
    if ranges is None:
        return response
    if not ranges:
        return Response(416, b'', response.headers.with_header(
            'Content-Range', f'bytes */{len(content)}'))
    if len(ranges) == 1:
        lo, hi = ranges[0]
        return replace(response, status=206, body=content[lo:hi + 1], headers=response.headers.with_header(
            'Content-Range', f'bytes {lo}-{hi}/{len(content)}'))
    boundary = token_hex(12)
    mime = response.headers.one('Content-Type') or 'application/octet-stream'
    chunks = []
    for lo, hi in ranges:
        chunks.append((f'--{boundary}\r\nContent-Type: {mime}\r\n'
                       f'Content-Range: bytes {lo}-{hi}/{len(content)}\r\n\r\n').encode('ascii'))
        chunks.append(content[lo:hi + 1])
        chunks.append(b'\r\n')
    chunks.append(f'--{boundary}--\r\n'.encode('ascii'))
    headers = response.headers.with_header('Content-Type', f'multipart/byteranges; boundary={boundary}')
    return replace(response, status=206, body=b''.join(chunks), headers=headers)
