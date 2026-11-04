# Valiance: additional multi-module language-interaction coverage

**Validation environment:** Python 3.13.5 (project requires Python 3.14+).  
**Scope:** bounded, independently specified compiler/VM contracts; not exhaustive language proof.

## Changes

- Added 28 multi-owner semantic families exercising generic/trait/object mutation, copy and borrowed access, stack operations, spawned task execution, and channel payload transport.
- Added 28 designated invalid-program oracles for those families.
- Six contracts use `@nodup` and verify both their repeated-call results and emitted `LOAD_VAR_BORROW` instructions. Two further tests reject explicit duplication and isolated-resource task transfer in both source and compiled-only imports.
- Channel oracles check `Receive.Value` and nominal payload structure (generic objects, traits, variants, enums, scalars), derived from the source seed, including persisted bytecode.
- Added a stdout oracle path to the project runner for lifetime checks; three reproduced imported-destructor issues are **expected failures**, excluded from passing coverage.
- Fixed `--case-timeout` propagation in `tools.multimodule_matrix_slices.py` and added a test for that forwarding.
- Parallelized the scheduled interaction campaign into eight dynamically sized GitHub Actions shards, with one bounded worker per candidate and separately uploaded reports.

## Inventory (defined fixtures, NOT fully verified pairs)

| Item | Previous | Current | Change |
|---|---:|---:|---:|
| Connected positive families | 83 | 111 | +28 |
| Negative semantic contracts | 81 | 109 | +28 |
| Language-axis pairs with a fixture | 389 | 479 | +90 |
| Unimplemented pairs | 1,441 | 1,351 | −90 |
| Scheduled valid configurations | 2,490 | 3,330 | +840 |
| Potential execution-mode checks | 9,960 | 13,320 | +3,360 |
| Scheduled invalid configurations | 2,430 | 3,270 | +840 |

## Actually executed in this environment

| Run | Distinct configurations passed | Checks |
|---|---:|---:|
| All six source import graphs, 28 families | 168 | 672 VM-mode checks |
| Nested compiled-only dependencies, all 28 families | 28 | 112 VM-mode checks |
| Sampled diamond/facade/alias mixed-artifact layouts | 9 | 36 VM-mode checks |
| **Total distinct positive configurations** | **205** | **820 VM-mode checks** |
| All source negative contracts across six graphs | 168 | 168 diagnostics |
| Eight compiled-alias invalid configurations | 8 | 8 diagnostics |
| **Total distinct invalid configurations** | **176** | **176 diagnostics** |

The focused new test module passed 10 tests. The known-failure module reported five expected failures (three imported-destructor cases, plus the two pre-existing compiler failures). These expected failures are not passes.

A broad legacy regression command and a larger combined binary/mixed-artifact command exceeded local run limits; the complete repository suite and Python 3.14 validation were not completed. The full scheduled 3,330 positive and 3,270 negative configuration sweeps are configured in CI, not claimed executed locally.

## How to reproduce

```sh
PYTHONPATH=src:. python -m unittest discover -s tests -p 'test_multimodule_lifetime_concurrency.py' -v
PYTHONPATH=src:. python -m unittest discover -s tests -p 'test_multimodule_known_failures.py' -v
PYTHONPATH=src:. python -m tools.multimodule_interactions --inventory --output inventory.json
PYTHONPATH=src:. python -m tools.multimodule_matrix_slices --start 83 --stop 111 --graphs nested --layout compiled --case-timeout 15 --output nested.json
PYTHONPATH=src:. python -m tools.multimodule_matrix_slices --negative --start 81 --stop 109 --graphs alias --layout compiled --case-timeout 15 --output negative.json
```

The full test plan is `docs/maintenance/multimodule-interaction-test-plan.md`. Each status in the inventory is `fixture_defined` or `needs_semantic_contract`; **none** are upgraded to `verified` merely by being registered in code.
