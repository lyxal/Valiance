"""User-facing diagnostic rendering helpers."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from valiance.repl import highlighted_fragments


@dataclass(frozen=True, slots=True)
class SourceLocation:
    line: int
    column: int


class DiagnosticError(Exception):
    """Exception with a structured source location."""

    def __init__(
        self,
        message: str,
        *,
        line: int | None = None,
        column: int | None = None,
    ) -> None:
        """Initialize this diagnostic error."""
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    def __str__(self) -> str:
        """Return the human-readable representation of this diagnostic error."""
        if self.line is None or self.column is None:
            return self.message
        return f"{self.message} at {self.line}:{self.column}"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    stage: str
    message: str
    location: SourceLocation | None = None
    help: str | None = None


_RESET = "\033[0m"
_BOLD = "\033[1m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_BLUE = "\033[34m"
_MAGENTA = "\033[35m"
_CYAN = "\033[36m"
_GREEN = "\033[32m"
_WHITE = "\033[37m"
_DIM = "\033[2m"
_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_SYNTAX_STYLES = {
    "class:keyword": _BOLD + _MAGENTA,
    "class:type": _CYAN,
    "class:number": _YELLOW,
    "class:string": _GREEN,
    "class:comment": _DIM,
    "class:variable": _BLUE,
    "class:tag": _RED,
    "class:operator": _BOLD + _WHITE,
    "class:punctuation": _WHITE,
    "class:name": _WHITE,
}
_LOCATION_PREFIX = re.compile(
    r"^(?P<line>\d+):(?P<column>\d+):\s*(?P<message>.*)$",
    re.DOTALL,
)


def from_message(stage: str, message: str) -> Diagnostic:
    """Build a diagnostic from the analyser's current text format."""
    location = None
    match = _LOCATION_PREFIX.match(message)
    if match:
        location = SourceLocation(
            int(match.group("line")),
            int(match.group("column")),
        )
        message = match.group("message")
    message, specific_help = _split_specific_help(message)
    return Diagnostic(
        stage,
        message,
        location,
        specific_help or _help_for(message),
    )


def _split_specific_help(message: str) -> tuple[str, str | None]:
    """Move analyser-provided guidance out of the message into diagnostic help."""
    lines = message.splitlines()
    help_lines = [line.removeprefix("help: ") for line in lines if line.startswith("help: ")]
    if help_lines:
        message_lines = [line for line in lines if not line.startswith("help: ")]
        return "\n".join(message_lines), " ".join(help_lines)
    if len(lines) == 2 and lines[1].startswith("did you mean "):
        return lines[0], lines[1]
    return message, None


def from_exception(stage: str, exc: BaseException) -> Diagnostic:
    """Build a diagnostic from a compiler exception."""
    line = getattr(exc, "line", None)
    column = getattr(exc, "column", None)
    location = None
    message = str(exc)
    if isinstance(line, int) and isinstance(column, int):
        location = SourceLocation(line, column)
        message = getattr(exc, "message", message)
    else:
        parsed = from_message(stage, str(exc))
        if parsed.location is not None:
            return Diagnostic(stage, parsed.message, parsed.location, parsed.help)
    return Diagnostic(stage, message, location, _help_for(message))


