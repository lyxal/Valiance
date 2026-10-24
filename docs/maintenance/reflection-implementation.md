# Reflection implementation status

Updated: 2026-10-25. This tracks the implementation against the prototype stages
in **Valiance Reflection — Language Design Specification v1.0 (Naming-Finalized
Candidate)**. The specification describes the target; the table below describes
what the compiler currently supports. Stages overlap, so a later stage having
some infrastructure does not mean earlier stages are complete.

| Spec stage | Status | Implemented scope and remaining work |
| --- | --- | --- |
| R0: type and field metadata | Concrete owners, generic call sites and finite runtime unions implemented | Exact owner and field type evidence, generic arguments, declaration order, visibility, and field identity. Value-based enumeration selects a checked layout for finite distinguishable unions. Type-based enumeration describes the requested type's own storage. Open trait, variant and structural receiver shape selection remains pending. |
| R1: heterogeneous `foreach` | Concrete and finite-union metadata iteration implemented | Each possible declared field gets a separately checked callback with its actual `Type[T]` evidence and exact owner-read result. Runtime selection invokes only the actual layout's callbacks. Generic helpers, overload selection, nested loops, index parameters, shared bindings, and break/return are supported. |
| R2: CSTC over reflective outputs | Generic owner specialization implemented | Generic metadata, reads and iteration defer to concrete call sites, including through nonrecursive generic helpers and function values. Checked executable bodies retain layouts and exact callback types; escaping transformed results combine conservatively. Empty layouts skip callback instantiation. Inferred recursive reflection is unsupported. |
| R3: dynamic selection, reads and writes | Lookup, direct access and fallible access implemented | String indexing always performs dynamic metadata lookup; missing names raise `KeyFault`. `field?` returns an optional descriptor. Direct and fallible reads retain exact callback types and conservative dynamic unions. Direct writes and non-consuming `write?` use checked destination compatibility without invoking conversions. `read?` and `write?` return ordinary Results with named reflection errors. Explicit descriptor-directed `to` and `to?` conversions use checked ordinary providers and exact runtime target selection. |
| R4: permission and ownership integrity | Direct and fallible access contracts implemented | Lexical `unsafe` and conservative transitive `@nodup` checks gate metadata and value access. Descriptors carry no authority or owner instance. Access validates identity before visibility; writes check types last. Fallible writes preserve the original owner on success and failure and discard rejected replacements normally. Eager and lazy fallible outputs retain managed ownership. `DuplicationFault` bypasses handlers and testing helpers, including across task observation, aggregation and cancellation cleanup. |
| R5: managed `Uninit[T]` | Implemented for concrete managed object layouts and generic call sites | Empty storage, checked dynamic initialization and replacement, non-consuming `finished?`, consuming `finish`/`finish?`, and cleanup of initialized fields. All construction operations require lexical `unsafe`. Native/opaque storage remains unsupported. |
| R6: broader categories and `std.json` | Pending | Category-specific reflection and the end-to-end serialization proof. Empty `fields[T]` for non-storage concrete types does not imply those category-specific APIs exist. |

## Supported metadata surface

`Fields[Owner]` is heterogeneous, distinct from ordinary lists. Descriptors expose
read-only `name`, `index`, `owner`, `type`, and `visibility`; `Visibility.name`
exposes the existing accessibility category. Outside an opened callback, `.type`
has conservative union evidence. Within a concrete callback, it retains that
field's actual type through the existing structural refinement machinery.

Generic-owner operations require concrete evidence at their call sites. Value-based
enumeration over `A | B` selects between checked `Fields[A]` and `Fields[B]` layouts,
including empty alternatives. In contrast, `fields[A | B]` is empty because a union
type itself declares no storage. All possible callbacks must check, even if a
particular call's runtime value selects only one layout. Open-ended or ambiguous
runtime owner selection produces diagnostics rather than empty collections. No public erased reflection
type or extra `Field` type argument is introduced. Metadata enumeration includes
private fields. Direct reads and writes enforce ordinary visibility unless
the lexical operation has owner-scope authority or lexical `unsafe`; descriptors grant none.
Readable fields permit external reads but require that authority for writes.

Ordinary union overload dispatch does not yet retain multiple checked generic
reflection bodies. Such calls are diagnosed rather than executing an unresolved
template. In particular, passing `Field[A] | Field[B]` to a helper requiring one
invariant `Field[T]` may require separate instantiations; invoking that helper
inside reflective `foreach` supplies the concrete owner evidence. Generic helpers
whose ordinary argument solving accepts the union use the implemented shared
specialization path.

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
  portable layout, callback, direct/fallible access, construction and specialization payloads. Current bytecode version is **0x3E**;
  older compiled artifacts require recompilation.

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

The next integration work is variant and open-category owner-shape selection, broader category
reflection and the `std.json` serialization proof. These operations must preserve
the same evidence, failure precedence and ownership rules. Pending surfaces should not be inferred
to work from the implemented metadata and access APIs alone.
