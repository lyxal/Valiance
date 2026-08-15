# Valiance feature checklist

Current audit total: 674 / 803 items complete (83.9%).

Audit refreshed against the repository implementation and full test suite on 2026-07-25. Items remain unchecked where the feature is absent, deliberately deferred, only partially implemented, or too broad to treat as complete.

## 1. Lexer, parser, and general syntax

- [x] Parse source as left-to-right chains that execute right-to-left.
- [x] Recognise all chain-breaking constructs.
- [x] Include nilads and functions in the chains they terminate.
- [ ] Support `_` substitution for values pulled from the parent stack.
- [x] Support newline-sensitive expression termination.
- [x] Support `|` as a chain separator.
- [x] Support single-line comments beginning with `#?`.
- [x] Support nested, balanced multiline comments using `#/` and `/#`.
- [x] Implement the common single-line and multiline `=> ... end` block syntax.
- [x] Determine block form from the first significant token following `=>`.
- [x] Produce lexer errors for unterminated strings.
- [x] Produce syntax errors for unbalanced comments and blocks.
- [x] Parse element identifiers using the specified symbolic and alphanumeric character rules.
- [x] Require niladic element names to begin with `\`.

## 2. Core execution and stack semantics

- [x] Provide a top-level operand stack.
- [x] Execute elements by popping their declared arity and pushing their declared multiplicity.
- [x] Preserve the specified argument ordering when stack values are passed to elements.
- [x] Support zero-arity and zero-multiplicity elements.
- [x] Enforce fixed arity and multiplicity across every overload of an element.
- [x] Support state semantics for control-flow blocks that may write parent-scope variables.
- [x] Preserve extra stack values where constructs specify that only the top result is consumed.
- [x] Implement stack-underflow handling according to each construct’s rules.
- [x] Implement function-local stacks.
- [x] Implement argument cycling for explicitly declared function parameters.
- [ ] Implement argument cycling for loop inputs where specified.
- [x] Compile typed AST nodes to bytecode.
- [x] Run an extensible bytecode optimisation pipeline by default.
- [x] Fold pure constants and literal tuple/string builders.
- [x] Inline small constant functions using a configurable bytecode-size limit.
- [x] Materialise proven scalar cycle inputs as explicit parameter loads.
- [x] Simplify bytecode branches and redundant physical stack shuffles.
- [x] Allow optimisation to be disabled for a compilation.
- [x] Execute bytecode with a stack-based virtual machine.

## 3. Numbers and truthiness

- [x] Implement arbitrary-size, arbitrary-precision exact numbers.
- [x] Support integer runtime values.
- [x] Support real-number runtime values.
- [x] Support complex numbers.
- [ ] Support symbolic or exact representations involving π, e, surds, and similar values.
- [x] Parse decimal and signed numeric literals.
- [x] Parse complex-number literals.
- [x] Parse scientific notation with real-valued exponents.
- [x] Reject exponent syntax without a leading coefficient.
- [x] Provide the `Integer`, `Real`, and general `Number` types.
- [ ] Implement `Integer` as the appropriate tagged `Number` type.
- [ ] Implement `Real` as the appropriate tagged `Number` type.
- [x] Treat numeric zero as false and every other number as true at runtime.
- [x] Provide `true` as an alias for numeric `1`.
- [x] Provide `false` as an alias for numeric `0`.
- [x] Provide the `#boolean Number` type constrained to `0` or `1`.

## 4. Strings

- [x] Implement strings as dedicated objects rather than character lists.
- [x] Store or process strings as UTF-8.
- [x] Parse double-quoted string literals.
- [x] Allow literal newlines inside strings.
- [x] Support escaping quotes, backslashes, and dollar signs.
- [x] Support `$identifier` interpolation.
- [x] Support `${expression}` interpolation.
- [x] Convert interpolated values to their string representations.
- [x] Provide the `String` type.
- [x] Support string indexing and slicing.

## 5. Tuples

- [x] Implement fixed-length heterogeneous tuples.
- [x] Parse tuple literals using `{...}`.
- [x] Support nested tuples.
- [x] Represent tuple types using `{...}`.
- [x] Track tuple lengths at compile time.
- [x] Support arbitrary-length tuple parameter types using `...`.
- [x] Support repeated tuple-type segments before, between, or after fixed segments.
- [x] Restrict arbitrary-length tuple types to parameter positions.
- [x] Allow compatible fixed tuples where arbitrary-length tuple parameters are expected.
- [x] Restrict arbitrary-length tuple values to contexts expecting arbitrary-length tuples.
- [x] Trigger call-site type checking for functions containing variadic tuple parameters.

## 6. Records and dictionaries

- [x] Implement anonymous records with statically known bareword keys.
- [x] Parse `record{...}` literals.
- [x] Represent record shapes with ordinary row types; no separate record type syntax.
- [x] Support record member access.
- [x] Support record member replacement using assignment syntax.
- [x] Implement `record.extend{...}`.
- [x] Reject `record.extend` when a supplied key already exists.
- [x] Implement record merging with overwrite behavior.
- [x] Implement dictionaries with runtime-computed keys.
- [x] Parse `dict{...}` literals.
- [x] Represent dictionary types as `Dict[key, value]`.
- [x] Support dictionary indexing.
- [ ] Implement dictionary merging.

## 7. None, Some, and optional types

- [x] Provide the niladic element `\None`, which pushes the singleton absence value.
- [x] Keep the absence type named `None`.
- [x] Provide the `Some[T]` wrapper.
- [x] Parse optional types using `T?`.
- [x] Define `T?` as `Some[T] | None`.
- [x] Support nested optional types such as `T??`.
- [x] Automatically wrap non-`None` values in `Some` when used as optional values.
- [x] Preserve `None` values explicitly wrapped with `Some(\None)`.
- [x] Implement optional-type union simplification.
- [x] Canonicalise optional unions into the specified ordering.
- [x] Implement the specified `T | Some[U]` simplification rules.

