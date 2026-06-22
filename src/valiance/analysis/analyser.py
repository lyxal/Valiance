from __future__ import annotations

from dataclasses import dataclass

import valiance.types as T
from valiance.analysis.builtins import default_environment
from valiance.asts import (
    ASTNode,
    ElementNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    NumberLiteralNode,
    TypedFunctionNode,
    TypedNode,
)


@dataclass(frozen=True)
class AnalysisState:
    """One possible stack-analysis state."""

    inputs: tuple[T.Type, ...]
    stack: T.TypeStack


@dataclass(frozen=True)
class AnalysisBranch:
    """One possible typed body and stack state during block analysis."""

    state: AnalysisState
    typed_body: tuple[TypedNode, ...] = ()

    def append(self, result: NodeAnalysis) -> AnalysisBranch | None:
        """Return this branch after one analysed node, or None if invalid."""
        if result.state is None:
            return None
        return AnalysisBranch(
            result.state,
            self.typed_body + (result.typed_node,),
        )


@dataclass(frozen=True)
class NodeAnalysis:
    """Result of analysing one AST node on one branch."""

    typed_node: TypedNode
    state: AnalysisState | None


@dataclass(frozen=True)
class FunctionAnalysis:
    """Typed function literal result, including per-overload typed bodies."""

    typ: T.Type
    overloads: tuple[FunctionOverloadTyping, ...]


class Analyser:
    """Mutable analysis session that owns environment and inference mode."""

    def __init__(
        self,
        env: T.Environment | None = None,
        *,
        infer_missing: bool = False,
    ):
        self.env = env or default_environment()
        self.infer_missing = infer_missing

    def analyse(self, program: list[ASTNode]) -> list[TypedNode]:
        """Analyse a top-level sequence into typed nodes."""
        state = AnalysisState((), T.TypeStack())
        typed_program: list[TypedNode] = []
        for node in program:
            result = self.single_result(node, state)
            typed_program.append(result.typed_node)
            state = result.state or AnalysisState((), T.TypeStack())
        return typed_program

    def analyse_function(self, node: FunctionNode) -> T.Type | None:
        """Infer the stack-effect type of a function literal."""
        result = self.analyse_function_details(node)
        return None if result is None else result.typ

    def analyse_function_details(self, node: FunctionNode) -> FunctionAnalysis | None:
        """Infer a function literal and keep typed bodies for each overload."""
        function_env = self.env.child_scope()
        infer_params = node.params is None
        params = (
            ()
            if node.params is None
            else tuple(
                _param_type(param, index) for index, param in enumerate(node.params)
            )
        )
        if node.params is not None:
            for param, typ in zip(node.params, params, strict=True):
                if param.name is not None:
                    function_env.define_variable(param.name, typ)

        function_analyser = Analyser(function_env, infer_missing=infer_params)
        initial = AnalysisState(params, T.TypeStack(params))
        final_branches = function_analyser.typed_block(
            node.body,
            {AnalysisBranch(initial)},
        )
        signatures = self._function_signatures(node, final_branches, function_env)
        return _function_analysis_from_signatures(signatures)

    def block(
        self,
        nodes: tuple[ASTNode, ...],
        states: set[AnalysisState],
    ) -> set[AnalysisState]:
        """Analyse a sequence of nodes as stack-state transformations."""
        branches = self.typed_block(
            nodes,
            {AnalysisBranch(state) for state in states},
        )
        return {branch.state for branch in branches}

    def typed_block(
        self,
        nodes: tuple[ASTNode, ...],
        branches: set[AnalysisBranch],
    ) -> set[AnalysisBranch]:
        """Analyse a sequence of nodes as typed branch transformations."""
        current = branches
        for node in nodes:
            current = self._advance_branches(current, node)
            if not current:
                return set()
        return current

    def block_one(
        self,
        nodes: tuple[ASTNode, ...],
        state: AnalysisState,
    ) -> AnalysisState | None:
        """Analyse a block when exactly one output state is expected."""
        branch = self.typed_block_one(nodes, AnalysisBranch(state))
        return None if branch is None else branch.state

    def typed_block_one(
        self,
        nodes: tuple[ASTNode, ...],
        branch: AnalysisBranch,
    ) -> AnalysisBranch | None:
        """Analyse a block when exactly one typed output branch is expected."""
        branches = self.typed_block(nodes, {branch})
        if len(branches) != 1:
            return None
        return next(iter(branches))

    def condition_branches(
        self,
        nodes: tuple[ASTNode, ...],
        state: AnalysisState,
        condition_type: T.Type,
    ) -> set[AnalysisBranch]:
        """Analyse a condition block and pop its control value."""
        branches = self.typed_block(nodes, {AnalysisBranch(state)})
        results: set[AnalysisBranch] = set()
        for branch in branches:
            stack = branch.state.stack
            if (
                not stack
                or _is_never(stack[-1])
                or not T.assignable(
                    stack[-1],
                    condition_type,
                    self.env.context,
                )
            ):
                return set()
            results.add(
                AnalysisBranch(
                    AnalysisState(
                        branch.state.inputs,
                        T.TypeStack(stack.items[:-1]),
                    ),
                    branch.typed_body,
                )
            )
        return results

    def analyse_node(
        self,
        node: ASTNode,
        state: AnalysisState,
    ) -> tuple[T.Type | None, AnalysisState | None]:
        """Analyse one node when exactly one result is expected."""
        result = self.single_result(node, state)
        return result.typed_node.typ, result.state

    def single_result(self, node: ASTNode, state: AnalysisState) -> NodeAnalysis:
        """Return one result, or an untyped invalid result if analysis branched."""
        results = self.node_results(node, state)
        if len(results) != 1:
            return NodeAnalysis(TypedNode(node, None), None)
        return next(iter(results))

    def node_results(
        self,
        node: ASTNode,
        state: AnalysisState,
    ) -> set[NodeAnalysis]:
        """Analyse one AST node on one branch."""
        match node:
            case NumberLiteralNode(_):
                return {
                    NodeAnalysis(
                        TypedNode(node, T.Number),
                        AnalysisState(state.inputs, state.stack.push(T.Number)),
                    )
                }
            case ElementNode():
                return self._element(node, state)
            case FunctionNode():
                result = self.analyse_function_details(node)
                if result is None:
                    return {NodeAnalysis(TypedNode(node, None), None)}
                typed_node = TypedFunctionNode(node, result.typ, result.overloads)
                return {
                    NodeAnalysis(
                        typed_node,
                        AnalysisState(state.inputs, state.stack.push(result.typ)),
                    )
                }
            case _:
                return {NodeAnalysis(TypedNode(node, None), state)}

    def _advance_branches(
        self,
        branches: set[AnalysisBranch],
        node: ASTNode,
    ) -> set[AnalysisBranch]:
        next_branches: set[AnalysisBranch] = set()
        for branch in branches:
            for result in self.node_results(node, branch.state):
                next_branch = branch.append(result)
                if next_branch is not None:
                    next_branches.add(next_branch)
        return next_branches

    def _element(self, node: ElementNode, state: AnalysisState) -> set[NodeAnalysis]:
        if self.infer_missing:
            inferred = self._infer_element_candidates(node, state)
            if inferred:
                return inferred

        match self.env.apply(node.name, state.stack, infer_missing=self.infer_missing):
            case T.AppliedElement(application):
                return {
                    _element_analysis(
                        node,
                        AnalysisState(
                            state.inputs + application.inputs,
                            application.stack,
                        ),
                        application.actual_returns,
                    )
                }
            case T.UnknownElement():
                print(f"Error: unknown element '{node.name}'")
                return {NodeAnalysis(TypedNode(node, None), None)}
            case T.NoMatchingOverload() as result:
                print(
                    f"Error: no overloads for element '{node.name}' "
                    "match the given arguments"
                )
                return {
                    _element_analysis(
                        node,
                        AnalysisState(state.inputs, result.stack),
                        result.actual_returns,
                    )
                }
            case _:
                return {NodeAnalysis(TypedNode(node, None), None)}

    def _infer_element_candidates(
        self,
        node: ElementNode,
        state: AnalysisState,
    ) -> set[NodeAnalysis]:
        overloads = self.env.overloads_for(node.name)
        if not overloads:
            print(f"Error: unknown element '{node.name}'")
            return {NodeAnalysis(TypedNode(node, None), None)}

        applications = T.apply_overload_candidates_to_stack(
            overloads,
            state.stack,
            self.env.context,
            infer_missing=True,
        )
        return {
            _element_analysis(
                node,
                AnalysisState(
                    state.inputs + application.inputs,
                    application.stack,
                ),
                application.actual_returns,
            )
            for application in applications
        }

    def _function_signatures(
        self,
        node: FunctionNode,
        branches: set[AnalysisBranch],
        env: T.Environment,
    ) -> dict[T.Overload, tuple[TypedNode, ...]]:
        signatures: dict[T.Overload, tuple[TypedNode, ...]] = {}
        for branch in branches:
            final_state = branch.state
            returns = self._function_returns(node, final_state, env)
            if returns is None:
                continue
            signature = T.Overload(final_state.inputs, returns)
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
        state: AnalysisState,
        env: T.Environment,
    ) -> tuple[T.Type, ...] | None:
        if node.returns is None:
            return state.stack.items

        expected = T.TypeStack(node.returns)
        if not _stack_assignable(state.stack, expected, env.context):
            return None
        return node.returns


