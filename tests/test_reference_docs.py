import json
import unittest

from valiance.reference_docs import (
    collect_builtin_references,
    collect_language_references,
    collect_stdlib_references,
    render_language_reference_html,
    render_language_reference_json,
    render_language_reference_markdown,
)


class ReferenceDocumentationTests(unittest.TestCase):
    def test_every_builtin_has_documentation_and_aliases_are_grouped(self):
        references = collect_builtin_references()
        by_name = {reference.name: reference for reference in references}

        self.assertIn("dup", by_name)
        self.assertIn("/", by_name)
        self.assertIn("fold", by_name["/"].aliases)
        self.assertIn("len", by_name["length"].aliases)
        self.assertIn("getMessage", by_name["message"].aliases)
        self.assertTrue(all(reference.summary for reference in references))
        self.assertTrue(all(reference.overloads for reference in references))

    def test_every_native_and_valiance_stdlib_function_has_documentation(self):
        references = collect_stdlib_references()
        qualified_names = {reference.qualified_name for reference in references}

        self.assertIn("std.regex.matches", qualified_names)
        self.assertIn("std.testing.assertEqual", qualified_names)
        self.assertIn("std.text.trim", qualified_names)
        self.assertIn("std.text.exclaim", qualified_names)
        self.assertIn("std.arithmetic.square", qualified_names)
        self.assertIn("std.trig.sin", qualified_names)
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


if __name__ == "__main__":
    unittest.main()
