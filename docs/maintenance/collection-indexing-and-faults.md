# Collection indexing and deferred runtime faults

## Fixed behavior

- Out-of-range positions in lazy indexed selections and updates now raise
  `IndexFault` when the lazy result is traversed; positions cannot be skipped.
- Finite lazy lists support negative read, selection, and update indices. As for
  other end-relative operations, resolving a negative index requires exhausting
  the source; do not use one against an infinite list.
- Eager multi-position assignment validates bounds and reports `IndexFault`.
- Ownership/duplication traversal and diagnostic value formatting handle high
  collection ranks iteratively; rank-1000 `length` and `first` now succeed.
- Exact decimal remainder uses modular exponentiation when the dividend has a
  larger power-of-ten scale, and returns an already-valid small remainder when
  the divisor has the larger scale. Division/modulo by zero both raise
  `DivisionByZeroFault` at the language boundary.
- Zero raised to an enormous *positive integral* exponent does not materialize
  the exponent as a Python integer.
- Lazy values escaping a completed `VirtualMachine.run()` translate deferred
  `PanicSignal` exceptions into public Valiance `RuntimeError` exceptions when
  callers iterate them, without changing panic behavior inside VM execution.

## Regression tests

The indexed collection and deferred-fault behaviours are verified by
`tests/test_collection_boundaries.py`, and decimal modulus by
`tests/test_numeric_boundaries.py`.
