# Source-level regression fixtures

These are permanent, feature-oriented Valiance programs, not audit-session
artifacts. Their filename describes the behaviour under test. Each `.vlnc`
file is referenced by an explicit expectation in one of the regularly
`unittest discover`-collected test modules:

- `lexer/` → `test_numeric_lexer.py` (accepted tokens and lexical diagnostics)
- `analysis/` → `test_numeric_static_analysis.py` (static type diagnostics)
- `numeric/` → `test_numeric_boundaries.py` (exact arithmetic, power and display)
- `collections/` → `test_collection_boundaries.py` (indexing and bounded counts)
- `unicode/`, `stdlib/` → `test_stdlib_faults.py` (public fault contracts)
- `bytecode/` → `test_bytecode_numeric_literals.py` (serialization parity)

Run from the repository root:

```sh
python -m unittest discover -s tests -p 'test_*.py'
```

The seeded numeric and collection reference models are in
`tests/test_runtime_properties.py` and run through the same discovery command.

Individual modules can be run with `python -m unittest tests.test_numeric_boundaries`
(and equivalent names for other subsystems). Fixtures are checked in both
optimization settings, with and without bytecode serialization. Do not replace
exact value/fault assertions with smoke checks; keep source and its expectation
together when adding or moving a case.

When adding a new test, choose a named module and source fixture for the
**invariant**, not the date or investigation that discovered it. Avoid opaque
numeric IDs and re-testing an identical program solely to inflate counts.
