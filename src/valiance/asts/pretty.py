"""Small multiline AST pretty-printer for debugging."""

from __future__ import annotations

from collections.abc import Sequence

from valiance.asts.nodes import (
    ASTNode,
    ElementNode,
    FieldAccessNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    GetVariableNode,
    NumberLiteralNode,
    SetVariableNode,
    StringLiteralNode,
    TagApplicationNode,
    TypedCallNode,
    TypedElementNode,
    TypedFunctionNode,
    TypedNode,
)
from valiance.types import Type


def pretty_ast(value: ASTNode | TypedNode | Sequence[ASTNode | TypedNode]) -> str:
    """Return a readable multiline representation of AST or typed AST values."""
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not value:
            return "[]"
        lines = ["["]
        for item in value:
            lines.extend(_indent(_pretty(item, 0).splitlines()))
        lines.append("]")
        return "\n".join(lines)
    return _pretty(value, 0)


def _pretty(value: ASTNode | TypedNode | FunctionOverloadTyping, level: int) -> str:
    if isinstance(value, TypedFunctionNode):
        return _typed_function_node(value, level)
    if isinstance(value, TypedElementNode):
        return _typed_element_node(value, level)
    if isinstance(value, TypedCallNode):
        return _typed_call_node(value, level)
    if isinstance(value, TypedNode):
        return _typed_node(value, level)
    if isinstance(value, FunctionOverloadTyping):
        return _function_overload_typing(value, level)
    if isinstance(value, FunctionNode):
        return _function_node(value, level)
    if isinstance(value, NumberLiteralNode):
        return f"NumberLiteralNode(value={value.value!r}{_location_arg(value)})"
    if isinstance(value, StringLiteralNode):
        return f"StringLiteralNode(value={value.value!r}{_location_arg(value)})"
    if isinstance(value, ElementNode):
        if not value.modifier_args:
            return f"ElementNode(name={value.name}{_location_arg(value)})"
        lines = [
            f"ElementNode(name={value.name}{_location_arg(value)}, modifier_args=["
        ]
        for arg in value.modifier_args:
            lines.extend(_indent(_pretty(arg, level + 1).splitlines()))
        lines.append("])")
        return "\n".join(lines)
    if isinstance(value, GetVariableNode):
        return f"GetVariableNode(name={value.name}{_location_arg(value)})"
    if isinstance(value, SetVariableNode):
        return f"SetVariableNode(name={value.name}{_location_arg(value)})"
    if isinstance(value, FieldAccessNode):
        return f"FieldAccessNode(name={value.name}{_location_arg(value)})"
    if isinstance(value, TagApplicationNode):
        return f"TagApplicationNode(tag={_tag_label(value)}{_location_arg(value)})"
    return repr(value)


def _typed_node(value: TypedNode, level: int) -> str:
    lines = [f"TypedNode(type={_type_label(value.typ)}, node="]
    lines.extend(_indent(_pretty(value.node, level + 1).splitlines()))
    lines.append(")")
    return "\n".join(lines)


def _typed_element_node(value: TypedElementNode, level: int) -> str:
    overload = "unresolved"
    if value.overload is not None:
        overload = str(value.overload.overload)
    lines = [
        "TypedElementNode("
        f"type={_type_label(value.typ)}, "
        f"overload_index={value.overload_index}, "
        f"vectorised={value.overload.vectorised if value.overload else False}, "
        f"overload={overload}, "
        "node="
    ]
    lines.extend(_indent(_pretty(value.node, level + 1).splitlines()))
    if value.modifier_args:
        lines.append("  modifier_args=[")
        for arg in value.modifier_args:
            lines.extend(_indent(_pretty(arg, level + 1).splitlines(), 2))
        lines.append("  ]")
    lines.append(")")
    return "\n".join(lines)


def _typed_call_node(value: TypedCallNode, level: int) -> str:
    overload = "unresolved"
    if value.overload is not None:
        overload = str(value.overload.overload)
    lines = [
        "TypedCallNode("
        f"type={_type_label(value.typ)}, "
        f"vectorised={value.overload.vectorised if value.overload else False}, "
        f"overload={overload}, "
        "node="
    ]
    lines.extend(_indent(_pretty(value.node, level + 1).splitlines()))
    lines.append(")")
    return "\n".join(lines)


def _typed_function_node(value: TypedFunctionNode, level: int) -> str:
    lines = [f"TypedFunctionNode(type={_type_label(value.typ)}, node="]
    lines.extend(_indent(_pretty(value.node, level + 1).splitlines()))
    if value.overloads:
        lines.append("  overloads=[")
        for overload in value.overloads:
            lines.extend(_indent(_pretty(overload, level + 1).splitlines(), 2))
        lines.append("  ]")
    lines.append(")")
    return "\n".join(lines)


def _function_overload_typing(value: FunctionOverloadTyping, level: int) -> str:
    lines = [f"FunctionOverloadTyping(type={value.typ}, body=["]
    for node in value.body:
        lines.extend(_indent(_pretty(node, level + 1).splitlines()))
    lines.append("])")
    return "\n".join(lines)


def _function_node(value: FunctionNode, level: int) -> str:
    lines = [
        "FunctionNode(",
        f"  params={_params_label(value.params)},",
        f"  returns={_types_label(value.returns)},",
        "  body=[",
    ]
    for node in value.body:
        lines.extend(_indent(_pretty(node, level + 1).splitlines(), 2))
    lines.extend(["  ]", ")"])
    if value.location is not None:
        lines.insert(1, f"  location={_location_label(value)},")
    return "\n".join(lines)


def _params_label(params: tuple[FunctionParam, ...] | None) -> str:
    if params is None:
        return "infer"
    return "[" + ", ".join(_param_label(param) for param in params) + "]"


def _param_label(param: FunctionParam) -> str:
    name = "_" if param.name is None else str(param.name)
    typ = "infer" if param.typ is None else str(param.typ)
    return f"{name}: {typ}"


def _types_label(types: tuple[Type, ...] | None) -> str:
    if types is None:
        return "infer"
    return "[" + ", ".join(str(typ) for typ in types) + "]"


def _type_label(typ: Type | None) -> str:
    return "untyped" if typ is None else str(typ)


def _location_arg(node: ASTNode) -> str:
    if node.location is None:
        return ""
    return f", location={_location_label(node)}"


def _location_label(node: ASTNode) -> str:
    location = node.location
    if location is None:
        return "unknown"
    return f"{location.line}:{location.column}"


def _tag_label(node: TagApplicationNode) -> str:
    prefix = "#!" if node.tag.absent else "#"
    depth = "+" * node.tag.depth
    return f"{prefix}{node.tag.name}{depth}"


def _indent(lines: list[str], spaces: int = 2) -> list[str]:
    prefix = " " * spaces
    return [prefix + line for line in lines]
