"""Falsifiable inventory of the 70 risk cases and 9 compatibility sentinels.

Do not give an inventory case a passing outcome until a specific executable
oracle is registered and has run. The original plan is the source of truth.
"""

from dataclasses import dataclass
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / 'docs' / 'Valiance_Type_System_Test_Plan.md'
PACK_PRIORITIES = {
    'R01': 'P0', 'R02': 'P0', 'R03': 'P0', 'R04': 'P0',
    'R05': 'P1', 'R06': 'P0', 'R07': 'P0', 'R08': 'P1',
    'C09': 'P1', 'C10': 'P2', 'C11': 'P2', 'C12': 'P2',
}
SENTINEL_PRIORITIES = {
    'X01': 'P1', 'X02': 'P1', 'X03': 'P0', 'X04': 'P0',
    'X05': 'P0', 'X06': 'P1', 'X07': 'P1', 'X08': 'P1', 'X09': 'P1',
}

@dataclass(frozen=True)
class TestPlanEntry:
    id: str
    pack: str
    priority: str
    title: str
    scenario: str
    expected: str
    test_name: str | None


def registry():
    from test_type_soundness_counterexamples import CASES, TypeSoundnessCounterexampleTests

    raw = PLAN.read_text(encoding='utf-8')
    fixtures = {c.id: c for c in CASES}
    if len(fixtures) != len(CASES):
        raise AssertionError('duplicated executable case ID')
    pack = None
    found = []
    lines = raw.splitlines()
    for index, line in enumerate(lines):
        chapter = re.match(r'^### (R\d+|C\d+) — (.+?) \[(P\d);', line)
        if chapter:
            pack = chapter.group(1)
            if chapter.group(3) != PACK_PRIORITIES[pack]:
                raise AssertionError('priority mismatch for ' + pack)
            continue
        test = re.match(r'^#### ((?:V|U|RT|O|D|S|P|B|SELF|A|REC|DR)\d+) — (.+)$', line)
        cross = re.match(r'^\*\*(X\d+) — (.+?)\*\*', line)
        if not (test or cross):
            continue
        case_id = (test or cross).group(1)
        title = (test or cross).group(2)
        owning_pack = pack if test else 'X'
        priority = PACK_PRIORITIES[pack] if test else SENTINEL_PRIORITIES[case_id]
        next_lines = lines[index + 1:]
        scenario = ''
        expected = ''
        if test:
            for tail in next_lines:
                if tail.startswith('### ') or tail.startswith('#### '):
                    break
                match = re.search(r'\*\*Setup:\*\*\s*(.*)', tail)
                if match:
                    scenario = match.group(1).split(' **Stimulus:**')[0]
                match = re.search(r'\*\*Expected oracle:\*\*\s*(.*)', tail)
                if match:
                    expected = match.group(1)
        else:
            scenario = line.split('**', 2)[-1].strip()
            expected = line.split('Oracle:')[-1].strip() if 'Oracle:' in line else ''
        found.append(TestPlanEntry(
            case_id, owning_pack, priority, title, scenario, expected,
            'test_type_soundness_counterexamples.TypeSoundnessCounterexampleTests.test_' + case_id
            if case_id in fixtures else None,
        ))
    ids = [case.id for case in found]
    if len(found) != 79 or len(set(ids)) != 79 or set(fixtures) - set(ids):
        raise AssertionError(f'case inventory mismatch: {len(found)} total, '
                             f'{len(set(ids))} unique, missing: {set(fixtures)-set(ids)}')
    if sum(e.pack == 'X' for e in found) != 9:
        raise AssertionError('expected nine cross-cutting sentinels')
    if {p: sum(e.priority == p for e in found) for p in ('P0','P1','P2')} != \
            {'P0':45,'P1':22,'P2':12}:
        raise AssertionError('priority totals changed')
    for case in fixtures:
        if not hasattr(TypeSoundnessCounterexampleTests, 'test_' + case):
            raise AssertionError('missing unittest method for ' + case)
    return found
