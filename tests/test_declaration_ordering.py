"""Regression tests for declaration-first analysis and mutual recursion."""

from __future__ import annotations

import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import VirtualMachine, compile_program, dumps, loads, run
from valiance.runtime.runtime_values import RuntimeNumber


def analyse(source: str):
    """Parse and analyse one source string."""
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


class DeclarationOrderingTests(unittest.TestCase):
    """Exercise source-order independent top-level definitions."""

    def test_untyped_definition_is_available_to_earlier_executable_code(self):
        """Infer a self-contained define before analysing an earlier call."""
        analyser, typed = analyse("""
later(1)

define later(x) =>
  $x
end
""")
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(run(compile_program(typed, optimize=False)), [RuntimeNumber("1")])

    def test_fully_declared_definitions_can_be_mutually_recursive(self):
        """Prescanned signatures break a named mutual-recursion cycle."""
        source = """
define even(n: Integer) -> #boolean Integer =>
  if ($n == 0) => true
  else => odd($n - 1)
  end
end

define odd(n: Integer) -> #boolean Integer =>
  if ($n == 0) => false
  else => even($n - 1)
  end
end

even(10)
odd(10)
"""
        analyser, typed = analyse(source)
        self.assertEqual(analyser.diagnostics, [])
        program = compile_program(typed, optimize=False)
        self.assertEqual(run(program), [True, False])
        self.assertEqual(run(loads(dumps(program))), [True, False])

    def test_incomplete_mutual_recursion_is_not_prescanned(self):
        """Require explicit contracts for every inferred recursive element."""
        analyser, _typed = analyse("""
define left(n: Integer) => right($n) end
define right(n: Integer) => left($n) end
""")
        recursive = [
            diagnostic for diagnostic in analyser.diagnostics
            if "must have complete parameter and return signatures" in diagnostic
        ]
        self.assertEqual(len(recursive), 2)
        self.assertTrue(any("'left'" in diagnostic for diagnostic in recursive))
        self.assertTrue(any("'right'" in diagnostic for diagnostic in recursive))

    def test_inferred_helper_after_complete_recursive_callers(self):
        """Infer a later helper before checking fully declared recursive bodies."""
        analyser, _typed = analyse("""
define even?(:Integer) -> #boolean Number =>
  match =>
    if isZero? => 1
    _ => odd?(- 1)
  end
end

define odd?(:Integer) -> #boolean Number =>
  match =>
    if isZero? => 0
    _ => even?(- 1)
  end
end

define isZero? => == 0
""")
        self.assertEqual(analyser.diagnostics, [])

    def test_direct_inferred_recursion_has_explicit_signature_diagnostic(self):
        """Diagnose direct inferred recursion as a contract violation."""
        analyser, _typed = analyse("""
define recurse(value) => recurse($value) end
""")
        self.assertTrue(any(
            "recursive element 'recurse' must have complete parameter and return signatures"
            in diagnostic
            for diagnostic in analyser.diagnostics
        ))


    def test_three_definition_cycle_uses_prescanned_signatures(self):
        """Resolve a recursive cycle containing more than two definitions."""
        source = """
define first(n: Integer) -> Integer =>
  if ($n == 0) => 1 else => second($n - 1) end
end

define second(n: Integer) -> Integer =>
  if ($n == 0) => 2 else => third($n - 1) end
end

define third(n: Integer) -> Integer =>
  if ($n == 0) => 3 else => first($n - 1) end
end

first(2)
"""
        analyser, typed = analyse(source)
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            run(compile_program(typed, optimize=False)),
            [RuntimeNumber("3")],
        )

    def test_niladic_definitions_can_be_mutually_recursive(self):
        """Treat a backslash-prefixed definition as an explicit zero-input signature."""
        analyser, _typed = analyse(r"""
define \left -> #boolean Integer => \right end
define \right -> #boolean Integer => \left end
""")
        self.assertEqual(analyser.diagnostics, [])

    def test_generic_definitions_can_be_mutually_recursive(self):
        """Prescan generic contracts with fresh declared type variables."""
        analyser, _typed = analyse("""
define[T] left(value: T) -> T => right($value) end
define[T] right(value: T) -> T => left($value) end
""")
        self.assertEqual(analyser.diagnostics, [])

    def test_explicit_overload_sets_can_be_mutually_recursive(self):
        """Publish every explicit overload signature before checking shared bodies."""
        analyser, _typed = analyse("""
overload(Integer -> Integer)
overload(String -> String)
define left(value) => right($value) end

overload(Integer -> Integer)
overload(String -> String)
define right(value) => left($value) end
""")
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(len(analyser.env.overloads_for(parse("left")[0].name)), 2)
        self.assertEqual(len(analyser.env.overloads_for(parse("right")[0].name)), 2)

    def test_signature_can_reference_object_declared_later(self):
        """Analyse type-level declarations before function signatures regardless of order."""
        analyser, _typed = analyse("""
define keep(value: Box) -> Box => $value end
object Box => end
""")
        self.assertEqual(analyser.diagnostics, [])

    def test_optimized_forward_untyped_call_executes(self):
        """Keep declaration-first ordering through the optimizer pipeline."""
        analyser, typed = analyse("""
later(7)
define later(value) => $value end
""")
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            run(compile_program(typed, optimize=True)),
            [RuntimeNumber("7")],
        )


    def test_untyped_definition_dependencies_are_inferred_in_dependency_order(self):
        """Infer an acyclic declaration chain independently of source order."""
        analyser, typed = analyse("""
result(4)
define result(value) => helper($value) end
define helper(value) => $value + 1 end
""")
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(
            run(compile_program(typed, optimize=False)),
            [RuntimeNumber("5")],
        )

    def test_declared_return_mismatch_is_diagnosed(self):
        """Report an incompatible body while retaining its declared interface."""
        analyser, _typed = analyse("""
define invalid(value: Integer) -> String => $value end
""")
        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("function body returns Integer", analyser.diagnostics[0])
        self.assertIn("declares String", analyser.diagnostics[0])
        self.assertEqual(len(analyser.env.overloads_for(parse("invalid")[0].name)), 1)

    def test_last_prescanned_equal_overload_wins(self):
        """Let a later complete definition replace an equally specific overload."""
        analyser, typed = analyse(r"""
define \same -> String => "first" end
define \same -> String => "second" end
\same
""")
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(run(compile_program(typed, optimize=False)), ["second"])

    def test_forward_calls_use_last_inferred_equal_overload(self):
        """Resolve every forward call against the final source-order overload set."""
        source = r"""
println \foobaz
define \foobaz => "a"
println \foobaz
define \foobaz => "b"
println \foobaz
define \foobaz => "c"
"""
        analyser, typed = analyse(source)
        self.assertEqual(analyser.diagnostics, [])
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            for candidate in (program, loads(dumps(program))):
                output: list[object] = []
                self.assertEqual(VirtualMachine(output=output.append).run(candidate), [])
                self.assertEqual(output, ["c\n", "c\n", "c\n"])

    def test_more_specific_overload_beats_later_general_overload(self):
        """Use source order only to break ties after specificity selection."""
        analyser, typed = analyse("""
define classify(value: Integer) -> String => "integer" end
define classify(value: Number) -> String => "number" end
classify(1)
""")
        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(run(compile_program(typed, optimize=False)), ["integer"])


    def test_one_invalid_return_branch_is_diagnosed(self):
        """Reject a declared function when any branch violates its return contract."""
        analyser, _typed = analyse("""
define choose(flag: #boolean Number) -> Integer =>
  if ($flag) => 1 else => "bad" end
end
""")
        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("Integer | String", analyser.diagnostics[0])
        self.assertIn("declares Integer", analyser.diagnostics[0])

    def test_explicit_overload_body_is_checked_against_each_contract(self):
        """Validate a shared body against every explicit overload signature."""
        analyser, _typed = analyse("""
overload(Integer -> String)
define convert(value) => $value end
""")
        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("function body returns Integer", analyser.diagnostics[0])
        self.assertIn("declares String", analyser.diagnostics[0])

    def test_generic_return_contract_is_checked_rigidly(self):
        """Do not satisfy a generic return contract with one concrete body type."""
        analyser, _typed = analyse("""
define[T] identity(value: T) -> T => "bad" end
""")
        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("function body returns String", analyser.diagnostics[0])
        self.assertIn("declares T", analyser.diagnostics[0])

    def test_partially_declared_recursive_cycle_is_rejected(self):
        """Require every member of a recursive component to expose a full contract."""
        analyser, _typed = analyse("""
define left(value: Integer) -> Integer => right($value) end
define right(value) => left($value) end
""")
        cycle_diagnostics = [
            diagnostic
            for diagnostic in analyser.diagnostics
            if "must have complete parameter and return signatures" in diagnostic
        ]
        self.assertEqual(len(cycle_diagnostics), 1)
        self.assertTrue(cycle_diagnostics[0].startswith("3:1:"))


if __name__ == "__main__":
    unittest.main()
