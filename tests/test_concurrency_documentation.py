"""Executable examples and documentation release checks for concurrency."""

from __future__ import annotations

import contextlib
import io
import json
import re
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from valiance.analysis import Analyser
from valiance.main import main
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.concurrency import Receive
from valiance.runtime.runtime_values import ObjectValue, RuntimeNumber

ROOT = Path(__file__).parents[1]
EXAMPLES = ROOT / "samples" / "concurrency"


# Every runnable sample has a concrete semantic contract, not just "all modes agree".
# Error examples are explicit too, so a new .vlnc cannot silently go untested.
N = RuntimeNumber
EXPECTED_OUTPUTS = {
    "BasicTask.vlnc": [N(42)],
    "CheckoutQuote.vlnc": [N(3449)],
    "BatchInvoiceTotals.vlnc": [[N(1275), N(3575), N(965), N(6075)]],
    "TelemetryIngestion.vlnc": [
        Receive.Value(N(18)), Receive.Value(N(24)), Receive.Value(N(21)),
        Receive.Value(N(27)), Receive.Closed(),
    ],
    "ScopedQuoteInputs.vlnc": [N(3000), N(300)],
    "BoundedBackpressure.vlnc": [
        Receive.Value(N(1)), Receive.Value(N(2)), Receive.Value(N(3)),
    ],
    "CloseAndDrain.vlnc": [
        Receive.Value(N(7)), Receive.Value(N(8)), Receive.Closed(),
    ],
    "ClosedReceiverBroadcast.vlnc": [[
        Receive.Closed(), Receive.Closed(), Receive.Closed(),
    ]],
    "FanOutFanIn.vlnc": [
        [N(49), N(4), N(25), N(9)],
        [N(8), N(3), N(6), N(4)],
    ],
    "NestedScopeJoin.vlnc": [N(42)],
    "MultiProducerDrain.vlnc": [
        Receive.Value(N(11)), Receive.Value(N(12)),
        Receive.Value(N(21)), Receive.Value(N(22)), Receive.Closed(),
    ],
    "PingPong.vlnc": [
        Receive.Value(N(100)), Receive.Value(N(200)), Receive.Value(N(300)),
        Receive.Value(N(10)), Receive.Value(N(20)), Receive.Value(N(30)),
    ],
    "ProducerConsumer.vlnc": [Receive.Value(N(1))],
    "SafeCapture.vlnc": [[N(1), N(2), N(3)]],
    "TransferSnapshots.vlnc": [
        Receive.Value([N(1), N(2), N(3)]),
        [N(99), N(2), N(3)],
        [N(1), N(2), N(3)],
    ],
    "VectorisedWait.vlnc": [[N(1), N(2), N(3)]],
    "WaitIsNotReexecution.vlnc": [
        Receive.Value(N(42)), N(99), N(99),
    ],
}
# stdout contracts keep the examples useful when run from the CLI: they
# print the values they actually computed, while preserving the result stack.
EXPECTED_PRINTS = {
    "CheckoutQuote.vlnc": [
        "checkout subtotal, delivery, discount, total (cents):\n",
        "[4548, 499, 600, 3449]\n",
    ],
    "BatchInvoiceTotals.vlnc": [
        "invoice totals including 75-cent handling fee (cents, input order):\n",
        "[1275, 3575, 965, 6075]\n",
    ],
    "TelemetryIngestion.vlnc": [
        "sensor readings and end-of-stream marker:\n",
        f"{EXPECTED_OUTPUTS['TelemetryIngestion.vlnc']}\n",
    ],
    "ScopedQuoteInputs.vlnc": [
        "computed price and shipping (cents):\n",
        "[3000, 300]\n",
    ],
    "ClosedReceiverBroadcast.vlnc": [
        "three closed receivers:\n",
        f"{EXPECTED_OUTPUTS['ClosedReceiverBroadcast.vlnc'][0]}\n",
    ],
    "FanOutFanIn.vlnc": [
        "fan-out squares:\n",
        f"{EXPECTED_OUTPUTS['FanOutFanIn.vlnc'][0]}\n",
        "fan-out successors:\n",
        f"{EXPECTED_OUTPUTS['FanOutFanIn.vlnc'][1]}\n",
    ],
    "MultiProducerDrain.vlnc": [
        "mailbox events (including closure):\n",
        f"{EXPECTED_OUTPUTS['MultiProducerDrain.vlnc']}\n",
    ],
    "NestedScopeJoin.vlnc": ["nested scope result:\n", "42\n"],
    "PingPong.vlnc": [
        "reply messages:\n",
        f"{EXPECTED_OUTPUTS['PingPong.vlnc'][:3]}\n",
        "request messages:\n",
        f"{EXPECTED_OUTPUTS['PingPong.vlnc'][3:]}\n",
    ],
    "TransferSnapshots.vlnc": [
        "transferred, mutated, original:\n",
        f"{EXPECTED_OUTPUTS['TransferSnapshots.vlnc']}\n",
    ],
    "WaitIsNotReexecution.vlnc": [
        "single message, same result on both waits:\n",
        f"{EXPECTED_OUTPUTS['WaitIsNotReexecution.vlnc']}\n",
    ],
}

