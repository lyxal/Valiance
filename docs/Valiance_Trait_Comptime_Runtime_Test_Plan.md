# Valiance — Trait System Hardening Test Specification

**Status:** Proposed regression-test specification, derived from the supplied `language(2).md` (6,232 lines).  
**Purpose:** Harden *existing* trait support across static analysis / compile-time evaluation and runtime execution.  
**Notation:** The `valiance` snippets below are test blueprints based on the design document. They have **not** been compiled or executed against the current compiler. Check exact parser details and diagnostic wording against your implementation before checking them into CI.  
**Coverage claim:** High-coverage test inventory, not a mathematically exhaustive list of all possible programs.

## Design interpretation — important

Valiance is **not** assumed to have a Zig-style unrestricted `comptime` instruction. The design gives `where` clauses a deliberately restricted compile-time evaluator (§22), and otherwise relies on static overload/trait resolution (§4.2, §13) plus runtime execution. Runtime-specialized multiple dispatch is an **opt-in** facility via `multi define` (§20), and overloaded function values can carry a **precomputed union-branch dispatch plan** (§6.5). Trait relationships are **import-controlled behaviour sets** (§23.8–23.14 and “Trait-to-trait behaviour sets,” near line 5,988). These differences drive the cases below.

The plan deliberately avoids importing Rust-specific assumptions (associated types, coherence/orphan rules, vtables, trait-object ownership, or unrestricted const trait calls) that the document does not establish. Generic trait *parameters*, `$self` return types, anonymous structural traits, and trait-to-trait implication **are** established.

### Legend / test oracle

- **C+** — program compiles; verify inferred types, selected overload/impl, warnings and generated code where accessible.
- **C−** — program must fail compilation, produce an actionable diagnostic, and **not crash or hang** the compiler.
- **R** — run the compiled program; assert exact values, stack shape, number of effects, output order or dispatch identity.
- **F** — runtime fault is the expected outcome; assert fault kind and cleanup behavior.
- **P** — metamorphic/property test; compare multiple semantically equivalent source variants.
- **P0** — highest-priority trait correctness / soundness; **P1** — high-value interaction; **P2** — extended stress/integration.
- **[Decision]** means the document does not completely pin down a disputed semantic edge; first record the desired rule, then turn the case into an assertion. Do **not** silently treat suggested behavior as established Valiance semantics.

### Suggested test fixture metadata

Each fixture should declare: `id`, `sources` (single-file or a module tree), `expected_compile` (pass/fail), `expected_diagnostics` (code/category and primary span where available), `expected_type_or_resolution` (optional), `expected_stdout`, `expected_stack`, `expected_fault`, `timeout`, and `variants` (optimizer/mode/import/order). Do not assert whole diagnostic prose unless diagnostics are intentionally stable.

Use **separate source files** for compile-error cases. A valid compilation following an invalid compilation in the **same compiler process** is a distinct recovery test. Use helper functions for observable runtime inputs to avoid constant-folding all calls.

### Coverage summary

This specification includes **309 individually identified test cases** in **18 suites**: **155 P0**, **120 P1**, and **34 P2**. Its primary oracles cover **compile acceptance/rejection, runtime values or faults, and metamorphic consistency**. A single row may require several fixture variants: treat those variants as separate physical tests once automated.

| Suite | IDs | Cases | Focus |
|---|---|---:|---|
| TD | TD-001–017 | 17 | Declaration and implementation validity |
| DF | DF-001–017 | 17 | Defaults, overrides, receiver-relative types |
| IH | IH-001–014 | 14 | Intrinsic inheritance |
| BL | BL-001–021 | 21 | Blanket trait-to-trait behaviour sets |
| AN | AN-001–015 | 15 | Anonymous structural traits |
| GN | GN-001–017 | 17 | Generic arguments and substitution |
| OV | OV-001–022 | 22 | Trait-aware overload specificity |
| TY | TY-001–017 | 17 | Unions, intersections and ranks |
| UC | UC-001–015 | 15 | Reified union-branch function plans |
| MM | MM-001–020 | 20 | Runtime multimethod dispatch |
| IM | IM-001–025 | 25 | Behaviour imports and qualifications |
| WH | WH-001–018 | 18 | Compile-time `where` evaluation |
| CA | CA-001–014 | 14 | Casts and pattern matches |
| VS | VS-001–016 | 16 | Vectorization and higher-order calls |
| LC | LC-001–017 | 17 | Lifetime and object state |
| TG | TG-001–014 | 14 | Tags, effects and indexing |
| CC | CC-001–010 | 10 | Concurrency and trait values |
| IN | IN-001–020 | 20 | Incremental builds, caches and recovery |

### First-pass smoke suite (run before the full matrix)

Start with **TD-002/003**, **DF-001/002/003**, **BL-002/005/007**, **AN-001/002**, **GN-001/005**, **OV-001/008/011**, **UC-001/004/005**, **MM-002/003/004/006**, **IM-004/005/009**, **WH-002/010/016**, and **IN-004/006/008**. These are high-signal regression tests for the most likely trait correctness failures in this design, including wrong results that could otherwise compile cleanly.

### Minimal execution matrix

| Dimension | Values | Why |
|---|---|---|
| Compilation | clean, incremental, repeated in same process | cache, invalidation, recovery |
| Optimization | minimum, maximum (all available backends) | incorrect devirtualization / specialization |
| Invocation | stack call, element-call syntax, `:` when callable | parser/argument-lowering differences |
| Types | concrete, trait-typed, union, intersection, list, generic | stress distinct matching paths |
| Visibility | local, imported, qualified, re-exported, block-scoped | behaviour-set evidence |
| Dispatch | ordinary static, `multi`, union-covered function value | distinct runtime plans |
| Static evaluator | valid `where`, rejected `where`, overload fallback | comptime execution and solver rollback |
| Persistence | fresh compile, serialized bytecode if supported, restored bytecode | metadata preservation |

### Source map

- Trait syntax / requirements / inheritance: **§13** (around lines 2,328–2,401).
- Anonymous traits: **§13.1** (2,402–2,441); generic unification/variance: **§16** (2,562–2,693).
- Overload specificity/disambiguation: **§4.2** (617–672); function value union dispatch: **§6.5** (851–874).
- Objects and `$self`: **§12** (1,904–2,328); runtime multimethods: **§20** (3,328–3,398).
- Static `where` execution: **§22** (3,558–3,643); imports and conflicts: **§23.8–23.15** (4,050–4,332).
- Trait-to-trait blanket behaviour sets: addendum near **lines 5,988–6,039**.
- Traits in containers/casts/matches: **§2**, **§7**, **§10**, **§14**, **§17**, **§25**.

## TD. Trait declarations and implementation well-formedness

**Design basis:** §13; §11; §12  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **TD-001** | P0 | C+ | Empty trait and empty implementing object | Base object plus empty object-as-trait block type-check; value satisfies trait. |

| **TD-002** | P0 | C+ | One required nilad satisfied | Required `extend name -> T` matched by implementing object-friendly `define name` with exact return. |

| **TD-003** | P0 | C− | Missing required element | Impl rejected; diagnostic identifies missing requirement and implementing type. |

| **TD-004** | P0 | C− | Mismatched required return type | Wrong output type does not satisfy requirement even if element name matches. |

| **TD-005** | P0 | C− | Mismatched required arity | Required overload is not discharged by wrong parameter count. |

| **TD-006** | P0 | C− | Mismatched required multiplicity | One output cannot implement a requirement returning two outputs, or reverse. |

| **TD-007** | P0 | C− | Missing prerequisite object base definition | `object X as Trait` before/without required object declaration rejected per supported declaration rules. |

