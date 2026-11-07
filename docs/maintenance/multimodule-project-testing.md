# Multi-module project correctness

A project is the unit of testing here: each case creates a real directory tree,
with provider modules, optional public facades, an importing consumer, and an
independently specified result or expected diagnostic. The matrix is executable,
not a list of hypothetical language features.

For connected cross-feature data flows, five artifact layouts, a 2,490-case finite connected-interaction matrix,
and the explicit gaps inventory, see [the interaction plan](multimodule-interaction-test-plan.md).

## Current executable matrix

`tools/multimodule_fuzzing.py` generates 19 feature families:

| Declaration/interaction | Positive contract |
| --- | --- |
| Element | public function, multi-value caller results |
| Overloaded element | Int/String signatures both preserved across facades |
| Generic element | repeated independent Int/String specializations |
| Generic object | public fields, constructor type and specializations |
| Object method | imported object-friendly method and receiver |
| Trait | requirement, dispatch through trait view |
| Trait default | public default method through an imported trait |
| Trait inheritance | inherited requirement, subtype dispatch |
| Generic trait | generic object, two distinct generic trait instantiations |
| Generic variant | generic payload constructor and exhaustive match |
| Non-generic variant | fixed payload constructor and exhaustive match |
| Literal-backed variant | imported niladic members and stored values |
| Bare variant | imported niladic members and names |
| Computed tag | explicit tag import and tag-restricted callable |
| Constructed tag | sticky evidence and explicit tag removal |
| Unit tag | unit evidence and explicit unit removal |
| Variant tag | computed parent/variant evidence and runtime match |
| Cross-module trait | model and dispatch helper from different provider modules |
| Cross-module generic | model and generic helper from different provider modules |

Each family is crossed with **six dependency graphs**: direct selective import,
public facade, two-hop public facade, diamond containing an alternate
namespace, nested directory (`pkg.core`), and renamed facade export. This
makes **114 feature/graph combinations per seed**. Source-only and source-free
(`.vbcm` only) modes each run with optimization on/off and with live/restored
program bytecode: **912 independent execution comparisons per full seed**.
The generated inputs contain deterministic seed-dependent values, and the
expected outputs are computed in Python rather than copied from a Valiance
execution mode. Not every graph checks both arms of a diamond; separate
nominal-identity regression tests exercise both imported aliases.

Twelve *negative* project fixtures (24 source/binary configurations) require
specific diagnostics for private elements and objects, private re-export
leaks, generic specialization mismatches, incorrect variant instantiation,
trait mismatch, unresolved exports or modules, overload/name collisions,
missing tag imports, and wrong overload arguments. A nonempty diagnostic of
an unrelated kind does not count as success.

Additional `tests/test_multimodule_projects.py` regressions cover two aliases
of one nominal through a diamond, preservation of all callable overloads
through three module hops, incremental implementation invalidation, enum
value updates, and bytecode-link freshness.

## Invariants

1. **Import visibility.** Only permitted imports become name-resolvable;
   an explicit missing or private symbol reports a diagnostic.
2. **Provider provenance.** Generic object and trait identity remains tied to
   the defining declaration, independent of public aliases and facades.
3. **Overload completeness.** Public re-exports preserve *each* overload's
   parameter types, return types, and runtime body. A facade must not turn an
   overload set into the first function's signature.
4. **Type/VM agreement.** A program accepted by the analyser compiles and
   produces the independent expected stack for both optimization settings.
5. **Artifact fidelity.** `loads(dumps(program))` executes identically. Source
   deletion after successful module compilation must not change semantics.
6. **Rebuild correctness.** Provider body/value edits invalidate dependent
   compiled products; stale, incompatible module interfaces must not silently
   execute an old artifact.
7. **Determinism.** A family, graph, and seed reproduce identical source
   files and independent expected output on any supported host.
8. **Fail-closed diagnostics.** Wrong inputs must not become silently accepted
   merely because the provider was loaded from `.vbcm` instead of `.vlnc`.
9. **Lifetimes survive imports.** Private `~Type` implementations and their checked
   bodies are retained in module interfaces solely for runtime constructor
   metadata. Rebuilt trait-implementation surfaces must retain the constructor's
   lifecycle obligations; importing a destructor never makes it publicly callable.