## 8. Lists

- [x] Implement homogeneous lists whose element type may itself be a union.
- [x] Support finite lists.
- [x] Support potentially infinite lists.
- [x] Parse list literals using `[...]`.
- [x] Infer list base types and ranks.
- [x] Implement list-construction stack fallback when an item expression underflows.
- [x] Apply implicit fork-like behavior across list item expressions.
- [x] Pop the maximum required arity across list items during implicit construction.
- [x] Reject untyped empty-list literals.
- [x] Allow empty lists when a type annotation, cast, or explicit function return type supplies the element type and rank.
- [x] Support list indexing, slicing, multidimensional indexing, and spread indexing.
- [x] Support lazy list indexing and non-negative positive-step lazy slicing.
- [x] Support immutable list updates through indexed assignment syntax.
- [x] Support immutable slice updates through indexed and augmented assignment syntax.

## 9. Arrays

- [ ] Implement finite rectangular arrays.
- [x] Parse array literals using `arr{...}`.
- [ ] Validate rectangularity.
- [ ] Store array rank and shape.
- [ ] Preserve array results through vectorisation when all applicable inputs and outputs remain arrays.
- [ ] Produce list results when vectorisation mixes lists and arrays.
- [x] Support array indexing and multidimensional slicing.

## 10. Type system foundations

- [x] Assign a static type to every stack value.
- [x] Support concrete named types.
- [x] Support union types.
- [x] Support intersection types.
- [x] Support optional types.
- [x] Support function types.
- [x] Support overload-set types.
- [x] Support generic types.
- [x] Support trait types.
- [ ] Support object, variant, enum, record, dictionary, tuple, task, channel, result, and FFI types.
- [x] Canonicalise union and intersection types.
- [ ] Reject invalid or unsatisfiable type combinations where specified.

## 11. Ranked list types

- [x] Parse exact list-rank syntax using `+`.
- [x] Parse numeric exact-rank shorthand such as `T+3`.
- [x] Parse minimum list-rank syntax using `*`.
- [x] Parse numeric minimum-rank shorthand such as `T*3`.
- [x] Parse rugged list-rank syntax using `~`.
- [x] Parse numeric rugged-rank shorthand.
- [x] Enforce exact-rank compatibility.
- [x] Enforce minimum-rank compatibility.
- [x] Enforce rugged-rank compatibility.
- [x] Allow exact-rank lists where compatible minimum-rank lists are expected.
- [x] Allow minimum-rank lists as exact-rank call parameters when the minimum rank is high enough, without making them assignable.
- [x] Allow exact- or minimum-rank lists where compatible rugged lists are expected.
- [ ] Represent rugged rank as a compile-time abstraction over recursive union structures.
- [x] Allow explicit rugged types to vectorise only where atomic parameters are expected.
- [x] Reject rugged-to-collection vectorisation, regardless of relative rugged rank.
- [ ] Recognise equivalent expanded-union rugged types during vectorisation.

## 12. Ranked array types

- [x] Parse exact array-rank syntax using `^`.
- [x] Parse numeric exact-array-rank shorthand.
- [x] Parse minimum array-rank syntax using `>`.
- [x] Parse numeric minimum-array-rank shorthand.
- [x] Allow compatible arrays where corresponding list types are expected.
- [ ] Support checked list-to-array treatment.
- [ ] Emit compile-time warnings for list-to-array conversions requiring runtime validation.
- [ ] Perform rectangularity checks for checked list-to-array conversions.
- [ ] Avoid redundant checks when a list type is known to have originated as an array.
- [x] Reject rugged lists where array types are expected.

## 13. Type casting

- [x] Require bracket-delimited targets for every cast form.
- [x] Make `as[T]`, `as?[T]`, and `as![T]` executable chain separators with
  the same segmentation effect as a pipe on each side.
- [x] Accept both `as[T]` and whitespace-separated `as [T]`.
- [x] Parse statically proven coercions using `as[T]`.
- [x] Parse optional runtime refinements using `as?[T]`.
- [x] Type `as?[T]` as `T?` and return `Some` or `None` at runtime.
- [x] Parse asserted runtime refinements using `as![T]`.
- [x] Perform runtime validation for `as?` and `as!`.
- [x] Reject runtime refinements whose target cannot be checked.
- [x] Reject casts whose source and target cannot overlap.
- [x] Preserve optional-cast bytecode through serialization.
- [ ] Complete checked list-to-array rectangularity validation.
- [ ] Preserve zero-cost array-to-list-to-array restoration where provenance permits.
- [ ] Support inline parameter casts.
- [ ] Support inline return-value casts.


## 14. Variables and constants

- [x] Parse inferred variable declarations.
- [x] Parse explicitly typed variable declarations.
- [x] Require every variable to be initialised.
- [x] Preserve a variable’s declared or inferred type across later assignments.
- [x] Implement mutable bindings over immutable stored values.
- [x] Restrict variables to local scope.
- [x] Support assignment expressions extending to the end of the current line or containing delimiter.
- [x] Preserve unused stack values after assignment.
- [x] Parse augmented assignment using `$name := code`.
- [x] Push the previous variable value before augmented-assignment code runs.
- [ ] Disable argument cycling for the implicitly supplied augmented-assignment value.
- [x] Parse constant declarations using `const`.
- [x] Reject reassignment of constants.
- [x] Parse multiple assignment.
- [x] Map multiple-assignment targets to corresponding stack results.
- [x] Fill missing multiple-assignment values from the existing stack.
- [x] Implement evaluation-time variable shadowing.
- [x] Create a new local binding instead of modifying an outer-scope variable.
- [x] Permit an assignment expression to read the outer binding before creating its shadow.