| **TD-008** | P1 | C− | Trait implementation defines a constructor | Rejected; constructor belongs to base object definition. |

| **TD-009** | P1 | C+R | Trait implementation accesses private and readable fields | Same access privileges as object-friendly elements; output correct. |

| **TD-010** | P1 | C− | External ordinary element accesses private field | No accidental privilege inheritance from nearby trait implementation. |

| **TD-011** | P1 | C+R | Required element satisfied by matching existing implementation | Proper element identity, receiver and callable signature are used. |

| **TD-012** | P1 | C+ | Multiple required elements with different names | Complete impl accepted; all requirements checked independently. |

| **TD-013** | P1 | C− | One of many requirements missing | Fails specifically on unsatisfied requirement, no partial impl leakage. |

| **TD-014** | P1 | C+ | Trait declared with generic parameter names only | `trait[T]` accepted with constraints at typed-use sites. |

| **TD-015** | P1 | C− | Illegal bounded generic in trait declaration list | `trait[T: Trait]` rejected rather than silently misparsed. |

| **TD-016** | P1 | C− | Malformed nested trait/impl declarations | Recover without symbol-table corruption or compiler crash. |

| **TD-017** | P2 | P | Rename trait consistently across files | All results unchanged, proving semantic identity not hard-coded to spelling. |



## DF. Required methods, defaults, overrides, and receivers

**Design basis:** §13; §12.2; §12.2.1; §12.4; §19.2  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **DF-001** | P0 | C+R | Default method inherited without override | Trait default is callable on implementing object, correct result. |

| **DF-002** | P0 | C+R | Explicit override of default method | Concrete impl wins, including through a parameter typed as the trait. |

| **DF-003** | P0 | C+R | Default method invokes a required trait method | Call uses correct satisfying implementation for the receiver. |

| **DF-004** | P0 | C+R | Default method calls another default method | Chained defaults preserve receiver, stack effects and result. |

| **DF-005** | P0 | C+R | Default method invokes overridden sibling method | Override observed, not frozen default behavior. |

| **DF-006** | P0 | C− | Bad override signature | Does not accidentally satisfy distinct trait requirement. |

| **DF-007** | P0 | C+R | `$self` return specializes to implementing object | Requirement `extend resize(factor: Int) -> $self` returns concrete implementing type. |

| **DF-008** | P1 | C+R | Two implementers with different shapes share default | Default accesses only valid trait requirements; each gets its own implementation. |

| **DF-009** | P1 | C+R | Default uses explicit function arguments | Parameters and implicit receiver are not swapped. |

| **DF-010** | P1 | C+R | Trait element writes receiver field in implementing block | Correct returned immutable reconstruction, no change to original copy. |

| **DF-011** | P1 | C+R | Explicit receiver at first parameter position | No extra implicit receiver added; correct access and signature. |

| **DF-012** | P1 | C+R | Explicit receiver at second parameter position | Declared order preserved in stack/ECS call and return. |

| **DF-013** | P1 | C− | Two exact `$self` parameters | Rejected by explicit receiver restriction. |

| **DF-014** | P1 | C+R | `@self` on trait-related object-friendly element | Returns the updated receiver object and preserves concrete type. |

| **DF-015** | P1 | C+R | Nested `$self` return type | `$self` in a nested output type is receiver-relative but not a second receiver. |

| **DF-016** | P2 | P | Direct call versus receiver-qualified call | Same implementation and return where both forms are legal. |

| **DF-017** | P2 | C+R | Default method executes from a generic wrapper | Correct generic substitution and stack shape survive lowering. |



## IH. Trait inheritance and intrinsic requirements

**Design basis:** §13; supplement near lines 5,988–6,039  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **IH-001** | P0 | C+R | Derived trait adds a method | Implementing derived trait must satisfy both derived and parent requirements. |

| **IH-002** | P0 | C+R | Parent trait implementation plus derived empty block | Parent requirements reused when object also declares parent impl. |

| **IH-003** | P0 | C− | Derived impl missing inherited requirement everywhere | Compilation fails; parent requirement is not silently considered implemented. |

| **IH-004** | P0 | C+R | Inherited default reused automatically | No duplicate default body required in child impl. |

| **IH-005** | P0 | C+R | Parent default calls child-supplied required method | Correct concrete call and return. |

| **IH-006** | P0 | C+R | Three-level intrinsic inheritance | All ancestor obligations propagated transitively. |

| **IH-007** | P1 | C+R | Parent and child declared in different source files | Imports retain hierarchy and required elements. |

| **IH-008** | P1 | C+R | Child has generic parent with correlated parameters | Instantiation of child binds inherited requirement correctly. |

| **IH-009** | P1 | C− | Derived implementation supplies wrong inherited signature | Wrong overload does not discharge parent requirement. |

| **IH-010** | P1 | C+R | Independent sibling traits with shared ancestor | Common parent evidence handled once; calls unambiguous when provider is unique. |

| **IH-011** | P1 | C+R | Child instance used as parent-typed argument | Trait subtype accepted and parent call works. |

| **IH-012** | P1 | C+R | Trait inheritance plus polymorphic return `$self` | Concrete type preserved through inherited requirement. |

| **IH-013** | P2 | C− | Invalid inheritance cycle or ungrounded recursive requirements | No nontermination; reject or diagnose according to supported cycle policy [Decision if self-inheritance syntax is legal]. |

| **IH-014** | P2 | P | Reorder unrelated intrinsic trait definitions | Same resolution, diagnostics and output. |



## BL. Trait-to-trait blanket behaviour sets

**Design basis:** Trait-to-trait behaviour sets addendum near lines 5,988–6,039  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **BL-001** | P0 | C+R | A implements B through trait A-as-B relationship | Object satisfying A can be used as B without direct object-as-B block. |

| **BL-002** | P0 | C+R | Generic argument correlation | A[Int] implies B[Int], never B[String]. |

| **BL-003** | P0 | C− | Wrong target generic argument | Evidence for A[Int] does not discharge B[String]. |

| **BL-004** | P0 | C+R | Transitive chain A implies B implies C | A object usable as C when chain visible and applicable. |

| **BL-005** | P0 | C− | Pure cycle without concrete evidence | A-as-B and B-as-A does not invent A or B evidence for unrelated object. |

| **BL-006** | P0 | C+R | Cycle with a concrete base fact | Solver terminates and uses established evidence correctly. |

| **BL-007** | P0 | C− | Two independent applicable blanket providers for same target | Use ambiguous unless explicitly qualified. |

| **BL-008** | P0 | C+R | Explicitly qualified blanket provider | Correct provider invoked; qualification does not wrap runtime value. |

| **BL-009** | P0 | C− | Constraint on blanket implementation binder fails | Inapplicable candidate ignored; if none remain, no evidence. |

| **BL-010** | P0 | C+R | One blanket candidate fails constraints, one succeeds | Successful provider used without false ambiguity. |

| **BL-011** | P0 | C− | Two paths to the same target with different providers | No automatic shortest-path or owner-preference ranking. |

| **BL-012** | P1 | C+R | Import source trait auto-brings owned behaviour set | Imported subject trait exposes its module-owned outgoing relationships. |

| **BL-013** | P1 | C− | Import target trait alone | Does not automatically import outgoing behaviour sets from other modules. |

| **BL-014** | P1 | C+R | Explicit import of trait behaviour set | Relationship available without importing all other module exports. |

| **BL-015** | P1 | C− | Block-scoped imported blanket relationship used outside block | Evidence must no longer be visible outside scope. |

| **BL-016** | P1 | C+R | Public re-export of blanket provider | Source-facing qualifier uses re-export module, internal identity stable. |

