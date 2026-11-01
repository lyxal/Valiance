# Trait hardening tests

The [numbered specification](../Valiance_Trait_Comptime_Runtime_Test_Plan.md)
contains 309 rows across 18 suites. The executable registry is
[trait_plan_registry.py](../../tests/trait_plan_registry.py). It connects each
active row to independently loadable unittest methods, reusing existing
semantic tests where they already establish the requested behavior.
[The coverage check](../../tests/test_trait_plan_coverage.py) rejects missing,
duplicate, unknown, or skipped registrations.

There are 306 active rows. LC-003, LC-004, and CC-006 exclusively test managed
shared-state objects and arenas; those features were scrapped and these rows
are retired explicitly. LC-017 tests immutable reconstruction and protocol
preservation. Retirement is recorded separately from test outcomes and does
not turn a failing active row into a success.

## Running the plan

From the repository root:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run python -m tools.trait_plan --smoke
uv run python -m tools.trait_plan --priority P0 --report .uv-cache/traits.json
uv run python -m tools.trait_plan --suite IM
uv run python -m tools.trait_plan --case BL-021 --timeout 60
uv run python -m tools.trait_plan --report .uv-cache/traits.json
```

The runner executes each unique referenced method in a fresh process with a
60-second deadline. Shared references execute once. `--list` displays selected
rows without executing them; repeated `--case`, `--suite`, and `--priority`
options select sets, and the different filters intersect. `--fail-fast` leaves
unexecuted rows marked `not_run`. JSON reports retain diagnostics, elapsed
times, per-case results, and retirement reasons. A timeout is a test failure.

The ordinary unittest discovery command also discovers every new test module.
CI runs isolated P0 oracles on pushes and manual runs, and the full isolated
plan on its daily schedule. Reports are uploaded even when validation fails.

## Oracles and organization

[trait_plan_support.py](../../tests/trait_plan_support.py) checks successful
source fixtures with optimization both disabled and enabled, and executes
both direct and serialized bytecode. Runtime fixtures assert exact stacks,
stdout, or expected faults. Rejection fixtures assert relevant diagnostic
fragments without depending on complete prose.

The `test_trait_plan_*` modules group declarations, calls, static predicates,
dispatch, imports, lifetimes, incremental invalidation, and interactions.
Graph tests use a separate reachability oracle, deterministic seeds, bounded
subprocesses, and explicit cycle/non-seed cases. Incremental tests edit real
module trees and overlays, exercising recovery and dependency invalidation.
Channel tests observe the native `Receive` result and invoke compiled trait
methods on received values instead of assuming an undocumented wrapper API.

The specification's examples are blueprints. Fixtures use current parser
spellings, including `exact`, explicit `fn` callable captures, and call-site
`@@tupled`. Existing documented overload rules take precedence; unresolved
multimethod or behavior-provider ambiguity must be diagnosed rather than
resolved by declaration or import order.

## Invariants exercised by the fixes

- Default methods contribute callable contracts, so sibling defaults dispatch
  through overrides. Blanket implementations do not add requirements to their
  source trait.
- Imported method identities survive renaming and re-export. Provider identity
  belongs to the implementation's origin; facade qualifiers select the
  explicitly re-exported provider set.
- Union branches entering structural abstractions carry separately checked
  witnesses. Runtime dispatch executes those proofs rather than discovering
  methods from live values.
- Match cases own their subjects while bindings borrow them. Interpreted
  vector kernels and repeated implicit parameter calls borrow their inputs;
  returning an access materializes or transfers ownership before frame cleanup.
- Failed and successful module loads both record dependency paths. Changes
  invalidate transitive importers, including recovery from missing modules.

Provider metadata is persisted in analysed interface schema 13. Rebuild older
compiled module interfaces when testing source-free imports after this change.
