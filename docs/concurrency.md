# Concurrency

Valiance provides deterministic, cooperative concurrency on one bytecode executor. Tasks interleave at explicit scheduler, wait, channel, timer, external-wakeup, and cancellation-poll boundaries. This is concurrency, not CPU parallelism.

## Tasks and structured scopes

`spawn` creates a `Task[...]` owned by the nearest dynamic `concurrent` scope. A task preserves its full output row. For example, a function returning `Integer, String` produces `Task[Integer, String]`.

`wait` observes the stored terminal result. Waiting is repeatable and aliases observe the same task identity, outputs, or fault. Waiting over a collection is vectorised and preserves collection shape and task order.

A `concurrent` scope joins all children before it exits. The first deterministic child failure becomes the primary fault; sibling tasks receive cooperative cancellation and cleanup faults are retained as secondary context.

## Channels

`Channel[T]` is invariant. `Channel[T]` is unbuffered; `n Channel[T]` has bounded capacity `n`. Sends rendezvous or apply FIFO backpressure. Closing rejects future sends, wakes blocked operations, and leaves buffered values drainable.

A receive returns either:

- `Receive.Value(value)`, including `Receive.Value(None)` when `T` permits `None`;
- `Receive.Closed()`, only when the channel is closed and drained.

## Transfer and ownership

Ordinary value graphs cross task and channel boundaries without eager deep copying. Runtime values detach through copy-on-write when mutated. Lazy values transfer without forcing their source. Task and channel identities are shared handles. Isolated resources cannot cross implicitly; a unique isolated value requires explicit `move` and becomes unavailable to the sender.

## Timers, I/O, cancellation, and deadlocks

Integrated timers and external wake sources suspend cooperatively. Unsupported host-blocking calls are rejected in concurrent execution. Long-running runtime loops poll cancellation at bounded intervals. Deadlock reports include task, spawn, scope, blocked-operation, and channel creation information where available.

## Deferred beyond the initial release

The initial release does not expose public cancellation or timeout syntax, channel `select`/`match channels`, directional endpoints, detached tasks, priorities, work stealing, parallel execution, or general blocking host I/O.

See `samples/concurrency/` for executable examples and `docs/maintenance/runtime-system.md` for implementation, bytecode, optimizer, fuzz, leak, and benchmark details.
