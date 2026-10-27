# Typed AST transformations

When adding a typed node that owns checked expressions or operation plans, update
`analysis/state/transformations.py::_refine_typed_node` alongside analysis,
serialization and code generation. Its fallback produces a plain `TypedNode`,
discarding subtype-specific data even when refinement concerns an unrelated
type. Cover branch/loop refinement and nested returns so code generation keeps
checked operations instead of rebuilding them from raw AST syntax.

When working on anonymous record reflection, preserve `RecordType` through type
rebuilders. It represents the complete literal storage in declaration order;
`RowType` only expresses required properties and can omit physical fields.
Rebuilding both as `T.Row(...)` silently discards the evidence needed to select a
runtime schema. Record reconstruction must retain its reified type signature.

# JSON encoding and managed constructor metadata

`std.json.encode` uses one generic public entry point. Nested managed
objects must be encoded through the JSON-only constructor layout in
`ObjectRuntimeType`
(`json_fields` and `json_generics`), populated by the compiler and decoded by
the VM. The layout is part of the serialized constructor's runtime metadata.
Never traverse `ObjectValue.fields` for JSON without a checked layout, since
that would expose private storage. Instantiating reified generic field types
also matters for Boolean tags, including those nested in collections.

# General host reflection construction

`VirtualMachine.register_reflection_type` registers novel host-runtime managed
schemas, `construct_reflected` constructs checked occurrences without JSON,
`reflected_fields` produces ordinary metadata, and `reflected_read`/
`reflected_write` enforce visibility and type/ownership rules. Compiled `uninit[T]` also accepts native `&T` storage, but `finish` and
`finish?` require a trusted no-argument VM-local factory and validate its
returned FFI value. Bytecode serialization must accept only empty native
layouts; native construction failures become `NativeConstructionFault` or
`ConstructionError`. `reflection_environment()` snapshots VM-registered
managed schemas into an analyser environment *before* compilation. Execute
that bytecode in the same VM; registration after compilation cannot alter
the compile-time type universe.
`VirtualMachine.register_reflection_native` accepts a trusted constructor and
optional JSON encoder. Validate the result's exact native type and live handle
state before exposing it, and never manufacture an FFI address from untrusted
data. `register_json_type`/`register_json_native` are thin delegates to the
same VM-local registry, not independent JSON type systems. Source-level new type declarations *during execution* remain unsupported;
compiler-visible schemas must be registered before analysis.

# Lazy JSON serialization

`std.json.encode` traverses `LazyList` / `PlannedLazyList` through their replayable
iterators. Distinguish an unknown-length lazy list (which may be finite) from a
positively `#infinite` list (which must be rejected before iteration). Nested
type tags matter; also keep `take`'s return type marked `#-infinite` so finite
prefixes of infinite sources can be encoded without retaining the source tag.

# Typed JSON decoding

`std.json.decode[T]` is a Valiance generic wrapper over the checked native
`decodeDynamic` parser. Native decoding obtains constructor schemas from the
running VM's registered `ObjectConstructorValue`s, **not** from arbitrary JSON
field names or privately inspectable objects. The VM records constructors as
`MAKE_OBJECT_CONSTRUCTOR` executes, and `RuntimeContext` provides lookup
services that continue working for source-free compiled standard-library calls.
Custom initializers and private fields without literal constructor defaults
must fail decoding. For variants, select only when exactly one case matches the
JSON object shape, otherwise return `ValueError`.
