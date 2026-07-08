from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, fields, replace
from decimal import Decimal, InvalidOperation
from enum import Enum, auto
from itertools import count, permutations
from pathlib import Path
from typing import Any, cast

import valiance.analysis.annotations as annotation_hooks
import valiance.types as T
from valiance.analysis.builtins import default_environment
from valiance.asts import (
    AnnotationNode,
    AssertNode,
    ASTNode,
    AtNode,
    BindingPatternNode,
    CallArgument,
    CastNode,
    DefineNode,
    DictLiteralNode,
    ElementNode,
    ElementTagDeclarationNode,
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
    MatchNode,
    MatchPatternNode,
    NumberLiteralNode,
    ObjectNode,
    OrPatternNode,
    RecordLiteralNode,
    RestPatternNode,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TagDeclarationNode,
    TagOverlayNode,
    TraitRequirementNode,
    TryNode,
    TupleLiteralNode,
    TypedCallNode,
    TypedElementNode,
    TypedFunctionNode,
    TypedNode,
    TypedTagApplicationNode,
    TypedUnfoldNode,
    TypePatternNode,
    UnfoldNode,
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
)
from valiance.symbols import Symbol
from valiance.types.default_types import Boolean
from valiance.types.relations import merge_stacks

_branch_ids = count(1)


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
    ) -> tuple[BranchVariables | None, str | None]:
        """Return variables after assigning ``name`` in this branch."""
        ctx = ctx or T.Context()
        existing_block_local = _lookup(self.block_locals, name)
        if existing_block_local is not None:
            if name in self.block_constants:
                return None, f"cannot assign to constant '{name}'"
            stored_type = _assignment_stored_type(existing_block_local, typ, ctx)
            diagnostic = _assignment_error(name, typ, existing_block_local, ctx)
            if diagnostic is not None:
                return None, diagnostic
            if T.same(stored_type, existing_block_local):
                return self, None
            return (
                BranchVariables(
                    function_locals=self.function_locals,
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=_set_item(self.block_locals, name, stored_type),
                    function_constants=self.function_constants,
                    block_constants=self.block_constants,
                ),
                None,
            )
        if _lookup(self.parameters, name) is not None:
            return None, f"cannot assign to read-only parameter '{name}'"
        existing_function_local = _lookup(self.function_locals, name)
        if existing_function_local is not None:
            if name in self.function_constants:
                return None, f"cannot assign to constant '{name}'"
            stored_type = _assignment_stored_type(existing_function_local, typ, ctx)
            diagnostic = _assignment_error(name, typ, existing_function_local, ctx)
            if diagnostic is not None:
                return None, diagnostic
            return (
                BranchVariables(
                    function_locals=_set_item(self.function_locals, name, stored_type),
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=self.block_locals,
                    function_constants=self.function_constants,
                    block_constants=self.block_constants,
                ),
                None,
            )
        if _lookup(self.captures, name) is not None:
            return (
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
                None,
            )
        if block_local or _lookup(self.block_locals, name) is not None:
            return (
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
                None,
            )
        return (
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
            None,
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
    input_mode: InputMode = InputMode.TOP_LEVEL
    cycle_params: tuple[T.Type, ...] = ()
    cycle_index: int = 0
    break_type: T.Type | None = None
    diagnostics: tuple[str, ...] = ()
    origin: int = field(default_factory=lambda: next(_branch_ids))

    def with_stack(self, stack: T.TypeStack) -> AnalysisBranch:
        return _replace_branch(self, stack=stack)

    def with_variables(self, variables: BranchVariables) -> AnalysisBranch:
        return _replace_branch(self, variables=variables)

    def append_typed(self, typed_node: TypedNode) -> AnalysisBranch:
        return _replace_branch(self, typed_body=self.typed_body + (typed_node,))

    def with_diagnostic(self, message: str) -> AnalysisBranch:
        return _replace_branch(self, diagnostics=self.diagnostics + (message,))

    def with_break(self, typ: T.Type | None) -> AnalysisBranch:
        return _replace_branch(self, break_type=typ)

    def refine_type(self, old: T.Type, new: T.Type) -> AnalysisBranch:
        """Replace one inferred/generic type fact across the branch."""
        return _replace_branch(
            self,
            stack=_refine_stack(self.stack, old, new),
            inputs=tuple(_refine_type(item, old, new) for item in self.inputs),
            variables=self.variables.refine_type(old, new),
            typed_body=_refine_typed_body(self.typed_body, old, new),
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
                    _replace_branch(
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
                    _replace_branch(
                        self,
                        stack=remaining,
                        cycle_index=(self.cycle_index + missing)
                        % len(self.cycle_params),
                    ),
                )
            case _:
                return None


def _empty_branch_set() -> frozenset[AnalysisBranch]:
    return frozenset()


@dataclass(frozen=True)
class BranchSet:
    """A set of possible analysis branches."""

    branches: frozenset[AnalysisBranch] = field(default_factory=_empty_branch_set)

    @classmethod
    def one(cls, branch: AnalysisBranch) -> BranchSet:
        return cls(frozenset((branch,)))

    def __bool__(self) -> bool:
        return bool(self.branches)

    def __iter__(self) -> Iterator[AnalysisBranch]:
        return iter(self.branches)

    def __len__(self) -> int:
        return len(self.branches)

    def map_node(self, node: ASTNode, analyser: Analyser) -> BranchSet:
        """Analyse one node from every branch."""
        return analyser.analyse_node(self, node)

    def extend_block(self, nodes: tuple[ASTNode, ...], analyser: Analyser) -> BranchSet:
        """Analyse a sequence of nodes from this branch set."""
        current = self
        for node in nodes:
            active = BranchSet(
                frozenset(branch for branch in current if branch.break_type is None)
            )
            stopped = frozenset(
                branch for branch in current if branch.break_type is not None
            )
            if active:
                current = BranchSet(active.map_node(node, analyser).branches | stopped)
            else:
                current = BranchSet(stopped)
            if not current:
                return current
        return current

    def require_all(
        self,
        predicate: Callable[[AnalysisBranch], bool],
        diagnostic: str,
    ) -> BranchSet:
        """Keep the set only if every branch satisfies ``predicate``."""
        if all(predicate(branch) for branch in self.branches):
            return self
        for branch in self.branches:
            if not predicate(branch):
                branch.with_diagnostic(diagnostic)
        return BranchSet()

    def require_stack_top_assignable(
        self,
        expected: T.Type,
        ctx: T.Context,
    ) -> BranchSet:
        """Require every branch top to be a non-Never assignable value."""
        return self.require_all(
            lambda branch: bool(branch.stack)
            and not _is_never(branch.stack[-1])
            and T.assignable(branch.stack[-1], expected, ctx),
            f"expected {T.show(expected)} on top of stack",
        )

    def pop_stack_top(self) -> BranchSet:
        """Pop one stack value from every branch."""
        return BranchSet(
            frozenset(
                branch.with_stack(T.TypeStack(branch.stack.items[:-1]))
                for branch in self.branches
                if branch.stack
            )
        )

    def join(self, other: BranchSet, analyser: Analyser) -> BranchSet:
        """Merge two branch sets pairwise with unioned stacks."""
        joined: set[AnalysisBranch] = set()
        for left in self.branches:
            for right in other.branches:
                if left.inputs != right.inputs:
                    continue
                stack: T.TypeStack = T.merge_stacks(left.stack, right.stack)
                variables = left.variables.merge_against(
                    right.variables,
                    left.variables,
                )
                joined.add(_replace_branch(left, stack=stack, variables=variables))
        return BranchSet(frozenset(joined))

    def rebase(self, from_branches: BranchSet, to_branches: BranchSet) -> BranchSet:
        """Relate derived branches back to the origins that created them."""
        origins = {branch.origin for branch in from_branches}
        valid_targets = {branch.origin for branch in to_branches}
        if not origins or not valid_targets:
            return BranchSet()
        return BranchSet(
            frozenset(
                branch
                for branch in self.branches
                if branch.origin in origins or branch.origin in valid_targets
            )
        )


@dataclass(frozen=True)
class FunctionAnalysis:
    """Typed function literal result, including per-overload typed bodies."""

    typ: T.Type
    overloads: tuple[FunctionOverloadTyping, ...]


@dataclass(frozen=True)
class ListItemAnalysis:
    """One possible analysis result for a forked list item."""

    branch: AnalysisBranch
    typ: T.Type
    consumed: int


@dataclass(frozen=True)
class ModifierArgumentAnalysis:
    """Analysed function value supplied by an element modifier."""

    typ: T.Type
    typed_node: TypedFunctionNode


@dataclass(frozen=True)
class ElementCallPreparation:
    """Analysed explicit call arguments plus their runtime stack order."""

    branch: AnalysisBranch
    call_arg_order: tuple[int, ...]


class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""

    def __init__(
        self,
        env: T.Environment | None = None,
        *,
        module_loader: ModuleLoader | None = None,
        source_file: Path | None = None,
    ):
        self.env = env if env is not None else default_environment().child_scope()
        self.module_loader = module_loader or ModuleLoader()
        self.source_file = source_file
        self.diagnostics: list[str] = []
        self.warnings: list[str] = []
        self._friendly_owners: tuple[Symbol, ...] = ()

    def analyse(self, program: list[ASTNode]) -> list[TypedNode]:
        """Analyse a top-level sequence into typed nodes."""
        initial = BranchSet.one(AnalysisBranch(input_mode=InputMode.TOP_LEVEL))
        final = self.analyse_block(initial, tuple(program))
        if len(final) != 1:
            return [TypedNode(node, None) for node in program]
        return list(next(iter(final)).typed_body)

    def analyse_block(
        self,
        initial: BranchSet,
        nodes: tuple[ASTNode, ...],
    ) -> BranchSet:
        """Analyse a block as a branch-set transformation."""
        return initial.extend_block(nodes, self)

    def analyse_node(self, branches: BranchSet, node: ASTNode) -> BranchSet:
        """Analyse one node from a branch set."""
        next_branches: set[AnalysisBranch] = set()
        for branch in branches:
            next_branches.update(self._analyse_node_from_branch(branch, node))
        return BranchSet(frozenset(next_branches))

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
    ) -> set[AnalysisBranch]:
        match node:
            case NumberLiteralNode(value):
                typ = _number_literal_type(value)
                return {
                    branch.with_stack(branch.stack.push(typ)).append_typed(
                        TypedNode(node, typ)
                    )
                }
            case StringLiteralNode(_):
                return {
                    branch.with_stack(branch.stack.push(T.String)).append_typed(
                        TypedNode(node, T.String)
                    )
                }
            case StringInterpolationNode():
                return self._string_interpolation(branch, node)
            case ElementNode():
                return self._element(branch, node)
            case TagApplicationNode():
                return self._tag_application(branch, node)
            case TagDeclarationNode():
                return self._tag_declaration(branch, node)
            case ElementTagDeclarationNode():
                return self._element_tag_declaration(branch, node)
            case TagOverlayNode():
                return self._tag_overlay(branch, node)
            case CastNode():
                return self._cast(branch, node)
            case StackShuffleNode():
                return self._stack_shuffle(branch, node)
            case FunctionNode():
                if not self._validate_annotations(node.annotations, "fn", node):
                    return {branch.append_typed(TypedNode(node, None))}
                function_node = _genericize_function_node(node, node.generics)
                result = self._analyse_function_literal(branch, function_node)
                if result is None:
                    return {branch.append_typed(TypedNode(node, None))}
                function, typed_branch = result
                typed_node = TypedFunctionNode(
                    function_node, function.typ, function.overloads
                )
                return {
                    typed_branch.with_stack(
                        typed_branch.stack.push(function.typ)
                    ).append_typed(typed_node)
                }
            case DefineNode(name, function_node):
                return self._define(branch, node, name, function_node)
            case ObjectNode():
                return self._object_declaration(branch, node)
            case ImportNode():
                return self._import(branch, node)
            case ListLiteralNode():
                return self._list_literal(branch, node)
            case TupleLiteralNode():
                return self._tuple_literal(branch, node)
            case RecordLiteralNode():
                return self._record_literal(branch, node)
            case DictLiteralNode():
                return self._dict_literal(branch, node)
            case CallNode():
                return self._call(branch, node)
            case GetVariableNode(name):
                typ = branch.variables.read(name)
                if typ is None:
                    self._diagnose(f"undefined variable '{name}'", node)
                    return {branch.append_typed(TypedNode(node, None))}
                return {
                    branch.with_stack(branch.stack.push(typ)).append_typed(
                        TypedNode(node, typ)
                    )
                }
            case SetVariableNode(name, declared_type, constant):
                if not branch.stack:
                    if branch.input_mode is InputMode.INFER_INPUTS:
                        inferred = declared_type or T.V(f"_inferred_{name}")
                        variables, diagnostic = branch.variables.write(
                            name,
                            inferred,
                            constant=constant,
                            ctx=self.env.context,
                        )
                        if diagnostic is not None:
                            self._diagnose(diagnostic, node)
                            return {branch.with_diagnostic(diagnostic)}
                        if variables is None:
                            return {
                                branch.with_diagnostic(
                                    f"cannot assign to variable '{name}'"
                                )
                            }
                        return {
                            branch.with_variables(variables).append_typed(
                                TypedNode(node, inferred)
                            )
                        }
                    return {
                        branch.with_diagnostic(
                            f"empty stack when trying to assign to variable '{name}'"
                        )
                    }
                value_type = branch.stack[-1]
                variable_type = declared_type or value_type
                if declared_type is not None and not T.assignable(
                    value_type,
                    declared_type,
                    self.env.context,
                ):
                    self._diagnose(
                        f"cannot assign {T.show(value_type)} to variable '{name}' "
                        f"of declared type {T.show(declared_type)}",
                        node,
                    )
                    return {branch.append_typed(TypedNode(node, None))}
                variables, diagnostic = branch.variables.write(
                    name,
                    variable_type,
                    block_local=True,
                    constant=constant,
                    ctx=self.env.context,
                )
                if diagnostic is not None:
                    self._diagnose(diagnostic, node)
                    return {branch.with_diagnostic(diagnostic)}
                if variables is None:
                    return {
                        branch.with_diagnostic(f"cannot assign to variable '{name}'")
                    }
                return {
                    branch.with_variables(variables)
                    .with_stack(branch.stack.pop())
                    .append_typed(TypedNode(node, variable_type))
                }
            case SetVariablesNode(targets):
                return self._set_variables(branch, node, targets)
            case FieldAccessNode(name):
                return self._field_access(branch, node, name)
            case FieldSetNode(name):
                return self._field_set(branch, node, name)
            case IndexAccessNode():
                return self._index_access(branch, node)
            case IndexSetNode():
                return self._index_set(branch, node)
            case IfNode():
                return self._if(branch, node)
            case AssertNode():
                return self._assert(branch, node)
            case WhileNode():
                return self._while(branch, node)
            case UnfoldNode():
                return self._unfold(branch, node)
            case AtNode():
                return self._at(branch, node)
            case ForNode():
                return self._foreach(branch, node)
            case MatchNode():
                return self._match(branch, node)
            case TryNode():
                return self._try(branch, node)
            case BreakNode():
                return self._break(branch, node)
            case _:
                return {branch.append_typed(TypedNode(node, None))}

    def _define(
        self,
        branch: AnalysisBranch,
        node: DefineNode,
        name: Symbol,
        function_node: FunctionNode,
    ) -> set[AnalysisBranch]:
        if not self._validate_annotations(node.annotations, "define", node):
            return {branch.append_typed(TypedNode(node, None))}
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            function_node,
            node.annotations,
        )
        function_node = _genericize_function_node(function_node, node.generics)
        self._validate_function_element_tags(function_node, node)
        declared_overload = (
            _fully_typed_overload(function_node)
            if not node.generics and _body_references_element(function_node.body, name)
            else None
        )
        if (
            declared_overload is not None
            and declared_overload not in self.env.overloads_for(name)
        ):
            self.env.define_overload(name, declared_overload)
        result = self._analyse_function_literal(branch, function_node)
        if result is None:
            return {branch.append_typed(TypedNode(node, None))}
        function, typed_branch = result
        generic_constraints = _generic_constraints(
            node.generics,
            node.generic_constraints,
        )
        overload_typings = list(function.overloads)
        for typing_index, typing in enumerate(function.overloads):
            if not isinstance(typing.overload, T.Overload):
                continue
            overload = typing.overload
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
                continue
            if name.text.startswith("#") and not _validator_overload_ok(
                overload,
                self.env.context,
            ):
                self._diagnose(
                    f"tag validator '{name}' must return #boolean Number",
                    node,
                )
                continue
            self._validate_data_tags((overload.params, overload.returns), node)
            overload = _with_generic_constraints(overload, generic_constraints)
            overload = annotation_hooks.DEFAULT_REGISTRY.transform_overload(
                overload,
                node.annotations,
            )
            if node.is_multi:
                overload = replace(overload, is_multi=True)
                if not _has_multimethod_fallback(
                    overload,
                    self.env.overloads_for(name),
                    self.env.context,
                ):
                    self._diagnose(
                        f"multi define '{name}' requires a non-multi fallback "
                        "with compatible parameters and identical returns",
                        node,
                    )
                    continue
            overload_typings[typing_index] = replace(typing, overload=overload)
            if overload not in self.env.overloads_for(name):
                self.env.define_overload(name, overload)
            original_index = _overload_index(self.env.overloads_for(name), overload)
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
                    if generated not in self.env.overloads_for(name):
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
        return {typed_branch.append_typed(typed_node)}

    def _tag_declaration(
        self,
        branch: AnalysisBranch,
        node: TagDeclarationNode,
    ) -> set[AnalysisBranch]:
        if node.disjoint is not None:
            self.env.add_disjoint_tags(node.tag.name, node.disjoint.name)
        elif node.parent is not None:
            self.env.add_variant_tag(node.tag.name, node.parent.name)
        elif node.kind == Symbol("constructed"):
            self.env.add_constructed_tag(node.tag.name)
        elif node.kind == Symbol("unit"):
            self.env.add_unit_tag(node.tag.name)
        else:
            self.env.add_computed_tag(node.tag.name)
        return {branch.append_typed(TypedNode(node, None))}

    def _validate_function_element_tags(
        self,
        node: FunctionNode,
        origin: ASTNode,
    ) -> None:
        positives = tuple(tag for tag in node.element_tags if not tag.absent)
        for tag in positives:
            definition = self.env.lookup_element_tag(tag.name)
            if definition is None:
                self._diagnose(f"undeclared element tag '{tag.name}'", origin)
                continue
            if (
                definition.kind is T.ElementTagKind.COMPANION
                and tag not in node.companion_tags_allowed
            ):
                self._diagnose(
                    f"companion element tag '{tag.name}' cannot be directly attached",
                    origin,
                )
        self._validate_element_tag_disjoints(positives, origin)

    def _validate_element_tag_disjoints(
        self,
        tags: Iterable[T.ElementTag],
        origin: ASTNode,
    ) -> None:
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
        self._validate_element_tag_disjoints(
            (tag for tag in final_tags if not tag.absent),
            node,
        )
        if not node.element_tags_explicit:
            return
        declared_properties = {
            tag
            for tag in node.element_tags
            if not tag.absent
            and (
                definition := self.env.lookup_element_tag(tag.name)
            ) is not None
            and definition.kind is T.ElementTagKind.PROPERTY
        }
        for tag in body_tags:
            if tag.absent or tag in declared_properties:
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

    def _validate_data_tags(
        self,
        groups: Iterable[Iterable[T.Type]],
        origin: ASTNode,
    ) -> None:
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

    def _element_tag_declaration(
        self,
        branch: AnalysisBranch,
        node: ElementTagDeclarationNode,
    ) -> set[AnalysisBranch]:
        if node.disjoint is not None:
            self.env.add_disjoint_element_tags(node.name, node.disjoint)
        elif node.kind == Symbol("companion"):
            self.env.add_companion_element_tag(node.name)
        else:
            self.env.add_property_element_tag(node.name)
        return {branch.append_typed(TypedNode(node, None))}

    def _tag_overlay(
        self,
        branch: AnalysisBranch,
        node: TagOverlayNode,
    ) -> set[AnalysisBranch]:
        public = node.visibility == Symbol("public")
        for element in node.elements:
            for params, returns in node.signatures:
                self._validate_data_tags((params, returns), node)
                overload = T.Overload(params, returns)
                if node.generics:
                    overload = _genericize_overload(overload, node.generics)
                self.env.define_tag_overlay(
                    node.tag.name,
                    element,
                    overload,
                    public=public,
                )
        return {branch.append_typed(TypedNode(node, None))}

    def _object_declaration(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> set[AnalysisBranch]:
        if not self._validate_annotations(node.annotations, node.kind.text, node):
            return {branch.append_typed(TypedNode(node, None))}
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
        return {branch.append_typed(TypedNode(node, None))}

    def _object_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> set[AnalysisBranch]:
        if not self._validate_object_lifecycle(node):
            return {branch.append_typed(TypedNode(node, None))}
        if node.target is not None:
            if node.fields:
                self._diagnose(
                    "trait implementation blocks cannot declare fields",
                    node,
                )
                return {branch.append_typed(TypedNode(node, None))}
            target = T.normalize(node.target)
            if isinstance(target, T.NominalType):
                self.env.add_trait_impl(node.name, target.name)
            current = branch.append_typed(TypedNode(node, None))
            for definition in node.definitions:
                current = self._register_friendly_definition(
                    current,
                    node.name,
                    definition,
                )
            return {current}

        attributes = tuple(self._object_attribute(field) for field in node.fields)
        if any(attribute is None for attribute in attributes):
            return {branch.append_typed(TypedNode(node, None))}
        object_attributes = tuple(
            _genericize_attribute(attribute, node.generics)
            for attribute in attributes
            if attribute
        )
        generic_variance = _declared_or_inferred_variance(
            node.generics,
            node.generic_variances,
            object_attributes,
            (),
        )
        generic_constraints = _generic_constraints(
            node.generics,
            node.generic_constraints,
        )
        self.env.define_object(
            node.name,
            object_attributes,
            generics=node.generics,
            generic_variance=generic_variance,
        )
        if annotation_hooks.has_annotation(node.annotations, "errType"):
            self.env.add_trait_impl(node.name, Symbol("Err"))
        self.env.define_constructor(
            node.name,
            object_attributes,
            defaults=frozenset(field.name for field in node.fields if field.default),
            result_type=_declared_nominal(node.name, node.generics),
            generic_constraints=generic_constraints,
        )
        current = branch.append_typed(TypedNode(node, None))
        for definition in node.definitions:
            current = self._register_friendly_definition(current, node.name, definition)
        return {current}

    def _validate_object_lifecycle(self, node: ObjectNode) -> bool:
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
    ) -> set[AnalysisBranch]:
        requirements = tuple(
            _genericize_requirement(requirement, node.generics)
            for item in node.requirements
            if (requirement := _trait_requirement(item)) is not None
        )
        self.env.define_trait(
            node.name,
            generics=node.generics,
            generic_variance=_declared_or_inferred_variance(
                node.generics,
                node.generic_variances,
                (),
                requirements,
            ),
            requirements=requirements,
        )
        if node.target is not None:
            target = T.normalize(node.target)
            if isinstance(target, T.NominalType):
                self.env.add_trait_parent(node.name, target.name)
        current = branch.append_typed(TypedNode(node, None))
        for definition in node.definitions:
            current = self._register_friendly_definition(current, node.name, definition)
        return {current}

    def _variant_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> set[AnalysisBranch]:
        generic_constraints = _generic_constraints(
            node.generics,
            node.generic_constraints,
        )
        requirements = tuple(
            _genericize_requirement(requirement, node.generics)
            for item in node.requirements
            if (requirement := _trait_requirement(item)) is not None
        )
        members: list[Symbol] = []
        for member in node.variants:
            member_name = Symbol(f"{node.name}.{member.name}")
            members.append(member_name)
            attributes = tuple(self._object_attribute(field) for field in member.fields)
            object_attributes = tuple(
                _genericize_attribute(attribute, node.generics)
                for attribute in attributes
                if attribute
            )
            variant_type = _declared_nominal(node.name, node.generics)
            self.env.define_object(
                member_name,
                object_attributes,
                generics=node.generics,
            )
            if annotation_hooks.has_annotation(node.annotations, "errType"):
                self.env.add_trait_impl(member_name, Symbol("Err"))
            self.env.define_constructor(
                member_name,
                object_attributes,
                result_type=variant_type,
                generic_constraints=generic_constraints,
            )
            self.env.define_overload(
                member.name,
                T.Overload(
                    tuple(attribute.typ for attribute in object_attributes),
                    (variant_type,),
                    generic_constraints,
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
        return {
            branch.append_typed(
                TypedNode(node, _declared_nominal(node.name, node.generics))
            )
        }

    def _enum_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> set[AnalysisBranch]:
        value_type = T.V(node.generics[0].text) if node.generics else None
        members = tuple(
            T.EnumMemberDefinition(
                Symbol(f"{node.name}.{member.name}"),
                value_type,
                bool(member.value),
            )
            for member in node.enum_members
        )
        self.env.define_enum(node.name, members, value_type=value_type)
        return {
            branch.append_typed(
                TypedNode(node, _declared_nominal(node.name, node.generics))
            )
        }

    def _object_attribute(self, field: ObjectFieldNode) -> T.ObjectAttribute | None:
        if field.typ is not None:
            typ = field.typ
        elif field.default:
            outputs = self.analyse_block(
                BranchSet.one(AnalysisBranch(input_mode=InputMode.TOP_LEVEL)),
                field.default,
            )
            types = tuple(output.stack[-1] for output in outputs if output.stack)
            if not types:
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

    def _register_friendly_definition(
        self,
        branch: AnalysisBranch,
        owner: Symbol,
        definition: DefineNode,
    ) -> AnalysisBranch:
        if not self._validate_annotations(definition.annotations, "define", definition):
            return branch.append_typed(TypedNode(definition, None))
        owner_definition = self.env.lookup_object(owner)
        self_type = _declared_nominal(
            owner,
            owner_definition.generics if owner_definition is not None else (),
        )
        params = (FunctionParam(Symbol("self"), self_type),) + tuple(
            definition.function.params or ()
        )
        function_node = annotation_hooks.DEFAULT_REGISTRY.transform_function(
            FunctionNode(
                params=params,
                body=definition.function.body,
                returns=definition.function.returns,
                where_clause=definition.function.where_clause,
                element_tags=definition.function.element_tags,
                annotations=definition.function.annotations,
                location=definition.function.location,
            ),
            definition.annotations,
        )
        function_node = _genericize_function_node(function_node, definition.generics)
        self._friendly_owners = self._friendly_owners + (owner,)
        try:
            result = self._analyse_function_literal(branch, function_node)
        finally:
            self._friendly_owners = self._friendly_owners[:-1]
        if result is None:
            return branch.append_typed(TypedNode(definition, None))
        function, typed_branch = result
        generic_constraints = _generic_constraints(
            definition.generics,
            definition.generic_constraints,
        )
        for name in (definition.name, Symbol(f"{owner}::{definition.name}")):
            for typing in function.overloads:
                if not isinstance(typing.overload, T.Overload):
                    continue
                self.env.define_overload(
                    name,
                    annotation_hooks.DEFAULT_REGISTRY.transform_overload(
                        _with_generic_constraints(typing.overload, generic_constraints),
                        definition.annotations,
                    ),
                )
        return typed_branch

    def _import(
        self,
        branch: AnalysisBranch,
        node: ImportNode,
    ) -> set[AnalysisBranch]:
        typed_nodes: list[TypedFunctionNode] = []
        for spec in node.specs:
            try:
                exports, resolved_spec, definitions = self._load_import_definitions(
                    spec
                )
                import_environment_facts(exports, resolved_spec, self.env)
            except ModuleLoadError as exc:
                self._diagnose(str(exc), node)
                return {branch.append_typed(TypedNode(node, None))}
            for definition in definitions:
                self._register_imported_definition(definition.name, definition.typed)
                typed_nodes.append(definition.typed)
        imported = branch
        for typed_node in typed_nodes:
            imported = imported.append_typed(typed_node)
        return {imported.append_typed(TypedNode(node, None))}

    def _load_import_definitions(
        self,
        spec: ImportSpec,
    ):
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
    ) -> None:
        for overload in _callable_overloads(typed_node.typ):
            self.env.define_overload(name, overload)

    def _string_interpolation(
        self,
        branch: AnalysisBranch,
        node: StringInterpolationNode,
    ) -> set[AnalysisBranch]:
        current = BranchSet.one(branch)
        expression_count = 0
        for part in node.parts:
            if isinstance(part, str):
                continue
            expression_count += 1
            current = self.analyse_block(current, part)
            if not current:
                return set()
            if any(not output.stack for output in current):
                self._diagnose(
                    "string interpolation expression must leave a value",
                    node,
                )
                return set()
        return {
            _replace_branch(
                output,
                stack=_pop_stack(output.stack, expression_count).push(T.String),
                typed_body=branch.typed_body,
            ).append_typed(TypedNode(node, T.String))
            for output in current
            if len(output.stack) >= expression_count
        }

    def _element(
        self,
        branch: AnalysisBranch,
        node: ElementNode,
    ) -> set[AnalysisBranch]:
        overloads = self.env.overloads_for(node.name)
        if not overloads:
            self._diagnose(f"unknown element '{node.name}'", node)
            return set()
        if not annotation_hooks.valid_element_annotations(node.annotations):
            self._diagnose(
                f"unsupported element annotation on '{node.name}'",
                node,
            )
            return set()

        modifier_args = self._modifier_argument_types(branch, node)
        if modifier_args is None:
            return {branch.append_typed(TypedNode(node, None))}
        if node.modifier_args and not _modifier_arity_matches(overloads, modifier_args):
            self._diagnose(
                f"element '{node.name}' expects "
                f"{_show_modifier_counts(overloads)} ':' function argument(s), "
                f"got {len(modifier_args)}",
                node,
            )
            return set()

        if node.call_args:
            if node.name == Symbol("call"):
                return self._call_element_call(branch, node, overloads)
            return self._element_call(branch, node, overloads, modifier_args)

        candidates: list[
            tuple[
                T.AppliedOverload,
                AnalysisBranch,
                tuple[ModifierArgumentAnalysis, ...],
                tuple[int, ...],
            ]
        ] = []
        for overload in overloads:
            for args, popped, ordered_modifiers in _source_element_arguments(
                branch,
                overload,
                modifier_args,
                self.env.context,
            ):
                candidate = _apply_overload_to_branch(
                    overload,
                    args,
                    popped,
                    self.env.context,
                    self.env,
                    node.disambiguation,
                    self,
                )
                if candidate is not None:
                    applied, candidate_branch = candidate
                    applied = _apply_tag_overlay(
                        node.name,
                        args,
                        applied,
                        self.env.context,
                        self.env,
                    )
                    applied = _mark_multidispatch(
                        applied,
                        overloads,
                        self.env.context,
                    )
                    candidates.append((applied, candidate_branch, ordered_modifiers))

        stack_before = branch.stack
        winners = _best_candidates(candidates, branch)
        if not winners:
            self._diagnose(
                f"no overloads for element '{node.name}' match stack "
                f"{_show_stack(stack_before)}; available overloads: "
                f"{_show_overloads(overloads)}",
                node,
            )
            return set()
        if (
            len(winners) > 1
            and branch.input_mode is not InputMode.INFER_INPUTS
            and not _winners_specialize_inputs(winners, branch)
        ):
            self._diagnose(
                f"ambiguous overloads for element '{node.name}' with stack "
                f"{_show_stack(stack_before)}; candidates: "
                f"{_show_applied_overloads(winners)}",
                node,
            )
            return set()

        results: set[AnalysisBranch] = set()
        for applied, popped, ordered_modifiers in winners:
            if applied.overload.annotation_error is not None:
                self._diagnose(applied.overload.annotation_error, node)
                continue
            if applied.overload.annotation_warning is not None:
                self._warn(applied.overload.annotation_warning, node)
            actual_returns = annotation_hooks.annotated_element_returns(
                node,
                applied.actual_returns,
            )
            results.add(
                popped.with_stack(popped.stack.push(*actual_returns)).append_typed(
                    TypedElementNode(
                        node,
                        _returns_result_type(actual_returns),
                        applied,
                        _overload_index(overloads, applied.overload),
                        _specialize_modifier_arguments(
                            applied,
                            ordered_modifiers,
                            self.env.context,
                        ),
                    )
                )
            )
        return results

    def _stack_shuffle(
        self,
        branch: AnalysisBranch,
        node: StackShuffleNode,
    ) -> set[AnalysisBranch]:
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
            return set()
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
                return set()
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
        return {
            popped.with_stack(stack).append_typed(
                TypedNode(node, _returns_result_type(post_types))
            )
        }

    def _element_call(
        self,
        branch: AnalysisBranch,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
        modifier_args: tuple[ModifierArgumentAnalysis, ...],
    ) -> set[AnalysisBranch]:
        candidates: list[
            tuple[
                T.AppliedOverload,
                AnalysisBranch,
                tuple[ModifierArgumentAnalysis, ...],
                tuple[int, ...],
            ]
        ] = []
        for overload in overloads:
            prepared = _prepare_element_call_branches(
                branch,
                overload,
                node.call_args,
                bool(node.modifier_args),
                self,
            )
            for preparation in prepared:
                for args, popped, ordered_modifiers in _source_element_arguments(
                    preparation.branch,
                    overload,
                    modifier_args,
                    self.env.context,
                    preparation.call_arg_order,
                ):
                    candidate = _apply_overload_to_branch(
                        overload,
                        args,
                        popped,
                        self.env.context,
                        self.env,
                        node.disambiguation,
                        self,
                    )
                    if candidate is not None:
                        applied, candidate_branch = candidate
                        applied = _apply_tag_overlay(
                            node.name,
                            args,
                            applied,
                            self.env.context,
                            self.env,
                        )
                        applied = _mark_multidispatch(
                            applied,
                            overloads,
                            self.env.context,
                        )
                        candidates.append(
                            (
                                applied,
                                candidate_branch,
                                ordered_modifiers,
                                preparation.call_arg_order,
                            )
                        )

        winners = _best_candidates(candidates, branch)
        if not winners:
            self._diagnose(
                f"no overloads for element '{node.name}' match explicit call syntax",
                node,
            )
            return set()
        if (
            len(winners) > 1
            and branch.input_mode is not InputMode.INFER_INPUTS
            and not _winners_specialize_inputs(winners, branch)
        ):
            self._diagnose(
                f"ambiguous overloads for element '{node.name}' with explicit call "
                f"syntax; candidates: {_show_applied_overloads(winners)}",
                node,
            )
            return set()

        results: set[AnalysisBranch] = set()
        for applied, popped, ordered_modifiers, call_arg_order in winners:
            if applied.overload.annotation_error is not None:
                self._diagnose(applied.overload.annotation_error, node)
                continue
            if applied.overload.annotation_warning is not None:
                self._warn(applied.overload.annotation_warning, node)
            actual_returns = annotation_hooks.annotated_element_returns(
                node,
                applied.actual_returns,
            )
            results.add(
                popped.with_stack(popped.stack.push(*actual_returns)).append_typed(
                    TypedElementNode(
                        node,
                        _returns_result_type(actual_returns),
                        applied,
                        _overload_index(overloads, applied.overload),
                        _specialize_modifier_arguments(
                            applied,
                            ordered_modifiers,
                            self.env.context,
                        ),
                        call_arg_order,
                    )
                )
            )
        return results

    def _call_element_call(
        self,
        branch: AnalysisBranch,
        node: ElementNode,
        overloads: tuple[T.Overload, ...],
    ) -> set[AnalysisBranch]:
        if node.modifier_args:
            self._diagnose("element 'call' does not accept ':' arguments", node)
            return set()
        if any(arg.name is not None or arg.placeholder for arg in node.call_args):
            self._diagnose(
                "element 'call' explicit arguments must be positional",
                node,
            )
            return set()

        current = BranchSet.one(branch)
        for arg in node.call_args:
            current = current.extend_block(arg.value, self)
            if not current:
                return set()

        call_arg_count = len(node.call_args)
        candidates: list[
            tuple[T.AppliedOverload, AnalysisBranch, tuple[int, ...], int]
        ] = []
        for arg_branch in current:
            if len(arg_branch.stack) < call_arg_count:
                continue
            call_values = (
                arg_branch.stack.items[-call_arg_count:] if call_arg_count else ()
            )
            base_stack = arg_branch.stack.items[:-call_arg_count]

            explicit_function_order = (
                (*range(1, call_arg_count), 0) if call_arg_count > 1 else ()
            )
            branch_candidates = _call_element_candidates(
                arg_branch,
                overloads[0],
                call_values[0],
                call_values[1:],
                base_stack,
                explicit_function_order,
                node.disambiguation,
                self.env.context,
            )
            if not branch_candidates and base_stack:
                branch_candidates = _call_element_candidates(
                    arg_branch,
                    overloads[0],
                    base_stack[-1],
                    call_values,
                    base_stack[:-1],
                    (),
                    node.disambiguation,
                    self.env.context,
                )
            candidates.extend(branch_candidates)

        winners = _best_candidates(candidates, branch)
        if not winners:
            self._diagnose(
                "no overloads for element 'call' match explicit call syntax",
                node,
            )
            return set()
        if (
            len(winners) > 1
            and branch.input_mode is not InputMode.INFER_INPUTS
            and not _winners_specialize_inputs(winners, branch)
        ):
            self._diagnose(
                "ambiguous overloads for element 'call' with explicit call syntax; "
                f"candidates: {_show_applied_overloads(winners)}",
                node,
            )
            return set()

        results: set[AnalysisBranch] = set()
        for applied, popped, call_arg_order, call_overload_index in winners:
            results.add(
                popped.with_stack(
                    popped.stack.push(*applied.actual_returns)
                ).append_typed(
                    TypedElementNode(
                        node,
                        _returns_result_type(applied.actual_returns),
                        applied,
                        0,
                        (),
                        call_arg_order,
                        call_overload_index,
                    )
                )
            )
        return results

    def _modifier_argument_types(
        self,
        branch: AnalysisBranch,
        node: ElementNode,
    ) -> tuple[ModifierArgumentAnalysis, ...] | None:
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

    def _tag_application(
        self,
        branch: AnalysisBranch,
        node: TagApplicationNode,
    ) -> set[AnalysisBranch]:
        if not branch.stack:
            self._diagnose(
                f"empty stack when applying tag '{_show_tag(node.tag)}'",
                node,
            )
            return {branch.append_typed(TypedNode(node, None))}

        value_type = branch.stack[-1]
        if node.tag.absent:
            tagged = _remove_data_tag(value_type, node.tag)
            if tagged is None:
                self._diagnose(
                    f"cannot remove absent tag '{_show_tag(node.tag)}' from "
                    f"{value_type}",
                    node,
                )
                return {branch.append_typed(TypedNode(node, None))}
        else:
            tagged = _with_data_tags(value_type, (node.tag,), self.env.context)
            validator: T.AppliedOverload | None = None
            validator_index: int | None = None
            validator_name = Symbol(f"#{node.tag.name}")
            validator_overloads = self.env.overloads_for(validator_name)
            if validator_overloads:
                matches: list[tuple[T.AppliedOverload, int]] = []
                for index, overload in enumerate(validator_overloads):
                    applied = T.apply_overload(
                        overload,
                        (value_type,),
                        self.env.context,
                    )
                    if applied is None:
                        continue
                    if not _validator_overload_ok(overload, self.env.context):
                        self._diagnose(
                            f"tag validator '{validator_name}' must return "
                            "#boolean Number",
                            node,
                        )
                        return {branch.append_typed(TypedNode(node, None))}
                    matches.append((applied, index))
                if not matches:
                    self._diagnose(
                        f"no validator overload for '{validator_name}' matches "
                        f"{T.show(value_type)}",
                        node,
                    )
                    return {branch.append_typed(TypedNode(node, None))}
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
                    return {branch.append_typed(TypedNode(node, None))}

        stack = T.TypeStack((*branch.stack.items[:-1], tagged))
        typed: TypedNode
        if node.tag.absent:
            typed = TypedNode(node, tagged)
        else:
            typed = TypedTagApplicationNode(node, tagged, validator, validator_index)
        return {branch.with_stack(stack).append_typed(typed)}

    def _cast(
        self,
        branch: AnalysisBranch,
        node: CastNode,
    ) -> set[AnalysisBranch]:
        target = T.normalize(node.typ)
        if not branch.stack:
            self._diagnose(
                f"empty stack when casting to {T.show(target)}",
                node,
            )
            return {branch.append_typed(TypedNode(node, None))}

        source = branch.stack[-1]
        if node.checked:
            if T.assignable(source, target, self.env.context):
                self._diagnose(
                    f"checked cast to {T.show(target)} is already statically safe",
                    node,
                )
                return set()
            if not _types_overlap(source, target, self.env.context):
                if _type_contains_rank_var(target):
                    stack = T.TypeStack((*branch.stack.items[:-1], target))
                    return {
                        branch.with_stack(stack).append_typed(TypedNode(node, target))
                    }
                self._diagnose(
                    f"cannot cast {T.show(source)} to {T.show(target)}",
                    node,
                )
                return set()
        elif not T.assignable(source, target, self.env.context):
            self._diagnose(
                f"cannot safely cast {T.show(source)} to {T.show(target)}",
                node,
            )
            return set()

        stack = T.TypeStack((*branch.stack.items[:-1], target))
        return {branch.with_stack(stack).append_typed(TypedNode(node, target))}

    def _call(
        self,
        branch: AnalysisBranch,
        node: CallNode,
    ) -> set[AnalysisBranch]:
        if not branch.stack:
            self._diagnose("call requires a function on the stack", node)
            return set()

        callable_type = T.normalize(branch.stack[-1])
        overloads = _callable_overloads(callable_type)
        if not overloads:
            self._diagnose(
                f"cannot call non-function value of type {T.show(callable_type)}",
                node,
            )
            return set()

        callable_popped = branch.with_stack(branch.stack.pop())
        arg_branches = self.analyse_block(BranchSet.one(callable_popped), node.args)

        candidates: list[tuple[T.AppliedOverload, AnalysisBranch]] = []
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
                if candidate is not None:
                    candidates.append(candidate)

        winners = _best_candidates(candidates, callable_popped)
        if not winners:
            self._diagnose(
                f"no overloads for call target {T.show(callable_type)} match stack "
                f"{_show_stack(callable_popped.stack)}; available overloads: "
                f"{_show_overloads(overloads)}",
                node,
            )
            return set()
        if (
            len(winners) > 1
            and branch.input_mode is not InputMode.INFER_INPUTS
            and not _winners_specialize_inputs(winners, callable_popped)
        ):
            self._diagnose(
                f"ambiguous call target {T.show(callable_type)} with stack "
                f"{_show_stack(callable_popped.stack)}; candidates: "
                f"{_show_applied_overloads(winners)}",
                node,
            )
            return set()

        results: set[AnalysisBranch] = set()
        for applied, popped in winners:
            results.add(
                popped.with_stack(
                    popped.stack.push(*applied.actual_returns)
                ).append_typed(
                    TypedCallNode(
                        node,
                        _returns_result_type(applied.actual_returns),
                        applied,
                    )
                )
            )
        return results

    def _set_variables(
        self,
        branch: AnalysisBranch,
        node: SetVariablesNode,
        targets: tuple[SetVariableNode, ...],
    ) -> set[AnalysisBranch]:
        if not targets:
            return {branch.append_typed(TypedNode(node, None))}
        available = min(len(branch.stack), len(targets))
        missing = len(targets) - available
        if missing and branch.input_mode is not InputMode.INFER_INPUTS:
            return {
                branch.with_diagnostic(
                    "empty stack when trying to assign to multiple variables"
                )
            }
        inferred = tuple(
            target.declared_type or T.V(f"_inferred_{target.name}")
            for target in targets[:missing]
        )
        value_types = inferred + branch.stack.items[len(branch.stack) - available :]
        variables = branch.variables
        for target, value_type in zip(targets, value_types, strict=True):
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
                return {branch.append_typed(TypedNode(node, None))}
            variables, diagnostic = variables.write(
                target.name,
                variable_type,
                block_local=True,
                constant=target.constant,
                ctx=self.env.context,
            )
            if diagnostic is not None:
                self._diagnose(diagnostic, target)
                return {branch.with_diagnostic(diagnostic)}
            if variables is None:
                return {
                    branch.with_diagnostic(f"cannot assign to variable '{target.name}'")
                }
        return {
            branch.with_variables(variables)
            .with_stack(branch.stack.pop(available))
            .append_typed(TypedNode(node, None))
        }

    def _field_access(
        self,
        branch: AnalysisBranch,
        node: FieldAccessNode,
        name: Symbol,
    ) -> set[AnalysisBranch]:
        sourced = self._source_field_receiver(branch, name)
        if sourced is None:
            self._diagnose(f"empty stack when trying to access field '{name}'", node)
            return set()

        receiver_type, field_type, branch = sourced
        if field_type is None:
            self._diagnose(
                f"type {T.show(receiver_type)} has no known field '{name}'",
                node,
            )
            return set()

        return {
            branch.with_stack(branch.stack.push(field_type)).append_typed(
                TypedNode(node, field_type)
            )
        }

    def _field_set(
        self,
        branch: AnalysisBranch,
        node: FieldSetNode,
        name: Symbol,
    ) -> set[AnalysisBranch]:
        if len(branch.stack) < 2:
            self._diagnose(
                f"field assignment to '{name}' requires receiver and value",
                node,
            )
            return set()
        receiver_type = branch.stack[-2]
        value_type = branch.stack[-1]
        field_type, refined_receiver = self._field_type(
            receiver_type,
            name,
            branch,
            write=True,
        )
        if field_type is None:
            self._diagnose(
                f"type {T.show(receiver_type)} has no writable field '{name}'",
                node,
            )
            return set()
        if not T.assignable(value_type, field_type, self.env.context):
            self._diagnose(
                f"cannot assign {T.show(value_type)} to field '{name}' "
                f"of type {T.show(field_type)}",
                node,
            )
            return set()
        result_type = receiver_type if refined_receiver is None else refined_receiver
        return {
            branch.with_stack(
                T.TypeStack(branch.stack.items[:-2]).push(result_type)
            ).append_typed(TypedNode(node, result_type))
        }

    def _index_access(
        self,
        branch: AnalysisBranch,
        node: IndexAccessNode,
    ) -> set[AnalysisBranch]:
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
                return set()
            (receiver_type,), base_branch = sourced
        else:
            self._diagnose("indexing requires receiver and index value(s)", node)
            return set()
        index_types = branch.stack.items[-selector_values:] if selector_values else ()
        if not _selectors_assignable(
            receiver_type,
            node.selectors,
            index_types,
            self.env.context,
        ):
            self._diagnose("list indexing requires Integer index value(s)", node)
            return set()
        result_type = _indexed_type(receiver_type, node.selectors, node.spread)
        return {
            base_branch.with_stack(base_branch.stack.push(result_type)).append_typed(
                TypedNode(node, result_type)
            )
        }

    def _index_set(
        self,
        branch: AnalysisBranch,
        node: IndexSetNode,
    ) -> set[AnalysisBranch]:
        selector_values = _selector_value_count(node.selectors)
        required = selector_values + 2
        if len(branch.stack) < required:
            self._diagnose(
                "indexed assignment requires value, receiver, and index",
                node,
            )
            return set()
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
            return set()
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
            return set()
        stack = T.TypeStack(branch.stack.items[:-required]).push(updated_receiver_type)
        return {
            branch.with_stack(stack).append_typed(
                TypedNode(node, updated_receiver_type)
            )
        }

    def _list_literal(
        self,
        branch: AnalysisBranch,
        node: ListLiteralNode,
    ) -> set[AnalysisBranch]:
        if not node.items:
            if node.typ is not None:
                typ = T.normalize(node.typ)
                if not isinstance(typ, T.CollectionType):
                    self._diagnose(
                        f"empty list cast needs a list type, got {T.show(typ)}",
                        node,
                    )
                    return set()
                return {
                    branch.with_stack(branch.stack.push(typ)).append_typed(
                        TypedNode(node, typ)
                    )
                }
            self._diagnose(
                "empty list literal requires a type annotation or cast",
                node,
            )
            return set()

        item_options: list[tuple[ListItemAnalysis, ...]] = []
        for item in node.items:
            item_outputs = self.analyse_block(BranchSet.one(branch), item)
            options = tuple(
                item_result
                for output in item_outputs
                if (item_result := _list_item_analysis(branch, output)) is not None
            )
            if not options:
                self._diagnose("list item must leave a value on the stack", node)
                return set()
            item_options.append(options)

        results: set[AnalysisBranch] = set()
        for combo in _cartesian_product(tuple(item_options)):
            inputs = _merge_inferred_inputs(branch.inputs, combo)
            if inputs is None:
                continue
            consumed = max(item.consumed for item in combo)
            item_type = T.U(*(item.typ for item in combo))
            list_type = T.C(T.ListExactType, item_type)
            variables = _merge_list_item_variables(branch.variables, combo)
            results.add(
                _replace_branch(
                    branch,
                    stack=_pop_stack(branch.stack, consumed).push(list_type),
                    inputs=inputs,
                    variables=variables,
                ).append_typed(TypedNode(node, list_type))
            )
        return results

    def _tuple_literal(
        self,
        branch: AnalysisBranch,
        node: TupleLiteralNode,
    ) -> set[AnalysisBranch]:
        item_options = self._literal_item_options(branch, node.items, node)
        if item_options is None:
            return set()

        results: set[AnalysisBranch] = set()
        for combo in _cartesian_product(item_options):
            inputs = _merge_inferred_inputs(branch.inputs, combo)
            if inputs is None:
                continue
            consumed = max((item.consumed for item in combo), default=0)
            tuple_type = T.Tup(*(item.typ for item in combo))
            variables = _merge_list_item_variables(branch.variables, combo)
            results.add(
                _replace_branch(
                    branch,
                    stack=_pop_stack(branch.stack, consumed).push(tuple_type),
                    inputs=inputs,
                    variables=variables,
                ).append_typed(TypedNode(node, tuple_type))
            )
        return results

    def _record_literal(
        self,
        branch: AnalysisBranch,
        node: RecordLiteralNode,
    ) -> set[AnalysisBranch]:
        expressions = tuple(expr for _, expr in node.fields)
        item_options = self._literal_item_options(branch, expressions, node)
        if item_options is None:
            return set()

        results: set[AnalysisBranch] = set()
        for combo in _cartesian_product(item_options):
            inputs = _merge_inferred_inputs(branch.inputs, combo)
            if inputs is None:
                continue
            consumed = max((item.consumed for item in combo), default=0)
            record_type = T.Row(
                T.N(Symbol("record")),
                *(
                    T.Field(name, item.typ)
                    for (name, _), item in zip(node.fields, combo, strict=True)
                ),
            )
            variables = _merge_list_item_variables(branch.variables, combo)
            results.add(
                _replace_branch(
                    branch,
                    stack=_pop_stack(branch.stack, consumed).push(record_type),
                    inputs=inputs,
                    variables=variables,
                ).append_typed(TypedNode(node, record_type))
            )
        return results

    def _dict_literal(
        self,
        branch: AnalysisBranch,
        node: DictLiteralNode,
    ) -> set[AnalysisBranch]:
        expressions = tuple(expr for entry in node.entries for expr in entry)
        item_options = self._literal_item_options(branch, expressions, node)
        if item_options is None:
            return set()

        results: set[AnalysisBranch] = set()
        for combo in _cartesian_product(item_options):
            inputs = _merge_inferred_inputs(branch.inputs, combo)
            if inputs is None:
                continue
            consumed = max((item.consumed for item in combo), default=0)
            key_types = combo[::2]
            value_types = combo[1::2]
            dict_type = T.N(
                Symbol("Dict"),
                T.U(*(item.typ for item in key_types)),
                T.U(*(item.typ for item in value_types)),
            )
            variables = _merge_list_item_variables(branch.variables, combo)
            results.add(
                _replace_branch(
                    branch,
                    stack=_pop_stack(branch.stack, consumed).push(dict_type),
                    inputs=inputs,
                    variables=variables,
                ).append_typed(TypedNode(node, dict_type))
            )
        return results

    def _literal_item_options(
        self,
        branch: AnalysisBranch,
        expressions: tuple[tuple[ASTNode, ...], ...],
        node: ASTNode,
    ) -> tuple[tuple[ListItemAnalysis, ...], ...] | None:
        item_options: list[tuple[ListItemAnalysis, ...]] = []
        for expression in expressions:
            item_outputs = self.analyse_block(BranchSet.one(branch), expression)
            options = tuple(
                item_result
                for output in item_outputs
                if (item_result := _list_item_analysis(branch, output)) is not None
            )
            if not options:
                self._diagnose("literal item must leave a value on the stack", node)
                return None
            item_options.append(options)
        return tuple(item_options)

    def _foreach(self, branch: AnalysisBranch, node: ForNode) -> set[AnalysisBranch]:
        consumes_stack_iterable = bool(branch.stack)
        if not branch.stack:
            item = _anonymous_type_var(branch, 1)
            sourced = branch.source_arguments((T.ExactList(item),))
            if sourced is None:
                self._diagnose("for loop requires iterable on the stack", node)
                return set()
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
            return set()
        body_stack = branch.stack.pop() if consumes_stack_iterable else branch.stack
        body_branch = branch.with_stack(body_stack)
        cycle_params = (item_type,)
        if node.index_variable is not None:
            cycle_params = (item_type, T.Integer)
        body_branch = _replace_branch(
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

        body_outputs = self.analyse_block(BranchSet.one(body_branch), node.body)
        if not body_outputs:
            return set()
        refined_item_type = _loop_variable_output_type(node.variable, body_outputs)
        if (
            refined_item_type is not None
            and _contains_type_var(item_type)
            and not T.same(item_type, refined_item_type)
        ):
            body_branch = body_branch.refine_type(item_type, refined_item_type)
            body_outputs = BranchSet(
                frozenset(
                    output.refine_type(item_type, refined_item_type)
                    for output in body_outputs
                )
            )
        break_types = tuple(
            output.break_type
            for output in body_outputs
            if output.break_type is not None
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
        typed_for = TypedNode(node, result_type)
        return {
            _refine_branch_like(branch, body_branch)
            .with_stack(body_branch.stack.push(result_type))
            .with_variables(variables)
            .append_typed(typed_for)
        }

    def _assert(
        self,
        branch: AnalysisBranch,
        node: AssertNode,
    ) -> set[AnalysisBranch]:
        condition = self.analyse_block(BranchSet.one(branch), node.condition)
        condition = condition.require_stack_top_assignable(Boolean, self.env.context)
        if not condition:
            self._diagnose("assert condition must be a boolean value", node)
            return set()

        success = branch.append_typed(TypedNode(node, None))
        if not node.else_branch:
            return {success}

        else_outputs = self.analyse_block(BranchSet.one(branch), node.else_branch)
        error_types = tuple(_top_or_none(output.stack) for output in else_outputs)
        error_type = T.U(*error_types) if error_types else T.NoneType()
        assert_error = T.N(Symbol("AssertError"), error_type)
        return {success.with_stack(success.stack.push(assert_error))}

    def _while(
        self,
        branch: AnalysisBranch,
        node: WhileNode,
    ) -> set[AnalysisBranch]:
        loop_input = branch
        if node.params is not None:
            params = _params_to_types(node.params)
            sourced = branch.source_arguments(params)
            if sourced is None:
                self._diagnose("while loop inputs do not match stack", node)
                return set()
            _, loop_input = sourced
            loop_input = loop_input.with_stack(loop_input.stack.push(*params))
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
            loop_input = _replace_branch(
                loop_input,
                input_mode=InputMode.CYCLE_EXPLICIT_PARAMS,
                cycle_params=params,
            )

        condition = self.analyse_block(BranchSet.one(loop_input), node.condition)
        condition = condition.require_stack_top_assignable(Boolean, self.env.context)
        if not condition:
            self._diagnose("while condition must be a boolean value", node)
            return set()

        body_inputs = condition.pop_stack_top()
        body_outputs = self.analyse_block(body_inputs, node.body)
        if not body_outputs:
            return set()

        joined: AnalysisBranch | None = None
        for output in body_outputs:
            candidate = output
            if joined is None:
                joined = candidate
                continue
            if joined.inputs != candidate.inputs:
                self._diagnose("while body inferred different inputs", node)
                return set()
            stack = merge_stacks(joined.stack, candidate.stack)
            variables = joined.variables.merge_against(
                candidate.variables,
                loop_input.variables,
            )
            joined = joined.with_stack(stack).with_variables(variables)
        if joined is None:
            return set()

        variables = (
            joined.variables
            if node.params is None
            else joined.variables.merge_against(loop_input.variables, branch.variables)
        )
        result = _refine_branch_like(branch, joined).with_variables(variables)
        return {
            result.append_typed(
                TypedNode(node, _returns_result_type(result.stack.items))
            )
        }

    def _unfold(
        self,
        branch: AnalysisBranch,
        node: UnfoldNode,
    ) -> set[AnalysisBranch]:
        body_function = FunctionNode(
            params=node.params,
            body=node.body,
            annotations=(AnnotationNode(Symbol("returnAll")),),
            element_tags=frozenset(),
            location=node.location,
        )
        body_analysis = self._analyse_unfold_body_function(branch, body_function)
        if body_analysis is None:
            return set()
        body_function_analysis = body_analysis

        candidates: list[tuple[T.AppliedOverload, AnalysisBranch, int]] = []
        for overload in _callable_overloads(body_function_analysis.typ):
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
                if self._analyse_function_literal(branch, condition_function) is None:
                    self._diagnose("unfold condition must return a boolean value", node)
                    continue
            sourced = branch.source_arguments(overload.params)
            if sourced is None:
                self._diagnose("unfold inputs do not match stack", node)
                continue
            args, popped = sourced
            applied = T.apply_overload(overload, args, self.env.context)
            if applied is None:
                continue
            candidates.append((applied, popped, state_arity))

        results: set[AnalysisBranch] = set()
        for applied, popped, state_arity in _best_candidates(candidates, branch):
            generated = _unfold_emitted_type(applied.params, applied.actual_returns)
            list_type = T.WithTag(T.ExactList(generated), "infinite")
            results.add(
                popped.with_stack(popped.stack.push(list_type)).append_typed(
                    TypedUnfoldNode(node, list_type, state_arity=state_arity)
                )
            )
        return results

    def _analyse_unfold_body_function(
        self,
        outer: AnalysisBranch,
        node: FunctionNode,
    ) -> FunctionAnalysis | None:
        if node.params is None:
            analysed = self._analyse_function_literal(outer, node)
            return None if analysed is None else analysed[0]

        params = _declared_params(node)
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(node.params, params, strict=True)
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=_function_capture_source(outer),
        )
        initial = AnalysisBranch(
            inputs=params,
            variables=variables,
            input_mode=InputMode.CYCLE_EXPLICIT_PARAMS if params else InputMode.NILADIC,
            cycle_params=params,
            origin=outer.origin,
        )
        function_analyser = Analyser(self.env)
        function_analyser._friendly_owners = self._friendly_owners
        final = function_analyser.analyse_block(BranchSet.one(initial), node.body)
        signatures = self._function_signatures(node, final)
        analysis = _function_analysis_from_signatures(signatures)
        if analysis is None:
            self.diagnostics.extend(function_analyser.diagnostics)
            self.warnings.extend(function_analyser.warnings)
            return None
        self.warnings.extend(function_analyser.warnings)
        return analysis

    def _at(
        self,
        branch: AnalysisBranch,
        node: AtNode,
    ) -> set[AnalysisBranch]:
        body_branch = _replace_branch(
            branch,
            input_mode=InputMode.CYCLE_EXPLICIT_PARAMS,
            cycle_params=branch.stack.items,
        )
        outputs = self.analyse_block(BranchSet.one(body_branch), node.body)
        return {
            output.append_typed(
                TypedNode(node, _returns_result_type(output.stack.items))
            )
            for output in outputs
        }

    def _break(
        self,
        branch: AnalysisBranch,
        node: BreakNode,
    ) -> set[AnalysisBranch]:
        value_outputs = self.analyse_block(BranchSet.one(branch), node.values)
        outputs: set[AnalysisBranch] = set()
        for value_branch in value_outputs:
            break_type = _top_or_none(value_branch.stack)
            outputs.add(
                value_branch.append_typed(TypedNode(node, break_type)).with_break(
                    break_type
                )
            )
        return outputs

    def _if(
        self,
        branch: AnalysisBranch,
        node: IfNode,
    ) -> set[AnalysisBranch]:
        incoming = BranchSet.one(branch)
        condition = self.analyse_block(incoming, node.condition)
        condition = condition.require_stack_top_assignable(Boolean, self.env.context)
        if not condition:
            self._diagnose("if condition must be a boolean value", node)
            return set()

        body_inputs = condition.pop_stack_top()
        then_outputs = self.analyse_block(body_inputs, node.then_branch)
        else_outputs = self.analyse_block(body_inputs, node.else_branch)

        outputs: set[AnalysisBranch] = set()
        for left in then_outputs:
            for right in else_outputs:
                if left.inputs != right.inputs:
                    self._diagnose("if branches inferred different inputs", node)
                    continue
                if left.break_type is not None or right.break_type is not None:
                    for output in (left, right):
                        typ = output.break_type
                        if typ is None:
                            typ = _returns_result_type(output.stack.items)
                        outputs.add(output.append_typed(TypedNode(node, typ)))
                    continue
                stack = merge_stacks(left.stack, right.stack)
                base = _replace_branch(
                    _refine_branch_like(branch, left),
                    inputs=left.inputs,
                )
                variables = left.variables.merge_against(
                    right.variables,
                    base.variables,
                )
                typed_if = TypedNode(node, _returns_result_type(stack.items))
                outputs.add(
                    base.with_stack(stack)
                    .with_variables(variables)
                    .append_typed(typed_if)
                )
        return outputs

    def _match(
        self,
        branch: AnalysisBranch,
        node: MatchNode,
    ) -> set[AnalysisBranch]:
        if not node.cases:
            self._diagnose("match requires at least one case", node)
            return set()

        arity = _match_arity(node)
        if arity is None:
            self._diagnose("match cases must match the same number of values", node)
            return set()
        if arity == 0:
            self._diagnose("match requires at least one pattern per case", node)
            return set()

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
                f"match requires {arity} value{'s' if arity != 1 else ''} "
                "on the stack",
                node,
            )
            return set()
        stack_subjects, body_input = sourced
        subject_types = tuple(reversed(stack_subjects))
        if not self._match_is_exhaustive(subject_types, node):
            return set()

        joined: AnalysisBranch | None = None
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
            case_input = _replace_branch(
                case_input,
                input_mode=InputMode.CYCLE_EXPLICIT_PARAMS,
                cycle_params=subject_types,
                cycle_index=0,
            )
            if not self._match_guards_are_valid(subject_types, case.patterns, node):
                return set()
            case_outputs = self.analyse_block(BranchSet.one(case_input), case.body)
            for output in case_outputs:
                candidate = output
                if candidate.break_type is not None:
                    typ = candidate.break_type
                    if typ is None:
                        typ = _returns_result_type(candidate.stack.items)
                    candidate = candidate.append_typed(TypedNode(node, typ))
                candidate = _replace_branch(
                    candidate,
                    typed_body=body_input.typed_body,
                    input_mode=body_input.input_mode,
                    cycle_params=body_input.cycle_params,
                    cycle_index=body_input.cycle_index,
                )
                if joined is None:
                    joined = candidate
                    continue
                if joined.inputs != candidate.inputs:
                    merged_inputs = _merge_branch_inputs(
                        joined.inputs,
                        candidate.inputs,
                    )
                    if merged_inputs is None:
                        self._diagnose("match cases inferred different inputs", node)
                        return set()
                else:
                    merged_inputs = joined.inputs
                stack = merge_stacks(joined.stack, candidate.stack)
                variables = joined.variables.merge_against(
                    candidate.variables,
                    body_input.variables,
                )
                base = (
                    _refine_branch_like(branch, joined)
                    if len(branch.inputs) == len(joined.inputs)
                    else joined
                )
                joined = (
                    base.with_stack(stack).with_variables(variables)
                )
                joined = _replace_branch(joined, inputs=merged_inputs)
            previous_patterns.append(case.patterns)

        if joined is None:
            return set()
        return {
            joined.append_typed(
                TypedNode(node, _returns_result_type(joined.stack.items))
            )
        }

    def _try(
        self,
        branch: AnalysisBranch,
        node: TryNode,
    ) -> set[AnalysisBranch]:
        if not node.handlers:
            self._diagnose("try requires at least one handler", node)
            return set()

        body_outputs = self.analyse_block(BranchSet.one(branch), node.body)
        outputs: set[AnalysisBranch] = set(body_outputs.branches)
        for handler in node.handlers:
            handler_outputs = self.analyse_block(
                BranchSet.one(branch),
                handler.body,
            )
            for output in handler_outputs:
                if output.inputs != branch.inputs:
                    self._diagnose("try handlers inferred different inputs", handler)
                    continue
                handler_result = _returns_result_type(output.stack.items)
                if handler_result is None:
                    handler_result = T.NoneType()
                outputs.add(
                    _refine_branch_like(branch, output)
                    .with_stack(
                        branch.stack.push(T.N(Symbol("PanicError"), handler_result))
                    )
                    .append_typed(TypedNode(handler, handler_result))
                )

        if not outputs:
            return set()

        joined: AnalysisBranch | None = None
        for output in outputs:
            if joined is None:
                joined = output
                continue
            if joined.inputs != output.inputs:
                self._diagnose("try branches inferred different inputs", node)
                return set()
            stack = merge_stacks(joined.stack, output.stack)
            variables = joined.variables.merge_against(
                output.variables,
                branch.variables,
            )
            joined = (
                _refine_branch_like(branch, joined)
                .with_stack(stack)
                .with_variables(variables)
            )
        if joined is None:
            return set()
        return {
            joined.append_typed(
                TypedNode(node, _returns_result_type(joined.stack.items))
            )
        }

    def _match_guards_are_valid(
        self,
        subject_types: tuple[T.Type, ...],
        patterns: tuple[MatchPatternNode, ...],
        node: MatchNode,
    ) -> bool:
        guards = tuple(_match_pattern_guards(patterns, subject_types))
        for guard, subject_type in guards:
            guard_input = AnalysisBranch(
                stack=T.TypeStack((subject_type,)),
                variables=BranchVariables(),
                input_mode=InputMode.TOP_LEVEL,
            )
            outputs = self.analyse_block(BranchSet.one(guard_input), guard)
            outputs = outputs.require_stack_top_assignable(Boolean, self.env.context)
            if not outputs:
                self._diagnose("match guard must be a boolean value", node)
                return False
        return True

    def _match_is_exhaustive(
        self,
        subject_types: tuple[T.Type, ...],
        node: MatchNode,
    ) -> bool:
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
    ) -> tuple[T.Type, T.Type | None, AnalysisBranch] | None:
        if branch.stack:
            receiver_type = branch.stack[-1]
            popped = branch.with_stack(branch.stack.pop())
            field_type, refined_receiver = self._field_type(receiver_type, name, popped)
            if refined_receiver is not None:
                popped = popped.refine_type(receiver_type, refined_receiver)
                receiver_type = refined_receiver
            return receiver_type, field_type, popped

        if branch.input_mode is not InputMode.INFER_INPUTS:
            return None

        base = _anonymous_type_var(branch, 1)
        field_type = _anonymous_type_var(branch, 2)
        receiver_type = T.Row(base, T.Field(name, field_type))
        return (
            receiver_type,
            field_type,
            _replace_branch(branch, inputs=branch.inputs + (receiver_type,)),
        )

    def _field_type(
        self,
        receiver_type: T.Type,
        name: Symbol,
        branch: AnalysisBranch,
        *,
        write: bool = False,
    ) -> tuple[T.Type | None, T.Type | None]:
        receiver_type = T.normalize(receiver_type)
        if isinstance(receiver_type, T.RowType):
            if write:
                return None, None
            existing = _row_field_type(receiver_type, name)
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
    ) -> tuple[FunctionAnalysis, AnalysisBranch] | None:
        if _needs_call_site_checking(node):
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
        body_params = tuple(_anonymous_trait_subject_view(param) for param in params)
        mode = _function_input_mode(node)
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(node.params or (), body_params, strict=True)
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=_function_capture_source(outer),
        )
        recursive_overload = annotation_hooks.recursive_overload(node, params)
        if annotation_hooks.has_annotation(node.annotations, "recursive"):
            if recursive_overload is None:
                self._diagnose(
                    "@recursive requires explicit parameter and return types",
                    node,
                )
                return None
            variables, _diagnostic = variables.write(
                Symbol("this"),
                T.Fn(
                    recursive_overload.params,
                    recursive_overload.returns,
                    recursive_overload.element_tags,
                ),
                block_local=False,
            )
            if variables is None:
                return None
        for name in _static_body_variable_names(node):
            variables, _diagnostic = variables.write(
                name,
                T.Number,
                block_local=False,
            )
            if variables is None:
                variables = BranchVariables.from_parameters(
                    named_params,
                    captures=_function_capture_source(outer),
                )
        initial_stack = T.TypeStack(
            body_params if mode is InputMode.CYCLE_EXPLICIT_PARAMS else ()
        )
        initial = AnalysisBranch(
            stack=initial_stack,
            inputs=body_params if mode is not InputMode.INFER_INPUTS else (),
            variables=variables,
            input_mode=mode,
            cycle_params=body_params if mode is InputMode.CYCLE_EXPLICIT_PARAMS else (),
            origin=outer.origin,
        )

        structural_overloads = _anonymous_trait_overloads(*params)
        function_env = self.env.child_scope() if structural_overloads else self.env
        for name, overload in structural_overloads:
            function_env.overloads.setdefault(name, []).append(overload)
        if recursive_overload is not None and annotation_hooks.has_annotation(
            node.annotations,
            "recursive",
        ):
            if function_env is self.env:
                function_env = self.env.child_scope()
            function_env.define_overload(Symbol("this"), recursive_overload)
        function_analyser = Analyser(function_env)
        function_analyser._friendly_owners = self._friendly_owners
        final = function_analyser.analyse_block(BranchSet.one(initial), node.body)
        signatures = self._function_signatures(node, final)
        analysis = _function_analysis_from_signatures(signatures)
        if analysis is None:
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
        params = _declared_params(node)
        overload = T.Overload(
            params,
            (),
            param_names=_function_param_names_for_overload(node, params),
            call_site_body=(outer, node),
            annotation_error=annotation_hooks.annotation_error_message(
                node.annotations
            ),
            annotation_warning=annotation_hooks.annotation_warning_message(
                node.annotations
            ),
            param_defaults=_function_param_defaults_for_overload(node, params),
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
        function_analyser = Analyser(self.env)
        function_analyser._friendly_owners = self._friendly_owners
        final = function_analyser.analyse_block(BranchSet.one(initial), node.body)
        signatures = self._function_signatures(call_site_node, final)
        return _function_analysis_from_signatures(signatures)

    def _function_signatures(
        self,
        node: FunctionNode,
        branches: BranchSet,
    ) -> dict[T.Overload, tuple[TypedNode, ...]]:
        signatures: dict[T.Overload, tuple[TypedNode, ...]] = {}
        for branch in branches:
            refined = self._function_returns(node, branch)
            if refined is None:
                continue
            returns, branch = refined
            body_element_tags = frozenset(_typed_body_element_tags(branch.typed_body))
            declared_element_tags = frozenset(node.element_tags)
            final_element_tags = frozenset(
                set(declared_element_tags) | set(body_element_tags)
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
            signature = T.Overload(
                inputs,
                returns,
                where_clause=node.where_clause,
                param_names=_function_param_names_for_overload(node, inputs),
                element_tags=final_element_tags,
                annotation_error=annotation_hooks.annotation_error_message(
                    node.annotations
                ),
                annotation_warning=annotation_hooks.annotation_warning_message(
                    node.annotations
                ),
                param_defaults=_function_param_defaults_for_overload(
                    node,
                    inputs,
                ),
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
        if annotation_hooks.has_annotation(node.annotations, "returnAll"):
            return branch.stack.items, branch
        if node.returns is None:
            return (branch.stack.items[-1:] if branch.stack else ()), branch

        expected = T.TypeStack(node.returns)
        actual_returns = _stack_returns(branch.stack, expected)
        if len(actual_returns) != len(node.returns):
            return None
        substitution = _branch_argument_substitution(
            actual_returns,
            node.returns,
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
        diagnostics = annotation_hooks.DEFAULT_REGISTRY.validate(
            annotations,
            target,
            node,
        )
        for diagnostic in diagnostics:
            self._diagnose(diagnostic, node)
        return not diagnostics

    def _diagnose(self, message: str, node: ASTNode | None = None) -> None:
        self.diagnostics.append(_diagnostic_message(message, node))

    def _warn(self, message: str, node: ASTNode | None = None) -> None:
        self.warnings.append(_diagnostic_message(message, node))


def analyse(
    program: list[ASTNode],
    env: T.Environment | None = None,
) -> list[TypedNode]:
    return Analyser(env).analyse(program)


def analyse_function(node: FunctionNode, env: T.Environment) -> T.Type | None:
    return Analyser(env).analyse_function(node)


def analyse_function_details(
    node: FunctionNode,
    env: T.Environment,
) -> FunctionAnalysis | None:
    return Analyser(env).analyse_function_details(node)


def _replace_branch(
    branch: AnalysisBranch,
    *,
    stack: T.TypeStack | None = None,
    inputs: tuple[T.Type, ...] | None = None,
    variables: BranchVariables | None = None,
    typed_body: tuple[TypedNode, ...] | None = None,
    input_mode: InputMode | None = None,
    cycle_params: tuple[T.Type, ...] | None = None,
    cycle_index: int | None = None,
    break_type: T.Type | None = None,
    diagnostics: tuple[str, ...] | None = None,
    origin: int | None = None,
) -> AnalysisBranch:
    return AnalysisBranch(
        stack=branch.stack if stack is None else stack,
        inputs=branch.inputs if inputs is None else inputs,
        variables=branch.variables if variables is None else variables,
        typed_body=branch.typed_body if typed_body is None else typed_body,
        input_mode=branch.input_mode if input_mode is None else input_mode,
        cycle_params=branch.cycle_params if cycle_params is None else cycle_params,
        cycle_index=branch.cycle_index if cycle_index is None else cycle_index,
        break_type=branch.break_type if break_type is None else break_type,
        diagnostics=branch.diagnostics if diagnostics is None else diagnostics,
        origin=branch.origin if origin is None else origin,
    )


def _declared_params(node: FunctionNode) -> tuple[T.Type, ...]:
    if node.params is None:
        return ()
    return _params_to_types(node.params)


def _fully_typed_overload(node: FunctionNode) -> T.Overload | None:
    if node.params is None or node.returns is None:
        return None
    if any(param.typ is None for param in node.params):
        return None
    params = tuple(param.typ for param in node.params if param.typ is not None)
    return T.Overload(
        params,
        node.returns,
        where_clause=node.where_clause,
        param_names=_function_param_names_for_overload(node, params),
        element_tags=node.element_tags,
        annotation_error=annotation_hooks.annotation_error_message(node.annotations),
        annotation_warning=annotation_hooks.annotation_warning_message(
            node.annotations
        ),
        param_defaults=_function_param_defaults_for_overload(node, params),
    )


def _validate_define_niladic_name(name: Symbol, overload: T.Overload) -> bool:
    is_named_nilad = name.text.startswith("\\")
    is_inferred_nilad = len(overload.params) == 0
    return is_named_nilad == is_inferred_nilad


def _body_references_element(body: tuple[ASTNode, ...], name: Symbol) -> bool:
    return any(_node_references_element(node, name) for node in body)


def _node_references_element(node: ASTNode, name: Symbol) -> bool:
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
    for item in value:
        if isinstance(item, ASTNode) and _node_references_element(item, name):
            return True
        if isinstance(item, tuple) and _tuple_references_element(item, name):
            return True
    return False


def _needs_call_site_checking(node: FunctionNode) -> bool:
    if node.params is None:
        return False
    return any(_is_call_site_checked_param(param.typ) for param in node.params)


def _is_call_site_checked_param(typ: T.Type | None) -> bool:
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
    declared = T.normalize(declared)
    if _is_bare_function_type(declared):
        return isinstance(T.normalize(actual), (T.FunctionType, T.OverloadSetType))
    return T.compatible(actual, declared, ctx)


def _call_site_substitute_type(declared: T.Type, actual: T.Type) -> T.Type:
    declared = T.normalize(declared)
    if _is_bare_function_type(declared) or isinstance(declared, T.VariadicTupleType):
        return actual
    return declared


def _is_bare_function_type(typ: T.Type) -> bool:
    typ = T.normalize(typ)
    return (
        isinstance(typ, T.FunctionType) and typ.params is None and typ.returns is None
    )


def _call_site_checked_returns(analysis: FunctionAnalysis) -> tuple[T.Type, ...] | None:
    overloads = _callable_overloads(analysis.typ)
    if len(overloads) != 1:
        return None
    return overloads[0].returns


def _function_param_names_for_overload(
    node: FunctionNode,
    inputs: tuple[T.Type, ...],
) -> tuple[Symbol | None, ...]:
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
    if node.params is None:
        return (None,) * len(inputs)
    defaults = tuple(param.default or None for param in node.params)
    if len(defaults) < len(inputs):
        return (None,) * (len(inputs) - len(defaults)) + defaults
    return defaults


def _contains_rank_var(types: tuple[T.Type, ...]) -> bool:
    return any(_type_contains_rank_var(typ) for typ in types)


def _type_contains_rank_var(typ: T.Type) -> bool:
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
    if isinstance(typ, T.TaggedType):
        return _type_contains_rank_var(typ.inner)
    return False


def _contains_type_var(typ: T.Type) -> bool:
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
        return _contains_type_var_in_stack(typ.params) or _contains_type_var_in_stack(
            typ.returns
        )
    if isinstance(typ, (T.TaggedType, T.ExactType, T.AtomicType)):
        return _contains_type_var(typ.inner)
    return False


def _contains_type_var_in_stack(types: tuple[T.Type, ...] | None) -> bool:
    return types is not None and any(_contains_type_var(typ) for typ in types)


def _static_body_variable_names(node: FunctionNode) -> tuple[Symbol, ...]:
    names: set[Symbol] = set()
    for typ in (*_declared_params(node), *(node.returns or ())):
        names.update(Symbol(name) for name in _rank_var_names_in_type(typ))
    for where_node in node.where_clause:
        if isinstance(where_node, SetVariableNode):
            names.add(where_node.name)
    return tuple(sorted(names))


def _rank_var_names_in_type(typ: T.Type) -> set[str]:
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
    elif isinstance(typ, T.TaggedType):
        names.update(_rank_var_names_in_type(typ.inner))
    return names


def _params_to_types(params: tuple[FunctionParam, ...]) -> tuple[T.Type, ...]:
    return tuple(_param_type(param, index) for index, param in enumerate(params))


def _function_input_mode(node: FunctionNode) -> InputMode:
    if node.params is None:
        return InputMode.INFER_INPUTS
    if not node.params:
        return InputMode.NILADIC
    return InputMode.CYCLE_EXPLICIT_PARAMS


def _function_capture_source(outer: AnalysisBranch) -> BranchVariables | None:
    if outer.input_mode is InputMode.TOP_LEVEL:
        return None
    return outer.variables


def _top_level_assignment_capture_nodes(
    outer: AnalysisBranch,
    node: FunctionNode,
) -> tuple[GetVariableNode, ...]:
    if outer.input_mode is not InputMode.TOP_LEVEL:
        return ()
    visible = {name for name, _typ in outer.variables.visible_items()}
    if not visible:
        return ()
    return _top_level_assignment_capture_reads_in_function(node, visible, frozenset())


def _top_level_assignment_capture_reads_in_function(
    node: FunctionNode,
    visible: set[Symbol],
    inherited_bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
    bound = inherited_bound | _function_bound_variable_names(node)
    return _top_level_assignment_capture_reads_in_nodes(node.body, visible, bound)


def _top_level_assignment_capture_reads_in_nodes(
    nodes: tuple[ASTNode, ...],
    visible: set[Symbol],
    bound: frozenset[Symbol],
) -> tuple[GetVariableNode, ...]:
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
    names = {
        param.name
        for param in node.params or ()
        if param.name is not None
    }
    names.update(
        assigned.name
        for assigned in node.body
        if isinstance(assigned, SetVariableNode)
    )
    for assigned in node.body:
        if isinstance(assigned, SetVariablesNode):
            names.update(target.name for target in assigned.targets)
    return frozenset(names)


def _function_analysis_from_signatures(
    signatures: dict[T.Overload, tuple[TypedNode, ...]],
) -> FunctionAnalysis | None:
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
    typ = T.normalize(typ)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return ()
        return (T.Overload(typ.params, typ.returns, element_tags=typ.element_tags),)
    if isinstance(typ, T.OverloadSetType):
        return typ.overloads
    return ()


def _typed_body_element_tags(body: tuple[TypedNode, ...]) -> tuple[T.ElementTag, ...]:
    tags: set[T.ElementTag] = set()
    for node in body:
        if isinstance(node, TypedElementNode) and node.overload is not None:
            tags.update(tag for tag in node.overload.element_tags if not tag.absent)
        elif isinstance(node, TypedCallNode) and node.overload is not None:
            tags.update(tag for tag in node.overload.element_tags if not tag.absent)
        if isinstance(node, TypedFunctionNode):
            continue
    return tuple(sorted(tags))


def _best_candidates(
    candidates: Iterable[tuple[Any, ...]],
    original: AnalysisBranch | None = None,
) -> tuple[tuple[Any, ...], ...]:
    ordered = list(candidates)
    winners: list[tuple[Any, ...]] = []
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
    left: tuple[Any, ...],
    right: tuple[Any, ...],
) -> bool:
    left_applied = left[0]
    right_applied = right[0]
    if _dominates(left_applied.scores, right_applied.scores):
        return True
    if left_applied.scores != right_applied.scores:
        return False
    return _params_more_specific(left_applied.params, right_applied.params)


def _params_more_specific(
    left: tuple[T.Type, ...],
    right: tuple[T.Type, ...],
) -> bool:
    return all(
        _type_more_specific_or_same(left_item, right_item)
        for left_item, right_item in zip(left, right, strict=False)
    ) and any(
        not _type_more_specific_or_same(right_item, left_item)
        for left_item, right_item in zip(left, right, strict=False)
    )


def _type_more_specific_or_same(left: T.Type, right: T.Type) -> bool:
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
    left: tuple[Any, ...],
    right: tuple[Any, ...],
    original: AnalysisBranch | None,
) -> bool:
    if original is None:
        return False
    left_key = _inferred_specialization_key(left[1], original)
    right_key = _inferred_specialization_key(right[1], original)
    return left_key is not None and right_key is not None and left_key != right_key


def _inferred_specialization_key(
    branch: AnalysisBranch,
    original: AnalysisBranch,
) -> tuple[object, ...] | None:
    if branch.inputs != original.inputs:
        return ("inputs", branch.inputs)
    if branch.cycle_params != original.cycle_params:
        return ("cycle_params", branch.cycle_params)
    if branch.variables != original.variables:
        return ("variables", branch.variables)
    return None


def _winners_specialize_inputs(
    winners: tuple[tuple[Any, ...], ...],
    original: AnalysisBranch,
) -> bool:
    return all(candidate[1].inputs != original.inputs for candidate in winners)


def _overload_index(
    overloads: tuple[T.Overload, ...],
    overload: T.Overload,
) -> int | None:
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
) -> Iterator[
    tuple[
        tuple[T.Type, ...],
        AnalysisBranch,
        tuple[ModifierArgumentAnalysis, ...],
    ]
]:
    if not modifier_args:
        params = _call_args_in_current_order(overload.params, call_arg_order)
        sourced = branch.source_arguments(params)
        if sourced is not None:
            current_args, popped = sourced
            args = _call_args_in_parameter_order(current_args, call_arg_order)
            yield args, popped, ()
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


def _call_args_in_current_order(
    items: tuple[T.Type, ...],
    call_arg_order: tuple[int, ...],
) -> tuple[T.Type, ...]:
    if not call_arg_order:
        return items
    return tuple(items[index] for index in _invert_call_arg_order(call_arg_order))


def _call_args_in_parameter_order(
    items: tuple[T.Type, ...],
    call_arg_order: tuple[int, ...],
) -> tuple[T.Type, ...]:
    if not call_arg_order:
        return items
    return tuple(items[index] for index in call_arg_order)


def _invert_call_arg_order(call_arg_order: tuple[int, ...]) -> tuple[int, ...]:
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
) -> list[tuple[T.AppliedOverload, AnalysisBranch, tuple[int, ...], int]]:
    candidates: list[tuple[T.AppliedOverload, AnalysisBranch, tuple[int, ...], int]] = (
        []
    )
    if disambiguation and len(disambiguation) != len(explicit_args):
        return candidates
    for callable_index, callable_overload in enumerate(
        _callable_overloads(function_type)
    ):
        callable_application = T.apply_overload(
            callable_overload,
            explicit_args,
            ctx,
            disambiguation=disambiguation,
        )
        if callable_application is None:
            continue
        concrete_function_type = T.Fn(
            callable_application.params,
            callable_application.actual_returns,
            callable_overload.element_tags,
        )
        concrete_args = (*explicit_args, concrete_function_type)
        concrete_overload = T.Overload(
            concrete_args,
            callable_application.actual_returns,
            call_site_body=len(explicit_args),
        )
        concrete_application = T.apply_overload(
            concrete_overload,
            concrete_args,
            ctx,
        )
        if concrete_application is None:
            continue
        actual_returns = _apply_data_tag_flow(
            explicit_args,
            callable_overload.returns,
            callable_application.actual_returns,
            ctx,
        )
        candidates.append(
            (
                T.AppliedOverload(
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
                    ),
                ),
                branch.with_stack(T.TypeStack(base_stack)),
                call_arg_order,
                callable_index,
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
    plan = _element_call_argument_plan(overload, call_args, has_modifier_args)
    if plan is None:
        return ()
    current = BranchSet.one(branch)
    expressions, call_arg_order = plan
    for expression in expressions:
        current = current.extend_block(expression, analyser)
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
            if param_defaults[index] is None:
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
) -> Iterator[tuple[dict[str, T.Type], tuple[ModifierArgumentAnalysis, ...]]]:
    if not modifier_indexes:
        yield substitution, ()
        return

    def rec(
        position: int,
        current_substitution: dict[str, T.Type],
        current_modifiers: tuple[ModifierArgumentAnalysis, ...],
    ) -> Iterator[tuple[dict[str, T.Type], tuple[ModifierArgumentAnalysis, ...]]]:
        if position == len(modifier_indexes):
            yield current_substitution, current_modifiers
            return
        param_index = modifier_indexes[position]
        expected = _substitute_branch_type(params[param_index], current_substitution)
        for modifier, modifier_substitution in _modifier_variants_for_expected(
            modifiers[position],
            expected,
            ctx,
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
) -> Iterator[tuple[ModifierArgumentAnalysis, dict[str, T.Type]]]:
    expected = T.normalize(expected)
    if not isinstance(expected, T.FunctionType) or _is_bare_function_type(expected):
        if T.compatible(modifier.typ, expected, ctx):
            yield modifier, {}
        return

    for overload in modifier.typed_node.overloads:
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


def _merge_substitutions(
    left: dict[str, T.Type],
    right: dict[str, T.Type],
) -> dict[str, T.Type] | None:
    merged = dict(left)
    for name, typ in right.items():
        existing = merged.get(name)
        if existing is not None and not T.same(existing, typ):
            return None
        merged[name] = typ
    return merged


def _modifier_arity_matches(
    overloads: tuple[T.Overload, ...],
    modifier_args: tuple[ModifierArgumentAnalysis, ...],
) -> bool:
    return len(modifier_args) in {
        len(_modifier_param_indexes(overload.params)) for overload in overloads
    }


def _specialize_modifier_arguments(
    applied: T.AppliedOverload,
    modifier_args: tuple[ModifierArgumentAnalysis, ...],
    ctx: T.Context,
) -> tuple[TypedFunctionNode, ...]:
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


def _function_overload_matches_type(
    overload: FunctionOverloadTyping,
    expected: T.FunctionType,
    ctx: T.Context,
) -> bool:
    typ = T.normalize(overload.typ)
    return isinstance(typ, T.FunctionType) and (
        T.same(typ, expected) or T.compatible(typ, expected, ctx)
    )


def _show_modifier_counts(overloads: tuple[T.Overload, ...]) -> str:
    counts = sorted(
        {len(_modifier_param_indexes(overload.params)) for overload in overloads}
    )
    if len(counts) == 1:
        return str(counts[0])
    return " or ".join(str(count) for count in counts)


def _modifier_param_indexes(params: tuple[T.Type, ...]) -> tuple[int, ...]:
    return tuple(
        index for index, param in enumerate(params) if _is_callable_parameter(param)
    )


def _is_callable_parameter(param: T.Type) -> bool:
    param = T.normalize(param)
    return isinstance(param, T.FunctionType) or _is_bare_function_type(param)


def _unique_permutations(
    modifier_args: tuple[ModifierArgumentAnalysis, ...],
) -> Iterator[tuple[ModifierArgumentAnalysis, ...]]:
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
) -> tuple[T.AppliedOverload, AnalysisBranch] | None:
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
    applied = T.apply_overload(
        overload,
        specialized_args,
        ctx,
        disambiguation=disambiguation,
    )
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
        element_tags=_propagated_element_tags(overload, specialized_args),
    )
    return applied, specialized_branch


