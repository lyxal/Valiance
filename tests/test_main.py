import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from valiance.diagnostics import Diagnostic, SourceLocation, render
from valiance.main import main


class MainTests(unittest.TestCase):
    def test_main_without_command_starts_repl(self):
        output = io.StringIO()
        input_stream = io.StringIO(":quit\n")
        with contextlib.redirect_stdout(output), patch("sys.stdin", input_stream):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Valiance REPL", rendered)
        self.assertIn("State persists between lines.", rendered)
        self.assertIn("vln:1> ", rendered)

    def test_repl_help_lists_styled_commands(self):
        output = io.StringIO()
        input_stream = io.StringIO(":help\n:quit\n")
        with contextlib.redirect_stdout(output), patch("sys.stdin", input_stream):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("REPL commands", rendered)
        self.assertIn(":reset  clear stack", rendered)
        self.assertIn(":quit   exit the REPL", rendered)

    def test_repl_runs_inline_source_with_implicit_output(self):
        output = io.StringIO()
        input_stream = io.StringIO("1 2 +\n:quit\n")
        with contextlib.redirect_stdout(output), patch("sys.stdin", input_stream):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("Stack [\n  0: 3\n]", output.getvalue())

    def test_repl_persists_stack_between_entries(self):
        output = io.StringIO()
        input_stream = io.StringIO("1\n2 +\n:quit\n")
        with contextlib.redirect_stdout(output), patch("sys.stdin", input_stream):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("Stack [\n  0: 1\n]", output.getvalue())
        self.assertIn("Stack [\n  0: 3\n]", output.getvalue())

    def test_repl_persists_variables_and_defines_between_entries(self):
        output = io.StringIO()
        input_stream = io.StringIO(
            "$x = 41\n"
            "define inc(n: Number) -> Number => $n 1 +\n"
            "$x inc\n"
            ":quit\n"
        )
        with contextlib.redirect_stdout(output), patch("sys.stdin", input_stream):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("Stack [\n  0: 42\n]", output.getvalue())

    def test_repl_reset_clears_stack_variables_and_defines(self):
        output = io.StringIO()
        error = io.StringIO()
        input_stream = io.StringIO("$x = 1\n:reset\n$x\n:quit\n")
        with (
            contextlib.redirect_stdout(output),
            contextlib.redirect_stderr(error),
            patch("sys.stdin", input_stream),
        ):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        self.assertIn("Reset REPL state.", output.getvalue())
        self.assertIn("Type error: undefined variable 'x'", error.getvalue())

    def test_repl_reset_restarts_prompt_counter(self):
        output = io.StringIO()
        input_stream = io.StringIO("1\n:reset\n2\n:quit\n")
        with contextlib.redirect_stdout(output), patch("sys.stdin", input_stream):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertGreaterEqual(rendered.count("vln:1> "), 2)

    def test_help_flag_prints_help(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["--help"])

        self.assertEqual(exit_code, 2)
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
        self.assertIn("TypedNode(type=Integer", rendered)

    def test_main_annotates_inline_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["annotate", "--code", "define double(n) => $n 2 *"])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertEqual(
            rendered,
            "define double(n) => $n 2 *\n",
        )

    def test_main_annotates_restored_source_chains_only_at_signatures(self):
        output = io.StringIO()
        source = "define foo => 0 - | positive?\nprintln foo 60"
        with contextlib.redirect_stdout(output):
            exit_code = main(["annotate", "--code", source])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue(),
            "define foo => 0 - | positive?\nprintln foo 60\n",
        )

    def test_main_runs_inline_code(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["run", "--code", '"hello" println'])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "hello\n")

    def test_main_run_inline_code_defaults_to_implicit_output(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["run", "--code", "1 2 +"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "Stack [\n  0: 3\n]\n")

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

    def test_main_formats_type_warnings_without_failing(self):
        output = io.StringIO()
        error = io.StringIO()
        source = '@warn("use newer") define old -> Number => 1\nold | println'
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            exit_code = main(["run", "--code", source])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "1\n")
        rendered = error.getvalue()
        self.assertIn("Type warning: use newer", rendered)
        self.assertIn("--> <code>:2:1", rendered)
        self.assertNotIn("\033[", rendered)

    def test_diagnostic_rendering_can_use_colour(self):
        rendered = render(
            Diagnostic("Type warning", "careful", SourceLocation(1, 2)),
            "ab",
            color=True,
        )

        self.assertIn("\033[1m\033[33mType warning\033[0m", rendered)
        self.assertIn("\033[34m  --> <code>:1:2\033[0m", rendered)
        self.assertIn("\033[33m^\033[0m", rendered)

    def test_main_formats_runtime_errors_with_context(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            exit_code = main(
                ["run", "--code", 'if true => 1 else => "x" end as! String']
            )

        self.assertEqual(exit_code, 1)
        rendered = error.getvalue()
        self.assertIn("Runtime error: checked cast failed: 1 is Integer", rendered)
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

    def test_package_commands_update_manifest_lock_and_install_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            output = io.StringIO()
            try:
                import os

                os.chdir(root)
                with contextlib.redirect_stdout(output):
                    add_exit = main(
                        ["add", "github.com/user/repo", "1.0.0", "as", "repo"]
                    )
                    upgrade_exit = main(["upgrade", "repo", "1.1.0"])
                    install_exit = main(["install"])
                    remove_exit = main(["remove", "repo"])
            finally:
                os.chdir(old_cwd)

            manifest = (root / "valiance.toml").read_text(encoding="utf-8")
            lock = (root / "valiance.lock").read_text(encoding="utf-8")

        self.assertEqual(
            (add_exit, upgrade_exit, install_exit, remove_exit),
            (0, 0, 0, 0),
        )
        self.assertIn("[dependencies]", manifest)
        self.assertNotIn("repo =", manifest)
        self.assertIn('"dependencies": []', lock)

    def test_package_init_creates_project_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(["init", str(project)])

            manifest = (project / "valiance.toml").read_text(encoding="utf-8")
            source = (project / "src" / "main.vlnc").read_text(encoding="utf-8")
            gitignore = (project / ".gitignore").read_text(encoding="utf-8")
            lock = (project / "valiance.lock").read_text(encoding="utf-8")

        self.assertEqual(exit_code, 0)
        self.assertIn('name = "demo"', manifest)
        self.assertIn('"Hello, Valiance" println', source)
        self.assertIn(".vln/", gitignore)
        self.assertIn('"dependencies": []', lock)

    def test_package_add_rejects_non_exact_versions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            error = io.StringIO()
            try:
                import os

                os.chdir(root)
                with contextlib.redirect_stderr(error):
                    exit_code = main(["add", "somelib", "^1.2.3"])
            finally:
                os.chdir(old_cwd)

        self.assertEqual(exit_code, 1)
        self.assertIn("Package error: version '^1.2.3'", error.getvalue())


if __name__ == "__main__":
    unittest.main()
