# Performance baselines and regression checks

Valiance has a stage-aware benchmark runner in `tools/performance.py` and a
same-host paired runner in `tools/performance_compare.py`. The first creates and
checks JSON reports. The second is the preferred merge-gate workflow because it
alternates a target checkout and candidate checkout on the same worker.

## Workload coverage

The suite contains deterministic workloads for small-program startup, arithmetic
reduction, lazy pipelines, vectorisation, guarded classification, text handling,
a medium record/text transformation, object construction, standard-library
imports, a real multi-module fixture, nested collections, and recursion.

Every workload measures parsing, analysis, compilation, serialization, loading,
VM execution, and a complete cold pipeline. Reports also contain raw samples,
median absolute deviation, a Python-only host calibration, result and source
hashes, bytecode size, recursive instruction count, process peak RSS context,
and opt-in VM optimization counters.

The multi-module workload writes a temporary `main.vlnc` and imported modules.
Each analysis sample receives a fresh `ModuleLoader`, so the measurement includes
cold module resolution, parsing, and analysis rather than an accidentally warm
in-process module cache.

## Create or inspect one report

Use the same interpreter, machine, and power mode when comparing stored reports:

```text
PYTHONPATH=src:. python -m tools.performance \
  --runs 5 --warmups 1 --output .performance/baseline.json
```

Filter by workload name or category during investigation:

```text
PYTHONPATH=src:. python -m tools.performance \
  --filter imports --filter vectorisation \
  --runs 7 --output .performance/focused.json
```

A direct stored-report comparison returns status 0 for pass, 1 for a material
regression, and 2 for borderline findings that should be rerun:

```text
PYTHONPATH=src:. python -m tools.performance \
  --runs 5 --output .performance/current.json \
  --compare .performance/baseline.json
```

The default regression boundary requires both a 15% relative slowdown and a
2 ms absolute slowdown. A borderline result reaches 75% of both thresholds.
Requiring both avoids treating short-stage timer noise as a regression.

## Preferred paired comparison

Compare two complete checkouts on one host:

```text
PYTHONPATH=src:. python -m tools.performance_compare \
  ../Valiance-target ../Valiance-candidate \
  --runs 3 --passes 2 --borderline-reruns 1 \
  --output-dir .performance/paired
```

Pass order alternates: target then candidate, followed by candidate then target.
Raw samples are pooled and robust statistics are recomputed; pass medians are
not averaged. If the first comparison is borderline, the runner adds a balanced
pass and compares again. It writes per-pass reports, merged reports, and a
`comparison.json` summary.

The calibration measures deterministic Python-only integer work. If merged
calibration medians differ by more than 20%, the paired runner exits with status
2 and marks the host unstable. This is a signal to rerun on a quieter worker,
not evidence that Valiance regressed.

## Reading reports

Prefer stage medians for decisions and inspect raw samples plus median absolute
deviation when a finding is close to a threshold. Peak RSS is cumulative process
context, not an isolated allocation measurement. `optimization_stats` helps
explain prepared-call selection and unexpected fallback. `instruction_count`
and `bytecode_bytes` show whether a speed change is accompanied by substantially
larger generated programs.

Workloads are compared only when both source and fixture hashes match. A changed
result is always a regression. A changed source or imported fixture is a new
measurement and is deliberately not compared with stale data.

## CI policy

A reliable performance job should:

1. pin the Python build and worker class;
2. use `tools.performance_compare` on one worker;
3. disable concurrent CPU-heavy jobs where possible;
4. retain all JSON reports as artifacts;
5. rerun borderline findings automatically;
6. reject an unstable calibration rather than attributing host noise to code;
7. run the complete correctness suite separately.

Do not use a developer-machine JSON file as a universal threshold. Cross-machine
trend data can be useful telemetry, but it should not be a merge gate without a
controlled normalization system.

## Guarded-match profiling and Patch B

The guarded-control-flow workload should report
`prepared.strategy.match-dispatch`. Patch B also exposes counters such as
`prepared.rejected.constant`, `prepared.rejected.straight-line`, and
`prepared.rejected.symbolic-match` when optimization statistics are enabled.
These are preparation-time diagnostics only; normal VMs do not allocate or
increment them.

Guard programs may contain dynamically loaded built-ins because their compact
bytecode predates ordinary resolved-call lowering. The prepared dispatcher
selects a pure ownership-trivial overload once for each complete runtime shape
and then invokes the cached implementation directly. A shape includes numeric
integer/real category and runtime tags, so caching does not collapse overloads
that differ on those facts. Less common aggregate values retain the conservative
canonical runtime-type description.

Profile guarded workloads with:

```text
PYTHONPATH=src:. python -m cProfile -o .performance/guarded.prof \
  -m tools.performance --filter control-flow --runs 1 --warmups 1
```

The current suite contains two independent guarded classifiers. Improvements
must benefit both or preserve the second within ordinary paired-run variance;
a result tied only to the original constants or branch labels is not considered
a general optimization.

## Static-call metadata audit

Patch C preserves the analyser's selected element overloads inside match-guard
bytecode. Guard lowering now emits `CALL_RESOLVED_ELEMENT` rather than discarding
typed nodes and rebuilding `LOAD_ELEMENT` plus dynamic `CALL` instructions.
The prepared match dispatcher therefore binds the selected pure implementation
without runtime overload discovery.

Audit a program recursively, including nested functions and pattern guards:

```text
PYTHONPATH=src:. python -m tools.audit_runtime_calls --file program.vlnc
PYTHONPATH=src:. python -m tools.audit_runtime_calls --code '1 2 +'
```

`resolved-element` is the desired category for statically selected element
calls. `dynamic-loaded-element` identifies a loaded named element followed by a
generic call, while `dynamic-callable` covers first-class or otherwise dynamic
callee values. The latter categories are not automatically defects, but they
provide a focused inventory for deciding whether more analysis metadata is lost
during lowering.
