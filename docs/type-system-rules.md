# Type System Rules

This document describes the core type relations needed for assignment,
parameter passing, overload resolution, generic solving, and generic
constraint combining.

The design intentionally keeps the relations separate:

- `same(A, B)` checks canonical type equality.
- `assignable(source, target)` checks whether a value can be stored in a
  variable, field, or return slot.
- `compatible(argument, parameter)` checks whether an argument can satisfy a
  call parameter.
- `matchSpecificity(argument, parameter)` classifies how specifically an
  argument matches a parameter for overload ranking.
- `solve(pattern, actual)` extracts generic constraints from a parameter type
  and an argument type.
- `combine(A, B)` merges multiple solutions for the same generic.
- `castable(source, target)` checks whether an explicit `as` or `as!` cast is
  allowed.

`assignable` should be strict. `compatible` should be call-site friendly.
`compatible` should return only a boolean. `solve` should be local and dumb.
Overload resolution should coordinate the whole process.

## 1. Canonical Types

All rules assume types have been normalized before comparison.

Recommended internal type forms:

```text
Never
None
Nominal(name, args)
Tuple(items)
TuplePattern(prefix, repeated, suffix)
Dict(fields)
DynDict(key, value)
Function(params, returns, elementTags)
CallSiteCheckedFunction(declaredParams, declaredReturns, body, capturedEnv)
Union(items)
Intersection(items)
Collection(kind, base, rank)
Tagged(tagSpec, inner)
TypeVar(name)
RankVar(name)
RowConstraint(base, fields)
Exact(type)
Atomic(typeVar)
```

Collection kinds:

```text
ListExact      T+n
ListMinimum    T*n
ListRugged     T~n
ArrayExact     T^n
ArrayMinimum   T>n
```

`n` is a positive integer in source syntax. Internally, rank `0` may be used
when solving collection bases:

```text
T+0 == T atomic
T^0 == T atomic
T*0 == T atomic | T*
T>0 == T atomic | T>
T~0 == T atomic | T~
```

## 2. Normalization Rules

Normalize recursively before applying relations.

### 2.1 Union Normalization

```text
T | T                 => T
T | Never             => T
Never | T             => T
flatten nested unions
sort union members canonically
```

For optionals:

```text
T?                    => Some[T] | None
T | None              => T?
None | T              => T?
None | None           => None
T | None | None       => T?
```

Non-`None` values are implicitly wrapped when forming optional types:

```text
T | None              => Some[T] | None
T | Some[U]           => Some[T | U]
```

The `T | Some[U]` rule only applies when `T` cannot be `None` and is not
already optional.

Nested optionals are preserved only when `Some` is explicit:

```text
T??                   => Some[T?] | None
T | Some[None] | None => T??
```

Canonical union ordering:

```text
1. ordinary non-None, non-Err values
2. None
3. Err values
```

### 2.2 Result Normalization

If `E` implements `Err`:

```text
T | E                 => Result[T, E]
OK[T] | E             => Result[T, E]
OK[T] | OK[U]         => OK[T | U]
T | E | V             => Result[T, E | V] if E and V implement Err
```

Result simplification only occurs when the union contains at least one
non-`Err` type and at least one `Err` type.

```text
E | V                 => E | V
```

Where both `E` and `V` implement `Err`.

### 2.3 Intersection Normalization

```text
T & T                 => T
flatten nested intersections
sort intersection members canonically
```

Intersections are primarily for trait requirements.

### 2.4 Tag Normalization

Tags should be stored separately from the inner type:

```text
#sorted Number+       => Tagged({#sorted}, Number+)
#!infinite Number+    => Tagged({#!infinite}, Number+)
```

Apply tag disjoint rules when a tag is added:

```text
if #A disjoint #B, then adding #A removes #B
```

Variant data tags imply their parent computed tag:

```text
#ascending T          => #ascending #sorted T
```

## 3. `same(A, B)`

`same` is canonical structural equality.

```text
same(A, B) = normalize(A) structurally equals normalize(B)
```

Use `same` for:

- exact overload matches
- detecting repeated union/intersection members
- checking whether substitutions are already identical
- avoiding unnecessary casts

`same` does not perform subtyping, optional wrapping, trait upcasting,
vectorisation, or generic solving.

## 4. Subsumption / Subtyping

Subsumption is used by `assignable` and `compatible`.

Notation:

```text
A <= B means A can be treated as B without changing the value.
```

### 4.1 Basic Rules

```text
Never <= T
T <= T
```

If numeric refinements are represented as tags or refined nominal types:

```text
Integer <= Number
Real <= Number
#integer Number <= Number
#real Number <= Number
#boolean Number <= Number
```

If `Integer` is meant to be a subset of `Real`, also allow:

```text
Integer <= Real
```

Otherwise keep `Integer` and `Real` as separate refinements of `Number`.

