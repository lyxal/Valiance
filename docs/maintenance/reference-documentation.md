# Built-in and standard-library reference documentation

Valiance keeps executable declarations and human-facing reference metadata close
together, then generates HTML, Markdown, or JSON from the resulting catalogue.
The generated reference covers globally available built-ins and every public
function shipped under `std.*`.

## Generate the reference

From any checkout or installed Valiance environment:

```text
vln docs --language
vln docs --language --format markdown
vln docs --language --format json
```

Without `--output`, files are written beneath the current directory:

```text
docs/language-reference.html
docs/language-reference.md
docs/language-reference.json
```

Use `--output -` to print the generated document to standard output, or choose a
path explicitly:

```text
vln docs --language --format json --output build/valiance-reference.json
```

The JSON form is versioned with `schema_version`. Documentation websites,
editor integrations, and language servers should consume that machine-readable
form rather than scraping HTML.

### Build all formats and continuous integration

From the repository root, generate a complete download from one strict collection:

```text
uv run python scripts/generate_reference_docs.py
uv run python scripts/generate_reference_docs.py --output build/reference
```

The output directory contains `index.html`, `language-reference.md`, and
`language-reference.json`. Builds use UTF-8 and LF newlines, with no timestamps
or absolute checkout paths, so identical input produces identical files.
Collection and rendering finish before files are written; missing metadata fails
the build instead of silently producing an incomplete catalogue.

The [reference workflow](../../.github/workflows/reference-docs.yml) runs on every
push, pull request, and manual dispatch using Python 3.14 and the frozen uv lock.
It runs `tests.test_reference_docs`, generates all three formats, and attaches
`language-reference-<commit SHA>` to the workflow run for 30 days. Download and
extract the artifact, then open `index.html`. No Pages configuration is required.
The workflow has read-only repository permissions and does not commit generated
files. Build outputs remain under the ignored `build/` directory.

### HTML presentation

The standalone HTML includes responsive section navigation, category badges,
stack signatures, parameter descriptions, examples, source paths, and related
entry links. Exact qualified names determine unique permalinks, including names
containing symbolic operators. Language forms have their own navigation group.
The sidebar drills down from scope (Built-ins, Standard library, Language forms)
to category or module, then to individual entries. Native disclosure controls
support keyboard expansion and work without JavaScript. Each group also has an
Overview link. Following a permalink expands its sidebar ancestors and marks
the current entry.

Search matches all displayed text, including aliases, parameter types, and
notes. All search words must match; the scope selector can limit results to
built-ins, standard library functions, or language forms. Counts and navigation
update with the results, and an empty state explains when nothing matches.
Filtering expands matching sidebar branches and hides nonmatching entries;
clearing filters restores the previous expanded/collapsed state.
Press `/` to focus search and Escape to reset filters. Following an entry link
resets filters to reveal the target.

The page follows the system light/dark preference, supports reduced motion and
printing, and loads no fonts, scripts, or styles from the network. Without
JavaScript, all documentation and navigation remain available and search controls
stay hidden. The maintainable source assets are
`src/valiance/elements/reference_docs.css` and `reference_docs.js`; the renderer
reads them as package resources and embeds them into each HTML file. Keep these
assets packaged when changing wheel build rules.

## Documentation metadata

Shared immutable metadata types live in `src/valiance/elements/documentation.py`:

- `ElementDocumentation` describes an element overview or one overload family.
- `ParameterDocumentation` describes one logical input.
- `DocumentationExample` stores source, an optional result, and optional prose.
- `element_documentation(...)` provides concise declaration syntax and normalizes
  strings and tuples into those dataclasses.

`ElementDocumentation` currently records:

- a one-line summary;
- longer description paragraphs;
- parameter descriptions;
- returned stack behavior;
- examples;
- category;
- notes; and
- related elements.

Keep summaries imperative and user-facing. Describe Valiance behavior, not the
Python implementation. Mention lazy/eager behavior, panic conditions, mutation
semantics, import requirements, or unusual stack ordering when those facts
matter to callers.

## Built-ins

`elements/builtins.py` remains the single static/runtime declaration source.
`BuiltinElement` now carries `documentation` and `canonical_name` fields.
Aliases such as `len` and `reduce` are registered normally but generated as aliases
of their canonical entries rather than duplicated as unrelated pages.

Core built-in metadata is stored in `_BUILTIN_DOCUMENTATION`; generated error
and fault constructors receive metadata from
`_message_type_documentation(...)`. The `@builtin(...)` decorator also accepts a
`documentation=` argument for declarations whose metadata is best created
locally or dynamically.

