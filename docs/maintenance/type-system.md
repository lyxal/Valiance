# Understanding Valiance's type system

This is the human-first guide to the Valiance analyser and type system. It is
written for maintainers who understand what types, generics, overloads, and
control-flow branches are supposed to accomplish, but do not yet feel confident
following the implementation.

The most important reassurance is this:

> The implementation is not a theorem prover. It is a collection of small,
> practical questions asked in a consistent order.

Most of the apparent complexity comes from several language features meeting in
one place: stack effects, generics, overloads, collections and ranks,
vectorisation, structural rows, traits, tags, and branch-local inference. Each
piece is manageable once its responsibility is separated from the others.

Use this guide before the more exhaustive
[analysis and type-system reference](../Compiler%20Documentation/analysis-type-system-guide.md).
The reference describes every supported feature. This guide explains how to
think while reading or changing the code.

## How to use this guide

You do not need to read all of it in one sitting.

- For a first mental model, read through **The relation ladder**, then skip to
  **Branch analysis is state exploration, not guesswork**.
- For a generic or overload bug, start at **Generic solving is pattern
  matching** and continue through **Choosing among successful overloads**.
- For an analyser bug, start at **Branch analysis**, then read **How an element
  call connects analysis and types** and **Function inference**.
- For day-to-day implementation work, keep **The API decision table** and
  **A final mental checklist** nearby.

### Contents

