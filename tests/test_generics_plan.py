"""Executable Valiance generics-plan oracles, with isolated positive/negative cases.

IDs and semantic expectations correspond to
``docs/Valiance_Generics_Comptime_Runtime_Test_Plan.md``.
All runtime cases exercise unoptimised, optimised and serialized bytecode.
"""

import json
from itertools import permutations
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from valiance.incremental import CompilationDatabase
from valiance.runtime import dumps, loads, run
from valiance.runtime.runtime_values import ObjectValue, TaggedValue
from valiance.vtypes import DataTag

from trait_plan_support import SourceCase as C, TraitPlanTestCase, source_cases


BOX = 'object[T] Box => public $value: T end\n'
IDENTITY = 'define[T] keep(value: T) -> T => $value end\n'
TWO = 'define[T, U] left(a: T, b: U) -> T => $a end\n'
SIZED = '''
trait Sized => extend size -> Int end
object Item => $value: Int end
object Other => $value: Int end
object Item as Sized => define size => $self.value end
object Other as Sized => define size => $self.value + 1 end
'''
SOURCE = '''
trait[T] Source => extend read -> T end
object[T] Box => $value: T end
object[T] Box as Source[T] => define read -> T => $self.value end
'''


class GenericsPlanSourceTests(TraitPlanTestCase):
    """Real-source semantic tests; each ID can be invoked individually."""

    def test_GD_004(self):
        # A return-only generic must be explicitly instantiated or uniquely
        # determined by a contextual result type; do not choose a default T.
        declaration = 'define[T] \\empty -> T? => \\None end\n'
        self.check_case(C('GD-004', declaration + '\\empty[Int]',
                          typ='Int?', stack=[ObjectValue('None', {})]))
        self.check_case(C('GD-004', declaration + '$x: Int? = \\empty\n$x',
                          typ='Int?', stack=[ObjectValue('None', {})]))
        self.check_case(C('GD-004', declaration +
                          'define accept(x:Int?)->Int?=>$x end\naccept(\\empty)',
                          typ='Int?', stack=[ObjectValue('None', {})]))
        self.check_case(C('GD-004', declaration +
                          'define \\get -> Int? => \\empty end\n\\get',
                          typ='Int?', stack=[ObjectValue('None', {})]))
        self.check_case(C('GD-004', declaration + '\\empty',
                          reject=('cannot infer generic parameter(s) T',)))

    def test_UN_002(self):
        declaration = 'define[T] first(a:T,b:T)->T=>$a end\n'
        self.check_case(C('UN-002', declaration + 'first(10,2.5)',
                          typ='Real', stack=[10]))
        self.check_case(C('UN-002', declaration + 'first(10,"hello")',
                          typ='Int | String', stack=[10]))
        self.check_case(C('UN-002', declaration + 'first("hello",10)',
                          typ='Int | String', stack=['hello']))

    def test_UN_010(self):
        # Exact-rank input is allowed by a minimum-rank generic parameter.
        declaration = 'define[T] preserveMin(xs:T* novec)->T*=>$xs end\n'
        self.check_case(C('UN-010', declaration + 'preserveMin([1,2,3])',
                          typ='Int*', stack=[[1,2,3]]))
        self.check_case(C('UN-010', declaration +
                          'preserveMin([[1,2],[3,4]])',
                          typ='Int*2', stack=[[[1,2],[3,4]]]))

    def test_UN_012(self):
        # Exact/uniform + is a subtype of minimum *, which is a subtype of
        # rugged ~; a generic binder never reverses these guarantees.
        declaration = 'define[T] preserve(xs:T+ novec)->T+=>$xs end\n'
        self.check_case(C('UN-012', declaration +
                          '$x:Number~2=[[1,[2,3]],[4,[5,6]]]\npreserve($x)',
                          reject=('no overloads',)))
        self.check_case(C('UN-012', declaration +
                          '$x:Number* = [[1,2],[3,4]]\npreserve($x)',
                          reject=('no overloads',)))
        self.check_case(C('UN-012', declaration +
                          'preserve([1,2,3])', typ='Int+', stack=[[1,2,3]]))

    def test_OV_005(self):
        self.check_case(C('OV-005',
            'define[T] describe(x:T)->String=>"first" end\n'
            'define[U] describe(x:U)->String=>"second" end\n'
            'describe(42)', ['second']))
        self.check_case(C('OV-005',
            'define foo(x:Int)->String=>"first" end\n'
            'define foo(x:Int)->String=>"second" end\n'
            'foo(42)', ['second']))

    def test_IN_012(self):
        self.check_case(C('IN-012',
            '$f=fn(x)=>42 end\n$f(10)\n$f("hello")', [42,42]))

    def test_GD_018(self):
        # A declared generic is universal: no call is required to discover an
        # unproved body operation. A concrete builtin cannot narrow T to Int.
        self.check_case(C('GD-018',
            'define[T] increment(x: T) -> Number => $x + 1 end',
            reject=("no overloads for element '+'",)))
        self.check_case(C('GD-018',
            'define[T] increment(x: T) -> Number => $x + 1 end\n'
            'increment(3)',
            reject=("no overloads for element '+'",)))
        self.check_case(C('GD-018',
            'define[T] +(t: T, i: Int) -> Number => 19 end\n'
            'define[T] increment(x: T) -> Number => $x + 1 end\n'
            'increment("text")\nincrement(3)', [19, 19]))
        # Structural evidence may be declared in the parameter's type.
        self.check_case(C('GD-018',
            'define[T] increment(x: trait[T] => '
            'extend +(T, Int) -> Number end) -> Number => $x + 1 end\n'
            'increment(3)', [4]))
        # The parser also supports an explicit generic binder bound for define.
        self.check_case(C('GD-018',
            'define[T: trait => extend +(T, Int) -> Number end] '
            'increment(x:T) -> Number => $x + 1 end\n'
            'increment(3)', [4]))
        self.check_case(C('GD-018',
            'define[T: Number] increment(x:T)->Number => $x + 1 end\n'
            'increment(3)', [4]))
        self.check_case(C('GD-018',
            'define[T: Number] increment(x:T)->Number => $x + 1 end\n'
            'increment("text")', reject=('no overloads',)))
        # A lower bound is not evidence that T itself is numeric.
        self.check_case(C('GD-018',
            'define[T: above Number] increment(x:T)->Number => $x + 1 end',
            reject=("no overloads for element '+'",)))

    def test_generic_body_does_not_narrow_through_other_functions(self):
        self.check_case(C('GD-018',
            'define[U: Number] numeric(x: U) -> Number => $x + 1 end\n'
            'define[T] forwarding(x: T) -> Number => numeric($x) end',
            reject=('no overloads',)))
        self.check_case(C('GD-018',
            'define[U] identity(x: U) -> U => $x end\n'
            'define[T] forwarding(x: T) -> T => identity($x) end\n'
            'forwarding("ok")', ['ok']))
        self.check_case(C('GD-018',
            'object[T] Box => $value: T end\n'
            'define extract(x: Box[Int]) -> Int => $x.value end\n'
            'define[T] forward(x: Box[T]) -> Int => extract($x) end',
            reject=('no overloads',)))
        self.check_case(C('GD-018',
            'define[U: Number] numeric(x:U) -> Number => $x + 1 end\n'
            'define[T: Number] forwarding(x:T) -> Number => numeric($x) end\n'
            'forwarding(3)', [4]))
        # Untyped function parameters are inference metavariables, not rigid T.
        self.check_case(C('GD-018', '$f = fn (x) => $x + 1 end\n$f(3)', [4]))

    def test_unresolved_generic_tag_validator_is_not_silently_lowered(self):
        # A generic function with an explicit return can otherwise retain a
        # failed, untyped tag operation and bypass its validator at runtime.
        self.check_case(C(
            'TG-007',
            'tag #positive as computed\n'
            'define #positive(value: Number) -> #boolean Number => $value 0 > end\n'
            'define[T] checked(x: T) -> #positive T => $x #positive end\n'
            '-1 checked',
            reject=("no validator overload for '#positive' matches T",),
        ))
        # Unvalidated constructed/computed tags remain legal on generic data.
        self.check_case(C(
            'TG-007',
            'tag #mark as computed\n'
            'define[T] mark(x:T) -> #mark T => $x #mark end\n'
            'mark(3)',
            [TaggedValue(3, frozenset({DataTag('mark')}))],
        ))

    def test_OV_019(self):
        # Alpha-renamed generic binders describe indistinguishable imported
        # signatures and must conflict before a call selects one arbitrarily.
        self.check_case(C(
            'OV-019', 'import {a.pick, b.pick}\npick(2)',
            reject=("conflicting imported overload 'pick'",),
            modules={
                'a.vlnc': 'public define[T] pick(x:T)->T=>$x end',
                'b.vlnc': 'public define[U] pick(x:U)->U=>$x end',
            },
        ))
        # Binder correlation is part of the parameter shape. These two
        # signatures are not alpha equivalent despite matching printed arity.
        self.check_case(C(
            'OV-019', 'import {a.pick, b.pick}',
            modules={
                'a.vlnc': 'public define[T] pick(x:T,y:T)->String=>"same" end',
                'b.vlnc': 'public define[U,V] pick(x:U,y:V)->String=>"distinct" end',
            },
        ))

        # Persisted source-free module interfaces must retain the same import
        # conflict rule as source-level module imports.
        from valiance.analysis import Analyser
        from valiance.incremental import CompilationCoordinator
        from valiance.modules_system.modules import ModuleLoader
        from valiance.parsing import parse
        with TemporaryDirectory() as directory:
            root = Path(directory)
            for module, binder in (('a', 'T'), ('b', 'U')):
                path = root / f'{module}.vlnc'
                path.write_text(
                    f'public define[{binder}] pick(x:{binder})->{binder}=>$x end',
                    encoding='utf-8',
                )
                CompilationCoordinator(root).build_module(
                    path, path.with_suffix('.vbcm'), module_name=module)
                path.unlink()
            analyser = Analyser(module_loader=ModuleLoader(),
                                source_file=root / 'main.vlnc')
            analyser.analyse(parse('import {a.pick,b.pick}'))
            self.assertTrue(any('conflicting imported overload' in message
                                for message in analyser.diagnostics),
                            analyser.diagnostics)

    def test_MT_010(self):
        # Unrelated overload declaration order is not a resolution policy.
        declarations = (
            'define choose(x:Int)->String=>"int" end',
            'define choose(x:String)->String=>"string" end',
            'define[T] choose(x:T+ novec)->String=>"list" end',
        )
        for ordered in permutations(declarations):
            with self.subTest(order=ordered):
                self.check_case(C('MT-010', '\n'.join(ordered)
                                  + '\nchoose(1)\nchoose("s")\nchoose([1,2])',
                                  ['int', 'string', 'list']))

    def test_MT_014(self):
        # The source-level harness compares the exact same compiled union plan
        # before and after bytecode persistence, in both optimizer modes.
        self.check_case(C('MT-014',
            'define[T] describe(x: Some[T]) -> String => "some" end\n'
            'define describe(x: None) -> String => "none" end\n'
            '[1 as[Int?], Some(2), \\None] map: describe',
            [['some', 'some', 'none']],
        ))

    def test_CP_008(self):
        fn = '$f = fn[T] (x: T+ exact) -> T+ => $x end\n'
        self.check_case(C('CP-008', fn + '$f([1, 2])', [[1, 2]]))
        self.check_case(C('CP-008', fn + '$f([[1], [2]])', reject=('call',)))

    def test_CX_001(self):
        for calls, result in (
            ('keep[Int](1)\nkeep[String]("b")\nkeep[Int](3)', [1, 'b', 3]),
            ('keep[String]("a")\nkeep[Int](2)\nkeep[String]("c")', ['a', 2, 'c']),
        ):
            self.check_case(C('CX-001', IDENTITY + calls, result))

    def test_CX_002(self):
        for values in ((1, 'b', 3), ('a', 2, 'c')):
            terms = [json.dumps(value) for value in values]
            self.check_case(C('CX-002', IDENTITY + '\n'.join(f'keep({v})' for v in terms), list(values)))

    def test_CX_004(self):
        self.check_case(C('CX-004', 'define[T] wrong(x: T) -> T => 10 end\n"str" wrong',
                          reject=('return type mismatch',)))
        self.check_case(C('CX-004', IDENTITY + 'keep("good")', ['good']))

    def test_CX_005(self):
        source = IDENTITY + 'keep(3)\nkeep("three")'
        for _ in range(50):
            self.check_case(C('CX-005', source, [3, 'three']))

    def test_MT_001(self):
        for binder in ('T', 'U', 'Value'):
            self.check_case(C('MT-001', f'define[{binder}] keep(value: {binder}) -> {binder} => $value end\n'
                              'keep(7)\nkeep("s")', [7, 's']))

    def test_MT_002(self):
        for call in ('keep[Int](7)', 'keep(7)'):
            self.check_case(C('MT-002', IDENTITY + call, [7], typ='Int'))

    def test_MT_004(self):
        for call in ('keep(7)', '7 keep'):
            self.check_case(C('MT-004', IDENTITY + call, [7], typ='Int'))

    def test_MT_007(self):
        calls = ('keep(7)', 'keep("hello")', 'keep([1, 2])')
        for order in (calls, tuple(reversed(calls))):
            results = ([7], ['hello'], [[1, 2]])
            expected = [results[calls.index(s)][0] for s in order]
            self.check_case(C('MT-007', IDENTITY + '\n'.join(order), expected))

    def test_OV_007(self):
        # Tag-specific concrete signature must win over an untagged generic,
        # regardless of declaration order.  Generic-tag-vs-generic matching
        # is a separate unresolved specificity edge (see hardening guide).
        tagged = 'define label(x: #fresh Int) -> String => "tagged" end'
        generic = 'define[T] label(x: T) -> String => "generic" end'
        for definitions in permutations((tagged, generic)):
            source = ('tag #fresh as computed\n'
                      + '\n'.join(definitions)
                      + '\nlabel(1 #fresh)\nlabel(2)')
            self.check_case(C('OV-007', source, ['tagged', 'generic']))

    def test_OV_012(self):
        definitions = ('define[T] mark(x: T) -> String => "generic" end',
                       'define mark(x: Int) -> String => "concrete" end')
        for order in permutations(definitions):
            self.check_case(C('OV-012', '\n'.join(order) + '\nmark(1)\nmark("a")',
                              ['concrete', 'generic']))


