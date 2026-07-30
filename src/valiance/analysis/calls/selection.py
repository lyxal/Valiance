"""Focused selection call analysis."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import (
    dataclass,
    field,
    fields,
    is_dataclass,
    replace,
)
from enum import Enum, auto
from hashlib import sha1
from itertools import count
from pathlib import Path
from typing import cast

import valiance.analysis.contracts.annotations as annotation_hooks
import valiance.vtypes as T
import valiance.analysis.contracts.where_clauses as static_where
from valiance.elements.builtins import default_environment
from valiance.analysis.lints import (
    DEFAULT_REGISTRY as DEFAULT_LINT_REGISTRY,
    BlockLintContext,
    LintFinding,
    LintRegistry,
    MatchLintContext,
    NodeLintContext,
)
from valiance.asts import (
    AnnotationNode,
    ASTNode,
    BindingPatternNode,
    DefineNode,
    ElementExtension,
    ElementNode,
    EnumMemberNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    ImportComponent,
    ImportPath,
    ImportSpec,
    ListPatternNode,
    MatchCaseNode,
    MatchNode,
    MatchPatternNode,
    ObjectNode,
    OrPatternNode,
    PopNNode,
    SourceLocation,
    TraitRequirementNode,
    TryHandlerNode,
    TryNode,
    TypePatternNode,
    TypedCallNode,
    TypedElementExtension,
    TypedElementNode,
    TypedExtensionPatternRule,
    TypedFunctionNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedMatchNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedTryNode,
    VariantMemberNode,
    is_default_match_case,
)
from valiance.asts.nodes import GetVariableNode, ObjectFieldNode
from valiance.modules_system.modules import ModuleLoader, ModuleLoadError, import_definitions
from valiance.asts.object_constructors import (
    constructor_definitions,
    definitely_initialized_fields,
    prepare_constructor_body,
)
from valiance.vtypes.symbols import Symbol
from valiance.vtypes.default_types import Boolean

from . import candidates as _calls
from . import callable_values as _functions
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
from .models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






class _CallSelection:
    """Own selection operations for call planning."""

    def select_call_winners(
        self,
        *,
        candidates: Iterable[CallCandidate],
        branch: AnalysisBranch,
        node: ASTNode,
        no_match_message: str,
        ambiguous_message: str,
    ) -> tuple[CallCandidate, ...] | None:
        """Select the most specific viable call candidates and diagnose ambiguity."""
        winners = _functions._collapse_equivalent_call_winners(
            _functions._collapse_equivalent_friendly_multidispatch_winners(
                _functions._best_candidates(candidates, branch)
            )
        )
        if not winners:
            self._diagnose(no_match_message, node)
            return None
        if (
            len(winners) > 1
            and branch.input_mode is not InputMode.INFER_INPUTS
            and not _functions._winners_specialize_inputs(winners, branch)
        ):
            self._diagnose(
                f"{ambiguous_message}\n"
                f"candidate overloads:\n{_utils._show_applied_overloads(winners)}",
                node,
            )
            return None
        return winners

    def element_call_candidates(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
        sources: Iterable[ElementArguments],
    ) -> list[CallCandidate]:
        """Build viable typed candidates for an explicit element call."""
        candidates: list[CallCandidate] = []
        for source in sources:
            candidate = _calls._apply_overload_to_branch(
                source.overload,
                source.arguments,
                source.branch,
                self.env.context,
                self.env,
                node.disambiguation,
                self,
            )
            if candidate is None:
                candidate = _calls._apply_overload_via_unit_overlay(
                    node.name,
                    source.overload,
                    source.arguments,
                    source.branch,
                    self.env.context,
                    self.env,
                    node.disambiguation,
                    self,
                )
            if candidate is None:
                continue

            applied = _calls._apply_tag_overlay(
                node.name,
                source.arguments,
                candidate.applied,
                self.env.context,
                self.env,
            )
            selected_is_friendly = self.env.overload_is_object_friendly(
                node.name,
                source.overload_index,
            )
            dispatch_overloads = tuple(
                overload
                for index, overload in enumerate(overloads)
                if self.env.overload_is_object_friendly(node.name, index)
                == selected_is_friendly
            )
            applied = _functions._mark_multidispatch(
                applied,
                dispatch_overloads,
                self.env.context,
            )
            candidates.append(
                CallCandidate(
                    applied=applied,
                    branch=candidate.branch,
                    modifiers=source.modifiers,
                    call_arg_order=source.call_arg_order,
                    overload_index=source.overload_index,
                    dispatch_priority=(
                        0
                        if selected_is_friendly
                        else 1
                    ),
                )
            )
        return candidates

    def commit_element_candidate(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
        candidate: CallCandidate,
    ) -> AnalysisBranch | None:
        """Emit the typed node for a selected element-call candidate."""
        overload = candidate.applied.overload
        if overload.annotation_error is not None:
            self._diagnose(overload.annotation_error, node)
            return None

        if overload.annotation_warning is not None:
            self._warn(overload.annotation_warning, node)

        actual_returns = annotation_hooks.annotated_element_returns(
            node,
            candidate.applied.actual_returns,
        )
        extension = self._analyse_element_extension(
            node.extension,
            candidate.applied,
            candidate.branch,
        )
        if node.extension is not None and extension is None:
            return None
        overload_runtime_name = self.env.overload_runtime_name_for(
            node.name, overload
        )
        selected_index = (
            candidate.overload_index
            if candidate.overload_index is not None
            else _calls._overload_index(overloads, overload)
        )
        # An imported source overload is compiled as its own one-slot function.
        # Its visible overload-set index therefore must not be reused at runtime.
        runtime_index = 0 if overload_runtime_name is not None else selected_index
        return candidate.branch.push(*actual_returns).emit(
            TypedElementNode(
                node,
                _calls._returns_result_type(actual_returns),
                candidate.applied,
                runtime_index,
                _calls._specialize_modifier_arguments(
                    candidate.applied,
                    candidate.modifiers,
                    self.env.context,
                ),
                candidate.call_arg_order,
                candidate.callable_overload_index,
                extension,
                overload_runtime_name or self.env.runtime_name_for(node.name),
            )
        )

    def _call_element_call(
        self,
        branch: AnalysisBranch,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
    ) -> BranchSet:
        """Compute an explicit call of a callable value from the stack."""
        if node.modifier_args:
            self._diagnose("element 'call' does not accept ':' arguments", node)
            return BranchSet()
        if any(arg.name is not None for arg in node.call_args):
            self._diagnose(
                "named arguments are not supported when calling a function value; "
                "function values use positional arguments, so remove the argument "
                "names. Named arguments are available when calling a named element "
                "directly",
                node,
            )
            return BranchSet()
        stack_function_overloads = (
            _functions._callable_overloads(branch.stack.items[-1])
            if branch.stack
            else ()
        )
        if not stack_function_overloads:
            current = BranchSet((branch,))
            for arg in node.call_args:
                current = self.analyse_scoped_block(current, arg.value)
                if not current:
                    return BranchSet()
            terminal, current = _utils._split_terminal_branches(current)
            if not current:
                return terminal
            positional_candidates: list[CallCandidate] = []
            call_arg_count = len(node.call_args)
            for arg_branch in current:
                positional_candidates.extend(
                    self.call_element_candidates_for_branch(
                        node, overloads[0], arg_branch, call_arg_count
                    )
                )
            winners = self.select_call_winners(
                candidates=positional_candidates,
                branch=branch,
                node=node,
                no_match_message=(
                    "no overloads for element 'call' match explicit call syntax"
                ),
                ambiguous_message=(
                    "ambiguous overloads for element 'call' with explicit call syntax"
                ),
            )
            if winners is None:
                return terminal
            results: list[AnalysisBranch] = list(terminal.branches)
            for candidate in winners:
                results.append(
                    candidate.branch.push(*candidate.applied.actual_returns).emit(
                        TypedElementNode(
                            node,
                            _calls._returns_result_type(
                                candidate.applied.actual_returns
                            ),
                            candidate.applied,
                            0,
                            (),
                            candidate.call_arg_order,
                            candidate.callable_overload_index,
                        )
                    )
                )
            return BranchSet.collect(results)

        function_type = branch.stack.items[-1]
        base = branch.with_stack(T.TypeStack(branch.stack.items[:-1]))
        candidates: list[CallCandidate] = []
        terminal: list[AnalysisBranch] = []

        for callable_index, callable_overload in enumerate(
            _functions._callable_overloads(function_type)
        ):
            prepared = _calls._prepare_element_call_branches(
                base,
                callable_overload,
                node.call_args,
                False,
                self,
                align_positional_right=False,
            )
            for preparation in prepared:
                if preparation.branch.terminal:
                    terminal.append(preparation.branch)
                    continue
                for args, popped, _modifiers in _calls._source_element_arguments(
                    preparation.branch,
                    callable_overload,
                    (),
                    self.env.context,
                    preparation.call_arg_order,
                    analyser=self,
                ):
                    stack_count = len(callable_overload.params) - sum(
                        not arg.placeholder for arg in node.call_args
                    )
                    argument_order = preparation.call_arg_order or tuple(
                        range(len(callable_overload.params))
                    )
                    runtime_order = tuple(
                        index if index < stack_count else index + 1
                        for index in argument_order
                    ) + (stack_count,)
                    identity = tuple(range(len(runtime_order)))
                    planned = _calls._call_element_candidates(
                        popped,
                        overloads[0],
                        function_type,
                        args,
                        popped.stack.items,
                        () if runtime_order == identity else runtime_order,
                        node.disambiguation,
                        self.env.context,
                        self.env,
                        self,
                    )
                    candidates.extend(
                        candidate
                        for candidate in planned
                        if candidate.callable_overload_index == callable_index
                    )

        winners = self.select_call_winners(
            candidates=candidates,
            branch=branch,
            node=node,
            no_match_message=(
                "no overloads for element 'call' match explicit call syntax"
            ),
            ambiguous_message=(
                "ambiguous overloads for element 'call' with explicit call syntax"
            ),
        )
        if winners is None:
            return BranchSet.collect(terminal)

        results: list[AnalysisBranch] = list(terminal)
        for candidate in winners:
            extension = self._analyse_element_extension(
                node.extension,
                candidate.applied,
                candidate.branch,
            )
            if node.extension is not None and extension is None:
                continue
            results.append(
                candidate.branch.push(*candidate.applied.actual_returns).emit(
                    TypedElementNode(
                        node,
                        _calls._returns_result_type(candidate.applied.actual_returns),
                        candidate.applied,
                        0,
                        (),
                        candidate.call_arg_order,
                        candidate.callable_overload_index,
                        extension,
                    )
                )
            )
        return BranchSet.collect(results)

    def call_element_candidates_for_branch(
        self,
        node: ElementNode,
        call_overload: T.Overload,
        arg_branch: AnalysisBranch,
        call_arg_count: int,
    ) -> list[CallCandidate]:
        """Build callable-value candidates for the built-in `call` element."""
        if len(arg_branch.stack) < call_arg_count:
            return []

        call_values = arg_branch.stack.items[-call_arg_count:] if call_arg_count else ()
        base_stack = arg_branch.stack.items[:-call_arg_count]
        explicit_function_order = (
            (*range(1, call_arg_count), 0) if call_arg_count > 1 else ()
        )
        candidates = _calls._call_element_candidates(
            arg_branch,
            call_overload,
            call_values[0],
            call_values[1:],
            base_stack,
            explicit_function_order,
            node.disambiguation,
            self.env.context,
            self.env,
            self,
        )
        if candidates or not base_stack:
            return candidates

        return _calls._call_element_candidates(
            arg_branch,
            call_overload,
            base_stack[-1],
            call_values,
            base_stack[:-1],
            (),
            node.disambiguation,
            self.env.context,
            self.env,
            self,
        )

