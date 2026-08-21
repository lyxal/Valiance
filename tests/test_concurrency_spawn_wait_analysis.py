import unittest

from valiance.analysis import Analyser
from valiance.asts import TypedSpawnNode, TypedWaitNode
from valiance.parsing import parse
from valiance.vtypes import Int, String, TaskType


def analyse(source: str):
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


class SpawnWaitAnalysisTests(unittest.TestCase):
    def test_niladic_spawn_creates_task_row(self):
        analyser, typed = analyse("fn -> Int => 1 end spawn")
        self.assertEqual(analyser.diagnostics, [])
        spawn = typed[-1]
        self.assertIsInstance(spawn, TypedSpawnNode)
        self.assertIsInstance(spawn.typ, TaskType)
        self.assertEqual(spawn.output_types, (Int,))

    def test_spawn_sources_arguments_immediately(self):
        analyser, typed = analyse("10 fn (value: Int) -> Int => $value end spawn")
        self.assertEqual(analyser.diagnostics, [])
        spawn = typed[-1]
        self.assertIsInstance(spawn, TypedSpawnNode)
        self.assertEqual(spawn.input_types, (Int,))

    def test_wait_restores_multiple_native_outputs(self):
        analyser, typed = analyse(
            'fn -> Int, String => 1 "one" end | spawn | wait'
        )
        self.assertEqual(analyser.diagnostics, [])
        self.assertIsInstance(typed[-2], TypedSpawnNode)
        self.assertIsInstance(typed[-1], TypedWaitNode)
        self.assertEqual(typed[-1].output_types, (Int, String))

    def test_wait_rejects_non_task(self):
        analyser, _ = analyse("1 wait")
        self.assertTrue(any("wait requires a task" in item for item in analyser.diagnostics))

    def test_spawn_rejects_non_function(self):
        analyser, _ = analyse("1 spawn")
        self.assertTrue(any("spawn requires a function" in item for item in analyser.diagnostics))

    def test_spawn_reports_missing_inputs(self):
        analyser, _ = analyse("fn (value: Int) => $value end spawn")
        self.assertTrue(any("not enough stack values" in item for item in analyser.diagnostics))

    def test_spawn_and_wait_preserve_callable_effect_contract(self):
        from valiance.asts import TypedWaitNode

        analyser, typed = analyse(
            'tag IO as property\nfn () <IO> -> Int => 1 end | spawn | wait'
        )
        self.assertEqual(analyser.diagnostics, [])
        wait = next(node for node in typed if isinstance(node, TypedWaitNode))
        self.assertEqual({str(effect.name) for effect in wait.effects}, {"IO"})


if __name__ == "__main__":
    unittest.main()
