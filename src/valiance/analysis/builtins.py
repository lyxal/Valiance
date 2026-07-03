"""Built-in elements and relationships for the standard analysis environment."""

# Every implementation below is registered via `@builtin(...)` and only ever
# called dynamically, through the registry, at runtime. Pyright has no way to
# see those call sites, so it flags each one as unused; that's a false
# positive inherent to this registration pattern, not a real dead-code issue.
# pyright: reportUnusedFunction=false

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import valiance.types as T
from valiance.runtime_values import (
    LazyList,
    ObjectValue,
    PanicSignal,
    format_runtime_value,
    is_finite_list_like,
    is_list_like,
)
from valiance.symbols import Symbol

# Symbols reused across trait wiring, generic variance, and runtime type
# checks below. Builtins that only ever need their own name (dup, +, map, ...)
# are registered with plain string literals instead -- see `builtin()`.
INTEGER = Symbol("Integer")
NUMBER = Symbol("Number")
REAL = Symbol("Real")
STRING = Symbol("String")
ERR = Symbol("Err")
FAULT = Symbol("Fault")
OK = Symbol("OK")
RESULT = Symbol("Result")

TRAIT_IMPLS = (
    (INTEGER, NUMBER),
    (REAL, NUMBER),
    (Symbol("AssertError"), ERR),
    (Symbol("PanicError"), ERR),
    (Symbol("UnwrappedNoneFault"), FAULT),
    (Symbol("UnwrappedResultFault"), FAULT),
)


@dataclass(frozen=True)
class RuntimeContext:
    """Runtime services available to built-in element implementations."""

    output: Callable[[str], None]
    call: Callable[[Any, list[Any]], list[Any]]
    format_value: Callable[[Any], str] = format_runtime_value


RuntimeImpl = Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]]


@dataclass(frozen=True)
class BuiltinOverload:
    """A built-in overload with static type and optional runtime behaviour."""

    signature: T.Overload
    implementation: RuntimeImpl | None = None

    def runtime_matches(self, args: tuple[Any, ...]) -> bool:
        """Return whether these runtime arguments match the nominal signature."""
        if len(args) != len(self.signature.params):
            return False
        if self.implementation is None:
            return False
        return all(
            _runtime_assignable(arg, param)
            for arg, param in zip(args, self.signature.params, strict=True)
        )

    def runtime_vector_matches(self, args: tuple[Any, ...]) -> bool:
        """Return whether scalar arguments are compatible before vectorising."""
        if len(args) != len(self.signature.params):
            return False
        if self.implementation is None:
            return False
        return all(
            _runtime_vector_arg_matches(arg, param)
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


# --------------------------------------------------------------------------
# Registration
#
# `_REGISTRY` collects one `BuiltinOverload` per (name, signature) pair.
# `@builtin(...)` appends to it, and can be stacked on a single function
# when several overloads share one implementation, or applied separately
# to distinct functions when they don't. `declare_overload(...)` is the
# only exception: it registers a signature with no implementation, for
# overloads the analyser should know about before a runtime behaviour
# exists.
# --------------------------------------------------------------------------

_REGISTRY: dict[str, list[BuiltinOverload]] = {}


def builtin(
    name: str,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    generic_constraints: tuple[T.GenericConstraint, ...] = (),
):
    """Register one overload of `name`, implemented by the decorated function.

    Stack multiple `@builtin(...)` applications on the same function when
    those overloads share an implementation; decorate separate functions
    with the same name when they don't.
    """

    def register(fn: RuntimeImpl) -> RuntimeImpl:
        _REGISTRY.setdefault(name, []).append(
            BuiltinOverload(T.Overload(params, returns, generic_constraints), fn)
        )
        return fn

    return register


def declare_overload(
    name: str,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    generic_constraints: tuple[T.GenericConstraint, ...] = (),
) -> None:
    """Register a signature for `name` with no runtime implementation yet."""
    _REGISTRY.setdefault(name, []).append(
        BuiltinOverload(T.Overload(params, returns, generic_constraints), None)
    )


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

_MISSING = object()


def _truth(value: bool) -> Decimal:
    return Decimal(1) if value else Decimal(0)


def _is_ok_value(value: Any) -> bool:
    return (
        isinstance(value, ObjectValue)
        and value.type_name == "OK"
        and "value" in value.fields
    )


def _is_err_value(value: Any) -> bool:
    return isinstance(value, ObjectValue) and (
        value.type_name == "Err"
        or value.type_name.endswith("Error")
        or value.type_name.rsplit(".", 1)[-1].endswith("Error")
    )


def _is_none_value(value: Any) -> bool:
    return value is None or (
        isinstance(value, ObjectValue) and value.type_name.rsplit(".", 1)[-1] == "None"
    )


def _present_value(value: Any) -> Any:
    if not isinstance(value, ObjectValue):
        return _MISSING
    if value.type_name == "Some" or value.type_name.rsplit(".", 1)[-1] == "Some":
        return value.fields.get("value", _MISSING)
    return _MISSING


def _runtime_assignable(value: Any, typ: T.Type) -> bool:
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return True
    if isinstance(typ, T.TaggedType):
        if any(
            tag.absent and tag.name == "infinite" and tag.depth == 0
            for tag in typ.tags
        ) and not is_finite_list_like(value):
            return False
        return _runtime_assignable(value, typ.inner)
    if isinstance(typ, T.NominalType):
        if typ.name == NUMBER:
            return isinstance(value, Decimal)
        if typ.name == STRING:
            return isinstance(value, str)
        if typ.name == OK:
            return _is_ok_value(value)
        if typ.name == RESULT:
            return _is_ok_value(value) or _is_err_value(value)
        if typ.name == ERR:
            return _is_err_value(value)
        return True
    if isinstance(typ, T.UnionType):
        return any(_runtime_assignable(value, item) for item in typ.items)
    if isinstance(typ, T.CollectionType):
        return is_list_like(value)
    return True


def _runtime_vector_arg_matches(value: Any, typ: T.Type) -> bool:
    if is_list_like(value) and not _is_collection_parameter(typ):
        return True
    return _runtime_assignable(value, typ)


def _is_collection_parameter(typ: T.Type) -> bool:
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _is_collection_parameter(typ.inner)
    return isinstance(typ, T.CollectionType)


# --------------------------------------------------------------------------
# Core stack operations
# --------------------------------------------------------------------------


@builtin("dup", (T.V("T"),), (T.V("T"), T.V("T")))
def _dup(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0], args[0])


