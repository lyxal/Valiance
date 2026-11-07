# Generics hardening test inventory

The [generics compiler and runtime test plan](../Valiance_Generics_Comptime_Runtime_Test_Plan.md)
contains **351 specified cases across 20 suites** (P0: 221, P1: 103,
P2: 27). Its scenarios are **not automatically executable tests**.

`tests/test_generics_plan.py` contains numbered source-level cases, including
positive/negative compiler oracles, static result types where relevant, runtime
stacks, faults and metamorphic comparisons. It reuses
`tests/trait_plan_support.py` so successful programs execute with and without
optimisation, both directly and after a bytecode serialization round trip.
`tests/generics_plan_registry.py` links those cases and a curated selection of
older regression tests to the original IDs. All 351 plan IDs have executable
mappings. The coverage test checks that the specification inventory and linked
test references remain valid; execution failures remain visible rather than
being treated as missing mappings.

## Running and interpreting the results

From the project root with Python 3.14 and dependencies installed:

```bash
uv run python -m unittest test_generics_plan -q
uv run python -m tools.generics_plan --smoke --jobs 6 --report generics-plan-results.json
uv run python -m tools.generics_plan --priority P0 --batch --report generics-plan-results.json
uv run python -m tools.generics_plan --batch --report generics-plan-results.json
uv run python -m tools.generics_plan --suite WH --list
uv run python -m tools.generics_plan --case GD-001 --case RK-005
```

Use `--timeout 30` to limit the execution time of **each isolated unit test**.
`--jobs` runs tests concurrently in independent Python processes, avoiding
shared analyser state between unrelated cases. `--batch` instead runs mapped
regressions in one Python process, which is fast but does **not** provide
per-test process isolation or enforce per-test timeouts; CI runs a separate
isolated smoke gate in addition to its complete mapped P0 batch. The runner can
run P1/P2 too; nightly CI covers all **mapped** cases. Use `--strict-coverage`
to make unimplemented selected rows a failure when a full gate is required. By default
missing oracle mappings remain visible in JSON and CLI totals without failing
CI; they are still outstanding test work.

The JSON report includes all 351 plan rows, each row's priority, oracle,
scenario, test references, run status and any unresolved-design annotations.
Results are **passed**, **failed**, **timed_out**, **uncovered**, **not_run** or
**not_selected**. A mapped case may exercise one aspect of a multidimensional
oracle rather than every mode or shape in the plan; additional fixtures are
still needed for comprehensive coverage. `--smoke` follows the proposed
high-signal initial gate and reports cases for which no concrete tests exist.

## Current specification decisions

The five formerly unresolved P0 cases are settled in §16 of `language.md`:
GD-004 permits unambiguous contextual result-type inference but rejects
unresolved calls; UN-002 joins `Int` and `Real` as `Real` and unrelated
`Int` and `String` as a union; UN-012 prohibits rugged `T~` from satisfying
uniform `T+`, and minimum-rank `T*` from doing so in `novec` calls (ordinary
runtime rank adaptation is distinct from subtyping); OV-005 follows latest-definition replacement for
alpha-equivalent overloads; IN-012 permits unused universally generic
inferred parameters. All five have executable positive/negative tests.

**GD-005** remains a separate historical grammar ambiguity: names-only
trait binders and bounded `define[T: Constraint]` are both supported, despite
older names-only wording for generic lists. Other tentative rank-combination
corners from §16.2 are not silently treated as settled by these decisions.

## Hardening continuation (2026-11-02)

The second pass adds 41 numbered oracles (39 P0, 2 P1), bringing mapped
coverage to **179/351** (P0 **160/221**, P1 **19/103**, P2 **0/27**).
The 172 unmapped cases are still outstanding, not passed. Current results:
**179/179 mapped cases pass** in the batch runner, **900/900** directly
selected compiler/type/trait/module/generics unit tests pass, and six
new fixes have passed in independent subprocesses. The full isolated smoke
run has not completed in this environment; do not count it as passed.
The full `test_runtime` module also exceeded the local execution window, and
therefore has no completed success result in this pass.

The additions include rank-four `exact`, generic `novec` broadcasting,
function-value `exact`, ranked/rugged rejection, two-argument generic
inference, object-friendly generic methods, generic variants and literal-backed case
payloads, generic indexing/update providers, multi-result functions,
import arity/conflicts, and nested immutable reconstruction.

