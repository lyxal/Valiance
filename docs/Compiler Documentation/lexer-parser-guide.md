# Lexer and Parser Guide

This guide is for future agents working on Valiance's lexer, parser, AST node
model, or parser-facing tests. It is intentionally self-contained: do not
assume the reader has loaded any other compiler guide.

The parser's job is not to preserve source order literally. Valiance source is
written as left-to-right chains, but chains execute right-to-left. The parser
therefore lowers chain syntax into the stack order consumed by analysis,
codegen, and runtime.

## Main Files

`src/valiance/parsing/lexer.py`

- Defines `TokenKind`, `Token`, `LexError`, and `lex(source)`.
- Owns comments, strings, numbers, identifiers, operators, data-tag tokens,
  delimiters, and source locations.
- Emits `NEWLINE` tokens because newlines are syntax.

`src/valiance/parsing/parser.py`

- Defines `ParseError`, `Parser`, `parse(source)`, and `parse_type(source)`.
- Owns source-to-AST lowering and type-expression parsing.
- Converts chain syntax into normal stack-order AST.
- Attaches `SourceLocation` to parser-produced AST nodes.
- Parses `@name` annotations before declarations/function literals and
  `@@name` annotations before element calls. Semantics live in
  `analysis/annotations.py`, not in the parser.

`src/valiance/asts/nodes.py`

- Defines all raw AST dataclasses and typed AST wrappers.
- Parser nodes inherit from `ASTNode`, whose keyword-only `location` is ignored
  for equality comparisons.

`src/valiance/asts/__init__.py`

- Public import surface for AST nodes. Add new AST nodes here when they should
  be used outside `asts.nodes`.

`src/valiance/asts/pretty.py`

- Debug printer for raw and typed AST. Update it when adding node fields that a
  human should see in `valiance analyse` output.

`tests/test_parser.py`

- Main lexer/parser regression suite.
- Prefer small tests that assert the exact AST shape, especially for chain
  lowering and ambiguous delimiters.

## Pipeline

The source pipeline starts here:

```text
source text -> lex(source) -> list[Token] -> Parser(...).parse_program() -> AST nodes
```

The public helpers are:

```python
from valiance.parsing import lex, parse, parse_type

tokens = lex("1 + 2")
program = parse("1 + 2")
typ = parse_type("Function[Number -> String]")
```

`parse()` always runs the lexer first. If a feature needs new punctuation or
token boundaries, change `lexer.py` before changing `parser.py`.

## Lexer Model

The lexer is a hand-written scanner. It walks source text with `index`, `line`,
and `column`, and emits tokens with line, column, and absolute offset.

Important token rules:

- Spaces, tabs, and carriage returns are emitted as `TokenKind.WHITESPACE`. The
  parser skips these tokens centrally during cursor movement and lookahead.
- `\n` emits `TokenKind.NEWLINE`.
- `#?` starts a single-line comment.
- `#/ ... /#` is a nested multiline comment.
- A bare `#name`, `#!name`, or `#name++` is emitted as one `OP` token for data
  tags.