## 15. Elements and overloads

- [x] Represent elements as immediate stack operations distinct from functions.
- [x] Support multiple overloads per element.
- [x] Dispatch overloads based on stack argument types.
- [x] Apply the specified overload-specificity ordering.
- [x] Give tagged matches priority over equivalent untagged matches.
- [x] Require one overload to be strictly more specific across every corresponding parameter.
- [x] Report ambiguous equally specific overloads as compile errors.
- [x] Support overload disambiguation using `element{Types}`.
- [x] Support generic-equivalent matching.
- [x] Support optional-substitution matching.
- [x] Support vectorising matches.
- [x] Support intersection and trait-implementation matches.
- [x] Support rank and union matches.
- [x] Allow module-scoped overloads to supersede imported and built-in overloads.
- [x] Provide `*::<element>` syntax for explicitly accessing built-in overloads.

## 16. Element call syntax

- [x] Parse `element(arguments)` with no intervening whitespace.
- [x] Evaluate call-syntax arguments left to right.
- [x] Push evaluated arguments before invoking the element.
- [x] Allow partial argument specification.
- [x] Fill unspecified arguments from the existing stack.
- [ ] Support `_` placeholders in any argument position.
- [ ] Preserve normal left-to-right evaluation despite placeholders.
- [x] Support named arguments.
- [x] Validate named arguments against declared parameter names.
- [ ] Allow named placeholders that consume values from the stack.
- [ ] Partition stack consumption right-to-left among call-syntax expressions.
- [ ] Use only the top result of a multi-result argument expression.
- [ ] Discard remaining results from such argument expressions.
- [ ] Emit a warning when argument-expression results are discarded.

## 17. Stack-shuffling operations

- [x] Provide `dup`.
- [x] Provide `swap`.
- [ ] Provide `pop`.
- [x] Parse and execute `copy(prestack -> poststack)`.
- [x] Parse and execute `move(prestack -> poststack)`.
- [x] Support duplicate post-stack labels.
- [x] Pop every labelled pre-stack value for `move`, including unused labels.
- [x] Support `_` as an ignored pre-stack label.
- [x] Reject duplicate non-underscore pre-stack labels.
- [x] Support `_n` shorthand for repeated ignored labels.
- [x] Interpret pre-stack labels from the top of the stack.

## 18. Functions

- [x] Implement anonymous first-class functions.
- [x] Parse full `fn` syntax.
- [x] Support optional parameter declarations.
- [x] Support optional return declarations.
- [x] Infer a single top-stack return when return types are omitted.
- [x] Discard non-returned function-stack values.
- [x] Support explicitly zero-return functions.
- [x] Support multiple return values.
- [x] Execute functions on independent stacks seeded with their arguments.
- [x] Pop function arguments from the parent stack.
- [x] Support named typed parameters.
- [x] Support unnamed typed parameters.
- [x] Support named inferred parameters.
- [x] Prevent writes to named function parameters.
- [x] Prevent shadowing named function parameters.
- [x] Support function calls through the `call` element.
- [x] Support call syntax on variables containing functions.
- [x] Support explicit stack-fed function invocation.
- [x] Implement argument cycling for explicitly declared parameters.
- [x] Disable argument cycling when parameters are inferred.
- [x] Disable argument cycling for explicitly zero-parameter functions.
- [x] Implement closure capture by value.
- [x] Preserve captured values after the defining scope exits.
- [x] Reject captures of top-level assignments.
- [x] Restore captured values at the start of each closure call.

## 19. Function type inference and call-site checking

- [x] Perform forward overload inference at function definition sites.
- [x] Infer parameter constraints from operations used in function bodies.
- [x] Discard overload possibilities made impossible by later operations.
- [x] Produce overload-set function types when multiple alternatives remain.
- [x] Infer untyped named parameters from use.
- [ ] Reject unused untyped parameters.
- [x] Support generic `Function` parameters with unknown arity and multiplicity.
- [x] Defer stack-polymorphic function validation to call sites.
- [x] Validate each call independently using the concrete function argument type.
- [x] Allow call-site-checked functions to consume additional outer-stack arguments.
- [x] Trigger call-site checking for variadic tuple parameters.
- [x] Type-check `both` against two same-arity stack groups at each call site.
- [x] Type-check `sequence` against independently sized stack groups at each
  call site.
- [x] Preserve call-site-selected group arities in resolved bytecode metadata.

## 20. Vectorisation

- [x] Automatically vectorise element calls over higher-ranked arguments.
- [x] Repeatedly vectorise until every argument reaches its expected rank.
- [x] Zip arguments still above their expected ranks.
- [x] Reuse arguments that have reached their expected ranks.
- [x] Reject calls with no direct or vectorised matching overload.
- [x] Require equal lengths at each paired vectorisation dimension by default.
- [x] Raise `VectorisationFault` for runtime length mismatches.
- [x] Make `VectorisationFault` catchable by `try/handle`.
- [x] Prevent user code from producing `VectorisationFault` through `panic`.
- [x] Exclude the `Panic` element tag from intrinsic `VectorisationFault`s.
- [x] Produce lists when all vectorised arguments are lists.
- [x] Produce arrays when all arguments are arrays and the return type retains arrayness.
- [x] Produce lists when vectorisation mixes lists and arrays.
- [x] Support fine-grained vectorisation depth through overload disambiguation.
- [x] Parse exact parameter types.
- [x] Prevent vectorisation through exact parameters.
- [x] Include exact in function types.
- [x] Require declared collection rank for exact collection parameters.
- [x] Bind generic exact parameters to the whole argument type.
- [x] Broadcast exact arguments unchanged when another parameter vectorises.
- [x] Preserve per-argument automatic vectorisation depths in saved bytecode.
- [x] Preserve dynamic exact target ranks for minimum-rank call adaptation in saved bytecode.
- [x] Reify known exact list ranks on eager and lazy runtime list values.
- [x] Apply atomic-only vectorisation for explicit rugged types.
- [ ] Apply the same atomic-only rule to equivalent expanded unions.
- [x] Parse and execute `at (...) => ...`.
- [ ] Support per-argument vectorisation-depth labels in `at`.
- [ ] Support underscore depth inference in `at`.

