"""The installable app: manifest, service worker, offline page. The worker is run for real in Node (when installed)."""
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.conf import settings
from django.test import Client
from PIL import Image

from colleges.tests.base import HOST_A, HOST_B, TwoCollegeTestCase

from .world import WorldTestCase

ROOT = Path(__file__).resolve().parents[2]
STATIC_PWA = ROOT / 'main_app' / 'static' / 'pwa'
HARNESS = Path(__file__).resolve().parent / 'sw_harness.js'


class ManifestTests(TwoCollegeTestCase):
    def manifest(self, host=HOST_A):
        response = Client(HTTP_HOST=host).get('/manifest.webmanifest')
        self.assertEqual(response.status_code, 200)
        return response, json.loads(response.content)

    def test_it_is_public_json_with_the_right_type_and_is_cacheable(self):
        response, _ = self.manifest()
        self.assertEqual(response['Content-Type'], 'application/manifest+json')
        self.assertEqual(response['Cache-Control'], 'public, max-age=3600')

    def test_it_meets_the_installability_rules_android_chrome_checks(self):
        _, data = self.manifest()
        for key in ('name', 'short_name', 'start_url', 'display', 'icons', 'theme_color', 'background_color', 'scope'):
            self.assertTrue(data.get(key), key)
        self.assertEqual(data['display'], 'standalone')
        self.assertEqual(data['start_url'], '/')
        self.assertEqual(data['scope'], '/')
        self.assertLessEqual(len(data['short_name']), 12)
        sizes = {(i['sizes'], i['purpose']) for i in data['icons']}
        self.assertIn(('192x192', 'any'), sizes)
        self.assertIn(('512x512', 'any'), sizes)
        self.assertIn(('512x512', 'maskable'), sizes)  # android crops it to its own shape
        self.assertTrue(all(i['type'] == 'image/png' for i in data['icons']))

    def test_every_icon_the_manifest_names_exists_and_has_the_size_it_claims(self):
        _, data = self.manifest()
        for icon in data['icons']:
            self.assertTrue(icon['src'].startswith('/static/pwa/'), icon['src'])
            path = STATIC_PWA / Path(icon['src']).name
            self.assertTrue(path.exists(), path)
            width, height = Image.open(path).size
            self.assertEqual(f'{width}x{height}', icon['sizes'], path.name)
        self.assertEqual(Image.open(STATIC_PWA / 'apple-touch-icon.png').size, (180, 180))

    def test_each_college_gets_its_own_name_and_nothing_personal(self):
        _, a = self.manifest(HOST_A)
        _, b = self.manifest(HOST_B)
        self.assertIn(self.college_a.name, a['name'])
        self.assertIn(self.college_b.name, b['name'])
        self.assertNotEqual(a['name'], b['name'])
        self.assertNotIn(self.college_b.name, json.dumps(a))

    def test_a_very_long_college_name_gets_a_short_label(self):
        original = self.college_a.name
        self.college_a.name = 'The Very Long Named Institute of Engineering and Technology'
        self.college_a.save()
        try:
            _, data = self.manifest()
        finally:
            self.college_a.name = original
            self.college_a.save()
        self.assertLessEqual(len(data['short_name']), 12)
        self.assertTrue(data['short_name'])

    def test_only_get_is_allowed(self):
        self.assertEqual(Client(HTTP_HOST=HOST_A).post('/manifest.webmanifest').status_code, 405)


class WorkerFileTests(TwoCollegeTestCase):
    def worker(self):
        response = Client(HTTP_HOST=HOST_A).get('/sw.js')
        self.assertEqual(response.status_code, 200)
        return response

    def test_headers_let_it_control_the_whole_site_and_always_be_rechecked(self):
        response = self.worker()
        self.assertEqual(response['Content-Type'], 'text/javascript')
        self.assertEqual(response['Service-Worker-Allowed'], '/')
        self.assertEqual(response['Cache-Control'], 'no-cache')
        self.assertEqual(Client(HTTP_HOST=HOST_A).post('/sw.js').status_code, 405)

    def test_the_version_comes_from_the_deploy_and_names_the_caches(self):
        text = self.worker().content.decode()
        self.assertIn(json.dumps(settings.PWA_CACHE_VERSION), text)
        self.assertIn("'erp-shell-' + CONFIG.version", text)

    def test_the_precache_list_is_only_the_offline_page_and_icons(self):
        text = self.worker().content.decode()
        config = json.loads(re.search(r'const CONFIG = (\{.*?\});', text).group(1))
        self.assertEqual(config['offlineUrl'], '/offline/')
        self.assertTrue(all(u.startswith('/static/pwa/') for u in config['precache']), config['precache'])

    def test_the_source_never_caches_pages_photos_or_data(self):
        text = self.worker().content.decode()
        code = '\n'.join(re.sub(r'//.*$', '', line) for line in text.splitlines())  # drop comments
        self.assertEqual(code.count('cache.put('), 1, 'the only cache write is the static-file refresh')
        for forbidden in ("'/photo", "'/students", "'/attendance", 'localStorage', 'IndexedDB', 'indexedDB', 'sync', 'push'):
            self.assertNotIn(forbidden, code, forbidden)
        self.assertIn("request.method !== 'GET'", code)
        self.assertIn('url.origin !== self.location.origin', code)


