"""Shared RFC 6455 framing with HTTP/1.1 and RFC 8441 HTTP/2 handshakes.

HTTP/2 uses a per-stream socket-like transport, never a connection-wide
upgrade. Optional permessage-deflate negotiates no-context-takeover. Opt-in heartbeat
supervision owns one input parser and a bounded application-message FIFO.
"""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, replace
import hashlib
import math
import os
import re
import select
import socket
import struct
import time
import zlib
from collections import deque
from threading import Condition, Event, Lock, Thread, current_thread
from urllib.parse import urlsplit
from typing import Callable

from valiance.std import _web_core as core

_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


@dataclass(frozen=True)
class WebSocketPolicy:
    allowed_origins: tuple[str, ...] = ()  # Defaults to exact same-origin.
    subprotocols: tuple[str, ...] = ()
    require_subprotocol: bool = False
    max_frame_bytes: int = 1024 * 1024
    max_message_bytes: int = 8 * 1024 * 1024
    idle_timeout_seconds: float = 60.0
    write_timeout_seconds: float = 30.0
    # RFC/spec default enables idle-ping supervision even when application
    # code never calls receive. Explicit 0 disables automatic heartbeats.
    heartbeat_interval_seconds: float = 30.0
    pong_timeout_seconds: float = 10.0
    extension_policy: str = 'Disabled'

    def __post_init__(self) -> None:
        if self.extension_policy not in ('Disabled', 'PermessageDeflate'):
            raise core.ConfigError('Unsupported WebSocket extension policy')
        if any(not isinstance(value, str) or not value for value in self.allowed_origins):
            raise core.ConfigError('Invalid WebSocket origin policy')
        # Fail at policy construction rather than letting a malformed allowlist
        # produce request-time failures (or accidentally broaden an origin).
        for value in self.allowed_origins:
            try:
                _origin_tuple(value)
            except core.WebError as exc:
                raise core.ConfigError('Invalid WebSocket origin policy') from exc
        if (len(set(self.subprotocols)) != len(self.subprotocols) or
                any(not _TOKEN.fullmatch(s) for s in self.subprotocols)):
            raise core.ConfigError('Invalid WebSocket subprotocol policy')
        if (type(self.max_frame_bytes) is not int or not 0 < self.max_frame_bytes <= 1024 ** 3 or
                type(self.max_message_bytes) is not int or not 0 < self.max_message_bytes <= 1024 ** 3 or
                self.max_message_bytes < self.max_frame_bytes):
            raise core.ConfigError('Invalid WebSocket frame or message limit')
        if any(type(t) not in (float, int) or not math.isfinite(t) or not 0 < t <= 86400
               for t in (self.idle_timeout_seconds, self.write_timeout_seconds,
                         self.pong_timeout_seconds)) or (
                type(self.heartbeat_interval_seconds) not in (float, int) or
                not math.isfinite(self.heartbeat_interval_seconds) or
                not 0 <= self.heartbeat_interval_seconds <= 86400):
            raise core.ConfigError('Invalid WebSocket timeout')


@dataclass(frozen=True)
class WebSocketMessage:
    kind: str  # 'Text' | 'Binary' | 'Closed'
    value: str | bytes | None = None
    code: int = 1000
    reason: str = ''


def _single(headers: core.Headers, name: str) -> str | None:
    values = headers.values(name)
    if len(values) > 1:
        raise core.WebError('BadRequest', f'Duplicate WebSocket {name}')
    return values[0] if values else None


def _tokens(value: str | None) -> set[str]:
    if value is None:
        return set()
    items = [v.strip() for v in value.split(',')]
    if not items or any(not _TOKEN.fullmatch(v) for v in items):
        raise core.WebError('BadRequest', 'Malformed WebSocket token list')
    return {v.lower() for v in items}


def upgrade_requested(headers: core.Headers) -> bool:
    """A suspicious WebSocket request must never become a normal GET."""
    return any(headers.values(name) for name in
               ('Upgrade', 'Sec-WebSocket-Key', 'Sec-WebSocket-Version',
                'Sec-WebSocket-Protocol', 'Sec-WebSocket-Extensions')) or (
                    'upgrade' in _tokens(','.join(headers.values('Connection')) or None))


def _origin_tuple(value: str) -> tuple[str, str, int]:
    # urllib.parse strips leading C0 controls and embedded TAB/CR/LF before
    # splitting. The wire Origin grammar does not: silently normalizing these
    # can turn a malformed attacker-supplied Origin into a trusted one.
    if not value or any(ord(char) <= 0x20 or ord(char) == 0x7f for char in value):
        raise core.WebError('Forbidden', 'Invalid WebSocket Origin')
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise core.WebError('Forbidden', 'Invalid WebSocket Origin') from exc
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or
            parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise core.WebError('Forbidden', 'Invalid WebSocket Origin')
    try:
        # An explicitly supplied port is not interchangeable with the
        # scheme's default. In particular ``:0`` must never compare equal to
        # an omitted port. A trailing empty port is not a serialized Origin.
        if parsed.netloc.endswith(':'):
            raise ValueError('empty Origin port')
        port = parsed.port
        if port is None:
            port = 443 if parsed.scheme == 'https' else 80
    except ValueError as exc:
        raise core.WebError('Forbidden', 'Invalid WebSocket Origin') from exc
    return parsed.scheme, parsed.hostname.lower(), port


