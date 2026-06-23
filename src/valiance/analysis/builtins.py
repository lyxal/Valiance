"""Built-in elements and relationships for the standard analysis environment."""

from __future__ import annotations

import valiance.types as T
from valiance.symbols import Symbol


def default_environment() -> T.Environment:
    """Build an environment populated with Valiance's built-in elements."""
    env = T.Environment()
    integer = Symbol("Integer")
    number = Symbol("Number")
    real = Symbol("Real")
    env.add_trait_impl(integer, number)
    env.add_trait_impl(real, number)

    plus = Symbol("+")
    slash = Symbol("/")
    map_symbol = Symbol("map")
    length = Symbol("length")
    head = Symbol("head")

    env.define_overload(plus, T.Overload((T.Number, T.Number), (T.Number,)))
    env.define_overload(plus, T.Overload((T.String, T.String), (T.String,)))
    env.define_overload(slash, T.Overload((T.Number, T.Number), (T.Number,)))
    env.define_overload(
        slash,
        T.Overload(
            (
                T.C(T.ListExactType, T.V("T")),
                T.Fn((T.V("T"), T.V("T")), (T.V("T"),)),
            ),
            (T.V("T"),),
        ),
    )
    env.define_overload(
        map_symbol,
        T.Overload(
            (
                T.C(T.ListExactType, T.V("T")),
                T.Fn((T.V("T"),), (T.V("U"),)),
            ),
            (T.C(T.ListExactType, T.V("U")),),
        ),
    )
    env.define_overload(
        length,
        T.Overload((T.C(T.ListExactType, T.V("T")),), (T.Number,)),
    )
    env.define_overload(
        head,
        T.Overload((T.C(T.ListExactType, T.V("T")),), (T.V("T"),)),
    )
    return env
