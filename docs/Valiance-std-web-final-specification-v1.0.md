# Valiance `std.web` — Final Normative API and Implementation Specification

**Version:** 1.0 · **Date:** 11 November 2026  
**Status:** Final design contract, ready for implementation; not a claim that `std.web` is already implemented.  
**Authoritative language baseline:** `Valiance-main(16).zip`, particularly `docs/language.md`, `src/valiance/std/json.vlnc`, `src/valiance/std/file.vlnc`, named-handler compiler and VM changes, and the current language tests.  
**Replaces:** All earlier `Valiance-std-web-API-design-plan*.md` drafts and their superseded choices.  
**Scope:** The complete supported `std.web` application API, semantic contracts, error model, request/response handling, routing, middleware, HTTP/1.1–3, lifecycle, streams, WebSockets, operational safeguards, conformance, and implementation acceptance gates. `std.html` remains a separate module.

> **Conformance rule:** In this document, **MUST**, **MUST NOT**, **SHOULD**, and **MAY** have their ordinary normative meanings (RFC 2119/8174). The syntax and types given in the *public contract* are binding. Certain code examples are **target Valiance**, not an assertion that new library elements already compile. Implementers MUST preserve Valiance grammar, existing function calling and ownership mechanisms, implicit Result unions, and actual `try/handle` semantics; when a typed signature is presented in mathematical/contract notation, it is the required callable constraint, not a request to add a language syntax feature.

## 0. Executive contract

A Valiance web application is an **immutable, protocol-independent** `web.App` composed from handlers, ordinary `web.Router` values, route groups, and middleware. The smallest valid program is:

```valiance
import {std.web, std.web.[get]}

$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "Hello, world!"
run $app
```

A handler returns **one** of `String`, `html.Node`, `web.Response`, or an ordinary `Err` implementing value; a union of non-`Err` and `Err` is **already** a Valiance `Result`. No `Ok` wrapper, no web-specific `Result`, and no implicit JSON encoding. `String` becomes 200 `text/plain`; `html.Node` becomes 200 `text/html`; `Response` is used as-is; an `Err` is mapped to an HTTP error response. A catchable panic is isolated by normal `try/handle`, logged, and mapped to a safe 500. Unrecoverable Valiance integrity faults and structured cancellation remain outside that catch-all recovery policy.

Middleware is an ordinary callable returning `Request`, `Response`, any `Err`, or `record{request, after}`; registered `after` callbacks transform the final `Response` in reverse order. There is **no `next` callback**. Request methods are distinct nominal types and preserve their identity through middleware. Routing is deterministic and compositional, independent of registration order.

An `App` can be served with blocking `run`, held in an explicit owned server lifetime with `web.serve`, or executed without sockets via `web.dispatch`. The same route/middleware/error pipeline MUST be used by all three. HTTP/1.1, HTTP/2, and HTTP/3 are requirements of the first complete implementation, not deferred architecture.

## 1. Scope, dependencies and invariants

### 1.1 Included

1. Immutable application and router construction; module-composable routers; path groups; application and group middleware.
2. GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS and **QUERY (RFC 10008)**; method-specific request objects and handlers; deliberate restrictions for methods without defined request-content semantics.
3. Strongly specified path matching, decoding, methods, errors, automatic HEAD/OPTIONS, and URL generation.
4. Request metadata, route captures, multi-valued query/form parameters, typed JSON, text/binary bodies, cookies, headers, content negotiation, multipart/form upload, bounded and streaming input.
5. Text, HTML, JSON, bytes, explicit responses, cookies, redirects, creations, static files, downloads, validators, cache policy, conditional/range responses, bounded and streamed output, server-sent events, and optional compression.
6. Returned `Err` mapping, catchable panic isolation, custom `onError` policy, after-middleware unwind, reporting and cancellation semantics.
7. HTTPS/TLS, HTTP/1–3, QUIC, connection/resource limits, proxy trust, structured concurrency, start/stop, signal policy and graceful drain.
8. Socket-free dispatch, synthetic requests, introspection, exhaustive conformance and adversarial tests.
9. WebSocket route and connection API, including HTTP/1.1 upgrade and HTTP/2/3 extended CONNECT.

### 1.2 Intentional API boundaries (final, not placeholders)

- `std.html` is independent. `std.web` MUST NOT expose `web.html`, re-export HTML tags, invent a templating language, or validate the complete HTML content model. `html.Node` body recognition is the only coupling.
- `std.json` owns JSON encoding and decoding. `std.web` owns HTTP media-type checking, error conversion and status/headers. It MUST NOT invent a second JSON serializer or serialize arbitrary returned objects implicitly.
- `std.concurrency.State[T]`, channels, ordinary closures and application-owned services provide shared state. There is no `web.State`, `web.Store`, implicit service locator, session database, or magic global state.
- No automatic authentication, authorization, session issuance, CSRF protection, database transactions, or schema validation beyond typed JSON and HTTP parsing. These are **application policies**, expressed using explicit middleware/functions. Secure cookie defaults and an opt-in CORS middleware are included; they do not claim to solve those application policies.
- No automatically enabled compression, CORS, caching of arbitrary dynamic responses, redirects between slash variants, speculative retry of unsafe handlers, or public transport framing controls. The app cannot edit HTTP/2 frames, HTTP/3 frames, or TCP connection internals.
- No built-in outbound HTTP client; `std.web` is the **server-side web application API**, and client HTTP belongs in a separate standard-library facility. No support for acting as a forward HTTP proxy (general CONNECT), generic HTTP tunnel, arbitrary RPC transport, or TRACE echo service. These are deliberately rejected, not unfinished parts of `std.web`.

### 1.3 Baseline Valiance facts (must not be changed to simplify the framework)

- `T | E`, with `E as Err`, is implicitly `Result[T,E]`; multiple OK or error branches normalize without explicit wrappers. Both `?` and `?!` already exist; **`?` propagates ordinary errors, `?!` panics** on failed unwrap.
- A source-language `handle` that completes returns its ordinary stack value **from its lexically enclosing function**; no `PanicError` wrapper is inserted. `handle as fault: ValueFault =>` binds a concrete original fault; `handle as fault =>` binds the original fault as `Fault`.
- `@nodup` forbids duplication of owned resources; RAII and structured concurrency govern cleanup. Resource-bearing responses MUST propagate this ownership constraint; no implicit dup of streaming bodies or handles.
- `define` does not capture ambient module-top-level assigned variables; use function-local closure capture or explicit parameters. Qualified `module.\nilad` **evaluates a niladic element** and is **not** a function reference.
- ECS, modifiers (`get("/"): callback`), typed `fn`, `record{}`, `dict{}`, normal `?`, and nominal objects/variants are existing mechanisms. No macros, special new syntax, varargs, or magic derived attributes are authorised by this specification.

### 1.4 Reference standards

Normative transport and semantic targets are RFCs **9110** (HTTP Semantics), **9111** (Caching), **9112** (HTTP/1.1), **9113** (HTTP/2), **9114** (HTTP/3), **9000/9001** (QUIC/TLS), **10008** (QUERY), **8259** (JSON), **6266/8187** (Content-Disposition/extended parameters), **7578** (multipart/form-data), **6265** plus current HTTPbis cookie interoperability guidance, **6455** (WebSockets), **8441/9220** (WebSockets over HTTP/2/3), **7233** as superseded by 9110 for range handling, and relevant TLS/URI/security specifications. Standards compliance and cross-protocol equivalence take precedence over illustrative examples.

## 2. Public module inventory and precise kinds

The public module is exactly `std.web`. **Import accuracy matters:** `import {std.web}` introduces the qualified `web` namespace; it does *not* automatically import unqualified `get`, `post`, `group`, or `middleware`. An explicit selective import of the public object `App` (for example `import {std.web.App}`) automatically selects its object-friendly native overloads `get`, `head`, `post`, `put`, `patch`, `delete`, `query`, `options`, `group`, and `middleware` for unqualified ECS/modifier use. An explicit import of `Router` analogously selects its router-compatible overloads. This import association does **not** introduce new instance-method call syntax or change how the leading target argument is passed. The imports `import {std.web, std.web.[get, post, group, middleware]}` and qualified calls such as `web.get` remain valid. Examples that omit a full import list assume these selected imports are already in scope. Exports are organized here by concept, not into additional namespaces:

| Kind | Public symbols |
|---|---|
| Config/value objects | `App`, `Router`, `ServerConfig`, `TLS`, `Limits`, `Headers`, `Params`, `MultipartForm`, `Upload`, `RequestContext`, `ContextKey[T]` |
| Request types | `Request` (closed common variant), `GetRequest`, `HeadRequest`, `PostRequest`, `PutRequest`, `PatchRequest`, `DeleteRequest`, `QueryRequest`, `OptionsRequest` |
| HTTP enum/variants | `Method`, `Status`, `Protocol`, `BodyMode`, `SameSite`, `CacheVisibility`, `Failure`, `ErrorStage`, `ServerStopReason` |
| Errors implementing `Err` | `Error` (HTTP/application request errors), `ConfigError`, `ServerError`, `DispatchError`, `BodyError` (wrapped/translated to `Error` at request boundary) |
| App/router registration | `get`, `head`, `post`, `put`, `patch`, `delete`, `query`, `options`, `group`, `middleware`, `withErrorHandler`, `prepare`, `routes` |
| Request utilities | `queryParams`, `formParams`, `paramValues`, `paramOne`, `requireParam`, `header`, `headerValues`, `cookie`, `cookies`, `bodyBytes`, `bodyText`, `bodyJson[T]`, `bodyStream`, `multipart`, `multipartStream`, `trailers`, `accepts`, `contentType`, `context`, `requireContext`, `withContext`, `allowedMethods` |
| Response functions | `Response`, `jsonResponse`, `created`, `redirect`, `withStatus`, `withHeader`, `addHeader`, `withoutHeader`, `withCookie`, `deleteCookie`, `withTrailers`, `file`, `page`, `download`, `staticRouter`, `noStore`, `cachePublic`, `cachePrivate`, `withETag`, `withLastModified`, `preconditions`, `conditional`, `compression`, `cors` |
| Streaming/events | `ByteSource`, `StreamBody`, `stream`, `eventStream`, `Event`, `UploadStream`, `collectBody`, `responseText` |
| WebSocket | `websocket`, `WebSocket`, `WebSocketMessage`, `websocketSendText`, `websocketSendBytes`, `websocketReceive`, `websocketPing`, `websocketClose` |
| Server and testing | `run`, `serve`, `ServerHandle`, `shutdown`, `wait`, `boundAddresses`, `dispatch`, `testRequest`, `testRequestStream` |

No symbol is a license to invent language-level implicit conversions: each accepts and returns the specific shapes defined below. Public helper names and case names in this version are fixed. Internal classes, adapters and native bindings are implementation details.

### 2.1 Exact public conventions

- `String+` denotes a rank-1 Valiance list of strings (it **can be empty**), not a nonempty list. All collection-returning lookup operations preserve on-wire order unless the API specifies otherwise.
- `Bytes` below means a rank-1 list of byte-tagged integers, **`#byte Int+`** from `std.file`; it is used as an explanatory alias, not a new language type that requires a compiler feature. No accidental UTF-8 decoding of byte bodies occurs.
- `String?` is Valiance's optional (`String | None`). A `String? | Error` is implicitly an ordinary `Result`.
- A target notation of `X -> Y | Err` describes an ordinary function returning a union, not a wrapper. Higher-order `Function[...]` constraints use normal generic analysis.
- An `App` and `Router` are pure immutable values. Runtime readiness is `PreparedApp` (an opaque internal/native validated routing snapshot). Preparing MUST NOT start network listeners or execute routes.
- `Response` is **one** public nominal type, not separate response classes for text, JSON, files, and streams. The body carries a private semantic variant. A response holding owned stream resources becomes nonduplicable as required by transitive ownership.

## 3. App, Router, registration, groups

### 3.1 Construction

```valiance
$app = web.App
$router = web.Router
```

These are zero-argument constructors accessible with the established nominal-constructor spelling (not `web.App()`/`web.Router()` as a special new niladic call rule). `App` holds routes, grouped middleware, error policy and configuration-independent metadata. `Router` holds routes and nested groups but **no server**. Neither registers any global process state.

### 3.2 Route registration

Canonical use:

```valiance
$app := get("/hello"): fn (req: web.GetRequest) => "Hello!"
$router := post("/users"): fn (req: web.PostRequest) => createUser($req) ?
```

Eight verbs are provided: `get`, `head`, `post`, `put`, `patch`, `delete`, `query`, `options`. Each has a conceptual overload of the form

```
verb(target: App|Router, path: String, handler: Function[MethodSpecificRequest -> H]) -> same target kind
where H is String | html.Node | Response | an Err type (or an implicit Result union thereof)
```

The target is the leading pipeline value; the callback is a higher-order modifier argument after `:`; a function object may also be supplied using ordinary ECS. The implementation MUST provide ordinary public Valiance-native overloads and static checking, not a custom parser rule for `get(...)`. Route handlers receive exactly one request argument; no magic injection of parameters or services. Handler return arity is exactly one.

If an ordinary non-`Err` output has no defined HTTP conversion (e.g., `Int`, `None`, arbitrary managed object, tuple/multi-return), route registration fails static typing. If the handler can return an `Err`, that branch is accepted automatically. `web.jsonResponse` is required for JSON.

Registration returns a new App/Router. Earlier references remain unchanged. Route method/path conflicts are validated during `prepare`, `run`, `serve`, or `dispatch` (before serving) as `ConfigError`. Those operations are not allowed to silently pick a registration winner.

### 3.3 Mounting a router

Both of the following are **the entire public overload set for the group content argument**:

```
group(target, prefix, router: Router, middleware: Middleware+ = []) -> target
group(target, prefix, builder: Function[Router -> Router], middleware: Middleware+ = []) -> target
```

There is **no third "router-producing zero-argument function" overload**. A qualified niladic element call already produces its Router value.

**Separate module:**

```valiance
# admin.vlnc
import {std.web, std.web.[get, post]}

public define \adminRoutes =>
  $router = web.Router
  $router := get("/whatever"): whateverHandler
  $router := post("/blah"): blahHandler
  $router
end
```

**Main application:**

```valiance
import {std.web, std.web.[group]}
import {admin}

$app = web.App
$app := group(
  "/admin",
  admin.\adminRoutes,
  middleware = [$requireAdmin]
)
```

Here `admin.\adminRoutes` **evaluates a niladic element** and returns an ordinary Router. It is not a function reference. Direct value mounting and `fn (routes: web.Router) => ...` building have identical mount semantics; builder invoked **once** during app construction, with an empty Router, not for each request.

```valiance
$app := group("/api", middleware = [$auth]): fn (routes: web.Router) =>
  $routes := get("/items"): listItems
  $routes := post("/items"): createItem
  $routes
end
```

Nested group prefixes concatenate segment-wise; mount `"/users"` plus child `"/"` yields `/users` rather than `/users/` or `//users`. Router values may be mounted repeatedly or into different apps; no mutable aliasing or duplicated listener ownership.

### 3.4 Configuration order

App-level `middleware` registration is **global** regardless of whether routes were added before or after it. Its ordering relative to other middleware registrations follows the sequence of those registrations. Group middleware is appended outside-to-inside following the selected mounted route. Child router middleware is innermost after group-provided middleware. All group/router composition is pure and immutable.

### 3.5 Route introspection

`web.routes(appOrRouter)` returns immutable route descriptors: source module/registration location, method, template, effective composed path (after mount on App), middleware scope IDs, and explicit versus automatic HEAD/OPTIONS status. It MUST NOT expose handler closures, captures, TLS keys, private context values, or native connection state. Introspection performs deterministic route preparation and returns `ConfigError` for invalid tables.

## 4. Router matching, method semantics, and URL identity

### 4.1 Path template grammar

A route template is a UTF-8 string starting with `/`. Supported segments are: (a) nonempty literal segments; (b) one-segment captures `{name}`; and (c) terminal catch-all `{*name}` matching zero or more remaining segments. Capture names match `[A-Za-z_][A-Za-z0-9_]*`, are unique per route, and do not collide with reserved internal names. An empty root template is rejected; use `/`. Query strings, fragments, optional segments, regex segments, and inline converters are **not** part of route templates. Use ordinary parsing and `Err` in handlers.

Examples: `/`, `/users`, `/users/{id}`, `/files/{*path}`. A catch-all MUST be final. Empty path segments from accidental `//` are not silently dropped; templates with double slashes are invalid. Dynamic captures use the decoded logical segment string, not undecoded URI syntax. Catch-all captures are joined using `/`, without leading slash, and are never treated as filesystem paths.

