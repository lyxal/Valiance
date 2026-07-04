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
- element tag declarations

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

Overload lookup is scope-sensitive. `Analyser()` creates a child environment
whose parent is the built-in environment. `Environment.overloads_for(name)`
returns local/user overloads when any are present; otherwise it falls back to
the parent environment. This means user-defined elements shadow built-ins
instead of merging with them.

Use the `*::element` spelling to explicitly request the parent built-in
overloads. For example, `Some(1)` can call a user-defined `Some`, while
`*::Some(1)` calls the built-in optional constructor. The parser stores this as
`Symbol("*::Some")`; codegen strips the `*::` prefix for runtime built-in
references.

The typed AST carries the selected overload for overload-resolved operations.
Element applications use `TypedElementNode`; call expressions use
`TypedCallNode`.

The important data to attach is the resolved compile-time operation, not just
the displayed type:

- original element symbol
- selected overload signature
- substituted parameter types
- substituted declared returns
- substituted element tags

Applied overloads also carry the element tags contributed by the selected
operation. The analyser treats those as sticky effect facts: if a function body
calls an `IO` operation, the inferred function type also carries `IO`.
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

### Generic Variance

Nominal generic constructors get declaration-site variance metadata from
`Context.generic_variance`. Unknown constructors, arity mismatches, and generic
positions without metadata default to invariant. `Variance.COVARIANT` checks
`source_arg` assignable to `target_arg`; `Variance.CONTRAVARIANT` checks the
opposite direction; `Variance.INVARIANT` requires canonical equality.

The analyser publishes variance for object, trait, and variant declarations.
Explicit markers in declaration generic lists win when present:

```valiance
object[T: any Vehicle] Box => ...
object[T: above Vehicle] Sink => ...
```

When no marker is present, variance is inferred from declaration usage:

- readable fields and returns are positive uses
- public writable fields count as both positive and negative uses
- function parameters are negative uses
- nested function positions flip polarity through parameters

Both positive and negative use makes the parameter invariant. Keep this
conservative: unknown or unsupported uses should not silently become variant.
Type syntax parses bare `T` as a nominal name, so the analyser rewrites type
names that match the declaration's generic parameters into `VarType` before
registering object attributes, constructors, and requirements.

Generic parameter bounds are stored as `GenericConstraint` records on overloads.
For `T: Vehicle`, `T: any Vehicle`, or `T: above Vehicle`, overload application
first solves `T` from the actual arguments, substitutes any solved variables in
the bound, and then requires the solution to be assignable to the bound. This
check belongs in `types.relations.apply_overload`, so constrained object
constructors, generic definitions, and any future constrained overload source
share the same rule.

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
- collection item types are covariant for assignability; for example `Car+` can
  satisfy `Vehicle+` when `Car` implements `Vehicle`, while rank rules still
  apply independently

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

## Tuple Types

Fixed tuple types are represented by `T.TupleType` and constructed with
`T.Tup(...)`.

Arbitrary-length tuple parameter types are represented by
`T.VariadicTupleType`, whose `items` are `T.TupleTypeItem` records. Each item
has a `typ` and a `repeated` flag. Use `T.TupVariadic(...)` or
`T.TupRepeat(item)` rather than constructing the node by hand:

```python
T.Tup(T.Number, T.String)  # {Number, String}
T.TupRepeat(T.Number)     # {Number...}
T.TupVariadic(
    T.TupleTypeItem(T.Number, repeated=True),
    T.TupleTypeItem(T.String),
)                         # {Number..., String}
```

Variadic tuple types exist only as parameter types. Fixed `TupleType` values can
be assigned to a variadic tuple parameter when they match the repeated/fixed
pattern. Variadic-to-variadic assignability is intentionally not generalized;
callers pass normal fixed tuple values.

Relation checks use backtracking for repeated segments. Keep this in
`types.relations` so overload application, generic solving, and direct
assignability all agree on which fixed tuple shapes match a variadic tuple
parameter.

## Rank Variables And `where` Clauses

Collection ranks may be an `int` or a `RankVariable`. Surface syntax uses
`$name` after a rank marker, for example `T+$n`, `T*$n`, `T~$n`, `T^$n`, or
`T>$n`.

`Overload.where_clause` stores the parsed static expression body, and
`Overload.param_names` maps overload parameters back to source parameter names.
When an overload is applied, analysis:

1. Binds rank variables that appear in parameter types from the actual argument
   collection ranks.
2. Binds source parameter names such as `$shape` to their actual static types.
3. Evaluates the `where` clause with the small static evaluator.
4. Substitutes solved rank values into parameters and returns.
5. Records the solved values in `AppliedOverload.rank_values` so runtime codegen
   can pass them into user-defined function bodies when needed.

The static evaluator intentionally permits only terminating operations:
number literals, static variables, assignment, arithmetic (`+`, `-`, `*`,
`min`, `max`), comparisons, boolean operations, stack operations (`dup`, `pop`,
`swap`), `length` on fixed tuple types/values, function type introspection, and
`?` overload assertions. Unknown element calls reject the current overload
candidate.

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

The implemented Result shape uses explicit success values plus error-like
nominal values:

```python
T.Result(ok, err)
T.OKType(ok)
```

