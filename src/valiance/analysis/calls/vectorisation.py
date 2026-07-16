"""Vectorisation, rank solving, and call-result propagation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from itertools import count, permutations
from typing import cast

import valiance.vtypes as T
import valiance.analysis.contracts.where_clauses as static_where
from valiance.asts import (
    ASTNode,
    CallArgument,
    ElementNode,
    FunctionNode,
    GetVariableNode,
    FunctionOverloadTyping,
    TypedFunctionNode,
    TypedNode,
)
from valiance.vtypes.symbols import Symbol

from .. import analyser as _core
from . import callable_values as _functions
from .. import _analyser_utils as _utils



_MAX_RANK_BINDING_CANDIDATES = 1_024

def _candidate_apply_overload_to_branch(*args, **kwargs):
    """Lazily call the core candidate applicator without an import cycle."""
    from .candidates import _apply_overload_to_branch
    return _apply_overload_to_branch(*args, **kwargs)


def _candidate_call_site_static_overload(*args, **kwargs):
    """Lazily build a static call-site overload without an import cycle."""
    from .candidates import _call_site_static_overload
    return _call_site_static_overload(*args, **kwargs)

def _call_site_explicit_args_match(
    params: tuple[T.Type, ...],
    args: tuple[T.Type, ...],
    ctx: T.Context,
) -> bool:
    """Return whether explicit call arguments match an overload."""
    return all(
        _functions._call_site_placeholder_accepts(param, arg, ctx)
        for param, arg in zip(params, args, strict=True)
    )

def _call_site_checked_overload_signature(
    overload: T.Overload,
    call_params: tuple[T.Type, ...],
    ctx: T.Context,
    analyser: _core.Analyser | None,
    *,
    rank_values: dict[str, int] | None = None,
    type_values: dict[str, T.Type] | None = None,
    where_evaluated: bool = False,
    static_values: dict[str, int] | None = None,
) -> T.Overload | None:
    """Build the signature for call site checked overload during static analysis."""
    if callable(overload.call_site_body):
        if (
            analyser is not None
            and getattr(overload.call_site_body, "__name__", "") == "_call_call_site"
            and call_params
        ):
            function_type = call_params[-1]
            stack = call_params[:-1]
            for callable_index, candidate in enumerate(
                _functions._callable_overloads(function_type)
            ):
                declared = _candidate_call_site_static_overload(candidate)
                uses_static_values = bool(
                    declared.where_clause
                    or static_where.rank_variable_names(
                        declared.params + declared.returns
                    )
                )
                if (
                    isinstance(candidate.call_site_body, tuple)
                    and len(candidate.call_site_body) == 2
                    and not uses_static_values
                ):
                    outer, node = candidate.call_site_body
                    if not isinstance(outer, _core.AnalysisBranch) or not isinstance(
                        node, FunctionNode
                    ):
                        continue
                    analysis = analyser._analyse_function_at_call_site(
                        outer,
                        node,
                        stack,
                    )
                    if analysis is None:
                        continue
                    candidates = _functions._callable_overloads(analysis.typ)
                    if len(candidates) != 1:
                        continue
                    concrete = candidates[0]
                    return T.Overload(
                        (*concrete.params, function_type),
                        concrete.returns,
                        call_site_body=len(concrete.params),
                        runtime_static_values=concrete.runtime_static_values,
                    )

                arity = len(declared.params)
                if len(stack) < arity:
                    continue
                explicit = stack[-arity:] if arity else ()
                if uses_static_values:
                    application = _candidate_apply_overload_to_branch(
                        candidate,
                        explicit,
                        _core.AnalysisBranch(),
                        ctx,
                        analyser=analyser,
                    )
                    if application is None:
                        continue
                    applied = application.applied
                    return T.Overload(
                        (*explicit, function_type),
                        applied.actual_returns,
                        call_site_body=len(explicit),
                        runtime_static_values=(
                            callable_index,
                            "__call_static__",
                            applied.vectorised,
                            applied.vectorised_depths,
                            applied.vectorised_target_ranks,
                            *applied.runtime_static_values,
                        ),
                    )

                applied = T.try_apply_overload(candidate, explicit, ctx).applied
                if applied is None:
                    continue
                concrete_function_type = T.Fn(
                    explicit,
                    applied.actual_returns,
                    candidate.element_tags,
                )
                return T.Overload(
                    (*explicit, concrete_function_type),
                    applied.actual_returns,
                    call_site_body=arity,
                    runtime_static_values=(),
                )
            return None
        return overload.call_site_body(call_params)
    if overload.call_site_body is not None and analyser is not None:
        outer, node = overload.call_site_body
        analysis = analyser._analyse_function_at_call_site(
            outer,
            node,
            call_params,
            rank_values=rank_values,
            type_values=type_values,
            where_evaluated=where_evaluated,
            static_values=static_values,
        )
        if analysis is None:
            return None
        overloads = _functions._callable_overloads(analysis.typ)
        if len(overloads) != 1:
            return None
        return _substitute_overload_ranks(overloads[0], rank_values or {})
    if len(call_params) < len(overload.params):
        return None
    explicit = call_params[-len(overload.params) :] if overload.params else ()
    if not _call_site_explicit_args_match(overload.params, explicit, ctx):
        return None
    return T.Overload(
        params=call_params,
        returns=overload.returns,
        generic_constraints=overload.generic_constraints,
        where_clause=overload.where_clause,
        param_names=(None,) * (len(call_params) - len(overload.params))
        + overload.param_names,
        element_tags=overload.element_tags,
        annotation_error=overload.annotation_error,
        annotation_warning=overload.annotation_warning,
        param_defaults=(None,) * len(call_params),
    )

def _call_site_consumed_count(
    overload: T.Overload,
    concrete: T.Overload,
    extra_count: int,
) -> int | None:
    """Compute call site consumed count during static analysis."""
    consumed = (
        concrete.call_site_body
        if isinstance(concrete.call_site_body, int)
        else len(concrete.params) - len(overload.params)
    )
    if consumed < 0:
        return None
    return consumed

def _propagated_element_tags(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    substitution: dict[str, T.Type] | None = None,
) -> frozenset[T.ElementTag]:
    """Compute propagated element tags during static analysis."""
    tags = {
        tag
        for tag in _substitute_branch_element_tags(
            overload.element_tags,
            substitution or {},
        )
        if not tag.absent
    }
    for arg in args:
        arg = T.normalize(arg)
        if isinstance(arg, T.FunctionType):
            tags.update(tag for tag in arg.element_tags if not tag.absent)
        elif isinstance(arg, T.OverloadSetType):
            for candidate in arg.overloads:
                tags.update(tag for tag in candidate.element_tags if not tag.absent)
    return frozenset(tags)

def _initial_rank_value_candidates(
    params: tuple[T.Type, ...],
    args: tuple[T.Type, ...],
) -> tuple[dict[str, int], ...]:
    """Collect every consistent rank binding that can fit the arguments."""
    candidates: tuple[dict[str, int], ...] = ({},)
    for param, arg in zip(params, args, strict=False):
        expanded = (
            candidate
            for values in candidates
            for candidate in _rank_value_candidates(param, arg, values)
        )
        candidates = _deduplicate_rank_candidates(expanded)
        if not candidates:
            return ()
    return candidates

def _rank_value_candidates(
    pattern: T.Type,
    actual: T.Type,
    values: dict[str, int],
) -> tuple[dict[str, int], ...]:
    """Collect consistent rank bindings across one nested type pair."""
    pattern = T.normalize(pattern)
    actual = T.normalize(actual)
    if isinstance(pattern, T.CollectionType) and isinstance(actual, T.CollectionType):
        candidate = dict(values)
        if isinstance(pattern.rank, T.RankVariable) and isinstance(actual.rank, int):
            previous = candidate.get(pattern.rank.name)
            if previous is not None and previous != actual.rank:
                return ()
            candidate[pattern.rank.name] = actual.rank
        return _rank_value_candidates(pattern.base, actual.base, candidate)
    if (
        isinstance(pattern, T.NominalType)
        and isinstance(actual, T.NominalType)
        and pattern.name == actual.name
        and len(pattern.args) == len(actual.args)
    ):
        return _rank_candidates_for_pairs(
            zip(pattern.args, actual.args, strict=True), values
        )
    if isinstance(pattern, T.RowType) and isinstance(actual, T.RowType):
        candidates = _rank_value_candidates(pattern.base, actual.base, values)
        actual_fields = {field.name: field.typ for field in actual.fields}
        pairs = tuple(
            (field.typ, actual_fields[field.name])
            for field in pattern.fields
            if field.name in actual_fields
        )
        return _extend_rank_candidates(candidates, pairs)
    if (
        isinstance(pattern, T.FunctionType)
        and isinstance(actual, T.FunctionType)
        and pattern.params is not None
        and pattern.returns is not None
        and actual.params is not None
        and actual.returns is not None
        and len(pattern.params) == len(actual.params)
        and len(pattern.returns) == len(actual.returns)
    ):
        pairs: list[tuple[T.Type, T.Type]] = list(
            zip(
                pattern.params + pattern.returns,
                actual.params + actual.returns,
                strict=True,
            )
        )
        actual_tags = {tag.name: tag for tag in actual.element_tags}
        for tag in pattern.element_tags:
            actual_tag = actual_tags.get(tag.name)
            if actual_tag is None or len(tag.args) != len(actual_tag.args):
                continue
            pairs.extend(zip(tag.args, actual_tag.args, strict=True))
        return _rank_candidates_for_pairs(pairs, values)
    if (
        isinstance(pattern, T.TupleType)
        and isinstance(actual, T.TupleType)
        and len(pattern.params) == len(actual.params)
    ):
        return _rank_candidates_for_pairs(
            zip(pattern.params, actual.params, strict=True), values
        )
    if isinstance(pattern, T.VariadicTupleType) and isinstance(actual, T.TupleType):
        return _variadic_tuple_rank_candidates(pattern, actual, values)
    if isinstance(pattern, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _rank_value_candidates(pattern.inner, actual, values)
    if isinstance(actual, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _rank_value_candidates(pattern, actual.inner, values)
    return (dict(values),)

def _rank_candidates_for_pairs(
    pairs: Iterable[tuple[T.Type, T.Type]],
    values: dict[str, int],
) -> tuple[dict[str, int], ...]:
    """Fold nested rank extraction over an iterable of type pairs."""
    candidates: tuple[dict[str, int], ...] = (dict(values),)
    for pattern, actual in pairs:
        candidates = _extend_rank_candidates(candidates, ((pattern, actual),))
        if not candidates:
            break
    return candidates

def _extend_rank_candidates(
    candidates: tuple[dict[str, int], ...],
    pairs: tuple[tuple[T.Type, T.Type], ...],
) -> tuple[dict[str, int], ...]:
    """Apply type pairs to every current rank-binding candidate."""
    current = candidates
    for pattern, actual in pairs:
        expanded = (
            candidate
            for values in current
            for candidate in _rank_value_candidates(pattern, actual, values)
        )
        current = _deduplicate_rank_candidates(expanded)
        if not current:
            return ()
    return current

def _variadic_tuple_rank_candidates(
    pattern: T.VariadicTupleType,
    actual: T.TupleType,
    values: dict[str, int],
) -> tuple[dict[str, int], ...]:
    """Backtrack over variadic tuple splits without leaking rank bindings."""
    cache: dict[
        tuple[int, int, tuple[tuple[str, int], ...]],
        tuple[tuple[tuple[str, int], ...], ...],
    ] = {}

    def rec(
        pattern_index: int,
        actual_index: int,
        bindings: tuple[tuple[str, int], ...],
    ) -> tuple[tuple[tuple[str, int], ...], ...]:
        """Return bounded distinct bindings from the current tuple indexes."""
        key = (pattern_index, actual_index, bindings)
        cached = cache.get(key)
        if cached is not None:
            return cached
        if pattern_index == len(pattern.items):
            result = (bindings,) if actual_index == len(actual.params) else ()
            cache[key] = result
            return result

        item = pattern.items[pattern_index]
        results: set[tuple[tuple[str, int], ...]] = set()
        if item.repeated:
            results.update(rec(pattern_index + 1, actual_index, bindings))
            consumed = (dict(bindings),)
            for index in range(actual_index, len(actual.params)):
                consumed = _extend_rank_candidates(
                    consumed, ((item.typ, actual.params[index]),)
                )
                if not consumed:
                    break
                for candidate in consumed:
                    results.update(
                        rec(
                            pattern_index + 1,
                            index + 1,
                            _rank_binding_key(candidate),
                        )
                    )
                if len(results) > _MAX_RANK_BINDING_CANDIDATES:
                    cache[key] = ()
                    return ()
        elif actual_index < len(actual.params):
            for candidate in _rank_value_candidates(
                item.typ, actual.params[actual_index], dict(bindings)
            ):
                results.update(
                    rec(
                        pattern_index + 1,
                        actual_index + 1,
                        _rank_binding_key(candidate),
                    )
                )
        result = _deduplicate_rank_binding_keys(sorted(results))
        cache[key] = result
        return result

    return tuple(dict(bindings) for bindings in rec(0, 0, _rank_binding_key(values)))

def _rank_binding_key(values: dict[str, int]) -> tuple[tuple[str, int], ...]:
    """Return a stable immutable key for one rank-binding candidate."""
    return tuple(sorted(values.items()))

def _deduplicate_rank_binding_keys(
    candidates: Iterable[tuple[tuple[str, int], ...]],
) -> tuple[tuple[tuple[str, int], ...], ...]:
    """Bound and deduplicate immutable rank-binding candidates."""
    unique: list[tuple[tuple[str, int], ...]] = []
    seen: set[tuple[tuple[str, int], ...]] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        unique.append(candidate)
        if len(unique) > _MAX_RANK_BINDING_CANDIDATES:
            return ()
    return tuple(unique)

def _deduplicate_rank_candidates(
    candidates: Iterable[dict[str, int]],
) -> tuple[dict[str, int], ...]:
    """Bound and deduplicate mutable rank-binding candidates."""
    keys = _deduplicate_rank_binding_keys(
        _rank_binding_key(candidate) for candidate in candidates
    )
    return tuple(dict(key) for key in keys)

def _match_variadic_tuple_types(
    pattern: T.VariadicTupleType,
    actual: T.TupleType,
    match: Callable[[T.Type, T.Type], bool],
) -> bool:
    """Return whether variadic tuple types match."""

    def rec(pattern_index: int, actual_index: int) -> bool:
        """Recursively continue the match variadic tuple types algorithm."""
        if pattern_index == len(pattern.items):
            return actual_index == len(actual.params)
        item = pattern.items[pattern_index]
        if item.repeated:
            if rec(pattern_index + 1, actual_index):
                return True
            for index in range(actual_index, len(actual.params)):
                if not match(item.typ, actual.params[index]):
                    return False
                if rec(pattern_index + 1, index + 1):
                    return True
            return False
        return (
            actual_index < len(actual.params)
            and match(
                item.typ,
                actual.params[actual_index],
            )
            and rec(pattern_index + 1, actual_index + 1)
        )

    return rec(0, 0)

def _evaluate_where_clause(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    rank_values: dict[str, int],
    type_substitution: dict[str, T.Type] | None = None,
) -> static_where.WhereEvaluation | None:
    """Evaluate one validated ``where`` clause for an overload candidate."""
    return static_where.evaluate_where_clause(
        params=overload.params,
        returns=overload.returns,
        param_names=overload.param_names,
        clause=overload.where_clause,
        args=args,
        initial_ranks=rank_values,
        type_substitution=type_substitution,
    )

def _substitute_overload_ranks(
    overload: T.Overload,
    ranks: dict[str, int],
) -> T.Overload:
    """Substitute overload ranks during static analysis."""
    return _functions._transform_overload_types(
        overload,
        lambda typ: _substitute_rank_values(typ, ranks),
        element_tags=frozenset(
            _substitute_rank_values_in_element_tags(overload.element_tags, ranks)
        ),
    )

def _substitute_rank_values(typ: T.Type, ranks: dict[str, int]) -> T.Type:
    """Substitute rank values recursively during static analysis."""
    return static_where.substitute_rank_variables(typ, ranks)

def _substitute_rank_values_in_element_tags(
    tags: frozenset[T.ElementTag],
    ranks: dict[str, int],
) -> tuple[T.ElementTag, ...]:
    """Substitute rank values in element tags during static analysis."""
    return tuple(
        T.ElementTag(
            tag.name,
            tuple(_substitute_rank_values(arg, ranks) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )

def _row_views_for_arguments(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    env: T.Environment | None,
) -> tuple[T.Type, ...]:
    """Determine the arguments for row views for during static analysis."""
    if env is None:
        return args
    return tuple(
        _row_view_for_argument(arg, param, env)
        for arg, param in zip(args, params, strict=True)
    )

def _row_view_for_argument(
    arg: T.Type,
    param: T.Type,
    env: T.Environment,
) -> T.Type:
    """Compute row view for argument during static analysis."""
    arg = T.normalize(arg)
    param = T.normalize(param)
    if not isinstance(param, T.RowType):
        return arg
    if isinstance(arg, T.RowType):
        return arg
    if not isinstance(arg, T.NominalType):
        return arg
    definition = env.lookup_object(arg.name)
    if definition is None:
        return arg
    return T.Row(
        arg,
        *(
            T.Field(attribute.name, attribute.typ)
            for attribute in definition.attributes
            if attribute.access.text in {"public", "readable"}
        ),
    )

def _specialize_branch_arguments(
    branch: _core.AnalysisBranch,
    substitution: dict[str, T.Type],
) -> _core.AnalysisBranch:
    """Specialize branch arguments during static analysis."""
    for name, typ in substitution.items():
        if _functions._contains_named_type_var(typ, name):
            continue
        branch = branch.refine_type(T.V(name), typ)
    return branch

def _apply_data_tag_flow(
    args: tuple[T.Type, ...],
    declared_returns: tuple[T.Type, ...],
    actual_returns: tuple[T.Type, ...],
    ctx: T.Context,
    *,
    overlay_tag: str | None = None,
) -> tuple[T.Type, ...]:
    """Apply computed and constructed data-tag flow for one chosen signature.

    Computed tags survive only when the return contract names them. Constructed
    and unit tags are sticky: every guaranteed constructed-like input tag is
    projected onto each output whose rank is high enough. An explicit absent
    fact, an exact tag-set return, or omission from the owning tag overlay is an
    intentional removal and suppresses that automatic projection.
    """
    constructed_sources = _constructed_tag_source_ranks(args, ctx)
    outputs: list[T.Type] = []
    for index, ret in enumerate(actual_returns):
        declared = declared_returns[index] if index < len(declared_returns) else ret
        explicit = _explicit_tags(declared)
        flowed = _strip_implicit_computed_tags(ret, explicit, ctx)
        output_rank = _type_rank(flowed)
        additions = tuple(
            T.DataTag(name, max(output_rank - 1, 0))
            for name, source_rank in constructed_sources.items()
            if output_rank >= source_rank
            and not _constructed_flow_is_suppressed(
                declared,
                name,
                overlay_tag=overlay_tag,
            )
        )
        if additions:
            flowed = _with_data_tags(flowed, additions, ctx)
            # An explicit return contract wins over implicit flow when tags are
            # disjoint. This also keeps an explicitly selected depth canonical.
            flowed = _with_data_tags(
                flowed,
                (tag for tag in explicit if not tag.absent),
                ctx,
            )
        outputs.append(flowed)
    return tuple(outputs)

def _constructed_tag_source_ranks(
    args: tuple[T.Type, ...],
    ctx: T.Context,
) -> dict[str, int]:
    """Return the lowest guaranteed source rank for each sticky input tag."""
    result: dict[str, int] = {}
    for arg in args:
        for name, rank in _guaranteed_constructed_tag_sources(arg, ctx).items():
            current = result.get(name)
            if current is None or rank < current:
                result[name] = rank
    return result

def _guaranteed_constructed_tag_sources(
    typ: T.Type,
    ctx: T.Context,
) -> dict[str, int]:
    """Find constructed-like tags guaranteed to occur within one value type."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        rank = _type_rank(typ.inner)
        result = _guaranteed_constructed_tag_sources(typ.inner, ctx)
        for tag in sorted(typ.tags):
            if tag.absent or not ctx.is_constructed_like_tag(tag.name):
                continue
            source_rank = max(rank - tag.depth, 0)
            current = result.get(tag.name)
            if current is None or source_rank < current:
                result[tag.name] = source_rank
        return result
    if isinstance(typ, T.CollectionType):
        return _guaranteed_constructed_tag_sources(typ.base, ctx)
    if isinstance(typ, T.UnionType):
        branches = [
            _guaranteed_constructed_tag_sources(item, ctx) for item in typ.items
        ]
        if not branches:
            return {}
        common = set(branches[0]).intersection(*(set(item) for item in branches[1:]))
        return {
            name: max(branch[name] for branch in branches) for name in sorted(common)
        }
    if isinstance(typ, T.IntersectionType):
        result: dict[str, int] = {}
        for item in typ.items:
            for name, rank in _guaranteed_constructed_tag_sources(item, ctx).items():
                current = result.get(name)
                if current is None or rank < current:
                    result[name] = rank
        return result
    if isinstance(typ, (T.ExactType, T.AtomicType)):
        return _guaranteed_constructed_tag_sources(typ.inner, ctx)
    return {}

