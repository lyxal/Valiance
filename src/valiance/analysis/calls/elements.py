"""Element invocation and smart call diagnostics."""

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

from ..calls import candidates as _calls
from ..calls import callable_values as _functions
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


















class _ElementCalls:
    """Own element invocation and suggestion operations."""

    def _element(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Analyse a `ElementNode` node and return the surviving branches."""
        overloads = self.env.overloads_for(node.name)
        if not overloads:
            self._diagnose(self._unknown_element_message(node, branch), node)
            return BranchSet()
        if not annotation_hooks.valid_element_annotations(node.annotations):
            self._diagnose(
                f"unsupported element annotation on '{node.name}'",
                node,
            )
            return BranchSet()

        modifier_args = self._modifier_argument_types(branch, node)
        if modifier_args is None:
            return BranchSet()
        if node.modifier_args and not _calls._modifier_arity_matches(
            overloads,
            modifier_args,
        ):
            self._diagnose(
                f"element '{node.name}' expects "
                f"{_calls._show_modifier_counts(overloads)} ':' function argument(s), "
                f"got {len(modifier_args)}",
                node,
            )
            return BranchSet()

        if node.call_args and node.name == Symbol("call"):
            return self._call_element_call(branch, node, overloads)

        diagnostics_before = len(self.diagnostics)
        sources, terminal = self.element_argument_sources(
            node,
            branch,
            overloads,
            modifier_args,
        )
        if not sources and terminal:
            return BranchSet.collect(terminal)
        if not sources and len(self.diagnostics) > diagnostics_before:
            return BranchSet()
        candidates = self.element_call_candidates(node, overloads, sources)

        stack_before = branch.stack
        if node.call_args:
            call_shape_message = self._explicit_call_shape_message(node, overloads)
            no_match_message = (
                f"{call_shape_message}\n"
                f"{_utils._show_overload_list(node.name, overloads)}"
                if call_shape_message is not None
                else (
                    f"no overloads for element '{node.name}' match explicit call "
                    f"syntax\n{_utils._show_overload_list(node.name, overloads)}"
                )
            )
            ambiguous_message = (
                f"ambiguous overloads for element '{node.name}' "
                "with explicit call syntax"
            )
        else:
            no_match_message = (
                f"no overloads for element '{node.name}' match stack "
                f"{_utils._show_stack(stack_before)}\n"
                f"{_utils._show_overload_list(node.name, overloads)}"
            )
            ambiguous_message = (
                f"ambiguous overloads for element '{node.name}' with stack "
                f"{_utils._show_stack(stack_before)}"
            )
        winners = self.select_call_winners(
            candidates=candidates,
            branch=branch,
            node=node,
            no_match_message=no_match_message,
            ambiguous_message=ambiguous_message,
        )
        if winners is None:
            return BranchSet.collect(terminal)

        results: list[AnalysisBranch] = list(terminal)
        for candidate in winners:
            committed = self.commit_element_candidate(node, overloads, candidate)
            if committed is not None:
                results.append(committed)
        return BranchSet.collect(results)

    def _unknown_element_message(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
    ) -> str:
        """Build an unknown-element message with type-viable typo suggestions."""
        message = f"unknown element '{node.name}'"
        suggestions = self._element_name_suggestions(node, branch)
        if not suggestions:
            return message
        return f"{message}\ndid you mean:\n" + "\n".join(
            f"  - {suggestion}" for suggestion in suggestions
        )

    def _element_name_suggestions(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
    ) -> tuple[str, ...]:
        """Return close visible element signatures that can consume this call."""
        attempted = str(node.name)
        ranked: list[tuple[float, Symbol]] = []
        for name in self.env.visible_overload_names():
            if _utils._internal_element_name(name):
                continue
            score = _utils._name_similarity(attempted, str(name))
            if score >= 0.62:
                ranked.append((score, name))
        ranked.sort(key=lambda item: (-item[0], str(item[1])))

        suggestions: list[str] = []
        for _, name in ranked[:12]:
            for overload in self._viable_suggestion_overloads(node, branch, name):
                rendered = _utils._show_overload_signature(name, overload)
                if rendered not in suggestions:
                    suggestions.append(rendered)
                if len(suggestions) == 3:
                    return tuple(suggestions)
        return tuple(suggestions)

    def _viable_suggestion_overloads(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
        name: Symbol,
    ) -> tuple[T.Overload, ...]:
        """Probe one similar name without leaking speculative diagnostics."""
        overloads = self.env.overloads_for(name)
        if not overloads:
            return ()
        candidate_node = replace(node, name=name, annotations=(), extension=None)
        probe = self._child_analyser(self.env.lexical_child_scope())
        prelude_nodes = len(self._prelude.nodes)
        prelude_bindings = len(self._prelude.bindings)
        try:
            modifiers = probe._modifier_argument_types(branch, candidate_node)
            if modifiers is None:
                return ()
            if candidate_node.modifier_args and not _calls._modifier_arity_matches(
                overloads,
                modifiers,
            ):
                return ()
            if candidate_node.call_args and name == Symbol("call"):
                return ()
            sources, _ = probe.element_argument_sources(
                candidate_node,
                branch,
                overloads,
                modifiers,
            )
            candidates = probe.element_call_candidates(
                candidate_node,
                overloads,
                sources,
            )
            viable: list[T.Overload] = []
            for candidate in candidates:
                overload = candidate.applied.overload
                if overload.annotation_error is not None or overload in viable:
                    continue
                viable.append(overload)
            return tuple(viable)
        finally:
            del self._prelude.nodes[prelude_nodes:]
            del self._prelude.bindings[prelude_bindings:]

    def _explicit_call_shape_message(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
    ) -> str | None:
        """Diagnose named-argument mistakes before generic overload failure."""
        named_args = tuple(arg.name for arg in node.call_args if arg.name is not None)
        seen: set[Symbol] = set()
        for name in named_args:
            if name in seen:
                return (
                    f"named argument '{name}' is provided more than once for "
                    f"element '{node.name}'"
                )
            seen.add(name)

        parameter_names = tuple(
            name
            for overload in overloads
            for name in overload.param_names
            if name is not None
        )
        known = set(parameter_names)
        for name in named_args:
            if name in known:
                continue
            message = f"unknown named argument '{name}' for element '{node.name}'"
            suggestions = _utils._similar_names(str(name), parameter_names, limit=1)
            if suggestions:
                message += f"\ndid you mean '{suggestions[0]}'?"
            return message
        return None

