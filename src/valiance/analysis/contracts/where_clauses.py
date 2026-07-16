"""Validation and evaluation for terminating compile-time ``where`` programs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import DecimalException
from enum import Enum, auto
from typing import Mapping

from valiance.runtime.runtime_values import RuntimeNumber
import valiance.vtypes as T
from valiance.asts import (
    ASTNode,
    ElementNode,
    FieldAccessNode,
    GetVariableNode,
    NumberLiteralNode,
    SetVariableNode,
    TypeLiteralNode,
)
from valiance.vtypes.symbols import Symbol

# Ranks become bytecode metadata and can drive nested runtime traversal.  Keeping
# the bound finite prevents a tiny source expression from manufacturing
# pathological metadata while remaining far above practical collection ranks.
MAX_COMPILE_TIME_RANK = 65_535


class StaticKind(Enum):
    """The small set of values accepted by the static evaluator."""

    NUMBER = auto()
    TYPE = auto()
    TYPE_TUPLE = auto()


StaticValue = RuntimeNumber | T.Type | tuple[T.Type, ...]


@dataclass(frozen=True)
class WhereClauseError:
    """One source-located validation failure in a ``where`` program."""

    message: str
    node: ASTNode | None = None


@dataclass(frozen=True)
class WhereClauseShape:
    """Definition-time facts needed by analysis and bytecode lowering."""

    numeric_assignments: tuple[str, ...]
    static_parameter_names: tuple[str, ...]


@dataclass(frozen=True)
class WhereEvaluation:
    """Call-site results of a successful ``where`` evaluation."""

    rank_values: tuple[tuple[str, int], ...]
    runtime_values: tuple[RuntimeNumber, ...]


def rank_variable_names_in_type(typ: T.Type) -> set[str]:
    """Collect every rank-variable name nested in ``typ``."""
    typ = T.normalize(typ)
    names: set[str] = set()
    if isinstance(typ, T.CollectionType):
        if isinstance(typ.rank, T.RankVariable):
            names.add(typ.rank.name)
        names.update(rank_variable_names_in_type(typ.base))
    elif isinstance(typ, T.NominalType):
        for arg in typ.args:
            names.update(rank_variable_names_in_type(arg))
    elif isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            names.update(rank_variable_names_in_type(item))
    elif isinstance(typ, T.TupleType):
        for item in typ.params:
            names.update(rank_variable_names_in_type(item))
    elif isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            names.update(rank_variable_names_in_type(item.typ))
    elif isinstance(typ, T.RowType):
        names.update(rank_variable_names_in_type(typ.base))
        for field in typ.fields:
            names.update(rank_variable_names_in_type(field.typ))
    elif isinstance(typ, T.FunctionType):
        for item in (typ.params or ()) + (typ.returns or ()):
            names.update(rank_variable_names_in_type(item))
        for tag in typ.element_tags:
            for arg in tag.args:
                names.update(rank_variable_names_in_type(arg))
    elif isinstance(typ, T.OverloadSetType):
        for overload in typ.overloads:
            for item in overload.params + overload.returns:
                names.update(rank_variable_names_in_type(item))
            for constraint in overload.generic_constraints:
                names.update(rank_variable_names_in_type(constraint.bound))
            for tag in overload.element_tags:
                for arg in tag.args:
                    names.update(rank_variable_names_in_type(arg))
    elif isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            overload = requirement.overload
            for item in overload.params + overload.returns:
                names.update(rank_variable_names_in_type(item))
            for constraint in overload.generic_constraints:
                names.update(rank_variable_names_in_type(constraint.bound))
            for tag in overload.element_tags:
                for arg in tag.args:
                    names.update(rank_variable_names_in_type(arg))
    elif isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        names.update(rank_variable_names_in_type(typ.inner))
    return names


def rank_variable_names(types: tuple[T.Type, ...]) -> set[str]:
    """Collect rank-variable names from a type sequence."""
    names: set[str] = set()
    for typ in types:
        names.update(rank_variable_names_in_type(typ))
    return names


def validate_where_clause(
    *,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    param_names: tuple[Symbol | None, ...],
    clause: tuple[object, ...],
) -> tuple[WhereClauseShape | None, WhereClauseError | None]:
    """Validate one static program without executing call-dependent values."""
    input_ranks = rank_variable_names(params)
    return_ranks = rank_variable_names(returns)
    all_ranks = input_ranks | return_ranks
    reserved_ranks = sorted(
        name for name in all_ranks if _is_generated_parameter_name(name)
    )
    if reserved_ranks:
        return None, WhereClauseError(
            "rank variable name(s) are reserved for generated parameters: "
            + ", ".join(f"${name}" for name in reserved_ranks)
        )
    parameter_names = {name.text for name in param_names if name is not None}
    conflicts = sorted(all_ranks & parameter_names)
    if conflicts:
        return None, WhereClauseError(
            "parameter name(s) conflict with rank variable(s): "
            + ", ".join(f"${name}" for name in conflicts)
        )
    read_only = input_ranks | parameter_names
    variables: dict[str, StaticKind] = {
        **{name: StaticKind.NUMBER for name in input_ranks},
        **{name: StaticKind.TYPE for name in parameter_names},
    }
    local_assignments: set[str] = set()
    stack: list[StaticKind] = []

    for raw_node in clause:
        if not isinstance(raw_node, ASTNode):
            return None, WhereClauseError(
                "contains a non-AST static instruction"
            )
        error = _validate_node(
            raw_node,
            stack,
            variables,
            read_only,
            local_assignments,
        )
        if error is not None:
            return None, error

    unresolved = sorted(return_ranks - input_ranks - local_assignments)
    if unresolved:
        return None, WhereClauseError(
            "does not assign return rank variable(s): " + ", ".join(
                f"${name}" for name in unresolved
            )
        )
    non_numeric_ranks = sorted(
        name
        for name in all_ranks
        if variables.get(name) is not StaticKind.NUMBER
    )
    if non_numeric_ranks:
        return None, WhereClauseError(
            "rank variable(s) must contain numbers: " + ", ".join(
                f"${name}" for name in non_numeric_ranks
            )
        )

    numeric_assignments = tuple(
        sorted(
            name
            for name in local_assignments
            if variables.get(name) is StaticKind.NUMBER
        )
    )
    static_names = tuple(sorted(all_ranks | set(numeric_assignments)))
    return WhereClauseShape(numeric_assignments, static_names), None


def static_parameter_names(
    *,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    param_names: tuple[Symbol | None, ...],
    clause: tuple[object, ...],
) -> tuple[str, ...]:
    """Return hidden numeric parameter names in deterministic call order."""
    shape, _ = validate_where_clause(
        params=params,
        returns=returns,
        param_names=param_names,
        clause=clause,
    )
    return () if shape is None else shape.static_parameter_names


def substitute_rank_variables(
    typ: T.Type,
    ranks: Mapping[str, int],
) -> T.Type:
    """Replace every free rank variable nested in ``typ``."""
    return substitute_static_type(typ, ranks=ranks)


def substitute_static_type(
    typ: T.Type,
    *,
    ranks: Mapping[str, int] | None = None,
    types: Mapping[str, T.Type] | None = None,
) -> T.Type:
    """Substitute solved rank and generic variables in one static type."""
    return _substitute_type(typ, ranks or {}, types or {})


def evaluate_where_clause(
    *,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    param_names: tuple[Symbol | None, ...],
    clause: tuple[object, ...],
    args: tuple[T.Type, ...],
    initial_ranks: Mapping[str, int],
    type_substitution: Mapping[str, T.Type] | None = None,
) -> WhereEvaluation | None:
    """Execute a validated static program for one overload candidate."""
    shape, error = validate_where_clause(
        params=params,
        returns=returns,
        param_names=param_names,
        clause=clause,
    )
    if shape is None or error is not None:
        return None

    input_ranks = rank_variable_names(params)
    all_ranks = input_ranks | rank_variable_names(returns)
    variables: dict[str, StaticValue] = {
        name: RuntimeNumber(value) for name, value in initial_ranks.items()
    }
    for param_name, arg in zip(param_names, args, strict=False):
        if param_name is not None:
            variables[param_name.text] = arg
    read_only = input_ranks | {
        name.text for name in param_names if name is not None
    }
    stack: list[StaticValue] = []
    substitutions = type_substitution or {}

    for raw_node in clause:
        if not isinstance(raw_node, ASTNode) or not _evaluate_node(
            raw_node,
            stack,
            variables,
            read_only,
            initial_ranks,
            substitutions,
        ):
            return None
    rank_values: list[tuple[str, int]] = []
    for name in sorted(all_ranks):
        value = variables.get(name)
        rank = _positive_rank(value)
        if rank is None:
            return None
        rank_values.append((name, rank))

    runtime_values: list[RuntimeNumber] = []
    for name in shape.static_parameter_names:
        value = variables.get(name)
        if not isinstance(value, RuntimeNumber) or not value.is_finite():
            return None
        runtime_values.append(value)

    return WhereEvaluation(tuple(rank_values), tuple(runtime_values))


def _validate_node(
    node: ASTNode,
    stack: list[StaticKind],
    variables: dict[str, StaticKind],
    read_only: set[str],
    local_assignments: set[str],
) -> WhereClauseError | None:
    """Abstractly execute one node and return its first validation error."""
    match node:
        case NumberLiteralNode(value):
            parsed = _parse_number(value)
            if parsed is None:
                return WhereClauseError(
                    f"contains invalid numeric literal {value!r}", node
                )
            stack.append(StaticKind.NUMBER)
            return None
        case TypeLiteralNode(typ):
            if _contains_result_type(typ):
                return WhereClauseError(
                    "cannot use Result types; use optionals instead", node
                )
            stack.append(StaticKind.TYPE)
            return None
        case GetVariableNode(name):
            kind = variables.get(name.text)
            if kind is None:
                return WhereClauseError(
                    f"reads undefined static variable '${name}'", node
                )
            stack.append(kind)
            return None
        case SetVariableNode(
            name=name, declared_type=declared_type, constant=constant
        ):
            if declared_type is not None or constant:
                return WhereClauseError(
                    "static assignment cannot declare a type or constant", node
                )
            if not stack:
                return WhereClauseError("assignment underflows the static stack", node)
            if _is_generated_parameter_name(name.text):
                return WhereClauseError(
                    f"static variable '${name}' uses a reserved generated name",
                    node,
                )
            if name.text in read_only:
                return WhereClauseError(
                    f"cannot assign read-only static variable '${name}'", node
                )
            kind = stack.pop()
            variables[name.text] = kind
            local_assignments.add(name.text)
            return None
        case FieldAccessNode(name=name, optional_safe=optional_safe):
            if optional_safe:
                return WhereClauseError(
                    "optional-safe field access is not allowed", node
                )
            if not stack:
                return WhereClauseError(
                    f"'.{name}' underflows the static stack", node
                )
            if stack.pop() is not StaticKind.TYPE:
                return WhereClauseError(
                    f"'.{name}' requires a function type", node
                )
            if name.text in {"inputs", "outputs"}:
                stack.append(StaticKind.TYPE_TUPLE)
                return None
            if name.text in {"arity", "multiplicity"}:
                stack.append(StaticKind.NUMBER)
                return None
            return WhereClauseError(
                f"function introspection field '.{name}' is not allowed", node
            )
        case ElementNode(
            name=name,
            modifier_args=modifier_args,
            disambiguation=disambiguation,
            call_args=call_args,
            annotations=annotations,
            extension=extension,
        ):
            if (
                name.namespace
                or modifier_args
                or disambiguation
                or annotations
                or extension is not None
            ):
                return WhereClauseError(
                    "static operations cannot be namespaced, modified, "
                    "annotated, extended, or disambiguated",
                    node,
                )
            for arg in call_args:
                if arg.placeholder or arg.name is not None:
                    return WhereClauseError(
                        "static calls do not allow placeholders or named arguments",
                        node,
                    )
                for value_node in arg.value:
                    error = _validate_node(
                        value_node,
                        stack,
                        variables,
                        read_only,
                        local_assignments,
                    )
                    if error is not None:
                        return error
            return _validate_element(name.text, stack, node)
        case _:
            return WhereClauseError(
                f"instruction '{type(node).__name__}' is not allowed", node
            )


def _validate_element(
    name: str,
    stack: list[StaticKind],
    node: ASTNode,
) -> WhereClauseError | None:
    """Apply one allowed operation to the abstract static stack."""
    numeric_binary = {"+", "-", "*", "max", "min", "<", ">", "<=", ">="}
    if name in numeric_binary:
        if len(stack) < 2:
            return WhereClauseError(
                f"'{name}' underflows the static stack", node
            )
        right = stack.pop()
        left = stack.pop()
        if left is not StaticKind.NUMBER or right is not StaticKind.NUMBER:
            return WhereClauseError(f"'{name}' requires two numbers", node)
        stack.append(StaticKind.NUMBER)
        return None
    if name in {"==", "!="}:
        if len(stack) < 2:
            return WhereClauseError(
                f"'{name}' underflows the static stack", node
            )
        right = stack.pop()
        left = stack.pop()
        if left is not right or left not in {StaticKind.NUMBER, StaticKind.TYPE}:
            return WhereClauseError(
                f"'{name}' requires two numbers or two types", node
            )
        stack.append(StaticKind.NUMBER)
        return None
    if name in {"and", "or"}:
        if len(stack) < 2:
            return WhereClauseError(
                f"'{name}' underflows the static stack", node
            )
        if (
            stack.pop() is not StaticKind.NUMBER
            or stack.pop() is not StaticKind.NUMBER
        ):
            return WhereClauseError(f"'{name}' requires two numbers", node)
        stack.append(StaticKind.NUMBER)
        return None
    if name in {"not", "?"}:
        if not stack:
            return WhereClauseError(
                f"'{name}' underflows the static stack", node
            )
        if stack.pop() is not StaticKind.NUMBER:
            return WhereClauseError(f"'{name}' requires a number", node)
        if name == "not":
            stack.append(StaticKind.NUMBER)
        return None
    if name == "length":
        if not stack:
            return WhereClauseError("'length' underflows the static stack", node)
        if stack.pop() not in {StaticKind.TYPE, StaticKind.TYPE_TUPLE}:
            return WhereClauseError(
                "'length' requires a fixed tuple type or type tuple", node
            )
        stack.append(StaticKind.NUMBER)
        return None
    if name == "dup":
        if not stack:
            return WhereClauseError("'dup' underflows the static stack", node)
        stack.append(stack[-1])
        return None
    if name == "pop":
        if not stack:
            return WhereClauseError("'pop' underflows the static stack", node)
        stack.pop()
        return None
    if name == "swap":
        if len(stack) < 2:
            return WhereClauseError("'swap' underflows the static stack", node)
        stack[-1], stack[-2] = stack[-2], stack[-1]
        return None
    return WhereClauseError(f"operation '{name}' is not allowed", node)


def _evaluate_node(
    node: ASTNode,
    stack: list[StaticValue],
    variables: dict[str, StaticValue],
    read_only: set[str],
    ranks: Mapping[str, int],
    type_substitution: Mapping[str, T.Type],
) -> bool:
    """Execute one static node without invoking arbitrary language elements."""
    match node:
        case NumberLiteralNode(value):
            number = _parse_number(value)
            if number is None:
                return False
            stack.append(number)
            return True
        case TypeLiteralNode(typ):
            dynamic_ranks = dict(ranks)
            for variable_name, variable_value in variables.items():
                if (rank := _positive_rank(variable_value)) is not None:
                    dynamic_ranks[variable_name] = rank
            value = _substitute_type(typ, dynamic_ranks, type_substitution)
            if (
                _contains_result_type(value)
                or rank_variable_names_in_type(value)
            ):
                return False
            stack.append(value)
            return True
        case GetVariableNode(name):
            value = variables.get(name.text)
            if value is None:
                return False
            if isinstance(value, T.Type) and _contains_result_type(value):
                return False
            stack.append(value)
            return True
        case SetVariableNode(
            name=name, declared_type=declared_type, constant=constant
        ):
            if declared_type is not None or constant:
                return False
            if not stack or name.text in read_only:
                return False
            variables[name.text] = stack.pop()
            return True
        case FieldAccessNode(name=name, optional_safe=optional_safe):
            if optional_safe or not stack:
                return False
            raw_value = stack.pop()
            value = T.normalize(raw_value) if isinstance(raw_value, T.Type) else None
            return _evaluate_field_access(name.text, value, stack)
        case ElementNode(
            name=name,
            modifier_args=modifier_args,
            disambiguation=disambiguation,
            call_args=call_args,
            annotations=annotations,
            extension=extension,
        ):
            if (
                name.namespace
                or modifier_args
                or disambiguation
                or annotations
                or extension is not None
            ):
                return False
            for arg in call_args:
                if arg.placeholder or arg.name is not None:
                    return False
                for value_node in arg.value:
                    if not _evaluate_node(
                        value_node,
                        stack,
                        variables,
                        read_only,
                        ranks,
                        type_substitution,
                    ):
                        return False
            return _evaluate_element(name.text, stack)
        case _:
            return False


def _evaluate_field_access(
    name: str,
    value: T.Type | None,
    stack: list[StaticValue],
) -> bool:
    """Evaluate one function-type introspection field."""
    if not isinstance(value, T.FunctionType):
        return False
    if value.params is None or value.returns is None:
        return False
    if name == "inputs":
        stack.append(value.params)
    elif name == "outputs":
        stack.append(value.returns)
    elif name == "arity":
        stack.append(RuntimeNumber(len(value.params)))
    elif name == "multiplicity":
        stack.append(RuntimeNumber(len(value.returns)))
    else:
        return False
    return True


def _evaluate_element(name: str, stack: list[StaticValue]) -> bool:
    """Evaluate one whitelisted static operation."""
    if name in {"+", "-", "*", "max", "min", "<", ">", "<=", ">="}:
        values = _pop_numbers(stack, 2)
        if values is None:
            return False
        left, right = values
        try:
            if name == "+":
                result = left + right
            elif name == "-":
                result = left - right
            elif name == "*":
                result = left * right
            elif name == "max":
                result = max(left, right)
            elif name == "min":
                result = min(left, right)
            elif name == "<":
                result = _truth_number(left < right)
            elif name == ">":
                result = _truth_number(left > right)
            elif name == "<=":
                result = _truth_number(left <= right)
            else:
                result = _truth_number(left >= right)
        except DecimalException:
            return False
        if not result.is_finite():
            return False
        stack.append(result)
        return True
    if name in {"==", "!="}:
        if len(stack) < 2:
            return False
        right = stack.pop()
        left = stack.pop()
        if isinstance(left, RuntimeNumber) and isinstance(right, RuntimeNumber):
            equal = left == right
        elif isinstance(left, T.Type) and isinstance(right, T.Type):
            equal = T.same(left, right)
        else:
            return False
        stack.append(_truth_number(equal if name == "==" else not equal))
        return True
    if name in {"and", "or"}:
        values = _pop_numbers(stack, 2)
        if values is None:
            return False
        left, right = values
        result = (left != 0 and right != 0) if name == "and" else (
            left != 0 or right != 0
        )
        stack.append(_truth_number(result))
        return True
    if name == "not":
        values = _pop_numbers(stack, 1)
        if values is None:
            return False
        stack.append(_truth_number(values[0] == 0))
        return True
    if name == "?":
        values = _pop_numbers(stack, 1)
        return values is not None and values[0] != 0
    if name == "length":
        if not stack:
            return False
        value = stack.pop()
        if isinstance(value, T.TupleType):
            stack.append(RuntimeNumber(len(value.params)))
            return True
        if isinstance(value, tuple) and all(isinstance(item, T.Type) for item in value):
            stack.append(RuntimeNumber(len(value)))
            return True
        return False
    if name == "dup":
        if not stack:
            return False
        stack.append(stack[-1])
        return True
    if name == "pop":
        if not stack:
            return False
        stack.pop()
        return True
    if name == "swap":
        if len(stack) < 2:
            return False
        stack[-1], stack[-2] = stack[-2], stack[-1]
        return True
    return False


def _pop_numbers(
    stack: list[StaticValue], count: int
) -> tuple[RuntimeNumber, ...] | None:
    """Pop ``count`` finite static numbers, preserving source order."""
    if len(stack) < count:
        return None
    values = tuple(stack[-count:])
    if not all(isinstance(value, RuntimeNumber) and value.is_finite() for value in values):
        return None
    del stack[-count:]
    return tuple(value for value in values if isinstance(value, RuntimeNumber))


def _parse_number(value: str) -> RuntimeNumber | None:
    """Parse a finite Valiance number without leaking decimal exceptions."""
    try:
        number = RuntimeNumber(value)
    except (DecimalException, ValueError):
        return None
    if number.is_complex():
        return None # Complex numbers are not allowed in static evaluation.
    return number if number.is_finite() else None


def _truth_number(value: bool) -> RuntimeNumber:
    """Represent static truth using Valiance's numeric truthiness model."""
    return RuntimeNumber(1 if value else 0)


