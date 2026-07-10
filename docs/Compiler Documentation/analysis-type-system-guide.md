# Analysis and Type System Guide

This guide is for future agents working on the Valiance analyser and type
system. It is intentionally self-contained: do not assume the reader has
loaded any other compiler guide.

The analyser is currently branch-centric. Every analysed block starts from a
set of possible analysis branches and returns a new set of possible branches.
Ordinary, non-inference analysis is just the one-branch case.

For an approachable explanation before this exhaustive reference, read
[Understanding Valiance's type system](../maintenance/type-system.md).

## Main Files

- `src/valiance/analysis/analyser.py`
  Owns branch analysis, AST node dispatch, function inference, variable facts,
  control-flow joins, literal inference, tag propagation, and diagnostics.

- `src/valiance/analysis/builtins.py`
  Declares the default element environment and runtime implementations for
  built-ins.

- `src/valiance/analysis/annotations.py`
  Declares the compiler annotation registry and built-in annotation handlers.
  New compiler-owned annotations should register `AnnotationSpec` values here;
  future plugin loading should call the same `register_annotation(...)` hook
  rather than adding annotation names directly to the analyser.

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

`BranchSet` is the driver for analysis. It is intentionally small:

- `BranchSet.collect(...)`
  Flattens nested branch sets, drops failed branches after recording their
  diagnostics, and deduplicates equivalent surviving branches.

- Iteration and truthiness
  Handlers and helpers iterate through possible branches directly. An empty
  branch set means the analysed path is impossible or already diagnosed.

Most branch operations now live on `AnalysisBranch` and `Analyser`:

- `AnalysisBranch.emit(...)`
  Appends a typed node to the branch.

- `AnalysisBranch.source_arguments(...)`
  Sources stack or inferred/cycled function inputs for calls and elements.

- `Analyser.require_stack_top_assignable(...)`
  Validates the top of every branch in a branch set.

Prefer adding general `BranchSet` operations only when more than one node kind
will use them. Node-specific checks belong in the node handler.

## Current Analyser Structure

The analyser uses concrete handler registration:

```python
@register(SomeNode)
def _some_node(self: Analyser, node: SomeNode, branch: AnalysisBranch) -> BranchSet:
    ...
```

`Analyser._analyse_node_from_branch(...)` does exact-type lookup in
`_NODE_HANDLERS`. Internal AST nodes such as object fields, trait requirements,
try handlers, match cases, and match patterns are classified by
`_INTERNAL_NODE_TYPES`. If one reaches normal expression analysis, it produces
an `internal-node` diagnostic.

Important helpers in `analyser.py`:

- `_function_overload(...)`
  Builds function overloads with parameter names, defaults, and annotation
  diagnostics in one place.

- `_transform_overload_types(...)` and `_transform_type_children(...)`
  Rebuild overloads and type children without hand-copying every dataclass
  field. Use these when genericizing, substituting, or refining types.

- `_define_object_shape(...)`, `_define_trait_shape(...)`,
  `_object_attributes(...)`, and `_trait_requirements(...)`
  Keep local and imported object/trait registration on the same path.

- `_match_case_output(...)`, `_join_match_output(...)`, `_try_handler_output(...)`,
  and `_join_try_output(...)`
  Keep control-flow branch normalization/joins out of the node handlers.

- `_literal_branch_results(...)`
  Shared branch/result construction for list, tuple, record, and dictionary
  literals.

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

Every canonical built-in also requires `ElementDocumentation`. Core entries are
kept in `_BUILTIN_DOCUMENTATION`; dynamically generated declarations may pass
`documentation=` directly to `@builtin(...)`. Aliases inherit the canonical
metadata. `collect_builtin_references()` validates completeness and turns the
same registry into renderer-neutral `ElementReference` records.

Standard-library modules are separate from built-ins. Do not add importable
stdlib functions to `analysis/builtins.py` just because their runtime behavior is
implemented in Python. Built-ins are globally available; stdlib functions must be
imported through `import { std.name }`.

Stdlib modules live in `src/valiance/std` and can be authored three ways:

- Python-only: `module.py` declares functions with `@stdlib_element(...)`.
- Valiance-only: `module.vlnc` contains ordinary `public define` declarations.
- Mixed: `module.py` provides private native hooks and `module.vlnc` provides
  Valiance definitions that call those hooks.

`ModuleLoader` is the analysis boundary for this. For `std.foo`, it first asks
`valiance.stdlib_native` for Python-backed exports. If `src/valiance/std/foo.vlnc`
also exists, the loader analyses it in an environment that contains only that
module's native hooks, then combines the Python and Valiance exports. The native
hook names are therefore visible while analysing the stdlib module itself, but
ordinary user code still sees them only through imported module exports.

Python-backed public stdlib functions pass `documentation=` to
`@stdlib_element(...)`. Valiance-backed public stdlib definitions use contiguous
`#??` blocks. `reference_docs.py` combines both sources when generating the
language reference.

## Union-Covered Callable Overloads

`types.relations.union_dispatched_callable_plan(...)` handles the safe case
where an `OverloadSetType` is used as a `FunctionType` whose input contains a
top-level union. It expands the cartesian product of union branches, resolves
one best overload for every combination, unions each return position, and emits
a `UnionDispatchPlan` that records the selected overload index for that branch.

The plan uses reified `RuntimeTypePattern` values. Nominal patterns contain the
closed-world subtype set known during analysis, so broader numeric types,
traits, variants, and declared generic variance are available to runtime branch
matching. Data-tag requirements are also retained and checked against runtime
tag evidence. Collections are currently excluded because their element types
are not reified and inspecting a lazy collection would be observably unsafe.
Other unsupported structural patterns also reject the adaptation.

Branches whose runtime predicates overlap may select the same overload. If two
overlapping branches select different overloads, the adaptation is rejected as
ambiguous. Missing or statically ambiguous branch coverage also rejects it.

Modifier specialization in `analyser.py` must preserve the complete typed
overload set and attach the plan to `TypedFunctionNode`. Narrowing the modifier
to one overload would make the static union function type disagree with the
runtime callable value.

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
- optional-parameter defaults copied from `define` declarations on
  `Overload.param_defaults`
- whether a fallback user-defined call needs runtime multimethod selection,
  stored on `AppliedOverload.multidispatch`

Keep this on typed metadata, not on the raw parser `ElementNode`. Parser AST
nodes should remain syntax. Do not make the runtime compiler repeat type-level
overload resolution as a workaround.

Raw `ElementNode.call_args` still matters during analysis: ECS calls preserve
which arguments were named, positional, or `_` placeholders. The analyser uses
that structure to lower optional defaults only for ECS calls. Ordinary stack
calls still require the full non-modifier arity.

### Multimethods

The parser records `multi define` as `DefineNode.is_multi`. Analysis turns that
into `Overload.is_multi` after generic constraints and annotation rewrites have
been applied, then stores the updated overload on the `FunctionOverloadTyping`
that codegen will compile.

A `multi` overload must have an already visible non-`multi` fallback overload of
the same element. Every `multi` parameter must be assignable to the corresponding
fallback parameter, and return types must match exactly after normalization. This
validation happens in the analyser with `T.assignable(...)` and `T.same(...)` so
trait implementations and ordinary subtype rules stay centralized.

Runtime dispatch is decided statically. If normal overload resolution selects a
`multi` overload exactly, the typed call remains an ordinary resolved call. If it
selects a non-`multi` fallback with compatible `multi` specialisations,
`AppliedOverload.multidispatch` is set so codegen can emit the runtime dispatch
flag. If no runtime specialisation matches at execution time, the VM calls the
fallback overload selected by analysis.

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

The analyser publishes variance for object, trait, and variant declarations by
inferring usage:

- readable fields and returns are positive uses
- public writable fields count as both positive and negative uses
- function parameters are negative uses
- nested function positions flip polarity through parameters

Both positive and negative use makes the parameter invariant. Keep this
conservative: unknown or unsupported uses should not silently become variant.
Type syntax parses bare `T` as a nominal name, so the analyser rewrites type
names that match the active generic parameters into `VarType` before
registering object attributes, constructors, function definitions, function
literals, and requirements. Nested generic function literals shadow outer
generic names with the same spelling.

Object declarations synthesize their field-order constructor only when the
declaration has no definition with the same name as the object. A same-name
definition is an explicit constructor: its declared parameters are the public
constructor signature, while a writable hidden `self` local is available only
while analysing its body. Constructor field writes are tracked across control
flow, and every non-default field must be definitely initialized on every
successful path before the overload is registered. Private fields participate
in this initialization check but never add implicit public parameters to an
explicit constructor.

Surface generic parameter lists accept bounds. Unlabelled `T: U` and labelled
`T: any U` are upper bounds: overload application first solves `T` from the
actual arguments, substitutes solved variables in `U`, and then requires the
solution to be assignable to `U`. `T: above U` is a lower bound and checks the
opposite direction, requiring `U` to be assignable to the solution. The parser
stores `any` and `above` in the generic variance marker slot, and the analyser
turns them into directed `GenericConstraint` records. The same labels also map
to declaration-site covariance and contravariance for nominal generic
constructors.

Anonymous trait types provide structural behavior checks without requiring a
named trait implementation. They are represented as `AnonymousTraitType` with
inline `AnonymousTraitRequirement` signatures, for example:

```valiance
define[T] sum(
  :trait[T] =>
    extend +(:T, :T) -> T
  end +
) -> T => fold: +
```

The first anonymous-trait generic is treated as the subject type. During function
body analysis the parameter is viewed as that subject type, while the declared
overload keeps the anonymous structural requirement. The analyser also installs
the inline requirements as local-only overloads while checking the function body,
so operations such as `fold: +` type-check against the required behavior. At
call sites, relation checks use the visible overload catalogue in `Context` to
verify that the actual subject type has matching element overloads.

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
Number+++++ == ExactList(Number, 5)
```

Exact ranks do not freely widen to other exact ranks. `Number+2` is not the same
as `Number+`.

### Minimum-Rank Lists

`AtLeastList(T, n)` means a list with at least `n` ranks. Surface syntax uses
`*`:

```text
Number*   == AtLeastList(Number, 1)
Number*3  == AtLeastList(Number, 3)
Number*** == AtLeastList(Number, 3)
```

An exact list can satisfy a compatible minimum-rank list if its rank is high
enough. Generic solving may widen exact/minimum constraints to a minimum-rank
solution.

Minimum rank is also compatible with a lower-or-equal exact rank specifically
at a call parameter. This is call adaptation, not assignability: `Number*` is
still not assignable to `Number+`. When a `Number*3` argument is passed to a
`Number+2` parameter, analysis records exact target rank 2. Runtime uses the
value's reified uniform rank to call once at rank 2 or peel additional outer
ranks through vectorisation. `AppliedOverload.vectorised_target_ranks` carries
this policy alongside the minimum fixed depths. Result types retain the
uncertainty: a dynamic excess of zero or more yields `R | R*`, while a known
minimum positive excess yields a minimum-rank collection result.

### Rugged Lists

`RuggedList(T, n)` means potentially ragged nested list structure. Surface
syntax uses `~`:

```text
Number~   == RuggedList(Number, 1)
Number~2  == RuggedList(Number, 2)
Number~~~~ == RuggedList(Number, 4)
```

Rugged rank is weaker than exact/minimum list structure. When generic
constraints include rugged lists, `_combine_collections` conservatively widens
to a rugged list.

Automatic vectorisation may peel an explicit rugged collection only when the
parameter is atomic. It must not peel some rugged rank to satisfy another
collection parameter, even when the argument's declared rugged rank is greater:
rugged rank does not guarantee a uniform outer prefix. Keep this restriction in
both `_can_vectorise` and `_vectorisation_excess`, so ordinary overload matching
and explicit disambiguation agree. Exact- and minimum-rank collections retain
their collection-to-collection vectorisation rules.

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

### Exact Parameter Markers

`T.Exact(inner)` / `ExactType(inner)` is a parameter call-policy marker, not a
runtime value wrapper. Surface syntax writes it as a postfix:

```text
Number exact
Number+ exact
Function[Number exact -> Number]
```

An exact parameter accepts values assignable to its inner type but never uses
vectorisation to make a higher-ranked argument fit. Thus `Number exact` accepts
`Integer` but rejects `Integer+`, while `Number+ exact` accepts a rank-1 number
list and rejects rank 2. Generic `T exact` may bind `T` to a collection type;
the collection is then treated as one argument value rather than a
vectorisation target.

When an overload mixes exact and vectorising parameters, overload application
must record an automatic vectorisation depth for every argument, including
zeroes. A zero depth means that the argument broadcasts unchanged while other
arguments are indexed. The aggregate `AppliedOverload.vectorised` flag is not
enough to represent this case: treating every list-shaped runtime argument as a
vector target would incorrectly iterate an exact collection parameter.

The marker remains in `Overload.params` and `FunctionType.params`, so type
display and introspection preserve it. Function-body analysis strips only the
outer marker before seeding parameter variables and the cycling stack, then
reapplies it when constructing the function signature. Data-tag normalization
hoists `exact` outside the tagged type so `#tag T exact` has the same call-policy
behaviour as `Exact(Tagged(T, tag))`.

### Nested Collection Normalization

Surface type syntax rejects mixed rank postfixes unless the outer marker is a
direct superset of the inner marker. For example, `Number+*` is accepted as
`Number**`, and `Number^>` is accepted as `Number>>`, but `Number*+`,
`Number+~`, and `Number^+` are errors. Optional postfixes are a meaningful
barrier, so `Number+?+` and `Number+?*` describe collections of optional
ranked lists and are valid.

`normalize` still collapses nested collection nodes when they are created by
type builders or generic solving and the rank modes have a clear combined
meaning. It combines list-with-list and array-with-array ranks, but preserves a
list whose item type is an array: that boundary carries useful item-type facts
and flattening it would incorrectly widen the type. Use `T.normalize(...)`
before comparing types structurally. Use `T.same(...)` for canonical equality.

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

`Overload.where_clause` stores the parsed static expression body,
`Overload.param_names` maps overload parameters back to source parameter names,
and `Overload.param_defaults` stores any trailing `define` defaults for ECS
lowering.
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

### Optional-safe member access

`FieldAccessNode.optional_safe` selects a separate typing path from ordinary
field access. `_safe_field_type` requires the receiver to be structurally
optional (`Some[T] | None`), extracts `T`, resolves the field on `T`, and returns
an optional field result. If the field is already optional, the result is
flattened. Ordinary `.` deliberately does not perform this refinement.

Reads vectorise over collections of optional receivers by preserving the
collection constructor/rank and optionalising each selected field. Writes return
the original optional receiver shape: a present payload is reconstructed with
the new field, while `None` remains `None`.

A chain is checked one segment at a time. After `$x->a`, the type is still
optional, so `.b` is invalid and another safe segment is required. This makes
mixed chains predictable:

```text
$x.a->b     # ordinary access before the optional boundary
$x->a->b    # safe at both optional boundaries
$x->a.b     # rejected
```

`_strict_optional_payload_type` is intentionally stricter than general
assignability. Safe access must not silently accept a broad union that merely
contains an optional-like branch.

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

The standard environment also defines the built-in `Err` trait and records the
recoverable built-in error objects, `AssertError`, and `PanicError` as
implementations. The recoverable built-ins are `Error`, `ValueError`,
`RangeError`, `ParseError`, `DivisionByZeroError`, `IndexError`, `KeyError`,
`ShapeError`, `StateError`, `IOError`, `NotFoundError`, `AlreadyExistsError`,
`PermissionError`, `ClosedError`, `TimeoutError`, and `CancelledError`. Each has
an analyser-visible readable `message: String` field and a native constructor
with a single `message` parameter. User code can add explicit implementations
with normal `object MyError as Err => ...` syntax; the current normalizer is
still name-convention based because `normalize(...)` does not take an
environment.

The standard environment also defines message-bearing built-in `Fault` objects:
`RuntimeFault`, `ValueFault`, `RangeFault`, `ParseFault`,
`DivisionByZeroFault`, `IndexFault`, `KeyFault`, `ShapeFault`, `StateFault`,
`IOFault`, `NotFoundFault`, `AlreadyExistsFault`, `PermissionFault`,
`ClosedFault`, `TimeoutFault`, `CancelledFault`, `UnwrappedNoneFault`,
`UnwrappedResultFault`, `DuplicationFault`, and `CleanupFault`. Each has the
same native one-`String` constructor and readable `message` field as built-in
errors, but implements `Fault` rather than `Err`.

The built-in `panic` overload is generic over `F` with a nominal `Fault` bound.
This both rejects non-fault values and preserves the concrete `Panic[F]` element
tag after generic substitution. Typed `try` handlers are likewise required to
name a type implementing `Fault`; an untyped handler remains a catch-all.

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
- outer visible variables as captures, except when the function is created from
  the top-level branch
- function-local stack according to `InputMode`

Function return rules:

- `returns is None`: return the top stack value, or nothing if the stack is empty
- `returns == ()`: return no values
- explicit returns: require the final stack to be assignable to the expected
  return stack

Multiple viable function signatures become an `OverloadSetType`. The typed
function node keeps per-overload typed bodies so the typed AST remains useful.

Function types can carry element tags. Effects are accumulated on each analysis
branch and survive nested control flow, loops, `try`, `match`, collection
literals, string interpolation, validators, and ordinary/callable invocations.
Only surviving branches contribute to the final function type. For example,
`println` contributes `Eager` and `IO`, so a function that calls `println` gets
those tags even if it did not write them explicitly.

When a function has no explicit `<...>` list, inferred property and companion
tags are added to its type. An explicit list is an effect contract: declared
property tags may be broader than a concrete body effect (for example,
`Panic[Fault]` covers `Panic[RuntimeFault]`), undeclared property effects are
diagnosed, and companion tags are still inferred from compiler-controlled
features. Declared absent tags are checked against the complete surviving body
effect set.

### Contextual higher-order function literals

An untyped function literal passed to a higher-order element may be impossible
to infer in isolation. The analyser first tries ordinary inference. When that
fails, `_contextual_stack_argument_variants` uses each candidate function-typed
parameter as an expected signature and re-analyses the literal against it.

This is what permits examples such as:

```valiance
$board allNeighbors(wrapping = true) | map fn (cells) => ... end
[1, 2, 3] sum ** 2
```

The contextual path must remain a fallback. Applying it before ordinary
inference can create duplicate winners or change established stack syntax.
Candidate collapsing prefers the variant with the strongest contextual evidence
when two applications otherwise have the same substituted signature.

Niladic mapping is a distinct overload: `map: randbit` invokes the zero-argument
callable once per input item and does not pass the item to it.

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
  annotation/cast or they are syntactically returned from a function with an
  explicit collection return type

Return-context inference chooses the exact minimal rank compatible with the
declared collection return. Thus `define \\xs -> Number* => []` analyses the
literal as `Number+` and then validates it against the declared `Number*`
return. The same contextualisation is applied to explicit `return` values and
terminal `if`, `match`, and `try` branches.

Tuple, record, and dict literals reuse the same literal item machinery but build
different result types.

### Explicit parameter cycling and caller-stack functions

Explicitly declared parameters use `InputMode.CYCLE_EXPLICIT_PARAMS`. Named
parameters are installed as read-only variables, while unnamed parameters remain
stack-only. Their values also form the conceptual cycle used when the physical
function stack underflows. Inferred functions and explicit zero-parameter
functions do not cycle.

A call-site checked function such as user-defined `dip` may need additional
values from the caller once its concrete function argument is known. The applied
overload records that stack consumption; code generation marks the resulting
function as accepting stack inputs. This is not ordinary closure capture and
must not make every function share the caller's stack.

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

## Trait inheritance and structural requirements

A declaration `trait Child as Parent` imports the parent's requirements into the
child trait and makes parent default bodies available while checking child
defaults. Requirements are exposed temporarily with the receiver specialized to
the child trait; they are not left in the persistent overload table, because
that would disturb runtime overload indexes for concrete implementations.

An object implementing the child must satisfy inherited abstract requirements,
but inherited default methods need no repeated implementation. Anonymous trait
constraints use the same structural requirement machinery. The generic `find`
example relies on a requirement for `===(T, T) -> #boolean Number`; the selected
built-in equality overload is then available inside the generic body.

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
- top-level tags in an explicit function return type are guarantees made by the
  function; body checking validates the underlying value shape and codegen
  applies the declared tags to the returned runtime value
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
tag. Parameterized effects are covariant for positive requirements, so
`Panic[RuntimeFault]` satisfies `Panic[Fault]`. Absent tags reject effects whose
payload types overlap the forbidden payload, and an unparameterized absence such
as `!Panic` rejects every `Panic[...]` effect. This lets parameter types such as
`Function[T -> U]<!Eager>` accept only non-eager callables.

Element-tag type arguments participate in ordinary substitution and generic
solving. A function parameter requiring `Panic[F]`, for example, can solve `F`
from the concrete callable effect.

Element tags propagate from resolved element calls and from concrete callable
arguments used by call-site type checking. This is why `map: println` is eager:
`println` is tagged `Eager`, `map`'s call-site checked overload sees that
callable argument, and the resulting applied overload/function type carries the
tag upward.

The parser accepts explicit function element tags and `eager define` attaches
the compiler-controlled `Eager` companion tag. Source declarations support both
`tag Name as property` and `tag Name as companion`. Positive companion tags may
appear in function-type requirements, but ordinary definitions and function
literals cannot attach them directly.

Element/element disjoints and data/element disjoints are stored symmetrically in
the environment. The analyser rejects incompatible explicit or inferred element
tag sets and records data-tag/effect uses per branch so violations inside nested
expressions and function bodies are not lost during branch merges. Both
`tag #infinite disjoint Eager` and `tag Eager disjoint #infinite` are accepted.

## Annotations

Annotations are parsed as `AnnotationNode` values and handled through
`analysis/annotations.py`. The analyser calls the registry at four extension
points:

- validation for a target kind such as `define`, `fn`, `object`, `variant`, or
  `element`
- function rewriting before function analysis
- object/variant rewriting before declaration analysis
- overload metadata rewriting before overloads are registered

This keeps annotation behavior out of the analyser dispatch. To add a built-in
annotation, register an `AnnotationSpec` in `annotations.py`; future
user-defined compiler plugins should be able to register specs through the same
`register_annotation(...)` API.

Implemented annotations:

- `@recursive`: allows a function with explicit parameter and return types to
  bind `this` to the current recursive callable.
- `@self`: for object-friendly definitions, appends `$self` to the function
  body and return type when possible.
- `@@tupled`: element annotation that wraps all selected element returns into a
  fixed tuple.
- `@error`: stores an annotation-provided compile-time error on the overload;
  using a matching overload reports that message.
- `@returnAll`: changes omitted-return inference to return every remaining
  stack value.
- `@commutative`: generates argument-order overload permutations and typed
  wrapper bodies for named parameters.
- `@errType`: on objects, inserts a `message: String` field, synthesizes a
  `message -> String` object-friendly element, and records an `Err`
  implementation. On variants, it adds a `message: String` field to each member
  and records `Err` implementations for the parent variant and generated member
  types.

- `@warn` and `@deprecated`: attach non-fatal call-site warnings to selected
  overloads. A string argument customizes the warning text; otherwise the
  compiler uses a default warning message.

## Explicit `at` Vectorisation

`AtNode` consumes one source value per declared level. The number of `+`
characters on a level is the collection rank at which vectorisation must stop:
`item` stops at rank zero, `list+` at rank one, and so on. Analysis derives a
typed function parameter for each stopped value, binds named levels as ordinary
function parameters, and analyses the body with explicit-parameter cycling. This
is why both `at (list+, item) => append` and an explicit body using `$list` and
`$item` have the same stack effect.

The resulting `TypedAtNode` retains the typed callable body, selected callable
overload, fixed minimum vectorisation depths, and runtime target ranks. The
input values are removed from the surrounding stack and the callable's returns
are wrapped in the outer vector shape selected by overload application.

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
3. Add a concrete `@register(NewNode)` handler in `analyser.py`.
4. Implement the handler so it takes one `AnalysisBranch` and returns a
   `BranchSet`.
5. Use existing branch/type helpers for stack, variables, and overloads.
6. Append a `TypedNode` with the useful result type.
7. Add focused analyser tests.

A node handler should not mutate a branch. Use branch replacement helpers such
as `with_stack`, `with_variables`, `push`, `pop`, or `emit`.

If the node contains sub-blocks, call `self.analyse_block(...)` rather than
manually iterating through AST nodes.

## Diagnostics

The analyser stores error and warning strings separately. Use
`_diagnose(message, node)` for fatal compiler diagnostics and `_warn(message,
node)` for non-fatal warnings so source locations are included when available.

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