1. [The sixty-second model](#the-sixty-second-model)
2. [A map of the implementation](#a-map-of-the-implementation)
3. [Type values and normalization](#type-values-are-ordinary-immutable-trees)
4. [The relation ladder](#the-relation-ladder)
5. [Generic solving](#generic-solving-is-pattern-matching)
6. [Overload application and selection](#overload-application-step-by-step)
7. [Branch analysis and function inference](#branch-analysis-is-state-exploration-not-guesswork)
8. [Rows, collections, callables, traits, and tags](#row-polymorphism-and-field-access)
9. [Diagnostics and the API decision table](#diagnostics-preserve-evidence-instead-of-returning-bare-failure)
10. [Debugging, extension patterns, and tests](#reading-a-type-failure-without-getting-lost)

## The sixty-second model

At any point in analysis, Valiance has one or more possible **branches**. Each
branch contains a stack of types and branch-local facts.

```text
source node
    |
    v
analyse the node from every incoming branch
    |
    +-- reject impossible branches with diagnostics
    +-- refine generic or variable facts
    +-- produce one or more surviving branches
    |
    v
continue with the next source node
```

When an element is called, the analyser roughly does this:

```text
1. Look up overload declarations in Environment.
2. Source argument types from the branch stack or explicit arguments.
3. For each overload:
   a. solve its generic variables;
   b. substitute the solutions;
   c. check call compatibility;
   d. calculate a specificity score;
   e. calculate vectorisation and return types.
4. Keep the non-dominated candidate or report ambiguity/no match.
5. Push the selected return types and emit a typed AST node.
```

The type library supports that process with a few central operations:

| Question | Main API |
|---|---|
| Are these two type descriptions canonically identical? | `same(a, b)` |
| Can every value of one type be viewed as another? | `subtype(source, target, ctx)` |
| Can this value be stored or returned where that type is expected? | `assignable(source, target, ctx)` |
| Can this argument satisfy this call parameter, including generics and vectorisation? | `compatible(argument, parameter, ctx)` |
| What generic substitutions follow from a parameter pattern and an argument? | `_solve(pattern, actual, ctx)` internally |
| Can this whole overload be applied to these arguments? | `try_apply_overload(...)` / `apply_overload(...)` |
| What type represents both branch results? | `merge_types(a, b)` |

If you can identify which of those questions is being asked, most type-system
code stops looking magical.

## A map of the implementation

The type system is intentionally split by responsibility.

### `types/nodes.py`: vocabulary

This file defines immutable data records such as:

- `NominalType(Number)`
- `VarType("T")`
- `UnionType(...)`
- `CollectionType` subclasses
- `FunctionType`
- `RowType`
- `TaggedType`
- `Overload`
- `AppliedOverload`

These classes describe facts. They should contain very little policy.

### `types/builders.py`: construction and canonical form

This file provides readable constructors and normalization:

```python
T.Number
T.TypeVariable("T")
T.U(T.Integer, T.String)
T.ExactList(T.Number, rank=2)
T.Fn((T.Number,), (T.String,))
T.Row(T.TypeVariable("T"), T.Field(Symbol("name"), T.String))
```

It also owns:

- `normalize(...)`
- `same(...)`
- `show(...)`

Use builders instead of directly assembling dataclasses unless you are working
inside the representation layer.

### `types/context.py`: relationship facts

`Context` contains the facts relation functions need while answering questions:

- which nominal types implement which traits;
- trait parent relationships;
- which object types are members of variants;
- generic variance declarations;
- data-tag relationships; and
- overloads visible to structural trait checks.

A `Context` does not own source-level declarations or local variables. It is the
small relationship database used by `subtype`, `assignable`, `compatible`, and
overload solving.

### `types/environment.py`: declarations and names

`Environment` stores compiler-visible declarations:

- named overload sets;
- objects and their fields;
- constructors;
- traits and requirements;
- variants and enums;
- data tags and element tags; and
- object-friendly overload metadata.

An environment owns a `Context` and updates it when declarations imply new type
relationships.

A useful distinction is:

```text
Environment: "What declarations exist under this name?"
Context:     "What type relationships are known?"
```

### `types/relations.py`: questions and solving

This is the policy-heavy core. It owns:

- subtyping;
- assignability;
- call compatibility;
- generic constraint collection and substitution;
- collection/rank matching;
- callable compatibility;
- vectorisation typing;
- overload application and specificity; and
- branch-type merging.

### `types/stack.py`: stack-effect convenience

`TypeStack` is an immutable tuple wrapper with `push`, `pop`, and overload
application helpers. It does not perform analyser branch management.

### `analysis/state`, `analysis/declarations`, `analysis/calls`, and `analysis/analyser.py`: applying types to programs

`analysis/state` owns the immutable `BranchVariables`, `AnalysisBranch`, and `BranchSet` foundations without depending on the concrete analyser. `analysis/declarations` owns environment-changing declarations and type-shape construction behind a `DeclarationAnalyser` service. `analysis/contracts` owns annotations, tags, where clauses, lifecycle rules, and their validation behind a `ContractAnalyser` service. The public façade owns diagnostics, handler
dispatch, and the `Analyser` orchestration class. Focused private modules own:

- `_analyser_handlers.py`: concrete AST handlers;
- `calls/arguments.py`: stack and explicit argument sourcing;
- `calls/candidates.py` and `calls/selection.py`: candidate construction, overload application, ranking, and commitment;
- `calls/vectorisation.py`: rank solving, vectorisation, and result propagation;
- `calls/callable_values.py` and `calls/signatures.py`: callable narrowing, function inference, genericisation, and signature transformations;
- `control_flow/patterns.py`: pattern typing, subject narrowing, and branch joins;
- `control_flow/matches.py` and `exhaustiveness.py`: match analysis and coverage;
- `control_flow/blocks.py`, `loop_handlers.py`, `loops.py`, and `exceptions.py`: branch-producing blocks, loops, unfolding, and try handling; and
- `_analyser_utils.py`: shared branch, diagnostic, literal, and refinement
  helpers.

Together these modules own variable scopes, field access, diagnostics, and
typed AST emission.

The type library knows how types relate. The analyser knows when and why to ask.

## Type values are ordinary immutable trees

An internal Valiance type is a Python object tree. For example:

```text
Number
```

is approximately:

```python
NominalType(Symbol("Number"))
```

and:

```text
Result[T+, ValueError]
```

is approximately:

```python
NominalType(
    Symbol("Result"),
    (
        ListExactType(VarType("T"), 1),
        NominalType(Symbol("ValueError")),
    ),
)
```

There is no hidden symbolic algebra engine behind these nodes. Relation
functions recursively inspect the tree and apply explicit rules for each node
kind.

### The main node families

#### Nominal types

A `NominalType` is identified by a declared name and optional arguments:

```text
Integer
Person
Box[String]
Result[Number, ValueError]
```

Trait implementation and variant membership are looked up through `Context`.
Generic arguments use declaration-site variance recorded in that context.
Unknown generic constructors default to invariant arguments.

#### Type variables

A `VarType("T")` is a hole in a parameter pattern. It is not itself an unknown
runtime type. During overload application, solving gathers evidence for the
hole and later substitutes one concrete type.

Anonymous source generics such as `@1` are represented the same way; only their
names and source presentation differ.

#### Unions and intersections

A union means a value can come from any member:

```text
Integer | String
```

An intersection means the required facts must all hold:

```text
Readable & Printable
```

Normalization flattens nested unions/intersections and removes duplicates.
Union rules usually quantify in opposite directions depending on which side is
being checked:

```text
source union -> target: every source member must fit
source -> target union: at least one target member must accept it
```

#### Collections and rank

All collection nodes have:

```text
kind + base type + rank
```

The kinds encode exact/minimum/rugged list and exact/minimum array semantics.
Examples:

```text
Number+   exact-rank list, rank 1
Number+2  exact-rank list, rank 2
Number*   minimum-rank list, rank at least 1
Number~   rugged list
Number^   exact-rank array
```

A rank describes nesting independently from the atomic base type. This is why
solving `T+` against `Integer+2` can infer `T = Integer+`: one rank is consumed
by the pattern, leaving one rank in the generic solution.

#### Function types

A `FunctionType` stores a stack effect:

```text
Function[Integer, String -> Boolean]
```

Its parameter tuple and return tuple are ordered. It may also carry element-tag
effects.

`FunctionType(None, None)` means an unconstrained callable shape rather than a
niladic function. A niladic function has empty tuples.

#### Row types

A row type combines a base with required fields:

```text
T(.name: String, .age: Number)
```

It means: whatever `T` becomes, the value must expose at least those fields with
assignable types.

Rows are structural constraints used during inference. Declared nominal object
fields are stored in `Environment`; inferred row facts live in types and
branches.

#### Tagged types

A `TaggedType` wraps a type with data-tag requirements, including absence
requirements and collection depth. Tags are type facts, not separate stack
values.

#### Exact and atomic call-policy wrappers

`ExactType` prevents automatic vectorisation through a parameter.

`AtomicType` requires the marked argument position to be scalar. It prevents a
collection pattern such as `T+` from solving `T` as another collection and
thereby absorbing extra rank.

Neither wrapper is a value or runtime type. Both remain in overload and
callable parameter signatures so resolution can enforce them, while
function-body parameter types are produced by recursively erasing the wrappers.
Top-level wrappers in return declarations and cast targets are also erased; a
marker nested inside a callable parameter signature is preserved because it
controls calls through that callable value. Substitution must preserve the
wrappers; it must never turn `atomic` into `exact` or replace a solved collection
generic with its scalar base.

## Normalize before reasoning

Different syntax trees can describe the same effective type. `normalize(...)`
converts them to a canonical representation before most comparisons.

Normalization performs operations such as:

- flattening nested unions and intersections;
- removing `Never` from ordinary unions;
- collapsing one-member unions/intersections;
- normalizing optional and `Result` shapes;
- merging duplicate row fields;
- recursively normalizing function and nominal arguments; and
- collapsing compatible nested collection ranks.

Compatible rank collapse preserves collection-family information. Adjacent list
ranks and adjacent array ranks combine, but a list whose item is an array keeps
that item boundary. Arrays can still satisfy corresponding list requirements
through relation checks without widening the canonical type itself.

For example, construction through `T.U(...)` normalizes immediately:

```python
T.U(T.Integer, T.Never()) == T.Integer
```

The practical rule is:

> Do not use raw dataclass equality as a semantic type relation unless both
> values are already guaranteed canonical. Use `same(...)`.

`same(a, b)` is deliberately boring: normalize both sides and compare their
immutable structures. That makes it the foundation for more permissive
relations.

## The relation ladder

The four most important relation APIs are similar but not interchangeable.
Think of them as increasingly call-oriented questions.

```text
same
  subset of
subtype
  subset of most cases of
assignable
  subset of most cases of
compatible
```

This is a mental model, not a formal theorem. Valiance has pragmatic special
cases, so the code should remain the source of truth.

## `same`: canonical identity

```python
T.same(source, target)
```

Use `same` when two facts must describe exactly the same canonical type.
Examples:

- checking invariant generic arguments;
- avoiding a needless branch refinement;
- detecting whether substitution changed a parameter; and
- deduplicating equivalent inferred signatures.

`same(Integer, Number)` is false even though an integer can be used as a number.

## `subtype`: safe subsumption

```python
T.subtype(source, target, ctx)
```

`subtype` asks:

> Can every value represented by `source` be viewed as a value of `target`
> without call adaptation or implicit wrapping?

Its rules include:

- `Never` is a subtype of every type;
- `Integer <: Real <: Number`;
- nominal trait implementations;
- variant member to variant parent;
- declared generic variance;
- union/intersection rules;
- collection rank relationships;
- row satisfaction;
- structural anonymous traits; and
- tag requirements.

Example:

```python
T.subtype(T.Integer, T.Number, ctx)  # True
T.subtype(T.Number, T.Integer, ctx)  # False
```

The implementation is practical rather than a pure academic calculus. Tuple
and collection checks may call `assignable` for their contained types because
that matches language behaviour.

## `assignable`: storage and result acceptance

```python
T.assignable(source, target, ctx)
```

`assignable` asks:

> May a value currently known as `source` be stored, returned, assigned, or
> consumed where `target` is required?

It starts with `same` and `subtype`, then adds language conveniences such as:

- implicit present-value acceptance by optional types;
- `None` acceptance by optionals;
- success/error acceptance by `Result`;
- the tagged boolean-number to boolean-integer special case; and
- union distribution.

Typical analyser uses include:

- variable reassignment;
- declared return checking;
- conditions requiring Boolean;
- branch-local field constraints; and
- generic bound validation.

Use `assignable(actual, expected)`, in that order. Reversing the arguments is a
common and subtle bug.

### Direction mnemonic

Read the call as a sentence:

```text
assignable(actual value type, destination type)
```

or:

```text
"Can this source go into that target?"
```

## `compatible`: call parameter acceptance

```python
T.compatible(argument, parameter, ctx)
```

`compatible` asks the broadest question:

> Can this argument participate in a call whose parameter has this type?

It includes assignability, then adds call-only behaviour:

- generic pattern solving;
- callable compatibility;
- overloaded callable selection;
- vectorisation;
- intersection/union parameter handling; and
- atomic scalar validation and rank-preserving generic solving.

Do not use `compatible` for ordinary variable assignment. A collection may be
compatible with a scalar parameter because the call can vectorise, but that
does not mean the collection can be stored in a scalar variable.

This distinction prevents many accidental type-system widenings.

## Generic solving is pattern matching

Generic solving is the most intimidating-looking part of the implementation,
but its core operation is straightforward:

```text
match parameter pattern against actual argument
collect evidence for every type variable
```

Internally, `_solve(pattern, actual, ctx)` returns:

```python
{
    "T": [candidate_type_1, candidate_type_2, ...],
    "U": [candidate_type, ...],
}
```

or `None` if the shapes cannot match.

It deliberately does **not** immediately decide the final value for each
generic. Every parameter gets a chance to contribute evidence first.

### Example: one occurrence

Given:

```text
define[T] identity(x: T) -> T
```

and an `Integer` argument:

```text
pattern: T
actual:  Integer
result:  T -> [Integer]
```

The combined substitution is:

```text
T = Integer
```

### Example: repeated generic

Given:

```text
define[T] choose(x: T, y: T) -> T
```

and arguments `Integer, Real`:

```text
first parameter:  T -> [Integer]
second parameter: T -> [Integer, Real]
```

The combiner sees that `Integer` is assignable to `Real`, so the shared solution
is `Real`.

With `Integer, String`, neither type accepts the other and there is no safe
single generic solution. The overload fails.

This is important:

> Generic evidence combination is not branch merging.

`merge_types(Integer, String)` may produce `Integer | String` because a branch
result can be either. `_combine(Integer, String)` returns failure because one
invocation of a repeated `T` requires one coherent substitution.

### Example: a generic inside a nominal type

For:

```text
Box[T]
```

against:

```text
Box[String]
```

solving recurses through matching nominal names and arguments:

```text
T -> [String]
```

Different nominal names do not match through generic solving alone. Trait and
subtype rules are checked at other stages.

### Example: collection rank peeling

For parameter `T+` and argument `Integer+2`:

```text
parameter rank: 1
argument rank:  2
difference:     1
solution:       T = Integer+
```

The solved `T` is the remainder after the parameter consumes its declared rank.
This rule is implemented by `_solve_collection(...)`.

### Example: optional generic

For `T?` and `Integer`:

```text
T = Integer
```

For `T?` and `None`, `None` supplies no evidence for `T`. Another parameter or
context must determine it. This avoids inventing a payload type from absence.

### Example: a row pattern

For:

```text
T(.name: U)
```

against an inferred row:

```text
Person(.name: String, .age: Integer)
```

solving collects:

```text
T = Person
U = String
```

The actual row may contain additional fields. Every required pattern field must
be present.

### Example: function parameters

Function arguments are sometimes deferred until non-function parameters solve
shared generics. Consider a parameter shaped like:

```text
Function[T -> U]
```

alongside another parameter that determines `T`. Solving the ordinary argument
first allows the expected function input to become concrete before callable
compatibility is checked.

That deferral is intentional. Without it, an overloaded function argument may
appear ambiguous merely because the surrounding call has not solved its input
type yet.

## Combining and substituting generic evidence

After collecting evidence, overload application performs two separate steps.

### 1. Combine evidence

`_combine_all(...)` reduces each variable's list to one type.

The combiner prefers:

- exact equality;
- the wider of two assignable types;
- compatible optional payloads; and
- conservative collection widening.

It returns `None` when no coherent solution exists.

### 2. Substitute

`_substitute(type, substitution)` recursively replaces variables throughout:

- nominal arguments;
- unions and intersections;
- tuples;
- rows;
- collections;
- function parameters and returns;
- structural trait requirements;
- tags; and
- exact/atomic wrappers.

Substitution preserves both wrappers around the substituted inner type. Marker
erasure happens only when deriving the value type visible in a function body.

The result is then checked again. Solving proposes a substitution; compatibility
validates that the substituted overload really accepts the arguments.

## Generic bounds and variance

`GenericConstraint` records:

```text
name + bound + variance
```

After substitution, `_generic_constraints_met(...)` checks each solution.

- Covariant/default bound: `solution` must be assignable to `bound`.
- Contravariant/`above` bound: `bound` must be assignable to `solution`.
- Invariant bound: solution and bound must be `same`.

For example, with `T: Vehicle`, a `Car` solution is valid when `Car` can be
assigned to `Vehicle`.

Variance also applies to declared generic nominal types. `Context` stores one
variance entry per generic argument. When two nominal types have the same name:

- covariant arguments are checked actual-to-expected;
- contravariant arguments are checked expected-to-actual; and
- invariant arguments must be canonically equal.

Unknown or mismatched variance declarations safely fall back to invariance.

## Overload application, step by step

The best public debugging entry point is:

```python
attempt = T.try_apply_overload(overload, args, ctx)
```

It returns either an `AppliedOverload` or structured mismatch evidence.
`apply_overload(...)` is the convenience form that returns only success or
`None`.

The algorithm in `try_apply_overload(...)` can be read as a pipeline.

### Step 1: check arity and explicit hints

The number of arguments must match the overload. Explicit vectorisation or
disambiguation hints must also have the correct length.

### Step 2: adapt explicitly disambiguated arguments

When source syntax provides a stop type/rank, each argument is checked against
that hint. The analyser records vectorisation depth and any dynamic target rank.

### Step 3: collect generic evidence

Each parameter pattern is solved against its argument. Function-shaped
arguments may be deferred until ordinary arguments solve shared generics.

### Step 4: combine evidence

Every generic variable receives one substitution through `_combine_all(...)`.
Conflicting evidence becomes a `GENERIC_CONSTRAINT` mismatch.

### Step 5: revisit deferred callables

Expected function types are substituted and overloaded callable arguments are
checked with the now-concrete input information.

### Step 6: substitute parameters and returns

The overload's declared types become concrete for this application.

```text
original:    (T, T) -> T
substitute:  T = Real
instantiated:(Real, Real) -> Real
```

### Step 7: validate declared generic bounds

All generic constraints must hold after substitution.

### Step 8: validate every argument

Each actual argument must be `compatible` with its instantiated parameter.
This catches substitutions that matched structurally but do not satisfy the
full call rules.

### Step 9: score specificity

Each argument/parameter pair receives a `Specificity` category. Lower enum
values are better:

```text
EXACT
EXACT_GENERIC
TAGGED
OPTIONAL
INTERSECTION
TRAIT
RANK
UNION
VECTORISED
CALL_SITE_CHECKED
NO_MATCH
```

The first applicable category in `_match_specificity(...)` wins.

### Step 10: calculate automatic vectorisation

If an argument only matches by vectorisation, the result records how many
collection levels the runtime must traverse and any target rank it must stop at.
Arguments that do not vectorise receive depth zero and broadcast unchanged.

### Step 11: calculate actual returns

`returns` means the declared return tuple after generic substitution.

`actual_returns` means the stack result after call adaptation, especially
vectorisation.

The distinction matters. A scalar overload may declare `Number`, while a
vectorised call actually returns `Number+`.

### What `AppliedOverload` tells you

An `AppliedOverload` is the analyser/compiler contract for one call. Important
fields include:

- `overload`: original declaration;
- `substitution`: solved generic map;
- `params`: instantiated parameters;
- `returns`: instantiated declared returns;
- `actual_returns`: adapted stack returns;
- `scores`: specificity vector;
- `vectorised_depths` and `vectorised_target_ranks`;
- `rank_values` from `where` clauses;
- `runtime_static_values` for hidden compile-time numbers and type-derived
  static arguments;
- propagated `element_tags`; and
- multidispatch metadata.

When runtime behaviour is wrong, first check whether this record was already
wrong. The VM should execute this plan, not rediscover it.

## Choosing among successful overloads

Several overloads may apply successfully. Success is not the same as selection.

Candidate A dominates candidate B when:

1. every specificity score in A is no worse than the corresponding score in B;
2. at least one score is better; or
3. the score vectors tie and A's instantiated parameters are strictly more
   specific.

This is a Pareto-style comparison, not a sum. One excellent parameter match
does not compensate for a worse match elsewhere.

Example:

```text
candidate A scores: (EXACT, TRAIT)
candidate B scores: (UNION, TRAIT)
```

A dominates B.

But:

```text
candidate A scores: (EXACT, UNION)
candidate B scores: (TRAIT, EXACT)
```

neither dominates the other. Unless another language rule resolves the tie,
the call is ambiguous.

The analyser adds source-language priorities around the type-level comparison,
such as external definitions taking priority over object-friendly defaults.
These priorities belong in call candidate selection, not in the generic type
relations.

## Stack application is overload application plus input removal

`apply_overload_to_stack(...)` is a convenience for pure type-stack work:

```text
stack before: [String, Integer, Real]
overload:              (Integer, Real) -> Number
stack after:  [String, Number]
```

With `infer_missing=True`, missing lower inputs are taken from the overload's
parameter types and returned as inferred function inputs. This utility is useful
for type-level tests.

The full analyser normally uses `AnalysisBranch.source_arguments(...)` because
it must also update branch inputs, explicit-parameter cycling state, variables,
and diagnostics.

## Branch analysis is state exploration, not guesswork

An `AnalysisBranch` is one internally consistent possible state. Its important
fields are:

- `stack`: current `TypeStack`;
- `inputs`: function inputs inferred or declared so far;
- `variables`: branch-local variable facts;
- `typed_body`: typed AST emitted along this path;
- `input_mode`: how missing stack arguments may be sourced;
- `cycle_params` and `cycle_index`;
- element/data-tag effects;
- loop break type;
- whether a direct `Never` stack value makes the path terminal; and
- branch diagnostics.

The record is immutable. Handlers return replacements rather than mutating
shared state.

A `BranchSet` is simply a tuple of possible branches with collection and
deduplication helpers.

### The handler contract

Conceptually every AST handler has this shape:

```python
@register(SomeNode)
def handle(
    analyser: Analyser,
    node: SomeNode,
    branch: AnalysisBranch,
) -> BranchSet:
    ...
```

`analyse_node(...)` calls the handler once per surviving input branch and
collects the outputs.

Ordinary deterministic code still uses a branch set of size one. Do not add a
second non-branching analysis path.

## Why branches multiply

Branches represent genuinely different static possibilities, for example:

- more than one viable inferred function signature;
- control-flow alternatives;
- overload-specific generic refinements;
- match cases; and
- call-site checked function possibilities.

A branch is not merely a speculative error-recovery attempt. If a source path
is possible, its facts must be preserved until a language rule joins or rejects
it.

A direct `Never` value is different from an ordinary branch result. It means
that the path cannot return normally, so `analyse_node(...)` preserves the typed
prefix but does not analyse later nodes on that path. Nested constructs split
terminal paths from continuing paths before checking conditions or call
arguments. This prevents unreachable code from producing follow-on diagnostics
or turning `Never` back into an ordinary result type.

When nested analysis has already emitted a primary diagnostic and yields no
branch, the enclosing construct must not add a generic wrapper diagnostic such
as “condition must be boolean” or “literal item must leave a value”. Wrapper
diagnostics still apply when a live nested branch exists but has the wrong
shape or type.


## Input modes explain function inference

`InputMode` controls what happens when an element needs more stack arguments
than a branch currently has.

### `TOP_LEVEL`

Underflow is an error. No values may be invented.

### `INFER_INPUTS`

Used by a function with omitted parameters:

```text
fn => ... end
```

When an element needs a missing input, its parameter type becomes a newly
inferred function input.

### `CYCLE_EXPLICIT_PARAMS`

Used by a function with explicit non-empty parameters:

```text
fn (x: Number, y: String) => ... end
```

The parameters are initially placed on the body stack. If the body later
underflows, explicit parameters can be sourced cyclically in declared order.
Named variable reads also use the parameter frame.

### `NILADIC`

Used by `fn () => ... end`. Underflow is an error.

### `source_arguments(...)`

This method centralizes the rule:

```text
enough stack values -> pop them
missing + infer mode -> append inferred inputs
missing + cycle mode -> source explicit parameters
otherwise -> fail
```

Element handlers should not manually reproduce this logic.

## Variables are branch-local facts

`BranchVariables` separates four readable scopes:

```text
block locals -> function locals -> parameters -> captures
```

Writes follow language rules:

- existing locals require assignability;
- constants cannot be reassigned;
- parameters are read-only;
- assigning to a captured name creates/shadows a function local; and
- new names become block or function locals according to the caller.

Variable facts belong to branches because different control-flow paths may
assign different types. The global `Environment` must not contain mutable local
state.

## Joining branches

When two control-flow paths reconverge, their stacks and pre-existing variables
must be joined.

### Type joins

`merge_types(a, b)` computes a safe branch result:

- if one is `None`, make the other optional;
- if one is assignable to the other, keep the wider type; otherwise
- construct a normalized union.

Examples:

```text
merge(Integer, Real)   -> Real
merge(Integer, String) -> Integer | String
merge(None, String)    -> String?
```

### Stack joins

`merge_stacks(...)` aligns stacks from the top. A shorter branch is padded on
the left with `None`, making missing lower outputs optional.

### Variable joins

`BranchVariables.merge_against(left, right, before)` preserves only variables
that existed before the split. This prevents a name defined in only one arm from
silently becoming available afterward.

For each surviving pre-existing variable, differing branch types are merged
with `merge_types(...)`.

## A complete branch example

Consider:

```text
fn =>
  if true => 1
  else => "x"
  end
end
```

A simplified trace is:

```text
initial function branch
  stack: []
  inputs: []
  mode: INFER_INPUTS

condition branch
  true produces #boolean Integer
  condition consumes it
  body input stack: []

then branch
  stack: [Integer]

else branch
  stack: [String]

join
  stack: [Integer | String]

function signature
  Function[ -> Integer | String]
```

Nothing guessed that union in advance. It appears because two valid branch
outputs were joined.

If one condition branch were not Boolean, it would be an error rather than
silently discarded. A possible invalid path is still an invalid program.

## How an element call connects analysis and types

The analyser's element-call path is best understood as five phases.

### 1. Name lookup

`Environment.overloads_for(name)` returns the visible overload tuple.

Unknown name handling belongs here, before type solving.

### 2. Argument sourcing

`element_argument_sources(...)` chooses either:

- ordinary stack sourcing; or
- explicit-call preparation and parameter-order merging.

Modifier function arguments are inserted in their declared parameter slots.
Each `ElementArguments` record retains the originating overload index and branch.

### 3. Candidate construction

`element_call_candidates(...)` calls `_apply_overload_to_branch(...)` for every
source. This layer combines type-level overload application with branch
specialization, call-site checking, tag overlays, and multidispatch marking.
For call-site checked built-ins, the concrete overload may also record
`runtime_static_values`: hidden constants derived during analysis, such as the
two independently selected argument-group arities for `correspond` or numeric
results produced by a `where` clause. These are compiler metadata, not
source-visible values or part of semantic type identity.

### 4. Winner selection

`select_call_winners(...)` applies specificity and source-language priority.
It diagnoses no-match and ambiguous cases. During input inference, distinct
specializations may deliberately survive as separate branches.

### 5. Commit

`commit_element_candidate(...)`:

- applies annotation warnings/errors;
- computes annotation-adjusted returns;
- analyses element extensions;
- pushes `actual_returns`; and
- emits `TypedElementNode` with the exact overload index and call plan.

This last point is crucial: preserve the selected overload index. Recovering an
index later through structural equality can collapse distinct declarations.

## Function inference, step by step

`_analyse_function_literal(...)` turns a function body into one or more typed
signatures.

### 1. Choose input mode

- no parameter list -> infer inputs;
- empty parameter list -> niladic;
- explicit parameters -> cycle explicit parameters.

### 2. Build variable and stack state

Named parameters become read-only `BranchVariables.parameters` entries.
Explicit parameter value types are placed on the initial function stack and
stored in `cycle_params`.

### 3. Prepare local type relationships

Structural anonymous-trait requirements publish temporary overloads in a child
environment. Recursive functions publish `this` when their annotation and
explicit types make that safe.

### 4. Analyse the body normally

A nested `Analyser` processes the function body as a branch-set transformation.
There is no separate mini type checker for functions.

### 5. Build signatures from surviving branches

`_function_signatures(...)` determines:

- inputs;
- declared or inferred returns;
- element-tag effects;
- rank/where-clause values; and
- the typed body associated with each distinct overload.

### 6. Assemble the callable type

One signature becomes `FunctionType`; multiple signatures become
`OverloadSetType` with corresponding typed bodies.

## Optional-safe field access

Optional-safe access is a field operation plus a strict optional boundary. The
analyser does not make ordinary row lookup understand `Some[T] | None`. Instead,
it first proves that the receiver has the canonical optional structure, extracts
the present payload, and performs normal field lookup on that payload.

The result rule is:

```text
(Some[T] | None)->field: U   = Some[U] | None
(Some[T] | None)->field: U?  = U?
```

The second line is flattening. It prevents every safe segment from adding
another optional layer when the field is already optional. This rule is also why
`$x->a.b` is rejected: after the safe segment the receiver of `.b` is optional.

For collections, safe reads preserve the collection shape and apply the rule to
each item. Safe writes preserve the receiver's optional type and reconstruct the
present payload; the absent branch remains `None`. Keep this behavior in the
analyser rather than treating `->` as syntactic sugar for `?`, because `?` has
function-return control flow and different effects.

## Row polymorphism and field access

Row types let field use contribute type information without requiring a known
nominal object immediately.

For a body such as:

```text
fn => $.x squared end
```

the analyser can introduce an inferred input resembling:

```text
@1(.x: @2)
```

The field access says the input has an `.x` field. `squared` then constrains the
field type to a numeric-compatible type.

### Declared nominal fields versus inferred rows

- `Environment` answers fields declared by known objects.
- A `RowType` records structural field requirements on inferred/generic values.
- Branch refinement replaces old type variables with stronger row facts across
  the stack, inputs, variables, and already-emitted typed nodes.

### Row relation direction

A source row satisfies a target row when:

1. the source base is a subtype of the target base; and
2. every target-required field exists in the source with an assignable type.

Extra source fields are allowed.

## Collections, vectorisation, and exactness

Collection compatibility has two related but distinct jobs.

### Collection subtyping

This asks whether one collection description can be viewed as another by kind,
rank, and base type. Exact arrays may be viewed as exact lists of the same rank;
exact ranks can satisfy compatible minimum-rank requirements; rugged list types
are intentionally weaker.

### Generic collection solving

A pattern such as `T+` consumes collection rank and binds `T` to the remainder.
This is how one generic overload can operate at arbitrary outer ranks.

### Automatic vectorisation

When an argument does not directly fit a scalar parameter but its collection
base does, `compatible(...)` may accept the call by vectorisation.

Overload application records:

- how many ranks to traverse per argument;
- which arguments broadcast at depth zero; and
- any dynamic target ranks.

Return types are wrapped back to the resulting vectorised shape.

### Exact parameters

`Exact(parameter)` removes vectorisation as an acceptance path for that
parameter. It does not mean nominal equality; after stripping the wrapper,
ordinary assignability still applies.

The wrapper is call-policy metadata. It is preserved in overload/function
signatures and recursively erased from the value type used to analyse the
function body.

Use exactness when a collection is meant to be passed as one value rather than
mapped elementwise.

### Atomic parameters

`Atomic(parameter)` requires the corresponding argument position to be proven
scalar. For `T atomic +`, generic solving must match the declared collection
rank directly and solve `T` from the scalar base; it may not peel excess rank
into `T`.

Atomic evidence is validation evidence. Ordinary occurrences of `T` retain one
consistent solution, and an atomic occurrence checks that same solution rather
than replacing it with a scalar-base view. When the atomic occurrence is the
only evidence, its scalar argument can provide a fallback solution.

During generic function analysis, scalar guarantees declared by atomic
parameters are tracked separately from value types. This lets the body see an
ordinary `T`/`T+` while preventing an unmarked generic wrapper from forwarding a
possibly collection-valued generic into an atomic overload.

## Callable compatibility

Function parameters need behaviour-based checking, not ordinary nominal
subtyping.

For an expected function:

```text
Function[Input -> Output]
```

the checker asks whether the argument callable can be invoked with `Input` and
whether its resulting values are compatible with `Output`.

For an overloaded callable, the expected input types may select one overload.
When expected inputs contain unions, `union_dispatched_callable_plan(...)` can
resolve every cartesian union branch statically and build a runtime dispatch
plan.

Runtime dispatch then only identifies which pre-resolved branch the concrete
values belong to. It does not rerun overload resolution.

Element tags on function types are checked at the same boundary. A callable
must provide required effects and must not violate absence requirements.

## Traits, variants, and structural requirements

### Nominal traits

`Context.implements(type_name, trait_name)` follows direct implementations and
trait parents. `subtype(...)` uses this relationship for nominal values.

### Variants

`Context.variant_members` maps a member nominal type to its variant parent. This
makes a member a subtype of the variant.

### Trait inheritance

A child trait accumulates the parent's structural requirements. Default methods
are checked with receiver-specialized views of those requirements, then reused by
concrete child implementations. Do not permanently register the temporary
receiver-specialized overloads: environment overload ordering is also the basis
for compiled runtime indexes.

Conformance checks distinguish abstract requirements from inherited defaults. An
object must provide every abstract operation somewhere in its implementation
chain, but it need not repeat a default body supplied by the trait hierarchy.

### Anonymous structural traits

An `AnonymousTraitType` contains required element overloads. Satisfying it means
finding visible structural overloads whose parameter/return shapes meet every
requirement under one consistent generic substitution.

The solver explores complete combinations of candidate overloads rather than
committing to the first candidate for each requirement. This matters when two
requirements share a generic: an early locally valid choice may conflict with a
later requirement while another overload yields one coherent substitution.
Requirement order and structural-overload insertion order must not change the
result.

If several complete solutions remain, their generic evidence is combined using
the active `Context`. Compatible solutions widen to their common assignable type
(for example `Car` and `Vehicle` become `Vehicle` when `Car <: Vehicle`), while
incompatible solutions make the structural match ambiguous and therefore fail.
Candidate-local generic bounds are checked through ordinary overload application,
so a generic structural implementation cannot bypass its own constraints.

Trait generic names are local binders. Substitution must not capture them, and
`same(...)` compares traits modulo alpha-renaming of those local names. The same
capture rule applies to generic constraints local to a required overload.

This is structural because the source type does not need to declare a named
implementation. It must simply provide the required callable behaviour.

### Structural-generic verification rules

Rows, anonymous traits, and anonymous source generics share the ordinary generic
solver, so their tests must cover interactions rather than isolated syntax. The
repository protects these laws:

- row width is covariant: extra source fields are allowed;
- row depth follows assignability for concrete fields and recursively solves
  generic fields;
- repeated named or anonymous variables produce one coherent substitution;
- `compatible` may solve free row variables for a call, while `assignable` does
  not allow an unresolved generic to escape into stored state;
- declaration-site covariance, contravariance, and invariance continue to apply
  when their arguments contain rows or anonymous traits;
- function-shaped solving substitutes inferred row variables before checking
  whether the actual callable accepts the solved input shape;
- trait parameters are contravariant and trait returns are covariant;
- bound variables in independently created traits and overloads remain scoped;
- alpha-renaming does not alter structural meaning; and
- every generated relation obeys `subtype(a, b) => assignable(a, b) =>
  compatible(a, b)`.

Focused examples live in `tests/test_structural_types.py`, source-level inference
and diagnostics live in `tests/test_structural_analysis.py`, and the
`structural-types` fuzz target composes these features across generated contexts.

## Data tags and element tags

The two tag systems serve different purposes.

### Data tags

`DataTag` decorates values/types. A requirement can say a tag must be present or
absent, optionally at a collection depth.

Normalization merges nested tagged wrappers. Relation checks enforce tag
requirements and context-declared disjointness.

### Element tags

`ElementTag` decorates callable behaviour/effects. They appear on `FunctionType`,
`Overload`, and `AppliedOverload`.

During typed-node emission, positive element tags are accumulated on the
analysis branch. Function inference then derives or validates the function's
final effect set.

Keep value facts and effect facts separate even when their source syntax looks
similar.

## Diagnostics: preserve evidence instead of returning bare failure

`try_apply_overload(...)` returns an `OverloadAttempt` containing an
`OverloadMismatch` when possible. Reasons include:

- arity;
- argument type;
- generic constraint;
- where clause;
- disambiguation;
- vectorisation;
- named/default arguments;
- call-site checks; and
- result mismatch.

This structure should be preferred when improving diagnostics. A plain `None`
loses the reason and the argument index that got furthest.

At analyser level, failed branches carry structured `Diagnostic` records while
human-facing strings are accumulated at the session boundary.

Do not emit speculative diagnostics for candidates that are expected to lose.
Diagnose after the candidate set proves that no valid interpretation survives.

## The API decision table

Use this table when writing analyser or type code.

| You need to know... | Use... | Avoid... |
|---|---|---|
| whether two canonical types are identical | `same` | raw equality on unnormalized values |
| whether a value can be viewed as a supertype | `subtype` | `compatible`, which may vectorise |
| whether a value can be assigned/returned | `assignable` | `_solve` directly |
| whether a call argument fits a parameter | `compatible` | `assignable` alone |
| whether an entire overload applies | `try_apply_overload` | manually solving each parameter |
| why an overload failed | `try_apply_overload` | `apply_overload`, which drops evidence |
| the selected result of one overload set | `resolve_overload_result` | choosing the first successful overload |
| all non-dominated stack candidates | `apply_overload_candidates_to_stack` | assuming a unique result |
| the common type of branch outputs | `merge_types` | generic `_combine` |
| the common solution for repeated generic evidence | `_combine_all` inside relation code | `merge_types`, which may create an invalid union solution |
| a new analyser call input | `AnalysisBranch.source_arguments` | manually slicing the branch stack |
| a new branch state | `replace`, `push`, `pop`, `emit`, helpers | mutating branch fields |
| declarations by name | `Environment` | local branch maps |
| subtype/trait/tag relationship facts | `Context` | ad hoc global lookups in relation functions |

Private helpers are exported from `valiance.types` for existing internal users,
but new code should prefer the highest-level API that answers the whole
question.

## Reading a type failure without getting lost

When a program reports an overload error, write down these facts in order.

### 1. Actual stack/arguments

Use rendered types, but also inspect their node kinds and ranks.

```text
actual: [Foo, Integer+]
```

### 2. Candidate declaration

```text
parameter pattern: (T, T+)
returns:           T
```

### 3. Generic evidence

```text
first T:  Foo
second T: Integer
```

Does `_combine_all` have one safe solution? If not, the failure is generic
coherence, not assignability.

### 4. Substituted signature

Record the final concrete parameters and returns.

### 5. Compatibility and scores

For each argument, note:

- direct assignability;
- vectorisation;
- specificity category; and
- exact/tag/trait/rank rules involved.

### 6. Candidate competition

A valid candidate may still lose to a more specific one or remain tied.

### 7. Typed-node payload

Confirm the chosen overload index, argument order, substitution, vectorisation
plan, and actual returns survived into typed AST.

### 8. Runtime only after that

If all static facts are correct, inspect code-generation and execution. Do not
patch the VM to compensate for a wrong or missing typed decision.

## A Python scratchpad for relation experiments

Small direct tests are often faster than running a whole source program.

```python
from valiance import types as T

ctx = T.Context()

assert T.same(T.U(T.Integer, T.Never()), T.Integer)
assert T.assignable(T.Integer, T.Number, ctx)
assert not T.assignable(T.Number, T.Integer, ctx)

T_var = T.TypeVariable("T")
overload = T.Overload(
    params=(T_var, T_var),
    returns=(T_var,),
)

attempt = T.try_apply_overload(
    overload,
    (T.Integer, T.Real),
    ctx,
)

assert attempt.applied is not None
assert attempt.applied.substitution == {"T": T.Real}
assert attempt.applied.actual_returns == (T.Real,)
```

For repository tests, place general relation-focused cases in
`tests/test_types.py`. Put row/anonymous-trait/anonymous-generic interaction
regressions in `tests/test_structural_types.py`. Use analyser tests, including
`tests/test_structural_analysis.py`, when branch facts, names, diagnostics, or
typed nodes matter. Use runtime tests only when execution is part of the
behaviour being protected.

## Common misconceptions

### “A generic is an unknown that gradually mutates.”

It is better to think of a generic as a named pattern hole. Each candidate call
collects evidence into a fresh substitution. Types and branches remain
immutable.

### “If two types disagree, union them.”

Only branch joins normally do that. Repeated occurrences of one generic need a
single coherent solution; unrelated evidence should reject the overload.

### “Subtype, assignable, and compatible are synonyms.”

They answer different questions. Compatibility may accept vectorisation or
callable adaptation that would be wrong for variable storage.

### “Branches are only for `if`.”

Branches represent any distinct static possibility, including inferred
signatures and overload-specific refinements.

### “The VM can choose the right overload from runtime values.”

Ordinary overload choice is static. Runtime dispatch is allowed only when the
analyser emitted a specific dispatch plan, such as union multidispatch.

### “The environment should store every known fact.”

Only stable declarations belong there. Local variables and control-flow
refinements belong to branches.

### “Raw dataclass equality is enough.”

Canonicalization matters. Use `same`, and use the relation appropriate to the
question.

### “An exact parameter requires exact type equality.”

`ExactType` blocks vectorisation; its inner type still uses assignability.

## Safe extension patterns

### Adding a new type node

1. Add an immutable node in `vtypes/nodes.py`.
2. Add a readable builder if source or built-ins will construct it.
3. Define normalization and display.
4. Decide its behaviour in `same`, `subtype`, `assignable`, `compatible`,
   `_solve`, and `_substitute`.
5. Decide how it merges across branches.
6. Add focused relation tests for both directions and negative cases.
7. Add analyser/runtime tests only where the node affects source behaviour.

Do not add a node and rely on fall-through behaviour. Silent “not compatible”
results are difficult to diagnose and often miss substitution or display paths.

### Adding a new relation rule

1. State the exact question being changed.
2. Put the rule in the narrowest relation that should accept it.
3. Test that broader relations inherit it where intended.
4. Test that narrower relations remain false.
5. Check both argument directions.
6. Check unions, generics, and collections if the rule can nest.

For example, a call-only adaptation belongs in `compatible`, not `assignable`.

### Adding overload behaviour

Prefer extending `try_apply_overload(...)` or its focused helpers so the result
continues to produce:

- mismatch evidence;
- substitutions;
- instantiated signatures;
- specificity;
- vectorisation data; and
- actual returns.

Do not create a parallel overload solver in the analyser or VM.

### Adding branch behaviour

Implement the node as a `BranchSet -> BranchSet` transformation. Preserve
possible invalid paths until they are diagnosed. Join only at the language's
control-flow convergence point.

## Tests by responsibility

Use the smallest test layer that observes the rule.

### `tests/test_types.py`

For:

- normalization;
- `same`, subtype, assignability, compatibility;
- generic solving;
- variance;
- rank rules;
- overload scoring and dominance;
- callable compatibility; and
- union dispatch plans.

### `tests/test_structural_types.py`

For generated and focused interactions among:

- row width/depth relations;
- named and anonymous generic solving;
- anonymous-trait requirement backtracking;
- alpha-renaming and capture avoidance;
- generic bounds and nominal subtype context;
- variance through structural arguments; and
- assignability versus call compatibility.

### `tests/test_structural_analysis.py`

For source-level structural inference, overload-order independence, and
structural-generic diagnostics.

### `tests/test_analyser.py`

For:

- branch creation and joins;
- input inference;
- variable refinement;
- element lookup;
- diagnostics;
- field/row inference;
- selected overload payloads; and
- function signatures.

### `tests/test_runtime.py`

For user-visible execution after analysis and compilation.

### `tests/test_bytecode_serialization.py`

For any typed decision that is serialized into bytecode.

## Recommended reading order in the source

A productive first pass is:

1. `vtypes/nodes.py` — learn the vocabulary.
2. `vtypes/builders.py` — learn canonical construction and display.
3. `vtypes/context.py` — learn relationship facts.
4. `vtypes/relations.py`: `subtype`, `assignable`, `_solve`, `_combine`,
   `compatible`, `_match_specificity`, `try_apply_overload`.
5. `analysis/analyser.py`: `AnalysisBranch`, `BranchSet`, `analyse_block`,
   `source_arguments`, and the orchestration methods.
6. `analysis/_analyser_handlers.py` and `analysis/calls/` — follow one node from dispatch through candidate
   selection and overload application; consult `control_flow/patterns.py` or
   `_analyser_utils.py` for the corresponding helper family.
7. `vtypes/environment.py` — connect names/declarations to the relation context.
8. Focused tests in `tests/test_types.py` and `tests/test_analyser.py`.

Read one end-to-end example with a debugger or temporary assertions. Do not try
to memorize all of `relations.py` at once.

## A final mental checklist

When changing type analysis, ask:

1. What exact question am I answering: sameness, subtyping, assignment, call
   compatibility, generic coherence, overload choice, or branch joining?
2. Is the information global (`Environment`/`Context`) or branch-local?
3. Is this a type-tree transformation, a relation, or an analyser state change?
4. Am I preserving direction: actual/source first, expected/target second?
5. Do repeated generics need one coherent solution rather than a union?
6. Can the rule nest inside collections, rows, functions, tags, or unions?
7. Does the selected static plan survive into typed AST and bytecode?
8. Is the negative case tested at the same layer as the positive case?

The system is large because the language is expressive, not because each
operation is mathematically mysterious. Follow the data records, identify the
question being asked, and keep each rule in its narrowest correct layer.
