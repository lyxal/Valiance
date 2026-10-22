"""Native backend for std.file's Valiance-defined RAII resources.

The public ReadFile/WriteFile/AppendFile are Valiance objects owning numeric,
unforgeable-in-practice process-local handles. Destructors invoke nativeClose;
all native operations reject closed or invalid handles. No implicit fsync.
"""
from __future__ import annotations

from itertools import count
from threading import RLock
from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.stdlib_native import stdlib_element
from valiance.runtime.runtime_values import ListValue, ObjectValue, RuntimeNumber, TaggedValue, unwrap_runtime_value
from valiance.std._filesystem_core import Encoding, File
from valiance.std._filesystem_errors import filesystem_operation, filesystem_effects
from valiance.std.path import native_path

_BYTE_TAGS = frozenset({T.DataTag("byte", 1)})
_BYTE_LIST = T.Tagged(T.AtLeastList(T.Int), *tuple(_BYTE_TAGS))

_handles: dict[int, File] = {}
_handle_ids = count(1)
_handle_lock = RLock()


def _encoding(value: Any) -> Encoding:
    """Validate a language Encoding enum and return its native encoding selection."""
    if not isinstance(value, ObjectValue) or value.type_name.split('.')[-1] != 'Encoding':
        raise TypeError('expected file.Encoding')
    key = value.fields.get('name')
    names = {'UTF8': Encoding.UTF8, 'UTF16LE': Encoding.UTF16LE,
             'UTF16BE': Encoding.UTF16BE, 'Latin1': Encoding.LATIN1}
    try:
        return names[key]
    except (KeyError, TypeError) as exc:
        raise ValueError(f'unsupported file encoding: {key!r}') from exc


def _handle(value: Any) -> File:
    """Resolve a numeric resource handle, rejecting closed or invalid handles."""
    key = int(value)
    with _handle_lock:
        handle = _handles.get(key)
    if handle is None:
        raise ValueError('file handle is closed or invalid')
    return handle


def _open(args: tuple[Any, ...], mode: str) -> tuple[Any, ...]:
    """Open a native file and register a process-local numeric handle for its owner."""
    path, encoding = args
    handle = File(native_path(path), mode, _encoding(encoding))
    with _handle_lock:
        key = next(_handle_ids)
        _handles[key] = handle
    return (RuntimeNumber(key),)


@stdlib_element('nativeOpenRead', (T.V('PathArg'), T.V('EncodingArg')), (T.Int,),
                param_names=('path', 'encoding'))
