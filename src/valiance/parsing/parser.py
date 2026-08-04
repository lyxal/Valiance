"""Recursive-descent parser for Valiance source."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace

from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
    AssertNode,
    ASTNode,
    AtLevel,
    AtNode,
    BindingPatternNode,
    BreakNode,
    CallArgument,
    CastNode,
    DefineNode,
    DictLiteralNode,
    ElementExtension,
    ElementNode,
    ElementTagDeclarationNode,
    EnumMemberNode,
    ExpressionPatternNode,
    ExtensionPatternRule,
    FieldAccessNode,
    FieldSetNode,
    FileLintSuppressionNode,
    ForNode,
    FunctionNode,
    FunctionParam,
    GetVariableNode,
    GuardPatternNode,
    IfNode,
    ImportComponent,
    ImportNode,
    ImportPath,
    ImportSpec,
    IndexAccessNode,
    IndexSelector,
    IndexSetNode,
    ListLiteralNode,
    ListPatternNode,
    LintSuppressionNode,
    LiteralPatternNode,
    MatchCaseNode,
    MatchNode,
    MinimumRankNode,
    MatchPatternNode,
    NumberLiteralNode,
    ObjectFieldNode,
    ObjectNode,
    OverloadSignature,
    PopNNode,
    OrPatternNode,
    RecordLiteralNode,
    RestPatternNode,
    ReturnNode,
    SetVariableNode,
    SetVariablesNode,
    SourceLocation,
    StackShuffleNode,
    StringInterpolationNode,
    StringLiteralNode,
    Symbol,
    TagApplicationNode,
    TagDeclarationNode,
    TagOverlayNode,
    TraitRequirementNode,
    TryHandlerNode,
    TryNode,
    TupleLiteralNode,
    TypeLiteralNode,
    TypePatternNode,
    UnfoldNode,
    VariantMemberNode,
    WhileNode,
    WildcardPatternNode,
)
from valiance.analysis.diagnostics import DiagnosticError
from valiance.parsing.lexer import LexError, Token, TokenKind, lex, lex_with_diagnostics
from valiance.vtypes import (
    AnonymousTrait,
    AnonymousTraitRequirement,
    ArrayExactType,
    ArrayMinType,
    NoVec,
    ExactType,
    C,
    CollectionType,
    DataTag,
    ElementTag,
    Exact,
    ExactTags,
    NoVecType,
    Field,
    Fn,
    FunctionType,
    I,
    IntersectionType,
    ListExactType,
    ListMinType,
    ListRuggedType,
    N,
    NominalType,
    NoneType,
    Overload,
    RankVariable,
    Row,
    RowField,
    RowType,
    Tagged,
    TaggedType,
    Tup,
    TupleType,
    TupleTypeItem,
    TupVariadic,
    Type,
    U,
    UnionType,
    V,
    VariadicTupleType,
    VarType,
)


class ParseError(DiagnosticError, SyntaxError):
    """Raised when Valiance source cannot be parsed."""


class ParseErrors(ParseError):
    """A batch of independent lexer/parser diagnostics from one source file."""

    def __init__(self, errors: tuple[DiagnosticError, ...]) -> None:
        """Initialize a batch while retaining each source-located error."""
        if not errors:
            raise ValueError("ParseErrors requires at least one diagnostic")
        self.errors = errors
        first = errors[0]
        super().__init__(
            f"{len(errors)} syntax errors; first: {first.message}",
            line=first.line,
            column=first.column,
        )


@dataclass(frozen=True, slots=True)
class ParseResult:
    """Best-effort syntax result and every diagnostic produced while recovering."""

    nodes: tuple[ASTNode, ...]
    diagnostics: tuple[DiagnosticError, ...]


_EXPANDED_SKIP = object()


@dataclass(frozen=True, slots=True)
class _ChainPiece:
    nodes: tuple[ASTNode, ...]
    breaks_chain: bool = False
    is_element: bool = False


def parse(source: str) -> list[ASTNode]:
    """Parse Valiance source, reporting all reasonably recoverable errors."""
    result = parse_with_diagnostics(source)
    if result.diagnostics:
        if len(result.diagnostics) == 1:
            raise result.diagnostics[0]
        raise ParseErrors(result.diagnostics)
    return list(result.nodes)


def parse_with_diagnostics(source: str) -> ParseResult:
    """Return a best-effort AST together with lexer and parser diagnostics."""
    tokens, lex_errors = lex_with_diagnostics(source)
    # Lexical ERROR tokens identify already-reported spans.  Removing them lets
    # the parser continue from the next real token without duplicate messages.
    parse_tokens = [token for token in tokens if token.kind is not TokenKind.ERROR]
    parser = Parser(parse_tokens, recover=True)
    try:
        nodes = parser.parse_program()
    except RecursionError:
        token = parser._current
        parser.diagnostics.append(
            ParseError("source nesting is too deep", line=token.line, column=token.column)
        )
        nodes = parser.recovered_nodes
    diagnostics = tuple(sorted(
        (*lex_errors, *parser.diagnostics),
        key=lambda item: (item.line or 0, item.column or 0),
    ))
    return ParseResult(tuple(nodes), diagnostics)


def parse_type(source: str) -> Type:
    """Parse one Valiance type expression."""
    try:
        parser = Parser(lex(source))
        typ = parser.parse_type_expression()
        parser._skip_newlines()
        parser._expect(TokenKind.EOF)
        return typ
    except LexError as exc:
        # ``parse_type`` has historically exposed one syntax-error category to
        # type-expression callers even when tokenization found the problem.
        raise ParseError(exc.message, line=exc.line, column=exc.column) from exc
    except RecursionError as exc:
        raise ParseError("type nesting is too deep") from exc


class Parser:
    def __init__(self, tokens: Iterable[Token], *, recover: bool = False) -> None:
        """Initialize this parser."""
        self.tokens = list(tokens)
        self.index = 0
        self.recover = recover
        self.diagnostics: list[ParseError] = []
        self.recovered_nodes: list[ASTNode] = []
        self._allow_variadic_tuple_type = False
        self._where_clause_depth = 0
        self._temporary_index = 0

    def parse_program(self) -> list[ASTNode]:
        """Parse all top-level statements from the current token stream."""
        nodes: list[ASTNode] = []
        self._skip_newlines()
        while not self._check(TokenKind.EOF):
            before = self.index
            try:
                statement = self._statement()
                if not statement and self.index == before:
                    self._error("expected a statement or declaration")
                nodes.extend(statement)
                self.recovered_nodes = list(nodes)
                self._skip_separators()
            except ParseError as error:
                if not self.recover:
                    raise
                self.diagnostics.append(error)
                self._synchronize_statement(before)
                if len(self.diagnostics) >= 100:
                    token = self._current
                    self.diagnostics.append(ParseError(
                        "too many syntax errors; stopped after 100 diagnostics",
                        line=token.line, column=token.column,
                    ))
                    break
        return nodes

    def _statement(self) -> tuple[ASTNode, ...]:
        """Parse statement from the current token stream."""
        lint_directive = self._lint_suppression_directive()
        if lint_directive is not None:
            scope, codes, location = lint_directive
            if scope == "file":
                return (FileLintSuppressionNode(codes, location=location),)
            body = self._statement()
            if not body:
                self._error("@lintOff must be followed by a statement")
            return (LintSuppressionNode(body, codes, location=location),)

        overloads = self._overload_signatures()
        annotations = self._annotations()
        visibility: Symbol | None = None
        is_multi = False
        eager = False
        if self._match_ident("public", "private"):
            visibility = Symbol(self._previous.value)
        if overloads and (
            self._check_ident("import", "tag", "object", "trait", "variant", "enum")
            or (self._check(TokenKind.OP) and self._current.value.startswith("#"))
        ):
            self._error("overload must be followed by define or fn")
        if self._match_ident("import"):
            return (
                self._import(
                    self._previous,
                    public=visibility == Symbol("public"),
                ),
            )
        if self._match_ident("tag"):
            return (self._tag_declaration(self._previous, visibility),)
        if self._check(TokenKind.OP) and self._current.value.startswith("#"):
            if self._peek(1).kind is TokenKind.COLON:
                return (self._tag_overlay(self._current, visibility),)
        if self._match_ident("multi"):
            is_multi = True
        if self._match_ident("eager"):
            eager = True

        if self._match_ident("define"):
            return (
                self._define(
                    self._previous,
                    annotations,
                    visibility,
                    is_multi,
                    eager=eager,
                    overloads=overloads,
                ),
            )
        if eager:
            self._error("eager must be followed by define")
        if self._match_ident("object", "trait", "variant", "enum"):
            return (
                self._object_like(
                    self._previous,
                    self._previous.value,
                    annotations,
                    visibility,
                ),
            )
        if self._match_ident("fn"):
            return (self._function(self._previous, annotations, overloads=overloads),)
        if self._match_ident("if"):
            return (self._if(self._previous),)
        if self._match_ident("assert"):
            return (self._assert(self._previous),)
        if self._match_ident("while"):
            return (self._while(self._previous),)
        if self._match_ident("unfold"):
            return (self._unfold(self._previous),)
        if self._match_ident("at"):
            return (self._at(self._previous),)
        if self._match_ident("foreach"):
            return (self._foreach(self._previous),)
        if self._match_ident("break"):
            start = self._previous
            return (BreakNode(self._optional_values(), location=_loc(start)),)
        if self._match_ident("return"):
            start = self._previous
            return (ReturnNode(*self._return_values(), location=_loc(start)),)
        if self._match_ident("match"):
            return (self._match_node(self._previous),)
        if self._match_ident("try"):
            return (self._try(self._previous),)
        if self._match_ident("const"):
            return self._constant(self._previous)

        if overloads:
            self._error("overload must be followed by another overload, a comment, whitespace, define, or fn")
        if annotations:
            self._error("annotation must be followed by a declaration")
        return self._chain_until(_LINE_TERMINATORS)

    def _lint_suppression_directive(
        self,
    ) -> tuple[str, tuple[str, ...], SourceLocation] | None:
        """Parse a node- or file-scoped lint suppression annotation."""
        if not self._check(TokenKind.AT) or self._peek(1).kind is not TokenKind.IDENT:
            return None
        name = self._peek(1).value
        if name not in {"lintOff", "lintFileOff"}:
            return None
        start = self._advance()
        self._advance()
        codes: list[str] = []
        if self._match(TokenKind.LPAREN):
            self._skip_newlines()
            if not self._check(TokenKind.RPAREN):
                while True:
                    token = self._expect(TokenKind.STRING)
                    codes.append(token.value)
                    self._skip_newlines()
                    if not self._match(TokenKind.COMMA):
                        break
                    self._skip_newlines()
            self._expect(TokenKind.RPAREN)
        self._skip_newlines()
        return ("file" if name == "lintFileOff" else "node", tuple(codes), _loc(start))

    def _overload_signatures(self) -> tuple[OverloadSignature, ...]:
        """Parse overload signatures attached to the following define or fn."""
        overloads: list[OverloadSignature] = []
        while self._match_ident("overload"):
            self._expect(TokenKind.LPAREN)
            params: list[Type] = []
            returns: list[Type] = []
            self._skip_newlines()
            if not self._check(TokenKind.ARROW):
                while True:
                    params.append(self._parameter_type())
                    if not self._match(TokenKind.COMMA):
                        break
            self._expect(TokenKind.ARROW)
            if not self._check(TokenKind.RPAREN):
                while True:
                    returns.append(self.parse_type_expression())
                    if not self._match(TokenKind.COMMA):
                        break
            self._expect(TokenKind.RPAREN)
            overloads.append(OverloadSignature(tuple(params), tuple(returns)))
            self._skip_newlines()
        return tuple(overloads)

    def _constant(self, start: Token) -> tuple[ASTNode, ...]:
        """Parse constant from the current token stream."""
        if not self._match(TokenKind.DOLLAR):
            self._error("expected $ after const")
        if self._check(TokenKind.LPAREN):
            return self._multiple_assignment(start, constant=True)
        name = self._symbol("expected constant name")
        declared_type = None
        if self._match(TokenKind.COLON):
            declared_type = self.parse_type_expression()
        self._expect(TokenKind.ASSIGN)
        rhs = self._chain_until(_LINE_TERMINATORS)
        return (
            *rhs,
            SetVariableNode(
                name,
                declared_type,
                constant=True,
                location=_loc(start),
            ),
        )

    def _import(self, start: Token, *, public: bool = False) -> ImportNode:
        """Parse import from the current token stream."""
        self._expect(TokenKind.LBRACE)
        specs: list[ImportSpec] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACE):
            return ImportNode((), public, location=_loc(start))
        while True:
            specs.append(self._import_spec())
            self._skip_newlines()
            if self._match(TokenKind.RBRACE):
                break
            self._expect(TokenKind.COMMA)
            self._skip_newlines()
        return ImportNode(tuple(specs), public, location=_loc(start))

    def _import_spec(self) -> ImportSpec:
        """Parse import spec from the current token stream."""
        path = self._import_path()
        components: tuple[ImportComponent, ...] = ()
        if self._match(TokenKind.DOT):
            if self._match(TokenKind.LBRACKET):
                components = self._import_components()
            elif self._check(TokenKind.OP) and self._current.value.startswith("#"):
                components = (self._import_component(),)
            else:
                components = (self._import_component(),)
        alias = None
        if self._match_ident("as"):
            alias = self._symbol("expected import alias")
        return ImportSpec(path, alias, components)

    def _import_path(self) -> ImportPath:
        """Parse import path from the current token stream."""
        root = None
        parts: list[str] = []
        if self._check(TokenKind.OP) and self._current.value == "~":
            self._advance()
            root = Symbol("root")
        elif self._match(TokenKind.AT):
            root = Symbol("dep")
            parts.append(self._expect(TokenKind.IDENT).value)
        else:
            first = self._expect(TokenKind.IDENT).value
            if first in {"root", "dep"}:
                root = Symbol(first)
            else:
                parts.append(first)
        while self._match(TokenKind.DOT):
            if self._check(TokenKind.LBRACKET):
                self.index -= 1
                break
            if self._check(TokenKind.OP) and self._current.value.startswith("#"):
                self.index -= 1
                break
            parts.append(self._expect(TokenKind.IDENT).value)
        return ImportPath(tuple(parts), root)

    def _import_components(self) -> tuple[ImportComponent, ...]:
        """Parse import components from the current token stream."""
        components: list[ImportComponent] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACKET):
            return ()
        while True:
            components.append(self._import_component())
            self._skip_newlines()
            if self._match(TokenKind.RBRACKET):
                return tuple(components)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _import_component(self) -> ImportComponent:
        """Parse import component from the current token stream."""
        if self._match_ident("object"):
            object_name = self._symbol("expected imported object name")
            self._expect_ident("as")
            trait_name = self._symbol("expected imported trait name")
            return ImportComponent(
                object_name,
                kind=Symbol("trait_impl"),
                trait=trait_name,
            )

        if self._check(TokenKind.OP) and self._current.value.startswith("#"):
            tag = _tag_from_token(self._advance())
            return ImportComponent(Symbol(f"#{tag.name}"), kind=Symbol("tag"))

        name = self._symbol("expected imported component")
        signature = self._import_signature() if self._match(TokenKind.LPAREN) else None
        exclusions: tuple[tuple[Type, ...], ...] = ()
        if self._match_ident("except"):
            if signature is not None:
                self._error("except is only valid after a bare imported element name")
            exclusions = self._import_exclusions()
        alias = None
        if self._match_ident("as"):
            alias = self._symbol("expected component alias")
        return ImportComponent(name, alias, signature, exclusions)

    def _import_signature(self) -> tuple[Type, ...]:
        """Parse import signature from the current token stream."""
        params = self._type_list_until({TokenKind.RPAREN})
        self._expect(TokenKind.RPAREN)
        return params

    def _import_exclusions(self) -> tuple[tuple[Type, ...], ...]:
        """Parse import exclusions from the current token stream."""
        self._expect(TokenKind.LBRACKET)
        exclusions: list[tuple[Type, ...]] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACKET):
            return ()
        while True:
            self._expect(TokenKind.LPAREN)
            exclusions.append(self._import_signature())
            self._skip_newlines()
            if self._match(TokenKind.RBRACKET):
                return tuple(exclusions)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _tag_declaration(
        self,
        start: Token,
        visibility: Symbol | None,
    ) -> TagDeclarationNode | ElementTagDeclarationNode:
        """Parse tag declaration from the current token stream."""
        if not (self._check(TokenKind.OP) and self._current.value.startswith("#")):
            name = self._symbol("expected element tag name")
            if self._match_ident("disjoint"):
                disjoint = (
                    _tag_from_token(self._advance())
                    if self._check(TokenKind.OP)
                    and self._current.value.startswith("#")
                    else self._symbol("expected disjoint tag name")
                )
                return ElementTagDeclarationNode(
                    name,
                    disjoint=disjoint,
                    visibility=visibility,
                    location=_loc(start),
                )
            self._expect_ident("as")
            kind = self._expect(TokenKind.IDENT).value
            if kind not in {"property", "companion"}:
                self._error("expected element tag kind property or companion")
            return ElementTagDeclarationNode(
                name,
                kind=Symbol(kind),
                visibility=visibility,
                location=_loc(start),
            )

        tag = _tag_from_token(self._expect_tag_token())
        if self._match_ident("disjoint"):
            disjoint = (
                _tag_from_token(self._advance())
                if self._check(TokenKind.OP)
                and self._current.value.startswith("#")
                else self._symbol("expected disjoint tag name")
            )
            return TagDeclarationNode(
                tag,
                disjoint=disjoint,
                visibility=visibility,
                location=_loc(start),
            )
        self._expect_ident("as")
        if self._check(TokenKind.OP) and self._current.value.startswith("#"):
            parent = _tag_from_token(self._advance())
            return TagDeclarationNode(
                tag,
                parent=parent,
                visibility=visibility,
                location=_loc(start),
            )
        kind = self._expect(TokenKind.IDENT).value
        if kind not in {"computed", "constructed", "unit"}:
            self._error("expected tag kind computed, constructed, unit, or parent tag")
        return TagDeclarationNode(
            tag,
            kind=Symbol(kind),
            visibility=visibility,
            location=_loc(start),
        )

    def _tag_overlay(
        self,
        start: Token,
        visibility: Symbol | None,
    ) -> TagOverlayNode:
        """Parse tag overlay from the current token stream."""
        tag = _tag_from_token(self._advance())
        self._expect(TokenKind.COLON)
        generics, _, _ = self._generic_parameters()
        elements = self._overlay_elements()
        self._expect(TokenKind.FAT_ARROW)
        signatures = self._overlay_signatures()
        return TagOverlayNode(
            tag,
            elements,
            signatures,
            generics,
            visibility,
            location=_loc(start),
        )

    def _overlay_elements(self) -> tuple[Symbol, ...]:
        """Parse overlay elements from the current token stream."""
        if self._match(TokenKind.LPAREN):
            elements: list[Symbol] = []
            self._skip_newlines()
            while not self._check(TokenKind.RPAREN):
                elements.append(self._overlay_element_symbol())
                self._skip_newlines()
                if self._match(TokenKind.RPAREN):
                    return tuple(elements)
                self._expect(TokenKind.COMMA)
                self._skip_newlines()
            self._expect(TokenKind.RPAREN)
            return tuple(elements)
        return (self._overlay_element_symbol(),)

    def _overlay_element_symbol(self) -> Symbol:
        """Parse overlay element symbol from the current token stream."""
        if not self._check(TokenKind.IDENT, TokenKind.OP):
            self._error("expected overlay element name")
        token = self._advance()
        if token.kind is TokenKind.OP:
            return self._operator_run(token)
        return self._qualified_symbol(token)

    def _overlay_signatures(
        self,
    ) -> tuple[tuple[tuple[Type, ...], tuple[Type, ...]], ...]:
        """Parse overlay signatures from the current token stream."""
        signatures: list[tuple[tuple[Type, ...], tuple[Type, ...]]] = []
        single_line = not self._check(TokenKind.NEWLINE)
        self._skip_newlines()
        while not self._check(TokenKind.EOF) and not self._check_ident("end"):
            self._expect(TokenKind.LPAREN)
            params = self._type_list_until({TokenKind.RPAREN})
            self._expect(TokenKind.RPAREN)
            self._expect(TokenKind.ARROW)
            returns = self._type_list_until(
                {TokenKind.NEWLINE, TokenKind.EOF}
                if single_line
                else {TokenKind.NEWLINE, TokenKind.EOF}
            )
            signatures.append((params, returns))
            if single_line:
                break
            self._skip_separators()
        self._consume_optional_end()
        return tuple(signatures)

    def _define(
        self,
        start: Token,
        annotations: tuple[ASTNode, ...],
        visibility: Symbol | None,
        is_multi: bool,
        *,
        eager: bool = False,
        overloads: tuple[OverloadSignature, ...] = (),
    ) -> DefineNode:
        """Parse define from the current token stream."""
        generics, generic_variances, generic_constraints = self._generic_parameters()
        attached_tag = None
        if (
            self._check(TokenKind.OP)
            and self._current.value.startswith("#")
            and self._peek(1).kind
            not in {TokenKind.LPAREN, TokenKind.FAT_ARROW, TokenKind.COLON}
        ):
            attached_tag = _tag_from_token(self._advance())
        name = self._symbol("expected definition name")
        params = (
            self._params(allow_defaults=True)
            if self._match(TokenKind.LPAREN)
            else None
        )
        element_tags, element_tags_explicit = self._function_element_tags()
        element_tag_set = set(element_tags)
        companion_tags_allowed: set[ElementTag] = set()
        if eager:
            eager_tag = ElementTag(Symbol("Eager"))
            element_tag_set.add(eager_tag)
            companion_tags_allowed.add(eager_tag)
        returns = self._returns()
        where_clause = self._where_clause()
        self._expect(TokenKind.FAT_ARROW)
        body = self._body()
        return DefineNode(
            name,
            FunctionNode(
                params=params,
                body=body,
                returns=returns,
                where_clause=where_clause,
                element_tags=frozenset(element_tag_set),
                element_tags_explicit=element_tags_explicit,
                companion_tags_allowed=frozenset(companion_tags_allowed),
                annotations=annotations,
                overloads=overloads,
                location=_loc(start),
            ),
            annotations,
            is_multi,
            visibility,
            generics,
            generic_variances,
            generic_constraints,
            attached_tag,
            location=_loc(start),
        )

    def _object_like(
        self,
        start: Token,
        kind: str,
        annotations: tuple[ASTNode, ...],
        visibility: Symbol | None = None,
    ) -> ObjectNode:
        """Parse object like from the current token stream."""
        generics, generic_variances, generic_constraints = self._generic_parameters()
        name = self._symbol("expected object name")
        target = self.parse_type_expression() if self._match_ident("as") else None
        self._expect(TokenKind.FAT_ARROW)
        if kind == "enum":
            enum_members = self._enum_body()
            return ObjectNode(
                Symbol(kind),
                name,
                generics,
                target,
                enum_members=enum_members,
                annotations=annotations,
                generic_variances=generic_variances,
                generic_constraints=generic_constraints,
                visibility=visibility,
                location=_loc(start),
            )
        fields, definitions, requirements, variants = self._object_body(kind, name)
        return ObjectNode(
            Symbol(kind),
            name,
            generics,
            target,
            fields,
            definitions,
            requirements,
            variants,
            (),
            annotations,
            generic_variances=generic_variances,
            generic_constraints=generic_constraints,
            visibility=visibility,
            location=_loc(start),
        )

    def _generic_names(self) -> tuple[Symbol, ...]:
        """Parse generic names from the current token stream."""
        names, _, _ = self._generic_parameters()
        return names

    def _generic_parameters(
        self,
    ) -> tuple[tuple[Symbol, ...], tuple[Symbol | None, ...], tuple[Type | None, ...]]:
        """Parse generic parameters from the current token stream."""
        if not self._match(TokenKind.LBRACKET):
            return (), (), ()
        names: list[Symbol] = []
        variances: list[Symbol | None] = []
        constraints: list[Type | None] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACKET):
            return (), (), ()
        while True:
            names.append(self._symbol("expected generic parameter name"))
            variance = None
            constraint = None
            if self._match(TokenKind.COLON):
                if self._check_ident("any") or self._check_ident("above"):
                    variance = Symbol(str(self._advance().value))
                constraint = self.parse_type_expression()
            variances.append(variance)
            constraints.append(constraint)
            if self._match(TokenKind.RBRACKET):
                return tuple(names), tuple(variances), tuple(constraints)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _object_body(
        self,
        kind: str,
        owner: Symbol,
    ) -> tuple[
        tuple[ObjectFieldNode, ...],
        tuple[DefineNode, ...],
        tuple[TraitRequirementNode, ...],
        tuple[VariantMemberNode, ...],
    ]:
        """Parse object body from the current token stream."""
        single_line = not self._check(TokenKind.NEWLINE)
        fields: list[ObjectFieldNode] = []
        definitions: list[DefineNode] = []
        requirements: list[TraitRequirementNode] = []
        variants: list[VariantMemberNode] = []

        if single_line:
            if self._check_ident("end"):
                self._consume_optional_end()
                return (), (), (), ()
            if kind == "variant" and self._check(TokenKind.IDENT):
                variants.append(self._variant_member())
            else:
                item = self._object_body_item(owner)
                _append_object_body_item(item, fields, definitions, requirements)
            self._consume_optional_end()
            return (
                tuple(fields),
                tuple(definitions),
                tuple(requirements),
                tuple(variants),
            )

        self._skip_newlines()
        while not self._check(TokenKind.EOF) and not self._check_ident("end"):
            if (
                kind == "variant"
                and self._check(TokenKind.IDENT)
                and self._peek(1).kind == TokenKind.FAT_ARROW
            ):
                variants.append(self._variant_member())
            else:
                item = self._object_body_item(owner)
                _append_object_body_item(item, fields, definitions, requirements)
            self._skip_separators()
        self._consume_optional_end()
        return tuple(fields), tuple(definitions), tuple(requirements), tuple(variants)

    def _object_body_item(
        self,
        owner: Symbol,
    ) -> ObjectFieldNode | DefineNode | TraitRequirementNode:
        """Parse object body item from the current token stream."""
        overloads = self._overload_signatures()
        annotations = self._annotations()
        visibility: Symbol | None = None
        if self._match_ident("public", "private"):
            visibility = Symbol(self._previous.value)
        if self._match_ident("define"):
            return self._define(
                self._previous,
                annotations,
                visibility,
                False,
                overloads=overloads,
            )
        if overloads:
            self._error("overload must be followed by define")
        if self._match_ident("extend"):
            return self._extend(self._previous)
        if annotations:
            self._error("annotation must be followed by a declaration")
        return self._field(owner, visibility)

    def _extend(self, start: Token) -> TraitRequirementNode:
        """Parse extend from the current token stream."""
        if not self._check(TokenKind.IDENT, TokenKind.OP):
            self._error("expected required element name")
        token = self._advance()
        name = (
            self._operator_run(token)
            if token.kind is TokenKind.OP
            else self._qualified_symbol(token)
        )
        params = (
            self._params(allow_empty=True)
            if self._match(TokenKind.LPAREN)
            else None
        )
        returns = self._returns()
        return TraitRequirementNode(name, params, returns, location=_loc(start))

    def _field(self, owner: Symbol, visibility: Symbol | None) -> ObjectFieldNode:
        """Parse field from the current token stream."""
        access = visibility
        if access is None and self._match_ident("readable"):
            access = Symbol("readable")
        if access is None and self._match_ident("public", "private"):
            access = Symbol(self._previous.value)
        access = access or Symbol("readable")
        start = self._expect(TokenKind.DOLLAR)
        name = self._symbol("expected field name")
        typ = None
        default: tuple[ASTNode, ...] = ()
        if self._match(TokenKind.COLON):
            typ = self.parse_type_expression()
        if self._match(TokenKind.ASSIGN):
            default = self._chain_until(_LINE_TERMINATORS)
        if typ is None and not default:
            self._error(f"field '{owner}.{name}' needs a type or default value")
        return ObjectFieldNode(name, typ, default, access, location=_loc(start))

    def _variant_member(self) -> VariantMemberNode:
        """Parse variant member from the current token stream."""
        start = self._current
        name = self._symbol("expected variant member name")
        self._expect(TokenKind.FAT_ARROW)
        fields, definitions, requirements, variants = self._object_body("object", name)
        if requirements or variants:
            self._error("variant members may contain fields and definitions only")
        return VariantMemberNode(name, fields, definitions, location=_loc(start))

    def _enum_body(self) -> tuple[EnumMemberNode, ...]:
        """Parse enum body from the current token stream."""
        members: list[EnumMemberNode] = []
        single_line = not self._check(TokenKind.NEWLINE)
        if single_line and self._check_ident("end"):
            self._consume_optional_end()
            return ()
        self._skip_newlines()
        while not self._check(TokenKind.EOF) and not self._check_ident("end"):
            start = self._current
            name = self._symbol("expected enum member name")
            value: tuple[ASTNode, ...] = ()
            if self._match(TokenKind.ASSIGN):
                value = self._chain_until(_LINE_TERMINATORS)
            members.append(EnumMemberNode(name, value, location=_loc(start)))
            self._skip_separators()
        self._consume_optional_end()
        return tuple(members)

    def _function(
        self,
        start: Token,
        annotations: tuple[ASTNode, ...] = (),
        *,
        overloads: tuple[OverloadSignature, ...] = (),
    ) -> FunctionNode:
        """Parse function from the current token stream."""
        generics, generic_variances, generic_constraints = self._generic_parameters()
        params = (
            self._params(allow_empty=True)
            if self._match(TokenKind.LPAREN)
            else None
        )
        element_tags, element_tags_explicit = self._function_element_tags()
        returns = self._returns()
        where_clause = self._where_clause()
        self._expect(TokenKind.FAT_ARROW)
        return FunctionNode(
            generics=generics,
            generic_variances=generic_variances,
            params=params,
            returns=returns,
            where_clause=where_clause,
            element_tags=element_tags,
            element_tags_explicit=element_tags_explicit,
            annotations=annotations,
            body=self._body(),
            generic_constraints=generic_constraints,
            overloads=overloads,
            location=_loc(start),
        )

    def _function_element_tags(self) -> tuple[frozenset[ElementTag], bool]:
        """Parse function element tags from the current token stream."""
        if not self._check_op("<"):
            return frozenset(), False
        self._advance()
        return self._element_tag_list(), True

    def _where_clause(self) -> tuple[ASTNode, ...]:
        """Parse where clause from the current token stream."""
        self._skip_newlines()
        if not self._match_ident("where"):
            return ()
        self._expect(TokenKind.LPAREN)
        self._where_clause_depth += 1
        try:
            expressions = self._comma_expressions(TokenKind.RPAREN)
        finally:
            self._where_clause_depth -= 1
        self._skip_newlines()
        return _flatten(expressions)

    def _if(self, start: Token) -> IfNode:
        """Parse if from the current token stream."""
        condition = self._condition()
        self._expect(TokenKind.FAT_ARROW)
        then_branch = self._body({"else", "end"}, owner_column=self._line_start_column(start))
        else_branch: tuple[ASTNode, ...] = ()
        self._skip_newlines()
        if self._match_ident("else"):
            if self._match_ident("if"):
                else_branch = (self._if(self._previous),)
                self._consume_optional_end(owner_column=self._line_start_column(start))
            else:
                self._expect(TokenKind.FAT_ARROW)
                else_branch = self._body({"end"}, owner_column=self._line_start_column(start))
                self._skip_newlines()
                self._consume_optional_end(owner_column=self._line_start_column(start))
        return IfNode(condition, then_branch, else_branch, location=_loc(start))

    def _while(self, start: Token) -> WhileNode:
        """Parse while from the current token stream."""
        condition = self._condition()
        self._expect(TokenKind.FAT_ARROW)
        return WhileNode(
            condition=condition,
            body=self._body(owner_column=self._line_start_column(start)),
            location=_loc(start),
        )

    def _assert(self, start: Token) -> AssertNode:
        """Parse assert from the current token stream."""
        self._expect(TokenKind.FAT_ARROW)
        condition = self._body({"else", "end"})
        else_branch: tuple[ASTNode, ...] = ()
        self._skip_newlines()
        if self._match_ident("else"):
            self._expect(TokenKind.FAT_ARROW)
            else_branch = self._body({"end"})
        return AssertNode(condition, else_branch, location=_loc(start))

    def _unfold(self, start: Token) -> UnfoldNode:
        """Parse unfold from the current token stream."""
        condition: tuple[ASTNode, ...] = ()
        if self._check(TokenKind.LPAREN):
            condition = self._condition()
        params = self._control_params()
        self._expect(TokenKind.FAT_ARROW)
        return UnfoldNode(
            condition,
            params,
            self._body(owner_column=self._line_start_column(start)),
            location=_loc(start),
        )

    def _try(self, start: Token) -> TryNode:
        """Parse try from the current token stream."""
        self._expect(TokenKind.FAT_ARROW)
        body = self._body({"handle", "end"})
        handlers: list[TryHandlerNode] = []
        self._skip_newlines()
        while self._match_ident("handle"):
            handler_start = self._previous
            typ = None
            if not self._check(TokenKind.FAT_ARROW):
                typ = self.parse_type_expression()
            self._expect(TokenKind.FAT_ARROW)
            handlers.append(
                TryHandlerNode(
                    typ,
                    self._body({"handle", "end"}),
                    location=_loc(handler_start),
                )
            )
            self._skip_newlines()
        if not handlers:
            self._error("try requires at least one handler")
        self._consume_optional_end()
        return TryNode(body, tuple(handlers), location=_loc(start))

    def _at(self, start: Token) -> AtNode:
        """Parse at from the current token stream."""
        self._expect(TokenKind.LPAREN)
        levels: list[AtLevel] = []
        self._skip_newlines()
        if self._match(TokenKind.RPAREN):
            self._expect(TokenKind.FAT_ARROW)
            return AtNode((), self._body(), location=_loc(start))
        while True:
            name = self._symbol("expected at level name")
            depth = 0
            while self._check(TokenKind.OP) and self._current.value == "+":
                self._advance()
                depth += 1
            levels.append(AtLevel(name, depth))
            if self._match(TokenKind.RPAREN):
                break
            self._expect(TokenKind.COMMA)
            self._skip_newlines()
        self._expect(TokenKind.FAT_ARROW)
        return AtNode(tuple(levels), self._body(), location=_loc(start))

    def _control_params(self) -> tuple[FunctionParam, ...] | None:
        """Parse control params from the current token stream."""
        if not self._match(TokenKind.ARROW):
            return None
        self._expect(TokenKind.LPAREN)
        return self._params()

    def _foreach(self, start: Token) -> ForNode:
        """Parse foreach from the current token stream."""
        self._expect(TokenKind.LPAREN)
        variable = self._symbol("expected foreach variable")
        index_variable = None
        if self._match(TokenKind.COMMA):
            index_variable = self._symbol("expected foreach index variable")
        self._expect(TokenKind.RPAREN)
        self._expect(TokenKind.FAT_ARROW)
        return ForNode(
            variable,
            index_variable,
            self._body(),
            location=_loc(start),
        )

    def _match_node(self, start: Token) -> MatchNode:
        """Parse match node from the current token stream."""
        self._expect(TokenKind.FAT_ARROW)
        cases: list[MatchCaseNode] = []
        self._skip_newlines()
        case_column = self._current.column
        while not self._check_ident("end") and not self._check(TokenKind.EOF):
            if cases and self._current.column < case_column:
                break
            case_start = self._current
            if self._match_ident("default"):
                self._expect(TokenKind.FAT_ARROW)
                cases.append(
                    MatchCaseNode(
                        (WildcardPatternNode(location=_loc(case_start)),),
                        (),
                        None,
                        True,
                        self._match_body(case_column),
                        location=_loc(case_start),
                    )
                )
                self._skip_newlines()
                continue
            patterns = self._match_case_patterns()
            pattern_type = None
            if (
                len(patterns) == 1
                and isinstance(patterns[0], TypePatternNode)
                and patterns[0].typ is not None
            ):
                pattern_type = patterns[0].typ
            self._expect(TokenKind.FAT_ARROW)
            cases.append(
                MatchCaseNode(
                    patterns,
                    (),
                    pattern_type,
                    False,
                    self._match_body(case_column),
                    location=_loc(case_start),
                )
            )
            self._skip_newlines()
        self._consume_optional_end()
        return MatchNode(tuple(cases), location=_loc(start))

    def _match_case_patterns(self) -> tuple[MatchPatternNode, ...]:
        """Parse match case patterns from the current token stream."""
        patterns: list[MatchPatternNode] = []
        while not self._check(TokenKind.FAT_ARROW):
            patterns.append(self._match_pattern({TokenKind.COMMA, TokenKind.FAT_ARROW}))
            if not self._match(TokenKind.COMMA):
                break
            self._skip_newlines()
        return tuple(patterns)

    def _match_pattern(
        self,
        terminators: set[TokenKind],
    ) -> MatchPatternNode:
        """Parse match pattern from the current token stream."""
        options = [self._match_pattern_atom(terminators | {TokenKind.PIPE})]
        while (
            self._check(TokenKind.PIPE)
            and self._peek(1).kind is TokenKind.PIPE
            and self._adjacent(self._current, self._peek(1))
        ):
            self._advance()
            self._advance()
            options.append(self._match_pattern_atom(terminators | {TokenKind.PIPE}))
        if len(options) == 1:
            return options[0]
        return OrPatternNode(tuple(options), location=options[0].location)

    def _match_pattern_atom(
        self,
        terminators: set[TokenKind],
    ) -> MatchPatternNode:
        """Parse match pattern atom from the current token stream."""
        if self._match_ident("as"):
            return self._type_match_pattern(self._previous, terminators)
        if self._match_ident("if"):
            start = self._previous
            return GuardPatternNode(
                self._chain_until(terminators),
                location=_loc(start),
            )
        if self._check_ident("_"):
            token = self._advance()
            return WildcardPatternNode(location=_loc(token))
        if self._match(TokenKind.DOLLAR):
            start = self._previous
            name = self._symbol("expected binding name")
            self._expect(TokenKind.ASSIGN)
            return BindingPatternNode(
                name,
                self._match_pattern_atom(terminators),
                location=_loc(start),
            )
        if self._match_ellipsis():
            return RestPatternNode(location=_loc(self._previous))
        if self._match(TokenKind.LBRACKET):
            start = self._previous
            return ListPatternNode(self._list_match_patterns(), location=_loc(start))
        if self._match(TokenKind.NUMBER):
            token = self._previous
            return LiteralPatternNode(
                NumberLiteralNode(token.value, location=_loc(token)),
                location=_loc(token),
            )
        if self._match(TokenKind.STRING):
            token = self._previous
            string_node = self._string_node(token)
            if isinstance(string_node, StringLiteralNode):
                return LiteralPatternNode(string_node, location=_loc(token))
            return ExpressionPatternNode((string_node,), location=_loc(token))

        start = self._current
        expression = self._chain_until(terminators)
        if not expression:
            self._error("expected match pattern")
        return ExpressionPatternNode(expression, location=_loc(start))

    def _type_match_pattern(
        self,
        start: Token,
        terminators: set[TokenKind],
    ) -> TypePatternNode:
        """Parse type match pattern from the current token stream."""
        name = None
        typ = None
        if self._match(TokenKind.COLON):
            typ = self.parse_type_expression()
        elif self._check(TokenKind.IDENT) and self._peek(1).kind is TokenKind.COLON:
            name = Symbol(self._advance().value)
            self._expect(TokenKind.COLON)
            typ = self.parse_type_expression()
        elif self._check(TokenKind.IDENT):
            name = Symbol(self._advance().value)
        else:
            self._error("expected type match pattern")

        fields: tuple[MatchPatternNode, ...] = ()
        if self._match(TokenKind.LPAREN):
            fields = self._type_match_fields()
        guard: tuple[ASTNode, ...] = ()
        if self._match_ident("if"):
            guard = self._chain_until(terminators)
        return TypePatternNode(typ, name, fields, guard, location=_loc(start))

    def _type_match_fields(self) -> tuple[MatchPatternNode, ...]:
        """Parse type match fields from the current token stream."""
        fields: list[MatchPatternNode] = []
        self._skip_newlines()
        if self._match(TokenKind.RPAREN):
            return ()
        while True:
            if self._check(TokenKind.IDENT) and self._peek(1).kind in {
                TokenKind.COMMA,
                TokenKind.RPAREN,
            }:
                token = self._advance()
                fields.append(
                    BindingPatternNode(
                        Symbol(token.value),
                        WildcardPatternNode(location=_loc(token)),
                        location=_loc(token),
                    )
                )
            else:
                fields.append(self._match_pattern({TokenKind.COMMA, TokenKind.RPAREN}))
            if self._match(TokenKind.RPAREN):
                return tuple(fields)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _list_match_patterns(self) -> tuple[MatchPatternNode, ...]:
        """Parse list match patterns from the current token stream."""
        items: list[MatchPatternNode] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACKET):
            return ()
        while True:
            items.append(self._match_pattern({TokenKind.COMMA, TokenKind.RBRACKET}))
            if self._match(TokenKind.RBRACKET):
                return tuple(items)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _match_body(self, case_column: int) -> tuple[ASTNode, ...]:
        """Parse a match-case body, using indentation to distinguish cases."""
        single_line = not self._check(TokenKind.NEWLINE)
        if single_line:
            body = self._chain_until(_LINE_TERMINATORS)
            self._consume_optional_end()
            return body
        self._skip_newlines()
        nodes: list[ASTNode] = []
        while not self._check(TokenKind.EOF):
            if self._check_ident("end"):
                break
            if self._current.column <= case_column and self._at_match_case_start():
                break
            if self._current.column < case_column:
                break
            before = self.index
            statement = self._statement()
            if not statement and self.index == before:
                self._error("expected statement")
            nodes.extend(statement)
            self._skip_separators()
        return tuple(nodes)

    def _at_match_case_start(self) -> bool:
        """Return the Boolean result of at match case start from the current parser token stream."""
        if self._check_ident("as", "default", "if", "_"):
            return True
        return self._check(TokenKind.NUMBER, TokenKind.STRING, TokenKind.LBRACKET)

    def _body(
        self,
        stop_words: set[str] | None = None,
        *,
        owner_column: int | None = None,
    ) -> tuple[ASTNode, ...]:
        """Parse a body, leaving less-indented `end` tokens to outer blocks."""
        single_line = not self._check(TokenKind.NEWLINE)
        if single_line:
            body_line = self._current.line
            body = self._chain_until(_LINE_TERMINATORS)
            if self._check_ident("end") and self._current.line == body_line:
                self._consume_optional_end(owner_column=owner_column)
            return body
        self._skip_newlines()
        nodes: list[ASTNode] = []
        stop_words = {"end"} if stop_words is None else stop_words
        while not self._check(TokenKind.EOF):
            if self._check_ident(*stop_words):
                break
            before = self.index
            statement = self._statement()
            if not statement and self.index == before:
                self._error("expected statement")
            nodes.extend(statement)
            self._skip_separators()
        self._consume_optional_end(owner_column=owner_column)
        return tuple(nodes)

    def _condition(self) -> tuple[ASTNode, ...]:
        """Parse condition from the current token stream."""
        if self._match(TokenKind.LPAREN):
            condition = self._chain_until({TokenKind.RPAREN})
            self._expect(TokenKind.RPAREN)
            return condition
        return self._chain_until({TokenKind.FAT_ARROW})

    def _return_values(
        self,
    ) -> tuple[tuple[tuple[ASTNode, ...], ...], bool]:
        """Parse bare, chained, or explicit argument-list return values."""
        if self._check(TokenKind.NEWLINE, TokenKind.EOF) or self._check_ident(
            "end", "else"
        ):
            return (), False
        if self._match(TokenKind.LPAREN):
            return self._comma_expressions(TokenKind.RPAREN), True
        return (self._chain_until(_LINE_TERMINATORS),), False

    def _optional_values(self) -> tuple[ASTNode, ...]:
        """Parse optional values from the current token stream."""
        if self._check(TokenKind.NEWLINE, TokenKind.EOF) or self._check_ident(
            "end", "else"
        ):
            return ()
        if self._match(TokenKind.LPAREN):
            return _flatten(self._comma_expressions(TokenKind.RPAREN))
        return self._chain_until(_LINE_TERMINATORS)

    def _chain_until(self, terminators: set[TokenKind | str]) -> tuple[ASTNode, ...]:
        """Parse chain until from the current token stream."""
        nodes: list[ASTNode] = []
        segment: list[_ChainPiece] = []
        self._skip_newlines()
        while not self._at_terminator(terminators):
            if self._where_clause_depth and self._check(TokenKind.NEWLINE):
                self._skip_newlines()
                if self._at_terminator(terminators):
                    break
            if self._match(TokenKind.PIPE):
                nodes.extend(
                    _lower_chain_segment(
                        segment,
                        reverse_elements=not bool(self._where_clause_depth),
                    )
                )
                segment.clear()
                continue
            piece = self._term()
            segment.append(piece)
            if piece.breaks_chain:
                nodes.extend(
                    _lower_chain_segment(
                        segment,
                        reverse_elements=not bool(self._where_clause_depth),
                    )
                )
                segment.clear()
        nodes.extend(
            _lower_chain_segment(
                segment,
                reverse_elements=not bool(self._where_clause_depth),
            )
        )
        return tuple(nodes)

    def _chain_segment_until(
        self,
        terminators: set[TokenKind | str],
    ) -> tuple[ASTNode, ...]:
        """Parse chain segment until from the current token stream."""
        segment: list[_ChainPiece] = []
        self._skip_newlines()
        while not self._at_terminator(terminators):
            piece = self._term()
            segment.append(piece)
            if piece.breaks_chain:
                break
        return tuple(
            _lower_chain_segment(
                segment,
                reverse_elements=not bool(self._where_clause_depth),
            )
        )

    def _term(self) -> _ChainPiece:
        """Parse term from the current token stream."""
        if self._match(TokenKind.NUMBER):
            token = self._previous
            return _ChainPiece(
                (NumberLiteralNode(token.value, location=_loc(token)),),
                True,
            )
        if self._match(TokenKind.STRING):
            token = self._previous
            return _ChainPiece(
                (self._string_node(token),),
                True,
            )
        if self._match(TokenKind.DOLLAR):
            return self._variable(self._previous)
        if self._where_clause_depth and self._where_type_literal_ahead():
            start = self._current
            return _ChainPiece(
                (TypeLiteralNode(self.parse_type_expression(), location=_loc(start)),),
                True,
            )
        if self._match_ellipsis():
            start = self._previous
            self._expect(TokenKind.DOLLAR)
            self._expect(TokenKind.LBRACKET)
            selectors = self._index_selectors()
            return _ChainPiece(
                (
                    *self._selector_expressions(selectors),
                    IndexAccessNode(
                        selectors,
                        spread=True,
                        location=_loc(start),
                    ),
                ),
                True,
            )
        if self._match(TokenKind.DOT):
            token = self._previous
            return _ChainPiece(
                (
                    FieldAccessNode(
                        self._symbol("expected field name"),
                        location=_loc(token),
                    ),
                ),
                breaks_chain=True,
            )
        if self._match(TokenKind.LBRACKET):
            token = self._previous
            items = self._comma_expressions(TokenKind.RBRACKET)
            cast = self._empty_list_cast() if not items else None
            return _ChainPiece(
                (
                    ListLiteralNode(
                        items,
                        cast.typ if cast is not None else None,
                        location=_loc(token),
                    ),
                    *((cast,) if cast is not None and (cast.checked or cast.optional) else ()),
                ),
                True,
            )
        if self._match_ident("arr") and self._match(TokenKind.LBRACE):
            token = self._previous
            return _ChainPiece(
                (
                    ArrayLiteralNode(
                        self._comma_expressions(TokenKind.RBRACE),
                        location=_loc(token),
                    ),
                ),
                True,
            )
        if (
            self._check_ident("record")
            and self._peek(1).kind is TokenKind.DOT
            and self._peek(2).kind is TokenKind.IDENT
            and self._peek(2).value == "extend"
            and self._peek(3).kind is TokenKind.LBRACE
        ):
            token = self._advance()
            self._advance()
            self._advance()
            self._advance()
            extension = RecordLiteralNode(
                self._record_fields(),
                location=_loc(token),
            )
            return _ChainPiece(
                (
                    extension,
                    ElementNode(
                        Symbol("extend", ("record",)),
                        location=_loc(token),
                    ),
                ),
                True,
            )
        if self._check_ident("record") and self._peek(1).kind is TokenKind.LBRACE:
            token = self._advance()
            self._advance()
            return _ChainPiece(
                (RecordLiteralNode(self._record_fields(), location=_loc(token)),),
                True,
            )
        if self._match_ident("dict") and self._match(TokenKind.LBRACE):
            token = self._previous
            return _ChainPiece(
                (DictLiteralNode(self._dict_entries(), location=_loc(token)),),
                True,
            )
        if self._match(TokenKind.LBRACE):
            token = self._previous
            return _ChainPiece(
                (
                    TupleLiteralNode(
                        self._comma_expressions(TokenKind.RBRACE),
                        location=_loc(token),
                    ),
                ),
                True,
            )
        if self._match(TokenKind.LPAREN):
            token = self._previous
            grouped = self._chain_until({TokenKind.RPAREN})
            self._expect(TokenKind.RPAREN)
            if not grouped:
                raise ParseError(
                    f"empty grouping is invalid at {token.line}:{token.column}"
                )
            return _ChainPiece(grouped, True)
        if self._check(TokenKind.AT) and self._peek(1).kind is TokenKind.AT:
            start = self._advance()
            self._advance()
            annotation_name = self._symbol("expected annotation name")
            annotation = AnnotationNode(
                Symbol(f"@@{annotation_name.text}"),
                (),
                (),
                location=_loc(start),
            )
            if not self._match(TokenKind.IDENT, TokenKind.OP):
                self._error("element annotation must be followed by an element")
            token = self._previous
            name = (
                self._operator_run(token)
                if token.kind is TokenKind.OP
                else self._qualified_symbol(token)
            )
            generic_args = self._element_generic_arguments(self._previous)
            disambiguation = self._element_disambiguation(self._previous)
            call_anchor = self._previous
            return self._element_piece(
                ElementNode(
                    name,
                    (),
                    disambiguation,
                    (),
                    (annotation,),
                    generic_args=generic_args,
                    location=_loc(token),
                )
            )
        if self._match(TokenKind.AT):
            start = self._previous
            name = self._symbol("expected annotation name")
            args: tuple[ASTNode, ...] = ()
            kwargs: tuple[tuple[Symbol, ASTNode], ...] = ()
            if self._match(TokenKind.LPAREN):
                args, kwargs = self._annotation_arguments(TokenKind.RPAREN)
            annotation = AnnotationNode(name, args, kwargs, location=_loc(start))
            if not self._match_ident("fn"):
                self._error("annotation must be followed by fn in expression position")
            return _ChainPiece(
                (self._function(self._previous, (annotation,)),),
                True,
            )
        if self._match_ident("fn"):
            return _ChainPiece((self._function(self._previous),), True)
        if self._match_ident("if"):
            return _ChainPiece((self._if(self._previous),), True)
        if self._match_ident("assert"):
            return _ChainPiece((self._assert(self._previous),), True)
        if self._match_ident("while"):
            return _ChainPiece((self._while(self._previous),), True)
        if self._match_ident("unfold"):
            return _ChainPiece((self._unfold(self._previous),), True)
        if self._match_ident("at"):
            return _ChainPiece((self._at(self._previous),), True)
        if self._match_ident("foreach"):
            return _ChainPiece((self._foreach(self._previous),), True)
        if self._match_ident("match"):
            return _ChainPiece((self._match_node(self._previous),), True)
        if self._match_ident("try"):
            return _ChainPiece((self._try(self._previous),), True)
        if (
            self._check_op("^")
            and self._peek(1).kind is TokenKind.OP
            and self._peek(1).value == "+"
            and self._adjacent(self._current, self._peek(1))
        ):
            start = self._advance()
            plus = self._advance()
            rank = 1
            if self._check(TokenKind.NUMBER) and self._adjacent(plus, self._current):
                literal = self._advance()
                if not literal.value.isdecimal() or literal.value.startswith("0"):
                    self._error("minimum rank must be a positive integer literal")
                rank = int(literal.value)
            elif (
                self._check_op("-")
                and self._adjacent(plus, self._current)
                and self._peek(1).kind is TokenKind.NUMBER
                and self._adjacent(self._current, self._peek(1))
            ):
                self._error("minimum rank must be a positive integer literal")
            return _ChainPiece(
                (MinimumRankNode(rank, location=_loc(start)),),
                breaks_chain=True,
            )
        if self._match_ident("as?"):
            return self._cast(self._previous, optional_spelling=True)
        if self._match_ident("as"):
            return self._cast(self._previous)
        if self._match_ident("break"):
            token = self._previous
            return _ChainPiece(
                (BreakNode(self._optional_values(), location=_loc(token)),),
                True,
            )
        if self._match_ident("return"):
            token = self._previous
            return _ChainPiece(
                (ReturnNode(*self._return_values(), location=_loc(token)),),
                True,
            )
        if self._check_ident("copy", "move") and self._peek(1).kind is TokenKind.LPAREN:
            return _ChainPiece((self._stack_shuffle(),), True)
        if self._check_ident("pop_n") and self._peek(1).kind is TokenKind.LPAREN:
            start = self._advance()
            self._expect(TokenKind.LPAREN)
            if self._match(TokenKind.NUMBER):
                count = self._previous
                try:
                    value: int | Symbol = int(count.value)
                except ValueError:
                    self._error("pop_n count must be a compile-time integer")
                if str(value) != count.value or value < 0:
                    self._error(
                        "pop_n count must be a non-negative compile-time integer"
                    )
            elif self._match(TokenKind.DOLLAR):
                value = self._symbol("expected static variable name")
            else:
                self._error("pop_n count must be a number or static variable")
            self._expect(TokenKind.RPAREN)
            return _ChainPiece((PopNNode(value, location=_loc(start)),), True)
        if self._match(TokenKind.IDENT, TokenKind.OP):
            token = self._previous
            if token.value.startswith("#"):
                return _ChainPiece(
                    (TagApplicationNode(_tag_from_token(token), location=_loc(token)),),
                    is_element=True,
                )
            name = (
                self._operator_run(token)
                if token.kind is TokenKind.OP
                else self._qualified_symbol(token)
            )
            generic_args = self._element_generic_arguments(self._previous)
            disambiguation = self._element_disambiguation(self._previous)
            call_anchor = self._previous
            call_args: tuple[CallArgument, ...] = ()
            modifier_args: tuple[FunctionNode, ...] = ()
            breaks_chain = name.text.startswith("\\")
            if self._check(TokenKind.LPAREN) and (
                token.kind is TokenKind.IDENT
                or self._adjacent(call_anchor, self._current)
            ):
                self._advance()
                call_args = self._call_arguments()
                breaks_chain = True
            if self._match(TokenKind.COLON):
                modifier_args = self._modifier_arguments(token)
                breaks_chain = True
            return self._element_piece(
                ElementNode(
                    name,
                    modifier_args,
                    disambiguation,
                    call_args,
                    generic_args=generic_args,
                    location=_loc(token),
                ),
                breaks_chain=breaks_chain,
            )
        self._error("expected expression")

    def _where_type_literal_ahead(self) -> bool:
        """Return whether the next static term starts a type literal."""
        if self._check(TokenKind.AT):
            return True
        if self._check(TokenKind.LBRACE):
            return self._peek(1).kind in {TokenKind.IDENT, TokenKind.RBRACE}
        if self._check(TokenKind.LBRACKET):
            token = self._peek(1)
            return token.kind is TokenKind.OP and token.value.startswith("#")
        if self._check(TokenKind.OP) and self._current.value.startswith("#"):
            return True
        if self._check(TokenKind.LPAREN):
            # Function type syntax ``(A, B -> C)`` is distinguishable from an
            # ordinary grouped static expression by the top-level arrow.
            depth = 0
            ahead = 0
            while True:
                token = self._peek(ahead)
                if token.kind in {TokenKind.EOF, TokenKind.NEWLINE}:
                    return False
                if token.kind is TokenKind.LPAREN:
                    depth += 1
                elif token.kind is TokenKind.RPAREN:
                    depth -= 1
                    if depth == 0:
                        return False
                elif token.kind is TokenKind.ARROW and depth == 1:
                    return True
                ahead += 1
        if not self._check(TokenKind.IDENT):
            return False
        if self._current.value in {"trait", "record"}:
            return True

        # Bare type symbols and generic variables conventionally start with an
        # upper-case letter.  For qualified names, inspect the final component
        # so lower-case namespaces such as ``pkg.Type`` remain available.
        ahead = 0
        final = self._current.value
        while (
            self._peek(ahead + 1).kind is TokenKind.DOT
            and self._peek(ahead + 2).kind is TokenKind.IDENT
        ):
            ahead += 2
            final = self._peek(ahead).value
        return bool(final) and final[0].isupper()

    def _element_piece(
        self,
        node: ElementNode,
        *,
        breaks_chain: bool = False,
    ) -> _ChainPiece:
        """Parse element piece from the current token stream."""
        extension = self._element_extension()
        if extension is not None:
            node = replace(node, extension=extension)
        return _ChainPiece((node,), breaks_chain=breaks_chain, is_element=True)

    def _element_extension(self) -> ElementExtension | None:
        """Parse element extension from the current token stream."""
        if not self._match_ident("extend"):
            return None
        start = self._previous

        if self._match(TokenKind.LPAREN):
            body = self._chain_until({TokenKind.RPAREN})
            self._expect(TokenKind.RPAREN)
            if not body:
                self._error("extend default must produce a value")
            return ElementExtension(
                default=FunctionNode(params=(), body=body, location=_loc(start)),
                location=_loc(start),
            )

        if self._match(TokenKind.FAT_ARROW):
            rules: list[ExtensionPatternRule] = []
            self._skip_separators()
            while not self._check_ident("end"):
                if self._check(TokenKind.EOF):
                    self._error("unterminated extend pattern block")
                rule_start = self._expect(TokenKind.LPAREN)
                pattern = self._extension_pattern()
                self._expect(TokenKind.RPAREN)
                self._expect(TokenKind.FAT_ARROW)
                body = self._body()
                if not body:
                    self._error("extend pattern rule must produce substitutions")
                params = tuple(
                    FunctionParam(name=name)
                    for name in pattern
                    if name is not None
                )
                rules.append(
                    ExtensionPatternRule(
                        pattern,
                        FunctionNode(
                            params=params,
                            body=body,
                            location=_loc(rule_start),
                        ),
                    )
                )
                self._skip_separators()
            self._expect_ident("end")
            if not rules:
                self._error("extend pattern block requires at least one rule")
            return ElementExtension(rules=tuple(rules), location=_loc(start))

        if self._match(TokenKind.COLON):
            selector_piece = self._term()
            if not selector_piece.is_element:
                self._error("extend selector must be an element")
            selector_body = tuple(_lower_chain_segment((selector_piece,)))
            return ElementExtension(
                selector=FunctionNode(body=selector_body, location=_loc(start)),
                location=_loc(start),
            )

        self._error("expected '(', '=>', or ':' after extend")

    def _extension_pattern(self) -> tuple[Symbol | None, ...]:
        """Parse extension pattern from the current token stream."""
        pattern: list[Symbol | None] = []
        names: set[Symbol] = set()
        self._skip_newlines()
        if self._check(TokenKind.RPAREN):
            self._error("extend pattern cannot be empty")
        while True:
            token = self._expect(TokenKind.IDENT)
            if token.value == "_":
                pattern.append(None)
            else:
                name = Symbol(token.value)
                if name in names:
                    self._error(f"duplicate extend pattern name '{name}'")
                names.add(name)
                pattern.append(name)
            self._skip_newlines()
            if not self._match(TokenKind.COMMA):
                break
            self._skip_newlines()
        if all(name is not None for name in pattern):
            self._error("extend pattern must contain at least one missing '_'")
        return tuple(pattern)

    def _stack_shuffle(self) -> StackShuffleNode:
        """Parse stack shuffle from the current token stream."""
        start = self._advance()
        mode = Symbol(start.value)
        self._expect(TokenKind.LPAREN)
        prestack = self._shuffle_labels(allow_skip=True)
        self._expect(TokenKind.ARROW)
        poststack = tuple(self._non_skip_shuffle_labels())
        self._expect(TokenKind.RPAREN)
        seen: set[Symbol] = set()
        for label in prestack:
            if label is None:
                continue
            if label in seen:
                raise ParseError(
                    f"duplicate prestack label '{label}'",
                    line=start.line,
                    column=start.column,
                )
            seen.add(label)
        missing = tuple(label for label in poststack if label not in seen)
        if missing:
            raise ParseError(
                f"poststack label '{missing[0]}' was not declared in prestack",
                line=start.line,
                column=start.column,
            )
        return StackShuffleNode(mode, prestack, poststack, location=_loc(start))

    def _non_skip_shuffle_labels(self) -> tuple[Symbol, ...]:
        """Parse non skip shuffle labels from the current token stream."""
        return tuple(
            label
            for label in self._shuffle_labels(allow_skip=True)
            if label is not None
        )

    def _shuffle_labels(self, *, allow_skip: bool) -> tuple[Symbol | None, ...]:
        """Parse shuffle labels from the current token stream."""
        labels: list[Symbol | None] = []
        self._skip_newlines()
        if self._check(TokenKind.ARROW, TokenKind.RPAREN):
            return ()
        while True:
            label = self._shuffle_label(allow_skip=allow_skip)
            if label is not _EXPANDED_SKIP:
                labels.append(label)
            else:
                value = self._previous.value
                count = (
                    int(value[1:])
                    if value.startswith("_") and value[1:].isdecimal()
                    else 0
                )
                labels.extend(None for _ in range(count))
            self._skip_newlines()
            if not self._match(TokenKind.COMMA):
                return tuple(labels)
            self._skip_newlines()

    def _shuffle_label(self, *, allow_skip: bool) -> Symbol | None | object:
        """Parse shuffle label from the current token stream."""
        token = self._expect(TokenKind.IDENT)
        value = token.value
        if value == "_":
            if allow_skip:
                return None
            self._error("'_' cannot be used here")
        if value.startswith("_") and value[1:].isdecimal():
            return _EXPANDED_SKIP
        return Symbol(value)

    def _cast(self, start: Token, *, optional_spelling: bool = False) -> _ChainPiece:
        """Parse ``as[T]``, ``as?[T]``, or ``as![T]``."""
        checked = self._check_op("!")
        if checked:
            self._advance()
        self._expect(TokenKind.LBRACKET)
        target = self.parse_type_expression()
        self._expect(TokenKind.RBRACKET)
        return _ChainPiece(
            (
                CastNode(
                    target,
                    checked=checked,
                    optional=optional_spelling,
                    location=_loc(start),
                ),
            ),
            breaks_chain=True,
        )

    def _empty_list_cast(self) -> CastNode | None:
        """Parse empty list cast from the current token stream."""
        if self._check_ident("as", "as?"):
            start = self._advance()
            [cast] = self._cast(start, optional_spelling=start.value == "as?").nodes
            if not isinstance(cast, CastNode):
                self._error("expected cast")
            return cast
        return None

    def _string_node(self, token: Token) -> ASTNode:
        """Parse string node from the current token stream."""
        raw = token.raw if token.raw is not None else token.value
        parts = _string_parts(raw, token)
        if len(parts) == 1 and isinstance(parts[0], str):
            return StringLiteralNode(parts[0], location=_loc(token))
        return StringInterpolationNode(parts, location=_loc(token))

    def _qualified_symbol(self, start: Token) -> Symbol:
        """Parse qualified symbol from the current token stream."""
        parts = [start.value]
        while self._check(TokenKind.DOT) and self._peek(1).kind in (
            TokenKind.IDENT,
            TokenKind.OP,
        ):
            self._advance()
            parts.append(self._advance().value)
        name = Symbol(parts[-1], tuple(parts[:-1]))
        if self._match(TokenKind.DOUBLE_COLON):
            if not self._match(TokenKind.IDENT, TokenKind.OP):
                self._error("expected qualified element name")
            token = self._previous
            name = Symbol(f"{name.dotted()}::{token.value}")
        return name

    def _operator_run(self, start: Token) -> Symbol:
        """Greedily merge a whitespace-free run of OP tokens into one name.

        The lexer emits a single-character OP token per operator character
        (so that e.g. `Number++` can distinguish a rank-2 list type from two
        separate unary operators). Here in expression/element position we
        glue an unbroken, whitespace-free run of such tokens back together
        into a single operator name, e.g. `+` `+` (adjacent) -> `++`, while a
        run broken by whitespace (`+ +`) stays as two separate operators.
        """
        parts = [start.value]
        last = start
        while self._check(TokenKind.OP) and self._adjacent(last, self._current):
            if self._current.value == "<" and self._peek(1).kind in {
                TokenKind.IDENT,
                TokenKind.OP,
                TokenKind.LBRACE,
            }:
                break
            last = self._advance()
            parts.append(last.value)
        name = "".join(parts)
        if self._match(TokenKind.DOUBLE_COLON):
            if not self._match(TokenKind.IDENT, TokenKind.OP):
                self._error("expected qualified element name")
            token = self._previous
            name = f"{name}::{token.value}"
        return Symbol(name)

    def _variable(self, start: Token) -> _ChainPiece:
        """Parse variable from the current token stream."""
        if self._check(TokenKind.LPAREN):
            return _ChainPiece(self._multiple_assignment(start), True)
        if self._match(TokenKind.LBRACKET):
            selectors = self._index_selectors()
            if self._match(TokenKind.ASSIGN, TokenKind.AUG_ASSIGN):
                op = self._previous.kind
                rhs = self._assignment_rhs()
                index_values = self._selector_expressions(selectors)
                if op is TokenKind.AUG_ASSIGN:
                    # A stack receiver must survive while the update body uses the
                    # ambient stack. Stashing it internally keeps that receiver out
                    # of the operand order, matching `$x := ...` semantics.
                    temporary = Symbol(f"\x00index_receiver_{self._temporary_index}")
                    self._temporary_index += 1
                    return _ChainPiece(
                        (
                            SetVariableNode(temporary, location=_loc(start)),
                            GetVariableNode(temporary, location=_loc(start)),
                            *index_values,
                            IndexAccessNode(
                                selectors, grouped_update=True, location=_loc(start)
                            ),
                            *rhs,
                            GetVariableNode(temporary, location=_loc(start)),
                            *index_values,
                            IndexSetNode(
                                selectors, grouped_update=True, location=_loc(start)
                            ),
                        ),
                        True,
                    )
                return _ChainPiece(
                    (
                        *rhs,
                        StackShuffleNode(
                            Symbol("move"),
                            (Symbol("receiver"), Symbol("value")),
                            (Symbol("value"), Symbol("receiver")),
                            location=_loc(start),
                        ),
                        *index_values,
                        IndexSetNode(selectors, location=_loc(start)),
                    ),
                    True,
                )
            return _ChainPiece(
                (
                    *self._selector_expressions(selectors),
                    IndexAccessNode(selectors, location=_loc(start)),
                ),
                True,
            )
        if self._match(TokenKind.ARROW, TokenKind.DOT):
            first_kind = (
                "safe_field"
                if self._previous.kind is TokenKind.ARROW
                else "field"
            )
            path: list[tuple[str, Symbol]] = [
                (first_kind, self._symbol("expected field name"))
            ]
            while True:
                if self._match(TokenKind.DOT):
                    path.append(("field", self._symbol("expected field name")))
                    continue
                if self._match(TokenKind.ARROW):
                    path.append(
                        ("safe_field", self._symbol("expected field name"))
                    )
                    continue
                break

            if self._match(TokenKind.ASSIGN, TokenKind.AUG_ASSIGN):
                if len(path) != 1:
                    self._error(
                        "assignment through a stack member chain is not supported"
                    )
                kind, field = path[0]
                optional_safe = kind == "safe_field"
                op = self._previous.kind
                rhs = self._assignment_rhs()
                prefix = (
                    (
                        FieldAccessNode(
                            field,
                            optional_safe=optional_safe,
                            location=_loc(start),
                        ),
                    )
                    if op is TokenKind.AUG_ASSIGN
                    else ()
                )
                return _ChainPiece(
                    (
                        *prefix,
                        *rhs,
                        FieldSetNode(
                            field,
                            optional_safe=optional_safe,
                            location=_loc(start),
                        ),
                    ),
                    True,
                )

            return _ChainPiece(
                tuple(
                    FieldAccessNode(
                        field,
                        optional_safe=kind == "safe_field",
                        location=_loc(start),
                    )
                    for kind, field in path
                ),
                breaks_chain=True,
            )
        name = self._symbol("expected variable name")
        path: list[tuple[str, object]] = []
        while True:
            if self._match(TokenKind.LBRACKET):
                path.append(("index", self._index_selectors()))
                continue
            if self._match(TokenKind.DOT):
                path.append(("field", self._symbol("expected field name")))
                continue
            if self._match(TokenKind.ARROW):
                path.append(("safe_field", self._symbol("expected field name")))
                continue
            break

        if path:
            if self._match(TokenKind.ASSIGN, TokenKind.AUG_ASSIGN):
                op = self._previous.kind
                rhs = self._assignment_rhs()
                nodes: list[ASTNode] = []
                if op is TokenKind.AUG_ASSIGN:
                    nodes.extend(
                        self._variable_path_read(
                            name, path, start, grouped_terminal=True
                        )
                    )
                nodes.extend(rhs)
                nodes.extend(
                    self._variable_path_rebuild(
                        name,
                        path,
                        start,
                        grouped_terminal=op is TokenKind.AUG_ASSIGN,
                    )
                )
                nodes.append(SetVariableNode(name, location=_loc(start)))
                return _ChainPiece(tuple(nodes), True)
            return _ChainPiece(
                self._variable_path_read(name, path, start),
                True,
            )
        declared_type = None
        if self._match(TokenKind.COLON):
            declared_type = self.parse_type_expression()
        if self._match(TokenKind.ASSIGN, TokenKind.AUG_ASSIGN):
            op = self._previous.kind
            rhs = self._assignment_rhs()
            if declared_type is not None:
                rhs = _contextual_empty_list(rhs, declared_type)
                rhs = (*rhs, *_declared_tag_applications(declared_type, start))
            prefix = (
                (GetVariableNode(name, location=_loc(start)),)
                if op is TokenKind.AUG_ASSIGN
                else ()
            )
            return _ChainPiece(
                (
                    *prefix,
                    *rhs,
                    SetVariableNode(
                        name,
                        declared_type,
                        location=_loc(start),
                    ),
                ),
                True,
            )
        if declared_type is not None:
            self._error("expected '=' after variable type annotation")
        if self._check(TokenKind.LPAREN) and self._adjacent(
            self._previous,
            self._current,
        ):
            self._advance()
            call_args = (
                ()
                if self._match(TokenKind.RPAREN)
                else self._call_arguments()
            )
            if not any(arg.placeholder or arg.name is not None for arg in call_args):
                return _ChainPiece(
                    (
                        *(_flatten(tuple(arg.value for arg in call_args))),
                        GetVariableNode(name, location=_loc(start)),
                        ElementNode(
                            Symbol("call"),
                            explicit_call=True,
                            location=_loc(start),
                        ),
                    ),
                    True,
                )
            return _ChainPiece(
                (
                    GetVariableNode(name, location=_loc(start)),
                    ElementNode(
                        Symbol("call"),
                        call_args=call_args,
                        explicit_call=True,
                        location=_loc(start),
                    ),
                ),
                True,
            )
        return _ChainPiece((GetVariableNode(name, location=_loc(start)),), True)

    def _assignment_rhs(self) -> tuple[ASTNode, ...]:
        """Parse assignment input, respecting static-expression separators."""
        terminators = set(_LINE_TERMINATORS)
        if self._where_clause_depth:
            terminators.update({TokenKind.COMMA, TokenKind.RPAREN})
        return self._chain_until(terminators)

    def _variable_path_read(
        self,
        name: Symbol,
        path: list[tuple[str, object]],
        start: Token,
        *,
        grouped_terminal: bool = False,
    ) -> tuple[ASTNode, ...]:
        """Lower a variable access path into ordinary field/index reads."""
        nodes: list[ASTNode] = [GetVariableNode(name, location=_loc(start))]
        for depth, (kind, payload) in enumerate(path):
            if kind in {"field", "safe_field"}:
                nodes.append(
                    FieldAccessNode(
                        payload,
                        optional_safe=kind == "safe_field",
                        location=_loc(start),
                    )
                )
                continue
            selectors = payload
            nodes.extend(self._selector_expressions(selectors))
            nodes.append(
                IndexAccessNode(
                    selectors,
                    grouped_update=grouped_terminal and depth == len(path) - 1,
                    location=_loc(start),
                )
            )
        return tuple(nodes)

    def _variable_path_rebuild(
        self,
        name: Symbol,
        path: list[tuple[str, object]],
        start: Token,
        *,
        grouped_terminal: bool = False,
    ) -> tuple[ASTNode, ...]:
        """Reconstruct every parent in a mixed field/index assignment path."""
        nodes: list[ASTNode] = []
        for depth in range(len(path) - 1, -1, -1):
            kind, payload = path[depth]
            parent_path = path[:depth]
            nodes.extend(self._variable_path_read(name, parent_path, start))
            if kind in {"field", "safe_field"}:
                nodes.append(
                    StackShuffleNode(
                        Symbol("move"),
                        (Symbol("value"), Symbol("receiver")),
                        (Symbol("receiver"), Symbol("value")),
                        location=_loc(start),
                    )
                )
                nodes.append(
                    FieldSetNode(
                        payload,
                        optional_safe=kind == "safe_field",
                        location=_loc(start),
                    )
                )
                continue
            selectors = payload
            nodes.extend(self._selector_expressions(selectors))
            nodes.append(
                IndexSetNode(
                    selectors,
                    grouped_update=grouped_terminal and depth == len(path) - 1,
                    location=_loc(start),
                )
            )
        return tuple(nodes)

    def _multiple_assignment(
        self,
        start: Token,
        *,
        constant: bool = False,
    ) -> tuple[ASTNode, ...]:
        """Parse multiple assignment from the current token stream."""
        self._expect(TokenKind.LPAREN)
        targets: list[SetVariableNode] = []
        self._skip_newlines()
        if self._match(TokenKind.RPAREN):
            self._error("multiple assignment requires at least one target")
        while True:
            self._match(TokenKind.DOLLAR)
            target_start = self._current
            name = self._symbol("expected assignment target")
            declared_type = None
            if self._match(TokenKind.COLON):
                declared_type = self.parse_type_expression()
            targets.append(
                SetVariableNode(
                    name,
                    declared_type,
                    constant=constant,
                    location=_loc(target_start),
                )
            )
            self._skip_newlines()
            if not self._match(TokenKind.COMMA):
                break
            self._skip_newlines()
        self._expect(TokenKind.RPAREN)
        self._expect(TokenKind.ASSIGN)
        values: list[ASTNode] = []
        while True:
            values.extend(
                self._chain_until(_LINE_TERMINATORS | {TokenKind.COMMA})
            )
            if not self._match(TokenKind.COMMA):
                break
            self._skip_newlines()
        return (
            *tuple(values),
            SetVariablesNode(
                tuple(targets),
                location=_loc(start),
            ),
        )

    def _index_selectors(self) -> tuple[IndexSelector, ...]:
        """Parse index selectors from the current token stream."""
        selectors: list[IndexSelector] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACKET):
            self._error("empty indexing expressions are invalid")
        while True:
            start: tuple[ASTNode, ...] = ()
            stop: tuple[ASTNode, ...] = ()
            step: tuple[ASTNode, ...] = ()
            is_slice = False
            if not self._check(TokenKind.COLON, TokenKind.DOUBLE_COLON):
                start = self._chain_until(
                    {
                        TokenKind.COMMA,
                        TokenKind.COLON,
                        TokenKind.DOUBLE_COLON,
                        TokenKind.RBRACKET,
                    }
                )
            if self._match(TokenKind.DOUBLE_COLON):
                is_slice = True
                if not self._check(TokenKind.COMMA, TokenKind.RBRACKET):
                    step = self._chain_until({TokenKind.COMMA, TokenKind.RBRACKET})
            elif self._match(TokenKind.COLON):
                is_slice = True
                if not self._check(
                    TokenKind.COLON,
                    TokenKind.DOUBLE_COLON,
                    TokenKind.COMMA,
                    TokenKind.RBRACKET,
                ):
                    stop = self._chain_until(
                        {
                            TokenKind.COMMA,
                            TokenKind.COLON,
                            TokenKind.DOUBLE_COLON,
                            TokenKind.RBRACKET,
                        }
                    )
                if self._match(TokenKind.DOUBLE_COLON):
                    self._error("slice syntax cannot contain three ':' characters")
                if self._match(TokenKind.COLON):
                    if not self._check(TokenKind.COMMA, TokenKind.RBRACKET):
                        step = self._chain_until({TokenKind.COMMA, TokenKind.RBRACKET})
            selectors.append(IndexSelector(start, stop, step, is_slice))
            if self._match(TokenKind.RBRACKET):
                return tuple(selectors)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _selector_expressions(
        self,
        selectors: tuple[IndexSelector, ...],
    ) -> tuple[ASTNode, ...]:
        """Return selector expressions with index-path wildcard shorthand lowered."""
        nodes: list[ASTNode] = []
        for selector in selectors:
            nodes.extend(
                self._lower_index_wildcard_shorthand(node)
                for node in selector.start
            )
            nodes.extend(selector.stop)
            nodes.extend(selector.step)
        return tuple(nodes)

    def _lower_index_wildcard_shorthand(self, node: ASTNode) -> ASTNode:
        """Translate ``_`` list items to ``\\None`` only in direct index syntax."""
        if not isinstance(node, ListLiteralNode):
            return node
        fixed_path = any(
            isinstance(item, ElementNode) and item.name.text in {"_", "\\None"}
            for expression in node.items for item in expression
        )
        items = tuple(
            tuple(
                ElementNode(Symbol("\\None"), location=item.location)
                if isinstance(item, ElementNode) and item.name.text == "_"
                else self._lower_index_wildcard_shorthand(item)
                for item in expression
            ) for expression in node.items
        )
        return TupleLiteralNode(items, location=node.location) if fixed_path else replace(node, items=items)

    def _comma_expressions(self, closer: TokenKind) -> tuple[tuple[ASTNode, ...], ...]:
        """Parse comma expressions from the current token stream."""
        items: list[tuple[ASTNode, ...]] = []
        self._skip_newlines()
        if self._match(closer):
            return ()
        while True:
            item = self._chain_until({TokenKind.COMMA, TokenKind.NEWLINE, closer})
            if not item:
                self._error("expected expression")
            items.append(item)
            self._skip_newlines()
            if self._match(closer):
                return tuple(items)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()
            if self._match(closer):
                return tuple(items)

    def _argument_expressions(
        self, closer: TokenKind
    ) -> tuple[tuple[ASTNode, ...], ...]:
        """Parse argument expressions from the current token stream."""
        self._skip_newlines()
        if self._check(closer):
            self._error("empty argument lists are invalid; use a \\nilad name")
        return self._comma_expressions(closer)

    def _call_arguments(self) -> tuple[CallArgument, ...]:
        """Parse call arguments from the current token stream."""
        args: list[CallArgument] = []
        self._skip_newlines()
        if self._check(TokenKind.RPAREN):
            self._error("empty argument lists are invalid; use a \\nilad name")
        while True:
            self._skip_newlines()
            if self._check(TokenKind.IDENT) and self._current.value == "_" and (
                self._peek(1).kind
                in {TokenKind.COMMA, TokenKind.RPAREN, TokenKind.NEWLINE}
            ):
                self._advance()
                args.append(CallArgument(placeholder=True))
            elif (
                self._check(TokenKind.IDENT)
                and self._peek(1).kind is TokenKind.ASSIGN
            ):
                name = Symbol(self._advance().value)
                self._expect(TokenKind.ASSIGN)
                value = self._chain_until(
                    {TokenKind.COMMA, TokenKind.RPAREN, TokenKind.NEWLINE}
                )
                if not value:
                    self._error("expected named argument value")
                args.append(CallArgument(name, value))
            else:
                value = self._chain_until(
                    {TokenKind.COMMA, TokenKind.RPAREN, TokenKind.NEWLINE}
                )
                if not value:
                    self._error("expected argument")
                prefix_count = 0
                while (
                    prefix_count < len(value)
                    and isinstance(value[prefix_count], MinimumRankNode)
                ):
                    prefix_count += 1
                if prefix_count and prefix_count < len(value):
                    value = (*value[prefix_count:], *value[:prefix_count])
                args.append(CallArgument(None, value))
            self._skip_newlines()
            if self._match(TokenKind.RPAREN):
                return tuple(args)
            self._expect(TokenKind.COMMA)

    def _element_generic_arguments(self, start: Token) -> tuple[Type | None, ...]:
        """Parse caller-supplied generic arguments in square brackets."""
        if not self._check(TokenKind.LBRACKET) or not self._adjacent(start, self._current):
            return ()
        self._advance()
        return self._element_type_arguments(
            close_kind=TokenKind.RBRACKET,
            empty_message="empty generic argument list is invalid",
        )

    def _element_disambiguation(self, start: Token) -> tuple[Type | None, ...]:
        """Parse positional overload hints in adjacent curly braces."""
        if not self._check(TokenKind.LBRACE) or not self._adjacent(start, self._current):
            return ()
        self._advance()
        return self._element_type_arguments(
            close_kind=TokenKind.RBRACE,
            empty_message="empty element disambiguation is invalid",
        )

    def _element_type_arguments(
        self,
        *,
        close_kind: TokenKind | None = None,
        close_op: str | None = None,
        empty_message: str,
    ) -> tuple[Type | None, ...]:
        """Parse a non-empty comma-separated type or underscore list."""
        values: list[Type | None] = []
        self._skip_newlines()
        if (close_kind is not None and self._check(close_kind)) or (
            close_op is not None and self._check_op(close_op)
        ):
            self._error(empty_message)
        while True:
            if self._check(TokenKind.IDENT) and self._current.value == "_":
                self._advance()
                values.append(None)
            else:
                values.append(self.parse_type_expression())
            self._skip_newlines()
            if close_kind is not None and self._match(close_kind):
                return tuple(values)
            if close_op is not None and self._check_op(close_op):
                self._advance()
                return tuple(values)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _modifier_arguments(self, start: Token) -> tuple[FunctionNode, ...]:
        """Parse modifier arguments from the current token stream."""
        if self._match(TokenKind.LPAREN):
            return tuple(
                self._modifier_function(start, body)
                for body in self._argument_expressions(TokenKind.RPAREN)
            )

        body = self._chain_segment_until(_LINE_TERMINATORS | {TokenKind.PIPE})
        if not body:
            self._error("expected modifier function body")
        return (self._modifier_function(start, body),)

    def _modifier_function(
        self,
        start: Token,
        body: tuple[ASTNode, ...],
    ) -> FunctionNode:
        """Parse modifier function from the current token stream."""
        if len(body) == 1 and isinstance(body[0], FunctionNode):
            return body[0]
        if (
            len(body) == 1
            and isinstance(body[0], NumberLiteralNode)
            and body[0].value.startswith("-")
            and len(body[0].value) > 1
        ):
            # Modifier shorthand is stack-oriented: ``apply: -1`` means
            # subtract one from the cycled argument. A constant negative value
            # remains available explicitly as ``apply(fn => -1)``.
            literal = replace(body[0], value=body[0].value[1:])
            body = (literal, ElementNode(name=Symbol("-"), location=body[0].location))
        return FunctionNode(body=body, location=_loc(start))

    def _record_fields(self) -> tuple[tuple[Symbol, tuple[ASTNode, ...]], ...]:
        """Parse record fields, including entries split across source lines."""
        fields: list[tuple[Symbol, tuple[ASTNode, ...]]] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACE):
            return ()
        while True:
            name = self._symbol("expected record field")
            self._expect(TokenKind.FAT_ARROW)
            value = self._chain_until(
                {TokenKind.COMMA, TokenKind.NEWLINE, TokenKind.RBRACE}
            )
            if not value:
                self._error("expected record field value")
            fields.append((name, value))
            self._skip_newlines()
            if self._match(TokenKind.RBRACE):
                return tuple(fields)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()
            if self._match(TokenKind.RBRACE):
                return tuple(fields)

    def _dict_entries(
        self,
    ) -> tuple[tuple[tuple[ASTNode, ...], tuple[ASTNode, ...]], ...]:
        """Parse dictionary entries, including entries split across source lines."""
        entries: list[tuple[tuple[ASTNode, ...], tuple[ASTNode, ...]]] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACE):
            return ()
        while True:
            key = self._chain_until({TokenKind.FAT_ARROW})
            if not key:
                self._error("expected dictionary key")
            self._expect(TokenKind.FAT_ARROW)
            value = self._chain_until(
                {TokenKind.COMMA, TokenKind.NEWLINE, TokenKind.RBRACE}
            )
            if not value:
                self._error("expected dictionary value")
            entries.append((key, value))
            self._skip_newlines()
            if self._match(TokenKind.RBRACE):
                return tuple(entries)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()
            if self._match(TokenKind.RBRACE):
                return tuple(entries)

    def _annotations(self) -> tuple[ASTNode, ...]:
        """Parse annotations from the current token stream."""
        annotations: list[ASTNode] = []
        while self._check(TokenKind.AT) and self._peek(1).kind is not TokenKind.AT:
            self._advance()
            start = self._previous
            name = self._symbol("expected annotation name")
            args: tuple[ASTNode, ...] = ()
            kwargs: tuple[tuple[Symbol, ASTNode], ...] = ()
            if self._match(TokenKind.LPAREN):
                args, kwargs = self._annotation_arguments(TokenKind.RPAREN)
            annotations.append(AnnotationNode(name, args, kwargs, location=_loc(start)))
            self._skip_newlines()
        return tuple(annotations)

    def _annotation_arguments(
        self,
        closer: TokenKind,
    ) -> tuple[tuple[ASTNode, ...], tuple[tuple[Symbol, ASTNode], ...]]:
        """Parse annotation arguments from the current token stream."""
        args: list[ASTNode] = []
        kwargs: list[tuple[Symbol, ASTNode]] = []
        self._skip_newlines()
        if self._match(closer):
            return (), ()
        while True:
            self._skip_newlines()
            if self._check(TokenKind.IDENT) and self._peek(1).kind is TokenKind.ASSIGN:
                key = Symbol(self._advance().value)
                self._expect(TokenKind.ASSIGN)
                value = self._annotation_argument_value({TokenKind.COMMA, closer})
                kwargs.append((key, value))
            else:
                args.append(self._annotation_argument_value({TokenKind.COMMA, closer}))
            self._skip_newlines()
            if self._match(closer):
                return tuple(args), tuple(kwargs)
            self._expect(TokenKind.COMMA)

    def _annotation_argument_value(self, terminators: set[TokenKind]) -> ASTNode:
        """Parse annotation argument value from the current token stream."""
        values = self._chain_until(terminators)
        if len(values) != 1:
            self._error("annotation arguments must contain exactly one expression")
        return values[0]

    def _params(
        self,
        *,
        allow_defaults: bool = False,
        allow_empty: bool = False,
    ) -> tuple[FunctionParam, ...]:
        """Parse params from the current token stream."""
        params: list[FunctionParam] = []
        seen_default = False
        self._skip_newlines()
        if allow_empty and self._match(TokenKind.RPAREN):
            return ()
        if self._check(TokenKind.RPAREN):
            self._error("empty parameter lists are invalid; use a \\nilad name")
        while True:
            name: Symbol | None = None
            typ: Type | None = None
            default: tuple[ASTNode, ...] = ()
            if self._match(TokenKind.COLON):
                typ = self._parameter_type()
            elif self._check(TokenKind.IDENT) and self._peek(1).kind == TokenKind.COLON:
                name = Symbol(self._advance().value)
                self._expect(TokenKind.COLON)
                typ = self._parameter_type()
            else:
                name = self._symbol("expected parameter")
            if self._match(TokenKind.ASSIGN):
                if not allow_defaults:
                    self._error("parameter defaults are only allowed on define")
                default = self._chain_until({TokenKind.COMMA, TokenKind.RPAREN})
                if not default:
                    self._error("expected default parameter value")
                seen_default = True
            elif seen_default:
                self._error("parameters with defaults must be trailing")
            params.append(FunctionParam(name, typ, default))
            self._skip_newlines()
            if self._match(TokenKind.RPAREN):
                return tuple(params)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _returns(self) -> tuple[Type, ...] | None:
        """Parse returns from the current token stream."""
        self._skip_newlines()
        if not self._match(TokenKind.ARROW):
            return None
        if self._check(TokenKind.FAT_ARROW):
            return ()
        returns: list[Type] = []
        while not self._check(
            TokenKind.FAT_ARROW, TokenKind.NEWLINE, TokenKind.EOF
        ) and not self._check_ident("where"):
            returns.append(self.parse_type_expression())
            if not self._match(TokenKind.COMMA):
                break
        return tuple(returns)

    def _parameter_type(self) -> Type:
        """Parse parameter type from the current token stream."""
        previous = self._allow_variadic_tuple_type
        self._allow_variadic_tuple_type = True
        try:
            return self.parse_type_expression()
        finally:
            self._allow_variadic_tuple_type = previous

    def parse_type_expression(self) -> Type:
        """Parse one complete type expression from the current token stream."""
        return self._type_union()

    def _type_union(self) -> Type:
        """Parse type union from the current token stream."""
        typ = self._type_intersection()
        while self._match(TokenKind.PIPE):
            typ = U(typ, self._type_intersection())
        return typ

    def _type_intersection(self) -> Type:
        """Parse type intersection from the current token stream."""
        typ = self._type_tagged()
        while self._check_op("&"):
            self._advance()
            typ = I(typ, self._type_tagged())
        return typ

    def _type_tagged(self) -> Type:
        """Parse type tagged from the current token stream."""
        tags: list[DataTag] = []
        exact = False
        if self._match(TokenKind.LBRACKET):
            exact = True
            self._skip_newlines()
            while not self._check(TokenKind.RBRACKET):
                if not (
                    self._check(TokenKind.OP)
                    and self._current.value.startswith("#")
                ):
                    self._error("expected data tag in exact tag set")
                tag = _tag_from_token(self._advance())
                if tag.absent:
                    self._error("exact tag sets can only contain present tags")
                tags.append(tag)
                self._skip_newlines()
                self._match(TokenKind.COMMA)
                self._skip_newlines()
            self._expect(TokenKind.RBRACKET)
        else:
            while self._check(TokenKind.OP) and self._current.value.startswith("#"):
                tags.append(_tag_from_token(self._advance()))
        typ = self._type_postfix()
        if exact:
            return ExactTags(typ, *tags)
        return Tagged(typ, *tags) if tags else typ

    def _type_postfix(self) -> Type:
        """Parse type postfix from the current token stream."""
        typ = self._type_primary()
        while True:
            if self._row_constraint_ahead():
                self._expect(TokenKind.LPAREN)
                typ = Row(typ, *self._row_fields())
                continue
            if self._check_op("<"):
                self._advance()
                if not isinstance(typ, FunctionType):
                    self._error("element tags can only be attached to function types")
                typ = FunctionType(typ.params, typ.returns, self._element_tag_list())
                continue
            if self._match_ident("exact"):
                if isinstance(typ, ExactType):
                    self._error("type is already marked exact")
                typ = Exact(typ)
                break
            if self._match_ident("novec"):
                if isinstance(typ, NoVecType):
                    self._error("type is already marked novec")
                typ = NoVec(typ)
                break
            if self._check(TokenKind.OP) and self._current.value in {
                "+",
                "*",
                "~",
                "^",
                ">",
                "?",
            }:
                op_token = self._advance()
                op = op_token.value
                rank = self._type_postfix_rank(op_token, op)
                if op == "?":
                    if isinstance(rank, RankVariable):
                        self._error("optional type depth cannot use a rank variable")
                    typ = _optionalize_type(typ, rank)
                    continue
                collection: type[CollectionType] | None = {
                    "+": ListExactType,
                    "*": ListMinType,
                    "~": ListRuggedType,
                    "^": ArrayExactType,
                    ">": ArrayMinType,
                }.get(op)
                if collection is not None:
                    typ = self._apply_collection_postfix(
                        typ,
                        collection,
                        rank,
                        op_token,
                    )
                continue
            break
        return typ

    def _type_postfix_rank(
        self, op_token: Token, op: str
    ) -> int | RankVariable:
        """Parse type postfix rank from the current token stream."""
        if self._match(TokenKind.NUMBER):
            token = self._previous
            if not token.value.isdecimal():
                raise ParseError(
                    "type rank must be a decimal integer",
                    line=token.line,
                    column=token.column,
                )
            rank = int(token.value)
            if rank < 1:
                raise ParseError(
                    "type rank must be a positive integer",
                    line=token.line,
                    column=token.column,
                )
            return rank
        if op != "?" and self._match(TokenKind.DOLLAR):
            return RankVariable(self._expect(TokenKind.IDENT).value)

        # Only fold contiguous, whitespace-free operator characters into the
        # rank count. `Number++` is rank 2, but `Number+ +` stops counting at
        # the whitespace boundary, since a space-separated run is a fresh
        # operator, not a rank continuation.
        rank = 1
        prev_tok = op_token
        while (
            self._check(TokenKind.OP)
            and self._current.value == op
            and self._adjacent(prev_tok, self._current)
        ):
            prev_tok = self._advance()
            rank += 1
        return rank

    def _apply_collection_postfix(
        self,
        typ: Type,
        collection: type[CollectionType],
        rank: int | RankVariable,
        token: Token,
    ) -> Type:
        """Parse apply collection postfix from the current token stream."""
        if not isinstance(typ, CollectionType):
            return C(collection, typ, rank)

        inner_collection = type(typ)
        if (
            isinstance(typ.rank, int)
            and isinstance(rank, int)
            and _collection_postfix_superset(inner_collection, collection)
        ):
            return C(collection, typ.base, typ.rank + rank)

        if inner_collection is collection:
            return C(collection, typ, rank)

        raise ParseError(
            "cannot combine rank type suffixes without an optional barrier",
            line=token.line,
            column=token.column,
        )

    def _row_constraint_ahead(self) -> bool:
        """Return the Boolean result of row constraint ahead from the current parser token stream."""
        if not self._check(TokenKind.LPAREN):
            return False
        ahead = 1
        while self._peek(ahead).kind is TokenKind.NEWLINE:
            ahead += 1
        return self._peek(ahead).kind is TokenKind.DOT

    def _row_fields(self) -> tuple[RowField, ...]:
        """Parse row fields from the current token stream."""
        fields: list[RowField] = []
        self._skip_newlines()
        if self._check(TokenKind.RPAREN):
            self._error("row constraint requires at least one field")
        while True:
            self._expect(TokenKind.DOT)
            name = self._symbol("expected row field name")
            self._expect(TokenKind.COLON)
            fields.append(Field(name, self.parse_type_expression()))
            self._skip_newlines()
            if self._match(TokenKind.RPAREN):
                return tuple(fields)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _type_primary(self) -> Type:
        """Parse type primary from the current token stream."""
        if self._check_ident("trait"):
            return self._anonymous_trait_type()
        if self._match(TokenKind.AT):
            token = self._expect(TokenKind.NUMBER)
            if not token.value.isdecimal() or int(token.value) < 1:
                raise ParseError(
                    "anonymous generic index must be a positive integer",
                    line=token.line,
                    column=token.column,
                )
            return V(f"@{int(token.value)}")
        if self._match(TokenKind.LBRACE):
            items: list[TupleTypeItem] = []
            has_repeated = False
            if self._match(TokenKind.RBRACE):
                return N(Symbol("{}"))
            while True:
                item = self.parse_type_expression()
                repeated = self._match_ellipsis()
                if repeated:
                    has_repeated = True
                    if not self._allow_variadic_tuple_type:
                        self._error(
                            "arbitrary-length tuple types are only allowed in "
                            "parameters"
                        )
                items.append(TupleTypeItem(item, repeated))
                if self._match(TokenKind.RBRACE):
                    if has_repeated:
                        return TupVariadic(*items)
                    return Tup(*(item.typ for item in items))
                self._expect(TokenKind.COMMA)
        if self._match(TokenKind.IDENT):
            parts = [self._previous.value]
            while self._check(TokenKind.DOT) and self._peek(1).kind == TokenKind.IDENT:
                self._advance()
                parts.append(self._expect(TokenKind.IDENT).value)
            name = parts[-1]
            namespace = tuple(parts[:-1])
            optional_depth = 0
            if name.endswith("?"):
                bare_name = name.rstrip("?")
                optional_depth = len(name) - len(bare_name)
                name = bare_name
                if (
                    optional_depth == 1
                    and self._check(TokenKind.NUMBER)
                    and self._adjacent(self._previous, self._current)
                ):
                    token = self._advance()
                    if not token.value.isdecimal():
                        raise ParseError(
                            "optional type depth must be a decimal integer",
                            line=token.line,
                            column=token.column,
                        )
                    optional_depth = int(token.value)
            if name == "None":
                typ = NoneType()
                return _optionalize_type(typ, optional_depth)
            args: list[Type] = []
            if self._match(TokenKind.LBRACKET):
                if name == "Function":
                    params = self._type_list_until({TokenKind.ARROW})
                    self._expect(TokenKind.ARROW)
                    returns = self._type_list_until({TokenKind.RBRACKET})
                    self._expect(TokenKind.RBRACKET)
                    return _optionalize_type(Fn(params, returns), optional_depth)
                if not self._match(TokenKind.RBRACKET):
                    while True:
                        args.append(self.parse_type_expression())
                        if self._match(TokenKind.RBRACKET):
                            break
                        self._expect(TokenKind.COMMA)
            if name == "Function" and args:
                return _optionalize_type(args[0], optional_depth)
            if name == "Function":
                return _optionalize_type(Fn(), optional_depth)
            return _optionalize_type(
                N(Symbol(name, namespace), *args),
                optional_depth,
            )
        if self._match(TokenKind.LPAREN):
            typ = self.parse_type_expression()
            self._expect(TokenKind.RPAREN)
            return typ
        self._error("expected type")

    def _anonymous_trait_type(self) -> Type:
        """Parse anonymous trait type from the current token stream."""
        self._expect_ident("trait")
        generics = self._generic_names()
        self._expect(TokenKind.FAT_ARROW)
        requirements: list[AnonymousTraitRequirement] = []
        single_line = not self._check(TokenKind.NEWLINE)
        self._skip_newlines()
        while not self._check(TokenKind.EOF) and not self._check_ident("end"):
            start = self._expect_ident("extend")
            requirement = self._anonymous_trait_requirement(
                self._extend(start),
                generics,
            )
            requirements.append(requirement)
            if single_line:
                break
            self._skip_separators()
        self._consume_optional_end()
        return AnonymousTrait(generics, requirements)

    def _anonymous_trait_requirement(
        self,
        node: TraitRequirementNode,
        generics: tuple[Symbol, ...],
    ) -> AnonymousTraitRequirement:
        """Parse anonymous trait requirement from the current token stream."""
        params = tuple(
            _local_generic_type(_parser_param_type(param, index), generics)
            for index, param in enumerate(node.params or ())
        )
        returns = tuple(
            _local_generic_type(ret, generics) for ret in node.returns or ()
        )
        return AnonymousTraitRequirement(
            node.name,
            Overload(
                params,
                returns,
                param_names=tuple(param.name for param in node.params or ()),
            ),
        )

    def _element_tag_list(self) -> frozenset[ElementTag]:
        """Parse element tag list from the current token stream."""
        tags: list[ElementTag] = []
        if self._check_op(">"):
            self._advance()
            return frozenset()
        while True:
            absent = self._check_op("!")
            if absent:
                self._advance()
            name = self._symbol("expected element tag name")
            args: list[Type] = []
            if self._match(TokenKind.LBRACKET):
                if not self._match(TokenKind.RBRACKET):
                    while True:
                        args.append(self.parse_type_expression())
                        if self._match(TokenKind.RBRACKET):
                            break
                        self._expect(TokenKind.COMMA)
            tags.append(ElementTag(name, tuple(args), absent))
            if self._check_op(">"):
                self._advance()
                return frozenset(tags)
            self._expect(TokenKind.COMMA)

    def _type_list_until(self, terminators: set[TokenKind]) -> tuple[Type, ...]:
        """Parse type list until from the current token stream."""
        items: list[Type] = []
        if self._current.kind in terminators:
            return ()
        while self._current.kind not in terminators:
            items.append(self.parse_type_expression())
            if not self._match(TokenKind.COMMA):
                break
        return tuple(items)

    def _symbol(self, message: str) -> Symbol:
        """Parse symbol from the current token stream."""
        if (
            self._check(TokenKind.OP)
            and self._current.value in {"~", "&"}
            and self._peek(1).kind is TokenKind.IDENT
            and self._adjacent(self._current, self._peek(1))
        ):
            prefix = self._advance().value
            return Symbol(prefix + self._expect(TokenKind.IDENT).value)
        if self._match(TokenKind.IDENT, TokenKind.OP):
            return Symbol(self._previous.value)
        self._error(message)

    def _expect_tag_token(self) -> Token:
        """Parse expect tag token from the current token stream."""
        if self._check(TokenKind.OP) and self._current.value.startswith("#"):
            return self._advance()
        self._error("expected data tag")

    def _skip_newlines(self) -> None:
        """Consume consecutive newline tokens."""
        while self._match(TokenKind.NEWLINE):
            pass

    def _skip_separators(self) -> None:
        """Consume statement separators and blank lines."""
        while self._match(TokenKind.NEWLINE, TokenKind.PIPE):
            pass

    def _line_start_column(self, token: Token) -> int:
        """Return the first non-whitespace token column on ``token``'s line."""
        return min(
            candidate.column
            for candidate in self.tokens
            if candidate.line == token.line
            and candidate.kind not in {TokenKind.WHITESPACE, TokenKind.NEWLINE}
        )

    def _consume_optional_end(self, *, owner_column: int | None = None) -> None:
        """Consume an `end` owned by this block, respecting indentation."""
        if not self._check_ident("end"):
            return
        if owner_column is not None and self._current.column < owner_column:
            return
        self._advance()

    def _at_terminator(self, terminators: set[TokenKind | str]) -> bool:
        """Return the Boolean result of at terminator from the current parser token stream."""
        if self._check(TokenKind.EOF):
            return True
        if self._current.kind in terminators:
            return True
        return (
            self._current.kind is TokenKind.IDENT and self._current.value in terminators
        )

    def _match_ident(self, *values: str) -> bool:
        """Return the Boolean result of match ident from the current parser token stream."""
        if self._check_ident(*values):
            self._advance()
            return True
        return False

    def _expect_ident(self, value: str) -> Token:
        """Parse expect ident from the current token stream."""
        if self._match_ident(value):
            return self._previous
        self._error(f"expected {value}")

    def _match_ellipsis(self) -> bool:
        """Return the Boolean result of match ellipsis from the current parser token stream."""
        if not self._check(TokenKind.DOT):
            return False
        first, second, third = self._current, self._peek(1), self._peek(2)
        if (
            second.kind is TokenKind.DOT
            and self._adjacent(first, second)
            and third.kind is TokenKind.DOT
            and self._adjacent(second, third)
        ):
            self._advance()
            self._advance()
            self._advance()
            return True
        return False

    def _check_ident(self, *values: str) -> bool:
        """Return the Boolean result of check ident from the current parser token stream."""
        return self._check(TokenKind.IDENT) and self._current.value in values

    def _check_op(self, value: str) -> bool:
        """Return the Boolean result of check op from the current parser token stream."""
        return self._check(TokenKind.OP) and self._current.value == value

    def _adjacent(self, first: Token, second: Token) -> bool:
        """Whether `second` immediately follows `first` with no whitespace.

        This is the whitespace-significance check used anywhere a run of
        identical/related tokens must be contiguous to count as one unit
        (operator merging, `...`, `||`, and type-rank counting) rather than
        being separated (even by a single space) into distinct units.
        """
        first_width = len(first.raw if first.raw is not None else first.value)
        return second.offset == first.offset + first_width

    def _match(self, *kinds: TokenKind) -> bool:
        """Return the Boolean result of match from the current parser token stream."""
        if self._check(*kinds):
            self._advance()
            return True
        return False

    def _check(self, *kinds: TokenKind) -> bool:
        """Return the Boolean result of check from the current parser token stream."""
        return self._current.kind in kinds

    def _expect(self, kind: TokenKind) -> Token:
        """Consume the required token or raise a parse error."""
        if self._match(kind):
            return self._previous
        self._error(f"expected {kind.value}")

    def _advance(self) -> Token:
        """Consume and return the current token."""
        while (
            self.index < len(self.tokens) - 1
            and self.tokens[self.index].kind is TokenKind.WHITESPACE
        ):
            self.index += 1
        token = self.tokens[self.index]
        if token.kind is not TokenKind.EOF:
            self.index += 1
        return token

    def _peek(self, ahead: int = 0) -> Token:
        """Return a lookahead token without consuming input."""
        pos = self.index
        remaining = ahead
        while True:
            while (
                pos < len(self.tokens) - 1
                and self.tokens[pos].kind is TokenKind.WHITESPACE
            ):
                pos += 1
            if remaining == 0 or pos >= len(self.tokens) - 1:
                return self.tokens[min(pos, len(self.tokens) - 1)]
            pos += 1
            remaining -= 1

    @property
    def _current(self) -> Token:
        """Return the current exposed by this parser."""
        return self._peek()

    @property
    def _previous(self) -> Token:
        """Return the previous exposed by this parser."""
        return self.tokens[self.index - 1]

    def _error(self, message: str) -> None:
        """Raise a source-located parse error with the unexpected token."""
        token = self._current
        if token.kind is TokenKind.EOF:
            detail = "end of file"
        elif token.kind is TokenKind.NEWLINE:
            detail = "end of line"
        else:
            detail = repr(token.raw if token.raw is not None else token.value)
        if "found " not in message and "unexpected " not in message:
            message = f"{message}; found {detail}"
        raise ParseError(message, line=token.line, column=token.column)

    def _synchronize_statement(self, start: int) -> None:
        """Advance to a conservative top-level statement boundary."""
        # Recovery intentionally favours finding more independent errors over
        # fabricating nested AST structure.  A newline is the strongest general
        # boundary in Valiance; orphan closers are consumed to guarantee progress.
        while not self._check(TokenKind.EOF):
            if self._check(TokenKind.NEWLINE):
                self._advance()
                self._skip_newlines()
                return
            self._advance()
        if self.index <= start and not self._check(TokenKind.EOF):
            self._advance()