source_cases(GenericsPlanSourceTests, [
    # Generic declarations and explicit argument handling.
    C('GD-001', IDENTITY + 'keep[Int](11)\nkeep[String]("hello")\nkeep[Int](37)', [11, 'hello', 37], typ='Int'),
    C('GD-002', TWO + 'left[Int, String](4, "no")\nleft[String, Int]("yes", 5)', [4, 'yes'], typ='String'),
    C('GD-005', 'trait[T: Number] Invalid => end', syntax=True),
    C('GD-006', 'define id(x: T) -> T => $x end', reject=('T',)),
    C('GD-007', 'define[U] id(x: U) -> U => $x end\nid(17)\nid("a")', [17, 'a']),
    C('GD-008', IDENTITY + 'keep[Int](3)\nkeep(3)', [3, 3]),
    C('GD-010', IDENTITY + 'keep[Int, String](1)', reject=('keep',)),
    C('GD-011', IDENTITY + 'keep[String](3)', reject=('keep',)),
    C('GD-012', IDENTITY + 'define[T] outer(x: T) -> T => keep[T]($x) end\nouter(3)\nouter("a")', [3, 'a']),
    C('GD-016', 'define[T, T] bad(x: T) -> T => $x end', syntax=True),

    # Unification, rank subtraction, optional shape.
    C('UN-001', 'define[T] first(a: T, b: T) -> T => $a end\nfirst(3, 4)', [3], typ='Int'),
    C('UN-003', IDENTITY + 'keep(11)\nkeep(2.5)\nkeep(13)', [11, 2.5, 13]),
    C('UN-004', BOX + 'define[T] unbox(b: Box[T]) -> T => $b.value end\nBox(9) unbox', [9], typ='Int'),
    C('UN-005', BOX + 'object[T] Other => $value: T end\n'
      'define[T] unbox(b: Box[T]) -> T => $b.value end\nOther(9) unbox', reject=('unbox',)),
    C('UN-006', 'define[T] head(xs: T+) -> T => $xs[0] end\n[2, 4] head', [2], typ='Int'),
    C('UN-007', 'define[T] retain(xs: T+) -> T+ => $xs end\n[[1], [2]] retain', [[[1], [2]]]),
    C('UN-008', 'define[T] retain(xs: T+2) -> T+2 => $xs end\n[[[1]], [[2]]] retain', [[[[1]], [[2]]]]),
    C('UN-016', 'define[T,U] choose(x: T | U) -> String => "ok" end\n'
      'choose(1 as[Int | String])', reject=('no overloads',)),
    C('UN-017', 'trait Shape => end\ntrait Vehicle => end\n'
      'object Car => end\nobject Car as Shape => end\n'
      'object Car as Vehicle => end\n'
      'define[T,U] choose(x: T & U) -> String => "ok" end\n'
      'choose(Car as[Shape & Vehicle])', reject=('no overloads',)),
    C('UN-009', 'define[T] deep(xs: T+3 exact) -> T+3 => $xs end\n[[1], [2]] deep', reject=('deep',)),
    C('UN-013', 'define[T] optional(x: T?) -> T? => $x end\n1 optional', [1]),

    C('UN-014', 'define[T] maybe(x: T?) -> T? => $x end\n'
      '\\None as[Int?] maybe | match =>\n as :None => 1\n as _ => 0\nend', [1]),

    # Call policies.
    C('CP-001', 'define[T] scalar(x: T exact) -> T => $x end\n3 scalar', [3]),
    C('CP-002', 'define[T] scalar(x: T exact) -> T => $x end\n[1, 2] scalar', reject=('scalar',)),
    C('CP-003', 'define[T] once(x: T+ exact) -> T+ => $x end\n[1, 2] once', [[1, 2]]),
    C('CP-004', 'define[T] once(x: T+ exact) -> T+ => $x end\n[[1], [2]] once', reject=('once',)),
    C('CP-009', 'define[T] whole(xs: T novec) -> T => $xs end\nwhole([1, 2, 3])', [[1, 2, 3]]),
    C('CP-010', 'define scalar(x: Number novec) -> Number => $x end\n[1, 2] scalar', reject=('scalar',)),
    C('CP-011', 'define scalar(x: Number+ novec) -> Number+ => $x end\n[[1], [2]] scalar', reject=('scalar',)),
    C('CP-018', 'define[T] choose(xs: T+ exact) -> String => "exact" end\n'
      'define[T] choose(xs: T) -> String => "generic" end\n[[1], [2]] choose', ['generic']),

    # Overloads and inference through anonymous functions / row types.
    C('OV-001', 'define[T] mark(x: T) -> String => "generic" end\n'
      'define mark(x: Int) -> String => "concrete" end\nmark(3)\nmark("x")', ['concrete', 'generic']),
    C('OV-006', SIZED + 'define mark(a: Item, b: Sized) -> String => "left" end\n'
      'define mark(a: Sized, b: Item) -> String => "right" end\nmark(Item(1), Item(2))', reject=('ambiguous',)),
    C('OV-011', 'define[T] choose(value: T+ exact) -> String => "rank" end\n'
      'define choose(value: Int) -> String => "scalar" end\n3 choose', ['scalar']),
    C('IN-001', '$f = fn (x) => $x end\n$f(42)\n$f("hi")', [42, 'hi']),
    C('IN-005', '$f = fn => $.x end\n$f(record{x => 5, y => "s"})', [5]),
    C('IN-006', '$f = fn[T, U] (x: T(.bar: U)) -> U => $x.bar end\n'
      '$f(record{bar => 31, other => "ok"})', [31]),
    C('IN-007', '$f = fn[T, U] (x: T(.bar: U)) -> U => $x.bar end\n'
      '$f(record{other => 5})', reject=('bar',)),
    C('IN-009', '$f = fn (x) => $x end\n$f(12)\n$f("s")\n$f(20)', [12, 's', 20]),

    # Generic objects and trait-argument correlation.
    C('OB-001', BOX + 'Box(17) | $.value\nBox("a") | $.value', [17, 'a']),
    C('OB-002', BOX + 'Box[Int]("wrong")', reject=('Box',)),
    C('OB-003', BOX + 'Box(Box(17)) | $.value | $.value', [17]),
    C('OB-004', 'object[T, U] Pair =>\n $first: T\n $second: U\nend\n'
      'Pair(1, "a") | $.first\nPair(2, "b") | $.second', [1, 'b']),
    C('OB-012', BOX + 'object[T] Other => $value: T end\n'
      'define inspect(x: Box[Int]) -> Int => $x.value end\nOther(1) inspect', reject=('inspect',)),
    C('TB-002', SOURCE + 'define consume(x: Source[String]) -> String => $x read end\n'
      'Box(1) consume', reject=('consume',)),
    C('TB-004', SOURCE + 'trait[T] Reader => extend first -> T end\n'
      'trait[T] Source as Reader[T] => define first -> T => $self read end\n'
      'define consume(x: Reader[String]) -> String => $x first end\nBox(1) consume', reject=('consume',)),

    # Rank, broadcast and runtime faults.
    C('RK-001', 'define[T] same(xs: T+ novec) -> T+ => $xs end\nsame([1, 2])', [[1, 2]]),
    C('RK-003', 'define[T] double(x: T) -> T => $x end\n[1, 2, 3] double', [[1, 2, 3]]),
    C('RK-004', 'define add(a: Int, b: Int) -> Int => $a + $b end\n'
      'add([1, 2], [10, 20])', [[11, 22]]),
    C('RK-005', 'define add(a: Int, b: Int) -> Int => $a + $b end\n'
      'add([1, 2], [10])', fault='VectorisationFault'),
    C('RK-007', 'define[T] whole(x: T novec) -> T => $x end\nwhole([1, 2])', [[1, 2]]),
    C('RK-008', 'define count(a: Number+ novec, b: Number) -> Number => length $a + $b end\n'
      'count([1, 2], [3, 4])', [[5, 6]]),

    # Higher-order functions and static evaluation.
    C('FV-001', 'define[T] forward(f: Function[T -> T], x: T) -> T => $f($x) end\n'
      'forward(fn(x: Int) -> Int => $x end, 3)', [3]),
    C('FV-002', 'define[T] forward(f: Function[T -> T], x: T) -> T => $f($x) end\n'
      'forward(fn(x: Int) -> String => "bad" end, 3)', reject=('forward',)),
    C('FV-003', 'define[T, U] applyAll(xs: T+, f: Function[T -> U]) -> U+ => $xs map: fn(item: T) -> U => $f($item) end end\n'
      'applyAll([1, 2, 3], fn(x: Int) -> String => "${$x}" end)', [['1', '2', '3']]),
    C('FV-010', IDENTITY + '$f = fn (x: Int) -> Int => keep($x) end\n$f(4)', [4]),
    C('WH-002', 'define[T] bad(x: T) -> T+$n where (1 2 == ?) => [$x] end\n1 bad', reject=('n',)),
    C('WH-003', 'define rank(xs: Number+$n) -> Number where ($r = $n + 1) => $r end\n'
      '[[1], [2]] rank', [3]),
    C('WH-007', 'define mark(xs: Number+$n) -> String where ($n 2 == ?) => "matrix" end\n'
      'define mark(xs: Number+) -> String => "list" end\n[1, 2] mark', ['list']),
    C('WH-009', 'define arity(f: Function[Int -> Int]) -> Number where ($n = $f.arity) => $n end\n'
      'fn(x: Int) -> Int => $x end arity', [1]),
    C('WH-010', 'define value(x: Number) -> Number where ($r = 13) => $r end\n1 value', [13]),
    C('WH-011', 'define bad(x: Number) -> Number where ($x + 1) => 1 end\n1 bad', reject=('where',)),
    C('WH-016', 'define[T] choose(x: T+) -> String where (1 2 == ?) => "bad" end\n'
      'define[T] choose(x: T+) -> String => "good" end\n[1] choose', ['good']),

    # Generic variants and runtime union branch selection.
    C('VA-001', 'variant[T] Maybe =>\n Some => $value: T end\n Empty => end\nend\n'
      'Some(7) match =>\n as :Some(item) => $item\n as :Empty => 0\nend', [7]),
    C('VA-004', 'variant[T] Maybe =>\n Some => $value: T end\n Empty => end\nend\n'
      'define unwrap(value: Maybe[Int]) -> Int =>\n'
      ' $value match =>\n  as :Some(item) => $item\n  as :Empty => 0\n end\nend\n'
      'Some(7) unwrap\n(Empty[Int]) unwrap', [7, 0]),
    C('VA-005', 'variant[T] Maybe =>\n Some => $value: T end\n Empty => end\nend\n'
      'define unwrap(x: Maybe[Maybe[Int]]) -> Int =>\n'
      ' $x match =>\n  as :Some(inner) =>\n   $inner match =>\n'
      '    as :Some(item) => $item\n    as :Empty => -1\n   end\n'
      '  as :Empty => -2\n end\nend\n'
      'Some(Some(17)) unwrap', [17]),
    C('UC-001', '$values = [1, 2, "A", "B"]\n$values map: * 2', [[2, 4, 'AA', 'BB']]),
    C('UC-002', 'define choose(x: Int) -> String => "int" end\n'
      'define choose(x: String) -> String => "str" end\n'
      '$f: Function[Int | String | Boolean -> String] = choose', reject=('choose',)),
    C('UC-004', 'define choose(x: Number) -> String => "number" end\n'
      'define choose(x: Real) -> String => "real" end\n'
      'define invoke(x: Number | String) -> String =>\n'
      ' $x match =>\n as :Number => $x choose\n as :String => "string"\n end\nend\n'
      '3 invoke', ['number']),
    C('IM-001', 'import {generic.identity[T](T)}\n"value" identity', ['value'],
      modules={'generic.vlnc': 'public define[U] identity(value: U) -> U => $value end'}),
    C('IM-004', 'import {generic.Box}\nBox(7) | $.value\nBox("word") | $.value', [7, 'word'],
      modules={'generic.vlnc': 'public object[T] Box => public $value: T end'}),
    C('IM-006', 'import {one.keep, two.keep}\nkeep(3)', reject=('conflicting',),
      modules={'one.vlnc': IDENTITY.replace('define[T]', 'public define[T]'),
               'two.vlnc': IDENTITY.replace('define[T]', 'public define[T]')}),
    C('IX-001', BOX + '@index(Box[T])\n'
      'define[T] read(box: Box[T], key: Int) -> T => $box.value end\n'
      '$box = Box(9)\n$box[0]', [9]),
    C('IX-013', 'object[T] Cell => public $item: T end\n'
      '$a = Cell(5)\n$b = $a\n$a.item = 8\n$a.item\n$b.item', [8, 5]),
    C('MT-009', IDENTITY + 'define keepInt(x: Int) -> Int => $x end\n'
      'keep(7)\nkeepInt(7)\nkeep("hi")', [7, 7, 'hi']),

    C('MT-005', 'define plusOne(x: Int) -> Int => $x + 1 end\n'
      '[1, 2, 3] plusOne\n[1 plusOne, 2 plusOne, 3 plusOne]',
      [[2, 3, 4], [2, 3, 4]]),

    # Additional exact-function, overload-hint, enum and rugged-shape checks.
    C('OV-008', 'define choose(x: Int) -> String => "int" end\n'
      'define choose(x: String) -> String => "str" end\nchoose{Int}(5)', ['int']),
    C('IN-004', 'define apply(f: Function[@1 -> @1], x: Int) -> Int => $f($x) end\n'
      'apply(fn(x: Int) -> Int => $x end, 4)', [4]),
    C('RK-009', 'define[T] strict(x: T+2 exact) -> T+2 => $x end\n'
      'strict([1, [2, 3]])', reject=('strict',)),
    C('VA-008', 'enum[Int] Token => A = "bad" end', reject=('incompatible',)),
    C('VA-012', 'enum Colour => RED = "red" end', reject=('cannot store a value',)),
    C('VA-013', 'enum[String] Token => A end', reject=('requires a value',)),

    # Second hardening pass: previously uncovered P0 compiler/runtime oracles.
    C('CP-005','define[T] deep(x: T+4 exact) -> T+4 => $x end\n[[[[1]]]] deep', [[[[[1]]]]]),
    C('CP-006','define[T] deep(x: T+4 exact) -> T+4 => $x end\n[[[1]]] deep',reject=('deep',)),
    C('CP-012','define[T] spread(x: T novec, y: Int) -> T => $x end\nspread([5, 6], [1, 2])',[[[5, 6], [5, 6]]]),
    C('OV-002','define[T] mark(x: T) -> String => "generic" end\ndefine mark(x: Int | String) -> String => "union" end\nmark(1)\nmark("q")',['generic','generic']),
    C('IN-002','$f = fn (x, y) => $x end\n$f(1, "q")\n$f("ok", 4)',[1,'ok']),
    C('IN-003','$f = fn (x) => $x end\n$f(10)\n$f("q")',[10,'q']),
    C('OB-006','object[T] Box =>\n public $value: T\n public define get -> T => $self.value end\nend\nBox(9) get\nBox("yes") get',[9,'yes']),
    C('OB-007','object[T] Box =>\n public $value: T\n public define identity -> Box[T] => $self end\nend\nBox(3) identity | $.value',[3]),
    C('OB-008','object[T] Box =>\n public $value: T\n public define wrong -> T => "wrong" end\nend\nBox(5) wrong',reject=('return type',)),
    C('TB-001',SOURCE+'define inspect(x: Source[Int]) -> Int => $x read end\nBox(3) inspect',[3]),
    C('TB-009','trait[T: Number] No => end',syntax=True),
    C('VA-002','variant[T] Maybe =>\n Some => $value: T end\n Empty => end\nend\nEmpty[Int] match =>\n as :Empty => 1\n as :Some(value) => 0\nend',[1]),
    C('VA-003','variant[T] Maybe =>\n Some => $value: T end\n Empty => end\nend\nSome[Int]("bad")',reject=('Some',)),
    C('VA-006','variant[T, U] Either =>\n Both =>\n $left: T\n $right: U\n end\nend\nBoth(9, "good") match =>\n as :Both(a, b) => $a $b\nend',[9,'good']),
    C('VA-007','enum[String] Token =>\n A = "apple"\n B = "banana"\nend\nToken.\\A.value\nToken.\\B.value',['apple','banana']),
    C('RK-002','define[T] same(x: T+2 novec) -> T+2 => $x end\nsame([[1,2],[3,4]])', [[[1,2],[3,4]]]),
    C('RK-006','define add(a: Int, b: Int) -> Int => $a + $b end\nadd([[1,2],[3,4]], [[10],[30,40]])',fault='VectorisationFault'),
    C('FV-004','define[T,U] fold(xs: T+, initial: U, f: Function[U,T -> U]) -> U => $initial end\nfold([1, 2], "init", fn(a: String, b: Int) -> String => $a end)',['init']),
    C('FV-009','define[T,U] pair(x: T,y: U) -> T,U => $x $y end\npair(1,"s")',[1,'s']),
    C('IM-002','import {generic.identity[_](T)}\n3 identity',reject=('generic',), modules={'generic.vlnc':'public '+IDENTITY}),
    C('IM-003','import {generic.identity[T](T, T)}\n3 identity',reject=('identity',), modules={'generic.vlnc':'public define[T] identity(x: T) -> T => $x end'}),
    C('IM-009','import {one.Box, two.Box}\nBox(3)',reject=('conflicting',),modules={'one.vlnc':'public '+BOX,'two.vlnc':'public '+BOX}),
    C('RT-003','define[T] same(x: T+ novec) -> T+ => $x end\nsame([1,2])\nsame([[3],[4]])',[[1,2], [[3],[4]]]),
    C('RT-005',BOX+'$a = Box(Box(1))\n$b = $a\n$a.value = Box(2)\n$a.value.value\n$b.value.value',[2,1]),
    C('RT-006','define[T,U] both(x: T, y: U) -> T, U => $x $y end\nboth(2,"s")',[2,'s']),
    C('RT-008','object[T] Box =>\n public $value: T\n public define get -> T => $self.value end\nend\nBox(12) get',[12]),
    C('EC-007','define[T] bad(x: T) -> T => if true => $x else => "not a T" end end\n1 bad',reject=('return',)),
    C('EC-008','define[T] early(x: T) -> T => $x return end\nearly("ok")',['ok']),
    C('EC-009','define[T,U] both(x:T, y:U) -> T,U => $x $y end\nboth(4, "four")',[4,'four']),
    C('IX-002',BOX+'@index(Box[String])\ndefine[T] bad(box: Box[T], index: Int) -> T => $box.value end',reject=('index',)),
    C('IX-003',BOX+'@update(Box[T])\ndefine[T] replace(box: Box[T], index: Int, value: T) -> Box[T] => Box($value) end\n$b = Box(5)\n$b[0] = 7\n$b.value',[7]),
    C('IX-004',BOX+'@update(Box[T])\ndefine[T] replace(box: Box[T], index: Int, value: T) -> T => $value end',reject=('update',)),
    C('IX-006','@index(Int+)\ndefine get(xs: Int+, i: Int) -> Int => 5 end',reject=('index',)),
    C('MT-003',IDENTITY+'$f = fn(x: Int) -> Int => keep($x) end\n$f(7)',[7]),

    # Third pass: call syntax, inference, ranked lifting, tuples and optional depth.
    C('GD-009', 'define[T,U] choose(a:T,b:U)->T => $a end\n'
      'choose[Int,_]{Int,String}(1,"v")', [1]),
    C('OV-009', 'define[T,U] choose(a:T,b:U)->T => $a end\n'
      'choose[Int,_]{Int,String}(1,"v")', [1]),
    C('OV-003', 'define[T] inspect(x:T+ novec)->String => "generic" end\n'
      'define inspect(x:Int)->String => "scalar" end\n[1,2] inspect', ['generic']),
    C('OV-004', 'trait Sized => extend size -> Int end\n'
      'object Item => $value: Int end\n'
      'object Item as Sized => define size => $self.value end\n'
      'define[T] select(x:T)->String => "generic" end\n'
      'define select(x:Sized)->String => "trait" end\n'
      'Item(3) select', ['generic']),
    C('RK-012', 'define[T] promote(x:T)->T+2 => $x ^+2 end\n'
      'promote(3)\npromote([1,2])', [[[3]], [[1, 2]]]),
    C('TB-006', SOURCE + 'define[T] inspect(x: Source[T])->T => $x read end\n'
      'Box(8) inspect\nBox("yes") inspect', [8, 'yes']),
    C('CS-006', 'define accept(xs: {Int, String..., Int})->String => "ok" end\n'
      '{1, 2} accept', ['ok']),
    C('CS-007', 'define accept(xs: {Int, String..., Int})->String => "ok" end\n'
      '{1,"a","b","c",2} accept', ['ok']),
    C('CS-008', 'define accept(xs: {Number..., String...})->String => "ok" end\n'
      '{1,2,"a","b"} accept', ['ok']),
    C('CS-009', 'define bad(x: {Int...}) -> {Int...} => $x end',
      syntax=True, reject=('arbitrary-length tuple types',)),
    C('CS-011', 'define keep(xs: {Number...,String})->String => "ok" end\n'
      '{1,2,"a"} keep', ['ok']),
    C('MT-006', 'define pick(x:Int)->String => "int" end\n'
      'define pick(x:String)->String => "str" end\n'
      'pick(3)\npick{Int}(3)', ['int', 'int']),
    C('MT-008', 'define[T] depth(xs:T+$n)->Number where($d = $n) => $d end\n'
      '[[[1]],[[2]]] depth', [3]),
    C('IN-011', '$f = fn => + end\n$f(3,4)\n$f("a","b")', [7, 'ab']),
    C('IX-008', 'define[T,U] read(xs: Dict[T,U] novec,key:T)->U? => $xs[$key] end\n'
      'read(dict{1=>"one"},1)', ['one']),
    C('IM-007', 'import {one.keep}\nkeep(3)', [3],
      modules={'one.vlnc': 'public ' + IDENTITY,
               'two.vlnc': 'public ' + IDENTITY}),
    C('UN-015', 'define[T] preserve(x:T??)->T?? => $x end\n'
      '\\None as[Int??] preserve\nSome(\\None as[Int?]) preserve',
      [ObjectValue('None', {}),
       ObjectValue('Some', {'value': ObjectValue('None', {})}, ('Int?',))],
      typ='(Int?)?'),
    C('EC-003', 'define[T] preserve(x:T??)->T?? => $x end\n'
      '\\None as[Int??] preserve\nSome(\\None as[Int?]) preserve',
      [ObjectValue('None', {}),
       ObjectValue('Some', {'value': ObjectValue('None', {})}, ('Int?',))],
      typ='(Int?)?'),

    # Runtime representation and failure/recovery.
    C('RT-001', BOX + IDENTITY + 'keep(3)\nkeep("hello")\nkeep([1, 2]) | length\nkeep(Box(3)) | $.value',
                        [3, 'hello', 2, 3]),
    C('RT-002', IDENTITY + 'keep[Int](1)\nkeep[String]("s")\nkeep[Int](2)', [1, 's', 2]),
    C('RT-004', BOX + 'Box(7) | $.value\nBox("x") | $.value', [7, 'x']),
    C('RT-007', IDENTITY + '$f = fn(x: Int) -> Int => keep($x) end\n$f(3)', [3]),
    C('EC-001', 'define[T] keepSome(x: T?) -> T? => $x end\n1 keepSome', [1]),
    C('EC-002', 'define[T] keepSome(x: T?) -> T? => $x end\n'
      '\\None as[Int?] keepSome | match =>\n as :None => 1\n as _ => 0\nend', [1]),
    C('IX-007', 'define[T, U] keep(x: record(.x:T, .y:U)) -> U => $x.y end\n'
      'record{x => 3, y => "good"} keep', ['good']),

    # Fourth pass: return-only generics, inferred callables, tuple arity and tags.
    C('GD-003', 'define[T] \\empty -> T? => \\None end\n\\empty[Int]',
      [ObjectValue('None', {})], typ='Int?'),
    C('IN-008', 'define[T] requireInt(x: T(.bar: Int)) -> Int => $x.bar end\n'
      'requireInt(record{bar => "bad"})', reject=('no overloads',)),
    C('IN-010', '$f = fn => + double end\n$f(2,3)', [8], typ='Number'),
    C('FV-005', 'define[T] factory(x:T) -> Function[T -> T] => '
      'fn(y:T) -> T => $y end end\n'
      '$f = factory(3)\n$f(9)\n'
      '$g = factory("hello")\n$g("good")', [9, 'good']),
    C('FV-007', 'define use(f: Function[@1 -> @1], x: Int) -> Int => '
      '$f($x) end\nuse(fn(x:Int) -> Int => $x end, 5)', [5]),
    C('FV-008', 'define[T] thunk(f: Function[ -> T]) -> T => $f() end\n'
      'thunk(fn -> Int => 42 end)', [42]),
    C('CS-004', 'define accept(xs: {Int...}) -> String => "ok" end\n'
      '{} accept', ['ok']),
    C('CS-010', 'define accept(xs: {Int, Int}) -> String => "ok" end\n'
      '{1, 2, 3} accept', reject=('no overloads',)),
    C('CP-014', 'define[T] select(xs:T+ novec, x:T exact)->T => $x end\n'
      'select([1,2], [3,4])', reject=('no overloads',)),
    C('TG-004', 'tag #km as unit\n'
      'define[T] take(value: #km T) -> String => "yes" end\n'
      '1 #km | take', ['yes']),
    C('TG-005', 'tag #km as unit\n'
      'define[T] bare(x: Number, sample:T) -> Number => $x end\n'
      'bare(1 #km, 3)', reject=('no overloads',)),
    C('TG-006', 'tag #km as unit\n'
      'define[T] stamped(x:T)->#km T => $x end\n'
      'stamped(1)', [TaggedValue(1, frozenset({DataTag("km")}))],
      typ='#km Int'),
    C('TG-008', 'tag #positive as computed\n'
      'define[T] negative(x:#-positive T) -> T => $x end\n'
      '1 #positive | negative', reject=('no overloads',)),
    C('TG-007', 'tag #positive as computed\n'
      'define #positive(value:Number) -> #boolean Number => $value 0 > end\n'
      'define[T] checked(x:Number, marker:T) -> #positive Number '
      '=> $x #positive end\nchecked(-1, "marker")',
      fault='tag validator #positive failed'),
    C('RT-009', 'define[T] keep(x:T) -> T => $x end\n'
      '$x = 1 as[Int | String]\n'
      '$x as?[Int] | match =>\n'
      ' as :Some(value) => $value keep\n'
      ' as :None => 0\nend', [1], typ='Int'),

    C('EC-004', 'object ParseError => end\nobject ParseError as Err => end\n'
      'define[T,E] mapIt(value: Result[T,E], f: Function[T -> T]) -> Result[T,E] '
      '=> $value & ($f) end\n'
      'mapIt(OK(3) as[Result[Int,ParseError]], '
      'fn(x:Int) -> Int => $x + 2 end)',
      [ObjectValue('OK', {'value': 5}, ('T', 'E'))],
      typ='Result[Int, ParseError]'),
    C('EC-005', 'object ParseError => end\nobject ParseError as Err => end\n'
      'define[T,E] mapIt(value: Result[T,E], f: Function[T -> T]) -> Result[T,E] '
      '=> $value & ($f) end\n'
      'mapIt(ParseError as[Result[Int,ParseError]], '
      'fn(x:Int) -> Int => $x + 2 end)',
      [ObjectValue('ParseError', {})], typ='Result[Int, ParseError]'),
    C('IX-005', 'trait[T] Indexable => extend fetch -> T end\n'
      'object[T] Box => $value:T end\n'
      'object[T] Box as Indexable[T] => '
      'define fetch -> T => $self.value end\n'
      '@index(Indexable[T])\n'
      'define[T] get(box:Indexable[T], key:Int) -> T => $box fetch end\n'
      '$b = Box(42)\n$b[0]', [42], typ='Int'),
])


