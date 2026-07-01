from __future__ import annotations

from dataclasses import dataclass, field

from valiance.symbols import Symbol
from valiance.types import AppliedOverload, DataTag, Type


@dataclass(frozen=True)
class SourceLocation:
    """A source position for parser-produced AST nodes."""

    line: int
    column: int
    offset: int


@dataclass(frozen=True)
class ASTNode:
    """Base class for all AST nodes."""

    location: SourceLocation | None = field(
        default=None,
        compare=False,
        kw_only=True,
    )


@dataclass(frozen=True, slots=True)
class TypedNode:
    node: ASTNode
    typ: Type | None = None


@dataclass(frozen=True, slots=True)
class TypedElementNode(TypedNode):
    """A typed element application with its compile-time resolved overload."""

    overload: AppliedOverload | None = None
    overload_index: int | None = None
    modifier_args: tuple[TypedFunctionNode, ...] = ()


@dataclass(frozen=True, slots=True)
class TypedCallNode(TypedNode):
    """A typed call expression with its compile-time resolved callable overload."""

    overload: AppliedOverload | None = None


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
class StringInterpolationNode(ASTNode):
    """A string literal with embedded expressions."""

    parts: tuple[str | tuple[ASTNode, ...], ...] = ()


@dataclass(frozen=True)
class ElementNode(ASTNode):
    """An element, such as an operator or function name."""

    name: Symbol
    modifier_args: tuple[FunctionNode, ...] = ()


@dataclass(frozen=True)
class TagApplicationNode(ASTNode):
    """Apply or remove a data tag from the top stack value."""

    tag: DataTag


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


@dataclass(frozen=True)
class DefineNode(ASTNode):
    """A named element/function definition."""

    name: Symbol
    function: FunctionNode
    annotations: tuple[ASTNode, ...] = ()
    is_multi: bool = False
    visibility: Symbol | None = None


@dataclass(frozen=True)
class ObjectFieldNode(ASTNode):
    """One field/member declared by an object-like type."""

    name: Symbol
    typ: Type | None = None
    default: tuple[ASTNode, ...] = ()
    access: Symbol = Symbol("readable")


@dataclass(frozen=True)
class TraitRequirementNode(ASTNode):
    """An element signature required by a trait or variant."""

    name: Symbol
    params: tuple[FunctionParam, ...] | None = None
    returns: tuple[Type, ...] | None = None


@dataclass(frozen=True)
class VariantMemberNode(ASTNode):
    """One closed member of a variant declaration."""

    name: Symbol
    fields: tuple[ObjectFieldNode, ...] = ()
    definitions: tuple[DefineNode, ...] = ()


@dataclass(frozen=True)
class EnumMemberNode(ASTNode):
    """One member of an enum declaration."""

    name: Symbol
    value: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class ImportPath:
    """A parsed module path."""

    parts: tuple[str, ...]
    root: Symbol | None = None


@dataclass(frozen=True)
class ImportComponent:
    """One component selected from an imported module."""

    name: Symbol
    alias: Symbol | None = None


@dataclass(frozen=True)
class ImportSpec:
    """One import clause inside an import block."""

    path: ImportPath
    alias: Symbol | None = None
    components: tuple[ImportComponent, ...] = ()


@dataclass(frozen=True)
class ImportNode(ASTNode):
    """A module import declaration."""

    specs: tuple[ImportSpec, ...] = ()
    public: bool = False


@dataclass(frozen=True)
class AnnotationNode(ASTNode):
    """A compile-time annotation applied to the following structure."""

    name: Symbol
    args: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class ReturnNode(ASTNode):
    """Return early from the current function."""

    values: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class ListLiteralNode(ASTNode):
    """A list literal whose items are stack expressions."""

    items: tuple[tuple[ASTNode, ...], ...] = ()


@dataclass(frozen=True)
class TupleLiteralNode(ASTNode):
    """A tuple literal whose items are stack expressions."""

    items: tuple[tuple[ASTNode, ...], ...] = ()


@dataclass(frozen=True)
class ArrayLiteralNode(ASTNode):
    """An array literal whose items are stack expressions."""

    items: tuple[tuple[ASTNode, ...], ...] = ()


@dataclass(frozen=True)
class RecordLiteralNode(ASTNode):
    """A record literal with static field names."""

    fields: tuple[tuple[Symbol, tuple[ASTNode, ...]], ...] = ()


@dataclass(frozen=True)
class DictLiteralNode(ASTNode):
    """A dictionary literal with expression keys and values."""

    entries: tuple[tuple[tuple[ASTNode, ...], tuple[ASTNode, ...]], ...] = ()


@dataclass(frozen=True)
class MatchPatternNode(ASTNode):
    """Base class for match case patterns."""


@dataclass(frozen=True)
class LiteralPatternNode(MatchPatternNode):
    value: ASTNode


@dataclass(frozen=True)
class ExpressionPatternNode(MatchPatternNode):
    expression: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class GuardPatternNode(MatchPatternNode):
    condition: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class WildcardPatternNode(MatchPatternNode):
    pass


@dataclass(frozen=True)
class RestPatternNode(MatchPatternNode):
    name: Symbol | None = None


@dataclass(frozen=True)
class BindingPatternNode(MatchPatternNode):
    name: Symbol
    pattern: MatchPatternNode


@dataclass(frozen=True)
class OrPatternNode(MatchPatternNode):
    options: tuple[MatchPatternNode, ...] = ()


@dataclass(frozen=True)
class ListPatternNode(MatchPatternNode):
    items: tuple[MatchPatternNode, ...] = ()


@dataclass(frozen=True)
class TypePatternNode(MatchPatternNode):
    typ: Type | None = None
    name: Symbol | None = None
    fields: tuple[MatchPatternNode, ...] = ()
    guard: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class MatchCaseNode(ASTNode):
    """One branch of a match expression."""

    patterns: tuple[MatchPatternNode, ...] = ()
    pattern: tuple[ASTNode, ...] = ()
    pattern_type: Type | None = None
    is_default: bool = False
    body: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class MatchNode(ASTNode):
    """A match control-flow structure."""

    cases: tuple[MatchCaseNode, ...] = ()


@dataclass(frozen=True)
class ObjectNode(ASTNode):
    """An object, trait, or variant-like top-level declaration."""

    kind: Symbol
    name: Symbol
    generics: tuple[Symbol, ...] = ()
    target: Type | None = None
    fields: tuple[ObjectFieldNode, ...] = ()
    definitions: tuple[DefineNode, ...] = ()
    requirements: tuple[TraitRequirementNode, ...] = ()
    variants: tuple[VariantMemberNode, ...] = ()
    enum_members: tuple[EnumMemberNode, ...] = ()
    annotations: tuple[ASTNode, ...] = ()
