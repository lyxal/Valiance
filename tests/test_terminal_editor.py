"""Behavioral tests for the new Textual editor and execution feasibility tools."""

import asyncio
import os
import time
import unittest

from textual import events
from textual.document._document import Selection
from textual.widgets import ContentSwitcher

from tools.terminal_editor.app import CommandEditor, FeasibilityApp
from tools.terminal_editor.worker import SessionProbe, wait_for
from valiance.terminal_editor.divider import PaneDivider
from valiance.terminal_editor.editor import SourceEmphasis


def _scoped_child(connection, gate):
    """Start an ordinary child only after the parent has attached containment."""
    import subprocess
    import sys

    from valiance.sessions.process_scope import enter_process_scope

    enter_process_scope()
    gate.wait()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    connection.send(child.pid)
    connection.recv()


def _process_alive(pid):
    """Inspect a test-owned child without sending a Windows termination signal."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    from pathlib import Path

    status = Path(f"/proc/{pid}/stat")
    if status.exists() and status.read_text().split(")", 1)[1].split()[0] == "Z":
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


class ProcessScopeTests(unittest.TestCase):
    """Exercise ordinary descendant cleanup without a foreign library fixture."""

    def test_process_scope_reaps_worker_owned_children(self):
        import multiprocessing

        from valiance.sessions.process_scope import ProcessScope

        context = multiprocessing.get_context("spawn")
        connection, writer = context.Pipe()
        gate = context.Event()
        process = context.Process(target=_scoped_child, args=(writer, gate))
        process.start()
        scope = None
        try:
            scope = ProcessScope(process)
            gate.set()
            self.assertTrue(connection.poll(10))
            pid = connection.recv()
            self.assertTrue(_process_alive(pid))
            process.terminate()
            process.join(1)
            scope.close()
            deadline = time.monotonic() + 3
            while _process_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(_process_alive(pid))
        finally:
            if process.is_alive():
                process.terminate()
                process.join(1)
            if scope is not None:
                scope.close()
            process.close()
            connection.close()
            writer.close()


class CompilerInspectionTests(unittest.TestCase):
    """Verify compiler facts, recovery and captured import query semantics."""

    def analyse(self, source, directory=None, path=None, overlays=()):
        from pathlib import Path

        from valiance.analysis.inspection import analyse_sources
        from valiance.incremental.snapshots import RootSource

        root = RootSource("root", source, 7, directory or Path.cwd(), path)
        return analyse_sources((root, *overlays), root, 12)

    def test_completion_fragments_follow_compiler_name_tokens(self):
        from valiance.analysis.completion import completion_end, completion_prefix

        for source in ("empty?", "module.empty?", "$cafe\u0301", "*::dup", "\\dup"):
            with self.subTest(source=source):
                start, prefix = completion_prefix(source, len(source))
                self.assertEqual(start, 0)
                self.assertEqual(prefix, source.replace("e\u0301", "é"))
        self.assertIsNone(completion_prefix('"empty?"', 5))
        self.assertIsNone(completion_prefix("#? empty?", 9))
        self.assertEqual(completion_end("empty?", 3), 6)
        self.assertEqual(completion_end("cafe\u0301", 3), 5)

    def test_completion_filters_stack_groups_overloads_and_does_not_mutate(self):
        source = (
            "define choose(x: Int) -> Int => $x\n"
            "define choose(x: String) -> String => $x\n"
            "1 cho"
        )
        product = self.analyse(source)
        context = product.at(source.rindex("cho")).completion
        self.assertIsNotNone(context)
        items = context.candidates("cho")
        self.assertEqual([item.text for item in items], ["choose"])
        self.assertIn("Int", items[0].meta)
        self.assertNotIn("String", items[0].meta)
        self.assertEqual(context.candidates("cho"), items)
        self.assertEqual(context.candidates("nonexistent"), ())
        empty = self.analyse("cho").at(0).completion
        self.assertEqual(empty.candidates("dup"), ())

    def test_completion_tracks_selected_surviving_world(self):
        source = (
            "define pick(x: Int) -> Int => $x\n"
            "define pick(x: String) -> String => $x\n"
            "define f =>\n pick\n dup\nend\n"
        )
        product = self.analyse(source)
        offset = source.rindex("dup")
        scope = product.scope_at(offset)
        self.assertEqual(len(scope.worlds), 2)
        details = [
            product.at(offset, world.identity).completion.candidates("pic")[0].meta
            for world in scope.worlds
        ]
        self.assertTrue(any("Int" in item and "String" not in item for item in details))
        self.assertTrue(any("String" in item and "Int" not in item for item in details))

    def test_completion_recovers_incomplete_function_and_checks_later_constraints(self):
        source = (
            "define pick(x: Int) -> Int => $x\n"
            "define pick(x: String) -> String => $x\n"
            "define only(x: Int) -> Int => $x\n"
            "define f(x: Int) -> Int =>\n pi\n only\nend\n"
        )
        product = self.analyse(source)
        self.assertTrue(product.diagnostics)
        context = product.completion_at(source.rindex(" pi") + 1).completion
        self.assertIsNotNone(context)
        self.assertEqual([item.text for item in context.candidates("pi")], ["pick"])
        rejected = source.replace("f(x: Int) -> Int", "f(x: String) -> Int")
        product = self.analyse(rejected)
        context = product.completion_at(rejected.rindex(" pi") + 1).completion
        self.assertIsNotNone(context)
        self.assertEqual(context.candidates("pi"), ())

    def test_session_completion_previews_draft_without_execution_or_commit(self):
        from valiance.sessions.service import SessionService

        session = SessionService()
        try:
            prepared = session.prepare("define only(x: Int) -> Int => $x\n1")
            session.execute(prepared.prepared)
            version = session._state_version
            stack = tuple(session.branch.stack.items)
            self.assertEqual(
                [item.text for item in session.complete_draft("on", 2)], ["only"]
            )
            self.assertEqual(session.complete_draft('"string" on', 11), ())
            self.assertEqual(
                [
                    item.text
                    for item in session.complete_draft('println "not executed" du', 25)
                ],
                ["dup"],
            )
            self.assertEqual(tuple(session.branch.stack.items), stack)
            self.assertEqual(session._state_version, version)
            self.assertEqual(session.complete_draft('"du', 3), ())
        finally:
            session.close()

    def test_chain_gaps_variable_types_function_context_and_empty_stack(self):
        source = (
            "object Circle =>\n  $radius: Real\nend\n"
            "define perimeter(c: Circle) -> Real => $c.radius\n"
            "$circle = Circle(2.0)\nprintln perimeter $circle\n\n"
        )
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        operand_gap = source.index(" perimeter $circle") + len(" perimeter")
        gap = product.at(operand_gap)
        self.assertEqual(gap.stack, ("Circle",))
        self.assertEqual(gap.last_element, "$circle: Circle")
        call_gap = source.index("println perimeter") + len("println")
        gap = product.at(call_gap)
        self.assertEqual(gap.stack, ("Real",))
        self.assertIn("perimeter", gap.last_element)
        token = product.at(call_gap + 1)
        self.assertIsNone(token.stack)
        self.assertIn("perimeter", token.element)
        self.assertFalse(token.function)
        self.assertEqual(token.target, (None, 3, 7))
        self.assertEqual(product.at(len(source)).stack, ())

    def test_function_gaps_and_failed_function_do_not_leak_checkpoint_facts(self):
        source = (
            "define broken(x: Int) -> String => $x 2 *\n"
            "define twice(x: Int) -> Int => $x 2 *\n"
        )
        product = self.analyse(source)
        self.assertTrue(product.diagnostics)
        self.assertIsNone(product.at(source.index("$x") + 2).stack)
        self.assertFalse(product.at(source.index("$x")).element)
        offset = source.rindex("$x") + 2
        facts = product.at(offset)
        self.assertEqual(facts.stack, ("Int",))
        self.assertEqual(facts.last_element, "$x: Int")
        self.assertIn("twice(x: Int) -> Int", facts.function)
        self.assertEqual(product.at(source.rindex("2") + 1).stack, ("Int", "Int"))

    def test_lexer_parser_recovery_and_comments_preserve_independent_functions(self):
        source = (
            "`\n$ =\n"
            "define valid(x: Int) -> Int => $x 2 *\n"
            "#? no facts here\n#/ outer #/ inner /# comment /#\n"
        )
        product = self.analyse(source)
        self.assertGreaterEqual(len(product.diagnostics), 2)
        self.assertIsNone(product.at(0).stack)
        self.assertEqual(product.at(source.index("$x") + 2).stack, ("Int",))
        self.assertIsNone(product.at(source.index("no facts")).stack)
        self.assertIsNone(product.at(source.index("inner")).stack)

    def test_import_diagnostics_are_gathered_even_after_other_imports_fail(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.vlnc"
            second = root / "second.vlnc"
            first.write_text("`\n$ =\n")
            second.write_text("define bad -> String => 1\n")
            product = self.analyse("import {first}\nimport {second}\n", root)
            paths = {diagnostic.source_file for diagnostic in product.diagnostics}
            self.assertIn(first, paths)
            self.assertIn(second, paths)
            self.assertTrue(product.workspace.disk_is_current())
            second.write_text("public define value -> Int => 2\n")
            self.assertFalse(product.workspace.disk_is_current())

    def test_unsaved_import_overlay_controls_docs_navigation_and_dependency_types(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from valiance.incremental.snapshots import RootSource

        with TemporaryDirectory() as directory:
            root = Path(directory)
            dependency = root / "library.vlnc"
            dependency.write_text('public define value(x: Int) -> String => "disk"\n')
            overlay = RootSource(
                "dependency",
                "#?? Unsaved value docs.\n"
                "public define value(x: Int) -> Int => $x 2 +\n",
                3,
                root,
                dependency,
            )
            source = "import {library.value}\nprintln value 1\n"
            product = self.analyse(source, root, overlays=(overlay,))
            self.assertFalse(product.diagnostics)
            facts = product.at(source.rindex("value"))
            self.assertIn("Int", facts.element)
            self.assertIn("Unsaved value docs", facts.documentation)
            self.assertEqual(facts.target, (dependency, 1, 14))
            self.assertEqual(product.at(source.rindex("println") + 7).stack, ("Int",))
            suggestions = facts.completion.candidates("val")
            self.assertEqual([item.text for item in suggestions], ["value"])
            self.assertIn("Int", suggestions[0].meta)
            self.assertNotIn("String", suggestions[0].meta)
            qualified = "import {library}\n1 library.val"
            product = self.analyse(qualified, root, overlays=(overlay,))
            context = product.at(qualified.index("library.val")).completion
            self.assertIsNotNone(context)
            self.assertEqual(
                [item.text for item in context.candidates("library.val")],
                ["library.value"],
            )

    def test_inspection_does_not_execute_source_or_alter_ordinary_analysis(self):
        from unittest.mock import patch

        from valiance.analysis import Analyser
        from valiance.parsing import parse

        source = "define twice(x: Int) -> Int => $x 2 *\nprintln twice 4\n"
        with patch(
            "valiance.runtime.vm.VirtualMachine.execute",
            side_effect=AssertionError("executed"),
        ):
            product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        ordinary = Analyser()
        observed = Analyser(observer=lambda *_: None)
        program = parse(source)
        self.assertEqual(ordinary.analyse(program), observed.analyse(program))
        self.assertEqual(ordinary.diagnostics, observed.diagnostics)

    def test_worlds_survive_later_calls_and_return_validation(self):
        prefix = (
            "define pick(x: Int) -> Int => $x\n"
            "define pick(x: String) -> String => $x\n"
            "define only(x: Int) -> Int => $x\n"
        )
        source = prefix + "define f =>\n pick\n dup\nend\n"
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        scope = product.scope_at(source.rindex("pick"))
        self.assertEqual(len(scope.worlds), 2)
        for world in scope.worlds:
            typ = "String" if "String" in world.signature else "Int"
            self.assertIn(
                typ, product.at(source.rindex("pick"), world.identity).element
            )
            self.assertEqual(
                product.at(source.rindex("pick") + 4, world.identity).stack, (typ,)
            )
        source = prefix + "define f =>\n pick\n only\nend\n"
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        scope = product.scope_at(source.rindex("pick"))
        self.assertEqual(len(scope.worlds), 1)
        self.assertIn("Int", product.at(source.rindex("pick")).element)
        source = prefix + "define f -> Int =>\n pick\nend\n"
        product = self.analyse(source)
        scope = product.scope_at(source.rindex("pick"))
        self.assertEqual(len(scope.worlds), 1)
        self.assertIn("Int", scope.worlds[0].signature)
        failed = prefix + "define f -> Boolean =>\n pick\nend\n"
        product = self.analyse(failed)
        self.assertTrue(product.diagnostics)
        self.assertIsNone(product.scope_at(failed.rindex("pick")))
        self.assertFalse(product.at(failed.rindex("pick")).element)

    def test_world_docs_and_identity_survive_whitespace_edits(self):
        source = (
            "#?? Integer choice.\ndefine pick(x: Int) -> Int => $x\n"
            "#?? Text choice.\ndefine pick(x: String) -> String => $x\n"
            "define f =>\n pick\n dup\nend\n"
        )
        first = self.analyse(source)
        scope = first.scope_at(source.rindex("pick"))
        self.assertEqual(len(scope.worlds), 2)
        for world in scope.worlds:
            docs = first.at(source.rindex("pick"), world.identity).documentation
            expected = (
                "Text choice" if "String" in world.signature else "Integer choice"
            )
            self.assertIn(expected, docs)
            row = 3 if "String" in world.signature else 1
            self.assertEqual(
                first.at(source.rindex("pick"), world.identity).target, (None, row, 7)
            )
        edited = "\n" + source.replace(" pick\n", "   pick\n")
        second = self.analyse(edited)
        updated = second.scope_at(edited.rindex("pick"))
        self.assertEqual(scope.identity, updated.identity)
        self.assertEqual(
            [w.identity for w in scope.worlds], [w.identity for w in updated.worlds]
        )
        self.assertEqual(
            second.at(edited.rindex("pick"), "removed"),
            second.at(edited.rindex("pick")),
        )

    def test_selected_local_declaration_shadows_identical_global_signature(self):
        source = (
            "#?? Global docs.\ndefine pick(x: Int) -> Int => $x\n"
            "define f(x: Int) -> Int =>\n"
            " #?? Local docs.\n define pick(x: Int) -> Int => $x\n"
            " pick $x\nend\n"
        )
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        facts = product.at(source.rindex("pick"))
        self.assertIn("Local docs", facts.documentation)
        self.assertNotIn("Global docs", facts.documentation)
        self.assertEqual(facts.target, (None, 4, 8))
        undocumented = source.replace(" #?? Local docs.\n", "\n")
        facts = self.analyse(undocumented).at(undocumented.rindex("pick"))
        self.assertNotIn("Global docs", facts.documentation)
        generic = source.replace(
            "define pick(x: Int) -> Int", "define[T] pick(x: T) -> T"
        )
        facts = self.analyse(generic).at(generic.rindex("pick"))
        self.assertIn("Local docs", facts.documentation)
        self.assertEqual(facts.target, (None, 4, 11))

    def test_nested_anonymous_scopes_and_control_flow_keep_ancestry(self):
        source = "define outer =>\n define inner => +\n inner\nend\n"
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        inner = product.scope_at(source.index("+"))
        outer = product.scope_at(source.rindex("inner"))
        self.assertNotEqual(inner.identity, outer.identity)
        self.assertGreater(len(inner.worlds), 1)
        anonymous = self.analyse("$f = fn => +\n")
        self.assertGreater(len(anonymous.scope_at(11).worlds), 1)
        source = (
            "define f(x: Int) -> Int =>\n if true =>\n"
            "  $x 1 +\n else =>\n  $x 2 +\n end\nend\n"
        )
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        self.assertEqual(len(product.scope_at(source.index("$x")).worlds), 1)
        for operation in ("$x 1", "$x 2"):
            facts = product.at(source.index(operation) + 2)
            self.assertEqual(facts.stack, ("Int",))
            self.assertEqual(facts.locals, (("x", "Int"),))

    def test_generic_calls_locals_and_match_case_checkpoints(self):
        source = (
            "define[T] identity(x: T) -> T => $x\n"
            "define f(x: Int) -> Int =>\n"
            " $local = identity $x\n $local\nend\n"
        )
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        self.assertIn(
            "identity(x: Int) -> Int", product.at(source.rindex("identity")).element
        )
        facts = product.at(source.rindex("$local") + 6)
        self.assertIn(("local", "Int"), facts.locals)
        source = (
            "define f(x: Int) -> Int =>\n $x match =>\n  1 => 2\n  _ => 3\n end\nend\n"
        )
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        self.assertEqual(len(product.scope_at(source.index("match")).worlds), 1)
        for value in ("2", "3"):
            self.assertEqual(product.at(source.index(value) + 1).stack, ("Int",))

    def test_lowered_modifier_worlds_require_committed_parent_choices(self):
        source = "define f(xs: Int+) -> Int+ => map($xs): + 1\n"
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        parent = product.scope_at(source.index("map"))
        child = product.scope_at(source.index("+ 1"))
        self.assertNotEqual(parent.identity, child.identity)
        self.assertEqual(len(parent.worlds), 1)
        self.assertEqual(len(child.worlds), 1)
        self.assertIn("Int+", parent.worlds[0].signature)
        self.assertIn("Int", product.at(source.index("+ 1")).element)
        self.assertNotIn("Real", child.worlds[0].signature)

    def test_unreachable_source_has_no_fabricated_checkpoint(self):
        source = "define f(x: Int) -> Int =>\n return $x\n unknown_name\nend\n"
        product = self.analyse(source)
        self.assertFalse(product.diagnostics)
        facts = product.at(source.index("unknown_name"))
        self.assertIsNone(facts.stack)
        self.assertFalse(facts.element)
        self.assertEqual(facts.unavailable, "Unreachable")

    def test_trace_refinement_merge_budget_and_compilation_parity(self):
        from dataclasses import replace
        from pathlib import Path

        import valiance.vtypes as T
        from valiance.analysis import Analyser
        from valiance.analysis.inspection import analyse_sources
        from valiance.analysis.state.branches import AnalysisBranch, BranchSet
        from valiance.analysis.traces import TraceCollector
        from valiance.incremental.snapshots import RootSource
        from valiance.parsing import parse

        source = "define f => +\n"
        nodes = parse(source)
        plain, traced = Analyser(), Analyser(trace=TraceCollector())
        self.assertEqual(plain.analyse(nodes), traced.analyse(nodes))
        self.assertEqual(plain.diagnostics, traced.diagnostics)
        trace = TraceCollector()
        branch = AnalysisBranch(stack=T.TypeStack((T.V("X"),)), trace=trace.add())
        branch = replace(branch, trace=trace.checkpoint(nodes[0], branch, branch))
        refined = branch.refine_type(T.V("X"), T.Int)
        facts = list(trace.checkpoints(refined.trace))
        self.assertEqual(facts[0][2].stack.items, (T.Int,))
        other = replace(branch, trace=trace.add())
        merged = BranchSet.collect((branch, other))
        self.assertEqual(len(merged), 1)
        self.assertEqual(len(merged.branches[0].trace.parents), 2)
        root = RootSource("root", source, 1, Path.cwd())
        limited = analyse_sources((root,), root, 1, trace_limit=2)
        self.assertTrue(limited.trace_limited)
        self.assertFalse(limited.scopes)
        self.assertFalse(limited.diagnostics)
        self.assertIsNone(limited.at(source.index("+")).stack)


class EditorAnalysisWorkerTests(unittest.IsolatedAsyncioTestCase):
    """Protect coalescing and stale-result gates independently of terminal rendering."""

    async def test_delayed_requests_cannot_restore_root_or_dependency_facts(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from threading import Event
        from types import SimpleNamespace
        from unittest.mock import patch

        from valiance.analysis.inspection import analyse_sources
        from valiance.terminal_editor.analysis import EditorAnalysis
        from valiance.terminal_editor.models import EditorWorkspace

        with TemporaryDirectory() as directory:
            workspace = EditorWorkspace(Path(directory))
            root_id = workspace.active_document
            workspace.update(root_id, "1")
            dependency = workspace.new_document("2")
            workspace.active_document = root_id
            app = SimpleNamespace(
                workspace=workspace,
                editor_screen=SimpleNamespace(query=lambda _: ()),
                refresh_inspection=lambda: None,
            )
            controller = EditorAnalysis(app)
            started, release = Event(), Event()
            requests = []

            def delayed(roots, root, revision):
                requests.append((root.source, revision))
                if len(requests) == 1:
                    started.set()
                    release.wait(3)
                return analyse_sources(roots, root, revision)

            with patch("valiance.terminal_editor.analysis.analyse_sources", delayed):
                controller.poll()
                self.assertTrue(await asyncio.to_thread(started.wait, 2))
                workspace.update(root_id, "3")
                controller.poll()
                workspace.update(dependency.id, "4")
                controller.poll()
                self.assertIsNone(controller.snapshot)
                self.assertEqual(len(requests), 1)
                controller.started -= 0.6
                self.assertEqual(controller.label, "Updating…")
                release.set()
                await controller.task
                self.assertIsNone(controller.snapshot)
                controller.poll()
                await controller.task
                self.assertEqual(len(requests), 2)
                self.assertEqual(controller.snapshot.workspace.root.source, "3")
                self.assertEqual(
                    controller.snapshot.workspace.workspace_revision, workspace.revision
                )
                workspace.update(dependency.id, "5")
                controller.poll()
                self.assertIsNone(controller.snapshot)
                await controller.task
                await controller.close()


class EditorInspectionApplicationTests(unittest.IsolatedAsyncioTestCase):
    """Check real cursor rendering and diagnostic keyboard/source navigation."""

    async def settled(self, app, pilot):
        deadline = time.monotonic() + 5
        while app.analysis.snapshot is None:
            if app.analysis.failure:
                self.fail(app.analysis.failure)
            if time.monotonic() > deadline:
                self.fail("analysis did not settle")
            await pilot.pause(0.02)
        await pilot.pause()

    async def test_selected_error_minimizes_restores_brightness_and_edits_dismiss(self):
        from valiance.terminal_editor.app import EditorApp

        app = EditorApp(watch_files=False, with_worker=False)
        async with app.run_test(size=(80, 24)) as pilot:
            app.source_editor.load_text("1 unknown_name\n2\n")
            await pilot.pause()
            await self.settled(app, pilot)
            await pilot.press("f9")
            await pilot.pause()
            self.assertTrue(app._error_expanded)
            self.assertIsNotNone(app.source_editor.selected_error)
            app.source_editor.move_cursor((1, 0))
            await pilot.pause()
            self.assertFalse(app._error_expanded)
            self.assertIsNone(app.source_editor.selected_error)
            self.assertTrue(app.query_one("#error-pin").display)
            app.expand_error_pressed()
            self.assertTrue(app._error_expanded)
            await pilot.press("3")
            await pilot.pause()
            self.assertIsNone(app._selected_diagnostic)
            self.assertFalse(app.query_one("#error-pin").display)
            await self.settled(app, pilot)
            await pilot.press("f9")
            await pilot.pause()
            app.source_editor.action_undo()
            await pilot.pause()
            self.assertIsNone(app._selected_diagnostic)
            await self.settled(app, pilot)
            await pilot.press("f9")
            await pilot.pause()
            app.source_editor.action_redo()
            await pilot.pause()
            self.assertIsNone(app._selected_diagnostic)

    async def test_grouped_import_errors_reuse_open_document_and_keep_source_focus(
        self,
    ):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from textual.widgets import OptionList

        from valiance.terminal_editor.app import EditorApp

        with TemporaryDirectory() as directory:
            root = Path(directory)
            dependency = root / "library.vlnc"
            dependency.write_text("public define broken(x: Int) -> String => $x\n")
            main = root / "main.vlnc"
            main.write_text("import {library.broken}\n")
            app = EditorApp(launch_directory=root, watch_files=False, with_worker=False)
            async with app.run_test(size=(120, 40)) as pilot:
                imported = app.files.open(dependency)
                await app._mount_document(imported.id)
                document = app.files.open(main)
                await app._mount_document(document.id)
                await self.settled(app, pilot)
                self.assertTrue(app.source_editor.has_focus)
                self.assertFalse(app.query_one("#error-list").display)
                listing = app.query_one("#error-list", OptionList)
                self.assertTrue(
                    any(
                        listing.get_option_at_index(i).disabled
                        for i in range(listing.option_count)
                    )
                )
                index = next(
                    i
                    for i, item in enumerate(app.analysis.snapshot.diagnostics)
                    if item.source_file == dependency
                )
                app._navigate_diagnostic(index)
                await app.workers.wait_for_complete()
                await pilot.pause()
                self.assertEqual(app.workspace.active_document, imported.id)
                self.assertEqual(
                    sum(
                        item.path == dependency
                        for item in app.workspace.documents.values()
                    ),
                    1,
                )
                self.assertTrue(app._error_expanded)
                app.source_editor.load_text(
                    "public define fixed(x: Int) -> Int => $x\n"
                )
                await pilot.pause()
                self.assertIsNone(app._selected_diagnostic)
                await self.settled(app, pilot)
                self.assertFalse(app.query_one("#show-errors").display)

    async def test_completion_threshold_keyboard_precedence_and_stale_results(self):
        from valiance.sessions.events import CompletionItem
        from valiance.terminal_editor.app import EditorApp

        app = EditorApp(watch_files=False, with_worker=False)
        async with app.run_test(size=(80, 24)) as pilot:
            source = (
                "\n".join(f"define candidate{i}(x: Int) -> Int => $x" for i in range(9))
                + "\n1 cand"
            )
            editor = app.source_editor
            editor.load_text(source)
            editor.move_cursor(editor.document.end)
            await pilot.pause()
            await self.settled(app, pilot)
            await pilot.pause(0.3)
            self.assertFalse(app.completion.listing.display)
            await pilot.press("ctrl+space")
            await pilot.pause(0.5)
            self.assertTrue(
                app.completion.listing.display,
                (
                    app.focused,
                    app.completion.key,
                    app.completion.explicit,
                    app.completion.task.exception()
                    if app.completion.task and app.completion.task.done()
                    else None,
                ),
            )
            self.assertEqual(len(app.completion.items), 9)
            cursor = editor.cursor_location
            await pilot.press("down")
            self.assertEqual(editor.cursor_location, cursor)
            self.assertEqual(app.completion.listing.highlighted, 1)
            await pilot.press("escape")
            self.assertFalse(app.completion.listing.display)
            await pilot.pause(0.2)
            self.assertFalse(app.completion.listing.display)
            await pilot.press("ctrl+space")
            await pilot.pause(0.5)
            await pilot.press("tab")
            self.assertTrue(editor.text.endswith("candidate0"))
            editor.action_undo()
            await pilot.pause()
            self.assertTrue(editor.text.endswith("cand"))
            old_key = app.completion.key
            editor.insert("x")
            await pilot.pause()
            app.completion.poll()
            app.completion.show((CompletionItem("wrong", ""),), old_key)
            self.assertFalse(app.completion.listing.display)

    async def test_completion_in_locked_repl_overrides_history_but_not_enter(self):
        from valiance.terminal_editor.app import EditorApp
        from valiance.terminal_editor.runtime import CommandInput

        app = EditorApp(watch_files=False)
        async with app.run_test(size=(120, 40)) as pilot:
            deadline = time.monotonic() + 10
            while not app.runtime.worker.ready:
                self.assertLess(time.monotonic(), deadline)
                await pilot.pause(0.05)
            app.runtime.worker.submit("1")
            while app.runtime.worker.busy:
                self.assertLess(time.monotonic(), deadline)
                await pilot.pause(0.05)
            editor = app.query_one("#command", CommandInput)
            app.state.input.history = ["history"]
            editor.load_text("du")
            editor.move_cursor((0, 2))
            editor.focus()
            editor.toggle_lock()
            await pilot.pause()
            deadline = time.monotonic() + 10
            while not app.completion.listing.display:
                self.assertLess(time.monotonic(), deadline)
                await pilot.pause(0.05)
            await pilot.press("down")
            self.assertEqual(editor.text, "du")
            self.assertEqual(editor.cursor_location, (0, 2))
            await pilot.press("escape")
            self.assertTrue(app.state.input.locked)
            await pilot.press("ctrl+space")
            deadline = time.monotonic() + 10
            while not app.completion.listing.display:
                self.assertLess(time.monotonic(), deadline)
                await pilot.pause(0.05)
            await pilot.press("enter")
            self.assertEqual(editor.text, "du\n")
            self.assertTrue(app.state.input.locked)

    async def test_automatic_completion_in_wrapped_unicode_source_joins_undo(self):
        from valiance.terminal_editor.app import EditorApp

        app = EditorApp(watch_files=False, with_worker=False)
        async with app.run_test(size=(80, 24)) as pilot:
            editor = app.source_editor
            prefix = '"λ" ' + " " * 90
            editor.load_text(prefix)
            editor.move_cursor(editor.document.end)
            await pilot.press("d", "u")
            await self.settled(app, pilot)
            deadline = time.monotonic() + 10
            while not app.completion.listing.display:
                self.assertLess(time.monotonic(), deadline)
                await pilot.pause(0.05)
            self.assertEqual([item.text for item in app.completion.items], ["dup"])
            await pilot.press("tab")
            self.assertEqual(editor.text, prefix + "dup")
            editor.action_undo()
            await pilot.pause()
            self.assertEqual(editor.text, prefix)
            editor.action_redo()
            await pilot.pause()
            self.assertEqual(editor.text, prefix + "dup")

    async def test_world_pages_keyboard_sync_and_silent_fallback(self):
        from textual.widgets import Static
        from valiance.terminal_editor.app import EditorApp

        source = (
            "define pick(x: Int) -> Int => $x\n"
            "define pick(x: String) -> String => $x\n"
            "define only(x: Int) -> Int => $x\n"
            "define f =>\n pick\n dup\nend\n"
        )
        app = EditorApp(watch_files=False, with_worker=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.source_editor.load_text(source)
            await pilot.pause()
            await self.settled(app, pilot)
            app.source_editor.move_cursor((4, 2))
            await pilot.pause()
            self.assertTrue(app.query_one("#world-pages").display)
            await pilot.press("alt+right")
            self.assertIn(
                "String", str(app.query_one("#inspection-facts", Static).content)
            )
            app.source_editor.move_cursor((5, 0))
            await pilot.pause()
            self.assertIn(
                "String", str(app.query_one("#inspection-facts", Static).content)
            )
            self.assertTrue(app.source_editor.has_focus)
            await pilot.click("#world-pages", offset=(11, 0))
            self.assertIn(
                "Int", str(app.query_one("#inspection-facts", Static).content)
            )
            await pilot.press("alt+right")
            app.source_editor.move_cursor((4, 2))
            await pilot.pause()
            await pilot.press("f12")
            self.assertEqual(app.source_editor.cursor_location, (1, 7))
            app.source_editor.load_text(source.replace(" dup\n", " only\n"))
            await pilot.pause()
            await self.settled(app, pilot)
            app.source_editor.move_cursor((4, 2))
            await pilot.pause()
            self.assertFalse(app.query_one("#world-pages").display)
            self.assertIn(
                "Int", str(app.query_one("#inspection-facts", Static).content)
            )

    async def test_caret_views_errors_and_definition_navigation(self):
        from textual.widgets import Static

        from valiance.terminal_editor.app import EditorApp

        app = EditorApp(watch_files=False, with_worker=False)
        async with app.run_test(size=(120, 40)) as pilot:
            app.source_editor.load_text(
                "define twice(x: Int) -> Int => $x 2 *\nprintln twice 4\n"
            )
            await pilot.pause()
            await self.settled(app, pilot)
            app.source_editor.move_cursor((1, 7))
            await pilot.pause()
            rendered = str(app.query_one("#inspection-facts", Static).content)
            self.assertIn("Stack", rendered)
            self.assertIn("Int", rendered)
            app.source_editor.move_cursor((1, 8))
            await pilot.pause()
            rendered = str(app.query_one("#inspection-facts", Static).content)
            self.assertIn("Selected element", rendered)
            self.assertNotIn("Stack", rendered)
            await pilot.press("f12")
            self.assertEqual(app.source_editor.cursor_location, (0, 7))
            app.source_editor.load_text("unknown_name\n")
            await pilot.pause()
            await self.settled(app, pilot)
            self.assertTrue(app.query_one("#show-errors").display)
            self.assertFalse(app.query_one("#error-list").display)
            self.assertTrue(app.source_editor._emphasis)
            await pilot.click(app.source_editor, offset=(6, 0))
            self.assertIn(
                "unknown",
                str(app.query_one("#diagnostic-detail", Static).content),
            )
            await pilot.press("f9")
            self.assertEqual(app.source_editor.cursor_location, (0, 0))
            self.assertIn(
                "unknown", str(app.query_one("#diagnostic-detail", Static).content)
            )
            await pilot.press("f11")
            self.assertTrue(app.query_one("#error-list").display)
            app.source_editor.load_text("1\n")
            await pilot.pause()
            await self.settled(app, pilot)
            self.assertFalse(app.query_one("#show-errors").display)
            self.assertFalse(app.source_editor._emphasis)


class DocumentFileTests(unittest.TestCase):
    """Protect saved bytes, document identities and imported overlays."""

    def setUp(self):
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from valiance.terminal_editor.files import DocumentFiles
        from valiance.terminal_editor.models import EditorWorkspace

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
        data = '"界"\r\n1\r\n'.encode()
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
            command.toggle_lock()
            self.assertTrue(app.state.input.locked)
            command.load_text("hello")
            await pilot.press("enter")
            await self.wait_runtime(pilot, lambda: not app.runtime.worker.busy)
            self.assertEqual(app.state.transcript.entries[-1].text, "'hello'\n")
            self.assertEqual(app.state.input.history, [])
            self.assertTrue(app.state.input.locked)
            app.source_editor.load_text("while (true) => end")
            app.source_editor.focus()
            await pilot.press("f5")
            await self.wait_runtime(pilot, lambda: app.runtime.label == "Running file")
            request = app.runtime.worker.request
            await pilot.press("f5")
            self.assertEqual(app.runtime.worker.request, request)
            app.source_editor.focus()
            await pilot.press("a")
            self.assertTrue(app.source_editor.text.startswith("a"))
            await pilot.press("ctrl+c")
            await self.wait_runtime(
                pilot, lambda: not app.runtime.closing and app.runtime.worker.ready
            )
            self.assertIsNone(app.state.loaded_source)
            self.assertTrue(app.state.input.locked)
            self.assertTrue(
                any("Stopped" in entry.text for entry in app.state.transcript.entries)
            )
        self.assertIsNone(app.runtime.worker.process)

    async def test_output_flood_keeps_retention_and_editor_usable(self):
        from valiance.terminal_editor.runtime import EditorRuntime
        from valiance.terminal_editor.transcript import TranscriptView

        app = self.app
        app.runtime = EditorRuntime(app)
        async with app.run_test(size=(120, 40)) as pilot:
            app.source_editor.load_text('"' + "α界" * 100_000 + '" println\n7')
            await pilot.press("f5")
            await self.wait_runtime(
                pilot,
                lambda: (
                    app.state.loaded_source is not None and not app.runtime.worker.busy
                ),
            )
            self.assertEqual(app.state.transcript.entries[-1].text, "7\n")
            self.assertLessEqual(app.state.transcript.characters, 64_000)
            self.assertLessEqual(len(app.state.transcript.entries), 300)
            view = app.query_one("#transcript", TranscriptView)
            self.assertEqual(view.text, app.state.transcript.export_text())
            self.assertIn("dropped", view.text)
            app.source_editor.focus()
            await pilot.press("a")
            self.assertTrue(app.source_editor.text.startswith("a"))

    async def test_repl_lock_history_and_paste_preserve_the_draft(self):
        from valiance.terminal_editor.models import InputState
        from valiance.terminal_editor.runtime import CommandInput

        app = self.app
        app.state.input = InputState(history=["1", "2\n3"])
        async with app.run_test(size=(120, 40)) as pilot:
            command = app.query_one("#command", CommandInput)
            command.focus()
            command.load_text("unfinished")
            command.selection = Selection((0, 2), (0, 5))
            draft = command.draft()
            await pilot.press("up")
            self.assertEqual(command.text, "2\n3")
            await pilot.press("down")
            self.assertEqual(command.draft(), draft)
            for key in ("ctrl+enter", "shift+enter"):
                await pilot.press(key)
                self.assertEqual(command.draft(), draft)
            self.assertFalse(app.state.input.locked)
            await pilot.press("ctrl+enter")
            self.assertIn("locked", str(command.border_title))
            command.load_text("if (true) =>")
            command.move_cursor(command.document.end)
            await pilot.press("enter")
            self.assertEqual(command.text, "if (true) =>\n  ")
            await pilot.press("enter")
            self.assertEqual(command.text, "if (true) =>\n  \n  ")
            command.move_cursor((0, 0))
            await pilot.press("up")
            self.assertTrue(command.text.startswith("if"))
            command.post_message(events.Paste("\n4\n5"))
            await pilot.pause()
            self.assertIn("\n4\n5", command.text)
            self.assertEqual(app.state.input.history, ["1", "2\n3"])
            await pilot.click("#enter-lock")
            self.assertFalse(app.state.input.locked)

    async def test_retained_transcript_styles_selection_clear_and_export(self):
        from unittest.mock import patch

        from valiance.terminal_editor.models import Transcript, TranscriptEntry
        from valiance.terminal_editor.transcript import TranscriptView

        app = self.app
        app.state.transcript = Transcript(max_entries=3, max_characters=30)
        async with app.run_test(size=(120, 40)) as pilot:
            view = app.query_one("#transcript", TranscriptView)
            for kind, text in (
                ("command", "> 7\n"),
                ("output", "printed\n"),
                ("result", "7\n"),
            ):
                app.state.transcript.append(TranscriptEntry(kind, text))
            view.refresh_transcript(app.state.transcript)
            await pilot.pause()
            self.assertEqual(view.text, "> 7\nprinted\n7\n")
            self.assertIn(
                view.STYLES["result"],
                [str(span.style) for span in view.get_line(2).spans],
            )
            self.assertNotIn(
                view.STYLES["result"],
                [str(span.style) for span in view.get_line(1).spans],
            )
            view.selection = Selection((1, 0), (1, 7))
            view.focus()
            with patch.object(app, "copy_to_clipboard") as copy:
                await pilot.press("ctrl+c")
                copy.assert_called_once_with("printed")
            app.state.transcript.append(TranscriptEntry("output", "more\n"))
            view.refresh_transcript(app.state.transcript)
            await pilot.pause()
            self.assertIn("dropped", view.text)
            self.assertEqual(view.selected_text, "printed")
            path = self.root / "transcript.txt"
            app.action_save_transcript()
            await self.choose_path(pilot, path)
            self.assertEqual(
                path.read_text(encoding="utf-8"), app.state.transcript.export_text()
            )
            app.action_clear_transcript()
            self.assertEqual(view.text, "")
            self.assertEqual(app.state.transcript.export_text(), "")
            app.state.transcript = Transcript(max_entries=1)
            for kind in ("output", "result", "output"):
                app.state.transcript.append(TranscriptEntry(kind, "7\n"))
                view.refresh_transcript(app.state.transcript)
                await pilot.pause()
                row = 1 if app.state.transcript.truncated else 0
                styles = [str(span.style) for span in view.get_line(row).spans]
                self.assertIn(view.STYLES[kind], styles)

    async def test_compact_load_clear_reset_fault_and_history_persistence(self):
        from valiance.terminal_editor.app import EditorApp
        from valiance.terminal_editor.runtime import CommandInput, EditorRuntime

        app = self.app
        app.config_path = self.root / "preferences.json"
        app.runtime = EditorRuntime(app)
        async with app.run_test(size=(80, 24)) as pilot:
            command = app.query_one("#command", CommandInput)
            app.source_editor.load_text(
                "$x = 40\ndefine inc(n: Number) -> Number => $n 1 +\n1"
            )
            await pilot.press("f5")
            await self.wait_runtime(
                pilot,
                lambda: (
                    app.state.loaded_source is not None and not app.runtime.worker.busy
                ),
            )
            self.assertIs(app.focused, command)
            self.assertTrue(app.query_one("#repl").display)
            loaded = app.state.loaded_source
            command.load_text("$x inc")
            await pilot.press("enter")
            await self.wait_runtime(pilot, lambda: not app.runtime.worker.busy)
            self.assertEqual(app.state.transcript.entries[-1].text, "41\n1\n")
            command.load_text(":scratch")
            await pilot.press("enter")
            self.assertIs(app.focused, app.source_editor)
            self.assertEqual(app.state.loaded_source, loaded)
            app.action_file_action("new")
            await pilot.pause()
            self.assertEqual(app.state.loaded_source, loaded)
            app.source_editor.load_text("unknown_word")
            app.reveal_repl()
            await pilot.pause()
            await pilot.press("f5")
            await self.wait_runtime(pilot, lambda: app.runtime.label == "Run failed")
            self.assertEqual(app.state.loaded_source, loaded)
            self.assertIs(app.focused, app.source_editor)
            self.assertTrue(app.query_one("#load-errors").display)
            self.assertEqual(
                sum(entry.kind == "load" for entry in app.state.transcript.entries), 1
            )
            app.reveal_repl()
            self.assertTrue(app.query_one("#load-errors").display)
            command.load_text("define broken(")
            await pilot.press("enter")
            await self.wait_runtime(pilot, lambda: app.runtime.label == "Run failed")
            self.assertEqual(command.text, "define broken(")
            app.action_clear_transcript()
            command.load_text("$x")
            await pilot.press("enter")
            await self.wait_runtime(pilot, lambda: not app.runtime.worker.busy)
            self.assertEqual(app.state.transcript.entries[-1].text, "40\n41\n1\n")
            command.toggle_lock()
            command.load_text("draft")
            history = list(app.state.input.history)
            app.action_reset_session()
            await self.wait_runtime(
                pilot,
                lambda: (
                    not app.runtime.closing
                    and app.runtime.worker.ready
                    and app.state.loaded_source is None
                ),
            )
            self.assertFalse(app.state.transcript.entries)
            self.assertTrue(app.state.input.locked)
            self.assertEqual(command.text, "draft")
            self.assertEqual(app.state.input.history, history)
            app.source_editor.load_text(
                '"before fault" println\nValueFault("failure") panic'
            )
            await pilot.press("f5")
            await self.wait_runtime(pilot, lambda: app.runtime.label == "Run failed")
            self.assertIsNone(app.state.loaded_source)
            self.assertTrue(app.state.input.locked)
            exported = app.state.transcript.export_text()
            self.assertIn("before fault", exported)
            self.assertIn("Runtime fault; session reset", exported)
            await self.wait_runtime(
                pilot, lambda: not app.runtime.closing and app.runtime.worker.ready
            )
            command.toggle_lock()
            command.load_text("$x")
            command.focus()
            await pilot.press("enter")
            await self.wait_runtime(pilot, lambda: not app.runtime.worker.busy)
            self.assertEqual(app.runtime.label, "Run failed")
            history = list(app.state.input.history)
        restored = EditorApp(
            launch_directory=self.root,
            config_path=app.config_path,
            with_worker=False,
            watch_files=False,
        )
        self.assertEqual(restored.state.input.history, history)
        self.assertFalse(restored.state.input.locked)
        self.assertIsNone(restored.state.loaded_source)
        self.assertEqual(
            restored.workspace.documents[restored.workspace.active_document].source, ""
        )

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
        from textual.document._document import Selection
        from textual.widgets import Input

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
        from textual.document._document import Selection
        from textual.widgets import Input

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

        from valiance.sessions.service import SessionService
        from valiance.terminal_editor.models import EditorState, EditorWorkspace

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
        from valiance.terminal_editor.models import DocumentView, InputDraft, InputState

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

    def test_production_worker_preview_allows_submission_and_preserves_stack(self):
        from valiance.sessions.worker import SessionWorker

        self.probe.close()
        self.probe = SessionWorker()
        self.probe.start()
        wait_for(self.probe, "ready")
        self.probe.submit("1")
        wait_for(self.probe, "finished")
        request = self.probe.complete("du", 2)
        self.assertFalse(self.probe.busy)
        self.probe.submit("2 +")
        events = wait_for(self.probe, "finished")
        preview = next(item for item in events if item.kind == "completion")
        self.assertEqual(preview.request, request)
        self.assertIn("dup", [item.text for item in preview.items])
        self.assertEqual(events[-1].text, "3")

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

    def test_production_worker_session_queries_and_runtime_branches(self):
        from valiance.sessions.worker import SessionWorker

        self.probe.close()
        self.probe = SessionWorker()
        self.probe.start()
        wait_for(self.probe, "ready")
        self.probe.submit("$x = 40\n1")
        wait_for(self.probe, "finished")
        self.probe.submit(":type $x 2 +")
        events = wait_for(self.probe, "finished")
        self.assertTrue(
            any("Stack types" in e.text for e in events if e.kind == "notice")
        )
        self.probe.submit(":branch")
        events = wait_for(self.probe, "finished")
        self.assertTrue(any("depth 1" in e.text for e in events))
        self.probe.submit("2")
        wait_for(self.probe, "finished")
        self.probe.submit(":copy 1")
        wait_for(self.probe, "finished")
        self.probe.submit(":restore")
        wait_for(self.probe, "finished")
        self.probe.submit("$x")
        events = wait_for(self.probe, "finished")
        self.assertEqual(events[-1].text, "40\n2\n1")
        self.probe.submit(":restore")
        events = wait_for(self.probe, "failed")
        self.assertTrue(any("requires an open" in e.text for e in events))
        self.assertFalse(any(e.kind == "reset" for e in events))
        self.probe.submit("1 +")
        events = wait_for(self.probe, "finished")
        self.assertEqual(events[-1].text, "41\n2\n1")

    @unittest.skipIf(
        os.name == "nt" and os.environ.get("GITHUB_ACTIONS") != "true",
        "Windows Valiance FFI fixtures are validated in CI, not locally",
    )
    def test_production_worker_native_stop_reaps_foreign_children(self):
        import sys
        from contextlib import ExitStack
        from pathlib import Path

        from tests.ffi_support import (
            build_shared_library,
            native_library_directory,
            shared_library_path,
        )
        from valiance.sessions.worker import SessionWorker

        self.probe.close()
        self.probe = SessionWorker()
        self.probe.start()
        wait_for(self.probe, "ready")
        with native_library_directory() as directory, ExitStack() as cleanup:
            cleanup.callback(self.probe.close)
            root = Path(directory)
            fixture = root / "session.c"
            library = shared_library_path(directory, "session")
            marker = root / "entered.txt"
            fixture.write_text(
                "#include <stdio.h>\n#include <stdint.h>\n"
                "#ifdef _WIN32\n#include <windows.h>\n#include <process.h>\n"
                "#else\n#include <unistd.h>\n#endif\n"
                "FFI_EXPORT int block(const char *python, const char *marker) {\n"
                "  long long pid;\n"
                "#ifdef _WIN32\n"
                '  intptr_t child = _spawnl(_P_NOWAIT, python, python, "-c", '
                "\"__import__('time').sleep(60)\", NULL);\n"
                "  pid = child == -1 ? -1 : (long long)GetProcessId((HANDLE)child);\n"
                "  if (child != -1) CloseHandle((HANDLE)child);\n"
                "#else\n"
                "  pid = fork();\n"
                '  if (pid == 0) { execl(python, python, "-c", '
                "\"__import__('time').sleep(60)\", NULL); _exit(1); }\n"
                "#endif\n"
                '  FILE *stream = fopen(marker, "w");\n'
                "  if (!stream) return -1;\n"
                '  fprintf(stream, "%lld", pid); fclose(stream);\n'
                "#ifdef _WIN32\n  Sleep(60000);\n"
                "#else\n  sleep(60);\n#endif\n  return 1;\n}\n",
                encoding="utf-8",
            )
            build_shared_library(str(fixture), library)
            self.probe.submit(
                f'import {{ffi("{library}") as native}}\n'
                "link native.block(&CString, &CString) -> &int as block\n"
                f'"{Path(sys.executable).as_posix()}" to[&CString] '
                f'"{marker.as_posix()}" to[&CString] block'
            )
            deadline = time.monotonic() + 10
            observed = []
            while not marker.exists() and time.monotonic() < deadline:
                observed.extend(self.probe.poll())
                time.sleep(0.01)
            self.assertTrue(marker.exists(), observed)
            pid = int(marker.read_text())
            self.assertGreater(pid, 0)
            self.assertTrue(_process_alive(pid))
            started = time.monotonic()
            self.assertEqual(self.probe.close(grace=0.1), "terminated")
            self.assertLess(time.monotonic() - started, 3)
            deadline = time.monotonic() + 3
            while _process_alive(pid) and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertFalse(_process_alive(pid))
            self.assertIsNone(self.probe.process)

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