# Critical remaining-plan scenarios with end-to-end compile/execute/round-trip
# oracles.  Every successful case runs with and without optimization.
source_cases(GenericsPlanSourceTests, [
    C('OB-005', 'object[T] Box =>\n'
      '  public $value: T\n'
      '  define Box(value:T) => $self.value = $value end\n'
      'end\n'
      'Box(3) $.value\nBox("hi") $.value', [3, 'hi']),
    C('CS-002', '@returnAll define invoke(f: Function) => $f call end\n'
      'fn -> => end invoke\n'
      'fn -> Int, String => 3 "x" end invoke', [3, 'x']),
    C('CP-007', 'define[T] inspect(xs: T+4 exact) -> T+4 => $xs end\n'
      '$x: Number+4 = [[[[1]]]]\ninspect($x)', [[[[[1]]]]], typ='Number+4'),
    C('TB-005', 'trait[T,U] Source => end\n'
      'trait[T,U] Middle => end\n'
      'trait[T,U] Reader => end\n'
      'trait[T,U] Source as Middle[U,T] => end\n'
      'trait[T,U] Middle as Reader[T,U] => end\n'
      'object[T,U] Pair =>\n  $a:T\n  $b:U\nend\n'
      'object[T,U] Pair as Source[T,U] => end\n'
      'define describe(x: Reader[String,Int]) -> String => "ok" end\n'
      'Pair(1,"x") describe', ['ok']),
    C('TB-007', 'define[T] sum(values: trait[T] => '
      'extend +(:T, :T) -> T end +) -> T => reduce: + end\n'
      'sum([1, 2, 3])', [6], typ='Int'),
    C('TB-008', 'object Weird => end\n'
      'define +(left: Weird, right: Weird) -> String => "wrong" end\n'
      'define[T] sum(values: trait[T] => '
      'extend +(:T, :T) -> T end +) -> T => reduce: + end\n'
      'sum([Weird, Weird])', reject=('no overloads',)),
    C('UC-006', 'trait[T] Payload => end\n'
      'object[T] Box => $value:T end\n'
      'object[T] Box as Payload[T] => end\n'
      'define[T] describe(x: Payload[T]) -> String => "payload" end\n'
      'define describe(x: String) -> String => "string" end\n'
      '[Box(1), "x", Box(2)] map: describe',
      [['payload', 'string', 'payload']]),
    C('UC-007', 'tag #left as computed\n'
      'tag #right as computed\n'
      'tag #left disjoint #right\n'
      'define[T] describe(x: #left T) -> String => "left" end\n'
      'define[T] describe(x: #right T) -> String => "right" end\n'
      '[1 #left, 2 #right] map: describe', [['left', 'right']]),
    C('UC-008', 'define[T] describe(x: Some[T]) -> String => "some" end\n'
      'define describe(x: None) -> String => "none" end\n'
      '[1 as[Int?], Some(2), \\None] map: describe',
      [['some', 'some', 'none']]),
    C('EC-006', 'object ParseError => end\n'
      'object ParseError as Err => end\n'
      'define choose(x: Int | ParseError) -> Result[Int, ParseError] => $x end\n'
      'choose(3)\nchoose(ParseError)', [3, ObjectValue('ParseError', {})],
      typ='Result[Int, ParseError]'),
])