# --------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------


@builtin("+", (T.Number, T.Number), (T.Number,))
@builtin("+", (T.String, T.String), (T.String,))
def _plus(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0] + args[1],)


@builtin("-", (T.Number, T.Number), (T.Number,))
def _minus(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0] - args[1],)


@builtin("*", (T.Number, T.Number), (T.Number,))
def _star(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0] * args[1],)


@builtin("%", (T.Number, T.Number), (T.Number,))
def _percent(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0] % args[1],)


@builtin("/", (T.Number, T.Number), (T.Number,))
def _slash(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0] / args[1],)


# List-reduce form of `/` is known to the analyser but has no runtime
# implementation yet.
declare_overload(
    "/",
    (
        T.ExactList(T.TypeVariable("Item")),
        T.Fn(
            (T.TypeVariable("Item"), T.TypeVariable("Item")),
            (T.TypeVariable("Item"),),
        ),
    ),
    (T.TypeVariable("Item"),),
)


@builtin("double", (T.Number,), (T.Number,))
def _double(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0] * 2,)


@builtin("positive?", (T.Number,), (T.Boolean,))
def _is_positive(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_truth(args[0] > 0),)


# --------------------------------------------------------------------------
# Comparisons
# --------------------------------------------------------------------------


@builtin("==", (T.Number, T.Number), (T.Boolean,))
@builtin("==", (T.String, T.String), (T.Boolean,))
def _equals(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_truth(args[0] == args[1]),)


@builtin("<", (T.Number, T.Number), (T.Boolean,))
def _less(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_truth(args[0] < args[1]),)


@builtin("<=", (T.Number, T.Number), (T.Boolean,))
def _less_equals(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_truth(args[0] <= args[1]),)


@builtin(">", (T.Number, T.Number), (T.Boolean,))
def _greater(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_truth(args[0] > args[1]),)


@builtin(">=", (T.Number, T.Number), (T.Boolean,))
def _greater_equals(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_truth(args[0] >= args[1]),)


