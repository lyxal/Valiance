# Valiance — Generics Compiler and Runtime Hardening Test Specification

**Status:** Proposed regression-test inventory based on the attached `language(2).md` (6,232 lines).  
**Scope:** Existing Valiance generics, their inference and type algebra, call-site type checking, `where` compile-time execution, and runtime specialization / representation.  
**Explicit exclusion:** **Shared-state objects and shared-state handles are scrapped**. No test in this document assumes §12.8 (`Shared State Objects`) exists or needs to be supported. All mutation tests use ordinary immutable value reconstruction and local variable assignment where permitted.  
**Source blueprints:** The original inventory predates the executable regressions. Five P0 expectations (GD-004, UN-002, UN-012, OV-005, IN-012) have since been reconciled with the decided language rules; use the numbered tests for compiler-verified source spelling.  
**Coverage:** Extensive target inventory, not a proof of exhaustiveness. A case may become multiple independent concrete fixtures.

## Design contract and limits

1. The primary generics contract is **no generic type erasure**, names-only generic parameter lists for `define`, traits, and variants, and generic constraints expressed in value/type positions (§16). §12's description of possible bounded object parameters differs from the names-only statement in §16; treat bounds spelling on objects as a **specification decision** until reconciled.
2. The generic **unification/combine rules in §16.2 are explicitly marked tentative** by the design. Tests with residual list rank and cross-parameter combination should assert the behavior currently chosen by the implementation and be tagged `[Decision]` if design review has not finalized it; do not hardcode one interpretation as settled language law.
3. `exact`, `atomic`, and `novec` are **call policies / candidate evidence**, not runtime types (§16.1–16.2, §7.2). `exact` is applied before normal unification; `novec` prevents peeling vectorisation rank. `atomic` enforces scalar evidence without resetting the solved generic.
4. `where` is a **restricted static evaluator**, *not* arbitrary Zig-style comptime execution (§22). Compile-time cases here test rank/type computations, function signature introspection, and overload rejection via `?`, not arbitrary trait-method execution.
5. Standard overload resolution is static (§4.2); `multi define` is opt-in runtime specialization (§20). Union-covered overloaded functions can carry precomputed runtime branch plans (§6.5); these tests keep those paths distinct.
6. Types and generics can occur in ranked lists (`+`, `*`, `~`), tuples, records, function values, objects, variants, tags, optionals, results, and type-constrained imports. These interactions are where cross-phase errors arise.
7. **Do not test shared-state semantics:** §12.8 has been intentionally excluded at the user's direction. There are no tests of `&Object` handles, shared alias mutation, lease rules, or shared-state concurrency.

## Oracle and priority legend

| Marker | Contract |
|---|---|
| **C+** | Must compile; assert type/substitution/selected overload (where observable) and expected warnings. |
| **C−** | Must reject at compile time with meaningful diagnostic; must not crash or hang. |
| **R** | Runtime exact value, output, inferred/actual stack shape, call counts or selected specialization. |
| **F** | Expected runtime fault, including exact fault family and cleanup if applicable. |
| **P** | Property / metamorphic comparison across equivalent source forms or compiler settings. |
| **P0** | Soundness or wrong-code blocker: gate every change. |
| **P1** | High-value interaction / edge correctness: gate main CI. |
| **P2** | Extended, fuzz, scale or secondary integration: nightly. |
| **[Decision]** | Expected semantic corner not fully fixed in design. Capture the decision before a hard assertion. |

**Per-case metadata:** `id`, `priority`, `oracle`, `main_source`, `auxiliary_modules`, `input/args`, `expected_compile`, `expected_type`, `expected_resolution`, `expected_stdout_or_stack`, `expected_fault`, `diagnostic_category/span`, `mode`, `random_seed`, `timeout`. Diagnostic category and span are safer than matching entire wording. Compile-error cases should be isolated in independent files.

**Test modes:** interpreter or VM (where present), compiled/debug, compiled/optimized, fresh build, incremental build, repeated compilation in same process, separate modules, serialized-bytecode reload if available. Runtime tests must use non-constant or externally provided inputs to prevent all execution paths being optimized away.

## Coverage summary

| Suite | IDs | Cases | Focus |
|---|---|---:|---|
| **GD** | GD-001–020 | 20 | Generic declaration, parsing, explicit instantiation |
| **UN** | UN-001–022 | 22 | Unification, substitution, optional and shape algebra |
| **CP** | CP-001–019 | 19 | `exact`, `atomic`, and `novec` candidate policy |
| **OV** | OV-001–020 | 20 | Generic overload resolution and disambiguation |
| **IN** | IN-001–018 | 18 | Inferred generic functions and row polymorphism |
| **OB** | OB-001–020 | 20 | Generic objects, fields, constructors, inferred variance |
| **TB** | TB-001–016 | 16 | Generic traits and structural generic constraints |
| **VA** | VA-001–015 | 15 | Generic variants, enums and tagged results |
| **RK** | RK-001–020 | 20 | Ranks, vectorisation, rugged lists and generic lists |
| **FV** | FV-001–017 | 17 | Generic function values, higher-order types and closures |
| **CS** | CS-001–016 | 16 | Call-site type checking, variadic tuple patterns |
| **WH** | WH-001–020 | 20 | `where` static evaluation, ranks, overload rejection |
| **UC** | UC-001–016 | 16 | Union-covered generic overloaded callable plans |
| **TG** | TG-001–016 | 16 | Generic data tags and tag reification |
| **IM** | IM-001–016 | 16 | Modules, separate compilation and generic visibility |
| **RT** | RT-001–018 | 18 | Runtime specialization, representations and code generation |
| **EC** | EC-001–014 | 14 | Errors, optionals, returns and generic flow |
| **IX** | IX-001–014 | 14 | Generic indexing, immutable updates and structured data |
| **CX** | CX-001–018 | 18 | Stress, cache invalidation, deterministic analysis and recovery |
| **MT** | MT-001–016 | 16 | Metamorphic, differential and mutation-based tests |

**Total: 351 individually identified cases, 20 suites; P0=221, P1=103, P2=27.**

## Recommended initial gate (high signal)

The first smoke run should include **GD-001/002/009**, **UN-001/006/007/008/013/016**, **CP-001/004/006/009/013/018**, **OV-001/006/011**, **IN-001/005/009**, **OB-001/009/010**, **TB-002/004**, **RK-003/005/007**, **FV-003/010**, **CS-001/012**, **WH-001/007/010/016**, **UC-001/003/004**, **IM-001/006**, **RT-002/004/007**, **CX-003/006/009**. This catches the bugs most likely to survive basic happy-path tests: wrong generic cache keys, invalid rank inference, inference rollback failures, incomplete constraint propagation and runtime branch/code specialization errors.

### Suggested CI matrix

| Dimension | Values | Motivation |
|---|---|---|
| Type instantiation | `Int`, `Real`, `Number`, `String`, generic object, nested object, union | Distinguish subtype evidence, nominal identity and generic shape |
| Shape | atomic, rank-1, rank-2, rank-4, min rank, rugged, optional | Exercises rank algebra and exact/novec policies |
| Call syntax | stack call, ECS, named ECS, `_`, explicit `[T]`, hints `{Type}` | Different parser/lowering paths |
| Overload source | local, imported, aliased, shared-body overload, function value | Correct candidate identity and argument binding |
| Program phase | analyzer, `where` static evaluation, runtime VM, optimizer | Static/runtime consistency |
| Build | fresh, repeated same process, incremental, separately compiled | Specialization and semantic-cache correctness |
| Observable values | runtime input, variant branches, mutable local stores of immutable values | Avoid constant-fold-only validation |

## GD. Generic declaration, parsing, explicit instantiation

