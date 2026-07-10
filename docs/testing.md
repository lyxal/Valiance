# Testing Valiance programs

Valiance tests are ordinary niladic definitions marked with `@test`. Test groups
are niladic definitions marked with `@testgroup`. Both annotations accept an
optional display description.

```vln
import { std.testing }

@testgroup("Arithmetic")
define \arithmetic =>
  @test("adds two numbers")
  define \addition =>
    testing.assertEqual(20 + 22, 42)
  end

  @testgroup("Division")
  define \division =>
    @test("rejects zero divisors")
    define \zeroDivisor =>
      testing.assertPanics: fn =>
        DivisionByZeroFault("division by zero") panic
      end
    end
  end
end
```

The annotation descriptions are used in reports. Definition names form stable,
dotted selectors. The example above defines:

```text
arithmetic.addition
arithmetic.division.zeroDivisor
```

## Test results

A test passes when it returns normally with an empty stack.

A returned `AssertError`, including one produced by `assert...else`, is reported
as a test failure:

```vln
@test
define \positiveResult =>
  assert =>
    calculate() > 0
  else =>
    "calculate must return a positive value"
  end
end
```

A bare failed `assert` is also a failure. Other returned `Err` values, uncaught
panics, runtime faults, compiler errors, and ordinary values left on the stack
are reported as test errors.

## `std.testing`

The initial testing module exports four helpers:

- `testing.assertEqual(actual, expected)`
- `testing.assertNotEqual(actual, unexpected)`
- `testing.assertPanics: fn => ... end`
- `testing.fail(message)`

These helpers behave like a bare `assert`: they return nothing on success and
raise an assertion failure with structured diagnostics on failure. This means
multiple helpers can be used sequentially without leaving values on the stack.
Equality follows Valiance's runtime value equality.

## Running tests

`vln test` discovers `*.vlnc` files recursively under the project's `tests/`
directory:

```text
vln test
```

A dotted selector can name a group or a single test:

```text
vln test arithmetic
vln test arithmetic.division
vln test arithmetic.division.zeroDivisor
vln test arithmetic.addition arithmetic.division.zeroDivisor
```

Selecting a group runs all of its descendant tests. Multiple selectors form a
union and a test is never run twice.

Paths can restrict source discovery:

```text
vln test ./tests/numbers
vln test ./tests/numbers/arithmetic.vlnc arithmetic.division
```

Other options:

```text
vln test --filter division
vln test --list
vln test --list --flat
vln test --fail-fast
vln test --show-output
```

`--filter` performs a case-insensitive search over logical names and display
descriptions. `--list --flat` emits copyable dotted selectors. Test output is
captured by default and displayed for failed or errored tests; `--show-output`
also displays output from passing tests.

The command exits with status `0` when every selected test passes and status `1`
when discovery fails or any selected test fails or errors.


## End-to-end language example regressions

The compiler's Python suite also contains end-to-end tests for programs that are
not practical as ordinary `@test` definitions:

- `tests/test_runtime.py` covers both Conway's Game of Life spellings and direct
  `std.grids.allNeighbors` edge/wrapping behavior.
- `tests/test_tentative_examples.py` covers the guessing game, Caesar cipher,
  run-length encoding, stack calculator, numeric examples, Fibonacci variants,
  records, argument cycling, user-defined `dip`, trait inheritance, and generic
  `find`.
- `tests/test_brainfuck_example.py` compiles and serializes the complete
  interpreter, replaces `input` deterministically, and checks successful and
  failing programs.
- `tests/test_optional_member_access.py` covers named and stack safe access,
  assignment, flattening, vectorisation, deep/mixed chains, diagnostics, and
  bytecode round trips.

Interactive input and randomness should be patched at their Python boundary so
regressions are deterministic. Infinite examples should be tested by extracting
a finite step or by supplying input that reaches a terminating branch. A useful
program regression asserts the computed value or output, not merely that parsing
and compilation complete.
