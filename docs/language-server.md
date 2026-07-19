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

## Go to definition

The server advertises the standard LSP `definitionProvider` capability. Editors
using an LSP client, including the Valiance VS Code extension, therefore support
Ctrl+click, F12, and **Go to Definition** without an extension-specific command.

Definition lookup supports:

- definitions in the current document;
- explicitly imported project functions;
- imports renamed with `as`;
- namespace-member imports;
- `root` imports;
- installed `dep` package imports, including transitive package-local imports;
- standard-library source modules when source is available.

The result is an ordinary LSP `Location` containing a file URI and the exact
source range of the declaration. VS Code opens closed target files automatically.
If a module cannot be read or parsed, or if the selected token is not a known
declaration, the server returns no location rather than surfacing an internal
error.

## Hover information

Hover uses analysed program data rather than token spelling alone:

- local and imported functions show every visible overload signature;
- imported function documentation is loaded from the source declaration,
  including descriptions, parameters, type parameters, returns, and extra fields;
- variables show their inferred or declared analyser type at that occurrence.

Imported documentation follows `root`, local, standard-library, and installed
`dep` source modules. If source is unavailable (for example, a compiled-only
module), signatures remain available but source docstrings may not be shown.
Variable hover is flow-sensitive to the typed occurrence retained by analysis,
so it reflects the type known at that read or assignment rather than guessing
from text.