def _open_read(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Open a read-only native resource using the supplied Path and Encoding."""
    del ctx
    return _open(args, 'rb')


@stdlib_element('nativeOpenWrite', (T.V('PathArg'), T.V('EncodingArg')), (T.Int,),
                param_names=('path', 'encoding'))
def _open_write(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create or truncate a native resource using the supplied Path and Encoding."""
    del ctx
    return _open(args, 'wb')


@stdlib_element('nativeOpenAppend', (T.V('PathArg'), T.V('EncodingArg')), (T.Int,),
                param_names=('path', 'encoding'))
def _open_append(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Open a native append resource using the supplied Path and Encoding."""
    del ctx
    return _open(args, 'ab')


@stdlib_element('nativeClose', (T.Int,), (), param_names=('handle',))
def _close(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Remove and close a registered resource if it is still open."""
    del ctx
    with _handle_lock:
        handle = _handles.pop(int(args[0]), None)
    if handle is not None:
        handle.close()
    return ()


@stdlib_element('nativeFlush', (T.Int,), (), param_names=('handle',))
def _flush(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Flush the buffer of a live writable resource without fsync."""
    del ctx
    _handle(args[0]).flush()
    return ()


@stdlib_element('nativeReadLine', (T.Int,), (T.optional(T.String),), param_names=('handle',))
def _read_line(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return the next decoded line as Some, or None at end of file."""
    del ctx
    line = _handle(args[0]).read_line()
    return (ObjectValue('None', {}) if line is None else
            ObjectValue('Some', {'value': line}),)


@stdlib_element('nativeReadLines', (T.Int,), (T.ExactList(T.String),), param_names=('handle',))
def _read_lines(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return all remaining decoded lines as a rank-one language list."""
    del ctx
    return (ListValue(_handle(args[0]).read_lines(), runtime_rank=1),)


@stdlib_element('nativeReadText', (T.Int,), (T.String,), param_names=('handle',))
def _read_text(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return all remaining decoded text from a live resource."""
    del ctx
    return (_handle(args[0]).read_text(),)


@stdlib_element('nativeReadBytes', (T.Int, T.Int), (_BYTE_LIST,),
                param_names=('handle', 'count'))
def _read_bytes(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Read exactly the requested byte count and return a byte-tagged language list."""
    del ctx
    return (TaggedValue(ListValue([RuntimeNumber(b) for b in _handle(args[0]).read_bytes(int(args[1]))], runtime_rank=1), _BYTE_TAGS),)


@stdlib_element('nativeReadAllBytes', (T.Int,), (_BYTE_LIST,), param_names=('handle',))
def _read_all_bytes(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return all remaining raw bytes as a byte-tagged language list."""
    del ctx
    return (TaggedValue(ListValue([RuntimeNumber(b) for b in _handle(args[0]).read_all_bytes()], runtime_rank=1), _BYTE_TAGS),)


@stdlib_element('nativeWriteText', (T.Int, T.String), (), param_names=('handle','text'))
def _write_text(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Write text to a live resource using its selected encoding."""
    del ctx
    _handle(args[0]).write_text(args[1])
    return ()


@stdlib_element('nativeWriteLine', (T.Int, T.String), (), param_names=('handle','line'))
def _write_line(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Write text and one encoded LF to a live resource."""
    del ctx
    _handle(args[0]).write_line(args[1])
    return ()


@stdlib_element('nativeWriteLines', (T.Int, T.AtLeastList(T.String)), (),
                param_names=('handle','lines'))
def _write_lines(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Write each language-list line with one encoded LF terminator."""
    del ctx
    _handle(args[0]).write_lines(list(args[1]))
    return ()


@stdlib_element('nativeWriteBytes', (T.Int, _BYTE_LIST), (),
                param_names=('handle','bytes'))
def _write_bytes(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Validate byte-tagged values are in range before writing their raw bytes."""
    del ctx
    values = [int(item) for item in unwrap_runtime_value(args[1])]
    if any(not 0 <= item <= 255 for item in values):
        raise ValueError('byte values must be between 0 and 255')
    _handle(args[0]).write_bytes(values)
    return ()

# All native exports participate in the strict stdlib documentation catalogue.
from dataclasses import replace
from valiance.elements.documentation import element_documentation

_FILE_DOCS = {
    'nativeOpenRead': 'Open a sequential native read handle for a Path.',
    'nativeOpenWrite': 'Create or truncate a sequential native write handle.',
    'nativeOpenAppend': 'Open a sequential native append handle.',
    'nativeClose': 'Flush and close a native file handle if still open.',
    'nativeFlush': 'Flush the language runtime buffer without fsync.',
    'nativeReadLine': 'Read a line, returning None on end of file.',
    'nativeReadLines': 'Read all remaining lines.',
    'nativeReadText': 'Read all remaining decoded text.',
    'nativeReadBytes': 'Read exactly the requested number of bytes.',
    'nativeReadAllBytes': 'Read all remaining raw bytes.',
    'nativeWriteText': 'Write text using the selected encoding.',
    'nativeWriteLine': 'Write text followed by a single LF newline.',
    'nativeWriteLines': 'Write lines with LF delimiters.',
    'nativeWriteBytes': 'Write bytes in the inclusive range 0 to 255.',
}
for _fn in tuple(globals().values()):
    _definition = getattr(_fn, '__valiance_native_function__', None)
    if _definition is not None and _definition.name.text in _FILE_DOCS:
        _fn.__valiance_native_function__ = replace(
            _definition,
            implementation=filesystem_operation(_definition.implementation),
            element_tags=frozenset(filesystem_effects(_definition.name.text)),
            documentation=element_documentation(_FILE_DOCS[_definition.name.text], category='Files'),
        )
