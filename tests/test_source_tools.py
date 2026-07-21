import tempfile
import unittest
from pathlib import Path

from valiance.source_tools import (
    add_missing_docstrings,
    extract_documented_defines,
    format_source,
    project_source_files,
    render_html_reference,
)


class SourceToolsTests(unittest.TestCase):
    def test_add_missing_docstrings_generates_structured_stub(self):
        source = "define[T] pair(value: T) -> T, T => $value $value\n"

        rendered = add_missing_docstrings(source)

        self.assertEqual(
            rendered,
            "#?? TODO: Describe `pair`.\n"
            "#??\n"
            "#?? @typeparam T TODO: Describe `T`.\n"
            "#?? @param value TODO: Describe `value`.\n"
            "#?? @returns TODO: Describe the returned stack value(s).\n"
            "define[T] pair(value: T) -> T, T => $value $value\n",
        )

    def test_add_missing_docstrings_preserves_existing_doc_comment(self):
        source = "#?? Existing docs.\ndefine value -> Number => 1\n"

        self.assertEqual(add_missing_docstrings(source), source)

    def test_add_missing_docstrings_places_stub_before_annotations(self):
        source = '@warn("old")\ndefine value -> Number => 1\n'

        rendered = add_missing_docstrings(source)

        self.assertTrue(rendered.startswith("#?? TODO: Describe `value`."))
        self.assertLess(rendered.index("#??"), rendered.index("@warn"))

    def test_add_missing_docstrings_finds_nested_defines(self):
        source = "object Counter =>\ndefine value -> Number => 1\nend\n"

        rendered = add_missing_docstrings(source)

        self.assertIn("#?? TODO: Describe `value`.", rendered)
        self.assertIn("define value -> Number", rendered)

    def test_format_source_uses_two_space_block_indentation(self):
        source = (
            "define choose(n: Number) -> Number =>\n"
            "if ($n 0 >) =>\n"
            "$n\n"
            "else =>\n"
            "0\n"
            "end\n"
            "end\n"
        )

        self.assertEqual(
            format_source(source),
            "define choose(n: Number) -> Number =>\n"
            "  if ($n 0 >) =>\n"
            "    $n\n"
            "  else =>\n"
            "    0\n"
            "  end\n"
            "end\n",
        )

    def test_format_source_indents_multiline_match_cases(self):
        source = (
            "define choose(n: Number) -> Number =>\n"
            "$n match =>\n"
            "0 =>\n"
            "$n\n"
            "_ => 1\n"
            "end\n"
            "end\n"
        )

        self.assertEqual(
            format_source(source),
            "define choose(n: Number) -> Number =>\n"
            "  $n match =>\n"
            "    0 =>\n"
            "      $n\n"
            "    _ => 1\n"
            "  end\n"
            "end\n",
        )

    def test_format_source_adds_multiline_list_trailing_commas(self):
        source = "[\n1\n]\n[1,\n2\n]\n[1, 2]\n"

        self.assertEqual(
            format_source(source),
            "[\n1,\n]\n[1,\n2,\n]\n[1, 2]\n",
        )

    def test_format_source_can_disable_trailing_commas(self):
        source = "[\n1\n]\n"

        self.assertEqual(
            format_source(source, add_trailing_commas=False),
            source,
        )

    def test_format_source_preserves_multiline_string_contents(self):
        source = 'define text -> String =>\n"first\n    second"\nend\n'

        rendered = format_source(source)

        self.assertIn('  "first\n    second"\n', rendered)

    def test_extract_and_render_documented_defines(self):
        source = (
            "#?? Doubles `value`.\n"
            "#??\n"
            "#?? @param value Input value.\n"
            "#?? @returns Doubled value.\n"
            "public define double(value: Number) -> Number => $value 2 *\n"
        )

        definitions = extract_documented_defines(
            source,
            source_path="src/main.vlnc",
        )
        rendered = render_html_reference(definitions, title="Demo Reference")

        self.assertEqual(len(definitions), 1)
        self.assertEqual(
            definitions[0].signature,
            "public define double(value: Number) -> Number",
        )
        self.assertIn("<title>Demo Reference</title>", rendered)
        self.assertIn("Doubles <code>value</code>.", rendered)
        self.assertIn("src/main.vlnc", rendered)

    def test_project_source_files_excludes_generated_and_dependency_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            included = root / "src" / "main.vlnc"
            test_source = root / "tests" / "sample.vlnc"
            excluded = root / ".vln" / "dep" / "source.vlnc"
            for path in (included, test_source, excluded):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("", encoding="utf-8")

            self.assertEqual(project_source_files(root), (included, test_source))


if __name__ == "__main__":
    unittest.main()
