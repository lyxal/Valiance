"""Incremental, single-owner multipart/form-data parser.

This module deliberately only buffers one small input window, one bounded field,
and one bounded MIME header block. Uploaded octets are never spooled or replayed:
applications consume/discard each file part before asking for the next event.
The sole owner is the request's preexisting RequestBodyStream.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict

from valiance.std import _web_core as core


@dataclass(frozen=True)
class MultipartPart:
    """Ordered multipart event. A file's bytes belong to the parent UploadStream."""
    name: str
    filename: str | None = None
    content_type: str | None = None
    value: str | None = None

    @property
    def is_file(self) -> bool:
        return self.filename is not None


class UploadStream:
    """One-pass multipart iterator with one active upload at a time.

    next_part() consumes bounded text fields but returns file metadata without
    consuming file data. read_part() returns exact octets, None at part EOF.
    Clients must reach EOF or discard_part() before calling next_part().
    """

    def __init__(self, body: core.RequestBodyStream, boundary: str, *,
                 max_parts: int = 128, max_fields: int = 1000,
                 max_files: int = 100, max_field_bytes: int = 1024 * 1024,
                 max_header_bytes: int = 16384,
                 max_file_bytes: int = 256 * 1024 * 1024,
                 max_name_bytes: int = 1024, max_filename_bytes: int = 4096):
        if (any(type(v) is not int or v <= 0 for v in (
                max_parts, max_fields, max_files, max_field_bytes,
                max_header_bytes, max_file_bytes, max_name_bytes,
                max_filename_bytes))):
            raise core.WebError('BadRequest', 'Invalid multipart parser limits')
        self.body = body
        self.delimiter = b'\r\n--' + boundary.encode('ascii')
        self.first_delimiter = b'--' + boundary.encode('ascii')
        self.max_parts = max_parts
        self.max_fields = max_fields
        self.max_files = max_files
        self.max_field_bytes = max_field_bytes
        self.max_header_bytes = max_header_bytes
        self.max_file_bytes = max_file_bytes
        self.max_name_bytes = max_name_bytes
        self.max_filename_bytes = max_filename_bytes
        self._buffer = bytearray()
        self._started = False
        self._finished = False
        self._active = False
        self._active_file = False
        self._file_size = 0
        self._parts = 0
        self._fields = 0
        self._files = 0
        self.closed = False

    def _fill(self, minimum: int) -> bool:
        # Each transport read is bounded, independent of the total upload size.
        while len(self._buffer) < minimum:
            if self.body.complete:
                return False
            data = self.body.read(min(8192, max(1, minimum - len(self._buffer))))
            if data is None:
                return False
            self._buffer.extend(data)
        return True

    def _take(self, size: int) -> bytes:
        item = bytes(self._buffer[:size])
        del self._buffer[:size]
        return item

    def _finish(self) -> None:
        # Final boundary may be followed by one CRLF, nothing else. Reaching
        # request EOF also validates any HTTP chunked trailers via the owner.
        if self._fill(2) and self._buffer[:2] == b'\r\n':
            self._take(2)
        if self._buffer or self._fill(1):
            raise core.WebError('InvalidMultipart', 'Data after final multipart boundary')
        self._finished = True

    def _begin(self) -> None:
        if self._started:
            return
        self._started = True
        marker = self.first_delimiter
        if not self._fill(len(marker) + 2) or self._take(len(marker)) != marker:
            raise core.WebError('InvalidMultipart', 'Missing opening multipart boundary')
        suffix = self._take(2)
        if suffix == b'--':
            self._finish()
        elif suffix != b'\r\n':
            raise core.WebError('InvalidMultipart', 'Malformed opening multipart boundary')

    def _read_headers(self) -> tuple[dict[str, str], dict[str, str]]:
        # Reject obs-fold and excessive headers before untrusted data can
        # force large allocations. The limit counts all header block octets.
        while True:
            end = self._buffer.find(b'\r\n\r\n')
            if end >= 0:
                if end > self.max_header_bytes:
                    raise core.WebError('BodyTooLarge', 'Multipart headers exceed limit')
                block = self._take(end + 4)[:-4]
                break
            if len(self._buffer) > self.max_header_bytes + 3:
                raise core.WebError('BodyTooLarge', 'Multipart headers exceed limit')
            if not self._fill(len(self._buffer) + 1):
                raise core.WebError('InvalidMultipart', 'Truncated multipart headers')
        lines = block.split(b'\r\n')
        if len(lines) > 16 or not all(lines) or any(line[:1] in (b' ', b'\t') for line in lines):
            raise core.WebError('InvalidMultipart', 'Invalid multipart header layout')
        headers: dict[str, str] = {}
        for line in lines:
            if b':' not in line:
                raise core.WebError('InvalidMultipart', 'Invalid multipart part header')
            key, val = line.split(b':', 1)
            try:
                name = key.decode('ascii').lower()
                value = val.strip(b' \t').decode('utf-8', 'strict')
            except UnicodeError as exc:
                raise core.WebError('InvalidMultipart', 'Invalid multipart header encoding') from exc
            if not core._TOKEN.fullmatch(name) or core._CONTROL.search(value) or name in headers:
                raise core.WebError('InvalidMultipart', 'Invalid or duplicate multipart header')
            headers[name] = value
        if ('content-disposition' not in headers or
                any(name not in ('content-disposition', 'content-type', 'content-transfer-encoding')
                    for name in headers)):
            raise core.WebError('InvalidMultipart', 'Unsupported multipart part header')
        if headers.get('content-transfer-encoding', 'binary').lower() != 'binary':
            raise core.WebError('InvalidMultipart', 'Unsupported multipart transfer encoding')
        disposition = core._multipart_header_params(headers['content-disposition'], 'form-data')
        part_media = headers.get('content-type')
        if part_media is not None and not core._CONTENT_TYPE.fullmatch(part_media):
            raise core.WebError('InvalidMultipart', 'Invalid multipart part media type')
        return headers, disposition

    def _read_data(self, maximum: int) -> bytes | None:
        if type(maximum) is not int or maximum < 1 or maximum > 65536:
            raise core.WebError('BadRequest', 'Multipart read limit must be 1..65536')
        if not self._active:
            raise core.WebError('BodyUnavailable', 'No active multipart file part')
        needle = self.delimiter
        # Keep a delimiter-length tail when no full delimiter is present.
        # This bounds memory even when uploads contain gigabytes of data.
        while True:
            pos = self._buffer.find(needle)
            while pos >= 0:
                after = pos + len(needle)
                if not self._fill(after + 2):
                    raise core.WebError('InvalidMultipart', 'Truncated multipart boundary')
                if self._buffer[after:after + 2] in (b'\r\n', b'--'):
                    if pos:
                        data = self._take(min(pos, maximum))
                        self._file_size += len(data)
                        if self._file_size > self.max_file_bytes:
                            raise core.WebError('BodyTooLarge', 'Multipart file exceeds per-file limit')
                        return data
                    self._take(len(needle))
                    closing = self._take(2)
                    self._active = False
                    self._active_file = False
                    if closing == b'--':
                        self._finish()
                    return None
                pos = self._buffer.find(needle, pos + 1)
            # A prefix of a boundary might span the final bytes in _buffer.
            safe = len(self._buffer) - (len(needle) + 2)
            if safe > 0:
                data = self._take(min(safe, maximum))
                self._file_size += len(data)
                if self._file_size > self.max_file_bytes:
                    raise core.WebError('BodyTooLarge', 'Multipart file exceeds per-file limit')
                return data
            # Batch reads so large files do not degrade to one-byte reads.
            # Only the marker-length look-behind window is retained.
            target = len(self._buffer) + min(maximum, 8192)
            previous = len(self._buffer)
            self._fill(target)
            if len(self._buffer) == previous:
                raise core.WebError('InvalidMultipart', 'Missing final multipart boundary')

    def next_part(self) -> MultipartPart | None:
        if self.closed:
            raise core.WebError('BodyUnavailable', 'Multipart stream is closed')
        if self._active:
            raise core.WebError('BodyUnavailable', 'Consume or discard current upload before advancing')
        try:
            self._begin()
            if self._finished:
                self.close()
                return None
            self._parts += 1
            if self._parts > self.max_parts:
                raise core.WebError('BodyTooLarge', 'Too many multipart parts')
            headers, disposition = self._read_headers()
            core._multipart_name_limits(disposition, max_name_bytes=self.max_name_bytes,
                                        max_filename_bytes=self.max_filename_bytes)
            name = disposition['name']
            media = headers.get('content-type')
            self._active = True
            self._file_size = 0
            if 'filename' in disposition:
                self._active_file = True
                self._files += 1
                if self._files > self.max_files:
                    raise core.WebError('BodyTooLarge', 'Too many multipart files')
                return MultipartPart(name, disposition['filename'], media)
            self._fields += 1
            if self._fields > self.max_fields:
                raise core.WebError('BodyTooLarge', 'Too many multipart fields')
            if media is not None:
                matched = core._CONTENT_TYPE.fullmatch(media)
                assert matched is not None
                if matched.group(1).lower() != 'text/plain':
                    raise core.WebError('InvalidMultipart', 'Unsupported text field media type')
                if matched.group(2):
                    params = core._media_parameters(core._split_http_list(matched.group(2), ';'))
                    if (any(k != 'charset' for k in params) or
                            params.get('charset', 'utf-8').lower() != 'utf-8'):
                        raise core.WebError('InvalidMultipart', 'Unsupported text field encoding')
            pieces = bytearray()
            while (data := self._read_data(min(65536, max(1, self.max_field_bytes - len(pieces) + 1)))) is not None:
                pieces.extend(data)
                if len(pieces) > self.max_field_bytes:
                    raise core.WebError('BodyTooLarge', 'Multipart field exceeds field limit')
            try:
                value = pieces.decode('utf-8', 'strict')
            except UnicodeError as exc:
                raise core.WebError('InvalidMultipart', 'Invalid UTF-8 multipart field') from exc
            return MultipartPart(name, value=value, content_type=media)
        except BaseException:
            self.close()
            raise

    def read_part(self, maximum: int) -> bytes | None:
        if self.closed:
            raise core.WebError('BodyUnavailable', 'Multipart stream is closed')
        try:
            return self._read_data(maximum)
        except BaseException:
            self.close()
            raise

    def discard_part(self) -> None:
        if self.closed:
            raise core.WebError('BodyUnavailable', 'Multipart stream is closed')
        if not self._active:
            raise core.WebError('BodyUnavailable', 'No active multipart file')
        while self.read_part(65536) is not None:
            pass

    def save_part(self, destination: str) -> int:
        """Stream the active file to a caller-chosen new path, without replay.

        The upload filename is NEVER used as a destination. The app must pick a
        trusted destination directory; files are created exclusively with
        private permissions and removed on incomplete/invalid uploads.
        """
        import os
        if self.closed or not self._active or not self._active_file:
            raise core.WebError('BodyUnavailable', 'No active multipart upload to save')
        if self._file_size:
            raise core.WebError('BadRequest', 'Cannot save after consuming upload bytes')
        if not isinstance(destination, str) or not destination or '\x00' in destination:
            raise core.WebError('Internal', 'Invalid upload destination')
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, 'O_CLOEXEC', 0) | getattr(os, 'O_NOFOLLOW', 0)
        try:
            descriptor = os.open(destination, flags, 0o600)
        except FileExistsError as exc:
            raise core.WebError('Conflict', 'Upload destination exists') from exc
        except OSError as exc:
            raise core.WebError('Internal', 'Unable to create upload destination') from exc
        complete = False
        size = 0
        try:
            while (chunk := self.read_part(65536)) is not None:
                offset = 0
                while offset < len(chunk):
                    count = os.write(descriptor, chunk[offset:])
                    if count <= 0:
                        raise OSError('Incomplete write')
                    offset += count
                size += len(chunk)
            complete = True
            return size
        except OSError as exc:
            self.close()
            raise core.WebError('Internal', 'Unable to write uploaded file') from exc
        finally:
            original = os.fstat(descriptor)
            os.close(descriptor)
            if not complete:
                # Do not delete a different inode if the destination path was
                # replaced concurrently. The app still picks a trusted dir.
                try:
                    current = os.stat(destination, follow_symlinks=False)
                except OSError:
                    current = None
                if current is not None and (current.st_dev, current.st_ino) == (original.st_dev, original.st_ino):
                    try:
                        os.unlink(destination)
                    except OSError:
                        pass

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self.body.close()
            callback = getattr(self, '_on_close', None)
            if callback is not None:
                callback()


