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

Evaluate a replacement normally, then atomically publish it to State[T].

**Overloads**

- `set(state: State[T], replacement: Function[ -> T]) -> ()`

  **Example**

  ```vlnc
  import {std.concurrency}
$s = concurrency.State(1)
$s set: 10
get $s
  ```
  Result:

  ```text
  10
  ```


**Parameters**

- `state` — Shared State handle.
- `replacement` — A zero-input expression producing a safe T.

**Returns:** No stack outputs.

**Example**

```vlnc
import {std.concurrency}
$s = concurrency.State(1)
$s set: 10
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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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

  - Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.


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

- Target-directed conversion selects an overload using `to[Type]`; available conversions depend on the source and target types. `to[String]` formats any value using Valiance runtime display syntax. For Strings, `to[Int]` parses a base-ten integer and panics with `ConversionFault` when parsing fails. `to[Real]` parses a real number and `to[Number]` also accepts complex numeric notation; both panic with `ConversionFault` for invalid text. `to?[Type]` invokes the same registered conversion and returns `None` if it panics.

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
