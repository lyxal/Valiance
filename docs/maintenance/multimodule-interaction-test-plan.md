# Cross-feature, multi-module verification plan

**Scope:** full compiler pipeline for projects containing two or more modules,
including separately built interfaces, private implementation dependencies,
public re-exports, incremental rebuilds, and persisted bytecode. This plan
complements [the basic project matrix](multimodule-project-testing.md) and
existing [type](../Valiance_Type_System_Test_Plan.md),
[generic](../Valiance_Generics_Comptime_Runtime_Test_Plan.md), and
[trait](../Valiance_Trait_Comptime_Runtime_Test_Plan.md) plans.

**Important limit:** Valiance accepts arbitrarily large programs, nested
expressions, dependency graphs, generic instantiations, numeric values, and
execution schedules. **No finite suite can exhaust every possible language
interaction.** “Exhaustive” below means *all members of a specified, bounded
and machine-enumerated testing universe*. Claims of a verified semantic
interaction require an executed oracle, not merely the existence of a fixture.

## Current executable contract registry

The active catalog contains **172 connected families**: 11 original templates,
30 established contracts in `tools/multimodule_additional_contracts.py`,
42 compositions in `tools/multimodule_composition_contracts.py`,
28 state/borrowing/task/channel compositions in
`tools/multimodule_lifetime_concurrency_contracts.py`,
36 cross-module control-flow, generic and borrowing contracts in
`tools/multimodule_deep_contracts.py`, and 25 reflection, JSON, tagging
and task contracts in `tools/multimodule_boundary_contracts.py`.
Each fixture moves a value or a type through declarations owned by two
modules. It is checked against an independent seed-derived oracle and can
be replayed across all supported import graphs and dependency artifact layouts.

- 172 × 6 dependency graphs × 5 layouts = **5,160 finite positive configurations**,
  each with four execution modes = **20,640 potential positive checks**.
- 170 semantic negative contracts × 6 graphs × 5 layouts = **5,100 finite
  invalid configurations**, each requiring a specific diagnostic class.
- **565 of the 1,830 language-axis pairs** have a connected executable fixture;
  **1,265 remain without one**. The 61 new semantic families add 86
  previously unrepresented pairs to the previous 479. Counts represent defined
  independent contracts, not certification of every project layout.

The previously added 42 compositions exercise variant case values in generic boxes, variants,
lists, records, dictionaries, and tuples; variant values transported through
objects, options, collections and mutation; inherited defaults through variants;
trait dispatch from collections, higher-order callbacks and optional values;
union/cast refinements; distinct generic specialisations and overloaded calls;
control-flow, exception, JSON, reflection, and lazy-list paths; and
constructed/computed/unit tags through data structures and generic boundaries.
Every new family also has a separately specified invalid-program diagnostic
contract. The grammar and expected values are independent of VM output.


### Ownership, borrowing, and cooperative concurrency contracts

The 28 contracts in `tools/multimodule_lifetime_concurrency_contracts.py`
verify imported objects reconstructed after mutation, trait-mediated copy
operations, generic ownership forwarding, stack manipulation, spawned calls
and named `@nodup` resources. Five channel contracts check the exact
`Receive.Value` protocol and *nominal payload type and fields* for generic
objects, traits, enums, variants and generic scalars; checks use a
seed-derived independent Python oracle, not a second compiler run.

Six no-duplicate resource cases must lower to two `LOAD_VAR_BORROW` reads
of the same named owner, and must succeed without creating a second owner.
The ordinary four VM combinations and all six graph shapes remain part of
the same generator, so compiled imports cannot silently bypass these checks.
An invalid contract for every positive family checks the designated
diagnostic class. No channel schedule is claimed as exhaustive: these
fixtures test finite single-threaded/cooperative send or task completion
paths, not all possible instruction interleavings.

**Tracked missing behavior:** destructors defined in a provider module do
not emit their expected cleanup output when the provider value is released
through an imported consumer. Three reproducible expected failures (trait
release, generic forwarding, nested field release) are isolated in
`tests/test_multimodule_known_failures.py` and are not registered as passing
semantic contracts. This must be repaired before any `destructor` pair is
marked fixture-defined. The independent stdout oracle is now supported by
`execute_interaction` for future lifecycle fixes.