### 4.2 Nominal Types and Generics

Generic nominal types are invariant.

```text
Box[T] <= Box[U] only if same(T, U)
```

For now, do not add covariance or contravariance.

### 4.3 Objects, Traits, Variants, and Enums

```text
Object <= Trait              if Object implements Trait
TraitA <= TraitB             if TraitA implements TraitB
VariantMember <= Variant     if member belongs to Variant
EnumMember <= Enum           if member belongs to Enum
```

Trait implementation is transitive.

```text
Object <= TraitB if Object <= TraitA and TraitA <= TraitB
```

### 4.4 Union Types

```text
A | B <= T       if A <= T and B <= T
T <= A | B       if T <= A or T <= B
```

### 4.5 Intersection Types

```text
T <= A & B       if T <= A and T <= B
A & B <= A
A & B <= B
```

### 4.6 Tuple Types

Fixed tuples are invariant and positional:

```text
{A1, A2, ... An} <= {B1, B2, ... Bn}
  if each Ai <= Bi
```

Tuple lengths must match.

Fixed tuples can satisfy tuple patterns:

```text
{A1, ... An} <= {prefix..., repeated..., suffix...}
  if the fixed tuple can be partitioned into the pattern
  and each item is assignable to the corresponding pattern item
```

Tuple patterns should only appear in parameter positions unless the language
later explicitly allows them elsewhere.

### 4.7 Dictionaries

Static dictionaries are structural:

```text
dict[a: A, b: B] <= dict[a: X]
  if A <= X
```

That is, a dictionary with extra fields can be passed where fewer fields are
expected.

For assignment to an explicitly exact dictionary type, choose one policy:

```text
simple policy: allow width subtyping
strict policy: require exact fields
```

Recommended initial policy: allow width subtyping for parameters, require exact
fields for assignment unless the target is a row-constrained type.

Dynamic dictionaries are invariant in key type and covariant in value type only
if writes through the target cannot violate the original dictionary.

Recommended simple policy:

```text
DynDict[K1, V1] <= DynDict[K2, V2]
  if same(K1, K2) and same(V1, V2)
```

### 4.8 Collections

Arrays can be used where lists are expected if base and rank match:

```text
T^n <= T+n
T>n <= T*n
```

Exact ranks can be used where minimum ranks are expected:

```text
T+n <= T*m    if n >= m
T^n <= T>m    if n >= m
```

Minimum ranks can be used where rugged ranks are expected:

```text
T*n <= T~m    if n >= m
T+n <= T~m    if n >= m
```

Exact ranks can be used where rugged ranks are expected:

```text
T+n <= T~m    if n >= m
```

Arrays can be used where rugged list ranks are expected through list
subsumption:

```text
T^n <= T+n <= T~m if n >= m
T>n <= T*n <= T~m if n >= m
```

Lists are not automatically assignable to arrays:

```text
T+n <= T^n    false by default
T*n <= T>n    false by default
T~n <= T^n    false
T~n <= T>n    false
```

List-to-array conversion is an explicit cast with a possible runtime
rectangularity check.

Recommended simple policy for collection bases:

```text
Collection[K, A, n] <= Collection[K, B, n] only if same(A, B)
```

Allow numeric/tag refinement erasure in bases only if it is already represented
as normal subsumption:

```text
#integer Number+ <= Number+
```

Avoid general covariance for now.

### 4.9 Tags

Constructed and computed tags are ordinary metadata unless expected.

```text
#tag T <= T
```

Unit tags are stricter:

```text
#unit T <= T          false unless parameter explicitly accepts unit erasure
#unit T <= #unit T    true
```

Expected positive tag:

```text
Tagged(actualTags, A) <= Tagged(required #x, B)
  if #x is in actualTags and A <= B
```

Expected absent tag:

```text
Tagged(actualTags, A) <= Tagged(required #!x, B)
  if #x is not in actualTags and A <= B
```

Exact tag set:

```text
Tagged(actualTags, A) <= Tagged(exact allowedTags, B)
  if actualTags contains no tags outside allowedTags and A <= B
```

## 5. `assignable(source, target)`

`assignable` checks whether a value can be stored in a location of `target`
type.

Use it for:

- variable initialization with explicit type
- reassignment
- field assignment
- return type checking
- branch state consistency where exact stack state is required

Assignment does not vectorise and does not perform custom casts.

### 5.1 Assignment Rules

```text
assignable(S, T) if same(S, T)
assignable(S, T) if S <= T
```

Unions:

```text
assignable(S1 | S2, T)
  if assignable(S1, T) and assignable(S2, T)

assignable(S, T1 | T2)
  if assignable(S, T1) or assignable(S, T2)
```

Optionals:

```text
assignable(None, T?)          true
assignable(T, T?)             true through implicit Some wrapping
assignable(T?, T)             false unless T itself accepts None
```

Objects and traits:

```text
assignable(Object, Trait)     if Object implements Trait
assignable(TraitA, TraitB)    if TraitA implements TraitB
```

Variants:

```text
assignable(Member, Variant)   if Member belongs to Variant
```

Collections:

```text
assignable(T^n, T+n)          true
assignable(T>n, T*n)          true
assignable(T+n, T*m)          if n >= m
assignable(T^n, T>m)          if n >= m
assignable(T+n, T^n)          false by default
assignable(T*n, T>n)          false by default
assignable(T~n, array type)   false
```

Tags:

```text
assignable(#tag T, T)         true for non-unit tags
assignable(#unit T, T)        false unless explicitly allowed
assignable(T, #tag T)         false unless tag is guaranteed by construction
```

### 5.2 Assignment Examples

```text
Number       -> Number        ok
Integer      -> Number        ok
Number       -> Number?       ok
None         -> Number?       ok
Number?      -> Number        error
Number^      -> Number+       ok
Number+      -> Number^       error, use cast
Number++     -> Number*       ok
Number*      -> Number++      error
#sorted T+   -> T+            ok
T+           -> #sorted T+    error
```

## 6. `compatible(argument, parameter)`

`compatible` checks whether a call argument can satisfy a parameter.

Use it for:

- element calls
- function calls
- overload candidate filtering
- matching call-site function parameters

Compatibility may include:

- assignability
- generic solving
- optional wrapping
- vectorisation
- tag requirements
- trait/intersection satisfaction
- `where` clause acceptance

Compatibility returns only:

```text
Bool
```

It answers whether the call is allowed at all. It does not decide which
overload wins. Overload ranking is handled by `matchSpecificity`.

### 6.1 Compatibility Rules

```text
compatible(A, P) if assignable(A, P)
```

Generic parameters:

```text
compatible(A, P-with-generics)
  if solve(P, A) succeeds,
  substitutions combine,
  and assignable/compatible(A, substitute(P)) succeeds
```

Optionals:

```text
compatible(T, T?)             true
compatible(None, T?)          true
compatible(T?, T)             false unless parameter accepts None
```

Traits:

```text
compatible(Object, Trait)     if Object implements Trait
compatible(T, A & B)          if compatible(T, A) and compatible(T, B)
```

Unions:

```text
compatible(A, P1 | P2)
  if compatible(A, P1) or compatible(A, P2)

compatible(A1 | A2, P)
  if compatible(A1, P) and compatible(A2, P)
```

Tags:

```text
compatible(#x A, #x P)        if compatible(A, P)
compatible(A, #x P)           false unless A has #x
compatible(#x A, #!x P)       false
compatible(A, #!x P)          if A lacks #x and compatible(A, P)
```

Unit tags must be expected:

```text
compatible(#km Number, Number)       false
compatible(#km Number, #km Number)   true
```

### 6.2 Function Compatibility

Function compatibility is based on callability, not literal function type
equality.

```text
compatible(callable, Function[P1, ... Pn -> R1, ... Rm])
  if callable can be called with P1, ... Pn
  and its result stack is assignable/compatible with R1, ... Rm
```

This check may use normal overload resolution and vectorisation inside the
callable.

For a concrete function value:

```text
compatible(Function[Ain -> Aout], Function[Pin -> Pout])
  if calling the actual function with Pin is valid
  and Aout is compatible with Pout after that call
```

For an element or overload set:

```text
compatible(OverloadSet[...], Function[Pin -> Pout])
  if at least one overload can be called with Pin
  and its output is compatible with Pout
```

If multiple overloads of the callable are equally specific for the expected
function input types, function compatibility is ambiguous and fails. The user
must disambiguate the callable.

For a call-site-checked function:

```text
compatible(CallSiteCheckedFunction, Function[Pin -> Pout])
  if checking the function body with Pin succeeds
  and the inferred output stack is compatible with Pout
```

This compatibility check may depend on the current call site. A
call-site-checked function is therefore not a single fully inferred function
type; it is a body template that can produce a concrete stack effect for each
valid call.

This rule is what allows:

```text
reduce[T](T+, Function[T, T -> T])
[[1, 2, 3], [4, 5, 6]] reduce: +
```

The first argument solves:

```text
solve(T+, Number++) => T := Number+
```

So the second parameter becomes:

```text
Function[Number+, Number+ -> Number+]
```

The `+` element is compatible with that function type because its
`Number, Number -> Number` overload can vectorise over `Number+, Number+` and
produce `Number+`.

### 6.3 Vectorisation Compatibility

If an argument has higher rank than the parameter expects, and the parameter is
not marked `exact`, the call may vectorise.

```text
compatible(T+n, T)            true by vectorisation
compatible(T^n, T)            true by vectorisation
compatible(T~n, T)            true by vectorisation only where atomic T is expected
```

For ranked parameters:

```text
compatible(T+n, T+m)          direct if n == m
compatible(T+n, T+m)          vectorise if n > m
compatible(T~n, T+m)          vectorise if n > m
```

If a parameter is marked exact:

```text
compatible(T+n, exact T)      false
compatible(T+n, exact T+n)    true
```

Multiple vectorised arguments are zipped. Runtime length checks may be required:

```text
compatible(A+, B+, params A, B)
  => vectorised call with length check
```

Vectorisation result shape:

```text
all vectorised args are lists  => list result
all vectorised args are arrays and result preserves arrayness => array result
mixed list/array args          => list result
```

## 7. `matchSpecificity(argument, parameter)`

`matchSpecificity` classifies how an argument matches a parameter after generic
substitution. It is used only for overload ranking.

It should return either:

```text
Specificity
NoMatch
```

Specificity is an ordered enum. Lower is more specific:

```text
Exact             = 0
ExactGeneric      = 1
Tagged            = 2
Optional          = 3
Intersection      = 4
Trait             = 5
Rank              = 6
Union             = 7
Vectorised        = 8
CallSiteChecked   = 9
NoMatch           = infinity
```

If several categories apply, use the most specific one.

### 7.1 Specificity Categories

```text
Exact
  same(argument, parameter)

ExactGeneric
  parameter came from a generic and solved exactly to the argument type

Tagged
  parameter requires a tag that the argument has, with the same inner type

Optional
  argument T used where T? is expected
  None used where T? is expected

Intersection
  argument satisfies an intersection requirement A & B

Trait
  argument satisfies parameter through object/trait implementation

Rank
  argument satisfies parameter through rank subsumption
  examples: T+n to T*m, T^n to T+n, T+n to T~m

Union
  argument matches one branch of a parameter union
  or every branch of an argument union matches the parameter

Vectorised
  argument only matches by vectorising over collection rank

CallSiteChecked
  argument/candidate only matches after checking a call-site-checked function
  body with the concrete call-site stack and argument types
```

### 7.2 Overload Dominance

For an overload with `n` parameters, compute one specificity value per
parameter:

```text
score(overload) = [
  matchSpecificity(arg1, param1),
  matchSpecificity(arg2, param2),
  ...
  matchSpecificity(argn, paramn)
]
```

An overload `A` dominates overload `B` when:

```text
for every parameter i:
  A.score[i] <= B.score[i]

and for at least one parameter j:
  A.score[j] < B.score[j]
```

Do not sum specificity values. Summing allows unrelated tradeoffs between
parameters and can choose surprising overloads.

Example:

```text
F(Number, Number | String)
F(Number | String, Number)

args: Number, Number
```

Scores:

```text
[Exact, Union]
[Union, Exact]
```

Neither overload dominates the other, so the call is ambiguous and requires
disambiguation.

## 8. `solve(pattern, actual)`

`solve` extracts generic constraints from a parameter pattern and actual
argument type.

It does not prove the whole call is valid. After solving, the instantiated
parameter must still be checked with `compatible`.

Solver output:

```text
SolveResult {
  typeConstraints: Map<TypeVar, List<Type>>
  rankConstraints: Map<RankVar, List<Int>>
  deferredChecks: [Check]
}
```

### 8.1 General Solver Rules

```text
solve(T, A) where T is a type variable
  => constraint T := A
```

Same concrete type:

```text
solve(A, A)
  => no constraints
```

Nominal generic types:

```text
solve(N[P1, ... Pn], N[A1, ... An])
  => solve(P1, A1), ... solve(Pn, An)
```

Different nominal constructors do not solve:

```text
solve(N[P], M[A])
  => fail if N != M
```

Tuples:

```text
solve({P1, ... Pn}, {A1, ... An})
  => solve(P1, A1), ... solve(Pn, An)
```

Tuple lengths must match unless the pattern is variadic.

Functions:

```text
solve(Function[Pin -> Pout], Function[Ain -> Aout])
  => solve corresponding parameter and return types
```

Recommended simple policy: solve function input and output positions
invariantly for now. Do not add function variance yet.

This rule only applies when the actual argument already has a concrete function
type. If the actual argument is an element name or overload set, do not try to
solve through all possible overloads:

```text
solve(Function[Pin -> Pout], OverloadSet[...])
  => no constraints, defer to compatibility after substitution
```

This keeps generic solving from guessing based on an overloaded callable before
other parameters have fixed the expected function type.

Optionals:

```text
solve(T?, A?)                  => solve(T, inner(A?))
solve(T?, None)                => no constraint for T
solve(T?, A)                   => solve(T, A) if A is not None
```

### 8.2 Collection Solver Rules

Where `m >= n`:

```text
solve(T+n, U+m)                => T := U+(m-n)
solve(T*n, U*m)                => T := U*(m-n)
solve(T~n, U~m)                => T := U~(m-n)
```

Exact actuals can solve minimum/rugged patterns:

```text
solve(T*n, U+m)                => T := U+(m-n)
solve(T~n, U+m)                => T := U+(m-n)
solve(T~n, U*m)                => T := U~(m-n)
```

Array patterns:

```text
solve(T^n, U^m)                => T := U^(m-n)
solve(T>n, U^m)                => T := U^(m-n)
solve(T>n, U>m)                => T := U>(m-n)
```

Array actuals against list patterns:

```text
solve(T+n, U^m)                => T := U+(m-n)
solve(T*n, U>m)                => T := U*(m-n)
solve(T*n, U^m)                => T := U+(m-n)
solve(T~n, U^m)                => T := U+(m-n)
solve(T~n, U>m)                => T := U~(m-n)
```

List actuals against array patterns do not solve directly:

```text
solve(T^n, U+m)                => fail
solve(T>n, U*m)                => fail
```

Use explicit casts for list-to-array conversion.

### 8.3 Rank Variables

When a pattern contains a rank variable:

```text
solve(T+$n, U+m)               => T := U, $n := m
solve(T*$n, U*m)               => T := U, $n := m
solve(T~$n, U~m)               => T := U, $n := m
solve(T^$n, U^m)               => T := U, $n := m
solve(T>$n, U>m)               => T := U, $n := m
```

If both base and rank need solving:

```text
solve(T+$n, U+m)               => T := U, $n := m
solve(T+$n, U^m)               => T := U, $n := m
```

For minimum/rugged matches with exact actuals:

```text
solve(T*$n, U+m)               => T := U, $n <= m
solve(T~$n, U+m)               => T := U, $n <= m
```

Recommended simple implementation: only bind rank variables exactly from exact
rank patterns at first. Use `where` clauses for more complex rank arithmetic.

### 8.4 Atomic

`T atomic` should not bind `T`.

```text
solve(T atomic, A)
  => deferred check only
```

After `T` is solved elsewhere:

```text
verify A compatible with atomic(T)
```

Example:

```text
solve(T+, Number++)            => T := Number+
verify needle Number against T atomic
atomic(Number+)                => Number
```

### 8.5 Row Constraints

For row-constrained anonymous generics:

```text
solve(T(.field: U), A)
  => T := A
  => require A has .field
  => solve(U, typeOf(A.field))
```

If `A` is itself unknown, keep a deferred row requirement:

```text
T must have field .field: U
```

### 8.6 Tags During Solving

Positive required tag:

```text
solve(#x P, #x A)              => solve(P, A)
solve(#x P, A)                 => fail unless A is known to have #x
```

Absent tag:

```text
solve(#!x P, A)                => solve(P, A) if A lacks #x
solve(#!x P, #x A)             => fail
```

Tags generally should not bind generic type variables unless the generic itself
appears inside the tag:

```text
solve(#x T, #x Number)         => T := Number
```

### 8.7 Unions and Intersections During Solving

Do not solve across unordered unions or intersections:

```text
solve(T | U, A | B)            => no direct solve
solve(T & U, A & B)            => no direct solve
```

Special optional syntax is allowed because it has canonical structure:

```text
solve(T?, A?)                  => solve(T, inner(A?))
solve(T?, None)                => no constraint for T
solve(T?, A)                   => solve(T, A) if A is not None
```

For ordinary union parameters, overload compatibility may try branches, but
branch selection must be unambiguous:

```text
solve(T | String, Number)      => may solve T := Number
solve(T | U, Number)           => ambiguous, reject or require explicit args
```

Recommended initial policy: do not solve through ordinary unions except
optional/result sugar.

### 8.8 Solver Failure

`solve` fails when:

- constructors differ and no special rule exists
- tuple lengths do not match
- required rank relation does not hold
- required tag is missing
- forbidden tag is present
- list actual is matched against an array pattern
- an ordinary union/intersection would require ambiguous matching

## 9. `combine(A, B)`

`combine` merges two candidate solutions for the same type variable.

It should be conservative, but it is not strict equality. Treat it as a
restricted least-common-supertype operation: find the narrowest type that both
candidate solutions can be used as. If there is no simple, predictable shared
type, fail instead of inventing a broad union.

This means `solve` should preserve precise information from each individual
argument, and `combine` should only widen when multiple constraints actually
need to agree.

### 9.1 Basic Combine Rules

```text
combine(A, A)                  => A
```

If one side is a refinement of the other, choose the wider type only when the
generic position is allowed to widen:

```text
combine(Integer, Number)       => Number if widening allowed, else fail
combine(#tag T, T)             => T if tag erasure allowed, else fail
```

Recommended initial policy for function generic solving:

```text
combine(Integer, Number)       => fail unless same after normalization
combine(Number, String)        => fail
```

This keeps calls like `same(1, "x")` from silently becoming
`T = Number | String`.

### 9.2 Collection Combine Rules

Same exact ranks:

```text
combine(T+n, T+n)              => T+n
combine(T^n, T^n)              => T^n
```

Exact list with exact list:

```text
combine(T+n, T+m)              => fail if n != m
```

This is a conservative initial restriction. A more permissive system could
combine different exact ranks into a minimum-rank type:

```text
combine(T+n, T+m)              => T*min(n, m)
```

But that can make same-generic calls accept values with different exact ranks,
so the simpler first version should reject it.

Minimum ranks:

```text
combine(T*n, T*m)              => T*min(n, m)
combine(T>n, T>m)              => T>min(n, m)
```

Exact plus minimum:

```text
combine(T+n, T*m)              => T*min(n, m)
combine(T^n, T>m)              => T>min(n, m)
```

Rugged:

```text
combine(T~n, T~m)              => T~min(n, m)
combine(T+n, T~m)              => T~min(n, m)
combine(T*n, T~m)              => T~min(n, m)
```

List and array:

```text
combine(T+n, T^n)              => T+n
combine(T*n, T>n)              => T*n
combine(T~n, T^n)              => T~n
```

The list form wins because arrays are usable as lists, but lists are not
automatically usable as arrays.

Different bases:

```text
combine(A+n, B+n)              => fail unless same(A, B)
```

Recommended simple policy: do not combine different bases into unions during
generic function solving.

### 9.3 Optional Combine Rules

```text
combine(T?, T?)                => combine(T, T)?
combine(T?, T)                 => T? if same inner T, else fail
combine(None, T?)              => T?
```

Avoid using optional combine to infer a generic from `None` alone:

```text
combine(None)                  => insufficient type information
```

Example:

```text
define[T] idOpt(:T?) -> T?
idOpt(None)
```

Should require explicit type arguments or contextual return type.

### 9.4 Rank Constraint Combine

Exact rank bindings:

```text
combineRank(n, n)              => n
combineRank(n, m)              => fail if n != m
```

Inequality rank constraints:

```text
$r <= n
$r <= m
=> $r <= min(n, m)
```

Recommended initial policy: require rank variables to resolve to exact integer
values before substituting return types.

### 9.5 Combining All Constraints

```text
combineAll([])                 => unsolved
combineAll([A])                => A
combineAll([A, B, C])          => combine(combine(A, B), C)
```

If a generic remains unsolved:

- use explicit type arguments if provided
- use contextual expected return type if available
- otherwise reject the call as underconstrained

## 10. `castable(source, target)`

`castable` checks explicit casts.

Use it for:

```text
value as Type
value as! Type
inline parameter casts
inline return casts
custom cast definitions
```

### 10.1 Safe Casts

Safe `as` is allowed when:

```text
assignable(source, target)
```

Or when a runtime check can validate the conversion:

```text
T+n as T^n                     runtime rectangularity check
T*n as T>n                     runtime rectangularity check
supertype as subtype           runtime type/tag check
trait as concrete object       runtime type check
collection rerank              runtime shape check
```

Or when a custom cast rule exists:

```text
cast A -> B
```

Custom cast rules should only apply to atomic non-union source and target
types unless the language later deliberately extends them.

### 10.2 Unsafe Casts

Unsafe `as!` is allowed only when:

- a safe cast would be allowed but requires a runtime check, or
- the target is in an explicitly unsafe domain such as FFI.

Unsafe casts should be rejected when the safe cast is already zero-cost:

```text
Number as! Number              error
Integer as! Number             error if Integer <= Number is zero-cost
```

Unsafe casts should also be rejected when there is no meaningful relationship:

```text
String as! Number              error unless an unsafe/custom rule exists
```

## 11. Call-Site Type Checking

Call-site type checking is used for functions whose full stack effect cannot be
validated at definition site.

Normal functions are checked once:

```text
definition site:
  infer/check body
  produce Function[inputs -> outputs]
```

Call-site-checked functions are checked per call:

```text
definition site:
  validate syntax, names, captures, and declared constraints
  store body and captured environment

call site:
  bind concrete argument types
  check body with the actual call-site stack
  produce a concrete stack effect for this call only
```

### 11.1 When a Function Is Call-Site Checked

Mark a function as call-site checked if any part of its stack effect is
intentionally under-specified at definition site.

Examples:

```text
Function                         bare function parameter
Function[...] with unknown arity  under-specified function shape
{T...}                            variadic tuple parameter
anonymous stack-polymorphic use   body consumes stack based on an argument
```

The common case is a bare `Function` parameter:

```text
fn (f: Function) => ...
```

The checker cannot know how many values `f` consumes or produces until a
concrete callable is supplied.

### 11.2 Internal Representation

Use a distinct function kind:

```text
CallSiteCheckedFunction {
  declaredParams
  declaredReturns
  body
  capturedEnvTypes
}
```

Do not pretend the unknown return is an ordinary type:

```text
Function[Function -> ?]
```

