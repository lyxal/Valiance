# Valiance Built-ins and Standard Library Reference

## Built-ins — Arithmetic

### `%`

Return the remainder after numeric division.

**Overloads**

- `%(left: Number, right: Number) -> Number`

  Return the remainder after numeric division.

  **Parameters**

  - `left` — Dividend.
  - `right` — Divisor.

  **Returns:** The remainder.

  **Example**

  ```vlnc
  7.5 2.5 %
  ```
  Result:

  ```text
  0
  ```

- `%(left: Int, right: Real) -> Real`

  Return the remainder after numeric division.

  **Parameters**

  - `left` — Dividend.
  - `right` — Divisor.

  **Returns:** The remainder.

  **Example**

  ```vlnc
  7 2.5 %
  ```
  Result:

  ```text
  2
  ```

- `%(left: Real, right: Int) -> Real`

  Return the remainder after numeric division.

  **Parameters**

  - `left` — Dividend.
  - `right` — Divisor.

  **Returns:** The remainder.

  **Example**

  ```vlnc
  7.5 2 %
  ```
  Result:

  ```text
  1.5
  ```

- `%(left: Real, right: Real) -> Real`

  Return the remainder after numeric division.

  **Parameters**

  - `left` — Dividend.
  - `right` — Divisor.

  **Returns:** The remainder.

  **Example**

  ```vlnc
  7.5 2.5 %
  ```
  Result:

  ```text
  0
  ```

- `%(left: Int, right: Int) -> Int`

  Return the remainder after numeric division.

  **Parameters**

  - `left` — Dividend.
  - `right` — Divisor.

  **Returns:** The remainder.

  **Example**

  ```vlnc
  7 2 %
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `left` — Dividend.
- `right` — Divisor.

**Returns:** The remainder.

**Example**

```vlnc
17 5 %
```
Result:

```text
2
```

### `*`

Multiply numbers or repeat a string an integer number of times.

**Overloads**

- `*(left: Number, right: Number) -> Number`

  Multiply two numbers.

  **Parameters**

  - `left` — First factor.
  - `right` — Second factor.

  **Returns:** The numeric product.

  **Example**

  ```vlnc
  7.5 2.5 *
  ```
  Result:

  ```text
  18.75
  ```

- `*(left: Int, right: Real) -> Real`

  Multiply two numbers.

  **Parameters**

  - `left` — First factor.
  - `right` — Second factor.

  **Returns:** The numeric product.

  **Example**

  ```vlnc
  7 2.5 *
  ```
  Result:

  ```text
  17.5
  ```

- `*(left: Real, right: Int) -> Real`

  Multiply two numbers.

  **Parameters**

  - `left` — First factor.
  - `right` — Second factor.

  **Returns:** The numeric product.

  **Example**

  ```vlnc
  7.5 2 *
  ```
  Result:

  ```text
  15
  ```

- `*(left: Real, right: Real) -> Real`

  Multiply two numbers.

  **Parameters**

  - `left` — First factor.
  - `right` — Second factor.

  **Returns:** The numeric product.

  **Example**

  ```vlnc
  7.5 2.5 *
  ```
  Result:

  ```text
  18.75
  ```

- `*(left: Int, right: Int) -> Int`

  Multiply two numbers.

  **Parameters**

  - `left` — First factor.
  - `right` — Second factor.

  **Returns:** The numeric product.

  **Example**

  ```vlnc
  7 2 *
  ```
  Result:

  ```text
  14
  ```

- `*(count: Int, value: String) -> String`

  Repeat a string an integer number of times.

  **Parameters**

  - `count` — Number of repetitions.
  - `value` — String to repeat.

  **Returns:** The repeated string, or an empty string when count is non-positive.

  **Example**

  ```vlnc
  3 "ha" *
  ```
  Result:

  ```text
  hahaha
  ```

- `*(value: String, count: Int) -> String`

  Repeat a string an integer number of times.

  **Parameters**

  - `value` — String to repeat.
  - `count` — Number of repetitions.

  **Returns:** The repeated string, or an empty string when count is non-positive.

  **Example**

  ```vlnc
  "ha" 3 *
  ```
  Result:

  ```text
  hahaha
  ```


**Parameters**

- `left` — Left operand.
- `right` — Right operand.

**Returns:** A numeric product or repeated string.

**Example**

```vlnc
6 7 *
```
Result:

```text
42
```

**Example**

```vlnc
"ha" 3 *
```
Result:

```text
hahaha
```

### `**`

Raise a non-negative real number to a real power.

**Overloads**

- `**(base: Number, exponent: Number) -> Number`

  **Example**

  ```vlnc
  9 0.5 **
  ```
  Result:

  ```text
  3
  ```

- `**(base: Real, exponent: Int) -> Real`

  Raise a non-negative real number to a real power.

  **Parameters**

  - `base` — Non-negative real base.
  - `exponent` — Real exponent.

  **Returns:** The real exponentiation result.

  **Example**

  ```vlnc
  2.5 2 **
  ```
  Result:

  ```text
  6.25
  ```

  **Notes**

  - Panics with DomainFault when the base is negative.

- `**(base: Int, exponent: Int) -> Real`

  Raise a non-negative real number to a real power.

  **Parameters**

  - `base` — Non-negative real base.
  - `exponent` — Real exponent.

  **Returns:** The real exponentiation result.

  **Example**

  ```vlnc
  2 3 **
  ```
  Result:

  ```text
  8
  ```

  **Notes**

  - Panics with DomainFault when the base is negative.


**Parameters**

- `base` — Non-negative real base.
- `exponent` — Real exponent.

**Returns:** The real exponentiation result.

**Example**

```vlnc
9 0.5 **
```
Result:

```text
3
```

**Notes**

- Panics with DomainFault when the base is negative.

### `**c`

Raise a number to a numeric power using complex exponentiation.

**Overloads**

- `**c(base: Number, exponent: Number) -> Number`

  **Example**

  ```vlnc
  2 3 **c
  ```
  Result:

  ```text
  8
  ```


**Parameters**

- `base` — Number to raise.
- `exponent` — Power to apply.

**Returns:** The exponentiated Number, including complex results.

**Example**

```vlnc
2 3 **c
```
Result:

```text
8
```

### `+`

Add numbers or concatenate strings.

**Overloads**

- `+(left: String, right: String) -> String`

  Concatenate two strings.

  **Parameters**

  - `left` — Text placed first.
  - `right` — Text placed second.

  **Returns:** The concatenated string.

  **Example**

  ```vlnc
  "Val" "iance" +
  ```
  Result:

  ```text
  Valiance
  ```

- `+(left: Number, right: Number) -> Number`

  Add two numbers.

  **Parameters**

  - `left` — First number.
  - `right` — Second number.

  **Returns:** The numeric sum.

  **Example**

  ```vlnc
  7.5 2.5 +
  ```
  Result:

  ```text
  10
  ```

- `+(left: Int, right: Real) -> Real`

  Add two numbers.

  **Parameters**

  - `left` — First number.
  - `right` — Second number.

  **Returns:** The numeric sum.

  **Example**

  ```vlnc
  7 2.5 +
  ```
  Result:

  ```text
  9.5
  ```

- `+(left: Real, right: Int) -> Real`

  Add two numbers.

  **Parameters**

  - `left` — First number.
  - `right` — Second number.

  **Returns:** The numeric sum.

  **Example**

  ```vlnc
  7.5 2 +
  ```
  Result:

  ```text
  9.5
  ```

- `+(left: Real, right: Real) -> Real`

  Add two numbers.

  **Parameters**

  - `left` — First number.
  - `right` — Second number.

  **Returns:** The numeric sum.

  **Example**

  ```vlnc
  7.5 2.5 +
  ```
  Result:

  ```text
  10
  ```

- `+(left: Int, right: Int) -> Int`

  Add two numbers.

  **Parameters**

  - `left` — First number.
  - `right` — Second number.

  **Returns:** The numeric sum.

  **Example**

  ```vlnc
  7 2 +
  ```
  Result:

  ```text
  9
  ```


**Parameters**

- `left` — Left numeric or string operand.
- `right` — Right operand of the same supported kind.

**Returns:** The numeric sum or concatenated string.

**Example**

```vlnc
2 3 +
```
Result:

```text
5
```

**Example**

```vlnc
"Val" "iance" +
```
Result:

```text
Valiance
```

### `-`

Subtract the top numeric operand from the value beneath it.

**Overloads**

- `-(left: Number, right: Number) -> Number`

  Subtract the top numeric operand from the value beneath it.

  **Parameters**

  - `left` — Value to subtract from.
  - `right` — Value to subtract.

  **Returns:** The numeric difference.

  **Example**

  ```vlnc
  7.5 2.5 -
  ```
  Result:

  ```text
  5
  ```

- `-(left: Int, right: Real) -> Real`

  Subtract the top numeric operand from the value beneath it.

  **Parameters**

  - `left` — Value to subtract from.
  - `right` — Value to subtract.

  **Returns:** The numeric difference.

  **Example**

  ```vlnc
  7 2.5 -
  ```
  Result:

  ```text
  4.5
  ```

- `-(left: Real, right: Int) -> Real`

  Subtract the top numeric operand from the value beneath it.

  **Parameters**

  - `left` — Value to subtract from.
  - `right` — Value to subtract.

  **Returns:** The numeric difference.

  **Example**

  ```vlnc
  7.5 2 -
  ```
  Result:

  ```text
  5.5
  ```

- `-(left: Real, right: Real) -> Real`

  Subtract the top numeric operand from the value beneath it.

  **Parameters**

  - `left` — Value to subtract from.
  - `right` — Value to subtract.

  **Returns:** The numeric difference.

  **Example**

  ```vlnc
  7.5 2.5 -
  ```
  Result:

  ```text
  5
  ```

- `-(left: Int, right: Int) -> Int`

  Subtract the top numeric operand from the value beneath it.

  **Parameters**

  - `left` — Value to subtract from.
  - `right` — Value to subtract.

  **Returns:** The numeric difference.

  **Example**

  ```vlnc
  7 2 -
  ```
  Result:

  ```text
  5
  ```


**Parameters**

- `left` — Value to subtract from.
- `right` — Value to subtract.

**Returns:** The numeric difference.

**Example**

```vlnc
10 3 -
```
Result:

```text
7
```

### `/`

Divide numbers or reduce a non-empty list.

**Aliases:** `reduce`

**Overloads**

- `/(left: Number, right: Number) -> Number`

  Divide the left number by the right number.

  **Parameters**

  - `left` — Dividend.
  - `right` — Non-zero divisor.

  **Returns:** The quotient; integer inputs can produce a fractional result.

  **Example**

  ```vlnc
  7.5 2.5 /
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Division by zero panics with `DivisionByZeroFault`.

- `/(left: Int, right: Real) -> Real`

  Divide the left number by the right number.

  **Parameters**

  - `left` — Dividend.
  - `right` — Non-zero divisor.

  **Returns:** The quotient; integer inputs can produce a fractional result.

  **Example**

  ```vlnc
  7 2.5 /
  ```
  Result:

  ```text
  2.8
  ```

  **Notes**

  - Division by zero panics with `DivisionByZeroFault`.

- `/(left: Real, right: Int) -> Real`

  Divide the left number by the right number.

  **Parameters**

  - `left` — Dividend.
  - `right` — Non-zero divisor.

  **Returns:** The quotient; integer inputs can produce a fractional result.

  **Example**

  ```vlnc
  7.5 2 /
  ```
  Result:

  ```text
  3.75
  ```

  **Notes**

  - Division by zero panics with `DivisionByZeroFault`.

- `/(left: Real, right: Real) -> Real`

  Divide the left number by the right number.

  **Parameters**

  - `left` — Dividend.
  - `right` — Non-zero divisor.

  **Returns:** The quotient; integer inputs can produce a fractional result.

  **Example**

  ```vlnc
  7.5 2.5 /
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Division by zero panics with `DivisionByZeroFault`.

- `/(left: Int, right: Int) -> Real`

  Divide the left number by the right number.

  **Parameters**

  - `left` — Dividend.
  - `right` — Non-zero divisor.

  **Returns:** The quotient; integer inputs can produce a fractional result.

  **Example**

  ```vlnc
  7 2 /
  ```
  Result:

  ```text
  3.5
  ```

  **Notes**

  - Division by zero panics with `DivisionByZeroFault`.

- `/(values: {Int, String}, reducer: Function[Int, String -> String]) -> String`

  Reduce a non-empty finite collection from left to right.

  Use the first item as the accumulator, then call the reducer with the accumulator followed by each remaining item. A singleton returns its item.

  The `reduce` alias exposes these collection overloads.

  **Parameters**

  - `values` — Non-empty finite collection.
  - `reducer` — Callable consuming accumulator and item, returning the next accumulator.

  **Returns:** The final accumulator.

  **Example**

  ```vlnc
  {3, "ha"} /: *
  ```
  Result:

  ```text
  hahaha
  ```

  **Notes**

  - An empty collection panics; use `fold` with a seed when it may be empty.

- `/(values: Item+, reducer: Function[Item, Item -> Item]) -> Item`

  Reduce a non-empty finite collection from left to right.

  Use the first item as the accumulator, then call the reducer with the accumulator followed by each remaining item. A singleton returns its item.

  The `reduce` alias exposes these collection overloads.

  **Parameters**

  - `values` — Non-empty finite collection.
  - `reducer` — Callable consuming accumulator and item, returning the next accumulator.

  **Returns:** The final accumulator.

  **Example**

  ```vlnc
  [10, 3, 2] /: -
  ```
  Result:

  ```text
  5
  ```

  **Example**

  ```vlnc
  [1, 2, 3] reduce: +
  ```
  Result:

  ```text
  6
  ```

  **Notes**

  - An empty collection panics; use `fold` with a seed when it may be empty.


Numeric overloads perform division.

The list overload, also available as `reduce`, starts with the first item and reduces the remainder.

**Returns:** The quotient or final reduced value.

### `double`

Multiply a number by two.

**Overloads**

- `double(value: Number) -> Number`

  **Example**

  ```vlnc
  21 double
  ```
  Result:

  ```text
  42
  ```


**Parameters**

- `value` — Number to double.

**Returns:** The doubled number.

**Example**

```vlnc
21 double
```
Result:

```text
42
```

### `inc`

Increase an integer by one.

**Overloads**

- `inc(value: Number) -> Number`

  **Example**

  ```vlnc
  4 inc
  ```
  Result:

  ```text
  5
  ```

- `inc(value: Int) -> Int`

  **Example**

  ```vlnc
  4 inc
  ```
  Result:

  ```text
  5
  ```


**Parameters**

- `value` — Int to increment.

**Returns:** The next integer.

**Example**

```vlnc
4 inc
```
Result:

```text
5
```

### `squared`

Multiply a number by itself.

**Aliases:** `square`

**Overloads**

- `squared(value: Number) -> Number`

  **Example**

  ```vlnc
  5 squared
  ```
  Result:

  ```text
  25
  ```


**Parameters**

- `value` — Number to square.

**Returns:** The square of the number.

**Example**

```vlnc
5 squared
```
Result:

```text
25
```

## Built-ins — Boolean

### `false`

Push the Boolean false value.

**Overloads**

- `false() -> #boolean Int`

  Push the Boolean false value.

  **Returns:** The `false` Boolean value.

  **Example**

  ```vlnc
  false
  ```
  Result:

  ```text
  0
  ```


**Returns:** The `false` Boolean value.

### `true`

Push the Boolean true value.

**Overloads**

- `true() -> #boolean Int`

  Push the Boolean true value.

  **Returns:** The `true` Boolean value.

  **Example**

  ```vlnc
  true
  ```
  Result:

  ```text
  1
  ```


**Returns:** The `true` Boolean value.

## Built-ins — Collections

### `addAll`

Append every item from one list to another list.

**Overloads**

- `addAll(target: Item+, items: Item+) -> Item+`

  **Example**

  ```vlnc
  [1, 2] [3, 4] addAll
  ```
  Result:

  ```text
  [1, 2, 3, 4]
  ```


**Parameters**

- `target` — List receiving the items.
- `items` — Items to append.

**Returns:** A combined list.

**Example**

```vlnc
[1, 2] [3, 4] addAll
```
Result:

```text
[1, 2, 3, 4]
```

### `append`

Return a list with one item appended.

**Overloads**

- `append(item: Item, values: Item+) -> Item+`

  Return a list with one item appended.

  The item and list may appear in either supported stack order.

  **Parameters**

  - `item` — Item added to the end.
  - `values` — Input list.

  **Returns:** A new list ending with the item.

  **Example**

  ```vlnc
  3 [1, 2] append
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

- `append(values: Item+, item: Item) -> Item+`

  Return a list with one item appended.

  The item and list may appear in either supported stack order.

  **Parameters**

  - `values` — Input list.
  - `item` — Item added to the end.

  **Returns:** A new list ending with the item.

  **Example**

  ```vlnc
  [1, 2] 3 append
  ```
  Result:

  ```text
  [1, 2, 3]
  ```


The item and list may appear in either supported stack order.

**Parameters**

- `values` — Input list.
- `item` — Item added to the end.

**Returns:** A new list ending with the item.

**Example**

```vlnc
[1, 2] 3 append
```
Result:

```text
[1, 2, 3]
```

**Example**

```vlnc
3 [1, 2] append
```
Result:

```text
[1, 2, 3]
```

### `cArr`

Validate dimensions and convert a nested numeric list into a contiguous C array.

**Overloads**

- `cArr(values: Int~, shape: {Number...}) -> &char+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&char]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &char+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&char]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &short+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&short]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &short+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&short]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &int+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&int]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &int+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr
  ```
  Infer the native scalar type from the numeric input.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &int+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&int]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &int+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr
  ```
  Infer the native scalar type from the numeric input.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &long+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&long]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &long+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&long]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &longlong+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&longlong]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &longlong+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&longlong]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &unsignedchar+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&unsignedchar]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &unsignedchar+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&unsignedchar]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &unsignedshort+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&unsignedshort]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &unsignedshort+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&unsignedshort]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &unsignedint+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&unsignedint]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &unsignedint+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&unsignedint]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &unsignedlong+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&unsignedlong]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &unsignedlong+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&unsignedlong]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: {Number...}) -> &unsignedlonglong+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] {2, 2} cArr[&unsignedlonglong]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Int~, shape: Number+) -> &unsignedlonglong+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1, 2], [3, 4]] [2, 2] cArr[&unsignedlonglong]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Real~, shape: {Number...}) -> &float+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1.5, 2.5], [3.5, 4.5]] {2, 2} cArr[&float]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Real~, shape: Number+) -> &float+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1.5, 2.5], [3.5, 4.5]] [2, 2] cArr[&float]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Real~, shape: {Number...}) -> &double+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1.5, 2.5], [3.5, 4.5]] {2, 2} cArr[&double]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Real~, shape: {Number...}) -> &double+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1.5, 2.5], [3.5, 4.5]] {2, 2} cArr
  ```
  Infer the native scalar type from the numeric input.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Real~, shape: Number+) -> &double+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1.5, 2.5], [3.5, 4.5]] [2, 2] cArr[&double]
  ```
  Validate a two-dimensional shape and copy its values into a flat native buffer.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

