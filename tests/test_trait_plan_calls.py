"""Numbered trait overload, algebraic type, cast and vector-call fixtures."""

from trait_plan_support import SIZED, SOURCE, SourceCase as C, TraitPlanTestCase, source_cases


class OverloadPlanTests(TraitPlanTestCase):
    pass


OVERLOADS = SIZED + '''
define choose(value: Sized) -> String => "trait" end
define choose(value: Item) -> String => "concrete" end
'''

TWO_TRAITS = SIZED + '''
trait Tagged => end
object Item as Tagged => end
'''

source_cases(OverloadPlanTests, [
    C("OV-001", OVERLOADS + 'Item(3) choose', ["concrete"]),
    C("OV-002", SIZED + 'define[T] choose(value: T) -> String => "generic" end\n'
      'define choose(value: Sized) -> String => "trait" end\nItem(3) choose', ["generic"]),
    C("OV-005", TWO_TRAITS + 'define choose(value: Sized) -> String => "trait" end\n'
      'define choose(value: Sized & Tagged) -> String => "both" end\nItem(3) choose', ["both"]),
    C("OV-006", SIZED + 'define choose(value: Sized | String) -> String => "union" end\n'
      'define choose(value: Sized) -> String => "trait" end\nItem(3) choose', ["trait"]),
    C("OV-008", TWO_TRAITS + 'define choose(value: Sized) -> String => "size" end\n'
      'define choose(value: Tagged) -> String => "tag" end\nItem(3) choose', reject=("ambiguous",)),
    C("OV-009", TWO_TRAITS + 'define choose(value: Sized) -> String => "size" end\n'
      'define choose(value: Tagged) -> String => "tag" end\nItem(3) choose{Sized}', ["size"]),
    C("OV-010", OVERLOADS + 'Other(3) choose{Item}', reject=("choose",)),
    C("OV-011", SIZED + 'define choose(a: Item, b: Sized) -> String => "left" end\n'
      'define choose(a: Sized, b: Item) -> String => "right" end\n'
      'choose(Item(1), Item(2))', reject=("ambiguous",)),
    C("OV-012", SIZED + 'define choose(a: Sized, b: Sized) -> String => "broad" end\n'
      'define choose(a: Item, b: Item) -> String => "narrow" end\n'
      'choose(Item(1), Item(2))', ["narrow"]),
    C("OV-014", SIZED + 'define size(value: Item) -> Int => 99 end\nItem(3) size', [99]),
    C("OV-015", SIZED + 'define size(value: Item) -> Int => 99 end\nItem(3) Item::size', [3]),
    C("OV-016", SIZED + 'define scalar(value: Sized novec) -> Int => $value size end\n'
      'Item(3) scalar', [3]),
    C("OV-017", SIZED + 'define scalar(value: Sized novec) -> Int => $value size end\n'
      '$f = fn(value: Sized novec) -> Int => $value scalar end\n[Item(1), Item(2)] $f call', reject=("call",)),
    C("OV-019", SOURCE + 'define[T] unwrap(value: Source[T]) -> T => $value read end\n'
      'Box(7) unwrap[Int]{Source[Int]}', [7]),
])


class TypePlanTests(TraitPlanTestCase):
    pass


source_cases(TypePlanTests, [
    C("TY-001", SIZED + 'Item(4) as[Sized] size', [4]),
    C("TY-002", SIZED + 'object Unrelated => $n: Int end\nUnrelated(4) as[Sized]',
      reject=("cannot safely cast",)),
    C("TY-003", TWO_TRAITS + 'define accept(value: Sized & Tagged) -> Int => $value size end\n'
      'Item(5) accept', [5]),
    C("TY-004", TWO_TRAITS + 'define accept(value: Sized & Tagged) -> Int => $value size end\n'
      'Other(5) accept', reject=("accept",)),
    C("TY-005", SIZED + 'define consume(value: Item | Other) -> Int => $value size end\n'
      'Item(2) consume\nOther(3) consume', [2, 4]),
    C("TY-006", SIZED + 'define consume(value: Item | String) -> Int => $value size end',
      reject=("size",)),
    C("TY-007", SIZED + '$items: Sized+ = [Item(2), Other(3), Item(4)]\nsize $items', [[2, 4, 4]]),
    C("TY-008", SIZED + '$items: Sized++ = [[Item(2)], [Other(3)]]\nsize $items', [[[2], [4]]]),
    C("TY-010", SIZED + 'define maybe(value: Sized?) -> Int =>\n'
      ' $value match =>\n  as :Some(item) => $item size\n  as :None => 0\n end\nend\n'
      'Item(7) maybe', [7]),
    C("TY-011", SIZED + 'define keep(value: Sized?) => $value as?[Item] end\n'
      '\\None keep | match =>\n as :None => 1\n as _ => 0\nend', [1]),
    C("TY-014", SIZED + 'Item(3) ^+ | size', [[3]]),
    C("TY-015", SIZED + 'define measure(value: trait => extend size -> Int end) -> Int => $value size end\n'
      'define consume(value: Item | Other) -> Int => $value measure end\n'
      'Item(2) consume\nOther(3) consume', [2, 4]),
])


