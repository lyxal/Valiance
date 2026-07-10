"""Pattern, match, try, indexing, and narrowing helpers."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import field, replace
from decimal import Decimal, InvalidOperation
from itertools import count
from typing import cast

import valiance.types as T
from valiance.asts import (
    ASTNode,
    BindingPatternNode,
    GuardPatternNode,
    IndexSelector,
    ListPatternNode,
    LiteralPatternNode,
    MatchNode,
    MatchPatternNode,
    NumberLiteralNode,
    OrPatternNode,
    RestPatternNode,
    StringLiteralNode,
    TryHandlerNode,
    TypedElementNode,
    TypedFunctionNode,
    TypedNode,
    TypePatternNode,
    WildcardPatternNode,
)
from valiance.asts.nodes import GetVariableNode
from valiance.symbols import Symbol
from valiance.types.relations import merge_stacks

from . import analyser as _core
from . import _analyser_functions as _functions
from . import _analyser_calls as _calls
from . import _analyser_utils as _utils


def _extension_selector_arity(function: TypedFunctionNode) -> int | None:
    """Determine the required arity for extension selector during static analysis."""
    arity: int | None = None
    for overload in function.overloads:
        if len(overload.body) != 1:
            return None
        [body_node] = overload.body
        if not isinstance(body_node, TypedElementNode) or body_node.overload is None:
            return None
        current = len(body_node.overload.params)
        if arity is None:
            arity = current
        elif arity != current:
            return None
    return arity


def _unfold_emitted_type(
    state_types: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
) -> T.Type:
    """Determine the type of unfold emitted during static analysis."""
    if len(returns) <= len(state_types):
        missing = len(state_types) - len(returns)
        next_state = state_types[-missing:] + returns if missing else returns
        return next_state[-1]
    return _optional_present_type(returns[-1])


def _strict_optional_payload_type(typ: T.Type) -> T.Type | None:
    """Return the payload of exactly ``Some[T] | None`` optional types."""
    typ = T.normalize(typ)
    if not isinstance(typ, T.UnionType):
        return None
    found_none = False
    payloads: list[T.Type] = []
    for item in typ.items:
        item = T.normalize(item)
        if isinstance(item, T.NoneTypeNode):
            found_none = True
            continue
        if (
            isinstance(item, T.NominalType)
            and item.name == Symbol("Some")
            and len(item.args) == 1
        ):
            payloads.append(item.args[0])
            continue
        return None
    if not found_none or not payloads:
        return None
    return payloads[0] if len(payloads) == 1 else T.U(*payloads)


def _optional_access_result_type(field_type: T.Type) -> T.Type:
    """Lift a member result into an optional, flattening optional members."""
    return (
        field_type
        if _strict_optional_payload_type(field_type) is not None
        else T.optional(field_type)
    )


def _optional_present_type(typ: T.Type) -> T.Type:
    """Determine the type of optional present during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NoneTypeNode):
        return T.Never()
    if (
        isinstance(typ, T.NominalType)
        and typ.name == Symbol("Some")
        and len(typ.args) == 1
    ):
        return typ.args[0]
    if not isinstance(typ, T.UnionType):
        return typ
    present: list[T.Type] = []
    for item in typ.items:
        item = T.normalize(item)
        if isinstance(item, T.NoneTypeNode):
            continue
        if (
            isinstance(item, T.NominalType)
            and item.name == Symbol("Some")
            and len(item.args) == 1
        ):
            present.append(item.args[0])
        else:
            present.append(item)
    if not present:
        return T.Never()
    return T.U(*present)


def _selector_value_count(selectors: tuple[IndexSelector, ...]) -> int:
    """Compute selector value count during static analysis."""
    count = 0
    for selector in selectors:
        count += bool(selector.start)
        count += bool(selector.stop)
        count += bool(selector.step)
    return count


def _indexed_type(
    receiver_type: T.Type,
    selectors: tuple[IndexSelector, ...],
    spread: bool,
) -> T.Type:
    """Determine the type of indexed during static analysis."""
    typ = T.normalize(receiver_type)
    for index, selector in enumerate(selectors):
        item = typ if selector.is_slice else _single_index_type(typ)
        if index + 1 < len(selectors):
            typ = item
            continue
        if spread:
            return item
        if len(selectors) > 1:
            return T.ExactList(item)
        return item
    return T.V("Indexed")


