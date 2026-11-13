"""Valiance-native bridge for the transport-independent HTTP application core.

The route table is stored in immutable App/Router *values*. Callbacks stay
owned by those values, not in a global registry. All execution goes through the
same _web_core.dispatch routing, middleware and error pipeline.
"""
from __future__ import annotations

from typing import Any
import hmac
import hashlib
from dataclasses import replace
from threading import RLock
from secrets import randbits, token_bytes
from time import monotonic
from weakref import WeakValueDictionary, ref

import valiance.vtypes as T
from valiance.vtypes.symbols import Symbol
from valiance.asts import ElementNode, NumberLiteralNode, StringLiteralNode
from valiance.elements.stdlib_native import stdlib_element
from valiance.elements.builtins import RuntimeContext
from valiance.elements.documentation import element_documentation
from valiance.runtime.runtime_values import ObjectValue, ObjectRuntimeType, ListValue, DictValue, RecordValue, RuntimeNumber, TaggedValue, BorrowedValue
from valiance.runtime.vm import _duplicate_occurrence, _release_value
from valiance.std import _web_core as core
from valiance.std.html import render_node as _render_html_node


def _nominal(name: str) -> T.Type:
    return T.N(Symbol(name.rsplit('.', 1)[-1], ('web', *name.rsplit('.', 1)[:-1])))

_APP = _nominal('App')
_ROUTER = _nominal('Router')
_RESPONSE = _nominal('Response')
_HTML_NODE = T.N(Symbol('Node', ('html',)))
_BYTE_SOURCE = _nominal('ByteSource')
_EVENT_SOURCE = _nominal('EventSource')
_EVENT = _nominal('Event')
_STREAM_BODY = _nominal('StreamBody')
_REQUEST_BODY_STREAM = _nominal('RequestBodyStream')
_BODY_MODE = _nominal('BodyMode')
_ERROR = _nominal('Error')
_REQUEST = _nominal('Request')
_PARAMS = _nominal('Params')
_HEADERS = _nominal('Headers')
_SERVER_CONFIG = _nominal('ServerConfig')
_PROXY_MODE = _nominal('ProxyMode')
_PROTOCOL = _nominal('Protocol')
_TLS = _nominal('TLS')
_LIMITS = _nominal('Limits')
_CORS_POLICY = _nominal('CorsPolicy')
_COMPRESSION_POLICY = _nominal('CompressionPolicy')
_COMPRESSION_MIDDLEWARE = _nominal('CompressionMiddleware')
_CORS_MIDDLEWARE = _nominal('CorsMiddleware')
_CONFIG_ERROR = _nominal('ConfigError')
_COOKIE_PAIRS = _nominal('CookiePairs')
_MULTIPART_FORM = _nominal('MultipartForm')
_MULTIPART_PART = _nominal('MultipartPart')
_UPLOAD_STREAM = _nominal('UploadStream')
_WEBSOCKET = _nominal('WebSocket')
_WEBSOCKET_REQUEST = _nominal('WebSocketRequest')
_WEBSOCKET_MESSAGE = _nominal('WebSocketMessage')
_WEBSOCKET_POLICY = _nominal('WebSocketPolicy')
_CONTEXT_KEY = _nominal('ContextKey')
_UPLOAD = _nominal('Upload')
_VALIDATORS = _nominal('Validators')
_PREPARED_APP = _nominal('PreparedApp')
_ROUTE_DESCRIPTOR = _nominal('RouteDescriptor')
_SERVER_HANDLE = _nominal('ServerHandle')
_SERVER_ERROR = _nominal('ServerError')
_SERVER_STOP_REPORT = _nominal('ServerStopReport')
_BOUND_ENDPOINT = _nominal('BoundEndpoint')
_SERVER_STATS = _nominal('ServerStats')
_LOG_EVENT = _nominal('LogEvent')
_ERROR_CONTEXT = _nominal('ErrorContext')
_VALUE_ERROR = T.N(Symbol('ValueError'))
_BYTE_TAG = T.DataTag('byte', 1)
_BYTE_TAGS = frozenset({_BYTE_TAG})
_BYTE_LIST = T.Tagged(T.AtLeastList(T.Int), _BYTE_TAG)
_STREAM_CALLBACK_GATE = RLock()
_METHODS = (('get', 'Get'), ('head', 'Head'), ('post', 'Post'),
            ('put', 'Put'), ('patch', 'Patch'), ('delete', 'Delete'),
            ('options', 'Options'), ('query', 'Query'))


def _optional_contents(value: Any) -> list[Any]:
    if not isinstance(value, ObjectValue):
        raise TypeError('Expected an optional collection')
    if value.type_name == 'None':
        return []
    if value.type_name != 'Some':
        raise TypeError('Expected Some or None')
    return list(value.fields['value'])


def _optional_list(values: list[Any]) -> ObjectValue:
    if not values:
        return ObjectValue('None', {})
    return ObjectValue('Some', {'value': ListValue(values, runtime_rank=1)})


def _new_config(old: ObjectValue, *, routes: list[Any] | None = None,
                middleware: list[Any] | None = None) -> ObjectValue:
    fields = {
        'routes': _optional_list(routes if routes is not None else
                                 [_duplicate_occurrence(route) for route in _optional_contents(old.fields['routes'])]),
        'middleware': _optional_list(middleware if middleware is not None else
                                     [_duplicate_occurrence(cb) for cb in _optional_contents(old.fields['middleware'])]),
    }
    if old.type_name == 'web.App':
        fields['logger'] = _duplicate_occurrence(old.fields.get('logger', ObjectValue('None', {})))
        fields['errorHandler'] = _duplicate_occurrence(old.fields.get('errorHandler', ObjectValue('None', {})))
    return ObjectValue(old.type_name, fields)


def _install_route(args: tuple[Any, ...], kind: str) -> tuple[Any, ...]:
    # Modifier callbacks arrive last at runtime, even when their nominal
    # parameter appears before the trailing optional arguments.
    is_body_route = kind in ('Post', 'Put', 'Patch', 'Query')
    if is_body_route:
        target, path, mode, limit, handler = args
    else:
        target, path, handler = args
    core._validate_template(path)
    body_mode = 'Buffered'
    max_body_bytes: int | None = None
    if is_body_route:
        body_mode = mode.type_name.rsplit('.', 1)[-1]
        raw_limit = _optional_scalar(limit)
        max_body_bytes = int(raw_limit) if raw_limit is not None else None
        # Validate using the same rules as the transport-independent core.
        core.Router().route(kind.upper(), path, lambda _: None,
                            body_mode=body_mode, max_body_bytes=max_body_bytes)
    old = [_duplicate_occurrence(route) for route in _optional_contents(target.fields['routes'])]
    fields = {
        'path': path, 'handler': _duplicate_occurrence(handler),
        'bodyMode': body_mode, 'middleware': ObjectValue('None', {}),
    }
    if kind in ('Post', 'Put', 'Patch', 'Query'):
        fields['maxBodyBytes'] = _duplicate_occurrence(limit)
    route = ObjectValue(f'web.Route.{kind}', fields)
    return (_new_config(target, routes=[*old, route]),)


_ROUTE_DOCS = {
    lower: element_documentation(
        f'Register a method-specific {lower.upper()} handler on an immutable web configuration.',
        parameters=(('target', 'Application or reusable Router.'),
                    ('path', 'Absolute route template.'),
                    ('handler', 'Typed request callback returning text or Response.')),
        returns='A new App or Router; the original remains unchanged.',
        category='Web',
        examples=((f'import {{std.web, std.web.[{lower}]}}\n'
                   f'$app = web.App\n'
                   f'$app := {lower}("/"): fn (req: web.{title}Request) => "ok" end', None),),
    )
    for lower, title in _METHODS
}

_DEFAULT_ROUTE_MODE = (ElementNode(Symbol('Buffered', ('web', 'BodyMode'))),)
_DEFAULT_ROUTE_LIMIT = (ElementNode(Symbol('nativeCookieNoneInt', ('std', 'web'))),)

for _lower, _title in _METHODS:
    for _target_type, _suffix in ((_APP, 'app'), (_ROUTER, 'router')):
        for _output, _output_name in (
            (T.String, 'text'), (_RESPONSE, 'response'), (_HTML_NODE, 'html'),
            (_ERROR, 'error'),
            (T.Result(T.String, _ERROR), 'text_result'),
            (T.Result(_RESPONSE, _ERROR), 'response_result'),
            (T.Result(_HTML_NODE, _ERROR), 'html_result'),
            (_VALUE_ERROR, 'value_error'),
            (T.Result(T.String, _VALUE_ERROR), 'text_value_error'),
            (T.Result(_RESPONSE, _VALUE_ERROR), 'response_value_error'),
            (T.Result(_HTML_NODE, _VALUE_ERROR), 'html_value_error'),
            (T.Result(T.String, T.U(_ERROR, _VALUE_ERROR)), 'text_both_errors'),
            (T.Result(_RESPONSE, T.U(_ERROR, _VALUE_ERROR)), 'response_both_errors'),
            (T.Result(_HTML_NODE, T.U(_ERROR, _VALUE_ERROR)), 'html_both_errors'),
        ):
            # Valiance function outputs are not covariant: publish two normal
            # same-arity overloads instead of pretending String is Function's
            # covariant Result output.
            def _register(args: tuple[Any, ...], ctx: RuntimeContext,
                          kind: str = _title) -> tuple[Any, ...]:
                del ctx
                return _install_route(args, kind)

            _register.__name__ = f'_{_lower}_{_suffix}_{_output_name}'
            _body_verb = _title in ('Post', 'Put', 'Patch', 'Query')
            _params = (_target_type, T.String,
                       T.Fn((_nominal(f'{_title}Request'),), (_output,)))
            if _body_verb:
                _params += (_BODY_MODE, T.optional(T.Int))
            _register = stdlib_element(
                _lower, _params,
                (_target_type,),
                param_names=(('target', 'path', 'handler', 'bodyMode', 'maxBodyBytes')
                             if _body_verb else ('target', 'path', 'handler')),
                param_defaults=((None, None, None, _DEFAULT_ROUTE_MODE, _DEFAULT_ROUTE_LIMIT)
                                if _body_verb else None),
                documentation=_ROUTE_DOCS[_lower],
                import_with=('App',) if _suffix == 'app' else ('Router',),
            )(_register)
            globals()[_register.__name__] = _register
del _register


