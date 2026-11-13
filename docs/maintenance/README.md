# Maintaining Valiance

This section is the starting point for people changing the Valiance compiler,
runtime, command-line tools, or standard library. It explains how the pieces fit
together, where a change belongs, and how to verify that a local fix has not
broken a different stage of the language.

Valiance has several moving parts, but most changes follow the same pipeline:

```text
source text
  -> lexer
  -> parser and raw AST
  -> analyser and typed AST
  -> bytecode compiler
  -> bytecode optimiser
  -> serializer or in-memory program
  -> virtual machine
  -> runtime values and output
```

The most important maintenance rule is to preserve that separation. The parser
should describe syntax, the analyser should decide meaning and types, the
compiler should lower typed decisions, and the VM should execute the resulting
plan. When the VM has to rediscover information that the analyser already knew,
behaviour becomes harder to reason about and static guarantees become weaker.

## Read these first

- [Architecture tour](architecture.md) explains the major packages and the
  ownership boundaries between them.
- [Terminal editor and REPL design](terminal-editor-design.md) describes current
  behavior and the product contract. The [implementation plan](terminal-editor-implementation-plan.md)
  maps subsystem ownership, delivered stages, and remaining acceptance gates.
  The [interactive reference](terminal-editor-reference/index.html) illustrates
  proposed layouts with simulated data. Start the internal editor with
  `uv run python -m valiance.terminal_editor`; regression coverage lives in
  [terminal editor tests](../../tests/test_terminal_editor.py),
  [CLI/session tests](../../tests/test_main.py), and
  [compilation database tests](../../tests/test_compilation_database.py).
- [Testing and debugging](testing-and-debugging.md) includes the discovered
  regression suites and the [source fixture inventory](../../tests/fixtures/regressions/README.md).
  The detailed contracts are documented under
  [collection indexing and deferred faults](collection-indexing-and-faults.md),
  [runtime invariants and numerical boundaries](runtime-invariants-and-numerics.md),
  and [source and bytecode boundaries](source-boundary-correctness.md).
- [Understanding the type system](type-system.md) gives a human-first mental
  model of type relations, generic solving, overload application, and branch
  analysis, with traced examples and an API decision table.
- [Type-system counterexample testing](type-soundness-counterexamples.md)
  explains the executable plan registry, coverage rules, and campaign commands.
