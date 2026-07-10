"""Built-in elements and relationships for the standard analysis environment."""

# Every implementation below is registered via `@builtin(...)` and only ever
# called dynamically, through the registry, at runtime. Pyright has no way to
# see those call sites, so it flags each one as unused; that's a false
# positive inherent to this registration pattern, not a real dead-code issue.
# pyright: reportUnusedFunction=false

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from decimal import MAX_EMAX, MIN_EMIN, Decimal, localcontext
from itertools import chain, islice
from typing import Any

import valiance.types as T
from valiance.documentation import ElementDocumentation, element_documentation
from valiance.runtime_values import (
    LazyList,
    ObjectValue,
    PanicSignal,
    format_runtime_value,
    is_finite_list_like,
    is_list_like,
    unwrap_runtime_value,
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

BUILTIN_ERROR_TYPES = tuple(
    Symbol(name)
    for name in (
        "Error",
        "ValueError",
        "RangeError",
        "ParseError",
        "DivisionByZeroError",
        "IndexError",
        "KeyError",
        "ShapeError",
        "StateError",
        "IOError",
        "NotFoundError",
        "AlreadyExistsError",
        "PermissionError",
        "ClosedError",
        "TimeoutError",
        "CancelledError",
    )
)

BUILTIN_FAULT_TYPES = tuple(
    Symbol(name)
    for name in (
        "RuntimeFault",
        "ValueFault",
        "RangeFault",
        "ParseFault",
        "DivisionByZeroFault",
        "IndexFault",
        "KeyFault",
        "ShapeFault",
        "StateFault",
        "IOFault",
        "NotFoundFault",
        "AlreadyExistsFault",
        "PermissionFault",
        "ClosedFault",
        "TimeoutFault",
        "CancelledFault",
        "UnwrappedNoneFault",
        "UnwrappedResultFault",
        "DuplicationFault",
        "CleanupFault",
    )
)

TRAIT_IMPLS = (
    (INTEGER, REAL),
    (REAL, NUMBER),
    *((error_type, ERR) for error_type in BUILTIN_ERROR_TYPES),
    *((fault_type, FAULT) for fault_type in BUILTIN_FAULT_TYPES),
    (Symbol("AssertError"), ERR),
    (Symbol("PanicError"), ERR),
)


_BUILTIN_DOCUMENTATION: dict[str, ElementDocumentation] = {
    "dup": element_documentation(
        "Duplicate the top stack value.",
        parameters=(("value", "Value to duplicate."),),
        returns="Two copies of the input value.",
        examples=(("10 dup", "10 10"),),
        category="Stack",
    ),
    "swap": element_documentation(
        "Exchange the two topmost stack values.",
        parameters=(
            ("lower", "Value immediately below the top of the stack."),
            ("upper", "Value at the top of the stack."),
        ),
        returns="The same values in reverse stack order.",
        examples=(("1 2 swap", "2 1"),),
        category="Stack",
    ),
    "top": element_documentation(
        "Return the top stack value unchanged.",
        parameters=(("value", "Value at the top of the stack."),),
        returns="The input value.",
        category="Stack",
    ),
    "peek": element_documentation(
        "Call a function while preserving the values it consumes.",
        description="The callable receives its normal inputs, and those inputs remain beneath the callable's results.",
        parameters=(("operation", "Callable to invoke."),),
        returns="The preserved inputs followed by the callable results.",
        category="Functions",
    ),
    "dip": element_documentation(
        "Call a function beneath one temporarily held stack value.",
        parameters=(
            ("held", "Value temporarily removed while the callable runs."),
            ("operation", "Callable to invoke on the remaining stack."),
        ),
        returns="The callable results followed by the held value.",
        category="Functions",
    ),
    "fork": element_documentation(
        "Apply two callables to the same available inputs.",
        parameters=(
            ("left", "First callable."),
            ("right", "Second callable."),
        ),
        returns="The results of the left callable followed by the results of the right callable.",
        category="Functions",
    ),
    "call": element_documentation(
        "Invoke a callable value.",
        parameters=(("operation", "Callable to invoke."),),
        returns="The values returned by the selected callable overload.",
        category="Functions",
    ),
    "+": element_documentation(
        "Add numbers or concatenate strings.",
        parameters=(
            ("left", "Left numeric or string operand."),
            ("right", "Right operand of the same supported kind."),
        ),
        returns="The numeric sum or concatenated string.",
        examples=(("2 3 +", "5"), ('"Val" "iance" +', "Valiance")),
        category="Arithmetic",
    ),
    "-": element_documentation(
        "Subtract the top numeric operand from the value beneath it.",
        parameters=(("left", "Value to subtract from."), ("right", "Value to subtract.")),
        returns="The numeric difference.",
        examples=(("10 3 -", "7"),),
        category="Arithmetic",
    ),
    "*": element_documentation(
        "Multiply numbers or repeat a string an integer number of times.",
        parameters=(("left", "Left operand."), ("right", "Right operand.")),
        returns="A numeric product or repeated string.",
        examples=(("6 7 *", "42"), ('"ha" 3 *', "hahaha")),
        category="Arithmetic",
    ),
    "%": element_documentation(
        "Return the remainder after numeric division.",
        parameters=(("left", "Dividend."), ("right", "Divisor.")),
        returns="The remainder.",
        examples=(("17 5 %", "2"),),
        category="Arithmetic",
    ),
    "/": element_documentation(
        "Divide numbers or fold a non-empty list with a reducer.",
        description=(
            "Numeric overloads divide the value beneath the top of the stack by the top value.",
            "The list overload, also available as `fold`, uses the first item as the accumulator and applies the reducer to every remaining item.",
        ),
        parameters=(("left_or_values", "Dividend or non-empty list."), ("right_or_reducer", "Divisor or two-input reducer.")),
        returns="The quotient or final accumulated value.",
        category="Arithmetic",
        see_also=("fold",),
    ),
    "double": element_documentation(
        "Multiply a number by two.",
        parameters=(("value", "Number to double."),),
        returns="The doubled number.",
        examples=(("21 double", "42"),),
        category="Arithmetic",
    ),
    "squared": element_documentation(
        "Multiply a number by itself.",
        parameters=(("value", "Number to square."),),
        returns="The square of the number.",
        examples=(("5 squared", "25"),),
        category="Arithmetic",
    ),
    "positive?": element_documentation(
        "Test whether a number is greater than zero.",
        parameters=(("value", "Number to test."),),
        returns="`true` when the number is positive; otherwise `false`.",
        category="Comparison",
    ),
    "==": element_documentation(
        "Test two numbers or two strings for equality.",
        parameters=(("left", "First value."), ("right", "Second value.")),
        returns="A Boolean equality result.",
        category="Comparison",
    ),
    "<": element_documentation(
        "Test whether the left number is less than the right number.",
        parameters=(("left", "First number."), ("right", "Second number.")),
        returns="A Boolean comparison result.",
        category="Comparison",
    ),
    "<=": element_documentation(
        "Test whether the left number is less than or equal to the right number.",
        parameters=(("left", "First number."), ("right", "Second number.")),
        returns="A Boolean comparison result.",
        category="Comparison",
    ),
    ">": element_documentation(
        "Test whether the left number is greater than the right number.",
        parameters=(("left", "First number."), ("right", "Second number.")),
        returns="A Boolean comparison result.",
        category="Comparison",
    ),
    ">=": element_documentation(
        "Test whether the left number is greater than or equal to the right number.",
        parameters=(("left", "First number."), ("right", "Second number.")),
        returns="A Boolean comparison result.",
        category="Comparison",
    ),
    "true": element_documentation(
        "Push the Boolean true value.",
        returns="The `true` Boolean value.",
        category="Boolean",
    ),
    "false": element_documentation(
        "Push the Boolean false value.",
        returns="The `false` Boolean value.",
        category="Boolean",
    ),
    "map": element_documentation(
        "Apply a callable to every item in a list.",
        description="Pure mappings are lazy; mappings whose callable is eager execute immediately and return no list.",
        parameters=(("values", "Input list."), ("operation", "Callable applied to each item.")),
        returns="A list of mapped values, or no value for an eager effect-only callable.",
        category="Collections",
    ),
    "take": element_documentation(
        "Return at most the first requested number of list items.",
        parameters=(("values", "Input list."), ("count", "Non-negative number of items to retain.")),
        returns="A list containing the selected prefix.",
        category="Collections",
    ),
    "length": element_documentation(
        "Return the number of items in a finite list.",
        parameters=(("values", "Finite list whose size is required."),),
        returns="The list length.",
        category="Collections",
        see_also=("len",),
    ),
    "head": element_documentation(
        "Return the first item of a non-empty list.",
        parameters=(("values", "Non-empty input list."),),
        returns="The first list item.",
        category="Collections",
    ),
    "range": element_documentation(
        "Create an inclusive lazy integer range.",
        parameters=(("start", "First integer."), ("stop", "Last integer, included.")),
        returns="A lazy list of integers from start through stop.",
        examples=(("1 4 range", "[1, 2, 3, 4]"),),
        category="Collections",
    ),
    "append": element_documentation(
        "Return a list with one item appended.",
        description="The item and list may appear in either supported stack order.",
        parameters=(("values", "Input list."), ("item", "Item added to the end.")),
        returns="A new list ending with the item.",
        category="Collections",
    ),
    "addAll": element_documentation(
        "Append every item from one list to another list.",
        parameters=(("items", "Items to append."), ("target", "List receiving the items.")),
        returns="A combined list.",
        category="Collections",
    ),
    "join": element_documentation(
        "Join a list of strings with a separator.",
        parameters=(("values", "Strings to join."), ("separator", "Text inserted between adjacent strings.")),
        returns="The joined string.",
        category="Strings",
    ),
    "message": element_documentation(
        "Read the message stored by an error or fault.",
        parameters=(("failure", "An `Err` or `Fault` value."),),
        returns="The failure message string.",
        category="Errors and faults",
        see_also=("getMessage",),
    ),
    "OK": element_documentation(
        "Wrap a successful value in a result.",
        parameters=(("value", "Successful result value."),),
        returns="An `OK` result containing the value.",
        category="Optionals and results",
    ),
    "Some": element_documentation(
        "Wrap a present value in an optional.",
        parameters=(("value", "Present optional value."),),
        returns="A `Some` optional containing the value.",
        category="Optionals and results",
    ),
    "None": element_documentation(
        "Create an empty optional value.",
        returns="The empty `None` optional.",
        category="Optionals and results",
    ),
    "&": element_documentation(
        "Continue an optional or result computation only when a value is present or successful.",
        parameters=(("value", "Optional, result, or recoverable error."), ("operation", "Callable applied to the present or successful value.")),
        returns="The transformed container, while empty or error values pass through unchanged.",
        category="Optionals and results",
    ),
    "?": element_documentation(
        "Unwrap a present optional or successful result with propagation semantics.",
        parameters=(("value", "Optional or result to inspect."),),
        returns="The contained value, or the original empty/error value for propagation.",
        category="Optionals and results",
    ),
    "?!": element_documentation(
        "Unwrap an optional or result and panic when no successful value exists.",
        parameters=(("value", "Optional or result to unwrap."),),
        returns="The contained value.",
        notes="Panics with `UnwrappedNoneFault` or `UnwrappedResultFault` on failure.",
        category="Optionals and results",
    ),
    "print": element_documentation(
        "Write a value without a trailing newline.",
        parameters=(("value", "Value to format and write."),),
        returns="No stack values.",
        category="Input and output",
    ),
    "println": element_documentation(
        "Write a value followed by a newline.",
        parameters=(("value", "Value to format and write."),),
        returns="No stack values.",
        category="Input and output",
    ),
    "panic": element_documentation(
        "Abort normal execution by raising a fault value.",
        parameters=(("fault", "Value implementing `Fault`."),),
        returns="Never returns normally.",
        category="Errors and faults",
    ),
    "toString": element_documentation(
        "Format a value using Valiance runtime display syntax.",
        parameters=(("value", "Value to format."),),
        returns="The formatted string.",
        category="Strings",
    ),
    "or": element_documentation(
        "Choose a fallback string or optional value.",
        parameters=(("value", "Preferred string or optional."), ("fallback", "Value used when the preferred value is empty.")),
        returns="The preferred non-empty value, otherwise the fallback.",
        category="Optionals and results",
    ),
}


@dataclass(frozen=True)
class RuntimeContext:
    """Runtime services available to built-in element implementations."""

    output: Callable[[str], None]
    call: Callable[[Any, list[Any]], list[Any]]
    format_value: Callable[[Any], str] = format_runtime_value
    call_overload: Callable[[Any, list[Any], int], list[Any]] | None = None
    static_values: tuple[Any, ...] = ()


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
    documentation: ElementDocumentation | None = None
    canonical_name: Symbol | None = None

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
# to distinct functions when they don't.
# --------------------------------------------------------------------------

_REGISTRY: dict[str, list[BuiltinOverload]] = {}
_DATA_TAG_REGISTRY: dict[str, T.TagKind] = {}
_DOCUMENTATION_REGISTRY: dict[str, ElementDocumentation] = {}
_CANONICAL_NAME_REGISTRY: dict[str, str] = {}


def builtin(
    name: str | Symbol,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...] = (),
    generic_constraints: tuple[T.GenericConstraint, ...] = (),
    call_site: Callable[..., T.Overload | None] | None = None,
    element_tags: tuple[T.ElementTag, ...] = (),
    data_tags: tuple[tuple[str | Symbol, T.TagKind], ...] = (),
    param_names: tuple[str | Symbol | None, ...] = (),
    documentation: ElementDocumentation | None = None,
):
    """Register one overload of `name`, implemented by the decorated function."""
    normalized_param_names = tuple(
        Symbol(param_name) if isinstance(param_name, str) else param_name
        for param_name in param_names
    )
    if normalized_param_names and len(normalized_param_names) != len(params):
        raise ValueError("param_names must match the number of parameters")

    def register(fn: RuntimeImpl) -> RuntimeImpl:
        """Register the decorated callable for the built-in catalogue and runtime."""
        for tag_name, tag_kind in data_tags:
            _DATA_TAG_REGISTRY[_name_key(tag_name)] = tag_kind
        overload = BuiltinOverload(
            T.Overload(
                params,
                returns,
                generic_constraints,
                param_names=normalized_param_names,
                call_site_body=call_site,
                element_tags=frozenset(element_tags),
            ),
            fn,
        )

        aliases: tuple[str, ...] = getattr(fn, _ALIAS_ATTR, ())
        canonical_name = _name_key(name)
        effective_documentation = documentation or _BUILTIN_DOCUMENTATION.get(
            canonical_name
        )
        names = dict.fromkeys((canonical_name, *aliases))

        for key in names:
            _REGISTRY.setdefault(key, []).append(overload)
            _CANONICAL_NAME_REGISTRY.setdefault(key, canonical_name)
            if effective_documentation is not None:
                existing = _DOCUMENTATION_REGISTRY.get(key)
                if existing is not None and existing != effective_documentation:
                    raise ValueError(
                        f"conflicting documentation registered for built-in {key!r}"
                    )
                _DOCUMENTATION_REGISTRY[key] = effective_documentation

        return fn

    return register


