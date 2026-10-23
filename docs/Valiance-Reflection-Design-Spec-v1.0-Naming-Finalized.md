# Valiance Reflection — Language Design Specification v1.0 (Naming-Finalized Candidate)

**Status:** Public API names and core semantics finalized for implementation planning. **Not implemented or compiler-verified.**  
**Supersedes:** v0.9 and earlier consolidated reflection drafts/addenda where they differ.  
**Scope:** General-purpose, type-preserving reflection; dynamic structural access; runtime generic execution; call-site type checking (CSTC); explicit unsafe managed-object construction.

> **Design thesis:** The public API should feel like ordinary Valiance: `fields`, `foreach`, indexing, generic functions, normal assignments, explicit `to` conversions, normal ownership, and explicit `unsafe`. Compiler/VM mechanisms—not source-level `withField` or a general erased `Any`—should reconcile dynamically selected fields with static type safety.

**Terminology:** **Confirmed** records behavior selected during the design discussion. **Named API** records final proposed public spellings. **Implementation contract** describes required behavior without claiming current compiler support. Code is illustrative design syntax unless identified as existing Valiance.

---

## 1. Normative decision record

| Subject | Status | Contract |
|---|---|---|
| Field enumeration | Confirmed | `fields[T]` is valid for **every** type, including primitives such as `Int`. It returns stored-field descriptors in declaration order, including private metadata, or an empty set when there are none. `$value fields` follows the same rule. |
| Lookup | Confirmed | Every name-based field lookup is **dynamic**, including string literals. Direct lookup panics if the field is missing; fallible lookup returns `Option` (`None` when missing). |
| Public hidden-type opening | Confirmed | **No public `withField` construct**. Opening a hidden field's type is internal compiler/runtime machinery. |
| Public erased reflection value | Confirmed for initial design | **No public `ReflectiveAny`** is needed in the supported closed-world reflection model. |
| Heterogeneous iteration | Confirmed | Reflective `foreach` preserves the **actual generic field type `T`** separately in each callback invocation; it is not replaced by an aggregate union inside the callback. |
| Direct reflective read | Confirmed | Reading a dynamically selected field outside reflective iteration has the conservative union of **all possible owner-field value types** as its static type. |
| CSTC trigger and propagation | Confirmed | If reflective behavior can produce different result types, generic return typing is deferred to concrete call sites. Nested generic operations propagate this deferral. |
| Conservative unions | Confirmed | All possible contributing field/result types are included, **even when runtime conditions may skip some fields**; impossible alternatives may be removed only by sound static proof. |
| Field callback validation | Confirmed | At a concrete call site, operations within reflective callbacks are checked for **every possible field type**; an invalid possibility is a compile-time error, not an avoidable runtime failure. |
| Runtime generics | Confirmed | Runtime type evidence remains available for actual-type generic execution; it complements, rather than replaces, CSTC. |
| Reflective read ownership | Confirmed | Ordinary member-access, borrowing, materialization, move, and duplication rules apply; reflection is not a special owner-creation operation. |
| Reflective assignment | Confirmed | `$obj[field] = value` works directly. The dynamic boundary implicitly performs a checked `as!` cast to the field's runtime type; failed checks panic. |
| Explicit conversion | Confirmed | `$obj[field] = value to[$field.type]` requests ordinary explicit `to` conversion, followed by normal assignment checks. No implicit `to` conversion occurs. |
| `as!` meaning | Confirmed | `as!` is a **checked cast**: succeeds if the value satisfies the target type, otherwise panics. It is **not** an unchecked/unsafe assertion. |
| Visibility | Confirmed | Outside `unsafe`, reflective value access checks field permissions at runtime and normally panics on denial; fallible alternatives can return errors. Inside lexical `unsafe`, visibility checks are omitted. |
| Type narrowing | Confirmed | Reflected unions are narrowed using ordinary Valiance `match`, `as?`, and checked `as!`; reflection introduces no dedicated narrowing construct. |
| Fallible field operations | Confirmed | Missing-field lookup returns `Option`; recoverable reflective read/write failures return `Result` using **reflection-specific** error types (final names in §4.4.1). `DuplicationFault` remains unrecoverable. |
| Failure precedence | Confirmed | After ordinary operand evaluation and any necessary descriptor/owner validation, check reflective **visibility permission before destination type compatibility**. Recoverable failures do not publish partially reconstructed objects. |
| Immutable failed writes | Confirmed | A non-consuming fallible reflective write returns `Ok(reconstructed_owner)` or `Err(error)`. Failure produces no replacement owner and leaves the original unchanged; no original owner is returned inside `Err`. |
| `@nodup` unsafe requirement | Confirmed | If the compiler cannot prove that **no `@nodup` can be involved, transitively**, reflection requires `unsafe`, including metadata-only reflection and unconstrained generic reflection. |
| Duplication | Confirmed | `unsafe` authorizes reflection but never illegal duplication. The compiler rejects statically provable violations; the VM is the **guaranteed fallback** for violations only recognizable at runtime. |
| Duplication fault recovery | Confirmed design intent | A detected duplication violation is an integrity failure and **must not be recoverable as an ordinary panic or a `Result`**. Exact process/task termination is a separate general VM decision. |
| Unsafe construction | Confirmed | `uninit[T]()` initially applies to compiler-known managed object layouts; native/opaque representation construction is deferred. |
| Builder operations | Confirmed | The **entire `Uninit[T]` workflow requires lexical `unsafe`**, including writes, `finished?`, `finish`, and `finish?`. |
| Completeness preflight | Confirmed | `finished? builder` returns `Bool` without consuming it and checks initialized storage only. |
| Finalization | Confirmed | `finish builder` consumes and returns `T` or panics if incomplete; `finish? builder` consumes and returns `Result[T, ConstructionError]`. Both consume even on failure. |
| Public API naming | Finalized | `Type`, `Field`, `Fields`, `field?`, `read?`, `write?`, `Uninit`/`uninit`, `finished?`/`finish`/`finish?`, and the named reflection error/fault family. |
| CSTC and recursion | Confirmed existing-language constraint | Reflection-dependent inferred output invokes CSTC; Valiance recursion requires fully specified parameter and return types. Recursive functions cannot depend on deferred CSTC inference to establish their recursive signature. No reflection-specific recursion exception is introduced. |
| Construction guarantees | Confirmed | Complete stored representation is required; constructors and unrelated constructor-enforced semantic invariants are deliberately bypassed. Partial-resource cleanup remains correct. |