- `"` starts a string. Strings may contain literal newlines. Escaped `"`, `\`,
  and `$` are unescaped; other backslash sequences are preserved with the
  backslash.
- Numbers include signed decimals, scientific notation, and the current complex
  literal form such as `3i4`.
- Alphanumeric identifiers use `_` or alphabetic start characters followed by
  `_`, alphabetic characters, or digits.
- Symbolic operators are made from `_OP_CHARS`. `_operator()` emits exactly one
  single-character `OP` token per source character, with that character's own
  offset. It must not emit growing, overlapping substrings such as `+` and `++`
  from the same starting offset.
- Backslash-prefixed niladic names such as `\foo` are emitted as a single `OP`
  token.

When adding tokens:

1. Add the token kind to `TokenKind`.
2. Add single-character punctuation to `_SINGLE` when possible.
3. Put multi-character forms before their prefixes in `_Lexer.lex`.
4. Add a lexer test that checks token kind, value, and location if location is
   relevant.

Do not make the parser infer token boundaries that the lexer can know cleanly.
For symbolic operators, however, the lexer deliberately preserves character-level
boundaries. The parser uses token offsets and whitespace to decide when adjacent
`OP` characters form one element symbol.

## AST Locations

Every parser-created AST node should receive a `location=_loc(token)` from the
token that begins the syntactic construct.

Examples:

- `NumberLiteralNode(..., location=_loc(number_token))`
- `FunctionNode(..., location=_loc(fn_token))`
- `ElementNode(..., location=_loc(element_token))`

`SourceLocation` contains:

```python
SourceLocation(line: int, column: int, offset: int)
```

Tests can compare AST nodes without specifying locations because `ASTNode`
marks `location` as `compare=False`. Add explicit location assertions when a
diagnostic feature depends on the source position.

## Parser Model

The parser is recursive descent over a token list. Its constructor materializes the
token iterator once; do not add debug iteration before `list(tokens)`, because
consuming the iterator there would leave the parser with an empty or truncated
stream.

It exposes a small cursor API:

- `_current`, `_previous`, and `_peek(ahead)`
- `_advance()` and `_peek(ahead)` skip `WHITESPACE` transparently, so every
  lookahead depth refers to meaningful tokens rather than raw list positions
- `_match(...)` to consume optional token kinds
- `_expect(kind)` to require a token kind
- `_match_ident(...)` and `_check_ident(...)` for keyword-like identifiers
- `_error(message)` to raise `ParseError` at the current token

Whitespace handling belongs in `_advance()` and `_peek()`. Do not reintroduce a
separate `_consume_whitespace` step in `_check`, `_expect`, or individual grammar
methods. Central skipping keeps `_peek(1)`, `_peek(2)`, and deeper lookahead
consistent.

The exception is syntax where whitespace itself is significant. `_adjacent(first,
second)` is the single authority for deciding whether two tokens touch in source,
using their offsets and token widths. Grammar code should call `_adjacent` rather
than reproducing offset arithmetic.

Keywords such as `define`, `fn`, `if`, and `while` are currently lexed as
ordinary `IDENT` tokens and recognized by parser methods. Do not add keyword
token kinds unless there is a strong reason.

Top-level parsing flows through:

```text
parse_program()
  -> _statement()
      -> declarations/control flow
      -> _chain_until(...)
```

Declarations and control flow return AST nodes directly. Ordinary expressions
are parsed as chains.

## Chain Lowering

This is the most important parser invariant.

Valiance chains are written left to right, but each element in a chain uses the
result of the next element as its rightmost argument. The parser lowers these
chains into stack order.

Examples:

```text
source: 1 + 2
AST:    Number(1), Number(2), Element(+)

source: 3 + 4 * 7
AST:    Number(3), Number(4), Element(+), Number(7), Element(*)

source:
[1, 2, 3]
println length

AST:
ListLiteral(...), Element(length), Element(println)
```

The parser represents each raw chain item as a private `_ChainPiece`:

```python
@dataclass(frozen=True, slots=True)
class _ChainPiece:
    nodes: tuple[ASTNode, ...]
    breaks_chain: bool = False
    is_element: bool = False
```

`_chain_until(terminators)` accumulates pieces until a terminator or `|`, then
calls `_lower_chain_segment`.

Current chain-breaking rules:

- Literals break chains and are included in the segment they break.
- Variables and parenthesized values break chains.
- `fn`, `if`, `while`, `foreach`, `break`, and `return` break chains.
- List, tuple, array, record, and dictionary literals break chains.
- Backslash-prefixed niladic element names break chains and are included.
- Element call syntax such as `foo(...)` breaks chains.
- The `:` modifier breaks chains.
- `|`, newlines, closers, `end`, and `else` terminate or split chains.

All-element segments are reversed. A segment ending in a breaker with only
elements to its left emits the breaker first, then those left elements in
reverse. Otherwise, pieces are flattened in source order.

When changing chain behavior, add parser tests before and after the boundary
you are changing. Most regressions here look like elements in the wrong order.

## Expression Terms

`_term()` parses one expression piece. It handles:

- Numbers and strings
- `$` variables, assignments, and variable call syntax
- `.field` access
- List literals: `[...]`
- Array literals: `arr{...}`
- Record literals: `record{...}`
- Dictionary literals: `dict{...}`
- Parenthesized grouping: `(...)`
- Tuple literals: `{...}`
- Function literals: `fn ... => ...`
- Quick functions: `'chain`
- Control-flow nodes in expression position
- `break` and `return`
- Data-tag application: `#tag` and `#!tag`
- Elements, element call syntax, niladic element names, and `:` modifiers
- Function annotations such as `@recursive fn ...`
- Element annotations such as `@@tupled foo`
- Whitespace-free runs of `OP` tokens, merged by `_operator_run()` into one
  `Symbol`; whitespace ends the run

