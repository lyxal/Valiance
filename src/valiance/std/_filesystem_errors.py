"""Translate native filesystem failures into catchable Valiance Fault panics.

Only native filesystem operations use this adapter. Other native-library
exceptions retain their existing behaviour.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Callable

from valiance.runtime.runtime_values import ObjectValue, PanicSignal
import valiance.vtypes as T
from valiance.vtypes.symbols import Symbol


def filesystem_operation(function: Callable[..., Any]) -> Callable[..., Any]:
    """Preserve the Fault panic protocol across the Python/native boundary."""
    def fault(kind: str, exc: Exception) -> None:
        raise PanicSignal(ObjectValue(kind, {"message": str(exc)})) from exc

    @wraps(function)
    def invoke(*args: Any, **kwargs: Any) -> Any:
        try:
            return function(*args, **kwargs)
        except PanicSignal:
            raise
        except FileNotFoundError as exc:
            fault('NotFoundFault', exc)
        except PermissionError as exc:
            fault('PermissionFault', exc)
        except FileExistsError as exc:
            fault('AlreadyExistsFault', exc)
        except EOFError as exc:
            fault('UnexpectedEOFFault', exc)
        except UnicodeError as exc:
            fault('ParseFault', exc)
        except ValueError as exc:
            fault('RangeFault', exc)
        except TypeError as exc:
            fault('StateFault', exc)
        except OSError as exc:
            fault('IOFault', exc)

    return invoke


_FILESYSTEM_FAULTS = {
    'open': ('IOFault', 'NotFoundFault', 'PermissionFault', 'AlreadyExistsFault'),
    'read': ('IOFault', 'ParseFault', 'UnexpectedEOFFault', 'RangeFault'),
    'write': ('IOFault', 'ParseFault', 'RangeFault'),
    'close': ('IOFault',),
}


def filesystem_effects(operation: str) -> tuple[T.ElementTag, ...]:
    """Declare native faults that the language cannot infer from Python code."""
    kind = (
        'open' if operation.startswith(('nativeOpen',)) else
        'close' if operation.startswith(('nativeClose', 'nativeFlush')) else
        'read' if operation.startswith(('nativeRead', 'read')) else
        'write' if operation.startswith(('nativeWrite', 'write', 'append')) else
        'read'
    )
    return tuple(
        T.ElementTag(Symbol('Panic'), (T.N(Symbol(fault)),))
        for fault in _FILESYSTEM_FAULTS[kind]
    )