### What is *not* a new rule

- Reflection does **not** introduce new implicit numeric, string, or user conversion rules.
- An empty field set is a **valid result**, even for `fields[Int]`; `fields` is not an object-only capability.
- Narrowing an inferred field-type union uses the **existing** `match`, `as?`, and `as!` features, not special reflection operators.
- `unsafe` does **not** disable the checked `as!` at reflective assignment, owner/field identity checks, type representation validation, duplication checks, lifetime bookkeeping, or initialized-storage checks.
- A conservative union **outside** a reflective callback is not the actual field type **inside** that callback.
- CSTC does **not** need to analyze imaginary types created after compilation. Support for runtime-created types, runtime schema-to-type compilation, or open-ended hot-loaded types is outside the initial design.
- CSTC does **not** waive Valiance's requirement that recursive functions have explicit, complete parameter and return types.
- A field name written as a literal is **still a dynamic lookup**: a known-missing literal name is not changed into a compile-time field-lookup diagnostic. Compile-time rejection remains mandatory for invalid generic operations and provable duplication violations.

---

## 2. Public reflection surface

### 2.1 Type and field descriptors

A runtime type descriptor, `Type[T]`, identifies a concrete runtime Valiance type, including its generic arguments and relevant tags/refinements. It is type evidence, not an instance of `T`.

A field descriptor identifies a **specific** stored field, including owner identity, field identity, name, position, visibility, and actual value type. Two `Int` fields on one object are distinct descriptors.

For internal compiler models, a field selected dynamically can be regarded as:

```text
SomeField[Owner] ~= exists FieldType. Field[Owner, FieldType]
```

`SomeField` is **internal terminology only**. The public name is `Field[Owner]`, carrying a hidden concrete field-type witness. The compiler may internally refine that witness to `Field[Owner, FieldType]` inside reflective iteration; the extra parameter is not required in user syntax.

The public collection is `Fields[Owner]`. It is heterogeneous, not a conventional `List[Field[Owner, T]]` for some fixed `T`. Its individual members are publicly `Field[Owner]` descriptors. Field identity and concrete value-type evidence remain available to the compiler/runtime.

### 2.1.1 Final public descriptors and metadata

| Name | Meaning |
|---|---|
| `Type[T]` | Reified runtime evidence for concrete type `T`; not a value of type `T` |
| `Field[Owner]` | One owner-bound field descriptor with internally retained concrete value-type evidence |
| `Fields[Owner]` | Heterogeneous declared-field collection in declaration order; can be empty |
| `Visibility` | Field accessibility category using the existing `public`, `readable`, `private` distinctions |

A field exposes **read-only metadata**: `$field.name: String`, `$field.type: Type[FieldType]` (the concrete `FieldType` is hidden outside generic reflection), `$field.owner: Type[Owner]`, `$field.index: Int` (zero-based declaration order), and `$field.visibility: Visibility`. `Field` identity distinguishes two fields with the same value type. Descriptor equality, where supported, compares identity within one compiled program and does not promise stability across compiled programs or hot reloads.

The familiar surfaces remain `fields[T]` and `$value fields` (no rename). No public `SomeField`, `withField`, or `ReflectiveAny` is introduced. The metadata names `name`, `type`, `owner`, `index`, and `visibility` are final.

### 2.2 Enumerating actual fields

```valiance
fields[Person] foreach (field) =>
  print $field.name
  print $field.type
end

$person fields foreach (field) =>
  inspectValue($field.type, $person[field])
end
```

- Enumerate actual declared instance storage fields in declaration order, including private metadata.
- Do not fabricate storage fields for methods, constructors, or computed properties with no backing storage.
- Seeing a private field's descriptor does not grant permission to read its value.
- The conservative `@nodup`-possibility gate applies **even to metadata-only enumeration**.
- `fields[T]` is valid for **all types**. A fieldless object, primitive such as `Int`, or another type without declared instance storage fields returns an **empty field collection**, not a compile-time error.
- The element type of an empty field-derived union has no inhabited alternatives (conceptually bottom/`Never`); its exact type-system representation is an implementation detail to reconcile with Valiance’s existing bottom type.
- Field reflection on non-object types does not fabricate object fields. Other kinds of metadata, such as variant cases or union alternatives, have separate category-appropriate reflection APIs.

### 2.3 Every field name lookup is dynamic

```valiance
$fieldA = fields[Person]["age"]       # Dynamic lookup despite literal name
$fieldB = fields[Person][$userName]    # Same lookup semantics
```

Both have the same result category and missing-name policy. Direct lookup panics for an absent name; an explicitly fallible operation handles absence as data.

```valiance
$maybeField = field?(fields[Person], $userName)
# Field[Person]?; None when missing
```

No compiler-visible literal-name-specialization exception is permitted, although the compiler may optimize runtime lookup internally without changing observable semantics. On `fields[Int]` or another empty set, direct lookup always fails and optional lookup always returns `None`.

