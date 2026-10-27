"""Concrete AST node handlers registered with the analyser."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
from valiance.analysis.contracts.release_effects import released_types_effects
from valiance.analysis.lints import canonical_lint_code, finding
import valiance.vtypes as T
from valiance.asts import (
    AnnotationNode,
    AssertNode,
    AtNode,
    CastNode,
    ConcurrentNode,
    UnsafeNode,
    FieldsNode,
    TypedFieldsNode,
    TypedTypeMetadataNode,
    TypedUnsafeNode,
    DictLiteralNode,
    ElementTagDeclarationNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionParam,
    ImportNode,
    IndexAccessNode,
    IndexSetNode,
    ListLiteralNode,
    LinkNode,
    LinkTypeNode,
    LintSuppressionNode,
    NumberLiteralNode,
    MinimumRankNode,
    ObjectNode,
    PopNNode,
    RecordLiteralNode,
    ReturnNode,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TagDeclarationNode,
    TagOverlayNode,
    TupleLiteralNode,
    TypedAssertNode,
    TypedAtNode,
    TypedCallNode,
    TypedCastNode,
    TypedConcurrentNode,
    TypedForNode,
    TypedFunctionNode,
    TypedIfNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedUnfoldNode,
    TypedWhileNode,
    UnfoldNode,
)
from valiance.asts.nodes import (
    BreakNode,
    CallNode,
    FieldAccessNode,
    FieldSetNode,
    ForNode,
    GetVariableNode,
    IfNode,
    SetVariableNode,
    SetVariablesNode,
    WhileNode,
)
from valiance.modules_system.modules import (
    ModuleLoadError,
    import_environment_facts,
    import_objects,
    import_trait_implementations,
    import_owned_trait_implementations,
    import_behaviour_set_objects,
)
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.default_types import Boolean
from valiance.vtypes.relations import merge_stacks

from .. import analyser as _core
from ..calls import callable_values as _functions
from ..calls import candidates as _calls
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils


@_core.register(FileLintSuppressionNode)
def _file_lint_suppression(
    self: _core.Analyser,
    node: FileLintSuppressionNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Apply a file-scoped lint suppression without emitting runtime code."""
    resolved = tuple((code, canonical_lint_code(code)) for code in node.codes)
    unknown = tuple(code for code, canonical in resolved if canonical is None)
    for code in unknown:
        self._record_lint_finding(
            finding(
                "unknown-lint-code",
                f"unknown lint code '{code}' in @lintFileOff",
                node,
            )
        )
    known = tuple(canonical for _code, canonical in resolved if canonical is not None)
    if node.codes:
        if self.disabled_lint_codes is not None:
            self.disabled_lint_codes.update(known)
        self.file_lint_suppressions.update({code: node for code in known})
    else:
        self.disabled_lint_codes = None
    return _core.BranchSet((branch,))


