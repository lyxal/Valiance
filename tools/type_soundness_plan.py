"""Run Valiance type-system counterexamples without hiding unimplemented cases.

    python -m tools.type_soundness_plan --report soundness-results.json
    python -m tools.type_soundness_plan --priority P0 --strict-coverage
    python -m tools.type_soundness_plan --case B01
    python -m tools.type_soundness_plan --list

Every registered negative must parse successfully and produce an analysis
error, not a generic parser failure. Positive fixtures run both optimization
modes and after bytecode serialization via the shared SourceCase harness.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def run_case(entry, timeout):
    import_type = entry.test_name
    if import_type is None:
        return {'status': 'uncovered', 'duration_seconds': 0}
    env = os.environ.copy()
    env['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT / 'tests'),
                                        env.get('PYTHONPATH', '')))
    started = time.monotonic()
    try:
        result = subprocess.run(
            [sys.executable, '-m', 'unittest', '-q', import_type],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        return {'status': 'timed_out', 'duration_seconds': round(time.monotonic()-started, 3),
                'stderr': str(error)}
    return {'status': ('not_applicable' if result.returncode == 0 and 'skipped=1' in result.stderr else
                       'passed' if result.returncode == 0 else 'failed'),
            'duration_seconds': round(time.monotonic()-started, 3),
            'exit_code': result.returncode,
            'stderr': result.stderr[-12000:] if result.returncode else '',
            'stdout': result.stdout[-4000:] if result.returncode else ''}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', action='append', default=[])
    parser.add_argument('--priority', choices=('P0', 'P1', 'P2'), action='append', default=[])
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--strict-coverage', action='store_true')
    parser.add_argument('--report', type=Path)
    parser.add_argument('--jobs', type=int, default=4)
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args(argv)
    sys.path.insert(0, str(ROOT / 'tests'))
    from type_soundness_registry import PLAN, registry
    from test_type_soundness_counterexamples import CASES
    specs = registry()
    cases = {case.id: case for case in CASES}
    if args.case:
        unknown = set(args.case) - {spec.id for spec in specs}
        if unknown:
            parser.error('unknown test IDs: ' + ', '.join(sorted(unknown)))
    selected = [spec for spec in specs if
                (not args.case or spec.id in args.case) and
                (not args.priority or spec.priority in args.priority)]
    if args.list:
        for spec in selected:
            print(f'{spec.id:7} {spec.priority} {"mapped" if spec.test_name else "uncovered":10} '
                  f'{spec.pack:3} {spec.title}')
        return 0
    results = {}
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(run_case, spec, args.timeout): spec for spec in selected}
        for future in as_completed(futures):
            spec = futures[future]
            results[spec.id] = future.result()
    rows = []
    for spec in specs:
        test = cases.get(spec.id)
        result = results.get(spec.id, {'status': 'not_selected'})
        rows.append({
            'id': spec.id, 'risk_pack': spec.pack, 'priority': spec.priority,
            'title': spec.title, 'scenario': spec.scenario, 'expected': spec.expected,
            'test_name': spec.test_name,
            'fixture_sha256': hashlib.sha256(test.source.encode()).hexdigest() if test else None,
            'oracle_kind': ('compile_reject' if test.reject else
                            'runtime_fault' if test.fault else
                            'compile_and_run' if test.stack is not None else
                            'compile_only') if test else None,
            **result,
        })
    counts = {status: sum(row['status'] == status for row in rows)
              for status in ('passed', 'failed', 'not_applicable', 'uncovered', 'timed_out', 'not_selected')}
    payload = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'plan_sha256': hashlib.sha256(PLAN.read_bytes()).hexdigest(),
        'baseline': 'Valiance_type_soundness_phase4.zip (A01, D02, B01 compiler repairs)',
        'python': sys.version.split()[0], 'platform': platform.platform(),
        'execution_modes': ['unoptimized', 'optimized', 'bytecode-reloaded'],
        'test_selection': {'ids': args.case, 'priorities': args.priority},
        'not_applicable_reasons': {
          'DR02': 'Recursive type aliases/equations are not part of the implemented grammar',
          'DR03': 'Module-split recursive type equations require unsupported type aliases',
        },
        'counts': counts, 'cases': rows,
    }
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(payload, indent=2) + '\n', encoding='utf-8')
    print('Type soundness:', ', '.join(f'{key}={value}' for key, value in counts.items()))
    for row in rows:
        if row['status'] in ('failed', 'timed_out'):
            print(f"  {row['id']} ({row['priority']}): {row['status']}")
            error = (row.get('stderr') or row.get('stdout') or '').splitlines()
            if error:
                print('    ' + error[0][:300])
    return int(counts['failed'] > 0 or counts['timed_out'] > 0 or
               args.strict_coverage and counts['uncovered'] > 0)

if __name__ == '__main__':
    raise SystemExit(main())
