# Valiance — Type-System Counterexample Testing Plan

Version 1.0 | 1 November 2026 | Planning status: not executed

## Executive scope

Purpose. Turn the counterexample audit into an executable, falsifiable soundness-regression program for Valiance. This is a plan, not a report of compiler results.

Interpretation. The earlier 8 primary / 4 conditional / 19 mitigated-or-out-of-scope classification is a TESTING PRIORITIZATION, not evidence of eight bugs. Counterexamples published for Java, Scala, OCaml, Rust etc. rarely transfer syntactically to Valiance; each fixture tests the underlying type-safety invariant.

Implementation status. No Valiance executable or compiler source was provided, and `vln` was not available in the working environment. None of these cases have been executed. Some items are deliberate design-confirmation probes because the specification has unresolved or overlapping grammar notes.

**Coverage**: 12 risk packs; 70 risk tests; 9 cross-cutting checks; 12 worked fixtures; 31 source chapter dispositions. Of the chapters, 8 are primary audit topics, 4 conditional, 19 mitigated/out of scope. These counts are classification, not bug counts.

## Exit criteria and priorities

**P0 gate**: 100% implemented/available P0 negative tests reject at type-check stage; P0 positives execute without type violations; all available P0 metamorphic checks agree; no compiler ICE. **P1 gate**: all P1 tests are pass or explicitly tracked blocked/N/A with justified owner. **P2 gate**: grammar-sensitive tests are resolved to pass/reject/N/A, never silently skipped. **Release gate**: no confirmed unsoundness remains open; regressions retain minimum reproducers.

## Execution model and oracles

**Triage.** P0 first: R01, R02, R03, R04, R06, R07 and X03–X05. P1 next: R05, R08, C09, X01–X02, X06–X09. P2: C10–C12 and scope-dependent declarations.

**Stages.** (1) Parse/normalize; (2) static type/constraint inference; (3) overload and exhaustiveness; (4) lower/optimize; (5) interpret/run; (6) compile/run serialized bytecode; (7) multi-module link.

**Test types.** Positive: should compile and have a specific type/result. Negative: must be rejected at compile time. Metamorphic: two equivalent programs/paths must agree. Design-confirmation: first decide feature availability/semantics, then turn into executable tests.

**Primary oracle.** The compiler must not accept an unchecked cast, wrong return representation, contradictory bound, improperly scoped quantified variable or ambiguous dispatch that becomes a runtime type fault in otherwise safe Valiance code.

**No compiler ICE.** Compilation errors should be controlled diagnostics. Internal errors, crashes, hangs and unbounded typechecker recursion are failures even for malformed inputs.

**Safe/unsafe partition.** Pure and safe-language fixtures should uphold the soundness claim. Explicit FFI is Unsafe; its behavior is audited separately and not treated as a safe-language proof.

**Backend comparators.** For accepted tests, compare optimized and unoptimized compiler modes, interpreter vs compiled bytecode if both supported, and module/import variations; compare full output, return types, and tagged runtime evidence.

**Change hygiene.** Pin source/spec revision, Valiance compiler commit, host OS/architecture, test run seed, optimization flags, module layout and expected diagnostic category. Any intentional rule change updates the spec and oracle together.

## Risk register and case catalog

### R01 — Inferred variance and generic interfaces [P0; Primary]

**Counterexample source:** `src/incomplete-variance.md`. **Valiance references:** §12.2–12.4; §13; §16.

**Failure hypothesis:** A generated covariance/contravariance judgment ignores an observable input position, private-access path, generic method, constructor exposure, or nested function variance. **Safety invariant:** Every permitted upcast preserves all observable operations; writable public fields and both positive/negative occurrences cannot be treated covariantly.

#### V01 — Immutable collection covariance (positive)

**Setup:** Define two nominal types Narrow <: Wide using object-as-trait. **Stimulus:** Pass Narrow+ to a function accepting Wide+; inspect returned elements. **Expected oracle:** Accept; all elements retain compatible runtime and static types.

#### V02 — Public write forces invariance (negative)

**Setup:** Declare object[T] Cell with public $value: T (ordinary value-style field writes). **Stimulus:** Try Cell[Narrow] as a Cell[Wide], then reconstruct it with a Wide-only value. **Expected oracle:** Reject the upcast if write access is exposed. Do not rely on runtime copies to justify an invalid type.

#### V03 — Readable-only covariance (positive)

**Setup:** Declare a generic object with a readable field of T and no exposed T-consuming operation. **Stimulus:** Upcast Box[Narrow] to Box[Wide] and read its payload. **Expected oracle:** Accept only if the inferred interface is genuinely covariant; read remains sound.

#### V04 — Private cross-instance write (negative)

**Setup:** Declare generic object with private field of T; define an object-friendly element taking a second instance and attempting to replace its field. **Stimulus:** Widen the second instance via inferred variance before the cross-instance operation. **Expected oracle:** Reject unsafe widened use or infer invariance; object-friendly access must not bypass variance checks.

#### V05 — Constructor and method exposure (negative)

**Setup:** Declare a generic object whose constructor or exposed factory consumes T; make returned objects readable at a wider type. **Stimulus:** Call the exposed constructor/factory through a widened generic interface with incompatible input. **Expected oracle:** Reject any route that could manufacture a supposedly narrow T from a wider value.