**Design basis:** §§11, 12, 13, 14, 15, 16; §7.1.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **GD-001** | P0 | C+R | `define[T] id(value: T) -> T` called with `Int`, `String`, and an object | Each call preserves the value and its independently inferred type. |
| **GD-002** | P0 | C+R | `define[T,U] first(a: T,b: U)->T` with distinct argument types | Output type equals first argument type; second does not overwrite `T`. |
| **GD-003** | P0 | C+R | Generic parameter used only in return type but supplied explicitly at call site | Explicit type argument determines return type if body can produce it; otherwise ordinary return type checking rejects body. |
| **GD-004** | P0 | C+ C− R | Return-only generic with explicit type arguments, uniquely expected return type, or no contextual evidence | Explicit or unambiguously contextual inference succeeds; otherwise reject with an unresolved-generic diagnostic. Never default to `Number`. |
| **GD-005** | P0 | C− | `define[T: Shape]` or `trait[T: Shape]` when generic parameter lists are names-only | Reject constraint in list; express constraint in type position. |
| **GD-006** | P0 | C− | Unbound generic name in a declared parameter type | Unknown generic identifier diagnosed at declaration. |
| **GD-007** | P0 | C+R | Generic parameters named `T,U` swapped consistently throughout declaration | Alpha-renaming preserves call results and types. |
| **GD-008** | P0 | C+R | Explicit call `convert[Int](...)` versus inferred `convert(...)` | Same selected implementation whenever both are valid and imply `T=Int`. |
| **GD-009** | P0 | C+R | Partial explicit arguments `convert[Int,_]{Number}(...)` | Unspecified positions inferred; `[]` precedes `{}` overload hints. |
| **GD-010** | P0 | C− | Wrong count of explicit type arguments | Reject with generic arity diagnostic; no stale parameter reuse. |
| **GD-011** | P0 | C− | Explicit generic type contradicts argument type | Reject or choose alternative overload only if its constraints genuinely fit. |
| **GD-012** | P0 | C+R | Nested generic invocation `outer[T]` calls `inner[T]` twice | Exact substitution survives two instantiations and stack/IR lowering. |
| **GD-013** | P1 | C+R | Generic declaration and call separated by several unrelated overload definitions | Same generic identity; no accidental binding to adjacent declaration. |
| **GD-014** | P1 | C+R | Generic symbol shadowing in nested function scopes | Each lexical generic belongs to its own binder and substitution environment. |
| **GD-015** | P1 | C+R | Generic name `T` in two separate exported modules | Different binders remain independent after import. |
| **GD-016** | P1 | C− | Malformed generic brackets, trailing separators, and duplicate generic parameter names | Clean parse/name diagnostic; no crash. |
| **GD-017** | P1 | C+R | Generic recursive call with explicit self-recursion and valid base case | Reuses/specializes consistent type arguments and terminates at runtime. |
| **GD-018** | P1 | C− | Generic type parameter is supplied to a numeric-only runtime operation expecting a number | Clean type mismatch rather than invalid bytecode. |
| **GD-019** | P2 | P | Move generic definition above/below its first use where resolution rules permit | No semantic or inferred-type change. |
| **GD-020** | P2 | P | Rename every generic binder and change whitespace around type arguments | No change in canonical type, runtime output or compiled specialization count. |

## UN. Unification, substitution, optional and shape algebra

**Design basis:** §16.2 (expressly provisional); §§2.1, 2.5.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **UN-001** | P0 | C+R | Same `T` occurs twice and arguments have identical concrete types | One consistent substitution propagated to return type and body. |
| **UN-002** | P0 | C+R | Two occurrences of `T` receive `Int` and `Real`, or unrelated `Int` and `String` | Promote numeric evidence to `Real` (not merely `Number`); unrelated evidence joins to `Int|String`. Never select an arbitrary argument type. |
| **UN-003** | P0 | C+R | `T` binds to `Int` then `Real` via distinct calls | Independent substitutions; no call-order dependent leakage. |
| **UN-004** | P0 | C+R | `Container[T]` parameter matched to `Container[Int]` | Bind `T=Int`, retain constructor identity. |
| **UN-005** | P0 | C− | `Container[T]` matched to unrelated generic constructor `Other[Int]` | Reject; do not unify merely because both have one parameter. |
| **UN-006** | P0 | C+R | `T+` against rank-1 numeric list | Bind scalar leaf type and return expected rank. |
| **UN-007** | P0 | C+R | `T+` against rank-2 numeric list | Test rank-subtraction solution (`T=Number+`) when permitted by candidate policy. |
| **UN-008** | P0 | C+R | `T+2` against rank-3 list | Check residual rank is one; no off-by-one in type substitution. |
| **UN-009** | P0 | C− | `T+3` against rank-2 list without compatible vectorisation | Reject, never create a negative-rank generic. |
| **UN-010** | P0 | C+R | `T*` against exact-rank 3 list | Minimum-rank generic evidence follows specified rank/subsumption rules. |
| **UN-011** | P0 | C+R | `T~` against nested ragged numeric list | Accepted only for legally rugged-compatible target; no uniform prefix assumed. |
| **UN-012** | P0 | C− | A `T+ novec` homogeneous requirement receives a rugged `T~` or minimum-rank `T*` argument | Reject reverse subtyping and generic rank absorption. `T+` <: `T*` <: `T~`; separate rank-adapted vectorised calls are not subtype conversion. |
| **UN-013** | P0 | C+R | `T?` with non-optional scalar input | Optional substitution works; result wrapping conforms to `Some[T]\|None`. |
| **UN-014** | P0 | C+R | `T?` with an optional input | Correct inner binding; do not accidentally increase optional depth. |
| **UN-015** | P0 | C+R | `T??` versus `T?` input / output | Preserve distinct nested optional levels; never flatten explicit `Some` nesting incorrectly. |
| **UN-016** | P0 | C− | Attempt to infer different generic variables by positional matching across reorderable union branches | Must not perform forbidden positional unification across unions. |
| **UN-017** | P0 | C− | Attempt analogous positional matching across an intersection | Must not perform forbidden positional intersection unification. |
| **UN-018** | P1 | C+R | Combine evidence `T*n` and `T*m` in either argument order | Canonical commutative result with documented minimum-rank behavior. |
| **UN-019** | P1 | C+R | Combine evidence `T+n` and `T` in either order | Same resulting substitution and runtime representation. |
| **UN-020** | P1 | C+R | Normalize `T\|None\|U` in a generic output | Canonical union order and correct optional lifting. |
| **UN-021** | P1 | C+R | Generic substitution in nested tuple, record row, `Function[T->U]`, and optional | Every nested occurrence substituted; no unresolved `T` in runtime signatures. |
| **UN-022** | P2 | P | Permute parameter order and corresponding arguments for commutative evidence combinations | Same inferred generic substitution or documented ambiguity. |

## CP. `exact`, `atomic`, and `novec` candidate policy

**Design basis:** §§16.1–16.2, 7.2.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **CP-001** | P0 | C+R | `T exact` with scalar integer | Accepted and binds scalar shape. |
| **CP-002** | P0 | C− | `T exact` with rank-1 collection | Candidate rejected before `T` can absorb list rank. |
| **CP-003** | P0 | C+R | `T+ exact` with rank-1 collection | Scalar `T` inferred; exact rank-one requirement retained. |
| **CP-004** | P0 | C− | `T+ exact` with rank-2 collection | Filtered; no vectorisation or generic rank absorption. |
| **CP-005** | P0 | C+R | `T+4 exact` with exact rank 4 collection | Accept, correct leaf binding and return rank. |
| **CP-006** | P0 | C− | `T+4 exact` with rank 3 and rank 5 values | Both filtered, no fallback unless another overload applies. |
| **CP-007** | P0 | C+R | `T+4 exact` with a valid rank-4 compatible view | Admit only documented exact-rank view compatibility. |
| **CP-008** | P0 | C+R | `exact` retained on function-value signature | Calling through value enforces same candidate filter as direct invocation. |
| **CP-009** | P0 | C+R | `T novec` receives `Number+` | Bind whole `T=Number+`; body sees list rather than per-item scalar. |
| **CP-010** | P0 | C− | `Number novec` receives list | Reject rather than vectorise. |
| **CP-011** | P0 | C− | `Number+ novec` receives rank 2 | Reject instead of peeling rank. |
| **CP-012** | P0 | C+R | Generic `T novec` and vectorising second parameter | Whole first argument broadcasts unchanged across second argument list. |
| **CP-013** | P0 | C+R | `atomic` marker on scalar occurrence plus other `T` evidence | Validate scalar without replacing previously solved `T` by scalar base. |
| **CP-014** | P0 | C− | `atomic` marker receives a non-scalar incompatible value | Candidate excluded; no malformed generic solution. |
| **CP-015** | P1 | C+R | `atomic` as sole generic evidence with scalar argument | Fallback generic solution comes from actual scalar type, not marker pseudo-type. |
| **CP-016** | P1 | C+R | Tagged and optional exact argument | Marker applies to complete type pattern and valid tags / optional shapes are checked before generic substitution. |
| **CP-017** | P1 | C− | `exact` occurs at wrong position in type expression | Parser/type checker rejects invalid marker order. |
| **CP-018** | P1 | C+R | Candidate A rejected by `exact`, generic candidate B succeeds | No partial substitutions or diagnostic residue from A. |
| **CP-019** | P2 | P | Compare direct call, function-value call, ECS, and imported call for same `exact`/`novec` signature | Identical acceptance and output. |

## OV. Generic overload resolution and disambiguation