Two compiler errors are now rejected at declaration analysis rather than
being treated as valid bytecode inputs:

* `@update(Box[T])` providers must return the same instantiated target
  `Box[T]`, not an unrelated `T` or different generic owner.
* Variant field defaults must match their declared types. Literal backing
  values are fixed constructor defaults and cannot be overridden by arguments.
  Bare cases must satisfy the parent's required friendly elements.

## Hardening continuation (third pass, 2026-11-02)

The third pass adds **24 more mapped P0 oracles** (18 independent source
fixtures and 6 incremental/stress methods), bringing the mapped inventory to
**203/351** (P0 **184/221**, P1 **19/103**, P2 **0/27**). The remaining
**148** are not mapped and must not be reported as passing. The batch runner
completed **203/203 mapped cases**; the isolated smoke runner completed **46/46
mapped smoke cases** with two of the 48 selected smoke IDs still uncovered.
The source/coverage module passed **156 tests**, a separate compiler/type/trait/
module/incremental group passed **788 tests**, and **46** end-to-end program
tests passed. Validation used Python 3.13; Python 3.14 and the full standalone
runtime module were not validated in this pass.

Source-level additions cover partial explicit generics together with overload
hints, generic-vs-vectorised and generic-vs-trait candidate priority, ranked
lifting, trait-generic correlation, unambiguous variadic tuple partitioning,
variadic return-position rejection, inferred overloaded function values,
generic dictionary lookup, imported selective generic overloads, full nested
optional values, and a runtime comparison of `where`-computed rank against
actual list shape. The incremental methods exercise dependency changes to a
generic function return, object variance, trait implication, and `where`
applicability (CX-006–009), with stress tests for rank 1 through 20 and
nested generic object depth through 12 (CX-010–011).

**Compiler fix:** `_compile_function_overload` computes hidden static parameter
names from each overload's own checked declaration, not from the first AST
member of an imported overload set. Otherwise an overload without a `where`
clause can accidentally acquire its sibling's hidden rank parameter, yielding
runtime call-arity faults even though static resolution selected the correct
member. The incremental CX-009 regression runs imported overloads with and
without the hidden rank in both optimisation modes, directly and after
bytecode serialization, including an edit that flips the applicable branch.

**Next targets:** the remaining **37 P0** cases, with emphasis on union-branch
runtime evidence, inferred higher-order generic returns, nontrivial call-site
checked tuples, tag validators, and results/error propagation. Preserve the
unresolved design decisions documented above rather than unilaterally changing
existing language behaviour.

No shared-state object, shared-state handle, or shared alias mutation oracle is
included. Ordinary variable replacement of immutable objects is tested.

## Hardening continuation (fourth pass, 2026-11-02)

Eighteen new source-level P0 cases extend the mapped inventory from 203 to
**221/351** (P0 **202/221**, P1 **19/103**, P2 **0/27**). New oracles
cover a return-only explicit generic with an optional result, row-field type
rejection, chained inferred callables, returned higher-order generic functions,
scoped anonymous function type variables, niladic function values, empty
variadic tuple matches, fixed/variadic tuple mismatches, `exact` filtering,
unit-tag constraints, generic tagged returns, tag absence constraints, runtime
refinement before generic invocation, `Result[T,E]` success/error propagation,
and generic index providers on named traits. All positive source cases are
executed unoptimised, optimised, and after serialization in each mode.

**Unresolved specification conflicts:** The implementation intentionally
prefers the latest candidate when two generic overloads yield equivalent
applied arguments and results (OV-005), while the proposed plan demands a
compile-time ambiguity. An unused untyped named function parameter remains
accepted as a generic callable (IN-012), despite the plan's rejection oracle.
These cases remain uncovered pending compatibility decisions.

**Validator hardening (TG-007):** A generic function can return an explicitly
validated computed tag when the tag operation has a concrete input type; an
invalid value faults under unoptimised/optimised and serialized execution.
A different form previously bypassed validation entirely:

```valiance
tag #positive as computed
define #positive(value: Number) -> #boolean Number => $value 0 > end
define[T] checked(x: T) -> #positive T => $x #positive end
-1 checked
```

