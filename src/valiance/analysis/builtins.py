"""Built-in elements and relationships for the standard analysis environment."""

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
    is_finite_list_like,
    is_list_like,
)
from valiance.symbols import Symbol

INTEGER = Symbol("Integer")
NUMBER = Symbol("Number")
REAL = Symbol("Real")
STRING = Symbol("String")
ERR = Symbol("Err")
FAULT = Symbol("Fault")
OK = Symbol("OK")
RESULT = Symbol("Result")
NONE = Symbol("None")
SOME = Symbol("Some")

DUP = Symbol("dup")
AMPERSAND = Symbol("&")
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
PANIC = Symbol("panic")
QUESTION = Symbol("?")
QUESTION_BANG = Symbol("?!")

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
    generic_constraints: tuple[T.GenericConstraint, ...] = (),
) -> BuiltinOverload:
    """Declare one stack-effect overload."""
    return BuiltinOverload(
        T.Overload(params, returns, generic_constraints),
        implementation,
    )


def _binary(func: Callable[[Any, Any], Any]) -> RuntimeImpl:
    return lambda args, ctx: (func(args[0], args[1]),)


def _comparison(func: Callable[[Any, Any], bool]) -> RuntimeImpl:
    return lambda args, ctx: (_truth(func(args[0], args[1])),)


def _print(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    ctx.output(_format_value(args[0]))
    return ()


def _println(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    ctx.output(_format_value(args[0]) + "\n")
    return ()


def _map(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    if not is_list_like(args[0]):
        raise RuntimeError("map requires a list")

    def mapped_items():
        for item in args[0]:
            mapped = ctx.call(args[1], [item])
            if len(mapped) != 1:
                raise RuntimeError("map function must return exactly one value")
            yield mapped[0]

    if is_finite_list_like(args[0]):
        return (list(mapped_items()),)
    return (LazyList(mapped_items()),)


def _length(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    if not is_finite_list_like(args[0]):
        raise RuntimeError("length requires a finite list")
    return (Decimal(len(args[0])),)


def _head(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    if not is_list_like(args[0]):
        raise RuntimeError("head requires a list")
    for item in args[0]:
        return (item,)
    raise RuntimeError("head requires a non-empty list")


def _panic(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    raise PanicSignal(args[0])


def _ok(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (ObjectValue("OK", {"value": args[0]}),)


def _and_then(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    value, callable_value = args
    if _is_none_value(value):
        return (value,)
    present = _present_value(value)
    if present is not _MISSING:
        called = ctx.call(callable_value, [present])
        if len(called) != 1:
            raise RuntimeError("& function must return exactly one value")
        return (called[0],)
    if _is_ok_value(value):
        called = ctx.call(callable_value, [value.fields["value"]])
        if len(called) != 1:
            raise RuntimeError("& function must return exactly one value")
        result = called[0]
        if _is_ok_value(result) or _is_err_value(result):
            return (result,)
        return (_ok((result,), ctx)[0],)
    if _is_err_value(value):
        return (value,)
    raise RuntimeError("& requires an optional or Result value")


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


def _some(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (ObjectValue("Some", {"value": args[0]}),)


def _none(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (ObjectValue("None", {}),)


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


def _format_value(value: Any) -> str:
    if isinstance(value, Decimal):
        if value == value.to_integral_value():
            return format(value.quantize(Decimal(1)), "f")
        return format(value.normalize(), "f")
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "[" + ", ".join(_format_value(item) for item in value) + "]"
    if is_list_like(value):
        return "<lazy list>"
    if isinstance(value, tuple):
        return "(" + ", ".join(_format_value(item) for item in value) + ")"
    if isinstance(value, dict):
        items = ", ".join(
            f"{_format_value(key)}: {_format_value(item)}"
            for key, item in value.items()
        )
        return "{" + items + "}"
    if isinstance(value, ObjectValue):
        items = ", ".join(
            f"{name}: {_format_value(item)}" for name, item in value.fields.items()
        )
        return f"{_object_type_name(value)}{{{items}}}"
    return str(value)


def _object_type_name(value: ObjectValue) -> str:
    if not value.type_args:
        return value.type_name
    return f"{value.type_name}[{', '.join(value.type_args)}]"


_MISSING = object()


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
        OK,
        overload((T.V("T"),), (T.OKType(T.V("T")),), _ok),
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
                T.ExactList(T.TypeVariable("Item")),
                T.Fn(
                    (T.TypeVariable("Item"), T.TypeVariable("Item")),
                    (T.TypeVariable("Item"),),
                ),
            ),
            (T.TypeVariable("Item"),),
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
                T.ExactList(T.TypeVariable("Item")),
                T.Fn((T.TypeVariable("Item"),), (T.TypeVariable("Mapped"),)),
            ),
            (T.ExactList(T.TypeVariable("Mapped")),),
            _map,
        ),
    ),
    element(
        AMPERSAND,
        overload(
            (
                T.optional(T.TypeVariable("T")),
                T.Fn((T.TypeVariable("T"),), (T.TypeVariable("U"),)),
            ),
            (T.optional(T.TypeVariable("U")),),
            _and_then,
        ),
        overload(
            (
                T.Result(T.TypeVariable("T"), T.TypeVariable("E")),
                T.Fn((T.TypeVariable("T"),), (T.TypeVariable("U"),)),
            ),
            (T.Result(T.TypeVariable("U"), T.TypeVariable("E")),),
            _and_then,
        ),
        overload(
            (
                T.TypeVariable("E"),
                T.Fn((T.TypeVariable("T"),), (T.TypeVariable("U"),)),
            ),
            (T.TypeVariable("E"),),
            _and_then,
            (T.GenericConstraint("E", T.N(ERR)),),
        ),
    ),
    element(
        QUESTION,
        overload((T.optional(T.TypeVariable("T")),), (T.TypeVariable("T"),), _question),
        overload(
            (T.Result(T.TypeVariable("T"), T.TypeVariable("E")),),
            (T.TypeVariable("T"),),
            _question,
        ),
    ),
    element(
        QUESTION_BANG,
        overload(
            (T.optional(T.TypeVariable("T")),),
            (T.TypeVariable("T"),),
            _question_bang,
        ),
        overload(
            (T.Result(T.TypeVariable("T"), T.TypeVariable("E")),),
            (T.TypeVariable("T"),),
            _question_bang,
        ),
    ),
    element(
        LENGTH,
        overload(
            (T.WithoutTag(T.ExactList(T.TypeVariable("Item")), "infinite"),),
            (T.Number,),
            _length,
        ),
    ),
    element(
        HEAD,
        overload(
            (T.ExactList(T.TypeVariable("Item")),),
            (T.TypeVariable("Item"),),
            _head,
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
        PANIC,
        overload((T.V("Fault"),), (T.Never(),), _panic),
    ),
    element(
        PRINT,
        overload((T.V("T"),), (), _print),
    ),
    element(
        PRINTLN,
        overload((T.V("T"),), (), _println),
    ),
    element(
        SOME,
        overload((T.V("T"),), (T.Some(T.V("T")),), _some),
    ),
    element(
        NONE,
        overload((), (T.NoneType(),), _none),
    ),
)


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
