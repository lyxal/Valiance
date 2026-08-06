import unittest

from valiance.analysis import Analyser
from valiance.asts import TypedSpawnNode
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.runtime_values import RuntimeNumber
from valiance.vtypes import Integer


def analyse(source: str):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


def execute(source: str):
    analyser, typed = analyse(source)
    if analyser.diagnostics:
        raise AssertionError(analyser.diagnostics)
    return run(compile_program(typed, optimize=False))


class SpawnModifierTests(unittest.TestCase):
    def test_modifier_spawn_has_fixed_plan(self):
        analyser, typed = analyse(
            "10 spawn: fn (value: Integer) -> Integer => $value 2 * end"
        )
        self.assertEqual(analyser.diagnostics, [])
        spawn = typed[-1]
        self.assertIsInstance(spawn, TypedSpawnNode)
        self.assertEqual(spawn.input_types, (Integer,))
        self.assertEqual(spawn.output_types, (Integer,))
        self.assertIsNotNone(spawn.callable_node)

    def test_modifier_spawn_executes_and_waits(self):
        self.assertEqual(
            execute(
                "10 spawn: fn (value: Integer) -> Integer => $value 3 * end | wait"
            ),
            [RuntimeNumber(30)],
        )

    def test_modifier_spawn_multiple_outputs(self):
        self.assertEqual(
            execute(
                '4 spawn: fn (value: Integer) -> Integer, String => '
                '$value "ok" end | wait'
            ),
            [RuntimeNumber(4), "ok"],
        )

    def test_modifier_spawn_round_trips(self):
        analyser, typed = analyse(
            "5 spawn: fn (value: Integer) -> Integer => $value 1 + end | wait"
        )
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        self.assertEqual(run(loads(dumps(program))), [RuntimeNumber(6)])

    def test_modifier_spawn_reports_missing_inputs(self):
        analyser, _ = analyse(
            "spawn: fn (value: Integer) -> Integer => $value end"
        )
        self.assertTrue(any("no spawn modifier overload" in item for item in analyser.diagnostics))

    def test_modifier_spawn_preserves_vectorisation_plan(self):
        analyser, typed = analyse(
            "[1, 2, 3] spawn: fn (value: Integer) -> Integer => "
            "$value 2 * end | wait"
        )
        self.assertEqual(analyser.diagnostics, [])
        spawn = typed[-2]
        self.assertIsInstance(spawn, TypedSpawnNode)
        self.assertTrue(spawn.vectorised)
        self.assertEqual(spawn.vectorised_depths, (1,))
        self.assertEqual(execute(
            "[1, 2, 3] spawn: fn (value: Integer) -> Integer => "
            "$value 2 * end | wait"
        ), [[RuntimeNumber(2), RuntimeNumber(4), RuntimeNumber(6)]])

    def test_first_class_spawn_preserves_vectorisation_plan(self):
        source = (
            "[1, 2, 3] fn (value: Integer) -> Integer => "
            "$value 1 + end spawn | wait"
        )
        analyser, typed = analyse(source)
        self.assertEqual(analyser.diagnostics, [])
        spawn = typed[-2]
        self.assertTrue(spawn.vectorised)
        self.assertEqual(spawn.vectorised_depths, (1,))
        self.assertEqual(execute(source), [
            [RuntimeNumber(2), RuntimeNumber(3), RuntimeNumber(4)]
        ])

    def test_vectorised_spawn_round_trips(self):
        source = (
            "[1, 2, 3] spawn: fn (value: Integer) -> Integer => "
            "$value 3 * end | wait"
        )
        analyser, typed = analyse(source)
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        expected = [[RuntimeNumber(3), RuntimeNumber(6), RuntimeNumber(9)]]
        self.assertEqual(run(program), expected)
        self.assertEqual(run(loads(dumps(program))), expected)

    def test_spawn_respects_novec_broadcast_position(self):
        source = (
            "[10, 20] [1, 2, 3] spawn: fn "
            "(fixed: Integer+ novec, value: Integer) -> Integer => "
            "$fixed $[0] $value + end | wait"
        )
        self.assertEqual(execute(source), [
            [RuntimeNumber(11), RuntimeNumber(12), RuntimeNumber(13)]
        ])


if __name__ == "__main__":
    unittest.main()