# Only accept offers whose parameters can be honored without retaining a
# compression dictionary from earlier messages. Unknown offers are declined.
# Explicitly opting in never permits reflection of arbitrary extension names.
_DEFLATE_RESPONSE = 'permessage-deflate; server_no_context_takeover; client_no_context_takeover'


def _negotiated_deflate(headers: core.Headers, policy: WebSocketPolicy) -> bool:
    if policy.extension_policy == 'Disabled':
        return False
    offers = headers.values('Sec-WebSocket-Extensions')
    if not offers:
        return False
    if sum(map(len, offers)) > 4096:
        raise core.WebError('BadRequest', 'WebSocket extension offer too large')
    for item in ','.join(offers).split(','):
        fields = [field.strip() for field in item.split(';')]
        if fields[0].lower() != 'permessage-deflate':
            continue
        seen: set[str] = set()
        valid = True
        for field in fields[1:]:
            key, sep, value = field.partition('=')
            key = key.strip().lower()
            if key in seen:
                valid = False
                break
            seen.add(key)
            if key in ('client_no_context_takeover', 'server_no_context_takeover'):
                valid = not sep
            elif key == 'client_max_window_bits':
                valid = not sep or (value.isascii() and value.isdecimal() and
                                    str(int(value)) == value and 8 <= int(value) <= 15)
            else:
                # In particular decline server_max_window_bits offers: this
                # implementation uses the full 15-bit outgoing window.
                valid = False
            if not valid:
                break
        if valid:
            return True
    return False


def validate_handshake(request: core.Request, policy: WebSocketPolicy) -> tuple[str, str | None, bool]:
    """Return Sec-WebSocket-Accept, subprotocol and deflate negotiation."""
    fields = request.headers
    if request.method != 'GET' or request.protocol != 'HTTP/1.1':
        raise core.WebError('BadRequest', 'WebSocket HTTP/1.1 requires GET')
    if ('websocket' not in _tokens(','.join(fields.values('Upgrade')) or None) or
            'upgrade' not in _tokens(','.join(fields.values('Connection')) or None)):
        raise core.WebError('BadRequest', 'Invalid WebSocket Upgrade headers')
    if _single(fields, 'Sec-WebSocket-Version') != '13':
        raise core.WebError('BadRequest', 'Unsupported WebSocket version')
    key = _single(fields, 'Sec-WebSocket-Key')
    try:
        decoded = base64.b64decode(key or '', validate=True)
        if len(decoded) != 16 or base64.b64encode(decoded).decode('ascii') != key:
            raise ValueError('invalid WebSocket nonce')
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise core.WebError('BadRequest', 'Invalid WebSocket key') from exc
    if fields.values('Transfer-Encoding') or _single(fields, 'Content-Length') not in (None, '0'):
        raise core.WebError('BadRequest', 'WebSocket handshake may not carry a request body')
    host = _single(fields, 'Host')
    if not host or not request.authority:
        raise core.WebError('BadRequest', 'WebSocket Host is required')
    origin = _single(fields, 'Origin')
    if origin is not None:
        candidate = _origin_tuple(origin)
        same = _origin_tuple(f'{request.scheme}://{host}')
        allowed = { _origin_tuple(o) for o in policy.allowed_origins }
        if candidate != same and candidate not in allowed:
            raise core.WebError('Forbidden', 'WebSocket Origin is not allowed')
    offered = _single(fields, 'Sec-WebSocket-Protocol')
    protocols: list[str] = []
    if offered is not None:
        protocols = [part.strip() for part in offered.split(',')]
        if len(set(protocols)) != len(protocols) or any(not _TOKEN.fullmatch(v) for v in protocols):
            raise core.WebError('BadRequest', 'Invalid WebSocket protocol offer')
    selected = next((p for p in policy.subprotocols if p in protocols), None)
    if selected is None and policy.require_subprotocol:
        raise core.WebError('BadRequest', 'WebSocket subprotocol required')
    deflate = _negotiated_deflate(fields, policy)
    accept = base64.b64encode(hashlib.sha1((key + _GUID).encode('ascii')).digest()).decode('ascii')
    return accept, selected, deflate



def validate_h2_handshake(request: core.Request, policy: WebSocketPolicy
                          ) -> tuple[str | None, bool]:
    """Validate RFC 8441 CONNECT WebSocket headers and select a subprotocol.

    Unlike RFC 6455 over H1, Sec-WebSocket-Key/Accept and HTTP Upgrade
    headers do not participate in this handshake. HTTP/2 :authority replaces
    Host, and successful extended CONNECT receives :status=200, not 101.
    """
    if request.method != 'CONNECT' or request.protocol != 'HTTP/2':
        raise core.WebError('BadRequest', 'WebSocket HTTP/2 requires extended CONNECT')
    headers = request.headers
    if _single(headers, 'Sec-WebSocket-Version') != '13':
        raise core.WebError('BadRequest', 'Unsupported WebSocket version')
    if (headers.values('Connection') or headers.values('Upgrade') or
            headers.values('Transfer-Encoding')):
        raise core.WebError('BadRequest', 'Connection-specific WebSocket fields are forbidden')
    # HTTP/2 cannot tunnel an outgoing connection to an arbitrary :authority.
    origin = _single(headers, 'Origin')
    if origin is not None:
        candidate = _origin_tuple(origin)
        same = _origin_tuple(f'{request.scheme}://{request.authority}')
        allowed = {_origin_tuple(value) for value in policy.allowed_origins}
        if candidate != same and candidate not in allowed:
            raise core.WebError('Forbidden', 'WebSocket Origin is not allowed')
    offered = _single(headers, 'Sec-WebSocket-Protocol')
    protocols = [] if offered is None else [part.strip() for part in offered.split(',')]
    if len(set(protocols)) != len(protocols) or any(not _TOKEN.fullmatch(p) for p in protocols):
        raise core.WebError('BadRequest', 'Invalid WebSocket subprotocol offer')
    selected = next((value for value in policy.subprotocols if value in protocols), None)
    if selected is None and policy.require_subprotocol:
        raise core.WebError('BadRequest', 'WebSocket subprotocol required')
    return selected, _negotiated_deflate(headers, policy)