## 21. Vectorisation extension rules

- [x] Parse `extend(default)`.
- [x] Evaluate an extension default exactly once.
- [x] Substitute the default for missing zipped values.
- [x] Validate default compatibility with every affected parameter.
- [x] Parse pattern-based `extend => ... end`.
- [x] Support present-value bindings and missing-value `_` patterns.
- [x] Select the matching extension rule for each missing-value combination.
- [x] Parse selector-based `extend: selector`.
- [x] Require selector arity to match the target element.
- [x] Pass optionalised arguments to extension selectors.
- [x] Preserve nested optional meaning for already optional target parameters.

## 22. Function-argument modifier

- [x] Parse the `element: chain` modifier.
- [x] Automatically wrap the following chain as a function argument.
- [x] Support multiple function arguments in parenthesised comma-separated form.
- [ ] Require all function-typed parameters to be supplied when `:` is used.
- [ ] Integrate `:` calls with optional function arguments.

## 23. Indexing and slicing

- [x] Parse stack indexing using `$[index]`.
- [x] Use zero-based indexing.
- [x] Support negative indices from the end.
- [ ] Dispatch indexing through the `index` overload mechanism.
- [x] Support tuple, list, array, string, and dictionary indexing.
- [x] Parse multiple indices and return the selected values as a list.
- [x] Parse direct variable indexing.
- [x] Parse inclusive slices with start, stop, and step.
- [x] Parse `::step` slice shorthand inside indexing expressions.
- [x] Apply default slice values.
- [x] Support lazy list indexing and non-negative positive-step lazy slicing.
- [x] Support multidimensional chained indices.
- [x] Support multidimensional slices.
- [x] Raise `SliceFault` for invalid multidimensional slicing.
- [x] Parse record member access using `$.member`.
- [x] Support indexed augmented assignment.
- [x] Support sliced assignment and augmented sliced assignment.
- [x] Treat indexed updates as immutable reconstruction.
- [x] Parse spread indexing using `...$[...]`.
- [x] Push statically known indexed values individually onto the stack.

## 24. Pattern matching

- [x] Parse `match` blocks.
- [x] Support matching one or more stack values.
- [x] Require every case to match the same number of values.
- [x] Support literal patterns.
- [x] Support predicate patterns using `if`.
- [x] Support list-structure patterns.
- [x] Support wildcard list positions.
- [x] Support list-rest patterns using `...`.
- [x] Support bindings within list patterns.
- [x] Support type patterns.
- [x] Support named type-pattern bindings.
- [x] Support object destructuring in type patterns.
- [x] Support guards on type patterns.
- [x] Support wildcard patterns.
- [x] Support `||` alternatives within a case item.
- [x] Pass matched values to the selected branch body.
- [x] Avoid popping additional values from the outer stack inside branch invocation.
- [x] Union corresponding branch result types.
- [ ] Pad missing branch results with `None`.
- [x] Require exhaustive matching.
- [x] Recognise wildcard cases as exhaustive.
- [x] Support exhaustive checking for variants and enums.

## 25. Assertions and conditionals

- [x] Parse `assert` blocks.
- [x] Require assertion conditions to return `#boolean Number`.
- [x] Evaluate assertion conditions by peeking rather than consuming inputs.
- [x] Panic when a basic assertion is false.
- [x] Parse `assert ... else`.
- [x] Return an `AssertError` wrapping the else result when the condition fails.
- [x] Parse single-branch `if`.
- [x] Peek condition inputs.
- [x] Optionalise the result of an `if` without `else`.
- [x] Return `None` when a single-branch `if` is not taken.
- [x] Parse `if/else`.
- [x] Require compatible input signatures across branches.
- [x] Resolve compatible overload sets across branches.
- [x] Infer the intersection of branch overload sets.
- [x] Union branch result stacks.
- [x] Pad missing branch results with `None`.
- [x] Parse `else if` chains.
- [x] Require every condition in an `else if` chain to accept the same input signature.
- [x] Evaluate every chained condition against the same values.
- [x] Require `else` to appear last.

## 26. Loops and generators

- [x] Parse `foreach`.
- [x] Require a list-type iterable.
- [x] Pop the iterable before iteration.
- [x] Support iteration-value bindings.
- [x] Support optional index bindings.
- [x] Cycle iteration inputs within each loop body.
- [x] Permit loop bodies to write parent-scope variables.
- [x] Return `None` when a `foreach` completes normally.
- [x] Parse `break value`.
- [x] Parse multi-value `break (...)`.
- [x] Pad differing break multiplicities with `None`.
- [x] Return break values from terminated loops.
- [x] Parse `while`.
- [x] Require a boolean-number condition.
- [x] Feed previous iteration results into subsequent condition checks.
- [x] Require loop-body outputs to match condition inputs.
- [x] Return the values that caused loop termination.
- [x] Parse `unfold`.
- [x] Maintain unfold state between iterations.
- [x] Support optional unfold conditions.
- [x] Support infinite unfolding when no condition is provided.
- [x] Infer state and generated values from body arity and multiplicity.
- [x] Support explicit unfold state parameters.
- [ ] Skip generated `None` values.
- [ ] Preserve explicitly generated `Some(\None)`.
- [x] Tag unfold results as `#infinite`.

