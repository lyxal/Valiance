"""Escaped HTML nodes and composable element constructors for std.web.

Only this module signs markup stored in a Node. Rendering verifies the seal so
constructing a similarly shaped managed object cannot bypass escaping. This
module intentionally does not validate the full HTML content model.
"""

from __future__ import annotations

import hashlib
import hmac
import html as _html
import re
import secrets
import unicodedata
from urllib.parse import urlsplit
from typing import Any

import valiance.vtypes as T
from valiance.elements.builtins import RuntimeContext
from valiance.elements.documentation import ElementDocumentation, element_documentation
from valiance.elements.stdlib_native import stdlib_element
from valiance.runtime.runtime_values import ObjectValue, TaggedValue, BorrowedValue
from valiance.vtypes.symbols import Symbol

_NODE = T.N(Symbol('Node', ('html',)))
_NODE_LIST = T.ExactList(_NODE)
_ATTRIBUTES = T.N(Symbol('Dict'), T.String, T.String)
_SECRET = secrets.token_bytes(32)
_TAG_NAME = re.compile(r'[a-z][a-z0-9-]*\Z', re.ASCII)
_ATTR_NAME = re.compile(r'[a-zA-Z_:][a-zA-Z0-9_:.-]*\Z', re.ASCII)
_VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
                   'meta', 'param', 'source', 'track', 'wbr'))
# Content inside these elements follows special parsing rules; ordinary escaped
# text nodes are not enough to make user-controlled data safe in these contexts.
_RESTRICTED = frozenset(('script', 'style', 'iframe', 'object', 'embed', 'template',
                         'svg', 'math', 'base', 'link', 'meta', 'plaintext', 'xmp',
                         'listing', 'noscript', 'noembed', 'noframes', 'frameset',
                         'frame', 'portal'))
_TEXT_ONLY = frozenset(('title', 'textarea'))
_BOOLEAN_ATTRIBUTES = frozenset((
    'allowfullscreen', 'async', 'autofocus', 'autoplay', 'checked', 'controls',
    'default', 'defer', 'disabled', 'formnovalidate', 'hidden', 'inert',
    'ismap', 'itemscope', 'loop', 'multiple', 'muted', 'nomodule', 'novalidate',
    'open', 'playsinline', 'readonly', 'required', 'reversed', 'selected',
))
_DANGEROUS_URL_LISTS = frozenset(('srcset', 'imagesrcset', 'ping'))
_SAFE_STYLE_PROPERTIES = frozenset((
    'background-color', 'border', 'border-color', 'border-radius', 'border-style',
    'border-width', 'color', 'display', 'font-size', 'font-weight', 'gap',
    'height', 'line-height', 'margin', 'margin-bottom', 'margin-left',
    'margin-right', 'margin-top', 'max-height', 'max-width', 'min-height',
    'min-width', 'opacity', 'overflow', 'padding', 'padding-bottom',
    'padding-left', 'padding-right', 'padding-top', 'text-align',
    'vertical-align', 'width',
))
_SAFE_STYLE_VALUE = re.compile(r'[A-Za-z0-9 .#%+\-/]+\Z', re.ASCII)
_META_NAMES = frozenset(('description', 'viewport', 'robots', 'author',
                         'theme-color', 'color-scheme', 'application-name',
                         'referrer'))
_LINK_RELS = frozenset(('stylesheet', 'icon', 'canonical', 'alternate',
                        'preload', 'preconnect', 'dns-prefetch'))
_LANG = re.compile(r'[A-Za-z]{2,8}(?:-[A-Za-z0-9]{1,8})*\Z', re.ASCII)
_ATTR_TOKEN = re.compile(r'\s+([a-zA-Z_:][a-zA-Z0-9_:.-]*)(?:="[^"]*")?', re.ASCII)
_URL_ATTRS = frozenset(('href', 'src', 'action', 'formaction', 'poster', 'cite',
                        'background', 'xlink:href'))