### High-risk boundary campaign — 61 new semantic families

New independently specified contracts cover exception recovery around imported
trait/object/variant paths, loop and branch refinement, higher-order generic
forwarding, borrowed resource reads and cooperative tasks. Separate templates
exercise reflection, JSON encoding of imported values, constructed/validated
and overlaid tags, and task results crossing imported nominal boundaries.
Each has a deliberately invalid counterpart with a designated diagnostic.

The **bounded exhaustive channel oracle** (`python -m
 tools.channel_schedule_oracle`) enumerates all 5,040 permutations of seven
operations for each of three capacities (0, 1, 2), giving 15,120 stateful
checks. An independent FIFO oracle verifies registration, cancellation,
closure, wake delivery, and exactly-once operation completion. This is not
exhaustive VM instruction scheduling or native-thread execution.

`tests/test_multimodule_boundary_hardening.py` checks native FFI handle
lease/destroy/rollback/host-exception transitions with a mocked native call,
as well as source and compiled namespace identity, private re-exports,
corrupted interface rejection, and incremental nested-package relinking.
**These checks are tracked separately from source-language pair coverage**;
they do not prove ABI integration against a native library. One package
function-alias signature regression is an expected failure, separate from
previously tracked destructor, transitive JSON, and variant-alias issues.

The 61 new families passed all six source graphs (366 projects / 1,464
execution-mode checks), all 61 nested compiled-only projects (244 checks),
and 61 compiled-facade negative diagnostics in the bounded 2026-11-03 campaign.
The entire 5,160/5,100 matrix and Python 3.14 certification are still unrun.

The isolated slice CLI forwards `--case-timeout` to the per-case executor.
A deadline produces a recorded failure; it is not a skip or pass.

`fixture_defined` does not mean that all associated layouts or execution modes
have been run. The CI scheduler uses `--all`, so an expanding registry does not
leave test cases outside the scheduled matrix. Local results are reported
separately, not mistaken for completed scheduled CI validation.

The earlier 30 contract families cover: inherited trait default with an imported compact variant;
trait-constrained generics; nominal object unions; optional and asserted
casts; higher-order generic object transport; structural record constraints;
records and dictionaries of imported objects; lists and dictionaries of
variants; optional generic objects; tuples; variant branching; loops over
imported object fields; exception handlers; higher-order method calls; unit
and computed variant tags; overloads with trait defaults; trait intersections;
generic list overloads and ranks; overloaded tag validators; constructed tag
overlays; reflected object fields; JSON of imported objects; lazy list mapping
of variants; and list/dictionary mutation of imported objects. Every positive
case has a seed-derived source-independent oracle and is exercised in four VM
modes by `execute_interaction`.

**Confirmed compiler fix:** persisted interfaces can now encode the known
built-in call-site compiler hook via an exact-identity whitelist instead of
rejecting generic higher-order function bodies. Arbitrary functions remain
forbidden, and invalid callback tags are rejected. This changes the analysed
interface schema to 14 and updates module/compiler ABI numbers.

**Module linking regressions:** Private imported destructors must execute at
final release through object fields, trait parameters, generic functions, and
public facade re-exports. Source and `.vbcm` programs exercise all four VM
modes. Import-time overload aliases must rewrite the selected callable contract
alongside the renamed nominal type. Distinct packages retain distinct nominal
origins even when their declarations have the same unqualified name.

**Known unresolved compiler cases:** `MM-LINK-STDJSON-001` — a transitive
`std.json` call in an imported helper fails during runtime function linking.
A renamed object through a facade can also change the runtime receiver name
used for nominal dispatch; an independent lifetime reproducer is kept as an
expected failure. Neither is counted as passing interaction coverage.

## 1. Assurance model and exit gates

Every test has five independently observable dimensions:

1. **Compiler acceptance:** a valid program parses, resolves, type-checks, and
   lowers without an unexpected diagnostic. Invalid input produces the
   *designated diagnostic class*, not an unrelated error or Python exception.
2. **Semantic oracle:** a source-independent expected value, type, visibility
   property, ownership state, or scheduler outcome. No “compare VM to VM” as
   the sole correctness oracle.
