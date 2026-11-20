"""Event-driven libcurl multi reactor for outbound HTTP/1.1 and HTTP/2.

Exactly one reactor thread owns every easy/multi handle for a Client. Reactor
commands and futures cross the host thread boundary; no VM work runs inside a
curl callback. Response chunks are bounded and curl is paused under pressure.
This is a private transport layer; public types do not expose libcurl values.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future
import ctypes as C
import ctypes.util
import ipaddress
import os
from pathlib import Path
import socket
from dataclasses import dataclass
from queue import Empty, Full, Queue, SimpleQueue
import threading
import weakref
from time import monotonic
from typing import Callable
from urllib.parse import urljoin, urlsplit

from valiance.std._http_body import UploadPipe, MultipartUpload, _PROCESS_UPLOAD_BUDGET
from valiance.std._http_cookies import CookieJar
from valiance.std._http_core import ClientConfig, Headers, Request, ResponseMetadata, validate_url
from valiance.std._http_errors import ErrorKind, HttpError, HttpValueError

# libcurl's CURLOPTTYPE constants have been stable since the public C API.
_STR = 10000
_FUN = 20000
_OFF = 30000
_SLIST = 10000
_OPTIONS = {
    "URL": _STR + 2, "PROXY": _STR + 4, "WRITEFUNCTION": _FUN + 11,
    "HEADERFUNCTION": _FUN + 79, "HTTPHEADER": _SLIST + 23,
    "CUSTOMREQUEST": _STR + 36, "NOBODY": 44, "FOLLOWLOCATION": 52,
    "CONNECTTIMEOUT_MS": 156, "TIMEOUT_MS": 155,
    "UPLOAD": 46, "READFUNCTION": _FUN + 12,
    "INFILESIZE_LARGE": _OFF + 115,
    "NOSIGNAL": 99, "SSL_VERIFYPEER": 64, "SSL_VERIFYHOST": 81,
    "HTTP_VERSION": 84, "ACCEPT_ENCODING": _STR + 102,
    "POSTFIELDSIZE_LARGE": _OFF + 120, "COPYPOSTFIELDS": _STR + 165,
    "CAINFO": _STR + 65,
    "OPENSOCKETFUNCTION": _FUN + 163, "NOPROGRESS": 43,
    "XFERINFOFUNCTION": _FUN + 219,
}
_CURL_HTTP_VERSION_2TLS = 4
_CURLINFO_CONN_ID = 0x600000 + 64  # libcurl >= 8.2.0
_WRITE_PAUSE = 0x10000001
_CURLMSG_DONE = 1
_CURLPAUSE_CONT = 0
_READ_PAUSE = 0x10000001
_READ_ABORT = 0x10000000
_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
# Caller-authored headers may contain credentials under *arbitrary* names.
# There is no reliable way to distinguish a custom tracing header from an
# application secret (e.g. X-Session-Key). On a cross-origin redirect, do not
# forward any caller-authored header; libcurl regenerates transport headers.
# This intentionally prioritizes credential isolation over preserving
# application-specific headers on an explicitly enabled redirect.

_BODY_HEADERS = frozenset((
    "content-length", "content-type", "content-encoding", "content-md5",
    "digest", "expect",
))


def _origin(url: str) -> tuple[str, str, int]:
    """Normalize a URL into the origin used for redirect and credential policy checks."""
    parsed = urlsplit(url)
    return parsed.scheme, parsed.hostname or "", parsed.port or (443 if parsed.scheme == "https" else 80)


def _next_redirect(request: Request, status: int, location: str,
                   *, allow_downgrade: bool) -> Request:
    """Apply the explicit redirect method and credential propagation policy.

    301/302 rewrite POST to GET for interoperability; 303 rewrites all
    methods but HEAD to GET. 307/308 never rewrite the method or body.
    """
    # urllib.urljoin silently strips literal CR/LF/TAB before resolving a
    # relative Location. Reject such bytes *before* normalization; otherwise
    # malformed redirect metadata can be accepted as a different target.
    if not isinstance(location, str) or any(ord(c) < 32 or ord(c) == 127 for c in location) or "\\" in location:
        raise HttpError(ErrorKind.PROTOCOL, "invalid redirect location", "redirect")
    try:
        url = validate_url(urljoin(request.url, location))
    except (HttpValueError, ValueError) as exc:
        raise HttpError(ErrorKind.PROTOCOL, str(exc), "redirect") from exc
    old_origin, new_origin = _origin(request.url), _origin(url)
    if old_origin[0] == "https" and new_origin[0] == "http" and not allow_downgrade:
        raise HttpError(ErrorKind.PROTOCOL, "HTTPS-to-HTTP redirect is not allowed", "redirect")
    rewrite = (status == 303 and request.method != "HEAD") or (
        status in (301, 302) and request.method == "POST"
    )
    if isinstance(request.body, (UploadPipe, MultipartUpload)) and not rewrite:
        raise HttpError(ErrorKind.PROTOCOL, "non-replayable upload cannot follow this redirect", "redirect")
    if old_origin != new_origin and not rewrite and request.body:
        # A 307/308 (or a preserving 301/302) can send the full POST/PUT body
        # to a destination chosen by the peer. Treat that body as potentially
        # credential-bearing and require a new explicit application request.
        raise HttpError(ErrorKind.PROTOCOL,
                        "cross-origin redirect cannot replay a request body", "redirect")
    headers = Headers(tuple(
        (name, value) for name, value in request.headers.pairs
        if old_origin == new_origin and not (rewrite and name.casefold() in _BODY_HEADERS)
    ))
    return Request("GET" if rewrite else request.method, url, headers,
                   None if rewrite else request.body)


def _destination_ip(family: int, sockaddr: bytes) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Interpret a libcurl-selected sockaddr, not a separate DNS lookup.

    The address bytes originate from the same sockaddr passed to connect() by
    libcurl. Re-resolving a hostname here would introduce a DNS rebinding race.
    """
    if family == socket.AF_INET and len(sockaddr) >= 16:
        return ipaddress.IPv4Address(sockaddr[4:8])
    if family == socket.AF_INET6 and len(sockaddr) >= 28:
        return ipaddress.IPv6Address(sockaddr[8:24])
    raise HttpError(ErrorKind.INVALID_REQUEST, "unsupported network address family", "network-policy")


# Transition protocols can embed IPv4 destinations that are not visible in
# ipaddress.is_global on the carrier IPv6 address. Deny such routes rather
# than assuming a global-looking IPv6 literal cannot reach an internal peer.
_UNSAFE_V6_TRANSITIONS = tuple(ipaddress.IPv6Network(value) for value in (
    "2002::/16", "2001::/32", "64:ff9b::/96", "64:ff9b:1::/48",
))


