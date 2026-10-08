# Concurrency

Valiance provides deterministic, cooperative concurrency on one bytecode executor. Tasks interleave at explicit scheduler, wait, channel, timer, external-wakeup, and cancellation-poll boundaries. This is concurrency, not CPU parallelism.

## Tasks and structured scopes

`spawn` creates a `Task[...]` owned by the nearest dynamic `concurrent` scope. A task preserves its full output row. For example, a function returning `Int, String` produces `Task[Int, String]`.

`wait` observes the stored terminal result. Waiting is repeatable and aliases observe the same task identity, outputs, or fault. Waiting over a collection is vectorised and preserves collection shape and task order.

A `concurrent` scope joins all children before it exits. The first deterministic child failure becomes the primary fault; sibling tasks receive cooperative cancellation and cleanup faults are retained as secondary context.

Concurrent bodies are **closed function-shaped scopes**. Explicit parameters
consume inputs from the caller's stack, bind them to fresh locals, and keep those
locals separate from the caller's bindings:

```valiance
10
concurrent (value: Int) -> Int =>
  $value 2 *
end
```

This produces `20`. Parameters can also be captured by a function created
*inside* the scope and passed to `spawn`. Inferred-input scopes (`concurrent =>`)
likewise receive their inferred inputs as parameters with stack-style cycling.
The body cannot directly capture variables from the surrounding scope: pass
required values through its input stack or explicit parameter list instead.
The body runs in its own VM frame and returns only its analysed output row;
its local declarations do not leak to the caller.

## Channels

`Channel[T]` is invariant. `Channel[T]` is unbuffered; `n Channel[T]` has bounded capacity `n`. Sends rendezvous or apply FIFO backpressure. Closing rejects future sends, wakes blocked operations, and leaves buffered values drainable.

A receive returns either:

- `Receive.Value(value)`, including `Receive.Value(None)` when `T` permits `None`;
- `Receive.Closed()`, only when the channel is closed and drained.

## Transfer and ownership

Ordinary value graphs cross task and channel boundaries without eager deep copying. Runtime values detach through copy-on-write when mutated. Lazy values transfer without forcing their source. Task and channel identities are shared handles. Isolated resources cannot cross implicitly; a unique isolated value requires explicit `move` and becomes unavailable to the sender.

## Timers, I/O, cancellation, and deadlocks

Integrated timers and external wake sources suspend cooperatively. Unsupported host-blocking calls are rejected in concurrent execution. Long-running runtime loops poll cancellation at bounded intervals. Deadlock reports include task, spawn, scope, blocked-operation, and channel creation information where available.

## Scope and limitations

`cancel` requests cooperative task cancellation. `timeout` waits under a deterministic logical-time deadline and requests cancellation if the deadline expires first. The runtime uses shared bidirectional channel handles and structured task ownership. It does not provide detached tasks, priorities, work stealing, CPU-parallel bytecode execution, or unrestricted blocking host I/O.

See the [executable concurrency sample guide](../samples/concurrency/README.md)
for runnable scenarios, expected results, and negative diagnostics. See
`docs/maintenance/runtime-system.md` for implementation, bytecode, optimizer, fuzz, leak, and benchmark details.

### Native calls

A native FFI call made by a scheduled task is executed on a bounded host worker
pool. The task suspends at the call and other tasks may run. Completion is
published through the scheduler's external-wakeup queue, and only the scheduler
thread resumes the task or mutates its Valiance stack. Root, non-concurrent
native calls remain synchronous. Cancellation abandons delivery of an in-flight
native result because arbitrary C functions cannot be interrupted safely.