Qualified element names are parsed by `_qualified_symbol`. Supported forms
include namespace qualification with dots, object-friendly qualification with
`::`, and the built-in escape namespace:

```text
utils.double
Foo::bar
*::+
*::Some
```

The token after `::` may be an identifier or operator. This is required for
built-in operator access such as `*::+`.

Keep `_term()` focused on choosing a syntactic form. Put nested parsing in
helper methods such as `_record_fields`, `_dict_entries`, or
`_modifier_arguments`.

### Adjacency-sensitive syntax

Because ordinary cursor movement skips whitespace, syntax that requires touching
tokens must check source adjacency explicitly. Consecutive tokens are treated as
one construct only when `_adjacent(first, second)` succeeds.

Current adjacency-sensitive cases include:

- `_operator_run()`, which merges only a whitespace-free run of `OP` tokens into
  one element symbol. For example, (`+`+`+` -> `++`), while `+ +` remains two
  symbols.
- `_match_ellipsis()`, where all three `.` tokens must be adjacent. `...` is an
  ellipsis; `. . .` is not.
- The `||` branch in `_match_pattern()`, where the two pipe tokens must touch.
- Rank continuation in `_type_postfix()`, where repeated rank operator tokens are
  counted only while each next token is adjacent to the previous one.

When adding another multi-token spelling, first decide whether whitespace may split
it. If not, use `_adjacent` for every consecutive pair rather than relying on
`_peek()` alone.

## Blocks

Most block forms use:

```text
keyword condition? => body end?
```

`_body(stop_words=None)` chooses between single-line and multiline bodies:

- If the token after `=>` is not `NEWLINE`, the body is a single chain ending at
  a line terminator or structural terminator. A trailing `end` is consumed when
  present.
- If the token after `=>` is `NEWLINE`, the body is a sequence of statements
  until `end` or another supplied stop word such as `else`.

This means single-line forms like this should parse without getting stuck:

```text
fn => + | double end
```

Be careful when adding stop words. `_at_terminator` accepts both token kinds and
identifier strings, so stop words such as `"end"` and `"else"` work even though
they are `IDENT` tokens.

## Delimited Expressions

The parser has two related helpers:

```python
_comma_expressions(closer)
_argument_expressions(closer)
```

`_comma_expressions` parses zero or more comma-separated chain expressions.
It is used for collection and tuple literals where empty forms can be valid.

`_argument_expressions` rejects empty argument lists. This enforces the language
rule that niladic elements must be recognizable without context: use `\nilad`,
not `nilad()`.

Examples:

```text
[]                 # empty list literal parses
foo()              # syntax error
define foo() => 1  # syntax error
```

Use `_argument_expressions` for call syntax and function parameter syntax where
empty parentheses would create ambiguity.

## Function-Argument Modifier

The `:` modifier binds function arguments directly to the element node:

```text
[1, 2, 3, 4] map: double
```

parses as:

```text
ListLiteral(...)
ElementNode(name=map, modifier_args=(FunctionNode(body=(ElementNode(double),)),))
```

Multiple function arguments use parenthesized comma-separated chains:

```text
fork: (sum, length) /
```

The parser wraps each modifier chain in a `FunctionNode` and stores those
functions in `ElementNode.modifier_args`. Do not emit modifier functions as
ordinary preceding stack values; the analyser matches bound modifier functions
to function-typed parameters by overload.

## ECS Call Arguments And Optional Defaults

Adjacent `foo(...)` syntax parses as an `ElementNode` with structured
`call_args`, not as ordinary expression nodes inserted into the surrounding
chain.

- Positional ECS arguments store `CallArgument(value=...)`.
- Named ECS arguments such as `foo(bar = 1)` store `CallArgument(name=bar, ...)`.
- `_` placeholders store `CallArgument(placeholder=True)` so later optional ECS
  arguments can skip earlier positions.

This structure matters because optional defaults are an ECS-only feature. The
parser should preserve which arguments were named, positional, or placeholders
instead of flattening them into normal stack expressions.

## Type Parser

`parse_type(source)` uses the same token stream but calls
`Parser.parse_type_expression()` and then requires EOF.

The type parser currently supports:

- Named types: `Number`, `String`, `Result[Number, String]`
- `None`
- Bare function type: `Function`, lowered to unknown-shape `T.Fn()`
- Function types: `Function[Number, String -> Number]`
- Function shorthand: `(Number, String -> Number)`
- Tuple types: `{Number, String}`
- Arbitrary-length tuple parameter types: `{Number...}`,
  `{Number..., String}`, `{Number..., String...}`
