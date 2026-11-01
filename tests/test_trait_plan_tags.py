"""Tag validators, overlays, effect bounds and custom trait index providers."""

from trait_plan_support import SIZED, SourceCase as C, TraitPlanTestCase, source_cases


class TagPlanTests(TraitPlanTestCase):
    pass


source_cases(TagPlanTests, [
    C("TG-002", SIZED + '''
tag #marked as computed
define keep(value: Sized) -> Sized => $value end
#marked: keep =>
  (#marked Sized) -> #marked Sized
end
define pick(value: #marked Sized) -> String => "marked" end
Item(3) #marked | keep | pick
''', ['marked']),
    C("TG-003", SIZED + 'tag #marked as computed\n'
      'define bad(value: #marked #-marked Sized) -> Int => 1 end', reject=("marked",)),
    C("TG-004", SIZED + '''
tag #colour as computed
tag #red as #colour
define classify(value: #colour Sized) -> String =>
  $value match =>
    as :#red Sized => "red"
    as _ => "other"
  end
end
Item(3) #red | classify
''', ['red']),
    C("TG-005", SIZED + '''
tag #checked as computed
define #checked(value: Sized) -> #boolean Number => false end
define #checked(value: Item) -> #boolean Number => true end
Item(3) #checked | size
''', [3]),
    C("TG-006", SIZED + '''
trait Marked => end
object Item as Marked => end
tag #checked as computed
define #checked(value: Sized) -> #boolean Number => true end
define #checked(value: Marked) -> #boolean Number => true end
Item(3) #checked
''', reject=("ambiguous",)),
    C("TG-007", SIZED + '''
tag #checked as computed
define #checked(value: Sized) -> #boolean Number => ($value size) > 0 end
Other(-2) #checked
''', fault="validator #checked failed"),
    C("TG-008", SIZED + '''
@index(Sized)
define get(value: Sized, key: Int) -> Int => ($value size) + $key end
$item: Sized = Other(3)
$item[2]
''', [6]),
    C("TG-009", SIZED + '''
@update(Sized)
define put(value: Sized, key: Int, replacement: Int) -> Sized => Item($replacement) end
$item: Sized = Other(3)
$item[0] = 7
$item size
''', [7]),
    C("TG-010", SIZED + '''
@index(Sized)
define invalid(value: Sized) -> Int => 1 end
''', reject=("exactly 2 inputs",)),
    C("TG-012", SIZED + 'Item(3) panic', reject=("panic",)),
])