#### V06 — Nested callable polarity (metamorphic)

**Setup:** Create fields or trait requirements with Function[T -> Int], Function[Int -> T], and Function[T -> T]. **Stimulus:** Check inferred variance; compose with Narrow <: Wide and call in both directions. **Expected oracle:** Input use => contravariant; output use => covariant; both => invariant, subject to full visibility.

#### V07 — Trait inheritance preserves polarity (negative)

**Setup:** Define a trait with a T-consuming requirement; implement it through a trait-to-trait behavior set. **Stimulus:** Attempt to use an inferred covariant subtype after combining inherited requirements. **Expected oracle:** No requirement may disappear during flattening; reject unsafe widening.

### R02 — One consistent generic instantiation [P0; Primary]

**Counterexample source:** `src/anything-once.md`. **Valiance references:** §16.1–16.4; §6.5–6.6; §7; §11.1.

**Failure hypothesis:** A generic variable acquires different solutions in different argument, rank, return, or overload positions within one invocation. **Safety invariant:** For each call, every occurrence of a quantified variable has one compatible substitution and all side-conditions are rechecked after combination.

#### U01 — Identity independently instantiated (positive)

**Setup:** Define define[T] id(value: T novec) -> T => $value end. **Stimulus:** Call id(42) and id("x") in distinct expressions. **Expected oracle:** Both compile with independent per-call substitutions.

#### U02 — Shared T across two parameters (negative)

**Setup:** Define define[T] sameType(a: T novec, b: T novec) -> T => $a end. **Stimulus:** Supply unrelated nominal values; attempt to force a narrower result than any common supertype guarantees. **Expected oracle:** Reject incompatible expected-result assumption or infer a safe union/supertype; never mislabel the result.

#### U03 — Rank subtraction and combination (negative)

**Setup:** Use signatures where T appears as T+2 and T+ in the same define. **Stimulus:** Supply exact-rank and minimum-rank inputs whose individually inferred residual T ranks conflict. **Expected oracle:** Reject or select one provably consistent T; never silently choose different residual ranks.

#### U04 — Exact-rank shape cannot be absorbed (negative)

**Setup:** Define rankFour(values: T+4 exact) -> T+4 from §16.1. **Stimulus:** Call with rank 3 and rank 5 values, including types inferred through unions. **Expected oracle:** No overload candidate; exact is tested before generic inference.

#### U05 — Atomic evidence cannot change T (negative)

**Setup:** Use a function with one atomic occurrence and a separate unconstrained occurrence of T. **Stimulus:** Pass a scalar and a ranked list forcing contradictory interpretations of T. **Expected oracle:** Reject unless the same T satisfies both; atomic is call-policy evidence only.

#### U06 — Vectorisation does not rebind T (negative)

**Setup:** Use define[T] on T novec and T vectorising positions; create mixed-rank actual arguments. **Stimulus:** Call with an outer rank which would encourage vectorisation of the wrong parameter. **Expected oracle:** Novec position binds the complete type; all vectorised calls share the correct substitution.

#### U07 — Repeated tuple segment coherence (negative)

**Setup:** Use parameter pattern {T..., String} and another parameter of T. **Stimulus:** Supply a tuple of inconsistent candidate types; exercise zero, one and multiple repeated entries. **Expected oracle:** Accept only consistent instantiations; no inference from a repeated segment may silently conflict.

### R03 — Runtime refinement and reified evidence [P0; Primary]

**Counterexample source:** `src/runtime-misinformation.md`. **Valiance references:** §2.4–2.5; §6.5 union-covered; §10.1; §14; §17.

**Failure hypothesis:** A runtime `as?`, match or branch dispatch reports a narrower type not warranted by value evidence. **Safety invariant:** A successful refinement supplies the exact structural, nominal, rank, generic, and tag evidence required by the static target; failure is safe.

#### RT01 — Nominal refinement (positive)

**Setup:** Use two unrelated object types in a union and match each via as . **Stimulus:** Execute both union alternatives. **Expected oracle:** Only the correct branch accepts each value; result types agree with selected branch.

#### RT02 — Generic argument reification (negative)

**Setup:** Construct Box[Int] and an unrelated Box[String] with the same object head. **Stimulus:** Attempt as?[Box[String]] on Box[Int]. **Expected oracle:** Return None, never Some[Box[String]]; no generic erasure in runtime checks.

#### RT03 — Rank-refinement precision (negative)

**Setup:** Construct Int+, Int+2, minimum-rank and rugged-rank candidates. **Stimulus:** Request as?[Int+2] against rank-1 input and vice versa. **Expected oracle:** Only structurally compatible rank checks succeed; no shape-only false positives.

#### RT04 — Computed and constructed tag checks (negative)

**Setup:** Use explicit #sorted and #infinite tags with matching and nonmatching evidence. **Stimulus:** Check tagged and untagged values through as?, match, overlays and bytecode reload. **Expected oracle:** Refinement cannot invent required tags; permitted erasure/removal follows tag policy.

#### RT05 — Nested optionals preserve Some/None (negative)

**Setup:** Construct None, Some[None] and Some[Some[Int]] using documented optional depths. **Stimulus:** Cast/match values at T?, T??, and T?3 and use safe access. **Expected oracle:** Depths must not be conflated; a failed depth check yields None or compile rejection.

