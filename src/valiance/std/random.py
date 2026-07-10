"""Python-backed random helpers for the Valiance standard library."""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import valiance.types as T
from valiance.analysis.builtins import RuntimeContext
from valiance.documentation import element_documentation
from valiance.stdlib_native import stdlib_element


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
    return (Decimal(random.getrandbits(1)),)
