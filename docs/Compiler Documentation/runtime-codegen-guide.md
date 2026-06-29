# Runtime and Codegen Guide

This guide is for future agents working on Valiance's bytecode compiler,
runtime, built-ins, or saved bytecode format. It is intentionally self-contained:
do not assume the reader has loaded any other compiler guide.

## Current Pipeline

The runtime path is:

1. Source text is parsed into AST nodes.
2. Static analysis turns those nodes into `TypedNode` values.
3. `compile_program()` lowers only typed AST nodes into bytecode.
4. `VirtualMachine` executes the bytecode.
5. The CLI can optionally write or read portable binary bytecode files.

The important boundary is step 3: analysis returns typed ASTs, and codegen
expects typed ASTs. Do not make codegen accept raw ASTs as a convenience. If a
test needs compilation, run the analyser first.

## Runtime Module Map

The runtime implementation is small, but several files must evolve together.

`src/valiance/runtime/bytecode.py`

- Defines the bytecode data model: `Program`, `FunctionCode`, `Instruction`,
  and `OpCode`.
- `FunctionCode.params` stores explicit parameter names.
- `FunctionCode.cycle_params` enables the stack-underflow parameter cycling used
  by explicit-parameter functions such as `define triple(:Number) => * 3`.

`src/valiance/runtime/compiler.py`

- Lowers typed AST nodes to `Instruction` values.
- `compile_program(nodes: list[TypedNode]) -> Program` rejects raw AST nodes.
- Function literals and definitions become `MAKE_FUNCTION`.
- Definitions compile as `MAKE_FUNCTION` followed by `STORE_VAR`.
- List literals and array literals currently both compile to `BUILD_LIST`.
- `TagApplicationNode` is currently a compile-time no-op.
- `ForNode` is not compiled yet.
- For typed functions with multiple inferred overload bodies, codegen currently
  picks the first typed overload body.

`src/valiance/runtime/vm.py`

- Executes bytecode with a stack per frame.
- Built-in elements are loaded into VM globals from
  `valiance.analysis.builtins.runtime_elements()`.
- User functions are represented as `FunctionValue`.
- Built-ins are represented as `BuiltinValue`.
- Function calls and built-in calls both source arguments from the current
  stack. Explicit-parameter functions can also source missing arguments from the
  parameter cycle.
- Legacy unresolved built-in dispatch tries runtime overloads dynamically. Normal
  compiled code should use resolved element calls produced by static analysis.
- Resolved built-in calls carry the analyser-selected overload slot and whether
  the call should be vectorised.
- Runtime call errors should include the stack, stack types, and attempted input
  shapes.

`src/valiance/runtime_values.py`

- Defines shared runtime-value helpers used by built-ins, the VM, and the CLI.
- `LazyList` wraps an iterable that should behave like a Valiance list without
  promising a finite length.
- `is_list_like(...)` tests for list-shaped runtime values. It
  accepts Python lists and lazy iterable values, but excludes strings, bytes,
  tuples, and mappings because those are distinct runtime shapes.
- `is_finite_list_like(...)` means a list-like value has a known `len(...)`.
- `is_eager_sequence(...)` means a list-like value can be indexed without
  consuming it.

`src/valiance/runtime/serialization.py`

- Encodes `Program` as portable binary bytecode.
- The current magic/version marker is `b"VLNCBC\x02"`.
- Opcodes are one byte each in `_OP_TO_BYTE`.
- Instruction arguments are tagged binary values, not Python pickle, repr, or
  JSON.
- Nested `FunctionCode` values are serialized as tagged values, which is how
  function literals survive bytecode round trips.

`src/valiance/analysis/builtins.py`

- This is the shared built-in catalogue for static analysis and runtime.
- `BuiltinElement` groups all overloads for one element name.
- `BuiltinOverload.signature` is the analyser-visible stack effect.
- `BuiltinOverload.implementation` is the runtime implementation.
- `default_environment()` publishes static overloads to the analyser.
- `runtime_elements()` publishes runtime-capable built-ins to the VM.

`src/valiance/main.py`