10. **Nominal aliases are contractual.** If a selected type becomes `Alias`,
    callable overloads imported in the same selection must use the alias, even
    through public facades. Aliasing two unrelated `Item` declarations never
    makes their identities equal.

The cross-module identity suite additionally tests private trait implementations
used by exported functions, through deep facades, generic return types,
exception paths, source-free modules, mixed dependency layouts and independent
providers declaring identically named receivers. It also checks private variant
payloads remain usable at runtime without becoming publicly importable.

Provider executable closure tests verify that an imported public helper can
construct an *unselected public* sibling object without a runtime undefined-name
fault. The constructor is linked through checked provider calls, not exported
into the consumer's namespace. The same module can be imported in separate
clauses or through aliases and compiled facades. Independent providers with
identically named constructors must keep separate runtime destructor bindings.
See `tests/test_multimodule_known_failures.py`.

## Commands

From the repository root, with Python 3.14 or newer:

```sh
PYTHONPATH=src:. python -m unittest tests.test_multimodule_projects -v
PYTHONPATH=src:. python -m tools.multimodule_fuzzing --iterations 114 --seed 94721 --output project-source.json
PYTHONPATH=src:. python -m tools.multimodule_fuzzing --iterations 114 --seed 94721 --source-free --output project-binary.json
PYTHONPATH=src:. python -m tools.multimodule_fuzzing --iterations 684 --seed 95702 --source-free --output project-nightly.json
```

Use `--family generic_trait` or `--graph diamond` to limit a campaign; include
`--source-free` to remove all provider sources after compiling their modules.
Long campaigns use 30-case subprocess batches by default, with a 45-second
per-batch timeout. Use `--start` with `--iterations` for an exact slice,
`--batch-size 0` to run without isolation, or `--batch-timeout` to change
the limit. Timeout and worker failures are counted as failures.

The campaign saves each failure's project source files, consumer, seed,
expected output, and dependency order in JSON. To reproduce an exact case:

```sh
PYTHONPATH=src:. python -m tools.multimodule_fuzzing \
  --replay-case overloaded_element/deep_facade/17 --source-free
```

The CLI returns a nonzero exit status when any generated project fails.
`iterations` samples a seeded shuffle of all 114 combinations before
repeating any; multiple cycles generate new values for each combination.

## Provider-qualified runtime declaration dependencies

The module loader resolves runtime dependencies from analysed declarations,
**not from library-specific imports**. The independent
`modules_system/runtime_dependencies.py` resolver computes a private,
transitive graph through object fields, generic substitutions, callable
signatures, variants, enums, traits, and implementation patterns. The closure
is stored in `ModuleExports.runtime_dependencies` and persists through public
facades and `.vbcm` interfaces without exposing those declarations as source
imports. Imported declarations keep canonical provider-qualified identities;
identically named private objects belonging to separate providers remain
independent.

An object declared locally may refer to a type imported from another module
in its field annotations, generic arguments, method signatures, or trait
contracts. Its exported runtime contract must therefore carry a spelling-to-
provider origin map, even when that import was private and never re-exported.
The analyzed interface retains the imported declaration as a **private runtime
requirement**; the closure resolver consults the owning declaration's origin
map before resolving unqualified type references. A missing recorded provider
is never replaced by a same-named local type. This contract is exercised by
`RuntimeDependencyResolutionTests` and multi-module source/binary tests that
compose providers, local containers and public facades. Generic arguments and
independent providers must survive the complete linking and VM path.

Public generic functions may defer checking a body containing reflective managed
construction (`uninit[Owner]`, `fields[Owner]`, `finish?`) until a consumer
supplies its type arguments. Their provider's private object declarations must
then be loaded into a **temporary lexical analysis scope**, including through
source-free and re-exported interfaces. The object is registered under its
canonical provider-qualified name for this check, not exposed as a new import
in the consumer. Internal aliases must retain the selected nominal spelling
in checked descriptors: an `alpha.Box` lookup must not silently normalize to
`Box` and collide with `beta.Box`. Variant and trait dependency loading
continues to use the ordinary import contracts; this managed-object scoped
recheck does not rewrite their runtime identities. See the managed-construction
and provider-isolation regressions in `tests/test_compiled_modules.py`.