## 27. Custom element definitions

- [x] Parse `define`.
- [x] Support generic parameter lists on definitions.
- [x] Support optional parameter lists.
- [x] Support optional return declarations.
- [x] Create new executable elements from definitions.
- [x] Add module-scoped overloads to existing elements.
- [x] Capture visible variables at definition evaluation time.
- [x] Support trailing optional parameters with default expressions.
- [x] Restrict optional element arguments to explicit call syntax.
- [x] Allow function-valued optionals through `:` syntax.
- [x] Support named optional arguments.
- [x] Allow omission of unrelated optional arguments.
- [x] Enforce identical arity and multiplicity across overloads.

## 28. Objects

- [x] Parse generic and non-generic object declarations.
- [x] Support statically known object members.
- [x] Support public, readable, and private member access levels.
- [x] Default omitted access modifiers to readable.
- [x] Support typed fields without defaults.
- [x] Support inferred fields with defaults.
- [x] Require every constructor path to initialise fields lacking defaults.
- [x] Treat elements named after an object as constructors.
- [x] Generate a default field-order constructor when none is declared.
- [x] Support constructor invocation through normal element syntax.
- [x] Support object-friendly elements declared inside object scopes.
- [x] Make object-friendly elements available in the intended calling form.
- [x] Support object member reads.
- [x] Support permitted object member writes.
- [x] Reconstruct immutable object values after writes.
- [x] Support optional-member access behavior.
- [x] Support deep and mixed optional-member chains.
- [x] Flatten optional-valued fields during safe access.
- [x] Cancel optional-member writes through `None`.
- [x] Preserve optional-safe field operations through bytecode serialization.
- [x] Provide `$self` in object-associated definitions.
- [x] Parse destructors named with the object’s destructor form.
- [x] Run destructors according to object lifetime rules.
- [x] Enforce destructor constraints.
- [x] Account for objects during stack copying, moving, and disposal.
- [ ] Support shared-state objects.
- [ ] Enforce shared-state access and update rules.

## 29. Traits

- [x] Parse trait declarations.
- [ ] Support required object members in traits.
- [x] Support required element signatures in traits.
- [x] Support default trait behavior where specified.
- [x] Parse object-to-trait implementations.
- [x] Validate that implementations satisfy all required members and elements.
- [x] Support generic traits and implementations.
- [x] Support trait inheritance or trait composition.
- [x] Support trait types in parameters and casts.
- [x] Support intersection types requiring multiple traits.
- [x] Support static dispatch through trait-typed values.
- [x] Integrate traits with overload resolution and multimethods.

## 30. Variants

- [x] Parse variant declarations.
- [x] Support named variant cases.
- [x] Support cases with and without associated values.
- [x] Allow variants to be used as types.
- [x] Construct variant values.
- [x] Access or destructure associated case values.
- [x] Integrate variants with exhaustive pattern matching.
- [x] Support generic variants.

## 31. Enums

- [x] Parse enum declarations.
- [x] Support enums without backing values.
- [x] Support enums with a declared backing type.
- [x] Require every member to have a value when a backing type is declared.
- [x] Support member access through `Enum.Member`.
- [x] Support backing-value access through `.value`.
- [x] Allow enum names to be used as types.
- [x] Integrate enums with exhaustive pattern matching.

## 32. Generics and unification

- [x] Parse generic parameter lists on all declaration kinds.
- [x] Parse generic parameter lists on object-like declarations.
- [x] Preserve generic object and variant constructor type arguments at runtime.
- [x] Default unknown or unsupported nominal generic constructors to invariant.
- [x] Support declaration-site covariance and contravariance metadata.
- [x] Infer object-like declaration variance from readable, writable, return,
      and function-parameter usage.
- [x] Compose inferred variance through nested nominal generic constructors.
- [x] Collect directional lower and upper evidence for concrete higher-order
      generic arguments.
- [x] Infer upper-only generics from their upper meet.
- [x] Infer normalized intersections from multiple unrelated upper bounds.
- [x] Infer reduced unions from unrelated covariant lower bounds.
- [x] Preserve the narrow numeric join `Integer | Real -> Real` during generic
      inference.
- [x] Prefer a compatible lower join when both lower and upper evidence exist.
- [x] Preserve contextual and rank/vectorisation-aware callable inference while
      applying directional solving to concrete scalar-shaped callables.
- [x] Support covariant collection item assignability.
- [x] Support trait constraints on generic parameters.
- [x] Parse the atomic overload-resolution marker.
- [x] Enforce scalar-only atomic positions without changing generic identity.
- [x] Use atomic positions as validation/fallback evidence rather than ordinary
      rank-peeling evidence.
- [x] Preserve atomic guarantees through generic function analysis without
      exposing markers as body value types.
- [x] Implement generic unification across concrete generic constructors.
- [x] Implement unification across exact, minimum, rugged, and array ranks.
- [x] Implement optional-type unification.
- [x] Respect the defined rank-zero interpretations.
- [x] Reject inconsistent generic solutions.
- [x] Avoid positional unification across unions.
- [ ] Avoid positional unification across intersections.
- [x] Support anonymous generics in function types.
- [x] Support row polymorphism for extensible record-like types.

## 33. Data tags

