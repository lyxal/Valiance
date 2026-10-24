import random
import unittest

from tools.fuzzing import FuzzConfig, TARGETS, _random_instruction, run_target


class DeterministicFuzzTests(unittest.TestCase):
    def test_each_fuzz_target_has_a_bounded_ci_smoke_run(self):
        config = FuzzConfig(
            seed=0xC0FFEE,
            iterations=120,
            max_depth=2,
            max_source_length=128,
        )

        for name in TARGETS:
            with self.subTest(target=name):
                run_target(name, config)

    def test_cases_are_reproducible_by_iteration(self):
        whole_run = FuzzConfig(seed=77, iterations=8, max_depth=2)
        single_case = FuzzConfig(seed=77, iterations=1, start=7, max_depth=2)

        run_target("serialization", whole_run)
        run_target("serialization", single_case)

    def test_all_opcodes_are_forced_through_serialization(self):
        from valiance.runtime.bytecode import OpCode

        run_target(
            "serialization",
            FuzzConfig(seed=91, iterations=len(OpCode), max_depth=2),
        )

    def test_reflection_opcodes_have_valid_generated_metadata(self):
        from valiance.runtime import dumps, loads
        from valiance.runtime.bytecode import (
            FunctionCode,
            OpCode,
            Program,
            ReflectionFieldsReference,
            ReflectionForeachReference,
        )

        reflective_foreach_cases = 0
        for seed in range(32):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                fields = _random_instruction(rng, 2, OpCode.MAKE_FIELDS)
                layouts = tuple(choice[1] for choice in fields.arg[1]) if isinstance(fields.arg, tuple) else (fields.arg,)
                for layout in layouts:
                    self.assertIsInstance(layout, ReflectionFieldsReference)
                foreach = _random_instruction(rng, 2, OpCode.FOREACH)
                if (isinstance(foreach.arg, ReflectionForeachReference)
                        or isinstance(foreach.arg, tuple) and foreach.arg and foreach.arg[0] == "reflection_choices"):
                    reflective_foreach_cases += 1
                program = Program(FunctionCode((fields, foreach)))
                self.assertEqual(loads(dumps(program)), program)
        self.assertGreater(reflective_foreach_cases, 0)


if __name__ == "__main__":
    unittest.main()
