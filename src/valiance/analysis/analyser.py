from __future__ import annotations

from dataclasses import dataclass

from valiance.analysis.builtins import default_environment
from valiance.asts import (
    ASTNode,
    ElementNode,
    FunctionNode,
    FunctionParam,
    NumberLiteralNode,
    TypedNode,
)
import valiance.types as T


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
    return typed_program


def analyse_function(node: FunctionNode, env: T.Environment) -> T.Type | None:
    """Infer the stack-effect type of a function literal."""
    params = tuple(_param_type(param, index) for index, param in enumerate(node.params))
    state = AnalysisState(params, T.TypeStack(params))
    final_states = analyse_block(node.body, {state}, env, infer_missing=True)
    if len(final_states) != 1:
        return None

    final_state = next(iter(final_states))
    if node.returns is not None:
        expected = T.TypeStack(node.returns)
        if not _stack_assignable(final_state.stack, expected, env.context):
            return None
        returns = node.returns
    else:
        returns = final_state.stack.items
    return T.Fn(final_state.inputs, returns)


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
            _, next_state = analyse_node(
                node,
                state,
                env,
                infer_missing=infer_missing,
            )
            next_states.add(next_state)
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
) -> tuple[T.Type | None, AnalysisState]:
    match node:
        case NumberLiteralNode(_):
            return T.Number, AnalysisState(state.inputs, state.stack.push(T.Number))
        case ElementNode(name):
            return _analyse_element(name, state, env, infer_missing=infer_missing)
        case FunctionNode():
            typ = analyse_function(node, env)
            if typ is None:
                return None, state
            return typ, AnalysisState(state.inputs, state.stack.push(typ))
        case _:
            return None, state


def _analyse_element(
    name: str,
    state: AnalysisState,
    env: T.Environment,
    *,
    infer_missing: bool,
) -> tuple[T.Type | None, AnalysisState]:
    match env.apply(name, state.stack, infer_missing=infer_missing):
        case T.AppliedElement(application):
            next_state = AnalysisState(
                state.inputs + application.inputs,
                application.stack,
            )
            return _element_result_type(application), next_state
        case T.UnknownElement():
            print(f"Error: unknown element '{name}'")
            return None, state
        case T.NoMatchingOverload():
            print(f"Error: no overloads for element '{name}' match the given arguments")
            return None, state


def _element_result_type(applied: T.StackApplication) -> T.Type | None:
    if len(applied.actual_returns) == 1:
        return applied.actual_returns[0]
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
    return all(T.assignable(a, e, ctx) for a, e in zip(actual, expected))
