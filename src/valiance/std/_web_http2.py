"""TLS/ALPN HTTP/2 adapter to the shared std.web logical dispatcher.

hyper-h2 owns HTTP/2 framing, HPACK and flow-control state. All H2Connection
mutations and socket I/O run in the connection worker; application dispatch
and streaming body production run in independent, bounded per-stream tasks.
This is an incremental transport implementation, not an RFC conformance claim.
"""
from __future__ import annotations

from collections import deque
from queue import Empty, Full, Queue
from dataclasses import dataclass, field, replace
from email.utils import formatdate
import logging
import select
import socket
import time
from threading import Condition, Event, Lock, Thread
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import h2.config
import h2.connection
import h2.errors
import h2.events
import h2.frame_buffer
import h2.exceptions
import h2.settings
import hpack.exceptions
from hyperframe.frame import GoAwayFrame

from valiance.std import _web_core as core
from valiance.std import _web_ws
from valiance.runtime.concurrency import CancelledFault

if TYPE_CHECKING:
    from valiance.std._web_http1 import Limits

_LOG = logging.getLogger(__name__)
MAX_STREAMS = 32
MAX_OUTPUT_CHUNK = 16384


class _DaemonTaskPool:
    """Bounded HTTP/2 callback workers that cannot pin listener shutdown.

    Python cannot interrupt a blocking user callback. Each worker is a daemon
    thread, and queued jobs are cancelled on shutdown. A running callback keeps
    its listener admission lease until it *actually* exits, even after the TCP
    connection is closed. This is bounded network shutdown, NOT forced task
    termination or isolation from arbitrary application code.
    """

    def __init__(self, max_workers: int, max_pending: int):
        """Initialize the daemon workers and their bounded work queue."""
        self._queue: Queue[tuple[object, object]] = Queue(maxsize=max_pending)
        self._workers: list[Thread] = []
        self._max_workers = max_workers
        self._closed = False

    def submit(self, fn, state):
        """Queue a callback if the pool can accept it."""
        if self._closed:
            raise RuntimeError('HTTP/2 task pool closed')
        self._queue.put_nowait((fn, state))
        # Spawn only the number of workers needed by the pending backlog.
        if len(self._workers) < self._max_workers and self._queue.qsize() > 0:
            t = Thread(target=self._work, name='valiance-h2-callback', daemon=True)
            self._workers.append(t)
            t.start()

    def _work(self):
        """Run submitted callbacks on a daemon worker."""
        while True:
            try:
                fn, state = self._queue.get(timeout=.05)
            except Empty:
                if self._closed:
                    return
                continue
            try:
                fn(state)
            except BaseException:
                _LOG.exception('Uncaught HTTP/2 worker failure')
            finally:
                self._queue.task_done()

    def shutdown(self) -> list['_State']:
        """Stop accepting work and shut down the worker pool."""
        self._closed = True
        abandoned = []
        while True:
            try:
                _, state = self._queue.get_nowait()
            except Empty:
                break
            abandoned.append(state)
            self._queue.task_done()
        # Drain idle workers with a *single* tiny total deadline, so short
        # lived connections do not leave polling daemon threads behind during
        # interpreter shutdown. Never wait for an uncooperative application
        # callback: an active worker remains a daemon and keeps its own lease.
        deadline = time.monotonic() + .06
        for worker in self._workers:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            worker.join(timeout=remaining)
        return abandoned


@dataclass
class _State:
    id: int
    method: str
    target: str
    headers: core.Headers
    authority: str
    mode: str
    limit: int
    request: core.Request | None = None
    received: int = 0
    buffered_bytes: int = 0
    response_reserved: int = 0
    stream_queue_reserved: int = 0
    stream_queue_flush_pending: int = 0
    chunks: deque[bytes] = field(default_factory=deque)
    incoming_end: bool = False
    incoming_closed: bool = False
    trailers: core.Headers = field(default_factory=core.Headers)
    done: Event = field(default_factory=Event)
    cancelled: Event = field(default_factory=Event)
    condition: Condition = field(default_factory=Condition)
    response: core.Response | None = None
    headers_sent: bool = False
    payload: bytes = b''
    sent: int = 0
    stream_queue: deque[bytes] = field(default_factory=deque)
    producer_done: bool = False
    producer_error: bool = False
    producer: object | None = None
    produced: int = 0
    complete: bool = False
    flush_pending: bool = False
    over_limit: bool = False
    admitted: bool = False
    accounted: bool = False
    worker_started: bool = False
    account_lock: Lock = field(default_factory=Lock)
    head_response_sent: Event = field(default_factory=Event)
    # All watchdog timestamps are monotonic and scoped to the logical stream,
    # not the connection (which may have other active streams).
    body_progress_at: float = field(default_factory=time.monotonic)
    response_progress_at: float = field(default_factory=time.monotonic)
    websocket: object | None = None
    websocket_owner: object | None = None
    ws_accepted: bool = False
    ws_headers: tuple[tuple[str, str], ...] = ()
    ws_opened: Event = field(default_factory=Event)
    ws_outbound_done: bool = False
    ws_lease_owned: bool = False


class _Incoming:
    def __init__(self, state: _State, ack: list[tuple[int, int]], ack_lock: Lock):
        """Adapt HTTP/2 request data to the incoming body reader interface."""
        self.state = state
        self.ack = ack
        self.ack_lock = ack_lock
        self.trailers = core.Headers()

    def read(self, maximum: int) -> bytes | None:
        """Read the next bounded chunk of request data."""
        s = self.state
        with s.condition:
            while not s.chunks and not s.incoming_end and not s.cancelled.is_set():
                s.condition.wait(timeout=0.25)
            if s.cancelled.is_set() or s.incoming_closed:
                raise core.WebError('BodyUnavailable', 'HTTP/2 request stream cancelled')
            if not s.chunks:
                self.trailers = s.trailers
                return None
            chunk = s.chunks.popleft()
            part, rest = chunk[:maximum], chunk[maximum:]
            if rest:
                s.chunks.appendleft(rest)
        if part:
            with self.ack_lock:
                self.ack.append((s.id, len(part)))
        return part

    def close(self) -> None:
        """Close the incoming request body reader."""
        s = self.state
        with s.condition:
            # Flow-controlled octets retained by a handler that returned early
            # must be credited back to the connection.  Otherwise a single
            # abandoned request can prevent unrelated streams from progressing.
            discarded = sum(map(len, s.chunks))
            s.incoming_closed = True
            s.chunks.clear()
            s.condition.notify_all()
        if discarded:
            with self.ack_lock:
                self.ack.append((s.id, discarded))



class _H2WebSocketTransport:
    """Socket-shaped, bounded duplex RFC 6455 octet stream on one H2 ID.

    The connection worker alone owns hyper-h2. Callback workers exchange
    immutable octets using per-stream conditions; reads return flow credit,
    and writes reserve both per-connection and listener-wide buffer capacity.
    """

    def __init__(self, owner: 'HTTP2Connection', state: _State):
        """Bind the WebSocket byte transport to one HTTP/2 stream."""
        self.owner = owner
        self.state = state
        self.timeout = 60.0

    def settimeout(self, seconds: float) -> None:
        """Set the receive timeout for the stream transport."""
        self.timeout = seconds

    def recv(self, maximum: int) -> bytes:
        """Read bytes queued for the upgraded stream."""
        s = self.state
        deadline = time.monotonic() + self.timeout
        with s.condition:
            while not s.chunks and not s.incoming_end and not s.cancelled.is_set() and not s.incoming_closed:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout('HTTP/2 WebSocket read timed out')
                s.condition.wait(timeout=min(.1, remaining))
            if s.cancelled.is_set() or s.incoming_closed or not s.chunks:
                return b''
            chunk = s.chunks.popleft()
            piece, remainder = chunk[:maximum], chunk[maximum:]
            if remainder:
                s.chunks.appendleft(remainder)
        if piece:
            self.owner._release_ws_inbound(s, len(piece))
            with self.owner.ack_lock:
                self.owner.ack.append((s.id, len(piece)))
        return piece

    def set_write_timeout(self, seconds: float) -> None:
        """Set the timeout for writing to the upgraded stream."""
        self._write_timeout = min(seconds, self.owner.limits.write_idle_timeout_seconds)

    def sendall(self, payload: bytes) -> None:
        """Queue bytes for transmission on the upgraded stream."""
        s = self.state
        limit = min(MAX_OUTPUT_CHUNK, self.owner.limits.max_total_buffered_bytes,
                    self.owner.limits.max_buffered_bytes_per_connection,
                    self.owner.limits.max_websocket_queued_send_bytes)
        if limit <= 0:
            raise OSError('WebSocket output buffer disabled')
        deadline = time.monotonic() + getattr(
            self, '_write_timeout', self.owner.limits.write_idle_timeout_seconds)
        for offset in range(0, len(payload), limit):
            part = payload[offset:offset + limit]
            while not s.cancelled.is_set() and not s.ws_outbound_done:
                with s.condition:
                    queue_full = len(s.stream_queue) >= 2
                if not queue_full and self.owner._reserve_producer_chunk(s, len(part)):
                    with s.condition:
                        if not s.cancelled.is_set() and not s.ws_outbound_done:
                            s.stream_queue.append(part)
                            s.condition.notify_all()
                            break
                    self.owner._release_producer_chunks(s, len(part))
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout('HTTP/2 WebSocket write timed out')
                with s.condition:
                    s.condition.wait(timeout=min(.025, remaining))
            else:
                raise OSError('HTTP/2 WebSocket stream reset')

    def send_close_nowait(self, payload: bytes) -> bool:
        """Queue a close control frame only when it cannot block cancellation."""
        s = self.state
        with s.condition:
            if (s.cancelled.is_set() or s.ws_outbound_done or
                    len(s.stream_queue) >= 2):
                return False
        if not self.owner._reserve_producer_chunk(s, len(payload)):
            return False
        with s.condition:
            if not s.cancelled.is_set() and not s.ws_outbound_done and len(s.stream_queue) < 2:
                s.stream_queue.append(payload)
                s.condition.notify_all()
                return True
        self.owner._release_producer_chunks(s, len(payload))
        return False

    def interrupt_write(self) -> None:
        """Wake a producer blocked behind H2 flow control on session abort."""
        s = self.state
        with s.condition:
            s.ws_outbound_done = True
            s.condition.notify_all()

    def interrupt_read(self) -> None:
        """Wake this WebSocket stream's reader without resetting H2 peers."""
        s = self.state
        with s.condition:
            s.incoming_closed = True
            s.condition.notify_all()

    def finish(self) -> None:
        """Mark outbound WebSocket production complete and wake the writer."""
        self.interrupt_write()


