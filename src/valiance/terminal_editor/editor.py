"""Compiler-token styling layered onto TextArea's source-coordinate model."""

from bisect import bisect_right
from dataclasses import dataclass

from rich.text import Text
from textual import on
from textual.message import Message
from textual.widgets import TextArea

from valiance.parsing.lexer import TokenKind, lex_with_diagnostics
from valiance.source_tools.syntax import KEYWORDS

from .appearance import REFERENCE, rich_color


@dataclass(frozen=True)
class SourceEmphasis:
    """A half-open range in Python source offsets, independent of terminal cells."""

    start: int
    end: int
    style: str


class SourceEditor(TextArea):
    """Retain native editing, undo, selection and wrapping while styling tokens."""

    class SourceClicked(Message):
        """Report a source click after TextArea has mapped wrapping and tab cells."""

        def __init__(self, editor, offset: int) -> None:
            """Carry the editor identity and native source offset to the controller."""
            super().__init__()
            self.editor = editor
            self.offset = offset

    def on_click(self) -> None:
        """Retain native selection and expose source diagnostic navigation."""
        row, column = self.cursor_location
        offset = (
            sum(len(line) for line in self.text.splitlines(keepends=True)[:row])
            + column
        )
        self.post_message(self.SourceClicked(self, offset))

    def __init__(self, text: str = "", **kwargs) -> None:
        """Configure two-space editing without installing a second parser."""
        self._source_text: str | None = None
        self._styled_lines: dict[int, list[tuple[int, int, str]]] = {}
        self._emphasis: tuple[SourceEmphasis, ...] = ()
        self.selected_error: tuple[int, int] | None = None
        self.notified_source = text
        self.role_colors = dict(REFERENCE)
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

    def select_error(self, span: tuple[int, int] | None) -> None:
        """Dim unrelated source only while selected diagnostic details are expanded."""
        if self.selected_error == span:
            return
        self.selected_error = span
        self.notify_style_update()
        self.refresh()

    @on(TextArea.Changed)
    def _source_changed(self, message: TextArea.Changed) -> None:
        """Dismiss obsolete overlays on every edit, including undo and redo."""
        if message.text_area is self:
            self.select_error(None)
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
        previous_end = 0
        for token in tokens:
            # The lexer omits comments; gaps between tokens retain their exact spans.
            if token.offset > previous_end:
                spans.append(
                    SourceEmphasis(
                        previous_end,
                        token.offset,
                        rich_color(self.role_colors["comment"]),
                    )
                )
            width = len(token.raw if token.raw is not None else token.value)
            if token.kind == TokenKind.STRING:
                width += 2  # The lexer retains string contents without the quotes.
            previous_end = token.offset + width
            role = {
                TokenKind.IDENT: "name",
                TokenKind.NUMBER: "number",
                TokenKind.STRING: "string",
                TokenKind.OP: "operator",
            }.get(token.kind)
            if token.kind == TokenKind.IDENT:
                if token.value in KEYWORDS:
                    role = "keyword"
                elif token.value[:1].isupper():
                    role = "type"
            if role is None:
                continue
            style = rich_color(self.role_colors[role])
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
            if style == "diagnostic":
                style = "underline " + rich_color(self.role_colors["diagnostic"])
            line.stylize(style, start, end)
        if self.selected_error is not None:
            offset = sum(
                len(row) + len(self.document.newline)
                for row in self.document.lines[:line_index]
            )
            start, end = self.selected_error
            line.stylize("dim")
            if start < offset + len(line) and end > offset:
                line.stylize(
                    "not dim bold", max(0, start - offset), min(len(line), end - offset)
                )
        return line
