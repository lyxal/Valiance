"""Behavioral tests for the new Textual editor and execution feasibility tools."""

import asyncio
import time
import unittest

from textual import events
from textual.document._document import Selection
from textual.widgets import ContentSwitcher

from tools.terminal_editor.app import CommandEditor, FeasibilityApp
from tools.terminal_editor.worker import SessionProbe, wait_for
from valiance.terminal_editor.divider import PaneDivider
from valiance.terminal_editor.editor import SourceEmphasis


class DocumentFileTests(unittest.TestCase):
    """Protect saved bytes, document identities and imported overlays."""

    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.terminal_editor.models import EditorWorkspace
        from valiance.terminal_editor.files import DocumentFiles

        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.workspace = EditorWorkspace(self.root)
        self.files = DocumentFiles(self.workspace)

    def test_save_as_and_rename_preserve_unsaved_text_and_overlay_identity(self):
        old = self.root / "old.vlnc"
        old.write_text("1")
        document = self.files.open(old)
        self.workspace.update(document.id, "2")
        new = self.root / "new.vlnc"
        renamed = self.files.rename(document.id, new)
        self.assertFalse(old.exists())
        self.assertEqual(new.read_text(), "1")
        self.assertEqual(renamed.source, "2")
        self.assertTrue(renamed.dirty)
        self.assertEqual(self.workspace.database.source_for(new), ("2", True))
        saved = self.files.save(document.id)
        self.assertFalse(saved.dirty)
        self.assertEqual(new.read_text(), "2")
        elsewhere = self.root / "other.vlnc"
        self.files.save(document.id, elsewhere)
        self.assertEqual(new.read_text(), "2")
        self.assertEqual(elsewhere.read_text(), "2")
        self.assertEqual(
            self.workspace.documents[document.id].root_source.base_directory, self.root
        )

    def test_failed_save_and_duplicate_target_leave_documents_and_disk_intact(self):
        document = self.workspace.new_document("unsaved")
        self.assertRaises(
            OSError, self.files.save, document.id, self.root / "missing" / "file.vlnc"
        )
        self.assertIsNone(self.workspace.documents[document.id].path)
        existing = self.root / "existing.vlnc"
        existing.write_text("saved")
        other = self.files.open(existing)
        self.assertRaises(
            ValueError, self.files.save, document.id, existing, overwrite=True
        )
        self.assertEqual(existing.read_text(), "saved")
        self.assertEqual(self.workspace.documents[other.id].source, "saved")

    def test_external_keep_reload_delete_and_conflict(self):
        from valiance.terminal_editor.files import DiskConflict

        path = self.root / "source.vlnc"
        path.write_text("1")
        document = self.files.open(path)
        path.write_text("2")
        (change,) = self.files.external_changes()
        self.assertFalse(document.dirty)
        self.assertEqual(document.source, "1")
        self.assertRaises(DiskConflict, self.files.save, document.id)
        kept = self.files.keep(change)
        self.assertTrue(kept.dirty)
        self.assertEqual(kept.source, "1")
        self.assertFalse(self.files.external_changes())
        reloaded = self.files.reload(document.id)
        self.assertEqual(reloaded.source, "2")
        self.assertFalse(reloaded.dirty)
        path.unlink()
        self.files.keep(self.files.external_changes()[0])
        self.assertTrue(self.workspace.documents[document.id].dirty)
        self.files.save(document.id)
        self.assertEqual(path.read_text(), "2")

    def test_edit_during_write_retains_newer_source_and_saved_baseline(self):
        document = self.workspace.new_document("1")
        path = self.root / "source.vlnc"
        operation = self.files.prepare(document.id, path)
        self.workspace.update(document.id, "2")
        operation.perform()
        saved = self.files.commit(operation)
        self.assertEqual(saved.source, "2")
        self.assertEqual(saved.saved_source, "1")
        self.assertTrue(saved.dirty)
        self.assertEqual(path.read_text(), "1")

    def test_preferences_are_bounded_and_never_restore_source(self):
        from valiance.terminal_editor.files import EditorPreferences

        path = self.root / "settings.json"
        preferences = EditorPreferences()
        for index in range(30):
            preferences.remember(self.root / str(index))
        preferences.save(path)
        loaded = EditorPreferences.load(path)
        self.assertEqual(len(loaded.recent), 20)
        self.assertNotIn("source", path.read_text())
        path.write_text("{bad")
        self.assertEqual(EditorPreferences.load(path), EditorPreferences())

    def test_crlf_and_unicode_round_trip_without_initial_dirty_state(self):
        path = self.root / "source.vlnc"
        data = '"界"\r\n1\r\n'.encode("utf-8")
        path.write_bytes(data)
        document = self.files.open(path)
        self.assertFalse(document.dirty)
        self.files.save(document.id)
        self.assertEqual(path.read_bytes(), data)