_LINE_TERMINATORS: set[TokenKind | str] = {
    TokenKind.NEWLINE,
    TokenKind.EOF,
    TokenKind.RPAREN,
    TokenKind.RBRACKET,
    TokenKind.RBRACE,
    "end",
    "else",
}


def _flatten(items: tuple[tuple[ASTNode, ...], ...]) -> tuple[ASTNode, ...]:
    """Parse flatten from the current token stream."""
    return tuple(node for item in items for node in item)


def _collection_postfix_superset(
    inner: type[CollectionType],
    outer: type[CollectionType],
) -> bool:
    """Return the Boolean result of collection postfix superset from the current parser token stream."""
    if inner is outer:
        return True
    return (inner, outer) in {
        (ListExactType, ListMinType),
        (ArrayExactType, ArrayMinType),
    }


def _optionalize_type(typ: Type, depth: int) -> Type:
    """Parse optionalize type from the current token stream."""
    for _ in range(depth):
        typ = U(N(Symbol("Some"), typ), NoneType())
    return typ


def _parser_param_type(param: FunctionParam, index: int) -> Type:
    """Parse parser param type from the current token stream."""
    if param.typ is not None:
        return param.typ
    name = param.name.text if param.name is not None else f"_{index}"
    return N(Symbol(name))


