"""HTTP/1.1 listener with TLS ALPN dispatch to the HTTP/2 adapter.

This does not implement HTTP/3 or the complete
Valiance server-handle lifecycle contract. It supports bounded HTTP/1.1
request/response streaming, but is not a
claim that the complete v1.0 network API is available.

HTTP parsing/framing is delegated to h11, not reconstructed with string splits.
Every admitted logical request enters _web_core.dispatch, the same pipeline
used by socket-free tests.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from email.utils import formatdate
from http import HTTPStatus
import logging
import socket
import socketserver
import ssl
from threading import BoundedSemaphore, Event, Thread, Lock, current_thread
import time
from weakref import WeakSet

import h11

from valiance.std import _web_core as core
from valiance.runtime.concurrency import CancelledFault

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class TLSConfig:
    cert_file: str
    key_file: str

    def __post_init__(self) -> None:
        if not self.cert_file or not self.key_file:
            raise core.ConfigError('TLS certificate and private key paths are required')


def _tls_context(config: TLSConfig, protocols: tuple[str, ...]) -> ssl.SSLContext:
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.maximum_version = ssl.TLSVersion.TLSv1_3
        # Server ALPN preference is significant: OpenSSL selects the first
        # mutually offered protocol. Prefer HTTP/2 for clients such as curl
        # that offer both h2 and http/1.1, while retaining H1-only clients.
        context.set_alpn_protocols([{'HTTP/2': 'h2', 'HTTP/1.1': 'http/1.1'}[p]
                                    for p in ('HTTP/2', 'HTTP/1.1') if p in protocols])
        context.load_cert_chain(config.cert_file, config.key_file)
        return context
    except (OSError, ssl.SSLError, ValueError) as exc:
        raise core.ConfigError('Invalid TLS certificate/key or TLS 1.3 configuration') from exc


@dataclass(frozen=True)
class Limits:
    max_request_target_bytes: int = 8192
    max_header_bytes: int = 32768
    max_header_fields: int = 100
    # HTTP/2 compressed/decoded field-block and HPACK decoder budgets.
    max_decoded_header_bytes: int = 65536
    max_compressed_header_bytes: int = 32768
    max_hpack_dynamic_table_bytes: int = 16384
    max_buffered_body_bytes: int = 16 * 1024 * 1024
    max_streaming_body_bytes: int = 1024 ** 3
    max_form_fields: int = 1000
    max_multipart_parts: int = 128
    max_upload_file_bytes: int = 256 * 1024 * 1024
    max_multipart_field_bytes: int = 1024 * 1024
    max_multipart_name_bytes: int = 1024
    max_multipart_filename_bytes: int = 4096
    max_connections: int = 128
    # Server-wide request admission and HTTP/2 buffered-body reservations.
    # Defaults follow normative std.web §12.4 / §17.3.
    max_concurrent_requests: int = 1024
    # Live WebSocket sessions share a listener-wide admission budget.
    max_active_websockets: int = 1024
    max_websocket_frame_bytes: int = 1024 * 1024
    max_websocket_messages_bytes: int = 8 * 1024 * 1024
    # Maximum outstanding outbound WebSocket frame bytes per session.
    max_websocket_queued_send_bytes: int = 4 * 1024 * 1024
    web_socket_ping_interval_seconds: int = 30
    web_socket_pong_timeout_seconds: int = 10
    max_total_buffered_bytes: int = 256 * 1024 * 1024
    max_buffered_bytes_per_connection: int = 32 * 1024 * 1024
    max_concurrent_streams_per_connection: int = 100
    header_timeout_seconds: float = 10.0
    body_idle_timeout_seconds: float = 30.0
    connection_idle_timeout_seconds: float = 60.0
    write_idle_timeout_seconds: float = 30.0
    graceful_shutdown_seconds: float = 30.0

    @property
    def multipart_policy(self) -> core.MultipartLimits:
        return core.MultipartLimits(
            max_fields=self.max_form_fields,
            max_parts=self.max_multipart_parts,
            max_file_bytes=self.max_upload_file_bytes,
            max_field_bytes=self.max_multipart_field_bytes,
            max_name_bytes=self.max_multipart_name_bytes,
            max_filename_bytes=self.max_multipart_filename_bytes)

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if (type(value) not in (int, float) or value < 0 or
                    (value == 0 and name != 'graceful_shutdown_seconds')):
                raise core.ConfigError('HTTP/1.1 limits must be positive (except zero grace)')
            if (name.startswith('max_') and (type(value) is not int or value > 1024 ** 3)) or (
                    name.endswith('_seconds') and value > 86400):
                raise core.ConfigError('HTTP/1.1 resource limit is out of range')


def _headers_from_request(event: h11.Request, limits: Limits) -> core.Headers:
    if len(event.headers.raw_items()) > limits.max_header_fields:
        raise core.WebError("HeadersTooLarge", "Too many header fields")
    if sum(len(k) + len(v) + 4 for k, v in event.headers.raw_items()) > limits.max_header_bytes:
        raise core.WebError("HeadersTooLarge", "Header list too large")
    pairs: list[tuple[str, str]] = []
    for key, value in event.headers.raw_items():
        # RFC 9112: obs-fold, leading whitespace, and controls must never be
        # silently normalized into an application-facing header.
        if value.startswith((b' ', b'\t')) or b'\r' in value or b'\n' in value or b'\x00' in value:
            raise core.WebError("InvalidHeader", "Invalid HTTP/1 field value")
        try:
            pairs.append((key.decode('ascii', 'strict'), value.decode('latin-1', 'strict')))
        except UnicodeError as exc:
            raise core.WebError("InvalidHeader", "Invalid header encoding") from exc
    headers = core.Headers(tuple(pairs))
    hosts = headers.values('Host')
    if len(hosts) != 1 or not hosts[0] or hosts[0].strip() != hosts[0]:
        raise core.WebError("BadRequest", "Exactly one Host is required")
    if len(headers.values('Content-Length')) > 1:
        raise core.WebError("BadRequest", "Ambiguous Content-Length")
    if headers.values('Transfer-Encoding') and headers.values('Content-Length'):
        raise core.WebError("BadRequest", "Conflicting framing fields")
    transfer = headers.one('Transfer-Encoding')
    if transfer and transfer.lower().strip() != 'chunked':
        raise core.WebError("BadRequest", "Unsupported transfer coding")
    expect = headers.one('Expect')
    if expect and expect.lower().strip() != '100-continue':
        raise core.WebError("BadRequest", "Unsupported expectation")
    return headers


def _request_trailer_declarations(headers: core.Headers) -> frozenset[str]:
    """Validate Trailer announcements without confusing them with headers.

    RFC 9110 recommends announcing trailing fields, but does not require it.
    We accept safe unannounced fields while enforcing all forbidden names.
    """
    declarations: set[str] = set()
    if headers.values('Trailer') and not headers.values('Transfer-Encoding'):
        raise core.WebError('InvalidHeader', 'Trailer announcement requires chunked framing')
    for line in headers.values('Trailer'):
        for raw in line.split(','):
            name = raw.strip()
            if (not core._TOKEN.fullmatch(name) or
                    name.lower() in core._FORBIDDEN_TRAILER_FIELDS):
                raise core.WebError('InvalidHeader', 'Invalid Trailer announcement')
            declarations.add(name.lower())
            if len(declarations) > 32:
                raise core.WebError('HeadersTooLarge', 'Too many announced trailer fields')
    return frozenset(declarations)


def _headers_from_trailers(event: h11.EndOfMessage, initial: core.Headers,
                           limits: Limits) -> core.Headers:
    raw = event.headers.raw_items()
    # Never permit trailer blocks to evade the initial header quota.
    if (len(raw) > min(32, limits.max_header_fields) or
            sum(len(name) + len(value) + 4 for name, value in raw)
            > min(32768, limits.max_header_bytes)):
        raise core.WebError('HeadersTooLarge', 'Request trailers exceed configured limits')
    pairs = []
    for name, value in raw:
        if (value.startswith((b' ', b'\t')) or b'\r' in value or
                b'\n' in value or b'\0' in value):
            raise core.WebError('InvalidHeader', 'Invalid request trailer value')
        pairs.append((name.decode('ascii', 'strict'), value.decode('latin-1')))
    return core.validate_request_trailers(
        core.Headers(tuple(pairs)), initial=initial,
        max_fields=min(32, limits.max_header_fields),
        max_bytes=min(32768, limits.max_header_bytes))


def _validate_request(event: h11.Request, limits: Limits) -> tuple[str, str, core.Headers]:
    if event.http_version != b'1.1':
        raise core.WebError("BadRequest", "Only HTTP/1.1 is supported")
    if len(event.target) > limits.max_request_target_bytes:
        raise core.WebError("TargetTooLong", "Request target exceeds maximum length")
    try:
        method = event.method.decode('ascii', 'strict')
        target = event.target.decode('utf-8', 'strict')
    except UnicodeError as exc:
        raise core.WebError("BadRequest", "Invalid request line") from exc
    if not core._TOKEN.fullmatch(method):
        raise core.WebError("BadRequest", "Invalid HTTP method")
    core.validate_target(target)
    headers = _headers_from_request(event, limits)
    _request_trailer_declarations(headers)
    return method, target, headers


def _wire_response(conn: h11.Connection, response: core.Response, *, head: bool = False,
                   close: bool = False) -> bytes:
    """Encode one final semantic response without trusting transport-owned fields."""
    if isinstance(response.body, core.StreamBody):
        raise core.WebError('InvalidResponse', 'Streaming responses require incremental wire emission')
    status = response.status
    body = response.bytes
    if status in (204, 304) or 100 <= status < 200:
        body = b''
    headers = [(k.encode('ascii'), v.encode('latin-1')) for k, v in response.headers.pairs]
    if close:
        # Connection framing is strictly transport-owned. In particular this
        # header must never be forged using web.withHeader.
        headers.append((b'Connection', b'close'))
    if not any(k.lower() == b'date' for k, _ in headers):
        headers.append((b'Date', formatdate(usegmt=True).encode('ascii')))
    explicit_length = response.headers.one('Content-Length')
    if explicit_length is not None:
        if not explicit_length.isdecimal() or (not head and int(explicit_length) != len(body)):
            raise core.WebError('InvalidResponse', 'Inconsistent Content-Length')
    elif status not in (204, 304) and not 100 <= status < 200:
        headers.append((b'Content-Length', str(len(body)).encode('ascii')))
    wire = bytearray()
    if response.early_hints:
        wire.extend(conn.send(h11.InformationalResponse(
            status_code=103,
            headers=[(b'Link', link.encode('ascii', 'strict')) for link in response.early_hints])))
    wire.extend(conn.send(h11.Response(status_code=status, headers=headers)))
    if body and not head:
        wire.extend(conn.send(h11.Data(data=body)))
    wire.extend(conn.send(h11.EndOfMessage()))
    return bytes(wire)


def _send_stream_response(conn: h11.Connection, tcp: socket.socket,
                          response: core.Response, *, head: bool = False,
                          close: bool = False) -> None:
    """Write one stream with h11 flow framing and bounded memory usage."""
    source = response.body
    assert isinstance(source, core.StreamBody)
    try:
        core.validate_response(response, head=head)
        headers = [(k.encode('ascii'), v.encode('latin-1')) for k, v in response.headers.pairs]
        if close:
            headers.append((b'Connection', b'close'))
        if not any(k.lower() == b'date' for k, _ in headers):
            headers.append((b'Date', formatdate(usegmt=True).encode('ascii')))
        if response.trailers.pairs:
            # A Trailer announcement selects chunked framing even for a source
            # with a known length. All trailers are validated precommit.
            names = list(dict.fromkeys(name.lower() for name, _ in response.trailers.pairs))
            headers.append((b'Trailer', ', '.join(names).encode('ascii')))
        elif source.length is not None and response.headers.one('Content-Length') is None:
            headers.append((b'Content-Length', str(source.length).encode('ascii')))
        # Header metadata (including every after-middleware mutation) is
        # committed before the first source read. Unknown size uses h11's
        # HTTP/1.1 chunked encoder, never a buffer-all fallback.
        if response.early_hints:
            tcp.sendall(conn.send(h11.InformationalResponse(
                status_code=103, headers=[(b'Link', hint.encode('ascii'))
                                         for hint in response.early_hints])))
        tcp.sendall(conn.send(h11.Response(status_code=response.status, headers=headers)))
        total = 0
        if not head:
            while True:
                part = source.read(65536)
                if part is None:
                    break
                if not part:
                    continue
                total += len(part)
                if source.length is not None and total > source.length:
                    raise core.WebError('InvalidResponse', 'Stream exceeded advertised length')
                tcp.sendall(conn.send(h11.Data(data=part)))
            if source.length is not None and total != source.length:
                raise core.WebError('InvalidResponse', 'Stream length mismatch')
        tcp.sendall(conn.send(h11.EndOfMessage(
            headers=[(name.encode('ascii'), value.encode('latin-1'))
                     for name, value in response.trailers.pairs]
            if not head else [])))
    finally:
        source.close()


def _failure_response(exc: Exception) -> core.Response:
    if isinstance(exc, h11.RemoteProtocolError):
        status = exc.error_status_hint
        if status not in (400, 408, 413, 414, 431):
            status = 400
        return core.Response(status, 'Request Header Fields Too Large' if status == 431
                             else 'Bad Request')
    if isinstance(exc, core.WebError):
        # Protocol errors do not enter the app's onError/middleware pipeline.
        status = exc.status
        return core.Response(status, {
            400: 'Bad Request', 413: 'Content Too Large', 414: 'URI Too Long',
            415: 'Unsupported Media Type', 421: 'Misdirected Request',
            431: 'Request Header Fields Too Large',
            503: 'Service Unavailable',
        }.get(status, 'Bad Request' if status < 500 else 'Internal Server Error'))
    if isinstance(exc, (h11.LocalProtocolError, core.DispatchError)):
        # Malformed outgoing framing or a failed internal dispatch is not a
        # client-side syntax error. Never leak the private failure reason.
        return core.Response(500, 'Internal Server Error')
    return core.Response(400, 'Bad Request')


class _H11IncomingBody:
    """Incremental, protocol-framed incoming body source.

    A logical read never requests more than 64 KiB; decoded h11.Data events
    are split, not silently buffered into one large Valiance collection.
    """
    def __init__(self, handler: '_ConnectionHandler', conn: h11.Connection,
                 tcp: socket.socket, limits: Limits, headers: core.Headers):
        self.handler = handler
        self.conn = conn
        self.tcp = tcp
        self.limits = limits
        self.initial_headers = headers
        self.trailers = core.Headers()
        self.pending = b''
        self.complete = False
        self.closed = False

    def read(self, maximum: int) -> bytes | None:
        if self.closed:
            raise core.WebError('BodyUnavailable', 'Request body source is closed')
        while not self.pending:
            if self.complete:
                return None
            try:
                event = self.handler._next_event(self.conn, self.tcp,
                                                 self.limits.body_idle_timeout_seconds)
            except TimeoutError as exc:
                raise core.WebError('RequestTimeout', 'Request body deadline exceeded') from exc
            except h11.RemoteProtocolError as exc:
                raise core.WebError('BadRequest', 'Malformed streaming request framing') from exc
            if isinstance(event, h11.EndOfMessage):
                self.trailers = _headers_from_trailers(
                    event, self.initial_headers, self.limits)
                self.complete = True
                return None
            if not isinstance(event, h11.Data):
                raise core.WebError('BadRequest', 'Incomplete streamed request body')
            self.pending = bytes(event.data)
        part, self.pending = self.pending[:maximum], self.pending[maximum:]
        return part

    def close(self) -> None:
        self.closed = True


class _ConnectionHandler(socketserver.BaseRequestHandler):
    def handle(self) -> None:
        tcp = self.request
        limits = self.server.web_limits
        conn = h11.Connection(h11.SERVER, max_incomplete_event_size=limits.max_header_bytes)
        self._raw_headers = bytearray()
        self._header_complete = False
        tcp.settimeout(limits.connection_idle_timeout_seconds)
        try:
            while True:
                if self.server.draining.is_set():
                    return
                if conn.our_state is h11.MUST_CLOSE or conn.their_state is h11.MUST_CLOSE:
                    return
                try:
                    self._raw_headers = bytearray()
                    self._header_complete = False
                    request_event = self._next_event(conn, tcp, limits.header_timeout_seconds,
                                                     header_phase=True)
                except (h11.RemoteProtocolError, core.WebError, UnicodeError, ValueError) as exc:
                    self._protocol_error(conn, tcp, exc)
                    return
                if isinstance(request_event, h11.ConnectionClosed):
                    return
                if not isinstance(request_event, h11.Request):
                    return
                admitted = False
                finished = False
                incoming = None
                try:
                    method, target, headers = _validate_request(request_event, limits)
                    effective_scheme, effective_authority, client_address = (
                        self.server.web_network_policy.verify(
                            headers, headers.one('Host') or '',
                            'https' if self.server.web_tls else 'http',
                            self.client_address[0]))
                    from valiance.std import _web_ws
                    websocket_match = core.select_websocket_route(
                        self.server.web_app, target)
                    wants_websocket = _web_ws.upgrade_requested(headers)
                    if wants_websocket or (websocket_match is not None and method == 'GET' and
                                            not any(r.method == 'GET' and
                                                    core._match(r.template, core.validate_target(target)[0]) is not None
                                                    for r in self.server.web_app.routes)):
                        if websocket_match is None:
                            raise core.WebError('NotFound', 'No WebSocket route')
                        if not wants_websocket:
                            raise core.WebError('UpgradeRequired', 'WebSocket upgrade required')
                        if not self.server.begin_request(tcp):
                            raise core.WebError('ServiceUnavailable', 'Request admission limit reached')
                        admitted = True
                        route, captures = websocket_match
                        request = core.test_request(method, target, headers=headers,
                                                    authority=headers.one('Host') or '')
                        request = replace(request, protocol='HTTP/1.1',
                                          scheme=effective_scheme,
                                          authority=effective_authority,
                                          peer=self.client_address[0],
                                          client_address=client_address,
                                          route_params=captures)
                        accept, subprotocol, deflate = _web_ws.validate_handshake(
                            request, route.websocket_policy)
                        request, handshake = _web_ws.approve_upgrade(
                            self.server.web_app, route, request)
                        if handshake.status != 101:
                            tcp.sendall(_wire_response(conn, handshake, close=True))
                            finished = True
                            return
                        if not self.server.begin_websocket():
                            raise core.WebError('ServiceUnavailable', 'WebSocket session limit reached')
                        fields = [(b'Upgrade', b'websocket'),
                                  (b'Connection', b'Upgrade'),
                                  (b'Sec-WebSocket-Accept', accept.encode('ascii'))]
                        if subprotocol:
                            fields.append((b'Sec-WebSocket-Protocol', subprotocol.encode('ascii')))
                        if deflate:
                            fields.append((b'Sec-WebSocket-Extensions', _web_ws._DEFLATE_RESPONSE.encode('ascii')))
                        fields += [(k.encode('ascii'), v.encode('latin-1'))
                                   for k, v in handshake.headers.pairs]
                        try:
                            tcp.sendall(conn.send(h11.InformationalResponse(
                                status_code=101, headers=fields)))
                            # h11 retains bytes received past the handshake. They
                            # belong to the upgraded protocol, never the HTTP parser.
                            trailing, _ = conn.trailing_data
                            session = _web_ws.WebSocket(tcp, request, route.websocket_policy,
                                initial_bytes=trailing, subprotocol=subprotocol,
                                buffer_budget=self.server, permessage_deflate=deflate)
                            self.server.register_websocket_owner(session)
                            self.server.begin_websocket_callback()
                            try:
                                outcome = route.handler(session)
                                if isinstance(outcome, Exception):
                                    raise outcome
                                finished = True
                            except CancelledFault:
                                # Transport-owned cancellation (peer close,
                                # heartbeat or shutdown) is not an app fault.
                                session.abort(1001)
                            except Exception as exc:
                                # Untrusted application errors must not leak
                                # secrets through transport/access logs.
                                _LOG.error('WebSocket handler failed after upgrade (%s)',
                                           type(exc).__name__)
                                session.abort(1011)
                            finally:
                                self.server.end_websocket_callback()
                                session.abort(1000)
                                self.server.unregister_websocket_owner(session)
                        finally:
                            self.server.end_websocket()
                        return
                    if not self.server.begin_request(tcp):
                        raise core.WebError('ServiceUnavailable', 'Request admission limit reached')
                    admitted = True
                    body_mode, route_limit = core.select_body_policy(
                        self.server.web_app, method, target)
                    declared_length = headers.one('Content-Length')
                    global_limit = (limits.max_streaming_body_bytes if body_mode == 'Stream'
                                    else limits.max_buffered_body_bytes)
                    limit = min(global_limit, route_limit) if route_limit is not None else global_limit
                    if declared_length is not None and int(declared_length) > limit:
                        raise core.WebError('BodyTooLarge', 'Request body too large')
                    if headers.one('Expect') == '100-continue':
                        tcp.sendall(conn.send(h11.InformationalResponse(status_code=100,
                                                                       headers=[])))
                    incoming = None
                    if body_mode == 'Stream':
                        if method == 'QUERY' and headers.one('Content-Type') is None:
                            # Absent content type is valid only for an empty
                            # QUERY representation, evaluated before dispatch.
                            if declared_length not in (None, '0') or headers.one('Transfer-Encoding'):
                                raise core.WebError('UnsupportedMediaType',
                                                    'QUERY content requires Content-Type')
                        reader = _H11IncomingBody(self, conn, tcp, limits, headers)
                        incoming = core.RequestBodyStream(
                            reader, max_bytes=limit, initial_headers=headers)
                        request = core.test_request(method, target, headers=headers,
                                                    authority=headers.one('Host'))
                        request = replace(request, body_stream=incoming,
                                          multipart_limits=limits.multipart_policy)
                    else:
                        parts: list[bytes] = []
                        total = 0
                        while True:
                            event = self._next_event(conn, tcp, limits.body_idle_timeout_seconds)
                            if isinstance(event, h11.EndOfMessage):
                                # Even buffered routes must validate and reject
                                # malicious trailers before invoking a handler.
                                _headers_from_trailers(event, headers, limits)
                                break
                            if isinstance(event, h11.Data):
                                total += len(event.data)
                                if total > limit:
                                    raise core.WebError('BodyTooLarge', 'Request body too large')
                                parts.append(event.data)
                            else:
                                raise core.WebError('BadRequest', 'Incomplete request body')
                        if method == 'QUERY' and total and headers.one('Content-Type') is None:
                            raise core.WebError('UnsupportedMediaType', 'QUERY content requires Content-Type')
                        request = core.test_request(method, target, headers=headers,
                                                    body=b''.join(parts), authority=headers.one('Host'))
                        request = replace(request, multipart_limits=limits.multipart_policy)
                    request = replace(request, scheme=effective_scheme,
                                      authority=effective_authority,
                                      peer=self.client_address[0],
                                      client_address=client_address)
                    response = core.dispatch(self.server.web_app, request)
                    # Unconsumed request data cannot leak into a later request.
                    # Close instead of replaying or silently draining an
                    # unbounded upload after the handler has stopped reading.
                    close_after = (self.server.draining.is_set() or
                                   (incoming is not None and not incoming.complete))
                    if incoming is not None:
                        incoming.close()
                    tcp.settimeout(limits.write_idle_timeout_seconds)
                    if isinstance(response.body, core.StreamBody):
                        _send_stream_response(conn, tcp, response, head=method == 'HEAD',
                                              close=close_after)
                    else:
                        wire = _wire_response(conn, response, head=method == 'HEAD',
                                              close=close_after)
                        tcp.sendall(wire)
                    finished = True
                    if close_after or self.server.draining.is_set():
                        if not isinstance(tcp, ssl.SSLSocket):
                            tcp.shutdown(socket.SHUT_WR)
                        return
                    tcp.settimeout(limits.connection_idle_timeout_seconds)
                    if conn.our_state is h11.DONE and conn.their_state is h11.DONE:
                        conn.start_next_cycle()
                    else:
                        return
                except (h11.LocalProtocolError, h11.RemoteProtocolError, core.WebError,
                        core.DispatchError, UnicodeError, ValueError) as exc:
                    if conn.our_state in (h11.SEND_BODY, h11.DONE, h11.MUST_CLOSE):
                        _LOG.warning('HTTP/1.1 response stream failed after commitment: %s',
                                     type(exc).__name__)
                        return
                    finished = self._protocol_error(conn, tcp, exc)
                    return
                except Exception:
                    # An arbitrary application source can fail after headers
                    # commit; never emit a second HTTP response inside its body.
                    _LOG.exception('HTTP/1.1 stream/application failure')
                    if conn.our_state not in (h11.SEND_BODY, h11.DONE, h11.MUST_CLOSE):
                        finished = self._protocol_error(conn, tcp,
                            core.WebError('Internal', 'Internal Server Error'))
                    return
                finally:
                    if incoming is not None:
                        incoming.close()
                    if admitted:
                        self.server.end_request(tcp, finished)
        except (OSError, TimeoutError, h11.RemoteProtocolError):
            # Client timeout/disconnect: no request-level 500 is fabricated.
            return

    def _next_event(self, conn: h11.Connection, tcp: socket.socket, timeout: float,
                    *, header_phase: bool = False) -> object:
        deadline = time.monotonic() + timeout
        # h11 can retain pipelined bytes after a prior EndOfMessage. Inspect
        # those bytes *before* h11 normalises Content-Length in the next
        # Request event. Do not try to scan the previous request body: h11
        # has already consumed its complete framing before start_next_cycle.
        # The receive-buffer interface is private to h11 0.16; this adapter
        # remains a prototype until framing auditing can use a stable API.
        if header_phase and not self._raw_headers:
            buffered = bytes(conn._receive_buffer)
            if buffered:
                self._capture_headers(buffered)
        while True:
            event = conn.next_event()
            if event is not h11.NEED_DATA:
                return event
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError('HTTP read deadline expired')
            tcp.settimeout(remaining)
            data = tcp.recv(65536)
            if header_phase and not self._header_complete:
                self.server.observe_h1_header_input(len(data))
                self._capture_headers(data)
            conn.receive_data(data)
            if not data:
                # h11 reports a closed/truncated message as a protocol error.
                return conn.next_event()

    def _capture_headers(self, data: bytes) -> None:
        """Reject duplicate CL before h11 coalesces identical values.

        Each new message is checked before h11 returns its parsed Request,
        including raw pipelined bytes left in h11's receive buffer.
        """
        combined = self._raw_headers + data
        sep = combined.find(b'\r\n\r\n')
        self._raw_headers = combined if sep < 0 else combined[:sep + 4]
        if len(self._raw_headers) > self.server.web_limits.max_header_bytes:
            raise core.WebError('HeadersTooLarge', 'Request headers too large')
        if sep < 0:
            return
        self._header_complete = True
        block = bytes(self._raw_headers)
        names = []
        for line in block.split(b'\r\n')[1:]:
            if not line:
                break
            if line[:1] in (b' ', b'\t') or b':' not in line:
                raise core.WebError('InvalidHeader', 'Obsolete folded or invalid header')
            names.append(line.split(b':', 1)[0].lower())
        if names.count(b'content-length') > 1:
            raise core.WebError('BadRequest', 'Duplicate Content-Length')

    def _protocol_error(self, conn: h11.Connection, tcp: socket.socket, exc: Exception) -> bool:
        try:
            response = _failure_response(exc)
            # An invalid request can leave h11's receive state unusable.
            # Send only a fresh, closed error response, never reuse the socket.
            line = f'HTTP/1.1 {response.status} {HTTPStatus(response.status).phrase}\r\n'
            body = response.bytes
            fields = f'Content-Type: text/plain; charset=utf-8\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n'
            tcp.sendall(line.encode('ascii') + fields.encode('ascii') + body)
            if not isinstance(tcp, ssl.SSLSocket):
                tcp.shutdown(socket.SHUT_WR)
            return True
        except (OSError, UnicodeError, ValueError):
            return False


class _ThreadedTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    # Arbitrary Python/native callbacks cannot be forcibly terminated in a
    # CPython thread. Never let server_close() join one indefinitely after the
    # grace period; the shutdown diagnostics explicitly report this boundary.
    daemon_threads = True
    block_on_close = False

    def __init__(self, app: core.App, host: str, port: int, limits: Limits,
                 tls: TLSConfig | None = None,
                 protocols: tuple[str, ...] = ('HTTP/1.1',),
                 network_policy: core.NetworkPolicy | None = None):
        # Validate certificate and private key *before* binding a port.
        self.web_tls = _tls_context(tls, protocols) if tls is not None else None
        self.web_protocols = protocols
        self.web_app = core.prepare(app)
        self.web_network_policy = network_policy or core.NetworkPolicy()
        self.web_limits = limits
        self._slots = BoundedSemaphore(limits.max_connections)
        self._metric_lock = Lock()
        self.draining = Event()
        self._connections: dict[socket.socket, int] = {}
        self._http2_connections: set[socket.socket] = set()
        # ThreadingMixIn uses daemon request workers so a genuinely
        # uncooperative native callback cannot pin shutdown forever. Record
        # *all* transport workers separately so cooperative workers can be
        # explicitly joined within one aggregate bounded deadline.
        self._connection_workers: set[Thread] = set()
        # TLS negotiation happens on admitted transport workers, before any
        # H1/H2 request exists. Keep this phase separately observable so a
        # stalled TLS ClientHello cannot be mistaken for a WebSocket callback.
        self._tls_handshakes_active = 0
        self._tls_handshakes_peak = 0
        self._tls_handshakes_completed = 0
        self._tls_handshakes_failed = 0
        self._h1_header_octets = 0
        self._h1_header_read_eof = 0
        self._active_requests = 0
        self._active_websockets = 0
        # A WebSocket admission lease can outlive its callback on H2 while
        # outbound DATA is awaiting flush. Track running handlers separately
        # so a stop report never calls that transport state a stuck callback.
        self._active_websocket_callbacks = 0
        self._websocket_owners: set[object] = set()
        # A callback can return while one of its explicitly registered Python
        # children ignores the cancellation Event. Keep such owners reachable
        # for bounded reaping/diagnostics during listener shutdown.
        self._retired_websocket_owners: WeakSet = WeakSet()
        self._h2_buffered_bytes = 0
        # High-water marks cover *reserved server-owned bytes*, not all
        # Python, TLS, HPACK or application allocations.
        self._peak_h2_buffered_bytes = 0
        self._peak_active_websockets = 0
        # Directly observed transport-stage peaks. These are independent
        # high-water observations (not additive and NOT charged budgets):
        # socket/TLS read chunks, outbound TLS writes, hyper-h2's unparsed
        # input frame buffer, and the HPACK dynamic table's stored entries.
        self._h2_tls_read_chunk_peak = 0
        self._h2_tls_write_batch_peak = 0
        self._h2_unparsed_frame_peak = 0
        self._h2_hpack_dynamic_peak = 0
        self.accepted_requests = 0
        self.completed_requests = 0
        self.cancelled_requests = 0
        super().__init__((host, port), _ConnectionHandler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            request.close()
            return
        try:
            with self._metric_lock:
                if self.draining.is_set():
                    request.close()
                    self._slots.release()
                    return
                self._connections[request] = 0
            super().process_request(request, client_address)
        except BaseException:
            with self._metric_lock:
                self._connections.pop(request, None)
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        worker = current_thread()
        with self._metric_lock:
            self._connection_workers.add(worker)
        active = request
        try:
            # TLS handshakes MUST NOT execute in the listener accept thread:
            # a slow client could otherwise block admission for the whole
            # server and evade max_connections. Slots and the header timeout
            # apply from before the handshake until the worker is released.
            # Capture one TLS context per connection. Credential rotation swaps
            # the listener reference atomically; accepted sockets keep the
            # context and certificate chosen at their own handshake boundary.
            with self._metric_lock:
                tls_context = self.web_tls
            if tls_context is not None:
                request.settimeout(self.web_limits.header_timeout_seconds)
                with self._metric_lock:
                    # Attach the SSL socket to the drain registry *before*
                    # starting the potentially stalled network handshake.
                    # Holding the lock only for the no-IO wrap/swap prevents
                    # drain from missing a detached raw socket in between.
                    active = tls_context.wrap_socket(request, server_side=True,
                                                      do_handshake_on_connect=False)
                    self._connections.pop(request, None)
                    self._connections[active] = 0
                    draining = self.draining.is_set()
                if draining:
                    return
                with self._metric_lock:
                    self._tls_handshakes_active += 1
                    self._tls_handshakes_peak = max(
                        self._tls_handshakes_peak, self._tls_handshakes_active)
                completed_handshake = False
                try:
                    active.do_handshake()
                    completed_handshake = True
                finally:
                    with self._metric_lock:
                        self._tls_handshakes_active -= 1
                        if completed_handshake:
                            self._tls_handshakes_completed += 1
                        else:
                            self._tls_handshakes_failed += 1
                negotiated = active.selected_alpn_protocol()
                if negotiated == 'h2':
                    from valiance.std import _web_http2
                    with self._metric_lock:
                        self._http2_connections.add(active)
                    _web_http2.HTTP2Connection(active, self).run()
                    return
                if negotiated not in (None, 'http/1.1'):
                    raise ssl.SSLError('Unsupported negotiated ALPN protocol')
                if 'HTTP/1.1' not in self.web_protocols:
                    raise ssl.SSLError('HTTP/1.1 not configured')
            super().process_request_thread(active, client_address)
        except (OSError, ssl.SSLError, TimeoutError):
            try:
                active.close()
            except OSError:
                pass
        finally:
            if active is not request:
                try:
                    active.close()
                except OSError:
                    pass
            with self._metric_lock:
                self._connections.pop(active, None)
                self._http2_connections.discard(active)
                self._connections.pop(request, None)
            self._slots.release()
            with self._metric_lock:
                self._connection_workers.discard(worker)

    def reload_tls(self, config: TLSConfig) -> None:
        """Validate replacement credentials before publishing to new handshakes.

        No accepted SSL connection or in-progress handshake changes identity.
        A cleartext server cannot silently turn into a TLS server.
        """
        if not isinstance(config, TLSConfig):
            raise core.ConfigError('A TLS certificate/key configuration is required')
        context = _tls_context(config, self.web_protocols)
        with self._metric_lock:
            if self.draining.is_set():
                raise core.ConfigError('Cannot reload TLS on a draining listener')
            if self.web_tls is None:
                raise core.ConfigError('Cannot enable TLS on a cleartext listener')
            self.web_tls = context

    def join_connection_workers(self, timeout: float) -> int:
        """Join transport-owned threads once, never wait indefinitely on callbacks.

        There is one deadline for the listener, not a timeout per client.
        Callback threads that ignore cancellation may remain; their active
        WebSocket leases and stop timeout diagnostics are not fabricated away.
        """
        deadline = time.monotonic() + max(0., timeout)
        with self._metric_lock:
            workers = tuple(self._connection_workers)
        for worker in workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if worker is not current_thread():
                worker.join(timeout=remaining)
        with self._metric_lock:
            return sum(worker.is_alive() for worker in self._connection_workers)

    def begin_request(self, connection: socket.socket) -> bool:
        with self._metric_lock:
            if (self.draining.is_set() or connection not in self._connections or
                    self._active_requests >= self.web_limits.max_concurrent_requests):
                return False
            self._connections[connection] += 1
            self._active_requests += 1
            self.accepted_requests += 1
            return True

    def begin_websocket(self) -> bool:
        """Reserve one WebSocket session across H1 and all H2 connections.

        Admission must occur before the 101/200 handshake is committed, and
        the lease remains owned until the application callback actually exits.
        A stream reset does not prematurely free a slot held by running code.
        """
        with self._metric_lock:
            if (self.draining.is_set() or
                    self._active_websockets >= self.web_limits.max_active_websockets):
                return False
            self._active_websockets += 1
            self._peak_active_websockets = max(
                self._peak_active_websockets, self._active_websockets)
            return True

    def end_websocket(self) -> None:
        with self._metric_lock:
            assert self._active_websockets > 0
            self._active_websockets -= 1

    def begin_websocket_callback(self) -> None:
        with self._metric_lock:
            self._active_websocket_callbacks += 1

    def end_websocket_callback(self) -> None:
        with self._metric_lock:
            assert self._active_websocket_callbacks > 0
            self._active_websocket_callbacks -= 1

    def register_websocket_owner(self, owner: object) -> None:
        """Supervise an upgraded socket independently of its handler thread."""
        with self._metric_lock:
            self._websocket_owners.add(owner)
            draining = self.draining.is_set()
        if draining:
            owner.abort(1001)

    def unregister_websocket_owner(self, owner: object) -> None:
        with self._metric_lock:
            self._websocket_owners.discard(owner)
            if owner.owned_threads_alive:
                self._retired_websocket_owners.add(owner)

    def reserve_h2_buffer(self, size: int) -> bool:
        """Atomic admission across every HTTP/2 connection on this listener."""
        with self._metric_lock:
            if self._h2_buffered_bytes + size > self.web_limits.max_total_buffered_bytes:
                return False
            self._h2_buffered_bytes += size
            self._peak_h2_buffered_bytes = max(
                self._peak_h2_buffered_bytes, self._h2_buffered_bytes)
            return True

    def release_h2_buffer(self, size: int) -> None:
        with self._metric_lock:
            assert 0 <= size <= self._h2_buffered_bytes
            self._h2_buffered_bytes -= size

    def observe_h1_header_input(self, size: int) -> None:
        """Count pre-dispatch header octets independently of TLS handshakes.

        These counters are diagnostic, not quota reservations. They help
        distinguish an idle client after TLS negotiation from a slow parser.
        """
        with self._metric_lock:
            self._h1_header_octets += size
            if size == 0:
                self._h1_header_read_eof += 1

    def observe_h2_transport(self, *, read_chunk: int = 0, write_batch: int = 0,
                             parser_pending: int = 0, hpack_dynamic: int = 0) -> None:
        """Record *measured* H2/HPACK staging separately from quota leases.

        Peaks describe observed Python-visible storage in a single operation,
        not TLS internal heap usage nor a bound on the sum over connections.
        Do not treat these values as bytes reserved under maxTotalBufferedBytes.
        """
        with self._metric_lock:
            self._h2_tls_read_chunk_peak = max(self._h2_tls_read_chunk_peak, read_chunk)
            self._h2_tls_write_batch_peak = max(self._h2_tls_write_batch_peak, write_batch)
            self._h2_unparsed_frame_peak = max(self._h2_unparsed_frame_peak, parser_pending)
            self._h2_hpack_dynamic_peak = max(self._h2_hpack_dynamic_peak, hpack_dynamic)

    def resource_snapshot(self) -> dict[str, int]:
        """Atomic listener counter snapshot for reproducible qualification.

        ``reserved_bytes_peak`` is the high-water mark of explicit shared
        reservations, NOT process RSS or a claim that TLS/HPACK allocations
        are all budgeted. Keep measurement and enforcement distinct.
        """
        with self._metric_lock:
            return {
                'active_requests': self._active_requests,
                'active_websockets': self._active_websockets,
                'active_websocket_callbacks': self._active_websocket_callbacks,
                'active_websocket_owners': len(self._websocket_owners),
                'active_connections': len(self._connections),
                'active_h2_connections': len(self._http2_connections),
                'active_connection_workers': len(self._connection_workers),
                'tls_handshakes_active': self._tls_handshakes_active,
                'tls_handshakes_peak': self._tls_handshakes_peak,
                'tls_handshakes_completed': self._tls_handshakes_completed,
                'tls_handshakes_failed': self._tls_handshakes_failed,
                'h1_header_octets': self._h1_header_octets,
                'h1_header_read_eof': self._h1_header_read_eof,
                'reserved_bytes': self._h2_buffered_bytes,
                'reserved_bytes_peak': self._peak_h2_buffered_bytes,
                'websocket_sessions_peak': self._peak_active_websockets,
                'h2_tls_read_chunk_peak': self._h2_tls_read_chunk_peak,
                'h2_tls_write_batch_peak': self._h2_tls_write_batch_peak,
                'h2_unparsed_frame_peak': self._h2_unparsed_frame_peak,
                'h2_hpack_dynamic_peak': self._h2_hpack_dynamic_peak,
            }

    def end_request(self, connection: socket.socket, completed: bool) -> None:
        with self._metric_lock:
            if connection in self._connections:
                assert self._connections[connection] > 0
                self._connections[connection] -= 1
            # A daemon HTTP/2 callback may finish after its TLS socket was
            # unregistered by forced shutdown. The global admission lease
            # still must be released exactly once in that case.
            assert self._active_requests > 0
            self._active_requests -= 1
            if completed:
                self.completed_requests += 1
            else:
                self.cancelled_requests += 1

    def start_drain(self) -> None:
        with self._metric_lock:
            self.draining.set()
            ws_sockets = {owner._socket for owner in self._websocket_owners
                          if isinstance(owner._socket, socket.socket)}
            idle = [conn for conn, count in self._connections.items()
                    if count == 0 and conn not in self._http2_connections
                    and conn not in ws_sockets]
        self._interrupt(idle)

    @staticmethod
    def _interrupt(connections: list[socket.socket]) -> None:
        for conn in connections:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def drain_finished(self) -> bool:
        with self._metric_lock:
            return not any(self._connections.values()) and self._active_websockets == 0

    def cancel_connections(self) -> tuple[int, int, int, int]:
        """Force *transport* teardown; cooperatively join owned session tasks.

        A stuck user callback can remain runnable in a daemon request worker,
        but cannot retain an open server-owned socket. The callback's lease
        remains counted until its finally path; it is never reported as killed.
        Returns (signalled sessions, unjoined owned threads, live transport
        FDs, reserved bytes).
        """
        with self._metric_lock:
            connections = list(self._connections)
            active_owners = tuple(self._websocket_owners)
            owners = tuple(self._websocket_owners | set(self._retired_websocket_owners))
        for owner in owners:
            owner.abort(1001, fast=True)
        self._interrupt(connections)
        for conn in connections:
            try:
                conn.close()
            except OSError:
                pass
        # One aggregate limit, not one 200ms wait per session/client.
        deadline = time.monotonic() + .2
        for owner in owners:
            owner.join_owned(max(0, deadline - time.monotonic()))
        unjoined = sum(owner.owned_threads_alive for owner in owners)
        # H2 owns outstanding DATA/close-frame reservations until its TLS
        # transport worker unwinds. Interrupting the socket wakes that worker,
        # but the release happens on a different thread. Observe its cleanup
        # within the same global teardown deadline, not a per-peer timeout.
        while True:
            with self._metric_lock:
                reserved = self._h2_buffered_bytes
            if reserved == 0 or time.monotonic() >= deadline:
                break
            time.sleep(min(.005, max(0, deadline - time.monotonic())))
        return len(active_owners), unjoined, sum(conn.fileno() >= 0 for conn in connections), reserved


class ServerHandle:
    """Python-owned ALPN HTTP/1.1 + HTTP/2 listener."""
    def __init__(self, server: _ThreadedTCPServer):
        self._server = server
        self._stopped = Event()
        self._close_lock = Lock()
        self._closed = False
        self._stop_diagnostics: dict[str, int] | None = None
        self._thread = Thread(target=self._run, name='valiance-http1', daemon=False)
        self._thread.start()

    @property
    def tls_enabled(self) -> bool:
        return self._server.web_tls is not None

    @property
    def bound_address(self) -> tuple[str, int]:
        return self._server.server_address

    def _run(self) -> None:
        try:
            self._server.serve_forever(poll_interval=0.05)
        finally:
            self._stopped.set()

    def shutdown(self, grace_seconds: float | None = None) -> None:
        if grace_seconds is None:
            grace_seconds = self._server.web_limits.graceful_shutdown_seconds
        if type(grace_seconds) not in (int, float) or not 0 <= grace_seconds <= 86400:
            raise core.ConfigError('Invalid shutdown grace interval')
        with self._close_lock:
            if self._closed:
                return
            self._server.start_drain()
            try:
                # Stop admission first. Existing accepted requests may finish,
                # while idle keep-alive sockets have already been interrupted.
                self._server.shutdown()
                deadline = time.monotonic() + grace_seconds
                while not self._server.drain_finished() and time.monotonic() < deadline:
                    time.sleep(min(0.01, deadline - time.monotonic()))
                if not self._server.drain_finished():
                    with self._server._metric_lock:
                        timed_out = self._server._active_websocket_callbacks
                        # A WebSocket admission lease can outlive a finished
                        # callback on a flow-controlled HTTP/2 stream.
                    signalled, unjoined, open_fds, reserved = self._server.cancel_connections()
                    self._stop_diagnostics = {
                        # These are signals issued, NOT successful thread kills.
                        'cancelled': signalled, 'timed_out': timed_out,
                        'isolated': 0, 'unjoined_owned_tasks': unjoined,
                        'transport_sockets_open': open_fds,
                        'transport_buffered_bytes': reserved,
                    }
                else:
                    # Even if handlers have exited, explicitly owned Python
                    # children could have failed to observe cancellation.
                    with self._server._metric_lock:
                        retired = tuple(self._server._retired_websocket_owners)
                    deadline = time.monotonic() + .2
                    for owner in retired:
                        owner.join_owned(max(0, deadline - time.monotonic()))
                    self._stop_diagnostics = {
                        'cancelled': 0, 'timed_out': 0, 'isolated': 0,
                        'unjoined_owned_tasks': sum(o.owned_threads_alive for o in retired),
                        'transport_sockets_open': 0,
                        'transport_buffered_bytes': self._server._h2_buffered_bytes,
                    }
            finally:
                self._server.server_close()
                # Daemon request threads normally exit as soon as their
                # drained/aborted socket closes; join the cooperative ones
                # explicitly before process teardown. A blocked application
                # callback remains daemonized and is reported by timed_out.
                self._server.join_connection_workers(.2)
                self._thread.join()
                self._closed = True

    def reload_tls(self, config: TLSConfig) -> None:
        """Rotate TLS credentials without changing or duplicating this owner."""
        with self._close_lock:
            if self._closed or self._stopped.is_set():
                raise core.ConfigError('Server has been closed')
            self._server.reload_tls(config)

    def resource_snapshot(self) -> dict[str, int]:
        """Expose internal server counters for tests and profiling tools."""
        return self._server.resource_snapshot()

    @property
    def stop_diagnostics(self) -> dict[str, int] | None:
        """Report overdue native callbacks rather than claiming they stopped.

        `cancelled` counts active WebSocket session owners signalled, not kills;
        `timed_out` counts callback workers still executing at the deadline,
        separately from H2 admission leases retained for buffered DATA.
        `unjoined_owned_tasks` counts explicitly session-owned workers that did
        not cooperate within the bounded join interval. `isolated` remains 0;
        arbitrary Python/native callbacks are outside the kill contract.
        """
        return None if self._stop_diagnostics is None else dict(self._stop_diagnostics)

    @property
    def counts(self) -> tuple[int, int]:
        with self._server._metric_lock:
            return self._server.accepted_requests, self._server.completed_requests

    @property
    def cancelled(self) -> int:
        with self._server._metric_lock:
            return self._server.cancelled_requests

    def wait(self) -> None:
        self._thread.join()


def serve(app: core.App, *, host: str = '127.0.0.1', port: int = 8080,
          limits: Limits | None = None, tls: TLSConfig | None = None,
          protocols: tuple[str, ...] | None = None,
          network_policy: core.NetworkPolicy | None = None) -> ServerHandle:
    """Serve a core App via HTTP/1.1 or HTTPS ALPN HTTP/2 (no h2c)."""
    if not isinstance(port, int) or not 0 <= port <= 65535:
        raise core.ConfigError('Invalid TCP port')
    limits = limits or Limits()
    if protocols is None:
        protocols = ('HTTP/1.1', 'HTTP/2') if tls else ('HTTP/1.1',)
    if (not isinstance(protocols, tuple) or not protocols or
            len(set(protocols)) != len(protocols) or
            any(p not in ('HTTP/1.1', 'HTTP/2') for p in protocols)):
        raise core.ConfigError('Unsupported or duplicate HTTP protocols')
    if 'HTTP/2' in protocols and tls is None:
        raise core.ConfigError('HTTP/2 requires TLS with ALPN; h2c is forbidden')
    if 'HTTP/2' in protocols:
        try:
            from valiance.std import _web_http2  # noqa: F401 - fail before port bind
        except ImportError as exc:
            raise core.ConfigError('HTTP/2 protocol engine is unavailable') from exc
    if network_policy is not None and not isinstance(network_policy, core.NetworkPolicy):
        raise core.ConfigError('Expected NetworkPolicy')
    if not isinstance(app, core.App):
        raise core.ConfigError('An App is required')
    prepared = core.prepare(app)
    for route in prepared.routes:
        global_limit = (limits.max_streaming_body_bytes if route.body_mode == 'Stream'
                        else limits.max_buffered_body_bytes)
        if route.max_body_bytes is not None and route.max_body_bytes > global_limit:
            raise core.ConfigError('Route body limit exceeds the configured global limit')
    return ServerHandle(_ThreadedTCPServer(app, host, port, limits, tls=tls,
                                           protocols=protocols, network_policy=network_policy))