### 4.2 Request target validation and decoding

On receipt, all transports MUST share one canonical target validator. Accept origin-form path-and-query targets for ordinary origin requests; absolute-form targets are accepted only if normalised to the same validated effective authority by an explicitly configured trusted proxy mode; authority-form is not a regular route target. Fragments are invalid in HTTP request targets. Validate encoding before any route/middleware code.

- Separate the path from the query on the first literal `?` **before** decoding percent escapes. Preserve the raw request target separately for diagnostics, but never route on it.
- Reject malformed `%` escapes, invalid UTF-8 in decoded path or query, embedded NUL/control codepoints, raw backslashes, percent-encoded `/` or `\\` in individual path segments, and ambiguous dot segments (`.`/`..`, including encoded equivalents). Percent-decode each segment **exactly once**; do not perform a second decode in a later routing or static-file layer.
- Reject ambiguity that could cause a downstream proxy or filesystem adapter to reinterpret a path after routing, including double-encoded path separators in security-sensitive paths. Never normalise away security-significant encoded separators.
- Match by Unicode scalar sequence after strict UTF-8 decoding, with **case-sensitive** comparisons and no implicit Unicode normalization. Application authors may normalise application data explicitly.
- `/users` and `/users/` are **different routes**. There are no automatic slash redirects and no implicit trailing-slash equivalence. A mounted root is the stated composition exception described in §3.3.
- Query parameters do **not** influence routing. They are parsed independently, preserving repeated names and order. Invalid percent-encoding or UTF-8 in query data yields a protocol-level 400 before normal dispatch; the parser does not silently repair it.
- Invalid `Host`/`:authority`, conflicting authorities, malformed headers, and forbidden HTTP/2/3 connection-specific fields are rejected at the transport boundary.

### 4.3 Deterministic path precedence

Routing matches **path specificity first**, then method. At each segment position: literal > named capture > catch-all. Lexicographic specificity across the complete path wins, with longer fully matched literal patterns ahead of catch-all when otherwise equal. Route registration order never changes which path wins. The matcher MUST reject templates that could tie on the same method/path with identical specificity (e.g. `/users/{id}` vs `/users/{name}`); duplicate effective registrations after mounting are `ConfigError`.

Given GET `/users/new` and POST `/users/{id}`, `POST /users/new` selects the more-specific static path, then returns 405 if POST isn't enabled there: it does **not** fall through to the dynamic POST route. This prevents route-security bypass based on method-dependent fallback. The same policy governs automatic OPTIONS, HEAD and group selection.

### 4.4 Method table

| Method | Request type | Safe | Idempotent | Framework body helpers | Registration |
|---|---|---:|---:|---|---|
| GET | `GetRequest` | Yes | Yes | None | `get` |
| HEAD | `HeadRequest` | Yes | Yes | None | `head`, or automatic GET fallback |
| QUERY | `QueryRequest` | Yes | Yes | JSON/text/bytes/form/multipart | `query` |
| POST | `PostRequest` | No guarantee | No guarantee | JSON/text/bytes/form/multipart | `post` |
| PUT | `PutRequest` | No | Yes | JSON/text/bytes/form/multipart | `put` |
| PATCH | `PatchRequest` | No | No guarantee | JSON/text/bytes/form/multipart | `patch` |
| DELETE | `DeleteRequest` | No | Yes | No general-purpose body helpers | `delete` |
| OPTIONS | `OptionsRequest` | Yes | Yes | None | `options`, or automatic |

The HTTP parser MUST still consume, validate and bound a framed body on *any* received method, including GET/HEAD/DELETE/OPTIONS. Not exposing a convenient body API does not entitle the transport to ignore framing or leave bytes contaminating the next request.

**QUERY is a first-class RFC 10008 method**: user handlers MUST respect safe/idempotent semantics; Content-Type is required when QUERY content is supplied and the server rejects missing or inconsistent content type. `Accept-Query` may advertise supported query-content media types on applicable responses; there is no automatic assertion that all QUERY handlers accept JSON. QUERY routing and request bodies use the same transport mechanics as POST but never imply side effects. No speculative retry by the server.

Unrecognized method tokens yield 501 (Not Implemented) if syntactically valid and not supported. TRACE is disabled and rejected (405 with appropriate `Allow` on an existing resource). Generic CONNECT is not a route method and returns 501/405 as appropriate; WebSocket extended CONNECT is processed only by registered WebSocket routes. Method names are case-sensitive.

### 4.5 Automatic HEAD

A GET registration implicitly enables HEAD on the same effective path unless an explicit `head` registration exists. A HEAD request enters app/group middleware as the actual `HeadRequest`, preserving actual wire method metadata; after middleware has completed, an explicit HEAD handler receives `HeadRequest`, or a GET fallback receives a **framework-created GET-compatible view** (`GetRequest`) with the same validated target, headers, context and route captures and with `originalMethod=HEAD`. The adapter is not an operation application middleware can perform. GET fallback handler executes once and is permitted to compute a representation; developers can register an explicit HEAD handler when this is expensive.

