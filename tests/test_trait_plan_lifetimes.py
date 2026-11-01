"""Trait abstraction must preserve ownership, cleanup and task-transfer semantics."""

from itertools import permutations

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import VirtualMachine, compile_program, dumps, loads
from valiance.runtime.concurrency import Receive

from trait_plan_support import SIZED, SOURCE, SourceCase as C, TraitPlanTestCase, source_cases


class LifetimePlanTests(TraitPlanTestCase):
    def test_LC_009(self):
        for operation in ('dup', 'copy(value -> value)', 'move(value -> value, value)'):
            source = SOURCE + ('define[T] duplicate(item: Source[T]) -> T, T =>\n'
                               f' $item {operation} | read | swap | read\nend\nBox(3) duplicate')
            self.check_case(C('LC-009', source, [3, 3]))


MUTABLE = '''
trait Counter =>
  extend increment -> $self
  extend count -> Int
end
object Item => public $n: Int end
object Item as Counter =>
  @self define increment => $self.n := + 1
  define count => $self.n
end
'''

OWNED = '''
trait Marker => end
object Item =>
  $n: Int
  define ~Item => "released ${$self.n}" println
end
object Item as Marker => end
'''

NODUP = '''
trait Marker => end
@nodup("resource is not copyable")
object Resource => $id: Int end
object Resource as Marker => end
'''

MUSTCALL = '''
trait Closable => extend close -> $self end
@mustcall(any = ["close"])
object Resource =>
  $id: Int
  public define close => $self
end
object Resource as Closable => end
'''

source_cases(LifetimePlanTests, [
    C("LC-001", 'trait Secret => extend read -> Int end\n'
      'object Item => private $n = 7 end\n'
      'object Other =>\n private $label = "other"\n private $n = 9\nend\n'
      'object Item as Secret => define read => $self.n end\n'
      'object Other as Secret => define read => $self.n end\n'
      'define reveal(value: Secret) => $value read end\nItem | reveal\nOther | reveal', [7, 9]),
    C("LC-002", MUTABLE + '$a = Item(3)\n$b = $a increment\n$a count\n$b count', [3, 4]),
    C("LC-005", OWNED + 'define dispose(item: Marker) -> => $item pop end\nItem(3) dispose', [],
      stdout="released 3\n"),
    C("LC-006", OWNED + 'define[T: Marker] forward(value: T) -> T => $value end\n'
      'Item(4) | forward | pop', [], stdout="released 4\n"),
    C("LC-007", NODUP + 'Resource(3) dup', reject=("resource is not copyable",)),
    C("LC-008", NODUP + 'define \\make -> Marker => Resource(3) end\n'
      'define duplicate(value: Marker) -> Marker, Marker => $value dup end\n'
      '\\make | duplicate', fault="DuplicationFault"),
    C("LC-010", NODUP + 'define consume(value: Marker) -> Int => 7 end\nResource(3) consume', [7]),
    C("LC-011", SIZED + 'define duplicate(value: Item | Other) -> Int => '
      '$value dup | size | swap | size | + end\nItem(3) duplicate\nOther(4) duplicate', [6, 10]),
    C("LC-012", NODUP + 'object Copyable => $id: Int end\n'
      'define bad(value: Resource | Copyable) => $value dup end', reject=("resource is not copyable",)),
    C("LC-013", 'trait Marker => end\n'
      'object Child =>\n $id: Int\n define ~Child => "child ${$self.id}" println\nend\n'
      'object Parent =>\n private $first: Child\n private $second: Child\nend\n'
      'object Parent as Marker => end\n'
      'define dispose(value: Marker) -> => $value pop end\nParent(Child(1), Child(2)) dispose', [],
      stdout="child 2\nchild 1\n"),
    C("LC-014", 'trait Marker => end\n'
      'object Child => define ~Child => "child released" println end\n'
      'object Parent =>\n private $child: Child\n'
      ' define ~Parent => RuntimeFault("cleanup failed") panic\nend\n'
      'object Parent as Marker => end\n'
      'define dispose(value: Marker) -> => $value pop end\nParent(Child) dispose',
      fault="cleanup failed", stdout="child released\n"),
    C("LC-015", MUSTCALL + 'Resource(3) Resource::close | pop', []),
    C("LC-016", MUSTCALL + 'define \\make -> Closable => Resource(3) end\n'
      'define dispose(value: Closable) -> => $value pop end\n\\make | dispose', fault="MustCallFault"),
    C("LC-017", MUTABLE + '$original = Item(3)\n'
      '$updated = $original increment | increment | increment\n'
      '$original count\n$updated count', [3, 6]),
])


class ConcurrencyPlanTests(TraitPlanTestCase):
    def channel_values(self, source, expected):
        analyser = Analyser()
        typed = analyser.analyse(parse(SIZED + 'define measure(value: Sized) -> Int => $value size end\n' + source))
        self.assertEqual(analyser.diagnostics, [])
        for optimize in (False, True):
            program = compile_program(typed, optimize=optimize)
            for candidate in (program, loads(dumps(program))):
                with VirtualMachine() as vm:
                    received = vm.run(candidate)
                    self.assertEqual(len(received), len(expected))
                    for item, size in zip(received, expected, strict=True):
                        self.assertIsInstance(item, Receive)
                        self.assertFalse(item.closed)
                        self.assertEqual(vm.call_value(vm.globals['measure'], [item.value]), [size])

    def test_CC_003(self):
        self.channel_values('$channel = 1 Channel[Sized]\n$channel Item(7) send\n$channel receive', [7])

    def test_CC_005(self):
        self.channel_values('''
$channel = 2 Channel[Sized]
$channel Item(2) send
$channel Other(3) send
define next(channel: Channel[Sized]) -> Receive[Sized] => $channel receive end
$channel next
$channel next
''', [2, 4])

    def test_CC_010(self):
        for order in permutations(('Item(2)', 'Other(3)', 'Item(5)')):
            calls = '\n'.join(f'$t{i} = fn -> Int => {value} size end | spawn'
                              for i, value in enumerate(order))
            expected = [2 if value == 'Item(2)' else 5 if value == 'Item(5)' else 4 for value in order]
            self.check_case(C("CC-010", SIZED + calls + '\n$t0 wait\n$t1 wait\n$t2 wait', expected))


source_cases(ConcurrencyPlanTests, [
    C("CC-001", SIZED + '$item: Sized = Other(3)\n'
      '$task = fn -> Int => $item size end | spawn\n$task wait', [4]),
    C("CC-002", SOURCE + 'define[T] measure(value: Source[T]) -> T => $value read end\n'
      '$task = fn -> Int => Box(7) measure end | spawn\n$task wait', [7]),
    C("CC-004", SIZED + '$channel = Channel[Item]\n'
      'define accept(value: Channel[Sized]) => $value end\n$channel accept', reject=("accept",)),
    C("CC-007", MUSTCALL + '$resource = Resource(3)\n'
      'spawn: fn -> Resource => $resource Resource::close end', reject=("isolated",)),
    C("CC-008", MUSTCALL + 'Resource(3)\nmove(value -> value)\n'
      'spawn: fn(value: Resource) -> Resource => $value close end | wait | pop', []),
    C("CC-009", OWNED + '$channel = Channel[Int]\n$task = fn -> =>\n'
      ' $resource: Marker = Item(7)\n $channel receive | pop\nend | spawn\n'
      '$task 1 timeout', fault="cancelled", stdout="released 7\n"),
])