`T.Result(ok, err)` is represented as `NominalType(Symbol("Result"), (ok, err))`.
`T.OKType(ok)` is represented as `NominalType(Symbol("OK"), (ok,))`.

The normalizer performs a conservative Result rewrite for unions that contain at
least one success-like item and at least one error-like item:

```text
Number | ParseError -> Result[Number, ParseError]
OK[Number] | OK[String] | ParseError -> Result[Number | String, ParseError]
Result[Number, ParseError] | String -> Result[Number | String, ParseError]
```

Error-like types are currently recognized by a narrow built-in convention:

- the nominal `Err` type itself
- non-generic nominal types whose names end with `Error`

The standard environment also defines the built-in `Err` trait and records
`AssertError` and `PanicError` as implementations. User code can add explicit
implementations with normal `object MyError as Err => ...` syntax; the current
normalizer is still name-convention based because `normalize(...)` does not take
an environment.

Assignability and overload solving know the Result success/error split:

- `OK[T]` is assignable to `Result[T, E]`.
- an error type is assignable to `Result[T, E]` through the error side.
- `OK[T]` can solve a `Result[T, E]` parameter, leaving unconstrained `E` as
  `Never` when needed.
- an error value can solve a `Result[T, E]` parameter through `E`, leaving
  unconstrained `T` as `Never` when needed.

The built-in helper elements are defined in `analysis/builtins.py`:

- `OK`: wraps a value in a runtime `ObjectValue("OK", {"value": value})`.
- `&`: maps present optional values and `OK` values through a callable, while
  preserving `None` and error values.
- `?`: unwraps present optional values and `OK` values; at runtime it
  short-circuits from the current function on `None` or error values.
- `?!`: unwraps like `?`, but panics with `UnwrappedNoneFault` or
  `UnwrappedResultFault` instead of short-circuiting.

Do not broaden `_is_err_nominal` casually. Rewriting ordinary unions into
`Result` is intentionally conservative because it changes overload resolution
and display.

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

Function types can carry element tags. Explicit tags written on a function are
combined with tags inferred from the function body and with tags propagated from
callable arguments. For example, `println` contributes `Eager` and `IO`, so a
function that calls `println` gets those tags even if it did not write them
explicitly.

## Call-Site Type Checking

Call-site type checking is not a separate function type. It is triggered by the
parameter types of an overload:

- a bare unknown-shape `Function`, represented as `T.Fn()`
- a variadic tuple parameter, represented as `T.VariadicTupleType`

For an unknown-shape function parameter, the definition says "this accepts a
function, but I cannot prove the body until I know which concrete function was
passed." The analyser therefore defers validation until an invocation supplies a
concrete callable type. At that call site it temporarily treats the overload as
if the parameter had been written with that exact `Function[...]` type and as if
any needed function arguments had been inferred from the outer stack.

For example, a built-in or user function declared like:

```valiance
define dip(function: Function) =>
  $temp = top
  $function()
  $temp
end
```

can be checked at a call like `1 2 3 dip: +` as though the callable parameter
were `Function[Number, Number -> Number]`, with the two `Number` inputs sourced
from the caller's stack and the held value sourced according to the concrete
`dip` body.

Important invariants:

- The analyser detects CSTC structurally from overload parameter types. Do not
  add element-name checks for built-ins such as `peek`, `dip`, or `fork`.
- A single element can have both ordinary overloads and CSTC overloads.
- User-defined functions and built-ins use the same CSTC path. Built-ins provide
  a small static helper through `@builtin(..., call_site=...)`; user functions
  are re-analysed from their typed body at the call site.
- CSTC functions remain stack-polymorphic. They do not produce one universal
  inferred function type that works for every callable argument.
- Modifier argument order matters for CSTC overloads. The analyser does not
  permute modifier arguments when applying a call-site checked overload.
- The concrete applied overload records the runtime arity and consumed-count
  details that codegen needs. This lets `peek` pass stack values to its runtime
  implementation without consuming those stack values.

Variadic tuple parameters trigger the same deferral because their concrete
length is known only once the argument tuple type is known. At the call site,
rank/length-related `where` clauses are evaluated against the fixed tuple shape
and the resulting rank values are recorded on the applied overload.

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

## Element Tags

Element tags are represented by `ElementTag`:

```python
ElementTag(name: Symbol, args: tuple[Type, ...] = (), absent: bool = False)
```

They attach to `FunctionType`, `Overload`, and `AppliedOverload`. Do not model
them as strings: tag arguments such as `Panic[Fault]` need normal type
substitution, and absent requirements such as `!Panic` participate in function
compatibility.

The default environment predeclares the currently supported tags:

- property tags: `IO`, `Random`, `Panic[T]`, `Memoizable`
- companion tags: `Eager`, `Memoized`

Positive element tags require the called function to carry a matching positive
tag. Absent tags reject a matching positive tag. This lets parameter types such
as `Function[T -> U]<!Eager>` accept only non-eager callables.

Element tags propagate from resolved element calls and from concrete callable
arguments used by call-site type checking. This is why `map: println` is eager:
`println` is tagged `Eager`, `map`'s call-site checked overload sees that
callable argument, and the resulting applied overload/function type carries the
tag upward.

The parser accepts explicit function element tags and `eager define` attaches
`Eager`. Full source declarations for property/companion element tags and the
rule preventing direct user attachment of companion tags are still future work.

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
