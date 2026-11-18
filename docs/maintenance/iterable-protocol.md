# User-defined traversal protocol

User-defined objects implement `Iterable[Index, Value, State]` to participate in
`foreach`. An optional `SingleView[Index, Value, Item]` supplies a different
item type for the one-binding form. These are ordinary traits: the analyser
selects visible evidence and type-checks resolved method calls before code
generation; the VM does not discover implementations by method name.

```vlnc
trait[Index, Value, State] Iterable =>
  extend initial -> State
  extend advance(state: State) -> {Index, Value, State}?
end

trait[Index, Value, Item] SingleView =>
  extend single(index: Index, value: Value) -> Item
end
```

`$self` is the implicit method receiver. `initial` constructs the traversal
cursor; `advance(state)` returns either `Some({index, value, nextState})` or
`\None` to terminate. The cursor is not the public index and may have a
different type. Every step returns a successor rather than modifying the
source; the source remains available through completion, break, or return.

The two-binding `foreach (index, value)` receives `Index` and `Value`.
One-binding `foreach (item)` receives `Value` unless a uniquely matching,
visible `SingleView` implementation provides `Item`; the latter is obtained
by invoking `single(index, value)` for every step. Both binding forms run the
same `initial`/`advance` sequence. `SingleView` without `Iterable` is not
sufficient, and an incompatible projection is rejected for unary iteration.
Trait-typed generic functions can rely only on the evidence in their parameter
types; they cannot adopt concrete call-site `SingleView` behavior implicitly.

For example:

```vlnc
object Count => $limit: Int end
object Count as Iterable[Int, Int, Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int, Int, Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None
    end
  end
end
Count(3) foreach (index, value) => println({$index, $value}) end
```

Native collection paths remain specialized. Two-binding lists use
`(index, value)`, dictionaries use `(key, value)`, and tuples use
`(position, value)` with union typing for heterogeneous tuple elements.
One-binding iteration retains each collection's default view. Reflection-backed
`Fields` continues to bind `(field, position)` through its own checked path.
The raw `ForNode` fields are named `first_variable` and `second_variable`
because they represent lexical positions, not a universal item/index pair.

## Compiler and runtime contract

`analysis/control_flow/loop_handlers.py` resolves the applicable trait
specialization and validates the `initial`, `advance` and optional `single`
call signatures with normal overload analysis. The chosen `TypedElementNode`
call plans are retained on `TypedForNode`. Static refinement must preserve
these nested plans (`analysis/state/transformations.py`). A call against an
abstract nominal trait places the receiver last for type-checking its `extend`
requirements; the compiled runtime method wrapper loads the concrete receiver
first according to the object-method ABI.

`runtime/compiler.py` lowers this to an `IterableForeachReference` with the
compiled body and checked protocol call wrappers. The `FOREACH` opcode
executes those wrappers through ordinary VM user-function scheduling, with
immutable state passing and explicit cleanup on completion, break, early return
and failure. Bytecode serialization records and validates the nested call plans
(format version 0x4A). Optimizer visitors and nested bytecode checks traverse
all these functions. The VM checks the runtime `Some`/`None` step shape, but
never performs its own trait search or specialization.

Regression coverage includes concrete and trait-typed iteration, `SingleView`,
non-integer indices, empty traversal, return/break, nested loops, imports,
interface and bytecode round trips, optimized execution, and malformed payloads
in `tests/test_traits_and_variants.py`, `tests/test_runtime.py`,
`tests/test_optimizer.py` and `tests/test_bytecode_serialization.py`.

