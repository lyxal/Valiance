from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum, auto
from itertools import count

import valiance.types as T
from valiance.analysis.builtins import default_environment
from valiance.asts import (
    ASTNode,
    ElementNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    NumberLiteralNode,
    StringLiteralNode,
    TypedFunctionNode,
    TypedNode,
)
from valiance.asts.nodes import FieldAccessNode, GetVariableNode, SetVariableNode
from valiance.symbols import Symbol

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

    def refine_type(self, old: T.Type, new: T.Type) -> AnalysisBranch:
        """Replace one inferred/generic type fact across the branch."""
        return _replace_branch(
            self,
            stack=_refine_stack(self.stack, old, new),
            inputs=tuple(_refine_type(item, old, new) for item in self.inputs),
            variables=self.variables.refine_type(old, new),
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
            current = current.map_node(node, analyser)
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


class Analyser:
    """Analysis session owning global environment, diagnostics, and dispatch."""

    def __init__(self, env: T.Environment | None = None):
        self.env = env or default_environment()
        self.diagnostics: list[str] = []

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
            case ElementNode():
                return self._element(branch, node)
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
            case GetVariableNode(name):
                typ = branch.variables.read(name)
                if typ is None:
                    self._diagnose(f"undefined variable '{name}'")
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
            case _:
                return {branch.append_typed(TypedNode(node, None))}

    def _element(
        self,
        branch: AnalysisBranch,
        node: ElementNode,
    ) -> set[AnalysisBranch]:
        overloads = self.env.overloads_for(node.name)
        if not overloads:
            self._diagnose(f"unknown element '{node.name}'")
            return set()

        candidates: list[tuple[T.AppliedOverload, AnalysisBranch]] = []
        for overload in overloads:
            sourced = branch.source_arguments(overload.params)
            if sourced is None:
                continue
            args, popped = sourced
            applied = T.apply_overload(overload, args, self.env.context)
            if applied is not None:
                candidates.append((applied, popped))

        winners = _best_candidates(candidates)
        if not winners:
            self._diagnose(
                f"no overloads for element '{node.name}' match the given arguments"
            )
            return set()
        if len(winners) > 1 and branch.input_mode is not InputMode.INFER_INPUTS:
            self._diagnose(f"ambiguous overloads for element '{node.name}'")
            return set()

        results: set[AnalysisBranch] = set()
        for applied, popped in winners:
            results.add(
                popped.with_stack(
                    popped.stack.push(*applied.actual_returns)
                ).append_typed(
                    TypedNode(node, _returns_result_type(applied.actual_returns))
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
            self._diagnose(f"empty stack when trying to access field '{name}'")
            return set()

        receiver_type, field_type, branch = sourced
        if field_type is None:
            self._diagnose(
                f"type {T.show(receiver_type)} has no known field '{name}'"
            )
            return set()

        return {
            branch.with_stack(branch.stack.push(field_type)).append_typed(
                TypedNode(node, field_type)
            )
        }

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
    ) -> tuple[T.Type | None, T.Type | None]:
        receiver_type = T.normalize(receiver_type)
        if isinstance(receiver_type, T.RowType):
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
            field_type = _anonymous_type_var(branch, 1)
            return field_type, T.Row(receiver_type, T.Field(name, field_type))

        if isinstance(receiver_type, T.NominalType):
            return self.env.lookup_attribute(receiver_type.name, name), None

        return None, None

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
        final = function_analyser.analyse_block(BranchSet.one(initial), node.body)
        self.diagnostics.extend(function_analyser.diagnostics)
        signatures = self._function_signatures(node, final)
        analysis = _function_analysis_from_signatures(signatures)
        if analysis is None:
            return None
        return analysis, outer

    def _function_signatures(
        self,
        node: FunctionNode,
        branches: BranchSet,
    ) -> dict[T.Overload, tuple[TypedNode, ...]]:
        signatures: dict[T.Overload, tuple[TypedNode, ...]] = {}
        for branch in branches:
            returns = self._function_returns(node, branch.stack)
            if returns is None:
                continue
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
        stack: T.TypeStack,
    ) -> tuple[T.Type, ...] | None:
        if node.returns is None:
            return stack.items

        expected = T.TypeStack(node.returns)
        if not _stack_assignable(stack, expected, self.env.context):
            return None
        return node.returns

    def _diagnose(self, message: str) -> None:
        self.diagnostics.append(message)
        print(f"Error: {message}")


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
        diagnostics=branch.diagnostics if diagnostics is None else diagnostics,
        origin=branch.origin if origin is None else origin,
    )


def _declared_params(node: FunctionNode) -> tuple[T.Type, ...]:
    if node.params is None:
        return ()
    return tuple(_param_type(param, index) for index, param in enumerate(node.params))


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


def _best_candidates(
    candidates: Iterable[tuple[T.AppliedOverload, AnalysisBranch]],
) -> tuple[tuple[T.AppliedOverload, AnalysisBranch], ...]:
    ordered = list(candidates)
    winners: list[tuple[T.AppliedOverload, AnalysisBranch]] = []
    for candidate in ordered:
        applied, _ = candidate
        if not any(
            other is not candidate and _dominates(other[0].scores, applied.scores)
            for other in ordered
        ):
            winners.append(candidate)
    return tuple(winners)


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


def _has_never_return(overload: T.Overload) -> bool:
    return any(isinstance(T.normalize(ret), T.NeverType) for ret in overload.returns)


def _is_never(t: T.Type) -> bool:
    return isinstance(T.normalize(t), T.NeverType)


def _param_type(param: FunctionParam, index: int) -> T.Type:
    if param.typ is not None:
        return param.typ
    name = param.name.text if param.name is not None else f"_{index}"
    return T.V(name)


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


def _refine_type(typ: T.Type, old: T.Type, new: T.Type) -> T.Type:
    typ = T.normalize(typ)
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


def _stack_assignable(
    actual: T.TypeStack,
    expected: T.TypeStack,
    ctx: T.Context,
) -> bool:
    if len(actual) != len(expected):
        return False
    return all(T.assignable(a, e, ctx) for a, e in zip(actual, expected, strict=True))


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
