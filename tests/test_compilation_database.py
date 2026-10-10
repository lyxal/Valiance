from __future__ import annotations
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from valiance.incremental import CompilationDatabase

class CompilationDatabaseTests(unittest.TestCase):
 def project(self,root):
  lib=root/'library.vlnc'; main=root/'main.vlnc'; other=root/'other.vlnc'
  (root/'valiance.toml').write_text('[project]\nname="demo"\nversion="1.0.0"\n[dependencies]\n')
  lib.write_text('public define convert(x: Int) -> Int => $x\n')
  main.write_text('import { root.library.convert }\n1 convert')
  other.write_text('public define id(x: Int) -> Int => $x\n')
  return lib,main,other
 def test_overlay_affects_importer_and_close_restores_disk(self):
  with TemporaryDirectory() as t:
   lib,main,_=self.project(Path(t)); db=CompilationDatabase(Path(t)); self.assertFalse(db.analyse(main).analyser.diagnostics)
   db.open_document(lib,'public define convert(x: String) -> String => $x\n'); self.assertTrue(db.analyse(main).analyser.diagnostics)
   db.close_document(lib); self.assertFalse(db.analyse(main).analyser.diagnostics)
 def test_unrelated_overlay_preserves_importer_snapshot(self):
  with TemporaryDirectory() as t:
   _,main,other=self.project(Path(t)); db=CompilationDatabase(Path(t)); before=db.analyse(main)
   db.open_document(other,'public define id(x: Int) -> Int => $x + 1\n'); self.assertIs(before,db.analyse(main))
 def test_failed_current_build_never_returns_stale_executable(self):
  with TemporaryDirectory() as t:
   root=Path(t); src=root/'main.vlnc'; src.write_text('1 2 +'); db=CompilationDatabase(root); db.compile_current(src)
   db.open_document(src,'1 "bad" +'); self.assertRaises(RuntimeError,db.compile_current,src)
 def test_preview_does_not_publish_artifacts(self):
  with TemporaryDirectory() as t:
   root=Path(t); src=root/'main.vlnc'; src.write_text('1 2 +'); db=CompilationDatabase(root); db.open_document(src,'2 3 +'); result=db.compile_current(src)
   self.assertTrue(result.analysis.overlay); self.assertFalse(src.with_suffix('.vbc').exists()); self.assertFalse(src.with_suffix('.vbcm').exists())
 def test_disk_metadata_never_contains_overlay_text(self):
  with TemporaryDirectory() as t:
   root=Path(t); lib,_,_=self.project(root); db=CompilationDatabase(root); db.refresh_disk((lib,)); db.open_document(lib,'SECRET UNSAVED')
   self.assertNotIn('SECRET', (root/'.vln/incremental/workspace-snapshot.json').read_text())

