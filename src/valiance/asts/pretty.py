"""Small multiline AST pretty-printer for debugging."""

from __future__ import annotations

from collections.abc import Sequence

from valiance.asts.nodes import (
    ASTNode,
    CallArgument,
    CastNode,
    ElementExtension,
    ElementNode,
    FieldAccessNode,
    FunctionNode,
    FunctionOverloadTyping,
    FunctionParam,
    GetVariableNode,
    ImportNode,
    NumberLiteralNode,
    SetVariableNode,
    SetVariablesNode,
    PopNNode,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    TagApplicationNode,
    TryHandlerNode,
    TryNode,
    TypedCallNode,
    TypedElementExtension,
    TypedElementNode,
    TypedExtensionPatternRule,
    TypedFunctionNode,
    TypedNode,
    TypeLiteralNode,
)
from valiance.types.symbols import Symbol
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
    """Compute pretty for AST diagnostic output."""
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
    if isinstance(value, TryNode):
        return _try_node(value, level)
    if isinstance(value, TryHandlerNode):
        return _try_handler_node(value, level)
    if isinstance(value, TypeLiteralNode):
        return f"TypeLiteralNode(type={value.typ}{_location_arg(value)})"
    if isinstance(value, NumberLiteralNode):
        return f"NumberLiteralNode(value={value.value!r}{_location_arg(value)})"
    if isinstance(value, StringLiteralNode):
        return f"StringLiteralNode(value={value.value!r}{_location_arg(value)})"
    if isinstance(value, StringInterpolationNode):
        lines = [f"StringInterpolationNode({_location_arg(value)}, parts=["]
        for part in value.parts:
            if isinstance(part, str):
                lines.append(f"  {part!r}")
            else:
                lines.append("  expression=[")
                for node in part:
                    lines.extend(_indent(_pretty(node, level + 1).splitlines(), 4))
                lines.append("  ]")
        lines.append("])")
        return "\n".join(lines)
    if isinstance(value, ElementNode):
        disambiguation = ""
        if value.disambiguation:
            hints = ", ".join(
                "_" if hint is None else str(hint) for hint in value.disambiguation
            )
            disambiguation = f", disambiguation=[{hints}]"
        if not value.modifier_args and not value.call_args and value.extension is None:
            return (
                f"ElementNode(name={value.name}{disambiguation}"
                f"{_location_arg(value)})"
            )
        lines = [
            f"ElementNode(name={value.name}{disambiguation}"
            f"{_location_arg(value)}"
        ]
        if value.call_args:
            lines.append("  call_args=[")
            for arg in value.call_args:
                lines.append(f"    {_call_argument_label(arg)}")
            lines.append("  ]")
        if value.modifier_args:
            lines.append("  modifier_args=[")
            for arg in value.modifier_args:
                lines.extend(_indent(_pretty(arg, level + 1).splitlines(), 4))
            lines.append("  ]")
        if value.extension is not None:
            lines.append("  extension=")
            lines.extend(
                _indent(_element_extension(value.extension, level + 1).splitlines(), 4)
            )
        lines.append(")")
        return "\n".join(lines)
    if isinstance(value, GetVariableNode):
        return f"GetVariableNode(name={value.name}{_location_arg(value)})"
    if isinstance(value, ImportNode):
        visibility = ", public=True" if value.public else ""
        return f"ImportNode(specs={value.specs!r}{visibility}{_location_arg(value)})"
    if isinstance(value, SetVariableNode):
        declared = (
            ""
            if value.declared_type is None
            else f", declared_type={value.declared_type}"
        )
        constant = ", constant=True" if value.constant else ""
        return (
            f"SetVariableNode(name={value.name}{declared}{constant}"
            f"{_location_arg(value)})"
        )
    if isinstance(value, SetVariablesNode):
        targets = ", ".join(_pretty(target, level + 1) for target in value.targets)
        return f"SetVariablesNode(targets=({targets}){_location_arg(value)})"
    if isinstance(value, FieldAccessNode):
        return f"FieldAccessNode(name={value.name}{_location_arg(value)})"
    if isinstance(value, TagApplicationNode):
        return f"TagApplicationNode(tag={_tag_label(value)}{_location_arg(value)})"
    if isinstance(value, CastNode):
        bang = "!" if value.checked else ""
        return (
            f"CastNode(as{bang} {_type_label(value.typ)}"
            f"{_location_arg(value)})"
        )
    if isinstance(value, PopNNode):
        return f"{prefix}PopNNode(count={value.count})"
    if isinstance(value, StackShuffleNode):
        prestack = ", ".join(_shuffle_label(label) for label in value.prestack)
        poststack = ", ".join(str(label) for label in value.poststack)
        return (
            f"StackShuffleNode(mode={value.mode}, "
            f"{prestack} -> {poststack}{_location_arg(value)})"
        )
    return repr(value)


