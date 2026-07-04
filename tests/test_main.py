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
        self.assertIn("valiance [compile] -c <code>", output.getvalue())
        self.assertIn("valiance run-bytecode <file>", output.getvalue())
        self.assertNotIn("analyse-demo", output.getvalue())

    def test_main_parses_inline_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["parse", "--code", "1 2 +"])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Parsed AST:", rendered)
        self.assertIn("ElementNode(name=+, location=1:5)", rendered)

    def test_main_analyses_inline_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["analyse", "--code", "1 2 +"])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Typed AST:", rendered)
        self.assertIn("TypedNode(type=Number", rendered)

    def test_main_annotates_inline_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["annotate", "--code", "define double(n) => $n 2 *"])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertEqual(
            rendered,
            "define double(n: Number) -> Number => $n 2 *\n",
        )

    def test_main_annotates_restored_source_chains_only_at_signatures(self):
        output = io.StringIO()
        source = "define foo => 0 - | positive?\nprintln foo 60"
        with contextlib.redirect_stdout(output):
            exit_code = main(["annotate", "--code", source])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue(),
            "define foo(_0: Number) -> #boolean Number => 0 - | positive?\n"
            "println foo 60\n",
        )

    def test_main_runs_inline_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["run", "--code", '"hello" println'])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "hello\n")

    def test_main_formats_lex_errors_with_source_context(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            exit_code = main(["run", "--code", '"missing'])

        self.assertEqual(exit_code, 1)
        rendered = error.getvalue()
        self.assertIn("Lex error: unterminated string", rendered)
        self.assertIn("--> <code>:1:1", rendered)
        self.assertIn('1 | "missing', rendered)
        self.assertIn("| ^", rendered)
        self.assertIn("help: Add the missing closing delimiter", rendered)

    def test_main_formats_parse_errors_with_source_context(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            exit_code = main(["run", "--code", "println()"])

        self.assertEqual(exit_code, 1)
        rendered = error.getvalue()
        self.assertIn("Parse error: empty argument lists are invalid", rendered)
        self.assertIn("--> <code>:1:9", rendered)
        self.assertIn("1 | println()", rendered)
        self.assertIn("|         ^", rendered)

    def test_main_formats_type_errors_with_source_context_and_help(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            exit_code = main(["run", "--code", "missing"])

        self.assertEqual(exit_code, 1)
        rendered = error.getvalue()
        self.assertIn("Type error: unknown element 'missing'", rendered)
        self.assertIn("--> <code>:1:1", rendered)
        self.assertIn("1 | missing", rendered)
        self.assertIn("help: Check the element name", rendered)

    def test_main_formats_runtime_errors_with_context(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            exit_code = main(
                ["run", "--code", 'if true => 1 else => "x" end as! String']
            )

        self.assertEqual(exit_code, 1)
        rendered = error.getvalue()
        self.assertIn("Runtime error: checked cast failed: 1 is Number", rendered)
        self.assertIn("runtime context:", rendered)
        self.assertIn("<main> ip", rendered)

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

    def test_main_implicitly_prints_finite_lazy_range_as_full_list(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "--run",
                    "--implicit-output",
                    "--code",
                    "range(1, 100)",
                ]
            )

        expected = "[" + ", ".join(str(index) for index in range(1, 101)) + "]"
        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), f"Stack [\n  0: {expected}\n]\n")

    def test_main_preview_lists_caps_runtime_printing(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                [
                    "run",
                    "--preview-lists",
                    "--code",
                    "println range(1, 101)",
                ]
            )

        expected = "[" + ", ".join(str(index) for index in range(1, 101)) + ", ...]\n"
        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), expected)

    def test_main_implicit_output_does_not_duplicate_explicit_prints(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(
                ["--run", "--implicit-output", "--code", '"hello" println\n1']
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "hello\n")

    def test_main_compiles_and_runs_bytecode_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bytecode = Path(tmp) / "sample.vbc"

            emit_output = io.StringIO()
            with contextlib.redirect_stdout(emit_output):
                emit_exit = main(
                    [
                        "compile",
                        "--code",
                        "[1, 2, 3] + [5, 6, 7]",
                        "--output",
                        str(bytecode),
                    ]
                )

            run_output = io.StringIO()
            with contextlib.redirect_stdout(run_output):
                run_exit = main(
                    [
                        "run-bytecode",
                        str(bytecode),
                        "--implicit-output",
                    ]
                )
            bytecode_data = bytecode.read_bytes()

        self.assertEqual(emit_exit, 0)
        self.assertIn(f"Wrote bytecode: {bytecode}", emit_output.getvalue())
        self.assertTrue(bytecode_data.startswith(b"VLNCBC"))
        self.assertEqual(run_exit, 0)
        self.assertEqual(run_output.getvalue(), "Stack [\n  0: [6, 8, 10]\n]\n")

    def test_main_compile_is_the_default_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            bytecode = Path(tmp) / "inline.vbc"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "--code",
                        "[1, 2, 3] + [5, 6, 7]",
                        "-o",
                        str(bytecode),
                    ]
                )

            bytecode_data = bytecode.read_bytes()

        self.assertEqual(exit_code, 0)
        self.assertIn(f"Wrote bytecode: {bytecode}", output.getvalue())
        self.assertTrue(bytecode_data.startswith(b"VLNCBC"))

    def test_main_run_does_not_emit_default_bytecode_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.vlnc"
            source.write_text("[1, 2, 3] + [5, 6, 7]", encoding="utf-8")
            output_path = Path(tmp) / "sample.vbc"

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "run",
                        str(source),
                        "--implicit-output",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertFalse(output_path.exists())
        self.assertEqual(output.getvalue(), "Stack [\n  0: [6, 8, 10]\n]\n")

    def test_main_emits_relative_bytecode_path_next_to_source_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp) / "src"
            source_dir.mkdir()
            source = source_dir / "sample.vlnc"
            source.write_text("[1, 2, 3] + [5, 6, 7]", encoding="utf-8")
            bytecode = source_dir / "sample.vbc"

            emit_output = io.StringIO()
            with contextlib.redirect_stdout(emit_output):
                emit_exit = main(
                    [
                        str(source),
                    ]
                )

            bytecode_data = bytecode.read_bytes()

        self.assertEqual(emit_exit, 0)
        self.assertIn(f"Wrote bytecode: {bytecode}", emit_output.getvalue())
        self.assertTrue(bytecode_data.startswith(b"VLNCBC"))

    def test_main_parses_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "sample.vlnc"
            source.write_text('"hello"', encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(["parse", str(source)])

        self.assertEqual(exit_code, 0)
        self.assertIn(
            "StringLiteralNode(value='hello', location=1:1)",
            output.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
