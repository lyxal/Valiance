"""Regression tests for trait requirement calls through intersection types."""

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, run


SOURCE = """
trait Named =>
  extend getName -> String
end
trait Colourful =>
  extend getColour -> String
end
object Square =>
  $length: Real
  define getName => "Square"
  define getColour => "Red"
end
object Square as Named => end
object Square as Colourful => end
define draw(obj: Named & Colourful) =>
  "Drawing a ${getName $obj} which is coloured ${getColour $obj}"
end
Square 10
draw top
"""


def test_trait_requirements_are_available_from_intersection_receiver():
    analyser = Analyser()
    typed = analyser.analyse(parse(SOURCE))

    assert analyser.diagnostics == []
    assert T.same(typed[-1].typ, T.String)


def test_trait_requirements_dispatch_from_intersection_receiver_at_runtime():
    analyser = Analyser()
    typed = analyser.analyse(parse(SOURCE))

    assert analyser.diagnostics == []
    assert run(compile_program(typed, optimize=False)) == [
        "Drawing a Square which is coloured Red"
    ]
