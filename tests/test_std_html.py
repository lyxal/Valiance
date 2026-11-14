"""Escaped HTML node generation and std.web response integration."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from http.client import HTTPConnection

from valiance.analysis import Analyser
from valiance.parsing import parse
from valiance.runtime import compile_program, dumps, loads, run
from valiance.runtime.runtime_values import ObjectValue
from valiance.std import html as html_bridge, web as web_bridge


def evaluate(source: str, *, serialized: bool = False):
    analyser = Analyser()
    checked = analyser.analyse(parse(source))
    failures = [str(issue) for issue in analyser.diagnostics]
    if failures:
        raise AssertionError(f"Unexpected Valiance analysis diagnostics: {failures}")
    program = compile_program(checked, optimize=serialized)
    return run(loads(dumps(program)) if serialized else program)


class HtmlNodeTests(unittest.TestCase):
    def test_escaped_nested_elements_and_fragment(self):
        source = '''import {std.html}
html.render(html.fragment([html.h1 "Hi <&>", html.div(html.p "quote \\""), html.text "<raw>"]))'''
        self.assertEqual(evaluate(source), [
            '<h1>Hi &lt;&amp;&gt;</h1><div><p>quote &quot;</p></div>&lt;raw&gt;'
        ])

    def test_element_and_attributes(self):
        source = '''import {std.html}
html.render(html.element("main", [html.h1 "Title", html.p "Body"], dict{"class" => "a&b", "id" => "x"}))'''
        self.assertEqual(evaluate(source), [
            '<main class="a&amp;b" id="x"><h1>Title</h1><p>Body</p></main>'
        ])

    def test_empty_and_unadorned_void_helpers(self):
        source = '''import {std.html}
html.render(html.fragment([html.lineBreak, html.empty, html.horizontalRule]))'''
        self.assertEqual(evaluate(source), ['<br><hr>'])

    def test_with_attrs_and_void_tags(self):
        source = '''import {std.html}
html.render(html.fragment([html.withAttrs(html.a "Read", dict{"href" => "/docs?a=1&b=2"}), html.br(dict{"class" => "clear"})]))'''
        self.assertEqual(evaluate(source), [
            '<a href="/docs?a=1&amp;b=2">Read</a><br class="clear">'
        ])

    def test_bad_attributes_and_urls_are_rejected(self):
        for name, value in (
            ('onclick', 'alert(1)'), ('style', 'color:red'),
            ('href', 'javascript:alert(1)'), ('href', '&#x6a;avascript:alert(1)'),
            ('href', 'java&#x0a;script:alert(1)'), ('href', '//evil.example/'),
            ('srcdoc', '<script>evil()</script>'), ('href', 'data:text/html,hi'),
        ):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                html_bridge._attributes({name: value})
        with self.assertRaises(ValueError):
            html_bridge._element('script', 'alert(1)')
        with self.assertRaises(ValueError):
            html_bridge._element('img', 'not allowed')
        with self.assertRaises(ValueError):
            html_bridge._attributes({'bad name': 'x'})
        with self.assertRaises(ValueError):
            html_bridge._attributes({'HREF': '/', 'href': '/'})

    def test_forged_or_tampered_nodes_cannot_render(self):
        forged = ObjectValue('html.Node', {'markup': '<script>x</script>', 'seal': '', 'tag': 'script'})
        with self.assertRaisesRegex(ValueError, 'Untrusted'):
            html_bridge.render_node(forged)
        legitimate = html_bridge._markup_node('<p>safe</p>')
        self.assertEqual(html_bridge.render_node(legitimate), '<p>safe</p>')
        legitimate.fields['markup'] = '<img src=x onerror=alert(1)>'
        with self.assertRaisesRegex(ValueError, 'Untrusted'):
            html_bridge.render_node(legitimate)
        with self.assertRaisesRegex(ValueError, 'single element'):
            html_bridge._with_attrs((html_bridge._markup_node('hello'), {'class': 'x'}), None)

    def test_direct_web_handler_returns_html(self):
        source = '''import {std.web, std.web.App, std.html}
$app = App
$app := get("/"): fn(req: web.GetRequest) => html.h1 "Hello <friend>" end
web.dispatch($app, web.testRequest("GET", "/")) ?'''
        result, = evaluate(source)
        response = web_bridge._unwrap_response(result)
        self.assertEqual(response.status, 200)
        self.assertEqual(response.body, '<h1>Hello &lt;friend&gt;</h1>')
        self.assertEqual(response.headers.one('Content-Type'), 'text/html; charset=utf-8')

    def test_html_error_union_handler(self):
        source = '''import {std.web, std.web.App, std.html}
$app = App
$app := get("/"): fn(req: web.GetRequest) -> html.Node | web.Error =>
  html.h1 "Okay"
end
web.dispatch($app, web.testRequest("GET", "/")) ?'''
        response, = evaluate(source)
        self.assertEqual(web_bridge._unwrap_response(response).body, '<h1>Okay</h1>')

    def test_explicit_response_body_node(self):
        source = '''import {std.web, std.html}
web.Response(body = html.div "Hello & goodbye")'''
        result, = evaluate(source)
        response = web_bridge._unwrap_response(result)
        self.assertEqual(response.body, '<div>Hello &amp; goodbye</div>')
        self.assertEqual(response.headers.one('Content-Type'), 'text/html; charset=utf-8')

    def test_plain_string_response_remains_plain_text(self):
        source = '''import {std.web, std.web.App, std.html}
$app = App
$app := get("/"): fn(req: web.GetRequest) => "<h1>ordinary text</h1>" end
web.dispatch($app, web.testRequest("GET", "/")) ?'''
        result, = evaluate(source)
        response = web_bridge._unwrap_response(result)
        self.assertEqual(response.body, '<h1>ordinary text</h1>')
        self.assertNotEqual(response.headers.one('Content-Type'), 'text/html; charset=utf-8')


    def test_live_http_response_is_html(self):
        source = '''import {std.web, std.web.App, std.html}
$app = App
$app := get("/generated"): fn(req: web.GetRequest) =>
  html.element("main", [html.h1 "A & B", html.p "<unsafe>"], dict{"class" => "content"})
end
$app := get("/plain"): fn(req: web.GetRequest) => "Not HTML" end
web.serve($app, web.ServerConfig(port = 0)) ?'''
        lease, = evaluate(source)
        token = web_bridge._lease_token(lease)
        try:
            with web_bridge._SERVER_LEASES_LOCK:
                port = web_bridge._SERVER_LEASES[token].bound_address[1]
            conn = HTTPConnection('127.0.0.1', port, timeout=4)
            try:
                conn.request('GET', '/generated')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader('Content-Type'), 'text/html; charset=utf-8')
                self.assertEqual(response.read().decode(),
                    '<main class="content"><h1>A &amp; B</h1><p>&lt;unsafe&gt;</p></main>')
            finally:
                conn.close()
            conn = HTTPConnection('127.0.0.1', port, timeout=4)
            try:
                conn.request('GET', '/plain')
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertNotEqual(response.getheader('Content-Type'), 'text/html; charset=utf-8')
                self.assertEqual(response.read(), b'Not HTML')
            finally:
                conn.close()
        finally:
            web_bridge._close_server_token(token, .5)

    def test_standards_mode_document(self):
        source = '''import {std.html}
html.render(html.document(html.html(html.fragment([
  html.head(html.title "Greeting"),
  html.body(html.h1 "Hi <world>")
]))))'''
        self.assertEqual(evaluate(source), [
            '<!doctype html><html><head><title>Greeting</title></head>'
            '<body><h1>Hi &lt;world&gt;</h1></body></html>'
        ])
        with self.assertRaisesRegex(ValueError, 'root html element'):
            html_bridge._document((html_bridge._element('div'),), None)

    def test_html_node_survives_optimized_serialized_bytecode(self):
        source = '''import {std.html}
html.render(html.div(html.h1 "Escaped <title>"))'''
        self.assertEqual(evaluate(source, serialized=True),
                         ['<div><h1>Escaped &lt;title&gt;</h1></div>'])

    def test_html5_boolean_attributes_and_void_rules(self):
        self.assertEqual(html_bridge._attributes({'disabled': '', 'autofocus': 'autofocus'}),
                         ' disabled autofocus')
        for invalid in ('false', '0', 'yes', 'true'):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(ValueError, 'Boolean'):
                html_bridge._attributes({'disabled': invalid})
        with self.assertRaisesRegex(ValueError, 'Void'):
            html_bridge._element('input', 'unexpected child')
        self.assertEqual(html_bridge._element('input', attrs={'required': ''}).fields['markup'],
                         '<input required>')

    def test_html5_special_parsing_contexts_rejected(self):
        for tag in ('plaintext', 'noscript', 'xmp', 'listing', 'iframe', 'svg',
                    'script', 'style', 'template', 'meta', 'link'):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                html_bridge._element(tag, 'hi')
        for tag in ('title', 'textarea'):
            with self.subTest(tag=tag), self.assertRaisesRegex(ValueError, 'text nodes'):
                html_bridge._element(tag, '<b>not text</b>')
            self.assertIn('&lt;', html_bridge._element(tag,
                          html_bridge._escape_text('</textarea>')).fields['markup'])

    def test_url_and_active_attribute_adversarial_vectors(self):
        vectors = (
            ('href', 'java\u200bscript:alert(1)'), ('href', 'https:\\example.net'),
            ('href', 'https:\n//example.net'), ('href', 'http:example.net'),
            ('href', '//example.org'), ('src', 'mailto:someone@example.org'),
            ('srcset', 'x.jpg 1x, javascript:evil 2x'),
            ('imagesrcset', 'x.jpg 1x'), ('ping', 'https://tracker'),
            ('is', 'my-custom-button'), ('xmlns:foo', 'urn:x'),
        )
        for name, value in vectors:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                html_bridge._attributes({name: value})
        self.assertEqual(html_bridge._attributes({'href': '/docs?x=1&y=2'}),
                         ' href="/docs?x=1&amp;y=2"')
        self.assertIn('href="https://example.org/docs"',
                      html_bridge._attributes({'href': 'https://example.org/docs'}))

    def test_quoted_attributes_are_not_reinterpreted_on_extension(self):
        node = html_bridge._element('a', 'Read', {'title': 'value = "id=other" > wow'})
        appended = html_bridge._attach_attributes(node, {'id': 'real'})
        self.assertIn('title="value = &quot;id=other&quot; &gt; wow" id="real"',
                      html_bridge.render_node(appended))
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            html_bridge._attach_attributes(appended, {'ID': 'new'})

    def test_node_composition_and_boolean_helpers_compiled(self):
        source = '''import {std.html}
html.render(html.children("main", [
  html.withStyle(html.withClass(html.div "Card", "panel"), dict{"color" => "rebeccapurple", "padding" => "1rem"}),
  html.withId(html.p "Body", "intro"),
  html.withData(html.span "x", "item-id", "a&b"),
  html.withAria(html.button "Go", "label", "Open <menu>"),
  html.when(true, html.strong "Yes"),
  html.when(false, html.em "No"),
  html.choose(false, html.p "Wrong", html.p "Right"),
  html.withBoolean(html.input(dict{"name" => "q"}), "required", true),
  html.withBoolean(html.input(dict{"name" => "p"}), "disabled", false)
]))'''
        result, = evaluate(source)
        for part in ('<div class="panel" style="color: rebeccapurple; padding: 1rem">Card</div>', '<p id="intro">Body</p>',
                     'data-item-id="a&amp;b"', 'aria-label="Open &lt;menu&gt;"',
                     '<strong>Yes</strong>', '<p>Right</p>', '<input name="q" required>',
                     '<input name="p">'):
            self.assertIn(part, result)
        self.assertNotIn('Wrong', result)
        self.assertNotIn('<em>', result)

    def test_join_and_reusable_component_compilation(self):
        source = '''import {std.html}
$card = fn(title: String) => html.withClass(html.article(html.h2 $title), "card") end
html.render(html.join([$card("One"), $card("Two")], html.lineBreak))'''
        self.assertEqual(evaluate(source), [
            '<article class="card"><h2>One</h2></article><br>'
            '<article class="card"><h2>Two</h2></article>'
        ])

    def test_document_metadata_and_form_helpers_compiled(self):
        source = r'''import {std.html}
html.render(html.pageWithHead("My <site>",
  html.postForm("/submit?x=1&y=2", html.fragment([
    html.textField("full-name", "Your <name>"),
    html.checkboxField("agree", "I agree"),
    html.hiddenField("csrf", "a\"&<>"),
    html.submitButton "Send"
  ])),
  [html.metaDescription("Example <description>"),
   html.meta("viewport", "width=device-width, initial-scale=1"),
   html.stylesheet("/assets/main.css"),
   html.scriptSrc("/assets/main.js")], "en-GB"))'''
        result, = evaluate(source)
        self.assertTrue(result.startswith('<!doctype html><html lang="en-GB"><head><meta charset="utf-8">'))
        for part in ('<title>My &lt;site&gt;</title>',
                     'name="description" content="Example &lt;description&gt;"',
                     '<link rel="stylesheet" href="/assets/main.css">',
                     '<script src="/assets/main.js" defer></script>',
                     '<form method="post" action="/submit?x=1&amp;y=2">',
                     '<label for="full-name">Your &lt;name&gt;</label>',
                     '<input type="checkbox" name="agree">I agree</label>',
                     'name="csrf" value="a&quot;&amp;&lt;&gt;"',
                     '<button type="submit">Send</button>'):
            self.assertIn(part, result)
        self.assertTrue(result.endswith('</body></html>'))

    def test_metadata_unsafe_link_and_form_inputs(self):
        with self.assertRaisesRegex(ValueError, 'HTTP refresh'):
            html_bridge._meta(('http-equiv', 'refresh'), None)
        with self.assertRaisesRegex(ValueError, 'relationship'):
            html_bridge._link(('import', '/unsafe'), None)
        with self.assertRaises(ValueError):
            html_bridge._script_src(('data:text/javascript,alert(1)',), None)
        with self.assertRaisesRegex(ValueError, 'method'):
            html_bridge._form_for(('delete', '/x', html_bridge._markup_node('')), None)
        with self.assertRaisesRegex(ValueError, 'language'):
            html_bridge._page('Title', html_bridge._markup_node('body'), [], 'en" onload="x')
        with self.assertRaisesRegex(ValueError, 'metadata'):
            html_bridge._page('Title', html_bridge._markup_node('body'),
                              [html_bridge._element('div', 'invalid')], 'en')

    def test_attribute_helpers_reject_forgery_and_names(self):
        forged = ObjectValue('html.Node', {'markup': '<div>bad</div>', 'seal': '', 'tag': 'div'})
        with self.assertRaisesRegex(ValueError, 'Untrusted'):
            html_bridge._with_class((forged, 'nice'), None)
        node = html_bridge._element('div')
        for name in ('OnClick', 'x:', 'Upper', '../escape'):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'suffix'):
                html_bridge._with_data((node, name, 'x'), None)
        with self.assertRaisesRegex(ValueError, 'boolean'):
            html_bridge._with_boolean((node, 'onload', True), None)
        for properties in ({'background-image': 'url(https://evil)'},
                           {'color': 'red;position:absolute'},
                           {'color': 'expression(alert(1))'},
                           {'color': 'red\\31'}):
            with self.subTest(properties=properties), self.assertRaises(ValueError):
                html_bridge._with_style((node, properties), None)
        self.assertIn('style="color: red"', html_bridge.render_node(
            html_bridge._with_style((node, {'color': 'red'}), None)[0]))

    def test_selected_conditional_node_can_still_receive_attributes(self):
        source = '''import {std.html}
html.render(html.withClass(html.choose(true, html.div "Visible", html.div "Hidden"), "selected"))'''
        self.assertEqual(evaluate(source), ['<div class="selected">Visible</div>'])

    def test_invalid_id_and_text_controls_fail(self):
        node = html_bridge._element('div')
        for ident in ('', 'white space', 'tab\there'):
            with self.subTest(ident=ident), self.assertRaisesRegex(ValueError, 'id'):
                html_bridge._with_id((node, ident), None)
        with self.assertRaisesRegex(ValueError, 'identifier'):
            html_bridge._text_field(('not valid', 'Name'), None)
        with self.assertRaisesRegex(ValueError, 'id'):
            html_bridge._attributes({'id': 'not valid'})
        with self.assertRaisesRegex(ValueError, 'control'):
            html_bridge._escape_text('unsafe\x00text')

    def test_simple_page_and_serialized_html_helper(self):
        source = '''import {std.html}
html.render(html.page("Hello <friend>",
  html.withClass(html.p "& welcome", "welcome")))'''
        expected = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
                    '<title>Hello &lt;friend&gt;</title></head><body>'
                    '<p class="welcome">&amp; welcome</p></body></html>')
        self.assertEqual(evaluate(source, serialized=True), [expected])

    @unittest.skipUnless(os.name == 'posix', 'The sample mounts a POSIX-only static router')
    def test_full_html_sample_over_http(self):
        source = Path('samples/web/html_components.vlnc').read_text(encoding='utf-8')
        self.assertIn('web.run($app, web.ServerConfig(port = 8767))', source)
        source = source.replace('web.run($app, web.ServerConfig(port = 8767))',
                                'web.serve($app, web.ServerConfig(port = 0)) ?')
        lease, = evaluate(source)
        token = web_bridge._lease_token(lease)
        try:
            with web_bridge._SERVER_LEASES_LOCK:
                port = web_bridge._SERVER_LEASES[token].bound_address[1]
            for method, path, expected_status, expected_fragment in (
                ('GET', '/', 200, '<!doctype html><html lang="en">'),
                ('GET', '/contact', 200, 'Use the form on the home page.'),
                ('GET', '/assets/site.css', 200, 'font-family: system-ui'),
                ('POST', '/contact', 200, 'nothing was saved'),
            ):
                with self.subTest(method=method, path=path):
                    conn = HTTPConnection('127.0.0.1', port, timeout=4)
                    try:
                        conn.request(method, path, body=b'',
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'}
                                     if method == 'POST' else {})
                        response = conn.getresponse()
                        self.assertEqual(response.status, expected_status)
                        self.assertIn(expected_fragment, response.read().decode('utf-8'))
                    finally:
                        conn.close()
        finally:
            web_bridge._close_server_token(token, .5)

    def test_html_page_direct_web_response(self):
        source = '''import {std.web, std.web.App, std.html}
$app = App
$app := get("/"): fn(req: web.GetRequest) =>
  html.page("Demo", html.main(html.h1 "H1/H2 HTML"))
end
web.dispatch($app, web.testRequest("GET", "/")) ?'''
        response, = evaluate(source)
        native = web_bridge._unwrap_response(response)
        self.assertEqual(native.status, 200)
        self.assertEqual(native.headers.one('Content-Type'), 'text/html; charset=utf-8')
        self.assertIn('<main><h1>H1/H2 HTML</h1></main>', native.body)


if __name__ == '__main__':
    unittest.main()
