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

from . import candidates as _calls
from . import callable_values as _functions
from ..control_flow import patterns as _patterns
from ..support import analysis_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
from .models import (
    CallCandidate, ContextualModifierCache, ElementArguments,
    ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






def _has_trailing_defaults(overload: T.Overload) -> bool:
    """True for a required positional prefix followed by optional parameters.

    A call to ``readText(path, encoding=UTF8)`` with one positional value
    supplies the required path. Defaults before required parameters retain
    ordinary ECS right-alignment.
    """
    defaults = overload.param_defaults or ()
    if not defaults or defaults[0] is not None:
        return False
    first_default = next(
        (index for index, value in enumerate(defaults) if value is not None),
        None,
    )
    return first_default is not None and all(
        value is not None for value in defaults[first_default:]
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
        constructor = self.env.lookup_constructor(node.name)
        if node.call_args or (
            constructor is not None
            and any(overload.param_defaults for overload in overloads)
        ):
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
        contextual_cache: ContextualModifierCache = {}
        for overload in overloads:
            for args, popped, ordered_modifiers in _calls._source_element_arguments(
                branch,
                overload,
                modifiers,
                self.env.context,
                analyser=self,
                contextual_cache=contextual_cache,
            ):
                sources.append(
                    ElementArguments(
                        overload=overload,
                        implementation_id=overload.implementation_id,
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
        for overload in overloads:
            prepared = _calls._prepare_element_call_branches(
                branch,
                overload,
                node.call_args,
                bool(node.modifier_args),
                self,
                # A required prefix followed by defaulted arguments binds
                # positionally from the left. Other ECS calls remain right-
                # aligned, including those whose defaults precede required
                # parameters.
                align_positional_right=not _has_trailing_defaults(overload),
            )
            for preparation in prepared:
                if preparation.branch.terminal:
                    terminal.append(preparation.branch)
                    continue
                # Explicit argument expressions can introduce distinct scoped
                # facts, so cache only within this prepared argument branch.
                contextual_cache: ContextualModifierCache = {}
                for args, popped, ordered_modifiers in _calls._source_element_arguments(
                    preparation.branch,
                    overload,
                    modifiers,
                    self.env.context,
                    preparation.call_arg_order,
                    analyser=self,
                    contextual_cache=contextual_cache,
                ):
                    # Explicit argument expressions are evaluated before omitted
                    # operands are sourced from the surrounding stack. In an
                    # inferred-input function this means top-of-stack inputs can
                    # be discovered before deeper inputs. Function parameters,
                    # however, are ordered from deeper to shallower stack values.
                    # Move inputs inferred while sourcing omitted operands ahead
                    # of those inferred by the explicit expressions.
                    if (
                        branch.input_mode is InputMode.INFER_INPUTS
                        and preparation.branch.inputs[: len(branch.inputs)]
                        == branch.inputs
                        and popped.inputs[: len(preparation.branch.inputs)]
                        == preparation.branch.inputs
                    ):
                        expression_inputs = preparation.branch.inputs[
                            len(branch.inputs) :
                        ]
                        omitted_inputs = popped.inputs[
                            len(preparation.branch.inputs) :
                        ]
                        if expression_inputs and omitted_inputs:
                            popped = replace(
                                popped,
                                inputs=(
                                    branch.inputs
                                    + omitted_inputs
                                    + expression_inputs
                                ),
                            )
                    sources.append(
                        ElementArguments(
                            overload=overload,
                            implementation_id=overload.implementation_id,
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
                    branch,
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

