"""Declaration, default, inheritance and generic source regressions from the plan."""

from trait_plan_support import NAMED, SIZED, SOURCE, SourceCase as C, TraitPlanTestCase, source_cases


class DeclarationPlanTests(TraitPlanTestCase):
    pass


source_cases(DeclarationPlanTests, [
    C("TD-001", 'trait Marker => end\nobject Item => $id: Int end\n'
      'object Item as Marker => end\nItem(3) as[Marker] pop', []),
    C("TD-002", NAMED + 'Item("name") name', ["name"], typ="String"),
    C("TD-006", 'trait Split => extend split -> Int, Int end\n'
      'object Item => $id: Int end\nobject Item as Split => define split => $self.id end',
      reject=("split", "signature")),
    C("TD-007", 'trait Marker => end\nobject Missing as Marker => end',
      reject=("Missing",)),
    C("TD-008", 'trait Marker => end\nobject Item => $id: Int end\n'
      'object Item as Marker => define Item(id: Int) => $self.id = $id end',
      reject=("constructor",)),
    C("TD-009", 'trait Total => extend total -> Int end\n'
      'object Item =>\n private $secret = 7\n $value: Int\nend\n'
      'object Item as Total => define total => $self.secret + $self.value end\n'
      'Item(5) total', [12]),
    C("TD-010", 'object Item => private $secret = 7 end\n'
      'trait Hidden => extend reveal -> Int end\n'
      'object Item as Hidden => define reveal => $self.secret end\n'
      'define leak(value: Item) => $value.secret end', reject=("secret",)),
    C("TD-012", SIZED + 'trait Complete =>\n extend size -> Int\n extend label -> String\nend\n'
      'object Item as Complete =>\n define size => $self.value\n'
      ' define label => "${$self.value}"\nend\nItem(3) label', ["3"]),
    C("TD-013", 'trait Both =>\n extend first -> Int\n extend second -> String\nend\n'
      'object Item => $id: Int end\nobject Item as Both => define first => $self.id end',
      reject=("second", "Item")),
    C("TD-014", SOURCE + 'Box(7) read\nBox("seven") read', [7, "seven"]),
    C("TD-015", 'trait[T: Number] Invalid => end', reject=("generic",), syntax=True),
    C("TD-016", 'trait Invalid => object Nested => end end', syntax=True),
])


class DefaultPlanTests(TraitPlanTestCase):
    pass


DEFAULTS = '''
trait Named =>
  extend name -> String
  define label -> String => $self name
  define description -> String => $self label
end
object Item => $label: String end
object Item as Named => define name => $self.label end
'''

source_cases(DefaultPlanTests, [
    C("DF-001", DEFAULTS + 'Item("plain") label', ["plain"]),
    C("DF-002", DEFAULTS + '''
object Other => $id: Int end
object Other as Named =>
  define name -> String => "other"
  define label -> String => "override"
end
define describe(item: Named) -> String => $item label end
Other(1) describe
''', ["override"]),
    C("DF-004", DEFAULTS + 'Item("chained") description', ["chained"]),
    C("DF-005", DEFAULTS + '''
object Other => $id: Int end
object Other as Named =>
  define name -> String => "other"
  define label -> String => "override"
end
Other(1) description
''', ["override"]),
    C("DF-006", DEFAULTS + '''
object Other => $id: Int end
object Other as Named =>
  define name -> String => "other"
  define label -> Int => 4
end
''', reject=("label",)),
    C("DF-008", DEFAULTS + '''
object Other => $number: Int end
object Other as Named => define name => "${$self.number}" end
Item("first") description
Other(7) description
''', ["first", "7"]),
    C("DF-009", 'trait Offset =>\n extend value -> Int\n'
      ' define plus(n: Int) -> Int => ($self value) + $n\nend\n'
      'object Item => $n: Int end\nobject Item as Offset => define value => $self.n end\n'
      'Item(7) | plus 5', [12]),
    C("DF-011", 'trait Reader => extend read(:Int) -> Int end\n'
      'object Item => $n: Int end\nobject Item as Reader =>\n'
      ' define read(item: $self, extra: Int) -> Int => $item.n + $extra\nend\n'
      'read(Item(5), 3)', [8]),
    C("DF-012", 'trait Reader => extend read(:Int) -> Int end\n'
      'object Item => $n: Int end\nobject Item as Reader =>\n'
      ' define read(extra: Int, item: $self) -> Int => $item.n + $extra\nend\n'
      'read(3, Item(5))', [8]),
    C("DF-013", 'trait Bad => extend read -> Int end\nobject Item => $n: Int end\n'
      'object Item as Bad => define read(a: $self, b: $self) -> Int => 1 end',
      reject=("only one $self receiver",)),
    C("DF-015", 'trait Wrapped => extend wrap -> $self+ end\n'
      'object Item => $n: Int end\nobject Item as Wrapped => define wrap => [$self] end\n'
      'Item(6) wrap | $.n', [[6]]),
    C("DF-017", DEFAULTS + 'define[T: Named] describe(item: T) -> String => $item description end\n'
      'Item("generic") describe', ["generic"]),
])


class InheritancePlanTests(TraitPlanTestCase):
    pass


INHERIT = '''
trait Parent => extend size -> Int end
trait Child as Parent => extend label -> String end
object Item => $n: Int end
object Item as Child =>
  define size => $self.n
  define label => "child"
end
'''

