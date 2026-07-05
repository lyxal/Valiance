"""Built-in elements and relationships for the standard analysis environment."""

# Every implementation below is registered via `@builtin(...)` and only ever
# called dynamically, through the registry, at runtime. Pyright has no way to
# see those call sites, so it flags each one as unused; that's a false
# positive inherent to this registration pattern, not a real dead-code issue.
# pyright: reportUnusedFunction=false

from __future__ import annotations

from collections.abc import Callable, Iterator
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
    (INTEGER, REAL),
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
    name: str | Symbol,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...] = (),
    generic_constraints: tuple[T.GenericConstraint, ...] = (),
    call_site: Callable[..., T.Overload | None] | None = None,
    element_tags: tuple[T.ElementTag, ...] = (),
):
    """Register one overload of `name`, implemented by the decorated function.

    Stack multiple `@builtin(...)` applications on the same function when
    those overloads share an implementation; decorate separate functions
    with the same name when they don't.
    """

    def register(fn: RuntimeImpl) -> RuntimeImpl:
        key = name.text if isinstance(name, Symbol) else name
        _REGISTRY.setdefault(key, []).append(
            BuiltinOverload(
                T.Overload(
                    params,
                    returns,
                    generic_constraints,
                    call_site_body=call_site,
                    element_tags=frozenset(element_tags),
                ),
                fn,
            )
        )
        return fn

    return register


def declare_overload(
    name: str,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    generic_constraints: tuple[T.GenericConstraint, ...] = (),
    element_tags: tuple[T.ElementTag, ...] = (),
) -> None:
    """Register a signature for `name` with no runtime implementation yet."""
    _REGISTRY.setdefault(name, []).append(
        BuiltinOverload(
            T.Overload(
                params,
                returns,
                generic_constraints,
                element_tags=frozenset(element_tags),
            ),
            None,
        )
    )


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

_MISSING = object()
EAGER_TAG = T.ElementTag(Symbol("Eager"))
IO_TAG = T.ElementTag(Symbol("IO"))


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
        if typ.name == REAL:
            return isinstance(value, Decimal)
        if typ.name == INTEGER:
            return isinstance(value, Decimal) and value == value.to_integral_value()
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


def _callable_has_element_tag(value: Any, tag: str) -> bool:
    code = getattr(value, "code", None)
    if code is not None and tag in getattr(code, "element_tags", ()):
        return True
    overloads = getattr(value, "overloads", ())
    return any(_callable_has_element_tag(overload, tag) for overload in overloads)


# --------------------------------------------------------------------------
# Core stack operations
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _CallableApplication:
    overload: T.Overload
    applied: T.AppliedOverload
    concrete_type: T.FunctionType


def _callable_overloads(typ: T.Type) -> tuple[T.Overload, ...]:
    typ = T.normalize(typ)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return ()
        return (T.Overload(typ.params, typ.returns, element_tags=typ.element_tags),)
    if isinstance(typ, T.OverloadSetType):
        return typ.overloads
    return ()


def _apply_callable(
    overload: T.Overload,
    args: tuple[T.Type, ...],
) -> _CallableApplication | None:
    applied = T.apply_overload(overload, args)
    if applied is None:
        return None
    return _CallableApplication(
        overload,
        applied,
        T.Fn(applied.params, applied.actual_returns, overload.element_tags),
    )


def _callable_applications(
    typ: T.Type,
    args: tuple[T.Type, ...],
) -> Iterator[_CallableApplication]:
    for overload in _callable_overloads(typ):
        application = _apply_callable(overload, args)
        if application is not None:
            yield application


def _first_callable_application(
    typ: T.Type,
    args: tuple[T.Type, ...],
) -> _CallableApplication | None:
    return next(_callable_applications(typ, args), None)


def _peek_call_site(call_params: tuple[T.Type, ...]) -> T.Overload | None:
    if not call_params:
        return None
    function_type = call_params[-1]
    stack = call_params[:-1]
    for candidate in _callable_overloads(function_type):
        arity = len(candidate.params)
        if len(stack) < arity:
            continue
        args = stack[-arity:] if arity else ()
        application = _apply_callable(candidate, args)
        if application is not None:
            return T.Overload(
                (*args, application.concrete_type),
                application.applied.actual_returns,
                call_site_body=0,
            )
    return None


def _dip_call_site(call_params: tuple[T.Type, ...]) -> T.Overload | None:
    if not call_params:
        return None
    function_type = call_params[-1]
    stack = call_params[:-1]
    for candidate in _callable_overloads(function_type):
        arity = len(candidate.params)
        if len(stack) < arity + 1:
            continue
        args = stack[-arity - 1 : -1] if arity else ()
        application = _apply_callable(candidate, args)
        if application is not None:
            held = stack[-1]
            return T.Overload(
                (*args, held, application.concrete_type),
                (*application.applied.actual_returns, held),
                call_site_body=arity + 1,
            )
    return None


