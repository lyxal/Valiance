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
from .support import analysis_utils as _utils
from .state import (
    AnalysisBranch, BranchSet, BranchVariables, Diagnostic,
    DiagnosticSeverity, InputMode, VariableWrite,
)
from .declarations import DeclarationAnalyser
from .calls import CallAnalyser
from .control_flow import ControlFlowAnalyser
from .contracts import ContractAnalyser
from .expressions import ExpressionAnalyser
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


def _rewrite_imported_self_calls(
    value,
    source_name: Symbol,
    runtime_name: Symbol,
):
    """Retarget recursive calls in an imported definition to its hidden binding."""
    if isinstance(value, TypedElementNode):
        rewritten = {
            field.name: _rewrite_imported_self_calls(
                getattr(value, field.name), source_name, runtime_name
            )
            for field in fields(value)
        }
        if isinstance(value.node, ElementNode) and value.node.name == source_name:
            rewritten["runtime_name"] = runtime_name
            rewritten["overload_index"] = 0
        return replace(value, **rewritten)
    if isinstance(value, tuple):
        return tuple(
            _rewrite_imported_self_calls(item, source_name, runtime_name)
            for item in value
        )
    if is_dataclass(value) and not isinstance(value, ASTNode):
        rewritten = {
            field.name: _rewrite_imported_self_calls(
                getattr(value, field.name), source_name, runtime_name
            )
            for field in fields(value)
        }
        return replace(value, **rewritten)
    return value


def _with_import_runtime_name(
    node: TypedNode,
    runtime_name: Symbol,
) -> TypedNode:
    """Attach a hidden runtime binding without changing source-level names."""
    if isinstance(node, TypedFunctionNode):
        source_name = (
            node.node.name if isinstance(node.node, DefineNode) else Symbol("")
        )
        overloads = _rewrite_imported_self_calls(
            node.overloads, source_name, runtime_name
        )
        return TypedImportedFunctionNode(
            node.node,
            node.typ,
            overloads,
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
        self.expressions = ExpressionAnalyser(self)

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
        expressions = self.__dict__.get("expressions")
        if expressions is not None and expressions.provides(name):
            return getattr(expressions, name)
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
        from valiance.analysis.lints import canonical_lint_code

        for node in program:
            if isinstance(node, FileLintSuppressionNode):
                if node.codes:
                    self.disabled_lint_codes.update(
                        canonical
                        for code in node.codes
                        if (canonical := canonical_lint_code(code)) is not None
                    )
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
        """Delegate element invocation to the call-planning subsystem."""
        return self.calls._element(node, branch)

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













    def _diagnose(self, message: str, node: ASTNode | None = None) -> None:
        """Update diagnose state during static analysis."""
        diagnostic = _utils._diagnostic_message(message, node)
        if diagnostic in self.diagnostics:
            return
        if "unknown element " in diagnostic and "did you mean '$" in diagnostic:
            # A bare parameter name is a primary spelling error. Put it before
            # overload failures produced while recovering through the same chain
            # so every frontend leads with the actionable cause.
            self.diagnostics.insert(0, diagnostic)
            return
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
from .handlers import core as _handlers  # noqa: E402
from .control_flow import blocks as _control_blocks  # noqa: E402
from .control_flow import loop_handlers as _control_loops  # noqa: E402
from .contracts import tag_handlers as _tag_handlers  # noqa: E402
from .expressions import assignments as _assignments  # noqa: E402
from .expressions import access_handlers as _access_handlers  # noqa: E402
from .expressions import collections as _collections  # noqa: E402

_ANALYSER_PARTS = (
    _functions, _calls, _patterns, _utils, _handlers,
    _control_blocks, _control_loops, _tag_handlers,
    _assignments, _access_handlers, _collections,
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