def _local_generic_type(typ: Type, generics: tuple[Symbol, ...]) -> Type:
    """Parse local generic type from the current token stream."""
    names = {generic.text for generic in generics}
    if isinstance(typ, NominalType):
        if not typ.args and typ.name.text in names:
            return V(typ.name.text)
        return N(typ.name, *(_local_generic_type(arg, generics) for arg in typ.args))
    if isinstance(typ, VarType):
        return typ
    if isinstance(typ, UnionType):
        return U(*(_local_generic_type(item, generics) for item in typ.items))
    if isinstance(typ, IntersectionType):
        return I(*(_local_generic_type(item, generics) for item in typ.items))
    if isinstance(typ, TupleType):
        return Tup(*(_local_generic_type(item, generics) for item in typ.params))
    if isinstance(typ, VariadicTupleType):
        return TupVariadic(
            *(
                TupleTypeItem(_local_generic_type(item.typ, generics), item.repeated)
                for item in typ.items
            )
        )
    if isinstance(typ, RowType):
        return Row(
            _local_generic_type(typ.base, generics),
            *(
                Field(field.name, _local_generic_type(field.typ, generics))
                for field in typ.fields
            ),
        )
    if isinstance(typ, CollectionType):
        return C(type(typ), _local_generic_type(typ.base, generics), typ.rank)
    if isinstance(typ, FunctionType):
        if typ.params is None or typ.returns is None:
            return typ
        return Fn(
            (_local_generic_type(param, generics) for param in typ.params),
            (_local_generic_type(ret, generics) for ret in typ.returns),
            typ.element_tags,
        )
    if isinstance(typ, TaggedType):
        return Tagged(
            _local_generic_type(typ.inner, generics),
            *typ.tags,
            exact=typ.exact,
        )
    if isinstance(typ, NoVecType):
        return NoVec(_local_generic_type(typ.inner, generics))
    if isinstance(typ, ExactType):
        return Exact(_local_generic_type(typ.inner, generics))
    return typ


