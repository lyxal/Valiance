# 0. Syntax

- Elements are parsed in "chains" in a left to right order.
- Each element in a chain uses the result of the next element as its right most argument. That is to say, chains are executed right-to-left.
- Chains are broken upon:
	- Nilads (numbers, strings, variables, elements that start with `\`)
	- Control flow structures
	- `|`
	- Elements using element call syntax
	- Newlines
- Note that nilads are included in the chain they break. `fn`s are also included in the chain they break.
- The key benefits of this is that you can write expressions as if they were infix.

Some examples:

```
3 + 4 * 7
```

Is considered as

```
[3] [+ 4] [* 7]
```

Which equals

```
3 +(_, 4) *(_, 7)
```

## 0.1. Pulling in Values From Parent Stack

- `_` in a chain acts as a substituion.

# 1. Fundamentals
- Stack based language. There's a top level stack where everything lives upon.
- `#?` starts single line comment
- `#??` starts a documentation comment line. A contiguous block immediately
  before a `define` can contain free text plus `@param`, `@typeparam`, and
  `@returns` fields. The compiler still treats these as comments; `vln docs`
  turns them into an HTML reference. See `docs/docstrings.md`.
- `#/` and `/#` for multiline comment. Can be nested, but must be balanced.

## 1.1. Numbers
- Unlimited size, unlimited precision.
        - As much as the computer can handle of course
        - Exact numbers. No `0.1 + 0.2 != 0.3` shenanigans.
        - Able to store multiples of Pi, e, surds, etc. (although pi, e, and surds do not have dedicated literal syntax). This is more to say "the number type itself is very powerful"
- Imaginary parts supported too.
- Can also have `${x}e{$y}` for `x * 10 ** y`. Just `e{y}` is a syntax error. `y` can be any real number.

- Numeric types form an explicit hierarchy: `Integer <: Real <: Number`. An
  `Integer` represents a whole number, `Real` represents a number with no
  complex part, and `Number` is the overarching type for all numbers.
- Implemented numeric helpers include:
  - `**` for exponentiation.
  - `square` as an alias of `squared`.
  - `inc` to add one.
  - `inRange(start, stop)` to test a half-open interval: `start <= value < stop`.
- Some example numbers:

```
69
420.69
69.0
-69
-420.69
-69.0
69i420
-69i-420
-420.69i-69
6e7
6e-7
1e2i3e4
```

## 1.2. Strings

- Objects, not lists of characters
        - More convenient for string-focused operations
        - No concern about string shape either.
- UTF-8 encoded
- Support for string interpolation
  - `$` followed by either an identifier or `{expression}` inside a double-quoted string replaces that part of the string with the string representation of the value.
  - Example:

```
$brainrot = "6 7"
"The 2025 word of the year was $brainrot"
```

- Double quotes - `"Hello, World!"`
- Can contain literal newlines = no need for `\n`. Only quotes, backslashes, and `$` can be escaped. ``\"``, ``\\``, and ``\$`` respectively.
- Unterminated string = lexer error.
- Type = `String`
- Strings can be mapped directly. Each function call receives a one-character `String`, and `map` returns a list of mapped values.
- Common string helpers include:
  - `split(separator)`; an empty separator splits into individual characters.
  - `rotate(amount)`; positive amounts rotate left and negative amounts rotate right.
  - `numeric?`; tests whether the complete string is a base-ten integer.
  - `parseInt`; returns `Integer?`, using `None` when parsing fails.
  - `fromCharcode` / `fromCharCode`; converts an `Integer` Unicode code point to a one-character string.
- `input(prompt)` prints the prompt and returns one line from standard input without its trailing newline. It carries the normal eager I/O effects.

## 1.3. Tuples

- Fixed length collections.
- Data need not be of the same type.
- Can contain other tuples.
- Finite length, known at compile time.
- Type expressed as `{<types>}`
- Create with `{}`
- Example tuples:

```
{1, 2, 3} #? {Number, Number, Number}
{"Hello", 5} #? {String, Number}
```

### 1.3.1. Arbitrary Length Tuple Types

- Sometimes, it's useful to accept an arbitrary length tuple as a parameter.
- `{<type>...}` will accept any tuple with that type repeated 0 or more times.
- `{<type1>, <type2>..., <type3>}` is 1 type1, followed by 0 or more type2, followed by type3.
- Repeated segments can appear before, between, or after fixed segments. More than one repeated segment is allowed, such as `{Number..., String...}`.
- Arbitrary length tuple types can only be used in parameters. This is open to change, but this restriction is sensible until further exploration is done.
- Arbitrary length tuples exist only as types. You pass normal tuples around.
- Arbitrary length tuples can only be passed where other arbitrary length tuples are expected. But fixed length tuples can be passed where an arbitrary length tuple is expected if it matches the expected pattern.
- Some examples:

```
{Number} -> A tuple of 1 Number
{Number...} -> A tuple of 0 or more Numbers
{Number..., String} -> A tuple of 0 or more Numbers, followed by a String
{Number..., Number} -> A tuple of at least 1 Number (one or more Numbers, followed by a guaranteed Number).
{Number..., String...} -> A tuple of 0 or more Numbers, followed by 0 or more Strings
```

## 1.4. Anonymous Records

  - Stores of `key->value` pairs.
  - `record{...}`
  - Items can be retrieved, if they are in the dictionary, by indexing by key.
  - Effectively anonymous objects
  - Basically hashmaps with bareword keys.
- Type = `record[<name>: <ValueType>...]`. For example, `record[cmd: String, jump: Integer]` describes a record with exactly those statically known fields.
- Example:

```
$store = record{a: 1, b: 2, c: 3}
#? record[a: Number, b: Number, c: Number]
$store.a #? 1
$store.c #? 3
```

### 1.4.1. Dictionaries

- Records have static keys. Not good if you want computed keys.
- `dict{...}` provides a hashmap where keys can be any value
- Type = `Dict[<keytype>, <valuetype>]`

```
dict{"a": 1, "b": 2, "c": 3}
#? dict[String -> Number]
```

### 1.4.2. Extending, Merging, and Updating Records

- `record.extend{...}` adds statically written fields to the record on the top of the stack.

```
record{x: 3} record.extend{y: 4}
#? Same as
record{x: 3, y: 4}
```

- `record.merge` combines two record values. Fields from the right record overwrite fields with the same name in the left record.

```
record{x: 3} record.merge record{y: 4}
#? record{x: 3, y: 4}

record{x: 3} record.merge record{x: 4}
#? record{x: 4}
```

- `record.extend` is rejected if a supplied field already exists.
- Record fields can be updated with ordinary member assignment. Nested index/member paths are rebuilt from the inside out:

```
$instructions[$i].jump = $open
```

  This does not expose mutation. The containing record and list values are reconstructed and written back to `$instructions`.

## 1.5. None
- A value representing the absence of any other values.
- Always has the type `None`
- Can be used where an optional type is expected.

## 1.6. Lists

- Core data type
        - Especially given this is an array programming language.
- Lists are homogenous, arbitrary (and potentially infinite) length collections of data.
        - But do note that `[1, 2, "3"]` is a completely valid list. Even though the individual item types are different, it is a homogenous list of `Number|String`. Union types to the rescue!
- The type representation of lists will be outlined in the section on types, because there's a few different ways of typing a list.
- `[]` syntax btw. Comma separated items.
- List items will pop from the stack if there are any stack underflow during construction.
	- This makes list syntax an implicit `fork`. The same arguments will be used for each item. List construction will pop max(arity) between list items.
- An empty list must be accompanied by a type cast to specify what type of list it is.
  - No `Any` type, so list base type must be specified. Compile error to not do so.
  - `$name: Type = []`, use the `list[T]` element, or `[] as Type`

### 1.6.1. Common Finite-Sequence Elements

- `length` returns an `Integer` for finite lists and strings. It is deliberately non-vectorising: `[[1, 2], [3]] length` returns `2`, not `[2, 1]`.
- `first` and `last` return the first or final item. Their inputs must be non-empty.
- `drop(count)` removes a non-negative prefix; `dropLast` removes the final item of a non-empty finite list.
- `overtake(count)` cycles a non-empty finite list until exactly `count` items have been produced.
- `groupConsecutive` groups adjacent equal items. A string produces a list of strings; a list produces a list of sublists.
- `removeAt(index)` returns a copy without the indexed item. Negative indices count from the end.
- `rotate(amount)` rotates a finite list or string.
- `sum` adds a numeric list and returns zero for an empty list.
- `reshape shape` reshapes a finite value using a non-empty list or tuple of dimensions; tuple length determines the exact result rank.
- `map` accepts strings as character sequences. It also accepts a niladic callable; in that overload the callable is invoked once per input item and the item itself is ignored. This supports constructions such as `range(1, 100) | map: randbit`.

```
[0] overtake 5                 #? [0, 0, 0, 0, 0]
"aaabbc" groupConsecutive     #? ["aaa", "bb", "c"]
[1, 2, 3, 4] removeAt(1)      #? [1, 3, 4]
range(1, 6) reshape {2, 3}     #? [[1, 2, 3], [4, 5, 6]]
```
 
## 1.7. Arrays

- Also a core data type
- Arrays are like lists except rectangular, always finite (to ensure rectangularity), and backed by actual arrays
- `arr{}` syntax - same as lists, but `arr{`instead of `[` and `}` instead of `]`
## 1.8. Booleans
- Valiance does not actually have booleans. Instead, `0` is considered false, and all other numbers are considered true.
- However, `#boolean Number` can be used as a type. This means that the number will always be 0 or 1 (enforced by tag validator). (Note: validator may or may not be dropped before full release)
- The `true` element is an alias for pushing `1`
- The `false` element is an alias for pushing `0`

# 2. Types
- Every stack value has a type.
- Types can be:
	- Concrete (simple name)
	- Union (`T|U`) - either T or U
	- Intersection (`T&U`) - two traits implemented
	- Optional (`T?`) - a union of `Some[T]|None`. More on this later because there's more to the story than normal.
	- Or a list/array type

## 2.1. List Types
- Traditionally, lists are expressed as a composition of generics.
	- `List<Int>` or `List<List<String>>`
- In Valiance, given the fundamentalness of lists, a list type is expressed as a function of the "base" type of the list
- A list is a type, followed by the rank of the list.
	- This makes the list type a baked-in feature, rather than an otherwise after-thought construct.

## 2.1.1. Exact List Rank
- `+` after a type represents 1 level of list rank
	- `Number+` is a rank-1 (flat) list of `Number`s.
	- `Number++` is a rank-2 (list of lists) list of `Number`s
	- `Number+++` is a rank-3 (list of lists of lists) list of `Number`s.
	- This pattern gets unruly quick, so `Type+n`, where `n` is a positive non-0 integer, is the rank.
		- `Number+3` == `Number+++`
- Notably, `+` is the _exact_ rank of the list.

## 2.1.2. Minimum List Rank
- Sometimes, the exact rank of a list is unknowable at compile time.
- However, a _minimum_ list rank can be known at runtime. You can safely say "I don't know exactly what rank this is, but I know for a fact it's a list of at least a certain rank. May be more, but that's okay"
- `*` after a type represents 1 level of minimum list rank.
	- `Number*` has a minimum list rank of 1. It may end up being a `Number+5` at runtime, but `5 > 1`, so that's okay.
	- `Number**` has a minimum list rank of 2. It will never be a `Number+` at runtime, because `1 < 2`
	- `Number*n`, where `n` is a positive non-0 integer, has a minimum rank of `n`. Like the exact list rank type, `Number*3` == `Number***`
- A list with exact rank `n` can be passed where a list with minimum rank `m` is expected if `n >= m`.
	- The opposite (passing minimum rank `m` where exact rank `n` is expected) is only true IF `m > n` AND the exact rank list is marked as accepting vectorisation.

## 2.1.3. Rugged List Rank
- Notably, `+` and `*` both imply a homogenous structure.
	- A list can have any structure it wants at runtime, but it'll always be expressed as some ranked-list of either a single base type or a union type.
	- This is inconvenient for wildly ragged lists.
		- `[1, [[2, 3], 4], [[[5]]]` can be expressed as an exact rank type, but it's going to be annoying to type.
- `~` after a type represents 1 level of minimum rugged rank
	- "Rugged rank? What's that"
	- It's basically saying "It's a list. It's completely arbitrarily recursive. We have no idea where it ends, but we do know that it's at least _a list_,"
	- Rugged rank only exists as a compile-time construct.
		- `[1, [[2, 3], 4], [[[5]]]` at runtime is always `(Number|(Number+|Number)+|Number+++)+`, but can be considered `Number~` for type checking purposes.
- A list with exact rank `n` or minimum rank `m` can be passed where rugged rank `x` is expected, if `n >= x` (or `m >= x`).

## 2.2. Array Types

### 2.2.1. Exact Array Rank
- Just like lists, arrays have an exact rank type. `^` after a type represents 1 level of exact array rank.
- An array with exact rank `n` can be used anywhere a list with exact rank `n` is expected. That is, you can pass a `T^n` where a `T+n` is expected.

### 2.2.2. Minimum Array Rank
- Just like lists, arrays have a miminum rank type. `>` after a type represents 1 level of minimum array rank.
- An array with minimum rank `n` can be used anywhere a list with minimum rank `n` is expected. That is, you can pass a `T>n` where a `T*n` is expected.
- There is no concept of rugged rank for arrays.

## 2.3. Mixing Lists and Arrays

- As stated, arrays can be passed anywhere lists are expected, given the relative ranks match.
- However, some lists can also be passed anywhere an array is expected.
- `T+n` can be treated as `T^n`
- `T*n` can be treated as `T>n`
- But! Doing so will generate a compile time warning AND perform a runtime check to make sure that the conversion is valid. Additionally, the list will not be reorganised as an array in memory.
	- The above is not performed if the list type is known to have come from an array type.
- `T~` can never be passed where an array type is expected.

- Use list types for 99% of cases (`T+`, `T*`, `T~`)
- Use array types if you really do need rectangularity. (`T^`, `T>`)


## 2.4. Type Casting
- A stack item with type `X` can be treated as type `Y` if and only if `X` makes sense as `Y`.
	- That is, if `Y` is a trait implemented by `X` OR
	- If `X` is a list type that could be flattened to `Y` (e.g. a `(Number|Number+)+` could be a `Number++` if there's no `Number`s, only `Number+`s) OR
	- `X` is a collection type with exact rank being cast to minimum rank type `Y`. [This cast is only runtime checked if going from list to array.] [This cast is 0 cost if an array has been cast to a list and is being cast back to an array]
- Two types of type casting:
	- Safe - the conversion is checked at runtime. Only collection casting will be checked - trait upcasting doesn't need to be checked.
	- Unsafe - the conversion is not checked at runtime. This also only really applies to collection casting. This is good for performance, but be careful that it is actually treatable as the intended type.
- Safe = `as Type`
	- `[[1, 2, 3], [4, 5, 6]] as Number*`
	- `Circle as Shape` (assuming trait `Shape` and `Circle` implements `Shape`)
- Unsafe = `as! Type`
	- `[[1, 2, 3], [4, 5, 6]] as! Number*`
	- Unsafe cast that is otherwise safe is a compile error (don't use `as!` where it isn't needed.)

## 2.5. Optional Types

- The Valiance definition of `T?` is `Some[T] | None`
- Notably, this definition allows for a meaningful definition of `T??` as `Some[T?] | None` or `Some[Some[T] | None] | None`.
- Numeric optional-depth shorthand is valid: `T?3` means the same thing as
  `T???`.
- However, that requires a lot of wrapping of values in `Some`. This can get verbose and noisy quickly.
- Therefore, `T|None` is also considered `T?`. The `T` is automatically wrapped in `Some`
- Note that `T|None|None` does not equal `T??`. It equals `T?`, because the `None|None` simplifies to `None`. You would need `Some[T?] | None` to get `T??`.
- An additional rule that exists is that `T|Some[U]` == `Some[T|U]` if `T` can never be `None` (i.e. `T` is not `None` nor an optional type).
- This is helpful because `Some[T]|None` can get noisy the further you stack `?`s. `Some[Some[T] | None] | None` has a lot of nesting. Therefore `T|Some[None]|None` == `T??` 
	- `T? | U` == `Some[T] | None | U` == `Some[T|U] | None` == `(T|U)?`
- In this way, non-`None` types are always implicitly `Some`. But `None` can still be explicitly wrapped in `Some`.
- This system means that there is a canonical ordering of unions. First, all non-none types are listed. Then, None types. In other words, `T | None | U` is reordered to `T | U | None`
- The benefit of this is a reduction in the amount of `Some` wrapping.
 
# 3. Variables
- Mutable stores of immutable values. You can write to the store as much as you want, but you can't mutate what's stored.
- `$name = value` - Type inferred from value
- `$name: type = value` - Type set.
- Variables, once initalised, have the same type. Must always be set to a value.
- `value` runs until the end of the line, or until a closing indicator. Whatever is on the top of the stack at the end of the line will be used. The remaining stack items will be left on the stack. It's up to the user to not leave more things on the stack than needed
	- You may want to have multiple values left after assignment, but be wise about how you use this.
- All variables are local. No global variables whatsoever.

## 3.1. Augmented Assignment
- Instead of providing a fixed set of augmented assignment operators, Valiance allows any function to be used.
- `$name := code`
- `code` runs until the end of the line, or until a closing indicator.
- The value of the variable is automatically pushed before `code` is executed. There is no argument cycling on the value though.
- Example:

```
$counter = 5
$counter := ++
```

## 3.2. Constants
- Variables can also be declared as "constant". That means they cannot be written to again.
- `const $name = value` - inferred type
- `const $name: type = value` - explicit type
- Also uses stack to calculate value

## 3.3. Multiple Assignment
- To assign multiple values from the stack at once:

```
$(<variables>) = <values>
```

- Each variable will be matched with the corresponding stack item. If there are more variables than values, the rest of the stack will be used.
- Note that this is not variable unpacking
  
```
$(a, b, c) = 1 2 3
#? Same as
$a = 1
$b = 2
$c = 3
```

Grouped constant declarations use the same target syntax:

```
const ($WIDTH, $HEIGHT) = 10 | 20
```

## 3.4. Variable Shadowing

- Attempting to write to a variable in an outer scope will not update that variable.
- Instead, a new variable inside the current scope will be created.
- After such an assignment, future reads will refer to the locally scoped variable.
- Shadowing occurs at evaluation time. That is to say that something like `$x = $x 1 +` will set local `$x` to outer `$x + 1`
- Example:

```
$x = 5
define foo =>
  $x = 6
  println("foo: x = $x")
  #? x = 6
end
foo
println("main: x = $x")
#? x = 5
```

# 4. Elements
- An element takes items from the stack, applies code, and pushes the results back.
- The number of items an element takes is its arity.
- The number of items an element pushes back is its multiplicity.
- Arity and multiplicity are integers >= 0 (no input and no output are possible).
- Note that the order stack items are passed to elements is reversed relative what is popped. That is, the top of the stack isn't always used as the "left most" argument. 
- Instead, look at the top (arity) items.

```
+ (Number, Number) -> Number   | Addition
+ (String, String) -> String   | Concatenation
- (Number, Number) -> Number   | Subtraction
length [T](T+) -> Integer      | Length
sum (Number+) -> Number        | Summate numeric list
** (Number, Number) -> Number  | Exponentiation
=== [T](T, T) -> #boolean Number | Structural equality
in [T](T, T+) -> #boolean Number | Membership
/ (Number, Number) -> Number   | Division
/ [T](T+, Function[T, T -> T]) | Reduce list by function
wrap [T] (T) -> T+             | Put item in a list (like wrap in [])
top [T] (T) -> T               | Push the top of the stack unchanged
```

- The syntax for an element is:

```
Element := ["\"] ElementFirstChar {ElementChar}
ElementFirstChar := <A-Z>|<a-z>|"-"|"+"|"*"|"%"|"!"|"?"|"="|"/"|"<"|">"
ElementChar := ElementFirstChar | <0-9>
```

- Elements can do multiple things based on the types of items on the stack.
	- Like how `+` can be "add 2 numbers" if given numbers, but also "concatenate two strings" if given strings.
- An element can have as many overloads as it likes.
	- However, just because it can, doesn't mean it necessarily _should_.
	- In practice, try to keep overloads as few as possible, and as related as possible (either consistency between overloads, or consistency with the meaning of a symbol).
	- For example, `/` is division because it's a commonly used symbol in mainstream programming languages. `/` is also reduce because it's commonly used in array programming languages.
- Elements have fixed arity and multiplicity.
	- That means an element will always pop the same number of items and push the same number of items.
	- This is cruical for making element overloads consistent

## 4.1. Element Call Syntax

- Elements can always be called as-is in chain order.
- However, this isn't always the most readable thing
	- `"This is a long string of things" println` kind of starts to lose focus.
- This isn't a problem in mainstream programming languages where the majority of "things that do things" are functions. `()` usually wraps function arguments.
	- `println("This is a long string of things")`.
- However, as already stated, elements are _not_ functions.
	- BUT. That doesn't mean `()` can't be applied as a language design concept.
- Therefore, `()` with no spaces after an element allows for arguments to be listed in a more familiar way.
	- `elementName(args)`
	- Generally equivalent to `args elementName`

```
length([1, 2, 3]) #? 3
reduce([1, 2, 3], fn => + end) #? 6
+(6, 7) #? 13
#? The above is valid, but is goofy-ahh
```

- Note that not all arguments to an element have to be specified
	- Arguments in element call syntax are pushed to the stack left-to-right before the element is called

```
[1, 2, 3] reduce(fn => + end)
#? Equivalent to
[1, 2, 3] reduce fn => + end
```

- If arguments would pop from the stack, they do so left to right, and pop as many as needed. For example (assuming `double` pops 1 item, and `+` pops 2):

```
foo(double, +)
#? Given stack (top) [a, b, c] (bottom), equals

foo(double($a), +($b, $c))
```

- If an argument pushes more than one result, only the top of the returned results is used. The rest of the values are discarded. Note that a compile warning will be raised in such a situation. 

- `_` can be used to indicate the argument is not being filled right now.

```
5 2 -     #? 3
5 -(2)    #? Also 3
5 -(_, 2) #? Also also 3
5 -(2, _) #? -3
#? Equivalent to
2 5 -
```

- This is helpful for when you want to specify an argument in a position that is in the middle of the function call
- `_` does not change evaluation order. Arguments are still evaluated left to right.
- Arguments can also be named. Note that the name must correspond to the parameter name.
	- `name = ...`
	- No spaces between `=` needed
	- Very useful for optional arguments
	- The value can also be `_` to indicate fill from the stack

```
"Hello World" split(on=" ")
#? Same as
"Hello World" split(" ")
#? Which is just
"Hello World" split " "
```

-  Arguments can consume stack items as needed.
	- When an argument in () needs to pop from the stack, and multiple expressions are provided, those expressions partition the stack right-to-left: the rightmost expression pops its values from the top of the stack first, then the next expression pops from what remains, and so on. Each expression pops exactly as many values as it requires.

```
6 7 +(double, halve)
#? Same as
double 6 + halve 7
#? Equals
12 3.5 +
15.5
```

## 4.2. Overload Resolution and Disambiguation

- When picking an overload to execute, the "most specific" overload is chosen.
	- That is, the overload that has the most specific matches to the element arguments is chosen.
 
Consider:

```
F (Number) -> Number  #? Overload 1
F (Number|String) -> Number #? Overload 2
```

- If `F` is given `Number`, then overload 1 is chosen. Although both overloads match, the first is more specific, as `Number` is a narrower match than `Number|String`.
- Order of narrowness (or specificness):

```md
1. exact match
2. exact generic-equivalent match
3. optional substitution (T where T? expected)
4. vectorising match
5. intersection type match
6. trait implmentation match
7. rank match
8. union type match
```

- Note that tagged versions of matches take priority over untagged matches.
	- A tag match is narrower than an untagged value
 
- Overload X is more specific than overload Y if and only if:

```
For all parameter pairs (Px, Py) in zip(X.params, Y.params):
  specificity(Px) > specificity(Py) in the specificity chain

(All parameters must be strictly more specific)
```

- If multiple overloads are just as specific, that's a compile error
- Overloads can be disambiguated by specifying how to treat types.
  - `[]` before `()`

Say:

```
F Overload 1: (T) -> Number
F Overload 2: (U) -> Number
$n = Type X implements T and U
```

```
$n F #? Compile error: ambiguous whether overload 1 or 2 desired
$n F[T] #? Use overload 1
$n F[U] #? Use overload 2
```

# 5. Stack Shuffling
- 3 fundamental elements
- `dup` takes 1 stack item, and pushes 2 copies back. `a -> a a`
- `swap` takes 2 stack items, and pushes them back in reverse order. `a b -> b a`
- `pop` discards the top stack item. `a -> `
- 2 extra convenience words (not exactly elements):
- `copy(prestack -> poststack)`. Given a labelling of the prestack state, copy the values in poststack to the top of the stack.
- `move(prestack -> poststack)`. Given a labelling of the prestack state, move the values in poststack to the top of the stack.
	- All labels in prestack will be popped, even if they aren't referenced in poststack
- Duplicate labels are allowed in both words' post stacks, and will result in the value being pushed multiple times. Note that with `move`, the original prestack values are only ever popped once.
- `_` can be used as a label to indicate that the item should be skipped
- all other labels must be unique - compile error to have duplicate labels in prestack.
- Examples:
  
```
1 2 3 4
copy(a, b -> a, b, b)
#? 1 2 3 4 3 4 4
1 2 3 4
move(a, _, b -> a, a, b)
#? 1 3 2 2 4
```

- `_n` can be used in labels as a shorthand for `n` repeated `_`s. For example `move(a, _3 -> a)` == `move(a, _, _, _ -> a)`
- `prestack` always starts from the top of the stack. `a, b, c` refers to the top 3 items, rather than the bottom 3.

# 6. Functions
- All functions are anonymous. Only executed when called. They can live on the stack.
- When called, functions pop their arguments from their parent stack and execute on their own stack.
- The function stack starts with all arguments pushed to the stack.
- If a stack underflow occurs within a function call, then the function will re-use its arguments if it tries to pop from an empty stack. This can be thought of as having an infinite cycle of inputs on the stack, but without actually having the values on the stack.

```
fn (<params>) -> <returns> =>
  <code>
end
```

- `(<params>)` is optional. If omitted, the arguments for the function will be inferred (consequently, no argument cycling will occur). If `<params>` is empty, then that function takes no arguments (and will error on stack underflow).
- `-> <returns>` is also optional. If omitted, the function returns the single value at the top of its stack when execution completes (nothing [literally, nothing is pushed] if 0 values are on the stack, else the type of the top of the stack). All other values on the stack are discarded. If `<returns>` is empty, then that function returns no values. `<returns>` can specify more than one return type to push multiple things back upon completion.
- `=> <code>` will either run to the end of the line (if the first non-space token after the `=>` is not a newline), or until a corresponding `end` is found.
- The body can contain any expression (i.e. things that aren't `define`s, `object`s, or control-flow structures, etc.)
- Explicit parameters establish the function's cycling inputs. Named parameters are also available through `$name`; unnamed `:Type` parameters are stack-only.
- Named function parameters cannot be written to as variables. They can only be referred to as variables.
- Named function parameters cannot be shadowed. That is, the following is an error

```
fn (x: Number) =>
  $x = 5
end
```

## 6.1. Calling Functions
- Functions can be called in a few ways:
	- Using the `call` element. This takes a function, and calls it. Note that ECS on `call` will set the arguments passed to the function.
	- Using ECS on a variable storing a function:

```
$myfun = fn (:Number, :Number) => +
$myfun(6, 7) #? 13
6 7 $myfun() #? Takes args from stack
```

## 6.2. Argument Cycling
- If a function's parameters are specified, then the function will re-use its arguments if it tries to pop from an empty stack.
	- This reduces the number of `dup`, `swap`, `move`, and `copy` needed in functions.
	- It's surprisingly effective.
- This can be thought of as having an infinite cycle of inputs on the stack, but without actually having the values on the stack.
- Examples:

```
$singleArg = fn (:Number) => println | println
$singleArg(5) #? Prints "5" twice

$doubleArg = fn (:Number, :Number) => println | println | println
$doubleArg(6, 7) #? prints "6", "7", "6"
```

Cycling is conceptual rather than eager duplication. Values are reused only when the function's physical stack would otherwise underflow. Inferred-parameter functions and explicit `fn ()` functions do not cycle.

## 6.3. Variable Capturing
- If a function refers to a variable from an outside scope, that function will "capture" that variable's value. If the function is returned from another function, that value will still be available
- Functions do not capture top-level assignments. A top-level `define` may depend on other elements, including niladic elements, but not on `$name` variables assigned at module/top level.
- Captured values are restored at the start of each closure call. Assigning to a captured name inside the closure changes that call's local copy, not the value stored in the closure for future calls.
- For example

```
$createMultiplier = fn (factor: Number) =>
  fn (:Number) => * $factor
end

$double = createMultiplier(2)
#? A Function[Number -> Number]
```

```
fn =>
  $x = 5
  fn => $x + 1
end
$wrapped = call(top)
$x = 10
$wrapped call #? 6
#? It used its stored value rather than the scope's value
```

```
define foo(x: Integer) =>
  fn () =>
    $x := 1 +
    println $x
  end
end

$c = foo(5)
$c()
$c()
#? Prints 6 both times
```

This is not valid, because `timesFive` would depend on a top-level assignment:

```
$x = 5
define timesFive(y: Number) => $x * $y
```

## 6.4. Parameters
- Parameters can be one of:
	- `name: Type` - explicit type, value stored in variable. 
	- `:Type` - explicit type, but not stored in any variable. Good for when a name is overkill. 
	- `name` - stored in a variable, but type inferred from usage.
 
## 6.5. Type Inference
- Type inference is performed using forward overload inference. It is performed at definition site.
- Inference works by tracking what types parameters must have in order for the function body to be valid.
- Examples (assuming no additional overload definitions)
```
fn => +
#? Inferred as OverloadSet[Function[Number, Number -> Number], Function[String, String -> String]]

fn => + double
#? Inferred as Function[Number, Number -> Number]
#? The `double` makes the `Function[String, String -> String]` overload impossible, and thus ` Function[String, String -> String]` is discarded.
```
- Untyped variables are inferred from their usage. If an untyped variable is not used, a compile-time error is raised.
- Multiple possible overloads during inference = that function has multiple possible overloads.

### Union-covered overloaded functions

An overloaded function may satisfy a function type whose input is a union when
every union branch resolves to one unambiguous overload. The result type is the
union of the selected overload results. For example:

```valiance
$values = [1, 2, "A", "B"]
println($values map: * 2)
#? [2, 4, AA, BB]
```

The compiler records which overload is selected for each union branch and
emits that branch-to-overload plan with the function value. At runtime it tests
the value against reified type predicates and invokes the overload already
chosen for that branch. Broader numeric types, named traits, variants, generic
nominal variance, and reified data tags can therefore cover a branch without
requiring exact type-name equality.

Every branch must still be covered unambiguously. If two runtime-overlapping
branches would select different overloads, the function is rejected. Runtime
dispatch never executes overload bodies speculatively and never re-resolves to
a narrower overload based only on the concrete runtime value.

## 6.6. Call-Site Type Checking
- Sometimes it may be desirable to have a function accept any function as input, rather than a fixed function.
	- For example, `fn (f: Function)` to work for any arity/multiplicity
- However, executing such a function isn't type safe, because it could pop and push any number of things. This would make type checking impossible.
- Rather than attempting to type-check such functions generically, type checking is deferred until each call site. At the point of invocation, the concrete type of the function argument is known, and the body is validated as if that exact function type had been explicitly specified. This does not produce a single inferred type for the function. Instead, the function remains stack-polymorphic, and each invocation is type-checked independently under the concrete function type provided at that call site.
- Note that a CSTC function pops as many extra arguments as needed from the outer stack.. It's kind of like if function parameters were inferred, but with some extra specified parameters.
- CSTC is also triggered when varadaic tuples are in a function's parameters.
- If creating a function that will be CSTC'd, make sure to document the expected behaviour.
- Example:

```
#? Considered `Function[Function -> ?]`
$dip = fn (function: Function) =>
  $temp = top
  $function()
  $temp
end

1 2 3 $dip(fn => +) #? 3 3
#? dip in this context is considered `Function[Number, Number, Number, Function[Number, Number -> Number] -> Number, Number]`
#? Note that future usages of dip may use function values that aren't `Function[Number, Number -> Number]`
```

### 6.6.1. Call-Site Checked Stack Combinators

`both` and `correspond` are built-in call-site checked elements. Their stack
effects are derived from the concrete callable types at each use, so they work
with any fixed callable arity and multiplicity.

`both` applies one callable to two consecutive groups of the same size. If the
callable takes `n` inputs, `both` consumes `2 * n` stack values. The lower group
is called first and the upper group second; the first call's results are placed
below the second call's results.

```valiance
1 2 both: double                    #? 2 4
1 2 3 4 both: +                    #? 3 7
1 2 3 4 5 6 both: fn (a, b, c) =>
  $a $b + $c +
end                                    #? 6 15
```

A niladic callable is invoked twice and consumes no stack values. A callable
that returns multiple values contributes all of its results for each group.
Each group must independently satisfy the callable's parameter types.

`correspond` applies two callables to two distinct consecutive groups. If the
first callable takes `n` inputs and the second takes `m`, the first callable
receives the lower `n` values and the second callable receives the upper `m`
values. Their arities and multiplicities do not need to match. Results from the
first callable are placed below results from the second callable.

```valiance
1 2 correspond: (double, squared)  #? 2 4
1 2 3 correspond: (double, +)      #? 2 5
1 2 3 4 5 correspond: (
  +,
  fn (a, b, c) => $a $b + $c + end
)                                      #? 3 12
```

Because both elements are call-site checked, they can also participate in input
inference for an enclosing function:

```valiance
$pairSums = fn => both: + end
1 2 3 4 $pairSums()                 #? 3 7
```

## 6.7. Inline Parameter/Return Type-Casting

_Note: Normal code will not need to make use of this feature. It exists primarily for ergonomic FFI_

- Sometimes, you might want to always type cast a parameter before usage.
	- And in a way that the type rules don't enable automatically (like rank subsumption/subtype as a supertype)
- For (contrived) example:

```
fn (x: (Number|Number+)+) -> (Number|Number+)+ =>
  #? Ignore the fact you'd just write `Number++` as the
  #? parameter type and typecast before calling
  $x as Number++ double
  as {Number|Number+}+
end
```

- You can specify the type cast in the parameters/return type using `as`:

```
fn (x: (Number|Number+)+ as Number++) -> Number++ as (Number|Number+)+ => end
```

- Most times you won't need this.
- But very useful for FFI where types _do_ need type casting:

```
external("math.dll") define sqrt(:Number as FFI.float) -> FFI.float as Number => end
```

- More on FFI later.

## 6.8. Quick Functions

- `'` before the next chain segment wraps that segment in a function
- `'chain` == `fn => chain`
- E.g.

```
[1, 2, 4, 5, 8] '< 5 filter
#? Same as
[1, 2, 4, 5, 8] filter fn => < 5
```

# 7. Vectorisation

- High level:

```
[1, 2, 3] + 4 #? [5, 6, 7]
```

- When one or more arguments to an element are of a higher rank than a parameter expects, those arguments are zipped together and the element applied to each combination. Arguments that have reached their expected rank are reused across all combinations. This process repeats until all parameters have received arguments at their expected rank. If no overload exists that can handle an argument at its given rank - either directly or through vectorisation - that is a compile error.

- Examples of re-use:

```py
zip([1, 2, 3], 4) == [[1, 4], [2, 4], [3, 4]]
zip([[1], [2], [3]], [1]) == [[[1], [1]], [[2], [1]], [[3], [1]]]
```

- When an element is applied to multiple array arguments, all arrays must have equal length at each corresponding dimension.
- For example, `[1, 2, 3] + [4, 5]` is an error, because the `3` is unpaired.
  - While it would be possible to have a trimming/re-use/universal default fill option, these can lead to surprising results.
- `[[1, 2], [3, 4, 5]] + [[6, 7], [8, 9]]` also raises a runtime error
  - The `[3, 4, 5]` does not have the same length as the `[8, 9]`
- Length mismatch errors are raised as `VectorisationFault`s.
	- Note that `VectorisationFault`s cannot be raised using `panic`. This is the only such fault that can only be raised at runtime by the language.
	- `VectorisationFault`s do not attach a `Panic` element tag.
	- However, a `try/handle` can handle a `VectorisationFault`
 
### 7.0.1. Mixing Lists and Arrays

- If all arguments in vectorisation are lists, then the output will be a list.
- If all arguments in vectorisation are arrays, and the return type doesn't lose arrayness, then the output will be an array.
- If there is a mix, then the result will be a list.

## 7.1. Fine Grained Vectorisation Control

- Pairwise behaviour may not always be useful.
- Consider:

```
[[1, 2], [3, 4]] + [10, 20]
```

- Pairwise results in:

```
[[1 + 10, 2 + 10], [3 + 20, 4 + 20]]
[[11, 12], [23, 24]]
```

But what if you want:

```
[
  [1, 2] + [10, 20],
  [3, 4] + [10, 20]
]
```

- You might think to wrap `+` in a function with parameters:

```
fn (:Number+, :Number+) => +
```

- But this can lose array types - passing two `Number^`s to this function will result in a `Number+`, not a `Number^`
- Thus you can also specify how to treat a higher-ranked argument using element overload disambiguation syntax:

```
+[Number+, _]
```

## 7.2. Disabling Vectorisation in an Overload

- By default, parameters vectorise. Marking a parameter with the postfix
  call-policy marker `exact` prevents that parameter from being used as a
  vectorisation target. An argument must be directly compatible with the
  marked type; vectorisation cannot peel collection ranks from it to make the
  call fit.
- `exact` is not a runtime type and does not change the value received by the
  function body. It is retained in overload and `Function[...]` signatures so
  direct calls and calls through function values enforce the same policy, then
  erased from the parameter type visible inside the body.
- `exact` does not require the runtime value to have exactly the same nominal
  type. Ordinary assignability still applies, so an `Integer` can satisfy
  `Number exact`. It only disables the vectorisation fallback for that
  parameter.
- `exact` is a terminal postfix for the type expression it marks. Put rank,
  optional, and tag syntax before it, such as `Number+ exact`, `Number? exact`,
  or `#sorted Number+ exact`.

```
$myfun = fn (:Number exact) => double
#? A Function[Number exact -> Number]
$myfun(10)        #? 20
$myfun([1, 2, 3]) #? Compile error: No overload found

$myfunvec = fn => double
#? Inferred as Function[Number -> Number]
$myfunvec(10)        #? 20
$myfunvec([1, 2, 3]) #? [2, 4, 6]
```

- Marking a collection type exact makes the collection itself one argument and
  requires its declared rank. `Number+ exact` accepts a rank-1 number list but
  rejects a rank-2 list instead of vectorising over its outer rank.

```
$first = fn (xs: Number+ exact) -> Number => $xs head
$first([1, 2, 3])        #? 1
$first([[1, 2], [3, 4]]) #? Compile error: No overload found
```

- Exact arguments broadcast unchanged when another parameter causes the call
  to vectorise. Only arguments whose parameters permit vectorisation are
  indexed at each vectorised depth.

```
define keep(xs: Number+ exact, x: Number) -> Number+ => $xs end

[10, 20, 30] [1, 2] keep
#? [[10, 20, 30], [10, 20, 30]]
```

- A generic exact parameter binds the whole argument type. For example,
  `T exact` given a `Number+` argument binds `T` to `Number+`; it does not bind
  `T` to `Number` and vectorise the call.

```
$identity = fn[T] (value: T exact) -> T => $value
$identity([1, 2, 3]) #? [1, 2, 3]
```

## 7.3. Vectorisation of `T~` and `T~`-able Types

- `T~` can only be safely vectorised where an atomic value is expected.
	- The only structural guarantee of a `T~` of any rugged rank is that there's atomic types present at different depths.
	- Because items can be `T | T~`, `T` is the base case.
- `T~n` cannot be vectorised to satisfy a collection parameter such as `T+m`, even when `n > m`. Rugged rank does not guarantee a uniform prefix of nesting that can be peeled away safely.
- Vectorisation behaviours of `T~` also extend to union types that are expansions of `T~`
	- For example, `T | T+` can vectorise where a `T~` can, because a `T | T+` _is_ a `T~`.
	- `{T | T+}+3` can vectorise where a `T~3` can, because it's still a `T~3`.

## 7.4. The `extend` keyword
- As already mentioned, something like `[1, 2, 3] [4, 5] +` errors at runtime. The lengths of the two lists do not match.
- `extend` after an element can:
	- Specify a value to use as a default value in case of length mismatch
	- Specify exact patterns on how to handle missing values
	- Specify a selection function that handles all cases where there's a missing value.

### 7.4.1. `extend` + Default Value
- Simplest case of `extend`.
- `extend(...)` - the result of `...` is used as a stand-in for any missing values. `...` is executed once after the arguments to `element` are popped.
- For example, `[1, 2, 3] [4, 5] + extend(0)` - uses `0` if any values are missing. This makes it `[1 + 4, 2 + 5, 3 + 0]`.
- Note that the default value must be compatible with all parameters of the element.
- This form of `extend` is most helpful for type-homogeneous elements (ie all arguments are the same type). These will most often be dyadic pervasive mathematical operations like addition.

### 7.4.2. `extend` + Patterns
- The default value version of `extend` can only be used on type homogeneous elements. This means it cannot be used on something defined on `T, U` where `T != U`.
- A more general form of `extend` exists where you explicitly define what happens when certain arguments are missing.

```
extend =>
  (<pattern>) => <rule> end
  (<pattern>) => <rule> end
  <...>
end
```

- Each `pattern` is a comma separated series of names or `_`s.
  - A name means "this argument is present, and can be used to determine what gets substituted"
  - A `_` means "this argument is missing"
- `rule` is a series of expressionable items (basically anything that can appear in a list)

```
[1, 2, 3] [4, 5] + extend =>
  (lhs, _) => $lhs end
  (_, rhs) => $rhs end
end
```

### 7.4.3. `extend` + Selector

- The pattern form of `extend` is compatible with all functions and is capable of expressing all substitution cases. However, it can be verbose having to write out a whole pattern set when the substitution can be expressed as a single element.

```
extend: <selector>
```


- `selector` is an element that needs to have the same arity as `element`, and must accept optional versions of the parameters of `element`. 
- That is to say, if an `element` is defined on `T, U`, then `selector` must be defined on `T?, U?`
- This is because missing values will be passed in as `None`. 
- This has the consequence of meaning that optional types in `element` must be accepted as double-wrapped optionals in `selector` (otherwise, the meaning of `None` becomes meaningless).
- The most common selector will probably be `or`, which, when given two options, returns the first non-None choice (or None of they're both None.)
- The example from before becomes:

```
[1, 2, 3] [4, 5] + extend: or
```

# 8. Modifiers
## 8.1. The `:` Modifier
- A common pattern is to pass functions to other functions. Basically higher order functions.
- For example:

```
[1, 2, 3, 4] '+ reduce
```

- That can get majorly inconvenient and also has readability problems
  - Say you're at the end of a function and _then_ you find out you're reducing a list by it. You now have to go all the way back to the start of the function to verify it's actually reducing and to see what the list contains.

- You _could_ write:

```
[1, 2, 3, 4] reduce '+
#? or
[1, 2, 3, 4] reduce fn => +
```

- But that, even by Valiance philosophical standards, is rather ceremonious.
- `:` after an element allows you to specify that the next chain should be automatically wrapped as a function argument.
- For example:

```
[1, 2, 3, 4] reduce: +
#? No need for `fn => +`, `'+` or E.C.S or postfix application
```

- If an element takes multiple function arguments, chains must be wrapped in `()` and separated by `,`.
	- This ensure that the language grammar is not context-sensitive

```
fork: (sum, length) /
```

- If `:` is used, then _all_ function arguments must be specified. This ensures 0 ambiguity as to which function-typed parameters are being filled.
- Modifier shorthand is stack-oriented. `apply: -1` wraps subtraction by one, not a constant function returning negative one. Write `apply(fn => -1)` when a constant function is intended.


# 9. Indexing
- `$[<index>]` will get the `index`th item from the top of the stack. 0-indexed.
	- Valid if a type has an overload of `index`. Built-in types that support this include list types, tuple types, and `String`
- `[1, 2, 3] $[1]` == `2`.
- Negative index goes from end (`-1` == last).
- Numeric indices must be integral at runtime. Tagged numeric values can be used as indices; their tags remain part of the source value but do not change the numeric index operation.
- Can also be multiple indices
- `$data $[2, 4, 1]` == `[$data $[2], $data $[4], $data $[1]]`
- Variables can be indexed directly
- `$data[2, 4, 1]` == `$data $[2, 4, 1]`
- Slicing:
	- `$[<start>:<stop>:<step>]` - items starting at `start`, and finishing (and including) at `stop`, collecting every `step`th item.
	- The shorthand `$[::step]` is equivalent to `$[0:-1:step]`.
	- `stop` being inclusive corresponds to how the `range` element is inclusive on both ends.
	- `start` = 0 if not provided
	- `stop` = -1 if not provided
	- `step` = 1 if not provided
	- Lazy lists support non-negative slices with positive steps. A slice with no explicit `stop` stays lazy.
- `$data[1:4]` == `$data[1, 2, 3, 4]`
- `[1, 2, 3, 4, 5, 6] $[::2]` == `[1, 3, 5]`
- Multi-dimensional indices
- `$data[[1, 2]]` == `$data[1][2]` == `$data[1] $[2]`
- You can multidimensional slice lists (runtime panic - `SliceFault` to try and multidim slice a non-list)

```
[[9, 2, 5], [1, 4, 2]] $[[0, 0]:[1, 1]]
#? [[9, 2], [1, 4]]
```

-  Dictionary access too

```
dict{"name": "Jeff", "age": 12} $["name"] #? "Jeff"
```

- Such indexing obviously isn't needed with records

```
record{name: "Jeff", age: 12}
#? Just use
$.name #? "Jeff" 
```

## 9.1. Indexing and Augmented Assignment
- Index assignment reconstructs the receiver with the selected item or items replaced.
- Stack indexing assignment writes back to the receiver on top of the stack.

```
[1, 2, 3, 4, 5]
$[1:3] = 4
#? [1, 4, 4, 4, 5]
```

- Slice assignment accepts either a single replacement value, which is written to every selected item, or a list-shaped replacement with exactly one value for each selected item.
- For zero-based FizzBuzz over `range(1, 100)`, use offsets 2, 4, and 14:

```
range(1, 100) map: toString
$[2::3] = "Fizz"
$[4::5] = "Buzz"
$[14::15] = "FizzBuzz"
```

- Augmented assignment can be applied to an index

```
$data[1] := + 3
```

- Augmented assignment can also be applied to slices. The augmentation function is applied to each selected value, then the receiver is reconstructed.

```
[1, 2, 3, 4, 5]
$[1:3] := + 1
#? [1, 3, 4, 5, 5]
```

- This is not mutation. It is sugar for reconstructing the receiver from the indexed or sliced update.

## 9.2. Spread Indexing
- If there are a static number of indices, `...$[]` can be used to dump the items of the index to the stack

```
[5, 1, 6, 2, 7] ...$[3, 4] #? Pushes 2 and 7
```

# 10. Control Flow

- All block-forming constructs in Valiance follow the same rule:

```
<construct> => <code>  #? Single line - no `end` needed
<construct> =>
  <code>  #? Multi line - `end` required
end
```

- The rule is determined at the `=>` — if the first non-whitespace, non-comment token after `=>` is a newline, the block is multi-line and requires end. Otherwise the block ends at the end of the line.
- All control flow structures execute with "state semantics" - this means that a block of code can write to variables in the parent scope.

## 10.1. `match`
- Match pops one or more values off the stack and execute code of the first pattern matched by a series of patterns.

```
match =>
  <case> => <code>
  <case> =>
    <code>
  end
end
```

- A case describes what to match against one or more stack values, and consists of one or more case items separated by `,`. Each item corresponds to one stack position from the top down. All cases in a match block must have the same number of items.
- A case item can be:
	- Literal - an exact value: `10`, `"hello"`
	- Condition - a predicate: ``if > 5``
	- List pattern - a structural match: `[1, _, 3]`, `[1, $x = _, 3]`, `[1, ..., 3]`
	- Type match - a type check with optional binding, destructuring, and guard: `as :Type`, `as x: Type`, `as :Obj(field)`, ``as :Type if > 5``
	- Wildcard - matches anything: `_`

- Within a single item, `||` separates alternatives:

```
3 || 4 => ...              #? literal alternatives
if > 5 || if < 2 => ...    #? condition alternatives
```

- Examples:

```
match =>
  10 => "The number was 10"
  if > 5 => "The number is bigger than 5"
  _ => "Too small"
end
```

```
match =>
  [1, _, 3] => "3 items, don't know the middle"
  [1, $x = _, 3] => "3 items, the middle is ${$x}"
  [1, ..., 3] => "Who knows how many items, but the first is 1, the last is 3"
  [1, ..., 3, $y = ..., 6] => "Similar deal, but y is a list"
end
```

```
match =>
  as :Type => "Type match"
  as x: OtherType => "Named type match"
  as :Number if > 5 => "Type match with guard"
  as :Obj(param, param) => "Destructured object"
  as y => "Default named type match"
end
```

```
match =>
  1, 2 => "Top of stack was 1 and then 2"
  3 || 4, 5 || 6 => "Top of stack was either 3 or 4, and then 5 or 6"
  if > 10 || if < 4, [1, 2, 3] => "Weird stack layout, but sure"
  _, _ => "default case"
end
```

- The branch body is given the matched arguments.
	- Branch bodies do not pop from the outer stack. This is to ensure consistent static typing
	- The result of a match statement is pairwise unions of each branch. If any branch returns less than the maximum multiplicity, `None` is returned as padding.
- Each match case much match the same number of values. This is because the match statement will pop as many items as the arity of the case. Note that this is not equivalent to the arity of the branch
- Exhaustive pattern matching is required. If it is not practical or desirable to specify all possible cases, `_` can be used as a case

## 10.2. `assert`

- Run a condition that returns a `#boolean Number`, and if it is 0, panic.

```
assert =>
	<condition>
end
```

- `<condition>` peeks its arguments from the stack. i.e. does not pop them.

## 10.3. `assert...else`
- Run a condition that returns a `#boolean Number`. If it is 1, continue execution. Else, return the result of the `else` block wrapped in an `AssertError` (a built-in type implementing `Err`)

```
assert =>
  <condition>
else =>
  <error value>
end
```


## 10.4. `if` / `else if` / `else`

### 10.4.1. `if`

- It's an if statement, but only one branch.
- Execute the branch if the condition evaluates as truthy.
- `if (cond) => code end`
- `cond` must return `#boolean Number`
- Return type is the top of the stack type of `code` but optionalised. `None` is returned if not executed.
- `cond` peeks its arguments - in other words, it doesn't pop them.
	- This is because if bodies quite commonly operate on the stack item they check.
	- Saves extra `dup`s and `copy`s

```
if (2 + 2 == 5) => "Uh oh" end
#? String? - Will most likely be None
```

- Condition is evaluated according to truthiness rules.
### 10.4.2. `if`/`else`

- Extension of `if` to allow for an `else` block
- `else` can appear where `end` would be expected for `if`
	- `if (<cond>) => <code> end else => <code> end` == `if (<cond>) => <code> else => <code> end`

```
if (2 + 2 == 5) => println("Math is broken")
else => println("Math is fine") #? This will hopefully be printed
```

- Note that the `if` and `else` blocks must take the exact same parameters.
	- If the `if` block takes `Number, Number`, then the `else` block must also take `Number, Number` (or have an overload set option)
	- This restriction is for static analysis to be possible - just the number of arguments doesn't suffice. It can't be a union type nor a overload set either. `"boom" if (0) => halve else length`. `halve` not defined on string, but type of `length` _is_
- However, if one block were `OverloadSet[(Number, Number) -> ..., (String, String) -> ...]` and the other were `Number, Number`, that would be fine.
	- The `OverloadSet` would be inferred to be always resolved as `Number, Number`
	- Two overload sets will be the intersection of the two. BUT the `OverloadSet` will then be used as the inferred type of the if statement.

```
if (1) => + else => /
```

- Type of `if` block = `OverloadSet[(...)]`, type of else block = `OverloadSet[(...)]`. Type of overall if statement = intersection of the two sets.
	- Generics will be considered the same as a well specified overload, and the well-specified overload will be kept.
- Return type of `if/else` = union of return stacks. Missing values across branches are unioned with None
    - Only the input needs to be consistent (multiple points of divergence vs one uniform merging point)
 
### 10.4.3. `if`/`else if`/`else`

```
#? In practice, use a match statement
if ($name == "Bob") => println("You're Bob!")
else if ($name == "Jeff") => println("You're Jeff!")
else => println("No match")
```

- All conditions must take the same number and types of parameters
- Each condition is checked against the same values
	- Conceptually as if it were a fork
- `else` must be last part of the chain
	- `if/else/else if` is invalid

## 10.5. `foreach`
- A `foreach` loop iterates over items in a list and applies code to each item.
- A `foreach` loop returns `None` if it executed to completion, otherwise it returns whatever was included in the break value.
	- If multiple values are returned by a `break`, `None` is returned for each value if the loop executes to completion
- The iterable used in the foreach loop is popped from the stack. Note that it must be a list type. It is a compile error to foreach an atomic value

```
foreach (<variable name>) =>
  <code>
end

#? Or

foreach (<variable name>) -> (<return annotations>) =>
  <code>
end
```

- `variable name` can be either one name or two names. One name means just the iteration variable. Two names means iteration variable and index.
- `return annotations` is optional and provides explicit type annotations for anything returned by any `break`s.
- `code` inside a `foreach` loop can write to variables in the parent scope.
- The input for each loop iteration is the item or `index, item` if index is specified.
- These inputs will cycle.
  
#### 10.5.1. Suppressing lint advice

Lint findings can be suppressed for one statement with `@lintOff`. With no
arguments it suppresses every lint produced while analysing the following
statement. Pass one or more stable lint codes to suppress only those rules:

```vlnc
$total = 0
@lintOff("prefer-fold")
[1, 2, 3, 4] foreach (n) => $total := + $n end
```

Place `@lintFileOff` at the beginning of a source file to suppress lints for the
rest of that file. It accepts the same optional list of lint codes:

```vlnc
@lintFileOff("prefer-fold", "prefer-vectorisation-or-map")
```

`@lintFileOff` without arguments disables all lints in the file. Suppressions
do not hide errors or ordinary warnings.

### 10.5.2. `break`
- While not a control flow structure, `break` has special syntax for terminating a loop early
- `break <value>` will terminate a loop and push `value` to the stack
- `break (<values>)` will terminate a loop and push all items in `values` to the stack.
- If there are multiple breaks in a loop with differing multiplicities, then the breaks with fewer values will be padded with `None`s
```
define find(ns: Number+, number: Number) -> Number? =>
  $ns foreach (n, ind) =>
    if ($n == $number) => break $ind
  end
end
```

## 10.6. `while`
- Unbounded iteration until a condition is met

```
while (<condition>) =>
  <code>
end
```

- `condition` must return a `#boolean Number`
- `condition` will operate on the top of the stack on its first iteration, and then on the results of the last while loop iteration thereafter.
- Consequently, `code` must return the same signature expected by `condition`
- `condition` is used to set the expectations for `code` returns
- The return of the while loop is the loop results that made the loop terminate.
- While loops can write to variables in the parent scope.
- The input to `code` is whatever is expected by `condition`, this input will cycle.

Examples

```
$count = 0
while ($count < 10) =>
  println("Count is ${$count}")
  $count := increment
end

#? Functionally equivalent to

0 while (< 10) =>
  println("Count is ${top}")
  increment
end
```

- Sometimes, a while loop may need to work with more values than popped by the condition (or it may be desirable to explicitly annotate types)

```
while (<condition>) -> (<inputs>) =>
  <code>
end
```

- If `inputs` is specified, `condition` will use those inputs and cycle them.
- `code` must leave the expected inputs on the stack for the next iteration. Note that these will more often than not be computed from the results of the while loop. That is to say, the results of each loop are passed to the next. And thus the results must match the required number and types of inputs.
- Inputs have the same syntax as function parameters.
- Named inputs can be referred to as variables.

```
while (> 0) -> (count: Number) =>

end
```

## 10.7. `unfold`

* `unfold` lazily generates a list while maintaining state between iterations in a functional programming manner.

```
unfold (<condition>) -> (<parameters>) =>
  <body>
end
```

* `condition` and `parameters` are optional.

```
unfold => <body>
unfold (<condition>) => <body>
unfold -> (<parameters>) => <body>
```

* The initial state is taken from the stack.
* Each iteration operates on its own local stack.

  * The local stack is initialised with the current state.
  * Changes to the local stack do not affect the outer stack.
  * The outer stack only receives the resulting lazy list.
* At each iteration, `unfold` evaluates `condition` using the current state.

  * Truthy means continue.
  * Falsey means stop.
  * `condition` peeks its inputs and does not modify the state.
* If `condition` is omitted, the list is generated infinitely.
* If iteration continues, `body` is executed using the current state as its input.
* The resulting local stack determines both the value generated by the iteration and the state used by the next iteration.

### 10.7.1. Implicit output

* If the multiplicity of `body` is less than or equal to its arity, normal input cycling is applied.
* The entire resulting local stack becomes the state for the next iteration.
* The value on top of that stack is also generated as the next item in the list.

```
0 1 unfold (true) -> (prev: Integer, next: Integer) =>
  +
end

#? 1, 1, 2, 3, 5, 8, ...
```

* In the example, `+` has an arity of 2 and a multiplicity of 1.
* The unused input cycles, producing the next state:

```
prev next -> next (prev + next)
```

* The top value, `prev + next`, is both generated and retained as part of the next state.

```
1 unfold => + 1

#? 2, 3, 4, 5, ...
```

* When arity and multiplicity are both 1, the generated value is also the entire next state.

### 10.7.2. Explicit output

* If the multiplicity of `body` is greater than its arity, the top value is generated but is not included in the next state.
* Every value below the generated value becomes the state for the next iteration.
* The state values must match the parameters expected by `body`.
* The multiplicity of `body` should normally equal `arity + 1`.
* If the multiplicity is greater than `arity + 1`, values below the required state are discarded.

```
<next state...> <generated value>
```

* A generated value of `None` skips that iteration.

  * The next state is still retained.
  * No item is added to the list for that iteration.
* Skipping is only valid when the generated value is separate from the next state.

  * Consequently, `None` cannot skip an iteration when multiplicity is less than or equal to arity.
* To intentionally generate a `None`, it must be wrapped in `Some`.

### 10.7.3. Parameters

* `parameters` explicitly define the values used as state.
* Parameters have the same syntax as function parameters.
* Named parameters can be referred to as variables in both `condition` and `body`.
* The state produced by each iteration must align with the number and types of the parameters.

```
0 1 unfold ($next < 50) -> (prev: Integer, next: Integer) =>
  +
end

#? 1, 1, 2, 3, 5, 8, 13, 21, 34
```

* If `parameters` are omitted, the state is inferred from the inputs required by `condition` and `body`.

### 10.7.4. Infiniteness

* The resulting list is evaluated lazily.
* An iteration is only executed when another item is requested from the list.
* `unfold` may therefore produce an infinite list without eagerly executing forever.
* The resulting list is tagged as `#infinite`.

  * Although an `unfold` may terminate, it is not always possible to determine this statically.
  * All lists produced by `unfold` are therefore marked infinite for safety.
  * `#-infinite` can be used to remove the tag when appropriate.

## 10.8. `at`

- A way to control vectorisation, applying a function `at` certain depths
- `at (${levels}) => <code> end`
- `levels`  is a list of names (i.e. variable identifiers), followed by an optional arbitrary number of `+`s
- Each name corresponds to an argument, and specifies when to stop digging down when vectorising.
- For example:

```
[[1, 2], [3, 4]] [5, 6]
at (list+, item) => append
#? Gives
#? [[1, 2, 5], [3, 4, 6]]
```

- In the example, `append` is applied for every list in `[[1, 2], [3, 4]]` zipped with every item in `[5, 6]`
- While `append[Number+, Number]` would work, what if it weren't as easy to specify the type?
- `at` makes it so that you do not have to worry about the type.
- Another example:

```
[[[1, None, "s"], ["h", 5, None]]] #? (String|Number)?+3
#? You _could_ write
getOrElse[(String|Number)?](0)
#? Or, simply
at (_) => getOrElse(0)
```

# 11. The `define` structure

- Functions need to be called, whereas elements are called immediately
- `define` allows for custom elements to be defined, ready to be used just like any other element.
- If an element already exists, `define` adds a new module-scoped overload to the element. Note that module-scope overloads overwrite imported and built-in element overloads.
  - Use `*::<element>` to explicitly access the built-in overloads when a user-defined element shadows them, for example `*::Some(1)` or `1 2 *::+`.
  - Otherwise, a new element is created.
- Syntax:

```
define[<generics>] <name>(<params>) -> <returns> => <code> end
```

- `generics` is optional.
- Generic parameter lists contain only generic names, for example `define[T]` or `define[T, U]`. Constraints are written in ordinary type positions instead of inside the `[]`.
- `params` is optional, but must contain at least one parameter if specified.
- `returns` is optional

- Example:

```
define doubleAndAdd5(n: Number) =>
  * 2 + 5
end

10 doubleAndAdd5 #? 25
```

## 11.1. Shared Explicit Overload Bodies

`overload(parameter types -> return types)` attaches an additional explicit
signature to the next `define` or `fn`. Multiple overload declarations may be
stacked, with comments and whitespace between them and the declaration. The
overload signatures contain types only; parameter names and defaults come from
the following function declaration.

```valiance
overload(Number+ -> Number)
overload(String+ -> String)
define sum(xs) => fold: +
```

For an otherwise untyped function, these signatures replace ordinary function
inference and the body is analysed once for each signature. If the following
function already has declared parameter or return types, its own signature is
retained and the `overload` signatures are added. Every overload signature must
have the same parameter count as an explicit parameter list on the following
function.

## 11.2. Optional Arguments

- Sometimes, it is helpful to have "configuration" style parameters.
	- For example, you might want `sort(list)` to do normal sorting, and `sort(list, key=function)` to sort by a function
- Problem is that the fixed arity requirement of overloads means you can't have `sort[T](T+) -> T+` and `sort[T](T+, Function[T -> Comparable]) -> T+` on the same element.
	- Fine for sorting, but sometimes it can get complicated
- Additionally, taking optional parameters from the stack would make the stack effect of a call context-sensitive.
	- That defeats the point of using fixed arity to reason about stack behaviour.
- However: there is one way to unambiguously specify the arguments to a element: ECS.
- Using that, `define` allows trailing parameters at the end of an element definition to be given a default value. This makes those parameters optional for ECS calls.
	- Optional arguments do not change the plain stack arity of the element.
	- A plain stack call still behaves as though every non-`:` parameter is required.
	- Optional arguments are supplied only through ECS.
	- Function arguments can be specified with `:` syntax though.
		- _ALL_ function arguments must be specified though
- `= <value>` after a parameter declares the default value
- Example:

```
define[T] sort(:T+, key: Function[T -> Comparable] = 'top) -> T+ => ... end

[4, 1, 3] sort(_) #? Calls with default key
[4, 1, 3] sort #? Compile error - plain calls still expect full stack arity
[4, 1, 3] sort: negate #? Overwrite key
[4, 1, 3] sort(_, 'negate) #? Overwrite key
[4, 1, 3] sort(key = 'negate) #? Explicit name
```

- Note that with ECS, not all optional args must be specified
- A named argument does not need to account for the position of other non-optional args
- Passing an optional as an unnamed arg _must_ account for non-optional args
	- Like with `sort(_, 'negate)`
	- Otherwise, `'negate` is treated as the thing to sort, which is a compile error.
 
## 11.3. Overloads and Arity Consistency

- All overloads of an element must have the same arity and multiplicity
	- Compile error if there are overloads with different arities and/or multiplicities
- While mixed arity is possible in Valiance's type system, mixed-arity overloads are typically indicative of elements that should have different names.
	- Instead of `sort(T+)` and `sort(T+, Function[T -> U])`, consider `sort(T+)` and `sortBy(T+, Function[T -> U])`
	- Alternatively, `sort(T+, Function[T -> U] = 'top`

 ## 11.3. `define` and Capturing

 - A `define` may capture variables from an enclosing function scope, but not from top-level assignments. Top-level defines should depend on parameters, stack inputs, other elements, and niladic constants instead of module-local `$name` bindings.
 - Variables captured from an enclosing function are captured in their state as they are before the element definition. That is, whatever variable values were set before the definition is evaluated is what is captured.
 - Captured variables are restored to that captured state at the start of every call. Assignment to a captured name inside the function does not persist into the next call.

```
define makeMultiplier(x: Integer) =>
  fn (y: Integer) => $x * $y
end

$double = makeMultiplier(2)
$triple = makeMultiplier(3)

$double($triple(4)) #? 24
```


## 11.4 Defining Nilads

- Elements that take 0 parameters _must_ have a name that starts with `\`.
- This ensures that the parser can reliably detect niladic elements. This is important for getting chain parsing correct.

# 12. Objects

- Objects are comparable to `structs` or `records`. Structural key-value pairs with all members known at compile time.
- Objects have associated members, but do not own any methods
	- Rather, elements are defined on object types.
- Syntax:

```
object[<generics>] Name =>
 ...
end
```

- `generics` is any generic type variables the object needs. A generic can be bounded with `T: U`, `T: any U`, or `T: above U`; `any` is an upper bound and `above` is a lower bound.
- `Name` is the name of the object
- Object members are defined as `<access modifier> $<name>: <type> = <value>`
- `access modifier` is one of `public` (public read, public write), `readable` (public read, private write), or `private` (private read, private write). `access modifier` can also be omitted, making the member `readable` by default.
- If `value` is not specified, then the member _must_ be set by the end of _all_ constructors.
- If `value` is provided, `type` can be omitted.

## 12.1. Constructors
- Constructors are really just elements with the same name as the object.
- Example:

```
object Person =>
  $name: String
  $age: Number
  define Person(name: String, age: Number) =>
	$self.name = $name
    $self.age = $age
  end
end
```

- However, that leads to noise like `$self.name = $name`.
- If no constructors are defined, then a default constructor will be created. This default constructor will have one parameter per field, in the order they are defined.

```
object Person =>
  $name: String
  $age: Number
end
#? A constructor of type Function[String, Number -> Person] is automatically created.
```

- Given constructors are just elements, you can create an object as if it were a normal element:

```
"Jeff" 67 Person
Person("Jeff", 67)
```

## 12.2. Object Friendly Elements
- As stated, objects do not own any methods. Instead, elements are defined on objects and static dispatch handles message passing.
- Example:

```
object Person =>
  $name: String
  $age: Number
end

define greet(:Person) => println("Howdy, {$.name}!")
```

- However, not all elements should be able to access the internals of an object. Especially given that access modifiers exist.
- Therefore, there is a distinction between elements defined outside an object and elements defined inside an object.
- Elements inside an object are termed "object friendly elements". Object friendly elements have full read and write access to all members of an object.
- Note that the object is an implicit part of the element parameter list.
	- The object becomes the leftmost parameter.
- Elements outside of an object can only read `public` and `readable` members, and can only write to `public` members.
- Note: elements defined outside of an object take priority over an object friendly element. This is because: a) a library author realistically is not defining such an element without good reason and b) a user of a library would be defining such a function to specifically overwrite the default element.
- However, you can always access the original object friendly element using `<object name>::<method name>`. `name::element` will always refer to the object friendly element.
- Example:

```
object Foo =>
  $x: Number
  define get => $self.x
end

define get(:Foo) => $.x + 5

Foo(10) get #? 15
Foo(10) Foo::get #? 10
```

## 12.3. Member Access and Writing
- Members can be accessed from an object by:
	- `$<name>.<member>` if an object is stored in a variable
	- `$.<member>` if an object is on the stack.
- Member access always vectorises. If you have a list of objects, member access will retrieve that member for each object.
- Members can also be written to (if context allows it) by:
	- `$<name>.<member> = <value>` if an object is stored in a variable
	- `$.<member> = <value>` if an object is on the stack.
- Augmented assignment is the same.
- Note that writing to a member does _not_ mutate the object. It instead creates a new object with every other field copied.
	- Also note that the implementation may actually use mutation under the hood if it is determined it is safe to do so. The end user never experiences mutation though.
- This is consistent with the fact that writing to a variable only updates what is inside the variable box.

### 12.3.1. Member Access on Optional Values

- If an object has an optional type (`Some[T] | None`), ordinary `.` access is unsafe and is rejected.
- Use `->` to access a field through an optional receiver:

```
$name->member
$->member          #? Receiver is on the stack.
```

- A present receiver is unwrapped, the field is read, and a non-optional field is wrapped back in `Some`. An absent receiver propagates `None`.
- If the selected field is already optional, the result is flattened rather than becoming `Some[Some[U] | None]`.
- Safe access vectorises over collections of optional receivers.
- Safe accesses can be chained:

```
$root->branch->leaf->value
```

  Any `None` encountered in the chain propagates to the final result.
- `.` and `->` may be mixed when each receiver type permits it. Ordinary access can precede the safe boundary:

```
$root.branch.leaf->value
```

  However, `$root->branch.leaf` is rejected because `$root->branch` is still optional. Continue with `$root->branch->leaf` instead.
- Safe assignment writes through a present receiver and cancels the write for `None`:

```
$person->age = 37
```

  The variable remains optional in both cases. As with ordinary member assignment, the visible semantics reconstruct the object rather than mutating it in place.

## 12.4. `$self`

- Inside an object friendly element, `$self` can be used to retrieve the object state as it was at the time of the element call.
- `$self $.member` and `$self.member` are both valid.
- `$self $.member = <value>` and `$self.member = <value>` are both valid. But only `$self.member = <value>` will update what is returned by `$self`.
- Note that returning `$self` is important if you want to chain object-friendly elements.

## 12.5. Destructors

- A destructor is an element called automatically when an object is no longer reachable - that is, when its reference count hits zero.
- Syntax:
  
```
define ~<ObjectName> => ... end
```

- Destructors are intended for infallible, silent cleanup - releasing handles, freeing resources that cannot fail, etc.
- The destructor of an object must not panic. That is, the destructor cannot call any elements that have the `Panic` tag. If cleanup may fail, use `@mustcall` instead to enforce explicit handling before the object goes out of scope.

- Consider:
  
```
import {system}
object File =>
  private $handle: system.StreamHandle
  define File(filename: String) =>
    $handle = system.openFile($filename)
  end
  define read -> String => system.readStream($self.handle, all = true)
  define write(:String) => system.writeStream($self.handle, _)
  define close => system.closeStream($self.handle)
  define ~File => system.releaseHandle($self.handle)
end
```

- Here, close handles the fallible cleanup - if enforcement is needed, `@mustcall` can be used to ensure it is called before the object goes out of scope. `~File` handles only the infallible release of the underlying handle.

## 12.6. Objects, Stack Manipulation, and Memory Management

- When `dup` is called on an object, it's reference count is incremented.
- Note that `copy` calls `dup` for each reference to an item in the poststack.
- `move` only calls `dup` if there is more than one reference to an item in the poststack.
- If an object should not be allowed to be duplicated, then an object can define `dup` as an object friendly element with the `@error` annotation
- For example:

```
object WriteFile =>
  ...
  @error("Writeable files cannot be duplicated")
  define dup => end
  ...
end
```

- A compile error will be raised if an object marked as un-duplicatable is duplicated.
- A `DuplicationFault` will be raised if an object marked as un-duplicatable ever has its reference count exceed 1. This is for cases where duplication may not be detectable by the compiler (e.g. duplication of a generic type or pushing a variable multiple times)

- When `pop` is called on an object, the reference count for that object is decremented. If a `pop` would make the reference count reach 0, then the destructor is called.
- `pop` will panic with a `CleanupFault` if an object would be destructed without any of its `@mustcall` obligations being met.
- However, an element can define `pop` as an object friendly element with `@mustcall` elements. This element will only be called when the reference count reaches 0.
- `pop` can be fallible, but will always call the destructor, even on panic.
- Example:

```
@mustcall(all = ["commit"])
object Transaction =>
  ...
  define pop =>
    $self commit
  end
end
```

## 12.7. Object Example - `Counter`

```
object Counter =>
  $count: Integer = 0
  define increment =>
    $self.count := + 1
    $self
  end
  define decrement => $self $.count := - 1
  define reset =>
    $self.count = 0
    $self
  end
end

Counter increment increment $.count #? 2
```

## 12.8. Shared State Objects
- The base Valiance object-oriented story is suitable for 99% of use cases.
- However, it is unable to represent objects that require shared mutable state.
- For example, consider a rudimentary doubly linked list implementation:

```
object[T] Node =>
  #? Members declared public for convenience.
  public $previous: Node? = None
  public $next: Node? = None
  $value: T
end

object[T] DoublyLinkedList =>
  $head: Node? = None
  $tail: Node? = None

  define append(item: T) =>
    #? x <-- (temp) --> x
    $temp = Node($item)
    if ($tail empty?) =>
      $self.head = $temp
      $self.tail = $temp
    else =>
      $self.tail.next = $temp
      $temp.previous = $self.tail #? Stores $self.tail in current state
      $self.tail = $temp #? Does NOT update what $temp.previous refers to
    end
  end
end
```

- As pointed out, `$temp.previous = $self.tail` creates an immutable copy of what is stored in `$self.tail` at that point in time.
- Updating `$self.tail` in the next line does not update what is stored in `$temp.previous`
- Thus, there is an extension to the object system. Prefixing an object name with `&` makes it a "shared-state object".
- When an instance of a shared-state object is created, what you get back is not the object itself, but a *handle* to it. The actual object lives in a per-type, per-scope arena.
- The actual object lives in an arena. There is one arena per shared-state object type, per scope. For example, if a scope creates three `&T` instances, all three live in the same arena - the `&T` arena for that scope. When the scope exits, the arena is dropped as a unit, freeing all instances at once, unless any handles to objects within it remain reachable outside the scope - in which case the arena escapes with them.
- All operations on a handle operate on the underlying object in the arena. Handles can be freely copied and passed around - all handles to the same object always see the same data.
- This is the key difference from normal objects. With normal objects, storing a reference to another object captures a snapshot of its value at that moment. With shared-state objects, storing a handle means "this object, whatever it currently contains." Updates to the object are immediately visible through any handle pointing to it, because the handle always refers to the same arena slot rather than a frozen copy.
- Applying this to the linked list example:

```
object[T] &Node =>
  #? Members declared public for convenience.
  public $previous: &Node? = None
  public $next: &Node? = None
  $value: T
end

object[T] DoublyLinkedList =>
  $head: &Node? = None
  $tail: &Node? = None

  define append(item: T) =>
    $temp = &Node($item)
    if ($tail empty?) =>
      $self.head = $temp
      $self.tail = $temp
    else =>
      $self.tail.next = $temp
      $temp.previous = $self.tail #? Stores the handle stored in $self.tail
      $self.tail = $temp #? $self.tail stores the handle in $temp
    end
  end
end
```

- The only change between the original version and the shared-state object version is that the node class is now `&Node`. `$head` and `$tail` both hold handles that, whenever attributes of the handle need to be read/written, accesses the underlying `&Node` object.
# 13. Traits
- No object inheritance -> Reliance upon composition.
- But! Sometimes, subtyping is very helpful
        - You might want an `Animal+`  to represent a `(Dog|Cat)+`
- Valiance allows for the definition of traits:

```
trait[<generics>] <name> =>
  <body>
end
```

- `generics` is optional and contains only generic names.
- Generic parameter lists contain only generic names. Trait constraints are expressed where values are typed, for example `:Shape`, `:Addable[T]`, or an anonymous trait type.
- `body` contains element definitions OR elements that must be implemented by any implementer.
	- A normal define is a default impl
	- A required impl is a define without a body, but using `extend` instead of `define`

- Example:

```
trait Shape =>
  extend getArea -> Number
  define largerThan(other: Shape) =>
    $self $other | both: getArea | >
  end
end 
```

- An object implements a trait using `object <objectname> as <trait> =>`
	- An object must have a base definition before it can implement a trait. This is because the trait impl cannot define a constructor,

```
object <name> as <trait> => <impls> end
```

- Continuing the `Shape` example:

```
object Circle => $radius: Number

object Rectangle =>
  $width: Number
  $height: Number
end

object Rectangle as Shape =>
  define getArea => $self.width * $self.height
end

object Circle as Shape =>
  define getArea => squared $self.radius * 3.14
end
```

- Trait impl has same member access as the main object block.
- Traits can also implement other traits using `trait <trait1> as <trait2>`
	- Traits do not need to have a base version to implement another trait.
	- An implementing object must satisfy inherited requirements somewhere in the implementation chain. Inherited default bodies are reused automatically and do not need to be repeated.

```
trait Logger => extend log(:String)
trait ErrorReporter as Logger =>
  define reportError(:String) => $self log
end

object ConsoleLogger => end
object ConsoleLogger as Logger =>
  define log(:String) => ...
end
#? No extra impl needed for ErrorReporter
object ConsoleLogger as ErrorReporter => end
```

## 13.1. Anonymous Traits

- Anonymous traits are inline trait definitions that can appear anywhere a type is expected.
- They are useful when you need behaviour rather than membership in a particular named trait.
- For example, a named trait for addable values can be written as:

```
trait[T] Addable =>
  extend +(:T, :T) -> T
end

define[T] sum(:Addable[T]+) -> T => fold: +
```

- The same requirement can be written structurally with an anonymous trait:

```
define[T] sum(
  :trait[T] =>
    extend +(:T, :T) -> T
  end +
) -> T => fold: +
```

- This accepts any type that has a visible `+` overload taking two `T` values and returning `T`; the type does not need to explicitly implement a named `Addable` trait.
- The requirements of an anonymous trait are available inside the element body, so calls like `fold: +` can type-check against the inline requirement.
- Anonymous traits can have generic parameters just like named traits, but they do not have a name and cannot be implemented directly with an `object ... as ...` block.
- Structural requirements can refer to the constrained type directly. For example, a generic find operation can require structural equality without requiring a named equality trait:

```
define[T: trait => extend ===(T, T) -> #boolean Number] find(
  haystack: T+,
  needle: T
) =>
  $haystack foreach (item, ind) =>
    if ($item === $needle) => return $ind
  end
end
```

# 14. Variants

- Objects and traits provide enough object-oriented support for comfortable OOPing. However, OOP support can be taken one step further with variants (what might be called `enums`, `sealed classes`, or `sum types` in other programming languages).
- A variant is a closed set of objects. Unlike a trait, which any object can implement, a variant's members are declared upfront and cannot be extended outside the variant definition.
- The benefit of this closed set is exhaustive pattern matching — the compiler knows every possible member and can guarantee that a `match` on a variant handles every case. Adding a new member to a variant will raise an exhaustivity error at every `match` site that doesn't handle it.
- Syntax:

```
variant[<generics>] <name> =>
  <extend declarations>
  <member definitions>
end
```

- `generics` is optional and contains only generic names.
- `extend` declarations come first, declaring the interface that every member must implement.
- Member definitions follow, each providing their own fields and implementations.
- Example:

```
variant Shape =>
  extend getArea -> Number

  Circle =>
    $radius: Number
    define getArea => squared $self.radius * 3.14
  end
  Rectangle =>
    $width: Number
    $height: Number
    define getArea => $self.width * $self.height
  end
end
```

- A compile error is raised if any member does not implement all `extend` declarations.
- The benefit of variants over traits is in pattern matching:

```
#? Assuming a trait definition
define typeOf(:Shape) =>
  match =>
    as :Rectangle => "Got a Rectangle"
    as :Circle    => "Got a Circle"
    default       => "Huh?" #? default case required - trait is open
  end
  #? If a Triangle were added to the trait,
  #? there would be no compiler error to
  #? indicate a change is needed here.
end

#? Assuming a variant definition
define typeOf(:Shape) =>
  match =>
    as :Rectangle => "Got a Rectangle"
    as :Circle    => "Got a Circle"
  end
  #? No default case needed - variant is closed
  #? Adding a Triangle member to the variant
  #? will raise an exhaustivity error here,
  #? indicating changes are needed.
end
```

- Variant members are only accessible as their variant type from outside the file. They cannot be used independently of the variant outside of the file they are defined in.
- Generics example:

```
variant Option[T] =>
  Some => $value: T
  None => end
end
```

# 15. Enums

- Sometimes, you may want a variant-esque structure without the ceremony of creating objects and traits
- The `enum` keyword is basically a lightweight `variant`
- Syntax:

```
enum[<generics>] <name> =>
  <memberName> = <memberValue>
}
```

- Note that generics is optional and contains only generic names. If no generic is provided, the enum is considered to just be names. Note that if no generics are provided, then members cannot have corresponding values. Note that if a generic is provided, all members must have a corresponding value 

- For example:

```
enum Colour =>
  RED
  GREEN
  BLUE
end

enum[String] TokenType =>
  NUMBER = "Number"
  STRING = "String"
  L_PAREN = "("
  R_PAREN = ")
end
```

- Member access with `enumName.member`
- Value access with `enumName.member.value`
- Example:

```
Colour.RED
TokenType.NUMBER.value
```

- The enum name can be used as a type, just like a trait or a variant.
- Enums are closed world, meaning that, like variants, you can have exhaustive checking.

# 16. Generics
- Type substitution mechanism.
- There is no type erasure with generics. If something is passed an object with a generic, both object and generic types are available. 
- Generic parameter lists contain only names, such as `T` or `T, U`. Bounds like `T: SomeTrait` are not valid in the `[]`.
- Generic constructors are invariant by default.
- Object, trait, and variant declarations can infer variance from how their generic parameters are used.
  - Readable fields and returns are covariant positions.
  - Function parameters are contravariant positions.
  - Public writable fields count as both covariant and contravariant, so they make the parameter invariant.
- Collection item types are covariant: for example, `Car+` can be passed where `Vehicle+` is expected if `Car` implements `Vehicle`. Rank rules still apply separately.
- Generic constraints are expressed where the constrained value is typed. Use named traits for nominal constraints, or anonymous traits for structural constraints.

```
define[T] sum(:Addable[T]+) -> T => fold: +

define[T] sum(
  :trait[T] =>
    extend +(:T, :T) -> T
  end +
) -> T => fold: +
```

## 16.1. `atomic` type marker

- `atomic` is an overload-resolution marker that requires the marked position
  to be scalar (rank zero). Like `exact`, it is retained in callable parameter
  signatures, but it is not a runtime type and is erased from the value type
  visible inside the function body. A top-level marker written on a return or
  cast target is likewise erased; it cannot create a distinct value type.
- Its main use is preventing a collection pattern from absorbing extra rank
  into a generic. Without the marker, `T+` accepts a rank-2 list by binding `T`
  to a rank-1 list type. `T atomic +` instead requires `T` itself to be scalar,
  so the argument must be a rank-1 list of scalar values.

```
define[T] rankOne(values: T atomic +) -> T+ =>
  #? $values has type T+ here, not an "atomic type"
  $values
end

[1, 2, 3] rankOne        #? accepted
[[1, 2], [3, 4]] rankOne #? compile error
```

- `atomic` never changes what `T` means. If another parameter solves `T` as a
  collection type, a separate `T atomic` parameter cannot reinterpret `T` as
  that collection's scalar base; the overload is inconsistent and is rejected.
- An atomic occurrence can provide fallback evidence when it is the only place
  a generic appears. For example, a scalar argument to `T atomic` can still
  infer `T`. If ordinary occurrences also provide evidence, they determine the
  generic and the atomic occurrence validates that same solution.
- A generic function that forwards a value to an atomic parameter must expose
  the same guarantee in its own signature. An unmarked `U+` parameter cannot
  safely be forwarded to `T atomic +`, because a later call could instantiate
  `U` with a collection type.

```
define[T] rankOne(values: T atomic +) -> T+ => $values end

define[U] safeForward(values: U atomic +) -> U+ =>
  $values rankOne
end

# This is rejected at its definition, not deferred until a bad call:
define[U] unsafeForward(values: U+) -> U+ =>
  $values rankOne
end
```

## 16.2. Generics and Unification

_Note: subject to determination about whether this is 100% correct_

- When a generic function is called, each parameter determines what the generic type variable must be based on its argument.
- Unification succeeds only if all parameters agree on the same type.
- Basically, a map and reduce scheme.
	- Each parameter solves for what type is needed to match the argument
	- All parameters are combined into a single type. If they can't be, a compile error is raised.
- Solve is roughly (where `T, X` are type variables, and `U, V, W` are concrete types. Additionally, `m >= n`). If a rule isn't specified, then it can't be unified.

```
solve(T, U) = T := U
solve(U[T], V[W]) = solve(T, W)
solve(T+n, U+m) = T := U+(m-n)
solve(T*n, U*m) = T := U*(m-n)
solve(T~n, U~m) = T := U~(m-n)
solve(T*n, U+m) = T := U*(m-n)
solve(T~n, U+m) = T := U~(m-n)
solve(T~n, U*m) = T := U~(m-n)
solve(T^n, U^m) = T := U^(m-n)
solve(T>n, U>m) = T := U>(m-n)
solve(T+n, U^m) = T := U+(m-n) // List type takes precendence
solve(T*n, U>m) = T := U*(m-n)
solve(T*n, U^m) = T := U*(m-n)
solve(T?, U?) = T := U
solve(T?, U) = T := U
```

The `atomic` marker is not a rank-zero type constructor and does not rewrite a
solved generic to its scalar base. It contributes scalar-validation evidence to
call resolution; the underlying generic still has one consistent meaning across
all parameters and returns.

Additionally:

- Unification does not happen across unions. That is, `solve(T|X, U|V)` will not occur, nor give `T := U, X := V` or `T := V, U := X`. Unification also does not happen across intersections. This is because both union types and intersection types can be arbitrarily reordered, meaning that there is no one correct arrangement.

- Combine is roughly (no assumptions are made about `n` and `m`) (commutative):

```
combine(T, T) = T
combine(T*n, T*m) = T*(min(n, m))
combine(T>n, T>m) = T>(min(n, m))
combine(T*n, T+m) = T*(min(n, m))
combine(T>n, T^m) = T>(min(n, m))
combine(T+n, T) = T+n
combine(T*n, T) = T*n
combine(T~n, T) = T~n
```

- `exact` and `atomic` are call-policy evidence, not alternate generic
  solutions. After ordinary evidence is combined, marked positions are checked
  against the resulting substitution and the actual argument shape.
- If an atomic occurrence is the only evidence for a generic, its scalar actual
  may supply the fallback solution; the marker itself is never part of that
  solution.

## 16.3. Anonymous Generics in Function Types

- If a function/element needs to create implicit generics for parameters, they will be part of the type.
- For example:

```
fn (x) => $x end
```

- There's no one single type that satisfies `x`. Instead, it is considered to be a generic type.
- But! There's no explicit generics in the function.
- So, the type of the function is `Function[@1 -> @1]`
- `@n` is effectively "anonymous generic type variable `n`"
- Anonymous generic types can be written in type positions, so generated
  signatures such as `(value: @1) -> @1` remain valid source.
- Anonymous generics are only used if a type can't be inferred from usage.

## 16.4. Row Polymorphism

- Consider the following function:

```
fn => $.x end
```

- What is the type of this function?
- It's `Function[@1(.baz: @2) -> @2]`
- When a parameter of an anonymous generic has a field accessed, it becomes part of the type.
- `${type}(${fields})` means that a value of `type` is expected, and that the type _must_ implement `fields`. Each field in `fields` must have its own type.
- This can also be used to constrain parameters:

```
fn[T, U] (x: T(.bar: U)) -> U => $x.bar #? Completely valid
```

# 17. Data Tags
- Data tags are compile-time metadata attached to values that represent properties about those values. They enable type-safe tracking of properties like sortedness, finiteness, or structural constraints without requiring explicit type hierarchies.
- There are 4 categories of data tags:
        - Constructed
        - Computed
        - Variant
	    - Unit
- Each category is handled differently throughout program flow
- Data tags can only add or remove themselves. They can poll to see if other tags are in on the act, but they can only decide if they're in or out. 
- To create a new tag type:

```
tag #<name> as <category>
```

- Category is one of `computed`, `constructed`, `unit`

- To apply a tag to a value, `#<name>`:

```
[1, 2, 3] #sorted
```

- The tag is then attached to the value. For example, `[1, 2, 3] #sorted` is a `#sorted Number+`.
- Tags can also be removed from a value using `#-<name>` or the equivalent `#-<name>` spelling. Attempting to remove a tag from a value that does not have that tag is a compile error.

## 17.1. Constructed Tags
- Constructed tags represent properties of data that are a consequence of how that data is constructed. For example, an infinite list can only be infinite if it is constructed that way.
- Constructed tags are sticky across ordinary operations and generic flow. A constructed-like tag guaranteed on any input is carried to every output whose rank is high enough, without requiring a tag overlay.
- A constructed tag is removed only by an explicit absence contract such as `#-infinite`, direct `#-infinite`/`#-infinite` removal, an exact return tag set that excludes it, or omission from that tag's own overlay return contract. Computed tags remain non-sticky.
- If a tagged input has effective rank `n`, the output carries the tag at depth `(output rank - 1)` when the output rank is at least `n`. For a tag at depth `d` on a rank-`r` input, its effective rank is `max(r - d, 0)`. If the output rank is lower, the tag is not carried.
- Runtime evidence follows the same rule through built-ins, user functions, casts, optimization, and serialized bytecode. Recursive return and cast contracts remove only evidence that their explicit tag policy excludes.
- For example, `#infinite [1, 2, 3] + 4` has type `#infinite Integer+`; no overlay is required for the constructed tag to survive the vectorized addition.
- Automatic propagation processes inputs from left to right. If different inputs carry disjoint constructed tags, the later input's tag replaces the earlier one, matching ordinary explicit tag-application order.
- Collection construction canonicalizes a tag shared by every item onto the collection at one greater depth. This keeps nested static types and runtime evidence aligned without wrapping each child redundantly.
- A constructed-tagged value can otherwise be used where the untagged base type is expected. Unit tags are the exception described below.

## 17.2. Unit Tags
- One might think that constructed tags would be helpful for attaching units to data. For example, you might have `km` as a unit you wish to attach to a number. By all means, `km` should stick to a number if it is passed into an operation - it's not information that should be easily lost.
- However, this can lead to situations where a unit number is used in a situation where it doesn't make semantic sense.
- For example, indexing a list by a distance doesn't really make that much sense.
- Unit tags are constructed tags with an extra rule: a unit-tagged value cannot be passed where that unit tag is not expected.
- Operations that consume a plain scalar, including collection indexing, therefore reject unit-tagged values. The unit must be explicitly removed with `#-unit` or `#-unit` when discarding it is intentional.
- A matching unit-tag overlay is an explicit permission for an otherwise plain implementation to consume that unit. Only the overlay's own unit is erased for underlying overload selection; unrelated units remain protected.
- Once an operation is permitted to consume a unit-tagged value, the unit follows the same sticky rank/depth rules as any constructed tag unless explicitly removed.

## 17.3. Computed Tags
- Some properties of data are more fragile than constructed tags. That is, they may be dependent on the computed structure of the data. For example, the sortedness of a list is computed from whether it is ordered, rather than solely when it is constructed. Additionally, the sortedness of a list is not sticky - whereas doing most things to an infinite list doesn't make that list finite, doing most things to a sorted list has a good chance of breaking the sortedness.
- Thus, computed tags are tags that are only kept if explicitly kept. The moment an operation can no longer guarantee the property represented by a computed tag, that tag is removed.
- This forms a parallel with constructed tags, which are only removed if an operation guarantees that it invalidates the property.

## 17.4. Variant Tags
- Sometimes, it may be useful to have specialised computed tags that are fully dependent on the actual runtime value of data.
- For example, while sortedness is a property that can be guaranteed between operations, the _direction_ of that sortedness can vary in a way undetectable by the compiler.
- For example, multiplying a sorted list of numbers by -1 maintains sortedness, but reverses the sortedness direction.
- Variant tags are runtime specialisations of computed tags. For example, if sorted is a computed tag, then ascending and descending can be represented as variants of the computed tag.
- All variant tags must have a parent computed tag.
- Syntax:

```
tag #<name> as #<parentTag>
```

- Example:

```
tag #sorted as computed
tag #ascending as #sorted
tag #descending as #sorted
```

- Applying a variant tag to a value automatically applies the parent computed tag.
- The compile-time type contains only the parent computed tag. The variant is retained as runtime evidence and may appear only in runtime match patterns, not in parameter, return, variable, overlay, or cast types.
- Removing the parent, or replacing it through a disjoint rule, also removes every runtime variant that depends on that parent. Applying a variant cannot leave its parent absent.
- Eg

```
define foo(:#sorted Number+) =>
  match =>
    as :#ascending Number+ => "Sorted low to high"
    as :#descending Number+ => "sorted high to low"
    _ => "sorted in some order, but there may be duplicates"
  end
end
```

## 17.5. Expecting Tags in Parameters

- To signify that a parameter expects data to have a certain tag, simply add that tag as part of the type.
- However, to signify that a parameter must have an absence of a certain tag, the tag must start with `#-` instead of `#`.
- When a tag is expected, a parameter is only matched if the argument has that expected tag. The argument can have any other number of tags, so long as it has the specified tag.
- However, it may be desirable to disallow this flexibility. Wrapping the set of data tags in `[]` in a parameter type requires the compile-time present-tag set to be exactly that set. An argument with any additional or missing present tags will not match. `[] T` therefore requires an exact empty tag set.
- Absence requirements are checked before erasable computed or constructed tags are forgotten, so a present `#tag` can never satisfy `#-tag` by implicit tag loss.
- Example:

```
define foo(:#someTag Number) => ...
#? foo can accept a `#someTag #otherTag Number`

define baz(:[#someTag] Number) => ...
#? baz cannot accept a `#someTag #otherTag Number`

def bar(:#-someTag Number) => ...
#? bar will not accept a `#someTag #otherTag Number`, but will accept an `#otherTag Number`
```

## 17.6. Disjointed Tags
- By default, all tags can coexist. But sometimes, that makes no semantic sense.
- For example, if you have a tag to say a list is empty, and a tag to say that a list is non-empty, then having a value tagged as empty and non-empty at the same time makes no sense.
- Thus, it is possible to declare two tags as being incompatible with each other.
- Syntax:

```
tag #<name> disjoint #<otherTag>
```

- Consider:

```
tag #A disjoint #B
```

- If `#A` is applied to a value tagged as `#B`, then `#B` is removed.
- If `#B` is applied to a value tagged as `#A`, then `#A` is removed.
- This is because the intention is to apply the new tag, making the old tag obsolete.
- In this way, the tag disjoint rule only belongs to `#A`. `#B` does not need to know about the rule.
- Explicit type signatures that contain disjoint present tags at the same position are rejected at compile time.
- Disjoint replacement is closed over variant parents: removing a computed parent also removes its runtime variants, while applying a variant restores its parent and removes tags disjoint with either one.

## 17.7. Tag Overlays
- To make use of tags, the tag needs to be included in a function's parameters.
- Sometimes though, tag interactions make no difference to a function's behaviour.
- For example, adding a number to a sorted list of numbers does not impact sortedness.
- But by default, `+` will strip a `sorted` computed tag, because it does not make an explicit guarantee about sortedness.
- One would need to add the following extension:

```
define +(:#sorted Number+, :Number) -> #sorted Number+ =>
  dip: #-sorted #? To avoid infinite recursion
  +
  #sorted
end
```

- Doing this for every single operation that does not change behaviour would be very ceremonious
- Additionally, there may be groups of operations with the same inputs (like mathematical operators) which all do not impact sortedness. This would lead to a lot of boilerplate.
- Therefore, tag interactions that do not change behaviour can be expressed in a streamlined manner:

```
#<tagname>: [<generics>] <elements> =>
  <signatures>
end
```

- For example:

```
tag #sorted as computed
#sorted: + =>
  (#sorted Number+, Number) -> #sorted Number+
  (Number, #sorted Number) -> #sorted Number+
  (#sorted Number+, #sorted Number+) -> #sorted Number+
end
#sorted: [T] filter => (#sorted T+, Function[T -> #boolean Number]) -> #sorted T+
```

- `generics` is only required if the element being overlayed requires generics, and contains only generic names.
- Note that the generic type need not have the same name as the element. Only the number of generics must be the same.
- Signatures do not have parameter names. Only parameter types.
- Multiple elements can be overlayed at once:

```
#sorted: (+, -, *, /) =>
  (#sorted Number+, Number) -> #sorted Number+
  (Number, #sorted Number) -> #sorted Number+
  (#sorted Number+, #sorted Number+) -> #sorted Number+
end
```

- Overlays can also be used for constructed tags:

```
tag #infinite as constructed
#infinite: [T] take =>
  (#infinite T+, Number) -> T+
end
```

- Absence of a constructed tag will remove that tag.
- Every overlay must require its attached tag positively on at least one input. An overlay can control only that tag in its return contract; it cannot add, remove, or preserve foreign tags.
- A constructed or unit overlay that preserves its tag must obey the rank/depth rule from 17.1. Unsafe rank changes are rejected during analysis.
- Overlay return contracts are reified on the actual runtime result, including user functions that suspend for nested calls and bytecode that has been serialized and restored.
- Constructed tags already flow through ordinary operations. Constructed overlays are therefore mainly useful for explicitly removing the attached tag, documenting a specialised rank contract, or granting a unit-tagged value permission to use an otherwise untagged implementation.

## 17.8. Tag Validators
- Sometimes you may want to validate that data being tagged actually exhibits the property of the tag.
	- Tags are meant to be compile time human trust based metadata, useful for avoiding costly runtime checks. But sometimes the semantic meaning of a tag may legitimately need validation before application.
- Tag validation is simply a `define` with the tag name. The `define` must return a `#boolean Number`.
- Tag validation occurs at runtime when a tag would be applied. A panic is raised if the validator fails (either returns `0`/`false` or panics). 
- Tag validators can have multiple overloads. Normal overload specificity rules choose the validator; declaration order does not decide between applicable overloads. Missing or ambiguous validator overloads are compile-time errors.
- Applying a variant runs every applicable validator in its chain, including the parent computed tag's validator, before any runtime tag evidence is committed. Validation is atomic: failure leaves the value untagged by that application.
- Example:

```
tag #Vector3 as constructed
define #Vector3(:Number+) =>
  length == 3
end

[1, 2, 3] #Vector3 #? Valid
[1, 2, 3, 4] #Vector3 #? Runtime error
["list", "of", "strings"] #Vector3 #? Compiler error.
```

- Applying a tag as part of a typed variable declaration also runs the validator. Validators may refer to top-level `const` values, which is useful for bounded unit tags such as tape pointers.
- Note that the compiler will optimise tag validators that always return `true` or `false` to ignore runtime checks. This is helpful if you just want to validate only on type. 

## 17.9. Importing Tags

- `import{<libraryName>.#<tagName>}`
- Importing a tag also imports any public elements that were attached to that tag with the tag-attached definition syntax.

## 17.10. Tag-Attached Elements
- Sometimes it may be desirable to import a set of elements whenever a tag is imported.
- Inserting a tag name before an element name in a define makes it so that the overload is imported when that tag is imported.
- Example:

```
define[T] #sorted sort(:#sorted T+) => top
```

- This overload of `sort` would be imported whenever `#sorted` is imported.
- Users still import the tag explicitly. The attached element arrives as a normal public element alongside the tag facts.

## 17.11. Tag Depth
- If you would have `(#tag T+)+`, you can rewrite it as `#tag+ T+`. Each `+` after a tag is a level of depth that tag applies at. ie levels of nesting from the top.
- Example: `(#B (#A T+)+)+` == `#A++ #B+ T+3`
- Numeric shorthand can also be used. `#tag+3` == `#tag+++`
- A tag depth cannot exceed the rank of the value it decorates. For example, `#tag+ Number+` is valid, while `#tag+ Number` and `#tag++ Number+` are compile-time errors.
- Single indexing lowers positive tag depth by one: indexing a `#tag+ T+` produces `#tag T`. Slicing preserves tag depth because the result retains the receiver's rank.

# 18. Element Tags

- Data tags are great for attaching metadata to stack items.
- But what if you want to attach metadata to elements and functions?
- Like for example, indicating that an element interacts with IO?
- Data tags can't help here, because they are only for data
        - Plus, they can be removed. If `IO` were a data tag, you'd be able to remove it from elements.
       - Additionally, tag depth for properties doesn't really make sense
- Thus, in addition to data tags, Valiance supports element tags.
- Element tags are sticky tags that propagate up to the caller of an element with an element tag.
        - For example, if an element/function calls an element that has `IO` attached, the caller will also have `IO` attached.
- There are two categories of element tags:
        - `property`
        - `companion`
- These categories do not impact tag flow. Rather, they impact _who_ can apply a tag. This will become obvious in the tag description sections.
- Unlike data tags, element tags do not have any overlay rules. If an element tag is present, it propagates up.
- But also unlike data tags, element tags can have type parameters.
        - This is useful for element tags like `Panic`, which require specification of the type of panic.
- Element tags are specified after the function type using `<>`. Multiple element tags are separated by `,`
- Element tag abscences are specified after the function type using `!` (eg `!Panic` to only accept functions that do not throw any errors).
- Examples:

```
Function[T -> ()]<Eager, IO>
Function[Number -> Number] <Panic[String]>
```

## 18.1. `property` Element Tags

- `property` element tags are tags that can be freely added by the user.
- `property` tags include:
        - `IO`
        - `Random`
        - `Panic[T]`
        - `Memoizable`

- Syntax:

```
tag <Name> as property
```

- No `#` before the name.
- Added to element definitions after the arguments:

```
define name(args)<element tags> -> returns => ... end
fn (args)<element tags> -> returns => ... end
```

- If property tags are not specified, they'll be implicit
- If property tags are specified, then using any property tag _not_ specified is a compile error.
        - For example, if you declare element `foo` uses only `Random` and then it uses `IO`, that's a compile error.
- An element does _not_ have to explicitly use an element with specified element tags.
        - Your element may be considered random or to use IO without using anything that is inherently random (e.g. direct command line access)

## 18.2. `companion` Element Tags

- `companion` element tags are tags that _cannot_ be added by the user.
- They can only be added by annotations or core system features.
- `companion` element tags include `Eager` and `Memoized`.
- Attempting to add a `companion` tag like a `property` tag is a compile error.
        - You should not be allowed to attach `Eager` to an element if it isn't made eager using the `eager` keyword.
- `companion` tags can still be expected as part of a parameter though.
- Syntax:

```
tag <Name> as companion
```

- No `#` before the name.
- Although companion element tags cannot be manually added, they still need to be defined in Valiance files so that other tags can use them in type parameters and in tag disjoints.

## 18.3. Other Notes on Element Tags
- Element tag abscence can be specified just like with data tags:

```
define[T, U] lazymap(xs: T+, function: Function[T -> U]<!Eager>) => ... end
```

```
define[T] callSafe(function: Function<!Panic>) => ... end
```

- Element tag abscence verified in back pass. Same one-in-all-in rule as constructed tags.
- Although functions can have data tags applied to them, element tags are for things that shouldn't be removable.
        - Don't use data tags for properties of functions that aren't value-level metadata.
- Element tags do not have `#` in their name

## 18.4. Tag Disjoints and Element Tags

- Element tags can declare that they are incompatible with other element tags:

```
tag Read disjoint Write
```

- A function or function type that explicitly includes both present tags is rejected.
- Data tags can declare that they are incompatible with element tags.
        - For example, most times an `#infinite` stack value should not be used in an `Eager` element.
- Syntax is exactly the same:

```
tag #data disjoint element
```

- Unlike when a data tag is disjoint with another data tag, this level of disjointedness throws a compile error.
- For example:

```
tag #infinite disjoint Eager
unfold (...) => ... end println #? Compile Error!
#? The #infinite from the unfold would need to be removed
#? with #-infinite
```

# 19. Annotations

- Where `:` modifies elements, annotations modify syntax structures
- There are 4 types of annotations:
        - Binding Conventions
                - These add additional bindings to the current scope. For example `@recursive` adds `this`.
        - Resolution Conventions
                - These change how certain compile time evaluations are resolved.
                - For example, `@mustcall` makes it so that an element must be called on a value before that value goes out of scope.
        - Return Conventions
                - These change how items from an element are returned. For example `@@tupled` wraps returns in a tuple. Note that such annotations are usually for things that are otherwise impractical to do from "first principles"
        - Invocation Conventions
                - These change how an element can be called. For example, `@error` makes calling an overload a compile error.
  - `@` for modifying structures, `@@`for modifying elements in ways that `:` can't.

## 19.1. `@recursive`
- `@recursive` is a binding convention annotation. It allows for tacit recursion by making the `this` element call the outer-most `@recursive` annotated function/element.
- Very useful for functions

```
$factorial = @recursive fn (:Number) =>
  match =>
    0 => 1,
    _ => * this - 1
    #? `this` calls the fn
  end
end

fn => #? Call this function A
  @recursive fn => #? Call this function B
    fn => #? Call this function C
      this #? Calls function B - it's the outer most recursive function
    end
  end
end
```

- Note that nested `@recursive` functions cannot call above the outer-most recursive function
        - `this` must be captured instead

```
#? Ignore the fact that this never terminates.
@recursive fn => #? Call this function A
  $outer = 'this
  @recursive fn => #? Call this function B
    this #? Calls function B
    $outer() #? Calls function A
  end
end
```

## 19.2. `@self`
- A return convention annotation
- Makes object friendly elements automatically return `$self`
	- Even if the return types are already specified.
- Compare:

```
object Counter =>
  $count = 0
  define increment =>
    $self.count := ++
    $self
  end
end

object Counter =>
  $count = 0
  @self define increment => $self.count := ++
end
```


## 19.3. `@@tupled`
- A return convention annotation
- Wraps the entire function return in a fixed-length tuple
- Useful for when you want to capture the whole output into a tuple, but you don't know how many items will be returned
- Tuple is determined by the function outputs.
        - If a function returns `Number, Number`, then `@tupled` will return `(Number, Number)`

```
define foo() -> Number, Number =>
  6 7
end

foo #? Pushes 6, 7
@@tupled foo #? Pushes (6, 7)
```

- "Why not wrap the function call in `()` then?"
        - Because that only takes the last returned item
        - `(foo)` would return `(7)`

## 19.4. `@error`
- An invocation convention annotation
- Only usable on `define`
- Marks an overload as a compile time error
        - Element must return a string
        - That string is the error message
- Primarily useful for tag overlays
        - Consider: extending `length` to be a compile time error when given `#infinite T+`
        - Just using exceptions won't cause compile-time error

```
@error("Cannot get the length of an infinite list.") define[T] #infinite length(:#infinite T+) => ...
```

## 19.5. `@warn`
- Also an invocation convention annotation
- Similar to `@error`, but generates a warning instead of an error.
- Useful for when something isn't an error, but also isn't the best.
        - Or, anything where you want to warn the user (perhaps performance etc)
        - Basically a lot more applicable than `@error`
- Only usable on `define`

```
@warn("This function is experimental. Use with caution") define foo() => ...
```

## 19.6. `@deprecated`

- A more specific `@warn` that doesn't require a full message.
- Only requires the name of what should be used instead
- Can also take `since` and `why` as parameters
- Only usable on `define`

```
@deprecated("bar") define foo() => ...
```

## 19.7. `@returnAll`

- Make a function return everything on its stack instead of just returning 1 item.
- That is, this annotation makes it so that the return signature of a function is "everything on the stack after the function"
- If there's already return type specified, that's a compile error.

## 19.8. `@errType`
- Used on an object definition to:
	- Insert a `message: String` member and
	- Create a default implementation of the `Err` trait

```
@errType object DivisonByZeroError => end

#? Equivalent to
object DivisonByZeroError =>
  $message: String
end
object DivisonByZeroError as Err =>
  define message => $message
end
```

- Used on a Variant to automatically make all subtypes be Err subtypes

```
@errType variant DBError =>
  object ConnectionClosedError => end
end
```

## 19.9. `@mustcall`

_Note: The exact semantics of this annotation are still being determined.
It is an open question as to whether this annotation is feasible_

- On object definition, this annotation makes it so that a set of object-friendly methods must be called on an instance of the object before that instance goes out of scope.
- This is helpful for ensuring that clean-up-time invariants are enforced before the object's destructor is called.
- Two forms: `@mustcall(all = [<methods>])` and `@mustcall(any = [<methods>])`
  - `all` means that all of the methods in the set must be called before the object goes out of scope. `any` means that at least one of the methods in the set must be called before the object goes out of scope.
  - The methods in the set must be object-friendly methods defined on the object. If a method in the set is not object-friendly, or is not defined on the object, that's a compile error.
  - Methods are specified as strings. 
- For example:

```
@mustcall(any = ["commit", "rollback"])
object DBTransaction =>
  define commit => ...
  define rollback => ...
end
```

- When a `DBTransaction` instance goes out of scope, if neither `commit` nor `rollback` has been called on that instance, a compile error is raised.
- Again, this requirement only applies to the scope where the object would no longer be kept alive. It is okay to return an object that has not been `@mustcall` handled.
- Additionally, calling `@mustcall` elements in a scope where the object is returned does not impact the consideration of whether the methods have been called in the destruction scope.

```
define foo(:DBTransaction) =>
  commit
end

define bar(:DBTransaction) =>
  DBTransaction() foo #? mustcall not considered satisfied here
  commit #? mustcall considered satisfied here
end
```

- The calls to `@mustcall` elements must be provably called. This means that if a `@mustcall` element is called in a context where it may not be called, then the compiler will not consider that element as satisfying the `@mustcall` requirement.
  - This means that if a `@mustcall` element is called in a branch of an if statement (without an else statement that also calls such an element), or in a loop, then the compiler will not consider that element as satisfying the `@mustcall` requirement, because there are execution paths where that element is not called.
 
## 19.10. `@commutative`
- Sometimes, it can be useful to make elements type commutative.
- For example, `get[T] (T+, Number)` could have an overload `get[T] (Number, T+)` that simply swaps the arguments before calling.
- Doing so cuts down on the amount of stack juggling required.
- But defining an overload for each and every overload combination is ceremonious.
- The `@commutative` annotation automatically generates all possible overload permutations
- For example:

```
@commutative define[T] get(xs: T+, ind: Number) => $xs[$ind]
```

- Will automatically create the overload

```
define[T] get(ind: Number, xs: T+) => swap get
```

- Note that normal overload resolution rules still apply.
# 20. Multimethods
- Standard overload resolution is static dispatch.
- The chosen overload is selected solely based on statically known types.
- But, what if arguments have been upcast to a trait?
  - `Animal+` says nothing of what animals are there
  - Mapping over an `Animal+` will apply the function defined for `Animal`.
- Executing on specialised types would otherwise require pattern matching
  - Not very extensible though, as the match statement would need to be updated every time a new subtype is added
- The `multi define` keyword allows a method to be executed at runtime based on the exact runtime type.
  - Even if static dispatch would choose the supertype overload, a `multi` annotated element would be chosen.
- A `multi` element must have a "fallback" element defined - one where all parameters are of either the same type or a supertype.
  - More specifically, if a `multi` element has parameters `(T1, T2, T3, ...)`, then there must be a non-`multi` element with parameters `(U1, U2, U3, ...)` where `T1 <= U1, T2 <= U2, T3 <= U3, ...`
- Additionally, a `multi` method must return the same type(s) as its fallback element. 
  - This restriction is what makes this system of multiple dispatch ergonomic. Otherwise the return type of the fallback element would need to be the union of all multimethods, which really isn't practical, and breaks extensibility.
  - Plus, chances are that if your runtime specialisation needs to return something different to if you just did pattern matching, then you're probably doing something wrong.
- Which element is considered the fallback is automatically handled by normal overload resolution.
  - Multimethods are only used at compile time if there is an exact type match. That is, if a multimethod can exactly match static types, the runtime dispatch is skipped. The same overload resolution rules apply.
  - This should not be surprising, because it's compile time resolution like there wasn't a `multi` to begin with.
- Canonical example of collisions between asteroids and spaceships
  - Note that the two objects have been made subtypes of a trait to actually show the multiple dispatch. Otherwise it'd just be normal overload resolution

```
trait Collidable => end
object Spaceship => end
object Spaceship as Collidable => end
object Asteroid => end
object Asteroid as Collidable => end

define collide(:Collidable * 2) => "Default collision"

multi define collide(:Asteroid, :Spaceship) => "a/s"

multi define collide(:Spaceship, :Asteroid) => "s/a"
 

multi define collide(:Spaceship, :Spaceship) => "s/s"

multi define collide(:Asteroid, :Asteroid) => "a/a"
```

- Better example is Hutton's razor

```
trait Expr => end
object Val => $n: Number
object Val as Expr => end
object Add =>
  $left: Expr
  $right: Expr
end

define eval(:Expr) =>
  match =>
    as :Val => $.n
    as :Add => sum eval [$.left, $.right]
  end
end

#? Now say later you want to add multiplication

object Mul =>
  $left: Expr
  $right: Expr
end
object Mul as Expr => end

multi define eval(:Mul) =>
  [$.left, $.right] eval | product
end
```

# 21. Error Handling

## 21.1. The `Result` Type

- `Result` types are the preferred way of doing error handling.
- `Result[T, E]` is defined as a sum type of `OK[T]` and any type implementing the `Err` trait.

```
trait Err =>
  extend message -> String
end
```

The following recoverable error objects are built in. Each constructor takes a
single `String` message, exposes it through `$.message` and `message`, and
implements `Err`:

- General validation and data errors: `Error`, `ValueError`, `RangeError`,
  `ParseError`, `DivisionByZeroError`, `IndexError`, `KeyError`, `ShapeError`,
  and `StateError`.
- System and concurrency errors: `IOError`, `NotFoundError`,
  `AlreadyExistsError`, `PermissionError`, `ClosedError`, `TimeoutError`, and
  `CancelledError`.

For example:

```
define safediv(x: Number, y: Number) =>
  if ($y 0 ==) => DivisionByZeroError("y cannot be 0")
  else => $x / $y
  end
end
```

The inferred return type is `Result[Number, DivisionByZeroError]`.

- However, for convenience, there is some union simplification.
- Akin to how optional types simplify from union types, `T | E` (where `E` implements `Err`) is turned into `OK[T] | E` which is turned into `Result[T, E]`
- If there are multiple `OK` types in a union, they will merge into one `OK`. If there are multiple types implementing `Err` in a union, they will form a union in the `Result` type.
	- `OK[T] | OK[U] | E | V` == `OK[T|U] | E | V` == `Result[T|U, E|V]`.
- Additionally:
	- `T | OK[U]` == `OK[T | U]`
- In terms of the union ordering, `Err` comes after `None`. So `T | None | E` == `T? | E` == `Result[T?, E]`.
	- Not-none types come first, then `None`, then `Err` types.
	- `E| None | T` -> `T | None | E`.
	- `Result` simplification only occurs if there is at least one non-`Err` type and one `Err` type. `E | V` (where `E` and `V` impl `Err`) will not simplify. `T | E | V` will simplify (as `Result[T, E | V]`)
- The benefit of this is a reduction in the amount of `OK`, and `Result` wrapping.
- For example:

```
#? Function[Number, Number -> Result[Number, Error]]
fn (x: Number, y: Number) =>
  if ($y 0 ==) => Error("y cannot be 0")
  else => OK($x / $y) #? The OK is optional here.
end
```

## 21.2. `Panic`s and the `Fault` trait.
- Sometimes, an error state really should terminate program execution. That is to say, some things should be more than just a `Result`.
- The `panic` element accepts only values implementing the `Fault` trait. Passing an ordinary value, such as a `String`, is a compile error.
- `panic` immediately returns from functions until either the top level is reached or the value is caught by a `try/handle`.
- Each function unwound by a panic performs the same cleanup that would normally happen when that function terminates.

```
trait Fault =>
  extend message -> String
end
```

The following fault objects are built in. Each constructor takes a single
`String` message, exposes it through `$.message`, `message`, and `getMessage`,
and implements `Fault`:

- General and data faults: `RuntimeFault`, `ValueFault`, `RangeFault`,
  `ParseFault`, `DivisionByZeroFault`, `IndexFault`, `KeyFault`, `ShapeFault`,
  and `StateFault`.
- System and concurrency faults: `IOFault`, `NotFoundFault`,
  `AlreadyExistsFault`, `PermissionFault`, `ClosedFault`, `TimeoutFault`, and
  `CancelledFault`.
- Language runtime faults: `UnwrappedNoneFault`, `UnwrappedResultFault`,
  `DuplicationFault`, and `CleanupFault`.

For example:

```
RuntimeFault("cannot continue") panic
```

Out-of-range sequence indexing raises `IndexFault`; indexing a dictionary with
an absent key raises `KeyFault`. Both are catchable with `try/handle`.

- Using `panic` in a function causes that function to have the `Panic[T]` element tag, where `T` is the concrete fault type.

## 21.3. `try/handle`
- A `try/handle` block allows a `Panic` to be caught
- Syntax:

```
try =>
  <code that panics>
handle <fault type> =>
  <handler>
handle =>
  <default handler>
end
```

- The code inside the try block will be executed first. Note that the code inside the try block must be able to panic. If it cannot, a compile error will be raised.
- There has to be at least one handler. However, not all panic types need to be handled. Additionally, it's valid to only specify the catch-all handler.
- A typed handler must name a type implementing `Fault`; the untyped `handle =>` form remains the catch-all handler.
- If a panic is raised with a type that matches a handler, then control flow goes to that handle block.

For example:

```
try =>
  [1, 2, 3] $[5]
handle IndexFault =>
  println "Caught IndexFault"
end
```
- After the handle block is finished, the function that contains the try block is immediately returned from.
- The result of the handler will be wrapped in a `PanicError` type (a built-in type implementing `Err`) 
- If at top level, the program will exit after the handler is finished

## 21.4. Optional and Result Helpers
## 21.4.1. `&`
- `&` has overloads defined as so:

```
define[T, U] &(x: T?, callable: Function[T -> U]) -> U? =>
  $x match =>
    as some: T => $callable($some)
    _          => None
end

define[T, U, E] &(x: Result[T, E], callable: Function[T -> U]) -> Result[U, E] =>
  $x match =>
    as ok: T => $callable($ok)
    as err: E => $err
end
```

- That is, for an input with an optional type:
	- If the input is present: call a function on the present value
	- Otherwise, return None
- For a result type input:
	- If the input is okay: call a function on the input
	- Otherwise, return the error
- `&` is most commonly called `flatmap` or `and_then` in other programming languages.

## 21.4.2. `?`
- `?` is an element defined for optionals as: If None, return None from the current function. Otherwise, unwrap.
- For result types: If Error, return Error from the current function. Otherwise, unwrap.

## 21.4.3. `?!`
- `?!` is an element defined for optionals as: If None, `panic(UnwrappedNoneFault("Tried to unwrap optional"))`, otherwise `?`
- For result types: If Error: `panic(UnwrappedResultFault("Tried to unwrap Result, found Error"))`

# 22. The `where` Clause

- The standard type system cannot always precisely express relationships between inputs and outputs.
- For example, the output rank of `reshape` depends on the length of the `shape` argument - but since `shape` can be any length, the exact output rank is unknowable without additional machinery. A minimum rank return type (`T*`) is valid but loses meaningful type information.
- The `where` clause solves this by allowing types to be constructed from
  compile-time-known properties of inputs and by letting those computed values
  flow into later type and call-site decisions.
- Syntax:

```
fn (...) -> ... where (<static expressions>) => ...
end

define name(...) -> ... where (<static expressions>) => ...
```

- The `where` clause is a small static stack-based program that runs at compile
  time. Its results are used to fill in type variables in the return type, to
  constrain overload selection, and to provide hidden numeric values to later
  code generation when needed.
- Static expressions are evaluated in order, left to right.
- Variables declared in the where clause can be used in the function body.
- Executed entirely at compile time.

## 22.1. Rank Variables

- A list parameter's rank can be named using `$n` after the `+`:
  - `T+$n` in a parameter makes `$n` a read-only rank variable, bound to the rank of the list at the call site.
  - `T+$n` in a return type makes `$n` a mandatory-write rank variable - it must be assigned in the `where` clause.
  - `T+$n` is still an exact rank type.
  - `T*$n` allows for minimum rank list types to be used.
  - `T~$n` names a rugged list rank.
  - `T^$n` and `T>$n` name exact and minimum array ranks.

## 22.2. Allowed Operations

- **Literals** - numbers and types can be pushed directly onto the static stack.
- **Rank variables** - `$n` from `T+$n`/`T*$n`/`T~$n` parameters, and any variables assigned in the `where` clause.
- **Arithmetic** - `+`, `-`, `*`, `max`, `min` on numbers.
- **Comparison** - `<`, `>`, `<=`, `>=`, `==`, `!=` on numbers; `==`, `!=` on types (no vectorisation).
- **Boolean operations** - `and`, `or`, `not` on numbers (following the same truthiness rules as the rest of Valiance - `0` is false, all other numbers are true).
- **Assignment** - `$name = value` to name a computed value for use in return types or later expressions.
- **Stack manipulation** - `swap`, `pop`, `dup`.
- **Function introspection** - given a function parameter `$f`:
  - `$f.inputs` - tuple of input types
  - `$f.outputs` - tuple of output types
  - `$f.arity` - number of inputs
  - `$f.multiplicity` - number of outputs
- **Type tuples and tuple values** - `length` returns the number of entries in a fixed tuple type or tuple value.
- **Overload assertion** - `?` asserts that a condition holds. If it does not, the current overload is rejected at the call site and overload resolution continues. This is not a runtime assertion. Basically, it's part of overload resolution.

## 22.3. Restrictions

- The implemented static evaluator is intentionally small. The supported operations are the ones listed above.
- Arbitrary element calls are not allowed - only the operations listed above.
  Namespaced, modified, annotated, disambiguated, or placeholder/named-argument
  static calls are rejected. This ensures the `where` clause always terminates.
- Recursive or looping constructs are not allowed for the same reason.
- `Result` types are not available in static type literals - only optionals.
- Optional-safe field access is not available in `where` clauses.

## 22.4. Examples

```
define[T] reshape(xs: T*, shape: Number+) -> T* =>
  #? Implementation here
end

define[T] reshape(xs: T*, shape: {Number...}) -> T+$n
where ($n = length $shape) => $xs as! T+$n

[[1, 2, 3], [4, 5, 6]] reshape {4, 5, 6}
```

```
define fork(
  f: Function,
  g: Function
) where ($n = max($f.arity, $g.arity)) =>
  $fRes = peek: @@tupled $f()
  $gRes = @@tupled $g()
  merge($fRes, $gRes)
  detuple #? Dumps everything from a tuple onto the stack
end
```


# 23. Imports and Modules

## 23.1. Modules

* One file defines one module.
* Directories form module namespaces.
* `x.vlnc` defines module `x`.
* `x/y.vlnc` defines module `x.y`.
* Imports load symbols only. They do not execute code.
* Circular imports are therefore permitted.
* Wildcard imports are not supported.

The following structures can be imported:

* `define`s
* `object`s
* `trait`s
* `variant`s
* `enum`s
* tags

A structure must be marked `public` to be importable. Tags are always importable and do not require `public`.

```vlnc
public define foo => 1
define bar => 2

#? foo can be imported
#? bar cannot be imported
```

## 23.2. Import Syntax

Imports are enclosed in `{}`.

```vlnc
import {
  module,
  module as alias,
  module.Component,
  module.[
    Component,
    Component as Alias,
    object X as Y,
    hash,
    hash(String),
    hash(_+),
    hash except [(String), (_+)]
  ]
}
```

The braces are always required, including for a single import:

```vlnc
import {utils}
```

This allows an import to appear on the same line as other code wherever the grammar permits:

```vlnc
import {utils}; run()
```

An import entry may import:

* an entire module namespace;
* a module namespace under an alias;
* one component from a module;
* several selected components from a module;
* one or more overloads of an element;
* a trait implementation;
* all overloads of an element except selected overloads.

### Block-scoped imports

Imports may appear in any structure body, including function and `define`
bodies, conditional branches, loop bodies, `match` cases, and `try` handlers.
The imported names are visible from the import statement to the end of the
innermost enclosing body. They are not visible in sibling branches or after the
body ends.

```vlnc
define format(value) => (
  import {text.format}
  format(value)       # available here
)

if condition then (
  import {debug.log}
  log("inside")      # available in this branch
) else (
  # log is not available here
)

# neither format nor log is available here
```

Import resolution is an analysis-time operation. Runtime declarations needed by
a scoped import are initialized once in the program's import prelude, not each
time a function is called, a branch is taken, or a loop iterates. Separate
blocks may therefore use the same local import alias without sharing or
replacing one another's binding.

## 23.3. Module Imports

Importing a module without selecting a component imports the module as a namespace:

```vlnc
import {utils}
utils.parse(input)
```

A module namespace may be aliased:

```vlnc
import {utilities.long_name as utils}
utils.parse(input)
```

The imported namespace name is otherwise the final component of the module path:

```vlnc
import {dep.somelib.parsers}
parsers.parse(input)
```

If two imported modules would produce the same namespace name, at least one must be aliased.

```vlnc
import {
  dep.first.parsers as firstParsers,
  dep.second.parsers as secondParsers
}
```

## 23.4. Component Imports

A single component may be imported directly with `.`:

```vlnc
import {utils.Parser}
```

Several components from the same module are selected with `[]`:

```vlnc
import {
  utils.[
    Parser,
    Token,
    parse
  ]
}
```

A selected component may be aliased:

```vlnc
import {
  utils.[
    Parser as InputParser,
    parse as parseInput
  ]
}
```

Brackets are optional only when exactly one component is selected:

```vlnc
import {utils.Parser}
```

The following is equivalent:

```vlnc
import {utils.[Parser]}
```

## 23.5. Module Resolution

The first component of an import path determines how the module is resolved.

There are four module resolution forms:

* an unqualified path is resolved relative to the current file;
* `root` resolves from the project root;
* `std` resolves from the standard library;
* `dep` resolves through the current project's dependency table.

`root`, `std`, and `dep` are reserved as the first component of an import path.

```vlnc
import {
  utils,
  root.utils,
  std.lists,
  dep.somelib.module
}
```

### 23.5.1. Relative Modules

An unqualified module path is resolved relative to the directory containing the current file.

```vlnc
import {
  utils,
  parsers.json
}
```

Given the importing file:

```text
src/main.vlnc
```

these resolve to:

```text
src/utils.vlnc
src/parsers/json.vlnc
```

Relative imports may move through child namespaces but cannot traverse above the importing file's directory. Parent-directory syntax such as `..` is not supported.

Use a `root` import when a module must be resolved from elsewhere in the project.

### 23.5.2. Project-Root Modules

A path beginning with `root` is resolved relative to the directory containing `valiance.toml`.

```vlnc
import {
  root.utils,
  root.shared.logging
}
```

These resolve to:

```text
<project root>/utils.vlnc
<project root>/shared/logging.vlnc
```

The `root` component selects the resolution root and is not part of the module's namespace.

For example:

```vlnc
import {root.shared.logging}
logging.info("started")
```

A file without an enclosing `valiance.toml` is treated as a standalone script. `root` imports are unavailable in standalone scripts.

### 23.5.3. Standard Library Modules

A path beginning with `std` is resolved from the compiler's standard library.

```vlnc
import {
  std.lists,
  std.strings,
  std.io.File
}
```

The standard library ships with the compiler and is always available. Current Python-backed modules include `std.grids`, `std.random`, and `std.string` in addition to the existing modules. Their notable exports include:

- `std.grids.allNeighbors(board, wrapping)`
- `std.random.randbit` and `std.random.between(minimum, maximum)`
- `std.string.\Alphabet` and `std.string.transliterate(value, source, target)`

For compatibility with small scripts, a single unqualified native standard-library module name such as `import {random}` or `import {string}` resolves to the same packaged module. The canonical path remains `std.random` or `std.string`.

The `std` component is part of the standard library's canonical module path, but a whole-module import still introduces only the final component as the local namespace:

```vlnc
import {std.lists}
lists.map(values, transform)
```

`std.grids.allNeighbors` accepts a rectangular rank-2 list. Each output cell is a list in top-left, top, top-right, left, cell, right, bottom-left, bottom, bottom-right order. With `wrapping = false`, out-of-bounds entries are omitted; with wrapping enabled every output neighborhood has nine entries.

### 23.5.4. Dependency Modules

A path beginning with `dep` is resolved through the current project's dependency table.

The component immediately after `dep` is the dependency name from `valiance.toml`. The remaining components identify a module inside that dependency.

```vlnc
import {
  dep.somelib,
  dep.somelib.parsers,
  dep.repo.module
}
```

Given:

```toml
[dependencies]
somelib = "1.2.3"
repo = { source = "github.com/user/repo", version = "1.0.0" }
```

the imports resolve through the dependencies named `somelib` and `repo`.

The import path does not contain:

* the dependency's version;
* its registry location;
* its VCS location;
* its installation directory.

Those details belong to the manifest and lockfile.

External dependencies are unavailable in standalone scripts because there is no dependency table without `valiance.toml`.

## 23.6. Importing Overloads

Importing a bare element name imports all of its overloads:

```vlnc
import {
  dep.somelib.[hash]
}
```

A specific overload may be selected by writing its signature:

```vlnc
import {
  dep.somelib.[
    hash(String),
    hash(Number)
  ]
}
```

The signature identifies parameter types only. It does not repeat parameter names or return types.

Generic overloads use the same wildcard type syntax as the rest of the language:

```vlnc
import {
  dep.somelib.[
    hash(_),
    hash(_+),
    hash(_++)
  ]
}
```

In an overload signature:

* `_` means any single type;
* `_+` means a rank-1 list of any type;
* `_++` means a rank-2 list of any type;
* further `+` suffixes indicate higher list ranks.

Selecting an overload that does not exist is a compile error.

## 23.7. Overload Exclusion

`except` imports every overload of an element except the listed signatures.

```vlnc
import {
  dep.somelib.[
    hash except [(String)]
  ]
}
```

Several overloads may be excluded:

```vlnc
import {
  dep.somelib.[
    hash except [
      (String),
      (_+)
    ]
  ]
}
```

Exclusions may refer to concrete or generic overloads.

`except` is valid only after a bare element name:

```vlnc
hash except [(String)]       #? valid
hash(String) except [(_+)]   #? compile error
```

The second form is invalid because `hash(String)` already selects one specific overload.

Every excluded overload must exist in the imported module. Excluding a nonexistent overload is a compile error:

```vlnc
#? compile error if hash(Number) does not exist
hash except [(Number)]
```

## 23.8. Importing Trait Implementations

A trait implementation may be imported explicitly:

```vlnc
import {
  dep.somelib.[
    object X as Y
  ]
}
```

This imports the implementation of trait `Y` for object `X`.

The object and trait names must identify an implementation defined by the selected module. Importing an implementation that does not exist is a compile error.

## 23.9. Importing Objects

Importing an object directly also imports its object-friendly elements.

```vlnc
import {somemod.Y}
Y foo
```

Object-friendly elements are not imported when the object is accessed through a module namespace:

```vlnc
import {somemod}
somemod.Y somemod.foo
```

In the second example, `Y` and `foo` remain members of the `somemod` namespace.

## 23.10. Importing Tags

Importing a tag imports:

* the tag;
* all overlay rules for the tag;
* all elements associated with the tag through tag definitions.

It does not import unrelated elements that merely use the tag in their signatures.

```vlnc
#? sorted.vlnc

tag #sorted as computed

define[T] #sorted min(:#sorted T+) => $[0]

define[T] max(:#sorted T+) => $.[-1]
```

```vlnc
import {sorted.#sorted}
```

This imports:

* `#sorted`;
* its overlay rules;
* `min`, because it is associated through the tag definition.

It does not import `max`, because `max` only uses the tag and is not associated through its definition.

## 23.11. Re-Exporting

Imported symbols are private to the importing module by default.

Prefix an import with `public` to make its imported symbols available to importers of the current module:

```vlnc
public import {
  internal.api.[
    Client,
    Request,
    send
  ]
}
```

A public module namespace import re-exports that namespace:

```vlnc
public import {dep.somelib}
```

A public selective import re-exports only the selected components:

```vlnc
public import {
  dep.somelib.[Client]
}
```

Re-exporting allows a library to provide a curated public API without exposing its internal file structure.

## 23.12. Import Conflicts

If two imports introduce the same non-overload symbol under the same name, the compiler raises an error unless one is aliased.

```vlnc
import {
  dep.first.[Parser],
  dep.second.[Parser]
}
```

This is an error if both imports introduce `Parser`.

Resolve it with aliases:

```vlnc
import {
  dep.first.[Parser as FirstParser],
  dep.second.[Parser as SecondParser]
}
```

## 23.13. Overload Conflicts

Overloads from different modules may coexist when their signatures are distinct.

```vlnc
import {
  dep.pkgA.[hash(String)],
  dep.pkgB.[hash(Number)]
}
```

If two imported modules define the same overload for the same parameter types, the compiler raises an error.

```vlnc
import {
  dep.pkgA.[hash],
  dep.pkgB.[hash]
}
```

If both modules define `hash(String)`, that overload is ambiguous.

Resolve the conflict by importing only the desired overloads:

```vlnc
import {
  dep.pkgA.[hash(String)],
  dep.pkgB.[hash(Number)]
}
```

Generic overloads are resolved in the same way:

```vlnc
import {
  dep.pkgA.[hash(_+)]
}
```

Overload exclusion may also be used:

```vlnc
import {
  dep.pkgA.[hash except [(String)]],
  dep.pkgB.[hash(String)]
}
```

Exclusions do not suppress unrelated conflicts. If both imports still provide the same remaining overload, the import remains invalid:

```vlnc
import {
  dep.pkgA.[hash except [(String)]],
  dep.pkgB.[hash except [(String)]]
}

#? compile error if both modules still provide hash(_+)
```

## 23.14. Trait Implementation Conflicts

If two imported modules provide the same trait implementation for the same object and trait, the compiler raises an error.

```vlnc
import {
  dep.pkgA,
  dep.pkgB
}
```

If both packages provide `object X as Y`, the implementation is ambiguous.

Resolve the conflict by importing the desired implementation explicitly:

```vlnc
import {
  dep.pkgA.[object X as Y]
}
```

Alternatively, import both packages as namespaces and access their members explicitly where namespace access is supported:

```vlnc
import {
  dep.pkgA,
  dep.pkgB
}
```

Namespace imports do not automatically merge object-friendly elements or trait implementations into the current module.

## 23.15. Namespace Disambiguation

Importing whole modules as namespaces avoids direct symbol conflicts:

```vlnc
import {
  dep.pkgA,
  dep.pkgB
}

pkgA.hash("string")
pkgB.hash("string")
```

A namespace may be aliased when the default final component is unclear or conflicts with another import:

```vlnc
import {
  dep.companyA.crypto.hash as hashA,
  dep.companyB.crypto.hash as hashB
}
```

The aliases are then used as namespace names:

```vlnc
hashA.digest(value)
hashB.digest(value)
```

# 24. Package Management

Valiance projects are described by a `valiance.toml` manifest. The manifest
defines project metadata, executable entry points, and direct dependencies.

The package manager currently provides project creation, manifest editing,
lockfile generation, and local package-directory setup. Registry downloads,
VCS cloning, transitive dependency resolution, and integrity verification are
not yet implemented.

## 24.1. Projects

A Valiance project is a directory containing `valiance.toml`.

The directory containing that manifest is the project root. Commands that need
project context search the current directory and its parents for the nearest
`valiance.toml`.

The project root determines:

- the location of the project manifest;
- the location of `valiance.lock`;
- the location of the managed `.vln` directory;
- the base directory used to resolve project entry paths;
- the dependency declarations available to the project.

Commands such as `vln run`, `vln compile`, `vln install`, `vln add`,
`vln remove`, and `vln upgrade` require an enclosing project unless they are
given an explicit source input where supported.

## 24.2. Creating a Project

Create a project in the current directory with:

```text
vln init
```

Create a project in another directory with:

```text
vln init myproject
```

The command creates:

```text
valiance.toml
valiance.lock
.gitignore
src/main.vlnc
```

The generated source file contains a small runnable program.

The generated `.gitignore` includes:

```gitignore
.vln/
```

`vln init` fails if the target directory already contains `valiance.toml`.

## 24.3. The Project Manifest

A new manifest has this shape:

```toml
[project]
name = "myproject"
version = "0.1.0"

[entries]
main = "src/main.vlnc"

[dependencies]
```

The manifest contains three main tables:

- `[project]` stores project metadata;
- `[entries]` maps executable entry names to source files;
- `[dependencies]` declares direct dependencies.

Unknown project metadata may be preserved when the package manager rewrites the
manifest, provided its values can be written as TOML strings, booleans, numbers,
or lists of supported values.

## 24.4. Project Entries

The `[entries]` table exposes named source entry points.

```toml
[entries]
main = "src/main.vlnc"
server = "src/server.vlnc"
tools = "src/tools.vlnc"
```

Entry names are used by `vln run` and `vln compile`.

Run the main entry:

```text
vln run
```

Run a named entry:

```text
vln run server
```

Compile the main entry:

```text
vln compile
```

Compile a named entry:

```text
vln compile server
```

The entry path is resolved relative to the project root.

Entry paths must:

- be strings;
- be relative paths;
- remain inside the project root;
- refer to existing files.

The `main` entry is the default selected by bare `vln run` and
`vln compile`.

## 24.5. Running and Compiling Explicit Files

Project entry names occupy the positional argument of `run` and `compile`.
Use `--file` to operate on an arbitrary source file directly.

Run a source file:

```text
vln run --file samples/example.vlnc
```

Compile a source file:

```text
vln compile --file samples/example.vlnc
```

Inline source remains available through `--code`:

```text
vln run --code "1 2 +"
vln compile --code "1 2 +" --output out.vbc
```

`--file`, `--code`, and a named project entry are mutually exclusive forms of
source selection.

## 24.6. Dependencies

The `[dependencies]` table maps local dependency names to exact versions and
package identities.

A registry-style dependency may use the compact form:

```toml
[dependencies]
somelib = "1.2.3"
```

This declares:

- local dependency name: `somelib`;
- package identity: `somelib`;
- source kind: registry;
- exact version: `1.2.3`.

A different external package name may be declared with the expanded form:

```toml
[dependencies]
math = { package = "advanced-math", version = "2.0.0" }
```

This keeps `math` as the local dependency name while recording
`advanced-math` as the external package identity.

A source-based dependency may be declared as:

```toml
[dependencies]
repo = { source = "github.com/user/repo", version = "1.0.0" }
```

The package manager classifies a dependency as source-based when it has a
`source` field.

At present, source strings are recorded as metadata. The package manager does
not yet clone repositories or download registry packages.

## 24.7. Dependency Names

A dependency name must be a valid Valiance module component.

It must:

- begin with a letter or underscore;
- contain only letters, digits, and underscores;
- not use a reserved name.

The reserved dependency names are:

- `root`;
- `std`;
- `dep`.

A dependency name identifies the dependency inside the current project and must
be unique within the manifest.

## 24.8. Exact Versions

Every dependency uses an exact numeric version.

Valid examples include:

```toml
[dependencies]
a = "1"
b = "1.2"
c = "1.2.3"
```

Version ranges and compatibility operators are rejected.

Invalid examples include:

```toml
[dependencies]
a = "^1.2.3"
b = ">=2.0"
c = "1.*"
d = "*"
```

The current version syntax accepts one or more numeric components separated by
periods.

## 24.9. Adding Dependencies

Add a registry-style dependency with:

```text
vln add somelib 1.2.3
```

This writes:

```toml
[dependencies]
somelib = "1.2.3"
```

Add a source-based dependency with:

```text
vln add github.com/user/repo 1.0.0
```

By default, the final path component becomes the local dependency name:

```toml
[dependencies]
repo = { source = "github.com/user/repo", version = "1.0.0" }
```

Choose a different local name with `as`:

```text
vln add github.com/user/repo 1.0.0 as userrepo
```

This writes:

```toml
[dependencies]
userrepo = { source = "github.com/user/repo", version = "1.0.0" }
```

Adding a dependency:

1. validates the local name and exact version;
2. updates `valiance.toml`;
3. regenerates `valiance.lock`;
4. ensures the corresponding `.vln` package directory exists.

Adding a dependency with an existing local name replaces that declaration.

## 24.10. Removing Dependencies

Remove a dependency by its local name:

```text
vln remove somelib
```

The command:

1. removes the dependency from `valiance.toml`;
2. regenerates `valiance.lock`;
3. removes the dependency's managed directory when possible.

Removing an undeclared dependency is an error.

The command does not currently scan project source code for imports before
removing a dependency. Any unresolved imports are reported later by the normal
analysis process.

## 24.11. Upgrading Dependencies

Change a dependency's exact version with:

```text
vln upgrade somelib 1.3.0
```

For a source-based dependency, use its local name:

```text
vln upgrade repo 1.1.0
```

The command preserves the dependency's package or source identity while
replacing its version.

Upgrading a dependency:

1. validates the new exact version;
2. updates `valiance.toml`;
3. regenerates `valiance.lock`;
4. refreshes the managed package metadata.

Upgrading an undeclared dependency is an error.

Dependencies are never upgraded automatically.

## 24.12. Installation

Install the dependencies declared by the current project with:

```text
vln install
```

The command:

1. loads the nearest `valiance.toml`;
2. regenerates `valiance.lock`;
3. creates the project's `.vln` directory;
4. creates one directory for each direct dependency;
5. writes package metadata for each dependency.

The managed directory has this form:

```text
<project root>/.vln/
├── somelib/
│   └── package.json
└── repo/
    └── package.json
```

Each `package.json` records the dependency's local name, identity, source, and
exact version.

The current installer does not yet fetch package contents. The generated
directories are placeholders for the future package acquisition and resolution
system.

## 24.13. The Lockfile

`valiance.lock` is generated from the current manifest.

The lockfile records:

- a lockfile format version;
- the root project's name and version;
- each direct dependency's local name;
- dependency kind;
- package identity;
- source;
- exact version;
- an empty transitive dependency list;
- a currently unset integrity value.

A simplified lockfile looks like:

```json
{
  "version": 1,
  "package": {
    "name": "myproject",
    "version": "0.1.0"
  },
  "dependencies": [
    {
      "name": "somelib",
      "kind": "registry",
      "identity": "somelib",
      "source": "registry",
      "version": "1.2.3",
      "dependencies": [],
      "integrity": null
    }
  ]
}
```

Do not edit `valiance.lock` by hand.

The current lockfile describes direct dependencies only. Transitive dependency
resolution and integrity verification are future package-manager work.

## 24.14. The Managed Package Directory

Project package metadata is stored under:

```text
<project root>/.vln/
```

This directory is managed by Valiance and should not be edited manually.

It should normally remain excluded from version control:

```gitignore
.vln/
```

`valiance.toml` and `valiance.lock` should be committed when the project is kept
in version control.

## 24.15. Current Resolution Model

The package manager currently records only direct dependencies declared by the
root project.

It does not yet:

- contact a package registry;
- clone VCS repositories;
- inspect dependency manifests;
- resolve transitive dependency graphs;
- install multiple versions of transitive packages;
- calculate integrity hashes;
- enforce package identity at the type level;
- provide global package installation.

The manifest and lockfile formats already preserve fields needed by parts of a
future implementation, but those fields must not be treated as evidence that
the corresponding behavior exists today.

## 24.16. Command Summary

Create a project:

```text
vln init
vln init myproject
```

Run project entries:

```text
vln run
vln run server
```

Compile project entries:

```text
vln compile
vln compile server
```

Operate on explicit files:

```text
vln run --file samples/example.vlnc
vln compile --file samples/example.vlnc
```

Install declared dependencies:

```text
vln install
```

Add dependencies:

```text
vln add somelib 1.2.3
vln add github.com/user/repo 1.0.0
vln add github.com/user/repo 1.0.0 as userrepo
```

Remove a dependency:

```text
vln remove somelib
```

Upgrade a dependency:

```text
vln upgrade somelib 1.3.0
```

All dependency-modifying commands rewrite the manifest, regenerate the
lockfile, and refresh the local managed package metadata.


# 25. Concurrency
_Features from this point onwards are for implementation further down the road. They are not considered core priority. As such, these features are very open to change._

_The concurrency story here is strongly inspired by Go._

- Where other languages use `async`/`await`, `fiber`s, or direct threading, Valiance uses a green threads system with channels for cross-thread communication
- `spawn => <code> end` creates a new `Task[T]` that will execute `code` alongside the main program.
        - The `[T]` in `Task[T]` is the return type of `code`
- `wait`, when given a `Task[T]`, will block until the `Task` completes, and then return the result `T`
        - Think of it like `unwrap` for `Task`s.
- A `Task` cannot be `wait`ed more than once
        - Tracked by each `Task` storing a reference to an internal thread handle
        - So like `spawn => ...` creates thread with internal id `x`, and the returned `Task` object stores `x`. You can copy `x` as much as you like, but there's only ever 1 true value of `x`.

- But `wait`s can get ceremonious, especially if you have a lot of them
- That's why there's two ways to automatically `wait`:
1. un`wait`ed tasks are automatically `wait`ed at the end of a function if they aren't returned
2. All un`wait`ed tasks are `wait`ed at the end of a `concurrent` block

- A `concurrent` block is just a labelled wrapper around a bunch of code
- `concurrent => <code> end`
- Serves to provide a scoped completion point without the ceremony of creating a new function.

- `wait` is defined as `[T] (Task[T]) -> T`
        - Meaning vectorisation kicks-in when given a `Task[T]+` or any list of `Task`s

- Putting this altogether:

```
spawn => println("Hello from a thread!")
println("Hello from main thread!")
```

- The exact order is of course runtime sensitive, but it'll most likely be:

```
Hello from main thread!
Hello from a thread!
```

- Note that auto-`wait` also applies to the main program.
        - No need to sleep a little to give the `Task` time to complete

## 25.1. Channels

- What if `Task`s need to communicate with each other, as well as the outside world?
- The built-in `Channel` object serves as a communication medium
- `Channel` is defined roughly as

```
object[T] Channel =>
  $bufferSize: Number? = None
  #? No buffer size = no bounding
  define write(value: T) -> => ...
  define read() -> T? => ...
  define close() -> => ...
  define hasNext() -> #boolean Number => ...
end
```

- Like `Task`s, `Channel` holds a reference to an actual channel identifier.
        - Allows `Channel`s to be `copy`'d and `move`'d

- `write` will write a value to the channel. Blocks if no `Task`s are using `read` or if there's a buffer size and the channel is full. Panics if `Channel` is closed.
- `read` will "pop" and return the last written value. Blocks if `Channel` is empty. Returns `None` if `Channel` is closed or is empty. Note that `read` on a closed channel will read any remaining buffered values.
- `close` closes the channel, allowing no more `write`s.
- `hasNext` returns whether a `read` would return `None`. This allows for iterating on a `Channel` in a while loop without consuming the value.

- An example

```
$ch = Channel[String]
concurrent =>
  #? Producer
  spawn =>
    ["a", "b", "c"] eagermap: spawn => send($ch, _)
    #? Close the channel once everything is sent
    close($ch)
  end
  #? Consumer
  spawn =>
    #? Consume until $ch is closed/empty
    while ($ch hasNext) => println(read($ch))
  end
  #? Concurrent block will wait until both Tasks have finished
end
```

## 25.2. `match channels`

- You thought that was it?
- Say you want to wait on multiple channels, and capture the first channel to produce a value.
- `match channels => <channels> end` does just that.
- `channels` contains `from` branches
        - `from ${channelVar} -> ${code}`
        - `channelVar` is the channel to watch
        - `code` gets the returned value from the channel
- Blocks until a channel produces a value
- Example:

```
import{time}

define fetchTimeout(url: String, ms: Number) -> Result[String, String] =>
  $data = Channel[String]
  $timeout = Channel[{}] #? Empty tuple channel

  spawn => $data send(fetch($url))
  spawn => time.sleep($ms) $timeout send(())

  match channels =>
    from $data    => id, #? Just return fetch result
    from $timeout => Error("Request timed out.") 
  end
```

# 26. Eager Evaluation 

- Sometimes, it's necessary to force evaluation of a list or other lazily evaluated object.
- For example, consider:

```
[1, 2, 3] map: println
```

- In theory, this example would not print each item.
- Instead, it would sit unevaluated until a forced evaluation context (like printing or a `foreach` loop) is encountered.
- That's not ideal, because all of a sudden, your side effects from earlier are being exposed during a separate calculation:

```
[1, 2, 3] map: println
#? Scenario 1: Printing that list
println
#/ Output = "[1
1, 2
2, 3
3]"
/#
#? Scenario 2: Using a foreach
foreach => ...
#? This has the surprise of all of a sudden printing during execution
```

- Except...this isn't what happens. (pretend with me for a second that everything is implemented). Running `map: println` immediately prints each number. Further operations do not trigger the printing behaviour.
- So then what's happening? Why isn't this mix of side effects and lazy evaluation ending in a mess?
- Under the hood, println was defined as:

```
eager define[T] println(:T) ->  => ...
```

- The `eager` keyword makes it so that anything calling `println` forces eager evaluation of all of its arguments.
- It also attaches the `Eager` element tag to the function type.
- Eagerness propagates up. Anything calling an eager element becomes eager itself.
  - Otherwise, you just have the same problem as before.
- Thus, `map: println` itself is eager. The map, by process of calling an eager function, becomes eager.
- And the type of `map: println` is `Function[T -> ()]<Eager>`.


# 27. Foreign Function Interfaces

_Note: semantics still experimental, subject to being implemented much much later. Designed and considered now to ensure that the implementation is future-proof_

_Note: FFI is very unsafe. Valiance can help make sure you're doing it right, but once you call C code, you're on your own._

- Sometimes, you'll want to dip into C code to get functionality of existing libraries.
- Like for example you may want bindings to a C-implemented graphics library.
- This sounds good, but there's a slight problem: Valiance is decidedly not C.
- The solution: Valiance allows you to define Valiance-safe interfaces to underlying C code.
- The first important structure is the `external` structure.
- This structure allows for Valiance mappings to be made to underlying C code.
- The structure is:

```
external[<namespace>] (<filename>) =>
  <declarations>
end
```

- `filename` is the name of the file to bind
- `namespace` is optional, and makes it so that any bindings are available under a namespace.
- `declarations` is a series of `define`s and `object`s.
- A `define` inside a `external` block creates a Valiance type-checkable element that directly calls the corresponding function.
- The name used in define must exactly match the C function name.
- The parameter types must also match. Notably, the parameter names need not match. Only the types.
- This element cannot be used outside of `external` blocks.
- `object`s inside a foreign block requires its own section.
- An `external` block always returns the top of the stack after the block
  - Note that FFI types cannot be returned from an external block. Only Valiance types can be returned.
- Everything else is just normal Valiance.
- Note that the filename is also optional. If no file name is provided, then the external block is used solely to provide access to elements using FFI types.

## 27.1. Binding C Functions to Valiance Elements 

- Say your C library contains the following function:

```c
// Say this is in shared library math.dll
int add(int x, int y) {
    return x + y;
}
```

- The goal is to end up with a Valiance-side binding which can be used in a wrapper for that function that looks like

```
define add(x: Number, y: Number) =>
  #? Call the C function here
end
```

- There's an immediate first problem: Valiance only has one number type: `Number`. There's no meaningful distinction between integer sizes and signedness.
- The solution is to have an `FFI` library containing a whole bunch of C types.
  - This FFI type library contains types that cannot be created, nor interacted with, in normal Valiance code. They only exist inside `external` blocks.
- Valiance types can be converted to FFI types inside foreign blocks, where compatible.
  - A `Number` can be cast to `FFI.int`, and there may be some pre-C-call verification. Casting rules are implemented using the `cast` keyword introduced in section 28.
  - A `String` cannot be cast to `FFI.i32`.
- FFI types can also be cast back to Valiance types where compatible.
  - A `FFI.int` can be cast to a Valiance `Number`
- The language core will provide a whole bunch of these conversions for convenience.
- With this in mind, the binding would become:

```
external ("math.dll") =>
  define add(:FFI.int, :FFI.int) -> FFI.int => end
}
```

- That's good, but it still doesn't give anything Valiance callable.
- It still needs to be wrapped:

```
define add(x: Number, y: Number) =>
  external =>
    $x $y both: as FFI.int
    add as Number
  end
end
```

- This first type casts the Valiance numbers to C ints (ie ensures the actual number is in the right int range and then changes the associated type), calls the C function, and then converts the result to Number.

## 27.2. FFI and Objects 

- Creating bindings and wrappers for C functions is pretty simple. You just make sure that the function call checks out, and away you go.
- Working with C types and structs, on the other hand, is not as plain cut.
  - C is a funky little child with funky little ways to declare types and structures.
- Valiance provides two types of bindings to C objects

1. Opaque type bindings
2. Struct bindings

- Opaque type bindings can be used when you're working with forward declarations. Like `typedef struct` in a header file.
- These are represented using the `external object` keyword. An `external object` has no members, no constructor, and no object-friendly-elements.
- For example, say a header file has the declarations

```c
// counter.h
typedef struct Counter Counter;

Counter* counter_create(int initial);
void counter_inc(Counter* c);
int counter_get(Counter* c);
void counter_destroy(Counter* c);
```

- On the Valiance side, this would look like

```vlnc
external[counter] ("counter.h") =>
  #? Represent the typedef
  external object Counter => end

  #? Represent the functions
  define counter_create(:FFI.int) -> Counter => end
  define counter_inc(:Counter) -> FFI.void => end
  define counter_get(:Counter) -> FFI.int => end
  define counter_destroy(:Counter) -> FFI.void => end
end
```

- This opaque binding can then be used as a "handle" - something that can be re-used between `external` blocks.
- Handles are allowed to be returned from `external` blocks.
        - Handles cannot be interacted with in Valiance-side code.
- For example, the `counter.Counter` object could be wrapped as:

```vlnc
object Counter =>
  private $handle: counter.Counter
  define Counter(value: Number) =>
    external => counter.counter_create($value as FFI.int)
        $self.handle = top
  end

  @self define increment() =>
    #? Modifies `handle` in place
    external => $self.handle counter.counter_inc
  end

  define get() -> Number => external => $handle counter.counter_get as Number

  define ~Counter => external => $handle counter.counter_destroy
end
```

- This object can be used 100% as if it were a Valiance object.

### 27.2.1. FFI and C `struct`s

- The above falls apart when you want to create a binding for something like:

```c
// in Point.c
typedef struct {
  public int x;
  public int y;
} Point
```

- Instances of `Point` will be by value, rather than something that can be neatly represented as a handle.
- Therefore, bindings and wrappers need to consider the fields.
- However, this is very simple. Just a normal `object` definition works.
        - Unlike Valiance-side objects, the fields of an object inside an `external` must not be filled.
- The `Point` struct would be bound as:

```vlnc
external[point] ("Point.c") =>
  object Point =>
    public $x: FFI.int
    public $y: FFI.int
  end
end
```

- Public fields can be directly read inside `external` blocks.
- However, they cannot be written to directly. `$p.x = 10` is not allowed inside an `external` block.
  - This makes it safer, as direct field writes may violate invariants.
- These kinds of objects can be instantiated directly inside `external` blocks.

```vlnc

```external =>
  point.Point(10 as FFI.int, 20 as FFI.int)
  #? Something else needs to be returned though
  #? because external blocks must return Valiance types
end
```

- The wrapper need not make any reference to `external` at all:

```vlnc
object Point =>
  $x: Number
  $y: Number
end
```

- It may be helpful to define some type casts between the FFI type and the Valiance-side type:

```vlnc
cast p: point.Point -> Point =>
  external =>
    Point($p.x as Number, $p.y as Number)
  end
end
```

- This means you can do stuff with a `point.Point` in an `external` block, and cast to `Point` using `as Point` on the way out.

## 27.3. FFI and Lists

- This section is to be written some other time.
- C uses arrays
- Valiance uses lists.
- One idea is to provide a sort of `FFI.toArray(<shape>)` function which does a runtime check to see that the list is rectangular, and of the expected shape.

## 27.4. FFI and Function Objects

- To be determined, given that function execution in Valiance is very very different to C. 

## 27.5. Inline Function Binding

- Two external blocks for a function bind is kinda verbose.
- Especially when interaction with the FFI is all just type casts
- Reusing the C example:

```c
// Say this is in shared library math.dll
int add(int x, int y) {
    return x + y;
}
```

- Instead of needing

```
external ("math.dll") =>
  define add(:FFI.int, :FFI.int) -> FFI.int => end
end
define add(:Number, :Number) =>
  external => both: as FFI.int; add as Number
end
```

- You can simply write

```
external("math.dll") define add(
  :Number as FFI.int,
  :Number as FFI.int
) -> FFI.int as Number => end
```

- Useful when it's all just type casts.

# 28. Type Cast Definitions

_Note: A feature planned in conjunction with FFI. I'm not 100% keen on the concept for normal Valiance, but it's something that is actually a life-saver for FFI._

- Type casting with `as` and `as!` has so far only been defined for `subtype -> supertype`, `supertype -> subtype`, and re-ranking relationships.
- However, it may sometimes be convenient to have type-cast rules that `as` can work with.
        - `as!` doesn't need to know about type-cast rules because it doesn't care about validity.
        - This is especially the case for FFI work, where a `Number` could be `FFI.int`, `FFI.i32`, etc.
- A custom type cast rule to turn type `A` into type `B` can be defined as:

```
cast <typeA> -> <typeB> =>
  <code>
end
```

- `typeA` is either `:{$type}` or `${name}: ${type}`.
- `typeB` is just a type.
- Note that "type" here means "atomic, no generics, no unions/intersections/whatever".
- `code` is the process of how to turn `typeA` into `typeB`. Note that it _must_ return something of `typeB`.
- A motiviating example is turning a `Number` into an `FFI.int`:

```
cast n: Number -> FFI.int =>
  assert => $n inRange(-32_767, 32_767)
  $n as! FFI.int
end
```

- Note that the ultimate conversion is just an `as!`
- But! `as FFI.int`, when given a `Number`, will now perform bounds checking.
        - FFI may be unsafe, but at least you know it has a chance at being valid
- Another example (from earlier):

```
cast p: point.Point -> Point =>
  external =>
    Point($p.x as Number, $p.y as Number)
  end
end
```

- Here, the type cast safely constructs a `Point`. There's no blind reliance on `as!`

### 5.1. Statically counted popping

`pop_n(Number)` discards exactly `Number` values from the top of the stack.
The count must be a non-negative integer known during analysis. It may be a
literal or a numeric static variable produced by the containing function's
`where` clause. Static values may depend on concrete call-site function
introspection, so call-site checked functions can use expressions such as
`$n = max($f.arity, $g.arity)`. Runtime variables are rejected because they
would make the stack shape unreliable.

### Project-wide lint configuration

When a source file belongs to a project, the analyser reads the nearest
`valiance.toml`. The optional `[lints]` table controls lint findings for every
source file in that project:

```toml
[lints]
enabled = true
disable = ["prefer-fold", "constant-never-reassigned"]
```

- `enabled` defaults to `true`. Set it to `false` to disable all project lints.
- `disable` defaults to an empty array and names individual stable lint codes to
  suppress project-wide.
- Unknown settings, invalid value types, and unknown lint codes are manifest
  errors rather than being silently ignored.

Source-level `@lintOff` and `@lintFileOff` directives layer on top of the project
policy and can suppress additional findings. A source directive cannot re-enable
a lint disabled by the project manifest.

### Unicode identifiers

Variable names and alphanumeric element names follow Unicode Standard Annex #31's
identifier model. An identifier starts with a character having the Unicode
`XID_Start` property, or `_`, and continues with characters having
`XID_Continue`. Identifiers are normalized to Unicode NFC as soon as they are
lexed, so canonically equivalent spellings name the same binding.

Control (`Cc`), format (`Cf`), private-use (`Co`), and surrogate (`Cs`)
characters are forbidden. This excludes invisible joiners and bidirectional
formatting controls. Emoji and pictographic symbols are not identifier
characters because they do not have the required XID properties. The compiler
may issue non-fatal `unicode-identifier-security` lints for mixed-script or
visually confusable names; these warnings do not prevent legitimate multilingual
programs.

Symbolic element names retain the operator characters accepted before Unicode
identifier support; this change only broadens the alphanumeric portions of
names.
