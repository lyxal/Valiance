"""Optional real-browser HTML5 and injection-boundary checks for std.html.

Requires Playwright and a locally installed Chromium executable; it is not a
runtime dependency of std.html. Tests render deterministic markup in a real
HTML parser without network navigation or external scripts.
"""
from __future__ import annotations

import shutil
import unittest

from valiance.std import html as html_bridge

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright is not None and
                     (shutil.which('chromium') or shutil.which('chromium-browser')),
                     'Optional Playwright/Chromium browser qualification is unavailable')
class HtmlBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.driver = sync_playwright().start()
        cls.browser = cls.driver.chromium.launch(
            executable_path=shutil.which('chromium') or shutil.which('chromium-browser'),
            headless=True,
            args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'],
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.driver.stop()

    def test_untrusted_text_and_attributes_cannot_create_executable_nodes(self):
        payload = '<svg/onload=window.__html_pwned=1><img src=x onerror=window.__html_pwned=1><script>window.__html_pwned=1</script>'
        unsafe_attr = '" onmouseover="window.__html_pwned=1'
        child = html_bridge._element('main',
            html_bridge._element('p', html_bridge._escape_text(payload)).fields['markup']
            + html_bridge._element('a', 'link', {'href': '/safe', 'title': unsafe_attr}).fields['markup'])
        doc = html_bridge._page('Escaped <title>', child, [], 'en')
        page = self.browser.new_page(java_script_enabled=True)
        try:
            page.set_content(html_bridge.render_node(doc), wait_until='load')
            self.assertEqual(page.evaluate('document.compatMode'), 'CSS1Compat')
            self.assertEqual(page.title(), 'Escaped <title>')
            self.assertEqual(page.locator('main p').inner_text(), payload)
            self.assertEqual(page.locator('main script, main svg, main img').count(), 0)
            self.assertIsNone(page.locator('a').get_attribute('onmouseover'))
            self.assertIsNone(page.evaluate('window.__html_pwned'))
        finally:
            page.close()

    def test_form_and_boolean_attributes_parse_as_intended(self):
        fields = html_bridge._markup_node(''.join((
            html_bridge._text_field(('name', 'Your name'), None)[0].fields['markup'],
            html_bridge._with_boolean((html_bridge._element('input', attrs={'name': 'accept'}),
                                       'required', True), None)[0].fields['markup'],
            html_bridge._submit_button(('Send',), None)[0].fields['markup'],
        )))
        form = html_bridge._form_for(('post', '/submit', fields), None)[0]
        page = self.browser.new_page()
        try:
            page.set_content(html_bridge.render_node(html_bridge._page('Form', form, [], 'en')))
            self.assertEqual(page.locator('form').get_attribute('method'), 'post')
            self.assertEqual(page.locator('label[for="name"]').inner_text(), 'Your name')
            self.assertEqual(page.locator('input[name="name"]').get_attribute('type'), 'text')
            self.assertTrue(page.locator('input[name="accept"]').evaluate('el => el.required'))
            self.assertEqual(page.locator('button[type="submit"]').inner_text(), 'Send')
        finally:
            page.close()


if __name__ == '__main__':
    unittest.main()
