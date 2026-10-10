"""Source editing actions built on TextArea and the shared source formatter."""

from textual import events
from textual.document._document import Selection

from valiance.parsing import LexError
from valiance.parsing.lexer import TokenKind, lex
from valiance.source_tools import format_source

from .editor import SourceEditor


class DocumentEditor(SourceEditor):
    """Add undoable indentation without replacing native selection or editing."""

    BINDINGS = [
        ("ctrl+],f10", "indent_lines", "Indent"),
        ("ctrl+[,shift+f10", "dedent_lines", "Dedent"),
    ]

    def source_offset(self, location: tuple[int, int]) -> int:
        """Convert source row/column, never wrapped cells, to a Python text offset."""
        row, column = location
        return (
            sum(
                len(line) + len(self.document.newline)
                for line in self.document.lines[:row]
            )
            + column
        )

    def source_location(self, offset: int) -> tuple[int, int]:
        """Convert a clamped Python offset back to source coordinates."""
        prefix = self.text[: max(0, min(len(self.text), offset))]
        return prefix.count("\n"), len(prefix.rsplit("\n", 1)[-1])

    async def _on_key(self, event: events.Key) -> None:
        """Make a newline or completed closing keyword one native undoable edit."""
        if not self.read_only and event.key == "enter":
            start, end = sorted(self.selection)
            prefix = self.text[: self.source_offset(start)]
            line = prefix.rsplit("\n", 1)[-1]
            indentation = line[: len(line) - len(line.lstrip(" \t"))]
            try:
                formatted = format_source(
                    prefix + "\nx",
                    indent_width=self.indent_width,
                    add_trailing_commas=False,
                )
                last = formatted.rsplit("\n", 1)[-1]
                indentation = last[: len(last) - len(last.lstrip())]
            except LexError:
                pass
            self.replace(
                "\n" + indentation, start, end, maintain_selection_offset=False
            )
            event.stop()
            event.prevent_default()
            return
        if not self.read_only and event.character == "d" and self.selection.is_empty:
            row, column = self.cursor_location
            line = self.document.lines[row]
            candidate = line[:column] + "d" + line[column:]
            if candidate.strip() == "end":
                prefix = "\n".join([*self.document.lines[:row], candidate])
                try:
                    tokens = lex(prefix)
                    closes = any(
                        token.kind == TokenKind.IDENT
                        and token.value == "end"
                        and token.line == row + 1
                        for token in tokens
                    )
                    if closes:
                        last = format_source(
                            prefix,
                            indent_width=self.indent_width,
                            add_trailing_commas=False,
                        ).rsplit("\n", 1)[-1]
                        self.replace(
                            last,
                            (row, 0),
                            (row, len(line)),
                            maintain_selection_offset=False,
                        )
                        event.stop()
                        event.prevent_default()
                        return
                except LexError:
                    pass
        await super()._on_key(event)

    def _indent_lines(self, dedent: bool) -> None:
        """Change selected source rows as one edit and retain the selected block."""
        first, last = sorted(self.selection)
        final_row = last[0] - int(last[1] == 0 and last[0] > first[0])
        rows = self.document.lines[first[0] : final_row + 1]
        if dedent:
            adjusted = [
                line[1:]
                if line.startswith("\t")
                else line[min(self.indent_width, len(line) - len(line.lstrip(" "))) :]
                for line in rows
            ]
        else:
            adjusted = [" " * self.indent_width + line for line in rows]
        self.replace(
            "\n".join(adjusted),
            (first[0], 0),
            (final_row, len(rows[-1])),
            maintain_selection_offset=False,
        )
        self.selection = Selection((first[0], 0), (final_row, len(adjusted[-1])))

    def action_indent_lines(self) -> None:
        """Indent the caret row or selected block using configured spaces."""
        self._indent_lines(False)

    def action_dedent_lines(self) -> None:
        """Dedent source rows, with a distinguishable fallback key."""
        self._indent_lines(True)