class EditorApplicationTests(unittest.IsolatedAsyncioTestCase):
    """Drive real keyboard/modal workflows and retained TextArea editing state."""

    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.terminal_editor.app import EditorApp

        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.app = EditorApp(
            launch_directory=self.root, watch_files=False, with_worker=False
        )

    async def choose_path(self, pilot, path):
        from textual.widgets import Input

        await pilot.pause()
        self.app.screen.query_one("#path-value", Input).value = str(path)
        await pilot.press("enter")
        await pilot.pause()

    async def choose(self, pilot, identity):
        await pilot.pause()
        self.app.screen.query_one("#" + identity).focus()
        await pilot.press("enter")
        await pilot.pause()

    async def wait_runtime(self, pilot, predicate):
        """Wait for real process boundaries while the UI continues receiving events."""
        deadline = time.monotonic() + 15
        while not predicate():
            if time.monotonic() > deadline:
                self.fail(
                    f"Runtime timeout: {self.app.runtime.label}; {list(self.app.state.transcript.entries)[-5:]}"
                )
            await pilot.pause(0.05)

    async def test_toolbar_alignment_and_mouse_menu_dismissal(self):
        from unittest.mock import patch

        from textual.widgets import Button, Tabs
        from valiance.terminal_editor.dialogs import MenuDialog

        app = self.app
        async with app.run_test(size=(140, 40)) as pilot:
            self.assertEqual(
                app.query_one("#tabs", Tabs).region.y,
                app.query_one("#file-menu", Button).region.y,
            )
            for identity in (
                "file-menu",
                "edit-menu",
                "view-menu",
                "run-file",
                "stop-execution",
            ):
                button = app.query_one(f"#{identity}", Button)
                self.assertEqual(button.region.height, 1)
                self.assertEqual(button.region.y, app.query_one("#tabs", Tabs).region.y)
                self.assertEqual(button.region.y, 1)
            panes = ("#file-strip", "#working-area", "#repl", "#status")
            regions = {identity: app.query_one(identity).region for identity in panes}
            for identity in ("file-menu", "edit-menu", "view-menu"):
                button = app.query_one(f"#{identity}", Button)
                first_frames = []
                original_position = MenuDialog._position

                def observe_position(dialog):
                    """Record the painted placement before deferred repositioning."""
                    first_frames.append(dialog.query_one(".menu-dialog").region)
                    original_position(dialog)

                with patch.object(MenuDialog, "_position", observe_position):
                    await pilot.click(button)
                    await pilot.pause()
                menu = app.screen.query_one(".menu-dialog")
                self.assertTrue(first_frames)
                self.assertTrue(all(region == menu.region for region in first_frames))
                self.assertEqual(menu.region.y, button.region.bottom)
                self.assertEqual(
                    menu.region.x,
                    min(button.region.x, app.size.width - menu.region.width),
                )
                self.assertEqual(app.screen.styles.background.a, 0)
                self.assertEqual(app.screen.styles.padding.top, 0)
                self.assertEqual(
                    regions,
                    {identity: app.query_one(identity).region for identity in panes},
                )
                await pilot.press("escape")
                await pilot.pause()
            await pilot.click("#view-menu")
            await pilot.pause()
            self.assertIsInstance(app.screen, MenuDialog)
            self.assertFalse(app.screen.query("#menu-action-find"))
            await pilot.click("#menu-dismiss")
            self.assertEqual(len(app.screen_stack), 1)
            await pilot.click("#view-menu")
            await pilot.pause()
            await pilot.click(offset=(1, 20))
            self.assertEqual(len(app.screen_stack), 1)
            await pilot.click("#edit-menu")
            await pilot.pause()
            self.assertTrue(app.screen.query("#menu-action-find"))
            self.assertFalse(app.screen.query("#menu-action-repl"))
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            menu = app.screen.query_one(".menu-dialog")
            button = app.query_one("#edit-menu", Button)
            self.assertEqual(menu.region.y, button.region.bottom)
            self.assertGreaterEqual(menu.region.x, 0)
            self.assertLessEqual(menu.region.right, 80)
            await pilot.press("escape")

    async def test_run_shortcuts_persistent_repl_and_failed_load(self):
        from textual.widgets import TextArea
        from valiance.terminal_editor.runtime import EditorRuntime

        app = self.app
        app.runtime = EditorRuntime(app)
        async with app.run_test(size=(140, 40)) as pilot:
            app.source_editor.load_text("1 2 +")
            await pilot.press("f5")
            await self.wait_runtime(
                pilot,
                lambda: (
                    app.state.loaded_source is not None and not app.runtime.worker.busy
                ),
            )
            self.assertEqual(app.state.transcript.entries[-1].text, "3\n")
            command = app.query_one("#command", TextArea)
            command.load_text("4 +")
            command.focus()
            await pilot.press("enter")
            await self.wait_runtime(pilot, lambda: not app.runtime.worker.busy)
            self.assertEqual(app.state.transcript.entries[-1].text, "7\n")
            self.assertEqual(command.text, "")
            previous = app.state.loaded_source
            app.source_editor.load_text("this_word_does_not_exist")
            app.source_editor.focus()
            await pilot.press("f5")
            await self.wait_runtime(pilot, lambda: app.runtime.label == "Run failed")
            self.assertEqual(app.state.loaded_source, previous)
            command.load_text("1 +")
            command.focus()
            await pilot.press("enter")
            await self.wait_runtime(pilot, lambda: not app.runtime.worker.busy)
            self.assertEqual(app.state.transcript.entries[-1].text, "8\n")
            app.source_editor.load_text("9")
            app.source_editor.focus()
            await pilot.press("alt+x")
            await self.wait_runtime(
                pilot,
                lambda: (
                    app.state.loaded_source != previous and not app.runtime.worker.busy
                ),
            )
            self.assertEqual(app.state.transcript.entries[-1].text, "9\n")
        self.assertIsNone(app.runtime.worker.process)

    async def test_stop_keeps_editor_responsive_and_routes_program_input(self):
        from textual.widgets import TextArea
        from valiance.terminal_editor.runtime import EditorRuntime

        app = self.app
        app.runtime = EditorRuntime(app)
        async with app.run_test(size=(140, 40)) as pilot:
            app.source_editor.load_text('"Prompt: " input')
            await pilot.press("f5")
            await self.wait_runtime(pilot, lambda: app.runtime.waiting_input)
            command = app.query_one("#command", TextArea)
            command.load_text("hello")
            await pilot.press("enter")
            await self.wait_runtime(pilot, lambda: not app.runtime.worker.busy)
            self.assertEqual(app.state.transcript.entries[-1].text, "'hello'\n")
            app.source_editor.load_text("while (true) => end")
            app.source_editor.focus()
            await pilot.press("f5")
            await self.wait_runtime(pilot, lambda: app.runtime.label == "Running file")
            await pilot.press("a")
            self.assertTrue(app.source_editor.text.startswith("a"))
            await pilot.press("f8")
            await self.wait_runtime(
                pilot, lambda: not app.runtime.closing and app.runtime.worker.ready
            )
            self.assertIsNone(app.state.loaded_source)
            self.assertTrue(
                any("Stopped" in entry.text for entry in app.state.transcript.entries)
            )
        self.assertIsNone(app.runtime.worker.process)

    async def test_open_save_as_rename_and_same_path_retains_native_undo(self):
        app = self.app
        path = self.root / "source.vlnc"
        path.write_text("1")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("ctrl+o")
            await self.choose_path(pilot, path)
            editor = app.source_editor
            identity = app.workspace.active_document
            await pilot.press("a")
            app.action_file_action("open")
            await self.choose_path(pilot, path)
            self.assertIs(app.source_editor, editor)
            self.assertEqual(editor.text, "a1")
            await pilot.press("ctrl+z")
            self.assertEqual(editor.text, "1")
            await pilot.press("b")
            app.action_file_action("rename")
            renamed = self.root / "renamed.vlnc"
            await self.choose_path(pilot, renamed)
            self.assertEqual(app.workspace.active_document, identity)
            self.assertIs(app.source_editor, editor)
            self.assertEqual(renamed.read_text(), "1")
            self.assertEqual(editor.text, "b1")
            self.assertFalse(path.exists())
            await pilot.press("ctrl+s")
            await pilot.pause()
            self.assertEqual(renamed.read_text(), "b1")
            self.assertFalse(app.workspace.documents[identity].dirty)

    async def test_cancelled_and_failed_save_prevent_close_and_last_close_is_blank(
        self,
    ):
        app = self.app
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("a", "ctrl+w")
            await self.choose(pilot, "save")
            await pilot.press("escape")
            await pilot.pause()
            self.assertEqual(app.source_editor.text, "a")
            self.assertEqual(len(app.workspace.documents), 1)
            await pilot.press("ctrl+w")
            await self.choose(pilot, "save")
            await self.choose_path(pilot, self.root / "missing" / "file.vlnc")
            self.assertEqual(app.source_editor.text, "a")
            await pilot.press("ctrl+w")
            await self.choose(pilot, "discard")
            self.assertEqual(len(app.workspace.documents), 1)
            self.assertEqual(app.source_editor.text, "")

    async def test_typing_during_slow_save_remains_dirty_and_prevents_close(self):
        import threading
        from unittest.mock import patch
        from valiance.terminal_editor.files import atomic_write

        app = self.app
        started, release = threading.Event(), threading.Event()

        def slow_write(path, data):
            started.set()
            release.wait(5)
            atomic_write(path, data)

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("a", "ctrl+w")
            await self.choose(pilot, "save")
            with patch("valiance.terminal_editor.files.atomic_write", slow_write):
                path = self.root / "source.vlnc"
                await self.choose_path(pilot, path)
                self.assertTrue(started.is_set())
                app.source_editor.focus()
                await pilot.press("b")
                release.set()
                await app.workers.wait_for_complete()
            self.assertEqual(app.source_editor.text, "ab")
            self.assertTrue(
                app.workspace.documents[app.workspace.active_document].dirty
            )
            self.assertEqual(path.read_text(), "a")

    async def test_dirty_quit_cancel_protects_all_documents(self):
        app = self.app
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("a", "ctrl+n")
            await pilot.press("b", "ctrl+q")
            await self.choose(pilot, "discard")
            await self.choose(pilot, "cancel")
            self.assertEqual(
                sorted(doc.source for doc in app.workspace.documents.values()),
                ["a", "b"],
            )
            self.assertEqual(len(app.screen_stack), 1)

    async def test_search_replace_and_block_indentation_are_undoable(self):
        from textual.widgets import Input
        from textual.document._document import Selection

        app = self.app
        async with app.run_test(size=(120, 40)) as pilot:
            app.source_editor.insert("α\t界\nα\t界")
            await pilot.pause()
            app.action_search(True)
            app.query_one("#find-text", Input).value = "α"
            app.query_one("#replace-text", Input).value = "omega"
            app.replace_matches(True)
            self.assertEqual(app.source_editor.text, "omega\t界\nomega\t界")
            app.action_dismiss_search()
            await pilot.press("ctrl+z")
            self.assertEqual(app.source_editor.text, "α\t界\nα\t界")
            app.source_editor.selection = Selection((0, 0), (1, 3))
            await pilot.press("f10")
            self.assertEqual(app.source_editor.text, "  α\t界\n  α\t界")
            await pilot.press("ctrl+z")
            self.assertEqual(app.source_editor.text, "α\t界\nα\t界")

    async def test_autoindent_closing_end_and_string_content(self):
        app = self.app
        async with app.run_test(size=(120, 40)) as pilot:
            app.source_editor.insert("if (True) =>")
            await pilot.press("enter")
            self.assertTrue(app.source_editor.text.endswith("\n  "))
            await pilot.press("e", "n", "d")
            self.assertTrue(app.source_editor.text.endswith("\nend"))
            await pilot.press("ctrl+z")
            self.assertTrue(app.source_editor.text.endswith("\n  en"))
            app.source_editor.load_text('if (True) =>\n  "en')
            app.source_editor.move_cursor((1, 5))
            await pilot.press("d")
            self.assertTrue(app.source_editor.text.endswith('  "end'))

    async def test_external_clean_change_requires_choice_and_keep_becomes_unsaved(self):
        from valiance.terminal_editor.app import EditorApp

        app = self.app
        path = self.root / "source.vlnc"
        path.write_text("1")
        async with app.run_test(size=(120, 40)) as pilot:
            document = app.files.open(path)
            await app._mount_document(document.id)
            path.write_text("2")
            app._poll_files()
            await pilot.pause()
            self.assertEqual(app.source_editor.text, "1")
            await self.choose(pilot, "keep")
            self.assertTrue(app.workspace.documents[document.id].dirty)
            self.assertEqual(app.source_editor.text, "1")
            path.write_text("3")
            app.post_message(EditorApp.FilesChanged(app.files.external_changes()))
            await self.choose(pilot, "reload")
            await self.choose(pilot, "reload")
            self.assertEqual(app.source_editor.text, "3")
            self.assertFalse(app.workspace.documents[document.id].dirty)

    async def test_same_basename_labels_recent_paths_and_save_as_import_base(self):
        from textual.widgets import Tab

        app = self.app
        first = self.root / "one"
        second = self.root / "two"
        first.mkdir()
        second.mkdir()
        for directory in (first, second):
            (directory / "source.vlnc").write_text("1")
        async with app.run_test(size=(120, 40)) as pilot:
            one = app.files.open(first / "source.vlnc")
            await app._mount_document(one.id)
            two = app.files.open(second / "source.vlnc")
            await app._mount_document(two.id)
            self.assertNotEqual(
                str(app.query_one("#tab-" + one.id, Tab).label),
                str(app.query_one("#tab-" + two.id, Tab).label),
            )
            app.preferences.remember(first / "source.vlnc")
            app.action_file_action("recent")
            await self.choose(pilot, "recent-0")
            self.assertEqual(app.workspace.active_document, one.id)
            app.action_file_action("save-as")
            target = second / "moved.vlnc"
            await self.choose_path(pilot, target)
            self.assertEqual(
                app.workspace.documents[one.id].root_source.base_directory, second
            )
            self.assertEqual(app.workspace.database.source_for(target), ("1", True))
            self.assertEqual((first / "source.vlnc").read_text(), "1")

    async def test_native_copy_cut_paste_and_find_match_keep_unicode_source_coordinates(
        self,
    ):
        from textual.widgets import Input
        from textual.document._document import Selection

        app = self.app
        async with app.run_test(size=(80, 24)) as pilot:
            editor = app.source_editor
            editor.insert("α\t界\nhello")
            editor.selection = Selection((0, 0), (0, 3))
            await pilot.press("ctrl+c")
            self.assertEqual(app.clipboard, "α\t界")
            editor.action_cut()
            self.assertEqual(editor.text, "\nhello")
            editor.action_paste()
            self.assertEqual(editor.text, "α\t界\nhello")
            app.action_search()
            app.query_one("#find-text", Input).value = "界"
            self.assertTrue(app.find_next())
            self.assertEqual(editor.selection, Selection((0, 2), (0, 3)))
            app._view_action("wrap")
            self.assertFalse(editor.soft_wrap)
            self.assertEqual(editor.text, "α\t界\nhello")

    async def test_pane_modes_and_resizes_preserve_buffers_selection_and_undo(self):
        app = self.app
        async with app.run_test(size=(160, 50)) as pilot:
            await pilot.press("x")
            self.assertEqual(
                app.query_one("#inspector").region.height,
                app.query_one("#working-area").region.height,
            )
            editor = app.source_editor
            app.preferences.inspector_width = 40
            app.preferences.repl_height = 14
            app.action_toggle_repl()
            app.action_toggle_repl()
            app._view_action("repl-only")
            app._view_action("repl-only")
            self.assertEqual(
                (app.preferences.inspector_width, app.preferences.repl_height), (40, 14)
            )
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            self.assertTrue(app.query_one("#documents-view").display)
            app.action_cycle_panes()
            self.assertTrue(app.query_one("#inspector").display)
            app.action_cycle_panes()
            self.assertTrue(app.query_one("#repl").display)
            app.action_cycle_panes()
            self.assertIs(app.source_editor, editor)
            await pilot.press("ctrl+z")
            self.assertEqual(editor.text, "")
            await pilot.resize_terminal(120, 40)
            await pilot.pause()
            self.assertTrue(app.query_one("#inspector").display)


