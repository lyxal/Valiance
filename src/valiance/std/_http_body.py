"""Bounded, single-producer streaming upload pipe shared with the HTTP reactor.

The writer never runs on a reactor callback. Uploading is pull-based: libcurl
reads available chunks, pauses on an empty pipe, and is resumed through the
reactor command queue when the Valiance producer supplies more bytes or EOF.
"""

from __future__ import annotations

from collections import deque
from concurrent.futures import Future
from threading import Lock
from typing import Callable
import weakref

from valiance.std._http_errors import ErrorKind, HttpError


class _ProcessUploadBudget:
    """Cap HTTP-owned queued-upload capacity across independent clients.

    A pipe may retain up to ``capacity`` queued bytes and one pending write
    of up to ``capacity`` bytes. Reserving both slots up front bounds even
    producers whose writes have not yet been accepted by the reactor. Multipart
    MIME fragments are charged separately when their body is admitted.

    Caller-owned buffers and libcurl/native allocations are *not* included.
    The conservative reservation is a capacity bound, not a heap measurement.
    """

    def __init__(self, maximum: int = 64 * 1024 * 1024) -> None:
        self.maximum = maximum
        self.used = 0
        self.peak = 0
        self._lock = Lock()

    def reserve(self, amount: int) -> None:
        if amount < 0:
            raise ValueError("negative HTTP upload reservation")
        with self._lock:
            if amount > self.maximum - self.used:
                raise HttpError(ErrorKind.RESOURCE_LIMIT,
                                "process-wide upload capacity exceeded", "upload")
            self.used += amount
            self.peak = max(self.peak, self.used)

    def release(self, amount: int) -> None:
        with self._lock:
            if amount < 0 or amount > self.used:
                raise RuntimeError("HTTP process upload budget underflow")
            self.used -= amount


_PROCESS_UPLOAD_BUDGET = _ProcessUploadBudget()


