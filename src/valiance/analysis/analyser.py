from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from itertools import count, permutations
from pathlib import Path
from typing import Any

import valiance.types as T
from valiance.analysis.builtins import default_environment
from valiance.asts import (
    AssertNode,
    ASTNode,
    AtNode,
    BindingPatternNode,
    DefineNode,
    DictLiteralNode,
    ElementNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    GuardPatternNode,
    ImportNode,
    ListLiteralNode,
    ListPatternNode,
    MatchNode,
    MatchPatternNode,
    NumberLiteralNode,
    ObjectNode,
    OrPatternNode,
    RecordLiteralNode,
    RestPatternNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TraitRequirementNode,
    TupleLiteralNode,
    TypedCallNode,
    TypedElementNode,
    TypedFunctionNode,
    TypedNode,
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
    WhileNode,
)
from valiance.modules import ModuleLoader, ModuleLoadError, import_definitions
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
        ctx: T.Context | None = None,
    ) -> tuple[BranchVariables | None, str | None]:
        """Return variables after assigning ``name`` in this branch."""
        ctx = ctx or T.Context()
        existing_block_local = _lookup(self.block_locals, name)
        if existing_block_local is not None:
            diagnostic = _assignment_error(name, typ, existing_block_local, ctx)
            if diagnostic is not None:
                return None, diagnostic
            return self, None
        if _lookup(self.parameters, name) is not None:
            return None, f"cannot assign to read-only parameter '{name}'"
        existing_function_local = _lookup(self.function_locals, name)
        if existing_function_local is not None:
            diagnostic = _assignment_error(name, typ, existing_function_local, ctx)
            if diagnostic is not None:
                return None, diagnostic
            return (
                BranchVariables(
                    function_locals=self.function_locals,
                    parameters=self.parameters,
                    captures=self.captures,
                    block_locals=self.block_locals,
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
                ),
                None,
            )
        return (
            BranchVariables(
                function_locals=_set_item(self.function_locals, name, typ),
                parameters=self.parameters,
                captures=self.captures,
                block_locals=self.block_locals,
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
        )

    def drop_block_locals(self) -> BranchVariables:
        """Return this frame after leaving a block-local scope."""
        return BranchVariables(
            function_locals=self.function_locals,
            parameters=self.parameters,
            captures=self.captures,
        )

    def refine_type(self, old: T.Type, new: T.Type) -> BranchVariables:
        """Replace one type fact wherever it appears in visible branch variables."""
        return BranchVariables(
            function_locals=_refine_items(self.function_locals, old, new),
            parameters=_refine_items(self.parameters, old, new),
            captures=_refine_items(self.captures, old, new),
            block_locals=_refine_items(self.block_locals, old, new),
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
        return BranchVariables(
            function_locals=_sorted_items(locals_by_name.items()),
            parameters=before.parameters,
            captures=before.captures,
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


class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""

    def __init__(
        self,
        env: T.Environment | None = None,
        *,
        module_loader: ModuleLoader | None = None,
        source_file: Path | None = None,
    ):
        self.env = env or default_environment()
        self.module_loader = module_loader or ModuleLoader()
        self.source_file = source_file
        self.diagnostics: list[str] = []
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
            case NumberLiteralNode(_):
                return {
                    branch.with_stack(branch.stack.push(T.Number)).append_typed(
                        TypedNode(node, T.Number)
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
            case FunctionNode():
                result = self._analyse_function_literal(branch, node)
                if result is None:
                    return {branch.append_typed(TypedNode(node, None))}
                function, typed_branch = result
                typed_node = TypedFunctionNode(node, function.typ, function.overloads)
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
            case SetVariableNode(name):
                if not branch.stack:
                    if branch.input_mode is InputMode.INFER_INPUTS:
                        inferred = T.V(f"_inferred_{name}")
                        variables, diagnostic = branch.variables.write(
                            name,
                            inferred,
                            ctx=self.env.context,
                        )
                        if diagnostic is not None:
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
                variables, diagnostic = branch.variables.write(
                    name,
                    value_type,
                    block_local=True,
                    ctx=self.env.context,
                )
                if diagnostic is not None:
                    return {branch.with_diagnostic(diagnostic)}
                if variables is None:
                    return {
                        branch.with_diagnostic(f"cannot assign to variable '{name}'")
                    }
                return {
                    branch.with_variables(variables)
                    .with_stack(branch.stack.pop())
                    .append_typed(TypedNode(node, value_type))
                }
            case FieldAccessNode(name):
                return self._field_access(branch, node, name)
            case FieldSetNode(name):
                return self._field_set(branch, node, name)
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
        result = self._analyse_function_literal(branch, function_node)
        if result is None:
            return {branch.append_typed(TypedNode(node, None))}
        function, typed_branch = result
        for overload in _callable_overloads(function.typ):
            self.env.define_overload(name, overload)
        typed_node = TypedFunctionNode(node, function.typ, function.overloads)
        return {typed_branch.append_typed(typed_node)}

    def _object_declaration(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> set[AnalysisBranch]:
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
        object_attributes = tuple(attribute for attribute in attributes if attribute)
        self.env.define_object(node.name, object_attributes, generics=node.generics)
        self.env.define_constructor(
            node.name,
            object_attributes,
            defaults=frozenset(
                field.name for field in node.fields if field.default
            ),
        )
        current = branch.append_typed(TypedNode(node, None))
        for definition in node.definitions:
            current = self._register_friendly_definition(current, node.name, definition)
        return {current}

    def _trait_definition(
        self,
        branch: AnalysisBranch,
        node: ObjectNode,
    ) -> set[AnalysisBranch]:
        requirements = tuple(
            requirement
            for item in node.requirements
            if (requirement := _trait_requirement(item)) is not None
        )
        self.env.define_trait(
            node.name,
            generics=node.generics,
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
        requirements = tuple(
            requirement
            for item in node.requirements
            if (requirement := _trait_requirement(item)) is not None
        )
        members: list[Symbol] = []
        for member in node.variants:
            member_name = Symbol(f"{node.name}.{member.name}")
            members.append(member_name)
            attributes = tuple(self._object_attribute(field) for field in member.fields)
            object_attributes = tuple(
                attribute for attribute in attributes if attribute
            )
            self.env.define_object(member_name, object_attributes)
            self.env.define_constructor(member_name, object_attributes)
            self.env.define_overload(member.name, T.Overload(
                tuple(attribute.typ for attribute in object_attributes),
                (T.N(node.name),),
            ))
        self.env.define_variant(
            node.name,
            tuple(members),
            generics=node.generics,
            requirements=requirements,
        )
        return {branch.append_typed(TypedNode(node, T.N(node.name)))}

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
        return {branch.append_typed(TypedNode(node, T.N(node.name)))}

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
        self_type = T.N(owner)
        params = (FunctionParam(Symbol("self"), self_type),) + tuple(
            definition.function.params or ()
        )
        function_node = FunctionNode(
            params=params,
            body=definition.function.body,
            returns=definition.function.returns,
            location=definition.function.location,
        )
        self._friendly_owners = self._friendly_owners + (owner,)
        try:
            result = self._analyse_function_literal(branch, function_node)
        finally:
            self._friendly_owners = self._friendly_owners[:-1]
        if result is None:
            return branch.append_typed(TypedNode(definition, None))
        function, typed_branch = result
        for name in (definition.name, Symbol(f"{owner}::{definition.name}")):
            for overload in _callable_overloads(function.typ):
                self.env.define_overload(name, overload)
        return typed_branch

    def _import(
        self,
        branch: AnalysisBranch,
        node: ImportNode,
    ) -> set[AnalysisBranch]:
        typed_nodes: list[TypedFunctionNode] = []
        for spec in node.specs:
            try:
                exports = self.module_loader.load(
                    spec.path,
                    current_file=self.source_file,
                )
                definitions = import_definitions(exports, spec)
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
            )
            .append_typed(TypedNode(node, T.String))
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

        candidates: list[
            tuple[
                T.AppliedOverload,
                AnalysisBranch,
                tuple[ModifierArgumentAnalysis, ...],
            ]
        ] = []
        for overload in overloads:
            for args, popped, ordered_modifiers in _source_element_arguments(
                branch,
                overload,
                modifier_args,
            ):
                candidate = _apply_overload_to_branch(
                    overload,
                    args,
                    popped,
                    self.env.context,
                    self.env,
                )
                if candidate is not None:
                    applied, candidate_branch = candidate
                    candidates.append((applied, candidate_branch, ordered_modifiers))

        stack_before = branch.stack
        winners = _best_candidates(candidates)
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
            results.add(
                popped.with_stack(
                    popped.stack.push(*applied.actual_returns)
                ).append_typed(
                    TypedElementNode(
                        node,
                        _returns_result_type(applied.actual_returns),
                        applied,
                        _overload_index(overloads, applied.overload),
                        tuple(item.typed_node for item in ordered_modifiers),
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

        stack = T.TypeStack((*branch.stack.items[:-1], tagged))
        return {branch.with_stack(stack).append_typed(TypedNode(node, tagged))}

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
                )
                if candidate is not None:
                    candidates.append(candidate)

        winners = _best_candidates(candidates)
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

    def _list_literal(
        self,
        branch: AnalysisBranch,
        node: ListLiteralNode,
    ) -> set[AnalysisBranch]:
        if not node.items:
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
        if not branch.stack:
            self._diagnose("for loop requires iterable on the stack", node)
            return set()
        iterable_type = branch.stack[-1]
        item_type = T.collection_item_type(iterable_type)
        if not item_type:
            self._diagnose(
                "for loop iterable must actually be iterable. "
                f"Got {T.show(iterable_type)}",
                node,
            )
            return set()
        body_branch = branch.with_stack(branch.stack.pop())
        body_branch = body_branch.with_variables(
            body_branch.variables.with_block_local(node.variable, item_type)
        )
        if node.index_variable is not None:
            body_branch = body_branch.with_variables(
                body_branch.variables.with_block_local(node.index_variable, T.Number)
            )

        body_outputs = self.analyse_block(BranchSet.one(body_branch), node.body)
        if not body_outputs:
            return set()
        break_types = tuple(
            output.break_type
            for output in body_outputs
            if output.break_type is not None
        )
        result_type = _loop_break_result_type(break_types)
        variables = _merge_loop_variables(body_branch.variables, body_outputs)
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
            location=node.location,
        )
        body_analysis = self._analyse_function_literal(branch, body_function)
        if body_analysis is None:
            return set()
        body_function_analysis, _ = body_analysis

        results: set[AnalysisBranch] = set()
        for overload in _callable_overloads(body_function_analysis.typ):
            if not overload.returns:
                self._diagnose("unfold body must generate a value", node)
                continue
            if node.condition:
                condition_function = FunctionNode(
                    params=tuple(
                        FunctionParam(param.name, typ)
                        for param, typ in zip(
                            node.params or (),
                            overload.params,
                            strict=False,
                        )
                    )
                    if node.params is not None
                    else tuple(FunctionParam(None, typ) for typ in overload.params),
                    body=node.condition,
                    returns=(Boolean,),
                    location=node.location,
                )
                if self._analyse_function_literal(branch, condition_function) is None:
                    self._diagnose("unfold condition must return a boolean value", node)
                    continue
            sourced = branch.source_arguments(overload.params)
            if sourced is None:
                self._diagnose("unfold inputs do not match stack", node)
                continue
            _, popped = sourced
            generated = overload.returns[-1]
            list_type = T.WithTag(T.ExactList(generated), "infinite")
            results.add(
                popped.with_stack(popped.stack.push(list_type)).append_typed(
                    TypedNode(node, list_type)
                )
            )
        return results

    def _at(
        self,
        branch: AnalysisBranch,
        node: AtNode,
    ) -> set[AnalysisBranch]:
        outputs = self.analyse_block(BranchSet.one(branch), node.body)
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
                base = _refine_branch_like(branch, left)
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
        if not branch.stack:
            self._diagnose("match requires a value on the stack", node)
            return set()
        if not node.cases:
            self._diagnose("match requires at least one case", node)
            return set()

        subject_type = branch.stack[-1]
        body_input = branch.with_stack(branch.stack.pop())
        if not self._match_is_exhaustive(subject_type, node):
            return set()

        joined: AnalysisBranch | None = None
        for case in node.cases:
            case_input = body_input.with_variables(
                _match_case_variables(body_input.variables, case.patterns)
            )
            if not self._match_guards_are_valid(subject_type, case.patterns, node):
                return set()
            case_outputs = self.analyse_block(BranchSet.one(case_input), case.body)
            for output in case_outputs:
                candidate = output
                if candidate.break_type is not None:
                    typ = candidate.break_type
                    if typ is None:
                        typ = _returns_result_type(candidate.stack.items)
                    candidate = candidate.append_typed(TypedNode(node, typ))
                if joined is None:
                    joined = candidate
                    continue
                if joined.inputs != candidate.inputs:
                    self._diagnose("match cases inferred different inputs", node)
                    return set()
                stack = merge_stacks(joined.stack, candidate.stack)
                variables = joined.variables.merge_against(
                    candidate.variables,
                    body_input.variables,
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
        subject_type: T.Type,
        patterns: tuple[MatchPatternNode, ...],
        node: MatchNode,
    ) -> bool:
        guards = tuple(_match_pattern_guards(patterns))
        for guard in guards:
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

    def _match_is_exhaustive(self, subject_type: T.Type, node: MatchNode) -> bool:
        if any(
            case.is_default or _is_default_match_case(case.patterns)
            for case in node.cases
        ):
            return True
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
            if (resolved := _resolve_closed_member(expected, pattern_type))
            is not None
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
            attribute = self.env.lookup_attribute_definition(receiver_type.name, name)
            if attribute is None:
                return None, None
            if not self._can_access_attribute(
                receiver_type.name,
                attribute,
                write=write,
            ):
                return None, None
            return attribute.typ, None

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
        params = _declared_params(node)
        mode = _function_input_mode(node)
        named_params = tuple(
            (param.name, typ)
            for param, typ in zip(node.params or (), params, strict=True)
            if param.name is not None
        )
        variables = BranchVariables.from_parameters(
            named_params,
            captures=outer.variables,
        )
        initial_stack = T.TypeStack(
            params if mode is InputMode.CYCLE_EXPLICIT_PARAMS else ()
        )
        initial = AnalysisBranch(
            stack=initial_stack,
            inputs=params if mode is not InputMode.INFER_INPUTS else (),
            variables=variables,
            input_mode=mode,
            cycle_params=params if mode is InputMode.CYCLE_EXPLICIT_PARAMS else (),
            origin=outer.origin,
        )

        function_analyser = Analyser(self.env)
        function_analyser._friendly_owners = self._friendly_owners
        final = function_analyser.analyse_block(BranchSet.one(initial), node.body)
        signatures = self._function_signatures(node, final)
        analysis = _function_analysis_from_signatures(signatures)
        if analysis is None:
            self.diagnostics.extend(function_analyser.diagnostics)
            return None
        return analysis, outer

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
            signature = T.Overload(branch.inputs, returns)
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
        if substitution is not None:
            branch = _specialize_branch_arguments(branch, substitution)
        if not _stack_assignable(branch.stack, expected, self.env.context):
            return None
        return node.returns, branch

    def _diagnose(self, message: str, node: ASTNode | None = None) -> None:
        self.diagnostics.append(_diagnostic_message(message, node))


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


def _params_to_types(params: tuple[FunctionParam, ...]) -> tuple[T.Type, ...]:
    return tuple(_param_type(param, index) for index, param in enumerate(params))


def _function_input_mode(node: FunctionNode) -> InputMode:
    if node.params is None:
        return InputMode.INFER_INPUTS
    if not node.params:
        return InputMode.NILADIC
    return InputMode.CYCLE_EXPLICIT_PARAMS


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
            T.Fn(signature.params, signature.returns),
            signatures[signature],
        )
        for signature in ordered
    )
    if len(ordered) == 1:
        signature = ordered[0]
        typ = T.Fn(signature.params, signature.returns)
    else:
        typ = T.Overloads(*ordered)
    return FunctionAnalysis(typ, overload_typings)


def _callable_overloads(typ: T.Type) -> tuple[T.Overload, ...]:
    typ = T.normalize(typ)
    if isinstance(typ, T.FunctionType):
        return (T.Overload(typ.params, typ.returns),)
    if isinstance(typ, T.OverloadSetType):
        return typ.overloads
    return ()


def _best_candidates(
    candidates: Iterable[tuple[Any, ...]],
) -> tuple[tuple[Any, ...], ...]:
    ordered = list(candidates)
    winners: list[tuple[Any, ...]] = []
    for candidate in ordered:
        applied = candidate[0]
        if not any(
            other is not candidate and _dominates(other[0].scores, applied.scores)
            for other in ordered
        ):
            winners.append(candidate)
    return tuple(winners)


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
) -> Iterator[
    tuple[
        tuple[T.Type, ...],
        AnalysisBranch,
        tuple[ModifierArgumentAnalysis, ...],
    ]
]:
    if not modifier_args:
        sourced = branch.source_arguments(overload.params)
        if sourced is not None:
            args, popped = sourced
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
    sourced = branch.source_arguments(stack_params)
    if sourced is None:
        return
    stack_args, popped = sourced

    for ordered_modifiers in _unique_permutations(modifier_args):
        args: list[T.Type] = []
        stack_index = 0
        modifier_index = 0
        for index in range(len(overload.params)):
            if index in modifier_indexes:
                args.append(ordered_modifiers[modifier_index].typ)
                modifier_index += 1
            else:
                args.append(stack_args[stack_index])
                stack_index += 1
        yield tuple(args), popped, ordered_modifiers


def _modifier_arity_matches(
    overloads: tuple[T.Overload, ...],
    modifier_args: tuple[ModifierArgumentAnalysis, ...],
) -> bool:
    return len(modifier_args) in {
        len(_modifier_param_indexes(overload.params)) for overload in overloads
    }


def _show_modifier_counts(overloads: tuple[T.Overload, ...]) -> str:
    counts = sorted(
        {len(_modifier_param_indexes(overload.params)) for overload in overloads}
    )
    if len(counts) == 1:
        return str(counts[0])
    return " or ".join(str(count) for count in counts)


def _modifier_param_indexes(params: tuple[T.Type, ...]) -> tuple[int, ...]:
    return tuple(
        index
        for index, param in enumerate(params)
        if isinstance(T.normalize(param), T.FunctionType)
    )


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
) -> tuple[T.AppliedOverload, AnalysisBranch] | None:
    args = _row_views_for_arguments(args, overload.params, env)
    substitution = _branch_argument_substitution(args, overload.params, ctx)
    if substitution is None:
        return None
    specialized_branch = _specialize_branch_arguments(branch, substitution)
    specialized_args = tuple(_substitute_branch_type(arg, substitution) for arg in args)
    applied = T.apply_overload(overload, specialized_args, ctx)
    if applied is None:
        return None
    actual_returns = _apply_data_tag_flow(
        specialized_args,
        overload.returns,
        applied.actual_returns,
        ctx,
    )
    applied = T.AppliedOverload(
        applied.overload,
        applied.substitution,
        applied.params,
        applied.returns,
        actual_returns,
        applied.scores,
        applied.vectorised,
    )
    return applied, specialized_branch


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
    """Strip implicit computed tags and propagate sticky data tags."""
    explicit_tags = tuple(_explicit_tags(ret) for ret in declared_returns)
    returns = tuple(
        _strip_implicit_computed_tags(
            ret,
            explicit_tags[index] if index < len(explicit_tags) else frozenset(),
            ctx,
        )
        for index, ret in enumerate(actual_returns)
    )
    sticky_inputs = tuple(
        sticky for arg in args for sticky in _sticky_input_tags(arg, ctx)
    )
    if not sticky_inputs:
        return returns
    return tuple(_propagate_sticky_tags(ret, sticky_inputs, ctx) for ret in returns)


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
        kept = tuple(
            tag
            for tag in typ.tags
            if tag in explicit_tags
        )
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
            if item.name not in ctx.disjoint_tags.get(tag.name, set())
        }
        existing.add(tag)
        parent = ctx.tag_parents.get(tag.name)
        if parent is not None:
            existing.add(T.DataTag(parent, tag.depth))
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
        if T.compatible(arg, param, ctx):
            continue
        constraints = _solve_branch_argument(arg, param, ctx)
        if constraints is None:
            return None
        for name, typ in constraints.items():
            existing = substitution.get(name)
            if existing is not None and not T.same(existing, typ):
                return None
            substitution[name] = typ
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
        if T.compatible(actual, expected, ctx):
            return True
        if isinstance(actual, T.VarType):
            return bind(actual.name, expected)
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
        return T.Fn(
            (_substitute_branch_type(item, substitution) for item in typ.params),
            (_substitute_branch_type(item, substitution) for item in typ.returns),
        )
    if isinstance(typ, T.TaggedType):
        return T.Tagged(_substitute_branch_type(typ.inner, substitution), *typ.tags)
    if isinstance(typ, T.ExactType):
        return T.Exact(_substitute_branch_type(typ.inner, substitution))
    if isinstance(typ, T.AtomicType):
        return T.Atomic(_substitute_branch_type(typ.inner, substitution))
    return typ


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
) -> BranchVariables:
    result = variables
    for pattern in patterns:
        result = _add_match_pattern_variables(result, pattern)
    return result


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
) -> Iterator[tuple[ASTNode, ...]]:
    for pattern in patterns:
        yield from _pattern_guards(pattern)