def _append_object_body_item(
    item: ObjectFieldNode | DefineNode | TraitRequirementNode,
    fields: list[ObjectFieldNode],
    definitions: list[DefineNode],
    requirements: list[TraitRequirementNode],
) -> None:
    """Parse append object body item from the current token stream."""
    if isinstance(item, ObjectFieldNode):
        fields.append(item)
    elif isinstance(item, DefineNode):
        definitions.append(item)


    else:
        requirements.append(item)


def _loc(token: Token) -> SourceLocation:
    """Parse loc from the current token stream."""
    return SourceLocation(token.line, token.column, token.offset)


def _declared_tag_applications(
    typ: Type,
    token: Token,
) -> tuple[TagApplicationNode, ...]:
    """Lower declared top-level data tags into validating applications."""
    normalized = typ
    tags: list[DataTag] = []
    while isinstance(normalized, TaggedType):
        tags.extend(tag for tag in normalized.tags if not tag.absent)
        normalized = normalized.inner
    return tuple(
        TagApplicationNode(tag, location=_loc(token))
        for tag in sorted(tags, key=lambda item: (item.depth, item.name))
    )


def _tag_from_token(token: Token) -> DataTag:
    """Parse tag from token from the current token stream."""
    value = token.value
    if not value.startswith("#"):
        raise ParseError("expected data tag", line=token.line, column=token.column)
    raw = value[1:]
    absent = raw.startswith("-")
    if absent:
        raw = raw[1:]
    name, _, suffix = raw.partition("+")
    if not name:
        raise ParseError(
            "expected data tag name",
            line=token.line,
            column=token.column,
        )
    if not suffix and "+" not in raw:
        depth = 0
    elif suffix.isdecimal():
        depth = int(suffix)
    elif set(suffix) <= {"+"}:
        depth = len(suffix) + 1
    else:
        raise ParseError(
            "invalid data tag depth",
            line=token.line,
            column=token.column,
        )
    return DataTag(name, depth=depth, absent=absent)