| **BL-017** | P1 | C+R | Diamond implication with same single provider | Repeated paths to identical provider do not manufacture distinct providers. |

| **BL-018** | P1 | C− | Diamond with genuinely different providers | Ambiguity diagnosed and not decided by traversal order. |

| **BL-019** | P2 | P | Increase transitive path length with irrelevant traits | Same selected target provider. |

| **BL-020** | P2 | P | Reverse provider declaration and import order | Same ambiguity or unique resolution. |

| **BL-021** | P2 | P | Random cyclic graph, seeded concrete facts | Solver terminates; proof only when graph contains an applicable evidence path. |



## AN. Anonymous structural traits

**Design basis:** §13.1; §16; §4.2  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **AN-001** | P0 | C+R | Inline trait with one required overload | Concrete type accepted solely from visible structural behavior, no nominal impl. |

| **AN-002** | P0 | C− | Missing structural overload | Reject even if unrelated named trait has same human-readable name. |

| **AN-003** | P0 | C+R | Inline trait with two required overloads | Both requirements enforced; body may call either. |

| **AN-004** | P0 | C− | One of multiple structural requirements missing | Reject; no partial satisfaction. |

| **AN-005** | P0 | C+R | Structural trait generic argument binds correctly | `trait[T]` requirement matches actual argument and result types. |

| **AN-006** | P0 | C− | Structural overload has wrong return type | Name and input match are insufficient. |

| **AN-007** | P0 | C− | Structural overload has wrong arity | Correct name but wrong signature does not satisfy. |

| **AN-008** | P0 | C+R | Body calls required element inside structural constraint | Call type-checks using constraint evidence and executes correct visible overload. |

| **AN-009** | P1 | C+R | Named trait and anonymous trait for same behavior | Both accepted in their respective nominal/structural conditions. |

| **AN-010** | P1 | C− | Anonymous trait used as direct `object X as trait =>` target | Rejected because anonymous traits cannot be implemented directly. |

| **AN-011** | P1 | C+R | Structural equality requirement in generic `find` | Matches a type with visible `===` and returns correct search result. |

| **AN-012** | P1 | C+R | Imported overload satisfies structural requirement | Visibility of imported overload suffices, without nominal trait membership. |

| **AN-013** | P1 | C− | Required overload hidden in module / scope | Not satisfied after imported behavior disappears. |

| **AN-014** | P2 | P | Rename anonymous bound type parameters consistently | Same results; alpha-equivalent constraints. |

| **AN-015** | P2 | P | Reorder unrelated visible overloads | Structural satisfaction unchanged. |



## GN. Generic trait instances, variance and substitution

**Design basis:** §13; §16.1–16.4; §7.2  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **GN-001** | P0 | C+R | Same nominal trait with different generic arguments | Correct impl/generic argument selected; no collapse into bare trait identity. |

| **GN-002** | P0 | C− | Trait generic argument mismatch | A[T] is not accepted as A[U] just because trait name is equal. |

| **GN-003** | P0 | C+R | Nested generic trait argument | Binding traverses nested type constructor correctly. |

| **GN-004** | P0 | C+R | Trait-typed function nested in two generic functions | Constrained type evidence flows through both calls. |

| **GN-005** | P0 | C+R | Same generic wrapper instantiated A then B then A | No stale specialization or resolution cache reuse. |

| **GN-006** | P0 | C+R | Same wrapper invoked in reverse B then A order | Semantic result independent of specialization order. |

| **GN-007** | P0 | C− | Generic constraints disagree across parameters | No invalid inference unification or arbitrary selection. |

| **GN-008** | P0 | C+R | Generic object with trait-generic argument | Receiver type and trait type arguments independently substituted. |

| **GN-009** | P1 | C+R | Covariant generic trait interface in return-only position | Valid subtype conversions obey inferred variance where applicable. |

| **GN-010** | P1 | C− | Generic object with public writable field | Invariant position cannot be unsafely widened. |

| **GN-011** | P1 | C+R | Trait-constrained `novec` generic parameter | Binds entire argument collection type, not scalar leaf. |

| **GN-012** | P1 | C− | Trait constraint paired with `exact` incompatible shape | Candidate removed before unification, no vectorization fallback. |

| **GN-013** | P1 | C+R | Trait constraint plus `atomic` evidence | Consistent generic substitution and scalar validation. |

| **GN-014** | P1 | C− | Union/intersection arguments incorrectly unified positionally | Reject unpermitted cross-union or cross-intersection inference. |

| **GN-015** | P1 | C+R | Structurally constrained row type with trait-bearing member | Required field and its trait behavior both checked. |

| **GN-016** | P2 | P | Alpha-rename all generic type binders | Inferred type and outputs unchanged. |

| **GN-017** | P2 | P | Permute equivalent generic call instantiation order | Monomorphization cache sound. |



## OV. Overload resolution with trait evidence

**Design basis:** §4.2; §11; §13; §16.1; §7.2  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **OV-001** | P0 | C+R | Exact concrete overload vs trait overload | Exact concrete match wins by specificity. |

| **OV-002** | P0 | C+R | Generic-equivalent exact overload vs trait implementation | Generic-equivalent rule takes documented precedence. |

| **OV-003** | P0 | C+R | Optional-substitution overload vs trait match | Optional-substitution wins when applicable under specificity rule. |

| **OV-004** | P0 | C+R | Vectorizing overload vs trait match | Vectorizing match wins when comparable as specified. |

| **OV-005** | P0 | C+R | Intersection overload vs trait match | Intersection match precedes trait implementation match. |

| **OV-006** | P0 | C+R | Trait match vs rank or union match | Trait implementation match wins over lower-ranked categories. |

| **OV-007** | P0 | C+R | Tagged trait match vs untagged trait match | Tagged candidate wins when both otherwise comparable. |

| **OV-008** | P0 | C− | Two unrelated trait overloads both apply | Ambiguity diagnostic; no arbitrary winner. |

| **OV-009** | P0 | C+R | Explicit overload disambiguation | Type hint chooses specified applicable overload. |

| **OV-010** | P0 | C− | Explicit disambiguation to inapplicable trait overload | Error, not silent fallback. |

| **OV-011** | P0 | C− | Two candidates each more specific on different parameter | Neither dominates; ambiguity rather than lexicographic ranking. |

| **OV-012** | P0 | C+R | All argument positions strictly favor one overload | Dominant candidate selected, independent of definition order. |

| **OV-013** | P0 | C− | Tie in trait specificity | Identically ranked applicable candidates rejected. |

| **OV-014** | P0 | C+R | External element shadows object-friendly element | External element has documented precedence where applicable. |

| **OV-015** | P0 | C+R | Owner-qualified object-friendly call | Original callable remains accessible and correct. |

| **OV-016** | P1 | C+R | `novec` trait overload refuses list peeling | Directly compatible arguments accepted; disallowed vectorization rejected. |

| **OV-017** | P1 | C− | `novec` marker lost through first-class function value | Calls through a function value must enforce same policy. |

| **OV-018** | P1 | C+R | `exact` candidate filtering before generic unification | Only structurally exact candidate eligible. |

| **OV-019** | P1 | C+R | Trait disambiguation plus generic arguments | Generic arguments before `{...}` hints parsed and resolved correctly. |

| **OV-020** | P1 | C+R | Failed `where` candidate then lower-priority viable overload | Candidate skipped cleanly; constraints do not leak. |

| **OV-021** | P2 | P | Shuffle overload declaration order | Selected overload or ambiguity unchanged. |

| **OV-022** | P2 | P | Add an unrelated overload or impl | Existing resolution unchanged. |



## TY. Union, intersection, optional and rank-based trait typing

