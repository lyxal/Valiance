"""User-facing diagnostic rendering helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


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
        super().__init__(message)
        self.message = message
        self.line = line
        self.column = column

    def __str__(self) -> str:
        if self.line is None or self.column is None:
            return self.message
        return f"{self.message} at {self.line}:{self.column}"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    stage: str
    message: str
    location: SourceLocation | None = None
    help: str | None = None


_LOCATION_PREFIX = re.compile(r"^(?P<line>\d+):(?P<column>\d+):\s*(?P<message>.*)$")


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
    return Diagnostic(stage, message, location, _help_for(message))


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
) -> str:
    """Render a compiler diagnostic with source context when available."""
    lines = [f"{diagnostic.stage}: {diagnostic.message}"]
    if diagnostic.location is not None:
        label = "<code>" if source_file is None else str(source_file)
        lines.append(
            f"  --> {label}:{diagnostic.location.line}:{diagnostic.location.column}"
        )
        if source is not None:
            snippet = _source_line(source, diagnostic.location.line)
            if snippet is not None:
                gutter_width = len(str(diagnostic.location.line))
                caret_column = max(diagnostic.location.column, 1)
                lines.append(f"{' ' * gutter_width} |")
                lines.append(f"{diagnostic.location.line} | {snippet}")
                lines.append(
                    f"{' ' * gutter_width} | "
                    f"{' ' * (caret_column - 1)}^"
                )
    if diagnostic.help is not None:
        lines.append(f"  help: {diagnostic.help}")
    return "\n".join(lines)


def _source_line(source: str, line: int) -> str | None:
    lines = source.splitlines()
    if line < 1 or line > len(lines):
        return None
    return lines[line - 1].replace("\t", "    ")


def _help_for(message: str) -> str | None:
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
        return "Use `as!` only for runtime-checked casts that may genuinely succeed."
    if message.startswith("empty stack"):
        return "This operation needs a value first; place the producer before it."
    if message.startswith("expected "):
        return "The parser reached a different token than this construct requires."
    if message.startswith("unexpected character"):
        return "Remove the character or add lexer support for the syntax you intended."
    if message.startswith("unterminated "):
        return "Add the missing closing delimiter before the end of the file."
    return None