_DEFAULT_WS_POLICY = (ElementNode(Symbol('WebSocketPolicy', ('web',))),)
for _target_type, _label in ((_APP, 'app'), (_ROUTER, 'router')):
    for _output_type, _output_name in ((T.N(Symbol('None')), 'none'), (_ERROR, 'error'),
                                      (T.Result(T.N(Symbol('None')), _ERROR), 'result')):
        def _register_ws(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
            del ctx
            target, path, policy, handler = args
            from valiance.std import _web_ws
            config = _core_ws_policy(policy)
            core.Router().websocket(path, lambda _: None, config)
            existing = [_duplicate_occurrence(r) for r in _optional_contents(target.fields['routes'])]
            route = ObjectValue('web.Route.Websocket', {
                'path': path, 'handler': _duplicate_occurrence(handler),
                'policy': _duplicate_occurrence(policy), 'middleware': ObjectValue('None', {}),
            })
            return (_new_config(target, routes=[*existing, route]),)

        _register_ws.__name__ = f'_websocket_{_label}_{_output_name}'
        _register_ws = stdlib_element(
            'websocket', (_target_type, T.String,
                          T.Fn((_WEBSOCKET,), (_output_type,)), _WEBSOCKET_POLICY),
            (_target_type,),
            param_names=('target', 'path', 'handler', 'policy'),
            param_defaults=(None, None, None, _DEFAULT_WS_POLICY),
            import_with=('App',) if _label == 'app' else ('Router',),
            documentation=element_documentation(
                'Register a WebSocket upgrade route separate from ordinary GET.',
                category='Web', returns='Immutable App or Router.'),
        )(_register_ws)
        globals()[_register_ws.__name__] = _register_ws

del _register_ws


def _core_ws_policy(obj: ObjectValue):
    from valiance.std._web_ws import WebSocketPolicy
    return WebSocketPolicy(
        allowed_origins=tuple(_optional_contents(obj.fields['allowedOrigins'])),
        subprotocols=tuple(_optional_contents(obj.fields['subprotocols'])),
        require_subprotocol=bool(int(obj.fields['requireSubprotocol'])),
        max_frame_bytes=int(obj.fields['maxFrameBytes']),
        max_message_bytes=int(obj.fields['maxMessageBytes']),
        idle_timeout_seconds=int(obj.fields['idleTimeoutSeconds']),
        write_timeout_seconds=int(obj.fields['writeTimeoutSeconds']),
        heartbeat_interval_seconds=int(obj.fields['heartbeatIntervalSeconds']),
        pong_timeout_seconds=int(obj.fields['pongTimeoutSeconds']),
        extension_policy=obj.fields.get('extensionPolicy', 'Disabled'))


# The structural record in the normative middleware contract is a regular
# Valiance record. It is not a special "next" callback or a new syntax feature.
_AFTER_RECORD = T.RecordType(T.N(Symbol('record')), (
    T.Field(Symbol('request'), _REQUEST),
    T.Field(Symbol('after'), T.Fn((_RESPONSE,), (_RESPONSE,))),
))
_AFTER_ERROR_RECORD = T.RecordType(T.N(Symbol('record')), (
    T.Field(Symbol('request'), _REQUEST),
    T.Field(Symbol('after'), T.Fn((_RESPONSE,), (T.Result(_RESPONSE, _ERROR),))),
))

for _target_type, _label in ((_APP, 'app'), (_ROUTER, 'router')):
    for _return_type, _result_label in (
        (_REQUEST, 'request'), (_RESPONSE, 'response'), (_ERROR, 'error'),
        (T.Result(_REQUEST, _ERROR), 'request_result'),
        (T.Result(_RESPONSE, _ERROR), 'response_result'),
        (_AFTER_RECORD, 'after_record'),
        (_AFTER_ERROR_RECORD, 'after_error_record'),
    ):
        def _register_middleware(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
            del ctx
            target, callback = args
            old = [_duplicate_occurrence(item) for item in _optional_contents(target.fields['middleware'])]
            return (_new_config(target, middleware=[*old, _duplicate_occurrence(callback)]),)

        _register_middleware.__name__ = f'_{_label}_middleware_{_result_label}'
        _register_middleware = stdlib_element(
            'middleware',
            (_target_type, T.Fn((_REQUEST,), (_return_type,))),
            (_target_type,),
            param_names=('target', 'callback'),
            import_with=('App',) if _label == 'app' else ('Router',),
            documentation=element_documentation(
                'Register before middleware, optionally returning a {request, after} record '
                'whose response callback runs in reverse order.',
                category='Web', returns='New immutable App or Router.',
                examples=(('import {std.web}\n$app = web.App\n'
                           '$app := web.middleware: fn(req: web.Request) => $req end', None),)),
        )(_register_middleware)
        globals()[_register_middleware.__name__] = _register_middleware
del _register_middleware





@stdlib_element('cors', (_CORS_POLICY,), (_CORS_MIDDLEWARE,),
                param_names=('policy',),
                documentation=element_documentation(
                    'Create validated, opt-in CORS middleware for App/Router registration.',
                    category='Web', returns='CORS middleware policy value.',
                    examples=(('import {std.web, std.web.App}\n$app = App\n$app := middleware web.cors(web.CorsPolicy(allowedOrigins = [\"https://example.com\"], allowedMethods = [web.Method.GET]))', None),)))
def _cors_factory(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    # Validate at app construction, not on the first incoming request.
    _core_cors_policy(args[0])
    return (ObjectValue('web.CorsMiddleware', {'policy': _duplicate_occurrence(args[0])}),)


def _core_cors_policy(value: ObjectValue) -> core.CorsPolicy:
    if not isinstance(value, ObjectValue) or value.type_name != 'web.CorsPolicy':
        raise core.ConfigError('Expected web.CorsPolicy')
    def names(field: str) -> tuple[str, ...]:
        raw = value.fields[field]
        return tuple(_optional_contents(raw)) if isinstance(raw, ObjectValue) else tuple(raw)
    def truth(field: str) -> bool:
        number = value.fields[field]
        number = int(number.value if isinstance(number, TaggedValue) else number)
        if number not in (0, 1):
            raise core.ConfigError('CORS boolean policy flags must be true/false or 0/1')
        return bool(number)
    methods = tuple(item.type_name.rsplit('.', 1)[-1].upper()
                    for item in value.fields['allowedMethods'])
    optional_age = value.fields['maxAge']
    if isinstance(optional_age, ObjectValue) and optional_age.type_name == 'None':
        age = None
    elif isinstance(optional_age, ObjectValue) and optional_age.type_name == 'Some':
        age = int(optional_age.fields['value'])
    else:
        age = int(optional_age)
    return core.CorsPolicy(names('allowedOrigins'), truth('allowCredentials'), methods,
                           names('allowedHeaders'), names('exposedHeaders'), age,
                           truth('allowPrivateNetwork'))


def _core_tls(config: ObjectValue) -> object:
    from valiance.std import _web_http1
    option = config.fields.get('tls', ObjectValue('None', {}))
    if isinstance(option, ObjectValue) and option.type_name == 'None':
        return None
    value = option.fields['value'] if isinstance(option, ObjectValue) and option.type_name == 'Some' else option
    if not isinstance(value, ObjectValue) or value.type_name != 'web.TLS':
        raise core.ConfigError('Expected web.TLS or None')
    return _web_http1.TLSConfig(value.fields['certificateFile'], value.fields['privateKeyFile'])


def _core_protocols(config: ObjectValue) -> tuple[str, ...] | None:
    """Map typed protocol selection; an unavailable transport must fail start."""
    selection = config.fields.get('protocols', ObjectValue('None', {}))
    if isinstance(selection, ObjectValue) and selection.type_name == 'None':
        return None
    if isinstance(selection, (list, tuple, ListValue)):
        entries = selection
    else:
        try:
            entries = _optional_contents(selection)
        except (TypeError, KeyError) as exc:
            raise core.ConfigError('Expected an optional Protocol list') from exc
    mapping = {'HTTP1': 'HTTP/1.1', 'HTTP2': 'HTTP/2', 'HTTP3': 'HTTP/3'}
    result = []
    for entry in entries:
        if not isinstance(entry, ObjectValue):
            raise core.ConfigError('Expected Protocol variant')
        name = entry.type_name.rsplit('.', 1)[-1]
        if name not in mapping:
            raise core.ConfigError('Unsupported transport protocol')
        result.append(mapping[name])
    if not result or len(set(result)) != len(result):
        raise core.ConfigError('Empty or duplicate listener protocol set')
    return tuple(result)


def _core_network_policy(config: ObjectValue) -> core.NetworkPolicy:
    """Validate the public host/proxy configuration before listener binding."""
    def strings(name: str) -> tuple[str, ...]:
        option = config.fields.get(name, ObjectValue('None', {}))
        if isinstance(option, ObjectValue) and option.type_name == 'None':
            return ()
        if isinstance(option, (list, tuple, ListValue)):
            return tuple(option)
        try:
            return tuple(_optional_contents(option))
        except (TypeError, KeyError) as exc:
            raise core.ConfigError('Expected optional string list for ' + name) from exc

    origin = config.fields.get('publicOrigin', ObjectValue('None', {}))
    if isinstance(origin, ObjectValue) and origin.type_name == 'None':
        origin_value = None
    elif isinstance(origin, ObjectValue) and origin.type_name == 'Some':
        origin_value = origin.fields['value']
    else:
        origin_value = origin
    proxy_mode = config.fields.get('proxyMode', ObjectValue('web.ProxyMode.Disabled', {}))
    if not isinstance(proxy_mode, ObjectValue):
        raise core.ConfigError('Expected optional ProxyMode')
    if proxy_mode.type_name == 'None':
        mode = 'Disabled'
    elif proxy_mode.type_name == 'Some':
        mode = proxy_mode.fields['value'].type_name.rsplit('.', 1)[-1]
    else:
        mode = proxy_mode.type_name.rsplit('.', 1)[-1]
    return core.NetworkPolicy(
        allowed_hosts=strings('allowedHosts'),
        trusted_proxies=strings('trustedProxies'),
        public_origin=origin_value,
        proxy_mode=mode)


def _core_limits(config: ObjectValue) -> object:
    """Resolve validated public Valiance limits to the HTTP/1.1 adapter."""
    from valiance.std import _web_http1
    option = config.fields.get('limits', ObjectValue('None', {}))
    if isinstance(option, ObjectValue) and option.type_name == 'None':
        return _web_http1.Limits()
    value = (option.fields['value'] if isinstance(option, ObjectValue)
             and option.type_name == 'Some' else option)
    if not isinstance(value, ObjectValue) or value.type_name != 'web.Limits':
        raise core.ConfigError('Expected web.Limits or None')
    f = value.fields
    return _web_http1.Limits(
        max_request_target_bytes=int(f['maxRequestTargetBytes']),
        max_header_bytes=int(f['maxHeaderBytes']),
        max_header_fields=int(f['maxHeaderFields']),
        max_decoded_header_bytes=int(f['maxDecodedHeaderBytes']),
        max_compressed_header_bytes=int(f['maxCompressedHeaderBytes']),
        max_hpack_dynamic_table_bytes=int(f['maxHpackDynamicTableBytes']),
        max_buffered_body_bytes=int(f['maxBufferedBodyBytes']),
        max_streaming_body_bytes=int(f['maxStreamingBodyBytes']),
        max_form_fields=int(f['maxFormFields']),
        max_multipart_parts=int(f['maxMultipartParts']),
        max_upload_file_bytes=int(f['maxUploadFileBytes']),
        max_multipart_field_bytes=int(f['maxMultipartFieldBytes']),
        max_multipart_name_bytes=int(f['maxMultipartNameBytes']),
        max_multipart_filename_bytes=int(f['maxMultipartFilenameBytes']),
        max_connections=int(f['maxConnections']),
        max_concurrent_requests=int(f['maxConcurrentRequests']),
        max_active_websockets=int(f['maxActiveWebSockets']),
        max_websocket_frame_bytes=int(f['maxWebSocketFrameBytes']),
        max_websocket_messages_bytes=int(f['maxWebSocketMessagesBytes']),
        max_websocket_queued_send_bytes=int(f['maxWebSocketQueuedSendBytes']),
        web_socket_ping_interval_seconds=int(f.get('webSocketPingIntervalSeconds', 30)),
        web_socket_pong_timeout_seconds=int(f.get('webSocketPongTimeoutSeconds', 10)),
        max_total_buffered_bytes=int(f['maxTotalBufferedBytes']),
        max_buffered_bytes_per_connection=int(f['maxBufferedBytesPerConnection']),
        max_concurrent_streams_per_connection=int(f['maxConcurrentStreamsPerConnection']),
        header_timeout_seconds=int(f['headerTimeoutMilliseconds']) / 1000,
        body_idle_timeout_seconds=int(f['bodyIdleTimeoutMilliseconds']) / 1000,
        connection_idle_timeout_seconds=int(f['connectionIdleTimeoutMilliseconds']) / 1000,
        write_idle_timeout_seconds=int(f['writeIdleTimeoutMilliseconds']) / 1000,
        graceful_shutdown_seconds=int(f['gracefulShutdownMilliseconds']) / 1000,
    )


for _target_type, _suffix in ((_APP, 'app'), (_ROUTER, 'router')):
    def _register_cors(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        target, callback = args
        old = [_duplicate_occurrence(item) for item in _optional_contents(target.fields['middleware'])]
        return (_new_config(target, middleware=[*old, _duplicate_occurrence(callback)]),)
    _register_cors.__name__ = f'_middleware_cors_{_suffix}'
    _register_cors = stdlib_element(
        'middleware', (_target_type, _CORS_MIDDLEWARE), (_target_type,),
        param_names=('target', 'callback'),
        import_with=('App',) if _suffix == 'app' else ('Router',),
        documentation=element_documentation(
            'Register validated CORS middleware on an immutable App or Router.',
            category='Web', returns='Updated App or Router.'),
    )(_register_cors)
    globals()[_register_cors.__name__] = _register_cors
del _register_cors


# Compression is another normal after-middleware, with an opaque native
# registration value until the compiler supports callable middleware factories.
def _core_compression_policy(value: ObjectValue) -> core.CompressionPolicy:
    if not isinstance(value, ObjectValue) or value.type_name != 'web.CompressionPolicy':
        raise core.ConfigError('Expected web.CompressionPolicy')
    def flag(field: str) -> bool:
        val = value.fields[field]
        val = int(val.value if isinstance(val, TaggedValue) else val)
        if val not in (0, 1):
            raise core.ConfigError('Compression flags must be boolean values')
        return bool(val)
    return core.CompressionPolicy(int(value.fields['minBytes']), flag('gzipEnabled'),
                                  flag('brotliEnabled'), int(value.fields['maxBytes']))


@stdlib_element('compression', (_COMPRESSION_POLICY,), (_COMPRESSION_MIDDLEWARE,),
                param_names=('policy',),
                documentation=element_documentation(
                    'Configure opt-in, bounded gzip/Brotli response compression.',
                    category='Web', returns='Validated compression middleware.',
                    examples=(('import {std.web, std.web.App}\n$app = App\n'
                               '$app := middleware web.compression(web.CompressionPolicy)', None),)))
def _compression_factory(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    _core_compression_policy(args[0])
    return (ObjectValue('web.CompressionMiddleware', {'policy': _duplicate_occurrence(args[0])}),)


for _target_type, _suffix in ((_APP, 'app'), (_ROUTER, 'router')):
    def _register_compression(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        target, callback = args
        old = [_duplicate_occurrence(item) for item in _optional_contents(target.fields['middleware'])]
        return (_new_config(target, middleware=[*old, _duplicate_occurrence(callback)]),)
    _register_compression.__name__ = f'_middleware_compression_{_suffix}'
    _register_compression = stdlib_element(
        'middleware', (_target_type, _COMPRESSION_MIDDLEWARE), (_target_type,),
        param_names=('target', 'callback'),
        import_with=('App',) if _suffix == 'app' else ('Router',),
        documentation=element_documentation(
            'Register opt-in response compression on App or Router.',
            category='Web', returns='New immutable App or Router.'),
    )(_register_compression)
    globals()[_register_compression.__name__] = _register_compression
del _register_compression


# Mounting is a pure composition operation.  The router's own middleware is
# captured for each mounted route so it remains scoped, not app-global.
def _mount_router(args: tuple[Any, ...], ctx: RuntimeContext, *, builder: bool) -> tuple[Any, ...]:
    target, prefix, router = args
    core._validate_template(prefix)
    if builder:
        outputs = ctx.call(router, [ObjectValue('web.Router', {
            'routes': ObjectValue('None', {}),
            'middleware': ObjectValue('None', {}),
        })])
        if len(outputs) != 1:
            raise core.ConfigError('Group builder must return one Router')
        router, = outputs
    if not isinstance(router, ObjectValue) or router.type_name != 'web.Router':
        raise core.ConfigError('Group content must be a Router')
    new_routes = [_duplicate_occurrence(route) for route in _optional_contents(target.fields['routes'])]
    group_middleware = _optional_contents(router.fields['middleware'])
    for route in _optional_contents(router.fields['routes']):
        path = prefix.rstrip('/') + (route.fields['path'] if route.fields['path'] != '/' else '')
        path = path or '/'
        core._validate_template(path)
        scoped = [_duplicate_occurrence(cb) for cb in group_middleware]
        scoped.extend(_duplicate_occurrence(cb) for cb in _optional_contents(route.fields['middleware']))
        fields = {
            'path': path,
            'bodyMode': route.fields.get('bodyMode', 'Buffered'),
            'handler': _duplicate_occurrence(route.fields['handler']),
            'middleware': _optional_list(scoped),
        }
        if 'maxBodyBytes' in route.fields:
            fields['maxBodyBytes'] = _duplicate_occurrence(route.fields['maxBodyBytes'])
        new_routes.append(ObjectValue(route.type_name, fields))
    return (_new_config(target, routes=new_routes),)


for _target, _kind in ((_APP, 'app'), (_ROUTER, 'router')):
    for _is_builder, _last_type, _suffix in (
        (False, _ROUTER, 'value'),
        (True, T.Fn((_ROUTER,), (_ROUTER,)), 'builder'),
    ):
        def _register_group(args: tuple[Any, ...], ctx: RuntimeContext,
                            is_builder: bool = _is_builder) -> tuple[Any, ...]:
            return _mount_router(args, ctx, builder=is_builder)

        _register_group.__name__ = f'_group_{_kind}_{_suffix}'
        _register_group = stdlib_element(
            'group', (_target, T.String, _last_type), (_target,),
            param_names=('target', 'prefix', 'router'),
            import_with=('App',) if _kind == 'app' else ('Router',),
            documentation=element_documentation(
                'Mount a reusable Router at a path prefix, with scoped router middleware.',
                category='Web', returns='New immutable App or Router.',
                examples=(('import {std.web}\nweb.group(web.App, "/api", web.Router)', None),)),
        )(_register_group)
        globals()[_register_group.__name__] = _register_group
del _register_group

def _wrap_params(params: core.Params) -> ObjectValue:
    return ObjectValue('web.Params', {
        'names': _optional_list(list(name for name, _ in params.pairs)),
        'values': _optional_list(list(value for _, value in params.pairs)),
    })


def _unwrap_params(value: ObjectValue) -> core.Params:
    return core.Params(tuple(zip(_optional_contents(value.fields['names']),
                                 _optional_contents(value.fields['values']), strict=True)))


def _wrap_headers(headers: core.Headers) -> ObjectValue:
    return ObjectValue('web.Headers', {
        'names': _optional_list([key for key, _ in headers.pairs]),
        'values': _optional_list([val for _, val in headers.pairs]),
    })


def _unwrap_headers(value: Any) -> core.Headers:
    if isinstance(value, ObjectValue) and value.type_name == 'None':
        return core.Headers()
    if isinstance(value, ObjectValue) and value.type_name == 'Some':
        value = value.fields['value']
    return core.Headers(tuple(zip(_optional_contents(value.fields['names']),
                                  _optional_contents(value.fields['values']), strict=True)))


# Request streams have a request-lifetime token, not a replayable byte body.
# Leases are dropped on EOF, explicit close, or HTTP request cleanup.
_REQUEST_STREAM_LEASES: dict[int, core.RequestBodyStream] = {}
# EOF closes an owned stream, but its request may still be executing and is
# entitled to inspect validated trailing fields. Weak leases do not retain a
# completed request past its real application lifetime.
_REQUEST_COMPLETED_STREAMS: WeakValueDictionary[int, core.RequestBodyStream] = WeakValueDictionary()
_REQUEST_STREAM_LOCK = RLock()
_REQUEST_BODY_RUNTIME = ObjectRuntimeType(
    destructor_name='web.RequestBodyStream::~RequestBodyStream',
    dup_error='A request body stream cannot be duplicated',
    field_order=('token',),
)


def _request_token(stream: core.RequestBodyStream | None) -> int:
    if stream is None:
        return 0
    with _REQUEST_STREAM_LOCK:
        token = getattr(stream, '_valiance_token', 0)
        if token and _REQUEST_STREAM_LEASES.get(token) is stream:
            return token
        token = randbits(112) or 1
        while token in _REQUEST_STREAM_LEASES:
            token = randbits(112) or 1
        _REQUEST_STREAM_LEASES[token] = stream
        stream._valiance_token = token
        previous_close = getattr(stream, '_on_close', None)
        stream_ref = ref(stream)
        def on_close():
            current = stream_ref()
            with _REQUEST_STREAM_LOCK:
                _REQUEST_STREAM_LEASES.pop(token, None)
                if current is not None and current.complete:
                    _REQUEST_COMPLETED_STREAMS[token] = current
            if previous_close is not None:
                previous_close()
        stream._on_close = on_close
        return token


def _stream_for_token(token: int) -> core.RequestBodyStream | None:
    if not token:
        return None
    with _REQUEST_STREAM_LOCK:
        return (_REQUEST_STREAM_LEASES.get(token) or
                _REQUEST_COMPLETED_STREAMS.get(token))


def _context_snapshot(request: core.Request) -> DictValue:
    # Store the immutable request-local snapshot directly in the VM value.
    # No global registry, leaked lease, or easily forgeable string-name key.
    return DictValue({key: _duplicate_occurrence(value)
                      for key, value in request.context.items()})


def _wrap_request(request: core.Request, *, concrete: bool = False) -> ObjectValue:
    # Route captures are an immutable String -> String dictionary with no
    # interpretation as query fields.
    name = ('WebSocketRequest' if request.method == 'WEBSOCKET' else
            f'{request.method.capitalize()}Request')
    return ObjectValue(f'web.{name}' if concrete else f'web.Request.{name}', {
        'path': request.path,
        'rawTarget': request.raw_target,
        'originalMethod': request.original_method,
        'routeParams': DictValue(dict(request.route_params)),
        'query': _wrap_params(request.query),
        'headers': _wrap_headers(request.headers),
        'body': request.body.decode('utf-8', errors='surrogateescape'),
        'streamToken': RuntimeNumber(_request_token(request.body_stream)),
        'multipartPolicy': DictValue({name: RuntimeNumber(value)
                                      for name, value in vars(request.multipart_limits).items()}),
        'scheme': request.scheme, 'authority': request.authority,
        'protocol': request.protocol, 'requestId': request.request_id,
        'peer': request.peer, 'clientAddress': request.client_address,
        '__contextValues': _context_snapshot(request),
    })


def _unwrap_request(value: ObjectValue) -> core.Request:
    if not isinstance(value, ObjectValue) or not value.type_name.startswith('web.Request.'):
        raise TypeError('Expected web.Request')
    kind = value.type_name.rsplit('.', 1)[-1]
    method = ('WEBSOCKET' if kind == 'WebSocketRequest'
              else kind.removesuffix('Request').upper())
    original = value.fields['originalMethod']
    return core.Request(method, value.fields['path'], value.fields['rawTarget'],
                        _unwrap_params(value.fields['query']),
                        _unwrap_headers(value.fields['headers']), original,
                        dict(value.fields['routeParams']),
                        value.fields['body'].encode('utf-8', errors='surrogateescape'),
                        body_stream=_stream_for_token(int(value.fields.get('streamToken', 0))),
                        scheme=value.fields['scheme'], authority=value.fields['authority'],
                        protocol=value.fields['protocol'], request_id=value.fields['requestId'],
                        peer=value.fields.get('peer', ''),
                        client_address=value.fields.get('clientAddress', ''),
                        multipart_limits=core.MultipartLimits(
                            **{name: int(number) for name, number in
                               value.fields['multipartPolicy'].items()}),
                        context=dict(value.fields.get('__contextValues', {})))


# Cryptographically authenticated capabilities, not a registry of values or
# keys. In particular, the public generic object constructor alone cannot
# create a key that can access a request's context entries.
_CONTEXT_KEY_SECRET = token_bytes(32)


def _context_key_mac(token: int) -> int:
    data = token.to_bytes(16, 'big')
    return int.from_bytes(hmac.new(_CONTEXT_KEY_SECRET, data, hashlib.sha256).digest()[:16], 'big')


def _context_key_token(obj: ObjectValue) -> int:
    if not isinstance(obj, ObjectValue) or obj.type_name != 'web.ContextKey':
        raise core.WebError('MissingContext', 'Expected a typed web.ContextKey')
    try:
        token = int(obj.fields['token'])
        authenticator = int(obj.fields['authenticator'])
    except (KeyError, TypeError, ValueError) as exc:
        raise core.WebError('MissingContext', 'Invalid context key') from exc
    if token <= 0 or token.bit_length() > 112 or authenticator < 0 or authenticator.bit_length() > 128:
        raise core.WebError('MissingContext', 'Invalid context key')
    expected = _context_key_mac(token).to_bytes(16, 'big')
    received = authenticator.to_bytes(16, 'big')
    if not hmac.compare_digest(expected, received):
        raise core.WebError('MissingContext', 'Unauthenticated context key')
    return token


@stdlib_element('nativeContextNonce', (), (T.Int,), exported=False)
def _mint_context_nonce(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (RuntimeNumber(randbits(112) or 1),)


@stdlib_element('nativeContextAuthenticator', (T.Int,), (T.Int,), exported=False)
def _mint_context_authenticator(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (RuntimeNumber(_context_key_mac(int(args[0]))),)


@stdlib_element('contextKey', (), (T.N(Symbol('ContextKey', ('web',)), T.V('T')),),
                param_names=(),
                documentation=element_documentation(
                    'Mint a unique, copyable typed request-context key.', category='Web',
                    returns='An opaque ContextKey[T] with unique identity.'))
def _new_context_key(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args
    nonce = randbits(112) or 1
    return (ObjectValue('web.ContextKey', {
        'token': RuntimeNumber(nonce),
        'authenticator': RuntimeNumber(_context_key_mac(nonce)),
    }, type_args=ctx.type_args),)




def _context_items(req: ObjectValue) -> dict[object, object]:
    if not isinstance(req, ObjectValue) or not (
            req.type_name.startswith('web.Request.') or
            req.type_name.endswith('Request') and req.type_name.startswith('web.')):
        raise core.WebError('MissingContext', 'Expected web request')
    return dict(req.fields.get('__contextValues', {}))


def _new_context_request(req: ObjectValue, items: dict[object, object]) -> ObjectValue:
    # A new request value has its own fields. The application-facing request's
    # original identity, framing tokens and route captures are not changed.
    fields = {name: _duplicate_occurrence(value) for name, value in req.fields.items()
              if name != '__contextValues'}
    fields['__contextValues'] = DictValue(items)
    return ObjectValue(req.type_name, fields, type_args=req.type_args)


_REQ_CONTEXT_TYPES = ((_REQUEST, 'Request'),
                      *[( _nominal(f'{name}Request'), f'{name}Request')
                        for name in ('Get', 'Head', 'Post', 'Put', 'Patch',
                                     'Delete', 'Options', 'Query', 'WebSocket')])
for _ctx_type, _ctx_name in _REQ_CONTEXT_TYPES:
    def _with_context(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        req, key, value = args
        token = _context_key_token(key)
        items = {name: _duplicate_occurrence(item)
                 for name, item in _context_items(req).items()}
        # Explicitly require duplication to be legal: a resource with @nodup
        # ownership cannot be smuggled into copyable Request metadata.
        items[token] = _duplicate_occurrence(value)
        return (_new_context_request(req, items),)

    _with_context.__name__ = f'_with_context_{_ctx_name}'
    _with_context = stdlib_element(
        'withContext', (_ctx_type, T.N(Symbol('ContextKey', ('web',)), T.V('T')), T.V('T')),
        (_ctx_type,), param_names=('request', 'key', 'value'),
        documentation=element_documentation(
            'Return the same request variant with one typed context value attached.',
            category='Web', returns='An immutable enriched request.'))(_with_context)
    globals()[_with_context.__name__] = _with_context

    def _get_context(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        req, key = args
        items = _context_items(req)
        token = _context_key_token(key)
        return ((ObjectValue('Some', {'value': _duplicate_occurrence(items[token])})
                 if token in items else ObjectValue('None', {})),)

    _get_context.__name__ = f'_get_context_{_ctx_name}'
    _get_context = stdlib_element(
        'context', (_ctx_type, T.N(Symbol('ContextKey', ('web',)), T.V('T'))),
        (T.optional(T.V('T')),), param_names=('request', 'key'),
        documentation=element_documentation(
            'Read a typed context value, returning None when the key is absent.',
            category='Web', returns='An optional value of the key type.'))(_get_context)
    globals()[_get_context.__name__] = _get_context

    def _require_context(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        req, key = args
        items = _context_items(req)
        token = _context_key_token(key)
        return ((_duplicate_occurrence(items[token]) if token in items else
                 ObjectValue('web.Error.MissingContext', {'message': 'Required request context is missing'})),)

    _require_context.__name__ = f'_require_context_{_ctx_name}'
    _require_context = stdlib_element(
        'requireContext', (_ctx_type, T.N(Symbol('ContextKey', ('web',)), T.V('T'))),
        (T.Result(T.V('T'), _ERROR),), param_names=('request', 'key'),
        documentation=element_documentation(
            'Require a typed context value or return Error.MissingContext.',
            category='Web', returns='The keyed payload or MissingContext.'))(_require_context)
    globals()[_require_context.__name__] = _require_context
del _with_context, _get_context, _require_context

def _wrap_bytes(value: bytes) -> TaggedValue:
    """Retain exact octets (including invalid UTF-8 and NUL) across the VM boundary."""
    return TaggedValue(
        ListValue([RuntimeNumber(byte) for byte in value], runtime_rank=1), _BYTE_TAGS)


def _unwrap_bytes(value: TaggedValue | ListValue) -> bytes:
    # Native argument conversion may erase tag *evidence* after static checking.
    # Stored Response fields keep evidence; both paths still verify byte ranges.
    if isinstance(value, TaggedValue):
        if not _BYTE_TAGS.issubset(value.tags):
            raise core.WebError('InvalidResponse', 'Expected byte-tagged response body')
    elif not isinstance(value, ListValue):
        raise core.WebError('InvalidResponse', 'Expected a byte-list body')
    try:
        octets = tuple(value)
        if any(not isinstance(item, (int, RuntimeNumber)) or
               item != int(item) or not 0 <= int(item) <= 255 for item in octets):
            raise ValueError('Response body contains a non-byte value')
        return bytes(int(item) for item in octets)
    except (ValueError, TypeError) as exc:
        raise core.WebError('InvalidResponse', 'Invalid byte response body') from exc


# Each @nodup StreamBody is represented by a single token. Converting to a
# core Response transfers ownership out of this registry. A VM destructor that
# observes an already transferred token must be a no-op.
_STREAM_LEASES: dict[int, core.StreamBody] = {}
_STREAM_LEASES_LOCK = RLock()
_STREAM_BODY_RUNTIME = ObjectRuntimeType(
    destructor_name='web.StreamBody::~StreamBody',
    dup_error='A web.StreamBody owns its source and cannot be duplicated',
    field_order=('token',),
)


def _lease_stream(body: core.StreamBody) -> ObjectValue:
    with _STREAM_LEASES_LOCK:
        token = randbits(112) or 1
        while token in _STREAM_LEASES:
            token = randbits(112) or 1
        _STREAM_LEASES[token] = body
    return ObjectValue('web.StreamBody', {'token': RuntimeNumber(token)},
                       runtime_type=_STREAM_BODY_RUNTIME)


def _claim_stream(obj: ObjectValue) -> core.StreamBody:
    if not isinstance(obj, ObjectValue) or obj.type_name != 'web.StreamBody':
        raise core.WebError('InvalidResponse', 'Expected a StreamBody')
    with _STREAM_LEASES_LOCK:
        body = _STREAM_LEASES.pop(int(obj.fields['token']), None)
    if body is None:
        raise core.WebError('BodyUnavailable', 'Stream already transferred or closed')
    return body


@stdlib_element('nativeCloseStream', (T.Int,), (), exported=False,
                param_names=('token',))
def _native_close_stream(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    with _STREAM_LEASES_LOCK:
        body = _STREAM_LEASES.pop(int(args[0]), None)
    if body is not None:
        body.close()
    return ()


class _VMByteSource:
    def __init__(self, source: ObjectValue, ctx: RuntimeContext):
        self.read_callback = _duplicate_occurrence(source.fields['read'])
        self.close_callback = _duplicate_occurrence(source.fields['close'])
        self.ctx = ctx
        self.closed = False

    def read(self, max_bytes: int) -> bytes | None:
        with _STREAM_CALLBACK_GATE:
            result = self.ctx.call(self.read_callback, [RuntimeNumber(max_bytes)])
        if len(result) != 1:
            raise core.WebError('InvalidResponse', 'ByteSource.read must return one value')
        data = result[0]
        if isinstance(data, ObjectValue) and data.type_name.startswith('web.Error.'):
            raise _unpack_web_error(data)
        if isinstance(data, ObjectValue) and data.type_name == 'None':
            return None
        if isinstance(data, (ListValue, TaggedValue)):
            return _unwrap_bytes(data)
        raise core.WebError('InvalidResponse', 'ByteSource.read must return bytes or None')

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            with _STREAM_CALLBACK_GATE:
                result = self.ctx.call(self.close_callback, [])
            if len(result) != 1 or not isinstance(result[0], ObjectValue):
                raise core.WebError('InvalidResponse', 'ByteSource.close must return None or Err')
            if result[0].type_name.startswith('web.Error.'):
                raise _unpack_web_error(result[0])
            if result[0].type_name != 'None':
                raise core.WebError('InvalidResponse', 'ByteSource.close must return None or Err')
        finally:
            # The stream, not the now-finished handler frame, owns both
            # callbacks until its final read or cancellation.
            _release_value(self.read_callback, self.ctx)
            _release_value(self.close_callback, self.ctx)


class _VMEventSource:
    """Keep event-producing callbacks alive until stream EOF/disconnect."""
    def __init__(self, source: ObjectValue, ctx: RuntimeContext):
        self.next_callback = _duplicate_occurrence(source.fields['next'])
        self.close_callback = _duplicate_occurrence(source.fields['close'])
        self.ctx = ctx
        self.closed = False

    def next(self) -> core.Event | None:
        with _STREAM_CALLBACK_GATE:
            result = self.ctx.call(self.next_callback, [])
        if len(result) != 1:
            raise core.WebError('InvalidResponse', 'EventSource.next must return one value')
        event = result[0]
        if not isinstance(event, ObjectValue):
            raise core.WebError('InvalidResponse', 'Expected Event, None, or Err')
        if event.type_name.startswith('web.Error.'):
            raise _unpack_web_error(event)
        if event.type_name == 'None':
            return None
        if event.type_name != 'web.Event':
            raise core.WebError('InvalidResponse', 'EventSource.next must return Event or None')
        fields = event.fields
        return core.Event(fields['data'], _optional_scalar(fields['event']),
                          _optional_scalar(fields['id']),
                          _optional_scalar(fields['retryMs']))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            with _STREAM_CALLBACK_GATE:
                result = self.ctx.call(self.close_callback, [])
            if len(result) != 1 or not isinstance(result[0], ObjectValue):
                raise core.WebError('InvalidResponse', 'EventSource.close must return None or Err')
            if result[0].type_name.startswith('web.Error.'):
                raise _unpack_web_error(result[0])
            if result[0].type_name != 'None':
                raise core.WebError('InvalidResponse', 'EventSource.close must return None or Err')
        finally:
            _release_value(self.next_callback, self.ctx)
            _release_value(self.close_callback, self.ctx)


@stdlib_element('eventStream', (_EVENT_SOURCE,), (_RESPONSE,),
                param_names=('source',),
                documentation=element_documentation(
                    'Encode owned EventSource events as bounded text/event-stream chunks.',
                    category='Web', returns='An ordinary streaming Response.'))
def _event_stream(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return (_wrap_response(core.event_stream(_VMEventSource(args[0], ctx))),)


@stdlib_element('stream', (_BYTE_SOURCE, T.String), (_STREAM_BODY,),
                param_names=('source', 'contentType'),
                param_defaults=(None, (StringLiteralNode('application/octet-stream'),)),
                documentation=element_documentation(
                    'Transfer one owned ByteSource into a single streaming response body.',
                    category='Web', returns='A nonduplicable StreamBody.'))
def _stream(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    source, content_type = args
    return (_lease_stream(core.stream(_VMByteSource(source, ctx), content_type)),)


@stdlib_element('streamResponse', (_BYTE_SOURCE, T.String), (_RESPONSE,),
                param_names=('source', 'contentType'),
                param_defaults=(None, (StringLiteralNode('application/octet-stream'),)),
                documentation=element_documentation(
                    'Construct a single-response owned stream without buffering, while the '
                    'compiler learns to transfer StreamBody through Response constructors.',
                    category='Web', returns='Response owning an exclusive ByteSource.'))
def _stream_response(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    source, content_type = args
    return (_wrap_response(core.Response(body=core.stream(_VMByteSource(source, ctx), content_type))),)

def _wrap_response(response: core.Response) -> ObjectValue:
    # Response is one public nominal type for both textual and binary bodies.
    # Never force opaque response bytes through UTF-8, even on an error path.
    body = (_lease_stream(response.body) if isinstance(response.body, core.StreamBody) else
            _wrap_bytes(response.body) if isinstance(response.body, bytes) else
            response.body if isinstance(response.body, str) else '')
    return ObjectValue('web.Response', {
        'status': RuntimeNumber(response.status),
        'body': body,
        'headers': ObjectValue('Some', {'value': _wrap_headers(response.headers)})
                   if response.headers.pairs else ObjectValue('None', {}),
        'earlyHints': _optional_list(list(response.early_hints)),
        'trailers': (ObjectValue('Some', {'value': _wrap_headers(response.trailers)})
                     if response.trailers.pairs else ObjectValue('None', {})),
    })


def _unwrap_response(value: ObjectValue) -> core.Response:
    if not isinstance(value, ObjectValue) or value.type_name != 'web.Response':
        raise TypeError('Expected a web.Response')
    headers = value.fields['headers']
    if headers.type_name == 'Some':
        headers = headers.fields['value']
    parsed_headers = _unwrap_headers(headers)
    body = value.fields['body']
    # Access through an active borrow must remain a bounded byte COPY; never
    # retain a borrowed VM-owned collection in a Response beyond its owner.
    if isinstance(body, BorrowedValue):
        body = body.read()
    if isinstance(body, ObjectValue) and body.type_name == 'web.StreamBody':
        body = _claim_stream(body)
    elif isinstance(body, (TaggedValue, ListValue)):
        body = _unwrap_bytes(body)
    elif isinstance(body, ObjectValue) and body.type_name == 'html.Node':
        try:
            body = _render_html_node(body)
        except (ValueError, UnicodeError) as exc:
            raise core.WebError('InvalidResponse', 'Invalid HTML node') from exc
        if not any(name.lower() == 'content-type' for name, _ in parsed_headers.pairs):
            parsed_headers = core.Headers((*parsed_headers.pairs, ('Content-Type', 'text/html; charset=utf-8')))
    elif not isinstance(body, str):
        raise core.WebError('InvalidResponse', 'Unsupported response body value')
    status = value.fields['status']
    if isinstance(status, ObjectValue):
        status = _status_code(status)
    return core.Response(int(status), body, parsed_headers,
                         tuple(_optional_contents(value.fields.get('earlyHints', ObjectValue('None', {})))),
                         _unwrap_headers(value.fields.get('trailers', ObjectValue('None', {}))))


_WS_LEASES: dict[int, object] = {}
_WS_LOCK = RLock()
_WS_RUNTIME = ObjectRuntimeType(
    destructor_name='web.WebSocket::~WebSocket',
    dup_error='A WebSocket connection cannot be duplicated',
    field_order=('token', 'request', 'subprotocol'),
)


def _wrap_ws_request(request: core.Request) -> ObjectValue:
    """Snapshot upgrade metadata and its typed context without sharing the owner."""
    return _wrap_request(request, concrete=True)


def _ws_lease(ws: object) -> ObjectValue:
    with _WS_LOCK:
        token = randbits(112) or 1
        while token in _WS_LEASES:
            token = randbits(112) or 1
        _WS_LEASES[token] = ws
    return ObjectValue('web.WebSocket', {
        'token': RuntimeNumber(token), 'request': _wrap_ws_request(ws.request),
        'subprotocol': _string_optional(ws.subprotocol),
    }, runtime_type=_WS_RUNTIME)


@stdlib_element('websocketRequest', (_WEBSOCKET,), (_WEBSOCKET_REQUEST,),
                param_names=('socket',),
                documentation=element_documentation(
                    'Read the validated WebSocket handshake snapshot without transferring the connection owner.',
                    category='Web', returns='An immutable WebSocketRequest value.'))
def _ws_request(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_wrap_ws_request(_ws_get(args[0]).request),)


def _ws_get(obj: ObjectValue):
    with _WS_LOCK:
        conn = _WS_LEASES.get(int(obj.fields['token']))
    if conn is None:
        raise core.WebError('BodyUnavailable', 'WebSocket connection has closed')
    return conn


def _ws_retire(token: int) -> None:
    with _WS_LOCK:
        conn = _WS_LEASES.pop(token, None)
    if conn is not None:
        conn.abort()


@stdlib_element('nativeCloseWebSocket', (T.Int,), (), exported=False)
def _native_close_websocket(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    _ws_retire(int(args[0]))
    return ()


@stdlib_element('websocketReceive', (_WEBSOCKET,), (T.Result(_WEBSOCKET_MESSAGE, _ERROR),),
                param_names=('socket',),
                documentation=element_documentation('Receive a WebSocket text, binary or close event.',
                                                    category='Web', returns='WebSocketMessage or web.Error.'))
def _ws_receive(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        message = _ws_get(args[0]).receive()
        if message.kind == 'Text':
            wrapped = ObjectValue('web.WebSocketMessage.Text', {'value': message.value})
        elif message.kind == 'Binary':
            wrapped = ObjectValue('web.WebSocketMessage.Binary', {'value': _wrap_bytes(message.value)})
        else:
            wrapped = ObjectValue('web.WebSocketMessage.Closed', {
                'code': RuntimeNumber(message.code), 'reason': message.reason})
        return (ObjectValue('OK', {'value': wrapped}),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


for _method, _data_type in (('websocketSendText', T.String),
                           ('websocketSendBytes', _BYTE_LIST)):
    def _ws_send(args: tuple[Any, ...], ctx: RuntimeContext, name: str = _method
                 ) -> tuple[Any, ...]:
        del ctx
        try:
            socket_obj, payload = args
            ws = _ws_get(socket_obj)
            if name == 'websocketSendText':
                ws.send_text(payload)
            else:
                ws.send_bytes(_unwrap_bytes(payload))
            return (ObjectValue('OK', {'value': ObjectValue('None', {})}),)
        except core.WebError as exc:
            return (_web_error_value(exc),)
        except (OSError, TimeoutError):
            return (_web_error_value(core.WebError('BodyUnavailable',
                                      'WebSocket transport is unavailable')),)

    _ws_send.__name__ = '_ws_send_' + _method
    _ws_send = stdlib_element(
        _method, (_WEBSOCKET, _data_type), (T.Result(T.N(Symbol('None')), _ERROR),),
        param_names=('socket', 'value'),
        documentation=element_documentation('Send one bounded WebSocket message.',
                                            category='Web', returns='None or web.Error.'),
    )(_ws_send)
    globals()[_ws_send.__name__] = _ws_send

del _ws_send


@stdlib_element('websocketPing', (_WEBSOCKET, _BYTE_LIST),
                (T.Result(T.N(Symbol('None')), _ERROR),),
                param_names=('socket', 'data'),
                param_defaults=(None, (ElementNode(Symbol('nativeEmptyBytes', ('std', 'web'))),)),
                documentation=element_documentation('Send a WebSocket ping control frame.',
                                                    category='Web', returns='None or web.Error.'))
def _ws_ping(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        _ws_get(args[0]).ping(_unwrap_bytes(args[1]))
        return (ObjectValue('OK', {'value': ObjectValue('None', {})}),)
    except core.WebError as exc:
        return (_web_error_value(exc),)
    except (OSError, TimeoutError):
        return (_web_error_value(core.WebError('BodyUnavailable',
                                  'WebSocket transport is unavailable')),)


@stdlib_element('websocketClose', (_WEBSOCKET, T.Int, T.String),
                (T.Result(T.N(Symbol('None')), _ERROR),),
                param_names=('socket', 'code', 'reason'),
                param_defaults=(None, (NumberLiteralNode(1000),), (StringLiteralNode(''),)),
                documentation=element_documentation('Send a WebSocket close control frame.',
                                                    category='Web', returns='None or web.Error.'))
def _ws_close(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        _ws_get(args[0]).close(int(args[1]), args[2])
        return (ObjectValue('OK', {'value': ObjectValue('None', {})}),)
    except core.WebError as exc:
        return (_web_error_value(exc),)
    except (OSError, TimeoutError):
        return (_web_error_value(core.WebError('BodyUnavailable',
                                  'WebSocket transport is unavailable')),)


for _error_output, _error_label in ((_RESPONSE, 'response'),
                                     (T.Result(_RESPONSE, _ERROR), 'response_or_error')):
    def _set_error_handler(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        app, callback = args
        result = _new_config(app)
        result.fields['errorHandler'] = _duplicate_occurrence(callback)
        return (result,)

    _set_error_handler.__name__ = '_error_handler_' + _error_label
    _set_error_handler = stdlib_element(
        'withErrorHandler', (_APP, T.Fn((_ERROR_CONTEXT,), (_error_output,))), (_APP,),
        param_names=('app', 'callback'),
        documentation=element_documentation(
            'Set a single application HTTP error callback. Faults in the error '
            'callback revert to a minimal safe 500 response; never reenter the hook.',
            category='Web', returns='New immutable App.'))(_set_error_handler)
    globals()[_set_error_handler.__name__] = _set_error_handler

del _set_error_handler


@stdlib_element('defaultErrorResponse', (_ERROR_CONTEXT,), (_RESPONSE,),
                param_names=('context',),
                documentation=element_documentation(
                    'Return the safe default HTTP error representation for an ErrorContext.',
                    category='Web', returns='Response.'))
def _default_error_response(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    info, = args
    status = int(info.fields['status'])
    # The public context contains no private exception or user credential data.
    if not 400 <= status <= 599:
        status = 500
    if status >= 500:
        message = 'Internal Server Error'
    else:
        from http import HTTPStatus
        try:
            message = HTTPStatus(status).phrase
        except ValueError:
            message = 'Bad Request'
    response = core.Response(status, message)
    if status == 405:
        response = response.with_header('Allow', ', '.join(info.fields['allowedMethods']))
    if status == 401:
        response = response.with_header('WWW-Authenticate', 'Bearer')
    return (_wrap_response(response),)


for _log_result in (T.N(Symbol('None')), _ERROR, T.Result(T.N(Symbol('None')), _ERROR)):
    def _with_logger(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        app, callback = args
        result = _new_config(app)
        result.fields['logger'] = _duplicate_occurrence(callback)
        return (result,)

    _with_logger.__name__ = '_with_logger_' + str(len(str(_log_result))) + '_' + str(len(globals()))
    _with_logger = stdlib_element(
        'withLogger', (_APP, T.Fn((_LOG_EVENT,), (_log_result,))), (_APP,),
        param_names=('app', 'callback'),
        documentation=element_documentation(
            'Attach an immutable completion-event logger. Events include no raw '
            'URI, authorization, cookie, request body, or response body fields. '
            'Logging errors cannot alter an HTTP response.',
            category='Web', returns='New App with a safe logger.'))(_with_logger)
    globals()[_with_logger.__name__] = _with_logger

del _with_logger


def _to_core_config(target: ObjectValue, ctx: RuntimeContext) -> core.App:
    app = core.App()
    # The interpreter callback context is not safe to invoke concurrently on
    # separate socket workers. Serialize Valiance callbacks for this subset.
    callback_gate = _STREAM_CALLBACK_GATE

    def make_middleware(callback: Any):
        if isinstance(callback, ObjectValue) and callback.type_name == 'web.CorsMiddleware':
            return core.cors(_core_cors_policy(callback.fields['policy']))
        if isinstance(callback, ObjectValue) and callback.type_name == 'web.CompressionMiddleware':
            return core.compression(_core_compression_policy(callback.fields['policy']))
        def middleware(req: core.Request) -> object:
            with callback_gate:
                outputs = ctx.call(callback, [_wrap_request(req)])
            if len(outputs) != 1:
                raise core.WebError('InvalidResponse', 'Middleware must return one value')
            value = outputs[0]
            if isinstance(value, RecordValue) and set(value) == {'request', 'after'}:
                updated_request = _unwrap_request(value['request'])
                after_callback = value['after']
                def run_after(response: core.Response) -> object:
                    with callback_gate:
                        after_outputs = ctx.call(after_callback, [_wrap_response(response)])
                    if len(after_outputs) != 1:
                        raise core.WebError('InvalidResponse', 'After callback must return one value')
                    result = after_outputs[0]
                    if isinstance(result, ObjectValue) and result.type_name == 'web.Response':
                        return _unwrap_response(result)
                    if isinstance(result, ObjectValue) and result.type_name.startswith('web.Error.'):
                        return _unpack_web_error(result)
                    raise core.WebError('InvalidResponse', 'After callback must return Response or Err')
                return core.After(updated_request, run_after)
            return (_unwrap_response(value) if isinstance(value, ObjectValue) and
                    value.type_name == 'web.Response' else
                    _unwrap_request(value) if isinstance(value, ObjectValue) and
                    value.type_name.startswith('web.Request.') else
                    _unpack_web_error(value)
                    if isinstance(value, ObjectValue) and value.type_name.startswith('web.Error.')
                    else value)
        return middleware

    for route in _optional_contents(target.fields['routes']):
        method = route.type_name.rsplit('.', 1)[-1].upper()
        callback = route.fields['handler']
        if method == 'WEBSOCKET':
            def websocket_handler(ws, callback=callback):
                socket_value = _ws_lease(ws)
                token = int(socket_value.fields['token'])
                try:
                    with callback_gate:
                        if ctx.call_scoped is not None:
                            result = ctx.call_scoped(
                                callback, [socket_value], cancel_event=ws.cancellation_event)
                        else:
                            result = ctx.call(callback, [socket_value])
                    if len(result) != 1:
                        raise core.WebError('InvalidResponse', 'WebSocket callback returned wrong arity')
                    if isinstance(result[0], ObjectValue) and result[0].type_name.startswith('web.Error.'):
                        raise _unpack_web_error(result[0])
                finally:
                    _ws_retire(token)
            app = app.websocket(route.fields['path'], websocket_handler,
                                _core_ws_policy(route.fields['policy']))
            scoped = tuple(make_middleware(cb) for cb in
                           _optional_contents(route.fields['middleware']))
            app = replace(app, entries=(*app.entries[:-1],
                                        replace(app.entries[-1], middleware=scoped)))
            continue
        if isinstance(callback, ObjectValue) and callback.type_name == 'web._StaticFileHandler':
            root = callback.fields['root']
            def handler(req: core.Request, root: str = root) -> object:
                return core.static_file(root, req)
            app = app.route(method, route.fields['path'], handler)
            scoped = tuple(make_middleware(cb) for cb in
                           _optional_contents(route.fields['middleware']))
            app = replace(app, entries=(*app.entries[:-1], replace(app.entries[-1], middleware=scoped)))
            continue
        def handler(req: core.Request, callback: Any = callback) -> object:
            with callback_gate:
                outputs = ctx.call(callback, [_wrap_request(req, concrete=True)])
            if len(outputs) != 1:
                raise core.WebError('InvalidResponse', 'Handler must return one value')
            value = outputs[0]
            if isinstance(value, ObjectValue) and value.type_name.startswith('web.Error.'):
                return _unpack_web_error(value)
            if isinstance(value, ObjectValue) and value.type_name == 'ValueError':
                return core.WebError('Internal', 'Application JSON encoding failed')
            if isinstance(value, ObjectValue) and value.type_name == 'html.Node':
                try:
                    markup = _render_html_node(value)
                except (ValueError, UnicodeError) as exc:
                    raise core.WebError('InvalidResponse', 'Invalid HTML node') from exc
                return core.Response(body=markup, headers=core.Headers((
                    ('Content-Type', 'text/html; charset=utf-8'),)))
            return (_unwrap_response(value) if isinstance(value, ObjectValue)
                    and value.type_name == 'web.Response' else value)
        limit_field = _optional_scalar(route.fields.get('maxBodyBytes', ObjectValue('None', {})))
        app = app.route(method, route.fields['path'], handler,
                        body_mode=route.fields.get('bodyMode', 'Buffered'),
                        max_body_bytes=int(limit_field) if limit_field is not None else None)
        scoped = tuple(make_middleware(cb) for cb in
                       _optional_contents(route.fields['middleware']))
        app = replace(app, entries=(*app.entries[:-1], replace(app.entries[-1], middleware=scoped)))
    for callback in _optional_contents(target.fields['middleware']):
        app = app.use(make_middleware(callback))
    error_callback = target.fields.get('errorHandler', ObjectValue('None', {}))
    if not isinstance(error_callback, ObjectValue) or error_callback.type_name != 'None':
        def on_error(req: core.Request | None, error: Exception, stage: str) -> core.Response:
            if isinstance(error, core.WebError):
                name = error.name
                status = error.status
                if error.stage == 'ResponseNormalization' and name in (
                        'InvalidCookie', 'InvalidHeader', 'AmbiguousHeader'):
                    status = 500
                allowed = error.allowed
            else:
                name, status, allowed = 'Internal', 500, ()
            wrapped = ObjectValue('web.ErrorContext', {
                'request': (ObjectValue('Some', {'value': _wrap_request(req)})
                            if req is not None else ObjectValue('None', {})),
                'requestId': req.request_id if req is not None else '',
                'stage': stage, 'name': name,
                'status': RuntimeNumber(status),
                'allowedMethods': ListValue(list(allowed), runtime_rank=1),
            })
            with callback_gate:
                outputs = ctx.call(error_callback, [wrapped])
            if len(outputs) != 1:
                raise core.WebError('InvalidResponse', 'Error handler returned wrong arity')
            result = outputs[0]
            if isinstance(result, ObjectValue) and result.type_name.startswith('web.Error.'):
                raise _unpack_web_error(result)
            return _unwrap_response(result)
        app = replace(app, on_error=on_error)
    logger_value = target.fields.get('logger', ObjectValue('None', {}))
    if not isinstance(logger_value, ObjectValue) or logger_value.type_name != 'None':
        def log_callback(event: core.LogEvent) -> None:
            wrapped = ObjectValue('web.LogEvent', {
                'requestId': event.request_id,
                'method': event.method,
                'routeTemplate': _string_optional(event.route_template),
                'status': RuntimeNumber(event.status),
                'protocol': event.protocol,
                'elapsedMilliseconds': RuntimeNumber(event.elapsed_milliseconds),
                'requestBytes': RuntimeNumber(event.request_bytes),
                'responseBytes': RuntimeNumber(event.response_bytes),
                'peer': event.peer,
                'clientAddress': event.client_address,
                'errorStage': _string_optional(event.error_stage),
            })
            with callback_gate:
                outputs = ctx.call(logger_value, [wrapped])
            if len(outputs) != 1:
                raise core.WebError('InvalidResponse', 'Logger callback returned wrong arity')
            if isinstance(outputs[0], ObjectValue) and outputs[0].type_name.startswith('web.Error.'):
                raise _unpack_web_error(outputs[0])
        app = app.with_logger(log_callback)
    return app


for _cfg_type, _which in ((_APP, 'app'), (_ROUTER, 'router')):
    def _set_stream_mode(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        target, method, path, mode = args
        try:
            method_name = method.type_name.rsplit('.', 1)[-1].upper()
            mode_name = mode.type_name.rsplit('.', 1)[-1]
            routes = [_duplicate_occurrence(route) for route in _optional_contents(target.fields['routes'])]
            matching = [i for i, route in enumerate(routes) if
                        route.type_name.rsplit('.', 1)[-1].upper() == method_name and
                        route.fields['path'] == path]
            if method_name not in ('POST', 'PUT', 'PATCH', 'QUERY') or len(matching) != 1 or mode_name not in ('Buffered', 'Stream'):
                raise core.ConfigError('Body mode requires one registered body-capable route')
            i = matching[0]
            fields = dict(routes[i].fields)
            fields['bodyMode'] = mode_name
            routes[i] = ObjectValue(routes[i].type_name, fields)
            return (_new_config(target, routes=routes),)
        except core.ConfigError as exc:
            return (_config_error_value(exc),)

    _set_stream_mode.__name__ = f'_set_body_mode_{_which}'
    _set_stream_mode = stdlib_element(
        'withBodyMode', (_cfg_type, _nominal('Method'), T.String, _BODY_MODE),
        (T.Result(_cfg_type, _CONFIG_ERROR),),
        param_names=('appOrRouter', 'method', 'template', 'mode'),
        documentation=element_documentation(
            'Opt a registered content-bearing route into incremental request-body reads.',
            category='Web', returns='New App or Router; rejects unmatched route identities.'),
    )(_set_stream_mode)
    globals()[_set_stream_mode.__name__] = _set_stream_mode

del _set_stream_mode


@stdlib_element('nativeCloseRequestStream', (T.Int,), (), exported=False)
def _native_close_request_stream(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    stream = _stream_for_token(int(args[0]))
    if stream is not None:
        stream.close()
    return ()


for _method_name in ('Post', 'Put', 'Patch', 'Query'):
    def _body_stream(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        value, = args
        stream = _stream_for_token(int(value.fields.get('streamToken', 0)))
        try:
            if stream is None:
                raise core.WebError('BodyUnavailable', 'Request is not in streaming mode')
            stream.claim()
            token = _request_token(stream)
            return (ObjectValue('web.RequestBodyStream', {'token': RuntimeNumber(token)},
                                runtime_type=_REQUEST_BODY_RUNTIME),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _body_stream.__name__ = f'_body_stream_{_method_name.lower()}'
    _body_stream = stdlib_element(
        'bodyStream', (_nominal(f'{_method_name}Request'),),
        (T.Result(_REQUEST_BODY_STREAM, _ERROR),),
        param_names=('request',),
        documentation=element_documentation('Claim one owned incoming body stream.', category='Web',
                                           returns='RequestBodyStream or Error.'),
    )(_body_stream)
    globals()[_body_stream.__name__] = _body_stream

del _body_stream


# Incoming trailing headers belong to the body, not the initial Headers.
# They cannot be observed before the stream was consumed all the way to EOF.
for _method_name in ('Post', 'Put', 'Patch', 'Query'):
    def _request_trailers(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        request, = args
        stream = _stream_for_token(int(request.fields.get('streamToken', 0)))
        try:
            if stream is None:
                raise core.WebError('WrongMode', 'Request trailers require a streaming body')
            if not stream.complete:
                raise core.WebError('BodyUnavailable', 'Request body must reach EOF first')
            return (_wrap_headers(stream.trailer_fields),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _request_trailers.__name__ = f'_request_trailers_{_method_name.lower()}'
    _request_trailers = stdlib_element(
        'trailers', (_nominal(f'{_method_name}Request'),),
        (T.Result(_HEADERS, _ERROR),), param_names=('request',),
        documentation=element_documentation(
            'Read validated request trailers after streaming-body EOF only; '
            'never merge them with initial request headers.',
            category='Web', returns='Headers or a typed web.Error.'),
    )(_request_trailers)
    globals()[_request_trailers.__name__] = _request_trailers

del _request_trailers


@stdlib_element('readBodyStream', (_REQUEST_BODY_STREAM, T.Int),
                (T.Result(T.U(_BYTE_LIST, T.N(Symbol('None'))), _ERROR),),
                param_names=('stream', 'maxBytes'),
                documentation=element_documentation('Read up to 64 KiB of request bytes; None means EOF.',
                                                    category='Web', returns='Exact bytes or None or Error.'))
def _read_request_stream(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    stream = _stream_for_token(int(args[0].fields['token']))
    try:
        if stream is None:
            raise core.WebError('BodyUnavailable', 'Incoming stream already closed')
        data = stream.read(int(args[1]))
        return (_wrap_bytes(data) if data is not None else ObjectValue('OK', {'value': ObjectValue('None', {})}),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


@stdlib_element('discardBodyStream', (_REQUEST_BODY_STREAM,),
                (T.Result(T.N(Symbol('None')), _ERROR),),
                param_names=('stream',),
                documentation=element_documentation('Discard a streaming request body, up to its limit.',
                                                    category='Web', returns='None or Error.'))
def _discard_request_stream(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    stream = _stream_for_token(int(args[0].fields['token']))
    try:
        if stream is None:
            raise core.WebError('BodyUnavailable', 'Incoming stream already closed')
        stream.discard()
        return (ObjectValue('OK', {'value': ObjectValue('None', {})}),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


_DEFAULT_HEADERS = (ElementNode(Symbol('nativeEmptyHeaders', ('std', 'web'))),)
_DEFAULT_BYTES = (ElementNode(Symbol('nativeEmptyBytes', ('std', 'web'))),)


@stdlib_element('nativeEmptyHeaders', (), (_HEADERS,), exported=False)
def _native_empty_headers(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (_wrap_headers(core.Headers()),)


@stdlib_element('nativeEmptyBytes', (), (_BYTE_LIST,), exported=False)
def _native_empty_bytes(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (_wrap_bytes(b''),)


@stdlib_element('testRequest', (T.String, T.String, _HEADERS, _BYTE_LIST), (_REQUEST,),
                param_names=('method', 'target', 'headers', 'body'),
                param_defaults=(None, None, _DEFAULT_HEADERS, _DEFAULT_BYTES),
                documentation=element_documentation('Create a validated, socket-free request.', category='Web',
                                                     returns='Method-specific request value.',
                                                     examples=(('import {std.web}\nweb.testRequest("GET", "/")', None),)))
def _test_request(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    method, target, headers, body = args
    return (_wrap_request(core.test_request(method, target, headers=_unwrap_headers(headers),
                                           body=_unwrap_bytes(body))),)


_DISPATCH_DOCS = element_documentation(
    'Execute the same transport-independent routing pipeline for an App or prepared App.',
    category='Web', returns='A normalized HTTP Response.',
    examples=(('import {std.web}\nweb.dispatch(web.App, web.testRequest("GET", "/"))', None),),
)


@stdlib_element('dispatch', (_APP, _REQUEST), (_RESPONSE,),
                param_names=('app', 'request'), documentation=_DISPATCH_DOCS)
def _dispatch(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    target, request = args
    return (_wrap_response(core.dispatch(_to_core_config(target, ctx), _unwrap_request(request))),)


@stdlib_element('prepare', (_APP,), (T.Result(_PREPARED_APP, _CONFIG_ERROR),),
                param_names=('app',),
                documentation=element_documentation(
                    'Validate the immutable route table without starting a listener.',
                    category='Web', returns='PreparedApp or ConfigError.',
                    examples=(('import {std.web}\nweb.prepare(web.App)', None),)))
def _prepare_app(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    target, = args
    try:
        core.prepare(_to_core_config(target, ctx))
        return (ObjectValue('web.PreparedApp', {'app': _duplicate_occurrence(target)}),)
    except core.ConfigError as exc:
        return (_config_error_value(exc),)


@stdlib_element('dispatch', (_PREPARED_APP, _REQUEST), (_RESPONSE,),
                param_names=('prepared', 'request'), documentation=_DISPATCH_DOCS)
def _dispatch_prepared(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    target, request = args
    return (_wrap_response(core.dispatch(
        _to_core_config(target.fields['app'], ctx), _unwrap_request(request))),)


for _target_type, _kind in ((_APP, 'app'), (_ROUTER, 'router')):
    def _routes(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        try:
            registered = core.routes(_to_core_config(args[0], ctx))
            descriptors = []
            for method, path, automatic in registered:
                descriptors.append(ObjectValue('web.RouteDescriptor', {
                    'method': ObjectValue(f'web.Method.{"GET" if method == "WEBSOCKET" else method}', {}),
                    'template': path, 'path': path,
                    'automatic': TaggedValue(RuntimeNumber(int(automatic)), T.Boolean.tags),
                    'upgrade': TaggedValue(RuntimeNumber(int(method == 'WEBSOCKET')), T.Boolean.tags),
                    # Source locations are unavailable in the current native
                    # registration representation; never fabricate them.
                    'source': ObjectValue('None', {}),
                }))
            return (ListValue(descriptors, runtime_rank=1),)
        except core.ConfigError as exc:
            return (_config_error_value(exc),)

    _routes.__name__ = f'_routes_{_kind}'
    _routes = stdlib_element(
        'routes', (_target_type,), (T.Result(T.ExactList(_ROUTE_DESCRIPTOR), _CONFIG_ERROR),),
        param_names=('appOrRouter',),
        documentation=element_documentation(
            'Inspect validated effective routes and automatic HEAD/OPTIONS entries without exposing closures.',
            category='Web', returns='Route descriptors or ConfigError.',
            examples=(('import {std.web}\nweb.routes(web.App)', None),)),
    )(_routes)
    globals()[_routes.__name__] = _routes

del _routes


@stdlib_element('withHeader', (_HEADERS, T.String, T.String), (_HEADERS,),
                param_names=('headers', 'name', 'value'),
                documentation=element_documentation(
                    'Immutably edit validated response header fields.',
                    category='Web', returns='New Response value.',
                    examples=(('import {std.web}\nweb.withHeader(web.Response(body = "ok"), "X-Web", "yes")', None),)))
def _with_headers_collection(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    headers, name, value = args
    return (_wrap_headers(_unwrap_headers(headers).with_header(name, value)),)


# Buffer inspection is deliberately bounded. Source-body bytes are retained by
# collectBody; responseText performs strict text decoding and reports errors in
# the ordinary Valiance Result union instead of exposing UnicodeError to the VM.
_DEFAULT_COLLECT_LIMIT = (NumberLiteralNode('16777216'),)


@stdlib_element('collectBody', (_RESPONSE, T.Int), (T.Result(_BYTE_LIST, _ERROR),),
                param_names=('response', 'limit'),
                param_defaults=(None, _DEFAULT_COLLECT_LIMIT),
                documentation=element_documentation(
                    'Collect a buffered HTTP representation as byte-tagged integers, subject to a limit.',
                    category='Web', returns='Exact bytes or a typed web.Error.',
                    examples=(('import {std.web}\nweb.collectBody(web.Response(body = "ok"))', None),)))
def _collect_body(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        return (_wrap_bytes(core.collect_body(_unwrap_response(args[0]), int(args[1]))),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


@stdlib_element('responseText', (_RESPONSE, T.Int), (T.Result(T.String, _ERROR),),
                param_names=('response', 'limit'),
                param_defaults=(None, _DEFAULT_COLLECT_LIMIT),
                documentation=element_documentation(
                    'Decode a bounded buffered response as strict UTF-8, returning an error for binary data.',
                    category='Web', returns='UTF-8 content or a typed web.Error.',
                    examples=(('import {std.web}\nweb.responseText(web.Response(body = "ok"))', None),)))
def _response_text(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        return (core.response_text(_unwrap_response(args[0]), int(args[1])),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


# Media negotiation is available on the common Request and each nominal
# method request. A malformed Accept field yields an ordinary typed Err.
for _req_type, _req_label in ((_REQUEST, 'common'), *(
    (_nominal(f'{title}Request'), title.lower()) for _, title in _METHODS)):
    def _accepts(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        request, proposed = args
        if not request.type_name.startswith('web.Request.'):
            request = ObjectValue('web.Request.' + request.type_name.rsplit('.', 1)[-1], request.fields)
        try:
            return (RuntimeNumber(1 if core.accepts(_unwrap_request(request), proposed) else 0),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _accepts.__name__ = f'_accepts_{_req_label}'
    _accepts = stdlib_element(
        'accepts', (_req_type, T.String), (T.Result(T.Boolean, _ERROR),),
        param_names=('request', 'mediaType'),
        documentation=element_documentation(
            'Check Accept media ranges, specificity, and quality (including q=0).',
            category='Web', returns='Boolean or InvalidHeader error.',
            examples=(('import {std.web}\nweb.accepts(web.testRequest("GET", "/"), "text/plain")', None),)),
    )(_accepts)
    globals()[_accepts.__name__] = _accepts

del _accepts

# Pure operations on the ordered multi-value query and header types. They are
# published for each concrete handler request and the common Request variant;
# this is ordinary overload resolution, not an implicit subtype conversion.
_STRING_LIST = T.ExactList(T.String)
for _req_type, _req_name in ((_REQUEST, 'common'), *(
    (_nominal(f'{title}Request'), title.lower()) for _, title in _METHODS)):
    def _request_query(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        return (_duplicate_occurrence(args[0].fields['query']),)

    _request_query.__name__ = f'_query_params_{_req_name}'
    _request_query = stdlib_element(
        'queryParams', (_req_type,), (_PARAMS,), param_names=('request',),
        documentation=element_documentation('Read the ordered query multimap without merging other parameter sources.', category='Web', returns='Immutable Params value.', examples=(('import {std.web}\nweb.queryParams(web.testRequest("GET", "/?q=1"))', None),)),
    )(_request_query)
    globals()[_request_query.__name__] = _request_query

    def _request_header_values(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        request, name = args
        headers = _unwrap_headers(request.fields['headers'])
        return (ListValue(list(headers.values(name)), runtime_rank=1),)

    _request_header_values.__name__ = f'_header_values_{_req_name}'
    _request_header_values = stdlib_element(
        'headerValues', (_req_type, T.String), (_STRING_LIST,),
        param_names=('request', 'name'),
        documentation=element_documentation('Get all raw field occurrences by case-insensitive header name.', category='Web', returns='List of values in arrival order.', examples=(('import {std.web}\nweb.headerValues(web.testRequest("GET", "/"), "Accept")', None),)),
    )(_request_header_values)
    globals()[_request_header_values.__name__] = _request_header_values

del _request_query, _request_header_values


@stdlib_element('headerValues', (_HEADERS, T.String), (_STRING_LIST,),
                param_names=('headers', 'name'),
                documentation=element_documentation(
                    'Read all values of a validated Headers collection, including request trailers.',
                    category='Web', returns='Values in field arrival order.'))
def _header_values_collection(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (ListValue(list(_unwrap_headers(args[0]).values(args[1])), runtime_rank=1),)


@stdlib_element('header', (_HEADERS, T.String),
                (T.Result(T.optional(T.String), _ERROR),),
                param_names=('headers', 'name'),
                documentation=element_documentation(
                    'Read one validated Headers value, rejecting ambiguous singleton fields.',
                    category='Web', returns='Optional value or typed Error.'))
def _header_single_collection(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        return (_string_optional(_unwrap_headers(args[0]).one(args[1])),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


# The public body helpers accept only methods with defined request-body
# semantics. The bridge preserves arbitrary octets with surrogateescape while
# these helpers enforce the requested strict UTF-8 decoding and media rules.
for _body_method in ('Post', 'Put', 'Patch', 'Query'):
    _body_req_type = _nominal(f'{_body_method}Request')
    for _public_name, _impl, _result_type in (
        ('bodyText', core.body_text, T.String),
        ('bodyBytes', core.body_bytes, _BYTE_LIST),
        ('formParams', core.form_params, _PARAMS),
    ):
        def _body_helper(args: tuple[Any, ...], ctx: RuntimeContext,
                         impl: Any = _impl) -> tuple[Any, ...]:
            del ctx
            request, = args
            # The concrete request is a method-specific nominal view, whereas
            # middleware gets the equivalent closed-variant case.
            kind = request.type_name.rsplit('.', 1)[-1]
            raw_request = ObjectValue(f'web.Request.{kind}', request.fields)
            try:
                result = impl(_unwrap_request(raw_request))
                if isinstance(result, core.Params):
                    result = _wrap_params(result)
                elif isinstance(result, bytes):
                    result = _wrap_bytes(result)
                return (result,)
            except core.WebError as exc:
                return (_web_error_value(exc),)

        _body_helper.__name__ = f'_{_public_name}_{_body_method.lower()}'
        _body_helper = stdlib_element(
            _public_name, (_body_req_type,),
            (T.Result(_result_type, _ERROR),),
            param_names=('request',),
            documentation=element_documentation(
                'Decode a bounded buffered body according to its media type; '
                'return a typed web.Error for invalid input.',
                category='Web',
                returns='Decoded content or a web.Error.',
                examples=((
                    'import {std.web, std.web.[post]}\n'
                    '$app = web.App\n'
                    '$app := post("/echo"): fn(req: web.PostRequest) -> String | web.Error =>\n'
                    f'  $value = web.{_public_name}($req) ?\n'
                    '  "ok"\nend', None),),
            ),
        )(_body_helper)
        globals()[_body_helper.__name__] = _body_helper

del _body_helper


# Multipart request streams have a single owner and share the framed
# RequestBodyStream lease. Closing the iterator also closes its request owner.
_MULTIPART_STREAM_LEASES: dict[int, core.UploadStream] = {}
_MULTIPART_STREAM_LOCK = RLock()
_MULTIPART_STREAM_RUNTIME = ObjectRuntimeType(
    destructor_name='web.UploadStream::~UploadStream',
    dup_error='A multipart upload stream cannot be duplicated',
    field_order=('token',),
)


def _multipart_stream_token(stream: core.UploadStream) -> int:
    with _MULTIPART_STREAM_LOCK:
        token = randbits(112) or 1
        while token in _MULTIPART_STREAM_LEASES:
            token = randbits(112) or 1
        _MULTIPART_STREAM_LEASES[token] = stream
        prev = getattr(stream, '_on_close', None)
        def cleanup():
            with _MULTIPART_STREAM_LOCK:
                _MULTIPART_STREAM_LEASES.pop(token, None)
            if prev is not None:
                prev()
        stream._on_close = cleanup
        return token


def _multipart_stream_for_token(token: int) -> core.UploadStream | None:
    with _MULTIPART_STREAM_LOCK:
        return _MULTIPART_STREAM_LEASES.get(token)


@stdlib_element('nativeCloseMultipartStream', (T.Int,), (), exported=False)
def _native_close_multipart_stream(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    stream = _multipart_stream_for_token(int(args[0]))
    if stream is not None:
        stream.close()
    return ()


for _method in ('Post', 'Put', 'Patch', 'Query'):
    def _multipart_stream(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        request, = args
        raw = ObjectValue('web.Request.' + request.type_name.rsplit('.', 1)[-1], request.fields)
        try:
            reader = core.multipart_stream(_unwrap_request(raw))
            token = _multipart_stream_token(reader)
            return (ObjectValue('web.UploadStream', {'token': RuntimeNumber(token)},
                                runtime_type=_MULTIPART_STREAM_RUNTIME),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _multipart_stream.__name__ = '_multipart_stream_' + _method.lower()
    _multipart_stream = stdlib_element(
        'multipartStream', (_nominal(f'{_method}Request'),),
        (T.Result(_UPLOAD_STREAM, _ERROR),), param_names=('request',),
        documentation=element_documentation(
            'Claim a streaming multipart/form-data request once. Iterate ordered '
            'field/file events and consume every file before advancing.',
            category='Web', returns='Owned UploadStream or web.Error.'),
    )(_multipart_stream)
    globals()[_multipart_stream.__name__] = _multipart_stream

del _multipart_stream


@stdlib_element('multipartNext', (_UPLOAD_STREAM,),
                (T.Result(T.optional(_MULTIPART_PART), _ERROR),),
                param_names=('stream',),
                documentation=element_documentation(
                    'Advance to the next multipart field/file event (None at EOF). '
                    'The previous file must already be consumed or discarded.',
                    category='Web', returns='Optional MultipartPart or web.Error.'))
def _multipart_next(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    reader = _multipart_stream_for_token(int(args[0].fields['token']))
    try:
        if reader is None:
            raise core.WebError('BodyUnavailable', 'Multipart stream is closed')
        part = reader.next_part()
        if part is None:
            return (ObjectValue('OK', {'value': ObjectValue('None', {})}),)
        event = ObjectValue('web.MultipartPart', {
            'name': part.name,
            'filename': _string_optional(part.filename),
            'contentType': _string_optional(part.content_type),
            'value': _string_optional(part.value),
        })
        return (ObjectValue('Some', {'value': event}),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


@stdlib_element('multipartRead', (_UPLOAD_STREAM, T.Int),
                (T.Result(T.optional(_BYTE_LIST), _ERROR),),
                param_names=('stream', 'maxBytes'),
                documentation=element_documentation(
                    'Read up to 64 KiB of the active upload; None means part EOF.',
                    category='Web', returns='Exact bytes, None or web.Error.'))
def _multipart_read(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    reader = _multipart_stream_for_token(int(args[0].fields['token']))
    try:
        if reader is None:
            raise core.WebError('BodyUnavailable', 'Multipart stream is closed')
        data = reader.read_part(int(args[1]))
        return (_wrap_bytes(data) if data is not None else
                ObjectValue('OK', {'value': ObjectValue('None', {})}),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


@stdlib_element('multipartDiscard', (_UPLOAD_STREAM,),
                (T.Result(T.N(Symbol('None')), _ERROR),),
                param_names=('stream',),
                documentation=element_documentation(
                    'Discard the current upload with bounded reads to its next MIME boundary.',
                    category='Web', returns='None or web.Error.'))
def _multipart_discard(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    reader = _multipart_stream_for_token(int(args[0].fields['token']))
    try:
        if reader is None:
            raise core.WebError('BodyUnavailable', 'Multipart stream is closed')
        reader.discard_part()
        return (ObjectValue('OK', {'value': ObjectValue('None', {})}),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


@stdlib_element('saveMultipartPart',
                (_UPLOAD_STREAM, T.N(Symbol('Path', ('path',)))),
                (T.Result(T.Int, _ERROR),),
                param_names=('stream', 'destination'),
                documentation=element_documentation(
                    'Save the current unconsumed multipart upload to an explicitly '
                    'chosen new Path. Streams bytes to a private file, fails if '
                    'the destination exists, and deletes incomplete uploads.',
                    category='Web', returns='Octets written or web.Error.'))
def _save_multipart_part(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    stream, destination = args
    reader = _multipart_stream_for_token(int(stream.fields['token']))
    try:
        if reader is None:
            raise core.WebError('BodyUnavailable', 'Multipart stream is closed')
        return (RuntimeNumber(reader.save_part(_native_file_path(destination))),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


def _wrap_upload(upload: core.BufferedUpload) -> ObjectValue:
    return ObjectValue('web.Upload', {
        'filename': _string_optional(upload.filename),
        'contentType': _string_optional(upload.content_type),
        'size': RuntimeNumber(upload.size),
        'fieldName': upload.name,
        'data': _wrap_bytes(upload.data),
    })


def _wrap_multipart(form: core.MultipartForm) -> ObjectValue:
    return ObjectValue('web.MultipartForm', {
        'fields': _wrap_params(form.fields),
        'files': _optional_list([_wrap_upload(upload) for upload in form.files]),
    })


for _method in ('Post', 'Put', 'Patch', 'Query'):
    def _multipart(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        request, = args
        raw = ObjectValue('web.Request.' + request.type_name.rsplit('.', 1)[-1], request.fields)
        try:
            return (_wrap_multipart(core.multipart(_unwrap_request(raw))),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _multipart.__name__ = '_multipart_' + _method.lower()
    _multipart = stdlib_element(
        'multipart', (_nominal(f'{_method}Request'),),
        (T.Result(_MULTIPART_FORM, _ERROR),), param_names=('request',),
        documentation=element_documentation(
            'Parse a bounded buffered multipart/form-data body with repeated UTF-8 fields and exact file bytes.',
            category='Web', returns='MultipartForm or web.Error.',
            examples=(('import {std.web}\n$app = web.App\n'
                       '$app := web.post("/upload"): fn(req: web.PostRequest) -> String | web.Error =>\n'
                       '  $form = web.multipart($req) ?\n'
                       '  web.requireParam(web.multipartFields($form), "name") ?\nend', None),)),
    )(_multipart)
    globals()[_multipart.__name__] = _multipart

del _multipart


@stdlib_element('multipartFields', (_MULTIPART_FORM,), (_PARAMS,),
                param_names=('form',),
                documentation=element_documentation(
                    'Read the ordered field Params of a buffered multipart form.',
                    category='Web', returns='Params preserving all repeated text fields.',
                    examples=(('import {std.web}\nweb.multipartFields', None),)))
def _multipart_fields(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_duplicate_occurrence(args[0].fields['fields']),)


@stdlib_element('uploads', (_MULTIPART_FORM, T.String), (T.ExactList(_UPLOAD),),
                param_names=('form', 'name'),
                documentation=element_documentation(
                    'Select multipart files under a field name in arrival order.',
                    category='Web', returns='Possibly empty upload list.',
                    examples=(('import {std.web}\nweb.uploads', None),)))
def _uploads(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    form, name = args
    uploads = [item for item in _optional_contents(form.fields['files'])
               if item.fields['fieldName'] == name]
    return (ListValue([_duplicate_occurrence(item) for item in uploads], runtime_rank=1),)


@stdlib_element('uploadOne', (_MULTIPART_FORM, T.String),
                (T.Result(T.optional(_UPLOAD), _ERROR),), param_names=('form', 'name'),
                documentation=element_documentation(
                    'Read one matching upload, rejecting ambiguous repetitions.',
                    category='Web', returns='Optional upload or web.Error.',
                    examples=(('import {std.web}\nweb.uploadOne', None),)))
def _upload_one(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    form, name = args
    matches = [item for item in _optional_contents(form.fields['files'])
               if item.fields['fieldName'] == name]
    if len(matches) > 1:
        return (_web_error_value(core.WebError('AmbiguousParam', 'More than one matching upload')),)
    if not matches:
        return (ObjectValue('None', {}),)
    return (ObjectValue('Some', {'value': _duplicate_occurrence(matches[0])}),)


@stdlib_element('saveUpload', (_UPLOAD, T.N(Symbol('Path', ('path',)))),
                (T.Result(T.N(Symbol('None')), _ERROR),),
                param_names=('upload', 'destination'),
                documentation=element_documentation(
                    'Save a buffered upload to an explicitly chosen std.path.Path; fail if the path exists.',
                    category='Web', returns='None or web.Error.',
                    examples=(('import {std.web, std.path.Path}\nweb.saveUpload', None),)))
def _save_upload(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    upload, destination = args
    try:
        core.save_upload(core.BufferedUpload(
            upload.fields['fieldName'],
            _optional_scalar(upload.fields['filename']) or '',
            _optional_scalar(upload.fields['contentType']),
            _unwrap_bytes(upload.fields['data'])),
            _native_file_path(destination))
        return (ObjectValue('OK', {'value': ObjectValue('None', {})}),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


@stdlib_element('readUpload', (_UPLOAD,), (T.Result(_BYTE_LIST, _ERROR),),
                param_names=('upload',),
                documentation=element_documentation(
                    'Read exact bytes of a bounded buffered upload, without filename-based path access.',
                    category='Web', returns='Byte-tagged octets or web.Error.',
                    examples=(('import {std.web}\nweb.readUpload', None),)))
def _read_upload(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    upload, = args
    return (_duplicate_occurrence(upload.fields['data']),)


@stdlib_element('paramValues', (_PARAMS, T.String), (_STRING_LIST,),
                param_names=('params', 'name'),
                documentation=element_documentation('Retrieve every matching parameter in original order.', category='Web', returns='Possibly empty list of strings.', examples=(('import {std.web}\nweb.paramValues(web.queryParams(web.testRequest("GET", "/?q=1")), "q")', None),)))
def _param_values(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    params, name = args
    return (ListValue(list(_unwrap_params(params).values(name)), runtime_rank=1),)


@stdlib_element('withParam', (_PARAMS, T.String, T.String), (_PARAMS,),
                param_names=('params', 'name', 'value'),
                documentation=element_documentation('Append a parameter without replacing earlier values.', category='Web', returns='New Params value.', examples=(('import {std.web}\nweb.withParam(web.queryParams(web.testRequest("GET", "/")), "q", "2")', None),)))
def _with_param(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    params, name, value = args
    return (_wrap_params(_unwrap_params(params).append(name, value)),)


@stdlib_element('replaceParam', (_PARAMS, T.String, T.String), (_PARAMS,),
                param_names=('params', 'name', 'value'),
                documentation=element_documentation('Replace all occurrences of a parameter with one value.', category='Web', returns='New Params value.', examples=(('import {std.web}\nweb.replaceParam(web.queryParams(web.testRequest("GET", "/?q=1")), "q", "2")', None),)))
def _replace_param(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    params, name, value = args
    return (_wrap_params(_unwrap_params(params).replace(name, value)),)


def _string_optional(value: str | None) -> ObjectValue:
    return ObjectValue('None', {}) if value is None else ObjectValue('Some', {'value': value})


_WEB_ERROR_RUNTIME = ObjectRuntimeType(
    accepted_names=('Err', 'web.Error'), field_order=('message',),
)
_WEB_CONFIG_ERROR_RUNTIME = ObjectRuntimeType(
    accepted_names=('Err', 'web.ConfigError'), field_order=('message',),
)


def _web_error_value(error: core.WebError) -> ObjectValue:
    # Native Err values need the same nominal trait evidence as values made by
    # Valiance's generated variant constructors. The VM's `?` operation uses
    # this evidence to propagate errors rather than treating them as success.
    fields = {'message': error.message}
    # A response-cookie policy error has the public InvalidCookie case, but
    # must never be interpreted as malformed client Cookie input (HTTP 400).
    # This marker is produced only at the native construction boundary.
    if error.stage == 'ResponseNormalization':
        fields['_web_error_stage'] = error.stage
    return ObjectValue(f'web.Error.{error.name}', fields,
                       runtime_type=_WEB_ERROR_RUNTIME)


def _unpack_web_error(value: ObjectValue) -> core.WebError:
    return core.WebError(value.type_name.rsplit('.', 1)[-1], value.fields['message'],
                         stage=value.fields.get('_web_error_stage', 'Handler'))


def _config_error_value(error: core.ConfigError) -> ObjectValue:
    return ObjectValue('web.ConfigError.InvalidRoute', {'message': str(error)},
                       runtime_type=_WEB_CONFIG_ERROR_RUNTIME)


for _public_name, _operation, _type in (
    ('paramOne', core.Params.one, T.optional(T.String)),
    ('requireParam', core.Params.require, T.String),
):
    def _param_single(args: tuple[Any, ...], ctx: RuntimeContext,
                      operation: Any = _operation, optional: bool = _public_name == 'paramOne') -> tuple[Any, ...]:
        del ctx
        params, name = args
        try:
            value = operation(_unwrap_params(params), name)
            return (_string_optional(value) if optional else value,)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _param_single.__name__ = f'_{_public_name}'
    _param_single = stdlib_element(
        _public_name, (_PARAMS, T.String), (T.Result(_type, _ERROR),),
        param_names=('params', 'name'),
        documentation=element_documentation(
            'Retrieve exactly one named parameter without silently discarding duplicates.',
            category='Web', returns='Value or typed HTTP parameter error.',
            examples=((
                'import {std.web}\n'
                'web.paramOne(web.queryParams(web.testRequest("GET", "/?q=one")), "q")', None),),
        ),
    )(_param_single)
    globals()[_param_single.__name__] = _param_single

del _param_single


for _req_type, _req_name in ((_REQUEST, 'common'), *(
    (_nominal(f'{title}Request'), title.lower()) for _, title in _METHODS),
    (_RESPONSE, 'response')):
    def _header_single(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        source, name = args
        try:
            headers = (_unwrap_response(source).headers if source.type_name == 'web.Response'
                       else _unwrap_headers(source.fields['headers']))
            return (_string_optional(headers.one(name)),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _header_single.__name__ = f'_header_single_{_req_name}'
    _header_single = stdlib_element(
        'header', (_req_type, T.String), (T.Result(T.optional(T.String), _ERROR),),
        param_names=('requestOrResponse', 'name'),
        documentation=element_documentation(
            'Read an optional header, rejecting ambiguous singleton or Set-Cookie fields.',
            category='Web', returns='Optional String or typed web.Error.',
            examples=(('import {std.web}\nweb.header(web.testRequest("GET", "/"), "Accept")', None),),
        ),
    )(_header_single)
    globals()[_header_single.__name__] = _header_single

del _header_single


for _fn_name, _core_method, _arguments in (
    ('withHeader', 'with_header', (_RESPONSE, T.String, T.String)),
    ('addHeader', 'add_header', (_RESPONSE, T.String, T.String)),
    ('withoutHeader', 'without_header', (_RESPONSE, T.String)),
):
    def _response_header_helper(args: tuple[Any, ...], ctx: RuntimeContext,
                                method: str = _core_method) -> tuple[Any, ...]:
        del ctx
        response, *params = args
        return (_wrap_response(getattr(_unwrap_response(response), method)(*params)),)

    _response_header_helper.__name__ = f'_{_fn_name}'
    _response_header_helper = stdlib_element(
        _fn_name, _arguments, (_RESPONSE,),
        param_names=('response', 'name', 'value') if len(_arguments) == 3 else ('response', 'name'),
        documentation=element_documentation('Immutably edit validated response header fields.', category='Web', returns='New Response value.', examples=(('import {std.web}\nweb.withHeader(web.Response(body = "ok"), "X-Web", "yes")', None),)),
    )(_response_header_helper)
    globals()[_response_header_helper.__name__] = _response_header_helper

del _response_header_helper


# Cookie helpers provide the complete named policy surface. Default values are
# regular Valiance arguments, not a secondary/options-only API.
@stdlib_element('nativeCookieTrue', (), (T.Boolean,), exported=False)
def _native_cookie_true(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (TaggedValue(RuntimeNumber(1), T.Boolean.tags),)


@stdlib_element('nativeCookieFalse', (), (T.Boolean,), exported=False)
def _native_cookie_false(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (TaggedValue(RuntimeNumber(0), T.Boolean.tags),)


@stdlib_element('nativeCookieNoneString', (), (T.optional(T.String),), exported=False)
def _native_cookie_none_string(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (ObjectValue('None', {}),)


@stdlib_element('nativeCookieNoneInt', (), (T.optional(T.Int),), exported=False)
def _native_cookie_none_int(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (ObjectValue('None', {}),)


_DEFAULT_COOKIE_PATH = (StringLiteralNode('/'),)
_DEFAULT_COOKIE_DOMAIN = (ElementNode(Symbol('nativeCookieNoneString', ('std', 'web'))),)
_DEFAULT_COOKIE_TRUE = (ElementNode(Symbol('nativeCookieTrue', ('std', 'web'))),)
_DEFAULT_COOKIE_FALSE = (ElementNode(Symbol('nativeCookieFalse', ('std', 'web'))),)
_DEFAULT_COOKIE_AGE = (ElementNode(Symbol('nativeCookieNoneInt', ('std', 'web'))),)
_DEFAULT_COOKIE_SITE = (ElementNode(Symbol('Lax', ('web', 'SameSite'))),)


def _cookie_bool(value: Any) -> bool:
    if isinstance(value, TaggedValue):
        return bool(value.value)
    return bool(value)


@stdlib_element('withCookie', (_RESPONSE, T.String, T.String, T.String,
                                T.optional(T.String), T.Boolean, T.Boolean,
                                _nominal('SameSite'), T.optional(T.Int),
                                T.optional(T.Int), T.Boolean),
                (T.Result(_RESPONSE, _ERROR),),
                param_names=('response', 'name', 'value', 'path', 'domain', 'httpOnly',
                             'secure', 'sameSite', 'maxAge', 'expires', 'partitioned'),
                param_defaults=(None, None, None, _DEFAULT_COOKIE_PATH,
                                _DEFAULT_COOKIE_DOMAIN, _DEFAULT_COOKIE_TRUE,
                                _DEFAULT_COOKIE_TRUE, _DEFAULT_COOKIE_SITE,
                                _DEFAULT_COOKIE_AGE, _DEFAULT_COOKIE_AGE,
                                _DEFAULT_COOKIE_FALSE),
                documentation=element_documentation(
                    'Issue a Set-Cookie field using validated, explicit cookie scope, expiration and security settings.',
                    category='Web', returns='Response or typed cookie error.',
                    examples=(('import {std.web}\nweb.withCookie(web.Response, "sid", "value")', None),)))
def _with_cookie_options(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    response, name, value, path, domain, http_only, secure, same_site, max_age, expires, partitioned = args
    try:
        return (_wrap_response(core.with_cookie(
            _unwrap_response(response), name, value, path=path,
            domain=_optional_scalar(domain), http_only=_cookie_bool(http_only),
            secure=_cookie_bool(secure), same_site=same_site.type_name.rsplit('.', 1)[-1],
            max_age=(int(_optional_scalar(max_age)) if _optional_scalar(max_age) is not None else None),
            expires=(int(_optional_scalar(expires)) if _optional_scalar(expires) is not None else None),
            partitioned=_cookie_bool(partitioned))),)
    except core.WebError as exc:
        return (_web_error_value(replace(exc, stage="ResponseNormalization")),)


@stdlib_element('deleteCookie', (_RESPONSE, T.String, T.String,
                                 T.optional(T.String), T.Boolean),
                (T.Result(_RESPONSE, _ERROR),),
                param_names=('response', 'name', 'path', 'domain', 'partitioned'),
                param_defaults=(None, None, _DEFAULT_COOKIE_PATH,
                                _DEFAULT_COOKIE_DOMAIN, _DEFAULT_COOKIE_FALSE),
                documentation=element_documentation(
                    'Expire a named cookie with caller-selected matching path, domain and partition scope.',
                    category='Web', returns='Response or typed cookie error.',
                    examples=(('import {std.web}\nweb.deleteCookie(web.Response, "sid")', None),)))
def _delete_cookie_options(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    response, name, path, domain, partitioned = args
    try:
        return (_wrap_response(core.delete_cookie(
            _unwrap_response(response), name, path=path,
            domain=_optional_scalar(domain), partitioned=_cookie_bool(partitioned))),)
    except core.WebError as exc:
        return (_web_error_value(replace(exc, stage="ResponseNormalization")),)


@stdlib_element('noStore', (_RESPONSE,), (_RESPONSE,),
                param_names=('response',),
                documentation=element_documentation('Set Cache-Control: no-store on a response.', category='Web', returns='New Response value.', examples=(('import {std.web}\nweb.noStore(web.Response(body = "ok"))', None),)))
def _no_store(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_wrap_response(core.no_store(_unwrap_response(args[0]))),)


@stdlib_element('cachePublic', (_RESPONSE, T.Int), (_RESPONSE,),
                param_names=('response', 'maxAge'),
                documentation=element_documentation('Set public cache policy with a nonnegative max-age.', category='Web', returns='New Response value.', examples=(('import {std.web}\nweb.cachePublic(web.Response(body = "ok"), 60)', None),)))
def _cache_public(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_wrap_response(core.cache_public(_unwrap_response(args[0]), int(args[1]))),)


@stdlib_element('cachePrivate', (_RESPONSE, T.Int), (_RESPONSE,),
                param_names=('response', 'maxAge'),
                documentation=element_documentation('Set private cache policy with a nonnegative max-age.', category='Web', returns='New Response value.', examples=(('import {std.web}\nweb.cachePrivate(web.Response(body = "ok"), 60)', None),)))
def _cache_private(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_wrap_response(core.cache_private(_unwrap_response(args[0]), int(args[1]))),)


@stdlib_element('withETag', (_RESPONSE, T.String), (_RESPONSE,),
                param_names=('response', 'tag'),
                documentation=element_documentation('Attach a validated strong ETag representation validator.', category='Web', returns='New Response value.', examples=(('import {std.web}\nweb.withETag(web.Response(body = "ok"), "v1")', None),)))
def _with_etag(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_wrap_response(core.with_etag(_unwrap_response(args[0]), args[1])),)


_DEFAULT_REDIRECT_STATUS = (ElementNode(Symbol('SeeOther', ('web', 'Status'))),)


@stdlib_element('redirect', (T.String, _nominal('Status')), (T.Result(_RESPONSE, _ERROR),),
                param_names=('location', 'status'),
                param_defaults=(None, _DEFAULT_REDIRECT_STATUS),
                documentation=element_documentation('Create a same-origin redirect (default 303) with a validated target and optional status.', category='Web', returns='Response with Location header.', examples=(('import {std.web}\nweb.redirect("/next")', None),)))
def _redirect(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        return (_wrap_response(core.redirect(args[0], status=_status_code(args[1]))),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


_DEFAULT_CREATED_RESPONSE = (ElementNode(Symbol('nativeCreatedEmptyResponse', ('std', 'web'))),)


@stdlib_element('nativeCreatedEmptyResponse', (), (_RESPONSE,), exported=False)
def _created_empty_response(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (_wrap_response(core.Response(201)),)


@stdlib_element('created', (T.String, _RESPONSE), (T.Result(_RESPONSE, _ERROR),),
                param_names=('location', 'response'),
                param_defaults=(None, _DEFAULT_CREATED_RESPONSE),
                documentation=element_documentation('Create a 201 response with a validated Location, optionally preserving a response body.', category='Web', returns='Created Response.', examples=(('import {std.web}\nweb.created("/new")', None),)))
def _created(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        core.redirect(args[0])
        response = _unwrap_response(args[1])
        return (_wrap_response(replace(response.with_header('Location', args[0]), status=201)),)
    except core.WebError as exc:
        return (_web_error_value(exc),)


def _run_http1(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Block in a loopback-first HTTP listener within the calling scope.

    Cleartext is HTTP/1.1; TLS 1.3 negotiates HTTP/1.1 or HTTP/2 via ALPN.
    HTTP/3 is not supported. SIGINT/SIGTERM are handled
    only while this blocking call is active.
    """
    from threading import Event, current_thread, main_thread
    import signal
    from valiance.std import _web_http1

    app_value = args[0]
    config = args[1] if len(args) == 2 else None
    host = config.fields['host'] if config is not None else '127.0.0.1'
    port = int(config.fields['port']) if config is not None else 8080
    server = _web_http1.serve(_to_core_config(app_value, ctx), host=host, port=port,
                              tls=_core_tls(config) if config is not None else None,
                              limits=_core_limits(config) if config is not None else None,
                              network_policy=_core_network_policy(config) if config is not None else None,
                              protocols=_core_protocols(config) if config is not None else None)
    stop = Event()
    previous: dict[signal.Signals, Any] = {}
    try:
        if current_thread() is main_thread():
            def stop_signal(signum: int, frame: Any) -> None:
                stop.set()
            for sig in (signal.SIGINT, signal.SIGTERM):
                previous[sig] = signal.signal(sig, stop_signal)
            while not stop.wait(.1):
                if server._stopped.is_set():
                    break
        else:
            server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for sig, previous_handler in previous.items():
            signal.signal(sig, previous_handler)
        server.shutdown()
    return ()





# The synthesized source object constructor uses TLS? for its default.
# Ordinary explicit TLS values are supported by an additional same-arity
# native constructor overload, without changing normal ServerConfig calls.
@stdlib_element('nativeDefaultLimits', (), (_LIMITS,), exported=False)
def _default_web_limits(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (ObjectValue('web.Limits', {
        'maxRequestTargetBytes': RuntimeNumber(8192),
        'maxHeaderBytes': RuntimeNumber(32768),
        'maxHeaderFields': RuntimeNumber(100),
        'maxDecodedHeaderBytes': RuntimeNumber(65536),
        'maxCompressedHeaderBytes': RuntimeNumber(32768),
        'maxHpackDynamicTableBytes': RuntimeNumber(16384),
        'maxBufferedBodyBytes': RuntimeNumber(16777216),
        'maxStreamingBodyBytes': RuntimeNumber(1073741824),
        'maxFormFields': RuntimeNumber(1000),
        'maxMultipartParts': RuntimeNumber(128),
        'maxUploadFileBytes': RuntimeNumber(268435456),
        'maxMultipartFieldBytes': RuntimeNumber(1048576),
        'maxMultipartNameBytes': RuntimeNumber(1024),
        'maxMultipartFilenameBytes': RuntimeNumber(4096),
        'maxConnections': RuntimeNumber(128),
        'maxConcurrentRequests': RuntimeNumber(1024),
        'maxActiveWebSockets': RuntimeNumber(1024),
        'maxTotalBufferedBytes': RuntimeNumber(268435456),
        'maxBufferedBytesPerConnection': RuntimeNumber(33554432),
        'maxConcurrentStreamsPerConnection': RuntimeNumber(100),
        'headerTimeoutMilliseconds': RuntimeNumber(10000),
        'bodyIdleTimeoutMilliseconds': RuntimeNumber(30000),
        'connectionIdleTimeoutMilliseconds': RuntimeNumber(60000),
        'writeIdleTimeoutMilliseconds': RuntimeNumber(30000),
        'gracefulShutdownMilliseconds': RuntimeNumber(30000),
    }),)


_DEFAULT_LIMITS = (ElementNode(Symbol('nativeDefaultLimits', ('std', 'web'))),)


@stdlib_element('nativeNoneTLS', (), (T.N(Symbol('None')),), exported=False)
def _native_none_tls(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (ObjectValue('None', {}),)


_DEFAULT_NONE_TLS = (ElementNode(Symbol('nativeNoneTLS', ('std', 'web'))),)


_DEFAULT_PROXY_DISABLED = _DEFAULT_NONE_TLS


@stdlib_element('ServerConfig', (T.String, T.Int, T.N(Symbol('None')), _LIMITS,
                                T.U(T.String, T.N(Symbol('None'))), T.U(T.AtLeastList(T.String), T.N(Symbol('None'))),
                                T.U(T.AtLeastList(T.String), T.N(Symbol('None'))), T.U(_PROXY_MODE, T.N(Symbol('None'))),
                                T.U(T.AtLeastList(_PROTOCOL), T.N(Symbol('None')))),
                (_SERVER_CONFIG,),
                param_names=('host', 'port', 'tls', 'limits', 'publicOrigin',
                             'allowedHosts', 'trustedProxies', 'proxyMode', 'protocols'),
                param_defaults=((StringLiteralNode('127.0.0.1'),),
                                (NumberLiteralNode('8080'),), _DEFAULT_NONE_TLS, None,
                                _DEFAULT_NONE_TLS, _DEFAULT_NONE_TLS,
                                _DEFAULT_NONE_TLS, _DEFAULT_PROXY_DISABLED, _DEFAULT_NONE_TLS),
                documentation=element_documentation(
                    'Construct a resource-limited cleartext HTTP/1.1 server configuration.',
                    category='Web', returns='ServerConfig with validated limits.'))
def _server_config_with_limits(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    host, port, _tls, limits, origin, hosts, proxies, mode, protocols = args
    return (ObjectValue('web.ServerConfig', {
        'host': host, 'port': port, 'tls': ObjectValue('None', {}),
        'limits': ObjectValue('Some', {'value': _duplicate_occurrence(limits)}),
        'publicOrigin': _duplicate_occurrence(origin),
        'allowedHosts': _duplicate_occurrence(hosts),
        'trustedProxies': _duplicate_occurrence(proxies),
        'proxyMode': _duplicate_occurrence(mode),
        'protocols': _duplicate_occurrence(protocols),
    }),)


@stdlib_element('ServerConfig', (T.String, T.Int, _TLS, _LIMITS,
                                T.U(T.String, T.N(Symbol('None'))), T.U(T.AtLeastList(T.String), T.N(Symbol('None'))),
                                T.U(T.AtLeastList(T.String), T.N(Symbol('None'))), T.U(_PROXY_MODE, T.N(Symbol('None'))),
                                T.U(T.AtLeastList(_PROTOCOL), T.N(Symbol('None')))), (_SERVER_CONFIG,),
                param_names=('host', 'port', 'tls', 'limits', 'publicOrigin',
                             'allowedHosts', 'trustedProxies', 'proxyMode', 'protocols'),
                param_defaults=((StringLiteralNode('127.0.0.1'),),
                                (NumberLiteralNode('8080'),), None, _DEFAULT_LIMITS,
                                _DEFAULT_NONE_TLS, _DEFAULT_NONE_TLS,
                                _DEFAULT_NONE_TLS, _DEFAULT_PROXY_DISABLED, _DEFAULT_NONE_TLS),
                documentation=element_documentation(
                    'Construct a TLS 1.3 server configuration with automatic HTTP/1.1 + HTTP/2 ALPN.',
                    category='Web', returns='ServerConfig with TLS enabled.'))
def _server_config_with_tls(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    host, port, tls, limits, origin, hosts, proxies, mode, protocols = args
    if not isinstance(tls, ObjectValue) or tls.type_name != 'web.TLS':
        raise core.ConfigError('Expected web.TLS')
    return (ObjectValue('web.ServerConfig', {
        'host': host, 'port': port,
        'tls': ObjectValue('Some', {'value': _duplicate_occurrence(tls)}),
        'limits': ObjectValue('Some', {'value': _duplicate_occurrence(limits)}),
        'publicOrigin': _duplicate_occurrence(origin),
        'allowedHosts': _duplicate_occurrence(hosts),
        'trustedProxies': _duplicate_occurrence(proxies),
        'proxyMode': _duplicate_occurrence(mode),
        'protocols': _duplicate_occurrence(protocols),
    }),)



_DEFAULT_SERVER_CONFIG = (ElementNode(Symbol('nativeDefaultServerConfig', ('std', 'web'))),)


@stdlib_element('nativeDefaultServerConfig', (), (_SERVER_CONFIG,), exported=False)
def _native_default_server_config(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (ObjectValue('web.ServerConfig', {'host': '127.0.0.1',
                                            'port': RuntimeNumber(8080),
                                            'tls': ObjectValue('None', {}),
                                            'limits': ObjectValue('None', {}),
                                            'publicOrigin': ObjectValue('None', {}),
                                            'allowedHosts': ObjectValue('None', {}),
                                            'trustedProxies': ObjectValue('None', {}),
                                            'proxyMode': ObjectValue('None', {}),
                                            'protocols': ObjectValue('None', {})}),)


@stdlib_element('run', (_APP, _SERVER_CONFIG), (T.N(Symbol('None')),),
                param_names=('app', 'config'),
                param_defaults=(None, _DEFAULT_SERVER_CONFIG),
                documentation=element_documentation(
                    'Block in the caller scope serving HTTP; SIGINT/SIGTERM initiate '
                    'shutdown; TLS 1.3 negotiates HTTP/1.1 and HTTP/2 using ALPN. HTTP/3 remains unsupported.',
                    category='Web', returns='None on graceful stop.',
                    examples=(('import {std.web}\nweb.run(web.App, web.ServerConfig(port = 8080))', None),)))
def _run(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return _run_http1(args, ctx)


# A private native lease registry keeps each live listener attached to exactly
# one Valiance @nodup ServerHandle. Only the owning handle may shut it down.
# Server startup is NOT registered in module-global App/router state.
_SERVER_LEASES: dict[int, Any] = {}
_SERVER_LEASES_LOCK = RLock()
_SERVER_HANDLE_RUNTIME = ObjectRuntimeType(
    destructor_name='web.ServerHandle::~ServerHandle',
    dup_error='A web.ServerHandle owns its listener and cannot be duplicated',
    field_order=('token',),
)
_SERVER_ERROR_RUNTIME = ObjectRuntimeType(accepted_names=('Err', 'web.ServerError'),
                                          field_order=('message',))


def _server_error(message: str, *, stopped: bool = False) -> ObjectValue:
    return ObjectValue('web.ServerError.Stopped' if stopped else 'web.ServerError.Failed',
                       {'message': message}, runtime_type=_SERVER_ERROR_RUNTIME)


def _lease_token(value: Any) -> int:
    if not isinstance(value, ObjectValue) or value.type_name != 'web.ServerHandle':
        raise ValueError('Expected owned web.ServerHandle')
    return int(value.fields['token'])


def _close_server_token(token: int, grace_seconds: float | None = None) -> tuple[Any, int, int, int, float] | None:
    with _SERVER_LEASES_LOCK:
        handle = _SERVER_LEASES.pop(token, None)
    if handle is None:
        return None
    started = monotonic()
    try:
        handle.shutdown(grace_seconds=grace_seconds)
    finally:
        duration = monotonic() - started
    accepted, completed = handle.counts
    return handle, accepted, completed, handle.cancelled, duration


def _stop_report(accepted: int, completed: int, cancelled: int, seconds: float,
                 diagnostics: dict[str, int] | None = None) -> ObjectValue:
    stats = diagnostics or {}
    return ObjectValue('web.ServerStopReport', {
        'reason': ObjectValue('web.ServerStopReason.Requested', {}),
        'accepted': RuntimeNumber(accepted),
        'completed': RuntimeNumber(completed),
        'cancelled': RuntimeNumber(cancelled),
        'gracefulSeconds': RuntimeNumber(seconds),
        'webSocketCancellationSignals': RuntimeNumber(stats.get('cancelled', 0)),
        'webSocketCallbacksTimedOut': RuntimeNumber(stats.get('timed_out', 0)),
        'webSocketOwnedTasksUnjoined': RuntimeNumber(stats.get('unjoined_owned_tasks', 0)),
        'webSocketTransportSocketsOpen': RuntimeNumber(stats.get('transport_sockets_open', 0)),
        'webSocketTransportBytesRemaining': RuntimeNumber(stats.get('transport_buffered_bytes', 0)),
    })


@stdlib_element('nativeCloseServer', (T.Int,), (), exported=False,
                param_names=('token',))
def _native_close_server(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    # Destructor cleanup is intentionally idempotent.
    _close_server_token(int(args[0]))
    return ()


@stdlib_element('serve', (_APP, _SERVER_CONFIG),
                (T.Result(_SERVER_HANDLE, _SERVER_ERROR),),
                param_names=('app', 'config'),
                param_defaults=(None, _DEFAULT_SERVER_CONFIG),
                documentation=element_documentation(
                    'Start an owned HTTP/1.1 listener, optionally with TLS 1.3 and ALPN HTTP/2.',
                    category='Web', returns='An exclusive ServerHandle or ServerError.',
                    examples=(('import {std.web}\nweb.serve(web.App, web.ServerConfig(port = 0))', None),)))
def _serve_owned(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    from valiance.std import _web_http1
    app, config = args
    try:
        host = config.fields['host']
        port = int(config.fields['port'])
        handle = _web_http1.serve(_to_core_config(app, ctx), host=host, port=port,
                                  tls=_core_tls(config), limits=_core_limits(config),
                                  network_policy=_core_network_policy(config),
                                  protocols=_core_protocols(config))
    except (core.ConfigError, OSError, ValueError, TypeError) as exc:
        return (_server_error(str(exc)),)
    with _SERVER_LEASES_LOCK:
        token = randbits(112) or 1
        while token in _SERVER_LEASES:
            token = randbits(112) or 1
        _SERVER_LEASES[token] = handle
    return (ObjectValue('web.ServerHandle', {'token': RuntimeNumber(token)},
                        runtime_type=_SERVER_HANDLE_RUNTIME),)


@stdlib_element('boundAddresses', (_SERVER_HANDLE,), (T.ExactList(_BOUND_ENDPOINT),),
                param_names=('server',),
                documentation=element_documentation(
                    'Read the bound TCP endpoint of an HTTP/1.1 development server.',
                    category='Web', returns='BoundEndpoint list.',
                    examples=(('import {std.web}\n$server = web.serve(web.App, web.ServerConfig(port = 0)) ?', None),)))
def _bound_addresses(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    with _SERVER_LEASES_LOCK:
        handle = _SERVER_LEASES.get(_lease_token(args[0]))
    if handle is None:
        raise core.ConfigError('Server has been closed')
    address, port = handle.bound_address
    return (ListValue([ObjectValue('web.BoundEndpoint', {
        'address': str(address), 'port': RuntimeNumber(port),
        'protocol': 'HTTP/1.1', 'tls': TaggedValue(RuntimeNumber(int(handle.tls_enabled)), T.Boolean.tags),
    })], runtime_rank=1),)


@stdlib_element('serverStats', (_SERVER_HANDLE,), (_SERVER_STATS,),
                param_names=('server',),
                documentation=element_documentation(
                    'Read a bounded, read-only snapshot of listener admission and '
                    'resource counters; no unauthenticated metrics endpoint is created.',
                    category='Web', returns='ServerStats snapshot.'))
def _server_stats(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    with _SERVER_LEASES_LOCK:
        handle = _SERVER_LEASES.get(_lease_token(args[0]))
    if handle is None:
        raise core.ConfigError('Server has been closed')
    snapshot = handle.resource_snapshot()
    accepted, completed = handle.counts
    return (ObjectValue('web.ServerStats', {
        'accepted': RuntimeNumber(accepted),
        'completed': RuntimeNumber(completed),
        'cancelled': RuntimeNumber(handle.cancelled),
        'activeRequests': RuntimeNumber(snapshot['active_requests']),
        'activeWebSockets': RuntimeNumber(snapshot['active_websockets']),
        'activeConnections': RuntimeNumber(snapshot['active_connections']),
        'activeHttp2Connections': RuntimeNumber(snapshot['active_h2_connections']),
        'activeTransportWorkers': RuntimeNumber(snapshot['active_connection_workers']),
        'bufferedHttp2Bytes': RuntimeNumber(snapshot['reserved_bytes']),
        'peakBufferedHttp2Bytes': RuntimeNumber(snapshot['reserved_bytes_peak']),
        'tlsHandshakesActive': RuntimeNumber(snapshot['tls_handshakes_active']),
        'tlsHandshakesFailed': RuntimeNumber(snapshot['tls_handshakes_failed']),
    }),)


@stdlib_element('reloadTLS', (_SERVER_HANDLE, _TLS),
                (T.Result(T.N(Symbol('None')), _SERVER_ERROR),),
                param_names=('server', 'tls'),
                documentation=element_documentation(
                    'Atomically rotate validated TLS 1.3 credentials on a TLS listener; '
                    'existing connections retain their original negotiated identity.',
                    category='Web', returns='None or ServerError.'))
def _reload_tls(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    server, tls = args
    try:
        with _SERVER_LEASES_LOCK:
            handle = _SERVER_LEASES.get(_lease_token(server))
        if handle is None:
            return (_server_error('Server has been closed', stopped=True),)
        from valiance.std._web_http1 import TLSConfig
        config = TLSConfig(tls.fields['certificateFile'], tls.fields['privateKeyFile'])
        handle.reload_tls(config)
        return (ObjectValue('None', {}),)
    except (core.ConfigError, OSError, ValueError, TypeError) as exc:
        # Never return certificate material or private-key content.
        return (_server_error(str(exc)),)


@stdlib_element('shutdown', (_SERVER_HANDLE,),
                (T.Result(_SERVER_STOP_REPORT, _SERVER_ERROR),),
                param_names=('server',),
                documentation=element_documentation(
                    'Consume an owned development server handle, close its listener and join workers.',
                    category='Web', returns='ServerStopReport or ServerError.',
                    examples=(('import {std.web}\n$server = web.serve(web.App, web.ServerConfig(port = 0)) ?\nweb.shutdown $server', None),)))
def _shutdown_server(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        stopped = _close_server_token(_lease_token(args[0]))
        if stopped is None:
            return (_server_error('Server was already closed', stopped=True),)
        handle, accepted, completed, cancelled, duration = stopped
        return (_stop_report(accepted, completed, cancelled, duration,
                             handle.stop_diagnostics),)
    except Exception as exc:
        return (_server_error(str(exc)),)


@stdlib_element('shutdownWithin', (_SERVER_HANDLE, T.Int),
                (T.Result(_SERVER_STOP_REPORT, _SERVER_ERROR),),
                param_names=('server', 'graceSeconds'),
                documentation=element_documentation(
                    'Consume an owned HTTP/1.1 server handle with an explicit grace timeout in seconds.',
                    category='Web', returns='ServerStopReport or ServerError.'))
def _shutdown_server_with_grace(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        grace = int(args[1])
        if grace < 0 or grace > 86400:
            return (_server_error('Invalid graceful shutdown interval'),)
        stopped = _close_server_token(_lease_token(args[0]), grace)
        if stopped is None:
            return (_server_error('Server was already closed', stopped=True),)
        handle, accepted, completed, cancelled, duration = stopped
        return (_stop_report(accepted, completed, cancelled, duration,
                             handle.stop_diagnostics),)
    except Exception as exc:
        return (_server_error(str(exc)),)


@stdlib_element('wait', (_SERVER_HANDLE,),
                (T.Result(_SERVER_STOP_REPORT, _SERVER_ERROR),),
                param_names=('server',),
                documentation=element_documentation(
                    'Wait for externally initiated server shutdown and consume its owned handle.',
                    category='Web', returns='ServerStopReport or ServerError.',
                    examples=(('import {std.web}\nweb.run(web.App)', None),)))
def _wait_server(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        token = _lease_token(args[0])
        with _SERVER_LEASES_LOCK:
            handle = _SERVER_LEASES.get(token)
        if handle is None:
            return (_server_error('Server was already closed', stopped=True),)
        handle.wait()
        stopped = _close_server_token(token)
        if stopped is None:
            return (_server_error('Server was already closed', stopped=True),)
        handle, accepted, completed, cancelled, duration = stopped
        return (_stop_report(accepted, completed, cancelled, duration,
                             handle.stop_diagnostics),)
    except Exception as exc:
        return (_server_error(str(exc)),)


def _status_code(value: ObjectValue) -> int:
    if not isinstance(value, ObjectValue) or not value.type_name.startswith('web.Status.'):
        raise core.WebError('InvalidResponse', 'Expected web.Status')
    name = value.type_name.rsplit('.', 1)[-1]
    if name == 'Custom':
        code = int(value.fields['code'])
        if not 100 <= code <= 599 or code == 306:
            raise core.WebError('InvalidResponse', 'Invalid custom HTTP status')
        return code
    try:
        return core._STATUS_CODES[name]
    except KeyError as exc:
        raise core.WebError('InvalidResponse', 'Unknown HTTP status') from exc


@stdlib_element('withStatus', (_RESPONSE, _nominal('Status')), (_RESPONSE,),
                param_names=('response', 'status'),
                documentation=element_documentation('Immutably replace the HTTP response status.',
                    category='Web', returns='New Response value with validated status.',
                    examples=(('import {std.web}\nweb.withStatus(web.Response(body = "ok"), web.Status.Created)', None),)))
def _with_status(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    response, status = args
    return (_wrap_response(replace(_unwrap_response(response), status=_status_code(status))),)


# Cookie request utilities use a distinct nominal type because Cookie header
# values are not query strings (notably, + and % are not decoded).
def _wrap_cookie_pairs(pairs: core.Params) -> ObjectValue:
    return ObjectValue('web.CookiePairs', {
        'names': _optional_list([name for name, _ in pairs.pairs]),
        'values': _optional_list([value for _, value in pairs.pairs]),
    })


for _req_type, _req_label in ((_REQUEST, 'common'), *(
    (_nominal(f'{title}Request'), title.lower()) for _, title in _METHODS)):
    def _get_cookies(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        req = args[0]
        if not req.type_name.startswith('web.Request.'):
            req = ObjectValue('web.Request.' + req.type_name.rsplit('.', 1)[-1], req.fields)
        try:
            return (_wrap_cookie_pairs(core.cookies(_unwrap_request(req))),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _get_cookies.__name__ = f'_cookies_{_req_label}'
    _get_cookies = stdlib_element(
        'cookies', (_req_type,), (T.Result(_COOKIE_PAIRS, _ERROR),),
        param_names=('request',),
        documentation=element_documentation(
            'Parse ordered Cookie request pairs without choosing duplicate names.',
            category='Web', returns='CookiePairs or InvalidCookie.',
            examples=(('import {std.web}\nweb.cookies(web.testRequest("GET", "/"))', None),)),
    )(_get_cookies)
    globals()[_get_cookies.__name__] = _get_cookies

    def _get_cookie(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        req, name = args
        if not req.type_name.startswith('web.Request.'):
            req = ObjectValue('web.Request.' + req.type_name.rsplit('.', 1)[-1], req.fields)
        try:
            return (_string_optional(core.cookie(_unwrap_request(req), name)),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _get_cookie.__name__ = f'_cookie_{_req_label}'
    _get_cookie = stdlib_element(
        'cookie', (_req_type, T.String), (T.Result(T.optional(T.String), _ERROR),),
        param_names=('request', 'name'),
        documentation=element_documentation(
            'Read one Cookie value, reporting malformed or ambiguous names.',
            category='Web', returns='Optional value or Cookie error.',
            examples=(('import {std.web}\nweb.cookie(web.testRequest("GET", "/"), "sid")', None),)),
    )(_get_cookie)
    globals()[_get_cookie.__name__] = _get_cookie

del _get_cookie, _get_cookies


for _kind in ('Get', 'Head'):
    def _conditional(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        req, response = args
        if not req.type_name.startswith('web.Request.'):
            req = ObjectValue('web.Request.' + req.type_name.rsplit('.', 1)[-1], req.fields)
        try:
            return (_wrap_response(core.conditional(_unwrap_request(req), _unwrap_response(response))),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _conditional.__name__ = f'_conditional_{_kind.lower()}'
    _conditional = stdlib_element(
        'conditional', (_nominal(f'{_kind}Request'), _RESPONSE),
        (T.Result(_RESPONSE, _ERROR),),
        param_names=('request', 'response'),
        documentation=element_documentation(
            'Apply safe GET/HEAD conditional representation selection.',
            category='Web', returns='Original response or 304 Not Modified.',
            examples=(('import {std.web}\n'
                       '$app = web.App\n'
                       '$app := web.get("/"): fn(req: web.GetRequest) =>\n'
                       '  web.conditional($req, web.withETag(web.Response(body = "ok"), "v1"))\n'
                       'end', None),),
        ),
    )(_conditional)
    globals()[_conditional.__name__] = _conditional

del _conditional


@stdlib_element('withEarlyHints', (_RESPONSE, T.ExactList(T.String)),
                (T.Result(_RESPONSE, _ERROR),),
                param_names=('response', 'links'),
                documentation=element_documentation(
                    'Attach validated preload Link metadata for one HTTP 103 Early Hints response.',
                    category='Web', returns='Response or InvalidHeader.',
                    examples=(('import {std.web}\n'
                               'web.withEarlyHints(web.Response(body = "ok"), ["</style.css>; rel=preload; as=style"])', None),)))
def _with_early_hints(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        return (_wrap_response(core.with_early_hints(_unwrap_response(args[0]), list(args[1]))),)
    except (core.WebError, UnicodeError) as exc:
        return (_web_error_value(exc if isinstance(exc, core.WebError) else
                                 core.WebError('InvalidHeader', 'Invalid preload Link encoding')),)


@stdlib_element('withTrailers', (_RESPONSE, _HEADERS), (T.Result(_RESPONSE, _ERROR),),
                param_names=('response', 'trailers'),
                documentation=element_documentation(
                    'Attach validated HTTP trailing fields to an owned stream. '
                    'H1 emits a Trailer announcement and chunked terminator.',
                    category='Web', returns='Response or InvalidHeader.'))
def _with_trailers(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    response = None
    try:
        response = _unwrap_response(args[0])
        return (_wrap_response(core.with_trailers(response, _unwrap_headers(args[1]))),)
    except core.WebError as exc:
        if response is not None and isinstance(response.body, core.StreamBody):
            response.body.close()
        return (_web_error_value(exc),)


@stdlib_element('withLastModified', (_RESPONSE, T.Int), (_RESPONSE,),
                param_names=('response', 'unixSeconds'),
                documentation=element_documentation(
                    'Attach a Last-Modified validator formatted as an HTTP date.',
                    category='Web', returns='New Response.',
                    examples=(('import {std.web}\n'
                               'web.withLastModified(web.Response(body = "ok"), 0)', None),)))
def _with_last_modified(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    return (_wrap_response(core.with_last_modified(_unwrap_response(args[0]), int(args[1]))),)


def _optional_scalar(value: Any) -> Any | None:
    # Optional object fields may hold a direct successful value: the runtime
    # only boxes absence (None) and some generic/union field operations.
    if not isinstance(value, ObjectValue):
        return value
    if value.type_name == 'None':
        return None
    if value.type_name != 'Some':
        raise TypeError('Expected Some or None')
    return value.fields['value']


for _req_type, _label in ((_REQUEST, 'common'), *(
    (_nominal(f'{title}Request'), title.lower()) for _, title in _METHODS)):
    def _preconditions(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        request, given = args
        if not request.type_name.startswith('web.Request.'):
            request = ObjectValue('web.Request.' + request.type_name.rsplit('.', 1)[-1], request.fields)
        try:
            state = core.Validators(
                etag=_optional_scalar(given.fields['etag']),
                last_modified=_optional_scalar(given.fields['lastModified']),
                exists=bool(int(given.fields['exists'])),
            )
            core.preconditions(_unwrap_request(request), state)
            # Preserve the successful Result[None, Error] branch explicitly:
            # a bare None would be interpreted as an optional short-circuit
            # by Valiance's `?` control-flow instruction.
            return (ObjectValue('OK', {'value': ObjectValue('None', {})}),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _preconditions.__name__ = f'_preconditions_{_label}'
    _preconditions = stdlib_element(
        'preconditions', (_req_type, _VALIDATORS),
        (T.Result(T.N(Symbol('None')), _ERROR),),
        param_names=('request', 'validators'),
        documentation=element_documentation(
            'Evaluate strong If-Match, If-Unmodified-Since and mutation If-None-Match before state changes.',
            category='Web', returns='None on success or a typed HTTP error.',
            examples=(('import {std.web}\nweb.preconditions(web.testRequest("PUT", "/"), web.Validators)', None),)),
    )(_preconditions)
    globals()[_preconditions.__name__] = _preconditions

del _preconditions


_DEFAULT_PARAMS = (ElementNode(Symbol('nativeEmptyParams', ('std', 'web'))),)


@stdlib_element('nativeEmptyParams', (), (_PARAMS,), exported=False)
def _native_empty_params(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del args, ctx
    return (_wrap_params(core.Params()),)


def _method_name(value: ObjectValue) -> str:
    if not isinstance(value, ObjectValue) or not value.type_name.startswith('web.Method.'):
        raise core.ConfigError('Expected web.Method')
    method = value.type_name.rsplit('.', 1)[-1]
    if method not in (item.upper() for item, _ in _METHODS):
        raise core.ConfigError('Unsupported route method')
    return method


for _config, _kind in ((_APP, 'app'), (_ROUTER, 'router')):
    def _route_url(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        target, method, template, params, query = args
        try:
            compiled = _to_core_config(target, ctx)
            result = core.route_url(compiled, _method_name(method), template, dict(params),
                                    _unwrap_params(query))
            return (result,)
        except (core.ConfigError, core.WebError, ValueError, TypeError) as exc:
            return (_config_error_value(exc),)

    _route_url.__name__ = f'_route_url_{_kind}'
    _route_url = stdlib_element(
        'routeUrl', (_config, _nominal('Method'), T.String,
                     T.N(Symbol('Dict'), T.String, T.String), _PARAMS),
        (T.Result(T.String, _CONFIG_ERROR),),
        param_names=('appOrRouter', 'method', 'template', 'params', 'query'),
        param_defaults=(None, None, None, None, _DEFAULT_PARAMS),
        documentation=element_documentation(
            'Generate a validated relative URL from a registered route identity.',
            category='Web', returns='Relative URL or ConfigError.',
            examples=(('import {std.web}\n'
                       '$app = web.App\n'
                       '$app := web.get("/p/{id}"): fn(req: web.GetRequest) => "ok" end\n'
                       'web.routeUrl($app, web.Method.GET, "/p/{id}", dict{"id" => "1"})', None),)),
    )(_route_url)
    globals()[_route_url.__name__] = _route_url

del _route_url

# Explicit JSON responses: reuse the native codec services installed by
# std.json instead of a second HTTP-owned JSON encoder.
@stdlib_element(
    'jsonResponse', (T.V('T'), _nominal('Status'), _HEADERS),
    (T.Result(_RESPONSE, T.N(Symbol('ValueError'))),),
    param_names=('value', 'status', 'headers'),
    param_defaults=(None, (ElementNode(Symbol('OK', ('web', 'Status'))),), _DEFAULT_HEADERS),
    documentation=element_documentation(
        'Encode a value using std.json and return a JSON HTTP response.',
        category='Web', returns='Response or std.json ValueError.',
        examples=(('import {std.web}\nweb.jsonResponse(record{name => "Ada"})', None),),
    ),
)
def _json_response(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    from valiance.std.json import encode_host
    try:
        text = encode_host(args[0], ctx.type_services)
    except (ValueError, RecursionError) as exc:
        return (ObjectValue('ValueError', {'message': str(exc)}),)
    status = _status_code(args[1])
    headers = _unwrap_headers(args[2]).with_header('Content-Type', 'application/json')
    return (_wrap_response(core.Response(status, text.encode('utf-8'), headers)),)


# A strictly validated JSON representation reader. It intentionally performs
# HTTP media/byte validation, leaving schema decoding to std.json.decode[T]
# until the language's output-only generic request overload is supported.
for _json_method in ('Post', 'Put', 'Patch', 'Query'):
    _json_request_type = _nominal(f'{_json_method}Request')

    def _json_body_text(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
        del ctx
        request, = args
        kind = request.type_name.rsplit('.', 1)[-1]
        try:
            req = _unwrap_request(ObjectValue(f'web.Request.{kind}', request.fields))
            media = core.content_type(req)
            if (media is None or (media[0] != 'application/json' and not
                (media[0].startswith('application/') and media[0].endswith('+json')))):
                raise core.WebError('UnsupportedMediaType', 'Unsupported Media Type')
            if media[1].get('charset', 'utf-8').lower() != 'utf-8':
                raise core.WebError('UnsupportedMediaType', 'Unsupported Media Type')
            try:
                return (core.body_bytes(req).decode('utf-8', 'strict'),)
            except UnicodeDecodeError as exc:
                raise core.WebError('InvalidJson', 'Invalid JSON') from exc
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _json_body_text.__name__ = f'_body_json_text_{_json_method.lower()}'
    _json_body_text = stdlib_element(
        'bodyJsonText', (_json_request_type,), (T.Result(T.String, _ERROR),),
        param_names=('request',),
        documentation=element_documentation(
            'Read a bounded UTF-8 body only when the request has a JSON media type. '
            'Parse with std.json.decode[T].',
            category='Web', returns='Validated JSON text or web.Error.',
            examples=(('import {std.web}\n$app = web.App\n$app := web.post("/json"): fn(req: web.PostRequest) -> String | web.Error => web.bodyJsonText($req) end', None),),
        ),
    )(_json_body_text)
    globals()[_json_body_text.__name__] = _json_body_text

del _json_body_text




# Bounded file helpers. These use file descriptors and exact octets; they do
# not claim to implement the future lazy @nodup file lease representation.
for _name, _operation, _args in (
    ('file', core.file, (T.String,)),
    ('page', lambda path: core.file(path, html=True), (T.String,)),
    ('download', core.download, (T.String, T.String)),
):
    def _file_helper(args: tuple[Any, ...], ctx: RuntimeContext,
                     operation: Any = _operation) -> tuple[Any, ...]:
        del ctx
        try:
            return (_wrap_response(operation(*args)),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _file_helper.__name__ = f'_{_name}_response'
    _file_helper = stdlib_element(
        _name, _args, (T.Result(_RESPONSE, _ERROR),),
        param_names=('path', 'filename') if _name == 'download' else ('path',),
        documentation=element_documentation(
            'Read a regular file into a bounded binary Response (16 MiB development limit).',
            category='Web', returns='Response or web.Error.',
            examples=((f'import {{std.web}}\nweb.{_name}("public/example.txt"' +
                       (', "example.txt")' if _name == 'download' else ')'), None),),
        ),
    )(_file_helper)
    globals()[_file_helper.__name__] = _file_helper

del _file_helper


@stdlib_element('staticRouter', (T.String,), (T.Result(_ROUTER, _CONFIG_ERROR),),
                param_names=('root',),
                documentation=element_documentation(
                    'Build a confined GET/HEAD static Router using no-follow dirfd traversal.',
                    category='Web', returns='Router or ConfigError.',
                    examples=(('import {std.web}\nweb.staticRouter("public/assets")', None),)))
def _static_router(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    try:
        root = core.static_root(args[0])
    except core.ConfigError as exc:
        return (_config_error_value(exc),)
    handler = ObjectValue('web._StaticFileHandler', {'root': root})
    route = ObjectValue('web.Route.Get', {
        'path': '/{*path}', 'handler': handler, 'middleware': ObjectValue('None', {}),
    })
    return (ObjectValue('web.Router', {
        'routes': _optional_list([route]), 'middleware': ObjectValue('None', {}),
    }),)


# std.path.Path keeps POSIX filename bytes losslessly. The bridge converts its
# native spelling with os.fsdecode (surrogateescape on POSIX), rather than
# calling the potentially lossy user-visible Path-to-String conversion.
_PATH = T.N(Symbol('Path', ('path',)))


def _native_file_path(value: ObjectValue) -> str:
    import os
    from valiance.std.path import native_path
    return os.fsdecode(native_path(value).native)


for _name, _operation, _args in (
    ('file', core.file, (_PATH,)),
    ('page', lambda path: core.file(path, html=True), (_PATH,)),
    ('download', core.download, (_PATH, T.String)),
):
    def _path_file_helper(args: tuple[Any, ...], ctx: RuntimeContext,
                          operation: Any = _operation) -> tuple[Any, ...]:
        del ctx
        try:
            return (_wrap_response(operation(_native_file_path(args[0]), *args[1:])),)
        except core.WebError as exc:
            return (_web_error_value(exc),)

    _path_file_helper.__name__ = f'_{_name}_path_response'
    _path_file_helper = stdlib_element(
        _name, _args, (T.Result(_RESPONSE, _ERROR),),
        param_names=('path', 'filename') if _name == 'download' else ('path',),
        documentation=element_documentation(
            'Serve an application-selected std.path.Path as a bounded binary response.',
            category='Web', returns='Response or web.Error.',
            examples=((f'import {{std.web, std.path.Path}}\nweb.{_name}(Path("public/example.txt")' +
                       (', "example.txt")' if _name == 'download' else ')'), None),),
        ),
    )(_path_file_helper)
    globals()[_path_file_helper.__name__] = _path_file_helper

del _path_file_helper


@stdlib_element('staticRouter', (_PATH,), (T.Result(_ROUTER, _CONFIG_ERROR),),
                param_names=('root',),
                documentation=element_documentation(
                    'Build a confined static Router from a std.path.Path root.',
                    category='Web', returns='Router or ConfigError.',
                    examples=(('import {std.web, std.path.Path}\n'
                               'web.staticRouter(Path("public/assets"))', None),)))
def _static_router_path(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    return _static_router((_native_file_path(args[0]),), ctx)

# Common, generic bridge for the source-level bodyJson[T] wrapper. Valiance
# currently cannot resolve a source-defined output-generic wrapper against
# four method-specific native overloads, so the raw bridge accepts the
# inferred nominal request type and enforces the body-capable method policy.
@stdlib_element('bodyJsonRaw', (T.V('R'),), (T.Result(T.String, _ERROR),), exported=False)
def _body_json_raw(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    del ctx
    request, = args
    try:
        if not isinstance(request, ObjectValue) or not request.type_name.startswith(
            ('web.PostRequest', 'web.PutRequest', 'web.PatchRequest', 'web.QueryRequest',
             'web.Request.')
        ):
            raise core.WebError('BodyUnavailable', 'Unsupported request method')
        kind = request.type_name.rsplit('.', 1)[-1]
        source = (request if request.type_name.startswith('web.Request.') else
                  ObjectValue(f'web.Request.{kind}', request.fields))
        req = _unwrap_request(source)
        if req.method not in ('POST', 'PUT', 'PATCH', 'QUERY'):
            raise core.WebError('BodyUnavailable', 'Unsupported request method')
        media = core.content_type(req)
        if (media is None or (media[0] != 'application/json' and not (
                media[0].startswith('application/') and media[0].endswith('+json')))
                or media[1].get('charset', 'utf-8').lower() != 'utf-8'):
            raise core.WebError('UnsupportedMediaType', 'Unsupported Media Type')
        try:
            return (core.body_bytes(req).decode('utf-8', 'strict'),)
        except UnicodeDecodeError as exc:
            raise core.WebError('InvalidJson', 'Malformed JSON request content') from exc
    except core.WebError as exc:
        return (_web_error_value(exc),)


@stdlib_element('decodeBodyJson', (T.String, T.String),
                (T.Result(T.V('T'), _ERROR),), exported=False)
def _decode_body_json(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    # The public wrapper supplies typeInfo[T].name. Reuse the exact same
    # checked schema reconstruction, private-field checks, and error handling
    # as std.json.decode[T]; never introduce a separate JSON deserializer.
    from valiance.std.json import decode_host
    try:
        return (decode_host(args[0], args[1], ctx.type_services),)
    except (ValueError, RecursionError):
        return (_web_error_value(core.WebError('InvalidJson', 'Invalid JSON')),)