def _apply_tag_overlay(
    element: Symbol,
    args: tuple[T.Type, ...],
    applied: T.AppliedOverload,
    ctx: T.Context,
    env: T.Environment,
) -> T.AppliedOverload:
    matches: list[T.AppliedOverload] = []
    for overlay in env.overlays_for(element):
        candidate = T.apply_overload(overlay.overload, args, ctx)
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
) -> tuple[T.AppliedOverload, AnalysisBranch] | None:
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
        if consumed_count is None or consumed_count > len(branch.stack):
            continue
        concrete_stack_count = len(concrete.params) - len(args)
        if concrete_stack_count < 0 or concrete_stack_count > len(branch.stack):
            continue
        concrete_stack_args = (
            branch.stack.items[-concrete_stack_count:] if concrete_stack_count else ()
        )
        concrete_args = concrete_stack_args + args
        if len(concrete.params) != len(concrete_args):
            continue
        rank_values = _initial_rank_values(concrete.params, concrete_args)
        rank_values = _evaluate_where_clause(concrete, concrete_args, rank_values)
        if rank_values is None:
            continue
        concrete = _substitute_overload_ranks(concrete, rank_values)
        candidate = T.apply_overload(concrete, concrete_args, ctx)
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
            element_tags=_propagated_element_tags(concrete, concrete_args),
        )
        return applied, branch.with_stack(branch.stack.pop(consumed_count))
    return None