def _selectors_assignable(
    receiver_type: T.Type,
    selectors: tuple[IndexSelector, ...],
    index_types: tuple[T.Type, ...],
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of selectors assignable during static analysis."""
    expected = _selector_expected_types(receiver_type, selectors)
    return len(expected) == len(index_types) and all(
        T.assignable(_index_value_type(actual), target, ctx)
        for actual, target in zip(index_types, expected, strict=True)
    )


def _index_value_type(typ: T.Type) -> T.Type:
    """Strip data tags from a value used as an index.

    Unit-like tags refine an integer's meaning without changing its suitability
    as a list or string index. Runtime indexing already unwraps tagged values.
    """
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _index_value_type(typ.inner)
    if isinstance(typ, T.UnionType):
        return T.U(*(_index_value_type(item) for item in typ.items))
    return typ


def _selector_expected_types(
    receiver_type: T.Type,
    selectors: tuple[IndexSelector, ...],
) -> tuple[T.Type, ...]:
    """Determine the types used for selector expected during static analysis."""
    typ = T.normalize(receiver_type)
    expected: list[T.Type] = []
    for selector in selectors:
        key_type = _single_index_key_type(typ)
        slice_bound_type = T.U(T.Integer, T.ExactList(T.Integer))
        if selector.start:
            expected.append(slice_bound_type if selector.is_slice else key_type)
        if selector.stop:
            expected.append(slice_bound_type)
        if selector.step:
            expected.append(T.Integer)
        typ = typ if selector.is_slice else _single_index_type(typ)
    return tuple(expected)


def _indexed_assignment_type(
    receiver_type: T.Type,
    selectors: tuple[IndexSelector, ...],
    value_type: T.Type,
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of indexed assignment during static analysis."""
    if len(selectors) == 1 and selectors[0].is_slice:
        slice_type = _indexed_type(receiver_type, selectors, spread=False)
        if T.assignable(value_type, slice_type, ctx):
            return receiver_type
        return _single_index_assignment_type(receiver_type, value_type, ctx)
    if len(selectors) != 1:
        item_type = _indexed_type(receiver_type, selectors, spread=False)
        return receiver_type if T.assignable(value_type, item_type, ctx) else None
    return _single_index_assignment_type(receiver_type, value_type, ctx)


def _single_index_assignment_type(
    receiver_type: T.Type,
    value_type: T.Type,
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of single index assignment during static analysis."""
    typ = T.normalize(receiver_type)
    if isinstance(typ, T.TaggedType):
        updated = _single_index_assignment_type(typ.inner, value_type, ctx)
        return None if updated is None else T.Tagged(updated, *typ.tags)
    if isinstance(typ, T.CollectionType):
        if T.assignable(value_type, typ.base, ctx):
            return receiver_type
        if T.assignable(typ.base, value_type, ctx):
            return T.C(type(typ), value_type, typ.rank)
        return None
    if isinstance(typ, T.NominalType):
        if typ.name.text == "Dict" and len(typ.args) == 2:
            key, item = typ.args
            if T.assignable(value_type, item, ctx):
                return receiver_type
            if T.assignable(item, value_type, ctx):
                return T.N(typ.name, key, value_type)
            return None
        if typ.name.text == "String":
            return receiver_type if T.assignable(value_type, T.String, ctx) else None
    item_type = _single_index_type(receiver_type)
    return receiver_type if T.assignable(value_type, item_type, ctx) else None


def _single_index_key_type(typ: T.Type) -> T.Type:
    """Determine the type of single index key during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _single_index_key_type(typ.inner)
    if isinstance(typ, T.UnionType):
        return T.U(*(_single_index_key_type(item) for item in typ.items))
    if (
        isinstance(typ, T.NominalType)
        and typ.name.text == "Dict"
        and len(typ.args) == 2
    ):
        return typ.args[0]
    return T.Number


def _single_index_type(typ: T.Type) -> T.Type:
    """Determine the type of single index during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _single_index_type(typ.inner)
    if isinstance(typ, T.UnionType):
        return T.U(*(_single_index_type(item) for item in typ.items))
    if isinstance(typ, T.CollectionType):
        return T.collection_item_type(typ)
    if isinstance(typ, T.TupleType):
        return T.U(*typ.params) if typ.params else T.Never()
    if isinstance(typ, T.NominalType):
        if typ.name.text == "String":
            return T.String
        if typ.name.text == "Dict" and len(typ.args) == 2:
            return typ.args[1]
    return T.V("Indexed")


def _nominal_name(typ: T.Type) -> Symbol | None:
    """Return the canonical name for nominal during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return typ.name
    return None


def _closed_match_members(
    env: T.Environment,
    name: Symbol,
) -> tuple[Symbol, ...] | None:
    """Compute closed match members during static analysis."""
    variant = env.lookup_variant(name)
    if variant is not None:
        return variant.members
    enum = env.lookup_enum(name)
    if enum is not None:
        return tuple(member.name for member in enum.members)
    return None


def _resolve_closed_member(
    expected: tuple[Symbol, ...],
    typ: T.Type,
) -> Symbol | None:
    """Compute resolve closed member during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NoneTypeNode):
        name = Symbol("None")
    else:
        name = _nominal_name(typ)
    if name is None:
        return None
    for member in expected:
        if name == member or name.text == member.text.rsplit(".", 1)[-1]:
            return member
    return None


def _lookup_object_by_suffix(
    env: T.Environment,
    name: Symbol,
) -> T.ObjectDefinition | None:
    """Resolve an object by its visible qualified or unique short name."""
    direct = env.lookup_object(name)
    if direct is not None:
        return direct
    current: T.Environment | None = env
    while current is not None:
        matches = tuple(
            definition
            for candidate, definition in current.objects.items()
            if candidate.text.rsplit(".", 1)[-1] == name.text
        )
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return None
        current = current.parent
    return None


def _pattern_object_definition(
    pattern_type: T.Type | None,
    subject_type: T.Type,
    env: T.Environment,
) -> T.ObjectDefinition | None:
    """Resolve the concrete object destructured by a type pattern."""
    pattern_name = None if pattern_type is None else _nominal_name(pattern_type)
    subject_name = _nominal_name(subject_type)
    if pattern_name is not None and subject_name is not None:
        members = _closed_match_members(env, subject_name)
        if members is not None:
            resolved = _resolve_closed_member(members, pattern_type)
            if resolved is not None:
                definition = env.lookup_object(resolved)
                if definition is not None:
                    return definition
    if pattern_name is not None:
        definition = _lookup_object_by_suffix(env, pattern_name)
        if definition is not None:
            return definition
    if subject_name is not None:
        return _lookup_object_by_suffix(env, subject_name)
    return None


def _invalid_destructure_arity(
    pattern: MatchPatternNode,
    subject_type: T.Type,
    env: T.Environment,
) -> tuple[TypePatternNode, Symbol, int, int] | None:
    """Return the first object pattern whose field count cannot match."""
    if isinstance(pattern, BindingPatternNode):
        return _invalid_destructure_arity(pattern.pattern, subject_type, env)
    if isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            invalid = _invalid_destructure_arity(option, subject_type, env)
            if invalid is not None:
                return invalid
        return None
    if isinstance(pattern, ListPatternNode):
        item_type = T.collection_item_type(subject_type) or T.V("_matched_item")
        for item in pattern.items:
            nested_type = (
                T.ExactList(item_type)
                if _is_rest_match_pattern(item)
                else item_type
            )
            invalid = _invalid_destructure_arity(item, nested_type, env)
            if invalid is not None:
                return invalid
        return None
    if not isinstance(pattern, TypePatternNode):
        return None

    definition = _pattern_object_definition(pattern.typ, subject_type, env)
    if pattern.fields and definition is not None:
        actual = len(pattern.fields)
        expected = len(definition.attributes)
        if actual != expected:
            return pattern, definition.name, actual, expected
    field_types = _destructure_field_types(pattern, subject_type, env)
    if pattern.fields and definition is None and field_types:
        actual = len(pattern.fields)
        expected = len(field_types)
        if actual != expected:
            pattern_name = _nominal_name(pattern.typ) or Symbol("value")
            return pattern, pattern_name, actual, expected
    for index, field in enumerate(pattern.fields):
        field_type = (
            field_types[index]
            if index < len(field_types)
            else T.V(f"_matched_field_{index}")
        )
        invalid = _invalid_destructure_arity(field, field_type, env)
        if invalid is not None:
            return invalid
    return None


def _covered_closed_members(
    pattern: MatchPatternNode,
    subject_type: T.Type,
    expected: tuple[Symbol, ...],
    env: T.Environment,
) -> tuple[Symbol, ...]:
    """Return closed members that this pattern accepts on every value path."""
    if _has_repeated_match_bindings((pattern,)):
        return ()
    if isinstance(pattern, BindingPatternNode):
        return _covered_closed_members(
            pattern.pattern,
            subject_type,
            expected,
            env,
        )
    if isinstance(pattern, OrPatternNode):
        covered: set[Symbol] = set()
        for option in pattern.options:
            covered.update(
                _covered_closed_members(option, subject_type, expected, env)
            )
        return tuple(sorted(covered, key=str))
    if not isinstance(pattern, TypePatternNode) or pattern.typ is None:
        return ()
    member = _resolve_closed_member(expected, pattern.typ)
    if member is None or pattern.guard:
        return ()
    if not pattern.fields:
        return (member,)
    definition = env.lookup_object(member)
    if definition is None or len(pattern.fields) != len(definition.attributes):
        return ()
    if all(
        _pattern_is_irrefutable(field, attribute.typ, env)
        for field, attribute in zip(
            pattern.fields,
            definition.attributes,
            strict=True,
        )
    ):
        return (member,)
    return ()


def _pattern_is_irrefutable(
    pattern: MatchPatternNode,
    subject_type: T.Type,
    env: T.Environment,
) -> bool:
    """Return whether a pattern succeeds for every value of ``subject_type``."""
    if _has_repeated_match_bindings((pattern,)):
        return False
    if isinstance(pattern, (WildcardPatternNode, RestPatternNode)):
        return True
    if isinstance(pattern, BindingPatternNode):
        return _pattern_is_irrefutable(pattern.pattern, subject_type, env)
    if isinstance(pattern, OrPatternNode):
        return any(
            _pattern_is_irrefutable(option, subject_type, env)
            for option in pattern.options
        )
    if not isinstance(pattern, TypePatternNode) or pattern.guard:
        return False
    if pattern.typ is not None and not T.assignable(
        subject_type,
        pattern.typ,
        env.context,
    ):
        return False
    if not pattern.fields:
        return True
    field_types = _destructure_field_types(pattern, subject_type, env)
    if len(pattern.fields) != len(field_types):
        return False
    return all(
        _pattern_is_irrefutable(field, field_type, env)
        for field, field_type in zip(
            pattern.fields,
            field_types,
            strict=True,
        )
    )


def _try_handler_output(
    output: _core.AnalysisBranch,
    branch: _core.AnalysisBranch,
    handler: TryHandlerNode,
) -> _core.AnalysisBranch:
    """Compute try handler output during static analysis."""
    handler_result = _calls._returns_result_type(output.stack.items)
    if handler_result is None:
        handler_result = T.NoneType()
    return (
        _calls._refine_branch_like(branch, output)
        .with_stack(branch.stack.push(T.N(Symbol("PanicError"), handler_result)))
        .emit(TypedNode(handler, handler_result))
    )


def _join_try_output(
    branch: _core.AnalysisBranch,
    joined: _core.AnalysisBranch | None,
    output: _core.AnalysisBranch,
    ctx: T.Context,
) -> _core.AnalysisBranch | None:
    """Join try output during static analysis."""
    if joined is None:
        return output
    if joined.inputs != output.inputs:
        return None
    stack = merge_stacks(joined.stack, output.stack, ctx)
    variables = joined.variables.merge_against(
        output.variables,
        branch.variables,
        ctx,
    )
    return (
        _calls._refine_branch_like(branch, joined)
        .with_stack(stack)
        .with_variables(variables)
        .with_element_tags(output.element_tags)
        .with_data_element_uses(output.data_element_uses)
    )


def _typed_block(
    outputs: _core.BranchSet,
    start: int,
    source_nodes: tuple[ASTNode, ...],
) -> tuple[ASTNode | TypedNode, ...]:
    """Return a stable typed block, falling back when branch metadata diverges."""
    suffixes = tuple(output.typed_body[start:] for output in outputs)
    if not suffixes:
        return source_nodes
    first = suffixes[0]
    if all(suffix == first for suffix in suffixes[1:]):
        return first
    return source_nodes


def _match_case_output(
    output: _core.AnalysisBranch,
    baseline: _core.AnalysisBranch,
    node: MatchNode,
) -> _core.AnalysisBranch:
    """Compute match case output during static analysis."""
    candidate = output
    if candidate.break_type is not None:
        typ = candidate.break_type
        if typ is None:
            typ = _calls._returns_result_type(candidate.stack.items)
        candidate = candidate.emit(TypedNode(node, typ))
    return replace(
        candidate,
        typed_body=baseline.typed_body,
        input_mode=baseline.input_mode,
        cycle_params=baseline.cycle_params,
        cycle_index=baseline.cycle_index,
    )


def _join_match_output(
    *,
    original: _core.AnalysisBranch,
    baseline: _core.AnalysisBranch,
    joined: _core.AnalysisBranch | None,
    candidate: _core.AnalysisBranch,
    ctx: T.Context,
) -> _core.AnalysisBranch | None:
    """Join match output during static analysis."""
    if joined is None:
        return candidate
    if joined.inputs != candidate.inputs:
        merged_inputs = _utils._merge_branch_inputs(
            joined.inputs,
            candidate.inputs,
            ctx,
        )
        if merged_inputs is None:
            return None
    else:
        merged_inputs = joined.inputs
    stack = merge_stacks(joined.stack, candidate.stack, ctx)
    variables = joined.variables.merge_against(
        candidate.variables,
        baseline.variables,
        ctx,
    )
    base = (
        _calls._refine_branch_like(original, joined)
        if len(original.inputs) == len(joined.inputs)
        else joined
    )
    return replace(
        base.with_stack(stack)
        .with_variables(variables)
        .with_element_tags(candidate.element_tags)
        .with_data_element_uses(candidate.data_element_uses),
        inputs=merged_inputs,
    )


def _match_arity(node: MatchNode) -> int | None:
    """Determine the required arity for match during static analysis."""
    arity: int | None = None
    for case in node.cases:
        case_arity = len(case.patterns)
        if arity is None:
            arity = case_arity
        elif case_arity != arity:
            return None
    return arity


def _match_subject_pattern_type(
    branch: _core.AnalysisBranch,
    node: MatchNode,
    index: int,
    env: T.Environment,
) -> T.Type:
    """Determine the type of match subject pattern during static analysis."""
    inferred = tuple(
        typ
        for case in node.cases
        if index < len(case.patterns)
        if (typ := _pattern_subject_type(case.patterns[index], env.context)) is not None
    )
    if not inferred:
        return _functions._anonymous_type_var(branch, index + 1)
    result = inferred[0]
    for typ in inferred[1:]:
        result = T.merge_types(result, typ, env.context)
    return result


def _pattern_subject_type(
    pattern: MatchPatternNode,
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of pattern subject during static analysis."""
    if isinstance(pattern, TypePatternNode):
        return pattern.typ
    if isinstance(pattern, BindingPatternNode):
        return _pattern_subject_type(pattern.pattern, ctx)
    if isinstance(pattern, LiteralPatternNode):
        if isinstance(pattern.value, NumberLiteralNode):
            return T.Number
        if isinstance(pattern.value, StringLiteralNode):
            return T.String
        return None
    if isinstance(pattern, ListPatternNode):
        item_types = tuple(
            item_type
            for item in pattern.items
            if not isinstance(item, RestPatternNode)
            if (item_type := _pattern_subject_type(item, ctx)) is not None
        )
        if not item_types:
            return None
        item_result = item_types[0]
        for item_type in item_types[1:]:
            item_result = T.merge_types(item_result, item_type, ctx)
        return T.ExactList(item_result)
    if isinstance(pattern, OrPatternNode):
        option_types = tuple(
            typ
            for option in pattern.options
            if (typ := _pattern_subject_type(option, ctx)) is not None
        )
        if not option_types:
            return None
        result = option_types[0]
        for typ in option_types[1:]:
            result = T.merge_types(result, typ, ctx)
        return result
    return None


def _is_default_match_case(patterns: tuple[MatchPatternNode, ...]) -> bool:
    """Return whether a case accepts every combination of subject values."""
    return (
        bool(patterns)
        and not _has_repeated_match_bindings(patterns)
        and all(_is_default_match_pattern(pattern) for pattern in patterns)
    )


def _is_default_match_pattern(pattern: MatchPatternNode) -> bool:
    """Return whether a pattern unconditionally accepts every subject value."""
    if _has_repeated_match_bindings((pattern,)):
        return False
    if isinstance(pattern, (WildcardPatternNode, RestPatternNode)):
        return True
    if isinstance(pattern, BindingPatternNode):
        return _is_default_match_pattern(pattern.pattern)
    if isinstance(pattern, OrPatternNode):
        return any(_is_default_match_pattern(option) for option in pattern.options)
    return (
        isinstance(pattern, TypePatternNode)
        and pattern.typ is None
        and not pattern.fields
        and not pattern.guard
    )


def _match_case_variables(
    variables: _core.BranchVariables,
    patterns: tuple[MatchPatternNode, ...],
    subject_types: tuple[T.Type, ...],
    env: T.Environment,
) -> _core.BranchVariables:
    """Determine variable facts for match case during static analysis."""
    result = variables
    if subject_types:
        result = result.with_block_local(Symbol("top"), subject_types[0])
    for pattern, subject_type in zip(patterns, subject_types, strict=True):
        for name, typ in _pattern_binding_types(pattern, subject_type, env).items():
            result = result.with_block_local(name, typ)
    return result


def _match_subject_variables(
    branch: _core.AnalysisBranch,
    arity: int,
) -> tuple[Symbol | None, ...]:
    """Determine variable facts for match subject during static analysis."""
    if arity <= 0 or len(branch.typed_body) < arity:
        return ()
    subject_nodes = branch.typed_body[-arity:]
    names: list[Symbol | None] = []
    for typed in subject_nodes:
        if isinstance(typed.node, GetVariableNode):
            names.append(typed.node.name)
        else:
            names.append(None)
    return tuple(reversed(names))


def _refine_match_subject_variables(
    variables: _core.BranchVariables,
    subject_variables: tuple[Symbol | None, ...],
    patterns: tuple[MatchPatternNode, ...],
    subject_types: tuple[T.Type, ...],
    previous_patterns: tuple[tuple[MatchPatternNode, ...], ...],
    env: T.Environment,
) -> _core.BranchVariables:
    """Refine match subject variables during static analysis."""
    result = variables
    for index, name in enumerate(subject_variables):
        if name is None or index >= len(subject_types) or index >= len(patterns):
            continue
        narrowed = _match_case_subject_type(
            patterns[index],
            subject_types[index],
            _independently_excluding_patterns(
                index,
                previous_patterns,
                subject_types,
                env,
            ),
            env,
        )
        if narrowed is None:
            continue
        result = _narrow_variable(result, name, narrowed)
    return result


def _independently_excluding_patterns(
    subject_index: int,
    previous_cases: tuple[tuple[MatchPatternNode, ...], ...],
    subject_types: tuple[T.Type, ...],
    env: T.Environment,
) -> tuple[MatchPatternNode, ...]:
    """Return prior patterns that independently exclude one subject branch.

    A multi-subject case is conjunctive. Failure of ``(Number, Number)`` does
    not imply that either subject is non-numeric; only a case whose other
    coordinates are irrefutable can safely narrow the selected coordinate.
    """
    result: list[MatchPatternNode] = []
    for case in previous_cases:
        if (
            subject_index >= len(case)
            or len(case) != len(subject_types)
            or _has_repeated_match_bindings(case)
        ):
            continue
        if all(
            index == subject_index
            or _pattern_is_irrefutable(pattern, subject_types[index], env)
            for index, pattern in enumerate(case)
        ):
            result.append(case[subject_index])
    return tuple(result)


def _narrow_variable(
    variables: _core.BranchVariables,
    name: Symbol,
    typ: T.Type,
) -> _core.BranchVariables:
    """Compute narrow variable during static analysis."""
    if _utils._lookup(variables.block_locals, name) is not None:
        return _core.BranchVariables(
            function_locals=variables.function_locals,
            parameters=variables.parameters,
            captures=variables.captures,
            block_locals=_utils._set_item(variables.block_locals, name, typ),
            function_constants=variables.function_constants,
            block_constants=variables.block_constants,
        )
    if _utils._lookup(variables.function_locals, name) is not None:
        return _core.BranchVariables(
            function_locals=_utils._set_item(variables.function_locals, name, typ),
            parameters=variables.parameters,
            captures=variables.captures,
            block_locals=variables.block_locals,
            function_constants=variables.function_constants,
            block_constants=variables.block_constants,
        )
    if _utils._lookup(variables.parameters, name) is not None:
        return _core.BranchVariables(
            function_locals=variables.function_locals,
            parameters=_utils._set_item(variables.parameters, name, typ),
            captures=variables.captures,
            block_locals=variables.block_locals,
            function_constants=variables.function_constants,
            block_constants=variables.block_constants,
        )
    if _utils._lookup(variables.captures, name) is not None:
        return _core.BranchVariables(
            function_locals=variables.function_locals,
            parameters=variables.parameters,
            captures=_utils._set_item(variables.captures, name, typ),
            block_locals=variables.block_locals,
            function_constants=variables.function_constants,
            block_constants=variables.block_constants,
        )
    return variables


def _match_case_subject_type(
    pattern: MatchPatternNode,
    subject_type: T.Type,
    previous_patterns: tuple[MatchPatternNode, ...],
    env: T.Environment,
) -> T.Type | None:
    """Determine the type of match case subject during static analysis."""
    if not _is_default_match_pattern(pattern):
        return _successful_pattern_subject_type(pattern, subject_type, env)
    excluded = tuple(
        typ
        for previous in previous_patterns
        for typ in _fully_excluded_pattern_types(previous, env)
    )
    if not excluded:
        return subject_type
    return _subtract_match_types(subject_type, excluded, env.context)


def _successful_pattern_subject_type(
    pattern: MatchPatternNode,
    subject_type: T.Type,
    env: T.Environment,
) -> T.Type:
    """Return a safe subject refinement shared by every successful path."""
    if isinstance(pattern, BindingPatternNode):
        return _successful_pattern_subject_type(pattern.pattern, subject_type, env)
    if isinstance(pattern, TypePatternNode):
        return pattern.typ or subject_type
    if isinstance(pattern, LiteralPatternNode):
        if isinstance(pattern.value, NumberLiteralNode):
            return T.Number
        if isinstance(pattern.value, StringLiteralNode):
            return T.String
        return subject_type
    if isinstance(pattern, OrPatternNode) and pattern.options:
        refinements = tuple(
            _successful_pattern_subject_type(option, subject_type, env)
            for option in pattern.options
        )
        result = refinements[0]
        for refinement in refinements[1:]:
            result = T.merge_types(result, refinement, env.context)
        return result
    # Guards, expression patterns, and structural list patterns can constrain
    # values without proving a narrower type for the entire subject.
    return subject_type


def _fully_excluded_pattern_types(
    pattern: MatchPatternNode,
    env: T.Environment,
) -> tuple[T.Type, ...]:
    """Return type branches completely consumed by an earlier match pattern."""
    if isinstance(pattern, BindingPatternNode):
        return _fully_excluded_pattern_types(pattern.pattern, env)
    if isinstance(pattern, OrPatternNode):
        return tuple(
            typ
            for option in pattern.options
            for typ in _fully_excluded_pattern_types(option, env)
        )
    if (
        isinstance(pattern, TypePatternNode)
        and pattern.typ is not None
        and _pattern_is_irrefutable(pattern, pattern.typ, env)
    ):
        return (pattern.typ,)
    return ()


def _subtract_match_types(
    subject_type: T.Type,
    excluded: tuple[T.Type, ...],
    ctx: T.Context,
) -> T.Type:
    """Determine the types used for subtract match during static analysis."""
    subject_type = T.normalize(subject_type)
    if not isinstance(subject_type, T.UnionType):
        return subject_type
    remaining = tuple(
        item
        for item in subject_type.items
        if not any(T.assignable(item, typ, ctx) for typ in excluded)
    )
    if not remaining:
        return T.NeverType()
    return T.U(*remaining)


def _pattern_binding_types(
    pattern: MatchPatternNode,
    subject_type: T.Type,
    env: T.Environment,
) -> dict[Symbol, T.Type]:
    """Return bindings guaranteed to exist when this pattern succeeds."""
    result: dict[Symbol, T.Type] = {}

    def add(name: Symbol, typ: T.Type) -> None:
        """Merge one guaranteed binding into the pattern-local type map."""
        existing = result.get(name)
        result[name] = (
            typ
            if existing is None
            else T.merge_types(existing, typ, env.context)
        )

    if isinstance(pattern, BindingPatternNode):
        result.update(_pattern_binding_types(pattern.pattern, subject_type, env))
        add(
            pattern.name,
            _narrowed_pattern_type(pattern.pattern, subject_type, env),
        )
        return result
    if isinstance(pattern, RestPatternNode) and pattern.name is not None:
        add(pattern.name, subject_type)
        return result
    if isinstance(pattern, TypePatternNode):
        if pattern.name is not None:
            add(pattern.name, pattern.typ or subject_type)
        field_types = _destructure_field_types(pattern, subject_type, env)
        for index, field in enumerate(pattern.fields):
            field_type = (
                field_types[index]
                if index < len(field_types)
                else T.V(f"_matched_field_{index}")
            )
            for name, typ in _pattern_binding_types(field, field_type, env).items():
                add(name, typ)
        return result
    if isinstance(pattern, ListPatternNode):
        item_type = T.collection_item_type(subject_type) or T.V("_matched_item")
        for item in pattern.items:
            nested_type = (
                T.ExactList(item_type)
                if _is_rest_match_pattern(item)
                else item_type
            )
            for name, typ in _pattern_binding_types(item, nested_type, env).items():
                add(name, typ)
        return result
    if isinstance(pattern, OrPatternNode):
        option_bindings = tuple(
            _pattern_binding_types(option, subject_type, env)
            for option in pattern.options
        )
        if not option_bindings:
            return result
        names = set(option_bindings[0])
        for option in option_bindings[1:]:
            names.intersection_update(option)
        for name in names:
            types = tuple(option[name] for option in option_bindings)
            typ = types[0]
            for other in types[1:]:
                typ = T.merge_types(typ, other, env.context)
            add(name, typ)
        return result
    return result


def _is_rest_match_pattern(pattern: MatchPatternNode) -> bool:
    """Return whether a list item consumes and optionally binds a rest slice."""
    return isinstance(pattern, RestPatternNode) or (
        isinstance(pattern, BindingPatternNode)
        and _is_rest_match_pattern(pattern.pattern)
    )


def _narrowed_pattern_type(
    pattern: MatchPatternNode,
    subject_type: T.Type,
    env: T.Environment,
) -> T.Type:
    """Return the value type bound by a successful wrapper binding."""
    return _successful_pattern_subject_type(pattern, subject_type, env)


def _pattern_binding_counts(pattern: MatchPatternNode) -> dict[Symbol, int]:
    """Return maximum binding occurrences along one successful pattern path."""

    def combine(
        left: dict[Symbol, int],
        right: dict[Symbol, int],
    ) -> dict[Symbol, int]:
        """Add binding counts from conjunctive child patterns."""
        result = dict(left)
        for name, count_ in right.items():
            result[name] = result.get(name, 0) + count_
        return result

    if isinstance(pattern, BindingPatternNode):
        result = _pattern_binding_counts(pattern.pattern)
        result[pattern.name] = result.get(pattern.name, 0) + 1
        return result
    if isinstance(pattern, RestPatternNode):
        return {} if pattern.name is None else {pattern.name: 1}
    if isinstance(pattern, TypePatternNode):
        result = {} if pattern.name is None else {pattern.name: 1}
        for field in pattern.fields:
            result = combine(result, _pattern_binding_counts(field))
        return result
    if isinstance(pattern, ListPatternNode):
        result: dict[Symbol, int] = {}
        for item in pattern.items:
            result = combine(result, _pattern_binding_counts(item))
        return result
    if isinstance(pattern, OrPatternNode):
        result: dict[Symbol, int] = {}
        for option in pattern.options:
            for name, count_ in _pattern_binding_counts(option).items():
                result[name] = max(result.get(name, 0), count_)
        return result
    return {}


def _has_repeated_match_bindings(
    patterns: tuple[MatchPatternNode, ...],
) -> bool:
    """Return whether a successful case path can bind one name twice.

    Reusing a name is an equality constraint at runtime, so a syntactically
    catch-all pattern such as ``$x = _, $x = _`` is still refutable.
    """
    counts: dict[Symbol, int] = {}
    for pattern in patterns:
        for name, count_ in _pattern_binding_counts(pattern).items():
            counts[name] = counts.get(name, 0) + count_
    return any(count_ > 1 for count_ in counts.values())


def _pattern_bound_names(pattern: MatchPatternNode) -> frozenset[Symbol]:
    """Return every name that at least one successful pattern path may bind."""
    names: set[Symbol] = set()
    if isinstance(pattern, BindingPatternNode):
        names.add(pattern.name)
        names.update(_pattern_bound_names(pattern.pattern))
    elif isinstance(pattern, RestPatternNode) and pattern.name is not None:
        names.add(pattern.name)
    elif isinstance(pattern, TypePatternNode):
        if pattern.name is not None:
            names.add(pattern.name)
        for field in pattern.fields:
            names.update(_pattern_bound_names(field))
    elif isinstance(pattern, ListPatternNode):
        for item in pattern.items:
            names.update(_pattern_bound_names(item))
    elif isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            names.update(_pattern_bound_names(option))
    return frozenset(names)


def _uncheckable_runtime_pattern_type(
    pattern: MatchPatternNode,
) -> tuple[MatchPatternNode, T.Type] | None:
    """Return the first pattern type the runtime cannot discriminate."""
    if isinstance(pattern, TypePatternNode):
        if pattern.typ is not None:
            invalid = _uncheckable_runtime_type(pattern.typ)
            if invalid is not None:
                return pattern, invalid
        for field_pattern in pattern.fields:
            nested = _uncheckable_runtime_pattern_type(field_pattern)
            if nested is not None:
                return nested
        return None
    if isinstance(pattern, BindingPatternNode):
        return _uncheckable_runtime_pattern_type(pattern.pattern)
    if isinstance(pattern, GuardPatternNode):
        return None
    if isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            nested = _uncheckable_runtime_pattern_type(option)
            if nested is not None:
                return nested
        return None
    if isinstance(pattern, ListPatternNode):
        for item in pattern.items:
            nested = _uncheckable_runtime_pattern_type(item)
            if nested is not None:
                return nested
    return None


def _uncheckable_runtime_type(typ: T.Type) -> T.Type | None:
    """Return a type whose values carry no usable runtime discriminator."""
    typ = T.normalize(typ)
    if isinstance(
        typ,
        (
            T.FunctionType,
            T.OverloadSetType,
            T.AnonymousTraitType,
            T.RowType,
            T.VariadicTupleType,
        ),
    ):
        return typ
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _uncheckable_runtime_type(typ.inner)
    if isinstance(typ, T.UnionType):
        for item in typ.items:
            invalid = _uncheckable_runtime_type(item)
            if invalid is not None:
                return invalid
    if isinstance(typ, T.IntersectionType):
        for item in typ.items:
            invalid = _uncheckable_runtime_type(item)
            if invalid is not None:
                return invalid
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            invalid = _uncheckable_runtime_type(item)
            if invalid is not None:
                return invalid
    if isinstance(typ, T.CollectionType):
        return _uncheckable_runtime_type(typ.base)
    return None


def _or_pattern_binding_mismatch(
    pattern: MatchPatternNode,
) -> tuple[Symbol, ...]:
    """Return names not bound by every alternative of a nested or-pattern."""
    children: tuple[MatchPatternNode, ...] = ()
    if isinstance(pattern, BindingPatternNode):
        children = (pattern.pattern,)
    elif isinstance(pattern, ListPatternNode):
        children = pattern.items
    elif isinstance(pattern, TypePatternNode):
        children = pattern.fields
    elif isinstance(pattern, OrPatternNode):
        children = pattern.options

    for child in children:
        mismatch = _or_pattern_binding_mismatch(child)
        if mismatch:
            return mismatch

    if not isinstance(pattern, OrPatternNode) or not pattern.options:
        return ()
    bound = tuple(_pattern_bound_names(option) for option in pattern.options)
    union = set().union(*bound)
    intersection = set(bound[0])
    for names in bound[1:]:
        intersection.intersection_update(names)
    return tuple(sorted(union - intersection, key=str))


def _destructure_field_types(
    pattern: TypePatternNode,
    subject_type: T.Type,
    env: T.Environment,
) -> tuple[T.Type, ...]:
    """Return declared field types for a type pattern when they are known."""
    pattern_type = None if pattern.typ is None else T.normalize(pattern.typ)
    if (
        isinstance(pattern_type, T.NominalType)
        and pattern_type.name in {Symbol("Some"), Symbol("OK")}
        and len(pattern_type.args) == 1
    ):
        return pattern_type.args
    definition = _pattern_object_definition(pattern.typ, subject_type, env)
    if definition is None:
        return ()
    return tuple(attribute.typ for attribute in definition.attributes)


def _match_pattern_guards(
    patterns: tuple[MatchPatternNode, ...],
    subject_types: tuple[T.Type, ...],
) -> Iterator[tuple[tuple[ASTNode, ...], T.Type]]:
    """Compute match pattern guards during static analysis."""
    for pattern, subject_type in zip(patterns, subject_types, strict=True):
        yield from _pattern_guards(pattern, subject_type)


def _pattern_guards(
    pattern: MatchPatternNode,
    subject_type: T.Type,
) -> Iterator[tuple[tuple[ASTNode, ...], T.Type]]:
    """Compute pattern guards during static analysis."""
    if isinstance(pattern, GuardPatternNode):
        yield pattern.condition, subject_type
    elif isinstance(pattern, TypePatternNode) and pattern.guard:
        yield pattern.guard, pattern.typ or subject_type
    elif isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            yield from _pattern_guards(option, subject_type)
    elif isinstance(pattern, ListPatternNode):
        item_type = T.collection_item_type(subject_type) or T.V("_matched_item")
        for item in pattern.items:
            yield from _pattern_guards(item, item_type)
    elif isinstance(pattern, BindingPatternNode):
        yield from _pattern_guards(pattern.pattern, subject_type)