- [x] Parse data-tag declarations.
- [x] Support constructed tags.
- [x] Support unit tags.
- [x] Reject unit-tagged values at untagged scalar boundaries such as indexing.
- [x] Support computed tags.
- [x] Support variant tags.
- [x] Keep variants out of compile-time types while retaining runtime evidence.
- [x] Require variant parents to be computed tags.
- [x] Remove dependent variants when their parent is removed or replaced.
- [x] Apply tags to values.
- [x] Remove tags using tag-negation syntax.
- [x] Include tags in parameter and return types.
- [x] Support exact present-tag sets, including exact-empty `[]`.
- [x] Check absent-tag requirements before erasable tags are forgotten.
- [x] Give tagged overloads higher specificity.
- [x] Support tag constraints in generic contexts.
- [x] Support tag disjoint declarations.
- [x] Reject disallowed simultaneous tags.
- [x] Support tag-overlay declarations.
- [x] Apply overlay signatures without changing underlying element behavior.
- [x] Support overlays for multiple elements.
- [x] Propagate constructed tags automatically through ordinary and generic flow.
- [x] Remove constructed tags only through explicit absence, exact exclusion, rank drop, or owning-overlay removal.
- [x] Support tag validators as tag-named definitions.
- [x] Resolve validator overloads by specificity rather than declaration order.
- [x] Run parent validators when applying a variant.
- [x] Require validators to return `#boolean Number`.
- [x] Run validators when tags are applied.
- [x] Panic when tag validation fails.
- [x] Report compile errors when no applicable validator overload exists.
- [x] Eliminate checks for validators statically known to always succeed or fail.
- [x] Import tags explicitly.
- [x] Import tag overlay rules.
- [x] Support elements attached to tag imports.
- [x] Parse tag depth using repeated `+` or numeric shorthand.
- [x] Apply tags at the requested nested collection depth.
- [x] Canonicalize runtime tag evidence to declared function return tags.
- [x] Preserve Boolean runtime-pattern metadata across bytecode serialization.

## 34. Element tags and effects

- [x] Support sticky element tags that propagate to callers.
- [x] Support generic parameters on element tags.
- [x] Represent element tags after function types using `<...>`.
- [x] Represent required tag absence using `!Tag`.
- [x] Parse property element-tag declarations.
- [x] Parse companion element-tag declarations.
- [x] Support built-in property tags including `IO`, `Random`, `Panic[T]`, and `Memoizable`.
- [x] Support built-in companion tags including `Eager` and `Memoized`.
- [x] Allow users to attach property tags.
- [x] Prevent users from directly attaching companion tags.
- [x] Infer unspecified property tags.
- [x] Validate explicitly declared property-tag sets.
- [x] Reject undeclared effects used inside explicitly constrained definitions.
- [x] Allow explicitly declared effects even when no tagged operation is called.
- [x] Support element-tag disjoint rules.
- [x] Propagate effect information through functions and higher-order calls.

## 35. Annotations

- [x] Parse annotations on definitions.
- [x] Parse annotations on function literals and element invocations.
- [x] Provide an extensible Python annotation registry for built-ins and future compiler plugins.
- [x] Implement `@recursive`.
- [ ] Enforce recursive-call restrictions unless `@recursive` is present.
- [x] Implement `@self`.
- [x] Supply or transform the implicit object receiver as specified.
- [x] Implement `@@tupled`.
- [ ] Generate tuple-taking forms as specified.
- [x] Implement `@error`.
- [x] Emit compile errors using annotation-provided diagnostics.
- [x] Implement `@warn`.
- [x] Emit compile warnings using annotation-provided diagnostics.
- [x] Implement `@deprecated`.
- [x] Emit deprecation diagnostics at use sites.
- [x] Implement `@returnAll`.
- [x] Return all remaining function-stack values where requested.
- [x] Implement `@errType`.
- [x] Apply implemented error-type transformations or constraints.
- [x] Implement `@mustcall`.
- [x] Warn or error when marked results are discarded.
- [x] Implement `@commutative`.
- [x] Generate every required argument-order overload permutation.
- [x] Apply normal overload resolution to generated permutations.

## 36. Multimethods

- [x] Parse `multi define`.
- [x] Register runtime-dispatched overloads.
- [x] Require a compatible non-multi fallback overload.
- [x] Require multimethod return signatures to match their fallback.
- [x] Select specialisations from exact runtime argument types.
- [x] Use compile-time dispatch when static types already identify an exact multimethod.
- [x] Fall back to normal overload resolution when no runtime specialisation applies.
- [x] Support multiple runtime-dispatched parameters.

## 36.1. Union-covered overloaded function values

- [x] Resolve every cartesian union-input branch statically.
- [x] Merge the selected overload return positions into union results.
- [x] Serialize branch-to-overload dispatch plans with function sets.
- [x] Match broad numeric supertypes, named traits, and variants at runtime.
- [x] Respect declared variance for reified nominal generic arguments.
- [x] Retain data-tag evidence for runtime branch matching.
- [x] Reject missing, ambiguous, or differently selected overlapping branches.
- [x] Invoke the statically selected overload without speculative execution.

## 37. Error handling

- [x] Provide the generic `Result[T, E]` type.
- [x] Provide success and error variants or constructors.
- [x] Provide the `Err` trait.
- [x] Provide the standard recoverable built-in error objects.
- [x] Provide the `Fault` trait.
- [x] Implement panic values and panic propagation.
- [x] Require explicit panic values and typed handlers to implement `Fault`.
- [x] Attach `Panic[T]` element tags to panicking operations.
- [x] Distinguish intrinsic runtime faults from explicit panic where specified.
- [x] Parse `try/handle`.
- [x] Match handlers against panic or fault types.
- [x] Support recovery values from handlers.
- [x] Integrate handler return types with normal stack typing.
- [x] Provide optional/result propagation using `&`.
- [x] Provide the `?` optional/result helper.
- [x] Provide the `?!` helper.
- [ ] Preserve nested optional and result semantics through these helpers.
- [x] Provide `AssertError`.
- [x] Provide `VectorisationFault`.
- [x] Provide `SliceFault`.
- [x] Provide the standard general, system, and runtime built-in faults.
- [x] Raise catchable `IndexFault` and `KeyFault` values from indexing.

