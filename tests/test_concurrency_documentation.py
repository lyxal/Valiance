"""Executable examples and documentation release checks for concurrency."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from valiance.analysis import Analyser
from valiance.main import main
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run

ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / "samples" / "concurrency"


class ConcurrencyExampleTests(unittest.TestCase):
    def test_successful_examples_execute_in_all_compiler_modes(self):
        for path in sorted(EXAMPLES.glob("*.vlnc")):
            if path.name == "StructuredFailure.vlnc":
                continue
            with self.subTest(example=path.name):
                source = path.read_text(encoding="utf-8")
                analyser = Analyser(source_file=path)
                typed = analyser.analyse(parse(source))
                self.assertEqual(analyser.diagnostics, [])
                outputs = []
                for optimize in (False, True):
                    program = compile_program(typed, optimize=optimize)
                    outputs.append(run(program))
                    outputs.append(run(loads(dumps(program))))
                self.assertTrue(all(output == outputs[0] for output in outputs))

    def test_structured_failure_is_deterministic_in_all_compiler_modes(self):
        path = EXAMPLES / "StructuredFailure.vlnc"
        analyser = Analyser(source_file=path)
        typed = analyser.analyse(parse(path.read_text(encoding="utf-8")))
        self.assertEqual(analyser.diagnostics, [])
        messages = []
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            for executable in (program, loads(dumps(program))):
                with self.assertRaises(Exception) as raised:
                    run(executable)
                messages.append(str(raised.exception))
        self.assertEqual(len(set(messages)), 1)
        self.assertIn("worker failed", messages[0])


class ConcurrencyDocumentationTests(unittest.TestCase):
    def test_public_docs_do_not_call_concurrency_deferred_or_incomplete(self):
        documents = [ROOT / "docs" / "language.md", ROOT / "docs" / "valiance-feature-checklist.md"]
        for path in documents:
            text = path.read_text(encoding="utf-8").casefold()
            self.assertNotIn("concurrency — deferred", text)
            self.assertNotIn("concurrency is incomplete", text)

    def test_generated_language_reference_contains_concurrency_api(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "language.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(
                    main(["docs", "--language", "--format", "json", "--output", str(target)]),
                    0,
                )
            document = json.loads(target.read_text(encoding="utf-8"))
            names = {item["qualified_name"] for item in document["elements"]}
        for name in ("spawn", "wait", "Channel", "send", "receive", "close"):
            self.assertIn(name, names)

    def test_parser_pretty_format_ast_and_repl_paths_accept_concurrency(self):
        source = (EXAMPLES / "BasicTask.vlnc").read_text(encoding="utf-8")
        commands = (
            ["parse", "--code", source],
            ["analyse", "--code", source],
            ["tidy", "--code", source, "--stdout"],
        )
        for command in commands:
            with self.subTest(command=command[0]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(command), 0)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch(
            "sys.stdin", io.StringIO(source + "\n:quit\n")
        ):
            self.assertEqual(main([]), 0)
        self.assertIn("42", output.getvalue())


if __name__ == "__main__":
    unittest.main()