**Design basis:** §§4.2, 7.1, 11.1–11.3, 16.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **OV-001** | P0 | C+R | Exact concrete overload versus applicable generic overload | Select exact according to specificity ordering. |
| **OV-002** | P0 | C+R | Exact-generic-equivalent versus broader union overload | Select generic-equivalent when strictly more specific. |
| **OV-003** | P0 | C+R | Generic overload versus overload reached only through vectorisation | Follow documented specificity ranking. |
| **OV-004** | P0 | C+R | Generic overload versus trait-match overload | Compare per-parameter specificity; do not pick by declaration order. |
| **OV-005** | P0 | C+R | Two equivalent generic overloads differ only by binder spelling or declaration order | The later definition replaces the earlier equivalent signature, just like repeated concrete overloads; unrelated incomparable signatures may still be ambiguous. |
| **OV-006** | P0 | C− | Two-argument candidates each more specific in a different parameter | Do not falsely order partial candidates; report ambiguity under all-parameters rule. |
| **OV-007** | P0 | C+R | Tagged candidate versus untagged generic candidate | Tag-specific match takes priority when legal. |
| **OV-008** | P0 | C+R | Generic overload explicitly selected by braces `{Type,_}` | Only hinted parameter patterns constrain candidate selection. |
| **OV-009** | P0 | C+R | Generic arguments and overload hints combined as `name[Int,_]{Number}(...)` | Generic argument slots and hint slots remain distinct. |
| **OV-010** | P0 | C− | Generic constraint unsatisfied after an apparently good parameter match | Candidate rejected and other candidates considered. |
| **OV-011** | P0 | C+R | Unsuccessful candidate temporarily binds `T` then loses to alternate overload | Winner binds independently; no inference-state contamination. |
| **OV-012** | P0 | C+R | Generic overloads defined before and after concrete overload | Same selected implementation when declarations reordered. |
| **OV-013** | P1 | C+R | `overload(...)` shared body with generic and concrete signatures | Body analyzed once per signature with correct substituted types. |
| **OV-014** | P1 | C− | Shared overload signature has wrong parameter count or multiplicity | Reject according to overload consistency restrictions. |
| **OV-015** | P1 | C+R | Generic overload with trailing default ECS argument | Default evaluated/substituted in selected generic context. |
| **OV-016** | P1 | C− | Same generic overload invoked as plain stack call missing optional ECS argument | Plain stack arity remains fixed; reject. |
| **OV-017** | P1 | C+R | Named ECS generic arguments and `_` placeholders | Correct positional filling and unified types. |
| **OV-018** | P1 | C+R | Imported overload and module-local overload with same visible name | Module-local precedence and built-in escape work as defined. |
| **OV-019** | P1 | C− | Imported generic overload conflict with indistinguishable signature | Clean conflict or ambiguity diagnostic as specified for imports. |
| **OV-020** | P2 | P | Permute unrelated overload declaration order and import order | Selection and diagnostics are stable. |

## IN. Inferred generic functions and row polymorphism

**Design basis:** §§6.4–6.6, 16.3–16.4.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **IN-001** | P0 | C+R | `fn (x) => $x` inference | Type is `Function[@1 -> @1]` and returns supplied value unchanged. |
| **IN-002** | P0 | C+R | Two independent inferred inputs | Distinct `@1` and `@2` unless use connects them. |
| **IN-003** | P0 | C+R | Inferred `@n` appears again in output | Same type variable rather than fresh independent output variable. |
| **IN-004** | P0 | C+R | Explicit anonymous generic `@1` in a function-type parameter | Source parsing and invocation preserve correlation. |
| **IN-005** | P0 | C+R | Row inference from `fn => $.x` | Input type requires field `.x`; output field type is correlated with row variable. |
| **IN-006** | P0 | C+R | Explicit row `fn[T,U](x:T(.bar:U))->U` | Works for distinct records/objects having `bar` with appropriate type. |
| **IN-007** | P0 | C− | Call row-polymorphic function on record missing `.bar` | Reject at call site with field requirement diagnostic. |
| **IN-008** | P0 | C− | Call row-polymorphic function with field `.bar` of incompatible type | Reject; do not coerce unrelated value silently. |
| **IN-009** | P0 | C+R | Same anonymous function value called with two different types consecutively | No leakage of first substitution to second. |
| **IN-010** | P0 | C+R | Inference through chained overloaded elements (`fn => + double`) | Keep only compatible numeric candidate per forward inference. |
| **IN-011** | P0 | C+R | Inferred `fn => +` produces overload set | Both numeric and string usages work according to available overloads. |
| **IN-012** | P0 | C+R | Untyped named parameter never used | Valid universally generic inferred input; different caller argument types all produce the same result. No inferred trait requirements. |
| **IN-013** | P1 | C+R | Row constraint with multiple fields and different generic field types | Each field independently bound and reusable. |
| **IN-014** | P1 | C+R | Row type flowing through another generic function value | Required field and output correlation retained after higher-order pass. |
| **IN-015** | P1 | C+R | Row field type appears in optional, list or function output | Nested binding and reification correct. |
| **IN-016** | P1 | C− | Row access from disallowed/private field outside owner scope | Normal visibility applies despite structural inference. |
| **IN-017** | P1 | C+R | Two scopes independently generate `@1` | Do not unify anonymous generic variables based on printed index alone. |
| **IN-018** | P2 | P | Alpha-rename row-generic binders and reorder independent fields | Same behavior and normalized type constraints. |

## OB. Generic objects, fields, constructors, inferred variance

**Design basis:** §§12, 16; §12.2.1.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **OB-001** | P0 | C+R | Generic `object[T] Box` with field `T`, default constructor | `Box[Int]` and `Box[String]` preserve field types/values. |
| **OB-002** | P0 | C− | Construct `Box[Int]` with `String` field | Reject constructor type mismatch. |
| **OB-003** | P0 | C+R | Nested generic object `Box[Box[Int]]` | Correct nested substitution and readback. |
| **OB-004** | P0 | C+R | Two type parameters stored in reversed field declaration order | Constructors bind by declared field order and retain each type. |
| **OB-005** | P0 | C+R | Generic constructor with explicit return through implicit completed `$self` | No unresolved type parameter and no premature escape of receiver. |
| **OB-006** | P0 | C+R | Generic object friendly element returns `T` field | Method's result is the instance's concrete `T`. |
| **OB-007** | P0 | C+R | Object friendly element's `$self` return type | Includes all owner generic arguments, even when using explicit receiver. |
| **OB-008** | P0 | C− | Generic object method returns wrong `T` | Compile-time type mismatch; no erased return conversion. |
| **OB-009** | P0 | C+R | Read-only generic field with `Child <: Parent` | Covariant use admitted only if every occurrence is covariant. |
| **OB-010** | P0 | C− | Public writable `T` field and attempted covariant upcast | Invariance prevents storing a Parent into Child backing object. |
| **OB-011** | P0 | C+R | Generic parameter occurs only in function-parameter position | Contravariant relation applied only if inferred per declaration. |
| **OB-012** | P0 | C− | Unrelated nominal constructors with same generic arguments | No structural conflation of nominal identities. |
| **OB-013** | P1 | C+R | Field of type `T+` | Collection covariance and object variance composed legally. |
| **OB-014** | P1 | C+R | Field of type `Function[T->U]` | Variance polarity reverses in function parameter position and preserves in return. |
| **OB-015** | P1 | C+R | Field of type `T?` and explicit `Some[T]` | Optional wrapping and variance preserve nested generic type. |
| **OB-016** | P1 | C− | Field initializer infers inconsistent generic type relative to declared field | Reject even if constructor is otherwise unused. |
| **OB-017** | P1 | C+R | Separate instantiations of generic object share method body | Correct specialization and field layout for both instantiations. |
| **OB-018** | P1 | C+R | Explicit receiver appears in second parameter position of generic owner element | Receiver type includes generic args and argument order preserved. |
| **OB-019** | P2 | P | Reorder independent read-only fields in definition and adapt constructor | No change to inferred variance or generic semantics. |
| **OB-020** | P2 | P | Use `Int`, `String`, ranked list, tuple as same `Box[T]` test payload | No generic type erasure or incorrect runtime field decoding. |

## TB. Generic traits and structural generic constraints

