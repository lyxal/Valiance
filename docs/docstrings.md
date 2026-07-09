# Documentation comments and source tooling

Valiance documentation comments use `#??`. The extra question mark makes them
visibly distinct from ordinary `#?` comments while remaining comments to the
language lexer and runtime.

A documentation block is a contiguous group of `#??` lines immediately before
a `define`. Declaration annotations may appear between a documentation block
and the definition.

```valiance
#?? Return `value` multiplied by two.
#??
#?? @param value The number to multiply.
#?? @returns The doubled number.
public define double(value: Number) -> Number => $value 2 *
```

The first section is free text. Blank `#??` lines separate paragraphs. Inline
text between backticks is rendered as code in generated HTML.

The initial structured fields are:

- `@param <name> <description>` documents a value parameter.
- `@typeparam <name> <description>` documents a generic type parameter.
- `@returns <description>` documents the complete returned stack effect.

Unknown `@` fields are retained in generated references under an “Other”
heading. This allows the format to grow without making older documentation
generators discard information.

## Adding documentation stubs

`vln tidy --docstrings` inserts a stub for every undocumented `define`. It uses
parameter, generic, and return information already present in the declaration.
Combine it with inferred signatures and formatting when desired:

```text
vln tidy --types --docstrings --format
vln tidy src/math.vlnc --docstrings
```

With no file, `vln tidy` processes every `.vlnc` file in the current project,
including project tests. It excludes `.vln`, `bin`, VCS metadata, and common
build-output directories. A file argument limits the rewrite to that file.
`--stdout` previews a single result instead of writing it.

When none of `--types`, `--docstrings`, or `--format` is supplied, `tidy`
defaults to `--types`. The old `vln annotate` command remains as a compatibility
alias for `vln tidy --types --stdout` on one file or an inline snippet.
The type pass fills missing parameter or return annotations and preserves
signature components that were already written explicitly.

The formatter only normalizes leading indentation. It uses two spaces per
multiline `=>` block and does not change expression spacing or line wrapping,
because indentation is not semantically meaningful in Valiance.

## Generating an HTML reference

Generate one file's reference beside it:

```text
vln docs src/math.vlnc
```

Generate a project-wide reference at `docs/reference.html`:

```text
vln docs
```

Use `--output` to choose another destination and `--title` to override the page
title. The generated file is self-contained HTML with an inline stylesheet and
contains only definitions that have `#??` documentation blocks.