The resulting response MUST send **no content bytes**, with permissible representation metadata (including Content-Length if known as the GET representation's length) and without inventing a body. Streams that cannot determine a safe length must not guess one. Error statuses and 304/204 rules still apply. Middleware after-callbacks see the logical response before HEAD suppression. There is no erroneous second GET request or extra state mutation.

### 4.6 Automatic OPTIONS and 404/405

If a path resolves and no explicit OPTIONS route exists, OPTIONS returns 204 with `Allow`, listing all supported methods for that path, including automatic HEAD and OPTIONS. An explicit OPTIONS route wins. Explicit handlers must emit an accurate `Allow` if returning 405. Automatically generated responses carry `Allow` and are passed through the ordinary error/response and middleware pipeline.

- No matching path: 404 Not Found.
- Matching path but unsupported method: 405 Method Not Allowed, with accurate `Allow`.
- Unsupported method token: 501 Not Implemented when applicable.
- Invalid target or malformed framing: reject at the transport boundary (often 400) without invoking arbitrary application code.
- A matching path on an explicit WebSocket route is **not** counted as an ordinary GET/POST route; a non-upgrade GET does not start the WebSocket handler and gets a deliberate 426/400 handshake response, rather than an accidental success.

App-wide middleware runs for logical framework-generated 404/405 and automatic OPTIONS; scoped middleware runs only when that scope is selected by the most-specific route or applicable automatic-method path. No group is guessed for an entirely unmatched path.

### 4.7 Reverse routing / URL construction

`web.routeUrl(appOrRouter, method, template, params: Dict[String,String], query: Params = empty) -> String | ConfigError` generates a **relative** URL using the exact effective route template. Parameter names must match exactly, missing or unexpected keys error, and each substituted path value is UTF-8-percent-encoded as **one segment** (unless it is the explicitly final catch-all, which encodes each subsegment). No parameter can inject `?`, `/`, `#`, or a traversal segment. Duplicate method+template identities after mount are configuration errors; this utility does not select routes by handler identity, avoiding callable reflection and captures. Query rendering preserves insertion order.

## 5. Requests: exact representation and utilities

### 5.1 Nominal request types

`web.Request` is a **closed variant** representing the eight concrete method-specific request types. Each concrete request is a nominal immutable value with common readable fields:

| Field | Type / meaning |
|---|---|
| `method` | `web.Method`: effective logical handler method; GET fallback is `GET` in its adapted handler view |
| `originalMethod` | `web.Method`: actual incoming wire method (HEAD in GET fallback) |
| `path` | `String`: validated decoded route path |
| `rawTarget` | `String`: exact validated request target for diagnostics only |
| `routeParams` | Read-only `Dict[String,String]`: validated matched path captures |
| `headers` | `web.Headers`: case-insensitive multimap |
| `query` | `web.Params`: decoded ordered query pairs |
| `scheme` | `String`: trusted effective `http`/`https` |
| `authority` | `String`: validated effective host/port |
| `peer` | `web.Peer`: actual socket peer, never synthesized from untrusted forwarding headers |
| `clientAddress` | `String?`: verified effective client IP, if known |
| `protocol` | `web.Protocol`: transport version |
| `requestId` | `String`: generated safe identifier; never from untrusted input without validation |
| `context` | Internal typed context store, read via keys only |
| `body` | Internal `Buffered | Streaming | Consumed` state, available through method-allowed operations only |

Public request fields are readable, not writable; middleware cannot directly alter the method, target, effective authority, route parameters or body ownership. The public `@index` provider for requests reads **only** path captures: `$req["id"]`. It returns a `String` for an existing matched capture; absence returns `web.Error.MissingParam` rather than panicking with an unrelated dictionary `KeyFault`. A programmer-supplied unknown capture key is a normal request-level error.

### 5.2 `web.Params`: one ordered multimap

`web.Params` is an immutable ordered sequence of key/value string pairs, **not a `Dict`**, so duplicates and order survive. It is used by query strings, URL-encoded forms and the `fields` part of multipart forms. The public operations are:

```
web.queryParams(req: Request) -> Params                     # already parsed
web.paramValues(params: Params, name: String) -> String+    # [] if absent
web.paramOne(params: Params, name: String) -> String? | Error.AmbiguousParam
web.requireParam(params: Params, name: String) -> String | Error.MissingParam | Error.AmbiguousParam
web.withParam(params, name, value) -> Params                # append, do not replace
web.replaceParam(params, name, value) -> Params             # remove all same-name values and add one
```

The optional `paramOne` returns `None` for absence, `String` for one occurrence, and an `Err` for more than one. `requireParam` requires exactly one. Empty strings are values, not missing parameters. `paramValues` returns every occurrence in order. Lookups are case-sensitive. API MUST never silently choose first or last repeated values.

Query decoding follows URI query percent-encoding; literal `+` in a generic query string is **not** globally conflated with space. URL-encoded form decoding follows HTML form rules (`+` → space). Both strictly validate UTF-8 and encoding. Query parameters MAY be present on any method, including POST and QUERY. No helper ever silently merges route, query, cookie and form sources.

### 5.3 Headers and content negotiation

`web.Headers` is a validated, immutable, insertion-ordered collection of `(name, value)` pairs preserving individual field lines while comparing field names case-insensitively using ASCII. Normalize field-name case for comparison, not for exposing original wire spelling. Header operations:

```
web.header(reqOrResponse, name: String) -> String? | Error.AmbiguousHeader
web.headerValues(reqOrResponse, name: String) -> String+
web.contentType(req: Request) -> web.MediaType? | Error.InvalidHeader
web.accepts(req: Request, mediaType: String) -> #boolean Int | Error.InvalidHeader
```

`header` returns an optional single value if the field is singleton or only one raw occurrence, and an error for ambiguous repeated non-combinable fields; callers that need full repeated data use `headerValues`. **Do not combine `Set-Cookie`**. Combination of list-valued header fields follows their individual RFC field grammar; a generic comma-join is forbidden. Validate CR/LF, controls, invalid tokens, HTTP/2/3 pseudo-header placement, duplicated Host/authority, and framing before dispatch. `accepts` correctly parses `Accept` media ranges, wildcard, `q` quality and specificity; absent Accept means acceptable. It does not automatically reject every arbitrary response with 406.

### 5.4 Request-scoped typed context

Middleware can enrich a request without altering protocol identity:

```
web.ContextKey[T] -> ContextKey[T]                          # mints opaque unique key at app construction
web.withContext[T](req: R, key: ContextKey[T], value: T) -> R
web.context[T](req: Request, key: ContextKey[T]) -> T?
web.requireContext[T](req: Request, key: ContextKey[T]) -> T | Error.MissingContext
```

`R` is the **same exact nominal request subtype** as input. Keys have opaque unforgeable identities (not string names), may be copied/captured, and do not expose a global registry. Creating a new key with the same type does not recover an existing value. Context payloads must be ordinary duplicable immutable data; `@nodup` handles cannot be smuggled through an implicitly duplicated request and require explicit owned service/handler composition instead. Context is unavailable outside the request lifetime. The framework treats a middleware-returned different concrete method, changed path/captures/authority, or invalid body owner as a framework contract violation → guarded 500 and log.

Create keys **inside an application-building function** so closures can capture them under Valiance's actual capture rules:

```valiance
public define \buildApp =>
  $userKey = web.ContextKey[User]
  $app = web.App
  $app := middleware: fn (req: web.Request) =>
    const $user = authenticateRequest $req ?
    web.withContext($req, $userKey, $user)
  end
  $app := get("/me"): fn (req: web.GetRequest) =>
    const $user = web.requireContext($req, $userKey) ?
    web.jsonResponse $user
  end
  $app
end
```

### 5.5 Body access policy

Only `PostRequest | PutRequest | PatchRequest | QueryRequest` can call `bodyBytes`, `bodyText`, `bodyJson[T]`, `formParams`, `multipart`, `multipartStream`, or `bodyStream`. These functions are not exposed on GET, HEAD, DELETE or OPTIONS; network adapters nonetheless consume and validate any legally framed content, enforcing limits. The body is either buffered or streaming, selected at route registration using `bodyMode` (default `Buffered`) and the server's applicable limits. A call requesting buffered content on a streaming body returns a typed `BodyError.WrongMode`, never transparently buffers an unbounded stream.

One logical body read/decode per request is owned by the request body state. Multiple *pure* decoders may operate on a bounded immutable buffered copy, but streaming consumption is single-owner, linear and non-replayable. A route can't change body mode after the request has been matched. `Expect: 100-continue` is handled by the transport after admission and validation of headers, without promising application acceptance prematurely.

Body utilities and errors:

```
web.bodyBytes(req: BodyCapableRequest) -> Bytes | Error.BodyTooLarge | Error.BodyUnavailable
web.bodyText(req: BodyCapableRequest) -> String | Error.BadText | Error.UnsupportedMediaType
web.bodyJson[T](req: BodyCapableRequest) -> T | Error.InvalidJson | Error.UnsupportedMediaType | Error.BodyTooLarge
web.formParams(req: BodyCapableRequest) -> Params | Error.InvalidForm | Error.UnsupportedMediaType | Error.BodyTooLarge
```

- JSON accepts `application/json` and structured-suffix `application/*+json`; the payload is UTF-8 JSON decoded by `std.json.decode[T]`. Wrong syntax or target shape → 400; wrong/missing media type → 415. JSON `null` retains `std.json`'s target-type semantics. Unsupported charset declarations or invalid UTF-8 are request errors. JSON decoding never panics for malformed client data.
- `bodyText` decodes strict UTF-8 with a supported UTF-8 charset; unsupported character sets give 415, invalid byte sequence 400. It is not an arbitrary character-set guessing utility.
- URL-encoded forms accept `application/x-www-form-urlencoded`, decode `+` as space, apply finite field count/name/value limits, preserve duplicates and order, and return `Params`; nonmatching content type → 415.
- `bodyBytes` is bounded by the buffered body limit, and exposes exact octets with no content transformation. A request with zero body yields an empty byte list. A body parser may report a malformed framing error before a route is invoked.
- A failed body helper returns an ordinary web `Err`; typical handlers use `?`, not `?!`.

### 5.6 Multipart forms and uploads

```
web.multipart(req) -> MultipartForm | Error.InvalidMultipart | Error.BodyTooLarge
form.fields: Params
web.uploads(form, name: String) -> Upload+
web.uploadOne(form, name: String) -> Upload? | Error.AmbiguousParam
upload.filename: String?            # untrusted submitted label
upload.contentType: String?         # untrusted declared media type
upload.size: Int                    # measured octet count, not client-provided claim
web.readUpload(upload) -> Bytes | Error.BodyTooLarge
web.saveUpload(upload, destination: path.Path) -> None | web.Error
```

A buffered `MultipartForm` is bounded by total-byte, field, file, part, and nesting quotas; uploads may spool to securely created temporary files above an in-memory threshold. `Upload` is an opaque owned/borrowed resource whose lifetime cannot outlive request cleanup unless its content is deliberately saved or its owning resource transferred. `filename` is never an output path. `saveUpload` requires an application-chosen `std.path.Path`, creates/writes safely with explicit overwrite policy (default fail if exists), and MUST NOT trust client path segments or symlinks. A multipart file is not automatically parsed as JSON or text. Multiple files of the same name preserve order.

A route registered with `bodyMode=BodyMode.Stream` also supports `web.bodyStream(req) -> RequestBodyStream | Error.BodyUnavailable | Error.WrongMode`, a linear `@nodup` source with `read(maxBytes) -> Bytes | None | Err`, `discard -> None | Err`, and deterministic `close`. A stream can be claimed **once**, either via `bodyStream` or `multipartStream`. Raw body streaming preserves bytes and never silently parses JSON. Streaming multipart (`web.multipartStream`) returns an owned `@nodup` `UploadStream` iterator yielding ordered field and upload-part events with bounded buffers and flow control. A part's bytes must be consumed or explicitly discarded before advancing; limits, cancellation, and cleanup apply throughout. Streaming sources are not clonable, indexable or replayable. A handler MUST NOT use buffered and streaming parsing on the same body owner.

### 5.7 Cookies

```
web.cookies(req: Request) -> web.CookiePairs | Error.InvalidCookie
web.cookie(req: Request, name: String) -> String? | Error.InvalidCookie | Error.AmbiguousCookie
```

Cookies are parsed conservatively according to interoperable request-cookie rules; multiple Cookie header lines are accepted. `cookie` does not silently select between repeated same-name cookies. The request contains only a cookie name/value; a request cannot reveal whether the browser originally received HttpOnly/Secure/SameSite attributes. Cookie values are untrusted and are never assumed to authenticate a user. Avoid decoding percent escapes in cookies without a policy chosen by the application. Provide a distinct `CookiePairs` ordered multimap, since cookie wire syntax differs from query/form encoding.

### 5.8 Synthetic request metadata and ownership

Request fields are immutable snapshots, not mutable socket handles. The actual body, if streaming, is an owned request-scoped resource. Reading a stream and emitting a response occurs in the request task's structured lifetime; any child tasks or temp files are joined/released on success, `Err`, panic or cancellation. A body may be omitted by the handler; the transport must still drain or close as required to maintain connection correctness and backpressure.

## 6. Middleware: complete execution contract

### 6.1 Return values, exact branch meanings

Middleware is a normal function object accepting one `Request` (the common request variant or a compatible generic specialization) and returning exactly one of:

```
Request
Response
E where E implements Err
record{request => Request, after => Function[Response -> Response | E]}
```

Any union of these branches follows normal Valiance union/implicit-Result rules. `record{request, after}` is a **structural record** with exactly these meaningful fields; `request` must be a valid same-method request and `after` must be a callable taking one response and producing `Response | Err`. No mandatory wrapper for the first three branches and no `next()` function. Do not treat arbitrary record shapes as middleware results.

```
middleware(target: App|Router, callback: Middleware) -> same target kind
```

Canonical forms:

```valiance
$log = fn (req: web.Request) =>
  println "Incoming request"
  $req
end

$auth = fn (req: web.Request) =>
  if (isAuthenticated $req) => $req
  else => web.Error.Unauthorized("Login required")
  end
end

$security = fn (req: web.Request) =>
  record{
    request => $req,
    after => fn (res: web.Response) =>
      web.withHeader($res, "X-Content-Type-Options", "nosniff")
    end
  }
end

$app := middleware $log
$app := middleware $security
$app := middleware $auth
```

### 6.2 Scheduling and unwind

For selected middleware A→B→C and handler H, the request phase is A, B, C, H; the response phase is C.after, B.after, A.after (skipping callbacks never registered). Every middleware and each after-callback runs **at most once**; there is no retry or redispatch. A callback is registered only after its middleware successfully returns the complete `{request, after}` record. Only already-registered after-callbacks can run on short circuits/failures. The ordering is deterministic and unaffected by route registration order.

- `Request`: continue to next middleware/handler with same method and routing identity; validated context enrichment survives.
- `record{request,after}`: validate request, register after-callback, continue with its request.
- `Response`: stop the request phase, normalise/validate response, unwind previously registered after-callbacks.
- `Err`: stop request phase, map through error policy to Response, then unwind previously registered after-callbacks.
- Catchable `Fault` in any before middleware or handler: the **corresponding protected invocation** logs, constructs safe 500 through the common failure mapper, then unwinds previously registered callbacks. This uses source-language `try/handle` in a dedicated execution function, so implicit lexical return cannot skip the outer unwind orchestrator.
- Exception during an `after`: convert to error response (default safe 500 for unexpected failure), then continue remaining **outer** after-callbacks on that replacement response. Do not rerun the failed after-callback or inner callbacks. If the custom error callback itself fails, use the minimal fallback 500.
- Cancellation/disconnect: do **not** fabricate 500 or promise to execute response after-callbacks. Structured cancellation/RAII cleanup runs; any response already committed cannot be replaced.

### 6.3 Boundaries and invariants

The dispatcher MUST establish a separate catchable-fault boundary for (a) each middleware before callback, (b) the route handler or automatic method responder, (c) each after-callback, and (d) the configured onError callback. Each boundary returns an ordinary `Response` or an internal typed outcome to its caller; `handle`'s early return is **not** allowed to escape past the dispatcher and omit unwind logic. Nonrecoverable faults remain nonrecoverable, with sibling request isolation at the runtime/task supervisor as permitted by Valiance semantics.

`Request` identity invariant: middleware cannot change original/effective method (except internal HEAD adaptation after middleware), validated path and capture mapping, authority, transport peer, request identifier, or body mode. Only typed context and defined safe request metadata transformations are allowed. Enforce this both statically where possible and at the request boundary when a common variant obscures a concrete subtype. No implicit reroute, rewrite, or proxy hop.

Response after-callbacks execute **before** HTTP headers and streaming bytes are committed. They see a regular immutable `Response` even when the original handler returned a `String` or `html.Node`. A stream-backed `Response` may be transformed without copying its owned stream. An after-callback returning an unexpected non-Response success is rejected by its callable signature, not silently encoded.

### 6.4 Group scopes and framework responses

Middleware order for a mounted nested route is: app-wide registration sequence, outer group middleware, inner group middleware, router-local middleware, handler. After-callbacks run the reverse registered stack. App-wide middleware applies to logical 404/405 and automatic OPTIONS; selected group middleware applies only when its route or method-generated response is selected. Protocol parser rejects malformed wire requests **before** an app Request exists, and therefore cannot run arbitrary middleware.

### 6.5 Middleware factories and policy helpers

Normal closures provide configured middleware: `web.cors(policy)` and `web.compression(policy)` return ordinary middleware function objects, rather than being special router features. Factories receive immutable policy values; mutable shared state such as a rate limiter is supplied explicitly using captured service handles or `std.concurrency.State`. No global framework service registry.

## 7. Errors, fault isolation and error response policy

### 7.1 Public error families

`web.Error` is a nominal, closed variant implementing Valiance `Err` on each case. The cases and default status mapping are:

| Case | HTTP status |
|---|---:|
| `BadRequest`, `MissingParam`, `AmbiguousParam`, `InvalidHeader` (incoming request), `AmbiguousHeader` (incoming request), `InvalidCookie` (incoming request), `AmbiguousCookie`, `InvalidForm`, `InvalidMultipart`, `InvalidJson`, `BadText`, `MalformedTarget` | 400 |
| `Unauthorized` | 401 |
| `Forbidden` | 403 |
| `NotFound` | 404 |
| `MethodNotAllowed` | 405, with Allow |
| `NotAcceptable` | 406 |
| `RequestTimeout` | 408 |
| `Conflict` | 409 |
| `Gone` | 410 |
| `PreconditionFailed` | 412 |
| `BodyTooLarge` | 413 |
| `TargetTooLong` | 414 |
| `UnsupportedMediaType` | 415 |
| `RangeNotSatisfiable` | 416, with Content-Range when known |
| `UnprocessableContent` | 422 |
| `TooEarly` | 425 |
| `PreconditionRequired` | 428 |
| `TooManyRequests` | 429 |
| `HeadersTooLarge` | 431 |
| `MissingContext`, `BodyUnavailable`, `InvalidResponse`, `InvalidLocation`, `InvalidETag`, `InvalidCachePolicy`, `Internal` | 500 (programming or server errors) |
| `InvalidHeader`, `AmbiguousHeader`, `InvalidCookie` when originating in application response construction | 500 (not client error) |
| `NotImplemented` | 501 |
| `BadGateway` | 502 |
| `ServiceUnavailable` | 503 |
| `GatewayTimeout` | 504 |

Every `Error` case has `message: String` satisfying `Err`. Malformed response construction is always a server-side 500 irrespective of which header/cookie input validation helper detected it; the framework must preserve the error stage when mapping. Relevant cases may have structured, readable fields: `MethodNotAllowed.allowed: Method+`, `TooManyRequests.retryAfter: Int?`, `ServiceUnavailable.retryAfter: Int?`, `RangeNotSatisfiable.size: Int?`. Constructors validate those fields. A generic returned `Err` **outside** `web.Error` maps to 500 by default. No reflection-based guessing of HTTP status from an arbitrary `Err` type name.

`ConfigError`, `ServerError`, `DispatchError` are **non-HTTP error families**; they are returned when there is no usable application request (configuration, startup, lifecycle, invalid synthetic request) and are never automatically converted to 500 merely to satisfy a call signature. Internal parsing/IO `BodyError` is translated into a specific `web.Error` when part of an application request; low-level transport failure after commit ends the stream and logs.

### 7.2 Error messages and safe output

`web.Error` 4xx values explicitly constructed by application code may expose their `message` in the default plain-text response (programmers are responsible for choosing safe text). Framework-generated 4xx text uses stable generic messages and MUST NOT echo attacker-controlled raw bytes, cookie values, credentials, filesystem paths or diagnostic stack traces. All 5xx responses use generic public text, regardless of private message. Logged diagnostics include the original error/fault and request ID; credentials, Authorization/Cookie fields, URL secrets and payloads are redacted by default. Every 401 response requiring an authentication challenge MUST have a valid application-configured `WWW-Authenticate` field; a `web.Error.Unauthorized` without one receives the framework's explicit default `WWW-Authenticate: Bearer` challenge when applicable rather than emitting a semantically incomplete 401.

### 7.3 Single overridable error hook

An app may configure one error hook:

```
web.withErrorHandler(app, fn(context: web.ErrorContext) -> Response | Err) -> App
context.request: Request?              # None if no logical request exists
context.failure: web.Failure
context.stage: web.ErrorStage
context.requestId: String?
```

`web.defaultErrorResponse(context: web.ErrorContext) -> Response` is a pure public helper implementing the safe default status/body/header mapping, designed for custom error handlers that want to preserve HTTP correctness while adding decoration. It does **not** invoke the installed error hook or dispatch recursively. `Failure` has typed cases `Http(error: web.Error)`, `Application(err: Err)`, and `Panic(fault: Fault)`; stage cases include `Routing`, `BeforeMiddleware`, `Handler`, `AfterMiddleware`, `ResponseNormalization`. No untyped `Any` or magical exception dictionary. The hook runs for returned errors, framework-generated logical 404/405 and catchable faults, **not** for explicit successful `Response(status=404)` returned deliberately by a handler, and **not** for malformed transport input before a validated Request exists. It must return a valid `Response`; a returned `Err`, panic, invalid response, or cancellation from the hook triggers a minimal safe 500 fallback (cancellation is propagated rather than misreported as a successful HTTP response). No recursive `onError` invocation on an error in `onError`.

An application override may choose custom status and public text for known errors, but does not get a license to suppress an unrecoverable fault or send invalid wire framing. Error response mapping occurs before after-unwind, including mapping failures of after-callbacks. Already-completed inner after-callbacks are not replayed if a later outer callback fails.

### 7.4 Language-level `try/handle` use

The framework uses ordinary Valiance source semantics in **a separate lexical function**:

```valiance
# Explanatory implementation sketch, not an extra web syntax feature.
define executeProtected(req: web.Request, callback) =>
  try =>
    const $value = $callback($req)
    normaliseOrMap $value
  handle as fault =>
    logFault $req $fault
    web.Response(status = web.Status.InternalServerError,
                 body = "Internal Server Error")
  end
end
```

The successful path returns the normalised response; the handler's ordinary result immediately returns from `executeProtected`. That *does not* bypass middleware unwind because the enclosing dispatcher is a different function. Do not introduce `PanicError`, automatic exception wrappers or a new `Result` class. `?!` is not a substitute for `?` when handling expected invalid input.

### 7.5 Cancellation, cleanup and commit barrier

A request is either **precommit** (the entire response can still be replaced) or **committed** (headers or streaming bytes have been sent). Before commitment, a catchable fault can become 500 and remaining outer after-callbacks may run. After commitment, status and headers cannot be rewritten; stream failure results in protocol-appropriate reset/termination and logging. Structured cancellation never becomes 500 by default, and owned handles/temp files/child tasks are released via RAII and scheduler cancellation paths. Nonrecoverable `DuplicationFault` and other runtime-integrity failures MUST NOT be swallowed by `handle =>` or mapped as though routine client errors; the server supervisor isolates/terminates according to the runtime's integrity policy.

## 8. Responses: types, construction and wire rules

### 8.1 `web.Status`

`Status` is a closed nominal variant with named standard codes and `Custom(code: Int)` restricted to 100–599. Standard cases (and numeric values) are:

| Class | Named cases |
|---|---|
| 1xx | `Continue` 100, `SwitchingProtocols` 101, `Processing` 102, `EarlyHints` 103 |
| 2xx | `OK` 200, `Created` 201, `Accepted` 202, `NonAuthoritativeInformation` 203, `NoContent` 204, `ResetContent` 205, `PartialContent` 206, `MultiStatus` 207, `AlreadyReported` 208, `IMUsed` 226 |
| 3xx | `MultipleChoices` 300, `MovedPermanently` 301, `Found` 302, `SeeOther` 303, `NotModified` 304, `UseProxy` 305 (legacy, not automatically emitted), `TemporaryRedirect` 307, `PermanentRedirect` 308 |
| 4xx | `BadRequest` 400, `Unauthorized` 401, `PaymentRequired` 402, `Forbidden` 403, `NotFound` 404, `MethodNotAllowed` 405, `NotAcceptable` 406, `ProxyAuthenticationRequired` 407, `RequestTimeout` 408, `Conflict` 409, `Gone` 410, `LengthRequired` 411, `PreconditionFailed` 412, `ContentTooLarge` 413, `URITooLong` 414, `UnsupportedMediaType` 415, `RangeNotSatisfiable` 416, `ExpectationFailed` 417, `ImATeapot` 418, `MisdirectedRequest` 421, `UnprocessableContent` 422, `Locked` 423, `FailedDependency` 424, `TooEarly` 425, `UpgradeRequired` 426, `PreconditionRequired` 428, `TooManyRequests` 429, `RequestHeaderFieldsTooLarge` 431, `UnavailableForLegalReasons` 451 |
| 5xx | `InternalServerError` 500, `NotImplemented` 501, `BadGateway` 502, `ServiceUnavailable` 503, `GatewayTimeout` 504, `HTTPVersionNotSupported` 505, `VariantAlsoNegotiates` 506, `InsufficientStorage` 507, `LoopDetected` 508, `NotExtended` 510, `NetworkAuthenticationRequired` 511 |

Names are case-sensitive. The constructor `Status.Custom(306)` is rejected as an unassigned reserved code; other valid extension codes are accepted, but still obey class-specific content restrictions. A primary handler cannot return an arbitrary 1xx informational response as its final response; `100 Continue` is handled by transport, `103 Early Hints` by response metadata, and `101` only by the WebSocket handshake adapter. No version-specific status enum.

### 8.2 Single nominal `web.Response`

Canonical public construction (the body is an optional **value**, not an explicit body enum):

```valiance
web.Response(body = "Hello")
web.Response(status = web.Status.Accepted, body = "Processing")
web.Response(status = web.Status.NoContent)
web.Response(status = web.Status.OK, body = html.h1 "Hello")
```

The constructor accepts `status = Status.OK`, `body = None`, and `headers = Headers.empty` as defaults through ordinary Valiance default arguments. Valid `body` input types are `None`, `String`, `html.Node`, `Bytes`, `FileBody`, `StreamBody` and the internal SSE body adapter. The internals normalise to exactly one `Body.Empty`, `Body.Text`, `Body.Html`, `Body.Bytes`, `Body.File`, `Body.Stream`, `Body.Events`; **`Body` is not required in user source**. Public readable fields are `status: Status`, `headers: Headers`, and `body: Body` (semantic, immutable/owned), with optional `earlyHints` metadata.

`Response` fields cannot be mutated. Helpers return a new value. A `Response` holding a `@nodup` stream or file lease cannot be duplicated despite the object otherwise being immutable; an update transfers the exclusive body owner into the new response. If the current compiler lacks generic transitive ownership for such a value, the implementation MUST add the required ownership tracking at the appropriate runtime/library boundary, not silently copy the resource or invent a second public `StreamResponse` type.

### 8.3 Direct handler normalisation

| Successful handler value | Status | Default media type / semantics |
|---|---|---|
| `String` | 200 | UTF-8, `text/plain; charset=utf-8` |
| `html.Node` | 200 | UTF-8 safe `std.html` rendering, `text/html; charset=utf-8` |
| `Response` | Explicit / default | Respect specified body/status/headers; validate before commit |
| Anything else non-`Err` | **Static registration error** | Do not guess JSON, numbers, booleans or `None` |

`Err` values are not successful bodies. A `String | Response | html.Node | Error` union is an ordinary implicit Valiance `Result`. The framework detects `Err` and maps it; it MUST NOT attempt to serialise it as a response body. `std.html` alone serializes HTML (escaping text/attributes; not claiming a fully conforming document). Strings are plain text, never guessed to be HTML.

### 8.4 Response utility signatures

```
web.jsonResponse[T](value: T, status: Status = Status.OK, headers: Headers = empty) -> Response | ValueError
web.created(location: String, response: Response = Response(status=Status.Created)) -> Response | Error.InvalidLocation
web.redirect(location: String, status: Status = Status.SeeOther, allowExternal: #boolean Int = false) -> Response | Error.InvalidLocation
web.withStatus(response: Response, status: Status) -> Response | Error.InvalidResponse
web.withHeader(response: Response, name: String, value: String) -> Response | Error.InvalidHeader
web.addHeader(response: Response, name: String, value: String) -> Response | Error.InvalidHeader
web.withoutHeader(response: Response, name: String) -> Response
web.withCookie(response: Response, name: String, value: String, ...) -> Response | Error.InvalidCookie
web.deleteCookie(response: Response, name: String, ...) -> Response | Error.InvalidCookie
```

`jsonResponse` calls `std.json.encode`, writes UTF-8 JSON bytes with `Content-Type: application/json`, and returns its encoding error unchanged as an `Err` (default 500 at the request boundary). It never guesses JSON from a handler return. `created` sets status 201 and `Location`; it does **not** automatically encode a managed object. With a supplied response, it preserves its body/other valid headers but overrides status to 201 and replaces Location. Without one, it constructs a bodyless 201. If the location is invalid, it returns `Err`.

`redirect` defaults to **303 See Other** with empty body and validated Location. Also support explicit 301, 302, 307, 308 only; 307/308 preserve method. Default destination is a same-origin path/reference (reject scheme-relative `//`, CR/LF, userinfo, unsafe schemes and invalid Unicode/URI syntax); external absolute HTTP(S) redirects require `allowExternal=true`. Never redirect automatically to attacker-controlled `returnTo` query values. For relative redirect destinations, preserve query encoding and reject path traversal ambiguity.

`withHeader` **replaces all fields with that name**; `addHeader` appends a separate field, but disallows illegal duplicates for singleton/framing fields. `Set-Cookie` MUST be emitted as independent fields; use cookie helpers. Hop-by-hop connection fields and transport-owned framing fields (`Content-Length`, Transfer-Encoding, Connection, Upgrade for ordinary responses and HTTP/2/3-specific pseudoheaders) are generated/validated at commitment rather than blindly trusted from a user header list. A user can provide a Content-Length only when it exactly matches the representation; otherwise reject before commit. Invalid names, CR/LF and controls always error.

### 8.5 Cookies on responses

`withCookie` accepts optional named parameters `path="/"`, `domain=None`, `httpOnly=true`, `secure=true`, `sameSite=SameSite.Lax`, `maxAge=None`, `expires=None`, `partitioned=false`. No expiry means session cookie. Use an explicit domain only after validating DNS scope; by default the cookie is host-only. Reject invalid name/value characters; **do not percent-encode silently**. Enforce `__Host-`, `__Secure-`, applicable partitioned-cookie requirements, and `SameSite=None` requiring `Secure`. `deleteCookie` sets a non-positive expiry/Max-Age with matching path/domain/partition scope, without guessing a cookie's historical scope.

Secure defaults are intentional. Plain localhost HTTP development that needs client-side cookie storage can deliberately set `secure=false`; production HTTPS should retain it. These defaults are not authentication or CSRF protection.

### 8.6 Status/body correctness

Before committing headers the framework MUST validate:

- 204 and 304 and informational responses have no body and no forbidden framing; 205 is a zero-length response. 101 belongs only to a negotiated protocol switch; 1xx other than protocol-managed informational responses cannot be final.
- HEAD never transmits body bytes (metadata may describe the corresponding GET representation).
- Content-Length matches the final outgoing octet count when known; unknown streamed length uses protocol-appropriate framing (HTTP/1.1 chunking or connection close where necessary; HTTP/2/3 data stream semantics). No application-visible HTTP chunk syntax.
- Date, Location, ETag, Last-Modified, Vary, Content-Range, Content-Disposition, Accept-Ranges, Retry-After and Cache-Control follow their relevant field grammar and response status restrictions.
- A 304 retains only suitable response metadata; a 416 includes the unsatisfied range size when known; a 405 includes Allow; a 401 includes an applicable challenge. Invalid response declarations from application code are guarded framework errors before commitment, never wire-level response-smuggling opportunities.

### 8.7 Informational response metadata

`web.withEarlyHints(response, links: String+) -> Response | Error.InvalidHeader` attaches validated `Link` preload hints. When hints are provided and the response remains precommit, the HTTP/1.1, HTTP/2 and HTTP/3 adapters **MUST emit one 103 Early Hints response** before final headers and still transmit the final response; clients remain free to ignore these hints. `dispatch` preserves hints in Response for tests and does not simulate network flushes. Do not expose 100 Continue as an application Response or send 103 after final headers. Security and cache policy must not depend on hints being observed.

## 9. Files, downloads, caching, compression, and static resources

### 9.1 Fixed files and downloads

```
web.file(path: path.Path | String) -> Response | Error.NotFound | Error.Forbidden | Error.Internal
web.page(path: path.Path | String) -> Response | web.Error
web.download(path: path.Path | String, filename: String) -> Response | web.Error
web.staticRouter(root: path.Path | String, ...) -> Router | ConfigError
```

`file` opens a regular readable file safely and returns a **lazy file-backed Response** with measured size, stable descriptor/lease, MIME type from a conservative extension mapping, default `application/octet-stream`, and `X-Content-Type-Options: nosniff` and `Cache-Control: no-store` by default. The file is sent through the active protocol adapter using bounded chunks, kernel sendfile when available, and correct cleanup. `page` serves the specified **fixed** HTML file with `text/html; charset=utf-8`; it does not interpolate data or compile templates. `download` sets safe `Content-Disposition: attachment` with RFC-compliant filename and UTF-8 `filename*` encoding, strips path components from the suggested filename, and keeps the file bytes unchanged.

`file` and `page` missing path → `web.Error.NotFound`; permission → `Forbidden` only when disclosure of path existence is acceptable, otherwise safe generic mapping; unexpected filesystem IO → 500. A direct file helper is explicit application authority to serve **that exact chosen path**; if the path comes from user input, validation remains the caller's responsibility. `staticRouter` applies much stricter root confinement automatically.

### 9.2 `staticRouter` security and routes

`web.staticRouter(root, indexFiles = ["index.html"], cachePolicy = CacheVisibility.NoStore, allowDotfiles = false, followSymlinks = false)` produces a Router with a terminal catch-all GET route and implicit HEAD. It does not list directories, auto-redirect slash variants, execute scripts, reveal dotfiles or serve files outside `root`. Requested targets are decoded by the common router exactly once, mapped segment-by-segment beneath an application-chosen root, and resolved with race-safe/no-follow OS operations. Reject dot segments, backslash, drives, absolute path injection, device names where relevant, encoded separators, broken links, escapes via symlinks or TOCTOU substitution, and non-regular files. Directory index selection uses the explicit order in `indexFiles`, never guesses a directory listing. Missing/disallowed files produce safe 404 (not a host path disclosure).

The default static cache policy is `no-store`; callers deliberately opt into public long-lived caching when asset names are versioned. `staticRouter` is **ordinary Router composition**: `$app := group("/assets", web.staticRouter("public/assets"))`. A mounted `/assets` static router does not own a listener or separate pipeline. A specific literal route on the App takes precedence over a static catch-all by the router specificity rules. All applicable middleware still runs, and file responses use the same error and HEAD/conditional machinery.

### 9.3 Range and conditional static responses

Static file serving MUST implement GET/HEAD `If-None-Match`, `If-Modified-Since`, `If-Range`, `Range`, satisfiable single and multi-byte ranges, `206 Partial Content`, `416 Range Not Satisfiable`, validators, `Accept-Ranges: bytes`, and correct `multipart/byteranges` formatting. Limits cap the number of ranges and aggregate overhead; overlapping/excessive ranges may be coalesced or rejected according to RFC 9110, without excessive allocations. The file lease is pinned so the transmitted bytes correspond to the metadata used to construct the response. Strong/weak validator comparison follows HTTP rules. Content encoding and byte ranges apply to a **selected representation**, not accidentally to compressed/uncompressed mixes.

### 9.4 Cache helpers

```
web.noStore(response) -> Response
web.cachePublic(response, maxAge: Int, immutable: #boolean Int = false) -> Response | Error.InvalidCachePolicy
web.cachePrivate(response, maxAge: Int) -> Response | Error.InvalidCachePolicy
web.withETag(response, tag: String, weak: #boolean Int = false) -> Response | Error.InvalidETag
web.withLastModified(response, unixSeconds: Int) -> Response | Error.InvalidHeader
web.preconditions(request, validators: web.Validators) -> None | Error.PreconditionFailed
web.conditional(request: GetRequest|HeadRequest, response: Response) -> Response | Error
```

`noStore` is exactly `Cache-Control: no-store`. `cachePublic` emits `public, max-age=N` (and optional `immutable`); `cachePrivate` emits `private, max-age=N`. Nonnegative integer ages are required. Helpers replace rather than append mutually incompatible Cache-Control directives. They do not assume authentication, dynamic JSON or cookies are safe for public caches; application policy makes that choice.

`withETag` formats and validates a quoted ETag; caller supplies an opaque **tag value**, not raw ETag header grammar, and `weak=true` adds `W/`. `withLastModified` formats an HTTP date. `web.conditional` processes safe GET/HEAD validators with RFC precedence and returns a 304 when eligible; otherwise returns original response. `preconditions` is a separate **pre-mutation** helper for state-changing handlers, supporting `If-Match`, `If-Unmodified-Since`, and the appropriate 412 path; never validate write preconditions only after the mutation has occurred. Conditional validation never bypasses authorization. If an application demands preconditions and receives none, it may return `Error.PreconditionRequired` (428).

No implicit cache store lives inside `std.web`. The helpers set representation policies; intermediary and browser caches make their decisions under RFC 9111. StaticRouter handles its own automatic validation without making arbitrary dynamic endpoints cached.

### 9.5 Optional compression middleware

`web.compression(web.CompressionPolicy(...)) -> Middleware` is a normal middleware factory, default disabled until registered. Its `after` callback negotiates `Accept-Encoding` with `q` values and supports `gzip` and `br` when enabled by policy; it preserves `identity` when acceptable. It skips tiny content, already encoded content, `no-transform`, range/multipart responses, streaming SSE and other explicitly excluded types. It sets `Content-Encoding`, merges `Vary: Accept-Encoding`, adjusts ETag strength/variant validators correctly, recomputes/removes Content-Length, and respects resources/CPU limits. It MUST NOT compress text/plain into an unlabelled byte stream, or reuse an uncompressed strong ETag for different compressed bytes. Streaming compression, if enabled, is bounded and backpressured; absent compression capability requested explicitly is a startup `ConfigError`, not a silent downgrade.

### 9.6 CORS policy middleware

`web.cors(web.CorsPolicy(...)) -> Middleware` is **opt-in**. Default is no cross-origin permission headers. Policy fields are `allowedOrigins: String+`, `allowCredentials: Bool=false`, `allowedMethods: Method+`, `allowedHeaders: String+`, `exposedHeaders: String+`, `maxAge: Int?`, `allowPrivateNetwork: Bool=false`. Origin values are parsed and compared as origins (scheme/host/port), not suffix strings or untrusted substring matches. Credentials forbid wildcard `Access-Control-Allow-Origin: *`; when selecting a specific origin, respond with the selected origin and `Vary: Origin`. Preflight checks require a valid Origin and Access-Control-Request-Method, verify method against **both** policy and the selected route's allowed methods, and verify requested headers; successful preflight returns appropriate 204 without executing a business handler, with `Vary` accounting for requested method/headers. Rejected preflights return safe 403 or no CORS grant under configured policy; a response must never claim the server permits a method the router forbids. Normal responses get CORS headers in the registered after-callback, including 4xx/5xx when policy allows. Never add CORS to an HTTP message before validating origin and route policy.

CORS does **not** authenticate a caller or prevent CSRF for credentials-based applications. If a resource requires anti-CSRF tokens or same-origin validation, implement that as explicit application middleware.

## 10. Streaming responses and server-sent events

### 10.1 Body source and ownership

`web.ByteSource` is the minimal owned, pull-based byte producer contract: `read(maxBytes: Int) -> Bytes | None | Err`, `close -> None | Err`, with cancellation/backpressure support. `web.StreamBody` is an opaque `@nodup` owner created with `web.stream(source: ByteSource, contentType: String = "application/octet-stream")`. Sources may wrap `std.file.ReadFile`, an application generator, or a native async producer but MUST be connected to the request's structured task lifetime. The producer cannot escape into detached work or be duplicated into multiple responses. `None` from read means EOF; an empty byte list is not EOF and cannot produce unbounded busy looping. Errors before response commitment are mapped; errors after commit terminate/reset and log.

```valiance
$app := get("/export"): fn (req: web.GetRequest) =>
  const $body = web.stream(makeCsvSource(), contentType = "text/csv")
  web.Response(body = $body)
end
```

`Response(body = StreamBody)` remains the **same public Response type**. No user-managed chunked encoding or HTTP/2/3 frame access. Adapter respects flow-control windows, downstream drain, client cancellation, outgoing memory budget and graceful shutdown. `Content-Length` is optional and only used when accurately known. No buffer-all fallback is permitted for unknown-length streams. Headers and all after-callbacks run before the first streamed octet.

### 10.2 Server-sent events

`web.Event` is an immutable object with `data: String`, `event: String? = None`, `id: String? = None`, `retryMs: Int? = None`. `web.eventStream(source: web.EventSource) -> Response` creates a streaming response with `Content-Type: text/event-stream; charset=utf-8`, `Cache-Control: no-cache` by default, safe UTF-8 event-line serialization, and no compression by default. The encoder splits multiline data into `data:` lines, validates event/ID fields (reject newline injection and NUL in IDs), emits required blank-line terminators, and uses bounded backpressure/heartbeat configuration. `Last-Event-ID` is available through the ordinary request header helper. Event-source lifetime is owned by the request; disconnect cancels the source. SSE is a standard streaming HTTP response, not a separate server loop.

### 10.3 Response test collection

`web.collectBody(response: Response, limit: Int = 16777216) -> Bytes | web.Error` and `web.responseText(response: Response, limit: Int = 16777216) -> String | web.Error` allow tests to inspect text, HTML, bytes, file and bounded stream representations. Collecting a streaming response **consumes** its body owner and is not replayable. For `HEAD`, `dispatch` returns logical response metadata while transmission suppresses the bytes; the test helpers can inspect logical representation if body is still available but must not claim that HEAD would send it. Tests should independently verify transport suppression via integration fixtures. No implicit buffering in production request dispatch.

## 11. WebSocket applications (complete separate upgrade path)

A WebSocket is a separate, long-lived protocol **initiated through** an HTTP handshake. It is supported as a first-class `std.web` route but is **not** an ordinary handler returning `Response`, and it must not acquire special implicit Result rules.

### 11.1 Registration and common types

```
web.websocket(target: App|Router, path: String,
              handler: Function[WebSocket -> None | Err],
              policy: WebSocketPolicy = secure defaults) -> same target kind
```

Canonical shape:

```valiance
$app := websocket("/echo"): fn (socket: web.WebSocket) =>
  # Repeatedly receive messages, then send an appropriate response.
  # The socket is an owned resource; its loop uses ordinary Valiance control flow.
  echoMessages $socket
end
```

`WebSocket` is an `@nodup` owned bidirectional connection, whose `request: WebSocketRequest` is a validated read-only handshake snapshot. `WebSocketRequest` is a ninth internal/public `Request` variant case used **only for upgrade middleware and connection metadata**. It preserves its true `originalMethod` (HTTP/1.1 GET or HTTP/2/3 extended CONNECT), headers, effective origin, route captures, request ID and middleware context; it never pretends that CONNECT is an ordinary GET. The eight ordinary route method types remain unchanged. `WebSocketPolicy` includes allowed origins, allowed subprotocols, required subprotocol preference, maximum frame/message bytes, idle/ping/pong limits and extension policy.

WebSocket and ordinary GET routes MAY coexist on the same exact path because the handshake and ordinary GET are distinct dispatch kinds. An authentic WebSocket upgrade selects the WebSocket route; a normal GET selects the ordinary GET route if any, otherwise returns an appropriate non-upgrade error (426 or 400). A malformed/unauthorised upgrade MUST NOT quietly fall through to a GET handler. WebSocket routes participate in mounted router prefixes and app/group middleware and must not be exposed to ordinary OPTIONS/HEAD fallback as if they were GET resources.

### 11.2 Handshake and middleware

1. Validate HTTP version-specific handshake syntax, WebSocket key/version, method, extension offers, Origin policy, frame settings and supported subprotocols. Reject invalid input before committing an upgrade.
2. Run app/group middleware on `WebSocketRequest` exactly once. A returned `Response` or `Err` rejects upgrade as a normal HTTP response; registered after-callbacks unwind on that rejection. Request context enrichment is preserved.
3. On approval create an internal handshake `Response` (101 for HTTP/1.1, success extended CONNECT for HTTP/2/3); unwind registered response after-callbacks **before** committing the handshake. Reject illegal handshake-header changes and invalid subprotocol/extension negotiation.
4. Commit upgrade and transfer exclusive transport stream/connection ownership to the `WebSocket` handler. From then on, ordinary HTTP `Response` and after-callback machinery is **finished**; WebSocket sends/receives are protocol operations, not response mutations.
5. If the WebSocket handler returns an `Err`, panics catchably, disconnects or is cancelled, log/report and perform the correct WebSocket close/reset. **Never** attempt to replace an established WebSocket stream with HTTP 500. Cancellation propagates to child tasks and closes the socket.

Default Origin policy for browser-like clients is **same-origin**; callers must opt into explicitly allowed origins. Non-browser clients may omit Origin, with acceptance determined by policy rather than assumed trust. Cookie presence does not imply authentication; cross-site WebSocket hijacking must be prevented by explicit policy and application auth middleware.

### 11.3 Messages and lifetime

```
web.WebSocketMessage = Text(value: String) | Binary(value: Bytes) | Closed(code: Int, reason: String)
web.websocketReceive(socket: WebSocket) -> WebSocketMessage | Error
web.websocketSendText(socket: WebSocket, text: String) -> None | Error
web.websocketSendBytes(socket: WebSocket, bytes: Bytes) -> None | Error
web.websocketPing(socket: WebSocket, data: Bytes = []) -> None | Error
web.websocketClose(socket: WebSocket, code: Int = 1000, reason: String = "") -> None | Error
```

Operations are exclusive against the owned connection, and a single receive loop is permitted. Writes are serialised with bounded buffering/backpressure; callers may serialize access through existing Valiance channels rather than duplicating the socket. Ping/Pong, close handshakes, masking requirements, fragmentation, UTF-8 validation, legal close codes, control-frame restrictions, message limits and extensions follow RFC 6455. Permessage-deflate is off by default; when enabled explicitly it must enforce bounded decompression and negotiated parameters.

Support HTTP/1.1 RFC 6455 upgrade, HTTP/2 extended CONNECT per RFC 8441, and HTTP/3 extended CONNECT per RFC 9220; feature negotiation must use the proper transport settings. No HTTP/2/3 connection-wide 101/Upgrade hack. WebSocket sessions count toward connection, concurrent task, memory and shutdown budgets and drain/cancel during server shutdown.

## 12. Server configuration, TLS and lifecycle

### 12.1 Local default

```valiance
$app = web.App
$app := get("/"): fn (req: web.GetRequest) => "Hello!"
run $app
```

Blocking `run` defaults to `127.0.0.1:8080`, cleartext HTTP/1.1, no TLS, finite limits, one explicit server-owning structured scope, graceful shutdown on supported termination signals. It must **not** expose a local development app to all interfaces by default.

### 12.2 `ServerConfig` public value

```
ServerConfig(
  host = "127.0.0.1", port = 8080,
  listeners = [],                       # optional additional Listener values
  tls = None,
  protocols = None,                        # inferred from TLS: H1; or H1+H2
  limits = Limits,
  publicOrigin = None,
  allowedHosts = [],
  trustedProxies = [],
  proxyMode = ProxyMode.Disabled,
  gracefulShutdownSeconds = 30,
  requestLog = LogPolicy.Safe
)
```

These are **ordinary default arguments**; field types are validated constructor inputs. The primary `(host,port)` defines one TCP endpoint. `listeners` adds explicit additional TCP or Unix-socket listeners, each with its own bind address and TLS binding policy (for Unix sockets, no direct TLS/HTTP3). Every configured listener is validated before startup and an app may have more than one. A single HTTPS TCP endpoint with HTTP/3 also binds the corresponding UDP port for QUIC; UDP and TCP bind failures cause atomic startup rollback. `port=0` requests an ephemeral TCP port; HTTP/3 with port zero uses a coordinated chosen UDP port or fails atomically, and the bound endpoints are available via `boundAddresses`.

`protocols` is `None` for automatic defaults or a nonempty ordered set when explicitly supplied; duplicates are rejected. Without TLS only HTTP/1.1 is enabled by default; cleartext HTTP/2 (h2c prior knowledge or Upgrade) is **not a supported listener mode** in this contract, so explicit HTTP2 without TLS is a startup ConfigError rather than a silent downgrade. HTTPS with `tls` and omitted protocols defaults to HTTP/1.1 plus HTTP/2; HTTP/3 must be explicitly listed. A request for any protocol MUST either be fully provided or produce `ServerError` on startup; silent degradation is prohibited.

### 12.3 `TLS` and QUIC

```
TLS(
  certificateFile: path.Path | String,
  privateKeyFile: path.Path | String,
  sniCertificates: Dict[String, CertificatePair] = {},
  clientAuth: ClientAuthPolicy = Disabled,
  minVersion: TLSVersion = TLS13,
  alpn: Auto = derived from configured protocols
)
```

Validate file permissions where possible, certificate/private-key match, trust roots, certificate chain, time validity, SNI names, ALPN and supported versions before accepting clients. TLS 1.3 is the HTTPS default; TLS 1.2 may be explicitly enabled for HTTP/1.1 and HTTP/2 with compliant ciphers; HTTP/3 requires QUIC's TLS 1.3. Reject TLS 1.0/1.1, weak ciphers, invalid cross-protocol negotiation and accidental plaintext fallback.

QUIC/HTTP3 requires UDP, transport parameters, stream/connection flow control, anti-amplification, address validation, connection migration/security policy, idle timeouts, correct QPACK limits and HTTP/3 settings. `Alt-Svc` advertisement occurs only for a reachable configured public HTTP/3 endpoint, honoring `publicOrigin` and trusted proxy deployment; no advertisement for localhost-disabled H3 or failed listeners. **TLS 0-RTT early data is disabled for this API contract**, including QUIC. It is deliberately not exposed as a configurable switch because robust anti-replay policy is not an ordinary per-route concern. A client offering early data cannot have it delivered to application handlers before a normal confirmed handshake.

TLS credential reload is available through `web.reloadTLS(server, tlsConfig)` returning `None | ServerError`: validate replacement completely before atomically applying to new handshakes; existing accepted connections keep their negotiated state. Reload does not restart `App` or re-run route construction. Failure leaves old validated TLS configuration active.

### 12.4 Resource limits and timeouts

`web.Limits` MUST expose at least the following fields and enforce **finite defaults**. These are baseline API defaults, not a claim that they fit every deployment:

| Field | Default | Enforcement |
|---|---:|---|
| `maxRequestTargetBytes` | 8 KiB | Target parser admission |
| `maxHeaderBytes` | 32 KiB | Compressed and decoded header budget as applicable |
| `maxHeaderFields` | 100 | Header admission |
| `maxBufferedBodyBytes` | 16 MiB | Buffered body read/decoding |
| `maxStreamingBodyBytes` | 1 GiB | Total streamed request budget (configurable) |
| `maxFormFields` | 1,000 | URL-encoded and multipart |
| `maxMultipartParts` | 128 | Multipart parser |
| `maxUploadFileBytes` | 256 MiB | Each upload; must also obey total streamed cap |
| `multipartSpoolThresholdBytes` | 1 MiB | Memory before disk spool |
| `maxConnections` | 4,096 | Accepted connections |
| `maxConcurrentRequests` | 1,024 | In-flight HTTP request tasks |
| `maxWebSocketMessagesBytes` | 8 MiB | Per reassembled message |
| `maxActiveWebSockets` | 1,024 | Live WebSocket sessions |
| `headerTimeoutSeconds` | 10 | Slow headers / slowloris |
| `bodyIdleTimeoutSeconds` | 30 | Stalled body progress |
| `connectionIdleTimeoutSeconds` | 60 | Keepalive idle |
| `writeIdleTimeoutSeconds` | 30 | Backpressured output watchdog |
| `gracefulShutdownSeconds` | 30 | Drain before cancellation |
| `maxRanges` | 16 | Range parser |
| `maxDecompressionRatio` | 100 | Maximum uncompressed/compressed size ratio |

Limits also include bounded global buffering, per-connection stream budgets, connection accept queue/backlog, maximum per-client concurrency, decompressed-header limits for HPACK/QPACK, temporary disk budget, maximum multipart field bytes, WebSocket ping/pong timeouts, and logging queue limits. Their **fixed numeric defaults** are specified in §17.3; all are enforced simultaneously, and none silently defaults to unlimited. Invalid negative/zero limits for fields requiring positive values are `ConfigError`. A route may request a **lower** body limit than global limits; raising a route above global limits requires explicit global configuration, not a local bypass.

Overlimit outcomes use proper HTTP semantics: 413 content too large, 414 target too long, 431 headers too large, 408 for timeouts where a valid response is possible, 503/connection refusal under admission pressure, and protocol-specific stream reset/close where a response is no longer safe. Limits are applied during input parsing and during streaming, not only after whole-body buffering.

### 12.5 Reverse proxies, host trust, and client identity

`trustedProxies` is a list of validated CIDR networks (or Unix-socket peers). The **actual TCP/UDP/Unix peer** is always recorded separately. `proxyMode = Disabled` by default ignores Forwarded and X-Forwarded-* for security decisions; with explicit trust enabled, only a direct peer in trustedProxies can supply a forwarding chain. Walk that chain from the trusted peer toward the first untrusted hop, cap count/length, and validate IPv4/IPv6, scheme, port and host. Conflicting Forwarded and X-Forwarded-* representations are rejected or handled by a single explicitly selected precedence policy, never merged opportunistically. Public URL generation uses `publicOrigin` when configured, else verified effective scheme/authority. An optional `allowedHosts` allowlist protects name-based routing; when nonempty, invalid hosts receive 421/400 before handlers. A wildcard bind does not automatically mean forwarded headers are trusted.

Proxy mode does not change the application-visible method or path after route matching. Forwarded request content cannot alter the authenticated user's identity, escape a trusted TLS scheme, or synthesize `secure=true` cookie context without the defined trust policy. The `peer` and effective `clientAddress` are both exposed so audit logs can distinguish observation from claimed information. Reverse proxy deployments may terminate TLS externally and serve the app over loopback/Unix socket; in that case the app's *public* origin and proxy settings are configured explicitly.

### 12.6 `run` versus `serve`

```
web.run(app: App, config: ServerConfig = ServerConfig) -> None | ServerError
web.serve(app: App, config: ServerConfig = ServerConfig) -> ServerHandle | ServerError
web.boundAddresses(server: borrowed ServerHandle) -> web.BoundEndpoint+
web.shutdown(server: owned ServerHandle, graceSeconds: Int? = None) -> ServerStopReport | ServerError
web.wait(server: owned ServerHandle) -> ServerStopReport | ServerError
web.reloadTLS(server: borrowed ServerHandle, tls: TLS) -> None | ServerError
```

`run` is blocking: prepare App, atomically bind all listeners, serve requests within the caller's structured-concurrency lifetime, initiate graceful stop on supported SIGINT/SIGTERM, join all owned tasks, close listeners and return an ordinary server-level `Err` on startup or terminal failure. It does not call a module-global `main` or create a permanently detached daemon task. Signal ownership is held by `run`, **not** by `serve`.

`serve` prepares and binds identically but returns a single `@nodup` `ServerHandle` controlling a running server in the caller's structured scope. `shutdown` **consumes** this handle, stops admission, initiates graceful drain, and waits for termination; `wait` **consumes** the handle and observes termination initiated by another cause/scope. `boundAddresses` and `reloadTLS` are non-consuming borrowed operations; they cannot conjure a duplicate handle or bypass `@nodup`. Repeated shutdown through an alias is impossible by ownership and is an error if attempted across a host boundary. Abandoning the handle triggers RAII shutdown/cancellation and the owning scope joins server tasks; no hidden leak or uncontrolled orphan. A successful completion produces a `ServerStopReport` with reason, accepted/finished/cancelled request counts, graceful duration and transport failures (with no credentials in public text).

A zero-argument `run $app` uses defaults. For production:

```valiance
$config = web.ServerConfig(
  host = "0.0.0.0",
  port = 443,
  tls = web.TLS(
    certificateFile = "certs/fullchain.pem",
    privateKeyFile = "certs/privkey.pem"
  ),
  protocols = [web.Protocol.HTTP1, web.Protocol.HTTP2, web.Protocol.HTTP3],
  publicOrigin = "https://example.com",
  allowedHosts = ["example.com"],
  limits = web.Limits(maxBufferedBodyBytes = 16777216)
)
run($app, config = $config)
```

If the programmer explicitly requests HTTP/3 but the QUIC adapter, UDP bind, certificate, ALPN, or transport policy cannot start, return `ServerError` with all partial listeners/resources rolled back. It is a failed start, not a running downgraded server.

### 12.7 Shutdown state machine

The server transitions `Configured → Starting → Serving → Draining → Closed` or `Failed`. Startup is atomic, and no user request is accepted until **all** requested listeners and app routing tables are prepared. Shutdown performs:

1. Stop admission and close new-accept paths; existing multiplexed transports signal graceful stop (`GOAWAY` for HTTP/2 and HTTP/3, compliant HTTP/1.1 close policy). Do not announce a connection-close header in HTTP/2/3.
2. Let already-admitted requests and opened WebSocket/SSE streams finish within the grace interval, subject to individual quotas. Do not start new request streams after announced shutdown.
3. On expiration, cancel remaining tasks, signal protocol-appropriate stream resets/connection closes, release upload temp storage and owned sockets/files, and join structured child tasks.
4. Report fatal transport errors separately from per-request recoverable faults. `run` returns when termination is complete; `shutdown`/`wait` return their stop reports or `ServerError`.

A second signal may expedite shutdown in a process policy, but does not corrupt RAII cleanup. A client disconnect is request cancellation; it is not reported as HTTP 500 and does not crash other requests. Cancellation of a parent server scope propagates to all children without detached survivors.

### 12.8 Metrics, logging, health and failure reporting

Log **structured events**, not concatenated untrusted header strings. At minimum record request ID, peer/effective-client distinction, method, decoded route template (not necessarily private raw path), status, latency, request/response bytes, protocol, route/middleware error stage and correlated fault details. Redact Authorization, Cookie, Set-Cookie, secrets and body content by default. Use an explicitly configured safe logging callback (`web.withLogger(app, logger) -> App`) or process logging backend; a logger fault must not create an unbounded recursion of error responses. The server exposes **read-only** counters and bound endpoint state through `web.serverStats(borrowed handle)` without background magic endpoints. `/health` is an ordinary user route; the framework does not expose unauthenticated debug/admin URLs.

## 13. The HTTP/1.1, HTTP/2, HTTP/3 adapter contract

### 13.1 Single logical `Request → Response` core

All adapters emit the same **validated logical request envelope** after their version-specific parsing. They call one `prepare/dispatch` request pipeline and translate the completed semantic Response back to version-appropriate framing. The routing, `Err` mapping, middleware and body APIs MUST NOT branch on protocol except for exposed immutable `req.protocol` and controlled transport features. Request tasks are scheduled with fair limits across connections and streams; user handler code is never asked to manage TCP reads, HTTP chunking, HPACK or QPACK.

Recommended separated architecture:

```
std.web public Valiance declarations / native bindings
  App + Router + registration + public requests/responses
  immutable compiled route tree + app middleware plan
  dispatch logical pipeline / error mapper / resource cleanup
  semantic body readers and writers
    HTTP/1.1 state machine (h11 or equivalent)
    HTTP/2 state machine (hyper-h2 or equivalent)
    HTTP/3/QUIC state machine (aioquic or equivalent)
  TLS/listeners + scheduler adapter + WebSocket/SSE bridge
```

This is not a requirement to vendor or reimplement all RFCs. A mature, maintained, interoperable protocol engine is preferred, with a thin scheduler integration that preserves Valiance `@nodup` and structured-cancellation semantics. Python 3.14+ support and transitive dependency security are mandatory compatibility checks. The external engine must not invent a second framework routing or middleware pipeline.

### 13.2 HTTP/1.1

Validate request line, HTTP version, method token, `Host`, field grammar, message framing and connection persistence. Reject request smuggling: conflicting/multiple Content-Length, inconsistent Transfer-Encoding, forbidden whitespace and obs-fold, malformed chunk syntax, untrusted trailers or ambiguous authority, rather than choosing a forgiving interpretation. Support persistent connections, safe sequential requests, bounded pipelining, `Expect: 100-continue`, and request-body draining/connection close where a handler finishes early. Trailers may be exposed via an explicit parsed trailer collection for streaming bodies, but are never mixed with initial headers. Connection-specific fields stay out of HTTP/2/3. Never write contradictory Content-Length and Transfer-Encoding.

### 13.3 HTTP/2

Support TLS ALPN `h2` on HTTPS; validate the connection preface, SETTINGS, pseudo-header rules, lowercase field names, HPACK compressed/decoded budgets, flow-control windows, stream lifecycle and GOAWAY. Reject illegal connection-specific fields, uppercase names, duplicate pseudoheaders and invalid path/authority combinations. Multiplexed requests run in isolated tasks under a connection parent; stream reset cancels only its request. An application `Response` commits headers once, writes DATA with flow control, sends trailers if specifically supported and closes/reset streams correctly. No HTTP/1.1 upgrade/transfer-coding concepts leak into the public API.

### 13.4 HTTP/3

Support QUIC/UDP TLS 1.3, ALPN `h3`, QPACK budgets/blocked stream limits, HTTP/3 SETTINGS, stream flow control and GOAWAY; validate pseudoheaders and forbidden fields as for HTTP/2 with version-specific rules. HTTP/3 traffic is not silently served over TCP. Handle QUIC loss/retransmission/congestion and connection migration through the protocol engine, not user callbacks. Control streams and QPACK state are isolated and cleaned at shutdown. HTTP/3 bootstrapping is advertised with correct `Alt-Svc` only when configured and reachable. Extended CONNECT for WebSocket follows RFC 9220, not HTTP/1.1 Upgrade.

### 13.5 Cross-protocol equivalence and exclusions

The **same** logical Request (method, target, header values, body bytes, peer/proxy policy) MUST select the same route, validate the same body, invoke the same middleware sequence and produce an equivalent status, semantic headers and representation bytes across HTTP/1.1, HTTP/2 and HTTP/3. Differences are limited to standard transport framing, forbidden hop-by-hop headers, stream error codes, and handshake forms. No server push is exposed to application code; HTTP/2 server push is not assumed necessary or advisable for a general web app. QUIC datagrams and WebTransport are separate protocols, not hidden `Response` encodings.

## 14. Socket-free dispatch, testing, and application preparation

### 14.1 Public test interface

```
web.prepare(app: App) -> PreparedApp | ConfigError
web.testRequest(method: Method = Method.GET,
                target: String = "/", headers: Headers = empty,
                body: Bytes = [], scheme: String = "http",
                authority: String = "localhost") -> Request | DispatchError
web.testRequestStream(method, target, body: ByteSource, ...) -> Request | DispatchError
web.dispatch(app: App|PreparedApp, req: Request) -> Response | ConfigError | DispatchError
```

`testRequest` validates the same logical target/header/content invariants as a real request and uses explicit safe synthetic peer/authority metadata. It supports method-specific values, buffered body bytes, and streaming test sources, with no socket. Malformed synthetic input is `DispatchError`, **not** a fake Response from a nonexistent parsed request. It does not validate malformed raw HTTP/2/3 frames; those are adapter tests. `prepare` freezes a deterministic route tree, middleware plan, static roots and named route metadata without starting listeners, and returns an opaque reusable prepared snapshot. `dispatch` on a raw App calls the same preparation internally and returns `ConfigError` if invalid; on a PreparedApp it skips redundant preparation.

A normal valid request passed to `dispatch` always yields a final `Response`, including 404, 405, mapped `Err`, and caught faults. It does not turn an application `Err` into `DispatchError`; the latter is reserved for failed setup, invalid synthetic transport metadata, or an internal dispatch precondition impossible to map to a logical request.

### 14.2 Request pipeline phases (the one normative algorithm)

1. Validate/normalise the incoming logical request envelope (done by transport or test constructor). Any protocol parsing error is handled by adapter, not application middleware.
2. Resolve most-specific effective path and method; record route captures, group scope and automatic-method decision. Generate a logical 404/405/OPTIONS outcome when required.
3. Execute app middleware, then selected outer→inner group/router middleware, collecting `after` callbacks. A `Request` continues; a `Response` or `Err` short-circuits. A catchable fault is converted at its own invocation boundary.
4. If not short-circuited and a route exists, execute the handler in its own lexical `try/handle` function; for HEAD fallback create the controlled GET-compatible handler view; for WebSocket process its special handshake path. Catchable faults are mapped before unwind. `Err` values are recognised by type and mapped; successful values are normalized only among permitted response types.
5. Invoke `onError` for errors/faults as specified; create a valid ordinary `Response` or minimal fallback 500. Apply the reverse `after` stack, with individual fault/Err boundaries and continued outer unwind. Validate final headers, status/body semantics, cookie policy and stream ownership.
6. Return the semantic final Response to `dispatch`, or commit it through the selected transport adapter. On HEAD suppress wire body. For streams, commit once and use flow control; errors after commit terminate stream, not restart the pipeline.
7. Release all request-scoped resources and join/cancel child tasks as appropriate. App and Router remain immutable/reusable.

### 14.3 Test examples

```valiance
import {std.web, std.web.[get]}

$app = web.App
$app := get("/hello"): fn (req: web.GetRequest) => "Hello!"

const $req = web.testRequest(target = "/hello") ?
const $res = web.dispatch($app, $req) ?
assert ($res.status == web.Status.OK)
assert (web.responseText($res) ? == "Hello!")
```

Test a missing route by dispatching `/missing` and asserting 404. Test wrong method on `/hello` with POST and assert 405 and `Allow: GET, HEAD, OPTIONS`. Test malformed JSON through a POST handler using `bodyJson[T]` and assert 400 rather than 500. Test an injected `ValueFault` and assert generic 500 and one log record; no raw fault text in response. Test security-header after callback on 401 short circuit, 404, recovered panic, and failure of a later inner after-callback. Test `std.concurrency.State[T]` by constructing shared state explicitly and invoking concurrent dispatch under the scheduler; no implicit application singleton.

### 14.4 Limits of *what dispatch claims to test* (a precise boundary)

`dispatch` is **fully equivalent at application semantics** but has no TCP/UDP socket, TLS handshake, QUIC connection, HTTP/1 framing, HPACK/QPACK, ALPN, network cancellation or kernel sendfile. It does not claim to test these. The **complete implementation** MUST also pass real transport integration tests covering all such paths; their absence is a failing acceptance gate, not a feature deferred to a future release.

## 15. Acceptance matrix: mandatory observable behaviour

No part of this specification is considered implemented merely because its type signatures compile. Every row below requires executable tests against both the logical dispatcher and applicable live transports. Unless noted, expected error text is stable/sanitized and faults/credentials are not reflected to clients.

| # | Area | Required acceptance checks |
|---:|---|---|
| 1 | Language compatibility | App/Router constructors, `get(...): fn`, default arguments, typed `fn`, record `{request,after}`, `?`, implicit `Result`, `handle as fault` and niladic `module.\routes` are tested against latest Valiance analyser/runtime; no parser fork or new syntax keyword |
| 2 | Implicit results | Handler returns String, html.Node, Response, web.Error and unrelated Err; only non-Err approved body values convert; nested union and `?` preserve Err; `?!` panic gets 500 |
| 3 | Named faults | Original typed and catch-all faults remain inspectable for logs; handler completion exits lexical wrapper; after-callback unwind still happens; unrecoverable integrity faults cannot be swallowed |
| 4 | Routing precedence | Literal beats parameter beats catch-all regardless registration order; path-first method matching; no dynamic fallback on static-method mismatch |
| 5 | Route validity | Same method/path duplicate and same-structure differently named captures fail `prepare`; invalid brace/capture names/empty path/illegal catch-all fail; source spans included in diagnostics |
| 6 | Router composition | Both `group(prefix, router)` and `group(prefix): fn(router)` work; imported niladic router is evaluated normally; repeated mounting is independent and immutable; nested groups produce expected URLs/middleware |
| 7 | Paths | Case-sensitive, strict trailing slash; percent-decoding exactly once; encoded slash/backslash and traversal rejected; malformed UTF-8 and `%` rejected; query never changes route identity |
| 8 | Methods | All 8 method-specific types; QUERY may read body and is safe; GET/HEAD/DELETE/OPTIONS body helpers unavailable; frame parser still consumes content; unsupported methods handled correctly |
| 9 | HEAD | Auto fallback and explicit HEAD override; preserves originalMethod; zero bytes transmitted; correct status, Content-Length when known, no duplicate handler invocation |
| 10 | OPTIONS | Auto 204 + exact Allow; explicit override; preflight CORS route-policy check; no accidental unpermitted method advertisement |
| 11 | Errors | 404 vs 405, Allow, 400/413/414/415/416/429/431/503, generic 500, error stage and request ID, one onError invocation per failure, safe onError-failure fallback |
| 12 | Middleware | Direct Request/Response/Err/record result; registration order; reverse unwind; 401 short circuit still adds prior security headers; later after fault still invokes outer after; zero retries |
| 13 | Method identity | App/group middleware cannot change nominal method or path; typed `PostRequest` handler always receives a PostRequest; only internal HEAD fallback adaptation permitted |
| 14 | Params | Repeated and empty query/form values, percent escapes, plus handling source-specific, strict UTF-8; paramOne absent vs repeated distinction; path/query/form never merge |
| 15 | JSON | Valid nested typed decoding via std.json; invalid JSON and type mismatch 400; wrong content type 415; valid `+json` suffix; encoding failure maps 500; raw object not autoencoded |
| 16 | Cookies | Cookie header multiples and duplicate names, prefix constraints, secure defaults, SameSite, host-only/domain, correct deletion scope, separate Set-Cookie headers, CRLF rejection |
| 17 | Headers | Case insensitive, original order preserved, forbidden framing, obs-fold, duplicate Host/Content-Length/Transfer-Encoding smuggling, HTTP/2/3 connection-field validation |
| 18 | Multipart | Repeated fields/files, filename untrusted, secure temp spool, upload-size/part limits, read/save, path/symlink escapes, file cleanup under return/Err/panic/cancel |
| 19 | Streaming request | Single owner, double-read rejected, backpressure, timeouts, disconnect cancellation, streaming multipart no full-buffer bypass; raw single-owner stream and trailers |
| 20 | Responses | Text/html/bytes/content type; 204/205/304/HEAD restrictions; 201/Location, redirects 303/307/308; explicit status, immutable headers and cookies |
| 21 | Static files | Traversal, Unicode/encoded path, hidden and symlink exclusion, no listing, MIME fallback, GET/HEAD, missing file, conditional 304, If-Range and ranges 206/416 |
| 22 | Caching | ETag strong vs weak, If-Match/If-None-Match precedence, If-Modified-Since, pre-mutation preconditions, Vary, noStore/public/private, no implicit dynamic cache |
| 23 | Compression | gzip/br negotiated, identity fallback, no-transform, `Vary`, ETag variants, Content-Length adjustments, no double compression or SSE compression by default |
| 24 | SSE | multiline data, ID/event injection rejection, live flush/backpressure, Last-Event-ID, disconnect and shutdown cleanup |
| 25 | WebSockets | HTTP/1 upgrade, H2/H3 extended CONNECT, Origin/subprotocol/extension validation, ping/pong, close, fragmentation, invalid UTF-8/mask/length, auth rejection before upgrade, post-upgrade fault close |
| 26 | TLS | Bad key/cert startup fails atomically; SNI, ALPN, TLS version policy, mutual TLS; no silent downgrade; 0-RTT off default |
| 27 | HTTP/1.1 | Fuzz malformed frames, CL/TE ambiguity, slowloris, persistent requests after rejected bodies, chunked, trailers and graceful close |
| 28 | HTTP/2 | Multiplexing, stream isolation/reset, HPACK bomb limits, invalid pseudoheaders/connection fields, flow control, GOAWAY, correct headers/trailers |
| 29 | HTTP/3 | UDP QUIC bind, TLS13/ALPN, QPACK limits, version negotiation, stream cancellation/flow control, correct Alt-Svc, GOAWAY and WebSocket CONNECT |
| 30 | Resource budgets | Byte/field/part/concurrency/time limits enforced for buffered and streamed paths; overload protects memory and scheduler fairness; no unbounded queue or spool |
| 31 | Structured concurrency | Parent shutdown cancels/joins requests, SSE/WebSocket child tasks, upload temp objects; no detached leaks; `@nodup` copying rejected, response transformations preserve exclusive owner |
| 32 | Proxy trust | Untrusted Forwarded ignored, trusted CIDR works with chain rules, spoofed scheme/client IP rejected, Host allowlist, publicOrigin URL generation, no reflected secret |
| 33 | Lifecycle | `run` blocks and exits cleanly on SIGINT/SIGTERM; `serve` returns nonduplicable handle; boundAddresses reports live ports; shutdown drains, cancels on deadline and closes every listener |
| 34 | Dispatch parity | App/error/middleware/route outputs match across dispatch and all three real HTTP transports; synthetic malformed request yields DispatchError, not fake 400 from non-existent Request |
| 35 | Multimodule | Imports and qualified niladic routes from module work; no implicit server startup; app routing independent of module import order; handler captures behave as Valiance normally specifies |
| 36 | Diagnostics | Source-grounded ConfigError, route/conflict locations, structured redacted logs, request ID propagation, stage-specific error mapping, failure injected into logging doesn't recurse infinitely |
| 37 | Property/fuzz | Random path/percent/parameter inputs, header framing, union return branches, multipart boundary splits, H2/H3 invalid frames, WebSocket fragmentation, cancellation races, ownership stress |
| 38 | Performance | No body-buffering of streams; bounded per-request overhead, no unbounded per-connection state; fairness with slow clients; no latency regression due to repeated router rebuild in `PreparedApp` |

### 15.1 Interoperability requirements

The test suite MUST use at least two independent client implementations per protocol where practical, plus RFC/Web Platform Tests vectors for QUIC/HTTP3, live browsers for cookie/CORS/WebSocket behaviour, and a real reverse proxy fixture. Test browser differences involving Set-Cookie, 303/307/308, SSE buffering and cross-origin preflight, not only raw scripted HTTP clients. TLS and proxy tests MUST be performed in environments matching production deployment topology.

### 15.2 Acceptance result classification

- **Fail:** Any missing public symbol or specified behaviour; protocol silently downgraded; user fault disclosure; method type erasure; unbounded memory/stream; insecure static-file path; wrong error mapping; application middleware skipped contrary to unwind rule; nondeterministic routing; leaking detached tasks.
- **Fail:** Tests absent for any required standard adapter or security invariant, even when nominal unit tests pass.
- **Pass:** All public behaviours and error/ownership rules work in normal and optimized VM execution and bytecode serialization, with the conformance matrix passing across all supported transports; retained tests for `std.json`, `std.file`, `std.concurrency.State`, named `try/handle`, and existing language functionality remain passing.

## 16. Implementation architecture and dependency-ordered execution plan

The implementation MUST be a single cohesive architecture delivered in dependency order; **no throwaway prototype, no temporary alternate API, no silent partial transport release presented as complete**. Each stage must preserve stable public shape but can be developed/tested incrementally.

### Stage 0 — language and host compatibility audit

Confirm actual parser/ECS support for `get(path): callback`, `middleware` registration, `group` with Router and function object, return unions, default parameters, qualified niladic router, structural record, `@index`, `@nodup`, context keys, and `handle as fault` in the latest checkout. Establish working Python 3.14+ environment, backend native-binding conventions and scheduler integration hooks. If a proposed public declaration cannot be represented with existing grammar, implement equivalent **ordinary Valiance function overloads or native bindings** without changing syntax; keep the contract and tests invariant. Document the exact source-level declarations in the implementation PR.

### Stage 1 — pure typed core

Build Status/Method/Error variants, Headers/Params/CookiePairs, response semantic body types, request method variants, URL validator, MIME parser, optional typed context keys and deterministic response normaliser. Add value/ownership and property tests before network code. Implement explicit `std.json` integration without modifying its public contract.

### Stage 2 — immutable route composition and `prepare`

Implement `App`, `Router`, route descriptors, group value/function overloads, route conflict diagnostics, deterministic trie/specificity matching, synthetic URL generation, automatic HEAD/OPTIONS/404/405, method-specific handler typing, route limits/body mode and prepared immutable snapshot. Build `web.routes`, `web.prepare`, `web.testRequest`. Test multimodule nilad route construction and app immutability.

### Stage 3 — single application dispatcher

Implement protected handler/middleware invocation using existing `try/handle`, error mapping, `onError`, direct middleware return types, registered after unwind, synthetic context, immutable request validation, and `web.dispatch`. Run success/Err/panic/after/cancellation acceptance tests, including ownership and nested error handler failures. Avoid blanket catch-all around entire dispatch that would bypass after-callbacks.

### Stage 4 — request parsers and response helpers

Implement media type and accept matching; bodyBytes/Text/JSON/form; raw streaming body/trailers; cookies; multipart buffered/streamed; file/page/download/staticRouter; status/redirect/cookie/header construction; ETag/cache/ranges; CORS/compression; bytes/file/SSE streaming. Route-size, temp-file, cleanup and compression budgets are active from this stage. Test with synthetic request and injected reader failures.

### Stage 5 — transport-neutral flow-controlled IO bridge

Define a private request task, body reader, response writer and cancellation bridge so the same dispatcher can be used by all transports. Integrate `@nodup` body ownership, RAII, Valiance scheduler quanta and structured-concurrency supervisors. The bridge must expose backpressure, cancellation and commit state to transports and streaming adapters without exposing frame APIs to handlers.

### Stage 6 — complete HTTP/1.1

Integrate a maintained HTTP/1.1 protocol state machine; validate framing, persistent connection lifecycle, HTTP error responses, chunked streaming, TLS termination and shutdown. Run smuggling, fuzz, slowloris, pipelining, upload, file and browser tests.

### Stage 7 — complete HTTP/2

Integrate a maintained HTTP/2 protocol engine with ALPN, HPACK, multiplexing, stream cancellation, flow control and GOAWAY. Run equivalent application conformance and adversarial tests, including mixed request-body and streaming responses on one connection.

### Stage 8 — complete QUIC/HTTP/3

Integrate a maintained TLS1.3 QUIC/HTTP3 engine, coordinate UDP/TCP binds, implement QPACK/stream budgets and graceful shutdown, Alt-Svc policy, and cross-protocol parity. Run QUIC interoperability suites, cancellation and migration security tests. A failure to start the H3 adapter MUST cause configured H3 server startup to fail, not silently degrade.

### Stage 9 — WebSockets and SSE complete

Build version-specific WebSocket handshakes (H1 upgrade, H2/H3 extended CONNECT) over the same route and middleware policy, then owned message IO, close/ping/pong, origin and subprotocol checks, and post-upgrade fault isolation. Complete SSE event serializer and stream budget/cancellation tests on all HTTP versions.

### Stage 10 — lifecycle, hardening, observability and documentation

Implement `run`, `serve`, `ServerHandle`, shutdown/wait/reloadTLS/stats, limits, trusted proxy and host policy, structured redacted logs, 0-RTT policy and comprehensive docs. Then run every matrix row across normal and optimized VM execution, serialization, malicious clients, live browsers, TLS and all transport versions. Freeze stable API only when complete acceptance gates pass; remove any dev-only implementation scaffolding.

### 16.1 Minimum source boundaries

Target a source layout equivalent in responsibilities to:

```
src/valiance/std/web.vlnc            # public Valiance declarations and ergonomic helpers
src/valiance/std/web.py              # registered native/FFI adapters and binding glue
src/valiance/runtime/web/core.py     # semantic App/Router/Request/Response, independent of IO
src/valiance/runtime/web/router.py   # compilation, path templates and precedence
src/valiance/runtime/web/dispatch.py # one middleware/error/handler pipeline
src/valiance/runtime/web/body.py     # buffered/streamed body source/ownership/limits
src/valiance/runtime/web/http1.py    # HTTP/1.1 adapter
src/valiance/runtime/web/http2.py    # HTTP/2 adapter
src/valiance/runtime/web/http3.py    # QUIC/HTTP/3 adapter
src/valiance/runtime/web/ws.py       # WebSocket handshakes and owned messages
src/valiance/runtime/web/server.py   # TLS/listeners/tasks/shutdown/stats
src/valiance/runtime/web/security.py # HTTP validation, proxy, limits and static root
src/valiance/runtime/web/files.py    # static file/range/conditional handling
src/valiance/runtime/web/tests/...   # protocol and integration fixtures as appropriate
```

This is a separation-of-concerns reference, not permission to violate the existing repository's module layout conventions; existing runtime and stdlib registration helpers take precedence. No new VM opcode or compiler AST construct should be added solely for web routing. Language fixes are permissible only for correctness of already-existing general ownership/concurrency or callable typing, and must be independently tested.

### 16.2 Dependencies, locking and licensing

Supported baseline: Python **3.14+**, matching the repository metadata. Prefer `h11` for HTTP/1.1, `hyper-h2`/`hpack` for HTTP/2 and `aioquic` for QUIC/HTTP3 if supported in the build environment; equivalent maintained conforming engines are acceptable. Pin exact versions and hashes in the repository's supported dependency-lock mechanism **after evaluating the current Python 3.14 compatibility and licenses**, then preserve a reproducible build. No assumptions that a library's marketing claims substitute for the acceptance matrix. Native embedding MUST not cross Valiance's protected-callback restrictions or bypass cancellation/ownership guards. Avoid new language-level dependencies on Python-specific event-loop details.

### 16.3 Implementation invariants checklist

- One prepared App snapshot; one route matcher; one application dispatcher; one error mapper; no duplicate routing logic in individual transports.
- All user callback invocation through guarded typed Valiance callable machinery; no untracked Python callback escape from a protected operation.
- No ambient current-request global; every handler/middleware gets a specific request and explicitly captured services.
- No `next()`, varargs middleware, magic route attributes, implicit JSON, or web-specific `Result` wrapper.
- No hidden stream buffering, immutable-response body duplication, retry of user handlers, or detached server tasks.
- Every explicit TLS/protocol/configuration request is either fulfilled or fails loudly before serving.
- All request- and server-level errors produce deterministic outcomes and useful redacted diagnostics.

## 17. Complete public contract index (signature and policy closure)

This section consolidates every public symbol mentioned above, including advanced types that may be rare in ordinary applications. **No type in this inventory is an unresolved placeholder.** A contract signature is a callable/return constraint; actual Valiance declarations use existing `define`, generics, modifiers and nominal-object syntax.

### 17.1 Public type definitions

| Type | Required public meaning / fields |
|---|---|
| `App` | Immutable collection of route registrations, middleware and error/log policy |
| `Router` | Immutable reusable route collection, independent of server lifecycle |
| `PreparedApp` | Opaque immutable validated compiled route/middleware plan; never starts sockets |
| `Request` | Closed variant of eight method-specific request types **plus `WebSocketRequest`** handshake case; common metadata as §5.1 |
| `Method` | Cases GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS, QUERY, CONNECT, TRACE, OTHER(token); only first eight routable with normal verb registration |
| `Status` | Named standard codes and `Custom(Int)` as §8.1 |
| `Protocol` | HTTP1, HTTP2, HTTP3, and Test for synthetic requests |
| `Headers` | Immutable ordered case-insensitive multimap with individual raw field entries and validator |
| `Params` | Immutable ordered URL/form key-value multimap, preserving repeats |
| `CookiePairs` | Ordered parsed cookie name/value pairs with ambiguous-name detection |
| `MediaType` | Validated type, subtype, parameters, and structured suffix; RFC token syntax |
| `ContextKey[T]` | Copyable opaque token minted once, with unique identity and stored generic type |
| `RequestContext` | Internal immutable typed context map, readable only via matching ContextKey |
| `BodyMode` | Buffered, Stream; selected when registering a body-capable method |
| `Body` | Internal variant Empty, Text, Html, Bytes, File, Stream, Events; users need not instantiate |
| `Response` | One nominal response: readable status, headers, body, optional early-hints |
| `FileBody` | Opaque owned file lease for transfer; no public frame or raw descriptor exposure |
| `StreamBody` | Opaque owned stream wrapper with contentType, length?; not duplicable |
| `ByteSource` | Owned read(maxBytes) → Bytes/None/Err and close → None/Err protocol |
| `MultipartForm` | fields: Params, files: ordered Upload collection |
| `Upload` | Owned bounded file part: name, filename?, contentType?, measured size |
| `UploadStream` | Linear multipart field/file event iterator and single active-part reader |
| `RequestBodyStream` | `@nodup` raw streaming request-body reader with bounded read/discard/close, one owner |
| `Event` | data, event?, id?, retryMs?; safe validated SSE encoding |
| `EventSource` | Owned cancellable event producer/iterator, not a global background task |
| `Validators` | optional ETag (opaque tag + weak flag), lastModifiedUtcSeconds?, representationLength? |
| `CacheVisibility` | Public, Private, NoStore; helpers serialize policy |
| `CorsPolicy` | Explicit origin/method/header/credential/preflight policy as §9.6 |
| `CompressionPolicy` | enabled encodings, minimum bytes, types, CPU/buffer budgets and streaming flag |
| `WebSocketRequest` | Validated handshake request; variant case with true original GET/CONNECT and upgrade metadata |
| `WebSocket` | `@nodup` connected session, buffered backpressure, request snapshot, cancellation owner |
| `WebSocketMessage` | Text(String), Binary(Bytes), Closed(code:Int, reason:String) |
| `WebSocketPolicy` | Origins, subprotocol list, max frame/message bytes, ping/pong, extensions and timeouts |
| `Failure` | Http(Error), Application(Err), Panic(Fault), preserving original typed value |
| `ErrorStage` | Routing, BeforeMiddleware, Handler, AfterMiddleware, ResponseNormalization |
| `ErrorContext` | request?, failure, stage, requestId?; no untrusted global state |
| `Error` | Closed HTTP request-error variant with `Err` implementation, case mapping in §7.1 |
| `ConfigError` | Invalid routes, conflicts, options or unsupported configured capability; never an HTTP reply itself |
| `ServerError` | Startup/transport/lifecycle fatal errors; never an HTTP reply itself |
| `DispatchError` | Invalid synthetic envelope or dispatch precondition; never an application returned error |
| `BodyError` | Internal body state/read/ownership failures converted to web.Error at request boundary |
| `ServerConfig` | Endpoint(s), TLS, protocols, limits, origin/host/proxy trust, logs, shutdown settings |
| `Limits` | Every field listed in §12.4 and §17.3, finite defaults |
| `TLS` | Certificate/private key, SNI map, client-auth, TLS version/ALPN and reload |
| `Listener` | TCP(host,port,tlsPolicy), Unix(path,mode); HTTP3 adds UDP for eligible TCP listeners |
| `BoundEndpoint` | Protocol, socket kind/address, bound port and TLS boolean; read-only |
| `Peer` | Actual socket address / Unix peer information; not derived from forwarded headers |
| `ProxyMode` | Disabled, RFCForwarded, XForwarded; never merge both automatically |
| `ServerHandle` | Single owned `@nodup` running server lifetime, borrowed observations, consuming termination |
| `ServerStopReason` | Requested, Signal, ParentCancelled, FatalTransportError |
| `ServerStopReport` | Stop reason, accepted/completed/cancelled counts, elapsed and redacted error summary |
| `LogPolicy` | Safe (redacted structured default), Disabled, Custom callback |
| `TLSVersion` | TLS12 and TLS13; TLS13 default, HTTP3 always TLS13 |
| `ClientAuthPolicy` | Disabled, Optional(trustRoots), Required(trustRoots) |

### 17.2 Full public function set with overload and defaults

The following spelling is the canonical public element name. For `get`/`post` etc., `target` is the current App/Router stack input and `handler` is a regular higher-order modifier parameter. There is no user-visible route-registration macro.

| Category | Signature contract |
|---|---|
| Registration | `get/head/post/put/patch/delete/query/options(target, path, handler, bodyMode=Buffered, maxBodyBytes=None) -> same target` |
| Registration | `websocket(target, path, handler: Function[WebSocket -> None|Err], policy=WebSocketPolicy) -> same target` |
| Composition | `group(target, prefix, router: Router, middleware=[]) -> same target` |
| Composition | `group(target, prefix, builder: Function[Router -> Router], middleware=[]) -> same target` |
| Middleware | `middleware(target, callback) -> same target` |
| App policy | `withErrorHandler(app, callback: Function[ErrorContext -> Response|Err]) -> App`; `defaultErrorResponse(context) -> Response` |
| App policy | `withLogger(app, callback: Function[LogEvent -> None|Err]) -> App` |
| Preparation | `prepare(app) -> PreparedApp | ConfigError`; `routes(appOrRouter) -> RouteDescriptor+ | ConfigError` |
| URL | `routeUrl(appOrRouter, method, template, params, query=empty) -> String | ConfigError` |
| Request | `queryParams(req) -> Params`; `paramValues/paramOne/requireParam`, `withParam`, `replaceParam` as §5.2 |
| Request | `header`, `headerValues`, `contentType`, `accepts`, `allowedMethods` as §§4–5 |
| Request | `cookies(req)`, `cookie(req,name)`, `bodyBytes`, `bodyText`, `bodyJson[T]`, `bodyStream`, `formParams`, `multipart`, `multipartStream`, `trailers` |
| Request | `ContextKey[T]`, `withContext`, `context`, `requireContext` |
| Upload | `uploads`, `uploadOne`, `readUpload`, `saveUpload(destination,overwrite=false)` |
| Response | `Response(status=Status.OK,body=None,headers=empty)`; `jsonResponse[T](value,status=OK,headers=empty)` |
| Response | `created(location,response=bodyless 201)`, `redirect(location,status=303,allowExternal=false)` |
| Response | `withStatus`, `withHeader`, `addHeader`, `withoutHeader`, `withEarlyHints`, `withTrailers` |
| Response | `withCookie(name,value,path="/",domain=None,httpOnly=true,secure=true,sameSite=Lax,maxAge=None,expires=None,partitioned=false)` |
| Response | `deleteCookie(name,path="/",domain=None,partitioned=false)` |
| File | `file(path)`, `page(path)`, `download(path,filename)`, `staticRouter(root,indexFiles=["index.html"],allowDotfiles=false,followSymlinks=false,cachePolicy=CacheVisibility.NoStore)` |
| Cache | `noStore`, `cachePublic(maxAge,immutable=false)`, `cachePrivate(maxAge)`, `withETag(tag,weak=false)`, `withLastModified(unixSeconds)`, `preconditions`, `conditional` |
| Middleware factory | `cors(policy) -> Middleware`; `compression(policy) -> Middleware` |
| Streams | `stream(source,contentType="application/octet-stream")`, `eventStream(source)`, `collectBody(response,limit=16777216)`, `responseText(response,limit=16777216)` |
| WebSocket | `websocketReceive`, `websocketSendText`, `websocketSendBytes`, `websocketPing`, `websocketClose` as §11.3 |
| Lifecycle | `run`, `serve`, `boundAddresses`, `shutdown`, `wait`, `reloadTLS`, `serverStats` |
| Testing | `testRequest`, `testRequestStream`, `dispatch(appOrPrepared,request)` |

**Special registration rule:** `bodyMode` and `maxBodyBytes` arguments are legal only for POST/PUT/PATCH/QUERY, and are omitted (including their default handling) from GET/HEAD/DELETE/OPTIONS overloads. All overloads of a given verb must respect Valiance's uniform arity/multiplicity rule; use one ordinary generic target overload where possible, rather than incompatible arity combinations. The concrete method-specific request parameter is checked for each verb. A route body-size override may only reduce the global limit unless the global limit itself is explicitly raised. `bodyMode=Stream` means streaming ownership is active before any handler body read. For example: `$app := post("/upload", bodyMode = web.BodyMode.Stream): uploadHandler`.

**Special static router rule:** `staticRouter` catches all path suffixes beneath its mounting prefix but is a regular Router value. It has no automatic writable upload endpoint; serving assets is a read-only operation.

### 17.3 Numeric defaults that are part of the contract

The fields in §12.4 are supplemented with these additional **fixed** defaults to avoid hidden unlimited implementation choices:

| Field | Default |
|---|---:|
| `maxTotalBufferedBytes` | 256 MiB per server |
| `maxBufferedBytesPerConnection` | 32 MiB |
| `maxConcurrentStreamsPerConnection` | 100 |
| `maxPerClientInFlightRequests` | 64 |
| `acceptBacklog` | 512 |
| `maxDecodedHeaderBytes` | 64 KiB |
| `maxCompressedHeaderBytes` | 32 KiB |
| `maxQpackBlockedStreams` | 16 |
| `maxHpackDynamicTableBytes` | 16 KiB |
| `maxQpackDynamicTableBytes` | 16 KiB |
| `maxTotalTemporaryDiskBytes` | 4 GiB |
| `maxMultipartFieldBytes` | 1 MiB |
| `maxMultipartNameBytes` | 1024 |
| `maxMultipartFilenameBytes` | 4096 |
| `maxQueryPairs` | 1000 |
| `maxQueryKeyBytes` | 1024 |
| `maxQueryValueBytes` | 8192 |
| `maxCookiePairs` | 256 |
| `maxCookieBytes` | 8192 total request-cookie bytes |
| `maxWebSocketFrameBytes` | 1 MiB |
| `maxWebSocketQueuedSendBytes` | 4 MiB |
| `webSocketPingIntervalSeconds` | 30 |
| `webSocketPongTimeoutSeconds` | 10 |
| `maxForwardedHops` | 16 |
| `maxForwardedHeaderBytes` | 8192 |
| `maxLogQueueEntries` | 10000 (drop and count rather than block requests) |
| `maxSSEQueuedEvents` | 128 |
| `maxSSEHeartbeatSeconds` | 15 |
| `maxResponseBytesInTestCollection` | 16 MiB |
| `maxCompressionWorkingBytesPerRequest` | 4 MiB |


Totals and per-connection/per-client limits all apply simultaneously; no individual allowance bypasses a server-wide budget. Limits are `Int` byte/count/second values, rejecting out-of-range, nonfinite or negative values. A user may explicitly configure larger budgets, subject to hard process and host resource availability. A quota must not be effectively infinite by using a sentinel `-1`. Slow timers use monotonic elapsed time; UTC/date metadata uses an appropriate wall clock. Policy values are copied immutably into PreparedApp/ServerConfig snapshots, not read from mutable ambient globals.

### 17.4 Complete precommit error priority

For an HTTP request whose headers are syntactically valid: **route preparation/configuration failure** prevents serving (server-level error); **target/header/media/frame validation** precedes app callbacks; **route match** precedes middleware; **before middleware** may short circuit; **handler result** maps `Err` or catchable `Fault`; **onError** may override; **after middleware** transforms/replaces; **final response validation** precedes commit. Errors from outer after-callbacks replace the current response and do not revisit inner callbacks. Once committed, only transport-appropriate stream failure reporting remains. This order is mandatory and no handler or middleware can override an earlier malformed framing rejection.

### 17.5 Streaming trailers and commit ownership

The raw request stream may carry HTTP trailers. `web.trailers(req: BodyCapableRequest) -> Headers | Error.WrongMode | Error.BodyUnavailable` becomes available **only after** complete streaming-body consumption and validates fields under transport-specific rules; before EOF it returns `Error.BodyUnavailable` rather than blocking unexpectedly or exposing unvalidated fields. No trailer is merged into the initial request header collection. For streamed responses, `web.withTrailers(response, trailers: Headers) -> Response | Error.InvalidHeader` declares validated trailing fields before commitment; H1 requires legal chunked trailers and correct `Trailer` announcement, H2/H3 use trailing HEADERS. A bodyless or already-committed response cannot acquire new trailers. Streaming failures do not invent success trailers. Both helpers are public and are included in conformance tests.

The response stream's owner passes from handler to `Response`, then to the adapter exactly once. Response middleware may transform the response metadata without duplicating or consuming the stream. If an after-callback replaces a stream response with an error Response, the abandoned body is closed via RAII before the replacement commits. If a HEAD fallback constructs a stream-backed logical representation, the stream is closed without transmitting its content. These are testable ownership requirements, not implementation options.

### 17.6 External type boundaries resolved

- `std.path.Path` is the filesystem path type; request URI paths are **not** `std.path.Path` and are never accepted as filesystem paths without explicit confined static mapping. Strings are accepted by file helpers for ergonomic construction, but resolved through `std.path` operations.
- Date/last-modified APIs use **UTC Unix epoch seconds as `Int`**. `std.web` does **not** rely on an undocumented `DateTime` core type. HTTP date formatting/parsing is internal; absolute response Expires accepts a Unix seconds value.
- `bool` policy parameters use Valiance's `#boolean Int` where static declarations require it; examples use existing `true`/`false` syntax.
- `None` is absence and is a non-`Err` outcome, but **not** an automatically encodable HTTP handler return. A route that intentionally returns no representation uses an explicit bodyless `Response`.
- `Function[... -> E]` with `E as Err` is an ordinary `Result` when combined with a success branch. There are no status-code magic conversions beyond `web.Error` and configured `onError`.
- Middleware/handler closure captures are ordinary Valiance snapshot captures; `State[T]` and other explicitly shared services provide mutable shared identity. A `define` at module scope may depend on niladic elements but not secretly capture a module assignment.

## 18. Worked applications: ordinary Valiance, no framework DSL

The examples below describe the **intended public API**. They are not representations of a currently installed `std.web` package. Their structure is normative; the implementation must expose these elements using Valiance's existing ECS/`define`/modifier constructs.

### 18.1 A modular application

`users.vlnc`:

```valiance
import {std.web, std.web.[get, post], std.html}

public define \routes =>
  $router = web.Router

  $router := get("/"): fn (req: web.GetRequest) =>
    html.h1 "Users"
  end

  $router := get("/{id}"): fn (req: web.GetRequest) =>
    const $id = $req["id"] ?
    html.h1 "User ${$id}"
  end

  $router := post("/"): fn (req: web.PostRequest) =>
    const $new = web.bodyJson[NewUser] $req ?
    const $created = createUser $new ?
    const $res = web.jsonResponse $created ?
    web.created("/users/${$created.id}", $res)
  end

  $router
end
```

`admin.vlnc`:

```valiance
import {std.web, std.web.[get, post], std.html}

public define \adminRoutes =>
  $router = web.Router
  $router := get("/"): fn (req: web.GetRequest) => html.h1 "Admin" end
  $router := post("/reindex"): reindexSearch
  $router
end
```

`main.vlnc`:

```valiance
import {std.web, std.web.[get, post, group, middleware], std.html}
import {users, admin}

$app = web.App
$app := middleware $logging
$app := middleware $securityHeaders

$app := get("/"): web.page "public/index.html"
$app := group("/assets", web.staticRouter("public/assets"))
$app := group("/users", users.\routes)
$app := group("/admin", admin.\adminRoutes, middleware = [$requireAdmin])

$app := get("/health"): fn (req: web.GetRequest) => "OK" end

run $app
```

Only values are composed; importing `admin` does not open a listener. `users.\routes` and `admin.\adminRoutes` are evaluated niladic elements returning ordinary Router values. `web.page` supplies a normal response-producing callable; the framework's route registration still owns the handler argument.

### 18.2 Using ordinary `Err` and generic fault recovery

```valiance
$app := post("/account"): fn (req: web.PostRequest) =>
  const $newAccount = web.bodyJson[NewAccount] $req ?
  const $account = createAccount $newAccount ?
  web.jsonResponse $account
end
```

An invalid JSON request becomes ordinary `Error.InvalidJson` (400); a storage `Err` not explicitly mapped becomes 500. A catchable `ValueFault` escaping `createAccount` reaches the protected handler wrapper, is logged, and returns safe 500 without leaking fault text. All registered outer after-callbacks still run.

### 18.3 Direct-return authentication and response decoration

```valiance
$security = fn (req: web.Request) =>
  record{
    request => $req,
    after => fn (res: web.Response) =>
      web.withHeader($res, "X-Content-Type-Options", "nosniff")
    end
  }
end

$requireAdmin = fn (req: web.Request) =>
  if (isAdmin $req) => $req
  else => web.Error.Forbidden("Forbidden")
  end
end

$app := middleware $security
$app := group("/admin", admin.\adminRoutes, middleware = [$requireAdmin])
```

A rejected admin request returns 403, still receives the security header, and never invokes its handler. No `next()` call is needed.

### 18.4 A query with duplicate-friendly input

```valiance
$app := get("/search"): fn (req: web.GetRequest) =>
  const $params = web.queryParams $req
  const $term = web.requireParam($params, "q") ?
  const $tags = web.paramValues($params, "tag")
  const $results = search($term, $tags) ?
  web.jsonResponse $results
end
```

`/search?q=valiance&tag=web&tag=language` passes both tag values in order; `/search?q=x&q=y` returns 400 from `requireParam` instead of silently selecting one.

### 18.5 Safe body-bearing QUERY

```valiance
$app := query("/search"): fn (req: web.QueryRequest) =>
  const $criteria = web.bodyJson[SearchCriteria] $req ?
  const $results = searchReadOnly $criteria ?
  web.jsonResponse $results
end
```

The request **requires** an appropriate `Content-Type`; the handler must have safe/idempotent semantics. QUERY is not a covert POST.

### 18.6 An ordinary form and post/redirect/get

```valiance
$app := post("/contact"): fn (req: web.PostRequest) =>
  const $fields = web.formParams $req ?
  const $email = web.requireParam($fields, "email") ?
  const $message = web.requireParam($fields, "message") ?
  sendMessage($email, $message) ?
  web.redirect "/thank-you"
end
```

The result is 303 by default and the browser follows with GET. Form values are not mixed with query parameters or path captures.

### 18.7 Session cookie with explicit authentication responsibility

```valiance
$app := post("/login"): fn (req: web.PostRequest) =>
  const $creds = web.bodyJson[Credentials] $req ?
  const $session = createSession $creds ?
  web.withCookie(web.redirect("/dashboard"), "session", $session.token)
end

$app := post("/logout"): fn (req: web.PostRequest) =>
  invalidateSession $req ?
  web.deleteCookie(web.redirect("/login"), "session")
end
```

`createSession` and `invalidateSession` are application functions. The framework sets secure cookie attributes but cannot infer whether a token is authentic, expired or protected against CSRF.

### 18.8 File download, cache validators and no-store

```valiance
$app := get("/report"): fn (req: web.GetRequest) =>
  web.download("reports/annual.csv", filename = "annual-report.csv")
end

$app := get("/catalog"): fn (req: web.GetRequest) =>
  const $products = listCatalog() ?
  const $res = web.jsonResponse $products ?
  web.cachePublic($res, maxAge = 300)
end

$app := get("/me"): fn (req: web.GetRequest) =>
  const $account = currentAccount $req ?
  const $res = web.jsonResponse $account ?
  web.noStore $res
end
```

### 18.9 App-level error page

```valiance
$app = web.App
$app := web.withErrorHandler: fn (context: web.ErrorContext) =>
  const $fallback = web.defaultErrorResponse $context
  web.withHeader($fallback, "Cache-Control", "no-store")
end
```

The default error-mapper helper preserves a genuine 404, 405, 429 or 500 and applies the correct mandatory headers. This callback simply decorates those responses without swallowing the typed failure. A handler's explicitly returned `Response(status=NotFound)` is not an error value and does not invoke this hook.

### 18.10 Independent socket-free test

```valiance
$app = web.App
$app := get("/hello"): fn (req: web.GetRequest) => "Hello!" end

const $req = web.testRequest(method = web.Method.GET, target = "/hello") ?
const $res = web.dispatch($app, $req) ?
assert ($res.status == web.Status.OK)
assert (web.responseText($res) ? == "Hello!")
```

For a live HTTP/3 integration test, do **not** reuse this synthetic dispatch result as evidence of QUIC correctness: initiate a real QUIC/TLS connection and assert equivalent logical response under the live adapter.

## 19. Normative references, verification and version policy

### 19.1 Sources used to ground this design

**Valiance repository baseline:** `Valiance-main(16).zip` as supplied in the conversation. Relevant language and stdlib source sections: `docs/language.md` §§2.5, 3, 6, 8, 9, 10, 11, 12–14, 19.4, 20–21, 25; `src/valiance/std/json.vlnc`; `src/valiance/std/file.vlnc`; `tests/test_try_handle_early_return.py`; `src/valiance/parsing/parser.py`, `src/valiance/runtime/compiler.py`, `src/valiance/runtime/vm.py`. This repository provides the language/runtime mechanics; it **does not** already provide the requested complete `std.web` API.

**HTTP and transport standards (official primary documents):**

- RFC 9110, HTTP Semantics — https://www.rfc-editor.org/rfc/rfc9110.html
- RFC 9111, HTTP Caching — https://www.rfc-editor.org/rfc/rfc9111.html
- RFC 9112, HTTP/1.1 — https://www.rfc-editor.org/rfc/rfc9112.html
- RFC 9113, HTTP/2 — https://www.rfc-editor.org/rfc/rfc9113.html
- RFC 9114, HTTP/3 — https://www.rfc-editor.org/rfc/rfc9114.html
- RFC 9000, QUIC transport — https://www.rfc-editor.org/rfc/rfc9000.html
- RFC 9001, TLS with QUIC — https://www.rfc-editor.org/rfc/rfc9001.html
- RFC 10008, The HTTP QUERY Method (June 2026) — https://www.rfc-editor.org/rfc/rfc10008.html
- RFC 8259, JSON — https://www.rfc-editor.org/rfc/rfc8259.html
- RFC 7578, multipart/form-data — https://www.rfc-editor.org/rfc/rfc7578.html
- RFC 6265, HTTP State Management — https://www.rfc-editor.org/rfc/rfc6265.html
- HTTPbis updated cookie guidance (draft status as of specification date) — https://httpwg.org/http-extensions/draft-ietf-httpbis-rfc6265bis.html
- RFC 6266, Content-Disposition — https://www.rfc-editor.org/rfc/rfc6266.html
- RFC 8187, Header Field Parameters — https://www.rfc-editor.org/rfc/rfc8187.html
- RFC 6455, WebSocket — https://www.rfc-editor.org/rfc/rfc6455.html
- RFC 8441, WebSocket over HTTP/2 — https://www.rfc-editor.org/rfc/rfc8441.html
- RFC 9220, WebSocket over HTTP/3 — https://www.rfc-editor.org/rfc/rfc9220.html

**Candidate protocol libraries examined as feasible implementation boundaries (not a substitute for testing):** [h11](https://pypi.org/project/h11/), [hyper-h2](https://python-hyper.org/projects/hyper-h2/en/stable/), and [aioquic](https://pypi.org/project/aioquic/). The implementation pins supported releases after lock-file, Python 3.14 and security review. QUIC and HTTP/3 are required regardless of chosen backend.

### 19.2 Version and change control

This is the **v1.0 implementation contract**. Earlier draft policies superseded here include: `web.App()` as assumed niladic spelling; slash-equivalence default; deferral of streaming/HTTP3; response-pipeline `next()`/tuple wrappers; explicit Result wrapping of handler return unions; catching faults as `PanicError`; implicit JSON of arbitrary objects; and a third special niladic-router-factory group overload. None of those superseded behaviours may be silently reintroduced.

The implementation agent MUST maintain the public API and semantics specified in this document, write the public API reference and conformance tests, and report any **actual** contradiction in the current language/runtime before proposing a general language fix. Such a report is an implementation defect to resolve under this specification, not permission to ship a degraded alternative. If a standards erratum or new standard supersedes a wire-level rule, update the relevant protocol adapter and tests without altering everyday Valiance handler syntax unless the type-level contract genuinely requires a versioned change.

**Completion definition:** every public symbol in §17 is implemented, every relevant §15 acceptance gate passes, and the application in §18 compiles with the specified ordinary Valiance syntax using the installed `std.web`/`std.html` modules. No deferred features remain within the supported `std.web` scope.

---

*End of normative `std.web` v1.0 specification.*