class UploadPipe:
    """One writer, one claimed transport reader, with strictly bounded queues."""

    def __init__(self, capacity: int, expected_bytes: int | None = None) -> None:
        if type(capacity) is not int or capacity < 1:
            raise HttpError(ErrorKind.INVALID_CONFIG, "upload capacity must be positive", "upload")
        if expected_bytes is not None and (type(expected_bytes) is not int or expected_bytes < 0):
            raise HttpError(ErrorKind.INVALID_CONFIG, "expected byte count must be nonnegative", "upload")
        self.capacity = capacity
        self.expected_bytes = expected_bytes
        self._lock = Lock()
        self._chunks: deque[bytes] = deque()
        self._buffered = 0
        self._written = 0
        self._read = 0
        self._waiting: tuple[bytes, Future[int]] | None = None
        self._finished = False
        self._failure: BaseException | None = None
        self._claimed = False
        self._paused = False
        self._wake: Callable[[], None] | None = None
        self._writer_released = False
        self._source_released = False
        self._transfer_done = False
        self._finalizer: Callable[[], None] | None = None
        self._finalized = False
        # Reserve the maximum data this pipe can retain, including the single
        # pending cooperative writer. The finalizer is also a GC safety net for
        # abandoned, never-claimed Python pipes.
        reservation = capacity * 2
        _PROCESS_UPLOAD_BUDGET.reserve(reservation)
        self._budget_release = weakref.finalize(
            self, _PROCESS_UPLOAD_BUDGET.release, reservation)

    def set_finalizer(self, callback: Callable[[], None]) -> None:
        """Arrange registry cleanup when a transfer ends or both ends close."""
        with self._lock:
            self._finalizer = callback

    def _maybe_finalize(self) -> None:
        callback = None
        with self._lock:
            if (not self._finalized and
                    (self._transfer_done or
                     (not self._claimed and self._writer_released and
                      self._source_released))):
                self._finalized = True
                callback = self._finalizer
        if self._finalized:
            self._budget_release()
        if callback is not None:
            callback()

    def reserve(self) -> None:
        with self._lock:
            if self._failure is not None:
                raise self._failure
            if self._claimed or self._source_released:
                raise HttpError(ErrorKind.CLOSED, "upload source has already been consumed", "upload")
            self._claimed = True

    def attach(self, wake: Callable[[], None]) -> None:
        with self._lock:
            if not self._claimed or self._wake is not None:
                raise HttpError(ErrorKind.CLOSED, "upload source is not reserved for this transfer", "upload")
            self._wake = wake

    def write_async(self, data: bytes) -> Future[int]:
        future: Future[int] = Future()
        wake: Callable[[], None] | None = None
        with self._lock:
            if self._failure is not None:
                future.set_exception(self._failure)
            elif self._finished or self._writer_released:
                future.set_exception(HttpError(ErrorKind.CLOSED, "upload writer is closed", "upload"))
            elif self._waiting is not None:
                future.set_exception(HttpError(ErrorKind.RESOURCE_LIMIT, "concurrent upload writes are forbidden", "upload"))
            elif not data:
                future.set_result(0)
            elif len(data) > self.capacity:
                future.set_exception(HttpError(ErrorKind.RESOURCE_LIMIT, "upload chunk exceeds pipe capacity", "upload"))
            elif self.expected_bytes is not None and self._written + len(data) > self.expected_bytes:
                future.set_exception(HttpError(ErrorKind.INVALID_REQUEST, "upload exceeds declared content length", "upload"))
            elif self._buffered + len(data) <= self.capacity:
                self._chunks.append(data)
                self._buffered += len(data)
                self._written += len(data)
                future.set_result(len(data))
                if self._paused:
                    self._paused = False
                    wake = self._wake
            else:
                # One pending writer adds at most capacity more bytes to memory.
                self._waiting = (data, future)
        if wake is not None:
            wake()
        return future

    def finish(self) -> None:
        wake: Callable[[], None] | None = None
        with self._lock:
            if self._failure is not None:
                raise self._failure
            if self._finished:
                return
            if self._waiting is not None:
                raise HttpError(ErrorKind.RESOURCE_LIMIT, "finish while write is pending", "upload")
            if self.expected_bytes is not None and self._written != self.expected_bytes:
                raise HttpError(ErrorKind.INVALID_REQUEST, "upload is shorter than declared content length", "upload")
            self._finished = True
            if self._paused:
                self._paused = False
                wake = self._wake
        if wake is not None:
            wake()

    def read_native(self, maximum: int) -> bytes | None:
        """Reactor-only; None signals curl pause, b'' means producer EOF."""
        completion: tuple[Future[int], int] | None = None
        with self._lock:
            if self._failure is not None:
                raise self._failure
            if self._chunks:
                chunk = self._chunks[0]
                piece = chunk[:maximum]
                if len(piece) == len(chunk):
                    self._chunks.popleft()
                else:
                    self._chunks[0] = chunk[len(piece):]
                self._buffered -= len(piece)
                self._read += len(piece)
                if self._waiting is not None:
                    data, future = self._waiting
                    if self._buffered + len(data) <= self.capacity:
                        self._waiting = None
                        self._chunks.append(data)
                        self._buffered += len(data)
                        self._written += len(data)
                        completion = (future, len(data))
                result = piece
            elif self._finished:
                result = b''
            else:
                self._paused = True
                result = None
        if completion is not None:
            if not completion[0].done():
                completion[0].set_result(completion[1])
        return result

    def abort(self, error: BaseException | None = None) -> None:
        wake: Callable[[], None] | None = None
        pending: Future[int] | None = None
        with self._lock:
            if self._failure is not None:
                return
            self._failure = error or HttpError(ErrorKind.CLOSED, "upload was cancelled", "upload")
            self._chunks.clear()
            self._buffered = 0
            if self._waiting is not None:
                pending = self._waiting[1]
                self._waiting = None
            if self._paused:
                self._paused = False
                wake = self._wake
        if pending is not None:
            pending.set_exception(self._failure)
        if wake is not None:
            wake()

    def release_writer(self) -> None:
        with self._lock:
            self._writer_released = True
            finished = self._finished
        if not finished:
            self.abort(HttpError(ErrorKind.CLOSED, "upload writer abandoned", "upload"))
        self._maybe_finalize()

    def release_source(self) -> None:
        with self._lock:
            self._source_released = True
            claimed = self._claimed
        if not claimed:
            self.abort(HttpError(ErrorKind.CLOSED, "upload source abandoned", "upload"))
        self._maybe_finalize()

    def release_transfer(self, error: BaseException | None = None) -> None:
        with self._lock:
            self._transfer_done = True
            self._wake = None
        self.abort(error or HttpError(ErrorKind.CLOSED, "upload transfer completed", "upload"))
        self._maybe_finalize()

    @property
    def releasable(self) -> bool:
        with self._lock:
            return (self._transfer_done or
                    (not self._claimed and self._writer_released and
                     self._source_released))