The input `T` does not establish that the `Number` validator can be selected.
The analyser could retain an untyped `TagApplicationNode` in the surviving
function body, while the explicit result annotation made the callable appear
valid. Code generation then emitted a tag operation without a checked validator.
`_analyse_function_literal` now rejects *surviving* failed tag operations with
unreported diagnostics rather than allowing unchecked lowering. Crucially, it
does **not** reject diagnostics from unsuccessful speculative inference branches,
which can coexist with valid callable signatures. A focused negative regression
covers this path, and the TG-007 fault fixture covers a valid constrained
validator call. The inferred-type constraint policy for permitting the generic
`T` form remains a separate language design question; it is now safely rejected.

## Hardening continuation (fifth pass, 2026-11-02)

The fifth pass adds **28 mapped cases**, including **14 P0** and **14 P1**, for
**249/351 mapped and passing** (P0 **216/221**, P1 **33/103**, P2 **0/27**).
The **102 uncovered** rows must not be treated as passes. Tests added in this
pass exercise nested and swapped generic trait implications, structural
constraints, a real runtime input through generic calls, tag/trait/optional
union-dispatch plans, call-site checked output multiplicity, constructor
initialization, rank-four compatible views, union and intersection inference
rejection, nested generic binders, recursion, object field and method types,
closure capture, indexing, optional spelling equivalence, and generic mapping
of empty and 64-element lists. Positive cases execute unoptimized, optimized,
and after bytecode serialization in both modes.

The five remaining P0 rows (GD-004, UN-002, UN-012, OV-005, IN-012) require
specification or compatibility decisions. Notably, a rugged rank-two value
currently matches `T+` by binding `T` to a rugged rank-one type; the proposed
UN-012 negative assertion conflicts with that existing unification path.
Changing these rules without resolving the design would be a breaking language
change. No shared-state cases are included.

**Known unresolved correctness defects (not counted as passed):**

* **GD-018 (resolved in the seventh pass): missing numeric requirement on generic argument.** Previously the analyser
  accepted:

  ```valiance
  define[T] bad(x:T) -> Number => $x + 1 end
  bad("x")
  ```

  The analyser now rejects the declaration itself, even without a call. A
  generic source binder is universally quantified: it can use a non-generic
  operation only if its declared upper bound proves applicability, or an
  appropriate universally generic overload is visible. Explicit structural
  requirements in parameter types or generic binder bounds are supported;
  no requirements are inferred from the function body. `test_GD_018` checks
  both the rejection and accepted evidence paths.

* **OV-007: tagged generic vs untagged generic tie.** A concrete `#fresh Int`
  signature reliably beats an untagged generic regardless of declaration
  order, which is what the mapped OV-007 test asserts. Two *generic*
  signatures `#fresh T` and `T` currently select by declaration order for a
  tagged `Int`. This finer specificity rule remains unverified and should be
  resolved without changing other generic ranking cases unexpectedly.

Validation on Python 3.13: all **249/249 mapped** cases passed in-process;
**47/47 mapped smoke** cases passed in isolated subprocesses (one smoke row
uncovered); **504/504** selected generics, type, trait, module and incremental
unittests passed; and **46/46** end-to-end program tests passed. The complete
standalone runtime module and Python 3.14 CI were not run in this pass.

## Sixth pass: implicit optional and Result success representation (2026-11-02)

The language explicitly defines **all non-`None` values as implicitly `Some`**
and **all non-`Err` values as implicitly `OK`**. These are type/branch
interpretations, not requirements to allocate runtime wrappers. Explicit
`Some(...)` and `OK(...)` constructors remain valid; the payload of an explicit
wrapper determines the inner type. `Some(\None)` is present and must not be
confused with bare `\None`. `OK(error)` is explicit success, not an error
branch merely because its contained value implements `Err`.

Union dispatch in `runtime/vm.py::_runtime_pattern_matches` now follows the
same rule already used for checked casts. The `UC-008` source-level fixture
combines raw `1 as[Int?]`, explicit `Some(2)`, and bare `\None`; dedicated
regressions cover nested presence, unwrapped `OK[Int]` success, explicit
wrappers, error/absence discrimination, optimized execution and serialized
bytecode round trips. This resolves the previously documented optional union
missing-branch defect without changing the runtime representation.

The unrelated `GD-018` generic numeric constraint defect remained open at the
end of this sixth pass. It is resolved by the declaration-time check below.

Validation (Python 3.13): 249/249 mapped generics cases passed in a shared
process (102 uncovered), 47/47 isolated mapped smoke cases passed (one smoke
case uncovered), 205/205 generics and implicit-success source tests passed,
46/46 end-to-end program tests passed, and eight targeted runtime cases passed.
The entire runtime module and Python 3.14 CI were not run here.