- [Reflection metadata](type-system.md#reflection-metadata) describes the
  [compiler descriptor model](../../src/valiance/vtypes/reflection.py), generic
  storage queries, [runtime metadata](../../src/valiance/runtime/reflection.py),
    heterogeneous iteration, finite runtime union and closed variant selection, checked generic union specializations and owner access,
  fallible reconstruction, managed incomplete construction, finite compiled
  shape discovery, and category metadata.
- [Reflection implementation status](reflection-implementation.md) maps completed
  support and remaining work to the design specification's R0–R6 stages.
- [Web implementation record](web.md) consolidates implementation history,
  feature coverage, protocol completion gates, qualification evidence, and
  remaining work for `std.web`.
- [Practical `std.web` preview guide](../std-web-preview.md) provides the runnable
  H1/H2 sample, supported-use envelope, deployment precautions, and the
  functional smoke qualification (distinct from full RFC acceptance).
- [Typed HTML generation](../std-html.md) documents `std.html` safe node builders, escaping, attributes and Web response integration, tested by `tests/test_std_html.py` and the optional real-browser
  `tests/test_std_html_browser.py`.
- [Web application core](web.md) tracks the currently incomplete `std.web`
  runtime and language API, including the bounded WebSocket shutdown contract,
  completion gates, acceptance matrix, and qualification evidence, with focused
  regression tests in `tests/test_std_web.py`.
  [WebSocket release qualification runner](../../tools/qualify_websocket_release.py) writes a fail-closed gate-to-command evidence report; it refuses to sign off missing Python 3.14, external interoperability, full-regression, stress or H3 prerequisites.
- [JSON encoding and decoding](reflection-implementation.md#json-encoding) provides automatic
  managed-object and checked compositional
  [Valiance encoders and typed decoders](../../src/valiance/std/json.vlnc), backed by
  [JSON parsing and runtime reconstruction](../../src/valiance/std/json.py).
- [State runtime and protected callbacks](state-runtime.md) documents the private
  FIFO gate, resumable activation cleanup, and no-new-bytecode integration.
- [Shared State user guide](../concurrency-state.md) explains the public
  `std.concurrency.State[T]` API, stack effects, and callback restrictions.
- [Understanding the runtime and code generator](runtime-system.md) follows
  typed nodes through bytecode, frames, calls, vectorisation, ownership, panics,
  serialization, and VM debugging.
- [Change playbooks](change-playbooks.md) gives step-by-step recipes for common
  work such as adding syntax, an element, a runtime value, or a CLI command.
- [Concurrency examples](../../samples/concurrency/README.md) links runnable
  multi-task scenarios to their outcome-asserting regression tests.
- [Testing and debugging](testing-and-debugging.md) explains the test layers,
  useful inspection commands, and a practical fault-isolation workflow.
  [Trait and variant semantic tests](../../tests/test_traits_and_variants.py)
  cover requirement checking, defaults, inheritance, generic evidence, closed
  matching, and dispatch through executable and serialized programs.
  [Numbered trait hardening](trait-hardening.md) describes the 306 active
  plan oracles, explicit retirements, isolated runner, and CI reports.
  Cross-layer vectorisation invariants and execution-mode parity are exercised in
  `tests/test_vectorisation_properties.py`. Multi-hop private import identity,
  re-exported generic specializations and source-free facade chains are covered
  in `tests/test_type_soundness_multimodule.py`. Incremental generic-trait implication,
  body/constraint edits, diamond re-exports and source-free dependency freshness
  are covered in `tests/test_type_soundness_incremental_traits_multimodule.py`.
  The portable FFI fixture compiler
  `tests/ffi_support.py` supports the native ABI integration tests on Windows,
  macOS, and Linux.
- [Numbered generics hardening](generics-hardening.md) describes the 351-case
  inventory, executable source fixtures, isolated runner and CI gates. The
  additional [adversarial generics tests](../../tests/test_generics_adversarial.py)
  use deterministic permutations and cross-mode comparisons to stress `where`,
  union dispatch and ranked list substitution beyond the inventory.
- [Multi-module project correctness](multimodule-project-testing.md) documents the
  19-family / six-graph source and binary execution matrix, failure oracles,
  incremental regressions, and remaining import/type-system risks.
  The [connected-feature multi-module plan](multimodule-interaction-test-plan.md)
  defines the connected cross-feature matrix and exposes the unverified
  pairwise language-interaction backlog.
  [Lifetime and concurrency integration tests](../../tests/test_multimodule_lifetime_concurrency.py)
  exercise borrowed non-duplicable resources, spawned imported functions, and
  channel payload identity across compiled module boundaries.
- [Fuzzing campaigns](../fuzzing.md) describe type-directed source and VM
  ownership invariants, the [coverage feedback runner](../../tools/coverage_guided.py),
  the [scheduler interleaving explorer](../../tools/schedule_explorer.py), and
  their focused tests in `tests/test_fuzzing.py` and
  `tests/test_concurrency_runtime.py`.
- [Performance baselines](performance-benchmarking.md) documents the
  stage-aware benchmark runner, same-machine comparisons, and CI policy.
- [Declaration-level incremental analysis](declaration-incremental-analysis.md) documents stable semantic products, dependency fingerprints, and targeted invalidation.
- [Lints and optimisation passes](lints-and-rewrites.md) documents the
  structured lint API, the bytecode optimisation pipeline, and the safety
  boundary for future typed rewrites.
- [Docstring policy](docstrings.md) explains what production docstrings should
  contain and how coverage is enforced.
- [Reference documentation](reference-documentation.md) explains the metadata,
  validation, and generators for built-ins and standard-library functions,
  including the [bundle builder](../../scripts/generate_reference_docs.py),
  [CI workflow](../../.github/workflows/reference-docs.yml), and
  [reference regression tests](../../tests/test_reference_docs.py).

The deeper subsystem references remain useful after this overview:

- [Lexer and parser guide](../Compiler%20Documentation/lexer-parser-guide.md)
- [Analysis and type-system guide](../Compiler%20Documentation/analysis-type-system-guide.md)
- [Runtime and code-generation guide](../Compiler%20Documentation/runtime-codegen-guide.md)

## Working principles

### Keep decisions in the earliest correct stage

A stage should make the decisions for which it has enough information:

- The lexer identifies tokens and source locations.
- The parser resolves grammatical ambiguity and lowers chain syntax.
- The analyser resolves names, access, overloads, vectorisation, types, and
  diagnostics.
- The compiler turns typed nodes into explicit bytecode instructions.
- The optimiser performs semantics-preserving bytecode rewrites.
- The VM performs runtime-only work such as executing instructions, inspecting
  concrete values for authorised dynamic dispatch, and raising runtime faults.

Do not move work later simply because it is convenient to implement there. A
runtime workaround for a statically knowable ordering or binding rule usually
creates a second, subtly different implementation of the language.

### Treat typed AST as the compiler contract

`compile_program(...)` accepts typed nodes. Raw AST should not bypass analysis.
Typed nodes carry selected overloads, argument order, vectorisation depth,
member-access decisions, and other information needed for deterministic
code-generation.

### Keep branch-local facts out of the global environment

The analyser's `Environment` stores declarations and relationships that are
stable across control-flow paths. `AnalysisBranch` and `BranchVariables` store
facts that can differ between paths, such as stack types, local variables,
errors, warnings, and inferred inputs.

### Change serialization deliberately

Bytecode is a file format, not merely an implementation detail. When an opcode,
instruction payload, or `FunctionCode` record changes incompatibly, update the
serializer and bump the bytecode magic/version marker. Add a round-trip test so
an in-memory success cannot hide a serialization failure.

### Prefer one source of truth

Static and runtime built-in catalogues are generated from the same declarations
in `analysis/builtins.py`. Standard-library native hooks are declared through
`stdlib_native.py`. New features should extend these existing registries rather
than adding parallel lookup tables.

## Before opening a change

1. Reduce the problem to the smallest Valiance program that demonstrates it.
2. Decide which stage first has enough information to enforce the intended
   behaviour.
3. Add a focused test at that stage and, for user-visible behaviour, a runtime
   test as well.
4. Implement the fix without weakening unrelated diagnostics or dispatch rules.
5. Run the focused test, the fundamental-program tests, and then the complete
   suite.
6. For bytecode-affecting work, include an explicit serialization round trip.

## Definition of done

A maintenance change is complete when:

- the intended source program works;
- invalid nearby programs fail with a useful diagnostic;
- typed and runtime behaviour agree;
- bytecode round trips still work when relevant;
- production functions introduced or changed have useful docstrings;
- the complete test suite passes; and
- the maintenance guides are updated when an architectural boundary changes.

### Incremental compilation coordinator

`src/valiance/incremental/coordinator.py` owns cross-process build freshness,
dependency-interface validation, recursive module artifact publication, and
structured build dispositions. CLI commands must delegate cache decisions to
this service rather than adding freshness checks to `main.py`, the analyser,
compiler, or VM. `ModuleLoader` remains the source of truth for import
resolution and analysed-interface restoration.

- [Unified compilation database](unified-compilation-database.md) documents workspace overlays, targeted invalidation, and safe executable snapshots.

- [Incremental build inspection](incremental-inspection.md) documents structured rebuild explanations and cache inspect, verify, and clean commands.
