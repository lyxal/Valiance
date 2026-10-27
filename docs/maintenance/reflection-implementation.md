# Reflection implementation status

Updated: 2026-10-27. This tracks the implementation against the prototype stages
in **Valiance Reflection — Language Design Specification v1.0 (Naming-Finalized
Candidate)**. The specification describes the target; the table below describes
what the compiler currently supports. Stages overlap, so a later stage having
some infrastructure does not mean earlier stages are complete.

| Spec stage | Status | Implemented scope and remaining work |
| --- | --- | --- |
| R0: type and field metadata | Concrete and erased managed owners implemented | Exact owner and field type evidence, generic arguments, declaration order, visibility, and field identity. Value-based enumeration selects a checked layout for finite distinguishable unions and active variant members. Type-based enumeration describes the requested type's own storage. Erased nominal/anonymous traits and structural receivers select from a discovered finite compiled owner universe. Anonymous record literals retain complete, reified storage shapes. |
| R1: heterogeneous `foreach` | Concrete, finite-union and closed-variant metadata iteration implemented | Each possible declared field gets a separately checked callback with its actual `Type[T]` evidence and exact owner-read result. Runtime selection invokes only the actual layout's callbacks. Generic helpers, overload selection, nested loops, index parameters, shared bindings, and break/return are supported. |
| R2: CSTC over reflective outputs | Concrete call-site specialization implemented | Generic metadata, reads and iteration defer to concrete call sites, including through helpers, trait and structural parameters, and function values. Explicitly typed `@recursive` helpers reuse their checked specialization through `this`. Checked executable bodies retain layouts and exact callback types; escaping transformed results combine conservatively. Empty layouts skip callback instantiation. Recursive signatures cannot be inferred. |
| R3: dynamic selection, reads and writes | Lookup, direct access and fallible access implemented | String indexing always performs dynamic metadata lookup; missing names raise `KeyFault`. `field?` returns an optional descriptor. Direct and fallible reads retain exact callback types and conservative dynamic unions. Direct writes and non-consuming `write?` use checked destination compatibility without invoking conversions. `read?` and `write?` return ordinary Results with named reflection errors. Explicit descriptor-directed `to` and `to?` conversions use checked ordinary providers and exact runtime target selection. |
| R4: permission and ownership integrity | Direct and fallible access contracts implemented | Lexical `unsafe` and conservative transitive `@nodup` checks gate metadata and value access. Descriptors carry no authority or owner instance. Access validates identity before visibility; writes check types last. Fallible writes preserve the original owner on success and failure and discard rejected replacements normally. Eager and lazy fallible outputs retain managed ownership. `DuplicationFault` bypasses handlers and testing helpers, including across task observation, aggregation and cancellation cleanup. |
| R5: managed and native `Uninit[T]` | Implemented for concrete managed storage, compiler-visible runtime-registered schemas, and native factory finalization | Empty managed storage, checked dynamic initialization and replacement, non-consuming `finished?`, consuming `finish`/`finish?`, and cleanup of initialized fields. `uninit[&T]` represents *opaque empty* storage, completed only by a VM-registered trusted native factory; failed native construction returns `ConstructionError` or raises `NativeConstructionFault`. All construction operations require lexical `unsafe`. |
| R6: broader categories and `std.json` | Category metadata, runtime schema registration and typed JSON decoding implemented | `typeInfo[T]`, `alternatives[T]`, and `cases[T]` expose exact category evidence for compound types, collections, functions, tasks, tags, enums and resources. `std.json` composes checked object, optional and collection encoders for recursive schemas. Strict parsing and typed decoding reconstruct public storage from compiler-checked constructor layouts. |

## Supported metadata surface

`Fields[Owner]` is heterogeneous, distinct from ordinary lists. Descriptors expose
read-only `name`, `index`, `owner`, `type`, and `visibility`; `Visibility.name`
exposes the existing accessibility category. Outside an opened callback, `.type`
has conservative union evidence. Within a concrete callback, it retains that
field's actual type through the existing structural refinement machinery.