def _overload_needs_call_site_checking(overload: T.Overload) -> bool:
    return any(_is_call_site_checked_param(param) for param in overload.params)


def _call_site_explicit_args_match(
    params: tuple[T.Type, ...],
    args: tuple[T.Type, ...],
    ctx: T.Context,
) -> bool:
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
    if callable(overload.call_site_body):
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
        call_params,
        overload.returns,
        overload.generic_constraints,
        overload.where_clause,
        (None,) * (len(call_params) - len(overload.params)) + overload.param_names,
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
    consumed = (
        concrete.call_site_body
        if isinstance(concrete.call_site_body, int)
        else len(concrete.params) - len(overload.params)
    )
    if consumed < 0 or consumed > extra_count:
        return None
    return consumed


def _propagated_element_tags(
    overload: T.Overload,
    args: tuple[T.Type, ...],
) -> frozenset[T.ElementTag]:
    tags = {tag for tag in overload.element_tags if not tag.absent}
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
    values: dict[str, int] = {}
    for param, arg in zip(params, args, strict=False):
        _collect_rank_values(param, arg, values)
    return values


def _collect_rank_values(
    pattern: T.Type,
    actual: T.Type,
    values: dict[str, int],
) -> None:
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
    elif isinstance(pattern, T.TaggedType):
        _collect_rank_values(pattern.inner, actual, values)
    elif isinstance(actual, T.TaggedType):
        _collect_rank_values(pattern, actual.inner, values)