is useful surface documentation, but internally the function should remain a
call-site-checked template.

### 11.3 Definition-Site Checks

At definition site, check only what is independent of the eventual call site:

```text
parameter syntax is valid
declared type and trait constraints are valid
captured variables exist and have stable types
referenced names can be resolved where possible
the body is syntactically valid
statically-known body fragments are valid
```

Do not require one final input/output stack effect.

### 11.4 Direct Call Algorithm

When directly calling a call-site-checked function:

```text
checkCSTCCall(function, explicitArgs, outerStack):
  bind declared parameters from explicit arguments
  replace under-specified parameters with concrete argument types
  build local type environment from parameters and capturedEnvTypes
  type-check the body using normal stack simulation
  allow the body to consume needed values from outerStack
  infer the concrete input stack consumed by this call
  infer the concrete output stack produced by this call
  cache the instantiation if desired
  return the concrete stack effect
```

This inferred stack effect belongs only to this call.

### 11.5 Compatibility With Expected Function Types

When a call-site-checked function is passed where a function type is expected:

```text
compatible(CSTC, Function[Pin -> Pout])
  if checkCSTCCall(CSTC, parameter types Pin, synthetic stack Pin) succeeds
  and inferred outputs are compatible with Pout
```

Use a synthetic stack containing the expected input types. This answers:

```text
"Can this function behave as Function[Pin -> Pout]?"
```

If yes, the match specificity is:

```text
CallSiteChecked
```

This should be less specific than ordinary concrete, generic, trait, union, or
vectorised matches, so concrete overloads win over stack-polymorphic catch-all
functions.

### 11.6 Overload Interaction

For overload resolution, a call-site-checked candidate is accepted only if its
body checks successfully for the current call.

```text
if candidate is normal:
  use normal solve/compatible/specificity rules

if candidate is call-site checked:
  instantiate and check its body at this call site
  if body check succeeds, accept with inferred stack effect
  otherwise reject candidate
```

If a normal overload and a call-site-checked overload both work, the normal
overload usually wins because `CallSiteChecked` is less specific.

### 11.7 Example: `dip`

```text
dip = fn (function: Function) =>
  temp = top
  function()
  temp
end
```

At definition site, `function` has no known arity or multiplicity, so `dip` is
stored as a call-site-checked function.

At a call site:

```text
1 2 3 dip(fn => +)
```

The supplied function has type:

```text
Function[Number, Number -> Number]
```

The body is checked with that concrete function type and the visible outer
stack. This call can therefore infer its own stack effect, separate from any
future call to `dip` with a different function.

### 11.8 Caching

Caching is optional for correctness but useful for performance.

Simple cache key:

```text
function id
explicit argument types
visible stack types
captured environment type version
```

If the same call-site shape appears again, reuse the previously inferred stack
effect.

### 11.9 Error Reporting

Errors should mention both the call site and the deferred body location:

```text
call site: dip(fn => +)
while checking body of dip:
  function()
available stack: ...
required stack: ...
```

This keeps call-site checking understandable instead of making the definition
look invalid in isolation.

## 12. Generic Call and Overload Resolution Algorithm

For each overload candidate:

```text
tryCandidate(overload, argumentTypes):
  constraints = empty

  for each parameter, argument:
    result = solve(parameter.type, argument.type)
    if result failed:
      reject candidate
    constraints += result.constraints

  substitution = combineAll(constraints)
  if substitution failed:
    reject candidate

  fill unresolved generics from explicit type arguments or context
  if any required generic remains unresolved:
    reject candidate

  instantiatedParams = substitute(overload.params, substitution)

  run where clause if present
  if where rejects:
    reject candidate
  add any type/rank bindings produced by where to substitution

  instantiatedParams = substitute(overload.params, substitution)
  instantiatedReturns = substitute(overload.returns, substitution)

  if overload is call-site checked:
    cstcEffect = checkCSTCCall(overload, argumentTypes, currentStack)
    if cstcEffect failed:
      reject candidate
    accept candidate with cstcEffect and CallSiteChecked specificity
    continue to next candidate

  for each instantiated parameter, argument:
    if !compatible(argument.type, parameter.type):
      reject candidate

  check generic trait constraints
  check row constraints

  scores = []
  for each instantiated parameter, argument:
    specificity = matchSpecificity(argument.type, parameter.type)
    if specificity is NoMatch:
      reject candidate
    scores.push(specificity)

  accept candidate with scores
```

If a parameter is a function type and the argument is an overloaded element,
`solve` should usually produce no constraints rather than failing. The later
`compatible(argument.type, instantiatedParameter.type)` step checks whether the
overloaded element can actually act as the expected function type.

Then overload resolution:

```text
candidates = all accepted candidates
if none: compile error
if one: choose it

winners = candidates that are not dominated by another candidate
if winners has one item: choose it
otherwise: ambiguous overload compile error
```