#### RT06 — Union branch plan is stable (negative)

**Setup:** Define overloaded callable covering Number and String then map over (Number|String)+. **Stimulus:** Execute values of each union branch and compare to direct corresponding calls. **Expected oracle:** Recorded branch-to-overload plan is respected; runtime must not choose a narrower incompatible overload.

#### RT07 — Variant tags require parent evidence (negative)

**Setup:** Define computed parent #sorted with runtime variants #ascending/#descending. **Stimulus:** Remove parent, apply disjoint tag, then attempt match as #ascending. **Expected oracle:** No stale variant branch succeeds; variant cannot exist without its parent.

### R04 — Overload selection, polymorphism and dispatch [P0; Primary]

**Counterexample source:** `src/overloading-polymorphism.md`. **Valiance references:** §4.2; §6.5–6.6; §11.1–11.3; §20; §23.6–23.15.

**Failure hypothesis:** Static overload, trait/generic abstraction, module imports, or runtime multimethods disagree about which implementation is callable or what it returns. **Safety invariant:** Overload choice is deterministic or diagnosed ambiguous; call-site and runtime dispatch respect declared arity, multiplicity, and result contracts.

#### O01 — Specific-over-general (positive)

**Setup:** Provide overloads for Number and Number|String of one same-arity element. **Stimulus:** Call with a Number and a String. **Expected oracle:** Number selects most-specific Number overload; String selects union overload.

#### O02 — Ambiguous trait intersection (negative)

**Setup:** Provide incomparable overloads on two traits implemented by one object. **Stimulus:** Call without and with explicit overload disambiguation. **Expected oracle:** Undisambiguated call is compile error; explicit choice calls specified overload.

#### O03 — Generic substitution before selection (negative)

**Setup:** Create generic overload and a concrete overload whose eligibility changes after solving T. **Stimulus:** Exercise under inferred function and explicitly typed function caller. **Expected oracle:** Same concrete call chooses same sound overload or is rejected at both call sites.

#### O04 — Union branch coverage (positive)

**Setup:** Use numeric and string `* 2` overloads with (Number|String)+. **Stimulus:** Map the overloaded function across [1, "A"]. **Expected oracle:** Produce [2, "AA"] with one unambiguous branch per union component.

#### O05 — Overlapping runtime union branches (negative)

**Setup:** Construct two overlapping union branches with different recorded overload choices. **Stimulus:** Pass overload-set callable into a union-covered higher-order operation. **Expected oracle:** Reject the callable when overlap could dispatch inconsistently.

#### O06 — Multimethod fallback contract (negative)

**Setup:** Declare normal fallback and multi specialization with incompatible return type or multiplicity. **Stimulus:** Attempt to compile and invoke through trait-typed receiver. **Expected oracle:** Reject incompatible specialization; never expose runtime output that violates fallback static type.

#### O07 — Module/import override coherence (metamorphic)

**Setup:** Split overloads across imported and local modules; add excluded and owner-qualified variants. **Stimulus:** Compare direct call, qualified call and call through function value under import order changes. **Expected oracle:** Selection follows documented override rules; irrelevant import ordering changes cannot silently alter type safety.

#### O08 — Fixed arity across all overloads (negative)

**Setup:** Define two overloads of same name with distinct arity or multiplicity. **Stimulus:** Compile module and try an ECS call. **Expected oracle:** Compile error as required by §11.3; no runtime overread or dropped output.

### R05 — Distinctness, injectivity and exhaustiveness [P1; Primary]

**Counterexample source:** `src/distinctness-injectivity.md`. **Valiance references:** §2; §10.1; §14–16; §17.6; §22.

**Failure hypothesis:** An optimizer or exhaustive-match checker treats types as provably distinct using an unjustified injectivity or normalization assumption. **Safety invariant:** A branch may be pruned only when its impossibility is established by sound nominal/structural reasoning; opacity and unresolved generics are conservative.

#### D01 — Nominal constructor distinction (positive)

**Setup:** Declare independent object constructors with equal field shapes. **Stimulus:** Match a union by object type and exhaustively enumerate actual members. **Expected oracle:** Do not conflate distinct nominal identities solely from equal structure.

#### D02 — Generic constructor vs alias normalization (design-confirmation)

**Setup:** Create parameterized nominal objects and any supported alias/renaming mechanism. **Stimulus:** Ask checker whether C[Int] vs C[String] or equivalent aliases can overlap. **Expected oracle:** No injectivity assumption may be applied to an arbitrary type-level mapping; if type aliases are unsupported record N/A.

#### D03 — Closed-variant exhaustiveness (negative)

**Setup:** Define variant with two member constructors, one carrying a generic payload. **Stimulus:** Compile a match omitting a reachable constructor; repeat with a formally impossible arm. **Expected oracle:** Reject omission of any reachable arm; unreachable-arm suppression requires a derivation.

#### D04 — Disjoint tag contradictory signature (negative)

**Setup:** Define `tag #A disjoint #B`. **Stimulus:** Declare a parameter requiring #A #B at the same depth. **Expected oracle:** Compile error per §17.6; no value is assumed to inhabit contradictory tags.

#### D05 — Where-clause type equality conservatism (negative)

**Setup:** Use where type equality/inequality on unresolved generic type values. **Stimulus:** Make overload validity depend on a claim of unequal generic constructor arguments. **Expected oracle:** Do not prove inequality from an unproved constructor-injectivity principle.

