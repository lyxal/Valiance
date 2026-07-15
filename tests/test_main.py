import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from valiance.analysis.diagnostics import Diagnostic, SourceLocation, render
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

    def test_repl_type_command_previews_without_executing(self):
        output = io.StringIO()
        input_stream = io.StringIO(":type 1 2 +\n:quit\n")
        with contextlib.redirect_stdout(output), patch("sys.stdin", input_stream):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Types: [] -> [Integer]", rendered)
        self.assertNotIn("Stack [", rendered)

    def test_repl_type_command_uses_current_stack_without_mutating_it(self):
        output = io.StringIO()
        input_stream = io.StringIO("1\n:type 2 +\n2 +\n:quit\n")
        with contextlib.redirect_stdout(output), patch("sys.stdin", input_stream):
            exit_code = main([])

        self.assertEqual(exit_code, 0)
        rendered = output.getvalue()
        self.assertIn("Types: [Integer] -> [Integer]", rendered)
        self.assertIn("Stack [\n  0: 3\n]", rendered)

    def test_help_flag_prints_help(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["--help"])

        self.assertEqual(exit_code, 0)
        self.assertIn("USAGE", output.getvalue())
        self.assertIn("valiance compile --file src/main.vlnc", output.getvalue())
        self.assertIn("exec      Execute existing bytecode without recompiling", output.getvalue())
        self.assertNotIn("analyse-demo", output.getvalue())


    def test_subcommand_help_is_focused_and_succeeds(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["compile", "--help", "ignored"])
        self.assertEqual(exit_code, 0)
        self.assertIn("valiance compile", output.getvalue())
        self.assertIn("EXAMPLE", output.getvalue())

    def test_help_subcommand_and_version(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(["help", "test"]), 0)
            self.assertEqual(main(["--version"]), 0)
        self.assertIn("valiance test", output.getvalue())
        self.assertIn("valiance 0.1.0", output.getvalue())

    def test_typo_suggests_a_command_on_stderr(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            exit_code = main(["compiel"])
        self.assertEqual(exit_code, 2)
        self.assertIn("Did you mean 'compile'?", error.getvalue())

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

    def test_main_annotate_keeps_niladic_definition_syntax_valid(self):
        output = io.StringIO()
        source = "define \\value -> Number => 1"
        with contextlib.redirect_stdout(output):
            exit_code = main(["annotate", "--code", source])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), source + "\n")

    def test_main_annotate_handles_multiple_empty_niladic_definitions(self):
        output = io.StringIO()
        source = "define \\first => end\ndefine \\second => end"
        with contextlib.redirect_stdout(output):
            exit_code = main(["annotate", "--code", source])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue(),
            "define \\first -> => end\ndefine \\second -> => end\n",
        )

    def test_main_annotate_ignores_arrows_inside_generic_constraints(self):
        output = io.StringIO()
        source = (
            "define[T: trait => extend ==(:T, :T) -> Number end] "
            "\\value => end"
        )
        with contextlib.redirect_stdout(output):
            exit_code = main(["annotate", "--code", source])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue(),
            source.replace("\\value =>", "\\value -> =>") + "\n",
        )

    def test_main_tidy_renders_inferred_parameter_as_anonymous_generic(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = main(
                ["tidy", "--code", "define id(x) => $x", "--stdout"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue(),
            "define id(x: @1) -> @1 => $x\n",
        )

    def test_main_tidy_renders_all_inferred_row_generics(self):
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            exit_code = main(
                ["tidy", "--code", "define get(x) => $x.foo", "--stdout"]
            )

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue(),
            "define get(x: @1(.foo: @2)) -> @2 => $x.foo\n",
        )

    def test_main_tidy_preserves_named_generics_beside_anonymous_ones(self):
        output = io.StringIO()
        source = "define[T: Vehicle] choose(x: T, y) => $y"

        with contextlib.redirect_stdout(output):
            exit_code = main(["tidy", "--code", source, "--stdout"])

        self.assertEqual(exit_code, 0)
        self.assertEqual(
            output.getvalue(),
            "define[T: Vehicle] choose(x: T, y: @1) -> @1 => $y\n",
        )

    def test_main_tidy_generic_output_is_idempotent(self):
        source = "define get(x) => $x.foo"
        first_output = io.StringIO()
        with contextlib.redirect_stdout(first_output):
            first_exit = main(["tidy", "--code", source, "--stdout"])
        rendered = first_output.getvalue().rstrip("\n")

        second_output = io.StringIO()
        with contextlib.redirect_stdout(second_output):
            second_exit = main(["tidy", "--code", rendered, "--stdout"])

        self.assertEqual(first_exit, 0)
        self.assertEqual(second_exit, 0)
        self.assertEqual(second_output.getvalue(), first_output.getvalue())

    def test_main_tidy_rewrites_one_file_with_docstrings_and_formatting(self):
        with tempfile.TemporaryDirectory() as directory:
            source_file = Path(directory) / "main.vlnc"
            source_file.write_text(
                "define choose(n: Number) -> Number =>\n"
                "if ($n 0 >) =>\n"
                "$n\n"
                "else =>\n"
                "0\n"
                "end\n"
                "end\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    ["tidy", str(source_file), "--docstrings", "--format"]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn(f"Updated: {source_file}", output.getvalue())
            self.assertEqual(
                source_file.read_text(encoding="utf-8"),
                "#?? TODO: Describe `choose`.\n"
                "#??\n"
                "#?? @param n TODO: Describe `n`.\n"
                "#?? @returns TODO: Describe the returned stack value(s).\n"
                "define choose(n: Number) -> Number =>\n"
                "  if ($n 0 >) =>\n"
                "    $n\n"
                "  else =>\n"
                "    0\n"
                "  end\n"
                "end\n",
            )

    def test_main_tidy_without_file_rewrites_whole_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
                '[entries]\nmain = "src/main.vlnc"\n',
                encoding="utf-8",
            )
            first = root / "src" / "main.vlnc"
            second = root / "tests" / "sample.vlnc"
            dependency = root / ".vln" / "dep" / "ignored.vlnc"
            for path in (first, second, dependency):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("define value -> Number => 1\n", encoding="utf-8")

            output = io.StringIO()
            with (
                patch("pathlib.Path.cwd", return_value=root),
                contextlib.redirect_stdout(output),
            ):
                exit_code = main(["tidy", "--docstrings"])

            self.assertEqual(exit_code, 0)
            self.assertTrue(first.read_text(encoding="utf-8").startswith("#??"))
            self.assertTrue(second.read_text(encoding="utf-8").startswith("#??"))
            self.assertFalse(dependency.read_text(encoding="utf-8").startswith("#??"))
            self.assertIn("Tidied 2 file(s)", output.getvalue())

    def test_main_docs_generates_html_for_one_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source_file = Path(directory) / "math.vlnc"
            output_file = Path(directory) / "reference.html"
            source_file.write_text(
                "#?? Double a number.\n"
                "#?? @param value Number to double.\n"
                "#?? @returns The doubled number.\n"
                "public define double(value: Number) -> Number => $value 2 *\n",
                encoding="utf-8",
            )
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    ["docs", str(source_file), "--output", str(output_file)]
                )

            self.assertEqual(exit_code, 0)
            rendered = output_file.read_text(encoding="utf-8")
            self.assertIn("math Reference", rendered)
            self.assertIn(
                "public define double(value: Number) -&gt; Number",
                rendered,
            )
            self.assertIn("Double a number.", rendered)
            self.assertIn(f"Wrote documentation: {output_file}", output.getvalue())

    def test_main_docs_without_file_generates_project_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
                '[entries]\nmain = "src/main.vlnc"\n',
                encoding="utf-8",
            )
            source_file = root / "src" / "main.vlnc"
            source_file.parent.mkdir(parents=True)
            source_file.write_text(
                "#?? Main entry.\ndefine \\main -> Number => 1\n",
                encoding="utf-8",
            )

            with patch("pathlib.Path.cwd", return_value=root):
                exit_code = main(["docs"])

            output_file = root / "docs" / "reference.html"
            self.assertEqual(exit_code, 0)
            self.assertTrue(output_file.is_file())
            rendered = output_file.read_text(encoding="utf-8")
            self.assertIn("demo Reference", rendered)
            self.assertIn("src/main.vlnc", rendered)

    def test_main_docs_language_generates_builtin_and_stdlib_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            output_file = Path(directory) / "language-reference.json"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                exit_code = main(
                    [
                        "docs",
                        "--language",
                        "--format",
                        "json",
                        "--output",
                        str(output_file),
                    ]
                )

            self.assertEqual(exit_code, 0)
            rendered = output_file.read_text(encoding="utf-8")
            self.assertIn('"qualified_name": "println"', rendered)
            self.assertIn('"qualified_name": "both"', rendered)
            self.assertIn('"qualified_name": "correspond"', rendered)
            self.assertIn('"qualified_name": "std.regex.matches"', rendered)
            self.assertIn(
                "128 built-in and standard-library entries",
                output.getvalue(),
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

    def test_main_formats_arbitrarily_large_integer(self):
        output = io.StringIO()
        value = "99999999999999999999999999999"
        with contextlib.redirect_stdout(output):
            exit_code = main(["run", "--code", value])

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), f"Stack [\n  0: {value}\n]\n")

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
        source = '@warn("use newer") define \\old -> Number => 1\n\\old | println'
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
                        "exec",
                        "--file",
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
                        "--file",
                        str(source),
                        "--implicit-output",
                    ]
                )

        self.assertEqual(exit_code, 0)
        self.assertFalse(output_path.exists())
        self.assertEqual(output.getvalue(), "Stack [\n  0: [6, 8, 10]\n]\n")

    def test_main_compile_uses_main_project_entry_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            source = root / "src" / "main.vlnc"
            source.write_text('"main" println', encoding="utf-8")
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n'
                '[entries]\nmain = "src/main.vlnc"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            output = io.StringIO()
            try:
                import os

                os.chdir(root)
                with contextlib.redirect_stdout(output):
                    exit_code = main(["compile"])
            finally:
                os.chdir(old_cwd)

            bytecode = root / "bin" / "main.vbc"
            bytecode_data = bytecode.read_bytes()

        self.assertEqual(exit_code, 0)
        self.assertIn(f"Wrote bytecode: {bytecode}", output.getvalue())
        self.assertTrue(bytecode_data.startswith(b"VLNCBC"))
        self.assertFalse((root / "src" / "main.vbc").exists())

    def test_main_compile_uses_named_project_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.vlnc").write_text('"main" println', encoding="utf-8")
            source = root / "src" / "server.vlnc"
            source.write_text('"server" println', encoding="utf-8")
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n'
                '[entries]\nmain = "src/main.vlnc"\nserver = "src/server.vlnc"\n\n'
                "[dependencies]\n",
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            output = io.StringIO()
            try:
                import os

                os.chdir(root)
                with contextlib.redirect_stdout(output):
                    exit_code = main(["compile", "server"])
            finally:
                os.chdir(old_cwd)

            bytecode = root / "bin" / "server.vbc"
            bytecode_data = bytecode.read_bytes()

        self.assertEqual(exit_code, 0)
        self.assertIn(f"Wrote bytecode: {bytecode}", output.getvalue())
        self.assertTrue(bytecode_data.startswith(b"VLNCBC"))
        self.assertFalse((root / "src" / "server.vbc").exists())

    def test_main_run_uses_main_project_entry_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.vlnc").write_text('"main" println', encoding="utf-8")
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n'
                '[entries]\nmain = "src/main.vlnc"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            output = io.StringIO()
            try:
                import os

                os.chdir(root)
                with contextlib.redirect_stdout(output):
                    exit_code = main(["run"])
            finally:
                os.chdir(old_cwd)

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "main\n")

    def test_main_run_uses_named_project_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.vlnc").write_text('"main" println', encoding="utf-8")
            (root / "src" / "server.vlnc").write_text(
                '"server" println',
                encoding="utf-8",
            )
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n'
                '[entries]\nmain = "src/main.vlnc"\nserver = "src/server.vlnc"\n\n'
                "[dependencies]\n",
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            output = io.StringIO()
            try:
                import os

                os.chdir(root)
                with contextlib.redirect_stdout(output):
                    exit_code = main(["run", "server"])
            finally:
                os.chdir(old_cwd)

        self.assertEqual(exit_code, 0)
        self.assertEqual(output.getvalue(), "server\n")

    def test_main_run_rejects_unknown_project_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.vlnc").write_text('"main" println', encoding="utf-8")
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "1.0.0"\n\n'
                '[entries]\nmain = "src/main.vlnc"\nserver = "src/server.vlnc"\n\n'
                "[dependencies]\n",
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            error = io.StringIO()
            try:
                import os

                os.chdir(root)
                with contextlib.redirect_stderr(error):
                    exit_code = main(["run", "missing"])
            finally:
                os.chdir(old_cwd)

        self.assertEqual(exit_code, 1)
        self.assertIn("project has no entry named 'missing'", error.getvalue())
        self.assertIn("available entries: main, server", error.getvalue())

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
        self.assertIn("[entries]", manifest)
        self.assertIn('main = "src/main.vlnc"', manifest)
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

    def test_test_command_discovers_groups_and_selects_exact_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "main.vlnc").write_text("", encoding="utf-8")
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
                '[entries]\nmain = "src/main.vlnc"\n\n[dependencies]\n',
                encoding="utf-8",
            )
            test_source = (
                'import { std.testing }\n\n'
                '@testgroup("Arithmetic")\n'
                'define \\arithmetic =>\n'
                '  @test("adds two numbers")\n'
                '  define \\addition =>\n'
                '    testing.assertEqual(20 + 22, 42)\n'
                '  end\n\n'
                '  @testgroup("Division")\n'
                '  define \\division =>\n'
                '    @test("expects a panic")\n'
                '    define \\zero =>\n'
                '      testing.assertPanics: fn => RuntimeFault("boom") panic end\n'
                '    end\n'
                '  end\n'
                'end\n'
            )
            (root / "tests" / "arithmetic.vlnc").write_text(
                test_source,
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            output = io.StringIO()
            tree_output = io.StringIO()
            try:
                import os

                os.chdir(root)
                with contextlib.redirect_stdout(output):
                    exit_code = main(["test", "arithmetic.division"])
                with contextlib.redirect_stdout(tree_output):
                    list_exit = main(["test", "--list"])
            finally:
                os.chdir(old_cwd)

        self.assertEqual(exit_code, 0)
        self.assertEqual(list_exit, 0)
        self.assertIn("arithmetic — Arithmetic", tree_output.getvalue())
        self.assertIn("  division — Division", tree_output.getvalue())
        rendered = output.getvalue()
        self.assertIn("PASS arithmetic.division.zero", rendered)
        self.assertNotIn("arithmetic.addition", rendered)
        self.assertIn("1 passed, 0 failed, 0 errors", rendered)

    def test_test_command_lists_flat_selectors_and_reports_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "tests").mkdir()
            (root / "valiance.toml").write_text(
                '[project]\nname = "demo"\nversion = "0.1.0"\n\n'
                '[entries]\n\n[dependencies]\n',
                encoding="utf-8",
            )
            test_source = (
                'import { std.testing }\n\n'
                '@testgroup\n'
                'define \\checks =>\n'
                '  @test\n'
                '  define \\passes =>\n'
                '    assert =>\n'
                '      20 + 22 == 42\n'
                '    else =>\n'
                '      "bad arithmetic"\n'
                '    end\n'
                '  end\n\n'
                '  @test\n'
                '  define \\fails =>\n'
                '    testing.fail("intentional failure")\n'
                '  end\n'
                'end\n'
            )
            (root / "tests" / "checks.vlnc").write_text(
                test_source,
                encoding="utf-8",
            )
            old_cwd = Path.cwd()
            listed = io.StringIO()
            run_output = io.StringIO()
            try:
                import os

                os.chdir(root)
                with contextlib.redirect_stdout(listed):
                    list_exit = main(["test", "--list", "--flat"])
                with contextlib.redirect_stdout(run_output):
                    run_exit = main(["test"])
            finally:
                os.chdir(old_cwd)

        self.assertEqual(list_exit, 0)
        self.assertEqual(
            listed.getvalue().splitlines(),
            ["checks.fails", "checks.passes"],
        )
        self.assertEqual(run_exit, 1)
        rendered = run_output.getvalue()
        self.assertIn("FAIL checks.fails", rendered)
        self.assertIn("intentional failure", rendered)
        self.assertIn("PASS checks.passes", rendered)
        self.assertIn("1 passed, 1 failed, 0 errors", rendered)


    def test_main_preserves_source_location_for_multiline_suggestions(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            exit_code = main(["compile", "--code", "1 pritn"])

        self.assertEqual(exit_code, 1)
        rendered = error.getvalue()
        self.assertIn("Type error: unknown element 'pritn'", rendered)
        self.assertNotIn("Type error: 1:3:", rendered)
        self.assertIn("--> <code>:1:3", rendered)
        self.assertIn("did you mean:", rendered)
        self.assertIn("  - print(", rendered)

    def test_main_renders_multiline_overloads_without_function_prefixes(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            exit_code = main(
                [
                    "compile",
                    "--code",
                    "define convert(value: Integer) -> String => \"\"\n"
                    "define convert(text: String) -> Integer => 0\n"
                    "None convert",
                ]
            )

        self.assertEqual(exit_code, 1)
        rendered = error.getvalue()
        self.assertIn(
            "available overloads:\n  - convert(value: Integer) -> String",
            rendered,
        )
        self.assertIn("  - convert(text: String) -> Integer", rendered)
        self.assertNotIn("Function[", rendered)

    def test_main_renders_lints_with_actionable_replacement(self):
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            exit_code = main(["compile", "--code", "1 as! Number"])

        self.assertEqual(exit_code, 0)
        rendered = error.getvalue()
        self.assertIn(
            "Lint warning: checked cast to Number is statically safe",
            rendered,
        )
        self.assertIn("write `as Number` instead of `as! Number`", rendered)


if __name__ == "__main__":
    unittest.main()
