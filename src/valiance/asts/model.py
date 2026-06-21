from dataclasses import dataclass

from valiance.types import Type


class ASTNode:
    """Base class for all AST nodes."""

    pass


@dataclass(frozen=True, slots=True)
class TypedNode:
    node: ASTNode
    typ: Type | None = None


@dataclass(frozen=True)
class NumberLiteralNode(ASTNode):
    """A numeric literal."""

    value: str


@dataclass(frozen=True)
class ElementNode(ASTNode):
    """An element, such as an operator or function name."""

    name: str