def _typed_node(value: TypedNode, level: int) -> str:
    """Compute typed node for AST diagnostic output."""
    lines = [f"TypedNode(type={_type_label(value.typ)}, node="]
    lines.extend(_indent(_pretty(value.node, level + 1).splitlines()))
    lines.append(")")
    return "\n".join(lines)


def _typed_element_node(value: TypedElementNode, level: int) -> str:
    """Compute typed element node for AST diagnostic output."""
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
    if value.extension is not None:
        lines.append("  typed_extension=")
        lines.extend(
            _indent(
                _typed_element_extension(value.extension, level + 1).splitlines(),
                2,
            )
        )
    lines.append(")")
    return "\n".join(lines)


def _element_extension(value: ElementExtension, level: int) -> str:
    """Compute element extension for AST diagnostic output."""
    lines = ["ElementExtension("]
    if value.default is not None:
        lines.append("  default=")
        lines.extend(_indent(_pretty(value.default, level + 1).splitlines(), 4))
    if value.rules:
        lines.append("  rules=[")
        for rule in value.rules:
            pattern = ", ".join(
                "_" if name is None else str(name) for name in rule.pattern
            )
            lines.append(f"    ({pattern}) =>")
            lines.extend(_indent(_pretty(rule.function, level + 1).splitlines(), 6))
        lines.append("  ]")
    if value.selector is not None:
        lines.append("  selector=")
        lines.extend(_indent(_pretty(value.selector, level + 1).splitlines(), 4))
    lines.append(")")
    return "\n".join(lines)


def _typed_element_extension(value: TypedElementExtension, level: int) -> str:
    """Compute typed element extension for AST diagnostic output."""
    lines = ["TypedElementExtension("]
    if value.default is not None:
        lines.append("  default=")
        lines.extend(_indent(_pretty(value.default, level + 1).splitlines(), 4))
    if value.rules:
        lines.append("  rules=[")
        for rule in value.rules:
            lines.extend(
                _indent(_typed_extension_rule(rule, level + 1).splitlines(), 4)
            )
        lines.append("  ]")
    if value.selector is not None:
        lines.append("  selector=")
        lines.extend(_indent(_pretty(value.selector, level + 1).splitlines(), 4))
    lines.append(")")
    return "\n".join(lines)


def _typed_extension_rule(value: TypedExtensionPatternRule, level: int) -> str:
    """Compute typed extension rule for AST diagnostic output."""
    pattern = ", ".join(
        "_" if name is None else str(name) for name in value.pattern
    )
    lines = [f"TypedExtensionPatternRule(pattern=({pattern}), function="]
    lines.extend(_indent(_pretty(value.function, level + 1).splitlines()))
    lines.append(")")
    return "\n".join(lines)


def _typed_call_node(value: TypedCallNode, level: int) -> str:
    """Compute typed call node for AST diagnostic output."""
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
    """Compute typed function node for AST diagnostic output."""
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
    """Compute function overload typing for AST diagnostic output."""
    lines = [f"FunctionOverloadTyping(type={value.typ}, body=["]
    for node in value.body:
        lines.extend(_indent(_pretty(node, level + 1).splitlines()))
    lines.append("])")
    return "\n".join(lines)


