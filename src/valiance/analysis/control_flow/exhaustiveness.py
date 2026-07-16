"""Focused exhaustiveness control-flow analysis."""

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

from ..calls import candidates as _calls
from ..calls import callable_values as _functions
from . import patterns as _patterns
from .. import _analyser_utils as _utils
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)

from ..calls.models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






class _ExhaustivenessAnalysis:
    """Own exhaustiveness control-flow operations."""

    def _match_is_exhaustive(
        self,
        subject_types: tuple[T.Type, ...],
        node: MatchNode,
    ) -> bool:
        """Return the Boolean result of match is exhaustive during static analysis."""
        if any(
            case.is_default or is_default_match_case(case.patterns)
            for case in node.cases
        ):
            return True
        if len(subject_types) != 1:
            self._diagnose(
                "match without default requires one enum or variant value",
                node,
            )
            return False
        subject_type = T.normalize(subject_types[0])
        if isinstance(subject_type, T.UnionType):
            missing = tuple(
                item
                for item in sorted(subject_type.items, key=T.show)
                if not any(
                    len(case.patterns) == 1
                    and _patterns._pattern_is_irrefutable(
                        case.patterns[0],
                        item,
                        self.env,
                    )
                    for case in node.cases
                )
            )
            if not missing:
                return True
            self._diagnose(
                "non-exhaustive match; missing cases for: "
                + ", ".join(T.show(item) for item in missing),
                node,
            )
            return False
        if (
            isinstance(subject_type, T.NominalType)
            and subject_type.name.text == "Result"
            and len(subject_type.args) == 2
        ):
            result_branches = (T.OKType(subject_type.args[0]), subject_type.args[1])
            missing = tuple(
                item
                for item in result_branches
                if not any(
                    len(case.patterns) == 1
                    and _patterns._pattern_is_irrefutable(
                        case.patterns[0],
                        item,
                        self.env,
                    )
                    for case in node.cases
                )
            )
            if not missing:
                return True
            self._diagnose(
                "non-exhaustive Result match; missing cases for: "
                + ", ".join(T.show(item) for item in missing),
                node,
            )
            return False
        closed_name = _patterns._nominal_name(subject_type)
        if closed_name is None:
            self._diagnose("match without default requires enum or variant value", node)
            return False
        expected = _patterns._closed_match_members(self.env, closed_name)
        if expected is None:
            self._diagnose("match without default requires enum or variant value", node)
            return False
        covered = {
            member
            for case in node.cases
            for pattern in case.patterns
            for member in _patterns._covered_closed_members(
                pattern,
                subject_type,
                expected,
                self.env,
            )
        }
        missing = tuple(member for member in expected if member not in covered)
        if missing:
            self._diagnose(
                "non-exhaustive match for "
                f"{closed_name}; missing cases: "
                + ", ".join(str(member) for member in missing),
                node,
            )
            return False
        return True

