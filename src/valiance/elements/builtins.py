"""Built-in elements and relationships for the standard analysis environment."""

# Every implementation below is registered via `@builtin(...)` and only ever
# called dynamically, through the registry, at runtime. Pyright has no way to
# see those call sites, so it flags each one as unused; that's a false
# positive inherent to this registration pattern, not a real dead-code issue.
# pyright: reportUnusedFunction=false

from __future__ import annotations

import builtins as python_builtins
import operator
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import chain, cycle, groupby, islice
from typing import Any

import valiance.types as T
from valiance.elements.documentation import ElementDocumentation, element_documentation
from valiance.runtime.runtime_values import (
    LazyList,
    ObjectValue,
    PanicSignal,
    format_runtime_value,
    is_finite_list_like,
    is_list_like,
    unwrap_runtime_value,
    RuntimeNumber,
)
from valiance.types.symbols import Symbol

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
    "both": element_documentation(
        "Apply one callable to two consecutive groups of stack values.",
        description=(
            "If the callable takes n inputs, both consumes two groups of n values.",
            "The lower group is called first, followed by the upper group.",
        ),
        parameters=(("operation", "Callable applied to each input group."),),
        returns="The first call's results followed by the second call's results.",
        category="Functions",
    ),
    "correspond": element_documentation(
        "Apply two callables to two distinct consecutive groups of stack values.",
        description=(
            "The first callable consumes the lower group and the second "
            "callable consumes the upper group.",
            "The callables may have different input and output arities.",
        ),
        parameters=(
            ("lower", "Callable applied to the lower input group."),
            ("upper", "Callable applied to the upper input group."),
        ),
        returns=(
            "The lower callable's results followed by the upper callable's " "results."
        ),
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
        parameters=(
            ("left", "Value to subtract from."),
            ("right", "Value to subtract."),
        ),
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
        parameters=(
            ("left_or_values", "Dividend or non-empty list."),
            ("right_or_reducer", "Divisor or two-input reducer."),
        ),
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
    "**": element_documentation(
        "Raise a number to a numeric power.",
        parameters=(("base", "Number to raise."), ("exponent", "Power to apply.")),
        returns="The exponentiated number.",
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
    "===": element_documentation(
        "Test any two values for structural equality.",
        parameters=(("left", "First value."), ("right", "Second value.")),
        returns="A Boolean equality result.",
        category="Comparison",
    ),
    "in": element_documentation(
        "Test whether a value occurs in a collection or string.",
        parameters=(("needle", "Value to find."), ("haystack", "Value to search.")),
        returns="A Boolean membership result.",
        category="Comparison",
    ),
    "numeric?": element_documentation(
        "Test whether a string is a valid base-ten integer.",
        parameters=(("value", "String to inspect."),),
        returns="A Boolean result.",
        category="Strings",
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
        parameters=(
            ("values", "Input list."),
            ("operation", "Callable applied to each item."),
        ),
        returns="A list of mapped values, or no value for an eager effect-only callable.",
        category="Collections",
    ),
    "take": element_documentation(
        "Return at most the first requested number of list items.",
        parameters=(
            ("values", "Input list."),
            ("count", "Non-negative number of items to retain."),
        ),
        returns="A list containing the selected prefix.",
        category="Collections",
    ),
    "length": element_documentation(
        "Return the number of items in a finite list or string.",
        parameters=(("values", "Finite list or string whose size is required."),),
        returns="The value length as an `Integer`.",
        category="Collections",
        see_also=("len",),
    ),
    "head": element_documentation(
        "Return the first item of a non-empty list.",
        parameters=(("values", "Non-empty input list."),),
        returns="The first list item.",
        category="Collections",
    ),
    "first": element_documentation(
        "Return the first item of a non-empty list or string.",
        parameters=(("values", "Non-empty input value."),),
        returns="The first item or one-character string.",
        category="Collections",
    ),
    "last": element_documentation(
        "Return the last item of a non-empty finite list or string.",
        parameters=(("values", "Non-empty input value."),),
        returns="The final item or one-character string.",
        category="Collections",
    ),
    "drop": element_documentation(
        "Discard a prefix from a list or string.",
        parameters=(
            ("values", "Input value."),
            ("count", "Number of leading items to remove."),
        ),
        returns="The remaining suffix.",
        category="Collections",
    ),
    "groupConsecutive": element_documentation(
        "Group adjacent equal items.",
        parameters=(("values", "Input list or string."),),
        returns="A list of consecutive groups.",
        category="Collections",
    ),
    "range": element_documentation(
        "Create an inclusive lazy integer range.",
        parameters=(("start", "First integer."), ("stop", "Last integer, included.")),
        returns="A lazy list of integers from start through stop.",
        examples=(("1 4 range", "[1, 2, 3, 4]"),),
        category="Collections",
    ),
    "sum": element_documentation(
        "Add every number in a list.",
        parameters=(("values", "Numbers to add."),),
        returns="The total, or zero for an empty list.",
        examples=(("[1, 2, 3] sum", "6"),),
        category="Collections",
    ),
    "removeAt": element_documentation(
        "Return a list without the item at one index.",
        parameters=(
            ("values", "Input list."),
            (
                "index",
                "Zero-based index to remove; negative indices count from the end.",
            ),
        ),
        returns="A new list containing every other item.",
        category="Collections",
    ),
    "reshape": element_documentation(
        "Reshape a flat list into a rectangular two-dimensional list.",
        parameters=(
            ("values", "Flat input list."),
            ("rows", "Number of output rows."),
            ("columns", "Number of items in each row."),
        ),
        returns="A two-dimensional list with the requested shape.",
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
        parameters=(
            ("items", "Items to append."),
            ("target", "List receiving the items."),
        ),
        returns="A combined list.",
        category="Collections",
    ),
    "join": element_documentation(
        "Join a list of strings with a separator.",
        parameters=(
            ("values", "Strings to join."),
            ("separator", "Text inserted between adjacent strings."),
        ),
        returns="The joined string.",
        category="Strings",
    ),
    "split": element_documentation(
        "Split a string around a separator.",
        parameters=(("value", "String to split."), ("separator", "Separator text.")),
        returns="A list of string segments.",
        category="Strings",
    ),
    "rotate": element_documentation(
        "Rotate a finite list or string to the left.",
        parameters=(
            ("value", "Value to rotate."),
            ("amount", "Signed rotation amount."),
        ),
        returns="The rotated value.",
        category="Collections",
    ),
    "parseInt": element_documentation(
        "Parse a base-ten integer string.",
        parameters=(("value", "String to parse."),),
        returns="The parsed `Integer`, or `None` when parsing fails.",
        category="Strings",
    ),
    "input": element_documentation(
        "Read one line of text from standard input.",
        parameters=(("prompt", "Prompt displayed before reading."),),
        returns="The entered line without its trailing newline.",
        category="Input and output",
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
        parameters=(
            ("value", "Optional, result, or recoverable error."),
            ("operation", "Callable applied to the present or successful value."),
        ),
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
        parameters=(
            ("value", "Preferred string or optional."),
            ("fallback", "Value used when the preferred value is empty."),
        ),
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
    call_overload: (
        Callable[
            [
                Any,
                list[Any],
                int,
                tuple[Any, ...],
                bool,
                tuple[int, ...],
                tuple[int | None, ...],
            ],
            list[Any],
        ]
        | None
    ) = None
    static_values: tuple[Any, ...] = ()
    type_args: tuple[str, ...] = ()


RuntimeImpl = Callable[[tuple[Any, ...], RuntimeContext], tuple[Any, ...]]


def _runtime_return_tags(typ: T.Type) -> tuple[T.DataTag, ...]:
    """Return top-level reified tags once for a built-in return type."""
    typ = T.normalize(typ)
    if not isinstance(typ, T.TaggedType):
        return ()
    return tuple(sorted(tag for tag in typ.tags if tag.depth == 0))


def _runtime_type_is_ownership_trivial(typ: T.Type) -> bool:
    """Conservatively identify values that never need retain/release work."""
    typ = T.normalize(typ)
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _runtime_type_is_ownership_trivial(typ.inner)
    if isinstance(typ, T.NominalType):
        return not typ.args and typ.name.text in {
            "Boolean",
            "Integer",
            "Number",
            "Real",
            "String",
        }
    if isinstance(typ, T.NoneTypeNode):
        return True
    if isinstance(typ, T.TupleType):
        return all(_runtime_type_is_ownership_trivial(item) for item in typ.params)
    if isinstance(typ, T.UnionType):
        return all(_runtime_type_is_ownership_trivial(item) for item in typ.items)
    return False


@dataclass(frozen=True)
class BuiltinOverload:
    """A built-in overload with static type and optional runtime behaviour."""

    signature: T.Overload
    implementation: RuntimeImpl | None = None
    vectorisable: bool = True
    runtime_return_tags: tuple[tuple[T.DataTag, ...], ...] = field(
        init=False,
        repr=False,
        compare=False,
    )
    runtime_return_tag_deltas: tuple[
        tuple[tuple[T.DataTag, ...], tuple[T.DataTag, ...]], ...
    ] = field(init=False, repr=False, compare=False)
    ownership_trivial: bool = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        """Cache runtime-only signature facts used on every invocation."""
        tags = tuple(_runtime_return_tags(typ) for typ in self.signature.returns)
        object.__setattr__(
            self,
            "runtime_return_tags",
            tags if any(tags) else (),
        )
        object.__setattr__(
            self,
            "runtime_return_tag_deltas",
            (
                tuple(
                    (
                        tuple(tag for tag in return_tags if not tag.absent),
                        tuple(tag for tag in return_tags if tag.absent),
                    )
                    for return_tags in tags
                )
                if any(tags)
                else ()
            ),
        )
        object.__setattr__(
            self,
            "ownership_trivial",
            all(
                _runtime_type_is_ownership_trivial(typ)
                for typ in (*self.signature.params, *self.signature.returns)
            ),
        )

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
    vectorisable: bool = True,
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
            vectorisable,
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
    return name.dotted() if isinstance(name, Symbol) else name


def _symbol_key(name: str) -> Symbol:
    """Reconstruct a qualified symbol from a dotted registry key."""
    parts = name.split(".")
    return Symbol(parts[-1], tuple(parts[:-1]))


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


def _truth(value: bool) -> RuntimeNumber:
    """Compute truth for the built-in catalogue and runtime."""
    return RuntimeNumber(1) if value else RuntimeNumber(0)


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
            return isinstance(value, RuntimeNumber)
        if typ.name == REAL:
            return isinstance(value, RuntimeNumber)
        if typ.name == INTEGER:
            return (
                isinstance(value, RuntimeNumber) and value == value.to_integral_value()
            )
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
        if len(stack) >= arity:
            args = stack[-arity:] if arity else ()
        elif not stack:
            # In an input-inferred function (for example an unfold body), the
            # callable itself supplies the input shape that peek preserves.
            args = candidate.params
        else:
            continue
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


def _completed_call_site_stack(
    stack: tuple[T.Type, ...],
    expected: tuple[T.Type, ...],
) -> tuple[T.Type, ...]:
    """Complete missing lower stack inputs from a callable's declared parameters."""
    required = len(expected)
    if required == 0:
        return ()
    if len(stack) >= required:
        return stack[-required:]
    missing = required - len(stack)
    return (*expected[:missing], *stack)


def _both_call_site(call_params: tuple[T.Type, ...]) -> T.Overload | None:
    """Type-check one callable against two consecutive input groups."""
    if not call_params:
        return None
    function_type = call_params[-1]
    stack = call_params[:-1]
    for candidate in _callable_overloads(function_type):
        arity = len(candidate.params)
        expected = (*candidate.params, *candidate.params)
        args = _completed_call_site_stack(stack, expected)
        first_args = args[:arity]
        second_args = args[arity:]
        first_application = _apply_callable(candidate, first_args)
        second_application = _apply_callable(candidate, second_args)
        if first_application is None or second_application is None:
            continue
        concrete_function_type = (
            first_application.concrete_type
            if T.same(
                first_application.concrete_type,
                second_application.concrete_type,
            )
            else function_type
        )
        return T.Overload(
            (*args, concrete_function_type),
            (
                *first_application.applied.actual_returns,
                *second_application.applied.actual_returns,
            ),
            call_site_body=arity * 2,
            runtime_static_values=(arity,),
        )
    return None


def _correspond_call_site(call_params: tuple[T.Type, ...]) -> T.Overload | None:
    """Type-check two callables against distinct consecutive input groups."""
    if len(call_params) < 2:
        return None
    lower_type, upper_type = call_params[-2:]
    stack = call_params[:-2]
    for lower in _callable_overloads(lower_type):
        for upper in _callable_overloads(upper_type):
            lower_arity = len(lower.params)
            upper_arity = len(upper.params)
            expected = (*lower.params, *upper.params)
            args = _completed_call_site_stack(stack, expected)
            lower_args = args[:lower_arity]
            upper_args = args[lower_arity:]
            lower_application = _apply_callable(lower, lower_args)
            upper_application = _apply_callable(upper, upper_args)
            if lower_application is None or upper_application is None:
                continue
            return T.Overload(
                (
                    *args,
                    lower_application.concrete_type,
                    upper_application.concrete_type,
                ),
                (
                    *lower_application.applied.actual_returns,
                    *upper_application.applied.actual_returns,
                ),
                call_site_body=lower_arity + upper_arity,
                runtime_static_values=(lower_arity, upper_arity),
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


@builtin("both", (T.Fn(),), call_site=_both_call_site)
def _both(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Apply one callable to two statically sized argument groups."""
    if not args:
        raise RuntimeError("both requires a callable")
    callable_value = args[-1]
    values = args[:-1]
    arity = int(ctx.static_values[0]) if ctx.static_values else len(values) // 2
    if arity < 0 or len(values) != arity * 2:
        raise RuntimeError("invalid both call-site arity metadata")
    return (
        *ctx.call(callable_value, list(values[:arity])),
        *ctx.call(callable_value, list(values[arity:])),
    )


@builtin(
    "correspond",
    (T.Fn(), T.Fn()),
    call_site=_correspond_call_site,
)
def _correspond(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Apply two callables to their statically sized argument groups."""
    if len(args) < 2:
        raise RuntimeError("correspond requires two callables")
    *values, lower, upper = args
    if len(ctx.static_values) >= 2:
        lower_arity = int(ctx.static_values[0])
        upper_arity = int(ctx.static_values[1])
    else:
        lower_arity = _runtime_callable_arity(lower)
        upper_arity = _runtime_callable_arity(upper)
    if lower_arity < 0 or upper_arity < 0 or len(values) != lower_arity + upper_arity:
        raise RuntimeError("invalid correspond call-site arity metadata")
    split = lower_arity
    return (
        *ctx.call(lower, list(values[:split])),
        *ctx.call(upper, list(values[split:])),
    )


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
    vectorised = False
    vectorised_depths: tuple[int, ...] = ()
    vectorised_target_ranks: tuple[int | None, ...] = ()
    if len(ctx.static_values) >= 5 and ctx.static_values[1] == "__call_static__":
        vectorised = bool(ctx.static_values[2])
        raw_depths = ctx.static_values[3]
        raw_targets = ctx.static_values[4]
        if not isinstance(raw_depths, tuple) or not all(
            isinstance(depth, int) for depth in raw_depths
        ):
            raise RuntimeError("invalid call vectorisation depth metadata")
        if not isinstance(raw_targets, tuple) or not all(
            target is None or isinstance(target, int) for target in raw_targets
        ):
            raise RuntimeError("invalid call vectorisation rank metadata")
        vectorised_depths = raw_depths
        vectorised_target_ranks = raw_targets
        hidden_static_values = tuple(ctx.static_values[5:])
    else:
        hidden_static_values = tuple(ctx.static_values[1:])
    if isinstance(selected, int) and ctx.call_overload is not None:
        return tuple(
            ctx.call_overload(
                callable_value,
                call_args,
                selected,
                hidden_static_values,
                vectorised,
                vectorised_depths,
                vectorised_target_ranks,
            )
        )
    if hidden_static_values:
        raise RuntimeError("call is missing its statically selected overload")
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


def _wrapping_mod(a: RuntimeNumber, b: RuntimeNumber) -> RuntimeNumber:
    """Return modulo wrapped to the divisor's sign."""
    remainder = a % b
    if remainder and (remainder < 0) != (b < 0):
        remainder += b
    return remainder


@builtin("+", (T.Integer, T.Integer), (T.Integer,))
@builtin("+", (T.Real, T.Real), (T.Real,))
@builtin("+", (T.Real, T.Integer), (T.Real,))
@builtin("+", (T.Integer, T.Real), (T.Real,))
@builtin("+", (T.Number, T.Number), (T.Number,))
@builtin("+", (T.String, T.String), (T.String,))
def _plus(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `+`, `+`, `+`, `+`, `+`, `+` built-in runtime overloads."""
    return (args[0] + args[1],)


@builtin("-", (T.Integer, T.Integer), (T.Integer,))
@builtin("-", (T.Real, T.Real), (T.Real,))
@builtin("-", (T.Real, T.Integer), (T.Real,))
@builtin("-", (T.Integer, T.Real), (T.Real,))
@builtin("-", (T.Number, T.Number), (T.Number,))
def _minus(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `-`, `-`, `-`, `-`, `-` built-in runtime overloads."""
    return (args[0] - args[1],)


@builtin("*", (T.Integer, T.Integer), (T.Integer,))
@builtin("*", (T.Real, T.Real), (T.Real,))
@builtin("*", (T.Real, T.Integer), (T.Real,))
@builtin("*", (T.Integer, T.Real), (T.Real,))
@builtin("*", (T.Number, T.Number), (T.Number,))
def _multiply(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `*`, `*`, `*`, `*`, `*` built-in runtime overloads."""
    return (args[0] * args[1],)


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


@builtin("**", (T.Number, T.Number), (T.Number,))
def _power(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Raise one Valiance number to another numeric power."""
    del ctx
    base, exponent = args
    try:
        return (base**exponent,)
    except (ArithmeticError, ValueError) as exc:
        raise RuntimeError("invalid numeric exponentiation") from exc


@builtin("%", (T.Integer, T.Integer), (T.Integer,))
@builtin("%", (T.Real, T.Real), (T.Real,))
@builtin("%", (T.Real, T.Integer), (T.Real,))
@builtin("%", (T.Integer, T.Real), (T.Real,))
@builtin("%", (T.Number, T.Number), (T.Number,))
def _percent(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `%`, `%`, `%`, `%`, `%` built-in runtime overloads."""
    return (_wrapping_mod(args[0], args[1]),)


@builtin("/", (T.Integer, T.Integer), (T.Real,))
@builtin("/", (T.Real, T.Real), (T.Real,))
@builtin("/", (T.Real, T.Integer), (T.Real,))
@builtin("/", (T.Integer, T.Real), (T.Real,))
@builtin("/", (T.Number, T.Number), (T.Number,))
def _slash(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `/`, `/`, `/`, `/`, `/` built-in runtime overloads."""
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
@builtin(
    "/",
    (
        T.Tup(T.Integer, T.String),
        T.Fn((T.Integer, T.String), (T.String,)),
    ),
    (T.String,),
)
@alias("fold")
def _fold(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `/` built-in runtime overload."""
    values, reducer = args
    if hasattr(values, "code") or hasattr(values, "overloads"):
        values, reducer = reducer, values
    iterator = iter(values)
    try:
        result = next(iterator)
    except StopIteration as exc:
        raise RuntimeError("reduce requires a non-empty list") from exc
    for item in iterator:
        called = ctx.call(reducer, [result, item])
        result = called[0]
    return (result,)


@builtin("double", (T.Number,), (T.Number,))
def _double(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `double` built-in runtime overload."""
    return (args[0] * 2,)


@builtin("squared", (T.Number,), (T.Number,))
@alias("square")
def _squared(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `squared` built-in runtime overload."""
    return (args[0] * args[0],)


@builtin(
    "inc",
    (T.Integer,),
    (T.Integer,),
    documentation=element_documentation(
        "Increase an integer by one.",
        parameters=(("value", "Integer to increment."),),
        returns="The next integer.",
        category="Arithmetic",
    ),
)
@builtin("inc", (T.Number,), (T.Number,))
def _inc(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Increase a numeric value by one."""
    del ctx
    return (args[0] + RuntimeNumber(1),)


@builtin(
    "inRange",
    (T.Number, T.Number, T.Number),
    (T.Boolean,),
    param_names=("start", "stop", "value"),
    documentation=element_documentation(
        "Test whether a number lies in a half-open interval.",
        description="The start is included and the stop is excluded.",
        parameters=(
            ("start", "Inclusive lower bound."),
            ("stop", "Exclusive upper bound."),
            ("value", "Number to test, normally supplied from the stack."),
        ),
        returns="A Boolean number indicating whether start <= value < stop.",
        category="Comparison",
    ),
)
def _in_range(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return whether value is within the requested half-open interval."""
    del ctx
    start, stop, value = args
    return (_truth(start <= value < stop),)


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


@builtin(
    "!=",
    (T.Number, T.Number),
    (T.Boolean,),
    documentation=element_documentation(
        "Test whether two numbers or strings differ.",
        parameters=(("left", "First value."), ("right", "Second value.")),
        returns="A Boolean number that is true when the values differ.",
        category="Comparison",
    ),
)
@builtin("!=", (T.String, T.String), (T.Boolean,))
def _not_equals(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return the negation of ordinary value equality."""
    del ctx
    return (_truth(args[0] != args[1]),)


@builtin("===", (T.V("T"), T.V("T")), (T.Boolean,))
def _structural_equals(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Compare two values using the runtime's structural value equality."""
    del ctx
    return (_truth(args[0] == args[1]),)


@builtin("in", (T.String, T.String), (T.Boolean,))
@builtin(
    "in",
    (T.V("Item"), T.ExactList(T.V("Item"))),
    (T.Boolean,),
)
def _contains(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Test membership in a string or finite/list-like value."""
    del ctx
    needle, haystack = args
    return (_truth(needle in haystack),)


@builtin("numeric?", (T.String,), (T.Boolean,))
def _numeric(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return whether a string can be parsed as a base-ten integer."""
    del ctx
    try:
        int(args[0].strip(), 10)
    except ValueError:
        return (_truth(False),)
    return (_truth(True),)


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
    return (RuntimeNumber(1),)


@builtin("false", (), (T.Boolean,))
def _false(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `false` built-in runtime overload."""
    return (RuntimeNumber(0),)


# --------------------------------------------------------------------------
# Lists
# --------------------------------------------------------------------------


@builtin(
    "map",
    (
        T.String,
        T.Fn((T.String,), (T.TypeVariable("Mapped"),)),
    ),
    (T.ExactList(T.TypeVariable("Mapped")),),
)
def _map_string(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Map a unary function over the characters of a finite string."""
    values, function = args
    return ([ctx.call(function, [character])[0] for character in values],)


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
        T.Fn((), (T.TypeVariable("Mapped"),)),
    ),
    (T.ExactList(T.TypeVariable("Mapped")),),
)
def _map_niladic(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Call a niladic mapping function once for every input-list item."""

    def mapped_items():
        """Yield one niladic callable result for each input item."""
        for _item in args[0]:
            mapped = ctx.call(args[1], [])
            yield mapped[0]

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
    "overtake",
    (T.ExactList(T.V("Item")), T.Integer),
    (T.ExactList(T.V("Item")),),
    documentation=element_documentation(
        "Repeat a finite list cyclically until the requested length is reached.",
        parameters=(
            ("values", "Non-empty finite source list."),
            ("count", "Requested output length."),
        ),
        returns="A list of exactly count items.",
        category="Collections",
    ),
)
def _overtake(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Cycle a non-empty finite list and take exactly the requested count."""
    del ctx
    values, raw_count = args
    count = int(raw_count)
    if raw_count != raw_count.to_integral_value() or count < 0:
        raise RuntimeError("overtake requires a non-negative integer count")
    materialized = list(values)
    if count and not materialized:
        raise RuntimeError("overtake requires a non-empty source list")
    return (list(islice(cycle(materialized), count)),)


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
    (T.Integer,),
    vectorisable=False,
)
@builtin("length", (T.String,), (T.Integer,), vectorisable=False)
@alias("len")
def _length(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return the exact length of a finite list or string."""
    return (RuntimeNumber(len(args[0])),)


@builtin("head", (T.ExactList(T.TypeVariable("Item")),), (T.TypeVariable("Item"),))
def _head(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `head` built-in runtime overload."""
    for item in args[0]:
        return (item,)
    raise RuntimeError("head requires a non-empty list")


@builtin("first", (T.ExactList(T.V("Item")),), (T.V("Item"),))
@builtin("first", (T.String,), (T.String,))
def _first_value(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return the first item from a non-empty finite/list-like value."""
    del ctx
    value = args[0]
    for item in value:
        return (item,)
    raise RuntimeError("first requires a non-empty value")


@builtin(
    "last",
    (T.WithoutTag(T.ExactList(T.V("Item")), "infinite"),),
    (T.V("Item"),),
)
@builtin("last", (T.String,), (T.String,))
def _last_value(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return the final item from a non-empty finite list or string."""
    del ctx
    value = args[0]
    if not value:
        raise RuntimeError("last requires a non-empty value")
    return (value[-1],)


@builtin("range", (T.Integer, T.Integer), (T.ExactList(T.Integer),))
@builtin("range", (T.Number, T.Number), (T.ExactList(T.Number),))
def _range(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `range` built-in runtime overload."""
    start, stop = args
    if start != start.to_integral_value() or stop != stop.to_integral_value():
        raise RuntimeError("range bounds must be integral numbers")
    return (LazyList(RuntimeNumber(item) for item in range(int(start), int(stop) + 1)),)


@builtin(
    "drop",
    (T.ExactList(T.V("Item")), T.Integer),
    (T.ExactList(T.V("Item")),),
)
@builtin(
    "drop",
    (T.Integer, T.ExactList(T.V("Item"))),
    (T.ExactList(T.V("Item")),),
)
@builtin("drop", (T.String, T.Integer), (T.String,))
@builtin("drop", (T.Integer, T.String), (T.String,))
def _drop(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Drop a non-negative number of leading items from a list or string."""
    del ctx
    if isinstance(args[0], RuntimeNumber):
        raw_count, values = args
    else:
        values, raw_count = args
    count = int(raw_count)
    if count < 0:
        raise RuntimeError("drop requires a non-negative integer")
    if isinstance(values, LazyList):
        return (LazyList(islice(iter(values), count, None)),)
    return (values[count:],)


@builtin(
    "dropLast",
    (T.ExactList(T.V("Item")),),
    (T.ExactList(T.V("Item")),),
    documentation=element_documentation(
        "Return a finite list without its final item.",
        parameters=(("values", "Input finite list."),),
        returns="All items except the final item.",
        category="Collections",
    ),
)
def _drop_last(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return a materialized list with its final item removed."""
    del ctx
    values = list(args[0])
    if not values:
        raise RuntimeError("dropLast requires a non-empty list")
    return (values[:-1],)


@builtin(
    "groupConsecutive",
    (T.ExactList(T.V("Item")),),
    (T.ExactList(T.V("Item"), 2),),
)
def _group_consecutive_list(
    args: tuple[Any, ...], ctx: RuntimeContext
) -> tuple[Any, ...]:
    """Group adjacent equal list items into materialized sublists."""
    del ctx
    return ([[*items] for _key, items in groupby(args[0])],)


@builtin("groupConsecutive", (T.String,), (T.ExactList(T.String),))
def _group_consecutive_string(
    args: tuple[Any, ...], ctx: RuntimeContext
) -> tuple[Any, ...]:
    """Group adjacent equal characters into strings."""
    del ctx
    return (["".join(items) for _key, items in groupby(args[0])],)


@builtin("sum", (T.ExactList(T.Number),), (T.Number,))
def _sum(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Add every numeric item in a list, using zero for an empty list."""
    return (sum(args[0], RuntimeNumber(0)),)


@builtin(
    "removeAt",
    (T.ExactList(T.V("Item")), T.Integer),
    (T.ExactList(T.V("Item")),),
    param_names=("values", "index"),
)
def _remove_at(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return a materialized copy of a list without the indexed item."""
    values, raw_index = args
    result = list(values)
    index = int(raw_index)
    try:
        del result[index]
    except IndexError as exc:
        raise RuntimeError(f"removeAt index {index} is out of range") from exc
    return (result,)


@builtin(
    "reshape",
    (T.ExactList(T.V("Item")), T.Integer, T.Integer),
    (T.ExactList(T.V("Item"), 2),),
    param_names=("values", "rows", "columns"),
)
@builtin(
    "reshape",
    (T.Integer, T.Integer, T.ExactList(T.V("Item"))),
    (T.ExactList(T.V("Item"), 2),),
    param_names=("rows", "columns", "values"),
)
def _reshape(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Reshape a flat finite list into a rectangular two-dimensional list."""
    if isinstance(args[0], RuntimeNumber):
        raw_rows, raw_columns, values = args
    else:
        values, raw_rows, raw_columns = args
    rows = int(raw_rows)
    columns = int(raw_columns)
    if rows < 0 or columns < 0:
        raise RuntimeError("reshape dimensions must be non-negative")

    expected = rows * columns
    items = list(islice(iter(values), expected + 1))
    if len(items) != expected:
        raise RuntimeError(
            f"reshape needs exactly {expected} items for shape ({rows}, {columns}); "
            f"received {'more than ' if len(items) > expected else ''}{len(items)}"
        )
    return ([items[row * columns : (row + 1) * columns] for row in range(rows)],)


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


@builtin(
    "split",
    (T.String, T.String),
    (T.ExactList(T.String),),
    param_names=("on", "value"),
)
def _split_string(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Split a string at every occurrence of a literal separator."""
    del ctx
    separator, value = args
    if separator == "":
        return (list(value),)
    return (value.split(separator),)


@builtin("rotate", (T.String, T.Integer), (T.String,))
@builtin(
    "rotate",
    (T.ExactList(T.V("Item")), T.Integer),
    (T.ExactList(T.V("Item")),),
)
def _rotate(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Rotate a finite sequence left by the requested signed amount."""
    del ctx
    values, raw_amount = args
    materialized = values if isinstance(values, str) else list(values)
    if not materialized:
        return (materialized,)
    amount = int(raw_amount) % len(materialized)
    return (materialized[amount:] + materialized[:amount],)


@builtin("parseInt", (T.String,), (T.optional(T.Integer),))
def _parse_int(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Parse a base-ten integer, returning `None` when parsing fails."""
    del ctx
    try:
        return (RuntimeNumber(int(args[0].strip(), 10)),)
    except ValueError:
        return (ObjectValue("None", {}),)


@builtin(
    Symbol("merge", ("record",)),
    (T.N(Symbol("record")), T.N(Symbol("record"))),
    (T.N(Symbol("record")),),
    documentation=element_documentation(
        "Merge two anonymous records, preferring fields from the right record.",
        parameters=(("left", "Base record."), ("right", "Fields to merge.")),
        returns="A merged anonymous record.",
        category="Records",
    ),
)
def _record_merge(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Merge two anonymous records, preferring fields from the right record."""
    del ctx
    left, right = args
    return ({**left, **right},)


@builtin(
    Symbol("extend", ("record",)),
    (T.N(Symbol("record")), T.N(Symbol("record"))),
    (T.N(Symbol("record")),),
    documentation=element_documentation(
        "Extend an anonymous record with fields that are not already present.",
        parameters=(("record", "Base record."), ("fields", "New fields.")),
        returns="The extended anonymous record.",
        category="Records",
    ),
)
def _record_extend(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Extend an anonymous record while rejecting duplicate field names."""
    del ctx
    left, right = args
    duplicates = set(left).intersection(right)
    if duplicates:
        names = ", ".join(sorted(duplicates))
        raise RuntimeError(f"record.extend duplicates field(s): {names}")
    return ({**left, **right},)


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
    return (ObjectValue("OK", {"value": args[0]}, type_args=ctx.type_args),)


@builtin("Some", (T.V("T"),), (T.Some(T.V("T")),))
def _some(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `Some` built-in runtime overload."""
    return (ObjectValue("Some", {"value": args[0]}, type_args=ctx.type_args),)


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


@builtin("input", (T.String,), (T.String,), element_tags=(EAGER_TAG, IO_TAG))
def _input(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Read one line from standard input after displaying a prompt."""
    del ctx
    return (python_builtins.input(args[0]),)


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
    element_tags=(T.ElementTag(Symbol("Panic"), (T.TypeVariable("F"),)),),
)
def _panic(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Implement the `panic` built-in runtime overload."""
    raise PanicSignal(args[0])


# --------------------------------------------------------------------------
# Strings
# --------------------------------------------------------------------------


@builtin(
    "fromCharcode",
    (T.Integer,),
    (T.String,),
    documentation=element_documentation(
        "Convert an integer Unicode code point to a one-character string.",
        parameters=(("codepoint", "Unicode scalar value."),),
        returns="The corresponding character.",
        category="Strings",
    ),
)
@alias("fromCharCode")
def _from_charcode(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Convert an integral Unicode code point to a character."""
    del ctx
    raw_codepoint = args[0]
    if raw_codepoint != raw_codepoint.to_integral_value():
        raise RuntimeError("fromCharcode requires an integer code point")
    codepoint = int(raw_codepoint)
    try:
        return (chr(codepoint),)
    except ValueError as exc:
        raise RuntimeError("fromCharcode code point is out of range") from exc


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
            _symbol_key(name),
            tuple(overloads),
            _DOCUMENTATION_REGISTRY.get(name),
            (
                _symbol_key(canonical)
                if (canonical := _CANONICAL_NAME_REGISTRY.get(name, name)) != name
                else None
            ),
        )
        for name, overloads in _REGISTRY.items()
    )


# Public, for callers that want the full built-in catalogue directly (e.g.
# `from valiance.elements.builtins import BUILTIN_ELEMENTS`). This is derived
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
        item.name.dotted(): item
        for item in BUILTIN_ELEMENTS
        if any(overload.implementation is not None for overload in item.definitions)
    }
