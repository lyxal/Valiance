"""Branch-based static analysis, type inference, and overload resolution.

The public analyser API and branch model live here. Large implementation
families are split into sibling ``_analyser_*`` modules and intentionally remain
private implementation details.
"""

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

from .calls import candidates as _calls
from .calls import callable_values as _functions
from .control_flow import patterns as _patterns
from . import _analyser_utils as _utils
from .state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
from .declarations import DeclarationAnalyser
from .calls import CallAnalyser
from .control_flow import ControlFlowAnalyser
from .contracts import ContractAnalyser
from .calls.models import (
    CallCandidate, ElementArguments, ElementCallPreparation, FunctionAnalysis,
    ListItemAnalysis, ModifierArgumentAnalysis, OverloadApplication,
)


















NodeHandler = Callable[
    ["Analyser", ASTNode, AnalysisBranch],
    BranchSet,
]

_NODE_HANDLERS: dict[type[ASTNode], NodeHandler] = {}


_INTERNAL_NODE_TYPES: tuple[type[ASTNode], ...] = (
    AnnotationNode,
    ObjectFieldNode,
    TraitRequirementNode,
    VariantMemberNode,
    EnumMemberNode,
    TryHandlerNode,
    MatchCaseNode,
    MatchPatternNode,
)


def register(node_type: type[ASTNode]) -> Callable[[NodeHandler], NodeHandler]:
    """Create a decorator that registers an analyser handler for one AST type."""
    def decorate(handler: NodeHandler) -> NodeHandler:
        """Store the decorated analyser handler in the node-handler registry."""
        if node_type in _NODE_HANDLERS:
            raise RuntimeError(f"duplicate analyser handler for {node_type.__name__}")

        _NODE_HANDLERS[node_type] = handler
        return handler

    return decorate
















@dataclass
class _AnalysisPrelude:
    """Runtime declarations imported during one analysis session."""

    namespace_seed: str
    nodes: list[TypedNode] = field(default_factory=list)
    bindings: list[tuple[TypedNode, Symbol, Symbol]] = field(default_factory=list)

    def add(self, node: TypedNode) -> None:
        """Add one imported runtime declaration exactly once."""
        if node not in self.nodes:
            self.nodes.append(node)

    def add_declaration(self, node: TypedNode, source_name: Symbol) -> Symbol:
        """Hoist one declaration and return its hidden runtime binding."""
        for existing, existing_source, runtime_name in self.bindings:
            if existing == node and existing_source == source_name:
                return runtime_name
        index = len(self.bindings)
        runtime_name = Symbol(
            source_name.text,
            (f"__valiance_import_{self.namespace_seed}_{index}",),
        )
        self.nodes.append(_with_import_runtime_name(node, runtime_name))
        self.bindings.append((node, source_name, runtime_name))
        return runtime_name


def _prelude_seed(source_file: Path | None) -> str:
    """Return a stable internal namespace seed for imported declarations."""
    identity = "<inline>" if source_file is None else str(source_file.resolve())
    return sha1(identity.encode("utf-8")).hexdigest()[:12]


def _with_import_runtime_name(
    node: TypedNode,
    runtime_name: Symbol,
) -> TypedNode:
    """Attach a hidden runtime binding without changing source-level names."""
    if isinstance(node, TypedFunctionNode):
        return TypedImportedFunctionNode(
            node.node,
            node.typ,
            node.overloads,
            node.dispatch_plan,
            runtime_name,
        )
    if isinstance(node.node, ObjectNode):
        return TypedImportedObjectNode(node.node, node.typ, runtime_name)
    return node


def _nested_types(typ: T.Type) -> Iterator[T.Type]:
    """Yield a normalized type and every nested type it contains."""
    typ = T.normalize(typ)
    yield typ
    if isinstance(typ, T.TaggedType):
        yield from _nested_types(typ.inner)
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            yield from _nested_types(arg)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            yield from _nested_types(item)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            yield from _nested_types(item)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            yield from _nested_types(item.typ)
        return
    if isinstance(typ, T.RowType):
        yield from _nested_types(typ.base)
        for field in typ.fields:
            yield from _nested_types(field.typ)
        return
    if isinstance(typ, T.CollectionType):
        yield from _nested_types(typ.base)
        return
    if isinstance(typ, T.FunctionType):
        for item in (*(typ.params or ()), *(typ.returns or ())):
            yield from _nested_types(item)
        for element_tag in typ.element_tags:
            for arg in element_tag.args:
                yield from _nested_types(arg)
        return
    if isinstance(typ, (T.ExactType, T.AtomicType)):
        yield from _nested_types(typ.inner)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for item in (
                *requirement.overload.params,
                *requirement.overload.returns,
            ):
                yield from _nested_types(item)
        return
    if isinstance(typ, T.OverloadSetType):
        for overload in typ.overloads:
            for item in (*overload.params, *overload.returns):
                yield from _nested_types(item)


