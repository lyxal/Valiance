# Analysis and Type System Guide

This guide is for future agents working on the Valiance analyser and type
system. It is intentionally self-contained: do not assume the reader has
loaded any other compiler guide.

The analyser is currently branch-centric. Every analysed block starts from a
set of possible analysis branches and returns a new set of possible branches.
Ordinary, non-inference analysis is just the one-branch case.

## Main Files

- `src/valiance/analysis/analyser.py`
  Owns branch analysis, AST node dispatch, function inference, variable facts,
  control-flow joins, literal inference, tag propagation, and diagnostics.

- `src/valiance/analysis/builtins.py`
  Declares the default element environment and runtime implementations for
  built-ins.

- `src/valiance/types/nodes.py`
  Defines the type model dataclasses.

- `src/valiance/types/builders.py`
  Provides type constructors, normalization, display formatting, and readable
  helpers such as `ExactList`, `TypeVariable`, `WithTag`, and `WithoutTag`.

- `src/valiance/types/relations.py`
  Owns assignability, subtyping, generic solving, overload resolution,
  vectorisation typing, collection item typing, and stack merging.

- `src/valiance/types/environment.py`
  Stores non-branch-dependent facts: overloads, object definitions, attributes,
  traits, variants, and tag declarations.

- `src/valiance/types/context.py`
  Stores type relationship facts used by relation checks.

- `src/valiance/asts/nodes.py`
  Defines parser/analyser AST nodes and typed AST wrapper nodes.

## Mental Model

Think of the analyser as a transformation:

```text
BranchSet + ASTNode -> BranchSet
BranchSet + block   -> BranchSet
```

Each `AnalysisBranch` is one possible world:

- `stack`: the current `TypeStack`
- `inputs`: function inputs inferred so far
- `variables`: branch-local variable facts
- `typed_body`: typed AST accumulated on this branch
- `input_mode`: how missing stack inputs may be sourced
- `cycle_params`: explicit function parameters available for cycling
- `break_type`: loop-break result currently carried by the branch

Do not add a separate non-branching path. If a feature has no ambiguity, it
should naturally produce one branch.

## Branch Sets

`BranchSet` is the driver for analysis. Its important methods are:

- `extend_block(nodes, analyser)`
  Analyses a sequence node by node.

- `map_node(node, analyser)`
  Applies one AST node to every current branch.

- `require_stack_top_assignable(expected, ctx)`
  Validates every branch. This is used by conditions: one invalid condition path
  invalidates the whole condition.

- `pop_stack_top()`
  Removes the condition value after validation.

- `join(other, analyser)`
  Merges branch sets pairwise.

Prefer adding general `BranchSet` operations only when more than one node kind
will use them. Node-specific checks belong in the node handler.

## Input Modes

`InputMode` controls stack underflow:

- `TOP_LEVEL`
  Underflow is an error.

- `INFER_INPUTS`
  Used by `fn => ...`. Missing inputs become inferred function inputs. It never
  cycles explicit parameters.

- `CYCLE_EXPLICIT_PARAMS`
  Used by `fn (x: T, y: U) => ...`. If the function stack underflows, parameters
  are reused cyclically.

- `NILADIC`
  Used by `fn () => ...`. Underflow is an error.

All element and call application should source inputs via
`AnalysisBranch.source_arguments`. Do not manually slice stacks in node
handlers.

## Variables

Variables live in `BranchVariables`, not in `Environment`.

Read order is:

```text
block locals -> function locals -> parameters -> captures
```

Write behavior:

- Existing block locals and function locals can be updated only with assignable
  types.
- Parameters are read-only.
- Captured outer variables are shadowed by new function-local bindings.
- New names can become function locals or block locals depending on the caller.

This means overload-specific variable writes stay branch-specific until a
control-flow join. Do not prematurely union variable types while branches are
still separate.

## Environment

`Environment` stores facts that do not vary per branch:

- element overloads
- object definitions and attributes
- trait implementations and trait parents
- variant membership
- data tag declarations and disjointness

Do not put mutable local variables in `Environment`.

Built-ins should be added in `src/valiance/analysis/builtins.py`. Prefer readable
type builders:

```python
T.ExactList(T.TypeVariable("Item"))
T.WithoutTag(T.ExactList(T.TypeVariable("Item")), "infinite")
T.Fn((T.TypeVariable("Item"),), (T.TypeVariable("Mapped"),))
```

