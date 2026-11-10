# Process-local shared state: `State[T]`

`std.concurrency.State` holds a single **committed immutable logical value**.
Independent handles share the same cell. Reads and writes on an individual cell
are linearizable. Construction does not create an actor or a background task.

```valiance
import {std.concurrency}

$counter = concurrency.State(0)
const $before = get $counter
$counter set 10
$counter modify: (1 +)
const $after = get $counter
```

A snapshot can be interpolated directly: `println "State = ${get $counter}"`
formats the committed value, not the State handle.
The qualified spelling is also supported inside interpolation:
`println "State = ${concurrency.get($counter)}"`.

The module also exports explicitly called operations, useful when passing a
named callback or spelling a call unambiguously:

```valiance
import {std.concurrency}

$counter = concurrency.State(0)
concurrency.set($counter, 10)
concurrency.modify($counter, fn(value: Int) -> Int => $value 1 + end)
concurrency.get($counter)
```

To compute a result separately from the new committed state, use `modifyWith`.
Its callback returns **two stack values**, not a tuple encoding:

```valiance
import {std.concurrency}

$stock = concurrency.State(5)
$reserved = $stock modifyWith: fn(quantity: Int) -> Int, Bool =>
  if $quantity > 0 =>
    $quantity 1 -
    true
  else =>
    $quantity
    false
  end
end
```

## Operations and stack effects

| Operation | Input | Output | Behaviour |
| --- | --- | --- | --- |
| `concurrency.State` | `T` | `State[T]` | Create one cell with an owned committed value |
| `get` / `concurrency.get` | `State[T]` | `T` | Retain an immutable logical snapshot |
| `set` / `concurrency.set` | `State[T], T` | — | Commit an already-evaluated replacement atomically |
| `modify:` / `concurrency.modify` | `State[T], Function[T->T]` | — | Transform the latest value atomically |
| `modifyWith:` / `concurrency.modifyWith` | `State[T], Function[T->T,R]` | `R` | Commit the first callback output, return the second |

The unqualified `update` element already belongs to indexed collection
reconstruction and takes three inputs. A new, two-input unqualified `update:`
overload would conflict with the current element-overload rules; use
`modify:` for modifier syntax or `concurrency.modify(...)` instead. For
partially-applied infix operators use `(1 +)` inside `modify:`; Valiance's
existing modifier grammar does not treat the unparenthesized `1 +` as one
modifier function body.

A typed state handle is spelled `State[T]` in type positions.

## Concurrency and failures

The internal gate admits one operation at a time and queues other tasks in FIFO
order. Reads wait for an in-progress transformation. The value stored in the
cell is never taken out during a transaction; a successful validated replacement
is published at one commit point. A callback panic or cancellation before that
point retains the old value. Cancellation after commit does not undo it.
`set` takes a value directly, **not a callback**. Its replacement expression is
evaluated by ordinary Valiance execution before the `set` operation starts. That
calculation may perform I/O or suspend, without holding the State gate; if it
fails or is cancelled, the State remains unchanged. Both `$state set value`
and `concurrency.set(state, value)` then use the same atomic, cooperatively
acquired replacement path. When replacements race, the last committed value
wins. Separate `get` and `set` calls do *not* implement an atomic
read-modify-write; use `modify` for that operation.

`modify` and `modifyWith` callbacks run as ordinary **resumable VM
activations** and yield to other tasks after their instruction quantum while
still owning the gate.
They must not wait on channels, tasks, scopes, native calls or external I/O, and
cannot start another State operation on the same or another cell. These operations
raise a runtime error before they suspend or register a wait. Exceptions and
panic signals use Valiance's normal error propagation; there is no automatic
interpretation of a `Result` value as transaction failure. Side effects performed
outside the State cell are not rolled back.

State values must be duplicable and task-transfer-safe. Runtime duplication and
transfer checks reject incompatible isolated resources and `@nodup` values.
The compiler also rejects statically known nonduplicable or task-isolated
initial types at the `State` constructor, including transitive owned fields and
collection contents, alongside generic callback signatures
and replacement types. Unresolved type parameters require runtime validation.
The underlying snapshot relies on Valiance's ordinary persistent-value reconstruction
semantics; Python extensions that mutate retained runtime objects in place are
outside this contract. A local declaration named `get` may shadow the global
State `get` overload; `concurrency.get($state)` remains unambiguous.

This is a *process-local* primitive, not a database, an actor, a public mutex or
a multi-cell transaction.

### Runtime storage restrictions

`State[T]` rejects `@nodup` resources, including resources nested inside
otherwise duplicable values. The runtime additionally rejects lazy list
representations and opaque native handles whose deferred evaluation or native
lifetime could violate immutable snapshot sharing. Materialise a finite lazy
collection into a duplicable eager value before storing it.

Protected callbacks may yield for instruction-budget fairness, but cannot
wait, invoke built-in I/O (such as `input` or `println`), invoke native
standard-library operations, host-registered built-ins, native reflection
factories or encoders, or acquire another protected State. The runtime
admits only its own registered core intrinsic implementations (subject to
the I/O effect check); it rejects host implementations before dispatch.
Host code running independently of the VM cannot be sandboxed by this policy.
