import json
import unittest

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


if __name__ == "__main__":
    unittest.main()