#### D06 — Optimization/exhaustiveness agreement (metamorphic)

**Setup:** Build equivalent match programs: explicit wildcard, exhaustive cases, and simplified match. **Stimulus:** Run interpreted vs compiled and with/without optimizer across generic instantiations. **Expected oracle:** Identical behavior; no absent branch can become reachable only after optimization.

### R06 — Type-variable scope escape and generalization [P0; Primary]

**Counterexample source:** `src/scope-escape.md`. **Valiance references:** §3.4; §6.3–6.6; §11; §16.3–16.4; §22–23.

**Failure hypothesis:** An inferred free type variable, row variable, rank variable or generic evidence survives outside the binder that justified it. **Safety invariant:** Every type variable in an exported/inferred signature is bound at a scope visible at its use; higher-rank generic promises are checked at their own binding sites.

#### S01 — Independent closure instantiation (positive)

**Setup:** Define id as fn (x) => $x end; pass it as a higher-order value. **Stimulus:** Call via different separate specializations in valid scopes. **Expected oracle:** Distinct calls instantiate correctly without leaking an internal @n.

#### S02 — Higher-rank escape through closure (negative)

**Setup:** Accept a generic polymorphic identity callback and create a closure that forwards its argument to an outer monomorphic function. **Stimulus:** Infer the outer function parameter from use under a locally quantified type. **Expected oracle:** Reject escaping quantified variable; no fabricated polymorphic signature.

#### S03 — Call-site checking rebinds locally (negative)

**Setup:** Use CSTC combinator both/sequence or dip with different callable signatures. **Stimulus:** Call at separate sites; then create nested inferred closure capturing the first call’s type variable. **Expected oracle:** Call-site substitutions cannot escape or contaminate other instantiations.

#### S04 — Row-polymorphic anonymous vars (negative)

**Setup:** Use fn => $.field end and a closure returning a value whose row contains locally inferred @n. **Stimulus:** Return closure from defining function and call with incompatible record layouts. **Expected oracle:** Only exported row requirements may survive; unknown local type names cannot escape.

#### S05 — Module boundary generalization (negative)

**Setup:** Export inferred generic functions through modules; import with aliased and selective imports. **Stimulus:** Specialize exported signature from two consuming modules using different generic arguments. **Expected oracle:** Either valid universally quantified interface or a compile diagnostic; no silent accidental monomorphization.

#### S06 — Rank vars and local static where (negative)

**Setup:** Define T+$n input and return rank using where, then produce a closure that captures $n. **Stimulus:** Invoke closure at incompatible ranks in a different scope. **Expected oracle:** Where-computed rank evidence is bound per call and never escapes as unbound $n.

### R07 — Polymorphic unions and runtime narrowing [P0; Primary]

**Counterexample source:** `src/polymorphic-union-refinement.md`. **Valiance references:** §2.1; §2.4; §2.5; §6.5; §10.1; §16.

**Failure hypothesis:** A check for “is a list” misclassifies a generic scalar T that itself happens to be a list, incorrectly narrowing T|T+ to T+. **Safety invariant:** If branches overlap after substitution, refinement must remain sound; shape-only observations cannot establish element type or rank relative to T.

#### P01 — T itself is a rank-one list (negative)

**Setup:** Define a polymorphic helper accepting T|T+; specialize T to Int+. **Stimulus:** Supply an Int+ value and take a list-shaped branch that purports to return T+. **Expected oracle:** Reject unsupported narrowing or preserve union; it must not return Int+ where Int+2 promised.

#### P02 — T nested list at higher rank (negative)

**Setup:** Instantiate same helper with T = Int+2 or String+. **Stimulus:** Check via match as + and as?; compare to actual structural rank. **Expected oracle:** No confusion between scalar T and outer T+ when both are list-shaped.

#### P03 — Rugged/minimum-rank overlap (negative)

**Setup:** Define union T|T\* or T|T\~ where supported. **Stimulus:** Instantiate T with ranked types and attempt static narrowing from a generic list predicate. **Expected oracle:** Reject or conservatively represent overlapping candidates; preserve rank lower bounds.

#### P04 — Nested options and overlapping union (negative)

**Setup:** Define T|T? and choose T = Int?; compare Some[None] and None. **Stimulus:** Attempt branch refinement and extraction. **Expected oracle:** Cannot conflate T and T? when both accept None-like values.

#### P05 — Strong reified proof succeeds (positive)

**Setup:** Use fully reified as?[Int+2] on values of an explicitly known union. **Stimulus:** Run with rank-2 and rank-1 candidates. **Expected oracle:** Correct candidate returns Some[Int+2], other None; no cast fault from valid typecase.

#### P06 — Higher-order dispatch uses one branch plan (negative)

**Setup:** Place overlapping generic union branches in overloaded function input. **Stimulus:** Apply map; exploit ordering of Number|Number+ under T = Number+. **Expected oracle:** Reject inconsistent branch-to-overload plans or dispatch with a provably stable plan.

#### P07 — Refinement path independence (metamorphic)

**Setup:** Construct one value using literal list, function return, and imported call. **Stimulus:** Pass through as?, match, and a generic identity before narrowing. **Expected oracle:** Refinement result depends on evidence, not creation path or optimizer pass.

