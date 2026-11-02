# Runtime invariants, numeric boundaries and fault contracts

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
by the runtime. Use `Decimal('0.1')` or `RuntimeNumber('0.1')` when working with
exact decimal values in an embedding application.


## Regression tests

See `tests/fixtures/regressions/README.md` for the behaviour-named source
fixtures and their discovered test modules. Run `python -m unittest discover -s tests`.
