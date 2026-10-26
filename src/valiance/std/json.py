"""Primitive JSON token formatting for the Valiance-defined encoders."""

from __future__ import annotations

import json
from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.stdlib_native import stdlib_element
from valiance.runtime.runtime_values import ObjectValue
from valiance.vtypes.symbols import Symbol


@stdlib_element("quote", (T.String,), (T.String,), param_names=("value",), exported=False)
def _quote(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Escape a complete JSON string token, including control codes and surrogates."""
    del ctx
    return (json.dumps(args[0], ensure_ascii=True),)


@stdlib_element(
    "number", (T.Number,), (T.Result(T.String, T.N(Symbol("ValueError"))),),
    param_names=("value",), exported=False,
)
def _number(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Format exact real digits without rounding through a host float."""
    del ctx
    value, = args
    if value.is_complex():
        return (ObjectValue("ValueError", {"message": "JSON numbers must be real"}),)
    return (str(value.real),)
