from enum import Enum, auto


class TokenType(Enum):
    # === Literals ===
    NUMBER = auto()  # any numeric literal: 69, -3.14, 69i420, 6e7, 1e2i3e4, …

    # String interpolation – a string is emitted as a sequence of these tokens:
    #   STRING_START  STRING_CONTENT?
    #   ( INTERP_START <tokens> INTERP_END STRING_CONTENT? )*
    #   STRING_END
    # A plain string with no interpolation still uses START/END so the parser
    # has a uniform shape.  STRING_CONTENT holds the raw (unescaped) text
    # between interpolations.  INTERP_START covers both "$ident" (the lexer
    # emits VARIABLE directly after it) and "${" (arbitrary tokens follow until
    # the matching INTERP_END / "}").
    STRING_START = auto()  # the opening "
    STRING_CONTENT = auto()  # literal text segment inside a string
    INTERP_START = auto()  # $ (before identifier) or ${ (before expression)
    INTERP_END = auto()  # implicit end-of-identifier or explicit }
    STRING_END = auto()  # the closing "

    # === Identifiers & references ===
    IDENTIFIER = auto()  # element / type names: foo, +, <=, myElement2
    VARIABLE = auto()  # $name
    TAG_NAME = auto()  # #sorted, #infinite, #boolean …

    # === Keywords ===
    # Declarations
    DEFINE = auto()  # define
    VECDEFINE = auto()  # vecdefine
    MULTI = auto()  # multi  (used before define)
    OBJECT = auto()  # object
    TRAIT = auto()  # trait
    VARIANT = auto()  # variant
    ENUM = auto()  # enum
    TAG = auto()  # tag
    EXTERNAL = auto()  # external
    IMPORT = auto()  # import
    PUBLIC = auto()  # public
    CAST = auto()  # cast  (type-cast definition)

    # Access modifiers
    PRIVATE = auto()  # private
    READABLE = auto()  # readable

    # Block delimiters
    ARROW = auto()  # =>
    END = auto()  # end

    # Functions & closures
    FN = auto()  # fn
    RETURN_ARROW = auto()  # ->

    # Control flow
    IF = auto()  # if
    ELSE = auto()  # else
    WHILE = auto()  # while
    FOREACH = auto()  # foreach
    UNFOLD = auto()  # unfold
    AT = auto()  # at
    BREAK = auto()  # break
    MATCH = auto()  # match
    AS = auto()  # as  (type-cast / match arm)
    AS_BANG = auto()  # as!  (unsafe type-cast)
    DEFAULT = auto()  # default  (match fallthrough)
    TRY = auto()  # try
    HANDLE = auto()  # handle

    # Error / assertion
    ASSERT = auto()  # assert
    PANIC = auto()  # panic

    # Type-system key IDENTIFIERs
    VEC = auto()  # vec  (vectorisation marker in params)
    ATOMIC = auto()  # atomic  (atomic generic marker)
    CONST = auto()  # const
    EXTEND = auto()  # extend  (trait required-impl / extend modifier)
    WHERE = auto()  # where  (where clause)
    SELF = auto()  # self

    # Literal aliases
    TRUE = auto()  # true  (alias for 1)
    FALSE = auto()  # false  (alias for 0)
    NONE = auto()  # None

    # === Modifiers (prefix sigils) ===
    COLON_MOD = auto()  # :  element modifier (wrap next elem as fn arg)
    BACKSLASH_MOD = auto()  # \  infix / right-hand-value modifier
    TICK = auto()  # '  quick-function wrapper  'element == fn => element

    # === Annotations (bare names after @ / @@) ===
    STRUCTURE_ANNOTATION = auto()  # structural annotation (e.g. @serializable)
    ELEMENT_ANNOTATION = auto()  # element annotation (e.g. @@tupled)

    # === Punctuation & delimiters ===
    L_PAREN = auto()  # (
    R_PAREN = auto()  # )
    L_BRACKET = auto()  # [
    R_BRACKET = auto()  # ]
    L_BRACE = auto()  # {  used in tuple types {T, U}
    R_BRACE = auto()  # }
    COMMA = auto()  # ,
    DOT = auto()  # .  member access  $.field  or  $name.field
    COLON = auto()  # :  type annotation  $name: Type
    DOUBLE_COLON = auto()  # :: object-friendly element access  Name::element
    SEMICOLON = auto()  # ;  statement separator (inside ECS / where clauses)
    TILDE = auto()  # ~  local-root import prefix  ~utils + potential rank suffix
    UNDERSCORE = auto()  # _  placeholder / wildcard
    PLUS = auto()  # +  (also used as a rank suffix, but always emitted as PLUS)
    STAR = auto()  # *  (also used as a rank suffix, but always emitted as STAR)
    EXCLAMATION = (
        auto()
    )  # !  (also used as a rank suffix, but always emitted as EXCLAMATION)
    LT = auto()  # <
    GT = auto()  # >

    # === Operators ===
    # Assignment
    EQUAL = auto()  # =
    AUG_ASSIGN = auto()  # :=  augmented assignment
    MULTIPLE_ASSIGN = auto()  # $(  multiple assignment (destructuring)
    ELLIPSIS = auto()  # ...  variadic tuple type  {Number...}

    # Type operators
    PIPE = auto()  # |  union type
    AMPERSAND = auto()  # &  intersection type
    QUESTION = auto()  # ?  optional type suffix
    QUESTION_BANG = auto()  # ?!  unsafe unwrap element

    # Hash / tag sigils
    HASH = auto()  # #  starts a tag application  #sorted
    HASH_BANG = auto()  # #! tag removal  #!sorted

    # Misc operators
    DOLLAR_BRACKET = auto()  # $[  stack-index / slice operator

    # === Comments ===
    LINE_COMMENT = auto()  # #?  …  (single-line comment)
    BLOCK_COMMENT = auto()  # #/ … /#  (nested multi-line comment)

    # === Special ===
    NEWLINE = auto()  # significant newline (ends statements / values)
    WHITESPACE = auto()  # non-significant whitespace (indentation, spacing)
    EOF = auto()  # end of file
    ERROR = auto()  # lexer error (e.g. unterminated string)


