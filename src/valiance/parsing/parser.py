"""Recursive-descent parser for Valiance source."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from valiance.asts import (
    AnnotationNode,
    ArrayLiteralNode,
    ASTNode,
    BreakNode,
    DefineNode,
    DictLiteralNode,
    ElementNode,
    FieldAccessNode,
    ForNode,
    FunctionNode,
    FunctionParam,
    GetVariableNode,
    IfNode,
    ListLiteralNode,
    MatchCaseNode,
    MatchNode,
    NumberLiteralNode,
    ObjectNode,
    RecordLiteralNode,
    ReturnNode,
    SetVariableNode,
    SourceLocation,
    StringLiteralNode,
    Symbol,
    TupleLiteralNode,
    WhileNode,
)
from valiance.parsing.lexer import Token, TokenKind, lex
from valiance.types import (
    ArrayExactType,
    ArrayMinType,
    C,
    Fn,
    I,
    ListExactType,
    ListMinType,
    ListRuggedType,
    N,
    NoneType,
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
            nodes.extend(self._statement())
            self._skip_separators()
        return nodes

    def _statement(self) -> tuple[ASTNode, ...]:
        annotations = self._annotations()
        visibility: Symbol | None = None
        is_multi = False
        if self._match_ident("public", "private"):
            visibility = Symbol(self._previous.value)
        if self._match_ident("multi"):
            is_multi = True

        if self._match_ident("define"):
            return (self._define(self._previous, annotations, visibility, is_multi),)
        if self._match_ident("object", "trait", "variant"):
            return (self._object(self._previous, self._previous.value, annotations),)
        if self._match_ident("fn"):
            return (self._function(self._previous),)
        if self._match_ident("if"):
            return (self._if(self._previous),)
        if self._match_ident("while"):
            return (self._while(self._previous),)
        if self._match_ident("foreach"):
            return (self._foreach(self._previous),)
        if self._match_ident("break"):
            start = self._previous
            return (BreakNode(self._optional_values(), location=_loc(start)),)
        if self._match_ident("return"):
            start = self._previous
            return (ReturnNode(self._optional_values(), location=_loc(start)),)
        if self._match_ident("match"):
            return (self._match(self._previous),)

        if annotations:
            self._error("annotation must be followed by a declaration")
        return self._chain_until(_LINE_TERMINATORS)

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

    def _object(
        self, start: Token, kind: str, annotations: tuple[ASTNode, ...]
    ) -> ObjectNode:
        name = self._symbol("expected object name")
        self._expect(TokenKind.FAT_ARROW)
        return ObjectNode(
            Symbol(kind),
            name,
            self._body(),
            annotations,
            location=_loc(start),
        )

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
        if self._match_ident("else"):
            if self._match_ident("if"):
                else_branch = (self._if(self._previous),)
            else:
                self._expect(TokenKind.FAT_ARROW)
                else_branch = self._body({"end"})
        else:
            self._consume_optional_end()
        return IfNode(condition, then_branch, else_branch, location=_loc(start))

    def _while(self, start: Token) -> WhileNode:
        condition = self._condition()
        self._returns()
        self._expect(TokenKind.FAT_ARROW)
        return WhileNode(condition, self._body(), location=_loc(start))

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

    def _match(self, start: Token) -> MatchNode:
        self._expect(TokenKind.FAT_ARROW)
        cases: list[MatchCaseNode] = []
        self._skip_newlines()
        while not self._check_ident("end") and not self._check(TokenKind.EOF):
            case_start = self._current
            if self._match_ident("if"):
                pattern = (self._if(self._previous),)
                cases.append(MatchCaseNode(pattern, (), location=_loc(case_start)))
                self._skip_separators()
                continue
            pattern = self._chain_until({TokenKind.FAT_ARROW})
            self._expect(TokenKind.FAT_ARROW)
            cases.append(
                MatchCaseNode(
                    pattern,
                    self._body({"end", "_case"}),
                    location=_loc(case_start),
                )
            )
            self._skip_newlines()
        self._consume_optional_end()
        return MatchNode(tuple(cases), location=_loc(start))

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
                (StringLiteralNode(token.value, location=_loc(token)),),
                True,
            )
        if self._match(TokenKind.DOLLAR):
            return self._variable(self._previous)
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
        if self._match(TokenKind.LPAREN):
            token = self._previous
            items = self._comma_expressions(TokenKind.RPAREN)
            if len(items) == 1:
                return _ChainPiece(items[0], True)
            return _ChainPiece((TupleLiteralNode(items, location=_loc(token)),), True)
        if self._match_ident("fn"):
            return _ChainPiece((self._function(self._previous),), True)
        if self._match_ident("if"):
            return _ChainPiece((self._if(self._previous),), True)
        if self._match_ident("while"):
            return _ChainPiece((self._while(self._previous),), True)
        if self._match_ident("foreach"):
            return _ChainPiece((self._foreach(self._previous),), True)
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
            name = Symbol(token.value)
            if self._match(TokenKind.LPAREN):
                args = self._comma_expressions(TokenKind.RPAREN)
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

    def _variable(self, start: Token) -> _ChainPiece:
        if self._match(TokenKind.DOT):
            return _ChainPiece(
                (
                    FieldAccessNode(
                        self._symbol("expected field name"),
                        location=_loc(start),
                    ),
                ),
                is_element=True,
            )
        name = self._symbol("expected variable name")
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
            args = self._comma_expressions(TokenKind.RPAREN)
            return _ChainPiece(
                (*_flatten(args), GetVariableNode(name, location=_loc(start))),
                True,
            )
        return _ChainPiece((GetVariableNode(name, location=_loc(start)),), True)

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
                args = _flatten(self._comma_expressions(TokenKind.RPAREN))
            annotations.append(AnnotationNode(name, args, location=_loc(start)))
            self._skip_newlines()
        return tuple(annotations)

    def _params(self) -> tuple[FunctionParam, ...]:
        params: list[FunctionParam] = []
        self._skip_newlines()
        if self._match(TokenKind.RPAREN):
            return ()
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
        typ = self._type_postfix()
        while self._check_op("&"):
            self._advance()
            typ = I(typ, self._type_postfix())
        return typ

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


def _loc(token: Token) -> SourceLocation:
    return SourceLocation(token.line, token.column, token.offset)


def _lower_chain_segment(segment: list[_ChainPiece]) -> tuple[ASTNode, ...]:
    if not segment:
        return ()

    if segment[-1].breaks_chain:
        right = segment[-1]
        left = segment[:-1]
        if left and all(piece.is_element for piece in left):
            return (
                *right.nodes,
                *(node for piece in reversed(left) for node in piece.nodes),
            )

    return tuple(node for piece in segment for node in piece.nodes)
