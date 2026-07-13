"""Python-backed regex helpers for the Valiance standard library."""

from __future__ import annotations

import re
from typing import Any

import valiance.types as T
from valiance.analysis.builtins import RuntimeContext
from valiance.documentation import element_documentation
from valiance.runtime_values import Number, ObjectValue
from valiance.stdlib_native import stdlib_element


def _truth(value: bool) -> Number:
    """Compute truth within this subsystem."""
    return Number(1) if value else Number(0)


@stdlib_element(
    "matches",
    (T.String, T.String),
    (T.Boolean,),
    param_names=("pattern", "value"),
    documentation=element_documentation(
        "Test whether an entire string matches a regular expression.",
        parameters=(
            ("pattern", "Regular-expression pattern."),
            ("value", "String to test."),
        ),
        returns="A Boolean indicating whether the complete string matched.",
        examples=((r'"[A-Z][a-z]+" "Jeff" | matches', "true"),),
        category="Regular expressions",
    ),
)
def _matches(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `regex.matches` standard-library element."""
    pattern, value = args
    return (_truth(re.fullmatch(pattern, value) is not None),)


@stdlib_element(
    "contains",
    (T.String, T.String),
    (T.Boolean,),
    param_names=("pattern", "value"),
    documentation=element_documentation(
        "Test whether a regular expression occurs anywhere in a string.",
        parameters=(
            ("pattern", "Regular-expression pattern."),
            ("value", "String to search."),
        ),
        returns="A Boolean indicating whether a match was found.",
        category="Regular expressions",
    ),
)
def _contains(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `regex.contains` standard-library element."""
    pattern, value = args
    return (_truth(re.search(pattern, value) is not None),)


@stdlib_element(
    "first",
    (T.String, T.String),
    (T.optional(T.String),),
    param_names=("pattern", "value"),
    documentation=element_documentation(
        "Return the first regular-expression match in a string.",
        parameters=(
            ("pattern", "Regular-expression pattern."),
            ("value", "String to search."),
        ),
        returns="`Some[String]` containing the first match, or `None`.",
        category="Regular expressions",
    ),
)
def _first(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `regex.first` standard-library element."""
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
    documentation=element_documentation(
        "Replace every regular-expression match in a string.",
        parameters=(
            ("pattern", "Regular-expression pattern."),
            ("replacement", "Replacement text."),
            ("value", "String to transform."),
        ),
        returns="The transformed string.",
        category="Regular expressions",
    ),
)
def _replace(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `regex.replace` standard-library element."""
    pattern, replacement, value = args
    return (re.sub(pattern, replacement, value),)


@stdlib_element(
    "split",
    (T.String, T.String),
    (T.ExactList(T.String),),
    param_names=("pattern", "value"),
    documentation=element_documentation(
        "Split a string at regular-expression matches.",
        parameters=(("pattern", "Separator pattern."), ("value", "String to split.")),
        returns="A list of string segments.",
        category="Regular expressions",
    ),
)
def _split(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `regex.split` standard-library element."""
    pattern, value = args
    return (re.split(pattern, value),)
