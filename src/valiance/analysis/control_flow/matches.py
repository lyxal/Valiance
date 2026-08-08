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
    ExpressionPatternNode,
    ExtractPatternNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    GuardPatternNode,
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
        """Collect groups that participate on every path through a sequence."""
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
        """Return the static type of one mandatory or optional capture group."""
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

        if not self._match_value_patterns_are_self_contained(node, branch):
            return BranchSet()

        analysed_guards: list[tuple[tuple[ASTNode | TypedNode, ...], ...]] = []
        case_guard_inputs: list[tuple[tuple[T.Type, ...], ...]] = []
        case_pattern_arities: list[tuple[int, ...]] = []
        for case in node.cases:
            subject_hints = tuple(
                _patterns._match_subject_pattern_type(branch, node, index, self.env)
                for index in range(len(case.patterns))
            )
            guard_result = self._analyse_match_guards(case.patterns, subject_hints, node)
            if guard_result is None:
                return BranchSet()
            typed_guards, guard_inputs = guard_result
            analysed_guards.append(typed_guards)
            case_guard_inputs.append(guard_inputs)
            guard_iter = iter(guard_inputs)
            pattern_arities: list[int] = []
            for pattern in case.patterns:
                try:
                    pattern_arities.append(self._match_pattern_consumption(pattern, guard_iter))
                except ValueError as exc:
                    self._diagnose(str(exc), pattern)
                    return BranchSet()
            case_pattern_arities.append(tuple(pattern_arities))

        # Arity is measured uniformly for every case form. Literal/value,
        # typed, structural, extracted, and wildcard patterns each consume one
        # match subject per top-level pattern. A guard consumes the number of
        # inputs inferred for its isolated guard function. Extraction changes
        # what the selected body can cycle over, never how many subjects the
        # case consumes.
        consumptions = {sum(arities) for arities in case_pattern_arities}
        arity = next(iter(consumptions)) if len(consumptions) == 1 else None
        if arity is None:
            self._diagnose("match cases must consume the same number of inputs", node)
            return BranchSet()
        if arity == 0:
            self._diagnose("match requires at least one pattern per case", node)
            return BranchSet()

        coordinate_types: list[list[T.Type]] = [[] for _ in range(arity)]
        for case, pattern_arities, guard_inputs in zip(
            node.cases, case_pattern_arities, case_guard_inputs, strict=True
        ):
            guard_iter = iter(guard_inputs)
            coordinate = 0
            for pattern, consumed in zip(case.patterns, pattern_arities, strict=True):
                if isinstance(pattern, GuardPatternNode):
                    inputs = next(guard_iter)
                    for offset, typ in enumerate(inputs):
                        coordinate_types[coordinate + offset].append(typ)
                else:
                    inferred = _patterns._pattern_subject_type(pattern, self.env.context)
                    if inferred is not None:
                        coordinate_types[coordinate].append(inferred)
                coordinate += consumed
        subject_params = tuple(
            reversed(tuple(
                self._merge_match_coordinate_types(types)
                if types else _functions._anonymous_type_var(branch, index + 1)
                for index, types in enumerate(coordinate_types)
            ))
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
        if not self._match_patterns_are_valid(subject_types, node, tuple(case_pattern_arities)):
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
        all_simple_coordinates = all(
            all(count == 1 for count in arities) for arities in case_pattern_arities
        )
        previous_patterns: list[tuple[MatchPatternNode, ...]] = []
        for case_index, case in enumerate(node.cases):
            pattern_arities = case_pattern_arities[case_index]
            simple_coordinates = all_simple_coordinates and all(count == 1 for count in pattern_arities)
            case_variables = (
                _patterns._match_case_variables(
                    body_input.variables, case.patterns, subject_types, self.env
                )
                if simple_coordinates
                else body_input.variables
            )
            exposed_types: list[T.Type] = []
            coordinate = 0
            for pattern, consumed in zip(case.patterns, pattern_arities, strict=True):
                segment = subject_types[coordinate:coordinate + consumed]
                coordinate += consumed
                try:
                    exposed = self._pattern_exposed_types(pattern, segment)
                except (ValueError, re.error) as exc:
                    self._diagnose(str(exc), pattern)
                    return BranchSet()
                exposed_types.extend(exposed)
                for name, typ in self._extract_named_regex_captures(pattern):
                    case_variables = case_variables.with_block_local(name, typ)
            if subject_variables and simple_coordinates:
                case_variables = _patterns._refine_match_subject_variables(
                    case_variables, subject_variables, case.patterns, subject_types,
                    tuple(previous_patterns), self.env,
                )
            retained_subject_types = tuple(exposed_types)
            case_input = body_input.with_variables(case_variables).with_stack(T.TypeStack())
            case_input = replace(
                case_input, input_mode=InputMode.CYCLE_EXPLICIT_PARAMS,
                cycle_params=retained_subject_types, cycle_index=0,
                cycle_stack_remaining=0, cycle_from_top=True,
            )
            case_outputs = self.analyse_scoped_block(BranchSet((case_input,)), case.body)
            typed_case_bodies.append(
                _patterns._typed_block(case_outputs, len(case_input.typed_body), case.body)
            )
            for output in case_outputs:
                candidate = _patterns._match_case_output(output, body_input, node)
                joined = _patterns._join_match_output(
                    original=branch, baseline=body_input, joined=joined,
                    candidate=candidate, ctx=self.env.context,
                )
                if joined is None:
                    self._diagnose("match cases inferred different inputs", node)
                    return BranchSet()
            previous_patterns.append(case.patterns)

        if joined is None:
            return BranchSet()
        return BranchSet((joined.emit(TypedMatchNode(
            node, _calls._returns_result_type(joined.stack.items),
            case_bodies=tuple(typed_case_bodies),
            case_guards=tuple(analysed_guards),
            case_pattern_arities=tuple(case_pattern_arities),
            case_guard_arities=tuple(
                tuple(len(inputs) for inputs in guards) for guards in case_guard_inputs
            ),
        )),))

    def _match_value_patterns_are_self_contained(
        self,
        node: MatchNode,
        branch: AnalysisBranch,
    ) -> bool:
        """Reject value-pattern expressions that require external stack inputs."""
        for case in node.cases:
            for pattern in case.patterns:
                for expression_pattern in self._match_expression_patterns(pattern):
                    diagnostics_before = len(self.diagnostics)
                    expression_input = AnalysisBranch(
                        variables=branch.variables,
                        input_mode=InputMode.INFER_INPUTS,
                    )
                    outputs = self.analyse_scoped_block(
                        BranchSet((expression_input,)),
                        expression_pattern.expression,
                    )
                    if len(self.diagnostics) != diagnostics_before:
                        return False
                    if not outputs:
                        self._diagnose(
                            "value match expression must produce exactly one value",
                            expression_pattern,
                        )
                        return False
                    if any(output.inputs for output in outputs):
                        self._diagnose(
                            "value match expressions are stack isolated and cannot consume inputs",
                            expression_pattern,
                        )
                        return False
                    if any(len(output.stack) != 1 for output in outputs):
                        self._diagnose(
                            "value match expression must produce exactly one value",
                            expression_pattern,
                        )
                        return False
        return True

    def _match_expression_patterns(
        self,
        pattern: MatchPatternNode,
    ) -> Iterator[ExpressionPatternNode]:
        """Yield expression-valued patterns nested inside one case pattern."""
        if isinstance(pattern, ExpressionPatternNode):
            yield pattern
            return
        if isinstance(pattern, ExtractPatternNode):
            yield from self._match_expression_patterns(pattern.pattern)
            return
        if isinstance(pattern, BindingPatternNode):
            yield from self._match_expression_patterns(pattern.pattern)
            return
        if isinstance(pattern, OrPatternNode):
            for option in pattern.options:
                yield from self._match_expression_patterns(option)
            return
        if isinstance(pattern, ListPatternNode):
            for item in pattern.items:
                yield from self._match_expression_patterns(item)
            return
        if isinstance(pattern, TypePatternNode):
            for field in pattern.fields:
                yield from self._match_expression_patterns(field)

    def _pattern_exposed_types(
        self,
        pattern: MatchPatternNode,
        subject_types: tuple[T.Type, ...],
    ) -> tuple[T.Type, ...]:
        """Return the common body-input interface of a successful pattern."""
        subject_type = subject_types[0]
        if isinstance(pattern, ExtractPatternNode):
            inner = pattern.pattern
            regex = _extract_regex(inner)
            if regex is not None:
                anonymous, named = _regex_capture_interface(regex)
                captures = (*anonymous, *(typ for _name, typ in named))
            else:
                captures = _patterns._pattern_capture_types(
                    inner, subject_type, self.env
                )
            if not captures and not _patterns._pattern_has_proper_named_capture(inner):
                raise ValueError(
                    "extract requires at least one nested hole, nested binding, "
                    "rest capture, or regular-expression capture group"
                )
            return tuple(captures)
        if isinstance(pattern, OrPatternNode):
            options = tuple(
                self._pattern_exposed_types(option, subject_types)
                for option in pattern.options
            )
            counts = {len(option) for option in options}
            if len(counts) != 1:
                raise ValueError(
                    "or-pattern alternatives expose different numbers of matched values"
                )
            merged = list(options[0]) if options else []
            for option in options[1:]:
                merged = [
                    T.merge_types(left, right, self.env.context)
                    for left, right in zip(merged, option, strict=True)
                ]
            return tuple(merged)
        if isinstance(pattern, GuardPatternNode):
            return subject_types
        return (
            _patterns._successful_pattern_subject_type(
                pattern, subject_type, self.env
            ),
        )

    def _extract_named_regex_captures(
        self,
        pattern: MatchPatternNode,
    ) -> Iterator[tuple[Symbol, T.Type]]:
        """Yield named regex captures introduced by extracting alternatives."""
        if isinstance(pattern, ExtractPatternNode):
            regex = _extract_regex(pattern.pattern)
            if regex is not None:
                _anonymous, named = _regex_capture_interface(regex)
                yield from named
            return
        if isinstance(pattern, OrPatternNode):
            for option in pattern.options:
                yield from self._extract_named_regex_captures(option)

    def _merge_match_coordinate_types(self, types: list[T.Type]) -> T.Type:
        """Merge subject hints contributed for one match input coordinate."""
        result = types[0]
        for typ in types[1:]:
            result = T.merge_types(result, typ, self.env.context)
        return result

    def _match_pattern_consumption(
        self, pattern: MatchPatternNode, guard_inputs: Iterator[tuple[T.Type, ...]]
    ) -> int:
        """Return the number of match subjects consumed by one pattern."""
        if isinstance(pattern, ExtractPatternNode):
            return self._match_pattern_consumption(pattern.pattern, guard_inputs)
        if isinstance(pattern, GuardPatternNode):
            return len(next(guard_inputs))
        if isinstance(pattern, OrPatternNode):
            option_counts = {self._match_pattern_consumption(option, guard_inputs) for option in pattern.options}
            if len(option_counts) != 1:
                raise ValueError("every alternative in an or-pattern must consume the same number of inputs")
            return next(iter(option_counts))
        nested_guards = tuple(_patterns._pattern_guards(pattern, T.V("_match_guard")))
        for _condition, _subject in nested_guards:
            inputs = next(guard_inputs)
            if len(inputs) != 1:
                raise ValueError("nested match guards must consume exactly one input")
        return 1

    def _analyse_match_guards(
        self,
        patterns: tuple[MatchPatternNode, ...],
        subject_hints: tuple[T.Type, ...],
        node: MatchNode,
    ) -> tuple[
        tuple[tuple[ASTNode | TypedNode, ...], ...],
        tuple[tuple[T.Type, ...], ...],
    ] | None:
        """Analyse guards as self-contained stack functions and record arity."""
        guards = tuple(_patterns._match_pattern_guards(patterns, subject_hints))
        typed_guards: list[tuple[ASTNode | TypedNode, ...]] = []
        guard_inputs: list[tuple[T.Type, ...]] = []
        for guard, subject_type in guards:
            diagnostics_before = len(self.diagnostics)
            guard_input = AnalysisBranch(
                stack=T.TypeStack((subject_type,)),
                variables=BranchVariables(),
                input_mode=InputMode.INFER_INPUTS,
            )
            outputs = self.analyse_scoped_block(BranchSet((guard_input,)), guard)
            terminal, outputs = _utils._split_terminal_branches(outputs)
            if not outputs:
                if terminal:
                    typed_guards.append(_patterns._typed_block(
                        terminal, len(guard_input.typed_body), guard
                    ))
                    guard_inputs.append((subject_type,))
                    continue
                if len(self.diagnostics) == diagnostics_before:
                    self._diagnose("match guard must be a boolean value", node)
                return None
            outputs = self.require_stack_top_assignable(
                outputs, expected=Boolean, location=node.location,
                message="match guard must be a boolean value", code="match-guard-type",
            )
            if not outputs or any(output.failed for output in outputs):
                self._diagnose("match guard must be a boolean value", node)
                return None
            first = next(iter(outputs))
            input_counts = {len(output.inputs) for output in outputs}
            if len(input_counts) != 1:
                self._diagnose("match guard has no single input arity", node)
                return None
            inferred_inputs = list(first.inputs)
            for output in tuple(outputs)[1:]:
                inferred_inputs = [
                    T.merge_types(left, right, self.env.context)
                    for left, right in zip(inferred_inputs, output.inputs, strict=True)
                ]
            typed_guards.append(_patterns._typed_block(
                outputs, len(guard_input.typed_body), guard
            ))
            guard_inputs.append((*inferred_inputs, subject_type))
        return tuple(typed_guards), tuple(guard_inputs)

    def _match_patterns_are_valid(
        self,
        subject_types: tuple[T.Type, ...],
        node: MatchNode,
        case_pattern_arities: tuple[tuple[int, ...], ...] | None = None,
    ) -> bool:
        """Validate pattern structure that must agree with every runtime path."""
        for case_index, case in enumerate(node.cases):
            arities = (
                case_pattern_arities[case_index]
                if case_pattern_arities is not None
                else tuple(1 for _ in case.patterns)
            )
            coordinate = 0
            for pattern, consumed in zip(case.patterns, arities, strict=True):
                subject_type = subject_types[coordinate]
                coordinate += consumed
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