---

## 3. Values produced by reflection and CSTC

### 3.1 Concrete owner: direct field read

```valiance
object Person =>
  $name: String
  $age: Int
  $active: Bool
end

$field = fields[Person][$name]
$value = $person[$field]
# Inferred static type: String | Int | Bool
```

Even if `$name` is the literal `"age"`, the standalone dynamic read has the same conservative possible-value union. At runtime the value's **actual** concrete type evidence is retained. Use ordinary `match`, `as?`, and checked `as!` to narrow that union. Do not add reflection-specific narrowing syntax or silently invent different overload rules for reflective unions.

### 3.2 Reflective `foreach`: per-invocation generic type, not union

```valiance
$person fields foreach (field) =>
  $value = $person[field]
  # Here $value has the current field's actual generic type T.
  # It is not statically String | Int | Bool inside this callback.
  process($value)  # Generic analysis uses this field's T.
end
```

The compiler treats the callback as generically valid for the field's actual type on each invocation. The runtime invokes it with corresponding type evidence. There is **no public `withField`**: type opening and callback invocation are internal mechanisms.

### 3.3 Generic reflective results trigger CSTC

```valiance
define[T] collectValues(obj: T) =>
  unsafe =>
    $results = []
    $obj fields foreach (field) =>
      $results append $obj[field]
    end
    $results
  end
end

$values = collectValues($person)
# Conceptual inferred return: List[String | Int | Bool]
```

The generic function's reflective return relationship is deferred through CSTC until a concrete call-site owner is known. **Unknown iteration length is not unknown element type**; list element types can be a union even when list length is dynamic.

The trigger is **not limited to explicitly returned lists**. Whenever reflective iteration or a standalone dynamic read can affect an inferred output type through multiple field-dependent possibilities, CSTC computes an upper-bound output type for the call site. This applies to returns, assigned values escaping a callback, nested generic results, and ordinary composed expressions as appropriate to the compiler's stack/type rules.

### 3.4 Include every possible result type

```valiance
define[T] sometimesCollect(obj: T) =>
  unsafe =>
    $result = []
    $obj fields foreach (field) =>
      if shouldInclude($field.name) =>
        $result append $obj[field]
      end
    end
    $result
  end
end

# At a call site with Person:
# List[String | Int | Bool], even if some fields are skipped at runtime.
```

For a transformation such as `transform($obj[field])`, collect **all possible transformed result types**, rather than blindly unioning the input field types. Type alternatives can be excluded only when the compiler proves them impossible (for example through sound static narrowing), not because a runtime condition *usually* filters them.

### 3.5 Nested generic calls propagate CSTC

```valiance
define[U] transform(value: U) =>
  # Generic work on this U.
end

define[T] collectTransformed(obj: T) =>
  unsafe =>
    $out = []
    $obj fields foreach (field) =>
      $out append transform($obj[field])
    end
    $out
  end
end
```

CSTC propagates concrete possibilities through `transform` and any further generic calls. Inside reflective `foreach`, each invocation retains its specific generic field type. The output union is computed at the concrete call site from every possible resulting specialization.

**Required compiler capability:** Extend existing CSTC beyond its current documented callable/variadic triggers to field-dependent inferred outputs, including propagation through **nonrecursive** nested generic functions. Existing `@recursive`/explicit-signature rules prohibit relying on inferred recursive returns; no reflection-specific recursive CSTC inference is required. This is **new compiler work**, not a feature claimed to exist already.

### 3.6 Type-check every possible field

```valiance
define[T] printLengths(obj: T) =>
  unsafe =>
    $obj fields foreach (field) =>
      print length($obj[field])
    end
  end
end

printLengths($person)
# Must fail at compile time if length is invalid for Int or Bool.
```

The compiler must reject the call whenever **any** possible field type makes an operation invalid. A runtime error is not an acceptable substitute for an invalid call that CSTC can prove at compilation. Runtime checks remain for inherently dynamic facts (e.g., field-name existence and permission). The surrounding `unsafe` acknowledges possible `@nodup` involvement; it does not make `length(Int)` or `length(Bool)` valid.

### 3.7 Zero-field owners and empty unions

`fields[T]` returns an empty field collection when `T` has no stored fields, including primitive types such as `Int`. Reflective `foreach` then invokes its body zero times; generic callback validation has no field instances to check for that owner. A direct name lookup on the empty set panics, while fallible lookup yields `None`.

If a static return type depends exclusively on values drawn from such a zero-field iteration, there are zero possible field-value types (conceptually the bottom/`Never` union), rather than a fabricated `Any`, `Unit`, or arbitrary value type. The exact bottom-type spelling/lowering should follow Valiance's existing type algebra. Ordinary control-flow rules still apply: a function that cannot produce a return value on the empty path must not be silently considered to return a value.

### 3.8 Closed compiled type universe (initial scope)

In the initial reflection model, generic calls ultimately originate from concrete compiled type instantiations and their known field shapes. CSTC can thus enumerate the finite possible field types at reachable concrete calls. The runtime still needs reified type evidence and executable generic behavior to choose and run the actual field specialization.

**Out of initial scope:** runtime-generated new Valiance type definitions, dynamic schema compilation into new types, and modules introducing previously unknown type definitions after compile time. If such features are introduced later, this contract must be extended rather than silently manufacturing an erased `Any` fallback.

---

## 4. Reflective reads, writes, casts, and conversions

### 4.1 Reflective reads

```valiance
$field = fields[Person][$name]
$value = $person[$field]
```

Reading a field follows ordinary Valiance member-access semantics: a borrow is not an owned duplicate; materializing another independent occurrence is subject to ordinary duplication constraints; moves/consuming access are separate operations. A direct read can panic for invalid access permissions. A separate fallible read operation (name to be decided) may return a typed access error.