def _request_headers(fields: list[tuple[bytes, bytes]], limits: Limits) -> tuple[str, str, core.Headers, str]:
    """Validate request pseudo-headers and convert ordinary fields."""
    if len(fields) > limits.max_header_fields + 4 or sum(len(k) + len(v) + 4 for k, v in fields) > limits.max_header_bytes:
        raise core.WebError('HeadersTooLarge', 'HTTP/2 decoded header quota exceeded')
    pseudo: dict[bytes, bytes] = {}
    ordinary: list[tuple[str, str]] = []
    cookies: list[str] = []
    first_cookie_index: int | None = None
    seen_ordinary = False
    for key, value in fields:
        if (not key or key != key.lower() or
                value.startswith((b' ', b'\t')) or value.endswith((b' ', b'\t')) or
                any(o < 32 and o != 9 or o == 127 for o in value)):
            raise core.WebError('InvalidHeader', 'Invalid HTTP/2 field')
        if key.startswith(b':'):
            if seen_ordinary or key in pseudo or key not in (b':method', b':path', b':scheme', b':authority', b':protocol'):
                raise core.WebError('BadRequest', 'Illegal HTTP/2 pseudoheaders')
            pseudo[key] = value
            continue
        seen_ordinary = True
        try:
            name = key.decode('ascii', 'strict')
            if not core._TOKEN.fullmatch(name) or name.lower() in ('connection', 'proxy-connection', 'keep-alive', 'upgrade', 'transfer-encoding'):
                raise ValueError('Forbidden field')
            if name.lower() == 'te' and value.strip().lower() != b'trailers':
                raise ValueError('Invalid TE')
            decoded = value.decode('latin-1')
            if name.lower() == 'cookie':
                if first_cookie_index is None:
                    first_cookie_index = len(ordinary)
                    ordinary.append(('cookie', ''))
                cookies.append(decoded)
            else:
                ordinary.append((name, decoded))
        except (ValueError, UnicodeError) as exc:
            raise core.WebError('InvalidHeader', 'Forbidden HTTP/2 header') from exc
    # RFC 9113 §8.2.3: cookie crumbs are one logical field in applications.
    if first_cookie_index is not None:
        ordinary[first_cookie_index] = ('cookie', '; '.join(cookies))
    connect = pseudo.get(b':method') == b'CONNECT'
    extended = b':protocol' in pseudo
    expected = ({b':method', b':authority', b':path', b':scheme', b':protocol'}
                if connect and extended else
                {b':method', b':authority'} if connect else
                {b':method', b':path', b':scheme'})
    if (not expected.issubset(pseudo) or (connect and set(pseudo) != expected)
            or (extended and not connect)):
        raise core.WebError('BadRequest', 'HTTP/2 request pseudoheaders missing')
    try:
        method = pseudo[b':method'].decode('ascii', 'strict')
        path = '/' if connect and not extended else pseudo[b':path'].decode('utf-8', 'strict')
        scheme = '' if connect and not extended else pseudo[b':scheme'].decode('ascii', 'strict')
        authority = pseudo.get(b':authority', b'').decode('ascii', 'strict')
        if ((not connect or extended) and scheme != 'https') or not method or not core._TOKEN.fullmatch(method):
            raise ValueError('Invalid HTTP/2 method or scheme')
        if (not connect or extended) and len(pseudo[b':path']) > limits.max_request_target_bytes:
            raise core.WebError('TargetTooLong', 'HTTP/2 target too long')
        if (not connect or extended) and not (method == 'OPTIONS' and path == '*'):
            core.validate_target(path)
        if authority and any(c in authority for c in ' /\\@?#\x00\r\n\t'):
            raise ValueError('Invalid authority')
    except (ValueError, UnicodeError) as exc:
        raise core.WebError('BadRequest', 'Invalid HTTP/2 pseudoheader') from exc
    if extended:
        proto = pseudo[b':protocol']
        if not proto or any(b < 33 or b > 126 for b in proto):
            raise core.WebError('BadRequest', 'Invalid extended CONNECT protocol token')
    headers = core.Headers(tuple(ordinary))
    hosts = headers.values('host')
    if len(hosts) > 1:
        raise core.WebError('BadRequest', 'Duplicate HTTP/2 Host')
    if not authority and hosts:
        authority = hosts[0]
    if not authority:
        raise core.WebError('BadRequest', 'Missing HTTP/2 authority')
    if connect and not pseudo.get(b':authority'):
        raise core.WebError('BadRequest', 'CONNECT requires explicit authority')
    actual_authority = _authority_identity(authority)
    if hosts and _authority_identity(hosts[0]) != actual_authority:
        raise core.WebError('BadRequest', 'HTTP/2 host and authority conflict')
    content_lengths = headers.values('content-length')
    if len(content_lengths) > 1:
        raise core.WebError('BadRequest', 'Duplicate HTTP/2 content length')
    if content_lengths and (not content_lengths[0].isascii() or
                            not content_lengths[0].isdigit()):
        raise core.WebError('BadRequest', 'Invalid HTTP/2 Content-Length')
    return method, path, headers, authority


def _authority_identity(authority: str) -> tuple[str, int]:
    """Normalize Host/:authority for a TLS origin (RFC 9113 §8.3.1)."""
    try:
        url = urlsplit('https://' + authority)
        port = 443 if url.port is None else url.port
        if (not url.hostname or url.username is not None or url.password is not None or
                url.path or url.query or url.fragment or not (0 < port <= 65535)):
            raise ValueError('Invalid HTTP/2 authority')
        return url.hostname.lower(), port
    except ValueError as exc:
        raise core.WebError('BadRequest', 'Invalid HTTP/2 authority') from exc


def _request_trailers(fields: list[tuple[bytes, bytes]], initial: core.Headers,
                      limits: Limits) -> core.Headers:
    """Validate fields received in a trailing header block."""
    if (len(fields) > min(32, limits.max_header_fields) or
            sum(len(k) + len(v) + 4 for k, v in fields) > min(32768, limits.max_header_bytes)):
        raise core.WebError('HeadersTooLarge', 'HTTP/2 trailers exceed configured limits')
    parsed: list[tuple[str, str]] = []
    for name, value in fields:
        if (not name or name.startswith(b':') or name != name.lower() or
                value.startswith((b' ', b'\t')) or value.endswith((b' ', b'\t')) or
                any((o < 32 and o != 9) or o == 127 for o in value)):
            raise core.WebError('InvalidHeader', 'Invalid HTTP/2 trailer')
        try:
            decoded_name = name.decode('ascii', 'strict')
            if not core._TOKEN.fullmatch(decoded_name):
                raise core.WebError('InvalidHeader', 'Invalid HTTP/2 trailer field name')
            parsed.append((decoded_name, value.decode('latin-1')))
        except UnicodeError as exc:
            raise core.WebError('InvalidHeader', 'Invalid HTTP/2 trailer encoding') from exc
    return core.validate_request_trailers(core.Headers(tuple(parsed)), initial=initial,
                                          max_fields=min(32, limits.max_header_fields),
                                          max_bytes=min(32768, limits.max_header_bytes))


