# The Valiance Programming Language

Valiance is a stack-based array language that moves beyond the traditional "notation as a tool of thought" dogma into "notation as a tool of _doing_". More specifically, Valiance takes the aspects of Iversonian array languages that provide such beautiful clarity of thought and balances them with practicality.

The defining features of Valiance are that it:

- Provides an inherently integrated interface to the array programming paradigm, while still being useful for software development.
- Favours conceptual brevity over literal brevity.
- Intentionally incorperates other programming paradigms like Object-Oriented and Functional Programming, rather than tacking them on as afterthoughts.
- Comes with a large suite of pre-made built-ins, rather than forcing users to build from a limited set of primitives.
- Strives to be accessible to more than just mathematicians and array-language fanatics.

Ultimately, Valiance acts to elevate array languages beyond rough sketches and algorithmatic prototypes. Valiance brings array languages to the software development table.

For more information, check out the [working docs](docs/language.md).

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
