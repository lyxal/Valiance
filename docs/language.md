# 0. Syntax

- Elements are parsed in "chains" in a left to right order.
- Each element in a chain uses the result of the next element as its right most argument. That is to say, chains are executed right-to-left.
- Chains are broken upon:
	- Nilads (numbers, strings, variables, elements that start with `\`)
	- Control flow structures
	- `|>`
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

# 1. Fundamentals
- Stack based language. There's a top level stack where everything lives upon.
- `#?` starts single line comment
- `#/` and `/#` for multiline comment. Can be nested, but must be balanced.

## 1.1. Numbers
- Unlimited size, unlimited precision.
        - As much as the computer can handle of course
        - Exact numbers. No `0.1 + 0.2 != 0.3` shenanigans.
        - Able to store multiples of Pi, e, surds, etc. (although pi, e, and surds do not have dedicated literal syntax). This is more to say "the number type itself is very powerful"
- Imaginary parts supported too.
- Can also have `${x}e{$y}` for `x * 10 ** y`. Just `e{y}` is a syntax error. `y` can be any real number.

- 4 Number types, each more general than the last. An `Integer` represents a whole number. A `Real` represents a number with a decimal portion. `Number` is the overarching type for all numbers.
- Notably, `Integer` is sugar for `#integer Number`, and `Real` is sugar for `#real Number`.
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

## 1.3. Tuples

- Fixed length collections.
- Data need not be of the same type.
- Can contain other tuples.
- Finite length, known at compile time.
- Type expressed as `{<types>}`
- Create with `()`
- Example tuples:

```
(1, 2, 3) #? {Number, Number, Number}
("Hello", 5) #? {String, Number}
```

### 1.3.1. Arbitrary Length Tuple Types

- Sometimes, it's useful to accept an arbitrary length tuple as a parameter.
- `{<type>...}` will accept any tuple with that type repeated 0 or more times.
- `{<type1>, <type2>..., <type3>}` for example is 1 type1, followed by 0 or more type2, followed by type3
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

## 1.4. Dictionaries

  - Stores of `key->value` pairs.
  - `dict{...}`
  - Items can be retrieved, if they are in the dictionary, by indexing by key.
  - Effectively anonymous objects
  - Basically hashmaps with bareword keys.
 - Type = `dict[<name>: <ValueType>...]`
- Example:

```
$store = dict{a: 1, b: 2, c: 3}
#? dict[a: Number, b: Number, c: Number]
$store.a #? 1
$store.c #? 3
```

### 1.4.1. Dynamic Dictionaries

- Normal dictionaries have static keys. Not good if you want computed keys.
- `dyndict{...}` provides a hashmap where keys can be any value
- Type = `DynDict[<keytype> -> <valuetype>]`

```
dyndict{"a": 1, "b": 2, "c": 3}
#? dyndict[String -> Number]
```

### 1.4.2. Merging Dictionaries

- `dyndict`s can be merged using elements. This is because keys are determined at runtime.
- `dict`s need special syntax:
- `dict.extend{...}` will add the keys in `{}` to a `dict` on the top of the stack.

```
dict{x: 3} dict.extend{y: 4}
#? Same as
dict{x: 3, y: 4}
```

- `dict.merge` will merge two dictionaries together

```
dict{x: 3} dict.merge dict{y: 4} 
#? Roughly the same as
dict{x: 3} dict.extend{y: 4}
#? Same as
dict{x: 3, y: 4}
```

- `dict.extend` will compile error if any key already exists.
- `dict.merge` will overwrite existing keys in the left hand dictionary.
- Keys in a dict can be updated using `$.key = value`

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
 
## 1.7. Booleans
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
	- Or a list type
- Traditionally, lists are expressed as a composition of generics.
	- `List<Int>` or `List<List<String>>`
- In Valiance, given the fundamentalness of lists, a list type is expressed as a function of the "base" type of the list
- A list is a type, followed by the rank of the list.
	- This makes the list type a baked-in feature, rather than an otherwise after-thought construct.
- `+` after a type represents 1 level of rank
	- `Number+` is a rank-1 (flat) list of `Number`s.
	- `Number++` is a rank-2 (list of lists) list of `Number`s
	- `Number+++` is a rank-3 (list of lists of lists) list of `Number`s.
	- This pattern gets unruly quick, so `Type+n`, where `n` is a positive non-0 integer, is the rank.
		- `Number+3` == `Number+++`
- Notably, `+` is the _exact_ rank of the list.
	- Sometimes, the exact rank of a list is unknowable at compile time.
	- However, a _minimum_ rank can be known at runtime. You can safely say "I don't know exactly what rank this is, but I know for a fact it's a list of at least a certain rank. May be more, but that's okay"
- `*` after a type represents 1 level of minimum rank.
	- `Number*` has a minimum rank of 1. It may end up being a `Number+5` at runtime, but `5 > 1`, so that's okay.
	- `Number**` has a minimum rank of 2. It will never be a `Number+` at runtime, because `1 < 2`
	- `Number*n`, where `n` is a positive non-0 integer, has a minimum rank of `n`. Like the exact rank type, `Number*3` == `Number***`
- A list with exact rank `n` can be passed where a list with minimum rank `m` is expected if `n >= m`.
	- The opposite (passing minimum rank `m` where exact rank `n` is expected) is only true IF `m > n` AND the exact rank list is marked as accepting vectorisation.
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

## 2.1. Type Casting
- A stack item with type `X` can be treated as type `Y` if and only if `X` makes sense as `Y`.
	- That is, if `Y` is a trait implemented by `X` OR
	- If `X` is a list type that could be flattened to `Y` (e.g. a `(Number|Number+)+` could be a `Number++` if there's no `Number`s, only `Number+`s).
- Two types of type casting:
	- Safe - the conversion is checked at runtime. Only list casting will be checked - trait upcasting doesn't need to be checked.
	- Unsafe - the conversion is not checked at runtime. This also only really applies to list casting. This is good for performance, but be careful that it is actually treatable as the intended type.
- Safe = `as Type`
	- `[[1, 2, 3], [4, 5, 6]] as Number*`
	- `Circle as Shape` (assuming trait `Shape` and `Circle` implements `Shape`)
- Unsafe = `as! Type`
	- `[[1, 2, 3], [4, 5, 6]] as! Number*`
	- Unsafe cast that is otherwise safe is a compile error (don't use `as!` where it isn't needed.)

## 2.2. Optional Types

- The Valiance definition of `T?` is `Some[T] | None`
- Notably, this definition allows for a meaningful definition of `T??` as `Some[T?] | None` or `Some[Some[T] | None] | None`.
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
length [T](T+) -> Number       | Length
sum (Number+) -> Number        | Summate numeric list
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
$singleArg = fn (:Number) => println |> println
$singleArg(5) #? Prints "5" twice

$doubleArg = fn (:Number, :Number) => println |> println |> println
$doubleArg(6, 7) #? prints "6", "7", "6"
```

## 6.3. Variable Capturing
- If a function refers to a variable from an outside scope, that function will "capture" that variable's value. If the function is returned from another function, that value will still be available
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
$wrapped() #? 6
#? It used its stored value rather than the scope's value
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
#? Inferred as OverloadSet[VectFunction[Number, Number -> Number], VectFunction[String, String -> String]]

fn => + double
#? Inferred as VectFunction[Number, Number -> Number]
#? The `double` makes the `VectFunction[String, String -> String]` overload impossible, and thus ` VectFunction[String, String -> String]` is discarded.
```
- Untyped variables are inferred from their usage. If an untyped variable is not used, a compile-time error is raised.
- Multiple possible overloads during inference = that function has multiple possible overloads.

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
#? dip in this context is considered `Function[Number, Number, Number, VectFunction[Number, Number -> Number] -> Number, Number]`
#? Note that future usages of dip may use function values that aren't `VectFunction[Number, Number -> Number]`
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

- `'` before an element wraps that element in a function
- `'element` == `fn => element`

# 7. Vectorisation

- High level:

```
[1, 2, 3] + 4 #? [5, 6, 7]
```

- When one or more arguments to an element are of a higher rank than a parameter marked `vec`, those arguments are zipped together and the element applied to each combination. Arguments that have reached their expected rank are reused across all combinations. This process repeats until all `vec` parameters have received arguments at their expected rank. If no overload exists that can handle an argument at its given rank — either directly or through vectorisation — that is a compile error.

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
fn (:Number+ vec, :Number+ vec) => +
```

- But you can also specify how to treat a higher-ranked argument using element overload disambiguation syntax:

```
+[Number+, _]
```

## 7.2. Enabling Vectorisation in an Overload

- By default, parameters do not vectorise. Marking a parameter with `vec` allows it to be used as a vectorisation target - arguments of a higher rank than expected will be zipped and the element applied to each combination.
- `vec` is part of the parameter's type, not just an overload resolution hint. A function where all parameters are marked vec can be written as `VecFunction[...],` which is shorthand for `Function[T vec, U vec -> ...]` — they are exactly the same type.
- Type inference propagates `vec` naturally - `fn => double` infers as `VecFunction[Number -> Number]` because double requires a `vec` parameter to type check.
For example:

```
$myfun = fn (:Number) => double
#? A Function[Number -> Number]
$myfun(10)        #? 20
$myfun([1, 2, 3]) #? Compile error: No overload found

$myfunvec = fn => double
#? Inferred as VecFunction[Number -> Number]
$myfunvec(10)        #? 20
$myfunvec([1, 2, 3]) #? [2, 4, 6]
```

## 7.3. Vectorisation of `T~` and `T~`-able Types

- `T~` can only be safely vectorised where an atomic value is expected.
	- The only structural guarantee of a `T~` of any rugged rank is that there's atomic types present at different depths.
	- Because items can be `T | T~`, `T` is the base case
- However, `T~n` can be vectorised where `T+m` expected granted `n > m`
	- A function expecting `T+m` can safely be given `T~(n - m)`, as there'll be `> 1` level of uniform nesting
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


# 9. Indexing
- `$[<index>]` will get the `index`th item from the top of the stack. 0-indexed.
	- Valid if a type has an overload of `index`. Built-in types that support this include list types, tuple types, and `String`
- `[1, 2, 3] $[1]` == `2`.
- Negative index goes from end (`-1` == last)
- Can also be multiple indices
- `$data $[2, 4, 1]` == `[$data $[2], $data $[4], $data $[1]]`
- Variables can be indexed directly
- `$data[2, 4, 1]` == `$data $[2, 4, 1]`
- Slicing:
	- `$[<start>:<stop>:<step>]` - items starting at `start`, and finishing (and including) at `end`, collecting every `step`th item.
	- `stop` being inclusive corresponds to how the `range` element is inclusive on both ends.
	- `start` = 0 if not provided
	- `stop` = -1 if not provided
	- `step` = 1 if not provided
- `$data[1:4]` == `$data[1, 2, 3, 4]`
- Multi-dimensional indices
- `$data[[1, 2]]` == `$data[1][2]` == `$data[1] $[2]`
- You can multidimensional slice lists (runtime panic - `SliceFault` to try and multidim slice a non-list)

```
[[9, 2, 5], [1, 4, 2]] $[[0, 0]:[1, 1]]
#? [[9, 2], [1, 4]]
```

- Dynamic Dictionary access too

```
dyndict{"name": "Jeff", "age": 12} $["name"] #? "Jeff"
```

- Such indexing obviously isn't needed with normal dictionaries

```
dict{name: "Jeff", age: 12}
#? Just use
$.name #? "Jeff" 
```

## 9.1. Indexing and Augmented Assignment
- Augmented assignment can be applied to an index

```
$data[1] := + 3
```

- This is not mutation. It is sugar for `updateBy($item, $index, $function)`

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

- Within a single item, `|` separates alternatives:

```
3 | 4 => ...              #? literal alternatives
if > 5 | if < 2 => ...    #? condition alternatives
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
  [1, $x = _, 3] => "3 items, the middle is ${x}"
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
  3 | 4, 5 | 6 => "Top of stack was either 3 or 4, and then 5 or 6"
  if > 10 | if < 4, [1, 2, 3] => "Weird stack layout, but sure"
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
  
### 10.5.1. `break`
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
  println("Count is ${count}")
  $count := increment
end

#? Functionally equivalent to

0 while (< 10) =>
  println("Count is {top}")
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

- `unfold` allows for state to be maintained between iterations in a functional programming manner.

```
unfold (<condition>) -> (parameters) =>
  <body>
end
```

- At each iteration, `unfold` checks to see if it should continue iterating
	- Does so by evaluating `condition` on the last generated state
	- Truthy = continue, false = stop
- If it should continue, it executes the `body` using the last generated state as its input
- The arity and multiplicity of `body` determines what values are used as state and what values are returned.
	- If arity and multiplicity are both 1, then the value on the top of the stack is both state and what is generated each iteration.
	- Otherwise, multiplicity must equal `arity + 1`
		- If multiplicity > `arity + 1`, items after the (`arity + 1`)th item are discarded.
	- The generated value will be the top of the stack. All other values will be used as state for the next iteration. Consequently, the state values must be compatible with the parameters expected by `body`
- If a generated value is `None`, then that value will be skipped. To intentionally generate a `None`, it must be wrapped in a `Some`
- The resulting list is tagged as `#infinite`
    - Although unfold can generate finite lists,  it isn't always possible to tell whether it's actually finite. Therefore, all lists are marked infinite for safety, and you can always `#!infinite` if needed.
 - `condition` can be omitted to unfold infinitely.
 - `parameters` is optional. If specified, they define what is used for state, even if the arity would otherwise need more or less parameters. The returns must align with parameters if parameters are provided.

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
  - Otherwise, a new element is created.
- Syntax:

```
define[<generics>] <name>(<params>) -> <returns> => <code> end
```

- `generics` is optional
- `params` is optional, but must contain at least one parameter if specified.
- `returns` is optional

- Example:

```
define doubleAndAdd5(n: Number) =>
  * 2 + 5
end

10 doubleAndAdd5 #? 25
```

## 11.1. Optional Arguments

- Sometimes, it is helpful to have "configuration" style parameters.
	- For example, you might want `sort(list)` to do normal sorting, and `sort(list, key=function)` to sort by a function
- Problem is that the fixed arity requirement of overloads means you can't have `sort[T] (T+) -> T+` and `sort[T, U impl Comparable] (T+, Function[T, U]) -> T+` on the same element.
	- Fine for sorting, but sometimes it can get complicated
- Additionally, specifying when to take optional parameters from the stack can be a little unruly
	- When do values get considered part of the function call.
- However: there is one way to unambiguously specify the arguments to a element: ECS.
- Using that, `define` allows for parameters at the end of an element definition to be given a default value. This makes those parameters optional
	- They can then only be specified using ECS.
	- Function arguments can be specified with `:` syntax though.
		- _ALL_ function arguments must be specified though
- `= <value>` after a parameter declares the default value
- Example:

```
define[T, U: Comparable] sort(:T+, key: Function[T -> U] = 'top) -> T+ => ... end

[4, 1, 3] sort #? Calls with default key
fn => negate end [4, 1, 3] sort #? Still calls with default key
[4, 1, 3] sort: negate #? Overwrite key
[4, 1, 3] sort(_, 'negate) #? Overwrite key
[4, 1, 3] sort(key = 'negate) #? Explicit name
```

- Note that with ECS, not all optional args must be specified
- A named argument does not need to account for the position of other non-optional args
- Passing an optional as an unnamed arg _must_ account for non-optional args
	- Like with `sort(_, 'negate)`
	- Otherwise, `'negate` is treated as the thing to sort, which is a compile error.
 
## 11.2. Overloads and Arity Consistency

- All overloads of an element must have the same arity and multiplicity
	- Compile error if there are overloads with different arities and/or multiplicities
- While mixed arity is possible in Valiance's type system, mixed-arity overloads are typically indicative of elements that should have different names.
	- Instead of `sort(T+)` and `sort(T+, Function[T -> U])`, consider `sort(T+)` and `sortBy(T+, Function[T -> U])`
	- Alternatively, `sort(T+, Function[T -> U] = 'top`

 ## 11.3. `define` and Capturing

_Note: this may change depending on what's easier or more efficient to implement and execute_

 - Variables are captured in their state as they are before the element definition. That is, whatever variable values were set before the definition is evaluated is what is captured.

```
$x = 5
define foo => $x * 3
$x = 10

foo #? 15
#? NOT 30
```

## 11.4. `vecdefine`

- You can use `vecdefine` instead of `define` to mark all parameters as vectorising

```
define +(:Number vec, :Number vec) -> Number => ... end

#? Can be written as

vecdefine +(:Number, :Number) -> Number => ... end
```

## 11.5 Defining Nilads

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

- `generics` is any generic type variables the object needs
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

- Note that a default constructor will have all parameters marked as `vec`. This is because vectorised object creation is a useful default if it is not overridden.

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
- If an object is an optional type (ie `Some[T] | None`), then `.` access is not safe.
- `->`s can be used instead of `.`s to either access the member if the object is `Some` or otherwise push `None`
- `$name->member`, `$->member`
- For assignment, `$name->member = value` will write if `name` is `Some`. Otherwise, the assignment will be cancelled.

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
  $count: #integer Number = 0
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

- `generics` is optional
- `body` contains element definitions OR elements that must be implemented by any implementer.
	- A normal define is a default impl
	- A required impl is a define without a body, but using `extend` instead of `define`

- Example:

```
trait Shape =>
  extend getArea -> Number
  define largerThan(other: Shape) =>
    $self $other |> both: getArea |> >
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
	- Objects implementing a trait implementing other traits must implement every trait in the chain

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

- `generics` is optional.
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

- Note that generics is optional. If no generic is provided, the enum is considered to just be names. Note that if no generics are provided, then members cannot have corresponding values. Note that if a generic is provided, all members must have a corresponding value 

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
- For now, generics are invariant. This is to keep the initial design simple. Covariance and contravariance may be added at a later date.
- When they are added, `define [T: above U]` will be contravariance (any type above or equal to T) and `define [T: any U]` will be covariance (any type of T).
- However, a type can also be constrained to implement certain traits. `T: U` means "generic type `T` which implements `U`"

## 16.1. `atomic` type marker 
_Note: The utility of this feature is still under question_

- Sometimes you may want to express parameters as the atomic type of a generic type.
- For example, if `T` could be a `Number+4`, you may wish to say "I want to be able to expect a `Number+`
- `atomic` after a generic type will expect that type.
- Example

```
define[T] find(
  haystack: T+, 
  needle: T atomic
) => ...
```

- Note that `(T atomic)+` does not always equal `T+`. If `T` would unify to `Number++`, `(T atomic)+` would equal `Number+`, because `T atomic` would be `Number`.
- Also note that at this stage, `T atomic` types are not going to influence the unification result.

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
solve(T vec, U) = T := U vec
solve(U[T], V[W]) = solve(T, W)
solve(T+n, U+m) = T := U+(m-n)
solve(T*n, U*m) = T := U*(m-n)
solve(T~n, U~m) = T := U~(m-n)
solve(T*n, U+m) = T := U*(m-n)
solve(T~n, U+m) = T := U~(m-n)
solve(T~n, U*m) = T := U~(m-n)
solve(T?, U?) = T := U
solve(T?, U) = T := U
```

Note that:

- `T+0` == `T atomic`
- `T*0` == `T atomic | T*`
- `T~0` == `T atomic | T~`

Additionally:

- Unification does not happen across unions. That is, `solve(T|X, U|V)` will not occur, nor give `T := U, X := V` or `T := V, U := X`. Unification also does not happen across intersections. This is because both union types and intersection types can be arbitrarily reordered, meaning that there is no one correct arrangement.
- If a parameter is marked as `vec`, then the solved type will also be marked as `vec`. But not that `vec` marking will not be included in any return types, because `vec` is an input marker, not an actual type constraint.

- Combine is roughly (no assumptions are made about `n` and `m`) (commutative):

```
combine(T, T) = T
combine(T*n, T*m) = T*(min(n, m))
combine(T*n, T+m) = T*(min(m, n))
combine(T+n, T vec) = T+n
combine(T*n, T vec) = T*n
combine(T~n, T vec) = T~n
combine(T, T vec) = T vec
```

- After solving and combining, all types that do not participate in unification (eg union types, types marked as `atomic`) are verified for consistency with the unified type and the overload being applied.

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

- The tag is then attacted to the value. For example, `[1, 2, 3] #sorted` is a `#sorted Number+`.
- Tags can also be removed from a value using `#!<name>`. Attempting to remove a tag from a value that does not have that tag is a compile error.

## 17.1. Constructed Tags
- Constructed tags represent properties of data that are a consequence of how that data is constructed. For example, an infinite list can only be infinite if it is constructed that way. In a sense, constructed tags are sticky. Performing an operation on an infinite list usually does not change its infiniteness. Thus a constructed tag sticks around unless explicitly removed.
- If an input of rank n has constructed tag T, then the output will have tag T at depth (output rank - 1) if output rank >= n. If output rank < input rank, then no tag is carried.
- A value tagged with a constructed tag can be used anywhere the value's type is expected.

## 17.2. Unit Tags
- One might think that constructed tags would be helpful for attaching units to data. For example, you might have `km` as a unit you wish to attach to a number. By all means, `km` should stick to a number if it is passed into an operation - it's not information that should be easily lost.
- However, this can lead to situations where a unit number is used in a situation where it doesn't make semantic sense.
- For example, indexing a list by a distance doesn't really make that much sense.
- Unit tags are constructed tags with an extra rule: a unit tagged value cannot be passed where a unit tag isn't expected.
- This preserves semantic meaning while still having the utility of a sticky tag.

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
- Variant tags do not form part of the compile-time type. However, they can be pattern matched upon at runtime.
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
- However, to signify that a parameter must have an absence of a certain tag, the tag must start with `#!` instead of `#`.
- When a tag is expected, a parameter is only matched if the argument has that expected tag. The argument can have any other number of tags, so long as it has the specified tag.
- However, it may be desirable to disallow this flexibility. Wrapping the set of data tags in `[]` in a parameter type will indicate that only those tags can be present. An argument with any tags not inside the set will not match the parameter.
- Example:

```
define foo(:#someTag Number) => ...
#? foo can accept a `#someTag #otherTag Number`

define baz(:[#someTag] Number) => ...
#? baz cannot accept a `#someTag #otherTag Number`

def bar(:#!someTag Number) => ...
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

## 17.7. Tag Overlays
- To make use of tags, the tag needs to be included in a function's parameters.
- Sometimes though, tag interactions make no difference to a function's behaviour.
- For example, adding a number to a sorted list of numbers does not impact sortedness.
- But by default, `+` will strip a `sorted` computed tag, because it does not make an explicit guarantee about sortedness.
- One would need to add the following extension:

```
define +(:#sorted Number+, :Number vec) -> #sorted Number+ =>
  dip: #!sorted #? To avoid infinite recursion
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
  (#sorted Number+, Number vec) -> #sorted Number+
  (Number vec, #sorted Number) -> #sorted Number+
  (#sorted Number+, #sorted Number+) -> #sorted Number+
end
#sorted: [T] filter => (#sorted T+, Function[T -> #boolean Number]) -> #sorted T+
```

- `generics` is only required if the element being overlayed requires generics
- Note that the generic type need not have the same name as the element. Only the number of generics must be the same.
- Signatures do not have parameter names. Only parameter types.
- Multiple elements can be overlayed at once:

```
#sorted: (+, -, *, /) =>
  (#sorted Number+, Number vec) -> #sorted Number+
  (Number vec, #sorted Number) -> #sorted Number+
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

## 17.8. Tag Validators
- Sometimes you may want to validate that data being tagged actually exhibits the property of the tag.
	- Tags are meant to be compile time human trust based metadata, useful for avoiding costly runtime checks. But sometimes the semantic meaning of a tag may legitimately need validation before application.
- Tag validation is simply a `define` with the tag name. The `define` must return a `#boolean Number`.
- Tag validation occurs at runtime when a tag would be applied. A panic is raised if the validator fails (either returns `0`/`false` or panics). 
- Tag validators can have multiple overloads. If an overload is not found for a validator, and there is at least one validator, then a compile time (not runtime - this can be checked during compilation) is raised.
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

- Note that the compiler will optimise tag validators that always return `true` or `false` to ignore runtime checks. This is helpful if you just want to validate only on type. 

## 17.9. Importing Tags

- `import{<libraryName>.#<tagName>}`

## 17.10. Tag-Attached Elements
- Sometimes it may be desirable to import a set of elements whenever a tag is imported.
- Inserting a tag name before an element name in a define makes it so that the overload is imported when that tag is imported.
- Example:

```
define[T] #sorted sort(:#sorted T+) => top
```

- This overload of `sort` would be imported whenever `#sorted` is imported.

## 17.11. Tag Depth
- If you would have `(#tag T+)+`, you can rewrite it as `#tag+ T+`. Each `+` after a tag is a level of depth that tag applies at. ie levels of nesting from the top.
- Example: `(#B (#A T+)+)+` == `#A++ #B+ T+3`
- Numeric shorthand can also be used. `#tag+3` == `#tag+++`

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
#? with #!infinite
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
  [$.left, $.right] eval |> product
end
```

# 21. Error Handling

## 21.1. The `Result` Type

- `Result` types are the preferred way of doing error handling.
- `Result[T, E: Err]` is defined as a sum type of `OK[T]` and any type implementing the `Err` trait.

```
trait Err =>
  extend message -> String
end
```

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
- The `panic` element takes an object implementing the `Fault` trait, and then immediately returns from all functions until either the top level is reached, or it is caught by a `try/handle`
- Note that each time a function in the call stack is returned from, the same clean up that would usually happen upon function termination occurs. This means that the panic cleans up each layer of the call stack as it bubbles up.

```
trait Fault =>
  extend getMessage -> String
end
```

- Using `panic` in a function will cause that function to have the `Panic[T]` element tag, where `T` is the type of the fault.

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
- If a panic is raised with a type that matches a handler, then control flow goes to that handle block.
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

define[T, U, E: Err] &(x: Result[T, E], callable: Function[T -> U]) -> Result[U, E] =>
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
- The `where` clause solves this by allowing types to be constructed from compile-time-known properties of inputs.
- Syntax:

```
fn (...) -> ... where (<static expressions>) => ...
end
```

- The `where` clause is a small stack-based program that runs at compile time. Its results are used to fill in type variables in the return type, and to constrain overload selection.
- Static expressions are evaluated in order, left to right. The same stack rules apply as everywhere else in Valiance.
- Variables declared in the where clause can be used in the function body
- Executed entirely at compile tieme

## 22.1. Rank Variables

- A list parameter's rank can be named using `$n` after the `+`:
  - `T+$n` in a parameter makes `$n` a read-only rank variable, bound to the rank of the list at the call site.
  - `T+$n` in a return type makes `$n` a mandatory-write rank variable - it must be assigned in the `where` clause.
  - `T+$n` is still an exact rank type.
  - `T*$n` allows for minimum rank list types to be used
  - `T~$n` for rugged rank

## 22.2. Allowed Operations

- **Literals** - numbers and types can be pushed directly onto the static stack.
- **Rank variables** - `$n` from `T+$n`/`T*$n`/`T~$n` parameters, and any variables assigned in the `where` clause.
- **Arithmetic** - `+`, `-`, `*`, `max`, `min` on numbers.
- **Comparison** - `<`, `>`, `<=`, `>=`, `==`, `!=` on numbers; `==`, `!=` on types (no vectorisation).
- **Boolean operations** - `and`, `or`, `not` on numbers (following the same truthiness rules as the rest of Valiance - `0` is false, all other numbers are true).
- **Assignment** - `$name = value` to name a computed value for use in return types or later expressions.
- **Stack manipulation** - `swap`, `pop`, `dup`, `move`, `copy`.
- **Conditionals** - `if/else` follows normal Valiance semantics. `else` is optional - an `if` without an `else` produces an optional value, and the same optional rules apply as elsewhere in the language.
- **Optional operations** - since `if` without `else` produces optional values, two operations are available to deal with them:
  - `?!` - unwrap the optional, or reject the current overload if `None`.
  - `or` - provide a fallback value if `None`.
- **Function introspection** - given a function parameter `$f`:
  - `$f.inputs` - tuple of input types
  - `$f.outputs` - tuple of output types
  - `$f.arity` - number of inputs
  - `$f.multiplicity` - number of outputs
- **Type tuples** - ordered collections of types. Supported operations:
  - `append`, `prepend` - add a type to a tuple
  - `addAll` - merge two tuples
  - `length` - number of types in the tuple
  - `contains` - check if a type is present
  - Indexing - retrieve a type by position
- **Overload assertion** - `?` asserts that a condition holds. If it does not, the current overload is rejected at the call site and overload resolution continues. This is not a runtime assertion. Basically, it's part of overload resolution.

## 22.3. Restrictions

- Type tuples can only contain types, not values.
- Arbitrary element calls are not allowed - only the operations listed above. This ensures the `where` clause always terminates.
- Recursive or looping constructs are not allowed for the same reason.
- `Result` types are not available in the `where` clause - only optionals.

## 22.4. Examples

```
define[T] reshape(xs: T*, shape: Number+) -> T* =>
  #? Implementation here
end

define[T] reshape(xs: T*, shape: {Number...}) -> T+$n where ($n = $shape length) =>
  #? Delegate to the normal version
  reshape($xs, listFrom($shape))
  as! T+$n #? Unsafe cast because we know reshape has done its job
end
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

## 23.1. Basics

- 1 file = 1 module. Directories are namespaces.
- `x.vlnc` = module `x`. `x/y.vlnc` = module `x.y`.
- Only `define`s, `object`s, `trait`s, `variant`s, `enum`s, and tags can be imported.
- A structure must be marked `public` to be importable. Tags are exempt.
- No code executes at import time - only symbols are loaded. Circular imports are therefore not a problem.
- Wildcard imports are not supported.

```
public define foo => 1
define bar => 2
#? foo can be imported, bar cannot
```

## 23.2. Import Syntax

```
import{
  module,
  module as alias,
  module.[Component],
  module.[
    Component,
    Component as Alias,              #? aliased import
    object X as Y,                   #? trait implementation
    hash,                            #? all overloads
    hash(String),                    #? specific concrete overload
    hash(_+),                        #? specific generic overload
    hash except [(String), (_+)],    #? all overloads except these
  ]
}
```

- `_` in an overload signature means "any type." `_+` means "a list of any type," `_++` means "a rank-2 list of any type," and so on, consistent with the rest of the language.
- Multiple components from the same module are listed inside `[]`.
- Single component imports do not require `[]`.
- `except` is only valid after a bare element name - `hash(String) except [...]` is a compile error since you are already importing a specific overload.

## 23.3. Module Resolution

There are four kinds of modules, distinguished by the shape of their import path:

- **Local modules** - a plain name or path, resolved relative to the current file. Prefix with `~` to resolve from the project root instead.
- **Standard library** - first component is `std`. Ships with the compiler, always available.
- **VCS packages** - contains `/`, e.g. `github.com/user/repo`. Resolved from the project's package directory.
- **Installed packages** - prefixed with `@`, e.g. `@somelib`. Resolved from the project's package directory.

```
import {
  utils,                        #? local: ./utils.vlnc
  ~utils,                       #? local: <project root>/utils.vlnc
  std.lists,                    #? standard library
  github.com/user/repo.module,  #? VCS package
  @somelib.module               #? installed package
}
```

## 23.4. Re-exporting

- Imported symbols are not visible to importers of the current module by default.
- Prefix an import with `public` to re-export it:

```
public import {
  module.[Component]
}
```

- This allows library authors to curate a public API from internal modules without exposing internal structure.

## 23.5. Conflict Resolution

- If two imported modules define the same overload for the same types, the compiler raises an error and requires explicit resolution.
- Resolve by explicitly importing only the overload you want:

```
import {
  @pkgA.[hash(String)],   #? explicitly choose pkgA's String overload
  @pkgB.[hash(Number)]    #? and pkgB's Number overload
}
```

- The same applies to trait implementations - if two modules define `object X as Y`, you must explicitly import the one you want:

```
import {
  @pkgA.[object X as Y]   #? explicitly choose pkgA's implementation
}
```

- If two modules define the same generic overload, use wildcard syntax to resolve:

```
import {
  @pkgA.[hash(_+)]   #? pkgA's generic list overload
}
```

- Alternatively, you can just import the whole namespace and use namespace access to disambiguate:

```
import {
  @pkgA,
  @pkgB
}
pkgA.hash("string") #? pkgA's String overload
pkgB.hash("string") #? pkgB's String overload
```

### 23.5.1. Overload Exclusion

- `except` imports all overloads of an element except those specified.
- Syntax: `element except [overload, overload, ...]`
- Each exclusion is an overload signature in `()` - the same syntax as selective overload imports.
- `except` is only valid after a bare element name. Using it after a specific overload is a compile error:

```
hash except [(String)]       #? valid - all overloads except String
hash(String) except [(_+)]   #? compile error - already specific
```

- Exclusions can be concrete or generic:

```
hash except [(String)]          #? exclude concrete String overload
hash except [(_+)]              #? exclude generic list overload
hash except [(String), (_+)]    #? exclude both
```

- If an excluded overload doesn't exist in the module, it is a compile error:

```
#? @somelib doesn't define hash(Number)
hash except [(Number)]   #? compile error - nothing to exclude
```

- `except` interacts with conflict resolution - if after exclusions there are still conflicting overloads from two modules, the conflict error still applies:

```
import{
  @pkgA.[hash except [(String)]],  #? still has hash(_+)
  @pkgB.[hash except [(String)]]   #? still has hash(_+) - conflict!
}
```

## 23.6. Importing Objects

- Importing an object as a component automatically imports all its object-friendly elements.
- OFEs are not automatically imported if the object is namespace-accessed.

```
#? Component import - OFEs imported automatically
import{somemod.Y}
Y foo

#? Namespace access - OFEs not imported
import{somemod}
somemod.Y somemod.foo
```

## 23.7. Tag Importing

- Importing a tag imports all overlay rules and any elements associated via tag definitions.
- Elements that use the tag but are not associated via tag definitions are not imported.

```
#? In sorted.vlnc:
tag #sorted as computed
define[T] #sorted min(:#sorted T+) => $[0]  #? will be imported
define[T] max(:#sorted T+) => $.[-1]        #? will not be imported
```

```
import {sorted.#sorted}
#? #sorted overlay rules imported
#? min imported
#? max not imported
```

# 24. Package Management

## 24.1. The Project Manifest

- Every Valiance project has a `valiance.toml` at its project root.
- Its presence defines the project root for `~` imports.
- If no `valiance.toml` exists, the file is treated as a standalone script. Local and standard library imports still work. External packages are unavailable without a project file.

```toml
[project]
name = "myproject"
version = "1.0.0"
authors = ["Your Name"]

[dependencies]
somelib = "1.2.3"
"github.com/user/repo" = "1.0.0"
```

- All versions are exact. No ranges, no wildcards, no specifiers. Every dependency at every level of the dependency tree declares an exact version.

## 24.2. The Lockfile

- `valiance.lock` records the exact resolved versions of all dependencies, including transitive ones.
- Always commit `valiance.lock` - ensures reproducible builds regardless of what package authors do.
- The lockfile is managed automatically. Never edit it by hand.

## 24.3. Package Installation

- Packages are installed per-project into a `.vln` directory at the project root.
- `.vln` should be added to `.gitignore` - it is always reproducible from `valiance.lock`.
- A global package directory exists for tools intended to be used across projects. Per-project installation is always preferred.

## 24.4. Version Conflicts

- If two dependencies require different versions of the same package, both are installed and used simultaneously.
- Each dependent gets exactly the version it declared.
- Types from different versions of the same package are distinct types - passing a `somelib.MyType 1.2.3` where `somelib.MyType 2.0.0` is expected is a compile error. This is correct since the two types may have different fields and behavior.
- No dependency resolution algorithm is needed - there are no conflicts to resolve, only versions to install.

## 24.5. Upgrading Dependencies

- Upgrading is always explicit. There is no automatic version resolution.
- To upgrade a dependency, change its version in `valiance.toml` and run `vln install`.
- Alternatively, `vln upgrade @somelib 1.3.0` updates a specific package to a specific version, updating both `valiance.toml` and `valiance.lock`.
- Library authors should use `@deprecated` to signal that users should upgrade, rather than relying on version ranges to force it.

## 24.6. Package Manager Commands

- `vln install` - install all dependencies declared in `valiance.toml`
- `vln add @somelib 1.2.3` - add an installed package at an exact version
- `vln add github.com/user/repo 1.0.0` - add a VCS dependency at an exact version
- `vln remove @somelib` - remove a package
- `vln upgrade @somelib 1.3.0` - upgrade a specific package to a specific version

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
