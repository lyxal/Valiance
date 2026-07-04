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
- `FunctionCode.element_tags` stores runtime function tag names copied from the
  analysed function type.
- `FunctionCode.cycle_params` enables the stack-underflow parameter cycling used
  by explicit-parameter functions such as `define triple(:Number) => * 3`.

`src/valiance/runtime/compiler.py`

- Lowers typed AST nodes to `Instruction` values.
- `compile_program(nodes: list[TypedNode]) -> Program` rejects raw AST nodes.
- Function literals and definitions become `MAKE_FUNCTION`.
- Definitions compile as `MAKE_FUNCTION` followed by `STORE_VAR`.
- List literals and array literals currently both compile to `BUILD_LIST`.
- Object and variant declarations compile constructor globals with
  `MAKE_OBJECT_CONSTRUCTOR`; enum members compile constants with
  `MAKE_ENUM_MEMBER` and optional backing-value globals.
- Permitted object/record member writes compile to `SET_FIELD`, which returns a
  reconstructed value instead of mutating the original visible value.
- `TagApplicationNode` is currently a compile-time no-op.
- `TryNode` compiles to `TRY_BEGIN` / `TRY_END` plus handler jumps. Runtime
  panics are carried by `PanicSignal` and caught by the nearest active handler
  whose nominal type name matches, or by a catch-all handler.
- `ForNode` compiles to a runtime foreach operation. Loop bodies may signal
  `break` values; normal completion returns runtime `None` values matching the
  analysed break result shape.
- For typed functions with multiple inferred overload bodies, codegen currently
  picks the first typed overload body.

`src/valiance/runtime/vm.py`

- Executes bytecode with a stack per frame.
- Built-in elements are loaded into VM globals from
  `valiance.analysis.builtins.runtime_elements()`.
- User functions are represented as `FunctionValue`.
- Built-ins are represented as `BuiltinValue`.
- Object constructors are represented as `ObjectConstructorValue`, and nominal
  object/variant/enum member values are represented as `ObjectValue`.
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
- The current magic/version marker is `b"VLNCBC\x05"`.
- Opcodes are one byte each in `_OP_TO_BYTE`.
- Instruction arguments are tagged binary values, not Python pickle, repr, or
  JSON.
- Nested `FunctionCode` values are serialized as tagged values, which is how
  function literals survive bytecode round trips.

`src/valiance/analysis/builtins.py`

- This is the shared built-in catalogue for static analysis and runtime.
- `BuiltinElement` groups all overloads for one element name.
- `BuiltinOverload.signature` is the analyser-visible stack effect.
- `BuiltinOverload.element_tags` is the analyser-visible element tag set.
- `BuiltinOverload.implementation` is the runtime implementation.
- `default_environment()` publishes static overloads to the analyser.
- `runtime_elements()` publishes runtime-capable built-ins to the VM.

`src/valiance/main.py`

- Wires the CLI to analysis, codegen, VM execution, and bytecode files.
- Source input compiles by default and emits `.vbc` bytecode.
- Relevant actions are `compile`, `run`, `run-bytecode`, `parse`, and `analyse`.
- `run` compiles and executes source without writing bytecode.

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

Resolved element references are serialized as tuples for bytecode portability,
but VM code should decode them into `_ResolvedElementReference` before use.
Avoid indexing the raw tuple directly; the tuple layout is a bytecode boundary,
and positional magic numbers make compatibility changes fragile.

`AppliedOverload.vectorised` records whether overload application required
vectorisation. Codegen can inspect `typed_node.overload.vectorised` on
`TypedElementNode` or `TypedCallNode` when it needs different lowering for a
vectorised call shape.

Runtime checks may still be needed for values whose static type permits several
runtime shapes that the signature alone does not disambiguate, such as whether
a list is non-empty for `head`, or which case of an optional/Result union a
value actually is for `&`, `?`, and `?!`. Those checks belong inside the
selected overload implementation and should validate assumptions made by that
overload; they should not choose a different overload.

