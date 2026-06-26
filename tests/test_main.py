import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from valiance.main import main


class MainTests(unittest.TestCase):
    def test_main_without_command_prints_help(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("usage: valiance", output.getvalue())
        self.assertIn("valiance -c <code>", output.getvalue())
        self.assertNotIn("analyse-demo", output.getvalue())

    def test_main_analyses_inline_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["--code", "1 2 +"])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Parsed AST:", rendered)
        self.assertIn("Typed AST:", rendered)
        self.assertIn("ElementNode(name=+)", rendered)
        self.assertIn("TypedNode(type=Number", rendered)

    def test_main_analyses_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.vlnc"
            source.write_text('"hello"', encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main([str(source)])

        self.assertEqual(exit_code, 0)
        self.assertIn("StringLiteralNode(value='hello')", output.getvalue())


if __name__ == "__main__":
    unittest.main()
