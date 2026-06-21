from __future__ import annotations

"""Built-in elements and relationships for the standard analysis environment."""

import valiance.types as T


def default_environment() -> T.Environment:
    """Build an environment populated with Valiance's built-in elements."""
    env = T.Environment()
    env.add_trait_impl("Integer", "Number")
    env.add_trait_impl("Real", "Number")

    env.define_overload("+", T.Overload((T.Number, T.Number), (T.Number,)))
    env.define_overload("+", T.Overload((T.String, T.String), (T.String,)))
    env.define_overload("/", T.Overload((T.Number, T.Number), (T.Number,)))
    env.define_overload(
        "length",
        T.Overload((T.C(T.ListExactType, T.V("T")),), (T.Number,)),
    )
    env.define_overload(
        "head",
        T.Overload((T.C(T.ListExactType, T.V("T")),), (T.V("T"),)),
    )
    return env
