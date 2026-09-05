# Incremental build inspection

Cache inspection makes reuse and invalidation decisions observable without parsing
log strings. `RebuildReason` records a stable `ReasonCode`, summary, detail
lines, and the first changed dependency when applicable. Rendering is a terminal
concern and remains deterministic for redirected CI logs.

Use:

```text
vln build --explain
vln cache inspect
vln cache verify
vln cache clean
```

`cache inspect` reports artifact schema, module and target identities, source,
interface, implementation and dependency fingerprints, reachability metadata,
last disposition and structured reason, plus object integrity. `cache verify`
returns a failing status when an index or referenced object is corrupt.
`cache clean` removes only `.vln/incremental`; source files and configured final
outputs such as `bin/*.vbc` remain untouched.

Portable build identities remain project-relative. Absolute paths are not added
to artifact identities or normal inspection output.