def _all_data_tags(typ: T.Type) -> Iterator[T.DataTag]:
    """Yield every data-tag requirement nested inside one type."""
    for nested in _nested_types(typ):
        if isinstance(nested, T.TaggedType):
            yield from nested.tags


def _match_pattern_types(pattern: MatchPatternNode) -> Iterator[T.Type]:
    """Yield every explicit runtime type nested inside a match pattern."""
    if isinstance(pattern, TypePatternNode):
        if pattern.typ is not None:
            yield pattern.typ
        for field in pattern.fields:
            yield from _match_pattern_types(field)
        return
    if isinstance(pattern, BindingPatternNode):
        yield from _match_pattern_types(pattern.pattern)
        return
    if isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            yield from _match_pattern_types(option)
        return
    if isinstance(pattern, ListPatternNode):
        for item in pattern.items:
            yield from _match_pattern_types(item)


class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""

    def __init__(
        self,
        env: T.Environment | None = None,
        *,
        module_loader: ModuleLoader | None = None,
        source_file: Path | None = None,
        lint_registry: LintRegistry | None = None,
        _prelude: _AnalysisPrelude | None = None,
    ):
        """Initialize an analysis session with its environment and module context."""
        self.env = env if env is not None else default_environment().child_scope()
        self.module_loader = module_loader or ModuleLoader()
        self.source_file = source_file
        self.lint_registry = lint_registry or DEFAULT_LINT_REGISTRY
        self._prelude = _prelude or _AnalysisPrelude(_prelude_seed(source_file))
        self._owns_prelude = _prelude is None
        self.diagnostics: list[str] = []
        self.warnings: list[str] = []
        self.lints: list[str] = []
        self.lint_findings: list[LintFinding] = []
        self.project_lints_enabled = True
        self.project_disabled_lint_codes: frozenset[str] = frozenset()
        self._load_project_lint_settings()
        self.disabled_lint_codes: set[str] | None = set()
        self.attempted_lint_codes: set[str] = set()
        self.file_lint_suppressions: dict[str, ASTNode] = {}
        self._friendly_owners: tuple[Symbol, ...] = ()
        self._reported_data_element_disjoints: set[
            tuple[int, Symbol, Symbol]
        ] = set()
        self.declarations = DeclarationAnalyser(self)
        self.calls = CallAnalyser(self)
        self.control_flow = ControlFlowAnalyser(self)
        self.contracts = ContractAnalyser(self)

    def __getattr__(self, name: str):
        """Delegate declaration operations to their owning subsystem."""
        declarations = self.__dict__.get("declarations")
        if declarations is not None and declarations.provides(name):
            return getattr(declarations, name)
        calls = self.__dict__.get("calls")
        if calls is not None and calls.provides(name):
            return getattr(calls, name)
        control_flow = self.__dict__.get("control_flow")
        if control_flow is not None and control_flow.provides(name):
            return getattr(control_flow, name)
        contracts = self.__dict__.get("contracts")
        if contracts is not None and contracts.provides(name):
            return getattr(contracts, name)
        raise AttributeError(name)

    def _load_project_lint_settings(self) -> None:
        """Load project-wide lint policy from the nearest ``valiance.toml``."""
        from valiance.modules_system.packages import find_project_root, load_manifest

        start = self.source_file or Path.cwd()
        root = find_project_root(start)
        if root is None:
            return
        settings = load_manifest(root).lints
        self.project_lints_enabled = settings.enabled
        self.project_disabled_lint_codes = frozenset(settings.disabled)

    def analyse(self, program: list[ASTNode]) -> list[TypedNode]:
        """Analyse a top-level sequence into typed nodes."""
        self.disabled_lint_codes = (
            set(self.project_disabled_lint_codes)
            if self.project_lints_enabled
            else None
        )
        for node in program:
            if isinstance(node, FileLintSuppressionNode):
                if node.codes:
                    self.disabled_lint_codes.update(node.codes)
                else:
                    self.disabled_lint_codes = None
                    break
        self.attempted_lint_codes.clear()
        self.file_lint_suppressions.clear()
        if self._owns_prelude:
            self._prelude.nodes.clear()
            self._prelude.bindings.clear()
        initial = BranchSet((AnalysisBranch(input_mode=InputMode.TOP_LEVEL),))
        final = self.analyse_block(initial, tuple(program))
        for code, directive in self.file_lint_suppressions.items():
            if code not in self.attempted_lint_codes:
                self._record_lint_finding(
                    LintFinding(
                        code="unused-lint-suppression",
                        message=f"file lint suppression for '{code}' is unused",
                        location=directive.location,
                        node=directive,
                    )
                )
        if len(final) != 1:
            return [TypedNode(node, None) for node in program]
        return [*self._prelude.nodes, *next(iter(final)).typed_body]

    @property
    def runtime_prelude(self) -> tuple[TypedNode, ...]:
        """Return declarations hoisted from imports for one-time initialization."""
        return tuple(self._prelude.nodes)

    def analyse_block(
        self,
        initial: BranchSet,
        nodes: tuple[ASTNode, ...],
    ) -> BranchSet:
        """Analyse a block as a branch-set transformation."""
        self._extend_lint_findings(
            self.lint_registry.check_block(
                BlockLintContext(nodes=nodes, env=self.env)
            )
        )
        current = initial

        for node in nodes:
            current = self.analyse_node(current, node)

            if not current:
                break

        return current

    def analyse_node(self, branches: BranchSet, node: ASTNode) -> BranchSet:
        """Analyse one node from a branch set."""
        next_branches: list[AnalysisBranch] = []
        for branch in branches:
            if branch.failed or branch.break_type is not None or branch.terminal:
                next_branches.append(branch)
                continue
            next_branches.extend(self._analyse_node_from_branch(branch, node))
        return BranchSet.collect(next_branches)

    def analyse_from(
        self,
        branch: AnalysisBranch,
        body: tuple[ASTNode, ...],
    ) -> BranchSet:
        """Analyse a nested lexical block from one existing branch."""
        return self.analyse_scoped_block(BranchSet((branch,)), body)

    def analyse_scoped_block(
        self,
        initial: BranchSet,
        nodes: tuple[ASTNode, ...],
    ) -> BranchSet:
        """Analyse a nested block with declarations local to that block."""
        outer = self.env
        self.env = outer.lexical_child_scope()
        try:
            return self.analyse_block(initial, nodes)
        finally:
            self.env = outer

    def _child_analyser(self, env: T.Environment) -> Analyser:
        """Create a nested analyser sharing module resolution and import prelude."""
        child = Analyser(
            env,
            module_loader=self.module_loader,
            source_file=self.source_file,
            lint_registry=self.lint_registry,
            _prelude=self._prelude,
        )
        child._friendly_owners = self._friendly_owners
        child.project_lints_enabled = self.project_lints_enabled
        child.project_disabled_lint_codes = self.project_disabled_lint_codes
        child.disabled_lint_codes = (
            None
            if self.disabled_lint_codes is None
            else set(self.disabled_lint_codes)
        )
        return child

    def require_stack_top_assignable(
        self,
        branches: BranchSet,
        *,
        expected: T.Type,
        location: SourceLocation | None,
        message: str,
        code: str = "type-mismatch",
    ) -> BranchSet:
        """Validate and consume an assignable top value from every branch."""
        return BranchSet.collect(
            self.consume_top(
                branch,
                expected=expected,
                message=message,
                location=location,
                code=code,
            )
            for branch in branches
        )

    def consume_top(
        self,
        branch: AnalysisBranch,
        *,
        expected: T.Type,
        message: str,
        location: SourceLocation | None,
        code: str = "type-mismatch",
    ) -> AnalysisBranch:
        """Validate and remove one top stack type from a branch."""
        actual = branch.top

        if actual is None:
            return branch.error(
                message,
                location,
                code="stack-underflow",
                expected=expected,
            )

        if _utils._is_never(actual) or not T.assignable(
            actual,
            expected,
            self.env.context,
        ):
            return branch.error(
                message,
                location,
                code=code,
                expected=expected,
                actual=actual,
            )

        return branch.pop()

    def analyse_function(self, node: FunctionNode) -> T.Type | None:
        """Infer the stack-effect type of a function literal."""
        result = self.analyse_function_details(node)
        return None if result is None else result.typ

    def analyse_function_details(self, node: FunctionNode) -> FunctionAnalysis | None:
        """Infer a function literal outside an existing branch."""
        outer = AnalysisBranch(input_mode=InputMode.TOP_LEVEL)
        result = self._analyse_function_literal(outer, node)
        if result is None:
            return None
        return result[0]

    def _analyse_node_from_branch(
        self,
        branch: AnalysisBranch,
        node: ASTNode,
    ) -> BranchSet:
        """Analyse node from branch during static analysis."""
        handler = _NODE_HANDLERS.get(type(node))

        if handler is None:
            if isinstance(node, _INTERNAL_NODE_TYPES):
                return BranchSet(
                    (
                        branch.error(
                            f"{type(node).__name__} is an internal AST node and "
                            "cannot be analysed as a standalone expression",
                            node.location,
                            code="internal-node",
                        ),
                    )
                )
            return BranchSet(
                (
                    branch.error(
                        f"Analysis is not implemented for {type(node).__name__}",
                        node.location,
                        code="unsupported-node",
                    ),
                )
            )

        outputs = handler(self, node, branch)
        self._extend_lint_findings(
            self.lint_registry.check_node(
                NodeLintContext(
                    node=node,
                    branch=branch,
                    outputs=outputs,
                    env=self.env,
                )
            )
        )
        self._observe_element_effects(branch, outputs)
        return outputs

    def _observe_element_effects(
        self,
        branch: AnalysisBranch,
        outputs: BranchSet,
    ) -> None:
        """Collect effects from executed calls, including nested expressions."""
        start = len(branch.typed_body)
        for output in outputs:
            if len(output.typed_body) <= start:
                continue
            for typed_node in output.typed_body[start:]:
                applied: T.AppliedOverload | None = None
                if isinstance(typed_node, (TypedElementNode, TypedCallNode)):
                    applied = typed_node.overload
                elif isinstance(typed_node, TypedTagApplicationNode):
                    applied = typed_node.validator
                if applied is None:
                    continue
                positives = tuple(
                    tag for tag in applied.element_tags if not tag.absent
                )
                self._validate_element_tag_disjoints(
                    positives,
                    typed_node.node,
                )
                self._validate_data_element_tag_disjoints(
                    applied.params,
                    positives,
                    typed_node.node,
                )



    @register(DefineNode)
    def _define(
        self,
        node: DefineNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Delegate function declaration registration to its owning subsystem."""
        return self.declarations._define(node, branch)

























    @register(ElementNode)
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



















    @register(MatchNode)
    def _match(
        self,
        node: MatchNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Delegate match analysis to the control-flow subsystem."""
        return self.control_flow._match(node, branch)

    @register(TryNode)
    def _try(
        self,
        node: TryNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Delegate try analysis to the control-flow subsystem."""
        return self.control_flow._try(node, branch)

    def _source_field_receiver(
        self,
        branch: AnalysisBranch,
        name: Symbol,
        *,
        optional_safe: bool = False,
    ) -> tuple[T.Type, T.Type | None, AnalysisBranch] | None:
        """Source field receiver during static analysis."""
        if branch.stack:
            receiver_type = branch.stack[-1]
            popped = branch.with_stack(branch.stack.pop())
            resolver = self._safe_field_type if optional_safe else self._field_type
            field_type, refined_receiver = resolver(receiver_type, name, popped)
            if refined_receiver is not None:
                popped = popped.refine_type(receiver_type, refined_receiver)
                receiver_type = refined_receiver
            return receiver_type, field_type, popped

        if (
            branch.input_mode is InputMode.CYCLE_EXPLICIT_PARAMS
            and branch.cycle_params
        ):
            receiver_type = branch.cycle_params[
                branch.cycle_index % len(branch.cycle_params)
            ]
            popped = replace(
                branch,
                cycle_index=(branch.cycle_index + 1) % len(branch.cycle_params),
            )
            resolver = self._safe_field_type if optional_safe else self._field_type
            field_type, refined_receiver = resolver(
                receiver_type,
                name,
                popped,
            )
            if refined_receiver is not None:
                popped = popped.refine_type(receiver_type, refined_receiver)
                receiver_type = refined_receiver
            return receiver_type, field_type, popped

        if branch.input_mode is not InputMode.INFER_INPUTS:
            return None

        base = _functions._anonymous_type_var(branch, 1)
        field_type = _functions._anonymous_type_var(branch, 2)
        present_type = T.Row(base, T.Field(name, field_type))
        receiver_type = T.optional(present_type) if optional_safe else present_type
        result_type = (
            field_type
            if _patterns._optional_payload_type(field_type, strict=True) is not None
            else T.optional(field_type)
            if optional_safe
            else field_type
        )
        return (
            receiver_type,
            result_type,
            replace(branch, inputs=branch.inputs + (receiver_type,)),
        )

    def _safe_field_type(
        self,
        receiver_type: T.Type,
        name: Symbol,
        branch: AnalysisBranch,
        *,
        write: bool = False,
    ) -> tuple[T.Type | None, T.Type | None]:
        """Determine a field type through an optional present value."""
        receiver_type = T.normalize(receiver_type)
        if not write and isinstance(receiver_type, T.CollectionType):
            field_type, refined_base = self._safe_field_type(
                receiver_type.base,
                name,
                branch,
            )
            if field_type is None:
                return None, None
            refined = (
                receiver_type
                if refined_base is None
                else T.C(type(receiver_type), refined_base, receiver_type.rank)
            )
            return T.C(type(receiver_type), field_type, receiver_type.rank), refined

        payload_type = _patterns._optional_payload_type(receiver_type, strict=True)
        if payload_type is None:
            return None, None
        field_type, refined_payload = self._field_type(
            payload_type,
            name,
            branch,
            write=write,
        )
        if field_type is None:
            return None, None
        refined_receiver = (
            None if refined_payload is None else T.optional(refined_payload)
        )
        if write:
            return field_type, refined_receiver
        result_type = (
            field_type
            if _patterns._optional_payload_type(field_type, strict=True) is not None
            else T.optional(field_type)
        )
        return result_type, refined_receiver

    def _field_type(
        self,
        receiver_type: T.Type,
        name: Symbol,
        branch: AnalysisBranch,
        *,
        write: bool = False,
    ) -> tuple[T.Type | None, T.Type | None]:
        """Determine the type of field during static analysis."""
        receiver_type = T.normalize(receiver_type)
        if isinstance(receiver_type, T.RowType):
            existing = _utils._row_field_type(receiver_type, name)
            if write:
                return (existing, None) if existing is not None else (None, None)
            if existing is not None:
                return existing, None
            field_type = _functions._anonymous_type_var(branch, 1)
            return (
                field_type,
                T.Row(
                    receiver_type.base,
                    *receiver_type.fields,
                    T.Field(name, field_type),
                ),
            )

        if isinstance(receiver_type, T.VarType):
            if write:
                return None, None
            field_type = _functions._anonymous_type_var(branch, 1)
            return field_type, T.Row(receiver_type, T.Field(name, field_type))

        if isinstance(receiver_type, T.NominalType):
            definition = self.env.lookup_object(receiver_type.name)
            attribute = None if definition is None else definition.attribute(name)
            if attribute is None:
                return None, None
            if not self._can_access_attribute(
                receiver_type.name,
                attribute,
                write=write,
            ):
                return None, None
            substitution = {
                generic.text: arg
                for generic, arg in zip(
                    definition.generics,
                    receiver_type.args,
                    strict=False,
                )
            }
            return _calls._substitute_branch_type(attribute.typ, substitution), None

        if isinstance(receiver_type, T.CollectionType):
            field_type, refined_base = self._field_type(
                receiver_type.base,
                name,
                branch,
                write=write,
            )
            if field_type is None:
                return None, None
            refined = (
                receiver_type
                if refined_base is None
                else T.C(type(receiver_type), refined_base, receiver_type.rank)
            )
            return T.C(type(receiver_type), field_type, receiver_type.rank), refined

        return None, None

    def _can_access_attribute(
        self,
        receiver_name: Symbol,
        attribute: T.ObjectAttribute,
        *,
        write: bool,
    ) -> bool:
        """Return whether the analyser can access attribute."""
        access = attribute.access.text
        if access == "public":
            return True
        if access == "readable" and not write:
            return True
        return receiver_name in self._friendly_owners









    def _diagnose(self, message: str, node: ASTNode | None = None) -> None:
        """Update diagnose state during static analysis."""
        diagnostic = _utils._diagnostic_message(message, node)
        if diagnostic not in self.diagnostics:
            self.diagnostics.append(diagnostic)

    def _warn(self, message: str, node: ASTNode | None = None) -> None:
        """Update warn state during static analysis."""
        self.warnings.append(_utils._diagnostic_message(message, node))

    def _record_lint_finding(self, finding: LintFinding) -> None:
        """Append one structured finding while preserving the string API."""
        self.attempted_lint_codes.add(finding.code)
        if self.disabled_lint_codes is None or finding.code in self.disabled_lint_codes:
            return
        if finding in self.lint_findings:
            return
        self.lint_findings.append(finding)
        self.lints.append(finding.render())

    def _extend_lint_findings(self, findings: Iterable[LintFinding]) -> None:
        """Merge child-analyser lint findings without duplicating messages."""
        for finding in findings:
            self._record_lint_finding(finding)

    def clear_lints(self) -> None:
        """Clear both the legacy strings and structured lint findings."""
        self.lints.clear()
        self.lint_findings.clear()



def _resolve_pop_n_static_counts(
    node: ASTNode,
    values: dict[str, int],
) -> ASTNode:
    """Replace static pop counts throughout one deferred function body."""
    if isinstance(node, PopNNode):
        if isinstance(node.count, int):
            return node
        value = values.get(node.count.text)
        return node if value is None else replace(node, count=value)
    if isinstance(node, FunctionNode) or not is_dataclass(node):
        return node
    changes: dict[str, object] = {}
    for field_info in fields(node):
        if field_info.name == "location":
            continue
        value = getattr(node, field_info.name)
        if isinstance(value, ASTNode):
            replacement = _resolve_pop_n_static_counts(value, values)
            if replacement is not value:
                changes[field_info.name] = replacement
        elif isinstance(value, tuple):
            replaced = tuple(
                _resolve_pop_n_static_counts(item, values)
                if isinstance(item, ASTNode)
                else item
                for item in value
            )
            if replaced != value:
                changes[field_info.name] = replaced
    return replace(node, **changes) if changes else node


def analyse(
    program: list[ASTNode],
    env: T.Environment | None = None,
) -> list[TypedNode]:
    """Analyse a complete raw AST program and return typed nodes."""
    return Analyser(env).analyse(program)


def analyse_function(node: FunctionNode, env: T.Environment) -> T.Type | None:
    """Infer and return the stack-effect type of a function literal."""
    return Analyser(env).analyse_function(node)


def analyse_function_details(
    node: FunctionNode,
    env: T.Environment,
) -> FunctionAnalysis | None:
    """Infer a function literal and return its typed overload details."""
    return Analyser(env).analyse_function_details(node)


# Importing the handlers runs their registration decorators against the shared
# registry above. Keep this after ``Analyser`` and all helper modules exist.
from . import _analyser_handlers as _handlers  # noqa: E402
from .control_flow import blocks as _control_blocks  # noqa: E402
from .control_flow import loop_handlers as _control_loops  # noqa: E402
from .contracts import tag_handlers as _tag_handlers  # noqa: E402

_ANALYSER_PARTS = (
    _functions, _calls, _patterns, _utils, _handlers,
    _control_blocks, _control_loops, _tag_handlers,
)


def __getattr__(name: str):
    """Preserve access to private helpers moved out of this façade module."""
    for part in _ANALYSER_PARTS:
        try:
            return getattr(part, name)
        except AttributeError:
            continue
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    """Include moved implementation names in interactive module discovery."""
    names = set(globals())
    for part in _ANALYSER_PARTS:
        names.update(vars(part))
    return sorted(names)
