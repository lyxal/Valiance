"""Shared source-fixture execution for the numbered trait hardening plan."""

from contextlib import redirect_stdout
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.modules_system.modules import ModuleLoader
from valiance.parsing import parse
from valiance.parsing.parser import ParseError, ParseErrors
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.runtime_values import LazyList


@dataclass(frozen=True)
class SourceCase:
    """One compile oracle and, when applicable, its observable execution oracle."""

    id: str
    source: str
    stack: list | None = None
    reject: tuple[str, ...] = ()
    syntax: bool = False
    stdout: str = ""
    fault: str | None = None
    typ: str | None = None
    modules: dict[str, str] = field(default_factory=dict)


def materialize(value):
    """Force finite lazy results recursively without erasing object identity."""
    if isinstance(value, (list, LazyList)):
        return [materialize(item) for item in value]
    return value


class TraitPlanTestCase(unittest.TestCase):
    """Check source fixtures in both optimization modes and persistence formats."""

    def check_case(self, case: SourceCase):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for name, source in case.modules.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(source, encoding="utf-8")
            if case.syntax:
                with self.assertRaises((ParseError, ParseErrors)) as caught:
                    parse(case.source)
                for message in case.reject:
                    self.assertIn(message, str(caught.exception))
                return
            analyser = Analyser(module_loader=ModuleLoader(), source_file=root / "main.vlnc")
            typed = analyser.analyse(parse(case.source))
            if case.reject:
                self.assertTrue(analyser.diagnostics, case.id)
                diagnostic = "\n".join(analyser.diagnostics)
                for message in case.reject:
                    self.assertIn(message, diagnostic, case.id)
                return
            self.assertEqual(analyser.diagnostics, [], case.id)
            if case.typ is not None:
                self.assertEqual(T.show(typed[-1].typ), case.typ)
            if case.stack is None and case.fault is None:
                return
            for optimize in (False, True):
                program = compile_program(typed, optimize=optimize)
                for persisted, candidate in ((False, program), (True, loads(dumps(program)))):
                    with self.subTest(optimize=optimize, bytecode=persisted):
                        output = StringIO()
                        with redirect_stdout(output):
                            if case.fault is not None:
                                with self.assertRaisesRegex(Exception, case.fault):
                                    run(candidate)
                            else:
                                self.assertEqual(materialize(run(candidate)), case.stack)
                        self.assertEqual(output.getvalue(), case.stdout)


def source_cases(cls, cases):
    """Expose every numbered fixture as a separately discoverable unittest."""
    for case in cases:
        def test(self, fixture=case):
            self.check_case(fixture)

        test.__name__ = "test_" + case.id.replace("-", "_")
        test.__doc__ = case.id
        test.trait_plan_ids = (case.id,)
        setattr(cls, test.__name__, test)
    return cls


NAMED = '''
trait Named => extend name -> String end
object Item => $label: String end
object Item as Named => define name => $self.label end
'''

SIZED = '''
trait Sized => extend size -> Int end
object Item => $value: Int end
object Other => $amount: Int end
object Item as Sized => define size => $self.value end
object Other as Sized => define size => $self.amount + 1 end
'''

SOURCE = '''
trait[T] Source => extend read -> T end
object[T] Box => $value: T end
object[T] Box as Source[T] => define read -> T => $self.value end
'''