**Design basis:** §2; §4.2; §7; §10; §13; §16  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **TY-001** | P0 | C+R | Object coerced to implemented trait | Correct static trait type and runtime value preserved. |

| **TY-002** | P0 | C− | Object coerced to unimplemented trait | Static `as[Target]` fails; no unsound conversion. |

| **TY-003** | P0 | C+R | Value implements both traits in intersection | Intersection `A&B` accepted where both evidence visible. |

| **TY-004** | P0 | C− | Intersection with missing conjunct | Value implementing A but not B rejected for A&B. |

| **TY-005** | P0 | C+R | Union of two implementers used as trait | Both branches have required evidence, calls work. |

| **TY-006** | P0 | C− | Union contains a non-implementer | Cannot claim whole union implements trait without narrowing. |

| **TY-007** | P0 | C+R | Trait-typed list with heterogeneous implementers | Rank and trait element type retained; operations correct. |

| **TY-008** | P0 | C+R | Trait-constrained list rank one vs rank two | Type-check and vectorization obey rank rules. |

| **TY-009** | P0 | C− | Exact-rank trait list given inappropriate minimum-rank argument | Reject unless permitted vectorizing match applies. |

| **TY-010** | P1 | C+R | Optional trait value `Trait?` with Some branch | Safe narrowing preserves witness/implementation. |

| **TY-011** | P1 | C+R | Optional None branch checked with `as?` | No accidental fabricated trait evidence. |

| **TY-012** | P1 | C+R | Rugged list type containing trait implementers | Where permitted, vectorizing to atomic trait method works. |

| **TY-013** | P1 | C− | Rugged type used as uniform exact-rank trait list | Reject invalid uniform-prefix inference. |

| **TY-014** | P1 | C+R | Minimum-rank assurance `^+` on trait-bearing collection | Correct wrapped values and adjusted type rank. |

| **TY-015** | P1 | C+R | Anonymous-trait constraints over union branches | All branches prove structural requirements before use. |

| **TY-016** | P2 | P | Reorder union members and intersection conjuncts | Type normalization and trait evidence unchanged. |

| **TY-017** | P2 | P | Duplicate identical union branches | Normalization does not duplicate provider evidence. |



## UC. Union-covered overloaded function values

**Design basis:** §6.5 “Union-covered overloaded functions”; §4.2  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **UC-001** | P0 | C+R | Every union branch covered by one unambiguous overload | Function value creation succeeds and calls dispatch to preselected branch overloads. |

| **UC-002** | P0 | C− | One union branch lacks a matching overload | Function type conversion rejected at compile time. |

| **UC-003** | P0 | C− | One branch has two applicable undominated overloads | Reject ambiguous branch rather than arbitrarily choose. |

| **UC-004** | P0 | C− | Two runtime-overlapping branches select different overloads | Reject unsafe dispatch plan at compile time. |

| **UC-005** | P0 | C+R | Broad static union branch contains a narrower concrete subtype | Runtime must not re-resolve to narrower overload only from runtime concrete type. |

| **UC-006** | P0 | C+R | Trait predicate covers one branch | Generated runtime type predicate accepts trait membership as planned. |

| **UC-007** | P0 | C+R | Numeric-supertype trait/nominal branch | Reified predicate uses assignability semantics, not exact type name. |

| **UC-008** | P0 | C+R | Tagged branch with trait-constrained overload | Runtime tag evidence preserves branch selection. |

| **UC-009** | P1 | C+R | Union-covered callable used by `map` | Per-item branch dispatch correct and resulting list type matches selected outputs. |

| **UC-010** | P1 | C+R | Multiple different functions with equal union input signature | No dispatch-plan cache collision. |

| **UC-011** | P1 | C+R | Function value captured inside closure | Branch plan survives capture and repeated calls. |

| **UC-012** | P1 | C+R | Function value stored in collection/record | Branch plan survives storage and retrieval. |

| **UC-013** | P1 | C+R | Function value passed across module boundary | Serialized/reified plan remains correct. |

| **UC-014** | P1 | P | Permute union branch order | Same outputs for same values; no order-dependent overlap resolution. |

| **UC-015** | P2 | P | Rebuild bytecode, if bytecode persistence enabled | Saved plans equal fresh-compiled behavior. |



## MM. Runtime multiple dispatch (`multi define`)

**Design basis:** §20; §4.2; §13  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **MM-001** | P0 | C+R | Exact static subtype matches `multi` | Compile-time selection uses exact subtype overload. |

| **MM-002** | P0 | C+R | Trait-upcast argument calls `multi` | Runtime selects overload from exact runtime concrete type. |

| **MM-003** | P0 | C+R | Ordinary non-`multi` trait-upcast call | Stays statically dispatched; runtime concrete type does not override choice. |

| **MM-004** | P0 | C+R | Two-argument multi matrix A/A A/B B/A B/B | Every ordered pair selects expected specialization. |

| **MM-005** | P0 | C+R | Fallback used for unhandled runtime subtype | Broad non-multi fallback runs. |

| **MM-006** | P0 | C− | Multi declaration without valid non-multi fallback | Reject at compile time. |

| **MM-007** | P0 | C− | Fallback parameters fail to be supertypes | Reject invalid `multi` relationship. |

| **MM-008** | P0 | C− | Specialized return differs from fallback | Reject mismatched return types or multiplicity. |

| **MM-009** | P0 | C+R | Runtime types switch across repeated calls | No cached previous subtype or method-slot mix-up. |

| **MM-010** | P0 | C+R | Multi call in loop over trait-typed list | Per-iteration runtime types determine correct specialization. |

| **MM-011** | P0 | C+R | Multi call through generic wrapper | Specialization and fallback selection preserved across generic boundary. |

| **MM-012** | P0 | C+R | Multi call with an exact static concrete type | Dynamic path can be elided without changing result. |

| **MM-013** | P1 | C+R | Multi-specific overload has two trait constraints | Runtime predicate checks both argument positions. |

| **MM-014** | P1 | C+R | Multi result passed to trait-constrained element | Output static type equal fallback return, usable by caller. |

| **MM-015** | P1 | C+R | Multi parameter is union of implementer types | Runtime matches correct concrete type or fallback. |

| **MM-016** | P1 | C+R | Multimethod with zero/one/multiple applicable specializations | Respect the documented selection rules; never use declaration order. |

| **MM-017** | P1 | C+R | Multi and ordinary overload with same element name | Static first-stage resolution and runtime dispatch cooperate correctly. |

| **MM-018** | P1 | P | Permute list order of concrete runtime arguments | Dispatch follows values, not previous call history. |

| **MM-019** | P2 | P | Compile same dispatch matrix under all optimization levels | Identical results and side effect counts. |

| **MM-020** | P2 | C− | Overlapping equally applicable specialized multimethods | [Decision] Specify ambiguity handling before asserting exact error or fallback. |



## IM. Behaviour-set imports, qualification and module boundaries

**Design basis:** §23.1–23.15; blanket-impl addendum  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **IM-001** | P0 | C+R | Import object whose object and trait are module-owned | Owned trait impl arrives automatically with object. |

| **IM-002** | P0 | C− | Import foreign-object/foreign-trait symbol without behavior set | Missing foreign implementation is not silently in scope. |

| **IM-003** | P0 | C+R | Explicit `object X as Y` import | Foreign behavior becomes available in importing scope. |

| **IM-004** | P0 | C− | Two imported distinct providers for same object and trait | Unqualified use rejected as ambiguous. |

| **IM-005** | P0 | C+R | Qualified provider A then B | Explicit qualification selects respective implementations with distinguishable results. |

