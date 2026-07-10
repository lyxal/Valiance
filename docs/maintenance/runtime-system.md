# Understanding Valiance's runtime and code generator

This is the human-first guide to Valiance code generation, bytecode, and the
virtual machine. It is written for maintainers who understand the broad idea of
a compiler and a stack machine but do not yet feel confident following a typed
node all the way to a runtime value.

The most important reassurance is this:

> The runtime is not a second analyser. Code generation records the decisions
> analysis already made, and the VM carries out that recorded plan.

Most runtime complexity comes from a small interpreter supporting several
language features at once: stack functions, explicit parameters, overloads,
closures, vectorisation, lazy lists, objects, panics, tags, and lifecycle rules.
Each mechanism is understandable once its boundary is separated from the
others.

Use this guide before the exhaustive
[runtime and code-generation reference](../Compiler%20Documentation/runtime-codegen-guide.md).
The reference is the detailed implementation contract. This guide explains the
mental model, the main APIs, and how to debug the pipeline without treating the
VM as a black box.

## How to use this guide

You do not need to read it all at once.

- For the first mental model, read **The sixty-second model**, **The bytecode
  data model**, and **One VM frame at a time**.
- For a wrong-result or wrong-stack bug, read **How code generation lowers typed
  nodes**, **The physical and conceptual stacks**, and **Tracing one program**.
- For overload or vectorisation bugs, read **Resolved calls**, **Authorised
  runtime dispatch**, and **Runtime vectorisation**.
- For object, closure, or cleanup bugs, read **Runtime values and ownership**.
- For saved-bytecode problems, read **Serialization is a compatibility
  boundary**.
- For implementation work, keep **The API decision table**, **Fault isolation**,
  and **A final mental checklist** nearby.

### Contents