## Seventh pass: prove explicit generic operation requirements (2026-11-02)

`define[T] increment(x: T) -> Number => $x + 1 end` is rejected when
`+` has only concrete overloads: the body must type-check for any `T`, even if
the function is never called. The analyser no longer solves source-level bound
`T` to `Int` while checking its body. Compiler-created inference metavariables
continue to use ordinary type inference.

An operation on generic parameters is accepted with an applicable explicitly
declared upper bound (`define[T: Number]`), an explicit structural trait bound
(`define[T: trait => extend +(T, Int) -> Number end]`), an anonymous trait in a
parameter type, or a visible genuinely universal overload such as
`define[U] +(x: U, i: Int) -> Number => 19 end`. These are independent proofs,
not requirements inferred from the function body. A `T: above Number` lower
bound does not prove that `T` itself supports numeric arithmetic.

Lexically scoped upper bounds live in `Context.type_var_upper_bounds`. Overload
solving can use those bounds as input views without erasing the source-level
binder or leaking constraints across functions. Negative data-tag requirements
from a resolved overload remain attached to the original generic variable,
not a concrete upper-bound view.

The `GD-018` fixture tests both rejection and accepted evidence paths,
including indirect generic forwarding, nested object types, valid inference
metavariables, and optimized and serialized execution for positive programs.
Validation: 250/250 mapped generics cases passed (101 uncovered),
47/47 isolated mapped smoke cases passed (one smoke case uncovered),
and 646/646 selected compiler, type-system, trait, module, and end-to-end
program tests passed. The full runtime module and Python 3.14 matrix were
not executed in this pass.

## Eighth pass: alpha-equivalent imported overloads and P1/P2 coverage (2026-11-02)

The importer compares overload **parameter patterns** after canonicalizing the
names of that overload's own generic binders. `pick[T](T)` and `pick[U](U)`
therefore conflict when imported from separate modules under the same visible
name; they are the same signature even though the source binder letters and
module-scoped identities differ. Distinct correlation patterns such as
`(T, T)` versus `(T, U)` do not conflict. Return types remain irrelevant to
parameter-signature conflict checks, consistent with existing import policy.
The same comparison applies to source-free compiled module interfaces.

`vtypes.overload_parameter_shape` provides the canonical parameter tuple;
`analysis/declarations/imports.py` uses it both when checking pending/imported
conflicts and when committing the successfully imported signatures. The
OV-019 fixture verifies alpha-renamed conflicts, non-conflicting correlated
patterns, and compiled-module persistence. No implicit generic operation
requirements were introduced; explicit constraints and genuinely universal
operator overloads remain the only permitted generic-body evidence.

The new fixtures map **23 additional plan IDs**: 20 P1 and 3 P2. They exercise
module-local generic binders, import visibility and aliasing, named-ECS default
arity, row-field inference, higher-order trait evidence, closure capture
restrictions, empty generic maps, generic rank/optional spelling equivalence,
source-free bytecode round trips, 50 distinct nominal specializations, rejected
candidate stress, cached/fresh type agreement, and metamorphic comparisons.
All positive source fixtures run in both optimization modes and after bytecode
serialization. No shared-state semantics are tested.

Validation on Python 3.13: **273/273 mapped cases passed** (P0 216/221,
P1 54/103, P2 3/27), leaving **78 uncovered**. The isolated smoke gate passed
47/47 mapped cases (one of the 48 smoke IDs remains uncovered). Independent
suite runs passed: dedicated generics 226/226, registry 2/2, type library
185/185, compiled modules 59/59, public re-exports 16/16, traits and variants
44/44, analyser 482/482, and end-to-end programs 46/46. A combined all-suites
command timed out and is not counted as a pass. The complete standalone runtime
suite and Python 3.14 CI matrix remain unverified. The five outstanding P0
specification decisions are unchanged: GD-004, UN-002, UN-012, OV-005, IN-012.

## Typed generic field defaults and serialized static rank parameters

A field default is part of an object's declaration contract, not a value that
may silently narrow the object's generic type at the first constructor call.
When a field declares a type, the analyser checks every possible default
branch against that declared type during object declaration. A concrete
`String` default for a field of unconstrained `T` is rejected, even when the
object is never instantiated; the analyser accepts an absence default for `T?`. The latter is an
analysis-only control; compiling that default currently fails because `\None`
is not lowered as a supported object-default literal. Runtime support for this
existing limitation remains separate work.
The numbered OB-016 test asserts the rejection and independent positive
controls assert legitimate defaults.

