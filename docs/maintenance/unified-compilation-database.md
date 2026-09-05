# Unified compilation database

`CompilationDatabase` is the shared workspace authority for
LSP, CLI compilation, test roots, run safety, and REPL integration points.

The database retains saved-file fingerprints, in-memory unsaved overlays,
resolved import dependencies, coherent analysis snapshots, and executable
snapshots bound to the exact successful analysis that produced them. Overlay
text is never published to the artifact store. Closing an overlay restores the
saved source view.

Invalidation follows the transitive importer closure. `ModuleLoader.invalidate`
removes selected source and compiled-interface entries without clearing
unrelated cache state. Frontends no longer access `_cache.clear()`.

`AnalysisSnapshot` binds source, syntax, typed nodes, analyser diagnostics, and
the source fingerprint. `compile_current` refuses a type-invalid current view,
so an earlier successful executable cannot be returned after a failed edit.
Speculative preview compilation remains memory-only.

Regression coverage is in `tests/test_compilation_database.py` and the existing
LSP, compiled-module, semantic-invalidation, and CLI suites.