- Wires the CLI to analysis, codegen, VM execution, and bytecode files.
- Relevant flags are `--run`, `--implicit-output`, `--emit-bytecode <file>`, and
  `--run-bytecode <file>`.

## Core Invariants

Keep these rules intact unless the language design explicitly changes.

`compile_program` takes typed AST nodes only.

The analyser is responsible for producing `TypedNode` values. The compiler may
unwrap typed nodes internally to inspect the underlying AST, but the public
compile boundary should keep rejecting raw ASTs.

Built-ins have one source of truth.

Do not reintroduce separate static-only and runtime-only definitions for the
same element. Add built-ins in `analysis/builtins.py` so the analyser and VM see
the same element set.

Overload resolution should be a compile-time decision.

The analyser attaches resolved overload metadata to `TypedElementNode` and
`TypedCallNode`. Codegen lowers resolved element calls to bytecode that
identifies the selected overload slot directly. The VM then executes that
selected overload instead of repeating overload resolution from runtime value
shapes. Built-in elements select a built-in overload slot; user-defined elements
select a compiled function overload slot from `FunctionSetCode`.

`AppliedOverload.vectorised` records whether overload application required
vectorisation. Codegen can inspect `typed_node.overload.vectorised` on
`TypedElementNode` or `TypedCallNode` when it needs different lowering for a
vectorised call shape.

Runtime checks may still be needed for values whose static type permits several
runtime shapes, such as finite list lengths. Those checks belong inside the
selected overload implementation and should validate assumptions made by that
overload; they should not choose a different overload.

The VM stack is ordered bottom to top.

When an operation consumes multiple values, the rightmost values of the Python
list are the top of the Valiance stack. `_pop_many(stack, count)` preserves
left-to-right argument order for calls and collection builders.

Built-in implementations return stack fragments.

A runtime implementation receives a tuple of arguments and returns a tuple of
values to push. Niladic returns use `()`, not `None`.

Vectorisation belongs in dispatch, not in each arithmetic built-in.

Scalar built-ins such as `+` and `*` should remain simple scalar
implementations. For resolved calls, analysis decides whether vectorisation is
needed and codegen records that decision in bytecode. The VM then maps the
selected scalar implementation over each list item and collects the returned
stack fragments.

Saved bytecode must stay portable.

Do not use pickle, Python object reprs, or textual opcode names for bytecode
files. A future non-Python implementation should be able to decode the format
from the binary spec in `serialization.py`.

Runtime diagnostics are part of the developer experience.

When dispatch fails, keep enough information to debug:

- the target element or function name
- the current stack values
- the runtime type shape of the stack
- the input shapes that were attempted

## Adding a Built-In Element

Add new built-ins in `src/valiance/analysis/builtins.py`.

1. Add a `Symbol` constant near the other built-in names.
2. Add an `element(...)` entry to `BUILTIN_ELEMENTS`.
3. Add one or more `overload(...)` entries.
4. Provide a runtime implementation for overloads that should execute.
5. Put value-shape checks that cannot be proven statically inside the runtime
   implementation.
6. Add analyser and runtime tests.

Example shape:

```python
SQUARE = Symbol("square")

element(
    SQUARE,
    overload(
        (T.Number,),
        (T.Number,),
        lambda args, ctx: (args[0] * args[0],),
    ),
)
```

For richer runtime validation:

```python
def _head(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    if not is_list_like(args[0]):
        raise RuntimeError("head requires a list")
    return (next(iter(args[0])),)


element(
    HEAD,
    overload(
        (T.ExactList(T.TypeVariable("Item")),),
        (T.TypeVariable("Item"),),
        _head,
    ),
)
```

Prefer named helper functions once behaviour is more than a tiny lambda. This
keeps overload entries readable as the built-in catalogue grows.

Use the helpers from `valiance.runtime_values` for collection validation. Do not
write new runtime built-ins that check only `isinstance(value, list)` unless the
operation truly requires Python's eager list object specifically.

## Adding an Opcode

An opcode change crosses the bytecode model, compiler, VM, serializer, and
tests.

