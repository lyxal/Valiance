from __future__ import annotations

from dataclasses import dataclass

from valiance.types import Type


class ASTNode:
    """Base class for all AST nodes."""

    pass


@dataclass(frozen=True, slots=True)
class TypedNode:
    node: ASTNode
    typ: Type | None = None


@dataclass(frozen=True, slots=True)
class FunctionOverloadTyping:
    typ: Type
    body: tuple[TypedNode, ...]


@dataclass(frozen=True, slots=True)
class TypedFunctionNode(TypedNode):
    overloads: tuple[FunctionOverloadTyping, ...] = ()


@dataclass(frozen=True)
class FunctionParam:
    """A function literal parameter annotation."""

    name: str | None = None
    typ: Type | None = None


@dataclass(frozen=True)
class NumberLiteralNode(ASTNode):
    """A numeric literal."""

    value: str


@dataclass(frozen=True)
class StringLiteralNode(ASTNode):
    """A string literal"""

    value: str


@dataclass(frozen=True)
class ElementNode(ASTNode):
    """An element, such as an operator or function name."""

    name: str


@dataclass(frozen=True)
class FunctionNode(ASTNode):
    """A function literal."""

    params: tuple[FunctionParam, ...] | None = None
    body: tuple[ASTNode, ...] = ()
    returns: tuple[Type, ...] | None = None