**Design basis:** §§13, 13.1, 16; trait-to-trait behaviour-set addendum.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **TB-001** | P0 | C+R | `trait[T] Producer` and `Producer[Int]` implementation | Generic trait argument preserved and usable as constraint. |
| **TB-002** | P0 | C− | `Producer[Int]` evidence supplied to `Producer[String]` requirement | Reject; no omission of trait parameter from cache key. |
| **TB-003** | P0 | C+R | `Producer[T] as Iterable[T]` implication | Same concrete generic argument propagated through blanket relation. |
| **TB-004** | P0 | C− | Source `Producer[Int]` must not imply `Iterable[String]` | Reject even if trait names match. |
| **TB-005** | P0 | C+R | Nested generic trait implication with one transformation | Argument mapping exactly follows declared target, not position-based guess. |
| **TB-006** | P0 | C+R | Generic function parameter constrained by `Addable[T]` | Preserves relationship between trait parameter and return `T`. |
| **TB-007** | P0 | C+R | Anonymous trait `[T]` requiring `+(T,T)->T` | Structural satisfaction makes required operation available in generic function body. |
| **TB-008** | P0 | C− | Anonymous generic trait requirement has wrong return type | Reject structurally nonconforming candidate. |
| **TB-009** | P0 | C− | Generic trait parameters incorrectly written as bounds in `trait[...]` list | Reject per names-only grammar. |
| **TB-010** | P1 | C+R | Two valid generic trait instances for same object with distinct type arguments | Correct evidence selected by full instantiated trait type. |
| **TB-011** | P1 | C+R | Generic trait constraints combined with union or intersection parameter types | No forbidden positional union/intersection unification. |
| **TB-012** | P1 | C+R | Generic trait constraint resolved after explicit `as[QualifiedTrait]` evidence | Provider qualification preserved through substitution. |
| **TB-013** | P1 | C− | Candidate generic blanket implication fails its binder constraint | Ignore candidate; must not create ambiguity. |
| **TB-014** | P1 | C+R | Generic trait constraint inside function value passed to higher-order function | Evidence preserved across indirect call. |
| **TB-015** | P2 | P | Rename type parameter in trait and blanket relationship consistently | Identical concrete trait implication graph. |
| **TB-016** | P2 | P | Cycle in generic trait implications with no original evidence | No implication derived from nothing; solver terminates. |

## VA. Generic variants, enums and tagged results

**Design basis:** §§14, 15, 2.5, 21.1.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **VA-001** | P0 | C+R | Generic `Option[T]` variant with `Some` payload | Payload type preserved and extracted correctly. |
| **VA-002** | P0 | C+R | Generic variant constructor `None` whose member stores no `T` | Type args retained through type annotation/expected context. |
| **VA-003** | P0 | C− | Generic variant member supplied payload inconsistent with instantiated `T` | Reject at construction. |
| **VA-004** | P0 | C+R | Exhaustive match on `Option[Int]` with `Some` and `None` | Correct branch and bound `Int` payload. |
| **VA-005** | P0 | C+R | Nested `Option[Option[Int]]` | One and two absence levels distinguished. |
| **VA-006** | P0 | C+R | Generic variant member has two distinct type parameters | Destructuring preserves each bound type. |
| **VA-007** | P0 | C+R | Generic enum with value type `String` | All values read with declared type. |
| **VA-008** | P0 | C− | Generic enum member value incompatible with declared generic type | Reject member definition. |
| **VA-009** | P1 | C+R | Generic variant and a union of differently instantiated variants | Correct branch identity and legal union normalization. |
| **VA-010** | P1 | C+R | Generic variant in a collection where element type is a supertype | Runtime member evidence retained. |
| **VA-011** | P1 | C+R | Variant generic parameter occurring in immutable readable positions | Infer variance only where declaration actually permits. |
| **VA-012** | P1 | C− | Non-generic enum member incorrectly assigned a stored value | Reject per enum rule. |
| **VA-013** | P1 | C− | Generic enum member omitted required value | Reject per generic enum rule. |
| **VA-014** | P1 | C+R | `Result[T,E]` generic result with disjoint success/error payloads | Preserve `T` and `E`, no accidental swapping or flattening. |
| **VA-015** | P2 | P | Construct same generic variant across modules and pattern-match after re-export | Same type identity and payload interpretation. |

## RK. Ranks, vectorisation, rugged lists and generic lists

**Design basis:** §§2.1, 7, 7.2–7.4, 16.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **RK-001** | P0 | C+R | Generic list identity of `T+` | Exact rank and item type retained for rank-1 list. |
| **RK-002** | P0 | C+R | Generic list identity with `T+2` | Rank-two exact shape retained without vectorizing accidentally. |
| **RK-003** | P0 | C+R | Generic scalar element called on `T+` list | Vectorises when allowed; result rank reflects input rank. |
| **RK-004** | P0 | C+R | Generic element on two equal-length lists | Pairwise zipper order and output values correct. |
| **RK-005** | P0 | F | Generic vectorisation on mismatched lengths | Runtime `VectorisationFault`, not compile-time inferred generic type corruption. |
| **RK-006** | P0 | F | Generic vectorisation on nested lists with inner length mismatch | Fault on mismatched inner dimension rather than silent truncation. |
| **RK-007** | P0 | C+R | Generic `T novec` list argument and scalar second argument | Whole list passed as one value. |
| **RK-008** | P0 | C+R | Generic element with one `novec` parameter and one vectorising parameter | Novec parameter broadcast unchanged for each iteration. |
| **RK-009** | P0 | C− | Generic exact-rank parameter attempts to accept incompatible rugged prefix | Reject; rugged cannot be peeled to satisfy uniform rank. |
| **RK-010** | P0 | C+R | `T~2` argument admitted where `T~1` required | Minimum rugged rank subtype relation holds. |
| **RK-011** | P0 | C+R | `T*3` argument admitted where `T*2` required | Minimum rank subtype relation holds. |
| **RK-012** | P0 | C+R | `^+2` applied to generic atomic and ranked values | Correct type transformation and runtime wrapping only when required. |
| **RK-013** | P1 | C− | Generic `^+` on empty implicit stack | Reject instead of inventing unspecified generic rank. |
| **RK-014** | P1 | C+R | `T+3` and `T+++` type expressions | Same canonical signature and inference. |
| **RK-015** | P1 | C+R | Generic list-of-union payload `T\|U` and union-of-list payload | Do not conflate distinct structural forms. |
| **RK-016** | P1 | C+R | Generic element plus `extend(default)` fills a missing vector cell | Default evaluated once and checked against generic scalar parameter. |
| **RK-017** | P1 | C− | `extend(default)` generic fallback returns list where scalar required | Rejected rather than additional implicit vectorisation. |
| **RK-018** | P1 | C+R | Generic `map` or `reduce` over zero-length typed list | Correct empty handling according to operation and no unresolved type. |
| **RK-019** | P1 | C+R | Generic vectorisation and nested tuple return | All return positions have correct type/rank. |
| **RK-020** | P2 | P | Transpose legal independent dimensions / adjust input shapes | Generic mapping behaves like scalar reference implementation. |

## FV. Generic function values, higher-order types and closures

**Design basis:** §§6, 6.3–6.6, 16.3.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **FV-001** | P0 | C+R | `Function[T->T]` argument with `T=Int` and `T=String` | Callable accepts and returns the corresponding concrete type. |
| **FV-002** | P0 | C− | `Function[T->T]` passed a callable whose output is unrelated to its input | Reject the broken correlation. |
| **FV-003** | P0 | C+R | Generic `map[T,U](T+,Function[T->U])` | `U` inferred from callable return type and resulting list type is `U+`. |
| **FV-004** | P0 | C+R | Generic `fold[T,U]` with accumulator type distinct from item type | Accumulator type stays `U`; no false requirement `T=U`. |
| **FV-005** | P0 | C+R | Generic function value returned from another generic function | Retains its own environment and inferred signature. |
| **FV-006** | P0 | C+R | Two different generic function values passed through same wrapper | Each call-site instance checked and dispatched independently. |
| **FV-007** | P0 | C+R | Anonymous generic `Function[@1->@1]` embedded in another function signature | Index denotes same binder only within proper scope. |
| **FV-008** | P0 | C+R | Callable with zero arity and generic result stored as a value | No dummy argument inserted and correct result type. |
| **FV-009** | P0 | C+R | Callable with multiple generic return values | Output ordering, arity and multiplicity correct. |
| **FV-010** | P0 | C+R | Generic function is called via ECS variable invocation | Arguments and substitutions same as `call` form. |
| **FV-011** | P1 | C+R | Generic closure captures concrete value of type `T` | Capture restored for each call without changing generic binding. |
| **FV-012** | P1 | C+R | Two closures captured from different generic instantiations | No shared captured environment or code specialization collision. |
| **FV-013** | P1 | C− | Generic closure illegally captures a top-level assigned variable | Reject according to top-level capture rule. |
| **FV-014** | P1 | C+R | Generic function closure mutates local copy of captured variable | Each call starts from capture-time value. |
| **FV-015** | P1 | C+R | Generic callable passed into `both` with multi-value return | Both result groups preserve order and generic types. |
| **FV-016** | P1 | C+R | Generic callable pair passed to `sequence` with differing arity | Return groups and parameter substitutions remain separate. |
| **FV-017** | P2 | P | Transform direct generic call into wrapper/closure call | Same output type, value and effects. |

