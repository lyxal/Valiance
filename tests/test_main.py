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
        self.assertIn("ElementNode(name=+, location=1:5)", rendered)
        self.assertIn("TypedNode(type=Number", rendered)

    def test_main_runs_inline_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["--run", "--code", '"hello" println'])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "hello\n")

    def test_main_implicitly_prints_stack_when_requested(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "--run",
                    "--implicit-output",
                    "--code",
                    '[1, 2] "done"',
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue(),
            "Stack [\n  0: [1, 2]\n  1: 'done'\n]\n",
        )

    def test_main_implicitly_prints_vectorised_stack_neatly(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "--run",
                    "--implicit-output",
                    "--code",
                    "[1, 2, 3] + [5, 6, 7]",
                ]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "Stack [\n  0: [6, 8, 10]\n]\n")

    def test_main_implicit_output_does_not_duplicate_explicit_prints(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                ["--run", "--implicit-output", "--code", '"hello" println\n1']
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "hello\n")

    def test_main_emits_and_runs_bytecode_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bytecode = Path(tmp) / "sample.vbc"

            emit_output = io.StringIO()
            with contextlib.redirect_stdout(emit_output):
                emit_exit = main(
                    [
                        "--code",
                        "[1, 2, 3] + [5, 6, 7]",
                        "--emit-bytecode",
                        str(bytecode),
                    ]
                )

            run_output = io.StringIO()
            with contextlib.redirect_stdout(run_output):
                run_exit = main(
                    [
                        "--run-bytecode",
                        str(bytecode),
                        "--implicit-output",
                    ]
                )
            bytecode_data = bytecode.read_bytes()

        self.assertEqual(emit_exit, 0)
        self.assertEqual(emit_output.getvalue(), "")
        self.assertTrue(bytecode_data.startswith(b"VLNCBC"))
        self.assertEqual(run_exit, 0)
        self.assertEqual(run_output.getvalue(), "Stack [\n  0: [6, 8, 10]\n]\n")

    def test_main_analyses_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.vlnc"
            source.write_text('"hello"', encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main([str(source)])

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "StringLiteralNode(value='hello', location=1:1)",
            output.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
