# Type-system counterexample campaign — first implementation pass

Baseline: `Valiance_generics_hardening_v14.zip`, user-supplied on 2026-11-02. Original plan: `docs/Valiance_Type_System_Test_Plan.md`. Comparative reading: the user's `stedolan counterexamples main src.zip` (Counterexamples in Type Systems source chapters). This pass changes **tests, runner and documentation only**; no Valiance analyser, type algebra or runtime implementation code was changed.

## Scope and status

The plan specifies **79 IDs**: 70 risk-specific tests (R01–R08 and C09–C12), plus nine X sentinels. This implementation contains **42 distinct mapped source-level tests**. The run is **41 passed, 1 failed, 37 uncovered**. Do not describe this as 79 cases passing. Priority totals are P0 45 (30 passed, 15 uncovered); P1 22 (11 passed, 1 failed, 10 uncovered); P2 12 (0 passed, 12 uncovered).

Each positive source fixture with a runtime oracle is evaluated unoptimized and optimized, both directly and after serialization/reload; negative fixtures must **parse** and produce a meaningful analyser diagnostic. Compile-only positives test static acceptance and/or type, rather than runtime behaviour. The case registry and JSON report explicitly identify that difference. Some fixtures exercise a *slice* of a multidimensional test-plan scenario; passing a source fixture is not a proof of soundness of the complete feature.

### Observed failure: B01 — bounded generic method evidence

The following Valiance source fails at declaration analysis:

```valiance
trait Sized => extend size -> Int end
object Item => $value:Int end
object Item as Sized => define size=>$self.value end
define[T:Sized] readSize(x:T)->Int=>$x size end
Item(3) readSize
```

The analyser reports:

```
no overloads for element 'size' match stack [T]
available overloads:
  - size(Item) -> Int
```

Controls pass: replacing the generic parameter `T` with concrete trait parameter `Sized` permits `$x size`; keeping `T:Sized` while returning a constant also compiles. That isolates loss of method evidence from a named bound in the generic body. It appears to be a **constraint-resolution / completeness gap**, not a demonstrated violation of type safety; triage before applying any language change. The B01 fixture is intentionally **failing**, not marked xfail or skipped. This plan's B01 also calls for a negative missing-evidence case; that side has not been implemented as an independent oracle yet.

### Not yet executed

The 37 uncovered IDs remain in the registry rather than being silently dropped. They include conditional/spec-dependent aliases, recursive constraints, and ownership/FFI cases. For each new case, confirm feature availability and legal syntax before choosing a pass/reject/N/A oracle. In particular, parse errors do not count as type-checker rejections.

## Execution

From the repository root, with project dependencies and Python 3.14 where available:

```bash
PYTHONPATH=src:tests python -m unittest test_type_soundness_registry -q
PYTHONPATH=src:tests python -m unittest test_type_soundness_counterexamples -q
PYTHONPATH=src:tests python -m tools.type_soundness_plan --report soundness-results.json --jobs 6
PYTHONPATH=src:tests python -m tools.type_soundness_plan --priority P0 --strict-coverage
PYTHONPATH=src:tests python -m tools.type_soundness_plan --case B01
PYTHONPATH=src:tests python -m tools.type_soundness_plan --list
```

The suite exits nonzero while B01 fails. `--strict-coverage` additionally fails for uncovered selected IDs. Isolated runner tests have per-case time limits; a timeout is never a passing rejection. The baseline here ran with Python 3.13.5, not a verified Python 3.14 CI environment.

Completed nearby regression validation: 319 existing generics/adversarial and new registry tests passed. A separate attempt to run the *entire* original 351-case mapped generics inventory exceeded the execution window without a completed report, and therefore is **not counted as passed** in this pass. The new soundness campaign is not a substitute for existing full CI.

## Next actions

Finish the 15 uncovered P0 cases first (variance through private and inherited interfaces, rank/unification contradictions, runtime variant evidence, and polymorphic-union refinement). Then complete the 10 missing P1 cases and 12 conditional P2 cases, documenting N/A only after checking supported grammar. Investigate B01 independently before any compiler change. Preserve the user's established Valiance semantics: explicit generic constraints, `T+ <: T* <: T~`, numeric widening and unrelated-type unions, later equivalent overload wins, implicit `Some` and `OK` for non-None/non-Err values.

