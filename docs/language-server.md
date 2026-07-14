# Valiance language server

Valiance includes a dependency-free Language Server Protocol server. Start it
through the normal command-line entry point:

```text
vln lsp
```

The server communicates over standard input and output using the LSP
`Content-Length` framing and JSON-RPC 2.0. Editors should register `*.vlnc`
files with the language id `valiance` and launch the command above.

The initial implementation supports:

- full-document open/change/close synchronization;
- parser, analyser, warning, and lint diagnostics;
- completion for keywords, types, tags, declarations, and built-ins;
- signature hover for elements;
- go to definition for definitions in the current document;
- document symbols for definitions; and
- whole-document two-space indentation formatting.

The implementation deliberately calls the normal Valiance parser and analyser,
so editor diagnostics and command-line compilation use the same language rules.
It has no editor-specific dependency and can be used by VS Code, Neovim, Emacs,
Helix, Zed, or any other client that can launch a stdio language server.
