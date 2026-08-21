# The Valiance Programming Language

Valiance is a stack-based array language that moves beyond the traditional "notation as a tool of thought" dogma into "notation as a tool of _doing_". More specifically, Valiance takes the aspects of Iversonian array languages that provide such beautiful clarity of thought and balances them with practicality.

The defining features of Valiance are that it:

- Provides an inherently integrated interface to the array programming paradigm, while still being useful for software development.
- Favours conceptual brevity over literal brevity.
- Intentionally incorperates other programming paradigms like Object-Oriented and Functional Programming, rather than tacking them on as afterthoughts.
- Comes with a large suite of pre-made built-ins, rather than forcing users to build from a limited set of primitives.
- Strives to be accessible to more than just mathematicians and array-language fanatics.

Ultimately, Valiance acts to elevate array languages beyond rough sketches and algorithmatic prototypes. Valiance brings array languages to the software development table.

For more information, check out the [working language guide](docs/language.md).
The [worked examples](docs/examples.md) index the currently exercised sample
programs, including Conway's Game of Life, a Brainfuck interpreter, optional-safe
member chains, records, traits, and generic functions.

## Maintenance

Start with [`docs/maintenance/README.md`](docs/maintenance/README.md) before
changing the compiler or runtime. It links to an architecture tour, task-oriented
change playbooks, debugging guidance, a human-first [type-system guide](docs/maintenance/type-system.md), a [runtime and code-generator guide](docs/maintenance/runtime-system.md), and the production docstring policy.

## Testing

Run the full test suite with:

```powershell
uv run python -m unittest discover -s tests -v
```

Run individual test modules with:

```powershell
uv run python -m unittest tests.test_main -v
uv run python -m unittest tests.test_types -v
```

## Current Code

For day-to-day CLI use, install the editable command once:

```powershell
uv tool install --editable .
```

After that, the short command is available without `uv run`:

```powershell
vln
```

Running `vln` with no arguments starts the REPL. Stack state, variables,
definitions, and imports persist between entered lines. Use `:reset` to clear
that REPL state, and `:quit` to exit.

On an interactive terminal, the REPL uses an enhanced prompt with Valiance
syntax highlighting, completion for language keywords, built-ins, user
definitions, variables, tags, and types, plus inline suggestions from command
history. Press `Tab` or `Ctrl-Space` to open completions and `F2` to toggle the
live stack-type preview shown beneath the input line.

Type information is also available in every terminal mode without executing the
source:

```text
vln:1> :type 1 2 +
Types: [] -> [Int]
```

Redirected input, `TERM=dumb`, or an unavailable enhanced-prompt dependency
uses the portable plain REPL automatically. Set `VALIANCE_REPL_MODE=plain` to
force that mode; set it to `fancy` to request the enhanced frontend explicitly.
Both frontends use the same analyser, persistent session state, compiler, and
virtual machine.

If you prefer to keep the command only inside the project virtual environment,
use `uv pip install -e .` instead and activate `.venv` before running `vln`.
You can still run through uv without installing either way:

```powershell
uv run vln
```

Compile project entries to bytecode:

```powershell
cd myproject
vln compile
vln compile server
```

The `main` entry is compiled when no entry name is supplied. Project bytecode
is written under `bin/` using the entry name:

```text
bin/main.vbc
bin/server.vbc
```

Compile an arbitrary source file explicitly with `--file`, or compile inline
code with `--code`:

```powershell
vln compile --file samples/strings.vlnc
vln compile --code '"hello" println' --output C:\tmp\hello.vbc
```

Bytecode optimisation is enabled by default for both `compile` and `run`. The
default pipeline folds constants, inlines small constant functions, materialises
proven scalar cycle inputs, simplifies bytecode and stack shuffles, and cleans up
control flow. Use `--no-optimize` on either command when inspecting or comparing
the direct code-generator output. Checked-in differential workloads live under
`samples/optimizations/`.

Run Valiance tests declared under a project's `tests/` directory:

```powershell
vln test
vln test arithmetic.division
vln test --list --flat
```

Tests use `@test` and `@testgroup` on niladic definitions. See
[`docs/testing.md`](docs/testing.md) for the source API, standard-library
assertions, selectors, and runner options.

Run project entries without writing bytecode:

```powershell
cd myproject
vln run
vln run server
```

Project entry points are declared in the manifest. The `main` entry is used when
no name is supplied:

```toml
[entries]
main = "src/main.vlnc"
server = "src/server.vlnc"
```

Run an arbitrary source file explicitly with `--file`, or run inline code with
`--code`:

```powershell
vln run --file samples/strings.vlnc
vln run --code "1 2 +"
```

Inline `run --code` snippets print the final stack automatically when the code
does not print anything itself.

Inspect compiler stages:

```powershell
vln parse samples/strings.vlnc
vln analyse samples/strings.vlnc
```

Rewrite source with inferred signatures, documentation stubs, and consistent
two-space indentation:

```powershell
vln tidy src/main.vlnc --types --docstrings --format
vln tidy --docstrings --format
```

With no file, `vln tidy` processes every Valiance source file in the current
project. The legacy `vln annotate` command remains available as a print-only
alias for inferred signatures.

Documentation comments start with `#??`. Generate a self-contained HTML
reference for one file or the whole project with:

```powershell
vln docs src/main.vlnc
vln docs
```

Generate the built-in and standard-library reference directly from compiler
metadata and packaged stdlib sources:

```powershell
vln docs --language
vln docs --language --format markdown
vln docs --language --format json --output docs/language-reference.json
```

The JSON form is versioned for editor, website, and language-tool integrations.

See [`docs/docstrings.md`](docs/docstrings.md) for the comment format, supported
fields, output defaults, and language-reference generation.

Execute previously compiled bytecode without recompiling:

```powershell
cd myproject
vln exec
vln exec server
```

`vln exec` runs `bin/main.vbc`; a named entry such as `server` runs
`bin/server.vbc`. Execute an arbitrary bytecode file explicitly with `--file`:

```powershell
vln exec --file C:\tmp\hello.vbc
```

Create and manage projects:

```powershell
vln init myproject
cd myproject
vln run
vln add somelib 1.2.3
vln install
```

## License

Licensed under either of:

- MIT License ([LICENSE-MIT](LICENSE-MIT) or http://opensource.org/licenses/MIT)
- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or http://www.apache.org/licenses/LICENSE-2.0)

at your option. 
