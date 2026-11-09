# Canonical diamond imports and trait evidence

A public diamond can re-export the *same* object declaration under the same
visible name via two facades while each facade contributes different trait
implementations. Compare `ModuleObject.origin` with
`Environment.context.nominal_origins`, not intermediary facade paths: the
nominal constructor/method surface must be registered only once, but the
second path's trait implementation evidence must still be linked. Do not
permit same-short-name types from different providers to unify. Check
`test_diamond_reexports_same_generic_nominal_with_split_trait_evidence` and
the negative cross-provider diamond regression in
`test_multimodule_generic_trait_executable_closure.py`.

# Trait implication dispatch and generic bound evidence

Multiple imported `trait[T: Bound] Source as Target[T]` implementations may
have the same unqualified runtime receiver (`Source`). Compiled methods retain
receiver-indexed `dispatch_bounds`, including when a module re-exports the
implementation's friendly surface. Nominal-trait dispatch resolves a concrete
object's `Source` ancestor via its guarded generic projection facts, then checks
those bounds. Do not substitute the object's own type parameters for the
trait's projected parameters: `object[A, B] Pair as Source[B]` would apply the
wrong constraint. Preserve ambiguity if two distinct bound implementations
remain applicable; never resolve it by import order. The multi-module
regression covers serialized and source-free execution.

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

# Runtime constructor metadata and provider-owned dependency closure

Imported declarations carry provider-qualified runtime identities and a private
transitive declaration closure. `modules_system/runtime_dependencies.py` derives
this closure from checked object field types, generic constraints, callable
signatures and implementation patterns; it is **not** tied to the library that
may consume the metadata. Public import surfaces must not expose the private
required declarations. The linker loads checked constructor/variant
metadata for the runtime without granting source access.

`ObjectRuntimeType.declared_fields` and `declared_generics` are the authoritative
checked storage layout for reflection and serializers. Codecs must only inspect
public fields from this layout, not infer visibility from `ObjectValue.fields`.
All standard-library native implementations can consume
`RuntimeContext.type_services` to resolve checked constructors and variant
cases, or to use explicitly registered native construction/representation
adapters; no module name is special to the import pipeline. Host codec entry
points belong to their standard-library module, not `VirtualMachine`.


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
optional host-authorized representation adapter. Validate the result's exact native type and live handle
state before exposing it, and never manufacture an FFI address from untrusted
data. Libraries consume `RuntimeContext.type_services` to access the same
VM-local type registry without codec-specific VM entry points. Source code cannot
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

A **locally declared** public object may have a field of a *privately imported*
external nominal type. Its own `ModuleObject.origin` is local, but that field's
`Item` spelling belongs to the other provider. Preserve the import's candidate
`ModuleObject` in `ModuleExports.runtime_dependencies` and preserve the local
object's `runtime_nominal_origins` map in the interface. Resolve each field
reference using the owning declaration's map **before** same-namespace lookup.
Otherwise a one-provider test can appear to work while two providers exporting
`Item` collide at runtime, particularly after a compiled facade. A recorded
origin missing from an incomplete interface must not silently match another
`Item`. Test provider swaps in incremental builds with identically shaped
objects; their semantic interface hashes must differ.

A public generic whose body contains `uninit[Private[T]]` may be fully checked
only in a consumer's call-site scope. When importing deferred function bodies,
carry private object dependencies through public facades and `.vbcm` interfaces;
register the managed object under its recorded provider-qualified origin in
that temporary scope. Do not expose it as a source-level import. The alias
registry must also store a declaration whose `name` is the alias being looked
up, not reuse an object definition whose `name` is the unqualified original:
`_resolve_imported_type_names` otherwise erases the provider from reflection
metadata and may silently merge equal-short-name private objects. Do not apply
managed-object alias rewriting indiscriminately to variant witnesses:
variant reflection has distinct runtime identity requirements. Check
`test_private_generic_construction_*` and source-free variant reflection
regressions after changing this path.

# High-rank collection and decimal resource boundaries

A list with hundreds of singleton dimensions is tiny in element count but can
exhaust Python recursion through analyser AST walks, runtime tag normalization,
cast checking, release bookkeeping or output formatting. When adding a new
per-axis runtime pass, use an explicit stack and exercise rank 350+ with
optimized and reloaded bytecode. In particular, ownership of builtin output
containers (`_retain_embedded_builtin_args`), minimum-rank checks for higher-order
calls (`_minimum_runtime_collection_rank`), and rank-typed JSON decoding each
need depth-independent traversals; an iterative vectorisation loop alone does
not protect these callers. Wide tuple/dict ASTs also need iterative
Cartesian-product generation. For decimals, normalized `BigDecimal` coefficients
allow integrality tests without expanding `10**(-exponent)`; avoid eager large
powers in other operations, and keep runtime diagnostics printable on failure.


