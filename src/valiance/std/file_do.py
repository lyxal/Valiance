"""Native one-shot file operations exposed as ``file.do`` in Valiance.

These operations open a path for a single action, then close it even if an
operation raises. Native paths remain opaque throughout.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import valiance.vtypes as T
from valiance.elements.documentation import element_documentation
from valiance.elements.stdlib_native import stdlib_element
from valiance.runtime.runtime_values import ListValue, RuntimeNumber, TaggedValue, unwrap_runtime_value
from valiance.std._filesystem_core import Encoding, File
from valiance.std._filesystem_errors import filesystem_operation, filesystem_effects
from valiance.std.file import _encoding, _BYTE_LIST, _BYTE_TAGS
from valiance.std.path import native_path
from valiance.vtypes.symbols import Symbol
from valiance.asts import ElementNode

# One-shot path helpers. These use the same backend as the stateful resources
# but have independent lifetimes; the resource is closed even on failure.
# Their nominal Path first argument distinguishes them from handle methods.

_PATH = T.N(Symbol('Path', ('path',)))
_ENCODING = T.N(Symbol('Encoding', ('file',)))
_DEFAULT_ENCODING = (ElementNode(Symbol('nativeDefaultEncoding', ('std', 'file_do'))),)

# A default argument must be a genuine typed Encoding enum value, even when
# std.file.do is imported directly (without importing std.file separately).
@stdlib_element('nativeDefaultEncoding', (), (_ENCODING,), exported=False)
def _default_encoding(args, ctx):
    """Construct the UTF-8 language enum without requiring a separate file import."""
    del args, ctx
    from valiance.runtime.runtime_values import ObjectValue
    return (ObjectValue('file.Encoding', {'name': 'UTF8'}),)



def _unborrow(value: Any) -> Any:
    """Unwrap borrowed call arguments before accessing native paths or encodings."""
    while type(value).__name__ == '_BorrowedValue':
        value = value.value
    return value


def _quick_read(args: tuple[Any, ...], operation: str) -> tuple[Any, ...]:
    """Read text, lines, or raw bytes and close the temporary resource even on failure."""
    args = tuple(_unborrow(arg) for arg in args)
    path = native_path(args[0])
    encoding = _encoding(args[1]) if len(args) > 1 else Encoding.UTF8
    with File(path, 'rb', encoding) as handle:
        if operation == 'text':
            return (handle.read_text(),)
        if operation == 'lines':
            return (ListValue(handle.read_lines(), runtime_rank=1),)
        return (TaggedValue(ListValue([RuntimeNumber(b) for b in handle.read_all_bytes()], runtime_rank=1), _BYTE_TAGS),)


def _quick_write(args: tuple[Any, ...], operation: str, mode: str) -> tuple[Any, ...]:
    """Write or append text, lines, or validated bytes with deterministic resource cleanup."""
    args = tuple(_unborrow(arg) for arg in args)
    path = native_path(args[0])
    encoding = _encoding(args[2]) if len(args) > 2 else Encoding.UTF8
    with File(path, mode, encoding) as handle:
        if operation == 'text':
            handle.write_text(args[1])
        elif operation == 'lines':
            handle.write_lines(list(args[1]))
        else:
            values = [int(item) for item in unwrap_runtime_value(args[1])]
            if any(not 0 <= value <= 255 for value in values):
                raise ValueError('byte values must be between 0 and 255')
            handle.write_bytes(values)
    return ()


@stdlib_element('readText', (_PATH, _ENCODING), (T.String,), param_names=('path', 'encoding'),
                param_defaults=(None, _DEFAULT_ENCODING))
def _quick_read_text(args, ctx):
    """Read an entire file as decoded text and close its temporary resource."""
    del ctx
    return _quick_read(args, 'text')


@stdlib_element('readLines', (_PATH, _ENCODING), (T.AtLeastList(T.String),), param_names=('path', 'encoding'),
                param_defaults=(None, _DEFAULT_ENCODING))
def _quick_read_lines(args, ctx):
    """Read an entire file into decoded lines and close its temporary resource."""
    del ctx
    return _quick_read(args, 'lines')


@stdlib_element('readBytes', (_PATH,), (_BYTE_LIST,), param_names=('path',))
def _quick_read_bytes(args, ctx):
    """Read an entire file into a byte-tagged list and close its temporary resource."""
    del ctx
    return _quick_read(args, 'allBytes')


@stdlib_element('writeText', (_PATH, T.String, _ENCODING), (),
                param_names=('path', 'text', 'encoding'),
                param_defaults=(None, None, _DEFAULT_ENCODING))
def _quick_write_text(args, ctx):
    """Replace file contents with encoded text and close the temporary resource."""
    del ctx
    return _quick_write(args, 'text', 'wb')


@stdlib_element('writeLines', (_PATH, T.AtLeastList(T.String), _ENCODING), (),
                param_names=('path', 'lines', 'encoding'),
                param_defaults=(None, None, _DEFAULT_ENCODING))
def _quick_write_lines(args, ctx):
    """Replace file contents with LF-delimited lines and close the temporary resource."""
    del ctx
    return _quick_write(args, 'lines', 'wb')


@stdlib_element('writeBytes', (_PATH, _BYTE_LIST), (), param_names=('path','bytes'))
def _quick_write_bytes(args, ctx):
    """Replace file contents with validated raw bytes and close the temporary resource."""
    del ctx
    return _quick_write(args, 'bytes', 'wb')


@stdlib_element('appendText', (_PATH, T.String, _ENCODING), (),
                param_names=('path', 'text', 'encoding'),
                param_defaults=(None, None, _DEFAULT_ENCODING))
def _quick_append_text(args, ctx):
    """Append encoded text and close the temporary resource."""
    del ctx
    return _quick_write(args, 'text', 'ab')


@stdlib_element('appendLines', (_PATH, T.AtLeastList(T.String), _ENCODING), (),
                param_names=('path', 'lines', 'encoding'),
                param_defaults=(None, None, _DEFAULT_ENCODING))
def _quick_append_lines(args, ctx):
    """Append LF-delimited lines and close the temporary resource."""
    del ctx
    return _quick_write(args, 'lines', 'ab')


@stdlib_element('appendBytes', (_PATH, _BYTE_LIST), (), param_names=('path','bytes'))
def _quick_append_bytes(args, ctx):
    """Append validated raw bytes and close the temporary resource."""
    del ctx
    return _quick_write(args, 'bytes', 'ab')


_DO_DOCS = {
    'nativeDefaultEncoding': 'Get the default UTF-8 encoding for one-shot file helpers.',
    'readText': 'Read an entire file using the selected encoding and close it.',
    'readLines': 'Read all lines using the selected encoding and close the file.',
    'readBytes': 'Read all bytes from a file and close it.',
    'writeText': 'Create or replace a file with text in the selected encoding.',
    'writeLines': 'Create or replace a file with LF-delimited lines using the selected encoding.',
    'writeBytes': 'Create or replace a file with raw bytes and close it.',
    'appendText': 'Append text using the selected encoding and close the file.',
    'appendLines': 'Append LF-delimited lines using the selected encoding.',
    'appendBytes': 'Append raw bytes to a file and close it.',
}
for _fn in tuple(globals().values()):
    _definition = getattr(_fn, '__valiance_native_function__', None)
    if _definition is not None and _definition.name.text in _DO_DOCS:
        _fn.__valiance_native_function__ = replace(
            _definition,
            implementation=filesystem_operation(_definition.implementation),
            element_tags=(frozenset() if _definition.name.text == 'nativeDefaultEncoding'
                          else frozenset(filesystem_effects(_definition.name.text))),
            documentation=element_documentation(_DO_DOCS[_definition.name.text], category='Files'),
        )