def _match_variadic_tuple_types(
    pattern: T.VariadicTupleType,
    actual: T.TupleType,
    match: Callable[[T.Type, T.Type], bool],
) -> bool:
    def rec(pattern_index: int, actual_index: int) -> bool:
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
    def pop_truthy_values(count: int) -> tuple[int | bool, ...] | None:
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
    return T.Overload(
        tuple(_substitute_rank_values(param, ranks) for param in overload.params),
        tuple(_substitute_rank_values(ret, ranks) for ret in overload.returns),
        overload.generic_constraints,
        overload.where_clause,
        overload.param_names,
        overload.call_site_body,
        frozenset(
            _substitute_rank_values_in_element_tags(overload.element_tags, ranks)
        ),
        overload.annotation_error,
        overload.annotation_warning,
        overload.param_defaults,
    )


def _substitute_rank_values(typ: T.Type, ranks: dict[str, int]) -> T.Type:
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
    return typ


def _substitute_rank_values_in_element_tags(
    tags: frozenset[T.ElementTag],
    ranks: dict[str, int],
) -> tuple[T.ElementTag, ...]:
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
    for name, typ in substitution.items():
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
    return _with_data_tags(typ, (T.DataTag(tag, depth),), ctx)


def _with_data_tags(
    typ: T.Type,
    tags: Iterable[T.DataTag],
    ctx: T.Context,
) -> T.Type:
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
    prefix = "#!" if tag.absent else "#"
    depth = "+" * tag.depth
    return f"{prefix}{tag.name}{depth}"