### R08 — Constrained generics and nearly-universal quantification [P1; Primary]

**Counterexample source:** `src/nearly-universal.md`. **Valiance references:** §12; §13; §16; §22; §23.

**Failure hypothesis:** A generic type instantiation violates a hidden bound after substitution, variance, where-evaluation or module abstraction. **Safety invariant:** Generic quantification ranges only over well-formed instantiations; all implicit bounds remain visible and enforceable through substitutions.

#### B01 — Named trait bound accepted/rejected (positive-negative)

**Setup:** Use a generic element constrained by named Addable[T] or anonymous trait structural requirement. **Stimulus:** Instantiate once with satisfying type and once with missing required overload. **Expected oracle:** Accept valid instantiation; reject missing trait evidence before execution.

#### B02 — Generic object bound syntax confirmation (design-confirmation)

**Setup:** Object §12 permits T: U / T: any U / T: above U; §16 says [] lists names only. **Stimulus:** Try supported bound syntaxes and invalid upper/lower pair; reconcile language grammar first. **Expected oracle:** Compiler and published grammar must agree; invalid bounds never instantiate.

#### B03 — Constraint survives covariance (negative)

**Setup:** Make a constrained generic object and another type with a strict subtype/supertype relationship. **Stimulus:** Upcast through inferred variance then pass a value that violates original constraint. **Expected oracle:** Reject widening that loses required bound.

#### B04 — Where overload gate is compile time (negative)

**Setup:** Declare overloaded define with where condition ? depending on ranks/types. **Stimulus:** Attempt values violating a where condition; test next eligible overload as fallback. **Expected oracle:** Reject candidate at compile time, never defer invalid assumption to runtime.

#### B05 — Imported constrained signature remains constrained (negative)

**Setup:** Define generic constrained element in module A and import/re-export from B. **Stimulus:** Call with invalid generic type through two levels of forwarding. **Expected oracle:** Implicit or explicit type constraints survive exports, imports and aliases.

#### B06 — No unrestricted quantifier from local evidence (negative)

**Setup:** Build polymorphic function whose body requires a trait implementation available only for one concrete type. **Stimulus:** Call using another unrelated type. **Expected oracle:** Infer restricted overload or reject generalization; never promise “all T” without proof.

### C09 — Self-relative types and trait reuse [P1; Conditional]

**Counterexample source:** `src/selfishness.md`. **Valiance references:** §12.2.1; §12.4; §13; §20.

**Failure hypothesis:** Self-relative parameter or result gets replaced with the wrong implementing object type during trait default reuse or dispatch. **Safety invariant:** Every `$self` type is specialized to its actual implementing object; trait/default reuse cannot claim to return a different object subtype.

#### SELF01 — Self-return correct owner (positive)

**Setup:** Trait requires extend resize(factor: Int) -> $self; implement for two unrelated objects. **Stimulus:** Call through direct and trait-typed paths. **Expected oracle:** Each result has the concrete owner type, never the other implementation.

#### SELF02 — Binary self-parameter safety (negative)

**Setup:** Define trait requirement accepting another $self value and implement in two object types. **Stimulus:** Try a call between two unlike implementations after widening both to the trait. **Expected oracle:** Reject cross-type call or use conservative trait-safe interface; cannot treat different Self as equal.

#### SELF03 — Trait-to-trait default specialization (negative)

**Setup:** Provide default trait implementation constructing/returning a concrete object; reuse trait in other type. **Stimulus:** Try to satisfy a return-$self requirement via reused default. **Expected oracle:** Reject when default does not return the actual implementer.

#### SELF04 — Multimethod self binding (metamorphic)

**Setup:** Combine self-relative return requirement and valid multimethod fallback. **Stimulus:** Compare static direct call with trait-upcast runtime call. **Expected oracle:** Runtime choice never changes promised concrete return or arity.

### C10 — Avoidance of out-of-scope inferred types [P2; Conditional]

**Counterexample source:** `src/avoidance.md`. **Valiance references:** §6.3; §16.3–16.4; §23.

**Failure hypothesis:** A locally defined nominal/structural type appears in an exported inferred type after its defining scope ends. **Safety invariant:** Inferred interfaces either expose a valid public type, retain an explicit existential if implemented, or reject without widening beyond justified constraints.

#### A01 — Local inferred type does not leak (design-confirmation)

**Setup:** Define local type or hidden module-local object if parser supports local type declarations. **Stimulus:** Return value/function whose inferred public signature mentions the hidden local identity. **Expected oracle:** Reject or package a sound representation; no unbound type in generated interface.

#### A02 — Anonymous row type exports (negative)

**Setup:** Infer function via fn => $.x end inside a module; export through alias. **Stimulus:** Import and call on records with and without x. **Expected oracle:** Exported structural constraint remains precisely required; no unconstrained widening.

#### A03 — Equivalent aliases remain equivalent (metamorphic)

**Setup:** Form semantically equal type aliases/interfaces through different intermediate local definitions. **Stimulus:** Export both after local names disappear. **Expected oracle:** Two paths cannot diverge into incompatible public type guarantees.

#### A04 — Scope error remains diagnostic (negative)

**Setup:** Force inferred generic result to mention locally scoped @n or rank variable. **Stimulus:** Compile cross-module and check error output. **Expected oracle:** Clear compile error or valid abstraction; no compiler crash / corrupted signature.