def render(
    diagnostic: Diagnostic,
    source: str | None = None,
    *,
    source_file: Path | None = None,
    color: bool = False,
) -> str:
    """Render a compiler diagnostic with source context when available."""
    message = _highlight_inline_code(diagnostic.message, color)
    lines = [f"{_style_stage(diagnostic.stage, color)}: {message}"]
    if diagnostic.location is not None:
        label = "<code>" if source_file is None else str(source_file)
        location = diagnostic.location
        lines.append(
            _style(
                f"  --> {label}:{location.line}:{location.column}",
                _BLUE,
                color,
            )
        )
        if source is not None:
            snippet = _source_line(source, diagnostic.location.line)
            if snippet is not None:
                gutter_width = len(str(diagnostic.location.line))
                caret_column = max(diagnostic.location.column, 1)
                blank_gutter = f"{' ' * gutter_width} |"
                line_gutter = f"{diagnostic.location.line} |"
                caret = _style("^", _diagnostic_color(diagnostic.stage), color)
                lines.append(_style(blank_gutter, _BLUE, color))
                highlighted = _highlight_source(snippet, color)
                lines.append(f"{_style(line_gutter, _BLUE, color)} {highlighted}")
                lines.append(
                    f"{_style(blank_gutter, _BLUE, color)} "
                    f"{' ' * (caret_column - 1)}{caret}"
                )
    if diagnostic.help is not None:
        help_text = _highlight_inline_code(diagnostic.help, color)
        lines.append(f"  {_style('help', _BOLD, color)}: {help_text}")
    return "\n".join(lines)


def should_color(stream: TextIO | None = None) -> bool:
    """Return whether diagnostics should use ANSI color for this stream."""
    stream = sys.stderr if stream is None else stream
    return bool(getattr(stream, "isatty", lambda: False)())


def _highlight_source(source: str, color: bool) -> str:
    """Render Valiance source with the same token colours as the enhanced REPL."""
    if not color:
        return source
    return "".join(
        _style(text, _SYNTAX_STYLES.get(token_style, ""), True)
        if token_style
        else text
        for token_style, text in highlighted_fragments(source)
    )


def _highlight_inline_code(text: str, color: bool) -> str:
    """Syntax-highlight backtick-delimited Valiance fragments in diagnostic prose."""
    if not color:
        return text

    def replace(match: re.Match[str]) -> str:
        """Highlight one backtick-delimited source fragment."""
        code = _highlight_source(match.group(1), True)
        return f"{_style('`', _DIM, True)}{code}{_style('`', _DIM, True)}"

    return _INLINE_CODE.sub(replace, text)


def _source_line(source: str, line: int) -> str | None:
    """Source line while rendering compiler diagnostics."""
    lines = source.splitlines()
    if line < 1 or line > len(lines):
        return None
    return lines[line - 1].replace("\t", "    ")


def _style(text: str, code: str, enabled: bool) -> str:
    """Compute style while rendering compiler diagnostics."""
    if not enabled:
        return text
    return f"{code}{text}{_RESET}"


def _diagnostic_color(stage: str) -> str:
    """Compute diagnostic color while rendering compiler diagnostics."""
    return _YELLOW if "warning" in stage.lower() else _RED


def _style_stage(stage: str, color: bool) -> str:
    """Compute style stage while rendering compiler diagnostics."""
    return _style(stage, _BOLD + _diagnostic_color(stage), color)


def _help_for(message: str) -> str | None:
    """Compute help for while rendering compiler diagnostics."""
    if message.startswith("unknown element "):
        return (
            "Check the element name, define it before use, or import the module "
            "that provides it."
        )
    if message.startswith("undefined variable "):
        return (
            "Variables are read with `$name`; make sure this name was assigned "
            "first."
        )
    if message.startswith("no overloads for "):
        return (
            "The values on the stack do not match any available overload. "
            "Look at the stack shape immediately before this call."
        )
    if message.startswith("ambiguous "):
        return (
            "Add a type annotation or element disambiguation so the compiler can "
            "choose one overload."
        )
    if message.startswith("cannot cast ") or message.startswith(
        "cannot safely cast "
    ):
        return "Use `as?[T]` for optional refinement or `as![T]` when failure should stop execution."
    if message.startswith("empty stack"):
        return "This operation needs a value first; place the producer before it."
    if message.startswith("expected "):
        return "The parser reached a different token than this construct requires."
    if message.startswith("unexpected character"):
        return "Remove the character or add lexer support for the syntax you intended."
    if message.startswith("unterminated "):
        return "Add the missing closing delimiter before the end of the file."
    return None