def _lower_chain_segment(
    segment: list[_ChainPiece],
    *,
    reverse_elements: bool = True,
) -> tuple[ASTNode, ...]:
    """Parse lower chain segment from the current token stream."""
    if not segment:
        return ()

    if all(piece.is_element for piece in segment):
        pieces = reversed(segment) if reverse_elements else segment
        return tuple(node for piece in pieces for node in piece.nodes)

    if segment[-1].breaks_chain:
        right = segment[-1]
        left = segment[:-1]
        if left and all(piece.is_element for piece in left):
            lowered_left = tuple(
                node
                for piece in (reversed(left) if reverse_elements else left)
                for node in piece.nodes
            )
            if right.nodes and isinstance(
                right.nodes[0],
                (
                    AssertNode,
                    AtNode,
                    CastNode,
                    ForNode,
                    IfNode,
                    MatchNode,
                    TryNode,
                    UnfoldNode,
                    WhileNode,
                ),
            ):
                # Control-flow structures terminate the preceding chain but do
                # not become part of it. Lower the pending elements before the
                # structure so ``5 + if ...`` means ``5 +`` followed by ``if``.
                return (*lowered_left, *right.nodes)
            return (*right.nodes, *lowered_left)

    return tuple(node for piece in segment for node in piece.nodes)