## 38. `where` clauses and type-level constraints

- [x] Parse `where` clauses.
- [x] Support constraints on generic types.
- [x] Support rank variables.
- [x] Bind collection ranks to rank variables.
- [x] Support the allowed rank arithmetic and comparisons.
- [x] Validate relationships between parameter and return ranks.
- [x] Restrict operations in `where` clauses to the specified set.
- [x] Reject invalid, unresolved, or unsupported constraints.
- [x] Integrate solved constraints with overload selection and unification.

## 39. Imports and modules

- [x] Treat each source file as a module.
- [x] Parse import blocks.
- [x] Support importing entire namespaces.
- [x] Support importing individual components.
- [x] Support importing several components using bracket syntax.
- [x] Support selective element-overload imports.
- [x] Support explicitly declared generic overload signatures in imports.
- [x] Support importing object-to-trait implementations.
- [x] Support local relative module paths.
- [x] Support project-root-relative paths using `root`, with `~` as an alias.
- [x] Support standard-library paths beginning with `std`.
- [x] Resolve manifest dependencies through canonical `dep` paths, with `@` as an alias.
- [x] Keep VCS locations in the manifest and lockfile rather than import paths.
- [x] Support namespace-qualified access.
- [x] Keep imports private by default.
- [x] Support public re-exports.
- [x] Detect conflicting imported overloads.
- [x] Detect conflicting imported trait implementations.
- [x] Require explicit conflict resolution.
- [x] Parse overload exclusions using `except`.
- [x] Support concrete and generic exclusions.
- [x] Reject exclusions of nonexistent overloads.
- [x] Reject `except` after an already specific overload import.
- [x] Continue detecting conflicts after exclusions.
- [x] Automatically import object-friendly elements with component object imports.
- [x] Avoid automatically importing object-friendly elements through namespace-only imports.
- [x] Import tag overlays and tag-associated elements.
- [x] Avoid importing unrelated elements that merely use an imported tag.

## 40. Package management and CLI

- [x] Recognise `valiance.toml` as the project manifest.
- [x] Use the manifest location as the project root.
- [x] Support standalone scripts without a manifest.
- [ ] Disable external packages for standalone scripts.
- [x] Parse project metadata.
- [x] Require fully specified dependency source kinds, package identities, and coordinates.
- [x] Parse exact-version dependency declarations.
- [x] Reject version ranges, wildcards, and implicit version selection.
- [x] Generate and maintain `valiance.lock`.
- [x] Record exact transitive dependency versions.
- [x] Install per-project packages into `.vln`.
- [ ] Support a global package location for tools.
- [x] Install multiple versions of the same dependency simultaneously.
- [ ] Keep types from different package versions distinct.
- [x] Support explicit dependency upgrades.
- [x] Implement `vln install`.
- [ ] Accept and acquire registry packages.
- [x] Implement Git-package `vln add`.
- [x] Implement local-snapshot `vln add`.
- [x] Implement live path dependencies across nested project roots.
- [x] Infer package metadata for concise `vln add --path`, `--local`, and `--git` commands.
- [x] Convert live path dependencies into managed snapshots with `vln localize`.
- [ ] Accept and acquire Mercurial, Subversion, and Fossil packages.
- [x] Implement `vln remove`.
- [x] Implement `vln upgrade`.
- [x] Update manifest, lockfile, and managed package tree transactionally during package changes.

## 41. Concurrency — initial release complete

- [x] Implement cooperative tasks and `spawn`.
- [x] Preserve native task output rows in `Task[...]`.
- [x] Implement repeatable scalar and vectorised `wait`.
- [x] Implement structured `concurrent` scopes and automatic child joining.
- [x] Implement deterministic fail-fast cancellation and fault selection.
- [x] Implement invariant generic channels, rendezvous, bounded buffering, and FIFO.
- [x] Implement channel send, receive, close, draining, and closed-send faults.
- [x] Distinguish `Receive.Value(None)` from `Receive.Closed`.
- [x] Integrate transfer classes, isolated movement, copy-on-write, and lazy values.
- [x] Integrate cooperative timers/non-blocking wake sources and deadlock reporting.
- [x] Preserve concurrency through optimization and bytecode serialization.
- [x] Add deterministic fuzzing, stress/leak gates, benchmarks, and executable examples.
- [ ] Deferred: public cancellation and timeout syntax.
- [ ] Deferred: `match channels` / select across multiple channel operations.
- [ ] Deferred: directional channel endpoints, priorities, detached tasks, and parallel execution.

## 42. Eager evaluation — deferred design area

- [x] Parse eager definitions or eager markers.
- [x] Attach the `Eager` companion tag.
- [x] Trigger eager execution under the specified conditions.
- [x] Preserve eager behavior through higher-order function calls.
- [ ] Integrate eagerness with vectorisation.
- [x] Integrate eagerness with effect-tag propagation.
- [ ] Enforce restrictions associated with eager functions.
- [x] Prevent direct user attachment of the `Eager` companion tag.

## 43. Foreign-function interface — deferred design area