class OfflinePageTests(WorldTestCase):
    def test_it_is_public_light_and_holds_no_personal_data_or_session(self):
        response = Client(HTTP_HOST=HOST_A).get('/offline/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Cache-Control'], 'no-cache')
        self.assertFalse(response.cookies, 'the offline page must not set a session or CSRF cookie')
        page = response.content.decode()
        self.assertIn('You are offline', page)
        self.assertIn('Nothing you were doing has been saved', page)  # a teacher must not think a missed submit was saved
        self.assertIn('location.reload()', page)
        self.assertLess(len(response.content), 5000)
        for personal in ('stud1@a.in', 'Stu', 'Sita', 'Ada'):
            self.assertNotIn(personal, page)

    def test_it_is_the_same_for_a_logged_in_user(self):
        for user in (self.admin, self.staff1, self.stud1):
            page = self.as_user(user).get('/offline/').content.decode()
            self.assertNotIn(user.email, page)
            self.assertNotIn(user.first_name, page.replace('Stu', '') if user.first_name == 'Stu' else page)


class PagesAreInstallableTests(WorldTestCase):
    def head(self, response):
        page = response.content.decode()
        return page[:page.index('</head>')]

    def test_every_kind_of_page_links_the_manifest_the_icon_the_colour_and_the_worker(self):
        pages = {'login': Client(HTTP_HOST=HOST_A).get('/'), 'admin': self.as_user(self.admin).get('/admin/home/'),
                 'teacher': self.as_user(self.staff1).get('/staff/home/'), 'student': self.as_user(self.stud1).get('/student/home/'),
                 'password reset': Client(HTTP_HOST=HOST_A).get('/accounts/password_reset/')}
        for name, response in pages.items():
            self.assertEqual(response.status_code, 200, name)
            head = self.head(response)
            self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', head, name)
            self.assertIn('name="theme-color" content="#5e64ff"', head, name)
            self.assertIn('rel="apple-touch-icon"', head, name)
            self.assertIn('/static/pwa/register.js', head, name)
            self.assertIn('width=device-width, initial-scale=1', head, name)  # the viewport an installable phone page needs

    def test_the_register_script_registers_the_worker_at_the_root_and_remembers_a_refusal(self):
        script = (STATIC_PWA / 'register.js').read_text()
        self.assertIn("register('/sw.js', { scope: '/' })", script)
        self.assertIn('beforeinstallprompt', script)
        self.assertIn('try {', script)  # storage can be blocked (private mode); the page must still work

    def test_the_policy_lets_the_manifest_and_worker_load(self):
        response = Client(HTTP_HOST=HOST_A).get('/')
        policy = {p.split()[0]: p.split()[1:] for p in response['Content-Security-Policy'].split('; ')}
        self.assertEqual(policy['default-src'], ["'self'"])
        for directive in ('manifest-src', 'worker-src', 'child-src'):  # not listed means they fall back to default-src/script-src
            self.assertNotIn(directive, policy)
        self.assertIn("'self'", policy['script-src'])


@skipUnless(shutil.which('node'), 'Node is not installed')
class WorkerBehaviourTests(TwoCollegeTestCase):
    """Runs the real worker file in a fake worker scope (main_app/tests/sw_harness.js) and checks what it keeps."""

    @classmethod
    def run_worker(cls):
        source = Client(HTTP_HOST=HOST_A).get('/sw.js').content.decode()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'sw.js'
            path.write_text(source)
            result = subprocess.run([shutil.which('node'), str(HARNESS), str(path)], capture_output=True, text=True, timeout=60)  # noqa: S603
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    def setUp(self):
        super().setUp()
        self.out = self.run_worker()

    def test_install_saves_the_offline_page_and_icons_and_takes_over(self):
        (name, urls), = self.out['install']['addAll']
        self.assertTrue(name.startswith('erp-shell-'))
        self.assertEqual(urls[0], '/offline/')
        self.assertTrue(all(u.startswith('/static/pwa/') for u in urls[1:]))
        self.assertTrue(self.out['install']['skipWaiting'])

    def test_activate_removes_old_versions_but_not_other_peoples_caches(self):
        self.assertEqual(sorted(self.out['activate']['deleted']), ['erp-shell-OLD', 'erp-static-OLD'])
        self.assertIn('unrelated-cache', self.out['activate']['kept'])
        self.assertTrue(self.out['activate']['claimed'])

    def test_opening_a_page_uses_the_network_and_keeps_nothing(self):
        self.assertEqual(self.out['navigateOnline'], {'intercepted': True, 'body': 'network page', 'puts': 0})

    def test_opening_a_page_offline_shows_the_offline_page_and_keeps_nothing(self):
        self.assertEqual(self.out['navigateOffline'], {'intercepted': True, 'body': 'cached /offline/', 'puts': 0})

    def test_forms_other_hosts_pictures_data_and_lookalikes_are_not_touched(self):
        self.assertEqual(self.out['notIntercepted'], {label: False for label in self.out['notIntercepted']})
        self.assertGreaterEqual(len(self.out['notIntercepted']), 8)
        self.assertEqual(self.out['putsAfterSkipped'], 0)

    def test_static_files_are_shown_from_the_saved_copy_when_offline(self):
        self.assertEqual(self.out['staticFirst'], {'intercepted': True, 'body': 'css v1'})
        self.assertEqual(len(self.out['staticPuts']), 1)
        self.assertTrue(self.out['staticPuts'][0].startswith('erp-static-'))
        self.assertEqual(self.out['staticOffline'], {'intercepted': True, 'body': 'css v1'})
        self.assertEqual(self.out['staticOfflineUnseen']['response'], 'none')  # never seen and offline: a plain failure, no invention

    def test_the_only_things_ever_written_to_a_cache_are_the_icons_the_offline_page_and_static_files(self):
        written = set(self.out['allPuts'])
        self.assertEqual(written, {'/static/dist/app.css'})
