"""Revision-safe completion presentation shared by source and command input."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.widgets import OptionList
from textual.widgets.option_list import Option

from valiance.analysis.completion import completion_end, completion_prefix

from .editing import DocumentEditor


class CompletionList(OptionList, can_focus=False):
    """An anchored scrollable overlay that leaves keyboard focus in the editor."""


class EditorCompletion:
    """Coalesce cursor queries and reject stale draft, session and world results."""

    def __init__(self, app) -> None:
        """Start with no automatic insertion or restored popup."""
        self.app = app
        self.key = None
        self.dismissed = None
        self.explicit = False
        self.task = None
        self.pending_query = None
        self.items = ()
        self.editor = None
        self.fragment = None
        self.pending_request = None
        self.session_revision = 0

    @property
    def listing(self):
        """Resolve the mounted overlay without following modal screens."""
        return self.app.editor_screen.query_one("#completion-list", CompletionList)

    def hide(self, *, dismiss=False) -> None:
        """Close suggestions without changing source, input lock or history."""
        if dismiss:
            self.dismissed = self.key[:3] if self.key else None
            self.explicit = False
        self.items = ()
        if self.app.editor_screen.query("#completion-list"):
            self.listing.display = False

    def poll(self, *, explicit=False) -> None:
        """Query only matching semantic checkpoints, never textual name lists."""
        if self.app.analysis.closed or not self.app.editor_screen.query(
            "#completion-list"
        ):
            return
        editor = self.app.focused
        if not isinstance(editor, DocumentEditor) or getattr(
            editor, "program_input", False
        ):
            self.hide()
            self.key = None
            self.explicit = False
            return
        offset = editor.source_offset(editor.cursor_location)
        source = editor.text
        command = editor.id == "command"
        product = self.app.analysis.snapshot
        key = (
            editor.id,
            source,
            offset,
            id(product) if not command else self.session_revision,
            tuple(self.app._world_selection.items()),
            self.app._control_state_index,
        )
        if key == self.key and not explicit:
            return
        fragment = completion_prefix(source, offset)
        self.pending_query = None
        if self.editor is not editor:
            self.explicit = False
        self.editor = editor
        if explicit:
            self.explicit = True
            self.dismissed = None
        self.key = key
        self.fragment = fragment
        self.hide()
        if (
            fragment is None
            or self.dismissed == key[:3]
            or not editor.selection.is_empty
        ):
            return
        start, prefix = fragment
        if command:
            runtime = self.app.runtime
            if (
                runtime is None
                or runtime.worker.busy
                or runtime.worker.preview_pending
                or not runtime.worker.ready
                or runtime.closing
            ):
                # Reconsider when the session becomes available.
                self.key = None
                return
            request = runtime.worker.complete(editor.text, offset)
            self.pending_request = (request, key)
            return
        if product is None:
            return
        scope = product.scope_at(start)
        world = (
            self.app._world_selection.get(
                (self.app.workspace.active_document, scope.identity)
            )
            if scope
            else None
        )
        facts = product.completion_at(start, world)
        if facts.states:
            facts = facts.states[
                min(self.app._control_state_index, len(facts.states) - 1)
            ]
        if facts.completion is None:
            return
        self.pending_query = (facts.completion, prefix, key)
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._query())

    async def _query(self) -> None:
        """Keep candidate inference off the UI thread and discard superseded replies."""
        try:
            while self.pending_query is not None:
                context, prefix, key = self.pending_query
                self.pending_query = None
                items = await asyncio.to_thread(context.candidates, prefix)
                if not self.app.analysis.closed:
                    self.show(items, key)
        except asyncio.CancelledError:
            pass

    def receive(self, event) -> None:
        """Accept a preview only for the draft and session that requested it."""
        if self.pending_request and self.pending_request[0] == event.request:
            self.show(event.items, self.pending_request[1])
            self.pending_request = None

    def invalidate_session(self) -> None:
        """Invalidate previews on every execution, branch change, reset and Stop."""
        self.session_revision += 1
        self.pending_request = None
        self.key = None
        self.hide()

    def show(self, items, key) -> None:
        """Apply the automatic threshold after semantic filtering and grouping."""
        if (
            key != self.key
            or self.app.focused is not self.editor
            or self.dismissed == key[:3]
            or not self.app.editor_screen.query("#completion-list")
        ):
            return
        self.items = items
        if not items or (len(items) > 8 and not self.explicit):
            self.hide()
            return
        listing = self.listing
        listing.clear_options()
        listing.add_options(
            [
                Option(Text(f"{item.text}  ·  {item.meta.replace(chr(10), ' | ')}"))
                for item in items
            ]
        )
        listing.highlighted = 0
        listing.styles.height = min(10, len(items) + 2)
        listing.styles.width = min(70, self.app.size.width)
        cursor = self.editor.cursor_screen_offset
        height = min(10, len(items) + 2)
        y = (
            cursor.y + 1
            if cursor.y + height + 1 < self.app.size.height
            else max(0, cursor.y - height)
        )
        listing.styles.offset = (min(cursor.x, max(0, self.app.size.width - 70)), y)
        listing.display = True

    def handle_key(self, event) -> bool:
        """Give completion precedence while leaving Enter's normal meaning intact."""
        if event.key in {"ctrl+space", "ctrl+@"}:
            self.poll(explicit=True)
        elif self.listing.display and self.items:
            if event.key in {"up", "down"}:
                delta = -1 if event.key == "up" else 1
                self.listing.highlighted = (
                    (self.listing.highlighted or 0) + delta
                ) % len(self.items)
                self.listing.scroll_to_highlight()
            elif event.key == "escape":
                self.hide(dismiss=True)
            elif event.key == "tab":
                self.accept()
            else:
                if event.key == "enter":
                    self.hide(dismiss=True)
                return False
        else:
            return False
        event.stop()
        event.prevent_default()
        return True

    def accept(self) -> None:
        """Replace a source fragment as an undoable native editor operation."""
        if not self.items or self.fragment is None:
            return
        item = self.items[self.listing.highlighted or 0]
        start, _ = self.fragment
        end = self.editor.source_offset(self.editor.cursor_location)
        end = completion_end(self.editor.text, end)
        self.editor.apply_completion(start, end, item.text)
        self.hide(dismiss=True)
        self.dismissed = (
            self.editor.id,
            self.editor.text,
            self.editor.source_offset(self.editor.cursor_location),
        )
        self.explicit = False

    async def close(self) -> None:
        """Detach completion queries during application shutdown."""
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