# P1 extensions: independent binders, recursion, field layouts, runtime shape,
# nested optionals, callable captures and typed tuple indexing.
source_cases(GenericsPlanSourceTests, [
    C('GD-013', 'define[T] keep(x:T)->T=>$x end\n'
      'define pick(x:Int)->Int=>0 end\n'
      'define pick(x:String)->String=>"other" end\n'
      'keep(7)\nkeep("text")', [7, 'text']),
    C('GD-014', 'define[T] outer(x:T) -> T =>\n'
      '  define[T] inner(y:T) -> T => $y end\n'
      '  inner($x)\nend\nouter(2)\nouter("a")', [2, 'a']),
    C('GD-017', 'define[T] repeat(x:T,n:Int)->T =>\n'
      '  if ($n == 0) => $x else => repeat[T]($x,$n - 1) end\n'
      'end\nrepeat("ok",3)', ['ok']),
    C('OB-013', 'object[T] Box => public $values: T+ end\n'
      'Box([1,2]) $.values\nBox(["a","b"]) $.values',
      [[1, 2], ['a', 'b']]),
    C('OB-015', 'object[T] Box => public $maybe: T? end\n'
      'Box(Some(1)) $.maybe',
      [ObjectValue('Some', {'value': 1}, ('Int',))]),
    C('OB-017', 'object[T] Box =>\n  public $value:T\n'
      '  define read -> T => $self.value end\nend\n'
      'Box(1) read\nBox("x") read\nBox(2) read', [1, 'x', 2]),
    C('RT-012', 'define[T] keep(x:T) -> T => $x end\n'
      '[] as[Int+] | map: keep\n'
      '[' + ', '.join(str(n) for n in range(64)) + '] map: keep',
      [[], list(range(64))]),
    C('RK-014', 'define[T] one(xs:T+3)->T+3=>$xs end\n'
      'define[T] two(xs:T+++)->T+++=>$xs end\n'
      '$x=[[[1]]]\n$x one\n$x two', [[[[1]]], [[[1]]]]),
    C('EC-013', 'define[T] one(x:T??)->T??=>$x end\n'
      'define[T] two(x:T?2)->T?2=>$x end\n'
      'Some(Some(1)) one\nSome(Some(1)) two',
      [ObjectValue('Some', {'value': ObjectValue('Some', {'value': 1}, ('Int',))}, ('Some[Int]',))] * 2),
    C('IX-010', 'define[T] fetch(xs:T+, i:Int) -> T => $xs[$i] end\n'
      '$i = 2\nfetch([1,2,3], $i)', [3], typ='Int'),
    C('IX-011', 'define[T,U] first(xs:{T,U}) -> T => $xs[0] end\n'
      'first({1,"x"})', [1], typ='Int'),
    C('IX-012', 'define[T,U] fetch(xs:{T,U},i:Int)->T|U=>$xs[$i] end\n'
      '$i = 1\nfetch({1,"x"},$i)', ['x']),
    C('FV-011', 'define[T] make(x:T)->Function[ -> T]=> '
      'fn -> T => $x end end\n'
      '$f=make(3)\n$f()\n$g=make("a")\n$g()', [3, 'a']),
    C('FV-012', 'define[T] make(x:T)->Function[ -> T]=> '
      'fn -> T => $x end end\n'
      '$f=make(3)\n$g=make("a")\n$f()\n$g()\n$f()', [3, 'a', 3]),
])


