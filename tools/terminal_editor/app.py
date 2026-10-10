"""Isolated four-pane Textual feasibility harness; not a public CLI replacement."""

from __future__ import annotations

import asyncio
from collections import deque

from rich.text import Text
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, ContentSwitcher, RichLog, Static, TextArea

from valiance.parsing.lexer import lex_with_diagnostics
from valiance.terminal_editor.divider import PaneDivider
from valiance.terminal_editor.editor import SourceEditor, SourceEmphasis

from .worker import SessionProbe


class CommandEditor(SourceEditor):
    """Probe Enter locking without adapting the legacy frontend."""

    class Submitted(Message):
        """Request execution of a command draft."""

        def __init__(self, source: str) -> None:
            super().__init__()
            self.source = source

    class LockChanged(Message):
        """Ask the shell to refresh its visible lock indicator."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.locked = False
        self.show_line_numbers = False

    def toggle_lock(self) -> None:
        """Preserve the draft and selection while changing Enter behavior."""
        self.locked = not self.locked
        self.post_message(self.LockChanged())

    def on_key(self, event: events.Key) -> None:
        """Keep modified Enter from either inserting text or submitting."""
        if event.key in {"ctrl+enter", "shift+enter"}:
            self.toggle_lock()
            event.prevent_default()
            event.stop()
        elif event.key == "enter" and not self.locked:
            if self.text.strip():
                self.post_message(self.Submitted(self.text))
            event.prevent_default()
            event.stop()


class FeasibilityApp(App):
    """Exercise retained editors, wrapping, IPC responsiveness and terminal keys."""

    CSS_PATH = "feasibility.tcss"
    BINDINGS = [
        ("ctrl+q", "quit", "Quit probe"),
        ("f6", "focus_next", "Focus"),
        ("f2", "next_document", "Next document"),
        ("f3", "toggle_inspector", "Inspector"),
        ("f4", "toggle_lock", "Enter lock"),
        ("f5,alt+x", "run_source", "Run source probe"),
        ("f8", "stop", "Stop probe"),
    ]

    def __init__(self, *, with_worker: bool = True) -> None:
        super().__init__()
        self.probe = SessionProbe() if with_worker else None
        self.inspector_width = 32
        self.repl_height = 12
        self.inspector_requested = True
        self.waiting_input = False
        self.recent_keys: deque[str] = deque(maxlen=8)
        self.retained_output: deque[str] = deque(maxlen=300)
        self.output_truncated = False
        self.stopping = False

    def compose(self) -> ComposeResult:
        """Mount each document once so switching never reloads or clears undo."""
        with Horizontal(id="file-strip"):
            yield Button("Document 1 / 2 (F2)", id="documents")
            yield Button("State (F3)", id="state-toggle")
            yield Button("Run (F5)", id="run")
            yield Button("Stop (F8)", id="stop")
            yield Button("Lock (F4)", id="lock")
        with Horizontal(id="working-area"):
            with ContentSwitcher(initial="source-1", id="documents-view"):
                yield SourceEditor("1 2 +", id="source-1")
                yield SourceEditor("", id="source-2")
            yield PaneDivider(vertical=True, id="state-divider")
            yield Static(
                "State unavailable\nSemantic inspection arrives in Stage 3.",
                id="inspector",
            )
        yield PaneDivider(vertical=False, id="repl-divider")
        with Vertical(id="repl"):
            yield RichLog(max_lines=300, wrap=True, markup=False, id="transcript")
            yield Static(
                "Input: Enter submits · F4 toggles multiline", id="lock-status"
            )
            yield CommandEditor(
                id="command", placeholder="Type a Valiance command here"
            )
        yield Static("Development probe — no file is saved or loaded", id="status")

    def on_mount(self) -> None:
        """Start the process and poll bounded IPC on the application thread."""
        self.source_editor.focus()
        self.query_one("#transcript", RichLog).border_title = "REPL output"
        self.query_one(CommandEditor).border_title = "REPL input"
        self._layout()
        if self.probe is not None:
            self.probe.start()
            self.set_interval(0.03, self._poll_worker)

    @property
    def source_editor(self) -> SourceEditor:
        """Return the mounted active document without rebuilding it."""
        current = self.query_one(ContentSwitcher).current
        return self.query_one(f"#{current}", SourceEditor)

    def on_resize(self) -> None:
        """Adapt cell minima while retaining all mounted document state."""
        if self.is_mounted:
            self._layout()

    def _layout(self) -> None:
        """At fewer than 100 columns, switch inspector and source views."""
        compact = self.size.width < 100
        show_state = self.inspector_requested and (
            not compact or self.has_class("state-view")
        )
        self.query_one("#inspector").display = show_state
        self.query_one("#inspector").styles.width = (
            "1fr" if compact else self.inspector_width
        )
        self.query_one("#documents-view").display = not (compact and show_state)
        self.query_one("#state-divider").display = show_state and not compact
        self.query_one("#repl").styles.height = min(
            self.repl_height, max(8, self.size.height // 2)
        )

    def action_next_document(self) -> None:
        """Switch retained editors, preserving caret, selection, scroll and undo."""
        switcher = self.query_one(ContentSwitcher)
        switcher.current = "source-2" if switcher.current == "source-1" else "source-1"
        self.remove_class("state-view")
        self._layout()
        self.source_editor.focus()

    def action_toggle_inspector(self) -> None:
        """Provide a keyboard alternative to a hidden compact inspector."""
        if self.size.width < 100:
            self.toggle_class("state-view")
            self.inspector_requested = True
        else:
            self.inspector_requested = not self.inspector_requested
        self._layout()

    def action_toggle_lock(self) -> None:
        """Offer a distinguishable fallback and clickable lock control."""
        self.query_one(CommandEditor).toggle_lock()

    @on(CommandEditor.LockChanged)
    def _lock_changed(self) -> None:
        """Keep lock visibility independent of runtime/process state."""
        locked = self.query_one(CommandEditor).locked
        self.query_one("#lock-status", Static).update(
            "Input: Enter adds a newline [locked] · F4 unlocks"
            if locked
            else "Input: Enter submits · F4 toggles multiline"
        )

    async def on_event(self, event: events.Event) -> None:
        """Record physical keys before routing, including app-bound chords."""
        if isinstance(event, events.Key) and self.is_mounted:
            self.recent_keys.append(event.key)
            self.query_one("#status", Static).update(
                "Keys: " + "  ".join(self.recent_keys)
            )
        await super().on_event(event)

    @on(TextArea.SelectionChanged)
    def _selection_changed(self, message: TextArea.SelectionChanged) -> None:
        """Display actual source coordinates without fabricating stack facts."""
        if message.text_area is self.source_editor:
            row, column = message.selection.end
            self.query_one("#inspector", Static).update(
                f"State unavailable\nSource {row + 1}:{column + 1}\n"
                "Semantic inspection arrives in Stage 3."
            )

    @on(TextArea.Changed)
    def _text_changed(self, message: TextArea.Changed) -> None:
        """Use real recoverable lexical diagnostics to exercise emphasis."""
        editor = message.text_area
        if isinstance(editor, SourceEditor) and not isinstance(editor, CommandEditor):
            _, errors = lex_with_diagnostics(editor.text)
            starts = [0]
            starts.extend(i + 1 for i, char in enumerate(editor.text) if char == "\n")
            spans = []
            for error in errors:
                if error.line is not None and error.column is not None:
                    offset = starts[error.line - 1] + error.column - 1
                    spans.append(
                        SourceEmphasis(offset, offset + 1, "underline bold red")
                    )
            editor.set_emphasis(tuple(spans))

    @on(PaneDivider.Resized)
    def _resize_pane(self, message: PaneDivider.Resized) -> None:
        """Clamp pane dimensions and retain chosen sizes across visibility changes."""
        if message.divider.vertical:
            self.inspector_width = max(
                24, min(self.size.width - 50, self.inspector_width - message.delta)
            )
        else:
            self.repl_height = max(
                8, min(self.size.height - 10, self.repl_height - message.delta)
            )
        self._layout()

    @on(Button.Pressed)
    def _button_pressed(self, message: Button.Pressed) -> None:
        """Expose key-probe actions through clickable fallback controls."""
        actions = {
            "documents": self.action_next_document,
            "state-toggle": self.action_toggle_inspector,
            "run": self.action_run_source,
            "stop": self.action_stop,
            "lock": self.action_toggle_lock,
        }
        actions[message.button.id]()

    def action_run_source(self) -> None:
        """Submit the complete draft to the existing session as a feasibility run.

        This is explicitly not transactional Alt+X loading; Stage 1 extracts
        preparation and Stage 5 connects the specified fresh-session behavior.
        """
        self._submit(self.source_editor.text)

    @on(CommandEditor.Submitted)
    def _command_submitted(self, message: CommandEditor.Submitted) -> None:
        """Route input requests separately from persistent REPL submissions."""
        if self.waiting_input and self.probe is not None:
            self.probe.provide_input(message.source)
            self.waiting_input = False
        else:
            self._submit(message.source)

    def _submit(self, source: str) -> None:
        """Reject busy workers visibly and keep the document/draft intact."""
        if self.probe is None or self.stopping:
            return
        try:
            self.probe.submit(source)
        except RuntimeError as error:
            self._append(str(error), "yellow")

    def _append(self, text: str, style: str = "") -> None:
        """Bound retained output and expose a marker instead of silent truncation."""
        if len(self.retained_output) == self.retained_output.maxlen:
            self.output_truncated = True
        self.retained_output.append(text[:2048])
        self.query_one("#transcript", RichLog).write(Text(text[:2048], style=style))
        if self.output_truncated:
            self.query_one("#status", Static).update(
                "Earlier transcript output dropped"
            )

    def _poll_worker(self) -> None:
        """Apply presentation events exclusively on Textual's application thread."""
        if self.probe is None or self.stopping:
            return
        for event in self.probe.poll():
            if event.kind == "input":
                self.waiting_input = True
                self.query_one(CommandEditor).focus()
            style = "green" if event.kind == "finished" else ""
            if event.kind in {"failed", "diagnostic"}:
                style = "red"
            if event.kind == "truncated":
                self.output_truncated = True
            self._append(event.text or event.kind, style)

    def action_stop(self) -> None:
        """Stop without blocking the application thread during process joins."""
        if not self.stopping and self.probe is not None:
            self.stopping = True
            self._replace_worker()

    @work
    async def _replace_worker(self) -> None:
        """Discard stopped runtime state while preserving mounted editor widgets."""
        assert self.probe is not None
        disposition = await asyncio.to_thread(self.probe.close)
        self._append(f"Stop: {disposition}; runtime state discarded")
        self.probe = SessionProbe()
        self.probe.start()
        self.waiting_input = False
        self.stopping = False

    async def on_unmount(self) -> None:
        """Reap the owned process even when quitting while a program runs."""
        try:
            await self.workers.wait_for_complete()
        finally:
            if self.probe is not None:
                await asyncio.to_thread(self.probe.close)
