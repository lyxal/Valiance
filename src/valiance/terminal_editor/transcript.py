"""Selectable, bounded transcript presentation from semantic session entries."""

from rich.text import Text
from textual.document._document import Selection
from textual.widgets import TextArea

from .models import Transcript


class TranscriptView(TextArea):
    """Reuse native selection/copy and wrapping without retaining a second log."""

    STYLES = {
        "result": "#8bcf91",
        "command": "#bfc9e0",
        "input": "#bfc9e0",
        "output": "#d4daea",
        "diagnostic": "#ef8794",
        "load": "#8bb9df",
        "notice": "#dcb778",
        "truncated": "#dcb778",
    }

    def __init__(self, **kwargs) -> None:
        """Make output read-only while retaining keyboard and mouse selection."""
        self._spans = []
        self._starts = [0]
        self._discarded = 0
        self._marker_length = 0
        self._entries = ()
        super().__init__(
            read_only=True,
            show_line_numbers=False,
            soft_wrap=True,
            tab_behavior="focus",
            highlight_cursor_line=False,
            **kwargs,
        )

    def refresh_transcript(self, transcript: Transcript) -> None:
        """Render once per batch, preserving selection and a scrolled-back view."""
        text = transcript.export_text()
        entries = tuple(transcript.entries)
        if (
            text == self.text
            and entries == self._entries
            and transcript.discarded_characters == self._discarded
        ):
            return
        self._entries = entries
        selection = self.selection
        offsets = [self.document.get_index_from_location(point) for point in selection]
        follow = selection.is_empty and self.scroll_y >= self.max_scroll_y
        scroll = self.scroll_offset
        removed = transcript.discarded_characters - self._discarded
        marker_length = len(text) - transcript.characters
        delta = marker_length - self._marker_length - max(0, removed)
        self._spans = []
        offset = marker_length
        if offset:
            self._spans.append((0, offset, self.STYLES["truncated"]))
        for entry in entries:
            end = offset + len(entry.text)
            self._spans.append((offset, end, self.STYLES.get(entry.kind, "#d4daea")))
            offset = end
        self._starts = [0]
        self._starts.extend(
            index + 1 for index, char in enumerate(text) if char == "\n"
        )
        self._discarded = transcript.discarded_characters
        self._marker_length = marker_length
        self.load_text(text)
        if follow:
            self.move_cursor(self.document.end)
            self.call_after_refresh(self.scroll_end, animate=False)
        else:
            points = [
                self.document.get_location_from_index(
                    max(0, min(len(text), pos + delta))
                )
                for pos in offsets
            ]
            self.selection = Selection(*points)
            self.call_after_refresh(self.scroll_to, scroll.x, scroll.y, animate=False)

    def get_line(self, line_index: int) -> Text:
        """Color source characters by event kind before native wrapping."""
        line = super().get_line(line_index)
        start = self._starts[min(line_index, len(self._starts) - 1)]
        end = start + len(line)
        for first, last, style in self._spans:
            if last > start and first < end:
                line.stylize(style, max(0, first - start), min(len(line), last - start))
        return line