def _validator_overload_ok(overload: T.Overload, ctx: T.Context) -> bool:
    return len(overload.returns) == 1 and T.assignable(
        overload.returns[0],
        T.WithTag(T.Number, "boolean"),
        ctx,
    )


def _static_validator_result(body: tuple[TypedNode, ...]) -> bool | None:
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
    substitution = _branch_pair_substitution(branch.inputs, refined.inputs)
    if substitution is None:
        return branch
    return _specialize_branch_arguments(branch, substitution)


def _branch_pair_substitution(
    source: tuple[T.Type, ...],
    target: tuple[T.Type, ...],
) -> dict[str, T.Type] | None:
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
    substitution: dict[str, T.Type] = {}
    for arg, param in zip(args, params, strict=True):
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
    solved = T._solve(param, arg, ctx)
    if solved is None:
        return None
    substitution: dict[str, T.Type] = {}
    for name, values in solved.items():
        combined = T._combine_all(values)
        if combined is None:
            return None
        substitution[name] = combined
    return substitution


def _solve_branch_argument(
    arg: T.Type,
    param: T.Type,
    ctx: T.Context,
) -> dict[str, T.Type] | None:
    constraints: dict[str, T.Type] = {}

    def bind(name: str, typ: T.Type) -> bool:
        previous = constraints.get(name)
        if previous is None:
            constraints[name] = typ
            return True
        return T.same(previous, typ)

    def rec(actual: T.Type, expected: T.Type) -> bool:
        actual = T.normalize(actual)
        expected = T.normalize(expected)
        if isinstance(actual, T.VarType):
            return bind(actual.name, expected)
        if isinstance(expected, T.VarType):
            return bind(expected.name, actual)
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
        return T.Atomic(_substitute_branch_type(typ.inner, substitution))
    return typ