class WorkspaceSnapshotTests(unittest.TestCase):
    """Capture real import closures and reject obsolete execution candidates."""

    def setUp(self):
        from valiance.sessions.service import SessionService
        from valiance.terminal_editor.models import EditorWorkspace

        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.library = self.root / "library.vlnc"
        self.library.write_text("public define value(n: Int) -> Int => 1 end")
        self.workspace = EditorWorkspace(self.root)
        self.session = SessionService()
        self.addCleanup(self.session.close)

    def source_document(self):
        return self.workspace.new_document("import { library.value }\n1 value")

    def test_untitled_local_imports_use_launch_base_and_do_not_create_a_file(self):
        document = self.source_document()
        snapshot = self.workspace.capture(document.id)
        self.assertIsNone(snapshot.root.path)
        result = self.session.prepare(snapshot=snapshot)
        self.assertTrue(result.successful, result.diagnostics)
        outcome = self.session.execute(
            result.prepared, workspace_revision=self.workspace.revision
        )
        self.assertTrue(outcome.successful)
        self.assertEqual(self.session.runtime_stack, [1])
        self.assertEqual([p.name for p in self.root.iterdir()], ["library.vlnc"])

    def test_frozen_import_bytes_and_changed_disk_reject_execution(self):
        document = self.source_document()
        snapshot = self.workspace.capture(document.id)
        self.library.write_text("public define value(n: Int) -> Int => 2 end")
        result = self.session.prepare(snapshot=snapshot)
        self.assertTrue(result.successful, result.diagnostics)
        self.assertFalse(snapshot.disk_is_current())
        self.assertRaises(
            ValueError,
            self.session.execute,
            result.prepared,
            workspace_revision=self.workspace.revision,
        )
        self.assertEqual(self.session.runtime_stack, [])

    def test_import_overlay_is_executed_instead_of_disk_and_edits_invalidate_root(self):
        imported = self.workspace.open_document(self.library)
        self.workspace.update(
            imported.id, "public define value(n: Int) -> Int => 9 end"
        )
        document = self.source_document()
        snapshot = self.workspace.capture(document.id)
        result = self.session.prepare(snapshot=snapshot)
        self.assertTrue(result.successful, result.diagnostics)
        self.session.execute(
            result.prepared, workspace_revision=self.workspace.revision
        )
        self.assertEqual(self.session.runtime_stack, [9])
        self.workspace.update(
            imported.id, "public define value(n: Int) -> Int => 10 end"
        )
        self.assertFalse(self.workspace.is_current(snapshot))
        self.assertNotEqual(
            snapshot.identity, self.workspace.capture(document.id).identity
        )
        self.assertEqual(
            self.workspace.documents[document.id].revision, document.revision
        )

    def test_closed_root_and_newly_created_import_candidate_are_stale(self):
        document = self.source_document()
        snapshot = self.workspace.capture(document.id)
        self.library.with_suffix(".vbcm").write_bytes(b"new artifact")
        self.assertFalse(snapshot.disk_is_current())
        self.workspace.close_document(document.id)
        self.assertFalse(self.workspace.is_current(snapshot))

    def test_loaded_definition_stays_coherent_after_dependency_edit(self):
        document = self.source_document()
        snapshot = self.workspace.capture(document.id)
        result = self.session.prepare(snapshot=snapshot)
        self.assertTrue(result.successful, result.diagnostics)
        self.session.execute(
            result.prepared, workspace_revision=self.workspace.revision
        )
        self.library.write_text("public define value(n: Int) -> Int => 2 end")
        self.assertTrue(self.session.run("1 value").successful)
        self.assertEqual(self.session.runtime_stack, [1, 1])

    def test_project_metadata_and_transitive_imports_are_revision_dependencies(self):
        manifest = self.root / "valiance.toml"
        manifest.write_text('[project]\nname="demo"\nversion="1.0.0"\n')
        child = self.root / "child.vlnc"
        child.write_text("public define child(n: Int) -> Int => $n end")
        self.library.write_text(
            "import { child.child }\npublic define value(n: Int) -> Int => $n child end"
        )
        document = self.source_document()
        snapshot = self.workspace.capture(document.id)
        self.assertTrue(any(file.path == child for file in snapshot.files))
        child.write_text("public define child(n: Int) -> Int => $n 1 + end")
        self.assertFalse(snapshot.disk_is_current())
        snapshot = self.workspace.capture(document.id)
        manifest.write_text('[project]\nname="changed"\nversion="1.0.0"\n')
        self.assertFalse(snapshot.disk_is_current())

    def test_overlay_invalidates_existing_compiled_artifact(self):
        import contextlib
        import io
        from valiance.main import main_vln

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(
                main_vln(["compile-module", "--file", str(self.library)]), 0
            )
        imported = self.workspace.open_document(self.library)
        self.workspace.update(
            imported.id, "public define value(n: Int) -> Int => 9 end"
        )
        document = self.source_document()
        result = self.session.prepare(snapshot=self.workspace.capture(document.id))
        self.assertTrue(result.successful, result.diagnostics)
        self.session.execute(
            result.prepared, workspace_revision=self.workspace.revision
        )
        self.assertEqual(self.session.runtime_stack, [9])

    def test_snapshot_round_trips_through_worker_serialization(self):
        import pickle

        document = self.source_document()
        snapshot = self.workspace.capture(document.id)
        copied = pickle.loads(pickle.dumps(snapshot))
        self.assertEqual(copied, snapshot)
        self.assertEqual(copied.identity, snapshot.identity)


if __name__=='__main__': unittest.main()
