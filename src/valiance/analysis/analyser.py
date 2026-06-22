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


def analyse(
    program: list[ASTNode], env: T.Environment | None = None
) -> list[TypedNode]:
    env = env or default_environment()
    state = AnalysisState((), T.TypeStack())
    typed_program: list[TypedNode] = []
    for node in program:
        results = _analyse_node_results(node, state, env, infer_missing=False)
        if len(results) == 1:
            result = next(iter(results))
            typed_program.append(result.typed_node)
            state = result.state
        else:
            typed_program.append(TypedNode(node, None))
            state = None
        if state is None:
            state = AnalysisState((), T.TypeStack())
    return typed_program


def analyse_function(node: FunctionNode, env: T.Environment) -> T.Type | None:
    """Infer the stack-effect type of a function literal."""
    result = analyse_function_details(node, env)
    return None if result is None else result.typ


def analyse_function_details(
    node: FunctionNode,
    env: T.Environment,
) -> FunctionAnalysis | None:
    """Infer a function literal and keep typed bodies for each overload."""
    function_env = env.child_scope()
    infer_params = node.params is None
    params = (
        ()
        if node.params is None
        else tuple(_param_type(param, index) for index, param in enumerate(node.params))
    )
    if node.params is not None:
        for param, typ in zip(node.params, params, strict=True):
            if param.name is not None:
                function_env.define_variable(param.name, typ)
    state = AnalysisState(params, T.TypeStack(params))
    final_branches = analyse_typed_block(
        node.body,
        {AnalysisBranch(state)},
        function_env,
        infer_missing=infer_params,
    )
    signatures: dict[T.Overload, tuple[TypedNode, ...]] = {}
    for branch in final_branches:
        final_state = branch.state
        if node.returns is not None:
            expected = T.TypeStack(node.returns)
            if not _stack_assignable(final_state.stack, expected, function_env.context):
                continue
            returns = node.returns
        else:
            returns = final_state.stack.items
        signature = T.Overload(final_state.inputs, returns)
        signatures.setdefault(signature, branch.typed_body)

    if len(signatures) > 1:
        signatures = {
            signature: body
            for signature, body in signatures.items()
            if not _has_never_return(signature)
        }

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


def analyse_block(
    nodes: tuple[ASTNode, ...],
    states: set[AnalysisState],
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[AnalysisState]:
    """Analyse a sequence of nodes as stack-state transformations."""
    branches = analyse_typed_block(
        nodes,
        {AnalysisBranch(state) for state in states},
        env,
        infer_missing=infer_missing,
    )
    return {branch.state for branch in branches}


def analyse_typed_block(
    nodes: tuple[ASTNode, ...],
    branches: set[AnalysisBranch],
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[AnalysisBranch]:
    """Analyse a sequence of nodes as typed branch transformations."""
    for node in nodes:
        next_branches: set[AnalysisBranch] = set()
        for branch in branches:
            for result in _analyse_node_results(
                node,
                branch.state,
                env,
                infer_missing=infer_missing,
            ):
                if result.state is None:
                    continue
                next_branches.add(
                    AnalysisBranch(
                        result.state,
                        branch.typed_body + (result.typed_node,),
                    )
                )
        branches = next_branches
        if not branches:
            return set()
    return branches


def analyse_node(
    node: ASTNode,
    state: AnalysisState,
    env: T.Environment,
    *,
    infer_missing: bool,
) -> tuple[T.Type | None, AnalysisState | None]:
    results = _analyse_node_results(
        node,
        state,
        env,
        infer_missing=infer_missing,
    )
    if len(results) != 1:
        return None, None
    result = next(iter(results))
    return result.typed_node.typ, result.state


def _analyse_node_results(
    node: ASTNode,
    state: AnalysisState,
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[NodeAnalysis]:
    match node:
        case NumberLiteralNode(_):
            return {
                NodeAnalysis(
                    TypedNode(node, T.Number),
                    AnalysisState(state.inputs, state.stack.push(T.Number)),
                )
            }
        case ElementNode():
            return _analyse_element(node, state, env, infer_missing=infer_missing)
        case FunctionNode():
            result = analyse_function_details(node, env)
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


def _analyse_node_states(
    node: ASTNode,
    state: AnalysisState,
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[AnalysisState]:
    return {
        result.state
        for result in _analyse_node_results(
            node,
            state,
            env,
            infer_missing=infer_missing,
        )
        if result.state is not None
    }


def _analyse_element(
    node: ElementNode,
    state: AnalysisState,
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[NodeAnalysis]:
    name = node.name
    if infer_missing:
        overloads = env.overloads_for(name)
        if not overloads:
            print(f"Error: unknown element '{name}'")
            return {NodeAnalysis(TypedNode(node, None), None)}
        applications = T.apply_overload_candidates_to_stack(
            overloads,
            state.stack,
            env.context,
            infer_missing=True,
        )
        if applications:
            return {
                NodeAnalysis(
                    TypedNode(
                        node,
                        _returns_result_type(application.actual_returns),
                    ),
                    AnalysisState(
                        state.inputs + application.inputs,
                        application.stack,
                    ),
                )
                for application in applications
            }

    match env.apply(name, state.stack, infer_missing=infer_missing):
        case T.AppliedElement(application):
            next_state = AnalysisState(
                state.inputs + application.inputs,
                application.stack,
            )
            return {
                NodeAnalysis(
                    TypedNode(node, _returns_result_type(application.actual_returns)),
                    next_state,
                )
            }
        case T.UnknownElement():
            print(f"Error: unknown element '{name}'")
            return {NodeAnalysis(TypedNode(node, None), None)}
        case T.NoMatchingOverload() as result:
            print(f"Error: no overloads for element '{name}' match the given arguments")
            next_state = AnalysisState(state.inputs, result.stack)
            return {
                NodeAnalysis(
                    TypedNode(node, _returns_result_type(result.actual_returns)),
                    next_state,
                )
            }


def _returns_result_type(returns: tuple[T.Type, ...]) -> T.Type | None:
    if len(returns) == 1:
        return returns[0]
    return None


def _has_never_return(overload: T.Overload) -> bool:
    return any(isinstance(T.normalize(ret), T.NeverType) for ret in overload.returns)


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
