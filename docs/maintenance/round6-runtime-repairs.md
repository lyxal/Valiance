# Round 6 runtime fault repairs

This change builds on the Round 5 repaired compiler. Regression inputs from the
Round 6 audit are in `tests/round6_programs/`; run the full matrix with:

```sh
PYTHONPATH=src:. python -m unittest tests.test_round6_runtime_regressions
```

## Corrections

- Lazy lists replay their terminal error on every traversal instead of silently
  returning a cached prefix on subsequent iterations. Escaped VM panics remain
  translated into public runtime exceptions.
- Mutations of `ListValue` and `DictValue` invalidate owner/tag metadata in a
  `finally` clause, including partial mutations interrupted by an iterator.
  Empty-list operations reset both caches.
- Native list rank inference and runtime formatting use iterative traversal;
  formatting rejects cycles explicitly. JSON encoding traverses nested native
  dictionaries and lists iteratively while preserving path-local cycle checks.
- Huge decimal floor division takes mathematically exact zero/small-quotient
  shortcuts without expanding the denominator.
- `**c` computes integral powers of `-1`, `i`, and `-i` by modular exponent
  arithmetic. Zero to a negative power now reports `DivisionByZeroFault`;
  zero to a non-real exponent reports a domain fault.
- Half-powers of positive real numbers use compact decimal square roots rather
  than constructing an impractically large host `Decimal` first. Non-perfect
  roots remain approximations (up to 100 significant digits); ordinary exact
  decimal addition, subtraction, and multiplication remain unchanged.
- Trigonometric domain/range failures, invalid regex replacement references,
  invalid random ranges, and invalid transliteration alphabets now raise
  controlled Valiance faults rather than host exceptions or invalid-bytecode
  messages.

## Exact decimals and native floats

In Valiance, `0.1` is precisely one-tenth, `0.2` is precisely two-tenths, and
`0.1 0.2 + | 0.3 ==` evaluates to true. The `|` here explicitly separates the
stack operations; alternatively use `(0.1 0.2 +) 0.3 ==`.

`RuntimeNumber(0.1)` also imports the *displayed* decimal spelling of a Python
float as exact `0.1`. Python's original `0.1` float still represents a distinct
binary rational, so equality between `RuntimeNumber('0.1')` and native float
`0.1` is **false**. This is deliberate: treating them as equal while retaining
Python's exact hashes would violate the dictionary/set hash invariant restored
in Round 5. Use `Decimal('0.1')` or `RuntimeNumber('0.1')` when working with
exact decimal values in an embedding application.

## Validation environment

Tests ran with Python 3.13.5 although `pyproject.toml` requires Python 3.14+.
The full suite is not claimed to have passed. The existing `regex` dependency
remains declared in `pyproject.toml`, but the previous archive's `uv.lock` does
not include it; `uv lock --offline` cannot regenerate the lock without an
available Python 3.14 interpreter. Regenerate the lock on a 3.14 machine with
`uv lock && uv sync`.