A generic direct read may have a conservative union static type, but its runtime value still retains its exact represented type. The compiler and VM must not erase ownership requirements when forming or handling the union.

### 4.2 Direct assignment has an implicit checked `as!`

```valiance
$person[$field] = 42
```

This illustrative write assumes the lexical context has permission to write the selected field (or is inside `unsafe`) and that normal ownership rules permit reconstruction. At a dynamic reflective write boundary, this has the checked-type meaning of:

```valiance
$person[$field] = 42 as![$field.type]  # Conceptual lowering
```

`as!` is Valiance's **checked runtime cast**. If the assigned value satisfies the target field type, the write proceeds under ordinary reconstruction and ownership rules; otherwise the cast **panics**. It does not invoke `to` conversion. The conceptual lowering is about semantics, not mandatory source rewriting or a promise that `$field.type` is presently accepted in generic syntax.

This is the sole deliberate dynamic-reflection extension of ordinary assignment checking: the destination type is discovered dynamically and checked at runtime. Otherwise the **same existing compatibility, tag/refinement, and ownership rules** apply. If the compiler can prove an operation invalid, it should diagnose it at compile time.

### 4.3 Explicit `to` remains an opt-in conversion

```valiance
$person[$field] = "42" to[$field.type]
```

This **explicitly** requests an ordinary Valiance `to` conversion using the destination's runtime type descriptor. The resulting value must still satisfy the field's type and ownership requirements. This requires runtime type descriptor support in conversion dispatch; it is design syntax, not current compiler behavior.

An explicit `to` conversion can execute conversion logic and have its normal failure/effect behavior. Reflective assignment must **never** silently search for or invoke such conversions.

### 4.4 Access faults and fallible APIs

Outside lexical `unsafe`, reflective read and write checks visibility dynamically; denial normally panics. Inside lexical `unsafe`, visibility checks are skipped. The descriptor itself carries no permission token that can be used to launder private access elsewhere.

The public names and return categories are finalized:

- Fallible field lookup `field?(Fields[Owner], String)` yields `Field[Owner]?` (`None` when the name does not exist).
- Fallible field read yields `Result[ValueType, ReflectionReadError]` for recoverable failures such as permission denial. For dynamically selected fields, `ValueType` is the same conservative CSTC-inferred union as a direct read. The recoverable failure type is `ReflectionReadError`.
- A **non-consuming**, fallible field reconstruction yields `Result[Owner, ReflectionWriteError]`. A recoverable permission denial or failed checked cast yields a reflection-specific error. `Ok(newOwner)` contains the reconstructed value; `Err(error)` contains no owner, and the original remains available and unchanged. A failed write must discard any partial replacement.

Named public fallible API (illustrative signatures):

```valiance
$maybeField = field?(fields[Person], $name)  # Field[Person]?
$read = read?($person, $field)                  # Result[FieldValueUnion, ReflectionReadError]
$written = write?($person, $field, $value)      # Result[Person, ReflectionWriteError]
```

A write may still fail irrecoverably if it attempts prohibited duplication; **no fallible API may turn `DuplicationFault` into `Err` or allow execution to resume**. `Result` is for recoverable dynamic type/access failures, not for ownership-integrity violations. Normal immutable assignment/rebinding syntax can be layered on top of the same reconstruction mechanism.

### 4.4.1 Named recoverable errors and direct faults

**Final public API names and signatures** (illustrative stack-neutral call notation):

| Operation | Public form | Success type | Failure |
|---|---|---|---|
| Direct field lookup | `fields[T][name]` | `Field[T]` | `KeyFault` on absent name |
| Optional field lookup | `field?(fields[T], name)` | `Field[T]?` | `None` on absent name |
| Direct reflective read | `owner[field]` | CSTC-derived field-value union | Panic on recoverable access failure |
| Fallible reflective read | `read?(owner, field)` | `Result[Value, ReflectionReadError]` | `Err` |
| Direct reflective write | `owner[field] = value` | Reconstructed owner binding | Panic on recoverable access/type failure |
| Fallible reflective write | `write?(owner, field, value)` | `Result[Owner, ReflectionWriteError]` | `Err`; no new owner |

**Error types:** Both `ReflectionReadError` and `ReflectionWriteError` implement Valiance's ordinary recoverable `Err` protocol. The named failure reasons are:

- `ReflectionReadError`: `AccessDenied`, `InvalidField`.
- `ReflectionWriteError`: `AccessDenied`, `TypeMismatch`, `InvalidField`.
- `ConstructionError`: `MissingFields`, containing the missing declared fields; returned by `finish?`.

`InvalidField` is a defensive owner/descriptor mismatch (or other invalid descriptor identity). The compiler must reject statically provable owner mismatches; the runtime checks mismatches it cannot prove impossible. For `read?`/`write?`, `InvalidField` validation occurs before permissions; otherwise `AccessDenied` takes precedence over `TypeMismatch`. `TypeMismatch` uses the ordinary checked `as!` compatibility rules. Precise error payload storage and message formatting are runtime concerns; the named reasons and their distinction are part of the API.

**Direct-failure fault names:** A direct reflective access failure uses `ReflectionAccessFault` (`AccessDenied`) or `ReflectionFieldFault` (`InvalidField`); incompatible direct reflective assignment uses `ReflectionTypeFault` (`TypeMismatch`). Missing direct name lookup uses the existing `KeyFault`, not a second lookup-specific fault. Plain incomplete `finish` uses `IncompleteConstructionFault`. All named direct faults implement `Fault` and follow the language's ordinary panic rules. The existing `DuplicationFault` remains a separate **unrecoverable integrity failure** and must never become any of these errors or a catchable ordinary reflection fault.

