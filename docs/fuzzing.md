# Fuzzing Valiance

Valiance includes deterministic, dependency-free fuzz targets for compiler and
runtime boundaries. They use the Python standard library so they can run in an
offline checkout, and every generated case is reproducible from a target name,
base seed, and iteration number.

## Targets

- `lexer-parser` feeds token soup, comments, interpolation fragments, Unicode,
  delimiters, operators, and malformed source through the lexer and parser. It
  checks token location invariants and requires malformed source to fail through
  `LexError` or `ParseError`, not an internal exception.
- `source-mutations` damages slices of checked-in `samples/*.vlnc` programs by
  deleting, inserting, replacing, duplicating, and transposing source text. It
  checks the same diagnostic-only failure contract against near-valid grammar.
- `parser-depth` generates deeply nested and selectively truncated parentheses,
  lists, and tuples. Excessive nesting must either parse successfully or fail
  with a language diagnostic rather than leaking Python recursion failures.
- `valid-programs` generates grammar-valid arithmetic, variables, conditionals,
  functions, `both`/`correspond` call-site partitions, flat vectors, and nested
  vectors. It compares execution with an
  independent `Decimal` model and requires direct bytecode execution to equal a
  serialize/deserialize execution.
- `serialization` generates nested bytecode records, functions, function sets,
  dispatch patterns, vector extensions, object constructors, function
  stack-input flags, parameter ranks, and every opcode. It requires exact object
  round trips and canonical re-encoding.
- `numeric-booleans` generates host Boolean constants at the bytecode boundary
  and requires them to canonicalize to the language's numeric `0`/`1`
  representation after decoding and execution.
- `malformed-bytecode` truncates, flips, replaces, appends, and splices bytes in
  valid programs. A payload must either decode successfully and stabilize after
  re-encoding or fail with `BytecodeFormatError`.
- `bytecode-depth` constructs portable bytecode with deeply nested tuple values.
  Recursive payloads must decode and stabilize or fail through
  `BytecodeFormatError`, never through an implementation exception.
- `runtime-bytecode` executes bounded, straight-line programs containing
  malformed instruction arguments. They may succeed or raise Valiance's public
  `RuntimeError`, but Python implementation exceptions must not escape.
- `type-relations` generates nested concrete types and checks normalization,
  equality, subtyping, assignability, commutative upper-bound merging,
  exact/minimum list and array covariance, one-way array-to-list compatibility,
  tags, rows, and display invariants. Failure reports include both generated
  types and the wrapper rank.
- `type-algebra` targets the highest-risk algebraic contracts directly: bottom
  intersections, numeric-intersection simplification, optional covariance,
  associative branch and stack joins, permutation-independent generic evidence,
  and overload declaration-order independence.
- `analyser-never` checks analyser recovery around bottom-typed paths. It
  verifies that nested primary errors do not produce generic wrapper
  diagnostics, and that a direct `Never` result terminates subsequent analysis
  while preserving the typed prefix.
- `smart-diagnostics` checks that typo suggestions are name-similar and
  overload-viable, explicit-call and named-argument mistakes receive focused
  guidance, overload lists stay multiline and omit repeated `Function`
  wrappers, and actionable lint patterns remain non-fatal. It also checks that
  rendered and structured lint views agree, rewrite hints are marked
  semantics-preserving, and dead/duplicate match patterns retain stable codes.
- `match-safety` exercises exhaustiveness, guards, destructuring, alternative
  bindings, equality bindings, source-order lowering, correlated multi-subject
  narrowing, and analyser/compiler/runtime agreement for generated matches.
- `soundness-boundaries` targets optional/result covariance and joins, runtime
  tags and generic patterns, checked casts, panic handlers, and malformed
  control-flow targets. It checks that static acceptance is reflected by the
  runtime representation rather than by naming heuristics.
- `correctness-workloads` runs medium-sized valid programs through analysis,
  compilation, execution, serialization, and restored execution. Its cases use
  explicit calls and `|` separators and cover optional/result workflows,
  dictionaries and collection ranks, user traits and faults, direct and
  transitive generic trait projections, and closed match exhaustiveness.
- `structural-types` builds contexts containing nominal subtype facts,
  declaration-site variance, row fields, and structural overloads. It generates
  named and anonymous generics, rows, anonymous traits, functions, collections,
  unions, optionals, and generic constructors. It checks row width/depth laws,
  alpha-renaming, capture-avoiding substitution, coherent shared substitutions,
  overload/requirement ordering, generic bounds, trait parameter and return
  variance, and the directional implication `subtype => assignable => compatible`.
  Positive worlds are built by construction; controlled mutations exercise
  missing fields, incompatible evidence, reversed variance, and ambiguous trait
  solutions.

## Running a campaign

From the repository root:

```text
PYTHONPATH=src:. python -m tools.fuzz --target all
```

The default campaign runs 1,000 cases per target at recursion depth 2. Select a
single target or repeat `--target`:

```text
PYTHONPATH=src:. python -m tools.fuzz \
  --target lexer-parser \
  --target malformed-bytecode \
  --iterations 10000 \
  --seed 12345
```

Depth 2 is intentionally the default because nested overload and structural
analysis becomes substantially more expensive at depth 3 and above. Larger
target-specific campaigns can raise the limit deliberately:

```text
PYTHONPATH=src:. python -m tools.fuzz \
  --target serialization \
  --iterations 50000 \
  --max-depth 4
```

## Reproducing a failure

Failures print a complete command. A typical reproduction looks like:

```text
PYTHONPATH=src:. python -m tools.fuzz \
  --target lexer-parser \
  --seed 1511464998 \
  --start 24 \
  --iterations 1
```

Cases are independently seeded, so changing `--start` does not require replaying
all earlier iterations. Keep the target, seed, iteration, depth, and source
length from the failure report when reducing a case.

## Test-suite integration

`tests/test_fuzzing.py` runs bounded deterministic smoke campaigns and forces
every opcode through serialization. These tests are deliberately smaller than a
manual campaign so the ordinary unit suite remains useful during development.

When a fuzzer finds a defect, add a focused regression test near the affected
subsystem before fixing it. The fuzz smoke test should remain as the broad guard,
while the focused test documents the exact language or bytecode contract.
Structural relation regressions belong in `tests/test_structural_types.py`; use
`tests/test_structural_analysis.py` when source parsing, inference, or diagnostics
must participate in the failure.
