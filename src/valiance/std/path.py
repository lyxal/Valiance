"""Native path operations for the Valiance Path object.

On POSIX, os.fsdecode/os.fsencode round-trip all legal filename bytes,
including non-UTF-8 names.  Only toString exposes potentially lossy text.
"""
from __future__ import annotations

import os
from typing import Any

import valiance.vtypes as T
from valiance.vtypes.symbols import Symbol
from valiance.elements.stdlib_native import stdlib_element
from valiance.elements.builtins import RuntimeContext
from valiance.runtime.runtime_values import ObjectValue
from valiance.std._filesystem_core import Path as NativePath

def native_path(obj: ObjectValue) -> NativePath:
    if not isinstance(obj, ObjectValue) or obj.type_name.split('.')[-1] != 'Path':
        raise TypeError('expected std.path.Path')
    value = obj.fields['native']
    return NativePath(value if os.name == 'nt' else os.fsencode(value))


def wrap_path(path: NativePath) -> ObjectValue:
    return ObjectValue('path.Path', {'native': os.fsdecode(path.native) if os.name != 'nt' else path.native})


_PATH = T.N(Symbol('Path', ('path',)))
_OPTIONAL_PATH = T.optional(_PATH)


def _optional_path(value: NativePath | None) -> ObjectValue:
    if value is None:
        return ObjectValue('None', {})
    return ObjectValue('Some', {'value': wrap_path(value)})


@stdlib_element('isAbsolute', (), (T.Boolean,), owner='Path', exported=False)
def _is_absolute(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (native_path(args[0]).is_absolute(),)


@stdlib_element(
    'toString', (), (T.String,), owner='Path', exported=False,
    warning='Path to String may lose non-Unicode filename data; use Path for filesystem operations',
)
def _to_string(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (native_path(args[0]).to_string(),)


@stdlib_element('join', (_PATH, _PATH), (_PATH,), param_names=('root', 'child'))
def _join_paths(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (wrap_path(native_path(args[0]).join(native_path(args[1]))),)


@stdlib_element('parent', (_PATH,), (_OPTIONAL_PATH,), param_names=('path',))
def _parent_path(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_optional_path(native_path(args[0]).parent()),)


@stdlib_element('name', (_PATH,), (_OPTIONAL_PATH,), param_names=('path',))
def _name_path(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_optional_path(native_path(args[0]).name()),)


@stdlib_element('extension', (_PATH,), (_OPTIONAL_PATH,), param_names=('path',))
def _extension_path(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_optional_path(native_path(args[0]).extension()),)

from dataclasses import replace
from valiance.elements.documentation import element_documentation

_PATH_DOCS = {
    'isAbsolute': 'Test whether the native path is absolute.',
    'toString': 'Convert the native path to a possibly lossy String.',
    'join': 'Join native Path objects without losing filename bytes.',
    'parent': 'Retrieve the lexical parent, or None if none exists.',
    'name': 'Retrieve the last native path component, or None if absent.',
    'extension': 'Retrieve the native extension as Path, or None if absent.',
}
for _fn in tuple(globals().values()):
    _definition = getattr(_fn, '__valiance_native_function__', None)
    if _definition is not None and _definition.name.text in _PATH_DOCS:
        _fn.__valiance_native_function__ = replace(
            _definition,
            documentation=element_documentation(_PATH_DOCS[_definition.name.text], category='Paths'),
        )
