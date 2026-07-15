"""Python-backed text helpers for the Valiance standard library."""

from __future__ import annotations

from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.documentation import element_documentation
from valiance.elements.stdlib_native import stdlib_element


@stdlib_element(
    "trim",
    (T.String,),
    (T.String,),
    param_names=("value",),
    documentation=element_documentation(
        "Remove leading and trailing whitespace from a string.",
        parameters=(("value", "String to trim."),),
        returns="The trimmed string.",
        examples=(('"  hello  " | trim', "hello"),),
        category="Text",
    ),
)
def _trim(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `text.trim` standard-library element."""
    return (args[0].strip(),)
