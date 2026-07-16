"""Focused matches control-flow analysis."""

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
from . import patterns as _patterns
from .. import _analyser_utils as _utils
from .. import analyser as _core
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)

from ..calls.models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






class _MatchAnalysis:
    """Own matches control-flow operations."""

    def _match(
        self,
        node: MatchNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Analyse a `MatchNode` node and return the surviving branches."""
        if not node.cases:
            self._diagnose("match requires at least one case", node)
            return BranchSet()

        arities = {len(case.patterns) for case in node.cases}
        arity = next(iter(arities)) if len(arities) == 1 else None
        if arity is None:
            self._diagnose("match cases must match the same number of values", node)
            return BranchSet()
        if arity == 0:
            self._diagnose("match requires at least one pattern per case", node)
            return BranchSet()

        subject_params = tuple(
            reversed(
                tuple(
                    _patterns._match_subject_pattern_type(branch, node, index, self.env)
                    for index in range(arity)
                )
            )
        )
        sourced = branch.source_arguments(subject_params)
        if sourced is None:
            self._diagnose(
                f"match requires {arity} value{'s' if arity != 1 else ''} on the stack",
                node,
            )
            return BranchSet()
        stack_subjects, body_input = sourced
        subject_types = tuple(reversed(stack_subjects))
        if not self._match_patterns_are_valid(subject_types, node):
            return BranchSet()
        if not self._match_is_exhaustive(subject_types, node):
            return BranchSet()
        self._extend_lint_findings(
            self.lint_registry.check_match(
                MatchLintContext(node=node, branch=branch, env=self.env)
            )
        )

        joined: AnalysisBranch | None = None
        typed_case_bodies: list[tuple[ASTNode | TypedNode, ...]] = []
        subject_variables = _patterns._match_subject_variables(branch, arity)
        previous_patterns: list[tuple[MatchPatternNode, ...]] = []
        for case in node.cases:
            case_variables = _patterns._match_case_variables(
                body_input.variables,
                case.patterns,
                subject_types,
                self.env,
            )
            if subject_variables:
                case_variables = _patterns._refine_match_subject_variables(
                    case_variables,
                    subject_variables,
                    case.patterns,
                    subject_types,
                    tuple(previous_patterns),
                    self.env,
                )
            case_input = body_input.with_variables(case_variables)
            case_input = replace(
                case_input,
                input_mode=InputMode.CYCLE_EXPLICIT_PARAMS,
                cycle_params=subject_types,
                cycle_index=0,
            )
            if not self._match_guards_are_valid(subject_types, case.patterns, node):
                return BranchSet()
            case_outputs = self.analyse_scoped_block(
                BranchSet((case_input,)),
                case.body,
            )
            typed_case_bodies.append(
                _patterns._typed_block(
                    case_outputs,
                    len(case_input.typed_body),
                    case.body,
                )
            )
            for output in case_outputs:
                candidate = _patterns._match_case_output(output, body_input, node)
                joined = _patterns._join_match_output(
                    original=branch,
                    baseline=body_input,
                    joined=joined,
                    candidate=candidate,
                    ctx=self.env.context,
                )
                if joined is None:
                    self._diagnose("match cases inferred different inputs", node)
                    return BranchSet()
            previous_patterns.append(case.patterns)

        if joined is None:
            return BranchSet()
        return BranchSet(
            (
                joined.emit(
                    TypedMatchNode(
                        node,
                        _calls._returns_result_type(joined.stack.items),
                        case_bodies=tuple(typed_case_bodies),
                    )
                ),
            )
        )

    def _match_guards_are_valid(
        self,
        subject_types: tuple[T.Type, ...],
        patterns: tuple[MatchPatternNode, ...],
        node: MatchNode,
    ) -> bool:
        """Return whether every match guard is valid."""
        guards = tuple(_patterns._match_pattern_guards(patterns, subject_types))
        for guard, subject_type in guards:
            diagnostics_before = len(self.diagnostics)
            guard_input = AnalysisBranch(
                stack=T.TypeStack((subject_type,)),
                variables=BranchVariables(),
                input_mode=InputMode.TOP_LEVEL,
            )
            outputs = self.analyse_scoped_block(BranchSet((guard_input,)), guard)
            terminal, outputs = _utils._split_terminal_branches(outputs)
            if not outputs:
                if terminal:
                    continue
                if len(self.diagnostics) == diagnostics_before:
                    self._diagnose("match guard must be a boolean value", node)
                return False
            outputs = self.require_stack_top_assignable(
                outputs,
                expected=Boolean,
                location=node.location,
                message="match guard must be a boolean value",
                code="match-guard-type",
            )
            if not outputs or any(output.failed for output in outputs):
                self._diagnose("match guard must be a boolean value", node)
                return False
        return True

    def _match_patterns_are_valid(
        self,
        subject_types: tuple[T.Type, ...],
        node: MatchNode,
    ) -> bool:
        """Validate pattern structure that must agree with every runtime path."""
        for case in node.cases:
            for pattern, subject_type in zip(
                case.patterns,
                subject_types,
                strict=True,
            ):
                for pattern_type in _core._match_pattern_types(pattern):
                    if not self._validate_data_tags(
                        ((pattern_type,),),
                        pattern,
                        allow_variants=True,
                        require_declared=True,
                    ):
                        return False
                uncheckable = _patterns._uncheckable_runtime_pattern_type(pattern)
                if uncheckable is not None:
                    invalid_pattern, invalid_type = uncheckable
                    self._diagnose(
                        f"{T.show(invalid_type)} cannot be checked at runtime",
                        invalid_pattern,
                    )
                    return False
                mismatch = _patterns._or_pattern_binding_mismatch(pattern)
                if mismatch:
                    names = ", ".join(str(name) for name in mismatch)
                    self._diagnose(
                        "every alternative in an or-pattern must bind the same "
                        f"names; missing from some alternatives: {names}",
                        pattern,
                    )
                    return False
                invalid = _patterns._invalid_destructure_arity(
                    pattern,
                    subject_type,
                    self.env,
                )
                if invalid is not None:
                    invalid_pattern, name, actual, expected = invalid
                    self._diagnose(
                        f"pattern for {name} destructures {actual} fields, but "
                        f"the type declares {expected}",
                        invalid_pattern,
                    )
                    return False
        return True