def _markup_node(markup: str, tag: str = '') -> ObjectValue:
    if not isinstance(markup, str):
        raise ValueError('HTML serialization must produce text')
    # UTF-8 encoder rejects isolated surrogate code points rather than producing
    # inconsistent markup at HTTP serialization time.
    payload = tag.encode('utf-8', 'strict') + b'\0' + markup.encode('utf-8', 'strict')
    seal = hmac.new(_SECRET, payload, hashlib.sha256).hexdigest()
    return ObjectValue('html.Node', {'markup': markup, 'seal': seal, 'tag': tag})


def render_node(node: Any) -> str:
    """Return trusted rendered markup; fail closed for forged/invalid nodes."""
    if isinstance(node, BorrowedValue):
        node = node.read()
    if not isinstance(node, ObjectValue) or node.type_name != 'html.Node':
        raise ValueError('Expected an html.Node')
    markup = node.fields.get('markup')
    seal = node.fields.get('seal')
    tag = node.fields.get('tag')
    if not isinstance(markup, str) or not isinstance(seal, str) or not isinstance(tag, str):
        raise ValueError('Invalid html.Node representation')
    payload = tag.encode('utf-8', 'strict') + b'\0' + markup.encode('utf-8', 'strict')
    if not hmac.compare_digest(hmac.new(_SECRET, payload, hashlib.sha256).hexdigest(), seal):
        raise ValueError('Untrusted html.Node; construct nodes through std.html')
    return markup


def _escape_text(value: str) -> str:
    value.encode('utf-8', 'strict')
    # HTML5 has no representation for NUL and forbids most control codepoints.
    if any((ord(char) < 32 and char not in '\t\n\r') or 127 <= ord(char) <= 159
           or ord(char) in (0xfffe, 0xffff) for char in value):
        raise ValueError('Invalid control character in HTML text')
    # quote=True is also safe in attributes; quotes in text are harmless.
    return _html.escape(value, quote=True)


def _check_tag(tag: str) -> str:
    if not isinstance(tag, str) or not _TAG_NAME.fullmatch(tag):
        raise ValueError('Invalid HTML element name')
    if tag in _RESTRICTED:
        raise ValueError('Active or non-HTML elements are not supported by safe builders')
    return tag


def _check_url(item: str) -> None:
    # Browsers normalize URL input; reject whitespace, controls, backslashes,
    # and entity-obfuscated schemes before serializing anything into HTML.
    candidate = _html.unescape(item)
    for value in (item, candidate):
        if any(char.isspace() or char == "\\" or unicodedata.category(char).startswith('C')
               for char in value):
            raise ValueError('Whitespace/control characters in HTML URL')
    if candidate.startswith('//') or candidate.startswith('/\\'):
        raise ValueError('Protocol-relative HTML URLs are unsupported')
    try:
        scheme = urlsplit(candidate).scheme.lower()
    except ValueError as exc:
        raise ValueError('Invalid HTML URL') from exc
    if scheme and scheme not in ('https', 'http', 'mailto', 'tel'):
        raise ValueError('Unsupported HTML URL scheme')
    if scheme in ('https', 'http') and not candidate.lower().startswith(scheme + '://'):
        raise ValueError('HTTP HTML URLs must include // and a host')
    if scheme in ('https', 'http') and not urlsplit(candidate).netloc:
        raise ValueError('HTTP HTML URLs must have a host')
    # A colon before the first slash/query/hash which was not recognized as
    # a URL scheme is ambiguous; refuse it rather than rely on browser repair.
    prefix = re.split(r'[/?#]', candidate, maxsplit=1)[0]
    if ':' in prefix and not scheme:
        raise ValueError('Ambiguous HTML URL scheme')


def _check_web_resource(url: str) -> None:
    """Validate a URL for a stylesheet, script, or form action."""
    _check_url(url)
    if urlsplit(_html.unescape(url)).scheme.lower() in ('mailto', 'tel'):
        raise ValueError('Web resources require an HTTP or relative URL')