def analyse(
    program: list[ASTNode], env: T.Environment | None = None
) -> list[TypedNode]:
    return Analyser(env).analyse(program)


def analyse_function(node: FunctionNode, env: T.Environment) -> T.Type | None:
    return Analyser(env).analyse_function(node)


def analyse_function_details(
    node: FunctionNode,
    env: T.Environment,
) -> FunctionAnalysis | None:
    return Analyser(env).analyse_function_details(node)


def analyse_block(
    nodes: tuple[ASTNode, ...],
    states: set[AnalysisState],
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[AnalysisState]:
    return Analyser(env, infer_missing=infer_missing).block(nodes, states)


def analyse_typed_block(
    nodes: tuple[ASTNode, ...],
    branches: set[AnalysisBranch],
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[AnalysisBranch]:
    return Analyser(env, infer_missing=infer_missing).typed_block(nodes, branches)


def analyse_node(
    node: ASTNode,
    state: AnalysisState,
    env: T.Environment,
    *,
    infer_missing: bool,
) -> tuple[T.Type | None, AnalysisState | None]:
    return Analyser(env, infer_missing=infer_missing).analyse_node(node, state)


def _element_analysis(
    node: ElementNode,
    state: AnalysisState,
    returns: tuple[T.Type, ...],
) -> NodeAnalysis:
    return NodeAnalysis(TypedNode(node, _returns_result_type(returns)), state)


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
    name = param.name or f"_{index}"
    return T.V(name)


def _stack_assignable(
    actual: T.TypeStack,
    expected: T.TypeStack,
    ctx: T.Context,
) -> bool:
    if len(actual) != len(expected):
        return False
    return all(T.assignable(a, e, ctx) for a, e in zip(actual, expected, strict=True))