# Additional P1/P2 regression oracles. These retain their true plan IDs and
# are executed under optimized, unoptimized and serialized execution.
source_cases(GenericsPlanSourceTests, [
    C('GD-015', 'import {a.first, b.second}\nfirst(5)\nsecond("x")',
      [5, 'x'], modules={
          'a.vlnc': 'public define[T] first(x:T)->T=>$x end',
          'b.vlnc': 'public define[T] second(x:T)->T=>$x end',
      }),
    C('IN-013', 'define[T,U,V] both(x:T(.foo:U,.bar:V))->U,V => '
      '$x.foo $x.bar end\nboth(record{foo=>3,bar=>"hi"})', [3, 'hi']),
    C('IN-015', 'define[T,U] read(x:T(.foo:U?))->U?=>$x.foo end\n'
      'read(record{foo=>Some(2)})',
      [ObjectValue('Some', {'value': 2}, ('Int',))]),
    C('CP-017', 'define[T] bad(x: exact T)->T=>$x end', syntax=True),
    C('IM-010', 'import {secret.[keep]}\nkeep(2)',
      reject=("no public component 'keep'",),
      modules={'secret.vlnc': 'define[T] keep(x:T)->T=>$x end'}),
    C('IM-011', 'import {lib.[keep as saved]}\nsaved(2)\nsaved("hi")',
      [2, 'hi'], modules={'lib.vlnc': 'public define[T] keep(x:T)->T=>$x end'}),
    C('RK-013', '^+', reject=('empty stack',)),
    C('RK-018', 'define[T] keep(x:T)->T=>$x end\n[] as[Int+] | map: keep', [[]]),
    C('FV-013', '$top=3\ndefine[T] make(x:T)->Function[ -> T] => '
      'fn -> T => $top end end', reject=('cannot capture top-level variable',)),
    C('OV-015', 'define[T] choose(x:T,y:Int=3)->T=>$x end\n'
      'choose(x="x")', ['x']),
    C('OV-016', 'define[T] choose(x:T,y:Int=3)->T=>$x end\n'
      '"x" choose', reject=('no overloads',)),
    C('TB-014', 'trait[T] Source => extend read->T end\n'
      'object[T] Box => $value:T end\n'
      'object[T] Box as Source[T] => define read->T=>$self.value end\n'
      'define[T] apply(f:Function[Source[T]->T],x:Source[T])->T=>$f($x) end\n'
      'apply(fn(x:Source[Int])->Int=>$x read end, Box(3))', [3]),
    C('MT-012', 'define[T] three(x:T+++)->T+++=>$x end\n'
      'define[T] rank(x:T+3)->T+3=>$x end\n'
      'three([[[1]]])\nrank([[[2]]])', [[[[1]]], [[[2]]]]),
    C('MT-013', 'define[T] nested(x:T??)->T??=>$x end\n'
      'define[T] count(x:T?2)->T?2=>$x end\n'
      'nested(Some(Some(1)))\ncount(Some(Some(2)))', [
          ObjectValue('Some', {'value': ObjectValue('Some', {'value': 1}, ('Int',))}, ('Some[Int]',)),
          ObjectValue('Some', {'value': ObjectValue('Some', {'value': 2}, ('Int',))}, ('Some[Int]',)),
      ]),
    C('GD-020', 'define[Value] keep (value: Value) -> Value => $value end\n'
      'keep("hey")\nkeep(4)', ['hey', 4]),
    C('FV-017', 'define[T] keep(x:T)->T=>$x end\n'
      'define[U] wrapper(x:U)->U=>keep($x) end\n'
      'keep(3)\nwrapper(3)\nkeep("x")\nwrapper("x")', [3, 3, 'x', 'x']),
    C('IX-014', 'object[T] Box => public $value:T end\n'
      '@index(Box[T])\n'
      'define[T] read(b:Box[T], key:Int)->T=>$b.value end\n'
      '$b=Box(7)\n$b[0]\n$b.value', [7, 7]),
])

# Source-backed continuation: these cases have concrete compile and runtime
# oracles. Successful cases run in both optimizer modes and after serialization.
source_cases(GenericsPlanSourceTests, [
    C('UC-011',
      'define label(x:Int)->String=>"int" end\n'
      'define label(x:String)->String=>"str" end\n'
      '[1 as[Int|String],"b" as[String|Int]] map: label',
      [['int', 'str']]),
    C('IN-016',
      'object Secret => private $secret: Int end\n'
      'define[T] read(x:T(.secret:Int))->Int=>$x.secret end\n'
      'read(Secret(3))', reject=('no overloads',)),
    C('UN-021',
      'define[T,U] apply(pair:{T,U}, f:Function[T->U], maybe:T?) -> U? '
      '=> $f($pair[0]) end\n'
      'apply({3,"a"},fn(x:Int)->String=>"hi" end,Some(3))',
      ['hi']),
    C('IN-014',
      'define[T,U] apply(f:Function[T->U],x:T)->U=>$f($x) end\n'
      '$f=fn => $.bar end\napply($f,record{bar=>3})', [3]),
    C('OB-016',
      'object[T] Box => $value:T = "not every T" end',
      reject=("default for field 'value' is not assignable to T",)),
    C('OB-020',
      'object[T] Box => public $value:T end\n'
      'Box(3) $.value\nBox("x") $.value\n'
      'Box([1,2]) $.value\nBox({3,"ok"}) $.value',
      [3, 'x', [1, 2], (3, 'ok')]),
    C('VA-014',
      'object Oops=>end\nobject Oops as Err=>end\n'
      'define[T,E] keep(x:Result[T,E])->Result[T,E]=>$x end\n'
      'keep(OK(3) as[Result[Int,Oops]])\n'
      'keep(Oops as[Result[Int,Oops]])',
      [ObjectValue('OK', {'value': 3}, ('Int',)), ObjectValue('Oops', {})]),
    C('FV-016',
      'define[T] first(x:T)->T=>$x end\n'
      'define[T,U] second(x:T,y:U)->U=>$y end\n'
      '1 2 3 sequence: (first,second)', [1, 3]),
    C('CS-014',
      'define[T] repeat(x:T)->T,T => both: fn -> T => $x end end\n'
      'repeat(5)\nrepeat("a")', [5, 5, 'a', 'a']),
    C('IM-012',
      'define[T] inner(x:T)->T=>\n'
      '  import {lib.[keep]}\n'
      '  keep($x)\n'
      'end\ninner(3)\ninner("x")', [3, 'x'],
      modules={'lib.vlnc':'public define[T] keep(x:T)->T=>$x end'}),
    C('RT-015',
      'define \\zero -> Int => 9 end\n'
      'define[T] one(x:T)->T=>$x end\n'
      'define[T,U] two(x:T,y:U)->U,T=>$y $x end\n'
      'define[T,U,V,W] four(a:T,b:U,c:V,d:W)->T,U,V,W=>$a $b $c $d end\n'
      '\\zero\none(2)\ntwo("x",3)\n'
      'four[Int,String,Int,Int](1,"s",3,4)',
      [9, 2, 3, 'x', 1, 's', 3, 4]),
    C('EC-010',
      'define[T,U] pair(x:T,y:U)->T,U=>$x $y end\n'
      'define[T,U] gather(x:T,y:U)->{T,U} '
      '=> $x $y @@tupled pair end\n'
      'gather(3,"a")', [(3, 'a')]),
    C('IX-009',
      'object[T] Box=>public $value:T end\n'
      '@index(Box[T])\n'
      'define[T] get(b:Box[T],i:Int)->T=>$b.value end\n'
      '$b=Box(5)\n$b[0,1]', [[5, 5]]),
    C('WH-017',
      'define make(shape:{Number...})->Int+$n '
      'where ($n = max(1,length $shape)) => [1] as![Int+$n] end\n'
      'make({})', [[1]], typ='Int+'),
])



