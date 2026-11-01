"""Bounded evidence-graph properties with independent reachability oracles."""

import json
from pathlib import Path
import random
import subprocess
import sys

from trait_plan_support import SourceCase as C, TraitPlanTestCase, source_cases


WORKER = r'''
import json, sys
data = json.load(sys.stdin)
sys.path[:0] = [data['root'] + '/src', data['root'] + '/tests']
import valiance.vtypes as T
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.vtypes.symbols import Symbol
a = Analyser()
a.analyse(parse(data['source']))
assert not a.diagnostics, a.diagnostics
for source, target, expected in data['queries']:
    from valiance.parsing.parser import parse_type
    actual = T.assignable(parse_type(source), parse_type(target), a.env.context)
    assert actual == expected, (data.get('seed'), source, target, actual, expected)
'''


class GraphPlanTests(TraitPlanTestCase):
    def check_graph(self, identifier, edges, size, *, seed=0, prefix="Trait"):
        reachable = {0}
        while True:
            expanded = reachable | {target for source, target in edges if source in reachable}
            if expanded == reachable:
                break
            reachable = expanded
        source = '\n'.join(f'trait[T] {prefix}{i} => end' for i in range(size))
        source += '\n' + '\n'.join(f'trait[T] {prefix}{a} as {prefix}{b}[T] => end'
                                    for a, b in edges)
        source += f'\nobject[T] Box => $value: T end\nobject[T] Box as {prefix}0[T] => end\n'
        queries = [("Box[Int]", f"{prefix}{i}[Int]", i in reachable) for i in range(size)]
        queries += [("Box[Int]", f"{prefix}{i}[String]", False) for i in range(size)]
        self.check_worker(identifier, source, queries, seed)

    def check_worker(self, identifier, source, queries, seed):
        root = Path(__file__).resolve().parents[1]
        payload = dict(root=str(root), source=source, queries=queries, seed=seed)
        result = subprocess.run([sys.executable, '-c', WORKER], input=json.dumps(payload),
                                capture_output=True, text=True, timeout=20, cwd=root)
        self.assertEqual(result.returncode, 0, f'{identifier} seed={seed}\n{result.stderr}')

    def random_graph(self, identifier, *, cyclic, seeds):
        for seed in seeds:
            rng = random.Random(seed)
            candidates = [(a, b) for a in range(6) for b in range(6)
                          if a != b and (cyclic or a < b)]
            edges = sorted(rng.sample(candidates, 8))
            with self.subTest(seed=seed):
                self.check_graph(identifier, edges, 6, seed=seed)

    def test_BL_017(self):
        self.check_graph('BL-017', [(0, 1), (0, 2), (1, 3), (2, 3)], 4)

    def test_BL_019(self):
        self.check_graph('BL-019', [(0, 5)], 6)
        self.check_graph('BL-019', [(0, 1), (1, 2), (2, 5), (3, 4)], 6)

    def test_BL_021(self):
        self.random_graph('BL-021', cyclic=True, seeds=(7, 19, 37))

    def test_IN_015(self):
        self.random_graph('IN-015', cyclic=False, seeds=(11, 23, 41))

    def test_IN_016(self):
        self.random_graph('IN-016', cyclic=True, seeds=(13, 29, 43))

    def test_IN_017(self):
        for name in ('Trait', 'Renamed', 'UnrelatedSpelling'):
            self.check_graph('IN-017', [(0, 1), (1, 2), (2, 0), (3, 4)], 5, prefix=name)

    def test_IN_020(self):
        source = '\n'.join(f'trait Trait{i} => end' for i in range(101))
        source += '\n' + '\n'.join(
            f'object Item{i} => $id: Int end\nobject Item{i} as Trait{i % 101} => end'
            for i in range(1000)
        )
        queries = []
        for i in range(1000):
            queries.append((f'Item{i}', f'Trait{i % 101}', True))
            queries.append((f'Item{i}', f'Trait{(i + 1) % 101}', False))
        self.check_worker('IN-020', source, queries, 0)


source_cases(GraphPlanTests, [
    C("BL-018", 'import { base.[Origin, Left, Right, Target, Item] }\n'
      'import { plain.trait Left as Target, rich.trait Right as Target }\n'
      'Item(1) as[Target]', reject=('ambiguous',), modules={
          'base.vlnc': 'public trait Origin => end\npublic trait Left => end\n'
          'public trait Right => end\npublic trait Target => end\n'
          'trait Origin as Left => end\ntrait Origin as Right => end\n'
          'public object Item => $id: Int end\nobject Item as Origin => end',
          'plain.vlnc': 'import { base.[Left, Target] }\ntrait Left as Target => end',
          'rich.vlnc': 'import { base.[Right, Target] }\ntrait Right as Target => end',
      }),
])
