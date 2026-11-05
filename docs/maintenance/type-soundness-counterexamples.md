# Type-system counterexample testing

The [type-system test plan](../Valiance_Type_System_Test_Plan.md) defines
70 risk cases and nine compatibility sentinels. It is an input to
`tests/type_soundness_registry.py`, not a record of current test results.
Keep its case IDs and headings aligned with the registry when editing it.

## Executable coverage

- `tests/test_type_soundness_counterexamples.py` supplies source-level oracles.
- `tests/test_type_soundness_registry.py` checks the plan-to-fixture mapping.
- `tools/type_soundness_plan.py` lists cases and runs selected oracles in
  isolated processes with time limits and optional JSON reports.
- `tests/test_type_soundness_multimodule.py` covers nominal provenance,
  private dependencies, re-exports and source-free module execution.
- `tests/test_type_soundness_incremental_multimodule.py` covers dependency
  freshness and incremental rebuilds.
- `tests/test_type_soundness_incremental_traits_multimodule.py` covers
  trait evidence and provider changes through incremental module chains.

Positive runtime fixtures compare unoptimized and optimized execution, both
directly and after bytecode serialization. Negative fixtures must parse before
being rejected by analysis; a syntax error is not evidence of type safety.
A mapped fixture establishes its explicit assertion, not every dimension of
the prose scenario. Unsupported cases need an explicit disposition rather
than being counted as passing.

## Running the campaign

From the repository root in PowerShell, using the project's Python version:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run python -m unittest tests.test_type_soundness_registry -q
uv run python -m unittest tests.test_type_soundness_counterexamples -q
uv run python -m tools.type_soundness_plan --list
uv run python -m tools.type_soundness_plan --case B01
uv run python -m tools.type_soundness_plan --priority P0 --strict-coverage
uv run python -m tools.type_soundness_plan --report soundness-results.json --jobs 4
```

Use fresh runner output for coverage and outcomes. Historical pass counts and
individual defect reports do not describe the current compiler. Follow the
repository's agent instructions to skip local Windows FFI tests and leave
native validation to CI.

For implementation invariants, consult the [type-system guide](type-system.md),
[incremental build inspection](incremental-inspection.md), and
[agent notes](../AGENT_NOTES.md). Cross-feature import coverage belongs to the
[multi-module verification plan](multimodule-interaction-test-plan.md).