| **IM-006** | P0 | C+R | Qualified `as[module.Trait]` cast | Qualification is zero-cost and selects provider; value identity preserved. |

| **IM-007** | P0 | C+R | Qualified provider element call | Owner-qualified `first.Shape.area` and `second.Shape.area` differ as defined. |

| **IM-008** | P0 | C− | Import behavior set not declared by that module | Diagnostic instead of ghost candidate. |

| **IM-009** | P0 | C+R | Import single provider while other exists in package | No false ambiguity from non-imported behavior. |

| **IM-010** | P0 | C− | Imported provider scoped inside a block used outside | Provider not visible after block. |

| **IM-011** | P0 | C+R | Nested block imports override visibility context | Inner qualifier chosen, outer context restored afterward. |

| **IM-012** | P0 | C+R | Namespace import only | Does not merge behavior sets into unqualified current scope. |

| **IM-013** | P0 | C+R | Import same physical provider through re-export paths | Preserve provider identity, do not falsely count as two independent impls. |

| **IM-014** | P0 | C− | Re-export two genuinely distinct providers unqualified | Ambiguity preserved across re-export boundary. |

| **IM-015** | P1 | C+R | Public re-export of object and behavior set | Downstream access matches re-exported API. |

| **IM-016** | P1 | C+R | Imported behavior implementation name collides with unrelated trait | Qualifier selects semantic identity, not short name. |

| **IM-017** | P1 | C+R | Two modules declare traits with identical names | Distinct symbol IDs prevent cross-wired implementations. |

| **IM-018** | P1 | C+R | Two modules declare objects with identical names | Correct module-qualified receiver identity. |

| **IM-019** | P1 | C+R | Import bare trait plus explicit foreign behavior | Trait and provider both available; no duplicate import confusion. |

| **IM-020** | P1 | C− | Nonpublic trait or object imported | Visibility diagnostic; no evidence leak. |

| **IM-021** | P1 | C+R | Cyclic module imports of trait declarations | Symbols resolve without executing import-time code. |

| **IM-022** | P1 | P | Reverse module import order | Provider choice or ambiguity stays identical. |

| **IM-023** | P1 | P | Replace direct import with legal public re-export | Same runtime behavior and semantic provider ID. |

| **IM-024** | P2 | P | Compile modules separately and link/reuse cache | Type identity, qualified provider, and selected methods unchanged. |

| **IM-025** | P2 | P | Change only a provider definition and incremental rebuild | All consumers rebind/recompile; no stale implementation. |



## WH. Compile-time `where` evaluator interacting with traits

**Design basis:** §22; §4.2; §16  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **WH-001** | P0 | C+R | Trait-bearing overload with true `where` predicate | Candidate remains and runtime body executes correctly. |

| **WH-002** | P0 | C+R | First applicable overload has false `where` assertion | That candidate rejected; next viable overload selected. |

| **WH-003** | P0 | C− | All candidates fail `where` assertions | Clean no-overload diagnostic, not runtime assert failure. |

| **WH-004** | P0 | C+R | `where` reads function-valued parameter arity | Correct arity of trait-constrained callable used for later selection. |

| **WH-005** | P0 | C+R | `where` reads callable input and output type tuples | Types and multiplicity accurately reflect selected function signature. |

| **WH-006** | P0 | C+R | Rank variable from trait-bearing list | Exact input rank feeds computed return rank/hidden static value correctly. |

| **WH-007** | P0 | C− | Mandatory-write return rank never assigned in `where` | Compile-time diagnostic, no bogus default rank. |

| **WH-008** | P0 | C+R | Two overloads with same trait constraints, different static ranks | Rank-dependent predicate selects intended overload. |

| **WH-009** | P0 | C+R | `where` type equality on two trait-constrained types | Equal nominal identities compare equal; distinct ones do not. |

| **WH-010** | P0 | C− | Arbitrary trait method call inside `where` | Rejected as outside evaluator whitelist, even if method is side-effect-free. |

| **WH-011** | P0 | C− | Namespaced / disambiguated / modified call inside `where` | Rejected by static evaluator restriction. |

| **WH-012** | P0 | C− | Loops and recursion inside `where` | Rejected; no compiler hang. |

| **WH-013** | P0 | C− | `Result` static type literal inside `where` | Rejected; only supported type literals accepted. |

| **WH-014** | P1 | C− | Optional-safe member access inside `where` | Rejected by restricted evaluator. |

| **WH-015** | P1 | C+R | `where` computation chains several numeric operations | Left-to-right static evaluation yields correct hidden numeric output. |

| **WH-016** | P1 | C+R | False `where` predicate with generic inference variables | Rejected candidate does not contaminate later candidate unification. |

| **WH-017** | P1 | P | Reorder unrelated candidate overloads | Static evaluator returns same selection and results. |

| **WH-018** | P2 | P | Repeat same `where` computation across distinct generic instantiations | No static-evaluation cache collisions. |



## CA. Casts, type guards and trait matching

**Design basis:** §2.4; §10.1; §13; §14  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **CA-001** | P0 | C+R | Static `as[Trait]` from implementing object | Proven coercion accepted with preserved runtime value. |

| **CA-002** | P0 | C− | Static `as[Trait]` from non-implementer | Rejected at compile time. |

| **CA-003** | P0 | C+R | Checked `as?[Concrete]` from trait-typed value succeeds | Returns `Some[Concrete]` with fields accessible. |

| **CA-004** | P0 | C+R | Checked `as?[Concrete]` from nonmatching trait value fails | Returns `None`, not a panic or arbitrary reinterpretation. |

| **CA-005** | P0 | C+R | Asserted `as![Concrete]` succeeds | Original concrete value and metadata preserved. |

| **CA-006** | P0 | C+F | Asserted `as![Concrete]` fails | Proper runtime cast fault, not compiler crash or invalid access. |

| **CA-007** | P0 | C+R | Open named trait match has catch-all | Concrete branches and default behave correctly. |

| **CA-008** | P0 | C− | Open trait match without catch-all | Nonexhaustive match rejected. |

| **CA-009** | P0 | C+R | Closed variant match exhaustive without catch-all | Accepted, unlike open trait. |

| **CA-010** | P1 | C+R | `as?` against trait requiring imported provider | Runtime refinement follows appropriate visible qualified relationship. |

| **CA-011** | P1 | C+R | Matching trait type after runtime multimethod call | Concrete type preserved for later pattern checks. |

| **CA-012** | P1 | C+R | Repeated cast / match over union of trait implementers | Each branch runtime predicate accurate. |

| **CA-013** | P1 | C+R | Cast is chain separator | Trait coercion does not alter expression chain ordering. |

| **CA-014** | P2 | P | Refactor pattern matching into checked casts where equivalent | Same branches and observable outputs. |



## VS. Vectorization, higher-order calls and stack behavior

**Design basis:** §0; §4; §6; §7; §8; §13  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **VS-001** | P0 | C+R | Trait-friendly element maps over list of implementers | One output per item in correct order. |

| **VS-002** | P0 | C+R | Two trait-bearing vectorized argument lists equal length | Per-index application yields correct results. |

| **VS-003** | P0 | C+F | Two unequal-length trait-bearing lists | `VectorisationFault` at runtime, no out-of-bounds behavior. |

| **VS-004** | P0 | C+R | One scalar trait-bearing receiver broadcast against list | Scalar reused exactly as specified across each call. |

| **VS-005** | P0 | C+R | `novec` trait parameter broadcast | Whole nonvectorized argument preserved while other input vectorizes. |

| **VS-006** | P0 | C− | Trait method invocation under disallowed vectorization | Reject when `novec` blocks the only adaptation. |

