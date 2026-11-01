"""Restricted compile-time evaluation against trait-bearing signatures."""

from itertools import permutations

from trait_plan_support import SIZED, SourceCase as C, TraitPlanTestCase, source_cases


class WherePlanTests(TraitPlanTestCase):
    def test_WH_017(self):
        declarations = (
            'define choose(xs: Sized+$n) -> Number where ($n 2 == ?) => 2 end',
            'define choose(xs: Sized+) -> Int => 1 end',
            'define choose(xs: String) -> Int => 0 end',
        )
        for order in permutations(declarations):
            self.check_case(C("WH-017", SIZED + '\n'.join(order) + '\n[Item(3)] choose', [1]))

    def test_WH_018(self):
        for first, second in (('Item(3)', 'Other(4)'), ('Other(4)', 'Item(3)')):
            self.check_case(C("WH-018", SIZED + '''
define[T: Sized] rank(xs: T+$n) -> Number where ($r = $n + 1) => $r end
''' + f'[{first}] rank\n[[{second}]] rank\n[{first}] rank', [2, 3, 2]))


source_cases(WherePlanTests, [
    C("WH-001", SIZED + 'define rank(xs: Sized+$n) -> Number where ($n 1 == ?) => 7 end\n'
      '[Item(3)] rank', [7]),
    C("WH-002", SIZED + 'define rank(xs: Sized+$n) -> Number where ($n 2 == ?) => 2 end\n'
      'define rank(xs: Sized+) -> Int => 1 end\n[Item(3)] rank', [1]),
    C("WH-003", SIZED + 'define rank(xs: Sized+$n) -> Number where ($n 2 == ?) => 2 end\n'
      '[Item(3)] rank', reject=("rank",)),
    C("WH-004", SIZED + 'define arity(f: Function[Sized -> Int]) -> Number where ($n = $f.arity) => $n end\n'
      'fn(item: Sized) -> Int => $item size end arity', [1]),
    C("WH-005", SIZED + 'define signature(f: Function) -> Number, Number\n'
      'where ($i = length $f.inputs, $o = length $f.outputs) => $i $o\n'
      'fn(item: Sized) -> Int, String => ($item size) "sized" end signature', [1, 2]),
    C("WH-006", SIZED + 'define rank(xs: Sized+$n) -> Number where ($r = $n + 1) => $r end\n'
      '[[Item(1)], [Other(2)]] rank', [3]),
    C("WH-007", SIZED + 'define wrong(xs: Sized+) -> Sized+$m => $xs end\n[Item(1)] wrong',
      reject=("m",)),
    C("WH-008", SIZED + 'define choose(xs: Sized+$n) -> String where ($n 2 == ?) => "matrix" end\n'
      'define choose(xs: Sized+$n) -> String where ($n 1 == ?) => "list" end\n'
      '[Item(1)] choose\n[[Other(2)]] choose', ["list", "matrix"]),
    C("WH-009", SIZED + 'define[T: Sized] same(value: T) -> Number where ($value T == ?, $r = 1) => $r end\n'
      'Item(3) same\nOther(4) same', [1, 1]),
    C("WH-010", SIZED + 'define bad(value: Sized) -> Number where ($value size pop) => 1 end\nItem(3) bad',
      reject=("where",)),
    C("WH-011", SIZED + 'define bad(value: Sized) -> Number where (1 2 bad.max) => 1 end\nItem(3) bad',
      reject=("static", "namespaced")),
    C("WH-012", SIZED + 'define bad(value: Sized) -> Number where (while 1 => 1 end) => 1 end\nItem(3) bad',
      reject=("where",)),
    C("WH-013", SIZED + 'define bad(value: Sized) -> Number where (Result[Sized, String] pop) => 1 end\n'
      'Item(3) bad', reject=("Result",)),
    C("WH-014", SIZED + 'define bad(f: Function[Sized -> Int]) -> Number where ($n = $f?.arity) => $n end\n'
      'fn(item: Sized) -> Int => $item size end bad', reject=("where",)),
    C("WH-015", SIZED + 'define rank(xs: Sized+$n) -> Number\n'
      'where ($r = max($n + 2, 4) * 3 - 1) => $r\n[Item(1)] rank', [11]),
    C("WH-016", SIZED + 'define[T: Sized] choose(xs: T+$n) -> Number where ($n 2 == ?) => 2 end\n'
      'define[T: Sized] choose(xs: T+) -> Int => 1 end\n[Item(3)] choose\n[Other(4)] choose', [1, 1]),
])
