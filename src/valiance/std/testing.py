"""Testing assertions for the Valiance standard library."""

from __future__ import annotations

from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.documentation import element_documentation
from valiance.runtime.vm import AssertionFailure
from valiance.runtime.runtime_values import PanicSignal
from valiance.elements.stdlib_native import stdlib_element

_VALUE = T.V("Value")


@stdlib_element(
    "assertEqual",
    (_VALUE, _VALUE),
    (),
    param_names=("actual", "expected"),
    documentation=element_documentation(
        "Assert that two values are equal.",
        parameters=(("actual", "Observed value."), ("expected", "Required value.")),
        returns="No stack values when the assertion succeeds.",
        category="Testing",
    ),
)
def _assert_equal(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `testing.assertEqual` standard-library element."""
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
    documentation=element_documentation(
        "Assert that two values are different.",
        parameters=(("actual", "Observed value."), ("unexpected", "Value that must not match.")),
        returns="No stack values when the assertion succeeds.",
        category="Testing",
    ),
)
def _assert_not_equal(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `testing.assertNotEqual` standard-library element."""
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
    documentation=element_documentation(
        "Assert that invoking a callable raises a panic.",
        parameters=(("operation", "Niladic callable expected to panic."),),
        returns="No stack values when a panic is observed.",
        category="Testing",
    ),
)
def _assert_panics(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `testing.assertPanics` standard-library element."""
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
    documentation=element_documentation(
        "Fail the current test immediately.",
        parameters=(("message", "Failure message shown by the test runner."),),
        returns="Never returns normally.",
        category="Testing",
    ),
)
def _fail(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `testing.fail` standard-library element."""
    del ctx
    raise AssertionFailure(args[0])