3. **Cross-mode agreement:** unoptimized/optimized bytecode, live/serialized
   program, source/compiled/mixed dependencies, and interpreter restarts when
   applicable. Compare against the independent oracle in every mode.
4. **Graph invariance:** moving the same public contract through an alias,
   facade, diamond, or nested import must preserve observable semantics and
   declaration identity. A selective import must *not* expose other overloads.
5. **Lifecycle invariance:** clean builds, repeated builds, changing a body,
   changing an interface, removing a dependency, restoring it, and loading
   corrupt/stale artifacts must either produce fresh correct output or a
   specific controlled failure.

A green case asserts only its particular finite source and observed modes. A
blank coverage cell is **unverified**, not implicitly passing. Compiler
crashes, timeouts, unexpected diagnostics, unexpected exceptions, optimizer
mismatches, nondeterministic outcomes outside the declared schedule model,
and stale success are failures. `xfail` may exist only with an issue identifier,
owner, reproducible input, and explicit expiry; it must not increase the pass
count. There are no implicit skips.

## 2. Implemented connected semantic contracts

`tools/multimodule_interactions.py` creates **two distinct declaration-owner
modules** (`core`, `logic`), plus zero or more public re-export facades.
Unlike two independent expressions in one project, each example transmits a
value, type, or dispatch obligation across feature kinds or module boundaries.

| Connected family | Observable contract (positive oracle) |
| --- | --- |
| `generic_object_variant` | A generic `Box[Int]` is a field in a variant payload defined in a different module; matching reads the original integer |
| `generic_enum_identity` | A literal-backed variant case passes through an imported generic function and retains its value/type |
| `constructed_tag_generic` | A constructed tag survives generic identity and can be explicitly removed |
| `enum_object_field` | A literal-backed variant case initializes an object declared in another module; reading the object returns the independent expected string |
| `trait_variant_dispatch` | An object stored in a separately declared variant can be matched, cast to its imported trait and dynamically dispatched |
| `generic_trait_variant` | A generic object carrying a generic trait implementation passes through a generic variant and returns its typed payload |
| `overload_enum` | An imported enum and overloaded function interact; dispatch distinguishes `Token` from `Int` |
| `overload_generic_object` | Overloads on `Box[Int]` and `Box[String]` select the correct implementation |
| `tag_variant_payload` | Extracting a variant payload, applying and removing an imported computed tag preserves its value |
| `generic_variant_identity` | A generic function transports a generic variant through exhaustive matching |
| `object_tag_result` | An imported object flows to a function declared in another module whose return has a computed tag |

Inputs and source text are seed-dependent; expected outputs are constructed
in Python from the seed. The generator is deterministic and can recreate
exact files from a case identifier. A separate negative oracle registry
requires nine **specific** error classes: generic nominal mismatch, unsupported
overload, absent tag removal, invalid generic variant payload, unknown tag,
invalid variant case, wrong generic-object overload, wrong constructor type,
and incompatible generic-trait cast.

### Exact finite enumeration

| Axis | Values | Cardinality |
| --- | --- | ---: |
| Connected feature flow | 11 original + 30 established + 42 compositions + 28 lifetime/task/channel + 36 deep + 25 boundaries | 172 |
| Import topology | direct, facade, deep facade, diamond, nested, alias | 6 |
| Dependency artifact state | source, compiled, core binary, dependent binary, alternating binaries | 5 |
| Execution mode | optimization off/on × persisted no/yes | 4 |
| **Valid project configurations** | 172 × 6 × 5 | **5,160** |
| **Valid execution checks per seed** | 5,160 × 4 | **20,640** |
| Negative semantic mutations | 9 original + 30 established + 42 compositions + 28 lifetime/task/channel + 36 deep + 25 boundaries | 170 |
| **Negative diagnostic configurations** | 170 × 6 × 5 | **5,100** |

For a two-module direct graph, `alternating_binary` and `core_binary`
currently remove the same source files. They are intentionally distinct
*artifact-selection policies* that diverge on three-or-more-module graphs;
therefore the 5,160 count denotes requested configurations, **not necessarily
5,160 unique file-tree layouts**. Likewise, four modes are repeated checks of
one program, not four independent language interactions.

