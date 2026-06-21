from __future__ import annotations

from dataclasses import dataclass

import valiance.types as T
from valiance.analysis.builtins import default_environment
from valiance.asts import (
    ASTNode,
    ElementNode,
    FunctionNode,
    FunctionParam,
    NumberLiteralNode,
    TypedNode,
)


@dataclass(frozen=True)
class AnalysisState:
    """One possible stack-analysis state."""

    inputs: tuple[T.Type, ...]
    stack: T.TypeStack


def analyse(
    program: list[ASTNode], env: T.Environment | None = None
) -> list[TypedNode]:
    env = env or default_environment()
    state = AnalysisState((), T.TypeStack())
    typed_program: list[TypedNode] = []
    for node in program:
        typ, state = analyse_node(node, state, env, infer_missing=False)
        typed_program.append(TypedNode(node, typ))
        if state is None:
            state = AnalysisState((), T.TypeStack())
    return typed_program


def analyse_function(node: FunctionNode, env: T.Environment) -> T.Type | None:
    """Infer the stack-effect type of a function literal."""
    infer_params = node.params is None
    params = (
        ()
        if node.params is None
        else tuple(_param_type(param, index) for index, param in enumerate(node.params))
    )
    state = AnalysisState(params, T.TypeStack(params))
    final_states = analyse_block(
        node.body,
        {state},
        env,
        infer_missing=infer_params,
    )
    signatures: set[T.Overload] = set()
    for final_state in final_states:
        if node.returns is not None:
            expected = T.TypeStack(node.returns)
            if not _stack_assignable(final_state.stack, expected, env.context):
                continue
            returns = node.returns
        else:
            returns = final_state.stack.items
        signatures.add(T.Overload(final_state.inputs, returns))

    if not signatures:
        return None

    ordered = tuple(
        sorted(
            signatures,
            key=lambda overload: T.show(T.Fn(overload.params, overload.returns)),
        )
    )
    if len(ordered) == 1:
        signature = ordered[0]
        return T.Fn(signature.params, signature.returns)
    return T.Overloads(*ordered)


def analyse_block(
    nodes: tuple[ASTNode, ...],
    states: set[AnalysisState],
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[AnalysisState]:
    """Analyse a sequence of nodes as stack-state transformations."""
    for node in nodes:
        next_states: set[AnalysisState] = set()
        for state in states:
            next_states.update(
                _analyse_node_states(
                    node,
                    state,
                    env,
                    infer_missing=infer_missing,
                )
            )
        states = next_states
        if not states:
            return set()
    return states


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
    return next(iter(results))


def _analyse_node_states(
    node: ASTNode,
    state: AnalysisState,
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[AnalysisState]:
    return {
        next_state
        for _, next_state in _analyse_node_results(
            node,
            state,
            env,
            infer_missing=infer_missing,
        )
        if next_state is not None
    }


def _analyse_node_results(
    node: ASTNode,
    state: AnalysisState,
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[tuple[T.Type | None, AnalysisState | None]]:
    match node:
        case NumberLiteralNode(_):
            return {(T.Number, AnalysisState(state.inputs, state.stack.push(T.Number)))}
        case ElementNode(name):
            return _analyse_element(name, state, env, infer_missing=infer_missing)
        case FunctionNode():
            typ = analyse_function(node, env)
            if typ is None:
                return {(None, None)}
            return {(typ, AnalysisState(state.inputs, state.stack.push(typ)))}
        case _:
            return {(None, state)}


def _analyse_element(
    name: str,
    state: AnalysisState,
    env: T.Environment,
    *,
    infer_missing: bool,
) -> set[tuple[T.Type | None, AnalysisState | None]]:
    if infer_missing:
        overloads = env.overloads_for(name)
        if not overloads:
            print(f"Error: unknown element '{name}'")
            return {(None, None)}
        applications = T.apply_overload_candidates_to_stack(
            overloads,
            state.stack,
            env.context,
            infer_missing=True,
        )
        if applications:
            return {
                (
                    _returns_result_type(application.actual_returns),
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
            return {(_returns_result_type(application.actual_returns), next_state)}
        case T.UnknownElement():
            print(f"Error: unknown element '{name}'")
            return {(None, None)}
        case T.NoMatchingOverload() as result:
            print(f"Error: no overloads for element '{name}' match the given arguments")
            next_state = AnalysisState(state.inputs, result.stack)
            return {(_returns_result_type(result.actual_returns), next_state)}


def _returns_result_type(returns: tuple[T.Type, ...]) -> T.Type | None:
    if len(returns) == 1:
        return returns[0]
    return None


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