Function names ending in `?` are lexically valid in the provided Valiance lexer. The call forms above use parenthesized argument syntax for clarity; no distinct reflection calling convention is created. `field?`, `read?`, and `write?` do not rename or alter existing unrelated I/O operations.

### 4.5 Failure precedence and observable effects (confirmed contracts)

For fallible reflective reads and writes, and for the corresponding direct forms where applicable:

1. **Evaluate the receiver, descriptor, and supplied value expression according to existing Valiance evaluation rules.** Side effects or failures in an explicitly supplied conversion or other argument expression are not automatically intercepted or rolled back by reflection.
2. **Establish the descriptor/receiver relationship** and any mandatory representation preconditions. This is not a permission bypass; it ensures the operation addresses a valid field on the supplied owner. A malformed/mismatched descriptor uses `ReflectionFieldFault` for direct access or the `InvalidField` reason in the corresponding recoverable error.
3. **Check field visibility permission** at the lexical operation site; this check is skipped inside lexical `unsafe`. If permission is denied, return a dedicated recoverable reflection access error (or panic in the direct form).
4. **For writes, check destination type compatibility** using the implicit checked `as!` semantics. A failure returns a dedicated recoverable reflection type error (or panics in the direct form). If *both* permission and type compatibility would fail, **permission denial takes precedence**, after evaluation of caller expressions.
5. **Reconstruct and publish only on success**, respecting existing ownership checks and cleanup rules. Return `Ok(newOwner)` from a fallible non-consuming write, or expose the reconstructed owner through normal indexed-assignment semantics. A recoverable failure leaves the original owner intact and does not publish a partial replacement.

An explicit `to[$field.type]` conversion is an ordinary expression, not a service that `write?` invokes implicitly. Its own failures and side effects follow existing conversion rules. A prohibited duplication is never reclassified as a recoverable reflection error: VM ownership checking remains mandatory wherever an extra owned occurrence would be created, including during evaluation or reconstruction.

**The error type names and failure reasons are fixed in §4.4.1 above.** Their low-level representation and exact diagnostic text are implementation details.

---

## 5. `@nodup` and runtime integrity

### 5.1 Conservative, transitive unsafe requirement

If the compiler cannot establish that **no `@nodup` can possibly be involved**, any reflection operation requires `unsafe`. The proof considers the reflected owner, stored fields recursively, nested records, collections, generic arguments, and unknown type parameters; recursive type graphs terminate by tracking already-visited type identities. This applies even if the operation merely enumerates metadata.

```valiance
define[T] inspect(value: T) =>
  unsafe =>
    $value fields foreach (field) =>
      print $field.name
    end
  end
end
```

This `unsafe` requirement asks the programmer to consciously acknowledge the possibility of protected ownership. It does **not** imply a duplication attempt occurred or is permitted.

### 5.2 Static rejection, guaranteed runtime fallback

- If the compiler proves a prohibited duplication, **reject compilation**.
- If ownership behavior cannot be proven statically, the VM checks at the actual new-occurrence/materialization point.
- An illegal duplication must **never succeed** because static information was insufficient.
- `unsafe` cannot bypass this enforcement.
- Borrow/observation and proper ownership transfer are not duplications and must not produce spurious faults.
- Reconstructed or temporary state must not be published in an invalid partially-retained ownership state if a check fails.

### 5.3 `DuplicationFault` means an unrecoverable integrity failure

**Confirmed intention:** A detected `DuplicationFault` must not be catchable and resumable as an ordinary language panic, nor be convertible to a recoverable `Result`. It represents a fundamental guarantee that static analysis could not fully enforce. Its exact process-versus-task termination/unwinding policy belongs to the general runtime fault specification; reflection does not decide that policy.

**Repository compatibility note:** The uploaded VM currently raises a `PanicSignal` carrying a `DuplicationFault` from `_check_duplication_allowed`. Making this fault absolutely unrecoverable may require adjusting general panic-handling behavior. Do not claim that the current VM already enforces the stronger design intent in every handler configuration.

### 5.4 Immutable updates

Reflective indexed updates use the same ordinary object reconstruction model as direct member assignment. If preserving untouched fields would require an illegal duplicate, compilation rejects the operation when possible, otherwise the VM refuses it with `DuplicationFault`. `unsafe` never silently changes a persistent reconstruction into a consuming move. Explicit consuming reconstruction is a separate potential future API.

---

## 6. Visibility and `unsafe`

Field metadata is enumerable, including names/types of private fields, subject to the `@nodup` gate. Private **values** are not thereby exposed.

- Outside `unsafe`, field access permissions are checked dynamically at the reflective operation site; denied access panics (or returns an access error through an explicitly fallible API).
- Inside lexical `unsafe`, those visibility checks do not occur; private fields can be read/written reflectively.
- A descriptor created in `unsafe` does not provide privilege to unrelated safe code later.
- Calling an ordinary helper from `unsafe` does not automatically authorize the helper's internal reflective accesses. **Confirmed:** privileges are determined by the lexical body that performs each reflective operation, including callbacks and helpers; caller privileges are never implicitly inherited.
- Permission bypass does not bypass validated type, field owner, initialized storage, ownership, or cleanup checks.

A safe serializer must not serialize every private field by default. Its field-inclusion policy must be explicitly designed; generic metadata enumeration is not a disclosure policy.

---

## 7. `Uninit[T]`: unsafe structural construction

### 7.1 Intended ergonomic API

```valiance
unsafe =>
  $builder = uninit[Person]()
  $builder[fields[Person]["name"]] = "Ada"
  $builder[fields[Person]["age"]] = 36
  $builder[fields[Person]["active"]] = true

  if finished? $builder =>
    $person = finish $builder
  end
end
```

