# Protected callbacks and State runtime

`State[T]` is backed by three private, reusable layers:

1. `runtime.concurrency.ExclusiveGate`: a FIFO exclusive lease, synchronized
   internally with an `RLock`, with independent cancellation registrations.
   A granted waiter owns the gate before the wake callback runs. A cancellation
   after grant surrenders the lease to the next waiter.
2. `runtime.vm._ProtectedScope`: an activation-lifetime cleanup obligation and
   a task-local nonblocking policy. `_ActivationProtectedWait` is integrated with
   the existing scheduler's `TaskBlocked` and deadlock diagnostics.
3. `runtime.protected.GuardedValue`: independently retained cell handle and
   committed occurrence, with a single ownership-transferring commit point.

The native standard-library wrappers do **not** synchronously execute user
functions. They return a private `ProtectedOperation` request to the VM. Existing
`CALL_RESOLVED_ELEMENT` handling recognizes that request, acquires the gate,
and launches a `_FunctionCallRequest` through the ordinary activation runner.
The request's protected scope is retained in the suspended caller activation.
Normal completion commits before gate release; any callback fault or frame/task
unwind closes the scope idempotently and leaves an uncommitted value unchanged.
Fairness yields are allowed, waits are rejected *before* registration.

Both `$state set value` and `concurrency.set(state, value)` take an already-
evaluated `T` (stack effect `State[T], T -> ()`). Ordinary Valiance evaluation
computes that `T` before invoking either operation, with no State gate held.
The native wrapper validates and retains the replacement, then creates the
same internal `set` request for both spellings. The VM acquires its cancellable
FIFO lease, atomically commits the replacement, and releases the old occurrence.
Cancellation while waiting drops the retained replacement without a commit.
`modify` / `modifyWith` callbacks alone require protected resumable execution.

**No bytecode instructions were added.** Existing activation execution, scheduler
quanta, task cleanup and resolved-element calls provide the extension points.
This is not a claim that the previous VM supplied scoped protection without
changes: the VM was extended to recognize protected requests and associate a
cleanup obligation with nested activations.

## Compatibility notes

- The existing globally named `update` collection element has three arguments;
  global two-argument State updates are offered under `modify` to avoid
  changing all Valiance overload sets. Namespaced `concurrency.modify` uses the
  requested callback signature.
- Type annotations use `State[T]` (the underlying nominal type), while the
  constructor is imported as `concurrency.State`. A source-level `get` declared
  for another receiver can shadow the global State `get` overload; call
  `concurrency.get(state)` when that name is shadowed.
- Ordinary Valiance source type checking establishes the replacement type;
  runtime commit rechecks task-transfer safety. Host-side direct manipulation
  of internal runtime objects is not a public typed operation.
- The protected policy admits only the VM's own core built-in overload
  objects, rejecting host-installed intrinsic implementations regardless of
  namespace or whether they carry `@IO`. It also rejects core built-ins tagged
  `@IO` (including `input`, `print`, and `println`), native standard-library
  calls and explicit suspension instructions. The check precedes scalar and
  vectorised dispatch. Native reflection factories and encoders are guarded
  separately at their callback boundary. The policy is intended to remain
  reusable by other protected-resource abstractions, but is not a sandbox
  for arbitrary Python code running outside VM entry points.
- Stored values must be eagerly representable immutable snapshots. Lazy lists
  and opaque native handles are rejected at runtime because their deferred
  evaluation or external lifetime cannot be proved safe for shared snapshots;
  copied native aggregates are recursively checked for unsafe contents.

A blocked task keeps its wait-registration cancellation callback only until
its generator resumes. Clearing it on resumed execution matters for granted
exclusive waiters: otherwise a later fairness yield leaves a stale callback
that could surrender the **active** lease when cancellation is requested.
A task cancelled between wake and resumption still closes its suspended
activation, which cancels the granted-but-unused waiter.

At a statically resolved `std.concurrency.State` constructor, the call analyser
checks the concrete initial type, including nested managed object fields,
records and generic collections, for `@nodup` and task-transfer isolation.
Runtime cell validation walks the stored value graph independently, stopping
at identity-bearing shared handles, so a shallow owner copy cannot hide a
nonduplicable child. Dynamic checks also cover unresolved type
parameters and host-injected values. Contextual function modifiers preserve
their effect tags when applying expected callback overloads.

Tests in `tests/test_std_concurrency_state.py` exercise the gate independently,
optimised and unoptimised compiled execution, compiled serialization, concurrent
updates, snapshot retention, aborts and deterministic task cancellation.

An imported object-friendly method can share a name (notably `get`) with a
core intrinsic. For checked user declarations that shadow such methods,
`analysis/calls/selection.py` validates an overload-specific runtime alias
against the qualified binding's implementation identity. When the alias
points to a different declaration, the selected local definition is linked
through its own unqualified runtime name instead. This preserves both the
imported `Owner::method` and the external method without weakening static
identity verification in the VM.
