# Change playbooks

These playbooks are checklists for common maintenance tasks. They are not a
substitute for understanding the feature, but they reduce the chance of
updating one stage and forgetting another.

## Add or change syntax

1. Add or adjust token handling in `parsing/lexer.py` only when the lexical form
   changes.
2. Add or update a raw AST node in `asts/nodes.py`.
3. Export the node from `asts/__init__.py` when other packages need it.
4. Parse the form in `parsing/parser.py`, preserving source locations.
5. Update `asts/pretty.py` so `vln parse` remains useful.
6. Add exact-shape parser tests.
7. Add an analyser handler and typed representation.
8. Add compiler handling for the typed node.
9. Add runtime tests for successful behaviour and analyser tests for invalid
   forms.
10. Add a bytecode round-trip test when new instruction data is introduced.

A syntax feature is not complete when it merely parses. It must have defined
analysis, diagnostics, lowering, runtime behaviour, and inspection output.

## Add a built-in element

1. Declare the overload with `@builtin(...)` in `analysis/builtins.py`.
2. Use readable type builders and provide `param_names` when named or explicit
   calls should preserve source-level parameter names.
3. Add user-facing `ElementDocumentation` for the canonical built-in name, or
   pass `documentation=` when the metadata is generated beside the declaration.
4. Implement the runtime function with the standard `(args, ctx)` signature.
5. Decide whether the operation can panic and attach the correct element tags.
6. Add analyser tests for overload selection, including vectorised use where
   relevant.
7. Add runtime tests for values, faults, formatting, and bytecode round trips.
8. Test mixed numeric or generic overloads for ambiguity and specificity.
9. Run the reference-documentation tests and inspect generated output when the
   metadata shape changes.

Do not add importable library functionality here. Use the standard-library
registry unless the element is intentionally global language infrastructure.

## Add a standard-library element

Choose one implementation style:

- Python-only: add a function under `src/valiance/std/<module>.py` with
  `@stdlib_element(...)`.
- Valiance-only: add a public definition to `<module>.vlnc`.
- Mixed: expose private Python native hooks and call them from public Valiance
  wrappers.

Then add import and runtime tests. Verify that private native names are visible
while analysing the module itself but are not globally available to user code.
Python-backed exports must pass `documentation=` to `@stdlib_element(...)`.
Valiance-defined public exports must have a contiguous `#??` block.

## Add an AST analyser handler

1. Register the exact raw node type with `@register(NodeType)`.
2. Treat the handler as `branch + node -> BranchSet`.
3. Source stack arguments through the branch helpers rather than slicing stacks
   manually.
4. Keep variable writes branch-local.
5. Emit a typed node containing every decision code-generation will need.
6. Return diagnosed failed branches rather than throwing ordinary Python errors
   for user mistakes.
7. Add tests for success, underflow, type mismatch, and ambiguous nearby cases.

If the handler contains substantial reusable logic, move that logic into named
helpers with docstrings describing the invariant they enforce.

## Add an object, trait, or variant capability

Review both declaration-time and use-time behaviour:

- shape registration in the environment;
- field visibility and mutability;
- constructor requirements and defaults;
- object-friendly element registration;
- external-overload priority;
- qualified friendly dispatch;
- trait or variant conformance;
- widened static types versus concrete runtime members;
- import and renaming behaviour; and
- bytecode representation of constructors or dispatch plans.

Test at least one local declaration and one imported declaration. For variants,
include a value whose static type is the parent variant and whose concrete value
is a member.

## Change argument sourcing or vectorisation

Argument sourcing is shared language infrastructure. Before changing it, write
down:

- source parameter order;
- physical stack order;
- typed argument order;
- runtime call order;
- which values may broadcast;
- vectorisation stop ranks; and
- which values remain below the call on the stack.

Update the analyser's argument plan first. Store the plan on typed nodes or
resolved references, then make code-generation and the VM consume it. Avoid VM
input cycling for information that is statically determined.

Tests should include explicit variables, implicit stack use, scalar broadcast,
rugged lists, unrelated lower stack values, and bytecode serialization.

## Add or change bytecode

1. Add or modify the opcode/record in `runtime/bytecode.py`.
2. Emit it from `runtime/compiler.py` using typed information.
3. Execute it in `runtime/vm.py`.
4. Encode and decode it in `runtime/serialization.py`.
5. Bump the bytecode magic/version when compatibility is broken.
6. Test direct execution and `loads(dumps(program))` execution.
7. Test malformed or unsupported bytecode where the serializer has validation.

Never use Python `pickle`, `repr`, or implementation-specific object identity as
part of the bytecode format.

## Add a runtime value kind

Define the value and then audit all shared behaviours:

- runtime type names and cast matching;
- display and diagnostic formatting;
- equality and hashing;
- tags and collection ranks;
- copying, ownership, retain/release, and destructors;
- indexing or field access if applicable;
- constants and serialization; and
- interaction with vectorisation.

A value that prints correctly in one built-in but incorrectly in the CLI is a
sign that formatting logic has been duplicated instead of shared.

## Add a CLI command

1. Parse command arguments in `main.py`.
2. Keep orchestration in `main.py`; reusable compiler work belongs in its own
   subsystem.
3. Reuse project entry and file-resolution helpers.
4. Render failures through the diagnostics layer.
5. Add command tests in `tests/test_main.py`.
6. Update `README.md` and the relevant user guide.

Terminal presentation belongs in `repl.py`; compiler state belongs in
`_ReplSession` and the normal compiler objects.

## Change source tooling

For `tidy`, documentation generation, or test discovery:

- use the normal lexer/parser/analyser;
- preserve source text that the tool does not own;
- render inferred type variables with valid source syntax rather than leaking
  analyser-local parameter names;
- preserve named generic clauses and their constraints;
- test Windows and POSIX path handling;
- test inline source and project mode separately; and
- make output deterministic and idempotent so generated files produce stable
  diffs.