def _string_parts(raw: str, token: Token) -> tuple[str | tuple[ASTNode, ...], ...]:
    """Parse string parts from the current token stream."""
    parts: list[str | tuple[ASTNode, ...]] = []
    literal: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\":
            if index + 1 >= len(raw):
                raise ParseError(
                    "unterminated string escape",
                    line=token.line,
                    column=token.column,
                )
            escaped = raw[index + 1]
            escape_values = {
                '"': '"',
                "\\": "\\",
                "$": "$",
                "n": "\n",
                "t": "\t",
            }
            if escaped not in escape_values:
                raise ParseError(
                    f"invalid string escape '\\{escaped}'",
                    line=token.line,
                    column=token.column,
                )
            literal.append(escape_values[escaped])
            index += 2
            continue
        if char != "$":
            literal.append(char)
            index += 1
            continue
        if index + 1 < len(raw) and raw[index + 1] == "{":
            end = _interpolation_end(raw, index + 2, token)
            expression = raw[index + 2 : end]
            if literal:
                parts.append("".join(literal))
                literal.clear()
            parsed = _interpolation_expression(expression, token)
            if not parsed:
                raise ParseError(
                    "empty string interpolation",
                    line=token.line,
                    column=token.column,
                )
            parts.append(tuple(parsed))
            index = end + 1
            continue
        if index + 1 < len(raw) and _is_string_ident_start(raw[index + 1]):
            start = index + 1
            end = start + 1
            while end < len(raw) and _is_string_ident_part(raw[end]):
                end += 1
            if literal:
                parts.append("".join(literal))
                literal.clear()
            parts.append(
                (GetVariableNode(Symbol(raw[start:end]), location=_loc(token)),)
            )
            index = end
            continue
        literal.append("$")
        index += 1
    if literal or not parts:
        parts.append("".join(literal))
    return tuple(parts)