source_cases(InheritancePlanTests, [
    C("IH-001", INHERIT + 'Item(7) size\nItem(8) label', [7, "child"]),
    C("IH-002", 'trait Parent => extend size -> Int end\ntrait Child as Parent => end\n'
      'object Item => $n: Int end\nobject Item as Parent => define size => $self.n end\n'
      'object Item as Child => end\nItem(9) size', [9]),
    C("IH-003", 'trait Parent => extend size -> Int end\ntrait Child as Parent => end\n'
      'object Item => $n: Int end\nobject Item as Child => end', reject=("size", "Item")),
    C("IH-006", 'trait First => extend size -> Int end\ntrait Second as First => end\n'
      'trait Third as Second => end\nobject Item => $n: Int end\n'
      'object Item as Third => define size => $self.n end\n'
      'define measure(item: First) -> Int => $item size end\nItem(6) measure', [6]),
    C("IH-007", 'import { hierarchy.[Child] }\nobject Item => $n: Int end\n'
      'object Item as Child => define size => $self.n end\nItem(5) size', [5],
      modules={"base.vlnc": 'public trait Parent => extend size -> Int end',
               "hierarchy.vlnc": 'import { base.Parent }\npublic trait Child as Parent => end'}),
    C("IH-008", 'trait[T] Parent => extend read -> T end\ntrait[T] Child as Parent[T] => end\n'
      'object[T] Box => $value: T end\n'
      'object[T] Box as Child[T] => define read -> T => $self.value end\n'
      'Box(4) read\nBox("four") read', [4, "four"]),
    C("IH-009", 'trait Parent => extend size -> Int end\ntrait Child as Parent => end\n'
      'object Item => $n: Int end\nobject Item as Child => define size -> String => "bad" end',
      reject=("size",)),
    C("IH-010", 'trait Root => extend size -> Int end\ntrait Left as Root => end\n'
      'trait Right as Root => end\nobject Item => $n: Int end\n'
      'object Item as Left => define size => $self.n end\n'
      'object Item as Right => end\nItem(3) size', [3]),
    C("IH-011", INHERIT + 'define measure(item: Parent) -> Int => $item size end\nItem(7) measure', [7]),
])


class AnonymousPlanTests(TraitPlanTestCase):
    pass


STRUCTURAL = '''
object Item =>
  $n: Int
  public define size => $self.n
  public define label => "item"
end
'''

source_cases(AnonymousPlanTests, [
    C("AN-001", STRUCTURAL + 'define measure(item: trait => extend size -> Int end) => $item size end\n'
      'Item(7) measure', [7]),
    C("AN-002", 'trait Named => extend missing -> Int end\nobject Item => $n: Int end\n'
      'define measure(item: trait => extend missing -> Int end) => $item missing end\n'
      'Item(7) measure', reject=("measure",)),
    C("AN-003", STRUCTURAL + 'define describe(item: trait =>\n extend size -> Int\n'
      ' extend label -> String\nend) => ($item size) ($item label) end\nItem(3) describe', ["item"]),
    C("AN-004", STRUCTURAL + 'define measure(item: trait =>\n extend size -> Int\n'
      ' extend missing -> String\nend) => $item size end\nItem(7) measure', reject=("measure",)),
    C("AN-007", STRUCTURAL + 'define measure(item: trait => extend size(:Int) -> Int end) => $item end\n'
      'Item(7) measure', reject=("measure",)),
    C("AN-008", STRUCTURAL + 'define measure(item: trait => extend size -> Int end) -> Int => '
      '($item size) * 2 end\nItem(7) measure', [14]),
    C("AN-009", STRUCTURAL + 'trait Sized => extend size -> Int end\nobject Item as Sized => end\n'
      'define nominal(item: Sized) => $item size end\n'
      'define structural(item: trait => extend size -> Int end) => $item size end\n'
      'Item(3) nominal\nItem(4) structural', [3, 4]),
    C("AN-010", 'object Item => $n: Int end\n'
      'object Item as trait => extend size -> Int end => end', reject=("named trait",)),
])


class GenericPlanTests(TraitPlanTestCase):
    pass


source_cases(GenericPlanTests, [
    C("GN-001", SOURCE + 'Box(7) read\nBox("seven") read', [7, "seven"]),
    C("GN-003", SOURCE + 'define accept(item: Source[Int+]) -> Int => ($item read) sum end\n'
      'Box([1, 2]) accept', [3]),
    C("GN-004", SOURCE + 'define[T] inner(item: Source[T]) -> T => $item read end\n'
      'define[T] outer(item: Source[T]) -> T => $item inner end\n'
      'Box(5) outer\nBox("five") outer', [5, "five"]),
    C("GN-006", SOURCE + 'define[T] unwrap(item: Source[T]) -> T => $item read end\n'
      'Box("b") unwrap\nBox(3) unwrap\nBox("a") unwrap', ["b", 3, "a"]),
    C("GN-007", SOURCE + 'object[T] Cell => public $source: Source[T] end\n'
      'define[T] accept(a: Cell[T], b: Cell[T]) => $a end\n'
      'accept(Cell[Int](Box(7)), Cell[String](Box("bad")))', reject=("accept",)),
    C("GN-008", SOURCE + 'object[T] Holder => $source: Source[T] end\n'
      'define unwrap(item: Holder[Int]) -> Int => $item.source read end\n'
      'Holder[Int](Box(9)) unwrap', [9]),
    C("GN-010", 'trait Marker => end\nobject Item => $n: Int end\nobject Item as Marker => end\n'
      'object[T] Mutable => public $value: T end\n'
      'define accept(item: Mutable[Marker]) => $item end\nMutable(Item(1)) accept',
      reject=("accept",)),
    C("GN-015", SIZED + 'object Holder => $item: Sized end\n'
      'define[T] read(value: T(.item: Sized)) -> Int => $value.item size end\n'
      'Holder(Item(8)) read', [8]),
])