Do not re-check what the signature already guarantees. A parameter typed
`T.ExactList(...)` guarantees list shape; `T.WithoutTag(..., "infinite")`
guarantees finiteness; a `T.Fn(...)` parameter guarantees its return arity.
Overload implementations should not re-validate any of these at runtime --
that work is analysis's job, not the implementation's.

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

Add new built-ins in `src/valiance/analysis/builtins.py`, using the
`@builtin(...)` decorator. There is no `Symbol` constant to add and no tuple
to hand-edit. `BUILTIN_ELEMENTS` still exists as a module-level export for
callers that want the full catalogue directly, but it is derived from the
registry at import time (`BUILTIN_ELEMENTS = _all_elements()`) after every
`@builtin(...)` / `declare_overload(...)` call has run -- treat it as
read-only and never append to it by hand.

1. Write the runtime implementation as a function with signature
   `(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]`.
2. Decorate it with `@builtin(name, params, returns, generic_constraints=())`,
   where `name` is a plain string (e.g. `"square"`, not a `Symbol`).
3. If the element has multiple overloads that share one implementation, stack
   `@builtin(...)` multiple times on the same function -- one application per
   overload. Decorator stacking applies bottom-up, so the overload closest to
   `def` registers first; keep that in mind if overload order matters (see
   "Resolved Overload Codegen" below).
4. If overloads have different implementations, decorate separate functions
   with the same `name` string instead.
5. If an overload is analyser-visible but has no runtime behaviour yet, use
   `declare_overload(name, params, returns, generic_constraints=())` instead
   of `@builtin(...)` -- there is no function to decorate.
6. Put value-shape checks inside the implementation only for things the
   signature cannot prove -- see the "Core Invariants" note above. Do not
   duplicate checks the type system already guarantees.
7. Add analyser and runtime tests.

If a built-in has observable side effects or must not be delayed, give it
element tags through the decorator. For example, `print` and `println` are
tagged `Eager` and `IO`; `length` is intentionally not eager because it can be
used inside a lazy function without causing side effects.

Example shape, single overload:

```python
@builtin("square", (T.Number,), (T.Number,))
def _square(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (args[0] * args[0],)
```

Example shape, multiple overloads sharing one implementation:

```python
@builtin("==", (T.Number, T.Number), (T.Boolean,))
@builtin("==", (T.String, T.String), (T.Boolean,))
def _equals(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_truth(args[0] == args[1]),)
```

Example shape, an overload with no runtime implementation:

```python
declare_overload(
    "/",
    (
        T.ExactList(T.TypeVariable("Item")),
        T.Fn(
            (T.TypeVariable("Item"), T.TypeVariable("Item")),
            (T.TypeVariable("Item"),),
        ),
    ),
    (T.TypeVariable("Item"),),
)
```

Example shape, eager side-effecting built-in:

```python
@builtin("println", (T.V("T"),), (), element_tags=(EAGER_TAG, IO_TAG))
def _println(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    ctx.print(format_runtime_value(args[0]))
    return ()
```

Example of genuine runtime validation -- something the signature cannot
guarantee, unlike list shape or finiteness:

```python
@builtin("head", (T.ExactList(T.TypeVariable("Item")),), (T.TypeVariable("Item"),))
def _head(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    for item in args[0]:
        return (item,)
    raise RuntimeError("head requires a non-empty list")
```

Prefer named, decorated functions over inline lambdas once behaviour is more
than a one-liner. This keeps the catalogue readable as it grows, and gives
each overload a stable Python name for stack traces and tests.

Every builtin implementation is only ever invoked dynamically through the
registry, so pyright cannot see its call sites and will flag each one with
`reportUnusedFunction`. This is a false positive inherent to the pattern; it
is suppressed once, file-wide, with a `# pyright: reportUnusedFunction=false`
comment near the top of `builtins.py`. Do not add per-function
`# pyright: ignore[...]` comments to work around it.

