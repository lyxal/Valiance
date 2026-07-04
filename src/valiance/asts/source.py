"""Small source emitter for analysed Valiance programs."""

from __future__ import annotations

from collections.abc import Sequence

from valiance.asts.nodes import (
    ASTNode,
    DefineNode,
    ElementNode,
    FunctionNode,
    FunctionParam,
    GetVariableNode,
    ListLiteralNode,
    NumberLiteralNode,
    SetVariableNode,
    StringLiteralNode,
    TypedFunctionNode,
    TypedNode,
)
from valiance.types import FunctionType, Type, normalize, show


def typed_source(value: Sequence[ASTNode | TypedNode]) -> str:
    """Render an analysed program as source with available type annotations."""
    return "\n".join(_typed_node_source(node) for node in value)


def _typed_node_source(node: ASTNode | TypedNode) -> str:
    if isinstance(node, TypedFunctionNode):
        return _typed_function_source(node)
    if isinstance(node, TypedNode):
        rendered = _node_source(node.node)
        if node.typ is None:
            return rendered
        return f"{rendered} as {show(node.typ)}"
    return _node_source(node)


def _typed_function_source(node: TypedFunctionNode) -> str:
    ast = node.node
    typ = normalize(node.typ) if node.typ is not None else None
    if isinstance(ast, DefineNode) and isinstance(typ, FunctionType):
        return (
            f"define {ast.name}{_function_signature(ast.function, typ)} => "
            f"{_body_source(ast.function.body)}"
        )
    if isinstance(ast, FunctionNode) and isinstance(typ, FunctionType):
        return f"fn{_function_signature(ast, typ)} => {_body_source(ast.body)}"
    return _typed_node_source(TypedNode(ast, node.typ))


def _function_signature(node: FunctionNode, typ: FunctionType) -> str:
    params = typ.params or ()
    returns = typ.returns or ()
    return f"({_params_source(node.params, params)}) -> {_returns_source(returns)}"


def _params_source(
    source_params: tuple[FunctionParam, ...] | None,
    params: tuple[Type, ...],
) -> str:
    labels: list[str] = []
    for index, typ in enumerate(params):
        name = f"_{index}"
        if source_params is not None and index < len(source_params):
            param = source_params[index]
            if param.name is not None:
                name = str(param.name)
        labels.append(f"{name}: {show(typ)}")
    return ", ".join(labels)


def _returns_source(returns: tuple[Type, ...]) -> str:
    if not returns:
        return "()"
    return ", ".join(show(ret) for ret in returns)


def _body_source(body: tuple[ASTNode, ...]) -> str:
    return " ".join(_node_source(node) for node in body)


def _node_source(node: ASTNode) -> str:
    if isinstance(node, NumberLiteralNode):
        return node.value
    if isinstance(node, StringLiteralNode):
        return repr(node.value)
    if isinstance(node, GetVariableNode):
        return f"${node.name}"
    if isinstance(node, SetVariableNode):
        return f"${node.name} ="
    if isinstance(node, ElementNode):
        return str(node.name)
    if isinstance(node, ListLiteralNode):
        items = (typed_source(item) for item in node.items)
        return "[" + ", ".join(items) + "]"
    if isinstance(node, FunctionNode):
        return f"fn => {_body_source(node.body)}"
    if isinstance(node, DefineNode):
        return f"define {node.name} => {_body_source(node.function.body)}"
    return repr(node)
