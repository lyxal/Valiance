# Constructed Tag Flow Verification

Constructed and unit tags now follow the sticky flow rule from section 17.1.
For every chosen operation overload, each guaranteed constructed-like input tag
is projected onto each output when the output rank is at least the tagged
input's effective rank.

For a tag at depth `d` on a rank-`r` value, the effective input rank is
`max(r - d, 0)`. A propagated tag is attached at depth
`max(output_rank - 1, 0)`.

Example:

```valiance
tag #infinite as constructed
#infinite [1, 2, 3] + 4
```

The result has type `#infinite Integer+` and carries `#infinite` at runtime.
No overlay is required.

## Intentional removal

Automatic constructed flow is suppressed only by one of these explicit rules:

- the output rank is lower than the effective tagged input rank;
- the return contract contains `#!tag`;
- an exact return tag set excludes the tag;
- the tag's own overlay omits it from that overlay's return contract;
- the value is explicitly untagged with `#!tag` or `#-tag`.

Computed tags remain non-sticky. Unit tags use the same propagation rules after
an operation has explicitly accepted the unit, such as through a unit overlay.

## Coverage

The unit and integration suite exercises:

- constructed tags on either binary operand;
- vectorized built-in arithmetic;
- concrete and generic user functions;
- direct, optimized, and serialized execution;
- widening casts;
- multiple constructed tags;
- constructed plus computed tags;
- rank increase, same-rank normalization, indexing, slicing, and rank drop;
- explicit absence and exact-empty return contracts;
- overlay preservation and owning-overlay removal;
- unit overlays and unit boundary rejection;
- disjoint constructed tags using left-to-right input order;
- recursive runtime tag canonicalization.

Real-world programs:

- `samples/ConstructedTagInteractions.vlnc`
- `samples/TelemetryTagPipeline.vlnc`
- `samples/ConstructedTagFlow.vlnc`

Verification commands:

```sh
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m tools.fuzz --target data-tags --seed 1511464998 --iterations 6000 --max-depth 3
PYTHONPATH=src python -m tools.fuzz --target serialization --seed 2246822519 --iterations 3000 --max-depth 3
PYTHONPATH=src python -m tools.fuzz --target type-relations --seed 2654435761 --iterations 3000 --max-depth 3
PYTHONPATH=src python -m tools.fuzz --target malformed-bytecode --seed 362436069 --iterations 2000 --max-depth 3
```

Verified result: 979 tests and 301 subtests passed, plus 14,000 deterministic
fuzz cases across the four listed targets.
