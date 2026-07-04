"""Python-backed regex helpers for the Valiance standard library."""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import valiance.types as T
from valiance.analysis.builtins import RuntimeContext
from valiance.runtime_values import ObjectValue
from valiance.stdlib_native import stdlib_element


def _truth(value: bool) -> Decimal:
    return Decimal(1) if value else Decimal(0)


@stdlib_element(
    "matches",
    (T.String, T.String),
    (T.Boolean,),
    param_names=("pattern", "value"),
)
def _matches(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    pattern, value = args
    return (_truth(re.fullmatch(pattern, value) is not None),)


@stdlib_element(
    "contains",
    (T.String, T.String),
    (T.Boolean,),
    param_names=("pattern", "value"),
)
def _contains(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    pattern, value = args
    return (_truth(re.search(pattern, value) is not None),)


@stdlib_element(
    "first",
    (T.String, T.String),
    (T.optional(T.String),),
    param_names=("pattern", "value"),
)
def _first(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    pattern, value = args
    match = re.search(pattern, value)
    if match is None:
        return (ObjectValue("None", {}),)
    return (ObjectValue("Some", {"value": match.group(0)}),)


@stdlib_element(
    "replace",
    (T.String, T.String, T.String),
    (T.String,),
    param_names=("pattern", "replacement", "value"),
)
def _replace(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    pattern, replacement, value = args
    return (re.sub(pattern, replacement, value),)


@stdlib_element(
    "split",
    (T.String, T.String),
    (T.ExactList(T.String),),
    param_names=("pattern", "value"),
)
def _split(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    pattern, value = args
    return (re.split(pattern, value),)