class CastPlanTests(TraitPlanTestCase):
    pass


WIDEN = SIZED + 'define widen(value: Sized) -> Sized => $value end\n'

source_cases(CastPlanTests, [
    C("CA-001", SIZED + 'Item(4) as[Sized] size', [4]),
    C("CA-002", SIZED + 'object No => $n: Int end\nNo(4) as[Sized]', reject=("cannot safely cast",)),
    C("CA-003", WIDEN + 'Item(4) widen as?[Item] ? | $.value', [4]),
    C("CA-004", WIDEN + 'Other(4) widen as?[Item] | match =>\n as :None => 1\n as _ => 0\nend', [1]),
    C("CA-005", WIDEN + 'Item(4) widen as![Item] | $.value', [4]),
    C("CA-006", WIDEN + 'Other(4) widen as![Item]', fault="checked cast failed"),
    C("CA-007", WIDEN + 'define classify(value: Sized) -> String =>\n'
      ' $value match =>\n  as :Item => "item"\n  as _ => "other"\n end\nend\n'
      'Item(3) classify\nOther(4) classify', ["item", "other"]),
    C("CA-008", WIDEN + 'define classify(value: Sized) -> String =>\n'
      ' $value match =>\n  as :Item => "item"\n  as :Other => "other"\n end\nend',
      reject=("match",)),
    C("CA-012", WIDEN + 'define inspect(value: Sized) -> Int =>\n'
      ' $value match =>\n  as item: Item => $item.value\n  as _ => -1\n end\nend\n'
      'Item(3) inspect\nOther(4) inspect\nItem(7) inspect', [3, -1, 7]),
    C("CA-013", SIZED + 'Item(3) as[Sized] | size | + 2', [5]),
])


class VectorPlanTests(TraitPlanTestCase):
    pass


VECTOR = SIZED + 'define add(a: Sized, b: Sized) -> Int => ($a size) + ($b size) end\n'

source_cases(VectorPlanTests, [
    C("VS-001", SIZED + '$items: Sized+ = [Item(2), Other(3)]\nsize $items', [[2, 4]]),
    C("VS-002", VECTOR + 'add([Item(1), Other(2)], [Other(3), Item(4)])', [[5, 7]]),
    C("VS-003", VECTOR + 'add([Item(1), Other(2)], [Other(3)])', fault="VectorisationFault"),
    C("VS-004", VECTOR + 'add(Item(10), [Other(2), Item(4)])', [[13, 14]]),
    C("VS-005", SIZED + 'define count(items: Sized+ novec, extra: Int) -> Int => (length $items) + $extra end\n'
      'count([Item(1), Other(2)], [3, 4])', [[5, 6]]),
    C("VS-006", SIZED + 'define scalar(value: Sized novec) -> Int => $value size end\n'
      '[Item(1), Other(2)] scalar', reject=("scalar",)),
    C("VS-007", SIZED + '$items: Sized+ = [Item(2), Other(3)]\n$items map: size', [[2, 4]]),
    C("VS-009", SIZED + '$items: Sized+ = [Item(2), Other(3)]\n'
      '$items 0 fold: fn(total: Int, item: Sized) -> Int => $total + ($item size) end', [6]),
    C("VS-010", SIZED + 'define measure(item: Sized) -> Int => $item size end\n'
      'Item(2) Other(3) both: measure', [2, 4]),
    C("VS-012", VECTOR + 'Item(2) Other(3) add(_, _)', [6]),
    C("VS-014", SIZED + '$item: Sized = Other(6)\n'
      '$f = fn -> Int => $item size end\n$f call\n$f call', [7, 7]),
    C("VS-015", SIZED + 'define twice(item: Sized) -> Int => size | println | size end\nItem(4) twice', [4], stdout="4\n"),
])
