"""Testing assertions for the Valiance standard library."""

from __future__ import annotations

from typing import Any

import valiance.types as T
from valiance.analysis.builtins import RuntimeContext
from valiance.runtime.vm import AssertionFailure
from valiance.runtime_values import PanicSignal
from valiance.stdlib_native import stdlib_element

_VALUE = T.V("Value")


@stdlib_element(
    "assertEqual",
    (_VALUE, _VALUE),
    (),
    param_names=("actual", "expected"),
)
def _assert_equal(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    actual, expected = args
    if actual != expected:
        raise AssertionFailure(
            "expected values to be equal\n"
            f"expected: {ctx.format_value(expected)}\n"
            f"actual:   {ctx.format_value(actual)}"
        )
    return ()


@stdlib_element(
    "assertNotEqual",
    (_VALUE, _VALUE),
    (),
    param_names=("actual", "unexpected"),
)
def _assert_not_equal(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    actual, unexpected = args
    if actual == unexpected:
        raise AssertionFailure(
            "expected values to be different\n"
            f"both values were: {ctx.format_value(actual)}"
        )
    return ()


@stdlib_element(
    "assertPanics",
    (T.Fn(),),
    (),
    param_names=("operation",),
)
def _assert_panics(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    try:
        ctx.call(args[0], [])
    except PanicSignal:
        return ()
    raise AssertionFailure("expected the operation to panic, but it returned normally")


@stdlib_element(
    "fail",
    (T.String,),
    (),
    param_names=("message",),
)
def _fail(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    raise AssertionFailure(args[0])
