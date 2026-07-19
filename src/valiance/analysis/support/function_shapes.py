"""Shared helpers for aligning declared function syntax with overload shapes."""

from __future__ import annotations

import valiance.vtypes as T
from valiance.asts import FunctionNode
from valiance.vtypes.symbols import Symbol


def function_param_names_for_overload(
    node: FunctionNode,
    inputs: tuple[T.Type, ...],
) -> tuple[Symbol | None, ...]:
    """Return declared parameter names aligned to an overload input tuple."""
    if node.params is None:
        return (None,) * len(inputs)
    names = tuple(param.name for param in node.params)
    if len(names) < len(inputs):
        return (None,) * (len(inputs) - len(names)) + names
    return names