All dynamic field writes are legal **without public `withField`**; the implicit checked cast validates each assigned value against its target field type. All builder operations are inside `unsafe` by design, including `finished?`.

### 7.2 Boundaries

- Initially available only for compiler-known managed object storage; opaque native representations are deferred.
- `Uninit[T]` is a distinct ownership-carrying construction state, **never** an ordinary `T`.
- An initialization bitmap or equivalent tracks each real declared field.
- Every assigned value is checked for representation/type compatibility and lawful ownership transfer/materialization.
- Reading an unset slot as `T` is forbidden even in `unsafe`.
- A structurally complete builder deliberately need not satisfy constructor-only or external-resource invariants.
- Abandoning a builder cleans up initialized fields only; never invoke the incomplete owner's ordinary destructor.

### 7.3 Completion and readiness

| Expression | When complete | When incomplete | Consuming | Context |
|---|---|---|---|---|
| `finished? builder` | `true` | `false` | No | `unsafe` |
| `finish builder` | `T` | Panic | **Yes**, including failure | `unsafe` |
| `finish? builder` | `Ok(T)` | `Err(ConstructionError)` | **Yes**, including failure | `unsafe` |

`finished?` checks stored-field completeness only; it does not test constructor invariants, native validity, or readiness for arbitrary semantic use. A `false` result leaves the builder available for additional assignments. Neither finalizer leaves a repairable builder after a failed attempt. Replacing an initialized field keeps it initialized; no public field-unsetting operation has been specified.

Both finalizers share one completeness predicate, transfer initialized ownership on success without making illicit duplicates, and clean up initialized fields exactly once on failure. `finish?` returns `ConstructionError`; `finish` panics with `IncompleteConstructionFault` on incompleteness.

### 7.4 Unsafe constructor bypass and serialization

It is intentional that `finish` can construct a storage-complete object without running a constructor whose validation may depend on values wholly unrelated to fields. That is why the operation is unsafe. A JSON decoder can explicitly choose this capability, but parsing failures and malformed input should remain recoverable JSON results rather than automatically becoming panics.

---

## 8. Reflection categories beyond objects

The `fields` operation exists on **every type**. For nominal managed objects it enumerates declared instance storage fields, including concrete generic instantiations. For primitives (`Int`), fieldless objects, and other types without actual stored fields, it returns an **empty collection**. A category does not acquire fabricated stored fields merely by supporting `fields`.

Further metadata APIs may cover primitives, tags/refinements, optional and union types, variants, anonymous records, lists, dictionaries, and resource categories. A variant case, list element, or union alternative is not necessarily a declared object storage field. Category-appropriate APIs can share `Type[T]` evidence and CSTC where appropriate.

The first implementation must not claim support for arbitrary FFI-backed layout construction or runtime-defined novel Valiance types.

---

## 9. Compiler and runtime implementation proof obligations

1. Represent exact field identities, owner types, actual generic field types, visibility, order, and initialized storage layout.
2. Compile heterogeneous reflective `foreach` so each invocation retains its **actual `T`**, never a synthetic union in the body.
3. Preserve exact runtime type evidence through generic invocation and checked dynamic assignment.
4. **Extend CSTC** to defer field-dependent outputs until the reflected owner is concrete at a call site; propagate the requirement through nested generic calls, subject to Valiance's existing requirement for fully specified recursive-function signatures.
5. Enumerate all possible field types at CSTC sites; reject an operation if it is invalid for any possible type.
6. Compute conservative unions of all possible direct-read or escaped transformed result types, including branches that might not execute at runtime.
7. Integrate existing `match`, `as?`, checked `as!`, union normalization, normal overload behavior, and stack-effect inference without treating reflection as universally erased/dynamic; handle zero-field sets as empty/bottom-typed outcomes.
8. Use ordinary checked `as!` compatibility at reflective write boundaries; support explicit runtime-descriptor-directed `to` conversions as a distinct operation.
9. Support `fields[T]` for every type, including empty sets on primitives; make direct missing-field lookup panic and fallible lookup return `Option`. Check visibility at each lexical reflective access site; bypass visibility only for lexically privileged operations.
10. Enforce conservative transitive `@nodup` gates statically; apply existing runtime duplication checks at each actual extra-occurrence point.
11. Make `DuplicationFault` non-recoverable in the intended runtime model, resolving any conflict with current `PanicSignal` handling separately.
12. Implement managed `Uninit[T]`, complete/partial state, safe resource cleanup, `finished?`, `finish`, and `finish?` behind lexical `unsafe`.
13. Respect the existing recursion contract: any recursive function requires explicit, complete parameter and return types; deferred CSTC inference must not be used to infer a recursive signature. Cyclic stored-type graphs encountered by metadata or transitive `@nodup` checks must still be visited with finite, terminating graph traversal.
14. Preserve ordinary object reconstruction and storage/ownership invariants after failed reflective updates or incomplete construction; recoverable non-consuming writes return `Result[Owner, Error]` and never change the original on `Err`.

### Prototype order

**R0:** Type/field metadata for a two-field concrete object, including generic owner identity.  
**R1:** Heterogeneous `foreach`, keeping actual `T` inside callback; verify generic operations and stack semantics.  
**R2:** CSTC over reflective outputs, including nested generic calls, complete unions, and compile-time rejection.  
**R3:** Dynamic indexed selection and reads; union typing; checked writes and explicit conversion.  
**R4:** Permission checking and lexical unsafe; transitive `@nodup` proof; runtime ownership integrity.  
**R5:** `Uninit[T]`, dynamic field population, readiness, finalization, partial destruction.  
**R6:** Broader type forms and `std.json` proof.

