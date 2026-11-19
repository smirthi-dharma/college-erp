"""Content-Security-Policy and friends: present on every response, and wide enough for the assets the pages really load."""
import re
from pathlib import Path

from django.conf import settings
from django.test import Client, override_settings

from colleges.tests.base import HOST_A

from .world import WorldTestCase

ROOT = Path(__file__).resolve().parents[2]


def policy_of(response, header='Content-Security-Policy'):
    return {part.split()[0]: part.split()[1:] for part in response[header].split('; ')}


class HeaderTests(WorldTestCase):
    def test_every_kind_of_response_carries_the_policy(self):
        pages = {'login page': Client(HTTP_HOST=HOST_A).get('/'),
                 'admin home': self.as_user(self.admin).get('/admin/home/'),
                 'student home': self.as_user(self.stud1).get('/student/home/'),
                 'a 404': self.as_user(self.admin).get('/no/such/page/'),
                 'a redirect': Client(HTTP_HOST=HOST_A).get('/admin/home/'),
                 'a 403': self.as_user(self.stud1).post('/student/delete/1')}
        for name, response in pages.items():
            self.assertIn('Content-Security-Policy', response, name)
            self.assertNotIn('Content-Security-Policy-Report-Only', response, name)
            self.assertIn('camera=()', response['Permissions-Policy'], name)

    def test_the_policy_blocks_plugins_framing_base_tags_and_foreign_forms(self):
        policy = policy_of(Client(HTTP_HOST=HOST_A).get('/'))
        self.assertEqual(policy['default-src'], ["'self'"])
        self.assertEqual(policy['object-src'], ["'none'"])
        self.assertEqual(policy['frame-ancestors'], ["'none'"])
        self.assertEqual(policy['base-uri'], ["'self'"])
        self.assertEqual(policy['form-action'], ["'self'"])
        self.assertEqual(policy['connect-src'], ["'self'"])
        for directive, values in policy.items():
            self.assertNotIn("'unsafe-eval'", values, directive)
            self.assertNotIn('*', values, directive)
            self.assertFalse([v for v in values if v.startswith('http:')], directive)  # no plain-http sources

    @override_settings(CSP_REPORT_ONLY=True)
    def test_report_only_mode_is_a_switch_for_staging(self):
        response = Client(HTTP_HOST=HOST_A).get('/')
        self.assertIn('Content-Security-Policy-Report-Only', response)
        self.assertNotIn('Content-Security-Policy', response)

    def test_images_may_come_only_from_ourselves(self):
        self.assertEqual(policy_of(Client(HTTP_HOST=HOST_A).get('/'))['img-src'], ["'self'", 'data:', 'blob:'])


class PolicyCoversThePagesTests(WorldTestCase):
    """If a template starts loading from a new host, the policy must be widened on purpose (or this fails)."""

    def external_assets(self):
        found = []
        folders = [p for p in ROOT.iterdir() if (p / 'templates').is_dir()]
        for folder in folders:
            for path in (folder / 'templates').rglob('*.html'):
                text = path.read_text(errors='ignore')
                for tag, _attribute, url in re.findall(r'<(script|link|img|iframe)\b[^>]*?\b(src|href)="(https?://[^"]+)"', text):
                    found.append((path.relative_to(ROOT).as_posix(), tag, url))
        return found

    def allowed(self, url, tag, policy):
        directive = {'script': 'script-src', 'link': 'style-src', 'img': 'img-src', 'iframe': 'frame-src'}[tag]
        return any(url.startswith(source) for source in policy.get(directive, []))  # sources are origins or path prefixes

    def test_every_external_script_style_and_frame_in_the_templates_is_allowed_by_the_policy(self):
        policy = {name: list(values) for name, values in settings.CSP_DIRECTIVES.items()}
        assets = [a for a in self.external_assets() if not a[0].startswith('main_app/templates/registration/base.html')]
        self.assertTrue(assets, 'the scan found nothing: it is broken')
        blocked = [(f, tag, url) for f, tag, url in assets if not self.allowed(url, tag, policy)]
        self.assertEqual(blocked, [], 'the Content-Security-Policy would block these')

    def test_no_template_loads_anything_over_plain_http(self):
        plain = [a for a in self.external_assets() if a[2].startswith('http://') and a[1] != 'a']
        self.assertEqual(plain, [])
