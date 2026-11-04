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

    def test_type_directed_cases_are_model_checked_and_reproducible(self):
        from tools.invariant_fuzzing import SOURCE_FAMILIES, check_typed_case, typed_case

        for family in SOURCE_FAMILIES:
            with self.subTest(family=family):
                values = (4, -2) if family == "generic-call" else (4, -2, 9, -3)
                case = typed_case(family, values)
                check_typed_case(case)
                self.assertEqual(case, typed_case(family, values))
        run_target("type-directed", FuzzConfig(seed=472, iterations=16))
        run_target("type-directed", FuzzConfig(seed=472, iterations=1, start=15))

    def test_vm_ownership_reconstruction_and_fault_unwind(self):
        from tools.invariant_fuzzing import (
            check_cleanup_during_unwind,
            check_ownership_sequence,
            check_reconstruction_transition,
        )

        check_cleanup_during_unwind()
        for shared in (False, True):
            with self.subTest(shared=shared):
                check_reconstruction_transition(shared)
        for seed in range(16):
            with self.subTest(seed=seed):
                check_ownership_sequence(random.Random(seed))

    def test_coverage_campaign_retains_replayable_arc_winners(self):
        from tools.coverage_guided import run_coverage_campaign
        from tools.invariant_fuzzing import check_typed_case, typed_case

        report = run_coverage_campaign(seed=123, iterations=5)
        self.assertGreater(len(report.arcs), 0)
        self.assertTrue(any(path.startswith("analysis/") for path, _, _ in report.arcs))
        self.assertTrue(any(path.startswith("runtime/") for path, _, _ in report.arcs))
        self.assertTrue(report.corpus)
        inventory = report.as_dict()
        self.assertGreater(inventory["syntactic_branch_sites"], 0)
        self.assertGreater(inventory["unvisited_branch_sites"], 0)
        for winner in report.corpus:
            with self.subTest(iteration=winner["iteration"]):
                self.assertGreater(winner["new_arcs"], 0)
                case = typed_case(winner["family"], tuple(winner["values"]))
                self.assertEqual(case.source, winner["source"])
                check_typed_case(case, all_modes=False)

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