## CS. Call-site type checking, variadic tuple patterns

**Design basis:** §§1.3.1, 6.6, 6.6.1.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **CS-001** | P0 | C+R | CSTC function takes `Function` value of arity 1 then arity 2 | Each call independently computes concrete stack effect. |
| **CS-002** | P0 | C+R | CSTC same wrapper takes callable producing 0 then 2 outputs | Arity/multiplicity rechecked at each use. |
| **CS-003** | P0 | C− | Call-site callable requires more values than caller stack provides | Reject rather than reading beyond physical stack. |
| **CS-004** | P0 | C+R | `{T...}` repeated tuple parameter matches zero elements | Zero-length case accepted when pattern permits and callable semantics allow. |
| **CS-005** | P0 | C+R | `{T...}` repeated tuple matches several items | All items checked against consistent repeated type. |
| **CS-006** | P0 | C+R | `{T,U...,V}` matches zero repeated segment | Fixed ends remain correctly bound. |
| **CS-007** | P0 | C+R | `{T,U...,V}` matches 3 repeated middle items | Correct split of tuple into fixed/repeated/fixed parts. |
| **CS-008** | P0 | C+R | `{Number...,String...}` matches both repeated segments | Boundary selected soundly for allowed tuple content. |
| **CS-009** | P0 | C− | Variadic tuple parameter type used in a non-parameter type position | Reject per placement restriction. |
| **CS-010** | P0 | C− | Variadic tuple passed where a fixed tuple is required | Reject unless separately instantiated as a fixed tuple. |
| **CS-011** | P0 | C+R | Fixed tuple passed to matching variadic tuple parameter | Correct element-wise type checking. |
| **CS-012** | P1 | C+R | CSTC wrapper using `@@tupled` to gather unknown multiplicity | Return tuple size matches concrete callable outputs. |
| **CS-013** | P1 | C+R | Generic CSTC wrapper invoked once per tuple shape | No first-call type or arity contamination. |
| **CS-014** | P1 | C+R | `both` with generic zero-argument callable | Callable invoked twice; effect count correct. |
| **CS-015** | P1 | C+R | `sequence` with mixed generic callable arities | Lower/upper input grouping and output order correct. |
| **CS-016** | P2 | P | Repeat CSTC calls with each shape in reverse order | Independence of cached stack-polymorphic analysis. |

## WH. `where` static evaluation, ranks, overload rejection

**Design basis:** §22; §§6.6, 16.2.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **WH-001** | P0 | C+R | Generic rank-return `T+$n` where `$n` is assigned from `length $shape` | Correct return rank and concrete return type. |
| **WH-002** | P0 | C− | Output rank variable never assigned in `where` | Mandatory-write diagnostic before runtime. |
| **WH-003** | P0 | C+R | Input rank `T+$n` read in `where` | Bound exact list rank visible as number in static expressions. |
| **WH-004** | P0 | C− | Attempt write to read-only input rank variable in `where` | Reject mutation of parameter rank evidence. |
| **WH-005** | P0 | C+R | `where` type equality compares `T` and `U` | Correct decision without runtime type erasure. |
| **WH-006** | P0 | C+R | `where` computes `max`, `min`, addition and subtraction of two rank variables | Left-to-right static stack evaluation produces expected result. |
| **WH-007** | P0 | C+R | `where (?)` fails for first generic overload, later overload applies | Candidate rejection not whole-program failure; no corrupted inference state. |
| **WH-008** | P0 | C− | All overloads rejected by `where` assertions | Clean no-applicable-overload diagnostic. |
| **WH-009** | P0 | C+R | `where` uses `$f.inputs`, `$f.outputs`, `$f.arity`, `$f.multiplicity` | Returns exact type/length/count for concrete callable. |
| **WH-010** | P0 | C+R | `where` calculated value referenced in body | Hidden numeric/static value carried correctly into generated runtime code. |
| **WH-011** | P0 | C− | `where` calls arbitrary user element | Reject restricted static evaluator call, even if element is pure. |
| **WH-012** | P0 | C− | `where` calls namespaced / annotated / disambiguated / placeholder expression | Reject unsupported static-call syntax. |
| **WH-013** | P0 | C− | `where` tries loop or recursion | Reject rather than executing indefinitely. |
| **WH-014** | P0 | C− | `where` attempts optional-safe field access | Reject unsupported operation. |
| **WH-015** | P0 | C− | `where` static type literal uses `Result[T,E]` | Reject per static literal restriction. |
| **WH-016** | P1 | C+R | `where` asserts false for one generic type instantiation but true for another | Outcomes independent and cache keys include instantiated type/rank. |
| **WH-017** | P1 | C+R | `where` rank computed for empty shape tuple | Handle zero according to return rank validity; no invalid negative or zero rank in successful type. |
| **WH-018** | P1 | C+R | `where` accesses types via `.inputs` then compares equal | Uses type identity, not stringified name. |
| **WH-019** | P1 | C− | Invalid static arithmetic operand type | Clean compile diagnostic; no evaluator VM crash. |
| **WH-020** | P2 | P | Reorder independent `where` computations while preserving dependencies | Same inferred return type and selected overload. |

## UC. Union-covered generic overloaded callable plans

**Design basis:** §6.5; §§2.5, 16, 17.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **UC-001** | P0 | C+R | Generic overloaded callable covers `Int\|String` input union | Every branch covered; output union is selected overload result union. |
| **UC-002** | P0 | C− | One union branch lacks matching overload | Reject entire callable coercion, no partial runtime plan. |
| **UC-003** | P0 | C− | Two runtime-overlapping branches select different overloads | Reject ambiguous union coverage. |
| **UC-004** | P0 | C+R | Union branch statically selects broad numeric overload | Runtime narrower subtype does not unexpectedly re-resolve overload. |
| **UC-005** | P0 | C+R | Union branch using generic nominal variance | Runtime reified branch recognizes assignable instantiated object type. |
| **UC-006** | P0 | C+R | Union branch using named trait evidence | Branch predicate accepts implementing values and calls prechosen overload. |
| **UC-007** | P0 | C+R | Union branch uses tagged type predicate | Required tag evidence tested at runtime. |
| **UC-008** | P0 | C+R | Union parameter includes `Some[T]\|None` | Branch plan respects optional normalization and `None` distinction. |
| **UC-009** | P0 | C+R | Generic function value constructed before adding unrelated overload | Saved plan remains the type-checker's intended plan. |
| **UC-010** | P0 | C+R | Generic union branch plan serialized and restored where bytecode supported | Runtime invokes same overload after reload. |
| **UC-011** | P1 | C+R | Union branches supplied in alternative source order | Normalized plan returns same observable results. |
| **UC-012** | P1 | C+R | Union of two instances of same generic constructor | Runtime dispatch distinguishes type arguments if required by selected branch predicates. |
| **UC-013** | P1 | C+R | Union-covered callable mapped over heterogeneously typed list | Every list item takes planned branch; output values and inferred union correct. |
| **UC-014** | P1 | C− | Generic candidate becomes ambiguous only for one branch | Reject entire union-covered function. |
| **UC-015** | P1 | C+R | Generic union-covered function passed through wrapper | Precomputed plan retained after indirect call. |
| **UC-016** | P2 | P | Compare planned union invocation with explicit `match` branch calls | Equivalent outputs for every allowed branch. |

## TG. Generic data tags and tag reification

