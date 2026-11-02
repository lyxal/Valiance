# Round 3 runtime repairs

This release continues from `Valiance_phase8_runtime_round2_fixed.zip` and
addresses the 14 reproducer inputs from `Valiance_round3_bug_repros.zip`.

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

## Verification

Run the added regression tests from the project root:

```bash
PYTHONPATH=src:.:tests python -m unittest -q tests.test_round3_runtime_regressions
```

They exercise all 14 original reproducers in optimized, unoptimized, and both
serialized modes, plus negative-index bounds and mixed-scale remainder signs.
The Round 2 regression tests, selected ownership/indexing/type-soundness suites,
end-to-end compiler tests, and six deterministic fuzz targets were also run.

**Important:** The validation environment used Python 3.13.5. The project
requires Python 3.14 or newer. Full-suite and infinite-list behavior remain
outside this round's validation scope.