Deferred helpers retain explicit return contracts, including multiple results,
in their published overloads and callable types. Each concrete specialization
checks its body against that contract; deferral does not infer or weaken an
explicit return annotation.

Generic-owner operations require concrete evidence at their call sites. Value-based
enumeration over `A | B` selects between checked `Fields[A]` and `Fields[B]` layouts,
including empty alternatives. In contrast, `fields[A | B]` is empty because a union
type itself declares no storage. All possible callbacks must check, even if a
particular call's runtime value selects only one layout. Unresolved or ambiguous
runtime owner selection produces diagnostics rather than empty collections. No public erased reflection
type or extra `Field` type argument is introduced. Metadata enumeration includes
private fields. Direct reads and writes enforce ordinary visibility unless
the lexical operation has owner-scope authority or lexical `unsafe`; descriptors grant none.
Readable fields permit external reads but require that authority for writes.

Value-based enumeration of a closed variant selects its active member's storage.
Generic arguments are retained on member owner evidence; unions containing
variants flatten to distinct concrete member layouts. Empty members have empty
layouts. `fields[Variant]` remains empty because the parent declares no storage.
The transitive ownership gate checks every member, including recursive members.
Imported variants restore the same member shapes from compiled interfaces.

Helpers with nominal or anonymous trait parameters can defer value enumeration
until callers supply concrete owner evidence. Ordinary conformance is checked
before specializing the body. Concrete inputs retain their field evidence through
nonrecursive helper chains, named and implicit calls, function values, and
vectorization. Each scalar invocation is checked against its actual input type;
static inputs broadcast and lazy calls retain the definition's closure.

Structural row views of concrete nominal owners enumerate the owner's declared
storage, including private fields; row requirements do not add physical fields.
Descriptors identify the nominal storage owner, so ordinary access, visibility
and reconstruction apply. Abstract rows and erased traits select among the concrete shapes discovered by
ordinary checking. Anonymous record literals carry complete `RecordType` evidence
and a runtime storage signature; row requirements alone do not declare storage. Type-based queries
continue to describe only the requested type's own declared storage.

Intersections containing finite concrete owner evidence select from those checked
layouts. Remaining nominal and trait constraints filter incompatible owners;
unions and closed variants can supply several candidates, including empty
layouts. Occurrence tags and row requirements constrain values without adding
storage. Their ordinary type contracts remain in force, while descriptors retain
the selected storage owner's identity and declaration types. Type-based
`fields[A & View]` stays empty because the intersection declares no storage.

The ownership proof follows the selected layouts and retains conservative checks
on generic arguments and row field evidence. Release effects follow the same
selected owners, so excluded layouts do not contribute cleanup effects. An
intersection of open traits uses the same finite compiled universe. The VM never
discovers or instantiates an unchecked callback.

Union overload dispatch retains a checked specialization for each possible input
combination. A helper accepting invariant `Field[T]` can receive
`Field[A] | Field[B]` without erasing owner evidence. Every selected body must
check; runtime predicates select its executable specialization. Named calls,
function values, nested helpers and vectorized calls preserve definition closures,
lexical permissions and branch-specific static inputs. Lazy results retain their
specialized closures until traversal completes. Helpers whose ordinary argument
solving accepts the union use the shared specialization path.

## Implementation map