| **VS-007** | P1 | C+R | Trait method used in `map` via `:` shorthand | Contextual function signature and method resolution correct. |

| **VS-008** | P1 | C+R | Trait method used in `reduce` | Correct trait requirement and accumulator type. |

| **VS-009** | P1 | C+R | Trait method used in `fold` with distinct seed type | Generic inference does not confuse seed and element. |

| **VS-010** | P1 | C+R | Trait method used by `both` combinator | Each input group resolves correct overload and output row. |

| **VS-011** | P1 | C+R | Trait method used by `sequence` combinator | Two independent callable signatures preserve argument partition. |

| **VS-012** | P1 | C+R | Trait callable through ECS with omitted `_` arguments | Stack popping occurs in documented right-to-left partition. |

| **VS-013** | P1 | C+R | Trait element returning two results passed to tupled wrapper | Exactly two outputs remain, no branch leakage. |

| **VS-014** | P1 | C+R | Trait method inside closure with capture | Captured value and selected implementation persist across later calls. |

| **VS-015** | P1 | C+R | Explicit-parameter function argument cycling around trait call | Original inputs reused only when stack underflows. |

| **VS-016** | P2 | P | Equivalent ECS and stack-call forms | Same stack trace, outputs and side effects. |



## LC. Object representation, reconstruction, and lifecycle

**Design basis:** §12.3–12.8; §19.4; §19.9  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **LC-001** | P0 | C+R | Trait method reads private object state | Reads original correct field even when object layout differs across implementers. |

| **LC-002** | P0 | C+R | Trait method performs immutable field update | Returned object changed, original occurrence unchanged. |

| **LC-003** | P0 | C+R | Trait method called on shared-state `&Type` handles | Updates visible through all handles to same arena slot. |

| **LC-004** | P0 | C+R | Trait method on two different shared-state arena types | No cross-type arena collision. |

| **LC-005** | P0 | C+R | Trait implementing object with destructor | `~Type` runs exactly once on final release. |

| **LC-006** | P0 | C+R | Trait value passed through generic function then released | Final release and destructor behavior correct. |

| **LC-007** | P0 | C− | `@nodup` implementer duplicated statically | Diagnostic, not hidden extra occurrence due to trait upcast. |

| **LC-008** | P0 | C+F | Runtime duplication not statically decidable | `DuplicationFault` backstop fires when actual member noncopyable. |

| **LC-009** | P0 | C+R | Trait-constrained generic with copyable object | Permitted `dup`, `copy`, and `move` preserve value and count. |

| **LC-010** | P1 | C+R | `@nodup` type passed as trait value without duplication | No false positive due solely to abstraction. |

| **LC-011** | P1 | C+R | Copyable union of trait implementers | `dup` succeeds when all members copyable. |

| **LC-012** | P1 | C− | Union containing noncopyable implementer is duplicated | Compile-time rejection when member known. |

| **LC-013** | P1 | C+R | Trait object containing nested resource fields | Destruction releases fields in reverse declaration order. |

| **LC-014** | P1 | C+F | Trait-related destructor panics | Teardown completes and primary panic propagation follows object rules. |

| **LC-015** | P1 | C+R | `@mustcall` protocol operation invoked via object-friendly trait-related path | Contract state reflects qualifying normal completion [Decision if trait aliases of required method are counted]. |

| **LC-016** | P1 | C+F | Trait-typed value carries unsatisfied `@mustcall` protocol | Runtime backstop enforces contract when static check inconclusive. |

| **LC-017** | P2 | P | Reconstruct object repeatedly through trait methods | Shared-state identity and protocol state remain consistent. |



## TG. Tags, effects, indexing and other interfaces

**Design basis:** §9.0; §17; §18; §19; §21  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **TG-001** | P0 | C+R | Tagged concrete subtype vs untagged trait overload | Tag-specific overload has documented priority. |

| **TG-002** | P0 | C+R | Trait-aware computed tag remains after supported operation | Overlay return contract preserved on runtime value. |

| **TG-003** | P0 | C− | Invalid tagged trait signature requiring disjoint tags | Compile error, not impossible overload accepted. |

| **TG-004** | P0 | C+R | Trait-typed value with runtime tag variant | Runtime match checks variant while static type sees parent computed tag. |

| **TG-005** | P1 | C+R | Trait-aware tag validator selected among overloads | Most-specific applicable validator executes, not first declared. |

| **TG-006** | P1 | C− | Ambiguous tag validator of trait type | Compile error; validator never executes speculatively. |

| **TG-007** | P1 | C+F | Tag validator fails on trait-bearing value | Fault; validation atomic and no tag evidence committed. |

| **TG-008** | P1 | C+R | `@index(Trait)` provider on trait-typed receiver | Named-trait custom indexing uses compatible provider correctly. |

| **TG-009** | P1 | C+R | `@update(Trait)` provider on trait-typed receiver | Replacement returns correct rebuilt receiver, no extra result. |

| **TG-010** | P1 | C− | Trait index provider wrong arity or result count | Invalid provider rejected; no stack corruption. |

| **TG-011** | P1 | C+R | Trait method carries `Panic[FaultType]` effect | Effect inferred at callers and propagated at runtime. |

| **TG-012** | P1 | C− | `panic` with non-Fault trait value | Compile error; nominal Fault requirement enforced. |

| **TG-013** | P1 | C+R | `@errType`-generated Err implementation | Generated trait behavior available and `Result` conversion works. |

| **TG-014** | P2 | P | Serialize tagged trait results then restore, where supported | Validator/overlay evidence and dispatch preserved. |



## CC. Concurrency, tasks, channels, and trait values

**Design basis:** §25; §12.6; §13  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **CC-001** | P1 | C+R | Trait implementer captured by spawned task | Correct callable/receiver and required transfer semantics. |

| **CC-002** | P1 | C+R | Generic trait-constrained function executed in task | Correct specialization and Task output type. |

| **CC-003** | P1 | C+R | Trait-typed value passed through invariant Channel[Trait] | Sender/receiver observe same concrete behavior after transfer. |

| **CC-004** | P1 | C− | Channel[Concrete] widened to Channel[Trait] | Rejected because channel type is invariant. |

| **CC-005** | P1 | C+R | Multiple trait implementers sent through one Channel[Trait] | Dispatch/result correct per received value and send order. |

| **CC-006** | P1 | C+R | Shared-state implementer handles across task boundaries | Documented aliasing and arena lifetime preserved. |

| **CC-007** | P1 | C− | Isolated noncopyable object captured implicitly by task | Requires unique ownership and explicit move. |

| **CC-008** | P1 | C+R | Isolated trait implementing object explicitly moved into task | Sender no longer has occurrence; moved task method call succeeds. |

| **CC-009** | P2 | C+F | Cancellation while task holds trait implementing resource | Cleanup, destructor and mustcall behavior follow documented rules. |

| **CC-010** | P2 | P | Vary cooperative interleavings for calls on distinct trait values | Same type/impl identity; no accidental global dispatch-cache state. |



## IN. Diagnostics, recovery, caching and incremental compilation

**Design basis:** §4.2; §13; §16; §22; §23; implementation-hardening extrapolation  

| ID | Priority | Oracle | Test scenario | Expected result / regression detected |

|---|---|---|---|---|

| **IN-001** | P0 | C− | Invalid impl followed by valid unrelated impl | No compiler crash; valid declaration unaffected in recovered analysis. |

| **IN-002** | P0 | P | Compile failing file, then successful file in same process | No poisoned constraint or symbol state. |

| **IN-003** | P0 | P | Compile same fixture twice consecutively | Same diagnostics, generated resolution IDs and output. |

| **IN-004** | P0 | P | A-B-A trait instantiation sequence | First and third A results identical. |