---

## 10. Acceptance tests (design targets, not current test results)

### Dynamic lookup and reading

1. Literal and computed field names use the **same dynamic** name-lookup semantics and descriptor category.
2. Missing direct field lookup panics in both cases; separate fallible lookup returns `None`.
3. Direct read from an owner with `String`, `Int`, and `Bool` fields statically produces the normalized union of all three possibilities.
4. A literal field name does **not** narrow that direct-read union by itself.
5. Runtime values retain exact type evidence and owner/field identity.
6. Private metadata remains enumerable; safe direct private-value access is denied dynamically.

### `foreach`, CSTC, and generic execution

7. Heterogeneous `foreach` invokes a generic callback once per actual field with **its own actual `T`**.
8. The callback does not see the union of all owner fields as its local field type.
9. Collecting field values from `Person` returns an appropriately normalized list-element union.
10. Conditional collection includes every field type that could contribute, including branches not taken for particular runtime inputs.
11. Applying a generic transform builds the union of all *transformed result types*, not blindly the inputs.
12. Nested generic calls correctly propagate field-dependent CSTC until a concrete call site.
13. A callback calling an operation invalid for even one possible field type fails at compile time.
14. Fieldless objects and primitives such as `Int` yield zero fields; reflective foreach runs zero iterations, and field-derived unions contain zero alternatives (conceptual bottom).
15. Generic runtime invocation correctly dispatches using the actual field type evidence without exposing `withField`.

### Type checking, ownership, visibility

16. Direct `$obj[field] = value` uses checked `as!` to the actual destination field type and panics on mismatch.
17. Explicit `value to[$field.type]` uses only ordinary registered `to` conversions, not hidden assignment coercions.
18. Unsafe reflective access bypasses private visibility checks but not type, owner, or ownership checks.
19. Safe generic reflection is rejected if the compiler cannot rule out transitive `@nodup`, even if metadata-only.
20. Borrowing a reflected `@nodup` field does not create a duplicate.
21. An illegal duplicate known statically fails compilation; an otherwise unprovable illegal duplicate is caught by the VM before publishing another owner.
22. `DuplicationFault` cannot be converted to `Err` or recovered from to resume execution (general fault policy implementation required).
23. Reconstruction failing an ownership check does not corrupt the original object or release/retain a resource twice.
24. Ordinary `match`, `as?`, and `as!` narrow reflectively inferred unions without special reflection-only syntax.
25. Fallible `read?` operations return `Result` for recoverable reflection access failures using `ReflectionReadError`.
26. Non-consuming fallible writes return `Ok(reconstructedOwner)` or `Err(error)` for permission/type failures; the original owner remains unchanged on both branches and no owner appears inside `Err`.
27. When permission denial and type incompatibility both apply to a reflective write, the **permission error takes precedence** after normal operand evaluation.
28. A failed checked cast produces a dedicated reflection type error through a fallible write; direct assignment panics, and neither form implicitly invokes `to`.
29. Reflection inside helpers and callbacks uses the permissions of the lexical operation body, never caller-inherited unsafe authority.
30. Generic reflection examples (even metadata-only) require `unsafe` when generic arguments are unconstrained; lexical `unsafe` never suppresses CSTC compile-time type errors.
31. Missing fields remain dynamically handled even when a name is written as a literal; direct lookup panics and fallible lookup returns `None`.
32. A recursive function cannot use deferred inferred reflective returns in place of its mandatory explicit recursive return type; recursive stored-type graphs do not cause unbounded metadata traversal.

### Managed incomplete construction

33. Builder creation, assignment, `finished?`, `finish`, and `finish?` all require lexical `unsafe`.
34. Direct dynamic builder assignments work without public `withField`; wrong-value types fail checked casts.
35. `finished?` is non-consuming, false until all fields are assigned, true afterward.
36. `finish` succeeds with complete storage, panics when incomplete, and consumes the builder in either case.
37. `finish?` returns `Ok(T)` or `Err(ConstructionError)` and consumes the builder either way.
38. Partial abandonment or failed finishing destroys initialized fields exactly once, never unset fields or the incomplete owner as an ordinary complete object.
39. A finalized object may deliberately violate a constructor-only semantic invariant, while remaining structurally storage-complete.

---

## 11. Remaining work and the final design step

The core surface-language semantics and final public API names are now decided. No further naming review is required before implementation. Compiler prototyping may still reveal genuine feasibility conflicts, which should be documented rather than silently modifying these rules.

**Implementation contracts (not proposed new decisions):**

1. **CSTC integration:** Add reflection-dependent return inference and propagation to existing CSTC. Use ordinary recursive-signature restrictions rather than inventing recursive CSTC inference. Metadata traversal on recursive stored-type graphs must terminate.
3. **Zero-field type algebra:** Represent empty field-derived unions using existing bottom/`Never` semantics; preserve correct empty-execution stack behavior.
4. **Evaluation and fault mechanics:** Implement the confirmed evaluation order, the finalized error/fault categories, atomic reconstructed-owner publication, and disposal of failed partial replacements. Internal payload layout and diagnostic phrasing remain implementation details.
5. **Identity and ABI:** Stable descriptor identity inside one compiled program is sufficient initially; cross-program and hot-reload identity are deferred.
6. **Fatal ownership-fault policy:** Precisely how a non-recoverable `DuplicationFault` halts task/process execution remains a separate general VM policy, not a reflection API question. The current VM's `PanicSignal` representation must be checked against the non-recoverability guarantee.

**Deferred features (not initial implementation blockers):**

7. **Consuming reconstruction:** No separate move-based reflective write API in the first version.
8. **Broader reflection and JSON policy:** Variant cases, union alternatives, tag metadata, and serializer field-inclusion defaults get category-appropriate specifications later.