Avoid reaching for `T.C(...)`, `T.V(...)`, or raw `T.DataTag(...)` in built-ins
unless the readable helper does not exist yet.

## Element Application

Element application happens in `Analyser._element`:

1. Get overloads from `Environment`.
2. Ask each branch to source arguments with `source_arguments`.
3. Apply each overload through `_apply_overload_to_branch`.
4. Keep non-dominated winners with `_best_candidates`.
5. Reject ambiguity outside inference unless winners merely specialize inputs.
6. Push `applied.actual_returns`.
7. Append a typed node that records the resolved operation.

Overload resolution itself belongs in `types.relations`, not in the analyser.
The analyser owns how overload application transforms branches.

`AppliedOverload.returns` and `AppliedOverload.actual_returns` are different:

- `returns`: declared returns after generic substitution
- `actual_returns`: returns after call adaptation such as vectorisation and data
  tag flow

When updating stack state after a call, use `actual_returns`.

The typed AST carries the selected overload for overload-resolved operations.
Element applications use `TypedElementNode`; call expressions use
`TypedCallNode`.

The important data to attach is the resolved compile-time operation, not just
the displayed type:

- original element symbol
- selected overload signature
- substituted parameter types
- substituted declared returns
- actual returns after vectorisation/tag flow
- for elements, the overload index within the element definition, which lets
  codegen find the runtime implementation without re-resolving overloads by type

Keep this on typed metadata, not on the raw parser `ElementNode`. Parser AST
nodes should remain syntax. Do not make the runtime compiler repeat type-level
overload resolution as a workaround.

## Type Relations

Use the relation helpers instead of open-coding type checks:

- `T.assignable(source, target, ctx)`
  For storage and declared return checks.

- `T.compatible(argument, parameter, ctx)`
  For call compatibility, including vectorisation.

- `T.apply_overload(...)`
  For one overload against concrete argument types.

- `T.resolve_overload_result(...)`
  For choosing a single overload from callable/function types.

- `T.collection_item_type(type)`
  For loop/item analysis. It looks through tag wrappers.

- `T.merge_types(...)` and `T.merge_stacks(...)`
  For branch result joins.

The relation layer owns generic solving. If a feature needs to know what `T`
became in `Function[T -> U]`, use solved overload/application results rather
than re-solving in the analyser.

## Collection Types

Valiance has several collection type nodes that look similar but mean different
things. Do not collapse them into one generic "list" idea.

Readable constructors:

```python
T.ExactList(base, rank=1)    # T+
T.AtLeastList(base, rank=1)  # T*
T.RuggedList(base, rank=1)   # T~
T.ExactArray(base, rank=1)   # T^
T.AtLeastArray(base, rank=1) # T>
```

The lower-level constructor is:

```python
T.C(T.ListExactType, base, rank)
```

Use readable constructors in built-ins and docs. The lower-level constructor is
fine inside relation tests or generic type-manipulation code.

### Exact Lists

`ExactList(T, n)` means a list with exactly `n` ranks. Surface syntax uses `+`:

```text
Number+   == ExactList(Number, 1)
Number+2  == ExactList(Number, 2)
Number++  == ExactList(Number, 2)
```

Exact ranks do not freely widen to other exact ranks. `Number+2` is not the same
as `Number+`.

### Minimum-Rank Lists

`AtLeastList(T, n)` means a list with at least `n` ranks. Surface syntax uses
`*`:

```text
Number*   == AtLeastList(Number, 1)
Number*3  == AtLeastList(Number, 3)
```

An exact list can satisfy a compatible minimum-rank list if its rank is high
enough. Generic solving may widen exact/minimum constraints to a minimum-rank
solution.

### Rugged Lists

`RuggedList(T, n)` means potentially ragged nested list structure. Surface
syntax uses `~`:

```text
Number~   == RuggedList(Number, 1)
Number~2  == RuggedList(Number, 2)
```

Rugged rank is weaker than exact/minimum list structure. When generic
constraints include rugged lists, `_combine_collections` conservatively widens
to a rugged list.

### Arrays

Arrays have exact and minimum-rank forms:

```text
Number^   == ExactArray(Number, 1)
Number>   == AtLeastArray(Number, 1)
```

