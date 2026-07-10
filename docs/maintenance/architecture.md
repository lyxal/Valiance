# Architecture tour

This guide describes the Valiance implementation from the point of view of a
maintainer. It focuses on responsibilities and data flow rather than listing
every class.

## End-to-end flow

A normal compile-and-run operation follows these stages:

1. `parsing/lexer.py` turns source characters into tokens with source locations.
2. `parsing/parser.py` turns tokens into raw nodes from `asts/nodes.py`.
3. `analysis/analyser.py` transforms branch sets and emits typed AST nodes.
4. `runtime/compiler.py` lowers typed nodes into instructions and function code.
5. `runtime/serialization.py` optionally writes or reads portable bytecode.
6. `runtime/vm.py` executes instructions using values from `runtime_values.py`.
7. `main.py` presents diagnostics, output, and command behaviour to the user.

The same core pipeline is reused by project commands and the REPL. The REPL
keeps analysis state, globals, and stack values alive between entries, but it
must not implement a second compiler path.

## Syntax layer

### Lexer

`src/valiance/parsing/lexer.py` owns token boundaries, comments, string escapes,
numeric forms, delimiters, and source coordinates. It should not decide type or
overload meaning.

A lexer change normally needs:

- token-kind or scanning changes;
- focused tests in `tests/test_parser.py`; and
- checks for incomplete input because the REPL highlighter must remain tolerant.

### Parser

`src/valiance/parsing/parser.py` owns grammar and raw AST shape. Valiance source
uses left-to-right chains whose execution order is lowered for the stack model,
so source order and AST order are not always identical.

The parser should encode syntax precisely enough that later stages do not need
to reconstruct punctuation or source ordering. It should not resolve overloads,
member visibility, or inferred types.

### AST

`src/valiance/asts/nodes.py` contains both raw nodes and typed wrappers. Raw nodes
represent parsed source. Typed nodes record the analyser's decisions and are the
input to code-generation.

When a node gains a field, check all of these places:

- parser construction;
- pretty-printing in `asts/pretty.py`;
- analyser handling;
- typed wrappers;
- compiler handling;
- serialization payloads, if the field reaches bytecode; and
- tests that compare AST values directly.

## Analysis and type system

### Branch-based analysis

`src/valiance/analysis/analyser.py` models analysis as a transformation from a
set of possible branches to another set. A branch contains:

- the type stack;
- inferred or explicit function inputs;
- branch-local variables;
- typed nodes emitted so far;
- element and data-tag effects;
- break information; and
- diagnostics.

Handlers should return new branches rather than mutating existing ones. Branch
joins should happen only where control flow actually converges.

### Environment and context

`src/valiance/types/environment.py` stores global declarations: element
overloads, objects, traits, variants, tags, attributes, and constructors.
`src/valiance/types/context.py` stores type-relationship facts used by relation
checks.

Local variable facts belong to `BranchVariables`. Putting them in the global
environment would make overload candidates and control-flow branches leak into
one another.

### Type relations

`src/valiance/types/relations.py` owns assignability, generic solving,
vectorisation, overload application, specificity, union dispatch, and related
operations. `types/builders.py` owns constructors, normalisation, and readable
type formatting.

When a type-system problem appears, first identify whether it is:

- a malformed or insufficient type representation;
- an incorrect relation such as assignability or specificity;
- incorrect argument sourcing in the analyser; or
- a lost typed decision between analysis and code-generation.

Do not patch runtime value matching until those static paths have been checked.

### Built-ins and standard library

`analysis/builtins.py` is the shared static/runtime catalogue for globally
available built-ins. Each declaration combines an analyser-visible signature
with a runtime implementation and human-facing documentation metadata.

Importable standard-library elements live under `src/valiance/std` and are
registered through `stdlib_native.py`. A native stdlib helper is not a global
built-in merely because its implementation is written in Python.

`documentation.py` defines renderer-neutral metadata for built-ins and native
stdlib functions. `reference_docs.py` combines that metadata with `#??` blocks
from Valiance-defined stdlib modules, validates completeness, and renders HTML,
Markdown, or versioned JSON. Documentation remains outside bytecode and does not
participate in overload identity or runtime dispatch.

## Code-generation and bytecode

`src/valiance/runtime/compiler.py` consumes typed AST and emits instructions.
It should follow decisions already stored on typed nodes, including overload
slots, explicit argument order, vectorisation stops, qualified object-friendly
dispatch, and tag validators.

`src/valiance/runtime/bytecode.py` defines instruction and function records.
`runtime/serialization.py` defines the portable representation. These files
form a compatibility boundary: changing a record without updating serialization
can make in-memory tests pass while saved bytecode fails.

## Runtime

Start with [Understanding the runtime and code generator](runtime-system.md)
for a traced, human-first explanation of typed-AST lowering, bytecode records,
frames, calls, vectorisation, ownership, panics, and serialization.

### Virtual machine

`src/valiance/runtime/vm.py` owns frames, stacks, locals, globals, function
calls, bytecode execution, runtime vectorisation, panic handling, object
construction, indexing, and authorised dynamic dispatch.

The VM may inspect concrete runtime values when the typed plan explicitly calls
for it, such as union multidispatch. It should not guess parameter order, recover
undefined names, or choose among ordinary overloads that analysis already
resolved.

### Runtime values

`src/valiance/runtime_values.py` defines values shared across the VM, built-ins,
and CLI formatting. Keep equality, ownership, tags, ranks, and display rules in
shared helpers so different output paths do not disagree.

Object values are reconstructed for visible updates rather than mutated in
place. When adding a value kind, review:

- equality and hashing expectations;
- retain/release ownership rules;
- runtime type naming;
- formatting and diagnostics;
- serialization, if stored in constants; and
- collection-rank or tag behaviour.

## Modules, packages, and tools

`modules.py` analyses imports and moves public declarations and type facts
between module environments. `packages.py` handles project manifests,
dependencies, entries, and installation paths.

`source_tools.py` powers source rewriting and generated reference docs.
`testing.py` discovers and executes Valiance-language tests. Both use the normal
parser and analyser and should remain consumers of the compiler rather than
alternative implementations.

## User interface

`main.py` owns command selection and orchestration. `repl.py` owns terminal
presentation. The REPL frontends return source to the same persistent session;
they should not parse, analyse, or execute independently.

Diagnostics are assembled in `diagnostics.py`. Prefer structured diagnostic
information from the stage that detects the error, then render it at the user
boundary.