| **IN-005** | P0 | P | Make an inherited requirement invalid then fix it | Incremental compile rejects then accepts, with no stale success. |

| **IN-006** | P0 | P | Change a provider implementation only | Dependent callers recompile or invalidate correctly. |

| **IN-007** | P0 | P | Remove imported behavior-set evidence | Existing trait uses stop compiling until evidence restored. |

| **IN-008** | P0 | P | Replace imported provider A with B | Observable results update; no stale A call target. |

| **IN-009** | P0 | P | Change trait method return signature | All dependents re-typecheck and stale bytecode invalidated. |

| **IN-010** | P1 | P | Change only names of private unrelated elements | Trait dispatch/result unchanged. |

| **IN-011** | P1 | P | Reorder trait/import declarations without semantic differences | Same output and diagnostics. |

| **IN-012** | P1 | P | Run multi dispatch after several different concrete calls | No mutable global candidate cache leak. |

| **IN-013** | P1 | P | Compile bytecode, reload and execute | Preserve trait nominal identity, chosen impl and union branch plan when persistence supported. |

| **IN-014** | P1 | P | Cross-module generics compiled in two dependency versions | No invalid linkage or silently mixed implementation identities. |

| **IN-015** | P2 | P | Generate randomized acyclic blanket behavior graph | Compute expected transitive reachability and compare compiler outcomes. |

| **IN-016** | P2 | P | Generate randomized cyclic behavior graph with seeds | No unsupported evidence from a cycle; guaranteed solver termination. |

| **IN-017** | P2 | P | Generate alpha-renamed isomorphic trait program | Same outputs and diagnostics after renaming. |

| **IN-018** | P2 | P | Generate module import permutations | Unique/ambiguous provider classification stable. |

| **IN-019** | P2 | P | Run test suite under debug/release/alternative backends | Identical specified observable semantics. |

| **IN-020** | P2 | P | Stress 100+ unrelated traits and 1,000 impls | Bounded compile time; no identity collision or pathological lookup. |



## Reference fixtures — Valiance-style programs

These are **illustrative source fragments** to turn into stand-alone `.vlnc` tests. They use design syntax rather than translating the examples into Rust. Comments describe the test oracle. If a fragment needs a project harness, put supporting declarations in additional files. The examples are intentionally small enough to isolate each failure.

### F1. Required method + default method + two concrete implementers

```valiance
trait Labelled =>
  extend label -> String
  define decorated => "[" + ($self label) + "]"
end

object Alpha => end
object Beta => end

object Alpha as Labelled =>
  define label => "alpha"
end
object Beta as Labelled =>
  define label => "beta"
end

Alpha label       #? "alpha"
Beta label        #? "beta"
Alpha decorated   #? "[alpha]"
Beta decorated    #? "[beta]"
```

**Mutations:** remove `Beta.label` (C−); change its return to `Number` (C−); add an explicit `Beta.decorated` override (override wins); run calls B/A/B/A; put calls through a `Labelled`-typed wrapper. If your parser requires parentheses around the chained method inside the default, retain equivalent semantics and correct the surface syntax.

### F2. `$self` in the trait return contract

```valiance
trait Resizable =>
  extend resized(factor: Int) -> $self
end

object Box =>
  $width: Int
end

object Box as Resizable =>
  define resized(factor: Int) -> $self =>
    $self.width = $self.width * $factor
    $self
  end
end

Box(3) resized(4) $.width #? 12
```

**Oracles:** output type is `Box`, not the abstract `Resizable`; original `Box(3)` occurrence is not observably mutated; a deliberately wrong return type must fail. `Box` may need renaming if the implementation reserves it.

### F3. Intrinsic trait inheritance and reused requirements

```valiance
trait Logger => extend log(:String)
trait ErrorReporter as Logger =>
  define reportError(:String) => $self log
end

object ConsoleLogger => end
object ConsoleLogger as Logger =>
  define log(:String) => println
end
object ConsoleLogger as ErrorReporter => end

ConsoleLogger reportError("oops") #? stdout: oops
```

**Mutations:** remove the Logger impl (C−); add an override of `reportError`; add a third inherited level; pass `ConsoleLogger` to a parameter typed `Logger`.

### F4. Two provider modules: qualification must change runtime result

Create independent source files with the *same* object and trait identities, but two distinct behaviour providers. The syntactic module layout and public exports should follow §23.8:

```valiance
# provider_a.vlnc / provider_b.vlnc conceptual declarations:
# each defines or imports Rectangle and Shape and supplies
# object Rectangle as Shape => define area => 11/22 end end

# main.vlnc — using both explicitly
import {
  provider_a.object Rectangle as Shape,
  provider_b.object Rectangle as Shape
}
Rectangle(4) as[provider_a.Shape] | area #? 11
Rectangle(4) as[provider_b.Shape] | area #? 22
```

**Oracles:** unqualified `Rectangle(4) area` is C− ambiguous, the two qualified calls differ, reversing imports changes nothing. Implement provider modules with a common canonical `Rectangle` and `Shape` declaration; do **not** accidentally define two different nominal objects or traits.

### F5. Generic argument correlation through trait-to-trait implication

```valiance
trait[T] Producer => end
trait[T] Iterable => end
trait[T] Producer as Iterable[T] => end

# Supply an object with visible Producer[Int] evidence.
# Use it at a parameter typed Iterable[Int] (C+).
# Use the same object as Iterable[String] (C−).
```

**Oracle:** the type argument `Int` is preserved across the implication. Repeat after importing the relationship explicitly and after re-exporting it. This fixture is intentionally a *completion recipe* because the supplied design does not demonstrate fully concrete implementations of this particular empty generic relationship.

### F6. Anonymous structural trait

```valiance
define[T] sumStructurally(
  :trait[T] =>
    extend +(:T, :T) -> T
  end +
) -> T => reduce: +

sumStructurally([1, 2, 3] as[Number+]) #? 6
```

**Mutations:** use a type whose visible `+` overload has a wrong return type; hide/reveal the overload with imports; build another structural trait requiring `===` as shown in §13.1. Avoid treating nominal `Addable[T]` membership as sufficient for the inline trait unless the structural overload is visible.

### F7. `multi define`: exact runtime type matters only for multi

```valiance
trait Collidable => end
object Spaceship => end
object Spaceship as Collidable => end
object Asteroid => end
object Asteroid as Collidable => end

define collide(:Collidable, :Collidable) => "default"
multi define collide(:Asteroid, :Spaceship) => "A-S"
multi define collide(:Spaceship, :Asteroid) => "S-A"
multi define collide(:Spaceship, :Spaceship) => "S-S"
multi define collide(:Asteroid, :Asteroid) => "A-A"

collide(Asteroid, Spaceship) #? "A-S"
collide(Spaceship, Asteroid) #? "S-A"
```

**Mutations:** upcast both arguments to `Collidable` before the call to force dynamic selection, and compare with a parallel non-`multi` overload set where static `Collidable` must stay the broad overload. Include an unhandled third implementer to exercise `default`.

### F8. Open trait matching versus closed variant matching

```valiance
trait Form => end
object Round => end
object Round as Form => end
object Square => end
object Square as Form => end

define classify(:Form) =>
  match =>
    as :Round => "round"
    as :Square => "square"
    _ => "other"
  end
end
```

**Oracles:** this open-trait match compiles and uses the final fallback. Removing `_` is C− under §10.1; an exhaustive variant match is a separate positive case. Verify a newly introduced implementer returns `"other"` rather than selecting an unrelated concrete branch.

### F9. Controlled static computation: valid and invalid `where`