Arrays can often be treated as corresponding list types by relation checks:

- exact arrays can satisfy exact list parameters of the same rank
- minimum arrays can satisfy minimum list parameters of the same rank
- vectorisation preserves arrays only when the vectorised inputs are arrays
- mixing arrays and lists produces list results

Do not assume arrays are fully implemented runtime rectangular values just
because the type layer has array rank nodes.

### Nested Collection Normalization

`normalize` collapses nested collection nodes when rank modes have a clear
combined meaning. For example, surface syntax can produce nested nodes like
`(Number+2)*`; normalization collapses this into the weakest shape that
preserves meaning.

The important rule: when exact/minimum/rugged ranks mix, the weaker outer
meaning usually wins:

- exact + exact keeps exact
- any minimum involvement generally widens to minimum
- any rugged list involvement widens to rugged
- arrays wrapped by list syntax become list-shaped

Use `T.normalize(...)` before comparing types structurally. Use `T.same(...)`
for canonical equality.

### Collection Item Types

Use `T.collection_item_type(typ)` to peel one rank for `foreach`-style analysis.
It handles tagged collections:

```python
T.collection_item_type(T.ExactList(T.Number, 2)) == T.ExactList(T.Number)
T.collection_item_type(T.AtLeastList(T.Number)) == T.U(
    T.Number,
    T.AtLeastList(T.Number),
)
```

Minimum/rugged lists can leave a union because peeling one rank may expose
either the base element or another collection at runtime.

## Optional And Result Normalization

Optional and Result are Valiance-specific enough that future changes should be
explicit about what exists today and what is still design intent.

### Optional

The implemented optional shape is:

```text
T? == Some[T] | None
```

Use:

```python
T.optional(inner)
T.Some(inner)
T.NoneType()
```

`T.optional(inner)` builds `Union(Some(inner), None)`. This is not just cosmetic:
assignability treats optionals specially.

Current behavior:

- `None` is assignable to any optional.
- A non-`None` value assignable to `T` is assignable to `T?` through implicit
  `Some[T]` wrapping.
- `_optional_inner` extracts the present-value payload from `Some[T] | None`.
- generic solving for `T?` does not let `None` alone solve `T`.
- branch stack merging pads missing values with `None`, then uses optional
  normalization through `merge_types`.

Important implementation detail: optional detection is structural. `_is_optional`
recognises normalized unions containing `None`. Do not introduce a separate
`OptionalType` node unless you are deliberately changing the type model.

When merging optional generic solutions, `_combine` merges their inner payloads
and then re-wraps with `optional(...)`.

### Optional Caveats

The design document describes richer simplification rules such as
`T | Some[U] -> Some[T | U]` in specific cases. The current implementation only
has the subset needed by assignment, solving, branch merge padding, and display.
If you implement more optional normalization, add relation tests first because
small changes here affect overload resolution.

### Result

`Result` currently exists as a nominal generic constructor:

```python
T.Result(ok, err)
```

It normalizes like other nominal types: its generic arguments are normalized,
but there is no special Result-union rewrite in the current implementation.

This means:

```text
Result[Number, String]
```

is represented as:

```python
NominalType(Symbol("Result"), (Number, String))
```

Do not assume that unions such as `T | E` automatically become
`Result[T, E]`. That is future design intent, not current analyser behavior.

If adding Result normalization later, keep it separate from ordinary union
normalization until the error trait hierarchy exists. Otherwise the type layer
will incorrectly rewrite ordinary unions into Results.

## Function Literals

Function literals are handled by `_analyse_function_literal`.

The function analyser creates a fresh `AnalysisBranch` using:

- declared params as `inputs` for explicit functions
- no inputs initially for inferred functions
- named parameters as read-only variables
- outer visible variables as captures
- function-local stack according to `InputMode`

Function return rules:

- `returns is None`: return the top stack value, or nothing if the stack is empty
- `returns == ()`: return no values
- explicit returns: require the final stack to be assignable to the expected
  return stack

Multiple viable function signatures become an `OverloadSetType`. The typed
function node keeps per-overload typed bodies so the typed AST remains useful.

## Literals

List, tuple, record, and dict literals analyse their item expressions from the
same incoming branch. This is intentional: literals act like an implicit fork.

For list literals:

