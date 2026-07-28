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

## Documentation metadata

Shared immutable metadata types live in `src/valiance/elements/documentation.py`:

- `ElementDocumentation` describes one logical element across all overloads.
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

`analysis/builtins.py` remains the single static/runtime declaration source.
`BuiltinElement` now carries `documentation` and `canonical_name` fields.
Aliases such as `len` and `reduce` are registered normally but generated as aliases
of their canonical entries rather than duplicated as unrelated pages.

Core built-in metadata is stored in `_BUILTIN_DOCUMENTATION`; generated error
and fault constructors receive metadata from
`_message_type_documentation(...)`. The `@builtin(...)` decorator also accepts a
`documentation=` argument for declarations whose metadata is best created
locally or dynamically.

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
public define square(n: Number) -> Number => $n $n *
```

A public stdlib definition without a contiguous `#??` block is a documentation
error. This deliberately differs from ordinary project documentation, where an
undocumented definition is simply omitted from `vln docs` output.

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