class Token:
    def __init__(self, type_: TokenType, value: str, line: int, column: int):
        self.type = type_
        self.value = value
        self.line = line
        self.column = column

    def __repr__(self):
        return f"Token(type={self.type}, value='{self.value}', line={self.line}, column={self.column})"


WHITESPACE = " \t"
NEWLINE = ("\n", "\r\n")

KEYWORD_TO_TOKEN = {
    "define": TokenType.DEFINE,
    "vecdefine": TokenType.VECDEFINE,
    "multi": TokenType.MULTI,
    "object": TokenType.OBJECT,
    "trait": TokenType.TRAIT,
    "variant": TokenType.VARIANT,
    "enum": TokenType.ENUM,
    "tag": TokenType.TAG,
    "external": TokenType.EXTERNAL,
    "import": TokenType.IMPORT,
    "public": TokenType.PUBLIC,
    "cast": TokenType.CAST,
    "private": TokenType.PRIVATE,
    "readable": TokenType.READABLE,
    "=>": TokenType.ARROW,
    "end": TokenType.END,
    "fn": TokenType.FN,
    "->": TokenType.RETURN_ARROW,
    "if": TokenType.IF,
    "else": TokenType.ELSE,
    "while": TokenType.WHILE,
    "foreach": TokenType.FOREACH,
    "unfold": TokenType.UNFOLD,
    "at": TokenType.AT,
    "break": TokenType.BREAK,
    "match": TokenType.MATCH,
    "as": TokenType.AS,
    "as!": TokenType.AS_BANG,
    "default": TokenType.DEFAULT,
    "try": TokenType.TRY,
    "handle": TokenType.HANDLE,
    "assert": TokenType.ASSERT,
    "panic": TokenType.PANIC,
    "vec": TokenType.VEC,
    "atomic": TokenType.ATOMIC,
    "const": TokenType.CONST,
    "extend": TokenType.EXTEND,
    "where": TokenType.WHERE,
    "self": TokenType.SELF,
    "true": TokenType.TRUE,
    "false": TokenType.FALSE,
    "None": TokenType.NONE,
}


import string
from typing import Callable, TypeVar

T = TypeVar("T")

SINGLE_LINE_COMMENT = "#?"
MULTILINE_COMMENT_START = "#/"
MULTILINE_COMMENT_END = "/#"