class GenericsContinuationProperties(TraitPlanTestCase):
    """Metamorphic checks that compare independently compiled source variants."""

    def test_GD_019(self):
        declared = 'define[T] keep(x:T)->T=>$x end'
        for program in (declared + '\nkeep(3)\nkeep("x")',
                        'keep(3)\nkeep("x")\n' + declared):
            with self.subTest(program=program):
                self.check_case(C('GD-019', program, [3, 'x']))

    def test_CS_016(self):
        declared = '@returnAll define invoke(f:Function) => $f call end\n'
        empty = 'fn -> => end invoke'
        pair = 'fn -> Int,String => 3 "x" end invoke'
        for calls in ((empty, pair), (pair, empty)):
            with self.subTest(calls=calls):
                self.check_case(C('CS-016', declared + '\n'.join(calls), [3, 'x']))

    def test_valid_generic_field_defaults_remain_legal(self):
        # The absence default is valid for all T; a concrete numeric default
        # is valid on a field whose declared type is genuinely numeric.
        self.check_case(C('OB-016',
            'object[T] Box => $value:T? = \\None end\n'
            'object NumberBox => $value:Number = 5 end'))

    def test_MT_011(self):
        library = {'lib.vlnc': 'public define[T] keep(x:T)->T=>$x end'}
        for left, right in (('alpha', 'beta'), ('one', 'two')):
            with self.subTest(aliases=(left, right)):
                program = (f'import {{lib.[keep as {left}]}}\n'
                           f'{left}(3)\n'
                           f'import {{lib.[keep as {right}]}}\n'
                           f'{right}("x")')
                self.check_case(C('MT-011', program, [3, 'x'], modules=library))


class GenericsRuntimeInputTests(unittest.TestCase):
    """Optimizer may not constant-fold runtime input through generic calls."""

    def test_RT_010(self):
        from contextlib import redirect_stdout
        from io import StringIO
        from unittest.mock import patch
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.runtime import compile_program

        source = ('define[T] keep(x: T) -> T => $x end\n'
                  'input("name?") keep')
        analyser = Analyser()
        typed = analyser.analyse(parse(source))
        self.assertEqual(analyser.diagnostics, [])
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            for persisted, candidate in ((False, program),
                                         (True, loads(dumps(program)))):
                for provided in ('first', 'second'):
                    with self.subTest(optimize=optimize,
                                      persisted=persisted, provided=provided):
                        with patch('builtins.input', return_value=provided) as read:
                            with redirect_stdout(StringIO()) as output:
                                self.assertEqual(run(candidate), [provided])
                            read.assert_called_once_with('name?')
                            self.assertEqual(output.getvalue(), '')


class GenericsIncrementalPlanTests(unittest.TestCase):
    """Validate imported generic dependencies through real workspace invalidation."""

    def check_library_revision(self, initial, changed, source, imports,
                               expected_before, *, expected_after=None, diagnostic=None):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / "library.vlnc"
            main = root / "main.vlnc"
            library.write_text(initial, encoding="utf-8")
            main.write_text(
                "import {" + ", ".join("library." + name for name in imports)
                + "}\n" + source,
                encoding="utf-8",
            )
            database = CompilationDatabase(root)

            def check_success(expected):
                snapshot = database.analyse(main)
                self.assertEqual(snapshot.analyser.diagnostics, [])
                for optimize in (False, True):
                    program = database.compile_current(main, optimize=optimize).program
                    self.assertEqual(run(program), expected)
                    self.assertEqual(run(loads(dumps(program))), expected)
                return snapshot

            original = check_success(expected_before)
            database.open_document(library, changed)
            if diagnostic is not None:
                updated = database.analyse(main)
                self.assertIsNot(updated, original)
                self.assertTrue(
                    any(diagnostic in message for message in updated.analyser.diagnostics),
                    updated.analyser.diagnostics,
                )
                with self.assertRaises(RuntimeError):
                    database.compile_current(main)
            else:
                updated = check_success(expected_after)
                self.assertIsNot(updated, original)
            database.close_document(library)
            restored = check_success(expected_before)
            self.assertIsNot(restored, updated)

    def test_CX_006(self):
        self.check_library_revision(
            'public define[T] change(x:T)->T => $x end',
            'public define[T] change(x:T)->String => "new" end',
            'change(3) 1 +', ('change',), [4], diagnostic="no overloads",
        )

    def test_CX_007(self):
        self.check_library_revision(
            'public object[T] Box => $value: T end',
            'public object[T] Box => public $value: T end',
            'define take(x: Box[Number])->Number => $x.value end\n'
            'Box[Int](1) take', ('Box',), [1], diagnostic="no overloads",
        )

    def test_CX_008(self):
        source = ('public trait[T] Source => extend read -> T end\n'
                  'public trait[T] Reader => extend read -> T end\n')
        relation = ('public trait[T] Source as Reader[T] => '
                    'define read -> T => $self read end\n')
        obj = ('public object[T] Box => $value: T end\n'
               'public object[T] Box as Source[T] => '
               'define read -> T => $self.value end')
        self.check_library_revision(
            source + relation + obj, source + obj,
            'define take(x: Reader[Int]) -> Int => $x read end\nBox(3) take',
            ('Reader', 'Source', 'Box'), [3], diagnostic="no overloads",
        )

    def test_CX_009(self):
        def source(rank):
            return (
                'public define[T] choose(x:T+$n)->String '
                f'where ($n {rank} == ?) => "rank" end\n'
                'public define[T] choose(x:T novec)->String => "fallback" end'
            )
        self.check_library_revision(
            source(1), source(2), '[1,2] choose', ('choose',),
            ['rank'], expected_after=['fallback'],
        )

    def test_CX_010(self):
        """Exact generic list ranks remain stable from one to twenty levels."""
        for rank in range(1, 21):
            with self.subTest(rank=rank):
                value = 1
                for _ in range(rank):
                    value = [value]
                literal = '[' * rank + '1' + ']' * rank
                self_case = C(
                    'CX-010',
                    f'define[T] keep(xs:T+{rank} novec)->T+{rank} => $xs end\n'
                    f'{literal} keep',
                    [value],
                )
                TraitPlanTestCase.check_case(self, self_case)

    def test_CX_012(self):
        """Fifty genuinely different nominal instantiations share no cache entry."""
        count = 50
        source = ('define[T] keep(x:T)->T=>$x end\n'
                  + '\n'.join(f'object Case{i:02d} => end' for i in range(count))
                  + '\n'
                  + '\n'.join(f'keep(Case{i:02d})' for i in range(count)))
        expected = [ObjectValue(f'Case{i:02d}', {}) for i in range(count)]
        TraitPlanTestCase.check_case(self, C('CX-012', source, expected))

    def test_CX_013(self):
        """Inapplicable concrete candidates cannot pollute a generic winner."""
        count = 35
        source = ('\n'.join(f'object Case{i:02d} => end' for i in range(count))
                  + '\n'
                  + '\n'.join(
                      f'define choose(x:Case{i:02d})->String=>"nominal" end'
                      for i in range(count))
                  + '\n'
                  + 'define[T] choose(x:T)->String=>"generic" end\n'
                  + 'choose("first")\nchoose(17)\nchoose("last")')
        TraitPlanTestCase.check_case(self, C('CX-013', source,
                                             ['generic', 'generic', 'generic']))

    def test_CX_015(self):
        """Cached and fresh type analysis agree for imported generic callers."""
        import valiance.vtypes as T
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'lib.vlnc').write_text(
                'public define[T] keep(x:T)->T=>$x end', encoding='utf-8')
            main = root / 'main.vlnc'
            main.write_text('import {lib.keep}\nkeep(4)\nkeep("word")', encoding='utf-8')
            database = CompilationDatabase(root)
            clean = database.analyse(main)
            cached = database.analyse(main)
            fresh = CompilationDatabase(root).analyse(main)
            self.assertIs(clean, cached)
            self.assertEqual(clean.analyser.diagnostics, [])
            self.assertEqual(clean.analyser.diagnostics, fresh.analyser.diagnostics)
            self.assertEqual([T.show(node.typ) for node in clean.typed],
                             [T.show(node.typ) for node in fresh.typed])
            for optimize in (False, True):
                left = database.compile_current(main, optimize=optimize).program
                right = CompilationDatabase(root).compile_current(main, optimize=optimize).program
                self.assertEqual(run(left), [4, 'word'])
                self.assertEqual(run(right), run(left))
                self.assertEqual(run(loads(dumps(left))), run(right))

    def test_CX_011(self):
        """Nested nominal generic types retain their binder and field layout."""
        for depth in (1, 4, 8, 12):
            with self.subTest(depth=depth):
                literal = 'Box(' * depth + '7' + ')' * depth
                self_case = C('CX-011', BOX + literal + ' | $.value' * depth, [7])
                TraitPlanTestCase.check_case(self, self_case)

