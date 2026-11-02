# Hidden parameters in imported overload sets

Imported function sets can group several checked overloads under a single
`TypedImportedFunctionNode` whose raw function AST comes from the first member.
When compiling each member, use its own `FunctionOverloadTyping.overload`
for hidden static parameter names (`where`-computed values and rank variables).
Using the shared AST for members without a `where` clause can silently append
the first member's hidden parameter to another member, causing an incorrect
runtime call arity despite a valid static selection. CX-009 in the generics
plan checks import, `where` branch switching, incremental invalidation and
serialized execution together.

# Hidden `where` values in vectorised resolved calls

A `ResolvedElementReference` records vectorisation depths/target ranks for
source arguments, while `reference.static_values` adds extra scalar arguments
after them. `_call_resolved_function_value` and the normal overloaded-function
path pass those hidden values into `_call_function`. When vectorising a user
function, extend metadata with zero depths / absent target ranks only if its
length excludes the appended static arguments. The existing `call_value_overload`
path pads its own metadata and must not be padded twice. Cross-mode regression
fixtures in `test_generics_adversarial.py` exercise ordinary and imported calls,
empty/nested lists, pairwise vectorisation and mismatched-length faults.

# Function body errors and speculative inference

Function signature inference deliberately suppresses diagnostics from discarded
candidate branches. Do not unconditionally propagate all diagnostics from the
child analyser after a viable signature has been found: that rejects valid
inferred overload sets and branch refinements. However, when a failed
`TagApplicationNode` survives inside a published typed function body with no
type, raw lowering can bypass a checked tag validator. Check surviving typed
operations specifically and reject only those unresolved unsafe paths (see
TG-007 in the generics plan). The tag validator must be selected at analysis
time; a runtime tag operation with no validator implementation ID is not a
substitute for validation.

# Typed AST transformations

When adding a typed node that owns checked expressions or operation plans, update
`analysis/state/transformations.py::_refine_typed_node` alongside analysis,
serialization and code generation. Its fallback produces a plain `TypedNode`,
discarding subtype-specific data even when refinement concerns an unrelated
type. Cover branch/loop refinement and nested returns so code generation keeps
checked operations instead of rebuilding them from raw AST syntax.

Imported object and trait methods also carry checked expressions in
`TypedObjectNode.method_bodies`. Renaming and re-export helpers must preserve
these alongside `method_ids` and `method_results`. Dropping a checked body can
hide return-arity errors because raw lowering lacks the analysed multiplicity;
methods that intentionally return several values need explicit return types or
`@returnAll`.

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

# Re-exported private dependency and nominal provenance

A facade's `public import` carries more than renamed surface signatures. An
imported checked function may refer to a private constructor/helper in its
*original* provider; keep its existing `TypedElementNode.runtime_name` links
when exporting from the facade, rather than rebuilding the body with the
facade's provider links. Preserve `ModuleDefinition.private_nominals` so the
signature still distinguishes returned private provider types from same-named
consumer types. Keep `ModuleObject.origin` through each `_renamed_object` step,
including namespaced and selected imports, so successive `as` aliases remain
the same nominal declaration. Use `test_type_soundness_multimodule.py` with
source and source-free modules to detect failures that direct-import tests miss.

# Parallel unittest worker imports

Some test modules import sibling fixtures such as `ffi_support` as top-level
modules. `tools/test_suite.py` must add both `src/` and `tests/` to worker
`PYTHONPATH`; otherwise batches fail while the same suites pass under
`PYTHONPATH=src:tests` directly. Preserve this when changing test runners.

# Block lints and compilation-scoped builtins

Block lint rules may receive hundreds of statements. Collect read/write facts
once per block rather than recursively searching prefixes for every variable;
the variable-use rules deliberately ignore reads inside nested `FunctionNode`
bodies. Preserve that scope boundary when changing their traversal.

The builtin catalogue can change between compiler invocations. Reuse a lookup
snapshot within an individual `_Compiler` or optimisation pass, but never cache
it globally across separate compilations. Tests exercise dynamic registrations
and implementation identities; a stale catalogue can silently link the wrong
builtin even when the source name is unchanged.

