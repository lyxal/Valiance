"""Connect editor intents to an isolated, persistent session process."""

from __future__ import annotations

import asyncio
from time import monotonic

from rich.text import Text
from textual import events
from textual.document._document import Selection
from textual.message import Message
from textual.widgets import TextArea

from valiance.incremental.database import CompilationDatabase
from valiance.sessions.worker import SessionWorker

from .editing import DocumentEditor
from .models import DocumentView, InputDraft, InputState, LoadedSource, TranscriptEntry
from .transcript import TranscriptView


class CommandInput(DocumentEditor):
    """Share source editing with contextual submission, Enter lock and history."""

    def __init__(self, state: InputState, **kwargs) -> None:
        """Keep input preferences independent of runtime and document state."""
        self.input_state = state
        self.program_input = False
        super().__init__(**kwargs)
        self.show_line_numbers = False

    def update_guidance(self) -> None:
        """Expose the current Enter behavior even when modified keys alias Enter."""
        behavior = (
            "Enter sends program input"
            if self.program_input
            else "Enter: newline [locked]"
            if self.input_state.locked
            else "Enter: submit"
        )
        self.border_title = Text(f"REPL input · {behavior}")

    def toggle_lock(self) -> None:
        """Change the input preference without editing or submitting the draft."""
        self.input_state.locked = not self.input_state.locked
        self.update_guidance()

    def draft(self) -> InputDraft:
        """Capture source, selection and scroll before entering history."""
        return InputDraft(
            self.text,
            DocumentView(
                self.selection.end,
                self.selection.start,
                (int(self.scroll_x), int(self.scroll_y)),
            ),
        )

    def _recall(self, previous: bool) -> None:
        """Recall without execution and restore the complete unfinished draft."""
        current = self.draft()
        recalled = (
            self.input_state.previous(current)
            if previous
            else self.input_state.next(current)
        )
        if recalled == current:
            return
        self.load_text(recalled.source)
        if self.input_state._index is None:
            self.selection = Selection(
                recalled.view.selection_start, recalled.view.cursor
            )
            self.call_after_refresh(
                self.scroll_to, *recalled.view.scroll, animate=False
            )
        else:
            self.move_cursor(self.document.end if previous else (0, 0))

    def action_cursor_up(self, select: bool = False) -> None:
        """Access history only from the first input line while unlocked."""
        if (
            not select
            and not self.program_input
            and not self.input_state.locked
            and self.cursor_location[0] == 0
        ):
            self._recall(True)
        else:
            super().action_cursor_up(select)

    def action_cursor_down(self, select: bool = False) -> None:
        """Restore the draft past newest history only from the last input line."""
        if (
            not select
            and not self.program_input
            and not self.input_state.locked
            and self.cursor_location[0] == self.document.line_count - 1
        ):
            self._recall(False)
        else:
            super().action_cursor_down(select)

    class Submitted(Message):
        """Signal submission without exposing program stdin to a widget."""

    async def _on_key(self, event: events.Key) -> None:
        """Keep command submission separate from the native newline action."""
        if hasattr(self.app, "completion") and self.app.completion.handle_key(event):
            return
        if event.key in {"ctrl+enter", "shift+enter"}:
            event.stop()
            event.prevent_default()
            self.toggle_lock()
        elif event.key == "enter" and (
            self.program_input or not self.input_state.locked
        ):
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted())
        else:
            await super()._on_key(event)


def _capture(documents, root, revision):
    """Discover imports using an isolated database and frozen editor sources."""
    database = CompilationDatabase()
    for document in documents:
        if document.path is not None:
            database.open_document(
                document.path, document.source, version=document.revision
            )
    return database.capture_workspace(root, workspace_revision=revision)