EXPECTED_FAILURES = {
    "DeadlockDiagnostic.vlnc": ("concurrency deadlock", "waits for channel", "receive at", "task 2"),
    "NestedScopeFailure.vlnc": ("uncaught panic", "nested child failed"),
    "StructuredFailure.vlnc": ("uncaught panic", "worker failed"),
    "TimeoutCancellation.vlnc": ("cancelled task", "was cancelled"),
}


class ConcurrencyExampleTests(unittest.TestCase):
    def test_every_example_has_an_explicit_success_or_failure_contract(self):
        paths = {path.name for path in EXAMPLES.glob("*.vlnc")}
        self.assertFalse(set(EXPECTED_OUTPUTS) & set(EXPECTED_FAILURES))
        self.assertLessEqual(set(EXPECTED_PRINTS), set(EXPECTED_OUTPUTS))
        self.assertEqual(
            paths, set(EXPECTED_OUTPUTS) | set(EXPECTED_FAILURES) | {"IsolatedTransfer.vlnc"}
        )

    def _executables(self, path):
        """Compile one source in optimized/unoptimized and serialized forms."""
        analyser = Analyser(source_file=path)
        typed = analyser.analyse(parse(path.read_text(encoding="utf-8")))
        self.assertEqual(analyser.diagnostics, [], path.name)
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            yield optimize, False, program
            yield optimize, True, loads(dumps(program))

    def test_successful_examples_produce_expected_values_in_all_compiler_modes(self):
        for name, expected in sorted(EXPECTED_OUTPUTS.items()):
            path = EXAMPLES / name
            for optimize, serialized, executable in self._executables(path):
                with self.subTest(example=name, optimize=optimize, serialized=serialized):
                    printed = []
                    self.assertEqual(run(executable, output=printed.append), expected)
                    self.assertEqual(printed, EXPECTED_PRINTS.get(name, []))

    def test_moved_isolated_resource_is_closed_before_the_task_returns(self):
        path = EXAMPLES / "IsolatedTransfer.vlnc"
        for optimize, serialized, executable in self._executables(path):
            with self.subTest(optimize=optimize, serialized=serialized):
                results = run(executable)
                self.assertEqual(len(results), 1)
                self.assertIsInstance(results[0], ObjectValue)
                self.assertEqual(results[0].type_name, "Resource")
                self.assertEqual(results[0].mustcall_called, frozenset({"close"}))

    def test_intentional_failures_explain_why_they_fail_in_all_compiler_modes(self):
        for name, fragments in sorted(EXPECTED_FAILURES.items()):
            messages = []
            for optimize, serialized, executable in self._executables(EXAMPLES / name):
                with self.subTest(example=name, optimize=optimize, serialized=serialized):
                    with self.assertRaises(Exception) as raised:
                        run(executable)
                    message = str(raised.exception)
                    for fragment in fragments:
                        self.assertIn(fragment, message)
                    # Channel ids are process-global counters, not semantic identity.
                    messages.append(re.sub(r"channel \d+", "channel <id>", message))
            self.assertEqual(len(set(messages)), 1, name)


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
