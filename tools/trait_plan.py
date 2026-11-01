"""Run numbered trait hardening oracles in isolated, time-bounded processes.

Examples: python -m tools.trait_plan --smoke
          python -m tools.trait_plan --priority P0 --report .uv-cache/traits.json
          python -m tools.trait_plan --case BL-021 --timeout 60
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
SMOKE = ("TD-002 TD-003 DF-001 DF-002 DF-003 BL-002 BL-005 BL-007 AN-001 AN-002 "
         "GN-001 GN-005 OV-001 OV-008 OV-011 UC-001 UC-004 UC-005 MM-002 MM-003 "
         "MM-004 MM-006 IM-004 IM-005 IM-009 WH-002 WH-010 WH-016 IN-004 IN-006 IN-008").split()


def main(argv=None):
    """Select coverage rows, deduplicate shared tests, and enforce deadlines."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', default=[], help='numbered case; repeat to select several')
    parser.add_argument('--suite', action='append', default=[], help='suite prefix, such as BL or IN')
    parser.add_argument('--priority', choices=('P0', 'P1', 'P2'), action='append', default=[])
    parser.add_argument('--smoke', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--timeout', type=float, default=60)
    parser.add_argument('--report', type=Path, help='write full per-test diagnostics and per-case results as JSON')
    parser.add_argument('--fail-fast', action='store_true')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args(argv)
    if args.timeout <= 0:
        parser.error('--timeout must be positive')
    sys.path[:0] = [str(ROOT / 'tests'), str(ROOT / 'src')]
    from trait_plan_registry import RETIRED, coverage, plan_rows

    rows, registered = plan_rows(), coverage()
    if set(rows) != set(registered) | set(RETIRED):
        parser.error(f'coverage mismatch: missing={sorted(set(rows)-set(registered)-set(RETIRED))}; '
                     f'unknown={sorted(set(registered)-set(rows))}')
    if set(args.case) - set(rows):
        parser.error('unknown cases: ' + ', '.join(sorted(set(args.case) - set(rows))))
    identifiers = [identifier for identifier, row in rows.items()
                   if identifier not in RETIRED
                   and (not args.case or identifier in args.case)
                   and (not args.suite or identifier.split('-')[0] in args.suite)
                   and (not args.priority or row['priority'] in args.priority)
                   and (not args.smoke or identifier in SMOKE)]
    if not identifiers:
        parser.error('selection contains no cases')
    if args.list:
        for identifier in identifiers:
            print(identifier, rows[identifier]['priority'], *registered[identifier])
        return 0
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT / 'tests'), str(ROOT)))
    env['PYTHONIOENCODING'] = 'utf-8'
    results = {}
    for identifier in identifiers:
        for reference in registered[identifier]:
            if reference in results:
                continue
            start = time.monotonic()
            try:
                result = subprocess.run([sys.executable, '-m', 'unittest', reference, '-q'],
                                        cwd=ROOT, env=env, capture_output=True, text=True,
                                        encoding='utf-8', timeout=args.timeout)
                status = 'passed' if result.returncode == 0 else 'failed'
                diagnostics = result.stdout + result.stderr
            except subprocess.TimeoutExpired as error:
                status = 'timed_out'
                diagnostics = f'{reference} exceeded {args.timeout}s\n{error}'
            results[reference] = dict(status=status, seconds=time.monotonic()-start, diagnostics=diagnostics)
            if args.verbose or status != 'passed':
                print(f'{identifier} {status}: {reference}', flush=True)
                if status != 'passed':
                    print(diagnostics if args.verbose else '\n'.join(diagnostics.splitlines()[-24:]), flush=True)
            if args.fail_fast and status != 'passed':
                break
        if args.fail_fast and any(result['status'] != 'passed' for result in results.values()):
            break
    cases = {identifier: {'tests': registered[identifier], 'priority': rows[identifier]['priority'],
                         'status': ('not_run' if any(ref not in results for ref in registered[identifier])
                                    else 'passed' if all(results[ref]['status']=='passed' for ref in registered[identifier])
                                    else 'failed')}
             for identifier in identifiers}
    passed = sum(result['status']=='passed' for result in cases.values())
    print(f'Trait plan: {passed}/{len(cases)} cases passed; {len(results)} unique tests executed.')
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({'cases': cases, 'tests': results, 'retired': RETIRED}, indent=2), encoding='utf-8')
    return int(passed != len(cases))


if __name__ == '__main__':
    raise SystemExit(main())