def _pattern_guards(pattern: MatchPatternNode) -> Iterator[tuple[ASTNode, ...]]:
    if isinstance(pattern, GuardPatternNode):
        yield pattern.condition
    elif isinstance(pattern, TypePatternNode) and pattern.guard:
        yield pattern.guard
    elif isinstance(pattern, OrPatternNode):
        for option in pattern.options:
            yield from _pattern_guards(option)
    elif isinstance(pattern, ListPatternNode):
        for item in pattern.items:
            yield from _pattern_guards(item)
    elif isinstance(pattern, BindingPatternNode):
        yield from _pattern_guards(pattern.pattern)


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
        T.show(T.Fn(overload.params, overload.returns))
        for overload in overloads
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
        candidates = tuple(
            suffix[index] for suffix in suffixes if index < len(suffix)
        )
        merged.append(candidates[0] if len(candidates) == 1 else T.U(*candidates))
    return base_inputs + tuple(merged)


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
) -> BranchVariables:
    merged = before.drop_block_locals()
    for output in outputs:
        merged = merged.merge_against(
            output.variables.drop_block_locals(),
            before.drop_block_locals(),
        )
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
        _param_type(param, index)
        for index, param in enumerate(node.params or ())
    )
    returns = node.returns or ()
    return T.TraitRequirement(node.name, T.Overload(params, returns))


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
    if isinstance(typ, T.RowType):
        _collect_anonymous_type_indices(typ.base, indices)
        for row_field in typ.fields:
            _collect_anonymous_type_indices(row_field.typ, indices)
        return
    if isinstance(typ, T.CollectionType):
        _collect_anonymous_type_indices(typ.base, indices)
        return
    if isinstance(typ, T.FunctionType):
        for item in typ.params + typ.returns:
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
        )
    if isinstance(typed_node, TypedCallNode):
        return TypedCallNode(
            typed_node.node,
            typ,
            typed_node.overload,
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
        return T.Fn(
            (_refine_type(item, old, new) for item in typ.params),
            (_refine_type(item, old, new) for item in typ.returns),
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
    if T.assignable(source, target, ctx):
        return None
    return (
        f"cannot assign {T.show(source)} to variable '{name}' "
        f"of type {T.show(target)}"
    )


def _set_item(
    items: tuple[tuple[Symbol, T.Type], ...],
    name: Symbol,
    typ: T.Type,
) -> tuple[tuple[Symbol, T.Type], ...]:
    result = {key: value for key, value in items}
    result[name] = typ
    return _sorted_items(result.items())


def _sorted_items(
    items: Iterable[tuple[Symbol, T.Type]],
) -> tuple[tuple[Symbol, T.Type], ...]:
    return tuple(sorted(items, key=lambda item: item[0]))