class MultipartUpload:
    """One-shot multipart upload assembled lazily from byte segments and pipes.

    The reactor alone advances the segment cursor. Embedded UploadPipe writers
    remain cooperative: an empty source pauses curl, and its writer wakes the
    reactor without a worker reading from Python or Valiance code.
    """

    def __init__(self, segments: tuple[bytes | UploadPipe, ...]) -> None:
        sources = tuple(item for item in segments if isinstance(item, UploadPipe))
        if len({id(source) for source in sources}) != len(sources):
            raise HttpError(ErrorKind.INVALID_REQUEST,
                            "a multipart stream cannot use an upload source twice", "upload")
        self._segments = segments
        self.buffered_bytes = sum(len(item) for item in segments if isinstance(item, bytes))
        self._sources = sources
        self._index = 0
        self._offset = 0
        self._reserved = False
        self._attached = False
        self._released = False
        self._budget_release: weakref.finalize | None = None
        self.expected_bytes = (None if any(source.expected_bytes is None for source in sources)
                               else sum(len(item) if isinstance(item, bytes) else item.expected_bytes
                                        for item in segments))

    def reserve(self) -> None:
        if self._reserved or self._released:
            raise HttpError(ErrorKind.CLOSED, "multipart stream has already been sent", "upload")
        # Charge the generated multipart framing and buffered fields before
        # accepting any source; source-pipe queues have their own reservations.
        _PROCESS_UPLOAD_BUDGET.reserve(self.buffered_bytes)
        self._budget_release = weakref.finalize(
            self, _PROCESS_UPLOAD_BUDGET.release, self.buffered_bytes)
        claimed: list[UploadPipe] = []
        try:
            for source in self._sources:
                source.reserve()
                claimed.append(source)
        except BaseException:
            self._budget_release()
            for source in claimed:
                source.release_transfer(HttpError(ErrorKind.CLOSED,
                                                  "multipart source admission failed", "upload"))
            self._released = True
            raise
        self._reserved = True

    def attach(self, wake: Callable[[], None]) -> None:
        if not self._reserved or self._attached or self._released:
            raise HttpError(ErrorKind.CLOSED, "multipart stream is not reserved", "upload")
        self._attached = True
        for source in self._sources:
            source.attach(wake)

    def read_native(self, maximum: int) -> bytes | None:
        if maximum <= 0:
            return b''
        while self._index < len(self._segments):
            current = self._segments[self._index]
            if isinstance(current, UploadPipe):
                piece = current.read_native(maximum)
                if piece is None or piece:
                    return piece
            else:
                piece = current[self._offset:self._offset + maximum]
                self._offset += len(piece)
                if piece:
                    return piece
            self._index += 1
            self._offset = 0
        return b''

    def release_transfer(self, error: BaseException | None = None) -> None:
        if self._released:
            return
        self._released = True
        if self._budget_release is not None:
            self._budget_release()
        for source in self._sources:
            source.release_transfer(error)


StreamingUpload = UploadPipe | MultipartUpload
