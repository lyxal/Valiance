"""Structured incremental explanation and cache command tests."""
from __future__ import annotations
import io, unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from valiance.incremental import ArtifactStore, ReasonCode, RebuildReason, clean_cache, inspect_cache, render_cache_report

class InspectionTests(unittest.TestCase):
    """Verify deterministic explanations and cache integrity operations."""
    def test_reason_identifies_first_dependency(self):
        reason=RebuildReason(ReasonCode.SEMANTIC_DEPENDENCY_CHANGED,"Reanalysed definition",("consumed interface changed",),"root.model")
        self.assertEqual(reason.dependency,"root.model")
        self.assertIn("first changed dependency: root.model",reason.render("app.render"))
    def test_semantic_and_relink_reasons_are_distinct(self):
        semantic=RebuildReason(ReasonCode.SEMANTIC_DEPENDENCY_CHANGED,"Reanalysed module")
        relink=RebuildReason(ReasonCode.IMPLEMENTATION_DEPENDENCY_CHANGED,"Relinked target")
        self.assertNotEqual(semantic.code,relink.code)
    def test_verification_detects_corrupt_object_and_index(self):
        with TemporaryDirectory() as temporary:
            store=ArtifactStore(Path(temporary)); digest=store.put(b"valid")
            store.publish_index("targets",{"build:main":{"artifact":digest}})
            store.object_path(digest).write_bytes(b"corrupt")
            report=inspect_cache(store)
            self.assertFalse(report.valid); self.assertTrue(any(issue.kind=="object" for issue in report.issues))
            (store.indexes/"modules").write_text("not json",encoding="utf-8")
            report=inspect_cache(store)
            self.assertTrue(any(issue.kind=="index" for issue in report.issues))
    def test_clean_preserves_source_and_output(self):
        with TemporaryDirectory() as temporary:
            root=Path(temporary); source=root/"main.vlnc"; output=root/"bin/main.vbc"
            source.write_text("1",encoding="utf-8"); output.parent.mkdir(); output.write_bytes(b"final")
            store=ArtifactStore(root); store.put(b"cache")
            self.assertTrue(clean_cache(store)); self.assertEqual(source.read_text(),"1"); self.assertEqual(output.read_bytes(),b"final")
    def test_rendering_is_deterministic(self):
        with TemporaryDirectory() as temporary:
            store=ArtifactStore(Path(temporary)); a=store.put(b"a"); b=store.put(b"b")
            store.publish_index("targets",{"z":{"artifact":b,"source":"2"},"a":{"artifact":a,"source":"1"}})
            first=render_cache_report(inspect_cache(store)); second=render_cache_report(inspect_cache(store))
            self.assertEqual(first,second); self.assertLess(first.index("target a"),first.index("target z"))
if __name__=="__main__": unittest.main()
