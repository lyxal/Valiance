# Testing and debugging

Valiance is easiest to debug by identifying the first stage whose output is
wrong. A runtime symptom can originate in parsing, analysis, code-generation,
serialization, or execution, so jumping directly to the VM often treats the
last visible failure rather than the cause.

## Test layers

The repository uses `unittest`. Important suites include:

- `tests/test_parser.py`: tokenisation, grammar, source lowering, and AST shape.
- `tests/test_analyser.py`: stack effects, name resolution, overloads, types,
  control flow, objects, traits, variants, tags, and diagnostics.
- `tests/test_runtime.py`: compiled execution and user-visible behaviour.
- `tests/test_optimizer.py`: focused pass behaviour, safety boundaries,
  control-flow retargeting, default optimisation, and compile-time opt-out.
- `tests/test_bytecode_serialization.py`: portable bytecode round trips.
- `tests/test_types.py`: type relationships and overload solving.
- `tests/test_programs.py`: all end-to-end program regressions, including
  fundamental behaviour, deterministic examples, checked-in samples, and
  optimisation workloads with differential and serialization checks. Do not
  casually edit these tests to accommodate a regression.
- `tests/test_main.py` and `tests/test_repl.py`: CLI and persistent-session
  behaviour.
- `tests/test_source_tools.py`: tidy and documentation generation.
- `tests/test_docstring_coverage.py`: production module and function docstrings.
- `tests/test_correctness_rescan.py`: cross-layer soundness regressions and
  medium-sized realistic workloads that execute both directly and after a
  bytecode round trip.

## Useful commands

The project metadata requires Python 3.14. The canonical full-suite command is:

```powershell
uv run python -m unittest discover -s tests -v
```

Run a focused module while iterating:

```powershell
uv run python -m unittest tests.test_analyser -v
uv run python -m unittest tests.test_runtime -v
uv run python -m unittest tests.test_bytecode_serialization -v
```

A particular test can be named directly:

```powershell
uv run python -m unittest \
  tests.test_runtime.RuntimeTests.test_descriptive_name -v
```

Deterministic fuzz targets exercise parser, analyser, runtime, serialization,
and type-system invariants without external dependencies:

```powershell
$env:PYTHONPATH = "src;."
python -m tools.fuzz --target all
python -m tools.fuzz --target malformed-bytecode --iterations 10000 --seed 42
```

Each failure prints a one-case reproduction command. See
[fuzzing.md](../fuzzing.md) for target descriptions and campaign guidance.

Stage-aware performance baselines and comparisons use:

```powershell
$env:PYTHONPATH = "src;."
python -m tools.performance --runs 5 --output .performance/baseline.json
python -m tools.performance_compare ../target ../candidate --runs 3 --passes 2
```

See [Performance baselines](performance-benchmarking.md) for workload coverage,
threshold semantics, and same-machine CI guidance.

When working in an environment whose available interpreter is older but still
parses the checkout, direct `python -m unittest ...` can be useful for local
feedback. Release and CI validation should still use the version declared in
`pyproject.toml`.

## Stage-by-stage diagnosis

Start with a minimal source string and inspect each boundary.

### 1. Parse

Use:

```text
vln parse --code "..."
```

Or call `parse(...)` in a focused test. Check node order, parameter names,
source locations, nesting, and chain lowering.

### 2. Analyse

Use:

```text
vln analyse --code "..."
```

Check:

- the stack before and after the failing node;
- selected overload and overload index;
- argument order and vectorisation plan;
- variable scopes and captures;
- field visibility decisions;
- typed node payload; and
- diagnostics emitted on failed branches.

If analysis is already wrong, do not compensate in code-generation or the VM.

### 3. Compile and optimise

Compile the typed nodes and inspect instructions. Use
`compile_program(typed, optimize=False)` to inspect direct codegen, then compare
it with the default optimised program. Confirm that selected static information
is represented explicitly and survives optimisation. Common losses include
overload indexes, call argument order, dispatch flags, ranks, tags, constructor
metadata, and incorrectly retargeted control flow.

