"""Merge completed bounded qualification campaigns with explicit provenance.

This does NOT claim an uninterrupted pass. Duplicate suites use only the last
completed passing result; failed/skipped/missing runs never disappear silently.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def merge(inputs: list[tuple[str, dict]]) -> dict:
    if not inputs:
        raise ValueError('at least one completed campaign is required')
    versions = {(doc.get('platform'), doc.get('python')) for _, doc in inputs}
    if len(versions) != 1:
        raise ValueError('cannot merge results from different runtimes/platforms')
    results = {}
    campaigns = []
    faults = []
    for name, doc in inputs:
        suites = doc.get('suites', [])
        if not suites or doc.get('all_suites_passed') is not True or doc.get('local_gate_passed') is not True:
            faults.append(f'{name}: campaign did not complete successfully')
        campaigns.append({'source': name, 'suite_count': len(suites),
                          'all_suites_passed': doc.get('all_suites_passed'),
                          'local_gate_passed': doc.get('local_gate_passed')})
        for item in suites:
            suite = item.get('suite')
            if (item.get('outcome') != 'passed' or not item.get('tests_run')
                    or item.get('skipped') or item.get('exit_code') != 0):
                faults.append(f'{name}: invalid suite {suite}')
            results[suite] = dict(item, campaign=name)
    first = inputs[0][1]
    return {
        'schema': 2, 'qualification_scope': 'merged completed campaigns on one host; NOT a single uninterrupted run',
        'platform': first['platform'], 'python': first['python'],
        'python_314_or_later': first['python_314_or_later'],
        'unsupported_python_override': first['unsupported_python_override'],
        'unsafe_old_libcurl_override': any(doc.get('unsafe_old_libcurl_override', False) for _, doc in inputs),
        'native_dependency_production_qualified': all(doc.get('native_dependency_production_qualified', False) for _, doc in inputs),
        'single_uninterrupted_campaign': len(inputs) == 1,
        'campaigns': campaigns, 'all_suites_passed': not faults,
        'local_gate_passed': not faults,
        'cross_platform_qualified': False,
        'qualified_suite_count': len(results),
        'completed_tests': sum(item['tests_run'] for item in results.values()),
        'suites': list(results.values()), 'problems': faults,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, action='append', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if len(args.input) < 2:
        parser.error('use at least two reports; single reports need no merging')
    try:
        documents = [(str(path), json.loads(path.read_text(encoding='utf-8')))
                     for path in args.input]
        merged = merge(documents)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(merged, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': merged['local_gate_passed'],
                      'distinct_suites': merged['qualified_suite_count'],
                      'tests': merged['completed_tests'],
                      'campaigns': len(merged['campaigns']),
                      'problems': merged['problems']}, indent=2))
    return 0 if merged['local_gate_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