### C11 — Mutually recursive bounds and constraint cycles [P2; Conditional]

**Counterexample source:** `src/overdetermined-recursion.md`. **Valiance references:** §12; §13; §16; §19.1; §22; §23.

**Failure hypothesis:** A mutually recursive set of bounds is accepted by circularly using each bound as proof of itself. **Safety invariant:** A finite derivation of satisfiable constraints is required; contradictions never become valid through recursion or split modules.

#### REC01 — Direct contradictory bounds (design-confirmation)

**Setup:** If supported, declare a type variable with incompatible upper and lower bounds. **Stimulus:** Attempt instantiation. **Expected oracle:** Reject rather than invent an impossible type.

#### REC02 — Mutually recursive generic constraints (design-confirmation)

**Setup:** Spread a contradiction across two recursively referred trait/object constraints. **Stimulus:** Compile the mutually dependent declarations. **Expected oracle:** Reject or diagnose unsupported recursion; no cycle counts as evidence.

#### REC03 — Cross-module recursive constraints (design-confirmation)

**Setup:** Place halves of the same constraint cycle in separate modules. **Stimulus:** Build modules independently then together. **Expected oracle:** Link/import resolution must detect combined contradiction.

#### REC04 — Recursive element vs type recursion (positive-negative)

**Setup:** Use @recursive for ordinary terminating recursion plus an impossible recursive type bound. **Stimulus:** Compile/run ordinary recursive function and attempt invalid type bound. **Expected oracle:** Value recursion works; it does not license recursive type equality or impossible bound.

### C12 — Recursive type equations and distinctness [P2; Conditional]

**Counterexample source:** `src/distinctness-recursion.md`. **Valiance references:** §2; §10.1; §12–16; §23.

**Failure hypothesis:** A distinctness check assumes the occurs-check excludes recursive types even where module cycles or aliases admit an equation T = F[T]. **Safety invariant:** Recursive type definitions, if allowed, obey one consistent interpretation of equality and disjointness across compilation units.

#### DR01 — Occurs-check rejects impossible equation (negative)

**Setup:** Attempt to infer T equal to Function[T -> T] or T+ in a position requiring unification. **Stimulus:** Compile with no explicit recursive-type facility. **Expected oracle:** Reject occurs-check violation; never use it to prove an unsafe cast.

#### DR02 — Recursive alias support inventory (design-confirmation)

**Setup:** Check whether recursive aliases or recursive modules can define T = T+. **Stimulus:** Attempt syntax only if feature is supported. **Expected oracle:** Unsupported grammar rejected; supported grammar must document equality model.

#### DR03 — Module-split recursive constraints (design-confirmation)

**Setup:** Put two mutually referencing type declarations in separately imported modules. **Stimulus:** Compile separately and in a project. **Expected oracle:** No acceptance of a recursive equation that contradicts previously assumed distinctness.

#### DR04 — Exhaustiveness under abstract recursion (negative)

**Setup:** Use a generic variant or nominal union parameterized by a recursively constrained type. **Stimulus:** Omit a branch justified only by treating T and F[T] as distinct. **Expected oracle:** Reject omission unless inequivalence has a sound proof.

## Cross-cutting compatibility and retired-feature sentinels

**X01 — Value reconstruction preserves alias independence [positive].** Setup: Make two variables refer to the same immutable object value. Action: Write a field via one variable and read through the other. Oracle: Second alias retains original value; reconstruction preserves the original value (§12.3).

**X02 — Constructor cannot escape incomplete self [negative].** Setup: Define required fields with no default; create a constructor attempting closure capture/return/store `$self` before initialization. Action: Compile each prohibited escape route. Oracle: Compile error (§12.1).

**X03 — Channel[T] is invariant [negative].** Setup: Create Channel[Int]; attempt use as Channel[Number]. Action: Send a non-Int Number through a widened handle and receive as Int. Oracle: Compiler rejects widening (§25.8).

**X04 — Polymorphism cannot retype one channel identity [negative].** Setup: Capture one Channel[T] in a closure; attempt generic calls at incompatible T. Action: Send Int then receive as String through the same runtime channel. Oracle: Reject incompatible instantiation; no implicit value-restriction hole (§25.6–25.9).

**X05 — Task handle typed outputs remain stable [negative].** Setup: Capture Task[Int] and present it as Task[String] through inferred polymorphism. Action: Repeated wait on different static signatures. Oracle: Cannot retype stored task result; observed output always agrees with task signature (§25.3).

**X06 — Unsafe FFI never laundering into safe path [negative].** Setup: Create a linked native callback/handle operation, then wrap in generic or closure. Action: Call wrapper from module and generic interface. Oracle: `Unsafe` propagates transitively; no wrapper removes it (§27.3).

**X07 — Native-owner reconstruction guarded [negative].** Setup: Define owning pointer object with ordinary writable field and destructor. Action: Attempt field reconstruction of alias carrying owned pointer. Oracle: Must be rejected or handled by explicit ownership-safe design; spec §27.14 forbids any ordinary writable field on owner.

**X08 — Serialized bytecode preserves type witnesses [metamorphic].** Setup: Compile supported reified union/tag/multimethod examples to bytecode. Action: Run inline/interpreter and compiled/reloaded outputs. Oracle: Same accept/reject and runtime outputs; no evidence erased.

