"""Python-backed text helpers for the Valiance standard library."""

from __future__ import annotations

from typing import Any

import valiance.types as T
from valiance.analysis.builtins import RuntimeContext
from valiance.stdlib_native import stdlib_element


@stdlib_element("trim", (T.String,), (T.String,), param_names=("value",))
def _trim(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0].strip(),)
