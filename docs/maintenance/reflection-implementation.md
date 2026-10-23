# Reflection implementation status

Updated: 2026-10-23. This tracks the implementation against the prototype stages
in **Valiance Reflection — Language Design Specification v1.0 (Naming-Finalized
Candidate)**. The specification describes the target; the table below describes
what the compiler currently supports. Stages overlap, so a later stage having
some infrastructure does not mean earlier stages are complete.

| Spec stage | Status | Implemented scope and remaining work |
| --- | --- | --- |
| R0: type and field metadata | Implemented for concrete owners | Exact owner and field type evidence, generic arguments, declaration order, visibility, and field identity. `fields[T]` and `$value fields` enumerate declared storage; concrete types without storage produce empty collections. Runtime-generic `T` remains deferred work. |
| R1: heterogeneous `foreach` | Metadata iteration implemented | Each declared field gets a separately checked callback with its actual `Type[T]` evidence. Generic helpers receiving that evidence, overload selection, nested loops, index parameters, shared bindings, and break/return are supported. Reflective reads of owner values are still needed to complete value-based callback examples. |
| R2: CSTC over reflective outputs | Partial | Concrete iteration checks every specialization and conservatively combines escaping transformed results. Empty layouts skip callback instantiation. Deferring generic-owner reflection to concrete call sites, nested propagation of that requirement, and field-value result inference are pending. |
| R3: dynamic selection, reads and writes | Lookup implemented; value access pending | String indexing always performs dynamic metadata lookup; missing names raise `KeyFault`. `field?` returns an optional descriptor. `owner[field]`, union-typed reads, checked reconstruction, descriptor-directed conversions, `read?`/`write?`, and their faults/errors are pending. |
| R4: permission and ownership integrity | Metadata foundation implemented | Lexical `unsafe` and conservative transitive `@nodup` checks gate metadata operations. Descriptors carry no authority or owner instance. Metadata uses managed ownership. Reflective value-access visibility checks, failed-update ownership contracts, and the unrecoverable `DuplicationFault` contract still require integration and validation. |
| R5: managed `Uninit[T]` | Pending | Incomplete storage, dynamic initialization, readiness, `finished?`, `finish`, `finish?`, and cleanup of partially initialized resources. |
| R6: broader categories and `std.json` | Pending | Category-specific reflection and the end-to-end serialization proof. Empty `fields[T]` for non-storage concrete types does not imply those category-specific APIs exist. |

## Supported metadata surface

`Fields[Owner]` is heterogeneous, distinct from ordinary lists. Descriptors expose
read-only `name`, `index`, `owner`, `type`, and `visibility`; `Visibility.name`
exposes the existing accessibility category. Outside an opened callback, `.type`
has conservative union evidence. Within a concrete callback, it retains that
field's actual type through the existing structural refinement machinery.

Unresolved runtime-generic enumeration and iteration produce explicit diagnostics;
they are not silently treated as empty collections. No public erased reflection
type or extra `Field` type argument is introduced. Metadata enumeration includes
private fields, but value access and visibility bypass are not implemented yet.

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
  portable layout and callback payloads. Current bytecode version is **0x37**;
  older compiled artifacts require recompilation.

## Validation and next steps

Regression coverage is maintained in the existing type, parser, analyser,
control-flow, runtime, and bytecode serialization test modules. Runtime reflection
tests exercise optimized and unoptimized code both directly and after bytecode
round trips. Tests cover heterogeneous evidence, invalid specializations, empty
owners, nested loops, escaping results and descriptor lifetime. Serialization
tests cover interface round trips and malformed callback contracts.

Last implementation validation (2026-10-23): **1,432 tests passed**, covering the
ordinary runtime and reflection runtime classes, analyser/call/expression/
declaration/control-flow/state suites, parser, AST formatting, types, optimizer,
bytecode serialization, reference documentation, docstring coverage, and
`tests/test_programs.py`. Native Windows FFI test classes were excluded locally,
as required by the project instructions. This is validation of the implemented
scope, not a claim that all specification acceptance targets pass.

The next integration work is reflective owner reads and their visibility/identity
checks, followed by generic-owner CSTC and complete escaping result inference.
Checked writes and explicit conversions must preserve the same evidence and
failure precedence before incomplete construction is added. None of these pending
surfaces should be inferred to work from the metadata APIs alone.
