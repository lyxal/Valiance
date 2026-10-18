"""Lossless native filesystem primitives shared by Valiance path/file modules.

The language-facing Path, Encoding and @nodup types live in the Valiance
standard library; this module owns native operating-system I/O only.
"""
from __future__ import annotations

import codecs
import os
from dataclasses import dataclass
from enum import Enum
from typing import BinaryIO


class Encoding(Enum):
    UTF8 = 'utf-8'
    UTF16LE = 'utf-16-le'
    UTF16BE = 'utf-16-be'
    LATIN1 = 'latin-1'


@dataclass(frozen=True)
class Path:
    """Preserve the OS-native path representation, including invalid Unicode."""
    native: bytes | str

    def __post_init__(self) -> None:
        if not isinstance(self.native, (bytes, str)):
            raise TypeError('path must be native bytes or str')
        if os.name == 'nt' and isinstance(self.native, bytes):
            raise TypeError('Windows paths must preserve native wide strings')
        if (b'\x00' if isinstance(self.native, bytes) else '\x00') in self.native:
            raise ValueError('NUL is not permitted in a filesystem path')

    def join(self, other: 'Path') -> 'Path':
        if type(self.native) is not type(other.native):
            if isinstance(self.native, bytes):
                return Path(os.path.join(self.native, os.fsencode(other.native)))
            return Path(os.path.join(self.native, os.fsdecode(other.native)))
        return Path(os.path.join(self.native, other.native))

    def _component_spelling(self) -> bytes | str:
        """Ignore trailing separators for inspection; never mutate the path itself."""
        separators = os.sep + (os.altsep or '')
        if isinstance(self.native, bytes):
            separators = os.fsencode(separators)
        return self.native.rstrip(separators) or self.native

    def _is_root(self) -> bool:
        # In particular, Windows drive roots must not become a bogus "C:"
        # filename when their trailing separator is removed for inspection.
        return bool(self.native) and os.path.isabs(self.native) and os.path.dirname(self.native) == self.native

    def parent(self) -> 'Path | None':
        if self._is_root():
            return None
        spelling = self._component_spelling()
        value = os.path.dirname(spelling)
        return Path(value) if value and value != spelling else None

    def name(self) -> 'Path | None':
        if self._is_root():
            return None
        value = os.path.basename(self._component_spelling())
        return Path(value) if value else None

    def extension(self) -> 'Path | None':
        name = self.name()
        if name is None:
            return None
        value = os.path.splitext(name.native)[1]
        return Path(value) if value else None

    def is_absolute(self) -> bool:
        return os.path.isabs(self.native)

    def to_string(self) -> str:
        # Replacement is intentionally lossy; the Valiance facade must warn.
        if isinstance(self.native, bytes):
            return self.native.decode('utf-8', errors='replace')
        return self.native.encode('utf-8', errors='replace').decode('utf-8')


def _encode(text: str, encoding: Encoding) -> bytes:
    return text.encode(encoding.value, errors='strict')


def _decode(data: bytes, encoding: Encoding, *, first: bool) -> str:
    if first and encoding is Encoding.UTF16LE:
        if data.startswith(b'\xfe\xff'):
            raise UnicodeError('contradictory UTF-16 BOM')
        data = data.removeprefix(b'\xff\xfe')
    elif first and encoding is Encoding.UTF16BE:
        if data.startswith(b'\xff\xfe'):
            raise UnicodeError('contradictory UTF-16 BOM')
        data = data.removeprefix(b'\xfe\xff')
    return data.decode(encoding.value, errors='strict')


class File:
    """Sequential binary handle with explicit encoding and deterministic close.

    The Valiance wrapper owns this instance and calls close from its destructor.
    """
    def __init__(self, path: Path, mode: str, encoding: Encoding = Encoding.UTF8):
        if mode not in ('rb', 'wb', 'ab'):
            raise ValueError('invalid file mode')
        self._stream: BinaryIO = open(path.native, mode)
        self.mode = mode
        self.encoding = encoding
        self._decoder = codecs.getincrementaldecoder(encoding.value)(errors='strict')
        self._pending_text = ''
        self._first_text = True
        self._eof = False
        self._closed = False

    def _readable(self) -> None:
        if self.mode != 'rb':
            raise ValueError('not a readable file')

    def _writable(self) -> None:
        if self.mode == 'rb':
            raise ValueError('not a writable file')

    def read_bytes(self, count: int) -> bytes:
        self._readable()
        if count < 0:
            raise ValueError('byte count cannot be negative')
        # Do not silently mix buffered text decoding with raw byte reads.
        if self._pending_text or not self._first_text:
            raise ValueError('cannot switch from text reads to byte reads')
        data = self._stream.read(count)
        if len(data) != count:
            raise EOFError(f'expected {count} bytes, received {len(data)}')
        return data

    def read_all_bytes(self) -> bytes:
        self._readable()
        if self._pending_text or not self._first_text:
            raise ValueError('cannot switch from text reads to byte reads')
        return self._stream.read()

    def read_text(self) -> str:
        self._readable()
        data = self._stream.read()
        text = self._decode_chunk(data, final=True)
        result = self._pending_text + text
        self._pending_text = ''
        return result

    def _decode_chunk(self, data: bytes, *, final: bool) -> str:
        if self._first_text:
            if self.encoding is Encoding.UTF16LE:
                if data.startswith(b'\xfe\xff'):
                    raise UnicodeError('contradictory UTF-16 BOM')
                data = data.removeprefix(b'\xff\xfe')
            elif self.encoding is Encoding.UTF16BE:
                if data.startswith(b'\xff\xfe'):
                    raise UnicodeError('contradictory UTF-16 BOM')
                data = data.removeprefix(b'\xfe\xff')
            self._first_text = False
        return self._decoder.decode(data, final=final)

    def read_line(self) -> str | None:
        self._readable()
        while True:
            text = self._pending_text
            for index, ch in enumerate(text):
                if ch == '\n':
                    self._pending_text = text[index + 1:]
                    return text[:index].removesuffix('\r')
                if ch == '\r' and (index + 1 < len(text) or self._eof):
                    width = 2 if text[index + 1:index + 2] == '\n' else 1
                    self._pending_text = text[index + width:]
                    return text[:index]
            if self._eof:
                self._pending_text = ''
                return text if text else None
            chunk = self._stream.read(4096)
            if chunk:
                self._pending_text += self._decode_chunk(chunk, final=False)
            else:
                self._pending_text += self._decode_chunk(b'', final=True)
                self._eof = True

    def read_lines(self) -> list[str]:
        result = []
        while (line := self.read_line()) is not None:
            result.append(line)
        return result

    def write_bytes(self, values: bytes | list[int]) -> None:
        self._writable()
        self._stream.write(bytes(values))

    def write_text(self, text: str) -> None:
        self.write_bytes(_encode(text, self.encoding))

    def write_line(self, text: str) -> None:
        self.write_text(text + '\n')

    def write_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.write_line(line)

    def flush(self) -> None:
        self._writable()
        self._stream.flush()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._stream.close()

    def __enter__(self) -> 'File':
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()