class EditorModelTests(unittest.TestCase):
    """Exercise workspace/session separation without mounting a terminal."""

    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.terminal_editor.models import EditorState, EditorWorkspace
        from valiance.sessions.service import SessionService

        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.state = EditorState(EditorWorkspace(self.root))
        self.session = SessionService()
        self.addCleanup(self.session.close)

    def test_load_clear_reset_edit_and_failed_load_are_independent(self):
        from valiance.terminal_editor.models import InspectorSelection

        workspace = self.state.workspace
        document = workspace.documents[workspace.active_document]
        self.state.edit_document(document.id, "7")
        self.state.input.record("1 +")
        self.state.input.locked = True
        self.assertTrue(self.state.load(self.session, document.id).successful)
        loaded = self.state.loaded_source
        self.state.inspector = InspectorSelection(document.id, loaded.revision)
        self.state.edit_document(document.id, "unknown_name")
        self.assertIsNone(self.state.inspector)
        self.assertEqual(self.state.loaded_source, loaded)
        self.assertFalse(self.state.load(self.session, document.id).successful)
        self.assertEqual(self.session.runtime_stack, [7])
        self.assertEqual(self.state.loaded_source, loaded)
        self.state.clear()
        self.assertFalse(self.state.transcript.entries)
        self.assertEqual(self.session.runtime_stack, [7])
        self.state.reset(self.session)
        self.assertEqual(self.session.runtime_stack, [])
        self.assertIsNone(self.state.loaded_source)
        self.assertEqual(workspace.documents[document.id].source, "unknown_name")
        self.assertTrue(self.state.input.locked)
        self.assertEqual(self.state.input.history, ["1 +"])

    def test_untitled_identity_save_association_and_last_close(self):
        from valiance.terminal_editor.models import DocumentView

        workspace = self.state.workspace
        document = workspace.documents[workspace.active_document]
        self.assertFalse(document.dirty)
        self.assertIsNone(document.root_source.path)
        view = DocumentView(cursor=(0, 1), scroll=(2, 3))
        edited = workspace.update(document.id, "42", view=view)
        self.assertTrue(edited.dirty)
        path = self.root / "saved.vlnc"
        associated = workspace.associate_path(document.id, path, saved_source="42")
        self.assertEqual(associated.id, document.id)
        self.assertEqual(associated.view, view)
        self.assertFalse(associated.dirty)
        self.assertFalse(path.exists())
        workspace.close_document(document.id)
        self.assertEqual(len(workspace.documents), 1)
        self.assertIsNone(workspace.documents[workspace.active_document].path)

    def test_open_same_path_reuses_unsaved_overlay(self):
        workspace = self.state.workspace
        path = self.root / "source.vlnc"
        path.write_text("1")
        document = workspace.open_document(path)
        edited = workspace.update(document.id, "2")
        self.assertEqual(workspace.open_document(path), edited)
        self.assertEqual(workspace.database.source_for(path), ("2", True))

    def test_history_restores_text_caret_selection_and_scroll_and_respects_lock(self):
        from valiance.terminal_editor.models import InputDraft, InputState, DocumentView

        state = InputState(history=["1", "2"], limit=2)
        draft = InputDraft("unfinished", DocumentView((0, 3), (0, 1), (0, 4)))
        self.assertEqual(state.previous(draft).source, "2")
        self.assertEqual(state.previous(InputDraft("2")).source, "1")
        self.assertEqual(state.next(InputDraft("1")).source, "2")
        self.assertEqual(state.next(InputDraft("2")), draft)
        state.locked = True
        self.assertEqual(state.previous(draft), draft)
        state.record("3")
        self.assertEqual(state.history, ["2", "3"])

    def test_bounded_transcript_exports_truncation_marker(self):
        from valiance.terminal_editor.models import Transcript, TranscriptEntry

        transcript = Transcript(max_entries=2, max_characters=5)
        for value in ("12", "34", "56"):
            transcript.append(TranscriptEntry("output", value))
        self.assertEqual([e.text for e in transcript.entries], ["34", "56"])
        self.assertIn("dropped", transcript.export_text())
        transcript.append(TranscriptEntry("output", "123456789"))
        self.assertLessEqual(transcript.characters, 5)
        self.assertEqual(transcript.entries[-1].text, "56789")
        transcript.clear()
        self.assertEqual(transcript.export_text(), "")


