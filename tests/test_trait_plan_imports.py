"""Behaviour visibility, provider identity, re-exports and namespace isolation."""

from itertools import permutations

from trait_plan_support import SourceCase as C, TraitPlanTestCase, source_cases


BASE = '''
public trait Origin => end
public trait Target => extend label -> String end
public object Item => $id: Int end
object Item as Origin => end
'''
PROVIDERS = {"base.vlnc": BASE}
for _name in ("plain", "rich"):
    PROVIDERS[_name + '.vlnc'] = (
        'import { base.[Origin, Target] }\ntrait Origin as Target =>\n'
        f' define label -> String => "{_name}"\nend\n'
    )
IMPORT_BASE = 'import { base.[Origin, Target, Item] }\n'
IMPORT_PLAIN = 'import { plain.trait Origin as Target }\n'
IMPORT_BOTH = 'import { plain.trait Origin as Target, rich.trait Origin as Target }\n'


class BehaviourPlanTests(TraitPlanTestCase):
    def test_BL_020(self):
        for providers in permutations(('plain', 'rich')):
            imports = '\n'.join('import { ' + provider + '.trait Origin as Target }' for provider in providers)
            self.check_case(C("BL-020", IMPORT_BASE + imports + '\nItem(1) as[Target]',
                              reject=('ambiguous',), modules=PROVIDERS))

    def test_IM_022(self):
        for providers in permutations(('plain', 'rich')):
            imports = '\n'.join('import { ' + provider + '.trait Origin as Target }' for provider in providers)
            self.check_case(C("IM-022", IMPORT_BASE + imports + '\nItem(1) as[plain.Target] | label',
                              ['plain'], modules=PROVIDERS))


source_cases(BehaviourPlanTests, [
    C("BL-005", 'trait A => end\ntrait B => end\ntrait A as B => end\ntrait B as A => end\n'
      'object Item => $id: Int end\nItem(1) as[B]', reject=('cannot safely cast',)),
    C("BL-008", IMPORT_BASE + IMPORT_BOTH + 'Item(1) as[plain.Target] | label\n'
      'Item(2) as[rich.Target] | label', ['plain', 'rich'], modules=PROVIDERS),
    C("BL-010", 'import { base.[Origin, Target, Box] }\n'
      'import { numeric.trait Origin as Target, textual.trait Origin as Target }\n'
      'Box(7) as[Target[Int]] | first', [7], modules={
          'base.vlnc': 'public trait[T] Origin => extend read -> T end\n'
          'public trait[T] Target => extend first -> T end\n'
          'public object[T] Box => $value: T end\n'
          'object[T] Box as Origin[T] => define read -> T => $self.value end',
          'numeric.vlnc': 'import { base.[Origin, Target] }\n'
          'trait[T: Number] Origin as Target[T] => define first -> T => $self read end',
          'textual.vlnc': 'import { base.[Origin, Target] }\n'
          'trait[T: String] Origin as Target[T] => define first -> T => $self read end',
      }),
    C("BL-013", 'import { base.[Item, Target] }\nItem(1) as[Target]',
      reject=('cannot safely cast',), modules=PROVIDERS),
    C("BL-015", IMPORT_BASE + 'define inside(value: Origin) -> String =>\n'
      ' import { plain.trait Origin as Target }\n $value as[Target] | label\nend\n'
      'Item(1) inside\nItem(2) as[Target]', reject=('cannot safely cast',), modules=PROVIDERS),
    C("BL-016", 'import { facade.[Origin, Target, Item] }\nItem(1) as[facade.Target] | label', ['plain'],
      modules={**PROVIDERS, 'facade.vlnc': 'public import { base.[Origin, Target, Item], '
               'plain.trait Origin as Target }'}),
    C("AN-013", 'import { items.Item }\n'
      'define accept(value: trait => extend hidden -> Int end) -> Int => $value hidden end\n'
      'Item(3) accept', reject=('accept',), modules={
          'items.vlnc': 'public object Item => $n: Int end\n'
          'private define hidden(value: Item) -> Int => $value.n end'}),
    C("IM-009", IMPORT_BASE + IMPORT_PLAIN + 'Item(1) as[Target] | label', ['plain'], modules=PROVIDERS),
    C("IM-011", IMPORT_BASE + IMPORT_BOTH + '''
define inner(value: Origin) -> String => $value as[rich.Target] | label end
Item(1) as[plain.Target] | label
Item(2) inner
Item(3) as[plain.Target] | label
''', ['plain', 'rich', 'plain'], modules=PROVIDERS),
    C("IM-013", 'import { left.[Origin, Target, Item], left.trait Origin as Target, '
      'right.trait Origin as Target }\nItem(1) as[Target] | label', ['plain'], modules={
          **PROVIDERS,
          'left.vlnc': 'public import { base.[Origin, Target, Item], plain.trait Origin as Target }',
          'right.vlnc': 'public import { base.[Origin, Target, Item], plain.trait Origin as Target }',
      }),
    C("IM-014", 'import { facade.[Origin, Target, Item], facade.trait Origin as Target }\n'
      'Item(1) as[Target]', reject=('ambiguous',), modules={
          **PROVIDERS,
          'facade.vlnc': 'public import { base.[Origin, Target, Item], '
          'plain.trait Origin as Target, rich.trait Origin as Target }',
      }),
    C("IM-016", IMPORT_BASE + IMPORT_PLAIN + 'import { unrelated.OtherTarget }\n'
      'Item(1) as[plain.Target] | label', ['plain'], modules={
          **PROVIDERS, 'unrelated.vlnc': 'public trait OtherTarget => extend label -> Int end',
      }),
    C("IM-017", 'import { first.[Item as First, Shared as FirstTrait], '
      'second.[Item as Second, Shared as SecondTrait] }\n'
      'define a(value: FirstTrait) -> String => $value label end\n'
      'define b(value: SecondTrait) -> String => $value label end\n'
      'First(1) a\nSecond(2) b', ['first', 'second'], modules={
          name + '.vlnc': 'public trait Shared => extend label -> String end\n'
          'public object Item => $id: Int end\nobject Item as Shared => '
          f'define label -> String => "{name}" end'
          for name in ('first', 'second')
      }),
    C("IM-018", 'import { first.Item as First, second.Item as Second }\n'
      'First(1) label\nSecond(2) label', ['first', 'second'], modules={
          name + '.vlnc': 'public trait Shared => extend label -> String end\n'
          'public object Item => $id: Int end\nobject Item as Shared => '
          f'define label -> String => "{name}" end'
          for name in ('first', 'second')
      }),
    C("IM-021", 'import { a.First, b.Second }\nobject Item => $id: Int end\n'
      'object Item as First => end\nobject Item as Second => end\n'
      'define accept(value: First & Second) -> Int => 7 end\nItem(3) accept', [7], modules={
          'a.vlnc': 'import { b.Second }\npublic trait First => end',
          'b.vlnc': 'import { a.First }\npublic trait Second => end',
      }),
    C("IM-023", 'import { facade.[Item, Origin, Target] }\n'
      'Item(1) as[facade.Target] | label', ['plain'], modules={
          **PROVIDERS, 'facade.vlnc': 'public import { base.[Item, Origin, Target], '
          'plain.trait Origin as Target }',
      }),
])
