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

from . import _analyser_calls as _calls
from . import _analyser_functions as _functions
from . import _analyser_patterns as _patterns
from . import _analyser_utils as _utils


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

    def visible_names(self) -> tuple[Symbol, ...]:
        """Return variable names readable from this branch frame."""
        names = {
            name
            for entries in (
                self.function_locals,
                self.parameters,
                self.captures,
                self.block_locals,
            )
            for name, _ in entries
        }
        return tuple(sorted(names, key=str))

    @classmethod
    def from_parameters(
        cls,
        params: tuple[tuple[Symbol, T.Type], ...],
        *,
        captures: BranchVariables | None = None,
    ) -> BranchVariables:
        """Create a function variable frame from named parameters."""
        captured = () if captures is None else captures.visible_items()
        return cls(
            parameters=_utils._sorted_items(params),
            captures=_utils._sorted_items(captured),
        )

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
        return _utils._sorted_items(result.items())

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
            typ = _utils._lookup(scope, name)
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
        existing_block_local = _utils._lookup(self.block_locals, name)
        if existing_block_local is not None:
            if name in self.block_constants:
                return VariableWrite(None, f"cannot assign to constant '{name}'")
            stored_type = _utils._assignment_stored_type(existing_block_local, typ, ctx)
            diagnostic = _utils._assignment_error(name, typ, existing_block_local, ctx)
            if diagnostic is not None:
                return VariableWrite(None, diagnostic)
            if T.same(stored_type, existing_block_local):
                return VariableWrite(self)
            return VariableWrite(
                BranchVariables(
                    function_locals=self.function_locals,
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=_utils._set_item(self.block_locals, name, stored_type),
                    function_constants=self.function_constants,
                    block_constants=self.block_constants,
                ),
            )
        if _utils._lookup(self.parameters, name) is not None:
            return VariableWrite(
                None,
                f"cannot assign to read-only parameter '{name}'",
            )
        existing_function_local = _utils._lookup(self.function_locals, name)
        if existing_function_local is not None:
            if name in self.function_constants:
                return VariableWrite(None, f"cannot assign to constant '{name}'")
            stored_type = _utils._assignment_stored_type(
                existing_function_local,
                typ,
                ctx,
            )
            diagnostic = _utils._assignment_error(
                name,
                typ,
                existing_function_local,
                ctx,
            )
            if diagnostic is not None:
                return VariableWrite(None, diagnostic)
            return VariableWrite(
                BranchVariables(
                    function_locals=_utils._set_item(
                        self.function_locals,
                        name,
                        stored_type,
                    ),
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=self.block_locals,
                    function_constants=self.function_constants,
                    block_constants=self.block_constants,
                ),
            )
        if _utils._lookup(self.captures, name) is not None:
            return VariableWrite(
                BranchVariables(
                    function_locals=_utils._set_item(self.function_locals, name, typ),
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=self.block_locals,
                    function_constants=_utils._set_symbol_flag(
                        self.function_constants, name, constant
                    ),
                    block_constants=self.block_constants,
                ),
            )
        if block_local or _utils._lookup(self.block_locals, name) is not None:
            return VariableWrite(
                BranchVariables(
                    function_locals=self.function_locals,
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=_utils._set_item(self.block_locals, name, typ),
                    function_constants=self.function_constants,
                    block_constants=_utils._set_symbol_flag(
                        self.block_constants, name, constant
                    ),
                ),
            )
        return VariableWrite(
            BranchVariables(
                function_locals=_utils._set_item(self.function_locals, name, typ),
                parameters=self.parameters,
                captures=self.captures,
                block_locals=self.block_locals,
                function_constants=_utils._set_symbol_flag(
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
            block_locals=_utils._set_item(self.block_locals, name, typ),
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
            function_locals=_utils._refine_items(self.function_locals, old, new),
            parameters=_utils._refine_items(self.parameters, old, new),
            captures=_utils._refine_items(self.captures, old, new),
            block_locals=_utils._refine_items(self.block_locals, old, new),
            function_constants=self.function_constants,
            block_constants=self.block_constants,
        )

    def refine_input_requirement(
        self, old: T.Type, new: T.Type
    ) -> BranchVariables:
        """Refine parameter-backed variables with a call requirement."""
        return BranchVariables(
            function_locals=_utils._refine_input_requirement_items(
                self.function_locals, old, new
            ),
            parameters=_utils._refine_input_requirement_items(
                self.parameters, old, new
            ),
            captures=self.captures,
            block_locals=self.block_locals,
            function_constants=self.function_constants,
            block_constants=self.block_constants,
        )

    def merge_against(
        self,
        other: BranchVariables,
        before: BranchVariables,
        ctx: T.Context | None = None,
    ) -> BranchVariables:
        """Merge two branch outputs, preserving only variables visible before."""
        locals_by_name: dict[Symbol, T.Type] = {}
        before_names = {name for name, _ in before.function_locals}
        for name in before_names:
            left = _utils._lookup(self.function_locals, name) or _utils._lookup(
                before.function_locals, name
            )
            right = _utils._lookup(other.function_locals, name) or _utils._lookup(
                before.function_locals, name
            )
            if left is not None and right is not None:
                locals_by_name[name] = (
                    left if left == right else T.merge_types(left, right, ctx)
                )
        block_locals_by_name: dict[Symbol, T.Type] = {}
        before_block_names = {name for name, _ in before.block_locals}
        for name in before_block_names:
            left = _utils._lookup(self.block_locals, name) or _utils._lookup(
                before.block_locals, name
            )
            right = _utils._lookup(other.block_locals, name) or _utils._lookup(
                before.block_locals, name
            )
            if left is not None and right is not None:
                block_locals_by_name[name] = (
                    left if left == right else T.merge_types(left, right, ctx)
                )
        return BranchVariables(
            function_locals=_utils._sorted_items(locals_by_name.items()),
            parameters=before.parameters,
            captures=before.captures,
            block_locals=_utils._sorted_items(block_locals_by_name.items()),
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
    input_names: tuple[Symbol | None, ...] = ()
    variables: BranchVariables = field(default_factory=BranchVariables)
    typed_body: tuple[TypedNode, ...] = ()
    element_tags: frozenset[T.ElementTag] = field(default_factory=frozenset)
    data_element_uses: frozenset[tuple[Symbol, Symbol]] = field(
        default_factory=frozenset
    )
    input_mode: InputMode = InputMode.TOP_LEVEL
    cycle_params: tuple[T.Type, ...] = ()
    atomic_type_vars: frozenset[str] = field(default_factory=frozenset)
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
        return any(_utils._is_never(typ) for typ in self.stack)

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
                for tag in _functions._present_data_tags(param)
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
            stack=_utils._refine_stack(self.stack, old, new),
            inputs=tuple(_utils._refine_type(item, old, new) for item in self.inputs),
            variables=self.variables.refine_type(old, new),
            typed_body=_utils._refine_typed_body(self.typed_body, old, new),
            element_tags=frozenset(
                T.ElementTag(
                    tag.name,
                    tuple(_utils._refine_type(arg, old, new) for arg in tag.args),
                    tag.absent,
                )
                for tag in self.element_tags
            ),
            cycle_params=tuple(
                _utils._refine_type(item, old, new) for item in self.cycle_params
            ),
        )

    def refine_input_requirement(
        self, old: T.Type, new: T.Type
    ) -> AnalysisBranch:
        """Propagate a call constraint into enclosing function input facts."""
        return replace(
            self,
            inputs=tuple(
                _utils._refine_input_requirement(item, old, new)
                for item in self.inputs
            ),
            variables=self.variables.refine_input_requirement(old, new),
            cycle_params=tuple(
                _utils._refine_input_requirement(item, old, new)
                for item in self.cycle_params
            ),
        )

    def refine_named_input_requirement(
        self, name: Symbol, old: T.Type, new: T.Type
    ) -> AnalysisBranch:
        """Refine one named explicit input without affecting equal-typed peers."""
        inputs = tuple(
            (
                _utils._refine_input_requirement(item, old, new)
                if index < len(self.input_names) and self.input_names[index] == name
                else item
            )
            for index, item in enumerate(self.inputs)
        )
        parameters = tuple(
            (
                param_name,
                _utils._refine_input_requirement(typ, old, new)
                if param_name == name
                else typ,
            )
            for param_name, typ in self.variables.parameters
        )
        return replace(
            self,
            inputs=inputs,
            variables=replace(self.variables, parameters=parameters),
            cycle_params=tuple(
                _utils._refine_input_requirement(item, old, new)
                if index < len(self.input_names) and self.input_names[index] == name
                else item
                for index, item in enumerate(self.cycle_params)
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
        """Analyse a `DefineNode` node and return the surviving branches."""
        name = node.name
        function_node = node.function
        if not self._validate_annotations(node.annotations, "define", node):
            return BranchSet((branch.emit(TypedNode(node, None)),))
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            function_node,
            node.annotations,
        )
        if not function_node.overloads:
            function_node = _functions._genericize_function_node(
                function_node,
                node.generics,
            )
        function_node = replace(
            function_node,
            generics=node.generics,
            generic_variances=node.generic_variances,
            generic_constraints=node.generic_constraints,
        )
        self._validate_function_element_tags(function_node, node)
        declared_overload = (
            _functions._fully_typed_overload(function_node)
            if not node.generics
            and _functions._body_references_element(function_node.body, name)
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
        result = self._analyse_overloaded_function_literal(branch, function_node, node)
        if result is None:
            return BranchSet((branch.emit(TypedNode(node, None)),))
        function, typed_branch = result
        generic_constraints = _functions._generic_constraints(
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
                static_result = _calls._static_validator_result(typing.body)
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
        if not _functions._validate_define_niladic_name(name, overload):
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

        if name.text.startswith("#") and not _calls._validator_overload_ok(
            overload,
            self.env.context,
        ):
            self._diagnose(
                f"tag validator '{name}' must return #boolean Number",
                node,
            )
            return None

        if not self._validate_data_tags((overload.params, overload.returns), node):
            return None
        overload = _functions._with_generic_constraints(overload, generic_constraints)
        overload = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
            overload,
            node.annotations,
        )

        if not node.is_multi:
            return overload

        overload = replace(overload, is_multi=True)
        if _functions._has_multimethod_fallback(
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
                    if _functions._element_tag_absence_conflicts(
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
            for tags in _functions._function_type_element_tag_sets(typ):
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
                    if _functions._element_tag_absence_conflicts(
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
                _functions._element_tag_covers(declared, tag, self.env.context)
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
            for tag in _functions._present_data_tags(typ)
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
        *,
        allow_variants: bool = False,
        require_declared: bool = False,
    ) -> bool:
        """Validate data tags during static analysis and report success."""
        for group in groups:
            for typ in group:
                for tag in _all_data_tags(typ):
                    definition = self.env.lookup_tag(tag.name)
                    if definition is None:
                        if require_declared:
                            self._diagnose(
                                f"unknown data tag '#{tag.name}'",
                                origin,
                            )
                            return False
                        continue
                    if (
                        definition.kind is T.TagKind.VARIANT
                        and not allow_variants
                    ):
                        self._diagnose(
                            f"variant data tag '#{tag.name}' is runtime-only and "
                            "cannot appear in a compile-time signature",
                            origin,
                        )
                        return False
                for nested in _nested_types(typ):
                    if not isinstance(nested, T.TaggedType):
                        continue
                    rank = _calls._type_rank(T.normalize(nested.inner))
                    invalid_depths = tuple(
                        tag for tag in nested.tags if tag.depth > rank
                    )
                    if invalid_depths:
                        tag = sorted(invalid_depths)[0]
                        self._diagnose(
                            f"data tag '#{tag.name}{'+' * tag.depth}' has depth "
                            f"{tag.depth}, but {T.show(nested.inner)} has rank {rank}",
                            origin,
                        )
                        return False
                    positive = {
                        (tag.name, tag.depth)
                        for tag in nested.tags
                        if not tag.absent
                    }
                    negative = {
                        (tag.name, tag.depth)
                        for tag in nested.tags
                        if tag.absent
                    }
                    conflict = positive.intersection(negative)
                    if conflict:
                        name, depth = sorted(conflict)[0]
                        suffix = "+" * depth
                        self._diagnose(
                            f"data tag '#{name}{suffix}' cannot be both present "
                            "and absent",
                            origin,
                        )
                        return False
                conflict = _calls._disjoint_data_tags(typ, self.env.context)
                if conflict is None:
                    continue
                left, right = conflict
                self._diagnose(
                    f"data tags '#{left.text}' and '#{right.text}' cannot both apply",
                    origin,
                )
                return False
        return True

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
        """Return whether an object's lifecycle annotations are valid."""
        ok = True
        mustcall = _utils._mustcall_methods(node.annotations)
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
        self_type = _utils._declared_nominal(node.name, node.generics)

        # Trait requirements are abstract, so expose receiver-specialized versions
        # only while checking default bodies. Keeping them out of the persistent
        # overload table ensures concrete implementations retain stable runtime
        # overload indexes.
        snapshots: dict[Symbol, tuple[list[T.Overload] | None, set[int] | None]] = {}
        for requirement in trait.requirements if trait is not None else ():
            name = requirement.name
            if name not in snapshots:
                snapshots[name] = (
                    list(self.env.overloads[name])
                    if name in self.env.overloads
                    else None,
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
        generic_constraints = _functions._generic_constraints(
            node.generics,
            node.generic_variances,
            node.generic_constraints,
        )
        requirements = _utils._trait_requirements(node)
        members: list[Symbol] = []
        for member in node.variants:
            member_name = _utils._child_symbol(node.name, member.name)
            members.append(member_name)
            object_attributes = self._object_attributes(member.fields, node.generics)
            if object_attributes is None:
                return BranchSet((branch.emit(TypedNode(node, None)),))
            variant_type = _utils._declared_nominal(node.name, node.generics)
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
            generic_variance=_functions._declared_or_inferred_variance(
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
            TypedNode(node, _utils._declared_nominal(node.name, node.generics))
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

        self_type = _utils._declared_nominal(owner, variant.generics)
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
        function_node = _functions._genericize_function_node(
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
            *_functions._generic_constraints(
                variant.generics,
                variant.generic_variances,
                variant.generic_constraints,
            ),
            *_functions._generic_constraints(
                definition.generics,
                definition.generic_variances,
                definition.generic_constraints,
            ),
        )
        concrete = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
            _functions._with_generic_constraints(
                typing.overload,
                generic_constraints,
            ),
            definition.annotations,
        )
        variant_type = _utils._declared_nominal(variant.name, variant.generics)
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
                _utils._child_symbol(node.name, member.name),
                value_type,
                bool(member.value),
            )
            for member in node.enum_members
        )
        self.env.define_enum(node.name, members, value_type=value_type)
        return BranchSet(
            (
                branch.emit(
                    TypedNode(node, _utils._declared_nominal(node.name, node.generics))
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
            _functions._genericize_attribute(attribute, generics)
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
            _functions._generic_constraints(
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
            generic_variance=_functions._declared_or_inferred_variance(
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
                result_type=result_type
                or _utils._declared_nominal(name, node.generics),
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
        requirements = list(_utils._trait_requirements(node))
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
            generic_variance=_functions._declared_or_inferred_variance(
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
        self_type = _utils._declared_nominal(
            owner,
            owner_definition.generics if owner_definition is not None else (),
        )
        if definition.function.returns is not None and (
            len(definition.function.returns) != 1
            or not T.same(
                _functions._genericize_type(
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
        function_node = _functions._genericize_function_node(
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
            *_functions._generic_constraints(
                owner_node.generics,
                owner_node.generic_variances,
                owner_node.generic_constraints,
            ),
            *_functions._generic_constraints(
                definition.generics,
                definition.generic_variances,
                definition.generic_constraints,
            ),
        )
        for typing in function.overloads:
            if not isinstance(typing.overload, T.Overload):
                continue
            overload = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
                _functions._with_generic_constraints(
                    typing.overload,
                    generic_constraints,
                ),
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
        self_type = _utils._declared_nominal(
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
        function_node = _functions._genericize_function_node(
            function_node,
            definition.generics,
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
        generic_constraints = _functions._generic_constraints(
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
                        _functions._with_generic_constraints(
                    typing.overload,
                    generic_constraints,
                ),
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
        for selected in _functions._callable_overloads(typed_node.typ):
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

    def element_argument_sources(
        self,
        node: ElementNode,
        branch: AnalysisBranch,
        overloads: tuple[T.Overload, ...],
        modifiers: tuple[ModifierArgumentAnalysis, ...],
    ) -> tuple[list[ElementArguments], list[AnalysisBranch]]:
        """Enumerate parameter-ordered argument sources for an overload."""
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
        winners = _functions._collapse_equivalent_call_winners(
            _functions._collapse_equivalent_friendly_multidispatch_winners(
                _functions._best_candidates(candidates, branch)
            )
        )
        if not winners:
            self._diagnose(no_match_message, node)
            return None
        if (
            len(winners) > 1
            and branch.input_mode is not InputMode.INFER_INPUTS
            and not _functions._winners_specialize_inputs(winners, branch)
        ):
            self._diagnose(
                f"{ambiguous_message}\n"
                f"candidate overloads:\n{_utils._show_applied_overloads(winners)}",
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
            for args, popped, ordered_modifiers in _calls._source_element_arguments(
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
            prepared = _calls._prepare_element_call_branches(
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
                for args, popped, ordered_modifiers in _calls._source_element_arguments(
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
            candidate = _calls._apply_overload_to_branch(
                source.overload,
                source.arguments,
                source.branch,
                self.env.context,
                self.env,
                node.disambiguation,
                self,
            )
            if candidate is None:
                candidate = _calls._apply_overload_via_unit_overlay(
                    node.name,
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

            applied = _calls._apply_tag_overlay(
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
            applied = _functions._mark_multidispatch(
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
                _calls._returns_result_type(actual_returns),
                candidate.applied,
                (
                    candidate.overload_index
                    if candidate.overload_index is not None
                    else _calls._overload_index(overloads, overload)
                ),
                _calls._specialize_modifier_arguments(
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

        terminal, current = _utils._split_terminal_branches(current)
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
                        _calls._returns_result_type(candidate.applied.actual_returns),
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
        candidates = _calls._call_element_candidates(
            arg_branch,
            call_overload,
            call_values[0],
            call_values[1:],
            base_stack,
            explicit_function_order,
            node.disambiguation,
            self.env.context,
            self.env,
            self,
        )
        if candidates or not base_stack:
            return candidates

        return _calls._call_element_candidates(
            arg_branch,
            call_overload,
            base_stack[-1],
            call_values,
            base_stack[:-1],
            (),
            node.disambiguation,
            self.env.context,
            self.env,
            self,
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
                if (
                    item_result := _utils._list_item_analysis(branch, output)
                )
                is not None
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

        arity = _patterns._match_arity(node)
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
        typed_body = _patterns._typed_block(
            body_outputs,
            len(branch.typed_body),
            node.body,
        )
        outputs: list[AnalysisBranch] = list(body_outputs.branches)
        typed_handler_bodies: list[tuple[ASTNode | TypedNode, ...]] = []
        for handler in node.handlers:
            if handler.typ is not None:
                normalized_handler = T.normalize(handler.typ)
                if (
                    isinstance(normalized_handler, T.NominalType)
                    and self.env.lookup_trait(normalized_handler.name) is not None
                ):
                    self._diagnose(
                        f"try handler type {T.show(handler.typ)} is not a concrete "
                        "runtime fault type",
                        handler,
                    )
                elif not T.assignable(
                    handler.typ,
                    T.N(Symbol("Fault")),
                    self.env.context,
                ):
                    self._diagnose(
                        f"try handler type {T.show(handler.typ)} does not "
                        "implement Fault",
                        handler,
                    )
            handler_outputs = self.analyse_scoped_block(
                BranchSet((branch,)),
                handler.body,
            )
            typed_handler_bodies.append(
                _patterns._typed_block(
                    handler_outputs,
                    len(branch.typed_body),
                    handler.body,
                )
            )
            for output in handler_outputs:
                if output.inputs != branch.inputs:
                    self._diagnose("try handlers inferred different inputs", handler)
                    continue
                outputs.append(_patterns._try_handler_output(output, branch, handler))

        if not outputs:
            return BranchSet()

        joined: AnalysisBranch | None = None
        for output in outputs:
            joined = _patterns._join_try_output(
                branch,
                joined,
                output,
                self.env.context,
            )
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
                        _calls._returns_result_type(joined.stack.items),
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
                for pattern_type in _match_pattern_types(pattern):
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

    def _match_is_exhaustive(
        self,
        subject_types: tuple[T.Type, ...],
        node: MatchNode,
    ) -> bool:
        """Return the Boolean result of match is exhaustive during static analysis."""
        if any(
            case.is_default or _patterns._is_default_match_case(case.patterns)
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
            _patterns._optional_access_result_type(field_type)
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

        payload_type = _patterns._strict_optional_payload_type(receiver_type)
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
        return _patterns._optional_access_result_type(field_type), refined_receiver

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

    def _overload_function_variants(
        self,
        node: FunctionNode,
        origin: ASTNode,
    ) -> tuple[FunctionNode, ...] | None:
        """Expand explicit overload signatures into fully typed function nodes."""
        if not node.overloads:
            return (node,)

        source_params = node.params
        variants: list[FunctionNode] = []
        has_declared_signature = node.returns is not None or any(
            param.typ is not None for param in source_params or ()
        )
        if has_declared_signature:
            variants.append(replace(node, overloads=()))

        for signature in node.overloads:
            if source_params is not None and len(source_params) != len(signature.params):
                self._diagnose(
                    "overload signature has "
                    f"{len(signature.params)} parameter type(s), but the following "
                    f"function declares {len(source_params)} parameter(s)",
                    origin,
                )
                return None
            params = tuple(
                replace(param, typ=typ)
                for param, typ in zip(source_params, signature.params, strict=True)
            ) if source_params is not None else tuple(
                FunctionParam(None, typ) for typ in signature.params
            )
            variants.append(
                replace(
                    node,
                    params=params,
                    returns=signature.returns,
                    overloads=(),
                )
            )
        return tuple(variants)

    def _analyse_overloaded_function_literal(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
        origin: ASTNode,
    ) -> tuple[FunctionAnalysis, AnalysisBranch] | None:
        """Analyse every explicitly declared signature against one shared body."""
        variants = self._overload_function_variants(node, origin)
        if variants is None:
            return None
        if len(variants) == 1 and variants[0] is node:
            return self._analyse_function_literal(outer, node)

        typings: list[FunctionOverloadTyping] = []
        for variant in variants:
            genericized = _functions._genericize_function_node(
                variant,
                variant.generics,
            )
            result = self._analyse_function_literal(outer, genericized)
            if result is None:
                return None
            analysis, _ = result
            typings.extend(analysis.overloads)

        overloads = tuple(
            typing.overload
            for typing in typings
            if isinstance(typing.overload, T.Overload)
        )
        if len(overloads) != len(typings):
            self._diagnose("overload signatures must produce concrete function types", origin)
            return None
        typ = (
            T.Fn(overloads[0].params, overloads[0].returns, overloads[0].element_tags)
            if len(overloads) == 1 and not overloads[0].where_clause
            else T.Overloads(*overloads)
        )
        return FunctionAnalysis(typ, tuple(typings)), outer

    def _analyse_function_literal(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
        *,
        initial_function_locals: tuple[tuple[Symbol, T.Type], ...] = (),
    ) -> tuple[FunctionAnalysis, AnalysisBranch] | None:
        """Analyse function literal during static analysis."""
        declared_params = _functions._declared_params(node)
        _, where_error = static_where.validate_where_clause(
            params=declared_params,
            returns=node.returns or (),
            param_names=_functions._function_param_names_for_overload(
                node, declared_params
            ),
            clause=node.where_clause,
        )
        if where_error is not None:
            self._diagnose(
                f"invalid where clause: {where_error.message}",
                where_error.node or node,
            )
            return None
        node = _functions._contextualize_function_empty_returns(node)
        if node.params is not None and any(
            _functions._is_call_site_checked_param(param.typ) for param in node.params
        ):
            return self._call_site_checked_function(outer, node), outer

        top_level_captures = _functions._top_level_assignment_capture_nodes(outer, node)
        if top_level_captures:
            for capture in top_level_captures:
                self._diagnose(
                    f"cannot capture top-level assignment '{capture.name}'",
                    capture,
                )
            return None

        params = _functions._declared_params(node)
        body_params = tuple(
            _functions._parameter_value_type(
                _functions._anonymous_trait_subject_view(param)
            )
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
            captures=_functions._function_capture_source(outer),
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
        for name in _functions._static_body_variable_names(node):
            write = variables.write(
                name,
                T.Number,
                block_local=False,
            )
            if write.variables is None:
                diagnostic = write.diagnostic or f"cannot define '{name}'"
                self._diagnose(diagnostic, node)
                return None
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
            input_names=(
                tuple(param.name for param in node.params or ())
                if mode is not InputMode.INFER_INPUTS
                else ()
            ),
            variables=variables,
            input_mode=mode,
            cycle_params=body_params if mode is InputMode.CYCLE_EXPLICIT_PARAMS else (),
            atomic_type_vars=(
                outer.atomic_type_vars
                | _functions._atomic_parameter_type_vars(params)
            ),
            origin=outer.origin,
        )

        generic_constraints = _functions._generic_constraints(
            node.generics,
            node.generic_variances,
            node.generic_constraints,
        )
        structural_overloads = _functions._anonymous_trait_overloads(
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
        analysis = _functions._function_analysis_from_signatures(signatures)
        if analysis is None:
            if (
                node.params is not None
                and any(param.typ is None for param in node.params)
                and not function_analyser.diagnostics
            ):
                self.warnings.extend(function_analyser.warnings)
                self._extend_lint_findings(function_analyser.lint_findings)
                return self._call_site_checked_function(outer, node), outer
            self.diagnostics.extend(function_analyser.diagnostics)
            self.warnings.extend(function_analyser.warnings)
            self._extend_lint_findings(function_analyser.lint_findings)
            return None
        self.warnings.extend(function_analyser.warnings)
        self._extend_lint_findings(function_analyser.lint_findings)
        return analysis, outer

    def _call_site_checked_function(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
    ) -> FunctionAnalysis:
        """Compute call site checked function during static analysis."""
        params = _functions._declared_params(node)
        overload = _functions._function_overload(
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
        *,
        rank_values: dict[str, int] | None = None,
        type_values: dict[str, T.Type] | None = None,
        where_evaluated: bool = False,
        static_values: dict[str, int] | None = None,
    ) -> FunctionAnalysis | None:
        """Analyse a deferred function using call-site static bindings."""
        typed_node = cast(
            FunctionNode,
            _functions._substitute_rank_variables_in_ast(
                node, {}, type_values
            ),
        )
        parameter_node = cast(
            FunctionNode,
            _functions._substitute_rank_variables_in_ast(
                node, rank_values or {}, type_values
            ),
        )
        declared = tuple(parameter_node.params or ())
        if len(call_params) < len(declared):
            return None
        substituted_params = _functions._call_site_substituted_params(
            declared,
            call_params[-len(declared) :] if declared else (),
            self.env.context,
        )
        if substituted_params is None:
            return None
        call_site_node = FunctionNode(
            params=substituted_params,
            body=tuple(
                _resolve_pop_n_static_counts(item, static_values or {})
                for item in typed_node.body
            ),
            returns=typed_node.returns,
            where_clause=() if where_evaluated else typed_node.where_clause,
            element_tags=typed_node.element_tags,
            element_tags_explicit=typed_node.element_tags_explicit,
            companion_tags_allowed=typed_node.companion_tags_allowed,
            location=typed_node.location,
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
            captures=_functions._function_capture_source(outer),
        )
        for name in _functions._static_body_variable_names(node):
            write = variables.write(
                name,
                T.Number,
                block_local=False,
            )
            if write.variables is None:
                return None
            variables = write.variables
        initial = AnalysisBranch(
            stack=T.TypeStack(stack_params),
            inputs=call_params,
            variables=variables,
            input_mode=InputMode.NILADIC,
            atomic_type_vars=(
                outer.atomic_type_vars
                | _functions._atomic_parameter_type_vars(call_params)
            ),
            origin=outer.origin,
        )
        function_analyser = self._child_analyser(self.env.lexical_child_scope())
        final = function_analyser.analyse_block(
            BranchSet((initial,)), call_site_node.body
        )
        signatures = self._function_signatures(call_site_node, final)
        return _functions._function_analysis_from_signatures(signatures)

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
            final_element_tags = _functions._final_function_element_tags(
                node,
                body_element_tags,
                self.env,
            )
            self._validate_inferred_element_tags(
                node,
                body_element_tags,
                final_element_tags,
            )
            declared_params = _functions._declared_params(node)
            inputs = (
                declared_params
                if node.params is not None
                and any(
                    _functions._contains_anonymous_trait(param)
                    for param in declared_params
                )
                else branch.inputs
            )
            inputs = _functions._restore_parameter_markers(
                declared_params,
                inputs,
            )
            self._validate_data_element_tag_disjoints(
                inputs,
                final_element_tags,
                node,
            )
            signature = _functions._function_overload(
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
            if not _utils._has_never_return(signature)
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

        checked_returns = tuple(_utils._return_value_shape(typ) for typ in node.returns)
        expected = T.TypeStack(checked_returns)
        actual_returns = _utils._stack_returns(branch.stack, expected)
        if len(actual_returns) != len(node.returns):
            return None
        substitution = _calls._branch_argument_substitution(
            actual_returns,
            checked_returns,
            self.env.context,
        )
        if (
            substitution is None
            and node.where_clause
            and _functions._contains_rank_var(node.returns)
        ):
            return node.returns, branch
        if substitution is not None:
            branch = _calls._specialize_branch_arguments(branch, substitution)
        if not _utils._stack_assignable(branch.stack, expected, self.env.context):
            if node.where_clause and _functions._contains_rank_var(node.returns):
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

_ANALYSER_PARTS = (_functions, _calls, _patterns, _utils, _handlers)


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
