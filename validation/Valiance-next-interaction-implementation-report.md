# Valiance cross-module interaction coverage — implementation report

The new source catalog `tools/multimodule_composition_contracts.py` adds **42
connected semantic families** and `tools/multimodule_interactions.py` adds **42
specified negative-program counterparts**. This increases the finite coverage
inventory from **255 to 389 fixture-backed pairs**, leaving **1,441 of 1,830
pairs needing semantic contracts**. Pair counts measure defined fixtures, not
a proof of language correctness.

The expanded enumerated campaign now consists of **83 × 6 × 5 = 2,490 valid
project configurations**, or 9,960 execution-mode checks per seed, plus
**81 × 6 × 5 = 2,430 invalid project configurations**. These are configured,
not claimed fully executed.

## Validation actually performed

- 42 newly defined families passed direct-source checks (168 execution modes).
- The six-graph source sweep initially passed 251/252 projects (1,004 mode
  checks). The failing enum-alias/overload interaction is recorded as an
  expected-failure regression, and the supported function-alias version passed
  all six targeted import graphs (24 checks). The later full rerun timed out.
- All 42 new families passed as **nested compiled-only modules** (168 checks),
  across three isolated subprocess slices.
- Nine compiled alias cases passed (36 checks); 21 diamond/mixed-layout cases
  passed across three artifact layouts (84 checks).
- All 42 new direct-source negative diagnostics passed, as did 42 compiled
  alias negative diagnostics.
- Ten existing multi-module project unit tests passed. Focused new integration,
  replay, and subprocess-timeout tests passed. Both known-failure regressions
  are still expected failures, and are **not** counted as verified features.

## Implementation locations

- `tools/multimodule_composition_contracts.py` — multi-owner programs with
  independently specified expected values and genuine type/value transitions.
- `tools/multimodule_interactions.py` — positive and negative registration,
  replay indices, source/binary graph transformations, and coverage inventory.
- `tools/multimodule_matrix_slices.py` — optional `--case-timeout` per-case
  subprocess isolation, with crashes/timeouts recorded as failures.
- `tests/test_multimodule_interactions_expanded.py` — ordinary discoverable
  positive, compiled, invalid, inventory, and timeout tests.
- `tests/test_multimodule_known_failures.py` — two explicitly tracked xfails.
- `docs/maintenance/multimodule-interaction-test-plan.md` — finite scope,
  prioritization, release gates, and reproduction commands.

## Important limits

Python 3.13.5 was used, but Valiance requires Python 3.14+. The complete
repository suite and the full 2,490/2,430 campaign were not executed.
Native FFI, package graphs, arbitrary ownership/concurrency schedules, and
a number of compiler-phase taxonomy axes remain substantial priorities.

For exact case indices, inventory status, and all numerical evidence, consult
`Valiance-next-interaction-inventory.json` and
`Valiance-next-interaction-validation.json` alongside this package.
