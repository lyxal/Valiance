"""Concrete AST node handlers registered with the analyser."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
from valiance.analysis.lints import canonical_lint_code, finding
import valiance.vtypes as T
from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
    AssertNode,
    AtNode,
    CastNode,
    DictLiteralNode,
    ElementTagDeclarationNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionParam,
    ImportNode,
    IndexAccessNode,
    IndexSetNode,
    ListLiteralNode,
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
    elif isinstance(source, (T.ArrayExactType, T.ArrayMinType)):
        collection_type = T.ArrayExactType
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
    source_hints = tuple(T.V(f"_at_{branch.origin}_{index}") for index in range(arity))
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

    candidates: list[tuple[int, T.AppliedOverload]] = []
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
        candidates.append((index, applied))

    if not candidates:
        self._diagnose("at body does not accept the selected level values", node)
        return _core.BranchSet()
    if len(candidates) > 1:
        self._diagnose("at body has ambiguous inferred overloads", node)
        return _core.BranchSet()

    overload_index, applied = candidates[0]
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
                    overload_index,
                )
            ),
        )
    )


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
    target = _functions._parameter_value_type(T.normalize(node.typ))
    self._validate_element_tags_in_types((target,), node)
    if not self._validate_data_tags(((target,),), node, allow_variants=False, require_declared=True):
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    if not branch.stack:
        self._diagnose(f"empty stack when casting to {T.show(target)}", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    source = branch.stack[-1]
    statically_safe = T.assignable(source, target, self.env.context)
    runtime_refinement = node.checked or node.optional
    if runtime_refinement and not statically_safe:
        invalid = _patterns._uncheckable_runtime_type(target)
        if invalid is not None:
            self._diagnose(f"{T.show(invalid)} cannot be checked at runtime", node)
            return _core.BranchSet()
        if not _utils._types_overlap(source, target, self.env.context):
            if node.checked and _functions._type_contains_rank_var(target):
                stack = T.TypeStack((*branch.stack.items[:-1], target))
                return _core.BranchSet((branch.with_stack(stack).emit(TypedNode(node, target)),))
            self._diagnose(f"cannot cast {T.show(source)} to {T.show(target)}", node)
            return _core.BranchSet()
    elif not runtime_refinement and not statically_safe:
        self._diagnose(f"cannot safely cast {T.show(source)} to {T.show(target)}", node)
        return _core.BranchSet()
    flowed = _calls._apply_data_tag_flow((source,), (target,), (target,), self.env.context)[0]
    result = T.optional(flowed) if node.optional else flowed
    if node.checked and statically_safe:
        node = replace(node, checked=False)
    stack = T.TypeStack((*branch.stack.items[:-1], result))
    return _core.BranchSet((branch.with_stack(stack).emit(TypedNode(node, result)),))


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
    if not branch.stack:
        self._diagnose("empty stack when assuring minimum rank", node)
        return _core.BranchSet((branch.emit(TypedNode(node, None)),))
    result = _minimum_rank_type(branch.stack[-1], node.rank)
    stack = T.TypeStack((*branch.stack.items[:-1], result))
    return _core.BranchSet((branch.with_stack(stack).emit(TypedNode(node, result)),))


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
    params = tuple(T.V(f"_pop_n_{index}") for index in range(node.count))
    sourced = branch.source_arguments(params)
    if sourced is None:
        self._diagnose(
            f"stack underflow for pop_n({node.count}); "
            f"expected {node.count} value(s)",
            node,
        )
        return _core.BranchSet()
    _args, popped = sourced
    return _core.BranchSet((popped.emit(TypedNode(node)),))


@_core.register(StackShuffleNode)
def _stack_shuffle_node(
    self: _core.Analyser,
    node: StackShuffleNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `StackShuffleNode` node and return the surviving branches."""
    params = tuple(T.V(f"_shuffle_{index}") for index, _ in enumerate(node.prestack))
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
    copy_errors = tuple(
        _utils._copy_diagnostic(typ, self.env)
        for typ in _utils._copied_stack_shuffle_types(
            node,
            args,
            labelled,
            stack_arg_start,
        )
    )
    for error in copy_errors:
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
        no_match_message=(
            f"no overloads for call target {T.show(callable_type)} match stack "
            f"{_utils._show_stack(callable_popped.stack)}\n"
            f"{_utils._show_overload_list(None, overloads)}"
        ),
        ambiguous_message=(
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




































@_core.register(ImportNode)
def _import_node(
    self: _core.Analyser,
    node: ImportNode,
    branch: _core.AnalysisBranch,
) -> _core.BranchSet:
    """Analyse a `ImportNode` node and return the surviving branches."""
    for spec in node.specs:
        try:
            exports, resolved_spec, definitions = self._load_import_definitions(spec)
            objects = import_objects(exports, resolved_spec)
            import_environment_facts(exports, resolved_spec, self.env)
        except ModuleLoadError as exc:
            self._diagnose(str(exc), node)
            return _core.BranchSet((branch.emit(TypedNode(node, None)),))

        for typed_node in exports.runtime_prelude:
            self._prelude.add(typed_node)
        for obj in objects:
            runtime_name = self._prelude.add_declaration(obj.typed, obj.name)
            self._register_imported_object(obj, runtime_name)
        for definition in definitions:
            runtime_name = self._prelude.add_declaration(
                definition.typed,
                definition.name,
            )
            try:
                self._register_imported_definition(
                    definition.name,
                    definition.typed,
                    runtime_name,
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
            self._imported_definition_sources[definition.name] = (
                self._import_source_text(resolved_spec, definition.name)
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
