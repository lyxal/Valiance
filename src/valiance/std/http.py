"""Valiance language adapters for the outbound HTTP reactor.

All native callbacks return quickly: transfers and body reads yield
ExternalOperation objects consumed by the VM's scheduler integration.
Only the scheduler constructs or releases managed Valiance values.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import InvalidOperation
from itertools import count
from threading import RLock
from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.documentation import element_documentation
from valiance.elements.stdlib_native import stdlib_element
from valiance.runtime.external_io import ExternalOperation
from valiance.runtime.runtime_values import (
    ListValue, ObjectRuntimeType, ObjectValue, RuntimeNumber, TaggedValue,
)
from valiance.std import _http_core as core
from valiance.std._http_body import UploadPipe, MultipartUpload
from valiance.std import _http_transport as transport
from valiance.std import json as json_codec
from valiance.std._http_errors import ErrorKind, HttpError, HttpValueError
from valiance.vtypes.symbols import Symbol


# Generated reference metadata is kept with the native definitions. Each
# example analyses without requiring external network access or credentials;
# runtime examples are validated separately against local independent peers.
_HTTP_EXAMPLES: dict[str, tuple[str, str]] = {
    'openClient': ('Create a configured shareable HTTP client.',
                   'import {std.http}\nhttp.openClient(http.ClientConfig) ?'),
    'emptyHeaders': ('Create an empty ordered HTTP header collection.',
                     'import {std.http}\nhttp.emptyHeaders'),
    'withHeader': ('Append an HTTP field without deleting repeated values.',
                   'import {std.http}\nhttp.withHeader(http.emptyHeaders, "X-Test", "yes") ?'),
    'headerValues': ('Look up all values of a case-insensitive HTTP field.',
                     'import {std.http}\nhttp.headerValues(http.emptyHeaders, "Set-Cookie")'),
    'withQuery': ('Append ordered query parameters to a validated HTTP URL.',
                  'import {std.http}\nhttp.withQuery("https://example.org/", '
                  '[http.FormField("page", "1")]) ?'),
    'send': ('Send a validated request through a shareable client.',
             'import {std.http}\n$client = http.Client\n'
             'http.send($client, http.Request("GET", "https://example.org/")) ?'),
    'get': ('Send GET using a VM-lifetime-managed default client.',
            'import {std.http}\nhttp.get("https://example.org/") ?'),
    'head': ('Send HEAD using a VM-lifetime-managed default client.',
             'import {std.http}\nhttp.head("https://example.org/") ?'),
    'delete': ('Send DELETE using a VM-lifetime-managed default client.',
               'import {std.http}\nhttp.delete("https://example.org/item") ?'),
    'post': ('Send UTF-8 text in a POST request.',
             'import {std.http}\nhttp.post("https://example.org/item", "hello") ?'),
    'put': ('Send UTF-8 text in a PUT request.',
            'import {std.http}\nhttp.put("https://example.org/item", "hello") ?'),
    'patch': ('Send UTF-8 text in a PATCH request.',
              'import {std.http}\nhttp.patch("https://example.org/item", "hello") ?'),
    'bytes': ('Consume a response as bounded complete binary data.',
              'import {std.http}\n$response = http.get("https://example.org/") ?\n'
              'http.bytes($response) ?'),
    'text': ('Consume a response as bounded, strictly decoded UTF-8 text.',
             'import {std.http}\n$response = http.get("https://example.org/") ?\n'
             'http.text($response) ?'),
    'json': ('Consume a response as the dynamic std.json.Json hierarchy.',
             'import {std.http}\n$response = http.get("https://example.org/data") ?\n'
             'http.json($response) ?'),
}
_HTTP_DOCS = {
    name: element_documentation(summary, examples=(example,), category='HTTP')
    for name, (summary, example) in _HTTP_EXAMPLES.items()
}


def _nominal(name: str) -> T.Type:
    """Resolve an HTTP nominal type name in the Valiance type registry."""
    return T.N(Symbol(name, ("http",)))


_IO = (T.ElementTag(Symbol("IO")),)
_CLIENT_CONFIG = _nominal("ClientConfig")
_CLIENT = _nominal("Client")
_REQUEST = _nominal("Request")
_RESPONSE = _nominal("Response")
_HEADERS = _nominal("Headers")
_ERROR = _nominal("Error")
_BODY = _nominal("ResponseBody")
_UPLOAD_WRITER = _nominal("UploadWriter")
_UPLOAD_SOURCE = _nominal("UploadSource")
_UPLOAD_PAIR = _nominal("UploadPipe")
_FORM_FIELD = _nominal("FormField")
_BYTE_TAG = T.DataTag("byte", 1)
_BYTES = T.Tagged(T.AtLeastList(T.Int), _BYTE_TAG)
_OPTIONAL_BYTES = T.optional(_BYTES)
_RESULT_BYTES = T.Result(_BYTES, _ERROR)
_RESULT_READ = T.Result(_OPTIONAL_BYTES, _ERROR)
_RESULT_RESPONSE = T.Result(_RESPONSE, _ERROR)
_RESULT_HEADERS = T.Result(_HEADERS, _ERROR)
_RESULT_TEXT = T.Result(T.String, _ERROR)
_JSON = T.N(Symbol("Json", ("json",)))
_RESULT_JSON = T.Result(_JSON, _ERROR)
_ERROR_RUNTIME = ObjectRuntimeType(
    accepted_names=("Err", "http.Error"), field_order=("message",),
)
_RESOURCE_LOCK = RLock()
_IDS = count(1)
_CLIENTS: dict[int, _ClientEntry] = {}
_BODIES: dict[int, transport.ResponseBody] = {}
_UPLOADS: dict[int, UploadPipe] = {}


@dataclass
class _ClientEntry:
    client: transport.Client
    active: int = 0
    closed: bool = False

    def lease(self) -> None:
        """Retain a client lease while an accepted transfer is outstanding."""
        with _RESOURCE_LOCK:
            if self.closed:
                raise HttpError(ErrorKind.CLOSED, "client is closed", "admission")
            self.active += 1

    def release(self) -> None:
        """Release an active client lease, closing a client marked for shutdown if last."""
        should_close = False
        with _RESOURCE_LOCK:
            self.active -= 1
            if self.active < 0:
                raise RuntimeError("HTTP transfer lease underflow")
            should_close = self.closed and self.active == 0
        if should_close:
            self.client.close()

    def close(self) -> None:
        """Stop accepting new requests and close the transport after active leases finish."""
        with _RESOURCE_LOCK:
            self.closed = True
            should_close = self.active == 0
        if should_close:
            self.client.close()


def _vm(ctx: RuntimeContext) -> Any:
    """The existing builtin context's call callback owns this VM."""
    return ctx.call.__self__


