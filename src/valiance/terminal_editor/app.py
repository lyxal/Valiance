"""Full-screen document editor with protected file workflows and retained widgets."""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.document._document import Selection
from textual.geometry import Size
from textual.message import Message
from textual.widgets import (
    Button,
    ContentSwitcher,
    Input,
    RichLog,
    Static,
    Tab,
    Tabs,
    TextArea,
)

from .dialogs import ChoiceDialog, MenuDialog, PathDialog
from .divider import PaneDivider
from .editing import DocumentEditor
from .files import DiskConflict, DocumentFiles, EditorPreferences, ExternalChange
from .models import DocumentView, EditorState, EditorWorkspace
from .runtime import CommandInput, EditorRuntime


class InspectorPane(Static, can_focus=True):
    """Keep the unavailable inspector reachable by keyboard during editor stages."""


class EditorApp(App):
    """Own presentation and file actions; execute source through an isolated worker."""

    CSS_PATH = "editor.tcss"
    BINDINGS = [
        Binding("ctrl+n", "file_action('new')", "New", priority=True),
        Binding("ctrl+o", "file_action('open')", "Open", priority=True),
        Binding("ctrl+s", "file_action('save')", "Save", priority=True),
        Binding("ctrl+shift+s", "file_action('save-as')", "Save As", priority=True),
        Binding("ctrl+w", "file_action('close')", "Close", priority=True),
        Binding("ctrl+q", "file_action('quit')", "Quit", priority=True),
        Binding("ctrl+pageup", "previous_tab", "Previous tab", priority=True),
        Binding("ctrl+pagedown", "next_tab", "Next tab", priority=True),
        Binding("ctrl+f", "search(False)", "Find", priority=True),
        # Ctrl+H and Ctrl+[ can alias Backspace/Escape; menus/F-keys are safe defaults.
        Binding("f2", "file_action('menu')", "File menu", priority=True),
        Binding("f3", "toggle_inspector", "State", priority=True),
        Binding("f4", "toggle_repl", "REPL", priority=True),
        Binding("f5", "run_file", "Run file", priority=True),
        Binding("alt+x", "run_file", "Run file", priority=True),
        Binding("f8", "stop_execution", "Stop", priority=True),
        Binding("f6", "cycle_panes", "Next pane", priority=True),
        Binding("f7", "search(True)", "Replace", priority=True),
        Binding("ctrl+c", "copy_selection", "Copy", priority=True),
        Binding("escape", "dismiss_search", "Close search"),
    ]

    class FilesChanged(Message):
        """Deliver file-watcher results without mutating widgets in a thread."""

        def __init__(self, changes: tuple[ExternalChange, ...]) -> None:
            """Carry immutable observations back to the application thread."""
            super().__init__()
            self.changes = changes

    def __init__(
        self,
        *,
        launch_directory: Path | None = None,
        config_path: Path | None = None,
        watch_files: bool = True,
        with_worker: bool = True,
    ) -> None:
        """Start one blank document; preferences never reopen files or run source."""
        super().__init__()
        self.state = EditorState(EditorWorkspace(launch_directory or Path.cwd()))
        self.files = DocumentFiles(self.state.workspace)
        self.config_path = config_path
        self.preferences = EditorPreferences.load(config_path)
        self.watch_files = watch_files
        self.runtime = EditorRuntime(self) if with_worker else None
        self._watching = False
        self.inspector_requested = True
        self.repl_requested = True
        self.repl_only = False
        self.compact_view = "editor"
        self._file_busy = False
        self._pending_changes: dict[str, ExternalChange] = {}
        self._dismissed_changes: dict[str, bytes | None] = {}
        self._search_document: str | None = None
        self._was_compact = False

    @property
    def workspace(self) -> EditorWorkspace:
        """Expose document ownership independently of mounted controls."""
        return self.state.workspace

    @property
    def source_editor(self) -> DocumentEditor:
        """Return the active mounted editor without reconstructing its undo history."""
        return self.query_one(
            f"#source-{self.workspace.active_document}", DocumentEditor
        )

    def _editor(self, document_id: str) -> DocumentEditor:
        """Configure a single editor for its entire mounted document lifetime."""
        editor = DocumentEditor(
            self.workspace.documents[document_id].source, id=f"source-{document_id}"
        )
        editor.soft_wrap = self.preferences.soft_wrap
        editor.indent_width = self.preferences.indent_width
        return editor

    def compose(self) -> ComposeResult:
        """Mount file controls, source/state panes, a separate REPL and status row."""
        with Horizontal(id="file-strip"):
            yield Button("File (F2)", id="file-menu", compact=True)
            yield Tabs(
                Tab("Untitled", id=f"tab-{self.workspace.active_document}"), id="tabs"
            )
            yield Button("Edit", id="edit-menu", compact=True)
            yield Button("View", id="view-menu", compact=True)
            yield Button("Run (F5)", id="run-file", compact=True)
            yield Button("Stop (F8)", id="stop-execution", compact=True)
        with Horizontal(id="search-bar"):
            yield Input(placeholder="Find text", id="find-text")
            yield Input(placeholder="Replace with", id="replace-text")
            yield Button("Next", id="find-next")
            yield Button("Replace", id="replace-one")
            yield Button("All", id="replace-all")
            yield Button("×", id="search-close")
        with Horizontal(id="working-area"):
            with ContentSwitcher(
                initial=f"source-{self.workspace.active_document}", id="documents-view"
            ):
                yield self._editor(self.workspace.active_document)
            yield PaneDivider(vertical=True, id="state-divider")
            yield InspectorPane("State unavailable", id="inspector")
        yield PaneDivider(vertical=False, id="repl-divider")
        with Vertical(id="repl"):
            yield RichLog(max_lines=300, wrap=True, markup=False, id="transcript")
            yield CommandInput(id="command", soft_wrap=True, tab_behavior="indent")
        yield Static(id="status", markup=False)

    def on_mount(self) -> None:
        """Set section labels and start a non-executing disk watcher."""
        self.query_one("#search-bar").display = False
        self.query_one("#transcript", RichLog).border_title = "REPL output"
        self.query_one(
            "#command", TextArea
        ).border_title = "REPL input · Enter submits · Shift+Enter newline"
        self.query_one(
            "#command", TextArea
        ).indent_width = self.preferences.indent_width
        self._layout()
        self._labels()
        self.source_editor.focus()
        self._status()
        if self.runtime:
            self.runtime.start()
            self.set_interval(0.05, self.runtime.poll)
        if self.watch_files:
            self.set_interval(1, self._poll_files)

    def on_resize(self, event: events.Resize) -> None:
        """Switch narrow layouts without unmounting document or REPL contents."""
        if self.is_mounted:
            compact = event.size.width < 100 or event.size.height < 30
            if compact and not self._was_compact:
                self.compact_view = "editor"
            self._was_compact = compact
            self._layout(event.size)
            if self.focused is not None and any(
                not item.display for item in self.focused.ancestors
            ):
                (
                    self.query_one("#command") if self.repl_only else self.source_editor
                ).focus()

    def _layout(self, size: Size | None = None) -> None:
        """Keep cell minima at small sizes and remember requested pane dimensions."""
        width, height = size or self.size
        compact = width < 100 or height < 30
        state = self.inspector_requested and not self.repl_only
        repl = self.repl_requested or self.repl_only
        show_source = not self.repl_only
        if compact and not self.repl_only:
            show_source = self.compact_view == "editor"
            state = state and self.compact_view == "state"
            repl = repl and self.compact_view == "repl"
        self.query_one("#working-area").display = show_source or state
        self.query_one("#documents-view").display = show_source
        self.query_one("#inspector").display = state
        self.query_one("#inspector").styles.width = (
            "1fr"
            if compact
            else min(self.preferences.inspector_width, max(24, width - 45))
        )
        self.query_one("#state-divider").display = state and show_source and not compact
        self.query_one("#repl").display = repl
        self.query_one("#repl").styles.height = (
            "1fr"
            if compact or self.repl_only
            else min(self.preferences.repl_height, max(8, height - 12))
        )
        self.query_one("#repl-divider").display = repl and show_source and not compact
        self.query_one("#search-bar").display = (
            self._search_document is not None and show_source
        )

    def _status(self) -> None:
        """Show source coordinates, saved state and an honest analysis/session label."""
        document = self.workspace.documents[self.workspace.active_document]
        row, column = self.source_editor.cursor_location
        name = str(document.path) if document.path else document.suggested_name
        state = "unsaved" if document.dirty else "saved" if document.path else "new"
        self.query_one("#status", Static).update(
            f"{name} · Ln {row + 1}, Col {column + 1} · {state} · "
            f"Errors: unavailable · {self._session_label()}"
        )

    def _session_label(self) -> str:
        """Identify the loaded revision separately from the active editor tab."""
        if self.runtime is None:
            return "No session loaded"
        loaded = self.state.loaded_source
        if loaded is None:
            return self.runtime.label + " · No file loaded"
        document = self.workspace.documents.get(loaded.document_id)
        name = (
            str(loaded.path)
            if loaded.path
            else document.suggested_name
            if document
            else "Closed document"
        )
        stale = (
            " · source changed"
            if document is None or document.revision != loaded.revision
            else ""
        )
        return f"{self.runtime.label} · Loaded {name}{stale}"

    def action_run_file(self) -> None:
        """Execute the active document through the session worker."""
        if self.runtime is not None and len(self.screen_stack) == 1:
            self._run_file()

    @work(group="execution")
    async def _run_file(self) -> None:
        """Keep import capture and process startup off the UI event loop."""
        await self.runtime.run_file()

    def action_stop_execution(self) -> None:
        """Request bounded interruption without blocking the editor."""
        if self.runtime is not None:
            self._stop_execution()

    @work(group="stop-execution")
    async def _stop_execution(self) -> None:
        """Wait for process cleanup independently of rendering."""
        await self.runtime.stop()

    @on(CommandInput.Submitted)
    def command_submitted(self) -> None:
        """Route REPL submission or requested program input to the session."""
        if self.runtime is not None:
            self.runtime.submit_command()

    async def on_unmount(self) -> None:
        """Reap the session process on every application exit."""
        if self.runtime is not None:
            await self.runtime.close()

    @on(TextArea.Changed)
    def source_changed(self, message: TextArea.Changed) -> None:
        """Feed actual document changes into workspace overlays; never execute them."""
        editor = message.text_area
        if isinstance(editor, DocumentEditor):
            document_id = editor.id.removeprefix("source-")
            if document_id in self.workspace.documents:
                self.state.edit_document(document_id, editor.text)
                self._labels()
                self._status()

    @on(TextArea.SelectionChanged)
    def selection_changed(self, message: TextArea.SelectionChanged) -> None:
        """Retain source caret and selection alongside native widget state."""
        editor = message.text_area
        if isinstance(editor, DocumentEditor):
            document_id = editor.id.removeprefix("source-")
            if document_id in self.workspace.documents:
                self.workspace.update(
                    document_id,
                    editor.text,
                    view=DocumentView(
                        editor.selection.end,
                        editor.selection.start,
                        (int(editor.scroll_x), int(editor.scroll_y)),
                    ),
                )
                self._status()

    def _labels(self) -> None:
        """Disambiguate matching basenames and mark dirty tabs."""
        documents = tuple(self.workspace.documents.values())
        for document in documents:
            name = document.path.name if document.path else document.suggested_name
            if (
                document.path
                and sum(
                    item.path is not None and item.path.name == name
                    for item in documents
                )
                > 1
            ):
                name = str(document.path)
            self.query_one(f"#tab-{document.id}", Tab).label = name + (
                " •" if document.dirty else ""
            )

    async def _mount_document(self, document_id: str) -> None:
        """Mount each editor once and activate canonical duplicates."""
        selector = f"#source-{document_id}"
        if not self.query(selector):
            await self.query_one("#documents-view", ContentSwitcher).mount(
                self._editor(document_id)
            )
            await self.query_one("#tabs", Tabs).add_tab(
                Tab("Untitled", id=f"tab-{document_id}")
            )
        self._activate(document_id)
        self._labels()

    def _activate(self, document_id: str) -> None:
        """Switch tabs without reloading text, dropping undo or changing a session."""
        if document_id not in self.workspace.documents:
            return
        self.workspace.active_document = document_id
        self.query_one(
            "#documents-view", ContentSwitcher
        ).current = f"source-{document_id}"
        self.query_one("#tabs", Tabs).active = f"tab-{document_id}"
        self.compact_view = "editor"
        self.repl_only = False
        self._search_document = None
        self._layout()
        self.source_editor.focus()
        self._status()

    @on(Tabs.TabActivated)
    def tab_activated(self, message: Tabs.TabActivated) -> None:
        """Use tab identity rather than labels for mouse and keyboard activation."""
        document_id = message.tab.id.removeprefix("tab-")
        if document_id != self.workspace.active_document:
            self._activate(document_id)

    def _cycle_tab(self, delta: int) -> None:
        """Wrap tab traversal in retained document order."""
        if len(self.screen_stack) > 1:
            return
        identities = list(self.workspace.documents)
        index = identities.index(self.workspace.active_document)
        self._activate(identities[(index + delta) % len(identities)])

    def action_next_tab(self) -> None:
        """Activate the next retained document."""
        self._cycle_tab(1)

    def action_previous_tab(self) -> None:
        """Activate the previous retained document."""
        self._cycle_tab(-1)

    def action_file_action(self, action: str) -> None:
        """Serialize file dialogs so hotkeys cannot cancel an in-progress save flow."""
        if not self._file_busy and len(self.screen_stack) == 1:
            self._file_busy = True
            self._file_action(action)

    @work(group="file-actions")
    async def _file_action(self, action: str) -> None:
        """Run reversible dialogs in a worker, reporting I/O errors without closing."""
        try:
            await self._perform(action)
        except (OSError, UnicodeError, ValueError) as exc:
            self.notify(str(exc), title="File operation failed", severity="error")
        finally:
            self._file_busy = False
            if self.query(Tab):
                self._labels()
                self._status()
                if (
                    not self.repl_only
                    and self.compact_view == "editor"
                    and self._search_document is None
                ):
                    self.source_editor.focus()

    async def _perform(self, action: str) -> None:
        """Dispatch file/menu actions with explicit save and discard decisions."""
        document_id = self.workspace.active_document
        self.state.edit_document(document_id, self.source_editor.text)
        document = self.workspace.documents[document_id]
        base = (
            document.path.parent if document.path else self.workspace.launch_directory
        )
        if action == "menu":
            choice = await self.push_screen_wait(
                MenuDialog(
                    "File",
                    [
                        ("New — Ctrl+N", "new"),
                        ("Open — Ctrl+O", "open"),
                        ("Open Recent", "recent"),
                        ("Save — Ctrl+S", "save"),
                        ("Save As — Ctrl+Shift+S", "save-as"),
                        ("Rename", "rename"),
                        ("Reload", "reload"),
                        ("Close — Ctrl+W", "close"),
                        ("Quit — Ctrl+Q", "quit"),
                    ],
                    anchor=self.query_one("#file-menu", Button),
                )
            )
            if choice:
                await self._perform(choice)
        elif action == "new":
            await self._mount_document(self.workspace.new_document().id)
        elif action in {"open", "recent"}:
            if action == "recent":
                paths = self.preferences.recent
                choice = await self.push_screen_wait(
                    ChoiceDialog(
                        "Open Recent",
                        [(path, f"recent-{index}") for index, path in enumerate(paths)]
                        or [("No recent files", "cancel")],
                    )
                )
                path = (
                    Path(paths[int(choice.removeprefix("recent-"))])
                    if choice and choice != "cancel"
                    else None
                )
            else:
                path = await self.push_screen_wait(PathDialog("Open", base))
            if path:
                existing = next(
                    (
                        item
                        for item in self.workspace.documents.values()
                        if item.path == path
                    ),
                    None,
                )
                if existing:
                    opened = existing
                else:
                    data = await asyncio.to_thread(path.read_bytes)
                    opened = self.files.accept_open(path, data)
                self.preferences.remember(path)
                await self._mount_document(opened.id)
        elif action in {"save", "save-as", "rename"}:
            await self._save(document_id, action)
        elif action == "reload":
            await self._reload(document_id)
        elif action == "close":
            if await self._protect(document_id):
                self.workspace.close_document(document_id)
                self.files.observed.pop(document_id, None)
                await self.query_one(f"#source-{document_id}").remove()
                await self.query_one("#tabs", Tabs).remove_tab(f"tab-{document_id}")
                await self._mount_document(self.workspace.active_document)
        elif action == "quit":
            for identity in tuple(self.workspace.documents):
                if not await self._protect(identity):
                    return
            await asyncio.to_thread(self.preferences.save, self.config_path)
            self.exit()
        elif action in {"view", "edit"}:
            choice = await self.push_screen_wait(
                MenuDialog(
                    "View" if action == "view" else "Edit",
                    [
                        (f"{'✓' if self.repl_requested else '○'} REPL — F4", "repl"),
                        (
                            f"{'✓' if self.inspector_requested else '○'} State — F3",
                            "state",
                        ),
                        ("REPL only / return to editor", "repl-only"),
                        ("Toggle wrapping", "wrap"),
                    ]
                    if action == "view"
                    else [
                        ("Find — Ctrl+F", "find"),
                        ("Replace — F7", "replace"),
                        ("Indent — F10", "indent"),
                        ("Dedent — Shift+F10", "dedent"),
                        ("Indent width", "indent-width"),
                        ("Copy", "copy"),
                        ("Cut", "cut"),
                        ("Paste", "paste"),
                        ("Select all", "select-all"),
                        ("Undo", "undo"),
                        ("Redo", "redo"),
                    ],
                    anchor=self.query_one(
                        "#view-menu" if action == "view" else "#edit-menu", Button
                    ),
                )
            )
            if choice == "indent-width":
                width = await self.push_screen_wait(
                    ChoiceDialog(
                        "Indent width",
                        [
                            (f"{value} spaces", f"width-{value}")
                            for value in range(1, 9)
                        ],
                    )
                )
                if width:
                    self.preferences.indent_width = int(width.removeprefix("width-"))
                    for editor in self.query(DocumentEditor):
                        editor.indent_width = self.preferences.indent_width
            elif choice:
                self._view_action(choice)

    async def _save(self, document_id: str, action: str = "save") -> bool:
        """Commit disk and overlays on success; cancellation keeps tabs."""
        self.state.edit_document(
            document_id, self.query_one(f"#source-{document_id}", DocumentEditor).text
        )
        document = self.workspace.documents[document_id]
        path = document.path
        if path is None or action != "save":
            base = path.parent if path else self.workspace.launch_directory
            path = await self.push_screen_wait(
                PathDialog(
                    "Rename" if action == "rename" else "Save As",
                    base,
                    path.name if path else "Untitled.vlnc",
                )
            )
            if path is None:
                return False
        overwrite = False
        if path != document.path and path.exists():
            overwrite = (
                await self.push_screen_wait(
                    ChoiceDialog(
                        f"Replace existing file {path}?",
                        [("Replace", "replace"), ("Cancel", "cancel")],
                    )
                )
                == "replace"
            )
            if not overwrite:
                return False
        operation = self.files.prepare(
            document_id, path, overwrite=overwrite, rename=action == "rename"
        )
        try:
            await asyncio.to_thread(operation.perform)
            self.state.edit_document(
                document_id,
                self.query_one(f"#source-{document_id}", DocumentEditor).text,
            )
            self.files.commit(operation)
        except DiskConflict:
            changes = await asyncio.to_thread(self.files.external_changes)
            change = next(
                (item for item in changes if item.document_id == document_id), None
            )
            if change is not None:
                await self._external_choice(change)
            return False
        self.preferences.remember(path)
        self._labels()
        self._status()
        return not self.workspace.documents[document_id].dirty

    async def _protect(self, document_id: str) -> bool:
        """Save/Discard/Cancel each dirty document without losing a cancelled quit."""
        self.state.edit_document(
            document_id, self.query_one(f"#source-{document_id}", DocumentEditor).text
        )
        document = self.workspace.documents[document_id]
        if not document.dirty:
            return True
        choice = await self.push_screen_wait(
            ChoiceDialog(
                f"Unsaved changes: {document.path or document.suggested_name}",
                [("Save", "save"), ("Discard", "discard"), ("Cancel", "cancel")],
            )
        )
        return choice == "discard" or (
            choice == "save" and await self._save(document_id)
        )

    async def _reload(self, document_id: str) -> bool:
        """Require explicit protection before replacing an unsaved editor buffer."""
        document = self.workspace.documents[document_id]
        if document.path is None:
            return False
        if document.dirty:
            choice = await self.push_screen_wait(
                ChoiceDialog(
                    "Discard unsaved edits and reload the saved file?",
                    [("Reload", "reload"), ("Cancel", "cancel")],
                )
            )
            if choice != "reload":
                return False
        requested_revision = self.workspace.documents[document_id].revision
        data = await asyncio.to_thread(document.path.read_bytes)
        self.state.edit_document(
            document_id, self.query_one(f"#source-{document_id}", DocumentEditor).text
        )
        if self.workspace.documents[document_id].revision != requested_revision:
            self.notify("Document changed while reading the file. Reload again.")
            return False
        document = self.files.accept_reload(document_id, data)
        editor = self.query_one(f"#source-{document_id}", DocumentEditor)
        selection = editor.selection
        editor.load_text(document.source)
        editor.selection = selection
        self.state.inspector = None
        return True

    @work(group="file-watcher")
    async def _poll_files(self) -> None:
        """Coalesce disk reads off the UI loop and post immutable results."""
        if self._watching:
            return
        self._watching = True
        try:
            changes = await asyncio.to_thread(self.files.external_changes)
            self.post_message(self.FilesChanged(changes))
        finally:
            self._watching = False

    @on(FilesChanged)
    def files_changed(self, message: FilesChanged) -> None:
        """Queue changes until current dialogs finish, avoiding automatic reloads."""
        for change in message.changes:
            document = self.workspace.documents.get(change.document_id)
            if (
                document
                and document.path == change.path
                and not (
                    change.document_id in self._dismissed_changes
                    and self._dismissed_changes[change.document_id] == change.data
                )
            ):
                self._pending_changes[change.document_id] = change
        if (
            self._pending_changes
            and not self._file_busy
            and len(self.screen_stack) == 1
        ):
            self._file_busy = True
            self._offer_changes()

    @work(group="file-actions")
    async def _offer_changes(self) -> None:
        """Serialize external-change prompts with all other file protection dialogs."""
        try:
            while self._pending_changes:
                _, change = self._pending_changes.popitem()
                document = self.workspace.documents.get(change.document_id)
                if (
                    document
                    and document.path == change.path
                    and self.files.observed.get(document.id) != change.data
                ):
                    await self._external_choice(change)
        except (OSError, UnicodeError, ValueError) as exc:
            self.notify(str(exc), severity="error")
        finally:
            self._file_busy = False
            self._labels()
            self._status()

    async def _external_choice(self, change: ExternalChange) -> None:
        """Always offer Reload/Keep, including clean buffers and deleted disk files."""
        choice = await self.push_screen_wait(
            ChoiceDialog(
                f"File changed outside the editor: {change.path}",
                [("Reload", "reload"), ("Keep editor text", "keep")],
            )
        )
        if choice == "reload":
            await self._reload(change.document_id)
        elif choice == "keep":
            self.files.keep(change)
        else:
            self._dismissed_changes[change.document_id] = change.data

    @on(Button.Pressed)
    def button_pressed(self, message: Button.Pressed) -> None:
        """Keep toolbar/search controls accessible when terminal chords are aliased."""
        identity = message.button.id
        if identity in {"file-menu", "view-menu", "edit-menu"}:
            self.action_file_action(
                {"file-menu": "menu", "view-menu": "view", "edit-menu": "edit"}[
                    identity
                ]
            )
        elif identity == "run-file":
            self.action_run_file()
        elif identity == "stop-execution":
            self.action_stop_execution()
        elif identity == "search-close":
            self.action_dismiss_search()
        elif identity == "find-next":
            self.find_next()
        elif identity in {"replace-one", "replace-all"}:
            self.replace_matches(identity == "replace-all")

    def _view_action(self, action: str) -> None:
        """Apply menu choices through the same native editing and layout actions."""
        if action == "repl":
            self.action_toggle_repl()
        elif action == "state":
            self.action_toggle_inspector()
        elif action == "repl-only":
            self.repl_only = not self.repl_only
            self._layout()
            (
                self.query_one("#command") if self.repl_only else self.source_editor
            ).focus()
        elif action == "wrap":
            self.preferences.soft_wrap = not self.preferences.soft_wrap
            for editor in self.query(DocumentEditor):
                editor.soft_wrap = self.preferences.soft_wrap
        elif action in {"find", "replace"}:
            self.action_search(action == "replace")
        elif action in {"indent", "dedent"}:
            getattr(self.source_editor, f"action_{action}_lines")()
        else:
            getattr(self.source_editor, f"action_{action.replace('-', '_')}")()

    def action_toggle_inspector(self) -> None:
        """Reveal a compact State view or toggle a wide inspector."""
        if self.size.width < 100 or self.size.height < 30:
            self.compact_view = "editor" if self.compact_view == "state" else "state"
            self.inspector_requested = True
        else:
            self.inspector_requested = not self.inspector_requested
        self._layout()
        (
            self.query_one("#inspector")
            if self.query_one("#inspector").display
            else self.source_editor
        ).focus()

    def action_toggle_repl(self) -> None:
        """Reveal a compact REPL view or toggle it without dropping pane sizes."""
        if self.size.width < 100 or self.size.height < 30:
            self.compact_view = "editor" if self.compact_view == "repl" else "repl"
            self.repl_requested = True
        else:
            self.repl_requested = not self.repl_requested
        self._layout()
        (
            self.query_one("#command")
            if self.query_one("#repl").display
            else self.source_editor
        ).focus()

    def action_cycle_panes(self) -> None:
        """Cycle accessible visible panes; compact mode reveals each requested view."""
        if len(self.screen_stack) > 1:
            return
        if self.size.width < 100 or self.size.height < 30:
            views = (
                ["editor"]
                + (["state"] if self.inspector_requested else [])
                + (["repl"] if self.repl_requested else [])
            )
            self.compact_view = views[(views.index(self.compact_view) + 1) % len(views)]
            self.repl_only = False
            self._layout()
            target = (
                self.source_editor
                if self.compact_view == "editor"
                else self.query_one(
                    "#inspector" if self.compact_view == "state" else "#command"
                )
            )
            target.focus()
        else:
            panes = [
                item
                for item in (
                    self.source_editor,
                    self.query_one("#inspector"),
                    self.query_one("#transcript"),
                    self.query_one("#command"),
                )
                if item.display
                and item.visible
                and all(parent.display for parent in item.ancestors)
            ]
            index = panes.index(self.focused) if self.focused in panes else -1
            panes[(index + 1) % len(panes)].focus()

    @on(PaneDivider.Resized)
    def resize_pane(self, message: PaneDivider.Resized) -> None:
        """Clamp keyboard/mouse resize requests without forgetting hidden pane sizes."""
        if message.divider.vertical:
            self.preferences.inspector_width = max(
                24,
                min(
                    self.size.width - 45,
                    self.preferences.inspector_width - message.delta,
                ),
            )
        else:
            self.preferences.repl_height = max(
                8,
                min(
                    self.size.height - 12, self.preferences.repl_height - message.delta
                ),
            )
        self._layout()

    def action_copy_selection(self) -> None:
        """Copy selected editor/input text through TextArea's terminal clipboard API."""
        if isinstance(self.focused, (TextArea, Input)):
            self.focused.action_copy()

    def action_search(self, replace: bool = False) -> None:
        """Open a literal, case-sensitive search bar for the current document."""
        if len(self.screen_stack) > 1:
            return
        self._search_document = self.workspace.active_document
        self.compact_view = "editor"
        self.repl_only = False
        self.query_one("#replace-text").display = replace
        self.query_one("#replace-one").display = replace
        self.query_one("#replace-all").display = replace
        self._layout()
        self.query_one("#find-text", Input).focus()

    def action_dismiss_search(self) -> None:
        """Return focus to the editor without changing its text or selected match."""
        self._search_document = None
        self._layout()
        self.source_editor.focus()

    @on(Input.Submitted, "#find-text")
    def find_submitted(self) -> None:
        """Find the next match when Enter is pressed in the search field."""
        self.find_next()

    def find_next(self) -> bool:
        """Select a source-coordinate match, wrapping once from EOF to the start."""
        needle = self.query_one("#find-text", Input).value
        if not needle:
            return False
        editor = self.source_editor
        offset = editor.source_offset(max(editor.selection))
        index = editor.text.find(needle, offset)
        if index < 0:
            index = editor.text.find(needle)
        if index < 0:
            self.notify("No matches")
            return False
        editor.selection = Selection(
            editor.source_location(index), editor.source_location(index + len(needle))
        )
        editor.scroll_cursor_visible()
        return True

    def replace_matches(self, all_matches: bool = False) -> None:
        """Replace literal matches as one undoable edit; never cross document scope."""
        needle = self.query_one("#find-text", Input).value
        replacement = self.query_one("#replace-text", Input).value
        if not needle:
            return
        editor = self.source_editor
        if all_matches:
            if needle in editor.text:
                editor.replace(
                    editor.text.replace(needle, replacement),
                    (0, 0),
                    editor.document.end,
                    maintain_selection_offset=False,
                )
        else:
            if editor.selected_text != needle and not self.find_next():
                return
            editor.replace(
                replacement, *sorted(editor.selection), maintain_selection_offset=False
            )
            self.find_next()
