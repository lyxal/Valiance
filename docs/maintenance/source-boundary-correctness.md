# Source boundary and bytecode correctness

## Resolved categories

- **Exact text/formatting of large decimals:** `BigDecimal` now renders large
  coefficients in safe, lossless decimal chunks, avoiding Python's default
  integer-to-string size limit. `format(number, "")`, `to[String]`, `println`,
  and bytecode-serialized numeric literals retain every significant digit.
  Precision-limited numeric calculations remain explicit; formatting uses an
  exact `Decimal` construction instead of rounding through a 100-digit context.
- **Number token boundaries:** the lexer recognizes ASCII decimal numerals,
  accepts legal zero scientific and complex notation, and diagnoses signed
  leading zeroes and non-ASCII superscripts without leaking decimal errors.
- **Long string-to-integer conversions:** `numeric?` and `to[Int]` no longer
  use `int(text, 10)` for arbitrary-length input. Existing single underscores
  and Unicode decimal-digit strings continue to work (unlike Unicode source
  numeric literals, which are lexically restricted to ASCII).
- **Bounded indices/counts:** huge compact decimal indices are recognized
  without materializing their powers of ten, and finite rotation/take/removal
  are bounded by their actual collections. Lazy `take` retains exact large
  limits; it does not silently truncate an infinite stream to a fixed cap.
  Empty strings repeat to empty strings without converting huge count values.
- **Unicode scalar validation:** `fromCharcode` now rejects surrogate code
  points and values outside U+0000..U+10FFFF with a `RangeFault`.
- **Complex ordering:** `positive?` requires a real operand in the analyser.
- **Python embedding:** `int(RuntimeNumber("-0.1"))` truncates towards zero,
  consistent with Python's `__int__` contract. It does not change Valiance
  decimal floor-division semantics.

## Regression tests

See `tests/fixtures/regressions/README.md` for the behaviour-named source
fixtures and their discovered test modules. Run `python -m unittest discover -s tests`.
