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

The package entry point is available as:

```powershell
uv run valiance
```

## License

Licensed under either of:

- MIT License ([LICENSE-MIT](LICENSE-MIT) or http://opensource.org/licenses/MIT)
- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or http://www.apache.org/licenses/LICENSE-2.0)

at your option. 