def unwrap_and_test(val: T | None, condition: Callable[[T], bool]) -> bool:
    """
    Unwrap an optional value and test it against a condition.

    :param val: The optional value to unwrap.
    :param condition: A callable that takes the unwrapped value and returns a bool.
    :return: True if the value is not None and the condition is met, False otherwise.
    """
    if val is not None:
        return condition(val)
    return False


class Lexer:
    """
    Notice: A method ending with an underscore (_) does NOT
    update the line and column numbers.

    Methods starting with `scan`, and are called directly by scan_tokens()
    must add tokens at the end of their logic, rather than returning a
    value back to scan_tokens().
    """

    def __init__(self, source: str):
        self.source = source
        self.characters = list(source)
        self.tokens: list[Token] = []
        self.line, self.column = 1, 1

    def _advance(self) -> str:
        """
        Pop the first character from the character list and return it.

        If the character is a newline, the line and column numbers
        will be updated accordingly.

        :return: The popped character.
        """
        char = self.characters.pop(0)
        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def _add_token(self, type_: TokenType, value: str):
        """
        Create a new token and add it to the token list.

        Removes the token's value from the source string
        and updates the column number.

        :param type_: The type of the token.
        :param value: The value of the token.
        :return: None
        """
        token = Token(type_, value, self.line, self.column)
        self.tokens.append(token)
        self.characters = self.characters[len(value) :]
        self.column += len(value)

    def _add_token_(self, type_: TokenType, value: str, line: int, column: int):
        """
        Create a new token and add it to the token list.

        Does NOT remove the token's value from the source string
        or update the column number.

        Useful for flexible token creation without affecting
        the current scanning state.

        :param type_: The type of the token.
        :param value: The value of the token.
        :param line: The line number of the token.
        :param column: The column number of the token.
        :return: None
        """
        token = Token(type_, value, line, column)
        self.tokens.append(token)

    def _discard_(self, n: int = 1):
        """
        Pop the first character from the character list without
        caring about its value.

        :param self: The Scanner instance.
        :param n: The number of characters to discard.
        """
        for _ in range(n):
            self.characters.pop(0)

    def _head_equals_(self, string: str) -> bool:
        """
        Check if the start of the character list matches a given string.

        :param string: The string to compare against.
        :return: True if the start of the character list matches the string, False otherwise.
        """
        return unwrap_and_test(self._peek_(len(string)), lambda s: s == string)

    def _head_matches_(self, pattern: str) -> bool:
        """
        Check if the start of the character list matches a given regex pattern.

        :param self: The Scanner instance.
        :param pattern: The regex pattern to match against.
        :return: True if the start of the character list matches the pattern, False otherwise.
        :rtype: bool
        """

        import re

        pobj = re.compile(pattern)
        mobj = pobj.match("".join(self.characters))
        return mobj is not None

    def _peek_(self, n: int = 1) -> str | None:
        """
        Peek at the first n characters in the character list.

        :param n: The number of characters to peek at.
        :return: The first n characters as a string, or None if there are not enough
        """
        if n - 1 < len(self.characters):
            return "".join(self.characters[:n])
        return None

    def _scan_token_single_(self) -> bool:
        """
        Scan a single token from the current position.
        Returns True if a token was scanned, False if nothing matched.
        """
        if not self.characters:
            return False

        HEAD = self.characters[0]
        match HEAD:
            case " " | "\r" | "\t":
                self._add_token_(TokenType.WHITESPACE, HEAD, self.line, self.column)
                self._discard_()
                self.column += 1
            case "\n":
                self._add_token(TokenType.NEWLINE, HEAD)
                self.line += 1
                self.column = 1
            case _ if HEAD.isdecimal() or self._head_matches_(r"-\d"):
                self._scan_number()
            case "=" if not self._head_equals_("==") and not self._head_equals_("=>"):
                self._add_token(TokenType.EQUAL, "=")
            case _ if self._head_equals_(":="):
                self._add_token(TokenType.AUG_ASSIGN, ":=")
            case _ if HEAD in string.ascii_letters:
                self._scan_element()
            case "$" if not self._head_equals_("$(") and not self._head_equals_("$["):
                self._add_token(TokenType.VARIABLE, HEAD)
            case _ if self._head_equals_("$("):
                self._add_token(TokenType.MULTIPLE_ASSIGN, "$(")
            case _ if self._head_equals_("$["):
                self._add_token(TokenType.DOLLAR_BRACKET, "$[")
            case "(":
                self._add_token(TokenType.L_PAREN, HEAD)
            case ")":
                self._add_token(TokenType.R_PAREN, HEAD)
            case "[":
                self._add_token(TokenType.L_BRACKET, HEAD)
            case "]":
                self._add_token(TokenType.R_BRACKET, HEAD)
            case "{":
                self._add_token(TokenType.L_BRACE, HEAD)
            case "}":
                self._add_token(TokenType.R_BRACE, HEAD)
            case "|":
                self._add_token(TokenType.PIPE, HEAD)
            case "&":
                self._add_token(TokenType.AMPERSAND, HEAD)
            case ",":
                self._add_token(TokenType.COMMA, HEAD)
            case _ if self._head_equals_("->"):
                self._add_token(TokenType.RETURN_ARROW, "->")
            case _ if self._head_equals_("=>"):
                self._add_token(TokenType.ARROW, "=>")
            case ":" if not self._head_equals_("::") and not self._head_equals_(":="):
                self._add_token(TokenType.COLON, ":")
            case _ if self._head_equals_("::"):
                self._add_token(TokenType.DOUBLE_COLON, "::")
            case "@":
                self._scan_annotation()
            case _ if self._head_equals_(SINGLE_LINE_COMMENT):
                # Comment, discard until newline
                while self.characters and self.characters[0] != "\n":
                    self._discard_()
            case _ if self._head_equals_(MULTILINE_COMMENT_START):
                self._scan_multiline_comment_()
            case "+":
                self._add_token(TokenType.PLUS, HEAD)
            case "*":
                self._add_token(TokenType.STAR, HEAD)
            case _ if self._head_equals_("?!"):
                self._add_token(TokenType.QUESTION_BANG, "?!")
            case "?":
                self._add_token(TokenType.QUESTION, HEAD)
            case "~":
                self._add_token(TokenType.TILDE, HEAD)
            case _ if self._head_matches_("==="):
                self._add_token(TokenType.IDENTIFIER, "===")
            case _ if self._head_matches_("=="):
                self._add_token(TokenType.IDENTIFIER, "==")
            case "_":
                self._add_token(TokenType.UNDERSCORE, HEAD)
            case "'":
                self._add_token(TokenType.TICK, HEAD)
            case _ if self._head_equals_("..."):
                self._add_token(TokenType.ELLIPSIS, "...")
            case ".":
                self._add_token(TokenType.DOT, HEAD)
            case "#":
                self._scan_tag()
            case ";":
                self._add_token(TokenType.SEMICOLON, HEAD)
            case "<":
                self._add_token(TokenType.LT, HEAD)
            case ">":
                self._add_token(TokenType.GT, HEAD)
            case _:
                return False
        return True

    def scan_tokens(self) -> list[Token]:
        while self.characters:
            HEAD = self.characters[0]
            if HEAD == '"':
                self._scan_string_with_interpolation()
            elif not self._scan_token_single_():
                raise ValueError(
                    f'Unexpected character "{HEAD}" at line {self.line}, column {self.column}'
                )

        return self.tokens + [Token(TokenType.EOF, "", self.line, self.column)]

    def _scan_multiline_comment_(self):
        """Helper to scan multiline comments."""
        start_line, start_column = self.line, self.column
        while self.characters and not self._head_equals_(MULTILINE_COMMENT_END):
            self._discard_()
            if self.characters and self.characters[0] == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1
        if self._head_equals_(MULTILINE_COMMENT_END):
            self._discard_(len(MULTILINE_COMMENT_END))
        else:
            raise ValueError(
                f"Unterminated multiline comment starting at line {start_line}, column {start_column}"
            )

    def _scan_number(self):
        """
        Scan a number token from the character list.
        :return: None
        """
        start_line, start_column = self.line, self.column

        # Scan the real part of the number.
        value = self._scan_decimal()

        # Scan the imaginary OR **10 part of the number, if it exists.
        if self._head_equals_("i") or self._head_equals_("e"):
            value += self._advance()
            part = self._scan_decimal()
            if not part:
                raise ValueError(
                    f"Invalid number format at line {self.line}, column {self.column}"
                )
            value += part

        self._add_token_(TokenType.NUMBER, value, start_line, start_column)

    def _scan_decimal(self) -> str:
        """
        Scan a decimal number from the character list.
        :return: The scanned decimal number as a string.
        """

        value = ""
        if self._head_equals_("-"):
            value += self._advance()

        # Get the integer part, ensuring that multiple leading zeros
        # are treated as different tokens.
        value += self._scan_integer(separate_zeros=True)

        # Get the fractional part, if it exists.
        if self._head_equals_("."):
            value += self._advance()
            value += self._scan_integer(separate_zeros=False)

        if value.endswith("."):
            raise ValueError(
                f"Invalid number format at line {self.line}, column {self.column}"
            )

        return value

    def _scan_integer(self, separate_zeros: bool) -> str:
        """
        Scan an integer from the character list.
        :param separate_zeros: Whether to immediately stop scanning repeated '0's.
        :return: The scanned integer as a string.
        """
        value = ""
        if separate_zeros and self._head_equals_("0"):
            value += self._advance()
            return value
        while unwrap_and_test(self._peek_(), lambda c: c in string.digits):
            value += self._advance()
        return value

    def _scan_element(self):
        """
        Scan an element token from the character list.
        :return: None
        """
        start_line, start_column = self.line, self.column

        value = ""
        while unwrap_and_test(
            self._peek_(),
            lambda c: c in string.ascii_letters + string.digits + "_!*&%-",
        ):
            value += self._advance()

        if value in KEYWORD_TO_TOKEN:
            token_category = KEYWORD_TO_TOKEN[value]
        else:
            token_category = TokenType.IDENTIFIER

        self._add_token_(token_category, value, start_line, start_column)

    def _scan_identifier(self) -> str:
        """
        Scan a name identifier from the character list.
        :return: The scanned identifier as a string.
        """

        if not unwrap_and_test(
            self._peek_(), lambda c: c in string.ascii_letters + "_"
        ):
            raise ValueError(
                f"Invalid start of identifier at line {self.line}, column {self.column}"
            )
        value = ""
        while unwrap_and_test(
            self._peek_(),
            lambda c: c in string.ascii_letters + string.digits + "_",
        ):
            value += self._advance()
        return value

    def _scan_string_with_interpolation(self):
        """
        Scan a string literal with support for interpolation.
        Emits tokens in the form:
          STRING_START (STRING_CONTENT? (INTERP_START ... INTERP_END STRING_CONTENT?)*)? STRING_END
        """
        start_line, start_column = self.line, self.column

        # Emit STRING_START token
        self._add_token_(TokenType.STRING_START, '"', start_line, start_column)
        self._discard_()  # Discard the opening quote

        while self.characters and not self._head_equals_('"'):
            HEAD = self.characters[0]

            if HEAD == "\\":
                # Handle escape sequences
                self._scan_string_escape_()
            elif HEAD == "$" and self._is_interpolation_start_():
                # Handle interpolation: $identifier or ${expression}
                self._scan_string_interpolation_()
            else:
                # Regular string content
                self._scan_string_content_()

        if not self.characters:
            raise ValueError(
                f"Unterminated string literal starting at line {start_line}, column {start_column}"
            )

        # Emit STRING_END token
        end_line, end_column = self.line, self.column
        self._add_token_(TokenType.STRING_END, '"', end_line, end_column)
        self._discard_()  # Discard the closing quote

    def _is_interpolation_start_(self) -> bool:
        """Check if we're at the start of an interpolation ($ident or ${...)."""
        if not self._head_equals_("$"):
            return False
        peek2 = self._peek_(2)
        if not peek2 or len(peek2) < 2:
            return False
        next_char = peek2[1]
        return next_char in string.ascii_letters + "_" or self._head_equals_("${")

    def _scan_string_escape_(self):
        """Scan an escape sequence inside a string."""
        self._discard_()
        if not self.characters:
            raise ValueError("Unterminated escape sequence")
        escape_line, escape_column = self.line, self.column
        ESCAPE_CHAR = self._advance()
        match ESCAPE_CHAR:
            case "n":
                escape_value = "\n"
            case "t":
                escape_value = "\t"
            case "r":
                escape_value = "\r"
            case '"':
                escape_value = '"'
            case "\\":
                escape_value = "\\"
            case "$":
                escape_value = "$"  # Escaped dollar sign
            case _:
                raise ValueError(
                    f"Invalid escape sequence '\\{ESCAPE_CHAR}' at line {self.line}, column {self.column}"
                )
        self._add_token_(
            TokenType.STRING_CONTENT, escape_value, escape_line, escape_column
        )

    def _scan_string_content_(self):
        """Scan literal content inside a string until special character."""
        content_line, content_column = self.line, self.column
        content = ""
        while self.characters and self.characters[0] not in ('"', "\\", "$"):
            if self.characters[0] == "\n":
                self.line += 1
                self.column = 1
            else:
                self.column += 1
            content += self.characters.pop(0)

        # Check if next $ is an interpolation
        if (
            self.characters
            and self.characters[0] == "$"
            and not self._is_interpolation_start_()
        ):
            if self.column < len(self.characters):
                self.column += 1
            content += self.characters.pop(0)

        if content:
            self._add_token_(
                TokenType.STRING_CONTENT, content, content_line, content_column
            )

    def _scan_string_interpolation_(self):
        """
        Scan interpolation inside a string: either $identifier or ${expression}
        """
        interp_line, interp_column = self.line, self.column
        self._discard_()  # Discard the '$'

        if self._head_equals_("{"):
            # ${expression} - emit INTERP_START and scan tokens until }
            self._add_token_(TokenType.INTERP_START, "${", interp_line, interp_column)
            self._discard_()  # Discard the '{'

            # Scan tokens inside the interpolation until we find the matching '}'
            brace_depth = 1
            while self.characters and brace_depth > 0:
                if self.characters[0] == "}":
                    brace_depth -= 1
                    if brace_depth == 0:
                        self._add_token_(
                            TokenType.INTERP_END, "}", self.line, self.column
                        )
                        self._discard_()
                        break

                if self.characters[0] == "{":
                    brace_depth += 1

                if not self._scan_token_single_():
                    raise ValueError(
                        f'Unexpected character "{self.characters[0]}" inside interpolation at line {self.line}, column {self.column}'
                    )
        else:
            # $identifier - emit INTERP_START, then the identifier token, then INTERP_END
            self._add_token_(TokenType.INTERP_START, "$", interp_line, interp_column)

            # Scan the identifier
            ident_line, ident_column = self.line, self.column
            if unwrap_and_test(
                self._peek_(), lambda c: c in string.ascii_letters + "_"
            ):
                ident = self._scan_identifier()
                self._add_token_(TokenType.IDENTIFIER, ident, ident_line, ident_column)

            self._add_token_(TokenType.INTERP_END, "", self.line, self.column)

    def _scan_annotation(self):
        """
        Scan an annotation token from the character list.
        :return: None
        """
        start_line, start_column = self.line, self.column

        self._discard_()  # Discard the '@' character

        token_category = (
            TokenType.ELEMENT_ANNOTATION
            if self._head_equals_("@")
            else TokenType.STRUCTURE_ANNOTATION
        )
        if token_category == TokenType.ELEMENT_ANNOTATION:
            self._discard_()  # Discard the second '@' character for element annotations

        value = self._scan_identifier()

        self._add_token_(token_category, value, start_line, start_column)

    def _scan_tag(self):
        """
        Scan a tag token from the character list.
        :return: None
        """
        start_line, start_column = self.line, self.column
        self._discard_()  # Remove the '#'
        value = ""
        if self._head_equals_("!"):  # Handle optional leading '!'
            value += self._advance()
        while unwrap_and_test(
            self._peek_(),
            lambda c: c in string.ascii_letters + string.digits + "_",
        ):
            value += self._advance()
        if not value:
            raise ValueError(f"Invalid tag at line {self.line}, column {self.column}")
        if value == "!":
            raise ValueError(
                f"Missing tag name at line {self.line}, column {self.column}"
            )
        self._add_token_(TokenType.TAG_NAME, value, start_line, start_column)
