"""Recursive-descent parser for Valiance source."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
    AssertNode,
    ASTNode,
    AtLevel,
    AtNode,
    BindingPatternNode,
    BreakNode,
    DefineNode,
    DictLiteralNode,
    ElementNode,
    EnumMemberNode,
    ExpressionPatternNode,
    FieldAccessNode,
    FieldSetNode,
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
    LiteralPatternNode,
    MatchCaseNode,
    MatchNode,
    MatchPatternNode,
    NumberLiteralNode,
    ObjectFieldNode,
    ObjectNode,
    OrPatternNode,
    RecordLiteralNode,
    RestPatternNode,
    ReturnNode,
    SetVariableNode,
    SourceLocation,
    StringInterpolationNode,
    StringLiteralNode,
    Symbol,
    TagApplicationNode,
    TraitRequirementNode,
    TupleLiteralNode,
    TypePatternNode,
    UnfoldNode,
    VariantMemberNode,
    WhileNode,
    WildcardPatternNode,
)
from valiance.parsing.lexer import Token, TokenKind, lex
from valiance.types import (
    ArrayExactType,
    ArrayMinType,
    C,
    DataTag,
    Fn,
    I,
    ListExactType,
    ListMinType,
    ListRuggedType,
    N,
    NoneType,
    Tagged,
    Tup,
    Type,
    U,
)


class ParseError(SyntaxError):
    """Raised when Valiance source cannot be parsed."""


@dataclass(frozen=True, slots=True)
class _ChainPiece:
    nodes: tuple[ASTNode, ...]
    breaks_chain: bool = False
    is_element: bool = False


def parse(source: str) -> list[ASTNode]:
    """Parse Valiance source into AST nodes."""
    return Parser(lex(source)).parse_program()


def parse_type(source: str) -> Type:
    """Parse one Valiance type expression."""
    parser = Parser(lex(source))
    typ = parser.parse_type_expression()
    parser._skip_newlines()
    parser._expect(TokenKind.EOF)
    return typ


class Parser:
    def __init__(self, tokens: Iterable[Token]) -> None:
        self.tokens = list(tokens)
        self.index = 0

    def parse_program(self) -> list[ASTNode]:
        nodes: list[ASTNode] = []
        self._skip_newlines()
        while not self._check(TokenKind.EOF):
            before = self.index
            statement = self._statement()
            if not statement and self.index == before:
                self._error("expected statement")
            nodes.extend(statement)
            self._skip_separators()
        return nodes

    def _statement(self) -> tuple[ASTNode, ...]:
        annotations = self._annotations()
        visibility: Symbol | None = None
        is_multi = False
        if self._match_ident("public", "private"):
            visibility = Symbol(self._previous.value)
        if self._match_ident("import"):
            return (
                self._import(
                    self._previous,
                    public=visibility == Symbol("public"),
                ),
            )
        if self._match_ident("multi"):
            is_multi = True

        if self._match_ident("define"):
            return (self._define(self._previous, annotations, visibility, is_multi),)
        if self._match_ident("object", "trait", "variant", "enum"):
            return (
                self._object_like(self._previous, self._previous.value, annotations),
            )
        if self._match_ident("fn"):
            return (self._function(self._previous),)
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
            return (ReturnNode(self._optional_values(), location=_loc(start)),)
        if self._match_ident("match"):
            return (self._match_node(self._previous),)

        if annotations:
            self._error("annotation must be followed by a declaration")
        return self._chain_until(_LINE_TERMINATORS)

    def _import(self, start: Token, *, public: bool = False) -> ImportNode:
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
        path = self._import_path()
        components: tuple[ImportComponent, ...] = ()
        if self._match(TokenKind.DOT) and self._match(TokenKind.LBRACKET):
            components = self._import_components()
        alias = None
        if self._match_ident("as"):
            alias = self._symbol("expected import alias")
        return ImportSpec(path, alias, components)

    def _import_path(self) -> ImportPath:
        root = None
        parts: list[str] = []
        if self._check(TokenKind.OP) and self._current.value == "~":
            self._advance()
            root = Symbol("~")
        elif self._match(TokenKind.AT):
            root = Symbol("@")
            parts.append(self._expect(TokenKind.IDENT).value)
        else:
            parts.append(self._expect(TokenKind.IDENT).value)
        while self._match(TokenKind.DOT):
            if self._check(TokenKind.LBRACKET):
                self.index -= 1
                break
            parts.append(self._expect(TokenKind.IDENT).value)
        return ImportPath(tuple(parts), root)

    def _import_components(self) -> tuple[ImportComponent, ...]:
        components: list[ImportComponent] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACKET):
            return ()
        while True:
            name = self._symbol("expected imported component")
            alias = None
            if self._match_ident("as"):
                alias = self._symbol("expected component alias")
            components.append(ImportComponent(name, alias))
            self._skip_newlines()
            if self._match(TokenKind.RBRACKET):
                return tuple(components)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _define(
        self,
        start: Token,
        annotations: tuple[ASTNode, ...],
        visibility: Symbol | None,
        is_multi: bool,
    ) -> DefineNode:
        name = self._symbol("expected definition name")
        params = self._params() if self._match(TokenKind.LPAREN) else None
        returns = self._returns()
        self._expect(TokenKind.FAT_ARROW)
        body = self._body()
        return DefineNode(
            name,
            FunctionNode(
                params=params,
                body=body,
                returns=returns,
                location=_loc(start),
            ),
            annotations,
            is_multi,
            visibility,
            location=_loc(start),
        )

    def _object_like(
        self, start: Token, kind: str, annotations: tuple[ASTNode, ...]
    ) -> ObjectNode:
        generics = self._generic_names()
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
            location=_loc(start),
        )

    def _generic_names(self) -> tuple[Symbol, ...]:
        if not self._match(TokenKind.LBRACKET):
            return ()
        names: list[Symbol] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACKET):
            return ()
        while True:
            names.append(self._symbol("expected generic parameter name"))
            if self._match(TokenKind.RBRACKET):
                return tuple(names)
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
        annotations = self._annotations()
        visibility: Symbol | None = None
        if self._match_ident("public", "private"):
            visibility = Symbol(self._previous.value)
        if self._match_ident("define"):
            return self._define(self._previous, annotations, visibility, False)
        if self._match_ident("extend"):
            return self._extend(self._previous)
        if annotations:
            self._error("annotation must be followed by a declaration")
        return self._field(owner, visibility)

    def _extend(self, start: Token) -> TraitRequirementNode:
        name = self._symbol("expected required element name")
        params = self._params() if self._match(TokenKind.LPAREN) else None
        returns = self._returns()
        return TraitRequirementNode(name, params, returns, location=_loc(start))

    def _field(self, owner: Symbol, visibility: Symbol | None) -> ObjectFieldNode:
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
        start = self._current
        name = self._symbol("expected variant member name")
        self._expect(TokenKind.FAT_ARROW)
        fields, definitions, requirements, variants = self._object_body("object", name)
        if requirements or variants:
            self._error("variant members may contain fields and definitions only")
        return VariantMemberNode(name, fields, definitions, location=_loc(start))

    def _enum_body(self) -> tuple[EnumMemberNode, ...]:
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

    def _function(self, start: Token) -> FunctionNode:
        params = self._params() if self._match(TokenKind.LPAREN) else None
        returns = self._returns()
        self._expect(TokenKind.FAT_ARROW)
        return FunctionNode(
            params=params,
            returns=returns,
            body=self._body(),
            location=_loc(start),
        )

    def _if(self, start: Token) -> IfNode:
        condition = self._condition()
        self._expect(TokenKind.FAT_ARROW)
        then_branch = self._body({"else", "end"})
        else_branch: tuple[ASTNode, ...] = ()
        self._skip_newlines()
        if self._match_ident("else"):
            if self._match_ident("if"):
                else_branch = (self._if(self._previous),)
            else:
                self._expect(TokenKind.FAT_ARROW)
                else_branch = self._body({"end"})
                self._skip_newlines()
                self._consume_optional_end()
        else:
            self._consume_optional_end()
        return IfNode(condition, then_branch, else_branch, location=_loc(start))

    def _while(self, start: Token) -> WhileNode:
        condition = self._condition()
        params = self._control_params()
        self._expect(TokenKind.FAT_ARROW)
        return WhileNode(condition, params, self._body(), location=_loc(start))

    def _assert(self, start: Token) -> AssertNode:
        self._expect(TokenKind.FAT_ARROW)
        condition = self._body({"else", "end"})
        else_branch: tuple[ASTNode, ...] = ()
        self._skip_newlines()
        if self._match_ident("else"):
            self._expect(TokenKind.FAT_ARROW)
            else_branch = self._body({"end"})
            self._skip_newlines()
            self._consume_optional_end()
        else:
            self._consume_optional_end()
        return AssertNode(condition, else_branch, location=_loc(start))

    def _unfold(self, start: Token) -> UnfoldNode:
        condition: tuple[ASTNode, ...] = ()
        if self._check(TokenKind.LPAREN):
            condition = self._condition()
        params = self._control_params()
        self._expect(TokenKind.FAT_ARROW)
        return UnfoldNode(condition, params, self._body(), location=_loc(start))

    def _at(self, start: Token) -> AtNode:
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
        if not self._match(TokenKind.ARROW):
            return None
        self._expect(TokenKind.LPAREN)
        return self._params()

    def _foreach(self, start: Token) -> ForNode:
        self._expect(TokenKind.LPAREN)
        variable = self._symbol("expected foreach variable")
        index_variable = None
        if self._match(TokenKind.COMMA):
            index_variable = self._symbol("expected foreach index variable")
        self._expect(TokenKind.RPAREN)
        self._returns()
        self._expect(TokenKind.FAT_ARROW)
        return ForNode(variable, index_variable, self._body(), location=_loc(start))

    def _match_node(self, start: Token) -> MatchNode:
        self._expect(TokenKind.FAT_ARROW)
        cases: list[MatchCaseNode] = []
        self._skip_newlines()
        while not self._check_ident("end") and not self._check(TokenKind.EOF):
            case_start = self._current
            if self._match_ident("default"):
                self._expect(TokenKind.FAT_ARROW)
                cases.append(
                    MatchCaseNode(
                        (WildcardPatternNode(location=_loc(case_start)),),
                        (),
                        None,
                        True,
                        self._match_body(),
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
                    self._match_body(),
                    location=_loc(case_start),
                )
            )
            self._skip_newlines()
        self._consume_optional_end()
        return MatchNode(tuple(cases), location=_loc(start))

    def _match_case_patterns(self) -> tuple[MatchPatternNode, ...]:
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
        options = [self._match_pattern_atom(terminators | {TokenKind.PIPE})]
        while self._check(TokenKind.PIPE) and self._peek(1).kind is TokenKind.PIPE:
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
                fields.append(
                    self._match_pattern({TokenKind.COMMA, TokenKind.RPAREN})
                )
            if self._match(TokenKind.RPAREN):
                return tuple(fields)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _list_match_patterns(self) -> tuple[MatchPatternNode, ...]:
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

    def _match_body(self) -> tuple[ASTNode, ...]:
        single_line = not self._check(TokenKind.NEWLINE)
        if single_line:
            body = self._chain_until(_LINE_TERMINATORS)
            self._consume_optional_end()
            return body
        self._skip_newlines()
        nodes: list[ASTNode] = []
        while not self._check(TokenKind.EOF):
            if self._check_ident("end") or self._at_match_case_start():
                break
            nodes.extend(self._statement())
            self._skip_separators()
        return tuple(nodes)

    def _at_match_case_start(self) -> bool:
        if self._check_ident("as", "default", "if", "_"):
            return True
        return self._check(TokenKind.NUMBER, TokenKind.STRING, TokenKind.LBRACKET)

    def _body(self, stop_words: set[str] | None = None) -> tuple[ASTNode, ...]:
        single_line = not self._check(TokenKind.NEWLINE)
        if single_line:
            body = self._chain_until(_LINE_TERMINATORS)
            self._consume_optional_end()
            return body
        self._skip_newlines()
        nodes: list[ASTNode] = []
        stop_words = {"end"} if stop_words is None else stop_words
        while not self._check(TokenKind.EOF):
            if self._check_ident(*stop_words):
                break
            nodes.extend(self._statement())
            self._skip_separators()
        self._consume_optional_end()
        return tuple(nodes)

    def _condition(self) -> tuple[ASTNode, ...]:
        if self._match(TokenKind.LPAREN):
            condition = self._chain_until({TokenKind.RPAREN})
            self._expect(TokenKind.RPAREN)
            return condition
        return self._chain_until({TokenKind.FAT_ARROW})

    def _optional_values(self) -> tuple[ASTNode, ...]:
        if self._check(TokenKind.NEWLINE, TokenKind.EOF) or self._check_ident(
            "end", "else"
        ):
            return ()
        if self._match(TokenKind.LPAREN):
            return _flatten(self._comma_expressions(TokenKind.RPAREN))
        return self._chain_until(_LINE_TERMINATORS)

    def _chain_until(self, terminators: set[TokenKind | str]) -> tuple[ASTNode, ...]:
        nodes: list[ASTNode] = []
        segment: list[_ChainPiece] = []
        self._skip_newlines()
        while not self._at_terminator(terminators):
            if self._match(TokenKind.PIPE):
                nodes.extend(_lower_chain_segment(segment))
                segment.clear()
                continue
            piece = self._term()
            segment.append(piece)
            if piece.breaks_chain:
                nodes.extend(_lower_chain_segment(segment))
                segment.clear()
        nodes.extend(_lower_chain_segment(segment))
        return tuple(nodes)

    def _term(self) -> _ChainPiece:
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
                is_element=True,
            )
        if self._match(TokenKind.LBRACKET):
            token = self._previous
            return _ChainPiece(
                (
                    ListLiteralNode(
                        self._comma_expressions(TokenKind.RBRACKET),
                        location=_loc(token),
                    ),
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
        if self._match_ident("record") and self._match(TokenKind.LBRACE):
            token = self._previous
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
        if self._match_ident("break"):
            token = self._previous
            return _ChainPiece(
                (BreakNode(self._optional_values(), location=_loc(token)),),
                True,
            )
        if self._match_ident("return"):
            token = self._previous
            return _ChainPiece(
                (ReturnNode(self._optional_values(), location=_loc(token)),),
                True,
            )
        if self._match(TokenKind.IDENT, TokenKind.OP):
            token = self._previous
            if token.value.startswith("#"):
                return _ChainPiece(
                    (TagApplicationNode(_tag_from_token(token), location=_loc(token)),),
                    is_element=True,
                )
            name = self._qualified_symbol(token)
            if self._match(TokenKind.COLON):
                return _ChainPiece(
                    (
                        ElementNode(
                            name,
                            self._modifier_arguments(token),
                            location=_loc(token),
                        ),
                    ),
                    True,
                )
            if self._match(TokenKind.LPAREN):
                args = self._argument_expressions(TokenKind.RPAREN)
                return _ChainPiece(
                    (*_flatten(args), ElementNode(name, location=_loc(token))),
                    True,
                )
            return _ChainPiece(
                (ElementNode(name, location=_loc(token)),),
                breaks_chain=name.text.startswith("\\"),
                is_element=True,
            )
        self._error("expected expression")

    def _string_node(self, token: Token) -> ASTNode:
        raw = token.raw if token.raw is not None else token.value
        parts = _string_parts(raw, token)
        if len(parts) == 1 and isinstance(parts[0], str):
            return StringLiteralNode(parts[0], location=_loc(token))
        return StringInterpolationNode(parts, location=_loc(token))

    def _qualified_symbol(self, start: Token) -> Symbol:
        parts = [start.value]
        while self._check(TokenKind.DOT) and self._peek(1).kind == TokenKind.IDENT:
            self._advance()
            parts.append(self._advance().value)
        name = ".".join(parts)
        if self._match(TokenKind.DOUBLE_COLON):
            name = f"{name}::{self._expect(TokenKind.IDENT).value}"
        return Symbol(name)

    def _variable(self, start: Token) -> _ChainPiece:
        if self._match(TokenKind.LBRACKET):
            selectors = self._index_selectors()
            return _ChainPiece(
                (
                    *self._selector_expressions(selectors),
                    IndexAccessNode(selectors, location=_loc(start)),
                ),
                True,
            )
        if self._match(TokenKind.DOT):
            field = self._symbol("expected field name")
            if self._match(TokenKind.ASSIGN, TokenKind.AUG_ASSIGN):
                op = self._previous.kind
                rhs = self._chain_until(_LINE_TERMINATORS)
                prefix = (
                    (FieldAccessNode(field, location=_loc(start)),)
                    if op is TokenKind.AUG_ASSIGN
                    else ()
                )
                return _ChainPiece(
                    (*prefix, *rhs, FieldSetNode(field, location=_loc(start))),
                    True,
                )
            return _ChainPiece(
                (
                    FieldAccessNode(
                        field,
                        location=_loc(start),
                    ),
                ),
                is_element=True,
            )
        name = self._symbol("expected variable name")
        if self._match(TokenKind.LBRACKET):
            selectors = self._index_selectors()
            if self._match(TokenKind.AUG_ASSIGN):
                rhs = self._chain_until(_LINE_TERMINATORS)
                receiver = (GetVariableNode(name, location=_loc(start)),)
                index_values = self._selector_expressions(selectors)
                return _ChainPiece(
                    (
                        *receiver,
                        *index_values,
                        IndexAccessNode(selectors, location=_loc(start)),
                        *rhs,
                        *receiver,
                        *index_values,
                        IndexSetNode(selectors, location=_loc(start)),
                        SetVariableNode(name, location=_loc(start)),
                    ),
                    True,
                )
            return _ChainPiece(
                (
                    GetVariableNode(name, location=_loc(start)),
                    *self._selector_expressions(selectors),
                    IndexAccessNode(selectors, location=_loc(start)),
                ),
                True,
            )
        if self._match(TokenKind.DOT):
            field = self._symbol("expected field name")
            if self._match(TokenKind.ASSIGN, TokenKind.AUG_ASSIGN):
                op = self._previous.kind
                rhs = self._chain_until(_LINE_TERMINATORS)
                receiver = (GetVariableNode(name, location=_loc(start)),)
                prefix = (
                    (*receiver, FieldAccessNode(field, location=_loc(start)))
                    if op is TokenKind.AUG_ASSIGN
                    else receiver
                )
                return _ChainPiece(
                    (*prefix, *rhs, FieldSetNode(field, location=_loc(start))),
                    True,
                )
            return _ChainPiece(
                (
                    GetVariableNode(name, location=_loc(start)),
                    FieldAccessNode(field, location=_loc(start)),
                ),
                True,
            )
        if self._match(TokenKind.ASSIGN, TokenKind.AUG_ASSIGN):
            op = self._previous.kind
            rhs = self._chain_until(_LINE_TERMINATORS)
            prefix = (
                (GetVariableNode(name, location=_loc(start)),)
                if op is TokenKind.AUG_ASSIGN
                else ()
            )
            return _ChainPiece(
                (*prefix, *rhs, SetVariableNode(name, location=_loc(start))),
                True,
            )
        if self._match(TokenKind.LPAREN):
            args = self._argument_expressions(TokenKind.RPAREN)
            return _ChainPiece(
                (*_flatten(args), GetVariableNode(name, location=_loc(start))),
                True,
            )
        return _ChainPiece((GetVariableNode(name, location=_loc(start)),), True)

    def _index_selectors(self) -> tuple[IndexSelector, ...]:
        selectors: list[IndexSelector] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACKET):
            self._error("empty indexing expressions are invalid")
        while True:
            start: tuple[ASTNode, ...] = ()
            stop: tuple[ASTNode, ...] = ()
            step: tuple[ASTNode, ...] = ()
            is_slice = False
            if not self._check(TokenKind.COLON):
                start = self._chain_until(
                    {TokenKind.COMMA, TokenKind.COLON, TokenKind.RBRACKET}
                )
            if self._match(TokenKind.COLON):
                is_slice = True
                if not self._check(
                    TokenKind.COLON,
                    TokenKind.COMMA,
                    TokenKind.RBRACKET,
                ):
                    stop = self._chain_until(
                        {TokenKind.COMMA, TokenKind.COLON, TokenKind.RBRACKET}
                    )
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
        nodes: list[ASTNode] = []
        for selector in selectors:
            nodes.extend(selector.start)
            nodes.extend(selector.stop)
            nodes.extend(selector.step)
        return tuple(nodes)

    def _comma_expressions(self, closer: TokenKind) -> tuple[tuple[ASTNode, ...], ...]:
        items: list[tuple[ASTNode, ...]] = []
        self._skip_newlines()
        if self._match(closer):
            return ()
        while True:
            items.append(self._chain_until({TokenKind.COMMA, closer}))
            if self._match(closer):
                return tuple(items)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _argument_expressions(
        self, closer: TokenKind
    ) -> tuple[tuple[ASTNode, ...], ...]:
        self._skip_newlines()
        if self._check(closer):
            self._error("empty argument lists are invalid; use a \\nilad name")
        return self._comma_expressions(closer)

    def _modifier_arguments(self, start: Token) -> tuple[FunctionNode, ...]:
        if self._match(TokenKind.LPAREN):
            return tuple(
                FunctionNode(body=body, location=_loc(start))
                for body in self._argument_expressions(TokenKind.RPAREN)
            )

        body = self._chain_until(_LINE_TERMINATORS)
        if not body:
            self._error("expected modifier function body")
        return (FunctionNode(body=body, location=_loc(start)),)

    def _record_fields(self) -> tuple[tuple[Symbol, tuple[ASTNode, ...]], ...]:
        fields: list[tuple[Symbol, tuple[ASTNode, ...]]] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACE):
            return ()
        while True:
            name = self._symbol("expected record field")
            self._expect(TokenKind.COLON)
            fields.append(
                (name, self._chain_until({TokenKind.COMMA, TokenKind.RBRACE}))
            )
            if self._match(TokenKind.RBRACE):
                return tuple(fields)
            self._expect(TokenKind.COMMA)

    def _dict_entries(
        self,
    ) -> tuple[tuple[tuple[ASTNode, ...], tuple[ASTNode, ...]], ...]:
        entries: list[tuple[tuple[ASTNode, ...], tuple[ASTNode, ...]]] = []
        self._skip_newlines()
        if self._match(TokenKind.RBRACE):
            return ()
        while True:
            key = self._chain_until({TokenKind.COLON})
            self._expect(TokenKind.COLON)
            value = self._chain_until({TokenKind.COMMA, TokenKind.RBRACE})
            entries.append((key, value))
            if self._match(TokenKind.RBRACE):
                return tuple(entries)
            self._expect(TokenKind.COMMA)

    def _annotations(self) -> tuple[ASTNode, ...]:
        annotations: list[ASTNode] = []
        while self._match(TokenKind.AT):
            start = self._previous
            name = self._symbol("expected annotation name")
            args: tuple[ASTNode, ...] = ()
            if self._match(TokenKind.LPAREN):
                args = _flatten(self._argument_expressions(TokenKind.RPAREN))
            annotations.append(AnnotationNode(name, args, location=_loc(start)))
            self._skip_newlines()
        return tuple(annotations)

    def _params(self) -> tuple[FunctionParam, ...]:
        params: list[FunctionParam] = []
        self._skip_newlines()
        if self._check(TokenKind.RPAREN):
            self._error("empty parameter lists are invalid; use a \\nilad name")
        while True:
            name: Symbol | None = None
            typ: Type | None = None
            if self._match(TokenKind.COLON):
                typ = self.parse_type_expression()
            elif self._check(TokenKind.IDENT) and self._peek(1).kind == TokenKind.COLON:
                name = Symbol(self._advance().value)
                self._expect(TokenKind.COLON)
                typ = self.parse_type_expression()
            else:
                name = self._symbol("expected parameter")
            params.append(FunctionParam(name, typ))
            if self._match(TokenKind.RPAREN):
                return tuple(params)
            self._expect(TokenKind.COMMA)
            self._skip_newlines()

    def _returns(self) -> tuple[Type, ...] | None:
        if not self._match(TokenKind.ARROW):
            return None
        if self._check(TokenKind.FAT_ARROW):
            return ()
        returns: list[Type] = []
        while not self._check(TokenKind.FAT_ARROW, TokenKind.NEWLINE, TokenKind.EOF):
            returns.append(self.parse_type_expression())
            if not self._match(TokenKind.COMMA):
                break
        return tuple(returns)

    def parse_type_expression(self) -> Type:
        return self._type_union()

    def _type_union(self) -> Type:
        typ = self._type_intersection()
        while self._match(TokenKind.PIPE):
            typ = U(typ, self._type_intersection())
        return typ

    def _type_intersection(self) -> Type:
        typ = self._type_tagged()
        while self._check_op("&"):
            self._advance()
            typ = I(typ, self._type_tagged())
        return typ

    def _type_tagged(self) -> Type:
        tags: list[DataTag] = []
        while self._check(TokenKind.OP) and self._current.value.startswith("#"):
            tags.append(_tag_from_token(self._advance()))
        typ = self._type_postfix()
        return Tagged(typ, *tags) if tags else typ

    def _type_postfix(self) -> Type:
        typ = self._type_primary()
        while self._check(TokenKind.OP) and self._current.value in {
            "+",
            "*",
            "~",
            "^",
            ">",
            "?",
        }:
            op = self._advance().value
            if op == "?":
                typ = U(N(Symbol("Some"), typ), NoneType())
                continue
            rank = 1
            if self._match(TokenKind.NUMBER):
                rank = int(self._previous.value)
            collection = {
                "+": ListExactType,
                "*": ListMinType,
                "~": ListRuggedType,
                "^": ArrayExactType,
                ">": ArrayMinType,
            }.get(op)
            if collection is not None:
                typ = C(collection, typ, rank)
        return typ

    def _type_primary(self) -> Type:
        if self._match(TokenKind.LBRACE):
            items: list[Type] = []
            if self._match(TokenKind.RBRACE):
                return N(Symbol("{}"))
            while True:
                items.append(self.parse_type_expression())
                if self._match(TokenKind.RBRACE):
                    return Tup(*items)
                self._expect(TokenKind.COMMA)
        if self._match(TokenKind.IDENT):
            name = self._previous.value
            while self._match(TokenKind.DOT):
                name = f"{name}.{self._expect(TokenKind.IDENT).value}"
            if name == "None":
                return NoneType()
            args: list[Type] = []
            if self._match(TokenKind.LBRACKET):
                if name == "Function":
                    params = self._type_list_until({TokenKind.ARROW})
                    self._expect(TokenKind.ARROW)
                    returns = self._type_list_until({TokenKind.RBRACKET})
                    self._expect(TokenKind.RBRACKET)
                    return Fn(params, returns)
                if not self._match(TokenKind.RBRACKET):
                    while True:
                        args.append(self.parse_type_expression())
                        if self._match(TokenKind.RBRACKET):
                            break
                        self._expect(TokenKind.COMMA)
            if name == "Function" and args:
                return args[0]
            return N(Symbol(name), *args)
        if self._match(TokenKind.LPAREN):
            params: list[Type] = []
            if not self._check(TokenKind.ARROW):
                while True:
                    params.append(self.parse_type_expression())
                    if not self._match(TokenKind.COMMA):
                        break
            self._expect(TokenKind.ARROW)
            returns: list[Type] = []
            if not self._check(TokenKind.RPAREN):
                while True:
                    returns.append(self.parse_type_expression())
                    if not self._match(TokenKind.COMMA):
                        break
            self._expect(TokenKind.RPAREN)
            return Fn(params, returns)
        self._error("expected type")

    def _type_list_until(self, terminators: set[TokenKind]) -> tuple[Type, ...]:
        items: list[Type] = []
        if self._current.kind in terminators:
            return ()
        while self._current.kind not in terminators:
            items.append(self.parse_type_expression())
            if not self._match(TokenKind.COMMA):
                break
        return tuple(items)

    def _symbol(self, message: str) -> Symbol:
        if self._match(TokenKind.IDENT, TokenKind.OP):
            return Symbol(self._previous.value)
        self._error(message)

    def _skip_newlines(self) -> None:
        while self._match(TokenKind.NEWLINE):
            pass

    def _skip_separators(self) -> None:
        while self._match(TokenKind.NEWLINE, TokenKind.PIPE):
            pass

    def _consume_optional_end(self) -> None:
        if self._match_ident("end"):
            return

    def _at_terminator(self, terminators: set[TokenKind | str]) -> bool:
        if self._check(TokenKind.EOF):
            return True
        if self._current.kind in terminators:
            return True
        return (
            self._current.kind is TokenKind.IDENT
            and self._current.value in terminators
        )

    def _match_ident(self, *values: str) -> bool:
        if self._check_ident(*values):
            self._advance()
            return True
        return False

    def _match_ellipsis(self) -> bool:
        if (
            self._check(TokenKind.DOT)
            and self._peek(1).kind is TokenKind.DOT
            and self._peek(2).kind is TokenKind.DOT
        ):
            self._advance()
            self._advance()
            self._advance()
            return True
        return False

    def _check_ident(self, *values: str) -> bool:
        return self._check(TokenKind.IDENT) and self._current.value in values

    def _check_op(self, value: str) -> bool:
        return self._check(TokenKind.OP) and self._current.value == value

    def _match(self, *kinds: TokenKind) -> bool:
        if self._check(*kinds):
            self._advance()
            return True
        return False

    def _check(self, *kinds: TokenKind) -> bool:
        return self._current.kind in kinds

    def _expect(self, kind: TokenKind) -> Token:
        if self._match(kind):
            return self._previous
        self._error(f"expected {kind.value}")

    def _advance(self) -> Token:
        token = self._current
        if not self._check(TokenKind.EOF):
            self.index += 1
        return token

    def _peek(self, ahead: int = 0) -> Token:
        pos = min(self.index + ahead, len(self.tokens) - 1)
        return self.tokens[pos]

    @property
    def _current(self) -> Token:
        return self._peek()

    @property
    def _previous(self) -> Token:
        return self.tokens[self.index - 1]

    def _error(self, message: str) -> None:
        token = self._current
        raise ParseError(f"{message} at {token.line}:{token.column}")


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
    return tuple(node for item in items for node in item)


def _append_object_body_item(
    item: ObjectFieldNode | DefineNode | TraitRequirementNode,
    fields: list[ObjectFieldNode],
    definitions: list[DefineNode],
    requirements: list[TraitRequirementNode],
) -> None:
    if isinstance(item, ObjectFieldNode):
        fields.append(item)
    elif isinstance(item, DefineNode):
        definitions.append(item)
    else:
        requirements.append(item)


def _loc(token: Token) -> SourceLocation:
    return SourceLocation(token.line, token.column, token.offset)


def _tag_from_token(token: Token) -> DataTag:
    value = token.value
    if not value.startswith("#"):
        raise ParseError(f"expected data tag at {token.line}:{token.column}")
    raw = value[1:]
    absent = raw.startswith("!")
    if absent:
        raw = raw[1:]
    name, _, suffix = raw.partition("+")
    if not name:
        raise ParseError(f"expected data tag name at {token.line}:{token.column}")
    if not suffix and "+" not in raw:
        depth = 0
    elif suffix.isdecimal():
        depth = int(suffix)
    elif set(suffix) <= {"+"}:
        depth = len(suffix) + 1
    else:
        raise ParseError(f"invalid data tag depth at {token.line}:{token.column}")
    return DataTag(name, depth=depth, absent=absent)


def _lower_chain_segment(segment: list[_ChainPiece]) -> tuple[ASTNode, ...]:
    if not segment:
        return ()

    if all(piece.is_element for piece in segment):
        return tuple(node for piece in reversed(segment) for node in piece.nodes)

    if segment[-1].breaks_chain:
        right = segment[-1]
        left = segment[:-1]
        if left and all(piece.is_element for piece in left):
            return (
                *right.nodes,
                *(node for piece in reversed(left) for node in piece.nodes),
            )

    return tuple(node for piece in segment for node in piece.nodes)


def _string_parts(raw: str, token: Token) -> tuple[str | tuple[ASTNode, ...], ...]:
    parts: list[str | tuple[ASTNode, ...]] = []
    literal: list[str] = []
    index = 0
    while index < len(raw):
        char = raw[index]
        if char == "\\":
            if index + 1 >= len(raw):
                literal.append("\\")
                index += 1
                continue
            escaped = raw[index + 1]
            if escaped in {'"', "\\", "$"}:
                literal.append(escaped)
            else:
                literal.append("\\" + escaped)
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
                    f"empty string interpolation at {token.line}:{token.column}"
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
    stripped = expression.strip()
    if stripped and _is_string_ident_start(stripped[0]) and all(
        _is_string_ident_part(char) for char in stripped[1:]
    ):
        return (GetVariableNode(Symbol(stripped), location=_loc(token)),)
    return tuple(parse(expression))


def _interpolation_end(raw: str, start: int, token: Token) -> int:
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
        f"unterminated string interpolation at {token.line}:{token.column}"
    )


def _skip_raw_string(raw: str, start: int, token: Token) -> int:
    index = start
    while index < len(raw):
        if raw[index] == "\\":
            index += 2
            continue
        if raw[index] == '"':
            return index + 1
        index += 1
    raise ParseError(f"unterminated nested string at {token.line}:{token.column}")


def _is_string_ident_start(char: str) -> bool:
    return char == "_" or char.isalpha()


def _is_string_ident_part(char: str) -> bool:
    return char == "_" or char.isalpha() or char.isdigit()
