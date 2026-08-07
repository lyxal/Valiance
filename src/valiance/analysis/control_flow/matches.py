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
import re

try:
    from re import _parser as _regex_parser
except ImportError:  # pragma: no cover - alternate host regex implementation
    _regex_parser = None
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
    LiteralPatternNode,
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
    WildcardPatternNode,
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
from valiance.asts.nodes import GetVariableNode, ObjectFieldNode, StringLiteralNode
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
from .. import analyser as _core
from ..state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)

from ..calls.models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)






def _extract_regex(pattern: MatchPatternNode) -> re.Pattern[str] | None:
    """Compile a literal-string extracting pattern as a regular expression."""
    if not isinstance(pattern, LiteralPatternNode) or not isinstance(pattern.value, StringLiteralNode):
        return None
    source = pattern.value.value
    # Accept the common (?<name>...) spelling and lower it to Python's form.
    source = re.sub(r"\(\?<([A-Za-z_]\w*)>", r"(?P<\1>", source)
    return re.compile(source)


def _mandatory_regex_groups(source: str) -> frozenset[int]:
    """Return capture groups guaranteed to participate in every successful match."""
    if _regex_parser is None:
        return frozenset()
    parsed = _regex_parser.parse(source, 0)

    def guaranteed(sequence: object) -> set[int]:
        result: set[int] = set()
        for operation, argument in sequence:
            name = str(operation)
            if name == "SUBPATTERN":
                group, _add_flags, _del_flags, child = argument
                if group is not None:
                    result.add(group)
                result.update(guaranteed(child))
            elif name == "BRANCH":
                _none, branches = argument
                branch_sets = [guaranteed(branch) for branch in branches]
                if branch_sets:
                    result.update(set.intersection(*branch_sets))
            elif name in {"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"}:
                minimum, _maximum, child = argument
                if minimum > 0:
                    result.update(guaranteed(child))
        return result

    return frozenset(guaranteed(parsed))


def _regex_capture_interface(
    compiled: re.Pattern[str],
) -> tuple[tuple[T.Type, ...], tuple[tuple[Symbol, T.Type], ...]]:
    """Return anonymous stack captures and named local captures for a regex."""
    named_by_index = {index: name for name, index in compiled.groupindex.items()}
    mandatory = _mandatory_regex_groups(compiled.pattern)

    def capture_type(index: int) -> T.Type:
        return T.String if index in mandatory else T.optional(T.String)

    anonymous = tuple(
        capture_type(index)
        for index in range(1, compiled.groups + 1)
        if index not in named_by_index
    )
    named = tuple(
        (Symbol(name), capture_type(index))
        for name, index in sorted(compiled.groupindex.items(), key=lambda item: item[1])
    )
    return anonymous, named


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
        typed_case_guards: list[tuple[tuple[ASTNode | TypedNode, ...], ...]] = []
        subject_variables = _patterns._match_subject_variables(branch, arity)
        previous_patterns: list[tuple[MatchPatternNode, ...]] = []
        for case in node.cases:
            case_variables = _patterns._match_case_variables(
                body_input.variables,
                case.patterns,
                subject_types,
                self.env,
            )
            extracted_types: tuple[T.Type, ...] = ()
            if case.extract:
                captures: list[T.Type] = []
                proper_capture = False
                for pattern, subject_type in zip(case.patterns, subject_types, strict=True):
                    try:
                        regex = _extract_regex(pattern)
                    except re.error as exc:
                        self._diagnose(f"invalid extracting regular expression: {exc}", pattern)
                        return BranchSet()
                    if regex is not None:
                        anonymous, named = _regex_capture_interface(regex)
                        captures.extend(anonymous)
                        for name, typ in named:
                            case_variables = case_variables.with_block_local(name, typ)
                        proper_capture = proper_capture or bool(anonymous or named)
                        continue
                    try:
                        pattern_captures = _patterns._pattern_capture_types(pattern, subject_type, self.env)
                    except ValueError as exc:
                        self._diagnose(str(exc), pattern)
                        return BranchSet()
                    captures.extend(pattern_captures)
                    proper_capture = proper_capture or bool(pattern_captures) or _patterns._pattern_has_proper_named_capture(pattern)
                if not proper_capture:
                    self._diagnose(
                        "extract requires at least one nested hole, nested binding, rest capture, or regular-expression capture group",
                        case,
                    )
                    return BranchSet()
                extracted_types = tuple(captures)
            if subject_variables:
                case_variables = _patterns._refine_match_subject_variables(
                    case_variables,
                    subject_variables,
                    case.patterns,
                    subject_types,
                    tuple(previous_patterns),
                    self.env,
                )
            refined_subject_types = tuple(
                _patterns._match_case_subject_type(
                    pattern,
                    subject_type,
                    tuple(previous[index] for previous in previous_patterns),
                    self.env,
                )
                or subject_type
                for index, (pattern, subject_type) in enumerate(
                    zip(case.patterns, subject_types, strict=True)
                )
            )
            retained_subject_types = (
                extracted_types
                if case.extract
                else (
                    refined_subject_types
                    if is_catch_all_match_case(case.patterns)
                    else tuple(
                        typ
                        for pattern, typ in zip(case.patterns, refined_subject_types, strict=True)
                        if not isinstance(pattern, WildcardPatternNode)
                    )
                )
            )
            # Matched subjects are conceptual case inputs, not physical stack
            # values. A case therefore begins with the subjects removed, while
            # ordinary underflow can cycle retained coordinates on demand.
            # Case bodies operate on retained match subjects, not on values below
            # the consumed subjects. Keep the outer stack isolated while analysing
            # the case, then restore it when committing the case output.
            case_input = body_input.with_variables(case_variables).with_stack(
                T.TypeStack()
            )
            case_input = replace(
                case_input,
                input_mode=InputMode.CYCLE_EXPLICIT_PARAMS,
                cycle_params=retained_subject_types,
                cycle_index=0,
                cycle_stack_remaining=0,
                cycle_from_top=True,
            )
            typed_guards = self._analyse_match_guards(
                subject_types, case.patterns, node
            )
            if typed_guards is None:
                return BranchSet()
            typed_case_guards.append(typed_guards)
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
                        case_guards=tuple(typed_case_guards),
                    )
                ),
            )
        )

    def _analyse_match_guards(
        self,
        subject_types: tuple[T.Type, ...],
        patterns: tuple[MatchPatternNode, ...],
        node: MatchNode,
    ) -> tuple[tuple[ASTNode | TypedNode, ...], ...] | None:
        """Validate guards and retain their analysed nodes in traversal order."""
        guards = tuple(_patterns._match_pattern_guards(patterns, subject_types))
        typed_guards: list[tuple[ASTNode | TypedNode, ...]] = []
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
                    typed_guards.append(
                        _patterns._typed_block(
                            terminal,
                            len(guard_input.typed_body),
                            guard,
                        )
                    )
                    continue
                if len(self.diagnostics) == diagnostics_before:
                    self._diagnose("match guard must be a boolean value", node)
                return None
            outputs = self.require_stack_top_assignable(
                outputs,
                expected=Boolean,
                location=node.location,
                message="match guard must be a boolean value",
                code="match-guard-type",
            )
            if not outputs or any(output.failed for output in outputs):
                self._diagnose("match guard must be a boolean value", node)
                return None
            typed_guards.append(
                _patterns._typed_block(
                    outputs,
                    len(guard_input.typed_body),
                    guard,
                )
            )
        return tuple(typed_guards)

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

