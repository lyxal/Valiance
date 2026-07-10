"""Raw and typed abstract-syntax-tree node definitions."""

from __future__ import annotations

from dataclasses import dataclass, field

from valiance.symbols import Symbol
from valiance.types import (
    AppliedOverload,
    DataTag,
    ElementTag,
    Type,
    UnionDispatchPlan,
)


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
class TypedLiteralNode(TypedNode):
    """A literal whose item expressions retain their typed child nodes."""

    items: tuple[tuple[TypedNode, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class TypedElementNode(TypedNode):
    """A typed element application with its compile-time resolved overload."""

    overload: AppliedOverload | None = None
    overload_index: int | None = None
    modifier_args: tuple[TypedFunctionNode, ...] = ()
    call_arg_order: tuple[int, ...] = ()
    call_overload_index: int | None = None
    extension: TypedElementExtension | None = None


@dataclass(frozen=True, slots=True)
class TypedCallNode(TypedNode):
    """A typed call expression with its compile-time resolved callable overload."""

    overload: AppliedOverload | None = None


@dataclass(frozen=True, slots=True)
class TypedTagApplicationNode(TypedNode):
    """A typed data-tag application with an optional runtime validator."""

    validator: AppliedOverload | None = None
    validator_index: int | None = None
    added_tags: tuple[DataTag, ...] = ()
    removed_tags: tuple[DataTag, ...] = ()


@dataclass(frozen=True, slots=True)
class TypedIfNode(TypedNode):
    """A typed conditional retaining the analysed condition and branches."""

    condition: tuple[TypedNode, ...] = ()
    then_branch: tuple[TypedNode, ...] = ()
    else_branch: tuple[TypedNode, ...] = ()


@dataclass(frozen=True, slots=True)
class TypedUnfoldNode(TypedNode):
    """A typed unfold expression with its selected state and body typing."""

    state_arity: int = 0
    function: TypedFunctionNode | None = None


@dataclass(frozen=True, slots=True)
class TypedAtNode(TypedNode):
    """A typed at expression with its callable body and vectorisation plan."""

    function: TypedFunctionNode | None = None
    overload: AppliedOverload | None = None
    function_overload_index: int = 0


@dataclass(frozen=True, slots=True)
class FunctionOverloadTyping:
    typ: Type
    body: tuple[TypedNode, ...]
    overload: object | None = None


@dataclass(frozen=True, slots=True)
class TypedFunctionNode(TypedNode):
    overloads: tuple[FunctionOverloadTyping, ...] = ()
    dispatch_plan: UnionDispatchPlan | None = None


@dataclass(frozen=True)
class FunctionParam:
    """A function literal parameter annotation."""

    name: Symbol | None = None
    typ: Type | None = None
    default: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class CallArgument:
    """One explicit element-call argument."""

    name: Symbol | None = None
    value: tuple[ASTNode, ...] = ()
    placeholder: bool = False


@dataclass(frozen=True)
class ExtensionPatternRule:
    """One missing/present argument pattern and its substitution function."""

    pattern: tuple[Symbol | None, ...]
    function: FunctionNode


@dataclass(frozen=True)
class ElementExtension(ASTNode):
    """Vectorisation length-mismatch handling attached to an element call."""

    default: FunctionNode | None = None
    rules: tuple[ExtensionPatternRule, ...] = ()
    selector: FunctionNode | None = None


@dataclass(frozen=True, slots=True)
class TypedExtensionPatternRule:
    pattern: tuple[Symbol | None, ...]
    function: TypedFunctionNode


@dataclass(frozen=True, slots=True)
class TypedElementExtension:
    default: TypedFunctionNode | None = None
    rules: tuple[TypedExtensionPatternRule, ...] = ()
    selector: TypedFunctionNode | None = None


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
    disambiguation: tuple[Type | None, ...] = ()
    call_args: tuple[CallArgument, ...] = ()
    annotations: tuple[ASTNode, ...] = ()
    extension: ElementExtension | None = None


@dataclass(frozen=True)
class TagApplicationNode(ASTNode):
    """Apply or remove a data tag from the top stack value."""

    tag: DataTag


@dataclass(frozen=True)
class TagDeclarationNode(ASTNode):
    """Declare a data tag, variant tag, or disjoint tag relationship."""

    tag: DataTag
    kind: Symbol | None = None
    parent: DataTag | None = None
    disjoint: DataTag | Symbol | None = None
    visibility: Symbol | None = None


@dataclass(frozen=True)
class TagOverlayNode(ASTNode):
    """Declare tag-aware signatures for existing element behavior."""

    tag: DataTag
    elements: tuple[Symbol, ...] = ()
    signatures: tuple[tuple[tuple[Type, ...], tuple[Type, ...]], ...] = ()
    generics: tuple[Symbol, ...] = ()
    visibility: Symbol | None = None


@dataclass(frozen=True)
class CastNode(ASTNode):
    """Treat the top stack value as a target type."""

    typ: Type
    checked: bool = False


@dataclass(frozen=True)
class StackShuffleNode(ASTNode):
    """Copy or move labelled values from the top stack segment."""

    mode: Symbol
    prestack: tuple[Symbol | None, ...] = ()
    poststack: tuple[Symbol, ...] = ()


@dataclass(frozen=True)
class FunctionNode(ASTNode):
    """A function literal."""

    generics: tuple[Symbol, ...] = ()
    generic_variances: tuple[Symbol | None, ...] = ()
    params: tuple[FunctionParam, ...] | None = None
    body: tuple[ASTNode, ...] = ()
    returns: tuple[Type, ...] | None = None
    where_clause: tuple[ASTNode, ...] = ()
    element_tags: frozenset[ElementTag] = field(default_factory=frozenset[ElementTag])
    annotations: tuple[ASTNode, ...] = ()
    element_tags_explicit: bool = field(default=False, compare=False)
    companion_tags_allowed: frozenset[ElementTag] = field(
        default_factory=frozenset[ElementTag],
        compare=False,
    )
    generic_constraints: tuple[Type | None, ...] = ()


@dataclass(frozen=True)
class GetVariableNode(ASTNode):
    """A variable reference."""

    name: Symbol


@dataclass(frozen=True)
class SetVariableNode(ASTNode):
    """Assign the top stack value to a variable."""

    name: Symbol
    declared_type: Type | None = None
    constant: bool = False


@dataclass(frozen=True)
class SetVariablesNode(ASTNode):
    """Assign a stack segment to multiple variables."""

    targets: tuple[SetVariableNode, ...] = ()


@dataclass(frozen=True)
class FieldAccessNode(ASTNode):
    """Read an attribute from the top stack value."""

    name: Symbol


@dataclass(frozen=True)
class FieldSetNode(ASTNode):
    """Set an attribute on the top stack value."""

    name: Symbol


@dataclass(frozen=True)
class IndexSelector:
    """One index or slice selector inside an indexing expression."""

    start: tuple[ASTNode, ...] = ()
    stop: tuple[ASTNode, ...] = ()
    step: tuple[ASTNode, ...] = ()
    is_slice: bool = False


@dataclass(frozen=True)
class IndexAccessNode(ASTNode):
    """Read item(s) from the top stack value."""

    selectors: tuple[IndexSelector, ...] = ()
    spread: bool = False


@dataclass(frozen=True)
class IndexSetNode(ASTNode):
    """Return a copy of the receiver with an indexed item replaced."""

    selectors: tuple[IndexSelector, ...] = ()


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
class AssertNode(ASTNode):
    """An assertion with an optional else value."""

    condition: tuple[ASTNode, ...] = ()
    else_branch: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class WhileNode(ASTNode):
    """A while loop."""

    condition: tuple[ASTNode, ...] = ()
    params: tuple[FunctionParam, ...] | None = None
    body: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class UnfoldNode(ASTNode):
    """A lazy unfold expression."""

    condition: tuple[ASTNode, ...] = ()
    params: tuple[FunctionParam, ...] | None = None
    body: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class TryHandlerNode(ASTNode):
    """One panic handler inside a try expression."""

    typ: Type | None = None
    body: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class TryNode(ASTNode):
    """A panic-catching try/handle expression."""

    body: tuple[ASTNode, ...] = ()
    handlers: tuple[TryHandlerNode, ...] = ()


@dataclass(frozen=True)
class AtLevel:
    """One vectorisation stop level in an at expression."""

    name: Symbol
    depth: int = 0


@dataclass(frozen=True)
class AtNode(ASTNode):
    """Apply a body with explicit vectorisation stop levels."""

    levels: tuple[AtLevel, ...] = ()
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
    generics: tuple[Symbol, ...] = ()
    generic_variances: tuple[Symbol | None, ...] = ()
    generic_constraints: tuple[Type | None, ...] = ()
    attached_tag: DataTag | None = field(default=None, compare=False)


@dataclass(frozen=True)
class ElementTagDeclarationNode(ASTNode):
    """A declaration or disjoint rule for function/element tags."""

    name: Symbol
    kind: Symbol | None = None
    disjoint: Symbol | DataTag | None = None
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
    signature: tuple[Type, ...] | None = None
    exclusions: tuple[tuple[Type, ...], ...] = ()
    kind: Symbol | None = None
    trait: Symbol | None = None


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
    kwargs: tuple[tuple[Symbol, ASTNode], ...] = ()


@dataclass(frozen=True)
class ReturnNode(ASTNode):
    """Return early from the current function."""

    values: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class ListLiteralNode(ASTNode):
    """A list literal whose items are stack expressions."""

    items: tuple[tuple[ASTNode, ...], ...] = ()
    typ: Type | None = None


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
    generic_variances: tuple[Symbol | None, ...] = ()
    generic_constraints: tuple[Type | None, ...] = ()
    visibility: Symbol | None = None
