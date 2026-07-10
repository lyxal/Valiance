"""Branch-based static analysis, type inference, and overload resolution."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, fields, replace
from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from hashlib import sha1
from itertools import count, permutations
from pathlib import Path
from typing import cast

import valiance.analysis.annotations as annotation_hooks
import valiance.types as T
from valiance.analysis.builtins import default_environment
from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
    AssertNode,
    ASTNode,
    AtNode,
    BindingPatternNode,
    CallArgument,
    CastNode,
    DefineNode,
    DictLiteralNode,
    ElementExtension,
    ElementNode,
    ElementTagDeclarationNode,
    EnumMemberNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    GuardPatternNode,
    ImportComponent,
    ImportNode,
    ImportPath,
    ImportSpec,
    IndexAccessNode,
    IndexSelector,
    IndexSetNode,
    ListLiteralNode,
    ListPatternNode,
    LiteralPatternNode,
    MatchCaseNode,
    MatchNode,
    MatchPatternNode,
    NumberLiteralNode,
    ObjectNode,
    OrPatternNode,
    RecordLiteralNode,
    RestPatternNode,
    ReturnNode,
    SourceLocation,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TagDeclarationNode,
    TagOverlayNode,
    TraitRequirementNode,
    TryHandlerNode,
    TryNode,
    TupleLiteralNode,
    TypedAssertNode,
    TypedAtNode,
    TypedCallNode,
    TypedElementExtension,
    TypedElementNode,
    TypedExtensionPatternRule,
    TypedForNode,
    TypedFunctionNode,
    TypedIfNode,
    TypedImportedFunctionNode,
    TypedImportedObjectNode,
    TypedLiteralNode,
    TypedMatchNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedTryNode,
    TypedUnfoldNode,
    TypedWhileNode,
    TypePatternNode,
    UnfoldNode,
    VariantMemberNode,
    WildcardPatternNode,
)
from valiance.asts.nodes import (
    BreakNode,
    CallNode,
    FieldAccessNode,
    FieldSetNode,
    ForNode,
    GetVariableNode,
    IfNode,
    ObjectFieldNode,
    SetVariableNode,
    SetVariablesNode,
    WhileNode,
)
from valiance.modules import (
    ModuleLoader,
    ModuleLoadError,
    import_definitions,
    import_environment_facts,
    import_objects,
)
from valiance.object_constructors import (
    constructor_definitions,
    definitely_initialized_fields,
    prepare_constructor_body,
)
from valiance.symbols import Symbol
from valiance.types.default_types import Boolean
from valiance.types.relations import merge_stacks

_branch_ids = count(1)


class DiagnosticSeverity(Enum):
    ERROR = auto()
    WARNING = auto()


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    location: SourceLocation | None
    severity: DiagnosticSeverity = DiagnosticSeverity.ERROR
    expected: T.Type | None = None
    actual: T.Type | None = None
    notes: tuple[str, ...] = ()
    help: tuple[str, ...] = ()


@dataclass(frozen=True)
class VariableWrite:
    variables: BranchVariables | None
    error: str | None = None


class InputMode(Enum):
    """How a branch may satisfy missing element inputs."""

    TOP_LEVEL = auto()
    INFER_INPUTS = auto()
    CYCLE_EXPLICIT_PARAMS = auto()
    NILADIC = auto()


@dataclass(frozen=True)
class BranchVariables:
    """Branch-local variable facts.

    The environment owns global facts such as overloads and object definitions.
    This record owns values whose types can differ between analysis branches.
    """

    function_locals: tuple[tuple[Symbol, T.Type], ...] = ()
    parameters: tuple[tuple[Symbol, T.Type], ...] = ()
    captures: tuple[tuple[Symbol, T.Type], ...] = ()
    block_locals: tuple[tuple[Symbol, T.Type], ...] = ()
    function_constants: tuple[Symbol, ...] = ()
    block_constants: tuple[Symbol, ...] = ()

    @classmethod
    def from_parameters(
        cls,
        params: tuple[tuple[Symbol, T.Type], ...],
        *,
        captures: BranchVariables | None = None,
    ) -> BranchVariables:
        """Create a function variable frame from named parameters."""
        captured = () if captures is None else captures.visible_items()
        return cls(parameters=_sorted_items(params), captures=_sorted_items(captured))

    def visible_items(self) -> tuple[tuple[Symbol, T.Type], ...]:
        """Return all currently readable variables, inner names first."""
        result: dict[Symbol, T.Type] = {}
        for name, typ in reversed(self.captures):
            result.setdefault(name, typ)
        for name, typ in reversed(self.parameters):
            result[name] = typ
        for name, typ in reversed(self.function_locals):
            result[name] = typ
        for name, typ in reversed(self.block_locals):
            result[name] = typ
        return _sorted_items(result.items())

    def constant_items(self) -> tuple[tuple[Symbol, T.Type], ...]:
        """Return visible immutable bindings that are safe to read in functions."""
        constant_names = set(self.function_constants) | set(self.block_constants)
        return tuple(
            (name, typ)
            for name, typ in self.visible_items()
            if name in constant_names
        )

    def nonconstant_names(self) -> frozenset[Symbol]:
        """Return visible binding names that may change after function creation."""
        constant_names = set(self.function_constants) | set(self.block_constants)
        return frozenset(
            name for name, _typ in self.visible_items() if name not in constant_names
        )

    def read(self, name: Symbol) -> T.Type | None:
        """Read a variable using block, function, parameter, capture order."""
        for scope in (
            self.block_locals,
            self.function_locals,
            self.parameters,
            self.captures,
        ):
            typ = _lookup(scope, name)
            if typ is not None:
                return typ
        return None

    def write(
        self,
        name: Symbol,
        typ: T.Type,
        *,
        block_local: bool = False,
        constant: bool = False,
        ctx: T.Context | None = None,
    ) -> VariableWrite:
        """Return variables after assigning ``name`` in this branch."""
        ctx = ctx or T.Context()
        existing_block_local = _lookup(self.block_locals, name)
        if existing_block_local is not None:
            if name in self.block_constants:
                return VariableWrite(None, f"cannot assign to constant '{name}'")
            stored_type = _assignment_stored_type(existing_block_local, typ, ctx)
            diagnostic = _assignment_error(name, typ, existing_block_local, ctx)
            if diagnostic is not None:
                return VariableWrite(None, diagnostic)
            if T.same(stored_type, existing_block_local):
                return VariableWrite(self)
            return VariableWrite(
                BranchVariables(
                    function_locals=self.function_locals,
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=_set_item(self.block_locals, name, stored_type),
                    function_constants=self.function_constants,
                    block_constants=self.block_constants,
                ),
            )
        if _lookup(self.parameters, name) is not None:
            return VariableWrite(
                None,
                f"cannot assign to read-only parameter '{name}'",
            )
        existing_function_local = _lookup(self.function_locals, name)
        if existing_function_local is not None:
            if name in self.function_constants:
                return VariableWrite(None, f"cannot assign to constant '{name}'")
            stored_type = _assignment_stored_type(existing_function_local, typ, ctx)
            diagnostic = _assignment_error(name, typ, existing_function_local, ctx)
            if diagnostic is not None:
                return VariableWrite(None, diagnostic)
            return VariableWrite(
                BranchVariables(
                    function_locals=_set_item(self.function_locals, name, stored_type),
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=self.block_locals,
                    function_constants=self.function_constants,
                    block_constants=self.block_constants,
                ),
            )
        if _lookup(self.captures, name) is not None:
            return VariableWrite(
                BranchVariables(
                    function_locals=_set_item(self.function_locals, name, typ),
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=self.block_locals,
                    function_constants=_set_symbol_flag(
                        self.function_constants, name, constant
                    ),
                    block_constants=self.block_constants,
                ),
            )
        if block_local or _lookup(self.block_locals, name) is not None:
            return VariableWrite(
                BranchVariables(
                    function_locals=self.function_locals,
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=_set_item(self.block_locals, name, typ),
                    function_constants=self.function_constants,
                    block_constants=_set_symbol_flag(
                        self.block_constants, name, constant
                    ),
                ),
            )
        return VariableWrite(
            BranchVariables(
                function_locals=_set_item(self.function_locals, name, typ),
                parameters=self.parameters,
                captures=self.captures,
                block_locals=self.block_locals,
                function_constants=_set_symbol_flag(
                    self.function_constants, name, constant
                ),
                block_constants=self.block_constants,
            ),
        )

    def with_block_local(self, name: Symbol, typ: T.Type) -> BranchVariables:
        """Add or replace a temporary block-local variable."""
        return BranchVariables(
            function_locals=self.function_locals,
            parameters=self.parameters,
            captures=self.captures,
            block_locals=_set_item(self.block_locals, name, typ),
            function_constants=self.function_constants,
            block_constants=self.block_constants,
        )

    def drop_block_locals(self) -> BranchVariables:
        """Return this frame after leaving a block-local scope."""
        return BranchVariables(
            function_locals=self.function_locals,
            parameters=self.parameters,
            captures=self.captures,
            function_constants=self.function_constants,
        )

    def refine_type(self, old: T.Type, new: T.Type) -> BranchVariables:
        """Replace one type fact wherever it appears in visible branch variables."""
        return BranchVariables(
            function_locals=_refine_items(self.function_locals, old, new),
            parameters=_refine_items(self.parameters, old, new),
            captures=_refine_items(self.captures, old, new),
            block_locals=_refine_items(self.block_locals, old, new),
            function_constants=self.function_constants,
            block_constants=self.block_constants,
        )

    def merge_against(
        self,
        other: BranchVariables,
        before: BranchVariables,
    ) -> BranchVariables:
        """Merge two branch outputs, preserving only variables visible before."""
        locals_by_name: dict[Symbol, T.Type] = {}
        before_names = {name for name, _ in before.function_locals}
        for name in before_names:
            left = _lookup(self.function_locals, name) or _lookup(
                before.function_locals, name
            )
            right = _lookup(other.function_locals, name) or _lookup(
                before.function_locals, name
            )
            if left is not None and right is not None:
                locals_by_name[name] = (
                    left if left == right else T.merge_types(left, right)
                )
        block_locals_by_name: dict[Symbol, T.Type] = {}
        before_block_names = {name for name, _ in before.block_locals}
        for name in before_block_names:
            left = _lookup(self.block_locals, name) or _lookup(
                before.block_locals, name
            )
            right = _lookup(other.block_locals, name) or _lookup(
                before.block_locals, name
            )
            if left is not None and right is not None:
                block_locals_by_name[name] = (
                    left if left == right else T.merge_types(left, right)
                )
        return BranchVariables(
            function_locals=_sorted_items(locals_by_name.items()),
            parameters=before.parameters,
            captures=before.captures,
            block_locals=_sorted_items(block_locals_by_name.items()),
            function_constants=tuple(
                name for name in before.function_constants if name in locals_by_name
            ),
            block_constants=tuple(
                name for name in before.block_constants if name in block_locals_by_name
            ),
        )


@dataclass(frozen=True)
class AnalysisBranch:
    """One possible analysis path.

    A branch owns the current stack, inferred inputs, branch-specific variables,
    typed AST emitted on this path, and the input sourcing mode.
    """

    stack: T.TypeStack = field(default_factory=T.TypeStack)
    inputs: tuple[T.Type, ...] = ()
    variables: BranchVariables = field(default_factory=BranchVariables)
    typed_body: tuple[TypedNode, ...] = ()
    element_tags: frozenset[T.ElementTag] = field(default_factory=frozenset)
    data_element_uses: frozenset[tuple[Symbol, Symbol]] = field(
        default_factory=frozenset
    )
    input_mode: InputMode = InputMode.TOP_LEVEL
    cycle_params: tuple[T.Type, ...] = ()
    cycle_index: int = 0
    break_type: T.Type | None = None
    errors: tuple[Diagnostic, ...] = ()
    warnings: tuple[Diagnostic, ...] = ()
    origin: int = field(default_factory=lambda: next(_branch_ids))

    @property
    def top(self) -> T.Type | None:
        """Return the top stack type, or `None` for an empty stack."""
        return self.stack[-1] if self.stack else None

    @property
    def failed(self) -> bool:
        """Return whether this analysis path contains an error diagnostic."""
        return bool(self.errors)

    @property
    def terminal(self) -> bool:
        """Return whether this path cannot continue because it contains ``Never``."""
        return any(_is_never(typ) for typ in self.stack)

    def with_stack(self, stack: T.TypeStack) -> AnalysisBranch:
        """Return a branch with its type stack replaced."""
        return replace(self, stack=stack)

    def push(self, *types: T.Type) -> AnalysisBranch:
        """Return a branch with additional types pushed onto its stack."""
        return replace(self, stack=self.stack.push(*types))

    def pop(self, count: int = 1) -> AnalysisBranch:
        """Return a branch with the requested stack types removed."""
        return replace(self, stack=self.stack.pop(count))

    def with_variables(self, variables: BranchVariables) -> AnalysisBranch:
        """Return a branch with updated branch-local variable facts."""
        return replace(self, variables=variables)

    def with_element_tags(
        self,
        tags: Iterable[T.ElementTag],
    ) -> AnalysisBranch:
        """Return a branch carrying the additional positive element tags."""
        return replace(
            self,
            element_tags=frozenset(
                (*self.element_tags, *(tag for tag in tags if not tag.absent))
            ),
        )

    def with_data_element_uses(
        self,
        uses: Iterable[tuple[Symbol, Symbol]],
    ) -> AnalysisBranch:
        """Return a branch recording additional data-tag element uses."""
        return replace(
            self,
            data_element_uses=frozenset((*self.data_element_uses, *uses)),
        )

    def emit(self, typed_node: TypedNode) -> AnalysisBranch:
        """Append a typed node and accumulate its element-tag effects."""
        element_tags = set(self.element_tags)
        data_element_uses = set(self.data_element_uses)
        applied: T.AppliedOverload | None = None
        if isinstance(typed_node, (TypedElementNode, TypedCallNode)):
            if typed_node.overload is not None:
                applied = typed_node.overload
        elif isinstance(typed_node, TypedTagApplicationNode):
            if typed_node.validator is not None:
                applied = typed_node.validator
        if applied is not None:
            positives = tuple(tag for tag in applied.element_tags if not tag.absent)
            element_tags.update(positives)
            data_names = {
                Symbol(tag.name)
                for param in applied.params
                for tag in _present_data_tags(param)
            }
            data_element_uses.update(
                (data_name, element_tag.name)
                for data_name in data_names
                for element_tag in positives
            )
        return replace(
            self,
            typed_body=(*self.typed_body, typed_node),
            element_tags=frozenset(element_tags),
            data_element_uses=frozenset(data_element_uses),
        )

    def error(
        self,
        message: str,
        location: SourceLocation | None,
        *,
        code: str,
        expected: T.Type | None = None,
        actual: T.Type | None = None,
        notes: tuple[str, ...] = (),
        help: tuple[str, ...] = (),
    ) -> AnalysisBranch:
        """Return a failed branch containing a structured error diagnostic."""
        return replace(
            self,
            errors=(
                *self.errors,
                Diagnostic(
                    code=code,
                    message=message,
                    location=location,
                    expected=expected,
                    actual=actual,
                    notes=notes,
                    help=help,
                ),
            ),
        )

    def warning(
        self,
        message: str,
        location: SourceLocation | None,
        *,
        code: str,
        notes: tuple[str, ...] = (),
    ) -> AnalysisBranch:
        """Return a branch containing a structured warning diagnostic."""
        return replace(
            self,
            warnings=(
                *self.warnings,
                Diagnostic(
                    code=code,
                    message=message,
                    location=location,
                    severity=DiagnosticSeverity.WARNING,
                    notes=notes,
                ),
            ),
        )

    def with_break(self, typ: T.Type | None) -> AnalysisBranch:
        """Return a branch carrying the merged type of a break value."""
        return replace(self, break_type=typ)

    def refine_type(self, old: T.Type, new: T.Type) -> AnalysisBranch:
        """Replace one inferred/generic type fact across the branch."""
        return replace(
            self,
            stack=_refine_stack(self.stack, old, new),
            inputs=tuple(_refine_type(item, old, new) for item in self.inputs),
            variables=self.variables.refine_type(old, new),
            typed_body=_refine_typed_body(self.typed_body, old, new),
            element_tags=frozenset(
                T.ElementTag(
                    tag.name,
                    tuple(_refine_type(arg, old, new) for arg in tag.args),
                    tag.absent,
                )
                for tag in self.element_tags
            ),
            cycle_params=tuple(
                _refine_type(item, old, new) for item in self.cycle_params
            ),
        )

    def source_arguments(
        self,
        params: tuple[T.Type, ...],
    ) -> tuple[tuple[T.Type, ...], AnalysisBranch] | None:
        """Pop, infer, or cycle enough values to call an overload."""
        arity = len(params)
        if arity == 0:
            return (), self

        available = min(len(self.stack), arity)
        missing = arity - available
        remaining = T.TypeStack(self.stack.items[: len(self.stack) - available])
        stack_args = self.stack.items[len(self.stack) - available :]

        if missing == 0:
            return stack_args, self.with_stack(remaining)

        match self.input_mode:
            case InputMode.INFER_INPUTS:
                inferred = params[:missing]
                return (
                    inferred + stack_args,
                    replace(
                        self,
                        stack=remaining,
                        inputs=self.inputs + inferred,
                    ),
                )
            case InputMode.CYCLE_EXPLICIT_PARAMS if self.cycle_params:
                cycle_len = len(self.cycle_params)
                cycled = tuple(
                    self.cycle_params[(self.cycle_index + index) % cycle_len]
                    for index in range(missing)
                )
                return (
                    cycled + stack_args,
                    replace(
                        self,
                        stack=remaining,
                        cycle_index=(self.cycle_index + missing)
                        % len(self.cycle_params),
                    ),
                )
            case _:
                return None


@dataclass(frozen=True)
class BranchSet:
    """A set of possible analysis branches."""

    branches: tuple[AnalysisBranch, ...] = ()

    @classmethod
    def collect(cls, branches: Iterable[AnalysisBranch]) -> BranchSet:
        """Flatten, diagnose, and deduplicate a collection of analysis branches."""
        unique: list[AnalysisBranch] = []
        seen: set[AnalysisBranch] = set()

        for branch in branches:
            if branch in seen:
                continue

            seen.add(branch)
            unique.append(branch)

        return cls(tuple(unique))

    def __bool__(self) -> bool:
        """Return whether at least one analysis branch survives."""
        return bool(self.branches)

    def __iter__(self) -> Iterator[AnalysisBranch]:
        """Iterate over the surviving analysis branches."""
        return iter(self.branches)

    def __len__(self) -> int:
        """Return the number of surviving analysis branches."""
        return len(self.branches)


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


@dataclass(frozen=True)
class FunctionAnalysis:
    """Typed function literal result, including per-overload typed bodies."""

    typ: T.Type
    overloads: tuple[FunctionOverloadTyping, ...]


@dataclass(frozen=True)
class ListItemAnalysis:
    """One possible analysis result for a forked literal item."""

    branch: AnalysisBranch
    typ: T.Type
    consumed: int
    typed_body: tuple[TypedNode, ...]


@dataclass(frozen=True)
class ModifierArgumentAnalysis:
    """Analysed function value supplied by an element modifier."""

    typ: T.Type
    typed_node: TypedFunctionNode


@dataclass(frozen=True)
class ElementArguments:
    overload: T.Overload
    overload_index: int
    arguments: tuple[T.Type, ...]
    branch: AnalysisBranch
    modifiers: tuple[ModifierArgumentAnalysis, ...] = ()
    call_arg_order: tuple[int, ...] = ()


@dataclass(frozen=True)
class OverloadApplication:
    applied: T.AppliedOverload
    branch: AnalysisBranch


@dataclass(frozen=True)
class CallCandidate:
    applied: T.AppliedOverload
    branch: AnalysisBranch
    modifiers: tuple[ModifierArgumentAnalysis, ...] = ()
    call_arg_order: tuple[int, ...] = ()
    callable_overload_index: int | None = None
    overload_index: int | None = None
    dispatch_priority: int = 1


@dataclass(frozen=True)
class ElementCallPreparation:
    """Analysed explicit call arguments plus their runtime stack order."""

    branch: AnalysisBranch
    call_arg_order: tuple[int, ...]


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


class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""

    def __init__(
        self,
        env: T.Environment | None = None,
        *,
        module_loader: ModuleLoader | None = None,
        source_file: Path | None = None,
        _prelude: _AnalysisPrelude | None = None,
    ):
        """Initialize an analysis session with its environment and module context."""
        self.env = env if env is not None else default_environment().child_scope()
        self.module_loader = module_loader or ModuleLoader()
        self.source_file = source_file
        self._prelude = _prelude or _AnalysisPrelude(_prelude_seed(source_file))
        self._owns_prelude = _prelude is None
        self.diagnostics: list[str] = []
        self.warnings: list[str] = []
        self._friendly_owners: tuple[Symbol, ...] = ()
        self._reported_data_element_disjoints: set[
            tuple[int, Symbol, Symbol]
        ] = set()

    def analyse(self, program: list[ASTNode]) -> list[TypedNode]:
        """Analyse a top-level sequence into typed nodes."""
        if self._owns_prelude:
            self._prelude.nodes.clear()
            self._prelude.bindings.clear()
        initial = BranchSet((AnalysisBranch(input_mode=InputMode.TOP_LEVEL),))
        final = self.analyse_block(initial, tuple(program))
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
            _prelude=self._prelude,
        )
        child._friendly_owners = self._friendly_owners
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

        if _is_never(actual) or not T.assignable(actual, expected, self.env.context):
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
        """Analyse a `DefineNode` node and return the surviving branches."""
        name = node.name
        function_node = node.function
        if not self._validate_annotations(node.annotations, "define", node):
            return BranchSet((branch.emit(TypedNode(node, None)),))
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            function_node,
            node.annotations,
        )
        function_node = _genericize_function_node(function_node, node.generics)
        function_node = replace(
            function_node,
            generics=node.generics,
            generic_variances=node.generic_variances,
            generic_constraints=node.generic_constraints,
        )
        self._validate_function_element_tags(function_node, node)
        declared_overload = (
            _fully_typed_overload(function_node)
            if not node.generics and _body_references_element(function_node.body, name)
            else None
        )
        if (
            declared_overload is not None
            and not self.env.has_local_non_object_friendly_overload(
                name,
                declared_overload,
            )
        ):
            self.env.define_overload(name, declared_overload)
        result = self._analyse_function_literal(branch, function_node)
        if result is None:
            return BranchSet((branch.emit(TypedNode(node, None)),))
        function, typed_branch = result
        generic_constraints = _generic_constraints(
            node.generics,
            node.generic_variances,
            node.generic_constraints,
        )
        overload_typings = list(function.overloads)
        for typing_index, typing in enumerate(function.overloads):
            if not isinstance(typing.overload, T.Overload):
                continue
            overload = self.prepare_defined_overload(
                node,
                branch,
                typing.overload,
                generic_constraints,
            )
            if overload is None:
                continue
            overload_typings[typing_index] = replace(typing, overload=overload)
            if not self.env.has_local_non_object_friendly_overload(name, overload):
                self.env.define_overload(name, overload)
            original_index = self.env.non_object_friendly_overload_index(
                name,
                overload,
            )
            if name.text.startswith("#") and original_index is not None:
                static_result = _static_validator_result(typing.body)
                if static_result is not None:
                    self.env.set_tag_validator_static_result(
                        name,
                        original_index,
                        static_result,
                    )
            if annotation_hooks.has_annotation(node.annotations, "commutative"):
                for generated in annotation_hooks.commutative_overloads(overload):
                    if not self.env.has_non_object_friendly_overload(
                        name,
                        generated,
                    ):
                        self.env.define_overload(name, generated)
                    overload_typings.append(
                        annotation_hooks.commutative_overload_typing(
                            name,
                            overload,
                            generated,
                            original_index or 0,
                        )
                    )
        if node.attached_tag is not None:
            if self.env.lookup_tag(node.attached_tag.name) is None:
                self._diagnose(
                    f"cannot attach element '{name}' to undeclared tag "
                    f"'#{node.attached_tag.name}'",
                    node,
                )
            self.env.define_tag_attached_element(node.attached_tag.name, name)
        typed_node = TypedFunctionNode(node, function.typ, tuple(overload_typings))
        return BranchSet((typed_branch.emit(typed_node),))

    def prepare_defined_overload(
        self,
        node: DefineNode,
        _branch: AnalysisBranch,
        overload: T.Overload,
        generic_constraints: tuple[T.GenericConstraint, ...],
    ) -> T.Overload | None:
        """Apply definition annotations and register the resulting overload metadata."""
        name = node.name
        if not _validate_define_niladic_name(name, overload):
            if name.text.startswith("\\"):
                self._diagnose(
                    f"{name} named as nilad, but inferred as popping "
                    f"{len(overload.params)} value(s)",
                    node,
                )
            else:
                self._diagnose(
                    f"{name} inferred as nilad, but not named as one",
                    node,
                )
            return None

        if name.text.startswith("#") and not _validator_overload_ok(
            overload,
            self.env.context,
        ):
            self._diagnose(
                f"tag validator '{name}' must return #boolean Number",
                node,
            )
            return None

        self._validate_data_tags((overload.params, overload.returns), node)
        overload = _with_generic_constraints(overload, generic_constraints)
        overload = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
            overload,
            node.annotations,
        )

        if not node.is_multi:
            return overload

        overload = replace(overload, is_multi=True)
        if _has_multimethod_fallback(
            overload,
            self.env.overloads_for(name),
            self.env.context,
        ):
            return overload

        self._diagnose(
            f"multi define '{name}' requires a non-multi fallback "
            "with compatible parameters and identical returns",
            node,
        )
        return None

    def _validate_function_element_tags(
        self,
        node: FunctionNode,
        origin: ASTNode,
    ) -> None:
        """Validate function element tags during static analysis."""
        self._validate_element_tag_set(
            node.element_tags,
            origin,
            companion_tags_allowed=node.companion_tags_allowed,
        )
        annotated_types = tuple(
            param.typ
            for param in node.params or ()
            if param.typ is not None
        ) + tuple(node.returns or ()) + tuple(
            constraint
            for constraint in node.generic_constraints
            if constraint is not None
        )
        self._validate_element_tags_in_types(annotated_types, origin)

    def _validate_element_tag_set(
        self,
        tags: Iterable[T.ElementTag],
        origin: ASTNode,
        *,
        companion_tags_allowed: frozenset[T.ElementTag] | None = None,
    ) -> None:
        """Validate element tag set during static analysis."""
        tag_tuple = tuple(tags)
        positives = tuple(tag for tag in tag_tuple if not tag.absent)
        absences = tuple(tag for tag in tag_tuple if tag.absent)
        allowed = companion_tags_allowed or frozenset()
        for tag in tag_tuple:
            definition = self.env.lookup_element_tag(tag.name)
            if definition is None:
                self._diagnose(f"undeclared element tag '{tag.name}'", origin)
                continue
            if (
                not tag.absent
                and
                definition.kind is T.ElementTagKind.COMPANION
                and tag not in allowed
            ):
                self._diagnose(
                    f"companion element tag '{tag.name}' cannot be directly attached",
                    origin,
                )
        for absent in absences:
            conflict = next(
                (
                    positive
                    for positive in positives
                    if _element_tag_absence_conflicts(
                        absent,
                        positive,
                        self.env.context,
                    )
                ),
                None,
            )
            if conflict is not None:
                self._diagnose(
                    f"element tag '{absent.name}' cannot be both present and absent",
                    origin,
                )
                break
        self._validate_element_tag_disjoints(positives, origin)

    def _validate_element_tags_in_types(
        self,
        types: Iterable[T.Type],
        origin: ASTNode,
    ) -> None:
        """Validate element tags in types during static analysis."""
        for typ in types:
            for tags in _function_type_element_tag_sets(typ):
                self._validate_element_tag_set(
                    tags,
                    origin,
                    companion_tags_allowed=frozenset(
                        tag for tag in tags if not tag.absent
                    ),
                )

    def _validate_element_tag_disjoints(
        self,
        tags: Iterable[T.ElementTag],
        origin: ASTNode,
    ) -> None:
        """Validate element tag disjoints during static analysis."""
        seen: set[Symbol] = set()
        for tag in tags:
            disjoint = self.env.element_tag_disjoints(tag.name)
            conflict = next((name for name in seen if name in disjoint), None)
            if conflict is not None:
                self._diagnose(
                    f"element tags '{conflict}' and '{tag.name}' cannot both apply",
                    origin,
                )
                return
            seen.add(tag.name)

    def _validate_inferred_element_tags(
        self,
        node: FunctionNode,
        body_tags: frozenset[T.ElementTag],
        final_tags: frozenset[T.ElementTag],
    ) -> None:
        """Validate inferred element tags during static analysis."""
        self._validate_element_tag_disjoints(
            (tag for tag in final_tags if not tag.absent),
            node,
        )
        declared_absences = tuple(
            tag for tag in node.element_tags if tag.absent
        )
        for body_tag in body_tags:
            forbidden = next(
                (
                    absent
                    for absent in declared_absences
                    if _element_tag_absence_conflicts(
                        absent,
                        body_tag,
                        self.env.context,
                    )
                ),
                None,
            )
            if forbidden is not None:
                self._diagnose(
                    f"element tag '{body_tag.name}' is required to be absent "
                    "but is used by the function body",
                    node,
                )
                return
        if not node.element_tags_explicit:
            return
        declared_properties = tuple(
            tag
            for tag in node.element_tags
            if not tag.absent
            and (definition := self.env.lookup_element_tag(tag.name)) is not None
            and definition.kind is T.ElementTagKind.PROPERTY
        )
        for tag in body_tags:
            if tag.absent or any(
                _element_tag_covers(declared, tag, self.env.context)
                for declared in declared_properties
            ):
                continue
            definition = self.env.lookup_element_tag(tag.name)
            if definition is None or definition.kind is not T.ElementTagKind.PROPERTY:
                continue
            self._diagnose(
                f"element tag '{tag.name}' is used inside an explicitly "
                "constrained function but was not declared",
                node,
            )
            return

    def _validate_data_element_tag_disjoints(
        self,
        types: Iterable[T.Type],
        element_tags: Iterable[T.ElementTag],
        origin: ASTNode,
    ) -> None:
        """Validate data element tag disjoints during static analysis."""
        positive_elements = tuple(tag for tag in element_tags if not tag.absent)
        if not positive_elements:
            return
        data_tags = {
            tag.name
            for typ in types
            for tag in _present_data_tags(typ)
        }
        for data_name in data_tags:
            disjoint_elements = self.env.data_tag_element_disjoints(data_name)
            for element_tag in positive_elements:
                if element_tag.name not in disjoint_elements:
                    continue
                key = (id(origin), data_name, element_tag.name)
                if key in self._reported_data_element_disjoints:
                    continue
                self._reported_data_element_disjoints.add(key)
                self._diagnose(
                    f"data tag '#{data_name}' cannot be used by an element "
                    f"with tag '{element_tag.name}'",
                    origin,
                )

    def _validate_recorded_data_element_uses(
        self,
        uses: Iterable[tuple[Symbol, Symbol]],
        origin: ASTNode,
    ) -> None:
        """Validate recorded data element uses during static analysis."""
        for data_name, element_name in uses:
            if element_name not in self.env.data_tag_element_disjoints(data_name):
                continue
            key = (id(origin), data_name, element_name)
            if key in self._reported_data_element_disjoints:
                continue
            self._reported_data_element_disjoints.add(key)
            self._diagnose(
                f"data tag '#{data_name}' cannot be used by an element "
                f"with tag '{element_name}'",
                origin,
            )

    def _validate_data_tags(
        self,
        groups: Iterable[Iterable[T.Type]],
        origin: ASTNode,
    ) -> None:
        """Validate data tags during static analysis."""
        for group in groups:
            for typ in group:
                conflict = _disjoint_data_tags(typ, self.env.context)
                if conflict is None:
                    continue
                left, right = conflict
                self._diagnose(
                    f"data tags '#{left.text}' and '#{right.text}' cannot both apply",
                    origin,
                )
                return

    def _object_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> BranchSet:
        """Build the definition for object during static analysis."""
        if not self._validate_object_lifecycle(node):
            return BranchSet((branch.emit(TypedNode(node, None)),))
        if node.target is not None:
            if node.fields:
                self._diagnose(
                    "trait implementation blocks cannot declare fields",
                    node,
                )
                return BranchSet((branch.emit(TypedNode(node, None)),))
            target = T.normalize(node.target)
            if isinstance(target, T.NominalType):
                self.env.add_trait_impl(node.name, target.name)
            current = self._register_friendly_definitions(
                branch.emit(TypedNode(node, None)),
                node.name,
                node.definitions,
            )
            return BranchSet((current,))

        object_attributes = self._object_attributes(node.fields, node.generics)
        if object_attributes is None:
            return BranchSet((branch.emit(TypedNode(node, None)),))
        defaults = frozenset(field.name for field in node.fields if field.default)
        constructors = constructor_definitions(node.name, node.definitions)
        friendly_definitions = tuple(
            definition
            for definition in node.definitions
            if definition not in constructors
        )
        self._define_object_shape(
            node.name,
            node,
            object_attributes,
            defaults=defaults,
            synthesize_constructor=not constructors,
        )
        current = branch.emit(TypedNode(node, None))
        for constructor in constructors:
            current = self._register_constructor_definition(
                current,
                node,
                constructor,
                defaults,
            )
        current = self._register_friendly_definitions(
            current,
            node.name,
            friendly_definitions,
        )
        return BranchSet((current,))

    def _validate_object_lifecycle(self, node: ObjectNode) -> bool:
        """Return the Boolean result of validate object lifecycle during static analysis."""
        ok = True
        mustcall = _mustcall_methods(node.annotations)
        defined = {definition.name.text for definition in node.definitions}
        for method in mustcall:
            if method not in defined:
                self._diagnose(
                    f"@mustcall method '{method}' is not defined on {node.name}",
                    node,
                )
                ok = False
        destructor_name = f"~{node.name.text.rsplit('.', 1)[-1]}"
        destructors = [
            definition
            for definition in node.definitions
            if definition.name.text.startswith("~")
        ]
        for definition in destructors:
            if definition.name.text != destructor_name:
                self._diagnose(
                    f"destructor for {node.name} must be named '{destructor_name}'",
                    definition,
                )
                ok = False
            if definition.function.params:
                self._diagnose(
                    "destructors cannot declare explicit parameters", definition
                )
                ok = False
        return ok

    def _trait_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> BranchSet:
        """Build a trait and type-check its default methods against requirements."""
        self._define_trait_shape(node.name, node)
        trait = self.env.lookup_trait(node.name)
        self_type = _declared_nominal(node.name, node.generics)

        # Trait requirements are abstract, so expose receiver-specialized versions
        # only while checking default bodies. Keeping them out of the persistent
        # overload table ensures concrete implementations retain stable runtime
        # overload indexes.
        snapshots: dict[Symbol, tuple[list[T.Overload] | None, set[int] | None]] = {}
        for requirement in trait.requirements if trait is not None else ():
            name = requirement.name
            if name not in snapshots:
                snapshots[name] = (
                    list(self.env.overloads[name]) if name in self.env.overloads else None,
                    set(self.env.object_friendly_overloads[name])
                    if name in self.env.object_friendly_overloads
                    else None,
                )
            # Object-friendly elements receive their explicit arguments below
            # the receiver on the stack.  A default such as ``$self log``
            # therefore sees the requirement's arguments before ``self``.
            overload = replace(
                requirement.overload,
                params=(*requirement.overload.params, self_type),
                param_names=(*requirement.overload.param_names, None),
            )
            candidates = self.env.overloads.setdefault(name, [])
            index = len(candidates)
            candidates.append(overload)
            self.env.object_friendly_overloads.setdefault(name, set()).add(index)

        try:
            current = self._register_friendly_definitions(
                branch.emit(TypedNode(node, None)),
                node.name,
                node.definitions,
            )
        finally:
            for name, (overloads, friendly) in snapshots.items():
                if overloads is None:
                    self.env.overloads.pop(name, None)
                else:
                    self.env.overloads[name] = overloads
                if friendly is None:
                    self.env.object_friendly_overloads.pop(name, None)
                else:
                    self.env.object_friendly_overloads[name] = friendly
        return BranchSet((current,))

    def _variant_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> BranchSet:
        """Build the definition for variant during static analysis."""
        generic_constraints = _generic_constraints(
            node.generics,
            node.generic_variances,
            node.generic_constraints,
        )
        requirements = _trait_requirements(node)
        members: list[Symbol] = []
        for member in node.variants:
            member_name = _child_symbol(node.name, member.name)
            members.append(member_name)
            object_attributes = self._object_attributes(member.fields, node.generics)
            if object_attributes is None:
                return BranchSet((branch.emit(TypedNode(node, None)),))
            variant_type = _declared_nominal(node.name, node.generics)
            self._define_object_shape(
                member_name,
                node,
                object_attributes,
                result_type=variant_type,
                generic_constraints=generic_constraints,
            )
            self.env.define_overload(
                member.name,
                T.Overload(
                    params=tuple(attribute.typ for attribute in object_attributes),
                    returns=(variant_type,),
                    generic_constraints=generic_constraints,
                ),
            )
        self.env.define_variant(
            node.name,
            tuple(members),
            generics=node.generics,
            generic_variance=_declared_or_inferred_variance(
                node.generics,
                node.generic_variances,
                (),
                requirements,
            ),
            requirements=requirements,
        )
        if annotation_hooks.has_annotation(node.annotations, "errType"):
            self.env.add_trait_impl(node.name, Symbol("Err"))

        current = branch.emit(
            TypedNode(node, _declared_nominal(node.name, node.generics))
        )
        requirements_by_name = {
            requirement.name: requirement for requirement in requirements
        }
        for member, member_name in zip(node.variants, members, strict=True):
            definitions_by_name: dict[Symbol, list[DefineNode]] = {}
            for definition in member.definitions:
                definitions_by_name.setdefault(definition.name, []).append(definition)

            for requirement in requirements:
                implementations = definitions_by_name.get(requirement.name, ())
                if not implementations:
                    self._diagnose(
                        f"variant member '{member.name}' must implement element "
                        f"'{requirement.name}'",
                        member,
                    )
                elif len(implementations) > 1:
                    self._diagnose(
                        f"variant member '{member.name}' must implement element "
                        f"'{requirement.name}' exactly once",
                        member,
                    )

            for definition in member.definitions:
                current = self._register_variant_member_definition(
                    current,
                    node,
                    member_name,
                    definition,
                    requirements_by_name.get(definition.name),
                )

        return BranchSet((current,))

    def _register_variant_member_definition(
        self,
        branch: AnalysisBranch,
        variant: ObjectNode,
        owner: Symbol,
        definition: DefineNode,
        requirement: T.TraitRequirement | None,
    ) -> AnalysisBranch:
        """Register variant member definition during static analysis."""
        if requirement is None:
            return self._register_friendly_definition(branch, owner, definition)
        if not self._validate_annotations(definition.annotations, "define", definition):
            return branch.emit(TypedNode(definition, None))

        explicit_params = tuple(definition.function.params or ())
        if len(explicit_params) != len(requirement.overload.params):
            self._diagnose(
                f"variant member element '{definition.name}' must take "
                f"{len(requirement.overload.params)} explicit parameter(s), got "
                f"{len(explicit_params)}",
                definition,
            )
            return branch.emit(TypedNode(definition, None))

        contextual_params: list[FunctionParam] = []
        for source, required in zip(
            explicit_params,
            requirement.overload.params,
            strict=True,
        ):
            if source.typ is not None and not T.same(source.typ, required):
                self._diagnose(
                    f"variant member element '{definition.name}' parameter type "
                    f"{T.show(source.typ)} does not match required type "
                    f"{T.show(required)}",
                    definition,
                )
                return branch.emit(TypedNode(definition, None))
            contextual_params.append(replace(source, typ=required))

        if definition.function.returns is not None and (
            len(definition.function.returns) != len(requirement.overload.returns)
            or any(
                not T.same(actual, required)
                for actual, required in zip(
                    definition.function.returns,
                    requirement.overload.returns,
                    strict=False,
                )
            )
        ):
            self._diagnose(
                f"variant member element '{definition.name}' return signature "
                "does not match the variant extend declaration",
                definition,
            )
            return branch.emit(TypedNode(definition, None))

        self_type = _declared_nominal(owner, variant.generics)
        params = (FunctionParam(Symbol("self"), self_type), *contextual_params)
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            FunctionNode(
                params=params,
                body=definition.function.body,
                returns=requirement.overload.returns,
                where_clause=definition.function.where_clause,
                element_tags=definition.function.element_tags,
                annotations=definition.function.annotations,
                location=definition.function.location,
            ),
            definition.annotations,
        )
        function_node = replace(
            function_node,
            params=(replace(function_node.params[0], name=None),)
            + function_node.params[1:],
        )
        function_node = _genericize_function_node(
            function_node,
            (*variant.generics, *definition.generics),
        )
        function_node = replace(
            function_node,
            generics=(*variant.generics, *definition.generics),
            generic_variances=(
                *variant.generic_variances,
                *definition.generic_variances,
            ),
            generic_constraints=(
                *variant.generic_constraints,
                *definition.generic_constraints,
            ),
        )

        self._friendly_owners = self._friendly_owners + (owner,)
        try:
            result = self._analyse_function_literal(
                branch,
                function_node,
                initial_function_locals=((Symbol("self"), self_type),),
            )
        finally:
            self._friendly_owners = self._friendly_owners[:-1]
        if result is None:
            return branch.emit(TypedNode(definition, None))

        function, typed_branch = result
        [typing] = function.overloads
        if not isinstance(typing.overload, T.Overload):
            return branch.emit(TypedNode(definition, None))

        generic_constraints = (
            *_generic_constraints(
                variant.generics,
                variant.generic_variances,
                variant.generic_constraints,
            ),
            *_generic_constraints(
                definition.generics,
                definition.generic_variances,
                definition.generic_constraints,
            ),
        )
        concrete = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
            _with_generic_constraints(
                typing.overload,
                generic_constraints,
            ),
            definition.annotations,
        )
        variant_type = _declared_nominal(variant.name, variant.generics)
        exposed = replace(
            concrete,
            params=(variant_type, *requirement.overload.params),
            returns=requirement.overload.returns,
            param_names=(None, *requirement.overload.param_names),
            is_multi=True,
        )
        self.env.define_overload(
            definition.name,
            exposed,
            object_friendly=True,
        )
        self.env.define_overload(
            Symbol(f"{owner}::{definition.name}"),
            replace(concrete, is_multi=False),
        )
        return typed_branch

    def _enum_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> BranchSet:
        """Build the definition for enum during static analysis."""
        value_type = T.V(node.generics[0].text) if node.generics else None
        members = tuple(
            T.EnumMemberDefinition(
                _child_symbol(node.name, member.name),
                value_type,
                bool(member.value),
            )
            for member in node.enum_members
        )
        self.env.define_enum(node.name, members, value_type=value_type)
        return BranchSet(
            (
                branch.emit(
                    TypedNode(node, _declared_nominal(node.name, node.generics))
                ),
            )
        )

    def _object_attribute(self, field: ObjectFieldNode) -> T.ObjectAttribute | None:
        """Compute object attribute during static analysis."""
        if field.typ is not None:
            typ = field.typ
        elif field.default:
            diagnostics_before = len(self.diagnostics)
            outputs = self.analyse_scoped_block(
                BranchSet((AnalysisBranch(input_mode=InputMode.TOP_LEVEL),)),
                field.default,
            )
            types = tuple(output.stack[-1] for output in outputs if output.stack)
            if not types:
                if outputs or len(self.diagnostics) == diagnostics_before:
                    self._diagnose(
                        f"default for field '{field.name}' must leave a value",
                        field,
                    )
                return None
            typ = T.U(*types)
        else:
            self._diagnose(f"field '{field.name}' needs a type", field)
            return None
        return T.ObjectAttribute(
            field.name,
            typ,
            field.access,
            has_default=bool(field.default),
        )

    def _object_attributes(
        self,
        fields: tuple[ObjectFieldNode, ...],
        generics: tuple[Symbol, ...],
    ) -> tuple[T.ObjectAttribute, ...] | None:
        """Compute object attributes during static analysis."""
        attributes = tuple(self._object_attribute(field) for field in fields)
        if any(attribute is None for attribute in attributes):
            return None
        return tuple(
            _genericize_attribute(attribute, generics)
            for attribute in attributes
            if attribute is not None
        )

    def _define_object_shape(
        self,
        name: Symbol,
        node: ObjectNode,
        attributes: tuple[T.ObjectAttribute, ...],
        *,
        defaults: frozenset[Symbol] = frozenset(),
        result_type: T.Type | None = None,
        generic_constraints: tuple[T.GenericConstraint, ...] | None = None,
        synthesize_constructor: bool = True,
    ) -> None:
        """Record object shape during static analysis."""
        constraints = (
            _generic_constraints(
                node.generics,
                node.generic_variances,
                node.generic_constraints,
            )
            if generic_constraints is None
            else generic_constraints
        )
        self.env.define_object(
            name,
            attributes,
            generics=node.generics,
            generic_variance=_declared_or_inferred_variance(
                node.generics,
                node.generic_variances,
                attributes,
                (),
            ),
        )
        if annotation_hooks.has_annotation(node.annotations, "errType"):
            self.env.add_trait_impl(name, Symbol("Err"))
        if synthesize_constructor:
            self.env.define_constructor(
                name,
                attributes,
                defaults=defaults,
                result_type=result_type or _declared_nominal(name, node.generics),
                generic_constraints=constraints,
            )
        else:
            self.env.define_constructor_metadata(
                name,
                attributes,
                defaults=defaults,
                generic_constraints=constraints,
            )

    def _define_trait_shape(self, name: Symbol, node: ObjectNode) -> None:
        """Record trait shape, including requirements inherited from a parent."""
        requirements = list(_trait_requirements(node))
        parent_name: Symbol | None = None
        if node.target is not None:
            target = T.normalize(node.target)
            if isinstance(target, T.NominalType):
                parent_name = target.name
                parent = self.env.lookup_trait(parent_name)
                if parent is not None:
                    for requirement in parent.requirements:
                        if requirement not in requirements:
                            requirements.append(requirement)

        all_requirements = tuple(requirements)
        self.env.define_trait(
            name,
            generics=node.generics,
            generic_variance=_declared_or_inferred_variance(
                node.generics,
                node.generic_variances,
                (),
                all_requirements,
            ),
            requirements=all_requirements,
        )
        if parent_name is not None:
            self.env.add_trait_parent(name, parent_name)

    def _register_constructor_definition(
        self,
        branch: AnalysisBranch,
        owner_node: ObjectNode,
        definition: DefineNode,
        defaults: frozenset[Symbol],
    ) -> AnalysisBranch:
        """Register constructor definition during static analysis."""
        if not self._validate_annotations(definition.annotations, "define", definition):
            return branch

        owner = owner_node.name
        owner_definition = self.env.lookup_object(owner)
        self_type = _declared_nominal(
            owner,
            owner_definition.generics if owner_definition is not None else (),
        )
        if definition.function.returns is not None and (
            len(definition.function.returns) != 1
            or not T.same(
                _genericize_type(
                    definition.function.returns[0],
                    (*owner_node.generics, *definition.generics),
                ),
                self_type,
            )
        ):
            self._diagnose(
                f"constructor '{owner}' must return {T.show(self_type)}",
                definition,
            )
            return branch

        body = prepare_constructor_body(definition.function.body)
        initialized = definitely_initialized_fields(body, defaults)
        missing = tuple(
            field.name for field in owner_node.fields if field.name not in initialized
        )
        if missing:
            self._diagnose(
                f"constructor '{owner}' does not initialize field(s): "
                + ", ".join(str(name) for name in missing),
                definition,
            )
            return branch

        function_node = FunctionNode(
            params=definition.function.params,
            body=(*body, GetVariableNode(Symbol("self"), location=definition.location)),
            returns=(self_type,),
            where_clause=definition.function.where_clause,
            element_tags=definition.function.element_tags,
            annotations=definition.function.annotations,
            element_tags_explicit=definition.function.element_tags_explicit,
            companion_tags_allowed=definition.function.companion_tags_allowed,
            location=definition.function.location,
        )
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            function_node,
            definition.annotations,
        )
        function_node = _genericize_function_node(
            function_node,
            (*owner_node.generics, *definition.generics),
        )
        function_node = replace(
            function_node,
            generics=(*owner_node.generics, *definition.generics),
            generic_variances=(
                *owner_node.generic_variances,
                *definition.generic_variances,
            ),
            generic_constraints=(
                *owner_node.generic_constraints,
                *definition.generic_constraints,
            ),
        )
        self._validate_function_element_tags(function_node, definition)
        self._friendly_owners = self._friendly_owners + (owner,)
        try:
            result = self._analyse_function_literal(
                branch,
                function_node,
                initial_function_locals=((Symbol("self"), self_type),),
            )
        finally:
            self._friendly_owners = self._friendly_owners[:-1]
        if result is None:
            return branch

        function, typed_branch = result
        generic_constraints = (
            *_generic_constraints(
                owner_node.generics,
                owner_node.generic_variances,
                owner_node.generic_constraints,
            ),
            *_generic_constraints(
                definition.generics,
                definition.generic_variances,
                definition.generic_constraints,
            ),
        )
        for typing in function.overloads:
            if not isinstance(typing.overload, T.Overload):
                continue
            overload = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
                _with_generic_constraints(typing.overload, generic_constraints),
                definition.annotations,
            )
            if overload not in self.env.overloads_for(owner):
                existing = self.env.overloads_for(owner)
                if existing and len(overload.params) != len(existing[0].params):
                    self._diagnose(
                        f"constructor overloads for '{owner}' must all take "
                        f"{len(existing[0].params)} inputs, got "
                        f"{len(overload.params)}",
                        definition,
                    )
                    continue
                self.env.define_overload(owner, overload)
        return typed_branch

    def _register_friendly_definition(
        self,
        branch: AnalysisBranch,
        owner: Symbol,
        definition: DefineNode,
    ) -> AnalysisBranch:
        """Register friendly definition during static analysis."""
        if not self._validate_annotations(definition.annotations, "define", definition):
            return branch.emit(TypedNode(definition, None))
        owner_definition = self.env.lookup_object(owner)
        self_type = _declared_nominal(
            owner,
            owner_definition.generics if owner_definition is not None else (),
        )
        params = (FunctionParam(Symbol("self"), self_type),) + tuple(
            definition.function.params or ()
        )
        body = definition.function.body
        if annotation_hooks.has_annotation(definition.annotations, "self"):
            body = prepare_constructor_body(body)
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            FunctionNode(
                params=params,
                body=body,
                returns=definition.function.returns,
                where_clause=definition.function.where_clause,
                element_tags=definition.function.element_tags,
                annotations=definition.function.annotations,
                location=definition.function.location,
            ),
            definition.annotations,
        )
        function_node = replace(
            function_node,
            params=(replace(function_node.params[0], name=None),)
            + function_node.params[1:],
        )
        function_node = _genericize_function_node(function_node, definition.generics)
        self._friendly_owners = self._friendly_owners + (owner,)
        try:
            result = self._analyse_function_literal(
                branch,
                function_node,
                initial_function_locals=((Symbol("self"), self_type),),
            )
        finally:
            self._friendly_owners = self._friendly_owners[:-1]
        if result is None:
            return branch.emit(TypedNode(definition, None))
        function, typed_branch = result
        generic_constraints = _generic_constraints(
            definition.generics,
            definition.generic_variances,
            definition.generic_constraints,
        )
        for name in (definition.name, Symbol(f"{owner}::{definition.name}")):
            object_friendly = name == definition.name
            for typing in function.overloads:
                if not isinstance(typing.overload, T.Overload):
                    continue
                self.env.define_overload(
                    name,
                    annotation_hooks.DEFAULT_REGISTRY.transform_overload(
                        _with_generic_constraints(typing.overload, generic_constraints),
                        definition.annotations,
                    ),
                    object_friendly=object_friendly,
                )
        return typed_branch

    def _load_import_definitions(
        self,
        spec: ImportSpec,
    ):
        """Load import definitions during static analysis."""
        try:
            exports = self.module_loader.load(
                spec.path,
                current_file=self.source_file,
            )
            return exports, spec, import_definitions(exports, spec)
        except ModuleLoadError:
            if spec.components or len(spec.path.parts) < 2:
                raise
            module_path = ImportPath(spec.path.parts[:-1], spec.path.root)
            component = ImportComponent(Symbol(spec.path.parts[-1]))
            split_spec = ImportSpec(module_path, spec.alias, (component,))
            exports = self.module_loader.load(
                split_spec.path,
                current_file=self.source_file,
            )
            return exports, split_spec, import_definitions(exports, split_spec)

    def _register_imported_definition(
        self,
        name: Symbol,
        typed_node: TypedFunctionNode,
        runtime_name: Symbol,
    ) -> None:
        """Register imported definition during static analysis."""
        self.env.bind_runtime_name(name, runtime_name)
        declared = tuple(
            typing.overload
            for typing in typed_node.overloads
            if isinstance(typing.overload, T.Overload)
        )
        for selected in _callable_overloads(typed_node.typ):
            overload = next(
                (
                    candidate
                    for candidate in declared
                    if candidate.params == selected.params
                    and candidate.returns == selected.returns
                ),
                selected,
            )
            self.env.define_overload(name, overload)

    def _register_imported_object(
        self,
        obj,
        runtime_name: Symbol,
    ) -> None:
        """Register imported object during static analysis."""
        self.env.bind_runtime_name(obj.name, runtime_name)
        node = obj.typed.node
        if not isinstance(node, ObjectNode):
            return
        kind = node.kind.text
        if kind == "trait":
            self._define_trait_shape(obj.name, node)
            return

        if kind != "object" or node.target is not None:
            return

        object_attributes = self._object_attributes(node.fields, node.generics)
        if object_attributes is None:
            return
        defaults = frozenset(field.name for field in node.fields if field.default)
        constructors = constructor_definitions(obj.name, node.definitions)
        friendly_definitions = tuple(
            definition
            for definition in node.definitions
            if definition not in constructors
        )
        self._define_object_shape(
            obj.name,
            node,
            object_attributes,
            defaults=defaults,
            synthesize_constructor=not constructors,
        )
        current = AnalysisBranch()
        for constructor in constructors:
            current = self._register_constructor_definition(
                current,
                node,
                constructor,
                defaults,
            )
        if obj.import_friendly:
            self._register_friendly_definitions(
                current,
                obj.name,
                friendly_definitions,
            )

    def _register_friendly_definitions(
        self,
        branch: AnalysisBranch,
        owner: Symbol,
        definitions: tuple[DefineNode, ...],
    ) -> AnalysisBranch:
        """Register friendly definitions during static analysis."""
        current = branch
        for definition in definitions:
            current = self._register_friendly_definition(current, owner, definition)
        return current

    @register(ElementNode)
    def _element(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Analyse a `ElementNode` node and return the surviving branches."""
        overloads = self.env.overloads_for(node.name)
        if not overloads:
            self._diagnose(f"unknown element '{node.name}'", node)
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
        if node.modifier_args and not _modifier_arity_matches(overloads, modifier_args):
            self._diagnose(
                f"element '{node.name}' expects "
                f"{_show_modifier_counts(overloads)} ':' function argument(s), "
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
            no_match_message = (
                f"no overloads for element '{node.name}' match explicit call syntax"
            )
            ambiguous_message = (
                f"ambiguous overloads for element '{node.name}' "
                "with explicit call syntax"
            )
        else:
            no_match_message = (
                f"no overloads for element '{node.name}' match stack "
                f"{_show_stack(stack_before)}; available overloads: "
                f"{_show_overloads(overloads)}"
            )
            ambiguous_message = (
                f"ambiguous overloads for element '{node.name}' with stack "
                f"{_show_stack(stack_before)}"
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

    def element_argument_sources(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
        overloads: tuple[T.Overload, ...],
        modifiers: tuple[ModifierArgumentAnalysis, ...],
    ) -> tuple[list[ElementArguments], list[AnalysisBranch]]:
        """Enumerate valid parameter-ordered argument sources for one element overload."""
        if node.call_args:
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

    def select_call_winners(
        self,
        *,
        candidates: Iterable[CallCandidate],
        branch: AnalysisBranch,
        node: ASTNode,
        no_match_message: str,
        ambiguous_message: str,
    ) -> tuple[CallCandidate, ...] | None:
        """Select the most specific viable call candidates and diagnose ambiguity."""
        winners = _collapse_equivalent_call_winners(
            _collapse_equivalent_friendly_multidispatch_winners(
                _best_candidates(candidates, branch)
            )
        )
        if not winners:
            self._diagnose(no_match_message, node)
            return None
        if (
            len(winners) > 1
            and branch.input_mode is not InputMode.INFER_INPUTS
            and not _winners_specialize_inputs(winners, branch)
        ):
            self._diagnose(
                f"{ambiguous_message}; candidates: {_show_applied_overloads(winners)}",
                node,
            )
            return None
        return winners

    def stack_element_arguments(
        self,
        branch: AnalysisBranch,
        overloads: tuple[T.Overload, ...],
        modifiers: tuple[ModifierArgumentAnalysis, ...],
    ) -> tuple[list[ElementArguments], list[AnalysisBranch]]:
        """Source an element overload's ordinary arguments from the branch stack."""
        sources: list[ElementArguments] = []
        for overload_index, overload in enumerate(overloads):
            for args, popped, ordered_modifiers in _source_element_arguments(
                branch,
                overload,
                modifiers,
                self.env.context,
                analyser=self,
            ):
                sources.append(
                    ElementArguments(
                        overload=overload,
                        overload_index=overload_index,
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
        for overload_index, overload in enumerate(overloads):
            prepared = _prepare_element_call_branches(
                branch,
                overload,
                node.call_args,
                bool(node.modifier_args),
                self,
            )
            for preparation in prepared:
                if preparation.branch.terminal:
                    terminal.append(preparation.branch)
                    continue
                for args, popped, ordered_modifiers in _source_element_arguments(
                    preparation.branch,
                    overload,
                    modifiers,
                    self.env.context,
                    preparation.call_arg_order,
                    analyser=self,
                ):
                    sources.append(
                        ElementArguments(
                            overload=overload,
                            overload_index=overload_index,
                            arguments=args,
                            branch=popped,
                            modifiers=ordered_modifiers,
                            call_arg_order=preparation.call_arg_order,
                        )
                    )
        return sources, terminal

    def element_call_candidates(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
        sources: Iterable[ElementArguments],
    ) -> list[CallCandidate]:
        """Build viable typed candidates for an explicit element call."""
        candidates: list[CallCandidate] = []
        for source in sources:
            candidate = _apply_overload_to_branch(
                source.overload,
                source.arguments,
                source.branch,
                self.env.context,
                self.env,
                node.disambiguation,
                self,
            )
            if candidate is None:
                continue

            applied = _apply_tag_overlay(
                node.name,
                source.arguments,
                candidate.applied,
                self.env.context,
                self.env,
            )
            selected_is_friendly = self.env.overload_is_object_friendly(
                node.name,
                source.overload_index,
            )
            dispatch_overloads = tuple(
                overload
                for index, overload in enumerate(overloads)
                if self.env.overload_is_object_friendly(node.name, index)
                == selected_is_friendly
            )
            applied = _mark_multidispatch(
                applied,
                dispatch_overloads,
                self.env.context,
            )
            candidates.append(
                CallCandidate(
                    applied=applied,
                    branch=candidate.branch,
                    modifiers=source.modifiers,
                    call_arg_order=source.call_arg_order,
                    overload_index=source.overload_index,
                    dispatch_priority=(
                        0
                        if selected_is_friendly
                        else 1
                    ),
                )
            )
        return candidates

    def commit_element_candidate(
        self,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
        candidate: CallCandidate,
    ) -> AnalysisBranch | None:
        """Emit the typed node for a selected element-call candidate."""
        overload = candidate.applied.overload
        if overload.annotation_error is not None:
            self._diagnose(overload.annotation_error, node)
            return None

        if overload.annotation_warning is not None:
            self._warn(overload.annotation_warning, node)

        actual_returns = annotation_hooks.annotated_element_returns(
            node,
            candidate.applied.actual_returns,
        )
        extension = self._analyse_element_extension(
            node.extension,
            candidate.applied,
            candidate.branch,
        )
        if node.extension is not None and extension is None:
            return None
        return candidate.branch.push(*actual_returns).emit(
            TypedElementNode(
                node,
                _returns_result_type(actual_returns),
                candidate.applied,
                (
                    candidate.overload_index
                    if candidate.overload_index is not None
                    else _overload_index(overloads, overload)
                ),
                _specialize_modifier_arguments(
                    candidate.applied,
                    candidate.modifiers,
                    self.env.context,
                ),
                candidate.call_arg_order,
                candidate.callable_overload_index,
                extension,
                self.env.runtime_name_for(node.name),
            )
        )

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
            returns = _single_function_return(typed)
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
                returns = _consistent_function_returns(typed)
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
            selector_arity = _extension_selector_arity(typed)
            if selector_arity != len(applied.params):
                self._diagnose(
                    "extend selector arity must match the target element arity",
                    extension,
                )
                return None
            returned = _single_function_return(typed)
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

    def _call_element_call(
        self,
        branch: AnalysisBranch,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
    ) -> BranchSet:
        """Compute call element call during static analysis."""
        if node.modifier_args:
            self._diagnose("element 'call' does not accept ':' arguments", node)
            return BranchSet()
        if any(arg.name is not None or arg.placeholder for arg in node.call_args):
            self._diagnose(
                "element 'call' explicit arguments must be positional",
                node,
            )
            return BranchSet()

        current = BranchSet((branch,))
        for arg in node.call_args:
            current = self.analyse_scoped_block(current, arg.value)
            if not current:
                return BranchSet()

        terminal, current = _split_terminal_branches(current)
        if not current:
            return terminal

        call_arg_count = len(node.call_args)
        candidates: list[CallCandidate] = []
        for arg_branch in current:
            candidates.extend(
                self.call_element_candidates_for_branch(
                    node,
                    overloads[0],
                    arg_branch,
                    call_arg_count,
                )
            )

        winners = self.select_call_winners(
            candidates=candidates,
            branch=branch,
            node=node,
            no_match_message=(
                "no overloads for element 'call' match explicit call syntax"
            ),
            ambiguous_message=(
                "ambiguous overloads for element 'call' with explicit call syntax"
            ),
        )
        if winners is None:
            return terminal

        results: list[AnalysisBranch] = list(terminal.branches)
        for candidate in winners:
            extension = self._analyse_element_extension(
                node.extension,
                candidate.applied,
                candidate.branch,
            )
            if node.extension is not None and extension is None:
                continue
            results.append(
                candidate.branch.push(*candidate.applied.actual_returns).emit(
                    TypedElementNode(
                        node,
                        _returns_result_type(candidate.applied.actual_returns),
                        candidate.applied,
                        0,
                        (),
                        candidate.call_arg_order,
                        candidate.callable_overload_index,
                        extension,
                    )
                )
            )
        return BranchSet.collect(results)

    def call_element_candidates_for_branch(
        self,
        node: ElementNode,
        call_overload: T.Overload,
        arg_branch: AnalysisBranch,
        call_arg_count: int,
    ) -> list[CallCandidate]:
        """Build callable-value candidates for the built-in `call` element."""
        if len(arg_branch.stack) < call_arg_count:
            return []

        call_values = arg_branch.stack.items[-call_arg_count:] if call_arg_count else ()
        base_stack = arg_branch.stack.items[:-call_arg_count]
        explicit_function_order = (
            (*range(1, call_arg_count), 0) if call_arg_count > 1 else ()
        )
        candidates = _call_element_candidates(
            arg_branch,
            call_overload,
            call_values[0],
            call_values[1:],
            base_stack,
            explicit_function_order,
            node.disambiguation,
            self.env.context,
        )
        if candidates or not base_stack:
            return candidates

        return _call_element_candidates(
            arg_branch,
            call_overload,
            base_stack[-1],
            call_values,
            base_stack[:-1],
            (),
            node.disambiguation,
            self.env.context,
        )

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
                if (item_result := _list_item_analysis(branch, output)) is not None
            )
            if not options:
                if item_outputs or len(self.diagnostics) == diagnostics_before:
                    self._diagnose(message, node)
                return None
            item_options.append(options)
        return tuple(item_options)

    def _analyse_unfold_body_function(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
    ) -> FunctionAnalysis | None:
        """Analyse unfold body function during static analysis."""
        if node.params is None:
            analysed = self._analyse_function_literal(outer, node)
            return None if analysed is None else analysed[0]

        params = _declared_params(node)
        body_params = tuple(_parameter_value_type(param) for param in params)
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(node.params, body_params, strict=True)
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=_function_capture_source(outer),
        )
        initial = AnalysisBranch(
            inputs=body_params,
            variables=variables,
            input_mode=(
                InputMode.CYCLE_EXPLICIT_PARAMS if body_params else InputMode.NILADIC
            ),
            cycle_params=body_params,
            origin=outer.origin,
        )
        function_analyser = self._child_analyser(self.env.lexical_child_scope())
        final = function_analyser.analyse_block(BranchSet((initial,)), node.body)
        signatures = self._function_signatures(node, final)
        analysis = _function_analysis_from_signatures(signatures)
        if analysis is None:
            self.diagnostics.extend(function_analyser.diagnostics)
            self.warnings.extend(function_analyser.warnings)
            return None
        self.warnings.extend(function_analyser.warnings)
        return analysis

    @register(MatchNode)
    def _match(
        self,
        node: MatchNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Analyse a `MatchNode` node and return the surviving branches."""
        if not node.cases:
            self._diagnose("match requires at least one case", node)
            return BranchSet()

        arity = _match_arity(node)
        if arity is None:
            self._diagnose("match cases must match the same number of values", node)
            return BranchSet()
        if arity == 0:
            self._diagnose("match requires at least one pattern per case", node)
            return BranchSet()

        subject_params = tuple(
            reversed(
                tuple(
                    _match_subject_pattern_type(branch, node, index)
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
        if not self._match_is_exhaustive(subject_types, node):
            return BranchSet()

        joined: AnalysisBranch | None = None
        typed_case_bodies: list[tuple[ASTNode | TypedNode, ...]] = []
        subject_variables = _match_subject_variables(branch, arity)
        previous_patterns: list[tuple[MatchPatternNode, ...]] = []
        for case in node.cases:
            case_variables = _match_case_variables(
                body_input.variables,
                case.patterns,
                subject_types,
            )
            if subject_variables:
                case_variables = _refine_match_subject_variables(
                    case_variables,
                    subject_variables,
                    case.patterns,
                    subject_types,
                    tuple(previous_patterns),
                    self.env.context,
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
                _typed_block(
                    case_outputs,
                    len(case_input.typed_body),
                    case.body,
                )
            )
            for output in case_outputs:
                candidate = _match_case_output(output, body_input, node)
                joined = _join_match_output(
                    original=branch,
                    baseline=body_input,
                    joined=joined,
                    candidate=candidate,
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
                        _returns_result_type(joined.stack.items),
                        case_bodies=tuple(typed_case_bodies),
                    )
                ),
            )
        )

    @register(TryNode)
    def _try(
        self,
        node: TryNode,
        branch: AnalysisBranch,
    ) -> BranchSet:
        """Analyse a `TryNode` node and return the surviving branches."""
        if not node.handlers:
            self._diagnose("try requires at least one handler", node)
            return BranchSet()

        body_outputs = self.analyse_scoped_block(BranchSet((branch,)), node.body)
        typed_body = _typed_block(
            body_outputs,
            len(branch.typed_body),
            node.body,
        )
        outputs: list[AnalysisBranch] = list(body_outputs.branches)
        typed_handler_bodies: list[tuple[ASTNode | TypedNode, ...]] = []
        for handler in node.handlers:
            if handler.typ is not None and not T.assignable(
                handler.typ,
                T.N(Symbol("Fault")),
                self.env.context,
            ):
                self._diagnose(
                    f"try handler type {T.show(handler.typ)} does not implement Fault",
                    handler,
                )
            handler_outputs = self.analyse_scoped_block(
                BranchSet((branch,)),
                handler.body,
            )
            typed_handler_bodies.append(
                _typed_block(
                    handler_outputs,
                    len(branch.typed_body),
                    handler.body,
                )
            )
            for output in handler_outputs:
                if output.inputs != branch.inputs:
                    self._diagnose("try handlers inferred different inputs", handler)
                    continue
                outputs.append(_try_handler_output(output, branch, handler))

        if not outputs:
            return BranchSet()

        joined: AnalysisBranch | None = None
        for output in outputs:
            joined = _join_try_output(branch, joined, output)
            if joined is None:
                self._diagnose("try branches inferred different inputs", node)
                return BranchSet()
        if joined is None:
            return BranchSet()
        return BranchSet(
            (
                joined.emit(
                    TypedTryNode(
                        node,
                        _returns_result_type(joined.stack.items),
                        body=typed_body,
                        handler_bodies=tuple(typed_handler_bodies),
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
        """Return the Boolean result of match guards are valid during static analysis."""
        guards = tuple(_match_pattern_guards(patterns, subject_types))
        for guard, subject_type in guards:
            diagnostics_before = len(self.diagnostics)
            guard_input = AnalysisBranch(
                stack=T.TypeStack((subject_type,)),
                variables=BranchVariables(),
                input_mode=InputMode.TOP_LEVEL,
            )
            outputs = self.analyse_scoped_block(BranchSet((guard_input,)), guard)
            terminal, outputs = _split_terminal_branches(outputs)
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

    def _match_is_exhaustive(
        self,
        subject_types: tuple[T.Type, ...],
        node: MatchNode,
    ) -> bool:
        """Return the Boolean result of match is exhaustive during static analysis."""
        if any(
            case.is_default or _is_default_match_case(case.patterns)
            for case in node.cases
        ):
            return True
        if len(subject_types) != 1:
            self._diagnose(
                "match without default requires one enum or variant value",
                node,
            )
            return False
        subject_type = subject_types[0]
        closed_name = _nominal_name(subject_type)
        if closed_name is None:
            self._diagnose("match without default requires enum or variant value", node)
            return False
        expected = _closed_match_members(self.env, closed_name)
        if expected is None:
            self._diagnose("match without default requires enum or variant value", node)
            return False
        covered = {
            resolved
            for case in node.cases
            for pattern_type in _match_case_pattern_types(case.patterns)
            if (resolved := _resolve_closed_member(expected, pattern_type)) is not None
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

        base = _anonymous_type_var(branch, 1)
        field_type = _anonymous_type_var(branch, 2)
        present_type = T.Row(base, T.Field(name, field_type))
        receiver_type = T.optional(present_type) if optional_safe else present_type
        result_type = _optional_access_result_type(field_type) if optional_safe else field_type
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

        payload_type = _strict_optional_payload_type(receiver_type)
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
        return _optional_access_result_type(field_type), refined_receiver

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
            existing = _row_field_type(receiver_type, name)
            if write:
                return (existing, None) if existing is not None else (None, None)
            if existing is not None:
                return existing, None
            field_type = _anonymous_type_var(branch, 1)
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
            field_type = _anonymous_type_var(branch, 1)
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
            return _substitute_branch_type(attribute.typ, substitution), None

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

    def _analyse_function_literal(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
        *,
        initial_function_locals: tuple[tuple[Symbol, T.Type], ...] = (),
    ) -> tuple[FunctionAnalysis, AnalysisBranch] | None:
        """Analyse function literal during static analysis."""
        node = _contextualize_function_empty_returns(node)
        if node.params is not None and any(
            _is_call_site_checked_param(param.typ) for param in node.params
        ):
            return self._call_site_checked_function(outer, node), outer

        top_level_captures = _top_level_assignment_capture_nodes(outer, node)
        if top_level_captures:
            for capture in top_level_captures:
                self._diagnose(
                    f"cannot capture top-level assignment '{capture.name}'",
                    capture,
                )
            return None

        params = _declared_params(node)
        body_params = tuple(
            _parameter_value_type(_anonymous_trait_subject_view(param))
            for param in params
        )
        if node.params is None:
            mode = InputMode.INFER_INPUTS
        elif not node.params:
            mode = InputMode.NILADIC
        else:
            mode = InputMode.CYCLE_EXPLICIT_PARAMS
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(node.params or (), body_params, strict=True)
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=_function_capture_source(outer),
        )
        for local_name, local_type in initial_function_locals:
            write = variables.write(local_name, local_type, ctx=self.env.context)
            if write.variables is None:
                diagnostic = write.diagnostic or f"cannot define '{local_name}'"
                self._diagnose(diagnostic, node)
                return None
            variables = write.variables
        recursive_overload = annotation_hooks.recursive_overload(node, params)
        if annotation_hooks.has_annotation(node.annotations, "recursive"):
            if recursive_overload is None:
                self._diagnose(
                    "@recursive requires explicit parameter and return types",
                    node,
                )
                return None
            write = variables.write(
                Symbol("this"),
                T.Fn(
                    recursive_overload.params,
                    recursive_overload.returns,
                    recursive_overload.element_tags,
                ),
                block_local=False,
            )
            if write.variables is None:
                return None
            variables = write.variables
        for name in _static_body_variable_names(node):
            write = variables.write(
                name,
                T.Number,
                block_local=False,
            )
            if write.variables is None:
                variables = BranchVariables.from_parameters(
                    named_params,
                    captures=_function_capture_source(outer),
                )
            else:
                variables = write.variables
        initial_stack = T.TypeStack(
            tuple(
                typ
                for param, typ in zip(node.params or (), body_params, strict=True)
                if param.name is None
            )
            if mode is InputMode.CYCLE_EXPLICIT_PARAMS
            else ()
        )
        initial = AnalysisBranch(
            stack=initial_stack,
            inputs=body_params if mode is not InputMode.INFER_INPUTS else (),
            variables=variables,
            input_mode=mode,
            cycle_params=body_params if mode is InputMode.CYCLE_EXPLICIT_PARAMS else (),
            origin=outer.origin,
        )

        generic_constraints = _generic_constraints(
            node.generics,
            node.generic_variances,
            node.generic_constraints,
        )
        structural_overloads = _anonymous_trait_overloads(
            *params,
            *(constraint.bound for constraint in generic_constraints),
        )
        function_env = self.env.lexical_child_scope()
        for name, overload in structural_overloads:
            function_env.overloads.setdefault(name, []).append(overload)
        if recursive_overload is not None and annotation_hooks.has_annotation(
            node.annotations,
            "recursive",
        ):
            function_env.define_overload(Symbol("this"), recursive_overload)
        function_analyser = self._child_analyser(function_env)
        final = function_analyser.analyse_block(BranchSet((initial,)), node.body)
        signatures = self._function_signatures(node, final)
        analysis = _function_analysis_from_signatures(signatures)
        if analysis is None:
            if (
                node.params is not None
                and any(param.typ is None for param in node.params)
                and not function_analyser.diagnostics
            ):
                self.warnings.extend(function_analyser.warnings)
                return self._call_site_checked_function(outer, node), outer
            self.diagnostics.extend(function_analyser.diagnostics)
            self.warnings.extend(function_analyser.warnings)
            return None
        self.warnings.extend(function_analyser.warnings)
        return analysis, outer

    def _call_site_checked_function(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
    ) -> FunctionAnalysis:
        """Compute call site checked function during static analysis."""
        params = _declared_params(node)
        overload = _function_overload(
            node,
            params=params,
            returns=(),
            call_site_body=(outer, node),
        )
        typ = T.Overloads(overload)
        return FunctionAnalysis(
            typ,
            (FunctionOverloadTyping(T.Fn(params, ()), (), overload),),
        )

    def _analyse_function_at_call_site(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
        call_params: tuple[T.Type, ...],
    ) -> FunctionAnalysis | None:
        """Analyse function at call site during static analysis."""
        declared = tuple(node.params or ())
        if len(call_params) < len(declared):
            return None
        substituted_params = _call_site_substituted_params(
            declared,
            call_params[-len(declared) :] if declared else (),
            self.env.context,
        )
        if substituted_params is None:
            return None
        call_site_node = FunctionNode(
            params=substituted_params,
            body=node.body,
            returns=node.returns,
            where_clause=node.where_clause,
            element_tags=node.element_tags,
            element_tags_explicit=node.element_tags_explicit,
            companion_tags_allowed=node.companion_tags_allowed,
            location=node.location,
        )
        explicit_count = len(declared)
        stack_params = call_params[:-explicit_count] if explicit_count else call_params
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(
                substituted_params,
                call_params[-explicit_count:] if explicit_count else (),
                strict=False,
            )
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=_function_capture_source(outer),
        )
        initial = AnalysisBranch(
            stack=T.TypeStack(stack_params),
            inputs=call_params,
            variables=variables,
            input_mode=InputMode.NILADIC,
            origin=outer.origin,
        )
        function_analyser = self._child_analyser(self.env.lexical_child_scope())
        final = function_analyser.analyse_block(BranchSet((initial,)), node.body)
        signatures = self._function_signatures(call_site_node, final)
        return _function_analysis_from_signatures(signatures)

    def _function_signatures(
        self,
        node: FunctionNode,
        branches: BranchSet,
    ) -> dict[T.Overload, tuple[TypedNode, ...]]:
        """Build the signatures for function during static analysis."""
        signatures: dict[T.Overload, tuple[TypedNode, ...]] = {}
        surviving_element_tags = frozenset(
            tag for branch in branches for tag in branch.element_tags
        )
        surviving_data_element_uses = frozenset(
            use for branch in branches for use in branch.data_element_uses
        )
        self._validate_recorded_data_element_uses(
            surviving_data_element_uses,
            node,
        )
        for branch in branches:
            if branch.failed:
                continue
            refined = self._function_returns(node, branch)
            if refined is None:
                continue
            returns, branch = refined
            body_element_tags = surviving_element_tags
            final_element_tags = _final_function_element_tags(
                node,
                body_element_tags,
                self.env,
            )
            self._validate_inferred_element_tags(
                node,
                body_element_tags,
                final_element_tags,
            )
            declared_params = _declared_params(node)
            inputs = (
                declared_params
                if node.params is not None
                and any(_contains_anonymous_trait(param) for param in declared_params)
                else branch.inputs
            )
            inputs = _restore_exact_parameter_markers(declared_params, inputs)
            self._validate_data_element_tag_disjoints(
                inputs,
                final_element_tags,
                node,
            )
            signature = _function_overload(
                node,
                params=inputs,
                returns=returns,
                where_clause=node.where_clause,
                element_tags=final_element_tags,
            )
            signatures.setdefault(signature, branch.typed_body)

        if len(signatures) <= 1:
            return signatures
        return {
            signature: body
            for signature, body in signatures.items()
            if not _has_never_return(signature)
        }

    def _function_returns(
        self,
        node: FunctionNode,
        branch: AnalysisBranch,
    ) -> tuple[tuple[T.Type, ...], AnalysisBranch] | None:
        """Determine the return types for function during static analysis."""
        if annotation_hooks.has_annotation(node.annotations, "returnAll"):
            return branch.stack.items, branch
        if node.returns is None:
            return (branch.stack.items[-1:] if branch.stack else ()), branch

        checked_returns = tuple(_return_value_shape(typ) for typ in node.returns)
        expected = T.TypeStack(checked_returns)
        actual_returns = _stack_returns(branch.stack, expected)
        if len(actual_returns) != len(node.returns):
            return None
        substitution = _branch_argument_substitution(
            actual_returns,
            checked_returns,
            self.env.context,
        )
        if (
            substitution is None
            and node.where_clause
            and _contains_rank_var(node.returns)
        ):
            return node.returns, branch
        if substitution is not None:
            branch = _specialize_branch_arguments(branch, substitution)
        if not _stack_assignable(branch.stack, expected, self.env.context):
            if node.where_clause and _contains_rank_var(node.returns):
                return node.returns, branch
            return None
        return node.returns, branch

    def _validate_annotations(
        self,
        annotations: tuple[ASTNode, ...],
        target: str,
        node: ASTNode,
    ) -> bool:
        """Return the Boolean result of validate annotations during static analysis."""
        diagnostics = annotation_hooks.DEFAULT_REGISTRY.validate(
            annotations,
            target,
            node,
        )
        for diagnostic in diagnostics:
            self._diagnose(diagnostic, node)
        return not diagnostics

    def _diagnose(self, message: str, node: ASTNode | None = None) -> None:
        """Update diagnose state during static analysis."""
        diagnostic = _diagnostic_message(message, node)
        if diagnostic not in self.diagnostics:
            self.diagnostics.append(diagnostic)

    def _warn(self, message: str, node: ASTNode | None = None) -> None:
        """Update warn state during static analysis."""
        self.warnings.append(_diagnostic_message(message, node))


@register(NumberLiteralNode)
def _number_literal(
    self: Analyser,
    node: NumberLiteralNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `NumberLiteralNode` node and return the surviving branches."""
    typ = _number_literal_type(node.value)
    return BranchSet((branch.push(typ).emit(TypedNode(node, typ)),))


@register(StringLiteralNode)
def _string_literal(
    self: Analyser,
    node: StringLiteralNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `StringLiteralNode` node and return the surviving branches."""
    return BranchSet((branch.push(T.String).emit(TypedNode(node, T.String)),))


@register(GetVariableNode)
def _get_variable(
    self: Analyser,
    node: GetVariableNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `GetVariableNode` node and return the surviving branches."""
    typ = branch.variables.read(node.name)

    if typ is None:
        message = f"undefined variable '{node.name}'"
        self._diagnose(message, node)
        return BranchSet(
            (
                branch.error(
                    message,
                    node.location,
                    code="undefined-variable",
                ).emit(TypedNode(node, None)),
            )
        )

    return BranchSet((branch.push(typ).emit(TypedNode(node, typ)),))


@register(SetVariableNode)
def _set_variable(
    self: Analyser,
    node: SetVariableNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `SetVariableNode` node and return the surviving branches."""
    if not branch.stack:
        if branch.input_mode is InputMode.INFER_INPUTS:
            inferred = node.declared_type or T.V(f"_inferred_{node.name}")
            write = branch.variables.write(
                node.name,
                inferred,
                constant=node.constant,
                ctx=self.env.context,
            )

            if write.error is not None:
                self._diagnose(write.error, node)
                return BranchSet(
                    (
                        branch.error(
                            write.error,
                            node.location,
                            code="variable-write",
                        ),
                    )
                )

            if write.variables is None:
                return BranchSet(
                    (
                        branch.error(
                            f"cannot assign to variable '{node.name}'",
                            node.location,
                            code="variable-write",
                        ),
                    )
                )

            return BranchSet(
                (
                    branch.with_variables(write.variables).emit(
                        TypedNode(node, inferred)
                    ),
                )
            )

        return BranchSet(
            (
                branch.error(
                    f"empty stack when trying to assign to variable '{node.name}'",
                    node.location,
                    code="stack-underflow",
                ),
            )
        )

    value_type = branch.stack[-1]
    variable_type = node.declared_type or value_type

    if node.declared_type is not None and not T.assignable(
        value_type,
        node.declared_type,
        self.env.context,
    ):
        self._diagnose(
            f"cannot assign {T.show(value_type)} to variable '{node.name}' "
            f"of declared type {T.show(node.declared_type)}",
            node,
        )
        return BranchSet((branch.emit(TypedNode(node, None)),))

    write = branch.variables.write(
        node.name,
        variable_type,
        block_local=True,
        constant=node.constant,
        ctx=self.env.context,
    )

    if write.error is not None:
        self._diagnose(write.error, node)
        return BranchSet(
            (
                branch.error(
                    write.error,
                    node.location,
                    code="variable-write",
                ),
            )
        )

    if write.variables is None:
        return BranchSet(
            (
                branch.error(
                    f"cannot assign to variable '{node.name}'",
                    node.location,
                    code="variable-write",
                ),
            )
        )

    return BranchSet(
        (
            branch.with_variables(write.variables)
            .pop()
            .emit(TypedNode(node, variable_type)),
        )
    )


@register(SetVariablesNode)
def _set_variables_node(
    self: Analyser,
    node: SetVariablesNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `SetVariablesNode` node and return the surviving branches."""
    if not node.targets:
        return BranchSet((branch.emit(TypedNode(node, None)),))

    available = min(len(branch.stack), len(node.targets))
    missing = len(node.targets) - available
    if missing and branch.input_mode is not InputMode.INFER_INPUTS:
        return BranchSet(
            (
                branch.error(
                    "empty stack when trying to assign to multiple variables",
                    node.location,
                    code="stack-underflow",
                ),
            )
        )

    inferred = tuple(
        target.declared_type or T.V(f"_inferred_{target.name}")
        for target in node.targets[:missing]
    )
    value_types = inferred + branch.stack.items[len(branch.stack) - available :]
    variables = branch.variables
    for target, value_type in zip(node.targets, value_types, strict=True):
        variable_type = target.declared_type or value_type
        if target.declared_type is not None and not T.assignable(
            value_type,
            target.declared_type,
            self.env.context,
        ):
            self._diagnose(
                f"cannot assign {T.show(value_type)} to variable "
                f"'{target.name}' of declared type {T.show(target.declared_type)}",
                target,
            )
            return BranchSet((branch.emit(TypedNode(node, None)),))

        write = variables.write(
            target.name,
            variable_type,
            block_local=True,
            constant=target.constant,
            ctx=self.env.context,
        )
        if write.error is not None:
            self._diagnose(write.error, target)
            return BranchSet(
                (
                    branch.error(
                        write.error,
                        target.location,
                        code="variable-write",
                    ),
                )
            )
        if write.variables is None:
            return BranchSet(
                (
                    branch.error(
                        f"cannot assign to variable '{target.name}'",
                        target.location,
                        code="variable-write",
                    ),
                )
            )
        variables = write.variables

    return BranchSet(
        (
            branch.with_variables(variables)
            .pop(available)
            .emit(TypedNode(node, None)),
        )
    )


@register(IfNode)
def _if_node(
    self: Analyser,
    node: IfNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a conditional and retain typed nodes for both runtime branches."""
    diagnostics_before = len(self.diagnostics)
    condition = self.analyse_from(branch, node.condition)
    terminal, condition = _split_terminal_branches(condition)
    if not condition:
        if terminal:
            return terminal
        if len(self.diagnostics) == diagnostics_before:
            self._diagnose("if condition must be a boolean value", node)
        return BranchSet()
    body_inputs = self.require_stack_top_assignable(
        condition,
        expected=Boolean,
        location=node.location,
        message="if condition must be a boolean value",
        code="if-condition-type",
    )

    if not body_inputs or any(output.failed for output in body_inputs):
        self._diagnose("if condition must be a boolean value", node)
        return terminal

    outputs: list[AnalysisBranch] = list(terminal.branches)
    saw_mismatched_inputs = False
    for body_input in body_inputs:
        condition_body = body_input.typed_body[len(branch.typed_body) :]
        then_outputs = self.analyse_from(body_input, node.then_branch)
        else_outputs = self.analyse_from(body_input, node.else_branch)

        for left in then_outputs:
            for right in else_outputs:
                if left.inputs != right.inputs:
                    saw_mismatched_inputs = True
                    continue

                if left.break_type is not None or right.break_type is not None:
                    for output in (left, right):
                        typ = output.break_type
                        if typ is None:
                            typ = _returns_result_type(output.stack.items)
                        outputs.append(output.emit(TypedNode(node, typ)))
                    continue

                stack = merge_stacks(left.stack, right.stack)
                base = replace(
                    _refine_branch_like(branch, left),
                    inputs=left.inputs,
                ).with_element_tags(right.element_tags).with_data_element_uses(
                    right.data_element_uses
                )
                variables = left.variables.merge_against(
                    right.variables,
                    base.variables,
                )
                typ = _returns_result_type(stack.items)
                typed_if = TypedIfNode(
                    node,
                    typ,
                    condition=condition_body,
                    then_branch=left.typed_body[len(body_input.typed_body) :],
                    else_branch=right.typed_body[len(body_input.typed_body) :],
                )
                outputs.append(
                    base.with_stack(stack).with_variables(variables).emit(typed_if)
                )

    if not outputs and saw_mismatched_inputs:
        self._diagnose("if branches inferred different inputs", node)

    return BranchSet.collect(outputs)


@register(AssertNode)
def _assert_node(
    self: Analyser,
    node: AssertNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `AssertNode` node and return the surviving branches."""
    diagnostics_before = len(self.diagnostics)
    condition = self.analyse_from(branch, node.condition)
    terminal, condition = _split_terminal_branches(condition)
    if not condition:
        if terminal:
            return terminal
        if len(self.diagnostics) == diagnostics_before:
            self._diagnose("assert condition must be a boolean value", node)
        return BranchSet()
    condition = self.require_stack_top_assignable(
        condition,
        expected=Boolean,
        location=node.location,
        message="assert condition must be a boolean value",
        code="assert-condition-type",
    )

    if not condition or any(output.failed for output in condition):
        self._diagnose("assert condition must be a boolean value", node)
        return terminal

    condition_tags = frozenset(
        tag for output in condition for tag in output.element_tags
    )
    condition_uses = frozenset(
        use for output in condition for use in output.data_element_uses
    )
    typed_condition = _typed_block(
        condition,
        len(branch.typed_body),
        node.condition,
    )
    typed_assert = TypedAssertNode(
        node,
        None,
        condition=typed_condition,
    )
    success = (
        branch.with_element_tags(condition_tags)
        .with_data_element_uses(condition_uses)
        .emit(typed_assert)
    )
    if not node.else_branch:
        return BranchSet.collect((*terminal.branches, success))

    else_outputs = self.analyse_from(branch, node.else_branch)
    typed_assert = TypedAssertNode(
        node,
        None,
        condition=typed_condition,
        else_branch=_typed_block(
            else_outputs,
            len(branch.typed_body),
            node.else_branch,
        ),
    )
    success = replace(
        success,
        typed_body=(*success.typed_body[:-1], typed_assert),
    ).with_element_tags(
        tag for output in else_outputs for tag in output.element_tags
    ).with_data_element_uses(
        use for output in else_outputs for use in output.data_element_uses
    )
    error_types = tuple(_top_or_none(output.stack) for output in else_outputs)
    error_type = T.U(*error_types) if error_types else T.NoneType()
    assert_error = T.N(Symbol("AssertError"), error_type)
    return BranchSet.collect((*terminal.branches, success.push(assert_error)))


@register(BreakNode)
def _break_node(
    self: Analyser,
    node: BreakNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `BreakNode` node and return the surviving branches."""
    value_outputs = self.analyse_from(branch, node.values)
    return BranchSet.collect(
        value_branch.emit(
            TypedNode(node, _top_or_none(value_branch.stack))
        ).with_break(_top_or_none(value_branch.stack))
        for value_branch in value_outputs
    )


@register(WhileNode)
def _while_node(
    self: Analyser,
    node: WhileNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `WhileNode` node and return the surviving branches."""
    loop_input = branch
    if node.params is not None:
        params = _params_to_types(node.params)
        sourced = branch.source_arguments(params)
        if sourced is None:
            self._diagnose("while loop inputs do not match stack", node)
            return BranchSet()

        _, loop_input = sourced
        loop_input = loop_input.push(*params)
        named = tuple(
            (param.name, typ)
            for param, typ in zip(node.params, params, strict=True)
            if param.name is not None
        )
        if named:
            loop_input = loop_input.with_variables(
                BranchVariables.from_parameters(
                    named,
                    captures=loop_input.variables,
                )
            )
        loop_input = replace(
            loop_input,
            input_mode=InputMode.CYCLE_EXPLICIT_PARAMS,
            cycle_params=params,
        )

    diagnostics_before = len(self.diagnostics)
    condition = self.analyse_from(loop_input, node.condition)
    terminal, condition = _split_terminal_branches(condition)
    if not condition:
        if terminal:
            return terminal
        if len(self.diagnostics) == diagnostics_before:
            self._diagnose("while condition must be a boolean value", node)
        return BranchSet()
    body_inputs = self.require_stack_top_assignable(
        condition,
        expected=Boolean,
        location=node.location,
        message="while condition must be a boolean value",
        code="while-condition-type",
    )
    if not body_inputs or any(output.failed for output in body_inputs):
        self._diagnose("while condition must be a boolean value", node)
        return terminal

    body_outputs = self.analyse_scoped_block(body_inputs, node.body)
    if not body_outputs:
        return BranchSet()

    joined: AnalysisBranch | None = None
    for output in body_outputs:
        if joined is None:
            joined = output
            continue

        if joined.inputs != output.inputs:
            self._diagnose("while body inferred different inputs", node)
            return BranchSet()

        stack = merge_stacks(joined.stack, output.stack)
        variables = joined.variables.merge_against(
            output.variables,
            loop_input.variables,
        )
        joined = joined.with_stack(stack).with_variables(variables)
        joined = joined.with_element_tags(output.element_tags)
        joined = joined.with_data_element_uses(output.data_element_uses)

    if joined is None:
        return BranchSet()

    variables = (
        joined.variables
        if node.params is None
        else joined.variables.merge_against(loop_input.variables, branch.variables)
    )
    result = _refine_branch_like(branch, joined).with_variables(variables)
    condition_body = _typed_block(
        condition,
        len(loop_input.typed_body),
        node.condition,
    )
    body_start = (
        len(body_inputs.branches[0].typed_body)
        if body_inputs.branches
        else len(loop_input.typed_body)
    )
    body = _typed_block(body_outputs, body_start, node.body)
    return BranchSet.collect(
        (
            *terminal.branches,
            result.emit(
                TypedWhileNode(
                    node,
                    _returns_result_type(result.stack.items),
                    condition=condition_body,
                    body=body,
                )
            ),
        )
    )


@register(ReturnNode)
def _return_node(
    self: Analyser,
    node: ReturnNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `ReturnNode` node and return the surviving branches."""
    return BranchSet((branch.emit(TypedNode(node, None)),))


def _at_collection_view(typ: T.Type) -> T.CollectionType | None:
    """Build the view of at collection during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _at_collection_view(typ.inner)
    return typ if isinstance(typ, T.CollectionType) else None


def _at_level_type(source: T.Type, target_rank: int) -> T.Type | None:
    """Determine the type of at level during static analysis."""
    source = T.normalize(source)
    if isinstance(source, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _at_level_type(source.inner, target_rank)
    if not isinstance(source, T.CollectionType):
        return source if target_rank == 0 else None
    if not isinstance(source.rank, int) or source.rank < target_rank:
        return None
    if target_rank == 0:
        return source.base
    collection_type: type[T.CollectionType]
    if isinstance(source, (T.ListExactType, T.ListMinType)):
        collection_type = T.ListExactType
    elif isinstance(source, T.ListRuggedType):
        collection_type = T.ListRuggedType
    elif isinstance(source, (T.ArrayExactType, T.ArrayMinType)):
        collection_type = T.ArrayExactType
    else:
        return None
    return T.C(collection_type, source.base, target_rank)


@register(AtNode)
def _at_node(
    self: Analyser,
    node: AtNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `AtNode` node and return the surviving branches."""
    arity = len(node.levels)
    source_hints = tuple(
        T.V(f"_at_{branch.origin}_{index}") for index in range(arity)
    )
    sourced = branch.source_arguments(source_hints)
    if sourced is None:
        self._diagnose(
            f"at requires {arity} value(s) on the stack",
            node,
        )
        return BranchSet()

    source_types, popped = sourced
    target_types: list[T.Type] = []
    explicit_target_ranks: list[int | None] = []
    minimum_depths: list[int] = []
    for level, source_type in zip(node.levels, source_types, strict=True):
        target = _at_level_type(source_type, level.depth)
        if target is None:
            self._diagnose(
                f"at level '{level.name}' requires rank {level.depth}, "
                f"but received {T.show(source_type)}",
                node,
            )
            return BranchSet()
        target_types.append(target)
        collection = _at_collection_view(source_type)
        if collection is None:
            explicit_target_ranks.append(None)
            minimum_depths.append(0)
        else:
            explicit_target_ranks.append(level.depth)
            minimum_depths.append(
                max(collection.rank - level.depth, 0)
                if isinstance(collection.rank, int)
                else 0
            )

    params = tuple(
        FunctionParam(
            None if level.name.text == "_" else level.name,
            target_type,
        )
        for level, target_type in zip(node.levels, target_types, strict=True)
    )
    function_node = FunctionNode(
        params=params,
        body=node.body,
        location=node.location,
    )
    analysed = self._analyse_function_literal(popped, function_node)
    if analysed is None:
        return BranchSet()
    function, _ = analysed
    typed_function = TypedFunctionNode(
        function_node,
        function.typ,
        function.overloads,
    )

    candidates: list[tuple[int, T.AppliedOverload]] = []
    for index, overload_typing in enumerate(function.overloads):
        overload = overload_typing.overload
        if not isinstance(overload, T.Overload):
            continue
        applied = T.apply_overload(overload, source_types, self.env.context)
        if applied is None:
            continue
        applied = replace(
            applied,
            vectorised=any(depth > 0 for depth in minimum_depths),
            vectorised_depths=tuple(minimum_depths),
            vectorised_target_ranks=tuple(explicit_target_ranks),
        )
        candidates.append((index, applied))

    if not candidates:
        self._diagnose("at body does not accept the selected level values", node)
        return BranchSet()
    if len(candidates) > 1:
        self._diagnose("at body has ambiguous inferred overloads", node)
        return BranchSet()

    overload_index, applied = candidates[0]
    result = popped.with_stack(popped.stack.push(*applied.actual_returns))
    result = result.with_element_tags(applied.element_tags)
    return BranchSet(
        (
            result.emit(
                TypedAtNode(
                    node,
                    _returns_result_type(applied.actual_returns),
                    typed_function,
                    applied,
                    overload_index,
                )
            ),
        )
    )


@register(FunctionNode)
def _function_node(
    self: Analyser,
    node: FunctionNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `FunctionNode` node and return the surviving branches."""
    if not self._validate_annotations(node.annotations, "fn", node):
        return BranchSet((branch.emit(TypedNode(node, None)),))

    function_node = _genericize_function_node(node, node.generics)
    self._validate_function_element_tags(function_node, node)
    result = self._analyse_function_literal(branch, function_node)
    if result is None:
        return BranchSet((branch.emit(TypedNode(node, None)),))

    function, typed_branch = result
    typed_node = TypedFunctionNode(function_node, function.typ, function.overloads)
    return BranchSet((typed_branch.push(function.typ).emit(typed_node),))


@register(CastNode)
def _cast_node(
    self: Analyser,
    node: CastNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `CastNode` node and return the surviving branches."""
    target = T.normalize(node.typ)
    self._validate_element_tags_in_types((target,), node)
    if not branch.stack:
        self._diagnose(
            f"empty stack when casting to {T.show(target)}",
            node,
        )
        return BranchSet((branch.emit(TypedNode(node, None)),))

    source = branch.stack[-1]
    if node.checked:
        if T.assignable(source, target, self.env.context):
            self._diagnose(
                f"checked cast to {T.show(target)} is already statically safe",
                node,
            )
            return BranchSet()
        if not _types_overlap(source, target, self.env.context):
            if _type_contains_rank_var(target):
                stack = T.TypeStack((*branch.stack.items[:-1], target))
                return BranchSet(
                    (branch.with_stack(stack).emit(TypedNode(node, target)),)
                )
            self._diagnose(
                f"cannot cast {T.show(source)} to {T.show(target)}",
                node,
            )
            return BranchSet()
    elif not T.assignable(source, target, self.env.context):
        self._diagnose(
            f"cannot safely cast {T.show(source)} to {T.show(target)}",
            node,
        )
        return BranchSet()

    stack = T.TypeStack((*branch.stack.items[:-1], target))
    return BranchSet((branch.with_stack(stack).emit(TypedNode(node, target)),))


@register(StackShuffleNode)
def _stack_shuffle_node(
    self: Analyser,
    node: StackShuffleNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `StackShuffleNode` node and return the surviving branches."""
    params = tuple(
        T.V(f"_shuffle_{index}") for index, _ in enumerate(node.prestack)
    )
    sourced = branch.source_arguments(params)
    if sourced is None:
        self._diagnose(
            f"stack underflow for {node.mode}; expected "
            f"{len(node.prestack)} value(s)",
            node,
        )
        return BranchSet()

    args, popped = sourced
    labelled = {
        label: typ
        for label, typ in zip(node.prestack, args, strict=True)
        if label is not None
    }
    stack_arg_start = len(node.prestack) - min(
        len(branch.stack),
        len(node.prestack),
    )
    copy_errors = tuple(
        _copy_diagnostic(typ, self.env)
        for typ in _copied_stack_shuffle_types(
            node,
            args,
            labelled,
            stack_arg_start,
        )
    )
    for error in copy_errors:
        if error is not None:
            self._diagnose(error, node)
            return BranchSet()

    post_types = tuple(labelled[label] for label in node.poststack)
    if node.mode == Symbol("copy"):
        stack = branch.stack.push(*post_types)
    else:
        kept = tuple(
            typ
            for label, typ in zip(node.prestack, args, strict=True)
            if label is None
        )
        stack = popped.stack.push(*kept, *post_types)

    return BranchSet(
        (
            popped.with_stack(stack).emit(
                TypedNode(node, _returns_result_type(post_types))
            ),
        )
    )


@register(FieldAccessNode)
def _field_access_node(
    self: Analyser,
    node: FieldAccessNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `FieldAccessNode` node and return the surviving branches."""
    sourced = self._source_field_receiver(
        branch,
        node.name,
        optional_safe=node.optional_safe,
    )
    if sourced is None:
        action = "safely access" if node.optional_safe else "access"
        self._diagnose(
            f"empty stack when trying to {action} field '{node.name}'",
            node,
        )
        return BranchSet()

    receiver_type, field_type, branch = sourced
    if field_type is None:
        if node.optional_safe:
            self._diagnose(
                f"optional type {T.show(receiver_type)} has no known field "
                f"'{node.name}' on its present value",
                node,
            )
        else:
            self._diagnose(
                f"type {T.show(receiver_type)} has no known field '{node.name}'",
                node,
            )
        return BranchSet()

    return BranchSet((branch.push(field_type).emit(TypedNode(node, field_type)),))


@register(FieldSetNode)
def _field_set_node(
    self: Analyser,
    node: FieldSetNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `FieldSetNode` node and return the surviving branches."""
    if len(branch.stack) < 2:
        self._diagnose(
            f"field assignment to '{node.name}' requires receiver and value",
            node,
        )
        return BranchSet()

    receiver_type = branch.stack[-2]
    value_type = branch.stack[-1]
    if node.optional_safe:
        field_type, refined_receiver = self._safe_field_type(
            receiver_type,
            node.name,
            branch,
            write=True,
        )
    else:
        field_type, refined_receiver = self._field_type(
            receiver_type,
            node.name,
            branch,
            write=True,
        )
    if field_type is None:
        if node.optional_safe:
            self._diagnose(
                f"optional type {T.show(receiver_type)} has no writable field "
                f"'{node.name}' on its present value",
                node,
            )
        else:
            self._diagnose(
                f"type {T.show(receiver_type)} has no writable field '{node.name}'",
                node,
            )
        return BranchSet()

    if not T.assignable(value_type, field_type, self.env.context):
        self._diagnose(
            f"cannot assign {T.show(value_type)} to field '{node.name}' "
            f"of type {T.show(field_type)}",
            node,
        )
        return BranchSet()

    result_type = receiver_type if refined_receiver is None else refined_receiver
    stack = T.TypeStack(branch.stack.items[:-2]).push(result_type)
    return BranchSet((branch.with_stack(stack).emit(TypedNode(node, result_type)),))


@register(IndexAccessNode)
def _index_access_node(
    self: Analyser,
    node: IndexAccessNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `IndexAccessNode` node and return the surviving branches."""
    selector_values = _selector_value_count(node.selectors)
    required = selector_values + 1
    if len(branch.stack) >= required:
        receiver_type = branch.stack[-required]
        base_branch = branch.with_stack(T.TypeStack(branch.stack.items[:-required]))
    elif len(branch.stack) == selector_values:
        source_branch = branch.with_stack(
            T.TypeStack(branch.stack.items[: len(branch.stack) - selector_values])
        )
        sourced = source_branch.source_arguments((T.V("IndexReceiver"),))
        if sourced is None:
            self._diagnose("indexing requires receiver and index value(s)", node)
            return BranchSet()
        (receiver_type,), base_branch = sourced
    else:
        self._diagnose("indexing requires receiver and index value(s)", node)
        return BranchSet()

    index_types = branch.stack.items[-selector_values:] if selector_values else ()
    if not _selectors_assignable(
        receiver_type,
        node.selectors,
        index_types,
        self.env.context,
    ):
        self._diagnose("list indexing requires Integer index value(s)", node)
        return BranchSet()

    result_type = _indexed_type(receiver_type, node.selectors, node.spread)
    return BranchSet(
        (base_branch.push(result_type).emit(TypedNode(node, result_type)),)
    )


@register(IndexSetNode)
def _index_set_node(
    self: Analyser,
    node: IndexSetNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `IndexSetNode` node and return the surviving branches."""
    selector_values = _selector_value_count(node.selectors)
    required = selector_values + 2
    if len(branch.stack) < required:
        self._diagnose(
            "indexed assignment requires value, receiver, and index",
            node,
        )
        return BranchSet()

    value_type = branch.stack[-required]
    receiver_type = branch.stack[-selector_values - 1]
    index_types = branch.stack.items[-selector_values:] if selector_values else ()
    if not _selectors_assignable(
        receiver_type,
        node.selectors,
        index_types,
        self.env.context,
    ):
        self._diagnose("list indexing requires Integer index value(s)", node)
        return BranchSet()

    item_type = _indexed_type(receiver_type, node.selectors, spread=False)
    updated_receiver_type = _indexed_assignment_type(
        receiver_type,
        node.selectors,
        value_type,
        self.env.context,
    )
    if updated_receiver_type is None:
        self._diagnose(
            f"cannot assign {T.show(value_type)} to indexed item "
            f"of type {T.show(item_type)}",
            node,
        )
        return BranchSet()

    stack = T.TypeStack(branch.stack.items[:-required]).push(updated_receiver_type)
    return BranchSet(
        (branch.with_stack(stack).emit(TypedNode(node, updated_receiver_type)),)
    )


@register(CallNode)
def _call_node(
    self: Analyser,
    node: CallNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `CallNode` node and return the surviving branches."""
    if not branch.stack:
        self._diagnose("call requires a function on the stack", node)
        return BranchSet()

    callable_type = T.normalize(branch.stack[-1])
    overloads = _callable_overloads(callable_type)
    if not overloads:
        self._diagnose(
            f"cannot call non-function value of type {T.show(callable_type)}",
            node,
        )
        return BranchSet()

    callable_popped = branch.pop()
    diagnostics_before = len(self.diagnostics)
    arg_branches = self.analyse_from(callable_popped, node.args)
    terminal, arg_branches = _split_terminal_branches(arg_branches)
    if not arg_branches:
        if terminal:
            return terminal
        if len(self.diagnostics) > diagnostics_before:
            return BranchSet()

    candidates: list[CallCandidate] = []
    for arg_branch in arg_branches:
        for overload in overloads:
            sourced = arg_branch.source_arguments(overload.params)
            if sourced is None:
                continue
            args, popped = sourced
            candidate = _apply_overload_to_branch(
                overload,
                args,
                popped,
                self.env.context,
                analyser=self,
            )
            if candidate is None:
                continue

            candidates.append(CallCandidate(candidate.applied, candidate.branch))

    winners = self.select_call_winners(
        candidates=candidates,
        branch=callable_popped,
        node=node,
        no_match_message=(
            f"no overloads for call target {T.show(callable_type)} match stack "
            f"{_show_stack(callable_popped.stack)}; available overloads: "
            f"{_show_overloads(overloads)}"
        ),
        ambiguous_message=(
            f"ambiguous call target {T.show(callable_type)} with stack "
            f"{_show_stack(callable_popped.stack)}"
        ),
    )
    if winners is None:
        return terminal

    return BranchSet.collect(
        (
            *terminal.branches,
            *(
                candidate.branch.push(*candidate.applied.actual_returns).emit(
                    TypedCallNode(
                        node,
                        _returns_result_type(candidate.applied.actual_returns),
                        candidate.applied,
                    )
                )
                for candidate in winners
            ),
        )
    )


@register(StringInterpolationNode)
def _string_interpolation_node(
    self: Analyser,
    node: StringInterpolationNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `StringInterpolationNode` node and return the surviving branches."""
    current = BranchSet((branch,))
    expression_count = 0
    for part in node.parts:
        if isinstance(part, str):
            continue

        expression_count += 1
        current = self.analyse_scoped_block(current, part)
        if not current:
            return BranchSet()
        if any(not output.stack for output in current):
            self._diagnose(
                "string interpolation expression must leave a value",
                node,
            )
            return BranchSet()

    terminal, current = _split_terminal_branches(current)
    return BranchSet.collect(
        (
            *terminal.branches,
            *(
                replace(
                    output,
                    stack=_pop_stack(output.stack, expression_count).push(T.String),
                    typed_body=branch.typed_body,
                ).emit(TypedNode(node, T.String))
                for output in current
                if len(output.stack) >= expression_count
            ),
        )
    )


@register(ListLiteralNode)
def _list_literal_node(
    self: Analyser,
    node: ListLiteralNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `ListLiteralNode` node and return the surviving branches."""
    if not node.items:
        if node.typ is not None:
            typ = T.normalize(node.typ)
            if not isinstance(typ, T.CollectionType):
                self._diagnose(
                    f"empty list cast needs a list type, got {T.show(typ)}",
                    node,
                )
                return BranchSet()
            return BranchSet((branch.push(typ).emit(TypedNode(node, typ)),))

        self._diagnose(
            "empty list literal requires a type annotation or cast",
            node,
        )
        return BranchSet()

    item_options = self._literal_item_options(
        branch,
        node.items,
        node,
        message="list item must leave a value on the stack",
    )
    if item_options is None:
        return BranchSet()

    return _literal_branch_results(
        branch,
        item_options,
        node,
        lambda combo: T.C(T.ListExactType, T.U(*(item.typ for item in combo))),
    )


@register(TupleLiteralNode)
def _tuple_literal_node(
    self: Analyser,
    node: TupleLiteralNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `TupleLiteralNode` node and return the surviving branches."""
    item_options = self._literal_item_options(branch, node.items, node)
    if item_options is None:
        return BranchSet()

    return _literal_branch_results(
        branch,
        item_options,
        node,
        lambda combo: T.Tup(*(item.typ for item in combo)),
    )


@register(RecordLiteralNode)
def _record_literal_node(
    self: Analyser,
    node: RecordLiteralNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `RecordLiteralNode` node and return the surviving branches."""
    expressions = tuple(expr for _, expr in node.fields)
    item_options = self._literal_item_options(branch, expressions, node)
    if item_options is None:
        return BranchSet()

    return _literal_branch_results(
        branch,
        item_options,
        node,
        lambda combo: T.Row(
            T.N(Symbol("record")),
            *(
                T.Field(name, item.typ)
                for (name, _), item in zip(node.fields, combo, strict=True)
            ),
        ),
    )


@register(DictLiteralNode)
def _dict_literal_node(
    self: Analyser,
    node: DictLiteralNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `DictLiteralNode` node and return the surviving branches."""
    expressions = tuple(expr for entry in node.entries for expr in entry)
    item_options = self._literal_item_options(branch, expressions, node)
    if item_options is None:
        return BranchSet()

    return _literal_branch_results(
        branch,
        item_options,
        node,
        lambda combo: T.N(
            Symbol("Dict"),
            T.U(*(item.typ for item in combo[::2])),
            T.U(*(item.typ for item in combo[1::2])),
        ),
    )


@register(ArrayLiteralNode)
def _array_literal_node(
    self: Analyser,
    node: ArrayLiteralNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `ArrayLiteralNode` node and return the surviving branches."""
    return BranchSet((branch.emit(TypedNode(node, None)),))


@register(ForNode)
def _for_node(
    self: Analyser,
    node: ForNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `ForNode` node and return the surviving branches."""
    consumes_stack_iterable = bool(branch.stack)
    if not branch.stack:
        item = _anonymous_type_var(branch, 1)
        sourced = branch.source_arguments((T.ExactList(item),))
        if sourced is None:
            self._diagnose("for loop requires iterable on the stack", node)
            return BranchSet()
        (iterable_type,), branch = sourced
    else:
        iterable_type = branch.stack[-1]

    item_type = T.collection_item_type(iterable_type)
    if not item_type:
        self._diagnose(
            "for loop iterable must actually be iterable. "
            f"Got {T.show(iterable_type)}",
            node,
        )
        return BranchSet()

    body_stack = branch.stack.pop() if consumes_stack_iterable else branch.stack
    body_branch = branch.with_stack(body_stack)
    cycle_params = (item_type,)
    if node.index_variable is not None:
        cycle_params = (item_type, T.Integer)
    body_branch = replace(
        body_branch,
        input_mode=InputMode.CYCLE_EXPLICIT_PARAMS,
        cycle_params=cycle_params,
    )
    body_branch = body_branch.with_variables(
        body_branch.variables.with_block_local(node.variable, item_type)
    )
    if node.index_variable is not None:
        body_branch = body_branch.with_variables(
            body_branch.variables.with_block_local(node.index_variable, T.Integer)
        )

    body_outputs = self.analyse_from(body_branch, node.body)
    if not body_outputs:
        return BranchSet()

    refined_item_type = _loop_variable_output_type(node.variable, body_outputs)
    if (
        refined_item_type is not None
        and _contains_type_var(item_type)
        and not T.same(item_type, refined_item_type)
    ):
        body_branch = body_branch.refine_type(item_type, refined_item_type)
        body_outputs = BranchSet.collect(
            output.refine_type(item_type, refined_item_type)
            for output in body_outputs
        )

    break_types = tuple(
        output.break_type for output in body_outputs if output.break_type is not None
    )
    result_type = _loop_break_result_type(break_types)
    loop_locals = (node.variable,) + (
        (node.index_variable,) if node.index_variable is not None else ()
    )
    variables = _merge_loop_variables(
        body_branch.variables,
        body_outputs,
        loop_locals,
    )
    typed_for = TypedForNode(
        node,
        result_type,
        body=_typed_block(
            body_outputs,
            len(body_branch.typed_body),
            node.body,
        ),
    )
    body_element_tags = frozenset(
        tag for output in body_outputs for tag in output.element_tags
    )
    body_data_element_uses = frozenset(
        use for output in body_outputs for use in output.data_element_uses
    )
    return BranchSet(
        (
            _refine_branch_like(branch, body_branch)
            .with_element_tags(body_element_tags)
            .with_data_element_uses(body_data_element_uses)
            .with_stack(body_branch.stack.push(result_type))
            .with_variables(variables)
            .emit(typed_for),
        )
    )


@register(UnfoldNode)
def _unfold_node(
    self: Analyser,
    node: UnfoldNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `UnfoldNode` node and return the surviving branches."""
    body_function = FunctionNode(
        params=node.params,
        body=node.body,
        annotations=(AnnotationNode(Symbol("returnAll")),),
        element_tags=frozenset(),
        location=node.location,
    )
    body_analysis = self._analyse_unfold_body_function(branch, body_function)
    if body_analysis is None:
        return BranchSet()

    candidates: list[CallCandidate] = []
    for overload in _callable_overloads(body_analysis.typ):
        condition_element_tags: frozenset[T.ElementTag] = frozenset()
        state_arity = len(overload.params)
        if state_arity == 0:
            self._diagnose("unfold requires at least one state value", node)
            continue
        if len(overload.returns) > state_arity + 1:
            self._diagnose(
                "unfold body may not produce more than state arity plus one value",
                node,
            )
            continue

        if node.condition:
            condition_function = FunctionNode(
                params=(
                    tuple(
                        FunctionParam(param.name, typ)
                        for param, typ in zip(
                            node.params or (),
                            overload.params,
                            strict=False,
                        )
                    )
                    if node.params is not None
                    else tuple(FunctionParam(None, typ) for typ in overload.params)
                ),
                body=node.condition,
                returns=(Boolean,),
                element_tags=frozenset(),
                location=node.location,
            )
            condition_result = self._analyse_function_literal(
                branch,
                condition_function,
            )
            if condition_result is None:
                self._diagnose("unfold condition must return a boolean value", node)
                continue
            condition_analysis, _ = condition_result
            condition_element_tags = frozenset(
                tag
                for candidate_overload in _callable_overloads(condition_analysis.typ)
                for tag in candidate_overload.element_tags
                if not tag.absent
            )

        sourced = branch.source_arguments(overload.params)
        if sourced is None:
            self._diagnose("unfold inputs do not match stack", node)
            continue
        args, popped = sourced
        applied = T.try_apply_overload(overload, args, self.env.context).applied
        if applied is None:
            continue
        candidates.append(
            CallCandidate(
                applied=applied,
                branch=popped,
                callable_overload_index=state_arity,
            )
        )

    results: list[AnalysisBranch] = []
    for candidate in _best_candidates(candidates, branch):
        generated = _unfold_emitted_type(
            candidate.applied.params,
            candidate.applied.actual_returns,
        )
        list_type = T.WithTag(T.ExactList(generated), "infinite")
        results.append(
            candidate.branch.with_element_tags(
                (*candidate.applied.element_tags, *condition_element_tags)
            ).push(list_type).emit(
                TypedUnfoldNode(
                    node,
                    list_type,
                    state_arity=cast(int, candidate.callable_overload_index),
                    function=TypedFunctionNode(
                        body_function,
                        body_analysis.typ,
                        body_analysis.overloads,
                    ),
                )
            )
        )
    return BranchSet.collect(results)


@register(TagDeclarationNode)
def _tag_declaration_node(
    self: Analyser,
    node: TagDeclarationNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `TagDeclarationNode` node and return the surviving branches."""
    if node.disjoint is not None:
        if isinstance(node.disjoint, T.DataTag):
            self.env.add_disjoint_tags(node.tag.name, node.disjoint.name)
        else:
            self.env.add_disjoint_data_element_tags(node.tag.name, node.disjoint)
    elif node.parent is not None:
        self.env.add_variant_tag(node.tag.name, node.parent.name)
    elif node.kind == Symbol("constructed"):
        self.env.add_constructed_tag(node.tag.name)
    elif node.kind == Symbol("unit"):
        self.env.add_unit_tag(node.tag.name)
    else:
        self.env.add_computed_tag(node.tag.name)

    return BranchSet((branch.emit(TypedNode(node, None)),))


@register(ElementTagDeclarationNode)
def _element_tag_declaration_node(
    self: Analyser,
    node: ElementTagDeclarationNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `ElementTagDeclarationNode` node and return the surviving branches."""
    if node.disjoint is not None:
        if isinstance(node.disjoint, T.DataTag):
            self.env.add_disjoint_data_element_tags(node.disjoint.name, node.name)
        else:
            self.env.add_disjoint_element_tags(node.name, node.disjoint)
    elif node.kind == Symbol("companion"):
        self.env.add_companion_element_tag(node.name)
    else:
        self.env.add_property_element_tag(node.name)

    return BranchSet((branch.emit(TypedNode(node, None)),))


@register(TagOverlayNode)
def _tag_overlay_node(
    self: Analyser,
    node: TagOverlayNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `TagOverlayNode` node and return the surviving branches."""
    public = node.visibility == Symbol("public")
    for element in node.elements:
        for params, returns in node.signatures:
            self._validate_data_tags((params, returns), node)
            overload = T.Overload(params=params, returns=returns)
            if node.generics:
                overload = _genericize_overload(overload, node.generics)
            self.env.define_tag_overlay(
                node.tag.name,
                element,
                overload,
                public=public,
            )

    return BranchSet((branch.emit(TypedNode(node, None)),))


@register(TagApplicationNode)
def _tag_application_node(
    self: Analyser,
    node: TagApplicationNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `TagApplicationNode` node and return the surviving branches."""
    sourced = branch.source_arguments((T.V("_tagged_value"),))
    if sourced is None:
        self._diagnose(
            f"empty stack when applying tag '{_show_tag(node.tag)}'",
            node,
        )
        return BranchSet((branch.emit(TypedNode(node, None)),))

    (value_type,), base_branch = sourced
    validator: T.AppliedOverload | None = None
    validator_index: int | None = None
    validator_runtime_name: Symbol | None = None
    added_tags: tuple[T.DataTag, ...] = ()
    removed_tags: tuple[T.DataTag, ...] = ()
    if node.tag.absent:
        tagged = _remove_data_tag(value_type, node.tag)
        if tagged is None:
            self._diagnose(
                f"cannot remove absent tag '{_show_tag(node.tag)}' from "
                f"{value_type}",
                node,
            )
            return BranchSet((branch.emit(TypedNode(node, None)),))
        removed_tags = (T.DataTag(node.tag.name, node.tag.depth),)
    else:
        tagged = _with_data_tags(value_type, (node.tag,), self.env.context)
        added = [T.DataTag(node.tag.name, node.tag.depth)]
        parent = self.env.context.tag_parent(node.tag.name)
        if parent is not None:
            added.append(T.DataTag(parent.text, node.tag.depth))
        added_tags = tuple(added)
        removed_tags = tuple(
            T.DataTag(str(name), node.tag.depth)
            for name in sorted(
                self.env.context.tag_disjoints(node.tag.name),
                key=str,
            )
        )
        validator_name = Symbol(f"#{node.tag.name}")
        validator_overloads = self.env.overloads_for(validator_name)
        if validator_overloads:
            matches: list[tuple[T.AppliedOverload, int]] = []
            for index, overload in enumerate(validator_overloads):
                applied = T.try_apply_overload(
                    overload,
                    (value_type,),
                    self.env.context,
                ).applied
                if applied is None:
                    continue
                if not _validator_overload_ok(overload, self.env.context):
                    self._diagnose(
                        f"tag validator '{validator_name}' must return "
                        "#boolean Number",
                        node,
                    )
                    return BranchSet((branch.emit(TypedNode(node, None)),))
                matches.append((applied, index))

            if not matches:
                self._diagnose(
                    f"no validator overload for '{validator_name}' matches "
                    f"{T.show(value_type)}",
                    node,
                )
                return BranchSet((branch.emit(TypedNode(node, None)),))

            validator, validator_index = matches[0]
            static_result = self.env.tag_validator_static_result(
                validator_name,
                validator_index,
            )
            if static_result is True:
                validator = None
                validator_index = None
            elif static_result is False:
                self._diagnose(
                    f"tag validator '{validator_name}' is statically false",
                    node,
                )
                return BranchSet((branch.emit(TypedNode(node, None)),))
            if validator is not None:
                validator_runtime_name = self.env.runtime_name_for(validator_name)

    stack = base_branch.stack.push(tagged)
    typed = TypedTagApplicationNode(
        node,
        tagged,
        validator,
        validator_index,
        added_tags,
        removed_tags,
        validator_runtime_name,
    )
    return BranchSet((base_branch.with_stack(stack).emit(typed),))


@register(ImportNode)
def _import_node(
    self: Analyser,
    node: ImportNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `ImportNode` node and return the surviving branches."""
    for spec in node.specs:
        try:
            exports, resolved_spec, definitions = self._load_import_definitions(spec)
            objects = import_objects(exports, resolved_spec)
            import_environment_facts(exports, resolved_spec, self.env)
        except ModuleLoadError as exc:
            self._diagnose(str(exc), node)
            return BranchSet((branch.emit(TypedNode(node, None)),))

        for typed_node in exports.runtime_prelude:
            self._prelude.add(typed_node)
        for obj in objects:
            runtime_name = self._prelude.add_declaration(obj.typed, obj.name)
            self._register_imported_object(obj, runtime_name)
        for definition in definitions:
            runtime_name = self._prelude.add_declaration(
                definition.typed,
                definition.name,
            )
            self._register_imported_definition(
                definition.name,
                definition.typed,
                runtime_name,
            )

    return BranchSet((branch.emit(TypedNode(node, None)),))


@register(ObjectNode)
def _object_node(
    self: Analyser,
    node: ObjectNode,
    branch: AnalysisBranch,
) -> BranchSet:
    """Analyse a `ObjectNode` node and return the surviving branches."""
    if not self._validate_annotations(node.annotations, node.kind.text, node):
        return BranchSet((branch.emit(TypedNode(node, None)),))

    node = annotation_hooks.DEFAULT_REGISTRY.transform_object(node)
    kind = node.kind.text
    if kind == "object":
        return self._object_definition(branch, node)
    if kind == "trait":
        return self._trait_definition(branch, node)
    if kind == "variant":
        return self._variant_definition(branch, node)
    if kind == "enum":
        return self._enum_definition(branch, node)

    self._diagnose(f"unknown object-like declaration '{node.kind}'", node)
    return BranchSet((branch.emit(TypedNode(node, None)),))


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


def _declared_params(node: FunctionNode) -> tuple[T.Type, ...]:
    """Determine the parameters for declared during static analysis."""
    if node.params is None:
        return ()
    return _params_to_types(node.params)


def _parameter_value_type(typ: T.Type) -> T.Type:
    """Return the type visible inside a function body for one parameter."""
    typ = T.normalize(typ)
    return typ.inner if isinstance(typ, T.ExactType) else typ


def _restore_exact_parameter_markers(
    declared: tuple[T.Type, ...],
    inferred: tuple[T.Type, ...],
) -> tuple[T.Type, ...]:
    """Reapply call-policy markers after analysing parameter values."""
    if len(declared) > len(inferred):
        return inferred
    offset = len(inferred) - len(declared)
    restored = tuple(
        T.Exact(actual)
        if isinstance(T.normalize(expected), T.ExactType)
        else actual
        for expected, actual in zip(declared, inferred[offset:], strict=True)
    )
    return inferred[:offset] + restored


def _function_overload(
    node: FunctionNode,
    *,
    params: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
    where_clause: tuple[ASTNode, ...] = (),
    element_tags: frozenset[T.ElementTag] | None = None,
    call_site_body: object | None = None,
) -> T.Overload:
    """Build or resolve the overload for function during static analysis."""
    return T.Overload(
        params=params,
        returns=returns,
        where_clause=where_clause,
        param_names=_function_param_names_for_overload(node, params),
        call_site_body=call_site_body,
        element_tags=frozenset() if element_tags is None else element_tags,
        annotation_error=annotation_hooks.annotation_error_message(node.annotations),
        annotation_warning=annotation_hooks.annotation_warning_message(node.annotations),
        param_defaults=_function_param_defaults_for_overload(node, params),
    )


def _fully_typed_overload(node: FunctionNode) -> T.Overload | None:
    """Build or resolve the overload for fully typed during static analysis."""
    if node.params is None or node.returns is None:
        return None
    if any(param.typ is None for param in node.params):
        return None
    params = tuple(param.typ for param in node.params if param.typ is not None)
    return _function_overload(
        node,
        params=params,
        returns=node.returns,
        where_clause=node.where_clause,
        element_tags=node.element_tags,
    )


def _validate_define_niladic_name(name: Symbol, overload: T.Overload) -> bool:
    """Return the Boolean result of validate define niladic name during static analysis."""
    is_named_nilad = name.text.startswith("\\")
    is_inferred_nilad = len(overload.params) == 0
    return is_named_nilad == is_inferred_nilad


def _body_references_element(body: tuple[ASTNode, ...], name: Symbol) -> bool:
    """Return the Boolean result of body references element during static analysis."""
    return any(_node_references_element(node, name) for node in body)


def _node_references_element(node: ASTNode, name: Symbol) -> bool:
    """Return the Boolean result of node references element during static analysis."""
    if isinstance(node, ElementNode) and node.name == name:
        return True
    for item in fields(node):
        value = getattr(node, item.name)
        if isinstance(value, ASTNode):
            if _node_references_element(value, name):
                return True
        elif isinstance(value, tuple) and _tuple_references_element(value, name):
            return True
    return False


def _tuple_references_element(value: tuple[object, ...], name: Symbol) -> bool:
    """Return the Boolean result of tuple references element during static analysis."""
    for item in value:
        if isinstance(item, ASTNode) and _node_references_element(item, name):
            return True
        if isinstance(item, tuple) and _tuple_references_element(item, name):
            return True
    return False


def _is_call_site_checked_param(typ: T.Type | None) -> bool:
    """Return whether the value is call site checked param."""
    if typ is None:
        return False
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return typ.name == Symbol("Function") and not typ.args
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return True
        return any(_is_call_site_checked_type(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return True
    return _is_call_site_checked_type(typ)


def _is_call_site_checked_type(typ: T.Type) -> bool:
    """Return whether the value is call site checked type."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return any(_is_call_site_checked_type(arg) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_is_call_site_checked_type(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_is_call_site_checked_type(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return True
    if isinstance(typ, T.RowType):
        return _is_call_site_checked_type(typ.base) or any(
            _is_call_site_checked_type(field.typ) for field in typ.fields
        )
    if isinstance(typ, T.CollectionType):
        return _is_call_site_checked_type(typ.base)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return True
        return any(
            _is_call_site_checked_type(item) for item in typ.params + typ.returns
        )
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _is_call_site_checked_type(typ.inner)
    return False


def _call_site_substituted_params(
    params: tuple[FunctionParam, ...],
    actuals: tuple[T.Type, ...],
    ctx: T.Context,
) -> tuple[FunctionParam, ...] | None:
    """Determine the parameters for call site substituted during static analysis."""
    if len(params) != len(actuals):
        return None
    substituted: list[FunctionParam] = []
    for param, actual in zip(params, actuals, strict=True):
        typ = param.typ
        if typ is None:
            substituted.append(FunctionParam(param.name, actual, param.default))
            continue
        if not _call_site_placeholder_accepts(typ, actual, ctx):
            return None
        substituted.append(
            FunctionParam(
                param.name,
                _call_site_substitute_type(typ, actual),
                param.default,
            )
        )
    return tuple(substituted)


def _call_site_placeholder_accepts(
    declared: T.Type,
    actual: T.Type,
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of call site placeholder accepts during static analysis."""
    declared = T.normalize(declared)
    if _is_bare_function_type(declared):
        return isinstance(T.normalize(actual), (T.FunctionType, T.OverloadSetType))
    return T.compatible(actual, declared, ctx)


def _call_site_substitute_type(declared: T.Type, actual: T.Type) -> T.Type:
    """Determine the type of call site substitute during static analysis."""
    declared = T.normalize(declared)
    if _is_bare_function_type(declared) or isinstance(declared, T.VariadicTupleType):
        return actual
    return declared


def _is_bare_function_type(typ: T.Type) -> bool:
    """Return whether the value is bare function type."""
    typ = T.normalize(typ)
    return (
        isinstance(typ, T.FunctionType) and typ.params is None and typ.returns is None
    )


def _function_param_names_for_overload(
    node: FunctionNode,
    inputs: tuple[T.Type, ...],
) -> tuple[Symbol | None, ...]:
    """Build or resolve the overload for function param names for during static analysis."""
    if node.params is None:
        return (None,) * len(inputs)
    names = tuple(param.name for param in node.params)
    if len(names) < len(inputs):
        return (None,) * (len(inputs) - len(names)) + names
    return names


def _function_param_defaults_for_overload(
    node: FunctionNode,
    inputs: tuple[T.Type, ...],
) -> tuple[tuple[object, ...] | None, ...]:
    """Build or resolve the overload for function param defaults for during static analysis."""
    if node.params is None:
        return (None,) * len(inputs)
    defaults = tuple(param.default or None for param in node.params)
    if len(defaults) < len(inputs):
        return (None,) * (len(inputs) - len(defaults)) + defaults
    return defaults


def _contains_rank_var(types: tuple[T.Type, ...]) -> bool:
    """Return whether the value contains rank var."""
    return any(_type_contains_rank_var(typ) for typ in types)


def _type_contains_rank_var(typ: T.Type) -> bool:
    """Return the Boolean result of type contains rank var during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.CollectionType):
        return isinstance(typ.rank, T.RankVariable) or _type_contains_rank_var(typ.base)
    if isinstance(typ, T.NominalType):
        return any(_type_contains_rank_var(arg) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_type_contains_rank_var(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_type_contains_rank_var(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_type_contains_rank_var(item.typ) for item in typ.items)
    if isinstance(typ, T.FunctionType):
        return _contains_rank_var(typ.params) or _contains_rank_var(typ.returns)
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _type_contains_rank_var(typ.inner)
    return False


def _contains_type_var(typ: T.Type) -> bool:
    """Return whether the value contains type var."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return True
    if isinstance(typ, T.CollectionType):
        return _contains_type_var(typ.base)
    if isinstance(typ, T.NominalType):
        return any(_contains_type_var(arg) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_contains_type_var(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_contains_type_var(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_contains_type_var(item.typ) for item in typ.items)
    if isinstance(typ, T.FunctionType):
        return any(_contains_type_var(item) for item in typ.params or ()) or any(
            _contains_type_var(item) for item in typ.returns or ()
        )
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _contains_type_var(typ.inner)
    return False


def _contains_named_type_var(typ: T.Type, name: str) -> bool:
    """Return whether the value contains named type var."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return typ.name == name
    if isinstance(typ, T.CollectionType):
        return _contains_named_type_var(typ.base, name)
    if isinstance(typ, T.NominalType):
        return any(_contains_named_type_var(arg, name) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_contains_named_type_var(item, name) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_contains_named_type_var(item, name) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_contains_named_type_var(item.typ, name) for item in typ.items)
    if isinstance(typ, T.RowType):
        return _contains_named_type_var(typ.base, name) or any(
            _contains_named_type_var(field.typ, name) for field in typ.fields
        )
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return _element_tags_contain_named_type_var(typ.element_tags, name)
        return any(
            _contains_named_type_var(item, name) for item in typ.params + typ.returns
        ) or _element_tags_contain_named_type_var(typ.element_tags, name)
    if isinstance(typ, T.AnonymousTraitType):
        return any(
            _contains_named_type_var(item, name)
            for requirement in typ.requirements
            for item in requirement.overload.params + requirement.overload.returns
        )
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _contains_named_type_var(typ.inner, name)
    return False


def _element_tags_contain_named_type_var(
    tags: frozenset[T.ElementTag],
    name: str,
) -> bool:
    """Return the Boolean result of element tags contain named type var during static analysis."""
    return any(_contains_named_type_var(arg, name) for tag in tags for arg in tag.args)


def _static_body_variable_names(node: FunctionNode) -> tuple[Symbol, ...]:
    """Collect the names for static body variable during static analysis."""
    names: set[Symbol] = set()
    for typ in (*_declared_params(node), *(node.returns or ())):
        names.update(Symbol(name) for name in _rank_var_names_in_type(typ))
    for where_node in node.where_clause:
        if isinstance(where_node, SetVariableNode):
            names.add(where_node.name)
    return tuple(sorted(names))


def _rank_var_names_in_type(typ: T.Type) -> set[str]:
    """Determine the type of rank var names in during static analysis."""
    typ = T.normalize(typ)
    names: set[str] = set()
    if isinstance(typ, T.CollectionType):
        if isinstance(typ.rank, T.RankVariable):
            names.add(typ.rank.name)
        names.update(_rank_var_names_in_type(typ.base))
    elif isinstance(typ, T.NominalType):
        for arg in typ.args:
            names.update(_rank_var_names_in_type(arg))
    elif isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            names.update(_rank_var_names_in_type(item))
    elif isinstance(typ, T.TupleType):
        for item in typ.params:
            names.update(_rank_var_names_in_type(item))
    elif isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            names.update(_rank_var_names_in_type(item.typ))
    elif isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return names
        for item in typ.params + typ.returns:
            names.update(_rank_var_names_in_type(item))
    elif isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        names.update(_rank_var_names_in_type(typ.inner))
    return names


def _params_to_types(params: tuple[FunctionParam, ...]) -> tuple[T.Type, ...]:
    """Determine the types used for params to during static analysis."""
    return tuple(_param_type(param, index) for index, param in enumerate(params))


def _function_capture_source(outer: AnalysisBranch) -> BranchVariables | None:
    """Return bindings whose types are available inside a function body."""
    if outer.input_mode is not InputMode.TOP_LEVEL:
        return outer.variables
    constants = outer.variables.constant_items()
    if not constants:
        return None
    return BranchVariables(
        function_locals=constants,
        function_constants=tuple(name for name, _typ in constants),
    )


def _top_level_assignment_capture_nodes(
    outer: AnalysisBranch,
    node: FunctionNode,
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture nodes during static analysis."""
    if outer.input_mode is not InputMode.TOP_LEVEL:
        return ()
    visible = set(outer.variables.nonconstant_names())
    if not visible:
        return ()
    return _top_level_assignment_capture_reads_in_function(node, visible, frozenset())


def _top_level_assignment_capture_reads_in_function(
    node: FunctionNode,
    visible: set[Symbol],
    inherited_bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture reads in function during static analysis."""
    bound = inherited_bound | _function_bound_variable_names(node)
    return _top_level_assignment_capture_reads_in_nodes(node.body, visible, bound)


def _top_level_assignment_capture_reads_in_nodes(
    nodes: tuple[ASTNode, ...],
    visible: set[Symbol],
    bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture reads in nodes during static analysis."""
    reads: list[GetVariableNode] = []
    for node in nodes:
        if isinstance(node, GetVariableNode):
            if node.name in visible and node.name not in bound:
                reads.append(node)
            continue
        if isinstance(node, FunctionNode):
            reads.extend(
                _top_level_assignment_capture_reads_in_function(
                    node,
                    visible,
                    bound,
                )
            )
            continue
        for item in fields(node):
            reads.extend(
                _top_level_assignment_capture_reads_in_value(
                    getattr(node, item.name),
                    visible,
                    bound,
                )
            )
    return tuple(reads)


def _top_level_assignment_capture_reads_in_value(
    value: object,
    visible: set[Symbol],
    bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
    """Compute top level assignment capture reads in value during static analysis."""
    if isinstance(value, FunctionNode):
        return _top_level_assignment_capture_reads_in_function(value, visible, bound)
    if isinstance(value, ASTNode):
        return _top_level_assignment_capture_reads_in_nodes((value,), visible, bound)
    if isinstance(value, tuple):
        reads: list[GetVariableNode] = []
        for item in value:
            reads.extend(
                _top_level_assignment_capture_reads_in_value(item, visible, bound)
            )
        return tuple(reads)
    return ()


def _function_bound_variable_names(node: FunctionNode) -> frozenset[Symbol]:
    """Collect the names for function bound variable during static analysis."""
    names = {param.name for param in node.params or () if param.name is not None}
    names.update(
        assigned.name for assigned in node.body if isinstance(assigned, SetVariableNode)
    )
    for assigned in node.body:
        if isinstance(assigned, SetVariablesNode):
            names.update(target.name for target in assigned.targets)
    return frozenset(names)


def _function_analysis_from_signatures(
    signatures: dict[T.Overload, tuple[TypedNode, ...]],
) -> FunctionAnalysis | None:
    """Build the signatures for function analysis from during static analysis."""
    if not signatures:
        return None

    ordered = tuple(
        sorted(
            signatures,
            key=lambda overload: T.show(T.Fn(overload.params, overload.returns)),
        )
    )
    overload_typings = tuple(
        FunctionOverloadTyping(
            T.Fn(signature.params, signature.returns, signature.element_tags),
            signatures[signature],
            signature,
        )
        for signature in ordered
    )
    if len(ordered) == 1 and not ordered[0].where_clause:
        signature = ordered[0]
        typ = T.Fn(signature.params, signature.returns, signature.element_tags)
    else:
        typ = T.Overloads(*ordered)
    return FunctionAnalysis(typ, overload_typings)


def _callable_overloads(typ: T.Type) -> tuple[T.Overload, ...]:
    """Collect the overloads for callable during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return ()
        return (
            T.Overload(
                params=typ.params,
                returns=typ.returns,
                element_tags=typ.element_tags,
            ),
        )
    if isinstance(typ, T.OverloadSetType):
        return typ.overloads
    return ()


def _element_tag_covers(
    requirement: T.ElementTag,
    actual: T.ElementTag,
    ctx: T.Context,
) -> bool:
    """Return whether one declared tag covers a concrete propagated effect."""
    if requirement.name != actual.name:
        return False
    if not requirement.args:
        return True
    if len(requirement.args) != len(actual.args):
        return False
    return all(
        T.assignable(actual_arg, required_arg, ctx)
        for actual_arg, required_arg in zip(
            actual.args,
            requirement.args,
            strict=True,
        )
    )


def _element_tag_absence_conflicts(
    forbidden: T.ElementTag,
    actual: T.ElementTag,
    ctx: T.Context,
) -> bool:
    """Return whether a propagated effect may overlap a declared absence."""
    if forbidden.name != actual.name:
        return False
    if not forbidden.args:
        return True
    if len(forbidden.args) != len(actual.args):
        return False
    return all(
        _types_may_overlap(forbidden_arg, actual_arg, ctx)
        for forbidden_arg, actual_arg in zip(
            forbidden.args,
            actual.args,
            strict=True,
        )
    )


def _types_may_overlap(
    left: T.Type,
    right: T.Type,
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of types may overlap during static analysis."""
    left = T.normalize(left)
    right = T.normalize(right)
    if isinstance(left, T.UnionType):
        return any(_types_may_overlap(item, right, ctx) for item in left.items)
    if isinstance(right, T.UnionType):
        return any(_types_may_overlap(left, item, ctx) for item in right.items)
    return T.assignable(left, right, ctx) or T.assignable(right, left, ctx)


def _final_function_element_tags(
    node: FunctionNode,
    body_tags: frozenset[T.ElementTag],
    env: T.Environment,
) -> frozenset[T.ElementTag]:
    """Compute final function element tags during static analysis."""
    declared = set(node.element_tags)
    if not node.element_tags_explicit:
        return frozenset(declared | set(body_tags))

    final = set(declared)
    declared_properties = tuple(
        tag
        for tag in node.element_tags
        if not tag.absent
        and (definition := env.lookup_element_tag(tag.name)) is not None
        and definition.kind is T.ElementTagKind.PROPERTY
    )
    for tag in body_tags:
        definition = env.lookup_element_tag(tag.name)
        if definition is not None and definition.kind is T.ElementTagKind.COMPANION:
            final.add(tag)
            continue
        if not any(
            _element_tag_covers(declared_tag, tag, env.context)
            for declared_tag in declared_properties
        ):
            final.add(tag)
    return frozenset(final)


def _function_type_element_tag_sets(
    typ: T.Type,
) -> Iterator[frozenset[T.ElementTag]]:
    """Yield every function-tag set nested in a type annotation."""
    typ = T.normalize(typ)
    if isinstance(typ, T.FunctionType):
        yield typ.element_tags
        for tag in typ.element_tags:
            for arg in tag.args:
                yield from _function_type_element_tag_sets(arg)
        for item in (*(typ.params or ()), *(typ.returns or ())):
            yield from _function_type_element_tag_sets(item)
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            yield from _function_type_element_tag_sets(arg)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            yield from _function_type_element_tag_sets(item)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            yield from _function_type_element_tag_sets(item)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            yield from _function_type_element_tag_sets(item.typ)
        return
    if isinstance(typ, T.RowType):
        yield from _function_type_element_tag_sets(typ.base)
        for field in typ.fields:
            yield from _function_type_element_tag_sets(field.typ)
        return
    if isinstance(typ, T.CollectionType):
        yield from _function_type_element_tag_sets(typ.base)
        return
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        yield from _function_type_element_tag_sets(typ.inner)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            yield requirement.overload.element_tags
            for item in (*requirement.overload.params, *requirement.overload.returns):
                yield from _function_type_element_tag_sets(item)
        return
    if isinstance(typ, T.OverloadSetType):
        for overload in typ.overloads:
            yield overload.element_tags
            for item in (*overload.params, *overload.returns):
                yield from _function_type_element_tag_sets(item)


def _present_data_tags(typ: T.Type) -> Iterator[T.DataTag]:
    """Yield present data tags anywhere inside a call argument type."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        yield from (tag for tag in typ.tags if not tag.absent)
        yield from _present_data_tags(typ.inner)
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            yield from _present_data_tags(arg)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            yield from _present_data_tags(item)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            yield from _present_data_tags(item)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            yield from _present_data_tags(item.typ)
        return
    if isinstance(typ, T.RowType):
        yield from _present_data_tags(typ.base)
        for field in typ.fields:
            yield from _present_data_tags(field.typ)
        return
    if isinstance(typ, T.CollectionType):
        yield from _present_data_tags(typ.base)
        return
    if isinstance(typ, T.FunctionType):
        for tag in typ.element_tags:
            for arg in tag.args:
                yield from _present_data_tags(arg)
        for item in (*(typ.params or ()), *(typ.returns or ())):
            yield from _present_data_tags(item)
        return
    if isinstance(typ, (T.ExactType, T.AtomicType)):
        yield from _present_data_tags(typ.inner)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for item in (*requirement.overload.params, *requirement.overload.returns):
                yield from _present_data_tags(item)
        return
    if isinstance(typ, T.OverloadSetType):
        for overload in typ.overloads:
            for item in (*overload.params, *overload.returns):
                yield from _present_data_tags(item)


def _best_candidates(
    candidates: Iterable[CallCandidate],
    original: AnalysisBranch | None = None,
) -> tuple[CallCandidate, ...]:
    """Collect viable candidates for best during static analysis."""
    ordered = list(candidates)
    winners: list[CallCandidate] = []
    for candidate in ordered:
        if not any(
            other is not candidate
            and _candidate_dominates(other, candidate)
            and not _preserve_distinct_inferred_specializations(
                other,
                candidate,
                original,
            )
            for other in ordered
        ):
            winners.append(candidate)
    return tuple(winners)


def _candidate_dominates(
    left: CallCandidate,
    right: CallCandidate,
) -> bool:
    """Return the Boolean result of candidate dominates during static analysis."""
    left_applied = left.applied
    right_applied = right.applied
    if left.dispatch_priority != right.dispatch_priority:
        return left.dispatch_priority > right.dispatch_priority
    if _dominates(left_applied.scores, right_applied.scores):
        return True
    if left_applied.scores != right_applied.scores:
        return False
    return _params_more_specific(left_applied.params, right_applied.params)


def _params_more_specific(
    left: tuple[T.Type, ...],
    right: tuple[T.Type, ...],
) -> bool:
    """Return the Boolean result of params more specific during static analysis."""
    return all(
        _type_more_specific_or_same(left_item, right_item)
        for left_item, right_item in zip(left, right, strict=False)
    ) and any(
        not _type_more_specific_or_same(right_item, left_item)
        for left_item, right_item in zip(left, right, strict=False)
    )


def _type_more_specific_or_same(left: T.Type, right: T.Type) -> bool:
    """Return the Boolean result of type more specific or same during static analysis."""
    left = T.normalize(left)
    right = T.normalize(right)
    if T.same(left, right) or T.assignable(left, right):
        return True
    if isinstance(left, T.FunctionType) and isinstance(right, T.FunctionType):
        if left.params is None or left.returns is None:
            return right.params is None and right.returns is None
        if right.params is None or right.returns is None:
            return True
        if len(left.params) != len(right.params) or len(left.returns) != len(
            right.returns
        ):
            return False
        return all(
            _type_more_specific_or_same(left_item, right_item)
            for left_item, right_item in zip(
                left.params,
                right.params,
                strict=True,
            )
        ) and all(
            _type_more_specific_or_same(left_item, right_item)
            for left_item, right_item in zip(
                left.returns,
                right.returns,
                strict=True,
            )
        )
    return False


def _preserve_distinct_inferred_specializations(
    left: CallCandidate,
    right: CallCandidate,
    original: AnalysisBranch | None,
) -> bool:
    """Return the Boolean result of preserve distinct inferred specializations during static analysis."""
    if original is None:
        return False
    left_key = _inferred_specialization_key(left.branch, original)
    right_key = _inferred_specialization_key(right.branch, original)
    return left_key is not None and right_key is not None and left_key != right_key


def _inferred_specialization_key(
    branch: AnalysisBranch,
    original: AnalysisBranch,
) -> tuple[object, ...] | None:
    """Build the comparison key for inferred specialization during static analysis."""
    if branch.inputs != original.inputs:
        return ("inputs", branch.inputs)
    if branch.cycle_params != original.cycle_params:
        return ("cycle_params", branch.cycle_params)
    if branch.variables != original.variables:
        return ("variables", branch.variables)
    return None


def _winners_specialize_inputs(
    winners: tuple[CallCandidate, ...],
    original: AnalysisBranch,
) -> bool:
    """Return the Boolean result of winners specialize inputs during static analysis."""
    return all(candidate.branch.inputs != original.inputs for candidate in winners)


def _overload_index(
    overloads: tuple[T.Overload, ...],
    overload: T.Overload,
) -> int | None:
    """Find the index for overload during static analysis."""
    try:
        return overloads.index(overload)
    except ValueError:
        return None


def _source_element_arguments(
    branch: AnalysisBranch,
    overload: T.Overload,
    modifier_args: tuple[ModifierArgumentAnalysis, ...],
    ctx: T.Context,
    call_arg_order: tuple[int, ...] = (),
    analyser: Analyser | None = None,
) -> Iterator[
    tuple[
        tuple[T.Type, ...],
        AnalysisBranch,
        tuple[ModifierArgumentAnalysis, ...],
    ]
]:
    """Source element arguments during static analysis."""
    if not modifier_args:
        params = _call_args_in_current_order(overload.params, call_arg_order)
        sourced = branch.source_arguments(params)
        if sourced is not None:
            current_args, popped = sourced
            args = _call_args_in_parameter_order(current_args, call_arg_order)
            for specialized_args, specialized_popped in (
                _contextual_stack_argument_variants(
                    args,
                    overload.params,
                    popped,
                    ctx,
                    analyser,
                )
            ):
                yield specialized_args, specialized_popped, ()
        return

    modifier_indexes = _modifier_param_indexes(overload.params)
    if len(modifier_indexes) != len(modifier_args):
        return

    stack_params = tuple(
        param
        for index, param in enumerate(overload.params)
        if index not in modifier_indexes
    )
    current_stack_params = _call_args_in_current_order(stack_params, call_arg_order)
    sourced = branch.source_arguments(current_stack_params)
    if sourced is None:
        return
    current_stack_args, popped = sourced
    stack_args = _call_args_in_parameter_order(current_stack_args, call_arg_order)
    stack_substitution = _branch_argument_substitution(stack_args, stack_params, ctx)
    if stack_substitution is None:
        return

    modifier_orders = (
        (modifier_args,)
        if _overload_needs_call_site_checking(overload)
        else _unique_permutations(modifier_args)
    )
    for ordered_modifiers in modifier_orders:
        for substitution, specialized_modifiers in _specialized_modifier_orders(
            overload.params,
            modifier_indexes,
            ordered_modifiers,
            stack_substitution,
            ctx,
            analyser,
        ):
            specialized_stack_args = tuple(
                _substitute_branch_type(arg, substitution) for arg in stack_args
            )
            specialized_popped = _specialize_branch_arguments(popped, substitution)
            yield (
                _merge_element_arguments(
                    overload.params,
                    modifier_indexes,
                    specialized_stack_args,
                    specialized_modifiers,
                ),
                specialized_popped,
                specialized_modifiers,
            )


def _contextual_stack_argument_variants(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    branch: AnalysisBranch,
    ctx: T.Context,
    analyser: Analyser | None,
) -> Iterator[tuple[tuple[T.Type, ...], AnalysisBranch]]:
    """Contextualize deferred function literals passed on the value stack."""
    inferred_literal_vars: set[str] = set()
    for arg, param in zip(args, params, strict=True):
        if not isinstance(T.normalize(param), T.FunctionType):
            continue
        if _stack_function_literal(arg, branch) is None:
            continue
        inferred_literal_vars.update(_type_variable_names(arg))

    if inferred_literal_vars:
        inferred = _branch_argument_substitution(args, params, ctx)
        if inferred is not None:
            literal_substitution = {
                name: typ
                for name, typ in inferred.items()
                if name in inferred_literal_vars
            }
            if literal_substitution:
                branch = _specialize_branch_arguments(branch, literal_substitution)
                args = tuple(
                    _substitute_branch_type(arg, literal_substitution) for arg in args
                )

    deferred: list[tuple[int, ModifierArgumentAnalysis]] = []
    for index, (arg, param) in enumerate(zip(args, params, strict=True)):
        if not isinstance(T.normalize(param), T.FunctionType):
            continue
        modifier = _deferred_stack_function_argument(arg, branch)
        if modifier is not None:
            deferred.append((index, modifier))

    if not deferred:
        yield args, branch
        return

    deferred_indexes = {index for index, _ in deferred}
    ordinary_args = tuple(
        arg for index, arg in enumerate(args) if index not in deferred_indexes
    )
    ordinary_params = tuple(
        param for index, param in enumerate(params) if index not in deferred_indexes
    )
    substitution = _branch_argument_substitution(ordinary_args, ordinary_params, ctx)
    if substitution is None:
        return

    def rec(
        position: int,
        current_substitution: dict[str, T.Type],
        replacements: tuple[TypedFunctionNode, ...],
    ) -> Iterator[tuple[tuple[T.Type, ...], AnalysisBranch]]:
        """Recursively specialize each deferred stack function argument."""
        if position == len(deferred):
            specialized_args = list(
                _substitute_branch_type(arg, current_substitution) for arg in args
            )
            specialized_branch = _specialize_branch_arguments(
                branch,
                current_substitution,
            )
            for (argument_index, _), replacement in zip(
                deferred,
                replacements,
                strict=True,
            ):
                concrete_type = _substitute_branch_type(
                    replacement.typ,
                    current_substitution,
                )
                concrete_node = replace(replacement, typ=concrete_type)
                specialized_args[argument_index] = concrete_type
                specialized_branch = _replace_contextual_function_node(
                    specialized_branch,
                    concrete_node,
                )
            yield tuple(specialized_args), specialized_branch
            return

        argument_index, modifier = deferred[position]
        expected = _substitute_branch_type(
            params[argument_index],
            current_substitution,
        )
        for specialized, modifier_substitution in _modifier_variants_for_expected(
            modifier,
            expected,
            ctx,
            analyser,
        ):
            merged = _merge_substitutions(
                current_substitution,
                modifier_substitution,
            )
            if merged is None:
                continue
            yield from rec(
                position + 1,
                merged,
                (*replacements, specialized.typed_node),
            )

    yield from rec(0, substitution, ())


def _stack_function_literal(
    typ: T.Type,
    branch: AnalysisBranch,
) -> TypedFunctionNode | None:
    """Return the most recent function literal carrying the requested type."""
    for typed_node in reversed(branch.typed_body):
        if isinstance(typed_node, TypedFunctionNode) and T.same(typed_node.typ, typ):
            return typed_node
    return None


def _type_variable_names(typ: T.Type) -> frozenset[str]:
    """Collect free type-variable names from a type tree."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return frozenset((typ.name,))
    if isinstance(typ, T.NominalType):
        children = typ.args
    elif isinstance(typ, (T.UnionType, T.IntersectionType)):
        children = typ.items
    elif isinstance(typ, T.TupleType):
        children = typ.params
    elif isinstance(typ, T.VariadicTupleType):
        children = tuple(item.typ for item in typ.items)
    elif isinstance(typ, T.RowType):
        children = (typ.base, *(field.typ for field in typ.fields))
    elif isinstance(typ, T.CollectionType):
        children = (typ.base,)
    elif isinstance(typ, T.FunctionType):
        children = (
            ()
            if typ.params is None or typ.returns is None
            else typ.params + typ.returns
        )
    elif isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        children = (typ.inner,)
    else:
        children = ()
    names: set[str] = set()
    for child in children:
        names.update(_type_variable_names(child))
    return frozenset(names)


def _deferred_stack_function_argument(
    typ: T.Type,
    branch: AnalysisBranch,
) -> ModifierArgumentAnalysis | None:
    """Find the typed literal backing a deferred stack function type."""
    normalized = T.normalize(typ)
    overloads = (
        normalized.overloads
        if isinstance(normalized, T.OverloadSetType)
        else ()
    )
    source_nodes = tuple(
        overload.call_site_body[1]
        for overload in overloads
        if isinstance(overload.call_site_body, tuple)
        and len(overload.call_site_body) == 2
        and isinstance(overload.call_site_body[1], FunctionNode)
    )
    if not source_nodes:
        return None
    for typed_node in reversed(branch.typed_body):
        if not isinstance(typed_node, TypedFunctionNode):
            continue
        if any(
            typed_node.node is source or typed_node.node == source
            for source in source_nodes
        ):
            return ModifierArgumentAnalysis(typ, typed_node)
    return None


def _replace_contextual_function_node(
    branch: AnalysisBranch,
    replacement: TypedFunctionNode,
) -> AnalysisBranch:
    """Replace one deferred function literal with its contextual typing."""
    typed_body = list(branch.typed_body)
    for index in range(len(typed_body) - 1, -1, -1):
        node = typed_body[index]
        if not isinstance(node, TypedFunctionNode):
            continue
        if node.node is replacement.node or node.node == replacement.node:
            typed_body[index] = replacement
            return replace(branch, typed_body=tuple(typed_body))
    return branch


def _call_args_in_current_order(
    items: tuple[T.Type, ...],
    call_arg_order: tuple[int, ...],
) -> tuple[T.Type, ...]:
    """Compute call args in current order during static analysis."""
    if not call_arg_order:
        return items
    return tuple(items[index] for index in _invert_call_arg_order(call_arg_order))


def _call_args_in_parameter_order(
    items: tuple[T.Type, ...],
    call_arg_order: tuple[int, ...],
) -> tuple[T.Type, ...]:
    """Compute call args in parameter order during static analysis."""
    if not call_arg_order:
        return items
    return tuple(items[index] for index in call_arg_order)


def _invert_call_arg_order(call_arg_order: tuple[int, ...]) -> tuple[int, ...]:
    """Compute invert call arg order during static analysis."""
    current_to_parameter = [0] * len(call_arg_order)
    for parameter_index, current_index in enumerate(call_arg_order):
        current_to_parameter[current_index] = parameter_index
    return tuple(current_to_parameter)


def _call_element_candidates(
    branch: AnalysisBranch,
    call_overload: T.Overload,
    function_type: T.Type,
    explicit_args: tuple[T.Type, ...],
    base_stack: tuple[T.Type, ...],
    call_arg_order: tuple[int, ...],
    disambiguation: tuple[T.Type | None, ...],
    ctx: T.Context,
) -> list[CallCandidate]:
    """Collect viable candidates for call element during static analysis."""
    candidates: list[CallCandidate] = []
    if disambiguation and len(disambiguation) != len(explicit_args):
        return candidates
    for callable_index, callable_overload in enumerate(
        _callable_overloads(function_type)
    ):
        callable_application = T.try_apply_overload(
            callable_overload,
            explicit_args,
            ctx,
            disambiguation=disambiguation,
        ).applied
        if callable_application is None:
            continue
        concrete_function_type = T.Fn(
            callable_application.params,
            callable_application.actual_returns,
            callable_overload.element_tags,
        )
        concrete_args = (*explicit_args, concrete_function_type)
        concrete_overload = T.Overload(
            params=concrete_args,
            returns=callable_application.actual_returns,
            call_site_body=len(explicit_args),
        )
        concrete_application = T.try_apply_overload(
            concrete_overload,
            concrete_args,
            ctx,
        ).applied
        if concrete_application is None:
            continue
        actual_returns = _apply_data_tag_flow(
            explicit_args,
            callable_overload.returns,
            callable_application.actual_returns,
            ctx,
        )
        candidates.append(
            CallCandidate(
                applied=T.AppliedOverload(
                    call_overload,
                    concrete_application.substitution,
                    concrete_application.params,
                    concrete_application.returns,
                    actual_returns,
                    concrete_application.scores,
                    concrete_application.vectorised,
                    concrete_application.vectorised_depths,
                    runtime_consumed_count=len(explicit_args) + 1,
                    element_tags=_propagated_element_tags(
                        concrete_overload,
                        concrete_args,
                        concrete_application.substitution,
                    ),
                    vectorised_target_ranks=(
                        concrete_application.vectorised_target_ranks
                    ),
                ),
                branch=branch.with_stack(T.TypeStack(base_stack)),
                call_arg_order=call_arg_order,
                callable_overload_index=callable_index,
            )
        )
    return candidates


def _prepare_element_call_branches(
    branch: AnalysisBranch,
    overload: T.Overload,
    call_args: tuple[CallArgument, ...],
    has_modifier_args: bool,
    analyser: Analyser,
) -> tuple[ElementCallPreparation, ...]:
    """Prepare element call branches during static analysis."""
    plan = _element_call_argument_plan(overload, call_args, has_modifier_args)
    if plan is None:
        return ()
    current = BranchSet((branch,))
    expressions, call_arg_order = plan
    for expression in expressions:
        current = analyser.analyse_block(current, expression)
        if not current:
            return ()
    return tuple(
        ElementCallPreparation(prepared, call_arg_order) for prepared in current
    )


def _element_call_argument_plan(
    overload: T.Overload,
    call_args: tuple[CallArgument, ...],
    has_modifier_args: bool,
) -> tuple[tuple[tuple[ASTNode, ...], ...], tuple[int, ...]] | None:
    """Build the plan for element call argument during static analysis."""
    param_count = len(overload.params)
    if param_count == 0:
        return ((), ()) if not call_args else None
    param_names = overload.param_names or (None,) * param_count
    param_defaults = overload.param_defaults or (None,) * param_count
    if len(param_names) < param_count:
        param_names = (None,) * (param_count - len(param_names)) + param_names
    if len(param_defaults) < param_count:
        param_defaults = (None,) * (param_count - len(param_defaults)) + param_defaults

    modifier_indexes = (
        set(_modifier_param_indexes(overload.params)) if has_modifier_args else set()
    )
    assignments: list[CallArgument | tuple[ASTNode, ...] | None] = [None] * param_count
    cursor = 0

    for arg in call_args:
        if arg.name is not None:
            try:
                index = next(
                    candidate
                    for candidate, name in enumerate(param_names)
                    if name == arg.name
                )
            except StopIteration:
                return None
            if index in modifier_indexes or assignments[index] is not None:
                return None
            assignments[index] = arg
            continue

        while cursor < param_count and (
            cursor in modifier_indexes or assignments[cursor] is not None
        ):
            cursor += 1
        if cursor >= param_count:
            return None
        assignments[cursor] = arg
        cursor += 1

    ordered: list[tuple[ASTNode, ...]] = []
    current_slots: list[int] = []
    stack_sourced_slots: list[int] = []
    explicit_slots: list[int] = []
    for index in range(param_count):
        if index in modifier_indexes:
            continue
        assigned = assignments[index]
        if isinstance(assigned, CallArgument):
            if assigned.placeholder:
                stack_sourced_slots.append(index)
                continue
            ordered.append(assigned.value)
            explicit_slots.append(index)
            continue
        if assigned is not None:
            ordered.append(assigned)
            explicit_slots.append(index)
            continue
        default = param_defaults[index]
        if default is not None:
            ordered.append(cast("tuple[ASTNode, ...]", default))
            explicit_slots.append(index)
            continue
        stack_sourced_slots.append(index)
    current_slots.extend(stack_sourced_slots)
    current_slots.extend(explicit_slots)
    desired_slots = tuple(
        index for index in range(param_count) if index not in modifier_indexes
    )
    call_arg_order = tuple(current_slots.index(index) for index in desired_slots)
    identity = tuple(range(len(call_arg_order)))
    return tuple(ordered), (() if call_arg_order == identity else call_arg_order)


def _merge_element_arguments(
    params: tuple[T.Type, ...],
    modifier_indexes: tuple[int, ...],
    stack_args: tuple[T.Type, ...],
    modifiers: tuple[ModifierArgumentAnalysis, ...],
) -> tuple[T.Type, ...]:
    """Merge element arguments during static analysis."""
    args: list[T.Type] = []
    stack_index = 0
    modifier_index = 0
    modifier_index_set = set(modifier_indexes)
    for index in range(len(params)):
        if index in modifier_index_set:
            args.append(modifiers[modifier_index].typ)
            modifier_index += 1
        else:
            args.append(stack_args[stack_index])
            stack_index += 1
    return tuple(args)


def _specialized_modifier_orders(
    params: tuple[T.Type, ...],
    modifier_indexes: tuple[int, ...],
    modifiers: tuple[ModifierArgumentAnalysis, ...],
    substitution: dict[str, T.Type],
    ctx: T.Context,
    analyser: Analyser | None = None,
) -> Iterator[tuple[dict[str, T.Type], tuple[ModifierArgumentAnalysis, ...]]]:
    """Compute specialized modifier orders during static analysis."""
    if not modifier_indexes:
        yield substitution, ()
        return

    def rec(
        position: int,
        current_substitution: dict[str, T.Type],
        current_modifiers: tuple[ModifierArgumentAnalysis, ...],
    ) -> Iterator[tuple[dict[str, T.Type], tuple[ModifierArgumentAnalysis, ...]]]:
        """Recursively continue the specialized modifier orders algorithm."""
        if position == len(modifier_indexes):
            yield current_substitution, current_modifiers
            return
        param_index = modifier_indexes[position]
        expected = _substitute_branch_type(params[param_index], current_substitution)
        for modifier, modifier_substitution in _modifier_variants_for_expected(
            modifiers[position],
            expected,
            ctx,
            analyser,
        ):
            merged = _merge_substitutions(current_substitution, modifier_substitution)
            if merged is None:
                continue
            yield from rec(position + 1, merged, current_modifiers + (modifier,))

    yield from rec(0, substitution, ())


def _modifier_variants_for_expected(
    modifier: ModifierArgumentAnalysis,
    expected: T.Type,
    ctx: T.Context,
    analyser: Analyser | None = None,
) -> Iterator[tuple[ModifierArgumentAnalysis, dict[str, T.Type]]]:
    """Compute modifier variants for expected during static analysis."""
    expected = T.normalize(expected)
    if not isinstance(expected, T.FunctionType) or _is_bare_function_type(expected):
        if T.compatible(modifier.typ, expected, ctx):
            yield modifier, {}
        return

    if _function_has_union_parameter(expected):
        substitution = _branch_argument_substitution((modifier.typ,), (expected,), ctx)
        if substitution is not None:
            concrete_expected = _substitute_branch_type(expected, substitution)
            dispatch_plan = (
                _union_dispatch_plan_for_function(
                    modifier.typed_node,
                    concrete_expected,
                    ctx,
                )
                if isinstance(T.normalize(concrete_expected), T.FunctionType)
                else None
            )
            if (
                isinstance(T.normalize(concrete_expected), T.FunctionType)
                and T.compatible(
                    modifier.typ,
                    concrete_expected,
                    ctx,
                )
                and dispatch_plan is not None
            ):
                yield (
                    ModifierArgumentAnalysis(
                        concrete_expected,
                        TypedFunctionNode(
                            modifier.typed_node.node,
                            concrete_expected,
                            modifier.typed_node.overloads,
                            dispatch_plan,
                        ),
                    ),
                    substitution,
                )
                return

    overloads = _contextual_modifier_overloads(modifier, expected, analyser, ctx)
    for overload in overloads:
        typ = T.normalize(overload.typ)
        if not isinstance(typ, T.FunctionType):
            continue
        substitution = _branch_argument_substitution((typ,), (expected,), ctx)
        if substitution is None:
            continue
        concrete_expected = _substitute_branch_type(expected, substitution)
        if not isinstance(T.normalize(concrete_expected), T.FunctionType):
            continue
        if not _function_overload_matches_type(overload, concrete_expected, ctx):
            continue
        yield (
            ModifierArgumentAnalysis(
                concrete_expected,
                TypedFunctionNode(
                    modifier.typed_node.node,
                    concrete_expected,
                    (overload,),
                ),
            ),
            substitution,
        )


def _contextual_modifier_overloads(
    modifier: ModifierArgumentAnalysis,
    expected: T.Type,
    analyser: Analyser | None,
    ctx: T.Context,
) -> tuple[FunctionOverloadTyping, ...]:
    """Analyse deferred untyped modifier functions against concrete inputs."""
    expected = T.normalize(expected)
    if analyser is None or not isinstance(expected, T.FunctionType):
        return modifier.typed_node.overloads
    if expected.params is None:
        return modifier.typed_node.overloads

    resolved: list[FunctionOverloadTyping] = []
    deferred = False
    for typing in modifier.typed_node.overloads:
        overload = typing.overload
        if not (
            isinstance(overload, T.Overload)
            and isinstance(overload.call_site_body, tuple)
            and len(overload.call_site_body) == 2
        ):
            resolved.append(typing)
            continue
        deferred = True
        outer, node = overload.call_site_body
        if not isinstance(outer, AnalysisBranch) or not isinstance(node, FunctionNode):
            continue
        for params in _modifier_call_param_variants(expected.params):
            analysis = analyser._analyse_function_at_call_site(outer, node, params)
            if analysis is None:
                continue
            compatible = tuple(
                candidate
                for candidate in analysis.overloads
                if _contextual_modifier_overload_matches(candidate, expected, ctx)
            )
            if compatible:
                resolved.extend(compatible)
                break

    if deferred:
        unique: list[FunctionOverloadTyping] = []
        for typing in resolved:
            if typing not in unique:
                unique.append(typing)
        return tuple(unique)
    return modifier.typed_node.overloads


def _contextual_modifier_overload_matches(
    overload: FunctionOverloadTyping,
    expected: T.FunctionType,
    ctx: T.Context,
) -> bool:
    """Return whether a contextual modifier overload matches its expected type."""
    typ = T.normalize(overload.typ)
    if not isinstance(typ, T.FunctionType):
        return False
    substitution = _branch_argument_substitution((typ,), (expected,), ctx)
    if substitution is None:
        return False
    concrete_expected = _substitute_branch_type(expected, substitution)
    if not isinstance(T.normalize(concrete_expected), T.FunctionType):
        return False
    return _function_overload_matches_type(overload, concrete_expected, ctx)


def _modifier_call_param_variants(
    params: tuple[T.Type, ...],
) -> tuple[tuple[T.Type, ...], ...]:
    """Return concrete and progressively scalarized modifier input shapes."""
    variants: list[tuple[T.Type, ...]] = [()]
    for param in params:
        choices = _modifier_param_rank_variants(param)
        variants = [prefix + (choice,) for prefix in variants for choice in choices]
    return tuple(variants)


def _modifier_param_rank_variants(typ: T.Type) -> tuple[T.Type, ...]:
    """Return a type plus lower-rank views usable for vectorized callables."""
    normalized = T.normalize(typ)
    if not isinstance(normalized, T.CollectionType):
        return (typ,)
    if not isinstance(normalized.rank, int):
        return (typ,)
    return (normalized.base,) + tuple(
        T.C(type(normalized), normalized.base, rank)
        for rank in range(1, normalized.rank + 1)
    )


def _merge_substitutions(
    left: dict[str, T.Type],
    right: dict[str, T.Type],
) -> dict[str, T.Type] | None:
    """Merge substitutions during static analysis."""
    merged = dict(left)
    for name, typ in right.items():
        existing = merged.get(name)
        if existing is not None and not T.same(existing, typ):
            return None
        merged[name] = typ
    return merged


def _function_has_union_parameter(typ: T.FunctionType) -> bool:
    """Return the Boolean result of function has union parameter during static analysis."""
    return typ.params is not None and any(
        isinstance(T.normalize(param), T.UnionType) for param in typ.params
    )


def _modifier_arity_matches(
    overloads: tuple[T.Overload, ...],
    modifier_args: tuple[ModifierArgumentAnalysis, ...],
) -> bool:
    """Return the Boolean result of modifier arity matches during static analysis."""
    return len(modifier_args) in {
        len(_modifier_param_indexes(overload.params)) for overload in overloads
    }


def _specialize_modifier_arguments(
    applied: T.AppliedOverload,
    modifier_args: tuple[ModifierArgumentAnalysis, ...],
    ctx: T.Context,
) -> tuple[TypedFunctionNode, ...]:
    """Specialize modifier arguments during static analysis."""
    if not modifier_args:
        return ()

    offset = len(applied.params) - len(applied.overload.params)
    if offset < 0:
        return tuple(item.typed_node for item in modifier_args)

    typed_nodes: list[TypedFunctionNode] = []
    for item, original_index in zip(
        modifier_args,
        _modifier_param_indexes(applied.overload.params),
        strict=True,
    ):
        index = offset + original_index
        expected = applied.params[index] if index < len(applied.params) else None
        expected = T.normalize(expected) if expected is not None else None
        if item.typed_node.dispatch_plan is not None:
            typed_nodes.append(item.typed_node)
            continue
        if isinstance(expected, T.FunctionType):
            overloads = tuple(
                overload
                for overload in item.typed_node.overloads
                if _function_overload_matches_type(overload, expected, ctx)
            )
            if overloads:
                typed_nodes.append(
                    TypedFunctionNode(item.typed_node.node, expected, overloads)
                )
                continue
        typed_nodes.append(item.typed_node)
    return tuple(typed_nodes)


def _union_dispatch_plan_for_function(
    function: TypedFunctionNode,
    expected: T.Type,
    ctx: T.Context,
) -> T.UnionDispatchPlan | None:
    """Compute union dispatch plan for function during static analysis."""
    expected = T.normalize(expected)
    if not isinstance(expected, T.FunctionType):
        return None
    overloads = tuple(
        typing.overload
        for typing in function.overloads
        if isinstance(typing.overload, T.Overload)
    )
    if len(overloads) != len(function.overloads):
        return None
    return T.union_dispatched_callable_plan(T.Overloads(*overloads), expected, ctx)


def _function_overload_matches_type(
    overload: FunctionOverloadTyping,
    expected: T.FunctionType,
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of function overload matches type during static analysis."""
    typ = T.normalize(overload.typ)
    return isinstance(typ, T.FunctionType) and (
        T.same(typ, expected) or T.compatible(typ, expected, ctx)
    )


def _show_modifier_counts(overloads: tuple[T.Overload, ...]) -> str:
    """Format modifier counts during static analysis."""
    counts = sorted(
        {len(_modifier_param_indexes(overload.params)) for overload in overloads}
    )
    if len(counts) == 1:
        return str(counts[0])
    return " or ".join(str(count) for count in counts)


def _modifier_param_indexes(params: tuple[T.Type, ...]) -> tuple[int, ...]:
    """Compute modifier param indexes during static analysis."""
    return tuple(
        index for index, param in enumerate(params) if _is_callable_parameter(param)
    )


def _is_callable_parameter(param: T.Type) -> bool:
    """Return whether the value is callable parameter."""
    param = T.normalize(param)
    return isinstance(param, T.FunctionType) or _is_bare_function_type(param)


def _unique_permutations(
    modifier_args: tuple[ModifierArgumentAnalysis, ...],
) -> Iterator[tuple[ModifierArgumentAnalysis, ...]]:
    """Compute unique permutations during static analysis."""
    seen: set[tuple[T.Type, ...]] = set()
    for candidate in permutations(modifier_args):
        key = tuple(item.typ for item in candidate)
        if key in seen:
            continue
        seen.add(key)
        yield candidate


def _apply_overload_to_branch(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    branch: AnalysisBranch,
    ctx: T.Context,
    env: T.Environment | None = None,
    disambiguation: tuple[T.Type | None, ...] = (),
    analyser: Analyser | None = None,
) -> OverloadApplication | None:
    """Apply overload to branch during static analysis."""
    if _overload_needs_call_site_checking(overload):
        return _apply_call_site_checked_overload(
            overload,
            args,
            branch,
            ctx,
            env,
            disambiguation,
            analyser,
        )
    args = _row_views_for_arguments(args, overload.params, env)
    original_overload = overload
    rank_values = _initial_rank_values(overload.params, args)
    rank_values = _evaluate_where_clause(overload, args, rank_values)
    if rank_values is None:
        return None
    overload = _substitute_overload_ranks(overload, rank_values)
    substitution = _branch_argument_substitution(args, overload.params, ctx)
    if substitution is None:
        return None
    specialized_branch = _specialize_branch_arguments(branch, substitution)
    specialized_args = tuple(_substitute_branch_type(arg, substitution) for arg in args)
    attempt = T.try_apply_overload(
        overload,
        specialized_args,
        ctx,
        disambiguation=disambiguation,
    )
    applied = attempt.applied
    if applied is None:
        return None
    actual_returns = _apply_data_tag_flow(
        specialized_args,
        overload.returns,
        applied.actual_returns,
        ctx,
    )
    applied = T.AppliedOverload(
        original_overload,
        applied.substitution,
        applied.params,
        applied.returns,
        actual_returns,
        applied.scores,
        applied.vectorised,
        applied.vectorised_depths,
        tuple(sorted(rank_values.items())),
        element_tags=_propagated_element_tags(
            overload,
            specialized_args,
            applied.substitution,
        ),
        vectorised_target_ranks=applied.vectorised_target_ranks,
        runtime_static_values=applied.runtime_static_values,
    )
    return OverloadApplication(applied, specialized_branch)


def _apply_tag_overlay(
    element: Symbol,
    args: tuple[T.Type, ...],
    applied: T.AppliedOverload,
    ctx: T.Context,
    env: T.Environment,
) -> T.AppliedOverload:
    """Apply tag overlay during static analysis."""
    matches: list[T.AppliedOverload] = []
    for overlay in env.overlays_for(element):
        candidate = T.try_apply_overload(overlay.overload, args, ctx).applied
        if candidate is None:
            continue
        actual_returns = _apply_data_tag_flow(
            args,
            overlay.overload.returns,
            candidate.actual_returns,
            ctx,
        )
        matches.append(
            T.AppliedOverload(
                applied.overload,
                candidate.substitution,
                applied.params,
                candidate.returns,
                actual_returns,
                applied.scores,
                applied.vectorised,
                applied.vectorised_depths,
                applied.rank_values,
                applied.runtime_consumed_count,
                applied.element_tags,
                vectorised_target_ranks=applied.vectorised_target_ranks,
                runtime_static_values=applied.runtime_static_values,
            )
        )
    if not matches:
        return applied
    return sorted(
        matches,
        key=lambda item: tuple(score.value for score in item.scores),
        reverse=True,
    )[0]


def _apply_call_site_checked_overload(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    branch: AnalysisBranch,
    ctx: T.Context,
    env: T.Environment | None,
    disambiguation: tuple[T.Type | None, ...],
    analyser: Analyser | None,
) -> OverloadApplication | None:
    """Apply call site checked overload during static analysis."""
    args = _row_views_for_arguments(args, overload.params, env)
    if len(args) != len(overload.params):
        return None
    if not _call_site_explicit_args_match(overload.params, args, ctx):
        return None
    if disambiguation and len(disambiguation) != len(args):
        return None

    for extra_count in range(len(branch.stack) + 1):
        stack_args = branch.stack.items[-extra_count:] if extra_count else ()
        call_params = stack_args + args
        concrete = _call_site_checked_overload_signature(
            overload, call_params, ctx, analyser
        )
        if concrete is None or len(concrete.params) < len(args):
            continue
        consumed_count = _call_site_consumed_count(overload, concrete, extra_count)
        if consumed_count is None:
            continue
        concrete_stack_count = len(concrete.params) - len(args)
        if concrete_stack_count < 0:
            continue
        if concrete_stack_count <= len(branch.stack):
            concrete_stack_args = (
                branch.stack.items[-concrete_stack_count:]
                if concrete_stack_count
                else ()
            )
            result_branch = branch.with_stack(branch.stack.pop(consumed_count))
        else:
            stack_params = concrete.params[:concrete_stack_count]
            sourced = branch.source_arguments(stack_params)
            if sourced is None:
                continue
            concrete_stack_args, sourced_branch = sourced
            preserved = concrete_stack_args[: concrete_stack_count - consumed_count]
            result_branch = sourced_branch.push(*preserved)
        concrete_args = concrete_stack_args + args
        if len(concrete.params) != len(concrete_args):
            continue
        rank_values = _initial_rank_values(concrete.params, concrete_args)
        rank_values = _evaluate_where_clause(concrete, concrete_args, rank_values)
        if rank_values is None:
            continue
        concrete = _substitute_overload_ranks(concrete, rank_values)
        candidate = T.try_apply_overload(concrete, concrete_args, ctx).applied
        if candidate is None:
            continue
        actual_returns = _apply_data_tag_flow(
            concrete_args,
            concrete.returns,
            candidate.actual_returns,
            ctx,
        )
        applied = T.AppliedOverload(
            overload,
            candidate.substitution,
            concrete.params,
            concrete.returns,
            actual_returns,
            candidate.scores,
            candidate.vectorised,
            candidate.vectorised_depths,
            tuple(sorted(rank_values.items())),
            consumed_count + len(args),
            element_tags=_propagated_element_tags(
                concrete,
                concrete_args,
                candidate.substitution,
            ),
            vectorised_target_ranks=candidate.vectorised_target_ranks,
            runtime_static_values=concrete.runtime_static_values,
        )
        return OverloadApplication(applied, result_branch)
    return None


def _overload_needs_call_site_checking(overload: T.Overload) -> bool:
    """Return the Boolean result of overload needs call site checking during static analysis."""
    return any(_is_call_site_checked_param(param) for param in overload.params)


def _call_site_explicit_args_match(
    params: tuple[T.Type, ...],
    args: tuple[T.Type, ...],
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of call site explicit args match during static analysis."""
    return all(
        _call_site_placeholder_accepts(param, arg, ctx)
        for param, arg in zip(params, args, strict=True)
    )


def _call_site_checked_overload_signature(
    overload: T.Overload,
    call_params: tuple[T.Type, ...],
    ctx: T.Context,
    analyser: Analyser | None,
) -> T.Overload | None:
    """Build the signature for call site checked overload during static analysis."""
    if callable(overload.call_site_body):
        if (
            analyser is not None
            and getattr(overload.call_site_body, "__name__", "") == "_call_call_site"
            and call_params
        ):
            function_type = call_params[-1]
            explicit = call_params[:-1]
            deferred = False
            for candidate in _callable_overloads(function_type):
                if not (
                    isinstance(candidate.call_site_body, tuple)
                    and len(candidate.call_site_body) == 2
                ):
                    continue
                deferred = True
                outer, node = candidate.call_site_body
                if not isinstance(outer, AnalysisBranch) or not isinstance(
                    node, FunctionNode
                ):
                    continue
                analysis = analyser._analyse_function_at_call_site(
                    outer,
                    node,
                    explicit,
                )
                if analysis is None:
                    continue
                candidates = _callable_overloads(analysis.typ)
                if len(candidates) != 1:
                    continue
                concrete = candidates[0]
                return T.Overload(
                    (*concrete.params, function_type),
                    concrete.returns,
                    call_site_body=len(concrete.params),
                )
            if deferred:
                return None
        return overload.call_site_body(call_params)
    if overload.call_site_body is not None and analyser is not None:
        outer, node = overload.call_site_body
        analysis = analyser._analyse_function_at_call_site(outer, node, call_params)
        if analysis is None:
            return None
        overloads = _callable_overloads(analysis.typ)
        return overloads[0] if len(overloads) == 1 else None
    if len(call_params) < len(overload.params):
        return None
    explicit = call_params[-len(overload.params) :] if overload.params else ()
    if not _call_site_explicit_args_match(overload.params, explicit, ctx):
        return None
    return T.Overload(
        params=call_params,
        returns=overload.returns,
        generic_constraints=overload.generic_constraints,
        where_clause=overload.where_clause,
        param_names=(None,) * (len(call_params) - len(overload.params))
        + overload.param_names,
        element_tags=overload.element_tags,
        annotation_error=overload.annotation_error,
        annotation_warning=overload.annotation_warning,
        param_defaults=(None,) * len(call_params),
    )


def _call_site_consumed_count(
    overload: T.Overload,
    concrete: T.Overload,
    extra_count: int,
) -> int | None:
    """Compute call site consumed count during static analysis."""
    consumed = (
        concrete.call_site_body
        if isinstance(concrete.call_site_body, int)
        else len(concrete.params) - len(overload.params)
    )
    if consumed < 0:
        return None
    return consumed


def _propagated_element_tags(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    substitution: dict[str, T.Type] | None = None,
) -> frozenset[T.ElementTag]:
    """Compute propagated element tags during static analysis."""
    tags = {
        tag
        for tag in _substitute_branch_element_tags(
            overload.element_tags,
            substitution or {},
        )
        if not tag.absent
    }
    for arg in args:
        arg = T.normalize(arg)
        if isinstance(arg, T.FunctionType):
            tags.update(tag for tag in arg.element_tags if not tag.absent)
        elif isinstance(arg, T.OverloadSetType):
            for candidate in arg.overloads:
                tags.update(tag for tag in candidate.element_tags if not tag.absent)
    return frozenset(tags)


def _initial_rank_values(
    params: tuple[T.Type, ...],
    args: tuple[T.Type, ...],
) -> dict[str, int]:
    """Collect the values for initial rank during static analysis."""
    values: dict[str, int] = {}
    for param, arg in zip(params, args, strict=False):
        _collect_rank_values(param, arg, values)
    return values


def _collect_rank_values(
    pattern: T.Type,
    actual: T.Type,
    values: dict[str, int],
) -> None:
    """Collect rank values during static analysis."""
    pattern = T.normalize(pattern)
    actual = T.normalize(actual)
    if isinstance(pattern, T.CollectionType) and isinstance(actual, T.CollectionType):
        if isinstance(pattern.rank, T.RankVariable) and isinstance(actual.rank, int):
            values.setdefault(pattern.rank.name, actual.rank)
        _collect_rank_values(pattern.base, actual.base, values)
    elif isinstance(pattern, T.NominalType) and isinstance(actual, T.NominalType):
        for left, right in zip(pattern.args, actual.args, strict=False):
            _collect_rank_values(left, right, values)
    elif isinstance(pattern, T.FunctionType) and isinstance(actual, T.FunctionType):
        for left, right in zip(
            pattern.params + pattern.returns,
            actual.params + actual.returns,
            strict=False,
        ):
            _collect_rank_values(left, right, values)
    elif isinstance(pattern, T.TupleType) and isinstance(actual, T.TupleType):
        for left, right in zip(pattern.params, actual.params, strict=False):
            _collect_rank_values(left, right, values)
    elif isinstance(pattern, T.VariadicTupleType) and isinstance(actual, T.TupleType):
        _match_variadic_tuple_types(
            pattern,
            actual,
            lambda left, right: _collect_rank_values(left, right, values) or True,
        )
    elif isinstance(pattern, (T.TaggedType, T.ExactType, T.AtomicType)):
        _collect_rank_values(pattern.inner, actual, values)
    elif isinstance(actual, (T.TaggedType, T.ExactType, T.AtomicType)):
        _collect_rank_values(pattern, actual.inner, values)


def _match_variadic_tuple_types(
    pattern: T.VariadicTupleType,
    actual: T.TupleType,
    match: Callable[[T.Type, T.Type], bool],
) -> bool:
    """Return the Boolean result of match variadic tuple types during static analysis."""
    def rec(pattern_index: int, actual_index: int) -> bool:
        """Recursively continue the match variadic tuple types algorithm."""
        if pattern_index == len(pattern.items):
            return actual_index == len(actual.params)
        item = pattern.items[pattern_index]
        if item.repeated:
            if rec(pattern_index + 1, actual_index):
                return True
            for index in range(actual_index, len(actual.params)):
                if not match(item.typ, actual.params[index]):
                    return False
                if rec(pattern_index + 1, index + 1):
                    return True
            return False
        return (
            actual_index < len(actual.params)
            and match(
                item.typ,
                actual.params[actual_index],
            )
            and rec(pattern_index + 1, actual_index + 1)
        )

    return rec(0, 0)


def _evaluate_where_clause(
    overload: T.Overload,
    args: tuple[T.Type, ...],
    rank_values: dict[str, int],
) -> dict[str, int] | None:
    """Evaluate where clause during static analysis."""
    if not overload.where_clause:
        return rank_values
    variables: dict[str, StaticValue] = {
        name: value for name, value in rank_values.items()
    }
    for param_name, arg in zip(overload.param_names, args, strict=False):
        if param_name is not None:
            variables[param_name.text] = arg
    stack: list[StaticValue] = []
    for node in overload.where_clause:
        if not _static_eval_node(node, stack, variables):
            return None
    result = dict(rank_values)
    for name, value in variables.items():
        if isinstance(value, int) and not isinstance(value, bool):
            result[name] = value
    return result


StaticValue = int | bool | T.Type | tuple[T.Type, ...]


def _static_eval_node(
    node: ASTNode,
    stack: list[StaticValue],
    variables: dict[str, StaticValue],
) -> bool:
    """Return the Boolean result of static eval node during static analysis."""
    match node:
        case NumberLiteralNode(value):
            stack.append(int(value))
            return True
        case GetVariableNode(name):
            value = variables.get(name.text)
            if value is None:
                return False
            stack.append(value)
            return True
        case SetVariableNode(name):
            if not stack:
                return False
            variables[name.text] = stack.pop()
            return True
        case FieldAccessNode(name):
            if not stack:
                return False
            value = stack.pop()
            if isinstance(value, T.FunctionType):
                if value.params is None or value.returns is None:
                    return False
                match name.text:
                    case "inputs":
                        stack.append(value.params)
                        return True
                    case "outputs":
                        stack.append(value.returns)
                        return True
                    case "arity":
                        stack.append(len(value.params))
                        return True
                    case "multiplicity":
                        stack.append(len(value.returns))
                        return True
            return False
        case ElementNode(name, _, _, call_args):
            if call_args:
                for arg in call_args:
                    if arg.placeholder or arg.name is not None:
                        return False
                    for value_node in arg.value:
                        if not _static_eval_node(value_node, stack, variables):
                            return False
            return _static_eval_element(name.text, stack)
        case _:
            return False


def _static_eval_element(name: str, stack: list[StaticValue]) -> bool:
    """Return the Boolean result of static eval element during static analysis."""
    def pop_truthy_values(count: int) -> tuple[int | bool, ...] | None:
        """Collect the values for pop truthy during static analysis."""
        if len(stack) < count:
            return None
        values = tuple(stack[-count:])
        if not all(isinstance(value, (int, bool)) for value in values):
            return None
        del stack[-count:]
        return values

    if name in {"+", "-", "*", "max", "min", "<", ">", "<=", ">=", "==", "!="}:
        if len(stack) < 2:
            return False
        right = stack.pop()
        left = stack.pop()
        if name in {"==", "!="}:
            equal = left == right
            stack.append(equal if name == "==" else not equal)
            return True
        if not (
            isinstance(left, int)
            and not isinstance(left, bool)
            and isinstance(right, int)
            and not isinstance(right, bool)
        ):
            return False
        match name:
            case "+":
                stack.append(left + right)
            case "-":
                stack.append(left - right)
            case "*":
                stack.append(left * right)
            case "max":
                stack.append(max(left, right))
            case "min":
                stack.append(min(left, right))
            case "<":
                stack.append(left < right)
            case ">":
                stack.append(left > right)
            case "<=":
                stack.append(left <= right)
            case ">=":
                stack.append(left >= right)
        return True
    if name == "length":
        if not stack:
            return False
        value = stack.pop()
        if isinstance(value, T.TupleType):
            stack.append(len(value.params))
            return True
        if isinstance(value, tuple):
            stack.append(len(value))
            return True
        return False
    if name == "and":
        values = pop_truthy_values(2)
        if values is None:
            return False
        stack.append(bool(values[0]) and bool(values[1]))
        return True
    if name == "or":
        values = pop_truthy_values(2)
        if values is None:
            return False
        stack.append(bool(values[0]) or bool(values[1]))
        return True
    if name == "not":
        values = pop_truthy_values(1)
        if values is None:
            return False
        stack.append(not bool(values[0]))
        return True
    if name == "?":
        if not stack:
            return False
        return bool(stack.pop())
    if name == "dup":
        if not stack:
            return False
        stack.append(stack[-1])
        return True
    if name == "pop":
        if not stack:
            return False
        stack.pop()
        return True
    if name == "swap":
        if len(stack) < 2:
            return False
        stack[-1], stack[-2] = stack[-2], stack[-1]
        return True
    return False


def _substitute_overload_ranks(
    overload: T.Overload,
    ranks: dict[str, int],
) -> T.Overload:
    """Substitute overload ranks during static analysis."""
    return _transform_overload_types(
        overload,
        lambda typ: _substitute_rank_values(typ, ranks),
        element_tags=frozenset(
            _substitute_rank_values_in_element_tags(overload.element_tags, ranks)
        ),
    )


def _substitute_rank_values(typ: T.Type, ranks: dict[str, int]) -> T.Type:
    """Substitute rank values during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.CollectionType):
        rank = typ.rank
        if isinstance(rank, T.RankVariable):
            solved = ranks.get(rank.name)
            rank = solved if solved is not None else rank
        return T.C(type(typ), _substitute_rank_values(typ.base, ranks), rank)
    if isinstance(typ, T.NominalType):
        return T.N(typ.name, *(_substitute_rank_values(arg, ranks) for arg in typ.args))
    if isinstance(typ, T.UnionType):
        return T.U(*(_substitute_rank_values(item, ranks) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_substitute_rank_values(item, ranks) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(_substitute_rank_values(item, ranks) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(_substitute_rank_values(item.typ, ranks), item.repeated)
                for item in typ.items
            )
        )
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return T.Fn(
                None,
                None,
                _substitute_rank_values_in_element_tags(typ.element_tags, ranks),
            )
        return T.Fn(
            (_substitute_rank_values(item, ranks) for item in typ.params),
            (_substitute_rank_values(item, ranks) for item in typ.returns),
            _substitute_rank_values_in_element_tags(typ.element_tags, ranks),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(_substitute_rank_values(typ.inner, ranks), *typ.tags)
    if isinstance(typ, T.ExactType):
        return T.Exact(_substitute_rank_values(typ.inner, ranks))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_substitute_rank_values(typ.inner, ranks))
    return typ


def _substitute_rank_values_in_element_tags(
    tags: frozenset[T.ElementTag],
    ranks: dict[str, int],
) -> tuple[T.ElementTag, ...]:
    """Substitute rank values in element tags during static analysis."""
    return tuple(
        T.ElementTag(
            tag.name,
            tuple(_substitute_rank_values(arg, ranks) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )


def _row_views_for_arguments(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    env: T.Environment | None,
) -> tuple[T.Type, ...]:
    """Determine the arguments for row views for during static analysis."""
    if env is None:
        return args
    return tuple(
        _row_view_for_argument(arg, param, env)
        for arg, param in zip(args, params, strict=True)
    )


def _row_view_for_argument(
    arg: T.Type,
    param: T.Type,
    env: T.Environment,
) -> T.Type:
    """Compute row view for argument during static analysis."""
    arg = T.normalize(arg)
    param = T.normalize(param)
    if not isinstance(param, T.RowType):
        return arg
    if isinstance(arg, T.RowType):
        return arg
    if not isinstance(arg, T.NominalType):
        return arg
    definition = env.lookup_object(arg.name)
    if definition is None:
        return arg
    return T.Row(
        arg,
        *(
            T.Field(attribute.name, attribute.typ)
            for attribute in definition.attributes
            if attribute.access.text in {"public", "readable"}
        ),
    )


def _specialize_branch_arguments(
    branch: AnalysisBranch,
    substitution: dict[str, T.Type],
) -> AnalysisBranch:
    """Specialize branch arguments during static analysis."""
    for name, typ in substitution.items():
        if _contains_named_type_var(typ, name):
            continue
        branch = branch.refine_type(T.V(name), typ)
    return branch


def _apply_data_tag_flow(
    args: tuple[T.Type, ...],
    declared_returns: tuple[T.Type, ...],
    actual_returns: tuple[T.Type, ...],
    ctx: T.Context,
) -> tuple[T.Type, ...]:
    """Strip implicit tags that are not preserved by the chosen signature."""
    explicit_tags = tuple(_explicit_tags(ret) for ret in declared_returns)
    return tuple(
        _strip_implicit_computed_tags(
            ret,
            explicit_tags[index] if index < len(explicit_tags) else frozenset(),
            ctx,
        )
        for index, ret in enumerate(actual_returns)
    )


def _explicit_tags(typ: T.Type) -> frozenset[T.DataTag]:
    """Compute explicit tags during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return typ.tags | _explicit_tags(typ.inner)
    if isinstance(typ, T.CollectionType):
        return _explicit_tags(typ.base)
    if isinstance(typ, T.UnionType):
        result: set[T.DataTag] = set()
        for item in typ.items:
            result.update(_explicit_tags(item))
        return frozenset(result)
    return frozenset()


def _strip_implicit_computed_tags(
    typ: T.Type,
    explicit_tags: frozenset[T.DataTag],
    ctx: T.Context,
) -> T.Type:
    """Compute strip implicit computed tags during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        kept = tuple(tag for tag in typ.tags if tag in explicit_tags)
        inner = _strip_implicit_computed_tags(typ.inner, explicit_tags, ctx)
        return _with_data_tags(inner, kept, ctx) if kept else inner
    if isinstance(typ, T.CollectionType):
        return T.C(
            type(typ),
            _strip_implicit_computed_tags(typ.base, explicit_tags, ctx),
            typ.rank,
        )
    if isinstance(typ, T.UnionType):
        return T.U(
            *(
                _strip_implicit_computed_tags(item, explicit_tags, ctx)
                for item in typ.items
            )
        )
    return typ


@dataclass(frozen=True)
class StickyInputTag:
    tag: T.DataTag
    rank: int


def _sticky_input_tags(typ: T.Type, ctx: T.Context) -> tuple[StickyInputTag, ...]:
    """Compute sticky input tags during static analysis."""
    typ = T.normalize(typ)
    if not isinstance(typ, T.TaggedType):
        return ()
    return tuple(
        StickyInputTag(tag, max(_type_rank(typ.inner) - tag.depth, 0))
        for tag in sorted(typ.tags)
        if ctx.is_constructed_like_tag(tag.name)
    )


def _propagate_sticky_tags(
    typ: T.Type,
    sticky_inputs: tuple[StickyInputTag, ...],
    ctx: T.Context,
) -> T.Type:
    """Compute propagate sticky tags during static analysis."""
    result = typ
    output_rank = _type_rank(result)
    for sticky in sticky_inputs:
        if output_rank >= sticky.rank:
            result = _tag_at_depth(
                result,
                sticky.tag.name,
                max(output_rank - 1, 0),
                ctx,
            )
    return result


def _tag_at_depth(typ: T.Type, tag: str, depth: int, ctx: T.Context) -> T.Type:
    """Compute tag at depth during static analysis."""
    return _with_data_tags(typ, (T.DataTag(tag, depth),), ctx)


def _with_data_tags(
    typ: T.Type,
    tags: Iterable[T.DataTag],
    ctx: T.Context,
) -> T.Type:
    """Compute with data tags during static analysis."""
    existing: set[T.DataTag] = set()
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        existing.update(typ.tags)
        typ = typ.inner
    for tag in tags:
        existing = {
            item
            for item in existing
            if Symbol(item.name) not in ctx.tag_disjoints(tag.name)
        }
        existing.add(tag)
        parent = ctx.tag_parent(tag.name)
        if parent is not None:
            existing.add(T.DataTag(parent.text, tag.depth))
    return T.Tagged(typ, *sorted(existing)) if existing else typ


def _remove_data_tag(typ: T.Type, tag: T.DataTag) -> T.Type | None:
    """Remove data tag during static analysis."""
    typ = T.normalize(typ)
    if not isinstance(typ, T.TaggedType):
        return None
    existing = set(typ.tags)
    positive = T.DataTag(tag.name, tag.depth)
    if positive not in existing:
        return None
    existing.remove(positive)
    return T.Tagged(typ.inner, *sorted(existing)) if existing else typ.inner


def _show_tag(tag: T.DataTag) -> str:
    """Format tag during static analysis."""
    prefix = "#!" if tag.absent else "#"
    depth = "+" * tag.depth
    return f"{prefix}{tag.name}{depth}"


def _validator_overload_ok(overload: T.Overload, ctx: T.Context) -> bool:
    """Return the Boolean result of validator overload ok during static analysis."""
    return len(overload.returns) == 1 and T.assignable(
        overload.returns[0],
        T.WithTag(T.Number, "boolean"),
        ctx,
    )


def _static_validator_result(body: tuple[TypedNode, ...]) -> bool | None:
    """Compute the result for static validator during static analysis."""
    if len(body) != 1:
        return None
    node = body[0].node
    if isinstance(node, ElementNode):
        if node.name == Symbol("true"):
            return True
        if node.name == Symbol("false"):
            return False
    return None


def _disjoint_data_tags(
    typ: T.Type,
    ctx: T.Context,
) -> tuple[Symbol, Symbol] | None:
    """Compute disjoint data tags during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        positive = [Symbol(tag.name) for tag in typ.tags if not tag.absent]
        seen: set[Symbol] = set()
        for tag in positive:
            conflict = next(
                (name for name in seen if name in ctx.tag_disjoints(tag)),
                None,
            )
            if conflict is not None:
                return conflict, tag
            seen.add(tag)
        return _disjoint_data_tags(typ.inner, ctx)
    if isinstance(typ, T.CollectionType):
        return _disjoint_data_tags(typ.base, ctx)
    if isinstance(typ, T.UnionType):
        for item in typ.items:
            conflict = _disjoint_data_tags(item, ctx)
            if conflict is not None:
                return conflict
    if isinstance(typ, T.FunctionType):
        for item in (*(typ.params or ()), *(typ.returns or ())):
            conflict = _disjoint_data_tags(item, ctx)
            if conflict is not None:
                return conflict
    return None


def _type_rank(typ: T.Type) -> int:
    """Determine the collection rank for type during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _type_rank(typ.inner)
    if isinstance(typ, T.CollectionType):
        return typ.rank
    return 0


def _refine_branch_like(
    branch: AnalysisBranch,
    refined: AnalysisBranch,
) -> AnalysisBranch:
    """Refine branch like during static analysis."""
    substitution = _branch_pair_substitution(branch.inputs, refined.inputs)
    if substitution is None:
        return replace(
            branch,
            element_tags=refined.element_tags,
            data_element_uses=refined.data_element_uses,
        )
    return replace(
        _specialize_branch_arguments(branch, substitution),
        element_tags=refined.element_tags,
        data_element_uses=refined.data_element_uses,
    )


def _branch_pair_substitution(
    source: tuple[T.Type, ...],
    target: tuple[T.Type, ...],
) -> dict[str, T.Type] | None:
    """Compute branch pair substitution during static analysis."""
    if len(source) != len(target):
        return None
    substitution: dict[str, T.Type] = {}
    for left, right in zip(source, target, strict=True):
        constraints = _solve_branch_argument(left, right, T.Context())
        if constraints is None:
            return None
        for name, typ in constraints.items():
            existing = substitution.get(name)
            if existing is not None and not T.same(existing, typ):
                return None
            substitution[name] = typ
    return substitution


def _branch_argument_substitution(
    args: tuple[T.Type, ...],
    params: tuple[T.Type, ...],
    ctx: T.Context,
) -> dict[str, T.Type] | None:
    """Compute branch argument substitution during static analysis."""
    substitution: dict[str, T.Type] = {}
    for arg, param in zip(args, params, strict=True):
        arg = _substitute_branch_type(arg, substitution)
        param = _substitute_branch_type(param, substitution)
        constraints = _solve_branch_argument(arg, param, ctx)
        if constraints is None or (not constraints and _contains_type_var(param)):
            constraints = _solve_type_argument(arg, param, ctx)
        if constraints is None:
            if T.compatible(arg, param, ctx):
                continue
            return None
        for name, typ in constraints.items():
            existing = substitution.get(name)
            if existing is not None and not T.same(existing, typ):
                return None
            substitution[name] = typ
    return substitution


def _solve_type_argument(
    arg: T.Type,
    param: T.Type,
    ctx: T.Context | None = None,
) -> dict[str, T.Type] | None:
    """Compute solve type argument during static analysis."""
    solved = T._solve(param, arg, ctx)
    if solved is None:
        return None
    substitution: dict[str, T.Type] = {}
    for name, values in solved.items():
        combined = T._combine_all(values, ctx)
        if combined is None:
            return None
        substitution[name] = combined
    return substitution


def _solve_branch_argument(
    arg: T.Type,
    param: T.Type,
    ctx: T.Context,
) -> dict[str, T.Type] | None:
    """Compute solve branch argument during static analysis."""
    constraints: dict[str, T.Type] = {}

    def bind(name: str, typ: T.Type) -> bool:
        """Bind one inferred value during static analysis."""
        previous = constraints.get(name)
        if previous is None:
            constraints[name] = typ
            return True
        return T.same(previous, typ)

    def rec(actual: T.Type, expected: T.Type) -> bool:
        """Recursively continue the solve branch argument algorithm."""
        actual = T.normalize(actual)
        expected = T.normalize(expected)
        if isinstance(actual, T.VarType):
            if _contains_named_type_var(expected, actual.name):
                return True
            return bind(actual.name, expected)
        if isinstance(expected, T.VarType):
            if _contains_named_type_var(actual, expected.name):
                return True
            return bind(expected.name, actual)
        if (
            isinstance(actual, T.FunctionType)
            and isinstance(expected, T.FunctionType)
            and actual.params is not None
            and actual.returns is not None
            and expected.params is not None
            and expected.returns is not None
            and (_contains_type_var(actual) or _contains_type_var(expected))
        ):
            return (
                len(actual.params) == len(expected.params)
                and len(actual.returns) == len(expected.returns)
                and all(
                    rec(left, right)
                    for left, right in zip(
                        actual.params + actual.returns,
                        expected.params + expected.returns,
                        strict=True,
                    )
                )
            )
        if T.compatible(actual, expected, ctx):
            return True
        if isinstance(actual, T.RowType):
            if isinstance(expected, T.RowType):
                if not rec(actual.base, expected.base):
                    return False
                expected_fields = {field.name: field.typ for field in expected.fields}
                for field in actual.fields:
                    expected_field = expected_fields.get(field.name)
                    if expected_field is None or not rec(field.typ, expected_field):
                        return False
                return True
            return rec(actual.base, expected)
        if isinstance(actual, T.NominalType) and isinstance(expected, T.NominalType):
            return (
                actual.name == expected.name
                and len(actual.args) == len(expected.args)
                and all(
                    rec(left, right)
                    for left, right in zip(actual.args, expected.args, strict=True)
                )
            )
        if isinstance(actual, T.CollectionType) and isinstance(
            expected,
            T.CollectionType,
        ):
            return (
                type(actual) is type(expected)
                and actual.rank == expected.rank
                and rec(actual.base, expected.base)
            )
        if isinstance(actual, T.CollectionType):
            return rec(actual.base, expected)
        if isinstance(actual, T.FunctionType) and isinstance(expected, T.FunctionType):
            return (
                len(actual.params) == len(expected.params)
                and len(actual.returns) == len(expected.returns)
                and all(
                    rec(left, right)
                    for left, right in zip(
                        actual.params + actual.returns,
                        expected.params + expected.returns,
                        strict=True,
                    )
                )
            )
        if isinstance(actual, T.TupleType) and isinstance(expected, T.TupleType):
            return len(actual.params) == len(expected.params) and all(
                rec(left, right)
                for left, right in zip(actual.params, expected.params, strict=True)
            )
        if isinstance(actual, T.TupleType) and isinstance(
            expected,
            T.VariadicTupleType,
        ):
            return _match_variadic_tuple_types(
                expected,
                actual,
                lambda left, right: rec(right, left),
            )
        return False

    return constraints if rec(arg, param) else None


def _substitute_branch_type(typ: T.Type, substitution: dict[str, T.Type]) -> T.Type:
    """Substitute branch type during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return substitution.get(typ.name, typ)
    if isinstance(typ, T.NominalType):
        return T.N(
            typ.name,
            *(_substitute_branch_type(arg, substitution) for arg in typ.args),
        )
    if isinstance(typ, T.UnionType):
        return T.U(*(_substitute_branch_type(item, substitution) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_substitute_branch_type(item, substitution) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(
            *(_substitute_branch_type(item, substitution) for item in typ.params)
        )
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(
                    _substitute_branch_type(item.typ, substitution),
                    item.repeated,
                )
                for item in typ.items
            )
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            _substitute_branch_type(typ.base, substitution),
            *(
                T.Field(
                    field.name,
                    _substitute_branch_type(field.typ, substitution),
                )
                for field in typ.fields
            ),
        )
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), _substitute_branch_type(typ.base, substitution), typ.rank)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return T.Fn(
                None,
                None,
                _substitute_branch_element_tags(typ.element_tags, substitution),
            )
        return T.Fn(
            (_substitute_branch_type(item, substitution) for item in typ.params),
            (_substitute_branch_type(item, substitution) for item in typ.returns),
            _substitute_branch_element_tags(typ.element_tags, substitution),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(_substitute_branch_type(typ.inner, substitution), *typ.tags)
    if isinstance(typ, T.ExactType):
        return T.Exact(_substitute_branch_type(typ.inner, substitution))
    if isinstance(typ, T.AtomicType):
        inner = _substitute_branch_type(typ.inner, substitution)
        if not isinstance(inner, T.VarType):
            return _atomic_base_type(inner)
        return T.Atomic(inner)
    return typ


def _substitute_branch_element_tags(
    tags: frozenset[T.ElementTag],
    substitution: dict[str, T.Type],
) -> tuple[T.ElementTag, ...]:
    """Substitute branch element tags during static analysis."""
    return tuple(
        T.ElementTag(
            tag.name,
            tuple(_substitute_branch_type(arg, substitution) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )


def _dominates(
    left: tuple[T.Specificity, ...],
    right: tuple[T.Specificity, ...],
) -> bool:
    """Return the Boolean result of dominates during static analysis."""
    if len(left) != len(right):
        return False
    saw_strict = False
    for a, b in zip(left, right, strict=True):
        if a.value > b.value:
            return False
        if a.value < b.value:
            saw_strict = True
    return saw_strict


def _returns_result_type(returns: tuple[T.Type, ...]) -> T.Type | None:
    """Determine the type of returns result during static analysis."""
    if len(returns) == 1:
        return returns[0]
    return None


def _consistent_function_returns(
    function: TypedFunctionNode,
) -> tuple[T.Type, ...] | None:
    """Determine the return types for consistent function during static analysis."""
    returns: tuple[T.Type, ...] | None = None
    for overload in function.overloads:
        typ = overload.typ
        if not isinstance(typ, T.FunctionType) or typ.returns is None:
            return None
        current = tuple(typ.returns)
        if returns is None:
            returns = current
            continue
        if len(returns) != len(current) or not all(
            T.same(left, right)
            for left, right in zip(returns, current, strict=True)
        ):
            return None
    return returns


def _single_function_return(function: TypedFunctionNode) -> T.Type | None:
    """Compute single function return during static analysis."""
    returns = _consistent_function_returns(function)
    if returns is None or len(returns) != 1:
        return None
    return returns[0]


def _extension_selector_arity(function: TypedFunctionNode) -> int | None:
    """Determine the required arity for extension selector during static analysis."""
    arity: int | None = None
    for overload in function.overloads:
        if len(overload.body) != 1:
            return None
        [body_node] = overload.body
        if not isinstance(body_node, TypedElementNode) or body_node.overload is None:
            return None
        current = len(body_node.overload.params)
        if arity is None:
            arity = current
        elif arity != current:
            return None
    return arity


def _unfold_emitted_type(
    state_types: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
) -> T.Type:
    """Determine the type of unfold emitted during static analysis."""
    if len(returns) <= len(state_types):
        missing = len(state_types) - len(returns)
        next_state = state_types[-missing:] + returns if missing else returns
        return next_state[-1]
    return _optional_present_type(returns[-1])


def _strict_optional_payload_type(typ: T.Type) -> T.Type | None:
    """Return the payload of exactly ``Some[T] | None`` optional types."""
    typ = T.normalize(typ)
    if not isinstance(typ, T.UnionType):
        return None
    found_none = False
    payloads: list[T.Type] = []
    for item in typ.items:
        item = T.normalize(item)
        if isinstance(item, T.NoneTypeNode):
            found_none = True
            continue
        if (
            isinstance(item, T.NominalType)
            and item.name == Symbol("Some")
            and len(item.args) == 1
        ):
            payloads.append(item.args[0])
            continue
        return None
    if not found_none or not payloads:
        return None
    return payloads[0] if len(payloads) == 1 else T.U(*payloads)


def _optional_access_result_type(field_type: T.Type) -> T.Type:
    """Lift a member result into an optional, flattening optional members."""
    return (
        field_type
        if _strict_optional_payload_type(field_type) is not None
        else T.optional(field_type)
    )


def _optional_present_type(typ: T.Type) -> T.Type:
    """Determine the type of optional present during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NoneTypeNode):
        return T.Never()
    if (
        isinstance(typ, T.NominalType)
        and typ.name == Symbol("Some")
        and len(typ.args) == 1
    ):
        return typ.args[0]
    if not isinstance(typ, T.UnionType):
        return typ
    present: list[T.Type] = []
    for item in typ.items:
        item = T.normalize(item)
        if isinstance(item, T.NoneTypeNode):
            continue
        if (
            isinstance(item, T.NominalType)
            and item.name == Symbol("Some")
            and len(item.args) == 1
        ):
            present.append(item.args[0])
        else:
            present.append(item)
    if not present:
        return T.Never()
    return T.U(*present)


def _selector_value_count(selectors: tuple[IndexSelector, ...]) -> int:
    """Compute selector value count during static analysis."""
    count = 0
    for selector in selectors:
        count += bool(selector.start)
        count += bool(selector.stop)
        count += bool(selector.step)
    return count


def _indexed_type(
    receiver_type: T.Type,
    selectors: tuple[IndexSelector, ...],
    spread: bool,
) -> T.Type:
    """Determine the type of indexed during static analysis."""
    typ = T.normalize(receiver_type)
    for index, selector in enumerate(selectors):
        item = typ if selector.is_slice else _single_index_type(typ)
        if index + 1 < len(selectors):
            typ = item
            continue
        if spread:
            return item
        if len(selectors) > 1:
            return T.ExactList(item)
        return item
    return T.V("Indexed")


def _selectors_assignable(
    receiver_type: T.Type,
    selectors: tuple[IndexSelector, ...],
    index_types: tuple[T.Type, ...],
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of selectors assignable during static analysis."""
    expected = _selector_expected_types(receiver_type, selectors)
    return len(expected) == len(index_types) and all(
        T.assignable(_index_value_type(actual), target, ctx)
        for actual, target in zip(index_types, expected, strict=True)
    )


def _index_value_type(typ: T.Type) -> T.Type:
    """Strip data tags from a value used as an index.

    Unit-like tags refine an integer's meaning without changing its suitability
    as a list or string index. Runtime indexing already unwraps tagged values.
    """
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _index_value_type(typ.inner)
    if isinstance(typ, T.UnionType):
        return T.U(*(_index_value_type(item) for item in typ.items))
    return typ


def _selector_expected_types(
    receiver_type: T.Type,
    selectors: tuple[IndexSelector, ...],
) -> tuple[T.Type, ...]:
    """Determine the types used for selector expected during static analysis."""
    typ = T.normalize(receiver_type)
    expected: list[T.Type] = []
    for selector in selectors:
        key_type = _single_index_key_type(typ)
        slice_bound_type = T.U(T.Integer, T.ExactList(T.Integer))
        if selector.start:
            expected.append(slice_bound_type if selector.is_slice else key_type)
        if selector.stop:
            expected.append(slice_bound_type)
        if selector.step:
            expected.append(T.Integer)
        typ = typ if selector.is_slice else _single_index_type(typ)
    return tuple(expected)


def _indexed_assignment_type(
    receiver_type: T.Type,
    selectors: tuple[IndexSelector, ...],
    value_type: T.Type,
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of indexed assignment during static analysis."""
    if len(selectors) == 1 and selectors[0].is_slice:
        slice_type = _indexed_type(receiver_type, selectors, spread=False)
        if T.assignable(value_type, slice_type, ctx):
            return receiver_type
        return _single_index_assignment_type(receiver_type, value_type, ctx)
    if len(selectors) != 1:
        item_type = _indexed_type(receiver_type, selectors, spread=False)
        return receiver_type if T.assignable(value_type, item_type, ctx) else None
    return _single_index_assignment_type(receiver_type, value_type, ctx)


def _single_index_assignment_type(
    receiver_type: T.Type,
    value_type: T.Type,
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of single index assignment during static analysis."""
    typ = T.normalize(receiver_type)
    if isinstance(typ, T.TaggedType):
        updated = _single_index_assignment_type(typ.inner, value_type, ctx)
        return None if updated is None else T.Tagged(updated, *typ.tags)
    if isinstance(typ, T.CollectionType):
        if T.assignable(value_type, typ.base, ctx):
            return receiver_type
        if T.assignable(typ.base, value_type, ctx):
            return T.C(type(typ), value_type, typ.rank)
        return None
    if isinstance(typ, T.NominalType):
        if typ.name.text == "Dict" and len(typ.args) == 2:
            key, item = typ.args
            if T.assignable(value_type, item, ctx):
                return receiver_type
            if T.assignable(item, value_type, ctx):
                return T.N(typ.name, key, value_type)
            return None
        if typ.name.text == "String":
            return receiver_type if T.assignable(value_type, T.String, ctx) else None
    item_type = _single_index_type(receiver_type)
    return receiver_type if T.assignable(value_type, item_type, ctx) else None


def _single_index_key_type(typ: T.Type) -> T.Type:
    """Determine the type of single index key during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _single_index_key_type(typ.inner)
    if isinstance(typ, T.UnionType):
        return T.U(*(_single_index_key_type(item) for item in typ.items))
    if (
        isinstance(typ, T.NominalType)
        and typ.name.text == "Dict"
        and len(typ.args) == 2
    ):
        return typ.args[0]
    return T.Number


def _single_index_type(typ: T.Type) -> T.Type:
    """Determine the type of single index during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _single_index_type(typ.inner)
    if isinstance(typ, T.UnionType):
        return T.U(*(_single_index_type(item) for item in typ.items))
    if isinstance(typ, T.CollectionType):
        return T.collection_item_type(typ)
    if isinstance(typ, T.TupleType):
        return T.U(*typ.params) if typ.params else T.Never()
    if isinstance(typ, T.NominalType):
        if typ.name.text == "String":
            return T.String
        if typ.name.text == "Dict" and len(typ.args) == 2:
            return typ.args[1]
    return T.V("Indexed")


def _nominal_name(typ: T.Type) -> Symbol | None:
    """Return the canonical name for nominal during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return typ.name
    return None


def _closed_match_members(
    env: T.Environment,
    name: Symbol,
) -> tuple[Symbol, ...] | None:
    """Compute closed match members during static analysis."""
    variant = env.lookup_variant(name)
    if variant is not None:
        return variant.members
    enum = env.lookup_enum(name)
    if enum is not None:
        return tuple(member.name for member in enum.members)
    return None


def _resolve_closed_member(
    expected: tuple[Symbol, ...],
    typ: T.Type,
) -> Symbol | None:
    """Compute resolve closed member during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NoneTypeNode):
        name = Symbol("None")
    else:
        name = _nominal_name(typ)
    if name is None:
        return None
    for member in expected:
        if name == member or name.text == member.text.rsplit(".", 1)[-1]:
            return member
    return None


def _match_case_pattern_types(
    patterns: tuple[MatchPatternNode, ...],
) -> Iterator[T.Type]:
    """Determine the types used for match case pattern during static analysis."""
    for pattern in patterns:
        yield from _match_pattern_types(pattern)


def _try_handler_output(
    output: AnalysisBranch,
    branch: AnalysisBranch,
    handler: TryHandlerNode,
) -> AnalysisBranch:
    """Compute try handler output during static analysis."""
    handler_result = _returns_result_type(output.stack.items)
    if handler_result is None:
        handler_result = T.NoneType()
    return (
        _refine_branch_like(branch, output)
        .with_stack(branch.stack.push(T.N(Symbol("PanicError"), handler_result)))
        .emit(TypedNode(handler, handler_result))
    )


def _join_try_output(
    branch: AnalysisBranch,
    joined: AnalysisBranch | None,
    output: AnalysisBranch,
) -> AnalysisBranch | None:
    """Join try output during static analysis."""
    if joined is None:
        return output
    if joined.inputs != output.inputs:
        return None
    stack = merge_stacks(joined.stack, output.stack)
    variables = joined.variables.merge_against(
        output.variables,
        branch.variables,
    )
    return (
        _refine_branch_like(branch, joined)
        .with_stack(stack)
        .with_variables(variables)
        .with_element_tags(output.element_tags)
        .with_data_element_uses(output.data_element_uses)
    )


def _typed_block(
    outputs: BranchSet,
    start: int,
    source_nodes: tuple[ASTNode, ...],
) -> tuple[ASTNode | TypedNode, ...]:
    """Return a stable typed block, falling back when branch metadata diverges."""
    suffixes = tuple(output.typed_body[start:] for output in outputs)
    if not suffixes:
        return source_nodes
    first = suffixes[0]
    if all(suffix == first for suffix in suffixes[1:]):
        return first
    return source_nodes


def _match_case_output(
    output: AnalysisBranch,
    baseline: AnalysisBranch,
    node: MatchNode,
) -> AnalysisBranch:
    """Compute match case output during static analysis."""
    candidate = output
    if candidate.break_type is not None:
        typ = candidate.break_type
        if typ is None:
            typ = _returns_result_type(candidate.stack.items)
        candidate = candidate.emit(TypedNode(node, typ))
    return replace(
        candidate,
        typed_body=baseline.typed_body,
        input_mode=baseline.input_mode,
        cycle_params=baseline.cycle_params,
        cycle_index=baseline.cycle_index,
    )


def _join_match_output(
    *,
    original: AnalysisBranch,
    baseline: AnalysisBranch,
    joined: AnalysisBranch | None,
    candidate: AnalysisBranch,
) -> AnalysisBranch | None:
    """Join match output during static analysis."""
    if joined is None:
        return candidate
    if joined.inputs != candidate.inputs:
        merged_inputs = _merge_branch_inputs(joined.inputs, candidate.inputs)
        if merged_inputs is None:
            return None
    else:
        merged_inputs = joined.inputs
    stack = merge_stacks(joined.stack, candidate.stack)
    variables = joined.variables.merge_against(
        candidate.variables,
        baseline.variables,
    )
    base = (
        _refine_branch_like(original, joined)
        if len(original.inputs) == len(joined.inputs)
        else joined
    )
    return replace(
        base.with_stack(stack)
        .with_variables(variables)
        .with_element_tags(candidate.element_tags)
        .with_data_element_uses(candidate.data_element_uses),
        inputs=merged_inputs,
    )


def _match_pattern_types(pattern: MatchPatternNode) -> Iterator[T.Type]:
    """Determine the types used for match pattern during static analysis."""
    if isinstance(pattern, TypePatternNode) and pattern.typ is not None:
        yield pattern.typ
    if isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            yield from _match_pattern_types(option)


def _match_arity(node: MatchNode) -> int | None:
    """Determine the required arity for match during static analysis."""
    arity: int | None = None
    for case in node.cases:
        case_arity = len(case.patterns)
        if arity is None:
            arity = case_arity
        elif case_arity != arity:
            return None
    return arity


def _match_subject_pattern_type(
    branch: AnalysisBranch,
    node: MatchNode,
    index: int,
) -> T.Type:
    """Determine the type of match subject pattern during static analysis."""
    inferred = tuple(
        typ
        for case in node.cases
        if index < len(case.patterns)
        if (typ := _pattern_subject_type(case.patterns[index])) is not None
    )
    if not inferred:
        return _anonymous_type_var(branch, index + 1)
    result = inferred[0]
    for typ in inferred[1:]:
        result = T.merge_types(result, typ)
    return result


def _pattern_subject_type(pattern: MatchPatternNode) -> T.Type | None:
    """Determine the type of pattern subject during static analysis."""
    if isinstance(pattern, TypePatternNode):
        return pattern.typ
    if isinstance(pattern, BindingPatternNode):
        return _pattern_subject_type(pattern.pattern)
    if isinstance(pattern, LiteralPatternNode):
        if isinstance(pattern.value, NumberLiteralNode):
            return T.Number
        if isinstance(pattern.value, StringLiteralNode):
            return T.String
        return None
    if isinstance(pattern, ListPatternNode):
        item_types = tuple(
            item_type
            for item in pattern.items
            if not isinstance(item, RestPatternNode)
            if (item_type := _pattern_subject_type(item)) is not None
        )
        if not item_types:
            return None
        item_result = item_types[0]
        for item_type in item_types[1:]:
            item_result = T.merge_types(item_result, item_type)
        return T.ExactList(item_result)
    if isinstance(pattern, OrPatternNode):
        option_types = tuple(
            typ
            for option in pattern.options
            if (typ := _pattern_subject_type(option)) is not None
        )
        if not option_types:
            return None
        result = option_types[0]
        for typ in option_types[1:]:
            result = T.merge_types(result, typ)
        return result
    return None


def _is_default_match_case(patterns: tuple[MatchPatternNode, ...]) -> bool:
    """Return whether the value is default match case."""
    return bool(patterns) and all(
        _is_default_match_pattern(pattern) for pattern in patterns
    )


def _is_default_match_pattern(pattern: MatchPatternNode) -> bool:
    """Return whether the value is default match pattern."""
    return isinstance(pattern, (WildcardPatternNode, RestPatternNode)) or (
        isinstance(pattern, TypePatternNode) and pattern.typ is None
    )


def _match_case_variables(
    variables: BranchVariables,
    patterns: tuple[MatchPatternNode, ...],
    subject_types: tuple[T.Type, ...] = (),
) -> BranchVariables:
    """Determine variable facts for match case during static analysis."""
    result = variables
    if subject_types:
        result = _add_match_binding(result, Symbol("top"), subject_types[0])
    for pattern in patterns:
        result = _add_match_pattern_variables(result, pattern)
    return result


def _match_subject_variables(
    branch: AnalysisBranch,
    arity: int,
) -> tuple[Symbol | None, ...]:
    """Determine variable facts for match subject during static analysis."""
    if arity <= 0 or len(branch.typed_body) < arity:
        return ()
    subject_nodes = branch.typed_body[-arity:]
    names: list[Symbol | None] = []
    for typed in subject_nodes:
        if isinstance(typed.node, GetVariableNode):
            names.append(typed.node.name)
        else:
            names.append(None)
    return tuple(reversed(names))


def _refine_match_subject_variables(
    variables: BranchVariables,
    subject_variables: tuple[Symbol | None, ...],
    patterns: tuple[MatchPatternNode, ...],
    subject_types: tuple[T.Type, ...],
    previous_patterns: tuple[tuple[MatchPatternNode, ...], ...],
    ctx: T.Context,
) -> BranchVariables:
    """Refine match subject variables during static analysis."""
    result = variables
    for index, name in enumerate(subject_variables):
        if name is None or index >= len(subject_types) or index >= len(patterns):
            continue
        narrowed = _match_case_subject_type(
            patterns[index],
            subject_types[index],
            tuple(
                previous[index]
                for previous in previous_patterns
                if index < len(previous)
            ),
            ctx,
        )
        if narrowed is None:
            continue
        result = _narrow_variable(result, name, narrowed)
    return result


def _narrow_variable(
    variables: BranchVariables,
    name: Symbol,
    typ: T.Type,
) -> BranchVariables:
    """Compute narrow variable during static analysis."""
    if _lookup(variables.block_locals, name) is not None:
        return BranchVariables(
            function_locals=variables.function_locals,
            parameters=variables.parameters,
            captures=variables.captures,
            block_locals=_set_item(variables.block_locals, name, typ),
            function_constants=variables.function_constants,
            block_constants=variables.block_constants,
        )
    if _lookup(variables.function_locals, name) is not None:
        return BranchVariables(
            function_locals=_set_item(variables.function_locals, name, typ),
            parameters=variables.parameters,
            captures=variables.captures,
            block_locals=variables.block_locals,
            function_constants=variables.function_constants,
            block_constants=variables.block_constants,
        )
    if _lookup(variables.parameters, name) is not None:
        return BranchVariables(
            function_locals=variables.function_locals,
            parameters=_set_item(variables.parameters, name, typ),
            captures=variables.captures,
            block_locals=variables.block_locals,
            function_constants=variables.function_constants,
            block_constants=variables.block_constants,
        )
    if _lookup(variables.captures, name) is not None:
        return BranchVariables(
            function_locals=variables.function_locals,
            parameters=variables.parameters,
            captures=_set_item(variables.captures, name, typ),
            block_locals=variables.block_locals,
            function_constants=variables.function_constants,
            block_constants=variables.block_constants,
        )
    return variables


def _match_case_subject_type(
    pattern: MatchPatternNode,
    subject_type: T.Type,
    previous_patterns: tuple[MatchPatternNode, ...],
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of match case subject during static analysis."""
    pattern_type = _pattern_subject_type(pattern)
    if pattern_type is not None:
        return pattern_type
    if not _is_default_match_pattern(pattern):
        return None
    excluded = tuple(
        typ
        for previous in previous_patterns
        if (typ := _pattern_subject_type(previous)) is not None
    )
    if not excluded:
        return subject_type
    return _subtract_match_types(subject_type, excluded, ctx)


def _subtract_match_types(
    subject_type: T.Type,
    excluded: tuple[T.Type, ...],
    ctx: T.Context,
) -> T.Type:
    """Determine the types used for subtract match during static analysis."""
    subject_type = T.normalize(subject_type)
    if not isinstance(subject_type, T.UnionType):
        return subject_type
    remaining = tuple(
        item
        for item in subject_type.items
        if not any(T.assignable(item, typ, ctx) for typ in excluded)
    )
    if not remaining:
        return T.NeverType()
    return T.U(*remaining)


def _add_match_pattern_variables(
    variables: BranchVariables,
    pattern: MatchPatternNode,
) -> BranchVariables:
    """Add match pattern variables during static analysis."""
    if isinstance(pattern, BindingPatternNode):
        return _add_match_binding(
            _add_match_pattern_variables(variables, pattern.pattern),
            pattern.name,
            _pattern_binding_type(pattern.pattern, pattern.name),
        )
    if isinstance(pattern, RestPatternNode) and pattern.name is not None:
        return _add_match_binding(
            variables,
            pattern.name,
            T.C(T.ListExactType, T.V(f"_matched_{pattern.name}")),
        )
    if isinstance(pattern, TypePatternNode):
        result = variables
        if pattern.name is not None:
            result = _add_match_binding(
                result,
                pattern.name,
                pattern.typ or T.V(f"_matched_{pattern.name}"),
            )
        for field in pattern.fields:
            result = _add_match_pattern_variables(result, field)
        return result
    if isinstance(pattern, ListPatternNode):
        result = variables
        for item in pattern.items:
            result = _add_match_pattern_variables(result, item)
        return result
    if isinstance(pattern, OrPatternNode):
        result = variables
        for option in pattern.options:
            result = _add_match_pattern_variables(result, option)
        return result
    return variables


def _add_match_binding(
    variables: BranchVariables,
    name: Symbol,
    typ: T.Type,
) -> BranchVariables:
    """Add match binding during static analysis."""
    write = variables.write(name, typ, block_local=True)
    return variables if write.variables is None else write.variables


def _pattern_binding_type(pattern: MatchPatternNode, name: Symbol) -> T.Type:
    """Determine the type of pattern binding during static analysis."""
    if isinstance(pattern, RestPatternNode):
        return T.C(T.ListExactType, T.V(f"_matched_{name}"))
    if isinstance(pattern, TypePatternNode) and pattern.typ is not None:
        return pattern.typ
    return T.V(f"_matched_{name}")


def _match_pattern_guards(
    patterns: tuple[MatchPatternNode, ...],
    subject_types: tuple[T.Type, ...],
) -> Iterator[tuple[tuple[ASTNode, ...], T.Type]]:
    """Compute match pattern guards during static analysis."""
    for pattern, subject_type in zip(patterns, subject_types, strict=True):
        yield from _pattern_guards(pattern, subject_type)


def _pattern_guards(
    pattern: MatchPatternNode,
    subject_type: T.Type,
) -> Iterator[tuple[tuple[ASTNode, ...], T.Type]]:
    """Compute pattern guards during static analysis."""
    if isinstance(pattern, GuardPatternNode):
        yield pattern.condition, subject_type
    elif isinstance(pattern, TypePatternNode) and pattern.guard:
        yield pattern.guard, pattern.typ or subject_type
    elif isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            yield from _pattern_guards(option, subject_type)
    elif isinstance(pattern, ListPatternNode):
        item_type = T.collection_item_type(subject_type) or T.V("_matched_item")
        for item in pattern.items:
            yield from _pattern_guards(item, item_type)
    elif isinstance(pattern, BindingPatternNode):
        yield from _pattern_guards(pattern.pattern, subject_type)


def _show_stack(stack: T.TypeStack) -> str:
    """Format stack during static analysis."""
    if not stack:
        return "[]"
    return "[" + ", ".join(T.show(item) for item in stack.items) + "]"


def _diagnostic_message(message: str, node: ASTNode | None) -> str:
    """Format the message for diagnostic during static analysis."""
    if node is None or node.location is None:
        return message
    location = node.location
    return f"{location.line}:{location.column}: {message}"


def _show_overloads(overloads: Iterable[T.Overload]) -> str:
    """Format overloads during static analysis."""
    rendered = tuple(
        T.show(T.Fn(overload.params, overload.returns)) for overload in overloads
    )
    if not rendered:
        return "none"
    return "; ".join(rendered)


def _show_applied_overloads(
    candidates: Iterable[CallCandidate],
) -> str:
    """Format applied overloads during static analysis."""
    rendered = tuple(
        T.show(
            T.Fn(
                candidate.applied.params,
                candidate.applied.actual_returns,
            )
        )
        for candidate in candidates
    )
    if not rendered:
        return "none"
    return "; ".join(rendered)


def _top_or_none(stack: T.TypeStack) -> T.Type:
    """Compute top or none during static analysis."""
    if stack:
        return stack[-1]
    return T.NoneType()


def _loop_break_result_type(break_types: tuple[T.Type, ...]) -> T.Type:
    """Determine the type of loop break result during static analysis."""
    if not break_types:
        return T.NoneType()
    if len(break_types) == 1:
        return T.optional(break_types[0])
    return T.optional(T.U(*break_types))


def _list_item_analysis(
    base: AnalysisBranch,
    output: AnalysisBranch,
) -> ListItemAnalysis | None:
    """Compute list item analysis during static analysis."""
    if output.break_type is not None or not output.stack:
        return None
    return ListItemAnalysis(
        branch=output,
        typ=output.stack[-1],
        consumed=_forked_stack_consumption(base.stack, output.stack.pop()),
        typed_body=output.typed_body[len(base.typed_body) :],
    )


def _literal_branch_results(
    branch: AnalysisBranch,
    item_options: tuple[tuple[ListItemAnalysis, ...], ...],
    node: ASTNode,
    literal_type: Callable[[tuple[ListItemAnalysis, ...]], T.Type],
) -> BranchSet:
    """Compute the results for literal branch during static analysis."""
    results: list[AnalysisBranch] = []
    for combo in _cartesian_product(item_options):
        inputs = _merge_inferred_inputs(branch.inputs, combo)
        if inputs is None:
            continue
        consumed = max((item.consumed for item in combo), default=0)
        typ = (
            T.Never()
            if any(_is_never(item.typ) for item in combo)
            else literal_type(combo)
        )
        variables = _merge_list_item_variables(branch.variables, combo)
        element_tags = frozenset(
            tag for item in combo for tag in item.branch.element_tags
        )
        data_element_uses = frozenset(
            use for item in combo for use in item.branch.data_element_uses
        )
        results.append(
            replace(
                branch,
                stack=_pop_stack(branch.stack, consumed).push(typ),
                inputs=inputs,
                variables=variables,
            ).emit(
                TypedLiteralNode(
                    node,
                    typ,
                    tuple(item.typed_body for item in combo),
                )
            ).with_element_tags(element_tags)
            .with_data_element_uses(data_element_uses)
        )
    return BranchSet.collect(results)


def _forked_stack_consumption(base: T.TypeStack, item_remainder: T.TypeStack) -> int:
    """Compute forked stack consumption during static analysis."""
    prefix = 0
    limit = min(len(base), len(item_remainder))
    while prefix < limit and T.same(base[prefix], item_remainder[prefix]):
        prefix += 1
    return len(base) - prefix


def _cartesian_product(
    options: tuple[tuple[ListItemAnalysis, ...], ...],
) -> Iterator[tuple[ListItemAnalysis, ...]]:
    """Compute cartesian product during static analysis."""
    if not options:
        yield ()
        return
    first, rest = options[0], options[1:]
    for item in first:
        for suffix in _cartesian_product(rest):
            yield (item, *suffix)


def _merge_inferred_inputs(
    base_inputs: tuple[T.Type, ...],
    items: tuple[ListItemAnalysis, ...],
) -> tuple[T.Type, ...] | None:
    """Merge inferred inputs during static analysis."""
    suffixes: list[tuple[T.Type, ...]] = []
    for item in items:
        if item.branch.inputs[: len(base_inputs)] != base_inputs:
            return None
        suffixes.append(item.branch.inputs[len(base_inputs) :])

    merged: list[T.Type] = []
    max_len = max((len(suffix) for suffix in suffixes), default=0)
    for index in range(max_len):
        candidates = tuple(suffix[index] for suffix in suffixes if index < len(suffix))
        merged.append(candidates[0] if len(candidates) == 1 else T.U(*candidates))
    return base_inputs + tuple(merged)


def _merge_branch_inputs(
    left: tuple[T.Type, ...],
    right: tuple[T.Type, ...],
) -> tuple[T.Type, ...] | None:
    """Merge branch inputs during static analysis."""
    if len(left) != len(right):
        return None
    return tuple(
        left_item
        if T.same(left_item, right_item)
        else T.merge_types(left_item, right_item)
        for left_item, right_item in zip(left, right, strict=True)
    )


def _merge_list_item_variables(
    before: BranchVariables,
    items: tuple[ListItemAnalysis, ...],
) -> BranchVariables:
    """Merge list item variables during static analysis."""
    merged = before
    for item in items:
        merged = merged.merge_against(item.branch.variables, before)
    return merged


def _pop_stack(stack: T.TypeStack, count: int) -> T.TypeStack:
    """Compute pop stack during static analysis."""
    if count == 0:
        return stack
    return stack.pop(count)


def _merge_loop_variables(
    before: BranchVariables,
    outputs: BranchSet,
    loop_locals: tuple[Symbol, ...],
) -> BranchVariables:
    """Merge loop variables during static analysis."""
    before_loop = _drop_named_block_locals(before, loop_locals)
    merged = before_loop
    for output in outputs:
        merged = merged.merge_against(
            _drop_named_block_locals(output.variables, loop_locals),
            before_loop,
        )
    return merged


def _drop_named_block_locals(
    variables: BranchVariables,
    names: tuple[Symbol, ...],
) -> BranchVariables:
    """Compute drop named block locals during static analysis."""
    blocked = set(names)
    return BranchVariables(
        function_locals=variables.function_locals,
        parameters=variables.parameters,
        captures=variables.captures,
        block_locals=tuple(
            (name, typ) for name, typ in variables.block_locals if name not in blocked
        ),
    )


def _loop_variable_output_type(
    name: Symbol,
    outputs: BranchSet,
) -> T.Type | None:
    """Determine the type of loop variable output during static analysis."""
    types = tuple(
        typ for output in outputs if (typ := output.variables.read(name)) is not None
    )
    if not types:
        return None
    merged = types[0]
    for typ in types[1:]:
        merged = T.merge_types(merged, typ)
    return merged


def _has_never_return(overload: T.Overload) -> bool:
    """Return whether the analyser helper has never return."""
    return any(isinstance(T.normalize(ret), T.NeverType) for ret in overload.returns)


def _is_never(t: T.Type) -> bool:
    """Return whether the value is never."""
    return isinstance(T.normalize(t), T.NeverType)


def _split_terminal_branches(branches: BranchSet) -> tuple[BranchSet, BranchSet]:
    """Partition non-returning ``Never`` paths from normally continuing paths."""
    terminal: list[AnalysisBranch] = []
    live: list[AnalysisBranch] = []
    for branch in branches:
        (terminal if branch.terminal else live).append(branch)
    return BranchSet.collect(terminal), BranchSet.collect(live)


def _param_type(param: FunctionParam, index: int) -> T.Type:
    """Determine the type of param during static analysis."""
    if param.typ is not None:
        return param.typ
    name = param.name.text if param.name is not None else f"_{index}"
    return T.V(name)


def _trait_requirement(node: TraitRequirementNode) -> T.TraitRequirement | None:
    """Compute trait requirement during static analysis."""
    params = tuple(
        _param_type(param, index) for index, param in enumerate(node.params or ())
    )
    returns = node.returns or ()
    return T.TraitRequirement(
        node.name,
        T.Overload(
            params=params,
            returns=returns,
            param_names=tuple(param.name for param in node.params or ()),
        ),
    )


def _trait_requirements(node: ObjectNode) -> tuple[T.TraitRequirement, ...]:
    """Compute trait requirements during static analysis."""
    return tuple(
        _genericize_requirement(requirement, node.generics)
        for item in node.requirements
        if (requirement := _trait_requirement(item)) is not None
    )


def _declared_nominal(name: Symbol, generics: tuple[Symbol, ...]) -> T.Type:
    """Compute declared nominal during static analysis."""
    return T.N(name, *(T.V(generic.text) for generic in generics))


def _types_overlap(source: T.Type, target: T.Type, ctx: T.Context) -> bool:
    """Return the Boolean result of types overlap during static analysis."""
    source = T.normalize(source)
    target = T.normalize(target)
    if T.assignable(source, target, ctx) or T.assignable(target, source, ctx):
        return True
    if isinstance(source, T.UnionType):
        return any(_types_overlap(item, target, ctx) for item in source.items)
    if isinstance(target, T.UnionType):
        return any(_types_overlap(source, item, ctx) for item in target.items)
    return False


def _copied_stack_shuffle_types(
    node: StackShuffleNode,
    args: tuple[T.Type, ...],
    labelled: dict[Symbol, T.Type],
    stack_arg_start: int,
) -> tuple[T.Type, ...]:
    """Determine the types used for copied stack shuffle during static analysis."""
    if node.mode == Symbol("copy"):
        return tuple(dict.fromkeys(labelled[label] for label in node.poststack))

    copied: list[T.Type] = []
    counts: dict[Symbol, int] = {}
    for label in node.poststack:
        counts[label] = counts.get(label, 0) + 1

    for index, (label, typ) in enumerate(zip(node.prestack, args, strict=True)):
        if label is None:
            if index < stack_arg_start:
                copied.append(typ)
            continue
        count = counts.get(label, 0)
        retains = count if index < stack_arg_start else max(count - 1, 0)
        if retains:
            copied.append(typ)
    return tuple(dict.fromkeys(copied))


def _copy_diagnostic(typ: T.Type, env: T.Environment) -> str | None:
    """Compute copy diagnostic during static analysis."""
    reason = _noncopyable_reason(typ, env)
    if reason is None:
        return None
    return f"cannot copy value of type {T.show(typ)}: {reason}"


def _noncopyable_reason(typ: T.Type, env: T.Environment) -> str | None:
    """Compute noncopyable reason during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _noncopyable_reason(typ.inner, env)
    if isinstance(typ, T.UnionType):
        for item in typ.items:
            reason = _noncopyable_reason(item, env)
            if reason is not None:
                return reason
        return None
    if isinstance(typ, T.IntersectionType):
        for item in typ.items:
            reason = _noncopyable_reason(item, env)
            if reason is not None:
                return reason
        return None
    if isinstance(typ, T.CollectionType):
        return _noncopyable_reason(typ.base, env)
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            reason = _noncopyable_reason(item, env)
            if reason is not None:
                return reason
        return None
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            reason = _noncopyable_reason(item.typ, env)
            if reason is not None:
                return reason
        return None
    if isinstance(typ, T.NominalType):
        return _nominal_copy_error(typ.name, env)
    return None


def _nominal_copy_error(name: Symbol, env: T.Environment) -> str | None:
    """Return the error description for nominal copy during static analysis."""
    overloads = env.overloads_for(Symbol(f"{name}::dup"))
    for overload in overloads:
        if overload.annotation_error is not None:
            return overload.annotation_error
    return None


def _number_literal_type(value: str) -> T.Type:
    """Determine the type of number literal during static analysis."""
    if "i" in value.lower():
        return T.Number
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return T.Number
    if parsed == parsed.to_integral_value():
        return T.Integer
    return T.Real


def _generic_constraints(
    generics: tuple[Symbol, ...],
    variances: tuple[Symbol | None, ...],
    constraints: tuple[T.Type | None, ...],
) -> tuple[T.GenericConstraint, ...]:
    """Compute generic constraints during static analysis."""
    if len(generics) != len(constraints):
        return ()
    if len(variances) != len(generics):
        variances = (None,) * len(generics)
    return tuple(
        T.GenericConstraint(
            generic.text,
            _genericize_type(bound, generics),
            _constraint_variance_from_marker(marker),
        )
        for generic, marker, bound in zip(generics, variances, constraints, strict=True)
        if bound is not None
    )


def _constraint_variance_from_marker(marker: Symbol | None) -> T.Variance:
    """Compute constraint variance from marker during static analysis."""
    if marker is None or marker.text == "any":
        return T.Variance.COVARIANT
    if marker.text == "above":
        return T.Variance.CONTRAVARIANT
    return _variance_from_marker(marker)


def _with_generic_constraints(
    overload: T.Overload,
    constraints: tuple[T.GenericConstraint, ...],
) -> T.Overload:
    """Compute with generic constraints during static analysis."""
    if not constraints:
        return overload
    return replace(
        overload,
        generic_constraints=overload.generic_constraints + constraints,
    )


def _has_multimethod_fallback(
    overload: T.Overload,
    candidates: tuple[T.Overload, ...],
    ctx: T.Context,
) -> bool:
    """Return whether the analyser helper has multimethod fallback."""
    return any(
        not candidate.is_multi
        and len(candidate.params) == len(overload.params)
        and _multimethod_params_covered_by(overload.params, candidate.params, ctx)
        and _same_returns(overload.returns, candidate.returns)
        for candidate in candidates
    )


def _mark_multidispatch(
    applied: T.AppliedOverload,
    overloads: tuple[T.Overload, ...],
    ctx: T.Context,
) -> T.AppliedOverload:
    """Compute mark multidispatch during static analysis."""
    if applied.overload.is_multi:
        if any(
            candidate is not applied.overload
            and candidate.is_multi
            and candidate.params == applied.overload.params
            and candidate.returns == applied.overload.returns
            for candidate in overloads
        ):
            return replace(applied, multidispatch=True)
        return applied
    if not _has_runtime_multimethod_candidate(applied.overload, overloads, ctx):
        return applied
    return replace(applied, multidispatch=True)


def _collapse_equivalent_call_winners(
    winners: tuple[CallCandidate, ...],
) -> tuple[CallCandidate, ...]:
    """Collapse inference paths that resolve to the same concrete invocation."""
    unique: list[CallCandidate] = []
    for candidate in winners:
        equivalent_index = next(
            (
                index
                for index, existing in enumerate(unique)
                if candidate.applied.params == existing.applied.params
                and candidate.applied.actual_returns == existing.applied.actual_returns
                and candidate.branch == existing.branch
                and candidate.call_arg_order == existing.call_arg_order
                and candidate.callable_overload_index
                == existing.callable_overload_index
                and candidate.overload_index == existing.overload_index
                and candidate.dispatch_priority == existing.dispatch_priority
            ),
            None,
        )
        if equivalent_index is None:
            unique.append(candidate)
            continue
        existing = unique[equivalent_index]
        if _contextual_modifier_quality(candidate) > _contextual_modifier_quality(
            existing
        ):
            unique[equivalent_index] = candidate
    return tuple(unique)


def _contextual_modifier_quality(candidate: CallCandidate) -> int:
    """Prefer modifier typings already specialized to their contextual type."""
    return sum(
        typing.typ == modifier.typ
        for modifier in candidate.modifiers
        for typing in modifier.typed_node.overloads
    )


def _collapse_equivalent_friendly_multidispatch_winners(
    winners: tuple[CallCandidate, ...],
) -> tuple[CallCandidate, ...]:
    """Compute collapse equivalent friendly multidispatch winners during static analysis."""
    if len(winners) <= 1:
        return winners
    first = winners[0]
    if first.dispatch_priority != 0 or not first.applied.multidispatch:
        return winners
    if not all(
        candidate.dispatch_priority == 0
        and candidate.applied.multidispatch
        and candidate.applied.params == first.applied.params
        and candidate.applied.actual_returns == first.applied.actual_returns
        and candidate.branch == first.branch
        for candidate in winners[1:]
    ):
        return winners
    return (
        min(
            winners,
            key=lambda candidate: (
                candidate.overload_index
                if candidate.overload_index is not None
                else -1
            ),
        ),
    )


def _has_runtime_multimethod_candidate(
    fallback: T.Overload,
    overloads: tuple[T.Overload, ...],
    ctx: T.Context,
) -> bool:
    """Return whether the analyser helper has runtime multimethod candidate."""
    return any(
        candidate.is_multi
        and candidate is not fallback
        and len(candidate.params) == len(fallback.params)
        and _multimethod_params_covered_by(candidate.params, fallback.params, ctx)
        and _same_returns(candidate.returns, fallback.returns)
        for candidate in overloads
    )


def _multimethod_params_covered_by(
    specific: tuple[T.Type, ...],
    fallback: tuple[T.Type, ...],
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of multimethod params covered by during static analysis."""
    return all(
        T.assignable(specific_param, fallback_param, ctx)
        for specific_param, fallback_param in zip(specific, fallback, strict=True)
    )


def _same_returns(
    left: tuple[T.Type, ...],
    right: tuple[T.Type, ...],
) -> bool:
    """Return the Boolean result of same returns during static analysis."""
    return len(left) == len(right) and all(
        T.same(left_item, right_item)
        for left_item, right_item in zip(left, right, strict=True)
    )


def _genericize_overload(
    overload: T.Overload,
    generics: tuple[Symbol, ...],
) -> T.Overload:
    """Generalize overload during static analysis."""
    if not generics:
        return overload
    return _transform_overload_types(
        overload,
        lambda typ: _genericize_type(typ, generics),
    )


def _genericize_function_node(
    function: FunctionNode,
    generics: tuple[Symbol, ...],
) -> FunctionNode:
    """Generalize function node during static analysis."""
    if not generics:
        return function
    params = None
    if function.params is not None:
        params = tuple(
            cast(FunctionParam, _genericize_ast_value(param, generics))
            for param in function.params
        )
    returns = None
    if function.returns is not None:
        returns = tuple(_genericize_type(ret, generics) for ret in function.returns)
    generic_constraints = tuple(
        None if bound is None else _genericize_type(bound, generics)
        for bound in function.generic_constraints
    )
    return FunctionNode(
        generics=function.generics,
        generic_variances=function.generic_variances,
        params=params,
        body=tuple(_genericize_ast_node(node, generics) for node in function.body),
        returns=returns,
        where_clause=function.where_clause,
        element_tags=frozenset(
            _genericize_element_tags(function.element_tags, generics)
        ),
        annotations=function.annotations,
        element_tags_explicit=function.element_tags_explicit,
        companion_tags_allowed=frozenset(
            _genericize_element_tags(function.companion_tags_allowed, generics)
        ),
        generic_constraints=generic_constraints,
        location=function.location,
    )


def _contextualize_function_empty_returns(function: FunctionNode) -> FunctionNode:
    """Infer empty list literals that are syntactically returned by a function."""
    if not function.returns:
        return function
    body = _contextualize_return_block(function.body, function.returns)
    return function if body == function.body else replace(function, body=body)


def _contextualize_return_block(
    body: tuple[ASTNode, ...],
    returns: tuple[T.Type, ...],
) -> tuple[ASTNode, ...]:
    """Compute contextualize return block during static analysis."""
    nodes = tuple(_contextualize_explicit_return(node, returns) for node in body)
    if not nodes:
        return nodes
    if len(returns) == 1:
        final = _contextualize_return_expression(nodes[-1], returns[0])
        return (*nodes[:-1], final)
    if len(nodes) >= len(returns):
        prefix = nodes[: -len(returns)]
        suffix = tuple(
            _contextualize_return_expression(node, expected)
            for node, expected in zip(
                nodes[-len(returns) :],
                returns,
                strict=True,
            )
        )
        return prefix + suffix
    return nodes


def _contextualize_explicit_return(
    node: ASTNode,
    returns: tuple[T.Type, ...],
) -> ASTNode:
    """Compute contextualize explicit return during static analysis."""
    if isinstance(node, ReturnNode) and len(node.values) == len(returns):
        return replace(
            node,
            values=tuple(
                _contextualize_return_expression(value, expected)
                for value, expected in zip(node.values, returns, strict=True)
            ),
        )
    return node


def _contextualize_return_expression(node: ASTNode, expected: T.Type) -> ASTNode:
    """Compute contextualize return expression during static analysis."""
    if isinstance(node, ListLiteralNode) and not node.items and node.typ is None:
        inferred = _empty_list_return_type(expected)
        return node if inferred is None else replace(node, typ=inferred)
    if isinstance(node, IfNode):
        return replace(
            node,
            then_branch=_contextualize_return_block(node.then_branch, (expected,)),
            else_branch=_contextualize_return_block(node.else_branch, (expected,)),
        )
    if isinstance(node, MatchNode):
        return replace(
            node,
            cases=tuple(
                replace(
                    case,
                    body=_contextualize_return_block(case.body, (expected,)),
                )
                for case in node.cases
            ),
        )
    if isinstance(node, TryNode):
        return replace(
            node,
            body=_contextualize_return_block(node.body, (expected,)),
            handlers=tuple(
                replace(
                    handler,
                    body=_contextualize_return_block(handler.body, (expected,)),
                )
                for handler in node.handlers
            ),
        )
    return node


def _empty_list_return_type(expected: T.Type) -> T.Type | None:
    """Determine the type of empty list return during static analysis."""
    expected = T.normalize(expected)
    if isinstance(expected, (T.TaggedType, T.ExactType)):
        return _empty_list_return_type(expected.inner)
    if isinstance(expected, (T.ListExactType, T.ListMinType, T.ListRuggedType)):
        return T.C(T.ListExactType, expected.base, expected.rank)
    if isinstance(expected, (T.ArrayExactType, T.ArrayMinType)):
        return T.C(T.ArrayExactType, expected.base, expected.rank)
    return None


def _genericize_ast_node(node: ASTNode, generics: tuple[Symbol, ...]) -> ASTNode:
    """Generalize AST node during static analysis."""
    if isinstance(node, FunctionNode) and node.generics:
        shadowed = {generic.text for generic in node.generics}
        generics = tuple(
            generic for generic in generics if generic.text not in shadowed
        )
        if not generics:
            return node
    updates: dict[str, object] = {}
    for item in fields(node):
        value = getattr(node, item.name)
        updated = _genericize_ast_value(value, generics)
        if updated is not value:
            updates[item.name] = updated
    return replace(node, **updates) if updates else node


def _genericize_ast_value(value: object, generics: tuple[Symbol, ...]) -> object:
    """Generalize AST value during static analysis."""
    if isinstance(value, T.Type):
        return _genericize_type(value, generics)
    if isinstance(value, FunctionParam):
        typ = None if value.typ is None else _genericize_type(value.typ, generics)
        default = tuple(
            cast(ASTNode, _genericize_ast_node(node, generics))
            for node in value.default
        )
        if typ is value.typ and default == value.default:
            return value
        return replace(value, typ=typ, default=default)
    if isinstance(value, CallArgument):
        default = tuple(
            cast(ASTNode, _genericize_ast_node(node, generics)) for node in value.value
        )
        if default == value.value:
            return value
        return replace(value, value=default)
    if isinstance(value, ASTNode):
        return _genericize_ast_node(value, generics)
    if isinstance(value, tuple):
        return tuple(_genericize_ast_value(item, generics) for item in value)
    return value


def _genericize_attribute(
    attribute: T.ObjectAttribute,
    generics: tuple[Symbol, ...],
) -> T.ObjectAttribute:
    """Generalize attribute during static analysis."""
    return T.ObjectAttribute(
        attribute.name,
        _genericize_type(attribute.typ, generics),
        attribute.access,
        attribute.has_default,
    )


def _genericize_requirement(
    requirement: T.TraitRequirement,
    generics: tuple[Symbol, ...],
) -> T.TraitRequirement:
    """Generalize requirement during static analysis."""
    return T.TraitRequirement(
        requirement.name,
        _transform_overload_types(
            requirement.overload,
            lambda typ: _genericize_type(typ, generics),
        ),
    )


def _transform_overload_types(
    overload: T.Overload,
    transform: Callable[[T.Type], T.Type],
    *,
    element_tags: frozenset[T.ElementTag] | None = None,
) -> T.Overload:
    """Determine the types used for transform overload during static analysis."""
    return replace(
        overload,
        params=tuple(transform(param) for param in overload.params),
        returns=tuple(transform(ret) for ret in overload.returns),
        element_tags=overload.element_tags if element_tags is None else element_tags,
    )


def _transform_type_children(
    typ: T.Type,
    transform: Callable[[T.Type], T.Type],
    *,
    element_tags: Callable[
        [frozenset[T.ElementTag]],
        frozenset[T.ElementTag],
    ] = lambda tags: tags,
) -> T.Type:
    """Compute transform type children during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return T.N(typ.name, *(transform(arg) for arg in typ.args))
    if isinstance(typ, T.UnionType):
        return T.U(*(transform(item) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(transform(item) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(transform(item) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(transform(item.typ), item.repeated)
                for item in typ.items
            )
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            transform(typ.base),
            *(T.Field(field.name, transform(field.typ)) for field in typ.fields),
        )
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), transform(typ.base), typ.rank)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return T.Fn(None, None, element_tags(typ.element_tags))
        return T.Fn(
            tuple(transform(param) for param in typ.params),
            tuple(transform(ret) for ret in typ.returns),
            element_tags(typ.element_tags),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(transform(typ.inner), *typ.tags)
    if isinstance(typ, T.ExactType):
        return T.Exact(transform(typ.inner))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(transform(typ.inner))
    return typ


def _genericize_type(typ: T.Type, generics: tuple[Symbol, ...]) -> T.Type:
    """Generalize type during static analysis."""
    names = {generic.text for generic in generics}
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        if not typ.args and typ.name.text in names:
            return T.V(typ.name.text)
    if isinstance(typ, T.AnonymousTraitType):
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    _genericize_overload(requirement.overload, generics),
                )
                for requirement in typ.requirements
            ),
        )
    return _transform_type_children(
        typ,
        lambda child: _genericize_type(child, generics),
        element_tags=lambda tags: _genericize_element_tags(tags, generics),
    )


def _anonymous_trait_overloads(*types: T.Type) -> tuple[tuple[Symbol, T.Overload], ...]:
    """Collect the overloads for anonymous trait during static analysis."""
    overloads: list[tuple[Symbol, T.Overload]] = []
    for typ in types:
        _collect_anonymous_trait_overloads(T.normalize(typ), overloads)
    return tuple(overloads)


def _anonymous_trait_subject_view(typ: T.Type) -> T.Type:
    """Build the view of anonymous trait subject during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.AnonymousTraitType):
        subject = _anonymous_trait_subject_name(typ)
        if subject is not None:
            return T.V(subject)
        return typ
    return _transform_type_children(typ, _anonymous_trait_subject_view)


def _anonymous_trait_subject_name(typ: T.AnonymousTraitType) -> str | None:
    """Return the canonical name for anonymous trait subject during static analysis."""
    if typ.generics:
        return typ.generics[0].text
    for requirement in typ.requirements:
        for item in requirement.overload.params + requirement.overload.returns:
            name = _first_type_var_name(item)
            if name is not None:
                return name
    return None


def _first_type_var_name(typ: T.Type) -> str | None:
    """Return the canonical name for first type var during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        return typ.name
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            name = _first_type_var_name(arg)
            if name is not None:
                return name
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            name = _first_type_var_name(item)
            if name is not None:
                return name
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            name = _first_type_var_name(item)
            if name is not None:
                return name
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            name = _first_type_var_name(item.typ)
            if name is not None:
                return name
    if isinstance(typ, T.RowType):
        name = _first_type_var_name(typ.base)
        if name is not None:
            return name
        for field in typ.fields:
            name = _first_type_var_name(field.typ)
            if name is not None:
                return name
    if isinstance(typ, T.CollectionType):
        return _first_type_var_name(typ.base)
    if isinstance(typ, T.FunctionType):
        if typ.params is not None:
            for item in typ.params:
                name = _first_type_var_name(item)
                if name is not None:
                    return name
        if typ.returns is not None:
            for item in typ.returns:
                name = _first_type_var_name(item)
                if name is not None:
                    return name
    if isinstance(typ, T.AnonymousTraitType):
        return _anonymous_trait_subject_name(typ)
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _first_type_var_name(typ.inner)
    return None


def _contains_anonymous_trait(typ: T.Type) -> bool:
    """Return whether the value contains anonymous trait."""
    typ = T.normalize(typ)
    if isinstance(typ, T.AnonymousTraitType):
        return True
    if isinstance(typ, T.NominalType):
        return any(_contains_anonymous_trait(arg) for arg in typ.args)
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        return any(_contains_anonymous_trait(item) for item in typ.items)
    if isinstance(typ, T.TupleType):
        return any(_contains_anonymous_trait(item) for item in typ.params)
    if isinstance(typ, T.VariadicTupleType):
        return any(_contains_anonymous_trait(item.typ) for item in typ.items)
    if isinstance(typ, T.RowType):
        return _contains_anonymous_trait(typ.base) or any(
            _contains_anonymous_trait(field.typ) for field in typ.fields
        )
    if isinstance(typ, T.CollectionType):
        return _contains_anonymous_trait(typ.base)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return False
        return any(_contains_anonymous_trait(item) for item in typ.params) or any(
            _contains_anonymous_trait(item) for item in typ.returns
        )
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _contains_anonymous_trait(typ.inner)
    return False


def _collect_anonymous_trait_overloads(
    typ: T.Type,
    overloads: list[tuple[Symbol, T.Overload]],
) -> None:
    """Collect anonymous trait overloads during static analysis."""
    if isinstance(typ, T.AnonymousTraitType):
        overloads.extend(
            (requirement.name, requirement.overload) for requirement in typ.requirements
        )
        for requirement in typ.requirements:
            for item in requirement.overload.params + requirement.overload.returns:
                _collect_anonymous_trait_overloads(T.normalize(item), overloads)
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            _collect_anonymous_trait_overloads(arg, overloads)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            _collect_anonymous_trait_overloads(item, overloads)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            _collect_anonymous_trait_overloads(item, overloads)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            _collect_anonymous_trait_overloads(item.typ, overloads)
        return
    if isinstance(typ, T.RowType):
        _collect_anonymous_trait_overloads(typ.base, overloads)
        for field in typ.fields:
            _collect_anonymous_trait_overloads(field.typ, overloads)
        return
    if isinstance(typ, T.CollectionType):
        _collect_anonymous_trait_overloads(typ.base, overloads)
        return
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return
        for item in typ.params + typ.returns:
            _collect_anonymous_trait_overloads(item, overloads)
        return
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        _collect_anonymous_trait_overloads(typ.inner, overloads)


def _genericize_element_tags(
    tags: frozenset[T.ElementTag],
    generics: tuple[Symbol, ...],
) -> tuple[T.ElementTag, ...]:
    """Generalize element tags during static analysis."""
    return tuple(
        T.ElementTag(
            tag.name,
            tuple(_genericize_type(arg, generics) for arg in tag.args),
            tag.absent,
        )
        for tag in tags
    )


def _declared_or_inferred_variance(
    generics: tuple[Symbol, ...],
    explicit: tuple[Symbol | None, ...],
    attributes: tuple[T.ObjectAttribute, ...],
    requirements: tuple[T.TraitRequirement, ...],
) -> tuple[T.Variance, ...]:
    """Compute declared or inferred variance during static analysis."""
    inferred = _infer_generic_variance(generics, attributes, requirements)
    if len(explicit) != len(generics):
        return inferred
    return tuple(
        _variance_from_marker(marker) if marker is not None else inferred[index]
        for index, marker in enumerate(explicit)
    )


def _variance_from_marker(marker: Symbol) -> T.Variance:
    """Compute variance from marker during static analysis."""
    if marker.text in {"any", "covariant"}:
        return T.Variance.COVARIANT
    if marker.text in {"above", "contravariant"}:
        return T.Variance.CONTRAVARIANT
    return T.Variance.INVARIANT


def _infer_generic_variance(
    generics: tuple[Symbol, ...],
    attributes: tuple[T.ObjectAttribute, ...],
    requirements: tuple[T.TraitRequirement, ...],
) -> tuple[T.Variance, ...]:
    """Infer generic variance during static analysis."""
    usage = {generic.text: [False, False] for generic in generics}
    for attribute in attributes:
        _record_variance_use(attribute.typ, +1, usage)
        if attribute.access.text == "public":
            _record_variance_use(attribute.typ, -1, usage)
    for requirement in requirements:
        for param in requirement.overload.params:
            _record_variance_use(param, -1, usage)
        for ret in requirement.overload.returns:
            _record_variance_use(ret, +1, usage)
    variances: list[T.Variance] = []
    for generic in generics:
        positive, negative = usage[generic.text]
        if positive and not negative:
            variances.append(T.Variance.COVARIANT)
        elif negative and not positive:
            variances.append(T.Variance.CONTRAVARIANT)
        else:
            variances.append(T.Variance.INVARIANT)
    return tuple(variances)


def _record_variance_use(
    typ: T.Type,
    polarity: int,
    usage: dict[str, list[bool]],
) -> None:
    """Record variance use during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.VarType):
        if typ.name in usage:
            usage[typ.name][0 if polarity > 0 else 1] = True
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            _record_variance_use(arg, polarity, usage)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            _record_variance_use(item, polarity, usage)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            _record_variance_use(item, polarity, usage)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            _record_variance_use(item.typ, polarity, usage)
        return
    if isinstance(typ, T.RowType):
        _record_variance_use(typ.base, polarity, usage)
        for field in typ.fields:
            _record_variance_use(field.typ, polarity, usage)
        return
    if isinstance(typ, T.CollectionType):
        _record_variance_use(typ.base, polarity, usage)
        return
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            for tag in typ.element_tags:
                for arg in tag.args:
                    _record_variance_use(arg, polarity, usage)
            return
        for param in typ.params:
            _record_variance_use(param, -polarity, usage)
        for ret in typ.returns:
            _record_variance_use(ret, polarity, usage)
        for tag in typ.element_tags:
            for arg in tag.args:
                _record_variance_use(arg, polarity, usage)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for param in requirement.overload.params:
                _record_variance_use(param, -polarity, usage)
            for ret in requirement.overload.returns:
                _record_variance_use(ret, polarity, usage)
        return
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        _record_variance_use(typ.inner, polarity, usage)


def _anonymous_type_var(branch: AnalysisBranch, offset: int) -> T.Type:
    """Compute anonymous type var during static analysis."""
    taken = _anonymous_type_indices(
        *branch.stack.items,
        *branch.inputs,
        *branch.cycle_params,
        *(typ for _, typ in branch.variables.visible_items()),
    )
    start = max(taken, default=0)
    return T.V(f"@{start + offset}")


def _anonymous_type_indices(*types: T.Type) -> set[int]:
    """Compute anonymous type indices during static analysis."""
    indices: set[int] = set()
    for typ in types:
        _collect_anonymous_type_indices(T.normalize(typ), indices)
    return indices


def _collect_anonymous_type_indices(typ: T.Type, indices: set[int]) -> None:
    """Collect anonymous type indices during static analysis."""
    if isinstance(typ, T.VarType) and typ.name.startswith("@"):
        suffix = typ.name[1:]
        if suffix.isdecimal():
            indices.add(int(suffix))
        return
    if isinstance(typ, T.NominalType):
        for arg in typ.args:
            _collect_anonymous_type_indices(arg, indices)
        return
    if isinstance(typ, (T.UnionType, T.IntersectionType)):
        for item in typ.items:
            _collect_anonymous_type_indices(item, indices)
        return
    if isinstance(typ, T.TupleType):
        for item in typ.params:
            _collect_anonymous_type_indices(item, indices)
        return
    if isinstance(typ, T.VariadicTupleType):
        for item in typ.items:
            _collect_anonymous_type_indices(item.typ, indices)
        return
    if isinstance(typ, T.RowType):
        _collect_anonymous_type_indices(typ.base, indices)
        for row_field in typ.fields:
            _collect_anonymous_type_indices(row_field.typ, indices)
        return
    if isinstance(typ, T.CollectionType):
        _collect_anonymous_type_indices(typ.base, indices)
        return
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return
        for item in typ.params + typ.returns:
            _collect_anonymous_type_indices(item, indices)
        return
    if isinstance(typ, T.AnonymousTraitType):
        for requirement in typ.requirements:
            for item in requirement.overload.params + requirement.overload.returns:
                _collect_anonymous_type_indices(item, indices)
        return
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        _collect_anonymous_type_indices(typ.inner, indices)


def _row_field_type(row: T.RowType, name: Symbol) -> T.Type | None:
    """Determine the type of row field during static analysis."""
    for row_field in row.fields:
        if row_field.name == name:
            return row_field.typ
    return None


def _refine_stack(stack: T.TypeStack, old: T.Type, new: T.Type) -> T.TypeStack:
    """Refine stack during static analysis."""
    return T.TypeStack(tuple(_refine_type(item, old, new) for item in stack.items))


def _refine_items(
    items: tuple[tuple[Symbol, T.Type], ...],
    old: T.Type,
    new: T.Type,
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Refine items during static analysis."""
    return tuple((name, _refine_type(typ, old, new)) for name, typ in items)


def _refine_typed_body(
    typed_body: tuple[TypedNode, ...],
    old: T.Type,
    new: T.Type,
) -> tuple[TypedNode, ...]:
    """Refine typed body during static analysis."""
    return tuple(_refine_typed_node(node, old, new) for node in typed_body)


def _refine_typed_node(typed_node: TypedNode, old: T.Type, new: T.Type) -> TypedNode:
    """Refine typed node during static analysis."""
    typ = None if typed_node.typ is None else _refine_type(typed_node.typ, old, new)
    if isinstance(typed_node, TypedImportedFunctionNode):
        return TypedImportedFunctionNode(
            typed_node.node,
            typ,
            tuple(
                FunctionOverloadTyping(
                    _refine_type(overload.typ, old, new),
                    _refine_typed_body(overload.body, old, new),
                    overload.overload,
                )
                for overload in typed_node.overloads
            ),
            typed_node.dispatch_plan,
            typed_node.runtime_name,
        )
    if isinstance(typed_node, TypedFunctionNode):
        return TypedFunctionNode(
            typed_node.node,
            typ,
            tuple(
                FunctionOverloadTyping(
                    _refine_type(overload.typ, old, new),
                    _refine_typed_body(overload.body, old, new),
                    overload.overload,
                )
                for overload in typed_node.overloads
            ),
            typed_node.dispatch_plan,
        )
    if isinstance(typed_node, TypedLiteralNode):
        return TypedLiteralNode(
            typed_node.node,
            typ,
            tuple(
                _refine_typed_body(item, old, new) for item in typed_node.items
            ),
        )
    if isinstance(typed_node, TypedTagApplicationNode):
        return TypedTagApplicationNode(
            typed_node.node,
            typ,
            typed_node.validator,
            typed_node.validator_index,
            typed_node.added_tags,
            typed_node.removed_tags,
            typed_node.validator_runtime_name,
        )
    if isinstance(typed_node, TypedElementNode):
        return TypedElementNode(
            typed_node.node,
            typ,
            typed_node.overload,
            typed_node.overload_index,
            typed_node.modifier_args,
            typed_node.call_arg_order,
            typed_node.call_overload_index,
            _refine_typed_extension(typed_node.extension, old, new),
            typed_node.runtime_name,
        )
    if isinstance(typed_node, TypedCallNode):
        return TypedCallNode(
            typed_node.node,
            typ,
            typed_node.overload,
        )
    if isinstance(typed_node, TypedIfNode):
        return TypedIfNode(
            typed_node.node,
            typ,
            _refine_typed_body(typed_node.condition, old, new),
            _refine_typed_body(typed_node.then_branch, old, new),
            _refine_typed_body(typed_node.else_branch, old, new),
        )
    if isinstance(typed_node, TypedUnfoldNode):
        function = typed_node.function
        refined_function = (
            None
            if function is None
            else cast(
                TypedFunctionNode,
                _refine_typed_node(function, old, new),
            )
        )
        return TypedUnfoldNode(
            typed_node.node,
            typ,
            typed_node.state_arity,
            refined_function,
        )
    if isinstance(typed_node, TypedAtNode):
        function = typed_node.function
        refined_function = (
            None
            if function is None
            else cast(
                TypedFunctionNode,
                _refine_typed_node(function, old, new),
            )
        )
        return TypedAtNode(
            typed_node.node,
            typ,
            refined_function,
            typed_node.overload,
            typed_node.function_overload_index,
        )
    if isinstance(typed_node, TypedImportedObjectNode):
        return TypedImportedObjectNode(
            typed_node.node,
            typ,
            typed_node.runtime_name,
        )
    return TypedNode(typed_node.node, typ)


def _refine_typed_extension(
    extension: TypedElementExtension | None,
    old: T.Type,
    new: T.Type,
) -> TypedElementExtension | None:
    """Refine typed extension during static analysis."""
    if extension is None:
        return None

    def refine_function(function: TypedFunctionNode | None) -> TypedFunctionNode | None:
        """Refine function during static analysis."""
        if function is None:
            return None
        refined = _refine_typed_node(function, old, new)
        assert isinstance(refined, TypedFunctionNode)
        return refined

    return TypedElementExtension(
        default=refine_function(extension.default),
        rules=tuple(
            TypedExtensionPatternRule(
                rule.pattern,
                cast(TypedFunctionNode, _refine_typed_node(rule.function, old, new)),
            )
            for rule in extension.rules
        ),
        selector=refine_function(extension.selector),
    )


def _refine_type(typ: T.Type, old: T.Type, new: T.Type) -> T.Type:
    """Refine type during static analysis."""
    typ = T.normalize(typ)
    new = _erase_absent_tag_requirements(new)
    if T.same(typ, old):
        return new
    if isinstance(typ, T.AnonymousTraitType):
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    _transform_overload_types(
                        requirement.overload,
                        lambda item: _refine_type(item, old, new),
                    ),
                )
                for requirement in typ.requirements
            ),
        )
    if isinstance(typ, T.AtomicType):
        inner = _refine_type(typ.inner, old, new)
        if not isinstance(inner, T.VarType):
            return _atomic_base_type(inner)
        return T.Atomic(inner)
    return _transform_type_children(typ, lambda child: _refine_type(child, old, new))


def _atomic_base_type(typ: T.Type) -> T.Type:
    """Determine the type of atomic base during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType):
        return _atomic_base_type(typ.inner)
    if isinstance(typ, T.CollectionType):
        return _atomic_base_type(typ.base)
    return typ


def _erase_absent_tag_requirements(typ: T.Type) -> T.Type:
    """Compute erase absent tag requirements during static analysis."""
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType) and all(tag.absent for tag in typ.tags):
        return typ.inner
    return typ


def _stack_assignable(
    actual: T.TypeStack,
    expected: T.TypeStack,
    ctx: T.Context,
) -> bool:
    """Return the Boolean result of stack assignable during static analysis."""
    if len(actual) < len(expected):
        return False
    actual_returns = actual.items[-len(expected) :] if expected else ()
    return all(
        T.assignable(a, e, ctx) for a, e in zip(actual_returns, expected, strict=True)
    )


def _stack_returns(
    actual: T.TypeStack,
    expected: T.TypeStack,
) -> tuple[T.Type, ...]:
    """Determine the return types for stack during static analysis."""
    return actual.items[-len(expected) :] if expected else ()


def _return_value_shape(typ: T.Type) -> T.Type:
    """Return the underlying value shape checked inside a function body.

    Top-level return tags are guarantees made by the function signature. The
    compiler applies those tags to returned runtime values, so body checking
    must validate the underlying value rather than require the body to apply
    the same tags explicitly. Nested tags remain part of the value shape.
    """
    normalized = T.normalize(typ)
    if isinstance(normalized, T.TaggedType):
        return normalized.inner
    return normalized


def _lookup(
    items: tuple[tuple[Symbol, T.Type], ...],
    name: Symbol,
) -> T.Type | None:
    """Compute lookup during static analysis."""
    for key, typ in items:
        if key == name:
            return typ
    return None


def _assignment_error(
    name: Symbol,
    source: T.Type,
    target: T.Type,
    ctx: T.Context,
) -> str | None:
    """Return the error description for assignment during static analysis."""
    if _assignment_stored_type(target, source, ctx) is not None:
        return None
    return (
        f"cannot assign {T.show(source)} to variable '{name}' of type {T.show(target)}"
    )


def _assignment_stored_type(
    existing: T.Type,
    source: T.Type,
    ctx: T.Context,
) -> T.Type | None:
    """Determine the type of assignment stored during static analysis."""
    if T.assignable(source, existing, ctx):
        return existing
    if T.assignable(existing, source, ctx):
        return source
    return None


def _mustcall_methods(annotations: tuple[ASTNode, ...]) -> tuple[str, ...]:
    """Compute mustcall methods during static analysis."""
    for annotation in annotations:
        if not isinstance(annotation, AnnotationNode):
            continue
        if annotation.name.text != "mustcall":
            continue
        kwargs = dict(annotation.kwargs)
        for key in (Symbol("all"), Symbol("any")):
            value = kwargs.get(key)
            if not isinstance(value, ListLiteralNode):
                continue
            methods: list[str] = []
            for item in value.items:
                if len(item) != 1 or not isinstance(item[0], StringLiteralNode):
                    return ()
                methods.append(item[0].value)
            return tuple(methods)
    return ()


def _child_symbol(parent: Symbol, child: Symbol) -> Symbol:
    """Compute child symbol during static analysis."""
    return Symbol(child.text, (*parent.namespace, parent.text, *child.namespace))


def _set_item(
    items: tuple[tuple[Symbol, T.Type], ...],
    name: Symbol,
    typ: T.Type,
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Compute set item during static analysis."""
    result = {key: value for key, value in items}
    result[name] = typ
    return _sorted_items(result.items())


def _set_symbol_flag(
    items: tuple[Symbol, ...],
    name: Symbol,
    enabled: bool,
) -> tuple[Symbol, ...]:
    """Compute set symbol flag during static analysis."""
    result = set(items)
    if enabled:
        result.add(name)
    else:
        result.discard(name)
    return tuple(sorted(result))


def _sorted_items(
    items: Iterable[tuple[Symbol, T.Type]],
) -> tuple[tuple[Symbol, T.Type], ...]:
    """Collect the items for sorted during static analysis."""
    return tuple(sorted(items, key=lambda item: item[0]))


