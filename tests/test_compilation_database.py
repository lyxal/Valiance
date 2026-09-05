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
if __name__=='__main__': unittest.main()
