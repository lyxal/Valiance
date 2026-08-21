# Worked language examples

The programs in [`docs/tentative examples`](tentative%20examples/) are small,
illustrative Valiance programs. They are documentation, not test fixtures; the
canonical executable program regressions live directly in `tests/test_programs.py`.

## Running an example

Use the file runner for programs that terminate:

```text
vln run --file "docs/tentative examples/SumOfSquares.vlnc"
```

Interactive or intentionally unbounded programs, such as the number guessing
game, Conway's Game of Life, and the Brainfuck interpreter, should be stopped by
the user. Their tests execute finite deterministic variants instead.

The programs under `samples/optimizations/` are medium-sized deterministic
workloads for the bytecode optimiser. They cover project estimation, shipment
pricing, subscription forecasting, ledger reordering, and a payroll feature
flag. `tests/test_programs.py` runs each through direct codegen,
default optimisation, and bytecode serialization while checking the intended
rewrite family.

## Example index

| Example | Main features exercised |
| --- | --- |
| `ConwayGameOfLife.vlnc` | `std.grids.allNeighbors`, `std.random.randbit`, named arguments, niladic mapping, `removeAt`, `sum`, `reshape`, grouped constants |
| `GuessingGame.vlnc` | `std.random.between`, `input`, `parseInt`, matching an optional parse result, `break` |
| `CeaserCipher.vlnc` | local imports, `string.\Alphabet`, `rotate`, `string.transliterate` |
| `RunLengthEncoding.vlnc` | `groupConsecutive`, tuple construction, `length: Int`, `first`, `reduce`, `join` |
| `StackCalculator.vlnc` | `split`, membership with `in`, `numeric?`, `parseInt`, function values, `drop`, `last` |
| `SumOfSquares.vlnc` | contextual vectorisation, `square`, and `**` |
| `TrapezodialRule.vlnc` | higher-order functions, vectorised function calls, numeric ranges, `sum` |
| `Fibonacci.vlnc` | `unfold`, `@recursive`, tuple assignment, `while`, augmented assignment |
| `Records.vlnc` | anonymous record types, field reads, `record.extend`, `record.merge` |
| `ArgumentCycling.vlnc` | cycling explicitly declared function parameters |
| `Dip.vlnc` | call-site checked `Function` parameters and caller-stack borrowing |
| `TraitInheritance.vlnc` | traits implementing traits, inherited requirements, inherited default methods |
| `GenericFind.vlnc` | anonymous trait constraints and structural equality with `===` |
| `Brainfuck.vlnc` | tags and validators, tagged indices, string mapping, nested record/list updates, `overtake`, `dropLast`, `fromCharcode` |
| `OptionalMemberAccess.vlnc` | safe optional reads, deep chains, mixed `.`/`->`, and `\None` value propagation |

## Important semantic notes

### Conway's Game of Life

An assignment consumes the expression result. When a computed value is needed
again, read the variable explicitly:

```vlnc
$neighbors = $board allNeighbors(wrapping = true)
$neighbors map fn (cells) => ... end
```

`allNeighbors` returns the neighborhood in this order:

```text
[top-left, top, top-right, left, cell, right, bottom-left, bottom, bottom-right]
```

When `wrapping = false`, out-of-bounds positions are omitted. With wrapping
enabled every neighborhood contains nine values.

### Run-length encoding

`length` returns `Int`, so an encoded run has type `{Int, String}`.
When feeding one pipeline directly into another, use `|` to establish the
boundary:

```vlnc
"aaabbc" encode | decode
```

### Trapezoidal rule

The interval width is `($b - $a) / $n`. The function parameter is named `fn`,
so calls inside the body use `$fn(...)`.

### Fibonacci

The implemented `unfold` emits the first generated state rather than the two
initial seed values. The example handles indices `0` and `1` directly and uses
`$n - 2` for the generated sequence. The iterative version increments its loop
counter and returns `$prev`, which is `F(n)` after `n` iterations.

### Brainfuck

The tape pointer is a validated unit tag. Applying `#TapePointer` checks the
half-open range `0 <= pointer < TAPE_SIZE`; `#-TapePointer` removes the tag for
ordinary integer operations. Every non-jump instruction increments `$pc`.

### Optional member chains

A safe access always has an optional result:

```vlnc
$x->a->b->c
```

Each `->` unwraps a present `Some`, reads the field, and wraps a non-optional
field back in `Some`. `None` propagates through the rest of the chain. If the
field is already optional, the result is flattened instead of becoming nested.

Ordinary access may appear before the first safe boundary:

```vlnc
$x.a.b->c
```

An ordinary `.` immediately after `->` is unsafe because the previous result is
still optional:

```vlnc
$x->a.b # compile-time error
$x->a->b # safe
```