The built-in list `map` and `filter` now also accept an `(Int, T)` callback.
These are native list overloads, not calls through the `Iterable` protocol.
Indexing is zero-based at the callback's traversal depth. Their indexed lazy
stages count incoming items (before the stage's own filtering), and fusion or
replay does not change the callback's indices. Native list `map` retains its
rank-aware application, and native list `filter` retains its ordinary input-list
item semantics. The standard-library module `std.iterable` now provides generic, lazy
`Mapped[I,V,S,U]` and `Filtered[I,V,S]` adapters and the `mapIterable` and
`filterIterable` constructors. They are implemented as ordinary objects using
the public `Iterable` protocol; no additional VM traversal opcode is needed.

```vlnc
import {std.iterable.[Iterable, mapIterable, filterIterable]}

object Count => $limit: Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int,Int,Int}? =>
    if ($state < $self.limit) => Some({$state, $state + 10, $state + 1})
    else => \None end
  end
end

$selected = filterIterable(Count(4), fn(value: Int) -> #boolean Int => $value > 11 end)
$mapped = mapIterable($selected, fn(index: Int, value: Int) -> String => $value to[String] end)
$mapped foreach (index, value) => println({$index, $value}) end
```

Importing an operation by itself is sufficient to *call* it with a source
whose type already carries provider-qualified `Iterable` evidence. For example,
`import {std.iterable.[foldIterable]}` accepts a `std.json_iterable.memberView`
without also selecting the trait name. Selective imports preserve nominal
identity in the operation's signature but do not expose the `Iterable` or
`SingleView` declarations as new local names. To declare a generic function
parameter using either trait, or to traverse a returned adapter using its
trait methods in `foreach`, explicitly import the corresponding traits (or
the `std.iterable` namespace).

Both functions accept either a unary `Value` callback or a binary
`(Index, Value)` callback. They hold the original source and defer callback
execution until `advance`. `Mapped` retains the source index and produces the
mapped value `U`. `Filtered` retains original indices without renumbering and
advances its internal cursor until it finds a match or reaches the end. A
filtered iterator's state type remains the original source state type. Empty
sources and exhausted searches produce `\None`. Both adapters can be
composed; repeated iteration is only as reliable as the source's own
replayability.

When the source is known only as `Iterable[I,V,S]`, unary callbacks use
`Value` and an adapter's unary traversal returns its mapped or filtered value.
When a matching `SingleView[I,V,Item]` is visible, a more-specific unary overload
also accepts `Function[Item -> ...]`. It calls `single` on each source step before
applying the callback. The `ProjectedMapped` adapter retains the index but yields
mapped `U` values (it does not inherit the source's `SingleView`). The
`ProjectedFiltered` adapter retains the original `Index` and `Value`, implements
the same `SingleView[I,V,Item]`, and forwards unary projection back to its
source. Both adapters are lazy and can be composed. If `Item` and `Value` have
the same static type, the projected overload wins; callbacks consume `Item`,
not an implicitly interchangeable `Value`. A value-only or indexed-value
callback on a source with a distinct `Item` still uses the ordinary overload;
that form does not preserve `SingleView` in its public result type.

```vlnc
import {std.iterable.[Iterable, SingleView, mapIterable, filterIterable]}

object Count => $limit: Int end
object Count as Iterable[Int,Int,Int] =>
  define initial -> Int => 0
  define advance(state: Int) -> {Int,Int,Int}? =>
    if ($state < $self.limit) => Some({$state,$state+10,$state+1})
    else => \None end
  end
end
object Count as SingleView[Int,Int,String] =>
  define single(index: Int, value: Int) -> String => $index to[String]
end

$onlyLater = filterIterable(Count(4),
  fn(item: String) -> #boolean Int => $item != "0" end)
$onlyLater foreach (item) => println($item) end
mapIterable($onlyLater, fn(item: String) -> String => $item end)
  foreach (index, item) => println({$index, $item}) end
```

`foldIterable` is the eager consumer of the same checked protocol. It takes
an explicit accumulator seed and a callback of one of these shapes:

- `(Accumulator, Value) -> Accumulator` for the underlying iterable values;
- `(Accumulator, Index, Value) -> Accumulator` for indexed values; or
- `(Accumulator, Item) -> Accumulator` with a correlated `SingleView` witness.

An empty source returns the seed without calling the callback. Indices are the
source indices, even after `filterIterable`; they are not renumbered. When a
projected `Item` and underlying `Value` share a type, the `SingleView` overload
wins if that evidence is present. A function constrained only by `Iterable`
uses `Value` even if its concrete argument happens to implement `SingleView`.
The callback is executed once per step during the fold; the fold does not
implicitly materialize the iterable or assume it is finite. An infinite source
can therefore keep the fold running indefinitely. Unlike `reduceIterable`, `foldIterable` always returns the seed for an empty
source and does not wrap its result in an optional.

```vlnc
import {std.iterable.[Iterable, filterIterable, foldIterable]}
# Given a Count object implementing Iterable[Int,Int,Int] as above:
$selected = filterIterable(Count(4),
  fn(index: Int, value: Int) -> #boolean Int => $index >= 2 end)
foldIterable($selected, 0,
  fn(total: Int, index: Int, value: Int) -> Int => $total + $index + $value end)
```

`reduceIterable` is the seedless eager consumer. It initializes its
accumulator from the first source `Value` (or `SingleView.Item` with matching
projection evidence), then combines each subsequent value. The callback takes
`(Accumulator, Value)`, `(Accumulator, Index, Value)`, or
`(Accumulator, Item)` respectively; the indexed callback does not run on the
first step because it initializes the accumulator. The result is `V?` or
`Item?`: an empty traversal returns `\None`, a singleton returns its only
item in `Some`, and a longer traversal returns `Some(reduced value)`. No
arbitrary zero, source indexing, length, or finite traversal guarantee is
assumed. A generic consumer constrained only by `Iterable` reduces underlying
values even when the concrete source also has `SingleView` evidence.

```vlnc
import {std.iterable.[Iterable, reduceIterable]}
# Given Count(3) yielding values 10, 11 and 12 as above:
reduceIterable(Count(3), fn(a:Int, b:Int)->Int => $a+$b end) ?! # 33
```

The generic implementation advances its source directly and branches on `None`
before unwrapping the checked step; it does not require dynamic runtime trait
selection. Object-valued accumulators must preserve ownership even if a
callback returns the same object on consecutive steps.

The projection of a retained step is evaluated during projected filtering and
again if the consumer requests its unary view; therefore a `single` method
with observable effects may be invoked twice for a retained step. These APIs
do not claim that an arbitrary iterable can reconstruct its original type
after mapping, support random access, or be finite, and they never implicitly
collect to a list. A generic consumer constrained only by `Iterable` cannot
assume `SingleView` from a concrete call-site argument.

## Opt-in JSON object member traversal

`std.json_iterable` exposes `memberView` for the concrete `json.Json.Object`
case. It returns an intersection of `Iterable[String, json.Json, State]` and
`SingleView[String, json.Json, String]`, so unary traversal yields keys and
binary traversal yields `(key, json.Json)`. The state snapshots `members` into
a list of key/value pairs in `initial`, alongside an integer cursor and length;
`advance` never rescans the dictionary. Repeated traversals take new snapshots
without mutating the original JSON value.

```vlnc
import {std.json}
import {std.iterable}
import {std.json_iterable}
$doc = json.load("{\"answer\":42}") ?!
$doc match =>
  as obj: json.Json.Object =>
    json_iterable.memberView($obj) foreach(key, value) =>
      println({$key, json.stringify($value) ?!})
    end
  _ => println("not a JSON object")
end
```

The general `json.Json` variant is not iterable. Callers must match the object
case before adapting it. Keeping this optional adapter separate from `std.json`
avoids forcing a dependency on `std.iterable` into unrelated JSON consumers
and transitive `std.web` import bundles. The adapter is ordinary source code,
not an optimiser, JSON VM dispatch rule, or implicit list conversion.

The exported functions use the descriptive names `mapIterable` and
`filterIterable`, but explicitly importing them with aliases `map` and `filter`
adds them to native overload sets with matching input/output arities and
disjoint input domains. Lists still use their native,
rank-aware overloads; custom iterables select the imported generic adapters.
Unqualified user-defined elements still follow ordinary lexical shadowing.
Selective imports and whole-module namespace imports both retain the visible trait
identity for generic evidence. For example, `import {std.iterable as it}`
exposes `it.Iterable`, `it.SingleView`, `it.mapIterable`, `it.filterIterable`,
`it.foldIterable`, and `it.reduceIterable`; use those qualified spellings
consistently in trait implementations and generic type annotations. Selected type aliases such as
`import {std.iterable.[Iterable as Flow, mapIterable]}` also preserve the
correlation between the imported trait and callable signatures.

For example, `import {std.iterable.[mapIterable as map, filterIterable as filter]}`
allows `map` and `filter` to select native list overloads on lists and generic
iterable overloads on custom sources. The explicit `*::map` and `*::filter`
spellings always select the native built-ins. Imported aliases merge only when
native signatures have matching input/output arities and disjoint input
domains. If an imported signature overlaps the native input domain, the import
shadows the native set instead of introducing an ambiguous call. Namespace
imports remain available when a qualified API is preferable.

**Ownership:** object constructors retain temporary borrowed arguments before
placing them in persistent fields. This is necessary for factories that return
an adapter holding an input traversable or callback after the factory's frame
has ended. The import prelude optimizer accepts either list or tuple typed
nodes, including interface-serialization round trips.

A structure-preserving `It[T] -> It[U]` contract remains a separate design.