For Python interoperability, numeric equality and hashing are inseparable:
`RuntimeNumber`'s decimal coefficient/exponent should be hashed as an exact
rational modulo `sys.hash_info.modulus`, not materialized with `to_int()` and
not hashed as a tuple. Native `float` comparison must use its exact binary
value rather than decimalizing its display string, or equal objects can have
different hashes. Complex hashes follow Python's signed-width component
combination rule. Place cheap arithmetic identities before precision inference
and expensive decimal exponent conversion, including in `**c` execution.

## Module re-exported overload sets

When importing several overload definitions under one name, the analyser may
merge their runtime payload into one `TypedFunctionNode`. Both the
`overloads` array **and** its `typ` must describe the complete public
overload set. `_selected_import_overloads` filters the overload array against
`typ`; retaining the first overload's `FunctionType` discards the rest at the
next import hop. Run `tests.test_multimodule_projects` on source and compiled
facades when changing `handlers/core.py` or module import serialization.

## Recursive compiled module names for nested imports

When `CompilationCoordinator.build_module` recursively builds dependencies,
pass the **full import path's module identity**, including package segments,
not `dependency_source.stem`. A source import such as `pkg.core` expects the
corresponding `.vbcm` to declare `pkg.core`; rebuilding it recursively as
`core` breaks mixed source/compiled imports even when ordinary source imports
work. `tests.test_multimodule_interactions` checks nested source, binary and
mixed imports through a generic object/variant contract.

## Ownership and compiled-module interaction fixtures

The project-level lifetime and concurrency contracts in
`tools/multimodule_lifetime_concurrency_contracts.py` deliberately compare
`Receive.Value` payloads to a source-derived nominal schema, including generic
arguments and fields. `tests/test_multimodule_lifetime_concurrency.py` also
checks that repeated calls on imported `@nodup` resources use two
`LOAD_VAR_BORROW` operations, rather than mistaking a normal duplicate for a
borrow. Changes to interface persistence, callable argument adaptation, or
channel transfer should rerun the nested compiled-only fixtures.

Imported destructors currently have three independent source-level expected
failures in `tests/test_multimodule_known_failures.py`: a trait-typed release,
a bounded-generic release, and nested-object field teardown. These are excluded
from positive pair coverage. When repairing module runtime linking, first
compare the same destructor with a single-file declaration, then run all
three across source and `.vbcm` dependencies and remove the expected-failure
markers only when stdout and ownership teardown actually agree.

# Imported generic reflection and facade aliases

A public facade may rename an imported generic object without adapting an
associated generic function imported from a different provider. For example,
`gateway` can re-export `core.Box as Renamed` alongside `logic.reflect` where
`reflect(x:Box[T])` was analysed against `core.Box`. The consumer's call
`reflect(Renamed(9))` currently fails static overload selection, for both
source providers and `.vbcm` dependencies. Keep this distinct from the runtime
reflection-layout/cast-spec mismatch: bytecode compilation is never reached
in that reproducer. The expected-failure tests are in
`tests/test_multimodule_known_failures.py`. A repair must reconcile selected
callable signatures and their original provider-qualified identity without
making unrelated providers' same-short-name generic objects interchangeable.

# Imported lifecycle metadata and nominal aliases

A public object can have a private `~Type` definition. The exported module
interface must retain its checked implementation for constructor lowering,
without registering it as a source-callable method. `_renamed_object` carries
that lifecycle hook across public imports; `import_behaviour_set_objects` must
also carry it when constructing a trait implementation's callable surface.
Otherwise the second constructor silently replaces the first with one that
never executes a destructor. Compare direct, source-free, and nested ownership
using `tests/test_multimodule_known_failures.py`.

For selective imports that rename a nominal type *and* an exported function
referring to that type, rewrite the imported callable contract at the import
boundary. Preserving the function's original, hidden provider spelling in its
public overload makes valid alias calls fail static resolution. Never merge
nominal identities based only on unqualified names: two packages may both
export a different `Item`. The package-alias regression is in
`tests/test_multimodule_boundary_hardening.py`.

## Provider-private trait implementation methods

When debugging `undefined name '<method>'` for a public helper that constructs
and invokes traits on a private object, check the runtime prelude's **method
source definitions** as well as `TypedObjectNode.method_bodies`. Implementations
are checked in separate `object X as Trait` blocks; their checked metadata may
already be present on the exported object, but code generation still needs the
corresponding `friendly_definitions` to emit runtime method bindings. Use the
module-neutral `provider_runtime_object` projection, not a library-specific
import hook. Runtime multimethod overloads must preserve canonical receiver
dispatch evidence; otherwise identical short names from different providers
collide. See the multi-provider regressions in
`tests/test_multimodule_alias_identity_repairs.py`.