---

## Final naming review — v1.0

The user selected `Field`, `Fields`, `field?`, `read?`, `write?`, `Uninit`/`uninit`, and reflection-prefixed error names. The final pass retains `Type[T]`, `fields[T]`, `$value fields`, `$field.name/.type/.owner/.index/.visibility`, and `finished?`/`finish`/`finish?`. It adopts `Visibility`, the named failure reasons and dedicated fault family in §4.4.1, and `ConstructionError`/`IncompleteConstructionFault`. No public `SomeField`, `ReflectiveAny`, or `withField` is introduced.

The existing Valiance FFI `Unsafe` **element tag** is a distinct concept from the proposed lexical `unsafe` reflection block; the implementation must integrate them without silently giving a callee reflective privileges from its caller. This is a compiler integration note, not a new language-design decision.

## 12. Compatibility notes and change log

### Differences from v0.8

- **Confirmed dedicated reflection-specific recoverable error categories**, with final names and payloads deferred to the final API naming review.
- **Confirmed permission checking before checked-cast compatibility** for reflective writes after ordinary expression evaluation. Failed non-consuming writes do not publish a new owner or mutate the original.
- **Applied the existing recursion rule:** fully typed recursive functions cannot rely on reflection-driven inferred recursive signatures; the old special CSTC recursion-design question is closed.
- **Corrected generic reflection examples** to include lexical `unsafe`, as required for unconstrained generic `T`.
- **Clarified dynamic-only field names** even for missing string literals, and separated static generic-operation errors from runtime name lookups.

### Differences from v0.7

- **Confirmed `fields[T]` for every type:** fieldless and primitive types yield empty collections; a field-derived union with no alternatives is conceptually bottom/`Never`.
- **Confirmed existing narrowing only:** use normal `match`, `as?`, and checked `as!`; do not invent reflection-specific narrowing syntax.
- **Fixed fallible return categories:** optional field lookup (`Option`); fallible reads and non-consuming writes (`Result`); no recovery from `DuplicationFault`.
- **Clarified immutable write semantics:** successful fallible write returns a newly reconstructed owner; failed writes return an error, never the original owner inside that error, and leave the original untouched.
- **Confirmed lexical unsafe boundaries across callbacks and helpers:** no privilege inheritance from the caller.
- **Deferred consuming reconstruction** and reserved the final public-naming pass for the last design step before implementation.

### Differences from v0.6

- **Removed public `withField` entirely**; preserve its scoped-generic effect internally in reflective iteration and runtime generic calls.
- **Do not introduce public `ReflectiveAny`** for the closed compiled type universe.
- **Added CSTC-based union inference:** `foreach` retains per-field `T` locally; direct reflective reads and escaped field-dependent results obtain conservative unions at concrete call sites; nested generic functions propagate CSTC.
- **Made conservative soundness mandatory:** include all possible field/transformed types; compile-time reject a callback invalid for any possible field type.
- **Confirmed direct dynamic assignment** without opening a descriptor and with implicit **checked `as!`**, plus optional explicit runtime-targeted `to` conversion.
- **Clarified that `as!` is checked** (panics on failed cast), not an unchecked assertion.
- **Confirmed transitive, conservative `@nodup` unsafe gating**, including generics, containers, and metadata-only use.
- **Confirmed intended non-recoverability of `DuplicationFault`**, while noting a possible gap between this stronger contract and existing VM `PanicSignal` handling.
- **Confirmed all `Uninit[T]` operations, including `finished?`, require `unsafe`**.
- **Retained consuming `finish`/`finish?` and non-consuming `finished?`** exactly as settled.

### Existing compiler/runtime baseline versus proposed work

The uploaded Valiance repository documents standard `foreach` over list types, existing `as!` checked-cast and `to` conversion conventions, CSTC for specific callable/variadic patterns, and a VM `_check_duplication_allowed` fallback. The heterogeneous reflective `foreach`, deferred reflection-driven return typing, runtime-descriptor-directed generic invocation, explicit runtime conversion targets, and `Uninit[T]` machinery require implementation work. **No design snippet in this specification has been compiler-verified.**

---

## 13. Consistency audit record (v0.9)

**Audit inputs:** Consolidated v0.8, the user's subsequent decisions about CSTC recursion and recoverable failures, and the accompanying Valiance language/compiler documentation. This audit is **not a compiler execution test**.

| Finding | Resolution | Status |
|---|---|---|
| Generic reflective functions were shown outside `unsafe` despite the conservative `@nodup` rule. | All affected examples now explicitly enter lexical `unsafe`. | Corrected |
| Recursive CSTC analysis was marked open despite Valiance requiring fully specified recursive signatures. | Existing recursion rule is normative; only finite stored-type graph traversal is implementation work. | Corrected |
| Fallible reflective errors were left general/provisional after dedicated reflection errors were chosen. | Dedicated error categories are now mandatory; only final spellings/payloads remain. | Corrected |
| Permission-versus-type precedence was not stated as an operative rule. | Permission precedes checked casting after ordinary input evaluation and descriptor validation. | Corrected |
| A broad compile-time rejection clause could be read as rejecting missing *literal* field names, despite dynamic-only lookup. | Missing names always follow dynamic lookup semantics; compile-time checking still applies to provably invalid generic operations/ownership. | Clarified |
| Example direct write could be misread as always permitted despite default object visibility. | The example now states the access/ownership preconditions. | Clarified |

**Audit outcome:** No remaining conflict was found among the confirmed *core* semantic rules after these corrections. The unimplemented compiler/runtime obligations are explicitly identified above. The v1.0 naming pass finalizes the public spellings, but this paper does not itself verify implementation correctness.
