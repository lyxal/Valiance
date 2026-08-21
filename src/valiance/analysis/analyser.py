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
from valiance.analysis.import_runtime import prelude_seed, with_import_runtime_name
from valiance.analysis.transfer import (
    render_transfer_type_violations,
    validate_task_transfer_type,
)
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
    ConcurrentNode,
    DefineNode,
    ElementExtension,
    ElementNode,
    StackShuffleNode,
    EnumMemberNode,
    FileLintSuppressionNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    ImportComponent,
    ImportNode,
    ImportPath,
    ImportSpec,
    ListPatternNode,
    MatchCaseNode,
    MatchNode,
    MatchPatternNode,
    ObjectNode,
    TagDeclarationNode,
    ElementTagDeclarationNode,
    TagOverlayNode,
    OrPatternNode,
    PopNNode,
    SourceLocation,
    TraitRequirementNode,
    TryHandlerNode,
    TryNode,
    TypePatternNode,
    TypedCallNode,
    TypedChannelNode,
    TypedConcurrentNode,
    TypedElementExtension,
    TypedElementNode,
    TypedExtensionPatternRule,
    TypedFunctionNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedMatchNode,
    TypedNode,
    TypedSpawnNode,
    TypedWaitNode,
    TypedTagApplicationNode,
    TypedTryNode,
    VariantMemberNode,
    is_catch_all_match_case,
)
from valiance.asts.nodes import (
    GetVariableNode, ObjectFieldNode, SetVariableNode, SetVariablesNode,
)
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
        self.nodes.append(with_import_runtime_name(node, runtime_name))
        self.bindings.append((node, source_name, runtime_name))
        return runtime_name


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
    if isinstance(typ, T.TaskType):
        for item in typ.outputs:
            yield from _nested_types(item)
        for effect in typ.effects:
            for arg in effect.args:
                yield from _nested_types(arg)
        return
    if isinstance(typ, T.FunctionType):
        for item in (*(typ.params or ()), *(typ.returns or ())):
            yield from _nested_types(item)
        for element_tag in typ.element_tags:
            for arg in element_tag.args:
                yield from _nested_types(arg)
        return
    if isinstance(typ, (T.NoVecType, T.ExactType)):
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
        self._prelude = _prelude or _AnalysisPrelude(prelude_seed(source_file))
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
        self._imported_definition_sources: dict[Symbol, str] = {}
        self._imported_overload_sources: dict[tuple[Symbol, tuple[T.Type, ...]], str] = {}
        self._imported_object_sources: dict[Symbol, str] = {}
        self._imported_namespace_sources: dict[Symbol, str] = {}
        self._public_import_definitions = []
        self._public_import_objects = []
        self._public_import_tags = []
        self._public_import_overlays = []
        self._public_import_trait_implementations = []
        self._imported_trait_impl_sources: dict[tuple[Symbol, Symbol], str] = {}
        self._scope_depth = 0
        self._failed_imports: dict[tuple[str, ...], str] = {}
        self._top_level_declared_variable_names: frozenset[Symbol] = frozenset()
        self._prescanned_definition_overloads: dict[int, list[tuple[Symbol, int]]] = {}
        self._incomplete_recursive_definitions: tuple[DefineNode, ...] = ()
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
        setup, definitions, executable = self._top_level_analysis_phases(program)
        self._top_level_declared_variable_names = frozenset(
            target.name
            for node in program
            for target in (
                (node,)
                if isinstance(node, SetVariableNode)
                else node.targets
                if isinstance(node, SetVariablesNode)
                else ()
            )
        )
        current = self.analyse_block(initial, setup)
        if current:
            for definition in definitions:
                self.declarations.prescan_define(definition)
            for definition in self._incomplete_recursive_definitions:
                self._diagnose(
                    f"recursive element '{definition.name}' must have complete "
                    "parameter and return signatures",
                    definition,
                )
            current = self.analyse_block(current, definitions)
        final = self.analyse_block(current, executable) if current else current
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


    def _top_level_analysis_phases(
        self,
        program: list[ASTNode],
    ) -> tuple[tuple[ASTNode, ...], tuple[DefineNode, ...], tuple[ASTNode, ...]]:
        """Partition a module into prerequisite, function, and executable phases.

        Top-level named definitions cannot capture top-level runtime variables, so
        their declaration and inference may safely precede executable statements.
        Type-level declarations are retained ahead of functions so signatures can
        refer to objects, traits, and tags declared anywhere in the source file.
        """
        setup_types = (
            ImportNode,
            ObjectNode,
            TagDeclarationNode,
            ElementTagDeclarationNode,
            TagOverlayNode,
        )
        setup: list[ASTNode] = []
        definitions: list[DefineNode] = []
        executable: list[ASTNode] = []
        for node in program:
            if isinstance(node, DefineNode):
                definitions.append(node)
            elif isinstance(node, setup_types):
                setup.append(node)
            else:
                executable.append(node)
        return (
            tuple(setup),
            self._order_top_level_definitions(definitions),
            tuple(executable),
        )

    def _order_top_level_definitions(
        self,
        definitions: list[DefineNode],
    ) -> tuple[DefineNode, ...]:
        """Infer incomplete acyclic definitions before checking declared bodies.

        Complete signatures are published by the declaration prescan, so their
        bodies may remain in source order even when they are mutually recursive.
        Definitions requiring inference are dependency-ordered ahead of those
        bodies. Any recursive component containing an incomplete declaration is
        recorded for the language-level explicit-signature diagnostic.
        """
        names = {definition.name for definition in definitions}
        by_name: dict[Symbol, list[int]] = {}
        for index, definition in enumerate(definitions):
            by_name.setdefault(definition.name, []).append(index)

        dependencies: list[set[int]] = []
        for definition in definitions:
            referenced = self._definition_element_references(definition.function)
            dependencies.append({
                index
                for name in referenced & names
                for index in by_name[name]
            })

        self._incomplete_recursive_definitions = self._incomplete_cycle_members(
            definitions,
            dependencies,
        )

        incomplete_indexes = {
            index
            for index, definition in enumerate(definitions)
            if not self._definition_has_complete_contract(definition)
        }
        ordered_incomplete: list[int] = []
        complete: set[int] = set()
        visiting: set[int] = set()

        def visit_incomplete(index: int) -> None:
            """Schedule incomplete dependencies before the declaration using them."""
            if index in complete or index in visiting:
                return
            visiting.add(index)
            for dependency in sorted(dependencies[index] & incomplete_indexes):
                visit_incomplete(dependency)
            visiting.remove(index)
            complete.add(index)
            ordered_incomplete.append(index)

        for index in sorted(incomplete_indexes):
            visit_incomplete(index)

        declared = [
            index for index in range(len(definitions))
            if index not in incomplete_indexes
        ]
        return tuple(
            definitions[index]
            for index in (*ordered_incomplete, *declared)
        )

    @staticmethod
    def _definition_has_complete_contract(definition: DefineNode) -> bool:
        """Return whether a definition exposes complete callable signatures."""
        function = definition.function
        if function.overloads:
            return True
        explicit_params = function.params is not None or definition.name.text.startswith("\\")
        return (
            explicit_params
            and function.returns is not None
            and all(param.typ is not None for param in function.params or ())
        )

    def _incomplete_cycle_members(
        self,
        definitions: list[DefineNode],
        dependencies: list[set[int]],
    ) -> tuple[DefineNode, ...]:
        """Return incomplete declarations belonging to recursive components."""
        index = 0
        indexes: dict[int, int] = {}
        lowlinks: dict[int, int] = {}
        stack: list[int] = []
        on_stack: set[int] = set()
        incomplete: list[DefineNode] = []

        def connect(node_index: int) -> None:
            """Visit one definition using Tarjan's component algorithm."""
            nonlocal index
            indexes[node_index] = index
            lowlinks[node_index] = index
            index += 1
            stack.append(node_index)
            on_stack.add(node_index)
            for dependency in dependencies[node_index]:
                if dependency not in indexes:
                    connect(dependency)
                    lowlinks[node_index] = min(
                        lowlinks[node_index], lowlinks[dependency]
                    )
                elif dependency in on_stack:
                    lowlinks[node_index] = min(
                        lowlinks[node_index], indexes[dependency]
                    )
            if lowlinks[node_index] != indexes[node_index]:
                return
            component: list[int] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node_index:
                    break
            recursive = len(component) > 1 or node_index in dependencies[node_index]
            if recursive:
                incomplete.extend(
                    definitions[member]
                    for member in component
                    if not self._definition_has_complete_contract(definitions[member])
                )

        for node_index in range(len(definitions)):
            if node_index not in indexes:
                connect(node_index)
        return tuple(incomplete)

    @staticmethod
    def _definition_element_references(value: object) -> set[Symbol]:
        """Collect named element references nested inside a definition body."""
        references: set[Symbol] = set()

        def walk(item: object) -> None:
            """Visit AST and helper dataclasses while ignoring scalar metadata."""
            if isinstance(item, ElementNode):
                references.add(item.name)
            if isinstance(item, ASTNode) or is_dataclass(item):
                for data_field in fields(item):
                    walk(getattr(item, data_field.name))
            elif isinstance(item, (tuple, list, frozenset)):
                for child in item:
                    walk(child)

        walk(value)
        return references

    @property
    def public_import_definitions(self) -> tuple:
        """Return definitions made public through top-level public imports."""
        return tuple(self._public_import_definitions)

    @property
    def public_import_objects(self) -> tuple:
        """Return object-like declarations made public through public imports."""
        return tuple(self._public_import_objects)

    @property
    def public_import_tags(self) -> tuple:
        """Return tags made public through selective public imports."""
        return tuple(self._public_import_tags)

    @property
    def public_import_overlays(self) -> tuple:
        """Return overlays belonging to publicly re-exported tags."""
        return tuple(self._public_import_overlays)

    @property
    def public_import_trait_implementations(self) -> tuple:
        """Return trait implementations re-exported by top-level public imports."""
        return tuple(self._public_import_trait_implementations)

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
        imported_sources = self._imported_definition_sources.copy()
        overload_sources = self._imported_overload_sources.copy()
        object_sources = self._imported_object_sources.copy()
        namespace_sources = self._imported_namespace_sources.copy()
        trait_impl_sources = self._imported_trait_impl_sources.copy()
        self.env = outer.lexical_child_scope()
        self._scope_depth += 1
        try:
            return self.analyse_block(initial, nodes)
        finally:
            self._scope_depth -= 1
            self.env = outer
            self._imported_definition_sources = imported_sources
            self._imported_overload_sources = overload_sources
            self._imported_object_sources = object_sources
            self._imported_namespace_sources = namespace_sources
            self._imported_trait_impl_sources = trait_impl_sources

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
        child._scope_depth = self._scope_depth + 1
        child._imported_definition_sources = (
            self._imported_definition_sources.copy()
        )
        child._imported_overload_sources = self._imported_overload_sources.copy()
        child._imported_object_sources = self._imported_object_sources.copy()
        child._imported_namespace_sources = self._imported_namespace_sources.copy()
        child._imported_trait_impl_sources = self._imported_trait_impl_sources.copy()
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
        """Delegate ordinary calls, while fixing concurrency plans statically."""
        if node.name == Symbol("Channel") and not node.modifier_args and not node.call_args:
            if len(node.generic_args) != 1 or node.generic_args[0] is None:
                self._diagnose("Channel requires exactly one concrete item type", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            item_type = T.normalize(node.generic_args[0])
            channel_type = T.N(Symbol("Channel"), item_type)
            has_capacity = False
            remaining = branch
            if branch.stack and T.assignable(branch.stack[-1], T.Int, self.env.context):
                has_capacity = True
                remaining = branch.pop()
            typed = TypedChannelNode(
                node, channel_type, "new", item_type, has_capacity
            )
            return BranchSet((remaining.push(channel_type).emit(typed),))

        if node.name == Symbol("send") and not node.modifier_args and not node.call_args:
            if len(branch.stack) < 2:
                self._diagnose("send requires a channel and value", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            channel_type = T.normalize(branch.stack[-2])
            value_type = branch.stack[-1]
            if (
                not isinstance(channel_type, T.NominalType)
                or channel_type.name != Symbol("Channel")
                or len(channel_type.args) != 1
            ):
                self._diagnose("send requires Channel[T] below the value", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            item_type = channel_type.args[0]
            if not T.assignable(value_type, item_type, self.env.context):
                self._diagnose(
                    f"channel send expects {T.show(item_type)}, received {T.show(value_type)}",
                    node,
                )
                return BranchSet((branch.emit(TypedNode(node, None)),))
            typed = TypedChannelNode(node, None, "send", item_type, False)
            return BranchSet((branch.pop(2).emit(typed),))

        if node.name == Symbol("receive") and not node.modifier_args and not node.call_args:
            if not branch.stack:
                self._diagnose("receive requires a channel", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            channel_type = T.normalize(branch.stack[-1])
            if (
                not isinstance(channel_type, T.NominalType)
                or channel_type.name != Symbol("Channel")
                or len(channel_type.args) != 1
            ):
                self._diagnose("receive requires Channel[T]", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            item_type = channel_type.args[0]
            receive_type = T.N(Symbol("Receive"), item_type)
            typed = TypedChannelNode(node, receive_type, "receive", item_type, False)
            return BranchSet((branch.pop().push(receive_type).emit(typed),))

        if node.name == Symbol("close") and not node.modifier_args and not node.call_args:
            if not branch.stack:
                self._diagnose("close requires a channel", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            channel_type = T.normalize(branch.stack[-1])
            if (
                not isinstance(channel_type, T.NominalType)
                or channel_type.name != Symbol("Channel")
                or len(channel_type.args) != 1
            ):
                self._diagnose("close requires Channel[T]", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            item_type = channel_type.args[0]
            typed = TypedChannelNode(node, None, "close", item_type, False)
            return BranchSet((branch.pop().emit(typed),))

        if node.name == Symbol("spawn") and node.modifier_args and not node.call_args:
            if len(node.modifier_args) != 1:
                self._diagnose("spawn modifier requires exactly one function", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            function_node = node.modifier_args[0]
            visible = set(branch.variables.visible_names())
            capture_reads = _functions._top_level_capture_reads_in_function(
                function_node, visible, frozenset()
            )
            capture_violations = []
            seen_capture_names: set[Symbol] = set()
            for capture in capture_reads:
                if capture.name in seen_capture_names:
                    continue
                seen_capture_names.add(capture.name)
                capture_type = branch.variables.read(capture.name)
                if capture_type is None:
                    continue
                violation = validate_task_transfer_type(
                    capture_type,
                    self.env,
                    path=f"capture `{capture.name}`",
                )
                if violation is not None:
                    capture_violations.append(violation)
            if capture_violations:
                self._diagnose(
                    render_transfer_type_violations(tuple(capture_violations)),
                    function_node,
                )
                return BranchSet((branch.emit(TypedNode(node, None)),))
            analysed = self._analyse_function_literal(branch, function_node)
            if analysed is None:
                return BranchSet((branch.emit(TypedNode(node, None)),))
            details, _ = analysed
            matches: list[tuple[int, T.ResolvedOverload, AnalysisBranch, object, tuple[bool, ...]]] = []
            transfer_violation = None
            for index, item in enumerate(details.overloads):
                overload = item.overload
                if not isinstance(overload, T.Overload):
                    continue
                sourced = branch.source_arguments(overload.params)
                if sourced is None:
                    continue
                actual, remaining = sourced
                unique_inputs = _spawn_unique_inputs(branch, len(actual))
                resolved = T.apply_overload(overload, actual, self.env.context)
                if resolved is not None:
                    unsafe = next(
                        (
                            violation
                            for argument_index, typ in enumerate(actual)
                            if (
                                violation := validate_task_transfer_type(
                                    typ,
                                    self.env,
                                    path=f"argument[{argument_index}]",
                                )
                                if not unique_inputs[argument_index]
                                else None
                            )
                            is not None
                        ),
                        None,
                    )
                    if unsafe is None:
                        matches.append((index, resolved, remaining, item, unique_inputs))
                    elif transfer_violation is None:
                        transfer_violation = unsafe
            if not matches:
                if transfer_violation is not None:
                    self._diagnose(transfer_violation.render(), node)
                else:
                    self._diagnose("no spawn modifier overload accepts the current stack", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            if len(matches) > 1:
                # Reuse ordinary overload ranking across all viable declarations.
                arity = len(matches[0][1].params)
                if any(len(match[1].params) != arity for match in matches):
                    self._diagnose("spawn modifier overloads have ambiguous arity", node)
                    return BranchSet((branch.emit(TypedNode(node, None)),))
                sourced = branch.source_arguments(matches[0][1].params)
                assert sourced is not None
                actual, _ = sourced
                winner = T.resolve_overload_result(
                    tuple(match[1].overload for match in matches),
                    actual,
                    self.env.context,
                )
                if winner is None:
                    self._diagnose("spawn modifier overload is ambiguous", node)
                    return BranchSet((branch.emit(TypedNode(node, None)),))
                matches = [
                    match for match in matches if match[1].overload == winner.overload
                ]
            overload_index, resolved, remaining, selected, unique_inputs = matches[0]
            callable_type = T.Fn(
                resolved.params, resolved.returns, resolved.overload.element_tags
            )
            typed_callable = TypedFunctionNode(
                function_node, details.typ, details.overloads
            )
            task_type = T.Task(*resolved.returns, effects=resolved.overload.element_tags)
            typed = TypedSpawnNode(
                node,
                task_type,
                callable_type,
                resolved.params,
                resolved.returns,
                typed_callable,
                overload_index,
                unique_inputs,
                resolved.vectorised,
                resolved.vectorised_depths,
                resolved.vectorised_target_ranks,
                resolved.runtime_static_values,
            )
            return BranchSet((remaining.push(task_type).emit(typed),))

        if node.name == Symbol("spawn") and not node.modifier_args and not node.call_args:
            if not branch.stack:
                self._diagnose("empty stack when spawning a function", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            callable_type = T.normalize(branch.stack[-1])
            if not isinstance(callable_type, T.FunctionType):
                self._diagnose(
                    f"spawn requires a function, received {T.show(callable_type)}", node
                )
                return BranchSet((branch.emit(TypedNode(node, None)),))
            if callable_type.params is None or callable_type.returns is None:
                self._diagnose("spawn requires a concrete callable stack contract", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            without_callable = branch.pop()
            sourced = without_callable.source_arguments(callable_type.params)
            if sourced is None:
                self._diagnose("not enough stack values for spawned call", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            actual, remaining = sourced
            unique_inputs = _spawn_unique_inputs(without_callable, len(actual))
            for argument_index, typ in enumerate(actual):
                violation = validate_task_transfer_type(
                    typ, self.env, path=f"argument[{argument_index}]"
                )
                if violation is not None and not unique_inputs[argument_index]:
                    self._diagnose(violation.render(), node)
                    return BranchSet((branch.emit(TypedNode(node, None)),))
            call_plan = T.apply_overload(
                T.Overload(callable_type.params, callable_type.returns),
                actual,
                self.env.context,
            )
            if call_plan is None:
                self._diagnose("spawn argument type mismatch", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            task_type = T.Task(*call_plan.actual_returns, effects=callable_type.element_tags)
            typed = TypedSpawnNode(
                node, task_type, callable_type, call_plan.params, call_plan.actual_returns,
                None, 0, unique_inputs, call_plan.vectorised,
                call_plan.vectorised_depths, call_plan.vectorised_target_ranks,
                call_plan.runtime_static_values,
            )
            return BranchSet((remaining.push(task_type).emit(typed),))

        if node.name == Symbol("wait") and not node.modifier_args and not node.call_args:
            if not branch.stack:
                self._diagnose("empty stack when waiting for a task", node)
                return BranchSet((branch.emit(TypedNode(node, None)),))
            task_type = T.normalize(branch.stack[-1])
            if isinstance(task_type, T.TaskType):
                typed = TypedWaitNode(node, None, task_type.outputs, False, task_type.effects)
                return BranchSet((
                    branch.pop().push(*task_type.outputs).emit(typed),
                ))
            if isinstance(task_type, T.CollectionType) and isinstance(
                T.normalize(task_type.base), T.TaskType
            ):
                item_task = T.normalize(task_type.base)
                assert isinstance(item_task, T.TaskType)
                lifted = tuple(
                    T.C(type(task_type), output, task_type.rank)
                    for output in item_task.outputs
                )
                typed = TypedWaitNode(node, None, lifted, True, item_task.effects)
                return BranchSet((branch.pop().push(*lifted).emit(typed),))
            self._diagnose(
                f"wait requires a task or task collection, received {T.show(task_type)}",
                node,
            )
            return BranchSet((branch.emit(TypedNode(node, None)),))

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



def _spawn_unique_inputs(
    branch: AnalysisBranch,
    arity: int,
) -> tuple[bool, ...]:
    """Prove inputs unique only after one explicit non-duplicating move."""
    if arity == 0 or not branch.typed_body:
        return (False,) * arity
    source = branch.typed_body[-1].node
    if isinstance(source, FunctionNode) and len(branch.typed_body) >= 2:
        source = branch.typed_body[-2].node
    if (
        not isinstance(source, StackShuffleNode)
        or source.mode != Symbol("move")
        or len(source.poststack) < arity
        or len(set(source.poststack[-arity:])) != arity
    ):
        return (False,) * arity
    return (True,) * arity


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