def _attributes(value: Any) -> str:
    if isinstance(value, BorrowedValue):
        value = value.read()
    if value is None:
        return ''
    if not hasattr(value, 'items'):
        raise ValueError('Expected a dictionary of HTML attributes')
    parts: list[str] = []
    seen: set[str] = set()
    for name, item in value.items():
        if not isinstance(name, str) or not _ATTR_NAME.fullmatch(name):
            raise ValueError('Invalid HTML attribute name')
        key = name.lower()
        if key in seen:
            raise ValueError('Duplicate HTML attribute')
        seen.add(key)
        if (key.startswith('on') or key in ('srcdoc', 'style', 'xmlns', 'is')
                or key.startswith('xmlns:') or key in _DANGEROUS_URL_LISTS):
            raise ValueError('Active or unvalidated HTML attributes are unsupported')
        if not isinstance(item, str):
            raise ValueError('HTML attributes must be strings')
        item.encode('utf-8', 'strict')
        if key == 'id' and (not item or any(ch.isspace() or unicodedata.category(ch).startswith('C')
                                             for ch in item)):
            raise ValueError('HTML id must be nonempty without spaces or controls')
        if key in _URL_ATTRS:
            _check_url(item)
            if key in ('src', 'poster', 'background'):
                _check_web_resource(item)
        if key in _BOOLEAN_ATTRIBUTES:
            # In HTML, disabled="false" still means disabled. Require an
            # unambiguous presence marker; see withBoolean for conditional use.
            if item not in ('', key):
                raise ValueError('Boolean HTML attribute must be empty or its name')
            parts.append(f' {name}')
        else:
            parts.append(f' {name}="{_escape_text(item)}"')
    return ''.join(parts)


def _element(tag: str, inner: str = '', attrs: Any = None) -> ObjectValue:
    tag = _check_tag(tag)
    attr_text = _attributes(attrs)
    if tag in _TEXT_ONLY and '<' in inner:
        raise ValueError('HTML title/textarea children must be text nodes')
    if tag in _VOID:
        if inner:
            raise ValueError('Void HTML elements cannot contain children')
        return _markup_node(f'<{tag}{attr_text}>', tag)
    return _markup_node(f'<{tag}{attr_text}>{inner}</{tag}>', tag)


def _html_documentation(summary: str, example: str) -> ElementDocumentation:
    """Build reference documentation for an HTML element or helper."""
    return element_documentation(summary, category='HTML', examples=(f'import {{std.html}}\n{example}',))


@stdlib_element('text', (T.String,), (_NODE,), param_names=('value',),
                documentation=_html_documentation('Escape plain text for use as HTML content.', 'html.text("A & B")'))