# Further coverage uses independent programs for each plan oracle.  Every
# successful source is checked under both optimizer settings and after reload.
source_cases(GenericsPlanSourceTests, [
    C('UN-019',
      'define[T] pick(xs:T+ novec,y:T exact)->T=>$y end\n'
      'define[T] reverse(y:T exact,xs:T+ novec)->T=>$y end\n'
      'pick([1,2],3)\nreverse(3,[1,2])', [3,3], typ='Int'),
    C('CP-016',
      'define[T] keep(x:T? exact)->T?=>$x end\n'
      'keep(Some(3))\nkeep(\\None as[Int?])',
      [ObjectValue('Some', {'value':3}, ('Int',)), ObjectValue('None', {})]),
    C('OV-017',
      'define[T,U] pair(a:T,b:U)->T,U=>$a $b end\n'
      'pair(b="hello",a=4)\n'
      '4 pair(_,b="again")', [4,'hello',4,'again']),
    C('OV-018',
      'import {lib.mark}\n'
      'define mark(x:Int)->String=>"local" end\n'
      'mark(3)\nmark("hello")', ['local','imported'],
      modules={'lib.vlnc': 'public define[T] mark(x:T)->String=>"imported" end'}),
    C('IN-018',
      'define[A,B,C] read(x:A(.first:B,.second:C))->B,C=>$x.first $x.second end\n'
      'define[X,Y,Z] renamed(x:X(.second:Z,.first:Y))->Y,Z=>$x.first $x.second end\n'
      'read(record{first=>3,second=>"s"})\n'
      'renamed(record{second=>"s",first=>3})', [3,'s',3,'s']),
    C('OB-019',
      'object[T,U] First =>\n public $a:T\n public $b:U\nend\n'
      'object[U,T] Reordered =>\n public $b:U\n public $a:T\nend\n'
      'First(1,"a") $.a\nFirst(1,"a") $.b\n'
      'Reordered("a",1) $.a\nReordered("a",1) $.b', [1,'a',1,'a']),
    C('TB-010',
      'trait[T] Marker => end\n'
      'object Dual => end\n'
      'object Dual as Marker[Int] => end\n'
      'object Dual as Marker[String] => end\n'
      'define consume(x:Marker[Int])->String=>"int" end\n'
      'define text(x:Marker[String])->String=>"string" end\n'
      'Dual as[Marker[Int]] consume\nDual as[Marker[String]] text', ['int','string']),
    C('TB-015',
      'trait[T] Source => end\n'
      'trait[U] Renamed => end\n'
      'trait[T] Source as Renamed[T] => end\n'
      'object Item => end\n'
      'object Item as Source[Int] => end\n'
      'define read(x:Renamed[Int])->String=>"ok" end\n'
      'Item as[Renamed[Int]] read', ['ok']),
    C('RK-016',
      'define[T] plus(a:Int,b:Int,marker:T novec)->Int=>$a + $b end\n'
      '[1,2,3] [4] 1 plus extend(0)', [[5,2,3]]),
    C('RK-017',
      'define add(a:Int,b:Int)->Int=>$a + $b end\n'
      '[1,2,3] [4] add extend([0])', reject=('extend',)),
    C('RK-019',
      'define[T] duplicate(x:T)->T,T=>$x $x end\n'
      '[1,2] duplicate', [[1,2],[1,2]]),
    C('CS-015',
      'define[T] first(x:T)->T=>$x end\n'
      'define[T,U] second(x:T,y:U)->U=>$y end\n'
      '1 2 3 sequence:(first,second)', [1,3]),
    C('UC-015',
      '$f=fn => * 2 end\n'
      'define forward(f:Function[Int|String -> Int|String],x:Int|String)->Int|String=>$f($x) end\n'
      'forward($f,1)\nforward($f,"x")', [2,'xx']),
    C('TG-016',
      'tag #marked as constructed\n'
      'define[T] keep(x:T)->T=>$x end\n'
      '$f=fn[T](x:T)->T=>keep($x) end\n'
      '(3 #marked) keep\n$f(3 #marked)',
      [TaggedValue(3, frozenset({DataTag('marked')}))]*2),
    C('EC-014',
      'define[T] keep(x:T?)->T?=>$x end\n'
      '(3 as[Int|String]) keep\n(3 as[String|Int]) keep', [3,3]),
])

source_cases(GenericsPlanSourceTests, [
    C('OV-013',
      'overload(Int -> Int)\n'
      'define[T] keep(x:T)->T=>$x end\n'
      'keep(3)\nkeep("x")', [3,'x']),
    C('OV-014',
      'overload(Int, Int -> Int)\n'
      'define[T] keep(x:T)->T=>$x end', reject=('parameter type(s)',)),
    C('TB-011',
      'trait[T] Source => end\n'
      'trait Named => end\n'
      'object[T] Box => $value:T end\n'
      'object[T] Box as Source[T] => end\n'
      'object[T] Box as Named => end\n'
      'define pick(x:Source[Int] & Named)->String=>"both" end\n'
      'pick(Box(1) as[Source[Int] & Named])', ['both']),
    C('TB-012',
      'import {base.[Source, Box], impl.Reader, impl.trait Source as Reader}\n'
      'define read(x:impl.Reader[Int])->String=>"read" end\n'
      'Box(3) as[impl.Reader[Int]] read', ['read'],
      modules={
          'base.vlnc': 'public trait[T] Source => end\n'
                       'public object[T] Box => $value:T end\n'
                       'object[T] Box as Source[T] => end',
          'impl.vlnc': 'import {base.Source}\npublic trait[T] Reader => end\n'
                       'trait[T] Source as Reader[T] => end',
      }),
    C('TB-013',
      'trait[T] Source => end\n'
      'trait[T] Reader => end\n'
      'trait[T: Number] Source as Reader[T] => end\n'
      'object[T] Box => $value:T end\n'
      'object[T] Box as Source[T] => end\n'
      'define consume(x:Reader[String])->String=>"bad" end\n'
      'Box("x") consume', reject=('no overloads',)),
    C('TB-016',
      'trait[T] A => end\ntrait[T] B => end\n'
      'trait[T] A as B[T] => end\ntrait[T] B as A[T] => end\n'
      'object Box=>end\n'
      'define consume(x:A[Int])->String=>"bad" end\n'
      'Box consume', reject=('no overloads',)),
    C('VA-015',
      'import {facade.Option}\n'
      'define unwrap(value: Option[Int]) -> Int =>\n'
      ' $value match =>\n'
      '  as :Some(item) => $item\n'
      '  as :None => 0\n'
      ' end\nend\n'
      'Option.Some(3) unwrap', [3],
      modules={
          'model.vlnc': 'public variant[T] Option =>\n'
                        ' Some => $value:T end\n None => end\nend',
          'facade.vlnc': 'public import {model.Option}',
      }),
    C('RK-020',
      'define inc(x:Int)->Int=>$x + 1 end\n'
      '[[1,2],[3,4]] inc\n'
      '[[1,3],[2,4]] inc', [[[2,3],[4,5]],[[2,4],[3,5]]]),
    C('FV-015',
      'define[T] duplicate(x:T)->T,T=>$x $x end\n'
      '3 4 both: fn(x:Int)->Int,Int=>duplicate($x) end', [3,3,4,4]),
    C('CS-012',
      'define[T,U] pair(x:T,y:U)->T,U=>$x $y end\n'
      'define[T,U] pack(x:T,y:U)->{T,U}=> $x $y @@tupled pair end\n'
      'pack(3,"a")', [(3,'a')]),
    C('CS-013',
      'define[T] apply(f:Function,x:T)->T => $f($x) end\n'
      'apply(fn(x:Int)->Int=>$x end,3)\n'
      'apply(fn(x:String)->String=>$x end,"s")', [3,'s']),
    C('WH-018',
      'define compare(f:Function[Int->Int])->Number\n'
      'where ($isSame = $f.inputs $f.outputs ==) => $isSame end\n'
      'compare(fn(x:Int)->Int=>$x end)', [1]),
    C('UC-016',
      'define describe(x:Int)->String=>"i" end\n'
      'define describe(x:String)->String=>"s" end\n'
      'define manual(x:Int|String)->String=>\n'
      ' $x match =>\n'
      '  as :Int => "i"\n  as :String => "s"\n end\nend\n'
      '[1,"x"] map: describe\n[1,"x"] map: manual', [['i','s'],['i','s']]),
    C('TG-011',
      'variant[T] Maybe =>\n Some => $value:T end\n Empty=>end\nend\n'
      'define unwrap(value:Maybe[Int])->Int=>\n'
      ' $value match =>\n'
      '  as :Some(item) => 1\n'
      '  as :Empty => 0\n'
      ' end\nend\n'
      'Some(3) unwrap\n(Empty[Int]) unwrap', [1,0]),
    C('TG-014',
      'tag #marked as computed\n'
      '#marked: keep => (#marked T) -> #marked T end\n'
      'define[T] keep(x:T)->T=>$x end\n'
      '(3 #marked) keep', [TaggedValue(3, frozenset({DataTag('marked')}))]),
    C('TG-015',
      'tag #marked as computed\n'
      '#marked: keep => (Int,Int) -> Int end\n'
      'define[T] keep(x:T)->T=>$x end', reject=('overlay',)),
    C('RT-016',
      'define[T] protect(x:T) =>\n'
      ' try => $x\n'
      ' handle ValueFault => $x\n'
      ' end\nend\n'
      'protect(3)\nprotect("ok")', [3,'ok']),
    C('EC-011',
      'define[T] wrapper(f:Function[->T], fallback:T)=>\n'
      ' try => $f()\n'
      ' handle ValueFault => $fallback\n'
      ' end\nend\n'
      'wrapper(fn -> Int => panic ValueFault("bad") end,7)', [7]),
    C('EC-012',
      'object Oops=>end\nobject Oops as Err=>end\n'
      'define[T] bad(x:Oops)->Result[T,Oops]=>$x end\n'
      'bad( Oops )', reject=('cannot infer',)),
])

