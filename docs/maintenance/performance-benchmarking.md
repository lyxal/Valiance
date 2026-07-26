# Performance baselines and regression checks

Valiance has one stage-aware benchmark runner in `tools/performance.py`. It is
intended to answer whether a change affects parsing, analysis, compilation,
serialization, bytecode loading, VM execution, or complete cold execution. The
older focused benchmark scripts remain useful for investigating a particular
runtime mechanism, but they are not the regression baseline.

## Workload coverage

The suite contains deterministic workloads for:

- small-program startup;
- arithmetic reduction;
- lazy map/filter/reduce pipelines;
- user-function vectorisation;
- guarded classification;
- text processing;
- object construction;
- standard-library import analysis;
- nested collection/grid traversal; and
- iterative execution of a recursive function.

Each workload is parsed, analysed, compiled with the default optimiser,
serialized, loaded, and executed. The runner also measures a complete cold
pipeline, records bytecode size and recursive instruction count, validates that
staged and end-to-end results agree, and captures the VM's opt-in optimisation
statistics.

## Create a baseline

Run benchmarks on an otherwise idle machine. Use the same Python build, machine,
power mode, and operating-system environment for the baseline and comparison.
Five runs and one warmup are the normal local settings:

```text
PYTHONPATH=src:. python -m tools.performance \
  --runs 5 --warmups 1 --output .performance/baseline.json
```

The JSON report includes the environment and every raw timing sample. Do not
commit a developer-machine baseline as a universal threshold: timings from
different hosts are not comparable. CI should retain a baseline produced by the
same runner class or compare a candidate and its target branch on the same
worker.

Filter by workload name or category while investigating a subsystem:

```text
PYTHONPATH=src:. python -m tools.performance \
  --filter imports --filter vectorisation \
  --runs 7 --output .performance/focused.json
```

## Detect regressions

Compare a candidate against a stored same-machine baseline:

```text
PYTHONPATH=src:. python -m tools.performance \
  --runs 5 --warmups 1 \
  --output .performance/current.json \
  --compare .performance/baseline.json
```

The command exits with status 1 when a matching workload and stage exceeds both
thresholds. Defaults are a 15% relative slowdown and 2 ms absolute slowdown.
Using both avoids failing on timer noise in very short stages. Adjust them only
for a deliberately stricter or noisier environment:

```text
--regression-limit 0.10 --absolute-tolerance 0.005
```

A workload whose source hash changed is not compared, because it is no longer
the same measurement. A changed result is always reported as a regression.
Performance changes should still pass the complete correctness suite; benchmark
success is not evidence of semantic equivalence.

## Reading the report

For each stage, the report stores the median, minimum, raw samples, and current
process peak resident-set size. Prefer the median for regression decisions and
inspect raw samples when a result is close to a threshold. The process RSS value
is diagnostic context rather than an isolated allocation measurement because
portable standard-library APIs expose a cumulative process peak.

`optimization_stats` explains which prepared-call strategies were selected.
Unexpected general fallbacks can therefore be investigated before adding a new
special case. `instruction_count` and `bytecode_bytes` help distinguish runtime
speedups from compiler rewrites that merely produce a larger program.

## CI policy

A reliable CI job should:

1. pin the interpreter and worker class;
2. disable competing jobs where possible;
3. run the target branch and candidate on the same worker;
4. retain both JSON reports as artifacts;
5. fail only when both tolerances are exceeded; and
6. rerun a borderline failure before treating it as actionable.

Do not compare reports produced by unrelated machines. For cross-machine trend
tracking, normalize results against a stable calibration process and treat the
result as telemetry rather than a merge gate.
