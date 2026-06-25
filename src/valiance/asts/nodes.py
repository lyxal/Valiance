from __future__ import annotations

from dataclasses import dataclass

from valiance.symbols import Symbol
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

    name: Symbol | None = None
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

    name: Symbol


@dataclass(frozen=True)
class FunctionNode(ASTNode):
    """A function literal."""

    params: tuple[FunctionParam, ...] | None = None
    body: tuple[ASTNode, ...] = ()
    returns: tuple[Type, ...] | None = None


@dataclass(frozen=True)
class GetVariableNode(ASTNode):
    """A variable reference."""

    name: Symbol


@dataclass(frozen=True)
class SetVariableNode(ASTNode):
    """Assign the top stack value to a variable."""

    name: Symbol


@dataclass(frozen=True)
class FieldAccessNode(ASTNode):
    """Read an attribute from the top stack value."""

    name: Symbol


@dataclass(frozen=True)
class FieldSetNode(ASTNode):
    """Set an attribute on the top stack value."""

    name: Symbol


@dataclass(frozen=True)
class CallNode(ASTNode):
    """Call a function with provided arguments, falling back
    to taking values from the stack."""

    args: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class IfNode(ASTNode):
    """A conditional statement."""

    condition: tuple[ASTNode, ...] = ()
    then_branch: tuple[ASTNode, ...] = ()
    else_branch: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class WhileNode(ASTNode):
    """A while loop."""

    condition: tuple[ASTNode, ...] = ()
    body: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class ForNode(ASTNode):
    """A for loop."""

    variable: Symbol
    index_variable: Symbol | None = None
    body: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class BreakNode(ASTNode):
    """Break from a while/for loop with optional value(s)"""

    values: tuple[ASTNode, ...] = ()