@_core.register(LintSuppressionNode)
def _lint_suppression(
    self: _core.Analyser,
    node: LintSuppressionNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse one statement and discard its selected lint findings."""
    resolved = tuple((code, canonical_lint_code(code)) for code in node.codes)
    unknown = tuple(code for code, canonical in resolved if canonical is None)
    for code in unknown:
        self._record_lint_finding(
            finding("unknown-lint-code", f"unknown lint code '{code}' in @lintOff", node)
        )
    finding_count = len(self.lint_findings)
    outputs = self.analyse_from(branch, node.body)
    new_findings = self.lint_findings[finding_count:]
    produced = {item.code for item in new_findings}
    suppressed = {canonical for _code, canonical in resolved if canonical is not None}
    kept = (
        ()
        if not node.codes
        else tuple(item for item in new_findings if item.code not in suppressed)
    )
    del self.lint_findings[finding_count:]
    del self.lints[finding_count:]
    self._extend_lint_findings(kept)
    for code in sorted(suppressed - produced):
        self._record_lint_finding(
            finding(
                "unused-lint-suppression",
                f"lint suppression for '{code}' is unused",
                node,
            )
        )
    return outputs


@_core.register(NumberLiteralNode)
def _number_literal(
    self: _core.Analyser,
    node: NumberLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `NumberLiteralNode` node and return the surviving branches."""
    typ = _utils._number_literal_type(node.value)
    return _core.BranchSet((branch.push(typ).emit(TypedNode(node, typ)),))


@_core.register(StringLiteralNode)
def _string_literal(
    self: _core.Analyser,
    node: StringLiteralNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `StringLiteralNode` node and return the surviving branches."""
    return _core.BranchSet((branch.push(T.String).emit(TypedNode(node, T.String)),))


















def _at_collection_view(typ: T.Type) -> T.CollectionType | None:
    """Build the view of at collection during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _at_collection_view(typ.inner)
    return typ if isinstance(typ, T.CollectionType) else None


def _at_level_type(source: T.Type, target_rank: int) -> T.Type | None:
    """Determine the type of at level during static analysis."""
    source = T.normalize(source)
    if isinstance(source, (T.TaggedType, T.NoVecType, T.ExactType)):
        return _at_level_type(source.inner, target_rank)
    if not isinstance(source, T.CollectionType):
        return source if target_rank == 0 else None
    if not isinstance(source.rank, int) or source.rank < target_rank:
        return None
    if target_rank == 0:
        return source.base
    collection_type: type[T.CollectionType]
    if isinstance(source, (T.ListExactType, T.ListMinType)):
        collection_type = T.ListExactType
    elif isinstance(source, T.ListRuggedType):
        collection_type = T.ListRuggedType
    elif isinstance(source, (T.ListExactType, T.ListMinType)):
        collection_type = T.ListExactType
    else:
        return None
    return T.C(collection_type, source.base, target_rank)


@_core.register(AtNode)
def _at_node(
    self: _core.Analyser,
    node: AtNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `AtNode` node and return the surviving branches."""
    arity = len(node.levels)
    source_hints = tuple(T.V(
        f"_at_{branch.origin}_{index}",
        T.TypeVarId(branch.origin, 10_000 + index),
    ) for index in range(arity))
    sourced = branch.source_arguments(source_hints)
    if sourced is None:
        self._diagnose(
            f"at requires {arity} value(s) on the stack",
            node,
        )
        return _core.BranchSet()

    source_types, popped = sourced
    target_types: list[T.Type] = []
    explicit_target_ranks: list[int | None] = []
    minimum_depths: list[int] = []
    for level, source_type in zip(node.levels, source_types, strict=True):
        target = _at_level_type(source_type, level.depth)
        if target is None:
            self._diagnose(
                f"at level '{level.name}' requires rank {level.depth}, "
                f"but received {T.show(source_type)}",
                node,
            )
            return _core.BranchSet()
        target_types.append(target)
        collection = _at_collection_view(source_type)
        if collection is None:
            explicit_target_ranks.append(None)
            minimum_depths.append(0)
        else:
            explicit_target_ranks.append(level.depth)
            minimum_depths.append(
                max(collection.rank - level.depth, 0)
                if isinstance(collection.rank, int)
                else 0
            )

    params = tuple(
        FunctionParam(
            None if level.name.text == "_" else level.name,
            target_type,
        )
        for level, target_type in zip(node.levels, target_types, strict=True)
    )
    function_node = FunctionNode(
        params=params,
        body=node.body,
        location=node.location,
    )
    analysed = self._analyse_function_literal(popped, function_node)
    if analysed is None:
        return _core.BranchSet()
    function, _ = analysed
    typed_function = TypedFunctionNode(
        function_node,
        function.typ,
        function.overloads,
    )

    candidates: list[tuple[str, T.AppliedOverload]] = []
    for index, overload_typing in enumerate(function.overloads):
        overload = overload_typing.overload
        if not isinstance(overload, T.Overload):
            continue
        applied = T.apply_overload(overload, source_types, self.env.context)
        if applied is None:
            continue
        applied = replace(
            applied,
            vectorised=any(depth > 0 for depth in minimum_depths),
            vectorised_depths=tuple(minimum_depths),
            vectorised_target_ranks=tuple(explicit_target_ranks),
        )
        candidates.append((overload.implementation_id, applied))

    if not candidates:
        self._diagnose("at body does not accept the selected level values", node)
        return _core.BranchSet()
    if len(candidates) > 1:
        self._diagnose("at body has ambiguous inferred overloads", node)
        return _core.BranchSet()

    implementation_id, applied = candidates[0]
    result = popped.with_stack(popped.stack.push(*applied.actual_returns))
    result = result.with_element_tags(applied.element_tags)
    return _core.BranchSet(
        (
            result.emit(
                TypedAtNode(
                    node,
                    _calls._returns_result_type(applied.actual_returns),
                    typed_function,
                    applied,
                    implementation_id,
                )
            ),
        )
    )


def _direct_concurrent_captures(
    node: ConcurrentNode,
    branch: _core.AnalysisBranch,
) -> tuple[GetVariableNode, ...]:
    """Find outer variable reads, excluding reads inside closure literals."""
    visible = {name for name, _typ in branch.variables.visible_items()}
    parameter_names = {
        param.name for param in node.params or () if param.name is not None
    }
    captures: list[GetVariableNode] = []

    def visit(value: object) -> None:
        """Visit one value or syntax node while avoiding recursive cycles."""
        if isinstance(value, FunctionNode):
            return
        if isinstance(value, GetVariableNode):
            if value.name in visible and value.name not in parameter_names:
                captures.append(value)
            return
        if isinstance(value, _core.ASTNode):
            from dataclasses import fields
            for item in fields(value):
                if item.name != "location":
                    visit(getattr(value, item.name))
        elif isinstance(value, (tuple, list)):
            for item in value:
                visit(item)

    visit(node.body)
    return tuple(captures)


@_core.register(ConcurrentNode)
def _concurrent_node(
    self: _core.Analyser,
    node: ConcurrentNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a concurrent block as a closed function-shaped scope."""
    captures = _direct_concurrent_captures(node, branch)
    if captures:
        for capture in captures:
            self._diagnose(
                f"concurrent block cannot capture outer variable '{capture.name}'; "
                "pass the value through the stack or parameter list",
                capture,
            )
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    function = FunctionNode(
        params=node.params,
        body=node.body,
        returns=node.returns,
        location=node.location,
    )
    blank_outer = _core.AnalysisBranch(
        input_mode=_core.InputMode.TOP_LEVEL,
        origin=branch.origin,
    )
    analysed = self._analyse_function_literal(
        blank_outer, function, allow_top_level_captures=False
    )
    if analysed is None:
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    details, _ = analysed
    candidates = tuple(
        item for item in details.overloads if isinstance(item.overload, T.Overload)
    )
    if not candidates:
        self._diagnose("concurrent block must have a concrete stack contract", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    arities = {len(item.overload.params) for item in candidates}
    if len(arities) != 1:
        self._diagnose("inferred concurrent inputs have inconsistent arity", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    preview_params = candidates[0].overload.params
    sourced = branch.source_arguments(preview_params)
    if sourced is None:
        self._diagnose("not enough stack values for concurrent block inputs", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    actual, remaining = sourced
    resolved = T.resolve_overload_result(
        tuple(item.overload for item in candidates), actual, self.env.context
    )
    if resolved is None:
        expected = candidates[0].overload.params
        self._diagnose(
            "concurrent block input type mismatch: expected "
            f"{', '.join(T.show(item) for item in expected)}, received "
            f"{', '.join(T.show(item) for item in actual)}",
            node,
        )
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    selected = next(
        item for item in candidates if item.overload == resolved.overload
    )
    params = resolved.params
    returns = resolved.returns
    typed = TypedConcurrentNode(
        node,
        T.Fn(params, returns, resolved.overload.element_tags),
        node.params,
        tuple(params),
        tuple(returns),
        selected.body,
    )
    return _core.BranchSet((remaining.push(*returns).emit(typed),))


@_core.register(UnsafeNode)
def _unsafe_node(
    self: _core.Analyser,
    node: UnsafeNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Authorize reflection lexically without changing effects or ownership."""
    outer_unsafe = self.reflection_unsafe
    self.reflection_unsafe = True
    try:
        outputs = self.analyse_from(replace(branch, typed_body=()), node.body)
    finally:
        self.reflection_unsafe = outer_unsafe
    return _core.BranchSet(tuple(
        replace(output, typed_body=branch.typed_body).emit(
            TypedUnsafeNode(node, output.top, output.typed_body)
        )
        for output in outputs
    ))


@_core.register(FieldsNode)
def _fields_node(
    self: _core.Analyser, node: FieldsNode, branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Select a concrete managed storage layout without observing owner values."""
    owner = node.owner
    consumes = owner is None
    if owner is None:
        sourced = branch.source_arguments((T.V("ReflectedOwner"),))
        if sourced is None:
            self._diagnose("fields requires an explicit type or a value", node)
            return _core.BranchSet()
        (owner,), branch = sourced
        branch = branch.with_element_tags(released_types_effects(self.env, (owner,)))
    else:
        owner = _functions._resolve_imported_type_names(owner, self.env)
        before = len(self.diagnostics)
        self.declarations._validate_function_type_names(
            FunctionNode(returns=(owner,)), node,
        )
        if len(self.diagnostics) != before:
            return _core.BranchSet()
    from valiance.vtypes.structural import nested_types
    for nested in nested_types(owner):
        if isinstance(nested, T.NominalType) and not isinstance(nested, T.FFINamedType):
            declaration = (self.env.lookup_object(nested.name)
                           or self.env.lookup_trait(nested.name)
                           or self.env.lookup_variant(nested.name))
            if declaration is not None and len(nested.args) != len(declaration.generics):
                self._diagnose(f"field enumeration requires complete generic arguments for {nested.name}", node)
                return _core.BranchSet()
    if node.operation != "fields":
        if _functions._contains_type_var(owner):
            if self.defer_reflection:
                raise _core.ReflectionSpecializationRequired(owner)
            self._diagnose("type metadata requires concrete type evidence", node)
            return _core.BranchSet()
        from valiance.vtypes.reflection import type_metadata
        typ, payload = type_metadata(owner, self.env, node.operation)
        return _core.BranchSet((branch.push(typ).emit(TypedTypeMetadataNode(node, typ, payload)),))
    from valiance.vtypes.reflection import value_fields_need_specialization
    if consumes and self.defer_reflection and value_fields_need_specialization(owner, self.env):
        raise _core.ReflectionSpecializationRequired(owner)
    if not self.require_reflection_permission(owner, node):
        return _core.BranchSet()
    if _functions._contains_type_var(owner):
        if self.defer_reflection:
            raise _core.ReflectionSpecializationRequired(owner)
        self._diagnose("field enumeration requires concrete owner evidence at a call site", node)
        return _core.BranchSet()
    from valiance.vtypes.reflection import fields_for_value
    metadata = fields_for_value(owner, self.env) if consumes else self.env.field_metadata(owner)
    if (consumes and value_fields_need_specialization(owner, self.env) and not metadata.alternatives
            and self.env.reflection_universe is not None and self.env.reflection_universe.collecting):
        metadata = self.env.field_metadata(T.Int)
    possibilities = (tuple(layout.owner.typ for layout in metadata.alternatives)
                     if metadata.alternatives else (metadata.owner.typ,))
    def storage_type(typ):
        """Consult a layout's nominal representation beneath occurrence wrappers."""
        while isinstance(typ, (T.TaggedType, T.NoVecType, T.ExactType)):
            typ = typ.inner
        return typ
    possibilities = tuple(storage_type(item) for item in possibilities)
    if consumes and any(
        isinstance(item, (T.IntersectionType, T.AnonymousTraitType))
        or isinstance(item, T.RowType) and not isinstance(item, T.RecordType)
        or isinstance(item, T.NominalType) and (
            item.name == Symbol("record")
            or
            self.env.lookup_trait(item.name) is not None
            or self.env.lookup_variant(item.name) is not None
        ) for item in possibilities
    ):
        self._diagnose("value field enumeration requires a finite set of checked owner layouts; "
                       "use RuntimeType and Dynamic for late-bound values", node)
        return _core.BranchSet()
    if metadata.alternatives:
        for layout in metadata.alternatives:
            invalid = _patterns._uncheckable_runtime_type(layout.owner.typ)
            if invalid is not None:
                self._diagnose(f"runtime field selection cannot discriminate {T.show(invalid)}", node)
                return _core.BranchSet()
        layouts = metadata.alternatives
        for index, left in enumerate(layouts):
            for right in layouts[index + 1:]:
                a, b = left.owner.typ, right.owner.typ
                if isinstance(a, T.RecordType) and isinstance(b, T.RecordType):
                    continue
                if (_utils._types_overlap(a, b, self.env.context)
                        and not T.assignable(a, b, self.env.context)
                        and not T.assignable(b, a, self.env.context)):
                    self._diagnose("runtime field selection requires distinguishable owner alternatives", node)
                    return _core.BranchSet()
        layouts = tuple(sorted(layouts, key=lambda layout: sum(
            T.assignable(other.owner.typ, layout.owner.typ, self.env.context)
            and not T.assignable(layout.owner.typ, other.owner.typ, self.env.context)
            for other in layouts
        )))
        metadata = replace(metadata, alternatives=layouts,
                           fields=tuple(field for layout in layouts for field in layout.fields))
    typ = (T.U(*(T.N(Symbol("Fields"), layout.owner.typ) for layout in metadata.alternatives))
           if metadata.alternatives else T.N(Symbol("Fields"), metadata.owner.typ))
    return _core.BranchSet((branch.push(typ).emit(
        TypedFieldsNode(node, typ, metadata, consumes)
    ),))


@_core.register(FunctionNode)
def _function_node(
    self: _core.Analyser,
    node: FunctionNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `FunctionNode` node and return the surviving branches."""
    if not self._validate_annotations(node.annotations, "fn", node):
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    if node.params is None and node.body and isinstance(node.body[0], MinimumRankNode):
        self._diagnose("empty stack when assuring minimum rank", node.body[0])
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    function_node = (
        node
        if node.overloads
        else _functions._genericize_function_node(node, node.generics)
    )
    self._validate_function_element_tags(function_node, node)
    result = self._analyse_overloaded_function_literal(branch, function_node, node)
    if result is None:
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    function, typed_branch = result
    typed_node = TypedFunctionNode(function_node, function.typ, function.overloads)
    return _core.BranchSet((typed_branch.push(function.typ).emit(typed_node),))


@_core.register(CastNode)
def _cast_node(self: _core.Analyser, node: CastNode, branch: _core.AnalysisBranch) -> _core.BranchSet:
    """Analyse a coercing, checked, or optional cast."""
    target = _functions._parameter_value_type(
        _functions._resolve_imported_type_names(T.normalize(node.typ), self.env)
    )
    self._validate_element_tags_in_types((target,), node)
    if not self._validate_data_tags(((target,),), node, allow_variants=False, require_declared=True):
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    if not branch.stack:
        self._diagnose(f"empty stack when casting to {T.show(target)}", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    source = branch.stack[-1]
    statically_safe = T.assignable(source, target, self.env.context)
    runtime_refinement = node.checked or node.optional
    insufficient_collection_rank = False
    normalized_source = T.normalize(source)
    normalized_target = T.normalize(target)
    source_options = (
        normalized_source.items
        if isinstance(normalized_source, T.UnionType)
        else (normalized_source,)
    )
    if node.checked and isinstance(normalized_target, T.CollectionType):
        for source_option in source_options:
            if (
                isinstance(source_option, T.CollectionType)
                and isinstance(source_option.rank, int)
                and isinstance(normalized_target.rank, int)
                and source_option.rank < normalized_target.rank
            ):
                required_item_shape = T.C(
                    type(normalized_target),
                    normalized_target.base,
                    normalized_target.rank - source_option.rank,
                )
                if not T.assignable(
                    source_option.base,
                    required_item_shape,
                    self.env.context,
                ):
                    insufficient_collection_rank = True
                    break
    if runtime_refinement and (not statically_safe or insufficient_collection_rank):
        invalid = _patterns._uncheckable_runtime_type(target)
        if invalid is not None:
            self._diagnose(f"{T.show(invalid)} cannot be checked at runtime", node)
            return _core.BranchSet()
        if insufficient_collection_rank or not _utils._types_overlap(
            source, target, self.env.context
        ):
            if node.checked and _functions._type_contains_rank_var(target):
                stack = T.TypeStack((*branch.stack.items[:-1], target))
                return _core.BranchSet((branch.with_stack(stack).emit(TypedNode(node, target)),))
            diagnostic_source = source
            collection_options = tuple(
                item for item in source_options if isinstance(item, T.CollectionType)
            )
            if insufficient_collection_rank and collection_options:
                minimum_rank = min(item.rank for item in collection_options)
                lifted_items = tuple(
                    item.base
                    if item.rank == minimum_rank
                    else T.C(type(item), item.base, item.rank - minimum_rank)
                    for item in collection_options
                )
                diagnostic_source = T.C(
                    type(collection_options[0]),
                    T.U(*lifted_items),
                    minimum_rank,
                )
            self._diagnose(
                f"cannot cast {T.show(diagnostic_source)} to {T.show(target)}", node
            )
            return _core.BranchSet()
    elif not runtime_refinement and not statically_safe:
        if (
            isinstance(source, T.NominalType)
            and isinstance(target, T.NominalType)
            and target.name.namespace
        ):
            provider = Symbol(target.name.namespace[-1])
            trait_name = Symbol(target.name.text)
            self._diagnose(
                f"behaviour set {provider}.{trait_name} does not provide "
                f"{source.name} as {trait_name}",
                node,
            )
        elif (
            isinstance(source, T.NominalType)
            and isinstance(target, T.NominalType)
            and not target.name.namespace
            and len(
                T.implementation_pattern_providers(
                    source,
                    target,
                    self.env.context,
                )
            ) > 1
        ):
            providers = tuple(
                sorted(
                    T.implementation_pattern_providers(
                        source,
                        target,
                        self.env.context,
                    ),
                    key=str,
                )
            )
            choices = ", ".join(
                f"`as[{provider}.{T.show(target)}]`" for provider in providers
            )
            self._diagnose(
                f"ambiguous implementation of {T.show(target)} for {T.show(source)}\n"
                "candidate behaviour sets:\n"
                + "\n".join(
                    f"  {provider}.{T.show(target)}" for provider in providers
                )
                + f"\nhelp: qualify the cast with one of {choices}",
                node,
            )
        else:
            self._diagnose(
                f"cannot safely cast {T.show(source)} to {T.show(target)}",
                node,
            )
        return _core.BranchSet()
    flowed = _calls._apply_data_tag_flow((source,), (target,), (target,), self.env.context)[0]
    result = T.optional(flowed) if node.optional else flowed
    if node.checked and statically_safe:
        node = replace(node, checked=False)
    witnesses = ()
    if statically_safe and isinstance(normalized_target, T.AnonymousTraitType):
        try:
            proof = T.checked_structural_witnesses(
                source, normalized_target, self.env.context,
            )
        except T.AmbiguousStructuralWitness as exc:
            self._diagnose(str(exc), node)
            return _core.BranchSet()
        if proof is not None:
            witnesses = proof
    stack = T.TypeStack((*branch.stack.items[:-1], result))
    return _core.BranchSet((branch.with_stack(stack).emit(
        TypedCastNode(node, result, witnesses),
    ),))


def _minimum_rank_type(typ: T.Type, rank: int) -> T.Type:
    """Return the type produced by ensuring ``typ`` has at least ``rank``."""
    typ = T.normalize(typ)
    if isinstance(typ, T.UnionType):
        return T.U(*(_minimum_rank_type(item, rank) for item in typ.items))
    if isinstance(typ, (T.ListExactType, T.ListMinType, T.ListRuggedType)):
        if isinstance(typ.rank, int) and typ.rank >= rank:
            return typ
        return T.C(type(typ), typ.base, rank)
    return T.ExactList(typ, rank)


@_core.register(MinimumRankNode)
def _minimum_rank_node(
    self: _core.Analyser,
    node: MinimumRankNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse minimum-rank assurance without inferring an absent input."""
    sourced = branch.source_arguments((T.V("_minimum_rank_input"),))
    if sourced is None:
        self._diagnose("empty stack when assuring minimum rank", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    (source_type,), sourced_branch = sourced
    result = _minimum_rank_type(source_type, node.rank)
    return _core.BranchSet(
        (sourced_branch.push(result).emit(TypedNode(node, result)),)
    )


@_core.register(PopNNode)
def _pop_n_node(
    self: _core.Analyser,
    node: PopNNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Pop a compile-time fixed count while preserving input inference."""
    if not isinstance(node.count, int):
        self._diagnose(
            f"pop_n count '${node.count}' is not compile-time known",
            node,
        )
        return _core.BranchSet()
    params = tuple(T.V(
        f"_pop_n_{index}",
        T.TypeVarId(branch.origin, 20_000 + index),
    ) for index in range(node.count))
    sourced = branch.source_arguments(params)
    if sourced is None:
        self._diagnose(
            f"stack underflow for pop_n({node.count}); "
            f"expected {node.count} value(s)",
            node,
        )
        return _core.BranchSet()
    args, popped = sourced
    popped = popped.with_element_tags(released_types_effects(self.env, args))
    return _core.BranchSet((popped.emit(TypedNode(node)),))


@_core.register(StackShuffleNode)
def _stack_shuffle_node(
    self: _core.Analyser,
    node: StackShuffleNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `StackShuffleNode` node and return the surviving branches."""
    params = tuple(T.V(
        f"_shuffle_{index}",
        T.TypeVarId(branch.origin, 30_000 + index),
    ) for index, _ in enumerate(node.prestack))
    sourced = branch.source_arguments(params)
    if sourced is None:
        self._diagnose(
            f"stack underflow for {node.mode}; expected "
            f"{len(node.prestack)} value(s)",
            node,
        )
        return _core.BranchSet()

    args, popped = sourced
    labelled = {
        label: typ
        for label, typ in zip(node.prestack, args, strict=True)
        if label is not None
    }
    stack_arg_start = len(node.prestack) - min(
        len(branch.stack),
        len(node.prestack),
    )
    duplications = _utils._stack_duplications(node, args, stack_arg_start)
    for duplication in duplications:
        error = _utils._stack_duplication_diagnostic(
            node.mode,
            duplication,
            self.env,
        )
        if error is not None:
            self._diagnose(error, node)
            return _core.BranchSet()

    post_types = tuple(labelled[label] for label in node.poststack)
    if node.mode == Symbol("copy"):
        stack = branch.stack.push(*post_types)
    else:
        kept = tuple(
            typ for label, typ in zip(node.prestack, args, strict=True) if label is None
        )
        stack = popped.stack.push(*kept, *post_types)

    return _core.BranchSet(
        (
            popped.with_stack(stack).emit(
                TypedNode(node, _calls._returns_result_type(post_types))
            ),
        )
    )










@_core.register(CallNode)
def _call_node(
    self: _core.Analyser,
    node: CallNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `CallNode` node and return the surviving branches."""
    if not branch.stack:
        self._diagnose("call requires a function on the stack", node)
        return _core.BranchSet()

    callable_type = T.normalize(branch.stack[-1])
    overloads = _functions._callable_overloads(callable_type)
    if not overloads:
        self._diagnose(
            f"cannot call non-function value of type {T.show(callable_type)}",
            node,
        )
        return _core.BranchSet()

    callable_popped = branch.pop()
    diagnostics_before = len(self.diagnostics)
    arg_branches = self.analyse_from(callable_popped, node.args)
    terminal, arg_branches = _utils._split_terminal_branches(arg_branches)
    if not arg_branches:
        if terminal:
            return terminal
        if len(self.diagnostics) > diagnostics_before:
            return _core.BranchSet()

    candidates: list[_core.CallCandidate] = []
    for arg_branch in arg_branches:
        for overload in overloads:
            sourced = arg_branch.source_arguments(overload.params)
            if sourced is None:
                continue
            args, popped = sourced
            candidate = _calls._apply_overload_to_branch(
                overload,
                args,
                popped,
                self.env.context,
                analyser=self,
            )
            if candidate is None:
                continue

            candidates.append(_core.CallCandidate(candidate.applied, candidate.branch))

    winners = self.select_call_winners(
        candidates=candidates,
        branch=callable_popped,
        node=node,
        no_match_message=lambda: (
            f"no overloads for call target {T.show(callable_type)} match stack "
            f"{_utils._show_stack(callable_popped.stack)}\n"
            f"{_utils._show_overload_list(None, overloads)}"
        ),
        ambiguous_message=lambda: (
            f"ambiguous call target {T.show(callable_type)} with stack "
            f"{_utils._show_stack(callable_popped.stack)}"
        ),
    )
    if winners is None:
        return terminal

    return _core.BranchSet.collect(
        (
            *terminal.branches,
            *(
                candidate.branch.push(*candidate.applied.actual_returns).emit(
                    TypedCallNode(
                        node,
                        _calls._returns_result_type(candidate.applied.actual_returns),
                        candidate.applied,
                    )
                )
                for candidate in winners
            ),
        )
    )




































def _show_import_path_for_diagnostic(path) -> str:
    """Render an import path with canonical resolution-root spelling."""
    prefix = "" if path.root is None else f"{path.root}."
    return prefix + ".".join(path.parts)



def _ffi_storage_type(typ: T.Type) -> tuple[str, bool] | None:
    """Return canonical element spelling and whether storage is a flat buffer."""
    typ = T.normalize(typ)
    if isinstance(typ, T.FFINamedType):
        return T.show(typ), False
    if (
        isinstance(typ, T.CollectionType)
        and typ.rank == 1
        and isinstance(T.normalize(typ.base), T.FFINamedType)
    ):
        return T.show(T.normalize(typ.base)), True
    return None

@_core.register(LinkTypeNode)
def _link_type_node(self: _core.Analyser, node: LinkTypeNode, branch: _core.AnalysisBranch) -> _core.BranchSet:
    """Register one plain declaration-order C struct layout."""
    if node.namespace not in self._ffi_libraries:
        self._diagnose(f"unknown FFI library namespace '{node.namespace}'", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    if not node.fields:
        name = f"&{node.name}"
        if name in self._ffi_handles or name in self._ffi_structs:
            self._diagnose(f"duplicate linked type '{name}'", node)
        else:
            self._ffi_handles.add(name)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    field_storage = tuple(_ffi_storage_type(field.typ) for field in node.fields)
    if any(item is None for item in field_storage):
        self._diagnose("linked struct fields must be scalar FFI types or flat FFI buffers", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    for field, storage in zip(node.fields, field_storage, strict=True):
        assert storage is not None
        _type_name, is_buffer = storage
        if is_buffer != (field.fixed_size is not None):
            self._diagnose(
                "embedded FFI buffer fields require `size => positive_integer`, and scalar fields cannot declare size",
                node,
            )
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    name = f"&{node.name}"
    if name in self._ffi_structs:
        self._diagnose(f"duplicate linked struct '{name}'", node)
    else:
        self._ffi_structs[name] = T.FFIStructSpec(
            name,
            tuple(
                T.FFIFieldSpec(
                    field.name.text,
                    storage[0],
                    field.fixed_size,
                )
                for field, storage in zip(node.fields, field_storage, strict=True)
                if storage is not None
            ),
        )
        self.env.define_overload(
            node.name,
            T.Overload(
                tuple(field.typ for field in node.fields),
                (T.FFI(node.name),),
                param_names=tuple(field.name for field in node.fields),
            ),
        )
    return _core.BranchSet((branch.emit(TypedNode(node, None)),))


@_core.register(LinkNode)
def _link_node(
    self: _core.Analyser,
    node: LinkNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Register one primitive native-call overload."""
    annotation_names = tuple(
        annotation.name.text
        for annotation in node.annotations
        if isinstance(annotation, AnnotationNode)
    )
    unknown = tuple(
        name for name in annotation_names if name not in {"destroy", "owned", "nullable"}
    )
    if unknown:
        self._diagnose(f"annotation '@{unknown[0]}' cannot be used on link", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    library = self._ffi_libraries.get(node.namespace)
    if library is None:
        self._diagnose(f"unknown FFI library namespace '{node.namespace}'", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    callback_specs: list[T.FFICallbackSpec] = []
    for index, item in enumerate(node.params):
        normalized = T.normalize(item)
        if isinstance(normalized, T.FunctionType):
            if len(normalized.returns) > 1 or normalized.element_tags:
                self._diagnose("FFI callbacks may return at most one value and cannot declare effects", node)
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            callback_types = (*normalized.params, *normalized.returns)
            def callback_abi(value: T.Type) -> str | None:
                """Return the canonical primitive C ABI spelling for a callback type."""
                storage = _ffi_storage_type(value)
                if storage is not None:
                    # A callback has no buffer length or ownership contract.
                    # Treat only scalar primitives as callback values.
                    if storage[1]:
                        return None
                    return storage[0]
                value = T.normalize(value)
                if isinstance(value, T.NominalType) and not isinstance(value, T.FFINamedType):
                    candidate = "&" + value.name.text
                    return candidate if candidate in {
                        "&char", "&short", "&int", "&long", "&longlong",
                        "&unsignedchar", "&unsignedshort", "&unsignedint",
                        "&unsignedlong", "&unsignedlonglong", "&float", "&double",
                    } else None
                return None
            callback_abi_types = tuple(callback_abi(value) for value in callback_types)
            if any(value is None for value in callback_abi_types):
                self._diagnose("FFI callback parameters and returns must be primitive FFI-compatible types", node)
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            split = len(normalized.params)
            callback_specs.append(T.FFICallbackSpec(
                index,
                tuple(value for value in callback_abi_types[:split] if value is not None),
                callback_abi_types[split] if normalized.returns else None,
            ))
        elif _ffi_storage_type(item) is None:
            self._diagnose("link parameters must be FFI storage types or FFI callbacks", node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    return_storage = tuple(_ffi_storage_type(item) for item in node.returns)
    if any(storage is None for storage in return_storage):
        self._diagnose("link returns must be scalar FFI types or flat FFI buffers", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    if len(node.returns) > 1:
        self._diagnose("a primitive link may return at most one value", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    if any(storage[1] for storage in return_storage if storage is not None) and not any(
        isinstance(annotation, AnnotationNode) and annotation.name.text == "owned"
        for annotation in node.annotations
    ):
        self._diagnose("native buffer returns require @owned with an explicit size", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    name = node.alias or Symbol(node.symbol.text, (node.namespace.text,))
    retained = tuple(node.returns)
    return_conversion: tuple[str, str] | None = None
    conversion_tags: frozenset[T.ElementTag] = frozenset()
    if node.converted_return is not None:
        if len(node.returns) != 1:
            self._diagnose("a linked-return conversion requires one native return", node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        raw_return = node.returns[0]
        matches = tuple(
            (index, overload)
            for index, overload in enumerate(self.env.overloads_for(Symbol("to")))
            if len(overload.params) == 1
            and len(overload.returns) == 1
            and T.same(overload.params[0], raw_return)
            and overload.conversion_target is not None
            and T.same(overload.conversion_target, node.converted_return)
            and T.same(overload.returns[0], node.converted_return)
        )
        if not matches:
            self._diagnose(
                "linked return has no declared @convert implementation from "
                f"{T.show(raw_return)} to {T.show(node.converted_return)}",
                node,
            )
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        if len(matches) > 1:
            self._diagnose("linked return conversion is ambiguous", node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        conversion_index, conversion_overload = matches[0]
        runtime_name = self.env.overload_runtime_name_for(
            Symbol("to"), conversion_overload
        ) or self.env.runtime_name_for(Symbol("to")) or Symbol("to")
        return_conversion = (str(runtime_name), conversion_overload.implementation_id)
        conversion_tags = conversion_overload.element_tags
        retained = (node.converted_return,)
    owned_return_free = None
    owned_return_count = None
    nullable_return = "nullable" in annotation_names
    owned_annotation = next(
        (
            annotation
            for annotation in node.annotations
            if isinstance(annotation, AnnotationNode) and annotation.name.text == "owned"
        ),
        None,
    )
    if owned_annotation is not None:
        if len(node.returns) != 1 or len(owned_annotation.args) != 1:
            self._diagnose("@owned requires one native free-symbol string and one return", node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        free_node = owned_annotation.args[0]
        if not isinstance(free_node, StringLiteralNode):
            self._diagnose("@owned free symbol must be a string literal", node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        owned_return_free = free_node.value
        result_name = T.show(node.returns[0])
        if result_name == "&CString":
            visible: T.Type = T.String
        elif result_name.endswith("+"):
            visible = node.returns[0]
            count_node = next(
                (value for key, value in owned_annotation.kwargs if key.text == "size"),
                None,
            )
            if not isinstance(count_node, NumberLiteralNode) or not count_node.value.isdecimal() or int(count_node.value) < 0:
                self._diagnose("owned buffer returns require `size = non_negative_integer`", node)
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            owned_return_count = int(count_node.value)
        else:
            self._diagnose("@owned currently supports &CString and flat primitive buffers", node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        if return_conversion is not None:
            # The VM copies owned flat buffers as FFI buffers before applying
            # the selected conversion. CString returns are already Strings,
            # and nullable returns cannot be passed unconditionally to it.
            if result_name == "&CString" or nullable_return:
                self._diagnose(
                    "@owned linked-return conversion requires a non-nullable flat FFI buffer",
                    node,
                )
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            retained = (node.converted_return,)
        else:
            retained = (T.optional(visible) if nullable_return else visible,)
    elif nullable_return:
        self._diagnose("@nullable currently requires an @owned return", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    destroy_params: tuple[int, ...] = ()
    if "destroy" in annotation_names:
        if node.returns or len(node.params) != 1:
            self._diagnose("@destroy link must take one handle and return nothing", node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        parameter_name = T.show(node.params[0])
        if parameter_name not in self._ffi_handles:
            self._diagnose("@destroy parameter must be a declared opaque handle", node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        destroy_params = (0,)
    spec = T.NativeLinkSpec(
        library,
        node.symbol.text,
        tuple(
            "&callback" if isinstance(T.normalize(item), T.FunctionType) else T.show(item)
            for item in node.params
        ),
        T.show(node.returns[0]) if node.returns else None,
        tuple(self._ffi_structs.values()),
        tuple(sorted(self._ffi_handles)),
        destroy_params,
        owned_return_free,
        owned_return_count,
        nullable_return,
        return_conversion,
        tuple(callback_specs),
    )
    overload = T.Overload(
        tuple(node.params),
        retained,
        native_link=spec,
        element_tags=frozenset(
            (T.ElementTag(Symbol("Unsafe")), *conversion_tags)
        ),
    )
    try:
        self.env.define_overload(name, overload)
    except ValueError as exc:
        self._diagnose(str(exc), node)
    return _core.BranchSet((branch.emit(TypedNode(node, None)),))


@_core.register(ImportNode)
def _import_node(
    self: _core.Analyser,
    node: ImportNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ImportNode` node and return the surviving branches."""
    for spec in node.specs:
        if spec.path.root == Symbol("ffi"):
            if spec.components:
                self._diagnose("FFI library imports cannot select components", node)
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
            namespace = spec.alias or Symbol("ffi")
            self._ffi_libraries[namespace] = spec.path.parts[0][4:]
            continue
        try:
            exports, resolved_spec, definitions = self._load_import_definitions(spec)
            objects = import_objects(exports, resolved_spec)
            behaviour_objects = import_behaviour_set_objects(exports, resolved_spec)
            trait_implementations = import_trait_implementations(exports, resolved_spec)
            trait_implementations += import_owned_trait_implementations(
                exports, resolved_spec
            )
            import_environment_facts(exports, resolved_spec, self.env)
        except ModuleLoadError as exc:
            namespace = (spec.alias.text,) if spec.alias else (spec.path.parts[-1],)
            prefix = "" if spec.path.root is None else f"{spec.path.root}."
            self._failed_imports[namespace] = prefix + ".".join(spec.path.parts)
            if exc.diagnostics:
                for diagnostic in exc.diagnostics:
                    self._diagnose(diagnostic)
            else:
                self._diagnose(str(exc), node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))

        namespace = (
            (resolved_spec.alias.text,)
            if resolved_spec.alias
            else (resolved_spec.path.parts[-1],)
        )
        self._failed_imports.pop(namespace, None)
        if not resolved_spec.components:
            namespace_name = resolved_spec.alias or Symbol(resolved_spec.path.parts[-1])
            source = _show_import_path_for_diagnostic(resolved_spec.path)
            previous = self._imported_namespace_sources.get(namespace_name)
            if previous is not None and previous != source:
                self._diagnose(
                    f"conflicting imported namespace '{namespace_name}': provided by "
                    f"`{previous}` and `{source}`\n"
                    "help: alias at least one imported module namespace",
                    node,
                )
                return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        conflict = self._validate_import_conflicts(resolved_spec, definitions, objects)
        if conflict is not None:
            self._diagnose(conflict, node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))
        # Native helpers used from imported stdlib object bodies are not part
        # of ordinary source-level imports. Install their real overloads before
        # those bodies are analysed; source wrappers can finish in `$self` and
        # therefore cannot be discovered from their final AST node alone.
        if len(resolved_spec.path.parts) == 2 and resolved_spec.path.parts[0] == 'std':
            from valiance.elements.stdlib_native import _native_functions, _runtime_element
            module_name = resolved_spec.path.parts[1]
            for native in _native_functions(module_name) or ():
                if native.owner is not None:
                    continue
                element = _runtime_element(module_name, native)
                if not self.env.overloads_for(element.name):
                    for overload in element.overloads:
                        self.env.define_overload(element.name, overload)
        # Imported native defaults retain references to private, fully
        # qualified implementation helpers. Resolve those in their declaring
        # module, not in the caller's lexical namespace.
        from valiance.elements.stdlib_native import install_native_default_dependencies
        install_native_default_dependencies(self.env, definitions)
        for typed_node in exports.runtime_prelude:
            self._prelude.add(typed_node)
        from valiance.modules_system.modules import import_constructor_dependencies
        from valiance.analysis.import_runtime import with_import_runtime_name
        constructor_dependencies = exports.runtime_prelude
        for dependency in import_constructor_dependencies(exports):
            runtime_name = self._prelude.add_declaration(dependency.typed, dependency.name)
            constructor_dependencies += (
                with_import_runtime_name(dependency.typed, runtime_name),
            )
        for obj in objects:
            object_node = obj.typed.node
            # Selected owned implementation evidence belongs to the imported
            # constructor as well as to the analyser's nominal Context. A
            # source module's implementation block need not be exported as an
            # executable ObjectNode, so rebuilding its constructor otherwise
            # drops subtype information used by runtime structural linking.
            imported_supertypes = ()
            imported_guarded_supertypes = ()
            if (
                isinstance(object_node, ObjectNode)
                and object_node.kind.text == "object"
                and object_node.target is None
            ):
                # Preserve unconditional generic projections as well as
                # non-generic nominal edges. The implementation must cover
                # the entire constructor, not merely some instantiations.
                constructor_pattern = T.N(
                    obj.name,
                    *(T.V(generic.text) for generic in object_node.generics),
                )
                imported_supertypes = tuple(
                    implementation.trait_pattern
                    for implementation in trait_implementations
                    if implementation.object_name == obj.name
                    and implementation.subject_kind.text == "object"
                    and implementation.object_pattern == constructor_pattern
                    and implementation.trait_pattern is not None
                    and implementation.generics == object_node.generics
                    and not any(implementation.generic_constraints)
                )
                imported_guarded_supertypes = tuple(
                    (implementation.trait_pattern, implementation.generic_constraints)
                    for implementation in trait_implementations
                    if implementation.object_name == obj.name
                    and implementation.subject_kind.text == "object"
                    and implementation.object_pattern == constructor_pattern
                    and implementation.trait_pattern is not None
                    and implementation.generics == object_node.generics
                    and any(implementation.generic_constraints)
                )
            runtime_name = self._prelude.add_declaration(
                obj.typed, obj.name,
                imported_supertypes=imported_supertypes,
                imported_guarded_supertypes=imported_guarded_supertypes,
            )
            self._register_imported_object(obj, runtime_name, constructor_dependencies)
            module_source = _show_import_path_for_diagnostic(resolved_spec.path)
            for definition in obj.private_friendly_definitions:
                candidate = (obj.name, module_source)
                candidates = self._private_imported_friendly_elements.setdefault(
                    definition.name, []
                )
                if candidate not in candidates:
                    candidates.append(candidate)
        for obj in behaviour_objects:
            runtime_name = self._prelude.add_declaration(obj.typed, obj.name)
            self._register_imported_object(obj, runtime_name)
        definitions_by_name: dict[Symbol, list] = {}
        call_site_dependencies = ()
        if any(overload.overload.call_site_body is not None
               for definition in definitions for overload in definition.typed.overloads):
            dependencies = list(exports.runtime_prelude)
            for declaration in (*exports.objects, *exports.definitions):
                runtime_name = self._prelude.add_declaration(declaration.typed, declaration.name)
                dependencies.append(with_import_runtime_name(declaration.typed, runtime_name))
            call_site_dependencies = tuple(dependencies)
        for definition in definitions:
            definitions_by_name.setdefault(definition.name, []).append(definition)
        for definition_name, definition_group in definitions_by_name.items():
            first_typed = definition_group[0].typed
            merged_typed = TypedFunctionNode(
                first_typed.node,
                first_typed.typ,
                tuple(
                    overload
                    for definition in definition_group
                    for overload in definition.typed.overloads
                ),
                first_typed.dispatch_plan,
            )
            runtime_name = self._prelude.add_declaration(
                merged_typed,
                definition_name,
            )
            for definition in definition_group:
                try:
                    self._register_imported_definition(
                        definition.name,
                        definition.typed,
                        runtime_name,
                        call_site_dependencies,
                    )
                except ValueError as exc:
                    self._diagnose(
                        self._import_arity_diagnostic(
                            str(exc),
                            resolved_spec,
                            definition.name,
                        ),
                        node,
                    )
                    return _core.BranchSet(
                        (branch.emit(TypedNode(node, None)),)
                    )
        provider = Symbol(exports.module_name.rsplit(".", 1)[-1])
        for implementation in trait_implementations:
            self.env.add_trait_impl(
                implementation.object_name,
                implementation.trait_name,
                provider=provider,
                object_pattern=implementation.object_pattern,
                trait_pattern=implementation.trait_pattern,
                generic_names=implementation.generics,
                generic_constraints=implementation.generic_constraints,
                subject_kind=implementation.subject_kind,
            )
            if (
                implementation.definitions
                and implementation.object_pattern is not None
                and implementation.trait_pattern is not None
            ):
                self.env.add_trait_impl_behaviour(
                    implementation.object_pattern,
                    implementation.trait_pattern,
                    provider,
                    implementation.subject_kind,
                    implementation.definitions,
                )
        self._commit_import_sources(resolved_spec, definitions, objects)
        if not resolved_spec.components:
            namespace_name = resolved_spec.alias or Symbol(resolved_spec.path.parts[-1])
            self._imported_namespace_sources[namespace_name] = (
                _show_import_path_for_diagnostic(resolved_spec.path)
            )
            self._imported_namespace_exports[namespace_name] = exports
        reexport = node.public and self._scope_depth == 0
        if reexport:
            self._public_import_trait_implementations.extend(trait_implementations)
            self._public_import_objects.extend(
                replace(
                    obj,
                    public=True,
                    friendly_definitions=(
                        obj.friendly_definitions
                        + tuple(
                            replace(definition, visibility=Symbol("public"))
                            for definition in obj.private_friendly_definitions
                        )
                    ),
                    private_friendly_definitions=(),
                )
                for obj in objects
            )
            self._public_import_definitions.extend(
                replace(definition, public=True) for definition in definitions
            )
            selected_tags = {
                Symbol(component.name.text.removeprefix("#"))
                for component in resolved_spec.components
                if component.kind == Symbol("tag")
            }
            for tag in exports.tags:
                if tag.name in selected_tags:
                    self._public_import_tags.append(tag)
                    self._public_import_overlays.extend(
                        overlay for overlay in exports.overlays if overlay.tag == tag.name
                    )

    return _core.BranchSet((branch.emit(TypedNode(node, None)),))


@_core.register(ObjectNode)
def _object_node(
    self: _core.Analyser,
    node: ObjectNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ObjectNode` node and return the surviving branches."""
    if not self._validate_annotations(node.annotations, node.kind.text, node):
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))

    node = annotation_hooks.DEFAULT_REGISTRY.transform_object(node)
    kind = node.kind.text
    if kind == "object":
        return self._object_definition(branch, node)
    if kind == "trait":
        return self._trait_definition(branch, node)
    if kind == "variant":
        return self._variant_definition(branch, node)
    if kind == "enum":
        return self._enum_definition(branch, node)

    self._diagnose(f"unknown object-like declaration '{node.kind}'", node)
    return _core.BranchSet((branch.emit(TypedNode(node, None)),))