def _fork_call_site(call_params: tuple[T.Type, ...]) -> T.Overload | None:
    if len(call_params) < 2:
        return None
    left_type, right_type = call_params[-2:]
    stack = call_params[:-2]
    for left in _callable_overloads(left_type):
        for right in _callable_overloads(right_type):
            arity = max(len(left.params), len(right.params))
            if len(stack) < arity:
                continue
            args = stack[-arity:] if arity else ()
            left_args = args[-len(left.params) :] if left.params else ()
            right_args = args[-len(right.params) :] if right.params else ()
            left_application = _apply_callable(left, left_args)
            right_application = _apply_callable(right, right_args)
            if left_application is not None and right_application is not None:
                return T.Overload(
                    (
                        *args,
                        left_application.concrete_type,
                        right_application.concrete_type,
                    ),
                    (
                        *left_application.applied.actual_returns,
                        *right_application.applied.actual_returns,
                    ),
                    call_site_body=arity,
                )
    return None


def _eager_map_call_site(call_params: tuple[T.Type, ...]) -> T.Overload | None:
    if len(call_params) != 2:
        return None
    list_type, function_type = call_params
    item_type = T.collection_item_type(list_type)
    if item_type is None:
        return None
    for application in _callable_applications(function_type, (item_type,)):
        if application.applied.actual_returns:
            continue
        if EAGER_TAG not in application.concrete_type.element_tags:
            continue
        return T.Overload(
            (list_type, application.concrete_type),
            (),
            call_site_body=0,
            element_tags=frozenset((EAGER_TAG,)),
        )
    return None


@builtin("dup", (T.V("T"),), (T.V("T"), T.V("T")))
def _dup(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0], args[0])


@builtin("peek", (T.Fn(),), call_site=_peek_call_site)
def _peek(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    callable_value = args[-1]
    return ctx.call(callable_value, list(args[:-1]))


@builtin("dip", (T.Fn(),), call_site=_dip_call_site)
def _dip(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    callable_value = args[-1]
    held = args[-2]
    return (*ctx.call(callable_value, list(args[:-2])), held)


@builtin("fork", (T.Fn(), T.Fn()), call_site=_fork_call_site)
def _fork(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    *call_args, left, right = args
    left_arity = _runtime_callable_arity(left)
    right_arity = _runtime_callable_arity(right)
    left_args = call_args[-left_arity:] if left_arity else []
    right_args = call_args[-right_arity:] if right_arity else []
    return (*ctx.call(left, list(left_args)), *ctx.call(right, list(right_args)))


def _runtime_callable_arity(value: Any) -> int:
    code = getattr(value, "code", None)
    if code is not None:
        return len(getattr(code, "params", ()))
    overloads = getattr(value, "overloads", ())
    if overloads:
        return max(_runtime_callable_arity(overload) for overload in overloads)
    return 0


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


@builtin(
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
def _reduce(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    iterator = iter(args[0])
    try:
        result = next(iterator)
    except StopIteration as exc:
        raise RuntimeError("reduce requires a non-empty list") from exc
    for item in iterator:
        called = ctx.call(args[1], [result, item])
        if len(called) != 1:
            raise RuntimeError("reduce function must return exactly one value")
        result = called[0]
    return (result,)


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

    if _callable_has_element_tag(args[1], "Eager"):
        return (list(mapped_items()),)
    return (LazyList(mapped_items()),)


@builtin(
    "map",
    (
        T.ExactList(T.TypeVariable("Item")),
        T.Fn(),
    ),
    (),
    call_site=_eager_map_call_site,
    element_tags=(EAGER_TAG,),
)
def _map_eager_effect(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    for item in args[0]:
        ctx.call(args[1], [item])
    return ()


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


@builtin("print", (T.V("T"),), (), element_tags=(EAGER_TAG, IO_TAG))
def _print(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    ctx.output(ctx.format_value(args[0]))
    return ()


@builtin("println", (T.V("T"),), (), element_tags=(EAGER_TAG, IO_TAG))
def _println(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    ctx.output(ctx.format_value(args[0]) + "\n")
    return ()


@builtin(
    "panic",
    (T.V("Fault"),),
    (T.Never(),),
    element_tags=(T.ElementTag(Symbol("Panic"), (T.V("Fault"),)),),
)
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
    for name in ("IO", "Random", "Panic", "Memoizable"):
        env.add_property_element_tag(name)
    for name in ("Eager", "Memoized"):
        env.add_companion_element_tag(name)
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