def _substitute_branch_element_tags(
    tags: frozenset[T.ElementTag],
    substitution: dict[str, T.Type],
) -> tuple[T.ElementTag, ...]:
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
    if len(returns) == 1:
        return returns[0]
    return None


def _unfold_emitted_type(
    state_types: tuple[T.Type, ...],
    returns: tuple[T.Type, ...],
) -> T.Type:
    if len(returns) <= len(state_types):
        missing = len(state_types) - len(returns)
        next_state = state_types[-missing:] + returns if missing else returns
        return next_state[-1]
    return _optional_present_type(returns[-1])


def _optional_present_type(typ: T.Type) -> T.Type:
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
    expected = _selector_expected_types(receiver_type, selectors)
    return len(expected) == len(index_types) and all(
        T.assignable(actual, target, ctx)
        for actual, target in zip(index_types, expected, strict=True)
    )


def _selector_expected_types(
    receiver_type: T.Type,
    selectors: tuple[IndexSelector, ...],
) -> tuple[T.Type, ...]:
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
    if len(selectors) != 1 or selectors[0].is_slice:
        item_type = _indexed_type(receiver_type, selectors, spread=False)
        return receiver_type if T.assignable(value_type, item_type, ctx) else None
    return _single_index_assignment_type(receiver_type, value_type, ctx)


