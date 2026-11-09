"""Shareable, process-local, atomic immutable-value State[T].

All operations are Valiance functions; protected operations are handed to the
resumable VM rather than invoking callbacks through synchronous native calls.
"""
from __future__ import annotations

from typing import Any
import valiance.vtypes as T
from valiance.vtypes.symbols import Symbol
from valiance.elements.stdlib_native import stdlib_element
from valiance.elements.documentation import element_documentation
from valiance.elements.builtins import RuntimeContext
from valiance.runtime.protected import GuardedValue, ProtectedOperation
from valiance.runtime.transfer import require_shared_value

_ITEM = T.V('T')
_RESULT = T.V('R')
_STATE = T.N(Symbol('State'), _ITEM)


def _own(value: Any) -> Any:
    """Retain an independent language-owned occurrence for an operation."""
    # Native inputs are borrowed by the stdlib wrapper and released after the
    # call; the cell or operation needs an independent language occurrence.
    from valiance.runtime.vm import _duplicate_occurrence
    return _duplicate_occurrence(value)


def _request(kind: str, values: tuple[Any, ...], ctx: RuntimeContext | None = None) -> tuple[Any, ...]:
    """Retain the cell and return a deferred, VM-mediated operation request."""
    state = values[0]
    if not isinstance(state, GuardedValue):
        raise TypeError(f'{kind} requires State[T]')
    if state.destroyed:
        raise RuntimeError('State has been released')
    payload = None
    if len(values) > 1:
        payload = _own(values[1])
    try:
        state.retain_handle()
    except BaseException:
        if payload is not None and ctx is not None:
            from valiance.runtime.vm import _release_value
            # The operation did not take ownership of the duplicated payload.
            vm = getattr(ctx.call, '__self__', None)
            if vm is not None:
                _release_value(payload, vm)
        raise
    return (ProtectedOperation(kind, state, payload),)


@stdlib_element('State', (_ITEM,), (_STATE,), param_names=('initial',),
    documentation=element_documentation(
        "Create a shareable process-local State[T] without a background task.",
        parameters=(("initial", "Initial owned, duplicable, transferable T."),),
        returns="A shared State[T] handle.", category="Concurrency",
        examples=(('import {std.concurrency}\n$s = concurrency.State(3)\nget $s', '3'),),
    ))
def _state(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Construct a cell without spawning a background owner task."""
    require_shared_value(args[0], path='State initial value')
    return (GuardedValue(_own(args[0])),)


@stdlib_element('get', (_STATE,), (_ITEM,), param_names=('state',),
    documentation=element_documentation(
        "Read an immutable logical snapshot of the committed State[T].",
        parameters=(("state", "Shared cell handle."),),
        returns="Snapshot T.", category="Concurrency",
        examples=(('import {std.concurrency}\n$s = concurrency.State(3)\nconcurrency.get($s)', '3'),),
    ))
def _get(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Request a linearizable immutable snapshot."""
    return _request('get', args, ctx)


@stdlib_element('set', (_STATE, _ITEM), (), param_names=('state', 'replacement'),
    documentation=element_documentation(
        "Atomically publish a precomputed replacement for State[T].",
        parameters=(("state", "Shared cell handle."),
                    ("replacement", "New committed T.")),
        returns="No values.", category="Concurrency",
        examples=(('import {std.concurrency}\n$s = concurrency.State(2)\nconcurrency.set($s, 9)\nget $s', '9'),),
    ))
def _set(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Request an atomic replacement without reading previous state."""
    require_shared_value(args[1], path='State replacement')
    return _request('set', args, ctx)


@stdlib_element('modify', (_STATE, T.Fn((_ITEM,), (_ITEM,))), (), param_names=('state', 'transformation'),
    documentation=element_documentation(
        "Atomically modify State[T] using a non-waiting resumable callback.",
        parameters=(("state", "Shared cell handle."),
                    ("transformation", "Function from T to replacement T.")),
        returns="No values.", category="Concurrency",
        examples=(('import {std.concurrency}\n$s = concurrency.State(2)\nconcurrency.modify($s, fn(x:Int)->Int => $x 1 + end)\nget $s', '3'),),
    ))
def _update(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Request a resumable protected T -> T transformation."""
    return _request('update', args, ctx)


@stdlib_element('modifyWith', (_STATE, T.Fn((_ITEM,), (_ITEM, _RESULT))), (_RESULT,), param_names=('state', 'transformation'),
    documentation=element_documentation(
        "Atomically commit a replacement T and return a separate result R.",
        parameters=(("state", "Shared cell handle."),
                    ("transformation", "Function from T to two stack outputs: T, R.")),
        returns="The callback result R.", category="Concurrency",
        examples=(('import {std.concurrency}\n$s = concurrency.State(4)\nconcurrency.modifyWith($s, fn(x:Int)->Int,Int => ($x 1 +) $x end)\nget $s', '4 5'),),
    ))
def _update_with(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Request a protected T -> (T, R) transformation; return R separately."""
    return _request('updateWith', args, ctx)