## Phase 2 — P0 completion and P1 extension (tests only)

The compiler and VM remain **unchanged** from v14. This phase adds 23 mapped
fixtures, from 42 to **65 of 79**; each source test is run via the existing
parse/analyse and optimized/unoptimized/serialized harness. Existing Phase 1
fixtures are retained, including the failing named-trait bound B01 regression.

**Completed priority-scoped validation:** P0: **45 passed, 0 failed, 0 pending**;
P1: **18 passed, 2 failed, 2 pending**; P2: **12 pending**. Together: **63 passed,
2 failed, 14 not yet implemented**. A combined all-priority command exceeded
this environment's execution window, so the JSON report is assembled from the
completed independently run P0 and P1 reports. No aggregate all-at-once pass
is claimed. `test_type_soundness_registry` and the four focused P1 follow-on
oracles also completed successfully.

**D02 — second observed failure (imported nominal alias identity).** One module
exports `object[T] Box`, and the consumer imports both `Box` and `Box as Alias`.
Assigning `Alias(2)` to a `Box[Int]` variable yields:

```
3:1: cannot assign Alias[Int] to variable 'x' of declared type Box[Int]
```

Both names resolve to the **same source declaration**, so this appears to be a
nominal identity/alias normalization defect. It is distinct from B01 (which
still reports `no overloads for element 'size' match stack [T]`). Both remain
failing positive regressions. No compiler behavior was changed to suppress them.

**Coverage qualification:** A plan ID with a source fixture covers its specific
observable assertion, not necessarily all dimensions in the prose scenario.
For instance, V04 confirms read-only receiver parameters prevent a cross-instance
write; it does not establish every possible private method/variance escape path.
V05 confirms that a factory invoked through a widened generic view cannot be
assigned back to a narrower generic owner. P06 checks a stable union-branch
runtime result in all supported compiled modes. Conditional/unsafe FFI and
recursive-type cases remain tracked separately rather than being credited from
parser errors or unsupported syntax. `B02` confirms that object parameter
bounds `object[T:Number]` are implemented, despite the plan's grammar caveat.

**Recommended next sequence:** implement X06/X07 with real native/unsafe
fixtures when the relevant FFI test prerequisites are available; then handle
C10–C12 as supported grammar probes (with explicit N/A only where justified).
Triage B01 and D02 independently before making implementation changes.

## Phase 3 — final 14 conditional and native-boundary IDs (tests only)

Baseline: Phase 2; **no changes to parser, analyser, runtime, stdlib or type algebra**. All
79 planned IDs are now explicitly dispositioned. The 14 previously unimplemented IDs
are X06, X07, A01–A04, REC01–REC04 and DR01–DR04. Two of these are marked
**not applicable** rather than pretending an unsupported recursive type-alias
syntax is a type-checker soundness rejection.

Completed isolated priority runs (Python 3.13):

| Priority | Pass | Fail | N/A | Uncovered |
|---|---:|---:|---:|---:|
| P0 | 45 | 0 | 0 | 0 |
| P1 | 20 | 2 | 0 | 0 |
| P2 | 9 | 1 | 2 | 0 |
| **Total** | **74** | **3** | **2** | **0** |

**New failure A01 — hidden nominal identity / module scope:** A library privately
creates its own `Secret` object with an `Int` payload, then exposes a public
factory returning that type. The consumer independently declares `Secret` with
a **String** payload, and the analyser incorrectly accepts assigning the
library's `Secret` value into the consumer's `Secret` variable. This breaks the
intended nominal-module identity separation and can misrepresent the field's
static type. The negative regression intentionally fails; no fix included.

**Existing failures:** B01 (trait-bound method evidence) and D02 (import alias
identity) remain failing, independently reproducible. The phase adds no
implementation changes.

**Native checks:** X06 inspects the analyser's `Unsafe` effect on the raw linked
call, a generic relay, and a regular caller. X07 verifies that an `@nodup`
linked native handle with a declared field rejects ordinary field-write
reconstruction. No external C binary is invoked; X06 is an effect-analysis
contract, not a foreign-execution test.