def _single_index_assignment_type(
    receiver_type: T.Type,
    value_type: T.Type,
    ctx: T.Context,
) -> T.Type | None:
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
    return T.Integer


def _single_index_type(typ: T.Type) -> T.Type:
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
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        return typ.name
    return None


def _closed_match_members(
    env: T.Environment,
    name: Symbol,
) -> tuple[Symbol, ...] | None:
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
    for pattern in patterns:
        yield from _match_pattern_types(pattern)


def _match_pattern_types(pattern: MatchPatternNode) -> Iterator[T.Type]:
    if isinstance(pattern, TypePatternNode) and pattern.typ is not None:
        yield pattern.typ
    if isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            yield from _match_pattern_types(option)


def _match_arity(node: MatchNode) -> int | None:
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
    return bool(patterns) and all(
        _is_default_match_pattern(pattern) for pattern in patterns
    )


def _is_default_match_pattern(pattern: MatchPatternNode) -> bool:
    return isinstance(pattern, (WildcardPatternNode, RestPatternNode)) or (
        isinstance(pattern, TypePatternNode) and pattern.typ is None
    )


def _match_case_variables(
    variables: BranchVariables,
    patterns: tuple[MatchPatternNode, ...],
    subject_types: tuple[T.Type, ...] = (),
) -> BranchVariables:
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
    updated, _diagnostic = variables.write(name, typ, block_local=True)
    return variables if updated is None else updated


def _pattern_binding_type(pattern: MatchPatternNode, name: Symbol) -> T.Type:
    if isinstance(pattern, RestPatternNode):
        return T.C(T.ListExactType, T.V(f"_matched_{name}"))
    if isinstance(pattern, TypePatternNode) and pattern.typ is not None:
        return pattern.typ
    return T.V(f"_matched_{name}")


def _match_pattern_guards(
    patterns: tuple[MatchPatternNode, ...],
    subject_types: tuple[T.Type, ...],
) -> Iterator[tuple[tuple[ASTNode, ...], T.Type]]:
    for pattern, subject_type in zip(patterns, subject_types, strict=True):
        yield from _pattern_guards(pattern, subject_type)


def _pattern_guards(
    pattern: MatchPatternNode,
    subject_type: T.Type,
) -> Iterator[tuple[tuple[ASTNode, ...], T.Type]]:
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
    if not stack:
        return "[]"
    return "[" + ", ".join(T.show(item) for item in stack.items) + "]"


def _diagnostic_message(message: str, node: ASTNode | None) -> str:
    if node is None or node.location is None:
        return message
    location = node.location
    return f"{location.line}:{location.column}: {message}"


def _show_overloads(overloads: Iterable[T.Overload]) -> str:
    rendered = tuple(
        T.show(T.Fn(overload.params, overload.returns)) for overload in overloads
    )
    if not rendered:
        return "none"
    return "; ".join(rendered)


def _show_applied_overloads(
    candidates: Iterable[tuple[Any, ...]],
) -> str:
    rendered = tuple(
        T.show(T.Fn(candidate[0].params, candidate[0].actual_returns))
        for candidate in candidates
    )
    if not rendered:
        return "none"
    return "; ".join(rendered)


def _top_or_none(stack: T.TypeStack) -> T.Type:
    if stack:
        return stack[-1]
    return T.NoneType()


def _loop_break_result_type(break_types: tuple[T.Type, ...]) -> T.Type:
    if not break_types:
        return T.NoneType()
    if len(break_types) == 1:
        return T.optional(break_types[0])
    return T.optional(T.U(*break_types))


def _list_item_analysis(
    base: AnalysisBranch,
    output: AnalysisBranch,
) -> ListItemAnalysis | None:
    if output.break_type is not None or not output.stack:
        return None
    return ListItemAnalysis(
        branch=output,
        typ=output.stack[-1],
        consumed=_forked_stack_consumption(base.stack, output.stack.pop()),
    )


def _forked_stack_consumption(base: T.TypeStack, item_remainder: T.TypeStack) -> int:
    prefix = 0
    limit = min(len(base), len(item_remainder))
    while prefix < limit and T.same(base[prefix], item_remainder[prefix]):
        prefix += 1
    return len(base) - prefix


def _cartesian_product(
    options: tuple[tuple[ListItemAnalysis, ...], ...],
) -> Iterator[tuple[ListItemAnalysis, ...]]:
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
    merged = before
    for item in items:
        merged = merged.merge_against(item.branch.variables, before)
    return merged


def _pop_stack(stack: T.TypeStack, count: int) -> T.TypeStack:
    if count == 0:
        return stack
    return stack.pop(count)


def _merge_loop_variables(
    before: BranchVariables,
    outputs: BranchSet,
    loop_locals: tuple[Symbol, ...],
) -> BranchVariables:
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
    return any(isinstance(T.normalize(ret), T.NeverType) for ret in overload.returns)


def _is_never(t: T.Type) -> bool:
    return isinstance(T.normalize(t), T.NeverType)


def _param_type(param: FunctionParam, index: int) -> T.Type:
    if param.typ is not None:
        return param.typ
    name = param.name.text if param.name is not None else f"_{index}"
    return T.V(name)


def _trait_requirement(node: TraitRequirementNode) -> T.TraitRequirement | None:
    params = tuple(
        _param_type(param, index) for index, param in enumerate(node.params or ())
    )
    returns = node.returns or ()
    return T.TraitRequirement(
        node.name,
        T.Overload(
            params,
            returns,
            param_names=tuple(param.name for param in node.params or ()),
        ),
    )


def _declared_nominal(name: Symbol, generics: tuple[Symbol, ...]) -> T.Type:
    return T.N(name, *(T.V(generic.text) for generic in generics))


