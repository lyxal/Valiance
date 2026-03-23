# The Valiance Programming Language

Valiance is a stack-based array language that moves beyond the traditional "notation as a tool of thought" dogma into "notation as a tool of _doing_". More specifically, Valiance takes the aspects of Iversonian array languages that provide such beautiful clarity of thought and balances them with practicality.

The defining features of Valiance are that it:

- Provides an inherently integrated interface to the array programming paradigm, while still being useful for software development.
- Favours conceptual brevity over literal brevity.
- Intentionally incorperates other programming paradigms like Object-Oriented and Functional Programming, rather than tacking them on as afterthoughts.
- Comes with a large suite of pre-made built-ins, rather than forcing users to build from a limited set of primitives.
- Strives to be accessible to more than just mathematicians and array-language fanatics.

Ultimately, Valiance acts to elevate array languages beyond rough sketches and algorithmatic prototypes. Valiance brings array languages to the software development table.

For more information, check out the [working docs](docs/langdocs.md).

## But Where's the Exact Specification?

That, unfortunately, does not yet exist. I only just finished the rewrite of the language overview. I need time to actually write the specs.

## But Where's the Code?

Right now, I'm going for a python implementation of Valiance. I tried doing a scala project, but metals just wasn't playing nice. Additionally, while Valiance will need to be ported to something faster at some point, I think it's worth creating a quick and dirty proof of concept to demonstrate language features instead of going fast. 

You can run the interpreter with

```
uv run valiance
```

or run the tests with

```
uv run pytest
```

## License

Licensed under either of:

- MIT License ([LICENSE-MIT](LICENSE-MIT) or http://opensource.org/licenses/MIT)
- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE) or http://www.apache.org/licenses/LICENSE-2.0)

at your option. 