**Design basis:** §§17, 17.4–17.11, 16.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **TG-001** | P0 | C+R | Constructed-tagged generic input flows through identity function | Tag remains when output rank supports its effective depth. |
| **TG-002** | P0 | C+R | Constructed tag through generic vectorized transformation | Propagates under sticky policy at the correct depth. |
| **TG-003** | P0 | C+R | Computed tag through generic operation without explicit retention | Tag forgotten as intended. |
| **TG-004** | P0 | C+R | Unit tag in generic parameter requiring that unit | Accepted and correct value retained under unit-aware operation. |
| **TG-005** | P0 | C− | Unit-tagged scalar passed to generic element accepting bare scalar only | Reject implicit loss of unit. |
| **TG-006** | P0 | C+R | Generic return explicitly declares unit tag | Runtime evidence and validator preserved on successful result. |
| **TG-007** | P0 | F | Generic operation returns invalid tagged value under validator | Validator raises proper failure without committing invalid tag. |
| **TG-008** | P0 | C− | `#-tag` generic parameter receives value with present tag | Reject before erasable tag loss. |
| **TG-009** | P0 | C+R | `[#tag] T` exact present-tag-set constraint | Only exactly matching compile-time set allowed. |
| **TG-010** | P0 | C+R | Two disjoint constructed tags provided left-to-right | Later tag wins according to propagation order. |
| **TG-011** | P1 | C+R | Variant-tagged generic value matched by variant at runtime | Parent compile-time tag and variant runtime evidence remain consistent. |
| **TG-012** | P1 | C+R | Generic tagged list indexed once | Tag depth decreases one; generic result type updated. |
| **TG-013** | P1 | C+R | Generic tagged list sliced | Tag depth remains as collection rank preserved. |
| **TG-014** | P1 | C+R | Generic tag overlay on parameterized operation | Overlay's type substitutions and validation are correct. |
| **TG-015** | P1 | C− | Overlay generic arity disagrees with element generic arity | Reject matching overlay declaration. |
| **TG-016** | P2 | P | Compare generic tag flow via direct call and callable value | Same compile-time contract and runtime evidence. |

## IM. Modules, separate compilation and generic visibility

**Design basis:** §§23.6–23.15; §16.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **IM-001** | P0 | C+R | Import a specific generic overload using `hash[T](T+)` selector | Only requested overload visible, with correct generic binder. |
| **IM-002** | P0 | C− | Import generic overload using wildcard `_` in signature | Reject wildcard selector as specified. |
| **IM-003** | P0 | C− | Import generic overload with wrong declared arity | No-such-overload error; never silently choose another. |
| **IM-004** | P0 | C+R | Generic object imported and instantiated in another module | Type parameters reified, correct public field access and value. |
| **IM-005** | P0 | C+R | Generic trait relationship exported from module and imported explicitly | Full generic argument correlation maintained. |
| **IM-006** | P0 | C− | Two imported modules provide identical generic element signature | Import conflict detected; no first-import winner. |
| **IM-007** | P0 | C+R | Conflicting generic overload imports disambiguated with selective imports or exclusion | Only selected overloads remain visible. |
| **IM-008** | P0 | C+R | Generic symbol re-exported through public import | Re-export uses same originating canonical identity. |
| **IM-009** | P0 | C+R | Two modules define unrelated generic `Box[T]` types | They are not conflated because names and shapes match. |
| **IM-010** | P1 | C− | Private generic declaration imported outside module | Reject visibility violation. |
| **IM-011** | P1 | C+R | Generic overload imported with alias | Alias changes spelling, not identity or substitution. |
| **IM-012** | P1 | C+R | Generic overload imported inside block | Visible only in block; outside use resolves independently or fails. |
| **IM-013** | P1 | C+R | Circular import among generic declarations without execution | Terminates and resolves symbols correctly where legal. |
| **IM-014** | P1 | C+R | Compile dependency and consuming module separately | Same instantiated type identity as single compilation. |
| **IM-015** | P2 | P | Reorder independent imports and source-file discovery | Same ambiguity/selection and type identity. |
| **IM-016** | P2 | P | Change generic exported signature and incremental rebuild consumer | Consumer reanalyzed and stale instantiation invalidated. |

## RT. Runtime specialization, representations and code generation

**Design basis:** §§16, 12, 6.5; §7; §20.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **RT-001** | P0 | R | Same generic identity called with `Int`, `String`, object and list | Runtime representation and result correct for each type, no erasure. |
| **RT-002** | P0 | R | Alternate `id[Int]`, `id[String]`, `id[Int]` calls | No specialization-cache entry reused with wrong type. |
| **RT-003** | P0 | R | Generic function return `T+` with two ranks arriving through valid binding | Correct result stack shape and list rank. |
| **RT-004** | P0 | R | Generic object fields of distinct layouts and types | No wrong field offsets or swapped fields in generated code. |
| **RT-005** | P0 | R | Nested generic object `Box[Box[T]]` read/written through reconstruction | Correct nested payload after updates. |
| **RT-006** | P0 | R | Generic function with two outputs of different concrete types | Correct ordering, conversion and VM stack effects. |
| **RT-007** | P0 | R | Indirect generic function call from callable value | Same specialization and calling convention as direct call. |
| **RT-008** | P0 | R | Generic method on generic owner with explicit `$self` receiver | Actual instantiated owner representation passed correctly. |
| **RT-009** | P0 | R | Generic overload invoked after runtime `as?` refinement | Result uses statically refined type and correct runtime payload. |
| **RT-010** | P0 | R | Runtime concrete input prevents optimizer folding generic call | Real call path executes and produces correct result. |
| **RT-011** | P0 | R | Generic overload plan for union runtime input | Preselected branch dispatch and payload interpretation correct. |
| **RT-012** | P1 | R | Generic vectorised call on empty and large lists | Zero/large iterations handled without stale element type. |
| **RT-013** | P1 | R | Generic function invoked from callback / asynchronous task when supported | Correct captured type and native/VM calling convention. |
| **RT-014** | P1 | R | Generic value copied/moved on ordinary immutable object semantics | No accidental alias to separate mutable variable store. |
| **RT-015** | P1 | R | Generic call with 0, 1, 2 and many generic arguments | Stable calling convention / type metadata allocation. |
| **RT-016** | P1 | R | Generic function containing `try/handle` branches | All valid exits preserve output type and stack effect. |
| **RT-017** | P2 | P | Run runtime generic cases with interpreter, serialized bytecode and optimized backend if available | Same observable results and faults. |
| **RT-018** | P2 | P | Run runtime generic tests with memory checker / ASAN where available | No invalid object layouts or memory accesses. |

## EC. Errors, optionals, returns and generic flow

**Design basis:** §§2.5, 6, 21.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **EC-001** | P0 | C+R | Generic identity accepting `T?` with `Some[Int]` | Returns correct optional type and value. |
| **EC-002** | P0 | C+R | Generic identity accepting `T?` with `None` from annotated context | Returns absence, no invented inner type. |
| **EC-003** | P0 | C+R | Nested optional `Some[None]` through generic function | Preserves distinction from plain `None`. |
| **EC-004** | P0 | C+R | `Result[T,E]` generic propagation through `&` success path | Maps `T` to `U` and retains error `E`. |
| **EC-005** | P0 | C+R | `Result[T,E]` generic propagation through `&` error path | Carries original error without calling success mapping function. |
| **EC-006** | P0 | C+R | Union `T\|E` simplified to `Result[T,E]` where E is Err | Correct branch and generic parameter assignment. |
| **EC-007** | P0 | C− | Two incompatible explicit return paths of generic function | Fail multiplicity/type consistency at compilation. |
| **EC-008** | P0 | C+R | Generic function early `return` with specified type | Correct value and no hidden stack leftovers. |
| **EC-009** | P0 | C+R | Generic function returns tuple of multiple heterogeneous type variables | Each output slot retains its associated binding. |
| **EC-010** | P1 | C+R | Generic function uses `@@tupled` over multiple outputs | Final tuple type contains substituted concrete outputs in order. |
| **EC-011** | P1 | C+R | Generic closure throws failure caught by generic wrapper | Handler output matches declared generic return type. |
| **EC-012** | P1 | C− | Generic result branch typed only as error type | Do not simplify to a nonexistent success `Result[T,E]`. |
| **EC-013** | P1 | C+R | `T??` and shorthand `T?2` | Equivalent type identity and runtime representation. |
| **EC-014** | P2 | P | Commutatively reorder compatible union arms within optional/result output | Canonical type and error behavior stable. |

## IX. Generic indexing, immutable updates and structured data