- Union types: `A | B`
- Intersection types: `A & B`
- Optional types: `T?`, `T???`, `T?3`, lowered to nested `Some[T] | None`
- Atomic generic views: `T atomic`, lowered to `Atomic(T)`
- List rank postfixes: `T+`, `T+3`, `T+$n`, `T*`, `T*3`, `T*$n`,
  `T~`, `T~3`, `T~$n`
- Array rank postfixes: `T^`, `T^3`, `T^$n`, `T>`, `T>3`, `T>$n`
- Contiguous repeated rank/optional markers count as numeric shorthand:
  `T++ == T+2`, `T*** == T*3`, `T~~~~ == T~4`, `T??? == T?3`
- Mixed rank postfixes are rejected unless the outer marker is a direct
  superset, such as `T+* == T**`. Optional postfixes are a barrier, so
  `T+?+` and `T+?*` are valid.
- Data-tagged types: `#sorted Number+`, `#!infinite Number+`
- Function element tags after function types:
  `Function[Number -> ]<Eager, !Panic[String]>`

Type parsing is split by precedence:

```text
_type_union
  -> _type_intersection
      -> _type_tagged
          -> _type_postfix
              -> _type_primary
```

When adding new type syntax, place it at the correct precedence layer. Do not
bolt it onto `_type_primary` if it is actually a prefix, postfix, union-like, or
intersection-like form.

Arbitrary-length tuple types are only valid while parsing parameter types. The
parser uses an internal "allow variadic tuple type" flag around function and
trait parameter parsing. Do not allow `{T...}` in return types, casts,
disambiguation hints, object fields, or standalone `parse_type(...)` unless the
language restriction changes.

Bare `Function` is intentionally different from `Function[ -> ]`. The former is
an unknown-shape callable and triggers call-site type checking when it appears in
an overload parameter. The latter is a concrete niladic function type.

Element tags on function types are parsed after the `Function[...]` shape using
angle brackets. `!Tag` records a required tag absence. Tag arguments reuse
ordinary type parsing, so `Panic[String]` stores `String` as a type argument
rather than as text.

Tuple ellipsis is parsed after each tuple item, not as a postfix type operator.
This lets `{A..., B, C...}` lower to a single variadic tuple pattern with mixed
fixed and repeated segments. The three dots must be pairwise adjacent; whitespace
breaks the ellipsis.

## Generic Parameter Lists

Object-like declarations and function definitions parse generic parameter lists
before the declaration name:

```valiance
define[T: Vehicle] keep(value: T) -> T => ...
object[T] Box => ...
trait[T: any Vehicle] Readable => ...
variant[E: above Error] Result => ...
enum[T] Option => ...
```

The parser records generic names, optional variance markers, and optional bound
types on `ObjectNode` and `DefineNode`. `T: any U` records covariance plus the
bound `U`, `T: above U` records contravariance plus the bound `U`, and plain
`T: U` records the bound without an explicit variance marker. The analyser
rewrites matching type names into type variables and attaches bounds to
constructor/function overloads so overload application validates the solved
generic type after unification.

This declaration-local generic syntax is separate from ordinary type parsing:
outside a declaration's generic list, bare `T` is parsed as a nominal type name.
The analyser rewrites names that match the surrounding declaration's generic
parameters into type variables before storing object attributes, constructors,
function definitions, and requirements.

Function declarations may also carry element tags after the parameter list and
before the return arrow:

```valiance
define log(value: String)<IO> -> () => print(value)
eager define show(value: String) -> () => println(value)
```

The `eager define` spelling records the same parsed function node with the
`Eager` element tag attached.

Only `define` parameter lists may currently attach trailing `= <expr>` defaults.
Those defaults are recorded on `FunctionParam.default` for later ECS lowering;
they do not change ordinary stack-call arity.

## Data Tags

Data tags are tokenized by the lexer as a single `OP` token starting with `#`.
The parser converts them with `_tag_from_token`.

Supported forms:

```text
#sorted
#!infinite
#tag+
#tag++
#tag+3
```

A tag in expression position becomes `TagApplicationNode`. A tag before a type
becomes a `Tagged(...)` type.

`#!tag` represents tag removal or absence, depending on whether it appears in
expression or type position. The parser only records the syntax. Analysis
decides what it means for the current stack/type context.