**X09 — Malformed generic test is a compile diagnostic [negative].** Setup: Use intentionally contradictory rank, generic and variant constraints. Action: Compile in isolation and as module dependency. Oracle: Compiler reports controlled diagnostic; no stack overflow, internal error, or hang.

## Worked Valiance-style fixture starters

Exact syntax is lifted from, or adapted closely to, the uploaded specification where possible. SCHEMATIC blocks explicitly are not executable. Examples were not run against a compiler. In negative cases, a specific _type_ error is the pass oracle, not any parse error.

### E01 / U01 — universal identity, valid

Mode: **positive**

```valiance
define[T] identity(value: T novec) -> T => $value end

identity(42)
identity("hello")
```

**Expected:** Both calls compile; the two instantiations are independent. Based on §16 and §7.2.

### E02 / U04 — exact rank rejects mismatch

Mode: **compile-reject**

```valiance
define[T] rankFour(values: T+4 exact) -> T+4 => $values end

rankFour([1, 2, 3])
```

**Expected:** Compile error: rank 1 is not rank 4. Based on §16.1.

### E03 / O01 — specificity

Mode: **positive**

```valiance
define f(:Number) -> String => "number" end
define f(:Number|String) -> String => "union" end

f(3)
f("text")
```

**Expected:** First selects Number overload; second selects union overload. Check known module overload override rules.

### E04 / O08 — arity mismatch

Mode: **compile-reject**

```valiance
define o(:Int) -> Int => 1 end
define o(:Int, :Int) -> Int => 2 end
```

**Expected:** Compile error: overloads of one element must have identical arity and multiplicity (§11.3).

### E05 / RT03 — rank check

Mode: **positive**

```valiance
$value = [1, 2, 3]
$value as?[Int+2]
```

**Expected:** Evaluates to None rather than Some[Int+2] (§2.4 and §2.1); any assertion on result must use exact optional semantics.

### E06 / O04 — union-covered map

Mode: **positive**

```valiance
$values = [1, 2, "A", "B"]
println($values map: * 2)
```

**Expected:** Expected values [2, 4, "AA", "BB"] (§6.5).

### E07 / X03 — Channel invariance

Mode: **compile-reject**

```valiance
$ints = Channel[Int]
$asNumbers: Channel[Number] = $ints
```

**Expected:** Compile error: Channel[T] is invariant (§25.8).

### E08 / D04 — disjoint tags

Mode: **compile-reject**

```valiance
tag #A as computed
tag #B as computed
tag #A disjoint #B

define impossible(:#A #B Int) => 1 end
```

**Expected:** Compile error: contradictory present tags (§17.6).

### E09 / SELF01 — receiver-dependent result

Mode: **positive**

```valiance
trait Resizable =>
  extend resize(factor: Int) -> $self
end

object Panel =>
  $size: Int
end
object Panel as Resizable =>
  define resize(factor: Int) =>
    $self.size := * $factor
    $self
  end
end
```

**Expected:** Fixture must be adapted to final trait-implementation grammar; result type must specialize to Panel (§12.4, §13).

### E10 / X01 — observable value reconstruction

Mode: **positive**

```valiance
object Counter =>
  public $count: Int = 0
end

$a = Counter
$b = $a
$a.count = 5
$b.count
```

**Expected:** Reads 0 through $b, assuming default constructor and field-write syntax (§12.3).

### E11 / P01 — type-shape overlap; SCHEMATIC

Mode: **design-confirmation**

```valiance
# Schematic typing obligation (NOT Valiance source):
# f[T]: (T | T+) -> T+
# T := Int+
# x := [1, 2] : T
# "x is a list" does NOT prove x : T+ (Int+2).
```

**Expected:** Compiler must not refine from generic list-ness alone; adapt this obligation into match/as? fixtures.

### E12 / B04 — type-level overload gating; SCHEMATIC

Mode: **design-confirmation**

```valiance
# Schematic typing obligation (NOT Valiance source):
# Candidate requires rank(x) == 2 via `where (... ?)`
# Call with a rank-1 list.
# Candidate must be removed before runtime.
```

**Expected:** Adapt to precise where syntax and rank variable rules in §22.

## Reproduction and automation procedure

**Folder layout.** tests/soundness/{R01_variance,...,C12_recursion,X_crosscut}/ with fixtures/\*.vlnc, expectation.yaml per case, fixtures_multi_module/ for import tests.

**Compilation command.** The language spec documents `vln compile --file PATH` and `vln run --file PATH`. Build scripts should use actual installed CLI options; do not assume a `vln check` command.

**Negative expectations.** For compile-reject, assert nonzero compile status and a relevant diagnostic category; a compiler panic, timeout, or unrelated parse error is NOT a passing soundness test.

**Positive expectations.** Compile successfully, run within resource limit, compare values/type evidence/output deterministically, and fail on unexpected runtime fault.

**Metamorphic expectations.** Generate semantically equivalent variants (e.g., code organization, rank aliases, import orders, explicit vs inferred generics) and require agreement of acceptance and outputs.

**Cross-module expectations.** Run in both single-file and split-module forms. A contradiction hidden in separate modules must still be caught when modules are combined.

**Automation artifacts.** For each case store `case_id`, fixture hash, compiler commit, status (pass/fail/blocked/n-a), diagnostics, observed type, stdout/stderr, seed and minimised reproducer.