1. Add the operation to `OpCode` in `runtime/bytecode.py`.
2. Emit it from `runtime/compiler.py`.
3. Execute it in the `match instruction.op` block in `runtime/vm.py`.
4. Assign it a stable one-byte value in `_OP_TO_BYTE` in
   `runtime/serialization.py`.
5. Add decoding coverage through `_BYTE_TO_OP`.
6. Add tests for direct execution and bytecode round trip.

Choose opcode arguments that the serializer can encode. Currently supported
argument value tags are:

- `None`
- `int`
- `Decimal`
- `str`
- `tuple`
- nested `FunctionCode`

If a new opcode needs a new argument shape, extend the serializer deliberately
and test invalid/truncated data. If the on-disk format changes incompatibly,
bump the magic/version marker.

## Adding Codegen for an AST Node

Start from the typed AST produced by analysis.

1. Confirm the analyser returns a `TypedNode` for the new surface syntax.
2. Add a `case` in `_Compiler.node()`.
3. Reuse existing opcodes if they express the behaviour clearly.
4. Add a new opcode only when the VM needs a new primitive operation.
5. Add runtime tests through `analyse(...)`, `compile_program(...)`, and
   `run(...)`.
6. Add CLI or serialization tests when the feature should survive saved
   bytecode.

If the node has child expressions, compile them in source order unless the
language reference says otherwise. Stack effects should be obvious from the
instruction sequence; avoid hidden compiler-side stack mutation.

## Resolved Overload Codegen

Resolved built-in elements use analyser-selected overloads.

```text
TypedElementNode("+", overload_index=N)
  -> CALL_RESOLVED_ELEMENT ("+", N, vectorised)
VM invokes the selected built-in overload directly, vectorising only when the
compiled reference says to do so
```

Unresolved elements keep the normal path:

```text
ElementNode("name")
  -> LOAD_ELEMENT "name"
  -> CALL
```

The invariant is: type-level overload resolution belongs to analysis, and
runtime should not redo it for operations whose selected implementation is known
at compile time.

Implementation checklist:

1. Extend typed AST metadata so overload-resolved nodes expose the chosen
   overload.
2. Give built-in overloads an identity within their element definition.
3. Emit `CALL_RESOLVED_ELEMENT` with
   `(element_name, overload_index, vectorised)`.
4. Serialize that reference, not a Python function object.
5. In the VM, execute the selected overload directly.
6. Include the analyser's vectorisation decision in the resolved bytecode
   reference so runtime does not infer it again.
7. Keep runtime validation for vectorisation length mismatches and other
   concrete value assumptions inside the selected implementation.
8. Preserve useful errors if a bytecode file references an unknown element or
   overload id.

Be careful with saved bytecode. The current bytecode format encodes positional
overload indices, so changing built-in overload order is a compatibility
concern. Prefer explicit stable overload ids before bytecode is treated as
durable.

## Function Calls and Parameter Cycling

Valiance functions are stack functions. Calling a user function does not create
a shared stack with the caller. The VM:

1. Pops the callee.
2. Sources as many arguments as possible from the caller stack.
3. If the callee has `cycle_params`, fills missing arguments by cycling the
   caller function's explicit parameter values.
4. Executes the callee with its own fresh stack.
5. Pushes the callee's returned stack values back onto the caller stack.

This matters for definitions such as:

```valiance
define triple(:Number) => * 3
println triple 5
println(triple([1, 2, 3, 4, 5]))
```

The body `* 3` needs one argument from the call and one literal from the body.
For `triple([1, 2, 3, 4, 5])`, runtime vectorisation maps the scalar `*`
over the list and returns `[3, 6, 9, 12, 15]`.

Be careful when changing this area. Seeding function frames with arguments or
changing when parameter cycling happens can easily break stack-style function
bodies.

## Runtime Vectorisation

Runtime vectorisation executes a scalar built-in implementation across
list-shaped arguments when static analysis has selected a scalar overload and
marked the call as vectorised.

For a scalar overload with a runtime implementation:

- If analysis marked the resolved call as scalar, execute the selected overload
  implementation directly.
