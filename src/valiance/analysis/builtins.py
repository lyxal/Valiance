"""Built-in elements and relationships for the standard analysis environment."""

from __future__ import annotations

from dataclasses import dataclass

import valiance.types as T
from valiance.symbols import Symbol

INTEGER = Symbol("Integer")
NUMBER = Symbol("Number")
REAL = Symbol("Real")

DUP = Symbol("dup")
HEAD = Symbol("head")
LENGTH = Symbol("length")
MAP = Symbol("map")
PLUS = Symbol("+")
SLASH = Symbol("/")
EQUALS = Symbol("==")
IS_POSITIVE = Symbol("positive?")
DOUBLE = Symbol("double")

TRAIT_IMPLS = (
    (INTEGER, NUMBER),
    (REAL, NUMBER),
)


@dataclass(frozen=True)
class BuiltinElement:
    """A named built-in element and its overloads."""

    name: Symbol
    overloads: tuple[T.Overload, ...]


def element(name: Symbol, *overloads: T.Overload) -> BuiltinElement:
    """Declare a built-in element."""
    return BuiltinElement(name, overloads)


def overload(params: tuple[T.Type, ...], returns: tuple[T.Type, ...]) -> T.Overload:
    """Declare one stack-effect overload."""
    return T.Overload(params, returns)


BUILTIN_ELEMENTS = (
    element(
        DUP,
        overload((T.V("T"),), (T.V("T"), T.V("T"))),
    ),
    element(
        PLUS,
        overload((T.Number, T.Number), (T.Number,)),
        overload((T.String, T.String), (T.String,)),
    ),
    element(
        SLASH,
        overload((T.Number, T.Number), (T.Number,)),
        overload(
            (
                T.C(T.ListExactType, T.V("T")),
                T.Fn((T.V("T"), T.V("T")), (T.V("T"),)),
            ),
            (T.V("T"),),
        ),
    ),
    element(
        MAP,
        overload(
            (
                T.C(T.ListExactType, T.V("T")),
                T.Fn((T.V("T"),), (T.V("U"),)),
            ),
            (T.C(T.ListExactType, T.V("U")),),
        ),
    ),
    element(
        LENGTH,
        overload((T.C(T.ListExactType, T.V("T")),), (T.Number,)),
    ),
    element(
        HEAD,
        overload((T.C(T.ListExactType, T.V("T")),), (T.V("T"),)),
    ),
    element(
        EQUALS,
        overload((T.Number, T.Number), (T.Boolean,)),
        overload((T.String, T.String), (T.Boolean,)),
    ),
    element(
        IS_POSITIVE,
        overload((T.Number,), (T.Boolean,)),
    ),
    element(
        DOUBLE,
        overload((T.Number,), (T.Number,)),
    ),
)


def default_environment() -> T.Environment:
    """Build an environment populated with Valiance's built-in elements."""
    env = T.Environment()
    for type_name, trait_name in TRAIT_IMPLS:
        env.add_trait_impl(type_name, trait_name)
    for item in BUILTIN_ELEMENTS:
        for candidate in item.overloads:
            env.define_overload(item.name, candidate)
    return env