Existing `tools/multimodule_fuzzing.py` adds the original 19 × 6 independent
feature/graph matrix and 12 separately specified negative fixtures. The two
matrices overlap in semantics, and their case counts should **not** be
presented as unique defect classes or added without a qualifier.

## 3. Language-wide interaction inventory: no hidden gaps

`--inventory` generates a JSON manifest enumerating **every unordered pair**
of the 61 testing axes defined by `LANGUAGE_AXES`. It also records the
**exact 5,160 positive and 5,100 negative case indices** under the scheduled seed,
including their replay parameters. Each pair entry is marked
`fixture_defined` or `needs_semantic_contract` and lists the concrete connected
fixture(s), if any. This is a *planning taxonomy*, not an assertion that every
listed feature is implemented or that unordered co-occurrence is semantically
valid. A connected fixture is only **eligible** for executed verification.

Coverage reports distinguish four states:

- **verified**: a specific input passed its independent oracle in all the
  execution/artifact modes claimed, as evidenced by a dated run report;
- **fixture defined**: a concrete compilable fixture exists, but this exact
  shape/mode may not have been run;
- **needs contract**: there is no executable independent semantic oracle for
  the pair yet; feasibility and both data-flow directions require review;
- **unsupported/not applicable**: language specification expressly rules out
  the operation; retain a *specific negative diagnostic oracle* instead of
  silently removing it from the denominator.

For an interaction `A × B`, prioritize both directions (`A` produces a `B`
input and `B` wraps or returns `A`), *and* distinct representations: nominal
versus structural, public versus private, generic versus specialized, by-value
versus borrowed, and zero/one/multiple runtime values. A pair is not complete
until each applicable direction has been classified as verified, rejected
according to spec, or explicitly untested.

### Language capability axes

The registry spans lexing/parsing/stack evaluation; scalar, tuple, record,
dictionary, option, finite/lazy-list and ranked values; unions/intersections,
casts, inference, higher-order calls and vectorisation; elements, overloads,
generics, constraints and specializations; objects, fields, constructors,
methods, destructors and ownership; traits, defaults, inheritance and dynamic
dispatch; variants, enums, matches, branches and loops; computed,
constructed, unit, overlay and validator tags; reflection/JSON; exceptions,
borrowing, tasks, channels and concurrency; and FFI, packages, namespaces,
visibility, re-exports, bytecode artifacts, incremental builds and diagnostics.

`docs/valiance-feature-checklist.md` remains the authority on implemented,
partial, planned and superseded syntax. A planner must not create a positive
oracle for a feature not supported by the language. Use rejection contracts
for invalid or unsupported syntax instead.

## 4. Systematic higher-order interactions

Pairwise combinatorics are insufficient for architectural bugs. Extend the
connected fixture registry in the following **directed**, not merely
co-occurring, risk tiers:

| Priority | Required three-/four-way flow | Independent assertion |
| --- | --- | --- |
| P0 | public re-export → generic overload → trait dispatch → persisted module | selected specialization and implementation identity remain unchanged |
| P0 | generic object → variant payload → match → ownership teardown | exactly one logically owned live payload; no premature destruction/double release |
| P0 | borrowed object → trait default/override → fault unwinding → source-free import | borrowed receiver remains alive; cleanup occurs once |
| P0 | module alias → conflicting nominal generics → equality/cast → diamond | same provider has one nominal identity; independent providers stay distinct |
| P0 | tag validator → generic call → overload/vectorisation → re-export | validator checked for each required element and checked path |
| P0 | incremental provider edit → compiled facade → generic instance → serialized caller | old specialization is invalidated; value matches new source |
| P0 | source+binary hybrid → direct/indirect recursive import → public/private visibility | private contract never leaks; recursion is resolved or diagnosed by spec |
| P1 | literal-backed variant → payload variant → tagged generic object → reflection/JSON round trip | case/discriminant/payload and runtime type preserved |
| P1 | nested lazy list → generic overload → branch/exception → repeated traversal | lazy replays preserve values/errors and no extra evaluation |
| P1 | task/channel → borrowed trait object → cancellation/exception | no double drops; permitted schedules give modelled results |
| P1 | FFI → object construction → imported destructor → source-free load | ABI and lifetime contract preserved across module boundaries |
| P1 | package dependency → facade → private transitive import → version change | no unauthorized lookup or stale binary linking |
| P2 | deeply nested union/intersection → trait generics → vectorization | type preservation and resource limits under increasing rank |
| P2 | indexed record/dictionary mutation → variant match → captured closure | independent state-machine oracle across module layers |