class GenericsRemainingProperties(TraitPlanTestCase):
    """Independent seeded and build-mode invariants for the extended inventory."""

    def test_UN_022(self):
        definitions = (
            'define[T] pick(xs:T+ novec,y:T exact)->T=>$y end\n',
            'define[T] reverse(y:T exact,xs:T+ novec)->T=>$y end\n',
        )
        for order in permutations(definitions):
            self.check_case(C('UN-022', ''.join(order) +
                              'pick([1,2],3)\nreverse(3,[1,2])',
                              [3,3], typ='Int'))

    def test_CP_019(self):
        library = {'lib.vlnc':
                   'public define[T] whole(x:T novec)->T=>$x end'}
        for expression in ('whole([1,2])', 'whole(x=[1,2])'):
            self.check_case(C('CP-019', 'import {lib.whole}\n'+expression,
                              [[1,2]], modules=library))
        self.check_case(C('CP-019',
            '$f=fn[T](x:T novec)->T=>$x end\n$f([1,2])', [[1,2]]))
        self.check_case(C('CP-019',
            'define[T] whole(x:T novec)->T=>$x end\nwhole([1,2])', [[1,2]]))

    def test_OV_020(self):
        defs = ('define choose(x:Int)->String=>"int" end',
                'define choose(x:String)->String=>"string" end')
        for order in permutations(defs):
            for imports in ('import {a.keep}\nimport {b.mark}',
                            'import {b.mark}\nimport {a.keep}'):
                self.check_case(C('OV-020', imports+'\n'+'\n'.join(order)+
                                  '\nchoose(3)\nchoose("s")\nkeep(3)\nmark("m")',
                                  ['int','string',3,'m'], modules={
                                      'a.vlnc':'public define[T] keep(x:T)->T=>$x end',
                                      'b.vlnc':'public define[T] mark(x:T)->T=>$x end',
                                  }))

    def test_WH_020(self):
        for static in ('$a = 2, $b = 3', '$b = 3, $a = 2'):
            self.check_case(C('WH-020',
                'define calculate(x:Int)->Number where ('+static+') => $a + $b end\n'
                'calculate(1)', [5], typ='Number'))

    def test_IM_014(self):
        from valiance.analysis import Analyser
        from valiance.incremental import CompilationCoordinator
        from valiance.modules_system.modules import ModuleLoader
        from valiance.parsing import parse
        from valiance.runtime import compile_program
        with TemporaryDirectory() as directory:
            root = Path(directory)
            library = root/'lib.vlnc'
            library.write_text('public define[T] keep(x:T)->T=>$x end', encoding='utf-8')
            CompilationCoordinator(root).build_module(
                library, library.with_suffix('.vbcm'), module_name='lib')
            library.unlink()
            analyser = Analyser(module_loader=ModuleLoader(), source_file=root/'main.vlnc')
            typed = analyser.analyse(parse('import {lib.keep}\nkeep(3)\nkeep("x")'))
            self.assertEqual(analyser.diagnostics, [])
            for optimize in (False, True):
                compiled = compile_program(typed, optimize=optimize)
                self.assertEqual(run(compiled), [3,'x'])
                self.assertEqual(run(loads(dumps(compiled))), [3,'x'])

    def test_IM_015(self):
        library = {'a.vlnc': 'public define[T] keep(x:T)->T=>$x end',
                   'b.vlnc': 'public define[U] repeat(x:U)->U=>$x end'}
        for order in permutations(('import {a.keep}', 'import {b.repeat}')):
            self.check_case(C('IM-015', '\n'.join(order)+
                '\nkeep(3)\nrepeat("x")', [3,'x'], modules=library))

    def test_IM_016(self):
        GenericsIncrementalPlanTests.check_library_revision(
            self,
            'public define[T] convert(x:T)->T=>$x end',
            'public define[T] convert(x:T)->String=>"changed" end',
            'convert(1)', ('convert',), [1], expected_after=['changed'])

    def test_RT_017(self):
        # check_case checks four concrete runtime configurations, not merely
        # whether the source compiles in each configuration.
        self.check_case(C('RT-017', IDENTITY+
                          'keep(3)\nkeep("s")\nkeep([1,2])',
                          [3,'s',[1,2]]))

    def test_RT_018(self):
        # On CPython, PYTHONMALLOC=debug enables guarded allocations and
        # allocator checks even where a native ASAN build is unavailable.
        import os
        import subprocess
        import sys
        env = os.environ.copy()
        env['PYTHONMALLOC'] = 'debug'
        env['PYTHONPATH'] = (str(Path(__file__).resolve().parents[1]/'src') +
                             os.pathsep + str(Path(__file__).resolve().parent))
        result = subprocess.run(
            [sys.executable, '-X', 'dev', '-m', 'unittest',
             'test_generics_plan.GenericsPlanSourceTests.test_RT_004', '-q'],
            cwd=Path(__file__).resolve().parents[1], env=env,
            capture_output=True, text=True, timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)

    def test_CX_014(self):
        import concurrent.futures
        import os
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[1]
        env = os.environ.copy()
        env['PYTHONPATH'] = str(root/'src')
        script = (
            'import sys\n'
            'from valiance.analysis import Analyser\n'
            'from valiance.parsing import parse\n'
            'from valiance.runtime import compile_program,run\n'
            'n=int(sys.argv[1]); a=Analyser(); '
            'p=a.analyse(parse("define[T] keep(x:T)->T=>$x end\\nkeep("+str(n)+")")); '
            'assert not a.diagnostics,a.diagnostics; '
            'assert run(compile_program(p))==[n]\n'
        )
        def worker(i):
            return subprocess.run([sys.executable, '-c', script, str(i)],
                                  cwd=root, env=env, capture_output=True,
                                  text=True, timeout=20)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for completed in pool.map(worker, range(1,5)):
                self.assertEqual(completed.returncode, 0,
                                 completed.stdout+completed.stderr)

    def test_CX_016(self):
        from valiance.incremental import CompilationCoordinator
        with TemporaryDirectory() as directory:
            root=Path(directory)
            library=root/'lib.vlnc'
            library.write_text('public define[T] keep(x:T)->T=>$x end', encoding='utf-8')
            artifact=library.with_suffix('.vbcm')
            coordinator=CompilationCoordinator(root)
            coordinator.build_module(library, artifact, module_name='lib')
            first=artifact.read_bytes()
            self.assertTrue(first)
            artifact.unlink()
            self.assertFalse(artifact.exists())
            coordinator=CompilationCoordinator(root)
            coordinator.build_module(library, artifact, module_name='lib')
            self.assertTrue(artifact.read_bytes())
            self.check_case(C('CX-016',
                'import {lib.keep}\nkeep(3)', [3],
                modules={'lib.vlnc':library.read_text(encoding='utf-8')}))

    def test_CX_017(self):
        import os
        import subprocess
        import sys
        root=Path(__file__).resolve().parents[1]
        rng=__import__('random').Random(291)
        generated=['define['+'T,'*rng.randint(1,20)+'(x:T)->T=>$x end'
                   for _ in range(16)]
        script='''from valiance.parsing import parse
from valiance.parsing.parser import ParseError,ParseErrors
import sys
for line in sys.stdin:
 try: parse(line)
 except (ParseError,ParseErrors): pass
 else: raise AssertionError("malformed generic unexpectedly parsed: " + line)
'''
        env=os.environ.copy();env['PYTHONPATH']=str(root/'src')
        result=subprocess.run([sys.executable,'-c',script],cwd=root,env=env,
            input='\n'.join(generated)+'\n', text=True, capture_output=True,
            timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_CX_018(self):
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        irrelevant=['object Unused => end', 'define \u005cunused -> Int => 3 end',
                    'object Other => end']
        fault='define[T] bad(x:T)->Number=>$x + 1 end'
        def fails(lines):
            a=Analyser(); a.analyse(parse('\n'.join(lines)))
            return any("no overloads for element '+'" in d for d in a.diagnostics)
        initial=irrelevant+[fault]
        self.assertTrue(fails(initial))
        reduced=list(initial)
        for line in irrelevant:
            candidate=[s for s in reduced if s!=line]
            if fails(candidate): reduced=candidate
        self.assertEqual(reduced,[fault]); self.assertTrue(fails(reduced))

    def test_MT_015(self):
        self.check_case(C('MT-015',
            'define[T] keep(x:T)->T=>$x end\nkeep(3)', [3]))
        self.check_case(C('MT-015',
            'define[T] keep(x:T)->String=>$x end\nkeep(3)',
            reject=('return type',)))

    def test_MT_016(self):
        import random
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        rng=random.Random(60932)
        values=[rng.randint(0,999) for _ in range(12)]
        for index,value in enumerate(values):
            with self.subTest(seed=60932,index=index):
                self.check_case(C('MT-016', IDENTITY+f'keep({value})',
                                  [value],typ='Int'))
        # An independently checked counterexample shrinks by discarding
        # unrelated statements, with a stable seed for reproduction.
        GenericsRemainingProperties.test_CX_018(self)

source_cases(GenericsPlanSourceTests, [
    C('OB-018',
      'object[T] Box =>\n'
      ' $value:T\n'
      ' define read(offset:Int, box:$self)->T=>$box.value end\n'
      'end\n'
      'read(1,Box(3))\nread(2,Box("s"))', [3,'s']),
    C('VA-011',
      'variant[T] Maybe =>\n'
      ' Present => $value:T end\n'
      ' Empty => end\n'
      'end\n'
      'define accept(value:Maybe[Number])->String => "ok" end\n'
      'Present(3) accept', ['ok']),
    C('FV-014',
      'define[T] make(value:T, replacement:T)->Function[->T]=>\n'
      ' $saved=$value\n'
      ' fn -> T => $saved := $replacement | $saved end\n'
      'end\n'
      '$f=make(3,4)\n$f()\n$f()\n'
      '$g=make("old","new")\n$g()\n$g()', [4,4,"new","new"]),
    C('IM-013', 'import {a.A,b.B}\n'
      'define acceptOne(x:A[Int])->Int=>1 end\n'
      'define acceptTwo(x:B[String])->Int=>2 end', modules={
      'a.vlnc': 'import {b.B}\npublic trait[T] A => end',
      'b.vlnc': 'import {a.A}\npublic trait[T] B => end',
    }),
    C('RT-013',
      'define[T] keep(x:T)->T=>$x end\n'
      '10 spawn: fn (x:Int)->Int=>keep($x) end | wait\n'
      '"hi" spawn: fn (x:String)->String=>keep($x) end | wait',
      [10,'hi']),
])

class GenericsRemainingTypeAlgebra(TraitPlanTestCase):
    def test_UC_014(self):
        """Ambiguity in one union branch must reject the entire callable."""
        import valiance.vtypes as T
        from valiance.vtypes import Overload, Overloads, Int, String, Fn, U
        overloaded = Overloads(
            Overload((Int,), (Int,)),
            Overload((Int,), (String,)),
            Overload((String,), (String,)),
        )
        self.assertFalse(T.compatible(overloaded, Fn((U(Int, String),), (U(Int, String),))))

    def test_VA_011_reverse_variance(self):
        # Covariant case payloads permit widening, never the inverse narrowing.
        self.check_case(C('VA-011',
            'variant[T] Maybe =>\n'
            ' Present => $value:T end\n Empty => end\nend\n'
            'define accept(value:Maybe[Int])->String=>"bad" end\n'
            'Present(3.5) accept', reject=('no overloads',)))

    def test_WH_018_type_tuple_comparison(self):
        # Type tuple comparisons are structural, ordered, and arity-sensitive.
        self.check_case(C('WH-018',
            'define different(f:Function[Int,Real->Int])->Number\n'
            'where ($result=$f.inputs $f.outputs !=)=>$result end\n'
            'different(fn(x:Int,y:Real)->Int=>$x end)', [1]))
        self.check_case(C('WH-018',
            'define sameTypes(f:Function[Int,Real->Int,Real])->Number\n'
            'where ($result=$f.inputs $f.outputs ==)=>$result end\n'
            'sameTypes(fn(x:Int,y:Real)->Int,Real=>$x $y end)', [1]))

    def test_RK_015(self):
        """List-of-union and union-of-list must preserve their distinct shapes."""
        import valiance.vtypes as T
        from valiance.parsing.parser import parse_type
        a = parse_type('(Int | String)+')
        b = parse_type('Int+ | String+')
        with self.subTest(oracle='canonical_types'):
            self.assertFalse(T.same(a,b),
                'A union of homogeneous lists is not the same as a list of union items')
            self.assertTrue(T.subtype(b,a))
            self.assertFalse(T.subtype(a,b))
        # A mixed list is legal when each *item* is Int | String, but not
        # when the whole list must be uniformly Int or uniformly String.
        with self.subTest(oracle='mixed_list_accepted'):
            self.check_case(C('RK-015',
                'define take(x:(Int|String)+ novec)->Int=>length $x end\n'
                'take([1,"x"])', [2]))
        with self.subTest(oracle='union_of_uniform_lists_rejected'):
            self.check_case(C('RK-015',
                'define take(x:(Int+|String+) novec)->Int=>length $x end\n'
                'take([1,"x"])', reject=('no overloads',)))