# General host reflection construction

`VirtualMachine.register_reflection_type` registers novel host-runtime managed
schemas, `construct_reflected` constructs checked occurrences without JSON,
`reflected_fields` produces ordinary metadata, and `reflected_read`/
`reflected_write` enforce visibility and type/ownership rules. Compiled `uninit[T]` also accepts native `&T` storage. `finish` and
`finish?` use a trusted no-argument VM-local factory; `finishWith` and
`finishWith?` pass one typed argument through the same native validation.
`register_reflection_native(..., argument_type="T")` additionally validates the
input before calling the trusted factory; native constructor arguments may be
bundled in a checked record or dictionary.
Bytecode serialization must accept only empty native
layouts; native construction failures become `NativeConstructionFault` or
`ConstructionError`. `reflection_environment()` snapshots VM-registered
managed schemas into an analyser environment *before* compilation. Execute
that bytecode in the same VM; registration after compilation cannot alter
the compile-time type universe.
`VirtualMachine.register_reflection_native` accepts a trusted constructor and
optional JSON encoder. Validate the result's exact native type and live handle
state before exposing it, and never manufacture an FFI address from untrusted
data. `register_json_type`/`register_json_native` are thin delegates to the
same VM-local registry, not independent JSON type systems. Source code cannot
declare fresh nominal types at runtime. `lookupRuntimeType?` and
`runtimeTypeOf?` provide VM-owned witnesses for already declared or
host-registered types; checked construction and access use existential
`Dynamic` boxes. `dynamicUnbox?[T]` verifies the requested concrete type, and
witness-based access requires lexical `unsafe`. Never let `Dynamic` hide
transitive `@nodup` storage from VM duplication checks or accept a witness
issued by another VM. Late host registration cannot retroactively add static
types to code that was already compiled.

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

# Generic call safety and implicit optional/result dispatch

When checking a generic function body, scoped source-level type variables are
universally quantified, not inference metavariables. Do not specialize `T` to
`Int` just to accept `+(T, Int)`. `_solve_branch_argument` keeps those binders
rigid; explicit generic upper bounds live in the copied lexical `Context` and
can be used as *views* for overload proofs, without overwriting `T` in the
function signature. Keep negative tag requirements (`#-infinite`, etc.) on the
original generic type, not the bound view, when propagating call requirements.
`define[T] bad(x:T) -> Number => $x + 1 end` must reject at declaration unless
there is a universal generic `+` overload or an explicit applicable constraint.

Optional and Result success have **implicit value representations**: every
non-`None` value matches `Some[T]` when it matches `T`, and every non-`Err`
value matches `OK[T]` when it matches `T`. Explicit `Some(value)` and
`OK(value)` also match via their payload, including `Some(\None)` (present
nested absence). Checked casts already implemented this rule. Union dispatch
now implements it in `_runtime_pattern_matches` as well; the VM must not
require or synthesize wrappers just to dispatch a widened raw value. Both
paths must agree on the payload type and on the `None`/`Err` discriminants.


# Alpha-equivalent imported generic signatures

Import conflict tracking cannot use raw `Overload.params` tuples as dictionary
keys: `VarType` retains the binder's source spelling and lexical identity, so
`define[T] f(x:T)` and `define[U] f(x:U)` appear different even though they
have the same input signature. Use `T.overload_parameter_shape(overload)` for
both conflict lookup and commit, including source-free `.vbcm` imports.
Do not drop binder correlation: `(T,T)` and `(T,U)` are different patterns.
This is **not** permission to infer extra constraints inside generic bodies;
keep declaration-time universal checking as documented above.

# Private runtime dependencies in module imports

`ModuleExports` includes private local declarations for runtime linking even
when only a public function is selected by source import. An import prelude
installs them under unique provider-scoped runtime bindings; imported checked
calls must be retargeted to those bindings without rewriting their overload
implementation IDs. In analysed `TypedElementNode`s, a local call may already
carry `runtime_name == node.name` (not just `None`), so both forms must be
considered. Private nominal runtime type names need provider qualification to
keep source-free modules and checked casts sound. None of these dependencies
should enter the caller's source-level symbol or overload environment.