def _text(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Escape plain text for use as HTML content."""
    del ctx
    return (_markup_node(_escape_text(args[0])),)


@stdlib_element('render', (_NODE,), (T.String,), param_names=('node',),
                documentation=_html_documentation('Render a verified node as an HTML string.', 'html.render(html.p("Hello"))'))
def _render(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Render a verified node as an HTML string."""
    del ctx
    return (render_node(args[0]),)


def _node_items(value: Any) -> list[Any]:
    """Read the children of a list of HTML nodes."""
    if isinstance(value, BorrowedValue):
        value = value.read()
    if isinstance(value, TaggedValue):
        value = value.value
    return list(value)


@stdlib_element('empty', (), (_NODE,),
                documentation=_html_documentation('Create a fragment with no markup.', 'html.empty'))
def _empty(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Render no content without requiring an inferred empty list type."""
    del args, ctx
    return (_markup_node(''),)


@stdlib_element('fragment', (_NODE_LIST,), (_NODE,), param_names=('children',),
                documentation=_html_documentation('Combine child nodes without a wrapper element.', 'html.fragment([html.p("One"), html.p("Two")])'))
def _fragment(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Combine child nodes without a wrapper element."""
    del ctx
    return (_markup_node(''.join(render_node(child) for child in _node_items(args[0]))),)


@stdlib_element('element', (T.String, _NODE_LIST, _ATTRIBUTES), (_NODE,),
                param_names=('name', 'children', 'attributes'),
                documentation=_html_documentation('Create a named element with child nodes and attributes.', 'html.element("main", [html.p("Hello")], dict{"id" => "intro"})'))
def _make_element(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a named element with child nodes and attributes."""
    del ctx
    name, children, attributes = args
    return (_element(name, ''.join(render_node(child) for child in _node_items(children)), attributes),)


def _validated_node(value: Any) -> ObjectValue:
    """Read and verify an HTML node before reusing its markup."""
    if isinstance(value, BorrowedValue):
        value = value.read()
    render_node(value)
    return value


def _opening_attributes(markup: str, tag: str) -> tuple[int, set[str]]:
    """Locate the opening tag boundary, lexing quoted attribute values.

    Markup comes from a sealed builder node, but do not use loose regexes over
    quoted attribute values: whitespace within values is not an attribute.
    """
    prefix = f'<{tag}'
    if not markup.startswith(prefix) or markup[len(prefix):len(prefix) + 1] not in (' ', '>', '\n'):
        raise ValueError('Invalid element node')
    offset = len(prefix)
    names: set[str] = set()
    while offset < len(markup):
        if markup[offset] == '>':
            return offset, names
        match = _ATTR_TOKEN.match(markup, offset)
        if match is None:
            raise ValueError('Invalid element node attributes')
        name = match.group(1).lower()
        if name in names:
            raise ValueError('Duplicate HTML attribute')
        names.add(name)
        offset = match.end()
    raise ValueError('Invalid element node')


def _attach_attributes(value: Any, attributes: Any) -> ObjectValue:
    """Add validated attributes to the opening tag of an existing node."""
    node = _validated_node(value)
    tag = node.fields['tag']
    if not tag:
        raise ValueError('Attributes require a single element node')
    _check_tag(tag)
    markup = node.fields['markup']
    opening_end, existing = _opening_attributes(markup, tag)
    added = _attributes(attributes)
    _, new_names = _opening_attributes(f'<{tag}{added}>', tag)
    if existing & new_names:
        raise ValueError('Duplicate HTML attribute')
    return _markup_node(markup[:opening_end] + added + markup[opening_end:], tag)


@stdlib_element('document', (_NODE,), (_NODE,), param_names=('root',),
                documentation=_html_documentation('Add a standards-mode doctype to an HTML root node.', 'html.document(html.html(html.body("Hello")))'))
def _document(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a standards-mode document from a single root html element."""
    del ctx
    node = _validated_node(args[0])
    markup = node.fields['markup']
    if node.fields['tag'] != 'html':
        raise ValueError('HTML documents require a root html element')
    return (_markup_node('<!doctype html>' + markup),)


@stdlib_element('withAttrs', (_NODE, _ATTRIBUTES), (_NODE,),
                param_names=('node', 'attributes'),
                documentation=_html_documentation('Add attributes to an existing element.', 'html.withAttrs(html.p("Hello"), dict{"id" => "intro"})'))
def _with_attrs(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Add attributes to an existing element."""
    del ctx
    return (_attach_attributes(args[0], args[1]),)


@stdlib_element('withClass', (_NODE, T.String), (_NODE,),
                param_names=('node', 'value'),
                documentation=_html_documentation('Add a class attribute to an existing element.', 'html.withClass(html.p("Hello"), "intro")'))
def _with_class(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Add a class attribute to an existing element."""
    del ctx
    return (_attach_attributes(args[0], {'class': args[1]}),)


def _attach_style(node: Any, properties: Any) -> ObjectValue:
    """Serialize permitted CSS declarations into a style attribute."""
    node = _validated_node(node)
    tag = node.fields['tag']
    if not tag:
        raise ValueError('Styles require a single element node')
    _check_tag(tag)
    if isinstance(properties, BorrowedValue):
        properties = properties.read()
    if not hasattr(properties, 'items') or len(properties) > 32:
        raise ValueError('Expected a bounded dictionary of style properties')
    declarations: list[str] = []
    for name, value in properties.items():
        if name not in _SAFE_STYLE_PROPERTIES or not isinstance(value, str):
            raise ValueError('Unsupported inline style property')
        value.encode('utf-8', 'strict')
        value = value.strip()
        if len(value) > 256 or not _SAFE_STYLE_VALUE.fullmatch(value):
            raise ValueError('Unsafe or invalid inline style value')
        declarations.append(f'{name}: {value}')
    if not declarations:
        return node
    markup = node.fields['markup']
    end, existing = _opening_attributes(markup, tag)
    if 'style' in existing:
        raise ValueError('Duplicate HTML style attribute')
    style = _escape_text('; '.join(declarations))
    return _markup_node(markup[:end] + f' style="{style}"' + markup[end:], tag)


@stdlib_element('withStyle', (_NODE, _ATTRIBUTES), (_NODE,),
                param_names=('node', 'properties'),
                documentation=_html_documentation('Add validated inline CSS properties to an existing element.', 'html.withStyle(html.p("Hello"), dict{"color" => "red"})'))
def _with_style(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Add validated inline CSS properties to an existing element."""
    del ctx
    return (_attach_style(args[0], args[1]),)


@stdlib_element('withId', (_NODE, T.String), (_NODE,),
                param_names=('node', 'value'),
                documentation=_html_documentation('Add an ID attribute to an existing element.', 'html.withId(html.p("Hello"), "intro")'))
def _with_id(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Add an ID attribute to an existing element."""
    del ctx
    if not args[1] or any(ch.isspace() or unicodedata.category(ch).startswith('C')
                          for ch in args[1]):
        raise ValueError('HTML id must be nonempty without spaces or controls')
    return (_attach_attributes(args[0], {'id': args[1]}),)


def _custom_attribute(prefix: str, name: str) -> str:
    """Validate and qualify a data or ARIA attribute name."""
    if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_-]*', name):
        raise ValueError('Invalid HTML custom attribute suffix')
    return prefix + name


@stdlib_element('withData', (_NODE, T.String, T.String), (_NODE,),
                param_names=('node', 'name', 'value'),
                documentation=_html_documentation('Add a custom data attribute to an existing element.', 'html.withData(html.p("Hello"), "topic", "intro")'))
def _with_data(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Add a custom data attribute to an existing element."""
    del ctx
    return (_attach_attributes(args[0], {_custom_attribute('data-', args[1]): args[2]}),)


@stdlib_element('withAria', (_NODE, T.String, T.String), (_NODE,),
                param_names=('node', 'name', 'value'),
                documentation=_html_documentation('Add an ARIA attribute to an existing element.', 'html.withAria(html.p("Hello"), "label", "Introduction")'))
def _with_aria(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Add an ARIA attribute to an existing element."""
    del ctx
    return (_attach_attributes(args[0], {_custom_attribute('aria-', args[1]): args[2]}),)


@stdlib_element('withBoolean', (_NODE, T.String, T.Boolean), (_NODE,),
                param_names=('node', 'name', 'enabled'),
                documentation=_html_documentation('Add a boolean attribute when its condition is true.', 'html.withBoolean(html.button("Continue"), "disabled", true)'))
def _with_boolean(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Add a boolean attribute when its condition is true."""
    del ctx
    node, name, enabled = args
    _validated_node(node)
    if name not in _BOOLEAN_ATTRIBUTES:
        raise ValueError('Unknown boolean HTML attribute')
    if isinstance(enabled, TaggedValue):
        enabled = enabled.value
    return (_attach_attributes(node, {name: ''}) if bool(enabled) else node,)


@stdlib_element('when', (T.Boolean, _NODE), (_NODE,),
                param_names=('condition', 'child'),
                documentation=_html_documentation('Include a child node when a condition is true.', 'html.when(true, html.p("Hello"))'))
def _when(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Include a child node when a condition is true."""
    del ctx
    value, child = args
    node = _validated_node(child)
    if isinstance(value, TaggedValue):
        value = value.value
    return (node if bool(value) else _markup_node(''),)


@stdlib_element('choose', (T.Boolean, _NODE, _NODE), (_NODE,),
                param_names=('condition', 'yes', 'no'),
                documentation=_html_documentation('Select one of two nodes based on a condition.', 'html.choose(true, html.p("Yes"), html.p("No"))'))
def _choose(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Select one of two nodes based on a condition."""
    del ctx
    condition, yes, no = args
    true_node, false_node = _validated_node(yes), _validated_node(no)
    if isinstance(condition, TaggedValue):
        condition = condition.value
    return (true_node if bool(condition) else false_node,)


@stdlib_element('join', (_NODE_LIST, _NODE), (_NODE,),
                param_names=('children', 'separator'),
                documentation=_html_documentation('Join child nodes with a separator node.', 'html.join([html.span("A"), html.span("B")], html.text(", "))'))
def _join(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Join child nodes with a separator node."""
    del ctx
    items = [render_node(child) for child in _node_items(args[0])]
    return (_markup_node(render_node(args[1]).join(items)),)


@stdlib_element('children', (T.String, _NODE_LIST), (_NODE,),
                param_names=('name', 'children'),
                documentation=_html_documentation('Wrap child nodes in a named element.', 'html.children("main", [html.p("Hello")])'))
def _children(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Wrap child nodes in a named element."""
    del ctx
    return (_element(args[0], ''.join(render_node(child) for child in _node_items(args[1]))),)


# Common, readable builders accept either ordinary text or already-safe nodes.
# Use element(...) to combine multiple children and attributes.
_BUILDERS = (
    'html', 'head', 'body', 'title', 'main', 'header', 'footer', 'nav', 'section',
    'article', 'aside', 'div', 'span', 'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'a', 'strong', 'em', 'small', 'code', 'pre', 'blockquote', 'ul', 'ol', 'li',
    'table', 'thead', 'tbody', 'tr', 'th', 'td', 'form', 'label', 'button',
    'textarea', 'select', 'option', 'details', 'summary', 'figure', 'figcaption',
)
for _tag in _BUILDERS:
    for _kind, _arg_type in (('text', T.String), ('node', _NODE)):
        def _builder(args: tuple[Any, ...], ctx: RuntimeContext, tag: str = _tag,
                     kind: str = _kind) -> tuple[Any, ...]:
            """Wrap escaped text or a verified child node in the given tag."""
            del ctx
            return (_element(tag, _escape_text(args[0]) if kind == 'text'
                             else render_node(args[0])),)
        _builder.__name__ = f'_{_tag}_{_kind}'
        _example_child = '"Hello"' if _kind == 'text' else 'html.text("Hello")'
        _builder = stdlib_element(
            _tag, (_arg_type,), (_NODE,), param_names=('child',),
            documentation=_html_documentation(
                f'Wrap {_kind} content in a `<{_tag}>` element.',
                f'html.{_tag}({_example_child})',
            ),
        )(_builder)
        globals()[_builder.__name__] = _builder

del _builder

for _tag, _example_attributes in (
    ('br', 'dict{"class" => "clear"}'),
    ('hr', 'dict{"class" => "divider"}'),
    ('img', 'dict{"src" => "/logo.png", "alt" => "Logo"}'),
    ('input', 'dict{"type" => "text", "name" => "query"}'),
):
    def _void_builder(args: tuple[Any, ...], ctx: RuntimeContext,
                      tag: str = _tag) -> tuple[Any, ...]:
        """Create a void HTML element with validated attributes."""
        del ctx
        return (_element(tag, attrs=args[0]),)
    _void_builder.__name__ = f'_{_tag}_void'
    _void_builder = stdlib_element(
        _tag, (_ATTRIBUTES,), (_NODE,), param_names=('attributes',),
        documentation=_html_documentation(
            f'Create a `<{_tag}>` element with attributes.',
            f'html.{_tag}({_example_attributes})',
        ),
    )(_void_builder)
    globals()[_void_builder.__name__] = _void_builder

del _void_builder



@stdlib_element('lineBreak', (), (_NODE,),
                documentation=_html_documentation('Create a line break element.', 'html.lineBreak'))
def _line_break(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a line break element."""
    del args, ctx
    return (_element('br'),)


@stdlib_element('horizontalRule', (), (_NODE,),
                documentation=_html_documentation('Create a thematic break element.', 'html.horizontalRule'))
def _horizontal_rule(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a thematic break element."""
    del args, ctx
    return (_element('hr'),)


# Document/head and form helpers intentionally accept plain, escaped data. Raw
# CSS and JavaScript text are not accepted; linked resources are explicit.
@stdlib_element('metaDescription', (T.String,), (_NODE,), param_names=('description',),
                documentation=_html_documentation('Create a page description metadata element.', 'html.metaDescription("Introduction")'))
def _meta_description(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a page description metadata element."""
    del ctx
    return (_markup_node('<meta' + _attributes({'name': 'description', 'content': args[0]}) + '>', 'meta'),)


@stdlib_element('meta', (T.String, T.String), (_NODE,),
                param_names=('name', 'content'),
                documentation=_html_documentation('Create a named metadata element.', 'html.meta("author", "Ada")'))
def _meta(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a named metadata element."""
    del ctx
    name, content = args
    if name not in _META_NAMES:
        raise ValueError('Unsupported meta name; HTTP refresh is not a safe head helper')
    return (_markup_node('<meta' + _attributes({'name': name, 'content': content}) + '>', 'meta'),)


@stdlib_element('link', (T.String, T.String), (_NODE,),
                param_names=('rel', 'href'),
                documentation=_html_documentation('Create a link to a related resource.', 'html.link("canonical", "/intro")'))
def _link(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a link to a related resource."""
    del ctx
    rel, href = args
    if rel not in _LINK_RELS:
        raise ValueError('Unsupported link relationship')
    _check_web_resource(href)
    return (_markup_node('<link' + _attributes({'rel': rel, 'href': href}) + '>', 'link'),)


@stdlib_element('stylesheet', (T.String,), (_NODE,), param_names=('href',),
                documentation=_html_documentation('Link an external CSS stylesheet.', 'html.stylesheet("/site.css")'))
def _stylesheet(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Link an external CSS stylesheet."""
    del ctx
    return _link(('stylesheet', args[0]), None)


@stdlib_element('scriptSrc', (T.String,), (_NODE,), param_names=('src',),
                documentation=_html_documentation('Reference an external script.', 'html.scriptSrc("/site.js")'))
def _script_src(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Reference an external script."""
    del ctx
    # Only an explicit external script resource. No inline script contents or
    # templated JS evaluation are possible through this safe builder.
    _check_web_resource(args[0])
    attributes = _attributes({'src': args[0]})
    return (_markup_node(f'<script{attributes} defer></script>', 'script'),)


def _page(title: str, body: Any, extra: list[Any], lang: str) -> ObjectValue:
    """Build a complete HTML page from its title, body, and head nodes."""
    if not _LANG.fullmatch(lang):
        raise ValueError('Invalid document language')
    # Only trusted metadata nodes can appear inside the head. This also keeps
    # accidentally inserted body markup out of document metadata.
    metadata = []
    for value in extra:
        node = _validated_node(value)
        if node.fields['tag'] not in ('meta', 'link', 'script'):
            raise ValueError('Page head accepts only metadata and linked resources')
        metadata.append(node.fields['markup'])
    head = '<head><meta charset="utf-8"><title>' + _escape_text(title) + '</title>' + ''.join(metadata) + '</head>'
    result = ('<!doctype html><html' + _attributes({'lang': lang}) + '>' + head
              + '<body>' + render_node(body) + '</body></html>')
    return _markup_node(result)


@stdlib_element('page', (T.String, _NODE), (_NODE,),
                param_names=('title', 'body'),
                documentation=_html_documentation('Create an HTML document with a title and body.', 'html.page("Introduction", html.p("Hello"))'))
def _page_simple(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create an HTML document with a title and body."""
    del ctx
    return (_page(args[0], args[1], [], 'en'),)


@stdlib_element('pageWithHead', (T.String, _NODE, _NODE_LIST, T.String), (_NODE,),
                param_names=('title', 'body', 'head', 'lang'),
                documentation=_html_documentation('Create an HTML document with additional head nodes and a language.', 'html.pageWithHead("Introduction", html.p("Hello"), [html.metaDescription("Example")], "en")'))
def _page_extended(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create an HTML document with additional head nodes and a language."""
    del ctx
    return (_page(args[0], args[1], _node_items(args[2]), args[3]),)


@stdlib_element('formFor', (T.String, T.String, _NODE), (_NODE,),
                param_names=('method', 'action', 'fields'),
                documentation=_html_documentation('Create a GET or POST form with the given fields.', 'html.formFor("get", "/search", html.textField("q", "Search"))'))
def _form_for(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a GET or POST form with the given fields."""
    del ctx
    method, action, fields = args
    if method.lower() not in ('get', 'post'):
        raise ValueError('HTML form method must be GET or POST')
    _check_web_resource(action)
    return (_element('form', render_node(fields), {'method': method.lower(), 'action': action}),)


@stdlib_element('postForm', (T.String, _NODE), (_NODE,),
                param_names=('action', 'fields'),
                documentation=_html_documentation('Create a POST form with the given fields.', 'html.postForm("/save", html.submitButton("Save"))'))
def _post_form(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a POST form with the given fields."""
    del ctx
    return _form_for(('post', args[0], args[1]), None)


@stdlib_element('textField', (T.String, T.String), (_NODE,),
                param_names=('name', 'label'),
                documentation=_html_documentation('Create a labelled text input.', 'html.textField("name", "Name")'))
def _text_field(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a labelled text input."""
    del ctx
    name, label = args
    if not name or any(ch.isspace() or unicodedata.category(ch).startswith('C')
                       for ch in name):
        raise ValueError('Form field identifier must be nonempty without whitespace')
    # Attribute serializer escapes even if a form field name is user-controlled.
    markup = (_element('label', _escape_text(label), {'for': name}).fields['markup']
              + _element('input', attrs={'type': 'text', 'name': name, 'id': name}).fields['markup'])
    return (_markup_node(markup),)


@stdlib_element('hiddenField', (T.String, T.String), (_NODE,),
                param_names=('name', 'value'),
                documentation=_html_documentation('Create a hidden input with a name and value.', 'html.hiddenField("mode", "edit")'))
def _hidden_field(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a hidden input with a name and value."""
    del ctx
    return (_element('input', attrs={'type': 'hidden', 'name': args[0], 'value': args[1]}),)


@stdlib_element('checkboxField', (T.String, T.String), (_NODE,),
                param_names=('name', 'label'),
                documentation=_html_documentation('Create a labelled checkbox input.', 'html.checkboxField("notify", "Notify me")'))
def _checkbox_field(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a labelled checkbox input."""
    del ctx
    name, label = args
    child = (_element('input', attrs={'type': 'checkbox', 'name': name}).fields['markup']
             + _escape_text(label))
    return (_element('label', child),)


@stdlib_element('submitButton', (T.String,), (_NODE,), param_names=('label',),
                documentation=_html_documentation('Create a form submit button with escaped label text.', 'html.submitButton("Save")'))
def _submit_button(args: tuple[Any, ...], ctx: RuntimeContext) -> tuple[Any, ...]:
    """Create a form submit button with escaped label text."""
    del ctx
    return (_element('button', _escape_text(args[0]), {'type': 'submit'}),)
