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
    is_catch_all_match_case,
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

        stack_callable = T.normalize(branch.stack[-1]) if branch.stack else None
        generic_overload_set = (
            isinstance(stack_callable, T.OverloadSetType)
            and any(
                _functions._contains_type_var(item)
                for overload in stack_callable.overloads
                for item in (*overload.params, *overload.returns)
            )
        )
        if node.name == Symbol("call") and (
            node.call_args or (node.explicit_call and generic_overload_set)
        ):
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
        modifier_signature_message = self._modifier_signature_message(modifier_args)
        modifier_mismatch_message = self._modifier_generic_mismatch_message(
            node,
            stack_before,
            overloads,
            modifier_args,
        )
        if node.call_args:
            call_shape_message = self._explicit_call_shape_message(node, overloads)
            no_match_message = (
                f"{call_shape_message}\n"
                f"{modifier_signature_message}"
                f"{modifier_mismatch_message}"
                f"{_utils._show_overload_list(node.name, overloads)}"
                if call_shape_message is not None
                else (
                    f"no overloads for element '{node.name}' match explicit call "
                    f"syntax\n{modifier_signature_message}"
                    f"{modifier_mismatch_message}"
                    f"{_utils._show_overload_list(node.name, overloads)}"
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
                f"{modifier_signature_message}"
                f"{modifier_mismatch_message}"
                f"{_utils._show_overload_list(node.name, overloads)}"
            )
            if not candidates:
                near_miss_help = self._element_near_miss_help(node, branch)
                if near_miss_help is not None:
                    no_match_message += f"\nhelp: {near_miss_help}"
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

    @staticmethod
    def _modifier_signature_message(
        modifier_args: tuple[ModifierArgumentAnalysis, ...],
    ) -> str:
        """Render the inferred signatures supplied after ``:`` in diagnostics."""
        if not modifier_args:
            return ""
        heading = (
            "modifier argument signature:"
            if len(modifier_args) == 1
            else "modifier argument signatures:"
        )
        signatures = "\n".join(
            f"  - {index}: {T.show(argument.typ)}"
            for index, argument in enumerate(modifier_args, start=1)
        )
        return f"{heading}\n{signatures}\n"

    @staticmethod
    def _modifier_generic_mismatch_message(
        node: ElementNode,
        stack: T.TypeStack,
        overloads: tuple[T.Overload, ...],
        modifier_args: tuple[ModifierArgumentAnalysis, ...],
    ) -> str:
        """Explain when a collection's generic item type cannot be specialized."""
        if len(modifier_args) != 1 or not stack:
            return ""
        actual = T.normalize(stack[-1])
        modifier = T.normalize(modifier_args[0].typ)
        if not isinstance(actual, T.CollectionType):
            return ""
        if not isinstance(T.normalize(actual.base), T.VarType):
            return ""
        if not isinstance(modifier, T.FunctionType) or not modifier.params:
            return ""
        concrete_modifier_inputs = tuple(T.normalize(param) for param in modifier.params)
        if any(isinstance(param, T.VarType) for param in concrete_modifier_inputs):
            return ""
        reducer = next(
            (
                overload
                for overload in overloads
                if any(
                    isinstance(T.normalize(param), T.FunctionType)
                    for param in overload.params
                )
                and any(
                    isinstance(T.normalize(param), T.CollectionType)
                    for param in overload.params
                )
            ),
            None,
        )
        if reducer is None:
            return ""
        signature = _utils._show_overload_signature(node.name, reducer)
        item_type = T.show(actual.base)
        modifier_inputs = ", ".join(T.show(param) for param in modifier.params)
        return (
            "closest modifier overload mismatch:\n"
            f"  - {signature}\n"
            f"  - collection item type: {item_type} (generic type variable)\n"
            f"  - ':' function inputs: {modifier_inputs}\n"
            f"help: The preceding expression leaves `{item_type}` as an unresolved "
            "generic type variable, so it cannot be matched with the reducer's "
            f"`{modifier_inputs}` inputs. Check the stack-producing operation "
            "immediately before this call.\n"
        )

    def _unknown_element_message(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
    ) -> str:
        """Build an unknown-element message with type-viable typo suggestions."""
        message = f"unknown element '{node.name}'"
        failed_import = next(
            (
                module
                for namespace, module in self._failed_imports.items()
                if node.name.namespace[: len(namespace)] == namespace
            ),
            None,
        )
        if failed_import is not None:
            return (
                f"{message}\n"
                f"note: '{node.name}' is unavailable because module "
                f"'{failed_import}' could not be imported\n"
                "help: fix the import above before checking this element name"
            )
        if branch.variables.read(node.name) is not None:
            return f"{message}\ndid you mean '${node.name}'?"
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

    def _element_near_miss_help(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
    ) -> str | None:
        """Return guidance when a failed element call matches a close semantic peer."""
        if (
            node.name != Symbol("fold")
            or node.call_args
            or len(node.modifier_args) != 1
        ):
            return None
        if not self._viable_suggestion_overloads(node, branch, Symbol("reduce")):
            return None
        return (
            "`fold` requires an explicit accumulator seed; add one before the call, "
            "for example `0 fold: +`, or use `reduce: +` to use the first item"
        )

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

