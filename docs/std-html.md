# `std.html`: escaped, typed HTML generation

`std.html` is an independent standard-library module for composing `html.Node`
values. The `std.web` handler and `web.Response` adapter recognize these nodes
and produce **`text/html; charset=utf-8`**, whereas a plain `String` is still
plain text. The HTML builders do not open a server or validate the complete
HTML document/content model.

## Quick start

```valiance
import {std.web, std.web.App, std.html}

$app = App
$app := get("/"): fn(req: web.GetRequest) =>
  html.element("main", [
    html.h1 "Hello <world>",
    html.p "Escaped & safe",
    html.withAttrs(html.a "Documentation", dict{"href" => "/docs"})
  ], dict{"class" => "content"})
end
web.run($app, web.ServerConfig(port = 8766))
```

An ordinary `GET /` response is:

```html
<main class="content"><h1>Hello &lt;world&gt;</h1><p>Escaped &amp; safe</p><a href="/docs">Documentation</a></main>
```

For a runnable copy, start `samples/web/html_generation.vlnc` using the
normal `vln run --file` CLI, then visit `http://127.0.0.1:8766/`.

## Builders and composition

- Common element builders include `html.h1`–`html.h6`, `html.div`, `html.span`,
  `html.p`, `html.a`, `html.ul`/`li`, `html.table`/`tr`/`td`, `html.main`,
  `html.header`, `html.footer`, `html.form`, `html.button`, etc. Each accepts
  **one `String` or one `html.Node` child**; text input is escaped.
- `html.document(html.html(...))` wraps a single HTML root in `<!doctype html>` for standards-mode browser rendering.
- `html.text(value)` makes a safely escaped text node.
- `html.fragment([nodeA, nodeB])` joins nodes without a wrapper. The language requires an explicit type for bare empty list literals; use `html.empty` for no output.
- `html.element(name, [children], dict{"attr" => "value"})` creates a
  named element with any number of nodes and attributes.
- `html.withAttrs(node, dict{"id" => "header"})` adds attributes to an
  element node; duplicate names, including case variants, are rejected.
- `html.br(dict{"class" => "clear"})`, `html.hr`, `html.img`, and
  `html.input` accept attribute dictionaries. `html.lineBreak` and
  `html.horizontalRule` create unadorned void elements. Void tags reject children.
- `html.render(node)` returns the rendered string for use outside the web
  adapter, e.g. for logging or generating a static document. Only trusted
  `html.Node` values can be rendered.

For an explicit HTTP response, use
`web.Response(body = html.h1 "Hello")` or place any generated `html.Node`
in the `body` argument. The adapter chooses the HTML content type unless the
response already has an explicitly supplied content type. The normal route
handler form `=> html.h1 "Hello"` needs no response wrapper.

## Safety boundaries

- Text and attribute values are HTML-escaped, and invalid UTF-8 surrogate
  code points are rejected. Strings are **never** automatically treated as
  markup. There is deliberately no unescaped HTML injection constructor.
- `html.Node` includes a private integrity seal checked at rendering and at
  the Web response boundary. Forged or modified node values fail closed.
- Element and attribute names are validated. Active attributes (`on*`,
  `style`, `srcdoc`) and hazardous URL schemes (`javascript:`, `data:`,
  protocol-relative `//`, etc.) are rejected, including encoded entities that
  decode to URL control characters. Active/non-HTML contexts such as
  `script`, `style`, `iframe`, `svg`, and `math` are not available from the
  safe builders.
- This is an HTML **generation** API, not a sanitizer for arbitrary untrusted
  HTML, a JavaScript/stylesheet templating engine, or an implementation of the
  full HTML content model. Use CSP, authentication, CSRF protection and other
  deployment controls appropriate to your application.

The compiler/runtime checks are in `tests/test_std_html.py`, including a live
HTTP/1.1 request and optimized serialized bytecode. HTTP/3 is not required
for HTML generation; the separate H1/H2 web-preview status still applies.

## Practical HTML5 helpers

The basic builders remain compatible. For larger pages, use these additional
`html.Node`-returning functions:

- **Conditional and collection composition:** `html.when(condition, node)`
  emits a node or nothing; `html.choose(condition, yes, no)` selects a branch;
  `html.join([nodes], separator)` separates a collection; and
  `html.children("section", [nodes])` avoids supplying an empty attribute
  dictionary. Ordinary Valiance functions returning `html.Node` are reusable
  components. There is no separate template language or virtual DOM.
- **Attributes:** `html.withClass(node, value)`, `html.withId(node, value)`,
  `html.withData(node, name, value)`, and `html.withAria(node, name, value)`
  complement `html.withAttrs`. `html.withStyle(node, dict{"color" => "red"})`
  supports a small property/value allowlist for simple inline presentation;
  the generic `style` attribute remains blocked. Names supplied to `withData`/`withAria` are
  suffixes (for example, `"item-id"` becomes `data-item-id`).
  `html.withBoolean(node, "required", true)` adds a boolean attribute; `false`
  leaves the original node unchanged. The generic attribute dictionary also
  accepts boolean attributes using an empty string, e.g.
  `dict{"required" => ""}`. Writing `"false"` for a boolean attribute is an
  error, because HTML would interpret it as enabled.
- **Documents:** `html.page(title, body)` creates a doctype, a UTF-8 head,
  an escaped title, `<html lang="en">`, and a body; alternatively use
  `html.pageWithHead(title, body, [metadata], lang)` to add validated head
  nodes and an explicit language tag. `html.document(html.html(...))` is
  preserved for custom layouts.
- **Head metadata and resources:** `html.metaDescription(value)`,
  `html.meta(name, content)` (a restricted list of harmless names),
  `html.link(rel, href)` (known link relationships),
  `html.stylesheet(href)`, and `html.scriptSrc(src)` (an explicit external
  script with `defer`). Inline CSS, inline JavaScript, HTTP-refresh metadata,
  and arbitrary `script`/`style` elements are not provided by safe builders.
- **Forms:** `html.formFor("get", "/search", fields)`,
  `html.postForm("/contact", fields)`, `html.textField(name, label)`,
  `html.checkboxField(name, label)`, `html.hiddenField(name, value)`, and
  `html.submitButton(label)` construct ordinary HTML forms and controls.
  The helper creates markup only: it does **not** implement validation,
  authentication, CSRF tokens or form submission processing. A server must
  verify CSRF tokens and user permissions for state-changing submissions.

For a compiled full-page application with reusable components, a form, linked
stylesheet, and linked external script, see `samples/web/html_components.vlnc`.
It serves static resources from `samples/web/public` on `/assets`.

### Security and HTML5 limitations

Element names exclude foreign content and raw-text parsing contexts
(`script`, `style`, `plaintext`, `noscript`, `svg`, etc.). `title` and
`textarea` accept escaped text rather than nested markup. Untrusted text,
attribute values, metadata, form values, and link URLs use the same safe
serializer; HTML controls, invalid Unicode and unsafe or ambiguous URL syntax
are rejected. URI lists such as `srcset` and `ping` remain deliberately
unsupported until there is a dedicated parser for their grammar.

Attribute extension parses only the sealed element's opening tag and detects
duplicate names even when an earlier attribute contains words resembling
another attribute. Boolean attributes are emitted as presence-only HTML.
HTML's full element nesting/content model is not enforced; developers must
still use suitable parents/children and appropriate CSS/CSP policies. Linked
scripts are *explicitly executable resources* and must come from trusted
locations; the library does not guarantee third-party script safety.