def _default(ctx: RuntimeContext) -> _ClientEntry:
    """Find or create the default HTTP client attached to the executing VM."""
    vm = _vm(ctx)
    entry = getattr(vm, "_http_default_client", None)
    if entry is None or entry.closed:
        entry = _ClientEntry(transport.Client())
        vm._http_default_client = entry
    return entry


def _error(exc: BaseException) -> ObjectValue:
    """Translate native and validation errors into a structured Valiance HTTP error value."""
    if isinstance(exc, HttpValueError):
        kind = "InvalidRequest"
    elif isinstance(exc, HttpError):
        kind = {
            ErrorKind.INVALID_REQUEST: "InvalidRequest",
            ErrorKind.INVALID_CONFIG: "InvalidRequest",
            ErrorKind.TIMEOUT: "Timeout",
            ErrorKind.RESOURCE_LIMIT: "Limit",
            ErrorKind.CLOSED: "Closed",
            ErrorKind.CANCELLED: "Closed",
            ErrorKind.UNSUPPORTED: "Unsupported",
        }.get(exc.kind, "Network")
    elif isinstance(exc, UnicodeDecodeError):
        kind = "Decode"
    else:
        kind = "Network"
    # Native Err types need explicit runtime evidence for the '?' operator.
    return ObjectValue(f"http.Error.{kind}", {"message": str(exc)},
                       runtime_type=_ERROR_RUNTIME)


def _optional(value: Any) -> Any | None:
    """Unwrap a Valiance optional payload or return None for the empty variant."""
    if isinstance(value, ObjectValue) and value.type_name == "None":
        return None
    if isinstance(value, ObjectValue) and value.type_name == "Some":
        return value.fields["value"]
    return value


def _bool_option(value: Any) -> bool:
    """Decode an optional Valiance Boolean into a native policy flag."""
    unwrapped = _optional(value)
    return False if unwrapped is None else bool(unwrapped)