def _response_headers(response: core.Response, method: str) -> list[tuple[str, str]]:
    """Build the HTTP/2 response header block."""
    core.validate_response(response, head=method == 'HEAD')
    fields = [(':status', str(response.status))]
    for name, value in response.headers.pairs:
        if name.lower() in ('connection', 'transfer-encoding', 'upgrade', 'keep-alive', 'proxy-connection', 'trailer'):
            raise core.WebError('InvalidResponse', 'HTTP/2 response cannot send hop-by-hop headers')
        fields.append((name.lower(), value))
    if response.headers.one('date') is None:
        fields.append(('date', formatdate(usegmt=True)))
    if (not isinstance(response.body, core.StreamBody) and
            response.headers.one('content-length') is None and response.status not in (204, 304) and response.status >= 200):
        fields.append(('content-length', str(len(response.bytes))))
    elif (isinstance(response.body, core.StreamBody) and response.body.length is not None and
          response.headers.one('content-length') is None and not response.trailers.pairs):
        fields.append(('content-length', str(response.body.length)))
    return fields


class _HPACKDecodeFailure(h2.exceptions.ProtocolError):
    """RFC 9113 §4.3: broken HPACK is a connection COMPRESSION_ERROR."""
    error_code = h2.errors.ErrorCodes.COMPRESSION_ERROR


class _RFC9113FrameBuffer(h2.frame_buffer.FrameBuffer):
    """Correct the SETTINGS ACK nonempty-body error classification.

    hyper-h2 4.3's FrameBuffer translates hyperframe InvalidDataError to
    generic PROTOCOL_ERROR. RFC 9113 §6.5 specifies FRAME_SIZE_ERROR for
    an ACK with a payload. Inspect only this fixed-size frame header, leaving
    all other framing, preface and CONTINUATION handling with hyper-h2.
    """

    def add_data(self, data: bytes) -> None:
        """Append incoming wire data for frame decoding."""
        super().add_data(data)
        self.peak_pending_bytes = max(
            getattr(self, 'peak_pending_bytes', 0), len(self._data))

    def __next__(self):
        """Decode the next available frame with HTTP/2 validation."""
        if not self._preamble_len and len(self._data) >= 9:
            size = int.from_bytes(self._data[:3], 'big')
            # The underlying FrameBuffer checks this only after receiving
            # the complete body, permitting a peer to hold 16 MiB with an
            # oversized frame header alone. Reject before buffering content.
            if size > self.max_frame_size:
                raise h2.exceptions.FrameTooLargeError('HTTP/2 frame exceeds maximum')
            if (not self._headers_buffer and self._data[3] == 4 and
                    (self._data[4] & 1) and size):
                if len(self._data) < 9 + size:
                    raise StopIteration
                raise h2.exceptions.FrameDataMissingError('SETTINGS ACK has a body')
        return super().__next__()


class _StreamIsolatingH2Connection(h2.connection.H2Connection):
    """Isolate malformed message lengths despite hyper-h2's global error path.

    hyper-h2 promotes some malformed HTTP message conditions to a
    connection GOAWAY. RFC 9113 §8.1.1 requires a stream error for invalid
    Content-Length and trailing HEADERS without END_STREAM. Keep framing, HPACK
    and state management in hyper-h2; isolate only these explicit cases. The
    override uses private APIs and needs compatibility tests on upgrades.
    """

    def __init__(self, config):
        """Configure stream-local error handling on the HTTP/2 connection."""
        super().__init__(config)
        self.incoming_buffer = _RFC9113FrameBuffer(server=not config.client_side)

    def _receive_headers_frame(self, frame):
        """Process a HEADERS frame with stream-local error handling."""
        try:
            return super()._receive_headers_frame(frame)
        except h2.exceptions.ProtocolError as exc:
            if isinstance(exc.__cause__, (hpack.exceptions.HPACKError, IndexError,
                                          TypeError, UnicodeDecodeError)):
                raise _HPACKDecodeFailure(str(exc)) from exc
            # Decoding has already completed, so the HPACK dynamic table is
            # consistent. A malformed HTTP *message* must not close the
            # connection or discard valid streams using that table.
            message = str(exc)
            # hyper-h2 4.4 rejects non-digit lengths before int conversion,
            # so an invalid length may raise ProtocolError without a cause.
            malformed_length = message.startswith(('Invalid content-length header:',
                                                   'Conflicting content-length headers:'))
            malformed_trailers = message == 'Trailers must have END_STREAM set'
            if malformed_length or malformed_trailers:
                code = h2.errors.ErrorCodes.PROTOCOL_ERROR
                self.reset_stream(frame.stream_id, error_code=code)
                return [], [h2.events.StreamReset(stream_id=frame.stream_id,
                                                   error_code=code, remote_reset=False)]
            raise

    def _receive_data_frame(self, frame):
        """Process a DATA frame with stream-local error handling."""
        try:
            return super()._receive_data_frame(frame)
        except h2.exceptions.InvalidBodyLengthError:
            code = h2.errors.ErrorCodes.PROTOCOL_ERROR
            self.reset_stream(frame.stream_id, error_code=code)
            # No DATA event will reach the application, so release its
            # connection-level credit here. The stream window is now closed.
            self.increment_flow_control_window(frame.flow_controlled_length)
            return [], [h2.events.StreamReset(stream_id=frame.stream_id,
                                               error_code=code, remote_reset=False)]


class _H2HeaderDeadlineExceeded(h2.exceptions.ProtocolError):
    """An incomplete HTTP/2 preface/field section exceeded its time budget."""
    error_code = h2.errors.ErrorCodes.ENHANCE_YOUR_CALM


class _CompressedHeaderBudgetExceeded(h2.exceptions.ProtocolError):
    """Oversized HPACK field blocks are rejected before HPACK decompression."""
    error_code = h2.errors.ErrorCodes.ENHANCE_YOUR_CALM


class _CompressedHeaderBudget:
    """Incremental wire-frame scanner for the inbound compressed field budget.

    This scanner does not replace the hyper-h2 frame parser. It only measures
    HPACK octets in HEADERS/CONTINUATION, ignoring HEADERS padding and optional
    priority metadata. Retain at most the six prefix octets needed to inspect
    padding/priority: HPACK payloads are counted, never copied into another
    accumulating frame buffer. Reject once the *minimum* possible HPACK byte
    count exceeds the quota, including mid-frame and fragmented TCP reads.
    """

    PREFACE_LENGTH = 24

    def __init__(self, limit: int):
        """Initialize counters and deadlines for compressed header blocks."""
        self.limit = limit
        self.preface_remaining = self.PREFACE_LENGTH
        self.preface_started_at = time.monotonic()
        self.header_section_started_at: float | None = None
        self.frame_header = bytearray()
        self.frame_length = 0
        self.frame_remaining = 0
        self.frame_type = 0
        self.frame_flags = 0
        self.frame_prefix = bytearray()
        self.frame_body_length = 0
        self.frame_metadata_peak = 0
        self.block_bytes = 0
        self.block_open = False

    def feed(self, data: bytes) -> None:
        """Track header-block bytes as raw data arrives."""
        pos = 0
        while pos < len(data):
            if self.preface_remaining:
                take = min(len(data) - pos, self.preface_remaining)
                self.preface_remaining -= take
                pos += take
                continue
            if len(self.frame_header) != 9:
                take = min(9 - len(self.frame_header), len(data) - pos)
                self.frame_header.extend(data[pos:pos + take])
                pos += take
                if len(self.frame_header) != 9:
                    break
                h = self.frame_header
                self.frame_length = int.from_bytes(h[:3], 'big')
                self.frame_remaining = self.frame_length
                self.frame_type, self.frame_flags = h[3], h[4]
                self.frame_prefix.clear()
                self.frame_body_length = 0
                if self.frame_type == 1 and self.header_section_started_at is None:
                    self.header_section_started_at = time.monotonic()
                if not self.frame_remaining:
                    self._finish_frame()
                    continue
            take = min(self.frame_remaining, len(data) - pos)
            if self.frame_type in (1, 9):
                # For HEADERS, byte zero can encode a padding count and the
                # following five bytes can encode priority metadata. The
                # entire field block is *not* needed for quota enforcement.
                prefix_needed = 6 if self.frame_type == 1 else 0
                prefix_take = min(take, prefix_needed - len(self.frame_prefix))
                if prefix_take:
                    self.frame_prefix.extend(data[pos:pos + prefix_take])
                    self.frame_metadata_peak = max(
                        self.frame_metadata_peak, len(self.frame_prefix))
                self.frame_body_length += take
                self._check_hpack_budget()
            pos += take
            self.frame_remaining -= take
            if self.frame_remaining == 0:
                self._finish_frame()

    def _check_hpack_budget(self) -> None:
        """Reject as soon as even the padding-discounted count exceeds quota.

        For HEADERS, subtract the *maximum* known metadata/padding even when
        its trailing padding has not yet arrived; this never rejects a valid
        padded field block prematurely. hyper-h2 validates malformed padding.
        """
        if self.frame_type == 1:
            start = (1 if self.frame_flags & 0x08 else 0) + (
                5 if self.frame_flags & 0x20 else 0)
            pad = self.frame_prefix[0] if self.frame_flags & 0x08 and self.frame_prefix else 0
            count = max(0, self.frame_body_length - start - pad)
        else:
            count = self.frame_body_length
        if self.block_bytes + count > self.limit:
            raise _CompressedHeaderBudgetExceeded(
                'Compressed HTTP/2 field block exceeds quota')

    def _finish_frame(self) -> None:
        """Update header-block accounting at the end of a frame."""
        if self.frame_type == 1:  # HEADERS begins a field section.
            self.block_bytes = 0
            self.block_open = True
        if self.frame_type in (1, 9) and self.block_open:
            self._check_hpack_budget()
            if self.frame_type == 1:
                start = (1 if self.frame_flags & 0x08 else 0) + (
                    5 if self.frame_flags & 0x20 else 0)
                pad = self.frame_prefix[0] if self.frame_flags & 0x08 and self.frame_prefix else 0
                hp_count = max(0, self.frame_body_length - start - pad)
            else:
                hp_count = self.frame_body_length
            self.block_bytes += hp_count
            if self.frame_flags & 0x04:  # END_HEADERS
                self.block_open = False
                self.block_bytes = 0
                self.header_section_started_at = None
        self.frame_header.clear()
        self.frame_prefix.clear()
        self.frame_length = self.frame_remaining = self.frame_body_length = 0

    def header_deadline_expired(self, now: float, max_seconds: float) -> bool:
        # The connection preface and each HPACK field section must complete
        # within an absolute bounded interval. Sending a trickle of bytes
        # cannot extend the budget indefinitely.
        """Check whether an unfinished header block exceeded its deadline."""
        return (self.preface_remaining > 0 and
                now - self.preface_started_at >= max_seconds or
                self.header_section_started_at is not None and
                now - self.header_section_started_at >= max_seconds)


