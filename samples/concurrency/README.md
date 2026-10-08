# Concurrency examples you can actually run

These programs exercise **cooperative** concurrency on one Valiance VM. They do
not benchmark parallel CPU execution: progress happens at waits, channel
operations, and other scheduler boundaries.

Run a successful example from the repository root:

```sh
vln run --file samples/concurrency/PingPong.vlnc
```

The newer successful examples use `println` to show **computed runtime results**
(and leave the same values on the final stack for automated checks). For example,
`FanOutFanIn.vlnc` prints two different lists, one for each task result column:

```text
fan-out squares:
[49, 4, 25, 9]
fan-out successors:
[8, 3, 6, 4]
```

## Application-style examples (start here)

These are **small, runnable application sketches**, not performance benchmarks or
network-service implementations. The amounts and readings are illustrative;
replace the in-memory calculations and producer with real integrations where
appropriate. Concurrency here means *cooperative interleaving*, not parallel
execution across cores.

| Program | Application scenario | Observable result |
| --- | --- | --- |
| `CheckoutQuote.vlnc` | Launch subtotal, delivery, and discount calculations together; join before composing an order quote | Subtotal 4548, delivery 499, discount 600, total **3449 cents** |
| `BatchInvoiceTotals.vlnc` | Price a batch of independent invoices with a 75-cent per-invoice handling fee | Results **[1275, 3575, 965, 6075]**, in original input order |
| `TelemetryIngestion.vlnc` | Run a sensor and a collector through a bounded one-reading queue, then signal completion | Readings **18, 24, 21, 27**, followed by `Closed` |
| `ScopedQuoteInputs.vlnc` | Pass named price and delivery inputs into a closed concurrent scope, spawn two independent estimates that capture those parameters, and join both | Quoted price **3000 cents**, shipping **300 cents** |

Run one with `vln run --file samples/concurrency/CheckoutQuote.vlnc`.
The output is computed by the program, not a hard-coded sample transcript.

## Choose an example

| Program | Behavior worth checking |
| --- | --- |
| `FanOutFanIn.vlnc` | Four argument-taking tasks return two values each; a vectorised wait preserves input order and both output columns. |
| `PingPong.vlnc` | Two unbuffered channels enforce three rounds of bidirectional rendezvous; the trace contains all six received messages. |
| `MultiProducerDrain.vlnc` | Two producers contend for a capacity-one mailbox; a consumer observes all four values and a final `Closed` after producers finish. |
| `ClosedReceiverBroadcast.vlnc` | Three consumers receive `Closed` when a different task closes their unbuffered channel. |
| `WaitIsNotReexecution.vlnc` | A worker sends **exactly one** unbuffered message. Repeated waits using an alias yield the same `99` without repeating the send. |
| `TransferSnapshots.vlnc` | A list crosses a channel boundary, the worker changes a local alias, and the received value plus original list stay unchanged. |
| `NestedScopeJoin.vlnc` | An outer task executes an inner concurrent scope, joins its two child results, and returns `42`. |
| `TimeoutCancellation.vlnc` | **Expected failure:** a blocked receiver with zero logical-time timeout is cancelled instead of hanging. |
| `DeadlockDiagnostic.vlnc` | **Expected failure:** two receivers with no sender produce a deadlock report identifying both blocked channel operations. |
| `NestedScopeFailure.vlnc` | **Expected failure:** an unobserved child panic propagates through the inner scope and outer task. |

The original examples (`BasicTask`, `BoundedBackpressure`, `CloseAndDrain`,
`ProducerConsumer`, `SafeCapture`, `IsolatedTransfer`, `VectorisedWait`, and
`StructuredFailure`) are retained as minimal reference cases.

## Run the executable regression checks

```sh
python -m unittest tests.test_concurrency_documentation
python -m unittest tests.test_concurrency_runtime
```

`tests/test_concurrency_documentation.py` verifies **every** `.vlnc` file here:
expected return values or faults, plus printed output where present. It runs
with optimisation on and off, with both in-memory and serialized bytecode.
For the deadlock check, resource IDs are normalised because they are allocated
globally, rather than fixed per run. The runtime tests additionally inspect a
one-slot channel while its second sender is *actually blocked*, and check that
closing a channel wakes *all* registered receivers exactly once.

The intentionally failing `.vlnc` files are meant for diagnostic tests, not
normal successful CLI runs. Logical timeouts are scheduler deadlines, not
wall-clock timing benchmarks. The complete feature reference is
[`docs/concurrency.md`](../../docs/concurrency.md).
