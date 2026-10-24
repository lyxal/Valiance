# Reflection implementation status

Updated: 2026-10-24. This tracks the implementation against the prototype stages
in **Valiance Reflection — Language Design Specification v1.0 (Naming-Finalized
Candidate)**. The specification describes the target; the table below describes
what the compiler currently supports. Stages overlap, so a later stage having
some infrastructure does not mean earlier stages are complete.

| Spec stage | Status | Implemented scope and remaining work |
| --- | --- | --- |
| R0: type and field metadata | Implemented for statically concrete owners and generic call sites | Exact owner and field type evidence, generic arguments, declaration order, visibility, and field identity. `fields[T]` and `$value fields` enumerate declared storage; types without storage produce empty collections. Generic owners specialize when a call supplies concrete evidence. Runtime owner-shape selection remains pending. |
| R1: heterogeneous `foreach` | Concrete metadata and value iteration implemented | Each declared field gets a separately checked callback with its actual `Type[T]` evidence and exact owner-read result. Generic helpers receiving that evidence, overload selection, nested loops, index parameters, shared bindings, and break/return are supported. |
| R2: CSTC over reflective outputs | Generic owner specialization implemented | Generic metadata, reads and iteration defer to concrete call sites, including through nonrecursive generic helpers and function values. Checked executable bodies retain layouts and exact callback types; escaping transformed results combine conservatively. Empty layouts skip callback instantiation. Inferred recursive reflection is unsupported. |
| R3: dynamic selection, reads and writes | Lookup, direct access and fallible access implemented | String indexing always performs dynamic metadata lookup; missing names raise `KeyFault`. `field?` returns an optional descriptor. Direct and fallible reads retain exact callback types and conservative dynamic unions. Direct writes and non-consuming `write?` use checked destination compatibility without invoking conversions. `read?` and `write?` return ordinary Results with named reflection errors. Explicit descriptor-directed `to` and `to?` conversions use checked ordinary providers and exact runtime target selection. |
| R4: permission and ownership integrity | Direct and fallible access contracts implemented | Lexical `unsafe` and conservative transitive `@nodup` checks gate metadata and value access. Descriptors carry no authority or owner instance. Access validates identity before visibility; writes check types last. Fallible writes preserve the original owner on success and failure and discard rejected replacements normally. Eager and lazy fallible outputs retain managed ownership. `DuplicationFault` bypasses handlers and testing helpers, including across task observation, aggregation and cancellation cleanup. |
| R5: managed `Uninit[T]` | Pending | Incomplete storage, dynamic initialization, readiness, `finished?`, `finish`, `finish?`, and cleanup of partially initialized resources. |
| R6: broader categories and `std.json` | Pending | Category-specific reflection and the end-to-end serialization proof. Empty `fields[T]` for non-storage concrete types does not imply those category-specific APIs exist. |

## Supported metadata surface

`Fields[Owner]` is heterogeneous, distinct from ordinary lists. Descriptors expose
read-only `name`, `index`, `owner`, `type`, and `visibility`; `Visibility.name`
exposes the existing accessibility category. Outside an opened callback, `.type`
has conservative union evidence. Within a concrete callback, it retains that
field's actual type through the existing structural refinement machinery.

Generic-owner operations require concrete evidence at their call sites; unresolved
runtime owner selection produces diagnostics rather than empty collections. No public erased reflection
type or extra `Field` type argument is introduced. Metadata enumeration includes
private fields. Direct reads and writes enforce ordinary visibility unless
the lexical operation has owner-scope authority or lexical `unsafe`; descriptors grant none.
Readable fields permit external reads but require that authority for writes.

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
  portable layout, callback, direct/fallible access and specialization payloads. Current bytecode version is **0x3C**;
  older compiled artifacts require recompilation.

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
Native Windows FFI test classes were excluded locally,
as required by the project instructions. This is validation of the implemented
scope, not a claim that all specification acceptance targets pass.

The next integration work is incomplete construction. These operations must preserve the same evidence,
failure precedence and ownership rules. Pending surfaces should not be inferred
to work from the implemented metadata and access APIs alone.
