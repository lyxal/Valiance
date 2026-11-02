"""Cross-language type-soundness counterexamples translated into Valiance.

Source: docs/Valiance_Type_System_Test_Plan.md and the user-supplied
Counterexamples in Type Systems chapters. An accepted negative is a failure;
an unrelated parse error never counts as a passing negative. Positive fixtures
run unoptimized/optimized and bytecode-reloaded through trait_plan_support.

Only cases with a verified Valiance oracle are registered here. The accompanying
catalog lists every case, including unimplemented and design-dependent cases.
"""

from trait_plan_support import SourceCase as C, TraitPlanTestCase, source_cases
from valiance.runtime.runtime_values import ObjectValue


BOX = 'object[T] Box => public $value:T end\n'
IDENTITY = 'define[T] identity(value:T novec)->T=>$value end\n'


CASES = [
    # R01 — variance: a public write cannot become a covariant generic hole.
    C('V01', 'trait Wide=>extend size->Int end\n'
      'object Narrow=>$id:Int end\n'
      'object Narrow as Wide=>define size=>$self.id end\n'
      'define widen(values:Wide+ novec)->Wide+=>$values end\n'
      'widen([Narrow(1),Narrow(2)])', typ='Wide+'),
    C('V02', 'object[T] Cell => public $value:T end\n'
      '$small:Cell[Int]=Cell(1)\n$wide:Cell[Number]=$small\n$wide.value=2.5',
      reject=('Cell',)),
    C('V03', 'object[T] ReadBox =>\n $value:T\n'
      'public define read -> T => $self.value end\nend\n'
      'define readWide(box:ReadBox[Number])->Number=>$box read end\n'
      'ReadBox(4) readWide', [4]),
    # R02 — one type solution per universal variable, not one per use.
    C('U01', IDENTITY + 'identity(42)\nidentity("x")\nidentity(5)',
      [42, 'x', 5]),
    C('U02', 'define[T] left(a:T novec,b:T novec)->T=>$a end\n'
      '$bad:String=left(10,"x")', reject=('no overloads',)),
    C('U04', 'define[T] rankFour(values:T+4 exact)->T+4=>$values end\n'
      'rankFour([1,2,3])', reject=('no overloads',)),
    C('U06', 'define[T] whole(xs:T novec)->T=>$xs end\n'
      'whole([1,2,3])\nwhole("x")\nwhole([4,5])',
      [[1,2,3], 'x', [4,5]]),
    # R03 — checked refinements cannot invent runtime witnesses.
    C('RT02', BOX + '$x:Box[Int]|Box[String]=Box(3)\n'
      '$x as?[Box[String]]', [ObjectValue('None', {})], typ='Box[String]?'),
    C('RT03', '$x: Int+ | Int+2 = [1,2,3]\n$x as?[Int+2]', typ='Int+2?'),
    C('RT05', '$x:Int??=Some(\\None)\n$x', typ='(Int?)?'),
    C('RT06', 'define describe(x:Number)->String=>"numeric" end\n'
      'define describe(x:String)->String=>"text" end\n'
      '[1,"x",2] map: describe', [['numeric','text','numeric']]),
    # R04 — static overload ordering, arity and union coverage.
    C('O01', 'define f(x:Number)->String=>"number" end\n'
      'define f(x:Number|String)->String=>"union" end\n'
      'f(3)\nf("text")', ['number','union']),
    C('O04', '[1,2,"A","B"] map: * 2', [[2,4,'AA','BB']]),
    C('O08', 'define o(x:Int)->Int=>1 end\n'
      'define o(x:Int,y:Int)->Int=>2 end', reject=('must all take 1 input',)),
    # R05 — type constructor identity and contradictory tags.
    C('D01', 'object First => $x:Int end\nobject Second => $x:Int end\n'
      'define choose(x:First)->String=>"first" end\n'
      'define choose(x:Second)->String=>"second" end\n'
      'First(1) choose\nSecond(2) choose', ['first','second']),
    C('D04', 'tag #A as computed\ntag #B as computed\n'
      'tag #A disjoint #B\ndefine bad(x:#A #B Int)->Int=>1 end',
      reject=('cannot both apply',)),
    # R06 — local anonymous variables must not contaminate separate calls.
    C('S01', '$id=fn(x)=>$x end\n$id(7)\n$id("hi")\n$id(9)',
      [7,'hi',9]),
    C('S05', 'import {lib.id}\nid(1)\nid("a")',
      [1,'a'], modules={'lib.vlnc':
                         'public define[T] id(x:T)->T=>$x end'}),
    # R07 — precise nominal/ranked runtime evidence.
    C('P05', 'if true => [[1,2]] else => [1,2] end as?[Int+2]',
      [ObjectValue('Some', {'value': [[1,2]]})], typ='Int+2?'),
    # R08 — declared constraints, never inferred hidden requirements.
    C('B01', 'trait Sized=>extend size->Int end\n'
      'object Item=>$value:Int end\n'
      'object Item as Sized=>define size=>$self.value end\n'
      'define[T:Sized] readSize(x:T)->Int=>$x size end\n'
      'Item(3) readSize', [3]),
    C('B04', 'define choose(xs:Int+$n)->Int where ($n 2 == ?)=>2 end\n'
      'define choose(xs:Int+)->Int=>1 end\n'
      '[1,2] choose\n[[1,2]] choose', [1,2]),
    C('B06', 'define[T] increment(x:T)->Number=>$x+1 end',
      reject=("no overloads for element '+'",)),
    # Cross-cutting channel/type-handle and immutable reconstruction sentinels.
    C('X01', 'object Counter=>public $count:Int=0 end\n'
      '$a=Counter\n$b=$a\n$a.count=5\n$b.count\n$a.count', [0,5]),
    C('X03', '$ints=Channel[Int]\n$wide:Channel[Number]=$ints',
      reject=('Channel',)),
    C('X05', '$task=fn -> Int => 1 end spawn\n'
      '$asText:Task[String]=$task', reject=('Task',)),
    C('X08', 'define label(x:Int)->String=>"integer" end\n'
      'define label(x:String)->String=>"string" end\n'
      '[1,"x",2] map: label', [['integer','string','integer']]),
    C('X09', 'define[T] bad(xs:T+4 exact)->T+4=>$xs end\n'
      '[1] bad', reject=('no overloads',)),
    # Additional independent P0/P1 checks: deep overlap and visibility paths.
    C('RT01', 'object Left => $value:Int end\nobject Right => $value:String end\n'
      '$v:Left|Right=Left(3)\n$v as?[Right]',
      [ObjectValue('None', {})], typ='Right?'),
    C('RT04', 'tag #fresh as computed\n'
      '$v: #fresh Int | Int = 7\n$v as?[#fresh Int]',
      [ObjectValue('None', {})], typ='#fresh Int?'),
    C('O02', 'trait First=>extend size->Int end\n'
      'trait Second=>extend label->String end\n'
      'object Item=>$id:Int end\n'
      'object Item as First=>define size=>$self.id end\n'
      'object Item as Second=>define label=>"x" end\n'
      'define f(x:First)->String=>"first" end\n'
      'define f(x:Second)->String=>"second" end\n'
      'Item(3) f', reject=('ambiguous',)),
    C('O03', 'define[T] choose(x:T)->String=>"general" end\n'
      'define choose(x:Int)->String=>"integer" end\n'
      'choose(3)\nchoose("hi")', ['integer', 'general']),
    C('O06', 'object Thing=>$id:Int end\n'
      'define use(x:Thing)->String=>"fallback" end\n'
      'multi define use(x:Thing)->Int=>3 end\n'
      'Thing(1) use', reject=('return',)),
    C('O07', 'import {shapes.name}\n'
      'define name(x:Int)->String=>"local" end\n'
      'name(3)\nname("s")', ['local', 'imported'],
      modules={'shapes.vlnc':
               'public define name(x:Int|String)->String=>"imported" end'}),
    C('D03', 'variant[T] Either=>\n Some=>$value:T end\n Empty=>end\nend\n'
      'Some(3) match=>\n as :Some(value)=>$value\nend',
      reject=('exhaustive',)),
    C('S03', 'define invoke(f:Function)=>$f call end\n'
      '1 fn(x:Int)->Int=>$x+1 end invoke\n'
      '1 2 fn(x:Int,y:Int)->Int=>$x+$y end invoke', [2,3]),
    C('S04', '$field = fn => $.x end\n'
      '$field(record{x=>3})\n$field(record{y=>2})',
      reject=('x',)),
    C('P01', 'define[T] wrong(value:T|T+) -> T+ =>$value end',
      reject=('return',)),
    C('P02', 'define[T] maybe(x:T|T+) -> T|T+ =>$x end\n'
      'maybe[Int+]([1,2])', [[1,2]]),
    C('B05', 'import {mod.need}\nneed("bad")',
      reject=('need',),
      modules={'mod.vlnc':
               'public define[T:Number] need(x:T)->Number=>$x+1 end'}),
    C('X02', 'object Incomplete =>\n  $a:Int\n'
      '  define Incomplete => $self end\nend',
      reject=('does not initialize field(s): a',)),
    C('X04', '$channel=Channel[Int]\n'
      'define[T] expose(c:Channel[T])->Channel[T]=>$c end\n'
      '$asString:Channel[String]=expose($channel)', reject=('Channel',)),
    C('SELF01', 'trait Resizable=>extend resize(factor:Int)->$self end\n'
      'object Panel=>$size:Int end\n'
      'object Panel as Resizable=>define resize(factor:Int)=>$self end\n'
      'Panel(3) resize(2)',
      typ='Panel'),
    # Soundness Phase 2: previously uncovered P0 counterexample obligations.
    C('V06', 'object[T] Producer =>\n $f: Function[Int -> T]\nend\n$small: Producer[Int] = Producer(fn(v:Int)->Int=>$v end)\n$wide: Producer[Number] = $small'),
    C('V07', 'trait[T] Eater => extend eat(value:T)->Int end\ntrait[T] Derived as Eater[T]=>end\nobject[T] Box => $value:T end\nobject[T] Box as Derived[T] => define eat(value:T)->Int => 1 end end\n$box:Eater[Number]=Box(2)', reject=('cannot assign',)),
    C('U03', 'define[T] hold(a:T+2 novec,b:T+ novec)->T+2=>$a end\nhold([[1]],[[2,3]])', reject=('no overloads',)),
    C('U05', 'define[T] both(xs:T+ novec,item:T exact)->T=>$item end\nboth([1,2],"wrong")', reject=('no overloads',)),
    C('U07', 'define[T] check(xs:{T..., String}, y:T exact)->T=>$y end\ncheck({1,2,"a"}, "wrong")', reject=('no overloads',)),
    C('RT07', 'tag #sorted as computed\ntag #ascending as #sorted\ntag #other as computed\ntag #other disjoint #sorted\ndefine identify(x:Int)->String =>\n  $x match =>\n    as :#ascending Int => "ascending"\n    _ => "other"\n  end\nend\n1 #ascending | identify\n1 #ascending | #other | identify\n1 #other | #ascending | identify', ['ascending', 'other', 'ascending']),
    C('O05', 'define classify(x:Number)->String=>"number" end\ndefine classify(x:Int)->String=>"int" end\ndefine classify(x:String)->String=>"string" end\n$x: Number | String = 1\n[$x, "hello"] map: classify', [['number', 'string']]),
    C('S02', 'define[T] capture(f:Function[T->T], x:T)->Function[T->T] => fn(y:T)->T=>$f($x) end end\n$f=fn(z)=>$z end\n$r:Int=capture($f,3)("str")', reject=('cannot assign String',)),
    C('S06', 'define[T] getRank(xs:T+$n)->Function[ -> Number] where ($m=$n) => fn -> Number => $m end end\n$f = getRank([1,2])\n$g = getRank([[1]])\n$f call\n$g call', [1, 2]),
    C('P03', 'define[T] select(x:T|T~)->T~=>$x end\nselect[Int+]([1,2])', reject=('return type mismatch',)),
    C('P04', 'define[T] pass(x:T|T?)->T|T?=>$x end\npass[Int?](Some(\\None))\npass[Int?](\\None)', [ObjectValue("Some", {"value": ObjectValue("None", {})}, ("None",)), ObjectValue("None", {})], typ='(Int?)?'),
    C('P07', 'define[T] preserve(x:T)->T=>$x end\n$x: Int+ | String+ = [1,2]\n$x as?[Int+]\n$x preserve as?[Int+]\n$x as?[String+]\n$x preserve as?[String+]', [ObjectValue("Some", {"value": [1,2]}), ObjectValue("Some", {"value": [1,2]}), ObjectValue("None", {}), ObjectValue("None", {})], typ='String+?'),

    # Remaining P0 probes: receiver-scope writes, factory exposure and overlap.
    C('V04', 'object[T] Secret =>\n $value:T\n public define replace(other:Secret[T], newValue:T)->Secret[T] =>\n   $other.value = $newValue\n   $other\n end\nend', reject=('cannot assign to read-only parameter',)),
    C('V05', 'object[T] Maker =>\n $value:T\n public define make(new:T)->Maker[T] => Maker($new) end\nend\n$small:Maker[Int]=Maker(2)\n$wide:Maker[Number]=$small\n$made:Maker[Int]=$wide 2.5 make', reject=('cannot assign Maker[Number]',)),
    C('P06', 'define classify(x:Number)->String=>"num" end\ndefine classify(x:Number+ novec)->String=>"list" end\n[1,[2,3]] map: classify', [['num', ['num', 'num']]]),

    # Soundness Phase 2: P1 distinctness, bounds, and Self-relative contracts.
    C('D02', 'import {lib.Box as Alias}\nimport {lib.Box}\n$x:Box[Int]=Alias(2)\n', modules={'lib.vlnc': 'public object[T] Box=>public $value:T end\n'}),
    C('D05', 'define[T] equalToInt(x:T)->Number where ($same=T Int ==) => $same end\nequalToInt(3)\nequalToInt("a")', [1, 0]),
    C('D06', 'variant[T] Choice =>\n Yes => $value:T end\n No => end\nend\nYes(3) match =>\n as :Yes(value) => $value\n as :No => 0\nend\nNo[Int] match =>\n as :Yes(value) => $value\n as :No => 0\nend', [3, 0]),
    C('B02', 'object[T:Number] Bounded => $value:T end\nBounded(3)'),
    C('B03', 'object[T: Number] Bounded => $value:T end\n$small:Bounded[Int]=Bounded(3)\n$wide:Bounded[Number]=$small\n$wrong:Bounded[String]=$wide', reject=('cannot assign',)),
    C('SELF02', 'trait Peer => extend join(other:$self)->Int end\nobject A=>$id:Int end\nobject B=>$id:Int end\nobject A as Peer=>define join(other:A)->Int=>1 end end\nobject B as Peer=>define join(other:B)->Int=>2 end end\nA(1) B(2) join', reject=('no overloads',)),
    C('SELF03', 'trait Resizeable=>extend resize(factor:Int)->$self end\nobject Box=>$size:Int end\nobject Other=>$size:Int end\nobject Box as Resizeable=>define resize(factor:Int)->Box=>Box(9) end end\nobject Other as Resizeable=>define resize(factor:Int)->Box=>Box(4) end end', reject=('does not satisfy required signature',)),
    C('SELF04', 'trait Resizeable=>extend resize(factor:Int)->$self end\nobject Box=>$size:Int end\nobject Box as Resizeable=>@self define resize(factor:Int)=>$self.size := * $factor end\nBox(3) resize(2)\ndefine throughTrait(item:Resizeable)->Resizeable=>2 | $item | resize end\nthroughTrait(Box(3))'),


    # Phase 3: P1 native-boundary and P2 scope / recursive constraints.
    C('X06', 'import {ffi("/tmp/type_soundness_absent_library") as foreign}\n'
      'link foreign.abs(:&int)->&int as nativeAbs\n'
      'define[T] relay(x:T)->&int => FFI.&int(2) nativeAbs end\n'
      'define wrapper(x:Int)->&int => relay($x) end'),
    C('X07', 'import {ffi("/tmp/type_soundness_owned_handle") as native}\n'
      '@nodup("owns unique C pointer")\n'
      'link native.Handle as &Handle =>\n'
      '  $raw:&int\n'
      'end\n'
      'define mutate(h:&Handle)->&Handle =>\n'
      '  $copy=$h\n'
      '  $copy.raw=FFI.&int(3)\n'
      '  $copy\n'
      'end', reject=('no writable field',)),
    C('A01', 'import {lib.make}\n'
      'object Secret=>$value:String end\n'
      '$local:Secret=make(3)', reject=('cannot assign',),
      modules={'lib.vlnc': 'object Secret=>$value:Int end\n'
                            'public define make(n:Int)->Secret=>Secret($n) end\n'}),
    C('A02', 'import {lib.getX}\ngetX(record{y=>2})',
      reject=('x',),
      modules={'lib.vlnc': 'public define[T,U] getX(value:T(.x:U))->U=>$value.x end'}),
    C('A03', 'import {lib.[identity as first,identity as second]}\n'
      'first(3)\nsecond("a")', [3,'a'],
      modules={'lib.vlnc': 'public define[T] identity(x:T)->T=>$x end'}),
    C('A04', 'define[T] identity(x:T)->T=>$x end\n'
      '$outside:@1=identity(3)', reject=('cannot assign',)),
    C('REC01', 'object[T: above Number] MustSuper => $value:T end\n'
      'MustSuper("text")', reject=('no overloads',)),
    C('REC02', 'trait[T] A as B[T]=>end\n'
      'trait[T] B as A[T]=>end\n'
      'object Item=>end\n'
      '$evidence:A[Int]=Item', reject=('cannot assign',)),
    C('REC03', 'import {a.A,b.B}\n'
      'object Item=>end\n'
      '$evidence:A=Item', reject=("module 'a' has no public component 'A'",),
      modules={'a.vlnc': 'import {b.B}\npublic trait A as B=>end',
               'b.vlnc': 'import {a.A}\npublic trait B as A=>end'}),
    C('REC04', '@recursive\ndefine repeat(n:Int)->Int=>\n'
      ' if ($n==0)=>return 0\n'
      ' this($n-1)\nend'),
    C('DR01', 'define[T] take(value:T,f:Function[T->T])->T=>$value end\n'
      '$f=fn (x:Int)->Int=>$x end\n'
      'take($f,$f)', reject=('no overloads',)),
    C('DR02', 'type Self=Self+'),  # No recursive type alias feature: explicit N/A.
    C('DR03', 'type Left=Right\ntype Right=Left'),  # No type-level equations.
    C('DR04', 'variant[T] Option=>\n Some=>$value:T end\n None=>end\nend\n'
      'Some(3) match=>as :Some(v)=>$v end', reject=('non-exhaustive',)),

]