1. [The sixty-second model](#the-sixty-second-model)
2. [A map of the implementation](#a-map-of-the-implementation)
3. [The bytecode data model](#the-bytecode-data-model)
4. [How code generation lowers typed nodes](#how-code-generation-lowers-typed-nodes)
5. [Resolved calls and dispatch](#resolved-calls-are-the-main-compiler-vm-contract)
6. [Frames, stacks, parameters, and closures](#one-vm-frame-at-a-time)
7. [The interpreter loop](#the-interpreter-loop-is-a-small-state-machine)
8. [Control flow, panics, and patterns](#control-flow-is-jumps-plus-small-runtime-signals)
9. [Vectorisation and collections](#runtime-vectorisation-executes-a-static-plan)
10. [Values, objects, and ownership](#runtime-values-and-ownership)
11. [Serialization](#serialization-is-a-compatibility-boundary)
12. [Tracing, APIs, debugging, and tests](#tracing-one-program-end-to-end)

## The sixty-second model

A normal run has four runtime-facing stages:

```text
typed AST
   |
   | compile_program(...)
   v
Program(FunctionCode(...))
   |
   | optional dumps(...) / loads(...)
   v
portable bytecode representation
   |
   | VirtualMachine.run(...)
   v
final Valiance stack and side effects
```

The analyser has already decided:

- which element overload won;
- the order in which explicit or named arguments map to parameters;
- whether a call vectorises;
- how deeply each argument vectorises;
- static rank values from `where` clauses;
- whether runtime union or multimethod dispatch is permitted;
- return tags and known collection ranks; and
- which object-friendly or external element implementation is selected.

Code generation converts those decisions into explicit records and opcodes. The
VM should not infer them again.

At runtime, the VM repeatedly performs one simple step:

```text
instruction = code.instructions[ip]
execute instruction against the current frame
advance or replace ip
```

A frame contains a physical value stack, locals, globals, input-cycling state,
panic handlers, and ownership bookkeeping. The VM stores resumable `_Activation`
records in an explicit Python list. User-function calls push an activation and
returns pop it, so Valiance recursion does not consume Python recursion depth.
A cached direct-leaf check lets functions containing only ownership-trivial
resolved built-ins execute without entering the scheduler; any function that may
call user code still uses the activation stack.

The runtime still performs genuinely dynamic work where values matter:

- reading and writing concrete values;
- following jumps;
- constructing objects and collections;
- checking a checked cast;
- matching runtime patterns;
- selecting a branch from an analyser-produced union dispatch plan;
- selecting a `multi` specialisation when the typed call authorises it;
- traversing eager or lazy lists according to a compiled vectorisation plan;
- handling panics; and
- retaining, releasing, and cleaning up runtime values.

A useful rule is:

```text
If the answer depends only on types or source structure, analysis/codegen owns it.
If the answer depends on the concrete runtime value, the VM may own it.
```

## A map of the implementation

### `runtime/bytecode.py`: the execution vocabulary

This module defines immutable records used between code generation,
serialization, and execution:

- `OpCode`
- `Instruction`
- `ResolvedElementReference`
- `FunctionCode`
- `FunctionSetCode`
- constructor and vector-extension references
- `Program`

These records describe an execution plan. They contain almost no execution
policy.

### `runtime/compiler.py`: typed AST to bytecode

`compile_program(...)` accepts only analysed `TypedNode` values. `_Compiler`
walks those nodes, emits instructions, compiles nested functions, and patches
jump targets.

This file should translate decisions, not invent new semantic ones. When
compiler code needs to ask a type question that analysis should already have
answered, first check whether a typed-node field is missing.

### `runtime/vm.py`: bytecode execution

`VirtualMachine` owns:

- runtime globals;
- frame creation;
- the instruction loop;
- function and built-in calls;
- runtime-authorised dispatch;
- vectorisation;
- panic handling;
- indexing and field operations;
- match execution;
- object lifecycle and ownership; and
- runtime diagnostics.

The file is large because it contains many operations, but the central loop is
still a straightforward opcode dispatch.

### `runtime/serialization.py`: portable bytecode

This module converts `Program` records to and from a versioned binary format. It
owns the byte representation, value tags, opcode numbers, validation, and the
magic/version marker.

### `runtime_values.py`: shared value semantics

This module defines values used by both the VM and built-ins:

- `LazyList` and `ListValue`;
- `TaggedValue`;
- `ObjectValue` and `ObjectRuntimeType`;
- `PanicSignal`;
- list/rank helpers; and
- user-visible formatting.

Putting these rules in one place keeps `println`, diagnostics, built-ins, and
the VM from disagreeing about what a value is.

### `analysis/builtins.py`: shared static/runtime built-ins

A built-in declaration pairs a static `Overload` with a Python runtime
implementation. `RuntimeContext` gives implementations controlled access to
output, callable invocation, formatting, selected overload invocation, and
static values recorded by codegen.

There is deliberately one built-in registry rather than separate analyser and
VM registries.

### `stdlib_native.py`: Python-backed imported functions

Native standard-library functions use the same runtime implementation shape as
built-ins, but they remain module-qualified and importable. The analyser sees a
typed wrapper; the VM sees a qualified `BuiltinElement` in its runtime globals.

## The bytecode data model

### `Instruction` is an opcode plus a payload

```python
Instruction(OpCode.PUSH_CONST, Decimal("5"))
Instruction(OpCode.LOAD_VAR, "x")
Instruction(OpCode.JUMP_IF_FALSE, 17)
```

The payload is deliberately generic at the dataclass level because different
opcodes need different records. The compiler, serializer, and VM together define
the payload contract for each opcode.

When adding or changing an opcode, update all three places and add a round-trip
test. An in-memory execution test cannot prove the serializer understands the
new payload.

### `FunctionCode` is the unit of execution

A `FunctionCode` contains:

- an instruction tuple;
- runtime parameter names;
- an optional diagnostic name;
- whether explicit parameters participate in input cycling;
- element tags;
- recursion and `multi` flags;
- nominal runtime dispatch hints;
- declared return data tags; and
- declared exact return collection ranks.

The top-level program is also a `FunctionCode`, named `<main>`, wrapped in a
`Program`.

### `FunctionSetCode` preserves overload bodies

A source definition may analyse to several overload bodies. Codegen compiles
each one to `FunctionCode` and stores them in `FunctionSetCode`.

The set may also contain a `dispatch_plan`. That plan does not ask the VM to
solve overloads. It tells the VM which already-selected overload index belongs
to each reified runtime branch of a union input.

### References carry structured payloads

Complex instructions use named dataclasses rather than positional tuples. For
example, `ResolvedElementReference` carries:

- `name`: runtime lookup name;
- `overload_index`: selected slot;
- `vectorised`: whether to map rather than call once;
- `vectorised_depths`: per-argument recursive mapping depth;
- `vectorised_target_ranks`: exact runtime rank boundaries;
- `return_collection_ranks`: rank evidence to reattach;
- `type_args`: concrete generic object/variant arguments;
- `static_values`: hidden values such as solved rank variables;
- `arity_override` and `consumed_override`: call-site checked stack contracts;
- `multidispatch`: permission to select a `multi` specialisation; and
- `extension`: compiled unequal-length vector extension behaviour.

Named fields matter because this record crosses compiler, serializer, and VM
boundaries. A positional tuple would be easy to misread and hard to evolve.

## How code generation lowers typed nodes

`compile_program(nodes)` first verifies that every node is typed. It then calls
`_Compiler.compile_function(...)` for the top-level body.

The compiler is mostly a large pattern match in `_Compiler.node(...)`:

```text
NumberLiteralNode       -> PUSH_CONST
GetVariableNode         -> LOAD_VAR
SetVariableNode         -> STORE_VAR
FunctionNode            -> MAKE_FUNCTION
ListLiteralNode         -> item code, then BUILD_LIST
FieldAccessNode         -> GET_FIELD
IfNode                  -> condition, jumps, patched targets
TypedElementNode        -> usually CALL_RESOLVED_ELEMENT
```

That direct relationship is the easiest way to read codegen: find the AST node,
then inspect the emitted opcode sequence.

### Compilation is recursive

Nested source functions become nested `FunctionCode` or `FunctionSetCode`
objects stored inside `MAKE_FUNCTION`, constructor, loop, unfold, guard, or
extension payloads.

The VM materialises those code records into closures only when execution reaches
the instruction.

### Emission and patching

Forward jump targets are not known when the first instruction is emitted.
Codegen therefore uses a simple patching pattern:

```python
jump_to_else = emit(JUMP_IF_FALSE, None)
compile_then_branch()
jump_to_end = emit(JUMP, None)
patch(jump_to_else, current_instruction_index())
compile_else_branch()
patch(jump_to_end, current_instruction_index())
```

`if`, `assert`, `match`, `try`, `while`, and `break` all use variations of this
mechanism.

The compiler is not building a control-flow graph. It emits a linear instruction
stream and fills in integer instruction pointers.

### Typed metadata changes lowering

The raw AST says that an element was written. The typed wrapper says what that
call means.

Examples:

- `TypedElementNode.overload_index` chooses a runtime slot.
- `call_arg_order` causes a `STACK_SHUFFLE` before the call.
- `AppliedOverload.vectorised_depths` are copied to the resolved reference.
- a typed `?` call becomes `TRY_UNWRAP`, because it must return from the current
  frame rather than merely invoke a built-in function;
- `TypedAtNode` becomes a function value plus a resolved `call` carrying stop
  ranks and the analysed body-overload index; and
- typed literal items are compiled instead of their raw equivalents so nested
  constructor type arguments are not lost; and
- `TypedMatchNode` and `TypedForNode` retain analysed child bodies so resolved
  calls inside cases and loop bodies remain `CALL_RESOLVED_ELEMENT` operations.

Control-flow bodies can produce different analysis branches. The analyser keeps
child metadata only when every surviving branch agrees on the typed suffix; it
otherwise falls back to raw child nodes. This conservative fallback preserves
correctness while preventing stable recursive and loop bodies from repeating
runtime overload search on every execution.

When a codegen bug appears, compare the typed node to the emitted instruction.
The compiler should be a faithful projection of that data.

### Function metadata is compiled once

For each analysed overload, `_compile_function_overload(...)` records:

- parameter names, including hidden static parameters;
- whether it is `multi`;
- dispatch type names;
- return tags; and
- return ranks.

This lets runtime invocation remain mechanical. The VM does not reopen the AST
or call the type solver.

## Resolved calls are the main compiler-VM contract

The normal source element path is:

```text
TypedElementNode
  -> ResolvedElementReference
  -> CALL_RESOLVED_ELEMENT
  -> direct runtime slot invocation
```

The VM loads `reference.name`, examines what kind of runtime value it names, and
uses `reference.overload_index` directly.

For a built-in, the slot indexes `BuiltinElement.definitions`.

For a user-defined overload set, the slot indexes
`OverloadedFunctionValue.overloads`.

For a constructor, the slot selects the initializer overload and `type_args`
are attached to the new `ObjectValue`.

This is the practical meaning of “overload resolution is static”: the VM is
given an address inside the selected runtime definition.

### Dynamic `CALL` is a different operation

When codegen does not have a statically resolved element reference, it emits:

```text
LOAD_ELEMENT name
CALL
```

`CALL` pops a callable value from the stack and dispatches by runtime callable
kind:

- `BuiltinValue`;
- `FunctionValue`;
- `ObjectConstructorValue`; or
- a single-overload `OverloadedFunctionValue`.

A multi-overload function without a resolved slot is rejected on this path. That
prevents the VM from silently inventing an ordinary overload choice.

Dynamic calls are needed for first-class function values and some generated
wrappers. They are not the preferred lowering for an ordinary analysed element
call.

### `RuntimeContext.call` is the controlled callback path

Built-ins such as mapping, folding, and optional chaining need to call function
values supplied as arguments. `RuntimeContext` exposes:

- `call(value, args)`;
- `call_overload(value, args, index)`;
- output;
- formatting; and
- `static_values`.

`VirtualMachine.call_value(...)` handles first-class callable values. It may use
an analyser-produced union dispatch plan, an exact runtime dispatch signature,
or vectorisation where the callable value itself is the dynamic input.

This path should not be confused with source element resolution. It exists
because a built-in has received an actual callable value and must invoke it.

## Authorised runtime dispatch

A blanket rule that “the VM never dispatches” would be too strong. It performs
three narrow kinds of dispatch whose permission and search space are compiled
in advance.

### Static union dispatch plans

When analysis adapts an overload set to a union-typed callable, it emits
`UnionDispatchBranch` records. Each record contains runtime type patterns and one
overload index.

The VM matches concrete arguments against those patterns and calls the recorded
index. It does not rank overloads or choose a more specific return type.

### Multimethod dispatch

A resolved call may set `multidispatch=True`. The selected slot is the analysed
fallback, and the VM may replace it with a compatible `multi` specialisation
whose runtime object types match.

Without that flag, no multimethod search occurs.

### Runtime checks inside a selected implementation

Some facts cannot be represented by a static signature, such as whether a list
is non-empty or which value case a result object currently holds. A selected
built-in may inspect those facts.

That is validation or value-case behaviour, not overload resolution.

## One VM activation at a time

`VirtualMachine.call(...)` creates a root `_Activation` containing a `_Frame` and
an instruction pointer. `_drive_frames(...)` repeatedly runs the top activation
until it returns or requests another user-function call. Every function still
has its own physical stack, but nested calls are represented by activation
records instead of nested Python `execute()` calls.

A frame contains:

| Field | Purpose |
|---|---|
| `stack` | Physical runtime values produced inside this frame |
| `locals` | Parameters, local assignments, and isolated captures |
| `globals` | Built-ins, stdlib hooks, definitions, and captured environment |
| `cycle_values` | Explicit input values available to stack-style body operations |
| `cycle_index` | Next cyclic input position |
| `cycle_stack_remaining` | Initial conceptual inputs not yet consumed |
| `panic_handlers` | Active try-handler targets and protected stack depths |
| `cycle_scopes` | Saved input-cycling state for nested match/cycle scopes |
| `retained_locals` | Locals whose ownership must be released with the frame |

A call does not hand the caller's list object to the callee as a shared stack.
The callee executes independently and returns its final stack as a list of
results.

### Functions that accept proved caller-stack inputs

Most function calls remain isolated. A call-site checked body can be compiled
with `FunctionCode.accepts_stack_inputs` when analysis has proved that its
concrete callable argument requires an additional suffix from the caller stack.
User-defined `dip` is the canonical case. The VM transfers only the statically
calculated number of values; this is not an open-ended shared-stack mode.

`FunctionCode.param_collection_ranks` records exact parameter ranks when known.
Together with `accepts_stack_inputs`, this metadata affects argument sourcing and
therefore belongs to the serialized bytecode compatibility boundary.

### Parameters have two runtime views

Explicit parameters are available as named locals:

```valiance
define addFive(x: Number) => $x + 5
```

They are also available to stack-style operations through the conceptual input
cycle:

```valiance
define double(:Number) => +
```

The second body needs two operands even though the function has one input. The
input value is initially available once, then cycles, so `+` receives the same
value twice.

This dual view is why `FunctionCode` has `cycle_params` and `_Frame` has cycle
state. It is a runtime implementation of Valiance's stack-function semantics,
not runtime type inference.

## The physical and conceptual stacks

`_Frame.source_args(arity)` is the central argument-sourcing operation.

It considers three sources, in order:

1. values already on the physical frame stack;
2. initial explicit input values that have not yet been conceptually consumed;
3. cyclic reuse of explicit input values when more operands are required.

It returns:

```text
(arguments,
 number_of_physical_stack_values_consumed,
 next_cycle_index,
 next_cycle_stack_remaining)
```

The caller commits those bookkeeping values only after sourcing succeeds.

This API prevents every opcode and call helper from reimplementing parameter
cycling. It also preserves argument order: the right side of the Python list is
the top of the Valiance stack, while `_pop_many` and `source_args` return values
in the left-to-right parameter order expected by implementations.

### What input cycling should not do

Input cycling should supply values for an already known arity. It should not:

- decide which overload applies;
- reorder named arguments;
- guess a missing variable binding;
- infer vectorisation depths; or
- search an arbitrary number of inputs.

Those are static decisions. If a VM bug seems to require cycling changes, first
inspect the compiled arity, selected overload, and typed argument order.

## Closures and globals

`MAKE_FUNCTION` calls `_make_function_value(...)`. The resulting closure stores:

- its `FunctionCode`;
- a snapshot-like globals dictionary containing visible globals and locals;
- the names of captured locals whose ownership it retains; and
- a reference count.

Each invocation isolates captured local values into frame locals. Assigning to a
captured name during one call does not mutate the closure's captured template for
future calls.

Recursive code binds `this` to the closure. Named definitions are also rebound
when stored so self-reference works after assignment.

At top level, `STORE_VAR` publishes definitions into VM globals so later
instructions and retained REPL state can find them. Closures created directly in
the main frame read those bindings as globals rather than retaining a second
lexical-local ownership copy. Nested functions still isolate genuine enclosing
function locals on every call, so assignments to captured locals preserve their
existing non-persistent call semantics.

## The interpreter loop is a small state machine

`VirtualMachine.execute(...)` creates the root activation. `_run_activation(...)`
repeatedly matches on its current opcode until the activation returns or suspends
at a user-function call. The caller's instruction pointer and pending call are
stored on `_Activation`, so the driver can resume it without a Python call stack.

Most opcodes fall into a few families.

### Stack and name operations

- `PUSH_CONST`
- `LOAD_VAR`
- `LOAD_VAR_BORROW`
- `STORE_VAR`
- `LOAD_ELEMENT`
- `POP`
- `STACK_SHUFFLE`
- `SOURCE_ARGS`

These move values while applying retain/release rules. `LOAD_VAR_BORROW` is
emitted only for the root variable of a field or indexed assignment that stores
back to the same binding. It places a non-owning receiver wrapper on the stack;
ordinary reads continue to use `LOAD_VAR` and retain ownership normally. Plain
immutable scalar values and containers already classified as ownership-trivial
take a direct stack path because they cannot own runtime resources. Closures,
objects, tagged payloads, lazy values, and containers with lifecycle-bearing
contents still use the full ownership helpers. Frame cleanup iterates locals
once and clears the map after releases rather than snapshotting and deleting
every entry.

### Construction operations

- `MAKE_FUNCTION`
- `BUILD_LIST`
- `BUILD_STRING`
- `BUILD_TUPLE`
- `BUILD_RECORD`
- `BUILD_DICT`
- `MAKE_OBJECT_CONSTRUCTOR`
- `MAKE_ENUM_MEMBER`

Collection builders pop a known count and construct one value. `BUILD_LIST` may
also attach an analysed exact runtime rank.

### Access and update operations

- `GET_FIELD` / `SET_FIELD`
- `GET_INDEX` / `SET_INDEX`
- `CHECK_CAST`
- `VALIDATE_TAG`

Checked casts, tag validation, missing keys, invalid indexes, and other concrete
value failures happen here or in shared helpers.

Field opcode payloads retain whether the source used ordinary `.` or optional-safe
`->`. Safe reads unwrap `Some`, propagate `None`, flatten already-optional fields,
and vectorise over list-shaped receivers. Safe writes reconstruct the wrapped
payload and return `None` unchanged when the write is cancelled. Deep or mixed
chains are just consecutive field operations; each operation keeps its own flag.

### Calls

- `CALL`
- `CALL_RESOLVED_ELEMENT`
- `TRY_UNWRAP`

`TRY_UNWRAP` is a call-like primitive with frame-level control flow. It unwraps
`OK`/`Some`, but returns the current function immediately for `None` or an
error-like value.

### Control flow

- `JUMP`
- `JUMP_IF_FALSE`
- `JUMP_IF_MATCH`
- `MATCH_ERROR`
- `WHILE`
- `FOREACH`
- `UNFOLD`
- cycle and break operations
- try/panic operations
- `RETURN` / `RETURN_SIGNAL`

A jump changes `ip` and continues without the normal increment. A return moves
the frame stack out, releases frame-owned locals, and hands the result to the
caller.

### Error context is attached at the instruction boundary

If an opcode raises a Python runtime exception, `execute(...)` wraps or enriches
it with:

- function name;
- instruction pointer;
- instruction; and
- a stack snapshot.

Call helpers additionally attach target and argument information. Preserve that
layering when adding errors; a message without execution context is much harder
to debug.

## Control flow is jumps plus small runtime signals

### `if` and simple `while`

These compile to ordinary conditional and unconditional jumps. There is no
separate runtime AST evaluator.

### `match`

Codegen serializes patterns into compact tuple-like pattern specs and emits
`JUMP_IF_MATCH` targets. The VM checks concrete values, creates bindings, and
enters a cycle scope for the matched values.

The analyser remains responsible for type checking and static exhaustiveness
rules. `MATCH_ERROR` protects runtime integrity when no emitted case matches.

### `foreach`, parameterised loops, and return propagation

Nested loop bodies compile as `FunctionCode`. Internal exceptions `_LoopBreak`
and `_FunctionReturn` carry values across the nested Python call boundary.
They are VM implementation signals, not user-visible faults.

### Panics

A Valiance panic is carried internally by `PanicSignal(value)`.

`TRY_BEGIN` pushes a handler table and records the current stack depth. When a
panic reaches `_handle_panic(...)`, the VM:

1. scans active handlers from innermost outward;
2. checks the concrete panic value against each handler type;
3. releases stack values produced after the protected depth; and
4. jumps to the selected handler target.

If no handler matches, the signal leaves the frame. `VirtualMachine.run(...)`
converts an uncaught signal into a user-facing `RuntimeError`.

A panic is intentionally separate from a VM implementation error. A language
program may catch a panic value; it should not catch corrupt bytecode or an
invalid instruction payload.

## Runtime vectorisation executes a static plan

Automatic vectorisation begins in analysis. `AppliedOverload` records whether
mapping is needed and how each argument participates. Codegen copies that plan
to `ResolvedElementReference`.

The VM then performs traversal, because only runtime knows the concrete list
objects and lengths.

### Per-argument depths

A depth of zero means “broadcast this argument unchanged.” A positive depth
means “index one collection level, decrement the depth, and continue.”

This is more precise than “vectorise every list argument.” An exact-list
parameter may intentionally receive a whole list while another argument maps
over its items.

### Target ranks

A parameter may require mapping until an argument reaches an exact collection
rank. `vectorised_target_ranks` lets the VM calculate the needed runtime depth
from reified rank evidence without consuming a lazy list merely to inspect its
shape.

### Eager and lazy paths

Eager sequences can be indexed and have known lengths. The VM checks length
compatibility, recurses by index, and transposes multiple scalar return positions
into multiple result lists.

Lazy lists are advanced by iterators. Deferred vectorisation returns a
`LazyList`, so the scalar operation may execute later. Lazy vectorisation must
produce one value per item because a lazy stream cannot represent several
independent stack-result streams with the current value model.

### Unequal-length `extend`

A compiled vector extension may contain exactly one strategy:

- a default value/function;
- presence-pattern rules; or
- a selector receiving `Some`/`None` values.

Codegen embeds nested function code in `VectorExtensionReference`. The VM
materialises closures after sourcing the call arguments, fills missing vector
positions, and retains extension-owned values when the result is lazy.

### `at` uses the ordinary resolved-call machinery

`TypedAtNode` is lowered to a body closure and a resolved `call` instruction.
The level stop ranks become vectorisation target metadata. Named `at` levels are
normal function parameters; implicit bodies consume the same parameters through
input cycling.

This is a good example of keeping one mechanism: `at` does not require a second
VM iteration language.

## Runtime values and ownership

The VM uses reference-count-style ownership for values that may hold resources,
captures, deferred work, or nested values.

### Value categories

- Python immutable primitives such as `Decimal` and `str` need no explicit
  ownership action.
- `ListValue` is a reference-counted eager list with optional exact rank
  evidence and a cached classification for lists whose direct items require no
  ownership traversal.
- `DictValue` is the corresponding reference-counted eager mapping/record
  wrapper; it caches the same direct-value ownership classification and
  invalidates it on mutation.
- `LazyList` stores an iterable, retained owners, and a reference count.
- `TaggedValue` wraps a payload with reified data-tag evidence.
- `ObjectValue` stores nominal name, fields, generic type arguments, lifecycle
  metadata, and ownership state.
- `FunctionValue` stores code, captures, owned capture names, and a reference
  count.
- `OverloadedFunctionValue` owns its component closures.

### Wrappers that embed arguments

A fresh result can own references that were previously call arguments. `Some` and
`OK` wrappers, records, lists, tuples, and dictionaries may all embed object
values. Before releasing call arguments, result finalization recursively retains
embedded arguments that survive inside a new result. Missing this step produces a
wrapper whose payload has already been destroyed.

### Retain and release

Loading, copying, capturing, returning, consuming, and dropping values call
`_retain_value(...)` or `_release_value(...)` as appropriate.

Containers recursively retain or release contained values. Tagged values defer
to their payload. Lazy results retain their input owners so deferred iteration
does not observe destroyed captures.

Stack operations are therefore not merely Python `append` and `pop`. A semantic
copy may increase ownership; a consumed stack tail must release its values.
The VM first checks whether a consumed tail contains any ownership-bearing value
and skips the recursive release walk for scalar-only tails. `ListValue` and
`DictValue` also carry container reference counts. Ordinary loads increment the
count; releases decrement it and traverse children only when the final owner is
dropped. Borrowed assignment receivers do not increment the count. A uniquely
owned, ownership-trivial container can therefore be updated in place, while an
aliased container is cloned and the original remains unchanged. The cached
direct-item/value classification is invalidated after Python-side mutation and
preserved when a clone only installs scalar replacements. Large numeric buffers
and scalar records therefore avoid both recursive ownership walks and repeated
whole-container copies. Return-tag and collection-rank attachment similarly
return immediately when the analysed metadata is empty. These are performance
fast paths, not changes to ownership or value semantics.

### Object duplication, cleanup, and must-call rules

`ObjectRuntimeType` may describe:

- a destructor element;
- a custom pop hook;
- duplication behaviour or a duplication fault;
- `mustcall` mode; and
- required method names.

When an object's reference count reaches zero, cleanup runs once. The VM may:

1. invoke the pop hook;
2. check required method calls;
3. invoke the destructor; and
4. release field values.

A destructor is not allowed to panic. A missing must-call obligation becomes a
cleanup fault.

### Visible updates use ownership-aware copy-on-write

Field and indexed assignments still have value semantics. Code generation marks
only the assignment receiver as borrowed, and the VM mutates a `ListValue` or
`DictValue` in place only when its reference count proves it is uniquely owned
and its direct ownership metadata is trivial. Shared containers are copied before
the update, so aliases continue to observe the old value. Object values and
lifecycle-bearing container contents retain the conservative reconstruction
path. Generic type arguments, runtime rank evidence, ownership metadata, and
lifecycle state must be preserved by every clone.

When adding a runtime value form, check:

- retain/release;
- formatting;
- runtime type naming;
- equality;
- indexing and field behaviour;
- tags and collection ranks; and
- whether it can appear in bytecode constants.

## Built-ins are scalar stack-fragment functions

A runtime built-in implementation has the shape:

```python
def implementation(
    args: tuple[Any, ...],
    ctx: RuntimeContext,
) -> tuple[Any, ...]:
    ...
```

Arguments are already in parameter order. The result tuple is the stack
fragment to push. No result is `()`, not `None`.

Scalar arithmetic built-ins should remain scalar. Vectorisation wraps the
selected implementation outside the built-in.

A built-in should check only facts the static signature cannot guarantee. For
example, a list parameter need not recheck “is this list-like?” when the resolved
signature proves it, but `head` must still check non-emptiness.

Built-ins that return one of their input objects interact with ownership helpers;
they should not manually increment runtime reference counts.

`BuiltinValue` caches its arity-sorted dynamic candidates, and each
`BuiltinOverload` caches runtime return-tag deltas plus whether its parameter and
return types are ownership-trivial. Resolved call sites additionally cache the
validated built-in/overload pair while guarding against local shadowing or a
replaced global. Do not move these decisions back into the per-call path.

Decimal arithmetic first uses the active context when it is already sufficient
for an exact result, then expands precision and exponent bounds only when
needed. Any faster arithmetic path must retain the arbitrary-precision tests; a
small-number benchmark is not permission to round large integers silently.

## Serialization is a compatibility boundary

`dumps(program)` writes:

1. the magic/version marker `VLNCBC\x0f`;
2. the top-level `FunctionCode`; and
3. all nested instruction payloads recursively.

The format uses:

- fixed opcode bytes;
- big-endian length and integer fields;
- tagged values for constants and reference records;
- explicit strings and tuples; and
- validation while reading.

It intentionally does not use pickle, Python `repr`, enum names, or arbitrary
object serialization.

### Why the magic must change

If an old reader would interpret new bytes incorrectly, bump the magic/version.
Examples include:

- changing a function field order;
- changing an opcode payload layout;
- assigning a different meaning to an existing opcode byte; or
- adding an unmarked required field to a reference record.

Adding a new opcode also requires a stable byte number in `_OP_TO_BYTE` and a
reader mapping in `_BYTE_TO_OP`.

### Positional overload slots are part of current bytecode behaviour

Resolved calls serialize an overload index. Reordering built-in overload
registration can therefore change the meaning of existing bytecode even if the
source signatures are unchanged.

Treat overload order as compatibility-sensitive until the format uses stable
explicit overload identifiers.

### Round-trip testing is mandatory

For a bytecode-affecting feature, test:

```python
restored = loads(dumps(compile_program(typed)))
result = run(restored)
```

This protects against the common failure where in-memory dataclasses work but
the binary reader silently loses one field.

## Tracing one program end to end

Consider:

```valiance
define addFive(x: Number) => $x + 5
10 | addFive | println
```

After analysis, the body contains typed variable, literal, and element nodes.
The `+`, `addFive`, and `println` calls already carry selected overload slots.

Codegen produces a top-level shape like:

```text
0 MAKE_FUNCTION FunctionCode(name="addFive", params=("x",), ...)
1 STORE_VAR "addFive"
2 PUSH_CONST 10
3 CALL_RESOLVED_ELEMENT name="addFive", overload_index=<selected>
4 CALL_RESOLVED_ELEMENT name="println", overload_index=<selected>
5 RETURN
```

The nested function code is approximately:

```text
0 LOAD_VAR "x"
1 PUSH_CONST 5
2 CALL_RESOLVED_ELEMENT name="+", overload_index=<selected>
3 RETURN
```

Execution proceeds as follows:

1. `MAKE_FUNCTION` captures the current environment in a `FunctionValue`.
2. `STORE_VAR` binds it globally as `addFive`.
3. `PUSH_CONST` places `10` on the main frame stack.
4. The resolved call loads `addFive`, sources one argument, and creates a new
   frame with local `x = 10`.
5. The nested frame loads `x`, pushes `5`, and invokes the selected scalar `+`
   built-in directly.
6. The nested `RETURN` produces `[15]`.
7. The caller extends its stack with `15`.
8. The resolved `println` call consumes `15`, writes output, and returns `()`.
9. The main `RETURN` yields an empty final stack.

Nothing in those steps asks “which `+` overload matches?” The analyser answered
that before bytecode existed.

## A Python scratchpad for inspecting bytecode

A small recursive printer is often more useful than reading dataclass `repr`
output:

```python
from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.bytecode import FunctionCode, FunctionSetCode


def show_code(code: FunctionCode, indent: str = "") -> None:
    print(f"{indent}function {code.name!r} params={code.params}")
    for index, instruction in enumerate(code.instructions):
        print(f"{indent}{index:04} {instruction.op.name:<24} {instruction.arg!r}")
        nested = instruction.arg
        if isinstance(nested, FunctionCode):
            show_code(nested, indent + "    ")
        elif isinstance(nested, FunctionSetCode):
            for overload in nested.overloads:
                show_code(overload, indent + "    ")


source = """
define addFive(x: Number) => $x + 5
10 | addFive
"""

analyser = Analyser()
typed = analyser.analyse(parse(source))
assert not analyser.diagnostics

program = compile_program(typed)
show_code(program.main)

restored = loads(dumps(program))
assert run(restored) == [15]
```

`Decimal("15")` compares equal in the repository's tests as a numeric runtime
value; print it directly when you need to distinguish Python representation from
Valiance formatting.

For a typed-call bug, print the `TypedElementNode` before compiling and the
`ResolvedElementReference` after compiling. That usually reveals exactly where
metadata was lost.

## The API decision table

| You need to... | Use or inspect... | Do not... |
|---|---|---|
| compile source meaning | analyse first, then `compile_program(typed)` | pass raw AST to codegen |
| add a simple lowering | `_Compiler.node`, `emit` | interpret AST in the VM |
| add forward control flow | `emit` plus `patch`/`patch_match` | store source nodes in bytecode |
| call a statically selected element | `CALL_RESOLVED_ELEMENT` and `ResolvedElementReference` | rerun overload resolution |
| invoke a first-class callable value | `RuntimeContext.call` / `VirtualMachine.call_value` | assume it is a named element |
| source stack-function arguments | `_Frame.source_args` | manually slice stack and cycle values |
| compile an overload set | `FunctionSetCode` | discard all but one body |
| permit runtime union dispatch | analyser-produced `dispatch_plan` | try bodies until one works |
| permit runtime multimethod selection | `multidispatch=True` on the resolved reference | inspect every overload unconditionally |
| vectorise a resolved call | compiled depths/target ranks | make scalar built-ins map themselves |
| preserve lazy dependencies | `_bind_lazy_result_owners` and retain/release helpers | capture raw values without ownership |
| add a value kind | `runtime_values.py` plus VM ownership/type/format paths | define format rules only in `println` |
| add an opcode | bytecode, compiler, VM, serializer, round-trip tests | update only in-memory execution |
| change bytecode layout | update reader/writer and bump `MAGIC` when incompatible | rely on dataclass field order implicitly |
| improve runtime errors | `RuntimeError` call details and execution contexts | replace structured context with a bare string |

## Fault isolation: find the first wrong stage

When execution is wrong, do not start by editing the VM. Inspect the pipeline in
order.

### 1. Typed AST

Check:

- selected overload index;
- argument order;
- actual returns;
- vectorised flag, depths, and target ranks;
- runtime consumed count;
- static rank values;
- `multidispatch`; and
- constructor type arguments.

If these are wrong, the bug is in analysis or type relations.

### 2. Bytecode

Check:

- opcode sequence;
- nested function bodies;
- jump targets;
- `FunctionCode.params` and `cycle_params`;
- `ResolvedElementReference`; and
- return tags/ranks.

If typed metadata is correct but absent or changed here, the bug is codegen.

### 3. Serialization

Compare the in-memory program with `loads(dumps(program))`. Dataclass equality is
useful here because the bytecode records are immutable structural values.

If only the restored program fails, the bug is in reader/writer symmetry or the
format version.

### 4. VM frame before the failing instruction

Inspect:

- physical stack;
- locals;
- cycle values/index/remaining initial inputs;
- instruction pointer;
- active panic handlers; and
- the resolved reference payload.

A stack-order bug is often visible immediately.

### 5. Runtime helper

Only after the prior stages agree should you inspect value-specific code such as
indexing, pattern matching, vector traversal, or cleanup.

## Common misconceptions

### “Bytecode is just a serialized AST.”

It is an execution plan. It contains jumps, selected overload slots, hidden
static values, compiled nested functions, and runtime metadata that raw syntax
does not contain.

### “The VM owns overload resolution.”

Ordinary overload choice is static. The VM performs only explicitly authorised
dispatch with a compiled plan or flag.

### “The Python list in a frame is the whole Valiance input stack.”

It is the physical stack produced in that frame. Explicit inputs also live in
the conceptual cycle state.

### “Parameter cycling searches for values until something works.”

It supplies a known arity from a known input tuple. It does not search types or
overloads.

### “Every list argument should vectorise.”

Depth zero broadcasts. Exact collection parameters may remain whole while other
arguments vectorise.

### “A built-in returns one Python value.”

It returns a tuple representing zero or more Valiance stack outputs.

### “Python exceptions and Valiance panics are the same.”

`PanicSignal` is a catchable language control signal. VM integrity errors become
`RuntimeError` and carry execution context.

### “Saving bytecode is just calling pickle.”

The format is explicitly encoded for portability and validation.

### “Reference counting is an implementation detail I can ignore in a helper.”

A helper that copies, captures, consumes, or defers a value must preserve
ownership or cleanup behaviour will be wrong.

## Safe extension patterns

### Adding codegen for a new typed node

1. Decide what information analysis must attach.
2. Add or extend the typed-node payload first.
3. Define the minimal opcode sequence or structured reference.
4. Lower it in `_Compiler.node` or a focused helper.
5. Add an in-memory compile/execute test.
6. Add a serialization round-trip if the payload reaches bytecode.
7. Test the invalid nearby case at analyser or compiler level.

Do not store a raw AST node in bytecode and interpret it later.

### Adding an opcode

1. Add `OpCode` in `bytecode.py`.
2. Define a stable payload shape.
3. Emit it from codegen.
4. Execute it in the VM loop.
5. Assign a byte value and serialize its payload.
6. Validate the payload when reading.
7. Bump `MAGIC` if existing readers would misinterpret the stream.
8. Add compiler, VM, and round-trip tests.

### Adding runtime dispatch

1. Prove why static selection is insufficient.
2. Have analysis produce a finite dispatch plan or explicit permission flag.
3. Serialize that plan.
4. Make the VM select only within that plan.
5. Do not use body failure as a matching mechanism.
6. Test side-effecting bodies to ensure candidates are not speculatively run.

### Adding a runtime value

1. Define the shared value record in `runtime_values.py` when appropriate.
2. Add formatting and runtime type naming.
3. Decide list/rank and tag behaviour.
4. Add retain/release behaviour.
5. Add field/index/cast/pattern support where meaningful.
6. Decide whether constants of this type may be serialized.
7. Test cleanup and aliasing, not only equality.

## Tests by responsibility

### `tests/test_runtime.py`

Use for:

- emitted resolved calls;
- frame and stack behaviour;
- closures and recursion;
- built-ins and stdlib hooks;
- vectorisation and extensions;
- objects, fields, indexing, and cleanup;
- panics and matching; and
- end-to-end execution.

Many runtime tests also inspect the compiled instruction so they protect the
analysis-codegen-VM contract rather than only final output.

### `tests/test_bytecode_serialization.py`

Use for:

- every new payload record;
- nested function code;
- function sets and dispatch plans;
- flags and return metadata;
- malformed input validation; and
- version-sensitive changes.

### `tests/test_analyser.py`

Use when the desired runtime behaviour depends on a selected overload,
vectorisation plan, argument order, static values, or another typed decision.

### `tests/test_programs.py`

These protect fundamental language behaviour. Do not modify them as a shortcut
for a runtime change.

## Recommended reading order in the source

A productive first pass is:

1. `runtime/bytecode.py` — learn the records and opcodes.
2. `runtime/compiler.py`: `compile_program`, `_Compiler.compile_function`, and
   `_Compiler.node`.
3. `runtime/compiler.py`: `_resolved_element_reference` and function compilation
   helpers.
4. `runtime/vm.py`: `VirtualMachine.run`, `call`, and `execute`.
5. `_Frame.source_args`, `_call_resolved_element`, and `_call_function`.
6. vectorisation helpers around `_call_vectorized_resolved_builtin` and
   `_vectorize_*`.
7. ownership helpers `_retain_value`, `_release_value`, and object cleanup.
8. `runtime/serialization.py` writer and reader in parallel.
9. focused cases in `tests/test_runtime.py` and
   `tests/test_bytecode_serialization.py`.

Trace one small program before reading all four thousand lines of `vm.py`. Start
at the opcode you care about and follow only its helper chain.

## A final mental checklist

When changing code generation or runtime behaviour, ask:

1. Did analysis already decide this fact?
2. Is the typed-node payload sufficient to preserve that decision?
3. What exact instruction or reference should represent it?
4. Does the callee need a new frame, a nested code object, or only a stack
   operation?
5. Are arguments coming from the physical stack, initial conceptual inputs, or
   cyclic inputs?
6. Is any runtime dispatch explicitly authorised by a compiled plan or flag?
7. Are vectorisation depths, target ranks, and return ranks preserved?
8. Does the operation retain, release, capture, consume, or defer a value?
9. Can a panic cross this boundary, and is it distinct from a VM error?
10. Does the serializer round-trip every new field?
11. Will old bytecode be misinterpreted, requiring a magic/version bump?
12. Is the failure tested at the earliest layer that can observe it?

The runtime is large because it implements many language features, not because
its core is mysterious. Follow the records: typed node, instruction, reference,
frame, value. At each boundary, verify that the earlier stage made the decision
and the later stage merely executes it.
