import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

import valiance.vtypes as T
from valiance.elements.builtins import (
    BuiltinElement,
    BuiltinOverload,
    RuntimeContext,
    _CANONICAL_NAME_REGISTRY,
    _DOCUMENTATION_REGISTRY,
    _REGISTRY,
    builtin,
)
from valiance.elements.documentation import element_documentation
from valiance.vtypes.symbols import Symbol

from valiance.elements.reference_docs import (
    collect_builtin_references,
    collect_language_references,
    collect_stdlib_references,
    render_language_reference_html,
    render_language_reference_json,
    render_language_reference_markdown,
)


class ReferenceDocumentationTests(unittest.TestCase):
    def test_sidebar_drills_from_scope_through_group_to_every_entry(self):
        references = collect_language_references()
        page = _ReferenceHTML()
        page.feed(render_language_reference_html(references))
        self.assertEqual(len(page.entry_paths), len(references))
        self.assertEqual({scope for scope, group, target in page.entry_paths}, {
            "built-in", "standard-library", "language",
        })
        self.assertTrue(all(group in page.ids and target in page.ids
                            for scope, group, target in page.entry_paths))
        self.assertEqual(len({target for scope, group, target in page.entry_paths}),
                         len(references))
        self.assertTrue(all(depth == 2 for depth in page.entry_depths))

    def test_html_has_unique_anchors_and_resolvable_internal_links(self):
        references = collect_language_references()
        page = _ReferenceHTML()
        page.feed(render_language_reference_html(references))
        self.assertEqual(len(page.ids), len(set(page.ids)))
        self.assertTrue(page.targets)
        self.assertFalse(set(page.targets) - set(page.ids))
        self.assertEqual(page.articles, len(references))
        self.assertIn("Language — Concurrency", render_language_reference_html(references))

    def test_html_escapes_metadata_and_keeps_symbol_names_distinct(self):
        template = collect_builtin_references()[0]
        references = [
            replace(template, name=name, qualified_name=name, aliases=(),
                    summary='<script>alert("unsafe")</script>',
                    category='<img src=x>', see_also=("a-b",))
            for name in ("a-b", "a.b", "+", "++", "<>")
        ]
        rendered = render_language_reference_html(references, title="<unsafe title>")
        page = _ReferenceHTML()
        page.feed(rendered)
        self.assertEqual(len(page.ids), len(set(page.ids)))
        self.assertNotIn('<script>alert(', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertIn('&lt;unsafe title&gt;', rendered)
        self.assertFalse(set(page.targets) - set(page.ids))

    def test_bundle_is_complete_and_reproducible(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "reference"
            command = [sys.executable, str(root / "scripts/generate_reference_docs.py"),
                       "--output", str(output)]
            subprocess.run(command, cwd=root, check=True, capture_output=True)
            first = {path.name: path.read_bytes() for path in output.iterdir()}
            self.assertEqual(set(first), {
                "index.html", "language-reference.md", "language-reference.json",
            })
            payload = json.loads(first["language-reference.json"])
            self.assertEqual(len(payload["elements"]), len(collect_language_references()))
            subprocess.run(command, cwd=root, check=True, capture_output=True)
            self.assertEqual(first, {path.name: path.read_bytes() for path in output.iterdir()})

    def test_every_builtin_has_documentation_and_aliases_are_grouped(self):
        references = collect_builtin_references()
        by_name = {reference.name: reference for reference in references}

        self.assertIn("dup", by_name)
        self.assertIn("/", by_name)
        self.assertIn("reduce", by_name["/"].aliases)
        self.assertIn("fold", by_name)
        self.assertNotIn("fold", by_name["/"].aliases)
        self.assertIn("len", by_name["length"].aliases)
        self.assertIn("getMessage", by_name["message"].aliases)
        self.assertTrue(all(reference.summary for reference in references))
        self.assertTrue(all(reference.overloads for reference in references))

    def test_builtin_decorator_keeps_documentation_per_overload(self):
        numeric_doc = element_documentation("Handle numbers.")
        string_doc = element_documentation("Handle strings.")
        name = "__documentation_test_element__"

        @builtin(name, (T.Number,), (T.Number,), documentation=numeric_doc)
        def numeric(args: tuple[object, ...], _ctx: RuntimeContext) -> tuple[object, ...]:
            """Return the numeric test argument."""
            return args

        @builtin(name, (T.String,), (T.String,), documentation=string_doc)
        def string(args: tuple[object, ...], _ctx: RuntimeContext) -> tuple[object, ...]:
            """Return the string test argument."""
            return args

        try:
            element = BuiltinElement(Symbol(name), tuple(_REGISTRY[name]))
            self.assertIs(element.documentation_for(element.overloads[0]), numeric_doc)
            self.assertIs(element.documentation_for(element.overloads[1]), string_doc)
        finally:
            _REGISTRY.pop(name, None)
            _DOCUMENTATION_REGISTRY.pop(name, None)
            _CANONICAL_NAME_REGISTRY.pop(name, None)

    def test_every_native_and_valiance_stdlib_function_has_documentation(self):
        references = collect_stdlib_references()
        qualified_names = {reference.qualified_name for reference in references}

        self.assertIn("std.regex.matches", qualified_names)
        self.assertIn("std.testing.assertEqual", qualified_names)
        self.assertIn("std.text.trim", qualified_names)
        self.assertIn("std.text.exclaim", qualified_names)
        self.assertIn("std.arithmetic.square", qualified_names)
        self.assertIn("std.trig.sin", qualified_names)
        self.assertIn("std.grids.allNeighbors", qualified_names)
        self.assertIn("std.random.randbit", qualified_names)
        self.assertIn("std.random.between", qualified_names)
        self.assertIn("std.string.transliterate", qualified_names)
        self.assertIn("std.string.\\Alphabet", qualified_names)
        self.assertTrue(all(reference.summary for reference in references))

    def test_language_reference_renders_html_markdown_and_json(self):
        references = collect_language_references()

        rendered_html = render_language_reference_html(references)
        rendered_markdown = render_language_reference_markdown(references)
        payload = json.loads(render_language_reference_json(references))

        self.assertIn("Valiance Built-ins and Standard Library Reference", rendered_html)
        self.assertIn("std.regex.matches", rendered_html)
        self.assertIn("### `std.regex.matches`", rendered_markdown)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(len(payload["elements"]), len(references))
        self.assertTrue(
            any(item["qualified_name"] == "println" for item in payload["elements"])
        )


class _ReferenceHTML(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.targets = []
        self.articles = 0
        self.entry_paths = []
        self.entry_depths = []
        self.list_stack = []
        self.disclosure_depth = 0

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "details":
            self.disclosure_depth += 1
        if tag == "li":
            self.list_stack.append(attributes)
        if tag == "a" and self.list_stack and "data-entry" in self.list_stack[-1]:
            scope = next(row["data-scope"] for row in self.list_stack if "data-scope" in row)
            group = next(row["data-section"] for row in self.list_stack if "data-section" in row)
            self.entry_paths.append((scope, group, attributes["href"][1:]))
            self.entry_depths.append(self.disclosure_depth)
        if "id" in attributes:
            self.ids.append(attributes["id"])
        if attributes.get("href", "").startswith("#"):
            self.targets.append(attributes["href"][1:])
        if tag == "article":
            self.articles += 1

    def handle_endtag(self, tag):
        if tag == "li":
            self.list_stack.pop()
        if tag == "details":
            self.disclosure_depth -= 1


if __name__ == "__main__":
    unittest.main()