def _constructed_flow_is_suppressed(
    declared: T.Type,
    name: str,
    *,
    overlay_tag: str | None,
) -> bool:
    """Return whether a return contract intentionally removes a sticky tag."""
    explicit = _explicit_tags(declared)
    if any(tag.name == name and tag.absent for tag in explicit):
        return True
    positive = any(tag.name == name and not tag.absent for tag in explicit)
    if overlay_tag == name and not positive:
        return True
    return _has_exact_tag_contract(declared) and not positive

def _has_exact_tag_contract(typ: T.Type) -> bool:
    """Return whether a type contains an exact present-tag set at its root."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return typ.exact
    if isinstance(typ, (T.ExactType, T.AtomicType)):
        return _has_exact_tag_contract(typ.inner)
    return False

def _explicit_tags(typ: T.Type) -> frozenset[T.DataTag]:
    """Compute explicit tags during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return typ.tags | _explicit_tags(typ.inner)
    if isinstance(typ, T.CollectionType):
        return _explicit_tags(typ.base)
    if isinstance(typ, T.UnionType):
        result: set[T.DataTag] = set()
        for item in typ.items:
            result.update(_explicit_tags(item))
        return frozenset(result)
    return frozenset()

def _strip_implicit_computed_tags(
    typ: T.Type,
    explicit_tags: frozenset[T.DataTag],
    ctx: T.Context,
) -> T.Type:
    """Compute strip implicit computed tags during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        kept = tuple(tag for tag in typ.tags if tag in explicit_tags)
        inner = _strip_implicit_computed_tags(typ.inner, explicit_tags, ctx)
        if typ.exact:
            return T.Tagged(inner, *kept, exact=True)
        return _with_data_tags(inner, kept, ctx) if kept else inner
    if isinstance(typ, T.CollectionType):
        return T.C(
            type(typ),
            _strip_implicit_computed_tags(typ.base, explicit_tags, ctx),
            typ.rank,
        )
    if isinstance(typ, T.UnionType):
        return T.U(
            *(
                _strip_implicit_computed_tags(item, explicit_tags, ctx)
                for item in typ.items
            )
        )
    return typ

def _with_data_tags(
    typ: T.Type,
    tags: Iterable[T.DataTag],
    ctx: T.Context,
) -> T.Type:
    """Compute with data tags during static analysis."""
    existing: set[T.DataTag] = set()
    exact = False
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        existing.update(typ.tags)
        exact = typ.exact
        typ = typ.inner
    for tag in tags:
        existing = {
            item
            for item in existing
            if Symbol(item.name) not in ctx.tag_disjoints(tag.name)
        }
        existing.add(tag)
        parent = ctx.tag_parent(tag.name)
        if parent is not None:
            existing.add(T.DataTag(parent.text, tag.depth))
    return T.Tagged(typ, *sorted(existing), exact=exact) if existing or exact else typ

def _remove_data_tag(typ: T.Type, tag: T.DataTag) -> T.Type | None:
    """Remove data tag during static analysis."""
    typ = T.normalize(typ)
    if not isinstance(typ, T.TaggedType):
        return None
    existing = set(typ.tags)
    positive = T.DataTag(tag.name, tag.depth)
    if positive not in existing:
        return None
    existing.remove(positive)
    return (
        T.Tagged(typ.inner, *sorted(existing), exact=typ.exact)
        if existing or typ.exact
        else typ.inner
    )

def _show_tag(tag: T.DataTag) -> str:
    """Format tag during static analysis."""
    prefix = "#-" if tag.absent else "#"
    depth = "+" * tag.depth
    return f"{prefix}{tag.name}{depth}"

def _validator_overload_ok(overload: T.Overload, ctx: T.Context) -> bool:
    """Return the Boolean result of validator overload ok during static analysis."""
    return len(overload.returns) == 1 and T.assignable(
        overload.returns[0],
        T.WithTag(T.Number, "boolean"),
        ctx,
    )

def _static_validator_result(body: tuple[TypedNode, ...]) -> bool | None:
    """Compute the result for static validator during static analysis."""
    if len(body) != 1:
        return None
    node = body[0].node
    if isinstance(node, ElementNode):
        if node.name == Symbol("true"):
            return True
        if node.name == Symbol("false"):
            return False
    return None

def _disjoint_data_tags(
    typ: T.Type,
    ctx: T.Context,
) -> tuple[Symbol, Symbol] | None:
    """Compute disjoint data tags during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        positive = [Symbol(tag.name) for tag in typ.tags if not tag.absent]
        seen: set[Symbol] = set()
        for tag in positive:
            conflict = next(
                (name for name in seen if name in ctx.tag_disjoints(tag)),
                None,
            )
            if conflict is not None:
                return conflict, tag
            seen.add(tag)
        return _disjoint_data_tags(typ.inner, ctx)
    if isinstance(typ, T.CollectionType):
        return _disjoint_data_tags(typ.base, ctx)
    if isinstance(typ, T.UnionType):
        for item in typ.items:
            conflict = _disjoint_data_tags(item, ctx)
            if conflict is not None:
                return conflict
    if isinstance(typ, T.FunctionType):
        for item in (*(typ.params or ()), *(typ.returns or ())):
            conflict = _disjoint_data_tags(item, ctx)
            if conflict is not None:
                return conflict
    return None