_ALIAS_ATTR = "__builtin_aliases__"


def _name_key(name: str | Symbol) -> str:
    """Build the comparison key for name for the built-in catalogue and runtime."""
    return name.text if isinstance(name, Symbol) else name


def alias(*names: str | Symbol):
    """Add alternative names to all @builtin overloads on this function."""

    if not names:
        raise ValueError("alias() requires at least one name")

    keys = tuple(dict.fromkeys(_name_key(name) for name in names))

    def decorate(fn: RuntimeImpl) -> RuntimeImpl:
        """Register the decorated callable for the built-in catalogue and runtime."""
        existing: tuple[str, ...] = getattr(fn, _ALIAS_ATTR, ())
        setattr(fn, _ALIAS_ATTR, tuple(dict.fromkeys((*existing, *keys))))
        return fn

    return decorate


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

_MISSING = object()
EAGER_TAG = T.ElementTag(Symbol("Eager"))
IO_TAG = T.ElementTag(Symbol("IO"))


def _truth(value: bool) -> Decimal:
    """Compute truth for the built-in catalogue and runtime."""
    return Decimal(1) if value else Decimal(0)


def _is_ok_value(value: Any) -> bool:
    """Return whether the value is ok value."""
    return (
        isinstance(value, ObjectValue)
        and value.type_name == "OK"
        and "value" in value.fields
    )


