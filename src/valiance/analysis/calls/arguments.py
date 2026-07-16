"""Focused arguments call analysis."""

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
from .. import _analyser_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
from .models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






class _CallArguments:
    """Own arguments operations for call planning."""

    def element_argument_sources(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
        overloads: tuple[T.Overload, ...],
        modifiers: tuple[ModifierArgumentAnalysis, ...],
    ) -> tuple[list[ElementArguments], list[AnalysisBranch]]:
        """Enumerate parameter-ordered argument sources for an overload."""
        if node.call_args:
            return self.explicit_element_arguments(
                node,
                branch,
                overloads,
                modifiers,
            )

        return self.stack_element_arguments(
            branch,
            overloads,
            modifiers,
        )

    def stack_element_arguments(
        self,
        branch: AnalysisBranch,
        overloads: tuple[T.Overload, ...],
        modifiers: tuple[ModifierArgumentAnalysis, ...],
    ) -> tuple[list[ElementArguments], list[AnalysisBranch]]:
        """Source an element overload's ordinary arguments from the branch stack."""
        sources: list[ElementArguments] = []
        for overload_index, overload in enumerate(overloads):
            for args, popped, ordered_modifiers in _calls._source_element_arguments(
                branch,
                overload,
                modifiers,
                self.env.context,
                analyser=self,
            ):
                sources.append(
                    ElementArguments(
                        overload=overload,
                        overload_index=overload_index,
                        arguments=args,
                        branch=popped,
                        modifiers=ordered_modifiers,
                    )
                )
        return sources, []

    def explicit_element_arguments(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
        overloads: tuple[T.Overload, ...],
        modifiers: tuple[ModifierArgumentAnalysis, ...],
    ) -> tuple[list[ElementArguments], list[AnalysisBranch]]:
        """Merge explicit call arguments with stack inputs in parameter order."""
        sources: list[ElementArguments] = []
        terminal: list[AnalysisBranch] = []
        for overload_index, overload in enumerate(overloads):
            prepared = _calls._prepare_element_call_branches(
                branch,
                overload,
                node.call_args,
                bool(node.modifier_args),
                self,
            )
            for preparation in prepared:
                if preparation.branch.terminal:
                    terminal.append(preparation.branch)
                    continue
                for args, popped, ordered_modifiers in _calls._source_element_arguments(
                    preparation.branch,
                    overload,
                    modifiers,
                    self.env.context,
                    preparation.call_arg_order,
                    analyser=self,
                ):
                    sources.append(
                        ElementArguments(
                            overload=overload,
                            overload_index=overload_index,
                            arguments=args,
                            branch=popped,
                            modifiers=ordered_modifiers,
                            call_arg_order=preparation.call_arg_order,
                        )
                    )
        return sources, terminal

    def _modifier_argument_types(
        self,
        branch: AnalysisBranch,
        node: ElementNode,
    ) -> tuple[ModifierArgumentAnalysis, ...] | None:
        """Determine the types used for modifier argument during static analysis."""
        analyses: list[ModifierArgumentAnalysis] = []
        for arg in node.modifier_args:
            result = self._analyse_function_literal(branch, arg)
            if result is None:
                return None
            function, _ = result
            analyses.append(
                ModifierArgumentAnalysis(
                    function.typ,
                    TypedFunctionNode(arg, function.typ, function.overloads),
                )
            )
        return tuple(analyses)

    def _literal_item_options(
        self,
        branch: AnalysisBranch,
        expressions: tuple[tuple[ASTNode, ...], ...],
        node: ASTNode,
        *,
        message: str = "literal item must leave a value on the stack",
    ) -> tuple[tuple[ListItemAnalysis, ...], ...] | None:
        """Compute literal item options during static analysis."""
        item_options: list[tuple[ListItemAnalysis, ...]] = []
        for expression in expressions:
            diagnostics_before = len(self.diagnostics)
            item_outputs = self.analyse_scoped_block(
                BranchSet((branch,)),
                expression,
            )
            options = tuple(
                item_result
                for output in item_outputs
                if (
                    item_result := _utils._list_item_analysis(branch, output)
                )
                is not None
            )
            if not options:
                if item_outputs or len(self.diagnostics) == diagnostics_before:
                    self._diagnose(message, node)
                return None
            item_options.append(options)
        return tuple(item_options)

