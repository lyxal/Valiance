"""Python-backed string transformation helpers for the Valiance standard library."""

from __future__ import annotations

import json
from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.documentation import element_documentation
from valiance.elements.stdlib_native import stdlib_element


@stdlib_element(
    "\\Alphabet",
    (),
    (T.String,),
    documentation=element_documentation(
        "Return the uppercase English alphabet.",
        returns="`ABCDEFGHIJKLMNOPQRSTUVWXYZ`.",
        category="Strings",
        examples=(('import {std.string} string.\\Alphabet', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'),),
    ),
)
def _alphabet(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return the uppercase Latin alphabet as a niladic string value."""
    del args, ctx
    return ("ABCDEFGHIJKLMNOPQRSTUVWXYZ",)


@stdlib_element(
    "transliterate",
    (T.String, T.String, T.String),
    (T.String,),
    param_names=("value", "source", "target"),
    documentation=element_documentation(
        "Replace characters according to two positional alphabets.",
        parameters=(
            ("value", "Input string."),
            ("source", "Characters to look up."),
            ("target", "Replacement characters at matching positions."),
        ),
        returns="The transliterated string.",
        category="Strings",
        examples=(('import {std.string} "cab" "abc" "ABC" string.transliterate', 'CAB'),),
    ),
)
def _transliterate(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Transliterate characters using a one-to-one source/target mapping."""
    del ctx
    value, source, target = args
    if len(source) != len(target):
        raise RuntimeError("transliterate alphabets must have equal length")
    table = str.maketrans(source, target)
    return (value.translate(table),)


@stdlib_element(
    "quote", (T.String,), (T.String,), param_names=("value",),
    documentation=element_documentation(
        "Wrap a string in double quotes using JSON escaping.",
        parameters=(("value", "String to quote."),),
        returns="A complete JSON string token with non-ASCII characters escaped.",
        examples=(('import {std.string} "hello" string.quote', '"hello"'),),
        category="Strings",
    ),
)
def _quote(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return a quoted string with JSON escaping."""
    del ctx
    return (quote_string(args[0]),)


def quote_string(value: str) -> str:
    """Escape a complete JSON string token, including control codes and surrogates."""
    return json.dumps(value, ensure_ascii=True)
