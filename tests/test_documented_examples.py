"""Keep the worked Valiance example files parseable and type-correct."""

from __future__ import annotations

import unittest
from pathlib import Path

from valiance.analysis import Analyser
from valiance.parsing import parse


EXAMPLES = (
    "ConwayGameOfLife.vlnc",
    "GuessingGame.vlnc",
    "CeaserCipher.vlnc",
    "RunLengthEncoding.vlnc",
    "StackCalculator.vlnc",
    "SumOfSquares.vlnc",
    "TrapezodialRule.vlnc",
    "Fibonacci.vlnc",
    "Records.vlnc",
    "ArgumentCycling.vlnc",
    "Dip.vlnc",
    "TraitInheritance.vlnc",
    "GenericFind.vlnc",
    "Brainfuck.vlnc",
    "OptionalMemberAccess.vlnc",
)


class DocumentedExampleTests(unittest.TestCase):
    """Verify that every example advertised in docs/examples.md still analyses."""

    def test_worked_examples_analyse_without_diagnostics(self) -> None:
        """Parse and analyse each documented example without executing loops or I/O."""
        root = Path(__file__).parents[1] / "docs" / "tentative examples"
        failures: list[str] = []

        for name in EXAMPLES:
            path = root / name
            analyser = Analyser(source_file=path)
            analyser.analyse(parse(path.read_text(encoding="utf-8")))
            if analyser.diagnostics:
                failures.append(f"{name}: {analyser.diagnostics}")

        self.assertEqual(failures, [], "\n".join(failures))


if __name__ == "__main__":
    unittest.main()