- If analysis marked the resolved call as vectorised, map the selected scalar
  implementation over the list-shaped arguments.
- Scalar arguments broadcast across list arguments.
- Eager sequence list arguments must have the same length before mapping.
- Lazy list arguments are advanced with iterators and may be infinite.
- The scalar overload may return multiple stack values; vectorisation collects
  each return position into its own list.
- Lazy vectorisation returns a `LazyList` and requires the vectorised scalar call
  to produce exactly one stack value per item.
- Lazy vectorisation detects mismatched finite/lazy input lengths only when the
  shorter iterator is exhausted.

For example:

```valiance
[1, 2, 3] + [5, 6, 7]
```

uses the scalar `Number Number -> Number` overload of `+` and returns:

```text
[6, 8, 10]
```

Do not hardcode arithmetic vectorisation in `+`, `*`, or the compiler. Keep it
generic so future scalar built-ins get the same behaviour.

List-consuming built-ins should preserve laziness when possible. For example,
`map` returns an eager Python list for finite list-like inputs and a `LazyList`
for lazy inputs; `head` consumes only the first item; `length` requires a
finite list-like value and rejects lazy/infinite lists at runtime. Runtime value
formatters print lazy lists as `<lazy list>` rather than forcing iteration.

## Implicit Output

`--implicit-output` is a CLI feature, not a VM feature.

The VM returns the final stack from `run(program)`. The CLI tracks whether any
runtime output was printed. If the program printed nothing and
`--implicit-output` is present, the CLI prints the final stack using the
runtime-style value formatter.

This means library callers can still use `run()` without surprise printing.

## Bytecode Files

The CLI supports two bytecode workflows:

```powershell
uv run valiance --code "[1, 2, 3] + [5, 6, 7]" --emit-bytecode C:\tmp\sample.vbc
uv run valiance --run-bytecode C:\tmp\sample.vbc --implicit-output
```

The bytecode file contains:

1. The magic/version marker.
2. The top-level `FunctionCode`.
3. Function name, parameter cycle flag, parameters, and instructions.
4. One-byte opcodes and tagged instruction arguments.

When editing the serializer, preserve these properties:

- reject unknown magic/version values
- reject unknown opcode bytes
- reject unknown value tags
- reject trailing bytes
- reject truncated data
- never silently coerce malformed payloads

## Debugging Runtime Mismatches

Useful checks when a program analyses but fails at runtime:

1. Inspect the typed AST to confirm analysis selected the expected stack effect.
2. Inspect `compile_program(typed).main.instructions`.
3. Run with `--implicit-output` to see the final stack when nothing printed.
4. If dispatch fails, read the runtime error's stack values, stack types, and
   attempted input shapes.
5. Save and rerun bytecode to distinguish compiler issues from VM/serializer
   issues.

Good smoke commands:

```powershell
uv run valiance --code "[1, 2, 3] + [5, 6, 7]" --run --implicit-output
uv run valiance --code "[1, 2, 3] + [5, 6, 7]" --emit-bytecode C:\tmp\sample.vbc
uv run valiance --run-bytecode C:\tmp\sample.vbc --implicit-output
```

Good test commands:

```powershell
uv run ruff check .
uv run python -m unittest discover -s tests -v
```

## Current Limitations

These are known constraints of the current runtime/codegen layer:

- `ForNode` / foreach codegen is not implemented.
- `TagApplicationNode` currently compiles as a no-op.
- Unresolved element calls still compile through `LOAD_ELEMENT` / `CALL` and
  are runtime-dispatched.
- Runtime arrays are currently represented like lists.
- Function overload codegen chooses the first typed overload body.
- Full closure semantics should not be assumed; function values capture the
  current visible globals/locals shallowly at `MAKE_FUNCTION`.
- Built-in runtime dispatch is useful but not yet a complete multimethod system.
- Runtime vectorisation errors are plain runtime errors, not a dedicated
  `VectorisationFault` type.
- Generic type information is not preserved in bytecode.

When completing one of these items, update this guide and
`docs/valiance-feature-checklist.md` in the same change.