- every item expression must leave a value
- item types are unioned
- stack fallback/input inference is merged across item branches
- the final literal consumes the maximum stack arity consumed by any item
- empty list literals are rejected unless another feature supplies a type
  annotation/cast

Tuple, record, and dict literals reuse the same literal item machinery but build
different result types.

## Control Flow

`IfNode`:

1. Analyse condition from incoming branches.
2. Require every condition branch to leave assignable `Boolean`.
3. Pop the condition value.
4. Analyse then and else from the condition-derived branches.
5. Require branch inputs to match.
6. Merge result stacks with `merge_stacks`.
7. Merge variables against the pre-branch variables.

Do not filter out invalid condition paths if any valid path remains. That is
unsound: all possible condition branches must be valid.

`ForNode`:

- reads iterable type from the top of stack
- uses `T.collection_item_type`
- introduces block-local loop variable and optional index variable
- drops loop-local variables after body analysis
- returns `None` when no break value exists
- optionalises/merges break result types when breaks are present

`WhileNode` exists in the AST/runtime direction, but static-analysis support is
not yet as complete as `IfNode` and `ForNode`. Be careful before marking it done.

## Data Tags

Tags are represented by `DataTag`:

```python
DataTag(name: str, depth: int = 0, absent: bool = False)
```

This is important. Do not collapse tags back to strings. Depth distinguishes
facts like an outer list being infinite from an inner list being infinite.

Tag kinds live in `Context`:

- constructed
- computed
- unit
- variant

The analyser applies data tag flow after overload application:

- computed tags are stripped unless explicitly declared on the return type
- constructed/unit tags propagate according to rank/depth rules
- variant tags add their parent computed tag
- disjoint tags replace incompatible existing tags

Use `T.WithTag(...)` and `T.WithoutTag(...)` for readable signatures.

## Row Polymorphism And Fields

Field access can refine generic/row types.

If a field is accessed on an inferred parameter, the analyser creates a row type
like:

```text
@1(.field: @2)
```

Later overload use can refine the field type. This is how examples like
`fn => $.x double end` infer that `.x` must be numeric.

Nominal object attributes are stored in `Environment`. Row constraints are type
facts and can exist without a declared object.

## Adding A New AST Node

The normal workflow:

1. Add the AST node in `src/valiance/asts/nodes.py`.
2. Parse it in `src/valiance/parsing/parser.py`.
3. Add a dispatcher case in `Analyser._analyse_node_from_branch`.
4. Implement a helper that takes one `AnalysisBranch` and returns
   `set[AnalysisBranch]`.
5. Use existing branch/type helpers for stack, variables, and overloads.
6. Append a `TypedNode` with the useful result type.
7. Add focused analyser tests.

A node handler should not mutate a branch. Use branch replacement helpers such
as `with_stack`, `with_variables`, `append_typed`, or `_replace_branch`.

If the node contains sub-blocks, call `self.analyse_block(...)` rather than
manually iterating through AST nodes.

## Diagnostics

The analyser currently stores diagnostic strings. Use `_diagnose(message, node)`
so source locations are included when available.

For speculative inference, avoid emitting diagnostics for branches that are
cleanly trimmed. Diagnostics should explain final invalid programs, not every
failed candidate that was part of normal inference.

## Tests

Prefer focused tests in:

- `tests/test_types.py` for relation and overload behavior
- `tests/test_analyser.py` for branch/analyser behavior
- `tests/test_parser.py` for syntax lowering
- `tests/test_runtime.py` for compiler/runtime behavior

Useful verification commands:

```powershell
$env:PYTHONPATH='src'; python -m unittest tests.test_analyser tests.test_types -v
$env:PYTHONPATH='src'; python -m unittest discover -s tests -v
$env:UV_CACHE_DIR="$PWD\.uv-cache"; uv run ruff check .
```

## Common Pitfalls

- Do not add local variables to `Environment`.
- Do not add a non-branching analysis path.
- Do not manually manipulate stack state when `source_arguments` or `TypeStack`
  can do it.
- Do not treat `applied.returns` as the stack result of a call; use
  `applied.actual_returns`.
- Do not flatten `DataTag` to strings.
- Do not silently filter invalid condition branches.
- Do not union branch-local variable types while overload branches are still
  distinct.
- Do not update broad docs as the main source of truth for this stage; keep
  analysis/type-system guidance focused here.
