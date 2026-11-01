"""Metamorphic, rank-policy and combinator cases from the trait test plan."""

from itertools import permutations

from trait_plan_support import NAMED, SIZED, SOURCE, SourceCase as C, TraitPlanTestCase, source_cases


class InteractionPlanTests(TraitPlanTestCase):
    def test_TD_017(self):
        for name in ("Named", "Renamed", "Description"):
            self.check_case(C("TD-017", 'import { api.[Item, ' + name + '] }\nItem("value") name', ["value"],
                             modules={"api.vlnc": NAMED.replace('trait Named', 'public trait Named')
                                      .replace('object Item =>', 'public object Item =>').replace('Named', name)}))

    def test_IH_014(self):
        for unrelated in ('trait Unrelated => end\n', 'trait OtherTrait => extend unused -> Int end\n'):
            for before in (False, True):
                source = unrelated + SIZED if before else SIZED + unrelated
                self.check_case(C("IH-014", source + 'Item(3) size', [3]))

    def test_GN_017(self):
        for order in permutations((('Box(1)', 1), ('Box("one")', 'one'), ('Box(1.5)', 1.5))):
            self.check_case(C("GN-017", SOURCE + '\n'.join(value + ' read' for value, _ in order),
                             [expected for _, expected in order]))

    def test_OV_021(self):
        definitions = ('define pick(value: Sized) -> String => "trait" end',
                       'define pick(value: Item) -> String => "item" end',
                       'define pick(value: String) -> String => "string" end')
        for order in permutations(definitions):
            self.check_case(C("OV-021", SIZED + '\n'.join(order) + '\nItem(3) pick', ['item']))

    def test_TY_016(self):
        for left, right in (('Left', 'Right'), ('Right', 'Left')):
            source = f'''
trait Left => end
trait Right => end
object Item => $id: Int end
object Item as Left => end
object Item as Right => end
define accept(value: {left} & {right}) -> Int => 7 end
define union(value: {left} | {right}) -> Int => 9 end
Item(3) accept
Item(3) union
'''
            self.check_case(C("TY-016", source, [7, 9]))

    def test_CA_014(self):
        prelude = SIZED + 'define widen(value: Sized) -> Sized => $value end\n'
        match = 'define pick(value: Sized) -> Int =>\n $value match =>\n'
        match += ' as item: Item => $item.value\n as _ => -1\n end\nend\n'
        cast = 'define pick(value: Sized) -> Int =>\n $value as?[Item] match =>\n'
        cast += ' as :Some(item) => $item.value\n as _ => -1\n end\nend\n'
        for body in (match, cast):
            self.check_case(C("CA-014", prelude + body + 'Item(3) widen | pick\nOther(4) widen | pick', [3, -1]))

    def test_VS_016(self):
        for call in ('Item(3) measure', 'measure(Item(3))'):
            self.check_case(C("VS-016", SIZED + 'define measure(value: Sized) -> Int => $value size end\n' + call, [3]))


source_cases(InteractionPlanTests, [
    C("DF-016", NAMED + 'Item("a") name\nItem("a") Item::name', ['a', 'a']),
    C("IH-013", 'trait Loop as Loop => extend value -> Int end', reject=("cycle",)),
    C("GN-011", SIZED + 'define[T: Sized+] count(value: T novec) -> Int => length $value end\n'
      '[Item(2), Other(3)] count', [2]),
    C("GN-012", SIZED + 'define[T: Sized] count(values: T+ exact) -> Int => length $values end\n'
      '[[Item(2)], [Other(3)]] count', reject=("count",)),
    C("GN-013", SIZED + 'define[T: Sized] keep(value: T exact) -> T => $value end\nItem(3) keep | size', [3]),
    C("OV-003", SIZED + 'define pick(value: Sized) -> String => "trait" end\n'
      'define pick(value: Item?) -> String => "optional" end\nItem(3) pick', ['optional']),
    C("OV-004", SIZED + 'define pick(value: Sized) -> String => "trait" end\n'
      'define pick(value: Item) -> String => "concrete" end\n[Item(3), Item(4)] pick', [['concrete', 'concrete']]),
    C("OV-007", SIZED + 'tag #marked as computed\n'
      'define pick(value: Sized) -> String => "trait" end\n'
      'define pick(value: #marked Item) -> String => "marked" end\nItem(3) #marked | pick', ['marked']),
    C("OV-018", SIZED + 'define[T] pick(value: T+ exact) -> String => "exact" end\n'
      'define pick(value: Sized+) -> String => "trait" end\n[Item(3)] pick', ['exact']),
    C("OV-022", SIZED + 'trait Unrelated => end\nobject Extra => $id: Int end\n'
      'object Extra as Unrelated => end\n'
      'define pick(value: Sized) -> String => "trait" end\n'
      'define pick(value: String) -> String => "string" end\nItem(3) pick', ['trait']),
    C("TY-009", SIZED + 'define accept(values: Sized+ exact) -> Int => length $values end\n'
      '[[Item(1)]] as[Sized*2] | accept', reject=("accept",)),
    C("TY-012", SIZED + '$values: Sized~ = [[Item(2)], Other(3)]\nsize $values', [[[2], 4]]),
    C("TY-013", SIZED + 'define accept(value: Sized++) -> Int => 1 end\n'
      '$values: Sized~ = [[Item(2)], Other(3)]\n$values as[Sized++] | accept', reject=("cannot safely cast",)),
    C("TY-017", SIZED + 'define accept(value: Sized | Sized) -> Int => $value size end\nItem(7) accept', [7]),
    C("CA-011", SIZED + 'define widen(value: Sized) -> Sized => $value end\n'
      'define pick(value: Sized) -> Sized => Other(4) end\n'
      'multi define pick(value: Item) -> Sized => Item(7) end\n'
      'Item(1) widen | pick | match =>\n as item: Item => $item.value\n as _ => -1\nend', [7]),
    C("VS-008", SIZED + 'define merge(a: Sized, b: Sized) -> Sized => Item(($a size) + ($b size)) end\n'
      '$values: Sized+ = [Item(1), Other(2), Item(4)]\n$values reduce: merge | size', [8]),
    C("VS-011", SIZED + 'define first(item: Sized) -> Int => $item size end\n'
      'define second(item: Sized, amount: Int) -> Int => ($item size) + $amount end\n'
      'Item(3) Other(4) 10 sequence: (first, second)', [3, 15]),
    C("VS-013", SIZED + 'trait Split => extend split -> Int, Int end\n'
      'object Item as Split => define split -> Int, Int => $self.value $self.value end\n'
      'define pair(value: Split) -> Int, Int => $value split end\nItem(3) @@tupled pair', [(3, 3)]),
])