`tests/test_public_reexports.py:RuntimeDependencyResolutionTests` checks the
closure without importing any JSON library: transitive private dependencies,
provider isolation across a facade, and source-only and compiled-only loading.
`tests/test_reflection_dynamic.py` checks that the same VM type services are
available to non-codec consumers. Each standard-library implementation can
obtain these capabilities through `RuntimeContext.type_services`; there are no
JSON-specific AST fields or module-import fallbacks.

## Transitive JSON schema and nominal-identity regressions

`tests/test_multimodule_reconstructed_repairs.py` exercises a separate
project-level test matrix for a compiled consumer that imports only the **outer**
object declaration. It checks that unimported nested constructors are linked as
runtime schemas without becoming visible source names. The independent oracle
checks each decoded object's exact provider-qualified nominal name, its generic
arguments, the nested field values, and the exact error type on schema mismatch.

The deterministic matrix varies:

- one to three nested dependencies, via one or two public facade hops;
- source-only, source-free and **provider-only compiled** dependencies;
- generic object layers reified at `Int` and their serialization;
- two providers with identically named generic `Item`/`Container` declarations,
  imported in both orders and with one provider compiled at a time;
- negative generic overload calls across distinct providers, which must be
  rejected even when their `Box[Int]` layouts are structurally identical;
- several JSON scalar values, including negative, zero and large integers;
- edits to a private nested field type, requiring an unchanged `.vlnc` consumer
  to relink and observe the new JSON schema after an incremental rebuild.

The `run_project` helper runs every accepted project in four execution modes
(optimized/unoptimized × live/serialized bytecode) and rejects analyser
errors. Its artifact selection accepts `source`, `compiled`, or a `frozenset`
of module identities to precompile. This ensures partial source/binary builds
use isolated temporary directories and cannot pass through stale caches.

The generated cases in this section are **orthogonal** to the connected
language-feature pair inventory: they add topology, schema-depth, value and
binary-layout coverage, not new semantic contract families. In particular,
they do not reduce the unimplemented 1,265-pair backlog merely by counting
additional executions.

`tests/test_multimodule_known_failures.py` retains permanent, ordinary
regressions for imported generic reflection, aliased destructor dispatch, and
unselected executable constructors. Source and compiled-only layouts must pass
rather than report expected failures. Do not loosen nominal matching to short
names: unrelated provider types with identical structure must remain distinct.

Run this focused suite with:

```sh
PYTHONPATH=src:tests python -m unittest tests.test_multimodule_reconstructed_repairs -q
PYTHONPATH=src:tests python -m unittest tests.test_multimodule_known_failures -q
```

## Next investigation priorities (not claims of coverage)

The finite matrix **does not** establish that every possible import/type
combination is safe. Prioritize these extensions in descending risk:

- **Import graph shape:** packages from dependency manifests and registries,
  cycle/SCC diagnostics, import shadowing, ambiguous providers, duplicate
  import order, nested namespaces, conflicting aliases, private transitive
  dependencies, source/compiled hybrid graphs, filesystem normalization.
- **Type relationships:** cross-module generic bounds/`where`, variance,
  higher-rank function parameters, recursively nested generic objects,
  structural traits, open unions/intersections, overload selection plus
  vectorization and tags, inferred specialization order, generic defaults.
- **Lifecycle and dispatch:** private fields, constructors/destructors,
  explicitly borrowed receiver values, object reconstruction, trait behavior
  providers in independent modules, default/override conflict, behavior
  ambiguity, implementation deletion, native/FFI module boundaries.
- **Variants/enums/tags:** nested variants, discriminants, tagged payloads,
  disjoint tag lattices, validators, tag overlays, unit conversions, exhaustive
  matches through imports, reflection, and serialization of erased owners.
- **Incremental/binary compatibility:** mixed source/.vbcm diamonds,
  signature removals and changes, stale artifacts, rebuilding only an ancestor,
  source reappearance, unchanged unaffected modules, corrupt manifests and
  bytecode, concurrent builds and out-of-order dependency rebuilds.
- **Diagnostics/robustness:** malformed source in a dependency, missing imports,
  capitalization and Unicode collisions, path traversal and symlinks,
  initialization cycles, dependency graph size and depth, deterministic
  minimal failure reduction, memory and time budgets.

A case is only promoted to the verified matrix when it specifies its semantic
oracle (values or exact diagnostic), exercises the expected compiler stage,
and is run against source and compiled representations. More random test cases
are useful, but covering a missing semantic class is more valuable than
repeating shallow arithmetic imports.