def _valid_close_code(code: int) -> bool:
    # RFC 6455 §7.4 and the IANA WebSocket Close Code Number Registry:
    # 1012 (service restart), 1013 (try again later) and 1014 (gateway)
    # are assigned. 1004/1005/1006/1015 must never appear on the wire;
    # 1016..2999 remain unassigned. 3000..4999 are app/private-use ranges.
    return code in (1000, 1001, 1002, 1003, 1007, 1008, 1009, 1010,
                    1011, 1012, 1013, 1014) or 3000 <= code <= 4999


class WebSocket:
    """Nonduplicable conceptual owner of one masked-client RFC 6455 stream."""

    def __init__(self, stream: socket.socket, request: core.Request, policy: WebSocketPolicy,
                 *, initial_bytes: bytes = b'', subprotocol: str | None = None,
                 buffer_budget: object | None = None,
                 permessage_deflate: bool = False):
        self.request = request
        self.subprotocol = subprotocol
        self._permessage_deflate = permessage_deflate
        self._socket = stream
        limits = getattr(buffer_budget, 'web_limits', None)
        if limits is not None:
            # Both the route policy and the listener limits apply. A route
            # cannot widen the fixed public per-message/frame safety caps.
            message_cap = min(policy.max_message_bytes, limits.max_websocket_messages_bytes)
            frame_cap = min(policy.max_frame_bytes, limits.max_websocket_frame_bytes,
                            message_cap)
            policy = replace(
                policy, max_frame_bytes=frame_cap, max_message_bytes=message_cap,
                heartbeat_interval_seconds=min(policy.heartbeat_interval_seconds,
                                               limits.web_socket_ping_interval_seconds)
                if policy.heartbeat_interval_seconds else 0,
                pong_timeout_seconds=min(policy.pong_timeout_seconds,
                                         limits.web_socket_pong_timeout_seconds),
            )
        self._policy = policy
        # H1 may have consumed bytes past the Upgrade request before handing
        # the socket to this owner. Charge those staged octets until consumed.
        self._initial_lock = Lock()
        self._initial_bytes_remaining = len(initial_bytes)
        self._buffer = bytearray(initial_bytes)
        self._send_lock = Lock()
        # Exactly one consumer may parse the framed byte stream at a time.
        # Concurrent receives could otherwise split a frame header and mask
        # between two callbacks, violating the owned-connection contract.
        self._receive_lock = Lock()
        self._closed = False
        self._abort_guard = Lock()
        self._aborted = False
        self._child_cancel = Event()
        self._children_lock = Lock()
        self._children: set[Thread] = set()
        self._sent_close = False
        self._received_close = False
        self._reader_stop = Event()
        # A locally cancelled or disconnected owner notifies application
        # supervision without altering HTTP response state after an upgrade.
        self._terminated = Event()
        self._reader: Thread | None = None
        self._pending_ping: bytes | None = None
        self._pong_deadline = 0.0
        self._last_peer_activity = time.monotonic()
        self._incoming_condition = Condition()
        self._incoming_messages: deque[WebSocketMessage] = deque()
        self._incoming_bytes = 0
        # A heartbeat reader can accumulate messages while the application is
        # idle. Charge these bytes against the listener-wide buffer budget;
        # the H2 framed transport releases its *wire* reservation on read,
        # then this queue takes over the reservation until receive/abort.
        self._buffer_budget = buffer_budget
        if (self._initial_bytes_remaining and buffer_budget is not None and not
                buffer_budget.reserve_h2_buffer(self._initial_bytes_remaining)):
            raise core.WebError('BodyTooLarge', 'WebSocket upgrade staging budget exceeded')
        self._max_outbound_bytes = (limits.max_websocket_queued_send_bytes if limits
                                    else 4 * 1024 * 1024)
        self._outgoing_bytes = 0
        # The inbound-frame parser reserves quota *before* reading the frame
        # body, including manual receive with automatic heartbeat disabled.
        self._parsing_bytes = 0
        self._inflate_bytes = 0
        self._incoming_error: core.WebError | None = None
        self._incoming_finished = False
        stream.settimeout(policy.idle_timeout_seconds)
        set_write_timeout = getattr(stream, 'set_write_timeout', None)
        if callable(set_write_timeout):
            set_write_timeout(policy.write_timeout_seconds)
        if policy.heartbeat_interval_seconds:
            # The H2 virtual socket has its own read wait; a short interval
            # lets the heartbeat reader examine deadlines without application
            # receives. Real TCP/TLS sockets instead use select() before recv,
            # keeping their independently configured send timeout intact.
            if not isinstance(stream, socket.socket):
                stream.settimeout(.1)
            self._reader = Thread(target=self._heartbeat_reader,
                                  name='valiance-websocket-heartbeat', daemon=True)
            self._reader.start()

    def spawn_child(self, callback: Callable[[Event], None]) -> Thread:
        """Start an explicitly session-owned *cooperative* Python child.

        The socket owns cancellation and bounded cleanup. Callbacks must
        observe the supplied Event; Python cannot forcibly stop arbitrary
        blocking code. Valiance `spawn` tasks are instead supervised by the
        VM callback's structured TaskScope.
        """
        if not callable(callback):
            raise TypeError('WebSocket child callback must be callable')

        def run_child() -> None:
            try:
                callback(self._child_cancel)
            except Exception:
                # A child failure invalidates this protocol session, never an
                # already-committed HTTP response on the upgraded connection.
                self.abort(1011)
            finally:
                with self._children_lock:
                    self._children.discard(current_thread())

        with self._children_lock:
            if self.cancelled or self._aborted:
                raise core.WebError('BodyUnavailable', 'WebSocket session is closed')
            if len(self._children) >= 32:
                raise core.WebError('ServiceUnavailable', 'WebSocket child-task quota exceeded')
            thread = Thread(target=run_child, name='valiance-websocket-child', daemon=True)
            self._children.add(thread)
            thread.start()
            return thread

    @property
    def cancellation_event(self) -> Event:
        """Signal shared with the native callback's structured task scope.

        Only the owning VM thread may mutate scheduler state. This event is
        safe to set from a peer reset, heartbeat reader or listener thread;
        the bytecode engine observes it at cancellation checkpoints.
        """
        return self._terminated

    @property
    def cancelled(self) -> bool:
        """Whether the owner or an HTTP/2 peer reset ended this session."""
        return (self._terminated.is_set() or
                bool(getattr(getattr(self._socket, 'state', None), 'cancelled', None)
                     and self._socket.state.cancelled.is_set()))

    def _read(self, n: int) -> bytes:
        """Read exactly one frame segment while allowing session cancellation."""
        deadline = time.monotonic() + self._policy.idle_timeout_seconds
        while len(self._buffer) < n:
            supervised = self._reader is not None and current_thread() is self._reader
            if self._terminated.is_set():
                raise EOFError('WebSocket transport closed')
            if supervised:
                self._heartbeat_tick()
            if isinstance(self._socket, socket.socket):
                remaining = .1 if supervised else deadline - time.monotonic()
                if remaining <= 0:
                    raise socket.timeout('WebSocket read timed out')
                try:
                    pending = self._socket.pending() if hasattr(self._socket, 'pending') else 0
                    if not pending and not select.select([self._socket], [], [], min(.1, remaining))[0]:
                        continue
                except (OSError, ValueError) as exc:
                    raise EOFError('WebSocket transport closed') from exc
            try:
                data = self._socket.recv(min(65536, n - len(self._buffer)))
            except socket.timeout:
                if self._reader is not None and current_thread() is self._reader:
                    continue
                raise
            if not data:
                raise EOFError('WebSocket transport closed')
            self._buffer.extend(data)
            deadline = time.monotonic() + self._policy.idle_timeout_seconds
        chunk = bytes(self._buffer[:n])
        del self._buffer[:n]
        with self._initial_lock:
            staged = min(n, self._initial_bytes_remaining)
            self._initial_bytes_remaining -= staged
            if staged and self._buffer_budget is not None:
                self._buffer_budget.release_h2_buffer(staged)
        return chunk

    def _send_wire(self, header: bytes, payload: bytes) -> None:
        """Bound a complete server-side frame while it is handed to H1/H2.

        The H2 transport also independently holds reservations for chunks it
        queues behind a closed flow-control window; ownership is transferred
        by sendall and that transport releases its own queue reservations on
        flush/reset. The H1 path holds this reservation until sendall finishes.
        """
        length = len(header) + len(payload)
        if length > self._max_outbound_bytes:
            raise core.WebError('BodyTooLarge', 'WebSocket outgoing frame quota exceeded')
        if (self._buffer_budget is not None and
                not self._buffer_budget.reserve_h2_buffer(length)):
            raise core.WebError('BodyTooLarge', 'Shared WebSocket outgoing quota exceeded')
        self._outgoing_bytes += length
        try:
            # A stalled HTTP/1.1/TLS peer must not make sendall exceed the
            # route's write-idle deadline. H2 uses its own independent
            # per-stream deadline in its virtual socket transport.
            if isinstance(self._socket, socket.socket):
                previous = self._socket.gettimeout()
                effective = min(previous, self._policy.write_timeout_seconds) if previous else self._policy.write_timeout_seconds
                try:
                    self._socket.settimeout(effective)
                    self._socket.sendall(header + payload)
                finally:
                    try:
                        self._socket.settimeout(previous)
                    except OSError:
                        pass
            else:
                self._socket.sendall(header + payload)
        finally:
            self._outgoing_bytes -= length
            if self._buffer_budget is not None:
                self._buffer_budget.release_h2_buffer(length)

    def _frame(self, opcode: int, payload: bytes = b'') -> None:
        if opcode >= 8 and len(payload) > 125:
            raise core.WebError('BadRequest', 'WebSocket control frame too large')
        if len(payload) > self._policy.max_frame_bytes:
            raise core.WebError('BodyTooLarge', 'WebSocket frame too large')
        # The close flag and frame write must be serialized together: the
        # application callback, heartbeat reader and shutdown path may race.
        with self._send_lock:
            if opcode == 8 and self._sent_close:
                return
            if self._closed or self._sent_close:
                raise core.WebError('BodyUnavailable', 'WebSocket has closed')
            if opcode == 8:
                self._sent_close = True
            length = len(payload)
            header = bytes([0x80 | opcode, length]) if length < 126 else (
                bytes([0x80 | opcode, 126]) + struct.pack('!H', length) if length < 65536 else
                bytes([0x80 | opcode, 127]) + struct.pack('!Q', length))
            self._send_wire(header, payload)

    def _message(self, opcode: int, payload: bytes) -> None:
        if len(payload) > self._policy.max_message_bytes:
            raise core.WebError('BodyTooLarge', 'WebSocket message exceeds configured limit')
        if self._closed or self._sent_close:
            raise core.WebError('BodyUnavailable', 'WebSocket has closed')
        # Compression uses a fresh dictionary on each message; the fixed
        # no-context-takeover response guarantees bounded dictionary lifetime.
        # Reserve conservative staging memory *before* creating compressed
        # copies, separate from the already-budgeted frame write queue.
        compression_reserved = 0
        if self._permessage_deflate and payload:
            compression_reserved = len(payload) * 2 + 65536
            if (self._buffer_budget is not None and not
                    self._buffer_budget.reserve_h2_buffer(compression_reserved)):
                raise core.WebError('BodyTooLarge', 'WebSocket compression staging budget exceeded')
            try:
                compressor = zlib.compressobj(wbits=-15)
                payload = compressor.compress(payload) + compressor.flush(zlib.Z_SYNC_FLUSH)
                if not payload.endswith(b'\x00\x00\xff\xff'):
                    raise core.WebError('InvalidResponse', 'Invalid WebSocket compression flush')
                payload = payload[:-4]
            except Exception:
                if compression_reserved and self._buffer_budget is not None:
                    self._buffer_budget.release_h2_buffer(compression_reserved)
                raise
        try:
            self._message_frames(opcode, payload, compressed=bool(compression_reserved))
        finally:
            if compression_reserved and self._buffer_budget is not None:
                self._buffer_budget.release_h2_buffer(compression_reserved)

    def _message_frames(self, opcode: int, payload: bytes, *, compressed: bool) -> None:
        # Hold the lock for the entire fragmented message. An intervening
        # application control frame may be serialized *between* messages.
        with self._send_lock:
            # Abort may have acquired the owner transition while this sender
            # was waiting for serialization. Recheck *under* the lock so an
            # already-cancelled connection never starts a new message.
            if self._closed or self._sent_close or self._terminated.is_set():
                raise core.WebError('BodyUnavailable', 'WebSocket has closed')
            # Even a route with an 8 MiB message cap cannot place an oversized
            # individual frame into a 4 MiB default outbound queue.
            chunk_size = min(self._policy.max_frame_bytes,
                             max(1, self._max_outbound_bytes - 10))
            for offset in range(0, len(payload), chunk_size):
                chunk = payload[offset:offset + chunk_size]
                fin = offset + len(chunk) >= len(payload)
                kind = opcode if offset == 0 else 0
                length = len(chunk)
                first = (0x80 if fin else 0) | kind | (0x40 if compressed and offset == 0 else 0)
                header = (bytes([first, length]) if length < 126 else
                          bytes([first, 126]) + struct.pack('!H', length)
                          if length < 65536 else
                          bytes([first, 127]) + struct.pack('!Q', length))
                self._send_wire(header, chunk)
            if not payload:
                self._send_wire(bytes([0x80 | opcode, 0]), b'')

    def send_text(self, text: str) -> None:
        try:
            encoded = text.encode('utf-8')
        except (UnicodeError, AttributeError) as exc:
            raise core.WebError('BadRequest', 'WebSocket text is not valid UTF-8') from exc
        self._message(1, encoded)

    def send_bytes(self, payload: bytes) -> None:
        self._message(2, bytes(payload))

    def ping(self, data: bytes = b'') -> None:
        if len(data) > 125:
            raise core.WebError('BadRequest', 'WebSocket ping too large')
        self._frame(9, bytes(data))

    def close(self, code: int = 1000, reason: str = '') -> None:
        # RFC 6455 §7.4.1 reserves 1010 (Mandatory Extension) for a client
        # that did not receive its required handshake extensions. A server
        # rejects those handshakes instead of sending close code 1010.
        if code == 1010 or not _valid_close_code(code):
            raise core.WebError('BadRequest', 'Invalid WebSocket close code')
        try:
            encoded_reason = reason.encode('utf-8')
        except (UnicodeError, AttributeError) as exc:
            raise core.WebError('BadRequest', 'WebSocket close reason is not valid UTF-8') from exc
        payload = struct.pack('!H', code) + encoded_reason
        if len(payload) > 125:
            raise core.WebError('BadRequest', 'WebSocket close reason too long')
        if not self._closed:
            self._frame(8, payload)

    def abort(self, code: int = 1001, *, fast: bool = False) -> None:
        """Cancel a session without waiting on a backpressured writer.

        Cancellation is an exclusive ownership transition. Attempt a graceful
        close only if no other writer holds the send lock; otherwise interrupt
        that writer first. This prevents shutdown / H2 reset / heartbeat from
        deadlocking behind a blocked DATA send. Queued payload reservations
        remain owned by the transport, not this framing layer.
        """
        with self._abort_guard:
            if self._aborted:
                return
            self._aborted = True
        self._terminated.set()
        self._reader_stop.set()
        self._child_cancel.set()
        acquired = self._send_lock.acquire(blocking=False)
        try:
            if acquired and not self._closed and not self._sent_close and code != 1010 and _valid_close_code(code):
                self._sent_close = True
                payload = struct.pack('!H', code)
                frame = bytes([0x88, len(payload)]) + payload
                # A peer may have stopped reading. Never wait for an outgoing
                # flow-control window or a full TLS send during cancellation.
                try:
                    nowait = getattr(self._socket, 'send_close_nowait', None)
                    if callable(nowait):
                        nowait(frame)
                    else:
                        # The connection owner is being torn down, so changing
                        # this socket's timeout cannot affect a later request.
                        self._socket.settimeout(.002 if fast else .1)
                        self._socket.sendall(frame)
                except (OSError, TimeoutError):
                    pass
            self._closed = True
        finally:
            if acquired:
                self._send_lock.release()
        try:
            if isinstance(self._socket, socket.socket):
                self._socket.shutdown(socket.SHUT_WR if acquired else socket.SHUT_RDWR)
            else:
                if not acquired and callable(getattr(self._socket, 'interrupt_write', None)):
                    self._socket.interrupt_write()
                if callable(getattr(self._socket, 'interrupt_read', None)):
                    self._socket.interrupt_read()
        except OSError:
            pass
        with self._initial_lock:
            staged = self._initial_bytes_remaining
            self._initial_bytes_remaining = 0
            if staged and self._buffer_budget is not None:
                self._buffer_budget.release_h2_buffer(staged)
        with self._incoming_condition:
            if self._incoming_bytes and self._buffer_budget is not None:
                self._buffer_budget.release_h2_buffer(self._incoming_bytes)
            self._incoming_messages.clear()
            self._incoming_bytes = 0
            self._incoming_condition.notify_all()
        # Normal session teardown reaps cooperative owned tasks. A forced
        # listener teardown can defer joining to the common shutdown deadline.
        if not fast:
            self.join_owned(.2)

    @property
    def owned_threads_alive(self) -> int:
        """Running heartbeat/explicit session children, excluding the caller.

        This is deliberately not a count of arbitrary application callbacks:
        those execute in the transport's request worker and cannot be killed
        by the session. After cancellation no new owned child is admissible.
        """
        with self._children_lock:
            children = tuple(self._children)
        return sum(child.is_alive() for child in children) + int(
            self._reader is not None and self._reader.is_alive())

    def join_owned(self, timeout: float) -> int:
        """Join cooperative owned threads within one shared monotonic deadline.

        Returns the number still running. Never attempts to terminate a
        Python thread, join the calling thread, or hold socket/send locks.
        The caller must signal cancellation (normally via abort()) first.
        """
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError('Invalid WebSocket child join timeout')
        deadline = time.monotonic() + timeout
        with self._children_lock:
            children = tuple(self._children)
        owned = ((self._reader,) if self._reader is not None else ()) + children
        for thread in owned:
            if thread is not current_thread() and thread.is_alive():
                thread.join(timeout=max(0, deadline - time.monotonic()))
        return sum(thread.is_alive() for thread in owned
                   if thread is not current_thread())

    def _heartbeat_tick(self) -> None:
        """Check a monotonic Pong deadline without consuming application frames."""
        if self._reader_stop.is_set():
            raise EOFError('WebSocket session closed')
        now = time.monotonic()
        if self._pending_ping is not None:
            if now >= self._pong_deadline:
                self.abort(1001)
                raise core.WebError('BodyUnavailable', 'WebSocket heartbeat Pong deadline exceeded')
        elif now - self._last_peer_activity >= self._policy.heartbeat_interval_seconds:
            token = os.urandom(8)
            self._pending_ping = token
            self._pong_deadline = now + self._policy.pong_timeout_seconds
            self._frame(9, token)

    def _heartbeat_reader(self) -> None:
        """One reader owns control frames; application messages enter a bounded FIFO.

        A queue overflow is a session-local failure, not unbounded buffering.
        Processing control frames remains possible when the handler never calls
        receive(), so Pong deadlines cannot depend on application scheduling.
        """
        try:
            while not self._reader_stop.is_set():
                message = self._receive_exclusive()
                size = (len(message.value.encode('utf-8')) if message.kind == 'Text'
                        else len(message.value) if message.kind == 'Binary' else 0)
                with self._incoming_condition:
                    if (len(self._incoming_messages) >= 32 or
                            self._incoming_bytes + size > self._policy.max_message_bytes):
                        raise core.WebError('BodyTooLarge', 'WebSocket pending-message quota exceeded')
                    if (size and self._buffer_budget is not None and not
                            self._buffer_budget.reserve_h2_buffer(size)):
                        raise core.WebError('BodyTooLarge',
                                            'Shared WebSocket pending-message budget exceeded')
                    self._incoming_messages.append(message)
                    self._incoming_bytes += size
                    self._incoming_condition.notify_all()
                if message.kind == 'Closed':
                    break
        except core.WebError as exc:
            self._incoming_error = exc
            self.abort(1009 if exc.name == 'BodyTooLarge' else 1001)
        except Exception as exc:
            self._incoming_error = core.WebError('BodyUnavailable',
                                                 f'WebSocket input unavailable: {exc}')
            self.abort(1001)
        finally:
            with self._incoming_condition:
                self._incoming_finished = True
                self._incoming_condition.notify_all()

    def _incoming_frame(self) -> tuple[int, bool, bytes, bool]:
        first, second = self._read(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0f
        compressed = bool(first & 0x40)
        if ((first & 0x30) or
                (compressed and (not self._permessage_deflate or opcode not in (1, 2))) or
                opcode not in (0, 1, 2, 8, 9, 10)):
            raise _ProtocolError(1002, 'Reserved WebSocket bits/opcode')
        if not second & 0x80:
            raise _ProtocolError(1002, 'Client WebSocket frame was not masked')
        length = second & 0x7f
        if length == 126:
            length = struct.unpack('!H', self._read(2))[0]
            if length < 126:
                raise _ProtocolError(1002, 'Nonminimal frame length')
        elif length == 127:
            raw = self._read(8)
            if raw[0] & 0x80:
                raise _ProtocolError(1002, 'Invalid frame length')
            length = struct.unpack('!Q', raw)[0]
            if length < 65536:
                raise _ProtocolError(1002, 'Nonminimal frame length')
        if opcode >= 8 and (not fin or length > 125):
            raise _ProtocolError(1002, 'Invalid control frame')
        if length > self._policy.max_frame_bytes:
            raise _ProtocolError(1009, 'WebSocket frame too large')
        if length and self._buffer_budget is not None:
            required = 4 * length
            if not self._buffer_budget.reserve_h2_buffer(required):
                raise _ProtocolError(1009, 'Shared WebSocket in-flight message budget exceeded')
            self._parsing_bytes += required
        mask = self._read(4)
        payload = self._read(length)
        return opcode, fin, bytes(v ^ mask[i % 4] for i, v in enumerate(payload)), compressed

    def receive(self) -> WebSocketMessage:
        if not self._receive_lock.acquire(blocking=False):
            raise core.WebError('BodyUnavailable', 'WebSocket already has an active receiver')
        try:
            if self._reader is None:
                return self._receive_exclusive()
            with self._incoming_condition:
                while not self._incoming_messages and not self._incoming_finished:
                    self._incoming_condition.wait(timeout=.2)
                if self._incoming_messages:
                    message = self._incoming_messages.popleft()
                    size = (len(message.value.encode('utf-8')) if message.kind == 'Text'
                            else len(message.value) if message.kind == 'Binary' else 0)
                    self._incoming_bytes -= size
                    if size and self._buffer_budget is not None:
                        self._buffer_budget.release_h2_buffer(size)
                    return message
                if self._incoming_error is not None:
                    raise self._incoming_error
                return WebSocketMessage('Closed', code=1006)
        finally:
            self._receive_lock.release()

    def _receive_exclusive(self) -> WebSocketMessage:
        if self._closed or self._received_close:
            return WebSocketMessage('Closed', code=1006)
        parts = bytearray()
        current_op: int | None = None
        inflator: zlib.Decompress | None = None
        compressed_bytes = 0
        try:
            while True:
                opcode, fin, payload, compressed = self._incoming_frame()
                self._last_peer_activity = time.monotonic()
                if opcode == 9:
                    self._frame(10, payload)
                    continue
                if opcode == 10:
                    if self._reader is not None and payload == self._pending_ping:
                        self._pending_ping = None
                    continue
                if opcode == 8:
                    if len(payload) == 1:
                        raise _ProtocolError(1002, 'Invalid close payload')
                    code, reason = (1005, '') if not payload else (struct.unpack('!H', payload[:2])[0], '')
                    if payload:
                        if not _valid_close_code(code):
                            raise _ProtocolError(1002, 'Invalid peer close code')
                        try:
                            reason = payload[2:].decode('utf-8', 'strict')
                        except UnicodeDecodeError as exc:
                            raise _ProtocolError(1007, 'Invalid close text') from exc
                    self._received_close = True
                    # A peer close ends the owned callback scope even when
                    # application code is busy elsewhere. Keep the Closed
                    # message available to consumers; abort() would clear
                    # that message and its queued protocol data.
                    self._terminated.set()
                    self._child_cancel.set()
                    if not self._sent_close:
                        # Echoing a valid peer close code is legal; a missing
                        # code is answered with an empty close control frame.
                        self._frame(8, payload)
                        self._sent_close = True
                    return WebSocketMessage('Closed', code=code, reason=reason)
                if opcode in (1, 2):
                    if current_op is not None:
                        raise _ProtocolError(1002, 'Interleaved fragmented messages')
                    current_op = opcode
                    if compressed:
                        # Reserve the *maximum possible* uncompressed message
                        # before asking zlib to allocate any output bytes.
                        self._inflate_bytes = self._policy.max_message_bytes
                        if (self._buffer_budget is not None and not
                                self._buffer_budget.reserve_h2_buffer(self._inflate_bytes)):
                            self._inflate_bytes = 0
                            raise _ProtocolError(1009, 'Shared WebSocket inflation budget exceeded')
                        inflator = zlib.decompressobj(wbits=-15)
                elif current_op is None:
                    raise _ProtocolError(1002, 'Unexpected continuation')
                if inflator is not None:
                    compressed_bytes += len(payload)
                    if compressed_bytes > self._policy.max_message_bytes:
                        raise _ProtocolError(1009, 'Compressed wire message too large')
                    try:
                        maximum = self._policy.max_message_bytes - len(parts) + 1
                        decoded = inflator.decompress(payload + (b'\x00\x00\xff\xff' if fin else b''), maximum)
                        if inflator.unconsumed_tail or inflator.unused_data:
                            raise _ProtocolError(1009, 'Compressed WebSocket message too large')
                        payload = decoded
                    except zlib.error as exc:
                        raise _ProtocolError(1002, 'Invalid compressed WebSocket message') from exc
                if len(parts) + len(payload) > self._policy.max_message_bytes:
                    raise _ProtocolError(1009, 'WebSocket message too large')
                # A fixed expansion ceiling limits CPU and memory pressure
                # even when a very small wire payload inflates repeatedly.
                if inflator is not None and len(parts) + len(payload) > max(1024, compressed_bytes * 1024):
                    raise _ProtocolError(1009, 'WebSocket compression ratio exceeded')
                parts.extend(payload)
                if fin:
                    inflator = None
                    if self._inflate_bytes:
                        if self._buffer_budget is not None:
                            self._buffer_budget.release_h2_buffer(self._inflate_bytes)
                        self._inflate_bytes = 0
                    if current_op == 2:
                        return WebSocketMessage('Binary', bytes(parts))
                    try:
                        return WebSocketMessage('Text', bytes(parts).decode('utf-8', 'strict'))
                    except UnicodeDecodeError as exc:
                        raise _ProtocolError(1007, 'Invalid UTF-8 WebSocket message') from exc
        except _ProtocolError as exc:
            self.abort(exc.code)
            raise core.WebError('BadRequest', str(exc)) from exc
        except (TimeoutError, OSError, EOFError) as exc:
            self._closed = True
            self._terminated.set()
            self._child_cancel.set()
            with self._incoming_condition:
                self._incoming_condition.notify_all()
            raise core.WebError('BodyUnavailable', 'WebSocket peer disconnected') from exc
        finally:
            if self._inflate_bytes:
                amount = self._inflate_bytes
                self._inflate_bytes = 0
                if self._buffer_budget is not None:
                    self._buffer_budget.release_h2_buffer(amount)
            if self._parsing_bytes:
                amount = self._parsing_bytes
                self._parsing_bytes = 0
                if self._buffer_budget is not None:
                    self._buffer_budget.release_h2_buffer(amount)


class _ProtocolError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


def approve_upgrade(prepared: core.PreparedApp, route: core.Route,
                    request: core.Request) -> tuple[core.Request, core.Response]:
    """Run WebSocket middleware and after callbacks before committing 101."""
    # A handshake has a dedicated Request variant. Never expose an ordinary
    # GET or CONNECT request to upgrade middleware: both the route kind and
    # original wire method are security-relevant.
    if request.method not in ('GET', 'CONNECT') or request.original_method != request.method:
        raise core.WebError('BadRequest', 'Invalid WebSocket handshake request identity')
    current = replace(request, method='WEBSOCKET')
    result: core.Response | None = None
    afters = []
    for callback in (*prepared.app.middleware, *route.middleware):
        try:
            outcome = callback(current)
            if isinstance(outcome, Exception):
                raise outcome
            if isinstance(outcome, core.After):
                if not core._identity_ok(current, outcome.request) or not callable(outcome.after):
                    raise core.WebError('InvalidResponse', 'WebSocket middleware changed request identity')
                afters.append(outcome.after)
                current = outcome.request
            elif isinstance(outcome, core.Request):
                if not core._identity_ok(current, outcome):
                    raise core.WebError('InvalidResponse', 'WebSocket middleware changed request identity')
                current = outcome
            elif isinstance(outcome, core.Response):
                result = outcome
                break
            else:
                raise core.WebError('InvalidResponse', 'Invalid WebSocket middleware result')
        except Exception as exc:
            result = core._error_response(current, exc, 'BeforeMiddleware', prepared.app)
            break
    if result is None:
        result = core.Response(101)
    for callback in reversed(afters):
        try:
            updated = callback(result)
            if isinstance(updated, Exception):
                raise updated
            if not isinstance(updated, core.Response):
                raise core.WebError('InvalidResponse', 'Invalid WebSocket after callback')
            result = updated
        except Exception as exc:
            result = core._error_response(current, exc, 'AfterMiddleware', prepared.app)
    if result.status == 101 and (result.body is not None or result.trailers.pairs or
                                 result.early_hints or any(name.lower() in (
                                     'sec-websocket-accept', 'sec-websocket-protocol',
                                     'sec-websocket-extensions', 'connection', 'upgrade')
                                     for name, _ in result.headers.pairs)):
        result = core.Response(500, 'Invalid WebSocket handshake response')
    return current, result