@builtin("true", (), (T.Boolean,))
def _true(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (Decimal(1),)


@builtin("false", (), (T.Boolean,))
def _false(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (Decimal(0),)


# --------------------------------------------------------------------------
# Lists
# --------------------------------------------------------------------------


@builtin(
    "map",
    (
        T.ExactList(T.TypeVariable("Item")),
        T.Fn((T.TypeVariable("Item"),), (T.TypeVariable("Mapped"),)),
    ),
    (T.ExactList(T.TypeVariable("Mapped")),),
)
def _map(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    def mapped_items():
        for item in args[0]:
            mapped = ctx.call(args[1], [item])
            yield mapped[0]

    return (LazyList(mapped_items()),)


@builtin(
    "length",
    (T.WithoutTag(T.ExactList(T.TypeVariable("Item")), "infinite"),),
    (T.Number,),
)
def _length(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (Decimal(len(args[0])),)


@builtin("head", (T.ExactList(T.TypeVariable("Item")),), (T.TypeVariable("Item"),))
def _head(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    for item in args[0]:
        return (item,)
    raise RuntimeError("head requires a non-empty list")


@builtin("range", (T.Number, T.Number), (T.ExactList(T.Number),))
def _range(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    start, stop = args
    return (LazyList(range(int(start), int(stop) + 1)),)


# --------------------------------------------------------------------------
# Optionals and results
# --------------------------------------------------------------------------


@builtin("OK", (T.V("T"),), (T.OKType(T.V("T")),))
def _ok(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (ObjectValue("OK", {"value": args[0]}),)


@builtin("Some", (T.V("T"),), (T.Some(T.V("T")),))
def _some(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (ObjectValue("Some", {"value": args[0]}),)


@builtin("None", (), (T.NoneType(),))
def _none(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (ObjectValue("None", {}),)


@builtin(
    "&",
    (
        T.optional(T.TypeVariable("T")),
        T.Fn((T.TypeVariable("T"),), (T.TypeVariable("U"),)),
    ),
    (T.optional(T.TypeVariable("U")),),
)
@builtin(
    "&",
    (
        T.Result(T.TypeVariable("T"), T.TypeVariable("E")),
        T.Fn((T.TypeVariable("T"),), (T.TypeVariable("U"),)),
    ),
    (T.Result(T.TypeVariable("U"), T.TypeVariable("E")),),
)
@builtin(
    "&",
    (
        T.TypeVariable("E"),
        T.Fn((T.TypeVariable("T"),), (T.TypeVariable("U"),)),
    ),
    (T.TypeVariable("E"),),
    (T.GenericConstraint("E", T.N(ERR)),),
)
def _and_then(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    value, callable_value = args
    if _is_none_value(value):
        return (value,)
    present = _present_value(value)
    if present is not _MISSING:
        called = ctx.call(callable_value, [present])
        return (called[0],)
    if _is_ok_value(value):
        called = ctx.call(callable_value, [value.fields["value"]])
        result = called[0]
        if _is_ok_value(result) or _is_err_value(result):
            return (result,)
        return (_ok((result,), ctx)[0],)
    if _is_err_value(value):
        return (value,)
    raise RuntimeError("& requires an optional or Result value")


@builtin("?", (T.optional(T.TypeVariable("T")),), (T.TypeVariable("T"),))
@builtin(
    "?",
    (T.Result(T.TypeVariable("T"), T.TypeVariable("E")),),
    (T.TypeVariable("T"),),
)
def _question(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    value = args[0]
    if _is_none_value(value) or _is_err_value(value):
        return (value,)
    present = _present_value(value)
    if present is not _MISSING:
        return (present,)
    if _is_ok_value(value):
        return (value.fields["value"],)
    return (value,)


@builtin("?!", (T.optional(T.TypeVariable("T")),), (T.TypeVariable("T"),))
@builtin(
    "?!",
    (T.Result(T.TypeVariable("T"), T.TypeVariable("E")),),
    (T.TypeVariable("T"),),
)
def _question_bang(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    value = args[0]
    if _is_none_value(value):
        raise PanicSignal(
            ObjectValue(
                "UnwrappedNoneFault",
                {"message": "Tried to unwrap optional"},
            )
        )
    if _is_err_value(value):
        raise PanicSignal(
            ObjectValue(
                "UnwrappedResultFault",
                {"message": "Tried to unwrap Result, found Error"},
            )
        )
    return _question(args, ctx)


# --------------------------------------------------------------------------
# I/O and control flow
# --------------------------------------------------------------------------


@builtin("print", (T.V("T"),), ())
def _print(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    ctx.output(ctx.format_value(args[0]))
    return ()


@builtin("println", (T.V("T"),), ())
def _println(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    ctx.output(ctx.format_value(args[0]) + "\n")
    return ()


@builtin("panic", (T.V("Fault"),), (T.Never(),))
def _panic(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    raise PanicSignal(args[0])


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def _all_elements() -> tuple[BuiltinElement, ...]:
    return tuple(
        BuiltinElement(Symbol(name), tuple(overloads))
        for name, overloads in _REGISTRY.items()
    )


# Public, for callers that want the full built-in catalogue directly (e.g.
# `from valiance.analysis.builtins import BUILTIN_ELEMENTS`). This is derived
# from `_REGISTRY` once, at import time, after every `@builtin(...)` /
# `declare_overload(...)` call above has run -- it is not hand-maintained.
BUILTIN_ELEMENTS: tuple[BuiltinElement, ...] = _all_elements()


def default_environment() -> T.Environment:
    """Build an environment populated with Valiance's built-in elements."""
    env = T.Environment()
    env.define_trait(ERR)
    env.define_trait(FAULT)
    env.context.set_generic_variance(OK, (T.Variance.COVARIANT,))
    env.context.set_generic_variance(
        RESULT,
        (T.Variance.COVARIANT, T.Variance.COVARIANT),
    )
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
