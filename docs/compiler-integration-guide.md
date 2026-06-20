# Compiler Integration Guide

This guide explains how to use `valiance.types` as the type-relation core inside
a compiler implementation.

The module is deliberately small. It does not parse Valiance source, own symbol
tables, or walk your AST. Your compiler should provide those layers and call
this module for type questions.

## 1. Main Concepts

The most important values are:

```python
Type
Overload
Context
ResolvedOverload
```

Use constructor helpers instead of building `Type(...)` manually:

```python
from valiance.types import (
    ArrayExactType,
    ArrayMinType,
    C,
    CollectionType,
    Fn,
    ListExactType,
    ListMinType,
    ListRuggedType,
    N,
    Overload,
    U,
    V,
    optional,
)

Number = N("Number")
String = N("String")
T = V("T")

number_list = C(ListExactType, Number)
number_matrix = C(ListExactType, Number, 2)
maybe_number = optional(Number)
number_or_string = U(Number, String)
function_type = Fn((Number, Number), (Number,))
```

Collection helpers:

```python
C(ListExactType, T)        # T+
C(ListMinType, T)          # T*
C(ListRuggedType, T)       # T~
C(ArrayExactType, T)       # T^
C(ArrayMinType, T)         # T>
```

The collection shape is encoded in the class hierarchy. `ListExactType`,
`ListMinType`, `ListRuggedType`, `ArrayExactType`, and `ArrayMinType` all
subclass `CollectionType`, so compiler code can use `isinstance` checks when it
needs to distinguish scalar and collection annotations.

```python
if isinstance(annotation_type, CollectionType):
    element_type = annotation_type.base
    rank = annotation_type.rank
```

## 2. Context

`Context` stores relationships the type checker needs:

```python
from valiance.types import Context

ctx = Context()
ctx.trait_impls.setdefault("Circle", set()).add("Shape")
ctx.trait_parents.setdefault("Logger", set()).add("Resource")
ctx.variant_members["SomeMember"] = "SomeVariant"
ctx.unit_tags.add("km")
```

Keep one context per module/checking session, or layer contexts if your compiler
has imports/scopes.

## 3. Assignment Checking

Use `assignable(source, target, ctx)` for places where a value is stored:

```python
from valiance.types import assignable

if not assignable(value_type, declared_type, ctx):
    error("cannot assign value to variable")
```

Use this for:

- variable initialization
- reassignment
- field writes
- return type checking
- branch state consistency

Do not use `assignable` for function arguments. It intentionally does not
vectorise.

## 4. Call Compatibility

Use `compatible(argument, parameter, ctx)` for call parameters:

```python
from valiance.types import compatible

if not compatible(argument_type, parameter_type, ctx):
    error("argument does not satisfy parameter")
```

Compatibility includes assignment-like checks plus call-site conveniences:

- optional wrapping
- trait/intersection satisfaction
- rank compatibility
- vectorisation
- callable/function compatibility

## 5. Generic Solving

Generic solving is intentionally not exposed as primary compiler API. Compiler
code should normally call `apply_overload` or `resolve_overload_result` and
inspect the returned substitution:

```python
from valiance.types import apply_overload

reduce = Overload(
    (C(ListExactType, V("T")), Fn((V("T"), V("T")), (V("T"),))),
    (V("T"),),
)

applied = apply_overload(
    reduce,
    (
        C(ListExactType, Number, 2),
        Fn((Number, Number), (Number,)),
    ),
    ctx,
)

applied.substitution
# {"T": Number+}

applied.params
# (Number++, Function[Number+, Number+ -> Number+])
```

This keeps solve/combine/substitute details inside the type-system library
instead of spreading them throughout the compiler.

## 6. Overload Resolution

Represent each overload as:

```python
from valiance.types import Overload

plus_number = Overload((Number, Number), (Number,))
plus_string = Overload((String, String), (String,))
```

Resolve with:

```python
from valiance.types import resolve_overload_result

result = resolve_overload_result(
    [plus_number, plus_string],
    (Number, Number),
    ctx,
)

if result is None:
    error("no unique overload")
```

`result` gives you:

```python
result.overload        # raw overload
result.substitution   # solved generics, e.g. {"T": Number+}
result.params         # instantiated parameter types
result.returns        # instantiated return types
result.scores         # specificity vector
```

If you want to test one candidate directly, use `apply_overload`:

```python
from valiance.types import apply_overload

applied = apply_overload(reduce, arg_types, ctx)
if applied:
    applied.substitution   # {"T": Number+}
    applied.params         # instantiated params
    applied.returns        # declared returns after substitution
    applied.actual_returns # returns after vectorisation/call adaptation
```

For example:

```python
reduce = Overload(
    (C(ListExactType, V("T")), Fn((V("T"), V("T")), (V("T"),))),
    (V("T"),),
)

arg_types = (
    C(ListExactType, Number, 2),
    Fn((Number, Number), (Number,)),
)

result = resolve_overload_result([reduce], arg_types, ctx)
```

This resolves:

```text
T = Number+
instantiated: (Number++, Function[Number+, Number+ -> Number+]) -> Number+
```

The function argument works because `Function[Number, Number -> Number]` can
act as `Function[Number+, Number+ -> Number+]` through vectorisation.

## 7. Stack Expression Checking

Your compiler should own AST walking and stack simulation. For each element call:

```python
visible_args = stack[-arity:]
result = resolve_overload_result(element_overloads, visible_args, ctx)

if result is None:
    error("no matching overload")

stack = stack[:-arity] + list(result.returns)
```

For generic overloads, use `result.returns`, not `result.overload.returns`.

For vectorised concrete calls, `apply_overload` exposes the actual return stack:

```python
from valiance.types import apply_overload

applied = apply_overload(result.overload, tuple(visible_args), ctx)
returns = applied.actual_returns
```

For stack-based checking and definition-site inference, use
`apply_overload_to_stack`:

```python
from valiance.types import apply_overload_to_stack

# Normal checking: stack must already contain enough values.
applied = apply_overload_to_stack(plus_overload, stack, ctx)

# Inference: missing values are added to the function input list.
applied = apply_overload_to_stack(
    plus_overload,
    stack=(),
    ctx=ctx,
    infer_missing=True,
)

applied.inputs  # (Number, Number)
applied.stack   # (Number,)
```

## 8. Function Compatibility

Callable values are checked by callability, not exact function type equality.

```python
from valiance.types import compatible

actual = Fn((Number, Number), (Number,))
expected = Fn(
    (C(ListExactType, Number), C(ListExactType, Number)),
    (C(ListExactType, Number),),
)

compatible(actual, expected, ctx)  # True
```

This is how a scalar function can be accepted where a vectorised function type
is expected.

## 9. Definition-Site Inference

Definition-site inference should live in your AST analysis layer. The type
system library provides the primitive you need for element calls:

```python
from valiance.types import apply_overload_to_stack

applied = apply_overload_to_stack(
    Overload((Number, Number), (Number,)),
    stack=(),
    ctx=ctx,
    infer_missing=True,
)

applied.inputs  # (Number, Number)
applied.stack   # (Number,)
```

Each AST node can transform a set of stack states. For an element call, try each
candidate overload with `infer_missing=True`; successful applications become
the next possible states. This keeps inference AST-aware without putting AST
logic into the type-system library.

## 10. Suggested Compiler Pipeline

Recommended order:

1. Parse source into AST.
2. Build symbol tables for variables, elements, overloads, objects, traits, and tags.
3. Convert parsed type annotations into `Type` values.
4. Build a `Context` from imported trait/tag/variant information.
5. For each definition:
   - check explicit parameter and return types
   - infer missing parameter types if supported
   - stack-check the body
   - register resulting `Overload`
6. For each call:
   - collect argument types from the stack
   - call `resolve_overload_result`
   - apply `result.returns` to the stack
7. For assignments/returns:
   - call `assignable`
8. For diagnostics:
   - show raw overload, instantiated overload, and `result.substitution`

## 11. Control-Flow Merging

Use `merge_types` and `merge_stacks` when joining branches:

```python
from valiance.types import merge_types, merge_stacks

merge_types(Number, String)
# Number | String

merge_stacks((Number,), ())
# Number?

merge_stacks((Number,), (String,))
# Number | String
```

`merge_stacks` pads the shorter stack with `None` before merging, matching the
optional-padding rule used by branch joins.

## 12. Error Reporting

Useful error details:

```python
result = resolve_overload_result(overloads, args, ctx)
if result is None:
    # For now the module only reports failure as None.
    # Your compiler can rerun candidate checks to produce detailed reasons.
```

Good user-facing diagnostics should include:

- attempted argument types
- candidate overload signatures
- solved generic substitutions, when available
- which parameter failed compatibility
- whether failure involved assignment, vectorisation, trait satisfaction, or generic conflict

The REPL’s `overload ...` command is a useful model for displaying successful
generic resolution.

## 13. Current Limits

This module is a practical core, not a full compiler:

- no AST representation
- no parser for Valiance expressions
- no scope or import system
- no control-flow stack merging
- no row-polymorphism implementation beyond basic type shape support
- no detailed failure objects yet
- no runtime shape-check obligation tracking yet

The intended next step for compiler integration is to wrap these boolean/return
APIs in richer diagnostic functions once your AST and symbol table exist.