**Conditional scope:** A02 checks exported row requirements; A03 two public
aliases of one inferred generic function; A04 prevents an anonymous binder
from being assigned outside its scope. REC01 and REC02 check upper/lower
constraint evidence and absence of trait-evidence from a cycle. REC04 checks
ordinary `@recursive` *declaration typing*; runtime termination is already
covered by the existing runtime suite and not asserted in this new fixture.
DR01 checks incompatible recursive generic/function constraints; DR04 checks
missing generic-variant arms.

**N/A, not a pass:** DR02 and DR03 verify that Valiance's parser does not
provide the proposed `type Name = ...` facility; the source tests then call
`skipTest`, which the plan runner counts as `not_applicable`. If recursive
aliases are added later, these two cases need real type-equation oracles.

**Qualified REC03 coverage:** The cross-module mutual trait relationship is
rejected at module resolution (`module 'a' has no public component 'A'`) rather
than analysed as a satisfiable recursive type equation. This is a controlled
failure to resolve a cyclic dependency and shows no spurious evidence or
compiler ICE, but **does not prove a general cycle solver sound**. Treat deeper
cross-module recursive constraints as additional future testing.

Every executed negative uses a meaningful diagnostic rather than accepting
an unrelated parse failure. The full all-at-once 79-case process was not used
as validation; completed P0, P1 and P2 subprocess runs have separate JSON
reports combined by case ID. A successful test fixture covers only its
specified case's executable oracle, not every possible source variation.

## Phase 4 — repairs for A01, D02 and B01

Baseline: Phase 3 (all 79 cases mapped; 74 pass, 3 fail, 2 N/A). This phase
changes the analyser's import type identity and explicit-bound evidence checks;
it adds a separate `test_type_soundness_repairs.py` module. It does not alter the
original 79 case oracles, nor turn unsupported type-alias tests into passes.

* **A01: private provider nominal provenance.** Imported public function
  signatures qualify references to *private* nominal declarations with the
  provider module. An independently defined `Secret` in the consumer is not
  considered `lib.Secret`, even when fields happen to coincide. Call-site-
  checked function bodies remain in the provider's lexical scope: qualifying
  their internal AST names would break private construction/reflection.
* **D02: one declaration, multiple import spellings.** The type context records
  the origin module and declaration name for each imported object alias.
  Subtyping and generic argument matching use that provenance, so `Box[T]`
  and `Alias[T]` can interchange when both import the same declaration. Equal
  spelling from two different providers does **not** imply type identity.
* **B01: explicit named trait bound.** A generic body's lexical analyser
  exposes operations *declared in the named bound's trait*, using the bound
  variable as receiver. Calls use **nominal trait dispatch**, rather than
  the structural-witness mode for anonymous structural traits. No bound is
  inferred from the body. Calls with non-implementers remain invalid.

### Verification

The complete 79-case in-process run reports **77 passing, 0 failing and 2 N/A**
(DR02 and DR03: unsupported recursive type aliases/equations). Seven new
independent repair regressions also pass, including optimized/unoptimized
and bytecode reload execution where applicable. The analyser/type suite
passes 667 tests; the generics/type suite passes 493 tests; modules, public
re-exports, trait/variant and soundness tests pass 207 tests with two skips;
optimizer, bytecode and end-to-end tests pass 166 tests with one skip.
Some groups share test modules, so these totals must not be added as a count
of distinct tests. The standalone runtime module did not complete within
this execution window and is not counted as passing.

### Separate known limitation

A **different, pre-existing** provider-private constructor issue is exposed
by importing a public function whose body directly calls its private object
constructor. A call such as `one.make(4)` can raise `undefined name 'Secret'`
at runtime even when the static result correctly retains `one.Secret`.
This also reproduces on the unchanged Phase 3 baseline. It is **not** resolved
by the three soundness fixes; supporting that path requires provider-private
runtime dependencies to remain linked without leaking or colliding with
consumer-local declarations. The mapped A01 case checks unsafe assignment
rejection, not construction through a private factory at runtime.