These P0/P1/P2 rows are **planned, not implemented** unless a linked fixture
is explicitly recorded in the inventory. Do not report them as passes.

### Bounded depth and boundary schedule

For each approved contract, parameterize rather than writing near-identical
examples. Recommended exhaustive small ranges: zero/one/two/three exported
declarations, zero/one/two overloads per name, zero/one/two generic parameters,
zero/one/two type constraints, zero/one/two/three variant arms, zero/one/two
trait parents, module depth 1–5, diamond width 1–3, and 0–3 independent
providers. Add explicit boundary examples for empty/large collections,
Unicode identifiers, missing modules, nested tags, nullary values and
multiple-output stack semantics. Beyond these bounds run seeded coverage-
guided generation with shrinking, rather than claiming an infinite set was
exhausted.

Negative families must mutate **one semantic predicate at a time**: remove
a public export, alter one generic bound, erase one trait implementation,
remove one case from a match, change a variant constructor payload, duplicate
an overload signature, remove a tag import, change a module identity, or
corrupt one serialized checksum. Compare the exact diagnostic **category**
with source, compiled, mixed and incremental inputs. Record any phase that
raises a Python exception instead of reporting a controlled diagnostic.

## 5. Build and graph mutation matrix (planned beyond the current runner)

Each type-driven program should be revisited across:

- **Graph transforms:** rename owner, alias facade, add transparent facade,
  deepen 1→2→3 hops, reorder independent imports, diamond/unify a shared owner,
  split a provider into two, add unrelated provider, and move modules into
  nested/package directories. The output/nominal identity should be invariant
  under the allowed transformations.
- **Visibility transforms:** local/private/public declarations, selective
  import versus wildcard where supported, private dependencies of exported
  callables, an explicitly chosen subset of overloaded signatures, and
  private trait implementations. Require the same rejection boundaries from
  source and bytecode and no visibility change after renaming.
- **Artifact transforms:** all-source, all-compiled, compiled leaf only,
  compiled core only, alternate compiled modules, compiled facades with source
  providers, source facades with compiled providers, verified serialization
  round-trip, and explicit corrupted module/ABI/hash cases.
- **Incremental mutations:** body-only change, public signature change,
  generic constraint change, trait method change, tag validator edit, enum
  discriminant/value change, variant case change, export removal, rename,
  source removal/reappearance, deleting a `.vbcm`, and an unrelated edit.
  Assert rebuild/reuse dispositions as well as execution results.
- **Cycles and scheduler:** 2-, 3- and 4-node SCCs, self-imports, cycles with
  declaration-first interfaces versus circular initialization, multiple
  compile orders, and all bounded cooperative schedules of race/lifetime
  tests. Invalid cycles require specific diagnostics, not arbitrary failures.
- **Robustness:** deep and wide graph limits, path collisions, case/Unicode
  normalization, escaping, directory traversal, symlinks, malformed compiled
  interfaces, truncated blobs, deliberate resource ceilings and recovery
  without poisoning subsequent compilation.

Only those graph/layout transforms encoded by the 5,160-case generator are
currently **automatically executable**. The other rows are a specification of
future oracles and gates, not a statement of test completion.

## 6. Test oracle independence and metamorphic properties

Maintain at least one independent model or clearly stated semantic law for
all checks:

- exact-decimal `Decimal`/rational arithmetic and explicit boundary values;
- pure Python algebraic model for variant/variant case values and match arms;
- trait dispatch table keyed by *(provider identity, specialization, trait)*;
- nominal object identity under aliases and re-exports;
- reference-count/state machine for creation, borrow, move, retain, release,
  destructors, reconstruction and exception cleanup;
- deterministic scheduler state machine for task/channel primitives;
- directed graph model for reachability, public selection and cache
  invalidation; and
- explicit error-category expectations for rejected projects.

