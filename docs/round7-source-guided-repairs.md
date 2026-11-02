# Round 7 — Source-guided correctness repairs

This revision applies the findings in the Round 7 source-guided audit to the
Round 6 repaired Valiance baseline. The original 41 Valiance source programs
are installed as `tests/round7_programs/*.vlnc`. Run:

```sh
PYTHONPATH=src:tests python -m unittest -q tests.test_round7_source_regressions
```

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

## Verified here

- 41 audit programs across four modes (164 expectation assertions), plus 12
  positive controls across all four modes (48 checks).
- 252 parser/optimizer/end-to-end/previous-round/new regression test methods;
  a further 54 selected runtime regression test methods. Both runs passed.
- 11 Python host probes passed.
- The existing bytecode test selection remains at 86 passing, one skipped,
  and one *pre-existing* failure that expects header version 0x46 although
  this baseline emits 0x47. This repair does not change the bytecode version.

Testing used CPython 3.13.5, not the project's declared minimum of 3.14.
The complete large legacy runtime suite and full repository suite were not
completed in this environment. The `uv.lock` / regex dependency limitation
from earlier rounds remains unchanged.