def _function_node(value: FunctionNode, level: int) -> str:
    """Compute function node for AST diagnostic output."""
    lines = [
        "FunctionNode(",
        f"  params={_params_label(value.params)},",
        f"  returns={_types_label(value.returns)},",
        f"  overloads={value.overloads!r},",
        "  body=[",
    ]
    for node in value.body:
        lines.extend(_indent(_pretty(node, level + 1).splitlines(), 2))
    lines.extend(["  ]", ")"])
    if value.location is not None:
        lines.insert(1, f"  location={_location_label(value)},")
    return "\n".join(lines)


def _try_node(value: TryNode, level: int) -> str:
    """Compute try node for AST diagnostic output."""
    lines = ["TryNode(", "  body=["]
    for node in value.body:
        lines.extend(_indent(_pretty(node, level + 1).splitlines(), 2))
    lines.append("  ],")
    lines.append("  handlers=[")
    for handler in value.handlers:
        lines.extend(_indent(_pretty(handler, level + 1).splitlines(), 2))
    lines.extend(["  ]", ")"])
    return "\n".join(lines)


def _try_handler_node(value: TryHandlerNode, level: int) -> str:
    """Compute try handler node for AST diagnostic output."""
    lines = [f"TryHandlerNode(type={_type_label(value.typ)}, body=["]
    for node in value.body:
        lines.extend(_indent(_pretty(node, level + 1).splitlines()))
    lines.append("])")
    return "\n".join(lines)


def _params_label(params: tuple[FunctionParam, ...] | None) -> str:
    """Compute params label for AST diagnostic output."""
    if params is None:
        return "infer"
    return "[" + ", ".join(_param_label(param) for param in params) + "]"


def _param_label(param: FunctionParam) -> str:
    """Compute param label for AST diagnostic output."""
    name = "_" if param.name is None else str(param.name)
    typ = "infer" if param.typ is None else str(param.typ)
    if not param.default:
        return f"{name}: {typ}"
    return f"{name}: {typ} = {_nodes_label(param.default)}"


def _call_argument_label(arg: CallArgument) -> str:
    """Invoke argument label for AST diagnostic output."""
    if arg.placeholder:
        return "_"
    value = _nodes_label(arg.value)
    if arg.name is None:
        return value
    return f"{arg.name} = {value}"


def _nodes_label(nodes: tuple[ASTNode, ...]) -> str:
    """Compute nodes label for AST diagnostic output."""
    if len(nodes) == 1:
        return _pretty(nodes[0], 0)
    return "[" + ", ".join(_pretty(node, 0) for node in nodes) + "]"


def _types_label(types: tuple[Type, ...] | None) -> str:
    """Compute types label for AST diagnostic output."""
    if types is None:
        return "infer"
    return "[" + ", ".join(str(typ) for typ in types) + "]"


def _type_label(typ: Type | None) -> str:
    """Compute type label for AST diagnostic output."""
    return "untyped" if typ is None else str(typ)


def _location_arg(node: ASTNode) -> str:
    """Compute location arg for AST diagnostic output."""
    if node.location is None:
        return ""
    return f", location={_location_label(node)}"


def _location_label(node: ASTNode) -> str:
    """Compute location label for AST diagnostic output."""
    location = node.location
    if location is None:
        return "unknown"
    return f"{location.line}:{location.column}"


def _tag_label(node: TagApplicationNode) -> str:
    """Compute tag label for AST diagnostic output."""
    prefix = "#!" if node.tag.absent else "#"
    depth = "+" * node.tag.depth
    return f"{prefix}{node.tag.name}{depth}"


def _shuffle_label(label: Symbol | None) -> str:
    """Compute shuffle label for AST diagnostic output."""
    return "_" if label is None else str(label)


def _indent(lines: list[str], spaces: int = 2) -> list[str]:
    """Compute indent for AST diagnostic output."""
    prefix = " " * spaces
    return [prefix + line for line in lines]
