"""Focused loops control-flow analysis."""

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
from . import patterns as _patterns
from ..support import analysis_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)

from ..calls.models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






class _LoopAnalysis:
    """Own loops control-flow operations."""

    def _analyse_unfold_body_function(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
    ) -> FunctionAnalysis | None:
        """Analyse unfold body function during static analysis."""
        if node.params is None:
            analysed = self._analyse_function_literal(outer, node)
            return None if analysed is None else analysed[0]

        params = _functions._declared_params(node)
        body_params = tuple(_functions._parameter_value_type(param) for param in params)
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(node.params, body_params, strict=True)
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=_functions._function_capture_source(outer),
        )
        initial = AnalysisBranch(
            inputs=body_params,
            variables=variables,
            input_mode=(
                InputMode.CYCLE_EXPLICIT_PARAMS if body_params else InputMode.NILADIC
            ),
            cycle_params=body_params,
            atomic_type_vars=(
                outer.atomic_type_vars
                | _functions._atomic_parameter_type_vars(params)
            ),
            origin=outer.origin,
        )
        function_analyser = self._child_analyser(self.env.lexical_child_scope())
        final = function_analyser.analyse_block(BranchSet((initial,)), node.body)
        signatures = self._function_signatures(node, final)
        analysis = _functions._function_analysis_from_signatures(signatures)
        if analysis is None:
            self.diagnostics.extend(function_analyser.diagnostics)
            self.warnings.extend(function_analyser.warnings)
            self._extend_lint_findings(function_analyser.lint_findings)
            return None
        self.warnings.extend(function_analyser.warnings)
        self._extend_lint_findings(function_analyser.lint_findings)
        return analysis

