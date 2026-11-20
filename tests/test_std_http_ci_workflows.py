"""Guard the separation between quick commit tests and manual release testing.

These assertions deliberately use the standard library instead of a YAML
parser, because workflow validation must not require an additional runtime
package. They inspect the bounded job blocks, not just comments.
"""
from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / '.github' / 'workflows'


def _workflow(name: str) -> tuple[str, dict[str, str]]:
    source = (WORKFLOWS / name).read_text(encoding='utf-8')
    jobs = source.split('\njobs:\n', 1)
    if len(jobs) != 2:
        raise AssertionError(f'{name} has no jobs section')
    blocks = re.split(r'(?m)^  ([a-z][\w-]*):\s*$', jobs[1])
    return jobs[0], {blocks[i]: blocks[i + 1] for i in range(1, len(blocks), 2)}


class StdHttpCIWorkflowTests(unittest.TestCase):
    def test_12_hour_endurance_is_manual_only(self):
        triggers, jobs = _workflow('std-http-overnight.yml')
        self.assertRegex(triggers, r'(?m)^  workflow_dispatch:\s*$')
        for event in ('push', 'pull_request', 'schedule'):
            self.assertNotRegex(triggers, rf'(?m)^  {event}:')
        self.assertIn('twelve-hour-soak', jobs)
        self.assertIn('--duration-seconds 43200', jobs['twelve-hour-soak'])

    def test_expensive_native_qualification_requires_manual_dispatch(self):
        triggers, jobs = _workflow('std-http-qualification.yml')
        self.assertIn('push:', triggers)
        self.assertIn('pull_request:', triggers)
        for name in ('linux-native-and-security-validation',
                     'linux-instrumented-libcurl-bindings'):
            self.assertIn(name, jobs)
            self.assertRegex(jobs[name], r"(?m)^    if: github.event_name == 'workflow_dispatch'$")
        self.assertNotIn('if: github.event_name', jobs['qualification'])
        self.assertNotIn('--duration-seconds 43200', jobs['qualification'])

    def test_routine_ci_bypass_is_scoped_to_regressions_not_release(self):
        _, jobs = _workflow('std-http-qualification.yml')
        self.assertIn("VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL: '1'", jobs['qualification'])
        for name in ('linux-native-and-security-validation',
                     'linux-instrumented-libcurl-bindings'):
            self.assertNotIn("VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL: '1'", jobs[name])
        _, regular = _workflow('tests.yml')
        self.assertIn("VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL: '1'", regular['tests'])
        for name, contents in regular.items():
            if name != 'tests':
                self.assertNotIn("VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL: '1'", contents)

    def test_commit_job_uses_bounded_suites_not_long_soaks(self):
        _, jobs = _workflow('std-http-qualification.yml')
        smoke = jobs['qualification']
        self.assertIn('--timeout-seconds 180', smoke)
        self.assertIn('--suite tests.test_std_http_dependency_policy', smoke)
        self.assertIn("if: runner.os == 'Linux'", smoke)
        self.assertIn('--suite tests.test_std_http', smoke)
        for tool in ('tools/soak_std_http.py', 'tools/fuzz_std_http_protocol.py',
                     'tools/build_std_http_libcurl.py'):
            self.assertNotIn(f'python {tool}', smoke)


if __name__ == '__main__':
    unittest.main()