- `cArr(values: Real~, shape: Number+) -> &double+`

  Validate dimensions and convert a nested numeric list into a contiguous C array.

  **Parameters**

  - `values` — A finite list, nested to the declared depth.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

  **Example**

  ```vlnc
  [[1.5, 2.5], [3.5, 4.5]] [2, 2] cArr
  ```
  Infer the native scalar type from the numeric input.

  **Notes**

  - `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.


**Parameters**

- `values` — A finite list, nested to the declared depth.
- `shape` — Non-empty list or tuple of non-negative integer dimensions.

**Returns:** A flat FFI buffer, `&int+` for Int or `&double+` for Real by default.

**Example**

```vlnc
[[1, 2], [3, 4]] {2, 2} cArr
```
Result:

```text
&int+ buffer [1, 2, 3, 4]
```

**Notes**

- `cArr[&short]` or another supported FFI scalar target selects the C element type. Panics with ShapeFault for mismatched or ragged dimensions, and RangeFault for an unrepresentable scalar. The C ABI remains a flat pointer, not an int**.

### `drop`

Discard a prefix from a list or string.

**Overloads**

- `drop(count: Int, values: String) -> String`

  Discard a prefix from a list or string.

  **Parameters**

  - `count` — Number of leading items to remove.
  - `values` — Input value.

  **Returns:** The remaining suffix.

  **Example**

  ```vlnc
  1 "abcd" drop
  ```
  Result:

  ```text
  bcd
  ```

- `drop(values: String, count: Int) -> String`

  Discard a prefix from a list or string.

  **Parameters**

  - `values` — Input value.
  - `count` — Number of leading items to remove.

  **Returns:** The remaining suffix.

  **Example**

  ```vlnc
  "abcd" 1 drop
  ```
  Result:

  ```text
  bcd
  ```

- `drop(count: Int, values: Item+) -> Item+`

  Discard a prefix from a list or string.

  **Parameters**

  - `count` — Number of leading items to remove.
  - `values` — Input value.

  **Returns:** The remaining suffix.

  **Example**

  ```vlnc
  1 [1, 2, 3] drop
  ```
  Result:

  ```text
  [2, 3]
  ```

- `drop(values: Item+, count: Int) -> Item+`

  Discard a prefix from a list or string.

  **Parameters**

  - `values` — Input value.
  - `count` — Number of leading items to remove.

  **Returns:** The remaining suffix.

  **Example**

  ```vlnc
  [1, 2, 3] 1 drop
  ```
  Result:

  ```text
  [2, 3]
  ```


**Parameters**

- `values` — Input value.
- `count` — Number of leading items to remove.

**Returns:** The remaining suffix.

**Example**

```vlnc
[1, 2, 3] 1 drop
```
Result:

```text
[2, 3]
```

**Example**

```vlnc
1 "abc" drop
```
Result:

```text
bc
```

### `dropLast`

Return a finite list without its final item.

**Overloads**

- `dropLast(values: Item+) -> Item+`

  **Example**

  ```vlnc
  [1, 2, 3] dropLast
  ```
  Result:

  ```text
  [1, 2]
  ```


**Parameters**

- `values` — Input finite list.

**Returns:** All items except the final item.

**Example**

```vlnc
[1, 2, 3] dropLast
```
Result:

```text
[1, 2]
```

### `first`

Return the first item of a non-empty list or string.

**Overloads**

- `first(values: String) -> String`

  Return the first item of a non-empty list or string.

  **Parameters**

  - `values` — Non-empty input value.

  **Returns:** The first item or one-character string.

  **Example**

  ```vlnc
  "abc" first
  ```
  Result:

  ```text
  a
  ```

- `first(values: Item+) -> Item`

  Return the first item of a non-empty list or string.

  **Parameters**

  - `values` — Non-empty input value.

  **Returns:** The first item or one-character string.

  **Example**

  ```vlnc
  [10, 20, 30] first
  ```
  Result:

  ```text
  10
  ```


**Parameters**

- `values` — Non-empty input value.

**Returns:** The first item or one-character string.

**Example**

```vlnc
[10, 20] first
```
Result:

```text
10
```

### `fold`

Fold a list from an explicit seed value.

**Overloads**

- `fold(values: Item+, seed: Accumulator, folder: Function[Accumulator, Item -> Accumulator]) -> Accumulator`

  **Example**

  ```vlnc
  [1, 2, 3] 10 fold: +
  ```
  Result:

  ```text
  16
  ```

  **Example**

  ```vlnc
  ([] as[Int+]) 10 fold: +
  ```
  Result:

  ```text
  10
  ```


Applies the folder to the accumulator and every item in order.

An empty list returns the seed.

**Parameters**

- `values` — Values to fold.
- `seed` — Initial accumulator.
- `folder` — Accumulator and item callable.

**Returns:** The final accumulator.

**Example**

```vlnc
[1, 2, 3] 10 fold: +
```
Result:

```text
16
```

**Example**

```vlnc
([] as[Int+]) 10 fold: +
```
Result:

```text
10
```

### `groupConsecutive`

Group adjacent equal items.

**Overloads**

- `groupConsecutive(values: Item+) -> Item+2`

  Group adjacent equal items.

  **Parameters**

  - `values` — Input list or string.

  **Returns:** A list of consecutive groups.

  **Example**

  ```vlnc
  [1, 1, 2, 1] groupConsecutive
  ```
  Result:

  ```text
  [[1, 1], [2], [1]]
  ```

- `groupConsecutive(values: String) -> String+`

  Group adjacent equal items.

  **Parameters**

  - `values` — Input list or string.

  **Returns:** A list of consecutive groups.

  **Example**

  ```vlnc
  "aabbc" groupConsecutive
  ```
  Result:

  ```text
  ['aa', 'bb', 'c']
  ```


**Parameters**

- `values` — Input list or string.

**Returns:** A list of consecutive groups.

### `last`

Return the last item of a non-empty finite list or string.

**Overloads**

- `last(values: String) -> String`

  Return the last item of a non-empty finite list or string.

  **Parameters**

  - `values` — Non-empty input value.

  **Returns:** The final item or one-character string.

  **Example**

  ```vlnc
  "abc" last
  ```
  Result:

  ```text
  c
  ```

- `last(values: #-infinite Item+) -> Item`

  Return the last item of a non-empty finite list or string.

  **Parameters**

  - `values` — Non-empty input value.

  **Returns:** The final item or one-character string.

  **Example**

  ```vlnc
  [10, 20, 30] last
  ```
  Result:

  ```text
  30
  ```


**Parameters**

- `values` — Non-empty input value.

**Returns:** The final item or one-character string.

**Example**

```vlnc
[10, 20] last
```
Result:

```text
20
```

### `length`

Return the number of items in a finite list or string.

**Aliases:** `len`

**Overloads**

- `length(values: String) -> Int`

  Return the number of items in a finite list or string.

  **Parameters**

  - `values` — Finite list or string whose size is required.

  **Returns:** The value length as an `Int`.

  **Example**

  ```vlnc
  "abc" length
  ```
  Result:

  ```text
  3
  ```

- `length(values: #-infinite Item+) -> Int`

  Return the number of items in a finite list or string.

  **Parameters**

  - `values` — Finite list or string whose size is required.

  **Returns:** The value length as an `Int`.

  **Example**

  ```vlnc
  [10, 20, 30] length
  ```
  Result:

  ```text
  3
  ```

- `length(fields: Fields[Owner]) -> Int`

  Count the declared storage fields in a reflection collection.

  **Parameters**

  - `fields` — Declared field metadata.

  **Returns:** The number of fields, including private fields.

  **Example**

  ```vlnc
  fields[Int] | length
  ```
  Result:

  ```text
  0
  ```


**Parameters**

- `values` — Finite list or string whose size is required.

**Returns:** The value length as an `Int`.

**Example**

```vlnc
"hello" length
```
Result:

```text
5
```

### `map`

Apply a callable to every item in a list.

**Overloads**

- `map(values: String, operation: Function[String -> Mapped]) -> Mapped+`

  Apply a callable to every item in a list.

  Pure mappings are lazy; mappings whose callable is eager execute immediately and return no list.

  **Parameters**

  - `values` — Input list.
  - `operation` — Callable applied to each item.

  **Returns:** A list of mapped values, or no value for an eager effect-only callable.

  **Example**

  ```vlnc
  "abc" map: to[String]
  ```
  Result:

  ```text
  ['a', 'b', 'c']
  ```

- `map(values: Item+, operation: Function[Item -> Mapped]) -> Mapped+`

  **Example**

  ```vlnc
  [1, 2, 3] map: double
  ```
  Result:

  ```text
  [2, 4, 6]
  ```

- `map(values: Item+, operation: Function[ -> Mapped]) -> Mapped+`

  Apply a callable to every item in a list.

  Pure mappings are lazy; mappings whose callable is eager execute immediately and return no list.

  **Parameters**

  - `values` — Input list.
  - `operation` — Callable applied to each item.

  **Returns:** A list of mapped values, or no value for an eager effect-only callable.

  **Example**

  ```vlnc
  [1, 2, 3] map: fn => 7 end
  ```
  Result:

  ```text
  [7, 7, 7]
  ```

- `map(values: Item+, operation: Function) -> ()`

  Apply a callable to every item in a list.

  Pure mappings are lazy; mappings whose callable is eager execute immediately and return no list.

  **Parameters**

  - `values` — Input list.
  - `operation` — Callable applied to each item.

  **Returns:** A list of mapped values, or no value for an eager effect-only callable.

  **Example**

  ```vlnc
  [1, 2, 3] map: println
  ```
  Prints each item on its own line and leaves no list on the stack.


Pure mappings are lazy; mappings whose callable is eager execute immediately and return no list.

**Parameters**

- `values` — Input list.
- `operation` — Callable applied to each item.

**Returns:** A list of mapped values, or no value for an eager effect-only callable.

**Example**

```vlnc
[1, 2, 3] map: double
```
Result:

```text
[2, 4, 6]
```

### `overtake`

Repeat a finite list cyclically until the requested length is reached.

**Overloads**

- `overtake(values: Item+, count: Int) -> Item+`

  **Example**

  ```vlnc
  [1, 2] 5 overtake
  ```
  Result:

  ```text
  [1, 2, 1, 2, 1]
  ```


**Parameters**

- `values` — Non-empty finite source list.
- `count` — Requested output length.

**Returns:** A list of exactly count items.

**Example**

```vlnc
[1, 2] 5 overtake
```
Result:

```text
[1, 2, 1, 2, 1]
```

### `range`

Create an inclusive lazy integer range.

**Overloads**

- `range(start: Number, stop: Number) -> Number+`

  Create an inclusive lazy integer range.

  **Parameters**

  - `start` — First integer.
  - `stop` — Last integer, included.

  **Returns:** A lazy list of integers from start through stop.

  **Example**

  ```vlnc
  1.0 3.0 range
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

- `range(start: Int, stop: Int) -> Int+`

  Create an inclusive lazy integer range.

  **Parameters**

  - `start` — First integer.
  - `stop` — Last integer, included.

  **Returns:** A lazy list of integers from start through stop.

  **Example**

  ```vlnc
  1 3 range
  ```
  Result:

  ```text
  [1, 2, 3]
  ```


**Parameters**

- `start` — First integer.
- `stop` — Last integer, included.

**Returns:** A lazy list of integers from start through stop.

**Example**

```vlnc
1 4 range
```
Result:

```text
[1, 2, 3, 4]
```

### `removeAt`

Return a list without the item at one index.

**Overloads**

- `removeAt(values: Item+, index: Int) -> Item+`

  **Example**

  ```vlnc
  [10, 20, 30] 1 removeAt
  ```
  Result:

  ```text
  [10, 30]
  ```


**Parameters**

- `values` — Input list.
- `index` — Zero-based index to remove; negative indices count from the end.

**Returns:** A new list containing every other item.

**Example**

```vlnc
[10, 20, 30] 1 removeAt
```
Result:

```text
[10, 30]
```

### `reshape`

Reshape a finite value using a list or tuple of dimensions.

**Overloads**

- `reshape(values: Item~, shape: {Number...}) -> Item+$n`

  Reshape a finite value using a list or tuple of dimensions.

  **Parameters**

  - `values` — Finite input value; nested lists are flattened first.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A nested list whose rank equals the number of dimensions.

  **Example**

  ```vlnc
  [1, 2, 3, 4] {2, 2} reshape
  ```
  Result:

  ```text
  [[1, 2], [3, 4]]
  ```

- `reshape(values: Item~, shape: Number+) -> Item*`

  Reshape a finite value using a list or tuple of dimensions.

  **Parameters**

  - `values` — Finite input value; nested lists are flattened first.
  - `shape` — Non-empty list or tuple of non-negative integer dimensions.

  **Returns:** A nested list whose rank equals the number of dimensions.

  **Example**

  ```vlnc
  [1, 2, 3, 4] [2, 2] reshape
  ```
  Result:

  ```text
  [[1, 2], [3, 4]]
  ```


**Parameters**

- `values` — Finite input value; nested lists are flattened first.
- `shape` — Non-empty list or tuple of non-negative integer dimensions.

**Returns:** A nested list whose rank equals the number of dimensions.

**Example**

```vlnc
[1, 2, 3, 4, 5, 6] reshape {2, 3}
```
Result:

```text
[[1, 2, 3], [4, 5, 6]]
```

### `rotate`

Rotate a finite list or string to the left.

**Overloads**

- `rotate(value: Item+, amount: Int) -> Item+`

  Rotate a finite list or string to the left.

  **Parameters**

  - `value` — Value to rotate.
  - `amount` — Signed rotation amount.

  **Returns:** The rotated value.

  **Example**

  ```vlnc
  [1, 2, 3] 1 rotate
  ```
  Result:

  ```text
  [2, 3, 1]
  ```

- `rotate(value: String, amount: Int) -> String`

  Rotate a finite list or string to the left.

  **Parameters**

  - `value` — Value to rotate.
  - `amount` — Signed rotation amount.

  **Returns:** The rotated value.

  **Example**

  ```vlnc
  "abc" 1 rotate
  ```
  Result:

  ```text
  bca
  ```


**Parameters**

- `value` — Value to rotate.
- `amount` — Signed rotation amount.

**Returns:** The rotated value.

**Example**

```vlnc
"abcd" 1 rotate
```
Result:

```text
bcda
```

### `sum`

Add every number in a list.

**Overloads**

- `sum(values: Number+) -> Number`

  **Example**

  ```vlnc
  [1, 2, 3] sum
  ```
  Result:

  ```text
  6
  ```

- `sum(values: Real+) -> Real`

  **Example**

  ```vlnc
  [1, 2, 3] sum
  ```
  Result:

  ```text
  6
  ```

- `sum(values: Int+) -> Int`

  **Example**

  ```vlnc
  [1, 2, 3] sum
  ```
  Result:

  ```text
  6
  ```


**Parameters**

- `values` — Numbers to add.

**Returns:** The total, or zero for an empty list.

**Example**

```vlnc
[1, 2, 3] sum
```
Result:

```text
6
```

### `take`

Return at most the first requested number of list items.

**Overloads**

- `take(values: Item+, count: Int) -> #-infinite Item+`

  **Example**

  ```vlnc
  [1, 2, 3] 2 take
  ```
  Result:

  ```text
  [1, 2]
  ```


**Parameters**

- `values` — Input list.
- `count` — Non-negative number of items to retain.

**Returns:** A list containing the selected prefix.

**Example**

```vlnc
[1, 2, 3] 2 take
```
Result:

```text
[1, 2]
```

### `update`

Return a copy of an iterable with an indexed selection replaced.

**Overloads**

- `update(iterable: String, index: Int+, value: String) -> String`

  Return a copy of an iterable with an indexed selection replaced.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `value` — Replacement value or selection-shaped replacement.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  update("abc", [0, 2], "XY")
  ```
  Result:

  ```text
  XbY
  ```

- `update(iterable: String, index: Int, value: String) -> String`

  Return a copy of an iterable with an indexed selection replaced.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `value` — Replacement value or selection-shaped replacement.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  update("abc", 1, "X")
  ```
  Result:

  ```text
  aXc
  ```

- `update(iterable: Item+, index: Int+, value: Item+) -> Item+`

  Return a copy of an iterable with an indexed selection replaced.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `value` — Replacement value or selection-shaped replacement.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  update([1, 2, 3], [0, 2], [8, 9])
  ```
  Result:

  ```text
  [8, 2, 9]
  ```

- `update(iterable: Item+, index: Int+, value: Item) -> Item+`

  Return a copy of an iterable with an indexed selection replaced.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `value` — Replacement value or selection-shaped replacement.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  update([1, 2, 3], [0, 2], 9)
  ```
  Result:

  ```text
  [9, 2, 9]
  ```

- `update(iterable: Item+, index: Int, value: Item) -> Item+`

  Return a copy of an iterable with an indexed selection replaced.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `value` — Replacement value or selection-shaped replacement.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  update([1, 2, 3], 1, 9)
  ```
  Result:

  ```text
  [1, 9, 3]
  ```

- `update(iterable: Item+$n, index: {Int?...}, value: Item+$m) -> Item+$n`

  Return a copy of an iterable with an indexed selection replaced.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `value` — Replacement value or selection-shaped replacement.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  update([[1, 2], [3, 4]], {0}, [8, 9])
  ```
  Result:

  ```text
  [[8, 9], [3, 4]]
  ```

- `update(iterable: Item*, index: (Int?)+, value: Replacement) -> Item*`

  Return a copy of an iterable with an indexed selection replaced.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `value` — Replacement value or selection-shaped replacement.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  update([[1, 2], [3, 4]], [\None, 0], [8, 9])
  ```
  Result:

  ```text
  [[8, 2], [9, 4]]
  ```


**Parameters**

- `iterable` — Input list or string.
- `index` — An index or Int+ selection.
- `value` — Replacement value or selection-shaped replacement.

**Returns:** The reconstructed iterable; the input binding is unchanged.

### `updateBy`

Return a copy of an iterable after applying a function to an indexed selection.

**Overloads**

- `updateBy(iterable: String, index: Int+, function: Function[String -> String]) -> String`

  Return a copy of an iterable after applying a function to an indexed selection.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `function` — Unary function applied once to the indexed value or selection.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  updateBy("abc", [0, 2], fn (s: String) => $s 1 rotate end)
  ```
  Result:

  ```text
  cba
  ```

- `updateBy(iterable: String, index: Int, function: Function[String -> String]) -> String`

  Return a copy of an iterable after applying a function to an indexed selection.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `function` — Unary function applied once to the indexed value or selection.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  updateBy("abc", 1, fn (s: String) => "X" end)
  ```
  Result:

  ```text
  aXc
  ```

- `updateBy(iterable: Item+, index: Int+, function: Function[Item+ -> Item+]) -> Item+`

  Return a copy of an iterable after applying a function to an indexed selection.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `function` — Unary function applied once to the indexed value or selection.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  updateBy([1, 2, 3], [0, 2], fn => double end)
  ```
  Result:

  ```text
  [[2, 2, 3], [1, 2, 6]]
  ```

- `updateBy(iterable: Item+, index: Int, function: Function[Item -> Item]) -> Item+`

  Return a copy of an iterable after applying a function to an indexed selection.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `function` — Unary function applied once to the indexed value or selection.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  updateBy([1, 2, 3], 1, fn => double end)
  ```
  Result:

  ```text
  [1, 4, 3]
  ```

- `updateBy(iterable: Item+$n, index: {Int?...}, function: Function[Item+$m -> Item+$m]) -> Item+$n`

  Return a copy of an iterable after applying a function to an indexed selection.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `function` — Unary function applied once to the indexed value or selection.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  updateBy([[1, 2], [3, 4]], {0}, fn (xs: Int+) => $xs reverse end)
  ```
  Result:

  ```text
  [[2, 1], [3, 4]]
  ```

- `updateBy(iterable: Item*, index: (Int?)+, function: Function[Item* -> Item*]) -> Item*`

  Return a copy of an iterable after applying a function to an indexed selection.

  **Parameters**

  - `iterable` — Input list or string.
  - `index` — An index or Int+ selection.
  - `function` — Unary function applied once to the indexed value or selection.

  **Returns:** The reconstructed iterable; the input binding is unchanged.

  **Example**

  ```vlnc
  updateBy([[1, 2], [3, 4]], [\None, 0], fn (xs) => $xs 1 rotate end)
  ```
  Result:

  ```text
  [[3, 2], [1, 4]]
  ```


**Parameters**

- `iterable` — Input list or string.
- `index` — An index or Int+ selection.
- `function` — Unary function applied once to the indexed value or selection.

**Returns:** The reconstructed iterable; the input binding is unchanged.

## Built-ins — Comparison

### `!=`

Test whether two numbers or strings differ.

**Overloads**

- `!=(left: String, right: String) -> #boolean Int`

  **Example**

  ```vlnc
  2 3 !=
  ```
  Result:

  ```text
  1
  ```

- `!=(left: Number, right: Number) -> #boolean Int`

  **Example**

  ```vlnc
  2 3 !=
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `left` — First value.
- `right` — Second value.

**Returns:** A Boolean number that is true when the values differ.

**Example**

```vlnc
2 3 !=
```
Result:

```text
1
```

### `<`

Test whether the left number is less than the right number.

**Overloads**

- `<(left: Number, right: Number) -> #boolean Int`

  Test whether the left number is less than the right number.

  **Parameters**

  - `left` — First number.
  - `right` — Second number.

  **Returns:** A Boolean comparison result.

  **Example**

  ```vlnc
  2 3 <
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `left` — First number.
- `right` — Second number.

**Returns:** A Boolean comparison result.

### `<=`

Test whether the left number is less than or equal to the right number.

**Overloads**

- `<=(left: Number, right: Number) -> #boolean Int`

  Test whether the left number is less than or equal to the right number.

  **Parameters**

  - `left` — First number.
  - `right` — Second number.

  **Returns:** A Boolean comparison result.

  **Example**

  ```vlnc
  3 3 <=
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `left` — First number.
- `right` — Second number.

**Returns:** A Boolean comparison result.

### `==`

Test two numbers or two strings for equality.

**Overloads**

- `==(left: String, right: String) -> #boolean Int`

  Test two numbers or two strings for equality.

  **Parameters**

  - `left` — First value.
  - `right` — Second value.

  **Returns:** A Boolean equality result.

  **Example**

  ```vlnc
  "cat" "dog" ==
  ```
  Result:

  ```text
  0
  ```

- `==(left: Number, right: Number) -> #boolean Int`

  Test two numbers or two strings for equality.

  **Parameters**

  - `left` — First value.
  - `right` — Second value.

  **Returns:** A Boolean equality result.

  **Example**

  ```vlnc
  3 3 ==
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `left` — First value.
- `right` — Second value.

**Returns:** A Boolean equality result.

### `===`

Test any two values for structural equality.

**Overloads**

- `===(left: T, right: T) -> #boolean Int`

  Test any two values for structural equality.

  **Parameters**

  - `left` — First value.
  - `right` — Second value.

  **Returns:** A Boolean equality result.

  **Example**

  ```vlnc
  [1, 2] [1, 2] ===
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `left` — First value.
- `right` — Second value.

**Returns:** A Boolean equality result.

### `>`

Test whether the left number is greater than the right number.

**Overloads**

- `>(left: Number, right: Number) -> #boolean Int`

  Test whether the left number is greater than the right number.

  **Parameters**

  - `left` — First number.
  - `right` — Second number.

  **Returns:** A Boolean comparison result.

  **Example**

  ```vlnc
  3 2 >
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `left` — First number.
- `right` — Second number.

**Returns:** A Boolean comparison result.

### `>=`

Test whether the left number is greater than or equal to the right number.

**Overloads**

- `>=(left: Number, right: Number) -> #boolean Int`

  Test whether the left number is greater than or equal to the right number.

  **Parameters**

  - `left` — First number.
  - `right` — Second number.

  **Returns:** A Boolean comparison result.

  **Example**

  ```vlnc
  3 3 >=
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `left` — First number.
- `right` — Second number.

**Returns:** A Boolean comparison result.

### `in`

Test whether a value occurs in a collection or string.

**Overloads**

- `in(needle: Item, haystack: Item+) -> #boolean Int`

  Test whether a value occurs in a collection or string.

  **Parameters**

  - `needle` — Value to find.
  - `haystack` — Value to search.

  **Returns:** A Boolean membership result.

  **Example**

  ```vlnc
  2 [1, 2, 3] in
  ```
  Result:

  ```text
  1
  ```

- `in(needle: String, haystack: String) -> #boolean Int`

  Test whether a value occurs in a collection or string.

  **Parameters**

  - `needle` — Value to find.
  - `haystack` — Value to search.

  **Returns:** A Boolean membership result.

  **Example**

  ```vlnc
  "bc" "abcd" in
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `needle` — Value to find.
- `haystack` — Value to search.

**Returns:** A Boolean membership result.

### `inRange`

Test whether a number lies in a half-open interval.

**Overloads**

- `inRange(value: Number, start: Number, stop: Number) -> #boolean Int`

  **Example**

  ```vlnc
  3 1 5 inRange
  ```
  Result:

  ```text
  1
  ```


The start is included and the stop is excluded.

**Parameters**

- `value` — Number to test, normally supplied from the stack.
- `start` — Inclusive lower bound.
- `stop` — Exclusive upper bound.

**Returns:** A Boolean number indicating whether start <= value < stop.

**Example**

```vlnc
3 1 5 inRange
```
Result:

```text
1
```

### `positive?`

Test whether a number is greater than zero.

**Overloads**

- `positive?(value: Real) -> #boolean Int`

  **Example**

  ```vlnc
  0 positive?
  ```
  Result:

  ```text
  0
  ```


**Parameters**

- `value` — Number to test.

**Returns:** `true` when the number is positive; otherwise `false`.

**Example**

```vlnc
0 positive?
```
Result:

```text
0
```

## Built-ins — Concurrency

### `get`

Read the latest committed immutable snapshot from a State[T].

**Overloads**

- `get(state: State[T]) -> T`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(3)
get $s
  ```
  Result:

  ```text
  3
  ```


**Parameters**

- `state` — Shared process-local State handle.

**Returns:** An owned snapshot of T.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(3)
get $s
```
Result:

```text
3
```

### `modify`

Apply a resumable, atomic, non-waiting transformation to State[T].

**Overloads**

- `modify(state: State[T], transformation: Function[T -> T]) -> ()`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(1)
$s modify: (1 +)
get $s
  ```
  Result:

  ```text
  2
  ```


**Parameters**

- `state` — Shared State handle.
- `transformation` — Function taking latest T and returning new T.

**Returns:** No stack outputs.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(1)
$s modify: (1 +)
get $s
```
Result:

```text
2
```

### `modifyWith`

Atomically publish a State[T] replacement and return a separate R.

**Overloads**

- `modifyWith(state: State[T], transformation: Function[T -> T, R]) -> R`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(1)
$s modifyWith: fn(x:Int)->Int,Int =>
  $x 1 +
  $x
end
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `state` — Shared State handle.
- `transformation` — Function returning two stack values: T and R.

**Returns:** The callback's second output R.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(1)
$s modifyWith: fn(x:Int)->Int,Int =>
  $x 1 +
  $x
end
```
Result:

```text
1
```

### `set`

Atomically replace State[T] with an already-evaluated value.

**Overloads**

- `set(state: State[T], replacement: T) -> ()`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(1)
$s set 10
get $s
  ```
  Result:

  ```text
  10
  ```


**Parameters**

- `state` — Shared State handle.
- `replacement` — A duplicable, task-safe T evaluated before the call.

**Returns:** No stack outputs.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(1)
$s set 10
get $s
```
Result:

```text
10
```

## Built-ins — Conversions

### `to`

Convert a value to the requested target type.

**Overloads**

- `to(value: String) -> Int`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: String) -> Real`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  "2.5" to[Real]
  ```
  Result:

  ```text
  2.5
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: String) -> Number`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  "2.5" to[Number]
  ```
  Result:

  ```text
  2.5
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Number) -> Number`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: Real) -> Number`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: Real) -> Real`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: Int) -> Number`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: Int) -> Real`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: Int) -> Int`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: Number) -> Int`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: Real) -> Int`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: Number) -> Real`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: T) -> String`

  **Example**

  ```vlnc
  "42" to[Int]
  ```
  Result:

  ```text
  42
  ```

- `to(value: Int) -> &char`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&char]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int) -> &short`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&short]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int) -> &int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&int]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int) -> &long`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&long]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int) -> &longlong`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&longlong]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int) -> &unsignedchar`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedchar]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int) -> &unsignedshort`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedshort]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int) -> &unsignedint`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedint]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int) -> &unsignedlong`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedlong]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int) -> &unsignedlonglong`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedlonglong]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Real) -> &float`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&float]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Real) -> &double`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&double]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: String) -> &CString`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  "hello" to[&CString]
  ```
  Create a checked native scalar of the selected C type.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &char) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&char] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &short) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&short] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &int) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&int] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &long) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&long] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &longlong) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&longlong] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedchar) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedchar] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedshort) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedshort] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedint) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedint] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedlong) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedlong] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedlonglong) -> Int`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&unsignedlonglong] | to[Int]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &float) -> Real`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&float] | to[Real]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &double) -> Real`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  3 to[&double] | to[Real]
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &char+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&char+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &char+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&char+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &short+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&short+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &short+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&short+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&int+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &int+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&int+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &long+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&long+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &long+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&long+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &longlong+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&longlong+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &longlong+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&longlong+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &unsignedchar+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedchar+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedchar+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedchar+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &unsignedshort+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedshort+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedshort+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedshort+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &unsignedint+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedint+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedint+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedint+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &unsignedlong+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedlong+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedlong+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedlong+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Int+) -> &unsignedlonglong+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedlonglong+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &unsignedlonglong+) -> Int+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&unsignedlonglong+] | to[Int+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Real+) -> &float+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&float+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &float+) -> Real+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&float+] | to[Real+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: Real+) -> &double+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&double+]
  ```
  Copy a finite numeric list into a contiguous native buffer.

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &double+) -> Real+`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  [1, 2, 3] to[&double+] | to[Real+]
  ```
  Result:

  ```text
  [1, 2, 3]
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

- `to(value: &CString) -> String`

  Convert a value to the requested target type.

  **Parameters**

  - `value` — The value to convert.

  **Returns:** The value represented as the requested target type.

  **Example**

  ```vlnc
  "hello" to[&CString] | to[String]
  ```
  Result:

  ```text
  hello
  ```

  **Notes**

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.


**Parameters**

- `value` — The value to convert.

**Returns:** The value represented as the requested target type.

**Example**

```vlnc
"42" to[Int]
```
Result:

```text
42
```

**Notes**

- Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. Numeric types convert to Int, Real, and Number without losing precision; narrowing fails with `ConversionFault` if the value cannot fit exactly. Conversions terminate implicit element chains. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

## Built-ins — Errors

### `AlreadyExistsError`

Construct a AlreadyExistsError recoverable error value.

**Overloads**

- `AlreadyExistsError(message: String) -> AlreadyExistsError`

  **Example**

  ```vlnc
  AlreadyExistsError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `AlreadyExistsError` value containing the message.

**Example**

```vlnc
AlreadyExistsError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `CancelledError`

Construct a CancelledError recoverable error value.

**Overloads**

- `CancelledError(message: String) -> CancelledError`

  **Example**

  ```vlnc
  CancelledError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `CancelledError` value containing the message.

**Example**

```vlnc
CancelledError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `ClosedError`

Construct a ClosedError recoverable error value.

**Overloads**

- `ClosedError(message: String) -> ClosedError`

  **Example**

  ```vlnc
  ClosedError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ClosedError` value containing the message.

**Example**

```vlnc
ClosedError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `DivisionByZeroError`

Construct a DivisionByZeroError recoverable error value.

**Overloads**

- `DivisionByZeroError(message: String) -> DivisionByZeroError`

  **Example**

  ```vlnc
  DivisionByZeroError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `DivisionByZeroError` value containing the message.

**Example**

