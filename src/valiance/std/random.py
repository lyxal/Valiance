"""Python-backed random helpers for the Valiance standard library."""

from __future__ import annotations

import random
from typing import Any

from valiance.runtime.runtime_values import RuntimeNumber
import valiance.types as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.documentation import element_documentation
from valiance.elements.stdlib_native import stdlib_element


@stdlib_element(
    "randbit",
    (),
    (T.Integer,),
    documentation=element_documentation(
        "Return a uniformly random binary integer.",
        returns="Either zero or one, each with equal probability.",
        examples=(("randbit", "0"),),
        category="Randomness",
    ),
)
def _randbit(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return one random bit as a Valiance integer value."""
    return (RuntimeNumber(random.getrandbits(1)),)


@stdlib_element(
    "between",
    (T.Integer, T.Integer),
    (T.Integer,),
    param_names=("minimum", "maximum"),
    documentation=element_documentation(
        "Return a uniformly random integer in an inclusive range.",
        parameters=(
            ("minimum", "Smallest possible integer."),
            ("maximum", "Largest possible integer."),
        ),
        returns="A random integer between the two bounds, inclusive.",
        category="Randomness",
    ),
)
def _between(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return a random integer between two inclusive Valiance bounds."""
    del ctx
    minimum, maximum = (int(value) for value in args)
    if minimum > maximum:
        raise RuntimeError("between requires minimum <= maximum")
    return (RuntimeNumber(random.randint(minimum, maximum)),)
