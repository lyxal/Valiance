"""Python-backed trigonometry helpers for the Valiance standard library."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import valiance.types as T
from valiance.analysis.builtins import RuntimeContext
from valiance.documentation import element_documentation
from valiance.stdlib_native import stdlib_element


def _number(value: float) -> Decimal:
    """Compute number within this subsystem."""
    return Decimal(str(value))


@stdlib_element(
    "pi",
    (),
    (T.Number,),
    documentation=element_documentation(
        "Return the mathematical constant pi.",
        returns="An approximation of π as a Valiance `Number`.",
        category="Trigonometry",
    ),
)
def _pi(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `trig.pi` standard-library element."""
    return (_number(math.pi),)


@stdlib_element(
    "sin", (T.Number,), (T.Number,), param_names=("n",),
    documentation=element_documentation(
        "Return the sine of an angle in radians.",
        parameters=(("n", "Angle in radians."),), returns="The sine of the angle.", category="Trigonometry"
    ),
)
def _sin(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `trig.sin` standard-library element."""
    return (_number(math.sin(float(args[0]))),)


@stdlib_element(
    "cos", (T.Number,), (T.Number,), param_names=("n",),
    documentation=element_documentation(
        "Return the cosine of an angle in radians.",
        parameters=(("n", "Angle in radians."),), returns="The cosine of the angle.", category="Trigonometry"
    ),
)
def _cos(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `trig.cos` standard-library element."""
    return (_number(math.cos(float(args[0]))),)


@stdlib_element(
    "tan", (T.Number,), (T.Number,), param_names=("n",),
    documentation=element_documentation(
        "Return the tangent of an angle in radians.",
        parameters=(("n", "Angle in radians."),), returns="The tangent of the angle.", category="Trigonometry"
    ),
)
def _tan(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `trig.tan` standard-library element."""
    return (_number(math.tan(float(args[0]))),)


@stdlib_element(
    "asin", (T.Number,), (T.Number,), param_names=("n",),
    documentation=element_documentation(
        "Return the inverse sine in radians.",
        parameters=(("n", "Input ratio."),), returns="The corresponding angle in radians.", category="Trigonometry"
    ),
)
def _asin(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `trig.asin` standard-library element."""
    return (_number(math.asin(float(args[0]))),)


@stdlib_element(
    "acos", (T.Number,), (T.Number,), param_names=("n",),
    documentation=element_documentation(
        "Return the inverse cosine in radians.",
        parameters=(("n", "Input ratio."),), returns="The corresponding angle in radians.", category="Trigonometry"
    ),
)
def _acos(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `trig.acos` standard-library element."""
    return (_number(math.acos(float(args[0]))),)


@stdlib_element(
    "atan", (T.Number,), (T.Number,), param_names=("n",),
    documentation=element_documentation(
        "Return the inverse tangent in radians.",
        parameters=(("n", "Input ratio."),), returns="The corresponding angle in radians.", category="Trigonometry"
    ),
)
def _atan(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `trig.atan` standard-library element."""
    return (_number(math.atan(float(args[0]))),)
