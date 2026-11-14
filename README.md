# The Valiance Programming Language

Valiance is a stack-based array language that moves beyond the traditional "notation as a tool of thought" dogma into "notation as a tool of _doing_". More specifically, Valiance takes the aspects of Iversonian array languages that provide such beautiful clarity of thought and balances them with practicality.

The defining features of Valiance are that it:

- Provides an inherently integrated interface to the array programming paradigm, while still being useful for software development.
- Favours conceptual brevity over literal brevity.
- Intentionally incorperates other programming paradigms like Object-Oriented and Functional Programming, rather than tacking them on as afterthoughts.
- Comes with a large suite of pre-made built-ins, rather than forcing users to build from a limited set of primitives.
- Strives to be accessible to more than just mathematicians and array-language fanatics.

Ultimately, Valiance acts to elevate array languages beyond rough sketches and algorithmatic prototypes. Valiance brings array languages to the software development table.

For the current HTTP/1.1, TLS/HTTP/2 and WebSocket practical preview, see
[the `std.web` getting-started guide](docs/std-web-preview.md). This is a
limited developer preview, **not** an HTTP/3 or full-RFC production sign-off.
The guide explains supported use, functional checks, and remaining release limitations.

For more information, check out the [working language guide](docs/language.md).
The [worked examples](docs/examples.md) index the currently exercised sample
programs, including Conway's Game of Life, a Brainfuck interpreter, optional-safe
member chains, records, traits, and generic functions.

## Maintenance

Start with [`docs/maintenance/README.md`](docs/maintenance/README.md) before
changing the compiler or runtime. It links to an architecture tour, task-oriented
change playbooks, debugging guidance, a human-first [type-system guide](docs/maintenance/type-system.md), a [runtime and code-generator guide](docs/maintenance/runtime-system.md), and the production docstring policy.

## Testing

Run the full test suite in balanced parallel workers with:

```powershell
uv run python -m tools.test_suite
```

Use `--workers 1 --verbose` for serial debugging. To profile the slowest tests
and let subsequent runs use measured module durations to balance workers, run:

```powershell
uv run python -m tools.test_suite --workers 4 --durations 20 --timings .test-timings.json
```

The timings file is refreshed on each run and ignored by Git. The tests remain
ordinary `unittest` modules, so focused modules can still be run directly.
CI runs the main suite, trait oracles, and generics oracles in independent jobs.

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
that REPL state, and `:quit` to exit. REPL state can be explored transactionally
with `:branch`; use `:restore` to discard the current branch or `:continue` to
adopt it. `:copy [n]` duplicates the top values into the parent frame, while
`:escape [n]` moves them. The prompt displays the current branch depth.

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

### Terminal editor preview

The new full-screen editor is available from the checkout while its later stages
are developed. Bare `vln` and `valiance` still start the REPL described above.

```powershell
$env:UV_CACHE_DIR="$PWD\.uv-cache"
uv run python -m valiance.terminal_editor
```

File/Edit/View menus provide file workflows, editing and pane controls. F5 or
Alt+X runs the active buffer without saving, using unsaved open-file imports and
a fresh persistent session. Enter in REPL input submits a command; Shift+Enter
inserts a newline. F8 stops execution and resets runtime state. The editor keeps
open documents and its bounded transcript. Drag the dividers to resize panes;
F3/F4 toggle State/REPL and F6 cycles panes.

Startup opens a blank untitled document. Recent paths, wrapping, indentation
width and pane sizes are saved on normal quit; documents and runtime state are
not restored. Live semantic inspection, completion, Enter lock/history controls,
transcript export and themes remain later-stage work. See the
[current editor behavior](docs/maintenance/terminal-editor-design.md#current-terminal-implementation)
and [implementation plan](docs/maintenance/terminal-editor-implementation-plan.md)
for shortcuts, validation and remaining gates.

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

Pass arguments to a source or bytecode program after `--`:

```powershell
vln run --file main.vlnc -- --name Alice
vln exec --file main.vbc -- --name Alice
```

In the program, `import { std.cmdline }` then `cmdline.\argv` returns
`["--name", "Alice"]`. Arguments retain their order, including values starting
with `-`. The list excludes interpreter options and the program filename.
Without arguments after `--` (or without the separator), it is empty, as it is
in the REPL. Python callers can use `run(program, program_args=("one", "two"))`
or `VirtualMachine(program_args=("one", "two"))`; arguments belong to that VM.

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

For ordinary server-rendered web pages, `std.html` provides escaped typed HTML
nodes, reusable components, form and document helpers, and direct `std.web`
response integration. See the [HTML guide](docs/std-html.md) and runnable
[`html_components.vlnc`](samples/web/html_components.vlnc) example.

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

`vln init` guides terminal users through template and test choices with an inline
keyboard picker. Use Up/Down and Enter; Esc or Ctrl+C cancels before writing files.
The completion summary points to your starting files and lists the commands to
run, test, and build your project.

For a repeatable setup without prompts, supply the options explicitly:

```powershell
vln init myproject --template application --tests
vln init my_site --template web-app --tests
vln init my_library --template package --no-tests
vln init --list-templates
```

Run `vln run` from the web app project directory and open
<http://127.0.0.1:8080/> in your browser.

## License

Licensed under either of:

- MIT License ([LICENSE-MIT](LICENSE-MIT) or http://opensource.org/licenses/MIT)
- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or http://www.apache.org/licenses/LICENSE-2.0)

at your option.


### `std.web` H1/H2 practical developer preview

For a complete HTTP/WebSocket starter, see
[`samples/web/feature_demo.vlnc`](samples/web/feature_demo.vlnc) and the
[`std.web` practical preview guide](docs/std-web-preview.md).
The [consolidated implementation record](docs/maintenance/web.md)
distinguishes tested everyday features from partial contract parity and missing
HTTP/3. This is a local/controlled-deployment preview rather than a final
RFC/production sign-off. Python 3.14+ is required for supported installation.
