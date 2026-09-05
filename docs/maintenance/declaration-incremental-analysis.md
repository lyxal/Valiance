# Declaration-level incremental semantic analysis

The incremental analyser uses a stable semantic-product boundary beneath the whole-module
incremental coordinator. The product model is defined in
`src/valiance/incremental/semantic.py`.

## Product model

`DeclarationIdentity` identifies a declaration by package, canonical module,
declaration kind, declared name, optional owner, and a source-declaration
discriminator. It intentionally excludes Python identity and source offsets.
The discriminator preserves the language's distinction between separate
equal-signature overload declarations.

`SemanticProduct` records syntax, header, semantic-input, semantic-output, and
implementation fingerprints together with semantic dependencies, published
facts, typed payloads, diagnostics, lints, and optional executable-region state.
These records contain stable data only. Live analysers, branches, branch sets,
and mutable environments are not persistence products.

## Invalidation

`invalidate_products(...)` first identifies new, changed, and deleted syntax
products. An unchanged product is reusable only when each recorded semantic
fact still has the fingerprint observed during analysis. A body-only edit
therefore invalidates that declaration immediately, but consumers remain
reusable until reanalysis proves that the provider's semantic output changed.

Top-level executable regions additionally compare an incoming state
fingerprint. This prevents a statement from being restored independently when
an earlier statement changed the stack or variable state.

`SemanticProductStore` is the explicit publication and restoration API for the
in-memory product index. The coordinator and analyser can layer persistence and
fact-replay hooks on this boundary without serializing implementation objects.

## Follow-on integration

The next integration steps are to record dependencies at analyser decision
points, export and restore environment fact deltas, segment top-level executable
regions, persist products through the artifact store, and compare restored
analysis against clean whole-module analysis. The dependency kinds in
`DependencyKind` cover the complete dependency model so those hooks can be added
without changing the product schema.

## Verification

Focused coverage is in `tests/test_incremental_semantic.py`. It verifies stable
canonical fingerprints, body-edit precision, semantic-output propagation,
declaration deletion, and executable-region incoming-state checks.