def _interpolation_expression(
    expression: str,
    token: Token,
) -> tuple[ASTNode, ...]:
    """Parse interpolation expression from the current token stream."""
    return tuple(parse(expression))


def _contextual_empty_list(
    nodes: tuple[ASTNode, ...],
    typ: Type,
) -> tuple[ASTNode, ...]:
    """Parse contextual empty list from the current token stream."""
    if len(nodes) != 1:
        return nodes
    node = nodes[0]
    if isinstance(node, ListLiteralNode) and not node.items and node.typ is None:
        return (ListLiteralNode((), typ, location=node.location),)
    return nodes


def _interpolation_end(raw: str, start: int, token: Token) -> int:
    """Parse interpolation end from the current token stream."""
    depth = 1
    index = start
    while index < len(raw):
        char = raw[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            index = _skip_raw_string(raw, index + 1, token)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    raise ParseError(
        "unterminated string interpolation",
        line=token.line,
        column=token.column,
    )


def _skip_raw_string(raw: str, start: int, token: Token) -> int:
    """Parse skip raw string from the current token stream."""
    index = start
    while index < len(raw):
        if raw[index] == "\\":
            index += 2
            continue
        if raw[index] == '"':
            return index + 1
        index += 1
    raise ParseError(
        "unterminated nested string",
        line=token.line,
        column=token.column,
    )


def _is_string_ident_start(char: str) -> bool:
    """Return whether the value is string ident start."""
    return char == "_" or char.isalpha()


def _is_string_ident_part(char: str) -> bool:
    """Return whether the value is string ident part."""
    return char == "_" or char.isalpha() or char.isdigit()
