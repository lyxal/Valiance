# Valiance: full non-terminal core and standard-library test audit

**Environment:** Linux, Python 3.13.5, development-only legacy-libcurl bypass for local HTTP tests. Production libcurl security checks remain unchanged.

## Scope

- Core: 111 inventoried test modules, **110 run**. Explicitly excluded `tests/test_terminal_editor.py` because terminal/Textual tests cannot complete in this environment.
- Standard library: **all 66 modules run**, with no file-level exclusion.
- Run with isolated worker processes, per-batch deadlines, and targeted retries for two batches initially slowed by parallel contention.

## Final results

| Scope | Tests executed | Passed after remediation | Pre-existing skipped | Unresolved failures | Unresolved timeouts |
|---|---:|---:|---:|---:|---:|
| Core language/runtime | 3,901 | 3,863 | 38 | 0 | 0 |
| Standard library | 856 | 856 | 0 | 0 | 0 |
| **Total** | **4,757** | **4,719** | **38** | **0** | **0** |

The 38 skips come from existing test declarations/conditions, not the file-level terminal exclusion: deferred REPL/project creation tests, a Windows-only CRT test, and two not-applicable alias cases. They are **not** counted as passes.

## Remediation and retesting

The core documentation-coverage assertion initially failed, identifying 143 undocumented functions newly introduced in HTTP/runtime internals. I added 143 descriptive docstrings across eight Python files, without changing executable semantics. The single previously failing docstring test now passes.

Two unusually large worker batches exceeded their original 240-second limits while multiple heavy suites competed for four CPU cores. Each timed-out selection was then re-executed separately: **747 core tests** and **128 standard-library tests** passed, with no failures or timeouts. The originally timed-out batch results are not counted as successes; their separate rerun evidence establishes completion.

After the documentation change, a targeted **145-test** suite covering docstring enforcement, reference generation, HTTP, HTTP/2, security, and VM suspension completed successfully. A further **23 test-runner unit tests** passed. These targeted runs overlap the full inventory and are not added to the unique total.

## Reproduce

From the repository root, run:

```bash
python -m tools.full_suite_filtered --suite language-core --workers 3 --timeout 420 --output core-results.json
python -m tools.full_suite_filtered --suite standard-library --workers 3 --timeout 420 --output stdlib-results.json
```

The supplied helper excludes terminal/Textual-named modules and writes batch-level machine-readable results, including timeout/failure diagnostics. `VALIANCE_HTTP_INSECURE_ALLOW_OLD_LIBCURL=1` is set only inside its test subprocess environment, and these results must not be used as production libcurl qualification.

## Qualification limits

These results verify **all non-terminal, non-skipped tests in the repository's core and standard-library unittest inventory** on this Linux environment. Python 3.14 and native Windows/macOS matrices were not performed; previously deferred platform validation remains separate. This is an aggregate result of bounded runs plus isolated reruns, not a claim that the original full parallel invocation finished cleanly on its first attempt.
