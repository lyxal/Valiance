"""Focused extensions call analysis."""

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

import valiance.analysis.annotations as annotation_hooks
import valiance.vtypes as T
import valiance.analysis.where_clause as static_where
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






class _CallExtensions:
    """Own extensions operations for call planning."""

    def _analyse_element_extension(
        self,
        extension: ElementExtension | None,
        applied: T.AppliedOverload,
        outer: AnalysisBranch,
    ) -> TypedElementExtension | None:
        """Analyse element extension during static analysis."""
        if extension is None:
            return None

        if extension.default is not None:
            typed = self._analyse_extension_function(outer, extension.default)
            if typed is None:
                return None
            returns = _calls._single_function_return(typed)
            if returns is None:
                self._diagnose(
                    "extend default must produce exactly one value",
                    extension,
                )
                return None
            if not all(
                T.compatible(returns, param, self.env.context)
                for param in applied.params
            ):
                self._diagnose(
                    "extend default must be compatible with every element parameter",
                    extension,
                )
                return None
            return TypedElementExtension(default=typed)

        if extension.rules:
            typed_rules: list[TypedExtensionPatternRule] = []
            seen_patterns: set[tuple[bool, ...]] = set()
            for rule in extension.rules:
                if len(rule.pattern) != len(applied.params):
                    self._diagnose(
                        "extend pattern arity must match the target element arity",
                        extension,
                    )
                    return None
                presence = tuple(name is not None for name in rule.pattern)
                if presence in seen_patterns:
                    self._diagnose("duplicate extend pattern", extension)
                    return None
                seen_patterns.add(presence)

                typed_params = tuple(
                    FunctionParam(name=name, typ=param)
                    for name, param in zip(
                        rule.pattern,
                        applied.params,
                        strict=True,
                    )
                    if name is not None
                )
                function = replace(rule.function, params=typed_params)
                typed = self._analyse_extension_function(outer, function)
                if typed is None:
                    return None
                returns = _calls._consistent_function_returns(typed)
                missing = tuple(
                    param
                    for name, param in zip(
                        rule.pattern,
                        applied.params,
                        strict=True,
                    )
                    if name is None
                )
                if returns is None or len(returns) != len(missing):
                    self._diagnose(
                        "extend pattern rule must produce one substitution "
                        "for each missing argument",
                        extension,
                    )
                    return None
                if not all(
                    T.compatible(actual, expected, self.env.context)
                    for actual, expected in zip(returns, missing, strict=True)
                ):
                    self._diagnose(
                        "extend pattern substitutions must match the missing "
                        "parameter types",
                        extension,
                    )
                    return None
                typed_rules.append(TypedExtensionPatternRule(rule.pattern, typed))
            return TypedElementExtension(rules=tuple(typed_rules))

        if extension.selector is not None:
            optional_params = tuple(T.optional(param) for param in applied.params)
            selector = replace(
                extension.selector,
                params=tuple(FunctionParam(typ=param) for param in optional_params),
            )
            typed = self._analyse_extension_function(outer, selector)
            if typed is None:
                return None
            selector_arity = _patterns._extension_selector_arity(typed)
            if selector_arity != len(applied.params):
                self._diagnose(
                    "extend selector arity must match the target element arity",
                    extension,
                )
                return None
            returned = _calls._single_function_return(typed)
            if returned is None:
                self._diagnose(
                    "extend selector must produce exactly one value",
                    extension,
                )
                return None
            if not all(
                T.compatible(returned, T.optional(param), self.env.context)
                for param in applied.params
            ):
                self._diagnose(
                    "extend selector result must be optional-compatible with "
                    "every element parameter",
                    extension,
                )
                return None
            return TypedElementExtension(selector=typed)

        self._diagnose("invalid extend clause", extension)
        return None

    def _analyse_extension_function(
        self,
        outer: AnalysisBranch,
        function: FunctionNode,
    ) -> TypedFunctionNode | None:
        """Analyse extension function during static analysis."""
        result = self._analyse_function_literal(outer, function)
        if result is None:
            return None
        analysis, _ = result
        return TypedFunctionNode(function, analysis.typ, analysis.overloads)