class TerminalEditorTests(unittest.IsolatedAsyncioTestCase):
    """Exercise source coordinates and retained editing through actual widgets."""

    async def test_tab_switch_and_hidden_panes_retain_selection_and_undo(self):
        app = FeasibilityApp(with_worker=False)
        async with app.run_test(size=(120, 40)) as pilot:
            editor = app.source_editor
            editor.move_cursor((0, 5))
            await pilot.press("a", "b")
            editor.selection = Selection((0, 1), (0, 3))
            selection = editor.selection
            text = editor.text
            await pilot.press("f2")
            self.assertEqual(app.query_one(ContentSwitcher).current, "source-2")
            await pilot.press("x", "f2")
            app.action_toggle_inspector()
            app.action_toggle_inspector()
            self.assertIs(app.source_editor, editor)
            self.assertEqual(editor.selection, selection)
            self.assertEqual(editor.text, text)
            editor.action_undo()
            self.assertNotEqual(editor.text, text)
            self.assertEqual(app.query_one("#source-2").text, "x")

    async def test_compact_layout_and_resize_preserve_documents(self):
        app = FeasibilityApp(with_worker=False)
        async with app.run_test(size=(80, 24)) as pilot:
            editor = app.source_editor
            self.assertTrue(app.query_one("#documents-view").display)
            self.assertFalse(app.query_one("#inspector").display)
            await pilot.press("f3")
            self.assertTrue(app.query_one("#inspector").display)
            self.assertFalse(app.query_one("#documents-view").display)
            await pilot.press("f3")
            for size in ((120, 40), (160, 50), (80, 24)):
                await pilot.resize_terminal(*size)
                await pilot.pause()
                self.assertIs(app.source_editor, editor)
                self.assertGreater(editor.size.width, 0)
                self.assertGreater(editor.size.height, 0)

    async def test_wrapped_unicode_mouse_mapping_and_compiler_styles(self):
        app = FeasibilityApp(with_worker=False)
        async with app.run_test(size=(80, 24)) as pilot:
            editor = app.source_editor
            source = '"界\te\u0301 ' + "long " * 35 + '" 123'
            editor.load_text(source)
            await pilot.pause()
            column = 110
            editor.move_cursor((0, column))
            await pilot.pause()
            offset = editor.cursor_screen_offset
            editor.move_cursor((0, 0))
            await pilot.click(
                editor, offset=(offset.x - editor.region.x, offset.y - editor.region.y)
            )
            self.assertEqual(editor.cursor_location, (0, column))
            self.assertEqual(editor.text, source)
            line = editor.get_line(0)
            self.assertTrue(
                any(
                    span.start == 0 and span.end == source.rindex('"') + 1
                    for span in line.spans
                )
            )
            editor.set_emphasis((SourceEmphasis(column, column + 1, "underline red"),))
            self.assertIn("underline", str(editor.get_line(0).spans[-1].style))
            await pilot.press("z")
            self.assertFalse(editor._emphasis)

    async def test_lock_and_paste_do_not_submit_or_change_the_draft(self):
        app = FeasibilityApp(with_worker=False)
        async with app.run_test() as pilot:
            command = app.query_one(CommandEditor)
            command.focus()
            command.load_text("draft")
            command.move_cursor((0, 5))
            await pilot.press("ctrl+enter")
            self.assertTrue(command.locked)
            self.assertEqual(command.text, "draft")
            await pilot.press("enter")
            self.assertEqual(command.text, "draft\n")
            await pilot.press("shift+enter")
            self.assertFalse(command.locked)
            app.post_message(events.Paste("one\ntwo"))
            await pilot.pause()
            self.assertEqual(command.text, "draft\none\ntwo")
            await pilot.press("f4")
            self.assertTrue(command.locked)
            self.assertIn("f4", app.recent_keys)
            self.assertIn("shift+enter", app.recent_keys)

    async def test_clipboard_uses_native_selection_and_paste(self):
        app = FeasibilityApp(with_worker=False)
        async with app.run_test() as pilot:
            editor = app.source_editor
            editor.selection = Selection((0, 0), (0, 3))
            editor.action_copy()
            self.assertEqual(app.clipboard, "1 2")
            editor.move_cursor((0, 5))
            editor.action_paste()
            await pilot.pause()
            self.assertEqual(editor.text, "1 2 +1 2")

    async def test_keyboard_divider_clamps_and_preserves_size_when_hidden(self):
        app = FeasibilityApp(with_worker=False)
        async with app.run_test(size=(120, 40)) as pilot:
            divider = app.query_one("#state-divider", PaneDivider)
            divider.focus()
            await pilot.press("left", "left")
            self.assertEqual(app.inspector_width, 34)
            app.action_toggle_inspector()
            app.action_toggle_inspector()
            self.assertEqual(app.inspector_width, 34)
            x = divider.region.x + 3
            await pilot.mouse_down(divider)
            await pilot.hover("#working-area", offset=(x, 1))
            await pilot.mouse_up("#working-area", offset=(x, 1))
            self.assertEqual(app.inspector_width, 31)
            divider.action_resize(200)
            await pilot.pause()
            self.assertEqual(app.inspector_width, 24)

    async def test_worker_execution_keeps_editor_responsive(self):
        app = FeasibilityApp()
        async with app.run_test(size=(120, 40)) as pilot:
            deadline = time.monotonic() + 10
            while not app.probe.ready and time.monotonic() < deadline:
                await asyncio.sleep(0.03)
            self.assertTrue(app.probe.ready)
            app.probe.submit(operation="native-block")
            await asyncio.sleep(0.1)
            await pilot.press("a", "b", "c")
            self.assertEqual(app.source_editor.text, "abc1 2 +")
            app.query_one(CommandEditor).toggle_lock()
            app.action_stop()
            await app.workers.wait_for_complete()
            self.assertEqual(app.source_editor.text, "abc1 2 +")
            self.assertTrue(app.query_one(CommandEditor).locked)
            # Quitting with the replacement worker busy must also reap it.
            deadline = time.monotonic() + 10
            while not app.probe.ready and time.monotonic() < deadline:
                await asyncio.sleep(0.03)
            app.probe.submit(operation="native-block")
            stopped_probe = app.probe
        self.assertIsNone(stopped_probe.process)


