# Maintaining Valiance

This section is the starting point for people changing the Valiance compiler,
runtime, command-line tools, or standard library. It explains how the pieces fit
together, where a change belongs, and how to verify that a local fix has not
broken a different stage of the language.

Valiance has several moving parts, but most changes follow the same pipeline:

```text
source text
  -> lexer
  -> parser and raw AST
  -> analyser and typed AST
  -> bytecode compiler
  -> serializer or in-memory program
  -> virtual machine
  -> runtime values and output
```

The most important maintenance rule is to preserve that separation. The parser
should describe syntax, the analyser should decide meaning and types, the
compiler should lower typed decisions, and the VM should execute the resulting
plan. When the VM has to rediscover information that the analyser already knew,
behaviour becomes harder to reason about and static guarantees become weaker.

## Read these first

- [Architecture tour](architecture.md) explains the major packages and the
  ownership boundaries between them.
- [Change playbooks](change-playbooks.md) gives step-by-step recipes for common
  work such as adding syntax, an element, a runtime value, or a CLI command.
- [Testing and debugging](testing-and-debugging.md) explains the test layers,
  useful inspection commands, and a practical fault-isolation workflow.
- [Docstring policy](docstrings.md) explains what production docstrings should
  contain and how coverage is enforced.
- [Reference documentation](reference-documentation.md) explains the metadata,
  validation, and generators for built-ins and standard-library functions.

The deeper subsystem references remain useful after this overview:

- [Lexer and parser guide](../Compiler%20Documentation/lexer-parser-guide.md)
- [Analysis and type-system guide](../Compiler%20Documentation/analysis-type-system-guide.md)
- [Runtime and code-generation guide](../Compiler%20Documentation/runtime-codegen-guide.md)

## Working principles

### Keep decisions in the earliest correct stage

A stage should make the decisions for which it has enough information:

- The lexer identifies tokens and source locations.
- The parser resolves grammatical ambiguity and lowers chain syntax.
- The analyser resolves names, access, overloads, vectorisation, types, and
  diagnostics.
- The compiler turns typed nodes into explicit bytecode instructions.
- The VM performs runtime-only work such as executing instructions, inspecting
  concrete values for authorised dynamic dispatch, and raising runtime faults.

Do not move work later simply because it is convenient to implement there. A
runtime workaround for a statically knowable ordering or binding rule usually
creates a second, subtly different implementation of the language.

### Treat typed AST as the compiler contract

`compile_program(...)` accepts typed nodes. Raw AST should not bypass analysis.
Typed nodes carry selected overloads, argument order, vectorisation depth,
member-access decisions, and other information needed for deterministic
code-generation.

### Keep branch-local facts out of the global environment

The analyser's `Environment` stores declarations and relationships that are
stable across control-flow paths. `AnalysisBranch` and `BranchVariables` store
facts that can differ between paths, such as stack types, local variables,
errors, warnings, and inferred inputs.

### Change serialization deliberately

Bytecode is a file format, not merely an implementation detail. When an opcode,
instruction payload, or `FunctionCode` record changes incompatibly, update the
serializer and bump the bytecode magic/version marker. Add a round-trip test so
an in-memory success cannot hide a serialization failure.

### Prefer one source of truth

Static and runtime built-in catalogues are generated from the same declarations
in `analysis/builtins.py`. Standard-library native hooks are declared through
`stdlib_native.py`. New features should extend these existing registries rather
than adding parallel lookup tables.

## Before opening a change

1. Reduce the problem to the smallest Valiance program that demonstrates it.
2. Decide which stage first has enough information to enforce the intended
   behaviour.
3. Add a focused test at that stage and, for user-visible behaviour, a runtime
   test as well.
4. Implement the fix without weakening unrelated diagnostics or dispatch rules.
5. Run the focused test, the fundamental-program tests, and then the complete
   suite.
6. For bytecode-affecting work, include an explicit serialization round trip.

## Definition of done

A maintenance change is complete when:

- the intended source program works;
- invalid nearby programs fail with a useful diagnostic;
- typed and runtime behaviour agree;
- bytecode round trips still work when relevant;
- production functions introduced or changed have useful docstrings;
- the complete test suite passes; and
- the maintenance guides are updated when an architectural boundary changes.
