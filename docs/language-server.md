# Valiance language server

Start the dependency-free stdio language server with:

```text
vln lsp
```

Editors should associate `*.vlnc` files with the language id `valiance`.
The server supports diagnostics, completion, source-like signature hover,
current-document definition lookup, document symbols, and formatting.

Hover output renders every visible overload as a Valiance signature, including
parameter names when available, parameter and return types, negative data-tag
requirements, and element tags. Compiler diagnostics use the parser/analyser's
one-based line and column and convert them to LSP's zero-based positions.