class EditorRuntime:
    """Own process lifecycle while all document and widget mutations stay on UI."""

    def __init__(self, app) -> None:
        """Initialize transport without executing restored or blank source."""
        self.app = app
        self.worker = SessionWorker()
        self.label = "Starting session"
        self.pending = None
        self.preparing = False
        self.closing = False
        self.exiting = False
        self.waiting_input = False
        self.generation = 0
        self.command_source = None
        self._polling = False
        self._faulted = False

    def start(self) -> None:
        """Spawn once and poll bounded transport batches on the UI timer."""
        self.worker.start()

    def write(self, kind: str, text: str) -> None:
        """Retain bounded transcript entries and preserve streamed line breaks."""
        self.app.state.transcript.append(TranscriptEntry(kind, text))
        if not self._polling:
            self.refresh_transcript()

    def refresh_transcript(self) -> None:
        """Keep displayed selection/export synchronized with the retained model."""
        self.app.editor_screen.query_one(
            "#transcript", TranscriptView
        ).refresh_transcript(self.app.state.transcript)

    async def run_file(self) -> None:
        """Capture and prepare active source; stale preparations never execute."""
        if self.preparing or self.worker.busy or self.closing:
            self.app.notify("Stop the current execution before running another file")
            return
        self.preparing = True
        generation = self.generation
        self.label = "Preparing file"
        self.app._status()
        try:
            workspace = self.app.workspace
            workspace.update(workspace.active_document, self.app.source_editor.text)
            document = workspace.documents[workspace.active_document]
            snapshot = await asyncio.to_thread(
                _capture,
                tuple(workspace.documents.values()),
                document.root_source,
                workspace.revision,
            )
            deadline = monotonic() + 10
            while not self.worker.ready and generation == self.generation:
                if monotonic() >= deadline:
                    raise RuntimeError("Session worker did not start")
                await asyncio.sleep(0.05)
            if generation != self.generation:
                return
            if snapshot.workspace_revision != workspace.revision:
                self.write("notice", "Source changed during preparation; run again\n")
                return
            self.pending = snapshot
            self.app.completion.invalidate_session()
            self.worker.submit(snapshot=snapshot)
        except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
            self.app.notify(str(exc), title="Run failed", severity="error")
        finally:
            self.preparing = False
            if not self.worker.busy and not self.closing:
                self.label = "Ready"
            self.app._status()

    def submit_command(self) -> None:
        """Send program input while waiting, otherwise submit a persistent command."""
        editor = self.app.query_one("#command", TextArea)
        source = editor.text
        if self.waiting_input:
            self.worker.provide_input(source)
            self.write("input", source + "\n")
            self.waiting_input = False
            editor.program_input = False
            editor.update_guidance()
            self.label = "Running"
            editor.clear()
        elif (
            self.preparing or self.closing or not self.worker.ready or self.worker.busy
        ):
            self.app.notify("Session is busy; Stop before submitting another command")
            return
        elif not source.strip():
            return
        else:
            action = {
                ":clear": self.app.action_clear_transcript,
                ":c": self.app.action_clear_transcript,
                ":reset": self.app.action_reset_session,
                ":r": self.app.action_reset_session,
                ":quit": self.app.action_quit_editor,
                ":q": self.app.action_quit_editor,
                ":repl": self.app.reveal_repl,
                ":scratch": self.app.focus_source,
            }.get(source.strip())
            if action is not None:
                self.app.state.input.record(source)
                self.app.persist_history()
                editor.clear()
                action()
                return
            self.write("command", f"> {source}\n")
            self.app.completion.invalidate_session()
            self.worker.submit(source)
            self.command_source = source
            self.app.state.input.record(source)
            self.app.persist_history()
            self.label = "Running command"
        self.app._status()

    def poll(self) -> None:
        """Apply correlated lifecycle events without blocking rendering on output."""
        if self.closing:
            return
        batch = self.worker.poll()
        # stdout uses a separate bounded queue; drain it before completion labels.
        batch.sort(key=lambda item: item.kind in {"finished", "failed", "cancelled"})
        self._polling = True
        for event in batch:
            if event.kind == "completion":
                self.app.completion.receive(event)
            elif event.kind == "ready":
                self.label = "Ready"
            elif event.kind == "prepared":
                current = bool(
                    self.pending
                    and self.pending.workspace_revision == self.app.workspace.revision
                )
                self.worker.commit(event.request, current)
            elif event.kind == "loaded":
                snapshot = self.pending
                self.app.state.loaded_source = LoadedSource(
                    snapshot.root.document_id,
                    snapshot.root.revision,
                    snapshot.root.path,
                )
                self.write("load", f"— Run {snapshot.root.path or 'Untitled'} —\n")
                self.label = "Running file"
                self.app.editor_screen.query_one("#load-errors").display = False
                self.app.reveal_repl()
            elif event.kind == "accepted":
                editor = self.app.editor_screen.query_one("#command", CommandInput)
                if editor.text == self.command_source:
                    editor.clear()
            elif event.kind == "input":
                self.waiting_input = True
                self.label = "Waiting for program input"
                editor = self.app.editor_screen.query_one("#command", CommandInput)
                editor.program_input = True
                editor.update_guidance()
                self.app.reveal_repl()
            elif event.kind in {"output", "diagnostic", "truncated", "notice"}:
                self.write(
                    event.kind, event.text + ("\n" if event.kind == "truncated" else "")
                )
            elif event.kind in {"closed", "interrupted"}:
                self.app.state.loaded_source = None
                self.label = "Session worker exited"
                self.app.action_stop_execution()
            elif event.kind == "reset":
                self.app.state.loaded_source = None
                self._faulted = True
            elif event.kind == "blocked":
                self.app.show_blocked_load()
            elif event.kind in {"finished", "failed", "cancelled"}:
                if self._faulted:
                    self.write("notice", "Runtime fault; session reset\n")
                    self._faulted = False
                    self.worker.ready = False
                    self.app.recover_runtime_fault()
                if event.text:
                    self.write(
                        "result" if event.kind == "finished" else "notice",
                        event.text + "\n",
                    )
                self.pending = None
                self.command_source = None
                self.waiting_input = False
                editor = self.app.editor_screen.query_one("#command", CommandInput)
                editor.program_input = False
                editor.update_guidance()
                self.label = (
                    "Ready"
                    if event.kind == "finished"
                    else "Run failed"
                    if event.kind == "failed"
                    else "Run cancelled"
                )
        if batch:
            self.refresh_transcript()
            self.app._status()
        self._polling = False

    async def stop(self, *, reset: bool = False, fault: bool = False) -> None:
        """Reap the worker off the UI loop and replace its discarded session."""
        if self.closing:
            return
        self.closing = True
        self.app.completion.invalidate_session()
        self.generation += 1
        self.label = "Run failed" if fault else "Stopping"
        self.app._status()
        try:
            disposition = await asyncio.to_thread(self.worker.close)
            for event in self.worker.final_output:
                self.app.state.transcript.append(
                    TranscriptEntry(
                        event.kind,
                        event.text + ("\n" if event.kind == "truncated" else ""),
                    )
                )
            self.pending = None
            self.waiting_input = False
            self.app.state.loaded_source = None
            self.command_source = None
            self._faulted = False
            editor = self.app.editor_screen.query_one("#command", CommandInput)
            editor.program_input = False
            editor.update_guidance()
            if reset:
                self.app.state.transcript.clear()
                self.refresh_transcript()
            elif not fault:
                self.write("notice", f"Stopped; session reset ({disposition})\n")
            if self.exiting:
                return
            self.label = "Run failed" if fault else "Starting session"
            self.worker = SessionWorker()
            self.worker.start()
        finally:
            self.closing = False
            if not self.exiting:
                self.app._status()

    async def close(self) -> None:
        """Ensure normal quit and test teardown leave no session process behind."""
        self.closing = True
        self.exiting = True
        self.generation += 1
        await asyncio.to_thread(self.worker.close)