Rank variables computed in `where` clauses can survive into callable bytecode
as symbolic `RankVariable` operands, resolved using validated hidden static
arguments at execution. Portable bytecode encodes these as a named, tagged
value (format version `0x46`); the reader continues to accept `0x43` through
`0x45`. WH-017 checks an empty shape tuple and the minimum legal result rank,
including both optimizer modes and serialization/reload. This does not grant
arbitrary compile-time execution or infer additional constraints.

The next source-level fixtures cover higher-order row substitution, generic
result/error branches, call-site arity ordering, imported generics within
function bodies, runtime generic argument arities, multiple generic index
selectors, and alias/definition-order metamorphic invariants. They do not
change the five outstanding P0 semantic decisions.

## Generic inference and ranked-list safety after P0 reconciliation

The five settled P0 rules are exercised by source-level cases GD-004, UN-002,
UN-012, OV-005, and IN-012. Return-only generic calls may be specialized using
an explicit type argument or a *concrete* expected result type from a typed
assignment, a declared function return, or a concrete explicit call argument.
In every case the generic function body remains checked under its original
explicitly declared requirements, with no inferred operator or trait bounds.
A call whose return-only generic remains unsolved is a diagnostic, not a
concrete runtime signature. The specialization helper is in
`analysis/support/contextual_returns.py`; assignment and call-source analysis
use it only for the immediately preceding result-producing expression.

The collection rank solver does not peel rugged `~` rank to match homogeneous
`+` patterns. The existing minimum-rank dynamic call adapter is retained for
vectorising calls; a `novec` exact-list argument cannot use that adapter.
Three old analyser tests that assumed a rugged-list `fold`/`addAll` call could
be treated as homogeneous now assert a compile-time no-overload diagnostic.
This makes the subtype boundary explicit rather than accepting an unsafe
higher-order call. A dedicated rugged-aware folding overload would be a
separate feature, not an implicit generic conversion.

## Variant, collection union and static type-tuple invariants

A variant's generic variance includes positive uses from its immutable member
payload fields, in addition to the behaviour requirements of the variant.
Generic objects continue to count publicly writable fields as invariant.

Type normalization must not factor `Int+ | String+` into
`(Int | String)+`: a homogeneous-list alternative is not equivalent to a
heterogeneous list. When `length` or another operation propagates a negative
tag constraint back to a union-typed function input, preserve the original
union branches rather than replacing them with the wider parameter view
selected during overload checking.

`where` introspection exposes `.inputs` and `.outputs` as tuples of types.
Their `==` and `!=` comparisons check matching lengths and per-position
canonical type equality; they are still fully static, with no extra callable
operations enabled. VA-011, RK-015 and WH-018 cover these contracts across
unoptimised/optimised execution and bytecode serialization.

## Adversarial cross-phase tests beyond the inventory

`tests/test_generics_adversarial.py` extends the numbered plan with a fixed seed
(`20261102`) and source-level, independently reproducible permutations. These
checks are *additional* to the 351 numbered cases, not 352nd-and-later rows in
the coverage registry. Positive fixtures run both optimiser modes, with and
without portable bytecode reload; negative fixtures require a type diagnostic
or the expected vectorisation fault.

The generated cases cover ranked identity and `where`-computed rank results,
`where` overload fallback irrespective of declaration order, static function
input/output type-tuple equality, implicit/explicit `Some` and `None` dispatch,
commutative numeric promotion and union inference, strict homogeneous vs.
minimum/rugged rank matching, alpha-equivalent last-definition replacement,
and vectorised calls with hidden `where` values (including imported overloads,
empty lists and mismatched lengths).

**Hidden static arguments at vectorised user-function calls:** resolved element
references describe vectorisation depths/stop ranks for ordinary source
arguments. `where` rank or computed values are appended to a function's
physical arguments but are scalar; the VM appends zero vectorisation depth and
no target rank for each hidden value when the source metadata does not already
include it. This must also hold for optimized and serialized calls: otherwise
a perfectly valid `where` operation can fail with invalid vectorisation depth
metadata before its body executes. Other invocation paths that already include
hidden argument metadata are not padded twice.

Run with `PYTHONPATH=src:tests python -m unittest test_generics_adversarial -q`
(or use `uv run` with the project's configured environment).
