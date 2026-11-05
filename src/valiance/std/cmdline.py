"""Expose the arguments supplied to the running Valiance program."""

from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.documentation import element_documentation
from valiance.elements.stdlib_native import stdlib_element


@stdlib_element(
    "\\argv",
    tuple(),
    returns=(T.ListExactType(T.String),),
    documentation=element_documentation(
        "Return the arguments passed to the Valiance program.",
        returns="Program arguments, excluding interpreter arguments.",
        examples=(
            (
                r'import { std.cmdline } cmdline.\argv',
                None,
                "Returns the arguments supplied by the caller.",
            ),
        ),
    ),
)
def _argv(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return a fresh list of the current program's command-line arguments."""
    return (list(ctx.program_args),)
