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
- `valid-programs` generates grammar-valid arithmetic, variables, conditionals,
  functions, `both`/`correspond` call-site partitions, flat vectors, and nested
  vectors. It compares execution with an
  independent `Decimal` model and requires direct bytecode execution to equal a
  serialize/deserialize execution.
- `serialization` generates nested bytecode records, functions, function sets,
  dispatch patterns, vector extensions, object constructors, and every opcode.
  It requires exact object round trips and canonical re-encoding.
- `malformed-bytecode` truncates, flips, replaces, appends, and splices bytes in
  valid programs. A payload must either decode successfully and stabilize after
  re-encoding or fail with `BytecodeFormatError`.
- `type-relations` generates nested concrete types and checks normalization,
  equality, subtyping, assignability, merging, exact/minimum list and array
  covariance, one-way array-to-list compatibility, tags, rows, and display
  invariants. Failure reports include both generated types and the wrapper rank.
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