def _bytes_value(blob: bytes) -> TaggedValue:
    """Wrap native bytes as the existing tagged Valiance byte-list representation."""
    return TaggedValue(ListValue([RuntimeNumber(n) for n in blob], runtime_rank=1),
                       frozenset({_BYTE_TAG}))


def _bytes_raw(value: Any) -> bytes:
    """Validate and convert a Valiance byte collection into native bytes."""
    if isinstance(value, TaggedValue):
        value = value.value
    try:
        return bytes(int(x) for x in value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HttpValueError("body must contain valid bytes") from exc


def _headers_raw(value: Any) -> core.Headers:
    """Decode an HTTP header object while preserving ordering and repeated fields."""
    value = _optional(value)
    if value is None:
        return core.Headers()
    if not isinstance(value, ObjectValue) or value.type_name != "http.Headers":
        raise HttpValueError("expected HTTP Headers")
    names = _optional(value.fields["names"])
    values = _optional(value.fields["values"])
    if names is None and values is None:
        return core.Headers()
    if names is None or values is None or len(names) != len(values):
        raise HttpValueError("invalid headers layout")
    return core.Headers(tuple(zip(names, values, strict=True)))


def _headers_value(value: core.Headers) -> ObjectValue:
    """Wrap native HTTP headers as a Valiance runtime object."""
    names = ListValue([name for name, _ in value.pairs], runtime_rank=1)
    values = ListValue([item for _, item in value.pairs], runtime_rank=1)
    return ObjectValue("http.Headers", {
        "names": ObjectValue("Some", {"value": names}),
        "values": ObjectValue("Some", {"value": values}),
    })


def _form_fields(value: Any) -> tuple[tuple[str, str], ...]:
    """Extract ordered URL-encoded form fields from Valiance values."""
    try:
        if not isinstance(value, (tuple, list, ListValue)):
            raise HttpValueError("expected a list of FormField values")
        fields = []
        for item in value:
            if not isinstance(item, ObjectValue) or item.type_name != "http.FormField":
                raise HttpValueError("expected FormField values")
            fields.append((item.fields["name"], item.fields["value"]))
        return tuple(fields)
    except (KeyError, TypeError) as exc:
        raise HttpValueError("invalid form fields") from exc


def _multipart_parts(value: Any) -> tuple[core.MultipartPart, ...]:
    """Validate multipart fields and file parts from Valiance objects."""
    if not isinstance(value, (tuple, list, ListValue)):
        raise HttpValueError("expected multipart parts")
    parts = []
    for item in value:
        if not isinstance(item, ObjectValue):
            raise HttpValueError("expected MultipartPart variants")
        if item.type_name == "http.MultipartPart.Text":
            parts.append(core.MultipartPart(item.fields["name"], item.fields["value"].encode("utf-8"),
                                            content_type="text/plain; charset=utf-8"))
        elif item.type_name == "http.MultipartPart.StreamFile":
            source = item.fields["source"]
            if not isinstance(source, ObjectValue) or source.type_name != "http.UploadSource":
                raise HttpValueError("streamed multipart file requires UploadSource")
            parts.append(core.MultipartPart(item.fields["name"], _upload_source(source.fields["token"]),
                                            item.fields["filename"], item.fields["contentType"]))
        elif item.type_name == "http.MultipartPart.File":
            parts.append(core.MultipartPart(item.fields["name"], _bytes_raw(item.fields["data"]),
                                            item.fields["filename"], item.fields["contentType"]))
        else:
            raise HttpValueError("unknown MultipartPart variant")
    return tuple(parts)


def _request_raw(value: Any) -> core.Request:
    """Decode and validate a Valiance request into the transport-independent request model."""
    if not isinstance(value, ObjectValue) or value.type_name != "http.Request":
        raise HttpValueError("expected HTTP Request")
    body = _optional(value.fields["body"])
    data: bytes | UploadPipe | MultipartUpload | None = None
    auto_content_type: str | None = None
    if body is not None:
        if not isinstance(body, ObjectValue):
            raise HttpValueError("invalid request body")
        kind = body.type_name.rsplit(".", 1)[-1]
        if kind == "Text":
            data = body.fields["value"].encode("utf-8")
        elif kind == "Bytes":
            data = _bytes_raw(body.fields["value"])
        elif kind == "Form":
            data = core.encode_form(_form_fields(body.fields["fields"]))
            auto_content_type = "application/x-www-form-urlencoded; charset=utf-8"
        elif kind == "Multipart":
            data, auto_content_type = core.encode_multipart(_multipart_parts(body.fields["parts"]))
        elif kind == "MultipartStream":
            data, auto_content_type = core.encode_multipart_stream(_multipart_parts(body.fields["parts"]))
        elif kind == "Stream":
            source = body.fields["source"]
            if not isinstance(source, ObjectValue) or source.type_name != "http.UploadSource":
                raise HttpValueError("stream body requires UploadSource")
            data = _upload_source(source.fields["token"])
        elif kind != "Empty":
            raise HttpValueError("unrecognized request body")
    headers = _headers_raw(value.fields["headers"])
    if auto_content_type is not None:
        if headers.values("content-type"):
            raise HttpValueError("structured request bodies manage Content-Type")
        headers = headers.append("Content-Type", auto_content_type)
    return core.Request(value.fields["method"], value.fields["url"], headers, data)


def _response_value(response: transport.Response, vm: Any) -> ObjectValue:
    """Register and wrap a response body with VM-owned resource tracking."""
    with _RESOURCE_LOCK:
        token = next(_IDS)
        _BODIES[token] = response.body
    if not hasattr(vm, "_http_body_tokens"):
        vm._http_body_tokens = set()
    vm._http_body_tokens.add(token)
    meta = response.metadata
    return ObjectValue("http.Response", {
        "status": RuntimeNumber(meta.status), "finalUrl": meta.final_url,
        "protocol": meta.protocol, "headers": _headers_value(meta.headers),
        "body": ObjectValue("http.ResponseBody", {"token": RuntimeNumber(token)}),
    })


def _transfer(entry: _ClientEntry, request: core.Request, vm: Any) -> tuple[Any, ...]:
    """Create a scheduler-compatible external operation while retaining the client's lease."""
    entry.lease()
    try:
        future = entry.client.send_async(request, on_finish=entry.release)
    except BaseException:
        entry.release()
        raise
    return (ExternalOperation(
        future, lambda response: (_response_value(response, vm),),
        lambda exc: (_error(exc),), future.cancel,
    ),)


def _client_config(value: ObjectValue) -> core.ClientConfig:
    """Decode validated Valiance client configuration into immutable native options."""
    if not isinstance(value, ObjectValue) or value.type_name != "http.ClientConfig":
        raise HttpValueError("expected HTTP ClientConfig")
    fields = value.fields
    return core.ClientConfig(
        max_connections=int(fields["maxConnections"]),
        max_connections_per_origin=int(fields["maxConnectionsPerOrigin"]),
        max_pending=int(fields["maxPending"]),
        body_buffer_bytes=int(fields["bodyBufferBytes"]),
        max_client_buffer_bytes=int(fields["maxClientBufferBytes"]),
        max_response_header_bytes=int(fields["maxResponseHeaderBytes"]),
        max_response_header_fields=int(fields["maxResponseHeaderFields"]),
        max_body_bytes=int(fields["maxBodyBytes"]),
        max_buffered_upload_bytes=int(fields["maxBufferedUploadBytes"]),
        connect_timeout_ms=int(fields["connectTimeoutMs"]),
        connection_acquire_timeout_ms=(None if _optional(fields["connectionAcquireTimeoutMs"]) is None else
                                       int(_optional(fields["connectionAcquireTimeoutMs"]))),
        total_timeout_ms=int(fields["totalTimeoutMs"]),
        read_idle_timeout_ms=(None if _optional(fields["readIdleTimeoutMs"]) is None else
                              int(_optional(fields["readIdleTimeoutMs"]))),
        write_idle_timeout_ms=(None if _optional(fields["writeIdleTimeoutMs"]) is None else
                               int(_optional(fields["writeIdleTimeoutMs"]))),
        follow_redirects=_bool_option(fields["followRedirects"]),
        max_redirects=int(fields["maxRedirects"]),
        allow_downgrade=_bool_option(fields["allowDowngrade"]),
        enable_cookies=_bool_option(fields["enableCookies"]),
        allowed_origins=(None if _optional(fields["allowedOrigins"]) is None else
                         tuple(_optional(fields["allowedOrigins"]))),
        require_https=_bool_option(fields["requireHttps"]),
        deny_non_public_ips=_bool_option(fields["denyNonPublicIps"]),
        proxy=_optional(fields["proxy"]),
        ca_bundle=_optional(fields["caBundle"]),
    )


def _register_client(entry: _ClientEntry, ctx: RuntimeContext) -> int:
    """Register a native client token and its VM shutdown ownership."""
    with _RESOURCE_LOCK:
        token = next(_IDS)
        _CLIENTS[token] = entry
    vm = _vm(ctx)
    if not hasattr(vm, "_http_client_tokens"):
        vm._http_client_tokens = set()
    vm._http_client_tokens.add(token)
    return token


@stdlib_element("nativeOpenClient", (), (T.Int,), exported=False)
def _open_client(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a default owned client for the Valiance Client constructor."""
    del args
    return (RuntimeNumber(_register_client(_ClientEntry(transport.Client()), ctx)),)


@stdlib_element("openClient", (_CLIENT_CONFIG,), (T.Result(_CLIENT, _ERROR),),
                param_names=("config",),
                documentation=_HTTP_DOCS["openClient"])
def _configured_client(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Validate and snapshot immutable client policy before creating transport."""
    try:
        entry = _ClientEntry(transport.Client(_client_config(args[0])))
        token = _register_client(entry, ctx)
        return (ObjectValue("http.Client", {"token": RuntimeNumber(token)}),)
    except (HttpValueError, HttpError) as exc:
        return (_error(exc),)


@stdlib_element("nativeCloseClient", (T.Int,), (), exported=False)
def _close_client(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Close a registered Valiance client token without double-freeing active leases."""
    del ctx
    with _RESOURCE_LOCK:
        entry = _CLIENTS.pop(int(args[0]), None)
    if entry is not None:
        entry.close()
    return ()


@stdlib_element("emptyHeaders", (), (_HEADERS,),
                documentation=_HTTP_DOCS["emptyHeaders"])
def _empty_headers(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Construct the empty ordered HTTP header collection for Valiance callers."""
    del args, ctx
    return (_headers_value(core.Headers()),)


@stdlib_element("withHeader", (_HEADERS, T.String, T.String), (_RESULT_HEADERS,),
                param_names=("headers", "name", "value"),
                documentation=_HTTP_DOCS["withHeader"])
def _with_header(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Return headers with an additional validated field or an error result."""
    del ctx
    try:
        return (_headers_value(_headers_raw(args[0]).append(args[1], args[2])),)
    except HttpValueError as exc:
        return (_error(exc),)


@stdlib_element("headerValues", (_HEADERS, T.String), (T.AtLeastList(T.String),),
                param_names=("headers", "name"),
                documentation=_HTTP_DOCS["headerValues"])
def _header_values(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Expose every case-insensitive value of a header field to Valiance."""
    del ctx
    return (ListValue(list(_headers_raw(args[0]).values(args[1])), runtime_rank=1),)


@stdlib_element("withQuery", (T.String, T.AtLeastList(_FORM_FIELD)),
                (T.Result(T.String, _ERROR),), param_names=("url", "fields"),
                documentation=_HTTP_DOCS["withQuery"])
def _with_query(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Append encoded query parameters to a validated URL for Valiance callers."""
    del ctx
    try:
        return (core.url_with_query(args[0], _form_fields(args[1])),)
    except (HttpValueError, ValueError) as exc:
        return (_error(exc),)


@stdlib_element("send", (_CLIENT, _REQUEST), (_RESULT_RESPONSE,), element_tags=_IO,
                param_names=("client", "request"),
                documentation=_HTTP_DOCS["send"])
def _send(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Dispatch a Valiance request through its explicitly owned HTTP client."""
    try:
        client, request = args
        token = int(client.fields["token"])
        with _RESOURCE_LOCK:
            entry = _CLIENTS.get(token)
        if entry is None:
            raise HttpError(ErrorKind.CLOSED, "client no longer available", "admission")
        return _transfer(entry, _request_raw(request), _vm(ctx))
    except (HttpValueError, HttpError, KeyError, AttributeError) as exc:
        return (_error(exc),)


def _default_request(ctx: RuntimeContext, method: str, url: str,
                     body: bytes | None = None, headers: core.Headers | None = None) -> tuple[Any, ...]:
    """Send one request through the VM-scoped default client and error contract."""
    try:
        return _transfer(_default(ctx), core.Request(method, url, headers or core.Headers(), body), _vm(ctx))
    except (HttpValueError, HttpError) as exc:
        return (_error(exc),)


@stdlib_element("get", (T.String,), (_RESULT_RESPONSE,), param_names=("url",), element_tags=_IO,
                documentation=_HTTP_DOCS["get"])
def _get(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Send a GET request through the VM-managed default HTTP client."""
    return _default_request(ctx, "GET", args[0])


@stdlib_element("head", (T.String,), (_RESULT_RESPONSE,), param_names=("url",), element_tags=_IO,
                documentation=_HTTP_DOCS["head"])
def _head(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Send a HEAD request through the VM-managed default HTTP client."""
    return _default_request(ctx, "HEAD", args[0])


@stdlib_element("delete", (T.String,), (_RESULT_RESPONSE,), param_names=("url",), element_tags=_IO,
                documentation=_HTTP_DOCS["delete"])
def _delete(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Send a DELETE request through the VM-managed default HTTP client."""
    return _default_request(ctx, "DELETE", args[0])


@stdlib_element("post", (T.String, T.String), (_RESULT_RESPONSE,),
                param_names=("url", "text"), element_tags=_IO,
                documentation=_HTTP_DOCS["post"])
def _post(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Send a UTF-8 text POST request through the VM-managed default HTTP client."""
    return _default_request(ctx, "POST", args[0], args[1].encode("utf-8"),
                            core.Headers((("Content-Type", "text/plain; charset=utf-8"),)))


@stdlib_element("put", (T.String, T.String), (_RESULT_RESPONSE,),
                param_names=("url", "text"), element_tags=_IO,
                documentation=_HTTP_DOCS["put"])
def _put(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Send a UTF-8 text PUT request through the VM-managed default HTTP client."""
    return _default_request(ctx, "PUT", args[0], args[1].encode("utf-8"),
                            core.Headers((("Content-Type", "text/plain; charset=utf-8"),)))


@stdlib_element("patch", (T.String, T.String), (_RESULT_RESPONSE,),
                param_names=("url", "text"), element_tags=_IO,
                documentation=_HTTP_DOCS["patch"])
def _patch(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Send a UTF-8 text PATCH request through the VM-managed default HTTP client."""
    return _default_request(ctx, "PATCH", args[0], args[1].encode("utf-8"),
                            core.Headers((("Content-Type", "text/plain; charset=utf-8"),)))


def shutdown_vm(vm: Any) -> None:
    """Terminate all VM-owned native transfers and release bridge registrations."""
    with _RESOURCE_LOCK:
        entries = [_CLIENTS.pop(key, None) for key in getattr(vm, "_http_client_tokens", ())]
        bodies = [_BODIES.pop(key, None) for key in getattr(vm, "_http_body_tokens", ())]
        uploads = [_UPLOADS.pop(key, None) for key in getattr(vm, "_http_upload_tokens", ())]
    for body in bodies:
        if body is not None:
            body.close()
    for upload in uploads:
        if upload is not None:
            upload.release_transfer(HttpError(ErrorKind.CLOSED, "VM shutdown", "upload"))
    default = getattr(vm, "_http_default_client", None)
    for entry in (*entries, default):
        if entry is not None:
            entry.client.close()
    if hasattr(vm, "_http_client_tokens"):
        vm._http_client_tokens.clear()
    if hasattr(vm, "_http_body_tokens"):
        vm._http_body_tokens.clear()
    if hasattr(vm, "_http_upload_tokens"):
        vm._http_upload_tokens.clear()
    vm._http_default_client = None


def _upload_source(token: Any) -> UploadPipe:
    """Resolve an upload source token to its exclusively owned native pipe."""
    with _RESOURCE_LOCK:
        pipe = _UPLOADS.get(int(token))
    if pipe is None:
        raise HttpError(ErrorKind.CLOSED, "upload source is closed", "upload")
    return pipe


def _release_upload(token: int) -> None:
    """Release an upload registration after both endpoints or its transfer finish."""
    with _RESOURCE_LOCK:
        pipe = _UPLOADS.get(token)
        if pipe is not None and pipe.releasable:
            _UPLOADS.pop(token, None)


def _prune_upload_tokens(vm: Any) -> None:
    """Prune VM bookkeeping only from the owning VM execution thread.

    Upload completion runs on the reactor thread and can release the global
    native registry, but must never mutate a VM-owned token set itself.
    """
    tokens = getattr(vm, "_http_upload_tokens", None)
    if tokens:
        with _RESOURCE_LOCK:
            finished = tuple(token for token in tokens if token not in _UPLOADS)
        for token in finished:
            tokens.discard(token)


def _close_body_registration(token: int, vm: Any) -> None:
    """Drop a response-body token from VM ownership after closure."""
    with _RESOURCE_LOCK:
        body = _BODIES.pop(token, None)
    if body is not None:
        body.close()
    getattr(vm, "_http_body_tokens", set()).discard(token)
    _prune_upload_tokens(vm)


@stdlib_element("nativeUploadPipe", (T.Int, T.optional(T.Int)),
                (T.Result(_UPLOAD_PAIR, _ERROR),), exported=False)
def _new_upload(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Allocate a bounded upload pipe and register both of its Valiance endpoints."""
    try:
        pipe = UploadPipe(int(args[0]), None if _optional(args[1]) is None else int(_optional(args[1])))
        with _RESOURCE_LOCK:
            token = next(_IDS)
            _UPLOADS[token] = pipe
        vm = _vm(ctx)
        if not hasattr(vm, "_http_upload_tokens"):
            vm._http_upload_tokens = set()
        vm._http_upload_tokens.add(token)
        pipe.set_finalizer(lambda token=token: _release_upload(token))
        return (ObjectValue("http.UploadPipe", {
            "writer": ObjectValue("http.UploadWriter", {"token": RuntimeNumber(token)}),
            "source": ObjectValue("http.UploadSource", {"token": RuntimeNumber(token)}),
        }),)
    except (HttpError, HttpValueError, ValueError) as exc:
        return (_error(exc),)


@stdlib_element("nativeWriteUpload", (T.Int, _BYTES),
                (T.Result(T.Int, _ERROR),), exported=False, element_tags=_IO)
def _upload_write(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Schedule a bounded asynchronous write through the upload producer."""
    del ctx
    try:
        pipe = _upload_source(args[0])
        return (ExternalOperation(
            pipe.write_async(_bytes_raw(args[1])),
            lambda size: (RuntimeNumber(size),),
            lambda exc: (_error(exc),), pipe.abort,
        ),)
    except (HttpError, HttpValueError) as exc:
        return (_error(exc),)


@stdlib_element("nativeFinishUpload", (T.Int,),
                (T.Result(T.Int, _ERROR),), exported=False)
def _upload_finish(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Mark EOF for an upload writer and validate declared length."""
    del ctx
    try:
        _upload_source(args[0]).finish()
        return (RuntimeNumber(0),)
    except HttpError as exc:
        return (_error(exc),)


@stdlib_element("nativeDropUploadWriter", (T.Int,), (), exported=False)
def _upload_drop_writer(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Release the writer endpoint, aborting unfinished uploads safely."""
    del ctx
    token = int(args[0])
    with _RESOURCE_LOCK:
        pipe = _UPLOADS.get(token)
    if pipe is not None:
        pipe.release_writer()
    return ()


@stdlib_element("nativeDropUploadSource", (T.Int,), (), exported=False)
def _upload_drop_source(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Release the source endpoint and any abandoned transfer resources."""
    del ctx
    token = int(args[0])
    with _RESOURCE_LOCK:
        pipe = _UPLOADS.get(token)
    if pipe is not None:
        pipe.release_source()
    return ()


@stdlib_element("nativeCloseBody", (T.Int,), (), exported=False)
def _close_body(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Close a Valiance response-body token and release its queued bytes."""
    _close_body_registration(int(args[0]), _vm(ctx))
    return ()


def _body(token: Any) -> transport.ResponseBody:
    """Extract the single-use response body handle from a Valiance response."""
    with _RESOURCE_LOCK:
        body = _BODIES.get(int(token))
    if body is None:
        raise HttpError(ErrorKind.CLOSED, "body was closed or consumed", "body")
    return body


@stdlib_element("nativeRead", (T.Int,), (_RESULT_READ,), exported=False, element_tags=_IO)
def _read(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Schedule one incremental, cancellation-aware response-body read."""
    del ctx
    try:
        body = _body(args[0])
        return (ExternalOperation(
            body.read_async(),
            lambda chunk: (ObjectValue("None", {}) if chunk is None else
                           ObjectValue("Some", {"value": _bytes_value(chunk)}),),
            lambda exc: (_error(exc),), body.close,
        ),)
    except HttpError as exc:
        return (_error(exc),)


@stdlib_element("nativeReadAll", (T.Int, T.Int), (_RESULT_BYTES,), exported=False, element_tags=_IO)
def _read_all(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Collect remaining response bytes up to a bounded maximum."""
    try:
        body = _body(args[0])
        size = int(args[1])
        token = int(args[0])
        vm = _vm(ctx)

        def complete(blob: bytes) -> tuple[Any, ...]:
            """Convert collected bytes into the requested Valiance body result."""
            _close_body_registration(token, vm)
            return (_bytes_value(blob),)

        def fail(exc: BaseException) -> tuple[Any, ...]:
            """Translate aggregate-read failures into an HTTP result error."""
            _close_body_registration(token, vm)
            return (_error(exc),)

        return (ExternalOperation(
            body.collect_async(size),
            complete, fail,
            lambda: _close_body_registration(token, vm),
        ),)
    except (HttpError, ValueError) as exc:
        return (_error(exc),)


def _decode_error(exc: BaseException) -> ObjectValue:
    """Map malformed text or JSON data into the public decode-error variant."""
    return ObjectValue("http.Error.Decode", {"message": str(exc)},
                       runtime_type=_ERROR_RUNTIME)


def _consume_response(args: tuple[Any, ...], limit: int | None = None) -> tuple[transport.ResponseBody, int]:
    """Take response-body ownership for one complete-body operation."""
    response = args[0]
    if not isinstance(response, ObjectValue) or response.type_name != "http.Response":
        raise HttpValueError("expected HTTP Response")
    source = _body(response.fields["body"].fields["token"])
    if limit is None:
        limit = 16 * 1024 * 1024
    if type(limit) is not int or limit < 0:
        raise HttpValueError("body size limit must be nonnegative")
    return source, limit


def _read_body_all(args: tuple[Any, ...], ctx: RuntimeContext,
                   decode: str | None = None, limit: int | None = None,
                   typename: str | None = None) -> tuple[Any, ...]:
    """Schedule bounded body aggregation from an owned Valiance response."""
    try:
        body, budget = _consume_response(args, limit)
        token = int(args[0].fields["body"].fields["token"])
        vm = _vm(ctx)
        future = body.collect_async(budget)

        def complete(blob: bytes) -> tuple[Any, ...]:
            """Convert the completed response bytes into their requested decoding form."""
            _close_body_registration(token, vm)
            if decode is None:
                return (_bytes_value(blob),)
            try:
                text = blob.decode("utf-8", "strict")
                if decode == "text":
                    return (text,)
                # Deliberately share the strict parser and Json case builder
                # used by std.json.load (exact decimals, duplicate-key check).
                parsed = json_codec._parse_json(text)
                if decode == "json":
                    return (json_codec._load_value(parsed),)
                services = json_codec._type_services(ctx)
                return (json_codec._json_decode_typed(
                    parsed, json_codec._json_type(typename),
                    services.resolve_constructor,
                    variants=services.resolve_variant_cases,
                    native_decode=services.construct_native,
                ),)
            except (ValueError, UnicodeError, RecursionError, InvalidOperation) as exc:
                return (_decode_error(exc),)

        def fail(exc: BaseException) -> tuple[Any, ...]:
            """Return a structured error when response aggregation fails."""
            _close_body_registration(token, vm)
            return (_error(exc),)

        return (ExternalOperation(future, complete, fail,
                                  lambda: _close_body_registration(token, vm)),)
    except (HttpError, HttpValueError, KeyError, AttributeError) as exc:
        return (_error(exc),)


@stdlib_element("bytes", (_RESPONSE,), (_RESULT_BYTES,), element_tags=_IO,
                param_names=("response",),
                documentation=_HTTP_DOCS["bytes"])
def _response_bytes(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Consume a response into tagged Valiance bytes with a size cap."""
    return _read_body_all(args, ctx)


@stdlib_element("text", (_RESPONSE,), (_RESULT_TEXT,), element_tags=_IO,
                param_names=("response",),
                documentation=_HTTP_DOCS["text"])
def _response_text(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Consume a response into strictly decoded UTF-8 text."""
    return _read_body_all(args, ctx, decode="text")


@stdlib_element("json", (_RESPONSE,), (_RESULT_JSON,), element_tags=_IO,
                param_names=("response",),
                documentation=_HTTP_DOCS["json"])
def _response_json(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Consume a response into the existing dynamic std.json.Json model."""
    return _read_body_all(args, ctx, decode="json")




@stdlib_element("nativeJsonAs", (_RESPONSE, T.String),
                (T.Result(T.V("T"), _ERROR),), exported=False, element_tags=_IO)
def _response_json_as(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Consume and type-check JSON using Valiance's existing typed decoder."""
    return _read_body_all(args, ctx, decode="typed", typename=args[1])
