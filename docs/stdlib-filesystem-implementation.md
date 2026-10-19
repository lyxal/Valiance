# Standard filesystem library — integration status

This checkout extends the existing archive (including its terminal editor and
`@nodup` work) with a usable, **integrated** `std.path` / `std.file` (sequential file I/O and native paths).

## Supported and verified

* `path.Path(String)` stores native-path spelling for filesystem use;
  on POSIX, surrogate-escaped string storage round-trips arbitrary legal
  filename bytes through `os.fsencode`/`os.fsdecode`. `path.toString` always
  returns String and carries an existing `@warn` compile-time annotation.
* Path operations `path.join`, `path.parent`, `path.name`, `path.extension`,
  `path.isAbsolute` use the native path representation. `join` accepts and
  returns nominal `path.Path` values; `parent`, `name`, and `extension` accept
  a nominal `path.Path` and return `path.Path?`, producing `None` when the
  component is absent. These operations are lexical: inspecting a component
  never changes the original path, resolves symlinks, or accesses the disk.
  Trailing separators are ignored for component inspection; filesystem roots
  have no name or parent.
* `file.Encoding.UTF8`, `UTF16LE`, `UTF16BE`, and `Latin1` are importable enum
  members.
* `file.ReadFile`, `file.WriteFile`, `file.AppendFile` are `@nodup` objects,
  backed by native handles and real Valiance destructor methods. The
  destructor flushes/closes on final release; explicit `file.close` and
  `file.flush` work. Flushing is not a durability sync.
* Their Valiance-defined constructors use an actual typed default
  `encoding: Encoding = Encoding.UTF8`, not a sentinel or a native overload.
  Both `file.WriteFile(path.Path("x"))` and a two-positional-argument call
  with a `file.Encoding` enum value work, alongside named argument calls.
  The `path` parameter is nominally typed as `path.Path`; a `String`, `Int`,
  or `Encoding` is rejected during analysis rather than at the native boundary.
* Stateful `file.readLine`, `readLines`, `readText`, `readBytes`,
  `readAllBytes` return reader then value; writes (`writeText`, `writeLine`,
  `writeLines`, `writeBytes`) return the continuing writer or appender.
* The `file.do` submodule provides one-shot `readText`, `readLines`,
  `readBytes`, `writeText`, `writeLines`, `writeBytes`, `appendText`,
  `appendLines`, and `appendBytes`. `file.do` is re-exported by `std.file`
  and can also be imported directly with `import {std.file.do}`.
  All operations take a nominal `Path`, open/close their own file, and do not
  return a continuing resource. The six text/line helpers accept an optional
  nominal `file.Encoding` argument (default `UTF8`), positionally or by name.
  Byte operations do not accept encodings. Unlike the stateful exact-count
  `readBytes`, `file.do.readBytes` reads the entire file.
* Byte values use `#byte`, a non-propagating unit tag. As with other units,
  operations must explicitly accept and return it. The validator rejects
  values outside the inclusive 0–255 range.
* New integration tests exercise ownership, writer destructor, append, reader
  results, UTF-16 encoding selection, compiler warning, native path bytes, and
  standalone convenience operations.

## Example (actual, tested syntax)

`std.file` and `std.path` may be imported in either order. Selecting `Path`
directly also preserves its native type identity:

```valiance
import {std.file, std.path.Path}
file.WriteFile(Path "out.txt") | file.writeLine "67" | pop
```

With a named writer, rebind the returned handle:
`$res = $res file.writeLine "67"`. File handles cannot be copied. A local
writer is closed when its enclosing function releases it; the pipeline above
releases it explicitly with `pop`.

```valiance
import {std.path, std.file}
file.WriteFile(path.Path("notes.txt"))
| file.writeLine "Hello"
| file.writeLine "World"
| pop

$text = file.do.readText(path.Path("notes.txt"))
```

Optional encoding can be supplied positionally or by name:
`file.WriteFile(path.Path("notes.txt"), file.Encoding.UTF16LE)` or
`file.WriteFile(path = path.Path("notes.txt"), encoding = file.Encoding.UTF16LE)`.
The default encoding is UTF-8 for both stateful resources and `file.do` helpers.

```valiance
$p = path.Path("utf16.txt")
file.do.writeText($p, "hello", file.Encoding.UTF16LE)
$text = file.do.readText($p, encoding = file.Encoding.UTF16LE)
$component = path.extension($p)  # Some(Path(".txt"))
```

The Valiance-native `file.do` entrypoints carry typed default expressions.
Their internal default expression is evaluated through a private, qualified
stdlib-native element; the module import bridge resolves this dependency
without leaking it into the public `file.do` API. Calls with a required
parameter prefix and trailing defaults bind omitted suffix arguments from
that declared default, like file constructors.

## Boundaries and future work

* Valiance requires overloads of one name to share stack arity. The
  `file.do` submodule resolves that conflict without changing overloading:
  `file.readText(reader)` is stateful while `file.do.readText(path)` is one-shot.
  This also establishes a possible stdlib design pattern for single-action
  conveniences in other modules.
* `#byte` is a unit tag with a 0..255 validator. Import it explicitly with
  `import {std.file.#byte}`. Byte readers return `#byte+ Int+` values and
  writers require tagged bytes; the native boundary retains range checks as
  a defence in depth. Selective tag imports carry the validator.
* Native filesystem failures are raised as catchable Valiance Fault panics,
  including `NotFoundFault`, `ParseFault` and `UnexpectedEOFFault` for exact
  byte reads. The Python-backed native bridge now declares its `Panic` effects
  so these operations work with typed `try/handle`. File constructors initialise
  their handle slot to a reserved invalid sentinel to ensure partial-constructor
  cleanup never masks the opening panic.
* Mixing buffered text and raw-byte reads is rejected to avoid hidden
  read-ahead effects. This prevents implicit cursor changes caused by text
  decoder read-ahead; a future API could add explicit mixed-mode semantics.
* Directory iteration, moving, copying, deleting, seeking, random access,
  async file I/O, and durability sync are not in this iteration.

## Validation commands

```sh
PYTHONPATH=src python -m unittest tests.test_std_file_integration tests.test_filesystem_core tests.test_stdlib_native -q
PYTHONPATH=src python -m unittest tests.test_runtime -q
PYTHONPATH=src python -m unittest tests.test_analyser tests.test_analysis_calls tests.test_analysis_expressions tests.test_analysis_state tests.test_import_conflicts tests.test_reference_docs tests.test_compiled_modules tests.test_trait_implementation_imports tests.test_std_file_integration tests.test_filesystem_core tests.test_stdlib_native -q
```

Focused file/path tests, the runtime regression suite, the analyser/import
regression suite, and the language-program suite passed separately. A combined
suite invocation exceeded the execution time limit without completing; no
claim is made that it or an entire repository-wide discovery run passed.