**Design basis:** §§1.4, 9.0–9.1, 12.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **IX-001** | P0 | C+R | Generic `@index(Box[T])` provider returns `T` | Concrete selector and return type preserved across T instantiations. |
| **IX-002** | P0 | C− | Custom generic index provider receiver is not normalized-equal to annotated target | Reject invalid provider declaration. |
| **IX-003** | P0 | C+R | Generic `@update(Box[T])` provider returns updated `Box[T]` | Type preserved and new object reconstructed. |
| **IX-004** | P0 | C− | Generic `@update` returns wrong generic owner type | Reject mismatched receiver replacement type. |
| **IX-005** | P0 | C+R | Generic index provider on named trait | Resolved provider uses correct instantiated trait arguments. |
| **IX-006** | P0 | C− | Generic index provider attempts to replace intrinsic list/tuple indexing | Sealed intrinsic provider cannot be overridden. |
| **IX-007** | P0 | C+R | Generic record `record(.x:T,.y:U)` update | Reconstruct record with correct non-updated generic fields. |
| **IX-008** | P0 | C+R | Generic dictionary `Dict[T,U]` lookup | Retains key type T and value type U without mixing slots. |
| **IX-009** | P1 | C+R | Multiple custom indexes on generic receiver | Each selector independently resolved and result list has correct type. |
| **IX-010** | P1 | C+R | Generic list indexed by runtime integer | Value type is generic leaf type; bounds/fault semantics unchanged. |
| **IX-011** | P1 | C+R | Generic tuple indexed by constant literal | Return type narrowed to exact tuple member. |
| **IX-012** | P1 | C+R | Generic tuple indexed by variable runtime integer | Return type union of tuple item types, preserving alternatives. |
| **IX-013** | P1 | C+R | Generic object member reconstruction inside another generic object | Nested update does not mutate original immutable value. |
| **IX-014** | P2 | P | Compare generic indexed read/write against equivalent hand-written field/path access | Same values and inferred types. |

## CX. Stress, cache invalidation, deterministic analysis and recovery

**Design basis:** §§6, 11, 16, 22, 23 (compiler robustness tests).

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **CX-001** | P0 | P | Instantiate generic `F` for A then B then A in same process | A specialization unchanged, B independent. |
| **CX-002** | P0 | P | Reverse order B then A then B | Results/diagnostics identical after ignoring order of unrelated output. |
| **CX-003** | P0 | P | Failed generic overload candidate followed by successful candidate | Inference environment rolled back fully. |
| **CX-004** | P0 | P | Compile invalid generic source then valid generic source in one compiler process | No poisoned symbol table or incremental cache. |
| **CX-005** | P0 | P | Recompile same generic program in same process 50 times | Stable types, diagnostics and runtime outputs. |
| **CX-006** | P0 | P | Compile clean then incremental after changing generic function return type | All affected users invalidate and re-check. |
| **CX-007** | P0 | P | Change field variance in generic object and rebuild dependents | Previously valid/invalid assignments re-evaluated. |
| **CX-008** | P0 | P | Change generic trait relation and rebuild caller | Stale evidence / method resolution invalidated. |
| **CX-009** | P0 | P | Change generic overload `where` assertion and rebuild | Cached prior applicability not reused. |
| **CX-010** | P0 | P | Generate many `T+N` cases N=1..20 | Termination and exact rank inference; no integer overflow. |
| **CX-011** | P0 | P | Generate nested `Box[...Box[T]]` up to bounded depth | Solver terminates and memory remains bounded. |
| **CX-012** | P1 | P | Generate 50 valid distinct concrete instantiations of same generic function | No shared-entry contamination or pathological time blowup. |
| **CX-013** | P1 | P | Generate many inapplicable generic candidates before applicable overload | No exponential blowup, no side effects from rejected candidates. |
| **CX-014** | P1 | P | Compile disjoint generic sources in parallel compiler sessions | No non-deterministic type identities in caches/build artifacts. |
| **CX-015** | P1 | P | Compare inferred types in clean and cached build | Identical normalized types and diagnostics. |
| **CX-016** | P1 | P | Delete generated artifacts and rebuild from scratch | Same semantic result (not necessarily byte-for-byte build IDs). |
| **CX-017** | P2 | P | Fuzz malformed nested type brackets/type expressions | Compiler always terminates with error, never crashes. |
| **CX-018** | P2 | P | Shrink any failing generated program to minimal generic/overload fixture | Regression reproducible with deterministic seed. |

## MT. Metamorphic, differential and mutation-based tests

**Design basis:** §§4.2, 6.5, 7, 16, 22.

| ID | Priority | Oracle | Scenario | Expected behavior / bug detected |
|---|---|---|---|---|
| **MT-001** | P0 | P | Substitute `T` consistently with fresh name `U` | No change to well-typedness/results. |
| **MT-002** | P0 | P | Replace inferred generic call with equivalent explicit type arguments | Same type and output. |
| **MT-003** | P0 | P | Wrap generic call with identity function value | Same value and effects. |
| **MT-004** | P0 | P | Replace eligible direct generic call with ECS call | Same binding, stack result and output. |
| **MT-005** | P0 | P | Replace higher-rank call with element-wise scalar reference implementation | Same vectorized values, rank and faults. |
| **MT-006** | P0 | P | Replace resolved generic overload with explicit candidate hint | Same selected implementation when hint names same candidate. |
| **MT-007** | P0 | P | Reverse independent generic instantiation order | No change in outcomes. |
| **MT-008** | P0 | P | Compare compile-time computed return rank with actual runtime list rank | Equal for every successfully compiled fixture. |
| **MT-009** | P0 | P | Compare generic helper and manually specialized concrete helper | Identical observable behavior for permitted inputs. |
| **MT-010** | P1 | P | Reorder disjoint overload declarations | Same selection and ambiguity outcomes. |
| **MT-011** | P1 | P | Rename module aliases without changing underlying import | Same generic types and specialization. |
| **MT-012** | P1 | P | Use equivalent exact-rank type spellings (`T+++`, `T+3`) | Same canonical type and overload choice. |
| **MT-013** | P1 | P | Use equivalent optional spellings (`T??`, `T?2`) | Same normalized type and runtime meaning. |
| **MT-014** | P1 | P | Compare serialized/reloaded generic runtime plan against fresh plan | Equivalent branch selection and values. |
| **MT-015** | P2 | P | Mutate one generic signature to break constraint and verify test fails | Ensures assertions actually detect invalid specialization. |
| **MT-016** | P2 | P | Run property tests with fixed recorded seeds and shrink failing cases | Deterministic, actionable regressions. |

## Reference fixtures — illustrative Valiance-style programs

The fixtures below are **starting points**, not a claim that every exact spelling has already been parsed by your implementation. Ensure the test runner checks *static type* where indicated, not just stdout. Where a fixture intentionally contains two different expected outcomes, split it into separate source files.

### F01 — Repeated generic instantiation (`GD-001`, `RT-002`, `CX-001`)

```valiance
define[T] keep(value: T) -> T => $value end

keep[Int](11) println       #? 11
keep[String]("hello") println #? hello
keep[Int](37) println       #? 37
```

Oracle: all three values are correct and the first `Int` specialization is not corrupted by the intervening `String` call. Run B–A–B too. Avoid treating stdout correctness alone as proof of correct inferred types.

### F02 — Two independent generic binders (`GD-002`, `UN-001`)

```valiance
define[T, U] left(a: T, b: U) -> T => $a end

left[Int, String](4, "no") println #? 4
left[String, Int]("yes", 5) println #? yes
```

Oracle: output types `Int` then `String`, with no accidental swap of `T` and `U`.

### F03 — Exact-rank candidate filtering (`CP-005`, `CP-006`)

```valiance
define[T] rankFour(values: T+4 exact) -> T+4 => $values end
```

Feed: one exact rank-4 homogeneous numeric list (compile success, unchanged), one rank-3 list (compile fail), one rank-5 list (compile fail). These **must be separate fixture files** to avoid the first error hiding subsequent outcomes.

### F04 — `novec` generic binds whole list (`CP-009`, `RK-007`)

```valiance
$identity = fn[T] (value: T novec) -> T => $value
$identity([1, 2, 3]) #? [1, 2, 3]
```

Oracle: `T` is the whole list type, not an individual numeric item. Compare a vectorising function separately.

### F05 — Exact versus generic overload (`OV-001`)

```valiance
define[T] marker(x: T) -> Int => 100 end
define marker(x: Int) -> Int => 200 end

marker(3) println #? 200
marker("x") println #? 100
```

Oracle: exact concrete overload selected for `Int`, generic otherwise. If your declaration grammar uses another permitted overload order, adjust without changing this semantic assertion.

### F06 — Inferred anonymous generic (`IN-001`, `IN-003`)

```valiance
$f = fn (x) => $x end
$f(42) println
$f("hi") println
```

Oracle: inferred function type contains `Function[@1 -> @1]`, with independent per-call instantiation.

### F07 — Generic row constraint (`IN-006`, `IN-007`)

```valiance
$f = fn[T, U] (x: T(.bar: U)) -> U => $x.bar
$f(record{bar => 31, other => "ok"}) println #? 31
```

Negative sibling: record lacking `bar` must be rejected.

### F08 — Generic object and inferred variance (`OB-001`, `OB-009`, `OB-010`)

```valiance
object[T] Box =>
  $value: T
end

 $b1 = Box(17)
 $b2 = Box("a")
 $b1.value println
 $b2.value println
```

Oracle: exact field types and values; separately add an object with **readable-only** field and one with **public writable** field, then assert covariance versus invariance against a real `Child <: Parent` relationship.

