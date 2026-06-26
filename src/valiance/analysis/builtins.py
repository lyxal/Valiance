"""Built-in elements and relationships for the standard analysis environment."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import valiance.types as T
from valiance.symbols import Symbol

INTEGER = Symbol("Integer")
NUMBER = Symbol("Number")
REAL = Symbol("Real")
STRING = Symbol("String")

DUP = Symbol("dup")
HEAD = Symbol("head")
LENGTH = Symbol("length")
MAP = Symbol("map")
MINUS = Symbol("-")
PLUS = Symbol("+")
PRINT = Symbol("print")
PRINTLN = Symbol("println")
SLASH = Symbol("/")
STAR = Symbol("*")
PERCENT = Symbol("%")
EQUALS = Symbol("==")
LESS = Symbol("<")
LESS_EQUALS = Symbol("<=")
GREATER = Symbol(">")
GREATER_EQUALS = Symbol(">=")
IS_POSITIVE = Symbol("positive?")
DOUBLE = Symbol("double")
TRUE = Symbol("true")
FALSE = Symbol("false")

TRAIT_IMPLS = (
    (INTEGER, NUMBER),
    (REAL, NUMBER),
)


@dataclass(frozen=True)
class RuntimeContext:
    """Runtime services available to built-in element implementations."""

    output: Callable[[str], None]


RuntimeImpl = Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]]
RuntimePredicate = Callable[[tuple[Any, ...]], bool]


@dataclass(frozen=True)
class BuiltinOverload:
    """A built-in overload with static type and optional runtime behaviour."""

    signature: T.Overload
    implementation: RuntimeImpl | None = None
    accepts: RuntimePredicate | None = None

    def runtime_accepts(self, args: tuple[Any, ...]) -> bool:
        """Return whether this overload can execute these runtime arguments."""
        if len(args) != len(self.signature.params):
            return False
        if self.implementation is None:
            return False
        if self.accepts is not None:
            return self.accepts(args)
        return all(
            _runtime_assignable(arg, param)
            for arg, param in zip(args, self.signature.params, strict=True)
        )


@dataclass(frozen=True)
class BuiltinElement:
    """A named built-in element and its static/runtime overloads."""

    name: Symbol
    definitions: tuple[BuiltinOverload, ...]

    @property
    def overloads(self) -> tuple[T.Overload, ...]:
        """Return static overload signatures for the analyser."""
        return tuple(definition.signature for definition in self.definitions)


def element(name: Symbol, *overloads: BuiltinOverload) -> BuiltinElement:
    """Declare a built-in element."""
    return BuiltinElement(name, overloads)


def overload(
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    implementation: RuntimeImpl | None = None,
    accepts: RuntimePredicate | None = None,
) -> BuiltinOverload:
    """Declare one stack-effect overload."""
    return BuiltinOverload(T.Overload(params, returns), implementation, accepts)


def _binary(func: Callable[[Any, Any], Any]) -> RuntimeImpl:
    return lambda args, ctx: (func(args[0], args[1]),)


def _comparison(func: Callable[[Any, Any], bool]) -> RuntimeImpl:
    return lambda args, ctx: (_truth(func(args[0], args[1])),)


def _print(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    ctx.output(_format_value(args[0]))
    return ()


def _truth(value: bool) -> Decimal:
    return Decimal(1) if value else Decimal(0)


def _runtime_assignable(value: Any, typ: T.Type) -> bool:
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return True
    if isinstance(typ, T.TaggedType):
        return _runtime_assignable(value, typ.inner)
    if isinstance(typ, T.NominalType):
        if typ.name == NUMBER:
            return isinstance(value, Decimal)
        if typ.name == STRING:
            return isinstance(value, str)
        return True
    if isinstance(typ, T.CollectionType):
        return isinstance(value, Sequence) and not isinstance(value, (str, bytes))
    return True


def _format_value(value: Any) -> str:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return format(value.quantize(Decimal(1)), "f")
        return format(value.normalize(), "f")
    return str(value)


BUILTIN_ELEMENTS = (
    element(
        DUP,
        overload(
            (T.V("T"),),
            (T.V("T"), T.V("T")),
            lambda args, ctx: (args[0], args[0]),
        ),
    ),
    element(
        PLUS,
        overload(
            (T.Number, T.Number),
            (T.Number,),
            _binary(lambda left, right: left + right),
        ),
        overload(
            (T.String, T.String),
            (T.String,),
            _binary(lambda left, right: left + right),
        ),
    ),
    element(
        MINUS,
        overload(
            (T.Number, T.Number),
            (T.Number,),
            _binary(lambda left, right: left - right),
        ),
    ),
    element(
        STAR,
        overload(
            (T.Number, T.Number),
            (T.Number,),
            _binary(lambda left, right: left * right),
        ),
    ),
    element(
        SLASH,
        overload(
            (T.Number, T.Number),
            (T.Number,),
            _binary(lambda left, right: left / right),
        ),
        overload(
            (
                T.C(T.ListExactType, T.V("T")),
                T.Fn((T.V("T"), T.V("T")), (T.V("T"),)),
            ),
            (T.V("T"),),
        ),
    ),
    element(
        PERCENT,
        overload(
            (T.Number, T.Number),
            (T.Number,),
            _binary(lambda left, right: left % right),
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
        overload(
            (T.C(T.ListExactType, T.V("T")),),
            (T.Number,),
            lambda args, ctx: (Decimal(len(args[0])),),
        ),
    ),
    element(
        HEAD,
        overload(
            (T.C(T.ListExactType, T.V("T")),),
            (T.V("T"),),
            lambda args, ctx: (args[0][0],),
        ),
    ),
    element(
        EQUALS,
        overload(
            (T.Number, T.Number),
            (T.Boolean,),
            _comparison(lambda left, right: left == right),
        ),
        overload(
            (T.String, T.String),
            (T.Boolean,),
            _comparison(lambda left, right: left == right),
        ),
    ),
    element(
        LESS,
        overload(
            (T.Number, T.Number),
            (T.Boolean,),
            _comparison(lambda left, right: left < right),
        ),
    ),
    element(
        LESS_EQUALS,
        overload(
            (T.Number, T.Number),
            (T.Boolean,),
            _comparison(lambda left, right: left <= right),
        ),
    ),
    element(
        GREATER,
        overload(
            (T.Number, T.Number),
            (T.Boolean,),
            _comparison(lambda left, right: left > right),
        ),
    ),
    element(
        GREATER_EQUALS,
        overload(
            (T.Number, T.Number),
            (T.Boolean,),
            _comparison(lambda left, right: left >= right),
        ),
    ),
    element(
        IS_POSITIVE,
        overload((T.Number,), (T.Boolean,), lambda args, ctx: (_truth(args[0] > 0),)),
    ),
    element(
        DOUBLE,
        overload((T.Number,), (T.Number,), lambda args, ctx: (args[0] * 2,)),
    ),
    element(
        TRUE,
        overload((), (T.Boolean,), lambda args, ctx: (Decimal(1),)),
    ),
    element(
        FALSE,
        overload((), (T.Boolean,), lambda args, ctx: (Decimal(0),)),
    ),
    element(
        PRINT,
        overload((T.V("T"),), (), _print),
    ),
    element(
        PRINTLN,
        overload((T.V("T"),), (), _print),
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


def runtime_elements() -> dict[str, BuiltinElement]:
    """Return built-in elements that have at least one runtime implementation."""
    return {
        item.name.text: item
        for item in BUILTIN_ELEMENTS
        if any(overload.implementation is not None for overload in item.definitions)
    }