def _type_rank(typ: T.Type) -> int:
    """Determine the collection rank for type during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _type_rank(typ.inner)
    if isinstance(typ, T.CollectionType):
        return typ.rank
    return 0

def _refine_branch_like(
    branch: _core.AnalysisBranch,
    refined: _core.AnalysisBranch,
) -> _core.AnalysisBranch:
    """Refine branch like during static analysis."""
    substitution = _branch_pair_substitution(branch.inputs, refined.inputs)
    if substitution is None:
        return replace(
            branch,
            element_tags=refined.element_tags,
            data_element_uses=refined.data_element_uses,
        )
    return replace(
        _specialize_branch_arguments(branch, substitution),
        element_tags=refined.element_tags,
        data_element_uses=refined.data_element_uses,
    )

def _branch_pair_substitution(
    source: tuple[T.Type, ...],
    target: tuple[T.Type, ...],
) -> dict[str, T.Type] | None:
    """Compute branch pair substitution during static analysis."""
    if len(source) != len(target):
        return None
    substitution: dict[str, T.Type] = {}
    for left, right in zip(source, target, strict=True):
        constraints = _solve_branch_argument(left, right, T.Context())
        if constraints is None:
            return None
        for name, typ in constraints.items():
            existing = substitution.get(name)
            if existing is not None and not T.same(existing, typ):
                return None
            substitution[name] = typ
    return substitution

def _branch_argument_substitution(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    ctx: T.Context,
) -> dict[str, T.Type] | None:
    """Compute branch argument substitution during static analysis."""
    substitution: dict[str, T.Type] = {}
    for arg, param in zip(args, params, strict=True):
        arg = _substitute_branch_type(arg, substitution)
        param = _substitute_branch_type(param, substitution)
        constraints = _solve_branch_argument(arg, param, ctx)
        if constraints is None or (
            not constraints and _functions._contains_type_var(param)
        ):
            constraints = _solve_type_argument(arg, param, ctx)
        if constraints is None:
            if T.compatible(arg, param, ctx):
                continue
            return None
        for name, typ in constraints.items():
            existing = substitution.get(name)
            if existing is not None and not T.same(existing, typ):
                return None
            substitution[name] = typ
    return substitution

def _static_type_substitution(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    ctx: T.Context,
) -> dict[str, T.Type] | None:
    """Infer static generic values with collection variables as element types."""
    substitution = _branch_argument_substitution(args, params, ctx)
    if substitution is None:
        return None
    direct: dict[str, T.Type] = {}
    for arg, param in zip(args, params, strict=True):
        if not _collect_static_element_bindings(param, arg, direct):
            return None
    substitution.update(direct)
    specialized = tuple(
        _substitute_branch_type(param, substitution) for param in params
    )
    if not all(
        _functions._call_site_placeholder_accepts(param, arg, ctx)
        for param, arg in zip(specialized, args, strict=True)
    ):
        return None
    return substitution

def _collect_static_element_bindings(
    pattern: T.Type,
    actual: T.Type,
    bindings: dict[str, T.Type],
) -> bool:
    """Bind generic collection bases to compile-time element types."""
    pattern = T.normalize(pattern)
    actual = T.normalize(actual)
    if isinstance(pattern, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _collect_static_element_bindings(pattern.inner, actual, bindings)
    if isinstance(actual, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _collect_static_element_bindings(pattern, actual.inner, bindings)
    if isinstance(pattern, T.CollectionType) and isinstance(actual, T.CollectionType):
        if isinstance(pattern.base, T.VarType):
            previous = bindings.get(pattern.base.name)
            if previous is None:
                bindings[pattern.base.name] = actual.base
                return True
            return T.same(previous, actual.base)
        return _collect_static_element_bindings(pattern.base, actual.base, bindings)
    if (
        isinstance(pattern, T.NominalType)
        and isinstance(actual, T.NominalType)
        and pattern.name == actual.name
        and len(pattern.args) == len(actual.args)
    ):
        return all(
            _collect_static_element_bindings(left, right, bindings)
            for left, right in zip(pattern.args, actual.args, strict=True)
        )
    if (
        isinstance(pattern, T.TupleType)
        and isinstance(actual, T.TupleType)
        and len(pattern.params) == len(actual.params)
    ):
        return all(
            _collect_static_element_bindings(left, right, bindings)
            for left, right in zip(pattern.params, actual.params, strict=True)
        )
    if (
        isinstance(pattern, T.FunctionType)
        and isinstance(actual, T.FunctionType)
        and pattern.params is not None
        and pattern.returns is not None
        and actual.params is not None
        and actual.returns is not None
        and len(pattern.params) == len(actual.params)
        and len(pattern.returns) == len(actual.returns)
    ):
        return all(
            _collect_static_element_bindings(left, right, bindings)
            for left, right in zip(
                pattern.params + pattern.returns,
                actual.params + actual.returns,
                strict=True,
            )
        )
    return True

def _solve_type_argument(
    arg: T.Type,
    param: T.Type,
    ctx: T.Context | None = None,
) -> dict[str, T.Type] | None:
    """Compute solve type argument during static analysis."""
    solved = T._solve(param, arg, ctx)
    if solved is None:
        return None
    substitution: dict[str, T.Type] = {}
    for name, values in solved.items():
        combined = T._combine_all(values, ctx)
        if combined is None:
            return None
        substitution[name] = combined
    return substitution

def _solve_branch_argument(
    arg: T.Type,
    param: T.Type,
    ctx: T.Context,
) -> dict[str, T.Type] | None:
    """Compute solve branch argument during static analysis."""
    constraints: dict[str, T.Type] = {}

    def bind(name: str, typ: T.Type) -> bool:
        """Bind one inferred value during static analysis."""
        previous = constraints.get(name)
        if previous is None:
            constraints[name] = typ
            return True
        return T.same(previous, typ)

    def rec(actual: T.Type, expected: T.Type) -> bool:
        """Recursively continue the solve branch argument algorithm."""
        actual = T.normalize(actual)
        expected = T.normalize(expected)
        if isinstance(actual, T.VarType):
            if _functions._contains_named_type_var(expected, actual.name):
                return True
            return bind(actual.name, expected)
        if isinstance(expected, T.VarType):
            if _functions._contains_named_type_var(actual, expected.name):
                return True
            return bind(expected.name, actual)
        if (
            isinstance(actual, T.FunctionType)
            and isinstance(expected, T.FunctionType)
            and actual.params is not None
            and actual.returns is not None
            and expected.params is not None
            and expected.returns is not None
            and (
                _functions._contains_type_var(actual)
                or _functions._contains_type_var(expected)
            )
        ):
            return (
                len(actual.params) == len(expected.params)
                and len(actual.returns) == len(expected.returns)
                and all(
                    rec(left, right)
                    for left, right in zip(
                        actual.params + actual.returns,
                        expected.params + expected.returns,
                        strict=True,
                    )
                )
            )
        if T.compatible(actual, expected, ctx):
            return True
        if isinstance(actual, T.RowType):
            if isinstance(expected, T.RowType):
                if not rec(actual.base, expected.base):
                    return False
                expected_fields = {field.name: field.typ for field in expected.fields}
                for field in actual.fields:
                    expected_field = expected_fields.get(field.name)
                    if expected_field is None or not rec(field.typ, expected_field):
                        return False
                return True
            return rec(actual.base, expected)
        if isinstance(actual, T.NominalType) and isinstance(expected, T.NominalType):
            return (
                actual.name == expected.name
                and len(actual.args) == len(expected.args)
                and all(
                    rec(left, right)
                    for left, right in zip(actual.args, expected.args, strict=True)
                )
            )
        if isinstance(actual, T.CollectionType) and isinstance(
            expected,
            T.CollectionType,
        ):
            return (
                type(actual) is type(expected)
                and actual.rank == expected.rank
                and rec(actual.base, expected.base)
            )
        if isinstance(actual, T.CollectionType):
            return rec(actual.base, expected)
        if isinstance(actual, T.FunctionType) and isinstance(expected, T.FunctionType):
            return (
                len(actual.params) == len(expected.params)
                and len(actual.returns) == len(expected.returns)
                and all(
                    rec(left, right)
                    for left, right in zip(
                        actual.params + actual.returns,
                        expected.params + expected.returns,
                        strict=True,
                    )
                )
            )
        if isinstance(actual, T.TupleType) and isinstance(expected, T.TupleType):
            return len(actual.params) == len(expected.params) and all(
                rec(left, right)
                for left, right in zip(actual.params, expected.params, strict=True)
            )
        if isinstance(actual, T.TupleType) and isinstance(
            expected,
            T.VariadicTupleType,
        ):
            return _match_variadic_tuple_types(
                expected,
                actual,
                lambda left, right: rec(right, left),
            )
        return False

    return constraints if rec(arg, param) else None

def _substitute_branch_type(typ: T.Type, substitution: dict[str, T.Type]) -> T.Type:
    """Substitute branch type during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return substitution.get(typ.name, typ)
    if isinstance(typ, T.NominalType):
        return T.N(
            typ.name,
            *(_substitute_branch_type(arg, substitution) for arg in typ.args),
        )
    if isinstance(typ, T.UnionType):
        return T.U(*(_substitute_branch_type(item, substitution) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_substitute_branch_type(item, substitution) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(
            *(_substitute_branch_type(item, substitution) for item in typ.params)
        )
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(
                    _substitute_branch_type(item.typ, substitution),
                    item.repeated,
                )
                for item in typ.items
            )
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            _substitute_branch_type(typ.base, substitution),
            *(
                T.Field(
                    field.name,
                    _substitute_branch_type(field.typ, substitution),
                )
                for field in typ.fields
            ),
        )
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), _substitute_branch_type(typ.base, substitution), typ.rank)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return T.Fn(
                None,
                None,
                _substitute_branch_element_tags(typ.element_tags, substitution),
            )
        return T.Fn(
            (_substitute_branch_type(item, substitution) for item in typ.params),
            (_substitute_branch_type(item, substitution) for item in typ.returns),
            _substitute_branch_element_tags(typ.element_tags, substitution),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(
            _substitute_branch_type(typ.inner, substitution),
            *typ.tags,
            exact=typ.exact,
        )
    if isinstance(typ, T.ExactType):
        return T.Exact(_substitute_branch_type(typ.inner, substitution))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_substitute_branch_type(typ.inner, substitution))
    return typ