def _types_overlap(source: T.Type, target: T.Type, ctx: T.Context) -> bool:
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
    reason = _noncopyable_reason(typ, env)
    if reason is None:
        return None
    return f"cannot copy value of type {T.show(typ)}: {reason}"


def _noncopyable_reason(typ: T.Type, env: T.Environment) -> str | None:
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
    overloads = env.overloads_for(Symbol(f"{name}::dup"))
    for overload in overloads:
        if overload.annotation_error is not None:
            return overload.annotation_error
    return None


def _number_literal_type(value: str) -> T.Type:
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
    constraints: tuple[T.Type | None, ...],
) -> tuple[T.GenericConstraint, ...]:
    if len(generics) != len(constraints):
        return ()
    return tuple(
        T.GenericConstraint(generic.text, _genericize_type(bound, generics))
        for generic, bound in zip(generics, constraints, strict=True)
        if bound is not None
    )


def _with_generic_constraints(
    overload: T.Overload,
    constraints: tuple[T.GenericConstraint, ...],
) -> T.Overload:
    if not constraints:
        return overload
    return T.Overload(
        overload.params,
        overload.returns,
        overload.generic_constraints + constraints,
        overload.where_clause,
        overload.param_names,
        overload.call_site_body,
        overload.element_tags,
        overload.annotation_error,
        overload.annotation_warning,
        overload.param_defaults,
        overload.is_multi,
    )


def _has_multimethod_fallback(
    overload: T.Overload,
    candidates: tuple[T.Overload, ...],
    ctx: T.Context,
) -> bool:
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
    if applied.overload.is_multi:
        return applied
    if not _has_runtime_multimethod_candidate(applied.overload, overloads, ctx):
        return applied
    return replace(applied, multidispatch=True)


def _has_runtime_multimethod_candidate(
    fallback: T.Overload,
    overloads: tuple[T.Overload, ...],
    ctx: T.Context,
) -> bool:
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
    return all(
        T.assignable(specific_param, fallback_param, ctx)
        for specific_param, fallback_param in zip(specific, fallback, strict=True)
    )


def _same_returns(
    left: tuple[T.Type, ...],
    right: tuple[T.Type, ...],
) -> bool:
    return len(left) == len(right) and all(
        T.same(left_item, right_item)
        for left_item, right_item in zip(left, right, strict=True)
    )


def _genericize_overload(
    overload: T.Overload,
    generics: tuple[Symbol, ...],
) -> T.Overload:
    if not generics:
        return overload
    return T.Overload(
        tuple(_genericize_type(param, generics) for param in overload.params),
        tuple(_genericize_type(ret, generics) for ret in overload.returns),
        overload.generic_constraints,
        overload.where_clause,
        overload.param_names,
        overload.call_site_body,
        overload.element_tags,
        overload.annotation_error,
        overload.annotation_warning,
        overload.param_defaults,
        overload.is_multi,
    )


def _genericize_function_node(
    function: FunctionNode,
    generics: tuple[Symbol, ...],
) -> FunctionNode:
    if not generics:
        return function
    params = None
    if function.params is not None:
        params = tuple(
            FunctionParam(
                param.name,
                None if param.typ is None else _genericize_type(param.typ, generics),
                tuple(
                    cast(ASTNode, _genericize_ast_node(node, generics))
                    for node in param.default
                ),
            )
            for param in function.params
        )
    returns = None
    if function.returns is not None:
        returns = tuple(_genericize_type(ret, generics) for ret in function.returns)
    return FunctionNode(
        generics=function.generics,
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
        location=function.location,
    )


def _genericize_ast_node(node: ASTNode, generics: tuple[Symbol, ...]) -> ASTNode:
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
    return T.TraitRequirement(
        requirement.name,
        T.Overload(
            tuple(
                _genericize_type(param, generics)
                for param in requirement.overload.params
            ),
            tuple(
                _genericize_type(ret, generics) for ret in requirement.overload.returns
            ),
            requirement.overload.generic_constraints,
            requirement.overload.where_clause,
            requirement.overload.param_names,
            requirement.overload.call_site_body,
            requirement.overload.element_tags,
            requirement.overload.annotation_error,
            requirement.overload.annotation_warning,
            requirement.overload.param_defaults,
        ),
    )


def _genericize_type(typ: T.Type, generics: tuple[Symbol, ...]) -> T.Type:
    names = {generic.text for generic in generics}
    typ = T.normalize(typ)
    if isinstance(typ, T.NominalType):
        if not typ.args and typ.name.text in names:
            return T.V(typ.name.text)
        return T.N(
            typ.name,
            *(_genericize_type(arg, generics) for arg in typ.args),
        )
    if isinstance(typ, T.UnionType):
        return T.U(*(_genericize_type(item, generics) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_genericize_type(item, generics) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(_genericize_type(item, generics) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(_genericize_type(item.typ, generics), item.repeated)
                for item in typ.items
            )
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            _genericize_type(typ.base, generics),
            *(
                T.Field(field.name, _genericize_type(field.typ, generics))
                for field in typ.fields
            ),
        )
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), _genericize_type(typ.base, generics), typ.rank)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return T.Fn(
                None,
                None,
                _genericize_element_tags(typ.element_tags, generics),
            )
        return T.Fn(
            (_genericize_type(param, generics) for param in typ.params),
            (_genericize_type(ret, generics) for ret in typ.returns),
            _genericize_element_tags(typ.element_tags, generics),
        )
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
    if isinstance(typ, T.TaggedType):
        return T.Tagged(_genericize_type(typ.inner, generics), *typ.tags)
    if isinstance(typ, T.ExactType):
        return T.Exact(_genericize_type(typ.inner, generics))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_genericize_type(typ.inner, generics))
    return typ


def _anonymous_trait_overloads(*types: T.Type) -> tuple[tuple[Symbol, T.Overload], ...]:
    overloads: list[tuple[Symbol, T.Overload]] = []
    for typ in types:
        _collect_anonymous_trait_overloads(T.normalize(typ), overloads)
    return tuple(overloads)


def _anonymous_trait_subject_view(typ: T.Type) -> T.Type:
    typ = T.normalize(typ)
    if isinstance(typ, T.AnonymousTraitType) and typ.generics:
        return T.V(typ.generics[0].text)
    if isinstance(typ, T.NominalType):
        return T.N(typ.name, *(_anonymous_trait_subject_view(arg) for arg in typ.args))
    if isinstance(typ, T.UnionType):
        return T.U(*(_anonymous_trait_subject_view(item) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_anonymous_trait_subject_view(item) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(_anonymous_trait_subject_view(item) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(_anonymous_trait_subject_view(item.typ), item.repeated)
                for item in typ.items
            )
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            _anonymous_trait_subject_view(typ.base),
            *(
                T.Field(field.name, _anonymous_trait_subject_view(field.typ))
                for field in typ.fields
            ),
        )
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), _anonymous_trait_subject_view(typ.base), typ.rank)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return typ
        return T.Fn(
            (_anonymous_trait_subject_view(param) for param in typ.params),
            (_anonymous_trait_subject_view(ret) for ret in typ.returns),
            typ.element_tags,
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(_anonymous_trait_subject_view(typ.inner), *typ.tags)
    if isinstance(typ, T.ExactType):
        return T.Exact(_anonymous_trait_subject_view(typ.inner))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_anonymous_trait_subject_view(typ.inner))
    return typ


def _contains_anonymous_trait(typ: T.Type) -> bool:
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
    if isinstance(typ, T.AnonymousTraitType):
        overloads.extend(
            (requirement.name, requirement.overload)
            for requirement in typ.requirements
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
    inferred = _infer_generic_variance(generics, attributes, requirements)
    if len(explicit) != len(generics):
        return inferred
    return tuple(
        _variance_from_marker(marker) if marker is not None else inferred[index]
        for index, marker in enumerate(explicit)
    )


def _variance_from_marker(marker: Symbol) -> T.Variance:
    if marker.text == "covariant":
        return T.Variance.COVARIANT
    if marker.text == "contravariant":
        return T.Variance.CONTRAVARIANT
    return T.Variance.INVARIANT


def _infer_generic_variance(
    generics: tuple[Symbol, ...],
    attributes: tuple[T.ObjectAttribute, ...],
    requirements: tuple[T.TraitRequirement, ...],
) -> tuple[T.Variance, ...]:
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
    taken = _anonymous_type_indices(
        *branch.stack.items,
        *branch.inputs,
        *branch.cycle_params,
        *(typ for _, typ in branch.variables.visible_items()),
    )
    start = max(taken, default=0)
    return T.V(f"@{start + offset}")


def _anonymous_type_indices(*types: T.Type) -> set[int]:
    indices: set[int] = set()
    for typ in types:
        _collect_anonymous_type_indices(T.normalize(typ), indices)
    return indices


def _collect_anonymous_type_indices(typ: T.Type, indices: set[int]) -> None:
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
    for row_field in row.fields:
        if row_field.name == name:
            return row_field.typ
    return None


def _refine_stack(stack: T.TypeStack, old: T.Type, new: T.Type) -> T.TypeStack:
    return T.TypeStack(tuple(_refine_type(item, old, new) for item in stack.items))


def _refine_items(
    items: tuple[tuple[Symbol, T.Type], ...],
    old: T.Type,
    new: T.Type,
) -> tuple[tuple[Symbol, T.Type], ...]:
    return tuple((name, _refine_type(typ, old, new)) for name, typ in items)


def _refine_typed_body(
    typed_body: tuple[TypedNode, ...],
    old: T.Type,
    new: T.Type,
) -> tuple[TypedNode, ...]:
    return tuple(_refine_typed_node(node, old, new) for node in typed_body)


def _refine_typed_node(typed_node: TypedNode, old: T.Type, new: T.Type) -> TypedNode:
    typ = None if typed_node.typ is None else _refine_type(typed_node.typ, old, new)
    if isinstance(typed_node, TypedFunctionNode):
        return TypedFunctionNode(
            typed_node.node,
            typ,
            tuple(
                FunctionOverloadTyping(
                    _refine_type(overload.typ, old, new),
                    _refine_typed_body(overload.body, old, new),
                )
                for overload in typed_node.overloads
            ),
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
        )
    if isinstance(typed_node, TypedCallNode):
        return TypedCallNode(
            typed_node.node,
            typ,
            typed_node.overload,
        )
    if isinstance(typed_node, TypedUnfoldNode):
        return TypedUnfoldNode(
            typed_node.node,
            typ,
            typed_node.state_arity,
        )
    return TypedNode(typed_node.node, typ)


def _refine_type(typ: T.Type, old: T.Type, new: T.Type) -> T.Type:
    typ = T.normalize(typ)
    new = _erase_absent_tag_requirements(new)
    if T.same(typ, old):
        return new
    if isinstance(typ, T.NominalType):
        return T.N(typ.name, *(_refine_type(arg, old, new) for arg in typ.args))
    if isinstance(typ, T.UnionType):
        return T.U(*(_refine_type(item, old, new) for item in typ.items))
    if isinstance(typ, T.IntersectionType):
        return T.I(*(_refine_type(item, old, new) for item in typ.items))
    if isinstance(typ, T.TupleType):
        return T.Tup(*(_refine_type(item, old, new) for item in typ.params))
    if isinstance(typ, T.VariadicTupleType):
        return T.TupVariadic(
            *(
                T.TupleTypeItem(_refine_type(item.typ, old, new), item.repeated)
                for item in typ.items
            )
        )
    if isinstance(typ, T.RowType):
        return T.Row(
            _refine_type(typ.base, old, new),
            *(
                T.Field(row_field.name, _refine_type(row_field.typ, old, new))
                for row_field in typ.fields
            ),
        )
    if isinstance(typ, T.CollectionType):
        return T.C(type(typ), _refine_type(typ.base, old, new), typ.rank)
    if isinstance(typ, T.FunctionType):
        if typ.params is None or typ.returns is None:
            return typ
        return T.Fn(
            (_refine_type(item, old, new) for item in typ.params),
            (_refine_type(item, old, new) for item in typ.returns),
        )
    if isinstance(typ, T.AnonymousTraitType):
        return T.AnonymousTrait(
            typ.generics,
            (
                T.AnonymousTraitRequirement(
                    requirement.name,
                    T.Overload(
                        tuple(
                            _refine_type(param, old, new)
                            for param in requirement.overload.params
                        ),
                        tuple(
                            _refine_type(ret, old, new)
                            for ret in requirement.overload.returns
                        ),
                        requirement.overload.generic_constraints,
                        requirement.overload.where_clause,
                        requirement.overload.param_names,
                        requirement.overload.call_site_body,
                        requirement.overload.element_tags,
                        requirement.overload.annotation_error,
                        requirement.overload.annotation_warning,
                        requirement.overload.param_defaults,
                        requirement.overload.is_multi,
                    ),
                )
                for requirement in typ.requirements
            ),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(_refine_type(typ.inner, old, new), *typ.tags)
    if isinstance(typ, T.ExactType):
        return T.Exact(_refine_type(typ.inner, old, new))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_refine_type(typ.inner, old, new))
    return typ


def _erase_absent_tag_requirements(typ: T.Type) -> T.Type:
    typ = T.normalize(typ)
    if isinstance(typ, T.TaggedType) and all(tag.absent for tag in typ.tags):
        return typ.inner
    return typ


def _stack_assignable(
    actual: T.TypeStack,
    expected: T.TypeStack,
    ctx: T.Context,
) -> bool:
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
    return actual.items[-len(expected) :] if expected else ()


def _lookup(
    items: tuple[tuple[Symbol, T.Type], ...],
    name: Symbol,
) -> T.Type | None:
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
    if _assignment_stored_type(target, source, ctx) is not None:
        return None
    return (
        f"cannot assign {T.show(source)} to variable '{name}' "
        f"of type {T.show(target)}"
    )


def _assignment_stored_type(
    existing: T.Type,
    source: T.Type,
    ctx: T.Context,
) -> T.Type | None:
    if T.assignable(source, existing, ctx):
        return existing
    if T.assignable(existing, source, ctx):
        return source
    return None


def _mustcall_methods(annotations: tuple[ASTNode, ...]) -> tuple[str, ...]:
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


def _set_item(
    items: tuple[tuple[Symbol, T.Type], ...],
    name: Symbol,
    typ: T.Type,
) -> tuple[tuple[Symbol, T.Type], ...]:
    result = {key: value for key, value in items}
    result[name] = typ
    return _sorted_items(result.items())


def _set_symbol_flag(
    items: tuple[Symbol, ...],
    name: Symbol,
    enabled: bool,
) -> tuple[Symbol, ...]:
    result = set(items)
    if enabled:
        result.add(name)
    else:
        result.discard(name)
    return tuple(sorted(result))


def _sorted_items(
    items: Iterable[tuple[Symbol, T.Type]],
) -> tuple[tuple[Symbol, T.Type], ...]:
    return tuple(sorted(items, key=lambda item: item[0]))