- [ ] Parse `external` blocks.
- [ ] Support optional external library filenames.
- [ ] Support optional namespaces.
- [ ] Allow external function declarations.
- [ ] Bind declaration names to matching C function names.
- [ ] Validate foreign parameter and return types.
- [ ] Restrict foreign declarations to external contexts.
- [ ] Allow ordinary Valiance code inside external blocks.
- [ ] Return the external block’s top stack value.
- [ ] Prevent ordinary FFI scalar types from escaping external blocks.
- [ ] Permit opaque foreign handles to escape external blocks.
- [ ] Provide a standard library of C-compatible FFI types.
- [ ] Restrict creation and manipulation of FFI types to external contexts.
- [ ] Provide built-in casts between compatible Valiance and FFI types.
- [ ] Perform required range and representation validation.
- [ ] Parse external object opaque bindings.
- [ ] Prevent constructors, members, and object-friendly elements on opaque bindings.
- [ ] Support C struct bindings with field declarations.
- [ ] Allow foreign struct construction inside external blocks.
- [ ] Permit public foreign-struct field reads in external blocks.
- [ ] Reject direct foreign-struct field writes.
- [ ] Support wrapping opaque handles in ordinary Valiance objects.
- [ ] Support explicit foreign-resource destructors.
- [ ] Define FFI list/array conversion behavior.
- [ ] Define rectangular shape validation for list-to-C-array conversion.
- [ ] Define FFI function-object and callback behavior.
- [ ] Parse inline external function bindings.
- [ ] Apply inline parameter and return casts around an external call.

## 44. User-defined cast declarations — deferred design area

- [ ] Parse `cast Source -> Target => ... end`.
- [ ] Support named and unnamed source parameters.
- [ ] Restrict cast declarations to permitted atomic source and target types.
- [ ] Require cast bodies to return the declared target type.
- [ ] Include declared casts in safe `as` resolution.
- [ ] Keep unsafe `as!` independent of declared cast rules.
- [ ] Support casts involving external blocks.
- [ ] Detect ambiguous cast rules.
- [ ] Detect recursive or cyclic cast selection where prohibited.

## 45. Diagnostics and static validation

- [x] Report lexical errors with source locations.
- [x] Report syntax errors with source locations.
- [x] Report stack-underflow errors detectable at compile time.
- [x] Report unresolved element overloads.
- [x] Report ambiguous overloads.
- [x] Report arity or multiplicity inconsistencies.
- [x] Report invalid variable reassignment.
- [x] Report writes to constants.
- [x] Report writes to protected object members.
- [x] Report incomplete object construction.
- [x] Report non-exhaustive matches.
- [x] Report branch input-signature mismatches.
- [x] Report loop state-signature mismatches.
- [x] Report invalid casts.
- [x] Report unnecessary unsafe casts.
- [x] Report missing generic solutions.
- [x] Report conflicting generic solutions.
- [x] Report invalid rank relationships.
- [x] Report tag disjoint violations.
- [x] Report missing tag validators.
- [x] Report effect-tag violations.
- [ ] Report import and implementation conflicts.
- [x] Report invalid package-version usage.
- [ ] Report discarded multi-value expression results.
- [ ] Report ignored `@mustcall` results.
- [x] Emit annotation-driven warnings and errors.
- [x] Emit deprecation warnings.
- [ ] Emit list-to-array runtime-check warnings.
- [x] Include runtime stack values in call-error diagnostics.
- [x] Include runtime stack value types in call-error diagnostics.
- [x] Include attempted overload input shapes in call-error diagnostics.

## 46. Core runtime and standard built-ins

- [x] Execute inline source through the bytecode runtime.
- [x] Execute source files through the bytecode runtime.
- [x] Provide a persistent interactive REPL.
- [x] Provide syntax highlighting, dynamic completion, and history suggestions in
  capable terminals.
- [x] Provide non-executing stack-type previews for REPL input.
- [x] Fall back to a portable plain REPL for unsupported or redirected
  terminals.
- [x] Print the final stack when implicit output is requested and nothing prints.
- [x] Emit portable binary bytecode files.
- [x] Execute saved portable binary bytecode files.
- [x] Encode bytecode operations as implementation-independent byte values.
- [x] Provide core arithmetic elements and overloads.
- [x] Provide string concatenation.
- [x] Provide comparison and equality operations.
- [x] Provide list operations used by the implemented design examples.
- [x] Provide `length`.
- [x] Provide `sum`.
- [x] Provide reduction.
- [ ] Provide `wrap`.
- [x] Provide `top`.
- [x] Provide `call`.
- [x] Provide indexing and immutable-update elements.
- [x] Provide optional and result helper elements.
- [ ] Provide `or` for extension selection.
- [x] Provide tag application and removal operations.
- [x] Provide type inspection required by matching and multimethods.
- [ ] Provide standard fault, result, option, task, and channel types.
- [x] Provide the standard traits referenced by the language.
- [x] Provide the `std` module namespace and module-resolution behavior.
- [x] Provide Python-backed standard-library modules using `@stdlib_element`.
- [x] Support Valiance-only standard-library modules.
- [x] Support mixed Python and Valiance standard-library modules.
- [x] Provide initial `std.regex` helpers.
- [x] Provide initial `std.trig` helpers.
- [x] Provide `std.grids.allNeighbors` with edge omission and optional wrapping.
- [x] Provide `std.random.randbit` and inclusive `std.random.between`.
- [x] Provide `std.string.\Alphabet` and `std.string.transliterate`.
- [x] Provide string mapping, splitting, integer parsing, and Unicode code-point conversion.
- [x] Provide finite-sequence helpers used by the worked examples (`first`, `last`, `drop`, `dropLast`, `overtake`, `groupConsecutive`, `removeAt`, `reshape`, and `rotate`).
- [x] Return `Integer` from `length` for finite lists and strings.
- [x] Provide numeric exponentiation, `square`, `inc`, membership, structural equality, and half-open range checks.