An imported public helper can construct a public sibling without the consumer
selecting that constructor. `resolve_runtime_dependencies` collects checked
executable constructor calls from the entire provider interface (not one import
clause), because `_AnalysisPrelude.add_provider_declarations` memoizes the
provider's hidden bindings across repeated imports. The retained public object
must be **provider-qualified in the private prelude** just like a private
object, otherwise two modules' identically named constructors can collide
at destructor or trait dispatch. None of this grants the constructor a visible
source import. The source, compiled, facade, alias, and two-provider regressions
are in `tests/test_multimodule_known_failures.py`.

## Imported nominal spelling in checked casts and default arguments

A selected object can be exposed as `Path` or `Location` while another imported
signature still refers to its provider-local `path.Path`. Recording only the
selected alias in `Context.nominal_origins` makes otherwise valid calls fail
with `no overloads`; register the provider-qualified spelling as a *relation
fact* without making it a public source import. The private scope for omitted
constructor defaults must also retain provider-local variant member expressions.

Runtime match patterns are different: their bytecode type evidence must use the
actual runtime nominal spelling (`path.Path`), not the source alias (`Location`)
and not necessarily the full compiled-provider identity (`std.path.Path`).
`TypedMatchNode` and `TypedCastNode` carry the analyser's lexical alias map to
runtime lowering. Do not fix this by accepting all matching short names in the
VM: independent providers can define indistinguishable-looking `Item` types.
Run `tests.test_std_file_integration` and the alias identity module tests after
changes to import scopes, pattern lowering or nominal overload matching.

# Woken scheduler tasks and protected lease cancellation

A resumable task's blocked-wait cancellation callback is valid only until the
runner resumes from that wait. In `TaskControlBlock.run`, clear `blocked_cancel`
before `next(self.runner)` when continuing: a task can receive a FIFO exclusive
gate lease, resume its protected callback and then yield for fairness. Keeping
the old granted-waiter cancellation callback during those later yields allows
cancellation to release a lease still held by active code. Do not clear the
callback merely on `Scheduler.schedule`, since cancellation after wake but
before resumption still needs the pending wait cleaned up by the generator.
The deterministic regression is in `test_std_concurrency_state.py`.

# Callback effect tags during contextual modifier specialisation

`FunctionType.element_tags` and `Overload.generic_constraints` occupy different
fields. When using a function type as a temporary overload during contextual
modifier matching (`analysis/calls/candidates.py`), pass `element_tags=` by
keyword; a positional third argument incorrectly treats effect tags as generic
constraints, breaking callbacks that contain `try/handle` or panic effects.

- The protected-callback guard must inspect effect tags on *unqualified*
  built-ins as well as namespace-native calls. `println` and `input` have the
  `IO` element tag but no `std` namespace. Check **before** the vectorised
  dispatch branch so both paths reject effects before side effects.
- Runtime shared-value validation must traverse copied native aggregate
  fields; do not silently accept lazily evaluated collections or opaque native
  handles as immutable State snapshots.

# Protected native hooks and object-friendly intrinsic collisions

A protected callback's ban on waits cannot rely solely on `CALL_NATIVE` or
`@IO`: host-installed `BuiltinElement` wrappers can call arbitrary Python
without a VM suspension opcode. The VM records its core built-in overload
identities at initialization and rejects all other resolved native overloads
inside a protected scope, even untagged host extensions. Native reflection
factories and encoders need separate guards on their direct Python callback
paths (`construct_reflected` and `_runtime_represent_native`).

Adding a globally named intrinsic such as State's `get` can change which
object-friendly declarations are runtime-aliased under `Owner::get`. The
binding in `analysis/calls/selection.py` must agree with the selected
implementation identity, not only with its source-level signature. If a
local `get(:Foo)` overrides an imported `Foo::get`, keep the local call linked
to `get` while preserving the qualified `Foo::get`. The State and runtime
regressions exercise this distinction in both compiled execution modes.

# Resumable State modifier set evaluation

The modifier `state set: expression` compiles a zero-argument callback, not a
precomputed value. Its VM request must evaluate through ordinary resumable
activation *before* acquiring the exclusive gate, then turn the completed
replacement into the same internal `set` request as `concurrency.set(state,
value)`. When changing activation unwinding or cancellation, preserve the
precommit request until the child finishes, and preserve the computed value
when acquisition blocks. In contrast, `modify` and `modifyWith` retain the
protected no-wait policy throughout their callbacks.