```vlnc
DivisionByZeroError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `Error`

Construct a Error recoverable error value.

**Overloads**

- `Error(message: String) -> Error`

  **Example**

  ```vlnc
  Error("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `Error` value containing the message.

**Example**

```vlnc
Error("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `IndexError`

Construct a IndexError recoverable error value.

**Overloads**

- `IndexError(message: String) -> IndexError`

  **Example**

  ```vlnc
  IndexError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `IndexError` value containing the message.

**Example**

```vlnc
IndexError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `IOError`

Construct a IOError recoverable error value.

**Overloads**

- `IOError(message: String) -> IOError`

  **Example**

  ```vlnc
  IOError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `IOError` value containing the message.

**Example**

```vlnc
IOError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `KeyError`

Construct a KeyError recoverable error value.

**Overloads**

- `KeyError(message: String) -> KeyError`

  **Example**

  ```vlnc
  KeyError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `KeyError` value containing the message.

**Example**

```vlnc
KeyError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `NotFoundError`

Construct a NotFoundError recoverable error value.

**Overloads**

- `NotFoundError(message: String) -> NotFoundError`

  **Example**

  ```vlnc
  NotFoundError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `NotFoundError` value containing the message.

**Example**

```vlnc
NotFoundError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `ParseError`

Construct a ParseError recoverable error value.

**Overloads**

- `ParseError(message: String) -> ParseError`

  **Example**

  ```vlnc
  ParseError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ParseError` value containing the message.

**Example**

```vlnc
ParseError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `PermissionError`

Construct a PermissionError recoverable error value.

**Overloads**

- `PermissionError(message: String) -> PermissionError`

  **Example**

  ```vlnc
  PermissionError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `PermissionError` value containing the message.

**Example**

```vlnc
PermissionError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `RangeError`

Construct a RangeError recoverable error value.

**Overloads**

- `RangeError(message: String) -> RangeError`

  **Example**

  ```vlnc
  RangeError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `RangeError` value containing the message.

**Example**

```vlnc
RangeError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `ShapeError`

Construct a ShapeError recoverable error value.

**Overloads**

- `ShapeError(message: String) -> ShapeError`

  **Example**

  ```vlnc
  ShapeError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ShapeError` value containing the message.

**Example**

```vlnc
ShapeError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `StateError`

Construct a StateError recoverable error value.

**Overloads**

- `StateError(message: String) -> StateError`

  **Example**

  ```vlnc
  StateError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `StateError` value containing the message.

**Example**

```vlnc
StateError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `TimeoutError`

Construct a TimeoutError recoverable error value.

**Overloads**

- `TimeoutError(message: String) -> TimeoutError`

  **Example**

  ```vlnc
  TimeoutError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `TimeoutError` value containing the message.

**Example**

```vlnc
TimeoutError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

### `ValueError`

Construct a ValueError recoverable error value.

**Overloads**

- `ValueError(message: String) -> ValueError`

  **Example**

  ```vlnc
  ValueError("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ValueError` value containing the message.

**Example**

```vlnc
ValueError("operation failed")
```

**Notes**

- Error values represent recoverable failures.

## Built-ins — Errors and faults

### `message`

Read the message stored by an error or fault.

**Aliases:** `getMessage`

**Overloads**

- `message(failure: Fault) -> String`

  Read the message stored by an error or fault.

  **Parameters**

  - `failure` — An `Err` or `Fault` value.

  **Returns:** The failure message string.

  **Example**

  ```vlnc
  ValueFault("bad") message
  ```
  Result:

  ```text
  bad
  ```

- `message(failure: Err) -> String`

  Read the message stored by an error or fault.

  **Parameters**

  - `failure` — An `Err` or `Fault` value.

  **Returns:** The failure message string.

  **Example**

  ```vlnc
  ValueError("bad") message
  ```
  Result:

  ```text
  bad
  ```


**Parameters**

- `failure` — An `Err` or `Fault` value.

**Returns:** The failure message string.

**Example**

```vlnc
ValueError("bad value") message
```
Result:

```text
bad value
```

### `panic`

Abort normal execution by raising a fault value.

**Overloads**

- `panic(fault: F) -> Never where F: Fault`

  Abort normal execution by raising a fault value.

  **Parameters**

  - `fault` — Value implementing `Fault`.

  **Returns:** Never returns normally.

  **Example**

  ```vlnc
  ValueFault("invalid value") panic
  ```
  Raises ValueFault; execution does not return normally.


**Parameters**

- `fault` — Value implementing `Fault`.

**Returns:** Never returns normally.

## Built-ins — Faults

### `AlreadyExistsFault`

Construct a AlreadyExistsFault fault value.

**Overloads**

- `AlreadyExistsFault(message: String) -> AlreadyExistsFault`

  **Example**

  ```vlnc
  AlreadyExistsFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `AlreadyExistsFault` value containing the message.

**Example**

```vlnc
AlreadyExistsFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `CancelledFault`

Construct a CancelledFault fault value.

**Overloads**

- `CancelledFault(message: String) -> CancelledFault`

  **Example**

  ```vlnc
  CancelledFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `CancelledFault` value containing the message.

**Example**

```vlnc
CancelledFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `ClosedFault`

Construct a ClosedFault fault value.

**Overloads**

- `ClosedFault(message: String) -> ClosedFault`

  **Example**

  ```vlnc
  ClosedFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ClosedFault` value containing the message.

**Example**

```vlnc
ClosedFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `ConversionFault`

Construct a ConversionFault fault value.

**Overloads**

- `ConversionFault(message: String) -> ConversionFault`

  **Example**

  ```vlnc
  ConversionFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ConversionFault` value containing the message.

**Example**

```vlnc
ConversionFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `DivisionByZeroFault`

Construct a DivisionByZeroFault fault value.

**Overloads**

- `DivisionByZeroFault(message: String) -> DivisionByZeroFault`

  **Example**

  ```vlnc
  DivisionByZeroFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `DivisionByZeroFault` value containing the message.

**Example**

```vlnc
DivisionByZeroFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `DomainFault`

Construct a DomainFault fault value.

**Overloads**

- `DomainFault(message: String) -> DomainFault`

  **Example**

  ```vlnc
  DomainFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `DomainFault` value containing the message.

**Example**

```vlnc
DomainFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `DuplicationFault`

Construct a DuplicationFault fault value.

**Overloads**

- `DuplicationFault(message: String) -> DuplicationFault`

  **Example**

  ```vlnc
  DuplicationFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `DuplicationFault` value containing the message.

**Example**

```vlnc
DuplicationFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `IncompleteConstructionFault`

Construct a IncompleteConstructionFault fault value.

**Overloads**

- `IncompleteConstructionFault(message: String) -> IncompleteConstructionFault`

  **Example**

  ```vlnc
  IncompleteConstructionFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `IncompleteConstructionFault` value containing the message.

**Example**

```vlnc
IncompleteConstructionFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `IndexFault`

Construct a IndexFault fault value.

**Overloads**

- `IndexFault(message: String) -> IndexFault`

  **Example**

  ```vlnc
  IndexFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `IndexFault` value containing the message.

**Example**

```vlnc
IndexFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `IOFault`

Construct a IOFault fault value.

**Overloads**

- `IOFault(message: String) -> IOFault`

  **Example**

  ```vlnc
  IOFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `IOFault` value containing the message.

**Example**

```vlnc
IOFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `KeyFault`

Construct a KeyFault fault value.

**Overloads**

- `KeyFault(message: String) -> KeyFault`

  **Example**

  ```vlnc
  KeyFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `KeyFault` value containing the message.

**Example**

```vlnc
KeyFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `MustCallFault`

Construct a MustCallFault fault value.

**Overloads**

- `MustCallFault(message: String) -> MustCallFault`

  **Example**

  ```vlnc
  MustCallFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `MustCallFault` value containing the message.

**Example**

```vlnc
MustCallFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `NativeConstructionFault`

Construct a NativeConstructionFault fault value.

**Overloads**

- `NativeConstructionFault(message: String) -> NativeConstructionFault`

  **Example**

  ```vlnc
  NativeConstructionFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `NativeConstructionFault` value containing the message.

**Example**

```vlnc
NativeConstructionFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `NotFoundFault`

Construct a NotFoundFault fault value.

**Overloads**

- `NotFoundFault(message: String) -> NotFoundFault`

  **Example**

  ```vlnc
  NotFoundFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `NotFoundFault` value containing the message.

**Example**

```vlnc
NotFoundFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `ParseFault`

Construct a ParseFault fault value.

**Overloads**

- `ParseFault(message: String) -> ParseFault`

  **Example**

  ```vlnc
  ParseFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ParseFault` value containing the message.

**Example**

```vlnc
ParseFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `PermissionFault`

Construct a PermissionFault fault value.

**Overloads**

- `PermissionFault(message: String) -> PermissionFault`

  **Example**

  ```vlnc
  PermissionFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `PermissionFault` value containing the message.

**Example**

```vlnc
PermissionFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `RangeFault`

Construct a RangeFault fault value.

**Overloads**

- `RangeFault(message: String) -> RangeFault`

  **Example**

  ```vlnc
  RangeFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `RangeFault` value containing the message.

**Example**

```vlnc
RangeFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `ReflectionAccessFault`

Construct a ReflectionAccessFault fault value.

**Overloads**

- `ReflectionAccessFault(message: String) -> ReflectionAccessFault`

  **Example**

  ```vlnc
  ReflectionAccessFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ReflectionAccessFault` value containing the message.

**Example**

```vlnc
ReflectionAccessFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `ReflectionFieldFault`

Construct a ReflectionFieldFault fault value.

**Overloads**

- `ReflectionFieldFault(message: String) -> ReflectionFieldFault`

  **Example**

  ```vlnc
  ReflectionFieldFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ReflectionFieldFault` value containing the message.

**Example**

```vlnc
ReflectionFieldFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `ReflectionTypeFault`

Construct a ReflectionTypeFault fault value.

**Overloads**

- `ReflectionTypeFault(message: String) -> ReflectionTypeFault`

  **Example**

  ```vlnc
  ReflectionTypeFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ReflectionTypeFault` value containing the message.

**Example**

```vlnc
ReflectionTypeFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `RuntimeFault`

Construct a RuntimeFault fault value.

**Overloads**

- `RuntimeFault(message: String) -> RuntimeFault`

  **Example**

  ```vlnc
  RuntimeFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `RuntimeFault` value containing the message.

**Example**

```vlnc
RuntimeFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `ShapeFault`

Construct a ShapeFault fault value.

**Overloads**

- `ShapeFault(message: String) -> ShapeFault`

  **Example**

  ```vlnc
  ShapeFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ShapeFault` value containing the message.

**Example**

```vlnc
ShapeFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `SliceFault`

Construct a SliceFault fault value.

**Overloads**

- `SliceFault(message: String) -> SliceFault`

  **Example**

  ```vlnc
  SliceFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `SliceFault` value containing the message.

**Example**

```vlnc
SliceFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `StateFault`

Construct a StateFault fault value.

**Overloads**

- `StateFault(message: String) -> StateFault`

  **Example**

  ```vlnc
  StateFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `StateFault` value containing the message.

**Example**

```vlnc
StateFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `TimeoutFault`

Construct a TimeoutFault fault value.

**Overloads**

- `TimeoutFault(message: String) -> TimeoutFault`

  **Example**

  ```vlnc
  TimeoutFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `TimeoutFault` value containing the message.

**Example**

```vlnc
TimeoutFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `UnexpectedEOFFault`

Construct a UnexpectedEOFFault fault value.

**Overloads**

- `UnexpectedEOFFault(message: String) -> UnexpectedEOFFault`

  **Example**

  ```vlnc
  UnexpectedEOFFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `UnexpectedEOFFault` value containing the message.

**Example**

```vlnc
UnexpectedEOFFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `UnwrappedNoneFault`

Construct a UnwrappedNoneFault fault value.

**Overloads**

- `UnwrappedNoneFault(message: String) -> UnwrappedNoneFault`

  **Example**

  ```vlnc
  UnwrappedNoneFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `UnwrappedNoneFault` value containing the message.

**Example**

```vlnc
UnwrappedNoneFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `UnwrappedResultFault`

Construct a UnwrappedResultFault fault value.

**Overloads**

- `UnwrappedResultFault(message: String) -> UnwrappedResultFault`

  **Example**

  ```vlnc
  UnwrappedResultFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `UnwrappedResultFault` value containing the message.

**Example**

```vlnc
UnwrappedResultFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

### `ValueFault`

Construct a ValueFault fault value.

**Overloads**

- `ValueFault(message: String) -> ValueFault`

  **Example**

  ```vlnc
  ValueFault("operation failed")
  ```


**Parameters**

- `message` — Human-readable explanation of the failure.

**Returns:** A `ValueFault` value containing the message.

**Example**

```vlnc
ValueFault("operation failed")
```

**Notes**

- Fault values may be passed to `panic`.

## Built-ins — FFI

### `&char`

Construct an unchecked `&char` value.

**Overloads**

- `&char(value: Int) -> &char`

  **Example**

  ```vlnc
  FFI.&char(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&char` value.

**Example**

```vlnc
FFI.&char(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&CString`

Construct an unchecked `&CString` value.

**Overloads**

- `&CString(value: String) -> &CString`

  **Example**

  ```vlnc
  FFI.&CString("hello")
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&CString` value.

**Example**

```vlnc
FFI.&CString("hello")
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&double`

Construct an unchecked `&double` value.

**Overloads**

- `&double(value: Real) -> &double`

  **Example**

  ```vlnc
  FFI.&double(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&double` value.

**Example**

```vlnc
FFI.&double(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&float`

Construct an unchecked `&float` value.

**Overloads**

- `&float(value: Real) -> &float`

  **Example**

  ```vlnc
  FFI.&float(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&float` value.

**Example**

```vlnc
FFI.&float(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&int`

Construct an unchecked `&int` value.

**Overloads**

- `&int(value: Int) -> &int`

  **Example**

  ```vlnc
  FFI.&int(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&int` value.

**Example**

```vlnc
FFI.&int(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&long`

Construct an unchecked `&long` value.

**Overloads**

- `&long(value: Int) -> &long`

  **Example**

  ```vlnc
  FFI.&long(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&long` value.

**Example**

```vlnc
FFI.&long(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&longlong`

Construct an unchecked `&longlong` value.

**Overloads**

- `&longlong(value: Int) -> &longlong`

  **Example**

  ```vlnc
  FFI.&longlong(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&longlong` value.

**Example**

```vlnc
FFI.&longlong(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&short`

Construct an unchecked `&short` value.

**Overloads**

- `&short(value: Int) -> &short`

  **Example**

  ```vlnc
  FFI.&short(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&short` value.

**Example**

```vlnc
FFI.&short(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&unsignedchar`

Construct an unchecked `&unsignedchar` value.

**Overloads**

- `&unsignedchar(value: Int) -> &unsignedchar`

  **Example**

  ```vlnc
  FFI.&unsignedchar(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&unsignedchar` value.

**Example**

```vlnc
FFI.&unsignedchar(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&unsignedint`

Construct an unchecked `&unsignedint` value.

**Overloads**

- `&unsignedint(value: Int) -> &unsignedint`

  **Example**

  ```vlnc
  FFI.&unsignedint(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&unsignedint` value.

**Example**

```vlnc
FFI.&unsignedint(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&unsignedlong`

Construct an unchecked `&unsignedlong` value.

**Overloads**

- `&unsignedlong(value: Int) -> &unsignedlong`

  **Example**

  ```vlnc
  FFI.&unsignedlong(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&unsignedlong` value.

**Example**

```vlnc
FFI.&unsignedlong(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&unsignedlonglong`

Construct an unchecked `&unsignedlonglong` value.

**Overloads**

- `&unsignedlonglong(value: Int) -> &unsignedlonglong`

  **Example**

  ```vlnc
  FFI.&unsignedlonglong(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&unsignedlonglong` value.

**Example**

```vlnc
FFI.&unsignedlonglong(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

### `&unsignedshort`

Construct an unchecked `&unsignedshort` value.

**Overloads**

- `&unsignedshort(value: Int) -> &unsignedshort`

  **Example**

  ```vlnc
  FFI.&unsignedshort(3)
  ```
  Construct an unchecked native value; prefer checked `to` conversions.


**Parameters**

- `value` — Raw payload; no range validation is performed.

**Returns:** An `&unsignedshort` value.

**Example**

```vlnc
FFI.&unsignedshort(3)
```
Construct an unchecked native value; prefer checked `to` conversions.

## Built-ins — Functions

### `both`

Apply one callable to two consecutive groups of stack values.

**Overloads**

- `both(operation: Function) -> ()`

  **Example**

  ```vlnc
  2 3 both: double
  ```
  Result:

  ```text
  4 6
  ```


If the callable takes n inputs, both consumes two groups of n values.

The lower group is called first, followed by the upper group.

**Parameters**

- `operation` — Callable applied to each input group.

**Returns:** The first call's results followed by the second call's results.

**Example**

```vlnc
2 3 both: double
```
Result:

```text
4 6
```

### `call`

Invoke a callable value.

**Overloads**

- `call(operation: Function) -> ()`

  **Example**

  ```vlnc
  4 call: double
  ```
  Result:

  ```text
  8
  ```


**Parameters**

- `operation` — Callable to invoke.

**Returns:** The values returned by the selected callable overload.

**Example**

```vlnc
4 call: double
```
Result:

```text
8
```

### `dip`

Call a function beneath one temporarily held stack value.

**Overloads**

- `dip(input1: Function) -> ()`

  **Example**

  ```vlnc
  2 9 dip: double
  ```
  Result:

  ```text
  4 9
  ```


**Parameters**

- `held` — Value temporarily removed while the callable runs.
- `operation` — Callable to invoke on the remaining stack.

**Returns:** The callable results followed by the held value.

**Example**

```vlnc
2 9 dip: double
```
Result:

```text
4 9
```

### `fork`

Apply two callables to the same available inputs.

**Overloads**

- `fork(left: Function, right: Function) -> ()`

  Apply two callables to the same available inputs.

  **Parameters**

  - `left` — First callable.
  - `right` — Second callable.

  **Returns:** The results of the left callable followed by the results of the right callable.

  **Example**

  ```vlnc
  2 fork: (double, squared)
  ```
  Result:

  ```text
  4 4
  ```


**Parameters**

- `left` — First callable.
- `right` — Second callable.

**Returns:** The results of the left callable followed by the results of the right callable.

### `hook`

Fork functions f and g, then combine their results with h.

**Overloads**

- `hook(f: Function, g: Function, h: Function) -> ()`

  **Example**

  ```vlnc
  [19, 2, 3, 4, 13] hook: (sum, /, length)
  ```
  Result:

  ```text
  8.2
  ```


**Parameters**

- `f` — Callable applied to the lower input group.
- `g` — Callable applied to the upper input group.
- `h` — Callable that combines the results of f and g.

**Returns:** The combined results of f and g.

**Example**

```vlnc
[19, 2, 3, 4, 13] hook: (sum, /, length)
```
Result:

```text
8.2
```

### `peek`

Call a function while preserving the values it consumes.

**Overloads**

- `peek(operation: Function) -> ()`

  **Example**

  ```vlnc
  4 peek: double
  ```
  Result:

  ```text
  4 8
  ```


The callable receives its normal inputs, and those inputs remain beneath the callable's results.

**Parameters**

- `operation` — Callable to invoke.

**Returns:** The preserved inputs followed by the callable results.

**Example**

```vlnc
4 peek: double
```
Result:

```text
4 8
```

### `sequence`

Apply two callables to two distinct consecutive groups of stack values.

**Overloads**

- `sequence(lower: Function, upper: Function) -> ()`

  Apply two callables to two distinct consecutive groups of stack values.

  The first callable consumes the lower group and the second callable consumes the upper group.

  The callables may have different input and output arities.

  **Parameters**

  - `lower` — Callable applied to the lower input group.
  - `upper` — Callable applied to the upper input group.

  **Returns:** The lower callable's results followed by the upper callable's results.

  **Example**

  ```vlnc
  2 3 sequence: (double, squared)
  ```
  Result:

  ```text
  4 9
  ```


The first callable consumes the lower group and the second callable consumes the upper group.

The callables may have different input and output arities.

**Parameters**

- `lower` — Callable applied to the lower input group.
- `upper` — Callable applied to the upper input group.

**Returns:** The lower callable's results followed by the upper callable's results.

## Built-ins — Input and output

### `input`

Read one line of text from standard input.

**Overloads**

- `input(prompt: String) -> String`

  Read one line of text from standard input.

  **Parameters**

  - `prompt` — Prompt displayed before reading.

  **Returns:** The entered line without its trailing newline.

  **Example**

  ```vlnc
  "Name: " input
  ```
  Displays Name: and waits for a line from standard input.


**Parameters**

- `prompt` — Prompt displayed before reading.

**Returns:** The entered line without its trailing newline.

### `print`

Write a value without a trailing newline.

**Overloads**

- `print(value: T) -> ()`

  Write a value without a trailing newline.

  **Parameters**

  - `value` — Value to format and write.

  **Returns:** No stack values.

  **Example**

  ```vlnc
  "hello" print
  ```
  Writes hello without a newline and leaves no stack values.


**Parameters**

- `value` — Value to format and write.

**Returns:** No stack values.

### `println`

Write a value followed by a newline.

**Overloads**

- `println(value: T) -> ()`

  Write a value followed by a newline.

  **Parameters**

  - `value` — Value to format and write.

  **Returns:** No stack values.

  **Example**

  ```vlnc
  "hello" println
  ```
  Writes hello followed by a newline and leaves no stack values.


**Parameters**

- `value` — Value to format and write.

**Returns:** No stack values.

## Built-ins — Lists

### `filter`

Filter a list by a predicate

**Overloads**

- `filter(iterable: Item+, predicate: Function[Item -> #boolean Int]) -> Item+`

  **Example**

  ```vlnc
  [-1, 0, 2, 3] filter: positive?
  ```
  Result:

  ```text
  [2, 3]
  ```


**Parameters**

- `iterable` — The list to filter
- `predicate` — Callable that returns true for items to keep

**Returns:** The filtered list.

**Example**

```vlnc
[-1, 0, 2, 3] filter: positive?
```
Result:

```text
[2, 3]
```

### `reverse`

Reverse the order of a list of items

**Overloads**

- `reverse(iterable: Item+) -> Item+`

  **Example**

  ```vlnc
  [1, 2, 3] reverse
  ```
  Result:

  ```text
  [3, 2, 1]
  ```


**Parameters**

- `iterable` — The list to reverse

**Returns:** The reversed list.

**Example**

```vlnc
[1, 2, 3] reverse
```
Result:

```text
[3, 2, 1]
```

### `unpair`

Return the first two items of a finite list, raising if there are fewer than two.

**Overloads**

- `unpair(list: Item+) -> Item, Item`

  **Example**

  ```vlnc
  [10, 20] unpair
  ```
  Result:

  ```text
  10 20
  ```


**Parameters**

- `list` — The list to unpair

**Returns:** The two items of the list, pushed separately onto the stack.

**Example**

```vlnc
[10, 20] unpair
```
Result:

```text
10 20
```

## Built-ins — Mathematics

### `sqrt`

Compute the square root of a number.

**Overloads**

- `sqrt(value: Number) -> Number`

  **Example**

  ```vlnc
  9 sqrt
  ```
  Result:

  ```text
  3
  ```


**Parameters**

- `value` — Number to compute square root of.

**Returns:** The square root of the input number.

**Example**

```vlnc
9 sqrt
```
Result:

```text
3
```

## Built-ins — Optionals and results

### `&`

Continue an optional or result computation only when a value is present or successful.

**Overloads**

- `&(value: E, operation: Function[T -> U]) -> E where E: Err`

  Continue an optional or result computation only when a value is present or successful.

  **Parameters**

  - `value` — Optional, result, or recoverable error.
  - `operation` — Callable applied to the present or successful value.

  **Returns:** The transformed container, while empty or error values pass through unchanged.

  **Example**

  ```vlnc
  ValueError("bad") &: double
  ```
  Result:

  ```text
  ValueError{message: 'bad'}
  ```

- `&(value: Result[T, E], operation: Function[T -> U]) -> Result[U, E]`

  Continue an optional or result computation only when a value is present or successful.

  **Parameters**

  - `value` — Optional, result, or recoverable error.
  - `operation` — Callable applied to the present or successful value.

  **Returns:** The transformed container, while empty or error values pass through unchanged.

  **Example**

  ```vlnc
  OK(3) &: double
  ```
  Result:

  ```text
  OK[Number, Never]{value: 6}
  ```

- `&(value: T?, operation: Function[T -> U]) -> U?`

  Continue an optional or result computation only when a value is present or successful.

  **Parameters**

  - `value` — Optional, result, or recoverable error.
  - `operation` — Callable applied to the present or successful value.

  **Returns:** The transformed container, while empty or error values pass through unchanged.

  **Example**

  ```vlnc
  Some(3) &: double
  ```
  Result:

  ```text
  6
  ```


**Parameters**

- `value` — Optional, result, or recoverable error.
- `operation` — Callable applied to the present or successful value.

**Returns:** The transformed container, while empty or error values pass through unchanged.

**Example**

```vlnc
Some(3) &: double | ?!
```
Result:

```text
6
```

### `?`

Unwrap a present optional or successful result with propagation semantics.

**Overloads**

- `?(value: Result[T, E]) -> T`

  Unwrap a present optional or successful result with propagation semantics.

  **Parameters**

  - `value` — Optional or result to inspect.

  **Returns:** The contained value, or the original empty/error value for propagation.

  **Example**

  ```vlnc
  OK(3) ?
  ```
  Result:

  ```text
  3
  ```

- `?(value: T?) -> T`

  Unwrap a present optional or successful result with propagation semantics.

  **Parameters**

  - `value` — Optional or result to inspect.

  **Returns:** The contained value, or the original empty/error value for propagation.

  **Example**

  ```vlnc
  Some(3) ?
  ```
  Result:

  ```text
  3
  ```


**Parameters**

- `value` — Optional or result to inspect.

**Returns:** The contained value, or the original empty/error value for propagation.

### `?!`

Unwrap an optional or result and panic when no successful value exists.

**Overloads**

- `?!(value: Result[T, E]) -> T`

  Unwrap an optional or result and panic when no successful value exists.

  **Parameters**

  - `value` — Optional or result to unwrap.

  **Returns:** The contained value.

  **Example**

  ```vlnc
  OK(3) ?!
  ```
  Result:

  ```text
  3
  ```

  **Notes**

  - Panics with `UnwrappedNoneFault` or `UnwrappedResultFault` on failure.

- `?!(value: T?) -> T`

  **Example**

  ```vlnc
  Some(3) ?!
  ```
  Result:

  ```text
  3
  ```


**Parameters**

- `value` — Optional or result to unwrap.

**Returns:** The contained value.

**Example**

```vlnc
Some(3) ?!
```
Result:

```text
3
```

**Notes**

- Panics with `UnwrappedNoneFault` or `UnwrappedResultFault` on failure.

### `\None`

Push an empty optional value.

**Overloads**

- `\None() -> None`

  Push an empty optional value.

  **Returns:** The empty `None` optional.

  **Example**

  ```vlnc
  \None
  ```
  Result:

  ```text
  None{}
  ```


**Returns:** The empty `None` optional.

### `OK`

Wrap a successful value in a result.

**Overloads**

- `OK(value: T) -> OK[T]`

  **Example**

  ```vlnc
  OK(42) ?!
  ```
  Result:

  ```text
  42
  ```


**Parameters**

- `value` — Successful result value.

**Returns:** An `OK` result containing the value.

**Example**

```vlnc
OK(42) ?!
```
Result:

```text
42
```

### `or`

Choose a fallback string or optional value.

**Overloads**

- `or(value: String, fallback: String) -> String`

  **Example**

  ```vlnc
  "" "fallback" or
  ```
  Result:

  ```text
  fallback
  ```

- `or(value: T?, fallback: T) -> T`

  Choose a fallback string or optional value.

  **Parameters**

  - `value` — Preferred string or optional.
  - `fallback` — Value used when the preferred value is empty.

  **Returns:** The preferred non-empty value, otherwise the fallback.

  **Example**

  ```vlnc
  Some(3) 9 or
  ```
  Result:

  ```text
  Some[Int]{value: 3}
  ```


**Parameters**

- `value` — Preferred string or optional.
- `fallback` — Value used when the preferred value is empty.

**Returns:** The preferred non-empty value, otherwise the fallback.

**Example**

```vlnc
"" "fallback" or
```
Result:

```text
fallback
```

### `Some`

Wrap a present value in an optional.

**Overloads**

- `Some(value: T) -> Some[T]`

  **Example**

  ```vlnc
  Some(42) ?!
  ```
  Result:

  ```text
  42
  ```


**Parameters**

- `value` — Present optional value.

**Returns:** A `Some` optional containing the value.

**Example**

```vlnc
Some(42) ?!
```
Result:

```text
42
```

## Built-ins — Records

### `extend`

Extend an anonymous record with fields that are not already present.

**Overloads**

- `extend(record: record, fields: record) -> record`

  **Example**

  ```vlnc
  record{x => 1} record{y => 2} record.extend
  ```
  Result:

  ```text
  {"x" => 1, "y" => 2}
  ```


**Parameters**

- `record` — Base record.
- `fields` — New fields.

**Returns:** The extended anonymous record.

**Example**

```vlnc
record{x => 1} record{y => 2} record.extend
```
Result:

```text
{"x" => 1, "y" => 2}
```

### `merge`

Merge two anonymous records, preferring fields from the right record.

**Overloads**

- `merge(left: record, right: record) -> record`

  **Example**

  ```vlnc
  record{x => 1} record{x => 2} record.merge
  ```
  Result:

  ```text
  {"x" => 2}
  ```


**Parameters**

- `left` — Base record.
- `right` — Fields to merge.

**Returns:** A merged anonymous record.

**Example**

```vlnc
record{x => 1} record{x => 2} record.merge
```
Result:

```text
{"x" => 2}
```

## Built-ins — Reflection

### `constructRuntime?`

Reflect over existing compiler-declared or host-registered types: construct.

**Overloads**

- `constructRuntime?(arg1: RuntimeType, arg2: Dict[String, Dynamic]) -> Result[Dynamic, ConstructionError]`

  **Example**

  ```vlnc
  object LiveBox => public $value: Int end
unsafe =>
  $schema = lookupRuntimeType?("LiveBox") ?!
  $box = constructRuntime?($schema, dict{"value" => dynamicBox(8)}) ?!
  $new = writeRuntime?($box, "value", dynamicBox(9)) ?!
  dynamicUnbox?[Int](readRuntime?($new, "value") ?!) ?!
end
  ```


**Parameters**

- `arg1` — Checked runtime reflection argument.
- `arg2` — Checked runtime reflection argument.

**Returns:** A successful checked value or a named reflection error.

**Example**

```vlnc
object LiveBox => public $value: Int end
unsafe =>
  $schema = lookupRuntimeType?("LiveBox") ?!
  $box = constructRuntime?($schema, dict{"value" => dynamicBox(8)}) ?!
  $new = writeRuntime?($box, "value", dynamicBox(9)) ?!
  dynamicUnbox?[Int](readRuntime?($new, "value") ?!) ?!
end
```

### `constructRuntimeNative?`

Reflect over existing compiler-declared or host-registered types: native.

**Overloads**

- `constructRuntimeNative?(arg1: RuntimeType, arg2: Dynamic) -> Result[Dynamic, ConstructionError]`

  **Example**

  ```vlnc
  unsafe => constructRuntimeNative?(lookupRuntimeType?("&scalar") ?!, dynamicBox(17)) end
  ```


**Parameters**

- `arg1` — Checked runtime reflection argument.
- `arg2` — Checked runtime reflection argument.

**Returns:** A successful checked value or a named reflection error.

**Example**

```vlnc
unsafe => constructRuntimeNative?(lookupRuntimeType?("&scalar") ?!, dynamicBox(17)) end
```

### `dynamicBox`

Store a Valiance value behind a checked existential dynamic wrapper.

**Overloads**

- `dynamicBox(value: Value) -> Dynamic`

  **Example**

  ```vlnc
  dynamicBox(42)
  ```


**Parameters**

- `value` — The owned Valiance value.

**Returns:** A Dynamic box whose payload is not statically accessible.

**Example**

```vlnc
dynamicBox(42)
```

### `dynamicUnbox?`

Extract a Dynamic payload only when its reified value satisfies the requested type.

**Overloads**

- `dynamicUnbox?(value: Dynamic) -> Result[Value, ReflectionReadError]`

  **Example**

  ```vlnc
  dynamicUnbox?[Int](dynamicBox(42))
  ```


**Parameters**

- `value` — Dynamic box to check.

**Returns:** A typed result or ReflectionReadError on a type mismatch.

**Example**

```vlnc
dynamicUnbox?[Int](dynamicBox(42))
```

### `field?`

Look up a stored field dynamically, returning None when absent.

**Overloads**

- `field?(fields: Fields[Owner], name: String) -> Field[Owner]?`

  **Example**

  ```vlnc
  field?(fields[Int], "missing")
  ```
  Result:

  ```text
  None{}
  ```


**Parameters**

- `fields` — Declared field metadata.
- `name` — Field name.

**Returns:** An optional owner-bound field descriptor.

**Example**

```vlnc
field?(fields[Int], "missing")
```
Result:

```text
None{}
```

### `finish`

Consume a complete builder, panicking when fields are missing.

**Overloads**

- `finish(builder: Uninit[Owner]) -> Owner`

  **Example**

  ```vlnc
  object Empty => end
unsafe => uninit[Empty] | finish end
  ```
  Managed construction requires lexical unsafe.


**Parameters**

- `builder` — Incomplete managed or native storage.

**Returns:** The completed owner (or NativeConstructionFault).

**Example**

```vlnc
object Empty => end
unsafe => uninit[Empty] | finish end
```
Managed construction requires lexical unsafe.

### `finish?`

Consume a builder, returning a Result on missing fields.

**Overloads**

- `finish?(builder: Uninit[Owner]) -> Result[Owner, ConstructionError]`

  **Example**

  ```vlnc
  object Empty => end
unsafe => uninit[Empty] | finish? end
  ```
  Managed construction requires lexical unsafe.


**Parameters**

- `builder` — Incomplete managed or native storage.

**Returns:** The completed owner or ConstructionError.

**Example**

```vlnc
object Empty => end
unsafe => uninit[Empty] | finish? end
```
Managed construction requires lexical unsafe.

### `finished?`

Check stored-field completeness without consuming the builder.

**Overloads**

- `finished?(builder: Uninit[Owner]) -> #boolean Int`

  **Example**

  ```vlnc
  object Empty => end
unsafe => uninit[Empty] | finished? end
  ```
  Managed construction requires lexical unsafe.


**Parameters**

- `builder` — Incomplete managed or native storage.

**Returns:** Whether all stored fields are initialized.

**Example**

```vlnc
object Empty => end
unsafe => uninit[Empty] | finished? end
```
Managed construction requires lexical unsafe.

### `finishWith`

Finalize native opaque storage with an argument passed to its trusted factory.

**Overloads**

- `finishWith(builder: Uninit[Owner], argument: Argument) -> Owner`

  **Example**

  ```vlnc
  unsafe => finishWith(uninit[&scalar], 17) end
  ```


**Parameters**

- `builder` — Incomplete native storage.
- `argument` — Typed constructor argument.

**Returns:** The native owner, or a ConstructionError for finish?.

**Example**

```vlnc
unsafe => finishWith(uninit[&scalar], 17) end
```

### `finishWith?`

Finalize native opaque storage with an argument passed to its trusted factory.

**Overloads**

- `finishWith?(builder: Uninit[Owner], argument: Argument) -> Result[Owner, ConstructionError]`

  **Example**

  ```vlnc
  unsafe => finishWith?(uninit[&scalar], 17) end
  ```


**Parameters**

- `builder` — Incomplete native storage.
- `argument` — Typed constructor argument.

**Returns:** The native owner, or a ConstructionError for finish?.

**Example**

```vlnc
unsafe => finishWith?(uninit[&scalar], 17) end
```

### `lookupRuntimeType?`

Reflect over existing compiler-declared or host-registered types: lookup.

**Overloads**

- `lookupRuntimeType?(arg1: String) -> Result[RuntimeType, ConstructionError]`

  **Example**

  ```vlnc
  object Box => public $value: Int end
unsafe => lookupRuntimeType?("Box") end
  ```


**Parameters**

- `arg1` — Checked runtime reflection argument.

**Returns:** A successful checked value or a named reflection error.

**Example**

```vlnc
object Box => public $value: Int end
unsafe => lookupRuntimeType?("Box") end
```

### `read?`

Read a reflected field, returning a reflection error on denied or invalid access.

**Overloads**

- `read?(owner: Owner, field: Field[Owner]) -> Result[FieldValue, ReflectionReadError]`

  **Example**

  ```vlnc
  object Box => public $value: Int end
$box = Box(7)
read?($box, fields[Box]["value"])
  ```
  Result:

  ```text
  OK{value: 7}
  ```


**Parameters**

- `owner` — Owner to inspect; preserved by reconstruction.
- `field` — Owner-bound field descriptor.

**Returns:** A Result containing the value or the named reflection error.

**Example**

```vlnc
object Box => public $value: Int end
$box = Box(7)
read?($box, fields[Box]["value"])
```
Result:

```text
OK{value: 7}
```

### `readRuntime?`

Reflect over existing compiler-declared or host-registered types: read.

**Overloads**

- `readRuntime?(arg1: Dynamic, arg2: String) -> Result[Dynamic, ReflectionReadError]`

  **Example**

  ```vlnc
  object LiveBox => public $value: Int end
unsafe =>
  $schema = lookupRuntimeType?("LiveBox") ?!
  $box = constructRuntime?($schema, dict{"value" => dynamicBox(8)}) ?!
  $new = writeRuntime?($box, "value", dynamicBox(9)) ?!
  dynamicUnbox?[Int](readRuntime?($new, "value") ?!) ?!
end
  ```


**Parameters**

- `arg1` — Checked runtime reflection argument.
- `arg2` — Checked runtime reflection argument.

**Returns:** A successful checked value or a named reflection error.

**Example**

```vlnc
object LiveBox => public $value: Int end
unsafe =>
  $schema = lookupRuntimeType?("LiveBox") ?!
  $box = constructRuntime?($schema, dict{"value" => dynamicBox(8)}) ?!
  $new = writeRuntime?($box, "value", dynamicBox(9)) ?!
  dynamicUnbox?[Int](readRuntime?($new, "value") ?!) ?!
end
```

### `ReflectionReadError`

Construct a recoverable reflection error with its reason and message.

**Overloads**

- `ReflectionReadError(reason: String, message: String) -> ReflectionReadError`

  **Example**

  ```vlnc
  ReflectionReadError("AccessDenied", "private field")
  ```
  Construct an ordinary Err value with a named reflection reason.


**Parameters**

- `reason` — Named failure reason.
- `message` — Failure description.

**Returns:** A recoverable reflection error.

**Example**

```vlnc
ReflectionReadError("AccessDenied", "private field")
```
Construct an ordinary Err value with a named reflection reason.

### `ReflectionWriteError`

Construct a recoverable reflection error with its reason and message.

**Overloads**

- `ReflectionWriteError(reason: String, message: String) -> ReflectionWriteError`

  **Example**

  ```vlnc
  ReflectionWriteError("AccessDenied", "private field")
  ```
  Construct an ordinary Err value with a named reflection reason.


**Parameters**

- `reason` — Named failure reason.
- `message` — Failure description.

**Returns:** A recoverable reflection error.

**Example**

```vlnc
ReflectionWriteError("AccessDenied", "private field")
```
Construct an ordinary Err value with a named reflection reason.

### `runtimeFields?`

Reflect over existing compiler-declared or host-registered types: fields.

**Overloads**

- `runtimeFields?(arg1: RuntimeType) -> Result[RuntimeFieldSpec+, ReflectionReadError]`

  **Example**

  ```vlnc
  object LiveBox => public $value: Int end
unsafe =>
  $schema = lookupRuntimeType?("LiveBox") ?!
  $box = constructRuntime?($schema, dict{"value" => dynamicBox(8)}) ?!
  $new = writeRuntime?($box, "value", dynamicBox(9)) ?!
  dynamicUnbox?[Int](readRuntime?($new, "value") ?!) ?!
end
  ```


**Parameters**

- `arg1` — Checked runtime reflection argument.

**Returns:** A successful checked value or a named reflection error.

**Example**

```vlnc
object LiveBox => public $value: Int end
unsafe =>
  $schema = lookupRuntimeType?("LiveBox") ?!
  $box = constructRuntime?($schema, dict{"value" => dynamicBox(8)}) ?!
  $new = writeRuntime?($box, "value", dynamicBox(9)) ?!
  dynamicUnbox?[Int](readRuntime?($new, "value") ?!) ?!
end
```

### `runtimeTypeOf?`

Reflect over existing compiler-declared or host-registered types: typeOf.

**Overloads**

- `runtimeTypeOf?(arg1: Dynamic) -> Result[RuntimeType, ReflectionReadError]`

  **Example**

  ```vlnc
  object Box => public $value: Int end
unsafe => runtimeTypeOf?(dynamicBox(Box(42))) end
  ```


**Parameters**

- `arg1` — Checked runtime reflection argument.

**Returns:** A successful checked value or a named reflection error.

**Example**

```vlnc
object Box => public $value: Int end
unsafe => runtimeTypeOf?(dynamicBox(Box(42))) end
```

### `uninit`

Allocate empty managed or native storage under lexical unsafe. Native storage requires a registered trusted factory at finalization.

**Overloads**

- `uninit() -> Uninit[Owner]`

  **Example**

  ```vlnc
  object Empty => end
unsafe => uninit[Empty] end
  ```
  Managed construction requires lexical unsafe.


**Returns:** An empty Uninit owner.

**Example**

```vlnc
object Empty => end
unsafe => uninit[Empty] end
```
Managed construction requires lexical unsafe.

### `write?`

Reconstruct an independent owner, returning a reflection error on invalid access or replacement.

**Overloads**

- `write?(owner: Owner, field: Field[Owner], value: Value) -> Result[Owner, ReflectionWriteError]`

  **Example**

  ```vlnc
  object Box => public $value: Int end
$box = Box(7)
$updated = write?($box, fields[Box]["value"], 8) ?!
$updated.value
$box.value
  ```
  Result:

  ```text
  8 7
  ```


**Parameters**

- `owner` — Owner to inspect; preserved by reconstruction.
- `field` — Owner-bound field descriptor.
- `value` — Replacement without implicit conversion.

**Returns:** A Result containing the value or the named reflection error.

**Example**

```vlnc
object Box => public $value: Int end
$box = Box(7)
$updated = write?($box, fields[Box]["value"], 8) ?!
$updated.value
$box.value
```
Result:

```text
8 7
```

### `writeRuntime?`

Reflect over existing compiler-declared or host-registered types: write.

**Overloads**

- `writeRuntime?(arg1: Dynamic, arg2: String, arg3: Dynamic) -> Result[Dynamic, ReflectionWriteError]`

  **Example**

  ```vlnc
  object LiveBox => public $value: Int end
unsafe =>
  $schema = lookupRuntimeType?("LiveBox") ?!
  $box = constructRuntime?($schema, dict{"value" => dynamicBox(8)}) ?!
  $new = writeRuntime?($box, "value", dynamicBox(9)) ?!
  dynamicUnbox?[Int](readRuntime?($new, "value") ?!) ?!
end
  ```


**Parameters**

- `arg1` — Checked runtime reflection argument.
- `arg2` — Checked runtime reflection argument.
- `arg3` — Checked runtime reflection argument.

**Returns:** A successful checked value or a named reflection error.

**Example**

```vlnc
object LiveBox => public $value: Int end
unsafe =>
  $schema = lookupRuntimeType?("LiveBox") ?!
  $box = constructRuntime?($schema, dict{"value" => dynamicBox(8)}) ?!
  $new = writeRuntime?($box, "value", dynamicBox(9)) ?!
  dynamicUnbox?[Int](readRuntime?($new, "value") ?!) ?!
end
```

## Built-ins — Stack

### `dup`

Duplicate the top stack value.

**Overloads**

- `dup(value: T) -> T, T`

  **Example**

  ```vlnc
  10 dup
  ```
  Result:

  ```text
  10 10
  ```


**Parameters**

- `value` — Value to duplicate.

**Returns:** Two copies of the input value.

**Example**

```vlnc
10 dup
```
Result:

```text
10 10
```

### `pop`

Discard a value from the stack

**Overloads**

- `pop(value: Item) -> ()`

  **Example**

  ```vlnc
  1 2 pop
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `value` — The value to discard

**Returns:** Nothing.

**Example**

```vlnc
1 2 pop
```
Result:

```text
1
```

### `swap`

Exchange the two topmost stack values.

**Overloads**

- `swap(lower: A, upper: B) -> B, A`

  **Example**

  ```vlnc
  1 2 swap
  ```
  Result:

  ```text
  2 1
  ```


**Parameters**

- `lower` — Value immediately below the top of the stack.
- `upper` — Value at the top of the stack.

**Returns:** The same values in reverse stack order.

**Example**

```vlnc
1 2 swap
```
Result:

```text
2 1
```

### `top`

Return the top stack value unchanged.

**Overloads**

- `top(value: T) -> T`

  **Example**

  ```vlnc
  7 top
  ```
  Result:

  ```text
  7
  ```


**Parameters**

- `value` — Value at the top of the stack.

**Returns:** The input value.

**Example**

```vlnc
7 top
```
Result:

```text
7
```

## Built-ins — Strings

### `fromCharcode`

Convert an integer Unicode code point to a one-character string.

**Aliases:** `fromCharCode`

**Overloads**

- `fromCharcode(codepoint: Int) -> String`

  **Example**

  ```vlnc
  65 fromCharcode
  ```
  Result:

  ```text
  A
  ```


**Parameters**

- `codepoint` — Unicode scalar value.

**Returns:** The corresponding character.

**Example**

```vlnc
65 fromCharcode
```
Result:

```text
A
```

### `join`

Join a list of strings with a separator.

**Overloads**

- `join(values: String+, separator: String) -> String`

  **Example**

  ```vlnc
  ["a", "b"] ", " join
  ```
  Result:

  ```text
  a, b
  ```


**Parameters**

- `values` — Strings to join.
- `separator` — Text inserted between adjacent strings.

**Returns:** The joined string.

**Example**

```vlnc
["a", "b"] ", " join
```
Result:

```text
a, b
```

### `numeric?`

Test whether a string is a valid base-ten integer.

**Overloads**

- `numeric?(value: String) -> #boolean Int`

  **Example**

  ```vlnc
  "123" numeric?
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `value` — String to inspect.

**Returns:** A Boolean result.

**Example**

```vlnc
"123" numeric?
```
Result:

```text
1
```

### `split`

Split a string around a separator.

**Overloads**

- `split(value: String, on: String) -> String+`

  **Example**

  ```vlnc
  "a,b,c" "," split
  ```
  Result:

  ```text
  ['a', 'b', 'c']
  ```


**Parameters**

- `value` — String to split.
- `separator` — Separator text.

**Returns:** A list of string segments.

**Example**

```vlnc
"a,b,c" "," split
```
Result:

```text
['a', 'b', 'c']
```

## Language — Concurrency

### `Channel`

Create an invariant unbuffered or bounded FIFO channel.

**Overloads**

- `Channel[T] | Int Channel[T] -> Channel[T]`

  Create an invariant unbuffered or bounded FIFO channel.

  **Example**

  ```vlnc
  $channel = 1 Channel[Int]
$channel 7 send
$channel receive
  ```


**Example**

```vlnc
$channel = 1 Channel[Int]
$channel 7 send
$channel receive
```

### `close`

Close a channel; buffered values remain drainable.

**Overloads**

- `close(Channel[T]) ->`

  Close a channel; buffered values remain drainable.

  **Example**

  ```vlnc
  $channel = Channel[Int]
$channel close
$channel receive
  ```


**Example**

```vlnc
$channel = Channel[Int]
$channel close
$channel receive
```

### `concurrent`

Run a structured task scope and join its children.

**Overloads**

- `concurrent => ... end`

  Run a structured task scope and join its children.

  **Example**

  ```vlnc
  concurrent => fn => 42 end | spawn | wait end
  ```


**Example**

```vlnc
concurrent => fn => 42 end | spawn | wait end
```

### `receive`

Receive Value(value) or Closed without conflating None.

**Overloads**

- `receive(Channel[T]) -> Receive[T]`

  Receive Value(value) or Closed without conflating None.

  **Example**

  ```vlnc
  $channel = 1 Channel[Int]
$channel 7 send
$channel receive
  ```


**Example**

```vlnc
$channel = 1 Channel[Int]
$channel 7 send
$channel receive
```

### `Receive.Closed`

A channel receive reporting closed and fully drained.

**Overloads**

- `Receive.Closed`

  A channel receive reporting closed and fully drained.

  **Example**

  ```vlnc
  $channel = Channel[Int]
$channel close
$channel receive
  ```


**Example**

```vlnc
$channel = Channel[Int]
$channel close
$channel receive
```

### `Receive.Value`

A channel receive containing a transmitted value.

**Overloads**

- `Receive.Value(T)`

  A channel receive containing a transmitted value.

  **Example**

  ```vlnc
  $channel = 1 Channel[Int]
$channel 7 send
$channel receive
  ```


**Example**

```vlnc
$channel = 1 Channel[Int]
$channel 7 send
$channel receive
```

### `send`

Send one transferable value with rendezvous or backpressure.

**Overloads**

- `send(Channel[T], T) ->`

  Send one transferable value with rendezvous or backpressure.

  **Example**

  ```vlnc
  $channel = 1 Channel[Int]
$channel 7 send
$channel receive
  ```


**Example**

```vlnc
$channel = 1 Channel[Int]
$channel 7 send
$channel receive
```

### `spawn`

Create a cooperatively scheduled task in the current scope.

**Overloads**

- `spawn(function) -> Task[outputs...]`

  Create a cooperatively scheduled task in the current scope.

  **Example**

  ```vlnc
  $task = fn => 42 end | spawn
$task wait
  ```


**Example**

```vlnc
$task = fn => 42 end | spawn
$task wait
```

### `wait`

Observe a task result; observation is repeatable.

**Overloads**

- `wait(Task[T...]) -> T...`

  Observe a task result; observation is repeatable.

  **Example**

  ```vlnc
  $task = fn => 42 end | spawn
$task wait
  ```


**Example**

```vlnc
$task = fn => 42 end | spawn
$task wait
```

## std.arithmetic

### `std.arithmetic.cube`

Raise a number to the third power.

**Overloads**

- `public define cube(n: Number) -> Number`

  **Example**

  ```vlnc
  import {std.arithmetic.cube} 3 cube
  ```


Raise a number to the third power.

**Parameters**

- `n` — Number to cube.

**Returns:** The cube of `n`.

**Example**

```vlnc
import {std.arithmetic.cube} 3 cube
```

### `std.arithmetic.square`

Multiply a number by itself.

**Overloads**

- `public define square(n: Number) -> Number`

  **Example**

  ```vlnc
  import {std.arithmetic.square} 3 square
  ```


Multiply a number by itself.

**Parameters**

- `n` — Number to square.

**Returns:** The square of `n`.

**Example**

```vlnc
import {std.arithmetic.square} 3 square
```

## std.cmdline

### `std.cmdline.\argv`

Return the arguments passed to the Valiance program.

**Overloads**

- `std.cmdline.\argv() -> String+`

  **Example**

  ```vlnc
  import { std.cmdline } cmdline.\argv
  ```
  Returns the arguments supplied by the caller.


**Returns:** Program arguments, excluding interpreter arguments.

**Example**

```vlnc
import { std.cmdline } cmdline.\argv
```
Returns the arguments supplied by the caller.

## std.concurrency

### `std.concurrency.get`

Read an immutable logical snapshot of the committed State[T].

**Overloads**

- `std.concurrency.get(state: State[T]) -> T`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(3)
concurrency.get($s)
  ```
  Result:

  ```text
  3
  ```


**Parameters**

- `state` — Shared cell handle.

**Returns:** Snapshot T.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(3)
concurrency.get($s)
```
Result:

```text
3
```

### `std.concurrency.modify`

Atomically modify State[T] using a non-waiting resumable callback.

**Overloads**

- `std.concurrency.modify(state: State[T], transformation: Function[T -> T]) -> ()`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(2)
concurrency.modify($s, fn(x:Int)->Int => $x 1 + end)
get $s
  ```
  Result:

  ```text
  3
  ```


**Parameters**

- `state` — Shared cell handle.
- `transformation` — Function from T to replacement T.

**Returns:** No values.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(2)
concurrency.modify($s, fn(x:Int)->Int => $x 1 + end)
get $s
```
Result:

```text
3
```

### `std.concurrency.modifyWith`

Atomically commit a replacement T and return a separate result R.

**Overloads**

- `std.concurrency.modifyWith(state: State[T], transformation: Function[T -> T, R]) -> R`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(4)
concurrency.modifyWith($s, fn(x:Int)->Int,Int => ($x 1 +) $x end)
get $s
  ```
  Result:

  ```text
  4 5
  ```


**Parameters**

- `state` — Shared cell handle.
- `transformation` — Function from T to two stack outputs: T, R.

**Returns:** The callback result R.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(4)
concurrency.modifyWith($s, fn(x:Int)->Int,Int => ($x 1 +) $x end)
get $s
```
Result:

```text
4 5
```

### `std.concurrency.set`

Atomically publish a precomputed replacement for State[T].

**Overloads**

- `std.concurrency.set(state: State[T], replacement: T) -> ()`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(2)
concurrency.set($s, 9)
get $s
  ```
  Result:

  ```text
  9
  ```


**Parameters**

- `state` — Shared cell handle.
- `replacement` — New committed T.

**Returns:** No values.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(2)
concurrency.set($s, 9)
get $s
```
Result:

```text
9
```

### `std.concurrency.State`

Create a shareable process-local State[T] without a background task.

**Overloads**

- `std.concurrency.State(initial: T) -> State[T]`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(3)
get $s
  ```
  Result:

  ```text
  3
  ```


**Parameters**

- `initial` — Initial owned, duplicable, transferable T.

**Returns:** A shared State[T] handle.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(3)
get $s
```
Result:

```text
3
```

## std.file

### `std.file.#byte`

Validate that a byte is an Int in the inclusive range 0 to 255.

**Overloads**

- `public define #byte(:Int)`

  **Example**

  ```vlnc
  import {std.file.#byte} 65 #byte
  ```


Validate that a byte is an Int in the inclusive range 0 to 255.

**Parameters**

- `value` — Candidate integer value.

**Returns:** True if the value is an unsigned 8-bit byte.

**Example**

```vlnc
import {std.file.#byte} 65 #byte
```

### `std.file.nativeClose`

Flush and close a native file handle if still open.

**Overloads**

- `std.file.nativeClose(handle: Int) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeFlush`

Flush the language runtime buffer without fsync.

**Overloads**

- `std.file.nativeFlush(handle: Int) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeFlush
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeFlush
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeOpenAppend`

Open a sequential native append handle.

**Overloads**

- `std.file.nativeOpenAppend(path: PathArg, encoding: EncodingArg) -> Int`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenAppend(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenAppend(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeOpenRead`

Open a sequential native read handle for a Path.

**Overloads**

- `std.file.nativeOpenRead(path: PathArg, encoding: EncodingArg) -> Int`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeOpenWrite`

Create or truncate a sequential native write handle.

**Overloads**

- `std.file.nativeOpenWrite(path: PathArg, encoding: EncodingArg) -> Int`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeReadAllBytes`

Read all remaining raw bytes.

**Overloads**

- `std.file.nativeReadAllBytes(handle: Int) -> #byte+ Int*`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeReadAllBytes
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeReadAllBytes
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeReadBytes`

Read exactly the requested number of bytes.

**Overloads**

- `std.file.nativeReadBytes(handle: Int, count: Int) -> #byte+ Int*`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle 2 file.nativeReadBytes
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle 2 file.nativeReadBytes
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeReadLine`

Read a line, returning None on end of file.

**Overloads**

- `std.file.nativeReadLine(handle: Int) -> String?`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeReadLine
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeReadLine
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeReadLines`

Read all remaining lines.

**Overloads**

- `std.file.nativeReadLines(handle: Int) -> String+`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeReadLines
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeReadLines
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeReadText`

Read all remaining decoded text.

**Overloads**

- `std.file.nativeReadText(handle: Int) -> String`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeReadText
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenRead(path.Path("example.txt"), file.Encoding.\UTF8)
$handle file.nativeReadText
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeWriteBytes`

Write bytes in the inclusive range 0 to 255.

**Overloads**

- `std.file.nativeWriteBytes(handle: Int, bytes: #byte+ Int*) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
import {std.file.#byte}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle [65 #byte, 66 #byte] file.nativeWriteBytes
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
import {std.file.#byte}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle [65 #byte, 66 #byte] file.nativeWriteBytes
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeWriteLine`

Write text followed by a single LF newline.

**Overloads**

- `std.file.nativeWriteLine(handle: Int, line: String) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle "hello" file.nativeWriteLine
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle "hello" file.nativeWriteLine
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeWriteLines`

Write lines with LF delimiters.

**Overloads**

- `std.file.nativeWriteLines(handle: Int, lines: String*) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle ["a", "b"] file.nativeWriteLines
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle ["a", "b"] file.nativeWriteLines
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

### `std.file.nativeWriteText`

Write text using the selected encoding.

**Overloads**

- `std.file.nativeWriteText(handle: Int, text: String) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle "hello" file.nativeWriteText
$handle file.nativeClose
  ```
  Native read examples require example.txt to exist; native write examples modify it. Close handles after use.


**Example**

```vlnc
import {std.file}
import {std.path}
$handle = file.nativeOpenWrite(path.Path("example.txt"), file.Encoding.\UTF8)
$handle "hello" file.nativeWriteText
$handle file.nativeClose
```
Native read examples require example.txt to exist; native write examples modify it. Close handles after use.

## std.file_do

### `std.file_do.appendBytes`

Append raw bytes to a file and close it.

**Overloads**

- `std.file_do.appendBytes(path: path.Path, bytes: #byte+ Int*) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
import {std.file.#byte}
file.do.appendBytes(path.Path("example.txt"), [65 #byte, 66 #byte])
  ```
  Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.


**Example**

```vlnc
import {std.file}
import {std.path}
import {std.file.#byte}
file.do.appendBytes(path.Path("example.txt"), [65 #byte, 66 #byte])
```
Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.

### `std.file_do.appendLines`

Append LF-delimited lines using the selected encoding.

**Overloads**

- `std.file_do.appendLines(path: path.Path, lines: String*, encoding: file.Encoding) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
file.do.appendLines(path.Path("example.txt"), ["a", "b"], file.Encoding.\UTF8)
  ```
  Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.


**Example**

```vlnc
import {std.file}
import {std.path}
file.do.appendLines(path.Path("example.txt"), ["a", "b"], file.Encoding.\UTF8)
```
Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.

### `std.file_do.appendText`

Append text using the selected encoding and close the file.

**Overloads**

- `std.file_do.appendText(path: path.Path, text: String, encoding: file.Encoding) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
file.do.appendText(path.Path("example.txt"), "hello", file.Encoding.\UTF8)
  ```
  Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.


**Example**

```vlnc
import {std.file}
import {std.path}
file.do.appendText(path.Path("example.txt"), "hello", file.Encoding.\UTF8)
```
Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.

### `std.file_do.readBytes`

Read all bytes from a file and close it.

**Overloads**

- `std.file_do.readBytes(path: path.Path) -> #byte+ Int*`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
file.do.readBytes(path.Path("example.txt"))
  ```
  Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.


**Example**

```vlnc
import {std.file}
import {std.path}
file.do.readBytes(path.Path("example.txt"))
```
Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.

### `std.file_do.readLines`

Read all lines using the selected encoding and close the file.

**Overloads**

- `std.file_do.readLines(path: path.Path, encoding: file.Encoding) -> String+`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
file.do.readLines(path.Path("example.txt"), file.Encoding.\UTF8)
  ```
  Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.


**Example**

```vlnc
import {std.file}
import {std.path}
file.do.readLines(path.Path("example.txt"), file.Encoding.\UTF8)
```
Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.

### `std.file_do.readText`

Read an entire file using the selected encoding and close it.

**Overloads**

- `std.file_do.readText(path: path.Path, encoding: file.Encoding) -> String`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
file.do.readText(path.Path("example.txt"), file.Encoding.\UTF8)
  ```
  Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.


**Example**

```vlnc
import {std.file}
import {std.path}
file.do.readText(path.Path("example.txt"), file.Encoding.\UTF8)
```
Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.

### `std.file_do.writeBytes`

Create or replace a file with raw bytes and close it.

**Overloads**

- `std.file_do.writeBytes(path: path.Path, bytes: #byte+ Int*) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
import {std.file.#byte}
file.do.writeBytes(path.Path("example.txt"), [65 #byte, 66 #byte])
  ```
  Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.


**Example**

```vlnc
import {std.file}
import {std.path}
import {std.file.#byte}
file.do.writeBytes(path.Path("example.txt"), [65 #byte, 66 #byte])
```
Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.

### `std.file_do.writeLines`

Create or replace a file with LF-delimited lines using the selected encoding.

**Overloads**

- `std.file_do.writeLines(path: path.Path, lines: String*, encoding: file.Encoding) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
file.do.writeLines(path.Path("example.txt"), ["a", "b"], file.Encoding.\UTF8)
  ```
  Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.


**Example**

```vlnc
import {std.file}
import {std.path}
file.do.writeLines(path.Path("example.txt"), ["a", "b"], file.Encoding.\UTF8)
```
Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.

### `std.file_do.writeText`

Create or replace a file with text in the selected encoding.

**Overloads**

- `std.file_do.writeText(path: path.Path, text: String, encoding: file.Encoding) -> ()`

  **Example**

  ```vlnc
  import {std.file}
import {std.path}
file.do.writeText(path.Path("example.txt"), "hello", file.Encoding.\UTF8)
  ```
  Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.


**Example**

```vlnc
import {std.file}
import {std.path}
file.do.writeText(path.Path("example.txt"), "hello", file.Encoding.\UTF8)
```
Read examples require example.txt to exist; write and append examples modify it. The helper closes its handle automatically.

## std.grids

### `std.grids.allNeighbors`

Collect each cell together with its surrounding grid neighbors.

**Overloads**

- `std.grids.allNeighbors(board: Cell+2, wrapping: #boolean Int) -> Cell+3`

  **Example**

  ```vlnc
  import {std.grids} grids.allNeighbors([[1, 2]], false)
  ```
  Result:

  ```text
  [[[1, 2], [1, 2]]]
  ```


Each output position contains values in top-left, top-middle, top-right, middle-left, cell, middle-right, bottom-left, bottom-middle, bottom-right order.

Without wrapping, positions outside the grid are omitted.

**Parameters**

- `board` — Rectangular two-dimensional input grid.
- `wrapping` — Whether opposite edges are adjacent.

**Returns:** A grid whose cells are ordered neighborhood lists.

**Example**

```vlnc
import {std.grids} grids.allNeighbors([[1, 2]], false)
```
Result:

```text
[[[1, 2], [1, 2]]]
```

## std.html

### `std.html.a`

Wrap text content in a `<a>` element.

**Overloads**

- `std.html.a(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.a("Hello")
  ```

- `std.html.a(child: html.Node) -> html.Node`

  Wrap node content in a `<a>` element.

  **Example**

  ```vlnc
  import {std.html}
html.a(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.a("Hello")
```

### `std.html.article`

Wrap text content in a `<article>` element.

**Overloads**

- `std.html.article(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.article("Hello")
  ```

- `std.html.article(child: html.Node) -> html.Node`

  Wrap node content in a `<article>` element.

  **Example**

  ```vlnc
  import {std.html}
html.article(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.article("Hello")
```

### `std.html.aside`

Wrap text content in a `<aside>` element.

**Overloads**

- `std.html.aside(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.aside("Hello")
  ```

- `std.html.aside(child: html.Node) -> html.Node`

  Wrap node content in a `<aside>` element.

  **Example**

  ```vlnc
  import {std.html}
html.aside(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.aside("Hello")
```

### `std.html.blockquote`

Wrap text content in a `<blockquote>` element.

**Overloads**

- `std.html.blockquote(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.blockquote("Hello")
  ```

- `std.html.blockquote(child: html.Node) -> html.Node`

  Wrap node content in a `<blockquote>` element.

  **Example**

  ```vlnc
  import {std.html}
html.blockquote(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.blockquote("Hello")
```

### `std.html.body`

Wrap text content in a `<body>` element.

**Overloads**

- `std.html.body(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.body("Hello")
  ```

- `std.html.body(child: html.Node) -> html.Node`

  Wrap node content in a `<body>` element.

  **Example**

  ```vlnc
  import {std.html}
html.body(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.body("Hello")
```

### `std.html.br`

Create a `<br>` element with attributes.

**Overloads**

- `std.html.br(attributes: Dict[String, String]) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.br(dict{"class" => "clear"})
  ```


**Example**

```vlnc
import {std.html}
html.br(dict{"class" => "clear"})
```

### `std.html.button`

Wrap text content in a `<button>` element.

**Overloads**

- `std.html.button(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.button("Hello")
  ```

- `std.html.button(child: html.Node) -> html.Node`

  Wrap node content in a `<button>` element.

  **Example**

  ```vlnc
  import {std.html}
html.button(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.button("Hello")
```

### `std.html.checkboxField`

Create a labelled checkbox input.

**Overloads**

- `std.html.checkboxField(name: String, label: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.checkboxField("notify", "Notify me")
  ```


**Example**

```vlnc
import {std.html}
html.checkboxField("notify", "Notify me")
```

### `std.html.children`

Wrap child nodes in a named element.

**Overloads**

- `std.html.children(name: String, children: html.Node+) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.children("main", [html.p("Hello")])
  ```


**Example**

```vlnc
import {std.html}
html.children("main", [html.p("Hello")])
```

### `std.html.choose`

Select one of two nodes based on a condition.

**Overloads**

- `std.html.choose(condition: #boolean Int, yes: html.Node, no: html.Node) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.choose(true, html.p("Yes"), html.p("No"))
  ```


**Example**

```vlnc
import {std.html}
html.choose(true, html.p("Yes"), html.p("No"))
```

### `std.html.code`

Wrap text content in a `<code>` element.

**Overloads**

- `std.html.code(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.code("Hello")
  ```

- `std.html.code(child: html.Node) -> html.Node`

  Wrap node content in a `<code>` element.

  **Example**

  ```vlnc
  import {std.html}
html.code(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.code("Hello")
```

### `std.html.details`

Wrap text content in a `<details>` element.

**Overloads**

- `std.html.details(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.details("Hello")
  ```

- `std.html.details(child: html.Node) -> html.Node`

  Wrap node content in a `<details>` element.

  **Example**

  ```vlnc
  import {std.html}
html.details(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.details("Hello")
```

### `std.html.div`

Wrap text content in a `<div>` element.

**Overloads**

- `std.html.div(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.div("Hello")
  ```

- `std.html.div(child: html.Node) -> html.Node`

  Wrap node content in a `<div>` element.

  **Example**

  ```vlnc
  import {std.html}
html.div(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.div("Hello")
```

### `std.html.document`

Add a standards-mode doctype to an HTML root node.

**Overloads**

- `std.html.document(root: html.Node) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.document(html.html(html.body("Hello")))
  ```


**Example**

```vlnc
import {std.html}
html.document(html.html(html.body("Hello")))
```

### `std.html.element`

Create a named element with child nodes and attributes.

**Overloads**

- `std.html.element(name: String, children: html.Node+, attributes: Dict[String, String]) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.element("main", [html.p("Hello")], dict{"id" => "intro"})
  ```


**Example**

```vlnc
import {std.html}
html.element("main", [html.p("Hello")], dict{"id" => "intro"})
```

### `std.html.em`

Wrap text content in a `<em>` element.

**Overloads**

- `std.html.em(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.em("Hello")
  ```

- `std.html.em(child: html.Node) -> html.Node`

  Wrap node content in a `<em>` element.

  **Example**

  ```vlnc
  import {std.html}
html.em(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.em("Hello")
```

### `std.html.empty`

Create a fragment with no markup.

**Overloads**

- `std.html.empty() -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.empty
  ```


**Example**

```vlnc
import {std.html}
html.empty
```

### `std.html.figcaption`

Wrap text content in a `<figcaption>` element.

**Overloads**

- `std.html.figcaption(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.figcaption("Hello")
  ```

- `std.html.figcaption(child: html.Node) -> html.Node`

  Wrap node content in a `<figcaption>` element.

  **Example**

  ```vlnc
  import {std.html}
html.figcaption(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.figcaption("Hello")
```

### `std.html.figure`

Wrap text content in a `<figure>` element.

**Overloads**

- `std.html.figure(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.figure("Hello")
  ```

- `std.html.figure(child: html.Node) -> html.Node`

  Wrap node content in a `<figure>` element.

  **Example**

  ```vlnc
  import {std.html}
html.figure(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.figure("Hello")
```

### `std.html.footer`

Wrap text content in a `<footer>` element.

**Overloads**

- `std.html.footer(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.footer("Hello")
  ```

- `std.html.footer(child: html.Node) -> html.Node`

  Wrap node content in a `<footer>` element.

  **Example**

  ```vlnc
  import {std.html}
html.footer(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.footer("Hello")
```

### `std.html.form`

Wrap text content in a `<form>` element.

**Overloads**

- `std.html.form(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.form("Hello")
  ```

- `std.html.form(child: html.Node) -> html.Node`

  Wrap node content in a `<form>` element.

  **Example**

  ```vlnc
  import {std.html}
html.form(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.form("Hello")
```

### `std.html.formFor`

Create a GET or POST form with the given fields.

**Overloads**

- `std.html.formFor(method: String, action: String, fields: html.Node) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.formFor("get", "/search", html.textField("q", "Search"))
  ```


**Example**

```vlnc
import {std.html}
html.formFor("get", "/search", html.textField("q", "Search"))
```

### `std.html.fragment`

Combine child nodes without a wrapper element.

**Overloads**

- `std.html.fragment(children: html.Node+) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.fragment([html.p("One"), html.p("Two")])
  ```


**Example**

```vlnc
import {std.html}
html.fragment([html.p("One"), html.p("Two")])
```

### `std.html.h1`

Wrap text content in a `<h1>` element.

**Overloads**

- `std.html.h1(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.h1("Hello")
  ```

- `std.html.h1(child: html.Node) -> html.Node`

  Wrap node content in a `<h1>` element.

  **Example**

  ```vlnc
  import {std.html}
html.h1(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.h1("Hello")
```

### `std.html.h2`

Wrap text content in a `<h2>` element.

**Overloads**

- `std.html.h2(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.h2("Hello")
  ```

- `std.html.h2(child: html.Node) -> html.Node`

  Wrap node content in a `<h2>` element.

  **Example**

  ```vlnc
  import {std.html}
html.h2(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.h2("Hello")
```

### `std.html.h3`

Wrap text content in a `<h3>` element.

**Overloads**

- `std.html.h3(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.h3("Hello")
  ```

- `std.html.h3(child: html.Node) -> html.Node`

  Wrap node content in a `<h3>` element.

  **Example**

  ```vlnc
  import {std.html}
html.h3(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.h3("Hello")
```

### `std.html.h4`

Wrap text content in a `<h4>` element.

**Overloads**

- `std.html.h4(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.h4("Hello")
  ```

- `std.html.h4(child: html.Node) -> html.Node`

  Wrap node content in a `<h4>` element.

  **Example**

  ```vlnc
  import {std.html}
html.h4(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.h4("Hello")
```

### `std.html.h5`

Wrap text content in a `<h5>` element.

**Overloads**

- `std.html.h5(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.h5("Hello")
  ```

- `std.html.h5(child: html.Node) -> html.Node`

  Wrap node content in a `<h5>` element.

  **Example**

  ```vlnc
  import {std.html}
html.h5(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.h5("Hello")
```

### `std.html.h6`

Wrap text content in a `<h6>` element.

**Overloads**

- `std.html.h6(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.h6("Hello")
  ```

- `std.html.h6(child: html.Node) -> html.Node`

  Wrap node content in a `<h6>` element.

  **Example**

  ```vlnc
  import {std.html}
html.h6(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.h6("Hello")
```

### `std.html.head`

Wrap text content in a `<head>` element.

**Overloads**

- `std.html.head(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.head("Hello")
  ```

- `std.html.head(child: html.Node) -> html.Node`

  Wrap node content in a `<head>` element.

  **Example**

  ```vlnc
  import {std.html}
html.head(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.head("Hello")
```

### `std.html.header`

Wrap text content in a `<header>` element.

**Overloads**

- `std.html.header(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.header("Hello")
  ```

- `std.html.header(child: html.Node) -> html.Node`

  Wrap node content in a `<header>` element.

  **Example**

  ```vlnc
  import {std.html}
html.header(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.header("Hello")
```

### `std.html.hiddenField`

Create a hidden input with a name and value.

**Overloads**

- `std.html.hiddenField(name: String, value: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.hiddenField("mode", "edit")
  ```


**Example**

```vlnc
import {std.html}
html.hiddenField("mode", "edit")
```

### `std.html.horizontalRule`

Create a thematic break element.

**Overloads**

- `std.html.horizontalRule() -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.horizontalRule
  ```


**Example**

```vlnc
import {std.html}
html.horizontalRule
```

### `std.html.hr`

Create a `<hr>` element with attributes.

**Overloads**

- `std.html.hr(attributes: Dict[String, String]) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.hr(dict{"class" => "divider"})
  ```


**Example**

```vlnc
import {std.html}
html.hr(dict{"class" => "divider"})
```

### `std.html.html`

Wrap text content in a `<html>` element.

**Overloads**

- `std.html.html(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.html("Hello")
  ```

- `std.html.html(child: html.Node) -> html.Node`

  Wrap node content in a `<html>` element.

  **Example**

  ```vlnc
  import {std.html}
html.html(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.html("Hello")
```

### `std.html.img`

Create a `<img>` element with attributes.

**Overloads**

- `std.html.img(attributes: Dict[String, String]) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.img(dict{"src" => "/logo.png", "alt" => "Logo"})
  ```


**Example**

```vlnc
import {std.html}
html.img(dict{"src" => "/logo.png", "alt" => "Logo"})
```

### `std.html.input`

Create a `<input>` element with attributes.

**Overloads**

- `std.html.input(attributes: Dict[String, String]) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.input(dict{"type" => "text", "name" => "query"})
  ```


**Example**

```vlnc
import {std.html}
html.input(dict{"type" => "text", "name" => "query"})
```

### `std.html.join`

Join child nodes with a separator node.

**Overloads**

- `std.html.join(children: html.Node+, separator: html.Node) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.join([html.span("A"), html.span("B")], html.text(", "))
  ```


**Example**

```vlnc
import {std.html}
html.join([html.span("A"), html.span("B")], html.text(", "))
```

### `std.html.label`

Wrap text content in a `<label>` element.

**Overloads**

- `std.html.label(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.label("Hello")
  ```

- `std.html.label(child: html.Node) -> html.Node`

  Wrap node content in a `<label>` element.

  **Example**

  ```vlnc
  import {std.html}
html.label(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.label("Hello")
```

### `std.html.li`

Wrap text content in a `<li>` element.

**Overloads**

- `std.html.li(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.li("Hello")
  ```

- `std.html.li(child: html.Node) -> html.Node`

  Wrap node content in a `<li>` element.

  **Example**

  ```vlnc
  import {std.html}
html.li(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.li("Hello")
```

### `std.html.lineBreak`

Create a line break element.

**Overloads**

- `std.html.lineBreak() -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.lineBreak
  ```


**Example**

```vlnc
import {std.html}
html.lineBreak
```

### `std.html.link`

Create a link to a related resource.

**Overloads**

- `std.html.link(rel: String, href: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.link("canonical", "/intro")
  ```


**Example**

```vlnc
import {std.html}
html.link("canonical", "/intro")
```

### `std.html.main`

Wrap text content in a `<main>` element.

**Overloads**

- `std.html.main(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.main("Hello")
  ```

- `std.html.main(child: html.Node) -> html.Node`

  Wrap node content in a `<main>` element.

  **Example**

  ```vlnc
  import {std.html}
html.main(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.main("Hello")
```

### `std.html.meta`

Create a named metadata element.

**Overloads**

- `std.html.meta(name: String, content: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.meta("author", "Ada")
  ```


**Example**

```vlnc
import {std.html}
html.meta("author", "Ada")
```

### `std.html.metaDescription`

Create a page description metadata element.

**Overloads**

- `std.html.metaDescription(description: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.metaDescription("Introduction")
  ```


**Example**

```vlnc
import {std.html}
html.metaDescription("Introduction")
```

### `std.html.nav`

Wrap text content in a `<nav>` element.

**Overloads**

- `std.html.nav(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.nav("Hello")
  ```

- `std.html.nav(child: html.Node) -> html.Node`

  Wrap node content in a `<nav>` element.

  **Example**

  ```vlnc
  import {std.html}
html.nav(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.nav("Hello")
```

### `std.html.ol`

Wrap text content in a `<ol>` element.

**Overloads**

- `std.html.ol(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.ol("Hello")
  ```

- `std.html.ol(child: html.Node) -> html.Node`

  Wrap node content in a `<ol>` element.

  **Example**

  ```vlnc
  import {std.html}
html.ol(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.ol("Hello")
```

### `std.html.option`

Wrap text content in a `<option>` element.

**Overloads**

- `std.html.option(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.option("Hello")
  ```

- `std.html.option(child: html.Node) -> html.Node`

  Wrap node content in a `<option>` element.

  **Example**

  ```vlnc
  import {std.html}
html.option(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.option("Hello")
```

### `std.html.p`

Wrap text content in a `<p>` element.

**Overloads**

- `std.html.p(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.p("Hello")
  ```

- `std.html.p(child: html.Node) -> html.Node`

  Wrap node content in a `<p>` element.

  **Example**

  ```vlnc
  import {std.html}
html.p(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.p("Hello")
```

### `std.html.page`

Create an HTML document with a title and body.

**Overloads**

- `std.html.page(title: String, body: html.Node) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.page("Introduction", html.p("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.page("Introduction", html.p("Hello"))
```

### `std.html.pageWithHead`

Create an HTML document with additional head nodes and a language.

**Overloads**

- `std.html.pageWithHead(title: String, body: html.Node, head: html.Node+, lang: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.pageWithHead("Introduction", html.p("Hello"), [html.metaDescription("Example")], "en")
  ```


**Example**

```vlnc
import {std.html}
html.pageWithHead("Introduction", html.p("Hello"), [html.metaDescription("Example")], "en")
```

### `std.html.postForm`

Create a POST form with the given fields.

**Overloads**

- `std.html.postForm(action: String, fields: html.Node) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.postForm("/save", html.submitButton("Save"))
  ```


**Example**

```vlnc
import {std.html}
html.postForm("/save", html.submitButton("Save"))
```

### `std.html.pre`

Wrap text content in a `<pre>` element.

**Overloads**

- `std.html.pre(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.pre("Hello")
  ```

- `std.html.pre(child: html.Node) -> html.Node`

  Wrap node content in a `<pre>` element.

  **Example**

  ```vlnc
  import {std.html}
html.pre(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.pre("Hello")
```

### `std.html.render`

Render a verified node as an HTML string.

**Overloads**

- `std.html.render(node: html.Node) -> String`

  **Example**

  ```vlnc
  import {std.html}
html.render(html.p("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.render(html.p("Hello"))
```

### `std.html.scriptSrc`

Reference an external script.

**Overloads**

- `std.html.scriptSrc(src: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.scriptSrc("/site.js")
  ```


**Example**

```vlnc
import {std.html}
html.scriptSrc("/site.js")
```

### `std.html.section`

Wrap text content in a `<section>` element.

**Overloads**

- `std.html.section(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.section("Hello")
  ```

- `std.html.section(child: html.Node) -> html.Node`

  Wrap node content in a `<section>` element.

  **Example**

  ```vlnc
  import {std.html}
html.section(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.section("Hello")
```

### `std.html.select`

Wrap text content in a `<select>` element.

**Overloads**

- `std.html.select(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.select("Hello")
  ```

- `std.html.select(child: html.Node) -> html.Node`

  Wrap node content in a `<select>` element.

  **Example**

  ```vlnc
  import {std.html}
html.select(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.select("Hello")
```

### `std.html.small`

Wrap text content in a `<small>` element.

**Overloads**

- `std.html.small(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.small("Hello")
  ```

- `std.html.small(child: html.Node) -> html.Node`

  Wrap node content in a `<small>` element.

  **Example**

  ```vlnc
  import {std.html}
html.small(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.small("Hello")
```

### `std.html.span`

Wrap text content in a `<span>` element.

**Overloads**

- `std.html.span(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.span("Hello")
  ```

- `std.html.span(child: html.Node) -> html.Node`

  Wrap node content in a `<span>` element.

  **Example**

  ```vlnc
  import {std.html}
html.span(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.span("Hello")
```

### `std.html.strong`

Wrap text content in a `<strong>` element.

**Overloads**

- `std.html.strong(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.strong("Hello")
  ```

- `std.html.strong(child: html.Node) -> html.Node`

  Wrap node content in a `<strong>` element.

  **Example**

  ```vlnc
  import {std.html}
html.strong(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.strong("Hello")
```

### `std.html.stylesheet`

Link an external CSS stylesheet.

**Overloads**

- `std.html.stylesheet(href: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.stylesheet("/site.css")
  ```


**Example**

```vlnc
import {std.html}
html.stylesheet("/site.css")
```

### `std.html.submitButton`

Create a form submit button with escaped label text.

**Overloads**

- `std.html.submitButton(label: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.submitButton("Save")
  ```


**Example**

```vlnc
import {std.html}
html.submitButton("Save")
```

### `std.html.summary`

Wrap text content in a `<summary>` element.

**Overloads**

- `std.html.summary(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.summary("Hello")
  ```

- `std.html.summary(child: html.Node) -> html.Node`

  Wrap node content in a `<summary>` element.

  **Example**

  ```vlnc
  import {std.html}
html.summary(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.summary("Hello")
```

### `std.html.table`

Wrap text content in a `<table>` element.

**Overloads**

- `std.html.table(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.table("Hello")
  ```

- `std.html.table(child: html.Node) -> html.Node`

  Wrap node content in a `<table>` element.

  **Example**

  ```vlnc
  import {std.html}
html.table(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.table("Hello")
```

### `std.html.tbody`

Wrap text content in a `<tbody>` element.

**Overloads**

- `std.html.tbody(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.tbody("Hello")
  ```

- `std.html.tbody(child: html.Node) -> html.Node`

  Wrap node content in a `<tbody>` element.

  **Example**

  ```vlnc
  import {std.html}
html.tbody(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.tbody("Hello")
```

### `std.html.td`

Wrap text content in a `<td>` element.

**Overloads**

- `std.html.td(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.td("Hello")
  ```

- `std.html.td(child: html.Node) -> html.Node`

  Wrap node content in a `<td>` element.

  **Example**

  ```vlnc
  import {std.html}
html.td(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.td("Hello")
```

### `std.html.text`

Escape plain text for use as HTML content.

**Overloads**

- `std.html.text(value: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.text("A & B")
  ```


**Example**

```vlnc
import {std.html}
html.text("A & B")
```

### `std.html.textarea`

Wrap text content in a `<textarea>` element.

**Overloads**

- `std.html.textarea(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.textarea("Hello")
  ```

- `std.html.textarea(child: html.Node) -> html.Node`

  Wrap node content in a `<textarea>` element.

  **Example**

  ```vlnc
  import {std.html}
html.textarea(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.textarea("Hello")
```

### `std.html.textField`

Create a labelled text input.

**Overloads**

- `std.html.textField(name: String, label: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.textField("name", "Name")
  ```


**Example**

```vlnc
import {std.html}
html.textField("name", "Name")
```

### `std.html.th`

Wrap text content in a `<th>` element.

**Overloads**

- `std.html.th(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.th("Hello")
  ```

- `std.html.th(child: html.Node) -> html.Node`

  Wrap node content in a `<th>` element.

  **Example**

  ```vlnc
  import {std.html}
html.th(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.th("Hello")
```

### `std.html.thead`

Wrap text content in a `<thead>` element.

**Overloads**

- `std.html.thead(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.thead("Hello")
  ```

- `std.html.thead(child: html.Node) -> html.Node`

  Wrap node content in a `<thead>` element.

  **Example**

  ```vlnc
  import {std.html}
html.thead(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.thead("Hello")
```

### `std.html.title`

Wrap text content in a `<title>` element.

**Overloads**

- `std.html.title(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.title("Hello")
  ```

- `std.html.title(child: html.Node) -> html.Node`

  Wrap node content in a `<title>` element.

  **Example**

  ```vlnc
  import {std.html}
html.title(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.title("Hello")
```

### `std.html.tr`

Wrap text content in a `<tr>` element.

**Overloads**

- `std.html.tr(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.tr("Hello")
  ```

- `std.html.tr(child: html.Node) -> html.Node`

  Wrap node content in a `<tr>` element.

  **Example**

  ```vlnc
  import {std.html}
html.tr(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.tr("Hello")
```

### `std.html.ul`

Wrap text content in a `<ul>` element.

**Overloads**

- `std.html.ul(child: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.ul("Hello")
  ```

- `std.html.ul(child: html.Node) -> html.Node`

  Wrap node content in a `<ul>` element.

  **Example**

  ```vlnc
  import {std.html}
html.ul(html.text("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.ul("Hello")
```

### `std.html.when`

Include a child node when a condition is true.

**Overloads**

- `std.html.when(condition: #boolean Int, child: html.Node) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.when(true, html.p("Hello"))
  ```


**Example**

```vlnc
import {std.html}
html.when(true, html.p("Hello"))
```

### `std.html.withAria`

Add an ARIA attribute to an existing element.

**Overloads**

- `std.html.withAria(node: html.Node, name: String, value: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.withAria(html.p("Hello"), "label", "Introduction")
  ```


**Example**

```vlnc
import {std.html}
html.withAria(html.p("Hello"), "label", "Introduction")
```

### `std.html.withAttrs`

Add attributes to an existing element.

**Overloads**

- `std.html.withAttrs(node: html.Node, attributes: Dict[String, String]) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.withAttrs(html.p("Hello"), dict{"id" => "intro"})
  ```


**Example**

```vlnc
import {std.html}
html.withAttrs(html.p("Hello"), dict{"id" => "intro"})
```

### `std.html.withBoolean`

Add a boolean attribute when its condition is true.

**Overloads**

- `std.html.withBoolean(node: html.Node, name: String, enabled: #boolean Int) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.withBoolean(html.button("Continue"), "disabled", true)
  ```


**Example**

```vlnc
import {std.html}
html.withBoolean(html.button("Continue"), "disabled", true)
```

### `std.html.withClass`

Add a class attribute to an existing element.

**Overloads**

- `std.html.withClass(node: html.Node, value: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.withClass(html.p("Hello"), "intro")
  ```


**Example**

```vlnc
import {std.html}
html.withClass(html.p("Hello"), "intro")
```

### `std.html.withData`

Add a custom data attribute to an existing element.

**Overloads**

- `std.html.withData(node: html.Node, name: String, value: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.withData(html.p("Hello"), "topic", "intro")
  ```


**Example**

```vlnc
import {std.html}
html.withData(html.p("Hello"), "topic", "intro")
```

### `std.html.withId`

Add an ID attribute to an existing element.

**Overloads**

- `std.html.withId(node: html.Node, value: String) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.withId(html.p("Hello"), "intro")
  ```


**Example**

```vlnc
import {std.html}
html.withId(html.p("Hello"), "intro")
```

### `std.html.withStyle`

Add validated inline CSS properties to an existing element.

**Overloads**

- `std.html.withStyle(node: html.Node, properties: Dict[String, String]) -> html.Node`

  **Example**

  ```vlnc
  import {std.html}
html.withStyle(html.p("Hello"), dict{"color" => "red"})
  ```


**Example**

```vlnc
import {std.html}
html.withStyle(html.p("Hello"), dict{"color" => "red"})
```

## std.json

### `std.json.decode`

Decode strict JSON into a checked Valiance type.

**Overloads**

- `public define[T] decode(text: String) -> Result[T, ValueError]`

  **Example**

  ```vlnc
  import {std.json} json.decode[Int]("42") | ?!
  ```


Decode strict JSON into a checked Valiance type.



The caller selects a target type with decode[Type](text). Numbers retain

exact decimal precision, and nested arrays, records, dictionaries, optionals,

generic objects, and unambiguous variants are reconstructed recursively.

Required public/readable fields must be present; private fields need declared

defaults. Extra or duplicate keys, wrong shapes, invalid JSON, protected

owners, and objects with custom initializers return ValueError. Registered

host native factories can decode opaque FFI handles; unregistered handles

return ValueError. JSON arrays

are decoded eagerly rather than as lazy evaluation plans.

**Parameters**

- `text` — JSON string to decode.

**Returns:** A typed value or ValueError.

**Example**

```vlnc
import {std.json} json.decode[Int]("42") | ?!
```

### `std.json.encode`

Encode JSON-compatible values, including managed objects, without callbacks.

**Overloads**

- `public define[T] encode(value: T) -> Result[String, ValueError]`

  **Example**

  ```vlnc
  import {std.json} json.encode(record{name => "Ada"}) | ?!
  ```


Encode JSON-compatible values, including managed objects, without callbacks.



Nested objects and JSON-compatible scalar, finite eager or lazy list,

dictionary and optional values are handled automatically. Private fields

are excluded. Finite lazy lists are evaluated as needed, including nested

lazies; explicitly infinite lists return ValueError.

Managed objects nested at any depth inside lists, String-keyed

dictionaries, and optionals use constructor-checked field visibility.

Protected and unsupported values return ValueError.

**Parameters**

- `value` — Value to serialize.

**Returns:** JSON text or the first ValueError.

**Example**

```vlnc
import {std.json} json.encode(record{name => "Ada"}) | ?!
```

### `std.json.encodeDictionary`

Encode a String-keyed dictionary using a value encoder.

**Overloads**

- `public define[T] encodeDictionary(value: Dict[String, T], encodeValue: Function[T -> Result[String, ValueError]]) -> Result[String, ValueError]`

  **Example**

  ```vlnc
  import {std.json} json.encodeDictionary(dict{"age" => 42}, fn(value: Int) -> Result[String, ValueError] => json.encode($value) end) | ?!
  ```


Encode a String-keyed dictionary using a value encoder.

**Parameters**

- `value` — Dictionary to serialize.
- `encodeValue` — Encoder returning Result[String, ValueError] for a value.

**Returns:** JSON object text, or the first encoding error.

**Example**

```vlnc
import {std.json} json.encodeDictionary(dict{"age" => 42}, fn(value: Int) -> Result[String, ValueError] => json.encode($value) end) | ?!
```

### `std.json.encodeList`

Encode a finite list using an item encoder, preserving its array structure.

**Overloads**

- `public define[T] encodeList(value: T+, encodeItem: Function[T -> Result[String, ValueError]]) -> Result[String, ValueError]`

  **Example**

  ```vlnc
  import {std.json} json.encodeList([2, 3], fn(value: Int) -> Result[String, ValueError] => json.encode($value) end) | ?!
  ```


Encode a finite list using an item encoder, preserving its array structure.

**Parameters**

- `value` — List to serialize.
- `encodeItem` — Encoder returning Result[String, ValueError] for an item.

**Returns:** JSON array text, or the first encoding error.

**Example**

```vlnc
import {std.json} json.encodeList([2, 3], fn(value: Int) -> Result[String, ValueError] => json.encode($value) end) | ?!
```

### `std.json.encodeObject`

Encode managed storage with a field encoder.

**Overloads**

- `public define[T, FieldValue] encodeObject(value: T, encodeField: Function[FieldValue -> Result[String, ValueError]]) -> Result[String, ValueError]`

  **Example**

  ```vlnc
  import {std.json} json.encodeObject(record{name => "Ada"}, fn(value: String) -> Result[String, ValueError] => json.encode($value) end) | ?!
  ```


Encode managed storage with a field encoder.



Public and readable fields are emitted in declaration order; private fields

are omitted. The callback is checked against each declared field type.

Protected owners return ValueError. Ordinary ownership rules apply to values.

**Parameters**

- `value` — Managed object or record to serialize.
- `encodeField` — Encoder returning Result[String, ValueError] for a field.

**Returns:** JSON object text, or the first encoding error.

**Example**

```vlnc
import {std.json} json.encodeObject(record{name => "Ada"}, fn(value: String) -> Result[String, ValueError] => json.encode($value) end) | ?!
```

### `std.json.encodeOptional`

Encode an optional with a payload encoder, using null for absence.

**Overloads**

- `public define[T] encodeOptional(value: T?, encodePayload: Function[T -> Result[String, ValueError]]) -> Result[String, ValueError]`

  **Example**

  ```vlnc
  import {std.json} json.encodeOptional(Some(2), fn(value: Int) -> Result[String, ValueError] => json.encode($value) end) | ?!
  ```


Encode an optional with a payload encoder, using null for absence.

**Parameters**

- `value` — Optional value to serialize.
- `encodePayload` — Encoder returning Result[String, ValueError] for a payload.

**Returns:** JSON text, or the payload error.

**Example**

```vlnc
import {std.json} json.encodeOptional(Some(2), fn(value: Int) -> Result[String, ValueError] => json.encode($value) end) | ?!
```

## std.path

### `std.path.extension`

Retrieve the native extension as Path, or None if absent.

**Overloads**

- `std.path.extension(path: path.Path) -> path.Path?`

  **Example**

  ```vlnc
  import {std.path}
path.extension(path.Path("notes/report.txt"))
  ```
  Path spelling and separators follow the host operating system.


**Example**

```vlnc
import {std.path}
path.extension(path.Path("notes/report.txt"))
```
Path spelling and separators follow the host operating system.

### `std.path.join`

Join native Path objects without losing filename bytes.

**Overloads**

- `std.path.join(root: path.Path, child: path.Path) -> path.Path`

  **Example**

  ```vlnc
  import {std.path}
path.join(path.Path("notes"), path.Path("report.txt"))
  ```
  Path spelling and separators follow the host operating system.


**Example**

```vlnc
import {std.path}
path.join(path.Path("notes"), path.Path("report.txt"))
```
Path spelling and separators follow the host operating system.

### `std.path.name`

Retrieve the last native path component, or None if absent.

**Overloads**

- `std.path.name(path: path.Path) -> path.Path?`

  **Example**

  ```vlnc
  import {std.path}
path.name(path.Path("notes/report.txt"))
  ```
  Path spelling and separators follow the host operating system.


**Example**

```vlnc
import {std.path}
path.name(path.Path("notes/report.txt"))
```
Path spelling and separators follow the host operating system.

### `std.path.parent`

Retrieve the lexical parent, or None if none exists.

**Overloads**

- `std.path.parent(path: path.Path) -> path.Path?`

  **Example**

  ```vlnc
  import {std.path}
path.parent(path.Path("notes/report.txt"))
  ```
  Path spelling and separators follow the host operating system.


**Example**

```vlnc
import {std.path}
path.parent(path.Path("notes/report.txt"))
```
Path spelling and separators follow the host operating system.

### `std.path.to`

Convert the native path to a possibly lossy String.

**Overloads**

- `std.path.to(input1: path.Path) -> String`

  **Example**

  ```vlnc
  import {std.path.Path, std.path.to}
Path "notes.txt" | to[String]
  ```
  Path spelling and separators follow the host operating system.


**Example**

```vlnc
import {std.path.Path, std.path.to}
Path "notes.txt" | to[String]
```
Path spelling and separators follow the host operating system.

## std.random

### `std.random.between`

Return a uniformly random integer in an inclusive range.

**Overloads**

- `std.random.between(minimum: Int, maximum: Int) -> Int`

  **Example**

  ```vlnc
  import {std.random} 1 6 random.between
  ```
  Returns an integer from 1 through 6, inclusive; the result varies.


**Parameters**

- `minimum` — Smallest possible integer.
- `maximum` — Largest possible integer.

**Returns:** A random integer between the two bounds, inclusive.

**Example**

```vlnc
import {std.random} 1 6 random.between
```
Returns an integer from 1 through 6, inclusive; the result varies.

### `std.random.randbit`

Return a uniformly random binary integer.

**Overloads**

- `std.random.randbit() -> Int`

  **Example**

  ```vlnc
  import {std.random} random.randbit
  ```
  Returns either 0 or 1; the result varies.


**Returns:** Either zero or one, each with equal probability.

**Example**

```vlnc
import {std.random} random.randbit
```
Returns either 0 or 1; the result varies.

## std.regex

### `std.regex.contains`

Test whether a regular expression occurs anywhere in a string.

**Overloads**

- `std.regex.contains(pattern: String, value: String) -> #boolean Int`

  **Example**

  ```vlnc
  import {std.regex} "[0-9]+" "item 42" regex.contains
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `pattern` — Regular-expression pattern.
- `value` — String to search.

**Returns:** A Boolean indicating whether a match was found.

**Example**

```vlnc
import {std.regex} "[0-9]+" "item 42" regex.contains
```
Result:

```text
1
```

### `std.regex.first`

Return the first regular-expression match in a string.

**Overloads**

- `std.regex.first(pattern: String, value: String) -> String?`

  **Example**

  ```vlnc
  import {std.regex} "[0-9]+" "item 42" regex.first | ?!
  ```
  Result:

  ```text
  42
  ```


**Parameters**

- `pattern` — Regular-expression pattern.
- `value` — String to search.

**Returns:** `Some[String]` containing the first match, or `None`.

**Example**

```vlnc
import {std.regex} "[0-9]+" "item 42" regex.first | ?!
```
Result:

```text
42
```

### `std.regex.matches`

Test whether an entire string matches a regular expression.

**Overloads**

- `std.regex.matches(pattern: String, value: String) -> #boolean Int`

  **Example**

  ```vlnc
  import {std.regex} "[A-Z][a-z]+" "Jeff" regex.matches
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `pattern` — Regular-expression pattern.
- `value` — String to test.

**Returns:** A Boolean indicating whether the complete string matched.

**Example**

```vlnc
import {std.regex} "[A-Z][a-z]+" "Jeff" regex.matches
```
Result:

```text
1
```

### `std.regex.replace`

Replace every regular-expression match in a string.

**Overloads**

- `std.regex.replace(pattern: String, replacement: String, value: String) -> String`

  **Example**

  ```vlnc
  import {std.regex} "[0-9]+" "#" "item 42" regex.replace
  ```
  Result:

  ```text
  item #
  ```


**Parameters**

- `pattern` — Regular-expression pattern.
- `replacement` — Replacement text.
- `value` — String to transform.

**Returns:** The transformed string.

**Example**

```vlnc
import {std.regex} "[0-9]+" "#" "item 42" regex.replace
```
Result:

```text
item #
```

### `std.regex.split`

Split a string at regular-expression matches.

**Overloads**

- `std.regex.split(pattern: String, value: String) -> String+`

  **Example**

  ```vlnc
  import {std.regex} ", *" "a, b,c" regex.split
  ```
  Result:

  ```text
  ['a', 'b', 'c']
  ```


**Parameters**

- `pattern` — Separator pattern.
- `value` — String to split.

**Returns:** A list of string segments.

**Example**

```vlnc
import {std.regex} ", *" "a, b,c" regex.split
```
Result:

```text
['a', 'b', 'c']
```

## std.string

### `std.string.\Alphabet`

Return the uppercase English alphabet.

**Overloads**

- `std.string.\Alphabet() -> String`

  **Example**

  ```vlnc
  import {std.string} string.\Alphabet
  ```
  Result:

  ```text
  ABCDEFGHIJKLMNOPQRSTUVWXYZ
  ```


**Returns:** `ABCDEFGHIJKLMNOPQRSTUVWXYZ`.

**Example**

```vlnc
import {std.string} string.\Alphabet
```
Result:

```text
ABCDEFGHIJKLMNOPQRSTUVWXYZ
```

### `std.string.quote`

Wrap a string in double quotes using JSON escaping.

**Overloads**

- `std.string.quote(value: String) -> String`

  **Example**

  ```vlnc
  import {std.string} "hello" string.quote
  ```
  Result:

  ```text
  "hello"
  ```


**Parameters**

- `value` — String to quote.

**Returns:** A complete JSON string token with non-ASCII characters escaped.

**Example**

```vlnc
import {std.string} "hello" string.quote
```
Result:

```text
"hello"
```

### `std.string.transliterate`

Replace characters according to two positional alphabets.

**Overloads**

- `std.string.transliterate(value: String, source: String, target: String) -> String`

  **Example**

  ```vlnc
  import {std.string} "cab" "abc" "ABC" string.transliterate
  ```
  Result:

  ```text
  CAB
  ```


**Parameters**

- `value` — Input string.
- `source` — Characters to look up.
- `target` — Replacement characters at matching positions.

**Returns:** The transliterated string.

**Example**

```vlnc
import {std.string} "cab" "abc" "ABC" string.transliterate
```
Result:

```text
CAB
```

## std.testing

### `std.testing.assertEqual`

Assert that two values are equal.

**Overloads**

- `std.testing.assertEqual(actual: Value, expected: Value) -> ()`

  **Example**

  ```vlnc
  import {std.testing} 5 5 testing.assertEqual
  ```
  Result:

  ```text

  ```


**Parameters**

- `actual` — Observed value.
- `expected` — Required value.

**Returns:** No stack values when the assertion succeeds.

**Example**

```vlnc
import {std.testing} 5 5 testing.assertEqual
```
Result:

```text

```

### `std.testing.assertNotEqual`

Assert that two values are different.

**Overloads**

- `std.testing.assertNotEqual(actual: Value, unexpected: Value) -> ()`

  **Example**

  ```vlnc
  import {std.testing} 2 3 testing.assertNotEqual
  ```
  Result:

  ```text

  ```


**Parameters**

- `actual` — Observed value.
- `unexpected` — Value that must not match.

**Returns:** No stack values when the assertion succeeds.

**Example**

```vlnc
import {std.testing} 2 3 testing.assertNotEqual
```
Result:

```text

```

### `std.testing.assertPanics`

Assert that invoking a callable raises a recoverable panic.

**Overloads**

- `std.testing.assertPanics(operation: Function) -> ()`

  **Example**

  ```vlnc
  import {std.testing} testing.assertPanics(fn => ValueFault("bad") panic end)
  ```
  Result:

  ```text

  ```


**Parameters**

- `operation` — Niladic callable expected to panic.

**Returns:** No stack values when a panic is observed.

**Example**

```vlnc
import {std.testing} testing.assertPanics(fn => ValueFault("bad") panic end)
```
Result:

```text

```

### `std.testing.fail`

Fail the current test immediately.

**Overloads**

- `std.testing.fail(message: String) -> ()`

  **Example**

  ```vlnc
  import {std.testing} "unexpected result" testing.fail
  ```
  Stops the test with the supplied failure message.


**Parameters**

- `message` — Failure message shown by the test runner.

**Returns:** Never returns normally.

**Example**

```vlnc
import {std.testing} "unexpected result" testing.fail
```
Stops the test with the supplied failure message.

## std.trig

### `std.trig.acos`

Return the inverse cosine in radians.

**Overloads**

- `std.trig.acos(n: Number) -> Number`

  **Example**

  ```vlnc
  import {std.trig} 1 trig.acos
  ```
  Result:

  ```text
  0
  ```


**Parameters**

- `n` — Input ratio.

**Returns:** The corresponding angle in radians.

**Example**

```vlnc
import {std.trig} 1 trig.acos
```
Result:

```text
0
```

### `std.trig.asin`

Return the inverse sine in radians.

**Overloads**

- `std.trig.asin(n: Number) -> Number`

  **Example**

  ```vlnc
  import {std.trig} 0 trig.asin
  ```
  Result:

  ```text
  0
  ```


**Parameters**

- `n` — Input ratio.

**Returns:** The corresponding angle in radians.

**Example**

```vlnc
import {std.trig} 0 trig.asin
```
Result:

```text
0
```

### `std.trig.atan`

Return the inverse tangent in radians.

**Overloads**

- `std.trig.atan(n: Number) -> Number`

  **Example**

  ```vlnc
  import {std.trig} 0 trig.atan
  ```
  Result:

  ```text
  0
  ```


**Parameters**

- `n` — Input ratio.

**Returns:** The corresponding angle in radians.

**Example**

```vlnc
import {std.trig} 0 trig.atan
```
Result:

```text
0
```

### `std.trig.cos`

Return the cosine of an angle in radians.

**Overloads**

- `std.trig.cos(n: Number) -> Number`

  **Example**

  ```vlnc
  import {std.trig} 0 trig.cos
  ```
  Result:

  ```text
  1
  ```


**Parameters**

- `n` — Angle in radians.

**Returns:** The cosine of the angle.

**Example**

```vlnc
import {std.trig} 0 trig.cos
```
Result:

```text
1
```

### `std.trig.pi`

Return the mathematical constant pi.

**Overloads**

- `std.trig.pi() -> Number`

  **Example**

  ```vlnc
  import {std.trig} trig.pi
  ```
  Result:

  ```text
  3.141592653589793
  ```


**Returns:** An approximation of π as a Valiance `Number`.

**Example**

```vlnc
import {std.trig} trig.pi
```
Result:

```text
3.141592653589793
```

### `std.trig.sin`

Return the sine of an angle in radians.

**Overloads**

- `std.trig.sin(n: Number) -> Number`

  **Example**

  ```vlnc
  import {std.trig} 0 trig.sin
  ```
  Result:

  ```text
  0
  ```


**Parameters**

- `n` — Angle in radians.

**Returns:** The sine of the angle.

**Example**

```vlnc
import {std.trig} 0 trig.sin
```
Result:

```text
0
```

### `std.trig.tan`

Return the tangent of an angle in radians.

**Overloads**

- `std.trig.tan(n: Number) -> Number`

  **Example**

  ```vlnc
  import {std.trig} 0 trig.tan
  ```
  Result:

  ```text
  0
  ```


**Parameters**

- `n` — Angle in radians.

**Returns:** The tangent of the angle.

**Example**

```vlnc
import {std.trig} 0 trig.tan
```
Result:

```text
0
```

## std.web

### `std.web.accepts`

Check Accept media ranges, specificity, and quality (including q=0).

**Overloads**

- `std.web.accepts(request: web.Request, mediaType: String) -> Result[#boolean Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
  ```

- `std.web.accepts(request: web.GetRequest, mediaType: String) -> Result[#boolean Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
  ```

- `std.web.accepts(request: web.HeadRequest, mediaType: String) -> Result[#boolean Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
  ```

- `std.web.accepts(request: web.PostRequest, mediaType: String) -> Result[#boolean Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
  ```

- `std.web.accepts(request: web.PutRequest, mediaType: String) -> Result[#boolean Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
  ```

- `std.web.accepts(request: web.PatchRequest, mediaType: String) -> Result[#boolean Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
  ```

- `std.web.accepts(request: web.DeleteRequest, mediaType: String) -> Result[#boolean Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
  ```

- `std.web.accepts(request: web.OptionsRequest, mediaType: String) -> Result[#boolean Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
  ```

- `std.web.accepts(request: web.QueryRequest, mediaType: String) -> Result[#boolean Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
  ```


**Returns:** Boolean or InvalidHeader error.

**Example**

```vlnc
import {std.web}
web.accepts(web.testRequest("GET", "/"), "text/plain")
```

### `std.web.addHeader`

Immutably edit validated response header fields.

**Overloads**

- `std.web.addHeader(response: web.Response, name: String, value: String) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.withHeader(web.Response(body = "ok"), "X-Web", "yes")
  ```


**Returns:** New Response value.

**Example**

```vlnc
import {std.web}
web.withHeader(web.Response(body = "ok"), "X-Web", "yes")
```

### `std.web.bodyBytes`

Decode a bounded buffered body according to its media type; return a typed web.Error for invalid input.

**Overloads**

- `std.web.bodyBytes(request: web.PostRequest) -> Result[#byte+ Int*, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyBytes($req) ?
  "ok"
end
  ```

- `std.web.bodyBytes(request: web.PutRequest) -> Result[#byte+ Int*, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyBytes($req) ?
  "ok"
end
  ```

- `std.web.bodyBytes(request: web.PatchRequest) -> Result[#byte+ Int*, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyBytes($req) ?
  "ok"
end
  ```

- `std.web.bodyBytes(request: web.QueryRequest) -> Result[#byte+ Int*, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyBytes($req) ?
  "ok"
end
  ```


**Returns:** Decoded content or a web.Error.

**Example**

```vlnc
import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyBytes($req) ?
  "ok"
end
```

### `std.web.bodyJson`

Decode bounded JSON content with the existing std.json codec.

**Overloads**

- `public define[T, R] bodyJson(request: R) -> Result[T, Error]`

  **Example**

  ```vlnc
  import {std.web} web.bodyJson[Int](web.testRequest("POST", "/"))
  ```


Decode bounded JSON content with the existing std.json codec.

Wrong media types return 415; malformed syntax and type mismatches return 400.

Method-capability checks currently occur at runtime.

**Parameters**

- `request` — Body-capable request whose Content-Type is application/json.

**Returns:** Parsed T or a typed web.Error.

**Example**

```vlnc
import {std.web} web.bodyJson[Int](web.testRequest("POST", "/"))
```

### `std.web.bodyJsonText`

Read a bounded UTF-8 body only when the request has a JSON media type. Parse with std.json.decode[T].

**Overloads**

- `std.web.bodyJsonText(request: web.PostRequest) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.post("/json"): fn(req: web.PostRequest) -> String | web.Error => web.bodyJsonText($req) end
  ```

- `std.web.bodyJsonText(request: web.PutRequest) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.post("/json"): fn(req: web.PostRequest) -> String | web.Error => web.bodyJsonText($req) end
  ```

- `std.web.bodyJsonText(request: web.PatchRequest) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.post("/json"): fn(req: web.PostRequest) -> String | web.Error => web.bodyJsonText($req) end
  ```

- `std.web.bodyJsonText(request: web.QueryRequest) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.post("/json"): fn(req: web.PostRequest) -> String | web.Error => web.bodyJsonText($req) end
  ```


**Returns:** Validated JSON text or web.Error.

**Example**

```vlnc
import {std.web}
$app = web.App
$app := web.post("/json"): fn(req: web.PostRequest) -> String | web.Error => web.bodyJsonText($req) end
```

### `std.web.bodyStream`

Claim one owned incoming body stream.

**Overloads**

- `std.web.bodyStream(request: web.PostRequest) -> Result[web.RequestBodyStream, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PostRequest) => web.bodyStream($req) end
  ```

- `std.web.bodyStream(request: web.PutRequest) -> Result[web.RequestBodyStream, web.Error]`

  Claim one owned incoming body stream.

  **Returns:** RequestBodyStream or Error.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PutRequest) => web.bodyStream($req) end
  ```

- `std.web.bodyStream(request: web.PatchRequest) -> Result[web.RequestBodyStream, web.Error]`

  Claim one owned incoming body stream.

  **Returns:** RequestBodyStream or Error.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PatchRequest) => web.bodyStream($req) end
  ```

- `std.web.bodyStream(request: web.QueryRequest) -> Result[web.RequestBodyStream, web.Error]`

  Claim one owned incoming body stream.

  **Returns:** RequestBodyStream or Error.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.QueryRequest) => web.bodyStream($req) end
  ```


**Returns:** RequestBodyStream or Error.

**Example**

```vlnc
import {std.web}
fn (req: web.PostRequest) => web.bodyStream($req) end
```

### `std.web.bodyText`

Decode a bounded buffered body according to its media type; return a typed web.Error for invalid input.

**Overloads**

- `std.web.bodyText(request: web.PostRequest) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyText($req) ?
  "ok"
end
  ```

- `std.web.bodyText(request: web.PutRequest) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyText($req) ?
  "ok"
end
  ```

- `std.web.bodyText(request: web.PatchRequest) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyText($req) ?
  "ok"
end
  ```

- `std.web.bodyText(request: web.QueryRequest) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyText($req) ?
  "ok"
end
  ```


**Returns:** Decoded content or a web.Error.

**Example**

```vlnc
import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.bodyText($req) ?
  "ok"
end
```

### `std.web.boundAddresses`

Read the bound TCP endpoint of an HTTP/1.1 development server.

**Overloads**

- `std.web.boundAddresses(server: web.ServerHandle) -> web.BoundEndpoint+`

  **Example**

  ```vlnc
  import {std.web}
$server = web.serve(web.App, web.ServerConfig(port = 0)) ?
  ```


**Returns:** BoundEndpoint list.

**Example**

```vlnc
import {std.web}
$server = web.serve(web.App, web.ServerConfig(port = 0)) ?
```

### `std.web.cachePrivate`

Set private cache policy with a nonnegative max-age.

**Overloads**

- `std.web.cachePrivate(response: web.Response, maxAge: Int) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.cachePrivate(web.Response(body = "ok"), 60)
  ```


**Returns:** New Response value.

**Example**

```vlnc
import {std.web}
web.cachePrivate(web.Response(body = "ok"), 60)
```

### `std.web.cachePublic`

Set public cache policy with a nonnegative max-age.

**Overloads**

- `std.web.cachePublic(response: web.Response, maxAge: Int) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.cachePublic(web.Response(body = "ok"), 60)
  ```


**Returns:** New Response value.

**Example**

```vlnc
import {std.web}
web.cachePublic(web.Response(body = "ok"), 60)
```

### `std.web.collectBody`

Collect a buffered HTTP representation as byte-tagged integers, subject to a limit.

**Overloads**

- `std.web.collectBody(response: web.Response, limit: Int) -> Result[#byte+ Int*, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.collectBody(web.Response(body = "ok"))
  ```


**Returns:** Exact bytes or a typed web.Error.

**Example**

```vlnc
import {std.web}
web.collectBody(web.Response(body = "ok"))
```

### `std.web.compression`

Configure opt-in, bounded gzip/Brotli response compression.

**Overloads**

- `std.web.compression(policy: web.CompressionPolicy) -> web.CompressionMiddleware`

  **Example**

  ```vlnc
  import {std.web, std.web.App}
$app = App
$app := middleware web.compression(web.CompressionPolicy)
  ```


**Returns:** Validated compression middleware.

**Example**

```vlnc
import {std.web, std.web.App}
$app = App
$app := middleware web.compression(web.CompressionPolicy)
```

### `std.web.conditional`

Apply safe GET/HEAD conditional representation selection.

**Overloads**

- `std.web.conditional(request: web.GetRequest, response: web.Response) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.get("/"): fn(req: web.GetRequest) =>
  web.conditional($req, web.withETag(web.Response(body = "ok"), "v1"))
end
  ```

- `std.web.conditional(request: web.HeadRequest, response: web.Response) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.get("/"): fn(req: web.GetRequest) =>
  web.conditional($req, web.withETag(web.Response(body = "ok"), "v1"))
end
  ```


**Returns:** Original response or 304 Not Modified.

**Example**

```vlnc
import {std.web}
$app = web.App
$app := web.get("/"): fn(req: web.GetRequest) =>
  web.conditional($req, web.withETag(web.Response(body = "ok"), "v1"))
end
```

### `std.web.context`

Read a typed context value, returning None when the key is absent.

**Overloads**

- `std.web.context(request: web.Request, key: web.ContextKey[T]) -> T?`

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.Request) => web.context($req, web.contextKey[String]) end
  ```

- `std.web.context(request: web.GetRequest, key: web.ContextKey[T]) -> T?`

  Read a typed context value, returning None when the key is absent.

  **Returns:** An optional value of the key type.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.GetRequest) => web.context($req, web.contextKey[String]) end
  ```

- `std.web.context(request: web.HeadRequest, key: web.ContextKey[T]) -> T?`

  Read a typed context value, returning None when the key is absent.

  **Returns:** An optional value of the key type.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.HeadRequest) => web.context($req, web.contextKey[String]) end
  ```

- `std.web.context(request: web.PostRequest, key: web.ContextKey[T]) -> T?`

  Read a typed context value, returning None when the key is absent.

  **Returns:** An optional value of the key type.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PostRequest) => web.context($req, web.contextKey[String]) end
  ```

- `std.web.context(request: web.PutRequest, key: web.ContextKey[T]) -> T?`

  Read a typed context value, returning None when the key is absent.

  **Returns:** An optional value of the key type.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PutRequest) => web.context($req, web.contextKey[String]) end
  ```

- `std.web.context(request: web.PatchRequest, key: web.ContextKey[T]) -> T?`

  Read a typed context value, returning None when the key is absent.

  **Returns:** An optional value of the key type.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PatchRequest) => web.context($req, web.contextKey[String]) end
  ```

- `std.web.context(request: web.DeleteRequest, key: web.ContextKey[T]) -> T?`

  Read a typed context value, returning None when the key is absent.

  **Returns:** An optional value of the key type.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.DeleteRequest) => web.context($req, web.contextKey[String]) end
  ```

- `std.web.context(request: web.OptionsRequest, key: web.ContextKey[T]) -> T?`

  Read a typed context value, returning None when the key is absent.

  **Returns:** An optional value of the key type.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.OptionsRequest) => web.context($req, web.contextKey[String]) end
  ```

- `std.web.context(request: web.QueryRequest, key: web.ContextKey[T]) -> T?`

  Read a typed context value, returning None when the key is absent.

  **Returns:** An optional value of the key type.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.QueryRequest) => web.context($req, web.contextKey[String]) end
  ```

- `std.web.context(request: web.WebSocketRequest, key: web.ContextKey[T]) -> T?`

  Read a typed context value, returning None when the key is absent.

  **Returns:** An optional value of the key type.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.WebSocketRequest) => web.context($req, web.contextKey[String]) end
  ```


**Returns:** An optional value of the key type.

**Example**

```vlnc
import {std.web}
fn (req: web.Request) => web.context($req, web.contextKey[String]) end
```

### `std.web.contextKey`

Mint a unique, copyable typed request-context key.

**Overloads**

- `std.web.contextKey() -> web.ContextKey[T]`

  **Example**

  ```vlnc
  import {std.web}
web.contextKey[String]
  ```


**Returns:** An opaque ContextKey[T] with unique identity.

**Example**

```vlnc
import {std.web}
web.contextKey[String]
```

### `std.web.cookie`

Read one Cookie value, reporting malformed or ambiguous names.

**Overloads**

- `std.web.cookie(request: web.Request, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
  ```

- `std.web.cookie(request: web.GetRequest, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
  ```

- `std.web.cookie(request: web.HeadRequest, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
  ```

- `std.web.cookie(request: web.PostRequest, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
  ```

- `std.web.cookie(request: web.PutRequest, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
  ```

- `std.web.cookie(request: web.PatchRequest, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
  ```

- `std.web.cookie(request: web.DeleteRequest, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
  ```

- `std.web.cookie(request: web.OptionsRequest, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
  ```

- `std.web.cookie(request: web.QueryRequest, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
  ```


**Returns:** Optional value or Cookie error.

**Example**

```vlnc
import {std.web}
web.cookie(web.testRequest("GET", "/"), "sid")
```

### `std.web.cookies`

Parse ordered Cookie request pairs without choosing duplicate names.

**Overloads**

- `std.web.cookies(request: web.Request) -> Result[web.CookiePairs, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookies(web.testRequest("GET", "/"))
  ```

- `std.web.cookies(request: web.GetRequest) -> Result[web.CookiePairs, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookies(web.testRequest("GET", "/"))
  ```

- `std.web.cookies(request: web.HeadRequest) -> Result[web.CookiePairs, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookies(web.testRequest("GET", "/"))
  ```

- `std.web.cookies(request: web.PostRequest) -> Result[web.CookiePairs, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookies(web.testRequest("GET", "/"))
  ```

- `std.web.cookies(request: web.PutRequest) -> Result[web.CookiePairs, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookies(web.testRequest("GET", "/"))
  ```

- `std.web.cookies(request: web.PatchRequest) -> Result[web.CookiePairs, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookies(web.testRequest("GET", "/"))
  ```

- `std.web.cookies(request: web.DeleteRequest) -> Result[web.CookiePairs, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookies(web.testRequest("GET", "/"))
  ```

- `std.web.cookies(request: web.OptionsRequest) -> Result[web.CookiePairs, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookies(web.testRequest("GET", "/"))
  ```

- `std.web.cookies(request: web.QueryRequest) -> Result[web.CookiePairs, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.cookies(web.testRequest("GET", "/"))
  ```


**Returns:** CookiePairs or InvalidCookie.

**Example**

```vlnc
import {std.web}
web.cookies(web.testRequest("GET", "/"))
```

### `std.web.cors`

Create validated, opt-in CORS middleware for App/Router registration.

**Overloads**

- `std.web.cors(policy: web.CorsPolicy) -> web.CorsMiddleware`

  **Example**

  ```vlnc
  import {std.web, std.web.App}
$app = App
$app := middleware web.cors(web.CorsPolicy(allowedOrigins = ["https://example.com"], allowedMethods = [web.Method.GET]))
  ```


**Returns:** CORS middleware policy value.

**Example**

```vlnc
import {std.web, std.web.App}
$app = App
$app := middleware web.cors(web.CorsPolicy(allowedOrigins = ["https://example.com"], allowedMethods = [web.Method.GET]))
```

### `std.web.created`

Create a 201 response with a validated Location, optionally preserving a response body.

**Overloads**

- `std.web.created(location: String, response: web.Response) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.created("/new")
  ```


**Returns:** Created Response.

**Example**

```vlnc
import {std.web}
web.created("/new")
```

### `std.web.defaultErrorResponse`

Return the safe default HTTP error representation for an ErrorContext.

**Overloads**

- `std.web.defaultErrorResponse(context: web.ErrorContext) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
fn (err: web.ErrorContext) => web.defaultErrorResponse($err) end
  ```


**Returns:** Response.

**Example**

```vlnc
import {std.web}
fn (err: web.ErrorContext) => web.defaultErrorResponse($err) end
```

### `std.web.delete`

Register a method-specific DELETE handler on an immutable web configuration.

**Overloads**

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> String]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> web.Response]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> html.Node]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> web.Error]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> Result[String, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> Result[web.Response, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> Result[html.Node, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> ValueError]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> Result[String, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> Result[web.Response, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> Result[html.Node, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> Result[String, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> Result[web.Response, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.App, path: String, handler: Function[web.DeleteRequest -> Result[html.Node, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> String]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> web.Response]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> html.Node]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> web.Error]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> Result[String, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> Result[web.Response, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> Result[html.Node, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> ValueError]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> Result[String, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> Result[web.Response, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> Result[html.Node, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> Result[String, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> Result[web.Response, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```

- `std.web.delete(target: web.Router, path: String, handler: Function[web.DeleteRequest -> Result[html.Node, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
  ```


**Parameters**

- `target` — Application or reusable Router.
- `path` — Absolute route template.
- `handler` — Typed request callback returning text or Response.

**Returns:** A new App or Router; the original remains unchanged.

**Example**

```vlnc
import {std.web, std.web.[delete]}
$app = web.App
$app := delete("/"): fn (req: web.DeleteRequest) => "ok" end
```

### `std.web.deleteCookie`

Expire a named cookie with caller-selected matching path, domain and partition scope.

**Overloads**

- `std.web.deleteCookie(response: web.Response, name: String, path: String, domain: String?, partitioned: #boolean Int) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.deleteCookie(web.Response, "sid")
  ```


**Returns:** Response or typed cookie error.

**Example**

```vlnc
import {std.web}
web.deleteCookie(web.Response, "sid")
```

### `std.web.discardBodyStream`

Discard a streaming request body, up to its limit.

**Overloads**

- `std.web.discardBodyStream(stream: web.RequestBodyStream) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (stream: web.RequestBodyStream) => web.discardBodyStream($stream) end
  ```


**Returns:** None or Error.

**Example**

```vlnc
import {std.web}
fn (stream: web.RequestBodyStream) => web.discardBodyStream($stream) end
```

### `std.web.dispatch`

Execute the same transport-independent routing pipeline for an App or prepared App.

**Overloads**

- `std.web.dispatch(app: web.App, request: web.Request) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.dispatch(web.App, web.testRequest("GET", "/"))
  ```

- `std.web.dispatch(prepared: web.PreparedApp, request: web.Request) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.dispatch(web.App, web.testRequest("GET", "/"))
  ```


**Returns:** A normalized HTTP Response.

**Example**

```vlnc
import {std.web}
web.dispatch(web.App, web.testRequest("GET", "/"))
```

### `std.web.download`

Read a regular file into a bounded binary Response (16 MiB development limit).

**Overloads**

- `std.web.download(path: String, filename: String) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.download("public/example.txt", "example.txt")
  ```

- `std.web.download(path: path.Path, filename: String) -> Result[web.Response, web.Error]`

  Serve an application-selected std.path.Path as a bounded binary response.

  **Returns:** Response or web.Error.

  **Example**

  ```vlnc
  import {std.web, std.path.Path}
web.download(Path("public/example.txt"), "example.txt")
  ```


**Returns:** Response or web.Error.

**Example**

```vlnc
import {std.web}
web.download("public/example.txt", "example.txt")
```

### `std.web.eventStream`

Encode owned EventSource events as bounded text/event-stream chunks.

**Overloads**

- `std.web.eventStream(source: web.EventSource) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
fn (source: web.EventSource) => web.eventStream($source) end
  ```


**Returns:** An ordinary streaming Response.

**Example**

```vlnc
import {std.web}
fn (source: web.EventSource) => web.eventStream($source) end
```

### `std.web.file`

Read a regular file into a bounded binary Response (16 MiB development limit).

**Overloads**

- `std.web.file(path: String) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.file("public/example.txt")
  ```

- `std.web.file(path: path.Path) -> Result[web.Response, web.Error]`

  Serve an application-selected std.path.Path as a bounded binary response.

  **Returns:** Response or web.Error.

  **Example**

  ```vlnc
  import {std.web, std.path.Path}
web.file(Path("public/example.txt"))
  ```


**Returns:** Response or web.Error.

**Example**

```vlnc
import {std.web}
web.file("public/example.txt")
```

### `std.web.formParams`

Decode a bounded buffered body according to its media type; return a typed web.Error for invalid input.

**Overloads**

- `std.web.formParams(request: web.PostRequest) -> Result[web.Params, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.formParams($req) ?
  "ok"
end
  ```

- `std.web.formParams(request: web.PutRequest) -> Result[web.Params, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.formParams($req) ?
  "ok"
end
  ```

- `std.web.formParams(request: web.PatchRequest) -> Result[web.Params, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.formParams($req) ?
  "ok"
end
  ```

- `std.web.formParams(request: web.QueryRequest) -> Result[web.Params, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.formParams($req) ?
  "ok"
end
  ```


**Returns:** Decoded content or a web.Error.

**Example**

```vlnc
import {std.web, std.web.[post]}
$app = web.App
$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>
  $value = web.formParams($req) ?
  "ok"
end
```

### `std.web.get`

Register a method-specific GET handler on an immutable web configuration.

**Overloads**

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> String]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> web.Response]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> html.Node]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> web.Error]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> Result[String, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> Result[web.Response, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> Result[html.Node, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> ValueError]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> Result[String, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> Result[web.Response, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> Result[html.Node, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> Result[String, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> Result[web.Response, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.App, path: String, handler: Function[web.GetRequest -> Result[html.Node, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> String]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> web.Response]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> html.Node]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> web.Error]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> Result[String, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> Result[web.Response, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> Result[html.Node, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> ValueError]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> Result[String, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> Result[web.Response, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> Result[html.Node, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> Result[String, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> Result[web.Response, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```

- `std.web.get(target: web.Router, path: String, handler: Function[web.GetRequest -> Result[html.Node, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
  ```


**Parameters**

- `target` — Application or reusable Router.
- `path` — Absolute route template.
- `handler` — Typed request callback returning text or Response.

**Returns:** A new App or Router; the original remains unchanged.

**Example**

```vlnc
import {std.web, std.web.[get]}
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "ok" end
```

### `std.web.group`

Mount a reusable Router at a path prefix, with scoped router middleware.

**Overloads**

- `std.web.group(target: web.App, prefix: String, router: web.Router) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
web.group(web.App, "/api", web.Router)
  ```

- `std.web.group(target: web.App, prefix: String, router: Function[web.Router -> web.Router]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
web.group(web.App, "/api", web.Router)
  ```

- `std.web.group(target: web.Router, prefix: String, router: web.Router) -> web.Router`

  **Example**

  ```vlnc
  import {std.web}
web.group(web.App, "/api", web.Router)
  ```

- `std.web.group(target: web.Router, prefix: String, router: Function[web.Router -> web.Router]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web}
web.group(web.App, "/api", web.Router)
  ```


**Returns:** New immutable App or Router.

**Example**

```vlnc
import {std.web}
web.group(web.App, "/api", web.Router)
```

### `std.web.head`

Register a method-specific HEAD handler on an immutable web configuration.

**Overloads**

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> String]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> web.Response]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> html.Node]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> web.Error]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> Result[String, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> Result[web.Response, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> Result[html.Node, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> ValueError]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> Result[String, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> Result[web.Response, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> Result[html.Node, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> Result[String, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> Result[web.Response, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.App, path: String, handler: Function[web.HeadRequest -> Result[html.Node, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> String]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> web.Response]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> html.Node]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> web.Error]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> Result[String, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> Result[web.Response, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> Result[html.Node, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> ValueError]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> Result[String, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> Result[web.Response, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> Result[html.Node, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> Result[String, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> Result[web.Response, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```

- `std.web.head(target: web.Router, path: String, handler: Function[web.HeadRequest -> Result[html.Node, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
  ```


**Parameters**

- `target` — Application or reusable Router.
- `path` — Absolute route template.
- `handler` — Typed request callback returning text or Response.

**Returns:** A new App or Router; the original remains unchanged.

**Example**

```vlnc
import {std.web, std.web.[head]}
$app = web.App
$app := head("/"): fn (req: web.HeadRequest) => "ok" end
```

### `std.web.header`

Read one validated Headers value, rejecting ambiguous singleton fields.

**Overloads**

- `std.web.header(headers: web.Headers, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.header(web.Headers, "Accept")
  ```

- `std.web.header(requestOrResponse: web.Request, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.header(requestOrResponse: web.GetRequest, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.header(requestOrResponse: web.HeadRequest, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.header(requestOrResponse: web.PostRequest, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.header(requestOrResponse: web.PutRequest, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.header(requestOrResponse: web.PatchRequest, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.header(requestOrResponse: web.DeleteRequest, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.header(requestOrResponse: web.OptionsRequest, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.header(requestOrResponse: web.QueryRequest, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.header(requestOrResponse: web.Response, name: String) -> Result[String?, web.Error]`

  Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.

  **Returns:** Optional String or typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
web.header(web.testRequest("GET", "/"), "Accept")
  ```


**Returns:** Optional value or typed Error.

**Example**

```vlnc
import {std.web}
web.header(web.Headers, "Accept")
```

### `std.web.headerValues`

Get all raw field occurrences by case-insensitive header name.

**Overloads**

- `std.web.headerValues(request: web.Request, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.headerValues(request: web.GetRequest, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.headerValues(request: web.HeadRequest, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.headerValues(request: web.PostRequest, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.headerValues(request: web.PutRequest, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.headerValues(request: web.PatchRequest, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.headerValues(request: web.DeleteRequest, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.headerValues(request: web.OptionsRequest, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.headerValues(request: web.QueryRequest, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
  ```

- `std.web.headerValues(headers: web.Headers, name: String) -> String+`

  Read all values of a validated Headers collection, including request trailers.

  **Returns:** Values in field arrival order.

  **Example**

  ```vlnc
  import {std.web}
web.headerValues(web.Headers, "Accept")
  ```


**Returns:** List of values in arrival order.

**Example**

```vlnc
import {std.web}
web.headerValues(web.testRequest("GET", "/"), "Accept")
```

### `std.web.jsonResponse`

Encode a value using std.json and return a JSON HTTP response.

**Overloads**

- `std.web.jsonResponse(value: T, status: web.Status, headers: web.Headers) -> Result[web.Response, ValueError]`

  **Example**

  ```vlnc
  import {std.web}
web.jsonResponse(record{name => "Ada"})
  ```


**Returns:** Response or std.json ValueError.

**Example**

```vlnc
import {std.web}
web.jsonResponse(record{name => "Ada"})
```

### `std.web.middleware`

Register before middleware, optionally returning a {request, after} record whose response callback runs in reverse order.

**Overloads**

- `std.web.middleware(target: web.App, callback: Function[web.Request -> web.Request]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.App, callback: Function[web.Request -> web.Response]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.App, callback: Function[web.Request -> web.Error]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.App, callback: Function[web.Request -> Result[web.Request, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.App, callback: Function[web.Request -> Result[web.Response, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.App, callback: Function[web.Request -> record(.request: web.Request, .after: Function[web.Response -> web.Response])]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.App, callback: Function[web.Request -> record(.request: web.Request, .after: Function[web.Response -> Result[web.Response, web.Error]])]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.Router, callback: Function[web.Request -> web.Request]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.Router, callback: Function[web.Request -> web.Response]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.Router, callback: Function[web.Request -> web.Error]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.Router, callback: Function[web.Request -> Result[web.Request, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.Router, callback: Function[web.Request -> Result[web.Response, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.Router, callback: Function[web.Request -> record(.request: web.Request, .after: Function[web.Response -> web.Response])]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.Router, callback: Function[web.Request -> record(.request: web.Request, .after: Function[web.Response -> Result[web.Response, web.Error]])]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
  ```

- `std.web.middleware(target: web.App, callback: web.CorsMiddleware) -> web.App`

  Register validated CORS middleware on an immutable App or Router.

  **Returns:** Updated App or Router.

  **Example**

  ```vlnc
  import {std.web}
$config = web.App
$config := web.middleware web.cors(web.CorsPolicy(allowedOrigins = ["https://example.com"], allowedMethods = [web.Method.GET]))
  ```

- `std.web.middleware(target: web.Router, callback: web.CorsMiddleware) -> web.Router`

  Register validated CORS middleware on an immutable App or Router.

  **Returns:** Updated App or Router.

  **Example**

  ```vlnc
  import {std.web}
$config = web.Router
$config := web.middleware web.cors(web.CorsPolicy(allowedOrigins = ["https://example.com"], allowedMethods = [web.Method.GET]))
  ```

- `std.web.middleware(target: web.App, callback: web.CompressionMiddleware) -> web.App`

  Register opt-in response compression on App or Router.

  **Returns:** New immutable App or Router.

  **Example**

  ```vlnc
  import {std.web}
$config = web.App
$config := web.middleware web.compression(web.CompressionPolicy)
  ```

- `std.web.middleware(target: web.Router, callback: web.CompressionMiddleware) -> web.Router`

  Register opt-in response compression on App or Router.

  **Returns:** New immutable App or Router.

  **Example**

  ```vlnc
  import {std.web}
$config = web.Router
$config := web.middleware web.compression(web.CompressionPolicy)
  ```


**Returns:** New immutable App or Router.

**Example**

```vlnc
import {std.web}
$app = web.App
$app := web.middleware: fn(req: web.Request) => $req end
```

### `std.web.multipart`

Parse a bounded buffered multipart/form-data body with repeated UTF-8 fields and exact file bytes.

**Overloads**

- `std.web.multipart(request: web.PostRequest) -> Result[web.MultipartForm, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $form = web.multipart($req) ?
  web.requireParam(web.multipartFields($form), "name") ?
end
  ```

- `std.web.multipart(request: web.PutRequest) -> Result[web.MultipartForm, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $form = web.multipart($req) ?
  web.requireParam(web.multipartFields($form), "name") ?
end
  ```

- `std.web.multipart(request: web.PatchRequest) -> Result[web.MultipartForm, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $form = web.multipart($req) ?
  web.requireParam(web.multipartFields($form), "name") ?
end
  ```

- `std.web.multipart(request: web.QueryRequest) -> Result[web.MultipartForm, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $form = web.multipart($req) ?
  web.requireParam(web.multipartFields($form), "name") ?
end
  ```


**Returns:** MultipartForm or web.Error.

**Example**

```vlnc
import {std.web}
$app = web.App
$app := web.post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>
  $form = web.multipart($req) ?
  web.requireParam(web.multipartFields($form), "name") ?
end
```

### `std.web.multipartDiscard`

Discard the current upload with bounded reads to its next MIME boundary.

**Overloads**

- `std.web.multipartDiscard(stream: web.UploadStream) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (stream: web.UploadStream) => web.multipartDiscard($stream) end
  ```


**Returns:** None or web.Error.

**Example**

```vlnc
import {std.web}
fn (stream: web.UploadStream) => web.multipartDiscard($stream) end
```

### `std.web.multipartFields`

Read the ordered field Params of a buffered multipart form.

**Overloads**

- `std.web.multipartFields(form: web.MultipartForm) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
fn (form: web.MultipartForm) => web.multipartFields($form) end
  ```


**Returns:** Params preserving all repeated text fields.

**Example**

```vlnc
import {std.web}
fn (form: web.MultipartForm) => web.multipartFields($form) end
```

### `std.web.multipartNext`

Advance to the next multipart field/file event (None at EOF). The previous file must already be consumed or discarded.

**Overloads**

- `std.web.multipartNext(stream: web.UploadStream) -> Result[web.MultipartPart?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (stream: web.UploadStream) => web.multipartNext($stream) end
  ```


**Returns:** Optional MultipartPart or web.Error.

**Example**

```vlnc
import {std.web}
fn (stream: web.UploadStream) => web.multipartNext($stream) end
```

### `std.web.multipartRead`

Read up to 64 KiB of the active upload; None means part EOF.

**Overloads**

- `std.web.multipartRead(stream: web.UploadStream, maxBytes: Int) -> Result[#byte+ Int*?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (stream: web.UploadStream) => web.multipartRead($stream, 4096) end
  ```


**Returns:** Exact bytes, None or web.Error.

**Example**

```vlnc
import {std.web}
fn (stream: web.UploadStream) => web.multipartRead($stream, 4096) end
```

### `std.web.multipartStream`

Claim a streaming multipart/form-data request once. Iterate ordered field/file events and consume every file before advancing.

**Overloads**

- `std.web.multipartStream(request: web.PostRequest) -> Result[web.UploadStream, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PostRequest) => web.multipartStream($req) end
  ```

- `std.web.multipartStream(request: web.PutRequest) -> Result[web.UploadStream, web.Error]`

  Claim a streaming multipart/form-data request once. Iterate ordered field/file events and consume every file before advancing.

  **Returns:** Owned UploadStream or web.Error.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PutRequest) => web.multipartStream($req) end
  ```

- `std.web.multipartStream(request: web.PatchRequest) -> Result[web.UploadStream, web.Error]`

  Claim a streaming multipart/form-data request once. Iterate ordered field/file events and consume every file before advancing.

  **Returns:** Owned UploadStream or web.Error.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PatchRequest) => web.multipartStream($req) end
  ```

- `std.web.multipartStream(request: web.QueryRequest) -> Result[web.UploadStream, web.Error]`

  Claim a streaming multipart/form-data request once. Iterate ordered field/file events and consume every file before advancing.

  **Returns:** Owned UploadStream or web.Error.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.QueryRequest) => web.multipartStream($req) end
  ```


**Returns:** Owned UploadStream or web.Error.

**Example**

```vlnc
import {std.web}
fn (req: web.PostRequest) => web.multipartStream($req) end
```

### `std.web.noStore`

Set Cache-Control: no-store on a response.

**Overloads**

- `std.web.noStore(response: web.Response) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.noStore(web.Response(body = "ok"))
  ```


**Returns:** New Response value.

**Example**

```vlnc
import {std.web}
web.noStore(web.Response(body = "ok"))
```

### `std.web.options`

Register a method-specific OPTIONS handler on an immutable web configuration.

**Overloads**

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> String]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> web.Response]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> html.Node]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> web.Error]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> Result[String, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> Result[web.Response, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> Result[html.Node, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> ValueError]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> Result[String, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> Result[web.Response, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> Result[html.Node, ValueError]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> Result[String, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> Result[web.Response, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.App, path: String, handler: Function[web.OptionsRequest -> Result[html.Node, ValueError | web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> String]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> web.Response]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> html.Node]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> web.Error]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> Result[String, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> Result[web.Response, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> Result[html.Node, web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> ValueError]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> Result[String, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> Result[web.Response, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> Result[html.Node, ValueError]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> Result[String, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> Result[web.Response, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```

- `std.web.options(target: web.Router, path: String, handler: Function[web.OptionsRequest -> Result[html.Node, ValueError | web.Error]]) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
  ```


**Parameters**

- `target` — Application or reusable Router.
- `path` — Absolute route template.
- `handler` — Typed request callback returning text or Response.

**Returns:** A new App or Router; the original remains unchanged.

**Example**

```vlnc
import {std.web, std.web.[options]}
$app = web.App
$app := options("/"): fn (req: web.OptionsRequest) => "ok" end
```

### `std.web.page`

Read a regular file into a bounded binary Response (16 MiB development limit).

**Overloads**

- `std.web.page(path: String) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.page("public/example.txt")
  ```

- `std.web.page(path: path.Path) -> Result[web.Response, web.Error]`

  Serve an application-selected std.path.Path as a bounded binary response.

  **Returns:** Response or web.Error.

  **Example**

  ```vlnc
  import {std.web, std.path.Path}
web.page(Path("public/example.txt"))
  ```


**Returns:** Response or web.Error.

**Example**

```vlnc
import {std.web}
web.page("public/example.txt")
```

### `std.web.paramOne`

Retrieve exactly one named parameter without silently discarding duplicates.

**Overloads**

- `std.web.paramOne(params: web.Params, name: String) -> Result[String?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.paramOne(web.queryParams(web.testRequest("GET", "/?q=one")), "q")
  ```


**Returns:** Value or typed HTTP parameter error.

**Example**

```vlnc
import {std.web}
web.paramOne(web.queryParams(web.testRequest("GET", "/?q=one")), "q")
```

### `std.web.paramValues`

Retrieve every matching parameter in original order.

**Overloads**

- `std.web.paramValues(params: web.Params, name: String) -> String+`

  **Example**

  ```vlnc
  import {std.web}
web.paramValues(web.queryParams(web.testRequest("GET", "/?q=1")), "q")
  ```


**Returns:** Possibly empty list of strings.

**Example**

```vlnc
import {std.web}
web.paramValues(web.queryParams(web.testRequest("GET", "/?q=1")), "q")
```

### `std.web.patch`

Register a method-specific PATCH handler on an immutable web configuration.

**Overloads**

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> String], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> web.Response], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> html.Node], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> web.Error], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> Result[String, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> Result[web.Response, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> Result[html.Node, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> ValueError], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> Result[String, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> Result[web.Response, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> Result[html.Node, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> Result[String, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> Result[web.Response, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.App, path: String, handler: Function[web.PatchRequest -> Result[html.Node, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> String], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> web.Response], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> html.Node], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> web.Error], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> Result[String, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> Result[web.Response, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> Result[html.Node, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> ValueError], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> Result[String, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> Result[web.Response, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> Result[html.Node, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> Result[String, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> Result[web.Response, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```

- `std.web.patch(target: web.Router, path: String, handler: Function[web.PatchRequest -> Result[html.Node, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
  ```


**Parameters**

- `target` — Application or reusable Router.
- `path` — Absolute route template.
- `handler` — Typed request callback returning text or Response.

**Returns:** A new App or Router; the original remains unchanged.

**Example**

```vlnc
import {std.web, std.web.[patch]}
$app = web.App
$app := patch("/"): fn (req: web.PatchRequest) => "ok" end
```

### `std.web.post`

Register a method-specific POST handler on an immutable web configuration.

**Overloads**

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> String], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> web.Response], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> html.Node], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> web.Error], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> Result[String, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> Result[web.Response, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> Result[html.Node, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> ValueError], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> Result[String, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> Result[web.Response, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> Result[html.Node, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> Result[String, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> Result[web.Response, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.App, path: String, handler: Function[web.PostRequest -> Result[html.Node, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> String], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> web.Response], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> html.Node], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> web.Error], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> Result[String, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> Result[web.Response, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> Result[html.Node, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> ValueError], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> Result[String, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> Result[web.Response, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> Result[html.Node, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> Result[String, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> Result[web.Response, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```

- `std.web.post(target: web.Router, path: String, handler: Function[web.PostRequest -> Result[html.Node, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
  ```


**Parameters**

- `target` — Application or reusable Router.
- `path` — Absolute route template.
- `handler` — Typed request callback returning text or Response.

**Returns:** A new App or Router; the original remains unchanged.

**Example**

```vlnc
import {std.web, std.web.[post]}
$app = web.App
$app := post("/"): fn (req: web.PostRequest) => "ok" end
```

### `std.web.preconditions`

Evaluate strong If-Match, If-Unmodified-Since and mutation If-None-Match before state changes.

**Overloads**

- `std.web.preconditions(request: web.Request, validators: web.Validators) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
  ```

- `std.web.preconditions(request: web.GetRequest, validators: web.Validators) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
  ```

- `std.web.preconditions(request: web.HeadRequest, validators: web.Validators) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
  ```

- `std.web.preconditions(request: web.PostRequest, validators: web.Validators) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
  ```

- `std.web.preconditions(request: web.PutRequest, validators: web.Validators) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
  ```

- `std.web.preconditions(request: web.PatchRequest, validators: web.Validators) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
  ```

- `std.web.preconditions(request: web.DeleteRequest, validators: web.Validators) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
  ```

- `std.web.preconditions(request: web.OptionsRequest, validators: web.Validators) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
  ```

- `std.web.preconditions(request: web.QueryRequest, validators: web.Validators) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
  ```


**Returns:** None on success or a typed HTTP error.

**Example**

```vlnc
import {std.web}
web.preconditions(web.testRequest("PUT", "/"), web.Validators)
```

### `std.web.prepare`

Validate the immutable route table without starting a listener.

**Overloads**

- `std.web.prepare(app: web.App) -> Result[web.PreparedApp, web.ConfigError]`

  **Example**

  ```vlnc
  import {std.web}
web.prepare(web.App)
  ```


**Returns:** PreparedApp or ConfigError.

**Example**

```vlnc
import {std.web}
web.prepare(web.App)
```

### `std.web.put`

Register a method-specific PUT handler on an immutable web configuration.

**Overloads**

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> String], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> web.Response], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> html.Node], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> web.Error], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> Result[String, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> Result[web.Response, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> Result[html.Node, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> ValueError], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> Result[String, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> Result[web.Response, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> Result[html.Node, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> Result[String, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> Result[web.Response, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.App, path: String, handler: Function[web.PutRequest -> Result[html.Node, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> String], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> web.Response], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> html.Node], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> web.Error], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> Result[String, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> Result[web.Response, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> Result[html.Node, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> ValueError], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> Result[String, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> Result[web.Response, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> Result[html.Node, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> Result[String, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> Result[web.Response, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```

- `std.web.put(target: web.Router, path: String, handler: Function[web.PutRequest -> Result[html.Node, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
  ```


**Parameters**

- `target` — Application or reusable Router.
- `path` — Absolute route template.
- `handler` — Typed request callback returning text or Response.

**Returns:** A new App or Router; the original remains unchanged.

**Example**

```vlnc
import {std.web, std.web.[put]}
$app = web.App
$app := put("/"): fn (req: web.PutRequest) => "ok" end
```

### `std.web.query`

Register a method-specific QUERY handler on an immutable web configuration.

**Overloads**

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> String], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> web.Response], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> html.Node], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> web.Error], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> Result[String, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> Result[web.Response, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> Result[html.Node, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> ValueError], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> Result[String, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> Result[web.Response, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> Result[html.Node, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> Result[String, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> Result[web.Response, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.App, path: String, handler: Function[web.QueryRequest -> Result[html.Node, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.App`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> String], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> web.Response], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> html.Node], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> web.Error], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> Result[String, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> Result[web.Response, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> Result[html.Node, web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> ValueError], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> Result[String, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> Result[web.Response, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> Result[html.Node, ValueError]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> Result[String, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> Result[web.Response, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```

- `std.web.query(target: web.Router, path: String, handler: Function[web.QueryRequest -> Result[html.Node, ValueError | web.Error]], bodyMode: web.BodyMode, maxBodyBytes: Int?) -> web.Router`

  **Example**

  ```vlnc
  import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
  ```


**Parameters**

- `target` — Application or reusable Router.
- `path` — Absolute route template.
- `handler` — Typed request callback returning text or Response.

**Returns:** A new App or Router; the original remains unchanged.

**Example**

```vlnc
import {std.web, std.web.[query]}
$app = web.App
$app := query("/"): fn (req: web.QueryRequest) => "ok" end
```

### `std.web.queryParams`

Read the ordered query multimap without merging other parameter sources.

**Overloads**

- `std.web.queryParams(request: web.Request) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
  ```

- `std.web.queryParams(request: web.GetRequest) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
  ```

- `std.web.queryParams(request: web.HeadRequest) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
  ```

- `std.web.queryParams(request: web.PostRequest) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
  ```

- `std.web.queryParams(request: web.PutRequest) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
  ```

- `std.web.queryParams(request: web.PatchRequest) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
  ```

- `std.web.queryParams(request: web.DeleteRequest) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
  ```

- `std.web.queryParams(request: web.OptionsRequest) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
  ```

- `std.web.queryParams(request: web.QueryRequest) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
  ```


**Returns:** Immutable Params value.

**Example**

```vlnc
import {std.web}
web.queryParams(web.testRequest("GET", "/?q=1"))
```

### `std.web.readBodyStream`

Read up to 64 KiB of request bytes; None means EOF.

**Overloads**

- `std.web.readBodyStream(stream: web.RequestBodyStream, maxBytes: Int) -> Result[#byte+ Int* | None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (stream: web.RequestBodyStream) => web.readBodyStream($stream, 4096) end
  ```


**Returns:** Exact bytes or None or Error.

**Example**

```vlnc
import {std.web}
fn (stream: web.RequestBodyStream) => web.readBodyStream($stream, 4096) end
```

### `std.web.readUpload`

Read exact bytes of a bounded buffered upload, without filename-based path access.

**Overloads**

- `std.web.readUpload(upload: web.Upload) -> Result[#byte+ Int*, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (upload: web.Upload) => web.readUpload($upload) end
  ```


**Returns:** Byte-tagged octets or web.Error.

**Example**

```vlnc
import {std.web}
fn (upload: web.Upload) => web.readUpload($upload) end
```

### `std.web.redirect`

Create a same-origin redirect (default 303) with a validated target and optional status.

**Overloads**

- `std.web.redirect(location: String, status: web.Status) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.redirect("/next")
  ```


**Returns:** Response with Location header.

**Example**

```vlnc
import {std.web}
web.redirect("/next")
```

### `std.web.reloadTLS`

Atomically rotate validated TLS 1.3 credentials on a TLS listener; existing connections retain their original negotiated identity.

**Overloads**

- `std.web.reloadTLS(server: web.ServerHandle, tls: web.TLS) -> Result[None, web.ServerError]`

  **Example**

  ```vlnc
  import {std.web}
fn (server: web.ServerHandle, tls: web.TLS) => web.reloadTLS($server, $tls) end
  ```


**Returns:** None or ServerError.

**Example**

```vlnc
import {std.web}
fn (server: web.ServerHandle, tls: web.TLS) => web.reloadTLS($server, $tls) end
```

### `std.web.replaceParam`

Replace all occurrences of a parameter with one value.

**Overloads**

- `std.web.replaceParam(params: web.Params, name: String, value: String) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.replaceParam(web.queryParams(web.testRequest("GET", "/?q=1")), "q", "2")
  ```


**Returns:** New Params value.

**Example**

```vlnc
import {std.web}
web.replaceParam(web.queryParams(web.testRequest("GET", "/?q=1")), "q", "2")
```

### `std.web.requireContext`

Require a typed context value or return Error.MissingContext.

**Overloads**

- `std.web.requireContext(request: web.Request, key: web.ContextKey[T]) -> Result[T, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.Request) => web.requireContext($req, web.contextKey[String]) end
  ```

- `std.web.requireContext(request: web.GetRequest, key: web.ContextKey[T]) -> Result[T, web.Error]`

  Require a typed context value or return Error.MissingContext.

  **Returns:** The keyed payload or MissingContext.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.GetRequest) => web.requireContext($req, web.contextKey[String]) end
  ```

- `std.web.requireContext(request: web.HeadRequest, key: web.ContextKey[T]) -> Result[T, web.Error]`

  Require a typed context value or return Error.MissingContext.

  **Returns:** The keyed payload or MissingContext.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.HeadRequest) => web.requireContext($req, web.contextKey[String]) end
  ```

- `std.web.requireContext(request: web.PostRequest, key: web.ContextKey[T]) -> Result[T, web.Error]`

  Require a typed context value or return Error.MissingContext.

  **Returns:** The keyed payload or MissingContext.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PostRequest) => web.requireContext($req, web.contextKey[String]) end
  ```

- `std.web.requireContext(request: web.PutRequest, key: web.ContextKey[T]) -> Result[T, web.Error]`

  Require a typed context value or return Error.MissingContext.

  **Returns:** The keyed payload or MissingContext.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PutRequest) => web.requireContext($req, web.contextKey[String]) end
  ```

- `std.web.requireContext(request: web.PatchRequest, key: web.ContextKey[T]) -> Result[T, web.Error]`

  Require a typed context value or return Error.MissingContext.

  **Returns:** The keyed payload or MissingContext.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PatchRequest) => web.requireContext($req, web.contextKey[String]) end
  ```

- `std.web.requireContext(request: web.DeleteRequest, key: web.ContextKey[T]) -> Result[T, web.Error]`

  Require a typed context value or return Error.MissingContext.

  **Returns:** The keyed payload or MissingContext.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.DeleteRequest) => web.requireContext($req, web.contextKey[String]) end
  ```

- `std.web.requireContext(request: web.OptionsRequest, key: web.ContextKey[T]) -> Result[T, web.Error]`

  Require a typed context value or return Error.MissingContext.

  **Returns:** The keyed payload or MissingContext.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.OptionsRequest) => web.requireContext($req, web.contextKey[String]) end
  ```

- `std.web.requireContext(request: web.QueryRequest, key: web.ContextKey[T]) -> Result[T, web.Error]`

  Require a typed context value or return Error.MissingContext.

  **Returns:** The keyed payload or MissingContext.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.QueryRequest) => web.requireContext($req, web.contextKey[String]) end
  ```

- `std.web.requireContext(request: web.WebSocketRequest, key: web.ContextKey[T]) -> Result[T, web.Error]`

  Require a typed context value or return Error.MissingContext.

  **Returns:** The keyed payload or MissingContext.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.WebSocketRequest) => web.requireContext($req, web.contextKey[String]) end
  ```


**Returns:** The keyed payload or MissingContext.

**Example**

```vlnc
import {std.web}
fn (req: web.Request) => web.requireContext($req, web.contextKey[String]) end
```

### `std.web.requireParam`

Retrieve exactly one named parameter without silently discarding duplicates.

**Overloads**

- `std.web.requireParam(params: web.Params, name: String) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.paramOne(web.queryParams(web.testRequest("GET", "/?q=one")), "q")
  ```


**Returns:** Value or typed HTTP parameter error.

**Example**

```vlnc
import {std.web}
web.paramOne(web.queryParams(web.testRequest("GET", "/?q=one")), "q")
```

### `std.web.responseText`

Decode a bounded buffered response as strict UTF-8, returning an error for binary data.

**Overloads**

- `std.web.responseText(response: web.Response, limit: Int) -> Result[String, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.responseText(web.Response(body = "ok"))
  ```


**Returns:** UTF-8 content or a typed web.Error.

**Example**

```vlnc
import {std.web}
web.responseText(web.Response(body = "ok"))
```

### `std.web.routeCapture`

Look up a named GET route capture.

**Overloads**

- `public define routeCapture(request: GetRequest, name: String) -> Result[String, Error]`

  **Example**

  ```vlnc
  import {std.web} fn(req: web.GetRequest) => web.routeCapture($req, "id") end
  ```


Look up a named GET route capture.

**Parameters**

- `request` — GET request matched to a route.
- `name` — Name of the route capture.

**Returns:** Captured string or Error.MissingParam if the name is absent.

**Example**

```vlnc
import {std.web} fn(req: web.GetRequest) => web.routeCapture($req, "id") end
```

### `std.web.routes`

Inspect validated effective routes and automatic HEAD/OPTIONS entries without exposing closures.

**Overloads**

- `std.web.routes(appOrRouter: web.App) -> Result[web.RouteDescriptor+, web.ConfigError]`

  **Example**

  ```vlnc
  import {std.web}
web.routes(web.App)
  ```

- `std.web.routes(appOrRouter: web.Router) -> Result[web.RouteDescriptor+, web.ConfigError]`

  **Example**

  ```vlnc
  import {std.web}
web.routes(web.App)
  ```


**Returns:** Route descriptors or ConfigError.

**Example**

```vlnc
import {std.web}
web.routes(web.App)
```

### `std.web.routeUrl`

Generate a validated relative URL from a registered route identity.

**Overloads**

- `std.web.routeUrl(appOrRouter: web.App, method: web.Method, template: String, params: Dict[String, String], query: web.Params) -> Result[String, web.ConfigError]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.get("/p/{id}"): fn(req: web.GetRequest) => "ok" end
web.routeUrl($app, web.Method.GET, "/p/{id}", dict{"id" => "1"})
  ```

- `std.web.routeUrl(appOrRouter: web.Router, method: web.Method, template: String, params: Dict[String, String], query: web.Params) -> Result[String, web.ConfigError]`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.get("/p/{id}"): fn(req: web.GetRequest) => "ok" end
web.routeUrl($app, web.Method.GET, "/p/{id}", dict{"id" => "1"})
  ```


**Returns:** Relative URL or ConfigError.

**Example**

```vlnc
import {std.web}
$app = web.App
$app := web.get("/p/{id}"): fn(req: web.GetRequest) => "ok" end
web.routeUrl($app, web.Method.GET, "/p/{id}", dict{"id" => "1"})
```

### `std.web.run`

Block in the caller scope serving HTTP; SIGINT/SIGTERM initiate shutdown; TLS 1.3 negotiates HTTP/1.1 and HTTP/2 using ALPN. HTTP/3 remains unsupported.

**Overloads**

- `std.web.run(app: web.App, config: web.ServerConfig) -> None`

  **Example**

  ```vlnc
  import {std.web}
web.run(web.App, web.ServerConfig(port = 8080))
  ```


**Returns:** None on graceful stop.

**Example**

```vlnc
import {std.web}
web.run(web.App, web.ServerConfig(port = 8080))
```

### `std.web.saveMultipartPart`

Save the current unconsumed multipart upload to an explicitly chosen new Path. Streams bytes to a private file, fails if the destination exists, and deletes incomplete uploads.

**Overloads**

- `std.web.saveMultipartPart(stream: web.UploadStream, destination: path.Path) -> Result[Int, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.path}
fn (stream: web.UploadStream, file: path.Path) => web.saveMultipartPart($stream, $file) end
  ```


**Returns:** Octets written or web.Error.

**Example**

```vlnc
import {std.web, std.path}
fn (stream: web.UploadStream, file: path.Path) => web.saveMultipartPart($stream, $file) end
```

### `std.web.saveUpload`

Save a buffered upload to an explicitly chosen std.path.Path; fail if the path exists.

**Overloads**

- `std.web.saveUpload(upload: web.Upload, destination: path.Path) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.path.Path}
fn (upload: web.Upload) => web.saveUpload($upload, Path("upload.bin")) end
  ```


**Returns:** None or web.Error.

**Example**

```vlnc
import {std.web, std.path.Path}
fn (upload: web.Upload) => web.saveUpload($upload, Path("upload.bin")) end
```

### `std.web.serve`

Start an owned HTTP/1.1 listener, optionally with TLS 1.3 and ALPN HTTP/2.

**Overloads**

- `std.web.serve(app: web.App, config: web.ServerConfig) -> Result[web.ServerHandle, web.ServerError]`

  **Example**

  ```vlnc
  import {std.web}
web.serve(web.App, web.ServerConfig(port = 0))
  ```


**Returns:** An exclusive ServerHandle or ServerError.

**Example**

```vlnc
import {std.web}
web.serve(web.App, web.ServerConfig(port = 0))
```

### `std.web.ServerConfig`

Construct a resource-limited cleartext HTTP/1.1 server configuration.

**Overloads**

- `std.web.ServerConfig(host: String, port: Int, tls: None, limits: web.Limits, publicOrigin: None | String, allowedHosts: None | String*, trustedProxies: None | String*, proxyMode: None | web.ProxyMode, protocols: None | web.Protocol*) -> web.ServerConfig`

  **Example**

  ```vlnc
  import {std.web}
web.ServerConfig(port = 8081, limits = web.Limits)
  ```

- `std.web.ServerConfig(host: String, port: Int, tls: web.TLS, limits: web.Limits, publicOrigin: None | String, allowedHosts: None | String*, trustedProxies: None | String*, proxyMode: None | web.ProxyMode, protocols: None | web.Protocol*) -> web.ServerConfig`

  Construct a TLS 1.3 server configuration with automatic HTTP/1.1 + HTTP/2 ALPN.

  **Returns:** ServerConfig with TLS enabled.

  **Example**

  ```vlnc
  import {std.web}
web.ServerConfig(tls = web.TLS(certificateFile = "cert.pem", privateKeyFile = "key.pem"))
  ```


**Returns:** ServerConfig with validated limits.

**Example**

```vlnc
import {std.web}
web.ServerConfig(port = 8081, limits = web.Limits)
```

### `std.web.serverStats`

Read a bounded, read-only snapshot of listener admission and resource counters; no unauthenticated metrics endpoint is created.

**Overloads**

- `std.web.serverStats(server: web.ServerHandle) -> web.ServerStats`

  **Example**

  ```vlnc
  import {std.web}
fn (server: web.ServerHandle) => web.serverStats($server) end
  ```


**Returns:** ServerStats snapshot.

**Example**

```vlnc
import {std.web}
fn (server: web.ServerHandle) => web.serverStats($server) end
```

### `std.web.shutdown`

Consume an owned development server handle, close its listener and join workers.

**Overloads**

- `std.web.shutdown(server: web.ServerHandle) -> Result[web.ServerStopReport, web.ServerError]`

  **Example**

  ```vlnc
  import {std.web}
$server = web.serve(web.App, web.ServerConfig(port = 0)) ?
web.shutdown $server
  ```


**Returns:** ServerStopReport or ServerError.

**Example**

```vlnc
import {std.web}
$server = web.serve(web.App, web.ServerConfig(port = 0)) ?
web.shutdown $server
```

### `std.web.shutdownWithin`

Consume an owned HTTP/1.1 server handle with an explicit grace timeout in seconds.

**Overloads**

- `std.web.shutdownWithin(server: web.ServerHandle, graceSeconds: Int) -> Result[web.ServerStopReport, web.ServerError]`

  **Example**

  ```vlnc
  import {std.web}
fn (server: web.ServerHandle) => web.shutdownWithin($server, 5) end
  ```


**Returns:** ServerStopReport or ServerError.

**Example**

```vlnc
import {std.web}
fn (server: web.ServerHandle) => web.shutdownWithin($server, 5) end
```

### `std.web.staticRouter`

Build a confined GET/HEAD static Router using no-follow dirfd traversal.

**Overloads**

- `std.web.staticRouter(root: String) -> Result[web.Router, web.ConfigError]`

  **Example**

  ```vlnc
  import {std.web}
web.staticRouter("public/assets")
  ```

- `std.web.staticRouter(root: path.Path) -> Result[web.Router, web.ConfigError]`

  Build a confined static Router from a std.path.Path root.

  **Returns:** Router or ConfigError.

  **Example**

  ```vlnc
  import {std.web, std.path.Path}
web.staticRouter(Path("public/assets"))
  ```


**Returns:** Router or ConfigError.

**Example**

```vlnc
import {std.web}
web.staticRouter("public/assets")
```

### `std.web.stream`

Transfer one owned ByteSource into a single streaming response body.

**Overloads**

- `std.web.stream(source: web.ByteSource, contentType: String) -> web.StreamBody`

  **Example**

  ```vlnc
  import {std.web}
fn (source: web.ByteSource) => web.stream($source, "text/plain") end
  ```


**Returns:** A nonduplicable StreamBody.

**Example**

```vlnc
import {std.web}
fn (source: web.ByteSource) => web.stream($source, "text/plain") end
```

### `std.web.streamResponse`

Construct a single-response owned stream without buffering, while the compiler learns to transfer StreamBody through Response constructors.

**Overloads**

- `std.web.streamResponse(source: web.ByteSource, contentType: String) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
fn (source: web.ByteSource) => web.streamResponse($source, "text/plain") end
  ```


**Returns:** Response owning an exclusive ByteSource.

**Example**

```vlnc
import {std.web}
fn (source: web.ByteSource) => web.streamResponse($source, "text/plain") end
```

### `std.web.testRequest`

Create a validated, socket-free request.

**Overloads**

- `std.web.testRequest(method: String, target: String, headers: web.Headers, body: #byte+ Int*) -> web.Request`

  **Example**

  ```vlnc
  import {std.web}
web.testRequest("GET", "/")
  ```


**Returns:** Method-specific request value.

**Example**

```vlnc
import {std.web}
web.testRequest("GET", "/")
```

### `std.web.trailers`

Read validated request trailers after streaming-body EOF only; never merge them with initial request headers.

**Overloads**

- `std.web.trailers(request: web.PostRequest) -> Result[web.Headers, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PostRequest) => web.trailers($req) end
  ```

- `std.web.trailers(request: web.PutRequest) -> Result[web.Headers, web.Error]`

  Read validated request trailers after streaming-body EOF only; never merge them with initial request headers.

  **Returns:** Headers or a typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PutRequest) => web.trailers($req) end
  ```

- `std.web.trailers(request: web.PatchRequest) -> Result[web.Headers, web.Error]`

  Read validated request trailers after streaming-body EOF only; never merge them with initial request headers.

  **Returns:** Headers or a typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PatchRequest) => web.trailers($req) end
  ```

- `std.web.trailers(request: web.QueryRequest) -> Result[web.Headers, web.Error]`

  Read validated request trailers after streaming-body EOF only; never merge them with initial request headers.

  **Returns:** Headers or a typed web.Error.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.QueryRequest) => web.trailers($req) end
  ```


**Returns:** Headers or a typed web.Error.

**Example**

```vlnc
import {std.web}
fn (req: web.PostRequest) => web.trailers($req) end
```

### `std.web.uploadOne`

Read one matching upload, rejecting ambiguous repetitions.

**Overloads**

- `std.web.uploadOne(form: web.MultipartForm, name: String) -> Result[web.Upload?, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (form: web.MultipartForm) => web.uploadOne($form, "file") end
  ```


**Returns:** Optional upload or web.Error.

**Example**

```vlnc
import {std.web}
fn (form: web.MultipartForm) => web.uploadOne($form, "file") end
```

### `std.web.uploads`

Select multipart files under a field name in arrival order.

**Overloads**

- `std.web.uploads(form: web.MultipartForm, name: String) -> web.Upload+`

  **Example**

  ```vlnc
  import {std.web}
fn (form: web.MultipartForm) => web.uploads($form, "file") end
  ```


**Returns:** Possibly empty upload list.

**Example**

```vlnc
import {std.web}
fn (form: web.MultipartForm) => web.uploads($form, "file") end
```

### `std.web.wait`

Wait for externally initiated server shutdown and consume its owned handle.

**Overloads**

- `std.web.wait(server: web.ServerHandle) -> Result[web.ServerStopReport, web.ServerError]`

  **Example**

  ```vlnc
  import {std.web}
web.run(web.App)
  ```


**Returns:** ServerStopReport or ServerError.

**Example**

```vlnc
import {std.web}
web.run(web.App)
```

### `std.web.websocket`

Register a WebSocket upgrade route separate from ordinary GET.

**Overloads**

- `std.web.websocket(target: web.App, path: String, handler: Function[web.WebSocket -> None], policy: web.WebSocketPolicy) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$config = web.App
$config := web.websocket("/ws"): fn (socket: web.WebSocket) -> web.Error => web.Error.Forbidden("Connection refused") end
  ```

- `std.web.websocket(target: web.App, path: String, handler: Function[web.WebSocket -> web.Error], policy: web.WebSocketPolicy) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$config = web.App
$config := web.websocket("/ws"): fn (socket: web.WebSocket) -> web.Error => web.Error.Forbidden("Connection refused") end
  ```

- `std.web.websocket(target: web.App, path: String, handler: Function[web.WebSocket -> Result[None, web.Error]], policy: web.WebSocketPolicy) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$config = web.App
$config := web.websocket("/ws"): fn (socket: web.WebSocket) -> web.Error => web.Error.Forbidden("Connection refused") end
  ```

- `std.web.websocket(target: web.Router, path: String, handler: Function[web.WebSocket -> None], policy: web.WebSocketPolicy) -> web.Router`

  Register a WebSocket upgrade route separate from ordinary GET.

  **Returns:** Immutable App or Router.

  **Example**

  ```vlnc
  import {std.web}
$config = web.Router
$config := web.websocket("/ws"): fn (socket: web.WebSocket) -> web.Error => web.Error.Forbidden("Connection refused") end
  ```

- `std.web.websocket(target: web.Router, path: String, handler: Function[web.WebSocket -> web.Error], policy: web.WebSocketPolicy) -> web.Router`

  Register a WebSocket upgrade route separate from ordinary GET.

  **Returns:** Immutable App or Router.

  **Example**

  ```vlnc
  import {std.web}
$config = web.Router
$config := web.websocket("/ws"): fn (socket: web.WebSocket) -> web.Error => web.Error.Forbidden("Connection refused") end
  ```

- `std.web.websocket(target: web.Router, path: String, handler: Function[web.WebSocket -> Result[None, web.Error]], policy: web.WebSocketPolicy) -> web.Router`

  Register a WebSocket upgrade route separate from ordinary GET.

  **Returns:** Immutable App or Router.

  **Example**

  ```vlnc
  import {std.web}
$config = web.Router
$config := web.websocket("/ws"): fn (socket: web.WebSocket) -> web.Error => web.Error.Forbidden("Connection refused") end
  ```


**Returns:** Immutable App or Router.

**Example**

```vlnc
import {std.web}
$config = web.App
$config := web.websocket("/ws"): fn (socket: web.WebSocket) -> web.Error => web.Error.Forbidden("Connection refused") end
```

### `std.web.websocketClose`

Send a WebSocket close control frame.

**Overloads**

- `std.web.websocketClose(socket: web.WebSocket, code: Int, reason: String) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (socket: web.WebSocket) => web.websocketClose($socket) end
  ```


**Returns:** None or web.Error.

**Example**

```vlnc
import {std.web}
fn (socket: web.WebSocket) => web.websocketClose($socket) end
```

### `std.web.websocketPing`

Send a WebSocket ping control frame.

**Overloads**

- `std.web.websocketPing(socket: web.WebSocket, data: #byte+ Int*) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (socket: web.WebSocket) => web.websocketPing($socket) end
  ```


**Returns:** None or web.Error.

**Example**

```vlnc
import {std.web}
fn (socket: web.WebSocket) => web.websocketPing($socket) end
```

### `std.web.websocketReceive`

Receive a WebSocket text, binary or close event.

**Overloads**

- `std.web.websocketReceive(socket: web.WebSocket) -> Result[web.WebSocketMessage, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (socket: web.WebSocket) => web.websocketReceive($socket) end
  ```


**Returns:** WebSocketMessage or web.Error.

**Example**

```vlnc
import {std.web}
fn (socket: web.WebSocket) => web.websocketReceive($socket) end
```

### `std.web.websocketRequest`

Read the validated WebSocket handshake snapshot without transferring the connection owner.

**Overloads**

- `std.web.websocketRequest(socket: web.WebSocket) -> web.WebSocketRequest`

  **Example**

  ```vlnc
  import {std.web}
fn (socket: web.WebSocket) => web.websocketRequest($socket) end
  ```


**Returns:** An immutable WebSocketRequest value.

**Example**

```vlnc
import {std.web}
fn (socket: web.WebSocket) => web.websocketRequest($socket) end
```

### `std.web.websocketSendBytes`

Send one bounded WebSocket message.

**Overloads**

- `std.web.websocketSendBytes(socket: web.WebSocket, value: #byte+ Int*) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.file.#byte}
fn (socket: web.WebSocket) => web.websocketSendBytes($socket, [65 #byte]) end
  ```


**Returns:** None or web.Error.

**Example**

```vlnc
import {std.web, std.file.#byte}
fn (socket: web.WebSocket) => web.websocketSendBytes($socket, [65 #byte]) end
```

### `std.web.websocketSendText`

Send one bounded WebSocket message.

**Overloads**

- `std.web.websocketSendText(socket: web.WebSocket, value: String) -> Result[None, web.Error]`

  **Example**

  ```vlnc
  import {std.web, std.file.#byte}
fn (socket: web.WebSocket) => web.websocketSendText($socket, "hello") end
  ```


**Returns:** None or web.Error.

**Example**

```vlnc
import {std.web, std.file.#byte}
fn (socket: web.WebSocket) => web.websocketSendText($socket, "hello") end
```

### `std.web.withBodyMode`

Opt a registered content-bearing route into incremental request-body reads.

**Overloads**

- `std.web.withBodyMode(appOrRouter: web.App, method: web.Method, template: String, mode: web.BodyMode) -> Result[web.App, web.ConfigError]`

  **Example**

  ```vlnc
  import {std.web}
$config = web.App
$config := web.post("/upload"): fn (req: web.PostRequest) => "ok" end
web.withBodyMode($config, web.Method.POST, "/upload", web.BodyMode.Stream)
  ```

- `std.web.withBodyMode(appOrRouter: web.Router, method: web.Method, template: String, mode: web.BodyMode) -> Result[web.Router, web.ConfigError]`

  Opt a registered content-bearing route into incremental request-body reads.

  **Returns:** New App or Router; rejects unmatched route identities.

  **Example**

  ```vlnc
  import {std.web}
$config = web.Router
$config := web.post("/upload"): fn (req: web.PostRequest) => "ok" end
web.withBodyMode($config, web.Method.POST, "/upload", web.BodyMode.Stream)
  ```


**Returns:** New App or Router; rejects unmatched route identities.

**Example**

```vlnc
import {std.web}
$config = web.App
$config := web.post("/upload"): fn (req: web.PostRequest) => "ok" end
web.withBodyMode($config, web.Method.POST, "/upload", web.BodyMode.Stream)
```

### `std.web.withContext`

Return the same request variant with one typed context value attached.

**Overloads**

- `std.web.withContext(request: web.Request, key: web.ContextKey[T], value: T) -> web.Request`

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.Request) => web.withContext($req, web.contextKey[String], "en") end
  ```

- `std.web.withContext(request: web.GetRequest, key: web.ContextKey[T], value: T) -> web.GetRequest`

  Return the same request variant with one typed context value attached.

  **Returns:** An immutable enriched request.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.GetRequest) => web.withContext($req, web.contextKey[String], "en") end
  ```

- `std.web.withContext(request: web.HeadRequest, key: web.ContextKey[T], value: T) -> web.HeadRequest`

  Return the same request variant with one typed context value attached.

  **Returns:** An immutable enriched request.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.HeadRequest) => web.withContext($req, web.contextKey[String], "en") end
  ```

- `std.web.withContext(request: web.PostRequest, key: web.ContextKey[T], value: T) -> web.PostRequest`

  Return the same request variant with one typed context value attached.

  **Returns:** An immutable enriched request.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PostRequest) => web.withContext($req, web.contextKey[String], "en") end
  ```

- `std.web.withContext(request: web.PutRequest, key: web.ContextKey[T], value: T) -> web.PutRequest`

  Return the same request variant with one typed context value attached.

  **Returns:** An immutable enriched request.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PutRequest) => web.withContext($req, web.contextKey[String], "en") end
  ```

- `std.web.withContext(request: web.PatchRequest, key: web.ContextKey[T], value: T) -> web.PatchRequest`

  Return the same request variant with one typed context value attached.

  **Returns:** An immutable enriched request.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.PatchRequest) => web.withContext($req, web.contextKey[String], "en") end
  ```

- `std.web.withContext(request: web.DeleteRequest, key: web.ContextKey[T], value: T) -> web.DeleteRequest`

  Return the same request variant with one typed context value attached.

  **Returns:** An immutable enriched request.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.DeleteRequest) => web.withContext($req, web.contextKey[String], "en") end
  ```

- `std.web.withContext(request: web.OptionsRequest, key: web.ContextKey[T], value: T) -> web.OptionsRequest`

  Return the same request variant with one typed context value attached.

  **Returns:** An immutable enriched request.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.OptionsRequest) => web.withContext($req, web.contextKey[String], "en") end
  ```

- `std.web.withContext(request: web.QueryRequest, key: web.ContextKey[T], value: T) -> web.QueryRequest`

  Return the same request variant with one typed context value attached.

  **Returns:** An immutable enriched request.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.QueryRequest) => web.withContext($req, web.contextKey[String], "en") end
  ```

- `std.web.withContext(request: web.WebSocketRequest, key: web.ContextKey[T], value: T) -> web.WebSocketRequest`

  Return the same request variant with one typed context value attached.

  **Returns:** An immutable enriched request.

  **Example**

  ```vlnc
  import {std.web}
fn (req: web.WebSocketRequest) => web.withContext($req, web.contextKey[String], "en") end
  ```


**Returns:** An immutable enriched request.

**Example**

```vlnc
import {std.web}
fn (req: web.Request) => web.withContext($req, web.contextKey[String], "en") end
```

### `std.web.withCookie`

Issue a Set-Cookie field using validated, explicit cookie scope, expiration and security settings.

**Overloads**

- `std.web.withCookie(response: web.Response, name: String, value: String, path: String, domain: String?, httpOnly: #boolean Int, secure: #boolean Int, sameSite: web.SameSite, maxAge: Int?, expires: Int?, partitioned: #boolean Int) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.withCookie(web.Response, "sid", "value")
  ```


**Returns:** Response or typed cookie error.

**Example**

```vlnc
import {std.web}
web.withCookie(web.Response, "sid", "value")
```

### `std.web.withEarlyHints`

Attach validated preload Link metadata for one HTTP 103 Early Hints response.

**Overloads**

- `std.web.withEarlyHints(response: web.Response, links: String+) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
web.withEarlyHints(web.Response(body = "ok"), ["</style.css>; rel=preload; as=style"])
  ```


**Returns:** Response or InvalidHeader.

**Example**

```vlnc
import {std.web}
web.withEarlyHints(web.Response(body = "ok"), ["</style.css>; rel=preload; as=style"])
```

### `std.web.withErrorHandler`

Set a single application HTTP error callback. Faults in the error callback revert to a minimal safe 500 response; never reenter the hook.

**Overloads**

- `std.web.withErrorHandler(app: web.App, callback: Function[web.ErrorContext -> web.Response]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.withErrorHandler: fn (err: web.ErrorContext) => web.defaultErrorResponse($err) end
  ```

- `std.web.withErrorHandler(app: web.App, callback: Function[web.ErrorContext -> Result[web.Response, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.withErrorHandler: fn (err: web.ErrorContext) => web.defaultErrorResponse($err) end
  ```


**Returns:** New immutable App.

**Example**

```vlnc
import {std.web}
$app = web.App
$app := web.withErrorHandler: fn (err: web.ErrorContext) => web.defaultErrorResponse($err) end
```

### `std.web.withETag`

Attach a validated strong ETag representation validator.

**Overloads**

- `std.web.withETag(response: web.Response, tag: String) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.withETag(web.Response(body = "ok"), "v1")
  ```


**Returns:** New Response value.

**Example**

```vlnc
import {std.web}
web.withETag(web.Response(body = "ok"), "v1")
```

### `std.web.withHeader`

Immutably edit validated response header fields.

**Overloads**

- `std.web.withHeader(headers: web.Headers, name: String, value: String) -> web.Headers`

  **Example**

  ```vlnc
  import {std.web}
web.withHeader(web.Response(body = "ok"), "X-Web", "yes")
  ```

- `std.web.withHeader(response: web.Response, name: String, value: String) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.withHeader(web.Response(body = "ok"), "X-Web", "yes")
  ```


**Returns:** New Response value.

**Example**

```vlnc
import {std.web}
web.withHeader(web.Response(body = "ok"), "X-Web", "yes")
```

### `std.web.withLastModified`

Attach a Last-Modified validator formatted as an HTTP date.

**Overloads**

- `std.web.withLastModified(response: web.Response, unixSeconds: Int) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.withLastModified(web.Response(body = "ok"), 0)
  ```


**Returns:** New Response.

**Example**

```vlnc
import {std.web}
web.withLastModified(web.Response(body = "ok"), 0)
```

### `std.web.withLogger`

Attach an immutable completion-event logger. Events include no raw URI, authorization, cookie, request body, or response body fields. Logging errors cannot alter an HTTP response.

**Overloads**

- `std.web.withLogger(app: web.App, callback: Function[web.LogEvent -> None]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.withLogger: fn (event: web.LogEvent) -> web.Error => web.Error.Forbidden("Logging unavailable") end
  ```

- `std.web.withLogger(app: web.App, callback: Function[web.LogEvent -> web.Error]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.withLogger: fn (event: web.LogEvent) -> web.Error => web.Error.Forbidden("Logging unavailable") end
  ```

- `std.web.withLogger(app: web.App, callback: Function[web.LogEvent -> Result[None, web.Error]]) -> web.App`

  **Example**

  ```vlnc
  import {std.web}
$app = web.App
$app := web.withLogger: fn (event: web.LogEvent) -> web.Error => web.Error.Forbidden("Logging unavailable") end
  ```


**Returns:** New App with a safe logger.

**Example**

```vlnc
import {std.web}
$app = web.App
$app := web.withLogger: fn (event: web.LogEvent) -> web.Error => web.Error.Forbidden("Logging unavailable") end
```

### `std.web.withoutHeader`

Immutably edit validated response header fields.

**Overloads**

- `std.web.withoutHeader(response: web.Response, name: String) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.withHeader(web.Response(body = "ok"), "X-Web", "yes")
  ```


**Returns:** New Response value.

**Example**

```vlnc
import {std.web}
web.withHeader(web.Response(body = "ok"), "X-Web", "yes")
```

### `std.web.withParam`

Append a parameter without replacing earlier values.

**Overloads**

- `std.web.withParam(params: web.Params, name: String, value: String) -> web.Params`

  **Example**

  ```vlnc
  import {std.web}
web.withParam(web.queryParams(web.testRequest("GET", "/")), "q", "2")
  ```


**Returns:** New Params value.

**Example**

```vlnc
import {std.web}
web.withParam(web.queryParams(web.testRequest("GET", "/")), "q", "2")
```

### `std.web.withStatus`

Immutably replace the HTTP response status.

**Overloads**

- `std.web.withStatus(response: web.Response, status: web.Status) -> web.Response`

  **Example**

  ```vlnc
  import {std.web}
web.withStatus(web.Response(body = "ok"), web.Status.Created)
  ```


**Returns:** New Response value with validated status.

**Example**

```vlnc
import {std.web}
web.withStatus(web.Response(body = "ok"), web.Status.Created)
```

### `std.web.withTrailers`

Attach validated HTTP trailing fields to an owned stream. H1 emits a Trailer announcement and chunked terminator.

**Overloads**

- `std.web.withTrailers(response: web.Response, trailers: web.Headers) -> Result[web.Response, web.Error]`

  **Example**

  ```vlnc
  import {std.web}
fn (response: web.Response, fields: web.Headers) => web.withTrailers($response, $fields) end
  ```


**Returns:** Response or InvalidHeader.

**Example**

```vlnc
import {std.web}
fn (response: web.Response, fields: web.Headers) => web.withTrailers($response, $fields) end
```