def _is_generated_parameter_name(name: str) -> bool:
    """Return whether ``name`` belongs to the compiler's ``_N`` namespace."""
    return len(name) > 1 and name[0] == "_" and name[1:].isdigit()


def _positive_rank(value: StaticValue | None) -> int | None:
    """Convert a static number to a safe positive collection rank."""
    if not isinstance(value, RuntimeNumber) or not value.is_finite():
        return None
    if value <= 0 or value > RuntimeNumber(MAX_COMPILE_TIME_RANK):
        return None
    integral = value.to_integral_value()
    if integral != value:
        return None
    return int(integral)


def _contains_result_type(typ: T.Type) -> bool:
    """Return whether a type literal contains the unavailable Result type."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return typ.name.text == "Result" or any(
            _contains_result_type(arg) for arg in typ.args
        )
    if isinstance(typ, T.CollectionType):
        return _contains_result_type(typ.base)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_contains_result_type(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_contains_result_type(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_contains_result_type(item.typ) for item in typ.items)
    if isinstance(typ, T.RowType):
        return _contains_result_type(typ.base) or any(
            _contains_result_type(field.typ) for field in typ.fields
        )
    if isinstance(typ, T.FunctionType):
        return any(
            _contains_result_type(item)
            for item in (typ.params or ()) + (typ.returns or ())
        ) or any(
            _contains_result_type(arg)
            for tag in typ.element_tags
            for arg in tag.args
        )
    if isinstance(typ, T.OverloadSetType):
        return any(_overload_contains_result(overload) for overload in typ.overloads)
    if isinstance(typ, T.AnonymousTraitType):
        return any(
            _overload_contains_result(requirement.overload)
            for requirement in typ.requirements
        )
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _contains_result_type(typ.inner)
    return False


def _overload_contains_result(overload: T.Overload) -> bool:
    """Return whether an overload signature mentions ``Result`` statically."""
    return (
        any(
            _contains_result_type(item)
            for item in overload.params + overload.returns
        )
        or any(
            _contains_result_type(constraint.bound)
            for constraint in overload.generic_constraints
        )
        or any(
            _contains_result_type(arg)
            for tag in overload.element_tags
            for arg in tag.args
        )
    )


def _substitute_overload(
    overload: T.Overload,
    ranks: Mapping[str, int],
    types: Mapping[str, T.Type],
) -> T.Overload:
    """Substitute free static type variables in one overload signature."""
    local_names = {
        constraint.name for constraint in overload.generic_constraints
    }
    free_types = {
        name: typ for name, typ in types.items() if name not in local_names
    }
    return replace(
        overload,
        params=tuple(
            _substitute_type(item, ranks, free_types) for item in overload.params
        ),
        returns=tuple(
            _substitute_type(item, ranks, free_types) for item in overload.returns
        ),
        generic_constraints=tuple(
            replace(
                constraint,
                bound=_substitute_type(constraint.bound, ranks, free_types),
            )
            for constraint in overload.generic_constraints
        ),
        element_tags=frozenset(
            T.ElementTag(
                tag.name,
                tuple(
                    _substitute_type(arg, ranks, free_types) for arg in tag.args
                ),
                tag.absent,
            )
            for tag in overload.element_tags
        ),
    )


def _substitute_type(
    typ: T.Type,
    ranks: Mapping[str, int],
    types: Mapping[str, T.Type],
) -> T.Type:
    """Substitute rank and generic variables inside a static type literal."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return types.get(typ.name, typ)
    if isinstance(typ, T.CollectionType):
        rank = typ.rank
        if isinstance(rank, T.RankVariable):
            rank = ranks.get(rank.name, rank)
        return T.C(type(typ), _substitute_type(typ.base, ranks, types), rank)
    if isinstance(typ, T.NominalType):
        return T.N(
            typ.name,
            *(_substitute_type(arg, ranks, types) for arg in typ.args),
        )
    if isinstance(typ, T.UnionType):
        return T.U(*(_substitute_type(item, ranks, types) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_substitute_type(item, ranks, types) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(_substitute_type(item, ranks, types) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(
                    _substitute_type(item.typ, ranks, types), item.repeated
                )
                for item in typ.items
            )
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            _substitute_type(typ.base, ranks, types),
            *(
                T.Field(field.name, _substitute_type(field.typ, ranks, types))
                for field in typ.fields
            ),
        )
    if isinstance(typ, T.FunctionType):
        tags = tuple(
            T.ElementTag(
                tag.name,
                tuple(_substitute_type(arg, ranks, types) for arg in tag.args),
                tag.absent,
            )
            for tag in typ.element_tags
        )
        if typ.params is None or typ.returns is None:
            return T.Fn(None, None, tags)
        return T.Fn(
            (_substitute_type(item, ranks, types) for item in typ.params),
            (_substitute_type(item, ranks, types) for item in typ.returns),
            tags,
        )
    if isinstance(typ, T.OverloadSetType):
        return T.Overloads(
            *(
                _substitute_overload(overload, ranks, types)
                for overload in typ.overloads
            )
        )
    if isinstance(typ, T.AnonymousTraitType):
        local_names = {generic.text for generic in typ.generics}
        free_types = {
            name: value for name, value in types.items() if name not in local_names
        }
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    _substitute_overload(requirement.overload, ranks, free_types),
                )
                for requirement in typ.requirements
            ),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(
            _substitute_type(typ.inner, ranks, types),
            *typ.tags,
            exact=typ.exact,
        )
    if isinstance(typ, T.ExactType):
        return T.Exact(_substitute_type(typ.inner, ranks, types))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_substitute_type(typ.inner, ranks, types))
    return typ