class TerminalWorkerTests(unittest.TestCase):
    """Use real spawned interpreters and real VM programs, including input."""

    def setUp(self):
        self.probe = SessionProbe()
        self.probe.start()
        wait_for(self.probe, "ready")

    def tearDown(self):
        self.probe.close()

    def test_production_worker_rejects_stale_load_and_preserves_session(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from valiance.sessions.worker import SessionWorker
        from valiance.terminal_editor.models import EditorWorkspace

        self.probe.close()
        self.probe = SessionWorker()
        self.probe.start()
        wait_for(self.probe, "ready")
        self.probe.submit("42")
        wait_for(self.probe, "finished")
        with TemporaryDirectory() as directory:
            workspace = EditorWorkspace(Path(directory))
            identity = workspace.active_document
            workspace.update(identity, "1 2 +")
            self.probe.submit(snapshot=workspace.capture(identity))
            prepared = wait_for(self.probe, "prepared")[-1]
            workspace.update(identity, "9")
            self.probe.commit(prepared.request, False)
            wait_for(self.probe, "cancelled")
            self.probe.submit("1 +")
            events = wait_for(self.probe, "finished")
            self.assertEqual(next(e.text for e in events if e.kind == "finished"), "43")
            self.probe.submit(snapshot=workspace.capture(identity))
            prepared = wait_for(self.probe, "prepared")[-1]
            self.probe.commit(prepared.request, True)
            events = wait_for(self.probe, "finished")
            self.assertEqual(next(e.text for e in events if e.kind == "finished"), "9")

    def test_production_worker_delivers_output_before_completion(self):
        from valiance.sessions.worker import SessionWorker

        self.probe.close()
        self.probe = SessionWorker()
        self.probe.start()
        wait_for(self.probe, "ready")
        self.probe.submit('"' + "x" * 200_000 + '" println\n7')
        events = wait_for(self.probe, "finished")
        self.assertEqual(events[-1].kind, "finished")
        self.assertEqual(events[-1].text, "7")
        self.assertTrue(any(e.kind == "output" for e in events))
        self.assertFalse(any(e.kind == "output" for e in self.probe.poll()))

    def test_persistent_variables_definitions_and_stack(self):
        self.probe.submit("$x = 41\ndefine inc(n: Number) -> Number => $n 1 +\n1")
        wait_for(self.probe, "finished")
        self.probe.submit("$x inc")
        events = wait_for(self.probe, "finished")
        result = next(event.text for event in events if event.kind == "finished")
        self.assertEqual(result, "42\n1")

    def test_printed_lines_and_implicit_results_use_separate_events(self):
        self.probe.submit('"a" println\n"b" println\n7')
        events = wait_for(self.probe, "finished")
        self.assertEqual(
            "".join(e.text for e in events if e.kind == "output"), "a\nb\n"
        )
        self.assertEqual(next(e.text for e in events if e.kind == "finished"), "7")

    def test_program_input_is_separate_from_command_submission(self):
        self.probe.submit('"Prompt: " input')
        wait_for(self.probe, "input")
        with self.assertRaises(RuntimeError):
            self.probe.submit("42")
        self.probe.provide_input("hello")
        result = wait_for(self.probe, "finished")
        self.assertEqual(
            "'hello'", next(event.text for event in result if event.kind == "finished")
        )

    def test_infinite_vm_execution_can_be_interrupted_and_reaped(self):
        self.probe.submit("0 while (< 1) => end")
        wait_for(self.probe, "started")
        time.sleep(0.1)
        started = time.monotonic()
        self.assertIn(self.probe.close(), {"interrupted", "terminated"})
        self.assertLess(time.monotonic() - started, 3)

    def test_output_flood_drops_chunks_without_blocking_control(self):
        self.probe.submit('"' + "x" * 400_000 + '" println')
        # Do not drain stdout: completion must still arrive on its own channel.
        deadline = time.monotonic() + 10
        kinds = []
        while time.monotonic() < deadline:
            if self.probe.events.poll(0.05):
                event = self.probe.events.recv()
                kinds.append(event.kind)
                if event.kind in {"finished", "failed"}:
                    break
        self.assertIn("finished", kinds)
        self.assertGreater(self.probe.dropped.value, 0)
        self.probe.busy = False

    def test_blocked_native_stop_is_bounded_and_process_reaped(self):
        self.probe.submit(operation="native-block")
        wait_for(self.probe, "started")
        started = time.monotonic()
        self.assertEqual(self.probe.close(grace=0.1), "terminated")
        self.assertLess(time.monotonic() - started, 3)
        self.assertIsNone(self.probe.process)


if __name__ == "__main__":
    unittest.main()