Metamorphic rewrites include alpha-renaming, independent declaration
permutation, transparent facade insertion/removal, moving the same exposed
value through different valid import paths, list shape transformations where
vectorization is defined, and optimizer/serialization equivalence. Reject
invalid metamorphic transforms before executing; never treat the first
compiler result as the reference answer.

## 7. CI gates, reporting, and minimization

Ordinary test discovery runs `tests/test_multimodule_interactions.py`, covering
source graphs, nested compiled imports, mixed diamonds/facades, semantic
rejections, deterministic enumeration and qualified dependency identities.
The scheduled job should run both entire finite products, not just a random
subset:

```sh
PYTHONPATH=src:. python -m unittest tests.test_multimodule_interactions -v
PYTHONPATH=src:. python -m tools.multimodule_interactions --inventory --output interaction-inventory.json
PYTHONPATH=src:. python -m tools.multimodule_interactions --all --seed 49103 --output interaction-positive.json
PYTHONPATH=src:. python -m tools.multimodule_interactions --negative --all --seed 49103 --output interaction-negative.json
PYTHONPATH=src:. python -m tools.multimodule_interactions --replay-index 42 --seed 49103
```

All reports include seed, absolute case index, graph, artifact layout,
completed checks and explicit failures. CI batches eight cases per subprocess
to amortize Python startup; a timed-out, crashed, or malformed batch is
retried in smaller windows until any failing case is isolated in its own
subprocess. An unverified case is always a failure, never a pass. Save on failure:
exact module sources and consumer, independent output, build order, stderr,
software version, Python version, scheduler seed and minimized project when
available. Delta reduction should remove unaffected modules, declaration
bodies, type arguments and graph edges while preserving the original failure
category; do not shrink a real semantic mismatch into a parser error.

Suggested pipeline: PR = focused unit subset + original source matrix;
scheduled = complete positive + negative finite matrices + existing compiled
matrix; weekly = broader bounded depth/size/scheduling/coverage campaigns.
Measure time/memory separately so pathological cases cannot silently starve
other fixtures. CI runs on required Python 3.14+, and portable or platform-
specific FFI on its appropriate runners. A local Python 3.13 run must not be
presented as the supported-runtime certification.

### Bounded, isolated campaign slices

`tools.multimodule_matrix_slices` accepts `--case-timeout SECONDS` to execute
each family/graph/layout in a separate subprocess; crashes, malformed worker
records and timeouts are reported as **failed cases**, not silently skipped.
Use half-open family indices for repeatable validation and to avoid one large
process exceeding an overall CI deadline:

```sh
python -m tools.multimodule_matrix_slices --start 41 --stop 62 --graphs nested,alias --layout compiled --case-timeout 12 --output slice.json
python -m tools.multimodule_matrix_slices --start 62 --stop 172 --graphs direct,facade --layout source --case-timeout 12 --output slice2.json
python -m tools.multimodule_matrix_slices --negative --start 39 --stop 170 --graphs alias --layout compiled --case-timeout 12 --output negative.json
```

These are command examples, not claims that every listed slice has been run.
CI's scheduled full-matrix campaign remains dynamic (`--all`), and should be
split into isolated shards if runtime budgets grow. `fixture_defined` pairs
require actual passing campaign evidence before they may be called verified.

## 8. Release readiness checklist

- [x] Explicit finite set of connected multi-feature templates and seed oracles
- [x] Full index enumeration for valid graph/layout combinations
- [x] Full index enumeration for negative graph/layout combinations
- [x] Source, compiled, and three hybrid artifact policies
- [x] Normal discovered unit tests for representative dataflow/diagnostic cases
- [x] Machine-readable pairwise planning backlog exposing missing contracts
- [ ] Complete *executed* 5,160-case positive scheduled matrix on Python 3.14+
- [ ] Complete *executed* 5,100-case negative scheduled matrix on Python 3.14+
- [ ] Fully oracle-backed pairwise coverage of every feasible language axis pair
- [ ] Directed 3-/4-way invariants for all P0 semantic flows above
- [ ] Arbitrary package/recursive SCC, FFI, ownership and scheduler combinations
- [ ] Full repository suite, across all supported OS/Python configurations

Every unchecked item must stay visible in release reports; increasing the
number of generated projects does not substitute for completing these gates.
