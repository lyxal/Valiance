"""Compile-check every published std.http example against real Valiance types."""
from __future__ import annotations

import unittest

from valiance.analysis import Analyser
from valiance.elements.reference_docs import collect_stdlib_references
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse


class HTTPReferenceExamples(unittest.TestCase):
    def test_every_http_reference_has_compilable_example(self):
        refs = [item for item in collect_stdlib_references()
                if item.qualified_name.startswith('std.http.')]
        self.assertGreaterEqual(len(refs), 17)
        loader = ModuleLoader()
        sources: set[str] = set()
        for ref in refs:
            self.assertEqual(len(ref.overloads), len(ref.overload_documentation))
            for signature, documentation in zip(ref.overloads, ref.overload_documentation, strict=True):
                with self.subTest(signature=signature):
                    self.assertTrue(documentation.examples)
                    for example in documentation.examples:
                        self.assertTrue(example.source.strip())
                        sources.add(example.source)
        for source in sorted(sources):
            with self.subTest(example=source):
                analyzer = Analyser(module_loader=loader)
                analyzer.analyse(parse(source))
                self.assertEqual(analyzer.diagnostics, [])
