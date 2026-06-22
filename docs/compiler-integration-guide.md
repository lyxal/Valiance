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
TypeStack
Environment
Overload
Context
ResolvedOverload
```

Use constructor helpers instead of building `Type(...)` manually:

```python
from valiance.types import (
    ArrayExactType,
    ArrayMinType,
    AppliedElement,
    C,
    CollectionType,
    Environment,
    Fn,
    ListExactType,
    ListMinType,
    ListRuggedType,
    N,
    NoMatchingOverload,
    ObjectAttribute,
    Overload,
    TypeStack,
    U,
    UnknownElement,
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
empty_stack = TypeStack()
env = Environment()
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

## 2. Environment And Context

`Environment` is the compiler-facing registry for named things. Built-in
language elements live in `valiance.analysis.builtins`, which constructs the
standard environment:

```python
from valiance.analysis import default_environment

env = default_environment()
```

For focused tests or custom compiler phases, you can still build one manually:

```python
from valiance.types import Environment, N, Overload

env = Environment()
env.define_overload("+", Overload((N("Number"), N("Number")), (N("Number"),)))
env.define_object(
    "Foo",
    (
        ObjectAttribute("bar", N("Bax")),
        ObjectAttribute("name", N("String")),
    ),
)

env.overloads_for("+")
# overload candidates for +

env.lookup_object("Foo")
# object definition for Foo

env.lookup_attribute("Foo", "bar")
# Bax
```

Object definitions store type-shape facts for object types in scope. Use them
for questions like "does object `Foo` exist?" and "what is the type of
`Foo.bar`?". Object methods and object-friendly elements should still be stored
as overloads, because they participate in normal element dispatch.

```python
if not env.object_exists("Foo"):
    error("unknown object type")

attribute_type = env.lookup_attribute("Foo", "bar")
if attribute_type is None:
    error("Foo has no attribute bar")
```

Branch-dependent variables are not stored in `Environment`. They live in the
analyser's `BranchVariables`, because a variable type may differ per overload or
control-flow branch. Use the environment for facts that are stable across
branches: overloads, objects, traits, variants, tags, and built-ins.

```python
variables = branch.variables
variables.read("x")
# Number

variables, diagnostic = variables.write("x", String)
if diagnostic is not None:
    error(diagnostic)
```

Writes to function parameters are rejected. Writes to captured outer variables
create local shadows in the current function branch. Block-local variables, such
as loop variables, are added to `BranchVariables.block_locals` and dropped when
the block joins.

For stack-based element checking, ask the environment to apply the named
overload set:

```python
match env.apply("+", stack):
    case AppliedElement(application):
        stack = application.stack
    case UnknownElement():
        error("unknown element")
    case NoMatchingOverload() as result:
        stack = result.stack
        error("no matching overload")
```

`Context` stores relationships the type checker needs:

```python
from valiance.types import Context, Environment

ctx = Context()
ctx.trait_impls.setdefault("Circle", set()).add("Shape")
ctx.trait_parents.setdefault("Logger", set()).add("Resource")
ctx.variant_members["SomeMember"] = "SomeVariant"
ctx.unit_tags.add("km")
```

Every `Environment` owns a `Context` as `env.context`. You can either build a
context directly for low-level relation calls, or populate it through
environment helpers:

```python
env = Environment()
env.add_trait_impl("Circle", "Shape")
env.add_trait_parent("Logger", "Resource")
env.add_variant_member("SomeMember", "SomeVariant")
env.add_unit_tag("km")
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

Your compiler should own AST walking, but it should not manually pop and push
argument slices. If you use `valiance.analysis.Analyser`, element calls are
handled as branch transformations: the branch sources arguments from its stack,
from definition-site inference, or from explicit-parameter cycling, then applies
the overload and pushes the return types.

For lower-level stack-only checks, use the environment or stack application
helpers so arity checks, overload choice, generic substitution, and vectorised
returns stay in one place.

```python
from valiance.types import TypeStack

stack = TypeStack()

match env.apply(element_name, stack):
    case AppliedElement(application):
        stack = application.stack
    case UnknownElement():
        error("unknown element")
    case NoMatchingOverload() as result:
        stack = result.stack
        error("no matching overload")
```

`application` also gives you the resolved details for diagnostics:

```python
application.overload        # raw overload
application.substitution   # solved generics, e.g. {"T": Number+}
application.params         # instantiated parameter types
application.returns        # declared returns after substitution
application.actual_returns # returns after vectorisation/call adaptation
application.scores         # specificity vector
```

`UnknownElement` means the element name is not defined at all. `NoMatchingOverload`
means the name is defined, but none of its overloads accept the current argument
types. Overload sets deliberately have one fixed input arity and one fixed return
count across all candidates, so a failed match still has a deterministic stack
effect: it pops the expected number of inputs and pushes `Never` once for each
return value.

```python
match env.apply(element_name, stack):
    case NoMatchingOverload() as result:
        result.params         # fixed parameter shape for the element
        result.actual_returns # (Never, ...) with the fixed return count
        stack = result.stack  # failed stack transition already applied
        error("no matching overload")
```

If the compiler has already selected overload candidates, use `stack.apply`.
If it has already selected one overload, use `stack.apply_one`:

```python
# Normal checking against a candidate set.
applied = stack.apply(element_overloads, env.context)

# Normal checking against one known overload.
applied = stack.apply_one(plus_overload, ctx)

# Low-level inference primitive: missing values are reported as inputs.
applied = TypeStack().apply_one(
    plus_overload,
    ctx=ctx,
    infer_missing=True,
)

applied.inputs  # (Number, Number)
applied.stack   # stack with Number on top
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

Definition-site inference lives in the AST analysis layer. The analyser treats
every block as a branch-set transformation:

```python
from valiance.analysis import AnalysisBranch, BranchSet, InputMode

initial = BranchSet.one(
    AnalysisBranch(input_mode=InputMode.INFER_INPUTS)
)
final = analyser.analyse_block(initial, function_body)
```

Function literals distinguish omitted params from explicit empty params:

```python
FunctionNode(params=None, body=(ElementNode("+"),))
# fn => + end
# missing stack inputs are inferred; ambiguous overloads become branches

FunctionNode(params=(), body=(ElementNode("+"),))
# fn () => + end
# no missing stack inputs are inferred; underflow is an error

FunctionNode(
    params=(FunctionParam("x", Number), FunctionParam("y", Number)),
    body=(ElementNode("+"), ElementNode("+")),
)
# explicit non-empty params may be cycled on stack underflow
```

Each AST node transforms a `BranchSet`. For an element call, the analyser tries
each candidate overload against each incoming branch. Successful applications
become the next branches. If multiple candidates survive because there were not
enough concrete input types to choose one, keep those branches. A function
literal with several final stack effects infers an `OverloadSet` of function
signatures.

Top-level underflow, niladic underflow, unknown elements, and no viable overload
drop the affected branch and emit diagnostics. Branch filtering is sound for
overload candidates, but not for control-flow conditions; see the next section.

When a function literal infers multiple overloads, the typed AST should retain
the typed body for each overload. `analyse` returns a `TypedFunctionNode` for
function literals; its `typ` is the outer function type or overload set, and
`overloads` stores each inferred function type with the body typed under that
branch.

```python
from valiance.asts import TypedFunctionNode

typed = analyse([FunctionNode(body=(ElementNode("+"),))])
fn_node = typed[0]

if isinstance(fn_node, TypedFunctionNode):
    fn_node.typ
    # OverloadSet[Function[Number, Number -> Number], ...]

    for overload in fn_node.overloads:
        overload.typ
        # Function[Number, Number -> Number]
        overload.body
        # typed nodes for this specific overload branch
```

## 10. Suggested Compiler Pipeline

Recommended order:

1. Parse source into AST.
2. Start from `default_environment()` and layer imported/module symbols on top.
3. Convert parsed type annotations into `Type` values.
4. Add imported trait/tag/variant relationships to `env.context`.
5. For each definition:
   - check explicit parameter and return types
   - infer missing parameter types if supported
   - stack-check the body
   - register resulting `Overload`
6. For each call:
   - call `env.apply(element_name, stack)`
   - replace the current stack with `applied.stack`
7. For assignments/returns:
   - call `assignable`
8. For diagnostics:
   - show raw overload, instantiated overload, and `result.substitution`

## 11. Control-Flow Merging

Use `merge_types` and `merge_stacks` when joining branch stacks:

```python
from valiance.types import TypeStack, merge_types, merge_stacks

merge_types(Number, String)
# Number | String

merge_stacks(TypeStack((Number,)), TypeStack())
# stack with Number? on top

merge_stacks(TypeStack((Number,)), TypeStack((String,)))
# stack with Number | String on top
```

`merge_stacks` pads the shorter stack with `None` before merging, matching the
optional-padding rule used by branch joins.

Your AST analyser should treat control-flow nodes as transformations over a
`BranchSet`. The type library supplies stack and type merge primitives; the
analyser supplies general branch-set validation and block driving.

One possible AST shape:

```python
@dataclass(frozen=True)
class IfNode(ASTNode):
    condition: tuple[ASTNode, ...]
    then_body: tuple[ASTNode, ...]
    else_body: tuple[ASTNode, ...] = ()


@dataclass(frozen=True)
class WhileNode(ASTNode):
    condition: tuple[ASTNode, ...]
    body: tuple[ASTNode, ...]
```

For an `IfNode`, analyse the condition first. Require every condition branch to
leave a non-`Never` `Bool` on top of the stack, pop that condition value, then
analyse both branches from the same post-condition branch set. Merge every pair
of surviving branch stacks and variables.

```python
Bool = N("Bool")


def analyse_if(node: IfNode, branch: AnalysisBranch, analyser: Analyser):
    incoming = BranchSet.one(branch)
    condition = analyser.analyse_block(incoming, node.condition)
    condition = condition.require_stack_top_assignable(Bool, analyser.env.context)
    if not condition:
        error("if condition must leave Bool")
        return set()

    body_inputs = condition.pop_stack_top()
    then_outputs = analyser.analyse_block(body_inputs, node.then_body)
    else_outputs = analyser.analyse_block(body_inputs, node.else_body)

    outputs: set[AnalysisBranch] = set()
    for left in then_outputs:
        for right in else_outputs:
            if left.inputs != right.inputs:
                error("branches inferred different function inputs")
                continue
            outputs.add(
                branch.with_stack(
                    merge_stacks(left.stack, right.stack, analyser.env.context)
                )
            )
    return outputs
```

For example, if one branch leaves `Number` and the other leaves nothing, the
merged stack has `Number?` in that position. If one branch leaves `Number` and
the other leaves `String`, the merged stack has `Number | String`.

For a `WhileNode`, the condition and body may run zero or more times. Check that
one iteration is a valid loop transform and merge the before-loop branch with
the after-body branch to approximate the zero-or-more result.

```python
def analyse_while(node: WhileNode, branch: AnalysisBranch, analyser: Analyser):
    condition = analyser.analyse_block(BranchSet.one(branch), node.condition)
    condition = condition.require_stack_top_assignable(Bool, analyser.env.context)
    if not condition:
        error("while condition must leave Bool")
        return set()

    body_outputs = analyser.analyse_block(condition.pop_stack_top(), node.body)

    outputs: set[AnalysisBranch] = {branch}
    for body_output in body_outputs:
        outputs.add(
            branch.with_stack(
                merge_stacks(branch.stack, body_output.stack, analyser.env.context)
            )
        )
    return outputs
```

That simple `while` rule is conservative and useful as a first pass. Later, you
can refine it into a fixed-point loop:

1. Start with the state before the loop.
2. Analyse condition and body from the current approximation.
3. Merge the old approximation with the body output.
4. Repeat until the stack stops changing, or report an error if it keeps
   widening beyond a compiler limit.

The important rule is that the condition's `Bool` is a control value, not a
normal branch result. Pop it before analysing the branch or body.

For a fuller explanation of adding AST nodes to the analyser, including typed
child bodies and inference behaviour, see `docs/analyser-extension-guide.md`.
That guide also covers `ForEachNode`-style loop variables with
`BranchVariables.with_block_local(...)` and iterable item typing with
`collection_item_type(...)`.

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