def multipart_stream(request: core.Request, **limits: int) -> UploadStream:
    """Claim and parse the framed body exactly once, without implicit buffering."""
    core._body_capable(request)
    if request.body_stream is None:
        raise core.WebError('WrongMode', 'Multipart streaming requires BodyMode.Stream')
    media = core.content_type(request)
    if media is None or media[0] != 'multipart/form-data':
        raise core.WebError('UnsupportedMediaType', 'Expected multipart/form-data')
    boundary = media[1].get('boundary')
    if (not boundary or not boundary.isascii() or
            not core._MULTIPART_BOUNDARY.fullmatch(boundary) or boundary.endswith(' ') or
            any(key != 'boundary' for key in media[1])):
        raise core.WebError('InvalidMultipart', 'Invalid multipart boundary')
    # Explicit core overrides may only tighten the active server/synthetic
    # policy. No helper call can bypass its request-owned quota snapshot.
    try:
        selected = {name: min(getattr(request.multipart_limits, name), value)
                    for name, value in limits.items()}
        options = {**asdict(request.multipart_limits), **selected}
        core.MultipartLimits(**options)
    except (AttributeError, TypeError, ValueError, core.ConfigError) as exc:
        raise core.WebError('BadRequest', 'Invalid multipart parser limits') from exc
    owner = request.body_stream.claim()
    try:
        return UploadStream(owner, boundary, **options)
    except BaseException:
        owner.close()
        raise