class HTTP2Connection:
    def __init__(self, tcp: socket.socket, server: object):
        """Initialize one connection's protocol state and stream accounting."""
        self.tcp = tcp
        self.server = server
        self.limits: Limits = server.web_limits
        self.conn = _StreamIsolatingH2Connection(h2.config.H2Configuration(
            client_side=False, header_encoding=None,
            normalize_inbound_headers=False, validate_inbound_headers=False))
        self.conn.decoder.max_header_list_size = min(
            self.limits.max_header_bytes, self.limits.max_decoded_header_bytes)
        # Restrict decoder allocation even before the peer acknowledges the
        # lower SETTINGS_HEADER_TABLE_SIZE value.
        self.conn.decoder.max_allowed_table_size = min(
            4096, self.limits.max_hpack_dynamic_table_bytes)
        self.compressed_headers = _CompressedHeaderBudget(
            self.limits.max_compressed_header_bytes)
        self.states: dict[int, _State] = {}
        # HTTP/2 acknowledges buffered DATA as it arrives, so stream-level
        # body caps alone cannot bound aggregate retained bytes. Share a single
        # admission budget across all buffered requests on this connection.
        # A busy connection cannot reserve the full limit on every stream.
        self.buffered_in_flight = 0
        self.outbound_buffered_in_flight = 0
        # StreamBody queues are bounded in chunks AND counted in the shared
        # byte budget. The producer is a separate thread, so protect the
        # per-connection accounting and the listener reservation together.
        self.stream_queued_in_flight = 0
        self._buffer_lock = Lock()
        # Preserve v24's tighter aggregate cap when the per-body limit is
        # reduced, and also honor the new explicit per-connection ceiling.
        self.max_buffered_in_flight = min(
            self.limits.max_buffered_body_bytes,
            self.limits.max_buffered_bytes_per_connection)
        self.active_requests = 0
        self.active_lock = Lock()
        self.ack: list[tuple[int, int]] = []
        self.ack_lock = Lock()
        self.draining = False
        self.last_progress = time.monotonic()
        self.last_admitted_stream = 0
        self.drain_started: float | None = None
        self.shutdown_goaway_sent = False
        self.failure_code = h2.errors.ErrorCodes.NO_ERROR

    def _release_ws_inbound(self, s: _State, size: int) -> None:
        """Release accounting for buffered WebSocket input on a stream."""
        with self._buffer_lock:
            consumed = min(size, s.buffered_bytes)
            s.buffered_bytes -= consumed
            self.buffered_in_flight -= consumed
            if consumed:
                self.server.release_h2_buffer(consumed)

    def _release_buffered(self, s: _State) -> None:
        """Release a completed/rejected buffered request's shared byte lease."""
        with self._buffer_lock:
            if s.buffered_bytes:
                self.buffered_in_flight -= s.buffered_bytes
                self.server.release_h2_buffer(s.buffered_bytes)
                s.buffered_bytes = 0

    def _release_response_buffer(self, s: _State) -> None:
        """Return the listener's outgoing-body byte lease at stream teardown."""
        with self._buffer_lock:
            if s.response_reserved:
                size = s.response_reserved
                s.response_reserved = 0
                self.outbound_buffered_in_flight -= size
                self.server.release_h2_buffer(size)
                s.payload = b''

    def _reserve_producer_chunk(self, s: _State, size: int) -> bool:
        """Reserve a producer queue chunk, including bytes awaiting TLS flush.

        The application may block until space is available. It never buffers
        an unbounded number of chunks or circumvents the listener-wide cap.
        """
        with self._buffer_lock:
            if s.cancelled.is_set() or (
                s.mode == 'WebSocket' and
                s.stream_queue_reserved + size > self.limits.max_websocket_queued_send_bytes
            ) or (
                self.buffered_in_flight + self.outbound_buffered_in_flight +
                self.stream_queued_in_flight + size >
                self.limits.max_buffered_bytes_per_connection
            ) or not self.server.reserve_h2_buffer(size):
                return False
            self.stream_queued_in_flight += size
            s.stream_queue_reserved += size
            return True

    def _release_producer_chunks(self, s: _State, size: int | None = None) -> None:
        """Return reserved StreamBody octets, either after wire flush or reset."""
        with self._buffer_lock:
            amount = s.stream_queue_reserved if size is None else min(size, s.stream_queue_reserved)
            if amount:
                s.stream_queue_reserved -= amount
                self.stream_queued_in_flight -= amount
                self.server.release_h2_buffer(amount)

    def _discard_producer_queue(self, s: _State) -> None:
        # The reader/writer thread owns this method. Keep DATA already handed
        # to hyper-h2 reserved until the next successful socket flush.
        """Discard queued response chunks and release their buffer reservations."""
        with s.condition:
            queued = sum(map(len, s.stream_queue))
            s.stream_queue.clear()
            s.condition.notify_all()
        self._release_producer_chunks(s, queued)

    def _admit_response_buffer(self, s: _State, data: bytes) -> bool:
        """Reserve a buffered response under both connection and server caps.

        StreamBody producers have their own bounded pull queue. For a buffered
        byte/string Response the transport retains the entire octet string
        until DATA frames drain; those bytes count against the same listener
        budget as incoming buffered HTTP/2 request bodies.
        """
        size = len(data)
        if not size:
            return True
        with self._buffer_lock:
            if (self.buffered_in_flight + self.outbound_buffered_in_flight +
                    self.stream_queued_in_flight + size >
                    self.limits.max_buffered_bytes_per_connection):
                return False
            if not self.server.reserve_h2_buffer(size):
                return False
            self.outbound_buffered_in_flight += size
            s.response_reserved = size
            return True

    def _discard_queued(self, s: _State) -> None:
        """Release queued upload octets and associated connection budget."""
        self._release_buffered(s)
        with s.condition:
            discarded = sum(map(len, s.chunks)) if s.mode in ('Stream', 'WebSocket') else 0
            s.chunks.clear()
            s.incoming_closed = True
            s.condition.notify_all()
        if discarded:
            with self.ack_lock:
                self.ack.append((s.id, discarded))

    def _fail_stream(self, s: _State, code: h2.errors.ErrorCodes) -> None:
        """Terminate a failed stream and clean up its state."""
        s.cancelled.set()
        if s.websocket_owner is not None:
            s.websocket_owner.abort(1001, fast=True)
        self._discard_queued(s)
        self._discard_producer_queue(s)
        try:
            self.conn.reset_stream(s.id, error_code=code)
        except h2.exceptions.StreamClosedError:
            pass
        s.complete = True
        s.flush_pending = True
        self._account(s, False)

    def _start_request(self, s: _State, pool: _DaemonTaskPool) -> None:
        """Admit a request stream and schedule its handler when ready."""
        if s.worker_started or s.cancelled.is_set():
            return
        s.worker_started = True
        try:
            pool.submit(self._dispatch, s)
        except Full:
            s.worker_started = False
            self._fail_stream(s, h2.errors.ErrorCodes.REFUSED_STREAM)

    def _dispatch(self, s: _State) -> None:
        """Dispatch a stream request or its WebSocket upgrade."""
        incoming = None
        try:
            if s.cancelled.is_set():
                return
            if s.mode == 'Stream':
                source = _Incoming(s, self.ack, self.ack_lock)
                incoming = core.RequestBodyStream(source, max_bytes=s.limit,
                                                   initial_headers=s.headers)
                request = replace(s.request, body_stream=incoming)
            else:
                with s.condition:
                    body = b''.join(s.chunks)
                    s.chunks.clear()
                request = replace(s.request, body=body)
                incoming = None
            response = core.dispatch(self.server.web_app, request,
                                     defer_head_stream_close=True)
            if s.over_limit or s.cancelled.is_set():
                if isinstance(response.body, core.StreamBody):
                    response.body.close()
                return
            # Check protocol-specific response fields before transferring an
            # exclusive body producer to the connection writer. If validation
            # fails, the dispatch worker (not the multiplexed frame loop)
            # owns the potentially blocking cleanup and publishes a safe 500.
            try:
                _response_headers(response, s.method)
            except (core.WebError, ValueError):
                if isinstance(response.body, core.StreamBody):
                    response.body.close()
                response = core.Response(500, 'Internal Server Error')
            s.response = response
            if isinstance(response.body, core.StreamBody):
                s.producer = response.body
        except (core.WebError, core.DispatchError) as exc:
            if not s.over_limit:
                s.response = _error_response(exc)
        except BaseException:
            _LOG.exception('HTTP/2 application dispatch failed')
            if not s.over_limit:
                s.response = core.Response(500, 'Internal Server Error')
        finally:
            # A handler that raises or returns before consuming its upload
            # still owns the source until the dispatch boundary releases it.
            # Without this close, a failed streamed request can strand the
            # connection receive window and block unrelated streams.
            if incoming is not None:
                incoming.close()
            s.done.set()
            # Stream-reset accounting must not free the server-wide request
            # slot while an application callback is still running.
            if s.cancelled.is_set():
                self._account(s, False)

    def _dispatch_websocket(self, s: _State) -> None:
        """Run middleware and the user callback outside the H2 frame worker."""
        stream = None
        try:
            from valiance.std._web_http1 import _failure_response
            route_match = core.select_websocket_route(self.server.web_app, s.target)
            if route_match is None:
                raise core.WebError('NotFound', 'WebSocket route not found')
            route, captures = route_match
            request = replace(s.request, route_params=captures)
            selected, deflate = _web_ws.validate_h2_handshake(request, route.websocket_policy)
            request, approval = _web_ws.approve_upgrade(self.server.web_app, route, request)
            if approval.status != 101:
                s.response = approval
                return
            # Success carries 200 + negotiated metadata, never 101/Upgrade or
            # Sec-WebSocket-Accept in the HTTP/2 header block.
            if any(name.lower() in ('content-length', 'transfer-encoding', 'te',
                                    'host', 'keep-alive', 'proxy-connection')
                   for name, _ in approval.headers.pairs):
                raise core.WebError('InvalidResponse',
                                    'Forbidden HTTP/2 WebSocket response field')
            if not self.server.begin_websocket():
                raise core.WebError('ServiceUnavailable', 'WebSocket session limit reached')
            # Keep this lease until the final H2 frame has been flushed and
            # the callback has exited; otherwise a fast callback can bypass
            # the live-session limit with queued, flow-controlled frames.
            s.ws_lease_owned = True
            s.ws_headers = tuple((name.lower(), value) for name, value in approval.headers.pairs)
            if selected:
                s.ws_headers += (('sec-websocket-protocol', selected),)
            if deflate:
                s.ws_headers += (('sec-websocket-extensions', _web_ws._DEFLATE_RESPONSE),)
            stream = _H2WebSocketTransport(self, s)
            s.websocket = stream
            s.ws_accepted = True
            while not s.ws_opened.wait(.05):
                if s.cancelled.is_set():
                    return
            if s.cancelled.is_set():
                return
            socket_owner = _web_ws.WebSocket(stream, request, route.websocket_policy,
                                              subprotocol=selected, buffer_budget=self.server,
                                              permessage_deflate=deflate)
            s.websocket_owner = socket_owner
            self.server.register_websocket_owner(socket_owner)
            self.server.begin_websocket_callback()
            try:
                result = route.handler(socket_owner)
                if isinstance(result, Exception):
                    raise result
            except CancelledFault:
                # An HTTP/2 stream reset is a session cancellation, not an
                # internal handler failure or a reason to reset its peers.
                socket_owner.abort(1001)
            except Exception as exc:
                # Peer RST_STREAM, listener shutdown, or a flow-controlled
                # writer interrupted by cancellation is not a handler fault.
                if s.cancelled.is_set() or socket_owner.cancelled:
                    socket_owner.abort(1001)
                else:
                    # Keep application exception text out of protocol logs.
                    _LOG.error('HTTP/2 WebSocket handler failed (%s)', type(exc).__name__)
                    socket_owner.abort(1011)
            finally:
                self.server.end_websocket_callback()
                socket_owner.abort(1000, fast=True)
                # H2's multiplexed transport never waits for application
                # children. Their handler worker owns a bounded cooperative
                # join even if an earlier RST_STREAM already fast-aborted it.
                socket_owner.join_owned(.2)
                self.server.unregister_websocket_owner(socket_owner)
                s.websocket_owner = None
        except (core.WebError, core.DispatchError) as exc:
            if not s.ws_accepted:
                s.response = _error_response(exc)
        except BaseException as exc:
            # Middleware failures may contain secrets. Log the exception class
            # only, and reject before committing the extended CONNECT.
            _LOG.error('HTTP/2 WebSocket handshake failed (%s)', type(exc).__name__)
            if not s.ws_accepted:
                s.response = core.Response(500, 'Internal Server Error')
        finally:
            if stream is not None:
                stream.finish()
            elif not s.ws_accepted:
                self._discard_queued(s)
            s.done.set()
            if s.cancelled.is_set():
                self._account(s, False)

    def _consume(self, event: object, pool: _DaemonTaskPool) -> None:
        """Consume HTTP/2 events and update connection and stream state."""
        if isinstance(event, h2.events.RequestReceived):
            sid = event.stream_id
            if (self.server.draining.is_set() or
                    self.active_requests >= self.limits.max_concurrent_streams_per_connection):
                self.conn.reset_stream(sid, error_code=h2.errors.ErrorCodes.REFUSED_STREAM)
                return
            try:
                method, path, headers, authority = _request_headers(event.headers, self.limits)
                try:
                    peer = self.tcp.getpeername()[0]
                except OSError:
                    peer = ''
                effective_scheme, effective_authority, client_address = (
                    self.server.web_network_policy.verify(headers, authority, 'https', peer))
                extended_protocol = next((value for key, value in event.headers
                                          if key == b':protocol'), None)
                if method == 'CONNECT' and extended_protocol == b'websocket':
                    if headers.values('content-length'):
                        raise core.WebError('BadRequest', 'Invalid WebSocket tunnel Content-Length')
                    request = core.test_request('CONNECT', path, headers=headers, authority=authority)
                    request = replace(request, protocol='HTTP/2',
                                      scheme=effective_scheme,
                                      authority=effective_authority,
                                      peer=peer, client_address=client_address,
                                      multipart_limits=self.limits.multipart_policy)
                    s = _State(sid, method, path, headers, authority, 'WebSocket', 0,
                               request=request)
                    self.states[sid] = s
                    if not self.server.begin_request(self.tcp):
                        self.states.pop(sid, None)
                        self.conn.reset_stream(sid, error_code=h2.errors.ErrorCodes.REFUSED_STREAM)
                        return
                    s.admitted = True
                    with self.active_lock:
                        self.active_requests += 1
                    self.last_admitted_stream = max(self.last_admitted_stream, sid)
                    s.worker_started = True
                    try:
                        pool.submit(self._dispatch_websocket, s)
                    except Full:
                        s.worker_started = False
                        self._fail_stream(s, h2.errors.ErrorCodes.REFUSED_STREAM)
                    return
                if method == 'CONNECT' or (method == 'OPTIONS' and path == '*'):
                    # Both forms are legitimate HTTP/2 requests, not malformed
                    # pseudoheader sets. The typed router exposes neither a
                    # CONNECT tunnel nor an asterisk-target route yet.
                    s = _State(sid, method, path, headers, authority, 'Buffer', 0)
                    s.response = core.Response(501, 'Not Implemented')
                    s.incoming_closed = True
                    s.done.set()
                    self.states[sid] = s
                    return
                mode, route_limit = core.select_body_policy(self.server.web_app, method, path)
                global_limit = (self.limits.max_streaming_body_bytes if mode == 'Stream'
                                else self.limits.max_buffered_body_bytes)
                cap = min(global_limit, route_limit) if route_limit is not None else global_limit
                declared = headers.one('content-length')
                if declared is not None and (not declared.isascii() or not declared.isdigit()):
                    raise core.WebError('BadRequest', 'Invalid HTTP/2 Content-Length')
                if declared is not None and int(declared) > cap:
                    raise core.WebError('BodyTooLarge', 'HTTP/2 body too large')
                if method == 'QUERY' and declared not in (None, '0') and headers.one('content-type') is None:
                    raise core.WebError('UnsupportedMediaType', 'QUERY requires Content-Type')
                request = core.test_request(method, path, headers=headers, authority=authority)
                request = replace(request, protocol='HTTP/2',
                                  scheme=effective_scheme, authority=effective_authority,
                                  peer=peer, client_address=client_address,
                                  multipart_limits=self.limits.multipart_policy)
                s = _State(sid, method, path, headers, authority, mode, cap, request=request)
                self.states[sid] = s
                if not self.server.begin_request(self.tcp):
                    # Not processed: retryable stream-local refusal, without
                    # consuming a listener request slot or dispatching the app.
                    self.states.pop(sid, None)
                    self.conn.reset_stream(sid, error_code=h2.errors.ErrorCodes.REFUSED_STREAM)
                    return
                s.admitted = True
                with self.active_lock:
                    self.active_requests += 1
                self.last_admitted_stream = max(self.last_admitted_stream, sid)
                if mode == 'Stream':
                    self._start_request(s, pool)
            except (core.WebError, core.DispatchError, ValueError) as exc:
                if (isinstance(exc, core.DispatchError) or
                        isinstance(exc, core.WebError) and
                        exc.name in ('InvalidHeader', 'BadRequest', 'MalformedTarget',
                                     'AmbiguousHeader')):
                    # RFC 9113 §8.1.1 requires malformed messages to be
                    # stream-level PROTOCOL_ERROR, not just HTTP 400.
                    self.conn.reset_stream(sid, error_code=h2.errors.ErrorCodes.PROTOCOL_ERROR)
                    return
                if sid not in self.states:
                    s = _State(sid, 'GET', '/', core.Headers(), '', 'Buffer', 0)
                    self.states[sid] = s
                s.response = _error_response(exc)
                s.incoming_closed = True
                s.done.set()
        elif isinstance(event, h2.events.DataReceived):
            s = self.states.get(event.stream_id)
            if s is None or s.cancelled.is_set():
                # Even a just-reset stream consumes *connection* flow-control
                # window. Its stream window is no longer usable.
                self.conn.increment_flow_control_window(event.flow_controlled_length)
                return
            length = len(event.data)
            if not s.admitted and s.done.is_set():
                self.conn.acknowledge_received_data(event.flow_controlled_length, s.id)
                return
            s.received += length
            if length:
                s.body_progress_at = time.monotonic()
            padding = event.flow_controlled_length - length
            if padding:
                self.conn.acknowledge_received_data(padding, s.id)
            if s.mode == 'WebSocket':
                if s.incoming_closed or s.done.is_set() and not s.ws_accepted:
                    self.conn.acknowledge_received_data(length, s.id)
                    return
                with self._buffer_lock:
                    over = (self.buffered_in_flight + self.outbound_buffered_in_flight +
                            self.stream_queued_in_flight + length >
                            self.limits.max_buffered_bytes_per_connection)
                    if not over:
                        over = not self.server.reserve_h2_buffer(length)
                    if not over:
                        s.buffered_bytes += length
                        self.buffered_in_flight += length
                if over:
                    self._fail_stream(s, h2.errors.ErrorCodes.ENHANCE_YOUR_CALM)
                    self.conn.increment_flow_control_window(length)
                    return
                with s.condition:
                    if length:
                        s.chunks.append(bytes(event.data))
                    s.condition.notify_all()
                return
            with self._buffer_lock:
                over_connection_budget = (self.buffered_in_flight +
                    self.outbound_buffered_in_flight + self.stream_queued_in_flight +
                    length > self.limits.max_buffered_bytes_per_connection)
                over_incoming_budget = (self.buffered_in_flight + length >
                    self.max_buffered_in_flight)
            if (s.received > s.limit or
                    (s.mode != 'Stream' and not s.incoming_closed and
                     (over_incoming_budget or over_connection_budget))) and not s.over_limit:
                s.over_limit = True
                self._discard_queued(s)
                if not s.headers_sent:
                    s.response = core.Response(413, 'Content Too Large')
                    s.done.set()
                else:
                    self.conn.reset_stream(s.id, error_code=h2.errors.ErrorCodes.CANCEL)
                    s.cancelled.set()
                    s.complete = True
                    s.flush_pending = True
                    self._account(s, False)
                self.conn.acknowledge_received_data(length, s.id)
                return
            if s.incoming_closed:
                self.conn.acknowledge_received_data(length, s.id)
            elif s.mode != 'Stream' and not self.server.reserve_h2_buffer(length):
                # Another HTTP/2 connection holds the aggregate budget.
                # Reject only this stream, not the connection or its peers.
                s.over_limit = True
                self._discard_queued(s)
                if not s.headers_sent:
                    s.response = core.Response(413, 'Content Too Large')
                    s.done.set()
                else:
                    self.conn.reset_stream(s.id, error_code=h2.errors.ErrorCodes.CANCEL)
                    s.cancelled.set()
                    s.complete = True
                    s.flush_pending = True
                    self._account(s, False)
                self.conn.acknowledge_received_data(length, s.id)
            else:
                with s.condition:
                    s.chunks.append(bytes(event.data))
                    s.condition.notify_all()
                if s.mode != 'Stream':
                    with self._buffer_lock:
                        s.buffered_bytes += length
                        self.buffered_in_flight += length
                    self.conn.acknowledge_received_data(length, s.id)
        elif isinstance(event, h2.events.TrailersReceived):
            s = self.states.get(event.stream_id)
            if s is not None and s.mode == 'WebSocket':
                self._fail_stream(s, h2.errors.ErrorCodes.PROTOCOL_ERROR)
                return
            if s is not None:
                try:
                    s.trailers = _request_trailers(event.headers, s.headers, self.limits)
                except (core.WebError, UnicodeError):
                    self._fail_stream(s, h2.errors.ErrorCodes.PROTOCOL_ERROR)
        elif isinstance(event, h2.events.StreamEnded):
            s = self.states.get(event.stream_id)
            if s is not None:
                if (s.method == 'QUERY' and s.received and s.headers.one('content-type') is None):
                    s.over_limit = True
                    s.incoming_closed = True
                    s.response = core.Response(415, 'Unsupported Media Type')
                    s.done.set()
                if s.mode != 'WebSocket' and s.headers.one('content-length') is not None and s.received != int(s.headers.one('content-length')):
                    self._fail_stream(s, h2.errors.ErrorCodes.PROTOCOL_ERROR)
                s.incoming_end = True
                s.response_progress_at = time.monotonic()
                with s.condition:
                    s.condition.notify_all()
                if s.mode not in ('Stream', 'WebSocket') and not s.done.is_set():
                    self._start_request(s, pool)
        elif isinstance(event, h2.events.StreamReset):
            s = self.states.get(event.stream_id)
            if s is not None:
                s.cancelled.set()
                if s.websocket_owner is not None:
                    s.websocket_owner.abort(1001, fast=True)
                self._discard_queued(s)
                self._discard_producer_queue(s)
                if isinstance(s.producer, core.StreamBody):
                    # Don't block the connection reader on an arbitrary app source.
                    # The producer task owns final source cleanup.
                    pass
                s.complete = True
                s.flush_pending = True
                self._account(s, False)
        elif isinstance(event, h2.events.ConnectionTerminated):
            self.draining = True
            for s in self.states.values():
                s.cancelled.set()
                if s.websocket_owner is not None:
                    s.websocket_owner.abort(1001, fast=True)
                with s.condition:
                    s.condition.notify_all()

    def _account(self, s: _State, completed: bool) -> None:
        # The protocol stream can close before its handler/response producer
        # exits. Never reuse a request-task lease while user code is alive.
        """Finalize the request's accounting once."""
        if s.worker_started and not s.done.is_set():
            return
        # The transport has not yet flushed the final DATA / END_STREAM /
        # RST_STREAM frames, or still holds a buffered outbound byte lease.
        if s.flush_pending or s.response_reserved:
            return
        if s.headers_sent and s.producer is not None and not s.producer_done:
            return
        with s.account_lock:
            if s.admitted and not s.accounted:
                s.accounted = True
                with self.active_lock:
                    self.active_requests -= 1
                self.server.end_request(self.tcp, completed)
                if s.ws_lease_owned:
                    s.ws_lease_owned = False
                    self.server.end_websocket()

    def _produce(self, s: _State) -> None:
        """Produce response body chunks for the connection writer."""
        source = s.producer
        assert isinstance(source, core.StreamBody)
        try:
            while not s.cancelled.is_set():
                with s.condition:
                    while len(s.stream_queue) >= 2 and not s.cancelled.is_set():
                        s.condition.wait(timeout=.1)
                if s.cancelled.is_set():
                    break
                read_cap = min(MAX_OUTPUT_CHUNK, self.limits.max_total_buffered_bytes,
                               self.limits.max_buffered_bytes_per_connection)
                value = source.read(read_cap)
                if value is None:
                    break
                if not value:
                    continue
                s.produced += len(value)
                if source.length is not None and s.produced > source.length:
                    raise core.WebError('InvalidResponse', 'HTTP/2 stream length exceeded')
                # One bounded pending local read is possible per worker. Its
                # bytes are reserved before entering the shared queue; a peer
                # holding its flow window closed can no longer evade limits.
                while not s.cancelled.is_set():
                    if self._reserve_producer_chunk(s, len(value)):
                        with s.condition:
                            if not s.cancelled.is_set():
                                s.stream_queue.append(value)
                                s.condition.notify_all()
                                break
                        self._release_producer_chunks(s, len(value))
                        break
                    with s.condition:
                        s.condition.wait(timeout=.025)
            if not s.cancelled.is_set() and source.length is not None and s.produced != source.length:
                raise core.WebError('InvalidResponse', 'HTTP/2 stream length mismatch')
        except BaseException:
            s.producer_error = True
        finally:
            try:
                source.close()
            finally:
                s.producer_done = True
                if s.cancelled.is_set():
                    self._account(s, False)

    def _close_head_producer(self, s: _State) -> None:
        """Close the unused HEAD source without blocking multiplexed I/O.

        HEAD has no response content. A user source can nonetheless have an
        arbitrarily slow close(), so its exclusive ownership and the listener
        admission lease stay with a bounded worker until cleanup finishes.
        """
        try:
            assert isinstance(s.producer, core.StreamBody)
            s.producer.close()
        except BaseException:
            _LOG.warning('HTTP/2 HEAD response source close failed', exc_info=True)
        finally:
            s.producer_done = True
            # Completion is published only after the transport has queued
            # END_STREAM; otherwise a very fast close could free admission
            # before the response is committed.
            while not s.head_response_sent.wait(.05):
                if s.cancelled.is_set():
                    break
            self._account(s, s.complete and not s.cancelled.is_set())

    def _enforce_stream_deadlines(self) -> None:
        """Cancel stalled streams without sacrificing independent H2 streams.

        Unfinished uploads use the body-idle timeout; response dispatch,
        producer reads and flow-control-blocked writes use the write-idle
        timeout. The wire stream is reset with CANCEL because a partial body
        or partially committed response cannot always carry a safe HTTP 408.
        """
        now = time.monotonic()
        for s in tuple(self.states.values()):
            if s.complete or s.cancelled.is_set() or not s.admitted:
                continue
            if s.mode == 'WebSocket':
                if (s.ws_accepted and s.stream_queue and
                        now - s.response_progress_at >= self.limits.write_idle_timeout_seconds):
                    self._fail_stream(s, h2.errors.ErrorCodes.CANCEL)
                continue
            body_stalled = (not s.incoming_end and not s.incoming_closed and
                            now - s.body_progress_at >= self.limits.body_idle_timeout_seconds)
            response_stalled = (s.incoming_end and
                                now - s.response_progress_at >= self.limits.write_idle_timeout_seconds)
            if body_stalled or response_stalled:
                self._fail_stream(s, h2.errors.ErrorCodes.CANCEL)

    def _finish_response(self, s: _State) -> None:
        """Reclaim an upload abandoned after a complete final response.

        RFC 9113 §8.1 allows RST_STREAM(NO_ERROR) *after* END_STREAM to
        indicate that the client need not finish sending an unused request
        body. Without this, an early response leaves the remote half open
        indefinitely and completed stream state accumulates on keep-alive
        connections. The completed response remains valid despite the reset.
        """
        s.complete = True
        s.flush_pending = True
        if not s.incoming_end:
            try:
                self.conn.reset_stream(
                    s.id, error_code=h2.errors.ErrorCodes.NO_ERROR)
            except h2.exceptions.StreamClosedError:
                # The peer might have finished concurrently with the reply.
                pass
            s.incoming_closed = True
        self._account(s, True)

    def _writes(self, pool: _DaemonTaskPool) -> None:
        """Flush queued response data within flow-control and buffer limits."""
        with self.ack_lock:
            pending = self.ack[:]
            self.ack.clear()
        for sid, size in pending:
            try:
                self.conn.acknowledge_received_data(size, sid)
            except h2.exceptions.StreamClosedError:
                # A consumed byte always contributes to the connection window.
                self.conn.increment_flow_control_window(size)
        for s in list(self.states.values()):
            if s.cancelled.is_set() and s.complete:
                self._account(s, False)
            # Retain a buffered body's budget through application dispatch,
            # not merely until its bytes have been joined into a request.
            if s.mode not in ('Stream', 'WebSocket') and s.done.is_set():
                self._release_buffered(s)
            if s.complete or s.cancelled.is_set():
                continue
            if s.mode == 'WebSocket' and s.ws_accepted:
                try:
                    if not s.headers_sent:
                        self.conn.send_headers(s.id, [(':status', '200'), *s.ws_headers],
                                               end_stream=False)
                        s.headers_sent = True
                        s.response_progress_at = time.monotonic()
                        s.ws_opened.set()
                    window = self.conn.local_flow_control_window(s.id)
                    maximum = min(window, self.conn.max_outbound_frame_size, MAX_OUTPUT_CHUNK)
                    with s.condition:
                        if s.stream_queue and maximum > 0:
                            chunk = s.stream_queue[0]
                            part = chunk[:maximum]
                            if len(part) == len(chunk):
                                s.stream_queue.popleft()
                            else:
                                s.stream_queue[0] = chunk[len(part):]
                            s.condition.notify_all()
                        else:
                            part = b''
                        finished = s.ws_outbound_done and not s.stream_queue
                    if part:
                        self.conn.send_data(s.id, part)
                        s.stream_queue_flush_pending += len(part)
                        s.response_progress_at = time.monotonic()
                    if finished:
                        self.conn.end_stream(s.id)
                        # The remote half may still be sending messages; a
                        # completed server socket must not retain upload DATA.
                        s.incoming_closed = True
                        self._discard_queued(s)
                        s.complete = True
                        s.flush_pending = True
                except h2.exceptions.StreamClosedError:
                    self._fail_stream(s, h2.errors.ErrorCodes.CANCEL)
                continue
            if s.done.is_set() and not s.headers_sent:
                resp = s.response
                if resp is None:
                    continue
                try:
                    fields = _response_headers(resp, s.method)
                except (core.WebError, ValueError):
                    resp = core.Response(500, 'Internal Server Error')
                    s.response = resp
                    fields = _response_headers(resp, s.method)
                buffered_payload = (b'' if s.method == 'HEAD' or
                                    resp.status in (204, 304) or
                                    isinstance(resp.body, core.StreamBody)
                                    else resp.bytes)
                if buffered_payload and not self._admit_response_buffer(s, buffered_payload):
                    # Sending a response that exceeds the configured output
                    # memory budget is preferable to retaining unlimited
                    # octets or stalling all multiplexed streams. Reject
                    # *before* committing the original response headers.
                    resp = core.Response(503)
                    s.response = resp
                    fields = _response_headers(resp, s.method)
                    buffered_payload = b''
                # StreamBody.close() is application code and can block. In
                # particular a HEAD response must never run it in the HTTP/2
                # reader/writer thread, nor wait for it before END_STREAM.
                head_source = s.method == 'HEAD' and isinstance(resp.body, core.StreamBody)
                if head_source:
                    try:
                        pool.submit(self._close_head_producer, s)
                    except Full:
                        self._fail_stream(s, h2.errors.ErrorCodes.REFUSED_STREAM)
                        Thread(target=self._close_abandoned_producer, args=(s,),
                               name='valiance-h2-source-close', daemon=True).start()
                        continue
                for hint in resp.early_hints:
                    self.conn.send_headers(s.id, [(':status', '103'), ('link', hint)])
                s.headers_sent = True
                s.response_progress_at = time.monotonic()
                s.payload = buffered_payload
                end = (head_source or
                       (not s.payload and not isinstance(resp.body, core.StreamBody)
                        and not resp.trailers.pairs))
                self.conn.send_headers(s.id, fields, end_stream=end)
                if end:
                    self._finish_response(s)
                    s.head_response_sent.set()
                    continue
                if isinstance(resp.body, core.StreamBody):
                    try:
                        pool.submit(self._produce, s)
                    except Full:
                        self._fail_stream(s, h2.errors.ErrorCodes.REFUSED_STREAM)
                        Thread(target=self._close_abandoned_producer, args=(s,),
                               name='valiance-h2-source-close', daemon=True).start()
                        continue
            if not s.headers_sent or s.complete:
                continue
            try:
                window = self.conn.local_flow_control_window(s.id)
                maximum = min(window, self.conn.max_outbound_frame_size, MAX_OUTPUT_CHUNK)
                resp = s.response
                if s.producer is not None:
                    with s.condition:
                        if s.stream_queue and maximum > 0:
                            chunk = s.stream_queue[0]
                            part = chunk[:maximum]
                            if len(part) == len(chunk):
                                s.stream_queue.popleft()
                            else:
                                s.stream_queue[0] = chunk[len(part):]
                            s.condition.notify_all()
                        else:
                            part = b''
                    if part:
                        self.conn.send_data(s.id, part)
                        s.stream_queue_flush_pending += len(part)
                        s.response_progress_at = time.monotonic()
                    finished = s.producer_done and not s.stream_queue
                    if s.producer_error:
                        self._discard_producer_queue(s)
                        self.conn.reset_stream(s.id, error_code=h2.errors.ErrorCodes.INTERNAL_ERROR)
                        s.complete = True
                        s.flush_pending = True
                        self._account(s, False)
                        continue
                else:
                    part = s.payload[s.sent:s.sent + maximum] if maximum > 0 else b''
                    if part:
                        self.conn.send_data(s.id, part)
                        s.sent += len(part)
                        s.response_progress_at = time.monotonic()
                    finished = s.sent == len(s.payload)
                if finished:
                    trailers = [(k.lower(), v) for k, v in resp.trailers.pairs]
                    if trailers:
                        self.conn.send_headers(s.id, trailers, end_stream=True)
                    else:
                        self.conn.end_stream(s.id)
                    self._finish_response(s)
            except h2.exceptions.StreamClosedError:
                s.cancelled.set()
                self._discard_producer_queue(s)
                s.complete = True
                s.flush_pending = True

        # Keep-alive connections can serve arbitrarily many sequential streams.
        # Once both halves close, remove the application state (including large
        # buffered request/response bodies) instead of retaining it forever.
        for sid, s in tuple(self.states.items()):
            if (s.complete and not s.flush_pending and not s.response_reserved and
                    (not s.admitted or s.accounted) and
                    (s.incoming_end or s.incoming_closed or s.cancelled.is_set())):
                del self.states[sid]

    def _initiate_drain(self) -> None:
        """Begin draining the connection for graceful shutdown."""
        if self.drain_started is not None:
            return
        self.drain_started = time.monotonic()
        # hyper-h2.close_connection() transitions its state machine to CLOSED,
        # where it can no longer process replies on outstanding streams. Send
        # RFC 9113's initial GOAWAY independently, then let hyper-h2 send the
        # final accurate GOAWAY when admitted streams have completed.
        frame = GoAwayFrame(0)
        frame.last_stream_id = 0x7fffffff
        frame.error_code = h2.errors.ErrorCodes.NO_ERROR
        self.tcp.sendall(self.conn.data_to_send() + frame.serialize())
        self.shutdown_goaway_sent = True

    def _close_abandoned_producer(self, s: _State) -> None:
        """Release a response producer whose stream can no longer send data."""
        try:
            if isinstance(s.producer, core.StreamBody):
                s.producer.close()
        except BaseException:
            _LOG.debug('HTTP/2 abandoned producer close failed', exc_info=True)
        finally:
            s.producer_done = True
            self._account(s, False)

    def run(self) -> None:
        """Drive the HTTP/2 connection until it closes or finishes draining."""
        self.tcp.settimeout(self.limits.write_idle_timeout_seconds)
        self.conn.initiate_connection()
        self.conn.update_settings({
            h2.settings.SettingCodes.MAX_CONCURRENT_STREAMS: self.limits.max_concurrent_streams_per_connection,
            h2.settings.SettingCodes.ENABLE_CONNECT_PROTOCOL: 1,
            h2.settings.SettingCodes.MAX_HEADER_LIST_SIZE: min(
                self.limits.max_header_bytes, self.limits.max_decoded_header_bytes),
            h2.settings.SettingCodes.HEADER_TABLE_SIZE: min(
                4096, self.limits.max_hpack_dynamic_table_bytes),
        })
        pool = _DaemonTaskPool(
            max_workers=min(MAX_STREAMS, self.limits.max_concurrent_streams_per_connection),
            max_pending=2 * self.limits.max_concurrent_streams_per_connection)
        try:
            while not self.draining:
                if self.server.draining.is_set():
                    self._initiate_drain()
                self._enforce_stream_deadlines()
                if self.compressed_headers.header_deadline_expired(
                        time.monotonic(), self.limits.header_timeout_seconds):
                    raise _H2HeaderDeadlineExceeded('HTTP/2 preface or header section timed out')
                self._writes(pool)
                outbound = self.conn.data_to_send()
                if outbound:
                    self.server.observe_h2_transport(write_batch=len(outbound))
                    self.tcp.settimeout(self.limits.write_idle_timeout_seconds)
                    self.tcp.sendall(outbound)
                    self.last_progress = time.monotonic()
                # DATA serialized from StreamBody queues remains charged until
                # sendall finishes. A cancelled stream may retain only the
                # in-flight frame bytes, which are also freed at this point.
                for stream in tuple(self.states.values()):
                    if stream.stream_queue_flush_pending:
                        self._release_producer_chunks(stream, stream.stream_queue_flush_pending)
                        stream.stream_queue_flush_pending = 0
                # All queued frames have now traversed the TLS write. Release
                # the response memory reservation and request admission only
                # after successful wire delivery; cleanup can prune next tick.
                for stream in tuple(self.states.values()):
                    if stream.complete and (stream.flush_pending or stream.response_reserved):
                        self._release_response_buffer(stream)
                        stream.flush_pending = False
                        self._account(stream, not stream.cancelled.is_set())
                if (self.drain_started is not None and
                        all(s.complete for s in self.states.values()) and
                        time.monotonic() - self.drain_started >= 0.1):
                    break
                readable, _, _ = select.select([self.tcp], [], [], .02)
                if readable:
                    self.tcp.settimeout(self.limits.body_idle_timeout_seconds)
                    # Bound transient decrypted-byte staging to one standard
                    # HTTP/2 frame's payload at a time. TLS/HPACK engine
                    # internals remain separately owned and audited.
                    data = self.tcp.recv(16384)
                    if not data:
                        break
                    self.last_progress = time.monotonic()
                    self.server.observe_h2_transport(read_chunk=len(data))
                    self.compressed_headers.feed(data)
                    for event in self.conn.receive_data(data):
                        self._consume(event, pool)
                    self.server.observe_h2_transport(
                        parser_pending=self.conn.incoming_buffer.peak_pending_bytes,
                        hpack_dynamic=getattr(self.conn.decoder.header_table, '_current_size', 0))
                elif (time.monotonic() - self.last_progress > self.limits.connection_idle_timeout_seconds
                      and not any(s.admitted and not s.complete for s in self.states.values())):
                    break
        except (OSError, TimeoutError, h2.exceptions.H2Error, ValueError) as exc:
            _LOG.debug('Closing invalid/failed HTTP/2 session: %s', type(exc).__name__)
            if isinstance(exc, h2.exceptions.H2Error):
                self.failure_code = getattr(exc, 'error_code', h2.errors.ErrorCodes.PROTOCOL_ERROR)
            elif isinstance(exc, (ValueError,)):
                self.failure_code = h2.errors.ErrorCodes.PROTOCOL_ERROR
        finally:
            # Cancel pending jobs without waiting for non-cooperative user
            # callbacks. Their running counterparts still own admission until
            # their own finally path executes.
            for pending in pool.shutdown():
                pending.cancelled.set()
                pending.flush_pending = False
                if not pending.done.is_set():
                    pending.done.set()
                if pending.producer is not None and not pending.producer_done:
                    Thread(target=self._close_abandoned_producer, args=(pending,),
                           name='valiance-h2-source-close', daemon=True).start()
                else:
                    self._account(pending, False)
            for s in self.states.values():
                s.cancelled.set()
                if s.websocket_owner is not None:
                    s.websocket_owner.abort(1001, fast=True)
                self._discard_producer_queue(s)
                self._release_producer_chunks(s)
                self._release_buffered(s)
                self._release_response_buffer(s)
                s.flush_pending = False
                s.cancelled.set()
                self._account(s, False)
                with s.condition:
                    s.condition.notify_all()
                # Producers own their source cleanup; do not invoke a
                # possibly blocking close() in the connection worker.
            try:
                self.conn.close_connection(error_code=self.failure_code,
                                           last_stream_id=self.last_admitted_stream)
            except h2.exceptions.H2Error:
                # The engine might already have queued an error GOAWAY.
                pass
            try:
                self.tcp.sendall(self.conn.data_to_send())
            except OSError:
                pass


def _error_response(exc: Exception) -> core.Response:
    """Translate a stream request failure into a response."""
    from valiance.std._web_http1 import _failure_response
    return _failure_response(exc)