def _is_err_value(value: Any) -> bool:
    """Return whether the value is err value."""
    return isinstance(value, ObjectValue) and (
        value.type_name == "Err"
        or value.type_name.endswith("Error")
        or value.type_name.rsplit(".", 1)[-1].endswith("Error")
    )


def _is_none_value(value: Any) -> bool:
    """Return whether the value is none value."""
    return value is None or (
        isinstance(value, ObjectValue) and value.type_name.rsplit(".", 1)[-1] == "None"
    )


def _present_value(value: Any) -> Any:
    """Compute present value for the built-in catalogue and runtime."""
    if not isinstance(value, ObjectValue):
        return _MISSING
    if value.type_name == "Some" or value.type_name.rsplit(".", 1)[-1] == "Some":
        return value.fields.get("value", _MISSING)
    return _MISSING


def _runtime_assignable(value: Any, typ: T.Type) -> bool:
    """Return the Boolean result of runtime assignable for the built-in catalogue and runtime."""
    value = unwrap_runtime_value(value)
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return True
    if isinstance(typ, T.ExactType):
        return _runtime_assignable(value, typ.inner)
    if isinstance(typ, T.TaggedType):
        if any(
            tag.absent and tag.name == "infinite" and tag.depth == 0 for tag in typ.tags
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
    """Return the Boolean result of runtime vector arg matches for the built-in catalogue and runtime."""
    typ = T.normalize(typ)
    if isinstance(typ, T.ExactType):
        return _runtime_assignable(value, typ.inner)
    if is_list_like(value) and not _is_collection_parameter(typ):
        return True
    return _runtime_assignable(value, typ)


def _is_collection_parameter(typ: T.Type) -> bool:
    """Return whether the value is collection parameter."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _is_collection_parameter(typ.inner)
    return isinstance(typ, T.CollectionType)


def _callable_has_element_tag(value: Any, tag: str) -> bool:
    """Return the Boolean result of callable has element tag for the built-in catalogue and runtime."""
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
    """Collect the overloads for callable for the built-in catalogue and runtime."""
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
    """Apply callable for the built-in catalogue and runtime."""
    applied = T.try_apply_overload(overload, args).applied
    if applied is None:
        return None
    return _CallableApplication(
        overload,
        applied,
        T.Fn(args, applied.actual_returns, overload.element_tags),
    )


def _callable_applications(
    typ: T.Type,
    args: tuple[T.Type, ...],
) -> Iterator[_CallableApplication]:
    """Compute callable applications for the built-in catalogue and runtime."""
    for overload in _callable_overloads(typ):
        application = _apply_callable(overload, args)
        if application is not None:
            yield application


def _first_callable_application(
    typ: T.Type,
    args: tuple[T.Type, ...],
) -> _CallableApplication | None:
    """Compute first callable application for the built-in catalogue and runtime."""
    return next(_callable_applications(typ, args), None)


def _peek_call_site(call_params: tuple[T.Type, ...]) -> T.Overload | None:
    """Compute peek call site for the built-in catalogue and runtime."""
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
    """Compute dip call site for the built-in catalogue and runtime."""
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
    """Compute fork call site for the built-in catalogue and runtime."""
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


def _call_call_site(call_params: tuple[T.Type, ...]) -> T.Overload | None:
    """Invoke call site for the built-in catalogue and runtime."""
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
                call_site_body=arity,
            )
    return None


def _eager_map_call_site(call_params: tuple[T.Type, ...]) -> T.Overload | None:
    """Compute eager map call site for the built-in catalogue and runtime."""
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
    """Implement the `dup` built-in runtime overload."""
    return (args[0], args[0])


@builtin("swap", (T.V("A"), T.V("B")), (T.V("B"), T.V("A")))
def _swap(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `swap` built-in runtime overload."""
    return (args[1], args[0])


@builtin("top", (T.V("T"),), (T.V("T"),))
def _top(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `top` built-in runtime overload."""
    return (args[0],)


@builtin("peek", (T.Fn(),), call_site=_peek_call_site)
def _peek(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `peek` built-in runtime overload."""
    callable_value = args[-1]
    return ctx.call(callable_value, list(args[:-1]))


@builtin("dip", (T.Fn(),), call_site=_dip_call_site)
def _dip(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `dip` built-in runtime overload."""
    if len(args) < 2:
        raise RuntimeError("dip requires a held value beneath its callable")
    callable_value = args[-1]
    held = args[-2]
    return (*ctx.call(callable_value, list(args[:-2])), held)


@builtin("fork", (T.Fn(), T.Fn()), call_site=_fork_call_site)
def _fork(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `fork` built-in runtime overload."""
    *call_args, left, right = args
    left_arity = _runtime_callable_arity(left)
    right_arity = _runtime_callable_arity(right)
    left_args = call_args[-left_arity:] if left_arity else []
    right_args = call_args[-right_arity:] if right_arity else []
    return (*ctx.call(left, list(left_args)), *ctx.call(right, list(right_args)))


@builtin("call", (T.Fn(),), call_site=_call_call_site)
def _call(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `call` built-in runtime overload."""
    if _runtime_callable_value(args[-1]):
        callable_value = args[-1]
        call_args = list(args[:-1])
    else:
        callable_value = args[0]
        call_args = list(args[1:])
    selected = ctx.static_values[0] if ctx.static_values else None
    if isinstance(selected, int) and ctx.call_overload is not None:
        return tuple(ctx.call_overload(callable_value, call_args, selected))
    return tuple(ctx.call(callable_value, call_args))


def _runtime_callable_arity(value: Any) -> int:
    """Determine the required arity for runtime callable for the built-in catalogue and runtime."""
    code = getattr(value, "code", None)
    if code is not None:
        return len(getattr(code, "params", ()))
    overloads = getattr(value, "overloads", ())
    if overloads:
        return max(_runtime_callable_arity(overload) for overload in overloads)
    return 0


def _runtime_callable_value(value: Any) -> bool:
    """Return the Boolean result of runtime callable value for the built-in catalogue and runtime."""
    return getattr(value, "code", None) is not None or bool(
        getattr(value, "overloads", ())
    )


# --------------------------------------------------------------------------
# Arithmetic
# --------------------------------------------------------------------------


def _decimal_addition_precision(left: Decimal, right: Decimal) -> int:
    """Compute decimal addition precision for the built-in catalogue and runtime."""
    highest_place = max(left.adjusted(), right.adjusted())
    lowest_place = min(left.as_tuple().exponent, right.as_tuple().exponent)
    return max(1, highest_place - lowest_place + 2)


def _decimal_multiplication_precision(left: Decimal, right: Decimal) -> int:
    """Compute decimal multiplication precision for the built-in catalogue and runtime."""
    return max(1, len(left.as_tuple().digits) + len(right.as_tuple().digits))


def _decimal_division_precision(left: Decimal, right: Decimal) -> int:
    """Compute decimal division precision for the built-in catalogue and runtime."""
    return max(1, len(left.as_tuple().digits) + len(right.as_tuple().digits))


def _decimal_binary(
    left: Decimal,
    right: Decimal,
    operation: Callable[[Decimal, Decimal], Decimal],
    precision: int,
) -> Decimal:
    """Compute decimal binary for the built-in catalogue and runtime."""
    with localcontext() as context:
        context.prec = max(context.prec, precision)
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        return operation(left, right)


def _decimal_add(left: Decimal, right: Decimal) -> Decimal:
    """Compute decimal add for the built-in catalogue and runtime."""
    return _decimal_binary(
        left,
        right,
        lambda a, b: a + b,
        _decimal_addition_precision(left, right),
    )


def _decimal_subtract(left: Decimal, right: Decimal) -> Decimal:
    """Compute decimal subtract for the built-in catalogue and runtime."""
    return _decimal_binary(
        left,
        right,
        lambda a, b: a - b,
        _decimal_addition_precision(left, right),
    )


def _decimal_multiply(left: Decimal, right: Decimal) -> Decimal:
    """Compute decimal multiply for the built-in catalogue and runtime."""
    return _decimal_binary(
        left,
        right,
        lambda a, b: a * b,
        _decimal_multiplication_precision(left, right),
    )


def _decimal_remainder(left: Decimal, right: Decimal) -> Decimal:
    """Compute decimal remainder for the built-in catalogue and runtime."""
    return _decimal_binary(
        left,
        right,
        lambda a, b: a % b,
        _decimal_addition_precision(left, right),
    )


def _decimal_divide(left: Decimal, right: Decimal) -> Decimal:
    """Compute decimal divide for the built-in catalogue and runtime."""
    return _decimal_binary(
        left,
        right,
        lambda a, b: a / b,
        _decimal_division_precision(left, right),
    )


@builtin("+", (T.Integer, T.Integer), (T.Integer,))
@builtin("+", (T.Real, T.Real), (T.Real,))
@builtin("+", (T.Real, T.Integer), (T.Real,))
@builtin("+", (T.Integer, T.Real), (T.Real,))
@builtin("+", (T.Number, T.Number), (T.Number,))
@builtin("+", (T.String, T.String), (T.String,))
def _plus(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `+`, `+`, `+`, `+`, `+`, `+` built-in runtime overloads."""
    if isinstance(args[0], Decimal):
        return (_decimal_add(args[0], args[1]),)
    return (args[0] + args[1],)


@builtin("-", (T.Integer, T.Integer), (T.Integer,))
@builtin("-", (T.Real, T.Real), (T.Real,))
@builtin("-", (T.Real, T.Integer), (T.Real,))
@builtin("-", (T.Integer, T.Real), (T.Real,))
@builtin("-", (T.Number, T.Number), (T.Number,))
def _minus(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `-`, `-`, `-`, `-`, `-` built-in runtime overloads."""
    return (_decimal_subtract(args[0], args[1]),)


@builtin("*", (T.Integer, T.Integer), (T.Integer,))
@builtin("*", (T.Real, T.Real), (T.Real,))
@builtin("*", (T.Real, T.Integer), (T.Real,))
@builtin("*", (T.Integer, T.Real), (T.Real,))
@builtin("*", (T.Number, T.Number), (T.Number,))
def _multiply(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `*`, `*`, `*`, `*`, `*` built-in runtime overloads."""
    return (_decimal_multiply(args[0], args[1]),)


@builtin("*", (T.Integer, T.String), (T.String,))
def _string_repeat(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `*` built-in runtime overload."""
    return (int(args[0]) * args[1],)


@builtin("*", (T.String, T.Integer), (T.String,))
def _string_repeat_reverse(
    args: tuple[Any, ...], ctx: RuntimeContext
) -> tuple[Any, ...]:
    """Implement the `*` built-in runtime overload."""
    return (args[0] * int(args[1]),)


@builtin("%", (T.Integer, T.Integer), (T.Integer,))
@builtin("%", (T.Real, T.Real), (T.Real,))
@builtin("%", (T.Real, T.Integer), (T.Real,))
@builtin("%", (T.Integer, T.Real), (T.Real,))
@builtin("%", (T.Number, T.Number), (T.Number,))
def _percent(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `%`, `%`, `%`, `%`, `%` built-in runtime overloads."""
    return (_decimal_remainder(args[0], args[1]),)


@builtin("/", (T.Integer, T.Integer), (T.Real,))
@builtin("/", (T.Real, T.Real), (T.Real,))
@builtin("/", (T.Real, T.Integer), (T.Real,))
@builtin("/", (T.Integer, T.Real), (T.Real,))
@builtin("/", (T.Number, T.Number), (T.Number,))
def _slash(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `/`, `/`, `/`, `/`, `/` built-in runtime overloads."""
    return (_decimal_divide(args[0], args[1]),)


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
@alias("fold")
def _fold(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `/` built-in runtime overload."""
    iterator = iter(args[0])
    try:
        result = next(iterator)
    except StopIteration as exc:
        raise RuntimeError("reduce requires a non-empty list") from exc
    for item in iterator:
        called = ctx.call(args[1], [result, item])
        result = called[0]
    return (result,)


@builtin("double", (T.Number,), (T.Number,))
def _double(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `double` built-in runtime overload."""
    return (_decimal_multiply(args[0], Decimal(2)),)


@builtin("squared", (T.Number,), (T.Number,))
def _squared(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `squared` built-in runtime overload."""
    return (_decimal_multiply(args[0], args[0]),)


@builtin("positive?", (T.Number,), (T.Boolean,))
def _is_positive(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `positive?` built-in runtime overload."""
    return (_truth(args[0] > 0),)


# --------------------------------------------------------------------------
# Comparisons
# --------------------------------------------------------------------------


@builtin("==", (T.Number, T.Number), (T.Boolean,))
@builtin("==", (T.String, T.String), (T.Boolean,))
def _equals(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `==`, `==` built-in runtime overloads."""
    return (_truth(args[0] == args[1]),)


@builtin("<", (T.Number, T.Number), (T.Boolean,))
def _less(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `<` built-in runtime overload."""
    return (_truth(args[0] < args[1]),)


@builtin("<=", (T.Number, T.Number), (T.Boolean,))
def _less_equals(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `<=` built-in runtime overload."""
    return (_truth(args[0] <= args[1]),)


@builtin(">", (T.Number, T.Number), (T.Boolean,))
def _greater(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `>` built-in runtime overload."""
    return (_truth(args[0] > args[1]),)


@builtin(">=", (T.Number, T.Number), (T.Boolean,))
def _greater_equals(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `>=` built-in runtime overload."""
    return (_truth(args[0] >= args[1]),)


@builtin("true", (), (T.Boolean,))
def _true(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `true` built-in runtime overload."""
    return (Decimal(1),)


@builtin("false", (), (T.Boolean,))
def _false(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `false` built-in runtime overload."""
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
    """Implement the `map` built-in runtime overload."""
    def mapped_items():
        """Collect the items for mapped for the built-in catalogue and runtime."""
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
    """Implement the `map` built-in runtime overload."""
    for item in args[0]:
        ctx.call(args[1], [item])
    return ()


@builtin(
    "take",
    (T.ExactList(T.TypeVariable("Item")), T.Integer),
    (T.ExactList(T.TypeVariable("Item")),),
)
def _take(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `take` built-in runtime overload."""
    lst, n = args
    if n < 0:
        raise RuntimeError("take requires a non-negative integer")
    if isinstance(lst, LazyList):
        return (LazyList(islice(iter(lst), int(n))),)
    return (lst[: int(n)],)


@builtin(
    "length",
    (T.WithoutTag(T.ExactList(T.TypeVariable("Item")), "infinite"),),
    (T.Number,),
)
@alias("len")
def _length(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `length` built-in runtime overload."""
    return (Decimal(len(args[0])),)


@builtin("head", (T.ExactList(T.TypeVariable("Item")),), (T.TypeVariable("Item"),))
def _head(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `head` built-in runtime overload."""
    for item in args[0]:
        return (item,)
    raise RuntimeError("head requires a non-empty list")


@builtin("range", (T.Integer, T.Integer), (T.ExactList(T.Integer),))
def _range(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `range` built-in runtime overload."""
    start, stop = args
    return (LazyList(Decimal(item) for item in range(int(start), int(stop) + 1)),)


@builtin(
    "append",
    (T.ExactList(T.TypeVariable("Item")), T.TypeVariable("Item")),
    (T.ExactList(T.TypeVariable("Item")),),
)
@builtin(
    "append",
    (T.TypeVariable("Item"), T.ExactList(T.TypeVariable("Item"))),
    (T.ExactList(T.TypeVariable("Item")),),
)
def _append(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `append`, `append` built-in runtime overloads."""
    if is_list_like(args[0]):
        return ([*args[0], args[1]],)
    return ([*args[1], args[0]],)


@builtin(
    "addAll",
    (
        T.ExactList(T.TypeVariable("Item")),
        T.ExactList(T.TypeVariable("Item")),
    ),
    (T.ExactList(T.TypeVariable("Item")),),
)
def _add_all(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `addAll` built-in runtime overload."""
    items, target = args
    if isinstance(items, LazyList) or isinstance(target, LazyList):
        return (LazyList(chain(target, items)),)
    return ([*target, *items],)


@builtin("join", (T.ExactList(T.String), T.String), (T.String,))
def _join(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `join` built-in runtime overload."""
    values, separator = args
    return (separator.join(str(item) for item in values),)


# --------------------------------------------------------------------------
# Recoverable errors and panic faults
# --------------------------------------------------------------------------


def _message_type_documentation(type_name: Symbol) -> ElementDocumentation:
    """Build documentation for a generated built-in error or fault constructor."""
    is_fault = type_name in BUILTIN_FAULT_TYPES
    kind = "fault" if is_fault else "recoverable error"
    return element_documentation(
        f"Construct a {type_name.text} {kind} value.",
        parameters=(("message", "Human-readable explanation of the failure."),),
        returns=f"A `{type_name.text}` value containing the message.",
        examples=((f'{type_name.text}("operation failed")', None),),
        category="Faults" if is_fault else "Errors",
        notes=(
            "Fault values may be passed to `panic`."
            if is_fault
            else "Error values represent recoverable failures."
        ),
        see_also=("message",),
    )


def _register_builtin_message_type(type_name: Symbol) -> None:
    """Register builtin message type for the built-in catalogue and runtime."""
    @builtin(
        type_name,
        (T.String,),
        (T.N(type_name),),
        param_names=("message",),
        documentation=_message_type_documentation(type_name),
    )
    def construct(
        args: tuple[Any, ...],
        ctx: RuntimeContext,
        *,
        _type_name: Symbol = type_name,
    ) -> tuple[Any, ...]:
        """Compute construct for the built-in catalogue and runtime."""
        return (ObjectValue(_type_name.text, {"message": args[0]}),)


for _message_type in (*BUILTIN_ERROR_TYPES, *BUILTIN_FAULT_TYPES):
    _register_builtin_message_type(_message_type)


@builtin("message", (T.N(ERR),), (T.String,))
@builtin("message", (T.N(FAULT),), (T.String,))
@alias("getMessage")
def _failure_message(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `message`, `message` built-in runtime overloads."""
    failure = args[0]
    if not isinstance(failure, ObjectValue) or "message" not in failure.fields:
        raise RuntimeError("Err or Fault value has no message field")
    return (failure.fields["message"],)


# --------------------------------------------------------------------------
# Optionals and results
# --------------------------------------------------------------------------


@builtin("OK", (T.V("T"),), (T.OKType(T.V("T")),))
def _ok(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `OK` built-in runtime overload."""
    return (ObjectValue("OK", {"value": args[0]}),)


@builtin("Some", (T.V("T"),), (T.Some(T.V("T")),))
def _some(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `Some` built-in runtime overload."""
    return (ObjectValue("Some", {"value": args[0]}),)


@builtin("None", (), (T.NoneType(),))
def _none(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `None` built-in runtime overload."""
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
    """Implement the `&`, `&`, `&` built-in runtime overloads."""
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
    """Implement the `?`, `?` built-in runtime overloads."""
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
    """Implement the `?!`, `?!` built-in runtime overloads."""
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
    """Implement the `print` built-in runtime overload."""
    ctx.output(ctx.format_value(args[0]))
    return ()


@builtin("println", (T.V("T"),), (), element_tags=(EAGER_TAG, IO_TAG))
def _println(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `println` built-in runtime overload."""
    ctx.output(ctx.format_value(args[0]) + "\n")
    return ()


@builtin(
    "panic",
    (T.TypeVariable("F"),),
    (T.Never(),),
    (T.GenericConstraint("F", T.N(FAULT)),),
    element_tags=(
        T.ElementTag(Symbol("Panic"), (T.TypeVariable("F"),)),
    ),
)
def _panic(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `panic` built-in runtime overload."""
    raise PanicSignal(args[0])


# --------------------------------------------------------------------------
# Strings
# --------------------------------------------------------------------------


@builtin("toString", (T.V("T"),), (T.String,))
def _to_string(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `toString` built-in runtime overload."""
    return (ctx.format_value(args[0]),)


@builtin("or", (T.String, T.String), (T.String,))
def _or_string(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `or` built-in runtime overload."""
    return (args[0] or args[1],)


@builtin(
    "or",
    (
        T.optional(T.TypeVariable("T")),
        T.optional(T.TypeVariable("T")),
    ),
    (T.optional(T.TypeVariable("T")),),
)
def _or_optional(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `or` built-in runtime overload."""
    return (args[1] if _is_none_value(args[0]) else args[0],)


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------


def _all_elements() -> tuple[BuiltinElement, ...]:
    """Compute all elements for the built-in catalogue and runtime."""
    return tuple(
        BuiltinElement(
            Symbol(name),
            tuple(overloads),
            _DOCUMENTATION_REGISTRY.get(name),
            (
                Symbol(canonical)
                if (canonical := _CANONICAL_NAME_REGISTRY.get(name, name)) != name
                else None
            ),
        )
        for name, overloads in _REGISTRY.items()
    )


# Public, for callers that want the full built-in catalogue directly (e.g.
# `from valiance.analysis.builtins import BUILTIN_ELEMENTS`). This is derived
# from `_REGISTRY` once, at import time, after every `@builtin(...)` call above has run
# -- it is not hand-maintained.
BUILTIN_ELEMENTS: tuple[BuiltinElement, ...] = _all_elements()


def default_environment() -> T.Environment:
    """Build an environment populated with Valiance's built-in elements."""
    env = T.Environment()
    for name in ("IO", "Random", "Panic", "Memoizable"):
        env.add_property_element_tag(name)
    for name in ("Eager", "Memoized"):
        env.add_companion_element_tag(name)
    _DATA_TAG_REGISTRY.setdefault("infinite", T.TagKind.CONSTRUCTED)
    _DATA_TAG_REGISTRY.setdefault("boolean", T.TagKind.COMPUTED)
    for name, kind in _DATA_TAG_REGISTRY.items():
        env.define_tag(Symbol(name), kind)
    env.define_trait(ERR)
    env.define_trait(FAULT)
    message_attribute = T.ObjectAttribute(Symbol("message"), T.String)
    for message_type in (*BUILTIN_ERROR_TYPES, *BUILTIN_FAULT_TYPES):
        env.define_object(message_type, (message_attribute,))
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