Where:

```text
dominates(A, B):
  for every parameter i:
    A.score[i] <= B.score[i]

  and for at least one parameter j:
    A.score[j] < B.score[j]
```

## 13. Worked Examples

### 13.1 Identity

```text
define[T] id(x: T) -> T
id(1)
```

Solving:

```text
solve(T, Number) => T := Number
```

Return:

```text
Number
```

### 13.2 Same-Type Pair

```text
define[T] pair(x: T, y: T) -> {T, T}
pair(1, 2)
```

Solving:

```text
T := Number
T := Number
combine => Number
```

Return:

```text
{Number, Number}
```

Call:

```text
pair(1, "x")
```

Solving:

```text
T := Number
T := String
combine => fail
```

### 13.3 List Head

```text
define[T] head(xs: T+) -> T
head([1, 2, 3])
```

Solving:

```text
solve(T+, Number+) => T := Number
```

Return:

```text
Number
```

Nested list:

```text
head([[1], [2]])
```

Solving:

```text
solve(T+, Number++) => T := Number+
```

Return:

```text
Number+
```

### 13.4 Array as List

```text
define[T] head(xs: T+) -> T
head(arr{1, 2, 3})
```

Solving:

```text
solve(T+, Number^) => T := Number
```

Return:

```text
Number
```

### 13.5 Map

```text
define[T, U] map(xs: T+, f: Function[T -> U]) -> U+
map([1, 2, 3], fn(:Number) -> String => ...)
```

Solving:

```text
solve(T+, Number+) => T := Number
solve(Function[T -> U], Function[Number -> String])
  => T := Number
  => U := String
```

Combined:

```text
T = Number
U = String
```

Return:

```text
String+
```

### 13.6 Reduce Over Nested Lists

```text
define[T] reduce(xs: T+, f: Function[T, T -> T]) -> T
[[1, 2, 3], [4, 5, 6]] reduce: +
```

The list argument has type:

```text
Number++
```

Solving the first parameter:

```text
solve(T+, Number++) => T := Number+
```

The second parameter becomes:

```text
Function[Number+, Number+ -> Number+]
```

The `+` element does not need to literally have this overload:

```text
+(Number+, Number+) -> Number+
```

It is enough that `+` is callable as that function type. Its ordinary numeric
overload:

```text
+(Number, Number) -> Number
```

can vectorise over:

```text
Number+, Number+
```

and therefore produces:

```text
Number+
```

So the whole reduce call is valid and has return type:

```text
Number+
```

Operationally, this sums the rows pairwise:

```text
[1, 2, 3] + [4, 5, 6] => [5, 7, 9]
```

### 13.7 Optional

```text
define[T] fallback(x: T?, default: T) -> T
fallback(None, 10)
```

Solving:

```text
solve(T?, None)     => no useful T
solve(T, Number)    => T := Number
```

Combined:

```text
T = Number
```

Return:

```text
Number
```

Call:

```text
fallback(None, None)
```

Should fail unless contextual type or explicit type arguments provide `T`.

### 13.8 Atomic

```text
define[T] find(haystack: T+, needle: T atomic) -> Number?
find([[1], [2]], 1)
```

Solving:

```text
solve(T+, Number++) => T := Number+
solve(T atomic, Number) => deferred check
```

Verification:

```text
atomic(Number+) => Number
compatible(Number, Number) => ok
```

### 13.9 Trait Constraint

```text
define[T: Comparable] sort(xs: T+) -> T+
sort([1, 2, 3])
```

Solving:

```text
T := Number
```

Constraint check:

```text
Number implements Comparable
```

If true:

```text
return Number+
```

### 13.10 Assignment vs Compatibility

Assignment:

```text
$x: Number = [1, 2, 3]
```

Fails:

```text
assignable(Number+, Number) => false
```

Call:

```text
double([1, 2, 3])
```

May succeed:

```text
compatible(Number+, Number) => true by vectorisation
```

### 13.11 List to Array Cast

Assignment:

```text
$xs: Number^ = [1, 2, 3]
```

Fails:

```text
assignable(Number+, Number^) => false
```

Cast:

```text
[1, 2, 3] as Number^
```

Allowed with runtime rectangularity check.

## 14. Recommended Initial Restrictions

To keep the first implementation simple:

- Keep generics invariant.
- Do not solve through ordinary unions or intersections.
- Do not automatically combine different generic solutions into unions.
- Do not add function parameter contravariance or return covariance.
- Do not make lists covariant.
- Do not allow implicit list-to-array conversion.
- Require unresolved generics to be supplied explicitly or from context.
- Treat `atomic` as a verification-only constraint.
- Put vectorisation in compatibility, not assignability.
- Put casts in `castable`, not assignment.
- Treat call-site-checked functions as less specific than normal checked
  functions.

These restrictions can be relaxed later without changing the overall
architecture.
