"""Compiler-token styling layered onto TextArea's source-coordinate model."""

from bisect import bisect_right
from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.widgets import TextArea

from valiance.parsing.lexer import TokenKind, lex_with_diagnostics


@dataclass(frozen=True)
class SourceEmphasis:
    """A half-open range in Python source offsets, independent of terminal cells."""

    start: int
    end: int
    style: str


class SourceEditor(TextArea):
    """Retain native editing, undo, selection and wrapping while styling tokens."""

    TOKEN_STYLES = {
        TokenKind.IDENT: "#bfc9e0",
        TokenKind.NUMBER: "#dcb778",
        TokenKind.STRING: "#d5a1c8",
        TokenKind.OP: "#8bb9df",
    }

    def __init__(self, text: str = "", **kwargs) -> None:
        """Configure two-space editing without installing a second parser."""
        self._source_text: str | None = None
        self._styled_lines: dict[int, list[tuple[int, int, str]]] = {}
        self._emphasis: tuple[SourceEmphasis, ...] = ()
        super().__init__(
            text,
            soft_wrap=True,
            show_line_numbers=True,
            tab_behavior="indent",
            **kwargs,
        )
        self.indent_width = 2
        self.indent_type = "spaces"

    def set_emphasis(self, spans: tuple[SourceEmphasis, ...]) -> None:
        """Replace revision-specific overlays and invalidate cached rendering."""
        self._emphasis = spans
        self._source_text = None
        self.notify_style_update()
        self.refresh()

    @on(TextArea.Changed)
    def _source_changed(self, message: TextArea.Changed) -> None:
        """Dismiss obsolete overlays on every edit, including undo and redo."""
        if message.text_area is self:
            self.set_emphasis(())

    def _index_styles(self) -> None:
        """Index compiler token and overlay offsets by unwrapped source row."""
        source = self.text
        if source == self._source_text:
            return
        starts = [0]
        starts.extend(index + 1 for index, char in enumerate(source) if char == "\n")
        tokens, _ = lex_with_diagnostics(source)
        spans = []
        for token in tokens:
            style = self.TOKEN_STYLES.get(token.kind)
            if style is None:
                continue
            width = len(token.raw if token.raw is not None else token.value)
            if token.kind == TokenKind.STRING:
                width += 2  # The lexer retains string contents without the quotes.
            spans.append(SourceEmphasis(token.offset, token.offset + width, style))
        # Diagnostic emphasis must take precedence over lexical colors.
        spans.extend(self._emphasis)
        indexed: dict[int, list[tuple[int, int, str]]] = {}
        for span in spans:
            start, end = max(0, span.start), min(len(source), span.end)
            if start >= end:
                continue
            first = bisect_right(starts, start) - 1
            last = bisect_right(starts, end - 1) - 1
            for row in range(first, last + 1):
                row_start = starts[row]
                row_end = starts[row + 1] - 1 if row + 1 < len(starts) else len(source)
                indexed.setdefault(row, []).append(
                    (
                        max(start, row_start) - row_start,
                        min(end, row_end) - row_start,
                        span.style,
                    )
                )
        self._styled_lines = indexed
        self._source_text = source

    def get_line(self, line_index: int) -> Text:
        """Style source characters before TextArea expands tabs and wraps cells."""
        self._index_styles()
        line = super().get_line(line_index)
        for start, end, style in self._styled_lines.get(line_index, ()):
            line.stylize(style, start, end)
        return line
