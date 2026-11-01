"""Static selection, explicitly authorized multimethods and callable plans."""

from itertools import permutations

from trait_plan_support import SIZED, SourceCase as C, TraitPlanTestCase, source_cases


MULTI_BASE = SIZED + '''
object Third => $value: Int end
object Third as Sized => define size => $self.value end
define widen(value: Sized) -> Sized => $value end
define choose(a: Sized, b: Sized) -> String => "fallback" end
'''

SPECIALIZATIONS = (
    'multi define choose(a: Item, b: Item) -> String => "ii" end',
    'multi define choose(a: Item, b: Other) -> String => "io" end',
    'multi define choose(a: Other, b: Item) -> String => "oi" end',
    'multi define choose(a: Other, b: Other) -> String => "oo" end',
)
MATRIX = '\n'.join(SPECIALIZATIONS) + '\n'


class MultimethodPlanTests(TraitPlanTestCase):
    def test_MM_018(self):
        for order in permutations(('Item(1)', 'Other(2)', 'Third(3)')):
            calls = '\n'.join(f'choose(widen({item}), widen(Item(9)))' for item in order)
            expected = ['ii' if item.startswith('Item') else 'oi' if item.startswith('Other')
                        else 'fallback' for item in order]
            self.check_case(C("MM-018", MULTI_BASE + MATRIX + calls, expected))

    def test_MM_019(self):
        for definitions in (SPECIALIZATIONS, tuple(reversed(SPECIALIZATIONS))):
            self.check_case(C("MM-019", MULTI_BASE + '\n'.join(definitions) + '''
choose(widen(Item(1)), widen(Item(2)))
choose(widen(Item(1)), widen(Other(2)))
choose(widen(Other(1)), widen(Item(2)))
choose(widen(Other(1)), widen(Other(2)))
choose(widen(Third(1)), widen(Item(2)))
''', ["ii", "io", "oi", "oo", "fallback"]))


source_cases(MultimethodPlanTests, [
    C("MM-001", MULTI_BASE + MATRIX + 'choose(Item(1), Other(2))', ["io"]),
    C("MM-002", MULTI_BASE + MATRIX + 'choose(widen(Item(1)), widen(Other(2)))', ["io"]),
    C("MM-003", MULTI_BASE + 'define choose(a: Item, b: Other) -> String => "io" end\n'
      'choose(widen(Item(1)), widen(Other(2)))', ["fallback"]),
    C("MM-004", MULTI_BASE + MATRIX + '''
choose(widen(Item(1)), widen(Item(2)))
choose(widen(Item(1)), widen(Other(2)))
choose(widen(Other(1)), widen(Item(2)))
choose(widen(Other(1)), widen(Other(2)))
''', ["ii", "io", "oi", "oo"]),
    C("MM-005", MULTI_BASE + MATRIX + 'choose(widen(Third(1)), widen(Other(2)))', ["fallback"]),
    C("MM-006", SIZED + 'multi define choose(value: Item) -> Int => 1 end', reject=("fallback",)),
    C("MM-007", SIZED + 'define choose(value: Other) -> Int => 1 end\n'
      'multi define choose(value: Item) -> Int => 2 end', reject=("fallback",)),
    C("MM-008", SIZED + 'define choose(value: Sized) -> Int => 1 end\n'
      'multi define choose(value: Item) -> String => "wrong" end', reject=("return",)),
    C("MM-009", MULTI_BASE + MATRIX + '''
choose(widen(Item(1)), widen(Other(2)))
choose(widen(Other(1)), widen(Item(2)))
choose(widen(Item(1)), widen(Other(2)))
''', ["io", "oi", "io"]),
    C("MM-010", MULTI_BASE + MATRIX + '$values: Sized+ = [Item(1), Other(2), Third(3)]\n'
      '$values map: fn(value: Sized) -> String => choose($value, widen(Item(4))) end',
      [["ii", "oi", "fallback"]]),
    C("MM-011", MULTI_BASE + MATRIX + 'define[T: Sized] wrapper(a: T, b: Sized) -> String => choose($a, $b) end\n'
      'wrapper(widen(Item(1)), widen(Other(2)))', ["io"]),
    C("MM-012", MULTI_BASE + MATRIX + 'choose(Other(1), Item(2))', ["oi"]),
    C("MM-013", MULTI_BASE + 'trait Marked => end\nobject Item as Marked => end\n'
      'object Other as Marked => end\n'
      'multi define choose(a: Item & Marked, b: Other & Marked) -> String => "marked" end\n'
      'choose(widen(Item(1)), widen(Other(2)))', ["marked"]),
    C("MM-014", SIZED + 'define widen(value: Sized) -> Sized => $value end\n'
      'define select(value: Sized) -> Sized => Other(4) end\n'
      'multi define select(value: Item) -> Sized => Item(7) end\n'
      'Item(1) widen | select | size', [7]),
    C("MM-015", MULTI_BASE + MATRIX + 'define wrapper(a: Item | Other, b: Sized) -> String => choose($a, $b) end\n'
      'wrapper(Item(1), widen(Other(2)))\nwrapper(Other(1), widen(Item(2)))', ["io", "oi"]),
    C("MM-016", MULTI_BASE + MATRIX + 'choose(widen(Third(1)), widen(Third(2)))\n'
      'choose(widen(Item(1)), widen(Item(2)))', ["fallback", "ii"]),
    C("MM-017", MULTI_BASE + MATRIX + 'define choose(value: String, other: String) -> String => "strings" end\n'
      'choose("a", "b")\nchoose(widen(Item(1)), widen(Other(2)))', ["strings", "io"]),
    C("MM-020", MULTI_BASE + 'multi define choose(a: Item, b: Sized) -> String => "left" end\n'
      'multi define choose(a: Sized, b: Item) -> String => "right" end\n'
      'choose(widen(Item(1)), widen(Item(2)))', fault="ambiguous multimethod"),
])


class UnionCallablePlanTests(TraitPlanTestCase):
    pass


CALLABLE = SIZED + '''
define choose(value: Sized) -> String => "trait" end
define choose(value: Item) -> String => "concrete" end
define choose(value: String) -> String => "string" end
define apply(f: Function[Sized | String -> String], value: Sized | String) -> String => $f($value) end
'''

source_cases(UnionCallablePlanTests, [
    C("UC-001", CALLABLE + 'apply(fn => choose end, Item(3))\napply(fn => choose end, "x")', ["trait", "string"]),
    C("UC-006", CALLABLE + 'apply(fn => choose end, Other(3))', ["trait"]),
    C("UC-010", CALLABLE + 'define second(value: Sized) -> String => "second-trait" end\n'
      'define second(value: String) -> String => "second-string" end\n'
      'apply(fn => choose end, Item(3))\napply(fn => second end, Item(4))\napply(fn => choose end, "x")',
      ["trait", "second-trait", "string"]),
    C("UC-011", CALLABLE + 'define capture(f: Function[Sized | String -> String]) -> Function[Sized | String -> String] =>\n'
      ' fn(value: Sized | String) -> String => $f($value) end\nend\n'
      '$f = capture(fn => choose end)\n$f(Item(3))\n$f("x")', ["trait", "string"]),
    C("UC-012", CALLABLE + 'object Holder => $f: Function[Sized | String -> String] end\n'
      '$holder = Holder(fn => choose end)\n$operation = $holder.f\n$operation(Item(3))\n$operation("x")', ["trait", "string"]),
])