def _is_public_destination(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Reject non-public network destinations selected by a native socket callback."""
    return (ip.is_global and not (isinstance(ip, ipaddress.IPv6Address) and
                                  any(ip in network for network in _UNSAFE_V6_TRANSITIONS)))


class _CurlSockAddr(C.Structure):
    # Layout of curl_sockaddr in curl/curl.h. The addr member is a sockaddr
    # prefix; libcurl provides addrlen bytes (including IPv6's longer address).
    _fields_ = [
        ("family", C.c_int), ("socktype", C.c_int), ("protocol", C.c_int),
        ("addrlen", C.c_uint), ("addr", C.c_ubyte * 16),
    ]


class _CurlVersionInfo(C.Structure):
    # Stable prefix of curl_version_info_data; later age fields are unneeded.
    _fields_ = [
        ("age", C.c_int), ("version", C.c_char_p),
        ("version_num", C.c_uint), ("host", C.c_char_p),
        ("features", C.c_int),
        ("ssl_version", C.c_char_p),
        ("ssl_version_num", C.c_long),
        ("libz_version", C.c_char_p),
        ("protocols", C.POINTER(C.c_char_p)),
    ]


class _CurlMessageData(C.Union):
    _fields_ = [("whatever", C.c_void_p), ("result", C.c_int)]


class _CurlMessage(C.Structure):
    _fields_ = [("msg", C.c_int), ("easy_handle", C.c_void_p), ("data", _CurlMessageData)]


# A production release MUST NOT silently load whatever libcurl happens to be
# on the host. libcurl 8.12.0..8.22.0 includes CVE-2026-92392 (HTTP/2 upload
# UAF), with patched maintenance lines. Require the newest fixed release as
# our supported *baseline*, rather than attempting to track every historical
# security backport. Transport options below also require modern libcurl.
_MIN_RELEASE_CURL_VERSION = 0x081700  # 8.23.0 (October 14, 2026)
_UNSUPPORTED_CURL_OVERRIDE = "VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL"


def _libcurl_version_supported(version_num: int) -> bool:
    """Conservative stable-release baseline; no unsafe minor-version aliases."""
    return isinstance(version_num, int) and version_num >= _MIN_RELEASE_CURL_VERSION


def _validate_libcurl_version(version_num: int) -> None:
    """Enforce the minimum supported secure libcurl release unless explicitly in development mode."""
    if _libcurl_version_supported(version_num):
        return
    # Only tests and *explicitly untrusted* development may opt out. The
    # release gate rejects runs with this variable even if all tests pass.
    if os.environ.get(_UNSUPPORTED_CURL_OVERRIDE) == "1":
        return
    actual = f"{version_num >> 16}.{(version_num >> 8) & 255}.{version_num & 255}"
    raise HttpError(
        ErrorKind.UNSUPPORTED,
        f"libcurl {actual} is below the supported security baseline 8.23.0; "
        f"install a verified newer libcurl and set VALIANCE_LIBCURL_PATH "
        f"to its absolute path (development-only bypass: {_UNSUPPORTED_CURL_OVERRIDE}=1)",
        "startup",
    )


_CURL_LOCK = threading.Lock()
_CURL_LIB: C.CDLL | None = None


def _curl_library_name() -> str:
    """Resolve a system curl library or an explicitly packaged absolute path.

    A bare DLL name override could be resolved through a writable search path
    on Windows. Require a real, absolute file for deployment overrides. The
    process-global library is selected once; changing the environment after
    startup does not replace a loaded transport.
    """
    explicit = os.environ.get("VALIANCE_LIBCURL_PATH")
    if explicit is not None:
        if not os.path.isabs(explicit) or not Path(explicit).is_file():
            raise HttpError(
                ErrorKind.UNSUPPORTED,
                "VALIANCE_LIBCURL_PATH must name an existing absolute library file",
                "startup",
            )
        return explicit
    name = ctypes.util.find_library("curl")
    if not name:
        raise HttpError(ErrorKind.UNSUPPORTED, "libcurl is not installed", "startup")
    return name


def _curl() -> C.CDLL:
    """Load and configure the selected native libcurl library and its function signatures."""
    global _CURL_LIB
    with _CURL_LOCK:
        if _CURL_LIB is not None:
            return _CURL_LIB
        name = _curl_library_name()
        try:
            lib = C.CDLL(name)
        except OSError as exc:
            raise HttpError(ErrorKind.UNSUPPORTED,
                            f"cannot load compatible libcurl: {exc}", "startup") from exc
        signatures = {
            "curl_global_init": (C.c_int, [C.c_long]),
            "curl_easy_init": (C.c_void_p, []),
            "curl_easy_cleanup": (None, [C.c_void_p]),
            "curl_easy_pause": (C.c_int, [C.c_void_p, C.c_int]),
            "curl_easy_strerror": (C.c_char_p, [C.c_int]),
            "curl_multi_init": (C.c_void_p, []),
            "curl_multi_cleanup": (C.c_int, [C.c_void_p]),
            "curl_multi_add_handle": (C.c_int, [C.c_void_p, C.c_void_p]),
            "curl_multi_remove_handle": (C.c_int, [C.c_void_p, C.c_void_p]),
            "curl_multi_perform": (C.c_int, [C.c_void_p, C.POINTER(C.c_int)]),
            "curl_multi_poll": (C.c_int, [C.c_void_p, C.c_void_p, C.c_uint, C.c_int, C.POINTER(C.c_int)]),
            "curl_multi_wakeup": (C.c_int, [C.c_void_p]),
            "curl_multi_info_read": (C.POINTER(_CurlMessage), [C.c_void_p, C.POINTER(C.c_int)]),
            "curl_slist_append": (C.c_void_p, [C.c_void_p, C.c_char_p]),
            "curl_slist_free_all": (None, [C.c_void_p]),
        }
        for method, (restype, argtypes) in signatures.items():
            function = getattr(lib, method)
            function.restype = restype
            function.argtypes = argtypes
        lib.curl_version_info.restype = C.POINTER(_CurlVersionInfo)
        lib.curl_version_info.argtypes = [C.c_int]
        info = lib.curl_version_info(0)
        if info:
            _validate_libcurl_version(int(info.contents.version_num))
        if (not info or info.contents.version_num < 0x074400 or
                not info.contents.features & (1 << 16) or
                not info.contents.features & (1 << 2)):
            raise HttpError(
                ErrorKind.UNSUPPORTED,
                "libcurl >= 7.68 with SSL and HTTP/2 support is required",
                "startup",
            )
        lib.curl_easy_setopt.restype = C.c_int
        lib.curl_easy_setopt.argtypes = [C.c_void_p, C.c_int]
        lib.curl_easy_getinfo.restype = C.c_int
        lib.curl_easy_getinfo.argtypes = [C.c_void_p, C.c_int]
        lib.curl_multi_setopt.restype = C.c_int
        lib.curl_multi_setopt.argtypes = [C.c_void_p, C.c_int]
        if lib.curl_global_init(3) != 0:
            raise HttpError(ErrorKind.UNSUPPORTED, "libcurl global init failed", "startup")
        _CURL_LIB = lib
        return lib


_WRITE_CALLBACK = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t, C.c_size_t, C.c_void_p)
_HEADER_CALLBACK = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t, C.c_size_t, C.c_void_p)
_READ_CALLBACK = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_size_t, C.c_size_t, C.c_void_p)
_OPEN_SOCKET_CALLBACK = C.CFUNCTYPE(C.c_size_t, C.c_void_p, C.c_int, C.POINTER(_CurlSockAddr))
_PROGRESS_CALLBACK = C.CFUNCTYPE(C.c_int, C.c_void_p, C.c_longlong, C.c_longlong,
                                 C.c_longlong, C.c_longlong)
_SOCKET_BAD = C.c_size_t(-1).value


class _ProcessBufferBudget:
    """Process-wide bound on response chunks retained by all HTTP clients.

    The bound excludes chunks already handed to callers and memory libcurl
    owns internally. The weak registry does not keep closed clients alive.
    Buffer reservations always acquire the client lock before this lock.
    """

    def __init__(self, maximum: int = 64 * 1024 * 1024) -> None:
        """Initialize the process-wide response queue capacity controller."""
        self.maximum = maximum
        self.used = 0
        self.peak = 0
        self._lock = threading.Lock()
        self._clients: weakref.WeakSet[_BufferBudget] = weakref.WeakSet()

    def register(self, client: _BufferBudget) -> None:
        """Register a client's response buffer budget with the global allocator."""
        with self._lock:
            self._clients.add(client)


_PROCESS_BUFFER_BUDGET = _ProcessBufferBudget()


class _BufferBudget:
    """Atomically reserve client-wide AND process-wide queued body bytes.

    A reservation is charged against both budgets, or against neither.
    Releasing bytes wakes only those reactors waiting for either budget, so
    another client's stalled stream cannot be permanently left suspended.
    """

    def __init__(self, maximum: int, on_release: Callable[[], None]) -> None:
        """Create one client response budget and its wakeup state."""
        self.maximum = maximum
        self._on_release = on_release
        self._lock = threading.Lock()
        self.used = 0
        self.peak = 0
        self._wake_pending = False
        self._local_waiting = False
        self._global_waiting = False
        _PROCESS_BUFFER_BUDGET.register(self)

    def reserve(self, size: int) -> bool:
        """Reserve response-queue bytes while respecting client and process-wide limits."""
        with self._lock:
            if size > self.maximum - self.used:
                self._local_waiting = True
                return False
            global_budget = _PROCESS_BUFFER_BUDGET
            with global_budget._lock:
                if size > global_budget.maximum - global_budget.used:
                    self._global_waiting = True
                    return False
                global_budget.used += size
                global_budget.peak = max(global_budget.peak, global_budget.used)
            self.used += size
            self.peak = max(self.peak, self.used)
            return True

    def _wake_if_waiting(self) -> None:
        """Wake paused transfers when previously reserved response capacity becomes available."""
        with self._lock:
            if self._wake_pending or not (self._local_waiting or self._global_waiting):
                return
            self._wake_pending = True
        # Never call a reactor while the global/client accounting lock is held.
        self._on_release()

    def release(self, size: int) -> None:
        """Return consumed or abandoned response bytes to the global and client budgets."""
        if not size:
            return
        with self._lock:
            if size > self.used:
                raise RuntimeError("HTTP client response budget underflow")
            self.used -= size
            global_budget = _PROCESS_BUFFER_BUDGET
            with global_budget._lock:
                if size > global_budget.used:
                    raise RuntimeError("HTTP process response budget underflow")
                global_budget.used -= size
                waiting_clients = tuple(global_budget._clients)
        for client in waiting_clients:
            client._wake_if_waiting()

    def mark_wake_processed(self) -> None:
        """Clear the pending budget wakeup once the reactor has processed it."""
        with self._lock:
            self._wake_pending = False
            self._local_waiting = False
            self._global_waiting = False


class ResponseBody:
    """Single-reader, manually closeable, bounded response stream."""

    def __init__(self, reactor: _Reactor, identifier: int, budget: int, shared: _BufferBudget) -> None:
        """Create a single-reader stream with a bounded queue and terminal transfer state."""
        self._reactor = reactor
        self._identifier = identifier
        self._budget = budget
        self._shared = shared
        self._lock = threading.Lock()
        self._items: deque[bytes] = deque()
        self._stored = 0
        self._pending: Future[bytes | None] | None = None
        self._terminal: BaseException | None = None
        self._eof = False
        self._closed = False
        self._paused = False
        self._budget_waiting = False

    def _push(self, chunk: bytes) -> bool:
        """Called on the reactor thread; False asks libcurl to replay the chunk."""
        pending: Future[bytes | None] | None = None
        with self._lock:
            if self._closed:
                return True
            if self._pending is not None:
                pending, self._pending = self._pending, None
            else:
                # Preserve the per-response one-chunk allowance for a callback
                # larger than the local limit. The shared budget is strict.
                local_capacity = self._stored + len(chunk) <= self._budget or not self._items
                if local_capacity and self._shared.reserve(len(chunk)):
                    self._items.append(chunk)
                    self._stored += len(chunk)
                else:
                    self._paused = True
                    self._budget_waiting = local_capacity
                    return False
        if pending is not None:
            if not pending.done():
                pending.set_result(chunk)
        return True

    def _finish(self, error: BaseException | None = None) -> None:
        """Publish EOF or a transport error, waking any awaiting body reader."""
        pending: Future[bytes | None] | None = None
        with self._lock:
            if self._closed or self._eof:
                return
            self._terminal = error
            self._eof = True
            if not self._items:
                pending, self._pending = self._pending, None
        if pending is not None:
            if not pending.done():
                if error is not None:
                    pending.set_exception(error)
                else:
                    pending.set_result(None)

    def read_async(self) -> Future[bytes | None]:
        """Get the next body chunk, None at EOF; never block a Valiance task."""
        promise: Future[bytes | None] = Future()
        should_resume = False
        released = 0
        awaiting = False
        with self._lock:
            if self._pending is not None:
                promise.set_exception(HttpError(ErrorKind.RESOURCE_LIMIT, "concurrent body reads are forbidden", "body"))
            elif self._closed:
                promise.set_exception(HttpError(ErrorKind.CLOSED, "response body is closed", "body"))
            elif self._items:
                chunk = self._items.popleft()
                self._stored -= len(chunk)
                released = len(chunk)
                promise.set_result(chunk)
                if self._paused and self._stored < self._budget:
                    self._paused = False
                    self._budget_waiting = False
                    should_resume = True
            elif self._eof:
                if self._terminal is not None:
                    promise.set_exception(self._terminal)
                else:
                    promise.set_result(None)
            else:
                self._pending = promise
                awaiting = True
        if awaiting:
            # A cancelled suspended read must abort the response, not leave
            # a cancelled Future waiting in the delivery slot. Otherwise a
            # native callback could discard the next network chunk silently.
            promise.add_done_callback(
                lambda completed: self.close() if completed.cancelled() else None)
        if released:
            self._shared.release(released)
        if should_resume:
            self._reactor._command(("resume", self._identifier, None))
        return promise

    def read(self) -> bytes | None:
        """Root-execution adapter; tasks should suspend on read_async instead."""
        return self.read_async().result()

    def close(self) -> None:
        """Idempotently close and cancel any unfinished transfer."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._items.clear()
            released, self._stored = self._stored, 0
            pending, self._pending = self._pending, None
        self._shared.release(released)
        if pending is not None:
            if not pending.done():
                pending.set_exception(HttpError(ErrorKind.CLOSED, "response body closed", "body"))
        self._reactor._command(("cancel", self._identifier, None))

    def collect_async(self, limit: int) -> Future[bytes]:
        """Consume the body without recursion, a blocked worker or unbounded reads.

        A completed read's callback can execute inline with add_done_callback;
        a trampoline prevents large numbers of immediately available chunks
        from exhausting the Python call stack. Cancelling the aggregate future
        also closes the outstanding body read and aborts its transfer.
        """
        result: Future[bytes] = Future()
        if type(limit) is not int or limit < 0:
            result.set_exception(ValueError("body limit must be a nonnegative integer"))
            return result
        chunks = bytearray()
        gate = threading.Lock()
        pumping = False
        pending_pump = False

        def consume(read: Future[bytes | None]) -> bool:
            """Accumulate a body chunk within the configured maximum complete-body size."""
            try:
                item = read.result()
                if item is None:
                    self.close()
                    if not result.done():
                        result.set_result(bytes(chunks))
                    return False
                if len(chunks) + len(item) > limit:
                    raise HttpError(ErrorKind.RESOURCE_LIMIT,
                                    "response exceeds body limit", "body")
                chunks.extend(item)
                return not result.done()
            except BaseException as exc:
                self.close()
                if not result.done():
                    result.set_exception(exc)
                return False

        def on_ready(read: Future[bytes | None]) -> None:
            """Continue consumption after an asynchronous chunk-read future completes."""
            if consume(read):
                pump()

        def pump() -> None:
            """Drain immediately available chunks without recursive callback growth."""
            nonlocal pumping, pending_pump
            with gate:
                if pumping:
                    pending_pump = True
                    return
                pumping = True
            try:
                while not result.done():
                    with gate:
                        pending_pump = False
                    read = self.read_async()
                    if read.done():
                        if not consume(read):
                            return
                        continue
                    read.add_done_callback(on_ready)
                    # The callback may have run inline just after read.done().
                    # Its work is already consumed; continue without recursing.
                    with gate:
                        repeat = pending_pump
                    if not repeat:
                        return
            finally:
                with gate:
                    pumping = False
                    repeat = pending_pump
                    pending_pump = False
                if repeat and not result.done():
                    pump()

        def on_result_done(completed: Future[bytes]) -> None:
            """Close or cancel the body stream when aggregate consumption ends."""
            if completed.cancelled():
                self.close()

        result.add_done_callback(on_result_done)
        pump()
        return result

    def __del__(self) -> None:
        # Once a completed transfer no longer retains its body, dropping the
        # final Python reference must return any unread queue reservations.
        # The reactor still holds active bodies; their lifetime is controlled
        # by explicit close, cancellation, or the transfer deadline.
        """Release unread queued bytes when the response body is finalized."""
        try:
            self.close()
        except BaseException:
            pass  # Best effort during interpreter shutdown/partial construction.

    def bytes(self, limit: int) -> bytes:
        """Read the remaining body completely using the bounded synchronous adapter."""
        if type(limit) is not int or limit < 0:
            raise ValueError("body limit must be a nonnegative integer")
        accumulated = bytearray()
        try:
            while (chunk := self.read()) is not None:
                if len(accumulated) + len(chunk) > limit:
                    raise HttpError(ErrorKind.RESOURCE_LIMIT, "response exceeds body limit", "body")
                accumulated.extend(chunk)
            return bytes(accumulated)
        finally:
            self.close()


@dataclass(frozen=True, slots=True)
class Response:
    metadata: ResponseMetadata
    body: ResponseBody

    @property
    def status(self) -> int:
        """Expose the final HTTP status from immutable response metadata."""
        return self.metadata.status


class _TransferFuture(Future[Response]):
    """Cancelling a pending send also cancels its underlying libcurl handle."""

    def __init__(self, reactor: _Reactor, identifier: int) -> None:
        """Bind a pending native transfer future to its cancellation hook."""
        super().__init__()
        self._reactor = reactor
        self._identifier = identifier
        self.submitted_at = monotonic()

    def cancel(self) -> bool:
        """Forward cancellation to the owning reactor instead of only the result future."""
        if not super().cancel():
            return False
        self._reactor._command(("cancel", self._identifier, None))
        return True


class _Transfer:
    def __init__(self, reactor: _Reactor, key: int, request: Request, future: Future[Response]) -> None:
        """Initialize one transfer's callbacks, leases, deadlines, and response metadata."""
        self.key = key
        self.request = request
        self.acquisition_started = getattr(future, "submitted_at", monotonic())
        self.connection_acquired = False
        self.visited = {request.url}
        self.redirect_count = 0
        self.redirect_to: Request | None = None
        # One real deadline covers all redirect hops and the final body.
        self.deadline = monotonic() + reactor.config.total_timeout_ms / 1000
        self.future = future
        self.easy: int = 0
        self.headers: int = 0
        self.buffer_refs: list[bytes] = []
        self.callbacks: list[Callable] = []
        self.body = ResponseBody(reactor, key, reactor.config.body_buffer_bytes, reactor._buffer_budget)
        self.header_pairs: list[tuple[str, str]] = []
        self.status = 0
        self.protocol = ""
        self.completed = False
        self.received_bytes = 0
        self.received_header_bytes = 0
        self.received_header_fields = 0
        self.last_network_read = monotonic()
        self.last_network_write = monotonic()
        self.upload_sent = 0
        self.upload_progress_started = False
        self.upload_waiting = False
        self.upload_complete = request.body is None or request.body == b""
        self.response_headers_received = False
        self.callback_error: BaseException | None = None
        self.on_finish: Callable[[], None] | None = None


def _write_idle_expired(config: ClientConfig, transfer: _Transfer, now: float) -> bool:
    """True only for a stalled upload with known progress to time against.

    Producer starvation is not socket write-idle. Streaming writers resume the
    idle clock when they provide more data, while the total request deadline
    continues to cover producer stalls.
    """
    return (config.write_idle_timeout_ms is not None and
            transfer.upload_progress_started and not transfer.upload_complete and
            not transfer.upload_waiting and not transfer.response_headers_received and
            now - transfer.last_network_write >= config.write_idle_timeout_ms / 1000)


class _Reactor:
    def __init__(self, config: ClientConfig) -> None:
        """Initialize libcurl multi state, reactor commands, and connection-policy limits."""
        self.config = config
        self.lib = _curl()
        if (config.connection_acquire_timeout_ms is not None and
                self.lib.curl_version_info(0).contents.version_num < 0x080200):
            raise HttpError(ErrorKind.UNSUPPORTED,
                            "connection acquisition deadlines need libcurl >= 8.2", "startup")
        self._cookies = CookieJar() if config.enable_cookies else None
        self.multi: int = self.lib.curl_multi_init()
        if not self.multi:
            raise HttpError(ErrorKind.UNSUPPORTED, "libcurl multi init failed", "startup")
        self._set_multi(13, config.max_connections)
        self._set_multi(7, config.max_connections_per_origin)
        self._set_multi(16, 100)
        # Bound all accepted transfers, including handles already dequeued by
        # the reactor but still waiting for sockets, DNS or response bodies.
        self._admission_slots = threading.BoundedSemaphore(config.max_pending)
        self._queue: Queue[tuple[str, int, object]] = Queue(maxsize=config.max_pending)
        self._control: SimpleQueue[tuple[str, int, object]] = SimpleQueue()
        self._buffer_budget = _BufferBudget(config.max_client_buffer_bytes, self._wake_budget)
        self._transfers: dict[int, _Transfer] = {}
        self._by_easy: dict[int, int] = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name="valiance-http-reactor", daemon=True)
        self._thread.start()

    def _wake_budget(self) -> None:
        """Notify the reactor that response buffering can resume a paused transfer."""
        self._command(("resume_budget", 0, None))

    def _set_multi(self, key: int, value: int) -> None:
        """Apply and check an option on the libcurl multi connection pool."""
        if self.lib.curl_multi_setopt(self.multi, key, C.c_long(value)) != 0:
            raise HttpError(ErrorKind.UNSUPPORTED, "required libcurl multi feature unavailable", "startup")

    def _set_easy(self, easy: int, name: str, argument: object) -> None:
        """Apply and check an option on an individual libcurl easy handle."""
        result = self.lib.curl_easy_setopt(easy, _OPTIONS[name], argument)
        if result:
            raise HttpError(ErrorKind.UNSUPPORTED, f"libcurl rejected {name}", "setup")

    def _command(self, action: tuple[str, int, object]) -> None:
        """Queue one reactor operation and wake the transport event loop."""
        operation = action[0]
        # Lifecycle commands must never be starved behind a full admission queue.
        if operation != "send":
            with self._lock:
                if self._closed:
                    return
                self._control.put(action)
                # Keep the lifetime lock until wakeup returns. Otherwise the
                # reactor may close its multi handle between the closed check
                # and this native call (a use-after-free on shutdown).
                self.lib.curl_multi_wakeup(self.multi)
        else:
            # send() already owns _lock, including through this wakeup.
            try:
                self._queue.put_nowait(action)
            except Full as exc:
                raise HttpError(ErrorKind.RESOURCE_LIMIT, "HTTP command queue full", "admission") from exc
            self.lib.curl_multi_wakeup(self.multi)

    def send(self, request: Request, on_finish: Callable[[], None] | None = None) -> Future[Response]:
        """Admit and enqueue one validated outbound request for cooperative execution."""
        closed_future: Future[Response] | None = None
        with self._lock:
            if self._closed:
                closed_future = Future()
                closed_future.set_exception(HttpError(
                    ErrorKind.CLOSED, "HTTP client is closed", "admission"))
            else:
                if not self._admission_slots.acquire(blocking=False):
                    raise HttpError(ErrorKind.RESOURCE_LIMIT,
                                    "maximum concurrent HTTP requests reached", "admission")
                self._next_id += 1
                key = self._next_id
                promise: Future[Response] = _TransferFuture(self, key)
                upload_reserved = False
                buffered_reserved = 0
                buffered_admitted = False
                original_finish = on_finish
                try:
                    if isinstance(request.body, bytes):
                        # The bytes are already owned by the caller; this lease
                        # bounds how many such values transfers can retain at once.
                        buffered_reserved = len(request.body)
                        _PROCESS_UPLOAD_BUDGET.reserve(buffered_reserved)
                        buffered_admitted = True
                    elif isinstance(request.body, (UploadPipe, MultipartUpload)):
                        request.body.reserve()
                        upload_reserved = True
                    if buffered_reserved:
                        def finish_with_budget_release() -> None:
                            """Return reserved upload capacity after a transfer reaches terminal state."""
                            _PROCESS_UPLOAD_BUDGET.release(buffered_reserved)
                            if original_finish is not None:
                                original_finish()

                        on_finish = finish_with_budget_release
                    self._command(("send", key, (request, promise, on_finish)))
                except BaseException:
                    self._admission_slots.release()
                    if upload_reserved:
                        request.body.release_transfer(HttpError(
                            ErrorKind.CLOSED, "upload admission failed", "upload"))
                    if buffered_admitted:
                        _PROCESS_UPLOAD_BUDGET.release(buffered_reserved)
                    if on_finish is not None:
                        # Admission failed before the transfer acquired its finish
                        # callback. Do not invoke a wrapper which would release a
                        # reservation twice.
                        callback = original_finish
                        if callback is not None:
                            callback()
                    raise
        if closed_future is not None:
            # A VM/client lease supplied by the caller must be released even
            # when no transfer was accepted. Invoke outside the reactor lock.
            if on_finish is not None:
                on_finish()
            return closed_future
        return promise

    def close(self) -> None:
        """Shut down command admission, active transfers, and owned native resources."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._control.put(("shutdown", 0, None))
            # Synchronized with native multi cleanup in _run.finally.
            self.lib.curl_multi_wakeup(self.multi)
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=5)

    def _connection_assigned(self, transfer: _Transfer) -> bool:
        """Observe libcurl's connection ID without treating HTTP/2 streams as sockets.

        The getter must run on the multi owner thread. A -1 connection ID
        means a transfer has not yet acquired a connection; the same socket
        can serve multiple HTTP/2 streams. This optional feature requires
        libcurl 8.2 rather than guessing using active-socket state on 7.x.
        """
        if transfer.connection_acquired or transfer.response_headers_received:
            return True
        connection_id = C.c_longlong(-1)
        code = self.lib.curl_easy_getinfo(C.c_void_p(transfer.easy),
                                          _CURLINFO_CONN_ID, C.byref(connection_id))
        if code:
            raise HttpError(ErrorKind.UNSUPPORTED,
                            "cannot inspect libcurl connection assignment", "connection-acquisition")
        transfer.connection_acquired = connection_id.value >= 0
        return transfer.connection_acquired

    def _prepare(self, transfer: _Transfer) -> None:
        """Initialize the native request handle and its upload, header, and body callbacks."""
        if self.config.require_https and urlsplit(transfer.request.url).scheme != 'https':
            raise HttpError(ErrorKind.INVALID_REQUEST, "HTTPS is required by client policy", "policy")
        if self.config.allowed_origins is not None and _origin(transfer.request.url) not in {
            _origin(origin) for origin in self.config.allowed_origins
        }:
            raise HttpError(ErrorKind.INVALID_REQUEST, "request destination is outside the allowed origins", "policy")
        buffered_bytes = (len(transfer.request.body) if isinstance(transfer.request.body, bytes)
                          else transfer.request.body.buffered_bytes
                          if isinstance(transfer.request.body, MultipartUpload) else 0)
        if buffered_bytes > self.config.max_buffered_upload_bytes:
            raise HttpError(ErrorKind.RESOURCE_LIMIT, "buffered upload exceeds configured limit", "admission")
        lib = self.lib
        easy = lib.curl_easy_init()
        if not easy:
            raise HttpError(ErrorKind.RESOURCE_LIMIT, "cannot allocate transfer", "setup")
        transfer.easy = easy

        def opt_string(name: str, value: str) -> None:
            """Store a native option string for the easy handle's lifetime."""
            encoded = value.encode("utf-8")
            transfer.buffer_refs.append(encoded)
            self._set_easy(easy, name, C.c_char_p(encoded))

        opt_string("URL", transfer.request.url)
        opt_string("CUSTOMREQUEST", transfer.request.method)
        self._set_easy(easy, "NOSIGNAL", C.c_long(1))
        self._set_easy(easy, "HTTP_VERSION", C.c_long(_CURL_HTTP_VERSION_2TLS))
        self._set_easy(easy, "CONNECTTIMEOUT_MS", C.c_long(self.config.connect_timeout_ms))
        remaining_ms = int((transfer.deadline - monotonic()) * 1000)
        if remaining_ms <= 0:
            raise HttpError(ErrorKind.TIMEOUT, "total request deadline exceeded", "transfer")
        self._set_easy(easy, "TIMEOUT_MS", C.c_long(remaining_ms))
        self._set_easy(easy, "SSL_VERIFYPEER", C.c_long(1))
        self._set_easy(easy, "SSL_VERIFYHOST", C.c_long(2))
        self._set_easy(easy, "FOLLOWLOCATION", C.c_long(0))
        if self.config.decompress:
            opt_string("ACCEPT_ENCODING", "")
        if self.config.deny_non_public_ips:
            # Environment-derived HTTP_PROXY/HTTPS_PROXY/ALL_PROXY would let a
            # remote proxy reach destinations outside this socket policy.
            # Explicit proxies are rejected during ClientConfig validation.
            opt_string("PROXY", "")

            def open_socket(_userdata: int, _purpose: int,
                            address: C.POINTER(_CurlSockAddr)) -> int:
                """Reject blocked destination IPs before allowing libcurl to open a socket."""
                try:
                    info = address.contents
                    if not 16 <= info.addrlen <= 128:
                        raise HttpError(ErrorKind.INVALID_REQUEST,
                                        "invalid destination socket address", "network-policy")
                    raw = C.string_at(C.addressof(info) + _CurlSockAddr.addr.offset,
                                      info.addrlen)
                    ip = _destination_ip(info.family, raw)
                    if not _is_public_destination(ip):
                        raise HttpError(ErrorKind.INVALID_REQUEST,
                                        "destination IP is not public", "network-policy")
                    handle = socket.socket(info.family, info.socktype, info.protocol)
                    # A rejected DNS candidate must not poison a later
                    # permitted candidate of the same transfer.
                    transfer.callback_error = None
                    return handle.detach()
                except BaseException as exc:
                    if isinstance(exc, HttpError):
                        transfer.callback_error = exc
                    else:
                        transfer.callback_error = HttpError(
                            ErrorKind.CONNECT, f"cannot open destination socket: {exc}", "connect")
                    return _SOCKET_BAD

            callback = _OPEN_SOCKET_CALLBACK(open_socket)
            transfer.callbacks.append(callback)
            self._set_easy(easy, "OPENSOCKETFUNCTION", callback)
        elif self.config.proxy is not None:
            opt_string("PROXY", self.config.proxy)
        if self.config.ca_bundle is not None:
            opt_string("CAINFO", self.config.ca_bundle)

        if transfer.request.method == "HEAD":
            self._set_easy(easy, "NOBODY", C.c_long(1))
        if transfer.request.body is not None:
            body = transfer.request.body
            if isinstance(body, (UploadPipe, MultipartUpload)):
                body.attach(lambda: self._command(("resume_upload", transfer.key, None)))
                self._set_easy(easy, "UPLOAD", C.c_long(1))
                self._set_easy(easy, "INFILESIZE_LARGE", C.c_longlong(
                    -1 if body.expected_bytes is None else body.expected_bytes))

                def on_upload(buffer: int, size: int, nmemb: int, _userdata: int) -> int:
                    """Read upload data from an owned stream, pausing when the producer has no bytes."""
                    try:
                        payload = body.read_native(size * nmemb)
                        if payload is None:
                            transfer.upload_waiting = True
                            return _READ_PAUSE
                        if payload:
                            transfer.upload_waiting = False
                            C.memmove(buffer, payload, len(payload))
                        else:
                            transfer.upload_complete = True
                        return len(payload)
                    except BaseException as exc:
                        transfer.callback_error = exc
                        return _READ_ABORT

                upload_callback = _READ_CALLBACK(on_upload)
                transfer.callbacks.append(upload_callback)
                self._set_easy(easy, "READFUNCTION", upload_callback)
            else:
                transfer.buffer_refs.append(body)
                self._set_easy(easy, "POSTFIELDSIZE_LARGE", C.c_longlong(len(body)))
                self._set_easy(easy, "COPYPOSTFIELDS", C.c_char_p(body))

        if self.config.write_idle_timeout_ms is not None and transfer.request.body is not None:
            def on_progress(_userdata: int, _download_total: int, _download_now: int,
                            upload_total: int, upload_now: int) -> int:
                """Enforce connection and transfer deadlines using monotonic network progress."""
                if upload_now > transfer.upload_sent:
                    transfer.upload_sent = upload_now
                    transfer.upload_progress_started = True
                    transfer.last_network_write = monotonic()
                expected = (transfer.request.body.expected_bytes
                            if isinstance(transfer.request.body, (UploadPipe, MultipartUpload))
                            else len(transfer.request.body))
                if expected is not None and upload_now >= expected:
                    transfer.upload_complete = True
                return 0

            callback = _PROGRESS_CALLBACK(on_progress)
            transfer.callbacks.append(callback)
            self._set_easy(easy, "XFERINFOFUNCTION", callback)
            self._set_easy(easy, "NOPROGRESS", C.c_long(0))

        headers = transfer.request.headers.pairs
        # Explicit Cookie values take precedence. Cross-origin redirect
        # handling already strips those credentials from subsequent hops.
        if self._cookies is not None and not transfer.request.headers.values('cookie'):
            cookie_header = self._cookies.outbound(transfer.request.url)
            if cookie_header:
                headers = (*headers, ('Cookie', cookie_header))
        for name, value in headers:
            header = (name + ": " + value).encode("latin-1")
            next_ptr = lib.curl_slist_append(transfer.headers, C.c_char_p(header))
            if not next_ptr:
                raise HttpError(ErrorKind.RESOURCE_LIMIT, "cannot allocate HTTP headers", "setup")
            transfer.headers = next_ptr
        if transfer.headers:
            self._set_easy(easy, "HTTPHEADER", C.c_void_p(transfer.headers))

        def on_header(data: int, size: int, nmemb: int, _userdata: int) -> int:
            """Validate response framing and headers before publishing final metadata."""
            length = size * nmemb
            try:
                transfer.received_header_bytes += length
                if transfer.received_header_bytes > self.config.max_response_header_bytes:
                    transfer.callback_error = HttpError(
                        ErrorKind.RESOURCE_LIMIT, "response headers exceed byte limit", "headers")
                    return 0
                raw = C.string_at(data, length)
                if raw.startswith(b"HTTP/"):
                    tokens = raw.decode("latin-1").split(" ", 2)
                    transfer.protocol = tokens[0].strip()
                    transfer.status = int(tokens[1])
                    transfer.header_pairs.clear()
                elif raw in (b"\r\n", b"\n"):
                    if transfer.status >= 200 and not transfer.future.done():
                        transfer.response_headers_received = True
                        transfer.last_network_read = monotonic()
                        if self._cookies is not None:
                            for header_name, cookie in transfer.header_pairs:
                                if header_name.casefold() == 'set-cookie':
                                    self._cookies.receive(transfer.request.url, cookie)
                        if (self.config.follow_redirects and
                                transfer.status in _REDIRECT_STATUSES):
                            locations = tuple(value for name, value in transfer.header_pairs
                                              if name.casefold() == "location")
                            if len(locations) > 1:
                                transfer.callback_error = HttpError(
                                    ErrorKind.PROTOCOL, "multiple redirect locations", "redirect")
                                return 0
                            if locations:
                                if transfer.redirect_count >= self.config.max_redirects:
                                    transfer.callback_error = HttpError(
                                        ErrorKind.PROTOCOL, "redirect limit exceeded", "redirect")
                                    return 0
                                try:
                                    next_request = _next_redirect(
                                        transfer.request, transfer.status, locations[0],
                                        allow_downgrade=self.config.allow_downgrade,
                                    )
                                except HttpError as exc:
                                    transfer.callback_error = exc
                                    return 0
                                if next_request.url in transfer.visited:
                                    transfer.callback_error = HttpError(
                                        ErrorKind.PROTOCOL, "redirect loop detected", "redirect")
                                    return 0
                                transfer.redirect_to = next_request
                                # Abort the old body as soon as headers complete.
                                # The CURL_WRITE_ERROR completion below advances
                                # the same logical transfer to its next hop.
                                return 0
                        meta = ResponseMetadata(
                            transfer.status, transfer.request.url,
                            Headers(tuple(transfer.header_pairs)), transfer.protocol,
                        )
                        transfer.future.set_result(Response(meta, transfer.body))
                elif b":" in raw:
                    transfer.received_header_fields += 1
                    if transfer.received_header_fields > self.config.max_response_header_fields:
                        transfer.callback_error = HttpError(
                            ErrorKind.RESOURCE_LIMIT, "response headers exceed field limit", "headers")
                        return 0
                    name, value = raw.split(b":", 1)
                    transfer.header_pairs.append((name.decode("ascii"), value.strip(b" \t\r\n").decode("latin-1")))
            except Exception as exc:
                transfer.callback_error = HttpError(ErrorKind.PROTOCOL, f"invalid response headers: {exc}", "headers")
                return 0
            return length

        def on_data(data: int, size: int, nmemb: int, _userdata: int) -> int:
            """Queue bounded response chunks or pause the transport on backpressure."""
            length = size * nmemb
            try:
                if transfer.received_bytes + length > self.config.max_body_bytes:
                    transfer.callback_error = HttpError(ErrorKind.RESOURCE_LIMIT, "decoded response exceeds configured transfer limit", "body")
                    return 0
                chunk = C.string_at(data, length)
                if transfer.body._push(chunk):
                    transfer.received_bytes += length
                    transfer.last_network_read = monotonic()
                    return length
                return _WRITE_PAUSE
            except Exception as exc:
                transfer.callback_error = HttpError(ErrorKind.TRANSFER, f"response body callback failed: {exc}", "body")
                return 0

        header_callback = _HEADER_CALLBACK(on_header)
        write_callback = _WRITE_CALLBACK(on_data)
        transfer.callbacks.extend((header_callback, write_callback))
        self._set_easy(easy, "HEADERFUNCTION", header_callback)
        self._set_easy(easy, "WRITEFUNCTION", write_callback)
        rc = lib.curl_multi_add_handle(self.multi, easy)
        if rc:
            raise HttpError(ErrorKind.TRANSFER, "cannot register libcurl transfer", "setup")
        self._transfers[transfer.key] = transfer
        self._by_easy[easy] = transfer.key

    def _remove(self, transfer: _Transfer, error: BaseException | None = None) -> None:
        """Terminate one transfer and detach its native handle from the multi reactor."""
        if transfer.completed:
            return
        transfer.completed = True
        self._transfers.pop(transfer.key, None)
        self._admission_slots.release()
        self._cleanup_easy(transfer)
        if isinstance(transfer.request.body, (UploadPipe, MultipartUpload)):
            transfer.request.body.release_transfer(error)
        if error is not None and not transfer.future.done():
            transfer.future.set_exception(error)
        elif not transfer.future.done():
            transfer.future.set_exception(HttpError(ErrorKind.PROTOCOL, "response ended without final headers", "headers"))
        transfer.body._finish(error)
        if transfer.on_finish is not None:
            transfer.on_finish()
            transfer.on_finish = None

    def _cleanup_easy(self, transfer: _Transfer) -> None:
        """Release callback references and native easy-handle allocations safely."""
        if transfer.easy:
            self._by_easy.pop(transfer.easy, None)
            self.lib.curl_multi_remove_handle(self.multi, transfer.easy)
            self.lib.curl_easy_cleanup(transfer.easy)
            transfer.easy = 0
        if transfer.headers:
            self.lib.curl_slist_free_all(transfer.headers)
            transfer.headers = 0
        # Both libcurl callback objects and source bytes must outlive the easy
        # handle, but no longer need retaining once that handle is destroyed.
        transfer.callbacks.clear()
        transfer.buffer_refs.clear()

    def _follow_redirect(self, transfer: _Transfer) -> None:
        """Validate and prepare an allowed redirect, including credential and body policy."""
        request = transfer.redirect_to
        assert request is not None
        if isinstance(transfer.request.body, (UploadPipe, MultipartUpload)):
            transfer.request.body.release_transfer()
        self._cleanup_easy(transfer)
        transfer.redirect_count += 1
        transfer.acquisition_started = monotonic()
        transfer.connection_acquired = False
        transfer.visited.add(request.url)
        transfer.request = request
        transfer.redirect_to = None
        transfer.header_pairs.clear()
        transfer.status = 0
        transfer.protocol = ""
        transfer.callback_error = None
        transfer.received_bytes = 0
        transfer.response_headers_received = False
        transfer.last_network_read = monotonic()
        transfer.last_network_write = monotonic()
        transfer.upload_sent = 0
        transfer.upload_progress_started = False
        transfer.upload_waiting = False
        transfer.upload_complete = request.body is None or request.body == b""
        try:
            self._prepare(transfer)
        except BaseException as exc:
            self._remove(transfer, exc)

    def _perform(self) -> None:
        """Drive ready libcurl sockets and translate completed native operations."""
        running = C.c_int()
        while True:
            rc = self.lib.curl_multi_perform(self.multi, C.byref(running))
            if rc == -1:  # CURLM_CALL_MULTI_PERFORM (historical)
                continue
            if rc:
                raise HttpError(ErrorKind.TRANSFER, "libcurl multi perform failed", "reactor")
            break
        while True:
            pending = C.c_int()
            message = self.lib.curl_multi_info_read(self.multi, C.byref(pending))
            if not message:
                break
            msg = message.contents
            if msg.msg != _CURLMSG_DONE:
                continue
            transfer = self._transfers.get(self._by_easy.get(msg.easy_handle, -1))
            if transfer is None:
                continue
            if transfer.redirect_to is not None:
                self._follow_redirect(transfer)
                continue
            if msg.data.result:
                if transfer.callback_error is not None:
                    self._remove(transfer, transfer.callback_error)
                    continue
                description = self.lib.curl_easy_strerror(msg.data.result).decode("utf-8", "replace")
                kind = (ErrorKind.TIMEOUT if msg.data.result == 28 else
                        ErrorKind.DNS if msg.data.result == 6 else
                        ErrorKind.CONNECT if msg.data.result == 7 else
                        ErrorKind.TLS if msg.data.result in (35, 51, 58, 60) else
                        ErrorKind.TRANSFER)
                self._remove(transfer, HttpError(kind, description, "transfer"))
            else:
                self._remove(transfer)

    def _fail_closed(self, failure: BaseException) -> None:
        """Make every accepted operation terminal after an unexpected reactor fault.

        A reactor exception must never strand a VM activation, response reader,
        or upload writer. First close admission under the same lock as send(),
        then release active transfers and queued submissions on the reactor
        thread, where libcurl easy/multi ownership remains valid.
        """
        terminal = HttpError(
            ErrorKind.TRANSFER,
            f"HTTP transport reactor failed: {type(failure).__name__}",
            "reactor",
        )
        with self._lock:
            self._closed = True
        for transfer in tuple(self._transfers.values()):
            try:
                self._remove(transfer, terminal)
            except BaseException:
                # One broken cleanup callback must not strand unrelated tasks.
                if not transfer.future.done():
                    transfer.future.set_exception(terminal)
                transfer.body._finish(terminal)
        while True:
            try:
                operation, _, detail = self._queue.get_nowait()
            except Empty:
                break
            if operation != "send":
                continue
            request, promise, on_finish = detail
            if not promise.done():
                promise.set_exception(terminal)
            self._admission_slots.release()
            if isinstance(request.body, (UploadPipe, MultipartUpload)):
                request.body.release_transfer(terminal)
            if on_finish is not None:
                on_finish()

    def _run(self) -> None:
        """Own the transport event loop until shutdown, publishing failures on exit."""
        try:
            while True:
                while True:
                    try:
                        operation, key, detail = self._control.get_nowait()
                    except Empty:
                        try:
                            operation, key, detail = self._queue.get_nowait()
                        except Empty:
                            break
                    if operation == "shutdown":
                        terminal = HttpError(ErrorKind.CANCELLED, "client shutdown", "transfer")
                        for transfer in list(self._transfers.values()):
                            self._remove(transfer, terminal)
                        # A pending admission has already acquired a client lease.
                        # Complete and release it even though no easy handle exists.
                        while True:
                            try:
                                command, _, pending = self._queue.get_nowait()
                            except Empty:
                                break
                            if command == "send":
                                request, promise, on_finish = pending
                                if not promise.done():
                                    promise.set_exception(terminal)
                                self._admission_slots.release()
                                if isinstance(request.body, (UploadPipe, MultipartUpload)):
                                    request.body.release_transfer(terminal)
                                if on_finish is not None:
                                    on_finish()
                        return
                    if operation == "send":
                        request, promise, on_finish = detail
                        if promise.cancelled():
                            self._admission_slots.release()
                            if isinstance(request.body, (UploadPipe, MultipartUpload)):
                                request.body.release_transfer(HttpError(
                                    ErrorKind.CANCELLED, "upload cancelled before admission", "upload"))
                            if on_finish is not None:
                                on_finish()
                            continue
                        transfer = _Transfer(self, key, request, promise)
                        transfer.on_finish = on_finish
                        try:
                            self._prepare(transfer)
                        except BaseException as exc:
                            self._remove(transfer, exc)
                    elif operation == "cancel":
                        transfer = self._transfers.get(key)
                        if transfer is not None:
                            self._remove(transfer, HttpError(ErrorKind.CANCELLED, "response closed", "body"))
                    elif operation == "resume":
                        transfer = self._transfers.get(key)
                        if transfer is not None:
                            transfer.last_network_read = monotonic()
                            self.lib.curl_easy_pause(transfer.easy, _CURLPAUSE_CONT)
                    elif operation == "resume_budget":
                        self._buffer_budget.mark_wake_processed()
                        for transfer in tuple(self._transfers.values()):
                            with transfer.body._lock:
                                waiting = transfer.body._paused and transfer.body._budget_waiting
                                if waiting:
                                    transfer.body._budget_waiting = False
                                    transfer.body._paused = False
                            if waiting:
                                transfer.last_network_read = monotonic()
                                self.lib.curl_easy_pause(transfer.easy, _CURLPAUSE_CONT)
                    elif operation == "resume_upload":
                        transfer = self._transfers.get(key)
                        if transfer is not None:
                            transfer.upload_waiting = False
                            transfer.last_network_write = monotonic()
                            self.lib.curl_easy_pause(transfer.easy, _CURLPAUSE_CONT)
                    elif operation == "step":
                        detail()
                self._perform()
                if (self.config.read_idle_timeout_ms is not None or
                        self.config.write_idle_timeout_ms is not None or
                        self.config.connection_acquire_timeout_ms is not None):
                    now = monotonic()
                    for transfer in tuple(self._transfers.values()):
                        if self.config.connection_acquire_timeout_ms is not None:
                            try:
                                assigned = self._connection_assigned(transfer)
                            except HttpError as exc:
                                self._remove(transfer, exc)
                                continue
                            if (not assigned and now - transfer.acquisition_started >=
                                    self.config.connection_acquire_timeout_ms / 1000):
                                self._remove(transfer, HttpError(
                                    ErrorKind.TIMEOUT, "connection acquisition deadline exceeded",
                                    "connection-acquisition"))
                                continue
                        if (self.config.read_idle_timeout_ms is not None and
                                transfer.response_headers_received):
                            with transfer.body._lock:
                                paused = transfer.body._paused
                            if not paused and now - transfer.last_network_read >= self.config.read_idle_timeout_ms / 1000:
                                self._remove(transfer, HttpError(
                                    ErrorKind.TIMEOUT, "response read-idle deadline exceeded", "read-idle"))
                                continue
                        if _write_idle_expired(self.config, transfer, now):
                            self._remove(transfer, HttpError(
                                ErrorKind.TIMEOUT, "request write-idle deadline exceeded", "write-idle"))
                polled = C.c_int()
                poll_result = self.lib.curl_multi_poll(self.multi, None, 0, 100, C.byref(polled))
                if poll_result:
                    raise HttpError(ErrorKind.TRANSFER, "libcurl multi poll failed", "reactor")
        except BaseException as failure:
            self._fail_closed(failure)
        finally:
            # No other thread may enter curl_multi_wakeup on a destroyed
            # handle, including after a fatal reactor failure.
            with self._lock:
                self._closed = True
                self.lib.curl_multi_cleanup(self.multi)


class Client:
    """Shareable reference to one bounded libcurl multi connection pool."""

    def __init__(self, config: ClientConfig | None = None) -> None:
        """Open one reusable HTTP client with its immutable configuration snapshot."""
        self.config = config if config is not None else ClientConfig()
        self._reactor = _Reactor(self.config)

    def send_async(self, request: Request, on_finish: Callable[[], None] | None = None) -> Future[Response]:
        """Submit a request to the reactor and return a cancellation-aware future."""
        return self._reactor.send(request, on_finish)

    def send(self, request: Request) -> Response:
        """Root adapter using the same transport as cooperative future consumers."""
        return self.send_async(request).result()

    def close(self) -> None:
        """Close client admission and release its owned transport resources."""
        self._reactor.close()

    def __enter__(self) -> Client:
        """Return this client for use with a context-managed lifetime."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the client when leaving a context-managed lifetime."""
        self.close()