- [Type-system reflection guide](type-system.md#reflection-metadata): descriptor
  model, transitive safety query, lexical permission and specialization rules.
- [Compiler descriptors](../../src/valiance/vtypes/reflection.py) and
  [loop analysis](../../src/valiance/analysis/control_flow/loop_handlers.py):
  exact evidence and per-field callback checking.
- [Runtime descriptors](../../src/valiance/runtime/reflection.py),
  [compiler](../../src/valiance/runtime/compiler.py), and
  [VM](../../src/valiance/runtime/vm.py): managed metadata and execution.
- [Bytecode guide](../Compiler%20Documentation/runtime-codegen-guide.md#bytecode-files):
  portable layout, callback, direct/fallible access, construction and specialization payloads. Current bytecode version is **0x42**, with analysed interface schema **11**;
  older compiled artifacts require recompilation.

## JSON encoding

`std.json.encode(value)` returns `Result[String, ValueError]` and accepts
JSON-compatible values without a field callback: strings, exact real numbers,
Booleans, absence and present optionals, finite lists, String-keyed dictionaries,
and managed objects and records. Complex numbers return `ValueError`; numeric
formatting never rounds through a host float. Strings use JSON escaping,
including Unicode and control codes. The automatic encoder
recursively visits the public and readable fields of nested objects, preserving
declaration order while excluding private values. The same entry point accepts
primitive values, String-keyed dictionaries, finite eager or lazy lists, and
optional values containing JSON-compatible scalar and collection data. Exact numeric
formatting and tag-aware Boolean encoding are retained through collection
nesting. The result is still `Result[String, ValueError]`, including for unsupported
values, complex numbers, and protected owners. Managed objects stored inside
lists, dictionaries, optionals or other managed objects are automatically
serialized to arbitrary nesting depth. Constructor-checked runtime metadata
preserves the declaring field order, visibility and type information even
when object values appear inside collection storage. The native traversal
only reads public and readable fields from that metadata and refuses managed
values whose checked metadata is unavailable. Finite lazy lists are consumed
on demand (and replayable across encodings); explicitly infinite lists return
`ValueError`. Since an untagged lazy iterator cannot generally be proven finite,
only pass known-terminating lazy collections or limit them with `take` first.
`take` marks its output as non-infinite, including when its source is infinite.

```valiance
import {std.json}
object Person =>
  public $name: String
  private $token: String
  public $active: #boolean Int
end
json.encode(Person("Ada", "secret", true)) | ?!
# {"name":"Ada","active":true}
```

`std.json.decode[T](text)` returns `Result[T, ValueError]`, decoding strict JSON
into an explicitly selected Valiance type. The decoder supports strings, exact
numbers, Boolean tags, records, eager lists (including multidimensional lists),
String-keyed dictionaries, optionals, and generic managed objects recursively.
Only checked runtime constructors may be used; field names and values must
match their declared types. Public and readable fields are decoded, private
fields are filled only from declared defaults, and arbitrary extra fields or
ambiguous duplicate JSON keys are rejected. Missing required fields, invalid
number formats, wrong JSON shapes, and unsupported target types return
`ValueError` with a path indicating the failure. Union variants are selected
only when their constructor field layouts uniquely identify one case; ambiguous
cases return `ValueError`. Explicit object initializer methods are not invoked:
those objects are rejected rather than bypassing constructor invariants. Array
values are materialized eagerly (JSON does not preserve lazy evaluation plans).

```valiance
import {std.json}
object Person =>
  public $name: String
  public $age: Int
  private $token: String = ""
end
$person = json.decode[Person]("{\"name\":\"Ada\",\"age\":37}") ?!
json.encode($person) ?!
# {"name":"Ada","age":37}
```

## Host-defined types and native construction through general reflection

The `VirtualMachine` provides a **general runtime reflection API**; these
operations do not require JSON parsing or serialisation. The host registers a
managed type schema, then constructs and introspects instances:

```python
from valiance.runtime import VirtualMachine

vm = VirtualMachine()
vm.register_reflection_type(
    "ExternalBox",
    (("value", "T"), ("secret", "String", "private")),
    generics=("T",), defaults={"secret": "from-host"},
)
value = vm.construct_reflected("ExternalBox[Int]", {"value": 42})
assert vm.reflected_read(value, "value") == 42
assert [field.fields["name"] for field in vm.reflected_fields(value).fields["#fields"]] == ["value", "secret"]
updated = vm.reflected_write(value, "value", 43)  # Non-mutating replacement.
assert vm.reflected_read(value, "value") == 42
assert vm.reflected_read(updated, "value") == 43
```

This general reflection construction checks concrete field types, reified generic
arguments, required fields and defaults. `reflected_fields` returns the ordinary
`Fields`/`Field` runtime descriptors with declared types, visibility and order.
`reflected_read` denies private slots without explicit `unsafe=True`;
`reflected_write` also gates readable slots and uses the VM's normal
reconstruction/duplication checks. The embedding application is the authority
that can choose `unsafe=True` for private field construction and access. This
Python host flag is **not** Valiance's lexical `unsafe` language construct.

Native values use a trusted general reflection factory, with optional JSON
representation support:

```python
from valiance.runtime.runtime_values import FFIHandleValue

vm.register_reflection_native(
    "&Widget",
    construct=lambda data: FFIHandleValue("&Widget", trusted_create(data["key"])),
    encode=lambda handle: {"key": trusted_lookup(handle)},
)
handle = vm.construct_reflected("&Widget", {"key": "abc"})
```

The application supplies `trusted_create` and `trusted_lookup`: neither is a
Valiance built-in. The VM verifies that the native constructor returns precisely
the requested FFI type and that an opaque handle is live and not pending
release. A factory must establish native ownership and destructor contracts;
reflection **never** fabricates a pointer from arbitrary bytes. Copied native
scalars and structs may also be constructed through registered factories.
All registrations are VM-local and are not serialized into bytecode.

Registered native types can also be constructed from Valiance source through
lexically unsafe, typed incomplete storage. Native types have no reflected
fields: `finished?` reports structural completeness, while `finish?` asks the
registered factory for a value and returns `ConstructionError` if it is missing,
raises an exception, or returns an invalid/released handle. `finish` raises the
recoverable `NativeConstructionFault` on factory failure. The factory receives
`None` when invoked by `finish`/`finish?`; embedding applications requiring
constructor arguments can supply those through the host's
`construct_reflected("&Widget", data)` method.

```valiance
unsafe =>
  $builder = uninit[&Widget]
  $widget = finish? $builder ?!
end
```

The VM checks that the native result has **exactly** the declared FFI type,
not merely an assignable value, and that opaque handles are live. Bytecode
validation rejects any declared native field layout or native constructor
metadata injected into a construction payload.

**Compiler-visible registered schemas:** When a host registers a managed type
before compiling source, `vm.reflection_environment()` creates a checked
analyser environment with that schema's fields, generics, and visibility.
The host must compile with `Analyser(env=vm.reflection_environment())` and
execute the result in the same VM. Source code can then use `fields[T]`,
`uninit[T]`, reflective field access, and `finish` for those types:

```python
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program

vm.register_reflection_type("ExternalBox", (("value", "Int"),))
source = '''unsafe =>
  $box = uninit[ExternalBox]
  $box[fields[ExternalBox]["value"]] = 42
  $complete = finish $box
  $complete.value
end'''
analyser = Analyser(env=vm.reflection_environment())
typed = analyser.analyse(parse(source))
assert not analyser.diagnostics
assert vm.run(compile_program(typed)) == [42]
```

This is a closed snapshot, not runtime compiler mutation: registering a schema
**after** compilation does not rewrite checked static types or compiled
specializations. Without a schema snapshot, newly created types remain
inspectable through the VM's host reflection APIs, not named source-level types.

**JSON is a consumer of this shared reflection registry**, not its owner.
`std.json.decode[T](...)` uses registered native factories when the requested
`&T` is compiler-known; the host `decode_json` and `encode_json` functions
consume runtime-defined schema metadata. The earlier
`register_json_type`/`register_json_native` names delegate to these same general
reflection registrations.

```python
box = vm.decode_json('{"value":42}', "ExternalBox[Int]")
assert vm.encode_json(box) == '{"value":42}'
```

**Boundary:** Compiler-visible runtime-registered types require a schema snapshot
before static analysis; source cannot mint a new statically named type during
execution. Registration never causes unchecked bytecode specialization or
fabrication of native pointers. A source-native `uninit[&T]` requires a no-arg
registered factory at finalization; native constructors with arguments are
available through the general host reflection API.

Application schemas compose `encodeObject`, `encodeList`, `encodeDictionary`
and `encodeOptional` with unary callbacks returning `Result[String, ValueError]`.
Callbacks produce complete JSON values. `encodeObject` emits public and readable
fields in declaration order and skips private values. Every declared field type
must still satisfy the callback's checked signature, including private fields.
Protected owners and invalid object categories return `ValueError`. Lexical
unsafe inside the library's storage traversal grants no authority to callbacks
and does not bypass ordinary duplication or cleanup checks.

Recursive codecs use ordinary explicit signatures and `@recursive`:

```valiance
import {std.json}
object Node => public $next: Node? end
@recursive
define encodeNode(value: Node) -> Result[String, ValueError] =>
  $recur = $this
  json.encodeObject($value, fn(child: Node?) -> Result[String, ValueError] =>
    json.encodeOptional($child, $recur)
  end)
end
encodeNode(Node(Some(Node(\None)))) | ?!
```

The same composition supports recursive variants containing lists of their
parent type. Child errors return from the entire encoding operation without
publishing partial JSON. Source-free standard-library builds retain native
formatting hooks and each union branch's defining closure. Parsing and typed
decoding remain separate work; there is no decoding placeholder API.

## Managed construction

`unsafe => uninit[Owner] end` allocates empty `Uninit[Owner]` storage without
running initializers or inserting field defaults. The zero-argument element uses
ordinary bare call syntax; empty `()` argument lists are not part of Valiance's
call syntax. Builders accept checked descriptor assignments using `Field[Owner]`;
they do not expose the incomplete owner as an ordinary value or field receiver.

`finished?` borrows the builder and tests declared storage completeness only.
`finish` and `finish?` consume its construction state on success and failure,
including through shared references to that state. Later readiness, assignment
or finalization raises `StateFault`. `finish?` returns `Result[Owner,
ConstructionError]`; failure exposes `.reason = "MissingFields"`, `.message`,
and `.missing: String*` in declaration order. Direct `finish` raises
`IncompleteConstructionFault` instead. Cleanup failures remain ordinary panics
and integrity failures remain unrecoverable.

Successful finalization transfers initialized fields and restores the owner's
lifecycle metadata. Abandonment and failed finalization release initialized
fields in reverse declaration order without running the owner's destructor or
must-call contract. Replacements retain ordinary immutable reconstruction and
duplication checks; readiness remains set after replacement. Partial storage is
an isolated resource at task boundaries. Deferred imported helpers retain their
provider's private declarations for body checking without exposing those names
as caller imports.

## Validation and next steps

Regression coverage is maintained in the existing type, parser, analyser,
control-flow, runtime, and bytecode serialization test modules. Runtime reflection
tests exercise optimized and unoptimized code both directly and after bytecode
round trips. Tests cover heterogeneous evidence, invalid specializations, empty
owners, nested loops, escaping results and descriptor lifetime. Serialization
tests cover interface round trips and malformed callback contracts.

Implementation validation (2026-10-24) covers the
ordinary runtime and reflection runtime classes, analyser/call/expression/
declaration/control-flow/state suites, parser, AST formatting, types, optimizer,
bytecode serialization, structural types/analysis, compiled modules,
compilation database, incremental semantics, reference documentation, docstring coverage, and
`tests/test_programs.py`. Coverage includes direct and generic writes, nested and
augmented updates, alias preservation, cast and permission failures, discarded
replacement cleanup, protected owners, and source-free module specialization.
Integrity coverage includes named and catch-all handlers, reflected protected
field materialization, testing helpers, task waits, scope and grouped-wait fault
priority, and cancellation cleanup.
Fallible-access coverage includes named reasons, failure precedence, normal
replacement cleanup, original-owner preservation, implicit cycle arguments,
generic and source-free specialization, nested vectorisation and lazy-result cleanup.
Conversion coverage includes exact union targets, opened fields, optional and
missing targets, evaluation order, protected sources, lexical access checks, and
source-free generic specialization.
Construction coverage (2026-10-25) includes empty and generic owners, initializer
and default bypass, readiness, replacement, dynamic cast failures, lexical
permission checks, consumed-state rejection, protected child transfer, partial
cleanup, and source-free private owner declarations. It also checks namespaced
owner identity and lifecycle parity with ordinary construction, interface and
bytecode round trips, malformed construction payloads, and valid reflection
payload generation in the serialization, optimizer and runtime-bytecode fuzz
targets.
Finite-union coverage includes descriptor lookup, conservative reads and writes,
per-layout visibility and failure precedence, protected owner borrowing, temporary
cleanup panics, nested loops, shared state, break and return, escaped conversion
evidence, qualified owner identity, source-free generic helpers, and malformed
dispatch tables. Serialization, optimizer and runtime-bytecode fuzz generators
include the finite-choice payloads.
Native Windows FFI test classes were excluded locally,
as required by the project instructions. This is validation of the implemented
scope, not a claim that all specification acceptance targets pass.

Closed-variant coverage includes generic member evidence, empty members, unions
containing variants, exact callback checking for inactive members, direct and
fallible reads/writes, identity and visibility failure precedence, recursive
ownership checks, and source-free namespaced generic specialization. Runtime
cases run optimized and unoptimized, directly and after bytecode round trips.

Open-parameter specialization coverage includes nominal and anonymous trait
conformance, structural nominal rows, implicit cycle inputs, homogeneous and
heterogeneous nested vectors, static-input broadcasting, lazy closure lifetimes,
protected owner borrowing and cleanup, lexical permissions, source-free helpers,
erased receivers, and rejection of invalid callbacks.

Intersection coverage includes finite union filtering, generic trait arguments,
generic variant members and parent variance,
empty layouts, exact callbacks, direct and fallible updates, private-access
precedence, owner mismatch failures, protected layouts, cleanup panics and
source-free helper specialization. It also checks compositional ownership proofs,
release effects, union-constituent subtyping and preservation of checked return
expressions during branch refinement.

Erased-owner discovery runs in isolated analysis state until the reachable type
set stabilizes, then the authoritative pass checks every selected callback.
Provisional failures cannot prevent discovery of independent later value origins.
Diagnostics, traces and source-query observations come from the authoritative
pass. Discovery has a 64-pass compiler resource limit; nonconvergence is diagnosed.
Generic and source-free call-site templates participate in discovery. This is a
closed compiled universe, not runtime type-definition discovery.

Category metadata regression coverage includes generic evidence, union and variant
children, record declaration order and reconstruction, optional payloads, list
ranks, dictionary keys/values, callable signatures/effects, task outputs, enum
names, tag facts and resource annotations. Recipes, complete record shapes and
checked operations survive analysed-interface and optimized/unoptimized bytecode
round trips. Malformed recipes and layouts are rejected before execution.

Lexical ordinary and reflective `foreach` callbacks propagate `?` failures to their
enclosing source function, including through nested loops. Iteration retains
element-level tag evidence from collection types. Both behaviors are covered
with optimized/unoptimized execution and bytecode round trips.

The JSON encoding proof covers public/readable field policy, recursive codecs,
primitive and container values, error propagation and source-free modules.
Explicitly typed recursive reflection is supported through `this`,
including reflective callbacks, captured closures, finite union dispatch and
source-free helpers. Invalid specialized match branches reject the whole body.
General reflection now supports runtime-registered managed storage, checked metadata and construction, source-level native builders completed by trusted factories, and a compiler schema snapshot for registered managed types. JSON reuses the registry. Raw native pointer fabrication, late mutation of already compiled static types, and source-level runtime type declarations remain disallowed.
