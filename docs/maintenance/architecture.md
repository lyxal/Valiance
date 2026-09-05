# Architecture tour

This guide describes the Valiance implementation from the point of view of a
maintainer. It focuses on responsibilities and data flow rather than listing
every class.

## End-to-end flow

A normal compile-and-run operation follows these stages:

1. `parsing/lexer.py` turns source characters into tokens with source locations.
2. `parsing/parser.py` turns tokens into raw nodes from `asts/nodes.py`.
3. `analysis/state` owns immutable branch and variable transformations; `analysis/analyser.py` orchestrates branch sets and emits typed AST nodes.
4. `runtime/compiler.py` lowers typed nodes into instructions and function code.
5. `runtime/optimizer.py` runs ordered, semantics-preserving bytecode passes.
6. `runtime/serialization.py` optionally writes or reads portable bytecode.
7. `runtime/vm.py` executes instructions using values from `runtime_values.py`.
8. `main.py` presents diagnostics, output, and command behaviour to the user.

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

The `src/valiance/analysis/state` package provides analyser-independent `BranchVariables`, `AnalysisBranch`, and `BranchSet` values. The `src/valiance/analysis/expressions` package owns assignment, field, index, interpolation, and literal handlers. The `src/valiance/analysis/contracts` package owns annotations, tags, where clauses, object lifecycle rules, and tag handlers. The `src/valiance/analysis/control_flow` package owns match analysis, exhaustiveness, try handling, loops, unfolding, and branch-producing block handlers. The `src/valiance/analysis/calls` package owns argument sourcing, candidate construction, overload selection, vectorisation, callable dispatch, and extension planning. The `src/valiance/analysis/declarations` package owns function, object, trait, variant, enum, constructor, friendly-definition, and import registration. The `src/valiance/analysis/analyser.py` façade and its private
`_analyser_*` implementation modules model analysis as a transformation from a
set of possible branches to another set. The façade owns branch state, public
entry points, and the `Analyser` orchestration class; handlers and focused helper
families live in sibling modules. A branch contains:

- the type stack;
- inferred or explicit function inputs;
- branch-local variables;
- typed nodes emitted so far;
- element and data-tag effects;
- break information; and
- diagnostics.

Remaining cross-domain AST handlers live in `analysis/handlers/core.py`. They should
return new branches rather than mutating existing ones. Branch joins should
happen only where control flow actually converges. Function typing, call
resolution, pattern/control-flow logic, and shared refinement helpers live in
the corresponding `calls/` modules,
`control_flow/` and `support/analysis_utils.py` modules.

Non-fatal source-pattern advice is recorded both as rendered lint text and as
structured rewrite metadata. Detection belongs in the registry-driven
`analysis/lints/` package rather than concrete analyser handlers. Built-in rule
modules are discovered automatically, and the analyser only exposes generic
block, node, and validated-match lifecycle hooks. The current optimiser is a
separate bytecode pass pipeline; future typed rewrites may consume proven
analysis facts without coupling diagnostics to compilation. See
[lints-and-rewrites.md](lints-and-rewrites.md).

### Environment and context

`src/valiance/vtypes/environment.py` stores global declarations: element
overloads, objects, traits, variants, tags, attributes, and constructors.
`src/valiance/vtypes/context.py` stores type-relationship facts used by relation
checks.

Local variable facts belong to `BranchVariables`. Putting them in the global
environment would make overload candidates and control-flow branches leak into
one another.

### Type relations

`src/valiance/vtypes/relations.py` owns assignability, generic solving,
vectorisation, overload application, specificity, union dispatch, and related
operations. `vtypes/builders.py` owns constructors, normalisation, and readable
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
`runtime/optimizer.py` owns the extensible post-codegen pass pipeline. Its
independent default passes recursively optimise nested function payloads,
materialise proven cycle inputs, fold constants, inline small constant functions,
simplify bytecode and stack shuffles, remove unreachable instructions and
redundant jumps, and retarget absolute control-flow addresses. Add independent
passes through `OptimizationPipeline` rather than hiding rewrites in code
generation or VM execution.

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

`src/valiance/runtime/runtime_values.py` defines values shared across the VM, built-ins,
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
between module environments. An import is resolved during analysis. Nested
structure bodies use lexical child environments so imported names do not escape
their body, while required runtime declarations are collected into a shared,
deduplicated program prelude. Module exports carry that prelude transitively so
an exported definition containing a local relative import still works when
consumed by another module. `packages.py` handles project manifests,
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

#### Semantic and implementation invalidation

Compiled module artifacts carry separate fingerprints for the semantic facts
consumed by importers and for the executable bytecode contributed at runtime.
The semantic fingerprint projects `ModuleExports` onto declaration contracts,
object and trait shapes, tags, overlays, re-exports, and implementation facts;
function bodies are excluded when their declared contract is unchanged. The
implementation fingerprint hashes the canonical serialized `Program` together
with compiler and optimization compatibility inputs.

The incremental coordinator uses semantic fingerprints to decide whether an
importer must be analysed again. Executable target records additionally retain
reachable implementation fingerprints, so a body-only dependency edit relinks
the target without treating the dependency's unchanged public contract as a
semantic change. The persisted interface bytes also have an independent content
hash for corruption detection; that integrity digest is not an invalidation key.

#### Transactional incremental artifact store

Project builds publish generated cache objects beneath `.vln/incremental/`.
Immutable module and executable artifacts are addressed by SHA-256 under the
sharded `objects/` tree. Small `modules` and `targets` indexes map stable build
identities to those object hashes. Configured outputs remain in their normal
locations and can be restored from a verified object when deleted.

Writers validate bytes before publication, flush file contents, and replace
objects, indexes, and final outputs atomically. A project writer lock serializes
index and object publication while readers continue to read previously
published immutable files. A failed parse, analysis, compilation, validation,
or replacement leaves the previous index and output intact. Corrupt objects
are rejected and rebuilt when source is available. Reachability-based garbage
collection retains every object referenced by current module or target indexes.

#### Cross-module declaration-first cycles

Module loading no longer breaks recursive imports by returning an empty
`ModuleExports`. The loader tracks the active module stack and, on re-entry,
publishes provisional interfaces containing complete public definition
contracts. Bodies are analysed only after those contracts are visible, matching
the declaration-first rule used within one source module. Definitions whose
parameters or returns require inference are rejected at the cycle boundary with
a diagnostic that names the cycle and incomplete declarations.

`incremental/graph.py` discovers source-backed imports through
`ModuleLoader.resolve(...)` and computes deterministic Tarjan strongly connected
components. The coordinator avoids recursively rebuilding an already active
component member, while artifact publication ensures each completed artifact is
validated and atomically exposed. Source-free cycle re-entry requires a valid
compiled semantic interface and never substitutes an empty interface.

##### Explicit analysed-interface schema

Compiled module interfaces no longer use Python pickle. The `.vbcm` interface
section begins with the `VLNI` interface marker and contains canonical UTF-8
records with explicit tags for compiler dataclasses, enums, tuples, immutable
sets, dictionaries, decimals, and primitive values. Decoding uses a closed
registry of AST, type-system, environment, symbol, and module-export records;
it never imports a class named by untrusted module bytes or executes a reduction
hook.

The module container format is version 4 and the interface ABI is version 2.
Semantic hashes use the same canonical encoder while replacing source locations
with null, so declaration coordinates remain available in the persisted full
interface but do not invalidate importers. Unordered sets and dictionaries are
sorted by canonical encoded keys, while tuple, field, definition, and overload
order remains significant.
