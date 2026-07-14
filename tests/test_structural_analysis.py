import unittest

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.types.symbols import Symbol
from valiance.types import N


class StructuralTypeAnalysisTests(unittest.TestCase):
    def test_anonymous_trait_analysis_backtracks_across_overloads(self):
        analyser = Analyser()
        typed = analyser.analyse(
            parse(
                '''
object Foo => end
define read(value: Foo) -> String => "text"
define read(value: Foo) -> Number => 1
define write(value: Foo) -> Number => 1
define[T, U] accept(
  value: trait[T, U] =>
    extend read(:T) -> U
    extend write(:T) -> U
  end
) -> T => $value
Foo
accept
'''
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Foo")))

    def test_generic_row_analysis_accepts_concrete_field_subtypes(self):
        analyser = Analyser()
        typed = analyser.analyse(
            parse(
                '''
object Car =>
  public $value: Integer
end
define[T] get(value: T(.value: Number)) -> T => $value
Car(1)
get
'''
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Car")))


    def test_anonymous_trait_analysis_uses_nominal_context_when_widening(self):
        analyser = Analyser()
        typed = analyser.analyse(
            parse(
                """
trait Vehicle => end
object Car => end
object Car as Vehicle => end
object Foo => end
define read(value: Foo) -> Car => Car end
define read(value: Foo) -> Vehicle => Car end
define[T, U] accept(
  value: trait[T, U] =>
    extend read(:T) -> U
  end
) -> U => read($value) end
Foo
accept
"""
            )
        )

        self.assertEqual(analyser.diagnostics, [])
        self.assertEqual(typed[-1].typ, N(Symbol("Vehicle")))

    def test_anonymous_trait_rejects_incoherent_shared_generic(self):
        analyser = Analyser()
        analyser.analyse(
            parse(
                '''
object Foo => end
define read(value: Foo) -> String => "text"
define write(value: Foo) -> Number => 1
define[T, U] accept(
  value: trait[T, U] =>
    extend read(:T) -> U
    extend write(:T) -> U
  end
) -> T => $value
Foo
accept
'''
            )
        )

        self.assertEqual(len(analyser.diagnostics), 1)
        self.assertIn("no overloads for element 'accept'", analyser.diagnostics[0])


if __name__ == "__main__":
    unittest.main()