### 4. Serialize

For any bytecode-relevant issue, compare unoptimised, optimised, and
round-tripped execution:

```python
unoptimized = compile_program(typed, optimize=False)
optimized = compile_program(typed)
assert run(unoptimized) == run(optimized)
assert run(optimized) == run(loads(dumps(optimized)))
```

A difference isolates the problem to serialization or record compatibility.

### 5. Execute

Only after the earlier stages agree should the VM be inspected. Record the
instruction pointer, instruction, frame stack, locals, globals, cycle state,
and selected runtime overload. Runtime errors already collect call and
execution context; preserve that detail when adding new failure paths.

## Realistic workload programs

Focused regressions and fuzzers are necessary but can miss disagreements that
only appear when several valid features are composed. Add a medium-sized
Valiance workload when a change crosses two or more of these boundaries:
analysis, generic solving, pattern matching, control flow, constructor metadata,
serialization, and runtime discrimination.

Keep workload programs readable enough that a failure still identifies the
contract under test. Prefer:

- a small domain such as configuration, retry policy, validation, settlement,
  or classification;
- several declarations and calls rather than one isolated expression;
- `|` between top-level operations and explicit call syntax, so incidental
  chaining does not obscure argument sourcing;
- one nearby competing branch, such as the wrong generic instantiation, to
  protect against over-broad runtime matching; and
- execution of both `compile_program(typed)` and
  `loads(dumps(compile_program(typed)))`.

A workload is not a replacement for a focused regression. The focused test
documents the smallest broken invariant, the workload proves that the repaired
stages agree in a plausible program, and the deterministic fuzzer explores
variations around the same boundary.

## Regression-test shape

A strong language regression usually has three parts:

1. the exact source program that exposed the bug;
2. a nearby invalid or competing case that protects the intended boundary; and
3. a bytecode round trip when the behaviour crosses serialization.

For dispatch work, test both unqualified and qualified access. For imports, test
local and imported definitions. For vectorisation, test scalar broadcasting and
ragged shapes. For mutation-like syntax, verify whether values are reconstructed
or mutated and whether lower stack values are preserved.

## Diagnosing overload problems

Write down the candidate signatures and the actual typed stack. Then inspect:

- how arguments were sourced;
- whether explicit and modifier arguments were merged in parameter order;
- generic substitutions;
- vectorisation depth and stop rank;
- candidate scores;
- specificity comparison;
- external versus object-friendly priority; and
- whether an overload index was recovered by equality instead of preserved from
  selection.

A correct static winner should be stored directly. Recomputing it later risks
collapsing distinct but structurally equal overloads.

## Diagnosing variable problems

Check scope in this order:

```text
block locals -> function locals -> parameters -> captures
```

Explicit parameters are read-only. Captured names become function-local when
assigned. Iterator or structured-control bindings should be represented as
real parameters or block locals during analysis, not invented only by runtime
execution.

## Diagnosing stack-order problems

Keep four orders separate:

- the order written in source;
- the raw/typed AST order;
- physical order on the runtime stack; and
- parameter order in a callable.

Print or assert all four in a focused test. Avoid generic stack cycling when a
compile-time call plan can state the order exactly.

## Before finishing

Run, in order:

1. the new focused tests;
2. the affected subsystem module;
3. `tests/test_programs.py`;
4. serialization tests for bytecode work; and
5. the full suite.

Existing bytecode-shape and language regression tests compile with
`optimize=False` so their historical instruction expectations remain stable. New
optimiser tests must exercise the default path explicitly. For each pass, add a
focused unit test, an `optimizer` fuzz mode, and a workload in
`samples/optimizations/` covered by `tests/test_programs.py`.

Also run the docstring coverage test after introducing helpers. A feature is not
finished if maintainers cannot tell why its new functions exist.
