from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.stdlib_native import stdlib_element
from valiance.elements.documentation import element_documentation


@stdlib_element(
    "\\argv",
    tuple(),
    returns=(T.ListExactType(T.String),),
    documentation=element_documentation(
        "Return the arguments passed to the Valiance program.",
        returns="Program arguments, excluding interpreter arguments.",
    ),
)
def _argv(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (list(ctx.program_args),)