Give overloads with different meanings their own `documentation=` metadata.
Numeric division and collection reduction share `/`, but document separate
parameter names, results, failure conditions, and examples. Overloads with reversed
argument order need parameter descriptions in their actual stack order. Metadata
labels do not declare named-call parameters; use `param_names` for that language
feature.

The catalogue keeps a stable element overview from `_BUILTIN_DOCUMENTATION`
(or the first documented overload for elements without a shared entry).
Each definition retains its own metadata, including when reached through an alias.
HTML and Markdown show detailed overload documentation; JSON preserves it in
`overload_documentation`. Editor and LSP hovers include examples from the selected
overload.

Examples use valid Valiance syntax and show the resulting stack. Use `map: double`
or `fn => double end` for callables; parentheses alone evaluate an expression.
Every overload must have at least one example, including numeric variants,
conversions, native exports, and language forms. Use `examples=` on `@builtin`
to specialize examples without duplicating a shared description. Generated FFI
declarations build examples from their declared scalar, buffer, and shape types.
HTML and Markdown display examples immediately beneath each overload.

The reference tests check complete overload coverage and analyse every example.
Deterministic examples with stack results also execute and compare runtime display.
Interactive input, filesystem mutations, random output, and intentional failures
use explanatory descriptions instead of fixed stack results. Native boundary
examples are analysed locally; Windows FFI execution remains in CI.

When adding a built-in:

1. Declare and test its overload and runtime implementation.
2. Add an `ElementDocumentation` entry for its canonical name, or pass
   `documentation=` to `@builtin(...)`.
3. Add aliases with `@alias(...)`; do not add duplicate documentation entries.
4. Run `tests.test_reference_docs` so missing metadata or alias grouping fails
   immediately.
5. Inspect at least one generated format when the metadata shape changes.

## Python-backed standard library functions

`@stdlib_element(...)` accepts `documentation=`. The resulting
`NativeFunction.documentation` field is used by the collector, while the same
`NativeFunction` still supplies analyser and runtime declarations.

Example:

```python
@stdlib_element(
    "trim",
    (T.String,),
    (T.String,),
    param_names=("value",),
    documentation=element_documentation(
        "Remove leading and trailing whitespace from a string.",
        parameters=(("value", "String to trim."),),
        returns="The trimmed string.",
        category="Text",
    ),
)
def _trim(args, ctx):
    ...
```

Every public native stdlib export must have metadata. The strict collector raises
`DocumentationError` when one is missing.

## Valiance-defined standard library functions

Public functions in `src/valiance/std/*.vlnc` use normal `#??` documentation
comments. The language-reference collector reads those packaged source files,
extracts their analysed signatures, and merges them with Python-backed exports
from the same module.

```valiance
#?? Multiply a number by itself.
#??
#?? @param n Number to square.
#?? @returns The square of `n`.
#?? @example import {std.arithmetic.square} 3 square
public define square(n: Number) -> Number => $n $n *
```

An `@example` field contains one complete Valiance source snippet, including any
required imports. The language-reference collector turns it into structured
example metadata; editor hovers render it as a code block. Use a separate field
for each example.

A public stdlib definition without a contiguous `#??` block is a documentation
error. This deliberately differs from ordinary project documentation, where an
undocumented definition is simply omitted from `vln docs` output.

Source-language overloads retain their own documentation and examples beneath a
shared reference entry. Native hooks marked `exported=False` remain internal and
are excluded from the public reference.

## Collector and renderers

`src/valiance/elements/reference_docs.py` owns the generated catalogue:

- `collect_builtin_references()` groups aliases and renders every overload.
- `collect_stdlib_references()` combines native and Valiance-defined exports.
- `collect_language_references()` returns the complete deterministic catalogue.
- `render_language_reference_html(...)` creates a searchable standalone page.
- `render_language_reference_markdown(...)` creates portable prose reference.
- `render_language_reference_json(...)` creates versioned machine-readable data.

`ElementReference` is the renderer-neutral record. Add new presentation formats
by consuming that record rather than reaching back into compiler registries.

## Validation rules

The reference tests intentionally use strict collection. They fail when:

- a canonical built-in has no `ElementDocumentation`;
- a native stdlib function has no documentation metadata;
- a public Valiance stdlib definition has no `#??` block;
- aliases stop grouping with their canonical element; or
- one of the supported renderers cannot serialize the complete catalogue.

Documentation metadata does not enter bytecode and does not affect overload
identity. Keep it out of analyser comparisons and runtime dispatch plans.