class TypeSoundnessCounterexampleTests(TraitPlanTestCase):
    def test_X06(self):
        """No source wrapper, including a generic one, can discharge Unsafe."""
        from valiance.analysis import Analyser
        from valiance.parsing import parse
        from valiance.vtypes.symbols import Symbol
        from valiance.vtypes import ElementTag
        case = next(c for c in CASES if c.id == 'X06')
        analyser = Analyser()
        analyser.analyse(parse(case.source))
        self.assertEqual(analyser.diagnostics, [])
        unsafe = ElementTag(Symbol('Unsafe'))
        for name in ('nativeAbs', 'relay', 'wrapper'):
            overloads = analyser.env.overloads_for(Symbol(name))
            self.assertTrue(overloads, name)
            self.assertTrue(all(unsafe in o.element_tags for o in overloads), name)

    def test_X07(self):
        case = next(c for c in CASES if c.id == 'X07')
        self.check_case(case)

    def test_DR02(self):
        """The source language currently has no recursive type-alias declaration."""
        from valiance.parsing import parse
        from valiance.parsing.parser import ParseError, ParseErrors
        with self.assertRaises((ParseError, ParseErrors)):
            parse('type Self=Self+')
        self.skipTest('N/A: no type-alias facility, hence no recursive alias equation')

    def test_DR03(self):
        """No type equation facility to split between import units."""
        from valiance.parsing import parse
        from valiance.parsing.parser import ParseError, ParseErrors
        with self.assertRaises((ParseError, ParseErrors)):
            parse('type Left=Right')
        self.skipTest('N/A: no type-equation aliases across modules')

    def test_B02_rejects_bad_object_instantiation(self):
        """Object-level T:Number is real evidence, not just parser decoration."""
        self.check_case(C('B02-invalid',
            'object[T:Number] Bounded=>$value:T end\nBounded("text")',
            reject=('no overloads',)))

    def test_D05_refuses_unproved_inequality(self):
        """A where predicate must be rechecked after generic substitution."""
        self.check_case(C('D05-mismatch',
            'define[T] different(x:T)->String '
            'where (T Int != ?) => "different" end\ndifferent(3)',
            reject=('no overloads',)))
        self.check_case(C('D05-different',
            'define[T] different(x:T)->String '
            'where (T Int != ?) => "different" end\ndifferent("x")',
            ['different']))

    def test_B03_bound_respecting_covariance(self):
        self.check_case(C('B03-valid',
            'object[T:Number] Bounded=>$value:T end\n'
            '$small:Bounded[Int]=Bounded(3)\n$wide:Bounded[Number]=$small',
            typ='Bounded[Number]'))

    def test_SELF04_matches_direct_and_trait_views(self):
        from valiance.runtime.runtime_values import ObjectValue
        self.check_case(C('SELF04-direct-v-trait',
            'trait Resizeable=>extend resize(factor:Int)->$self end\n'
            'object Box=>$size:Int end\n'
            'object Box as Resizeable=>'
            '@self define resize(factor:Int)=>$self.size := * $factor end\n'
            'Box(3) resize(2)\n'
            'define throughTrait(item:Resizeable)->Resizeable '
            '=>2 | $item | resize end\nthroughTrait(Box(3))',
            [ObjectValue('Box',{'size':6}),ObjectValue('Box',{'size':6})]))

    def test_V06_input_polarity(self):
        """A T-consuming callable cannot make the owner covariant."""
        self.check_case(C('V06-input',
            'object[T] Sink =>\n $f:Function[T -> Int]\nend\n'
            '$small:Sink[Int]=Sink(fn(v:Int)->Int=>$v end)\n'
            '$wide:Sink[Number]=$small', reject=('cannot assign',)))

    def test_V07_same_instantiation(self):
        """Inherited trait evidence remains available for its original T."""
        self.check_case(C('V07-same',
            'trait[T] Eater=>extend eat(value:T)->Int end\n'
            'trait[T] Derived as Eater[T]=>end\n'
            'object[T] Box=>$value:T end\n'
            'object[T] Box as Derived[T]=>define eat(value:T)->Int=>1 end end\n'
            '$box:Eater[Int]=Box(2)', typ='Eater[Int]'))

    def test_B01_controls(self):
        """Verify trait and bounded-binder syntax independent of the failing proof."""
        prefix = ('trait Sized => extend size -> Int end\n'
                  'object Item => $value:Int end\n'
                  'object Item as Sized => define size=>$self.value end\n')
        self.check_case(C('B01-control-trait', prefix +
                          'define readSize(x:Sized)->Int=>$x size end\n'
                          'Item(3) readSize', [3]))
        self.check_case(C('B01-control-bound', prefix +
                          'define[T:Sized] readSize(x:T)->Int=>3 end\n'
                          'Item(3) readSize', [3]))


source_cases(TypeSoundnessCounterexampleTests, [case for case in CASES if case.id not in ('X06','X07','DR02','DR03')])