### F09 — Generic nominal trait argument correlation (`TB-002`, `TB-004`)

```valiance
trait[T] Producer => end
trait[T] Iterable => end
trait[T] Producer as Iterable[T] => end
```

Positive sibling: `Producer[Int]`-implementing object is assignable to `Iterable[Int]` when this behavior set is visible. Negative sibling: not assignable to `Iterable[String]`. Include an imported-provider version.

### F10 — Generic map / higher-order type inference (`FV-003`)

```valiance
define[T, U] applyAll(xs: T+, f: Function[T -> U]) -> U+ =>
  $xs map: $f
end

applyAll([1, 2, 3], fn (x: Int) -> String => "$x" end)
#? ["1", "2", "3"]
```

Oracle: `T=Int`, `U=String`, expected `String+` output. Adjust element/fn `map` binding spelling to compiler-accepted form, but preserve the inferred-type assertion.

### F11 — Anonymous function generic, typed callback (`FV-007`)

```valiance
$f: Function[@1 -> @1] = fn (x) => $x end
$f(9) println
$f("X") println
```

Oracle: `@1` is a correlated anonymous generic in this function type, **not** an unbound unrelated identifier; two call sites do not collide.

### F12 — `where` return-rank construction (`WH-001`, `WH-003`)

```valiance
define[T] makeRank(xs: T*, shape: {Number...}) -> T+$n
where ($n = length $shape) => $xs as![T+$n]
```

Oracle: rank computed statically from tuple length; runtime shape agrees with returned type **when the body cast succeeds**. This is schematic, adapted from §22, and its deliberate runtime `as!` may fault if input content does not satisfy the asserted rank. Keep success and fault fixtures separate.

### F13 — Generic overloaded union callable plan (`UC-001`, `UC-004`)

```valiance
$values = [1, 2, "A", "B"]
println($values map: * 2)
#? [2, 4, AA, BB]
```

Oracle: each union branch is covered unambiguously; the recorded branch-to-overload plan is preserved. Repeat via a function-value wrapper and compare after an input with runtime-only data.

### F14 — Generic index provider (`IX-001`, `IX-003`)

```valiance
object[T] Slot =>
  $value: T
end

#? Schematic provider declaration: exact receiver and selector
@index(Slot[T]) define[T] slotAt(receiver: Slot[T], ind: Int) -> T => $receiver.value end
```

Oracle: provider signature normalizes to the exact annotation target, returns `T` for each instantiation. Add a negative provider that returns `U` instead.

### F15 — Scalar/non-scalar failure under `novec` (`CP-010`, `CP-011`)

```valiance
$f = fn (:Number novec) => double
$f(10)       #? 20
#? Separate negative fixture:
$f([1, 2])   #? compile error; cannot vectorise novec
```

Oracle: call policy preserved even through function value.

### F16 — Generic optional depth (`UN-014`, `EC-003`)

```valiance
#? Test the three values/types separately:
#? None : Int?
#? Some(None) : Int??
#? Some(Some(7)) : Int??
```

Oracle: normalization must not flatten nested `Some` or treat `None` and `Some(None)` identically. Use the compiler's actual `Some` constructor/casts.

### F17 — Object type reconstruction is not shared state (`IX-013`)

```valiance
object[T] Cell =>
  public $item: T
end

$a = Cell(5)
$b = $a
$a.item = 8
#? Assert: $a.item == 8 and $b.item == 5
```

Oracle: generic type `T=Int` retained, and mutable local stores refer to separate *immutable values*. **This is not a shared-state test.**

### F18 — General-purpose typed generics across both codegen paths (`RT-001`, `RT-010`)

```valiance
define[T] echo(x: T) -> T => $x end

#? Read values from runtime input / a runtime-computed list, then call echo.
#? Also use an Int instance and a String instance in one program.
```

Oracle: actual runtime execution (not only constant folding) agrees with static type and specialization.

## Additional generator families and shrinking

Generate bounded source families rather than indiscriminately nesting unsupported syntax:

- **Type terms:** `Int`, `Real`, `Number`, `String`, concrete nominal object, `Box[T]`, `T+N`, `T*N`, `T~N`, `T?`, `T??`, `T|U`, tuple, row types, function types, named trait constraints, optional and tags.
- **Candidates:** exact, exact-generic-equivalent, broad generic, exact marker, scalar atomic evidence, novec, rank-vectorising fallback, trait-match, union-match, tied two-argument partial candidates.
- **Calls:** direct stack, ECS, explicit generics `[T]`, partial generics `[T,_]`, disambiguation `{Type,_}`, callable values, higher-order wrapper, variant match, union-covered runtime plan.
- **Valid/invalid pairs:** take one valid fixture and mutate one type argument, rank, tag, return multiplicity, constraint or import; prove the mutated fixture is rejected or faults exactly as declared.
- **Shrinking:** retain the original random seed and source/IR; shrink from nested terms to one type variable, two candidates and one call. Preserve both the failing oracle and any contextual source dependency while minimizing.

## Failure signatures and quick triage

| Symptom | Likely area | Best diagnostic dump |
|---|---|---|
| `T` changes after unrelated instantiation | Generic binder or specialization-cache key | Canonical binder IDs, substitutions, instantiation keys |
| Rank one too high/low | Residual-rank unification or vectorisation | Input type, candidate type, solved T, residual rank |
| Inapplicable candidate changes later selection | Solver transaction rollback | Snapshot before/after each candidate attempt |
| `exact` accepts higher-ranked list | Candidate filtering order | Exact-shape check before unification |
| Call through value differs from direct call | Call-policy erasure / indirect signature | Retained `exact`/`novec` metadata, callsite specialization |
| `where` result differs by compilation order | Static query cache or scope leakage | Evaluator input types, ranks, symbol environment |
| Generic object variance accepts unsafe write | Polarity analysis | Per-occurrence variance trace, writable field flags |
| Union callable chooses a new runtime overload | Wrong runtime branch-plan semantics | Preselected overload IDs and branch predicates |
| Result wrong only after incremental build | Missed dependency invalidation | Query keys, dependency edges, changed signature |
| Runtime wrong but static inferred type correct | Codegen representation / ABI | Instantiated IR, runtime type metadata and call site |

## Suggested CI gates

1. **Commit gate:** all P0 tests; compile-invalid tests must not crash; run a small runtime matrix with at least two generic type instantiations.
2. **PR gate:** P0 and P1, all `where` candidate rejection and inference rollback checks, direct vs indirect call comparison, clean + incremental compilation, debug + optimized execution where available.
3. **Nightly:** P2, bounded fuzzing, broad rank matrix, 50+ instantiations and compiler process stress, bytecode reload, sanitizer runs and performance threshold monitoring.
4. **Before release:** run every test using a fixed seed set, verify golden diagnostics, rebuild tests from scratch, confirm no shared-state-specific cases are enabled, inspect any `[Decision]` cases.

## Questions the design itself leaves unsettled

The following are **not** automatically compiler defects. They need explicit semantics if they are not already settled by implementation:

1. **Unification/combine formulas:** §16.2 itself says they are subject to determination. In particular, how rank subtraction, generic absorption, and combining incompatible concrete instances should interact with vectorisation.
2. **Bound syntax on generic objects:** §12 mentions bounds in object generic lists, while §16 says generic parameter lists contain only names. Decide and test one rule (or document the grammar distinction).
3. **`atomic` spelling and exact policy placement:** §16.2 documents semantics but does not provide a concrete accepted syntax example; verify the token order before executable golden fixtures.
4. **Unconstrained return-only generics:** The document does not fully spell out inference failure versus mandatory explicit type argument when `T` only appears in result types.
5. **Generic variance inference through deeply nested function/union/row types:** Core variance rules are defined, but complete higher-order polarity semantics and corner-case normalizations need explicit acceptance tests.
6. **Variadic tuple pattern matching with multiple repeated segments:** The document permits the pattern, but ambiguous partition resolution is not fully detailed. Add deterministic success/failure rules.
7. **Generic result signatures involving default optional `None` values:** Exact type inference from absence-only expressions may require expected-type contexts; test whichever inference/defaulting policy the compiler supports.
8. **Relationship between generic static specialization and `multi` dispatch:** They are two different systems; when both apply to a call, write down and test precedence of exact static specialization versus runtime multimethod dispatch.
9. **Expected rank-zero result from `where`:** Return-rank grammar requires positive ranks; make behavior of a zero-length `shape` tuple explicit (different overload, reject, or carefully typed scalar path).

**Scope reminder:** Shared-state-object semantics in §12.8 are intentionally excluded, as requested.