## Adding An AST Node

Parser-facing AST work usually touches several files:

1. Add the dataclass to `src/valiance/asts/nodes.py`.
2. Export it from `src/valiance/asts/__init__.py`.
3. Parse it in `src/valiance/parsing/parser.py`.
4. Add display support in `src/valiance/asts/pretty.py` if useful for
   debugging.
5. Add parser tests in `tests/test_parser.py`.
6. Coordinate with `analysis-type-system-guide.md` and `runtime-codegen-guide.md`
   for later stages if the node should analyse or compile.

Prefer immutable dataclasses consistent with existing nodes:

```python
@dataclass(frozen=True)
class NewNode(ASTNode):
    values: tuple[ASTNode, ...] = ()
```

Use tuples, not lists, in AST fields. Lists are fine as temporary parser
accumulators.

## Adding Syntax

A good syntax-change workflow:

1. Read the relevant section of `docs/language.md`.
2. Decide whether the lexer needs a new token boundary.
3. Add or adjust tokens in `lexer.py`.
4. Add parser support in the smallest relevant method.
5. Preserve chain lowering by returning the right `_ChainPiece` flags.
6. Attach locations to all new AST nodes.
7. Add focused tests in `tests/test_parser.py`.
8. Run parser tests and then the full test suite.

Use these commands:

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"; uv run python -m unittest tests.test_parser -v
$env:UV_CACHE_DIR="$PWD\.uv-cache"; uv run python -m unittest discover -s tests -v
$env:UV_CACHE_DIR="$PWD\.uv-cache"; uv run ruff check .
```

## Common Pitfalls

Do not lose stack order.

If a syntax form participates in a chain, think carefully about whether it is an
element, a nilad, or a chain breaker. A wrong `breaks_chain` or `is_element`
flag often produces an AST that looks plausible but executes backwards.

Do not use `foo()` for nilads.

Empty argument and parameter lists are intentionally syntax errors. Niladic
elements need source-level context independence, so they use backslash-prefixed
names such as `\foo`.

Do not treat `|` as type union everywhere.

The lexer emits one pipe token. In expression parsing it is a chain separator.
In type parsing it is a union operator. Keep that distinction local to parser
mode.

Do not make modifier arguments ordinary stack nodes.

`element: chain` is bound to the element, because the analyser needs to match
function arguments to function-typed parameters independent of ordinary stack
argument order.

Do not silently accept empty call syntax.

Use `_argument_expressions`, not `_comma_expressions`, for element calls,
variable calls, annotation arguments, and other places where empty parentheses
would imply niladic behavior.

Do not give annotations parser semantics.

The parser records `AnnotationNode` values on declarations, function literals,
and annotated element nodes. Validation and behavior belong in
`analysis/annotations.py` so built-in annotations and future compiler-plugin
annotations use the same extension point.


Do not infer adjacency from whitespace-skipping lookahead.

`_peek()` intentionally hides `WHITESPACE`, so seeing two punctuation tokens in
successive lookahead positions does not prove that they touched in source. Use
`_adjacent` for operator runs, ellipses, `||`, rank continuation, and any future
whitespace-sensitive multi-token spelling.

Do not merge operators in the lexer.

`_operator()` must emit one `OP` token per character with correct offsets. Emitting
growing substrings creates overlapping duplicate tokens and prevents the parser
from making reliable adjacency decisions. Operator-symbol merging belongs in
`_operator_run()`.

Do not forget source locations.

Diagnostics rely on locations. If a new node is parser-created and may be
analysed, compiled, or shown to the user, give it a location.

## Current Gaps To Notice

The parser is intentionally incomplete relative to `docs/language.md`. Before
assuming syntax exists, check `docs/valiance-feature-checklist.md` and
`tests/test_parser.py`.

Known parser-facing gaps include:

- `_` placeholders and parent-stack substitution.
- Cast syntax such as `as Type` and `as! Type`.
- Full environment exports for object/trait/variant/enum/tag imports. The parser
  accepts the advanced import spellings, and define overload selection/exclusion
  plus `root`, `std`, and `dep` module resolution are implemented, but the module
  export surface still needs to carry non-`define` environment facts.
- `spawn`, `concurrent`, `external`, and user-defined `cast` declarations.

When implementing one of these, prefer adding the parser shape first, then
making the analyser/runtime reject it explicitly if later stages are not ready.