Use the helpers from `valiance.runtime_values` for collection validation. Do not
write new runtime built-ins that check only `isinstance(value, list)` unless the
operation truly requires Python's eager list object specifically.

### Adding A Call-Site Checked Built-In

Use call-site type checking when a built-in accepts a function whose stack shape
is not known from the built-in's declared overload. The declared overload should
use bare `T.Fn()` for each unknown function parameter:

```python
@builtin("dip", (T.Fn(),), call_site=_dip_call_site)
def _dip(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    callable_value = args[-1]
    held = args[-2]
    return (*ctx.call(callable_value, list(args[:-2])), held)
```

`T.Fn()` means unknown-shape `Function`, not `Function[ -> ]`. This structurally
marks the overload as call-site checked. The analyser will not try to prove the
declared overload body for every possible function. Instead, at each element
call it substitutes the concrete modifier type, pulls any required extra inputs
from the outer stack, and checks that concrete shape.

A call-site helper receives `call_params`, which are the concrete outer-stack
types being considered followed by the explicit built-in parameters. It returns
a concrete `T.Overload` or `None`. The returned overload must:

1. Use concrete `Function[...]` types in the function-parameter slots.
2. Include every type passed to the runtime implementation in `params`.
3. Set `call_site_body` to the number of outer-stack values consumed, excluding
   explicit modifier/function arguments.
4. Return `None` when the concrete function(s) cannot apply to the proposed
   stack types.

`peek`, `dip`, and `fork` show the three common consumption patterns:

- `peek` passes the inspected stack values and function to runtime, but consumes
  no outer-stack values, so its concrete overload uses `call_site_body=0`.
- `dip` consumes the function's arguments plus the held value, so it uses
  `call_site_body=arity + 1`.
- `fork` passes one shared argument slice to two functions and consumes that
  slice once, so it uses `call_site_body=arity`.

Use the shared helpers in `builtins.py` when writing the call-site helper:

- `_callable_overloads(type)` opens a concrete `Function[...]` or overload set.
- `_apply_callable(overload, args)` checks one candidate and returns the applied
  overload plus the concrete `Function[...]` type to put back in the signature.
- `_callable_applications(type, args)` iterates all successful applications.

A future `correspond` built-in that applies two functions to corresponding
argument groups could follow the same pattern. For example, if
`3 4 5 6 | correspond: (+, -)` should return `7, -1`, the call-site helper
would split the candidate stack suffix into the argument tuple for `+` and the
argument tuple for `-`, call `_apply_callable(...)` for each modifier, then
return a concrete overload like:

```python
T.Overload(
    (*left_args, *right_args, left_application.concrete_type, right_application.concrete_type),
    (*left_application.applied.actual_returns, *right_application.applied.actual_returns),
    call_site_body=len(left_args) + len(right_args),
)
```

The runtime implementation should mirror the same concrete stack contract:

```python
@builtin("correspond", (T.Fn(), T.Fn()), call_site=_correspond_call_site)
def _correspond(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    *call_args, left, right = args
    left_args = call_args[: len(call_args) // 2]
    right_args = call_args[len(call_args) // 2 :]
    return (*ctx.call(left, list(left_args)), *ctx.call(right, list(right_args)))
```

If the split is not always half-and-half, encode that rule in both the call-site
helper and runtime implementation. Do not put element-name special cases in the
analyser; the `@builtin(..., call_site=...)` registration is the extension point.

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

Resolved object and variant constructor calls may include instantiated generic
arguments in the bytecode reference:

```text
(element_name, overload_index, vectorised, vectorised_depths, type_args)
```

The VM passes `type_args` to `ObjectValue`, and object field reconstruction must
preserve them. Older three- and four-item resolved references remain valid.

Resolved user-defined element calls may also include static rank values produced
by a `where` clause:

```text
(element_name, overload_index, vectorised, vectorised_depths, type_args, rank_values)
```

### `?` Result/Optional Short-Circuiting

The analyser resolves the built-in `?` element with ordinary overload metadata,
but the compiler does not emit it as `CALL_RESOLVED_ELEMENT`. A resolved `?`
lowers to:

```text
TRY_UNWRAP
```

The VM pops the top value:

- `OK{value: x}` and `Some{value: x}` push `x` and continue.
- `None` or an error-like object is pushed back and the current function returns
  immediately.
- any other value is pushed back unchanged.

This is intentionally a bytecode primitive because the short-circuit target is
the current frame, not just the built-in implementation. The non-short-circuit
helper `?!` remains a normal resolved built-in: it unwraps success values and
panics with `UnwrappedNoneFault` or `UnwrappedResultFault` for absent/error
values.

`rank_values` is a tuple of `(name, int)` pairs from
`AppliedOverload.rank_values`. The VM appends these values as hidden arguments
when invoking the selected user-defined overload, allowing function bodies to
read computed static variables such as `$n`. Built-in overloads should not infer
or recompute these values at runtime.

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
`map` always returns a `LazyList`, regardless of whether the input was finite
or lazy -- it does not eagerly materialise finite inputs, and callers that
need an eager list can drive the `LazyList` themselves; `head` consumes only
the first item; `length`'s parameter type is
`T.WithoutTag(T.ExactList(...), "infinite")`, so a finite list-like value is
already guaranteed by analysis and `length` does not re-check it at runtime.
The exception is an eager callable argument: `map: println` is materialised at
the call site because the callable carries the `Eager` element tag and must run
immediately.
Runtime value formatters fully iterate list-like values by default, including
lazy and potentially infinite values. The CLI `--preview-lists` flag opts
runtime output and implicit stack output into bounded list previews.

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
uv run valiance --code "[1, 2, 3] + [5, 6, 7]" --output C:\tmp\sample.vbc
uv run valiance run-bytecode C:\tmp\sample.vbc --implicit-output
```

The bytecode file contains:

1. The magic/version marker.
2. The top-level `FunctionCode`.
3. Function name, parameter cycle flag, parameters, element tags, and
   instructions.
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
3. Run with `run --implicit-output` to see the final stack when nothing printed.
4. If dispatch fails, read the runtime error's stack values, stack types, and
   attempted input shapes.
5. Save and rerun bytecode to distinguish compiler issues from VM/serializer
   issues.

Good smoke commands:

```powershell
uv run valiance run --code "[1, 2, 3] + [5, 6, 7]" --implicit-output
uv run valiance --code "[1, 2, 3] + [5, 6, 7]" --output C:\tmp\sample.vbc
uv run valiance run-bytecode C:\tmp\sample.vbc --implicit-output
uv run valiance parse --code "1 + 2"
uv run valiance analyse --code "1 + 2"
```

Good test commands:

```powershell
uv run ruff check .
uv run python -m unittest discover -s tests -v
```

## Current Limitations

These are known constraints of the current runtime/codegen layer:

- `TagApplicationNode` currently compiles as a no-op.
- Unresolved element calls still compile through `LOAD_ELEMENT` / `CALL` and
  are runtime-dispatched.
- Runtime arrays are currently represented like lists.
- Function overload codegen chooses the first typed overload body.
- Full closure semantics should not be assumed; function values capture the
  current visible globals/locals shallowly at `MAKE_FUNCTION`.
- Built-in runtime dispatch is useful but not yet a complete multimethod system.
- Runtime vectorisation errors are plain runtime errors, not yet dedicated
  catchable `VectorisationFault` panic values.
- Only resolved object/variant constructor calls currently preserve instantiated
  generic type arguments in runtime values.

When completing one of these items, update this guide and
`docs/valiance-feature-checklist.md` in the same change.
