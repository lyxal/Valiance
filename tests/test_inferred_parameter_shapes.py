"""Regression tests for inferred collection and unknown callable parameters."""

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.asts import TypedFunctionNode
from valiance.parsing import parse


def _analyse(source: str) -> tuple[Analyser, list[object]]:
    analyser = Analyser()
    typed = analyser.analyse(parse(source))
    return analyser, typed


def test_foreach_infers_generic_list_parameter() -> None:
    analyser, typed = _analyse(
        """
define foo(xs) => $xs foreach (x) => println $x
foo [1, 3, 5, 6]
"""
    )

    assert not analyser.diagnostics
    function = typed[0]
    assert isinstance(function, TypedFunctionNode)
    assert isinstance(function.typ, T.FunctionType)
    assert function.typ.params is not None
    parameter = T.normalize(function.typ.params[0])
    assert isinstance(parameter, T.CollectionType)
    assert isinstance(T.normalize(parameter.base), T.MetaVarType)


def test_called_parameter_infers_call_site_checked_function() -> None:
    analyser, typed = _analyse(
        """
define find(xs, fun) =>
  $xs foreach (x, i) =>
    if ($fun($x)) => return $i
  end
  -1
end

$r = 2
$i = find([1, 2, 3]): > $r
"""
    )

    assert not analyser.diagnostics
    function = typed[0]
    assert isinstance(function, TypedFunctionNode)
    assert isinstance(function.typ, T.OverloadSetType)
    overload = function.typ.overloads[0]
    assert isinstance(T.normalize(overload.params[0]), T.CollectionType)
    callable_parameter = T.normalize(overload.params[1])
    assert isinstance(callable_parameter, T.FunctionType)
    assert callable_parameter.params is None
    assert callable_parameter.returns is None
    assert overload.call_site_body is not None
    assert T.show(typed[-1].typ) == "Int"


def test_foreach_does_not_widen_known_scalar_to_list() -> None:
    analyser, _ = _analyse("1 foreach (x) => println $x")

    assert any(
        "for loop iterable must actually be iterable" in str(diagnostic)
        for diagnostic in analyser.diagnostics
    )
