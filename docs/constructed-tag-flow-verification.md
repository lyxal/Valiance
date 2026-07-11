# Constructed Tag Flow Verification

Constructed-tag flow is category-driven, not tag-name-driven. Any tag declared
with `tag #name as constructed` participates in the same flow algorithm;
`#infinite` is only one example.

For every chosen operation overload, all argument positions are inspected. Each
guaranteed constructed-like input tag is projected onto every output whose rank
is at least the tagged input's effective rank. The algorithm accepts an
arbitrary fixed argument tuple and an arbitrary fixed return tuple, so it is not
limited to unary or binary elements.

For a tag at depth `d` on a rank-`r` value, the effective input rank is
`max(r - d, 0)`. A propagated tag is attached at depth
`max(output_rank - 1, 0)`.

Example:

```valiance
tag #provenance as constructed

define combine(
  a: Number, b: Number, c: Number, d: Number, e: Number
) -> Number =>
  $a $b + $c + $d + $e +
end

combine(1, 2, 3 #provenance, 4, 5)
```

The result has type `#provenance Number` and carries `#provenance` at runtime.
The same rule applies to `#infinite`, `#encrypted`, `#cached`, or any other
constructed tag.

## Arity and multiplicity

- Fixed arities from one upward use the same propagation path.
- A tag may occur in any argument position.
- Tags from several arguments are merged, subject to disjoint rules.
- Every eligible return value receives the propagated tags.
- Generic functions, ordinary functions, first-class `call`, built-ins,
  optimized code, and deserialized bytecode use the same return contract.
- A niladic function has no input value from which a tag can flow. It can still
  construct or explicitly declare a tagged result.
- Unit tags are constructed-like after an operation explicitly accepts the
  unit; their stricter parameter-admission rule still applies.

Dynamic calls also carry recursive call-site tag contracts. This matters when a
user function shadows a built-in name or another call cannot be lowered to a
resolved element reference. Static and runtime tag evidence therefore remain
aligned for higher-arity and multiple-return calls.

## Intentional removal

Automatic constructed flow is suppressed only by one of these explicit rules:

- the output rank is lower than the effective tagged input rank;
- the return contract contains `#!tag`;
- an exact return tag set excludes the tag;
- the tag's own overlay omits it from that overlay's return contract;
- the value is explicitly untagged with `#!tag` or `#-tag`.

Computed tags remain non-sticky.

## Coverage

The unit and integration suite exercises:

- several unrelated constructed tag names;
- arities 1, 2, 3, 5, 7, 8, and 12;
- tags in first, middle, and final argument positions;
- many constructed tags merged across an eight-argument call;
- multiple return values from a higher-arity function;
- first-class calls and user functions shadowing built-in names;
- niladic construction of a tagged result;
- vectorized built-in arithmetic;
- concrete and generic user functions;
- direct, optimized, and serialized execution;
- widening casts;
- constructed plus computed tags;
- rank increase, same-rank normalization, indexing, slicing, and rank drop;
- explicit absence and exact-empty return contracts;
- overlay preservation and owning-overlay removal;
- unit overlays and unit boundary rejection;
- disjoint constructed tags using left-to-right input order;
- recursive runtime tag canonicalization.

The data-tag fuzzer now cycles through all 32 modes. Earlier, its modulus was
20, which accidentally left the newer constructed-flow modes unreachable. Its
randomized constructed-flow modes generate arbitrary safe tag names, arities up
to 12, random tagged argument positions, multiple returns, optimized programs,
and bytecode round trips.

Real-world programs:

- `samples/ConstructedTagArityMatrix.vlnc`
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

Verified result: 985 tests and 326 subtests passed, plus 14,000 deterministic
fuzz cases across the four listed targets.