def _substitute_branch_element_tags(
    tags: frozenset[T.ElementTag],
    substitution: dict[str, T.Type],
) -> tuple[T.ElementTag, ...]:
    """Substitute branch element tags during static analysis."""
    return tuple(
        T.ElementTag(
            tag.name,
            tuple(_substitute_branch_type(arg, substitution) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )

def _dominates(
    left: tuple[T.Specificity, ...],
    right: tuple[T.Specificity, ...],
) -> bool:
    """Return the Boolean result of dominates during static analysis."""
    if len(left) != len(right):
        return False
    saw_strict = False
    for a, b in zip(left, right, strict=True):
        if a.value > b.value:
            return False
        if a.value < b.value:
            saw_strict = True
    return saw_strict

def _returns_result_type(returns: tuple[T.Type, ...]) -> T.Type | None:
    """Determine the type of returns result during static analysis."""
    if len(returns) == 1:
        return returns[0]
    return None

def _consistent_function_returns(
    function: TypedFunctionNode,
) -> tuple[T.Type, ...] | None:
    """Determine the return types for consistent function during static analysis."""
    returns: tuple[T.Type, ...] | None = None
    for overload in function.overloads:
        typ = overload.typ
        if not isinstance(typ, T.FunctionType) or typ.returns is None:
            return None
        current = tuple(typ.returns)
        if returns is None:
            returns = current
            continue
        if len(returns) != len(current) or not all(
            T.same(left, right) for left, right in zip(returns, current, strict=True)
        ):
            return None
    return returns

def _single_function_return(function: TypedFunctionNode) -> T.Type | None:
    """Compute single function return during static analysis."""
    returns = _consistent_function_returns(function)
    if returns is None or len(returns) != 1:
        return None
    return returns[0]