```valiance
# Valid: only the documented static evaluator operations are used.
define rankInfo[T](xs: T+$n) -> Int
where ($r = $n + 1) => $r

# Negative variant: invoke an arbitrary user-defined trait method
# inside `where` and expect a COMPILE ERROR rather than const-folding.
```

**Oracles:** the exact-list-rank binder and static arithmetic produce the intended hidden numeric value; invalid arbitrary calls are rejected. **Implementation note:** if your current grammar requires a different form for returning a computed `$r`, keep the `where` arithmetic and assert the computed type/rank through a result type instead, as illustrated in §22.4.

### F10. Union-covered overload function should not re-resolve at runtime

Start from §6.5's documented example:

```valiance
$values = [1, 2, "A", "B"]
println($values map: * 2)
#? [2, 4, AA, BB]
```

**Upgrade for traits:** add a broad trait overload and a narrower concrete overload. Assign the overload set to a function type with a *union input*, then supply a runtime concrete subtype of a broad static branch. The preselected branch's overload must execute; the runtime must **not** speculatively select the narrower concrete overload. This upgrade is a construction recipe because §6.5 does not provide a one-line explicit coercion example for that function type.

### F11. Minimum-rank assurance and trait evidence

```valiance
trait Taggable => end
object Token => end
object Token as Taggable => end

Token ^+  #? singleton list containing the Token object
```

**Mutations:** `^+2`, union with a scalar and a rank-1 trait list, calling an element whose parameter type is `Taggable+`, and checking lint L023 when all possible branches already have enough rank. Keep the rank and trait tests orthogonal to identify the failing stage.

### F12. Concurrency with a trait-constrained generic

```valiance
trait HasValue => extend value -> Int
object Answer => end
object Answer as HasValue =>
  define value => 42
end

$task = fn -> Int => Answer value end | spawn
$task wait #? 42
```

**Mutations:** capture a trait-typed argument, pass a value via `Channel[HasValue]`, and send two different implementers. A `Channel[Answer]` must not be treated as interchangeable with `Channel[HasValue]` because channels are invariant.

## Failure classifications / bug signatures

| Symptom | First suspected subsystem | High-signal IDs |
|---|---|---|
| Works only when a different type is compiled first | substitution / solver cache / specialization | GN-005/006; IN-004 |
| Wrong provider only after import or re-export | behaviour-set visibility / identity | IM-004–015; BL-011–018 |
| Compile success but wrong `multi` result | runtime type predicates / multimethod lowering | MM-002–012 |
| Ordinary trait overload changes with runtime subtype | wrong static-vs-dynamic dispatch | MM-003; UC-005 |
| `where` false predicate spoils later candidate | inference snapshot / candidate rollback | WH-002/016; OV-020 |
| Works in clean build but not incremental | dependency invalidation | IN-005–009 |
| Valid anonymous trait spuriously fails | structural overload evidence / scope | AN-001/007/012 |
| `A[T]` accidentally proves `B[U]` | generic implication substitution | BL-002/003; GN-001/002 |
| Solver hangs on blanket cycles | graph search / memoization | BL-005/006/021; IN-016 |
| Behavior differs across optimization levels | codegen / reification / calling convention | MM-019; UC-015; IN-019 |

## Additional property-based and mutation testing

1. **Generate a graph of named traits** and importable `trait A as B` edges. Track which edges are visible. Given seeded concrete object impls, compute reachability for each nominal/generic argument pair. A pure cycle with no seed must not prove anything; multiple distinct applicable providers must remain ambiguous.
2. **Generate trait-bearing overload sets** with one to four parameters; calculate dominance using the documented *all parameter positions strictly more specific* rule. Include incomparable pairs, tagged/untagged cases, and `novec`/`exact` filters.
3. **Generate multi-dispatch matrices** over two to five implementers, with distinct sentinel outputs in every cell. Compare concrete calls, trait-upcast calls, generic wrappers, loops over trait lists, and optimized builds.
4. **Mutate successful programs one rule at a time:** delete an `extend` implementation, alter a return type, alter generic arguments, hide a provider import, replace `as[Provider]` with `as[OtherProvider]`, remove a fallback, or change a `where` assertion from true to false. Verify the oracle flips exactly where expected.
5. **Metamorphic programs:** alpha-renaming, irrelevant declaration additions, unrelated module import insertion, reordering declarations/loads, direct-vs-qualified equivalent calls, static generics wrapper insertion, clean-vs-incremental builds. Compare values, output effects, type inference and diagnostics.
6. **Bounded stress:** cap generated graph size, recursion/compile budget, and runtime steps; classify compiler timeout or segfault as a failure, not an inconclusive test. Record random seeds to reproduce.
7. **Backend inspections:** when the compiler exposes typed IR or traces, assert chosen trait ID, provider identity, generic arguments, preselected union branch plan, and `multi` fallback/specialization. These debug assertions make a wrong-result regression much easier to localize.

## Coverage and CI gates

- **Pre-merge:** all **P0** cases; P1 cases directly touched by a patch; run a small fixed-seed graph and multimethod matrix; run both non-optimized and optimized execution when both modes exist.
- **Nightly:** all P0/P1, P2 stress/property tests, module and incremental suites, multiple deterministic seeds, compiler-process recovery.
- **Release:** full suite plus from-scratch CI build, persisted bytecode/regeneration checks where implemented, randomized order and backend comparison, and a wall-clock timeout budget for cyclic trait resolution.
- **Test triage:** track bugs separately as parsing, symbol scope, trait evidence, generic inference, overload selection, typed IR, runtime predicate, codegen, static evaluator, module cache, effect/lifetime, and diagnostics.

### Suggested integration naming convention

`trait/<suite>/<ID>_<short_slug>.vlnc`; expected-output `.stdout`, expected-failure `.diag`, and multi-file examples in directories with a `main.vlnc` and supporting public modules. Use one assertion per failure oracle when feasible. Use stable internal diagnostic codes over raw message strings if your compiler emits them.

## Areas the design does **not** fully determine

1. **Unrestricted compile-time trait-method evaluation:** no such facility is specified. Comptime tests should target `where` and compile-time type/overload/trait reasoning unless a newer implementation document explicitly adds general evaluation.
2. **Runtime representation of a trait value:** no mandated vtable/fat-pointer ABI appears in the supplied document. Test observables (correct call, cast, copy, destructor, dispatch) rather than assuming table offsets or object layouts.
3. **Provider identity across redundant imports/re-exports:** the addendum says re-exports retain originating behavior identity internally; tests should confirm path deduplication for the same originating provider. If implementation handles duplicated direct imports differently, explicitly document that rule.
4. **Overlap resolution among multiple equally specific `multi` specializations:** §20 mandates fallback and return compatibility but does not fully spell out every tie-breaking edge. Decide and test it explicitly.
5. **Trait-default collisions across multiple intrinsic ancestors:** where two unrelated parent defaults provide an identically named/signatured element, §13 does not fully define conflict handling. Decide whether explicit override is required or which conflict diagnostic applies.
6. **Whether `@mustcall` named method obligations can be met through an alias exported as a trait method:** §19.9 keys names to object-friendly methods; do not invent new equivalences. Test only the established object-friendly call path until specified.
7. **Compiler execution commands / test harness:** the design includes `vln run` and `vln compile` project commands (§24), but this document has not inspected or run your compiler, so expected CLI flags and diagnostic codes are not guessed.

### Completion criteria

A hardened trait implementation should accept every valid fixture, reject every invalid fixture deterministically, preserve observed behavior across all applicable dispatch/optimization/import modes, and never hang or crash on invalid input. Record an explicit spec decision for each unresolved edge before promoting it into a mandatory regression test.