**Shrinking.** When fuzzing finds an accepted ill-typed program, shrink argument count, list rank, union members, generic bounds, modules and imports, preserving the exploit.

**Resource budgets.** Set test-specific time/memory budgets, especially for recursive type constraints and infinitely lazy lists; timeout is never treated as successful rejection.

## Change-driven re-test map

**Generics/unification/rank/novec/exact:** R02, R07, R08, R01, X04.

**Tags, optional types, as? and match:** R03, R05, R07, X08.

**Traits/objects and self methods:** R01, C09, R04, X01, X02.

**Module/import or scope inference:** R06, C10, C11, C12, R04, R08.

**Multimethod/overload selection:** R04, R03, R06.

**Concurrency/channel/task handles:** X03, X04, X05, R02, R06.

**FFI/ownership/Unsafe propagation:** X06, X07, R03.

**Bytecode or optimization:** X08, R03, R04, R05, R07.

## Issue reporting template

Case ID; compiler commit; spec revision; source chapter; fixture; compile command; expected result; observed result; stdout/stderr; inferred type and overload trace; backend/options; minimal reproducer; security/soundness impact; fix PR; regression ID; verification status.

## 31-chapter disposition and traceability

- **Polymorphic references** — Mitigated / sentinel: X03–X05; channels and tasks retain runtime identities.

- **Covariant containers** — Mitigated / sentinel: V01–V03; immutable collection covariance and channel invariance.

- **Incomplete variance checking** — Primary: R01.

- **Objects under construction** — Mitigated / sentinel: X02; constructor escape restriction.

- **Curry’s paradox** — Out of scope: No type-level self-application/equality proof calculus specified.

- **Eventually, nothing** — Out of scope: No documented general bottom-type/totality promise.

- **Dubious evidence** — Out of scope: No first-class propositional equality proof elimination specified.

- **Some kinds of Anything** — Out of scope: No documented universal Any type; reassess if introduced.

- **Any (single) thing** — Primary: R02.

- **Mutable matching** — Mitigated / sentinel: X01; value reconstruction preserves the original value; re-audit channels if matching state.

- **Runtime type misinformation** — Primary: R03.

- **Overloading and polymorphism** — Primary: R04.

- **Distinctness I: Injectivity** — Primary: R05; preventive testing rather than directly transferable GADT exploit.

- **Distinctness II: Recursion** — Conditional: C12.

- **Distinctness III: Options** — Out of scope: No documented general type-equality proof/inhabitedness analysis.

- **Subtyping vs. inheritance** — Out of scope: Object class inheritance absent; trait reuse covered SELF tests.

- **Selfishness** — Conditional: C09.

- **Privacy violation** — Mitigated / sentinel: V04; cross-instance field access still receives preventive regression.

- **Unstable type expressions** — Out of scope: No path-dependent mutable type member feature specified.

- **The avoidance problem** — Conditional: C10.

- **A little knowledge...** — Out of scope: No directly matching first-class abstract module mechanism documented.

- **Underdetermined recursion** — Out of scope: No general recursive type family feature documented.

- **Overdetermined recursion** — Conditional: C11.

- **Scope escape** — Primary: R06.

- **Under false pretenses** — Out of scope: No relevant type-level proof mechanism specified.

- **Suspicious subterms** — Out of scope: Totality / dependent-type coverage not a declared guarantee.

- **There’s only one Leibniz** — Out of scope: Equality proof principles not part of documented language.

- **Intersecting references** — Mitigated / sentinel: X03–X07; channels are shared identities and unsafe native handles remain.

- **Polymorphic union refinement** — Primary: R07.

- **Positivity, strict and otherwise** — Out of scope: No inductive dependent proof system specified.

- **Nearly-universal quantification** — Primary: R08.

## Assumptions and unresolved specification decisions

1. The specification refers to object generic bounds in §12 while §16 says generic lists contain only names; confirm which grammar is implemented before B02/REC-family tests.

2. Generic `T` may match collection ranks under the stated solve/combine rules; precisely specify when union normalization, rank subsumption, `atomic`, `exact`, and `novec` are permitted to affect a single substitution.

3. Runtime representation is described as reified (§16), but the exact algorithm for generic union branch overlap, arbitrary tags, and minimum/rugged ranks is not fully formalized; record intended witness/evidence semantics.

4. Recursive nominal values can exist without recursive type equality; whether recursive type aliases or type-level functions exist determines C11/C12 applicability. Unsupported features receive N/A after grammar verification.

5. Native FFI can violate memory/type guarantees by design: failures there are labeled Unsafe-boundary defects, not evidence that purely safe Valiance is unsound. Channel identities are shared and type invariant, so generic-handle laundering remains an active sentinel.

6. Several starter snippets intentionally exercise boundary syntax; if rejected by parser for an unrelated reason, repair syntax before assigning an oracle outcome. Do not claim coverage from a parse error.

## Primary sources

A. User-provided `language.md`, sections referenced in each risk pack
B. User-provided `counterexamples-main.zip`, project `Counterexamples in Type Systems`, `src/SUMMARY.md` and the named chapter `.md` files (31 included chapters; `lawvere.md` present in archive but commented out in SUMMARY).

All test outcomes in this document are **expected**, not observed.
