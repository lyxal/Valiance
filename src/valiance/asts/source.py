"""Small source emitter for analysed Valiance programs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

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
    SetVariablesNode,
    StringLiteralNode,
    TypedFunctionNode,
    TypedNode,
)
from valiance.types import FunctionType, Type, normalize, show


def typed_source(
    value: Sequence[ASTNode | TypedNode],
    source: str | None = None,
) -> str:
    """Render an analysed program as source with available type annotations."""
    if source is not None:
        rendered = _annotate_original_source(value, source)
        if rendered is not None:
            return rendered
    return "\n".join(_typed_node_source(node) for node in value)


def _typed_node_source(node: ASTNode | TypedNode) -> str:
    if isinstance(node, TypedFunctionNode):
        return _typed_function_source(node)
    if isinstance(node, TypedNode):
        return _node_source(node.node)
    return _node_source(node)


def _typed_function_source(node: TypedFunctionNode) -> str:
    ast = node.node
    typ = normalize(node.typ) if node.typ is not None else None
    if isinstance(ast, DefineNode) and isinstance(typ, FunctionType):
        signature = _function_signature(ast.function, typ, definition=True)
        return (
            f"define {ast.name}{signature} => "
            f"{_body_source(ast.function.body)}"
        )
    if isinstance(ast, FunctionNode) and isinstance(typ, FunctionType):
        return f"fn{_function_signature(ast, typ)} => {_body_source(ast.body)}"
    return _typed_node_source(TypedNode(ast, node.typ))


def _function_signature(
    node: FunctionNode,
    typ: FunctionType,
    *,
    definition: bool = False,
) -> str:
    params = typ.params or ()
    returns = typ.returns or ()
    if definition and node.params is None and not params:
        param_clause = ""
    else:
        param_clause = f"({_params_source(node.params, params)})"
    return param_clause + _return_clause(returns)


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


def _return_clause(returns: tuple[Type, ...]) -> str:
    if not returns:
        return " ->"
    return " -> " + ", ".join(show(ret) for ret in returns)


@dataclass(frozen=True)
class _Replacement:
    start: int
    end: int
    text: str


def _annotate_original_source(
    value: Sequence[ASTNode | TypedNode],
    source: str,
) -> str | None:
    from valiance.parsing.lexer import lex

    try:
        tokens = lex(source)
    except Exception:
        return None
    replacements: list[_Replacement] = []
    for node in value:
        replacements.extend(_function_replacements(node, source, tokens))
    if not replacements:
        return source
    replacements.sort(key=lambda item: item.start, reverse=True)
    rendered = source
    last_start = len(source) + 1
    for replacement in replacements:
        if replacement.end > last_start:
            continue
        rendered = (
            rendered[: replacement.start]
            + replacement.text
            + rendered[replacement.end :]
        )
        last_start = replacement.start
    return rendered


def _function_replacements(
    node: ASTNode | TypedNode,
    source: str,
    tokens: Sequence[Any],
) -> list[_Replacement]:
    replacements: list[_Replacement] = []
    if isinstance(node, TypedFunctionNode):
        replacements.extend(_signature_replacements(node, source, tokens))
        for overload in node.overloads:
            for child in overload.body:
                replacements.extend(_function_replacements(child, source, tokens))
        return replacements
    if isinstance(node, TypedNode):
        node = node.node
    replacements.extend(_raw_function_replacements(node, source, tokens))
    return replacements


def _raw_function_replacements(
    node: ASTNode,
    source: str,
    tokens: Sequence[Any],
) -> list[_Replacement]:
    replacements: list[_Replacement] = []
    if isinstance(node, DefineNode):
        replacements.extend(_raw_function_replacements(node.function, source, tokens))
    elif isinstance(node, FunctionNode):
        for child in node.body:
            replacements.extend(_raw_function_replacements(child, source, tokens))
    elif isinstance(node, ListLiteralNode):
        for item in node.items:
            for child in item:
                replacements.extend(_raw_function_replacements(child, source, tokens))
    return replacements


def _signature_replacements(
    node: TypedFunctionNode,
    source: str,
    tokens: Sequence[Any],
) -> list[_Replacement]:
    from valiance.parsing.lexer import TokenKind

    ast = node.node
    if isinstance(ast, DefineNode):
        function = ast.function
    elif isinstance(ast, FunctionNode):
        function = ast
    else:
        return []
    typ = normalize(node.typ) if node.typ is not None else None
    if not isinstance(typ, FunctionType) or function.location is None:
        return []

    params = typ.params or ()
    returns = typ.returns or ()
    replace_params = _needs_parameter_annotations(function, params)
    replace_returns = function.returns is None
    if not replace_params and not replace_returns:
        return []

    start = function.location.offset
    fat_arrow_index = _function_fat_arrow_index(tokens, start)
    if fat_arrow_index is None:
        return []
    fat_arrow = tokens[fat_arrow_index]
    param_span = _params_span(tokens, start, fat_arrow_index)
    param_text = f"({_params_source(function.params, params)})"
    replacements: list[_Replacement] = []
    if replace_params and param_span is not None:
        replacements.append(_Replacement(param_span[0], param_span[1], param_text))

    arrow_search_start = param_span[1] if param_span is not None else start
    arrow_index = _last_token_index(
        tokens,
        TokenKind.ARROW,
        arrow_search_start,
        fat_arrow.offset,
    )
    return_insert = _return_insert_token(tokens, start, fat_arrow_index)
    return_insert_start = _leading_whitespace_start(source, return_insert.offset)

    if not replace_returns:
        if replace_params and param_span is None:
            if arrow_index is None:
                insert_at = return_insert_start
            else:
                insert_at = _leading_whitespace_start(
                    source,
                    tokens[arrow_index].offset,
                )
            replacements.append(_Replacement(insert_at, insert_at, param_text))
        return replacements

    prefix = param_text if replace_params and param_span is None else ""
    return_text = _return_clause(returns)
    if arrow_index is None:
        replacements.append(
            _Replacement(
                return_insert_start,
                return_insert.offset,
                f"{prefix}{return_text} ",
            )
        )
    else:
        return_start = _leading_whitespace_start(source, tokens[arrow_index].offset)
        replacements.append(
            _Replacement(
                return_start,
                return_insert.offset,
                f"{prefix}{return_text} ",
            )
        )
    return replacements


def _needs_parameter_annotations(
    function: FunctionNode,
    inferred_params: tuple[Type, ...],
) -> bool:
    if function.params is None:
        return bool(inferred_params)
    return any(param.typ is None for param in function.params)


def _function_fat_arrow_index(
    tokens: Sequence[Any],
    start: int,
) -> int | None:
    from valiance.parsing.lexer import TokenKind

    paren_depth = 0
    square_depth = 0
    brace_depth = 0
    for index, token in enumerate(tokens):
        if token.offset < start:
            continue
        if token.kind is TokenKind.LPAREN:
            paren_depth += 1
        elif token.kind is TokenKind.RPAREN:
            paren_depth -= 1
        elif token.kind is TokenKind.LBRACKET:
            square_depth += 1
        elif token.kind is TokenKind.RBRACKET:
            square_depth -= 1
        elif token.kind is TokenKind.LBRACE:
            brace_depth += 1
        elif token.kind is TokenKind.RBRACE:
            brace_depth -= 1
        elif (
            token.kind is TokenKind.FAT_ARROW
            and paren_depth == 0
            and square_depth == 0
            and brace_depth == 0
        ):
            return index
    return None


def _token_index(
    tokens: Sequence[Any],
    kind: Any,
    start: int,
    end: int | None = None,
) -> int | None:
    for index, token in enumerate(tokens):
        if token.offset < start:
            continue
        if end is not None and token.offset >= end:
            return None
        if token.kind is kind:
            return index
    return None


def _last_token_index(
    tokens: Sequence[Any],
    kind: Any,
    start: int,
    end: int,
) -> int | None:
    from valiance.parsing.lexer import TokenKind

    found = None
    paren_depth = 0
    square_depth = 0
    brace_depth = 0
    for index, token in enumerate(tokens):
        if token.offset < start:
            continue
        if token.offset >= end:
            break
        if token.kind is TokenKind.LPAREN:
            paren_depth += 1
        elif token.kind is TokenKind.RPAREN:
            paren_depth -= 1
        elif token.kind is TokenKind.LBRACKET:
            square_depth += 1
        elif token.kind is TokenKind.RBRACKET:
            square_depth -= 1
        elif token.kind is TokenKind.LBRACE:
            brace_depth += 1
        elif token.kind is TokenKind.RBRACE:
            brace_depth -= 1
        elif (
            token.kind is kind
            and paren_depth == 0
            and square_depth == 0
            and brace_depth == 0
        ):
            found = index
    return found


def _params_span(
    tokens: Sequence[Any],
    start: int,
    fat_arrow_index: int,
) -> tuple[int, int] | None:
    from valiance.parsing.lexer import TokenKind

    square_depth = 0
    open_index = None
    for index, token in enumerate(tokens[:fat_arrow_index]):
        if token.offset < start:
            continue
        if token.kind is TokenKind.LBRACKET:
            square_depth += 1
        elif token.kind is TokenKind.RBRACKET:
            square_depth -= 1
        elif token.kind is TokenKind.LPAREN and square_depth == 0:
            open_index = index
            break
    if open_index is None:
        return None
    depth = 0
    for token in tokens[open_index : fat_arrow_index + 1]:
        if token.kind is TokenKind.LPAREN:
            depth += 1
        elif token.kind is TokenKind.RPAREN:
            depth -= 1
            if depth == 0:
                return tokens[open_index].offset, token.offset + len(token.value)
    return None


def _return_insert_token(
    tokens: Sequence[Any],
    start: int,
    fat_arrow_index: int,
) -> Any:
    where_index = _where_index(tokens, start, fat_arrow_index)
    return tokens[where_index] if where_index is not None else tokens[fat_arrow_index]


def _where_index(
    tokens: Sequence[Any],
    start: int,
    fat_arrow_index: int,
) -> int | None:
    from valiance.parsing.lexer import TokenKind

    paren_depth = 0
    square_depth = 0
    brace_depth = 0
    for index, token in enumerate(tokens[:fat_arrow_index]):
        if token.offset < start:
            continue
        if token.kind is TokenKind.LPAREN:
            paren_depth += 1
        elif token.kind is TokenKind.RPAREN:
            paren_depth -= 1
        elif token.kind is TokenKind.LBRACKET:
            square_depth += 1
        elif token.kind is TokenKind.RBRACKET:
            square_depth -= 1
        elif token.kind is TokenKind.LBRACE:
            brace_depth += 1
        elif token.kind is TokenKind.RBRACE:
            brace_depth -= 1
        elif (
            token.kind is TokenKind.IDENT
            and token.value == "where"
            and paren_depth == 0
            and square_depth == 0
            and brace_depth == 0
        ):
            return index
    return None


def _leading_whitespace_start(source: str, offset: int) -> int:
    index = offset
    while index > 0 and source[index - 1] in " \t":
        index -= 1
    return index


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
        declared = "" if node.declared_type is None else f": {show(node.declared_type)}"
        prefix = "const " if node.constant else ""
        return f"{prefix}${node.name}{declared} ="
    if isinstance(node, SetVariablesNode):
        targets = []
        for target in node.targets:
            declared = (
                ""
                if target.declared_type is None
                else f": {show(target.declared_type)}"
            )
            targets.append(f"{target.name}{declared}")
        prefix = "const " if any(target.constant for target in node.targets) else ""
        return f"{prefix}$({', '.join(targets)}) ="
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
