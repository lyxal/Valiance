"""Lexical analysis for Valiance source."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import unicodedata

from valiance.analysis.diagnostics import DiagnosticError
from valiance.parsing.unicode_identifiers import (
    forbidden_identifier_character,
    is_xid_continue,
    is_xid_start,
    normalize_identifier,
)


class TokenKind(StrEnum):
    IDENT = "IDENT"
    NUMBER = "NUMBER"
    STRING = "STRING"
    NEWLINE = "NEWLINE"
    WHITESPACE = "WHITESPACE"
    EOF = "EOF"
    ARROW = "->"
    FAT_ARROW = "=>"
    ASSIGN = "="
    AUG_ASSIGN = ":="
    DOUBLE_COLON = "::"
    LPAREN = "("
    RPAREN = ")"
    LBRACKET = "["
    RBRACKET = "]"
    LBRACE = "{"
    RBRACE = "}"
    COMMA = ","
    COLON = ":"
    DOT = "."
    PIPE = "|"
    AT = "@"
    DOLLAR = "$"
    OP = "OP"
    ERROR = "ERROR"


@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    value: str
    line: int
    column: int
    offset: int
    raw: str | None = None


class LexError(DiagnosticError, SyntaxError):
    """Raised when Valiance source cannot be tokenized."""


_SINGLE = {
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
    "[": TokenKind.LBRACKET,
    "]": TokenKind.RBRACKET,
    "{": TokenKind.LBRACE,
    "}": TokenKind.RBRACE,
    ",": TokenKind.COMMA,
    ":": TokenKind.COLON,
    ".": TokenKind.DOT,
    "|": TokenKind.PIPE,
    "@": TokenKind.AT,
    "$": TokenKind.DOLLAR,
}

_OP_CHARS = set("+-*%!?=/<>~&^")


def lex(source: str) -> list[Token]:
    """Return Valiance tokens, raising the first lexical diagnostic.

    Use :func:`lex_with_diagnostics` for editor/compiler-front-end recovery.
    """
    tokens, diagnostics = lex_with_diagnostics(source)
    if diagnostics:
        raise diagnostics[0]
    return tokens


def lex_with_diagnostics(source: str) -> tuple[list[Token], tuple[LexError, ...]]:
    """Tokenize *source* and collect recoverable lexical diagnostics.

    Invalid characters become ``ERROR`` tokens.  Scanning then resumes at the
    next character or natural token boundary so callers can report independent
    errors from the rest of the file.
    """
    lexer = _Lexer(source, recover=True)
    return lexer.lex(), tuple(lexer.diagnostics)


class _Lexer:
    def __init__(self, source: str, *, recover: bool = False) -> None:
        """Initialize this lexer."""
        self.source = source
        self.length = len(source)
        self.index = 0
        self.line = 1
        self.column = 1
        self.tokens: list[Token] = []
        self.recover = recover
        self.diagnostics: list[LexError] = []

    def lex(self) -> list[Token]:
        """Tokenize the complete source stream and append the EOF token."""
        while not self._at_end:
            char = self._peek()
            if char in " \t\r":
                self._emit(TokenKind.WHITESPACE, self._advance())
            elif char == "\n":
                line, col, offset = self.line, self.column, self.index
                self._emit(
                    TokenKind.NEWLINE,
                    self._advance(),
                    line=line,
                    col=col,
                    offset=offset,
                )
            elif char == "#":
                self._comment_or_tag()
            elif char == '"':
                self._string()
            elif self._starts_number():
                self._number()
            elif self._is_ident_start(char):
                self._ident()
            elif char == "-" and self._peek(1) == ">":
                self._emit(TokenKind.ARROW, self._advance(2))
            elif char == "=" and self._peek(1) == ">":
                self._emit(TokenKind.FAT_ARROW, self._advance(2))
            elif char == "=" and self._peek(1) in _OP_CHARS:
                self._operator()
            elif char == ":" and self._peek(1) == "=":
                self._emit(TokenKind.AUG_ASSIGN, self._advance(2))
            elif char == ":" and self._peek(1) == ":":
                self._emit(TokenKind.DOUBLE_COLON, self._advance(2))
            elif char == "=":
                self._emit(TokenKind.ASSIGN, self._advance())
            elif char in _SINGLE:
                self._emit(_SINGLE[char], self._advance())
            elif char in _OP_CHARS or char == "\\":
                self._operator()
            else:
                if forbidden_identifier_character(char):
                    name = unicodedata.name(char, "unnamed character")
                    self._fail(
                        f"forbidden character U+{ord(char):04X} {name} in source"
                    )
                self._fail(f"unexpected character {char!r}")
                if self.recover and not self._at_end:
                    self._advance()

        self.tokens.append(Token(TokenKind.EOF, "", self.line, self.column, self.index))
        return self.tokens

    @property
    def _at_end(self) -> bool:
        """Return the at end exposed by this lexer."""
        return self.index >= self.length

    def _peek(self, ahead: int = 0) -> str:
        """Return a lookahead character without consuming it."""
        pos = self.index + ahead
        if pos >= self.length:
            return ""
        return self.source[pos]

    def _advance(self, count: int = 1) -> str:
        """Consume and return the next source character."""
        start = self.index
        for _ in range(count):
            char = self.source[self.index]
            self.index += 1
            if char == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1
        return self.source[start : self.index]

    def _emit(
        self,
        kind: TokenKind,
        value: str,
        line: int | None = None,
        col: int | None = None,
        offset: int | None = None,
    ) -> None:
        """Scan emit while tokenizing Valiance source."""
        self.tokens.append(
            Token(
                kind,
                value,
                self.line if line is None else line,
                self.column - len(value) if col is None else col,
                self.index - len(value) if offset is None else offset,
            )
        )

    def _comment_or_tag(self) -> None:
        """Scan comment or tag while tokenizing Valiance source."""
        if self._peek(1) == "?":
            while not self._at_end and self._peek() != "\n":
                self._advance()
            return
        if self._peek(1) == "/":
            self._advance(2)
            depth = 1
            while not self._at_end and depth:
                if self._peek() == "#" and self._peek(1) == "/":
                    depth += 1
                    self._advance(2)
                elif self._peek() == "/" and self._peek(1) == "#":
                    depth -= 1
                    self._advance(2)
                else:
                    self._advance()
            if depth:
                self._fail("unterminated multiline comment; expected closing /#")
            return
        self._tag()

    def _tag(self) -> None:
        """Scan tag while tokenizing Valiance source."""
        line, col, offset = self.line, self.column, self.index
        self._advance()
        if self._peek() == "-":
            self._advance()
        if not self._is_ident_start(self._peek()):
            self._fail("expected tag name after '#'; for example #sorted or #-cached", line, col)
            if self.recover:
                return
        self._advance()
        while self._is_ident_part(self._peek()):
            self._advance()
        while self._peek() == "+":
            self._advance()
            while self._peek().isdigit():
                self._advance()
        self.tokens.append(
            Token(TokenKind.OP, self.source[offset : self.index], line, col, offset)
        )

    def _string(self) -> None:
        """Scan string while tokenizing Valiance source."""
        line, col, offset = self.line, self.column, self.index
        self._advance()
        pieces: list[str] = []
        interpolation_depth = 0
        while not self._at_end:
            char = self._advance()
            if char == '"' and interpolation_depth == 0:
                self.tokens.append(
                    Token(
                        TokenKind.STRING,
                        "".join(pieces),
                        line,
                        col,
                        offset,
                        self.source[offset + 1 : self.index - 1],
                    )
                )
                return
            if char == "\\":
                escape_line, escape_col = self.line, self.column - 1
                if self._at_end:
                    self._fail(
                        "unterminated string escape; expected a character after backslash",
                        escape_line,
                        escape_col,
                    )
                    if self.recover:
                        return
                escaped = self._advance()
                escape_values = {
                    '"': '"',
                    "\\": "\\",
                    "$": "$",
                    "n": "\n",
                    "t": "\t",
                }
                if escaped not in escape_values:
                    self._fail(
                        f"invalid string escape '\\{escaped}'",
                        escape_line,
                        escape_col,
                    )
                    if self.recover:
                        self._skip_invalid_string()
                        return
                pieces.append(escape_values[escaped])
            elif interpolation_depth > 0 and char == '"':
                pieces.append(char)
                if not self._string_interpolation_nested_string(pieces):
                    if self.recover:
                        self._skip_invalid_string()
                    return
            else:
                pieces.append(char)
                if char == "$" and self._peek() == "{":
                    pieces.append(self._advance())
                    interpolation_depth += 1
                elif interpolation_depth > 0 and char == "{":
                    interpolation_depth += 1
                elif interpolation_depth > 0 and char == "}":
                    interpolation_depth -= 1
        self._fail("unterminated string literal; expected a closing double quote", line, col)

    def _string_interpolation_nested_string(self, pieces: list[str]) -> bool:
        """Scan and validate a string nested inside an interpolation."""
        while not self._at_end:
            char = self._advance()
            pieces.append(char)
            if char == "\\":
                escape_line, escape_col = self.line, self.column - 1
                if self._at_end:
                    self._fail("unterminated string escape", escape_line, escape_col)
                    return False
                escaped = self._advance()
                pieces.append(escaped)
                if escaped not in {'"', "\\", "$", "n", "t"}:
                    self._fail(
                        f"invalid string escape '\\{escaped}'",
                        escape_line,
                        escape_col,
                    )
                    if self.recover:
                        self._skip_invalid_string()
                    return False
            elif char == '"':
                return True
        self._fail("unterminated nested string")
        return False

    def _skip_invalid_string(self) -> None:
        """Consume through a closing quote after a recoverable string error."""
        while not self._at_end:
            char = self._advance()
            if char == "\\" and not self._at_end:
                self._advance()
            elif char == '"':
                return

    def _starts_number(self) -> bool:
        """Return the Boolean result of starts number while tokenizing Valiance source."""
        char = self._peek()
        if char.isdigit():
            return True
        return char == "-" and self._peek(1).isdigit()

    def _number(self) -> None:
        """Scan number while tokenizing Valiance source."""
        line, col, offset = self.line, self.column, self.index
        if self._peek() == "-":
            self._advance()
        self._number_part()
        if self._peek() in {"e", "E"}:
            self._exponent()
        if self._peek() == "i":
            self._advance()
            if self._peek() in {"+", "-"} or self._peek().isdigit():
                if self._peek() in {"+", "-"}:
                    self._advance()
                self._number_part()
                if self._peek() in {"e", "E"}:
                    self._exponent()

        # Make sure that the number does not have any leading 0s (except for the number 0 itself).
        # This is a common source of bugs in many programming languages, so we want to catch it early.

        number_str = self.source[offset : self.index]
        if number_str.startswith("0") and len(number_str) > 1 and number_str[1] != ".":
            self._fail("numbers cannot have leading 0s", line, col)

        self.tokens.append(
            Token(
                TokenKind.NUMBER,
                self.source[offset : self.index],
                line,
                col,
                offset,
            )
        )

    def _number_part(self) -> None:
        """Scan number part while tokenizing Valiance source."""
        while self._peek().isdigit():
            self._advance()
        if self._peek() == "." and self._peek(1).isdigit():
            self._advance()
            while self._peek().isdigit():
                self._advance()

    def _exponent(self) -> None:
        """Scan an integer or real-valued scientific-notation exponent."""
        self._advance()
        if self._peek() in {"+", "-"}:
            self._advance()
        if not self._peek().isdigit():
            self._fail("expected exponent digits")
            if self.recover:
                return
        self._number_part()

    def _ident(self) -> None:
        """Scan ident while tokenizing Valiance source."""
        line, col, offset = self.line, self.column, self.index
        self._advance()
        while self._is_ident_part(self._peek()):
            self._advance()
        while self._peek() == "?":
            self._advance()
        raw = self.source[offset : self.index]
        self.tokens.append(
            Token(
                TokenKind.IDENT,
                normalize_identifier(raw),
                line,
                col,
                offset,
                raw=raw,
            )
        )

    def _operator(self) -> None:
        """Scan operator while tokenizing Valiance source."""
        line, col, offset = self.line, self.column, self.index
        if self._peek() == "\\":
            self._advance()
            if self._is_ident_start(self._peek()):
                self._advance()
                while self._is_ident_part(self._peek()):
                    self._advance()
                raw = self.source[offset : self.index]
                self.tokens.append(
                    Token(
                        TokenKind.OP,
                        "\\" + normalize_identifier(raw[1:]),
                        line,
                        col,
                        offset,
                        raw=raw,
                    )
                )
                return
        # Emit exactly one OP token per operator character, each spanning
        # only that character. This is deliberate: the parser is the layer
        # that decides whether an adjacent run of these single-char tokens
        # (e.g. "+" "+") should be treated as one merged operator ("++") or
        # as two separate ones, based on whether whitespace sits between
        # them (see Parser._adjacent / Parser._operator_run). The lexer
        # must not pre-merge them, or that distinction is lost before the
        # parser ever sees it.
        while self._peek() in _OP_CHARS:
            if self._peek() == "=" and self._peek(1) == ">":
                break
            char_line, char_col, char_offset = self.line, self.column, self.index
            char = self._advance()
            self.tokens.append(
                Token(TokenKind.OP, char, char_line, char_col, char_offset)
            )
        if self.index == offset:
            self._advance()

    @staticmethod
    def _is_ident_start(char: str) -> bool:
        """Return whether the value is ident start."""
        return is_xid_start(char) and not forbidden_identifier_character(char)

    @staticmethod
    def _is_ident_part(char: str) -> bool:
        """Return whether the value is ident part."""
        return is_xid_continue(char) and not forbidden_identifier_character(char)

    def _fail(
        self, message: str, line: int | None = None, col: int | None = None
    ) -> None:
        """Report a lexical error, optionally leaving a recoverable token."""
        error = LexError(message, line=line or self.line, column=col or self.column)
        if not self.recover:
            raise error
        self.diagnostics.append(error)
        self.tokens.append(
            Token(TokenKind.ERROR, message, error.line or self.line,
                  error.column or self.column, self.index)
        )
